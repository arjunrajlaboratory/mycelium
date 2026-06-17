"""
test_render_views.py — Tests for render_views.py.

Builds a small Graph + facets with:
- one genuinely cross-family concept (confirmed, spans 2 families)
- one same-family concept that is confirmed in source but candidate in effective (stale/would-demote)
- one curated_singleton concept (confirmed in source, curated_singleton effective)

Assertions:
1. cross-project-concepts.md names the cross-family concept and NOT the demoted one
2. lifecycle.md contains the project + a stage heading
3. stale-concepts.md lists the demoted concept
4. link-diff.md with no baseline says skipped
5. link-diff.md with a baseline reports added/removed edges
"""

import json
import sys
import tempfile
from pathlib import Path

# Flat import: the directory is on sys.path when run from its own dir.
# When run via pytest from a parent dir, we add the directory explicitly.
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

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
    ProjectHub,
    Provenance,
    SCHEMA_VERSION,
    SourceShape,
    Stage,
    StageSource,
    sha256_hash,
)
from render_views import render_views


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _entry(
    id: str,
    title: str,
    project_id: str,
    family: str,
    stage: Stage = Stage.analysis,
) -> Entry:
    return Entry(
        id=id,
        kind=EntryKind.learning,
        source_shape=SourceShape.aggregate_section,
        project_id=project_id,
        family=family,
        source_path=f"{project_id}/.living/learnings.md",
        anchor=f"[2026-01-01] {title}",
        line_start=1,
        line_end=5,
        title=title,
        date="2026-01-01",
        tags=[],
        body_excerpt=f"Body of {title}.",
        content_hash=sha256_hash(title),
        status=EntryStatus.active,
        schema_version=SCHEMA_VERSION,
    )


def _facet(stage: Stage = Stage.analysis) -> Facet:
    return Facet(stage=stage, stage_source=StageSource.keyword)


def _concept(
    slug: str,
    label: str,
    source_status: ConceptStatus,
    effective_status: ConceptStatus | None,
) -> Concept:
    return Concept(
        slug=slug,
        label=label,
        definition=f"Definition of {label}.",
        status=source_status,
        aliases=[label.lower()],
        positive_keywords=[label.lower()],
        negative_keywords=[],
        required_any=[],
        project_scope=None,
        match_mode=MatchMode.alias,
        relates=[],
        parent=None,
        effective_status=effective_status,
    )


def _about_edge(from_id: str, to_id: str, trigger: str = "alias-match") -> Edge:
    return Edge(
        from_id=from_id,
        to_id=to_id,
        type=EdgeType.about,
        provenance=Provenance.auto,
        trigger=trigger,
        confidence="1.00",
    )


# ---------------------------------------------------------------------------
# Build the shared fixture graph
# ---------------------------------------------------------------------------


def _build_fixture():
    """
    Projects:
      proj-alpha  family=alpha-family
      proj-beta   family=beta-family

    Concepts:
      cross-concept  confirmed in source, confirmed effective  (spans alpha + beta)
      stale-concept  confirmed in source, candidate effective  (only alpha — would-demote)
      lone-concept   curated_singleton in source, curated_singleton effective

    Entries:
      e-alpha-1  proj-alpha / alpha-family → cross-concept
      e-alpha-2  proj-alpha / alpha-family → stale-concept
      e-beta-1   proj-beta  / beta-family  → cross-concept
    """
    e_alpha_1 = _entry("e-alpha-1", "Alpha cross entry", "proj-alpha", "alpha-family")
    e_alpha_2 = _entry("e-alpha-2", "Alpha stale entry", "proj-alpha", "alpha-family")
    e_beta_1 = _entry(
        "e-beta-1", "Beta cross entry", "proj-beta", "beta-family", stage=Stage.planning
    )

    facets = {
        "e-alpha-1": _facet(Stage.analysis),
        "e-alpha-2": _facet(Stage.analysis),
        "e-beta-1": _facet(Stage.planning),
    }

    cross_concept = _concept(
        "cross-concept",
        "Cross Concept",
        source_status=ConceptStatus.confirmed,
        effective_status=ConceptStatus.confirmed,
    )
    stale_concept = _concept(
        "stale-concept",
        "Stale Concept",
        source_status=ConceptStatus.confirmed,
        effective_status=ConceptStatus.candidate,  # would-demote
    )
    lone_concept = _concept(
        "lone-concept",
        "Lone Concept",
        source_status=ConceptStatus.curated_singleton,
        effective_status=ConceptStatus.curated_singleton,
    )

    edges = [
        _about_edge("e-alpha-1", "cross-concept"),
        _about_edge("e-beta-1", "cross-concept"),
        _about_edge("e-alpha-2", "stale-concept"),
    ]

    hubs = [
        ProjectHub(
            project_id="proj-alpha", name="Project Alpha", family="alpha-family"
        ),
        ProjectHub(project_id="proj-beta", name="Project Beta", family="beta-family"),
    ]

    graph = Graph(
        entries=[e_alpha_1, e_alpha_2, e_beta_1],
        concepts=[cross_concept, stale_concept, lone_concept],
        edges=edges,
        project_hubs=hubs,
    )
    return graph, facets


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_cross_project_concepts_names_cross_family_concept():
    """cross-project-concepts.md must include the confirmed cross-family concept."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "cross-project-concepts.md").read_text(encoding="utf-8")

    assert "Cross Concept" in content, "confirmed cross-family concept should appear"
    assert "cross-concept" in content or "Cross Concept" in content


def test_cross_project_concepts_excludes_demoted_concept():
    """cross-project-concepts.md must NOT include the would-demote (stale) concept."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "cross-project-concepts.md").read_text(encoding="utf-8")

    # stale-concept has effective_status=candidate, so must not appear in cross-project view
    assert "Stale Concept" not in content, (
        "demoted concept must not appear in cross-project view"
    )
    assert "stale-concept" not in content, "demoted concept slug must not appear"


