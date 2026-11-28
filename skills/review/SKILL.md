---
name: review
description: >
  Review or audit analytical code and scientific changes in statistics, ML,
  bioinformatics, or data pipelines. Use for PRs, commits, diffs, sanity checks,
  correctness questions, pre-merge review, tripwire testing, or conversational
  grill mode. Check stated results against output tables, and consult the
  bundled statistical, leakage, bioinformatics, documentation, LLM-failure, and
  code-quality checklists; synthesize prioritized findings. Do not use for writing new analysis, reports, brainstorming, project
  initialization, or pure software review with no analytical component.
---

# Mycelium — Review

Resolve bundled `skills/` paths relative to the Mycelium plugin root (two
directories above this `SKILL.md`). Resolve repository and `.living/` paths
relative to the user's working repository.

Review code, analysis, or documentation changes for the kinds of mistakes that
matter in scientific data work. Two modes plus one opt-in follow-up:

- **Default**: run seven specialist checklists in sub-agents (the three
  mechanical ones share one lighter-tier sub-agent), parallelized up to the
  host capacity and run in waves when necessary, then synthesize a single
  prioritized report. Add `--focus` (and optionally `--cleared`) to re-review
  only some conclusions or files of a large analysis; see "Scoped review".
- **`grill`**: walk the user through every consequential analytical decision
  conversationally, one question at a time.
- **Tripwires (opt-in, offered after default)**: perturb inputs and verify
  the pipeline actually fails at named scientific boundaries. Three
  categories — fault-injection, metamorphic, and known-answer tests.
  Three modes — **audit** (default: write a behavioral spec describing
  which tripwires would apply, no code runs), **scaffold** (propose
  project-specific patches that add the instrumentation hooks),
  **run** (execute against existing instrumentation). Scaffold and
  run are agent-improvised per analysis rather than template-stamped.
  See `skills/core/references/review/deep-tripwires.md`.

The point of progressive disclosure is that the main skill stays under your
context budget and only loads the per-domain checklists into the sub-agents
that need them. Don't read the per-agent checklists yourself unless you are
debugging the skill — pass the file paths to the sub-agents.

## Why this skill exists

LLM coding agents tend to make the same kinds of mistakes when generating
analysis code, and many are silent: code that runs cleanly and produces a
confidently wrong number. This skill encodes the catalog of those failure
modes, partitions them across seven specialists so each can hold a tight prompt,
and aggressively prunes false positives in synthesis. Six specialists read
the code from the bottom up. The seventh, `results-and-claims`, works from the
top down: it starts from each stated conclusion and the output tables and
recomputes, read-only, what the prose asserts, because defects such as
omitted table rows or contradictions between conclusions never appear on a
code line. Severity calibration
matters more than raw recall — a list of fifty nits buries the one critical
finding.

## What gets reviewed

The skill works on any of the following review scopes. If the user did not
specify, ask once which scope they want.

| Scope | How to obtain the diff |
|-------|------------------------|
| **PR** (`/mycelium:review <PR#>` or PR URL) | Resolve `baseRefOid` and `headRefOid` via `gh pr view <num> --json baseRefOid,headRefOid,files,body,title`, then derive both the diff (`git diff <base>..<head>` or commit-pinned PR diff) and tracked evidence from that exact `<head>` SHA |
| **Commit / commit range** (`/mycelium:review <sha>` or `<sha1>..<sha2>`) | `git show <sha>` or `git diff <sha1>..<sha2>` |
| **Working tree** (default if no scope given on a dirty repo) | `git diff HEAD` plus `git status` |
| **Branch vs main** | `git diff main...HEAD` |
| **Pasted diff** (user dumped a diff into chat) | Use the diff verbatim |
| **Whole analysis directory** (`/mycelium:review analysis/<name>`) | Read the directory; treat all files as "added" |
| **Scoped re-review** (`/mycelium:review analysis/<name> --focus C7,C8 [--cleared C1,C2]`) | Any scope above, narrowed to the focused conclusions or files; see "Scoped review" |

