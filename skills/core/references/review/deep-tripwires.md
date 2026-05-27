# Deep tripwires — behavioral follow-up to static review

This file is consulted by the main `/mycelium:review` skill at Step 5
("Offer deep behavioral checks"), after the six static sub-agents have
returned findings and synthesis has rendered the report. Static review
reads code; tripwires perturb inputs and observe pipeline behavior.
They are intentionally heavier, so the user opts in.

A **tripwire** is a behavioral test that injects a deliberate
perturbation at the pipeline's input and asserts the pipeline either
breaks at the right place or stays invariant where it should. The
umbrella name is "tripwire"; when specificity matters, refer to
individual tests by their category (e.g. "the label-permutation
metamorphic tripwire", "the missing-counts fault-injection tripwire").

## Where this fits

```text
Cheap, frequent (already in the skill):
  static lint
  six review sub-agents
  synthesis

Heavier, opt-in (this file):
  fault-injection tripwires
  metamorphic tripwires
  known-answer tripwires
  freshness tripwires
```

Static review asks "does the code contain suspicious patterns?"
Tripwires ask "does the pipeline actually fail when bad inputs are
injected, and do labels actually fail to influence blind outputs?"
Both are useful; they catch different failure modes.

## Core principle

```text
Bad inputs must not pass named scientific boundaries.
Labels must not influence blind outputs.
```

A static reviewer can flag a suspicious `try/except` around a data
loader; only a tripwire can confirm whether the loader actually falls
back silently when the file is missing.

## Tripwire categories

Three (plus one) flavors. Pick category names when you need to be
precise about what a tripwire *does*; otherwise just say "tripwire".

| Category | What it does | Examples |
|----------|--------------|----------|
| **fault-injection** | Corrupt, remove, or replace an input; pipeline should error or refuse to advance | `missing-counts-file`, `missing-metadata-sample`, `duplicate-sample-id`, `synthetic-data-substitution`, `partial-api-response`, `magic-label-injection` |
| **metamorphic** | Change something that shouldn't matter; designated output should be invariant | `label-permutation`, `shuffled-sample-order` |
| **known-answer** | Run on toy data with a pre-specified expected result; check the answer | `toy-contrast-direction` |
| **freshness** (related, not always called a tripwire) | Verify derived artifacts came from current inputs by fingerprint | `report-values-freshness`, `silent-sample-drop` (no-ledger variant) |

Metamorphic testing has a real literature in ML testing; fault
injection is well-known in systems testing; known-answer tests are
standard in numerical software. Use the proper names when the
distinction matters.

## Talking to the user

This file is full of technical vocabulary — "fault-injection",
"metamorphic", "provenance check", "T1", "instrumentation", "audit
mode". That vocabulary is **load-bearing internally** (it lets
sub-agents emit precise `suggested_tripwire` tags, and it lets one
tripwire be cross-referenced from another). It is **the wrong
vocabulary to lead with when speaking to the user.**

The dual-layer rule:

- **Internal artifacts** (audit documents, finding tags, runner
  output schemas, cross-references between tripwires) keep the
  precise names. A tripwire's stable identifier is its slug —
  `missing-counts-file` (category: `fault-injection`). The hook is
  `checkpoint emission`. These names are the API; they don't
  change between documents.
- **User-facing prose** uses plain-English glosses. The
  `AskUserQuestion` menu options, the lead sentence of any results
  summary, and the first paragraph of any artifact a user reads
  should describe the failure mode the tripwire watches for, not
  the test's internal name.

Suggested translations:

