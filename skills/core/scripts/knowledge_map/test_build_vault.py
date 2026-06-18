"""
test_build_vault.py — Unit tests for build_vault.py
"""

import dataclasses
import json
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
    SupportingKind,
    SupportingNode,
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


def _make_graph_with_supporting() -> Graph:
    """Same as _make_graph() but with supporting_nodes populated."""
    g = _make_graph()
    return dataclasses.replace(
        g,
        supporting_nodes=[
            SupportingNode(
                id="rv-proj-alpha-myreview",
                kind=SupportingKind.review,
                title="My Review",
                project_id="proj-alpha",
                family="alpha-family",
                parent_id=None,
                project_ids=["proj-alpha"],
                date="2026-06-10",
                severity=None,
                body_excerpt="Review body.",
                source_path="alpha/.living/outputs/reviews/myreview.md",
            ),
            SupportingNode(
                id="rv-proj-alpha-myreview-f01",
                kind=SupportingKind.finding,
                title="Finding One",
                project_id=None,
                family=None,
                parent_id="rv-proj-alpha-myreview",
                project_ids=[],
                date="2026-06-10",
                severity="major",
                body_excerpt="F1 body.",
                source_path="",
            ),
            SupportingNode(
                id="rv-proj-alpha-myreview-f02",
                kind=SupportingKind.finding,
                title="Finding Two",
                project_id=None,
                family=None,
                parent_id="rv-proj-alpha-myreview",
                project_ids=[],
                date="2026-06-10",
                severity="minor",
                body_excerpt="F2 body.",
                source_path="",
            ),
            SupportingNode(
                id="tx-proj-beta-mytransfer",
                kind=SupportingKind.transfer,
                title="My Transfer",
                project_id="proj-beta",
                family="beta-family",
                parent_id=None,
                project_ids=["proj-beta"],
                date="2026-06-11",
                severity=None,
                body_excerpt="Transfer body.",
                source_path="beta/.living/outputs/knowledge-transfers/mytransfer.md",
            ),
            SupportingNode(
                id="tx-proj-beta-mytransfer-i01",
                kind=SupportingKind.transfer_item,
                title="Transfer Item One",
                project_id=None,
                family=None,
                parent_id="tx-proj-beta-mytransfer",
                project_ids=[],
                date="2026-06-11",
                severity=None,
                body_excerpt="Item body.",
                source_path="",
            ),
        ],
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
        """concepts/bridge/shared-concept.md has definition, cross-project badge, and both entries."""
        p = self.out_dir / "concepts" / "bridge" / "shared-concept.md"
        self.assertTrue(p.exists(), "concepts/bridge/shared-concept.md should exist")
        text = p.read_text(encoding="utf-8")
        print("\n--- concepts/bridge/shared-concept.md ---")
        print(text)
        print("--- end ---")
        self.assertIn("A concept shared across both families.", text)
        self.assertIn("**🔗 cross-project**", text)
        self.assertIn("[[e-00001]]", text)
        self.assertIn("[[e-00002]]", text)

    def test_entry_e00001_frontmatter_and_links(self) -> None:
        """entries/learning/e-00001.md has valid frontmatter, project link, and concept link."""
        p = self.out_dir / "entries" / "learning" / "e-00001.md"
        self.assertTrue(p.exists(), "entries/learning/e-00001.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "Entry note should start with '---'")
        self.assertIn("Project: [[proj-alpha]]", text)
        self.assertIn("[[shared-concept]]", text)

    def test_entry_e00002_project_link(self) -> None:
        """entries/decision/e-00002.md has project link to proj-beta."""
        p = self.out_dir / "entries" / "decision" / "e-00002.md"
        self.assertTrue(p.exists(), "entries/decision/e-00002.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertIn("Project: [[proj-beta]]", text)

    def test_project_alpha_concepts_touched(self) -> None:
        """projects/proj-alpha.md has 'Concepts touched' section with [[shared-concept]]."""
        p = self.out_dir / "projects" / "proj-alpha.md"
        self.assertTrue(p.exists(), "projects/proj-alpha.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertIn("## Concepts touched", text)
        self.assertIn("[[shared-concept]]", text)

    def test_obsidian_graph_json_exists_and_valid(self) -> None:
        """.obsidian/graph.json exists and is valid JSON."""
        p = self.out_dir / ".obsidian" / "graph.json"
        self.assertTrue(p.exists(), ".obsidian/graph.json should exist")
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertIn("colorGroups", data)
        self.assertIsInstance(data["colorGroups"], list)

    def test_moc_exists(self) -> None:
        """000-MAP-OF-CONTENT.md exists."""
        p = self.out_dir / "000-MAP-OF-CONTENT.md"
        self.assertTrue(p.exists(), "000-MAP-OF-CONTENT.md should exist")

    def test_entry_has_aliases(self) -> None:
        """An entry note contains an aliases: line."""
        p = self.out_dir / "entries" / "learning" / "e-00001.md"
        self.assertTrue(p.exists(), "entries/learning/e-00001.md should exist")
        text = p.read_text(encoding="utf-8")
        self.assertIn("aliases:", text)


class TestBuildVaultWithSupporting(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp_dir = tempfile.TemporaryDirectory()
        cls.out_dir = Path(cls.tmp_dir.name)
        graph = _make_graph_with_supporting()
        build_vault(graph, _FACETS, cls.out_dir)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp_dir.cleanup()

    def test_supporting_review_exists(self) -> None:
        p = self.out_dir / "supporting" / "reviews" / "rv-proj-alpha-myreview.md"
        self.assertTrue(p.exists(), f"Expected {p} to exist")

    def test_supporting_finding_f01_exists(self) -> None:
        p = (
            self.out_dir
            / "supporting"
            / "findings"
            / "rv-proj-alpha-myreview"
            / "F01.md"
        )
        self.assertTrue(p.exists(), f"Expected {p} to exist")

    def test_supporting_finding_f02_exists(self) -> None:
        p = (
            self.out_dir
            / "supporting"
            / "findings"
            / "rv-proj-alpha-myreview"
            / "F02.md"
        )
        self.assertTrue(p.exists(), f"Expected {p} to exist")

    def test_supporting_transfer_exists(self) -> None:
        p = self.out_dir / "supporting" / "transfers" / "tx-proj-beta-mytransfer.md"
        self.assertTrue(p.exists(), f"Expected {p} to exist")

    def test_supporting_transfer_item_exists(self) -> None:
        p = (
            self.out_dir
            / "supporting"
            / "transfer-items"
            / "tx-proj-beta-mytransfer"
            / "I01.md"
        )
        self.assertTrue(p.exists(), f"Expected {p} to exist")

    def test_finding_f01_severity_tag(self) -> None:
        p = (
            self.out_dir
            / "supporting"
            / "findings"
            / "rv-proj-alpha-myreview"
            / "F01.md"
        )
        text = p.read_text(encoding="utf-8")
        self.assertIn("severity/major", text)

    def test_finding_f01_parent_wikilink(self) -> None:
        p = (
            self.out_dir
            / "supporting"
            / "findings"
            / "rv-proj-alpha-myreview"
            / "F01.md"
        )
        text = p.read_text(encoding="utf-8")
        self.assertIn("[[rv-proj-alpha-myreview]]", text)

    def test_review_project_wikilink(self) -> None:
        p = self.out_dir / "supporting" / "reviews" / "rv-proj-alpha-myreview.md"
        text = p.read_text(encoding="utf-8")
        self.assertIn("[[proj-alpha]]", text)

    def test_graph_json_color_groups(self) -> None:
        p = self.out_dir / ".obsidian" / "graph.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        groups = data["colorGroups"]
        queries = [g["query"] for g in groups]
        # Must have both path groups and severity tag groups
        self.assertTrue(any("path:" in q for q in queries), "Expected path: groups")
        self.assertTrue(
            any("tag:severity" in q for q in queries), "Expected severity tag groups"
        )
        # All tag:severity groups must come AFTER all path: groups
        last_path_idx = max(i for i, q in enumerate(queries) if q.startswith("path:"))
        first_tag_idx = min(i for i, q in enumerate(queries) if q.startswith("tag:"))
        self.assertGreater(
            first_tag_idx,
            last_path_idx,
            "Severity tag groups must come after path groups",
        )

    def test_graph_json_settings(self) -> None:
        p = self.out_dir / ".obsidian" / "graph.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertAlmostEqual(data["nodeSizeMultiplier"], 2.2)
        self.assertAlmostEqual(data["lineSizeMultiplier"], 0.35)
        self.assertFalse(data["showOrphans"])

    def test_stale_colorgroups_file_removed(self) -> None:
        p = self.out_dir / ".obsidian" / "colorgroups-by-project.json"
        self.assertFalse(
            p.exists(), "Stale colorgroups-by-project.json should not exist"
        )

    def test_keep_graph_config_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / ".obsidian").mkdir()
            custom = {"custom": True, "nodeSizeMultiplier": 99}
            (out / ".obsidian" / "graph.json").write_text(
                json.dumps(custom), encoding="utf-8"
            )
            graph = _make_graph_with_supporting()
            build_vault(graph, _FACETS, out, keep_graph_config=True)
            data = json.loads(
                (out / ".obsidian" / "graph.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                data["nodeSizeMultiplier"], 99, "Custom graph.json should be preserved"
            )

    def test_keep_graph_config_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / ".obsidian").mkdir()
            custom = {"custom": True, "nodeSizeMultiplier": 99}
            (out / ".obsidian" / "graph.json").write_text(
                json.dumps(custom), encoding="utf-8"
            )
            graph = _make_graph_with_supporting()
            build_vault(graph, _FACETS, out, keep_graph_config=False)
            data = json.loads(
                (out / ".obsidian" / "graph.json").read_text(encoding="utf-8")
            )
            self.assertAlmostEqual(
                data["nodeSizeMultiplier"],
                2.2,
                msg="graph.json should be overwritten",
            )


if __name__ == "__main__":
    unittest.main()
