#!/usr/bin/env python3
"""Regenerate .living/MENU.md from .living/learnings/*.md frontmatter.

Idempotent. Called by mycelium-post-action.sh after .living/learnings/ writes.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import _frontmatter as fm  # noqa: E402


def _count_entries(body: str) -> int:
    return sum(1 for line in body.splitlines() if line.startswith("## ["))


def generate_menu(living_dir: Path, out_path: Path) -> None:
    learnings_dir = living_dir / "learnings"
    push_active: list[dict] = []
    pull_only: list[dict] = []
    total_entries = 0

    if learnings_dir.is_dir():
        for md in sorted(learnings_dir.glob("*.md")):
            if md.name.startswith("_"):
                continue
            meta, body = fm.parse(md)
            domain = meta.get("domain", md.stem)
            description = meta.get("description", "(no description)")
            count = _count_entries(body)
            total_entries += count
            bucket = push_active if meta.get("push_active") else pull_only
            bucket.append(
                {"domain": domain, "description": description, "count": count}
            )

    lines: list[str] = []
    lines.append("# Mycelium Knowledge Menu")
    lines.append("")
    lines.append(
        f"Generated: {datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}  ·  "
        f"Push-active: {len(push_active)}  ·  Pull-only: {len(pull_only)}  ·  "
        f"Total entries: {total_entries}"
    )
    lines.append("")
    lines.append("**Push-active** (auto-injected on matching Edit):")
    if push_active:
        for d in push_active:
            lines.append(f"- **{d['domain']}** ({d['count']}) — {d['description']}")
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("**Pull-only** (Readable on demand):")
    if pull_only:
        for d in pull_only:
            lines.append(f"- **{d['domain']}** ({d['count']}) — {d['description']}")
    else:
        lines.append("- (none)")
    lines.append("")

    monolith = living_dir / "learnings.md"
    if monolith.exists():
        mono_body = monolith.read_text(encoding="utf-8")
        mono_count = _count_entries(mono_body)
        lines.append(
            f"**Unmigrated corpus**: `.living/learnings.md` "
            f"(existing monolith, {mono_count} entries). Phase 1 does not reshape this file. "
            "Seeded entries are copied, not moved — they appear in both places during Phase 1."
        )
    lines.append("")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text("\n".join(lines), encoding="utf-8")
    tmp.replace(out_path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("living_dir", type=Path, help="Path to .living/ directory")
    args = ap.parse_args()
    out = args.living_dir / "MENU.md"
    generate_menu(args.living_dir, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