| Internal name | User-facing gloss |
|---|---|
| audit mode | "describe what we'd test, no code runs" |
| scaffold mode | "propose project-specific patches that would let us actually run the tests" |
| run mode | "execute the tests and report pass/fail" |
| instrumentation | "the hooks the project needs in order to run these tests" |
| starter check | "what you can check today without changing any code" |
| provenance check | "did the source files change since the report was last built?" |
| citation mismatch | "the numbers in the docs no longer match the source CSV" |
| fault-injection tripwire | "deliberately break an input and watch for silent fallbacks" |
| metamorphic tripwire | "change something that shouldn't matter and check the output didn't change" |
| known-answer tripwire | "run on a tiny known-result dataset and check the answer" |
| freshness tripwire | "verify the values in docs were derived from the current data" |
| `report-values-freshness` | "the report-numbers-still-match check" |
| `missing-counts-file` | "the missing-input-file blow-up check" |
| `label-permutation` | "the labels-don't-leak-into-blind-analysis check" |
| `toy-contrast-direction` | "the comparison-direction-not-flipped check" |

**Result presentation rule.** Lead with one English sentence ("Three
places in your docs cite p-values that don't match the source CSV"),
then show the table as evidence. Don't open with `Instrumentation
detected: 0/4` — open with "your project doesn't have the hooks these
tests need yet, here's what we can do anyway".

**Don't suggest "do one thing first" unless something depends on it.**
A clean analysis needs every flagged issue fixed; suggesting the user
"start with X" implies they might cherry-pick, which they won't.
Staging advice is only useful when one fix *enables* another (e.g.,
"apply patch A first because patches B and C source the helper it
adds"). Otherwise just list the patches and let the user fix all of
them.

**Numbering rule.** Number tripwires within each audit document as
`tripwire1, tripwire2, tripwire3 ...` — spelled out, lowercase, in
order of appearance. The number is per-document and only exists for
cross-references inside that document; the **stable identifier** is
the slug (`report-values-freshness`, `missing-counts-file`, etc.),
which is what sub-agents use in `suggested_tripwire` tags and what
synthesis cross-references. Don't use `T1` / `P1` / etc. — the
single-letter prefixes are opaque to readers who haven't memorized
the convention.

When *introducing* a tripwire in user-facing prose, lead with the
plain-English name and follow with the slug in parentheses:
"the report-numbers-still-match check (tripwire1, slug
`report-values-freshness`, freshness category)".

## Implementation philosophy

This skill ships **principles and examples, not templates**. Every
analysis has a different language (R, Python, Julia, shell), a
different layout, different input shapes, and a different idea of
what a "checkpoint" means. A rigid template would be wrong for most
projects.

So the contract is:

- The skill describes what each piece needs to **achieve** (what the
  checkpoint log should let you ask, what a label declaration should
  let a tripwire permute, what a drop ledger should let an auditor
  reconstruct).
- The skill ships **examples** in the most common languages so the
  agent has something concrete to adapt from.
- **The agent improvises** the actual code/config for the project at
  hand. Don't copy-paste an example without reading the surrounding
  code; if the project already has a logging helper, build on it
  rather than introducing a parallel one.

This means scaffold-mode and run-mode are not "press a button and
patches appear." They're "the agent reads the project, decides what
shape the hooks should take given the existing code, and proposes /
implements them." If you're invoking this skill, you are the agent —
read the project before writing anything.

## Three operating modes

Two terms worth grounding before the table, because both get used as
shorthand throughout this file.

- **Audit** = the document this skill produces by default. An audit
  walks the analysis, identifies which tripwires would apply, names
  the perturbation and the expected outcome for each, links each
  back to a static finding (where one exists), and surfaces what
  instrumentation is missing if you wanted to execute. The "plan"
  half of the audit names *what* would be done; the "audit" half
  names *what's there and what isn't*. No code is executed and no
  inputs are perturbed when running in audit mode. Think test plan,
  not test run.
- **Instrumentation** = the observability hooks the analysis
  pipeline needs so a tripwire can actually execute against it.
  Borrowed from systems engineering ("instrumenting" code = adding
  logs / metrics / traces so an outside observer can watch what it
  does at runtime). For tripwires specifically, four pieces:
  *checkpoint emission* (JSONL log lines at named boundaries),
  *`--stop-after`* (a way to halt at a chosen checkpoint),
  *`analysis_labels.yml`* (declares which columns are labels), and
  a *drop ledger* (a written record of every sample dropped with
  reason). Without these, tripwires can be audited but not executed.

The main skill picks one mode based on what it detects in the repo
and what the user asks for.

| Mode | When | Output |
|------|------|--------|
| **audit** (default) | Most analyses — none or some of the four instrumentation hooks present | A document (the audit & plan) listing the tripwires that would apply, what each would perturb, which checkpoint should block, and what instrumentation is missing |
| **scaffold** | User wants to add the instrumentation hooks so future runs can execute | A proposal document with project-specific patches — checkpoint helper, `--stop-after` wiring, `analysis_labels.yml`, drop-ledger helper, optional `Makefile` target. Patches are improvised to fit the project's language/layout, not stamped from a template |
| **run** | The instrumentation hooks exist (or the agent will improvise a project-specific runner inline) | Execute the selected tripwires, write pass/fail per tripwire, cite the checkpoint that should not have passed (or the artifact that changed when it shouldn't have) |

Audit-mode is the default and the most useful first step regardless
of where the project is. Writing the audit is how the agent
*discovers* what instrumentation hooks the project would need —
scaffold-mode is harder to do well without an audit first.

## Instrumentation detection

To pick the operating mode, check the repo for:

| Signal | Implies |
|--------|---------|
| Main analysis script writes a structured checkpoint log (JSONL, CSV, or equivalent) | Some instrumentation present |
| A `--stop-after` flag or `STOP_AFTER_CHECKPOINT` env var (any shape) | Can run tripwires cheaply without re-executing the full pipeline |
| `analysis_labels.yml` (or any project-specific label declaration) at analysis root | Metamorphic label tripwires can permute automatically |
| A drop ledger file (TSV / CSV / JSONL, any name) recording silent sample loss | Sample-drop tripwires have ground truth to audit |
| A `tools/run_tripwires.py` / `run_tripwires.R` / `make deep-review` target | Repo already has a tripwire runner; pick `run` mode |

Names and formats are illustrative — accept whatever shape the
project already uses. If a project logs checkpoints to a CSV with
different column names than the examples in this file, that's fine;
the audit just needs to know how to read it.

If none of these are present, default to **audit**. If the user
explicitly asks to instrument, switch to **scaffold**.

## Standard checkpoint vocabulary

A tripwire asserts that some checkpoint did or did not appear in the
structured log, or that a designated artifact matches between two runs.
The vocabulary the skill suggests (analyses can adopt a subset, or
substitute project-specific names):

| Checkpoint | Meaning | Should fail if |
|------------|---------|----------------|
| `raw_data_available` | Required raw inputs loaded | File missing, API empty, hash mismatch |
| `samples_reconciled` | Counts / expression and metadata aligned by key | Missing metadata samples, duplicate IDs, positional alignment |
| `analysis_matrix_complete` | All filtering applied, drops recorded | Silent sample drop, silent imputation, synthetic-data substitution |
| `blind_qc_ready` | Input to blind QC contains no label columns / label-derived features | Label file opened, forbidden term present, label column used |
| `blind_qc_complete` | Blind QC outputs computed | (Used as the comparison artifact for metamorphic tripwires) |
| `design_matrix_complete` | DE / supervised design explicit | Missing reference level, reversed target/reference, silent sample exclusion |
| `report_values_ready` | Report numbers regenerated from current outputs | Stale values, source fingerprint mismatch, contrast phrasing out of sync with code |

Each checkpoint emits a structured line, e.g.:

```json
{"checkpoint":"samples_reconciled","status":"passed","n_samples":48,"sample_id_hash":"b901","n_samples_dropped":0}
{"checkpoint":"blind_qc_ready","status":"passed","metadata_columns_used":["sample_id","batch","rin"],"label_columns_used":[]}
```

A tripwire then asserts presence/absence of `checkpoint` lines, or
equality of a comparison artifact between two runs. **The schema
above is a suggestion, not a requirement** — if a project already
emits checkpoints with different field names, adapt to them.

## The starter four

If the user has never run a tripwire on this analysis, propose only
these four. Adding more comes later.

1. **`missing-counts-file`** (fault-injection) — corrupt the input
   path; `raw_data_available` should not appear.
2. **`missing-metadata-sample`** (fault-injection) — drop one row
   from the metadata; `samples_reconciled` should not appear.
3. **`label-permutation`** (metamorphic) — shuffle declared label
   columns; blind QC input hash (or PCA distance matrix) should be
   unchanged.
4. **`toy-contrast-direction`** (known-answer) — a tiny synthetic
   dataset with a known sign; the DE result for the marker gene
   should be positive in the intended direction.

The first two confirm fallback / silent-drop behavior. The third
confirms label firewall. The fourth catches reversed
target/reference — a common LLM-coding error.

## Mapping static findings to tripwires

Sub-agents emit findings with an optional `suggested_tripwire` field.
The main skill aggregates those tags and composes the offer menu.
Common mappings:

| Static finding pattern | Suggested tripwire | Category | Checkpoint that should block |
|------------------------|--------------------|----------|------------------------------|
| `try/except` around data load, fallback to cache | `missing-counts-file` | fault-injection | `raw_data_available` |
| Join without `validate=`; positional sample alignment | `missing-metadata-sample`, `duplicate-sample-id` | fault-injection | `samples_reconciled` |
| Silent `dropna`, `fillna`, or imputer fit on full data | `silent-sample-drop`, `synthetic-data-substitution` | fault-injection / freshness | `analysis_matrix_complete` |
| Label column read before PCA / HVG / clustering | `label-permutation`, `magic-label-injection` | metamorphic / fault-injection | `blind_qc_ready` |
| Target / reference assignment looks fragile | `toy-contrast-direction` | known-answer | `design_matrix_complete` |
| Report values regenerated by hand; no fingerprint | `report-values-freshness` | freshness | `report_values_ready` |
| API call with broad `except`; partial response possible | `partial-api-response` | fault-injection | `raw_data_available` |

If no static finding suggests a tripwire, still offer the starter
four — the absence of a finding is not proof of behavioral safety.

## Audit-mode output

When the user picks audit-mode, write a single Markdown document to
`.living/outputs/reviews/YYYY-MM-DD-<scope-slug>-tripwires.md`
(date-first, matching the static report path so the two sort
together). Shape:

```markdown
# Tripwire audit & plan — <scope> — YYYY-MM-DD

**Static review**: `.living/outputs/reviews/<corresponding-review>.md`
**Mode**: audit (this document describes what would be tested; no
code was run and nothing was modified)
**Hooks the project has**: none yet / partial (list which) / full

## What this audit does

Walks the analysis, names the failure modes worth testing, says what
each test would actually *do*, and points at the cheapest thing the
user can check today without any setup.

## Tests that would apply here

### tripwire1. The report-numbers-still-match check
**Watches for**: doc values that no longer match the source CSVs
they were derived from (stale numbers in the report)
**How it'd work**: fingerprint every CSV cited as a source in the
report, recompute the fingerprint at audit time, flag any doc that
quotes a value disagreeing with its registered source
**Slug**: `report-values-freshness` (freshness category)
**Related static finding**: F1 (report quotes p-values that disagree
with on-disk CSV)
**Today**: cannot run end-to-end — no value-to-source registry exists
**Starter check** (runnable today, no setup): a ~30-line script that
greps the report for the cited p-values and confirms each matches the
corresponding CSV row. Catches the worst cases (large mismatches)
imperfectly but usefully.

### tripwire2. The labels-don't-leak-into-blind-analysis check
**Watches for**: a "blind" analysis step that secretly uses labels
**How it'd work**: shuffle the label column row-wise, rerun the
blind step, confirm the output is identical between original and
shuffled
**Slug**: `label-permutation` (metamorphic category)
**Related static finding**: F7 (label read before HVG selection)
**Today**: cannot run — no label declaration exists
**Starter check**: write a 30-line script that loads metadata,
permutes the label column, runs the blind step on each, diffs the
output

### tripwire3. ...

## Hooks this project would need to run the rest

These tests can be executed only when the analysis pipeline emits
enough signal for them to observe. The four hooks below are the
minimum; without them, this audit is the most the skill can do today.

- A **checkpoint log** — the pipeline writes one machine-readable
  line per named scientific boundary it crosses (e.g. "samples
  reconciled", "blind QC ready"). JSONL is convenient, CSV or any
  structured format works.
- A **stop-after** option — a way to tell the pipeline to halt at a
  chosen boundary instead of executing the whole pipeline. Cheap to
  add once the checkpoint log exists.
- A **label declaration** — a file naming which columns of which
  files count as labels, so the labels-don't-leak check knows what
  to shuffle.
- A **drop ledger** — a file recording every sample dropped at any
  filter step, with reason. So the silent-drop check has ground
  truth to compare against.

See scaffold-mode for project-specific patches that add these.

## What this audit does NOT cover

- Reproducibility of final figures (that's a CI concern)
- Numerical regression vs a prior run (that's a snapshot test)
- Performance / runtime regressions
```

Every audit should include the **Starter check** field per tripwire —
it's the part the user can act on today. The starter check names what
can be done without full instrumentation; often it's a 10–50 line
script that catches the relevant failure mode imperfectly but
usefully. Lead with that, not with the instrumentation gap, because
the starter check is what the user can take away even if they invest
in nothing else.

## Scaffold-mode output

When the user picks scaffold-mode, the agent reads the project (its
language, entry points, existing helpers, output directory
conventions), then writes a **proposal document** at
`.living/outputs/reviews/YYYY-MM-DD-<scope-slug>-tripwires-scaffold.md`
containing concrete patches. Do not auto-apply — present as
suggestions for the user to apply.

The scaffold proposal covers four contracts. For each, the agent
adapts the shape to the project. Examples below; do not copy them
verbatim.

### Contract 1 — Checkpoint emission

**What it needs to achieve**: one structured record per named
scientific boundary the pipeline crosses, written to a single file
the tripwire runner can read. The record must include the checkpoint
name and enough context to verify the boundary held (counts, hashes,
column lists, whatever is relevant). Records are append-only.

**Example (R)**:

```r
emit_checkpoint <- function(name, ...) {
  fields <- list(checkpoint = name, status = "passed", ...)
  line <- jsonlite::toJSON(fields, auto_unbox = TRUE)
  path <- Sys.getenv("CHECKPOINT_LOG", "build/checkpoints.jsonl")
  cat(line, "\n", file = path, append = TRUE, sep = "")
  if (identical(Sys.getenv("STOP_AFTER_CHECKPOINT"), name)) {
    quit(save = "no", status = 0)
  }
}
# Usage at each boundary:
emit_checkpoint("samples_reconciled",
                n_samples = nrow(meta),
                n_dropped = sum(!meta$ok),
                sample_id_hash = digest::digest(sort(meta$sample_id)))
```

**Example (Python)**:

```python
import json, os, sys
def emit_checkpoint(name, **fields):
    rec = {"checkpoint": name, "status": "passed", **fields}
    path = os.environ.get("CHECKPOINT_LOG", "build/checkpoints.jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    if os.environ.get("STOP_AFTER_CHECKPOINT") == name:
        sys.exit(0)
# Usage:
emit_checkpoint("samples_reconciled", n_samples=len(meta),
                n_dropped=int((~meta.ok).sum()))
```

If the project already has a logging helper, build on it. Don't
introduce a parallel logging path.

### Contract 2 — `--stop-after` support

**What it needs to achieve**: a way to tell the pipeline to halt
exactly after a named checkpoint emits, so a tripwire doesn't have
to run the full pipeline to check a boundary. Easiest implementation:
have `emit_checkpoint()` itself check an env var and exit when
matched (see the examples above — both already do this).

If the pipeline has multiple entry points (e.g., a Makefile that
chains several scripts), `STOP_AFTER_CHECKPOINT` should propagate to
all of them. The agent may need to wire it through each entry.

### Contract 3 — Label declaration

**What it needs to achieve**: a single file that names, for each
label column the analysis uses, where it lives and what its values
are. Tripwires read this to know what to permute and what terms to
treat as forbidden in blind stages.

**Example (`analysis_labels.yml`)**:

```yaml
labels:
  treatment:
    file: data/metadata.tsv
    column: treatment
    values: [control, treated]
    synonyms: [condition, group, arm]
  outcome:
    file: data/labels.csv
    column: response
    values: [responder, non_responder]
    synonyms: []

# Stage policy:
stages:
  blind_qc:
    label_policy: forbidden
    outputs_to_compare:
      - build/qc/pca_coords.tsv
      - build/qc/distances.tsv
      - build/qc/hvg.tsv
```

If the project uses something other than YAML (a CSV, a config in a
Python module, a JSON manifest), adapt. The shape matters more than
the file format.

### Contract 4 — Drop ledger

**What it needs to achieve**: every sample, cell, or row dropped at
any filter step writes a record naming the sample ID, the stage, the
reason, and whether the drop is allowed by the analysis's policy. A
tripwire can then audit "did any sample disappear without a
recorded reason?"

**Example (`build/dropped_samples.tsv`)**:

```text
sample_id   stage              reason                    allowed_by_policy
S17         design_matrix      missing treatment label   false
S31         qc_filter          library size too low      true
S42         dedup              duplicate sample ID       false
```

**Helper (R)**:

```r
log_drop <- function(sample_ids, stage, reason, allowed = FALSE) {
  if (length(sample_ids) == 0L) return(invisible())
  rows <- data.frame(sample_id = sample_ids,
                     stage = stage, reason = reason,
                     allowed_by_policy = allowed)
  path <- Sys.getenv("DROP_LEDGER", "build/dropped_samples.tsv")
  has_header <- !file.exists(path)
  write.table(rows, path, append = TRUE, col.names = has_header,
              row.names = FALSE, sep = "\t", quote = FALSE)
}
```

Adapt to the project's idioms. If the project already keeps a
filter-summary CSV, extending it is preferable to introducing a new
ledger file.

### Optional — `Makefile` (or equivalent) target

If the project has a `Makefile` or `tasks.py` / `noxfile.py` /
`justfile`, propose a `deep-review` target that runs the selected
tripwires. The exact target shape depends on the runner (see run-mode).

## Run-mode

Run-mode is **agent-improvised**, not template-driven. The skill
doesn't ship a `tools/run_tripwires.py` because the right runner
depends on the project (language, how the pipeline is invoked, what
the checkpoint log looks like). The agent either:

- writes a project-specific runner (typically 50–150 lines) into
  `tools/` and invokes it, OR
- runs the tripwires inline (one Bash invocation per tripwire) when
  the analysis is small enough that a dedicated runner is overkill.

### What the runner has to do

Regardless of how it's implemented, the runner is responsible for:

1. Knowing which tripwires to run (from CLI args, a YAML config, or
   the audit document).
2. For each tripwire: preparing the perturbed input in a temp
   directory (never mutating the real inputs).
3. Invoking the pipeline with the right env / CLI to use the
   perturbed input, write its checkpoint log to a per-tripwire path,
   and stop at the relevant checkpoint.
4. Reading the checkpoint log and applying the tripwire's assertion.
5. Writing a results record per tripwire.

### Per-category execution logic

**Fault-injection tripwire**:

```text
1. Copy/symlink real inputs to $TMP/
2. Replace the target file with a corrupted/missing version (e.g.
   write an empty TSV, point to /nonexistent/path, drop a row).
3. Run the pipeline with:
     CHECKPOINT_LOG=$TMP/checkpoints.jsonl
     STOP_AFTER_CHECKPOINT=<the checkpoint that should NOT appear>
     <project-specific input overrides>
4. Read $TMP/checkpoints.jsonl. The named checkpoint should NOT be
   present. The tripwire PASSES if absent; FAILS if present.
5. Optionally check the pipeline exited with a non-zero status —
   silent fallbacks often hide behind exit 0.
```

**Metamorphic tripwire**:

```text
1. Two runs in $TMP_A and $TMP_B:
   - $TMP_A: original input, normal run
   - $TMP_B: input perturbed in the way that shouldn't matter
     (e.g., labels permuted, sample order shuffled)
2. For each: STOP_AFTER_CHECKPOINT=<the boundary that should be
   invariant>
3. Compare the designated artifact between the two runs:
   - PCA coordinates: compare after canonical sign alignment
   - Distance matrix: compare with numerical tolerance
   - Selected gene set / module set: compare as sets
   - Sample order: compare as sorted vectors
4. PASS if the artifact is equivalent under the comparison rule;
   FAIL if it diverged.
```

**Known-answer tripwire**:

```text
1. Run the pipeline (or the relevant stage) on the toy fixture.
2. Compare the named output to the pre-specified expected value
   (sign, equality, structure — depending on the test).
3. PASS if match; FAIL otherwise.
```

**Freshness tripwire**:

```text
1. Compute the current fingerprint (sha256 + row count, or similar)
   of every source artifact cited by the report.
2. Compare each against the fingerprint embedded next to the value
   in the report (or, if the report doesn't embed fingerprints,
   against the value the report quotes — round-trip check).
3. PASS if all fingerprints / values match; FAIL on any mismatch.
```

### Run-mode output

Write a results document to
`.living/outputs/reviews/YYYY-MM-DD-<scope-slug>-tripwires-run.md`.
Per-tripwire entry:

```markdown
### T1. missing-counts-file (fault-injection) — **PASS**
**Perturbation**: replaced `data/counts.tsv` with empty file
**Expected**: `raw_data_available` absent from checkpoint log
**Observed**: checkpoint log empty after `data_load` stage; pipeline
exited with `Error: counts file empty (loader.R:31)`
**Reproduction**: `tools/run_tripwires.sh missing-counts-file`
```

```markdown
### T4. label-permutation (metamorphic) — **FAIL**
**Perturbation**: shuffled `treatment` column row-wise
**Expected**: PCA distance matrix identical between original and
shuffled runs
**Observed**: distance matrix differs (Frobenius norm 0.087 > 1e-6
tolerance); label was read by `select_hvg()` (qc.R:84)
**Action**: F7 from the static review is confirmed behaviorally; fix
needed before label firewall can be claimed.
```

If a tripwire was supposed to run but the instrumentation was
incomplete (e.g., the project has a checkpoint log but didn't emit
the specific checkpoint the tripwire watches), record it as
**SKIPPED** with the reason, not PASS or FAIL.

## When NOT to offer tripwires

- Pure refactor with no change to data flow or analytical behavior
  AND no documentation change
- Code that doesn't load any data (utility scripts, plotting helpers)
- The user has already run tripwires in this session for the same scope
- The static review found nothing and the analysis is a one-shot
  exploratory script (offer only the starter four with a quiet note)

**Documentation-only diffs are NOT a skip case.** When report text or
analysis docs change without the pipeline changing, that's exactly when
the freshness-category tripwires are most useful — they catch "someone
edited the doc value without regenerating the source CSV" silently. For
doc-only diffs, restrict the offered menu to freshness and known-answer
tripwires (fault-injection and metamorphic don't apply when the pipeline
wasn't touched) rather than skipping the whole step.

## What tripwires are NOT for

- Replacing the static review — they are complementary
- Catching style / API / documentation issues — that's the static side
- Validating final scientific conclusions — they validate pipeline
  integrity, not the rightness of the result
- Mutating real input data — all perturbations happen in a temp
  directory and the original files are untouched

## Severity language for tripwire failures

When a tripwire trips (i.e., the pipeline failed to do what the
tripwire was watching for), the finding goes back into the
static-review severity ladder, not a new one:

- Tripwire failure on a load-bearing boundary (`raw_data_available`,
  `samples_reconciled`, `blind_qc_ready`, `design_matrix_complete`)
  → **Major**. The pipeline silently tolerated a condition that
  invalidates downstream results.
- Tripwire failure on a soft boundary (`report_values_ready`
  freshness, drop-ledger absence with allowed-by-policy drops)
  → **Minor**. Fix this but the result is probably still correct.
- Tripwire infrastructure absence is not itself a finding. It
  belongs in the audit document's instrumentation-gap section.

## Notes from real use

What we learned the first time this ran end-to-end on a real project:

- **Five hooks beat four.** The skill's standard contract names four
  hooks (checkpoint emission, stop-after, label declaration, drop
  ledger). The first real project needed a fifth: a *report-values
  registry* (every reported number tagged with the source CSV it came
  from, plus a sha256). The freshness tripwire was the highest-value
  one for that project; none of the four standard hooks supported it.
  Lesson: when a project's top static finding is "the docs disagree
  with the data," propose the registry as a project-specific fifth
  hook in scaffold-mode and don't try to bend a standard hook to fit.

- **Lead with the starter check.** In the audit document I generated,
  I led each tripwire with its perturbation/expected/status before
  the starter check. The starter check turned out to be the most
  useful field — it's the one thing the user can act on without any
  setup. Reorder so it appears immediately after the one-line "what
  this watches for".

- **Plain-English names matter for triggering.** Slugs like
  `report-values-freshness` are useful as stable identifiers in
  artifacts and code. But when the skill first surfaces a tripwire
  to the user, the plain-English name ("the report-numbers-still-match
  check") is what makes the user understand whether they want it.
  See "Talking to the user" above.

- **Starter check → registry is a meaningful jump in fidelity.** On
  one project the 30-line starter check found ~75% of the real
  mismatches. Adding the proper registry (one helper-library change,
  one figure-generation refactor, one runner script) eliminated false
  positives, caught additional real mismatches, and added a free
  provenance check. The starter version is genuinely useful as a v1;
  the registry is the right v2 once the value is proven.

- **Stale numbers can live in surprising places.** On one project the
  static review at a specific commit flagged a doc-vs-data mismatch
  in the analysis's main report file. By the time the tripwire ran
  against HEAD, that file had been patched — but the same mismatches
  had silently survived in sibling docs (a STATUS file and the
  project's decisions log). The tripwire caught what the static
  review couldn't have known about, precisely because behavioral
  checks evaluate state-as-of-now rather than state-as-of-commit.

- **Fingerprint-algorithm consistency in registries is load-bearing.**
  When a scaffold-mode patch proposes a freshness registry that
  stores algorithm-prefixed hashes (e.g., `sha256:abc...`), the
  algorithm used to *recompute* the hash at check time must match
  the algorithm used to *write* it. A helper that falls back from
  `sha256` to `md5` when the primary library is missing produces
  `md5:*` strings that will never equal the stored `sha256:*`
  strings — every provenance check then reports STALE even when
  the files haven't changed. Either (a) make the primary algorithm
  a hard dependency and refuse to run without it, or (b) persist
  the algorithm name alongside the hash so the checker can compare
  apples to apples. Worth spelling out in any scaffold-mode patch
  that proposes a freshness registry.

- **Token tightening matters in user-prose checkers.** First version
  of a freshness checker on one project matched a short tag as a
  substring of a longer tag that contained it, and cross-attributed
  citations between two different categories as a result. Lesson
  for any future checker that does prose attribution: tokens must be
  ordered most-specific-first OR use word-boundary matching. Worth
  spelling out in scaffold-mode patches.

## Cross-references

- `commands/review.md` Step 5 — where this is invoked
- `skills/core/references/review/README.md` — the `suggested_tripwire`
  field on the finding contract
- `skills/core/references/review/synthesis.md` — the `**Behavioral
  check:**` line that surfaces a tripwire suggestion under a finding
- `skills/core/references/review/grill-mode.md` — grill asks the
  analyst what should happen; tripwires ask the pipeline what does
