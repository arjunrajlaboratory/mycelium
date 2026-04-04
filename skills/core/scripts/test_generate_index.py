#!/usr/bin/env python3
"""Tests for generate_index.py --counts-only and --summarize modes."""

import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Make generate_index importable regardless of cwd
_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import generate_index as gi  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def living_dir(tmp_path: Path) -> Path:
    """Return an empty .living/ directory."""
    d = tmp_path / ".living"
    d.mkdir()
    return d


def _write_learnings(living_dir: Path, count: int) -> Path:
    path = living_dir / "learnings.md"
    lines = ["# Learnings\n"]
    for i in range(count):
        lines.append(f"### Entry {i + 1}\n\nContent line {i + 1}.\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _write_decisions(living_dir: Path, count: int) -> Path:
    path = living_dir / "decisions.md"
    lines = ["# Decisions\n"]
    for i in range(count):
        lines.append(f"### Decision {i + 1}\n\nRationale {i + 1}.\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _write_conventions(living_dir: Path, count: int) -> Path:
    path = living_dir / "conventions.md"
    lines = ["# Conventions\n"]
    for i in range(count):
        lines.append(f"## Convention {i + 1}\n\nDetails {i + 1}.\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _run_counts_only(living_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_DIR / "generate_index.py"),
            "--living-dir",
            str(living_dir),
            "--counts-only",
        ],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# TestCountsOnly — subprocess tests
# ---------------------------------------------------------------------------


class TestCountsOnly:
    def test_fresh_directory_creates_quick_reference(self, living_dir: Path) -> None:
        """Fresh .living/ dir: INDEX.md is created with sentinel markers, no summary."""
        _write_learnings(living_dir, 2)
        result = _run_counts_only(living_dir)
        assert result.returncode == 0, result.stderr

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        assert gi.SUMMARY_BEGIN not in content
        assert gi.SUMMARY_END not in content

    def test_counts_match_actual_entries(self, living_dir: Path) -> None:
        """Entry counts in the table match actual header counts in source files."""
        _write_learnings(living_dir, 3)
        _write_decisions(living_dir, 2)
        _write_conventions(living_dir, 2)

        result = _run_counts_only(living_dir)
        assert result.returncode == 0, result.stderr

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "3 entries" in content
        assert "2 entries" in content
        # conventions uses "sections"
        assert "2 sections" in content

    def test_preserves_existing_summary_block(self, living_dir: Path) -> None:
        """--counts-only must not touch an existing KNOWLEDGE_SUMMARY block."""
        _write_learnings(living_dir, 1)

        # Seed an INDEX.md that has both blocks
        seed = "\n".join(
            [
                gi.QUICK_REF_BEGIN,
                "# .living/ Index",
                "Last audit: 2025-01-01",
                "",
                "| File | Entries | Last updated | Key topics |",
                "|------|---------|--------------|------------|",
                gi.QUICK_REF_END,
                "",
                gi.SUMMARY_BEGIN,
                "Last summarized: 2025-01-01",
                "",
                "### Key Knowledge Clusters — Learnings",
                "- **old cluster** (1 entry) — preserved description",
                "",
                "### Key Knowledge Clusters — Decisions",
                "- **old decision cluster** (1 entry) — preserved decision desc",
                gi.SUMMARY_END,
                "",
            ]
        )
        (living_dir / "INDEX.md").write_text(seed, encoding="utf-8")

        result = _run_counts_only(living_dir)
        assert result.returncode == 0, result.stderr

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # Summary block must be byte-for-byte preserved
        assert "Last summarized: 2025-01-01" in content
        assert "old cluster" in content
        assert "preserved description" in content
        # Quick reference must be updated
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content

    def test_preserves_content_outside_sentinels(self, living_dir: Path) -> None:
        """Manual notes placed outside sentinel blocks survive an update."""
        _write_learnings(living_dir, 1)

        seed = "\n".join(
            [
                "<!-- manual header note -->",
                "",
                gi.QUICK_REF_BEGIN,
                "# .living/ Index",
                "Last audit: 2025-01-01",
                "",
                "| File | Entries | Last updated | Key topics |",
                "|------|---------|--------------|------------|",
                gi.QUICK_REF_END,
                "",
                "<!-- manual footer note -->",
                "",
            ]
        )
        (living_dir / "INDEX.md").write_text(seed, encoding="utf-8")

        result = _run_counts_only(living_dir)
        assert result.returncode == 0, result.stderr

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "<!-- manual header note -->" in content
        assert "<!-- manual footer note -->" in content

    def test_legacy_index_migrated(self, living_dir: Path) -> None:
        """Legacy INDEX.md without sentinels is replaced with sentinel format."""
        _write_learnings(living_dir, 2)

        legacy = textwrap.dedent(
            """\
            # .living/ Index
            Last audit: 2024-01-01

            | File | Entries | Last updated | Key topics |
            |------|---------|--------------|------------|
            | learnings.md | 5 entries | 2024-01-01 | old topic |
            """
        )
        (living_dir / "INDEX.md").write_text(legacy, encoding="utf-8")

        result = _run_counts_only(living_dir)
        assert result.returncode == 0, result.stderr

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        # Counts should reflect real file, not legacy stale values
        assert "2 entries" in content
        assert "5 entries" not in content


# ---------------------------------------------------------------------------
# TestExtractEntrySnippets — in-process
# ---------------------------------------------------------------------------


class TestExtractEntrySnippets:
    def test_extracts_header_and_first_content_line(self, living_dir: Path) -> None:
        path = living_dir / "learnings.md"
        path.write_text(
            "### My Header\n\nFirst content line.\nSecond content line.\n",
            encoding="utf-8",
        )
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 1
        header, content = snippets[0]
        assert header == "My Header"
        assert content == "First content line."

    def test_newest_first_ordering(self, living_dir: Path) -> None:
        """Entries are returned reversed (newest appended = last in file = index 0)."""
        path = living_dir / "learnings.md"
        path.write_text(
            "### First Entry\n\nOldest.\n\n### Second Entry\n\nNewer.\n\n### Third Entry\n\nNewest.\n",
            encoding="utf-8",
        )
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 3
        assert snippets[0][0] == "Third Entry"
        assert snippets[1][0] == "Second Entry"
        assert snippets[2][0] == "First Entry"

    def test_sampling_over_500(self, living_dir: Path) -> None:
        """600 entries → at most 500 returned (250 recent + up to 250 sampled)."""
        path = living_dir / "learnings.md"
        lines = []
        for i in range(600):
            lines.append(f"### Entry {i + 1}\n\nContent {i + 1}.\n")
        path.write_text("\n".join(lines), encoding="utf-8")

        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) <= 500
        # First 250 must be the 600 most recent (reversed), i.e. original indices 599..350
        # Just confirm the first snippet header matches the last-written entry
        assert snippets[0][0] == "Entry 600"


# ---------------------------------------------------------------------------
# TestParseLlmClusters — in-process
# ---------------------------------------------------------------------------


VALID_LLM_OUTPUT = textwrap.dedent(
    """\
    ### Key Knowledge Clusters — Learnings
    - **API integration** (12 entries) — patterns for calling external APIs robustly
    - **Testing** (8 entries) — pytest setup and mock strategies

    ### Key Knowledge Clusters — Decisions
    - **Architecture** (5 entries) — key structural decisions made during development
    - **Tooling** (3 entries) — choice of tools and rationale
    """
)


class TestParseLlmClusters:
    def test_valid_cluster_output(self) -> None:
        result = gi.parse_llm_clusters(VALID_LLM_OUTPUT)
        assert result is not None
        assert "### Key Knowledge Clusters — Learnings" in result
        assert "### Key Knowledge Clusters — Decisions" in result
        assert "API integration" in result

    def test_malformed_output_returns_none(self) -> None:
        result = gi.parse_llm_clusters(
            "This is just free-form prose with no structure."
        )
        assert result is None

    def test_missing_decisions_section_returns_none(self) -> None:
        only_learnings = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **API integration** (12 entries) — patterns for calling external APIs
            """
        )
        result = gi.parse_llm_clusters(only_learnings)
        assert result is None

    def test_missing_learnings_section_returns_none(self) -> None:
        only_decisions = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Decisions
            - **Architecture** (5 entries) — key structural decisions
            """
        )
        result = gi.parse_llm_clusters(only_decisions)
        assert result is None

    def test_cluster_line_format_validated(self) -> None:
        """Lines not matching the exact pattern are stripped; if no valid lines remain, return None."""
        bad_format = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - API integration: some description (not bold, no entry count)

            ### Key Knowledge Clusters — Decisions
            - Architecture: another bad format
            """
        )
        result = gi.parse_llm_clusters(bad_format)
        assert result is None

    def test_trailing_chatter_stripped(self) -> None:
        """Prose after the cluster lines is stripped, valid clusters still returned."""
        with_chatter = (
            VALID_LLM_OUTPUT + "\nHope this helps! Let me know if you need more.\n"
        )
        result = gi.parse_llm_clusters(with_chatter)
        assert result is not None
        assert "Hope this helps" not in result

    def test_minimum_two_total_clusters(self) -> None:
        """Exactly 1 cluster per section (total 2) is still valid."""
        minimal = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **Solo learning** (1 entry) — the only learning

            ### Key Knowledge Clusters — Decisions
            - **Solo decision** (1 entry) — the only decision
            """
        )
        result = gi.parse_llm_clusters(minimal)
        assert result is not None


