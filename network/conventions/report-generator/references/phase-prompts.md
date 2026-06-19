# Phase prompts — sub-agent reviewers for report generation

This file contains the full prompts dispatched to the sub-agents in Phases 4, 5, and 6 of the report-generator flow. Each prompt is designed to be **self-contained**: the sub-agent reads the draft `.tex` files and the named inputs only, with no inherited context from the parent session. That blind-read is the missing ingredient in self-declared consistency checks.

Each phase produces findings that flow back to Phase 2 (draft patches). Loop until the sub-agent returns no findings.

If the `Agent` tool is unavailable (e.g., the report skill is running from inside a sub-agent context), execute the checklist in-line by reading the relevant inputs and applying the same questions. The output schema is the same either way.

---

## Output contract (all three phases)

Every finding has these fields:

```yaml
- severity: major | minor
  file: relative/path/to/file.tex
  line: 142            # or "142-148" for a range
  section: plain-english | framing | provenance | style   # per-phase categories below
  summary: one-line description of the issue
  evidence: |
    one to five lines verbatim from the draft (or grep output for cross-doc drift)
  why_it_matters: one or two sentences specific to this analysis
  suggested_fix: one sentence
  confidence: high | medium | low
```

Findings are returned as a flat list. The Phase 6 sub-agent also returns a top-level `provenance` vs `style` split — see that phase's prompt below.

---

## Phase 4 — Plain-English / glossary lint

**Inputs to load:**

- `analysis/[name]/reports/[name]-report.tex` (the draft itself)
- `analysis/[name]/reports/.manifest.json` (`terms[*]` and `policies` — nothing else)

**Do NOT load:**

- The planning brief
- The memory cheatsheet
- The analysis directory
- `.living/`

**Prompt:**

> You are reading a scientific report draft with **zero project context**. You do not know what the analysis is about, who the collaborators are, or what was decided in prior sessions. You will be given the draft `.tex` file, a list of coined terms from `.manifest.json:terms[*]`, and the manifest's `policies` block (acronym budget, strictness, intuition lead-in form). The `policies` block is the only configuration you act on; it is the manifest's compact summary of the Phase-0 audience tier.
>
> For each check below, return a finding with file, line, the quoted sentence, the issue, and a one-line fix.
>
> **Acronyms.** Use `policies.acronym_budget_per_page` and `policies.acronym_strictness`:
> - Find every acronym in the draft. For each one, locate its first use *per section* and verify a plain-English gloss is present in the same paragraph. The Abstract, Section titles, and Figure captions count as their own sections — an acronym defined in §2 does not count as defined in the abstract.
> - At `acronym_strictness: strict`: gloss every acronym on first use in every section, including standard terms (ARI, PC1, BIC). At `moderate` (default): gloss non-trivial acronyms per section; well-known field-standard terms (RNA-seq, PCA) may rely on the audience to know them. At `loose`: gloss only coined / overloaded acronyms; standard terms need no per-section regloss.
> - Count acronyms per page. If any page exceeds `policies.acronym_budget_per_page` distinct unexpanded acronyms, flag the page (judgment is allowed when a methods section legitimately uses a defined acronym many times).
> - Regardless of strictness: any acronym in a section title, the abstract, or a figure caption is a finding. These three surfaces always require spelled-out form.
>
> **Jargon-dense sentences.**
> - Scan each sentence for "noun-phrase stacks" — sequences of ≥ 3 unexplained technical noun-phrases. Examples to recognize: "consensus-floor depth-2 predictive-closure with lambda=0.10 sticky top-25", "empirical-bg vs fixed-bg permutation".
> - For each such sentence, check whether a plain-English description of the operation appears in the same paragraph. If not, flag it.
>
> **Intuitive-before-technical.** Use `policies.intuition_leadin_default_form`:
> - For each term in `manifest.terms[*]` that has a `plain_english` field (i.e., the manifest declared it load-bearing), locate its first use in the draft. Check whether the plain-English explanation appears *before* the technical statement.
> - At `intuition_leadin_default_form: paragraph` (Tier A audience): expect a full paragraph of intuition before the formal definition. At `sentence` (Tier B / default): a single intuitive sentence suffices. At `sentence-or-none` (Tier C): only novel or coined concepts need a lead-in.
> - Routine technical details (standard tests, well-known transforms) need no intuitive lead-in regardless of tier — do not flag those.
>
> **Overloaded-name guard.**
> - For each term in `manifest.terms[*]` that has an `overloaded_warning` field, locate its first use. Verify the warning is present and that it specifically names how this term differs from the standard literature term (missing factor of 2, not a p-value, etc.).
>
> Return findings as a YAML list following the output contract above. Use `section: plain-english` on every finding. Use `severity: major` for missing disclaimers on overloaded terms and for acronyms in the abstract; `severity: minor` for first-use-per-section gloss omissions and per-page budget overruns.
>
> If you find no issues, return an empty list and a one-line statement of what you checked. Do not invent findings to look thorough.

