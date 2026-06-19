# Report QC Checklist

Run before finalizing any report PDF. The list is split into **provenance** (does the result match what was actually run?) and **style** (is the prose clear and standalone?). Neither is more important than the other — calling them out separately helps the reader see at a glance whether an issue is factual or about clarity.

Phase 4–6 sub-agents in the report-generator flow check most of these automatically. This file is the user-facing surface so a reader who only sees the QC list can still audit a finished report.

---

## Compilation

- [ ] **pdflatex** completes without errors (warnings are OK to review but not block on)
- [ ] **Two passes** run (TOC and cross-references resolved)
- [ ] **No "??"** in the rendered PDF (unresolved references)
- [ ] **All figures** render (no missing file errors)
- [ ] **BibTeX** pass run if citations are used
- [ ] `.compile-log.md` exists and records PDF SHA256, compile timestamp, and sub-agent reviewer verdicts
- [ ] **`build/report_values.tex` is fresh** — regenerated from `.manifest.json` in this compile cycle (Phase 7 step 1)
- [ ] **`scitexlintr` blocking errors are 0** after waivers (Phase 7 step 2) — no `snapshot-mismatch`, `raw-generated-value`, `unwrapped-threshold`, `forbidden-alias`, or `unfingerprinted-figure` error findings remain
- [ ] **`scitexlintr` warnings are reviewed** — `bare-generated-macro`, `unsourced-numeric-token`, and `handwritten-numeric-claim` warnings are fixed, waived with specific `% ANALYSIS_OK[rule-code]` comments, or listed in `.compile-log.md` as advisory narrative numbers

---

## Provenance (factual correctness)

These checks verify the prose matches the artifacts. Phase 6 (blind numerical re-verify) is responsible for most of these.

### Label–value alignment

- [ ] **Every numeric token in prose** has a manifest entry (`.manifest.json:numbers[*]`)
- [ ] **Every adjacent label** matches the manifest's `label_canonical` and not any `label_aliases_forbidden`. *Failure mode: "48% correctly classified" when 0.482 is support-weighted F1, not exact accuracy.*
- [ ] **No swapped numbers between adjacent paragraphs.** *Failure mode: "lamanno Δ = -0.014" written when the actual value is -0.023, swapped with the adjacent paragraph.*
- [ ] **Precision vs recall, accuracy vs F1, sensitivity vs specificity** — for any number where a sibling metric exists in the same table, the label matches the column it came from.

### Figure freshness

- [ ] **Every figure's file hash** matches the manifest's `sha256`. If a figure was regenerated mid-draft, the prose may now reference the wrong version.
- [ ] **Every figure** is referenced in the text by `\ref{}` — if it's not discussed, it shouldn't be there.
- [ ] **Captions** are self-contained and accurately describe what the figure shows. *Failure mode: caption describes a filter that the underlying figure-generation code does not apply.*

### Cross-document drift

- [ ] **Every unique number** in the prose has been grep'd in `conclusions.md`, `specification.md`, the analysis `UPPER_SNAKE_CASE.md`, and any prior reports under `analysis/[name]/reports/`. Mismatches have either been patched or explicitly justified.
- [ ] **Every domain term** and **every collaborator name** has been grep'd against the same files. Spellings are consistent.
- [ ] **STATUS.md / MANIFEST.md** (if the project uses them) describe the methods and direction this report represents. *Failure mode: collaborators reading STATUS.md will not know the newer methodological direction exists.*

### Lying-comment / lying-glossary

- [ ] **No comment, caption, or glossary entry** describes a filter / threshold / transformation that the immediately-adjacent code or data refutes. *A comment that contradicts the code is worse than no comment.*
- [ ] **Single-example-as-proof claims** ("confirmed", "verified", "established") either rest on a tested invariant across all relevant inputs, or carry explicit "for this one case" hedging.

### Provenance section completeness

