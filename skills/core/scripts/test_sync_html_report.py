"""Tests for sync_html_report — inline registered figures and data into an HTML report."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sync_html_report as shr

SVG = """<?xml version="1.0" encoding="utf-8" standalone="no"?>
<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">
<svg xmlns:xlink="http://www.w3.org/1999/xlink" width="460.8pt" height="345.6pt" viewBox="0 0 460.8 345.6" xmlns="http://www.w3.org/2000/svg" version="1.1" onload="alert(1)">
 <!-- Created with matplotlib -->
 <defs><clipPath id="p1"><rect x="0" y="0" width="10" height="10"/></clipPath></defs>
 <g id="patch_1" clip-path="url(#p1)"><path d="M0 0L1 1"/></g>
 <use xlink:href="#patch_1" href="#patch_1"/>
 <script>alert(2)</script>
</svg>
"""

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def page(body: str) -> str:
    return f"<!doctype html>\n<html><head><title>t</title></head>\n<body>\n{body}\n</body></html>\n"


FIG = (
    '<figure class="sci-figure" id="fig-a" data-sci-fig="{fid}" data-alt="Alt text">\n'
    '  <div class="sci-media"><!-- sci-media --><!-- /sci-media --></div>\n'
    "  <figcaption>Caption.</figcaption>\n</figure>"
)
DATA = (
    '<figure data-sci-interactive="timeseries">'
    '<script type="application/json" data-sci-data="{did}">{{}}</script></figure>'
)


@pytest.fixture
def project(tmp_path: Path):
    """analysis/x/{outputs,reports} layout; paths in the manifest are report-relative."""
    reports = tmp_path / "reports"
    outputs = tmp_path / "outputs"
    reports.mkdir()
    outputs.mkdir()
    (outputs / "plot.svg").write_text(SVG, encoding="utf-8")
    (outputs / "img.png").write_bytes(PNG)
    series = {"x": [0, 1, 2], "series": [{"name": "A</script>", "values": [1, 2, 3]}]}
    (outputs / "series.json").write_text(json.dumps(series, indent=2), encoding="utf-8")
    (outputs / "table.csv").write_text("day,control,treated\n0,10,10\n1,12,9.5\n2,,8\n", encoding="utf-8")

    def make(body: str, figures=None, data=None):
        manifest = {"numbers": [], "figures": figures or [], "data": data or []}
        (reports / ".manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        report = reports / "report.html"
        report.write_text(page(body), encoding="utf-8")
        return report, reports / ".manifest.json"

    make.outputs = outputs
    make.reports = reports
    return make


def _payload(text: str, did: str) -> str:
    """The inlined payload of the data block ``did``."""
    return text.split(f'data-sci-data="{did}"')[1].split(">", 1)[1].split("</script>")[0]


def fig_entry(fid, rel, outputs, name):
    return {"id": fid, "path": rel, "sha256": sha((outputs / name).read_bytes())}


def run(report, manifest, *extra):
    return shr.main([str(report), f"--manifest={manifest}", *extra])


def test_inlines_svg_sets_sha_and_sanitizes(project):
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    report, manifest = project(FIG.format(fid="plot"), figures=[entry])
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    assert f'data-sha256="{entry["sha256"]}"' in out
    media = out.split("<!-- sci-media -->")[1].split("<!-- /sci-media -->")[0]
    assert media.lstrip().startswith("<svg")
    for gone in ("<?xml", "<!DOCTYPE", "<script", "onload", "Created with matplotlib", 'width="460.8pt"'):
        assert gone not in media
    assert 'viewBox="0 0 460.8 345.6"' in media
    assert 'role="img"' in media and 'aria-label="Alt text"' in media
    # ids are namespaced per figure and every reference follows them
    assert 'id="plot-p1"' in media and 'clip-path="url(#plot-p1)"' in media
    assert 'xlink:href="#plot-patch_1"' in media and 'href="#plot-patch_1"' in media
    assert 'id="p1"' not in media


def test_sync_is_idempotent_and_check_mode_detects_staleness(project):
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    report, manifest = project(FIG.format(fid="plot"), figures=[entry])
    before = report.read_bytes()
    assert run(report, manifest, "--check") == 1
    assert report.read_bytes() == before  # --check never writes
    assert run(report, manifest) == 0
    synced = report.read_bytes()
    assert run(report, manifest, "--check") == 0
    assert run(report, manifest) == 0
    assert report.read_bytes() == synced


def test_manifest_sha_mismatch_fails_without_writing(project, capsys):
    entry = {"id": "plot", "path": "../outputs/plot.svg", "sha256": "0" * 64}
    report, manifest = project(FIG.format(fid="plot"), figures=[entry])
    before = report.read_bytes()
    assert run(report, manifest) == 1
    assert report.read_bytes() == before
    assert "sha256" in capsys.readouterr().err


def test_missing_manifest_sha_is_an_error(project, capsys):
    report, manifest = project(FIG.format(fid="plot"), figures=[{"id": "plot", "path": "../outputs/plot.svg"}])
    assert run(report, manifest) == 1
    assert "no sha256" in capsys.readouterr().err


def test_failure_in_last_item_leaves_earlier_items_unwritten(project):
    good = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    bad = {"id": "gone", "path": "../outputs/missing.svg", "sha256": "1" * 64}
    body = FIG.format(fid="plot") + "\n" + FIG.format(fid="gone").replace("fig-a", "fig-b")
    report, manifest = project(body, figures=[good, bad])
    before = report.read_bytes()
    assert run(report, manifest) == 1
    assert report.read_bytes() == before


def test_unknown_figure_id_and_missing_markers_are_errors(project, capsys):
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    report, manifest = project(FIG.format(fid="nope"), figures=[entry])
    assert run(report, manifest) == 1
    assert "not in manifest" in capsys.readouterr().err
    no_markers = FIG.format(fid="plot").replace("<!-- sci-media --><!-- /sci-media -->", "")
    report, manifest = project(no_markers, figures=[entry])
    assert run(report, manifest) == 1
    assert "sci-media" in capsys.readouterr().err


def test_raster_becomes_data_uri_img(project):
    entry = fig_entry("img", "../outputs/img.png", project.outputs, "img.png")
    report, manifest = project(FIG.format(fid="img"), figures=[entry])
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    assert '<img alt="Alt text" src="data:image/png;base64,' in out


def test_json_data_is_minified_and_script_safe(project):
    raw = (project.outputs / "series.json").read_bytes()
    report, manifest = project(
        DATA.format(did="s"), data=[{"id": "s", "path": "../outputs/series.json", "sha256": sha(raw)}]
    )
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    assert f'data-sha256="{sha(raw)}"' in out
    payload = _payload(out, "s")
    assert "</script" not in payload.lower()
    assert json.loads(payload)["series"][0]["name"] == "A</script>"
    assert "\n" not in payload


def test_csv_data_becomes_columns_and_rows(project):
    raw = (project.outputs / "table.csv").read_bytes()
    report, manifest = project(
        DATA.format(did="t"), data=[{"id": "t", "path": "../outputs/table.csv", "sha256": sha(raw)}]
    )
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    payload = json.loads(_payload(out, "t"))
    assert payload == {"columns": ["day", "control", "treated"], "rows": [[0, 10, 10], [1, 12, 9.5], [2, None, 8]]}


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="pdftocairo not installed")
def test_pdf_is_converted_to_inline_svg(project):
    pdf_path = project.outputs / "plot.pdf"
    pdf_path.write_bytes(_minimal_pdf())
    entry = {"id": "pdf", "path": "../outputs/plot.pdf", "sha256": sha(pdf_path.read_bytes())}
    report, manifest = project(FIG.format(fid="pdf"), figures=[entry])
    assert run(report, manifest) == 0
    media = report.read_text(encoding="utf-8").split("<!-- sci-media -->")[1]
    assert media.lstrip().startswith("<svg")


def test_preserves_file_mode(project):
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    report, manifest = project(FIG.format(fid="plot"), figures=[entry])
    os.chmod(report, 0o640)
    assert run(report, manifest) == 0
    assert stat.S_IMODE(report.stat().st_mode) == 0o640


def test_root_option_overrides_path_base(project, tmp_path):
    entry = fig_entry("plot", "outputs/plot.svg", project.outputs, "plot.svg")
    report, manifest = project(FIG.format(fid="plot"), figures=[entry])
    assert run(report, manifest, f"--root={project.outputs.parent}") == 0


def test_synced_report_passes_scitexlintr_figure_and_data_rules(project):
    scitexlintr = pytest.importorskip("scitexlintr", minversion="0.2")
    fig = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    raw = (project.outputs / "series.json").read_bytes()
    data = {"id": "s", "path": "../outputs/series.json", "sha256": sha(raw)}
    report, manifest = project(FIG.format(fid="plot") + DATA.format(did="s"), figures=[fig], data=[data])
    assert run(report, manifest) == 0
    findings = scitexlintr.lint_file(report, manifest_path=manifest)
    assert [f for f in findings if f.rule in ("unfingerprinted-figure", "unfingerprinted-data")] == []


def _minimal_pdf() -> bytes:
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] /Contents 4 0 R >>",
    ]
    stream = b"0 0 1 rg 10 10 80 80 re f"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_markup_inside_html_comments_is_ignored(project):
    # Template guidance comments show example figure/data markup; syncing must
    # neither fail on their ids nor rewrite them.
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    # (HTML comments do not nest, so the example omits the sci-media markers.)
    example = FIG.format(fid="volcano_de").replace("<!-- sci-media --><!-- /sci-media -->", "")
    commented = "<!-- Example:\n" + example + "\n" + DATA.format(did="example") + "\n-->\n"
    report, manifest = project(commented + FIG.format(fid="plot"), figures=[entry])
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    assert commented in out
    assert 'data-sci-fig="volcano_de" data-alt="Alt text">' in out  # no sha stamped


@pytest.mark.skipif(shutil.which("pdftocairo") is None, reason="pdftocairo not installed")
def test_heavy_pdf_falls_back_to_raster(project, monkeypatch, capsys):
    # A dense scatter plot converted to SVG can be tens of megabytes; above the
    # budget the PDF is rasterized instead so the report stays loadable.
    monkeypatch.setattr(shr, "MAX_INLINE_SVG_BYTES", 10)
    pdf_path = project.outputs / "plot.pdf"
    pdf_path.write_bytes(_minimal_pdf())
    entry = {"id": "pdf", "path": "../outputs/plot.pdf", "sha256": sha(pdf_path.read_bytes())}
    report, manifest = project(FIG.format(fid="pdf"), figures=[entry])
    assert run(report, manifest) == 0
    media = report.read_text(encoding="utf-8").split("<!-- sci-media -->")[1]
    assert media.startswith('<img alt="Alt text" src="data:image/png;base64,')
    assert "rasterized" in capsys.readouterr().out


# -- content hashes, registered tables, reviewer copy ------------------------

def _between(text: str, name: str) -> str:
    return text.split(f"<!-- {name} -->")[1].split(f"<!-- /{name} -->")[0]


def test_content_hashes_cover_exactly_the_inlined_text(project):
    fig = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    raw = (project.outputs / "series.json").read_bytes()
    report, manifest = project(
        FIG.format(fid="plot") + DATA.format(did="s"),
        figures=[fig], data=[{"id": "s", "path": "../outputs/series.json", "sha256": sha(raw)}],
    )
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    media = _between(out, "sci-media")
    assert f'data-content-sha256="{sha(media.encode())}"' in out
    payload = out.split('data-sci-data="s"')[1].split(">", 1)[1].split("</script>")[0]
    assert f'data-content-sha256="{sha(payload.encode())}"' in out


TABLE = (
    '<table class="sci-table" data-sci-table="{did}"{attrs}><caption>Per-day counts.</caption>'
    "<thead><tr><th>Day</th><th>Control</th><th>Treated</th></tr></thead>"
    "<tbody><!-- sci-rows --><!-- /sci-rows --></tbody></table>"
)


def test_registered_table_rows_are_generated_from_csv(project):
    raw = (project.outputs / "table.csv").read_bytes()
    body = TABLE.format(did="t", attrs=' data-precision="1"')
    report, manifest = project(body, data=[{"id": "t", "path": "../outputs/table.csv", "sha256": sha(raw)}])
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    rows = _between(out, "sci-rows")
    assert rows.count("<tr>") == 3
    # data-precision rounds the fractional column; the integer column keeps its spelling.
    assert '<td class="num">9.5</td>' in rows and '<td class="num">12</td>' in rows
    assert '<td class="num">8.0</td>' in rows
    assert "<td></td>" in rows  # missing cell stays empty
    assert f'data-sha256="{sha(raw)}"' in out and f'data-content-sha256="{sha(rows.encode())}"' in out


def test_registered_table_column_selection_and_escaping(project):
    (project.outputs / "names.csv").write_text("name,score,note\nA&B,1,x\n<C>,2,y\n", encoding="utf-8")
    raw = (project.outputs / "names.csv").read_bytes()
    body = TABLE.format(did="n", attrs=' data-columns="name,score"')
    report, manifest = project(body, data=[{"id": "n", "path": "../outputs/names.csv", "sha256": sha(raw)}])
    assert run(report, manifest) == 0
    rows = _between(report.read_text(encoding="utf-8"), "sci-rows")
    assert "<td>A&amp;B</td>" in rows and "<td>&lt;C&gt;</td>" in rows
    assert "x" not in rows
    bad = TABLE.format(did="n", attrs=' data-columns="name,missing"')
    report, manifest = project(bad, data=[{"id": "n", "path": "../outputs/names.csv", "sha256": sha(raw)}])
    assert run(report, manifest) == 1


def test_synced_table_passes_scitexlintr(project):
    scitexlintr = pytest.importorskip("scitexlintr", minversion="0.2")
    raw = (project.outputs / "table.csv").read_bytes()
    report, manifest = project(TABLE.format(did="t", attrs=""), data=[{"id": "t", "path": "../outputs/table.csv", "sha256": sha(raw)}])
    assert run(report, manifest) == 0
    assert scitexlintr.lint_file(report, manifest_path=manifest) == []


def test_reviewer_copy_strips_media_and_keeps_line_numbers(project, tmp_path):
    entry = fig_entry("img", "../outputs/img.png", project.outputs, "img.png")
    fig = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    body = FIG.format(fid="img") + "\n" + FIG.format(fid="plot").replace("fig-a", "fig-b") + "\n<p>After.</p>"
    report, manifest = project(body, figures=[entry, fig])
    assert run(report, manifest) == 0
    copy = tmp_path / "review.html"
    before = report.read_bytes()
    assert run(report, manifest, f"--reviewer-copy={copy}") == 0
    assert report.read_bytes() == before
    text, full = copy.read_text(encoding="utf-8"), report.read_text(encoding="utf-8")
    assert "base64" not in text and "<path" not in text
    assert "figure img" in text and "figure plot" in text
    assert text.count("\n") == full.count("\n")
    assert text.splitlines().index("<p>After.</p>") == full.splitlines().index("<p>After.</p>")


# -- pre-release code review regressions ---------------------------------------

def test_non_finite_data_is_a_sync_error_not_a_traceback(project, capsys):
    (project.outputs / "bad.csv").write_text("t,v\n0,1\n1,inf\n", encoding="utf-8")
    (project.outputs / "bad.json").write_text('{"v": [1, NaN]}', encoding="utf-8")
    for name in ("bad.csv", "bad.json"):
        raw = (project.outputs / name).read_bytes()
        report, manifest = project(DATA.format(did="b"), data=[{"id": "b", "path": f"../outputs/{name}", "sha256": sha(raw)}])
        before = report.read_bytes()
        assert run(report, manifest) == 1
        assert report.read_bytes() == before
        assert "not finite" in capsys.readouterr().err


def test_attribute_lookup_does_not_match_hyphenated_suffixes():
    tag = '<svg stroke-width="2" data-height="9" width="400" height="300">'
    attrs = dict(shr.tag_attrs(tag)[1])
    assert attrs["width"] == "400" and attrs["height"] == "300" and attrs["stroke-width"] == "2"
    out = shr._svg_markup(tag + "</svg>", "f", "")
    assert 'viewBox="0 0 400 300"' in out and 'stroke-width="2"' in out


def test_table_rounding_handles_large_values_and_negative_zero():
    assert shr._format_cell("1e30", 2) == ("1000000000000000000000000000000.00", True)
    assert shr._format_cell("-0.001", 2) == ("0.00", True)
    assert shr._format_cell("nan", 2) == ("nan", False)


def test_csv_identifiers_keep_their_spelling_in_data_payloads(project):
    (project.outputs / "ids.csv").write_text("sample,value\n007,1.5\n1E5,2\n", encoding="utf-8")
    raw = (project.outputs / "ids.csv").read_bytes()
    report, manifest = project(DATA.format(did="i"), data=[{"id": "i", "path": "../outputs/ids.csv", "sha256": sha(raw)}])
    assert run(report, manifest) == 0
    payload = json.loads(_payload(report.read_text(encoding="utf-8"), "i"))
    assert payload["rows"] == [["007", 1.5], ["1E5", 2]]


# -- second review: parse HTML, type columns strictly, scope styles ----------

def test_quoted_gt_in_attributes_does_not_truncate_the_tag(project):
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    body = FIG.format(fid="plot").replace('data-alt="Alt text"', 'data-alt="p &gt; 0.05 in all arms" title="a > b"')
    report, manifest = project(body, figures=[entry])
    assert run(report, manifest) == 0
    out = report.read_text(encoding="utf-8")
    assert 'aria-label="p &gt; 0.05 in all arms"' in out
    assert 'title="a &gt; b"' in out and f'data-sha256="{entry["sha256"]}"' in out
    assert run(report, manifest, "--check") == 0  # rewritten tag is stable


def test_markup_inside_script_strings_is_not_a_live_region(project):
    entry = fig_entry("plot", "../outputs/plot.svg", project.outputs, "plot.svg")
    js = ("<script>const tpl = '<figure data-sci-fig=\"ghost\"><!-- sci-media --><!-- /sci-media --></figure>"
          "<table data-sci-table=\"ghost\"></table>';</script>")
    report, manifest = project(js + FIG.format(fid="plot"), figures=[entry])
    assert run(report, manifest) == 0
    assert js in report.read_text(encoding="utf-8")


def test_number_typing_is_strict_and_column_wide(project):
    (project.outputs / "labels.csv").write_text(
        "sample,replicate,value,year\n1_1,1,0.125,2024\n1_2,2,1.5,2025\n2_1,3,22.25,2026\n", encoding="utf-8")
    raw = (project.outputs / "labels.csv").read_bytes()
    entry = [{"id": "l", "path": "../outputs/labels.csv", "sha256": sha(raw)}]
    report, manifest = project(DATA.format(did="l"), data=entry)
    assert run(report, manifest) == 0
    payload = json.loads(_payload(report.read_text(encoding="utf-8"), "l"))
    assert [r[0] for r in payload["rows"]] == ["1_1", "1_2", "2_1"]
    table = TABLE.format(did="l", attrs=' data-precision="1"')
    report, manifest = project(table, data=entry)
    assert run(report, manifest) == 0
    rows = _between(report.read_text(encoding="utf-8"), "sci-rows")
    first = rows.splitlines()[0]
    # Labels and integer columns keep their spelling; only the fractional column is rounded.
    assert first == '<tr><td>1_1</td><td class="num">1</td><td class="num">0.1</td><td class="num">2024</td></tr>'


def test_inlined_svg_styles_are_scoped_to_their_figure():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><style>*{stroke-linecap:butt}'
           ".cls-1, .cls-2 > path{fill:red}</style><path class=\"cls-1\"/></svg>")
    out = shr._svg_markup(svg, "fig one", "")
    assert 'class="sci-svg-fig-one"' in out
    assert ".sci-svg-fig-one *{stroke-linecap:butt}" in out
    assert ".sci-svg-fig-one .cls-1, .sci-svg-fig-one .cls-2 > path{fill:red}" in out


def test_byte_order_marks_are_stripped_from_csv_and_json(project):
    (project.outputs / "bom.csv").write_bytes("﻿day,count\n0,1\n1,2\n".encode("utf-8"))
    raw = (project.outputs / "bom.csv").read_bytes()
    entry = [{"id": "b", "path": "../outputs/bom.csv", "sha256": sha(raw)}]
    report, manifest = project(TABLE.format(did="b", attrs=' data-columns="day,count"'), data=entry)
    assert run(report, manifest) == 0
    report, manifest = project(DATA.format(did="b"), data=entry)
    assert run(report, manifest) == 0
    assert json.loads(_payload(report.read_text(encoding="utf-8"), "b"))["columns"] == ["day", "count"]


def test_table_rounding_is_the_value_display_rounding():
    import render_report_values_tex as rrv
    for cell, p in [("0.125", 2), ("-0.001", 2), ("22.125", 1), ("1e30", 2)]:
        assert shr._format_cell(cell, p)[0] == rrv.round_half_up(cell, p)


def test_svg_root_with_quoted_gt_is_rewritten_safely():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" data-note="a > b" width="40" height="30"><path/></svg>'
    out = shr._svg_markup(svg, "f", "Alt")
    root = out[: out.index("<path")]
    assert 'data-note="a &gt; b"' in root and 'viewBox="0 0 40 30"' in root
    assert 'aria-label="Alt"' in root and "width=" not in root and "height=" not in root


# -- third review: sanitize by parsing, finish isolation, match the runtime ----

def test_svg_sanitizer_drops_handlers_and_script_urls_in_any_spelling():
    svg = ('<svg viewBox="0 0 1 1"><rect onclick=alert(1) ONLOAD="x()" width="1"/>'
           '<a href=" JavaScript:alert(1)"><text>x</text></a><a xlink:href="javascript:y()">y</a>'
           '<foreignObject><iframe src="https://x"></iframe></foreignObject>'
           '<script type="text/ecmascript">z()</script><a href="#ok">ok</a></svg>')
    out = shr._svg_markup(svg, "f", "")
    low = out.lower()
    for bad in ("onclick", "onload", "javascript:", "foreignobject", "<iframe", "<script", "z()"):
        assert bad not in low, bad
    assert '<rect width="1"/>' in out and 'href="#f-ok"' not in out  # no id "ok" to rename
    assert '<a href="#ok">' in out


def test_scoped_css_keeps_rules_that_target_the_svg_root():
    css = "svg .a{fill:red} svg{font:10px} svg>g text{x:1} .b svg .c{y:2}"
    assert shr.scope_css(css, ".S") == "svg.S .a{fill:red}svg.S{font:10px}svg.S>g text{x:1}.S .b svg .c{y:2}"


def test_cdata_wrapped_styles_are_scoped():
    svg = '<svg viewBox="0 0 1 1"><style><![CDATA[@media print{.a{fill:red}} .b{x:1}]]></style></svg>'
    out = shr._svg_markup(svg, "f", "")
    assert "@media print{.sci-svg-f .a{fill:red}}" in out and ".sci-svg-f .b{x:1}" in out
    assert "CDATA" not in out


def test_renamed_ids_follow_into_style_selectors_and_aria_references():
    svg = ('<svg viewBox="0 0 1 1"><style>#layer1 path{fill:red} .x{fill:url(#grad)}</style>'
           '<title id="t1">T</title><g id="layer1" aria-labelledby="t1 missing"><path/></g>'
           '<linearGradient id="grad"/></svg>')
    out = shr._svg_markup(svg, "f", "")
    assert "#f-layer1 path" in out and "url(#f-grad)" in out
    assert 'aria-labelledby="f-t1 missing"' in out


def test_percentage_sizes_do_not_become_a_viewbox():
    out = shr._svg_markup('<svg width="100%" height="100%"><path/></svg>', "f", "")
    root = out[: out.index("<path")]
    assert "viewBox" not in root and 'width="100%"' in root and 'height="100%"' in root
    out = shr._svg_markup('<svg width="640px" height="480px"><path/></svg>', "f", "")
    assert 'viewBox="0 0 640 480"' in out[: out.index("<path")]


def test_data_block_without_json_type_is_a_sync_error(project, capsys):
    raw = (project.outputs / "series.json").read_bytes()
    body = '<figure data-sci-interactive="timeseries"><script data-sci-data="s">{}</script></figure>'
    report, manifest = project(body, data=[{"id": "s", "path": "../outputs/series.json", "sha256": sha(raw)}])
    assert run(report, manifest) == 1
    assert "application/json" in capsys.readouterr().err


def test_unicode_digit_table_precision_is_a_sync_error(project, capsys):
    raw = (project.outputs / "table.csv").read_bytes()
    report, manifest = project(TABLE.format(did="t", attrs=' data-precision="²"'),
                               data=[{"id": "t", "path": "../outputs/table.csv", "sha256": sha(raw)}])
    assert run(report, manifest) == 1
    assert "data-precision" in capsys.readouterr().err
