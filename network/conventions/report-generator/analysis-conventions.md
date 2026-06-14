# Report Generator Conventions

Guides the generation of structured LaTeX PDF reports from analysis project outputs. This skill is for formal writeups — not PowerPoint, not HTML notebook exports, not quick Markdown summaries (those go in the analysis README).

The skill is organized as a **phase-based agentic flow**. Most phases are internal to the skill; the user is only consulted at Phase 0 (planning brief) and, optionally, Phase 8 (headline preview). Every phase produces a verifiable artifact, and the sub-agent review phases (4–6) get a fresh context so they read the draft blind — that is the missing ingredient in self-declared consistency checks.

---

## Architectural principle

**Automatic except for explicit user questions.** Report generation is designed to be hands-off after the planning brief. The user's review point is the generated PDF, not the intermediate scaffolding. Every check that *could* have become a "show this to the user" step is instead an internal artifact that flows into the final report or into a sub-agent reviewer.

User-in-the-loop phases: Phase 0 (planning brief). Optionally Phase 8 (headline preview before opening the PDF).

Internal-only phases: 0.5 (memory), 0.75 (outline + main/supplement assignment), 1 (manifest), 2 (draft), 3 (worked-example gate), 4–6 (sub-agent reviewers), 7 (recompile).

---

## Prerequisites

Before generating a report, verify that `pdflatex` is available:

```bash
which pdflatex
```

If not installed, guide the user to install the **full** TeX Live distribution:
- **macOS**: `brew install --cask mactex` (full distribution, ~4GB, includes all packages)
- **Linux**: `sudo apt-get install texlive-full` or equivalent
- **Do not** install BasicTeX or minimal distributions — missing packages cause frustrating compilation failures

Also verify that the LaTeX report linter is available:

```bash
which scitexlintr
scitexlintr --help
```

`scitexlintr` is a separate Python package from the analysis-code `scilintr` CLI, even though both live in the `scilintr` repository. Installing `scilintr` does not imply that `scitexlintr` is on the path. If it is missing, install the report linter into the environment that will run Phase 7:

```bash
python -m pip install "scitexlintr @ git+https://github.com/arjunrajlaboratory/scilintr.git#subdirectory=tex/scitexlintr"
```

When working from a local `scilintr` checkout, an editable install is also fine:

```bash
python -m pip install -e /path/to/scilintr/tex/scitexlintr
```

---

## Phase 0 — Planning brief (USER-FACING)

Ask the user a single round of questions before drafting begins. This is the only place the user is required in the loop until they open the PDF. Phrase the questions as one combined exchange — do not ask them serially.

Required questions:

1. **Headline question.** What is the report answering, in one sentence? ("Does our method beat the baseline at recovering known clones?")
2. **Baseline of comparison.** What are the reported numbers being compared against? ("vs. the production pipeline", "vs. random assignment", "vs. published reference X").
3. **Primary metric.** Which single metric should the abstract lead with? ("exact accuracy", not "weighted F1 because it was in the CSV"). If there's tension between what's plentiful in outputs and what's decision-relevant, ask explicitly.
4. **Audience.** Pick one tier. The choice drives the acronym budget per page, the depth of intuitive lead-ins, and how often plain-English glosses recur across sections.
   - **Tier A — Lay or out-of-field reader.** Acronym budget 2 / page; full intuition paragraphs before every load-bearing term; gloss every term on first use *per section* (not just per document).
   - **Tier B — Adjacent-field collaborator** (e.g., wet-lab partner for a stats-heavy report; PI for a methods-heavy report). Acronym budget 4 / page; sentence-length intuition lead-ins for load-bearing methodological choices; gloss most non-trivial terms on first use per section. **Default if the user names a collaborator type without a tier letter.**
   - **Tier C — In-field PI / close collaborator.** Acronym budget 6 / page; intuition lead-ins only for novel or coined concepts; standard terms (ARI, PC1, BIC, RNA-seq) need no per-section regloss.
5. **Standalone vs. addendum.** Default **standalone**: the draft must be readable without prior reports as a dependency. If the user opts in to **addendum**, ask which documents are the referenced base, and name them upfront in the introduction. Standalone is the default because every report that depended on "see §6.6.E" trips a future reader who didn't read §6.6.E.
6. **Report shape.** Default **overview + supplement**. Options:
   - **Overview** — main text only, 2–5 pages, no supplement. Use when the audience needs the headline and methods sketch but not the exhaustive table.
   - **Comprehensive** — single document with full methods, tables, and appendices inline. Use when the report *is* the artifact and no separate supplement is expected.
   - **Overview + supplement / appendix** (DEFAULT) — main text is the overview a collaborator could read in 10 minutes; supplement carries methods detail, worked examples for failure modes, and exhaustive tables.
7. **Results section style.** Default **narrative**: each result is prose, with a self-explanatory subsection title that states the finding. The alternative is **structured**: each result carries explicit `\paragraph{Question}` / `\paragraph{Findings}` / `\paragraph{Interpretation}` headers, paying for predictable skimmability with visual heaviness. Narrative is the default because it reads more like the final paper; structured pays off for long results sections (≥ 5 sub-results) or when readers want to land on a specific question quickly.

The shape and style choices drive the template selection in Phase 1 and the main-vs-supplement designation in Phase 0.75. They are not retrofitted after drafting.

Persist the answers as a small YAML artifact at `analysis/[name]/reports/.planning-brief.yaml`. All later phases read from it. The file is not shown to the user during normal flow; it is reproducible scaffolding.

---

## Phase 0.5 — Memory consultation (INTERNAL)

Read `.living/` and emit a small structured cheatsheet of names, terms, decisions already made, and previously-flagged failure modes for this analysis. Write it to `analysis/[name]/reports/.memory-cheatsheet.md`. The draft step in Phase 2 consumes it.

Files to read:

- `.living/learnings.md` — gotchas and surprises that may name domain terms, collaborator preferences, or prior corrections.
- `.living/decisions.md` — analytical choices already documented; the draft should not relitigate them.
- `.living/outputs/reviews/*.md` (if present) — most-recent reviews of this analysis. Previously-flagged style and provenance issues become a probe for the draft.
- `.living/conventions/` — installed convention packs (this one and any siblings) for project-specific phrasing rules.
- Any project-specific glossary (e.g., a `.living/glossary.md` if it exists; otherwise infer from `decisions.md`).

