#!/usr/bin/env python3
"""Tests for generate_menu.py."""

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import generate_menu as gm  # noqa: E402


@pytest.fixture()
def living_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".living"
    d.mkdir()
    (d / "learnings").mkdir()
    return d


def test_generate_menu_empty_learnings_produces_placeholder(living_dir: Path) -> None:
    out = living_dir / "MENU.md"
    gm.generate_menu(living_dir, out)
    content = out.read_text()
    assert "Mycelium Knowledge Menu" in content
    assert "Push-active: 0" in content
    assert "Pull-only: 0" in content
    assert "Total entries: 0" in content


def test_generate_menu_separates_push_and_pull(living_dir: Path) -> None:
    (living_dir / "learnings" / "figures.md").write_text(
        "---\n"
        "domain: figures\n"
        "description: color and DPI\n"
        "push_active: true\n"
        'matches: ["**/figures/**"]\n'
        "---\n"
        "## [2026-04-15] Colorblind palette\nbody\n"
        "## [2026-04-10] 300 DPI\nbody\n"
    )
    (living_dir / "learnings" / "debugging.md").write_text(
        "---\n"
        "domain: debugging\n"
        "description: runtime failures\n"
        "push_active: false\n"
        "---\n"
        "## [2026-04-01] matplotlib Agg\nbody\n"
    )
    (living_dir / "learnings" / "extraction.md").write_text(
        "---\n"
        "domain: extraction\n"
        "description: KG prompts\n"
        "push_active: true\n"
        'matches: ["**/Claim*.py"]\n'
        "---\n"
        "## [2026-04-18] Sonnet > Haiku\nbody\n"
    )

    out = living_dir / "MENU.md"
    gm.generate_menu(living_dir, out)
    content = out.read_text()

    assert "Push-active: 2" in content
    assert "Pull-only: 1" in content
    assert "Total entries: 4" in content
    push_section = content.split("**Push-active**")[1].split("**Pull-only**")[0]
    assert "**figures** (2) — color and DPI" in push_section
    assert "**extraction** (1) — KG prompts" in push_section
    pull_section = content.split("**Pull-only**")[1]
    assert "**debugging** (1) — runtime failures" in pull_section
