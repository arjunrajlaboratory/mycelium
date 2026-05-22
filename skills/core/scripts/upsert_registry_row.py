#!/usr/bin/env python3
"""Atomically upsert a single row into a mycelium LOG_REGISTRY.md.

Usage:
    python3 upsert_registry_row.py <registry_path> <session_id> <new_row>

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
import tempfile


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
    if len(argv) != 4:
        print(
            "usage: upsert_registry_row.py <registry_path> <session_id> <new_row>",
            file=sys.stderr,
        )
        return 1

    registry_path, session_id, new_row = argv[1], argv[2], argv[3]

    pipe_count = new_row.count("|")
    if pipe_count != 12:
        print(
            f"error: new_row must have exactly 12 '|' chars (got {pipe_count})",
            file=sys.stderr,
        )
        return 1

    if not os.path.exists(registry_path):
        print(f"error: registry not found: {registry_path}", file=sys.stderr)
        return 1

    if not new_row.endswith("\n"):
        new_row = new_row + "\n"

    with open(registry_path, encoding="utf-8") as f:
        lines = f.readlines()

    replaced = False
    out_lines: list[str] = []
    for line in lines:
        sid = _row_session_id(line)
        if not replaced and sid == session_id and not _is_header_or_separator(sid):
            out_lines.append(new_row)
            replaced = True
        else:
            out_lines.append(line)

    if not replaced:
        if out_lines and not out_lines[-1].endswith("\n"):
            out_lines[-1] = out_lines[-1] + "\n"
        out_lines.append(new_row)

    target_dir = os.path.dirname(os.path.abspath(registry_path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".log_registry.", suffix=".tmp", dir=target_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.writelines(out_lines)
        os.replace(tmp_path, registry_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise

    print("upserted" if replaced else "appended")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
