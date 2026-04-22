#!/usr/bin/env python3
"""Seed push-active domain files from a hand-curated seeds.yaml.

Entries are COPIED from learnings.md (monolith), not moved.
Idempotent — re-running with the same seeds.yaml is a no-op.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))


_HEADER_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]\s+(.+?)\s*$")


def parse_seeds(yaml_path: Path) -> list[dict]:
    """Parse a minimal seeds.yaml file.

    Expected shape:
        seeds:
          - title: "X"
            domain: figures
            tags: [a, b]   # optional
            triggers: [c]  # optional
    """
    text = yaml_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    seeds: list[dict] = []
    cur: dict | None = None
    in_seeds_block = False
    for line in lines:
        if line.rstrip() == "seeds:":
            in_seeds_block = True
            continue
        if not in_seeds_block:
            continue
        if line.startswith("  - ") or line.startswith("- "):
            if cur is not None:
                seeds.append(cur)
            cur = {"title": "", "domain": "", "tags": [], "triggers": []}
            rest = line.split("-", 1)[1].strip()
            if ":" in rest:
                k, _, v = rest.partition(":")
                cur[k.strip()] = _coerce(v.strip())
            continue
        if cur is None:
            continue
        if line.startswith("    ") and ":" in line:
            k, _, v = line.strip().partition(":")
            cur[k.strip()] = _coerce(v.strip())
    if cur is not None:
        seeds.append(cur)
    for s in seeds:
        s.setdefault("tags", [])
        s.setdefault("triggers", [])
    return seeds


def _coerce(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1]
        return [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def extract_entry(monolith: Path, title: str) -> dict | None:
    """Find an entry by title in the monolith. Returns None if absent."""
    text = monolith.read_text(encoding="utf-8")
    lines = text.splitlines()
    start_idx = None
    found_date = None
    for i, line in enumerate(lines):
        m = _HEADER_RE.match(line)
        if m and m.group(2) == title:
            start_idx = i
            found_date = m.group(1)
            break
    if start_idx is None:
        return None
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if _HEADER_RE.match(lines[j]):
            end_idx = j
            break
    body_lines = lines[start_idx + 1 : end_idx]
    while body_lines and not body_lines[-1].strip():
        body_lines.pop()
    return {
        "date": found_date,
        "title": title,
        "body": "\n".join(body_lines) + ("\n" if body_lines else ""),
    }


def main() -> int:
    return 0  # seeding implementation in Task 13


if __name__ == "__main__":
    sys.exit(main())
