#!/usr/bin/env python3
"""Stress tests and edge-case tests for generate_index.py.

Covers scenarios NOT exercised by the existing 18 tests in test_generate_index.py:
- Edge cases in counts-only mode (empty files, unicode, large files, sentinel edge cases)
- Edge cases in extract_entry_snippets
- Edge cases in parse_llm_clusters
- Summarize-mode error paths
- build_quick_reference with log/findings/skills directories
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import generate_index as gi  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestCountsOnlyEdgeCases
# ---------------------------------------------------------------------------


class TestCountsOnlyEdgeCases:
    def test_empty_learnings_file_shows_zero_count(self, tmp_path: Path) -> None:
        """learnings.md with only a header → 0 entries."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        (living_dir / "learnings.md").write_text("# Learnings\n", encoding="utf-8")

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "0 entries" in content

    def test_single_entry_uses_singular(self, tmp_path: Path) -> None:
        """Exactly 1 learning → '1 entry' (not '1 entries')."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 1)

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "1 entry" in content
        assert "1 entries" not in content

    def test_empty_living_dir_creates_index_with_empty_table(
        self, tmp_path: Path
    ) -> None:
        """Empty .living/ dir → INDEX.md created with table structure but no data rows."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        assert "| File | Entries |" in content

    def test_only_conventions_table_has_conventions_row(self, tmp_path: Path) -> None:
        """Only conventions.md present → table contains conventions row with 'sections'."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_conventions(living_dir, 3)

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "conventions.md" in content
        assert "sections" in content
        assert "learnings.md" not in content
        assert "decisions.md" not in content

    def test_unicode_headers_counted_correctly(self, tmp_path: Path) -> None:
        """Headers with Japanese, emoji, and accented characters → counts still correct."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        path = living_dir / "learnings.md"
        path.write_text(
            "# Learnings\n"
            "### 日本語ヘッダー\n\nJapanese header entry.\n"
            "### Entry with émojis 🎉🔬\n\nEmoji header entry.\n"
            "### Café & résumé\n\nAccented header entry.\n",
            encoding="utf-8",
        )

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "3 entries" in content

    def test_large_file_marker_appears_over_500_lines(self, tmp_path: Path) -> None:
        """learnings.md with 600 entries (>500 lines) → '(large — read selectively)' in table."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 600)

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "large — read selectively" in content

    def test_partial_sentinel_begin_only_treated_as_legacy(
        self, tmp_path: Path
    ) -> None:
        """INDEX.md with QUICK_REF_BEGIN but no QUICK_REF_END → treated as legacy, fully replaced."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 2)

        # Partial sentinels — only BEGIN, no END
        partial = (
            gi.QUICK_REF_BEGIN
            + "\n# .living/ Index\nLast audit: 2020-01-01\n\norphaned content\n"
        )
        (living_dir / "INDEX.md").write_text(partial, encoding="utf-8")

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # Must have both sentinels now
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        # Stale content should be gone — counts reflect real file
        assert "2 entries" in content

    def test_double_sentinel_pairs_first_pair_used(self, tmp_path: Path) -> None:
        """INDEX.md with two QUICK_REF_BEGIN markers → first pair used; content valid."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 1)

        # Construct INDEX.md with a proper first block, then a second stray block
        double = "\n".join(
            [
                gi.QUICK_REF_BEGIN,
                "# .living/ Index",
                "Last audit: 2020-01-01",
                "| File | Entries | Last updated | Key topics |",
                "|------|---------|--------------|------------|",
                gi.QUICK_REF_END,
                "",
                "some notes",
                "",
                gi.QUICK_REF_BEGIN,
                "# .living/ Index",
                "stray duplicate block",
                gi.QUICK_REF_END,
                "",
            ]
        )
        (living_dir / "INDEX.md").write_text(double, encoding="utf-8")

        gi.update_index_counts_only(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # At least one proper sentinel pair must exist with current date/counts
        assert gi.QUICK_REF_BEGIN in content
        assert gi.QUICK_REF_END in content
        assert "1 entry" in content

    def test_readonly_index_raises_permission_error(self, tmp_path: Path) -> None:
        """Read-only INDEX.md → PermissionError raised when trying to update."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 1)

        index_path = living_dir / "INDEX.md"
        index_path.write_text("# existing\n", encoding="utf-8")
        os.chmod(index_path, 0o444)  # read-only

        try:
            with pytest.raises(PermissionError):
                gi.update_index_counts_only(living_dir)
        finally:
            # Restore permissions so tmp_path cleanup works
            os.chmod(index_path, 0o644)


