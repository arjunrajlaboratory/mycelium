"""check_html_report — structural gate for an HTML report and its companion slides.

``scitexlintr`` checks the *scientific* content of an HTML report (values,
figures, data against the manifest). This script checks the conventions the
report-generator pack adds on top — the parts that make the file a report
plus a deck:

Errors
  placeholder     a ``%%PLACEHOLDER%%`` marker from the template remains
  self-contained  a script, stylesheet, frame, or media element loads from a
                  URL or a relative path (figures must be inlined by
                  sync_html_report.py; hyperlinks are fine)
  runtime         the ``<script id="sci-report-runtime">`` block is missing
  slide-count     the deck has fewer than 10 or more than 20 slides
                  (the title slide counts)
  title-slide     the first slide is not ``data-slide="title"``, or another
                  slide is
  slide-title     a non-title slide does not have exactly one ``<h2>`` that
                  is a single declarative sentence: at least four words,
                  ending in a period, with no colon, semicolon, question
                  mark, or second sentence
  slide-source    a non-title slide lacks ``data-source="#id"`` naming an
                  element of the report (outside the deck)
  slide-figure    a ``data-fig-ref`` is not on a ``class="slide-figure"``
                  element (the only place the runtime places figures), does
                  not name a report ``<figure>``, or a slide references more
                  than one figure

Warnings (errors with ``--strict``)
  slide-words     more than 45 words of body text on a slide
  slide-duplicate two slides share a title
  runtime         the runtime or style block differs from the template —
                  the installed pack's, else the plugin's (stale or
                  hand-edited: re-copy both blocks from it)
  file-size       the file exceeds 15 MB (usually several large raster
                  figures; register smaller exports)

Whether a title makes exactly one point, with a real subject, verb, and
object, is a judgment the Phase 9 storyline reviewer makes; this gate only
rejects titles whose shape cannot be a sentence.

Usage::

    python skills/core/scripts/check_html_report.py analysis/<name>/reports/<name>-report.html
    python skills/core/scripts/check_html_report.py --report-only <report>.html   # Phase 7, before Phase 9

``--report-only`` skips the deck rules (and the ``%%SLIDES%%`` placeholder)
so the report can pass its gate before the slides are written.

The runtime comparison uses the template of the project's installed pack
(``.living/conventions/report-generator/assets/report-template.html`` in an
ancestor of the report), falling back to the plugin's bundled template;
``--template`` overrides both.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sync_html_report import _uncommented  # noqa: E402  (one definition of "outside comments")

TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "network/conventions/report-generator/assets/report-template.html"
)

VOID = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})
_ABBREVIATION_RE = re.compile(r"\b(?:e\.g|i\.e|et al|vs|Fig|Figs|Eq|No|approx|ca|Dr|St)\.")
# <link> relations that only name another document; every other rel
# (stylesheet, icon, preload, modulepreload, manifest, …) makes the browser fetch.
_NONFETCHING_LINK_RELS = {"canonical", "alternate", "author", "license", "help", "next", "prev", "bookmark", "me"}
MAX_BODY_WORDS = 45
MAX_FILE_BYTES = 15 * 1024 * 1024
MIN_TITLE_WORDS = 4


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    line: int
    col: int
    message: str


@dataclass
class Node:
    tag: str
    attrs: dict
    line: int
    col: int
    parent: "Node | None" = None
    children: list = field(default_factory=list)  # Node | str

    def iter(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.iter()

    def text(self, skip=lambda n: False) -> str:
        parts = []
        for c in self.children:
            if isinstance(c, str):
                parts.append(c)
            elif not skip(c) and c.tag not in ("script", "style", "template"):
                parts.append(" " + c.text(skip) + " " if c.tag in _BLOCK else c.text(skip))
        return "".join(parts)

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())

    def has_ancestor(self, pred) -> bool:
        p = self.parent
        while p is not None:
            if pred(p):
                return True
            p = p.parent
        return False


_BLOCK = frozenset({"p", "div", "li", "ul", "ol", "section", "h1", "h2", "h3", "h4", "figure", "figcaption", "tr", "td", "th", "br"})


class _Builder(HTMLParser):
    def __init__(self, source: str = ""):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, 1, 1)
        self.stack = [self.root]
        self.styles: list[tuple[str, int, int]] = []
        self.source = source
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", source)]
        # Raw source extents of the first <style> and of the first runtime
        # script, found by the parser (never by pattern matching, which a
        # string or comment mentioning the tag would fool).
        self.blocks: dict[str, tuple[int, int, int]] = {}
        self._open_blocks: dict[str, int] = {}

    def _offset(self) -> int:
        line, col = self.getpos()
        return self.line_starts[line - 1] + col if line - 1 < len(self.line_starts) else 0

    def handle_starttag(self, tag, attrs):
        line, col = self.getpos()
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, line, col + 1, self.stack[-1])
        self.stack[-1].children.append(node)
        key = "style" if tag == "style" else "runtime" if tag == "script" and node.attrs.get("id") == "sci-report-runtime" else None
        if key and key not in self.blocks and key not in self._open_blocks:
            self._open_blocks[key] = self._offset()
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        line, col = self.getpos()
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, line, col + 1, self.stack[-1])
        self.stack[-1].children.append(node)

    def handle_endtag(self, tag):
        key = "style" if tag == "style" else "runtime" if tag == "script" else None
        if key in self._open_blocks:
            close = self._offset()
            end = self.source.find(">", close) + 1
            start = self._open_blocks.pop(key)
            line = self.source.count("\n", 0, start) + 1
            self.blocks[key] = (start, end, line)
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)
        if self.stack[-1].tag == "style":
            line, col = self.getpos()
            self.styles.append((data, line, col + 1))


def _norm(s: str) -> str:
    return " ".join(html.unescape(s).split())


def _parse(source: str) -> _Builder:
    builder = _Builder(source)
    builder.feed(source)
    builder.close()
    return builder


def _block_text(builder: _Builder, key: str) -> str | None:
    extent = builder.blocks.get(key)
    return builder.source[extent[0]:extent[1]] if extent else None


# ---------------------------------------------------------------------------


def _title_problems(title: str) -> list[str]:
    problems = []
    words = re.findall(r"[^\s]+", title)
    if not title:
        return ["is empty"]
    if title.endswith("?"):
        problems.append("is a question; state the finding instead")
    elif title.endswith("!"):
        problems.append("ends with an exclamation mark")
    elif not title.endswith("."):
        problems.append("does not end with a period")
    if ":" in title:
        problems.append("contains a colon (a label, not a sentence)")
    if ";" in title:
        problems.append("contains a semicolon (two points on one slide)")
    # A second sentence: terminal punctuation, space, then a capital letter.
    # Abbreviations are masked first, so "Fig. A" does not end a sentence but
    # does not excuse a real second sentence elsewhere in the title either.
    masked = _ABBREVIATION_RE.sub(lambda m: m.group(0).replace(".", "_"), title[:-1])
    if re.search(r"[.!?]\s+[A-Z]", masked):
        problems.append("contains more than one sentence")
    if len(words) < MIN_TITLE_WORDS:
        problems.append(f"has {len(words)} word(s); a sentence with subject, verb, and object needs at least {MIN_TITLE_WORDS}")
    return problems


INSTALLED_TEMPLATE = Path(".living/conventions/report-generator/assets/report-template.html")


def find_template(report: Path) -> Path:
    """The installed pack's template nearest the report, else the bundled one."""
    for parent in report.resolve().parents:
        candidate = parent / INSTALLED_TEMPLATE
        if candidate.is_file():
            return candidate
    return TEMPLATE