---

## Phase 5 — Framing critique

**Inputs to load:**

- `analysis/[name]/reports/[name]-report.tex` (the draft itself)
- `references/section-guide.md`, resolved relative to the convention pack root. When the pack is installed, this resolves to `.living/conventions/report-generator/references/section-guide.md`; when running from the mycelium source tree it resolves to `network/conventions/report-generator/references/section-guide.md`. The orchestrator passes the absolute path to the sub-agent; the sub-agent does not need to know where it lives. (The craft notes — read once, do not re-read per check.)
- The report's **audience tier** — the orchestrator passes `policies.audience_tier` (the single letter A / B / C) so the read-aloud jargon check can calibrate to the intended reader. This is the *only* manifest-derived value Phase 5 receives; it does not get the numbers, the framing fields, or the brief, so the standalone test stays honest. Default to Tier B (an adjacent-field colleague) if it is not supplied.

**Do NOT load:**

- The planning brief (this is the test — see if the standalone read recovers the framing the brief asked for, or if the brief is doing work the prose isn't)
- The manifest (beyond the single `policies.audience_tier` letter the orchestrator hands in)
- The analysis directory
- `.living/`

**Prompt:**

> You are reading a scientific report draft and asking: does this report stand alone? You have **zero project context**. You will be given the draft `.tex` file and the section-by-section craft notes the writer was supposed to follow.
>
> For each check below, return a finding with file, line, the quoted passage, the failed test, and a one-line fix.
>
> **Standalone test (skim-reader robustness).**
> - Read only the title, the abstract, every section header, and every figure caption. Set the body aside.
> - From those surfaces alone, write the one-paragraph summary you would take home. Then read the body. Does the body force you to retract or qualify any sentence in your summary?
> - The most common failure mode: the abstract states a finding without the body's qualifications. A reader who reads only the abstract — common — takes home the unqualified claim. Flag any such case.
>
> **Subsection title quality.**
> - For every subsection title under Results, decide whether the title states a *finding* (verb + outcome, e.g., "The root split cleanly separates dysplastic and non-dysplastic cells", "Recursive application stops rather than finds additional clone splits") or a *topic* (bare noun phrase, e.g., "Evidence-first cell calling", "Within-dysplasia branch selection").
> - Topic-only titles are findings. They waste the skim surface. Suggest a finding-form replacement that the body already supports.
> - Methods, Provenance, References, and supplement subsection titles are allowed to be topics.
> - A title that *contains* a finding plus a colon and a topic ("Within-dysplasia branch selection: SNP concentration improves, but cell calling stays hard") is fine — the colon clause carries the finding.
>
> **Baseline present.**
> - Does the body state what the headline finding is being compared against? Is the headline framing in terms of that baseline?
> - If the report makes a claim about a "new method" or "improvement," the production / published / prior baseline must be named upfront. Flag the absence.
>
> **Changelog framing.**
> - Does any prose read as "what we fixed" rather than "what we found"? Drafts that recap the path the author walked tend to read as changelogs.
> - Rewrite test: imagine the analysis was done the right way from the start. Would the prose change substantively? If yes, flag.
> - Particularly check the abstract and conclusions — body sections sometimes get away with light changelog framing, but the abstract anti-pattern is a finding regardless.
>
> **Read-aloud / voice.**
> - Read the load-bearing prose aloud in your head — the abstract, the problem statement, and the opening sentences of each Results unit. You are listening for prose a person would not actually say to a colleague.
> - Flag sentences that read as a *verified-facts archive* — a list of numbers and defined terms — rather than an explanation. The test: does the passage tell the reader what the numbers *mean* before (or instead of) enumerating them? A correct paragraph that reads like a CSV summary is a finding.
> - Flag any term that, read aloud, a reader at the report's audience tier (`policies.audience_tier`, passed to you; default Tier B = an adjacent-field colleague) could not parse without the page in front of them. This catches bare coined jargon in *running prose* — e.g. "centrality is not a volume proxy" — that the acronym and title checks miss because it is neither an acronym nor a title. Suggest a glossed or plain rewrite the body already supports.
> - Flag clunky number phrasings that the manifest's `unit` / `display` field should fix: "a fraction 0.978" should read "97.8%". You do not have the manifest, so flag the phrasing and let Phase 2 wire up the field.
>
> **Cross-document references.**
> - Count every "as discussed in the previous report," "we previously showed X," "see §X" (for non-self references), or "this builds on Y."
> - For each one, decide: was the relevant fact inlined in ≤ 1 sentence? If not, flag.
> - If the draft is an addendum (explicit opt-in from the planning brief — but you don't have access to that), check whether the introduction names the referenced base documents upfront and whether the cross-document references are confined to body text. Acceptable; the test is that the reader knows what to read.
> - Default assumption: the report is standalone. If the prose makes that assumption look wrong, flag it loudly.
>
> **Report-shape consistency.**
> - The report claims a shape implicitly through structure: a short main text + supplement vs. a single comprehensive document. Does the actual shape match the claim? An "overview" that runs 18 pages is not an overview.
> - Section length skew: if the Methods section is more than ~50% of the main text by length, flag as shape inconsistency (likely a comprehensive draft labeled as overview, or a methods-heavy section that should have been split into main + supplement).
>
> **Caveat prominence.**
> - For every headline number (any number that appears in the abstract or in a section conclusion), locate the strongest caveat for it. Verify the caveat lives at a heading level at least as prominent as the claim. A caveat buried in §1.3 footnote when the number is in the abstract is hiding.
>
> **Single-example-as-proof.**
> - Search for "confirmed", "verified", "established", "showed" used in a strong sense.
> - For each, check whether the supporting evidence is a tested invariant across all relevant inputs, or one example. If one example, the prose must hedge ("for this one case"). Flag absent hedging.
>
> Return findings as a YAML list following the output contract above. Use `section: framing` on every finding. Use `severity: major` for standalone-test failures and missing-baseline findings; `severity: minor` for cross-document reference style, caveat-prominence questions of degree, and voice findings — except an archive-register or unparseable-jargon passage that reaches the abstract, which is `major` (the abstract is the most-read surface).

---

## Phase 6 — Blind numerical re-verify

**Inputs to load:**

- `analysis/[name]/reports/[name]-report.tex` (the draft itself).
- `analysis/[name]/reports/.manifest.json` (full).
- `analysis/[name]/reports/build/report_values.tex` — the generated macro file (renderer output). **Required for the display-faithfulness check below.** For entries with `unit`/`display`, the reader-facing number is the macro *body* emitted here, not the manifest `value` and not the hand-maintained `\SciVal{\Macro}{snapshot}` in the draft. Phase 7's scitexlintr reconciles the snapshot against the manifest value; Phase 6 checks that the generated macro body is a faithful display of that value. If the file is absent or older than `.manifest.json`, rerun `render_report_values_tex.py` before the check.
- `analysis/[name]/reports/[name]-report.pdf` if it has been compiled — read with `pdftotext` to extract the prose-as-rendered (catches LaTeX-rendering edge cases that read differently than the source).
- **On-disk CSVs / outputs the manifest's `provenance` / `computed_at` fields point to.** Phase 6 is fundamentally a verification phase; it must be able to read the artifact a manifest entry claims to come from. Stay within `analysis/[name]/output/`, `analysis/[name]/outputs/`, and `analysis/[name]/figures/` (or whichever output directory the manifest's pointers establish — infer it once at the start).
- **The analysis script directory** for the Provenance-completeness check — `ls analysis/[name]/*.R` / `*.py` and read the first comment block of each script for the one-line description. Do not read deeper into scripts unless a specific finding (lying caption pointing to a specific figure generator) requires it.
- **The specific functions that implement load-bearing definitional / structural / enumeration claims**, for the code-grounding check below. This is the one check that reads the code as source of truth, so it is allowed to read deeper than the first comment block — but only into the named function that produces the claim under inspection (the enum builder, the pipeline driver, the schema definition, the precedence-ranking constant). Start from the manifest entry's `computed_at` / `grounded_in` pointer when present; otherwise grep for the value or the term. Do not crawl the whole codebase — open the implementing function, verify, move on.
- **The figure files themselves** (for `sha256` comparison against `manifest.figures[*].sha256`) and the figure-generation code referenced by `manifest.figures[*]` (typically `make_figures.R` or a similarly-named entry point) **only** when chasing a lying-caption finding. Open the specific function that draws the figure under suspicion; do not skim the whole file.
- **Cross-document drift target files** (for the cross-document drift check) — the analysis's `UPPER_SNAKE_CASE.md`, `STATUS.md`, `specification.md`, `conclusions.md`, `decisions_pre_run.md`, any plan documents, and other `.tex` files under `analysis/[name]/reports/`. Grep is preferred over full reads; named files only — do not crawl.

**Do NOT load:**

- The planning brief (`.planning-brief.yaml`) — that would tell you the verification target's framing intent and bias the blind read.
- The memory cheatsheet (`.memory-cheatsheet.md`) — same reason.
- `.living/` in any directory — project-context that biases the blind read.
- Any analysis directory other than `analysis/[name]/`.
- Other reports under `analysis/[name]/reports/` that have been excluded from verification (e.g., a human-revised baseline kept for comparison). The orchestrator names these explicitly when present; in their absence assume the report under verification is the only one.

The list above is permissive in scope but narrow in *kind*: you read the artifacts that the draft already cites or that the project's headline surfaces (STATUS, MANIFEST, etc.) carry, never the scaffolding the draft was written from. The blind read is preserved at the framing level — you still don't know the analyst's intent — while the verification has enough access to actually verify.

**Prompt:**

> You are verifying that every numeric token in a scientific report draft is consistent with the manifest of values that were computed, and that the same numbers are not contradicted elsewhere in the project. You are *blind to framing context* — you don't know the analyst's intent, you haven't seen the planning brief, you haven't read `.living/`. You may, however, read the on-disk artifacts the verification checks below need (CSVs the manifest points to, the figures themselves, the analysis script directory, and the named cross-doc drift files). The full input list and the explicit prohibition list are given above.
>
> Return findings in two top-level sections: **provenance** (label–value alignment, unsourced numbers, cross-document drift) and **style** (denominators, lying captions). The Phase-7 recompile log surfaces both sections; neither is more important than the other.
>
> **Provenance findings.**
>
> *Label–value alignment.*
> - Extract every numeric token in the prose (regex over `[-+]?\d*\.?\d+([eE][-+]?\d+)?`, with sensible filters for things like `figure 3` or `2024`).
> - **`\SciVal{\Macro}{snapshot}` and `\SciText{\Macro}{snapshot}` wrappers are pre-verified by scitexlintr in Phase 7.** When the numeric token sits inside the second argument of one of these wrappers, treat the value-to-manifest binding as already checked at lint time and focus this phase on the *label adjacent to the wrapper* (does it match `label_canonical`? is it in `label_aliases_forbidden`?). Numbers that appear bare in prose (no wrapper) are the unsourced-number candidates.
> - For each numeric token, locate its surrounding context — the noun phrase or label adjacent to it. ("0.482 ... weighted F1", "247 ... significantly upregulated genes", "p = 1.2e-8 ... NF-kB pathway enrichment").
> - Look up the numeric token in `manifest.numbers[*]`. If present:
>   - Verify the surrounding label matches `label_canonical`.
>   - Verify the surrounding label is not in `label_aliases_forbidden`.
> - If absent from the manifest: flag as **unsourced number**. These are often from a one-off check during the drafting conversation, not a stored test.
>
> *Display faithfulness (derived-number check).*
> - Some `numbers[*]` entries carry a `unit` or `display` field that controls how the number renders in prose: `unit:"percent"` makes the stored fraction `0.978` render as `97.8\%`, and `display` is a verbatim free-text override. The canonical `value` is unchanged; only the displayed string differs. The `\SciVal` snapshot in source remains the canonical value (`0.978`), not the rendered percent (`97.8\%`).
> - For each such entry, read the macro body from `build/report_values.tex` — the renderer output that actually reaches the PDF — and confirm it is a faithful function of `value`. Check the *generated macro*, not the `\SciVal{\Macro}{snapshot}` in the draft: the snapshot is a hand-maintained mirror of the manifest value, while this check verifies the display transformation that readers see. A `unit:"percent"` macro must equal `value*100` rounded to the entry's `precision` (default 1) — `0.978 → 97.8\%`, `0.0111 → 1.1\%`; a free-text `display` macro must not state a number that contradicts `value`. If `build/report_values.tex` is missing or older than `.manifest.json`, rerun `render_report_values_tex.py` before checking. Flag a macro that does not derive from `value` as a provenance finding (severity: `major` when it changes the number a reader takes home, `minor` for a rounding-only quibble). This is the one way the readable number can drift from the verified one, so it closes the hole that `unit`/`display` would otherwise open.
>
> *Adjacent-paragraph swaps.*
> - For each pair of numerically-similar values appearing in adjacent paragraphs, check whether the labels are consistent across the paragraphs. The lamanno / 10x swap pattern is: -0.014 in one paragraph, -0.023 in the next, when the labels indicate the values should be the other way around.
 Flag any candidate.
>
> *Figure freshness.*
> - For every `\includegraphics{...}` in the draft, locate the file. Hash it. Compare against `manifest.figures[*].sha256`. If mismatch, flag as **stale figure** — the figure was regenerated mid-draft and the prose may reference the wrong version.
>
> *Worked-example value verification.*
> - Identify every `\begin{table}` / `tabular` block in the draft that is a worked example. Heuristics: the surrounding caption or text uses the phrase "worked example", "trace", "single cell", or names a concrete identifier (`c_017`, `module_42`, etc.); the table columns include raw inputs (`N`, `Y`, `coverage`, `alt fraction`); the table is short (≤ ~10 rows) and is followed by an aggregate value derived from the rows.
> - For each candidate worked-example table, extract the named `subject_id` from the caption or surrounding text. Look up `manifest.worked_examples[*]` for an entry with that `subject_id`.
>   - **No matching entry**: flag as **unsourced worked example** (severity: major). The table values are not registered with provenance, which means they may be confabulated.
>   - **Matching entry exists**: verify every row in the rendered table equals the corresponding row in `manifest.worked_examples[*].rows[*]` — same `snp_id` / `role`, same `N`, same `Y`, same `alt_fraction`, same `covered` and `supports_target` flags. Any mismatch is a **fabricated worked-example value** (severity: major).
>   - Verify the rendered aggregate (typically a fraction-of-supported-SNPs and a call) equals `manifest.worked_examples[*].aggregate`. Mismatch is major.
> - This check exists because worked-example tables pass the visual sniff test and the Phase-3 presence gate even when the row values are invented. The manifest is the only thing that distinguishes a real trace from a plausible-looking confabulation.
>
> *Provenance-section completeness.*
> - Read the draft's Provenance section. Extract the list of cited script paths.
> - Infer the analysis script directory from `manifest.numbers[*].computed_at` and `manifest.worked_examples[*].computed_at` (e.g., `scripts/`, `analysis/[name]/`, or wherever the manifest's `computed_at` pointers live). List the actual script files in that directory matching the patterns `run_*.{R,py}`, `evaluate_*.{R,py}`, `make_*.{R,py}`.
> - Every script that writes into `outputs/` (heuristic: any script the manifest's `computed_at` fields cite, plus any sibling scripts in the same directory) should appear in the Provenance section with a one-line description. Pure diagnostic / development scripts (`*_test.R`, `*_dev.R`, `scratch_*.R`) can be omitted.
> - Flag missing scripts as **provenance-incomplete** (severity: minor) with a suggested one-line description drawn from the script's first comment block or filename.
>
> *Code-grounding (doc-vs-code drift).*
> - This is the only check in this phase that treats the **code as source of truth** rather than the manifest. The reason it exists: the manifest, and every prose number checked against it, can be internally consistent and still wrong, because the manifest was built from documentation (a `README`, `CLAUDE.md`, a docstring) that drifted away from the code. When the whole documentation surface agrees on a wrong claim, the manifest faithfully records the wrong consensus and every other check in this phase passes. Only reading the implementing code catches it.
> - Identify the report's **load-bearing definitional, structural, and enumeration claims**: the set of categories in an enum (e.g. a status field documented as `{pending, active, done}` that the code actually emits with more values), the count of stages or steps in a pipeline, the columns or fields a function emits, the order of a precedence / ranking chain, the closed vocabulary a classifier uses, the definition of a coined metric. These are claims about *what the code does*, not measured numbers.
> - For each such claim, open the function that implements it (use the manifest's `computed_at` / `grounded_in` pointer; else grep for the value, the term, or the enum members) and verify the claim against the code's actual behavior. Does the enum really have four members, or eight? Does the pipeline really have six steps, or eight? Does the precedence chain in the code match the order quoted in the prose?
> - A claim the code refutes is a **code-grounding finding**. Severity: `major` when it changes a reported value or a definition the reader would act on (wrong enum, wrong count, wrong precedence order); `minor` when it is a stale label that does not change the meaning. In the finding's `evidence`, quote both the draft's claim and the contradicting code (`file:line`). In `suggested_fix`, give the draft correction *and* note the documentation file that drifted, so the cross-document drift pass can patch it at the source.
> - Be conservative about scope: check the claims a reader would build on, not every incidental number. And be honest about the limit — if the code itself is wrong and every doc agrees with it, this check cannot catch that; it catches doc-vs-code disagreement, not code-vs-reality. Flag a claim only when you have read the implementing code and it disagrees with the draft.
>
> *Cross-document drift.*
> - For each unique number in the draft, `grep` for it in:
>   - `analysis/[name]/conclusions.md` (if present)
>   - `analysis/[name]/specification.md` (if present)
>   - `analysis/[name]/*.md` (the UPPER_SNAKE_CASE analysis doc)
>   - Any other `.tex` files under `analysis/[name]/reports/`
> - For each hit, check whether the value is consistent with the draft. If not, emit a patch suggestion — the other document needs the same fix.
> - For each domain term in `manifest.terms[*]` and each collaborator name (extractable from the report title page and any acknowledgements), `grep` the same set of files. Spellings must be consistent.
>
> **Style findings.**
>
> *Denominators.*
> - For every bare count or rate in the prose, verify the denominator appears within one sentence. ("Consensus floor of 5" without the sweep size is a finding.)
> - For cross-universe comparisons, verify the universe-eligible-restricted denominator is also reported. ("Sticky=1 of 3" when 2 of 3 are structurally excluded is misleading; the eligible denominator is 1.)
>
> *Lying captions / lying glossary entries.*
> - For each figure caption, scan the immediately-following or referenced code (`outputs/figures/` companion script if available, or the surrounding analysis context inferable from path) for filters and thresholds. If the caption describes a filter the code does not apply, flag as a **lying caption**.
> - For each glossary / definitions entry, scan the rest of the draft for uses that contradict the definition. If a term is defined as "X" in §2.1 but used as "Y" in §3.4, flag.
>
> Return findings as a YAML document with two top-level lists: `provenance:` and `style:`. Each entry follows the output contract. Use `section: provenance` or `section: style` accordingly.
>
> If you find no issues in either section, return both lists empty and a one-line statement of what you checked. Do not invent findings to look thorough — false positives in this phase cost the drafter real time chasing nothing.

---

## When to loop

Each sub-agent phase loops:

1. Sub-agent runs, returns findings.
2. Findings flow to Phase 2 (the draft is patched).
3. Re-run the same sub-agent.
4. Repeat until the sub-agent returns no findings.

In practice, two iterations is typical. Three or more iterations on the same sub-agent is a signal that the manifest (Phase 1) or the planning brief (Phase 0) needs revision — the draft is being patched against findings that should have been prevented upstream. Flag this in the compile log.

---

## Cross-cutting notes

- **Sub-agents must not know the planning brief, the memory cheatsheet, or the analysis directory.** The point of phase-4/5/6 is the blind read. Honor the input list above strictly.
- **One sub-agent per phase, not one sub-agent per checklist item.** Combining the checks in one prompt keeps the context small and lets the sub-agent share work (e.g., the regex pass for numeric tokens in Phase 6 is one pass, not one per check).
- **Output is YAML, not Markdown.** This matters because the parent flow programmatically applies the findings — Markdown free-text is error-prone to parse. The orchestrator persists each phase's output to `analysis/[name]/reports/.review-plain-english.yaml`, `.review-framing.yaml`, and `.review-numerical.yaml` respectively. The sub-agent returns the YAML body; the orchestrator owns the file.
- **Sub-agents err on the side of NOT flagging.** Each prompt says so; treat that line as load-bearing. False positives waste the drafter's time. A finding with `confidence: low` is fine; an invented finding is not.
