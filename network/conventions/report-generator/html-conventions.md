# HTML Report + Slides — Format Conventions

Read this when the Phase-0 planning brief chose `format: html`. The HTML format produces **one self-contained file** with two modes: the long-form report, and a companion deck of 10–20 slides generated from it. Everything in `analysis-conventions.md` still applies — the planning brief, memory cheatsheet, outline, manifest, the worked-example gate, and the three blind sub-agent reviewers are format-independent. This file covers only what changes:

| Phase | TeX / PDF | HTML + slides |
|---|---|---|
| 1 — Manifest | as written | adds `data[*]` for interactive figures and `display_html` where a TeX `display` override exists |
| 2 — Draft | `.tex` template, `\SciVal` / `\SciText` | `assets/report-template.html`, value spans, declared figures |
| 4–6 — Reviewers | read the `.tex` | read the `.html` report (not the deck) |
| 7 — Gate | render macros → scitexlintr → pdflatex | sync → scitexlintr `--write` → scitexlintr → check → browser render |
| 9 — Slides | — | ghost deck → blind storyline review → slides → re-run the Phase 7 gate |

Order matters: **write and gate the report first, then derive the slides.** The slides summarize a finished, verified report; they never introduce a number, figure, or claim the report does not contain.

Reference implementation: `assets/html-example/` is a complete synthetic report + 12-slide deck that passes every gate. Open `assets/html-example/reports/example-report.html` in a browser before drafting, and crib from `build_example.py` for markup.

---

## Prerequisites

```bash
python -m pip install "scitexlintr>=0.2"   # or the git install in CONVENTION_PACK.yaml
scitexlintr --version                       # must print 0.2.0 or later
which pdftocairo                            # only needed when a registered figure is a PDF
```

`scitexlintr` 0.1.x lints only TeX and will silently treat an `.html` file as TeX (every finding wrong), and it has no `--version` flag — if the command above errors, upgrade before Phase 7. Poppler's `pdftocairo` converts PDF figures to inline SVG; without it, register an SVG or PNG export of the figure instead.

---

## Phase 1 additions — manifest

The manifest schema is unchanged (see `references/manifest-example.json`) with two additions:

- **`data[*]`** — one entry per data file behind an interactive figure: `{"id", "path", "sha256"}`. Register the analysis output (JSON, CSV, or TSV), not a copy made for the report. Paths resolve against the report's directory, exactly like `figures[*].path`.
- **`display_html`** — if a number carries a TeX-only `display` override (e.g. `"3.2$\\times$"`), add the HTML rendering (`"3.2×"`). scitexlintr reports an error for a wrapper whose entry has `display` but no `display_html`; derive with `unit` / `precision` instead whenever possible.

Three manifest behaviors matter more in HTML, where every word of the report is linted against the manifest (they hold in TeX too):

- **`label_aliases_forbidden` bans the phrase anywhere in the prose**, not only next to that value. List a phrase only if it is wrong everywhere in this report ("accuracy" when every accuracy in the report is exact-match accuracy); a word the report legitimately uses elsewhere ("edges", "median") cannot be a forbidden alias.
- **`overloaded_warning` must appear verbatim in the prose** at or before the term's first use. Write it as the reader-facing sentence you will put in the report ("This is not a Wilks-sense likelihood-ratio test."), not as an instruction to the writer.
- **Rounded display comes from `unit` + `precision`**, not hand-typed strings: `"unit": "percent"` (stored fraction → `95.4%`) or `"unit": "decimal"` (`7.47712` → `7.48` at precision 2), rounded half-up. A plain-text `display` (`"0.3183"`) is used as-is in HTML; a TeX `display` — one containing `\\ $ { } ^ ~ %` or the ligatures `--`, ```` `` ```` or `''` — needs a `display_html`. `display_html` is HTML markup (`"3&times;"`, `"1.0 × 10<sup>-4</sup>"`): the span is checked by its rendered text and `--write` inserts the markup as written.

Every figure and data entry needs a `sha256`. `sync_html_report.py` refuses to inline a file whose hash disagrees with the manifest — a regenerated figure means Phase 1 is re-run first.

---

## Phase 2 — Draft (HTML)

Copy `assets/report-template.html` to `analysis/[name]/reports/[name]-report.html` and replace every `%%PLACEHOLDER%%`. The template is the overview + supplement shape; for **overview**, delete the `<section id="supplement">` block; for **comprehensive**, move the supplement's methods detail into Methods and drop the supplement heading. The section guide (`references/section-guide.md`), both standing prose rules, US English, finding-form Results headings, and every other drafting rule in `analysis-conventions.md` Phase 2 apply unchanged.

