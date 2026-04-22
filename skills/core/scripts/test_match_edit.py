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


def test_matches_domain_recursive_glob() -> None:
    assert (
        me.matches_domain("docs/figures/panels/panel_b.py", ["**/figures/**"]) is True
    )
    assert me.matches_domain("src/other.py", ["**/figures/**"]) is False


def test_matches_domain_multiple_patterns() -> None:
    pats = ["**/figures/**", "**/panel_*.py", "**/composite*.py"]
    assert me.matches_domain("src/panel_a.py", pats) is True
    assert me.matches_domain("src/composite_v2.py", pats) is True
    assert me.matches_domain("src/unrelated.py", pats) is False


def test_matches_domain_empty_patterns() -> None:
    assert me.matches_domain("anything.py", []) is False


def test_parse_entries_splits_by_date_header() -> None:
    body = (
        "## [2026-04-15] First entry\n"
        "**Tags**: palette, colorblind\n"
        "body text one\n"
        "## [2026-04-10] Second entry\n"
        "**Tags**: dpi\n"
        "body text two\n"
    )
    entries = me.parse_entries(body)
    assert len(entries) == 2
    assert entries[0]["title"] == "First entry"
    assert entries[0]["date"] == "2026-04-15"
    assert entries[0]["tags"] == ["palette", "colorblind"]
    assert "body text one" in entries[0]["body"]


def test_parse_entries_extracts_triggers() -> None:
    body = '## [2026-04-15] Entry\n**Triggers**: ["palette", "color"]\nbody\n'
    entries = me.parse_entries(body)
    assert entries[0]["triggers"] == ["palette", "color"]


def test_score_entry_title_match_plus3() -> None:
    path_tokens = ["panel", "figures"]
    entry = {
        "title": "Panel layout best practice",
        "tags": [],
        "triggers": [],
        "body": "unrelated body text",
        "date": "2026-04-22",
    }
    score = me.score_entry(entry, path_tokens, today="2026-04-22")
    assert score == pytest.approx(3.5)


def test_score_entry_tags_plus2() -> None:
    entry = {
        "title": "Unrelated",
        "tags": ["panel"],
        "triggers": [],
        "body": "",
        "date": "2020-01-01",
    }
    score = me.score_entry(entry, ["panel"], today="2026-04-22")
    assert score == pytest.approx(2.0)


def test_score_entry_triggers_plus2() -> None:
    entry = {
        "title": "X",
        "tags": [],
        "triggers": ["panel"],
        "body": "",
        "date": "2020-01-01",
    }
    score = me.score_entry(entry, ["panel"], today="2026-04-22")
    assert score == pytest.approx(2.0)


def test_score_entry_body_plus1() -> None:
    entry = {
        "title": "X",
        "tags": [],
        "triggers": [],
        "body": "first 500 chars mentioning panel somewhere",
        "date": "2020-01-01",
    }
    score = me.score_entry(entry, ["panel"], today="2026-04-22")
    assert score == pytest.approx(1.0)


def test_score_entry_recency_bonus_boundary() -> None:
    entry = {
        "title": "unrelated",
        "tags": [],
        "triggers": [],
        "body": "",
        "date": "2026-02-21",
    }
    score = me.score_entry(entry, ["nothing"], today="2026-04-22")
    assert score == pytest.approx(0.5)


def test_score_entry_body_first_500_chars_only() -> None:
    long_body = "x " * 300 + "panel"
    entry = {
        "title": "",
        "tags": [],
        "triggers": [],
        "body": long_body,
        "date": "2020-01-01",
    }
    score = me.score_entry(entry, ["panel"], today="2026-04-22")
    assert score == 0.0


def _mk_entry(title, score_hint, date="2026-04-22"):
    """Helper: build entry matched by ['panel']. score_hint indicates expected ballpark score."""
    if score_hint == 0:
        return {"title": title, "tags": [], "triggers": [], "body": "", "date": date}
    if score_hint == 2:
        return {
            "title": title,
            "tags": ["panel"],
            "triggers": [],
            "body": "",
            "date": "2020-01-01",
        }
    if score_hint == 3:
        return {
            "title": f"{title} panel",
            "tags": [],
            "triggers": [],
            "body": "",
            "date": "2020-01-01",
        }
    if score_hint == 5:
        return {
            "title": f"{title} panel",
            "tags": ["panel"],
            "triggers": [],
            "body": "",
            "date": "2020-01-01",
        }
    raise ValueError(f"unsupported score_hint {score_hint}")


