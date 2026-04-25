---
description: >
  ALWAYS invoke this skill when the user wants to review, audit, sanity-check,
  or grill any code or analysis change in a data-analysis, statistics, ML, or
  bioinformatics project — pull requests, commits, branch diffs, working-tree
  changes, or pasted diffs. Trigger phrases include: "review this PR", "review
  this diff", "review this commit", "review my analysis", "code review",
  "audit this code", "is this analysis right", "is this correct", "sanity
  check this", "anything I'm missing", "before I merge", "/mycelium:review",
  "/review my analysis", "grill me on this", "interrogate my analysis", "ask
  me about my decisions". Also trigger proactively when the user pastes a diff
  or a PR URL and asks for an opinion, when they finish writing analysis code
  and want a second pair of eyes, or when they ask about correctness or
  robustness of an analysis. This skill dispatches six specialized sub-agents
  in parallel (statistical and causal inference; data pipeline and leakage;
  bioinformatics; LLM-specific coding antipatterns; documentation/schema
  fidelity; code quality and API design) and synthesizes a prioritized
  Critical/Important/Minor/Nit report — you cannot produce a correct review
  without consulting the per-domain checklists this skill loads, because they
  encode silent failure modes (Excel gene-name corruption, scRNA-seq double
  dipping, train/test contamination, time-series look-ahead, p-hacking via
  forking-paths, sycophancy-driven parameter drift, hallucinated APIs,
  definition drift, smuggled default parameters, undocumented implicit
  behavior, pseudoreplication, reference-genome mismatch) that general code
  review will not catch. The skill also has a `grill` mode that interviews
  the user conversationally about every consequential analytical decision
  (estimand, sample-filtering thresholds, multiple-comparison correction,
  train/test split, normalization, reference choice) one question at a time.
  Do NOT trigger for: writing NEW analysis code (/mycelium:analyze),
  generating reports or paper sections (/mycelium:report), open-ended
  brainstorming (/mycelium:ideas), repo initialization (/mycelium:core init),
  or pure SWE code review with no analysis component.
---

# Mycelium — Review

Review code, analysis, or documentation changes for the kinds of mistakes that
matter in scientific data work. Two modes:

- **Default**: dispatch six specialized sub-agents in parallel, then synthesize
  a single prioritized report.
- **`grill`**: walk the user through every consequential analytical decision
  conversationally, one question at a time.

The point of progressive disclosure is that the main skill stays under your
context budget and only loads the per-domain checklists into the sub-agents
that need them. Don't read the per-agent checklists yourself unless you are
debugging the skill — pass the file paths to the sub-agents.

## Why this skill exists

LLM coding agents tend to make the same kinds of mistakes when generating
analysis code, and many are silent: code that runs cleanly and produces a
confidently wrong number. This skill encodes the catalog of those failure
modes, partitions them across six specialists so each can hold a tight prompt,
and aggressively prunes false positives in synthesis. Severity calibration
matters more than raw recall — a list of fifty nits buries the one critical
finding.

## What gets reviewed

The skill works on any of the following review scopes. If the user did not
specify, ask once which scope they want.

| Scope | How to obtain the diff |
|-------|------------------------|
| **PR** (`/mycelium:review <PR#>` or PR URL) | `gh pr diff <num>` plus `gh pr view <num> --json files,body,title` for context |
| **Commit / commit range** (`/mycelium:review <sha>` or `<sha1>..<sha2>`) | `git show <sha>` or `git diff <sha1>..<sha2>` |
| **Working tree** (default if no scope given on a dirty repo) | `git diff HEAD` plus `git status` |
| **Branch vs main** | `git diff main...HEAD` |
| **Pasted diff** (user dumped a diff into chat) | Use the diff verbatim |
| **Whole analysis directory** (`/mycelium:review analysis/<name>`) | Read the directory; treat all files as "added" |

For all scopes: also collect the relevant context — the analysis's
`UPPER_SNAKE_CASE.md` documentation file, `specification.md` if present,
`.living/decisions.md`, and any installed convention packs in
`.living/conventions/` — and pass these into each sub-agent so it can ground
findings in the project's stated intent rather than inferring from code alone.

## Default mode protocol

### Step 1 — Establish review scope

Determine the scope from the user's invocation. If unclear, ask one short
question ("review the working tree, last commit, or a specific PR?") rather
than guessing. For PRs, fetch with `gh`. For commits/working tree, use `git`.

Capture the diff, the list of touched files, and any relevant context files
into local variables (or a scratch file in `/tmp/`) so the sub-agents share a
common substrate.

