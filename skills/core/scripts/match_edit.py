#!/usr/bin/env python3
"""Score and select push-active learnings matching an edit path.

Invoked by mycelium-edit-injector.sh. Emits structured JSON on stdout.
See docs/designs/2026-04-22-accessibility-architecture.md §6.2 for full contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import _frontmatter as fm  # noqa: E402

SCHEMA_VERSION = 1


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