def test_select_entries_drops_below_threshold() -> None:
    domains = [
        {
            "domain": "figures",
            "entries": [
                _mk_entry("low", 0),
                _mk_entry("hit", 3),
            ],
        }
    ]
    selected, dropped = me.select_entries(domains, ["panel"], k=3, today="2026-04-22")
    assert len(selected) == 1
    assert selected[0]["title"].startswith("hit")
    assert dropped == 1


def test_select_entries_respects_top_k() -> None:
    domains = [
        {
            "domain": "figures",
            "entries": [
                _mk_entry("a", 5),
                _mk_entry("b", 5),
                _mk_entry("c", 3),
                _mk_entry("d", 3),
                _mk_entry("e", 2),
            ],
        }
    ]
    selected, _ = me.select_entries(domains, ["panel"], k=3, today="2026-04-22")
    assert len(selected) == 3
    scores = [s["score"] for s in selected]
    assert scores == sorted(scores, reverse=True)


def test_select_entries_all_below_threshold_returns_empty() -> None:
    domains = [
        {
            "domain": "figures",
            "entries": [_mk_entry("low", 0), _mk_entry("also_low", 0)],
        }
    ]
    selected, dropped = me.select_entries(domains, ["panel"], k=3, today="2026-04-22")
    assert selected == []
    assert dropped == 2


def test_select_entries_cross_domain_merge() -> None:
    domains = [
        {"domain": "figures", "entries": [_mk_entry("f1", 3)]},
        {"domain": "extraction", "entries": [_mk_entry("e1", 5)]},
    ]
    selected, _ = me.select_entries(domains, ["panel"], k=3, today="2026-04-22")
    assert len(selected) == 2
    assert selected[0]["title"].startswith("e1")
    assert selected[0]["domain"] == "extraction"
    assert selected[1]["domain"] == "figures"


CAP_BYTES = 8000


def test_render_selected_under_cap_emits_all() -> None:
    selected = [
        {
            "domain": "figures",
            "title": "T1",
            "date": "2026-04-22",
            "score": 5,
            "entry": {
                "title": "T1",
                "date": "2026-04-22",
                "tags": [],
                "triggers": [],
                "body": "short body\n",
            },
        }
    ]
    rendered, truncated = me.render_selected(selected, cap_bytes=CAP_BYTES)
    assert "T1" in rendered
    assert "short body" in rendered
    assert truncated is False


def test_render_selected_over_cap_drops_lowest_score_first() -> None:
    big_body = "x" * 6000
    selected = [
        {
            "domain": "figures",
            "title": "high",
            "date": "2026-04-22",
            "score": 5,
            "entry": {
                "title": "high",
                "date": "2026-04-22",
                "tags": [],
                "triggers": [],
                "body": big_body,
            },
        },
        {
            "domain": "figures",
            "title": "low",
            "date": "2026-04-22",
            "score": 3,
            "entry": {
                "title": "low",
                "date": "2026-04-22",
                "tags": [],
                "triggers": [],
                "body": big_body,
            },
        },
    ]
    rendered, truncated = me.render_selected(selected, cap_bytes=CAP_BYTES)
    assert "high" in rendered
    assert rendered.count("## [2026-04-22] low") == 0
    assert truncated is True


def test_render_selected_single_over_cap_mid_body_truncation() -> None:
    huge_body = "x" * 20000
    selected = [
        {
            "domain": "figures",
            "title": "alone",
            "date": "2026-04-22",
            "score": 5,
            "entry": {
                "title": "alone",
                "date": "2026-04-22",
                "tags": [],
                "triggers": [],
                "body": huge_body,
            },
        }
    ]
    rendered, truncated = me.render_selected(selected, cap_bytes=CAP_BYTES)
    assert truncated is True
    assert len(rendered.encode("utf-8")) <= CAP_BYTES + 200
    assert "… [truncated; Read .living/learnings/figures.md]" in rendered
