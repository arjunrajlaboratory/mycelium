"""
extract_reviews.py — Walk .living/outputs/reviews/ and .living/outputs/knowledge-transfers/
directories and extract SupportingNode objects (reviews, findings, transfers, transfer items).

Phase: supporting tier (reviews + transfers).
Python 3.11+, stdlib only.

Contract:
  - No ledger — IDs are deterministic slug-based
  - Returns (supporting_nodes, edges, report)
  - Tolerant parsing — malformed files emit a report note and continue
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from graph_model import (
    Edge,
    EdgeType,
    ProjectMeta,
    Provenance,
    SupportingKind,
    SupportingNode,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Excerpt cap (chars)
_EXCERPT_CAP = 2000

# Heading pattern
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")

# Date from filename: YYYY-MM-DD-...
_DATE_FROM_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Date from heading: ## [YYYY-MM-DD]
_DATE_FROM_HEADING_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")

# Finding markers in headings (F\d+, or severity token, or [date] title)
_FINDING_HEADING_RE = re.compile(
    r"F\d+|"
    r"\[(critical|major|minor|moderate|nit)\]|"
    r"\[\d{4}-\d{2}-\d{2}\]",
    re.IGNORECASE,
)

# Severity token in brackets
_SEVERITY_RE = re.compile(
    r"\[(critical|major|minor|moderate|nit)\]",
    re.IGNORECASE,
)

# Projects scanned line in transfer
_PROJECTS_SCANNED_RE = re.compile(
    r"^\*{0,2}Projects\s+scanned\*{0,2}:\s*(.+)",
    re.IGNORECASE,
)

# Transfer item: ### <n>. <title>
_TRANSFER_ITEM_RE = re.compile(r"^#{2,4}\s+(\d+)\.\s+(.*)")


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ExtractReviewsResult:
    supporting_nodes: list[SupportingNode]
    edges: list[Edge]
    report: list[str]


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------


def _slugify(s: str) -> str:
    """Lowercase, replace non-alphanumeric with hyphens, strip leading/trailing hyphens."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


# ---------------------------------------------------------------------------
# Project name → id resolution
# ---------------------------------------------------------------------------


def _build_name_index(projects: list[ProjectMeta]) -> dict[str, str]:
    """Build a map from name/slug variants → project id for fast lookup."""
    index: dict[str, str] = {}
    for p in projects:
        # Exact name
        index[p.name.strip()] = p.id
        # Lowercased name
        index[p.name.strip().lower()] = p.id
        # Slug of name
        index[_slugify(p.name)] = p.id
        # project id itself
        index[p.id] = p.id
        # path slug
        index[_slugify(p.path)] = p.id
    return index


def _resolve_project_names(
    raw_names: list[str],
    name_index: dict[str, str],
    report: list[str],
    source_path: str,
) -> list[str]:
    """Resolve a list of project names to project ids; note unresolved in report."""
    resolved: list[str] = []
    seen: set[str] = set()
    for name in raw_names:
        name = name.strip()
        if not name:
            continue
        pid = (
            name_index.get(name)
            or name_index.get(name.lower())
            or name_index.get(_slugify(name))
        )
        if pid is None:
            report.append(
                f"UNRESOLVED PROJECT ({source_path}): could not resolve project name {name!r}"
            )
        elif pid not in seen:
            resolved.append(pid)
            seen.add(pid)
    return resolved


# ---------------------------------------------------------------------------
# Portfolio-relative path helper
# ---------------------------------------------------------------------------


