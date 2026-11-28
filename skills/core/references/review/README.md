# Review checklists

These files are loaded by the sub-agents dispatched from `/mycelium:review`
(seven specialist checklists; the three lighter-tier ones share one sub-agent).
The main skill (`skills/review/SKILL.md`) does NOT read them directly — it passes
the path to the appropriate sub-agent.

## Files in this directory

| File | Purpose | Loaded by |
|------|---------|-----------|
| `stats-causal.md` | Statistical and causal-inference errors | stats-causal sub-agent |
| `data-pipeline-leakage.md` | Data wrangling, leakage, ML evaluation | data-pipeline-leakage sub-agent |
| `bioinformatics.md` | Genomics, scRNA-seq, gene names | bioinformatics sub-agent |
| `llm-failure-modes.md` | LLM-specific code antipatterns | llm-failure-modes sub-agent |
| `doc-schema-fidelity.md` | Doc/spec/schema vs reality | doc-schema-fidelity sub-agent |
| `code-quality.md` | API design, secrets, BC, organization | code-quality sub-agent |
| `results-and-claims.md` | Top-down check of each stated conclusion against the output tables, by read-only recomputation | results-and-claims sub-agent |
| `synthesis.md` | How to synthesize, dedupe, calibrate severity | main skill |
| `grill-mode.md` | Conversational grilling protocol | main skill |
| `deep-tripwires.md` | Opt-in behavioral follow-up: perturb inputs, observe checkpoints (fault-injection / metamorphic / known-answer tests) | main skill (Step 5) |

## Output contract for sub-agents

Each sub-agent must return findings in this shape (one block per finding):

```yaml
- severity: major | minor
  file: path/to/file.py
  line: 42  # or "42-58" for ranges; omit if N/A
  category: short tag from the agent's checklist (e.g. "leakage:preprocessing")
  summary: one sentence — what's wrong
  evidence: |
    1-5 lines copied VERBATIM from the source — enough that a reader sees
    the issue without opening the file. If the issue is a missing thing
    (no doublet detection, no multiple-comparison correction), show the
    surrounding lines and add a marker comment like `# (no FDR step here)`.
  why_it_matters: one or two sentences specific to this analysis context
  suggested_fix: what to do about it (one sentence is plenty)
  confidence: high | medium | low
  suggested_tripwire: missing-counts-file  # optional; see deep-tripwires.md
```

**Optional `suggested_tripwire` field.** If the finding is the kind of
silent failure that a behavioral tripwire could confirm — silent
fallback, missing-data handling, sample alignment, label leakage,
contrast direction, report-value freshness — tag it with the name of
the matching tripwire from `deep-tripwires.md`. Synthesis aggregates
these tags so the main skill can compose the Step 5 tripwire menu
without re-reasoning. If no tripwire fits, omit the field — do not
invent names.

**Severity is two levels only:**
- **`major`** — result is invalid, misleading, or insecure if not
  addressed (train/test contamination, look-ahead bias, hardcoded
  credentials, double dipping, hallucinated APIs, sample swap, missing
  multiple-comparison correction at scale, smuggled defaults on
  load-bearing knobs, BC cruft hiding the real code path, etc.)
- **`minor`** — would improve but doesn't change the conclusion (missing
  CIs alongside a clearly-significant estimate, hard-coded magic numbers,
  premature abstraction, comment freshness, naming clarity, etc.)

If the issue is purely stylistic and a linter would catch it, do not
include it. There's no separate Nit bucket — those just don't make it
into the output.

**`evidence` is the most important field after `summary`.** The synthesis
pass renders it as a code block under each finding so the user can see
what's wrong without opening the file. If you can't extract verbatim
lines, do not invent them — set evidence to `(unable to extract; see
file)` so the synthesis pass can flag it.

**Evidence for table and cross-conclusion findings.** A
`results-and-claims` finding rooted in an output table or in a
contradiction between conclusions may quote 1–5 lines of the results
prose (with `path:line`) together with the contradicting table rows
(with the table path and row numbers) or a compact read-only
recomputation result naming its input file and method. Every number in
it must have been read or computed, never paraphrased. Other
specialists keep the verbatim-source-lines rule.

When a sub-agent considered a thing and decided to skip flagging it
(because evidence was weak, or it was already justified in
`.living/decisions.md`), include it in a separate `not_flagged` section
so synthesis can dedupe with other agents that may flag the same thing:

```yaml
not_flagged:
  - file: path/to/file.py
    line: 42
    considered: short description
    reason: why we decided not to flag (e.g. "documented in decisions.md as intentional")
```

## Decisions list

In addition to findings, each sub-agent should return a short list of
**consequential analytical decisions** visible in its scope — the choices
that, if changed, would meaningfully change the result. The synthesis
pass aggregates these into the report's "Key decisions in this analysis"
section. For each decision:

```yaml
decisions:
  - name: short label (e.g., "clustering resolution", "train/test split strategy")
    description: one-line description of the choice the analysis made
    finding_ref: F2  # if there's an associated finding; otherwise omit
```

Don't duplicate findings as decisions. The decisions list is independent
— it's a way to show the user every load-bearing choice in their work,
some of which are flagged and some of which the user just may want to
double-check.

## Working within an effort budget

Every tool call re-reads everything already in your context, so a review's
cost grows with the number of calls, not only with what you read. Spend
calls on checks that could change a finding, not on finding your way around.

- Start from the context pack the orchestrator wrote (its path is in your
  prompt). It lists the in-scope files, the results documents and their
  numbered conclusions, and the output tables. Do not rediscover the tree
  with repeated `ls` or `find`.
- Batch reads: print several files in one command, each with a header line,
  and search many files with one `grep -n` instead of one call per file.
  Read the parts of a long file you need with a line range.
- Rough call budgets, as a guide rather than a hard stop:
  - the combined lighter-tier pass (`llm-failure-modes`,
    `doc-schema-fidelity`, `code-quality`): about 10 calls, on in-scope code
    and docs; do not sweep output tables (`results-and-claims` owns them);
  - each primary-tier code specialist (`stats-causal`,
    `data-pipeline-leakage`, `bioinformatics`): about 12 calls;
  - `results-and-claims`: about 20 calls, most of them on cross-checks and
    recomputation rather than reading.
- Go past the guide only when a check already under way could change a
  conclusion's number, direction, or scope. When the checklist is covered,
  stop and report; list anything you could not check in `not_flagged`.

## What every sub-agent should NOT flag

False positives kill review usefulness fast. Every checklist has a
"skip-flag" section. The shared rules across all agents:

- Don't flag stylistic preferences if a linter would catch them — let the
  linter do it
- Don't flag the same line twice with different framings; pick the most
  actionable
- Don't flag things explicitly justified in `.living/decisions.md` unless
  the justification is wrong
- Don't flag pre-existing code that the diff didn't touch, unless it
  directly affects the correctness of the touched code. The
  `results-and-claims` specialist is exempt: every conclusion in scope is
  its territory, and in a scoped review the `--focus`/`--cleared`
  contract replaces the diff boundary for every specialist (see
  `skills/review/SKILL.md`)
- Don't speculate. If you cannot point to evidence in the diff or context
  files, omit the finding. For `results-and-claims`, the results document,
  every output table and registered-values file in scope, and its own
  read-only recomputations are evidence; it is not limited to the diff
- Don't generate "general best practice" reminders untethered from the
  specific code
