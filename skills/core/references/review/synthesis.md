# Synthesis and severity calibration

This file is consulted by the main `/mycelium:review` skill after the six
sub-agents return findings. The aim of synthesis is to convert raw output
from six independent reviewers into a single report the user trusts enough
to act on.

The dominant failure mode of multi-agent review is *false-positive
inflation* — every agent has a checklist, the same line gets flagged six
different ways, the user gets a wall of low-quality findings, and the
genuinely critical item is buried. Synthesis is what prevents that.

## Inputs

For each sub-agent that ran:
- A list of findings (`severity`, `file`, `line`, `category`, `summary`,
  `evidence`, `why_it_matters`, `suggested_fix`, `confidence`)
- An optional `not_flagged` list (things considered and skipped, with
  reason)

## Output

A single Markdown report (template below) that the user reads top to
bottom.

## Severity ladder

Two levels. Keep it simple.

### Major — fix this

The result is invalid, misleading, or insecure if this isn't addressed.
Examples:
- Train/test contamination; look-ahead bias in time-series modeling
- Wrong reference genome; sample swap implied by the diff
- Conditioning on a collider that flips the sign
- Hallucinated function call in production analysis path
- Differential expression on a single replicate
- Pseudoreplication that inflates effective n by orders of magnitude
- Excel gene-name corruption flowing into downstream
- Credentials checked into the repo
- Missing multiple-comparison correction in a moderate/large-test setting
- p-hacking / forking-paths flexibility visible in the diff
- Double dipping in scRNA-seq DE without acknowledgment
- Causal language for a correlational design
- Smuggled-default parameter on a load-bearing analytical choice
- Caveats present early that have disappeared in the final version
- `try/except`-driven silent fallback to a different model
- Duplicate source of truth on a load-bearing mapping
- Required env var or external service not documented
- Misleading axis truncation in a published figure
- Data points modified to make the analysis "work"
- Fabricated numbers in a report or commit message

### Minor — consider improving

Doesn't change the conclusion or invalidate the result, but is worth
addressing:
- Missing CIs on a clearly-significant primary estimate
- Hard-coded magic numbers that should be named
- Missing units in axis labels; unlabeled error bars
- Boolean-flag pair refactor opportunity
- Inconsistent logging level
- Stale TODOs or comment freshness
- Premature abstraction with one user
- Naming preferences, code style
- Smuggled-default parameter on a non-load-bearing knob

If it's a stylistic nit a linter would catch, don't include it. There's
no separate "Nit" bucket — those just don't make the report.

## Synthesis steps

### Step 1 — Aggregate

Read all sub-agent outputs. For each finding, attach the sub-agent name
so you can attribute and dedupe.

### Step 2 — Dedupe

Two findings are duplicates if they refer to the same `file:line` range
and the same root cause, even if framed differently. When duplicates
exist:
- Keep the framing that's most actionable — concrete fix preferred over
  abstract concern
- If one agent has higher confidence, prefer that agent's framing
- If the same root cause shows up under two natural categories
  (e.g. pseudoreplication is both stats-causal and bioinformatics),
  place it under the category that matters most for *this* analysis
  and add a one-line "see also" cross-reference to the other category

### Step 3 — Recalibrate severity

Re-grade each finding against the Major/Minor ladder. Common
recalibrations:

- A `major` finding with `low` confidence should usually be demoted to
  `minor` and tagged "verify"
- A `major` finding already documented in `.living/decisions.md` as an
  intentional choice should be demoted with a note ("flagged but
  acknowledged in decisions.md") — unless the justification is wrong
- A `minor` finding flagged independently by multiple agents may be
  worth promoting — duplicate signal across agents is a reliability
  boost

### Step 4 — Identify key decisions

Independent of findings, list the consequential analytical decisions in
the work — choices that, if changed, would meaningfully change the
result. The category tags map roughly to grill-mode (see
`grill-mode.md`):

- Estimand and question framing
- Sample / cohort definition (inclusion/exclusion)
- Reference / comparator / baseline (genome, baseline arm, etc.)
- Variable definitions (continuous vs categorical, outcome construction)
- Filtering and QC thresholds (mt%, gene count, outlier rules)
- Normalization / preprocessing
- Model / test choice
- Multiple-comparison handling
- Adjustment / confounding / DAG
- Train/test or CV strategy
- Robustness / sensitivity coverage

For each decision actually made in the analysis, write one line. If a
decision has an associated finding, link it (`see F2`). If it's
informational (no finding, but worth surfacing because the user might
want to revisit it), say so.

This section replaces the older "Top three asks" pattern. The user will
typically fix everything anyway; what helps them more is seeing every
load-bearing choice in one place.

### Step 5 — Draft questions for the analyst

Distinct from "Key decisions" (which lists the choices the analysis
*made*), this is a short list of meta-level questions whose answers
would change which findings matter most. The point is to surface
ambiguity that the diff alone can't resolve — what's the analysis for,
who's downstream of it, what kind of replicate is this, what's the
acceptable false-positive rate, etc. A few worked examples by domain:

- scRNA-seq DE study: "Is the goal markers for known cell types (use a
  reference-based annotator and skip de-novo cluster DE) or discovery
  of novel subpopulations (then double-dipping must be handled
  rigorously)?" — "Are the 3 donors technical replicates of one tumor,
  or 3 separate patients?" — "What's the downstream use of the marker
  list — wet-lab validation, paper figure, or hypothesis generation?"
