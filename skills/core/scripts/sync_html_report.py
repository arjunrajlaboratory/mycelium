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
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_report_values_tex import round_half_up, round_significant  # noqa: E402  (one rounding rule)

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

_SCOPE_RE = re.compile(r"[^A-Za-z0-9_-]")


class SyncError(Exception):
    pass


@dataclass
class Edit:
    start: int
    end: int
    replacement: str


# Attribute tokenization for one raw opening tag, quote-aware (a ">" inside a
# quoted value does not end anything) and case-preserving (SVG's viewBox).
_ATTR_TOKEN_RE = re.compile(r"""([^\s"'>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'=<>`]+))?""")


def tag_attrs(raw_tag: str) -> tuple[str, list[tuple[str, str | None]]]:
    """(tag name, [(attribute, value)]) of a raw opening tag such as
    ``get_starttag_text()`` returns. Values are entity-decoded."""
    m = re.match(r"<\s*([^\s/>]+)", raw_tag)
    if not m:
        raise SyncError(f"not an opening tag: {raw_tag[:40]!r}")
    body = raw_tag[m.end():].rstrip(">").rstrip("/")
    attrs = []
    for a in _ATTR_TOKEN_RE.finditer(body):
        value = a.group(2)
        if value is not None and value[:1] in "\"'":
            value = value[1:-1]
        attrs.append((a.group(1), None if value is None else html.unescape(value)))
    return m.group(1), attrs


def _build_tag(tagname: str, attrs: list[tuple[str, str | None]], updates: dict[str, str | None],
               drop: tuple[str, ...] = (), self_closing: bool = False) -> str:
    """An opening tag rebuilt from parsed attributes, with ``updates`` set
    (replacing or appending) and ``drop`` removed. Values are re-escaped, so
    the result is valid whatever the original quoting was, and rebuilding is
    idempotent."""
    updates = dict(updates)
    merged = [(k, updates.pop(k) if k in updates else v) for k, v in attrs if k.lower() not in drop]
    merged += list(updates.items())
    parts = [tagname] + [k if v is None else f'{k}="{html.escape(v, quote=True)}"' for k, v in merged]
    return "<" + " ".join(parts) + ("/>" if self_closing else ">")


_UNSAFE_URL_RE = re.compile(r"^(?:javascript|vbscript|data:text/html)", re.I)
_URL_ATTRS = {"href", "xlink:href", "src", "action", "formaction"}
_DROPPED_ELEMENTS = {"script", "foreignobject"}


