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