The cheatsheet has these sections:

```markdown
# Memory cheatsheet for <analysis-name>

## Collaborator names (use the spelling exactly)
- ...

## Domain terms and their canonical phrasings
- ...

## Decisions already made (do not relitigate)
- ...

## Previously-flagged failure modes (re-check for these in this draft)
- ...

## User preferences (acronyms, units, formatting, ...)
- ...
```

If any section is empty, write `(none recorded)` so the absence is legible.

Catches: collaborator name spellings (e.g., "Devereux, not Devereaux"); domain term canonicalization ("ontogeny, not ontology"); decisions documented and now liable to be re-explained; prior-flagged failure modes that should be checked again before the sub-agent reviewers see the draft.

---

## Phase 0.75 — Section outline + main/supplement designation (INTERNAL)

Sketch the document section-by-section before any drafting. The skill writes this artifact for itself; it is not reviewed by the user.

For each planned section, record:

- **Section title** (one line).
- **1–3 key points** the section will make (one line each). If a section is making more than three load-bearing points, split it.
- **Main text vs. supplement / appendix** — which deliverable it lives in, driven by the Phase-0 report-shape choice.
- **Required figures / tables** (by file path under `outputs/`).
- **Dependencies on other sections** — if Methods §2.3 must precede Results §3.1, note it.

Write the artifact to `analysis/[name]/reports/.section-outline.md`. Markdown is fine; the format is internal.

This phase catches "the discussion section is making four different arguments" *before* the discussion has been written, when revision is cheap. It also fixes section ordering so the draft step doesn't impose ordering by what was easiest to write first.

For **overview** shape: everything in main text; no supplement.
For **comprehensive** shape: everything in main text; appendix only if explicitly justified by Phase 0.
For **overview + supplement** (DEFAULT): main text holds the headline plus one healthy worked example per analysis type (see Phase 3); supplement holds methods detail, failure-mode worked examples, and exhaustive tables.

---

## Phase 1 — Source-of-truth manifest (INTERNAL)

Build a JSON manifest of every concrete artifact the draft will contain. The Phase-2 draft step is constrained to source numbers and terms from this manifest — that is what makes blind numerical re-verification (Phase 6) catch label-vs-value bugs and what makes `scitexlintr` (Phase 7) catch drift at lint time.

**Read analysis-side fragments first.** Each contributing analysis registers reportable values with the `register_value` helper (`skills/core/scripts/register_value.py`), which writes mechanical fields (`value`, `provenance`, `computed_at`) into `analysis/<name>/outputs/numbers.json`. Phase 1 collects every fragment the report sources from and merges them into `numbers[*]`.

**Rename `key` → `id` during the merge.** Each fragment entry carries a `key` field (`{"key": "exact_accuracy_test", "value": ...}`), but the merged manifest entry must name that field `id` (`{"id": "exact_accuracy_test", "value": ...}`). This is not cosmetic: `render_report_values_tex.py` reads `entry.get("id")` to mint each macro, so a fragment entry copied verbatim with its `key` field intact emits **no** `\SciVal` macro for that value and silently breaks drift detection. The value string is unchanged — only the field name changes (`key` → `id`). The `value`, `provenance`, and `computed_at` fields carry over untouched.

The agent then *enriches* each entry with the framing-aware fields the analysis cannot know:

- `label_canonical` — the canonical phrasing the prose must use
- `label_aliases_forbidden` — phrasings that would mislead in this report's framing
- `appears_in_sections` — where this value will be cited
- `overloaded_warning` (rare) — when a value's name shadows an established literature term

For the ordinary fragment-merge path, the mechanical fields (`value`, `provenance`, `computed_at`) come from the fragment and must not be edited; the framing fields are this phase's contribution. Legacy exceptions are called out below and must be marked explicitly.

**Optional display fields (number formatting).** A `numbers[*]` entry may also carry three optional fields that control only how the value is *rendered* in prose — never what it *is*:

- `unit` — currently supports `"percent"`. When set, the generated macro is derived from `value`: the stored fraction `0.978` renders as `97.8\%`, so prose reads "97.8% of evidenced claims" instead of the stiff "a fraction 0.978." Use it for genuine fractions-of-a-whole (not for score means, correlations, or other decimals that are not percentages).
- `precision` — decimal places for the derived display (default 1; `precision: 0` gives `98\%`, `precision: 2` gives `97.80\%`).
- `display` — a free-text, LaTeX-ready override emitted verbatim, for the rare value no `unit` can derive (e.g. a fold-change `3.2$\times$`). Reserve it for genuine one-offs: a hand-typed `display` *can* drift from `value`, whereas a `unit`-derived display cannot.

In the ordinary path, the `value` stays the canonical number and is never edited — it remains the Phase-6 / scitexlintr anchor. For `unit`, the displayed string is *derived* from `value`, so the readable number cannot silently disagree with the verified one. Phase 6 re-checks this faithfulness for any entry carrying `unit`/`display`.

For `unit: "percent"`, the `\SciVal` snapshot in the `.tex` source is still the stored fraction, not the rendered percent. A manifest entry with `"value": 0.9653`, `"unit": "percent"`, and `"precision": 1` renders as `96.5\%`, but the draft uses `\SciVal{\FracDated}{0.9653}`. Writing `\SciVal{\FracDated}{96.5\%}` trips `snapshot-mismatch` because the snapshot no longer matches the manifest value.

If a value the draft needs has no fragment entry, do not type it into the manifest — return to the analysis, add a `register_value` call, re-run the relevant script, and re-enter Phase 1 with the updated fragment. (The framing fields in this paragraph and the next read "the analysis cannot know," but everything mechanical the report quotes still has to come from the code that produced it — see the code-grounding instruction below.) The default exception is *legacy* analyses that pre-date `register_value`: the agent may hand-author an entry by reading the CSV the value comes from, and the entry is flagged in the manifest's `_provenance: legacy` field so Phase 6 still verifies it against the on-disk file.

