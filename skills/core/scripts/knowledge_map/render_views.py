"""
render_views.py — Views/projections renderer for the knowledge-map pipeline.

Generates human-readable markdown projections from an assembled Graph.
Python 3.13+, stdlib only. No wall-clock anywhere in generated artifacts (§12).

All intermediate collections are sorted before iteration for determinism (§12).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from graph_model import (
    ConceptStatus,
    EdgeType,
    Graph,
    Facet,
    MASS_LINK_THRESHOLD,
    Stage,
)


# ---------------------------------------------------------------------------
# Public API (contract from INTERFACES.md)
# ---------------------------------------------------------------------------


def render_views(
    graph: Graph,
    facets: dict[str, Facet],
    out_dir: Path,
    baseline_path: Path | None = None,
) -> None:
    """
    Write all projection views under ``out_dir/`` (create if absent).

    Views written:
    - cross-project-concepts.md  — confirmed concepts spanning ≥2 families
    - lifecycle.md               — per-project → per-stage entry listing
    - elevation-ladder.md        — concepts grouped by effective_status
    - stale-concepts.md          — confirmed in source but candidate effective (would-demote)
    - link-diff.md               — added/removed edges vs baseline (or skip notice)

    Contract:
    - Never reads from disk (uses only the Graph + facets passed in).
    - Never writes wall-clock or non-deterministic content.
    - Sorts all intermediate collections before iteration.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_cross_project_concepts(graph, out_dir)
    _write_lifecycle(graph, facets, out_dir)
    _write_elevation_ladder(graph, out_dir)
    _write_stale_concepts(graph, out_dir)
    _write_link_diff(graph, out_dir, baseline_path)


# ---------------------------------------------------------------------------
# View 1: cross-project-concepts.md
# ---------------------------------------------------------------------------


def _write_cross_project_concepts(graph: Graph, out_dir: Path) -> None:
    """
    For every concept whose effective_status == confirmed (≥2 entries from ≥2 families),
    emit a section: label + definition, families/projects spanned, entry count per project,
    and a few example entry titles.

    Sort concepts by (descending family span, then slug).
    """
    # Index about edges: concept slug → list of entry ids
    concept_to_entry_ids: dict[str, list[str]] = defaultdict(list)
    for edge in sorted(graph.edges, key=lambda e: (e.from_id, e.to_id)):
        if edge.type == EdgeType.about:
            concept_to_entry_ids[edge.to_id].append(edge.from_id)

    # Index entries by id
    entry_by_id = {e.id: e for e in graph.entries}

    # Build per-concept stats for confirmed concepts
    confirmed_concepts = [
        c for c in graph.concepts if c.effective_status == ConceptStatus.confirmed
    ]

    # For each concept: compute family span, project counts, example titles
    concept_stats: list[tuple[int, str, object]] = []  # (family_span, slug, concept)
    concept_details: dict[str, dict] = {}

    for concept in confirmed_concepts:
        linked_entry_ids = sorted(concept_to_entry_ids.get(concept.slug, []))
        linked_entries = [
            entry_by_id[eid] for eid in linked_entry_ids if eid in entry_by_id
        ]

        # Count entries per project
        project_counts: dict[str, int] = defaultdict(int)
        families_seen: set[str] = set()
        for entry in linked_entries:
            project_counts[entry.project_id] += 1
            families_seen.add(entry.family)

        family_span = len(families_seen)

        # Collect up to 3 example titles (deterministic: sort by (project_id, title))
        example_titles = [
            e.title
            for e in sorted(linked_entries, key=lambda e: (e.project_id, e.title))
        ][:3]

        concept_stats.append((-family_span, concept.slug, concept))
        concept_details[concept.slug] = {
            "family_span": family_span,
            "families": sorted(families_seen),
            "project_counts": dict(sorted(project_counts.items())),
            "example_titles": example_titles,
            "total_entries": len(linked_entries),
        }

    # Sort: primary = descending family span (stored as negative), secondary = slug
    concept_stats.sort(key=lambda t: (t[0], t[1]))

    lines: list[str] = []
    n_confirmed = len(confirmed_concepts)
    lines.append(f"{n_confirmed} concepts connect ≥2 project families.")
    lines.append("")

    for neg_span, slug, concept in concept_stats:
        details = concept_details[slug]
        lines.append(f"## {concept.label}")
        lines.append("")
        lines.append(f"**Definition:** {concept.definition}")
        lines.append("")
        lines.append(
            f"**Families spanned ({details['family_span']}):** {', '.join(details['families'])}"
        )
        lines.append("")

        # Per-project entry counts
        lines.append("**Entry counts by project:**")
        for proj_id, count in sorted(details["project_counts"].items()):
            lines.append(f"- `{proj_id}`: {count} entr{'y' if count == 1 else 'ies'}")
        lines.append("")

        if details["example_titles"]:
            lines.append("**Example entries:**")
            for title in details["example_titles"]:
                lines.append(f"- {title}")
            lines.append("")

    _write_file(out_dir / "cross-project-concepts.md", lines)


