#!/usr/bin/env python3
"""Tests for match_edit.py."""

import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import match_edit as me  # noqa: E402


@pytest.fixture()
def learnings_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".living" / "learnings"
    d.mkdir(parents=True)
    return d


def test_collect_push_active_requires_push_active_true(learnings_dir: Path) -> None:
    (learnings_dir / "pull.md").write_text(
        "---\n"
        "domain: pull\n"
        "description: x\n"
        'matches: ["**/*.py"]\n'
        "---\n"
        "## [2026-04-01] entry\nbody\n"
    )
    result = me.collect_push_active_domains(learnings_dir.parent)
    assert result == []


def test_collect_push_active_requires_matches_field(learnings_dir: Path) -> None:
    (learnings_dir / "nomatch.md").write_text(
        "---\ndomain: nomatch\ndescription: x\npush_active: true\n---\n"
    )
    result = me.collect_push_active_domains(learnings_dir.parent)
    assert result == []


def test_collect_push_active_returns_valid_files(learnings_dir: Path) -> None:
    (learnings_dir / "figures.md").write_text(
        "---\n"
        "domain: figures\n"
        "description: color\n"
        "push_active: true\n"
        'matches: ["**/figures/**"]\n'
        "---\n"
        "## [2026-04-01] entry\nbody\n"
    )
    result = me.collect_push_active_domains(learnings_dir.parent)
    assert len(result) == 1
    assert result[0]["domain"] == "figures"
    assert result[0]["matches"] == ["**/figures/**"]


def test_tokenize_path_basic() -> None:
    tokens = me.tokenize_path("docs/figures/panels/panel_b_phase1.py")
    assert "panel" in tokens
    assert "b" not in tokens  # single letter filtered
    assert "phase1" in tokens
    assert "panels" in tokens
    assert "figures" in tokens


def test_tokenize_path_filters_empty_and_numeric(tmp_path: Path) -> None:
    tokens = me.tokenize_path("docs/figures/2026/panel_b.py")
    assert "" not in tokens
    assert "2026" not in tokens
    assert "figures" in tokens
    assert "panel" in tokens


def test_tokenize_path_lowercase_and_dedup() -> None:
    tokens = me.tokenize_path("Docs/FIGURES/Figures_panel.py")
    lowered = {t.lower() for t in tokens}
    assert lowered == set(tokens)
    assert tokens.count("figures") == 1


def test_tokenize_text_basic() -> None:
    tokens = me.tokenize_text("Colorblind-safe palette for matplotlib")
    assert "colorblind" in tokens
    assert "safe" in tokens
    assert "palette" in tokens
    assert "matplotlib" in tokens
    assert "for" in tokens  # stopwords NOT filtered in Phase 1


def test_tokenize_text_filters_empty_numeric() -> None:
    tokens = me.tokenize_text("2026 update")
    assert "2026" not in tokens
    assert "update" in tokens
