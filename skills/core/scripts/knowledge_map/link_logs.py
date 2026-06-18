"""
link_logs.py — Chain episodic LogNodes into follows edges only.

Log notes connect to their project hub (via a wikilink written by build_vault)
and to the previous session in the same project (follows chain).  The old
mentions (log→concept) edges have been removed: logs were flooding the concept
graph with low-signal connections, and build_vault no longer writes concept
wikilinks in log notes anyway.

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def link_logs(logs: list[LogNode], registry: Registry) -> LinkLogsResult:
    """
    Chain LogNodes into follows edges (chronological predecessor within project).

    Mentions edges (log→concept) are intentionally not generated.  Log notes
    link to concepts only through the project hub, keeping the episodic tier
    structurally clean.

    Args:
        logs: List of LogNode objects to link.
        registry: Loaded concept Registry (accepted for API compatibility; unused).

    Returns:
        LinkLogsResult with follows edges sorted by from_id and a report list.
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
    # Assemble result
    # ------------------------------------------------------------------

    n_follows = len(follows_edges)
    report.append(
        f"link_logs: 0 mentions edges (removed), "
        f"{n_follows} follows edges, {n_follows} total"
    )

    return LinkLogsResult(edges=follows_edges, report=report)
