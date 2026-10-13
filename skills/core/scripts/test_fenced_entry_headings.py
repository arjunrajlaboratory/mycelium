"""Entry parsers must ignore dated headings inside fenced code blocks.

A knowledge entry often documents the entry format itself, so an illustrative
``### [2026-10-03] Title`` inside a ``` fence is common. It is example text,
not an entry: counting it inflates `.living/INDEX.md`, renumbers every later
entry ID, feeds fake records into `detect_recurrence` clusters, and cuts
`recall_lessons` entry text short at the example. `validate_structure` already
skips fenced lines; these tests hold every entry parser to the same rule
(regression pattern 21).
"""

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import detect_recurrence as dr  # noqa: E402
import generate_index as gi  # noqa: E402
import recall_lessons as rl  # noqa: E402

# One real entry whose body documents the entry format in a fence, followed by
# a second real entry. The fenced example carries a heading and a Tags line,
# both of which must be ignored; the real Tags line follows the fence.
_LOG_WITH_FENCED_EXAMPLE = (
    "# Log\n\n"
    "### [2026-10-01] Document the entry format\n\n"
    "Write entries like this:\n\n"
    "```markdown\n"
    "### [2026-10-03] Example title\n"
    "**Tags**: [fake-tag]\n\n"
    "Example body.\n"
    "```\n\n"
    "**Tags**: [real-tag]\n\n"
    "Text after the example.\n\n"
    "### [2026-10-02] Second real entry\n"
    "**Tags**: [other]\n\n"
    "Second body.\n"
)


@pytest.fixture()
def living_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".living"
    d.mkdir()
    return d


@pytest.mark.parametrize(
    "log_name,file_type,prefix",
    [("learnings.md", "learnings", "L"), ("decisions.md", "decisions", "D")],
)
class TestGenerateIndexParsers:
    def test_count_skips_fenced_heading(
        self, living_dir: Path, log_name: str, file_type: str, prefix: str
    ) -> None:
        path = living_dir / log_name
        path.write_text(_LOG_WITH_FENCED_EXAMPLE, encoding="utf-8")
        count, _ = gi.count_headers_and_topics(path, file_type)
        assert count == 2

    def test_collect_entries_skips_fenced_heading_and_tags(
        self, living_dir: Path, log_name: str, file_type: str, prefix: str
    ) -> None:
        path = living_dir / log_name
        path.write_text(_LOG_WITH_FENCED_EXAMPLE, encoding="utf-8")
        entries = gi.collect_entries(path, file_type, prefix)
        assert [(e["id"], e["title"]) for e in entries] == [
            (f"{prefix}-1", "Document the entry format"),
            (f"{prefix}-2", "Second real entry"),
        ]
        assert entries[0]["tags"] == ["real-tag"]

    def test_snippets_skip_fenced_heading(
        self, living_dir: Path, log_name: str, file_type: str, prefix: str
    ) -> None:
        path = living_dir / log_name
        path.write_text(_LOG_WITH_FENCED_EXAMPLE, encoding="utf-8")
        headers = [h for h, _ in gi.extract_entry_snippets(path, file_type)]
        assert headers == [
            "[2026-10-02] Second real entry",
            "[2026-10-01] Document the entry format",
        ]


@pytest.mark.parametrize(
    "fence_open,fence_close",
    [
        ("~~~", "~~~"),
        # A three-backtick line inside a four-backtick fence does not close it.
        ("````markdown\n```", "```\n````"),
        # A fence opened on a list item's line.
        ("- ```markdown", "  ```"),
    ],
)
def test_fence_variants_hide_headings(
    living_dir: Path, fence_open: str, fence_close: str
) -> None:
    path = living_dir / "learnings.md"
    path.write_text(
        "### [2026-10-01] Real\n\n"
        f"{fence_open}\n### [2026-10-03] Example\n{fence_close}\n\n"
        "### [2026-10-02] Also real\n",
        encoding="utf-8",
    )
    entries = gi.collect_entries(path, "learnings", "L")
    assert [e["title"] for e in entries] == ["Real", "Also real"]


def test_heading_after_fence_closes_is_counted(living_dir: Path) -> None:
    """Negative neighbor: a fence must not hide the entry that follows it."""
    path = living_dir / "learnings.md"
    path.write_text(
        "```\nnot an entry\n```\n### [2026-10-01] Right after the fence\n",
        encoding="utf-8",
    )
    count, _ = gi.count_headers_and_topics(path, "learnings")
    assert count == 1


def test_index_md_counts_skip_fenced_headings(living_dir: Path) -> None:
    (living_dir / "learnings.md").write_text(
        _LOG_WITH_FENCED_EXAMPLE, encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_DIR / "generate_index.py"),
            "--living-dir",
            str(living_dir),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    index = (living_dir / "INDEX.md").read_text(encoding="utf-8")
    learnings_row = next(l for l in index.splitlines() if "learnings.md" in l)
    assert "| 2 " in learnings_row, learnings_row


class TestDetectRecurrence:
    def test_canonical_fenced_heading_is_not_a_record(self, living_dir: Path) -> None:
        path = living_dir / "learnings.md"
        path.write_text(_LOG_WITH_FENCED_EXAMPLE, encoding="utf-8")
        records = dr.collect_learning_records(path)
        assert [r["title"] for r in records] == [
            "Document the entry format",
            "Second real entry",
        ]
        # The entry body runs past its fenced example to the next real entry.
        assert "Text after the example." in records[0]["body"]

    def test_legacy_fenced_heading_is_not_a_record(self, living_dir: Path) -> None:
        path = living_dir / "learnings.md"
        path.write_text(
            "## [2025-01-01] Legacy entry\n\n"
            "Old entries looked like:\n\n"
            "```\n## [2025-01-02] Legacy example\n```\n\n"
            "Text after the example.\n\n"
            "## [2025-01-03] Another legacy entry\n",
            encoding="utf-8",
        )
        records = dr.collect_learning_records(path)
        assert [r["title"] for r in records] == [
            "Legacy entry",
            "Another legacy entry",
        ]
        assert "Text after the example." in records[0]["body"]


def test_recall_text_runs_past_fenced_heading(living_dir: Path) -> None:
    (living_dir / "learnings.md").write_text(
        _LOG_WITH_FENCED_EXAMPLE, encoding="utf-8"
    )
    matches = rl.recall(living_dir, ids=["L-1"], file_filter="learnings")
    assert len(matches) == 1
    assert "Text after the example." in matches[0]["text"]
    assert "### [2026-10-02]" not in matches[0]["text"]


def test_finding_counts_skip_fenced_headings(living_dir: Path) -> None:
    """Both INDEX.md builders count ``## F-`` findings outside fences only."""
    findings = living_dir / "findings"
    findings.mkdir()
    (findings / "topic.md").write_text(
        "# Topic\n\n## F-001 Real finding\n\n"
        "```markdown\n## F-999 Example finding\n```\n\n"
        "## F-002 Second finding\n",
        encoding="utf-8",
    )
    full_index = gi.generate_index(living_dir)
    quick_reference = gi.build_quick_reference(living_dir)
    for text in (full_index, quick_reference):
        assert "2 findings across 1 topics" in text
