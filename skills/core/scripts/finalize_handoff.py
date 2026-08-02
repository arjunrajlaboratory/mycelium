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


def finalize(content: str, accepted_at: str) -> str:
    content = _STATUS_BLOCK.sub("", content)
    kept = [line for line in content.splitlines() if not _STALE_STOP_LINE.match(line)]
    body = "\n".join(kept).strip("\n")
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
        prefix=".last-session.", suffix=".tmp", dir=path.parent
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--accepted-at", required=True)
    args = parser.parse_args()
    try:
        content, mode = read_regular(args.handoff)
    except (OSError, ValueError):
        return 1
    atomic_write(args.handoff, finalize(content, args.accepted_at), mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
