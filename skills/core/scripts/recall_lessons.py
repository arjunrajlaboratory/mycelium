#!/usr/bin/env python3
"""Query .living/learnings.md and .living/decisions.md for matching entries.

Cheap progressive-disclosure tool: instead of pulling whole files into
context, fetch only the entries matching tag(s), ID(s), or a date cutoff.

Usage:
    recall_lessons.py --living-dir <path> [--tag X]... [--id L-42]... [--since YYYY-MM-DD] [--file learnings|decisions|all] [--max N]

ANY-match semantics: if --tag is given multiple times, an entry matches
if it carries ANY of the requested tags. --id and --since further filter
the result set.

Exit codes:
    0  matches found (or no filters supplied — prints all)
    1  no matches found
    2  bad arguments
"""

import argparse
import sys
from pathlib import Path

# Import from the sibling generate_index module so we share the parsing helpers.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import generate_index as gi  # noqa: E402


def _slice_entry_text(path: Path, header_prefix: str, header_line_no: int) -> str:
    """Return the entry block starting at header_line_no (1-indexed) up to but
    not including the next header line or EOF."""
    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for n, raw in enumerate(fh, start=1):
            if n < header_line_no:
                continue
            if n > header_line_no and raw.startswith(header_prefix):
                break
            lines.append(raw.rstrip("\n"))
    # Trim trailing blanks
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


def recall(
    living_dir: Path,
    tags: list[str] | None = None,
    ids: list[str] | None = None,
    since: str | None = None,
    file_filter: str = "all",
    max_results: int = 20,
) -> list[dict]:
    """Return matching entry records (with `text` field) per filter combination.

    Filters compose AND-style across types but ANY-match within `tags`/`ids`.
    """
    tags = tags or []
    ids = ids or []
    tag_set = {t.lower() for t in tags}
    id_set = set(ids)

    sources: list[tuple[Path, str, str]] = []
    if file_filter in ("learnings", "all"):
        p = living_dir / "learnings.md"
        if p.exists():
            sources.append((p, "learnings", "L"))
    if file_filter in ("decisions", "all"):
        p = living_dir / "decisions.md"
        if p.exists():
            sources.append((p, "decisions", "D"))

    matched: list[dict] = []
    for path, file_type, prefix in sources:
        header_prefix = "### " if file_type in ("learnings", "decisions") else "## "
        entries = gi.collect_entries(path, file_type, prefix)
        for e in entries:
            if id_set and e["id"] not in id_set:
                continue
            if tag_set and not any(t.lower() in tag_set for t in e["tags"]):
                continue
            if since and e["date"] and e["date"] < since:
                continue
            if since and not e["date"]:
                # Entries without dates are excluded when --since is requested
                continue
            text = _slice_entry_text(path, header_prefix, e["line_no"])
            matched.append(
                {
                    "id": e["id"],
                    "title": e["title"],
                    "date": e["date"],
                    "tags": e["tags"],
                    "file": file_type,
                    "text": text,
                }
            )

    matched.sort(key=lambda r: (r["date"] or "0000-00-00"), reverse=True)
    return matched[:max_results]


def _format_match(record: dict) -> str:
    return record["text"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Query .living/ for entries matching tag/ID/date filters."
    )
    parser.add_argument(
        "--living-dir",
        required=True,
        type=Path,
        help="Path to the .living/ directory.",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Match any entry carrying this tag. Repeatable.",
    )
    parser.add_argument(
        "--id",
        action="append",
        default=[],
        help="Match the entry with this ID (e.g. L-42, D-7). Repeatable.",
    )
    parser.add_argument(
        "--since",
        type=str,
        default=None,
        help="Only entries dated >= YYYY-MM-DD (entries without dates are excluded).",
    )
    parser.add_argument(
        "--file",
        choices=["learnings", "decisions", "all"],
        default="all",
        help="Which file(s) to search. Default: all.",
    )
    parser.add_argument(
        "--max",
        dest="max_results",
        type=int,
        default=20,
        help="Maximum entries to return. Default: 20.",
    )
    parser.add_argument(
        "--count-only",
        action="store_true",
        help="Print only the match count, no entry text.",
    )

    args = parser.parse_args()

    living_dir: Path = args.living_dir.resolve()
    if not living_dir.is_dir():
        parser.error(f"--living-dir '{living_dir}' is not a directory.")

    matches = recall(
        living_dir=living_dir,
        tags=args.tag,
        ids=args.id,
        since=args.since,
        file_filter=args.file,
        max_results=args.max_results,
    )

    if args.count_only:
        print(f"{len(matches)} matches")
        sys.exit(0 if matches else 1)

    if not matches:
        print("No matches.", file=sys.stderr)
        sys.exit(1)

    for i, record in enumerate(matches):
        if i > 0:
            print()
            print("---")
            print()
        print(f"[{record['file']}] {record['id']}")
        print(_format_match(record))

    sys.exit(0)


if __name__ == "__main__":
    main()
