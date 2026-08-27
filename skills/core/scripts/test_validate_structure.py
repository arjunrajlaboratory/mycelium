"""Tests for mycelium repository structure validation.

Focused on ``check_entry_heading_levels``, which turns a previously silent
failure into a reported one: an append-only knowledge log whose entries use a
heading level no ``generate_index.py`` parser recognises is indexed as zero
entries with no error anywhere (issue #76).
"""

from pathlib import Path

import pytest

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


def _errors(target_dir: Path) -> list[str]:
    """Run the check and return its errors, asserting it emits no warnings.

    The check reports at error severity: a repo whose knowledge is missing from
    INDEX.md must fail validation, not merely mention it.
    """
    result = ValidationResult()
    check_entry_heading_levels(target_dir, result)
    assert result.warnings == [], f"unexpected warnings: {result.warnings}"
    return result.errors


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
    errors = _errors(target)
    assert len(errors) == 1
    assert "decisions.md" in errors[0]


def test_dated_h2_entry_in_learnings_is_flagged(tmp_path: Path) -> None:
    """learnings.md is subject to the same parser contract as decisions.md."""
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n## [2026-08-25] A learning\n\n**Category**: gotcha\n",
    )
    errors = _errors(target)
    assert len(errors) == 1
    assert "learnings.md" in errors[0]


def test_error_states_the_required_heading_level_and_line_numbers(
    tmp_path: Path,
) -> None:
    """The message has to be actionable without reading generate_index.py."""
    target = _living(
        tmp_path,
        decisions_md="# Decisions\n\n## [2026-08-25] Example decision\n\nbody\n",
    )
    (error,) = _errors(target)
    assert ENTRY_HEADING_PREFIX.strip() in error
    assert "line 3" in error


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
    (error,) = _errors(target)
    assert "learnings.md" in error
    assert "line 7" in error


def test_dated_h4_entry_is_flagged(tmp_path: Path) -> None:
    """Any level other than the parser's is dropped, not just ``##``."""
    target = _living(
        tmp_path,
        decisions_md="# Decisions\n\n#### [2026-08-25] Too deep\n\nbody\n",
    )
    assert len(_errors(target)) == 1


def test_bare_date_entry_without_brackets_is_flagged(tmp_path: Path) -> None:
    """Entries are also written without surrounding brackets."""
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n## 2026-08-25 Bare date entry\n\nbody\n",
    )
    assert len(_errors(target)) == 1


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
    assert len(_errors(target)) == 1


def test_both_logs_are_reported_independently(tmp_path: Path) -> None:
    target = _living(
        tmp_path,
        learnings_md="## [2026-08-25] One\n",
        decisions_md="## [2026-08-25] Two\n",
    )
    errors = _errors(target)
    assert len(errors) == 2
    assert {"learnings.md" in e for e in errors} == {True, False}


# ---------------------------------------------------------------------------
# Negative cases — correct or unrelated markdown must stay silent
# ---------------------------------------------------------------------------


def test_correctly_levelled_entries_are_not_flagged(tmp_path: Path) -> None:
    target = _living(
        tmp_path,
        learnings_md="# Learnings\n\n### [2026-08-25] Fine\n\n**Tags**: [a]\n",
        decisions_md="# Decisions\n\n### [2026-08-25] Fine\n\n**Tags**: [a]\n",
    )
    assert _errors(target) == []


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
    assert _errors(target) == []


@pytest.mark.parametrize(
    "heading",
    [
        "   ## [2026-08-01] Three spaces, h2",
        "  ### [2026-08-01] Two spaces, canonical level",
        " #### [2026-08-01] One space, h4",
    ],
)
def test_indented_entry_headings_are_flagged(tmp_path: Path, heading: str) -> None:
    """CommonMark allows up to three spaces before an ATX heading.

    The parsers match a column-1 prefix, so an indented entry — even at the
    canonical ``###`` level — is absent from INDEX.md. That is precisely the
    silent failure this check exists to surface, so it must be reported.
    """
    target = _living(
        tmp_path, learnings_md=f"# Learnings\n\n{heading}\n**Tags**: [t]\n"
    )
    path = tmp_path / ".living" / "learnings.md"
    count, _ = gi.count_headers_and_topics(path, "learnings")
    assert count == 0, "precondition: the parser cannot read this heading"
    assert len(_errors(target)) == 1


def test_four_space_indent_is_a_code_block_not_a_heading(tmp_path: Path) -> None:
    """Four spaces is an indented code block in CommonMark, so it is content."""
    target = _living(
        tmp_path,
        learnings_md=(
            "# Learnings\n\n"
            "### [2026-08-01] Real\n\n"
            "    ## [2026-01-01] Indented code, not an entry\n"
        ),
    )
    assert _errors(target) == []