For all scopes: also collect the relevant context — the analysis's
`UPPER_SNAKE_CASE.md` documentation file, `specification.md` if present,
`.living/decisions.md`, and any installed convention packs in
`.living/conventions/` — and pass these into each sub-agent so it can ground
findings in the project's stated intent rather than inferring from code alone.
Also list the analysis's results document(s), `outputs/tables/*`, and
`outputs/numbers.json` (or their equivalents) for `results-and-claims`, even
when the diff did not touch them: the conclusions they state are in scope.
For every revision-based scope (PR, commit, commit range, or branch vs main),
resolve tracked results documents, scripts, and tables against the reviewed
revision SHA (e.g., `git show <rev>:<path>` or a read-only export of that SHA)
rather than reading live working-tree files that may carry uncommitted local
edits; read live files only for working-tree or whole-directory reviews, and
treat untracked or generated artifacts as unavailable unless they can be tied
to the reviewed revision.

### Invocation options

| Option | Effect |
|--------|--------|
| `--focus <items>` | Comma-separated conclusion IDs as the results document numbers them (`C7,C8`, `Conclusion 7`) or repository paths (`scripts/05_overdispersion.py`). Review those in depth. |
| `--cleared <items>` | Conclusions or files already reviewed and cleared. Read-only boundary context; requires `--focus`. |
| `--all-frontier` | Run every specialist on the primary model; disables the lighter tier. |
| `--no-tripwires` | Skip Step 5. |

The user may also say these in words ("only re-review conclusions 7 and 8;
1–6 were fine last time"). Treat that as the same options.

## Default mode protocol

### Step 1 — Establish review scope

Determine the scope from the user's invocation. If unclear, ask one short
question ("review the working tree, last commit, or a specific PR?") rather
than guessing. For PRs, fetch with `gh`. For commits/working tree, use `git`.

Write a **context pack**: one scratch file in a temporary directory outside
the repository that every sub-agent reads first, so none of them spends calls
rediscovering the tree. Keep it to an index, not file contents:

- the scope, reviewed revision (SHA/ref when reviewing a PR, commit, or range),
  and how the diff was obtained, plus the diff itself (or the path to a saved
  copy when it is large);
- the in-scope files with their line counts;
- the results document(s), with each stated conclusion's ID (or section
  heading and line number when unnumbered), heading, and line number;
- the output tables and registered-values files, with their row counts;
- the context files (`.living/decisions.md`, `.living/learnings.md`,
  specifications, convention packs);
- for a scoped review, the resolved focus and boundary sets.

For a scoped review, resolve the focus before dispatch:

1. Map each focused conclusion ID to its section in the results document, the
   scripts that produce its numbers, and the tables it cites (use the
   document's own citations, `run.sh`, `outputs/numbers.json`, and any
   recorded lineage). Map each focused file to the conclusions that use it.
2. Do the same for `--cleared` items, as boundary context only.
3. If a focus item cannot be resolved (no such conclusion or file), ask one
   short question rather than guessing.
4. Without `--cleared`, everything outside the focus is still boundary
   context: read it where it bears on the focus, but file nothing standalone
   about it.

### Step 2 — Dispatch the specialists in parallel (or run their checklists in-line)

The seven specialist checklists run in five sub-agents: one each for
`results-and-claims`, `stats-causal`, `data-pipeline-leakage`, and
`bioinformatics`, and one **lighter pass** that applies the
`llm-failure-modes`, `doc-schema-fidelity`, and `code-quality` checklists in
turn and returns their findings under their own categories. Split the
lighter pass into three sub-agents only with `--all-frontier` or when the
change is mostly software (no results document in scope), where those
checklists carry the review.

