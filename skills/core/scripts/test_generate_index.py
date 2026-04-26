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


# ---------------------------------------------------------------------------
# TestParseTagLine
# ---------------------------------------------------------------------------


class TestParseTagLine:
    def test_bracketed_bold_format(self) -> None:
        assert gi.parse_tag_line("**Tags**: [foo, bar, baz]") == ["foo", "bar", "baz"]

    def test_bare_bold_format(self) -> None:
        assert gi.parse_tag_line("**Tags**: foo, bar") == ["foo", "bar"]

    def test_unbolded_format(self) -> None:
        assert gi.parse_tag_line("Tags: foo, bar") == ["foo", "bar"]

    def test_single_tag(self) -> None:
        assert gi.parse_tag_line("**Tags**: [solo]") == ["solo"]

    def test_empty_brackets(self) -> None:
        assert gi.parse_tag_line("**Tags**: []") == []

    def test_empty_value(self) -> None:
        assert gi.parse_tag_line("**Tags**: ") == []

    def test_non_tag_line(self) -> None:
        assert gi.parse_tag_line("**Category**: gotcha") == []
        assert gi.parse_tag_line("Some other content") == []
        assert gi.parse_tag_line("") == []

    def test_blockquoted_tag_line(self) -> None:
        assert gi.parse_tag_line("> **Tags**: [a, b]") == ["a", "b"]

    def test_extra_whitespace(self) -> None:
        assert gi.parse_tag_line("**Tags**:   foo,  bar  ,baz") == ["foo", "bar", "baz"]


# ---------------------------------------------------------------------------
# TestCollectEntries
# ---------------------------------------------------------------------------


class TestCollectEntries:
    def test_extracts_id_title_date_tags(self, living_dir: Path) -> None:
        """Entry with date and tags produces a complete record."""
        path = living_dir / "learnings.md"
        path.write_text(
            "# Learnings\n\n"
            "### [2026-04-01] First lesson\n"
            "**Tags**: [debugging, pydantic]\n\n"
            "Body content.\n\n"
            "### [2026-04-02] Second lesson\n"
            "**Tags**: testing\n\n"
            "More body.\n",
            encoding="utf-8",
        )
        entries = gi.collect_entries(path, "learnings", "L")

        assert len(entries) == 2
        assert entries[0]["id"] == "L-1"
        assert entries[0]["title"] == "First lesson"
        assert entries[0]["date"] == "2026-04-01"
        assert entries[0]["tags"] == ["debugging", "pydantic"]
        assert entries[1]["id"] == "L-2"
        assert entries[1]["tags"] == ["testing"]

    def test_handles_missing_tags(self, living_dir: Path) -> None:
        path = living_dir / "learnings.md"
        path.write_text(
            "### [2026-04-01] Untagged\n\nBody.\n",
            encoding="utf-8",
        )
        entries = gi.collect_entries(path, "learnings", "L")
        assert entries[0]["tags"] == []

    def test_handles_missing_date(self, living_dir: Path) -> None:
        path = living_dir / "learnings.md"
        path.write_text(
            "### Untitled lesson\n**Tags**: [misc]\n\nBody.\n",
            encoding="utf-8",
        )
        entries = gi.collect_entries(path, "learnings", "L")
        assert entries[0]["date"] == ""
        assert entries[0]["title"] == "Untitled lesson"

    def test_only_first_tags_line_per_entry(self, living_dir: Path) -> None:
        """If an entry has multiple Tags lines (rare), only the first counts."""
        path = living_dir / "learnings.md"
        path.write_text(
            "### [2026-04-01] Test\n"
            "**Tags**: [first]\n"
            "Some body.\n"
            "**Tags**: [second]\n",
            encoding="utf-8",
        )
        entries = gi.collect_entries(path, "learnings", "L")
        assert entries[0]["tags"] == ["first"]


# ---------------------------------------------------------------------------
# TestHeuristicSummary
# ---------------------------------------------------------------------------