A second legacy exception covers analyses that have on-disk values or `numbers.json` fragments, but those values were stored at full computational precision before the report-value convention stabilized. If the report only claims a rounded display-precision value, the agent may hand-author a rounded manifest entry, point `provenance` at the exact on-disk file/key that contains the full-precision value, and set `_provenance: "legacy_on_disk"` on that entry. Phase 6 must then verify the rounded manifest value against the cited on-disk source and the stated precision. Do not use this path to override a fresh `register_value` fragment in an active analysis; fix the analysis-side registration instead.

Do not register collision-prone constants just because they are number-like. Bare `0`, `1`, `1.0`, common publication years (`2020`, `2021`), and structural labels (`top-100`, `top-25`) cause `raw-generated-value` to fire on every incidental occurrence in prose. Register load-bearing results and thresholds; leave narrative constants unregistered when they are not computed outputs, and phrase them in words where practical ("a lift of one", "zero hub papers", "claims reaching degree 100").

`terms[*]`, `figures[*]`, and `worked_examples[*]` are still hand-authored from the analysis outputs at this phase — those carry framing information (canonical phrasings, sha256 fingerprints, row-level traces) that the analysis does not produce.

**Ground load-bearing claims in the code, not the documentation.** The most expensive failure this skill does *not* yet catch is the stale-doc bug: a `README`, `CLAUDE.md`, `specification.md`, or a docstring describes the pipeline one way, the code has since drifted, and the draft inherits the doc's wrong claim. When that happens the manifest becomes a faithful recorder of the wrong consensus — every later phase cross-checks the prose against the manifest, but nothing checks the manifest against what the code actually does. So for every **load-bearing definitional, structural, or enumeration claim** the report will make — the set of categories in an enum, the number of stages in a pipeline, the columns a function emits, the order of a precedence chain, the definition of a coined metric (for instance, a status field documented as having three allowed values that the code actually emits with five) — trace the claim to the code that implements it and source the manifest entry from the code, treating any prose documentation as a lead to verify rather than a source of truth. Read the function that produces the value, not the doc that describes it. Where the implementing file and line are known, record them in the entry's `computed_at` (for `numbers[*]`) or in a `grounded_in` note (for `terms[*]`) so Phase 6 can re-check against the same code. If a doc and the code disagree, the code wins and the doc is flagged for a fix (Phase 6 emits the cross-document patch). This is the Phase-1 half of code-grounding; Phase 6 re-verifies a sample of these claims against the code with fresh eyes.

A fully-populated example lives at `references/manifest-example.json` — read it once before generating the manifest for a new analysis. The example is drawn from a small clone-recovery / differential-expression report and covers the common shapes: a primary metric with bootstrap CIs modelled as separate `*_ci_low` / `*_ci_high` scalar entries (so each bound gets its own macro), an adjacent metric that exists in the same CSV but is *not* the primary (so the manifest carries both with distinct `label_aliases_forbidden` lists), a coined statistic with an `overloaded_warning`, a term whose role-name ("validation panel") could mislead a skim reader, and a figure entry with `sha256` for the Phase-6 freshness check.

Manifest schema (write to `analysis/[name]/reports/.manifest.json`):

```json
{
  "policies": {
    "audience_tier": "B",
    "acronym_budget_per_page": 4,
    "acronym_strictness": "moderate",
    "results_structure": "narrative",
    "intuition_leadin_default_form": "sentence",
    "shape": "overview-supplement"
  },
  "numbers": [
    {
      "id": "exact_accuracy_test",
      "value": 0.280,
      "label_canonical": "exact accuracy",
      "label_aliases_forbidden": ["accuracy", "support-weighted F1", "weighted F1"],
      "provenance": "outputs/tables/test_metrics.csv:row=overall,col=exact_accuracy",
      "computed_at": "scripts/03_evaluate.R:L142"
    },
    {
      "id": "exact_accuracy_test_ci_low",
      "value": 0.241,
      "label_canonical": "lower bound of the 95% bootstrap CI on exact accuracy",
      "label_aliases_forbidden": ["standard error", "margin of error"],
      "provenance": "outputs/tables/test_metrics.csv:row=overall,col=exact_accuracy_ci_low",
      "computed_at": "scripts/03_evaluate.R:L142",
      "_note": "Bootstrap n=1000, seed=42. CI bounds are their own scalar entries (paired _ci_low / _ci_high), not a nested 'uncertainty' object: register_value emits scalars only, and a bound bundled in a nested object gets no \\SciVal macro and so escapes scitexlintr."
    },
    {
      "id": "exact_accuracy_test_ci_high",
      "value": 0.322,
      "label_canonical": "upper bound of the 95% bootstrap CI on exact accuracy",
      "label_aliases_forbidden": ["standard error", "margin of error"],
      "provenance": "outputs/tables/test_metrics.csv:row=overall,col=exact_accuracy_ci_high",
      "computed_at": "scripts/03_evaluate.R:L142"
    }
  ],
  "terms": [
    {
      "id": "BCLRT",
      "expansion": "branch-coherency log-likelihood ratio test",
      "plain_english": "a per-cell logistic regression with two covariates: log(1 + total panel coverage) and a flat-false-positive artifact-load covariate",
      "first_use_section": "methods.technical_detail",
      "overloaded_warning": "This is NOT a standard log-likelihood-ratio test in the Wilks sense — dLL = LL_branch - LL_null, without the factor of 2. Threshold 10 corresponds to Wilks 20."
    }
  ],
  "figures": [
    {
      "id": "volcano_main",
      "path": "outputs/figures/volcano_de.pdf",
      "sha256": "...",
      "caption_seed": "Volcano plot of differential expression between treatment and control."
    }
  ],
  "worked_examples": [
    {
      "id": "snp_score_c017",
      "analysis_type": "sparse_axis_module_call",
      "subject_id": "c_017",
      "subject_kind": "cell",
      "provenance": "outputs/tables/module_call_per_cell.csv:row=c_017",
      "computed_at": "scripts/06_module_call.R:L88",
      "rows": [
        {"snp_id": "X17.76565019G>A", "role": "seed", "N": 5, "Y": 3, "alt_fraction": 0.60, "covered": true, "supports_target": true},
        {"snp_id": "X2.197498780A>G",  "role": "partner", "N": 2, "Y": 1, "alt_fraction": 0.50, "covered": true, "supports_target": true},
        {"snp_id": "X1.109675067C>T",  "role": "partner", "N": 0, "Y": 0, "alt_fraction": null, "covered": false, "supports_target": null}
      ],
      "aggregate": {"target_support_fraction": 0.75, "call": "high-support"},
      "appears_in_sections": ["results.headline"],
      "_note": "Every value in the row-level table must trace to scripts/06_module_call.R when executed against the row of the provenance CSV."
    }
  ]
}
```

