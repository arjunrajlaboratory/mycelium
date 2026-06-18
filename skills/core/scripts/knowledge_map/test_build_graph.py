"""
test_build_graph.py — Unit tests for build_graph and validate_graph.

All inputs are constructed directly (no disk I/O).
Run with: python -m pytest test_build_graph.py -v
"""

from __future__ import annotations

from dataclasses import dataclass, field


from graph_model import (
    Concept,
    ConceptStatus,
    Edge,
    EdgeType,
    Entry,
    EntryKind,
    EntryStatus,
    Facet,
    Graph,
    MatchMode,
    ProjectMeta,
    Provenance,
    SCHEMA_VERSION,
    SourceShape,
    SupportingKind,
    SupportingNode,
    confidence_for,
    sha256_hash,
)
from build_graph import build_graph, validate_graph


# ---------------------------------------------------------------------------
# Stub Registry (mirrors concept_registry.Registry without importing it)
# ---------------------------------------------------------------------------


@dataclass
class StubRegistry:
    """Minimal stand-in for concept_registry.Registry used in tests."""

    concepts: list[Concept]
    projects: list[ProjectMeta]
    force_about: list[dict] = field(default_factory=list)
    block_about: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_entry(
    eid: str,
    project_id: str,
    family: str,
    status: EntryStatus = EntryStatus.active,
) -> Entry:
    return Entry(
        id=eid,
        kind=EntryKind.learning,
        source_shape=SourceShape.aggregate_section,
        project_id=project_id,
        family=family,
        source_path=f"{project_id}/.living/learnings.md",
        anchor=f"[2026-01-01] Test entry {eid}",
        line_start=1,
        line_end=5,
        title=f"Test entry {eid}",
        date="2026-01-01",
        tags=[],
        body_excerpt=f"Body of {eid}.",
        content_hash=sha256_hash(f"body-{eid}"),
        status=status,
        schema_version=SCHEMA_VERSION,
    )


def make_concept(slug: str, status: ConceptStatus = ConceptStatus.candidate) -> Concept:
    return Concept(
        slug=slug,
        label=slug.replace("-", " ").title(),
        definition=f"Definition of {slug}.",
        status=status,
        aliases=[slug],
        positive_keywords=[],
        negative_keywords=[],
        required_any=[],
        project_scope=None,
        match_mode=MatchMode.alias,
        relates=[],
        parent=None,
        effective_status=None,
    )


def make_about_edge(from_id: str, to_id: str) -> Edge:
    return Edge(
        from_id=from_id,
        to_id=to_id,
        type=EdgeType.about,
        provenance=Provenance.auto,
        trigger=to_id,
        confidence=confidence_for("alias"),
    )


def make_project(pid: str, family: str) -> ProjectMeta:
    return ProjectMeta(
        id=pid,
        name=pid.replace("-", " ").title(),
        path=f"{pid}/",
        family=family,
        has_living=True,
    )


EMPTY_FACETS: dict[str, Facet] = {}


# ---------------------------------------------------------------------------
# Test 1: concept with 2 entries from 2 distinct families → confirmed
# ---------------------------------------------------------------------------


def test_confirmed_concept_two_families():
    e1 = make_entry("e-001", "proj-a", "family-alpha")
    e2 = make_entry("e-002", "proj-b", "family-beta")
    concept = make_concept("geo-access", ConceptStatus.candidate)
    edges = [
        make_about_edge("e-001", "geo-access"),
        make_about_edge("e-002", "geo-access"),
    ]
    registry = StubRegistry(
        concepts=[concept],
        projects=[
            make_project("proj-a", "family-alpha"),
            make_project("proj-b", "family-beta"),
        ],
    )

    graph = build_graph([e1, e2], EMPTY_FACETS, edges, registry)

    assert len(graph.concepts) == 1
    c = graph.concepts[0]
    assert c.slug == "geo-access"
    assert c.effective_status == ConceptStatus.confirmed, (
        f"Expected confirmed, got {c.effective_status}"
    )

    violations = validate_graph(graph)
    assert violations == [], f"Expected no violations, got: {violations}"


# ---------------------------------------------------------------------------
# Test 2: concept with 2 entries from the SAME family → candidate (demotion)
# ---------------------------------------------------------------------------


