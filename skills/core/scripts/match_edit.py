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
from datetime import date
from fnmatch import fnmatch
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import _frontmatter as fm  # noqa: E402

SCHEMA_VERSION = 1

_SPLIT_RE = re.compile(r"[_\-./\s]+")
_HEADER_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]\s+(.+?)\s*$")
_TAGS_RE = re.compile(r"^\*\*Tags\*\*\s*:\s*(.+)$", re.IGNORECASE)
_TRIGGERS_RE = re.compile(r"^\*\*Triggers\*\*\s*:\s*(.+?)\s*$", re.IGNORECASE)


def _parse_list_field(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        try:
            return [str(x).strip() for x in json.loads(raw)]
        except json.JSONDecodeError:
            pass
    return [t.strip().strip('"') for t in raw.split(",") if t.strip()]


def parse_entries(body: str) -> list[dict]:
    lines = body.splitlines()
    entries: list[dict] = []
    cur: dict | None = None
    for line in lines:
        m = _HEADER_RE.match(line)
        if m:
            if cur is not None:
                entries.append(cur)
            cur = {
                "date": m.group(1),
                "title": m.group(2),
                "tags": [],
                "triggers": [],
                "body": "",
            }
            continue
        if cur is None:
            continue
        tag_m = _TAGS_RE.match(line)
        if tag_m:
            cur["tags"] = _parse_list_field(tag_m.group(1))
            continue
        trig_m = _TRIGGERS_RE.match(line)
        if trig_m:
            cur["triggers"] = _parse_list_field(trig_m.group(1))
            continue
        cur["body"] += line + "\n"
    if cur is not None:
        entries.append(cur)
    return entries


def score_entry(entry: dict, path_tokens: list[str], today: str | None = None) -> float:
    score = 0.0
    title_tokens = set(tokenize_text(entry.get("title", "")))
    tag_tokens = {t.lower() for t in entry.get("tags", [])}
    trigger_tokens = {t.lower() for t in entry.get("triggers", [])}
    body_first500 = entry.get("body", "")[:500]
    body_tokens = set(tokenize_text(body_first500))

    for tok in path_tokens:
        if tok in title_tokens:
            score += 3
        if tok in tag_tokens:
            score += 2
        if tok in trigger_tokens:
            score += 2
        if tok in body_tokens:
            score += 1

    today_d = date.fromisoformat(today) if today else date.today()
    try:
        entry_d = date.fromisoformat(entry.get("date", ""))
    except (ValueError, TypeError):
        return score
    delta_days = (today_d - entry_d).days
    if 0 <= delta_days <= 60:
        score += 0.5
    return score


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


def matches_domain(path: str, patterns: list[str]) -> bool:
    """True if path matches any glob pattern. Supports ** via path-walking."""
    for pat in patterns:
        if _glob_match(path, pat):
            return True
    return False


def _glob_match(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/").lstrip("./")
    pattern = pattern.replace("\\", "/")
    if "**" not in pattern:
        return fnmatch(path, pattern)
    parts = pattern.split("**")
    return _seq_match(path, [p.strip("/") for p in parts])


def _seq_match(path: str, parts: list[str]) -> bool:
    """Parts in order; each (non-empty) must fnmatch a segment in sequence."""
    segments = path.split("/")
    parts = [p for p in parts if p != ""] or [""]
    if parts == [""]:
        return True
    i = 0
    for seg in segments:
        if i >= len(parts):
            return True
        if fnmatch(seg, parts[i]):
            i += 1
    if i < len(parts):
        if any(fnmatch(seg, parts[-1]) for seg in segments):
            return True
    return i >= len(parts)


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
