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

from extract_entries import (  # noqa: E402
    ExtractResult,
    _content_hash,
    _find_explicit_id_in_ledger,
    _find_in_ledger,
    _parse_signal_fields,
    extract_entries,
)
from graph_model import EntryKind, ProjectMeta, SourceShape  # noqa: E402


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


# ---------------------------------------------------------------------------
# New tests for Changes 1–5
# ---------------------------------------------------------------------------


class TestSignalFieldParsing(unittest.TestCase):
    """Change 1: parse mitigation_type, finding_status, source_project from body lines."""

    def test_bold_variant_all_three_fields(self) -> None:
        """Bold **key**: value syntax parses all three signal fields."""
        body = [
            "**mitigation_type**: structural",
            "**Status**: supported",
            "**source**: MyProject",
            "Some other content.",
        ]
        mit, fstatus, src = _parse_signal_fields(body)
        self.assertEqual(mit, "structural")
        self.assertEqual(fstatus, "supported")
        self.assertEqual(src, "MyProject")

    def test_plain_variant_mitigation_type(self) -> None:
        """Plain mitigation_type: value (no bold) is parsed and lowercased."""
        body = ["mitigation_type: ambient-awareness"]
        mit, fstatus, src = _parse_signal_fields(body)
        self.assertEqual(mit, "ambient-awareness")
        self.assertIsNone(fstatus)
        self.assertIsNone(src)

    def test_finding_status_does_not_touch_entry_status(self) -> None:
        """finding_status is stored separately; entry.status must not be 'supported'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write(
                root / "proj-s" / ".living" / "learnings.md",
                """\