def test_same_family_demotes_to_candidate():
    e1 = make_entry("e-003", "proj-a", "family-alpha")
    e2 = make_entry("e-004", "proj-c", "family-alpha")  # different project, SAME family
    concept = make_concept(
        "pipeline-design", ConceptStatus.confirmed
    )  # source says confirmed
    edges = [
        make_about_edge("e-003", "pipeline-design"),
        make_about_edge("e-004", "pipeline-design"),
    ]
    registry = StubRegistry(
        concepts=[concept],
        projects=[
            make_project("proj-a", "family-alpha"),
            make_project("proj-c", "family-alpha"),
        ],
    )

    graph = build_graph([e1, e2], EMPTY_FACETS, edges, registry)

    c = graph.concepts[0]
    assert c.slug == "pipeline-design"
    assert c.effective_status == ConceptStatus.candidate, (
        f"Same-family demotion failed: got {c.effective_status}"
    )

    # validate_graph should not flag it (effective_status is candidate, not confirmed)
    violations = validate_graph(graph)
    assert violations == [], f"Unexpected violations: {violations}"


# ---------------------------------------------------------------------------
# Test 3: curated_singleton stays curated_singleton regardless of evidence
# ---------------------------------------------------------------------------


def test_curated_singleton_preserved():
    # Only one entry — would normally be candidate, but curated_singleton overrides
    e1 = make_entry("e-005", "proj-a", "family-alpha")
    concept = make_concept("legacy-api", ConceptStatus.curated_singleton)
    edges = [make_about_edge("e-005", "legacy-api")]
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    graph = build_graph([e1], EMPTY_FACETS, edges, registry)

    c = graph.concepts[0]
    assert c.slug == "legacy-api"
    assert c.effective_status == ConceptStatus.curated_singleton, (
        f"curated_singleton was changed to {c.effective_status}"
    )

    violations = validate_graph(graph)
    assert violations == [], f"Unexpected violations: {violations}"


# ---------------------------------------------------------------------------
# Test 4: ProjectHub created for projects with active entries
# ---------------------------------------------------------------------------


def test_project_hubs_created_for_active_entries():
    e1 = make_entry("e-006", "proj-x", "family-x")
    e2 = make_entry("e-007", "proj-y", "family-y")
    concept = make_concept("test-concept")
    edges = [
        make_about_edge("e-006", "test-concept"),
        make_about_edge("e-007", "test-concept"),
    ]
    registry = StubRegistry(
        concepts=[concept],
        projects=[
            make_project("proj-x", "family-x"),
            make_project("proj-y", "family-y"),
        ],
    )

    graph = build_graph([e1, e2], EMPTY_FACETS, edges, registry)

    hub_ids = {h.project_id for h in graph.project_hubs}
    assert "proj-x" in hub_ids, "Hub for proj-x missing"
    assert "proj-y" in hub_ids, "Hub for proj-y missing"
    assert len(graph.project_hubs) == 2


def test_no_hub_for_tombstoned_only_project():
    """A project whose only entries are tombstoned should not generate a hub."""
    e_active = make_entry("e-008", "proj-live", "family-live")
    e_tomb = make_entry(
        "e-009", "proj-dead", "family-dead", status=EntryStatus.tombstone
    )
    concept = make_concept("some-concept")
    edges = [make_about_edge("e-008", "some-concept")]
    registry = StubRegistry(
        concepts=[concept],
        projects=[
            make_project("proj-live", "family-live"),
            make_project("proj-dead", "family-dead"),
        ],
    )

    graph = build_graph([e_active, e_tomb], EMPTY_FACETS, edges, registry)

    hub_ids = {h.project_id for h in graph.project_hubs}
    assert "proj-live" in hub_ids
    assert "proj-dead" not in hub_ids, "Tombstoned-only project should not get a hub"


# ---------------------------------------------------------------------------
# Test 5: validate_graph returns [] on a good graph and non-empty on dangling edge
# ---------------------------------------------------------------------------


def test_validate_clean_graph():
    e1 = make_entry("e-010", "proj-a", "family-alpha")
    e2 = make_entry("e-011", "proj-b", "family-beta")
    concept = make_concept("clean-concept", ConceptStatus.candidate)
    edges = [
        make_about_edge("e-010", "clean-concept"),
        make_about_edge("e-011", "clean-concept"),
    ]
    registry = StubRegistry(
        concepts=[concept],
        projects=[
            make_project("proj-a", "family-alpha"),
            make_project("proj-b", "family-beta"),
        ],
    )

    graph = build_graph([e1, e2], EMPTY_FACETS, edges, registry)
    violations = validate_graph(graph)
    assert violations == [], f"Clean graph should have no violations; got: {violations}"