def _write_tagged_learnings(living_dir: Path, entries: list[tuple[str, str, list[str]]]) -> Path:
    """Write a learnings.md from (date, title, tags) tuples."""
    path = living_dir / "learnings.md"
    lines = ["# Learnings\n\n"]
    for date, title, tags in entries:
        date_part = f"[{date}] " if date else ""
        lines.append(f"### {date_part}{title}\n")
        if tags:
            lines.append(f"**Tags**: [{', '.join(tags)}]\n")
        lines.append("\nBody content.\n\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


class TestHeuristicSummary:
    def test_emits_sentinels_when_empty(self, living_dir: Path) -> None:
        """No entries → still a valid sentinel-wrapped block."""
        block = gi.build_heuristic_summary(living_dir)
        assert block.startswith(gi.SUMMARY_BEGIN)
        assert block.endswith(gi.SUMMARY_END)
        assert "(heuristic)" in block

    def test_clusters_top_tags(self, living_dir: Path) -> None:
        _write_tagged_learnings(
            living_dir,
            [
                ("2026-04-01", "A", ["debugging", "pytest"]),
                ("2026-04-02", "B", ["debugging"]),
                ("2026-04-03", "C", ["debugging", "asyncio"]),
                ("2026-04-04", "D", ["pytest"]),
                ("2026-04-05", "E", ["asyncio"]),
                ("2026-04-06", "F", ["solo"]),
            ],
        )
        block = gi.build_heuristic_summary(living_dir)
        # debugging has 3, pytest has 2, asyncio has 2 — all included
        assert "**debugging** (3 entries)" in block
        assert "**pytest** (2 entries)" in block
        assert "**asyncio** (2 entries)" in block
        # `solo` (1 entry) must NOT appear in the headline "Tag clusters" block
        cluster_section = block.split("## Most recent")[0]
        assert "**solo**" not in cluster_section

    def test_singleton_tags_appear_in_inverted_index(self, living_dir: Path) -> None:
        """The full inverted index must include singleton tags — they're valid
        recall targets. Only the headline 'Tag clusters' filters by ≥2."""
        _write_tagged_learnings(
            living_dir,
            [
                ("2026-04-01", "A", ["popular", "popular-too"]),
                ("2026-04-02", "B", ["popular", "popular-too"]),
                ("2026-04-03", "C", ["singleton-tag"]),
            ],
        )
        block = gi.build_heuristic_summary(living_dir)
        by_tag_section = block.split("## By tag")[1]
        assert "`singleton-tag`" in by_tag_section
        assert "L-3" in by_tag_section

    def test_recent_section_sorted_by_date(self, living_dir: Path) -> None:
        _write_tagged_learnings(
            living_dir,
            [
                ("2026-01-01", "Old entry", ["x", "y"]),
                ("2026-04-01", "New entry", ["x", "y"]),
                ("2026-03-01", "Middle entry", ["x", "y"]),
            ],
        )
        block = gi.build_heuristic_summary(living_dir)
        recent_section = block.split("## Most recent")[1].split("## By tag")[0]
        # New entry should appear before Middle, before Old
        assert recent_section.index("New entry") < recent_section.index("Middle entry")
        assert recent_section.index("Middle entry") < recent_section.index("Old entry")

    def test_inverted_index_lists_all_ids(self, living_dir: Path) -> None:
        """The 'By tag' section maps tag → all matching IDs (T2)."""
        _write_tagged_learnings(
            living_dir,
            [
                ("2026-04-01", "A", ["debugging"]),
                ("2026-04-02", "B", ["debugging"]),
                ("2026-04-03", "C", ["debugging"]),
            ],
        )
        block = gi.build_heuristic_summary(living_dir)
        by_tag_section = block.split("## By tag")[1]
        # All three IDs present in the inverted index
        assert "L-1" in by_tag_section
        assert "L-2" in by_tag_section
        assert "L-3" in by_tag_section
        assert "`debugging`" in by_tag_section

    def test_inverted_index_combines_learnings_and_decisions(self, living_dir: Path) -> None:
        _write_tagged_learnings(living_dir, [("2026-04-01", "L1", ["shared"])])
        decisions_path = living_dir / "decisions.md"
        decisions_path.write_text(
            "# Decisions\n\n"
            "### [2026-04-02] D1\n"
            "**Tags**: [shared]\n\n"
            "Body.\n",
            encoding="utf-8",
        )
        block = gi.build_heuristic_summary(living_dir)
        # Both L-1 and D-1 should appear under the shared tag
        by_tag_section = block.split("## By tag")[1]
        assert "L-1" in by_tag_section
        assert "D-1" in by_tag_section


# ---------------------------------------------------------------------------
# TestUpdateIndexSummaryHeuristic
# ---------------------------------------------------------------------------


class TestUpdateIndexSummaryHeuristic:
    def test_creates_both_blocks_on_fresh_index(self, living_dir: Path) -> None:
        _write_tagged_learnings(
            living_dir,
            [
                ("2026-04-01", "A", ["x", "y"]),
                ("2026-04-02", "B", ["x"]),
            ],
        )
        gi.update_index_summary_heuristic(living_dir)
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        assert gi.SUMMARY_BEGIN in content
        assert gi.SUMMARY_END in content
        # Quick ref appears before summary
        assert content.index(gi.QUICK_REF_BEGIN) < content.index(gi.SUMMARY_BEGIN)

    def test_idempotent_rerun(self, living_dir: Path) -> None:
        """Running twice yields stable structure (modulo today's date)."""
        _write_tagged_learnings(living_dir, [("2026-04-01", "A", ["x", "y"])])
        gi.update_index_summary_heuristic(living_dir)
        first = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        gi.update_index_summary_heuristic(living_dir)
        second = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # Second run should not duplicate sentinels
        assert first.count(gi.SUMMARY_BEGIN) == 1
        assert second.count(gi.SUMMARY_BEGIN) == 1
        assert first.count(gi.QUICK_REF_BEGIN) == 1
        assert second.count(gi.QUICK_REF_BEGIN) == 1

    def test_replaces_existing_summary_block(self, living_dir: Path) -> None:
        """A stale summary block is replaced, not appended."""
        _write_tagged_learnings(living_dir, [("2026-04-01", "A", ["x", "y"])])
        seed = "\n".join(
            [
                gi.QUICK_REF_BEGIN,
                "# .living/ Index",
                gi.QUICK_REF_END,
                "",
                gi.SUMMARY_BEGIN,
                "STALE_CONTENT_MARKER",
                gi.SUMMARY_END,
                "",
            ]
        )
        (living_dir / "INDEX.md").write_text(seed, encoding="utf-8")

        gi.update_index_summary_heuristic(living_dir)
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "STALE_CONTENT_MARKER" not in content
        assert content.count(gi.SUMMARY_BEGIN) == 1

    def test_legacy_index_rebuilt_from_scratch(self, living_dir: Path) -> None:
        """A pre-sentinel legacy INDEX.md must be replaced wholesale, not
        prepended. Otherwise stale tables linger after migration."""
        _write_tagged_learnings(living_dir, [("2026-04-01", "A", ["x", "y"])])
        legacy = (
            "# .living/ Index\n"
            "Last audit: 2025-08-01\n\n"
            "| File | Entries | Last updated | Key topics |\n"
            "|------|---------|--------------|------------|\n"
            "| learnings.md | STALE_LEGACY_TABLE | 2025-01-01 | old topics |\n"
        )
        (living_dir / "INDEX.md").write_text(legacy, encoding="utf-8")

        gi.update_index_summary_heuristic(living_dir)
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # Stale content must be GONE — not coexisting with new content
        assert "STALE_LEGACY_TABLE" not in content
        # Both sentinel blocks must be present in the rebuilt file
        assert gi.QUICK_REF_BEGIN in content
        assert gi.SUMMARY_BEGIN in content

    def test_inserts_after_quick_ref_when_summary_missing(
        self, living_dir: Path
    ) -> None:
        _write_tagged_learnings(living_dir, [("2026-04-01", "A", ["x", "y"])])
        seed = "\n".join(
            [
                gi.QUICK_REF_BEGIN,
                "# .living/ Index",
                gi.QUICK_REF_END,
                "",
            ]
        )
        (living_dir / "INDEX.md").write_text(seed, encoding="utf-8")

        gi.update_index_summary_heuristic(living_dir)
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.SUMMARY_BEGIN in content
        assert content.index(gi.QUICK_REF_END) < content.index(gi.SUMMARY_BEGIN)


# ---------------------------------------------------------------------------
# TestSummaryHeuristicCli
# ---------------------------------------------------------------------------


class TestSummaryHeuristicCli:
    def test_subprocess_writes_summary_block(self, living_dir: Path) -> None:
        _write_tagged_learnings(
            living_dir,
            [
                ("2026-04-01", "A", ["debugging"]),
                ("2026-04-02", "B", ["debugging"]),
            ],
        )
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "generate_index.py"),
                "--living-dir",
                str(living_dir),
                "--summary-heuristic",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.SUMMARY_BEGIN in content
        assert "**debugging** (2 entries)" in content

    def test_dry_run_prints_without_writing(self, living_dir: Path) -> None:
        _write_tagged_learnings(living_dir, [("2026-04-01", "A", ["x", "y"])])
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "generate_index.py"),
                "--living-dir",
                str(living_dir),
                "--summary-heuristic",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert gi.SUMMARY_BEGIN in result.stdout
        # Dry run must not write the INDEX
        assert not (living_dir / "INDEX.md").exists()