class _SvgSanitizer(HTMLParser):
    """Finds every tag that must change in an SVG: elements that can run code
    or embed a document (``<script>``, ``<foreignObject>``) are removed; event
    handlers (``on*``, any quoting or case) and ``javascript:`` URLs are
    dropped. Works from parsed tags, so unquoted or oddly spaced attributes
    cannot slip past a pattern."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.edits: list[Edit] = []
        self.dropping: list[tuple[str, int]] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _start(self, tag: str, self_closing: bool) -> None:
        start = self._offset()
        raw = self.get_starttag_text() or ""
        if tag in _DROPPED_ELEMENTS:
            if self_closing or raw.rstrip().endswith("/>"):
                self.edits.append(Edit(start, start + len(raw), ""))
            else:
                self.dropping.append((tag, start))
            return
        name, attrs = tag_attrs(raw)
        kept = [(k, v) for k, v in attrs
                if not k.lower().startswith("on")
                and not (k.lower() in _URL_ATTRS and v and _UNSAFE_URL_RE.match(re.sub(r"[\s\x00-\x1f]", "", v)))]
        if len(kept) != len(attrs):
            self.edits.append(Edit(start, start + len(raw),
                                   _build_tag(name, kept, {}, self_closing=raw.rstrip().endswith("/>"))))

    def handle_starttag(self, tag, attrs):
        self._start(tag, False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag, True)

    def handle_endtag(self, tag):
        if self.dropping and self.dropping[-1][0] == tag:
            _, start = self.dropping.pop()
            close = self._offset()
            self.edits.append(Edit(start, self.source.find(">", close) + 1, ""))


class _SvgRewriter(HTMLParser):
    """One parsed pass over an SVG that rewrites start tags (``tag_fn`` gets
    the raw name and attributes and returns new attributes, or None to keep
    the tag byte-for-byte) and ``<style>`` contents (``style_fn``)."""

    def __init__(self, source: str, tag_fn, style_fn) -> None:
        super().__init__(convert_charrefs=True)
        self.source, self.tag_fn, self.style_fn = source, tag_fn, style_fn
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.edits: list[Edit] = []
        self.style_open: int | None = None

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def _start(self, tag: str) -> None:
        start = self._offset()
        raw = self.get_starttag_text() or ""
        name, attrs = tag_attrs(raw)
        new = self.tag_fn(name, attrs) if self.tag_fn else None
        if new is not None:
            self.edits.append(Edit(start, start + len(raw), _build_tag(name, new, {}, self_closing=raw.rstrip().endswith("/>"))))
        if tag == "style" and not raw.rstrip().endswith("/>"):
            self.style_open = start + len(raw)

    def handle_starttag(self, tag, attrs):
        self._start(tag)

    def handle_startendtag(self, tag, attrs):
        self._start(tag)

    def handle_endtag(self, tag):
        if tag == "style" and self.style_open is not None and self.style_fn:
            close = self._offset()
            self.edits.append(Edit(self.style_open, close, self.style_fn(self.source[self.style_open:close])))
            self.style_open = None


def rewrite_svg(svg: str, tag_fn=None, style_fn=None) -> str:
    parser = _SvgRewriter(svg, tag_fn, style_fn)
    parser.feed(svg)
    parser.close()
    out = svg
    for e in sorted(parser.edits, key=lambda e: e.start, reverse=True):
        out = out[: e.start] + e.replacement + out[e.end:]
    return out


def _collect_ids(svg: str) -> set[str]:
    found: set[str] = set()

    def grab(name, attrs):
        found.update(v for k, v in attrs if k.lower() == "id" and v)
        return None

    rewrite_svg(svg, tag_fn=grab)
    return found


_ID_LIST_ATTRS = {"aria-labelledby", "aria-describedby", "aria-controls", "aria-owns", "aria-flowto"}


def _rename_css_refs(css: str, rename: dict[str, str]) -> str:
    """``#id`` selectors and ``url(#id)`` references in CSS text."""
    if not rename:
        return css
    css = re.sub(r"url\(\s*(['\"]?)#([^'\")\s]+)\1\s*\)",
                 lambda m: f"url(#{rename[m.group(2)]})" if m.group(2) in rename else m.group(0), css)
    return re.sub(r"(?<![\w-])#(-?[A-Za-z_][\w-]*)",
                  lambda m: "#" + rename[m.group(1)] if m.group(1) in rename else m.group(0), css)


def _rename_refs(attrs: list[tuple[str, str | None]], rename: dict[str, str]):
    """New attribute list with ids and every reference to them renamed, or None if unchanged."""
    if not rename:
        return None
    out, changed = [], False
    for k, v in attrs:
        key, new = k.lower(), v
        if v is not None:
            if key == "id" and v in rename:
                new = rename[v]
            elif key in ("href", "xlink:href") and v.startswith("#") and v[1:] in rename:
                new = "#" + rename[v[1:]]
            elif key in _ID_LIST_ATTRS:
                new = " ".join(rename.get(t, t) for t in v.split())
            elif "url(" in v:
                new = _rename_css_refs(v, rename) if key == "style" else re.sub(
                    r"url\(\s*(['\"]?)#([^'\")\s]+)\1\s*\)",
                    lambda m: f"url(#{rename[m.group(2)]})" if m.group(2) in rename else m.group(0), v)
            elif key == "style":
                new = _rename_css_refs(v, rename)
        changed = changed or new != v
        out.append((k, new))
    return out if changed else None


def sanitize_svg(svg: str) -> str:
    parser = _SvgSanitizer(svg)
    parser.feed(svg)
    parser.close()
    for _, start in parser.dropping:  # unclosed: drop to the end
        parser.edits.append(Edit(start, len(svg), ""))
    # Apply outermost removals; skip edits nested inside a removed range.
    edits = sorted(parser.edits, key=lambda e: (e.start, -e.end))
    kept, last_end = [], -1
    for e in edits:
        if e.start < last_end:
            continue
        kept.append(e)
        last_end = max(last_end, e.end)
    out = svg
    for e in reversed(kept):
        out = out[: e.start] + e.replacement + out[e.end:]
    return out


class _FirstTag(HTMLParser):
    """Finds the first start tag named ``name`` and its raw source extent."""

    def __init__(self, source: str, name: str) -> None:
        super().__init__(convert_charrefs=True)
        self.name, self.found = name, None
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]

    def handle_starttag(self, tag, attrs):
        if self.found is None and tag == self.name:
            line, col = self.getpos()
            start = self.line_starts[line - 1] + col
            self.found = (start, start + len(self.get_starttag_text() or ""))

    handle_startendtag = handle_starttag