## [2026-03-01] Signal field learning
**mitigation_type**: structural
**Status**: supported
**source**: MyProject
Body text.
""",
            )
            projects = [
                ProjectMeta(
                    id="proj-s",
                    name="Signal Project",
                    path="proj-s",
                    family="test",
                    has_living=True,
                )
            ]
            result = extract_entries(root, projects, {})
            self.assertEqual(len(result.entries), 1)
            entry = result.entries[0]
            self.assertEqual(entry.mitigation_type, "structural")
            self.assertEqual(entry.finding_status, "supported")
            self.assertEqual(entry.source_project, "MyProject")
            # entry.status must NOT be "supported" — it's an EntryStatus enum
            self.assertNotEqual(str(entry.status), "supported")

    def test_absent_fields_are_none(self) -> None:
        """When none of the signal fields appear, all three are None."""
        body = ["Just some plain text.", "No special fields here."]
        mit, fstatus, src = _parse_signal_fields(body)
        self.assertIsNone(mit)
        self.assertIsNone(fstatus)
        self.assertIsNone(src)

    def test_case_insensitive_keys(self) -> None:
        """Keys are matched case-insensitively."""
        body = [
            "MITIGATION_TYPE: process",
            "status: replicated",
            "SOURCE: AnotherProject",
        ]
        mit, fstatus, src = _parse_signal_fields(body)
        self.assertEqual(mit, "process")
        self.assertEqual(fstatus, "replicated")
        self.assertEqual(src, "AnotherProject")


class TestFingerprintPipeInAnchor(unittest.TestCase):
    """Change 3: | in heading anchor must not corrupt the fingerprint."""

    def _build_fingerprint(
        self, project_id: str, source_path: str, anchor: str, kind: str, date: str
    ) -> str:
        return "\x00".join([project_id, source_path, anchor, kind, date])

    def test_pipe_in_anchor_fingerprint_stable(self) -> None:
        """Building the same fingerprint twice with | in anchor gives equal results."""
        fp1 = self._build_fingerprint(
            "proj-x", "proj-x/.living/learnings.md", "foo|bar", "learning", "2026-01-01"
        )
        fp2 = self._build_fingerprint(
            "proj-x", "proj-x/.living/learnings.md", "foo|bar", "learning", "2026-01-01"
        )
        self.assertEqual(fp1, fp2)

    def test_fingerprint_splits_to_five_fields(self) -> None:
        """A fingerprint with | in anchor splits to exactly 5 fields on \\x00."""
        fp = self._build_fingerprint(
            "proj-x",
            "proj-x/.living/learnings.md",
            "heading|with|pipes",
            "learning",
            "2026-01-01",
        )
        parts = fp.split("\x00")
        self.assertEqual(
            len(parts), 5, msg=f"Expected 5 parts, got {len(parts)}: {parts}"
        )
        self.assertEqual(parts[2], "heading|with|pipes")

    def test_pipe_anchor_roundtrip(self) -> None:
        """project_id, source_path, anchor, kind, date survive the join/split roundtrip."""
        project_id = "proj-x"
        source_path = "proj-x/.living/findings.md"
        anchor = "Finding: A|B split claim"
        kind = "finding"
        date = "2026-05-10"
        fp = self._build_fingerprint(project_id, source_path, anchor, kind, date)
        parts = fp.split("\x00")
        self.assertEqual(parts[0], project_id)
        self.assertEqual(parts[1], source_path)
        self.assertEqual(parts[2], anchor)
        self.assertEqual(parts[3], kind)
        self.assertEqual(parts[4], date)


class TestDuplicateAnchorIncrementalBuild(unittest.TestCase):
    """Change 4: duplicate anchors in one file get distinct stable IDs via claimed_ids."""

    def test_two_entries_same_fingerprint_get_different_ids(self) -> None:
        """_find_in_ledger with shared claimed_ids returns distinct ids for duplicate fingerprints."""
        fingerprint = "proj\x00path\x00anchor\x00learning\x002026-01-01"
        # Pre-populate ledger with two records sharing the same fingerprint
        ledger = {
            "e-00001": {
                "current_fingerprint": fingerprint,
                "previous_fingerprints": [],
                "content_hash": "sha256:aaa",
                "status": "active",
            },
            "e-00002": {
                "current_fingerprint": fingerprint,
                "previous_fingerprints": [],
                "content_hash": "sha256:bbb",
                "status": "active",
            },
        }
        claimed: set[str] = set()
        id1 = _find_in_ledger(ledger, fingerprint, claimed)
        id2 = _find_in_ledger(ledger, fingerprint, claimed)
        # Both must be found (not None), and they must be different
        self.assertIsNotNone(id1, "First call should find a ledger match")
        self.assertIsNotNone(id2, "Second call should find an unclaimed ledger match")
        self.assertNotEqual(
            id1, id2, "Duplicate fingerprint entries must get distinct IDs"
        )

    def test_third_call_returns_none_when_all_claimed(self) -> None:
        """Third call returns None when both ledger entries are already claimed."""
        fingerprint = "proj\x00path\x00anchor\x00learning\x002026-01-01"
        ledger = {
            "e-00001": {
                "current_fingerprint": fingerprint,
                "previous_fingerprints": [],
                "content_hash": "sha256:aaa",
                "status": "active",
            },
            "e-00002": {
                "current_fingerprint": fingerprint,
                "previous_fingerprints": [],
                "content_hash": "sha256:bbb",
                "status": "active",
            },
        }
        claimed: set[str] = set()
        _find_in_ledger(ledger, fingerprint, claimed)
        _find_in_ledger(ledger, fingerprint, claimed)
        id3 = _find_in_ledger(ledger, fingerprint, claimed)
        self.assertIsNone(id3, "Third call with all ids claimed should return None")


class TestCRLFContentHash(unittest.TestCase):
    """Change 5: CRLF body lines yield same content_hash as LF equivalent."""

    def test_crlf_same_hash_as_lf(self) -> None:
        """Body lines with \\r\\n endings hash identically to their \\n equivalents."""
        lf_lines = ["line one", "line two", "line three"]
        # Simulate what the parsers produce AFTER rstrip("\\r\\n"):
        # both LF and CRLF source files should reduce to the same stripped lines
        crlf_raw = ["line one\r\n", "line two\r\n", "line three\r\n"]
        lf_raw = ["line one\n", "line two\n", "line three\n"]

        stripped_crlf = [l.rstrip("\r\n") for l in crlf_raw]
        stripped_lf = [l.rstrip("\r\n") for l in lf_raw]

        self.assertEqual(stripped_crlf, lf_lines)
        self.assertEqual(stripped_lf, lf_lines)
        self.assertEqual(_content_hash(stripped_crlf), _content_hash(stripped_lf))

    def test_crlf_differs_from_lf_without_fix(self) -> None:
        """Confirms that rstrip('\\n') alone would NOT strip the \\r, causing hash divergence."""
        crlf_raw = ["line one\r\n", "line two\r\n"]
        lf_raw = ["line one\n", "line two\n"]

        broken_crlf = [l.rstrip("\n") for l in crlf_raw]  # retains \r
        broken_lf = [l.rstrip("\n") for l in lf_raw]  # clean

        # Without the fix, the lines differ because \r remains
        self.assertNotEqual(
            broken_crlf, broken_lf, "Pre-fix behaviour: CRLF and LF lines should differ"
        )
        self.assertNotEqual(
            _content_hash(broken_crlf),
            _content_hash(broken_lf),
            "Pre-fix behaviour: content hashes should differ when \\r is retained",
        )


class TestBug1MintedIdNotRegistered(unittest.TestCase):
    """Regression: Bug 1 — minted ids were never added to _file_claimed_ids.

    Two verbatim-duplicate sections in the same file (same fingerprint) must
    receive TWO distinct entry ids in a single cold build (empty ledger).
    Before the fix, the second record's _find_in_ledger call found the
    just-minted id in working_ledger and returned it again, collapsing both
    records to one id.
    """

    def test_duplicate_dated_sections_get_distinct_ids(self) -> None:
        """Two identical dated sections in one file → two distinct entry ids."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Two verbatim-duplicate sections: same heading text, same body.
            # They produce the same fingerprint, so this directly exercises
            # the mint path + _file_claimed_ids registration gap.
            _write(
                root / "proj-dup" / ".living" / "learnings.md",
                """\
## [2026-04-01] Duplicate Learning
Body text that is identical in both sections.

## [2026-04-01] Duplicate Learning
Body text that is identical in both sections.
""",
            )
            projects = [
                ProjectMeta(
                    id="proj-dup",
                    name="Dup Project",
                    path="proj-dup",
                    family="test",
                    has_living=True,
                )
            ]
            # Cold build: empty ledger
            result = extract_entries(root, projects, {})
            ids = [e.id for e in result.entries]
            self.assertEqual(
                len(ids),
                2,
                msg=f"Expected 2 entries for duplicate sections, got {len(ids)}: {ids}",
            )
            self.assertEqual(
                len(set(ids)),
                2,
                msg=f"Duplicate entry ids produced (Bug 1 not fixed): {ids}",
            )


