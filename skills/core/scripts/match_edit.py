#!/usr/bin/env python3
"""Score and select push-active learnings matching an edit path.

Invoked by mycelium-edit-injector.sh. Emits structured JSON on stdout.
See docs/designs/2026-04-22-accessibility-architecture.md §6.2 for full contract.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import _frontmatter as fm  # noqa: E402

SCHEMA_VERSION = 1

_SPLIT_RE = re.compile(r"[_\-./\s]+")


def _filter_tokens(raw: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for t in raw:
        t = t.lower()
        if not t:
            continue
        if len(t) <= 1:
            continue
        if t.isdigit():
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def tokenize_path(path: str) -> list[str]:
    """Tokenize edit path: basename stem + parent + grandparent, split on _-./."""
    p = Path(path)
    parts = [p.stem]
    if p.parent.name:
        parts.append(p.parent.name)
    if p.parent.parent.name:
        parts.append(p.parent.parent.name)
    combined = " ".join(parts)
    raw = _SPLIT_RE.split(combined)
    return _filter_tokens(raw)


def tokenize_text(text: str) -> list[str]:
    """Tokenize arbitrary text with the same rules."""
    raw = _SPLIT_RE.split(text)
    return _filter_tokens(raw)


def collect_push_active_domains(living_dir: Path) -> list[dict]:
    """Return metadata for all domain files with push_active=true + matches field."""
    learnings_dir = living_dir / "learnings"
    if not learnings_dir.is_dir():
        return []
    out: list[dict] = []
    for md in sorted(learnings_dir.glob("*.md")):
        meta, body = fm.parse(md)
        if not meta.get("push_active"):
            continue
        if not meta.get("matches"):
            continue
        out.append(
            {
                "path": md,
                "domain": meta.get("domain", md.stem),
                "description": meta.get("description", ""),
                "matches": meta["matches"],
                "top_k_push": meta.get("top_k_push"),
                "token_cap": meta.get("token_cap"),
                "body": body,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit_path", type=str)
    ap.add_argument("--living-dir", type=Path, required=True)
    args = ap.parse_args()

    result = {
        "entries": [],
        "matched_domains": [],
        "truncated": False,
        "dropped_below_threshold": 0,
        "bytes": 0,
        "schema_version": SCHEMA_VERSION,
    }
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
