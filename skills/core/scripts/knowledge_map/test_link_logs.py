"""
test_link_logs.py — Unit tests for link_logs.py (follows + created_in edges).

Run with: python3 test_link_logs.py
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

# Ensure the knowledge_map directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from graph_model import (
    EdgeType,
    Entry,
    EntryKind,
    EntryStatus,
    LogNode,
    Provenance,
    SourceShape,
    sha256_hash,
    SCHEMA_VERSION,
)
from link_logs import link_logs


# ---------------------------------------------------------------------------
# Stub Registry (mirrors concept_registry.Registry without importing it)
# ---------------------------------------------------------------------------


@dataclass
class StubRegistry:
    """Minimal stand-in for concept_registry.Registry used in tests."""

    concepts: list = field(default_factory=list)
    projects: list = field(default_factory=list)


EMPTY_REGISTRY = StubRegistry()


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_log(
    log_id: str,
    project_id: str,
    session_date: str | None,
    session_seq: int | None = None,
    family: str = "test-family",
) -> LogNode:
    return LogNode(
        id=log_id,
        project_id=project_id,
        family=family,
        session_date=session_date,
        session_seq=session_seq,
        title=f"Log {log_id}",
        body_excerpt=f"Body of log {log_id}.",
        source_path=f"{project_id}/.living/log/{log_id}.md",
    )


def _make_entry(
    entry_id: str,
    project_id: str,
    date: str | None,
    family: str = "test-family",
) -> Entry:
    return Entry(
        id=entry_id,
        kind=EntryKind.learning,
        source_shape=SourceShape.aggregate_section,
        project_id=project_id,
        family=family,
        source_path=f"{project_id}/.living/learnings.md",
        anchor=f"[{date}] Test entry {entry_id}",
        line_start=1,
        line_end=5,
        title=f"Test entry {entry_id}",
        date=date,
        tags=[],
        body_excerpt=f"Body of {entry_id}.",
        content_hash=sha256_hash(f"body-{entry_id}"),
        status=EntryStatus.active,
        schema_version=SCHEMA_VERSION,
    )


# ---------------------------------------------------------------------------
# follows edge tests
# ---------------------------------------------------------------------------


class TestFollowsEdges(unittest.TestCase):
    def test_single_log_no_follows(self) -> None:
        """A single-log project emits 0 follows edges."""
        log = _make_log("l-00001", "proj-a", "2026-06-10", session_seq=1)
        result = link_logs([log], EMPTY_REGISTRY)
        follows = [e for e in result.edges if e.type == EdgeType.follows]
        self.assertEqual(len(follows), 0, "Single log should produce no follows edges")

    def test_two_logs_one_follows(self) -> None:
        """A 2-log project emits exactly 1 follows edge (newer → older)."""
        log1 = _make_log("l-00001", "proj-a", "2026-06-10", session_seq=1)
        log2 = _make_log("l-00002", "proj-a", "2026-06-11", session_seq=1)
        result = link_logs([log1, log2], EMPTY_REGISTRY)
        follows = [e for e in result.edges if e.type == EdgeType.follows]
        self.assertEqual(
            len(follows), 1, "2-log project should produce exactly 1 follows edge"
        )
        # The later log (l-00002, date 06-11) follows the earlier one (l-00001, date 06-10)
        self.assertEqual(follows[0].from_id, "l-00002")
        self.assertEqual(follows[0].to_id, "l-00001")

    def test_follows_ordered_by_date_then_seq(self) -> None:
        """Ordering is by (session_date, session_seq) — earlier date/seq is 'older'."""
        log_a = _make_log("l-00001", "proj-a", "2026-06-10", session_seq=1)
        log_b = _make_log("l-00002", "proj-a", "2026-06-10", session_seq=2)
        log_c = _make_log("l-00003", "proj-a", "2026-06-11", session_seq=1)
        result = link_logs([log_a, log_b, log_c], EMPTY_REGISTRY)
        follows = [e for e in result.edges if e.type == EdgeType.follows]
        # Sorted order: l-00001 (06-10 seq1) < l-00002 (06-10 seq2) < l-00003 (06-11 seq1)
        # So: l-00002 follows l-00001, l-00003 follows l-00002
        self.assertEqual(len(follows), 2)
        from_ids = [e.from_id for e in follows]
        self.assertIn("l-00002", from_ids)
        self.assertIn("l-00003", from_ids)
        # Verify correct predecessor links
        follows_map = {e.from_id: e.to_id for e in follows}
        self.assertEqual(follows_map["l-00002"], "l-00001")
        self.assertEqual(follows_map["l-00003"], "l-00002")

    def test_follows_are_same_project_only(self) -> None:
        """Logs from different projects do not generate cross-project follows."""
        log_a = _make_log("l-00001", "proj-a", "2026-06-10", session_seq=1)
        log_b = _make_log("l-00002", "proj-b", "2026-06-11", session_seq=1)
        result = link_logs([log_a, log_b], EMPTY_REGISTRY)
        follows = [e for e in result.edges if e.type == EdgeType.follows]
        self.assertEqual(
            len(follows), 0, "Cross-project logs must not generate follows edges"
        )

    def test_follows_provenance_is_auto(self) -> None:
        """follows edges have provenance=auto."""
        log1 = _make_log("l-00001", "proj-a", "2026-06-10", session_seq=1)
        log2 = _make_log("l-00002", "proj-a", "2026-06-11", session_seq=1)
        result = link_logs([log1, log2], EMPTY_REGISTRY)
        follows = [e for e in result.edges if e.type == EdgeType.follows]
        self.assertEqual(follows[0].provenance, Provenance.auto)


# ---------------------------------------------------------------------------
# created_in edge tests
# ---------------------------------------------------------------------------


class TestCreatedInEdges(unittest.TestCase):
    def test_log_date_matches_entry_same_project(self) -> None:
        """A log dated D in project P links to an entry in P dated D."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry = _make_entry("e-00001", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(len(created_in), 1)
        self.assertEqual(created_in[0].from_id, "l-00001")
        self.assertEqual(created_in[0].to_id, "e-00001")

    def test_entry_different_project_not_linked(self) -> None:
        """An entry in a DIFFERENT project is not linked even if dates match."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry_other = _make_entry("e-00001", "proj-b", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry_other])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(
            len(created_in),
            0,
            "Entry from different project must not be linked via created_in",
        )

    def test_log_without_session_date_no_created_in(self) -> None:
        """A log with session_date=None yields no created_in edges."""
        log = _make_log("l-00001", "proj-a", session_date=None)
        entry = _make_entry("e-00001", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(
            len(created_in),
            0,
            "Log with no session_date must produce no created_in edges",
        )

    def test_multiple_same_day_entries_all_linked(self) -> None:
        """Multiple entries on the same day all link to the log."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry1 = _make_entry("e-00001", "proj-a", "2026-06-14")
        entry2 = _make_entry("e-00002", "proj-a", "2026-06-14")
        entry3 = _make_entry("e-00003", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry1, entry2, entry3])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(len(created_in), 3)
        to_ids = {e.to_id for e in created_in}
        self.assertEqual(to_ids, {"e-00001", "e-00002", "e-00003"})
        # All from the same log
        self.assertTrue(all(e.from_id == "l-00001" for e in created_in))

    def test_no_entries_no_created_in(self) -> None:
        """When entries=None (default), no created_in edges are emitted."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        result = link_logs([log], EMPTY_REGISTRY)
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(len(created_in), 0)

    def test_created_in_trigger_is_session_date(self) -> None:
        """created_in edges carry trigger='session-date'."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry = _make_entry("e-00001", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(created_in[0].trigger, "session-date")
        self.assertIsNone(created_in[0].confidence)

    def test_created_in_provenance_is_auto(self) -> None:
        """created_in edges have provenance=auto."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry = _make_entry("e-00001", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(created_in[0].provenance, Provenance.auto)

    def test_entry_different_date_not_linked(self) -> None:
        """An entry with a different date is not linked."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry = _make_entry("e-00001", "proj-a", "2026-06-15")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(len(created_in), 0)

    def test_entry_with_no_date_not_linked(self) -> None:
        """An entry with date=None is not linked (no date to match on)."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry = _make_entry("e-00001", "proj-a", date=None)
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(len(created_in), 0)

    def test_determinism_same_day_entries_sorted(self) -> None:
        """created_in edges are deterministically sorted by (from_id, to_id)."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        # Provide entries in reverse id order
        entry_c = _make_entry("e-00003", "proj-a", "2026-06-14")
        entry_a = _make_entry("e-00001", "proj-a", "2026-06-14")
        entry_b = _make_entry("e-00002", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry_c, entry_a, entry_b])
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        to_ids = [e.to_id for e in created_in]
        self.assertEqual(
            to_ids, sorted(to_ids), "created_in edges must be sorted by to_id"
        )

    def test_mixed_follows_and_created_in(self) -> None:
        """follows and created_in edges coexist correctly."""
        log1 = _make_log("l-00001", "proj-a", "2026-06-13", session_seq=1)
        log2 = _make_log("l-00002", "proj-a", "2026-06-14", session_seq=1)
        entry1 = _make_entry("e-00001", "proj-a", "2026-06-13")
        entry2 = _make_entry("e-00002", "proj-a", "2026-06-14")
        result = link_logs([log1, log2], EMPTY_REGISTRY, entries=[entry1, entry2])
        follows = [e for e in result.edges if e.type == EdgeType.follows]
        created_in = [e for e in result.edges if e.type == EdgeType.created_in]
        self.assertEqual(len(follows), 1)
        self.assertEqual(len(created_in), 2)
        # l-00002 follows l-00001
        self.assertEqual(follows[0].from_id, "l-00002")
        self.assertEqual(follows[0].to_id, "l-00001")
        # each log links to its same-day entry
        created_in_map = {e.from_id: e.to_id for e in created_in}
        self.assertEqual(created_in_map["l-00001"], "e-00001")
        self.assertEqual(created_in_map["l-00002"], "e-00002")


# ---------------------------------------------------------------------------
# Report string tests
# ---------------------------------------------------------------------------


class TestReportString(unittest.TestCase):
    def test_report_includes_counts(self) -> None:
        """Report string includes follows and created_in counts."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        entry = _make_entry("e-00001", "proj-a", "2026-06-14")
        result = link_logs([log], EMPTY_REGISTRY, entries=[entry])
        self.assertTrue(len(result.report) >= 1)
        report_str = result.report[0]
        self.assertIn("follows edges", report_str)
        self.assertIn("created_in edges", report_str)

    def test_report_entries_none_zero_created_in(self) -> None:
        """When entries=None, report shows 0 created_in edges."""
        log = _make_log("l-00001", "proj-a", "2026-06-14", session_seq=1)
        result = link_logs([log], EMPTY_REGISTRY)
        report_str = result.report[0]
        self.assertIn("follows edges", report_str)
        self.assertIn("0 created_in edges", report_str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
