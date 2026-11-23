"""Tests for atomic handoff lifecycle-status finalization."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap
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


def _documented_handoff_template() -> str:
    skill = (Path(__file__).parents[1] / "SKILL.md").read_text()
    start = skill.rindex("\n", 0, skill.index("SESSION RESUME — Last session")) + 1
    end = skill.rindex("\n", 0, skill.index("```", start)) + 1
    return textwrap.dedent(skill[start:end])


def test_documented_template_is_complete_and_nests_continuity_subsections() -> None:
    from finalize_handoff import handoff_is_complete_after_cleanup

    template = _documented_handoff_template()
    lines = template.splitlines()

    assert handoff_is_complete_after_cleanup(template)
    assert lines.index("## Key decisions made") < lines.index("### Locked decisions")
    assert lines.index("### Locked decisions") < lines.index("## Blockers & surprises")
    assert lines.index("## Blockers & surprises") < lines.index(
        "### Dead ends — do not redo"
    )
    assert lines.index("### Dead ends — do not redo") < lines.index("## Current state")


def test_handoff_with_continuity_subsections_is_preserved() -> None:
    handoff = _complete_handoff().replace(
        "## Key decisions made\n- Preserve complete authored handoffs.\n",
        "## Key decisions made\n- Preserve complete authored handoffs.\n\n"
        "### Locked decisions\n- Use DESeq2 (2026-10-14, Pick the DE method).\n",
    ).replace(
        "## Blockers & surprises\n- None.\n",
        "## Blockers & surprises\n- None.\n\n"
        "### Dead ends — do not redo\n- ComBat on counts: removed the effect.\n",
    )

    finalized = finalize(handoff, "2026-10-14T12:00:00-0400")

    assert "### Locked decisions\n- Use DESeq2" in finalized
    assert "### Dead ends — do not redo\n- ComBat on counts" in finalized


def test_section_holding_only_subheadings_is_incomplete() -> None:
    from finalize_handoff import handoff_is_complete_after_cleanup

    handoff = _complete_handoff().replace(
        "## Key decisions made\n- Preserve complete authored handoffs.\n",
        "## Key decisions made\n\n### Locked decisions\n",
    )

    assert not handoff_is_complete_after_cleanup(handoff)


NUMBERED_HANDOFF = Path(__file__).with_name("testdata") / "antigravity-app-numbered-handoff.md"


def _diagnose(tmp_path: Path, content: str) -> subprocess.CompletedProcess:
    handoff = tmp_path / "last-session.md"
    handoff.write_text(content)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--handoff", str(handoff), "--diagnose"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_diagnose_accepts_canonical_and_legacy_handoffs(tmp_path: Path) -> None:
    legacy = (
        "## Current State\n- Tests passed.\n\n"
        "## What Was Done\n- Lifecycle smoke audit.\n\n"
        "## Key Decisions\n- Preserve the authored handoff.\n\n"
        "## Next Steps\n- Continue review.\n\n"
        "## Relevant Files\n- `.living/learnings.md`.\n"
    )
    for content in (_complete_handoff(), legacy):
        result = _diagnose(tmp_path, content)
        assert (result.returncode, result.stdout) == (0, "")


def test_diagnose_names_the_numbered_headings_of_a_real_app_handoff(
    tmp_path: Path,
) -> None:
    result = _diagnose(tmp_path, NUMBERED_HANDOFF.read_text(encoding="utf-8"))

    assert result.returncode == 3
    assert result.stdout.count("\n") == 1
    assert result.stdout.strip() == (
        "missing `## What was worked on`, `## Key decisions made`, "
        "`## Blockers & surprises`, `## Current state`, `## Next steps`"
    )


def test_diagnose_names_a_section_holding_only_a_subheading(tmp_path: Path) -> None:
    handoff = _complete_handoff().replace(
        "## Key decisions made\n- Preserve complete authored handoffs.\n",
        "## Key decisions made\n\n### Locked decisions\n",
    )

    result = _diagnose(tmp_path, handoff)

    assert result.returncode == 3
    assert result.stdout.strip() == "`## Key decisions made` has no prose or bullet"


def test_diagnose_explains_a_stripped_stop_status_bullet(tmp_path: Path) -> None:
    handoff = _complete_handoff(
        current_state="- Natural Stop finalization remains pending."
    )

    result = _diagnose(tmp_path, handoff)

    assert result.returncode == 3
    assert "`## Current state` has no prose or bullet" in result.stdout
    assert "1 Stop-status line(s) are removed before the check" in result.stdout
    assert "Natural Stop finalization remains pending." in result.stdout


def test_diagnose_names_duplicated_and_misordered_headings(tmp_path: Path) -> None:
    duplicated = _complete_handoff() + "\n## Next steps\n- Again.\n"
    misordered = _complete_handoff().replace(
        "## What was worked on\n- Lifecycle finalization.\n\n", ""
    ) + "\n## What was worked on\n- Lifecycle finalization.\n"

    assert "`## Next steps` appears 2 times" in _diagnose(tmp_path, duplicated).stdout
    assert "headings out of order (`## Key decisions made`" in _diagnose(
        tmp_path, misordered
    ).stdout


def test_diagnose_fails_closed_to_an_error_status_on_unreadable_input(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--handoff",
            str(tmp_path / "absent.md"),
            "--diagnose",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""


def test_byte_order_mark_and_trailing_heading_space_are_complete(
    tmp_path: Path,
) -> None:
    # Both are invisible in an editor; the check and the diagnosis share one
    # normalisation, so neither costs the agent its authored handoff.
    for raw in (
        b"\xef\xbb\xbf" + _complete_handoff().encode(),
        _complete_handoff().replace("## Current state", "## Current state  \t").encode(),
    ):
        handoff = tmp_path / "last-session.md"
        handoff.write_bytes(raw)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--handoff", str(handoff), "--diagnose"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode, result.stdout) == (0, "")
        assert finalize(raw.decode("utf-8"), "2026-11-23T12:00:00-0500").count(
            "﻿"
        ) == 0


def test_diagnose_names_an_empty_section_before_a_duplicate(tmp_path: Path) -> None:
    handoff = _complete_handoff().replace(
        "## Current state\n- Analysis complete.\n\n", "## Current state\n\n"
    ) + "\n## Next steps\n- A second list.\n"

    result = _diagnose(tmp_path, handoff)

    assert result.returncode == 3
    assert "`## Next steps` appears 2 times" in result.stdout
    assert "`## Current state` has no prose or bullet" in result.stdout


LEGACY_WITH_FENCED_EXAMPLE = (
    "## Current State\n- Tests passed.\n\n"
    "## What Was Done\n- Documented the handoff format:\n\n"
    "{fence}markdown\n## Next Steps  \n- example bullet\n{fence}\n\n"
    "## Key Decisions\n- Preserve the authored handoff.\n\n"
    "## Next Steps\n- Continue review.\n\n"
    "## Relevant Files\n- `.living/learnings.md`.\n"
)


def test_headings_inside_fenced_code_are_examples_not_sections(tmp_path: Path) -> None:
    from finalize_handoff import handoff_is_complete_after_cleanup

    canonical_example = _complete_handoff().replace(
        "## Key decisions made\n- Preserve complete authored handoffs.\n",
        "## Key decisions made\n- Preserve complete authored handoffs.\n\n"
        "```\n## Current state\n```\n",
    )
    for content in (
        LEGACY_WITH_FENCED_EXAMPLE.format(fence="```"),
        LEGACY_WITH_FENCED_EXAMPLE.format(fence="~~~~"),
        canonical_example,
    ):
        assert handoff_is_complete_after_cleanup(content)
        result = _diagnose(tmp_path, content)
        assert (result.returncode, result.stdout) == (0, "")


FENCE = "`" * 3
LIST_ITEM_FENCE = "\n".join((
    "## Current State", "- Checks pass.", "",
    "## What Was Done", "- " + FENCE + "sh", "  python -m pytest -q", "  " + FENCE, "",
    "## Key Decisions", "- Keep the current format.", "",
    "## Next Steps", "- Publish the report.", "",
    "## Relevant Files", "- report.md", "",
))
NESTED_FENCE = "\n".join((
    "## What was worked on", "- Example:", "",
    "````markdown", FENCE, "## Current state", FENCE, "````", "",
    "## Key decisions made", "- None.", "",
    "## Blockers & surprises", "- None.", "",
    "## Current state", "- Ready.", "",
    "## Next steps", "- " + FENCE + "unclosed inline example", "- Continue.", "",
))
UNCLOSED_FENCE = _complete_handoff().replace(
    "## Current state", FENCE + "\n## Current state"
)


def _main_accepts(content: str) -> bool:
    """origin/main's validator, the floor this branch must never go below."""
    source = subprocess.run(
        ["git", "show", "origin/main:skills/core/scripts/finalize_handoff.py"],
        capture_output=True, text=True, check=False, cwd=Path(__file__).parent,
    )
    if source.returncode:
        import pytest

        pytest.skip("origin/main is not available")
    namespace: dict = {}
    exec(source.stdout, namespace)
    return namespace["handoff_is_complete_after_cleanup"](content)


