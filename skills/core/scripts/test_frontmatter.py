#!/usr/bin/env python3
"""Tests for _frontmatter.py (minimal YAML frontmatter parser)."""

import sys
from pathlib import Path


_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import _frontmatter as fm  # noqa: E402


def test_parse_returns_empty_dict_when_no_frontmatter(tmp_path: Path) -> None:
    f = tmp_path / "nofm.md"
    f.write_text("## [2026-01-01] Title\n\nbody\n")
    assert fm.parse(f) == ({}, "## [2026-01-01] Title\n\nbody\n")


def test_parse_scalar_and_list_fields(tmp_path: Path) -> None:
    f = tmp_path / "fm.md"
    f.write_text(
        "---\n"
        "domain: figures\n"
        "description: color and DPI rules\n"
        "push_active: true\n"
        "matches:\n"
        '  - "**/figures/**"\n'
        '  - "**/panel_*.py"\n'
        "top_k_push: 3\n"
        "---\n"
        "body\n"
    )
    meta, body = fm.parse(f)
    assert meta == {
        "domain": "figures",
        "description": "color and DPI rules",
        "push_active": True,
        "matches": ["**/figures/**", "**/panel_*.py"],
        "top_k_push": 3,
    }
    assert body == "body\n"


def test_parse_malformed_frontmatter_returns_empty(tmp_path: Path) -> None:
    f = tmp_path / "bad.md"
    f.write_text("---\nthis is not valid\n## missing-close\nbody\n")
    meta, _ = fm.parse(f)
    assert meta == {}


def test_parse_boolean_and_int_coercion(tmp_path: Path) -> None:
    f = tmp_path / "coerce.md"
    f.write_text("---\npush_active: false\ntop_k_push: 5\ntoken_cap: 1500\n---\n")
    meta, _ = fm.parse(f)
    assert meta["push_active"] is False
    assert meta["top_k_push"] == 5
    assert meta["token_cap"] == 1500


def test_parse_quoted_strings_strip_quotes(tmp_path: Path) -> None:
    f = tmp_path / "q.md"
    f.write_text('---\ndomain: "figures"\nmatches:\n  - "**/figures/**"\n---\n')
    meta, _ = fm.parse(f)
    assert meta["domain"] == "figures"
    assert meta["matches"] == ["**/figures/**"]
