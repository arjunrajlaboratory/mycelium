"""
test_extract_entries.py — Unit tests for extract_entries.py

Run with: python3 test_extract_entries.py -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the knowledge_map directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from graph_model import EntryKind, SourceShape
from extract_entries import extract_entries, ExtractResult
from graph_model import ProjectMeta


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestExtractEntries(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

        # -------------------------------------------------------------------
        # Fixture 1 — proj-a: .living/learnings.md (aggregate_section)
        # -------------------------------------------------------------------
        _write(
            self.root / "proj-a" / ".living" / "learnings.md",
            """\
# My Learnings

## [2026-01-15] First Learning
This is the body of the first learning.
It spans multiple lines.

### Sub-context
This is a sub-context, NOT a new entry.

### [2026-02-10] Nested dated entry
This is a nested entry that has a date in its heading.

## context-only heading
This heading has no date or id, so it is NOT an entry.
""",
        )

        # -------------------------------------------------------------------
        # Fixture 2 — proj-a: .living/INDEX.md (excluded)
        # -------------------------------------------------------------------
        _write(
            self.root / "proj-a" / ".living" / "INDEX.md",
            """\
# Index
This should be excluded.
""",
        )

        # -------------------------------------------------------------------
        # Fixture 3 — proj-a: .living/log/some-log.md (excluded)
        # -------------------------------------------------------------------
        _write(
            self.root / "proj-a" / ".living" / "log" / "some-log.md",
            """\
# Log entry
This should be excluded.
""",
        )

        # -------------------------------------------------------------------
        # Fixture 4 — proj-b: .living/findings/some-finding.md
        #             (standalone_finding_file)
        # -------------------------------------------------------------------
        _write(
            self.root / "proj-b" / ".living" / "findings" / "some-finding.md",
            """\
# A Standalone Finding
This is the body of the finding.
Tags: discovery, important
""",
        )

        self.projects = [
            ProjectMeta(
                id="proj-a",
                name="Project A",
                path="proj-a",
                family="test-family",
                has_living=True,
            ),
            ProjectMeta(
                id="proj-b",
                name="Project B",
                path="proj-b",
                family="test-family",
                has_living=True,
            ),
        ]

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _run(self) -> ExtractResult:
        return extract_entries(self.root, self.projects, {})

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_proj_a_learnings_exactly_two_entries(self) -> None:
        """learnings.md must yield exactly 2 entries: the two dated headings."""
        result = self._run()
        proj_a_entries = [e for e in result.entries if e.project_id == "proj-a"]
        # Only learnings.md is included for proj-a
        self.assertEqual(
            len(proj_a_entries),
            2,
            msg=(
                f"Expected exactly 2 entries from proj-a, got {len(proj_a_entries)}.\n"
                f"Entries: {[(e.anchor, e.title) for e in proj_a_entries]}"
            ),
        )

    def test_proj_a_entry_titles(self) -> None:
        """The two entries must correspond to the dated headings."""
        result = self._run()
        proj_a_entries = sorted(
            (e for e in result.entries if e.project_id == "proj-a"),
            key=lambda e: e.date or "",
        )
        dates = [e.date for e in proj_a_entries]
        self.assertIn("2026-01-15", dates, msg="First dated entry missing")
        self.assertIn("2026-02-10", dates, msg="Nested dated entry missing")

    def test_sub_context_is_not_an_entry(self) -> None:
        """### Sub-context must NOT produce an entry."""
        result = self._run()
        anchors = [e.anchor for e in result.entries if e.project_id == "proj-a"]
        for anchor in anchors:
            self.assertNotIn(
                "Sub-context",
                anchor,
                msg=f"'Sub-context' sub-heading was incorrectly parsed as an entry: {anchor!r}",
            )

    def test_context_only_heading_is_not_an_entry(self) -> None:
        """## context-only heading must NOT produce an entry."""
        result = self._run()
        anchors = [e.anchor for e in result.entries if e.project_id == "proj-a"]
        for anchor in anchors:
            self.assertNotIn(
                "context-only heading",
                anchor,
                msg=f"'context-only heading' was incorrectly parsed as an entry: {anchor!r}",
            )

    def test_index_md_excluded(self) -> None:
        """INDEX.md must contribute 0 entries."""
        result = self._run()
        index_entries = [e for e in result.entries if "INDEX.md" in e.source_path]
        self.assertEqual(
            len(index_entries),
            0,
            msg=f"INDEX.md contributed entries: {index_entries}",
        )

    def test_log_tree_excluded(self) -> None:
        """Files under log/ must contribute 0 entries."""
        result = self._run()
        log_entries = [
            e
            for e in result.entries
            if "/log/" in e.source_path or e.source_path.endswith("/log")
        ]
        self.assertEqual(
            len(log_entries),
            0,
            msg=f"log/ tree contributed entries: {log_entries}",
        )

    def test_proj_b_standalone_finding(self) -> None:
        """proj-b findings/some-finding.md → 1 entry with correct kind and source_shape."""
        result = self._run()
        proj_b_entries = [e for e in result.entries if e.project_id == "proj-b"]
        self.assertEqual(
            len(proj_b_entries),
            1,
            msg=f"Expected 1 entry from proj-b, got {len(proj_b_entries)}",
        )
        entry = proj_b_entries[0]
        self.assertEqual(entry.kind, EntryKind.finding)
        self.assertEqual(entry.source_shape, SourceShape.standalone_finding_file)

    def test_all_entries_have_sha256_content_hash(self) -> None:
        """Every entry must have a non-empty content_hash starting with 'sha256:'."""
        result = self._run()
        self.assertGreater(len(result.entries), 0, "No entries extracted at all")
        for entry in result.entries:
            self.assertTrue(
                entry.content_hash.startswith("sha256:"),
                msg=f"Entry {entry.id!r} has bad content_hash: {entry.content_hash!r}",
            )
            self.assertGreater(
                len(entry.content_hash),
                len("sha256:"),
                msg=f"Entry {entry.id!r} has empty sha256 digest",
            )

    def test_total_entry_count(self) -> None:
        """Total must be 3: 2 from proj-a learnings.md + 1 from proj-b findings/."""
        result = self._run()
        self.assertEqual(
            len(result.entries),
            3,
            msg=(
                f"Expected 3 total entries, got {len(result.entries)}.\n"
                f"Entries: {[(e.project_id, e.anchor) for e in result.entries]}"
            ),
        )

    def test_proj_b_tags_parsed(self) -> None:
        """The Tags: line in some-finding.md must be parsed."""
        result = self._run()
        proj_b_entries = [e for e in result.entries if e.project_id == "proj-b"]
        self.assertEqual(len(proj_b_entries), 1)
        tags = proj_b_entries[0].tags
        self.assertIn("discovery", tags)
        self.assertIn("important", tags)

    def test_entries_sorted(self) -> None:
        """Entries must be sorted by (project_id, source_path, id)."""
        result = self._run()
        keys = [(e.project_id, e.source_path, e.id) for e in result.entries]
        self.assertEqual(keys, sorted(keys), msg="Entries are not sorted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