def test_validate_dangling_from_id():
    """Inject a Graph with a dangling from_id edge after assembly."""
    e1 = make_entry("e-012", "proj-a", "family-alpha")
    concept = make_concept("target-concept", ConceptStatus.candidate)
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    # Build a good graph first
    good_edges = [make_about_edge("e-012", "target-concept")]
    graph = build_graph([e1], EMPTY_FACETS, good_edges, registry)

    # Inject a dangling edge directly into the graph (simulate corruption)
    bad_edge = make_about_edge("e-NONEXISTENT", "target-concept")

    bad_graph = Graph(
        entries=graph.entries,
        concepts=graph.concepts,
        edges=graph.edges + [bad_edge],
        project_hubs=graph.project_hubs,
    )

    violations = validate_graph(bad_graph)
    assert len(violations) >= 1, "Expected at least one violation for dangling from_id"
    assert any("e-NONEXISTENT" in v for v in violations), (
        f"Violation message should mention the bad id; got: {violations}"
    )


def test_validate_dangling_to_id():
    """Inject a Graph with a dangling to_id (concept slug that doesn't exist)."""
    e1 = make_entry("e-013", "proj-a", "family-alpha")
    concept = make_concept("real-concept", ConceptStatus.candidate)
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    good_edges = [make_about_edge("e-013", "real-concept")]
    graph = build_graph([e1], EMPTY_FACETS, good_edges, registry)

    bad_edge = make_about_edge("e-013", "ghost-concept")

    bad_graph = Graph(
        entries=graph.entries,
        concepts=graph.concepts,
        edges=graph.edges + [bad_edge],
        project_hubs=graph.project_hubs,
    )

    violations = validate_graph(bad_graph)
    assert len(violations) >= 1, "Expected at least one violation for dangling to_id"
    assert any("ghost-concept" in v for v in violations), (
        f"Violation message should mention the bad slug; got: {violations}"
    )


# ---------------------------------------------------------------------------
# Test 6: tombstoned entries excluded from graph
# ---------------------------------------------------------------------------


def test_tombstoned_entries_excluded():
    e_active = make_entry("e-014", "proj-a", "family-alpha")
    e_tomb = make_entry("e-015", "proj-a", "family-alpha", status=EntryStatus.tombstone)
    concept = make_concept("any-concept")
    edges = [
        make_about_edge("e-014", "any-concept"),
        make_about_edge("e-015", "any-concept"),  # tombstoned — should be filtered out
    ]
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    graph = build_graph([e_active, e_tomb], EMPTY_FACETS, edges, registry)

    entry_ids = {e.id for e in graph.entries}
    assert "e-014" in entry_ids
    assert "e-015" not in entry_ids, (
        "Tombstoned entry should not appear in graph.entries"
    )

    edge_from_ids = {e.from_id for e in graph.edges}
    assert "e-015" not in edge_from_ids, (
        "Tombstoned entry's edges should be filtered out"
    )


# ---------------------------------------------------------------------------
# Test 7: concepts present even with no linking entries (empty candidate)
# ---------------------------------------------------------------------------


def test_unmatched_concept_appears_as_candidate():
    e1 = make_entry("e-016", "proj-a", "family-alpha")
    concept_linked = make_concept("linked-concept")
    concept_unlinked = make_concept(
        "orphan-concept", ConceptStatus.confirmed
    )  # source=confirmed
    edges = [make_about_edge("e-016", "linked-concept")]
    registry = StubRegistry(
        concepts=[concept_linked, concept_unlinked],
        projects=[make_project("proj-a", "family-alpha")],
    )

    graph = build_graph([e1], EMPTY_FACETS, edges, registry)

    slugs = {c.slug: c for c in graph.concepts}
    assert "orphan-concept" in slugs, "Unmatched concept should still appear in graph"
    orphan = slugs["orphan-concept"]
    # source says confirmed but no entries → effective_status = candidate
    assert orphan.effective_status == ConceptStatus.candidate, (
        f"Unmatched source-confirmed concept should be demoted to candidate; got {orphan.effective_status}"
    )


# ---------------------------------------------------------------------------
# Test 8: duplicate detection in validate_graph
# ---------------------------------------------------------------------------