Every number that will appear in prose has an entry. Every coined term has a plain-English rendering. Every figure has a hash so Phase 6 can detect stale assets. Every worked example carries row-level provenance so Phase 6 can verify the example wasn't confabulated.

The `policies` block is derived from the Phase-0 planning brief and is the only project-context field the sub-agent reviewers (Phases 4–6) are allowed to read — they read the manifest, not the brief itself. The mapping is:

| Phase-0 audience tier | acronym_budget_per_page | acronym_strictness | intuition_leadin_default_form |
|---|---:|---|---|
| A (lay / out-of-field) | 2 | strict — gloss every term per section | paragraph |
| B (adjacent-field collaborator) — DEFAULT | 4 | moderate — gloss non-trivial terms per section | sentence |
| C (in-field PI / close collaborator) | 6 | loose — gloss only coined terms, no per-section regloss for standard ones | sentence-or-none |

`results_structure` is one of `narrative` (default) or `structured` (with explicit Question / Findings / Interpretation paragraph headers in each result). `shape` mirrors Phase 0's report-shape choice.

Constraints the manifest must satisfy before Phase 2 may proceed:

- Every `value` has a `provenance` pointing to a file path with row/col or line number that a reader could check.
- Every `label_canonical` matches the metric definition in the source file. If `support-weighted F1` and `exact accuracy` are both in the CSV, both have separate entries with distinct labels.
- Every coined term has either a plain-English rendering or an explicit note that no rendering is needed. If a term shadows an established literature term, the `overloaded_warning` field is mandatory.
- Every worked example has a `subject_id` (the concrete row, cell, or capsule the example traces) and `provenance` (the file/row that holds the raw inputs). The `rows` table values must match the provenance file when executed; Phase 6 verifies this.

The draft step is not allowed to introduce a number, term, or worked-example value that is not in the manifest. If a draft pass discovers a new artifact it needs, the flow returns to Phase 1, adds the entry, and re-enters Phase 2.

---

## Phase 2 — Draft (INTERNAL)

Fill the template chosen in Phase 0 (overview / comprehensive / overview+supplement). Source every numeric token from `numbers[*].value` and every coined term from `terms[*]`. Read `references/section-guide.md` for the per-section craft.

**Use `\SciVal` and `\SciText` wrappers for every reportable value.** Every quoted number from `numbers[*]` goes in prose as `\SciVal{\Macro}{snapshot}` (numeric values) or `\SciText{\Macro}{snapshot}` (text values like contrast phrases). The macro name follows the id→macro transform documented in scitexlintr: `n_samples` → `\NSamples`, `fdr_threshold` → `\FDRThreshold`. The snapshot is the current value, shown in the source for review. The LaTeX renderer prints only the macro (so the PDF is always fresh); `scitexlintr` (Phase 7) verifies the snapshot equals the manifest value.

Add `\input{build/report_values.tex}` to the preamble — Phase 7 generates that file from `.manifest.json`. The generated file provides the wrapper definitions and one `\newcommand` per manifest entry.

Examples:

```latex
We analyzed \SciVal{\NSamples}{48} cells passing QC.
At \SciVal{\FDRThreshold}{0.05}, \SciVal{\NDEGenesFDRZeroZeroFive}{317} genes were differentially expressed.
For the contrast \SciText{\ContrastPhrase}{treated versus control}, ...

% Manifest value is 0.9653 with unit:"percent", precision:1.
% The PDF renders 96.5\%, but the checked snapshot remains the stored fraction.
\SciVal{\FracDated}{0.9653} of dated claims supported the later-year conclusion.
```

Do not type raw values into prose without a wrapper — `scitexlintr`'s `raw-generated-value` rule will flag any literal that matches a manifest entry. Do not use bare `\Macro{}` without a `\SciVal`/`\SciText` wrapper — `scitexlintr`'s `bare-generated-macro` rule will flag it (the macro is fresh, but the source is unreviewable).

Not every numeric-looking token belongs in `numbers[*]`. Publication years, numbered structural labels, table row labels, and prose-only constants should be registered only when they are load-bearing computed results. Otherwise, prefer words where this improves lintability and readability ("first/second/third" rather than `(1)(2)(3)`, "claims reaching degree 100" rather than a bare `top-100` label when the exact token is not important).

Drafting order:

1. Data-driven sections first (Problem Statement, Methods, Results). These are bound by the manifest.
2. Interpretive sections last (Conclusions, Abstract, Next Steps). Derive these from what was written in Results — never pre-write them, never template-fill from the planning brief alone.

**Voice and readability.** All the rigor below (manifest-sourced numbers, finding-form titles, acronym discipline, denominators) is about *correctness*, not *register*. Do not let it flatten the prose into a methods section. The Abstract, Problem Statement, and the opening of each Results unit should read like the introduction of a good paper a person wrote — lead with the scientific story and why it matters before the machinery, motivate the question, and carry the reader. Reserve the dense, methods-paper register for the Methods technical detail and the supplement, where it belongs. A report that is exhaustively verified but reads as an archive of facts has failed the reader who needed to understand *why* before *how*. See `references/section-guide.md` (Abstract, Problem Statement, the "Read it aloud" craft block, and the readability note) for the craft.

**Drafting model.** Draft on the orchestrating model. A head-to-head test of delegating the Phase-2 draft to a separate, ostensibly more "natural" writer model did not improve prose naturalness — the section-guide craft (notably the "Read it aloud" block) does more for the prose than the model choice, and a weaker drafter is more likely to introduce an unsourced number or a confabulated expansion. The blind Phase-6 re-verify guards correctness regardless of who drafts, so the drafter is chosen for writing quality, and in testing the orchestrating model was at least as good.

