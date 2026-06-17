"""
link_logs.py — Auto-link episodic LogNodes to concept nodes and chain follows edges.

Implements the log edge-linking step of the knowledge-map pipeline.
Pure function: no I/O, no mutation of input objects.
Python 3.13+, stdlib only (no third-party imports).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from graph_model import (
    MASS_LINK_THRESHOLD,
    Edge,
    EdgeType,
    LogNode,
    MatchMode,
    Provenance,
    confidence_for,
    normalize_text,
)
from concept_registry import Registry

# Reuse whole-word matcher and _find_matches from link_entries (single source of truth).
from link_entries import _find_matches


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
    Auto-link LogNodes to concept nodes (mentions edges) and chain follows edges.

    Args:
        logs: List of LogNode objects to link.
        registry: Loaded concept Registry.

    Returns:
        LinkLogsResult with sorted edges (mentions block then follows block) and
        a report list of strings (mass-link warnings + summary counts).
    """
    report: list[str] = []

    # ------------------------------------------------------------------
    # Step 1: mentions edges (log → concept)
    # ------------------------------------------------------------------

    # Pre-normalize log text once per log
    log_text: dict[str, str] = {}
    for log in logs:
        combined = log.title + "\n" + log.body_excerpt
        log_text[log.id] = normalize_text(combined)

    # Track trigger → edge count for mass-link guard
    trigger_counts: dict[str, int] = defaultdict(int)

    # (log_id, concept_slug) → Edge  — deduplication map
    mentions_map: dict[tuple[str, str], Edge] = {}

    for log in logs:
        text = log_text[log.id]

        for concept in registry.concepts:
            # --- Negative-keyword veto ---
            if concept.negative_keywords:
                neg_matches = _find_matches(concept.negative_keywords, text)
                if neg_matches:
                    continue

            # NOTE: No required_any gate for logs (loosely associated per spec).

            mode = concept.match_mode

            # --- Active terms by match_mode ---
            matched_aliases: list[str] = []
            matched_positives: list[str] = []

            if mode in (MatchMode.alias, MatchMode.hybrid):
                matched_aliases = _find_matches(concept.aliases, text)

            if mode in (MatchMode.keyword, MatchMode.hybrid):
                matched_positives = _find_matches(concept.positive_keywords, text)

            # --- Trigger + confidence (alias > keyword, lexicographic tie) ---
            trigger: str | None = None
            confidence: str | None = None

            if matched_aliases:
                trigger = matched_aliases[0]  # lex smallest alias
                confidence = confidence_for("alias")
            elif matched_positives:
                trigger = matched_positives[0]  # lex smallest keyword
                confidence = confidence_for("positive")
            else:
                continue  # nothing matched

            pair = (log.id, concept.slug)
            if pair in mentions_map:
                continue  # dedup: keep first (highest-precedence) edge

            edge = Edge(
                from_id=log.id,
                to_id=concept.slug,
                type=EdgeType.mentions,
                provenance=Provenance.auto,
                trigger=trigger,
                confidence=confidence,
            )
            mentions_map[pair] = edge
            trigger_counts[trigger] += 1

    # --- Mass-link guard ---
    for term, count in trigger_counts.items():
        if count > MASS_LINK_THRESHOLD:
            report.append(
                f"MASS-LINK WARNING: trigger {term!r} produced {count} mentions edges "
                f"(> {MASS_LINK_THRESHOLD} threshold)"
            )

    # Sort mentions edges by (from_id, to_id, trigger)
    mentions_edges: list[Edge] = sorted(
        mentions_map.values(),
        key=lambda e: (e.from_id, e.to_id, e.trigger or ""),
    )

    # ------------------------------------------------------------------
    # Step 2: follows edges (log → previous log in same project)
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
    # Step 3: Assemble result
    # ------------------------------------------------------------------

    all_edges = mentions_edges + follows_edges

    n_mentions = len(mentions_edges)
    n_follows = len(follows_edges)
    n_concepts = len({e.to_id for e in mentions_edges})
    report.append(
        f"link_logs: {n_mentions} mentions edges ({n_concepts} distinct concepts), "
        f"{n_follows} follows edges, {len(all_edges)} total"
    )

    return LinkLogsResult(edges=all_edges, report=report)