def first_tag(source: str, name: str) -> tuple[int, int] | None:
    parser = _FirstTag(source, name)
    parser.feed(source)
    parser.close()
    return parser.found


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
    s = sanitize_svg(s)
    extent = first_tag(s, "svg")
    if extent is None:
        raise SyncError(f"figure {figure_id!r}: SVG has no <svg> root element")
    name, attrs = tag_attrs(s[extent[0]:extent[1]])
    values = {k.lower(): v for k, v in attrs}
    updates: dict[str, str | None] = {"role": "img", "aria-label": alt}
    has_viewbox = values.get("viewbox") is not None
    if not has_viewbox and values.get("width") and values.get("height"):
        # Only absolute user-unit sizes make a viewBox; "100%" does not.
        w, h = (re.fullmatch(r"\s*([\d.]+)\s*(?:px|pt)?\s*", values[k]) for k in ("width", "height"))
        if w and h:
            updates["viewBox"] = f"0 0 {w.group(1)} {h.group(1)}"
            has_viewbox = True
    # An inlined <style> applies to the whole page, so scope its rules to this
    # figure: matplotlib's "*{stroke-linecap:butt}" must not restyle the deck
    # icons, and two exports that both define .cls-1 must not recolor each other.
    scope = "sci-svg-" + _SCOPE_RE.sub("-", figure_id)
    if re.search(r"<style\b", s, re.I):
        existing = values.get("class")
        updates["class"] = f"{existing} {scope}".strip() if existing else scope
    # Width and height go only when a viewBox carries the geometry (CSS then sizes the figure).
    root = _build_tag(name, attrs, updates, drop=("width", "height") if has_viewbox else ())
    s = s[: extent[0]] + root + s[extent[1]:]
    scope_styles = "class" in updates

    # Namespace every id so several inlined figures (and their copies in the
    # slides) cannot resolve each other's clip paths, gradients, markers, or
    # styles; every reference follows the rename.
    prefix = _SCOPE_RE.sub("-", figure_id) + "-"
    ids = _collect_ids(s)
    rename = {i: prefix + i for i in ids}
    s = rewrite_svg(
        s,
        tag_fn=lambda name, attrs: _rename_refs(attrs, rename),
        style_fn=lambda css: scope_css(_rename_css_refs(_strip_cdata(css), rename), "." + scope) if scope_styles else _rename_css_refs(css, rename),
    )
    return s.strip()


def _strip_cdata(css: str) -> str:
    return re.sub(r"<!\[CDATA\[|\]\]>", "", css)


def _scope_selector(sel: str, scope: str) -> str:
    """``scope`` is a class on the <svg> root, so a selector that starts at
    the root (``svg``, ``svg .a``, ``svg>g``) gets the class on that compound
    (``svg.S .a``); every other selector becomes a descendant of the root."""
    if sel == ":root":
        return "svg" + scope
    m = re.match(r"svg(?![\w-])", sel)
    if m:
        return "svg" + scope + sel[3:]
    return f"{scope} {sel}"


def _split_selectors(selector: str) -> list[str]:
    """Split a selector list on top-level commas (not those inside ``:is(a, b)``)."""
    parts, depth, cur = [], 0, []
    for ch in selector:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def scope_css(css: str, scope: str) -> str:
    """Prefix every selector in ``css`` with ``scope`` (a class selector).
    ``@media`` / ``@supports`` blocks are scoped recursively; other at-rules
    (``@font-face``, ``@keyframes``, ``@import``) pass through unchanged."""
    out, i, n = [], 0, len(css)
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    n = len(css)
    while i < n:
        brace = css.find("{", i)
        if brace < 0:
            out.append(css[i:])
            break
        head = css[i:brace].strip()
        depth, j = 1, brace + 1
        while j < n and depth:
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            j += 1
        body = css[brace + 1:j - 1]
        if head.startswith("@"):
            inner = scope_css(body, scope) if re.match(r"@(media|supports)\b", head, re.I) else body
            out.append(f"{head}{{{inner}}}")
        else:
            prefixed = ", ".join(_scope_selector(sel, scope) for sel in _split_selectors(head))
            out.append(f"{prefixed}{{{body}}}")
        i = j
    return "".join(out)


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


