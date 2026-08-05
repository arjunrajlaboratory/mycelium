#!/usr/bin/env python3
"""Atomically upsert a single row into a mycelium LOG_REGISTRY.md.

Usage:
    python3 upsert_registry_row.py [--preserve-authored] \
        <registry_path> <session_id> <new_row>

Behavior:
    - Validates <new_row> has exactly 12 '|' characters (11 columns).
    - Finds the first data row whose session-id column equals <session_id>
      exactly (no prefix matches), and replaces it.
    - If no match, appends <new_row> at end of file.
    - Writes atomically via tempfile + os.replace.
    - Prints 'upserted' or 'appended' to stdout.
"""

from __future__ import annotations

import os
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


def _preserve_authored_cells(existing: str, replacement: str) -> str:
    old_cells = _row_cells(existing)
    new_cells = _row_cells(replacement)
    if old_cells is None or new_cells is None:
        return replacement
    # Summary, Key Outputs, and Tags are authored semantic metadata. Stop owns
    # the factual lifecycle columns and must not overwrite richer agent text.
    for index in (6, 7, 9):
        if old_cells[index]:
            new_cells[index] = old_cells[index]
    return _render_cells(new_cells)


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


def main(argv: list[str]) -> int:
    preserve_authored = len(argv) > 1 and argv[1] == "--preserve-authored"
    positional = argv[2:] if preserve_authored else argv[1:]
    if len(positional) != 3:
        print(
            "usage: upsert_registry_row.py [--preserve-authored] "
            "<registry_path> <session_id> <new_row>",
            file=sys.stderr,
        )
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

            replaced = False
            out_lines: list[str] = []
            for line in lines:
                sid = _row_session_id(line)
                if (
                    not replaced
                    and sid == session_id
                    and not _is_header_or_separator(sid)
                ):
                    out_lines.append(
                        _preserve_authored_cells(line, new_row)
                        if preserve_authored
                        else new_row
                    )
                    replaced = True
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