- ML model: "What's the deployment context — does the model see the
  same customers in production as in training, or only new ones?" —
  "What's the cost of a false positive vs a false negative here?"
- Causal study: "Is the claim being made a causal one ('X reduces Y')
  or an associational one ('X is associated with lower Y')?"
- Trial-style analysis: "Was this the registered analysis, or did the
  pre-registration specify something different?"

Three to five questions is plenty. The goal is to help the user pause
and think, not to subject them to an interview. If the analysis is
totally unambiguous on these axes (rare), this section can be a single
line: "No outstanding questions — the analysis is clear about its
goal and its replicate structure." Don't pad.

### Step 6 — Reassurance

For each sub-agent that ran, include one sentence in "What was checked
but is fine." If the sub-agent returned nothing, that's the message. If
it returned only minor things, summarize the *categories* it covered
without findings. This builds trust by making absent findings legible.

### Step 7 — Render

Use the template in the next section. Read the per-finding rendering
rules carefully — every finding must include a code snippet, not just
a `file:line` reference.

## Report template

```markdown
# Review — <scope> — YYYY-MM-DD

**Scope**: <PR / commit range / working tree / pasted diff>
**Files reviewed**: N
**Sub-agents run**: 6 (or list which were skipped and why)

## Key decisions in this analysis

The consequential analytical choices in this work. Some have associated
findings below; others are informational so you can decide whether to
revisit them.

- **<Decision name>** — <one-line description of the choice>. <"See F2"
  if there's a finding, or no link if informational>
- **<Decision name>** — ...
- ...

## Questions for the analyst

Things the diff alone can't tell us, whose answers would change which
findings matter most. Worth a quick conversation before scoping the
rework.

- <Open-ended question about analysis goal / replicate structure /
  downstream use>
- <Open-ended question>
- ...

## Findings

### Statistics & causal inference

#### Major

##### F1. <short description>

`<file>:<line>`
```python
<1-5 lines verbatim from the source — enough to see the issue>
```
**Why it matters here**: <one or two sentences specific to this
analysis>.
**Fix**: <one sentence — what to do>.

##### F2. ...

#### Minor

##### F3. ...

### Data pipeline & leakage

#### Major

##### F4. ...

#### Minor

...

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
- **Data pipeline & leakage**: ...
- **Bioinformatics**: ...
- **LLM coding antipatterns**: ...
- **Documentation & schema fidelity**: ...
- **Code quality**: ...

## Notes

<Optional cross-cutting observations: compound findings sharing a single
remediation path, "did this code ever run" questions, observations about
the analysis process itself, sub-agents that struggled.>
```

### Rendering rules per finding

1. **Heading** is a single-line description: short, concrete, no jargon
   from the agent's internal taxonomy.
2. **First line under the heading** is the file path and line range only.
3. **The code block** must contain 1–5 lines copied verbatim from the
   source — enough that a reader sees the issue without opening the
   file. If the issue is a missing thing (no doublet detection, no
   multiple-comparison correction), show the surrounding code where
   the missing thing should appear and add a comment line marking the
   absence: `# (no doublet detection step before this)`.
4. **Why it matters here** is one or two sentences specific to *this*
   analysis. Don't recite general best practice.
5. **Fix** is one sentence. If the fix is multi-step, name the
   approach in one sentence and link to a per-agent reference for
   detail.
6. **Behavioral check** (optional): if the sub-agent attached a
   `suggested_tripwire` tag to the finding, render a one-line
   `**Behavioral check:**` pointer naming the tripwire, e.g.
   `**Behavioral check:** `missing-counts-file` fault-injection
   tripwire (see Step 5)`. Step 5 of the parent skill collects these
   tags to compose the tripwire offer menu.

If a finding *cannot* point to specific source lines (e.g., "no random
seed anywhere"), still pick the most representative location and show
the surrounding code, with the rendering rule above's missing-thing
comment.

### Numbering

Number findings F1, F2, F3 ... globally across the whole report so
that the "Key decisions" section can link to them and so the user can
say "F4 is fine, F7 is wrong" in follow-up.

## Confidence handling

Findings with `confidence: low` should:
- Never be promoted to Major without independent confirmation
- Be tagged `(verify)` after the heading
- Be omitted entirely if they would otherwise have been Minor

Findings with `confidence: high` flagged by only one agent should still
be included — high-confidence single-agent findings are common and
valuable (each agent has a domain the others don't see).

## What to do when no findings exist

A clean review is a valid result:

```markdown
# Review — <scope> — YYYY-MM-DD

## Key decisions in this analysis

- **<Decision>** — ...
- ...

## Questions for the analyst

- <Open-ended question> (or "No outstanding questions" if the analysis
  is unambiguous)

## Findings

No findings.

## What was checked but is fine

- **Statistics & causal inference**: ... (one sentence per agent)
- ...

## Notes

...
```

Don't pad. Trust is partly built by being willing to say "this looks
good."