# One strict notion of "a number in a data file": digits with an optional
# sign, decimal point, and exponent. Python's int()/float()/Decimal() also
# accept "1_1", " 7 ", "infinity" — spellings that in a CSV are labels.
_NUMBER_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_INTEGER_RE = re.compile(r"[+-]?\d+")
_LEADING_ZERO_RE = re.compile(r"[+-]?0\d")
_NON_FINITE = {"nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "+infinity", "-infinity"}


def _is_number(cell: str) -> bool:
    return bool(_NUMBER_RE.fullmatch(cell)) and not _LEADING_ZERO_RE.match(cell)


def _column_types(header: list[str], rows: list[list[str]], data_id: str, name: str) -> list[str]:
    """Type every column as a whole: "int", "float", or "text".

    A column is numeric only if every non-empty cell is a strict number (so
    "007" or "1_1" make the whole column text and labels keep their
    spelling). A numeric column holding NaN or infinity is an error: JSON
    cannot carry it and a chart would plot it silently."""
    types = []
    for j in range(len(header)):
        cells = [r[j] for r in rows if j < len(r) and r[j] != ""]
        numeric = [c for c in cells if _is_number(c)]
        odd = [c for c in cells if not _is_number(c)]
        if cells and odd and all(c.lower() in _NON_FINITE for c in odd) and numeric:
            raise SyncError(
                f"data {data_id!r}: column {header[j]!r} of {name} holds a value that is not finite "
                f"({odd[0]!r}); write missing values as empty cells"
            )
        if not cells or odd:
            types.append("text")
        else:
            types.append("int" if all(_INTEGER_RE.fullmatch(c) for c in cells) else "float")
    return types


def _read_rows(path: Path, data_id: str) -> tuple[list[str], list[list[str]]]:
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".tsv"):
        raise SyncError(f"table {data_id!r}: registered tables are generated from .csv or .tsv, not {suffix!r}")
    text = path.read_text(encoding="utf-8-sig")  # tolerate the BOM Excel writes
    reader = csv.reader(io.StringIO(text), delimiter="\t" if suffix == ".tsv" else ",")
    rows = [r for r in reader if r]
    if not rows:
        raise SyncError(f"data {data_id!r}: {path.name} is empty")
    return rows[0], rows[1:]


