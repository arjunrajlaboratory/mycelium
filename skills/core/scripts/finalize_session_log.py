#!/usr/bin/env python3
"""Atomically publish a completed Mycelium session log.

The Stop hook must not expose frontmatter that says a session ended before the
matching footer is durable.  This helper builds the complete replacement in
memory and publishes it with one same-directory ``os.replace`` operation.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


_END_FOOTER_RE = re.compile(r"^### .* — Session ended \(", re.MULTILINE)


def _replace_frontmatter_field(frontmatter: str, field: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(field)}:.*$", re.MULTILINE)
    updated, replacements = pattern.subn(f"{field}: {value}", frontmatter)
    if replacements != 1:
        raise ValueError(
            f"expected exactly one {field!r} field in session frontmatter; "
            f"found {replacements}"
        )
    return updated


def _completed_log(
    original: str,
    *,
    ended: str,
    duration_minutes: int,
    files_changed: int,
    end_time: str,
    changed_paths: list[str],
) -> str:
    if not original.startswith("---\n"):
        raise ValueError("session log is missing opening YAML frontmatter")
    closing = original.find("\n---\n", 4)
    if closing < 0:
        raise ValueError("session log is missing closing YAML frontmatter")

    frontmatter_end = closing + len("\n---\n")
    frontmatter = original[:frontmatter_end]
    body = original[frontmatter_end:]
    frontmatter = _replace_frontmatter_field(frontmatter, "ended", ended)
    frontmatter = _replace_frontmatter_field(
        frontmatter, "duration_minutes", str(duration_minutes)
    )
    frontmatter = _replace_frontmatter_field(
        frontmatter, "files_changed", str(files_changed)
    )

    # A retry against a legacy partially finalized log repairs its frontmatter
    # without creating a second footer. New finalizations publish both pieces
    # together and therefore never expose that partial state.
    if _END_FOOTER_RE.search(body):
        return frontmatter + body

    footer = (
        f"\n### {end_time} — Session ended "
        f"({duration_minutes}m, {files_changed} files)\n"
    )
    if changed_paths:
        names = [Path(path).name for path in changed_paths[:3]]
        summary = ", ".join(names)
        if files_changed > 3:
            summary += f" (+{files_changed - 3} more)"
        file_list = "\n".join(f"- `{path}`" for path in changed_paths)
        footer += f"- Modified: {summary}\n\n### Files Modified\n{file_list}\n"

    return frontmatter + body + footer


def finalize_session_log(
    log_path: Path,
    *,
    ended: str,
    duration_minutes: int,
    files_changed: int,
    end_time: str,
    changed_paths: list[str],
) -> None:
    metadata = log_path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("session log must be a regular file, not a symlink")

    read_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    read_fd = os.open(log_path, read_flags)
    with os.fdopen(read_fd, "r", encoding="utf-8") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode) or (
            opened.st_dev,
            opened.st_ino,
        ) != (metadata.st_dev, metadata.st_ino):
            raise ValueError("session log changed while finalization began")
        original = handle.read()
    completed = _completed_log(
        original,
        ended=ended,
        duration_minutes=duration_minutes,
        files_changed=files_changed,
        end_time=end_time,
        changed_paths=changed_paths,
    )

    temporary_fd, temporary_name = tempfile.mkstemp(
        prefix=f".{log_path.name}.", suffix=".tmp", dir=log_path.parent
    )
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as handle:
            handle.write(completed)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, stat.S_IMODE(metadata.st_mode))
        os.replace(temporary_name, log_path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--ended", required=True)
    parser.add_argument("--duration-minutes", type=int, required=True)
    parser.add_argument("--files-changed", type=int, required=True)
    parser.add_argument("--end-time", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.duration_minutes < 0 or args.files_changed < 0:
        raise ValueError("duration and file count must be non-negative")
    changed_paths = [line for line in sys.stdin.read().splitlines() if line]
    finalize_session_log(
        args.log_path,
        ended=args.ended,
        duration_minutes=args.duration_minutes,
        files_changed=args.files_changed,
        end_time=args.end_time,
        changed_paths=changed_paths,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
