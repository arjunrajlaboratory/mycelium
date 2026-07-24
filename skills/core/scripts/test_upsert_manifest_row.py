"""Tests for upsert_manifest_row.py — including concurrent upserts."""

from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SCRIPT = Path(__file__).parent / "upsert_manifest_row.py"

HEADER = "| ID | Name | Status |\n|----|------|--------|\n"


def _run(registry: Path, key: str, row: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(registry), key, row, *extra],
        capture_output=True,
        text=True,
    )


def test_append_new_row(tmp_path: Path) -> None:
    reg = tmp_path / "M.md"
    reg.write_text(HEADER)
    r = _run(reg, "a-1", "| a-1 | first | ok |")
    assert r.stdout.strip() == "appended"
    assert "| a-1 | first | ok |" in reg.read_text()


def test_upsert_replaces_matching_row(tmp_path: Path) -> None:
    reg = tmp_path / "M.md"
    reg.write_text(HEADER + "| a-1 | old | wip |\n")
    r = _run(reg, "a-1", "| a-1 | new | done |")
    assert r.stdout.strip() == "upserted"
    text = reg.read_text()
    assert "| a-1 | new | done |" in text
    assert "old" not in text
    assert text.count("| a-1 |") == 1


def test_header_and_separator_never_matched(tmp_path: Path) -> None:
    reg = tmp_path / "M.md"
    reg.write_text(HEADER)
    # key "ID" equals the header label; must append, not overwrite the header.
    _run(reg, "ID", "| ID | x | y |")
    assert reg.read_text().startswith(HEADER)


def test_rejects_non_table_row(tmp_path: Path) -> None:
    reg = tmp_path / "M.md"
    reg.write_text(HEADER)
    r = _run(reg, "a-1", "not a table row")
    assert r.returncode == 1
    assert "table row" in r.stderr


def test_key_col_option(tmp_path: Path) -> None:
    reg = tmp_path / "M.md"
    # Match on the 2nd column instead of the 1st.
    reg.write_text("| A | B |\n|---|---|\n| x | k1 |\n")
    r = _run(reg, "k1", "| x | k1-updated |", "--key-col", "2")
    assert r.stdout.strip() == "upserted"
    assert "k1-updated" in reg.read_text()


def _upsert_key(args: tuple[str, int]) -> None:
    registry, i = args
    subprocess.run(
        [sys.executable, str(SCRIPT), registry, f"k-{i}", f"| k-{i} | row{i} | ok |"],
        check=True,
        capture_output=True,
    )


def test_concurrent_distinct_keys_no_lost_rows(tmp_path: Path) -> None:
    """25 processes each append a distinct key: all rows survive (no lost update)."""
    reg = tmp_path / "M.md"
    reg.write_text(HEADER)
    n = 25
    with ProcessPoolExecutor(max_workers=8) as ex:
        list(ex.map(_upsert_key, [(str(reg), i) for i in range(n)]))
    text = reg.read_text()
    for i in range(n):
        assert f"| k-{i} |" in text, f"lost row k-{i}"