# ---------------------------------------------------------------------------
# TestExtractEntrySnippetsEdgeCases
# ---------------------------------------------------------------------------


class TestExtractEntrySnippetsEdgeCases:
    def test_bullet_only_entry_uses_bullet_as_content(self, tmp_path: Path) -> None:
        """Entry with header + bullet lines but no prose → first bullet used as content."""
        path = tmp_path / "learnings.md"
        path.write_text(
            "### My Header\n\n- First bullet item\n- Second bullet item\n",
            encoding="utf-8",
        )
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 1
        _, content = snippets[0]
        assert content == "- First bullet item"

    def test_header_immediately_followed_by_header(self, tmp_path: Path) -> None:
        """Two consecutive headers → first entry has empty content string."""
        path = tmp_path / "learnings.md"
        path.write_text(
            "### First Header\n### Second Header\n\nActual content.\n",
            encoding="utf-8",
        )
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 2
        # Newest-first: second header is index 0
        second_header, second_content = snippets[0]
        assert second_header == "Second Header"
        assert second_content == "Actual content."
        first_header, first_content = snippets[1]
        assert first_header == "First Header"
        assert first_content == ""

    def test_entry_with_code_block_uses_fence_as_content(self, tmp_path: Path) -> None:
        """Entry where content starts with a code fence → fence line used as content."""
        path = tmp_path / "learnings.md"
        path.write_text(
            "### Code Example\n\n```python\nprint('hello')\n```\n",
            encoding="utf-8",
        )
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 1
        _, content = snippets[0]
        assert content == "```python"

    def test_empty_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Empty file → returns empty list."""
        path = tmp_path / "learnings.md"
        path.write_text("", encoding="utf-8")
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert snippets == []

    def test_headers_with_markdown_formatting_extracted_correctly(
        self, tmp_path: Path
    ) -> None:
        """Headers containing bold markers and date prefixes → header text extracted."""
        path = tmp_path / "learnings.md"
        path.write_text(
            "### [2026-04-01] **Bold** EZproxy fix\n\nFix description.\n",
            encoding="utf-8",
        )
        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 1
        header, _ = snippets[0]
        # The raw header text after stripping the "### " prefix
        assert "[2026-04-01] **Bold** EZproxy fix" in header

    def test_exactly_500_entries_returns_all_500(self, tmp_path: Path) -> None:
        """Exactly 500 entries → no sampling, all 500 returned."""
        path = tmp_path / "learnings.md"
        lines = []
        for i in range(500):
            lines.append(f"### Entry {i + 1}\n\nContent {i + 1}.\n")
        path.write_text("\n".join(lines), encoding="utf-8")

        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) == 500

    def test_501_entries_triggers_sampling(self, tmp_path: Path) -> None:
        """501 entries → sampling kicks in, returns ≤500."""
        path = tmp_path / "learnings.md"
        lines = []
        for i in range(501):
            lines.append(f"### Entry {i + 1}\n\nContent {i + 1}.\n")
        path.write_text("\n".join(lines), encoding="utf-8")

        snippets = gi.extract_entry_snippets(path, "learnings")
        assert len(snippets) <= 500
        # Most-recent entry must be first
        assert snippets[0][0] == "Entry 501"


# ---------------------------------------------------------------------------
# TestParseLlmClustersEdgeCases
# ---------------------------------------------------------------------------


class TestParseLlmClustersEdgeCases:
    def test_reversed_section_order_both_sections_validated(self) -> None:
        """Decisions section before Learnings → both still parsed and validated."""
        reversed_output = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Decisions
            - **Architecture** (5 entries) — key structural decisions

            ### Key Knowledge Clusters — Learnings
            - **API integration** (12 entries) — patterns for external APIs
            """
        )
        result = gi.parse_llm_clusters(reversed_output)
        assert result is not None
        assert "Key Knowledge Clusters — Learnings" in result
        assert "Key Knowledge Clusters — Decisions" in result

    def test_extra_blank_lines_between_sections_still_parses(self) -> None:
        """Many blank lines between sections and clusters → still valid."""
        with_blanks = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings


            - **API integration** (12 entries) — patterns for calling external APIs



            ### Key Knowledge Clusters — Decisions


            - **Architecture** (5 entries) — key structural decisions

            """
        )
        result = gi.parse_llm_clusters(with_blanks)
        assert result is not None

    def test_singular_entry_count_valid(self) -> None:
        """(1 entry) format → valid."""
        singular = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **Solo learning** (1 entry) — only one learning here

            ### Key Knowledge Clusters — Decisions
            - **Solo decision** (1 entry) — only one decision here
            """
        )
        result = gi.parse_llm_clusters(singular)
        assert result is not None

    def test_plural_entries_count_valid(self) -> None:
        """(12 entries) format → valid."""
        plural = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **Many learnings** (12 entries) — many things learned here

            ### Key Knowledge Clusters — Decisions
            - **Many decisions** (12 entries) — many decisions made here
            """
        )
        result = gi.parse_llm_clusters(plural)
        assert result is not None

    def test_mixed_singular_plural_all_valid(self) -> None:
        """Some lines use 'entry', others 'entries' → all valid."""
        mixed = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **Only one** (1 entry) — singleton learning
            - **Many** (5 entries) — several learnings

            ### Key Knowledge Clusters — Decisions
            - **Just one** (1 entry) — single decision
            - **Group** (3 entries) — grouped decisions
            """
        )
        result = gi.parse_llm_clusters(mixed)
        assert result is not None

    def test_cluster_name_with_parens_hyphens_ampersands_valid(self) -> None:
        """Bold cluster name containing parens, hyphens, and ampersands → valid."""
        special = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **CI/CD & Deploy (prod)** (4 entries) — deployment and integration patterns

            ### Key Knowledge Clusters — Decisions
            - **Auth & Security (v2-beta)** (2 entries) — security decisions
            """
        )
        result = gi.parse_llm_clusters(special)
        assert result is not None

    def test_preamble_before_first_header_is_stripped(self) -> None:
        """LLM outputs preamble prose before the first header → preamble stripped."""
        with_preamble = textwrap.dedent(
            """\
            Here are the clusters I identified from your knowledge base:

            ### Key Knowledge Clusters — Learnings
            - **API integration** (12 entries) — patterns for calling external APIs

            ### Key Knowledge Clusters — Decisions
            - **Architecture** (5 entries) — key structural decisions
            """
        )
        result = gi.parse_llm_clusters(with_preamble)
        assert result is not None
        assert "Here are the clusters" not in result

    def test_em_dash_in_description_is_valid(self) -> None:
        """Description separator uses em-dash (—) → valid (regex expects em-dash)."""
        em_dash = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **API** (3 entries) — uses em-dash separator here

            ### Key Knowledge Clusters — Decisions
            - **DB** (2 entries) — another em-dash description
            """
        )
        result = gi.parse_llm_clusters(em_dash)
        assert result is not None

    def test_regular_hyphen_separator_is_rejected(self) -> None:
        """Description separator uses regular hyphen (-) instead of em-dash → rejected."""
        hyphen_sep = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **API** (3 entries) - uses hyphen separator

            ### Key Knowledge Clusters — Decisions
            - **DB** (2 entries) - another hyphen description
            """
        )
        result = gi.parse_llm_clusters(hyphen_sep)
        # Lines with regular hyphens don't match cluster_re → sections have no valid clusters
        assert result is None

    def test_exactly_one_cluster_per_section_passes(self) -> None:
        """Exactly 1 cluster per section → minimum valid, passes."""
        minimal = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **Only learning** (1 entry) — the single learning

            ### Key Knowledge Clusters — Decisions
            - **Only decision** (1 entry) — the single decision
            """
        )
        result = gi.parse_llm_clusters(minimal)
        assert result is not None

    def test_large_output_50_clusters_all_preserved(self) -> None:
        """50 clusters per section → all preserved in output."""
        lines = ["### Key Knowledge Clusters — Learnings"]
        for i in range(50):
            lines.append(
                f"- **Cluster L{i + 1}** ({i + 1} entries) — description of cluster L{i + 1}"
            )
        lines.append("")
        lines.append("### Key Knowledge Clusters — Decisions")
        for i in range(50):
            lines.append(
                f"- **Cluster D{i + 1}** ({i + 1} entries) — description of cluster D{i + 1}"
            )

        result = gi.parse_llm_clusters("\n".join(lines))
        assert result is not None
        for i in range(50):
            assert f"Cluster L{i + 1}" in result
            assert f"Cluster D{i + 1}" in result

    def test_cluster_name_with_numbers_and_version_valid(self) -> None:
        """Cluster name like 'API v2.3 (legacy)' → valid."""
        versioned = textwrap.dedent(
            """\
            ### Key Knowledge Clusters — Learnings
            - **API v2.3 (legacy)** (4 entries) — old API version patterns

            ### Key Knowledge Clusters — Decisions
            - **Schema v1.0 (deprecated)** (2 entries) — old schema decisions
            """
        )
        result = gi.parse_llm_clusters(versioned)
        assert result is not None

    def test_whitespace_variations_leading_trailing_handled(self) -> None:
        """Trailing spaces at end of cluster lines → rstrip() in parser handles them, output valid."""
        # Build output with trailing spaces at end of cluster lines and blank lines
        output = (
            "### Key Knowledge Clusters — Learnings\n"
            "- **Clean cluster** (3 entries) — clean description   \n"
            "\n"
            "### Key Knowledge Clusters — Decisions\n"
            "- **Another cluster** (2 entries) — another description  \n"
        )
        result = gi.parse_llm_clusters(output)
        # rstrip() in the cleaned loop strips trailing spaces → cluster_re still matches
        assert result is not None
        assert "Clean cluster" in result
        assert "Another cluster" in result