def test_fences_never_make_the_check_stricter_than_main(tmp_path: Path) -> None:
    from finalize_handoff import handoff_is_complete_after_cleanup

    cases = {
        "list-item fence": LIST_ITEM_FENCE,
        "nested fence": NESTED_FENCE,
        "unclosed fence": UNCLOSED_FENCE,
        "handoff wrapped in a fence": FENCE + "markdown\n" + _complete_handoff() + FENCE + "\n",
    }
    for name, content in cases.items():
        if _main_accepts(content):
            assert handoff_is_complete_after_cleanup(content), name
            result = _diagnose(tmp_path, content)
            assert (result.returncode, result.stdout) == (0, ""), name
    # The reviewer's regressions: main accepts both, so the branch must too.
    assert _main_accepts(cases["list-item fence"])
    assert _main_accepts(cases["unclosed fence"])
    # Beyond main: a heading inside a closed column-0 fence is an example.
    assert handoff_is_complete_after_cleanup(cases["nested fence"])


def test_a_fenced_example_counts_as_section_body() -> None:
    from finalize_handoff import handoff_is_complete_after_cleanup

    content = _complete_handoff().replace(
        "## Blockers & surprises\n- None.\n",
        "## Blockers & surprises\n```\n## not a section\n```\n",
    )

    assert handoff_is_complete_after_cleanup(content)