# ---------------------------------------------------------------------------
# View 2: lifecycle.md
# ---------------------------------------------------------------------------

_STAGE_ORDER = [
    Stage.data_registry,
    Stage.lit_review,
    Stage.planning,
    Stage.analysis,
    Stage.figure_generation,
    Stage.writing,
    Stage.evaluation,
    Stage.infrastructure,
]


def _write_lifecycle(graph: Graph, facets: dict[str, Facet], out_dir: Path) -> None:
    """
    Per project → per Stage → entries listing.
    Header note: stage assignment is heuristic.
    """
    # Index entries by project
    entries_by_project: dict[str, list] = defaultdict(list)
    for entry in graph.entries:
        entries_by_project[entry.project_id].append(entry)

    # Sort project hubs deterministically
    sorted_hubs = sorted(graph.project_hubs, key=lambda h: h.project_id)

    lines: list[str] = []
    lines.append("# Lifecycle View")
    lines.append("")
    lines.append(
        "> **Note:** Stage assignment is heuristic (path-keyword or default). "
        "Curated overrides in `entry-facets.yaml` take precedence. "
        "Unassigned entries are collected in a trailing bucket."
    )
    lines.append("")

    for hub in sorted_hubs:
        lines.append(f"## {hub.name} (`{hub.project_id}`)")
        lines.append("")

        hub_entries = sorted(
            entries_by_project.get(hub.project_id, []),
            key=lambda e: e.id,
        )

        if not hub_entries:
            lines.append("_No entries extracted for this project._")
            lines.append("")
            continue

        # Group entries by stage
        stage_to_entries: dict[str, list] = defaultdict(list)
        for entry in hub_entries:
            facet = facets.get(entry.id)
            stage_val = facet.stage.value if facet else Stage.unassigned.value
            stage_to_entries[stage_val].append(entry)

        # Emit assigned stages in canonical order
        for stage in _STAGE_ORDER:
            stage_entries = stage_to_entries.get(stage.value, [])
            if not stage_entries:
                continue
            lines.append(f"### {stage.value.replace('_', ' ').title()}")
            lines.append("")
            for entry in sorted(stage_entries, key=lambda e: e.title):
                lines.append(f"- {entry.title} (`{entry.id}`)")
            lines.append("")

        # Unassigned bucket last
        unassigned = stage_to_entries.get(Stage.unassigned.value, [])
        if unassigned:
            lines.append("### Unassigned")
            lines.append("")
            for entry in sorted(unassigned, key=lambda e: e.title):
                lines.append(f"- {entry.title} (`{entry.id}`)")
            lines.append("")

    _write_file(out_dir / "lifecycle.md", lines)


# ---------------------------------------------------------------------------
# View 3: elevation-ladder.md
# ---------------------------------------------------------------------------

_STATUS_ORDER = [
    ConceptStatus.confirmed,
    ConceptStatus.candidate,
    ConceptStatus.curated_singleton,
]