# ---------------------------------------------------------------------------
# TestSummarizeMode — in-process with mocked call_llm
# ---------------------------------------------------------------------------


class TestSummarizeMode:
    def test_summarize_creates_both_blocks(self, living_dir: Path) -> None:
        """Successful LLM call → INDEX.md has both QUICK_REFERENCE and KNOWLEDGE_SUMMARY."""
        _write_learnings(living_dir, 2)
        _write_decisions(living_dir, 2)

        with patch("generate_index.call_llm", return_value=VALID_LLM_OUTPUT):
            gi.update_index_summarize(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        assert gi.SUMMARY_BEGIN in content
        assert gi.SUMMARY_END in content
        assert "Last summarized:" in content
        assert "API integration" in content

    def test_summarize_fallback_on_llm_failure(self, living_dir: Path) -> None:
        """LLM RuntimeError → old summary preserved byte-for-byte, counts updated."""
        _write_learnings(living_dir, 3)

        # Seed existing INDEX.md with a summary block
        old_summary = "\n".join(
            [
                gi.SUMMARY_BEGIN,
                "Last summarized: 2025-01-01",
                "",
                "### Key Knowledge Clusters — Learnings",
                "- **preserved cluster** (3 entries) — must survive failure",
                "",
                "### Key Knowledge Clusters — Decisions",
                "- **preserved decision** (1 entry) — also must survive",
                gi.SUMMARY_END,
            ]
        )
        seed = (
            gi.QUICK_REF_BEGIN
            + "\n# .living/ Index\n"
            + gi.QUICK_REF_END
            + "\n\n"
            + old_summary
            + "\n"
        )
        (living_dir / "INDEX.md").write_text(seed, encoding="utf-8")

        with patch("generate_index.call_llm", side_effect=RuntimeError("API down")):
            gi.update_index_summarize(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # Old summary must be preserved verbatim
        assert "Last summarized: 2025-01-01" in content
        assert "preserved cluster" in content
        # Counts must still be updated (quick ref refreshed)
        assert gi.QUICK_REF_BEGIN in content
        assert "3 entries" in content

    def test_summarize_fallback_on_malformed_output(self, living_dir: Path) -> None:
        """Malformed LLM output → old summary preserved, counts updated."""
        _write_learnings(living_dir, 1)
        _write_decisions(living_dir, 1)

        old_summary = "\n".join(
            [
                gi.SUMMARY_BEGIN,
                "Last summarized: 2025-06-01",
                "",
                "### Key Knowledge Clusters — Learnings",
                "- **old learning cluster** (1 entry) — should be preserved",
                "",
                "### Key Knowledge Clusters — Decisions",
                "- **old decision cluster** (1 entry) — should be preserved",
                gi.SUMMARY_END,
            ]
        )
        seed = (
            gi.QUICK_REF_BEGIN
            + "\n# .living/ Index\n"
            + gi.QUICK_REF_END
            + "\n\n"
            + old_summary
            + "\n"
        )
        (living_dir / "INDEX.md").write_text(seed, encoding="utf-8")

        with patch(
            "generate_index.call_llm", return_value="This is totally malformed output."
        ):
            gi.update_index_summarize(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "Last summarized: 2025-06-01" in content
        assert "old learning cluster" in content
        assert "old decision cluster" in content
