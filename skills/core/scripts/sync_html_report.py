"""sync_html_report — inline registered figures and interactive data into an HTML report.

An HTML report is one self-contained file, so every analysis figure and every
data file behind an interactive figure is copied into it. This script does the
copying from the report manifest, in place, and stamps each copy with the
sha256 of the file it came from so ``scitexlintr`` can detect a stale copy:

* ``<figure data-sci-fig="ID">`` — the region between ``<!-- sci-media -->``
  and ``<!-- /sci-media -->`` is replaced with the figure named by
  ``figures[ID].path``: SVG inline (sanitized, ids namespaced per figure),
  PNG/JPEG/GIF/WebP as a base64 ``<img>``, PDF converted to SVG with
  ``pdftocairo``. The figure's ``data-sha256`` is set.
* ``<script type="application/json" data-sci-data="ID">`` — the content is
  replaced with ``data[ID].path``: JSON minified, CSV as
  ``{"columns": [...], "rows": [[...]]}``. ``data-sha256`` is set.
* ``<table data-sci-table="ID">`` — the region between ``<!-- sci-rows -->``
  and ``<!-- /sci-rows -->`` is replaced with one ``<tr>`` per row of the
  CSV/TSV ``data[ID].path`` (``data-columns="a,b"`` selects and orders
  columns; ``data-precision="2"`` rounds numeric cells half-up). The author
  writes the ``<caption>`` and ``<thead>``; sync owns the rows.

Every inlined region also gets ``data-content-sha256`` — the sha256 of the
exact text inlined — which scitexlintr recomputes to catch hand edits.

Every source file must hash to the manifest's recorded ``sha256``; a mismatch
means the figure or data was regenerated after the manifest was built, and
the manifest (Phase 1) must be refreshed first. All items are prepared and
checked before the report is written, so a failure leaves it byte-identical.

Values are not handled here: ``scitexlintr report.html --manifest=... --write``
refreshes the rendered text of ``data-sci-val`` spans.

Usage::

    python skills/core/scripts/sync_html_report.py \\
        analysis/<name>/reports/<name>-report.html \\
        --manifest analysis/<name>/reports/.manifest.json

Paths in the manifest resolve against the report's directory (the same base
``\\includegraphics`` uses for the TeX report); ``--root`` overrides it.
``--check`` writes nothing and exits 1 if the report is out of date.
``--reviewer-copy PATH`` writes nothing to the report; it saves a copy with
every inlined region replaced by a one-line placeholder (line numbers kept)
for the Phase 4–6 reviewers, who otherwise face megabyte-long lines.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

RASTER_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

# A PDF whose SVG conversion exceeds this is rasterized instead (dense scatter
# plots can expand from a few hundred kilobytes of PDF to tens of megabytes of
# SVG, which browsers render slowly). Source SVGs are inlined as registered.
MAX_INLINE_SVG_BYTES = 2 * 1024 * 1024
RASTER_DPI = 200

_FIGURE_OPEN_RE = re.compile(r"<figure\b[^>]*>", re.I)
_SCRIPT_RE = re.compile(r"(<script\b[^>]*>)(.*?)(</script\s*>)", re.I | re.S)
_MEDIA_RE = re.compile(r"(<!--\s*sci-media\s*-->)(.*?)(<!--\s*/sci-media\s*-->)", re.S)
_ROWS_RE = re.compile(r"(<!--\s*sci-rows\s*-->)(.*?)(<!--\s*/sci-rows\s*-->)", re.S)
_FIGURE_CLOSE_RE = re.compile(r"</figure\s*>", re.I)
_TABLE_OPEN_RE = re.compile(r"<table\b[^>]*>", re.I)
_TABLE_CLOSE_RE = re.compile(r"</table\s*>", re.I)


class SyncError(Exception):
    pass


@dataclass
class Edit:
    start: int
    end: int
    replacement: str


def _attr(tag: str, name: str) -> str | None:
    m = re.search(r"\b" + re.escape(name) + r"\s*=\s*(\"([^\"]*)\"|'([^']*)')", tag, re.I)
    if not m:
        return None
    return html.unescape(m.group(2) if m.group(2) is not None else m.group(3))


def _set_attr(tag: str, name: str, value: str) -> str:
    """Return ``tag`` with attribute ``name`` set to ``value`` (added before ``>`` if absent)."""
    pattern = re.compile(r"(\s" + re.escape(name) + r"\s*=\s*)(\"[^\"]*\"|'[^']*')", re.I)
    quoted = '"' + html.escape(value, quote=True) + '"'
    if pattern.search(tag):
        return pattern.sub(lambda m: m.group(1) + quoted, tag, count=1)
    close = -2 if tag.endswith("/>") else -1
    return tag[:close].rstrip() + f" {name}={quoted}" + tag[close:]


# ---------------------------------------------------------------------------
# Source files → inline markup
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _checked_source(kind: str, entry_id: str, entry: dict, base: Path) -> Path:
    rel = entry.get("path")
    if not rel:
        raise SyncError(f"{kind} {entry_id!r}: manifest entry has no path")
    path = (base / rel).resolve()
    if not path.is_file():
        raise SyncError(f"{kind} {entry_id!r}: file not found: {path}")
    expected = entry.get("sha256")
    if not expected or not re.fullmatch(r"[0-9a-fA-F]{64}", str(expected)):
        raise SyncError(
            f"{kind} {entry_id!r}: manifest entry has no sha256 — fingerprint it in "
            f"Phase 1 (sha256 of {rel})"
        )
    actual = _sha256(path)
    if actual.lower() != str(expected).lower():
        raise SyncError(
            f"{kind} {entry_id!r}: {rel} has sha256 {actual[:12]}… but the manifest "
            f"records {str(expected)[:12]}… — the file changed after the manifest was "
            "built; refresh the manifest (Phase 1) before syncing"
        )
    return path


def _svg_markup(svg_text: str, figure_id: str, alt: str) -> str:
    s = re.sub(r"<\?xml[^>]*\?>", "", svg_text)
    s = re.sub(r"<!DOCTYPE[^>]*>", "", s, flags=re.I)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"<script\b.*?</script\s*>", "", s, flags=re.I | re.S)
    s = re.sub(r"\s+on[a-z]+\s*=\s*(\"[^\"]*\"|'[^']*')", "", s, flags=re.I)
    m = re.search(r"<svg\b[^>]*>", s, re.I)
    if not m:
        raise SyncError(f"figure {figure_id!r}: SVG has no <svg> root element")
    root = m.group(0)
    viewbox = _attr(root, "viewBox")
    width, height = _attr(root, "width"), _attr(root, "height")
    if viewbox is None and width and height:
        w, h = (re.match(r"[\d.]+", v) for v in (width, height))
        if w and h:
            root = _set_attr(root, "viewBox", f"0 0 {w.group(0)} {h.group(0)}")
    root = re.sub(r"\s(width|height)\s*=\s*(\"[^\"]*\"|'[^']*')", "", root)
    root = _set_attr(root, "role", "img")
    root = _set_attr(root, "aria-label", alt)
    s = s[: m.start()] + root + s[m.end():]

    # Namespace every id so several inlined figures (and their copies in the
    # slides) cannot resolve each other's clip paths, gradients, or markers.
    prefix = re.sub(r"[^A-Za-z0-9_-]", "-", figure_id) + "-"
    ids = set(re.findall(r"\sid\s*=\s*\"([^\"]+)\"", s)) | set(re.findall(r"\sid\s*=\s*'([^']+)'", s))
    if ids:
        s = re.sub(
            r"(\sid\s*=\s*)([\"'])([^\"']+)\2",
            lambda mm: mm.group(1) + mm.group(2) + (prefix + mm.group(3) if mm.group(3) in ids else mm.group(3)) + mm.group(2),
            s,
        )
        s = re.sub(
            r"url\(\s*(['\"]?)#([^'\")\s]+)\1\s*\)",
            lambda mm: f"url(#{prefix + mm.group(2)})" if mm.group(2) in ids else mm.group(0),
            s,
        )
        s = re.sub(
            r"((?:xlink:)?href\s*=\s*)([\"'])#([^\"']+)\2",
            lambda mm: mm.group(1) + mm.group(2) + "#" + (prefix + mm.group(3) if mm.group(3) in ids else mm.group(3)) + mm.group(2),
            s,
        )
    return s.strip()


def _pdftocairo(path: Path, figure_id: str, mode: str) -> bytes:
    """Convert page 1 of a PDF with poppler: ``mode`` is ``"svg"`` or ``"png"``."""
    exe = shutil.which("pdftocairo")
    if exe is None:
        raise SyncError(
            f"figure {figure_id!r}: {path.name} is a PDF and pdftocairo (poppler) is not "
            "installed — install poppler or register an SVG/PNG export of the figure"
        )
    with tempfile.TemporaryDirectory() as tmp:
        if mode == "svg":
            out = Path(tmp) / "figure.svg"
            args = [exe, "-svg", "-f", "1", "-l", "1", str(path), str(out)]
        else:
            stem = Path(tmp) / "figure"
            out = stem.with_suffix(".png")
            args = [exe, "-png", "-singlefile", "-r", str(RASTER_DPI), "-f", "1", "-l", "1", str(path), str(stem)]
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0 or not out.is_file():
            raise SyncError(f"figure {figure_id!r}: pdftocairo failed: {proc.stderr.strip()}")
        return out.read_bytes()


def _img_markup(data: bytes, mime: str, alt: str) -> str:
    b64 = base64.b64encode(data).decode("ascii")
    return f'<img alt="{html.escape(alt, quote=True)}" src="data:{mime};base64,{b64}">'


def figure_markup(path: Path, figure_id: str, alt: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".svg":
        return _svg_markup(path.read_text(encoding="utf-8"), figure_id, alt)
    if suffix == ".pdf":
        svg_bytes = _pdftocairo(path, figure_id, "svg")
        if len(svg_bytes) <= MAX_INLINE_SVG_BYTES:
            return _svg_markup(svg_bytes.decode("utf-8"), figure_id, alt)
        print(
            f"figure {figure_id!r}: SVG conversion of {path.name} is "
            f"{len(svg_bytes) // 1024} KB; rasterized at {RASTER_DPI} dpi instead"
        )
        return _img_markup(_pdftocairo(path, figure_id, "png"), "image/png", alt)
    if suffix in RASTER_TYPES:
        return _img_markup(path.read_bytes(), RASTER_TYPES[suffix], alt)
    raise SyncError(
        f"figure {figure_id!r}: unsupported figure type {suffix!r} "
        "(use .svg, .pdf, .png, .jpg, .gif, or .webp)"
    )


def _coerce(cell: str):
    if cell == "":
        return None
    try:
        return int(cell)
    except ValueError:
        pass
    try:
        return float(cell)
    except ValueError:
        return cell


def data_payload(path: Path, data_id: str) -> str:
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix == ".json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SyncError(f"data {data_id!r}: invalid JSON in {path.name}: {exc}") from exc
    elif suffix in (".csv", ".tsv"):
        reader = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
        rows = [r for r in reader if r]
        if not rows:
            raise SyncError(f"data {data_id!r}: {path.name} is empty")
        obj = {"columns": rows[0], "rows": [[_coerce(c) for c in r] for r in rows[1:]]}
    else:
        raise SyncError(f"data {data_id!r}: unsupported data type {suffix!r} (use .json, .csv, or .tsv)")
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    # Keep the payload from closing the <script> element or opening a comment.
    return payload.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def _read_rows(path: Path, data_id: str) -> tuple[list[str], list[list[str]]]:
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".tsv"):
        raise SyncError(f"table {data_id!r}: registered tables are generated from .csv or .tsv, not {suffix!r}")
    reader = csv.reader(io.StringIO(path.read_text(encoding="utf-8")), delimiter="\t" if suffix == ".tsv" else ",")
    rows = [r for r in reader if r]
    if not rows:
        raise SyncError(f"table {data_id!r}: {path.name} is empty")
    return rows[0], rows[1:]


def _format_cell(cell: str, precision: int | None) -> tuple[str, bool]:
    """Return (text, is_numeric). Numeric cells keep their CSV spelling unless
    ``precision`` asks for half-up rounding."""
    try:
        d = Decimal(cell.strip())
    except InvalidOperation:
        return cell, False
    if not d.is_finite():
        return cell, False
    if precision is None:
        return cell.strip(), True
    return str(d.quantize(Decimal(1).scaleb(-precision), rounding=ROUND_HALF_UP)), True


def table_rows(path: Path, data_id: str, tag: str) -> str:
    header, rows = _read_rows(path, data_id)
    wanted = _attr(tag, "data-columns")
    if wanted:
        names = [c.strip() for c in wanted.split(",") if c.strip()]
        missing = [c for c in names if c not in header]
        if missing:
            raise SyncError(f"table {data_id!r}: data-columns names {missing} not in {path.name} (columns: {header})")
        idx = [header.index(c) for c in names]
    else:
        idx = list(range(len(header)))
    raw_precision = _attr(tag, "data-precision")
    if raw_precision is not None and not raw_precision.strip().isdigit():
        raise SyncError(f"table {data_id!r}: data-precision must be a non-negative integer")
    precision = int(raw_precision) if raw_precision is not None else None
    out = []
    for r in rows:
        cells = []
        for i in idx:
            cell = r[i] if i < len(r) else ""
            if cell == "":
                cells.append("<td></td>")
                continue
            text, numeric = _format_cell(cell, precision)
            cls = ' class="num"' if numeric else ""
            cells.append(f"<td{cls}>{html.escape(text, quote=False)}</td>")
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "\n".join(out)


def _content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _uncommented(source: str) -> str:
    """``source`` with HTML comments blanked to spaces (same length, newlines kept).

    Scanning happens on this view so example markup inside comments — the
    template's guidance shows figure and data snippets — is never treated as
    live. The ``sci-media`` markers are themselves comments, so they are
    located in the original source, within a live figure's span.
    """
    return re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), source, flags=re.S)


def plan(source: str, manifest: dict, base: Path) -> list[Edit]:
    live = _uncommented(source)
    figures = {f.get("id"): f for f in manifest.get("figures") or [] if isinstance(f, dict)}
    data = {d.get("id"): d for d in manifest.get("data") or [] if isinstance(d, dict)}
    edits: list[Edit] = []
    errors: list[str] = []

    for m in _FIGURE_OPEN_RE.finditer(live):
        tag = m.group(0)
        fid = _attr(tag, "data-sci-fig")
        if not fid:
            continue
        try:
            entry = figures.get(fid)
            if entry is None:
                raise SyncError(f"figure {fid!r}: data-sci-fig id not in manifest figures[*]")
            close = _FIGURE_CLOSE_RE.search(live, m.end())
            region_end = close.start() if close else len(source)
            media = _MEDIA_RE.search(source, m.end(), region_end)
            if media is None:
                raise SyncError(
                    f"figure {fid!r}: no <!-- sci-media --><!-- /sci-media --> markers inside the figure"
                )
            path = _checked_source("figure", fid, entry, base)
            markup = figure_markup(path, fid, _attr(tag, "data-alt") or "")
            new_tag = _set_attr(tag, "data-sha256", entry["sha256"].lower())
            new_tag = _set_attr(new_tag, "data-content-sha256", _content_sha(markup))
            edits.append(Edit(m.start(), m.end(), new_tag))
            edits.append(Edit(media.end(1), media.start(3), markup))
        except SyncError as exc:
            errors.append(str(exc))

    for m in _SCRIPT_RE.finditer(live):
        tag = m.group(1)
        did = _attr(tag, "data-sci-data")
        if not did:
            continue
        try:
            entry = data.get(did)
            if entry is None:
                raise SyncError(f"data {did!r}: data-sci-data id not in manifest data[*]")
            path = _checked_source("data", did, entry, base)
            payload = data_payload(path, did)
            new_tag = _set_attr(tag, "data-sha256", entry["sha256"].lower())
            new_tag = _set_attr(new_tag, "data-content-sha256", _content_sha(payload))
            edits.append(Edit(m.start(1), m.end(1), new_tag))
            edits.append(Edit(m.start(2), m.end(2), payload))
        except SyncError as exc:
            errors.append(str(exc))

    for m in _TABLE_OPEN_RE.finditer(live):
        tag = m.group(0)
        tid = _attr(tag, "data-sci-table")
        if not tid:
            continue
        try:
            entry = data.get(tid)
            if entry is None:
                raise SyncError(f"table {tid!r}: data-sci-table id not in manifest data[*]")
            close = _TABLE_CLOSE_RE.search(live, m.end())
            region_end = close.start() if close else len(source)
            rows = _ROWS_RE.search(source, m.end(), region_end)
            if rows is None:
                raise SyncError(f"table {tid!r}: no <!-- sci-rows --><!-- /sci-rows --> markers inside the table")
            path = _checked_source("table", tid, entry, base)
            body = table_rows(path, tid, tag)
            new_tag = _set_attr(tag, "data-sha256", entry["sha256"].lower())
            new_tag = _set_attr(new_tag, "data-content-sha256", _content_sha(body))
            edits.append(Edit(m.start(), m.end(), new_tag))
            edits.append(Edit(rows.end(1), rows.start(3), body))
        except SyncError as exc:
            errors.append(str(exc))

    if errors:
        raise SyncError("\n".join(errors))
    return edits


def apply(source: str, edits: list[Edit]) -> str:
    out = source
    for e in sorted(edits, key=lambda e: e.start, reverse=True):
        out = out[: e.start] + e.replacement + out[e.end:]
    return out


def reviewer_view(source: str) -> str:
    """``source`` with every inlined region replaced by a placeholder that
    keeps the region's newline count, so line numbers still match the report."""
    live = _uncommented(source)
    edits: list[Edit] = []

    def stub(label: str, start: int, end: int) -> None:
        region = source[start:end]
        edits.append(Edit(start, end, f"[{label}: {len(region)} characters omitted for review]" + "\n" * region.count("\n")))

    for m in _FIGURE_OPEN_RE.finditer(live):
        fid = _attr(m.group(0), "data-sci-fig")
        close = _FIGURE_CLOSE_RE.search(live, m.end())
        media = fid and _MEDIA_RE.search(source, m.end(), close.start() if close else len(source))
        if media:
            stub(f"figure {fid}", media.end(1), media.start(3))
    for m in _SCRIPT_RE.finditer(live):
        did = _attr(m.group(1), "data-sci-data")
        if did:
            stub(f"data {did}", m.start(2), m.end(2))
    for m in _TABLE_OPEN_RE.finditer(live):
        tid = _attr(m.group(0), "data-sci-table")
        close = _TABLE_CLOSE_RE.search(live, m.end())
        rows = tid and _ROWS_RE.search(source, m.end(), close.start() if close else len(source))
        if rows:
            stub(f"table {tid} rows", rows.end(1), rows.start(3))
    return apply(source, edits)