_STATUS_DESCRIPTIONS = {
    ConceptStatus.confirmed: (
        "Confirmed — ≥2 entries from ≥2 distinct project families. "
        "These are the cross-project crystallizations the map exists to surface."
    ),
    ConceptStatus.candidate: (
        "Candidate — auto-proposed or has fewer than 2 families of evidence. "
        "Awaiting curation or additional cross-project entries."
    ),
    ConceptStatus.curated_singleton: (
        "Curated Singleton — explicitly curated by a human but backed by only one family. "
        "Not subject to the ≥2-family rule."
    ),
}


def _write_elevation_ladder(graph: Graph, out_dir: Path) -> None:
    """
    Concepts grouped by effective_status (confirmed / candidate / curated_singleton),
    each as a list with entry counts from about edges.
    """
    # Count about edges per concept slug
    about_count: dict[str, int] = defaultdict(int)
    for edge in graph.edges:
        if edge.type == EdgeType.about:
            about_count[edge.to_id] += 1

    # Group concepts by effective_status (fall back to source status if unset)
    status_groups: dict[ConceptStatus, list] = defaultdict(list)
    for concept in graph.concepts:
        eff = (
            concept.effective_status
            if concept.effective_status is not None
            else concept.status
        )
        status_groups[eff].append(concept)

    lines: list[str] = []
    lines.append("# Elevation Ladder")
    lines.append("")
    lines.append(
        "Concepts progress through three statuses: "
        "`candidate` → `confirmed` (cross-family crystallization) or `curated_singleton` (human override). "
        "This ladder shows the current distribution."
    )
    lines.append("")

    for status in _STATUS_ORDER:
        concepts_in_group = sorted(status_groups.get(status, []), key=lambda c: c.slug)
        count = len(concepts_in_group)
        lines.append(f"## {status.value.replace('_', ' ').title()} ({count})")
        lines.append("")
        lines.append(f"_{_STATUS_DESCRIPTIONS[status]}_")
        lines.append("")

        if not concepts_in_group:
            lines.append("_None._")
            lines.append("")
            continue

        for concept in concepts_in_group:
            n_entries = about_count.get(concept.slug, 0)
            lines.append(
                f"- **{concept.label}** (`{concept.slug}`) — {n_entries} linked entr{'y' if n_entries == 1 else 'ies'}"
            )
        lines.append("")

    _write_file(out_dir / "elevation-ladder.md", lines)


# ---------------------------------------------------------------------------
# View 4: stale-concepts.md
# ---------------------------------------------------------------------------


def _write_stale_concepts(graph: Graph, out_dir: Path) -> None:
    """
    Concepts where source status == confirmed but effective_status == candidate
    (would-demote per §11). Report-only — never implies source mutation.
    """
    stale = [
        c
        for c in graph.concepts
        if c.status == ConceptStatus.confirmed
        and c.effective_status == ConceptStatus.candidate
    ]
    stale_sorted = sorted(stale, key=lambda c: c.slug)

    lines: list[str] = []
    lines.append("# Stale Concepts (Would-Demote Report)")
    lines.append("")
    lines.append(
        "> **Report-only.** This file never implies mutation of `concepts.yaml`. "
        "If you agree with a demotion, edit the registry manually."
    )
    lines.append("")

    if not stale_sorted:
        lines.append(
            "No stale concepts detected — all confirmed concepts meet the ≥2-family threshold."
        )
        lines.append("")
    else:
        lines.append(
            f"{len(stale_sorted)} concept{'s' if len(stale_sorted) != 1 else ''} "
            f"{'are' if len(stale_sorted) != 1 else 'is'} confirmed in source "
            f"but would be demoted to `candidate` based on live evidence:"
        )
        lines.append("")
        for concept in stale_sorted:
            lines.append(f"## {concept.label} (`{concept.slug}`)")
            lines.append("")
            lines.append(f"**Source status:** `{concept.status.value}`")
            lines.append(
                f"**Effective status:** `{concept.effective_status.value if concept.effective_status else 'unset'}`"
            )
            lines.append(f"**Definition:** {concept.definition}")
            lines.append("")
            lines.append(
                "_Would-demote: live evidence does not meet ≥2 entries from ≥2 distinct families._"
            )
            lines.append("")

    _write_file(out_dir / "stale-concepts.md", lines)


