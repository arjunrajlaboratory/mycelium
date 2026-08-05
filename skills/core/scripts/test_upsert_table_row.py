"""Concurrency and validation tests for locked Markdown table upserts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import stat
import subprocess
import sys


SCRIPT = Path(__file__).parent / "upsert_table_row.py"
HEADER = "| ID | Title | Status |\n|----|-------|--------|\n"


def run_upsert(table: Path, key: str, title: str) -> subprocess.CompletedProcess:
    row = f"| {key} | {title} | open |"
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(table), key, row],
        capture_output=True,
        text=True,
        check=False,
    )


def test_concurrent_rows_are_all_preserved_with_original_mode(tmp_path: Path) -> None:
    table = tmp_path / "TODO_REGISTRY.md"
    table.write_text(HEADER)
    os.chmod(table, 0o640)
    keys = [f"T-{index:03d}" for index in range(1, 31)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda key: run_upsert(table, key, f"Todo {key}"), keys))

    assert all(result.returncode == 0 for result in results)
    content = table.read_text()
    assert all(content.count(f"| {key} |") == 1 for key in keys)
    assert stat.S_IMODE(table.stat().st_mode) == 0o640


def test_rejects_mismatched_key_without_mutating_table(tmp_path: Path) -> None:
    table = tmp_path / "FINDINGS_REGISTRY.md"
    table.write_text(HEADER)
    original = table.read_bytes()

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(table), "F-001", "| F-002 | Claim | open |"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert table.read_bytes() == original


def test_key_equal_to_header_label_appends_without_replacing_header(
    tmp_path: Path,
) -> None:
    table = tmp_path / "TODO_REGISTRY.md"
    table.write_text(HEADER)

    result = run_upsert(table, "ID", "Literal header-name todo")

    assert result.returncode == 0, result.stderr
    content = table.read_text()
    assert content.startswith(HEADER)
    assert content.count("| ID |") == 2
    assert "Literal header-name todo" in content
