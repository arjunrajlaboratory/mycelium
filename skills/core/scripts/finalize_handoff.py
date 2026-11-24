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


_FENCE = re.compile(r"^(`{3,}|~{3,})")


def _outside_fences(lines: list[str]) -> list[bool]:
    """Mark the lines that are not inside a fenced code block.

    A fenced example of a handoff is text, not structure. Deliberately
    minimal: a fence opens only at column 0, where a section heading must
    start, and only when a matching line (at least as many of the same
    character, nothing after) closes it later. An unclosed run is ordinary
    text, so it can never swallow the sections that follow it.
    """
    outside = [True] * len(lines)
    index = 0
    while index < len(lines):
        opened = _FENCE.match(lines[index])
        if opened and not (
            opened.group(1)[0] == "`" and "`" in lines[index][opened.end():]
        ):
            marker = opened.group(1)
            for close in range(index + 1, len(lines)):
                closing = _FENCE.match(lines[close])
                if (
                    closing
                    and closing.group(1)[0] == marker[0]
                    and len(closing.group(1)) >= len(marker)
                    and not lines[close][closing.end():].strip()
                ):
                    for inside in range(index, close + 1):
                        outside[inside] = False
                    index = close
                    break
        index += 1
    return outside


def _is_body(line: str, outside: bool) -> bool:
    # A subheading is structure; inside a fence every nonblank line is text.
    return bool(line.strip()) and (not outside or not _MARKDOWN_HEADING.match(line))


def clean_handoff_body(content: str) -> str:
    # Editors may save a byte-order mark; it is not part of the first heading.
    content = _STATUS_BLOCK.sub("", content.removeprefix("\ufeff"))
    kept = [line for line in content.splitlines() if not _STALE_STOP_LINE.match(line)]
    return "\n".join(kept).strip("\n")


def _section_starts(lines: list[str], *, tolerant: bool) -> dict[str, list[int]]:
    """Where each schema heading occurs.

    The strict reading is the original contract: whole-line equality,
    anywhere. The tolerant reading forgives trailing whitespace on a heading
    and ignores lines inside fenced examples. A handoff passing either is
    complete, so tolerance can only ever accept more than the strict reading.
    """
    outside = _outside_fences(lines) if tolerant else [True] * len(lines)
    found: dict[str, list[int]] = {}
    for headings in HANDOFF_SCHEMAS:
        for heading in headings:
            found[heading] = [
                index
                for index, line in enumerate(lines)
                if outside[index]
                and (line.rstrip() == heading if tolerant else line == heading)
            ]
    return found


def _complete_under(lines: list[str], *, tolerant: bool) -> bool:
    starts = _section_starts(lines, tolerant=tolerant)
    outside = _outside_fences(lines) if tolerant else [True] * len(lines)
    for headings in HANDOFF_SCHEMAS:
        positions: list[int] = []
        for heading in headings:
            if len(starts[heading]) != 1:
                break
            positions.append(starts[heading][0])
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


def handoff_has_complete_sections(content: str) -> bool:
    lines = content.splitlines()
    return _complete_under(lines, tolerant=False) or _complete_under(lines, tolerant=True)


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
    found = _section_starts(lines, tolerant=True)
    positions = {heading: found[heading] for heading in headings}
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


# Machine lines in the status block. The owner line says which host session
# and transaction published this handoff, and whether the agent wrote it or
# Stop generated the fallback; a later transaction of the same host session
# that has no handoff of its own keeps an authored one (pattern 53).
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")
_OWNER_LINE = re.compile(
    r"^Handoff owner: (?P<owner>[A-Za-z0-9._:-]+); transaction "
    r"(?P<transaction>[A-Za-z0-9._-]+); source (?P<source>authored|fallback)\.$",
    re.MULTILINE,
)
_BLOCKERS = HANDOFF_SCHEMAS[0][2]
GAP_BULLET = "- Lineage gaps ({transaction}): {gap}"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def published_owner(content: str) -> dict[str, str] | None:
    """The owner line of the handoff's status block, if it has exactly one."""
    block = _STATUS_BLOCK.search(content.removeprefix("\ufeff"))
    if block is None:
        return None
    found = list(_OWNER_LINE.finditer(block.group(0)))
    return found[0].groupdict() if len(found) == 1 else None


def keeps_for(content: str, owner: str) -> bool:
    """Whether a published handoff is one a later transaction of ``owner``
    with no handoff of its own keeps: the agent wrote it, Stop accepted it for
    the same host session, and it is still complete."""
    found = published_owner(content)
    return (
        found is not None
        and found["owner"] == owner
        and found["source"] == "authored"
        and handoff_is_complete_after_cleanup(content)
    )