class TestBug2ExplicitIdSubstringCollision(unittest.TestCase):
    """Regression: Bug 2 — _find_explicit_id_in_ledger had no claimed_ids guard
    and used a substring match.

    Three headings in the same file all tagged with the same explicit token
    (e.g. L72) must receive THREE distinct entry ids in a single cold build.
    Before the fix, every heading resolved to the same first-minted id because
    the substring match found it without checking claimed_ids.
    """

    def test_shared_explicit_token_headings_get_distinct_ids(self) -> None:
        """Three headings sharing explicit token L72 → three distinct entry ids."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Three decision headings, all bearing the same explicit id token L72.
            _write(
                root / "proj-exp" / ".living" / "decisions.md",
                """\
## [2026-05-01] L72 First decision variant
Body of the first variant.

## [2026-05-02] L72 Second decision variant
Body of the second variant.

## [2026-05-03] L72 Third decision variant
Body of the third variant.
""",
            )
            projects = [
                ProjectMeta(
                    id="proj-exp",
                    name="Explicit Id Project",
                    path="proj-exp",
                    family="test",
                    has_living=True,
                )
            ]
            # Cold build: empty ledger
            result = extract_entries(root, projects, {})
            ids = [e.id for e in result.entries]
            self.assertEqual(
                len(ids),
                3,
                msg=f"Expected 3 entries for three L72-tagged headings, got {len(ids)}: {ids}",
            )
            self.assertEqual(
                len(set(ids)),
                3,
                msg=f"Duplicate entry ids produced (Bug 2 not fixed): {ids}",
            )

    def test_find_explicit_id_in_ledger_claimed_ids_guard(self) -> None:
        """_find_explicit_id_in_ledger skips already-claimed ids (unit-level)."""
        # Pre-populate ledger with two entries that both match project/path/L72
        ledger = {
            "e-00010": {
                "current_fingerprint": "proj-exp\x00proj-exp/.living/decisions.md\x00L72 First decision variant\x00decision\x002026-05-01",
                "previous_fingerprints": [],
                "content_hash": "sha256:aaa",
                "status": "active",
            },
            "e-00011": {
                "current_fingerprint": "proj-exp\x00proj-exp/.living/decisions.md\x00L72 Second decision variant\x00decision\x002026-05-02",
                "previous_fingerprints": [],
                "content_hash": "sha256:bbb",
                "status": "active",
            },
        }
        claimed: set[str] = set()
        # Both anchors contain "L72" as a substring — the old code would keep
        # returning e-00010 for every call.  With the fix, the second call
        # skips e-00010 (already claimed) and returns e-00011.
        id1 = _find_explicit_id_in_ledger(
            ledger,
            "proj-exp",
            "proj-exp/.living/decisions.md",
            "L72",
            claimed,
        )
        id2 = _find_explicit_id_in_ledger(
            ledger,
            "proj-exp",
            "proj-exp/.living/decisions.md",
            "L72",
            claimed,
        )
        self.assertIsNotNone(id1, "First call must find a ledger match")
        self.assertIsNotNone(id2, "Second call must find a distinct unclaimed match")
        self.assertNotEqual(id1, id2, "Both calls must return distinct ids")


if __name__ == "__main__":
    unittest.main(verbosity=2)