def test_cross_project_concepts_summary_line():
    """First line of cross-project-concepts.md is the N-concept summary."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "cross-project-concepts.md").read_text(encoding="utf-8")

    first_line = content.splitlines()[0]
    # Only 1 confirmed concept in fixture
    assert (
        "1 concept" in first_line.lower() or "concepts connect" in first_line.lower()
    ), f"unexpected summary line: {first_line!r}"


def test_lifecycle_contains_project_and_stage():
    """lifecycle.md must contain both projects and at least one stage heading."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "lifecycle.md").read_text(encoding="utf-8")

    assert "Project Alpha" in content, "project name should appear"
    assert "proj-alpha" in content, "project id should appear"
    assert "Project Beta" in content, "second project should appear"
    # Stage headings: analysis entries go under Analysis; planning entries under Planning
    assert "### Analysis" in content or "### analysis" in content.lower(), (
        "Analysis stage heading expected"
    )


def test_lifecycle_heuristic_note():
    """lifecycle.md must carry the heuristic note."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "lifecycle.md").read_text(encoding="utf-8")

    assert "heuristic" in content.lower(), (
        "lifecycle must note stage assignment is heuristic"
    )


def test_stale_concepts_lists_demoted():
    """stale-concepts.md must list the concept that is confirmed in source but candidate effective."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "stale-concepts.md").read_text(encoding="utf-8")

    assert "Stale Concept" in content or "stale-concept" in content, (
        "would-demote concept must appear in stale-concepts.md"
    )
    assert "would-demote" in content.lower() or "demoted" in content.lower(), (
        "stale-concepts.md must note the demotion"
    )


def test_stale_concepts_excludes_cross_family():
    """stale-concepts.md must NOT list the genuinely confirmed cross-family concept."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "stale-concepts.md").read_text(encoding="utf-8")

    assert "Cross Concept" not in content, (
        "confirmed cross-family concept must not appear as stale"
    )


def test_stale_concepts_report_only_note():
    """stale-concepts.md must carry the report-only caveat."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "stale-concepts.md").read_text(encoding="utf-8")

    assert "report-only" in content.lower() or "never" in content.lower(), (
        "stale-concepts.md must say report-only / never mutates"
    )


def test_link_diff_no_baseline_says_skipped():
    """link-diff.md with no baseline must say 'No baseline provided — diff skipped.'"""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir, baseline_path=None)

        content = (out_dir / "link-diff.md").read_text(encoding="utf-8")

    assert "No baseline provided" in content, "must say no baseline"
    assert "diff skipped" in content.lower() or "skipped" in content.lower()