def test_duplicate_entry_ids_flagged():

    e1 = make_entry("e-017", "proj-a", "family-alpha")
    e2 = make_entry("e-017", "proj-b", "family-beta")  # duplicate id
    concept = make_concept("dup-concept")

    bad_graph = Graph(
        entries=[e1, e2],
        concepts=[concept],
        edges=[],
        project_hubs=[],
    )

    violations = validate_graph(bad_graph)
    assert any("DUPLICATE entry id" in v and "e-017" in v for v in violations), (
        f"Duplicate entry id not flagged; got: {violations}"
    )


# ---------------------------------------------------------------------------
# Smoke test: serialization round-trip
# ---------------------------------------------------------------------------


def test_canonical_json_serialization():
    e1 = make_entry("e-018", "proj-a", "family-alpha")
    e2 = make_entry("e-019", "proj-b", "family-beta")
    concept = make_concept("smoke-concept", ConceptStatus.candidate)
    edges = [
        make_about_edge("e-018", "smoke-concept"),
        make_about_edge("e-019", "smoke-concept"),
    ]
    registry = StubRegistry(
        concepts=[concept],
        projects=[
            make_project("proj-a", "family-alpha"),
            make_project("proj-b", "family-beta"),
        ],
    )

    graph = build_graph([e1, e2], EMPTY_FACETS, edges, registry)
    json_output = graph.to_canonical_json()

    assert isinstance(json_output, str)
    assert len(json_output) > 100, "Serialized graph unexpectedly short"
    assert json_output.endswith("\n"), "canonical_json must end with a newline (§12)"
    # Confirm effective_status is serialized
    assert '"effective_status"' in json_output
    assert '"confirmed"' in json_output
    # Print length for smoke verification
    print(f"\nSmoke: canonical JSON length = {len(json_output)} chars")


# ---------------------------------------------------------------------------
# Helpers for supporting-node tests
# ---------------------------------------------------------------------------


def make_supporting_node(
    sid: str,
    kind: SupportingKind,
    project_id: str | None = None,
    parent_id: str | None = None,
    project_ids: list[str] | None = None,
) -> SupportingNode:
    return SupportingNode(
        id=sid,
        kind=kind,
        title=f"Supporting node {sid}",
        project_id=project_id,
        family=project_id,
        parent_id=parent_id,
        project_ids=project_ids or [],
        date="2026-01-01",
        severity=None,
        body_excerpt="Excerpt.",
        source_path=f"some/path/{sid}.md",
    )


def make_edge(from_id: str, to_id: str, etype: EdgeType) -> Edge:
    return Edge(
        from_id=from_id,
        to_id=to_id,
        type=etype,
        provenance=Provenance.auto,
    )


# ---------------------------------------------------------------------------
# Test 9: review SupportingNode (documents→hub) + finding (detail_of→review) → clean
# ---------------------------------------------------------------------------


def test_supporting_nodes_review_finding_clean():
    e1 = make_entry("e-020", "proj-a", "family-alpha")
    concept = make_concept("some-concept")
    about_edges = [make_about_edge("e-020", "some-concept")]
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    review_sn = make_supporting_node(
        "rv-proj-a-review1", SupportingKind.review, project_id="proj-a"
    )
    finding_sn = make_supporting_node(
        "rv-proj-a-review1-f01",
        SupportingKind.finding,
        parent_id="rv-proj-a-review1",
    )

    supporting_edges = [
        make_edge("rv-proj-a-review1", "proj-a", EdgeType.documents),
        make_edge("rv-proj-a-review1-f01", "rv-proj-a-review1", EdgeType.detail_of),
    ]

    graph = build_graph(
        [e1],
        EMPTY_FACETS,
        about_edges + supporting_edges,
        registry,
        supporting_nodes=[review_sn, finding_sn],
    )

    # Supporting nodes present and sorted
    assert len(graph.supporting_nodes) == 2
    ids = [sn.id for sn in graph.supporting_nodes]
    assert ids == sorted(ids), "supporting_nodes must be sorted by id"
    assert "rv-proj-a-review1" in ids
    assert "rv-proj-a-review1-f01" in ids

    # documents and detail_of edges retained
    edge_types = {e.type for e in graph.edges}
    assert EdgeType.documents in edge_types
    assert EdgeType.detail_of in edge_types

    violations = validate_graph(graph)
    assert violations == [], f"Expected no violations; got: {violations}"


# ---------------------------------------------------------------------------
# Test 10: documents edge to a nonexistent hub is flagged
# ---------------------------------------------------------------------------


