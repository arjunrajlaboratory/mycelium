"""Tests for allocate_session_slot.py — including concurrent allocation."""

from __future__ import annotations

import subprocess
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).parent / "allocate_session_slot.py"


def _alloc(log_dir: str, date: str = "2026-07-24", slug: str = "proj") -> str:
    out = subprocess.run(
        [sys.executable, str(SCRIPT), log_dir, date, slug],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return out


def test_sequential_allocation_increments(tmp_path: Path) -> None:
    ids = [_alloc(str(tmp_path)).split("\t")[0] for _ in range(3)]
    assert ids == ["2026-07-24-001", "2026-07-24-002", "2026-07-24-003"]


def test_returns_absolute_path_and_creates_file(tmp_path: Path) -> None:
    sid, path = _alloc(str(tmp_path)).split("\t")
    assert sid == "2026-07-24-001"
    assert path == str((tmp_path / "2026-07-24-001-proj.md").resolve())
    assert Path(path).exists()


def test_slug_with_dashes_preserved(tmp_path: Path) -> None:
    sid, path = _alloc(str(tmp_path), slug="scientific-claims-prefilter").split("\t")
    assert sid == "2026-07-24-001"
    assert Path(path).name == "2026-07-24-001-scientific-claims-prefilter.md"


def test_concurrent_allocation_no_collisions(tmp_path: Path) -> None:
    """30 processes racing the same day must get 30 distinct slots."""
    n = 30
    with ProcessPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_alloc, [str(tmp_path)] * n))
    ids = [r.split("\t")[0] for r in results]
    dupes = [k for k, v in Counter(ids).items() if v > 1]
    assert not dupes, f"collision: {dupes}"
    assert len(set(ids)) == n
    # Every reserved file exists on disk.
    files = sorted(p.name for p in tmp_path.glob("2026-07-24-*.md"))
    assert len(files) == n
