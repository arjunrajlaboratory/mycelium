"""
test_extract_logs.py — Unit tests for extract_logs.py

Run with: python3 test_extract_logs.py -v
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Ensure the knowledge_map directory is on sys.path
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from graph_model import ProjectMeta
from extract_logs import (
    extract_logs,
    ExtractLogsResult,
    _parse_log_file,
    _LOG_FILENAME_RE,
    _MIN_BODY_LEN,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _make_project(root: Path, project_id: str = "proj-a") -> ProjectMeta:
    return ProjectMeta(
        id=project_id,
        name=f"Project {project_id}",
        path=project_id,
        family="test-family",
        has_living=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCRLFParsing(unittest.TestCase):
    """CRLF line endings must not leak into parsed body or affect stub filter."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _log_dir(self, project_id: str = "proj-a") -> Path:
        return self.root / project_id / ".living" / "log"

    def test_crlf_body_no_leading_cr(self) -> None:
        """Parsed body from a CRLF file must have no leading \\r character."""
        lf_body = (
            "# My Session Log\n\nSome narrative content here.\nMore lines follow.\n"
        )
        # Build a CRLF version with frontmatter
        crlf_content = "---\r\ndate: 2026-06-11\r\n---\r\n" + lf_body.replace(
            "\n", "\r\n"
        )
        crlf_bytes = crlf_content.encode("utf-8")

        fpath = self._log_dir() / "2026-06-11-001-test.md"
        _write_bytes(fpath, crlf_bytes)

        _, body, _ = _parse_log_file(fpath)

        self.assertFalse(
            body.startswith("\r"),
            msg=f"Body must not start with \\r; got {body[:20]!r}",
        )
        self.assertFalse(
            body.startswith("\n"),
            msg=f"Body must not start with \\n; got {body[:20]!r}",
        )

    def test_crlf_body_no_embedded_cr(self) -> None:
        """Parsed body from a CRLF file must contain no embedded \\r characters."""
        lf_body = (
            "# My Session Log\n\nSome narrative content here.\nMore lines follow.\n"
        )
        crlf_content = "---\r\ndate: 2026-06-11\r\n---\r\n" + lf_body.replace(
            "\n", "\r\n"
        )
        crlf_bytes = crlf_content.encode("utf-8")

        fpath = self._log_dir() / "2026-06-11-001-test.md"
        _write_bytes(fpath, crlf_bytes)

        _, body, _ = _parse_log_file(fpath)

        self.assertNotIn(
            "\r",
            body,
            msg=f"Body must not contain any \\r; first 80 chars: {body[:80]!r}",
        )

    def test_crlf_body_matches_lf_equivalent(self) -> None:
        """CRLF and LF versions of the same file must produce identical parsed bodies."""
        content_lf = "---\ndate: 2026-06-11\n---\n# My Log\n\nNarrative line one.\nNarrative line two.\n"
        content_crlf = content_lf.replace("\n", "\r\n")

        log_dir = self._log_dir()
        fpath_lf = log_dir / "lf-version.md"
        fpath_crlf = log_dir / "crlf-version.md"

        _write(fpath_lf, content_lf)
        _write_bytes(fpath_crlf, content_crlf.encode("utf-8"))

        _, body_lf, _ = _parse_log_file(fpath_lf)
        _, body_crlf, _ = _parse_log_file(fpath_crlf)

        self.assertEqual(
            body_lf,
            body_crlf,
            msg="LF and CRLF versions produced different body_excerpt",
        )