@pytest.mark.parametrize(
    "heading",
    [
        "## Archive (entries before 2025-01-01)",
        "## Sprint 2026-04-01 retro",
        "## Migrated from old repo on 2026-01-15",
        "## Entries 2025-01-01 through 2025-12-31",
    ],
)
def test_structural_headings_merely_mentioning_a_date_are_not_flagged(
    tmp_path: Path, heading: str
) -> None:
    """An entry heading leads with its date; a prose heading merely contains one.

    Treating any date-bearing heading as an entry made real section headings a
    hard validation failure, and the migration then rewrote them into fake
    entries that shifted every real entry's ID.
    """
    target = _living(
        tmp_path,
        learnings_md=f"# Learnings\n\n{heading}\n\n### [2026-04-01] Real\n",
    )
    assert _errors(target) == []


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
    assert _errors(target) == []


def test_tilde_fenced_code_blocks_are_ignored(tmp_path: Path) -> None:
    target = _living(
        tmp_path,
        decisions_md=(
            "### [2026-08-25] Real entry\n\n"
            "~~~\n## [2026-01-01] Example inside a tilde fence\n~~~\n"
        ),
    )
    assert _errors(target) == []


def test_longer_fence_is_not_closed_by_a_shorter_inner_run(
    tmp_path: Path,
) -> None:
    """CommonMark 4.5: a closing fence must be at least as long as the opener.

    A four-backtick fence is the normal way to document a triple-backtick block.
    Treating the inner ``` as a close made the example heading inside it a hard
    validation failure, and the migrator then rewrote it.
    """
    target = _living(
        tmp_path,
        learnings_md=(
            "### [2026-08-25] Documenting a fenced example\n\n"
            "````markdown\n"
            "```\n"
            "## [2026-01-01] Example heading, not an entry\n"
            "```\n"
            "````\n"
        ),
    )
    assert _errors(target) == []


def test_shorter_opener_is_closed_by_a_longer_run(tmp_path: Path) -> None:
    """A longer run does close a shorter opener, so the file keeps being checked."""
    target = _living(
        tmp_path,
        learnings_md=(
            "### [2026-08-25] Real\n\n"
            "```\n## [2026-01-01] Example\n````\n\n"
            "## [2026-08-26] Genuinely mislevelled\n"
        ),
    )
    (error,) = _errors(target)
    assert "line 7" in error


def test_fence_line_carrying_an_info_string_does_not_close(
    tmp_path: Path,
) -> None:
    """Only a bare run closes a fence; ```` ```python ```` inside one is content."""
    target = _living(
        tmp_path,
        learnings_md=(
            "### [2026-08-25] Real\n\n"
            "````\n"
            "```python\n"
            "## [2026-01-01] Example\n"
            "```\n"
            "````\n"
        ),
    )
    assert _errors(target) == []


def test_fence_handling_agrees_with_the_session_log_implementation() -> None:
    """Two hand-rolled fence trackers in one repo caused this bug; pin them.

    `finalize_session_log.py` already implemented CommonMark 4.5 correctly. This
    asserts the validator's copy agrees rather than drifting again.
    """
    import finalize_session_log as fsl
    import validate_structure as vs_mod

    lines = [
        "```",
        "````",
        "~~~",
        "~~~~",
        "```markdown",
        "```python",
        "`````",
        "   ```",
        "not a fence",
        "## [2026-01-01] heading",
        "``` ",
        "```` info",
        "``` with `backtick` in info",
    ]
    for line in lines:
        assert vs_mod._fence_open_marker(line) == fsl._fence_open_marker(line), line
    for opener in ["```", "````", "~~~", "~~~~"]:
        for line in lines:
            assert vs_mod._fence_closes(line, opener) == fsl._fence_closes(
                line, opener
            ), (opener, line)


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
    assert _errors(target) == []


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
    (error,) = _errors(target)
    assert "line 7" in error


def test_empty_and_missing_logs_are_not_flagged(tmp_path: Path) -> None:
    target = _living(tmp_path, learnings_md="")
    assert _errors(target) == []


def test_absent_living_directory_is_not_flagged(tmp_path: Path) -> None:
    """This check owns heading levels only; a missing .living/ is another check."""
    assert _errors(tmp_path) == []


def test_other_living_files_are_not_subject_to_the_check(tmp_path: Path) -> None:
    """conventions.md is parsed at ``##`` on purpose, so it must stay silent."""
    target = _living(
        tmp_path,
        conventions_md="# Conventions\n\n## [2026-08-25] A convention\n\nbody\n",
    )
    assert _errors(target) == []