def test_link_diff_with_missing_baseline_path():
    """Passing a nonexistent baseline_path should also result in a skip notice."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(
            graph, facets, out_dir, baseline_path=Path(tmpdir) / "nonexistent.json"
        )

        content = (out_dir / "link-diff.md").read_text(encoding="utf-8")

    assert "No baseline provided" in content


def test_link_diff_with_baseline_reports_added_removed():
    """With a baseline that is missing one current edge and has one extra edge."""
    graph, facets = _build_fixture()

    # Baseline: has e-alpha-2→stale-concept (same as current) + a phantom edge not in current
    baseline_edges = [
        {
            "from": "e-alpha-2",
            "to": "stale-concept",
            "type": "about",
            "provenance": "auto",
        },
        {
            "from": "e-phantom",
            "to": "phantom-concept",
            "type": "about",
            "provenance": "auto",
        },
    ]
    baseline_data = {"schema_version": 1, "edges": baseline_edges}

    with tempfile.TemporaryDirectory() as tmpdir:
        baseline_path = Path(tmpdir) / "knowledge-graph.json"
        baseline_path.write_text(json.dumps(baseline_data), encoding="utf-8")

        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir, baseline_path=baseline_path)

        content = (out_dir / "link-diff.md").read_text(encoding="utf-8")

    # Added edges: e-alpha-1→cross-concept and e-beta-1→cross-concept (not in baseline)
    assert "e-alpha-1" in content, "added edge from e-alpha-1 should appear"
    assert "e-beta-1" in content, "added edge from e-beta-1 should appear"
    # Removed edge: e-phantom→phantom-concept (in baseline, not in current)
    assert "e-phantom" in content, "removed phantom edge should appear"


def test_out_dir_created_if_absent():
    """render_views must create out_dir if it does not exist."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "nested" / "views"
        assert not out_dir.exists()
        render_views(graph, facets, out_dir)
        assert out_dir.exists()


def test_all_files_written():
    """All five view files must be written."""
    graph, facets = _build_fixture()
    expected = {
        "cross-project-concepts.md",
        "lifecycle.md",
        "elevation-ladder.md",
        "stale-concepts.md",
        "link-diff.md",
    }
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)
        written = {p.name for p in out_dir.iterdir()}

    assert expected.issubset(written), f"missing files: {expected - written}"


def test_elevation_ladder_has_confirmed_section():
    """elevation-ladder.md must contain a Confirmed section with the cross-family concept."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "elevation-ladder.md").read_text(encoding="utf-8")

    assert "Confirmed" in content
    assert "Cross Concept" in content


def test_elevation_ladder_candidate_section():
    """elevation-ladder.md must contain the stale concept under Candidate."""
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        content = (out_dir / "elevation-ladder.md").read_text(encoding="utf-8")

    assert "Candidate" in content
    assert "Stale Concept" in content


def test_no_timestamps_in_output():
    """No wall-clock timestamps should appear in any generated file (§12)."""
    import re

    graph, facets = _build_fixture()
    # Match ISO-date patterns that look like now() output (e.g. 2026-06-14T...)
    timestamp_re = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)

        for md_file in out_dir.glob("*.md"):
            content = md_file.read_text(encoding="utf-8")
            assert not timestamp_re.search(content), (
                f"wall-clock timestamp found in {md_file.name}"
            )


# ---------------------------------------------------------------------------
# Sample output helper (for the report)
# ---------------------------------------------------------------------------


def _print_sample_cross_project(n_lines: int = 15) -> None:
    graph, facets = _build_fixture()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_dir = Path(tmpdir) / "views"
        render_views(graph, facets, out_dir)
        content = (out_dir / "cross-project-concepts.md").read_text(encoding="utf-8")

    lines = content.splitlines()
    print(f"\n--- cross-project-concepts.md (first {n_lines} lines) ---")
    for line in lines[:n_lines]:
        print(line)
    print("---")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import traceback

    tests = [
        test_cross_project_concepts_names_cross_family_concept,
        test_cross_project_concepts_excludes_demoted_concept,
        test_cross_project_concepts_summary_line,
        test_lifecycle_contains_project_and_stage,
        test_lifecycle_heuristic_note,
        test_stale_concepts_lists_demoted,
        test_stale_concepts_excludes_cross_family,
        test_stale_concepts_report_only_note,
        test_link_diff_no_baseline_says_skipped,
        test_link_diff_with_missing_baseline_path,
        test_link_diff_with_baseline_reports_added_removed,
        test_out_dir_created_if_absent,
        test_all_files_written,
        test_elevation_ladder_has_confirmed_section,
        test_elevation_ladder_candidate_section,
        test_no_timestamps_in_output,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {test.__name__}")
            traceback.print_exc()
            failed += 1

    print(f"\n{passed}/{passed + failed} tests passed.")

    _print_sample_cross_project()