**Write in US English.** Default to US spelling and conventions throughout the report (`analyze`, `normalization`, `behavior`, `signaling`, `center`, `artifact`) unless the planning brief explicitly asks otherwise. The convention prose you are reading is itself US English; match it. Do not drift into British forms (`analyse`, `normalisation`, `behaviour`, `signalling`, `centre`, `artefact`) — that drift is a recurring failure mode of this skill.

While drafting:

- **Integrate the figures the analysis already produced.** Look in `outputs/figures/` (and any figure directory the analysis uses) and pull the load-bearing plots into the report with `\includegraphics`, a self-contained caption, and a `\ref{}` from the prose — do not default to a figure-free methods-archive. A report that describes a result the analysis has a figure for, but omits the figure, is harder to read than it needs to be; figures are the in-house strength this skill should match. Register each figure you include in `manifest.figures[*]` with its `sha256` so Phase 6 can check freshness and `scitexlintr`'s `unfingerprinted-figure` rule is satisfied. Only the supplement-heavy or genuinely figure-free analysis should ship zero figures, and that should be a deliberate choice, not a default.

- Every cross-document reference must either inline the relevant fact (≤ 1 sentence) or be deleted. The Phase-0 standalone default forbids "as discussed in the previous report" leaning. If Phase 0 picked **addendum**, the named base documents are introduced upfront and "see §X" references to them are acceptable in body text, but the abstract still stands alone.
- Apply the manifest's `policies` block. `acronym_budget_per_page` and `acronym_strictness` set how aggressively to spell-out and re-gloss. `intuition_leadin_default_form` sets whether load-bearing concepts get a paragraph or a sentence of intuition before the formal definition. `results_structure` decides whether Results sections use prose subsection titles (narrative) or `\paragraph{Question}` / `\paragraph{Findings}` / `\paragraph{Interpretation}` headers (structured). These are configuration, not exhortation — the policy is what gets enforced by the sub-agent reviewers, so the draft should follow it on first pass rather than wait for findings.
- In every shape, 0 acronyms in section titles, the abstract, and figure captions, regardless of audience tier. Tier C only loosens what happens *inside* sections.
- Every subsection title under Results states a finding, not a topic. "Evidence-first cell calling" is a topic; "Evidence-first calling trades recall for precision in a transparent way" is a finding. Phase 5 enforces this and it's cheap to get right the first time.

---

## Phase 3 — Worked-example gate (INTERNAL)

For each *new aggregation analysis type* introduced in the draft, the draft must contain one fully-traced concrete example: pick a single SNP / cell / capsule / row and show its raw inputs, the per-row metric calculation, and how it contributes to the aggregate. The example does the explanatory work once — subsequent claims using the same analysis type don't need their own example.

**Crucial:** the values inside the worked-example table are sourced from `manifest.worked_examples[*].rows[*]`, not invented to look plausible. Phase 1 already required a `subject_id` and a row-level `provenance` pointer for every worked example; Phase 3 is the gate that confirms the table in the draft cites the same `subject_id` and reproduces the same `rows` values. If the analysis hasn't produced the per-row tracer file yet, run the relevant script (or extend it) to write one before Phase 2 — confabulated worked examples pass the visual sniff test but fail the Phase-6 cross-check, and they teach the reader the wrong calculation if no one ever notices.

Format preference: small inline figures and sparkline-style mini-tables over big monolithic figures. A 5-row inline table with raw N, Y, score, rank, contribution is often clearer than a full plot. Tufte sparklines are a strong reference shape.

**Failure-mode worked examples** (cases where the analysis breaks) also exist, but they live in the supplement / appendix rather than the main body — the main body shows one healthy example per analysis type, the supplement shows the contrasting failure case.

Phase 3 is a gate: scan the draft for each new aggregation analysis type and confirm the worked example is present. If a section talks about "F1 = 0.48" or "Spearman ρ = 0.871" or "ARI 0.14 → 0.34" and the reader has not yet seen what one row of the underlying calculation looks like, return to Phase 2 and add the worked example before proceeding.

This phase also catches the inverse failure (S9): single examples dressed as proof of an invariant. "Confirmed" / "verified" / "established" claims need either a tested invariant across all relevant inputs or explicit "for this one case" hedging. The worked-example gate is the right place to spot it because it is already paying attention to the relationship between concrete instances and general claims.

---

## Phase 4 — Plain-English / glossary lint (SUB-AGENT)

Dispatch a sub-agent with **zero project context**. It reads the draft `.tex` files and the Phase-1 manifest (terms section only). It does not read the analysis directory, `.living/`, or the planning brief.

The sub-agent answers:

- For each acronym used in the draft: was it defined on first use *per section* (not just per document)? Is the acronym budget per page (≤ 4) respected? Are there any acronyms in section titles, abstracts, or figure captions?
- For each sentence that reads as jargon-dense (heuristic: ≥ 3 unexplained noun-phrases per sentence): which terms are unexplained, and is a plain-English rendering present in the same paragraph?
- For each load-bearing methodological choice in Methods (every term marked as load-bearing in `terms[*]`): is the intuitive explanation present *before* the technical statement? (Sentence-length or paragraph-length is a judgment call; the lint flags only when no intuitive lead-in exists at all.)
- For each coined term with an `overloaded_warning` in the manifest: is the warning present at first use? Does it specifically name how it differs from the standard term (missing factor of 2, not a p-value, no chi-square null, etc.)?

Output: a list of findings with file:line, the quoted sentence, the issue, and a one-line fix. The flow returns to Phase 2 with the findings and the draft is patched. Loop until the lint passes.

The full sub-agent prompt is in `references/phase-prompts.md`.

---

## Phase 5 — Framing critique (SUB-AGENT)

Dispatch a sub-agent that reads only the `.tex` files and the `references/section-guide.md` craft notes. It does not read the planning brief, the manifest, or the analysis directory.

The sub-agent answers:

