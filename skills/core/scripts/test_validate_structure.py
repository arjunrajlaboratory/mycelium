"""Tests for mycelium repository structure validation.

Focused on ``check_entry_heading_levels``, which turns a previously silent
failure into a reported one: an append-only knowledge log whose entries use a
heading level no ``generate_index.py`` parser recognises is indexed as zero
entries with no error anywhere (issue #76).
"""

from pathlib import Path

import generate_index as gi
from validate_structure import (
    ENTRY_HEADING_PREFIX,
    ValidationResult,
    check_entry_heading_levels,
)


def _living(tmp_path: Path, **files: str) -> Path:
    """Create ``.living/`` under tmp_path containing the given files."""
    living = tmp_path / ".living"
    living.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (living / name.replace("_", ".")).write_text(content, encoding="utf-8")
    return tmp_path


def _warnings(target_dir: Path) -> list[str]:
    result = ValidationResult()
    check_entry_heading_levels(target_dir, result)
    assert result.errors == [], f"unexpected errors: {result.errors}"
    return result.warnings


# ---------------------------------------------------------------------------
# Positive cases — mislevelled entries must be reported
# ---------------------------------------------------------------------------


def test_dated_h2_entry_in_decisions_is_flagged(tmp_path: Path) -> None:
    """The exact reproduction from issue #76 is no longer silent."""
    target = _living(
        tmp_path,
        decisions_md=(
            "# Decisions\n\n"
            "## [2026-08-25] Example decision\n\n"
            "**Context**: copied verbatim from the template\n"
            "**Decision**: use the template's heading level\n"
        ),
    )
    warnings = _warnings(target)
    assert len(warnings) == 1
    assert "decisions.md" in warnings[0]


def test_dated_h2_entry_in_learnings_is_flagged(tmp_path: Path) -> None:
    """learnings.md is subject to the same parser contract as decisions.md."""
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n## [2026-08-25] A learning\n\n**Category**: gotcha\n",
    )
    warnings = _warnings(target)
    assert len(warnings) == 1
    assert "learnings.md" in warnings[0]


def test_warning_states_the_required_heading_level_and_line_numbers(
    tmp_path: Path,
) -> None:
    """The warning has to be actionable without reading generate_index.py."""
    target = _living(
        tmp_path,
        decisions_md="# Decisions\n\n## [2026-08-25] Example decision\n\nbody\n",
    )
    (warning,) = _warnings(target)
    assert ENTRY_HEADING_PREFIX.strip() in warning
    assert "line 3" in warning


def test_mixed_levels_are_flagged_even_though_some_entries_parse(
    tmp_path: Path,
) -> None:
    """A partially-correct file is the sneakiest case: the count looks healthy."""
    target = _living(
        tmp_path,
        learnings_md=(
            "# Learnings\n\n"
            "### [2026-08-01] Parsed fine\n\nbody\n\n"
            "## [2026-08-25] Silently dropped\n\nbody\n"
        ),
    )
    (warning,) = _warnings(target)
    assert "learnings.md" in warning
    assert "line 7" in warning


def test_dated_h4_entry_is_flagged(tmp_path: Path) -> None:
    """Any level other than the parser's is dropped, not just ``##``."""
    target = _living(
        tmp_path,
        decisions_md="# Decisions\n\n#### [2026-08-25] Too deep\n\nbody\n",
    )
    assert len(_warnings(target)) == 1


def test_bare_date_entry_without_brackets_is_flagged(tmp_path: Path) -> None:
    """Entries are also written without surrounding brackets."""
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n## 2026-08-25 Bare date entry\n\nbody\n",
    )
    assert len(_warnings(target)) == 1


def test_tab_separated_h3_is_flagged_because_parsers_need_a_literal_space(
    tmp_path: Path,
) -> None:
    """The check mirrors the parsers, which match the literal prefix '### '."""
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n###\t[2026-08-25] Tab separated\n",
    )
    path = tmp_path / ".living" / "learnings.md"
    count, _ = gi.count_headers_and_topics(path, "learnings")
    assert count == 0, "precondition: the parser does not read this heading"
    assert len(_warnings(target)) == 1


