"""Cross-process file locking for concurrent mycelium writers.

Multiple Claude chats can operate on one shared working tree at once. Any
read-modify-write of a shared .living/ file (registries, indexes, the knowledge
graph) is therefore a lost-update hazard. `file_lock` serialises those critical
sections with an advisory `fcntl.flock` on a sidecar lockfile.

Usage:
    from mycelium_locks import file_lock
    with file_lock(registry_path):
        rows = read(registry_path)
        ...
        atomic_write(registry_path, new_content)
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Iterator


@contextlib.contextmanager
def file_lock(target_path: str, timeout: float = 30.0) -> Iterator[None]:
    """Hold an exclusive advisory lock for the duration of the block.

    The lock is taken on a sibling ``<target>.lock`` file (never on the target
    itself, so an atomic-replace of the target can't drop the lock). On
    platforms without ``fcntl`` (e.g. Windows) this degrades to a no-op rather
    than crashing — the pre-existing atomic-replace still prevents corruption,
    only lost updates remain possible.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX fallback
        yield
        return

    # Dot-prefixed sibling lockfile: hidden + git-ignorable, and kept off the
    # target itself so an atomic-replace of the target can't drop the lock.
    _abs = os.path.abspath(target_path)
    lock_path = os.path.join(os.path.dirname(_abs), f".{os.path.basename(_abs)}.lock")
    os.makedirs(os.path.dirname(_abs) or ".", exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        import time

        deadline = None  # set lazily to avoid importing time when uncontended
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if deadline is None:
                    deadline = time.monotonic() + timeout
                if time.monotonic() >= deadline:
                    # Give up waiting and proceed anyway: a slow writer must not
                    # deadlock a session's Stop hook. Atomic-replace still holds.
                    break
                time.sleep(0.05)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write(target_path: str, data: str, encoding: str = "utf-8") -> None:
    """Write ``data`` to ``target_path`` atomically via tempfile + os.replace."""
    target_dir = os.path.dirname(os.path.abspath(target_path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".myc_atomic.", suffix=".tmp", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as tmp:
            tmp.write(data)
        os.replace(tmp_path, target_path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise
