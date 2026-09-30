"""Tests for check_html_report — structural gate for HTML reports and their slides."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_html_report as chr_

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "network/conventions/report-generator/assets/report-template.html"

FILL = {
    "TITLE": "Drug X slows proliferation",
    "PROJECT": "Example project",
    "DEK": "Drug X slows growth in a dose-dependent way.",
    "AUTHORS": "A. Author",
    "DATE": "2026-09-30",
    "ABSTRACT": "<p>Abstract text.</p>",
    "PROBLEM_STATEMENT": "<p>Problem text.</p>",
    "METHODS_OVERVIEW": "<p>Methods text.</p>",
    "RESULTS": (
        '<section id="result-growth"><h3>Treated cells grow more slowly than controls.</h3>'
        '<figure class="sci-figure" id="fig-growth" data-sci-diagram>'
        '<div class="sci-media"><svg viewBox="0 0 4 4"></svg></div>'
        "<figcaption>Schematic.</figcaption></figure></section>"
    ),
    "CONCLUSIONS": "<p>Conclusions.</p>",
    "NEXT_STEPS": "<p>Next.</p>",
    "PROVENANCE": "<p>Provenance.</p>",
    "SUPPLEMENT": "<p>Supplement.</p>",
}


def slide(i: int, title: str | None = None, source: str = "#result-growth", body: str = "<p>Short body text.</p>", extra: str = "") -> str:
    title = title if title is not None else f"Treated cells grew more slowly in experiment {'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty twentyone'.split()[i]}."
    src = f' data-source="{source}"' if source is not None else ""
    return f'<section class="slide"{src}{extra}><h2>{title}</h2><div class="slide-body">{body}</div></section>'


def build(n_slides: int = 12, slides: list[str] | None = None, **overrides) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    fill = dict(FILL, **overrides)
    if slides is None:
        slides = [slide(i) for i in range(n_slides - 1)]
    fill["SLIDES"] = "\n".join(slides)
    for key, value in fill.items():
        text = text.replace(f"%%{key}%%", value)
    return text


def check(tmp_path: Path, text: str, *args: str):
    p = tmp_path / "report.html"
    p.write_text(text, encoding="utf-8")
    return chr_.check_file(p, **_opts(args))


def _opts(args):
    return {"min_slides": 10, "max_slides": 20} | dict(a.split("=", 1) for a in args)


def codes(findings, severity=None):
    return sorted({f.code for f in findings if severity is None or f.severity == severity})


def test_template_filled_with_valid_content_passes(tmp_path):
    assert check(tmp_path, build()) == []


def test_unfilled_template_reports_placeholders(tmp_path):
    findings = check(tmp_path, TEMPLATE.read_text(encoding="utf-8"))
    assert "placeholder" in codes(findings, "error")


@pytest.mark.parametrize("n,ok", [(9, False), (10, True), (20, True), (21, False)])
def test_slide_count_bounds_include_title_slide(tmp_path, n, ok):
    extra_titles = [slide(i % 20, title=f"Slide number {'abcdefghijklmnopqrstu'[i]} shows one distinct point.") for i in range(n - 1)]
    findings = check(tmp_path, build(slides=extra_titles))
    assert ("slide-count" in codes(findings, "error")) is (not ok)


@pytest.mark.parametrize(
    "title",
    [
        "Results: growth slows",                         # colon, no period
        "Growth slows in treated cells",                 # no terminal period
        "Does growth slow in treated cells?",            # question
        "Growth slows. Death does not change.",          # two sentences
        "Growth slows; death does not change.",          # semicolon joins two points
        "Results.",                                      # topic word
        "Growth slows.",                                 # too short to carry subject/verb/object
    ],
)
def test_non_sentence_titles_fail(tmp_path, title):
    slides = [slide(i) for i in range(10)] + [slide(10, title=title)]
    assert "slide-title" in codes(check(tmp_path, build(slides=slides)), "error")


@pytest.mark.parametrize(
    "title",
    [
        "Treated cells grow more slowly than controls.",
        'Drug X cuts growth by <span data-sci-val="frac">41.2%</span> at the top dose.',
        "The effect holds in all three replicate experiments (Fig. 2 shows each).",
        "Growth at 2.5 µM matches growth in untreated cells.",
    ],
)
def test_sentence_titles_pass(tmp_path, title):
    slides = [slide(i) for i in range(10)] + [slide(10, title=title)]
    assert "slide-title" not in codes(check(tmp_path, build(slides=slides)))


def test_title_slide_must_be_first_and_unique(tmp_path):
    text = build()
    moved = text.replace('<section class="slide slide--title" data-slide="title">', '<section class="slide slide--title">', 1)
    assert moved != text
    assert "title-slide" in codes(check(tmp_path, moved), "error")
    slides = [slide(i) for i in range(10)] + ['<section class="slide slide--title" data-slide="title"><h1>Again</h1></section>']
    assert "title-slide" in codes(check(tmp_path, build(slides=slides)), "error")


def test_each_slide_needs_exactly_one_h2(tmp_path):
    slides = [slide(i) for i in range(10)] + [
        '<section class="slide" data-source="#result-growth"><div class="slide-body"><p>No title here.</p></div></section>'
    ]
    assert "slide-title" in codes(check(tmp_path, build(slides=slides)), "error")


def test_slide_source_is_required_and_must_resolve_outside_deck(tmp_path):
    missing = [slide(i) for i in range(10)] + [slide(10, source=None)]
    assert "slide-source" in codes(check(tmp_path, build(slides=missing)), "error")
    dangling = [slide(i) for i in range(10)] + [slide(10, source="#nowhere")]
    assert "slide-source" in codes(check(tmp_path, build(slides=dangling)), "error")
    into_deck = [slide(i) for i in range(10)] + [slide(10, source="#deck")]
    assert "slide-source" in codes(check(tmp_path, build(slides=into_deck)), "error")


def test_slide_figure_refs_must_name_report_figures_and_be_single(tmp_path):
    ok = [slide(i) for i in range(10)] + [slide(10, body='<div class="slide-figure" data-fig-ref="fig-growth"></div>')]
    assert check(tmp_path, build(slides=ok)) == []
    bad = [slide(i) for i in range(10)] + [slide(10, body='<div class="slide-figure" data-fig-ref="fig-none"></div>')]
    assert "slide-figure" in codes(check(tmp_path, build(slides=bad)), "error")
    two = '<div class="slide-figure" data-fig-ref="fig-growth"></div>' * 2
    double = [slide(i) for i in range(10)] + [slide(10, body=two)]
    assert "slide-figure" in codes(check(tmp_path, build(slides=double)), "error")


def test_wordy_slide_body_is_a_warning(tmp_path):
    body = "<p>" + " ".join(["word"] * 60) + "</p>"
    slides = [slide(i) for i in range(10)] + [slide(10, body=body)]
    findings = check(tmp_path, build(slides=slides))
    assert codes(findings, "warning") == ["slide-words"]
    assert codes(findings, "error") == []


def test_external_resources_break_self_containment(tmp_path):
    for snippet in (
        '<script src="https://cdn.example.com/x.js"></script>',
        '<link rel="stylesheet" href="https://fonts.example.com/f.css">',
        '<p><img alt="" src="figures/plot.png"></p>',
        '<style>@import url("https://x.example/y.css");</style>',
        '<p><iframe src="https://example.com"></iframe></p>',
    ):
        text = build(ABSTRACT=f"<p>Abstract.</p>{snippet}")
        assert "self-contained" in codes(check(tmp_path, text), "error"), snippet


def test_external_hyperlinks_and_data_uris_are_fine(tmp_path):
    text = build(ABSTRACT='<p>See <a href="https://doi.org/10.1/x">the paper</a>.</p><p><img alt="" src="data:image/png;base64,AA=="></p>')
    assert "self-contained" not in codes(check(tmp_path, text))


def test_missing_runtime_is_an_error_and_modified_runtime_a_warning(tmp_path):
    text = build()
    no_runtime = re.sub(r'<script id="sci-report-runtime">.*?</script>', "", text, flags=re.S)
    assert "runtime" in codes(check(tmp_path, no_runtime), "error")
    edited = text.replace("Mycelium report runtime", "Mycelium report runtime (edited)")
    findings = check(tmp_path, edited)
    assert codes(findings, "warning") == ["runtime"]


def test_cli_exit_codes_and_strict(tmp_path, capsys):
    p = tmp_path / "report.html"
    p.write_text(build(), encoding="utf-8")
    assert chr_.main([str(p)]) == 0
    body = "<p>" + " ".join(["word"] * 60) + "</p>"
    p.write_text(build(slides=[slide(i) for i in range(10)] + [slide(10, body=body)]), encoding="utf-8")
    assert chr_.main([str(p)]) == 0
    assert chr_.main([str(p), "--strict"]) == 1
    p.write_text(build(n_slides=5), encoding="utf-8")
    assert chr_.main([str(p)]) == 1
    out = capsys.readouterr().out
    assert re.search(r"report\.html:\d+:\d+: \[slide-count\]", out)


def test_main_text_stats_exclude_supplement_provenance_and_deck(tmp_path):
    text = build(
        ABSTRACT="<p>" + " ".join(["alpha"] * 30) + "</p>",
        SUPPLEMENT="<p>" + " ".join(["beta"] * 500) + "</p>",
        PROVENANCE="<p>" + " ".join(["gamma"] * 200) + "</p>",
    )
    stats = chr_.main_text_stats(text)
    assert stats["figures"] == 1
    # 30 abstract words + short fixed sections and headings; none of the 700 excluded words
    assert 30 <= stats["words"] < 120


def test_oversized_report_is_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(chr_, "MAX_FILE_BYTES", 1000)
    findings = check(tmp_path, build())
    assert codes(findings, "warning") == ["file-size"]
    assert codes(findings, "error") == []


def test_report_only_mode_skips_deck_rules_before_phase_nine(tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in FILL.items():
        text = text.replace(f"%%{key}%%", value)
    # %%SLIDES%% is still in place and only the title slide exists.
    full = check(tmp_path, text)
    assert {"placeholder", "slide-count"} <= set(codes(full, "error"))
    assert chr_.check_source(text, report_only=True) == []
    # Report-only still catches a real placeholder outside the deck.
    leftover = text.replace("Conclusions.", "%%CONCLUSIONS%%")
    assert codes(chr_.check_source(leftover, report_only=True), "error") == ["placeholder"]


def test_cli_report_only_flag(tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8")
    for key, value in FILL.items():
        text = text.replace(f"%%{key}%%", value)
    p = tmp_path / "report.html"
    p.write_text(text, encoding="utf-8")
    assert chr_.main([str(p)]) == 1
    assert chr_.main([str(p), "--report-only"]) == 0


def test_runtime_is_compared_with_the_installed_pack_template(tmp_path):
    # A project whose installed pack carries an older template: the report
    # matches that template, so no runtime warning, even if the plugin moved on.
    installed = tmp_path / ".living/conventions/report-generator/assets/report-template.html"
    installed.parent.mkdir(parents=True)
    old_template = TEMPLATE.read_text(encoding="utf-8").replace("Mycelium report runtime", "Mycelium report runtime (older)")
    installed.write_text(old_template, encoding="utf-8")
    report_dir = tmp_path / "analysis/x/reports"
    report_dir.mkdir(parents=True)
    text = build().replace("Mycelium report runtime", "Mycelium report runtime (older)")
    p = report_dir / "x-report.html"
    p.write_text(text, encoding="utf-8")
    assert chr_.check_file(p) == []
    assert chr_.find_template(p) == installed
    assert codes(chr_.check_file(p, template=TEMPLATE), "warning") == ["runtime"]


def test_style_attributes_and_link_preloads_break_self_containment(tmp_path):
    for snippet in (
        '<div style="background:url(figs/a.png)">x</div>',
        '<link rel="preload" href="https://cdn.example.com/f.woff2" as="font">',
        '<link rel="icon" href="favicon.png">',
    ):
        text = build(ABSTRACT=f"<p>Abstract.</p>{snippet}")
        assert "self-contained" in codes(check(tmp_path, text), "error"), snippet
    ok = build(ABSTRACT='<p>Abstract.</p><div style="background:url(data:image/png;base64,AA==)">x</div>'
                        '<link rel="canonical" href="https://example.org/report">')
    assert "self-contained" not in codes(check(tmp_path, ok))


def test_abbreviation_does_not_hide_a_second_sentence(tmp_path):
    slides = [slide(i) for i in range(10)] + [slide(10, title="Growth slows in Fig. A panels. Cells survive.")]
    assert "slide-title" in codes(check(tmp_path, build(slides=slides)), "error")
    ok = [slide(i) for i in range(10)] + [slide(10, title="Growth slows at high doses, e.g. 5 µM and above.")]
    assert "slide-title" not in codes(check(tmp_path, build(slides=ok)))


def test_runtime_block_is_found_by_parsing_not_pattern_matching(tmp_path):
    # A script whose string mentions the runtime's opening tag must not be
    # mistaken for the runtime (a regex would match from the string onward).
    decoy = "<script>const t = '<script id=\"sci-report-runtime\">';</script>"
    text = build(ABSTRACT=f"<p>Abstract.</p>{decoy}")
    assert "runtime" not in codes(check(tmp_path, text))


def test_any_element_with_class_slide_is_a_slide(tmp_path):
    slides = [slide(i) for i in range(10)] + ['<div class="slide" data-source="#result-growth"><p>No title.</p></div>']
    findings = check(tmp_path, build(slides=slides))
    assert "slide-title" in codes(findings, "error")


def test_figure_reference_must_use_the_runtime_selector(tmp_path):
    bare = [slide(i) for i in range(10)] + [slide(10, body='<div data-fig-ref="fig-growth"></div>')]
    found = check(tmp_path, build(slides=bare))
    assert "slide-figure" in codes(found, "error")


def test_gate_selectors_match_the_runtime():
    # The contract between the checkers and the runtime, pinned: if the
    # runtime's selectors change, this fails and the gates must follow.
    runtime = TEMPLATE.read_text(encoding="utf-8")
    for selector in ('.slide-figure[data-fig-ref]', 'script[type="application/json"][data-sci-data]',
                     '$$(".slide", deck)', 'figure[data-sci-interactive]'):
        assert selector in runtime, selector
