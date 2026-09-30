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
    scitexlintr = pytest.importorskip("scitexlintr")
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
    assert '<td class="num">9.5</td>' in rows and '<td class="num">12.0</td>' in rows
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
    scitexlintr = pytest.importorskip("scitexlintr")
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