- **Standalone test.** Reading only the title + abstract + section headers + figure captions, does the story require body-prose recovery? If the abstract says "held-aside validation markers" but the body explains they are not held-aside, the skim-reader takes home the unqualified claim.
- **Subsection title quality.** For every subsection title under Results, does the title state a *finding* (verb + outcome) or a *topic* (bare noun phrase)? Topics get flagged. *"Evidence-first cell calling"* is a topic; *"Evidence-first calling trades recall for precision in a transparent way"* is a finding. Methods, Provenance, and References subsection titles are allowed to be topics.
- **Baseline present.** Does the draft state, in the body, what it is being compared against? Is the headline finding framed in terms of that baseline?
- **Changelog framing.** Does any prose read as "what we fixed" rather than "what we found"? Drafts that recap the path the author walked tend to read as changelogs. Fix: rewrite as though the analysis was done the right way from the start.
- **Read-aloud / voice.** Read the load-bearing prose — the abstract, the problem statement, and the opening of each Results unit — as if saying it aloud to a colleague. Flag three things: (a) sentences that read as a *verified-facts archive* rather than an explanation a person would give — the rigor machinery (manifest-sourced numbers, finding-form titles, denominators) pushes toward this flat register and no other phase catches it; (b) any term that, read aloud, a reader at the report's audience tier (the orchestrator passes `policies.audience_tier`; default Tier B) could not parse without the page in front of them — this catches bare jargon in *running prose* (e.g. "volume proxy") that the title and acronym checks miss; (c) clunky number phrasings the `unit`/`display` field should fix ("a fraction 0.978" should read "97.8%"). These are framing findings in the standard Phase-5 format; Phase 2 patches them.
- **Cross-document references.** Does the draft lean on "as discussed in the previous report" / "we previously showed X" without inlining? If standalone, every such reference is a finding. If addendum, the referenced documents must be named upfront.
- **Report-shape consistency.** Does the draft match the Phase-0 shape choice? (An "overview" draft with a 12-page methods section is not an overview.)
- **Caveat prominence.** For every headline number, where does the strongest caveat live? Caveats at heading level less prominent than the claim are hiding.
- **Single-example-as-proof.** Are any "confirmed" / "verified" / "established" claims grounded in one example without hedging?

Output: a list of findings with file:line, the quoted passage, the failed test, and a one-line fix. Return to Phase 2 and patch. Loop until framing passes.

The full sub-agent prompt is in `references/phase-prompts.md`.

---

## Phase 6 — Blind numerical re-verify (SUB-AGENT)

Dispatch a sub-agent that reads the draft `.tex` files, the Phase-1 manifest, the compiled PDF, and the narrowly-scoped on-disk artifacts the checks below depend on. The "blind" in this phase's name refers to the planning brief, the memory cheatsheet, and `.living/` — *not* to the analysis directory at large. Phase 6 is fundamentally a verification phase: it must be able to read the on-disk CSV that a manifest entry claims to come from, the figure file that a `\includegraphics` references, and the cross-document drift target files. The blind read is preserved at the framing level (the sub-agent doesn't know the analyst's intent) while the verification has enough access to actually verify. The full input list and the explicit prohibition list live in `references/phase-prompts.md`.

It extracts every numeric token in the prose plus its surrounding label, and cross-checks the manifest.

Output is structured as **provenance** vs **style** (mirroring the synthesis split of `mycelium:review`):

**Provenance findings** (factual problems):