### Step 2 — Dispatch six sub-agents in parallel (or run their checklists in-line)

If the `Agent` tool is available to you, send a single message with six
concurrent `Agent` tool calls (subagent_type `general-purpose` is fine; for
very large diffs prefer `Explore`). If the `Agent` tool is *not* available
(common when this skill runs from inside a sub-agent context that doesn't
expose it), execute each sub-agent's checklist in-line: read each checklist
file in turn, apply it to the diff, collect findings, then proceed to
synthesis. Either way the output contract and the synthesis steps are the
same.

Each sub-agent (or in-line pass) gets:

1. The diff (or path to it if large)
2. The list of context files to read
3. The path to its checklist reference: `skills/core/references/review/<agent>.md`
4. A clear instruction to follow the output contract in
   `skills/core/references/review/README.md` exactly. The required
   fields per finding are `severity` (`major | minor`), `file`, `line`
   (or range), `category`, `summary`, `evidence` (a 1–5 line verbatim
   code snippet — the synthesis pass renders this directly under each
   finding), `why_it_matters` (one or two sentences specific to this
   analysis), `suggested_fix`, and `confidence` (`high | medium |
   low`). Anything the agent considered and decided not to flag goes
   in a separate `not_flagged` list with `file`, `line`, `considered`,
   and `reason` — synthesis uses this to dedupe across agents.
   Sub-agents should also return a `decisions` list of consequential
   analytical choices in their scope (per the README contract); these
   roll up into the report's "Key decisions in this analysis" section.
5. The standing instruction to err on the side of NOT flagging when the
   evidence is weak — false positives are more costly than false negatives at
   this stage because synthesis can ask follow-up questions but cannot
   reconstruct missing certainty.

The six sub-agents and their checklist files:

| # | Sub-agent | Checklist file | Focus |
|---|-----------|----------------|-------|
| 1 | **stats-causal** | `skills/core/references/review/stats-causal.md` | Test selection, multiple comparisons, p-hacking, causal claims, effect-size reporting, study design |
| 2 | **data-pipeline-leakage** | `skills/core/references/review/data-pipeline-leakage.md` | Train/test contamination, time-series look-ahead, joins, missing values, dedup, units, batch effects, ML evaluation |
| 3 | **bioinformatics** | `skills/core/references/review/bioinformatics.md` | Gene names, reference genome, scRNA-seq pipeline, RNA-seq DE, double dipping, pseudoreplication |
| 4 | **llm-failure-modes** | `skills/core/references/review/llm-failure-modes.md` | Try/except antipatterns, hallucinated APIs, default-parameter smuggling, sycophancy/forking-paths drift, fabricated tool output |
| 5 | **doc-schema-fidelity** | `skills/core/references/review/doc-schema-fidelity.md` | Docstrings/specs/schemas/READMEs vs reality, definition drift, comment freshness, undocumented behavior |
| 6 | **code-quality** | `skills/core/references/review/code-quality.md` | Duplicate sources of truth, boolean flag pairs, misleading names, premature abstractions, secrets, import hacks, BC cruft, file organization, logging consistency |

The bioinformatics agent should self-skip with a one-line "no biology here" if
the diff doesn't touch genomic data — don't spawn it if the project clearly
isn't biology, but err on the side of running it for any project that has ever
touched a sequence file, gene table, or single-cell object.

### Step 3 — Synthesize

Read `skills/core/references/review/synthesis.md` and follow it. The short
form:

1. Aggregate findings across sub-agents and dedupe. Two sub-agents
   flagging the same line with different framings is the common case —
   keep the more actionable framing and add a one-line "see also" if the
   other framing adds value.
