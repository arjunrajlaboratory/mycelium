"""
extract_entries.py — Walk .living/ directories and extract Entry objects.

Phase: M1 (file-walking + signature-based entry detection).
Python 3.11+, stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Generator

from graph_model import (
    Entry,
    EntryKind,
    EntryStatus,
    Facet,
    ProjectMeta,
    SourceShape,
    Stage,
    StageSource,
    sha256_hash,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Basenames excluded at ANY depth (R4)
_EXCLUDED_FILENAMES: frozenset[str] = frozenset(
    {
        "INDEX.md",
        "MENU.md",
        "LOG_REGISTRY.md",
        "FINDINGS_REGISTRY.md",
        "last-session.md",
    }
)

# Subtrees of .living/ that are always excluded (R-logs: "log" removed; R5a: no "conventions")
_EXCLUDED_SUBTREES: frozenset[str] = frozenset({"generated-conventions", "graph"})

# Aggregate files that live directly in .living/ and are included
_AGGREGATE_FILENAMES: dict[str, EntryKind] = {
    "learnings.md": EntryKind.learning,
    "decisions.md": EntryKind.decision,
    "findings.md": EntryKind.finding,
}

# Regex patterns
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_DATE_RE = re.compile(r"\[?\d{4}-\d{2}-\d{2}\]?")
_EXPLICIT_ID_RE = re.compile(r"\b[DLF]-?\d+\b")
_FINDING_MARKER_RE = re.compile(r"Finding:", re.IGNORECASE)
_TAGS_LINE_RE = re.compile(r"^\*{0,2}Tags\*{0,2}:\s*(.+)", re.IGNORECASE)
_MITIGATION_TYPE_RE = re.compile(
    r"^\s*(?:\*{2})?mitigation_type(?:\*{2})?\s*:\s*(.+)$", re.IGNORECASE
)
_FINDING_STATUS_RE = re.compile(
    r"^\s*(?:\*{2})?Status(?:\*{2})?\s*:\s*(.+)$", re.IGNORECASE
)
_SOURCE_PROJECT_RE = re.compile(
    r"^\s*(?:\*{2})?source(?:\*{2})?\s*:\s*(.+)$", re.IGNORECASE
)
_DATE_EXTRACT_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_YAML_DATE_RE = re.compile(r"^date:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

# Stage path keywords (order matters — first match wins in path check)
_PATH_STAGE_MAP: list[tuple[str, Stage]] = [
    ("figures/", Stage.figure_generation),
    ("data/", Stage.data_registry),
    ("docs/plans", Stage.planning),
    ("eval", Stage.evaluation),
    ("tests/", Stage.infrastructure),
    ("analysis/", Stage.analysis),
    ("plan", Stage.planning),
]

# Stage keyword table applied to title + tags (lowercased)
_KW_STAGE_MAP: list[tuple[list[str], Stage]] = [
    (["figure", "panel", "dpi", "colorblind"], Stage.figure_generation),
    (["prereg", "protocol", "pilot"], Stage.planning),
    (["calibration", "benchmark", "κ", "kappa"], Stage.evaluation),
    (["extract", "prompt", "llm"], Stage.analysis),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExtractResult:
    entries: list[Entry]
    facets: dict[str, Facet]  # keyed by entry id
    ledger: dict  # updated entry-ids.json payload
    report: list[str]  # human-readable warnings


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_entry_signature(heading_text: str) -> bool:
    """Return True if heading_text qualifies as an entry boundary."""
    if _DATE_RE.search(heading_text):
        return True
    if _EXPLICIT_ID_RE.search(heading_text):
        return True
    if _FINDING_MARKER_RE.search(heading_text):
        return True
    return False


def _is_template(heading_text: str, body_lines: list[str]) -> bool:
    """Return True if the section looks like a template/sample placeholder."""
    combined = heading_text + " " + " ".join(body_lines)
    if "<placeholder>" in combined:
        return True
    if "YYYY-MM-DD" in combined:
        return True
    if re.search(r"TEMPLATE", combined, re.IGNORECASE):
        return True
    return False


def _extract_date(text: str) -> str | None:
    m = _DATE_EXTRACT_RE.search(text)
    return m.group(1) if m else None


def _clean_title(heading_text: str) -> str:
    """Strip date patterns and explicit id tokens from heading text."""
    title = heading_text.strip()
    # Remove bracketed dates like [2026-01-15]
    title = re.sub(r"\[\d{4}-\d{2}-\d{2}\]", "", title)
    # Remove bare dates like 2026-01-15
    title = re.sub(r"\d{4}-\d{2}-\d{2}", "", title)
    # Remove explicit ids like D1, L-177, F-001
    title = re.sub(r"\b[DLF]-?\d+\b", "", title)
    # Remove leading/trailing punctuation like " — ", ":", etc.
    title = re.sub(r"^[\s\-–—:]+|[\s\-–—:]+$", "", title)
    return title.strip()


def _parse_tags(body_lines: list[str]) -> list[str]:
    """Extract tags from a Tags:/``**Tags**:`` line if present."""
    for line in body_lines:
        m = _TAGS_LINE_RE.match(line.strip())
        if m:
            raw = m.group(1)
            tags = [t.strip().strip("*").strip() for t in re.split(r"[,;]", raw)]
            return [t for t in tags if t]
    return []


_EXCERPT_LEN = 2000


def _parse_signal_fields(
    body_lines: list[str],
) -> tuple[str | None, str | None, str | None]:
    """Extract (mitigation_type, finding_status, source_project) from body lines.

    Returns lowercased/stripped values, or None when absent.
    """
    mitigation_type: str | None = None
    finding_status: str | None = None
    source_project: str | None = None

    for line in body_lines:
        stripped = line.strip()
        if mitigation_type is None:
            m = _MITIGATION_TYPE_RE.match(stripped)
            if m:
                mitigation_type = m.group(1).strip().lower()
                continue
        if finding_status is None:
            m = _FINDING_STATUS_RE.match(stripped)
            if m:
                finding_status = m.group(1).strip().lower()
                continue
        if source_project is None:
            m = _SOURCE_PROJECT_RE.match(stripped)
            if m:
                source_project = m.group(1).strip()
                continue
        # Early-exit once all three found
        if mitigation_type and finding_status and source_project:
            break

    return mitigation_type, finding_status, source_project


def _body_excerpt(body_lines: list[str]) -> str:
    """Return first 2000 chars of normalized body."""
    full = "\n".join(body_lines).strip()
    return full[:_EXCERPT_LEN]


def _content_hash(body_lines: list[str]) -> str:
    full = "\n".join(body_lines).strip()
    return sha256_hash(full)


def _portfolio_rel(path: Path, portfolio_root: Path) -> str:
    """Return portfolio-relative path with forward slashes."""
    return path.relative_to(portfolio_root).as_posix()


def _infer_stage(
    source_path: str, title: str, tags: list[str]
) -> tuple[Stage, StageSource]:
    """Infer Stage and StageSource. First match wins."""
    # 1. Path-based
    sp_lower = source_path.lower()
    for fragment, stage in _PATH_STAGE_MAP:
        if fragment in sp_lower:
            return stage, StageSource.path

    # 2. Keyword-based (title + tags lowercased)
    text = (title + " " + " ".join(tags)).lower()
    for keywords, stage in _KW_STAGE_MAP:
        for kw in keywords:
            if kw in text:
                return stage, StageSource.keyword

    # 3. Default
    return Stage.unassigned, StageSource.default


# ---------------------------------------------------------------------------
# ID ledger helpers
# ---------------------------------------------------------------------------


def _mint_id(counter: list[int]) -> str:
    """Return next sequential e-NNNNN id and increment counter."""
    counter[0] += 1
    return f"e-{counter[0]:05d}"


def _find_in_ledger(
    ledger: dict,
    fingerprint: str,
    claimed_ids: set[str] | None = None,
) -> str | None:
    """Return ledger id if fingerprint matches current or previous fingerprints.

    claimed_ids: set of ids already claimed in this build pass. When two records
    share the same fingerprint, the second call (with the same id already in
    claimed_ids) will skip that id and continue searching, returning None so a
    fresh id gets minted. The caller must pass the same set across all calls for
    a given file/build to get correct deduplication.

    Legacy ledgers (written before the "|"→"\\x00" delimiter switch) store
    fingerprints joined by "|".  Converting the current "\\x00"-joined candidate
    back to the old "|" form reproduces byte-for-byte what the previous extractor
    would have written for the same fields, so unchanged entries still resolve to
    their existing ids on the first build after the upgrade instead of being
    re-minted and tombstoned.  We only ever *store* the "\\x00" form, so this
    cannot reintroduce the delimiter-collision bug the switch fixed.
    """
    legacy_fingerprint = fingerprint.replace("\x00", "|")

    def _is_legacy_match(stored: str | None) -> bool:
        return (
            stored is not None
            and "\x00" not in stored
            and stored == legacy_fingerprint
        )

    for entry_id, meta in ledger.items():
        if claimed_ids is not None and entry_id in claimed_ids:
            continue
        current_fp = meta.get("current_fingerprint")
        if current_fp == fingerprint or _is_legacy_match(current_fp):
            if claimed_ids is not None:
                claimed_ids.add(entry_id)
            return entry_id
        previous = meta.get("previous_fingerprints", [])
        if fingerprint in previous or any(_is_legacy_match(p) for p in previous):
            if claimed_ids is not None:
                claimed_ids.add(entry_id)
            return entry_id
    return None


def _find_explicit_id_in_ledger(
    ledger: dict,
    project_id: str,
    source_path: str,
    row_id: str,
    claimed_ids: set[str] | None = None,
) -> str | None:
    """Match on project_id\x00source_path\x00row_id for explicit-id rows.

    claimed_ids: set of ids already claimed in this build pass for the current
    file.  When two headings in the same file share the same explicit token (e.g.
    four headings all tagged ``L72``), each successive call skips ids already in
    claimed_ids so they receive distinct ids rather than collapsing to one.
    """
    key = "\x00".join([project_id, source_path, row_id])
    for entry_id, meta in ledger.items():
        if claimed_ids is not None and entry_id in claimed_ids:
            continue
        fp = meta.get("current_fingerprint", "")
        parts = fp.split("\x00")
        # fingerprint format: project_id\x00source_path\x00anchor\x00kind\x00date
        if len(parts) >= 3 and "\x00".join([parts[0], parts[1]]) == "\x00".join(
            [project_id, source_path]
        ):
            # Use exact match on the anchor field (parts[2]) to avoid the
            # substring-collision bug where multiple headings sharing a token
            # (e.g. three headings each containing "L72") all resolve to the
            # first minted id.  We check both row_id == anchor and the legacy
            # substring path so that existing behaviour is preserved when
            # anchors are unique.
            if row_id == (parts[2] if len(parts) > 2 else ""):
                if claimed_ids is not None:
                    claimed_ids.add(entry_id)
                return entry_id
    # Fallback: substring match (legacy) — also guarded by claimed_ids
    for entry_id, meta in ledger.items():
        if claimed_ids is not None and entry_id in claimed_ids:
            continue
        fp = meta.get("current_fingerprint", "")
        parts = fp.split("\x00")
        if len(parts) >= 3 and "\x00".join([parts[0], parts[1]]) == "\x00".join(
            [project_id, source_path]
        ):
            if row_id in (parts[2] if len(parts) > 2 else ""):
                if claimed_ids is not None:
                    claimed_ids.add(entry_id)
                return entry_id
    # Fallback: check previous_fingerprints too
    for entry_id, meta in ledger.items():
        if claimed_ids is not None and entry_id in claimed_ids:
            continue
        for pfp in meta.get("previous_fingerprints", []):
            parts = pfp.split("\x00")
            if len(parts) >= 3 and "\x00".join(parts[:3]) == key:
                if claimed_ids is not None:
                    claimed_ids.add(entry_id)
                return entry_id
    return None


# ---------------------------------------------------------------------------
# File walkers
# ---------------------------------------------------------------------------


def _should_exclude_file(rel_to_living: Path) -> bool:
    """Return True if this path (relative to .living/) should be skipped."""
    parts = rel_to_living.parts

    # Exclude subtrees at top level (e.g. generated-conventions)
    if parts and parts[0] in _EXCLUDED_SUBTREES:
        return True

    # Exclude generated-conventions subtree anywhere (belt-and-suspenders)
    if "generated-conventions" in parts:
        return True

    # Exclude known basenames at ANY depth (R4)
    if rel_to_living.name in _EXCLUDED_FILENAMES:
        return True

    # Exclude *_MANIFEST.md anywhere (R4)
    if rel_to_living.name.endswith("_MANIFEST.md"):
        return True

    return False


def _living_files(
    living_dir: Path,
) -> Generator[tuple[Path, str, EntryKind, SourceShape], None, None]:
    """
    Yield (abs_path, rel_to_living_posix, kind, source_shape) for all included files.
    """
    if not living_dir.is_dir():
        return

    for fpath in sorted(living_dir.rglob("*.md")):
        rel = fpath.relative_to(living_dir)

        # R7: skip any path containing a nested .living segment
        if ".living" in rel.parts:
            continue

        if _should_exclude_file(rel):
            continue

        parts = rel.parts

        # R-logs: skip files under a log/ subtree — extract_logs.py handles them
        if parts and parts[0] == "log":
            continue

        # Top-level files
        if len(parts) == 1:
            fname = rel.name

            # R5b: conventions.md → convention parser (first-class)
            if fname == "conventions.md":
                yield (
                    fpath,
                    rel.as_posix(),
                    EntryKind.convention,
                    SourceShape.aggregate_section,
                )
                continue

            if fname in _AGGREGATE_FILENAMES:
                yield (
                    fpath,
                    rel.as_posix(),
                    _AGGREGATE_FILENAMES[fname],
                    SourceShape.aggregate_section,
                )
            # Other top-level files not in the aggregate list are skipped
            continue

        # Subtree files: check for conventions.md nested under a subtree too
        if rel.name == "conventions.md":
            yield (
                fpath,
                rel.as_posix(),
                EntryKind.convention,
                SourceShape.aggregate_section,
            )
            continue

        subtree = parts[0]
        if subtree == "learnings":
            yield fpath, rel.as_posix(), EntryKind.learning, SourceShape.per_entry_file
        elif subtree == "findings":
            yield (
                fpath,
                rel.as_posix(),
                EntryKind.finding,
                SourceShape.standalone_finding_file,
            )
        elif subtree == "decisions":
            yield fpath, rel.as_posix(), EntryKind.decision, SourceShape.per_entry_file
        # Other subtrees are ignored


# ---------------------------------------------------------------------------
# Entry parsers
# ---------------------------------------------------------------------------


def _parse_aggregate_sections(
    fpath: Path,
    source_path: str,
    kind: EntryKind,
) -> Generator[dict, None, None]:
    """
    Parse an aggregate_section file line-by-line, yielding raw entry dicts.

    Each dict has keys: heading_text, body_lines, line_start, line_end
    """
    lines: list[str] = []
    try:
        with fpath.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return

    # Strip trailing newline chars but keep track of 1-based line numbers
    total = len(lines)

    # State machine
    current_heading: str | None = None
    current_start: int = 0
    current_body: list[str] = []

    def flush(end_line: int) -> dict | None:
        if current_heading is None:
            return None
        return {
            "heading_text": current_heading,
            "body_lines": list(current_body),
            "line_start": current_start,
            "line_end": end_line,
        }

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        m = _HEADING_RE.match(line)
        if m:
            heading_text = m.group(2).strip()
            if _is_entry_signature(heading_text):
                # Flush previous entry
                pending = flush(lineno - 1)
                if pending:
                    yield pending
                current_heading = heading_text
                current_start = lineno
                current_body = []
            else:
                # Sub-section heading — add to current body
                if current_heading is not None:
                    current_body.append(line)
        else:
            if current_heading is not None:
                current_body.append(line)

    # Flush last entry
    pending = flush(total)
    if pending:
        yield pending


def _parse_whole_file(
    fpath: Path,
) -> dict:
    """
    Parse a per_entry_file or standalone_finding_file as a single entry.
    Returns dict with: heading_text, body_lines, line_start, line_end, frontmatter_date
    """
    lines: list[str] = []
    try:
        with fpath.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        return {
            "heading_text": fpath.stem,
            "body_lines": [],
            "line_start": 1,
            "line_end": 0,
            "frontmatter_date": None,
        }

    total = len(lines)
    stripped = [l.rstrip("\r\n") for l in lines]

    # Detect YAML frontmatter
    frontmatter_date: str | None = None
    body_start = 0
    if stripped and stripped[0].strip() == "---":
        for i, l in enumerate(stripped[1:], start=1):
            if l.strip() == "---":
                # Parse frontmatter lines for date
                for fm_line in stripped[1:i]:
                    m = _YAML_DATE_RE.match(fm_line)
                    if m:
                        frontmatter_date = m.group(1)
                body_start = i + 1
                break

    # Find first heading
    heading_text = fpath.stem
    for l in stripped[body_start:]:
        m = _HEADING_RE.match(l)
        if m:
            heading_text = m.group(2).strip()
            break

    return {
        "heading_text": heading_text,
        "body_lines": stripped,
        "line_start": 1,
        "line_end": total,
        "frontmatter_date": frontmatter_date,
    }


# ---------------------------------------------------------------------------
# Convention parser (R5b)
# ---------------------------------------------------------------------------


def _parse_conventions(
    fpath: Path,
) -> Generator[dict, None, None]:
    """
    Parse conventions.md section-wise into raw entry dicts.

    Strategy (in order):
      1. ## headings → one entry per ## (body includes nested ### subsections)
      2. ### headings (if no ## found)
      3. Whole file as single entry
    """
    lines: list[str] = []
    try:
        with fpath.open(encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        yield {
            "heading_text": fpath.stem,
            "body_lines": [],
            "line_start": 1,
            "line_end": 0,
        }
        return

    stripped = [l.rstrip("\r\n") for l in lines]
    total = len(stripped)

    def _sections_at_level(level: int) -> list[dict]:
        """Split file at exactly `level` '#' headings."""
        sections: list[dict] = []
        current_heading: str | None = None
        current_start: int = 0
        current_body: list[str] = []
        prefix = "#" * level + " "

        def flush(end: int) -> None:
            if current_heading is None:
                return
            sections.append(
                {
                    "heading_text": current_heading,
                    "body_lines": list(current_body),
                    "line_start": current_start,
                    "line_end": end,
                }
            )

        for lineno, line in enumerate(stripped, start=1):
            m = _HEADING_RE.match(line)
            if m and len(m.group(1)) == level:
                flush(lineno - 1)
                current_heading = m.group(2).strip()
                current_start = lineno
                current_body = []
            else:
                if current_heading is not None:
                    current_body.append(line)

        flush(total)
        return sections

    # Try ## level first
    sections = _sections_at_level(2)
    if not sections:
        # Fall back to ### level
        sections = _sections_at_level(3)
    if not sections:
        # Whole-file fallback
        yield {
            "heading_text": fpath.stem,
            "body_lines": stripped,
            "line_start": 1,
            "line_end": total,
        }
        return

    for sec in sections:
        if _is_template(sec["heading_text"], sec["body_lines"]):
            continue
        yield sec


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_entries(
    portfolio_root: Path,
    projects: list[ProjectMeta],
    id_ledger: dict,
) -> ExtractResult:
    """
    Walk .living/ directories for each project and extract Entry objects.

    Parameters
    ----------
    portfolio_root:
        Absolute path to the portfolio root directory.
    projects:
        List of ProjectMeta objects. Only those with has_living=True are walked.
    id_ledger:
        Current entry-ids.json payload (may be empty on cold build).
        Will be updated in-place and returned as ExtractResult.ledger.

    Returns
    -------
    ExtractResult with entries sorted by (project_id, source_path, id).
    """
    raw_entries: list[tuple[str, str, Entry]] = []  # (project_id, source_path, entry)
    facets: dict[str, Facet] = {}
    report: list[str] = []

    # Working ledger — we'll update it as we go
    working_ledger: dict = {k: dict(v) for k, v in id_ledger.items()}
    # Track which ids we see this run (for tombstoning)
    seen_ids: set[str] = set()
    # Counter for minting new ids — find current max
    existing_nums = [
        int(eid.split("-")[1]) for eid in working_ledger if re.match(r"e-\d+$", eid)
    ]
    id_counter = [max(existing_nums) if existing_nums else 0]

    # We collect all raw records first (with fingerprint), sort, then mint ids
    # to ensure determinism
    pending_records: list[dict] = []

    for project in projects:
        if not project.has_living:
            continue

        project_dir = portfolio_root / project.path
        living_dir = project_dir / ".living"

        if not living_dir.is_dir():
            report.append(
                f"WARNING: .living/ not found for project {project.id} at {living_dir}"
            )
            continue

        for fpath, rel_living, kind, source_shape in _living_files(living_dir):
            abs_source_path = _portfolio_rel(fpath, portfolio_root)

            if (
                source_shape == SourceShape.aggregate_section
                and kind == EntryKind.convention
            ):
                # R5b: conventions.md → section-wise convention entries
                for raw in _parse_conventions(fpath):
                    heading_text = raw["heading_text"]
                    body_lines = raw["body_lines"]
                    # _parse_conventions already applies template skip; re-check for safety
                    if _is_template(heading_text, body_lines):
                        report.append(
                            f"SKIP template/placeholder convention: {heading_text!r} in {abs_source_path}"
                        )
                        continue

                    title = heading_text.strip()
                    tags = _parse_tags(body_lines)
                    excerpt = _body_excerpt(body_lines)
                    chash = _content_hash(body_lines)
                    anchor = heading_text
                    mit, fstatus, src_proj = _parse_signal_fields(body_lines)

                    fingerprint = "\x00".join(
                        [project.id, abs_source_path, anchor, kind.value, "None"]
                    )

                    pending_records.append(
                        {
                            "fingerprint": fingerprint,
                            "project_id": project.id,
                            "family": project.family,
                            "kind": kind,
                            "source_shape": source_shape,
                            "source_path": abs_source_path,
                            "anchor": anchor,
                            "line_start": raw["line_start"],
                            "line_end": raw["line_end"],
                            "title": title,
                            "date": None,
                            "tags": tags,
                            "body_excerpt": excerpt,
                            "content_hash": chash,
                            "heading_text": heading_text,
                            "mitigation_type": mit,
                            "finding_status": fstatus,
                            "source_project": src_proj,
                        }
                    )

            elif source_shape == SourceShape.aggregate_section:
                for raw in _parse_aggregate_sections(fpath, abs_source_path, kind):
                    heading_text = raw["heading_text"]
                    body_lines = raw["body_lines"]

                    if _is_template(heading_text, body_lines):
                        report.append(
                            f"SKIP template/placeholder section: {heading_text!r} in {abs_source_path}"
                        )
                        continue

                    date = _extract_date(heading_text)
                    title = _clean_title(heading_text)
                    tags = _parse_tags(body_lines)
                    excerpt = _body_excerpt(body_lines)
                    chash = _content_hash(body_lines)
                    anchor = heading_text
                    mit, fstatus, src_proj = _parse_signal_fields(body_lines)

                    fingerprint = "\x00".join(
                        [project.id, abs_source_path, anchor, kind.value, str(date)]
                    )

                    pending_records.append(
                        {
                            "fingerprint": fingerprint,
                            "project_id": project.id,
                            "family": project.family,
                            "kind": kind,
                            "source_shape": source_shape,
                            "source_path": abs_source_path,
                            "anchor": anchor,
                            "line_start": raw["line_start"],
                            "line_end": raw["line_end"],
                            "title": title,
                            "date": date,
                            "tags": tags,
                            "body_excerpt": excerpt,
                            "content_hash": chash,
                            "heading_text": heading_text,
                            "mitigation_type": mit,
                            "finding_status": fstatus,
                            "source_project": src_proj,
                        }
                    )

            else:
                # per_entry_file or standalone_finding_file
                raw = _parse_whole_file(fpath)
                heading_text = raw["heading_text"]
                body_lines = raw["body_lines"]

                if _is_template(heading_text, body_lines):
                    report.append(f"SKIP template/placeholder file: {fpath.name}")
                    continue

                date = raw.get("frontmatter_date") or _extract_date(heading_text)
                title = _clean_title(heading_text)
                tags = _parse_tags(body_lines)
                excerpt = _body_excerpt(body_lines)
                chash = _content_hash(body_lines)
                anchor = fpath.stem
                mit, fstatus, src_proj = _parse_signal_fields(body_lines)

                fingerprint = "\x00".join(
                    [project.id, abs_source_path, anchor, kind.value, str(date)]
                )

                pending_records.append(
                    {
                        "fingerprint": fingerprint,
                        "project_id": project.id,
                        "family": project.family,
                        "kind": kind,
                        "source_shape": source_shape,
                        "source_path": abs_source_path,
                        "anchor": anchor,
                        "line_start": raw["line_start"],
                        "line_end": raw["line_end"],
                        "title": title,
                        "date": date,
                        "tags": tags,
                        "body_excerpt": excerpt,
                        "content_hash": chash,
                        "heading_text": heading_text,
                        "mitigation_type": mit,
                        "finding_status": fstatus,
                        "source_project": src_proj,
                    }
                )

    # Sort pending records for deterministic id minting
    pending_records.sort(
        key=lambda r: (r["project_id"], r["source_path"], r["fingerprint"])
    )

    # Track duplicate fingerprints within this run for ordinal suffixing
    fp_seen_count: dict[str, int] = {}

    # Per-file claimed_ids set for duplicate-anchor deduplication (Change 4)
    _current_file_key: tuple[str, str] | None = None
    _file_claimed_ids: set[str] = set()

    for rec in pending_records:
        fingerprint = rec["fingerprint"]
        heading_text = rec["heading_text"]
        kind = rec["kind"]
        project_id = rec["project_id"]
        source_path = rec["source_path"]

        # Reset claimed_ids when we move to a new file
        file_key = (project_id, source_path)
        if file_key != _current_file_key:
            _current_file_key = file_key
            _file_claimed_ids = set()

        # Resolve or mint id
        # NOTE: _file_claimed_ids is reset at each (project_id, source_path)
        # boundary (see reset logic above).  pending_records is sorted by
        # (project_id, source_path, fingerprint), which guarantees all records
        # for a given file are contiguous, so the per-file reset is correct.
        #
        # ALL three paths below MUST call _file_claimed_ids.add(entry_id) so
        # that no id can be issued twice within the same file.

        # 1. Check for explicit id in heading
        explicit_match = _EXPLICIT_ID_RE.search(heading_text)
        entry_id: str | None = None
        if explicit_match:
            row_id = explicit_match.group(0)
            # Pass _file_claimed_ids so duplicate explicit tokens (e.g. four
            # headings tagged L72) each receive a distinct id (Bug 2 fix).
            # _find_explicit_id_in_ledger adds the returned id to claimed_ids.
            entry_id = _find_explicit_id_in_ledger(
                working_ledger, project_id, source_path, row_id, _file_claimed_ids
            )

        # 2. Check fingerprint match
        if entry_id is None:
            # _find_in_ledger already adds the returned id to _file_claimed_ids.
            entry_id = _find_in_ledger(working_ledger, fingerprint, _file_claimed_ids)

        # 3. Mint new id
        if entry_id is None:
            # TODO(rev3 §4.4): fuzzy Jaccard one-to-one rename detection goes here.
            # For now we always mint a new id for unmatched fingerprints.
            base_id = _mint_id(id_counter)

            # Handle duplicate fingerprints with ordinal suffix
            fp_count = fp_seen_count.get(fingerprint, 0)
            fp_seen_count[fingerprint] = fp_count + 1
            if fp_count > 0:
                entry_id = f"{base_id}-{fp_count + 1}"
            else:
                entry_id = base_id
            # Bug 1 fix: register the newly-minted id so that a second record
            # in the same file with the same fingerprint cannot claim it again
            # via _find_in_ledger (which would see it in working_ledger below).
            _file_claimed_ids.add(entry_id)
        else:
            # Track duplication for existing ids too
            fp_count = fp_seen_count.get(fingerprint, 0)
            fp_seen_count[fingerprint] = fp_count + 1

        seen_ids.add(entry_id)

        # Update ledger
        working_ledger[entry_id] = {
            "current_fingerprint": fingerprint,
            "previous_fingerprints": working_ledger.get(entry_id, {}).get(
                "previous_fingerprints", []
            ),
            "content_hash": rec["content_hash"],
            "status": EntryStatus.active.value,
        }

        # Infer stage facet
        stage, stage_source = _infer_stage(source_path, rec["title"], rec["tags"])
        facet = Facet(stage=stage, stage_source=stage_source)

        entry = Entry(
            id=entry_id,
            kind=kind,
            source_shape=rec["source_shape"],
            project_id=project_id,
            family=rec["family"],
            source_path=source_path,
            anchor=rec["anchor"],
            line_start=rec["line_start"],
            line_end=rec["line_end"],
            title=rec["title"],
            date=rec["date"],
            tags=rec["tags"],
            body_excerpt=rec["body_excerpt"],
            content_hash=rec["content_hash"],
            status=EntryStatus.active,
            mitigation_type=rec.get("mitigation_type"),
            finding_status=rec.get("finding_status"),
            source_project=rec.get("source_project"),
        )

        raw_entries.append((project_id, source_path, entry))
        facets[entry_id] = facet

    # Tombstone ids not seen this run
    for entry_id, meta in working_ledger.items():
        if (
            entry_id not in seen_ids
            and meta.get("status") != EntryStatus.tombstone.value
        ):
            working_ledger[entry_id] = {
                **meta,
                "status": EntryStatus.tombstone.value,
            }

    # Sort entries by (project_id, source_path, id)
    raw_entries.sort(key=lambda t: (t[0], t[1], t[2].id))
    entries = [e for _, _, e in raw_entries]

    return ExtractResult(
        entries=entries,
        facets=facets,
        ledger=working_ledger,
        report=report,
    )