def check_source(source: str, filename: str = "<report>", *, min_slides: int = 10, max_slides: int = 20,
                 template: Path | None = TEMPLATE, report_only: bool = False) -> list[Finding]:
    builder = _parse(source)
    root = builder.root
    live = _uncommented(source)
    findings: list[Finding] = []

    def emit(code, severity, node_or_pos, message):
        if isinstance(node_or_pos, Node):
            line, col = node_or_pos.line, node_or_pos.col
        else:
            line, col = node_or_pos
        findings.append(Finding(code, severity, line, col, message))

    # -- placeholders (template guidance comments may mention them) ----------
    for m in re.finditer(r"%%[A-Z][A-Z0-9_]*%%", live):
        if report_only and m.group(0) == "%%SLIDES%%":
            continue
        line = source.count("\n", 0, m.start()) + 1
        col = m.start() - (source.rfind("\n", 0, m.start()) + 1) + 1
        emit("placeholder", "error", (line, col), f"template placeholder {m.group(0)} was not replaced")

    # -- self-containment ----------------------------------------------------
    nodes = list(root.iter())
    for n in nodes:
        t, a = n.tag, n.attrs
        refs = []
        if t == "script" and "src" in a:
            refs.append(("src", a["src"]))
        if t == "link" and a.get("href"):
            rels = set((a.get("rel") or "").lower().split())
            if not rels or rels - _NONFETCHING_LINK_RELS:
                refs.append(("href", a["href"]))
        style = a.get("style") or ""
        for m in re.finditer(r"url\(\s*['\"]?(?!data:|#)([^'\")]+)", style):
            refs.append(("style", m.group(0)))
        if t in ("img", "source", "video", "audio", "track", "iframe", "embed", "input"):
            for k in ("src", "srcset", "poster"):
                if k in a:
                    refs.append((k, a[k]))
        if t == "object" and "data" in a:
            refs.append(("data", a["data"]))
        if t in ("image", "use") and n.has_ancestor(lambda p: p.tag == "svg"):
            for k in ("href", "xlink:href"):
                v = a.get(k, "")
                if v and not v.startswith("#"):
                    refs.append((k, v))
        for k, v in refs:
            if not v.strip().startswith("data:"):
                emit("self-contained", "error", n, f"<{t} {k}={v[:60]!r}> loads an external resource; inline it (figures: sync_html_report.py)")
    for css, line, col in builder.styles:
        for m in re.finditer(r"@import|url\(\s*['\"]?(?!data:|#)([^'\")]+)", css):
            emit("self-contained", "error", (line, col), f"stylesheet references an external resource ({m.group(0)[:60]!r})")

    size = len(source.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        emit("file-size", "warning", (1, 1), f"report is {size / 1048576:.1f} MB; above {MAX_FILE_BYTES // 1048576} MB it loads slowly and may not upload — register smaller figure exports")

    # -- runtime -------------------------------------------------------------
    runtime = _block_text(builder, "runtime")
    if runtime is None:
        emit("runtime", "error", (1, 1), 'the <script id="sci-report-runtime"> block is missing; copy it from the template')
    elif template is not None and template.is_file():
        tmpl = _parse(template.read_text(encoding="utf-8"))
        if runtime != _block_text(tmpl, "runtime") or _block_text(builder, "style") != _block_text(tmpl, "style"):
            emit("runtime", "warning", (builder.blocks["runtime"][2], 1),
                 f"the runtime or style block differs from the template ({template}); re-copy both blocks from it")

    # -- deck ----------------------------------------------------------------
    if report_only:
        return _sorted(findings)
    by_id = {n.attrs["id"]: n for n in nodes if n.attrs.get("id")}
    deck = by_id.get("deck")
    if deck is None:
        emit("slide-count", "error", (1, 1), 'no <div id="deck"> slide deck found')
        return _sorted(findings)
    in_deck = lambda n: n is deck or n.has_ancestor(lambda p: p is deck)  # noqa: E731
    # Whatever element carries class="slide" — the runtime's own selector.
    slides = [n for n in deck.iter() if "slide" in n.classes()]
    if not (min_slides <= len(slides) <= max_slides):
        emit("slide-count", "error", deck, f"deck has {len(slides)} slide(s); scope it to {min_slides}–{max_slides} (title slide included)")
    for i, s in enumerate(slides):
        is_title = s.attrs.get("data-slide") == "title"
        if i == 0 and not is_title:
            emit("title-slide", "error", s, 'the first slide must be the title slide (data-slide="title")')
        if i > 0 and is_title:
            emit("title-slide", "error", s, "only the first slide may be a title slide")

    titles: dict[str, Node] = {}
    for i, s in enumerate(slides):
        if s.attrs.get("data-slide") == "title":
            continue
        label = f"slide {i + 1}"
        h2s = [n for n in s.iter() if n.tag == "h2"]
        if len(h2s) != 1:
            emit("slide-title", "error", s, f"{label} has {len(h2s)} <h2> titles; each slide has exactly one sentence title")
        else:
            title = _norm(h2s[0].text())
            for problem in _title_problems(title):
                emit("slide-title", "error", h2s[0], f"{label} title {title!r} {problem}")
            key = title.lower()
            if key in titles:
                emit("slide-duplicate", "warning", h2s[0], f"{label} repeats the title of an earlier slide")
            titles[key] = h2s[0]

        src = s.attrs.get("data-source")
        if not src:
            emit("slide-source", "error", s, f'{label} has no data-source="#section-id"; Esc returns the reader there')
        elif not src.startswith("#") or src[1:] not in by_id:
            emit("slide-source", "error", s, f"{label} data-source {src!r} does not name an element id")
        elif in_deck(by_id[src[1:]]):
            emit("slide-source", "error", s, f"{label} data-source {src!r} points into the deck, not the report")

        refs = [n for n in s.iter() if "data-fig-ref" in n.attrs]
        for r in refs:
            if "slide-figure" not in r.classes():
                # The runtime fills only .slide-figure[data-fig-ref]; anything else renders empty.
                emit("slide-figure", "error", r, f'{label} data-fig-ref is not on a class="slide-figure" '
                                                  "element, so the runtime will not place the figure")
        if len(refs) > 1:
            emit("slide-figure", "error", refs[1], f"{label} references {len(refs)} figures; one figure per slide")
        for r in refs:
            target = by_id.get(r.attrs["data-fig-ref"])
            if target is None or target.tag != "figure" or in_deck(target):
                emit("slide-figure", "error", r, f"{label} data-fig-ref {r.attrs['data-fig-ref']!r} does not name a <figure> in the report")

        body = _norm(s.text(skip=lambda n: n.tag == "h2" or "slide-source" in n.classes()))
        n_words = len(re.findall(r"\w[\w'’.-]*", body))
        if n_words > MAX_BODY_WORDS:
            emit("slide-words", "warning", s, f"{label} has {n_words} words of body text; keep it under {MAX_BODY_WORDS} (the report carries the detail)")

    return _sorted(findings)


def main_text_stats(source: str) -> dict:
    """Word and figure counts for the main text — the HTML stand-in for the
    TeX shape budget's page count. Excludes the supplement, provenance, the
    footer, the deck, figure media, and code."""
    builder = _parse(source)
    main = next((n for n in builder.root.iter() if n.tag == "main"), None)
    if main is None:
        return {"words": 0, "figures": 0}
    excluded_ids = {"supplement", "provenance"}

    def skip(n: Node) -> bool:
        return (
            n.attrs.get("id") in excluded_ids
            or n.tag in ("footer", "pre", "code", "svg", "nav")
            or "sci-media" in n.classes()
        )

    text = _norm(main.text(skip=skip))
    figures = [
        n for n in main.iter()
        if n.tag == "figure" and not n.has_ancestor(lambda p: p.attrs.get("id") in excluded_ids)
    ]
    return {"words": len(re.findall(r"\w[\w'’.-]*", text)), "figures": len(figures)}


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (f.line, f.col, f.code))