# ---------------------------------------------------------------------------
# TestSummarizeModeEdgeCases
# ---------------------------------------------------------------------------


class TestSummarizeModeEdgeCases:
    @patch("generate_index.call_llm")
    def test_only_learnings_no_decisions_file(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """decisions.md doesn't exist → LLM still called with learnings only."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 3)
        # No decisions.md

        mock_llm.return_value = VALID_LLM_OUTPUT  # type: ignore[attr-defined]
        gi.update_index_summarize(living_dir)

        mock_llm.assert_called_once()  # type: ignore[attr-defined]
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.SUMMARY_BEGIN in content

    @patch("generate_index.call_llm")
    def test_only_decisions_no_learnings_file(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """learnings.md doesn't exist → LLM still called with decisions only."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_decisions(living_dir, 2)
        # No learnings.md

        mock_llm.return_value = VALID_LLM_OUTPUT  # type: ignore[attr-defined]
        gi.update_index_summarize(living_dir)

        mock_llm.assert_called_once()  # type: ignore[attr-defined]
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.SUMMARY_BEGIN in content

    @patch("generate_index.call_llm")
    def test_both_files_empty_no_llm_call(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """Both files exist but have 0 entries → no LLM call attempted, no summary block."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        (living_dir / "learnings.md").write_text("# Learnings\n", encoding="utf-8")
        (living_dir / "decisions.md").write_text("# Decisions\n", encoding="utf-8")

        gi.update_index_summarize(living_dir)

        mock_llm.assert_not_called()  # type: ignore[attr-defined]
        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.SUMMARY_BEGIN not in content

    @patch("generate_index.call_llm")
    def test_timeout_expired_fallback_to_old_summary(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """TimeoutExpired from LLM → fallback to existing old summary."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 2)

        old_summary = "\n".join(
            [
                gi.SUMMARY_BEGIN,
                "Last summarized: 2025-03-01",
                "",
                "### Key Knowledge Clusters — Learnings",
                "- **timeout preserved** (2 entries) — must survive timeout",
                "",
                "### Key Knowledge Clusters — Decisions",
                "- **timeout decision** (1 entry) — decision must survive",
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

        mock_llm.side_effect = subprocess.TimeoutExpired(  # type: ignore[attr-defined]
            cmd=["claude"], timeout=120
        )
        gi.update_index_summarize(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "Last summarized: 2025-03-01" in content
        assert "timeout preserved" in content

    @patch("generate_index.call_llm")
    def test_file_not_found_from_llm_fallback(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """FileNotFoundError (claude CLI not found) → fallback to old summary."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 2)

        old_summary = "\n".join(
            [
                gi.SUMMARY_BEGIN,
                "Last summarized: 2025-05-01",
                "",
                "### Key Knowledge Clusters — Learnings",
                "- **cli missing preserved** (2 entries) — survives missing CLI",
                "",
                "### Key Knowledge Clusters — Decisions",
                "- **cli decision** (1 entry) — also survives",
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

        mock_llm.side_effect = FileNotFoundError("claude: command not found")  # type: ignore[attr-defined]
        gi.update_index_summarize(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert "Last summarized: 2025-05-01" in content
        assert "cli missing preserved" in content

    @patch("generate_index.call_llm")
    def test_second_run_failure_preserves_first_run_summary(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """First summarize succeeds; second run fails → second run preserves first run's summary."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 2)
        _write_decisions(living_dir, 2)

        # First run — succeeds
        mock_llm.return_value = VALID_LLM_OUTPUT  # type: ignore[attr-defined]
        gi.update_index_summarize(living_dir)

        content_after_first = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.SUMMARY_BEGIN in content_after_first
        assert "API integration" in content_after_first

        # Second run — LLM fails
        mock_llm.side_effect = RuntimeError("LLM down on second run")  # type: ignore[attr-defined]
        gi.update_index_summarize(living_dir)

        content_after_second = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        # First run's summary must still be present
        assert gi.SUMMARY_BEGIN in content_after_second
        assert "API integration" in content_after_second

    @patch("generate_index.call_llm")
    def test_summarize_on_fresh_dir_no_prior_summary_on_failure(
        self, mock_llm: object, tmp_path: Path
    ) -> None:
        """Fresh dir + LLM failure → INDEX.md has counts but no summary block."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        _write_learnings(living_dir, 3)

        mock_llm.side_effect = RuntimeError("LLM unavailable")  # type: ignore[attr-defined]
        gi.update_index_summarize(living_dir)

        content = (living_dir / "INDEX.md").read_text(encoding="utf-8")
        assert gi.QUICK_REF_BEGIN in content
        assert "3 entries" in content
        assert gi.SUMMARY_BEGIN not in content
        assert gi.SUMMARY_END not in content


# ---------------------------------------------------------------------------
# TestBuildQuickReference
# ---------------------------------------------------------------------------


class TestBuildQuickReference:
    def test_log_directory_row_appears(self, tmp_path: Path) -> None:
        """With .living/log/ containing session files → log/ row appears in table."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        log_dir = living_dir / "log"
        log_dir.mkdir()
        # Create a session file with proper YYYY-MM-DD-NNN-slug naming
        (log_dir / "2026-04-01-001-myproject.md").write_text(
            "# Session\nSession content.\n", encoding="utf-8"
        )
        (log_dir / "2026-04-02-002-myproject.md").write_text(
            "# Session\nMore content.\n", encoding="utf-8"
        )

        quick_ref = gi.build_quick_reference(living_dir)

        assert "log/" in quick_ref
        assert "sessions" in quick_ref

    def test_findings_directory_row_appears(self, tmp_path: Path) -> None:
        """With .living/findings/ containing topic files with ## F- headers → findings/ row appears."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        findings_dir = living_dir / "findings"
        findings_dir.mkdir()

        topic_file = findings_dir / "rnaseq-analysis.md"
        topic_file.write_text(
            "# RNAseq Analysis\n\n## F-001 First finding\n\nContent.\n\n## F-002 Second finding\n\nContent.\n",
            encoding="utf-8",
        )

        quick_ref = gi.build_quick_reference(living_dir)

        assert "findings/" in quick_ref
        assert "findings" in quick_ref

    def test_skills_directory_shows_skills_section(self, tmp_path: Path) -> None:
        """With .living/skills/ directory → skills section appears in output."""
        living_dir = tmp_path / ".living"
        living_dir.mkdir()
        skills_dir = living_dir / "skills"
        skills_dir.mkdir()
        (skills_dir / "spatial-biology.md").write_text(
            "# Spatial Biology Skills\n", encoding="utf-8"
        )

        quick_ref = gi.build_quick_reference(living_dir)

        assert "skills" in quick_ref.lower()

    def test_date_accuracy_matches_today(self, tmp_path: Path) -> None:
        """'Last audit:' date in the quick reference matches today's date."""
        from datetime import datetime

        living_dir = tmp_path / ".living"
        living_dir.mkdir()

        today = datetime.now().strftime("%Y-%m-%d")
        quick_ref = gi.build_quick_reference(living_dir)

        assert f"Last audit: {today}" in quick_ref