def test_documents_edge_to_missing_hub():
    e1 = make_entry("e-021", "proj-a", "family-alpha")
    concept = make_concept("any-concept")
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    review_sn = make_supporting_node(
        "rv-proj-a-review2", SupportingKind.review, project_id="proj-a"
    )
    # documents edge points to a hub that doesn't exist
    bad_supporting_edge = make_edge(
        "rv-proj-a-review2", "nonexistent-hub", EdgeType.documents
    )

    graph = build_graph(
        [e1],
        EMPTY_FACETS,
        [make_about_edge("e-021", "any-concept"), bad_supporting_edge],
        registry,
        supporting_nodes=[review_sn],
    )

    violations = validate_graph(graph)
    assert any("nonexistent-hub" in v for v in violations), (
        f"Expected violation for missing hub; got: {violations}"
    )


# ---------------------------------------------------------------------------
# Test 11: detail_of edge to a missing parent SupportingNode is flagged
# ---------------------------------------------------------------------------


def test_detail_of_edge_to_missing_parent():
    e1 = make_entry("e-022", "proj-a", "family-alpha")
    concept = make_concept("another-concept")
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    finding_sn = make_supporting_node(
        "rv-proj-a-review3-f01",
        SupportingKind.finding,
        parent_id="rv-proj-a-review3",  # parent does NOT exist in supporting_nodes
    )
    bad_detail_edge = make_edge(
        "rv-proj-a-review3-f01", "rv-proj-a-review3", EdgeType.detail_of
    )

    graph = build_graph(
        [e1],
        EMPTY_FACETS,
        [make_about_edge("e-022", "another-concept"), bad_detail_edge],
        registry,
        supporting_nodes=[finding_sn],
    )

    violations = validate_graph(graph)
    assert any("rv-proj-a-review3" in v for v in violations), (
        f"Expected violation for missing parent supporting node; got: {violations}"
    )


# ---------------------------------------------------------------------------
# Test 12: supporting node ids appear in graph.supporting_nodes + serialize correctly
# ---------------------------------------------------------------------------


def test_supporting_nodes_serialization():
    e1 = make_entry("e-023", "proj-a", "family-alpha")
    concept = make_concept("serial-concept")
    registry = StubRegistry(
        concepts=[concept],
        projects=[make_project("proj-a", "family-alpha")],
    )

    review_sn = make_supporting_node(
        "rv-proj-a-serial", SupportingKind.review, project_id="proj-a"
    )
    finding_sn = make_supporting_node(
        "rv-proj-a-serial-f01",
        SupportingKind.finding,
        parent_id="rv-proj-a-serial",
    )

    graph = build_graph(
        [e1],
        EMPTY_FACETS,
        [
            make_about_edge("e-023", "serial-concept"),
            make_edge("rv-proj-a-serial", "proj-a", EdgeType.documents),
            make_edge("rv-proj-a-serial-f01", "rv-proj-a-serial", EdgeType.detail_of),
        ],
        registry,
        supporting_nodes=[review_sn, finding_sn],
    )

    # IDs present
    ids = [sn.id for sn in graph.supporting_nodes]
    assert "rv-proj-a-serial" in ids
    assert "rv-proj-a-serial-f01" in ids

    # Serialize and confirm supporting_nodes key present
    json_output = graph.to_canonical_json()
    assert '"supporting_nodes"' in json_output
    assert '"rv-proj-a-serial"' in json_output
    assert '"rv-proj-a-serial-f01"' in json_output
    assert '"review"' in json_output
    assert '"finding"' in json_output


if __name__ == "__main__":
    # Quick smoke without pytest
    import sys

    test_confirmed_concept_two_families()
    test_same_family_demotes_to_candidate()
    test_curated_singleton_preserved()
    test_project_hubs_created_for_active_entries()
    test_no_hub_for_tombstoned_only_project()
    test_validate_clean_graph()
    test_validate_dangling_from_id()
    test_validate_dangling_to_id()
    test_tombstoned_entries_excluded()
    test_unmatched_concept_appears_as_candidate()
    test_duplicate_entry_ids_flagged()
    test_canonical_json_serialization()
    test_supporting_nodes_review_finding_clean()
    test_documents_edge_to_missing_hub()
    test_detail_of_edge_to_missing_parent()
    test_supporting_nodes_serialization()
    print("All smoke tests passed.")
    sys.exit(0)