# ---------------------------------------------------------------------------
# View 5: link-diff.md
# ---------------------------------------------------------------------------


def _write_link_diff(
    graph: Graph,
    out_dir: Path,
    baseline_path: Path | None,
) -> None:
    """
    If baseline_path is given and exists, load that prior knowledge-graph.json
    (canonical JSON with an ``edges`` list of {from, to, ...});
    compute edges added/removed vs the current graph;
    list them; and flag any single ``trigger`` newly linking > MASS_LINK_THRESHOLD entries.
    If no baseline, write a skip notice.
    """
    lines: list[str] = []
    lines.append("# Link Diff")
    lines.append("")

    if baseline_path is None or not baseline_path.exists():
        lines.append("No baseline provided — diff skipped.")
        lines.append("")
        _write_file(out_dir / "link-diff.md", lines)
        return

    # Load baseline edges
    try:
        baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        lines.append(f"Error reading baseline: {exc}")
        lines.append("")
        _write_file(out_dir / "link-diff.md", lines)
        return

    raw_baseline_edges = baseline_data.get("edges", [])

    # Normalise: baseline uses "from"/"to" keys (Edge.to_dict() maps from_id→"from", to_id→"to")
    def _edge_key(e: dict) -> tuple[str, str, str]:
        return (e.get("from", ""), e.get("to", ""), e.get("type", ""))

    baseline_set: set[tuple[str, str, str]] = {_edge_key(e) for e in raw_baseline_edges}

    # Current edges
    def _current_edge_key(e) -> tuple[str, str, str]:
        return (e.from_id, e.to_id, e.type.value)

    current_set: set[tuple[str, str, str]] = {_current_edge_key(e) for e in graph.edges}

    added = sorted(current_set - baseline_set)
    removed = sorted(baseline_set - current_set)

    lines.append(
        f"Baseline: `{baseline_path.name}` — "
        f"{len(added)} edge(s) added, {len(removed)} edge(s) removed."
    )
    lines.append("")

    if added:
        lines.append("## Added Edges")
        lines.append("")
        for from_id, to_id, etype in added:
            lines.append(f"- `{from_id}` → `{to_id}` (type: `{etype}`)")
        lines.append("")
    else:
        lines.append("## Added Edges")
        lines.append("")
        lines.append("_None._")
        lines.append("")

    if removed:
        lines.append("## Removed Edges")
        lines.append("")
        for from_id, to_id, etype in removed:
            lines.append(f"- `{from_id}` → `{to_id}` (type: `{etype}`)")
        lines.append("")
    else:
        lines.append("## Removed Edges")
        lines.append("")
        lines.append("_None._")
        lines.append("")

    # Mass-link flag: check if any single trigger in added edges links > MASS_LINK_THRESHOLD entries
    # We look at about-edges in the current graph keyed by trigger
    trigger_to_new_entries: dict[str, set[str]] = defaultdict(set)
    added_about = {(f, t) for f, t, tp in added if tp == EdgeType.about.value}
    for edge in graph.edges:
        if edge.type == EdgeType.about and (edge.from_id, edge.to_id) in added_about:
            if edge.trigger:
                trigger_to_new_entries[edge.trigger].add(edge.from_id)

    mass_flags = sorted(
        (trigger, len(eids))
        for trigger, eids in trigger_to_new_entries.items()
        if len(eids) > MASS_LINK_THRESHOLD
    )

    if mass_flags:
        lines.append("## Mass-Link Flags")
        lines.append("")
        lines.append(
            f"> A single trigger linking >{MASS_LINK_THRESHOLD} entries is flagged for review."
        )
        lines.append("")
        for trigger, count in mass_flags:
            lines.append(f"- Trigger `{trigger}` newly links {count} entries")
        lines.append("")

    _write_file(out_dir / "link-diff.md", lines)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _write_file(path: Path, lines: list[str]) -> None:
    """Write lines to path, joined with LF, ending with a single trailing newline."""
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
