"""Locked, mode-preserving findings crystallization tests."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).parent / "crystallize_findings.py"
sys.path.insert(0, str(SCRIPT.parent))

import crystallize_findings as cf  # noqa: E402
import generate_index as gi  # noqa: E402


def _topic() -> str:
    return (
        "---\n"
        "topic: robustness\n"
        "description: Robust cross-project behavior\n"
        "last_updated: 2026-08-02\n"
        "status: active\n"
        "---\n\n"
        "## F-001 — Concurrent writers\n\n"
        "**Claim:** Locked writers preserve findings.\n\n"
        "**Status:** robust\n\n"
        "**Implications:** Shared state remains valid.\n\n"
        "**Tags:** concurrency\n"
    )


def test_crystallization_preserves_existing_output_modes(tmp_path: Path) -> None:
    meta = tmp_path / "portfolio"
    meta_findings = meta / ".living" / "findings"
    project_findings = meta / "project-a" / ".living" / "findings"
    meta_findings.mkdir(parents=True)
    project_findings.mkdir(parents=True)
    (project_findings / "robustness.md").write_text(_topic())
    meta_index = meta_findings / "INDEX.md"
    registry = project_findings / "FINDINGS_REGISTRY.md"
    meta_index.write_text("old index\n")
    registry.write_text("old registry\n")
    os.chmod(meta_index, 0o640)
    os.chmod(registry, 0o600)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--meta-root", str(meta)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "robustness" in meta_index.read_text()
    assert "F-001" in registry.read_text()
    assert stat.S_IMODE(meta_index.stat().st_mode) == 0o640
    assert stat.S_IMODE(registry.stat().st_mode) == 0o600


def test_linked_destination_fails_before_coordination_state_write(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "portfolio"
    living = meta / ".living"
    living.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (living / "findings").symlink_to(outside, target_is_directory=True)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--meta-root", str(meta)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert not (meta / ".mycelium").exists()
    assert list(outside.iterdir()) == []


def test_horizontal_rule_does_not_open_frontmatter(tmp_path: Path) -> None:
    topic = tmp_path / "rules.md"
    topic.write_text(
        "# Rules\n\n"
        "Intro paragraph.\n\n"
        "---\n\n"
        "description: x\n"
        "status: retired\n"
        "topic: hijacked\n"
        "last_updated: 1999-01-01\n",
        encoding="utf-8",
    )

    info = cf.parse_topic_file(topic)

    assert info["topic"] == "rules"
    assert info["description"] == ""
    assert info["status"] == "active"
    assert info["last_updated"] == ""


def test_fenced_rule_does_not_open_frontmatter(tmp_path: Path) -> None:
    topic = tmp_path / "format.md"
    topic.write_text(
        "# Topic file format\n\n"
        "Example frontmatter:\n\n"
        "```yaml\n"
        "---\n"
        "topic: example\n"
        "description: Example description\n"
        "status: superseded\n"
        "last_updated: 2000-01-01\n"
        "---\n"
        "```\n",
        encoding="utf-8",
    )

    info = cf.parse_topic_file(topic)

    assert info["topic"] == "format"
    assert info["description"] == ""
    assert info["status"] == "active"
    assert info["last_updated"] == ""


def test_unclosed_opening_rule_is_not_frontmatter(tmp_path: Path) -> None:
    topic = tmp_path / "open.md"
    topic.write_text(
        "---\n\ndescription: x\nstatus: retired\n", encoding="utf-8"
    )

    info = cf.parse_topic_file(topic)

    assert info["description"] == ""
    assert info["status"] == "active"


def test_leading_frontmatter_sets_metadata_and_stops_at_close(
    tmp_path: Path,
) -> None:
    topic = tmp_path / "robustness.md"
    topic.write_text(
        _topic() + "\n---\n\ndescription: body text, not metadata\n",
        encoding="utf-8",
    )

    info = cf.parse_topic_file(topic)

    assert info["topic"] == "robustness"
    assert info["description"] == "Robust cross-project behavior"
    assert info["last_updated"] == "2026-08-02"
    assert info["status"] == "active"
    assert info["finding_count"] == 1


def test_byte_order_mark_does_not_hide_frontmatter(tmp_path: Path) -> None:
    topic = tmp_path / "robustness.md"
    topic.write_text("﻿" + _topic(), encoding="utf-8")

    info = cf.parse_topic_file(topic)

    assert info["topic"] == "robustness"
    assert info["description"] == "Robust cross-project behavior"
# Regression pattern 21: a topic file that documents the finding format inside
# a code fence must not have its example parsed as live structure. Each fenced
# trap is placed so that reading it would change an asserted value: the fenced
# ledger row is the ledger's last line, and fenced section-ending lines sit
# before a later real question.
_FENCED_TOPIC = (
    "---\n"
    "topic: format\n"
    "description: Topic that documents its own format\n"
    "last_updated: 2026-10-13\n"
    "status: active\n"
    "---\n\n"
    "## F-001 — Real finding\n\n"
    "**Claim:** The real claim.\n\n"
    "**Status:** preliminary\n\n"
    "**Tags:** real\n\n"
    "A finding is written like this:\n\n"
    "```markdown\n"
    "## F-999 Example\n"
    "**Claim:** Example claim.\n"
    "**Status:** robust\n"
    "**Implications:** Example implication.\n"
    "**Tags:** example\n"
    "### Open Questions\n"
    "- Example question?\n"
    "```\n\n"
    "### Evidence Ledger\n\n"
    "| Date | Source |\n"
    "|------|--------|\n"
    "| 2026-10-01 | real row |\n"
    "~~~text\n"
    "# not a heading\n"
    "| 2099-02-02 | fenced row |\n"
    "~~~\n\n"
    "## F-002 — Second real finding\n\n"
    "**Status:** supported\n\n"
    "### Open Questions\n\n"
    "- Real question one?\n"
    "```markdown\n"
    "# fenced comment, not a section end\n"
    "---\n"
    "- fenced example question\n"
    "```\n"
    "- Real question two?\n"
    "- Real question three?\n"
)


def _registry_rows(findings: Path) -> list[str]:
    registry = (findings / "FINDINGS_REGISTRY.md").read_text()
    return [line for line in registry.splitlines() if line.startswith("| F-")]


def test_topic_parse_ignores_fenced_finding_status_and_questions(
    tmp_path: Path,
) -> None:
    topic = tmp_path / "format.md"
    topic.write_text(_FENCED_TOPIC)

    info = cf.parse_topic_file(topic)

    assert info["finding_count"] == 2
    assert info["strongest"] == "supported"
    assert info["open_questions"] == 3


def test_topic_finding_count_agrees_with_generate_index(tmp_path: Path) -> None:
    topic = tmp_path / "format.md"
    topic.write_text(_FENCED_TOPIC)

    assert cf.parse_topic_file(topic)["finding_count"] == (
        gi.count_finding_headings(_FENCED_TOPIC)
    )


def test_registry_ignores_fenced_finding_and_keeps_real_fields(
    tmp_path: Path,
) -> None:
    findings = tmp_path / "project" / ".living" / "findings"
    findings.mkdir(parents=True)
    (findings / "format.md").write_text(_FENCED_TOPIC.replace("\n", "\r\n"))

    cf.rebuild_project_registry(findings)

    assert _registry_rows(findings) == [
        "| F-001 | The real claim. | preliminary | [format](format.md)"
        " |  | real | 2026-10-01 |",
        "| F-002 | (no claim) | supported | [format](format.md) |  |  |  |",
    ]


def test_registry_drops_rows_once_only_fenced_examples_remain(
    tmp_path: Path,
) -> None:
    findings = tmp_path / "project" / ".living" / "findings"
    findings.mkdir(parents=True)
    (findings / "format.md").write_text(
        "# Format\n\n```markdown\n## F-001 Example\n**Status:** robust\n```\n"
    )
    # A registry written by an earlier version that counted the example.
    registry = findings / "FINDINGS_REGISTRY.md"
    registry.write_text("| F-001 | Example claim. | robust | x | | | |\n")

    cf.rebuild_project_registry(findings)

    assert registry.exists()
    assert _registry_rows(findings) == []
    assert "| ID | Claim |" in registry.read_text()


def test_registry_is_not_created_for_topics_without_findings(
    tmp_path: Path,
) -> None:
    findings = tmp_path / "project" / ".living" / "findings"
    findings.mkdir(parents=True)
    (findings / "format.md").write_text("# Format\n\nNo findings yet.\n")

    cf.rebuild_project_registry(findings)

    assert not (findings / "FINDINGS_REGISTRY.md").exists()


_UNCLOSED_TOPIC = (
    "## F-001 — First\n\n"
    "**Status:** preliminary\n\n"
    "```markdown\n"
    "## F-900 Example\n\n"
    "## F-002 — Hidden by the open fence\n\n"
    "**Status:** robust\n"
)


@pytest.mark.parametrize("parse", ["topic", "registry"])
def test_unclosed_fence_in_topic_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], parse: str
) -> None:
    findings = tmp_path / "project" / ".living" / "findings"
    findings.mkdir(parents=True)
    topic = findings / "format.md"
    topic.write_text(_UNCLOSED_TOPIC)

    if parse == "topic":
        cf.parse_topic_file(topic)
    else:
        cf.rebuild_project_registry(findings)

    err = capsys.readouterr().err
    assert f"{topic}:5" in err
    assert "unclosed code fence" in err


def test_closed_fences_produce_no_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    findings = tmp_path / "project" / ".living" / "findings"
    findings.mkdir(parents=True)
    (findings / "format.md").write_text(_FENCED_TOPIC)

    cf.parse_topic_file(findings / "format.md")
    cf.rebuild_project_registry(findings)

    assert capsys.readouterr().err == ""


def test_crystallization_run_empties_registry_of_fenced_only_rows(
    tmp_path: Path,
) -> None:
    meta = tmp_path / "portfolio"
    (meta / ".living").mkdir(parents=True)
    findings = meta / "project-a" / ".living" / "findings"
    findings.mkdir(parents=True)
    (findings / "format.md").write_text(
        "# Format\n\n```markdown\n## F-001 Example\n**Status:** robust\n```\n"
    )
    (findings / "FINDINGS_REGISTRY.md").write_text(
        "| F-001 | Example claim. | robust | x | | | |\n"
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--meta-root", str(meta)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert _registry_rows(findings) == []
    index = (meta / ".living" / "findings" / "INDEX.md").read_text()
    assert "| format | (no description) | project-a | 0 | preliminary | 0 |" in index