- [ ] **Git commit** is recorded
- [ ] **Script paths** for the analysis are listed with one-line descriptions
- [ ] **Every script** that writes into `outputs/` (per the manifest's `computed_at` pointers and the analysis-script directory) appears in the Provenance list. *Failure mode: post-redesign drafts dropped diagnostic scripts (`run_em_tree_pairwise_autopsy.R`, etc.) because they were demoted into supplement narrative, leaving Provenance incomplete.*
- [ ] **Analyst name** and **analysis date range** present
- [ ] **Software environment** reference (`ENVIRONMENTS_INSTALLATIONS.md`) cited
- [ ] **Manifest path** and **compile-log path** are recorded so a future reader can audit how the report was built

### Code-grounding (claims vs. code, not docs)

- [ ] **Every load-bearing definitional / structural / enumeration claim** (enum membership, stage / step / category counts, emitted columns, precedence-chain order, coined-metric definitions) has been verified against the **code that implements it**, not against a README, `CLAUDE.md`, `specification.md`, or docstring. *Failure mode: the docs all agree a status field has three categories, the code actually emits five, and the manifest faithfully records the wrong consensus — every prose-vs-manifest check passes while the report is wrong.* Phase 1 sources these claims from code; Phase 6 re-verifies a sample.
- [ ] **Where a doc and the code disagreed**, the code won and the drifting doc was flagged for a fix (cross-document drift patch).

---

## Style (clarity and standalone-readability)

These checks verify the prose works for a skim reader and a careful reader simultaneously. Phase 4 (plain-English lint) and Phase 5 (framing critique) catch most of these.

### Language and readability

- [ ] **US English throughout** — `analyze` / `normalization` / `behavior` / `signaling` / `center` / `artifact` / `modeling`, not the British forms. *Failure mode: the draft drifts into `analyse` / `behaviour` / `signalling` / `centre`, mirroring stale British prose; this skill's reports are US English by default.*
- [ ] **Abstract and Problem Statement read as a readable introduction**, not a methods setup — they lead with the scientific story and why it matters before the apparatus. *Failure mode: a correct report that reads as an archive of verified facts, with no higher-level framing to carry the reader.*
- [ ] **Available figures are integrated.** Load-bearing plots in `outputs/figures/` are pulled into the report with self-contained captions and `\ref{}`, not omitted in favor of a figure-free archive. Zero figures is a deliberate choice for a figure-free analysis, not a default.

### Skim-reader robustness

- [ ] **Title + abstract + section headers + figure captions** tell a coherent story without body-prose recovery. *Failure mode: abstract says "held-aside validation markers"; body says they are not held-aside.*
- [ ] **Abstract** leads with the question, not the method
- [ ] **Abstract** has no undefined acronyms and no citations and no figure references
- [ ] **Problem statement** is understandable by a non-specialist
- [ ] **Definitions** section covers every symbol and technical term used in the report
- [ ] **Every subsection title under Results** states a finding, not a topic. *"Evidence-first cell calling" is a topic; "Evidence-first calling trades recall for precision in a transparent way" is a finding.* Methods, Provenance, and References subsection titles may be topics.

### Acronym discipline

- [ ] **No acronym** in the abstract without spelling out the underlying concept (regardless of audience tier)
- [ ] **No acronym** in any section title (regardless of audience tier)
- [ ] **No acronym** in any figure caption (regardless of audience tier)
- [ ] **Acronym budget per page** matches the Phase-0 audience tier: Tier A = 2; Tier B (default) = 4; Tier C = 6
- [ ] **First use per section** gives the plain-English gloss at the strictness level the audience tier requires (Tier A: every term; Tier B: non-trivial terms; Tier C: coined / overloaded terms only)

### Plain-English with intuitive-before-technical

- [ ] **No sentence** has ≥ 3 unexplained noun-phrases. *Failure mode: "consensus-floor depth-2 predictive-closure with lambda=0.10 sticky top-25" with no plain-English description of what the analyst is actually doing.*
- [ ] **Every load-bearing methodological choice** has an intuitive explanation that precedes the technical statement. The intuitive lead-in is sentence-length or paragraph-length depending on the complexity of the concept; routine technical details (standard tests, well-known transforms) need no lead-in at all.
- [ ] **Coined terms that shadow established literature terms** have an explicit "this is NOT the standard X" disclaimer at first use, naming the specific way they differ (missing factor of 2, not a p-value, no chi-square null, etc.).

### Worked-example grounding

- [ ] **Every new aggregation analysis type** in the draft has one worked example with raw inputs traced to the aggregate. The example is a small inline figure or sparkline-style mini-table where possible (Tufte sparklines are a strong reference shape).
- [ ] **Failure-mode worked examples** (cases where the analysis breaks) live in the supplement / appendix.
- [ ] **No aggregate claim** rests on a single example without hedging.

### Worked-example provenance

- [ ] **Every worked-example table** in the draft has a matching `manifest.worked_examples[*]` entry with a `subject_id` (concrete cell / row / capsule) and row-level `provenance`. *Failure mode: a table that looks plausible but whose values were invented during drafting because the model wanted a concrete example.*
- [ ] **Every row** in each worked-example table matches the manifest's `rows[*]` for the same `subject_id` — same `snp_id` / `role`, same `N`, same `Y`, same `alt_fraction`, same `covered` / `supports_target` flags.
- [ ] **The aggregate** rendered below the rows (typically a fraction and a call) matches `manifest.worked_examples[*].aggregate`.

### Denominator discipline

- [ ] **Every count or rate** in the prose has its denominator within one sentence. *Failure mode: "consensus floor of 5" — out of how many sweep conditions?*
- [ ] **For cross-universe comparisons**, the universe-eligible-restricted denominator is also reported. *Failure mode: "baseline_band sticky=1" when X21 and X19 are structurally excluded — "1 of 1 eligible," not "1 of 3."*
- [ ] **Bare percentages** are stated alongside the count (or vice versa) so the reader does not have to back out the denominator from rounding.

### Caveat prominence

- [ ] **For every headline number**, the strongest caveat lives at a heading level at least as prominent as the claim. A caveat buried in §1.3 footnote is hiding when the number is in the abstract.
- [ ] **Conclusions** state caveats explicitly ("This analysis does not address...", "A limitation is...")
- [ ] **Distinguish** what the data shows from what it suggests ("is consistent with" not "proves"; "suggests" not "demonstrates" unless evidence is truly definitive)

### Framing

- [ ] **Standalone or addendum** matches the Phase-0 choice. If standalone, no "as discussed in the previous report" / "we previously showed X" without inlining the relevant fact. If addendum, the referenced documents are named upfront.
- [ ] **No changelog framing** — the draft reads as "what we found", not "what we fixed". *Test: rewrite as though the analysis was done the right way from the start.*
- [ ] **Baseline of comparison** is named in the body and frames the headline finding.
- [ ] **Wrong-metric featured** is ruled out — the abstract's primary metric matches the Phase-0 planning brief, not whichever metric was most plentiful in summary CSVs.

### Report-shape consistency

- [ ] **Report shape** matches the Phase-0 choice. An "overview" draft with a 12-page methods section is not an overview.
- [ ] **Main vs. supplement** designation from Phase 0.75 is honored.
- [ ] **Main-text page count** (pages before `\appendix`) is within the shape budget: overview ≤ 6 pages; overview+supplement ≤ 14 pages; comprehensive ≥ 5 pages. Overruns get explicit acknowledgement in `.compile-log.md` under `shape_budget`.

### Results structure

- [ ] **Results section style** matches the Phase-0 choice (`policies.results_structure` in the manifest):
   - **Narrative** (DEFAULT): each result is prose with a self-explanatory subsection title that states the finding (caught by the Subsection title quality item above).
   - **Structured**: each result carries explicit `\paragraph{Question}` / `\paragraph{Findings}` / `\paragraph{Interpretation}` headers consistently across the section.

### Conclusions discipline

- [ ] **Every conclusion** maps back to the problem statement
- [ ] **No new results** are introduced in conclusions
- [ ] **Conclusions** were the LAST section written, after Results were complete and verified

---

## Formatting

- [ ] **Gene names** in italics (`\textit{}`)
- [ ] **Special characters** escaped (`_`, `%`, `&`, `#`, `$`)
- [ ] **Tables** use booktabs (no vertical lines, no `\hline`)
- [ ] **Figures** have readable labels at printed size
- [ ] **Cross-references** use `~` before `\ref{}` (no line breaks before numbers)

---

## Appendix / supplement (if present)

- [ ] **Failure-mode worked examples** are present for the analysis types described in the main text
- [ ] **Sensitivity figures** referenced from main-text methods section
- [ ] **Each figure** has a caption explaining what was varied and the conclusion
- [ ] **Appendix** uses `\appendix` command for correct numbering (overview+supplement and comprehensive shapes)
