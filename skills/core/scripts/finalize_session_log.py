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


# Only the machine-emitted footer syntax is machine-owned: an HH:MM time and
# numeric duration/file-count exactly as this module writes them. Authored
# headings that merely resemble the footer, and examples inside Markdown code
# fences, belong to the agent and must survive finalization untouched.
_END_FOOTER_HEADING_RE = re.compile(
    r"^### \d{2}:\d{2} — Session ended \(\d+m, (?P<files>\d+) files\)$"
)
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>```+|~~~+)(?P<info>.*)$")


def _fence_open_marker(line: str) -> str | None:
    """Return the opening fence marker of a line, or None (CommonMark 4.5).

    A backtick fence's info string may not contain backticks; such a line is
    ordinary content, not a fence.
    """
    match = _FENCE_RE.match(line)
    if match is None:
        return None
    marker = match.group("marker")
    if marker[0] == "`" and "`" in match.group("info"):
        return None
    return marker


def _fence_closes(line: str, open_marker: str) -> bool:
    """True when a line closes the active fence: same character, at least the
    opening length, and nothing but whitespace after (CommonMark 4.5)."""
    match = _FENCE_RE.match(line)
    if match is None:
        return False
    marker = match.group("marker")
    return (
        marker[0] == open_marker[0]
        and len(marker) >= len(open_marker)
        and not match.group("info").strip()
    )


def _strip_machine_footers(body: str) -> str:
    """Remove every machine-emitted end-footer block outside code fences."""
    lines = body.split("\n")
    kept: list[str] = []
    open_marker: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if open_marker is not None:
            if _fence_closes(line, open_marker):
                open_marker = None
            kept.append(line)
            index += 1
            continue
        candidate = _fence_open_marker(line)
        if candidate is not None:
            open_marker = candidate
            kept.append(line)
            index += 1
            continue
        heading = _END_FOOTER_HEADING_RE.match(line)
        if heading is None:
            kept.append(line)
            index += 1
            continue
        # Consume only what the generator emitted for this footer: the
        # summary and file list exist only when files were changed, and the
        # list holds exactly the recorded number of entries. Anything beyond
        # that shape is authored content and stays.
        generated_files = int(heading.group("files"))
        index += 1
        if generated_files > 0:
            if index < len(lines) and lines[index].startswith("- Modified:"):
                index += 1
            if (
                index + 1 < len(lines)
                and lines[index] == ""
                and lines[index + 1] == "### Files Modified"
            ):
                index += 2
                consumed = 0
                while (
                    index < len(lines)
                    and consumed < generated_files
                    and lines[index].startswith("- `")
                ):
                    index += 1
                    consumed += 1
    return "\n".join(kept)


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

    # A Stop retry recomputes duration and changed files, so an end footer
    # published by an earlier attempt must be rebuilt from the same canonical
    # values now going into the frontmatter and registry — never left stale.
    # Removing every existing block also collapses duplicated legacy footers.
    body = _strip_machine_footers(body)
    if body.strip():
        body = body.rstrip("\n") + "\n"
    else:
        body = ""

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