Do not edit the template's `<style>` block or its `<script id="sci-report-runtime">` block. They are the shared runtime; `check_html_report.py` warns when either differs from the bundled template, and a template upgrade replaces both wholesale. Report-specific behavior goes in a separate `<script>` after the runtime (see *Custom interactive figures*).

### Values

Every reportable value is a wrapper span whose text is the rendered value:

```html
We analyzed <span data-sci-val="diff-expr.n_samples">48</span> cells passing QC.
For <span data-sci-text="diff-expr.contrast_phrase">treated versus control</span>, …
<span data-sci-val="frac_dated">96.5%</span> of dated claims …   <!-- unit: percent, precision: 1 -->
```

- `data-sci-val` for numbers, `data-sci-text` for text values. The attribute is the manifest id; the namespace-stripped key also resolves (`n_samples` for `diff-expr.n_samples`).
- A span may narrow the precision of a unit-derived value for one use with `data-precision`: `<span data-sci-val="frac" data-precision="0">95%</span>` on a slide where the report says `95.4%`.
- The span's text is what the reader sees **and** what scitexlintr checks. You do not need to get it right by hand: write the span with any placeholder text and let `scitexlintr --write` fill it in Phase 7. A `unit: percent` entry renders from its fraction (`0.9653` → `96.5%`).
- Never type a manifest value as bare text — `raw-generated-value` fails the gate. A typo in the id fails with `unknown-value-id` (HTML has no compiler to catch it the way an undefined TeX macro does).
- Values inside tables are wrapped too; table cells are prose.
- Prefer words for structural numbers ("three doses", "the second experiment") exactly as in TeX.
- Dates go in `<time>` (the template's byline already does this); `<time>` content is not linted, so a publication year or report date needs no waiver. A fractional percent that matches a manifest entry's rendering ("95.4%") typed as text is itself a `raw-generated-value`; a rounded decimal ("1.5") is only an unsourced-number warning, because many unrelated literals round to it.

### Figures

Every `<figure>` declares what it is — scitexlintr's `unfingerprinted-figure` rejects an undeclared one.

**Registered analysis figure** (from `figures[*]`):

```html
<figure class="sci-figure" id="fig-volcano" data-sci-fig="volcano_de" data-alt="Volcano plot of differential expression">
  <div class="sci-media"><!-- sci-media --><!-- /sci-media --></div>
  <figcaption>Self-contained caption.</figcaption>
</figure>
```

Leave the region between the two `sci-media` marker comments empty; `sync_html_report.py` fills it from the manifest (SVG inline with its ids namespaced and its `<style>` rules scoped to the figure, PNG/JPEG as a base64 image, PDF converted to SVG) and sets `data-sha256`. `data-alt` becomes the accessible name; sync also stamps `data-content-sha256`, the hash of the inlined markup, so a hand edit to an inlined figure fails the gate. A PDF whose SVG conversion would exceed 2 MB (dense scatter plots and embedding maps can expand from a few hundred kilobytes to tens of megabytes) is rasterized at 200 dpi instead, and sync says so. For such figures, registering a PNG export is better still. `check_html_report.py` warns above 15 MB for the whole file. Add `class="wide"` to let a figure break out of the text column. The figure's media is not prose (tick labels are not linted); its caption is.

**Hand-drawn schematic** (inline SVG you write — a pipeline diagram, a design sketch):

```html
<figure class="sci-figure" id="fig-design" data-sci-diagram>
  <div class="sci-media"><svg viewBox="0 0 640 150" role="img" aria-label="…">…</svg></div>
  <figcaption>…</figcaption>
</figure>
```

Text inside a diagram **is** prose and is linted, so a diagram cannot smuggle an unregistered number. Draw with `currentColor` so it reads in both themes. Use a diagram when a picture explains a mechanism faster than a paragraph — not to decorate.

**Cross-references.** Write `<a class="xref" href="#fig-volcano">Figure</a>`; the runtime numbers figures and tables at load ("Figure 2", "Figure S1", "Table 1"). Do not type figure numbers — they would be unsourced numeric tokens and would go stale when figures move.

**Structured Results style.** When the brief chose structured Results, use `<h4>Question</h4>`, `<h4>Findings</h4>`, `<h4>Interpretation</h4>` inside each result section in place of the TeX `\paragraph{…}` headers.

**Tables** use `<table class="sci-table">` with a `<caption>`, inside `<div class="table-wrap">` (give the wrapper the id you cross-reference); right-align numeric columns with `class="num"`. Captions and figure captions get their "Table 2." / "Figure S1." labels from the runtime, which numbers the cross-references too — do not type the label. A hand-written table's value cells are prose and use value spans.

**Registered tables** replace hand-written ones whenever a table reproduces an analysis output row for row — exhaustive supplement tables, per-seed results, worked-example rows. Register the CSV/TSV in `data[*]` and let sync generate the rows; the cells are fingerprinted instead of linted, so they need no spans and no waivers:

```html
<div class="table-wrap" id="tab-replicates">
  <table class="sci-table" data-sci-table="doubling_by_replicate" data-columns="condition,replicate,doubling_time_h" data-precision="1">
    <caption>Doubling time of every replicate well.</caption>
    <thead><tr><th>Condition</th><th class="num">Replicate</th><th class="num">Doubling time (hours)</th></tr></thead>
    <tbody><!-- sci-rows --><!-- /sci-rows --></tbody>
  </table>
</div>
```

`data-columns` selects and orders columns (default: all); `data-precision` rounds the cells of fractional columns half-up (default: the file's spelling). Columns are typed as a whole: a column is numeric only if every cell is a plain number, so labels such as `007` or `1_1` keep their spelling, and integer columns (counts, replicate numbers, years) are never given decimals. You write the caption and header; sync owns the rows between the markers.

**Other markup.** Definitions: `<dl>`. Display math: `<math display="block">` (scrolls horizontally rather than widening the page on a phone). References: a `<section id="references"><h2>References</h2><ol class="references">…</ol></section>` before the supplement, when the report cites anything. Delete the template's guidance comments as you fill each section.

**Caveats** that must be as prominent as their claim go in `<div class="caveat" id="…"><span class="caveat-label">Main caveat</span><p>…</p></div>`.

### Interactive figures (sliders, animation) — sparingly

Use an interactive figure only when **time (or an ordered parameter) is the axis of the argument** and a reader genuinely learns more by scrubbing through it than by reading a static plot: growth curves under several conditions, a trajectory, a dose sweep. A static figure is the default. At most one or two per report.

```html
<figure class="sci-figure wide" id="fig-growth" data-sci-interactive="timeseries"
        data-default-index="last" data-y-label="Cells per well (thousands)">
  <script type="application/json" data-sci-data="growth_counts"></script>
  <div class="sci-media"></div>
  <figcaption>…describe the default (final) state…</figcaption>
</figure>
```

- The data comes **only** from a registered `data[*]` block, which `sync_html_report.py` fills and fingerprints (source-file `data-sha256` plus `data-content-sha256` of the inlined payload, so a hand edit to the embedded data fails the gate). Never write data literals in script: an interactive figure with no registered block fails `unfingerprinted-data`, and a numeric array in a report script is flagged by `script-data-literal`.
- The built-in `timeseries` kind renders lines, a scrubber, a Play button that animates through time, a pinned readout, and a data-table view. Data shape: `{"x": [...], "x_label", "y_label", "series": [{"name", "values": [...]}], "y_min", "y_format"}` or a CSV/TSV registered as-is (first column is x, the others are series). With a CSV, `data-series="col_a,col_b"` picks and orders the columns to plot and `data-series-names="Low retention,High retention"` gives them reader-facing names. Up to eight series; fold the rest into "Other" or facet.
- One axis per figure. A derived series on a different scale (a difference, a ratio) is a second figure, not a second line on the same axis — leave it out of `data-series`.
- **The default state is the figure.** Print, reduced motion, and a reader who never touches the slider all see `data-default-index` (default: the last point). The caption and any prose describe that state, and every number the prose quotes is a wrapped manifest value — never a value read off the slider. The readout (`data-sci-live`) is runtime output and is not linted.
- No autoplay. Animation starts only when the reader presses Play.

**Custom interactive figures.** When no built-in kind fits, register one in a script after the runtime:

```html
<script>
SciReport.register("phase-portrait", function (figure, data) {
  // draw into figure.querySelector(".sci-media") from `data` only
});
</script>
```

The same contract holds: data only from the figure's `data-sci-data` block, a meaningful static default state, no autoplay, and readouts marked `data-sci-live`. The runtime re-initializes the kind for the figure's copy on a slide.

### Waivers

`<!-- ANALYSIS_OK[rule-code]: what, why valid, where recorded -->` on or up to four lines above the finding — the same rule-scoped waiver as TeX. Inside a `<script>`, where an HTML comment cannot appear, write it as a JavaScript comment: `// ANALYSIS_OK[script-data-literal]: …`. Do not nest `<!-- -->` inside another comment: HTML comments do not nest, so the inner `-->` ends the outer comment and the rest renders as page text.

---

## Phases 4–6 — Reviewers (HTML)

Pass the sub-agents a **reviewer copy** of the report in place of the `.tex` sources, with the same input lists and output contract (`references/phase-prompts.md`). Inlined figures and data make lines hundreds of kilobytes long; the reviewer copy replaces each inlined region with a one-line placeholder and keeps line numbers, so `file:line` findings point into the real report:

```bash
python skills/core/scripts/sync_html_report.py [name]-report.html --manifest .manifest.json --reviewer-copy /tmp/[name]-review.html
```
 Tell each reviewer to read only `<main>` — the text inside `<div id="deck">` is the slide deck, which Phase 9 reviews separately. In Phase 6, the "compiled PDF" input is the rendered page: give the reviewer the `.html` path and, when a browser is available, screenshots of each figure. Display-faithfulness checks compare each span's text to the manifest value through `unit` / `precision` / `display_html`, which scitexlintr already enforces; the reviewer's job there is the label next to the span.

---

## Phase 7 — Gate (HTML)

Run in order; the gate **must not proceed** past a failing step.

1. **Sync figures and data.**
   `python skills/core/scripts/sync_html_report.py analysis/[name]/reports/[name]-report.html --manifest analysis/[name]/reports/.manifest.json`
   A sha256 mismatch means a figure or data file changed after the manifest was built: re-run the analysis step if needed, refresh Phase 1, and sync again.
2. **Fill values.** `scitexlintr [name]-report.html --manifest=.manifest.json --write` rewrites stale span text from the manifest. Review the diff — it is the list of numbers that changed.
3. **Lint.** `scitexlintr [name]-report.html --manifest=.manifest.json` must exit with zero errors after waivers. Warnings follow the TeX gate's policy (fix real drift risks; document intentional leftovers in the compile log).
4. **Structure.** `python skills/core/scripts/check_html_report.py --report-only [name]-report.html` must report zero errors — `--report-only` skips the deck rules while the deck is still empty (before Phase 9); drop the flag once the slides exist. It checks placeholders, self-containment (no external scripts, styles, frames, or media — hyperlinks are fine), the runtime block, and every slide rule below. Its summary line reports the **main-text word count**, which stands in for the TeX page count in the shape budget:
   - **Overview**: target ≤ 1,800 words; flag above 2,200.
   - **Overview + supplement** (DEFAULT): target ≤ 4,000 words of main text (the 10-minute read); flag above 5,000.
   - **Comprehensive**: flag below 1,800 words.
5. **Render and look.** Open the file in a browser (or screenshot it with Playwright / the browser tools). Check that every figure appears, that cross-references read "Figure N", that the console is free of errors, and that nothing overflows at phone width. The linters check content, not layout. Screenshot long pages in viewport-sized chunks: Chrome repeats content in full-page captures taller than 16,384 px, which looks like duplicated sections but is not.
6. **Record** in `.compile-log-html.md` the same fields as the TeX gate, with these substitutions: the HTML file's SHA256 instead of the PDF's; main-text word count and shape-budget status instead of page count; `sync_html_report.py` result; `scitexlintr` version, exit code, error and warning counts; `check_html_report.py` error and warning counts; and the slide count and storyline-review verdict from Phase 9.

After the deck is written (Phase 9), run steps 1–5 again on the whole file — the deck lives in the same file, so its values, figure references, and structure pass through the same gate.

---

## Phase 9 — Slides (after the report passes Phase 7, before Phase 8)

The deck is the report's argument in 10–20 slides (the title slide counts). It is for presenting the finished work to people who have not read the report; the report remains the record.

### 9a. Ghost deck (INTERNAL)

Before writing any slide body, write only the titles, in order, into `analysis/[name]/reports/.ghost-deck.md`: one line per slide, plus the `data-source` section each summarizes. Rules for every title except the title slide:

- **A complete declarative sentence — subject, verb, object — ending with a period.** "Treated cells grow more slowly than controls." Not "Growth results", not "Results: growth slows", not a question.
- **One slide, one point.** If the title needs "and", a semicolon, or a second sentence, it is two slides.
- **States the finding, not the topic,** exactly like a Results heading — the report's finding-form `<h3>` headings are the natural starting point.
- Numbers in a title are wrapped values, like everywhere else; prefer words when the number is not the point.

Read the titles alone, top to bottom. They must tell the whole story — question, approach, each finding, the main caveat, what comes next — with no slide body. Typical arc: title → the finding in one line → the question and why it matters → approach (one or two slides) → one slide per result → the main caveat → next steps. Put the headline finding early (slide 2), not as a reveal at the end. The main caveat gets its own slide.

### 9b. Blind storyline review (SUB-AGENT)

Dispatch a sub-agent with zero project context that reads **only `.ghost-deck.md`** (prompt in `references/phase-prompts.md`, Phase 9). It restates the headline finding, the comparison baseline, and the main caveat from the titles alone, and flags every title that is not a single declarative sentence or that carries more than one point. The orchestrator compares its restatement against the Phase-0 planning brief: a mismatch in headline, baseline, or caveat means the storyline is wrong, and the titles are revised before any slide is built. Loop until the restatement matches and no **major** title findings remain; resolve minor findings at your judgment (a fresh blind reader will always find a new term it would like introduced — that is not a reason for another round). Persist the output to `.review-storyline.yaml`.

### 9c. Build the slides

Replace `%%SLIDES%%` in the deck with one `<section class="slide …">` per ghost-deck line. The first slide is the template's title slide (`data-slide="title"`), the only slide exempt from the sentence rule. Every other slide:

- has exactly one `<h2>` — its ghost-deck title;
- has `data-source="#section-id"` naming the report section it summarizes — Esc, the slide's return link, and the report's "▶ present" links all use it;
- carries **at most one figure**, referenced rather than copied: `<div class="slide-figure" data-fig-ref="fig-id"></div>` (the runtime copies the report's figure, including interactive ones);
- has at most about 40 words of body text — the title carries the point; the body supports it;
- uses the same value spans as the report for every number.

Slide kinds (a class on the section): `slide--statement` (one big wrapped value in `<div class="stat">` plus a `<p class="stat-label">`), `slide--figure` (a figure plus an optional `<p class="takeaway">`), `slide--split` (figure left, `<div class="slide-text">` right — only for a roughly square figure; a wide or multi-panel figure is unreadable at 60% of the stage, so give it `slide--figure`), `slide--points` (at most three short sentences in a `<ul>`). No speaker notes, no agenda slide, no "Questions?" slide. Keep the title slide's title and dek short: they come from the masthead's `%%TITLE%%` / `%%DEK%%`, and a long dek overflows the slide.

The deck needs no extra work for navigation: → / Space / PageDown / click advance; ← / PageUp / a click on the left quarter go back; Home / End jump; F toggles fullscreen; the progress bar at the bottom shows position and jumps to any slide; `report.html#slides/N` opens slide N directly; Esc — or leaving fullscreen — returns to the source section of the current slide. While presenting, printing produces one slide per page.

### 9d. Gate

Re-run Phase 7 steps 1–5, now without `--report-only`. `check_html_report.py` enforces the slide count, the title-slide position, one sentence-shaped `<h2>` per slide, resolvable `data-source` and `data-fig-ref` targets, and one figure per slide (errors), plus body length above 45 words and duplicate titles (warnings). Then check layout in a browser: `SciReport.checkLayout()` (in the page console, or `page.evaluate` in Playwright) must return `[]` — it opens every slide and lists the numbers of any whose content overflows the stage — and click through the deck once to confirm Esc returns to the right section.

---

## Artifacts (HTML)

An analysis can carry both a TeX and an HTML edition, so the HTML run's scaffolding takes an `-html` suffix and never overwrites the TeX run's. Both editions share one `.manifest.json` — the single source of truth for values — and its `policies`; if the HTML edition needs a different shape or audience tier, record it in `.planning-brief-html.yaml` and pass those policy values to the reviewers explicitly.

| Phase | Artifact |
|---|---|
| 0 | `analysis/[name]/reports/.planning-brief-html.yaml` |
| 0.75 | `analysis/[name]/reports/.section-outline-html.md` |
| 2 | `analysis/[name]/reports/[name]-report.html` |
| 4–6 | `.review-plain-english-html.yaml`, `.review-framing-html.yaml`, `.review-numerical-html.yaml` |
| 7 | `analysis/[name]/reports/.compile-log-html.md` |
| 9a | `analysis/[name]/reports/.ghost-deck.md` |
| 9b | `analysis/[name]/reports/.review-storyline.yaml` |

The memory cheatsheet (Phase 0.5) is format-independent and shared.

## Tools

- `skills/core/scripts/sync_html_report.py` — inline and fingerprint figures, data, and registered tables (`--check` for a read-only gate, `--reviewer-copy` for Phases 4–6).
- `skills/core/scripts/check_html_report.py` — structure, self-containment, and slide rules; main-text word count (`--report-only` before Phase 9). It compares the runtime block with the installed pack's template.
- `SciReport.checkLayout()` — in the rendered page, lists slides whose content overflows.
- `scitexlintr` ≥ 0.2 — values, figures, and data against the manifest (`--write` fills values).
- `assets/report-template.html` — the template; `assets/html-example/` — the worked example and its generator.