- For each numeric token in the prose: is it in the manifest? Does its surrounding label match `label_canonical` (and not any `label_aliases_forbidden`)? If `0.482` appears in prose adjacent to the word "accuracy", but the manifest says it is `support-weighted F1`, that is a label-vs-value mismatch.
- Are there numbers in the prose that have no manifest entry (unsourced numbers)? These often turn out to be from a one-off check during the conversation, not a stored test.
- **Display faithfulness (derived-number check).** For each `numbers[*]` entry carrying a `unit` or `display` field, read the generated macro from `build/report_values.tex` (the renderer output that reaches the PDF — not the draft's `\SciVal` snapshot, which Phase 7 reconciles separately and later) and confirm it is a faithful function of the canonical `value`: a `unit:"percent"` macro must equal `value*100` at the entry's `precision` (e.g. `0.978 → 97.8\%`), and a free-text `display` must not state a number that contradicts `value`. A macro that does not derive from `value` is the one way the readable number can drift from the verified one — flag it as a provenance finding (major when it changes the number a reader takes home).
- For every figure: does the file at `figures[*].path` still have the manifest's `sha256`? If a figure was regenerated mid-draft, the file may be newer than the prose claims.
- **Worked-example value verification.** For each table in the draft that the Phase-3 gate marked as a worked example, locate the matching `manifest.worked_examples[*]` entry by `subject_id` (mentioned in the caption or surrounding text). Verify that every row in the table matches the manifest's `rows[*]` — same `snp_id`/`role`, same `N` and `Y`, same `alt_fraction`, same `covered`/`supports_target`. Worked examples with no manifest entry are *unsourced worked examples* and get flagged as major; mismatching rows are *fabricated worked-example values* and get flagged as major. This catches confabulated plausible-looking values that pass the visual sniff test.
- **Provenance section completeness.** Read the Provenance section's script list. Run `ls scripts/` (or whichever directory the analysis uses, inferable from manifest provenance pointers). Every `run_*.R` / `run_*.py` / `evaluate_*.R` / `make_figures.R` etc. in the analysis directory should appear in the Provenance list with a one-line description. Scripts that are clearly diagnostic-only (`*_test.R`, `*_dev.R`) can be omitted; the heuristic is that anything which writes into `outputs/` belongs in Provenance. Flag missing scripts as minor provenance findings.
- **Code-grounding of load-bearing claims (doc-vs-code drift).** For a sample of the report's load-bearing *definitional, structural, and enumeration* claims — the set of categories in an enum, the number of stages/streams/tiers/grades, the columns a function emits, the order of a precedence chain, the definition of a coined metric — open the code that implements the claim and verify the claim against the code's actual behavior, not against any doc that describes it. This is the one check in Phase 6 that reads the code as source of truth rather than the manifest: the manifest can faithfully record a claim that every doc agrees on but the code contradicts (the "wrong consensus" failure), and only reading the implementing function catches it. Where the manifest entry carries a `computed_at` or `grounded_in` pointer, start there; otherwise locate the producing function. A claim that the code refutes is a **code-grounding finding** (severity: major when it changes a reported value or a definition; minor when it is a stale label) — emit both the draft fix and, via the cross-document drift mechanism, a patch for the doc that drifted.

**Cross-document drift findings** (also provenance):

- For each unique number in the prose, `grep` other documents that this report depends on: `conclusions.md`, `specification.md`, the analysis `UPPER_SNAKE_CASE.md`, and any prior reports under `analysis/[name]/reports/`. If the same number appears in those files with a stale value, emit a patch suggestion.
- For each domain term and each collaborator name: `grep` the same set of files. Inconsistencies become findings.

**Style findings** (clarity problems found in the verification pass):

- Bare counts and percentages without their denominator within one sentence.
- For cross-universe comparisons: is the universe-eligible-restricted denominator also reported?
- Lying comments / lying captions: does any caption describe a filter or threshold that the underlying figure-generation code refutes?

Return findings in the two-section structure. Return to Phase 2 (provenance findings are patched into the draft; cross-document drift findings produce patches for the other files in the same pass). Loop until both sections pass.

The full sub-agent prompt is in `references/phase-prompts.md`.

---

## Phase 7 — Recompile + re-run (INTERNAL)

After the sub-agents pass:

1. **Regenerate the LaTeX macros from the manifest.** Run `python skills/core/scripts/render_report_values_tex.py analysis/[name]/reports/.manifest.json`. This writes `build/report_values.tex` with one `\newcommand` per `numbers[*]` entry plus the `\SciVal` / `\SciText` wrappers. Re-run this step every time `.manifest.json` changes.
2. **Run scitexlintr on the draft.** `scitexlintr analysis/[name]/reports/[name]-report.tex --manifest=analysis/[name]/reports/.manifest.json`. The recompile gate **must not proceed** if error findings remain after waivers. Warnings are advisory by default: fix the ones that point to real drift risk, document intentional leftovers in the compile log, and promote them to blocking errors only when the team has stabilized that rule for the report type. Auto-fix snapshot drift with `--write` (typically interactively, not in the gate):
   - `snapshot-mismatch` is the load-bearing check — the snapshot in `\SciVal{\Macro}{...}` must equal the manifest value. Use `--write` to rewrite stale snapshots; the diff is small and reviewable.
   - `raw-generated-value`, `unwrapped-threshold`, and `forbidden-alias` are error checks; fix the prose to use the appropriate wrapper and label.
   - `unfingerprinted-figure` errors when `\includegraphics{...}` points at a path not in `manifest.figures[*]` or whose `sha256` has changed — re-fingerprint the manifest entry after regenerating the figure.
   - `bare-generated-macro` is a warning by default: the PDF is fresh, but the source is not reviewable. Prefer fixing it to `\SciVal` / `\SciText`; waive only with a specific reason.
   - `unsourced-numeric-token` and `handwritten-numeric-claim` are warnings in the default gate. They are useful prompts, not a requirement to waive every narrative year in a number-dense report.
   - For pervasive narrative numbers, register load-bearing results; phrase constants and labels in words where possible; and use clustered waiver comments for genuinely narrative years or supplement-table cells that are sourced from a named file or caption. `tabular` cells count as prose for the linter.
   - Waive any genuinely intentional case with `% ANALYSIS_OK[rule-code]: explanation` on or up to four lines above the offending line. The waiver window is four lines forward, and each waiver comment names exactly one rule code; if two rules apply, write two comments.
3. Recompile the report with two `pdflatex` passes (plus `bibtex` if citations are used).
4. If any Phase-6 finding triggered a code rerun (e.g., a figure had to be regenerated, a number was wrong because the script was outdated), re-run the relevant script, re-collect the analysis-side fragment, and re-run Phase 1's merge before recompiling.
5. Measure the **main-text page count** (pages before `\appendix`) from the compiled PDF and compare to the shape budget:
   - **Overview**: target 2–5 pages. Flag if main text > 6 pages.
   - **Overview + supplement** (DEFAULT): target main text ≤ a 10-minute read ≈ ≤ 12 pages. Flag if main text > 14 pages.
   - **Comprehensive**: no upper bound. Flag if main text < 5 pages (the shape was probably wrong; comprehensive reports rarely fit in fewer pages).
   A flag does not block compilation, but it appears in the compile log as `shape_budget: flagged` with a one-line reason. The drafter is expected to either prune to fit or, with explicit acknowledgement in the log, accept the overrun.
6. Record in `analysis/[name]/reports/.compile-log.md`:
   - PDF SHA256
   - Compile timestamp
   - Main-text page count and shape-budget status (`within` / `flagged` with reason)
   - Sub-agent reviewer verdicts (each: PASS / loop count to convergence)
   - Whether code was re-run since the last manifest snapshot
   - `scitexlintr` exit code, error count after waivers (must be 0 for the gate to pass), and warning count/disposition

The compile log is the answer to "okay, so all of this is fixed now? and was code rerun in case things changed?" — that question lands on every report draft and the log makes the answer mechanical.

---

## Phase 8 — Headline-summary preview (USER-FACING, optional)

Before the user opens the PDF, surface a one-paragraph summary that contains the headline question (from Phase 0), the baseline of comparison, the primary metric value, and the single biggest caveat. This pre-empts the most common framing failures (changelog framing, wrong metric featured) by giving the user a chance to reject the framing before they invest in reading the PDF.

The preview is optional. Skip if the Phase-0 planning brief recorded a "skip-preview" preference, or if the report shape is **comprehensive** — in the comprehensive shape a tight one-paragraph summary tends to mislead by omission, and the user is expected to read the document end-to-end anyway.

---

## Quick reference — phase artifacts

| Phase | User-in-loop? | Artifact path |
|---|---|---|
| 0 — Planning brief | YES | `analysis/[name]/reports/.planning-brief.yaml` |
| 0.5 — Memory cheatsheet | no | `analysis/[name]/reports/.memory-cheatsheet.md` |
| 0.75 — Section outline | no | `analysis/[name]/reports/.section-outline.md` |
| 1 — Manifest | no | `analysis/[name]/reports/.manifest.json` |
| 2 — Draft | no | `analysis/[name]/reports/[name]-report.tex` |
| 3 — Worked-example gate | no | (in-place patches to draft) |
| 4 — Plain-English lint | no | `analysis/[name]/reports/.review-plain-english.yaml` |
| 5 — Framing critique | no | `analysis/[name]/reports/.review-framing.yaml` |
| 6 — Blind numerical re-verify | no | `analysis/[name]/reports/.review-numerical.yaml` |
| 7 — Recompile log | no | `analysis/[name]/reports/.compile-log.md` |
| 8 — Headline preview | optional | (in chat, not a file) |

Phases 0.5 through 7 emit dotfile-prefixed artifacts so they do not clutter the analysis directory and so a future reader can audit the trail without it looking like part of the published report.

---

## Compilation reference

```bash
cd analysis/[name]/reports/
pdflatex [name]-report.tex
pdflatex [name]-report.tex
```

If using BibTeX citations, insert `bibtex [name]-report` between the two passes. **Verify** the PDF renders correctly — check that all figures appear, cross-references resolve (no "??"), and the TOC is populated.

---

## Report structure

The section order described below applies to the **overview + supplement** default. The other shapes adapt:

- **Overview** template: same main-text sections; no supplement / appendix.
- **Comprehensive** template: same main-text sections; appendix inline; methods detail expanded.
- **Overview + supplement** template (DEFAULT): main text below, plus a supplement with methods detail, failure-mode worked examples, and exhaustive tables.

### 1. Title page
Title, subtitle (optional), author, date. The title should describe the finding or question, not just the method ("Treatment X upregulates inflammatory pathways" not "RNA-seq analysis").

### 2. Abstract
Accessible to a general technical audience. Lead with the biological or scientific question, not the method. State the key finding last. No undefined acronyms. 1–2 paragraphs, 150–250 words.

### 3. Table of contents
Auto-generated by LaTeX.

### 4. Problem statement
Frame the question in plain English. Define every concept a non-specialist would need to follow the rest of the report. Explain why the answer matters and what a good answer would look like. A reader outside the immediate field should be able to understand this section completely.

### 5. Methods
Three required subsections:

- **Definitions**: Precisely define every mathematical symbol, technical term, and domain-specific concept. For symbols: what it represents, units, range of values. For terms: a one-sentence definition a non-specialist could follow. For coined terms that shadow an established literature term: explicit "this is NOT the standard X" disclaimer at first use, with the specific way it differs.
- **Overview**: 2–3 sentence high-level summary anyone could follow.
- **Technical detail**: Datasets (with sizes), tools (with versions), parameters (with rationale for non-default choices), statistical methods, diagrams if available. For load-bearing methodological choices, lead with the intuitive explanation before the technical definition.

### 6. Results
Each result is a self-contained unit: (1) state the question being tested, (2) describe what you'd expect under competing hypotheses, (3) report findings with figures/tables, (4) conclude in relation to the question. Every new aggregation analysis type is accompanied by one worked example (Phase 3). All figures and tables must have self-contained captions and be cross-referenced in the text.

### 7. Conclusions
Map each conclusion back to the problem statement. State caveats explicitly ("This analysis does not address...", "A limitation is..."). Distinguish what the data shows from what it suggests. The strongest caveat for each headline number must live at a heading level at least as prominent as the claim.

### 8. Next steps *(optional)*
Only include if there are clear, actionable follow-up analyses. Omit entirely if there's nothing concrete to say.

### 9. Provenance
Auto-populated from project metadata:
- Script paths and descriptions
- Git commit hash of the analysis
- Analysis date range (from git log)
- Analyst name
- Report generated date
- Software versions (from `ENVIRONMENTS_INSTALLATIONS.md`)
- Manifest path (`.manifest.json`) and compile-log path (`.compile-log.md`) so any reader can audit how the report was built.

### 10. Supplement / appendix *(in overview+supplement and comprehensive shapes)*
Failure-mode worked examples, methods detail for the analysis types described in the main text, threshold sweeps, method comparisons, subsample stability, alternative visualisations, extended tables.

---

## LaTeX tips

These come up frequently in computational analysis reports:

- **Special characters**: Escape `_`, `%`, `&`, `#`, `$` in text mode (`\_`, `\%`, `\&`, `\#`, `\$`). Gene names with underscores are the most common source of compilation errors.
- **Gene names**: Use `\textit{TP53}` for gene names (italic by convention in most fields).
- **Tables**: Use `booktabs` (`\toprule`, `\midrule`, `\bottomrule`) — never vertical lines or `\hline`.
- **Figure placement**: Use `[H]` from the `float` package for exact placement when the figure must appear at a specific point in the narrative. Use `[htbp]` when flexible placement is acceptable.
- **Cross-references**: Every figure and table needs a `\label{}` immediately after `\caption{}`. Reference with `Figure~\ref{fig:name}` (the `~` prevents a line break before the number).
- **Long tables**: For tables spanning multiple pages, use `longtable` instead of `tabular`.
- **Code listings**: Use the `listings` or `minted` package for code snippets — never raw monospace.

---

## Cross-references inside this convention pack

- `references/section-guide.md` — per-section craft (the writing guidance the draft step consumes); includes the results-structure (narrative vs structured) decision and the audience-tier acronym ladder.
- `references/phase-prompts.md` — sub-agent prompts for Phases 4, 5, 6.
- `references/manifest-example.json` — fully-populated Phase-1 manifest example with `policies`, `worked_examples`, `numbers`, `terms`, and `figures` sections, to crib from when starting a new report.
- `qc-checklist.md` — provenance / style checklist used in Phase 7 and surfaced to the user via `.compile-log.md`.
- `assets/report-template-overview.tex` — overview shape.
- `assets/report-template-comprehensive.tex` — comprehensive shape.
- `assets/report-template-overview-supplement.tex` — default shape.

## Cross-references outside this convention pack

- `skills/core/references/report-values-guide.md` — analysis-side `register_value` helper that produces the `numbers[*]` fragments Phase 1 merges.
- `skills/core/scripts/register_value.py` — the helper itself.
- `skills/core/scripts/render_report_values_tex.py` — Phase 7 step 1; emits `build/report_values.tex` from `.manifest.json`.
- scitexlintr — Phase 7 step 2; verifies the draft's `\SciVal`/`\SciText` snapshots against the manifest. It is the separate report-lint package under `tex/scitexlintr`, not the analysis-code `scilintr` CLI. Source: https://github.com/arjunrajlaboratory/scilintr/tree/main/tex/scitexlintr.