def check_file(path: str | Path, **kwargs) -> list[Finding]:
    p = Path(path)
    kwargs = {k: int(v) if k in ("min_slides", "max_slides") else v for k, v in kwargs.items()}
    kwargs.setdefault("template", find_template(p))
    return check_source(p.read_text(encoding="utf-8"), str(p), **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="check_html_report", description="Check an HTML report's structure and slides")
    parser.add_argument("paths", nargs="+", help="report .html files")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors")
    parser.add_argument("--min-slides", type=int, default=10)
    parser.add_argument("--max-slides", type=int, default=20)
    parser.add_argument("--report-only", action="store_true",
                        help="skip the deck rules (Phase 7 before Phase 9 has written the slides)")
    parser.add_argument("--template", default=None, help="template to compare the runtime block against")
    args = parser.parse_args(argv)
    failed = False
    for raw in args.paths:
        path = Path(raw)
        if not path.is_file():
            print(f"not found: {path}", file=sys.stderr)
            return 2
        extra = {"template": Path(args.template)} if args.template else {}
        findings = check_file(path, min_slides=args.min_slides, max_slides=args.max_slides,
                              report_only=args.report_only, **extra)
        for f in findings:
            print(f"{path}:{f.line}:{f.col}: [{f.code}] {f.severity}: {f.message}")
        errors = [f for f in findings if f.severity == "error" or args.strict]
        failed = failed or bool(errors)
        n_err = sum(f.severity == "error" for f in findings)
        n_warn = len(findings) - n_err
        stats = main_text_stats(path.read_text(encoding="utf-8"))
        print(
            f"{path}: {n_err} error(s), {n_warn} warning(s); main text "
            f"{stats['words']} words, {stats['figures']} figure(s)",
            file=sys.stderr,
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
