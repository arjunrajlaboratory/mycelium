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
