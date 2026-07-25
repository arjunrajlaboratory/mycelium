#!/usr/bin/env python3
"""Lock-protected, atomic upsert of a row into a mycelium markdown-table registry.

For markdown-TABLE registries — FINDINGS_REGISTRY.md, LOG_REGISTRY.md,
TODO_REGISTRY.md — which are otherwise mutated by the agent's Edit tool, a
read-modify-write with no coordination, so two concurrent chats adding entries
clobber each other or produce merge conflicts. Routing those updates through
this helper serialises them with an flock and writes atomically.

NOT for the YAML-block manifests (ANALYSIS_MANIFEST.md, DATA_MANIFEST.md), which
are `### name` headers + fenced YAML blocks + prose, not tables — this helper
requires the new row to be a markdown table row (starts with '|').

Usage:
    python3 upsert_manifest_row.py <registry_path> <key> <new_row> [--key-col N]

Finds the first data row (after the table's separator line) whose column N
(default 1, the first table column) equals <key> exactly and replaces it;
otherwise appends <new_row>. Header and separator rows are never matched.
Prints 'upserted' or 'appended'.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mycelium_locks import atomic_write, file_lock  # noqa: E402


def _row_col(line: str, col: int) -> str:
    parts = line.split("|")
    if len(parts) <= col:
        return ""
    return parts[col].strip()


def _is_separator_row(line: str) -> bool:
    """A markdown table separator, e.g. ``|----|:--:|``."""
    s = line.strip()
    if not s.startswith("|"):
        return False
    cells = [c.strip() for c in s.strip("|").split("|")]
    return bool(cells) and all(c and set(c) <= {"-", ":"} for c in cells)


def main(argv: list[str]) -> int:
    # Parse a single optional --key-col N flag out of the argument list.
    key_col = 1
    rest: list[str] = []
    i = 1
    while i < len(argv):
        if argv[i] == "--key-col" and i + 1 < len(argv):
            key_col = int(argv[i + 1])
            i += 2
            continue
        rest.append(argv[i])
        i += 1
    args = rest

    if len(args) != 3:
        print(
            "usage: upsert_manifest_row.py <registry_path> <key> <new_row> [--key-col N]",
            file=sys.stderr,
        )
        return 1

    registry_path, key, new_row = args
    if not new_row.lstrip().startswith("|"):
        print("error: new_row must be a markdown table row (start with '|')", file=sys.stderr)
        return 1
    if not new_row.endswith("\n"):
        new_row += "\n"

    with file_lock(registry_path):
        if not os.path.exists(registry_path):
            print(f"error: registry not found: {registry_path}", file=sys.stderr)
            return 1
        with open(registry_path, encoding="utf-8") as f:
            lines = f.readlines()

        # Only match data rows: those after the table's separator line. This
        # keeps a key that happens to equal a header cell from overwriting the
        # header, without needing to know the header's column labels.
        replaced = False
        seen_separator = False
        out_lines: list[str] = []
        for line in lines:
            is_sep = _is_separator_row(line)
            if is_sep:
                seen_separator = True
            cell = _row_col(line, key_col)
            if not replaced and seen_separator and not is_sep and cell == key:
                out_lines.append(new_row)
                replaced = True
            else:
                out_lines.append(line)

        if not replaced:
            if out_lines and not out_lines[-1].endswith("\n"):
                out_lines[-1] += "\n"
            out_lines.append(new_row)

        atomic_write(registry_path, "".join(out_lines))

    print("upserted" if replaced else "appended")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
