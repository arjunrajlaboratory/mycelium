#!/usr/bin/env python3
"""Seed push-active domain files from a hand-curated seeds.yaml.

Entries are COPIED from learnings.md (monolith), not moved.
Idempotent — re-running with the same seeds.yaml is a no-op.
"""

from __future__ import annotations

import argparse
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


DOMAIN_DEFAULTS = {
    "figures": {
        "description": "color, DPI, typography, and layout for publication plots",
        "matches": ["**/figures/**", "**/panel_*.py", "**/composite*.py"],
    },
    "extraction": {
        "description": "KG claim extraction prompts, schemas, model selection",
        "matches": ["**/Claim Extraction/**", "**/extraction/**", "**/*_extract*.py"],
    },
    "pipelines": {
        "description": "DAG orchestration, retry, error handling, pipeline outputs",
        "matches": ["**/orchestrator/**", "**/pipeline*.py", "**/DAG*.py"],
    },
    "statistics": {
        "description": "test selection, assumption checking, effect-size, reporting",
        "matches": ["**/analysis/**", "**/stats*.py", "**/test_stat*.py"],
    },
    "writing": {
        "description": "IMRAD, citations, prose conventions, figure legends",
        "matches": [
            "**/manuscript/**",
            "**/docs/paper/**",
            "**/*.tex",
            "**/*manuscript*.md",
        ],
    },
}


def _build_frontmatter(domain: str) -> str:
    defaults = DOMAIN_DEFAULTS.get(domain)
    if defaults is None:
        raise SystemExit(f"Unknown domain: {domain}. Add to DOMAIN_DEFAULTS.")
    lines = [
        "---",
        f"domain: {domain}",
        f"description: {defaults['description']}",
        "push_active: true",
        "matches:",
    ]
    for m in defaults["matches"]:
        lines.append(f'  - "{m}"')
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _render_seed_entry(entry: dict, seed: dict) -> str:
    parts = [f"## [{entry['date']}] {entry['title']}"]
    if seed.get("tags"):
        parts.append(f"**Tags**: {', '.join(seed['tags'])}")
    if seed.get("triggers"):
        parts.append(f"**Triggers**: {seed['triggers']}")
    parts.append(entry["body"].rstrip())
    return "\n".join(parts) + "\n"


def seed_from_yaml(seeds_yaml: Path, living_dir: Path) -> None:
    seeds = parse_seeds(seeds_yaml)
    monolith = living_dir / "learnings.md"
    if not monolith.exists():
        raise SystemExit(f"Monolith not found: {monolith}")

    by_domain: dict[str, list[tuple[dict, dict]]] = {}
    for seed in seeds:
        domain = seed["domain"]
        entry = extract_entry(monolith, seed["title"])
        if entry is None:
            raise SystemExit(f"Seed title not found in monolith: {seed['title']!r}")
        by_domain.setdefault(domain, []).append((seed, entry))

    learnings_dir = living_dir / "learnings"
    learnings_dir.mkdir(exist_ok=True)
    for domain, pairs in by_domain.items():
        pairs.sort(key=lambda p: p[1]["date"], reverse=True)
        body = _build_frontmatter(domain)
        body += "\n".join(_render_seed_entry(entry, seed) for seed, entry in pairs)
        domain_file = learnings_dir / f"{domain}.md"
        domain_file.write_text(body, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("seeds_yaml", type=Path)
    ap.add_argument("--living-dir", type=Path, required=True)
    args = ap.parse_args()
    seed_from_yaml(args.seeds_yaml, args.living_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