2. Recalibrate severity to two levels: **Major** (fix this — result
   invalid, misleading, or insecure) and **Minor** (consider improving —
   doesn't change the conclusion). Drop pure stylistic nits a linter
   would catch.
3. Identify the **key analytical decisions** in the work — the
   consequential choices that, if changed, would meaningfully change the
   result (estimand, sample-filtering thresholds, normalization, model /
   test choice, multiple-comparison handling, train/test strategy,
   reference choice, etc.). List them whether or not each has an
   associated finding.
4. Draft 3–5 **questions for the analyst** — meta-level questions whose
   answers change which findings matter most (e.g., "is this for
   wet-lab validation or paper figure?", "are donors technical or
   biological replicates?", "is the deployment seeing the same
   customers or new ones?"). The diff can't answer these.
5. For each finding, include: file:line, a 1–5 line code snippet
   verbatim from the source so the user sees the issue without opening
   the file, why it matters *for this analysis specifically*, and a
   one-sentence fix.
6. Group findings by category (the six sub-agent areas), and within each
   category list Major findings first, then Minor.
7. Number findings F1, F2, F3 ... globally so the Key decisions and
   Questions sections can link to them where useful.

### Step 4 — Render the report

Write the report to a file under `.living/outputs/reviews/` named
`YYYY-MM-DD-<scope-slug>.md` (e.g., `2026-04-24-pr-127.md`,
`2026-04-24-working-tree.md`). The structure:

```markdown
# Review — <scope> — YYYY-MM-DD

**Scope**: <PR / commit range / working tree / pasted diff>
**Files reviewed**: N
**Sub-agents run**: 6 (or list which were skipped and why)

## Key decisions in this analysis

- **<Decision>** — <one-line description>. <see F2 if linked, or no
  link if informational>
- ...

## Questions for the analyst

Three to five open-ended questions whose answers would change which
findings matter most (analysis goal, replicate type, downstream use,
acceptable false-positive rate, registration status, deployment
context). The diff can't answer these — only the analyst can.

- <Question>
- ...

## Findings

### Statistics & causal inference
#### Major
##### F1. <short description>
`<file>:<line>`
```python
<1-5 lines verbatim>
```
**Why it matters here**: ...
**Fix**: ...

#### Minor
##### F2. ...

### Data pipeline & leakage
... (Major / Minor under each category)

### Bioinformatics
...

### LLM coding antipatterns
...

### Documentation & schema fidelity
...

### Code quality
...

## What was checked but is fine
- **Statistics & causal inference**: <one sentence>
- ...

## Notes
- Cross-cutting observations: compound findings sharing one remediation
  path, "did this code ever run" questions, etc.
```

Print the path of the written file at the end. The chat reply should
surface the count of Major findings per category and the "Key decisions"
list so the user sees the shape of the report without opening the file.

### Step 5 — Post-action hook

Treat a review as a significant action: log a short entry to
`.living/learnings.md` if the review surfaced a recurring pattern (e.g., "the
analyze script is using `t-test` on count data — this is the third time this
pattern has come up, consider a convention"). Otherwise no logging is needed.

## Grill mode protocol

Triggered by the user invoking with `grill` in their request, e.g.,
`/mycelium:review grill` or "grill me on this analysis".

Read `skills/core/references/review/grill-mode.md` and follow it. The short
form:

1. Identify the consequential decisions in the analysis. These are anything
   that, if changed, would meaningfully change the result: choice of
   statistical test, multiple-comparison correction, sample-filtering
   thresholds, normalization steps, clustering parameters, train/test split
   strategy, choice of estimand, choice of reference (genome, baseline,
   comparator), exclusion criteria, etc. Read `.living/decisions.md` and the
   analysis script(s) to extract these.
2. **One question per turn**, conversationally phrased. Do not dump a
   numbered list. The point is to feel like a thoughtful colleague over coffee,
   not a checklist. Acknowledge the answer, then move to the next.
3. Track answers internally. After ~5–8 exchanges (or when the user signals
   they want to wrap up), produce a short summary: which decisions had clear
   justifications, which the user wasn't sure about, which deserve a
   follow-up. Offer to file the unsure ones as `todo/` items.
4. If the user gives a justification that itself reveals a problem (e.g.,
   "we used t-test because that's what the tutorial used"), don't lecture —
   ask the next question that exposes the implication ("got it — and the
   data here is counts, right? do we expect the t-test assumptions to hold
   on counts?"). Trust theory of mind.

The aim of grill mode is to help the user converge on what they actually
believe about each choice without exhausting them. If at any point they say
"enough" or "let's stop," stop and write the partial summary.

## What this skill is NOT for

- Running the analysis (`/mycelium:analyze`)
- Generating a report or paper section (`/mycelium:report`)
- Repo-wide refactoring without an analysis context — use a generic code
  reviewer for that
- Stylistic / linting work — let the linter handle it
- Validating raw data ingestion — `/mycelium:ingest` covers that with its
  own checks

## Cross-references inside this skill

- `skills/core/references/review/synthesis.md` — synthesis & severity
  calibration
- `skills/core/references/review/grill-mode.md` — grill protocol detail
- Per-agent checklists under `skills/core/references/review/` (six files;
  loaded by sub-agents, not by you)
- The mycelium core `Post-Action Hook Protocol` from `commands/core.md`
  governs what to log to `.living/` when a review surfaces a recurring
  pattern