def data_payload(path: Path, data_id: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            obj = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise SyncError(f"data {data_id!r}: invalid JSON in {path.name}: {exc}") from exc
    elif suffix in (".csv", ".tsv"):
        header, body = _read_rows(path, data_id)
        types = _column_types(header, body, data_id, path.name)

        def cell(c: str, t: str):
            if c == "":
                return None
            return int(c) if t == "int" else float(c) if t == "float" else c

        obj = {"columns": header, "rows": [
            [cell(c, types[j] if j < len(types) else "text") for j, c in enumerate(r)] for r in body
        ]}
    else:
        raise SyncError(f"data {data_id!r}: unsupported data type {suffix!r} (use .json, .csv, or .tsv)")
    try:
        payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise SyncError(
            f"data {data_id!r}: {path.name} contains a value that is not finite (NaN or "
            "infinity), which JSON cannot carry; write missing values as empty cells or null"
        ) from exc
    # Keep the payload from closing the <script> element or opening a comment.
    return payload.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def _format_cell(cell: str, precision: int | None, sig: int | None = None) -> tuple[str, bool]:
    """(text, is_numeric) for one cell of a numeric column. ``precision``
    (decimal places) or ``sig`` (significant figures) rounds with the
    value-display rule (render_report_values_tex)."""
    if not _is_number(cell):
        return cell, False
    if sig is not None:
        return round_significant(cell, sig), True
    if precision is None:
        return cell, True
    return round_half_up(cell, precision), True


def _int_attr(attrs: dict, name: str, item: str, minimum: int = 0) -> int | None:
    raw = attrs.get(name)
    if raw is None:
        return None
    if not re.fullmatch(r"[0-9]+", raw.strip()) or int(raw) < minimum:  # ASCII only
        raise SyncError(f"table {item!r}: {name} must be an integer of at least {minimum}")
    return int(raw)


def _rounding(attrs: dict, item: str) -> tuple[int | None, int | None]:
    precision = _int_attr(attrs, "data-precision", item)
    sig = _int_attr(attrs, "data-sig", item, minimum=1)
    if precision is not None and sig is not None:
        raise SyncError(f"table {item!r}: use data-precision (decimal places) or data-sig (significant figures), not both")
    return precision, sig


def _render_rows(header: list[str], rows: list[list[str]], types: list[str], idx: list[int],
                 precision: int | None, sig: int | None) -> str:
    out = []
    for r in rows:
        cells = []
        for i in idx:
            cell = r[i] if i < len(r) else ""
            if cell == "":
                cells.append("<td></td>")
            elif types[i] == "text":
                cells.append(f"<td>{html.escape(cell, quote=False)}</td>")
            else:
                # Rounding applies to fractional columns only: integer columns
                # (counts, replicate numbers, years) keep their spelling.
                fractional = types[i] == "float"
                text, _ = _format_cell(cell, precision if fractional else None, sig if fractional else None)
                cells.append(f'<td class="num">{html.escape(text, quote=False)}</td>')
        out.append("<tr>" + "".join(cells) + "</tr>")
    return "\n".join(out)


def _select_columns(header: list[str], attrs: dict, item: str, where: str) -> list[int]:
    wanted = attrs.get("data-columns")
    if not wanted:
        return list(range(len(header)))
    names = [c.strip() for c in wanted.split(",") if c.strip()]
    missing = [c for c in names if c not in header]
    if missing:
        raise SyncError(f"table {item!r}: data-columns names {missing} not in {where} (columns: {header})")
    return [header.index(c) for c in names]


def worked_rows_sha256(rows) -> str:
    """Mirror of scitexlintr's ``worked_rows_sha256``: canonical JSON of the rows."""
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def worked_rows(entry: dict, item: str, attrs: dict) -> str:
    """Rows of a ``data-sci-worked`` table, rendered from the manifest's
    ``worked_examples[item].rows`` (a list of objects, one per row)."""
    rows = entry.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise SyncError(f"worked example {item!r}: rows must be a list of objects")
    header: list[str] = []
    for r in rows:
        header += [k for k in r if k not in header]

    def spell(v) -> str:
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        return repr(v) if isinstance(v, float) else str(v)

    cells = [[spell(r.get(k)) for k in header] for r in rows]
    types = _column_types(header, cells, item, "worked_examples")
    precision, sig = _rounding(attrs, item)
    return _render_rows(header, cells, types, _select_columns(header, attrs, item, "worked_examples"), precision, sig)


def table_rows(path: Path, data_id: str, attrs: dict[str, str | None]) -> str:
    header, rows = _read_rows(path, data_id)
    types = _column_types(header, rows, data_id, path.name)
    precision, sig = _rounding(attrs, data_id)
    return _render_rows(header, rows, types, _select_columns(header, attrs, data_id, path.name), precision, sig)


def _content_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _uncommented(source: str) -> str:
    """``source`` with HTML comments blanked to spaces (same length, newlines kept)."""
    return re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m.group(0)), source, flags=re.S)


@dataclass
class Region:
    """One inlined region: a registered figure's media, a data block's
    payload, or a registered table's rows."""
    kind: str                 # "figure" | "data" | "table"
    item_id: str
    tagname: str
    attrs: list[tuple[str, str | None]]
    tag_start: int
    tag_end: int
    content_start: int | None  # None: the figure/table has no markers
    content_end: int | None

    def attr(self, name: str) -> str | None:
        return next((v for k, v in self.attrs if k == name), None)


_ID_ATTR = {"figure": "data-sci-fig", "table": "data-sci-table", "script": "data-sci-data"}
_MARKER = {"figure": "sci-media", "table": "sci-rows"}


