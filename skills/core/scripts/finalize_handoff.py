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


def clean_handoff_body(content: str) -> str:
    content = _STATUS_BLOCK.sub("", content)
    kept = [line for line in content.splitlines() if not _STALE_STOP_LINE.match(line)]
    return "\n".join(kept).strip("\n")


def handoff_has_complete_sections(content: str) -> bool:
    lines = content.splitlines()
    for headings in HANDOFF_SCHEMAS:
        positions: list[int] = []
        for heading in headings:
            matches = [index for index, line in enumerate(lines) if line == heading]
            if len(matches) != 1:
                break
            positions.append(matches[0])
        else:
            if positions != sorted(positions):
                continue
            for index, start in enumerate(positions):
                end = positions[index + 1] if index + 1 < len(positions) else len(lines)
                if not any(line.strip() for line in lines[start + 1 : end]):
                    break
            else:
                return True
    return False


def handoff_is_complete_after_cleanup(content: str) -> bool:
    return handoff_has_complete_sections(clean_handoff_body(content))


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
    args = parser.parse_args()
    try:
        content, mode = read_regular(args.handoff)
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
