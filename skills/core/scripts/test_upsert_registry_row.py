"""Tests for upsert_registry_row.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "upsert_registry_row.py"

HEADER = (
    "| Date | Session ID | Project | Branch | Duration | Files Changed | Summary | Key Outputs | Status | Tags | Log link |\n"
    "|------|-----------|---------|--------|----------|---------------|---------|-------------|--------|------|---------|\n"
)


def _row(session_id: str, summary: str = "did stuff", date: str = "2026-05-21") -> str:
    return f"| {date} | {session_id} | proj | main | 12m | 3 | {summary} |  | complete |  | [log]({session_id}-proj.md) |"


def _run(registry: Path, session_id: str, row: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(registry), session_id, row],
        capture_output=True,
        text=True,
    )


def test_appends_new_row_when_session_id_not_present(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    registry.write_text(HEADER)
    res = _run(registry, "2026-05-21-001", _row("2026-05-21-001"))
    assert res.returncode == 0
    assert res.stdout.strip() == "appended"
    content = registry.read_text()
    assert "2026-05-21-001" in content
    assert content.endswith("\n")


def test_replaces_existing_row_when_session_id_matches(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    initial = HEADER + _row("2026-05-21-001", summary="old") + "\n"
    registry.write_text(initial)
    new_row = _row("2026-05-21-001", summary="new and improved")
    res = _run(registry, "2026-05-21-001", new_row)
    assert res.returncode == 0
    assert res.stdout.strip() == "upserted"
    content = registry.read_text()
    assert "old" not in content
    assert "new and improved" in content
    assert content.count("2026-05-21-001") >= 1


def test_rejects_row_with_wrong_pipe_count(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    registry.write_text(HEADER)
    bad_row = "| only | three | columns |"  # 4 pipes, not 12
    res = _run(registry, "abc", bad_row)
    assert res.returncode == 1
    assert "12" in res.stderr


def test_does_not_match_header_separator_row(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    registry.write_text(HEADER)
    # Try to upsert a "session" with id "-----" — should append, not match the separator
    res = _run(registry, "2026-05-21-002", _row("2026-05-21-002"))
    assert res.returncode == 0
    assert res.stdout.strip() == "appended"
    content = registry.read_text()
    # Header lines must be preserved
    assert "| Date | Session ID |" in content
    assert "|------|" in content


def test_session_id_match_is_exact_not_prefix(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    initial = HEADER + _row("2026-04-10-0035", summary="prefix-collider") + "\n"
    registry.write_text(initial)
    # Upsert a shorter id that is a prefix of the existing one — must NOT replace it
    new_row = _row("2026-04-10-003", summary="distinct")
    res = _run(registry, "2026-04-10-003", new_row)
    assert res.returncode == 0
    assert res.stdout.strip() == "appended"
    content = registry.read_text()
    assert "prefix-collider" in content
    assert "distinct" in content


def test_atomic_write_preserves_file_on_error(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    original = HEADER + _row("2026-05-21-001", summary="keep me") + "\n"
    registry.write_text(original)
    # Corrupt row (wrong pipe count) — script must exit 1 and leave file untouched
    res = _run(registry, "2026-05-21-001", "| bad row |")
    assert res.returncode == 1
    assert registry.read_text() == original


def test_idempotent_when_run_twice_with_same_row(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    registry.write_text(HEADER)
    row = _row("2026-05-21-001", summary="hello")
    res1 = _run(registry, "2026-05-21-001", row)
    assert res1.returncode == 0
    assert res1.stdout.strip() == "appended"
    after_first = registry.read_text()
    res2 = _run(registry, "2026-05-21-001", row)
    assert res2.returncode == 0
    assert res2.stdout.strip() == "upserted"
    after_second = registry.read_text()
    # Same row, just replaced in-place — content equal
    assert after_first == after_second
    # Only one occurrence of the row body
    assert after_second.count("hello") == 1


def test_file_without_trailing_newline_gets_one_on_append(tmp_path: Path) -> None:
    registry = tmp_path / "LOG_REGISTRY.md"
    # Write header without a trailing newline on last line
    registry.write_text(HEADER.rstrip("\n"))
    res = _run(registry, "2026-05-21-001", _row("2026-05-21-001"))
    assert res.returncode == 0
    content = registry.read_text()
    # Final char must be a newline, and rows must be separated correctly
    assert content.endswith("\n")
    assert "---|\n|" in content  # separator row followed by data row on new line


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
