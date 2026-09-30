"""Tests for the HTML report template and its worked example.

Static tests always run. Browser tests drive the example report in headless
Chromium through Playwright and skip when Playwright (or a browser) is not
installed; CI installs both.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_html_report
import sync_html_report

REPO = Path(__file__).resolve().parents[3]
ASSETS = REPO / "network/conventions/report-generator/assets"
TEMPLATE = ASSETS / "report-template.html"
EXAMPLE_DIR = ASSETS / "html-example"
EXAMPLE = EXAMPLE_DIR / "reports/example-report.html"
EXAMPLE_MANIFEST = EXAMPLE_DIR / "reports/.manifest.json"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_example", EXAMPLE_DIR / "build_example.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------


def test_template_has_no_nested_comments():
    # HTML comments do not nest: an inner "-->" ends the outer comment early
    # and the rest of the guidance renders as page content.
    for m in re.finditer(r"<!--(.*?)-->", TEMPLATE.read_text(encoding="utf-8"), re.S):
        assert "<!--" not in m.group(1), m.group(0)[:120]


def test_example_is_rebuilt_from_current_template(tmp_path):
    _load_builder().build(tmp_path)
    for rel in ("reports/example-report.html", "reports/.manifest.json",
                "outputs/figures/dose_response.svg", "outputs/growth_counts.json"):
        assert (tmp_path / rel).read_bytes() == (EXAMPLE_DIR / rel).read_bytes(), (
            f"{rel} is stale; run python {EXAMPLE_DIR.relative_to(REPO)}/build_example.py"
        )


def test_example_passes_structure_gate_strictly():
    findings = check_html_report.check_file(EXAMPLE)
    assert findings == []


def test_example_is_fully_synced():
    assert sync_html_report.main([str(EXAMPLE), f"--manifest={EXAMPLE_MANIFEST}", "--check"]) == 0


def test_example_passes_scitexlintr():
    scitexlintr = pytest.importorskip("scitexlintr")
    findings = scitexlintr.lint_file(EXAMPLE, manifest_path=EXAMPLE_MANIFEST)
    assert findings == [], [f"{f.line}:{f.col} {f.rule} {f.message}" for f in findings]


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        launched = None
        for kwargs in ({}, {"channel": "chrome"}):
            try:
                launched = p.chromium.launch(**kwargs)
                break
            except Exception:  # noqa: BLE001 - try the next browser source
                continue
        if launched is None:
            pytest.skip("no Chromium available for Playwright")
        yield launched
        launched.close()


@pytest.fixture
def page(browser):
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.errors = []
    pg.requests = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type in ("error", "warning") else None)
    pg.on("request", lambda r: pg.requests.append(r.url))
    pg.goto(EXAMPLE.as_uri())
    pg.wait_for_function("window.SciReport && document.querySelector('.deck-progress button')")
    yield pg
    ctx.close()


def state(pg):
    return pg.evaluate(
        "({open: SciReport.presenting, current: SciReport.current, count: SciReport.count,"
        " hash: location.hash, hidden: document.getElementById('deck').hidden,"
        " counter: document.querySelector('.deck-count').textContent,"
        " done: document.querySelectorAll('.deck-progress .is-done').length})"
    )


def test_loads_clean_and_self_contained(page):
    assert page.errors == []
    assert [u for u in page.requests if not u.startswith("file:")] == []


def test_figures_and_cross_references_are_numbered(page):
    assert page.locator('a.xref[href="#fig-growth"]').first.text_content() == "Figure 2"
    assert page.locator('a.xref[href="#fig-dose"]').first.text_content() == "Figure 3"


def test_present_button_opens_deck_with_progress(page):
    page.click("[data-present]")
    s = state(page)
    assert s["open"] and not s["hidden"]
    assert s["current"] == 0 and s["hash"] == "#slides/1"
    assert s["counter"] == f"1 / {s['count']}" and s["done"] == 1
    assert page.locator(".deck-progress button").count() == s["count"]


def test_keyboard_navigation(page):
    page.click("[data-present]")
    for key, expected in (("ArrowRight", 1), ("Space", 2), ("PageDown", 3), ("ArrowLeft", 2),
                          ("End", None), ("Home", 0)):
        page.keyboard.press(key)
        s = state(page)
        assert s["current"] == (s["count"] - 1 if expected is None else expected), key
        assert s["done"] == s["current"] + 1
        assert s["hash"] == f"#slides/{s['current'] + 1}"
    page.keyboard.press("ArrowLeft")
    assert state(page)["current"] == 0  # clamps at the first slide


def test_click_navigation_and_progress_jump(page):
    page.click("[data-present]")
    vp = page.locator(".deck-viewport").bounding_box()
    page.mouse.click(vp["x"] + vp["width"] * 0.8, vp["y"] + vp["height"] / 2)
    assert state(page)["current"] == 1
    page.mouse.click(vp["x"] + vp["width"] * 0.1, vp["y"] + vp["height"] / 2)
    assert state(page)["current"] == 0
    page.locator(".deck-progress button").nth(6).click()
    assert state(page)["current"] == 6


def test_escape_returns_to_the_slides_report_section(page):
    page.click("[data-present]")
    page.locator(".deck-progress button").nth(6).click()  # a mid-report section
    source = page.evaluate("document.querySelectorAll('#deck .slide')[SciReport.current].dataset.source")
    page.keyboard.press("Escape")
    s = state(page)
    assert not s["open"] and s["hidden"]
    assert s["hash"] == source
    top = page.evaluate(f"document.querySelector('{source}').getBoundingClientRect().top")
    assert 0 <= top < 100


def test_slide_source_link_returns_to_report(page):
    page.goto(EXAMPLE.as_uri() + "#slides/4")
    page.wait_for_function("SciReport.presenting")
    page.locator("#deck .slide.is-current .slide-source").click()
    assert not state(page)["open"]
    assert state(page)["hash"] == "#result-growth"


def test_deep_link_opens_requested_slide(page):
    page.goto(EXAMPLE.as_uri() + "#slides/5")
    page.wait_for_function("SciReport.presenting")
    assert state(page)["current"] == 4


def test_fullscreen_toggle_and_exit_returns_to_report(page):
    # Count fullscreenchange events so each step waits for the transition to
    # finish (Chrome rejects a new request while one is still settling).
    page.evaluate("window.__fsc = 0; document.addEventListener('fullscreenchange', () => window.__fsc++)")
    page.click("[data-present]")
    page.keyboard.press("f")
    page.wait_for_function("window.__fsc === 1")
    assert page.evaluate("document.fullscreenElement && document.fullscreenElement.id") == "deck"
    page.keyboard.press("f")  # our own toggle leaves the deck open
    page.wait_for_function("window.__fsc === 2")
    assert page.evaluate("!document.fullscreenElement")
    assert state(page)["open"]
    page.keyboard.press("f")
    page.wait_for_function("window.__fsc === 3")
    # The browser consumes Esc in fullscreen and just exits fullscreen; any
    # exit we did not start is treated as escaping the slideshow.
    page.evaluate("document.exitFullscreen()")
    page.wait_for_function("window.__fsc === 4")
    assert not state(page)["open"]
    assert state(page)["hidden"]


def test_report_links_present_from_a_section(page):
    link = page.locator("#result-dose .present-here")
    assert link.count() == 1
    link.click()
    page.wait_for_function("SciReport.presenting")
    src = page.evaluate("document.querySelectorAll('#deck .slide')[SciReport.current].dataset.source")
    assert src == "#result-dose"


def test_interactive_figure_scrubs_plays_and_does_not_steal_slide_keys(page):
    fig = page.locator("main #fig-growth")
    readout = fig.locator("output[data-sci-live]")
    assert readout.text_content().startswith("Day 10")  # default state: last day
    fig.locator('input[type="range"]').fill("3")
    assert readout.text_content().startswith("Day 3")
    fig.locator("button", has_text="Play").click()
    page.wait_for_function(
        "document.querySelector('main #fig-growth input[type=range]').value === '10'", timeout=8000
    )
    page.goto(EXAMPLE.as_uri() + "#slides/5")
    page.wait_for_function("SciReport.presenting")
    slider = page.locator("#deck .slide.is-current input[type='range']")
    slider.focus()
    page.keyboard.press("ArrowLeft")
    assert state(page)["current"] == 4  # the slider handled the key, not the deck
    assert slider.input_value() == "9"


def test_slide_figures_are_copies_with_unique_ids(page):
    assert page.locator("#deck .slide-figure svg").count() >= 3
    dupes = page.evaluate(
        "(() => { const seen = {}, d = []; document.querySelectorAll('[id]').forEach(n => {"
        " if (seen[n.id]) d.push(n.id); seen[n.id] = 1; }); return d; })()"
    )
    assert dupes == []
    # clip-path / marker references inside copies resolve to the copy's own ids
    dangling = page.evaluate(
        "(() => [...document.querySelectorAll('#deck [clip-path], #deck [marker-end]')].flatMap(n =>"
        " [n.getAttribute('clip-path'), n.getAttribute('marker-end')]).filter(Boolean)"
        ".map(v => v.match(/#([^)]+)/)[1]).filter(id => !document.getElementById(id) ||"
        " !document.getElementById(id).closest('#deck')))()"
    )
    assert dangling == []


def test_phone_width_has_no_horizontal_scroll(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    pg = ctx.new_page()
    pg.goto(EXAMPLE.as_uri())
    assert pg.evaluate("document.documentElement.scrollWidth") <= 390
    ctx.close()


def test_print_shows_report_or_slides_by_mode(page):
    page.emulate_media(media="print")
    assert page.evaluate("getComputedStyle(document.getElementById('deck')).display") == "none"
    assert page.evaluate("getComputedStyle(document.querySelector('main')).display") != "none"
    page.emulate_media(media="screen")
    page.click("[data-present]")
    page.emulate_media(media="print")
    shown = page.evaluate(
        "[...document.querySelectorAll('#deck .slide')].filter(s => getComputedStyle(s).display !== 'none').length"
    )
    assert shown == state(page)["count"]
    assert page.evaluate("getComputedStyle(document.querySelector('.layout')).display") == "none"


def test_reduced_motion_disables_slide_animation(browser):
    ctx = browser.new_context(reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(EXAMPLE.as_uri() + "#slides/2")
    pg.wait_for_function("window.SciReport && SciReport.presenting")
    assert pg.evaluate("getComputedStyle(document.querySelector('.slide.is-current')).animationName") == "none"
    ctx.close()


# ---------------------------------------------------------------------------
# Round-two regressions (found by end-to-end runs on real reports)
# ---------------------------------------------------------------------------


def test_caption_labels_agree_with_cross_references(page):
    pairs = page.evaluate(
        "[...document.querySelectorAll('main a.xref')].map(a => {"
        " const t = document.querySelector(a.getAttribute('href'));"
        " const cap = t && (t.querySelector(':scope > figcaption, caption'));"
        " const lab = cap && cap.querySelector('.sci-label');"
        " return [a.textContent, lab ? lab.textContent : null]; })"
    )
    assert ("Figure S1", "Figure S1. ") in [tuple(p) for p in pairs]
    assert ("Table S1", "Table S1. ") in [tuple(p) for p in pairs]
    for xref, lab in pairs:
        if lab is not None:
            assert lab == xref + ". ", (xref, lab)


@pytest.mark.parametrize("size", [(1024, 768), (390, 844), (1920, 1080)])
def test_slide_stage_is_centered_and_fully_visible(browser, size):
    ctx = browser.new_context(viewport={"width": size[0], "height": size[1]})
    pg = ctx.new_page()
    pg.goto(EXAMPLE.as_uri() + "#slides/2")
    pg.wait_for_function("window.SciReport && SciReport.presenting")
    box = pg.evaluate("(() => { const r = document.querySelector('#deck .slide.is-current').getBoundingClientRect();"
                      " return {l: r.left, r: r.right, t: r.top, b: r.bottom}; })()")
    vp = pg.evaluate("({w: innerWidth, h: innerHeight - 44})")
    assert box["l"] >= -1 and box["r"] <= vp["w"] + 1 and box["t"] >= -1 and box["b"] <= vp["h"] + 1, (size, box)
    assert abs((box["l"] + box["r"]) / 2 - vp["w"] / 2) < 2
    ctx.close()


def test_no_slide_overflows_its_stage(page):
    assert page.evaluate("SciReport.checkLayout()") == []
    assert not state(page)["open"]  # checkLayout restores the closed state


def test_overflowing_slide_is_reported(page):
    page.evaluate("document.querySelectorAll('#deck .slide')[3].querySelector('.slide-body')"
                  ".insertAdjacentHTML('beforeend', '<p>' + 'word '.repeat(600) + '</p>')")
    assert page.evaluate("SciReport.checkLayout()") == [4]


def test_print_is_light_even_in_dark_mode(browser):
    ctx = browser.new_context(color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(EXAMPLE.as_uri())
    screen = pg.evaluate("getComputedStyle(document.body).color")
    pg.emulate_media(media="print")
    printed = pg.evaluate("getComputedStyle(document.body).color")
    assert screen != printed and printed in ("rgb(0, 0, 0)",), (screen, printed)
    ctx.close()


def test_wide_figures_break_out_of_the_text_column(browser):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.goto(EXAMPLE.as_uri())
    widths = pg.evaluate("({wide: document.querySelector('#fig-growth').getBoundingClientRect().width,"
                         " prose: document.querySelector('#result-growth > p').getBoundingClientRect().width})")
    assert widths["wide"] > widths["prose"] + 100, widths
    ctx.close()


def test_time_series_series_selection_and_names(browser, tmp_path):
    text = TEMPLATE.read_text(encoding="utf-8").replace("%%SLIDES%%", "")
    figure = (
        '<figure class="sci-figure" id="fig-t" data-sci-interactive="timeseries" '
        'data-series="low_retention_survival,high_retention_survival" '
        'data-series-names="Low retention,High retention">'
        '<script type="application/json" data-sci-data="t">'
        '{"columns":["time","low_retention_survival","high_retention_survival","difference"],'
        '"rows":[[0,1,1,0],[1,0.8,0.9,0.1],[2,0.5,0.8,0.3]]}</script>'
        '<div class="sci-media"></div><figcaption>Survival.</figcaption></figure>'
    )
    text = text.replace("%%RESULTS%%", figure)
    p = tmp_path / "t.html"
    p.write_text(text, encoding="utf-8")
    pg = browser.new_page()
    errors = []
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(p.as_uri())
    pg.wait_for_function("document.querySelector('#fig-t svg')")
    legend = pg.evaluate("[...document.querySelectorAll('#fig-t .sci-ts-legend span')].map(s => s.textContent).filter(Boolean)")
    assert legend == ["Low retention", "High retention"]
    assert pg.evaluate("document.querySelectorAll('#fig-t svg path[clip-path]').length") == 2
    labels = pg.evaluate("[...document.querySelectorAll('#fig-t svg text.ts-label')].map(t => +t.getAttribute('y'))")
    ends = sorted(labels[-2:])
    assert ends[1] - ends[0] >= 14  # end labels never overprint
    assert errors == []
    pg.close()
