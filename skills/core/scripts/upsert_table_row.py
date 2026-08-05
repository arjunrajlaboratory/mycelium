#!/usr/bin/env python3
"""Lock and atomically upsert one exact-key data row in a Markdown table."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from mycelium_locks import LockError, atomic_write_text, durable_path_lock


def row_cells(line: str) -> list[str] | None:
    stripped = line.rstrip("\n")
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(cell and set(cell) <= {"-", ":", " "} for cell in cells)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("table", type=Path)
    parser.add_argument("key")
    parser.add_argument("row")
    parser.add_argument("--key-column", type=int, default=1, metavar="N")
    args = parser.parse_args(argv)

    if args.key_column < 1:
        parser.error("--key-column must be one-based and positive")
    replacement = args.row if args.row.endswith("\n") else args.row + "\n"
    replacement_cells = row_cells(replacement)
    if replacement_cells is None or args.key_column > len(replacement_cells):
        print("error: replacement must be a complete Markdown table row", file=sys.stderr)
        return 1
    if replacement_cells[args.key_column - 1] != args.key:
        print("error: replacement key column does not equal the requested key", file=sys.stderr)
        return 1

    table = Path(os.path.abspath(args.table))
    try:
        with durable_path_lock(table):
            if not table.exists():
                print(f"error: table not found: {table}", file=sys.stderr)
                return 1
            lines = table.read_text(encoding="utf-8").splitlines(keepends=True)
            expected_columns = next(
                (
                    len(cells)
                    for line in lines
                    if (cells := row_cells(line)) is not None and not is_separator(cells)
                ),
                len(replacement_cells),
            )
            header_rows = {
                index
                for index in range(len(lines) - 1)
                if (cells := row_cells(lines[index])) is not None
                and (separator := row_cells(lines[index + 1])) is not None
                and len(cells) == len(separator)
                and is_separator(separator)
            }
            if len(replacement_cells) != expected_columns:
                print(
                    f"error: replacement has {len(replacement_cells)} columns; "
                    f"table has {expected_columns}",
                    file=sys.stderr,
                )
                return 1

            replaced = False
            output: list[str] = []
            for index, line in enumerate(lines):
                cells = row_cells(line)
                if (
                    not replaced
                    and index not in header_rows
                    and cells is not None
                    and len(cells) == expected_columns
                    and not is_separator(cells)
                    and cells[args.key_column - 1] == args.key
                ):
                    output.append(replacement)
                    replaced = True
                else:
                    output.append(line)
            if not replaced:
                if output and not output[-1].endswith("\n"):
                    output[-1] += "\n"
                output.append(replacement)
            atomic_write_text(table, "".join(output))
    except (LockError, OSError) as exc:
        print(f"error: table update failed: {exc}", file=sys.stderr)
        return 1

    print("upserted" if replaced else "appended")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
