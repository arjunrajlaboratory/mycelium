#!/usr/bin/env python3
"""Atomically publish authoritative Stop acceptance in a session handoff."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path

STATUS_BEGIN = "<!-- BEGIN MYCELIUM LIFECYCLE STATUS -->"
STATUS_END = "<!-- END MYCELIUM LIFECYCLE STATUS -->"
HANDOFF_SCHEMAS = (
    (
        "## What was worked on",
        "## Key decisions made",
        "## Blockers & surprises",
        "## Current state",
        "## Next steps",
    ),
    (
        "## Current State",
        "## What Was Done",
        "## Key Decisions",
        "## Next Steps",
        "## Relevant Files",
    ),
)
_STATUS_BLOCK = re.compile(
    rf"{re.escape(STATUS_BEGIN)}.*?{re.escape(STATUS_END)}\n*", re.DOTALL
)
_STALE_STOP_LINE = re.compile(
    r"^.*(?:"
    r"(?:\bnatural\s+stop\b|\bstop\s+(?:hook|finalization|attempt)\b)"
    r".*\b(?:pending|not yet|remains?|attempt)\b|"
    r"\battempt(?:ing)?\b.*\b(?:natural\s+)?stop\b"
    r").*$",
    re.IGNORECASE,
)
# A subheading is structure, not content: a section holding only subheadings is
# as empty as a section with no body at all.
_MARKDOWN_HEADING = re.compile(r"^ {0,3}#{1,6}(?:\s|$)")


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def _outside_fences(lines: list[str]) -> list[bool]:
    """Mark the lines that are not inside a fenced code block.

    A fenced example of a handoff is text, not structure. As in CommonMark, a
    fence closes on a line of at least as many of the same character with
    nothing after it, and an unclosed fence runs to the end of the document.
    """
    outside: list[bool] = []
    fence = ""
    for line in lines:
        match = _FENCE.match(line)
        if not fence:
            outside.append(not match)
            if match and not (match.group(1)[0] == "`" and "`" in line[match.end():]):
                fence = match.group(1)
        else:
            outside.append(False)
            if (
                match
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= len(fence)
                and not line[match.end():].strip()
            ):
                fence = ""
    return outside


def _is_heading(line: str, heading: str) -> bool:
    """Trailing whitespace on a heading line is invisible; it still counts."""
    return line.rstrip() == heading


def _heading_positions(lines: list[str], heading: str) -> list[int]:
    outside = _outside_fences(lines)
    return [
        index
        for index, line in enumerate(lines)
        if outside[index] and _is_heading(line, heading)
    ]


def _is_body(line: str, outside: bool) -> bool:
    # A subheading is structure; inside a fence every nonblank line is text.
    return bool(line.strip()) and (not outside or not _MARKDOWN_HEADING.match(line))


def clean_handoff_body(content: str) -> str:
    # Editors may save a byte-order mark; it is not part of the first heading.
    content = _STATUS_BLOCK.sub("", content.removeprefix("\ufeff"))
    kept = [line for line in content.splitlines() if not _STALE_STOP_LINE.match(line)]
    return "\n".join(kept).strip("\n")


def handoff_has_complete_sections(content: str) -> bool:
    lines = content.splitlines()
    outside = _outside_fences(lines)
    for headings in HANDOFF_SCHEMAS:
        positions: list[int] = []
        for heading in headings:
            matches = _heading_positions(lines, heading)
            if len(matches) != 1:
                break
            positions.append(matches[0])
        else:
            if positions != sorted(positions):
                continue
            for index, start in enumerate(positions):
                end = positions[index + 1] if index + 1 < len(positions) else len(lines)
                if not any(
                    _is_body(lines[line], outside[line]) for line in range(start + 1, end)
                ):
                    break
            else:
                return True
    return False


def handoff_is_complete_after_cleanup(content: str) -> bool:
    return handoff_has_complete_sections(clean_handoff_body(content))


def _quoted(headings: list[str] | tuple[str, ...]) -> str:
    return ", ".join(f"`{heading}`" for heading in headings)


def describe_handoff_problems(content: str) -> list[str]:
    """Say why a handoff fails, measured against the canonical schema.

    Returns an empty list for a handoff Stop would preserve. Each problem is a
    short single-line phrase an agent can act on without reading this file.
    """
    if handoff_is_complete_after_cleanup(content):
        return []
    headings = HANDOFF_SCHEMAS[0]
    without_status = _STATUS_BLOCK.sub("", content)
    stripped = [
        line.strip()
        for line in without_status.splitlines()
        if _STALE_STOP_LINE.match(line)
    ]
    lines = clean_handoff_body(content).splitlines()
    outside = _outside_fences(lines)
    positions = {
        heading: _heading_positions(lines, heading)
        for heading in headings
    }
    problems: list[str] = []
    missing = [heading for heading in headings if not positions[heading]]
    if missing:
        problems.append(f"missing {_quoted(missing)}")
    for heading in headings:
        if len(positions[heading]) > 1:
            problems.append(f"`{heading}` appears {len(positions[heading])} times")
    single = [heading for heading in headings if len(positions[heading]) == 1]
    found_order = sorted(single, key=lambda heading: positions[heading][0])
    if found_order != single:
        problems.append(f"headings out of order ({_quoted(found_order)})")
    # Every occurrence ends the section before it, so a duplicate heading's
    # body is never credited to the empty section above it.
    starts = sorted(position for found in positions.values() for position in found)
    for heading in single:
        start = positions[heading][0]
        later = [position for position in starts if position > start]
        end = later[0] if later else len(lines)
        if not any(
            _is_body(lines[line], outside[line]) for line in range(start + 1, end)
        ):
            problems.append(f"`{heading}` has no prose or bullet")
    if stripped:
        sample = stripped[0][:120]
        problems.append(
            f"{len(stripped)} Stop-status line(s) are removed before the check "
            f'(e.g. "{sample}")'
        )
    if not problems:
        problems.append("its sections do not match a supported heading schema")
    return problems


def finalize(content: str, accepted_at: str) -> str:
    body = clean_handoff_body(content)
    if not handoff_has_complete_sections(body):
        raise ValueError("handoff is incomplete after lifecycle cleanup")
    status_block = (
        f"{STATUS_BEGIN}\n"
        f"Lifecycle status: accepted by Stop at {accepted_at}.\n"
        f"{STATUS_END}"
    )
    return f"{status_block}\n\n{body}\n"


def read_regular(path: Path) -> tuple[str, int]:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("handoff must be a regular file, not a symlink")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("handoff changed while finalization began")
        return handle.read(), stat.S_IMODE(metadata.st_mode)


def atomic_write(path: Path, content: str, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def preflight_publication(destination: Path, default_mode: int) -> int:
    """Validate the shared destination before either handoff is rewritten."""
    parent_metadata = destination.parent.lstat()
    if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(
        parent_metadata.st_mode
    ):
        raise ValueError("publication directory must be a real directory")
    try:
        destination_metadata = destination.lstat()
    except FileNotFoundError:
        return default_mode
    else:
        if not stat.S_ISREG(destination_metadata.st_mode):
            raise ValueError("published handoff must be a regular file")
        return stat.S_IMODE(destination_metadata.st_mode)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--accepted-at")
    parser.add_argument("--publish-to", type=Path)
    parser.add_argument("--check-complete", action="store_true")
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="exit 0 if complete, 3 with a one-line diagnosis if not, 1 on error",
    )
    args = parser.parse_args()
    try:
        content, mode = read_regular(args.handoff)
        if args.diagnose:
            if (
                args.check_complete
                or args.accepted_at is not None
                or args.publish_to is not None
            ):
                return 1
            problems = describe_handoff_problems(content)
            if not problems:
                return 0
            print("; ".join(problems).replace("\n", " "))
            return 3
        if args.check_complete:
            if args.accepted_at is not None or args.publish_to is not None:
                return 1
            return 0 if handoff_is_complete_after_cleanup(content) else 1
        if args.accepted_at is None:
            return 1
        publish_destination: Path | None = None
        publish_mode: int | None = None
        if (
            args.publish_to is not None
            and os.path.abspath(args.publish_to) != os.path.abspath(args.handoff)
        ):
            publish_destination = args.publish_to
            publish_mode = preflight_publication(publish_destination, mode)

        finalized = finalize(content, args.accepted_at)
        if publish_destination is not None and publish_mode is not None:
            atomic_write(publish_destination, finalized, publish_mode)
        else:
            atomic_write(args.handoff, finalized, mode)
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