def test_both_logs_are_reported_independently(tmp_path: Path) -> None:
    target = _living(
        tmp_path,
        learnings_md="## [2026-08-25] One\n",
        decisions_md="## [2026-08-25] Two\n",
    )
    warnings = _warnings(target)
    assert len(warnings) == 2
    assert {"learnings.md" in w for w in warnings} == {True, False}


# ---------------------------------------------------------------------------
# Negative cases — correct or unrelated markdown must stay silent
# ---------------------------------------------------------------------------


def test_correctly_levelled_entries_are_not_flagged(tmp_path: Path) -> None:
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n### [2026-08-25] Fine\n\n**Tags**: [a]\n",
        decisions_md="# Decisions\n\n### [2026-08-25] Fine\n\n**Tags**: [a]\n",
    )
    assert _warnings(target) == []


def test_undated_h2_context_headings_are_not_flagged(tmp_path: Path) -> None:
    """``## Learnings`` and similar structural headings are legitimate."""
    target = _living(
        tmp_path,
        learnings_md=(
            "# Learnings\n\n"
            "## Older entries\n\n"
            "### [2026-08-25] Fine\n\n"
            "#### Sub-detail\n\nbody\n"
        ),
    )
    assert _warnings(target) == []


def test_dated_headings_inside_fenced_code_blocks_are_ignored(
    tmp_path: Path,
) -> None:
    """A learning that documents the entry format must not flag itself."""
    target = _living(
        tmp_path,
        learnings_md=(
            "# Learnings\n\n"
            "### [2026-08-25] How to write an entry\n\n"
            "**Resolution**: use this shape:\n\n"
            "```markdown\n"
            "## [YYYY-MM-DD] Wrong level, shown as an example\n"
            "```\n"
        ),
    )
    assert _warnings(target) == []


def test_tilde_fenced_code_blocks_are_ignored(tmp_path: Path) -> None:
    target = _living(
        tmp_path,
        decisions_md=(
            "### [2026-08-25] Real entry\n\n"
            "~~~\n## [2026-01-01] Example inside a tilde fence\n~~~\n"
        ),
    )
    assert _warnings(target) == []


def test_a_different_fence_marker_does_not_close_an_open_fence(
    tmp_path: Path,
) -> None:
    """Only the marker that opened a fence closes it, so nesting stays inert."""
    target = _living(
        tmp_path,
        learnings_md=(
            "### [2026-08-25] Documenting the format\n\n"
            "```markdown\n"
            "~~~\n"
            "## [2026-01-01] Still inside the outer fence\n"
            "~~~\n"
            "```\n"
        ),
    )
    assert _warnings(target) == []


def test_entries_after_a_closed_fence_are_still_checked(tmp_path: Path) -> None:
    """Fence skipping must not swallow the rest of the file."""
    target = _living(
        tmp_path,
        learnings_md=(
            "### [2026-08-01] Documented\n\n"
            "```markdown\n## [2026-01-01] Example\n```\n\n"
            "## [2026-08-25] Real mislevelled entry\n"
        ),
    )
    (warning,) = _warnings(target)
    assert "line 7" in warning


def test_empty_and_missing_logs_are_not_flagged(tmp_path: Path) -> None:
    target = _living(tmp_path, learnings_md="")
    assert _warnings(target) == []


def test_absent_living_directory_is_not_flagged(tmp_path: Path) -> None:
    """This check owns heading levels only; a missing .living/ is another check."""
    assert _warnings(tmp_path) == []


def test_other_living_files_are_not_subject_to_the_check(tmp_path: Path) -> None:
    """conventions.md is parsed at ``##`` on purpose, so it must stay silent."""
    target = _living(
        tmp_path,
        conventions_md="# Conventions\n\n## [2026-08-25] A convention\n\nbody\n",
    )
    assert _warnings(target) == []
