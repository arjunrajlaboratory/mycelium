"""
build_graph.py — Graph assembly and invariant validation for the knowledge-map pipeline.

Responsibilities (§11/§12 of the design spec):
- build_graph: pure function; assembles Entry/Concept/ProjectHub nodes + Edge set from
  raw pipeline outputs.  Computes effective_status on Concept copies (never mutates source).
- validate_graph: checks structural invariants; returns [] on a clean graph.

Python 3.13+, stdlib only.  Import flat (directory is on sys.path).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from graph_model import (
    Concept,
    ConceptStatus,
    Edge,
    EdgeType,
    Entry,
    EntryStatus,
    Facet,
    Graph,
    LogNode,
    ProjectHub,
)

if TYPE_CHECKING:
    from concept_registry import Registry

# TODO: crystallizes (concept→convention) and elevates (convention→global-knowledge) edges
# are deferred to a later milestone (§7).  The Graph.conventions and Graph.global_knowledge
# lists are kept empty here; add assembly logic when the elevation nodes are implemented.


# ---------------------------------------------------------------------------
# Public API (verbatim signatures from INTERFACES.md)
# ---------------------------------------------------------------------------


def build_graph(
    entries: list[Entry],
    facets: dict[str, Facet],
    edges: list[Edge],
    registry: "Registry",
    logs: list | None = None,
    log_edges: list | None = None,
) -> Graph:
    """
    Assemble the full node+edge graph (§11/§12).

    Pure function: same inputs → same Graph.  Never reads from disk.

    Steps:
    1. Collect active entries (status == active); tombstoned are excluded.
    2. Collect all registry concepts (even unmatched — they appear as empty/candidate nodes).
    3. Build ProjectHub nodes for projects that have ≥1 active entry.
    4. Compute effective_status per concept from live about-edge evidence:
         confirmed     iff ≥2 linked entries AND ≥2 distinct families
         curated_singleton  if source status is curated_singleton (regardless of evidence)
         candidate     otherwise
       Writes effective_status onto COPIES of Concept objects; never mutates registry objects.
    5. Filter edges: keep only `about` edges whose both endpoints resolve to live nodes.
    6. Return Graph with empty conventions/global_knowledge lists (deferred, §7).
    """
    # ------------------------------------------------------------------
    # Step 1: active entries
    # ------------------------------------------------------------------
    active_entries: list[Entry] = [e for e in entries if e.status == EntryStatus.active]
    active_entry_ids: set[str] = {e.id for e in active_entries}

    # Normalise optional params
    _logs: list = logs if logs is not None else []
    _log_edges: list = log_edges if log_edges is not None else []

    # Build log id set for validation and mentions population
    log_id_set: set[str] = {lg.id for lg in _logs}

    # ------------------------------------------------------------------
    # Step 2: project hubs — one per project that has ≥1 active entry
    # ------------------------------------------------------------------
    # Build a project_id → ProjectMeta lookup from the registry
    project_meta_by_id = {p.id: p for p in registry.projects}

    # Collect project_ids referenced by active entries
    project_ids_with_entries: set[str] = {e.project_id for e in active_entries}

    project_hubs: list[ProjectHub] = []
    for pid in sorted(project_ids_with_entries):
        meta = project_meta_by_id.get(pid)
        if meta is not None:
            project_hubs.append(
                ProjectHub(
                    project_id=meta.id,
                    name=meta.name,
                    family=meta.family,
                )
            )
        else:
            # Entry references a project not in registry — create a minimal hub
            # (should not happen in a well-formed run, but guard defensively)
            project_hubs.append(
                ProjectHub(
                    project_id=pid,
                    name=pid,
                    family="unknown",
                )
            )

    # ------------------------------------------------------------------
    # Step 3: partition edges
    #   • about-edges from live ENTRIES feed status computation
    #   • mentions/follows edges from logs are preserved for output
    #   • about-edges from log ids are validation errors (R1 invariant)
    # ------------------------------------------------------------------
    concept_slugs: set[str] = {c.slug for c in registry.concepts}

    live_about_edges: list[Edge] = []
    output_edges: list[Edge] = []  # all edges that appear in the graph
    validation_warnings: list[str] = []

    for e in edges:
        if e.type == EdgeType.about:
            if e.from_id in log_id_set:
                # R1 invariant violation: about edge must not originate from a log
                validation_warnings.append(
                    f"INVARIANT VIOLATION: about edge from log id {e.from_id!r} "
                    f"(to {e.to_id!r}) — logs may not carry about edges (R1)"
                )
                # still include in output_edges so callers see it, but NOT in live_about_edges
                output_edges.append(e)
            elif e.from_id in active_entry_ids and e.to_id in concept_slugs:
                live_about_edges.append(e)
                output_edges.append(e)
            # else: about edge with dead/unknown from_id or unknown to_id — drop silently
        elif e.type == EdgeType.mentions:
            # mentions: from_id must be a live log id; to_id must be a concept slug
            if e.from_id not in log_id_set:
                validation_warnings.append(
                    f"INVALID mentions edge: from_id {e.from_id!r} is not a live log id "
                    f"(to {e.to_id!r})"
                )
            if e.to_id not in concept_slugs:
                validation_warnings.append(
                    f"INVALID mentions edge: to_id {e.to_id!r} is not a live concept slug "
                    f"(from {e.from_id!r})"
                )
            output_edges.append(e)
        elif e.type == EdgeType.follows:
            # follows: both from_id and to_id must be live log ids in same project
            from_log = next((lg for lg in _logs if lg.id == e.from_id), None)
            to_log = next((lg for lg in _logs if lg.id == e.to_id), None)
            if from_log is None:
                validation_warnings.append(
                    f"INVALID follows edge: from_id {e.from_id!r} is not a live log id"
                )
            if to_log is None:
                validation_warnings.append(
                    f"INVALID follows edge: to_id {e.to_id!r} is not a live log id"
                )
            if from_log is not None and to_log is not None:
                if from_log.project_id != to_log.project_id:
                    validation_warnings.append(
                        f"INVALID follows edge: {e.from_id!r} (project {from_log.project_id!r}) "
                        f"→ {e.to_id!r} (project {to_log.project_id!r}) — must be same project"
                    )
            output_edges.append(e)
        else:
            # unknown edge type — pass through
            output_edges.append(e)

    # ------------------------------------------------------------------
    # Step 4: compute effective_status per concept
    # ------------------------------------------------------------------
    # Map concept slug → set of (entry_id, family) pairs from live about edges
    slug_to_entry_ids: dict[str, set[str]] = {}
    slug_to_families: dict[str, set[str]] = {}

    # Build family lookup from active entries
    entry_family: dict[str, str] = {e.id: e.family for e in active_entries}

    for edge in live_about_edges:
        slug = edge.to_id
        eid = edge.from_id
        fam = entry_family.get(eid, "")
        slug_to_entry_ids.setdefault(slug, set()).add(eid)
        slug_to_families.setdefault(slug, set()).add(fam)

    concepts_with_effective_status: list[Concept] = []
    for concept in registry.concepts:
        # Make a shallow copy to avoid mutating the registry's Concept object
        c = copy.copy(concept)

        linked_entry_ids = slug_to_entry_ids.get(c.slug, set())
        linked_families = slug_to_families.get(c.slug, set())

        n_entries = len(linked_entry_ids)
        n_families = len(linked_families)

        if c.status == ConceptStatus.curated_singleton:
            # curated_singleton is always kept as-is regardless of evidence
            effective = ConceptStatus.curated_singleton
        elif n_entries >= 2 and n_families >= 2:
            effective = ConceptStatus.confirmed
        else:
            # "report-only demotion": even a source-confirmed concept that fails
            # the threshold gets effective_status=candidate
            effective = ConceptStatus.candidate

        c.effective_status = effective
        concepts_with_effective_status.append(c)

    # ------------------------------------------------------------------
    # Step 4b: populate LogNode.mentions from mentions edges (single source of truth)
    # ------------------------------------------------------------------
    # Build log_id → list of concept slugs from valid mentions edges
    log_mentions: dict[str, list[str]] = {}
    for e in _log_edges:
        if e.type == EdgeType.mentions and e.from_id in log_id_set:
            log_mentions.setdefault(e.from_id, [])
            if e.to_id not in log_mentions[e.from_id]:
                log_mentions[e.from_id].append(e.to_id)

    # Also process mentions edges passed via the main edges param (already in output_edges)
    for e in output_edges:
        if e.type == EdgeType.mentions and e.from_id in log_id_set:
            log_mentions.setdefault(e.from_id, [])
            if e.to_id not in log_mentions[e.from_id]:
                log_mentions[e.from_id].append(e.to_id)

    logs_with_mentions: list[LogNode] = []
    for lg in _logs:
        lg_copy = copy.copy(lg)
        lg_copy.mentions = sorted(log_mentions.get(lg.id, []))
        logs_with_mentions.append(lg_copy)

    # Merge log_edges into output (they were not iterated above)
    for e in _log_edges:
        output_edges.append(e)

    # ------------------------------------------------------------------
    # Step 5: assemble graph
    # ------------------------------------------------------------------
    # Sort collections for determinism (§12)
    active_entries_sorted = sorted(active_entries, key=lambda e: e.id)
    concepts_sorted = sorted(concepts_with_effective_status, key=lambda c: c.slug)
    edges_sorted = sorted(
        output_edges, key=lambda e: (e.from_id, e.to_id, e.type.value)
    )
    hubs_sorted = sorted(project_hubs, key=lambda h: h.project_id)
    logs_sorted = sorted(logs_with_mentions, key=lambda l: l.id)

    return Graph(
        entries=active_entries_sorted,
        concepts=concepts_sorted,
        edges=edges_sorted,
        project_hubs=hubs_sorted,
        logs=logs_sorted,
        conventions=[],  # TODO: elevation nodes — deferred (§7)
        global_knowledge=[],  # TODO: elevation nodes — deferred (§7)
    )


def validate_graph(graph: Graph) -> list[str]:
    """
    Enforce build invariants (§12).  Returns invariant-violation messages; [] = ok.

    Invariants checked:
    - No dangling edges: every from_id resolves to an active entry id, and every to_id
      resolves to a concept slug (or hub/convention/global — none in MVP).
    - No duplicate entry ids; no duplicate concept slugs.
    - Every concept with effective_status == confirmed actually meets the threshold
      (≥2 entries AND ≥2 distinct families) — catches effective_status computation errors.
    - curated_singleton concepts are exempt from the family-count rule.
    - Every entry referenced by an edge exists and is active.
    """
    violations: list[str] = []

    # Build lookup sets
    entry_id_set: set[str] = set()
    for entry in graph.entries:
        if entry.id in entry_id_set:
            violations.append(f"DUPLICATE entry id: {entry.id!r}")
        entry_id_set.add(entry.id)

    concept_slug_set: set[str] = set()
    for concept in graph.concepts:
        if concept.slug in concept_slug_set:
            violations.append(f"DUPLICATE concept slug: {concept.slug!r}")
        concept_slug_set.add(concept.slug)

    # Hub/convention/global namespaces for future edge endpoint resolution
    # (no edges to these in MVP, but include for completeness)
    hub_id_set: set[str] = {h.project_id for h in graph.project_hubs}

    # All valid "to" targets: concept slugs (about edges), hubs, conventions, globals
    # In MVP only concept slugs are reachable targets for about edges
    valid_to_ids: set[str] = concept_slug_set | hub_id_set

    # Valid "from" ids: active entry ids
    valid_from_ids: set[str] = entry_id_set

    # Log id set for edge validation
    log_id_set_v: set[str] = {lg.id for lg in graph.logs}

    # Build project_id lookup for logs (for follows same-project check)
    log_project: dict[str, str] = {lg.id: lg.project_id for lg in graph.logs}

    # ------------------------------------------------------------------
    # Dangling edge check (type-aware)
    # ------------------------------------------------------------------
    for edge in graph.edges:
        if edge.type == EdgeType.about:
            # about: from_id must be an active entry; to_id must be a concept slug
            if edge.from_id not in valid_from_ids:
                violations.append(
                    f"DANGLING edge: from_id {edge.from_id!r} does not resolve to "
                    f"any active entry (edge → {edge.to_id!r})"
                )
            if edge.to_id not in valid_to_ids:
                violations.append(
                    f"DANGLING edge: to_id {edge.to_id!r} does not resolve to any "
                    f"concept slug or hub (edge from {edge.from_id!r})"
                )
        elif edge.type == EdgeType.mentions:
            # mentions: from_id must be a log id; to_id must be a concept slug
            if edge.from_id not in log_id_set_v:
                violations.append(
                    f"INVALID mentions edge: from_id {edge.from_id!r} is not a live log id "
                    f"(to {edge.to_id!r})"
                )
            if edge.to_id not in concept_slug_set:
                violations.append(
                    f"INVALID mentions edge: to_id {edge.to_id!r} is not a live concept slug "
                    f"(from {edge.from_id!r})"
                )
        elif edge.type == EdgeType.follows:
            # follows: both endpoints must be live log ids in the same project
            if edge.from_id not in log_id_set_v:
                violations.append(
                    f"INVALID follows edge: from_id {edge.from_id!r} is not a live log id"
                )
            if edge.to_id not in log_id_set_v:
                violations.append(
                    f"INVALID follows edge: to_id {edge.to_id!r} is not a live log id"
                )
            if edge.from_id in log_id_set_v and edge.to_id in log_id_set_v:
                if log_project.get(edge.from_id) != log_project.get(edge.to_id):
                    violations.append(
                        f"INVALID follows edge: {edge.from_id!r} and {edge.to_id!r} "
                        f"are in different projects"
                    )

    # ------------------------------------------------------------------
    # effective_status confirmed ↔ threshold consistency check
    # ------------------------------------------------------------------
    # Build slug → {entry_id} and slug → {family} from edges
    slug_to_entry_ids: dict[str, set[str]] = {}
    slug_to_families: dict[str, set[str]] = {}

    entry_family: dict[str, str] = {e.id: e.family for e in graph.entries}

    for edge in graph.edges:
        if edge.type == EdgeType.about and edge.from_id in entry_id_set:
            slug = edge.to_id
            eid = edge.from_id
            fam = entry_family.get(eid, "")
            slug_to_entry_ids.setdefault(slug, set()).add(eid)
            slug_to_families.setdefault(slug, set()).add(fam)

    for concept in graph.concepts:
        if concept.effective_status == ConceptStatus.confirmed:
            n_entries = len(slug_to_entry_ids.get(concept.slug, set()))
            n_families = len(slug_to_families.get(concept.slug, set()))
            if n_entries < 2 or n_families < 2:
                violations.append(
                    f"INVARIANT VIOLATION: concept {concept.slug!r} has "
                    f"effective_status=confirmed but only {n_entries} linked "
                    f"entry/entries from {n_families} distinct family/families "
                    f"(need ≥2 entries, ≥2 families)"
                )

    return violations
