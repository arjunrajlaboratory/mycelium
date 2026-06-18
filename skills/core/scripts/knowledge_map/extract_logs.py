"""
extract_logs.py — Walk .living/log/ directories and extract LogNode objects.

Phase: M-logs (episodic-log tier, separate from entries).
Python 3.11+, stdlib only.

Contract mirrors extract_entries.py:
  - Persistent id ledger (log-ids.json, l-NNNNN namespace)
  - Deterministic minting order: sort by (project_id, source_path) before new ids
  - Fingerprint: sha256_hash(project_id + "\\0" + source_path) — one per file
  - Tombstoning of ids absent this run
  - Returns ExtractLogsResult with logs sorted by id
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from graph_model import (
    LogNode,
    ProjectMeta,
    sha256_hash,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)")
_TAGS_LINE_RE = re.compile(r"^\*{0,2}Tags\*{0,2}:\s*(.+)", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_-]*)")

# Filename schema: YYYY-MM-DD-NNN-<slug>.md
_LOG_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)-(.+)\.md$")

# Basenames and suffixes to exclude at any depth inside log/
_EXCLUDED_BASENAMES: frozenset[str] = frozenset({"LOG_REGISTRY.md"})
_EXCLUDED_SUFFIX = "_MANIFEST.md"

# Excerpt target length (chars)
_EXCERPT_LEN = 500

# Minimum body length (stripped) to keep a log node.
# Logs shorter than this are auto-generated session-boundary stubs
# ("## Session Log / ### HH:MM Session started / Branch: main / ...") with no narrative.
_MIN_BODY_LEN = 150

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExtractLogsResult:
    logs: list[LogNode]
    ledger: dict  # updated log-ids.json payload
    report: list[str]  # human-readable warnings


# ---------------------------------------------------------------------------
# Ledger helpers (mirrors entry ledger; parameterised by namespace prefix "l")
# ---------------------------------------------------------------------------


def _mint_log_id(counter: list[int]) -> str:
    """Return next sequential l-NNNNN id and increment counter."""
    counter[0] += 1
    return f"l-{counter[0]:05d}"


def _find_in_log_ledger(ledger: dict, fingerprint: str) -> str | None:
    """Return existing ledger id for this fingerprint (current or previous)."""
    for log_id, meta in ledger.items():
        if meta.get("current_fingerprint") == fingerprint:
            return log_id
        if fingerprint in meta.get("previous_fingerprints", []):
            return log_id
    return None


def load_log_ledger(ledger_path: Path) -> dict:
    """
    Load log-ids.json from disk.  Returns {} on missing file or parse error.
    Mirror of the entry ledger load pattern in cli.py / build_graph.py.
    """
    if not ledger_path.exists():
        return {}
    try:
        import json

        return json.loads(ledger_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_log_ledger(ledger_path: Path, ledger: dict) -> None:
    """
    Persist ledger to log-ids.json with sorted keys for determinism.
    Mirror of the entry ledger save pattern.
    """
    import json

    ledger_path.write_text(
        json.dumps(ledger, sort_keys=True, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------


def _should_exclude(rel_to_log_dir: Path) -> bool:
    """Return True if this log-dir-relative path should be skipped."""
    name = rel_to_log_dir.name

    # Excluded basenames at any depth
    if name in _EXCLUDED_BASENAMES:
        return True

    # *_MANIFEST.md at any depth
    if name.endswith(_EXCLUDED_SUFFIX):
        return True

    # Nested .living guard (belt-and-suspenders; shouldn't occur but safe)
    if ".living" in rel_to_log_dir.parts:
        return True

    return False


def _parse_log_file(fpath: Path) -> tuple[str, str, list[str]]:
    """
    Return (title, body_excerpt, tags) for a log file.

    title       — first # or ## heading text, else filename stem
    body_excerpt — normalize_text of first EXCERPT_LEN chars of full body
    tags         — from frontmatter ``tags:`` line OR ``#hashtag`` tokens
    """
    try:
        raw = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fpath.stem, "", []

    lines = raw.splitlines()

    # --- title: first heading ---
    title: str | None = None
    for line in lines:
        m = _HEADING_RE.match(line.strip())
        if m:
            title = m.group(1).strip()
            break
    if not title:
        title = fpath.stem

    # --- tags: frontmatter ``tags:`` line first, then #hashtags ---
    tags: list[str] = []

    # 1. Tags: line (same pattern as extract_entries._parse_tags)
    for line in lines:
        m = _TAGS_LINE_RE.match(line.strip())
        if m:
            raw_tags = m.group(1)
            tags = [t.strip().strip("*").strip() for t in re.split(r"[,;]", raw_tags)]
            tags = [t for t in tags if t]
            break

    # 2. Fallback: #hashtag tokens from first 30 lines
    if not tags:
        head = "\n".join(lines[:30])
        tags = _HASHTAG_RE.findall(head)

    # --- body_excerpt: full log body, frontmatter stripped, newlines preserved ---
    # Strip leading YAML frontmatter block (--- ... ---) if present
    stripped = raw
    if raw.startswith("---"):
        # Find the closing --- line (must be on its own line, after the opener)
        fm_end = raw.find("\n---", 3)
        if fm_end != -1:
            # Skip past the closing --- line and any immediately following newline
            after_fm = fm_end + 4  # len("\n---") == 4
            if after_fm < len(raw) and raw[after_fm] == "\n":
                after_fm += 1
            stripped = raw[after_fm:]
    body_excerpt = stripped  # full body, no character cap, newlines preserved

    return title, body_excerpt, tags


def _portfolio_rel(path: Path, portfolio_root: Path) -> str:
    """Return portfolio-relative posix path."""
    return path.relative_to(portfolio_root).as_posix()


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_logs(
    portfolio_root: Path,
    projects: list[ProjectMeta],
    log_id_ledger: dict,
) -> ExtractLogsResult:
    """
    Walk .living/log/ directories for each project and extract LogNode objects.

    Parameters
    ----------
    portfolio_root:
        Absolute path to the portfolio root directory.
    projects:
        List of ProjectMeta objects.  Only those with has_living=True are walked.
    log_id_ledger:
        Current log-ids.json payload (may be empty on cold build).
        Updated in-place and returned as ExtractLogsResult.ledger.

    Returns
    -------
    ExtractLogsResult with logs sorted by id.
    """
    report: list[str] = []

    # Working ledger — shallow copy of each meta dict so we don't mutate the caller's
    working_ledger: dict = {k: dict(v) for k, v in log_id_ledger.items()}

    # Determine current max counter from existing l-NNNNN ids
    existing_nums: list[int] = [
        int(lid.split("-")[1]) for lid in working_ledger if re.match(r"^l-\d+$", lid)
    ]
    id_counter: list[int] = [max(existing_nums) if existing_nums else 0]

    # Track ids seen this run (for tombstoning)
    seen_ids: set[str] = set()

    # Collect pending records before minting, so we can sort for determinism
    pending_records: list[dict] = []

    for project in projects:
        if not project.has_living:
            continue

        project_dir = portfolio_root / project.path
        log_dir = project_dir / ".living" / "log"

        if not log_dir.is_dir():
            # Not an error — many projects may have no log/ subtree yet
            continue

        for fpath in sorted(log_dir.rglob("*.md")):
            rel = fpath.relative_to(log_dir)

            if _should_exclude(rel):
                continue

            source_path = _portfolio_rel(fpath, portfolio_root)

            # Fingerprint: keyed on project_id + NUL + source_path (file-level)
            fingerprint = sha256_hash(project.id + "\0" + source_path)

            # Filename schema parse
            fname = fpath.name
            m = _LOG_FILENAME_RE.match(fname)
            if m:
                session_date: str | None = m.group(1)
                session_seq: int | None = int(m.group(2))
            else:
                session_date = None
                session_seq = None

            title, body_excerpt, tags = _parse_log_file(fpath)

            # Drop near-empty session-boundary stubs — no narrative value.
            # The follows-chain is built downstream over only the logs that reach
            # link_logs; dropping here automatically re-links around these stubs.
            if len(body_excerpt.strip()) < _MIN_BODY_LEN:
                report.append(
                    f"STUB DROPPED ({source_path}): body length "
                    f"{len(body_excerpt.strip())} < {_MIN_BODY_LEN}"
                )
                continue

            pending_records.append(
                {
                    "project_id": project.id,
                    "family": project.family,
                    "source_path": source_path,
                    "fingerprint": fingerprint,
                    "session_date": session_date,
                    "session_seq": session_seq,
                    "title": title,
                    "body_excerpt": body_excerpt,
                    "tags": tags,
                }
            )

    # Sort for deterministic minting on cold build: (project_id, source_path)
    pending_records.sort(key=lambda r: (r["project_id"], r["source_path"]))

    logs: list[LogNode] = []

    for rec in pending_records:
        fingerprint = rec["fingerprint"]

        # Resolve or mint id
        log_id = _find_in_log_ledger(working_ledger, fingerprint)
        if log_id is None:
            log_id = _mint_log_id(id_counter)

        seen_ids.add(log_id)

        # Update ledger entry
        working_ledger[log_id] = {
            "current_fingerprint": fingerprint,
            "previous_fingerprints": working_ledger.get(log_id, {}).get(
                "previous_fingerprints", []
            ),
            "source_path": rec["source_path"],
            "status": "active",
        }

        node = LogNode(
            id=log_id,
            project_id=rec["project_id"],
            family=rec["family"],
            session_date=rec["session_date"],
            session_seq=rec["session_seq"],
            title=rec["title"],
            body_excerpt=rec["body_excerpt"],
            source_path=rec["source_path"],
            tags=rec["tags"],
            mentions=[],  # populated downstream by linker
        )
        logs.append(node)

    # Tombstone ids not seen this run
    for log_id, meta in working_ledger.items():
        if log_id not in seen_ids and meta.get("status") != "tombstone":
            working_ledger[log_id] = {**meta, "status": "tombstone"}

    # Sort output by id (lexicographic on l-NNNNN is equivalent to numeric)
    logs.sort(key=lambda n: n.id)

    return ExtractLogsResult(
        logs=logs,
        ledger=working_ledger,
        report=report,
    )
