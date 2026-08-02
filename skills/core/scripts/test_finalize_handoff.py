"""Tests for atomic handoff lifecycle-status finalization."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from finalize_handoff import STATUS_BEGIN, finalize

SCRIPT = Path(__file__).parent / "finalize_handoff.py"


def test_finalize_is_idempotent_and_removes_stale_stop_lines() -> None:
    original = (
        "## Current state\n- Analysis complete.\n"
        "- Stop codon remains annotated in the gene model.\n\n"
        "## Next steps\n- Attempt natural Stop now.\n"
    )

    once = finalize(original, "2026-08-02T12:00:00-0400")
    twice = finalize(once, "2026-08-02T12:00:00-0400")

    assert once == twice
    assert once.count(STATUS_BEGIN) == 1
    assert "Analysis complete." in once
    assert "Stop codon remains annotated" in once
    assert "Attempt natural Stop" not in once


def test_cli_preserves_existing_file_mode(tmp_path: Path) -> None:
    handoff = tmp_path / "last-session.md"
    handoff.write_text("## Current state\n- Complete.\n")
    os.chmod(handoff, 0o600)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(handoff),
            "--accepted-at",
            "2026-08-02T12:00:00-0400",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(handoff.stat().st_mode) == 0o600


def test_cli_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "outside.md"
    target.write_text("keep\n")
    handoff = tmp_path / "last-session.md"
    handoff.symlink_to(target)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(handoff),
            "--accepted-at",
            "2026-08-02T12:00:00-0400",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert target.read_text() == "keep\n"
