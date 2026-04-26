#!/usr/bin/env python3
"""Tests for recall_lessons.py."""

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import recall_lessons as rl  # noqa: E402


@pytest.fixture()
def living_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".living"
    d.mkdir()
    return d


def _seed(living_dir: Path) -> None:
    """Common fixture: 4 learnings, 2 decisions across a few tags/dates."""
    (living_dir / "learnings.md").write_text(
        "# Learnings\n\n"
        "### [2026-01-15] Old debugging insight\n"
        "**Tags**: [debugging, pytest]\n\n"
        "Body of L-1.\n\n"
        "### [2026-03-01] Pydantic strict mode trap\n"
        "**Tags**: [pydantic, gotcha]\n\n"
        "Body of L-2.\n\n"
        "### [2026-04-01] Recent debugging tip\n"
        "**Tags**: [debugging, asyncio]\n\n"
        "Body of L-3.\n\n"
        "### Untagged untitled\n\n"
        "Body of L-4.\n",
        encoding="utf-8",
    )
    (living_dir / "decisions.md").write_text(
        "# Decisions\n\n"
        "### [2026-02-10] Adopt structlog\n"
        "**Tags**: [logging, infrastructure]\n\n"
        "Rationale of D-1.\n\n"
        "### [2026-04-10] Use pytest-asyncio\n"
        "**Tags**: [testing, asyncio]\n\n"
        "Rationale of D-2.\n",
        encoding="utf-8",
    )


class TestRecall:
    def test_filter_by_single_tag(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, tags=["debugging"])
        ids = {r["id"] for r in results}
        assert ids == {"L-1", "L-3"}

    def test_filter_by_multiple_tags_any_match(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, tags=["pydantic", "logging"])
        ids = {r["id"] for r in results}
        assert ids == {"L-2", "D-1"}

    def test_filter_by_id(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, ids=["L-3"])
        assert len(results) == 1
        assert results[0]["id"] == "L-3"
        assert "Recent debugging tip" in results[0]["title"]

    def test_filter_by_id_combined_with_tag(self, living_dir: Path) -> None:
        """Tag and ID filters both must match (AND across filter types)."""
        _seed(living_dir)
        # L-3 has debugging, L-2 doesn't
        results = rl.recall(living_dir, tags=["debugging"], ids=["L-2", "L-3"])
        assert {r["id"] for r in results} == {"L-3"}

    def test_filter_since(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, since="2026-03-15")
        ids = {r["id"] for r in results}
        assert ids == {"L-3", "D-2"}

    def test_since_excludes_undated_entries(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, since="2020-01-01")
        ids = {r["id"] for r in results}
        # L-4 has no date, must be excluded
        assert "L-4" not in ids

    def test_file_filter_learnings_only(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, tags=["asyncio"], file_filter="learnings")
        assert {r["id"] for r in results} == {"L-3"}

    def test_file_filter_decisions_only(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, tags=["asyncio"], file_filter="decisions")
        assert {r["id"] for r in results} == {"D-2"}

    def test_max_results_limit(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, tags=["debugging"], max_results=1)
        assert len(results) == 1
        # Most recent first — L-3 wins
        assert results[0]["id"] == "L-3"

    def test_no_filters_returns_everything_capped(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir)
        # 4 learnings + 2 decisions = 6 total
        assert len(results) == 6

    def test_no_match_returns_empty(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, tags=["nonexistent-tag"])
        assert results == []

    def test_entry_text_includes_body(self, living_dir: Path) -> None:
        _seed(living_dir)
        results = rl.recall(living_dir, ids=["L-2"])
        text = results[0]["text"]
        assert "Pydantic strict mode trap" in text
        assert "**Tags**: [pydantic, gotcha]" in text
        assert "Body of L-2." in text


class TestCli:
    def test_subprocess_returns_zero_on_match(self, living_dir: Path) -> None:
        _seed(living_dir)
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "recall_lessons.py"),
                "--living-dir",
                str(living_dir),
                "--tag",
                "debugging",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Recent debugging tip" in result.stdout
        assert "Old debugging insight" in result.stdout

    def test_subprocess_returns_one_on_no_match(self, living_dir: Path) -> None:
        _seed(living_dir)
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "recall_lessons.py"),
                "--living-dir",
                str(living_dir),
                "--tag",
                "nonexistent",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1

    def test_count_only_prints_count_not_text(self, living_dir: Path) -> None:
        _seed(living_dir)
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "recall_lessons.py"),
                "--living-dir",
                str(living_dir),
                "--tag",
                "debugging",
                "--count-only",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "2 matches" in result.stdout
        assert "Recent debugging tip" not in result.stdout

    def test_repeatable_tag_flag(self, living_dir: Path) -> None:
        _seed(living_dir)
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "recall_lessons.py"),
                "--living-dir",
                str(living_dir),
                "--tag",
                "pydantic",
                "--tag",
                "logging",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "L-2" in result.stdout
        assert "D-1" in result.stdout

    def test_bad_living_dir_exits_two(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "recall_lessons.py"),
                "--living-dir",
                str(tmp_path / "nonexistent"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
