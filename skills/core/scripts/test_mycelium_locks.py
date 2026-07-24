"""Tests for mycelium_locks: file_lock mutual exclusion + atomic_write."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mycelium_locks import atomic_write, file_lock  # noqa: E402

# A worker that must run in a fresh process (module-level for pickling).
_INCR = """
import sys, time
sys.path.insert(0, {scripts!r})
from mycelium_locks import file_lock
target = sys.argv[1]
with file_lock(target):
    with open(target) as f:
        n = int(f.read().strip())
    time.sleep(0.005)          # widen the race window
    with open(target, "w") as f:
        f.write(str(n + 1))
"""


def _run_incr(target: str) -> None:
    subprocess.run(
        [sys.executable, "-c", _INCR.format(scripts=str(Path(__file__).parent)), target],
        check=True,
    )


def test_atomic_write_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    atomic_write(str(p), "hello")
    assert p.read_text() == "hello"
    atomic_write(str(p), "world")
    assert p.read_text() == "world"


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    p = tmp_path / "f.txt"
    atomic_write(str(p), "x")
    leftovers = [q.name for q in tmp_path.iterdir() if q.name != "f.txt"]
    assert leftovers == []


def test_file_lock_reentrant_in_process(tmp_path: Path) -> None:
    # Sanity: acquiring/releasing in one process does not hang.
    target = tmp_path / "t"
    target.write_text("ok")
    with file_lock(str(target)):
        pass
    with file_lock(str(target)):
        pass


def test_file_lock_serialises_across_processes(tmp_path: Path) -> None:
    """20 processes each read-increment-write under the lock: no lost updates."""
    target = tmp_path / "counter"
    target.write_text("0")
    n = 20
    with ProcessPoolExecutor(max_workers=8) as ex:
        list(ex.map(_run_incr, [str(target)] * n))
    assert int(target.read_text().strip()) == n
