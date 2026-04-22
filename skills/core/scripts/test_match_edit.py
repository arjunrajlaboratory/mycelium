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