def _atomic_write(path: Path, text: str) -> None:
    mode = path.stat().st_mode
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, mode & 0o7777)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sync_html_report",
        description="Inline manifest-registered figures and data into an HTML report",
    )
    parser.add_argument("report", help="path to the report .html file")
    parser.add_argument("--manifest", required=True, help="path to .manifest.json")
    parser.add_argument("--root", default=None, help="base directory for manifest paths (default: the report's directory)")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 if the report is out of date")
    parser.add_argument("--reviewer-copy", default=None, metavar="PATH",
                        help="write a copy with inlined media/data/rows stubbed out (line numbers kept); the report is not modified")
    args = parser.parse_args(argv)

    report = Path(args.report)
    manifest_path = Path(args.manifest)
    for p in (report, manifest_path):
        if not p.is_file():
            print(f"not found: {p}", file=sys.stderr)
            return 2
    if report.is_symlink():
        print(f"refusing to rewrite a symlinked report: {report}", file=sys.stderr)
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{manifest_path}: invalid JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(manifest, dict):
        print(f"{manifest_path}: manifest must be a JSON object", file=sys.stderr)
        return 2

    source = report.read_text(encoding="utf-8")
    if args.reviewer_copy:
        out = Path(args.reviewer_copy)
        if out.resolve() == report.resolve():
            print("--reviewer-copy must not be the report itself", file=sys.stderr)
            return 2
        out.write_text(reviewer_view(source), encoding="utf-8")
        print(f"wrote reviewer copy {out}")
        return 0
    base = Path(args.root) if args.root else report.parent
    try:
        edits = plan(source, manifest, base)
    except SyncError as exc:
        print(f"{report}: sync failed; nothing written\n{exc}", file=sys.stderr)
        return 1
    new_source = apply(source, edits)
    n_items = len(edits) // 2
    if new_source == source:
        print(f"{report}: up to date ({n_items} figure/data/table item(s))")
        return 0
    if args.check:
        print(f"{report}: out of date — run sync_html_report.py without --check", file=sys.stderr)
        return 1
    _atomic_write(report, new_source)
    print(f"{report}: synced {n_items} figure/data/table item(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
