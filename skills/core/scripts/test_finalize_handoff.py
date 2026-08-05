"""Tests for atomic handoff lifecycle-status finalization."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from finalize_handoff import STATUS_BEGIN, finalize

SCRIPT = Path(__file__).parent / "finalize_handoff.py"


def _complete_handoff(
    *,
    current_state: str = "- Analysis complete.",
    next_steps: str = "- Continue from the accepted handoff.",
) -> str:
    return (
        "## What was worked on\n- Lifecycle finalization.\n\n"
        "## Key decisions made\n- Preserve complete authored handoffs.\n\n"
        "## Blockers & surprises\n- None.\n\n"
        f"## Current state\n{current_state}\n\n"
        f"## Next steps\n{next_steps}\n"
    )


def test_finalize_is_idempotent_and_removes_stale_stop_lines() -> None:
    original = _complete_handoff(
        current_state=(
            "- Analysis complete.\n"
            "- Stop codon remains annotated in the gene model."
        ),
        next_steps=(
            "- Attempt natural Stop now.\n"
            "- Continue reviewing the accepted analysis."
        ),
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
    handoff.write_text(_complete_handoff())
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


def test_publish_preflight_failure_preserves_private_handoff(tmp_path: Path) -> None:
    private = tmp_path / "private"
    shared = tmp_path / "shared"
    outside = tmp_path / "outside.md"
    private.mkdir()
    shared.mkdir()
    outside.write_text("outside\n")
    handoff = private / "last-session.md"
    handoff.write_text("## Current state\n- Pending acceptance.\n")
    original = handoff.read_bytes()
    destination = shared / "last-session.md"
    destination.symlink_to(outside)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(handoff),
            "--accepted-at",
            "2026-08-02T12:00:00-0400",
            "--publish-to",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert handoff.read_bytes() == original
    assert outside.read_text() == "outside\n"


def test_publish_finalizes_shared_copy_without_mutating_private_source(tmp_path: Path) -> None:
    private = tmp_path / "private"
    shared = tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    handoff = private / "last-session.md"
    handoff.write_text(_complete_handoff(current_state="- Ready."))
    original = handoff.read_bytes()
    destination = shared / "last-session.md"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(handoff),
            "--accepted-at",
            "2026-08-02T12:00:00-0400",
            "--publish-to",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert handoff.read_bytes() == original
    assert "Lifecycle status: accepted by Stop" in destination.read_text()


def test_cli_rejects_post_cleanup_incomplete_handoff_without_writes(
    tmp_path: Path,
) -> None:
    private = tmp_path / "private"
    shared = tmp_path / "shared"
    private.mkdir()
    shared.mkdir()
    handoff = private / "last-session.md"
    handoff.write_text(
        _complete_handoff(
            current_state="- Natural Stop finalization remains pending.",
            next_steps="- Attempt natural Stop now.",
        )
    )
    os.chmod(handoff, 0o600)
    original = handoff.read_bytes()
    source_mode = stat.S_IMODE(handoff.stat().st_mode)
    destination = shared / "last-session.md"
    destination.write_text("shared handoff before failed finalization\n")
    os.chmod(destination, 0o640)
    published_before = destination.read_bytes()
    destination_mode = stat.S_IMODE(destination.stat().st_mode)
    private_entries = {path.name for path in private.iterdir()}
    shared_entries = {path.name for path in shared.iterdir()}

    check = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(handoff),
            "--check-complete",
        ],
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(handoff),
            "--accepted-at",
            "2026-08-02T12:00:00-0400",
            "--publish-to",
            str(destination),
        ],
        capture_output=True,
        text=True,
    )

    assert check.returncode == 1
    assert result.returncode == 1
    assert handoff.read_bytes() == original
    assert destination.read_bytes() == published_before
    assert stat.S_IMODE(handoff.stat().st_mode) == source_mode
    assert stat.S_IMODE(destination.stat().st_mode) == destination_mode
    assert {path.name for path in private.iterdir()} == private_entries
    assert {path.name for path in shared.iterdir()} == shared_entries
