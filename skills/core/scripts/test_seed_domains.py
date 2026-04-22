#!/usr/bin/env python3
"""Tests for seed_domains.py."""

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import seed_domains as sd  # noqa: E402


@pytest.fixture()
def living_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".living"
    d.mkdir()
    return d


@pytest.fixture()
def monolith(living_dir: Path) -> Path:
    p = living_dir / "learnings.md"
    p.write_text(
        "## [2026-04-15] Colorblind palette required\n"
        "**Category**: gotcha\n"
        "**Tags**: palette, colorblind\n"
        "Use viridis.\n"
        "\n"
        "## [2026-04-10] 300 DPI minimum\n"
        "**Tags**: dpi\n"
        "Use dpi=300.\n"
        "\n"
        "## [2026-04-05] Sonnet > Haiku for abstracts\n"
        "**Tags**: extraction, model-choice\n"
        "Prefer sonnet.\n"
    )
    return p


def test_parse_seeds_yaml(tmp_path: Path) -> None:
    f = tmp_path / "seeds.yaml"
    f.write_text(
        "seeds:\n"
        "  - title: Colorblind palette required\n"
        "    domain: figures\n"
        "    tags: [palette, colorblind]\n"
        "  - title: 300 DPI minimum\n"
        "    domain: figures\n"
    )
    seeds = sd.parse_seeds(f)
    assert len(seeds) == 2
    assert seeds[0]["title"] == "Colorblind palette required"
    assert seeds[0]["domain"] == "figures"
    assert seeds[0]["tags"] == ["palette", "colorblind"]
    assert seeds[1]["tags"] == []


def test_extract_entry_from_monolith(monolith: Path) -> None:
    entry = sd.extract_entry(monolith, "Colorblind palette required")
    assert entry is not None
    assert entry["date"] == "2026-04-15"
    assert entry["title"] == "Colorblind palette required"
    assert "Use viridis." in entry["body"]


def test_extract_entry_missing_returns_none(monolith: Path) -> None:
    entry = sd.extract_entry(monolith, "Nonexistent title")
    assert entry is None