If the `Agent` tool is available to you, schedule these sub-agents while
respecting the host capacity (subagent_type `general-purpose` is fine; for very
large diffs prefer `Explore`). Run as many concurrently as the host permits,
wait for that wave to finish, and dispatch the remaining specialists in later
waves. Do not assume that five slots are available. When slots are
limited, put `results-and-claims` and `stats-causal` in the first wave.
In a full review, do not run the checklists in-line to save cost or to fit
a spend cap when the `Agent` tool works: `results-and-claims` must stay
independent of the code-level reading, and in-line runs share one context.
If a cap cannot cover the dispatch, say so and offer a scoped review instead.
A scoped review is the exception: it runs in-line by default (see "Scoped
review" below). If dispatch reports
`agent thread limit reached`, wait for an active specialist to finish and retry
the undispatched specialist once; if capacity remains unavailable, run that
checklist in-line. If the `Agent` tool is *not* available (common when this
skill runs from inside a sub-agent context that doesn't expose it), execute
each specialist's checklist in-line: read each checklist file in turn, apply it
to the diff, collect findings, then proceed to synthesis. In-line, run
`results-and-claims` first and write its findings down before reading the
other checklists, so its top-down reading is not anchored to the code-level
one. Either way the output
contract and the synthesis steps are the same, and every applicable checklist
must run exactly once.

Each sub-agent (or in-line pass) gets:

1. The path to the context pack (which holds or points to the diff), and an
   instruction to read it first and to follow the effort budget in
   `skills/core/references/review/README.md`
2. Any context files the pack does not already list
3. The path to its checklist reference: `skills/core/references/review/<agent>.md`
   (three paths for the lighter pass)
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
6. For a scoped review, the resolved focus set and boundary set, with the
   scoping contract under "Scoped review" below.

Give `results-and-claims` the results document(s), the output tables, and the
registered values in addition to the diff, and do not give it any other
specialist's findings. Tell it that it may run read-only Python or R against
those files from a temporary directory outside the repository, and that it
is exempt from the diff-only rule (its checklist states the boundaries).

The seven specialists, their checklist files, and their default tier
(rows 5–7 share the lighter pass):

| # | Sub-agent | Checklist file | Tier | Focus |
|---|-----------|----------------|------|-------|
| 1 | **results-and-claims** | `skills/core/references/review/results-and-claims.md` | primary | Each stated conclusion against every row of its output tables; cross-conclusion consistency; read-only sensitivity, clustering, and noise-floor recomputation; per-sentence prose fidelity |
| 2 | **stats-causal** | `skills/core/references/review/stats-causal.md` | primary | Test selection, multiple comparisons, p-hacking, causal claims, effect-size reporting, study design |
| 3 | **data-pipeline-leakage** | `skills/core/references/review/data-pipeline-leakage.md` | primary | Train/test contamination, time-series look-ahead, joins, missing values, dedup, units, batch effects, ML evaluation |
| 4 | **bioinformatics** | `skills/core/references/review/bioinformatics.md` | primary | Gene names, reference genome, scRNA-seq pipeline, RNA-seq DE, double dipping, pseudoreplication |
| 5 | **llm-failure-modes** | `skills/core/references/review/llm-failure-modes.md` | lighter | Try/except antipatterns, hallucinated APIs, default-parameter smuggling, sycophancy/forking-paths drift, fabricated tool output |
| 6 | **doc-schema-fidelity** | `skills/core/references/review/doc-schema-fidelity.md` | lighter | Docstrings/specs/schemas/READMEs vs reality, definition drift, comment freshness, undocumented behavior |
| 7 | **code-quality** | `skills/core/references/review/code-quality.md` | lighter | Duplicate sources of truth, boolean flag pairs, misleading names, premature abstractions, secrets, import hacks, BC cruft, file organization, logging consistency |

`results-and-claims` and `stats-causal` are the primary reviewers of an
analysis, not optional extras. When host capacity or budget forces a choice,
and in scoped or lightweight runs, those two always run, on the primary tier;
the mechanical specialists (`llm-failure-modes`, `doc-schema-fidelity`,
`code-quality`) run on the lighter tier or on the changed or focused files
only. `results-and-claims` self-skips with the one line "no empirical
results or output tables in scope" when there is no results document, output table, or
registered-values file in scope (a pure software or infrastructure change).

The bioinformatics agent should self-skip with a one-line "no biology here" if
the diff doesn't touch genomic data — don't spawn it if the project clearly
isn't biology, but err on the side of running it for any project that has ever
touched a sequence file, gene table, or single-cell object.

A self-skipped specialist still gets its category's `0 | 0 | 0` row in the
Finding tally.

#### Model tiers

The specialists whose checks need the most judgement run on the
**primary** tier: the model this session already uses. That is
`results-and-claims`, `stats-causal`, `data-pipeline-leakage`,
`bioinformatics`, and the Step 3 synthesis, which you do yourself.

The more mechanical specialists, `llm-failure-modes`, `doc-schema-fidelity`,
and `code-quality`, run on the **lighter** tier when the host lets you choose
a model per sub-agent. Pick the host's faster, cheaper general model by the
alias the host documents (on Claude Code, for example, the sub-agent tool's
`sonnet` alias). Never write a versioned model ID into a dispatch. Fall back
to the primary model, without failing the review, when:

- the sub-agent tool has no model choice, or rejects the lighter model;
- you are running the checklists in-line;
- the user passed `--all-frontier`, or asked for every specialist on the
  primary model;
- the session's primary model is already the host's lighter model.

Record what actually ran in the report header's **Sub-agents run** line,
for example `7 specialists in 5 sub-agents (lighter tier, one pass:
llm-failure-modes, doc-schema-fidelity, code-quality)` or `7 specialists in
5 sub-agents, all on the primary model (no per-agent model choice on this
host)`, plus any self-skips.

#### Scoped review

A scoped review runs every checklist **in-line, in this session**, unless
the user asks for sub-agents. Starting five sub-agents costs more than a
narrow focus saves: each one rereads the context and its checklist, and they
overlap on the same few scripts. To keep `results-and-claims` independent
in-line:

1. Run the `results-and-claims` checklist first, on the primary model,
   before reading any other checklist, and write its findings to a scratch
   file outside the repository.
2. Then run `stats-causal`, `data-pipeline-leakage`, `bioinformatics`, and
   the three lighter-tier checklists on the focused files, adding their
   findings without editing the first pass's.
3. Synthesize as usual. Record the mode on the **Sub-agents run** line, for
   example `7 specialists in-line (scoped review; results-and-claims run
   first)`.

With `--focus`, every specialist pass receives the same contract:

- Review the focused conclusions, their producing scripts, and their tables
  (or the focused files and the conclusions that use them) in full.
- Treat `--cleared` items, and everything else outside the focus, as
  read-only boundary context. Do not file standalone findings on them.
- Exception: when something inside the focus exposes a shared-input flaw, a
  definition mismatch, or a direct logical contradiction that invalidates a
  cleared or out-of-focus item, file it as a boundary finding (category
  starting `boundary:`) that names the affected item.

A scoped review is cheaper, not a substitute for a full one: dependencies
nobody declared are exactly what it can miss. Recommend a full review before
the results are reported or shared.

### Step 3 — Synthesize

Read `skills/core/references/review/synthesis.md` and follow it. The short
form:

1. Aggregate findings across sub-agents and dedupe. Two sub-agents
   flagging the same line with different framings is the common case —
   keep the more actionable framing and add a one-line "see also" if the
   other framing adds value.
   One root cause gets one finding ID even when it has several consequences,
   affected files, or remediation steps. Nest those details under that finding;
   do not inflate the total by splitting one defect into multiple framings.
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
6. Group findings by category (the seven specialist areas), and within each
   category list Major findings first, then Minor. For a scoped review, apply
   the scoping rules in `synthesis.md` before numbering.
7. Number findings F1, F2, F3 ... globally so the Key decisions and
   Questions sections can link to them where useful.
8. Derive every reported count from the final `##### F<n>.` headings. Add the
   required Finding tally table, then run the deterministic validator from the
   active skill bundle (resolving `skills/core/scripts/validate_review_report.py`
   relative to the plugin root two directories above this `SKILL.md`, not
   `.mycelium/plugin-root`) before reporting or writing a handoff:
   `python3 "<plugin-root>/skills/core/scripts/validate_review_report.py" <report>`.
   If it fails, repair IDs, severities, or tallies and rerun it. Never copy an
   earlier draft's hand-count into chat or `.mycelium/last-session.md`.

### Step 4 — Render the report

Write the report to a file under `.living/outputs/reviews/` named
`YYYY-MM-DD-<scope-slug>.md` (e.g., `2026-04-24-pr-127.md`,
`2026-04-24-working-tree.md`, `2026-04-24-melanoma-focus-c7-c8.md` for a
scoped review). The structure:

```markdown
# Review — <scope> — YYYY-MM-DD

**Scope**: <PR / commit range / working tree / pasted diff / analysis directory>
  (scoped review: add `; focus: C7, C8; cleared: C1–C6`)
**Files reviewed**: N
**Sub-agents run**: 7 specialists in <n> sub-agents (lighter tier: <names>,
  or "all on the primary model"; list any skipped and why)

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

### Empirical claims & results
#### Major
##### F1. <short description>
`<results doc>:<line>`; table `<outputs/tables/file.csv>`
```text
<1-5 lines of results prose, verbatim>
<contradicting table rows, or a compact recomputation result>
```
**Why it matters here**: ... (say whether the conclusion's core verdict holds)
**Fix**: ...

### Statistics & causal inference
#### Major
##### F2. <short description>
`<file>:<line>`
```python
<1-5 lines verbatim>
```
**Why it matters here**: ...
**Fix**: ...

#### Minor
##### F3. ...

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
- **Empirical claims & results**: <one sentence, or the self-skip line>
- **Statistics & causal inference**: <one sentence>
- ...

## Finding tally

| Category | Major | Minor | Total |
| --- | ---: | ---: | ---: |
| Empirical claims & results | N | N | N |
| Statistics & causal inference | N | N | N |
| Data pipeline & leakage | N | N | N |
| Bioinformatics | N | N | N |
| LLM coding antipatterns | N | N | N |
| Documentation & schema fidelity | N | N | N |
| Code quality | N | N | N |
| **Total** | **N** | **N** | **N** |

## Notes
- Cross-cutting observations: compound findings sharing one remediation
  path, "did this code ever run" questions, etc.
```

Print the path of the written file at the end. The chat reply should
surface the validator-confirmed count of Major findings per category and the
"Key decisions" list so the user sees the shape of the report without opening
the file.

### Step 5 — Offer tripwires (opt-in behavioral follow-up)

Read `skills/core/references/review/deep-tripwires.md` and follow it.
The short form (two terms before the steps: an **audit** is the
*default artifact* — a written document that walks the analysis, names
each tripwire that would apply, names the perturbation and expected
outcome, links each to a static finding, and surfaces what's missing
if you wanted to execute. No code runs. **Instrumentation** is the
four observability hooks the pipeline needs for tripwires to actually
execute: checkpoint emission, `--stop-after`, `analysis_labels.yml`,
and a drop ledger. Definitions and the full mode table are in
`deep-tripwires.md` "Three operating modes"):

1. Scan the static findings for `suggested_tripwire` tags emitted by
   the sub-agents (see the output contract in `review/README.md`).
2. Detect whether the repo has any of the four instrumentation hooks
   (checkpoint logging anywhere in the analysis scripts, a
   `--stop-after` / `STOP_AFTER_CHECKPOINT` mechanism, an
   `analysis_labels.yml` or equivalent at the analysis root, a drop
   ledger of any name/shape, an existing tripwire runner under
   `tools/`). Names and formats are illustrative — accept what the
   project uses. This picks the default mode: `audit` if zero or
   only some hooks are present, `scaffold` if the user asks to add
   them, `run` if all four are present.
3. Use `AskUserQuestion` to offer a menu. **Lead every user-facing
   surface with plain English; keep internal IDs in parentheses or
   in artifacts.** See `deep-tripwires.md` "Talking to the user" for
   the gloss table. Examples of well-phrased options: "Describe what
   we'd test (no code runs)" rather than "audit mode"; "Propose
   project-specific patches that would let us actually run the
   tests" rather than "scaffold mode". Always include "skip" and the
   audit option. Include "scaffold" if the hooks are partial or
   absent. Include "run selected tripwires" if all four hooks are
   present. The list of named tripwires on the menu is shaped by
   which `suggested_tripwire` tags appeared in the static findings,
   plus the starter four (missing counts, missing metadata sample,
   label permutation, toy contrast direction) which are always
   available. When naming individual tripwires in the menu, use the
   plain-English glosses ("the report-numbers-still-match check")
   not the internal IDs.
4. Execute the chosen mode per `deep-tripwires.md`. Scaffold-mode
   and run-mode are **agent-improvised per analysis** — the skill
   ships principles + examples, and the agent reads the project's
   language / layout / existing helpers and adapts. Do not stamp
   templates blindly; if the project already has a logging or
   filter helper, build on it. Output paths:
   - audit → `.living/outputs/reviews/YYYY-MM-DD-<scope-slug>-tripwires.md`
   - scaffold → `…-tripwires-scaffold.md` (proposal, not auto-applied)
   - run → `…-tripwires-run.md` (pass/fail per tripwire)

Print the written path(s) at the end.

**Reporting rule (applies to every mode).** When summarizing the
result back to the user in chat, lead with one English sentence
("Three places in your docs cite p-values that don't match the
source CSV"), then show the table / artifact as evidence. Don't
open with `Instrumentation detected: 0/4` or raw checkmark/X
output. The user wants to know what failed and why before they
need the test IDs.

Skip Step 5 entirely if the diff is a pure refactor with no data-flow
change AND no documentation change, or if the user invoked the skill
with a flag asking to suppress tripwires (e.g., `/mycelium:review
--no-tripwires`).

**Documentation-only diffs are NOT a skip case.** When report text or
analysis docs change without the pipeline changing, that's exactly
when the report-values-freshness tripwire is most useful — it catches
"someone edited the report number without regenerating the source
CSV" silently. For doc-only diffs, restrict the offered menu to the
freshness and known-answer categories (the fault-injection and
metamorphic tripwires don't apply when the pipeline wasn't touched)
rather than skipping Step 5.

### Step 6 — Post-action hook

Treat a review as a significant action: log a short entry to
`.living/learnings.md` if the review surfaced a recurring pattern (e.g., "the
analyze script is using `t-test` on count data — this is the third time this
pattern has come up, consider a convention"). Derive `mitigation_type` by the rule in `skills/core/templates/learning-entry.md`: `structural` if a shipped test, assertion, type constraint, frozenset, or schema validation enforces it; otherwise `convention` if a `.living/conventions.md` entry covers it; otherwise `ambient-awareness`. Otherwise no logging is needed.

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
- `skills/core/references/review/deep-tripwires.md` — behavioral
  follow-up (Step 5)
- Per-agent checklists under `skills/core/references/review/` (seven files;
  loaded by sub-agents, not by you)
- The mycelium core `Post-Action Hook Protocol` from `../core/SKILL.md`
  governs what to log to `.living/` when a review surfaces a recurring
  pattern