def _portfolio_rel(path: Path, portfolio_root: Path) -> str:
    """Return portfolio-relative posix path."""
    try:
        return path.relative_to(portfolio_root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Review parsing
# ---------------------------------------------------------------------------


def _parse_date(filename_stem: str, lines: list[str]) -> str | None:
    """Extract date from filename YYYY-MM-DD-... or from a ## [YYYY-MM-DD] heading."""
    m = _DATE_FROM_FILENAME_RE.match(filename_stem)
    if m:
        return m.group(1)
    for line in lines:
        m2 = _DATE_FROM_HEADING_RE.search(line)
        if m2:
            return m2.group(1)
    return None


def _parse_review_file(
    fpath: Path,
    project: ProjectMeta,
    portfolio_root: Path,
    name_index: dict[str, str],
    report: list[str],
) -> tuple[list[SupportingNode], list[Edge]]:
    """
    Parse a review .md file into a primary SupportingNode and zero or more finding nodes.
    Returns (nodes, edges).
    """
    try:
        raw = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.append(f"READ ERROR ({fpath}): {exc}")
        return [], []

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.splitlines()
    source_path = _portfolio_rel(fpath, portfolio_root)
    stem = fpath.stem

    # --- title: first H1 ---
    title: str = stem
    for line in lines:
        m = _HEADING_RE.match(line.strip())
        if m and len(m.group(1)) == 1:
            title = m.group(2).strip()
            break

    # --- date ---
    date = _parse_date(stem, lines)

    # --- body_excerpt: Scope/Files reviewed/Sub-agents + exec summary ---
    body_parts: list[str] = []
    in_exec_summary = False
    for line in lines:
        if re.match(
            r"^\*{0,2}(Scope|Files reviewed|Sub-agents)\*{0,2}:",
            line.strip(),
            re.IGNORECASE,
        ):
            body_parts.append(line.strip())
        stripped = line.strip()
        if re.match(r"^#+\s+Executive Summary", stripped, re.IGNORECASE):
            in_exec_summary = True
            continue
        if in_exec_summary:
            if re.match(r"^#+", stripped):
                in_exec_summary = False
            else:
                body_parts.append(line)
    body_excerpt = "\n".join(body_parts)[:_EXCERPT_CAP]

    # --- review id ---
    review_id = f"rv-{project.id}-{_slugify(stem)}"

    # --- findings: parse section after ## Findings ---
    findings: list[tuple[str, str, str]] = []  # (title, severity, body_text)

    in_findings_section = False
    current_finding_lines: list[str] = []
    current_heading: str = ""

    def _flush_finding() -> None:
        """Commit accumulated finding lines."""
        if not (current_heading or current_finding_lines):
            return
        block_text = "\n".join(current_finding_lines)
        # Check if block qualifies as a finding
        qualifies = bool(
            _FINDING_HEADING_RE.search(current_heading)
            or re.search(r"\*\*Fix\*\*:", block_text)
            or re.search(r"\*\*Why it matters\*\*:", block_text)
        )
        if not qualifies:
            return
        # Severity
        sev_m = _SEVERITY_RE.search(current_heading + "\n" + block_text)
        severity = sev_m.group(1).lower() if sev_m else "unknown"
        # Title: strip severity token from heading
        clean_title = _SEVERITY_RE.sub("", current_heading).strip(" -—[]")
        findings.append((clean_title, severity, block_text))

    for line in lines:
        stripped = line.strip()
        hm = _HEADING_RE.match(stripped)

        if not in_findings_section:
            if hm and re.match(
                r"^#{1,3}\s+(Findings|Finding)\s*$", stripped, re.IGNORECASE
            ):
                in_findings_section = True
            continue

        # Inside findings section
        if hm:
            level = len(hm.group(1))
            heading_text = hm.group(2).strip()

            # A top-level section heading (## or #) that is NOT a finding ends the section
            if level <= 2 and not _FINDING_HEADING_RE.search(heading_text):
                # Check if it could be a finding by Fix/Why markers in accumulated
                block_text = "\n".join(current_finding_lines)
                if current_heading and (
                    re.search(r"\*\*Fix\*\*:", block_text)
                    or re.search(r"\*\*Why it matters\*\*:", block_text)
                    or _FINDING_HEADING_RE.search(current_heading)
                ):
                    _flush_finding()
                current_heading = ""
                current_finding_lines = []
                # If this is a new top-level section (##), end findings; otherwise continue
                if level <= 1:
                    in_findings_section = False
                    continue
                # level 2 non-finding heading ends section
                in_findings_section = False
                continue

            # Flush previous finding
            _flush_finding()
            current_heading = heading_text
            current_finding_lines = []
        else:
            current_finding_lines.append(line)

    # Flush last pending finding
    _flush_finding()

    # --- build nodes and edges ---
    nodes: list[SupportingNode] = []
    edges: list[Edge] = []

    # Primary review node
    review_node = SupportingNode(
        id=review_id,
        kind=SupportingKind.review,
        title=title,
        project_id=project.id,
        family=project.family,
        parent_id=None,
        project_ids=[project.id],
        date=date,
        severity=None,
        body_excerpt=body_excerpt,
        source_path=source_path,
    )
    nodes.append(review_node)

    # documents edge: review → project
    edges.append(
        Edge(
            from_id=review_id,
            to_id=project.id,
            type=EdgeType.documents,
            provenance=Provenance.auto,
        )
    )

    if not findings:
        report.append(
            f"NO FINDINGS ({source_path}): zero finding blocks parsed from review; only primary node emitted"
        )
    else:
        for nn, (f_title, f_severity, f_body) in enumerate(findings, start=1):
            finding_id = f"{review_id}-f{nn:02d}"
            finding_node = SupportingNode(
                id=finding_id,
                kind=SupportingKind.finding,
                title=f_title,
                project_id=None,
                family=project.family,
                parent_id=review_id,
                project_ids=[],
                date=date,
                severity=f_severity,
                body_excerpt=f_body[:_EXCERPT_CAP],
                source_path=source_path,
            )
            nodes.append(finding_node)
            edges.append(
                Edge(
                    from_id=finding_id,
                    to_id=review_id,
                    type=EdgeType.detail_of,
                    provenance=Provenance.auto,
                )
            )

    return nodes, edges


# ---------------------------------------------------------------------------
# Transfer parsing
# ---------------------------------------------------------------------------


def _parse_transfer_file(
    fpath: Path,
    project: ProjectMeta,
    portfolio_root: Path,
    name_index: dict[str, str],
    report: list[str],
) -> tuple[list[SupportingNode], list[Edge]]:
    """
    Parse a knowledge-transfer .md file into a primary SupportingNode and item sub-nodes.
    Returns (nodes, edges).
    """
    try:
        raw = fpath.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report.append(f"READ ERROR ({fpath}): {exc}")
        return [], []

    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = raw.splitlines()
    source_path = _portfolio_rel(fpath, portfolio_root)
    stem = fpath.stem

    # --- title: first H1 ---
    title: str = stem
    for line in lines:
        m = _HEADING_RE.match(line.strip())
        if m and len(m.group(1)) == 1:
            title = m.group(2).strip()
            break

    # --- date from filename ---
    date = _parse_date(stem, lines)

    # --- project_ids from "Projects scanned:" line ---
    raw_project_names: list[str] = []
    for line in lines:
        m = _PROJECTS_SCANNED_RE.match(line.strip())
        if m:
            raw_names = re.split(r"[,;]", m.group(1))
            raw_project_names = [n.strip() for n in raw_names if n.strip()]
            break

    project_ids = _resolve_project_names(
        raw_project_names, name_index, report, source_path
    )

    # Ensure the owning project is always in project_ids
    if project.id not in project_ids:
        project_ids.insert(0, project.id)

    # --- body excerpt: metadata lines ---
    body_parts: list[str] = []
    for line in lines:
        if re.match(r"^\*{0,2}Projects\s+scanned\*{0,2}:", line.strip(), re.IGNORECASE):
            body_parts.append(line.strip())
        stripped = line.strip()
        if re.match(r"^#+\s+(Summary|Overview)", stripped, re.IGNORECASE):
            body_parts.append(line)
    body_excerpt = "\n".join(body_parts)[:_EXCERPT_CAP]

    # --- transfer id ---
    transfer_id = f"tx-{project.id}-{_slugify(stem)}"

    # --- items: ### <n>. <title> under ## Transfers Identified ---
    items: list[tuple[int, str]] = []  # (ordinal, title)
    in_transfers_section = False

    for line in lines:
        stripped = line.strip()
        hm = _HEADING_RE.match(stripped)

        if not in_transfers_section:
            if hm and re.match(
                r"^#{1,3}\s+Transfers\s+Identified", stripped, re.IGNORECASE
            ):
                in_transfers_section = True
            continue

        if hm:
            level = len(hm.group(1))
            if level <= 2 and not _TRANSFER_ITEM_RE.match(stripped):
                in_transfers_section = False
                continue
            tim = _TRANSFER_ITEM_RE.match(stripped)
            if tim:
                ordinal = int(tim.group(1))
                item_title = tim.group(2).strip()
                items.append((ordinal, item_title))

    # --- build nodes and edges ---
    nodes: list[SupportingNode] = []
    edges: list[Edge] = []

    transfer_node = SupportingNode(
        id=transfer_id,
        kind=SupportingKind.transfer,
        title=title,
        project_id=project.id,
        family=project.family,
        parent_id=None,
        project_ids=project_ids,
        date=date,
        severity=None,
        body_excerpt=body_excerpt,
        source_path=source_path,
    )
    nodes.append(transfer_node)

    # documents edges: transfer → each project
    for pid in project_ids:
        edges.append(
            Edge(
                from_id=transfer_id,
                to_id=pid,
                type=EdgeType.documents,
                provenance=Provenance.auto,
            )
        )

    for nn, (ordinal, item_title) in enumerate(items, start=1):
        item_id = f"{transfer_id}-i{nn:02d}"
        item_node = SupportingNode(
            id=item_id,
            kind=SupportingKind.transfer_item,
            title=item_title,
            project_id=None,
            family=project.family,
            parent_id=transfer_id,
            project_ids=[],
            date=None,
            severity=None,
            body_excerpt="",
            source_path=source_path,
        )
        nodes.append(item_node)
        edges.append(
            Edge(
                from_id=item_id,
                to_id=transfer_id,
                type=EdgeType.detail_of,
                provenance=Provenance.auto,
            )
        )

    return nodes, edges


# ---------------------------------------------------------------------------
# Main extraction function
# ---------------------------------------------------------------------------


def extract_reviews(
    portfolio_root: Path,
    projects: list[ProjectMeta],
) -> ExtractReviewsResult:
    """
    Walk .living/outputs/reviews/*.md and .living/outputs/knowledge-transfers/*.md
    for each project, plus the portfolio-level equivalents.

    Parameters
    ----------
    portfolio_root:
        Absolute path to the portfolio root directory.
    projects:
        List of ProjectMeta objects.

    Returns
    -------
    ExtractReviewsResult with supporting_nodes and edges sorted deterministically.
    """
    report: list[str] = []
    name_index = _build_name_index(projects)

    all_nodes: list[SupportingNode] = []
    all_edges: list[Edge] = []

    # Directories to walk: per-project + portfolio-level
    # For portfolio-level, use a synthetic "portfolio" project rooted at portfolio_root
    walk_targets: list[tuple[Path, ProjectMeta]] = []

    for project in projects:
        project_dir = portfolio_root / project.path
        walk_targets.append((project_dir, project))

    # Portfolio-level .living (rooted at portfolio_root itself)
    # Create a synthetic ProjectMeta for the portfolio root if .living exists
    portfolio_living = portfolio_root / ".living"
    if portfolio_living.is_dir():
        # Check if any project already covers portfolio_root (path == ".")
        portfolio_project_ids = {p.id for p in projects if p.path in (".", "")}
        if not portfolio_project_ids:
            # Create a synthetic portfolio-level project
            portfolio_meta = ProjectMeta(
                id="portfolio",
                name="Portfolio",
                path=".",
                family="portfolio",
                has_living=True,
            )
            walk_targets.append((portfolio_root, portfolio_meta))

    # Process each target
    for base_dir, project in walk_targets:
        for subdir_name, is_review in (
            ("reviews", True),
            ("knowledge-transfers", False),
        ):
            output_dir = base_dir / ".living" / "outputs" / subdir_name
            if not output_dir.is_dir():
                continue

            for fpath in sorted(output_dir.glob("*.md")):
                # Skip .pdf (shouldn't occur via glob("*.md") but be safe)
                if fpath.suffix.lower() == ".pdf":
                    continue

                if is_review:
                    nodes, edges = _parse_review_file(
                        fpath, project, portfolio_root, name_index, report
                    )
                else:
                    nodes, edges = _parse_transfer_file(
                        fpath, project, portfolio_root, name_index, report
                    )

                all_nodes.extend(nodes)
                all_edges.extend(edges)

    # Sort deterministically
    all_nodes.sort(key=lambda n: n.id)
    all_edges.sort(key=lambda e: (e.from_id, e.to_id, e.type.value))

    return ExtractReviewsResult(
        supporting_nodes=all_nodes,
        edges=all_edges,
        report=report,
    )
