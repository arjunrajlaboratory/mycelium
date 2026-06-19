"""
link_entries.py — Auto-link knowledge entries to curated concept nodes.

Implements §5.2 of the knowledge-map pipeline spec.
Python 3.13+, stdlib only (no third-party imports).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from graph_model import (
    MASS_LINK_THRESHOLD,
    Edge,
    EdgeType,
    Entry,
    MatchMode,
    Provenance,
    confidence_for,
    normalize_text,
)
from concept_registry import Registry


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass
class LinkResult:
    edges: list[Edge]
    report: list[str]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _whole_word_match(term: str, text: str) -> bool:
    """Return True if *term* appears as a whole word in *text* (both pre-normalized)."""
    return bool(re.search(r"\b" + re.escape(term) + r"\b", text))


def _find_matches(terms: list[str], text: str) -> list[str]:
    """Return sorted list of terms (normalized) that whole-word match *text*."""
    matched = []
    for raw_term in terms:
        norm = normalize_text(raw_term)
        if norm and _whole_word_match(norm, text):
            matched.append(norm)
    return sorted(matched)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def link_entries(entries: list[Entry], registry: Registry) -> LinkResult:
    """
    Auto-link every active entry to matching concepts in *registry*.

    Returns a LinkResult with sorted edges and a report list.
    """
    report: list[str] = []

    # Fast-lookup sets for override validation
    entry_ids: set[str] = {e.id for e in entries}
    concept_slugs: set[str] = {c.slug for c in registry.concepts}

    # Pre-normalize entry text once per entry
    entry_text: dict[str, str] = {}
    for entry in entries:
        combined = entry.title + " " + entry.body_excerpt + " " + " ".join(entry.tags)
        entry_text[entry.id] = normalize_text(combined)

    # Build auto edges
    auto_edges: list[Edge] = []
    # Track trigger → edge count for mass-link guard
    trigger_counts: dict[str, int] = defaultdict(int)

    for entry in entries:
        text = entry_text[entry.id]

        for concept in registry.concepts:
            # --- §1 project_scope gate ---
            if (
                concept.project_scope is not None
                and entry.project_id not in concept.project_scope
            ):
                continue

            mode = concept.match_mode

            # --- §4 veto: any negative_keyword match → skip concept ---
            if concept.negative_keywords:
                neg_matches = _find_matches(concept.negative_keywords, text)
                if neg_matches:
                    continue

            # --- §5 required_any gate ---
            matched_required: list[str] = []
            if concept.required_any:
                matched_required = _find_matches(concept.required_any, text)
                if not matched_required:
                    continue  # gate fails

            # --- §6 active terms by match_mode ---
            matched_aliases: list[str] = []
            matched_positives: list[str] = []

            if mode in (MatchMode.alias, MatchMode.hybrid):
                matched_aliases = _find_matches(concept.aliases, text)

            if mode in (MatchMode.keyword, MatchMode.hybrid):
                matched_positives = _find_matches(concept.positive_keywords, text)
                # required_any already matched above; include them in positives pool
                # (per spec §6: keyword mode checks positive_keywords + required_any)
                # But trigger precedence in §7 keeps required_any separate.

            # --- §7 trigger + confidence (precedence order) ---
            trigger: str | None = None
            confidence: str | None = None

            if matched_aliases:
                trigger = matched_aliases[0]  # lex smallest
                confidence = confidence_for("alias")
            elif matched_required:
                trigger = matched_required[0]  # lex smallest
                confidence = confidence_for("required_any")
            elif matched_positives:
                trigger = matched_positives[0]  # lex smallest
                confidence = confidence_for("positive")
            else:
                continue  # nothing matched in active mode

            edge = Edge(
                from_id=entry.id,
                to_id=concept.slug,
                type=EdgeType.about,
                provenance=Provenance.auto,
                trigger=trigger,
                confidence=confidence,
            )
            auto_edges.append(edge)
            trigger_counts[trigger] += 1

    # --- Mass-link guard (before overrides) ---
    for term, count in trigger_counts.items():
        if count > MASS_LINK_THRESHOLD:
            report.append(
                f"MASS-LINK WARNING: trigger {term!r} produced {count} edges "
                f"(> {MASS_LINK_THRESHOLD} threshold)"
            )

    # --- Apply overrides ---
    # Build mutable set of (from_id, to_id) for fast lookup/removal
    # We need to track which auto edges to keep
    block_pairs: set[tuple[str, str]] = set()

    for override in registry.block_about:
        eid = override.get("entry", "")
        cslug = override.get("concept", "")
        if eid not in entry_ids or cslug not in concept_slugs:
            report.append(
                f"stale override: block_about {eid!r} / {cslug!r} — entry or concept not found"
            )
            continue
        block_pairs.add((eid, cslug))

    # Filter out blocked auto edges
    kept_edges: list[Edge] = [
        e for e in auto_edges if (e.from_id, e.to_id) not in block_pairs
    ]

    # Pairs already covered by a surviving auto edge. A force_about override
    # naming one of these would otherwise append a second `about` edge for the
    # same (entry, concept) pair, inflating edge counts and link-diff output.
    auto_kept_pairs: set[tuple[str, str]] = {(e.from_id, e.to_id) for e in kept_edges}

    # Collect existing manual pairs to avoid duplicates
    manual_pairs: set[tuple[str, str]] = set()
    manual_edges: list[Edge] = []

    for override in registry.force_about:
        eid = override.get("entry", "")
        cslug = override.get("concept", "")
        if eid not in entry_ids or cslug not in concept_slugs:
            report.append(
                f"stale override: force_about {eid!r} / {cslug!r} — entry or concept not found"
            )
            continue
        pair = (eid, cslug)
        if pair in manual_pairs or pair in auto_kept_pairs:
            continue  # dedup against other overrides and auto matches
        manual_pairs.add(pair)
        manual_edges.append(
            Edge(
                from_id=eid,
                to_id=cslug,
                type=EdgeType.about,
                provenance=Provenance.manual,
                trigger=None,
                confidence=None,
            )
        )

    all_edges = kept_edges + manual_edges

    # Sort by (from_id, to_id)
    all_edges.sort(key=lambda e: (e.from_id, e.to_id))

    # Summary stats
    distinct_concepts: set[str] = {e.to_id for e in all_edges}
    report.append(
        f"link_entries: {len(all_edges)} edges, {len(distinct_concepts)} distinct concepts linked"
    )

    return LinkResult(edges=all_edges, report=report)
