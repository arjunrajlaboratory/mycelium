"""
test_extract_reviews.py — Unit tests for extract_reviews.py

Run with: python3 test_extract_reviews.py -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the knowledge_map directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from graph_model import EdgeType, ProjectMeta, SupportingKind
from extract_reviews import extract_reviews, ExtractReviewsResult


# ---------------------------------------------------------------------------
# Fixture content
# ---------------------------------------------------------------------------

REVIEW_FIXTURE = """\
# Review — SCF M0–M3 Adversarial Review

**Scope**: M0–M3 implementation
**Files reviewed**: src/pipeline/*.py
**Sub-agents**: 5 specialist agents

## Executive Summary
This review covers the full M0–M3 implementation.

## Findings

## [2026-06-11] F1 — Faithful cells excluded [Major]

**Fix**: Apply per-item exclusion keyed on surviving items.
**Why it matters**: Biases rate up.

## [2026-06-11] F2 — CONTAM0 zero call sites [Critical]

**Fix**: Wire constraint into both paths.
**Why it matters**: Gate is computed but never consumed.
"""

TRANSFER_FIXTURE = """\
# Knowledge Transfer Report — 2026-06-13

**Projects scanned**: Scientific Communication Fragility, AutoReview

## Transfers Identified

### 1. Gate wiring pattern
Details about gates.

### 2. Manski interval approach
Details about partial identification.
"""

EMPTY_REVIEW_FIXTURE = """\
# Review — Empty Review
No findings section here.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_projects(root: Path) -> list[ProjectMeta]:
    """Build two projects: scf and autoreview."""
    return [
        ProjectMeta(
            id="scf",
            name="Scientific Communication Fragility",
            path="scf",
            family="science",
            has_living=True,
        ),
        ProjectMeta(
            id="autoreview",
            name="AutoReview",
            path="autoreview",
            family="science",
            has_living=True,
        ),
    ]


def _setup_fixtures(root: Path, projects: list[ProjectMeta]) -> None:
    """Write fixture files into the tmpdir tree under the first project (scf)."""
    scf = projects[0]
    scf_dir = root / scf.path

    reviews_dir = scf_dir / ".living" / "outputs" / "reviews"
    transfers_dir = scf_dir / ".living" / "outputs" / "knowledge-transfers"

    _write(reviews_dir / "2026-06-11-scf-m0-m3.md", REVIEW_FIXTURE)
    _write(reviews_dir / "2026-05-01-empty-review.md", EMPTY_REVIEW_FIXTURE)
    _write(transfers_dir / "2026-06-13-001-science.md", TRANSFER_FIXTURE)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReviewPrimary(unittest.TestCase):
    """Review primary node id, title, date."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)
        self.result: ExtractReviewsResult = extract_reviews(self.root, self.projects)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_review_primary_id(self) -> None:
        """Review primary node has expected deterministic id."""
        ids = [n.id for n in self.result.supporting_nodes]
        self.assertIn(
            "rv-scf-2026-06-11-scf-m0-m3",
            ids,
            msg=f"Expected review primary id not found; got ids: {ids}",
        )

    def test_review_primary_kind(self) -> None:
        """Review primary node has kind=review."""
        node = next(
            (
                n
                for n in self.result.supporting_nodes
                if n.id == "rv-scf-2026-06-11-scf-m0-m3"
            ),
            None,
        )
        self.assertIsNotNone(node, "Review primary node not found")
        self.assertEqual(node.kind, SupportingKind.review)

    def test_review_primary_project_id(self) -> None:
        """Review primary node has project_id=scf."""
        node = next(
            (
                n
                for n in self.result.supporting_nodes
                if n.id == "rv-scf-2026-06-11-scf-m0-m3"
            ),
            None,
        )
        self.assertIsNotNone(node)
        self.assertEqual(node.project_id, "scf")


class TestFindings(unittest.TestCase):
    """Finding sub-nodes: count, severity, ids."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)
        self.result: ExtractReviewsResult = extract_reviews(self.root, self.projects)
        self.review_id = "rv-scf-2026-06-11-scf-m0-m3"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _findings(self) -> list:
        return [
            n
            for n in self.result.supporting_nodes
            if n.kind == SupportingKind.finding and n.parent_id == self.review_id
        ]

    def test_exactly_two_findings(self) -> None:
        """Exactly 2 findings parsed from the review fixture."""
        findings = self._findings()
        self.assertEqual(
            len(findings),
            2,
            msg=f"Expected 2 findings; got {len(findings)}: {[n.id for n in findings]}",
        )

    def test_finding_ids(self) -> None:
        """Finding ids are ...-f01 and ...-f02."""
        expected = {
            f"{self.review_id}-f01",
            f"{self.review_id}-f02",
        }
        actual = {n.id for n in self._findings()}
        self.assertEqual(actual, expected, msg=f"Unexpected finding ids: {actual}")

    def test_finding_severities(self) -> None:
        """Findings have severities major and critical (case-insensitive)."""
        severities = {n.severity.lower() for n in self._findings() if n.severity}
        self.assertIn("major", severities, msg=f"Expected 'major' in {severities}")
        self.assertIn(
            "critical", severities, msg=f"Expected 'critical' in {severities}"
        )

    def test_finding_kind(self) -> None:
        """Finding nodes have kind=finding."""
        for node in self._findings():
            self.assertEqual(node.kind, SupportingKind.finding)

    def test_finding_parent_id(self) -> None:
        """Finding nodes have parent_id pointing to review."""
        for node in self._findings():
            self.assertEqual(node.parent_id, self.review_id)


class TestZeroFindingFallback(unittest.TestCase):
    """Empty review: only primary node; report contains a note."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)
        self.result: ExtractReviewsResult = extract_reviews(self.root, self.projects)
        self.empty_review_id = "rv-scf-2026-05-01-empty-review"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_empty_review_primary_exists(self) -> None:
        """Primary node for the empty review is present."""
        ids = [n.id for n in self.result.supporting_nodes]
        self.assertIn(
            self.empty_review_id,
            ids,
            msg=f"Empty review primary not found in {ids}",
        )

    def test_empty_review_no_findings(self) -> None:
        """No finding sub-nodes emitted for the empty review."""
        findings = [
            n
            for n in self.result.supporting_nodes
            if n.kind == SupportingKind.finding and n.parent_id == self.empty_review_id
        ]
        self.assertEqual(
            len(findings),
            0,
            msg=f"Expected 0 findings for empty review; got {len(findings)}",
        )

    def test_empty_review_report_note(self) -> None:
        """Report contains a note about the zero-finding fallback."""
        notes = [r for r in self.result.report if "NO FINDINGS" in r]
        self.assertGreater(
            len(notes),
            0,
            msg=f"Expected a NO FINDINGS note in report; got: {self.result.report}",
        )


class TestTransferPrimary(unittest.TestCase):
    """Transfer primary node id, title, kind."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)
        self.result: ExtractReviewsResult = extract_reviews(self.root, self.projects)
        self.transfer_id = "tx-scf-2026-06-13-001-science"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_transfer_primary_id(self) -> None:
        """Transfer primary node has expected deterministic id."""
        ids = [n.id for n in self.result.supporting_nodes]
        self.assertIn(
            self.transfer_id,
            ids,
            msg=f"Expected transfer primary id not found; got: {ids}",
        )

    def test_transfer_primary_kind(self) -> None:
        """Transfer primary node has kind=transfer."""
        node = next(
            (n for n in self.result.supporting_nodes if n.id == self.transfer_id), None
        )
        self.assertIsNotNone(node)
        self.assertEqual(node.kind, SupportingKind.transfer)

    def test_transfer_primary_project_id(self) -> None:
        """Transfer primary node project_id is the owning project."""
        node = next(
            (n for n in self.result.supporting_nodes if n.id == self.transfer_id), None
        )
        self.assertIsNotNone(node)
        self.assertEqual(node.project_id, "scf")


class TestTransferItems(unittest.TestCase):
    """Transfer item sub-nodes: count, ids, kind."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)
        self.result: ExtractReviewsResult = extract_reviews(self.root, self.projects)
        self.transfer_id = "tx-scf-2026-06-13-001-science"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _items(self) -> list:
        return [
            n
            for n in self.result.supporting_nodes
            if n.kind == SupportingKind.transfer_item
            and n.parent_id == self.transfer_id
        ]

    def test_exactly_two_items(self) -> None:
        """Exactly 2 transfer items parsed from the transfer fixture."""
        items = self._items()
        self.assertEqual(
            len(items),
            2,
            msg=f"Expected 2 transfer items; got {len(items)}: {[n.id for n in items]}",
        )

    def test_item_ids(self) -> None:
        """Transfer item ids are ...-i01 and ...-i02."""
        expected = {
            f"{self.transfer_id}-i01",
            f"{self.transfer_id}-i02",
        }
        actual = {n.id for n in self._items()}
        self.assertEqual(actual, expected, msg=f"Unexpected item ids: {actual}")

    def test_item_kind(self) -> None:
        """Transfer item nodes have kind=transfer_item."""
        for node in self._items():
            self.assertEqual(node.kind, SupportingKind.transfer_item)

    def test_item_parent_id(self) -> None:
        """Transfer item nodes have parent_id pointing to transfer."""
        for node in self._items():
            self.assertEqual(node.parent_id, self.transfer_id)


class TestEdges(unittest.TestCase):
    """Edge correctness: documents and detail_of."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)
        self.result: ExtractReviewsResult = extract_reviews(self.root, self.projects)
        self.review_id = "rv-scf-2026-06-11-scf-m0-m3"
        self.transfer_id = "tx-scf-2026-06-13-001-science"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_review_documents_project(self) -> None:
        """documents edge from review → project (scf) exists."""
        docs_edges = [
            e
            for e in self.result.edges
            if e.from_id == self.review_id
            and e.to_id == "scf"
            and e.type == EdgeType.documents
        ]
        self.assertGreater(
            len(docs_edges),
            0,
            msg=f"Expected documents edge review→scf; edges from review: "
            f"{[(e.from_id, e.to_id, e.type) for e in self.result.edges if e.from_id == self.review_id]}",
        )

    def test_finding_detail_of_review(self) -> None:
        """detail_of edges from findings → review exist."""
        f01_id = f"{self.review_id}-f01"
        detail_edges = [
            e
            for e in self.result.edges
            if e.from_id == f01_id
            and e.to_id == self.review_id
            and e.type == EdgeType.detail_of
        ]
        self.assertGreater(
            len(detail_edges),
            0,
            msg=f"Expected detail_of edge f01→review; all edges: {[(e.from_id, e.to_id, e.type) for e in self.result.edges]}",
        )

    def test_transfer_documents_projects(self) -> None:
        """documents edges from transfer → each resolved project exist."""
        docs_edges = [
            e
            for e in self.result.edges
            if e.from_id == self.transfer_id and e.type == EdgeType.documents
        ]
        target_ids = {e.to_id for e in docs_edges}
        # Should link to both scf and autoreview
        self.assertIn(
            "scf",
            target_ids,
            msg=f"Expected documents edge transfer→scf; got targets: {target_ids}",
        )
        self.assertIn(
            "autoreview",
            target_ids,
            msg=f"Expected documents edge transfer→autoreview; got targets: {target_ids}",
        )

    def test_item_detail_of_transfer(self) -> None:
        """detail_of edges from items → transfer exist."""
        i01_id = f"{self.transfer_id}-i01"
        detail_edges = [
            e
            for e in self.result.edges
            if e.from_id == i01_id
            and e.to_id == self.transfer_id
            and e.type == EdgeType.detail_of
        ]
        self.assertGreater(
            len(detail_edges),
            0,
            msg="Expected detail_of edge i01→transfer",
        )


class TestProjectNameResolution(unittest.TestCase):
    """Project name resolution and unresolved name handling."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_scf_name_resolves(self) -> None:
        """'Scientific Communication Fragility' resolves to project id 'scf'."""
        scf = self.projects[0]
        scf_dir = self.root / scf.path
        transfers_dir = scf_dir / ".living" / "outputs" / "knowledge-transfers"
        _write(transfers_dir / "2026-06-13-001-science.md", TRANSFER_FIXTURE)

        result = extract_reviews(self.root, self.projects)
        transfer_id = "tx-scf-2026-06-13-001-science"
        node = next((n for n in result.supporting_nodes if n.id == transfer_id), None)
        self.assertIsNotNone(
            node,
            f"Transfer node not found; ids: {[n.id for n in result.supporting_nodes]}",
        )
        self.assertIn(
            "scf",
            node.project_ids,
            msg=f"Expected 'scf' in project_ids; got: {node.project_ids}",
        )

    def test_autoreview_name_resolves(self) -> None:
        """'AutoReview' resolves to project id 'autoreview'."""
        scf = self.projects[0]
        scf_dir = self.root / scf.path
        transfers_dir = scf_dir / ".living" / "outputs" / "knowledge-transfers"
        _write(transfers_dir / "2026-06-13-001-science.md", TRANSFER_FIXTURE)

        result = extract_reviews(self.root, self.projects)
        transfer_id = "tx-scf-2026-06-13-001-science"
        node = next((n for n in result.supporting_nodes if n.id == transfer_id), None)
        self.assertIsNotNone(node)
        self.assertIn(
            "autoreview",
            node.project_ids,
            msg=f"Expected 'autoreview' in project_ids; got: {node.project_ids}",
        )

    def test_unresolved_project_name_adds_report_note(self) -> None:
        """Unresolved project name in transfer adds a note to report."""
        scf = self.projects[0]
        scf_dir = self.root / scf.path
        transfers_dir = scf_dir / ".living" / "outputs" / "knowledge-transfers"

        unresolved_transfer = """\
# Knowledge Transfer Report — 2026-06-14

**Projects scanned**: Scientific Communication Fragility, NonExistentProject42

## Transfers Identified

### 1. Some item
Details.
"""
        _write(transfers_dir / "2026-06-14-001-unresolved.md", unresolved_transfer)

        result = extract_reviews(self.root, self.projects)
        unresolved_notes = [r for r in result.report if "UNRESOLVED PROJECT" in r]
        self.assertGreater(
            len(unresolved_notes),
            0,
            msg=f"Expected UNRESOLVED PROJECT note in report; got: {result.report}",
        )
        # Unresolved project should not appear in project_ids
        transfer_id = "tx-scf-2026-06-14-001-unresolved"
        node = next((n for n in result.supporting_nodes if n.id == transfer_id), None)
        self.assertIsNotNone(node)
        self.assertNotIn(
            "nonexistentproject42",
            [pid.lower() for pid in node.project_ids],
        )


class TestDeterminism(unittest.TestCase):
    """Two calls with same inputs produce identical output (deterministic)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.projects = _make_projects(self.root)
        _setup_fixtures(self.root, self.projects)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_deterministic_node_ids(self) -> None:
        """Node ids are identical across two separate calls."""
        r1 = extract_reviews(self.root, self.projects)
        r2 = extract_reviews(self.root, self.projects)
        ids1 = [n.id for n in r1.supporting_nodes]
        ids2 = [n.id for n in r2.supporting_nodes]
        self.assertEqual(ids1, ids2, msg="Node ids differ between runs")

    def test_deterministic_edge_order(self) -> None:
        """Edges are in identical order across two separate calls."""
        r1 = extract_reviews(self.root, self.projects)
        r2 = extract_reviews(self.root, self.projects)
        edges1 = [(e.from_id, e.to_id, e.type.value) for e in r1.edges]
        edges2 = [(e.from_id, e.to_id, e.type.value) for e in r2.edges]
        self.assertEqual(edges1, edges2, msg="Edge order differs between runs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
