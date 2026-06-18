"""
link_logs.py — Chain episodic LogNodes into follows and created_in edges.

Log notes connect to their project hub (via a wikilink written by build_vault)
and to the previous session in the same project (follows chain).  When entries
are provided, created_in edges are emitted that link each log to entries in the
same project that share the same session date.  The old mentions (log→concept)
edges have been removed: logs were flooding the concept graph with low-signal
connections, and build_vault no longer writes concept wikilinks in log notes.

Implements the log edge-linking step of the knowledge-map pipeline.
Pure function: no I/O, no mutation of input objects.
Python 3.13+, stdlib only (no third-party imports).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph_model import (
    Edge,
    EdgeType,
    Entry,
    LogNode,
    Provenance,
)
from concept_registry import Registry


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class LinkLogsResult:
    edges: list[Edge]
    report: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _null_safe_log_sort_key(log: LogNode) -> tuple:
    """
    Null-safe sort key for ordering logs within a project.

    Order: (date_missing, date, seq_missing, seq, source_path)
    Logs with None session_date sort last; logs with None session_seq sort last
    within the same date group.
    """
    return (
        log.session_date is None,
        log.session_date or "",
        log.session_seq is None,
        log.session_seq if log.session_seq is not None else -1,
        log.source_path,
    )


def _normalize_date(date_val: str | None) -> str | None:
    """
    Normalize a date value to a YYYY-MM-DD string, or return None.

    Entry.date and LogNode.session_date are both typed as ``str | None``
    in graph_model.py and are stored as "YYYY-MM-DD" strings when present.
    This helper strips whitespace and returns None for empty/None values.
    """
    if date_val is None:
        return None
    normalized = str(date_val).strip()
    return normalized if normalized else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def link_logs(
    logs: list[LogNode],
    registry: Registry,
    entries: list[Entry] | None = None,
) -> LinkLogsResult:
    """
    Chain LogNodes into follows edges (chronological predecessor within project)
    and optionally emit created_in edges (log → entry, same project + same date).

    Mentions edges (log→concept) are intentionally not generated.  Log notes
    link to concepts only through the project hub, keeping the episodic tier
    structurally clean.

    Args:
        logs: List of LogNode objects to link.
        registry: Loaded concept Registry (accepted for API compatibility; unused).
        entries: Optional list of Entry objects.  When provided, created_in edges
            are emitted for every (log, entry) pair where both belong to the same
            project and share the same normalized session date.  Logs without a
            session_date produce no created_in edges.

    Returns:
        LinkLogsResult with follows + created_in edges sorted deterministically,
        and a report list.
    """
    report: list[str] = []

    # ------------------------------------------------------------------
    # follows edges (log → previous log in same project)
    # ------------------------------------------------------------------

    # Group logs by project_id
    by_project: dict[str, list[LogNode]] = defaultdict(list)
    for log in logs:
        by_project[log.project_id].append(log)

    follows_edges: list[Edge] = []

    for _project_id, project_logs in by_project.items():
        # Sort within project using null-safe key
        sorted_logs = sorted(project_logs, key=_null_safe_log_sort_key)

        for i in range(1, len(sorted_logs)):
            curr = sorted_logs[i]
            prev = sorted_logs[i - 1]
            follows_edges.append(
                Edge(
                    from_id=curr.id,
                    to_id=prev.id,
                    type=EdgeType.follows,
                    provenance=Provenance.auto,
                    trigger=None,
                    confidence=None,
                )
            )

    # Sort follows edges by from_id for determinism
    follows_edges.sort(key=lambda e: e.from_id)

    # ------------------------------------------------------------------
    # created_in edges (log → entry, same project + same session date)
    # ------------------------------------------------------------------

    created_in_edges: list[Edge] = []

    if entries is not None:
        # Group active entries by (project_id, normalized_date)
        entries_by_project_date: dict[tuple[str, str], list[Entry]] = defaultdict(list)
        for entry in entries:
            nd = _normalize_date(entry.date)
            if nd is not None:
                entries_by_project_date[(entry.project_id, nd)].append(entry)

        for log in logs:
            log_date = _normalize_date(log.session_date)
            if log_date is None:
                # No session_date → no created_in edges for this log
                continue

            key = (log.project_id, log_date)
            matched_entries = entries_by_project_date.get(key, [])

            for entry in sorted(matched_entries, key=lambda e: e.id):
                created_in_edges.append(
                    Edge(
                        from_id=log.id,
                        to_id=entry.id,
                        type=EdgeType.created_in,
                        provenance=Provenance.auto,
                        trigger="session-date",
                        confidence=None,
                    )
                )

        # Sort created_in edges deterministically: (from_id, to_id)
        created_in_edges.sort(key=lambda e: (e.from_id, e.to_id))

    # ------------------------------------------------------------------
    # Assemble result
    # ------------------------------------------------------------------

    all_edges = follows_edges + created_in_edges

    n_follows = len(follows_edges)
    n_created_in = len(created_in_edges)
    n_total = len(all_edges)
    report.append(
        f"link_logs: 0 mentions edges (removed), "
        f"{n_follows} follows edges, "
        f"{n_created_in} created_in edges, "
        f"{n_total} total"
    )

    return LinkLogsResult(edges=all_edges, report=report)