class TestStubFilter(unittest.TestCase):
    """Logs with body < _MIN_BODY_LEN chars are dropped; others are kept."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _run(self, projects: list[ProjectMeta]) -> ExtractLogsResult:
        return extract_logs(self.root, projects, {})

    def test_short_body_dropped(self) -> None:
        """A log with body < 150 chars after strip must be dropped (not appear in result)."""
        short_body = "# Session Log\n\nBrief stub.\n"
        # Verify it's actually short
        self.assertLess(len(short_body.strip()), _MIN_BODY_LEN)

        log_dir = self.root / "proj-a" / ".living" / "log"
        _write_path = log_dir / "2026-06-11-001-short.md"
        log_dir.mkdir(parents=True, exist_ok=True)
        _write_path.write_text(short_body, encoding="utf-8")

        project = _make_project(self.root, "proj-a")
        result = self._run([project])

        self.assertEqual(
            len(result.logs),
            0,
            msg=f"Short log should be dropped; got {len(result.logs)} log(s)",
        )
        # Report should contain a STUB DROPPED message
        stub_reports = [r for r in result.report if "STUB DROPPED" in r]
        self.assertGreater(len(stub_reports), 0, msg="Expected STUB DROPPED in report")

    def test_long_body_kept(self) -> None:
        """A log with body >= 150 chars after strip must be kept in result."""
        # Build a body that's definitely >= 150 chars
        long_body = "# Session Log\n\n" + "A" * 200 + "\n"
        self.assertGreaterEqual(len(long_body.strip()), _MIN_BODY_LEN)

        log_dir = self.root / "proj-a" / ".living" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "2026-06-11-001-long.md").write_text(long_body, encoding="utf-8")

        project = _make_project(self.root, "proj-a")
        result = self._run([project])

        self.assertEqual(
            len(result.logs),
            1,
            msg=f"Long log should be kept; got {len(result.logs)} log(s)",
        )

    def test_crlf_padding_does_not_sneak_past_stub_filter(self) -> None:
        """A body that is short in LF form must also be short in CRLF form — no sneaking."""
        # A short LF body
        short_lf = "# Log\n\nToo short.\n"
        self.assertLess(len(short_lf.strip()), _MIN_BODY_LEN)

        # Build a CRLF version — the \r characters must NOT inflate the stripped length
        short_crlf = short_lf.replace("\n", "\r\n")

        log_dir = self.root / "proj-a" / ".living" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "2026-06-11-001-crlf-short.md").write_bytes(
            short_crlf.encode("utf-8")
        )

        project = _make_project(self.root, "proj-a")
        result = self._run([project])

        self.assertEqual(
            len(result.logs),
            0,
            msg="CRLF-padded short body must still be dropped by stub filter",
        )


class TestFilenameSchema(unittest.TestCase):
    """Filename regex: valid schema → parsed date/seq; non-matching → None/None."""

    def test_valid_filename_parsed(self) -> None:
        """2026-06-11-001-some-slug.md → session_date='2026-06-11', session_seq=1."""
        m = _LOG_FILENAME_RE.match("2026-06-11-001-some-slug.md")
        self.assertIsNotNone(m, "Expected filename to match _LOG_FILENAME_RE")
        self.assertEqual(m.group(1), "2026-06-11")
        self.assertEqual(int(m.group(2)), 1)

    def test_valid_filename_three_digit_seq(self) -> None:
        """2026-06-11-042-another.md → session_seq=42."""
        m = _LOG_FILENAME_RE.match("2026-06-11-042-another.md")
        self.assertIsNotNone(m)
        self.assertEqual(int(m.group(2)), 42)

    def test_non_matching_filename_no_crash(self) -> None:
        """notes.md does not match _LOG_FILENAME_RE and must not crash."""
        m = _LOG_FILENAME_RE.match("notes.md")
        self.assertIsNone(m)

    def test_non_matching_yields_none_date_seq_in_extract(self) -> None:
        """A log file with non-schema filename must have session_date=None, session_seq=None."""
        tmpdir = tempfile.TemporaryDirectory()
        root = Path(tmpdir.name)
        log_dir = root / "proj-a" / ".living" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Long enough body so it passes the stub filter
        (log_dir / "notes.md").write_text(
            "# Notes\n\n" + "Some narrative content. " * 10 + "\n",
            encoding="utf-8",
        )

        project = _make_project(root, "proj-a")
        result = extract_logs(root, [project], {})

        self.assertEqual(len(result.logs), 1)
        log = result.logs[0]
        self.assertIsNone(
            log.session_date, msg="Non-schema filename must have session_date=None"
        )
        self.assertIsNone(
            log.session_seq, msg="Non-schema filename must have session_seq=None"
        )

        tmpdir.cleanup()


class TestUnterminatedFrontmatter(unittest.TestCase):
    """Unterminated frontmatter (no closing ---) must not embed raw --- opener in body."""

    def test_unterminated_fm_no_raw_opener_in_body(self) -> None:
        """Body must not start with or contain the orphan --- frontmatter opener."""
        # Frontmatter with no closing ---
        content = "---\ndate: 2026-06-11\nauthor: test\n# My Log\n\n" + "N" * 200 + "\n"
        fpath = Path(tempfile.mktemp(suffix=".md"))
        fpath.write_text(content, encoding="utf-8")

        try:
            _, body, _ = _parse_log_file(fpath)
            # Body must not start with ---
            self.assertFalse(
                body.lstrip().startswith("---"),
                msg=f"Body must not start with orphan '---'; got {body[:40]!r}",
            )
        finally:
            fpath.unlink(missing_ok=True)

    def test_terminated_fm_not_in_body(self) -> None:
        """With proper frontmatter, body must not start with --- either."""
        content = "---\ndate: 2026-06-11\n---\n# My Log\n\n" + "N" * 200 + "\n"
        fpath = Path(tempfile.mktemp(suffix=".md"))
        fpath.write_text(content, encoding="utf-8")

        try:
            _, body, _ = _parse_log_file(fpath)
            self.assertFalse(
                body.lstrip().startswith("---"),
                msg=f"Body must not start with '---'; got {body[:40]!r}",
            )
        finally:
            fpath.unlink(missing_ok=True)


class TestFrontmatterDateExtraction(unittest.TestCase):
    """Title extraction works correctly (frontmatter stripped, heading found)."""

    def test_title_from_heading_after_frontmatter(self) -> None:
        """Title is extracted from the first heading after frontmatter is stripped."""
        content = (
            "---\ndate: 2026-06-11\n---\n# My Session Title\n\n"
            + "Body content. " * 15
            + "\n"
        )
        fpath = Path(tempfile.mktemp(suffix=".md"))
        fpath.write_text(content, encoding="utf-8")

        try:
            title, _, _ = _parse_log_file(fpath)
            self.assertEqual(title, "My Session Title")
        finally:
            fpath.unlink(missing_ok=True)

    def test_title_fallback_to_stem_when_no_heading(self) -> None:
        """When no heading exists, title falls back to filename stem."""
        content = "---\ndate: 2026-06-11\n---\nJust plain text.\n" + "X" * 200 + "\n"
        fpath = Path(tempfile.mktemp(suffix="2026-06-11-001-fallback-slug.md"))
        fpath.write_text(content, encoding="utf-8")

        try:
            title, _, _ = _parse_log_file(fpath)
            # Should be the stem of the temp file path
            self.assertEqual(title, fpath.stem)
        finally:
            fpath.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
