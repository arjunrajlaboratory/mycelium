"""
test_build_vault.py — Unit tests for build_vault.py
"""

import tempfile
import unittest
from pathlib import Path

from graph_model import (
    ConceptStatus,
    Concept,
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
    SourceShape,
    Stage,
    StageSource,
)
from build_vault import build_vault


def _make_graph() -> Graph:
    entries = [
        Entry(
            id="e-00001",
            project_id="proj-alpha",
            family="alpha-family",
            title="Alpha Learning One",
            kind=EntryKind.learning,
            source_shape=SourceShape.aggregate_section,
            source_path="alpha/.living/learnings.md",
            anchor="anchor-1",
            line_start=1,
            line_end=5,
            date="2026-06-14",
            tags=[],
            body_excerpt="Alpha body excerpt.",
            content_hash="sha256:abc",
            status=EntryStatus.active,
        ),
        Entry(
            id="e-00002",
            project_id="proj-beta",
            family="beta-family",
            title="Beta Decision One",
            kind=EntryKind.decision,
            source_shape=SourceShape.aggregate_section,
            source_path="beta/.living/decisions.md",
            anchor="anchor-2",
            line_start=10,
            line_end=15,
            date="2026-06-13",
            tags=[],
            body_excerpt="Beta body excerpt.",
            content_hash="sha256:def",
            status=EntryStatus.active,
        ),
    ]

    concepts = [
        Concept(
            slug="shared-concept",
            label="Shared Concept",
            definition="A concept shared across both families.",
            status=ConceptStatus.confirmed,
            effective_status=ConceptStatus.confirmed,
            aliases=[],
            positive_keywords=[],
            negative_keywords=[],
            required_any=[],
            project_scope=None,
            match_mode=MatchMode.alias,
            relates=[],
            parent=None,
        ),
    ]

    edges = [
        Edge(
            from_id="e-00001",
            to_id="shared-concept",
            type=EdgeType.about,
            provenance=Provenance.auto,
            confidence="1.00",
            trigger=None,
        ),
        Edge(
            from_id="e-00002",
            to_id="shared-concept",
            type=EdgeType.about,
            provenance=Provenance.auto,
            confidence="1.00",
            trigger=None,
        ),
    ]

    project_hubs = [
        ProjectHub(
            project_id="proj-alpha", name="Alpha Project", family="alpha-family"
        ),
        ProjectHub(project_id="proj-beta", name="Beta Project", family="beta-family"),
    ]

    return Graph(
        entries=entries,
        concepts=concepts,
        edges=edges,
        project_hubs=project_hubs,
    )


_FACETS: dict[str, Facet] = {
    "e-00001": Facet(stage=Stage.analysis, stage_source=StageSource.path),
    "e-00002": Facet(stage=Stage.planning, stage_source=StageSource.keyword),
}


class TestBuildVault(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.out_dir = Path(cls.tmp_dir.name)
        graph = _make_graph()
        build_vault(graph, _FACETS, cls.out_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp_dir.cleanup()

    def test_project_alpha_stage_heading_and_entry(self) -> None:
        """projects/proj-alpha.md has an Analysis section with [[e-00001]]."""
        p = self.out_dir / "projects" / "proj-alpha.md"
        self.assertTrue(p.exists(), "projects/proj-alpha.md should exist")
        text = p.read_text(encoding="utf-8")
        has_analysis = any(line.startswith("## Analysis") for line in text.splitlines())
        self.assertTrue(has_analysis, f"Expected '## Analysis' heading in:\n{text}")
        self.assertIn("[[e-00001]]", text)

    def test_concept_shared_concept(self) -> None:
        """concepts/shared-concept.md has definition, cross-project badge, and both entries."""
        p = self.out_dir / "concepts" / "shared-concept.md"
        self.assertTrue(p.exists(), "concepts/shared-concept.md should exist")
        text = p.read_text(encoding="utf-8")
        print("\n--- concepts/shared-concept.md ---")
        print(text)
        print("--- end ---")
        self.assertIn("A concept shared across both families.", text)
        self.assertIn("**🔗 cross-project**", text)
        self.assertIn("[[e-00001]]", text)
        self.assertIn("[[e-00002]]", text)

    def test_entry_e00001_frontmatter_and_links(self) -> None:
        """entries/e-00001.md has valid frontmatter, project link, and concept link."""
        p = self.out_dir / "entries" / "e-00001.md"
        self.assertTrue(p.exists(), "entries/e-00001.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "Entry note should start with '---'")
        self.assertIn("Project: [[proj-alpha]]", text)
        self.assertIn("[[shared-concept]]", text)

    def test_entry_e00002_project_link(self) -> None:
        """entries/e-00002.md has project link to proj-beta."""
        p = self.out_dir / "entries" / "e-00002.md"
        self.assertTrue(p.exists(), "entries/e-00002.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertIn("Project: [[proj-beta]]", text)

    def test_project_alpha_concepts_touched(self) -> None:
        """projects/proj-alpha.md has 'Concepts touched' section with [[shared-concept]]."""
        p = self.out_dir / "projects" / "proj-alpha.md"
        self.assertTrue(p.exists(), "projects/proj-alpha.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertIn("## Concepts touched", text)
        self.assertIn("[[shared-concept]]", text)


if __name__ == "__main__":
    unittest.main()