class _RegionParser(HTMLParser):
    """Locates regions with the same tokenizer browsers approximate: quoted
    ``>`` in attribute values, comments, and markup inside script strings are
    all handled by the parser rather than by pattern matching."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        self.open: list[dict] = []
        self.comments: list[tuple[int, int, str]] = []
        self.found: list[Region] = []

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        start = self._offset()
        raw = self.get_starttag_text() or ""
        end = start + len(raw)
        attr = _ID_ATTR.get(tag)
        item = next((v for k, v in attrs if k == attr), None) if attr else None
        worked = None
        if tag == "table" and not item:
            worked = next((v for k, v in attrs if k == "data-sci-worked"), None)
            item = worked
        if item:
            _, attrs = tag_attrs(raw)  # keep the author's attribute spelling
        self.open.append({"tag": tag, "attrs": attrs, "start": start, "end": end, "item": item, "worked": bool(worked)})

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_comment(self, data):
        start = self._offset()
        self.comments.append((start, start + len(data) + 7, data.strip()))

    def handle_endtag(self, tag):
        close = self._offset()
        for k in range(len(self.open) - 1, -1, -1):
            if self.open[k]["tag"] == tag:
                el = self.open[k]
                del self.open[k:]
                self._finish(el, close)
                return

    def _finish(self, el: dict, close: int) -> None:
        if not el["item"]:
            return
        tag = el["tag"]
        if tag == "script":
            kind, lo, hi = "data", el["end"], close
        else:
            kind = "worked" if el.get("worked") else tag
            name = _MARKER[tag]
            inside = [c for c in self.comments if el["end"] <= c[0] and c[1] <= close]
            opening = next((c for c in inside if c[2] == name), None)
            ending = next((c for c in inside if opening and c[0] >= opening[1] and c[2] == "/" + name), None)
            lo, hi = (opening[1], ending[0]) if opening and ending else (None, None)
        self.found.append(Region(kind, el["item"], tag, list(el["attrs"]), el["start"], el["end"], lo, hi))


def regions(source: str) -> list[Region]:
    """Every inlined region outside comments and scripts, in document order."""
    parser = _RegionParser(source)
    parser.feed(source)
    parser.close()
    return sorted(parser.found, key=lambda r: r.tag_start)


_MARKER_HINT = {"figure": "<!-- sci-media --><!-- /sci-media -->", "table": "<!-- sci-rows --><!-- /sci-rows -->",
                "worked": "<!-- sci-rows --><!-- /sci-rows -->"}


def plan(source: str, manifest: dict, base: Path) -> list[Edit]:
    figures = {f.get("id"): f for f in manifest.get("figures") or [] if isinstance(f, dict)}
    data = {d.get("id"): d for d in manifest.get("data") or [] if isinstance(d, dict)}
    worked = {w.get("id"): w for w in manifest.get("worked_examples") or [] if isinstance(w, dict)}
    edits: list[Edit] = []
    errors: list[str] = []
    for r in regions(source):
        try:
            if r.kind == "worked":
                # Rendered from the manifest itself; fingerprinted by its rows.
                entry = worked.get(r.item_id)
                if entry is None:
                    raise SyncError(f"worked example {r.item_id!r}: data-sci-worked id not in manifest worked_examples[*]")
                if r.content_start is None:
                    raise SyncError(f"worked example {r.item_id!r}: no {_MARKER_HINT['worked']} markers inside the table")
                content = worked_rows(entry, r.item_id, dict(r.attrs))
                new_tag = _build_tag(r.tagname, r.attrs, {
                    "data-sha256": worked_rows_sha256(entry.get("rows") or []),
                    "data-content-sha256": _content_sha(content),
                })
                edits.append(Edit(r.tag_start, r.tag_end, new_tag))
                edits.append(Edit(r.content_start, r.content_end, content))
                continue
            registry, section = (figures, "figures") if r.kind == "figure" else (data, "data")
            entry = registry.get(r.item_id)
            if entry is None:
                raise SyncError(f"{r.kind} {r.item_id!r}: {_ID_ATTR[r.tagname]} id not in manifest {section}[*]")
            if r.kind == "data" and (r.attr("type") or "").strip().lower() != "application/json":
                # The runtime reads only script[type="application/json"][data-sci-data].
                raise SyncError(f"data {r.item_id!r}: the block must be <script type=\"application/json\"> "
                                "(the runtime reads only JSON blocks)")
            if r.content_start is None:
                raise SyncError(f"{r.kind} {r.item_id!r}: no {_MARKER_HINT[r.kind]} markers inside the {r.kind}")
            path = _checked_source(r.kind, r.item_id, entry, base)
            if r.kind == "figure":
                content = figure_markup(path, r.item_id, r.attr("data-alt") or "")
            elif r.kind == "table":
                content = table_rows(path, r.item_id, dict(r.attrs))
            else:
                content = data_payload(path, r.item_id)
            new_tag = _build_tag(r.tagname, r.attrs, {
                "data-sha256": entry["sha256"].lower(),
                "data-content-sha256": _content_sha(content),
            })
            edits.append(Edit(r.tag_start, r.tag_end, new_tag))
            edits.append(Edit(r.content_start, r.content_end, content))
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
    edits: list[Edit] = []
    for r in regions(source):
        if r.content_start is None:
            continue
        region = source[r.content_start:r.content_end]
        label = f"{r.kind} {r.item_id}" + (" rows" if r.kind in ("table", "worked") else "")
        edits.append(Edit(r.content_start, r.content_end,
                          f"[{label}: {len(region)} characters omitted for review]" + "\n" * region.count("\n")))
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
        out.parent.mkdir(parents=True, exist_ok=True)
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
