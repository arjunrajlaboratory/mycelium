#!/usr/bin/env python3
"""Atomically upsert a single row into a mycelium LOG_REGISTRY.md.

Usage:
    python3 upsert_registry_row.py [--preserve-authored] [--log-link <log>] \
        <registry_path> <session_id> <new_row>

Behavior:
    - Validates <new_row> has exactly 12 '|' characters (11 columns).
    - Finds the first data row whose session-id column equals <session_id>
      exactly (no prefix matches), and replaces it.
    - With --log-link, a row whose Log cell links that exact log basename is
      the same session even under another session ID (an agent that guessed
      one). Every such row folds into the one replacement row: the first
      match is replaced and the rest are dropped.
    - If no match, appends <new_row> at end of file.
    - Writes atomically via tempfile + os.replace.
    - Prints 'upserted' or 'appended' to stdout.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from mycelium_locks import LockError, atomic_write_text, durable_path_lock


def _row_cells(line: str) -> list[str] | None:
    parts = line.rstrip("\n").split("|")
    if len(parts) != 13 or parts[0].strip() or parts[-1].strip():
        return None
    return [part.strip() for part in parts[1:-1]]


def _render_cells(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |\n"


_AUTHORED_CELLS = (6, 7, 9)
_LOG_CELL = 10
_MARKDOWN_LINK_RX = re.compile(r"^\[[^\]]*\]\(([^)]*)\)$")


def _preserve_authored_cells(existing: list[str], replacement: str) -> str:
    """Keep the first nonempty authored cell, in ``existing`` order."""
    new_cells = _row_cells(replacement)
    if new_cells is None:
        return replacement
    # Summary, Key Outputs, and Tags are authored semantic metadata. Stop owns
    # the factual lifecycle columns and must not overwrite richer agent text.
    for index in _AUTHORED_CELLS:
        for line in existing:
            old_cells = _row_cells(line)
            if old_cells is not None and old_cells[index]:
                new_cells[index] = old_cells[index]
                break
    return _render_cells(new_cells)


def _row_log_basename(line: str) -> str | None:
    """Basename of the log a row's Log cell links (`[log](x.md)` or `x.md`)."""
    cells = _row_cells(line)
    if cells is None:
        return None
    target = cells[_LOG_CELL]
    match = _MARKDOWN_LINK_RX.match(target)
    if match:
        target = match.group(1).strip()
    return target.rsplit("/", 1)[-1] or None


def _row_session_id(line: str) -> str:
    """Extract the session-id column from a registry row.

    Registry rows look like: '| date | session_id | project | ... |'
    After split('|'): ['', ' date ', ' session_id ', ' project ', ..., '']
    Index 2 is the session_id column.
    """
    parts = line.split("|")
    if len(parts) < 3:
        return ""
    return parts[2].strip()


def _is_header_or_separator(sid: str) -> bool:
    """Header-name row has 'Session ID' (text); separator row has dashes only."""
    if not sid:
        return True
    if set(sid) <= {"-", ":", " "}:
        return True
    # The literal header label
    if sid.lower() in ("session id", "session_id"):
        return True
    return False


_USAGE = (
    "usage: upsert_registry_row.py [--preserve-authored] [--log-link <log>] "
    "<registry_path> <session_id> <new_row>"
)


def main(argv: list[str]) -> int:
    preserve_authored = False
    log_link: str | None = None
    args = argv[1:]
    while args and args[0].startswith("--"):
        if args[0] == "--preserve-authored":
            preserve_authored = True
            args = args[1:]
        elif args[0] == "--log-link" and len(args) > 1:
            log_link = args[1]
            args = args[2:]
        else:
            print(_USAGE, file=sys.stderr)
            return 1
    positional = args
    if len(positional) != 3:
        print(_USAGE, file=sys.stderr)
        return 1
    if log_link is not None and (
        not log_link or "/" in log_link or log_link in {".", ".."}
    ):
        print("error: --log-link must be a plain log file name", file=sys.stderr)
        return 1

    registry_path_raw, session_id, new_row = positional
    registry_path = Path(os.path.abspath(registry_path_raw))

    pipe_count = new_row.count("|")
    if pipe_count != 12:
        print(
            f"error: new_row must have exactly 12 '|' chars (got {pipe_count})",
            file=sys.stderr,
        )
        return 1

    if not new_row.endswith("\n"):
        new_row = new_row + "\n"

    try:
        with durable_path_lock(registry_path):
            if not registry_path.exists():
                print(f"error: registry not found: {registry_path}", file=sys.stderr)
                return 1
            lines = registry_path.read_text(encoding="utf-8").splitlines(keepends=True)

            exact = [
                i
                for i, line in enumerate(lines)
                if _row_session_id(line) == session_id
                and not _is_header_or_separator(session_id)
            ][:1]
            # Only rows keyed by a different (guessed) ID fold in by log link.
            # A later row under the canonical ID is another session's record
            # (for example merged from another branch) and is left alone.
            strays = []
            if log_link is not None:
                strays = [
                    i
                    for i, line in enumerate(lines)
                    if not _is_header_or_separator(_row_session_id(line))
                    and _row_session_id(line) != session_id
                    and _row_log_basename(line) == log_link
                ]
            # The canonical row's authored cells take precedence over strays.
            match_indices = exact + strays
            replaced = bool(match_indices)
            out_lines: list[str] = []
            first = min(match_indices) if match_indices else -1
            for index, line in enumerate(lines):
                if index == first:
                    out_lines.append(
                        _preserve_authored_cells(
                            [lines[i] for i in match_indices], new_row
                        )
                        if preserve_authored
                        else new_row
                    )
                elif index in match_indices:
                    continue
                else:
                    out_lines.append(line)

            if not replaced:
                if out_lines and not out_lines[-1].endswith("\n"):
                    out_lines[-1] = out_lines[-1] + "\n"
                out_lines.append(new_row)

            atomic_write_text(registry_path, "".join(out_lines))
    except (LockError, OSError) as exc:
        print(f"error: registry update failed: {exc}", file=sys.stderr)
        return 1

    print("upserted" if replaced else "appended")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