def _with_gap_bullet(body: str, transaction: str, gap: str) -> str | None:
    """``body`` with the gap as the last bullet of its single canonical
    `## Blockers & surprises` section, replacing an earlier bullet for the
    same transaction (a retry). None when that section is not unambiguous."""
    lines = body.split("\n")
    outside = _outside_fences(lines)
    starts = _section_starts(lines, tolerant=True)
    if len(starts[_BLOCKERS]) != 1:
        return None
    start = starts[_BLOCKERS][0]
    later = sorted(
        index for heading in HANDOFF_SCHEMAS[0] for index in starts[heading] if index > start
    )
    end = later[0] if later else len(lines)
    prefix = GAP_BULLET.format(transaction=transaction, gap="")
    kept = [
        index for index in range(start + 1, end)
        if not (outside[index] and lines[index].startswith(prefix))
    ]
    # The bullet belongs to the section's own body, before any subheading
    # (`### Dead ends — do not redo`), which would otherwise claim it.
    sub = next((position for position, index in enumerate(kept)
                if outside[index] and _MARKDOWN_HEADING.match(lines[index])), len(kept))
    head = [lines[index] for index in kept[:sub]]
    tail = [lines[index] for index in kept[sub:]]
    while head and not head[-1].strip():
        head.pop()
    head.append(GAP_BULLET.format(transaction=transaction, gap=gap))
    if tail or later:
        head.append("")
    return "\n".join(lines[: start + 1] + head + tail + lines[end:])


def finalize(
    content: str,
    accepted_at: str,
    *,
    owner: str = "",
    transaction: str = "",
    authored: bool = True,
    lineage_gap: str = "",
    follow_up: str = "",
) -> str:
    previous = published_owner(content) if follow_up else None
    body = clean_handoff_body(content)
    if not handoff_has_complete_sections(body):
        raise ValueError("handoff is incomplete after lifecycle cleanup")
    lines = [f"Lifecycle status: accepted by Stop at {accepted_at}."]
    gap = _one_line(lineage_gap)
    if gap:
        if not transaction:
            raise ValueError("a lineage gap needs the transaction that found it")
        with_gap = _with_gap_bullet(body, transaction, gap)
        if with_gap is not None and handoff_has_complete_sections(with_gap):
            body = with_gap
        else:
            lines.append(f"Lineage gaps ({transaction}): {gap}")
    if owner:
        source = "authored" if authored else "fallback"
        owner_transaction = transaction
        if previous is not None:
            # A kept handoff stays the earlier transaction's authored one.
            owner_transaction, source = previous["transaction"], previous["source"]
        if owner_transaction:
            lines.append(
                f"Handoff owner: {owner}; transaction {owner_transaction}; source {source}."
            )
    if _one_line(follow_up):
        lines.append(f"Follow-up activity: {_one_line(follow_up)}")
    status_block = f"{STATUS_BEGIN}\n" + "\n".join(lines) + f"\n{STATUS_END}"
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
    parser.add_argument("--owner", default="", help="HOST:SESSION that accepted it")
    parser.add_argument("--transaction", default="", help="the accepting transaction")
    parser.add_argument("--fallback", action="store_true",
                        help="the handoff is Stop's generated fallback")
    parser.add_argument("--lineage-gap", default="")
    parser.add_argument("--follow-up", default="",
                        help="one line on a later transaction that kept this handoff")
    parser.add_argument(
        "--keeps-for",
        metavar="OWNER",
        help="exit 0 if this published handoff is kept by OWNER's next transaction",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="exit 0 if complete, 3 with a one-line diagnosis if not, 1 on error",
    )
    args = parser.parse_args()
    for token in (args.owner, args.transaction, args.keeps_for or ""):
        if token and not _SAFE_TOKEN.match(token):
            return 1
    try:
        content, mode = read_regular(args.handoff)
        if args.keeps_for is not None:
            return 0 if keeps_for(content, args.keeps_for) else 1
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

        finalized = finalize(
            content,
            args.accepted_at,
            owner=args.owner,
            transaction=args.transaction,
            authored=not args.fallback,
            lineage_gap=args.lineage_gap,
            follow_up=args.follow_up,
        )
        if publish_destination is not None and publish_mode is not None:
            atomic_write(publish_destination, finalized, publish_mode)
        else:
            atomic_write(args.handoff, finalized, mode)
    except (OSError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
