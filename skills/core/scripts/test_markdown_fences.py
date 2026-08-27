"""Tests for the shared CommonMark 4.5 fence tracker.

This module exists because `validate_structure.py` and
`finalize_session_log.py` each carried their own copy and drifted. Every case
below is a defect one of those copies actually had.
"""

import pytest

import markdown_fences as fences


# ---------------------------------------------------------------------------
# visual_column — tabs expand to Markdown tab stops
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "prefix,expected",
    [
        ("", 0),
        (" ", 1),
        ("   ", 3),
        ("\t", 4),
        (" \t", 4),
        ("   \t", 4),
        ("\t\t", 8),
        ("\t ", 5),
        ("> ", 2),
    ],
)
def test_visual_column_expands_tabs(prefix: str, expected: int) -> None:
    assert fences.visual_column(prefix) == expected


# ---------------------------------------------------------------------------
# opening_fence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line,marker,column,depth",
    [
        ("```", "```", 0, 0),
        ("````", "````", 0, 0),
        ("~~~", "~~~", 0, 0),
        ("```markdown", "```", 0, 0),
        ("   ```", "```", 3, 0),
        ("- ```markdown", "```", 2, 0),
        ("* ```", "```", 2, 0),
        ("+ ```md", "```", 2, 0),
        ("1. ```md", "```", 3, 0),
        ("10) ```md", "```", 4, 0),
        ("> ```markdown", "```", 2, 1),
        (">```", "```", 1, 1),
        ("> - ```md", "```", 4, 1),
    ],
)
def test_opening_fence_recognizes_containers(
    line: str, marker: str, column: int, depth: int
) -> None:
    assert fences.opening_fence(line) == fences.Fence(marker, column, depth)


@pytest.mark.parametrize(
    "line",
    [
        "not a fence",
        "## [2026-01-01] a heading",
        "``",
        "~~",
        "    ```",  # four spaces is an indented code block
        "\t```",  # a tab reaches column 4, likewise
        "``` with `backtick` in info",  # CommonMark 4.5
        "",
    ],
)
def test_opening_fence_rejects_non_openers(line: str) -> None:
    assert fences.opening_fence(line) is None


# ---------------------------------------------------------------------------
# closes
# ---------------------------------------------------------------------------


def test_closer_must_be_at_least_as_long_as_the_opener() -> None:
    opener = fences.Fence("````", 0, 0)
    assert not fences.closes("```", opener)
    assert fences.closes("````", opener)
    assert fences.closes("`````", opener)


def test_closer_must_use_the_opener_character() -> None:
    assert not fences.closes("~~~", fences.Fence("```", 0, 0))
    assert not fences.closes("```", fences.Fence("~~~", 0, 0))


def test_closer_may_not_carry_an_info_string() -> None:
    opener = fences.Fence("```", 0, 0)
    assert not fences.closes("```python", opener)
    assert fences.closes("```   ", opener)


@pytest.mark.parametrize("indent,closes", [(0, True), (3, True), (4, False)])
def test_closer_indentation_is_bounded_by_the_opener(
    indent: int, closes: bool
) -> None:
    assert (
        fences.closes(" " * indent + "```", fences.Fence("```", 0, 0)) is closes
    )


def test_bound_is_relative_to_the_openers_column() -> None:
    """A list item's fence opens at column 2, so its closer may reach 5."""
    opener = fences.Fence("```", 2, 0)
    assert fences.closes("  ```", opener)
    assert fences.closes("     ```", opener)
    assert not fences.closes("      ```", opener)


def test_tab_indented_closer_is_measured_in_columns() -> None:
    """A tab reaches column 4, so it cannot close a top-level fence."""
    assert not fences.closes("\t```", fences.Fence("```", 0, 0))


def test_closer_must_match_the_openers_blockquote_depth() -> None:
    top_level = fences.Fence("```", 0, 0)
    quoted = fences.Fence("```", 2, 1)
    assert not fences.closes("> ```", top_level)
    assert not fences.closes("```", quoted)
    assert fences.closes("> ```", quoted)
    assert fences.closes(">```", quoted)


def test_a_list_marker_never_closes_a_fence() -> None:
    """A list marker begins a new block rather than continuing this one."""
    assert not fences.closes("- ```", fences.Fence("```", 2, 0))


# ---------------------------------------------------------------------------
# unclosed_fence_line
# ---------------------------------------------------------------------------


def test_balanced_fences_report_nothing() -> None:
    assert fences.unclosed_fence_line(["```", "body", "```", "after"]) is None


def test_unclosed_fence_reports_its_opening_line() -> None:
    assert fences.unclosed_fence_line(["text", "```md", "body"]) == 2


def test_inner_shorter_run_does_not_close_a_longer_fence() -> None:
    assert fences.unclosed_fence_line(["````", "```", "````"]) is None
    assert fences.unclosed_fence_line(["````", "```"]) == 1


def test_reopened_fence_after_a_clean_close_is_tracked() -> None:
    assert fences.unclosed_fence_line(["```", "```", "```"]) == 3


def test_container_fence_round_trips() -> None:
    assert fences.unclosed_fence_line(["- ```md", "  body", "  ```"]) is None
    assert fences.unclosed_fence_line(["> ```md", "> body", "> ```"]) is None


def test_no_lines_reports_nothing() -> None:
    assert fences.unclosed_fence_line([]) is None
