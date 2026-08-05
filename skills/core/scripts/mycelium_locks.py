#!/usr/bin/env python3
"""Fail-closed POSIX locks and mode-preserving atomic text replacement."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from collections.abc import Iterator


class LockError(RuntimeError):
    """A shared mutation could not establish its safety boundary."""


def _regular_or_absent(path: Path) -> int | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise LockError(f"unsafe non-regular target: {path}")
    return stat.S_IMODE(metadata.st_mode)


def _real_directory(path: Path) -> None:
    metadata = path.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise LockError(f"unsafe non-directory path: {path}")


def _ensure_private_directory(parent: Path, name: str) -> Path:
    _real_directory(parent)
    child = parent / name
    try:
        child.mkdir(mode=0o700)
    except FileExistsError:
        pass
    _real_directory(child)
    return child


def preflight_target(target: Path, root: Path) -> None:
    _real_directory(root)
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise LockError(f"target escapes coordination root: {target}") from exc
    current = root
    for part in relative.parts[:-1]:
        current /= part
        _real_directory(current)
    _regular_or_absent(target)


def preflight_lock_root(root: Path) -> None:
    root = Path(os.path.abspath(root))
    _real_directory(root)
    state_candidate = root / ".mycelium"
    if state_candidate.exists() or state_candidate.is_symlink():
        _real_directory(state_candidate)
        locks_candidate = state_candidate / "locks"
        if locks_candidate.exists() or locks_candidate.is_symlink():
            _real_directory(locks_candidate)


def coordination_root_for(target: Path) -> Path:
    """Find the nearest project/portfolio root that should own target's locks."""
    target = Path(os.path.abspath(target))
    for candidate in (target.parent, *target.parents):
        if candidate.name == ".living":
            return candidate.parent
        living = candidate / ".living"
        try:
            metadata = living.lstat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None:
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise LockError(f"unsafe .living directory: {living}")
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return target.parent


def _lock_name_for(target: Path, root: Path) -> str:
    target = Path(os.path.abspath(target))
    root = Path(os.path.abspath(root))
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise LockError(f"target escapes coordination root: {target}") from exc
    digest = hashlib.sha256(relative.as_posix().encode()).hexdigest()[:20]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", target.name).strip("-") or "target"
    return f"{stem[:60]}-{digest}.lock"


def _timeout_seconds() -> float:
    raw = os.environ.get("MYCELIUM_DURABLE_LOCK_TIMEOUT", "30")
    try:
        value = float(raw)
    except ValueError:
        value = 30.0
    return value if 0 < value <= 300 else 30.0


@contextmanager
def durable_lock(root: Path, name: str, timeout: float | None = None) -> Iterator[Path]:
    """Hold one stable fcntl lock under ``root/.mycelium/locks``."""
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - unsupported platform
        raise LockError("POSIX fcntl locking is unavailable") from exc

    if name in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise LockError(f"invalid lock name: {name!r}")
    root = Path(os.path.abspath(root))
    preflight_lock_root(root)
    state_dir = _ensure_private_directory(root, ".mycelium")
    locks_dir = _ensure_private_directory(state_dir, "locks")
    lock_path = locks_dir / name
    _regular_or_absent(lock_path)

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise LockError(f"lock is not a regular file: {lock_path}")
        deadline = time.monotonic() + (timeout if timeout is not None else _timeout_seconds())
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockError(f"timed out acquiring durable lock: {lock_path}")
                time.sleep(0.05)
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"{os.getpid()} {int(time.time())}\n".encode())
        os.fsync(descriptor)
        yield lock_path
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@contextmanager
def durable_path_lock(
    target: Path, root: Path | None = None, timeout: float | None = None
) -> Iterator[Path]:
    target = Path(os.path.abspath(target))
    root = coordination_root_for(target) if root is None else Path(os.path.abspath(root))
    preflight_target(target, root)
    with durable_lock(root, _lock_name_for(target, root), timeout=timeout) as lock:
        # The target may have changed while this writer waited. Revalidate inside
        # the serialization boundary before callers can read or derive from it.
        preflight_target(target, root)
        yield lock


def atomic_write_text(
    target: Path,
    content: str,
    *,
    root: Path | None = None,
    default_mode: int = 0o644,
) -> None:
    """Replace one regular file atomically while preserving its current mode."""
    target = Path(os.path.abspath(target))
    root = coordination_root_for(target) if root is None else Path(os.path.abspath(root))
    preflight_target(target, root)
    mode = _regular_or_absent(target)
    if mode is None:
        mode = default_mode

    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
