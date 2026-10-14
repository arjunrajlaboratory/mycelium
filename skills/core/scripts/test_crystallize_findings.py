"""Locked, mode-preserving findings crystallization tests."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys


SCRIPT = Path(__file__).parent / "crystallize_findings.py"


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


def _parse_topic(path: Path) -> dict:
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        import crystallize_findings
    finally:
        sys.path.remove(str(SCRIPT.parent))
    return crystallize_findings.parse_topic_file(path)


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

    info = _parse_topic(topic)

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

    info = _parse_topic(topic)

    assert info["topic"] == "format"
    assert info["description"] == ""
    assert info["status"] == "active"
    assert info["last_updated"] == ""


def test_unclosed_opening_rule_is_not_frontmatter(tmp_path: Path) -> None:
    topic = tmp_path / "open.md"
    topic.write_text(
        "---\n\ndescription: x\nstatus: retired\n", encoding="utf-8"
    )

    info = _parse_topic(topic)

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

    info = _parse_topic(topic)

    assert info["topic"] == "robustness"
    assert info["description"] == "Robust cross-project behavior"
    assert info["last_updated"] == "2026-08-02"
    assert info["status"] == "active"
    assert info["finding_count"] == 1


def test_byte_order_mark_does_not_hide_frontmatter(tmp_path: Path) -> None:
    topic = tmp_path / "robustness.md"
    topic.write_text("﻿" + _topic(), encoding="utf-8")

    info = _parse_topic(topic)

    assert info["topic"] == "robustness"
    assert info["description"] == "Robust cross-project behavior"
