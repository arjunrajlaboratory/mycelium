"""Safety boundaries for shared durable-writer primitives."""

from __future__ import annotations

import os
from pathlib import Path
import stat

import pytest

import mycelium_locks as locks


def test_atomic_replace_failure_preserves_original_bytes_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "registry.md"
    target.write_text("original\n")
    os.chmod(target, 0o640)

    def fail_replace(_source: str, _destination: Path) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(locks.os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        locks.atomic_write_text(target, "replacement\n", root=tmp_path)

    assert target.read_bytes() == b"original\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".registry.md.*.tmp")) == []


def test_linked_lock_ancestor_fails_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".mycelium").symlink_to(outside, target_is_directory=True)

    with pytest.raises(locks.LockError, match="unsafe"):
        with locks.durable_lock(root, "writer.lock"):
            raise AssertionError("unsafe lock unexpectedly acquired")

    assert list(outside.iterdir()) == []


def test_path_lock_and_atomic_write_preserve_existing_mode(tmp_path: Path) -> None:
    target = tmp_path / "INDEX.md"
    target.write_text("before\n")
    os.chmod(target, 0o444)

    with locks.durable_path_lock(target, root=tmp_path):
        locks.atomic_write_text(target, "after\n", root=tmp_path)

    assert target.read_text() == "after\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o444


def test_linked_target_parent_is_rejected_before_lock_state_creation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    living = root / ".living"
    living.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (living / "log").symlink_to(outside, target_is_directory=True)
    target = living / "log" / "LOG_REGISTRY.md"

    with pytest.raises(locks.LockError, match="unsafe"):
        with locks.durable_path_lock(target, root=root):
            raise AssertionError("unsafe target unexpectedly locked")

    assert not (root / ".mycelium").exists()
    assert list(outside.iterdir()) == []


def test_path_target_is_revalidated_after_lock_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "registry.md"
    target.write_text("safe\n")
    outside = tmp_path / "outside.md"
    outside.write_text("private\n")
    original_preflight = locks.preflight_target
    calls = 0

    def swap_after_initial_preflight(path: Path, root: Path) -> None:
        nonlocal calls
        calls += 1
        original_preflight(path, root)
        if calls == 1:
            path.unlink()
            path.symlink_to(outside)

    monkeypatch.setattr(locks, "preflight_target", swap_after_initial_preflight)

    with pytest.raises(locks.LockError, match="unsafe"):
        with locks.durable_path_lock(target, root=tmp_path):
            raise AssertionError("unsafe swapped target was exposed to the writer")

    assert calls == 2
    assert outside.read_text() == "private\n"
