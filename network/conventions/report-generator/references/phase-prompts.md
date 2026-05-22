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
- `analysis/[name]/reports/.manifest.json` (terms section only — `manifest.terms[*]`)

**Do NOT load:**

- The planning brief
- The memory cheatsheet
- The analysis directory
- `.living/`

**Prompt:**

> You are reading a scientific report draft with **zero project context**. You do not know what the analysis is about, who the collaborators are, or what was decided in prior sessions. You will be given the draft `.tex` file and a list of coined terms from `.manifest.json`. Your job is to flag every place a non-specialist reader would stumble.
>
> For each check below, return a finding with file, line, the quoted sentence, the issue, and a one-line fix.
>
> **Acronyms.**
> - Find every acronym in the draft. For each one, locate its first use *per section* (not per document) and verify a plain-English gloss is present in the same paragraph. The Abstract, Section titles, and Figure captions count as their own sections — an acronym defined in §2 does not count as defined in the abstract.
> - Count acronyms per page. If any page exceeds 4 distinct unexpanded acronyms, flag the page (heuristic — judgment is allowed when a methods section legitimately uses a defined acronym many times).
> - Any acronym in a section title or figure caption is a finding regardless of other definitions in the document.
>
> **Jargon-dense sentences.**
> - Scan each sentence for "noun-phrase stacks" — sequences of ≥ 3 unexplained technical noun-phrases. Examples to recognise: "consensus-floor depth-2 predictive-closure with lambda=0.10 sticky top-25", "empirical-bg vs fixed-bg permutation".
> - For each such sentence, check whether a plain-English description of the operation appears in the same paragraph. If not, flag it.
>
> **Intuitive-before-technical.**
> - For each term in `manifest.terms[*]` that has a `plain_english` field (i.e., the manifest declared it load-bearing), locate its first use in the draft. Check whether the plain-English explanation appears *before* the technical statement.
> - The intuitive lead-in may be a sentence or a paragraph — judgment is allowed based on the complexity of the concept. Flag only when no intuitive lead-in exists at all (the draft jumps straight to the technical form for a load-bearing concept).
> - Routine technical details (standard tests, well-known transforms) need no intuitive lead-in — do not flag those.
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
- `network/conventions/report-generator/references/section-guide.md` (the craft notes — read once, do not re-read per check)

**Do NOT load:**

