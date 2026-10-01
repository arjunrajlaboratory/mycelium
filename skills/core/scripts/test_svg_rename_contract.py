"""One contract for SVG id renaming, run through both implementations.

sync_html_report.py namespaces ids when it inlines a figure (Python); the
report runtime isolates ids again when it copies a figure into a slide
(JavaScript). Each case below must satisfy the same invariants in both:
every id reference in the output resolves to an id in the output, and
colors written as #hex are never touched. A fix to one implementation
that is not made in the other fails here.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_html_report as shr

CASES = {
    "hex_color_in_style_attribute": '<svg viewBox="0 0 1 1"><g id="fff" style="fill:#fff"/><use href="#fff"/></svg>',
    "hex_color_in_stylesheet": '<svg viewBox="0 0 1 1"><style>.x{fill:#fff} #fff{stroke:red}</style><g id="fff" class="x"/></svg>',
    "hex_color_presentation_attribute": '<svg viewBox="0 0 1 1"><g id="fff" fill="#fff"/></svg>',
    "mixed_case_url": '<svg viewBox="0 0 1 1"><linearGradient id="g"/><rect fill="URL(#g)" style="stroke:Url(#g)"/></svg>',
    "aria_references": ('<svg viewBox="0 0 1 1"><title id="t">T</title><desc id="d">D</desc>'
                        '<g id="a" aria-labelledby="t" aria-describedby="d" aria-details="d" '
                        'aria-errormessage="d" aria-activedescendant="t"/></svg>'),
}

REF_ATTRS = ("aria-labelledby", "aria-describedby", "aria-details", "aria-errormessage",
             "aria-activedescendant", "aria-controls", "aria-owns", "aria-flowto", "for", "headers", "list")


def colors(markup: str) -> list[str]:
    """Hex colors where colors live: inside CSS declaration blocks and in
    attribute values other than id/href (never a #id selector)."""
    found = []
    for block in re.findall(r"\{([^{}]*)\}", markup):
        found += re.findall(r"#[0-9a-fA-F]{3,8}(?![\w-])", block)
    for name, value in re.findall(r'\s([\w:-]+)="([^"]*)"', markup):
        if name not in ("id", "href", "xlink:href"):
            found += re.findall(r"#[0-9a-fA-F]{3,8}(?![\w-])", value)
    return sorted(found)


def invariants(markup: str, original: str) -> list[str]:
    problems = []
    ids = set(re.findall(r'\sid="([^"]+)"', markup))
    refs = re.findall(r"url\(\s*['\"]?#([^'\")\s]+)", markup, re.I)
    refs += re.findall(r'(?:xlink:)?href="#([^"]+)"', markup)
    for attr in REF_ATTRS:
        for value in re.findall(rf'\s{attr}="([^"]*)"', markup):
            refs += [v for v in value.split() if v != "missing"]
    problems += [f"dangling reference #{r}" for r in refs if r not in ids]
    if colors(original) != colors(markup):
        problems.append(f"colors changed: {colors(original)} -> {colors(markup)}")
    return problems


@pytest.mark.parametrize("case", sorted(CASES))
def test_sync_namespacing_meets_the_contract(case):
    out = shr._svg_markup(CASES[case], "f", "")
    assert invariants(out, CASES[case]) == [], out


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        b = None
        for kwargs in ({}, {"channel": "chrome"}):
            try:
                b = p.chromium.launch(**kwargs)
                break
            except Exception:  # noqa: BLE001
                continue
        if b is None:
            pytest.skip("no Chromium available")
        yield b
        b.close()


@pytest.mark.parametrize("case", sorted(CASES))
def test_runtime_slide_copy_meets_the_contract(browser, tmp_path, case):
    template = (Path(__file__).resolve().parents[3]
                / "network/conventions/report-generator/assets/report-template.html").read_text(encoding="utf-8")
    fig = (f'<section id="r"><h3>The diagram copies cleanly.</h3><figure class="sci-figure" id="fig-d" data-sci-diagram>'
           f'<div class="sci-media">{CASES[case]}</div><figcaption>D.</figcaption></figure></section>')
    slide = ('<section class="slide slide--figure" data-source="#r"><h2>The diagram copies cleanly.</h2>'
             '<div class="slide-body"><div class="slide-figure" data-fig-ref="fig-d"></div></div></section>')
    page_path = tmp_path / "p.html"
    page_path.write_text(template.replace("%%RESULTS%%", fig).replace("%%SLIDES%%", slide), encoding="utf-8")
    pg = browser.new_page()
    pg.goto(page_path.as_uri())
    pg.wait_for_function("document.querySelector('#deck .slide-figure svg')")
    copy = pg.evaluate("document.querySelector('#deck .slide-figure svg').outerHTML")
    pg.close()
    assert invariants(copy, CASES[case]) == [], copy
