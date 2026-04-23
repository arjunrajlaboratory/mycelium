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
    # Tags and triggers: join into text, then tokenize (splits on _-./ and spaces)
    tag_text = " ".join(entry.get("tags", []))
    trigger_text = " ".join(entry.get("triggers", []))
    tag_tokens = set(tokenize_text(tag_text))
    trigger_tokens = set(tokenize_text(trigger_text))
    body_first500 = entry.get("body", "")[:500]
    body_tokens = set(tokenize_text(body_first500))

    for tok in path_tokens:
        if tok in title_tokens:
            score += 3
        if tok in tag_tokens:
            score += 2
        if tok in trigger_tokens:
            score += 3
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
    """Tokenize edit path: basename stem + parent + grandparent, split on _-./.
    Adds singular forms (stripping trailing 's') for tokens len >= 4.
    """
    p = Path(path)
    parts = [p.stem]
    if p.parent.name:
        parts.append(p.parent.name)
    if p.parent.parent.name:
        parts.append(p.parent.parent.name)
    combined = " ".join(parts)
    raw = _SPLIT_RE.split(combined)
    tokens = _filter_tokens(raw)
    # Add singular forms for plural-looking tokens (len >= 4, ends in 's')
    out = list(tokens)
    seen = set(tokens)
    for t in tokens:
        if len(t) >= 4 and t.endswith("s") and t[:-1] not in seen:
            out.append(t[:-1])
            seen.add(t[:-1])
    return out


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


SCORE_THRESHOLD = 2.0


def select_entries(
    domains: list[dict], path_tokens: list[str], k: int = 3, today: str | None = None
) -> tuple[list[dict], int]:
    """Score entries across matching domains, filter by threshold, return top K.

    Returns (selected_entries, dropped_below_threshold_count).
    Selected entries are dicts: {domain, title, date, score, entry}.
    """
    scored: list[dict] = []
    dropped = 0
    for dom in domains:
        for entry in dom.get("entries", []):
            s = score_entry(entry, path_tokens, today=today)
            if s < SCORE_THRESHOLD:
                dropped += 1
                continue
            scored.append(
                {
                    "domain": dom["domain"],
                    "title": entry.get("title", ""),
                    "date": entry.get("date", ""),
                    "score": s,
                    "entry": entry,
                }
            )
    scored.sort(key=lambda e: e["score"], reverse=True)
    return scored[:k], dropped


DEFAULT_CAP_BYTES = 8000
TRUNC_MARKER = "… [truncated; Read .living/learnings/{domain}.md]"


def _render_entry(sel: dict) -> str:
    e = sel["entry"]
    parts = [f"## [{e['date']}] {e['title']}"]
    if e.get("tags"):
        parts.append(f"**Tags**: {', '.join(e['tags'])}")
    if e.get("triggers"):
        parts.append(f"**Triggers**: {json.dumps(e['triggers'])}")
    parts.append(e.get("body", "").rstrip())
    return "\n".join(parts) + "\n"


def render_selected(
    selected: list[dict], cap_bytes: int = DEFAULT_CAP_BYTES
) -> tuple[str, bool]:
    """Render selected entries to a single content string.

    Drops lowest-score entries first if combined exceeds cap.
    If a single entry alone exceeds cap, truncates body mid-line with marker.
    Returns (rendered_str, truncated_bool).
    """
    if not selected:
        return "", False

    by_score_desc = sorted(selected, key=lambda s: -s["score"])
    truncated = False
    kept = list(by_score_desc)
    while kept:
        rendered = "\n\n".join(_render_entry(s) for s in kept)
        if len(rendered.encode("utf-8")) <= cap_bytes:
            return rendered, (len(kept) != len(selected))
        if len(kept) == 1:
            break
        kept = kept[:-1]
        truncated = True

    sel = kept[0]
    e = sel["entry"]
    head = f"## [{e['date']}] {e['title']}\n"
    if e.get("tags"):
        head += f"**Tags**: {', '.join(e['tags'])}\n"
    marker = TRUNC_MARKER.replace("{domain}", sel["domain"])
    budget = cap_bytes - len(head.encode("utf-8")) - len(marker.encode("utf-8")) - 2
    budget = max(budget, 0)
    body = e.get("body", "")
    body_bytes = body.encode("utf-8")[:budget]
    while body_bytes:
        try:
            body_str = body_bytes.decode("utf-8")
            break
        except UnicodeDecodeError:
            body_bytes = body_bytes[:-1]
    else:
        body_str = ""
    return head + body_str + "\n" + marker + "\n", True


def compute_push(
    edit_path: str,
    living_dir: Path,
    today: str | None = None,
    k: int = 3,
    cap_bytes: int = DEFAULT_CAP_BYTES,
) -> dict:
    """Full pipeline: collect → match → score → select → render → structured result."""
    domains_meta = collect_push_active_domains(living_dir)
    path_tokens = tokenize_path(edit_path)

    matching = []
    for d in domains_meta:
        if matches_domain(edit_path, d["matches"]):
            entries = parse_entries(d["body"])
            matching.append(
                {
                    "domain": d["domain"],
                    "entries": entries,
                    "top_k_push": d.get("top_k_push"),
                    "token_cap": d.get("token_cap"),
                }
            )

    if not matching:
        return {
            "entries": [],
            "matched_domains": [],
            "truncated": False,
            "dropped_below_threshold": 0,
            "bytes": 0,
            "schema_version": SCHEMA_VERSION,
        }

    effective_k = k
    effective_cap = cap_bytes
    if len(matching) == 1:
        if matching[0].get("top_k_push"):
            effective_k = matching[0]["top_k_push"]
        if matching[0].get("token_cap"):
            effective_cap = matching[0]["token_cap"] * 4

    selected, dropped = select_entries(
        matching, path_tokens, k=effective_k, today=today
    )
    rendered, truncated = render_selected(selected, cap_bytes=effective_cap)

    entry_dicts = [
        {
            "domain": s["domain"],
            "title": s["title"],
            "date": s["date"],
            "score": s["score"],
            "content": _render_entry(s),
        }
        for s in selected
    ]
    return {
        "entries": entry_dicts,
        "matched_domains": [d["domain"] for d in matching],
        "truncated": truncated,
        "dropped_below_threshold": dropped,
        "bytes": len(rendered.encode("utf-8")),
        "schema_version": SCHEMA_VERSION,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("edit_path", type=str)
    ap.add_argument("--living-dir", type=Path, required=True)
    args = ap.parse_args()
    result = compute_push(args.edit_path, args.living_dir)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
