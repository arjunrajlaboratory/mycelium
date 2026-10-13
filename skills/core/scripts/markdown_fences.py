#!/usr/bin/env python3
"""CommonMark 4.5 fenced-code-block tracking, shared across Mycelium scripts.

Several call sites need to know whether a line sits inside a fenced code block:

- `validate_structure.py`, which must not mistake a heading inside a documented
  example for a real knowledge entry,
- the entry parsers (`generate_index.py`, `detect_recurrence.py`,
  `recall_lessons.py`, `knowledge_map/extract_entries.py`,
  `knowledge_map/extract_logs.py`), which must not count or split on such a
  heading either (via `mark_fenced`), and
- `finalize_session_log.py`, which must not strip a machine-shaped footer that a
  human deliberately wrote inside a fence.

The first and last each grew their own copy of this logic and drifted apart,
which produced a run of defects: a four-backtick fence closed by its own inner
three-backtick line, a fence opened on a list marker's line going unrecognized
so its closer became a phantom opener, blockquoted fences that could never
close, and tab offsets measured in characters rather than columns. This module is the single
implementation; every call site delegates to it.

Container tracking is still not a full Markdown parser -- nested containers with
irregular indentation can defeat it. Callers must therefore treat a fence left
open at end of input as a reportable condition rather than a clean parse; see
`unclosed_fence_line`.
"""

from __future__ import annotations

import re
from typing import Iterable, Iterator, NamedTuple

# An opening fence may sit up to three spaces in, optionally behind a run of
# list-item or blockquote markers ("- ```markdown", "> ```", "1. ```").
_FENCE_OPEN_RE = re.compile(
    r"^(?P<prefix> {0,3}(?:(?:[-+*]|\d{1,9}[.)])[ \t]+|>[ \t]?)*)"
    r"(?P<marker>```+|~~~+)(?P<info>.*)$"
)

# A closing fence carries only whitespace and blockquote continuations before
# its marker: a list marker would begin a new block, not continue this one.
_FENCE_CLOSE_RE = re.compile(
    r"^(?P<prefix>[ \t>]*)(?P<marker>```+|~~~+)(?P<info>.*)$"
)

# How far past the opener's marker column a closer may still sit (CommonMark
# allows three spaces of indentation relative to the container).
CLOSE_SLACK = 3

# Markdown expands a tab to the next four-column stop, so offsets have to be
# measured in columns: a leading tab puts a marker at column 4 (fenced content),
# not column 1.
TAB_STOP = 4


class Fence(NamedTuple):
    """An open fenced code block."""

    marker: str
    """The run of backticks or tildes that opened it."""

    column: int
    """Visual column where the marker begins; bounds a matching closer."""

    quote_depth: int
    """Blockquote continuations in its prefix; a closer must match exactly."""


def visual_column(prefix: str) -> int:
    """Column at which `prefix` ends, with tabs expanded to Markdown tab stops."""
    column = 0
    for character in prefix:
        if character == "\t":
            column += TAB_STOP - (column % TAB_STOP)
        else:
            column += 1
    return column


def opening_fence(line: str) -> Fence | None:
    """Return the `Fence` this line opens, or None if it opens none.

    A backtick fence's info string may not contain backticks; such a line is
    ordinary content rather than a fence (CommonMark 4.5).
    """
    match = _FENCE_OPEN_RE.match(line)
    if match is None:
        return None
    marker = match.group("marker")
    if marker[0] == "`" and "`" in match.group("info"):
        return None
    prefix = match.group("prefix")
    return Fence(marker, visual_column(prefix), prefix.count(">"))


def closes(line: str, fence: Fence) -> bool:
    """True when `line` closes `fence`.

    Requires the same fence character, a run at least as long as the opener's,
    nothing but whitespace after the marker, the opener's blockquote depth, and a
    marker column no more than `CLOSE_SLACK` past the opener's. Anything further
    indented -- including via a tab, which reaches column 4 -- is fenced content.
    """
    match = _FENCE_CLOSE_RE.match(line)
    if match is None:
        return False
    marker = match.group("marker")
    prefix = match.group("prefix")
    return (
        marker[0] == fence.marker[0]
        and len(marker) >= len(fence.marker)
        and not match.group("info").strip()
        and prefix.count(">") == fence.quote_depth
        and visual_column(prefix) <= fence.column + CLOSE_SLACK
    )


def mark_fenced(lines: Iterable[str]) -> Iterator[tuple[str, bool]]:
    """Yield each line unchanged, paired with whether it belongs to a fence.

    Opening and closing fence lines count as fenced along with their content,
    so a caller looking for structure (headings, tag lines) only has to act on
    lines paired with False. Trailing line endings are ignored for matching.
    A fence left open marks the rest of the input as fenced; callers that need
    to surface that use `unclosed_fence_line`.
    """
    fence: Fence | None = None
    for line in lines:
        bare = line.rstrip("\r\n")
        if fence is not None:
            if closes(bare, fence):
                fence = None
            yield line, True
            continue
        fence = opening_fence(bare)
        yield line, fence is not None


def unclosed_fence_line(lines: Iterable[str]) -> int | None:
    """1-based line number of a fence left open at end of input, if any.

    Container tracking here is deliberately incomplete, so callers must surface
    this instead of treating a scan that ended inside a fence as clean: whatever
    followed the opener was never examined.
    """
    fence: Fence | None = None
    opened_at: int | None = None
    for lineno, line in enumerate(lines, start=1):
        if fence is not None:
            if closes(line, fence):
                fence, opened_at = None, None
            continue
        opened = opening_fence(line)
        if opened is not None:
            fence, opened_at = opened, lineno
    return opened_at