- The planning brief (this is the test — see if the standalone read recovers the framing the brief asked for, or if the brief is doing work the prose isn't)
- The manifest
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
> **Baseline present.**
> - Does the body state what the headline finding is being compared against? Is the headline framing in terms of that baseline?
> - If the report makes a claim about a "new method" or "improvement," the production / published / prior baseline must be named upfront. Flag the absence.
>
> **Changelog framing.**
> - Does any prose read as "what we fixed" rather than "what we found"? Drafts that recap the path the author walked tend to read as changelogs.
> - Rewrite test: imagine the analysis was done the right way from the start. Would the prose change substantively? If yes, flag.
> - Particularly check the abstract and conclusions — body sections sometimes get away with light changelog framing, but the abstract anti-pattern is a finding regardless.
>
> **Cross-document references.**
> - Count every "as discussed in the previous report," "we previously showed X," "see §X" (for non-self references), or "this builds on Y."
> - For each one, decide: was the relevant fact inlined in ≤ 1 sentence? If not, flag.
> - If the draft is an addendum (explicit opt-in from the planning brief — but you don't have access to that), check whether the introduction names the referenced base documents upfront and whether the cross-document references are confined to body text. Acceptable; the test is that the reader knows what to read.
> - Default assumption: the report is standalone. If the prose makes that assumption look wrong, flag it loudly.
>
> **Report-shape consistency.**
> - The report claims a shape implicitly through structure: a short main text + supplement vs. a single comprehensive document. Does the actual shape match the claim? An "overview" that runs 18 pages is not an overview.
> - Section length skew: if the Methods section is more than ~50% of the main text by length, flag as shape inconsistency (likely a comprehensive draft labelled as overview, or a methods-heavy section that should have been split into main + supplement).
>
> **Caveat prominence.**
> - For every headline number (any number that appears in the abstract or in a section conclusion), locate the strongest caveat for it. Verify the caveat lives at a heading level at least as prominent as the claim. A caveat buried in §1.3 footnote when the number is in the abstract is hiding.
>
> **Single-example-as-proof.**
> - Search for "confirmed", "verified", "established", "showed" used in a strong sense.
> - For each, check whether the supporting evidence is a tested invariant across all relevant inputs, or one example. If one example, the prose must hedge ("for this one case"). Flag absent hedging.
>
> Return findings as a YAML list following the output contract above. Use `section: framing` on every finding. Use `severity: major` for standalone-test failures and missing-baseline findings; `severity: minor` for cross-document reference style and caveat-prominence questions of degree.

---

## Phase 6 — Blind numerical re-verify

**Inputs to load:**

- `analysis/[name]/reports/[name]-report.tex` (the draft itself)
- `analysis/[name]/reports/.manifest.json` (full)
- `analysis/[name]/reports/[name]-report.pdf` if it has been compiled — read with `pdftotext` to extract the prose-as-rendered (catches LaTeX-rendering edge cases that read differently than the source)
- Other documents to grep for cross-document drift (named below in the prompt)

**Do NOT load:**

- The planning brief, the memory cheatsheet, the analysis directory, `.living/`

**Prompt:**

> You are verifying that every numeric token in a scientific report draft is consistent with the manifest of values that were computed, and that the same numbers are not contradicted elsewhere in the project. You have **zero project context** beyond the draft and the manifest.
>
> Return findings in two top-level sections: **provenance** (label–value alignment, unsourced numbers, cross-document drift) and **style** (denominators, lying captions). The Phase-7 recompile log surfaces both sections; neither is more important than the other.
>
> **Provenance findings.**
>
> *Label–value alignment.*
> - Extract every numeric token in the prose (regex over `[-+]?\d*\.?\d+([eE][-+]?\d+)?`, with sensible filters for things like `figure 3` or `2024`).
> - For each numeric token, locate its surrounding context — the noun phrase or label adjacent to it. ("0.482 ... weighted F1", "247 ... significantly upregulated genes", "p = 1.2e-8 ... NF-kB pathway enrichment").
> - Look up the numeric token in `manifest.numbers[*]`. If present:
>   - Verify the surrounding label matches `label_canonical`.
>   - Verify the surrounding label is not in `label_aliases_forbidden`.
> - If absent from the manifest: flag as **unsourced number**. These are often from a one-off check during the drafting conversation, not a stored test.
>
> *Adjacent-paragraph swaps.*
> - For each pair of numerically-similar values appearing in adjacent paragraphs, check whether the labels are consistent across the paragraphs. The lamanno / 10x swap pattern is: -0.014 in one paragraph, -0.023 in the next, when the labels indicate the values should be the other way around. Flag any candidate.
>
> *Figure freshness.*
> - For every `\includegraphics{...}` in the draft, locate the file. Hash it. Compare against `manifest.figures[*].sha256`. If mismatch, flag as **stale figure** — the figure was regenerated mid-draft and the prose may reference the wrong version.
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

- **Sub-agents must not know the planning brief, the memory cheatsheet, or the analysis directory.** The point of phase-4/5/6 is the blind read. Honour the input list above strictly.
- **One sub-agent per phase, not one sub-agent per checklist item.** Combining the checks in one prompt keeps the context small and lets the sub-agent share work (e.g., the regex pass for numeric tokens in Phase 6 is one pass, not one per check).
- **Output is YAML, not Markdown.** This matters because the parent flow programmatically applies the findings — Markdown free-text is error-prone to parse. The orchestrator persists each phase's output to `analysis/[name]/reports/.review-plain-english.yaml`, `.review-framing.yaml`, and `.review-numerical.yaml` respectively. The sub-agent returns the YAML body; the orchestrator owns the file.
- **Sub-agents err on the side of NOT flagging.** Each prompt says so; treat that line as load-bearing. False positives waste the drafter's time. A finding with `confidence: low` is fine; an invented finding is not.
