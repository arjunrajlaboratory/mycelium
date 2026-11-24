# Reportable Values — capturing numbers at analysis time so the report can't drift

The `register_value` helper is mycelium's mechanism for keeping reports honest about the numbers they cite. The same pattern works for thresholds, contrast phrases, and any short scalar a report would otherwise type by hand and forget to update.

## Why this matters

Reports drift. A number gets typed into prose, then the analysis re-runs with one more cell filtered out, and now the prose says `48` while the analysis output says `47`. Scilintr can't catch this — it only sees code. The blind numerical re-verify (Phase 6 of report-generator) does catch it, but only when the report is rewritten. The cheaper, faster catch is **at lint time**, via [scitexlintr](https://github.com/arjunrajlaboratory/scilintr/tree/main/tex/scitexlintr).

For scitexlintr to do its job, the report needs a manifest of every reportable value. `register_value` is how analyses contribute to that manifest.

## The flow

```text
your analysis script
  └── register_value("n_samples", 48)
        ↓
analysis/<name>/outputs/numbers.json        (fragment — mechanical fields only; field name "key")
        ↓  [report-generator Phase 1 merges + enriches; renames "key" → "id"]
        ↓
analysis/<name>/reports/.manifest.json      (canonical source of truth; field name "id")
        ↓  [render_report_values_tex emits LaTeX macros]
        ↓
analysis/<name>/reports/build/report_values.tex
        ↓
report.tex uses \SciVal{\NSamples}{48}      (checked inline snapshot)
        ↓  [scitexlintr verifies snapshot == manifest value]
        ↓
report.pdf
```

The analysis only ever produces the *mechanical* half — `value`, `provenance`, `computed_at`. The report-writing agent fills in the framing fields (`label_canonical`, `label_aliases_forbidden`, `appears_in_sections`, `overloaded_warning`) at Phase 1.

## When to call `register_value`

Call it for **every value that will appear in the report's prose** — and only those. The rough rule is "would I have to update the report if this changed?".

- **Yes**, call `register_value`:
  - Sample counts, group counts, filtered counts (`n_samples`, `n_de_genes_fdr_0_05`)
  - Thresholds the analysis applied (`fdr_threshold`, `lfc_cutoff`)
  - Contrast and baseline phrases (`contrast_phrase`, `positive_logfc_means`)
  - Headline metrics (`exact_accuracy_test`, `auc_roc`)
  - Confidence intervals you'll quote (`acc_ci_low`, `acc_ci_high`)

- **No**, don't bother:
  - Intermediate scratch values you'll never quote
  - Debug counters, log messages
  - Values you compute but never put in the report

If you're uncertain, register it — the cost of an unused manifest entry is ~zero (the renderer just emits an unused `\newcommand`).

## Using the helper

The helper lives in mycelium at `skills/core/scripts/register_value.py`. To use it from a project's analysis script, the simplest pattern is to copy or symlink it into the project at a known location and import:

```python
# analysis/diff-expr/scripts/01_preprocess.py

import sys
from pathlib import Path

# Make the helper importable. The exact location depends on how the
# project chooses to vendor it; common patterns are:
#   - copy into analysis/_lib/register_value.py
#   - symlink to the mycelium repo's copy
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from register_value import register_value

# Now register reportable values as you compute them.
n_samples = sample_table.shape[0]
register_value("n_samples", n_samples)
register_value("n_control", (sample_table["condition"] == "control").sum())
register_value("n_treated", (sample_table["condition"] == "treated").sum())
register_value(
    "fdr_threshold",
    0.05,
    provenance="config/contrast.yaml:fdr",
)
register_value("contrast_phrase", "treated versus control")
```

### Auto-inferred namespace

The helper looks at the call site's file path and uses the directory under `analysis/` as the namespace. Scripts under `analysis/diff-expr/...` write to namespace `diff-expr`.

Override when needed:

```python
register_value("n_samples", 48, namespace="qc")
```

### Explicit provenance

If the value comes from a specific file or column, name it:

```python
register_value(
    "n_samples",
    48,
    provenance="outputs/tables/cell_qc.csv:row=passing,col=count",
)
```

The provenance string lives in the manifest entry and is what Phase 6 of report-generator uses to re-verify the value against the on-disk artifact.

### Hash-linked source (preferred for numbers read from a file)

When the number is read from a file the analysis wrote, link it to that file's hash with `source=`. Write and close the file **first**: the source is hashed at registration.

```python
table.to_csv("outputs/tables/cell_qc.csv")        # written and closed
register_value(
    "n_passing",
    n_passing,
    source={"path": "outputs/tables/cell_qc.csv",
            "locator": {"row": "passing", "column": "count"}},
)
```

- `path`: relative paths resolve against the current working directory, as `open()` does. The path is stored relative to the project root (the nearest ancestor with `.living/`).
- `locator`: required. It says where in the file the number comes from, either a non-empty string or a dict with any of `row`, `column`, `key`, `sheet`, `cell`, `query`. It is recorded as declared and is not evaluated.
- `provenance`: when you don't give one, it becomes `"<path>:<locator>"`.

The entry gains a `source` object:

```json
"source": {"path": "analysis/qc/outputs/tables/cell_qc.csv", "root": "repo",
           "sha256": "…", "size_bytes": 1234,
           "locator": {"row": "passing", "column": "count"}, "hashed_at": "…Z"}
```

`check_report_values.py` (`skills/core/scripts/`) recomputes each source's hash. It flags these as errors:

- `stale-value`: the file changed;
- `source-missing`: the file is gone;
- `source-invalid`: a bad path, hash or locator;
- `source-unreadable`: the file cannot be hashed.

A number with neither a `source` nor a `sources` field is an `unchecked-value` warning. A field that is present but `null`, a `null` member of `sources`, or a `numbers` entry that is not an object is a `source-invalid` error: a broken link is never read as an unlinked number. The HTML report gate and `sync_html_report.py` run the same check (`sync_html_report.py` runs it once, for both its warnings and its errors), and the TeX gate runs it as an explicit step. Each source file is hashed once per check, however many numbers name it.

`--explain` adds, for a stale or missing source, the recorded call that left the file as it is now. It names a call only when the lineage index says that call's write (or deletion) is still current. When the bytes on disk match a write that a later recorded write superseded (restored outside the record), it reports `current-bytes-match-superseded-write` and credits no one; a deletion followed by a recorded recreation reports `missing-after-superseded-deletion`. A write whose hash was still pending when it was observed is first resolved by file identity: if the file still has exactly the identity the observer recorded and hashes to the current bytes, that write is credited (`hash_origin: query-identity-match`). The checker says a file changed *outside the record* only when the record is complete for that path. If a write's resulting bytes are still unknown, a gap applies to the path, the path is one Bash observation never watches, or any write on the path has an index verdict of `unknown` (for example `coverage-unproven`, when the index cannot positively prove that every later call was observed at the path because the journal was compacted or a later call has no complete observation), it reports `producer-unknown` with those `reasons` instead.

**A number that depends on more than one file** lists them all. The usual case is a table plus the config that set its threshold. A threshold change can leave the table byte-identical while changing the number, so linking only the table would never flag it.

```python
register_value(
    "n_genes_up",
    n_up,
    source=[
        {"path": "outputs/gene_means.csv", "locator": {"column": "ratio"}},
        {"path": "params.yaml", "locator": {"key": "ratio_threshold"}},
    ],
)
```

The entry then carries `sources` (a list) instead of `source`, and the number is `stale-value` when any of the files changes.

A config is usually read at the start of a run and the number registered at the end. Read it with `read_source`, which returns the content together with a source entry for exactly those bytes. Registration then refuses a file that no longer has them:

```python
from register_value import register_value, read_source

text, cfg_source = read_source("params.yaml", {"key": "ratio_threshold"}, mode="r")
threshold = yaml.safe_load(text)["ratio_threshold"]
...
register_value("n_genes_up", n_up, source=[
    {"path": "outputs/gene_means.csv", "locator": {"column": "ratio"}}, cfg_source])
```

(`snapshot_source(path)` hashes without reading. Prefer `read_source`: it leaves no window between hashing a file and parsing it.) Both record the file's absolute path when they read it, so a later change of working directory cannot link a different file with the same bytes.

The fragment is written only to `analysis/<name>/outputs/` inside the project: if `outputs`, `numbers.json` or its lock is a symlink, registration refuses rather than writing through it. `access.json` follows the same rule. Two processes may create `outputs` at once on a fresh analysis: the loser accepts the directory the winner made, after rechecking that it is a real directory (not a symlink) that resolves inside the analysis.

**A stale number is fixed by rerunning the analysis that registers it, then redoing the Phase 1 merge.** Never edit or refresh `source.sha256` by hand. That would certify a number computed from bytes that no longer exist.

If you vendor `register_value.py` into a project (`analysis/_lib/`), copy the new version to get `source=`. Older vendored copies keep working, without hash links.

### What gets written

After your analysis runs, `analysis/<name>/outputs/numbers.json` looks like:

```json
{
  "namespace": "diff-expr",
  "values": [
    {
      "key": "contrast_phrase",
      "value": "treated versus control",
      "provenance": "scripts/02_de.py:L9",
      "computed_at": "scripts/02_de.py:L9"
    },
    {
      "key": "fdr_threshold",
      "value": 0.05,
      "provenance": "config/contrast.yaml:fdr",
      "computed_at": "scripts/02_de.py:L17"
    },
    {
      "key": "n_samples",
      "value": 48,
      "provenance": "outputs/tables/cell_qc.csv:row=passing,col=count",
      "computed_at": "scripts/01_preprocess.py:L42"
    }
  ]
}
```

Entries are sorted by `key` for deterministic diffs.

## Recording which rows and features a step used (`record_ids`)

Hooks see which files a step read and wrote, not which cells, samples or genes inside them it used. The same vendored file provides `record_ids`, so an analysis can record that at the points that matter: after QC, after a filter, for a train/test split, and for the genes a test ran on.

```python
from register_value import record_ids

record_ids("cells_after_qc", adata.obs_names, axis="row", role="used")
record_ids("cells_dropped_qc", dropped.obs_names, axis="row", role="excluded")
record_ids("genes_tested", res.index, axis="feature", role="used")
record_ids("test_cells", test.obs_names, axis="row", role="used", ordered=False)
```

- `axis` is `row` (cells, samples, observations) or `feature` (genes, variables).
- `role` is `read`, `used`, `written` or `excluded`.
- `source=` (optional) hashes the file the ids came from. Only the path and digest are kept, never a locator, since free text could carry an id. An entry from `read_source` or `snapshot_source` keeps the digest taken when the file was read: if the file at that absolute path has changed since, `record_ids` fails with "changed after it was read", as `register_value` does.
- `ids` is a collection. A bare string (or bytes) is refused, since it would be read one character at a time; to record one id, pass `["cell_1"]`.
- Ids must be non-empty strings. Convert numbers deliberately (`str(x)`, or in R `format(x, scientific = FALSE)`), because R and Python print numbers differently and the fingerprints would not match.

Only a count, a distinct count, and a fingerprint of the id set are stored, never the ids. The fingerprint is the sha256 of the sorted distinct ids, so order does not matter; with `ordered=True`, an order fingerprint is kept too. Records go to `analysis/<name>/outputs/access.json`, one per name, and re-recording a name replaces it. The file carries the format marker `"schema": "mycelium.access.v1"`. If an `access.json` already exists without that marker, or with top-level keys other than `schema`, `namespace` and `records` (a QC audit file of your own, for example), `record_ids` refuses to touch it. It writes nothing, and the command line and the R helper fail too. Move the file aside or rename it. (`numbers.json` has no marker and is updated as before, so fragments from 0.9.7 keep working.)

With these records, a reader such as Warrant can catch:

- two numbers that claim the same population but used different cells (their fingerprints differ);
- a QC drop that differs from what the report says (the counts disagree).

### The Stop-time access note

When a script this session wrote a table (.csv, .tsv, .parquet or .h5ad, seen created or modified by the observer) and no `access.json` record covers it, the Stop that accepts the session shows an advisory line to the user (`systemMessage`; on Claude it is not added to the model's context, so it never makes Claude continue):

```text
ACCESS NOTE: 1 script(s) this session wrote a table and recorded no row or feature access: analysis/qc/scripts/filter.py -> analysis/qc/outputs/kept.csv. Where a script filters, subsets or splits rows or features, consider record_ids there ...
```

A record covers the script's last run when its process started inside that run's window: after the previous call ended (or the session started) and before the run ended. A run whose call overlapped another is never named. The note is advice: it never appears in a block, never blocks Stop, and names a script at a given content only once. A script that only summarizes, plots or exports needs no record.

Fingerprints show only whether two sets are *equal*. A train set `{a, b}` and test set `{b, c}` give different fingerprints, exactly as two disjoint sets do. Overlap detection needs a separate check that sees the ids, inside the analysis.

Record at filter, subset and split steps. Mycelium recommends it but does not require it.

**R:** vendor `skills/core/scripts/record_access.R` next to `register_value.py`, then:

```r
source("analysis/_lib/record_access.R")
record_ids("cells_after_qc", colnames(sce), axis = "row", role = "used")
```

It calls the Python helper's command line, so R and Python write identical records. The command line takes `--run-pid` and `--run-started-at` together, or neither; one without the other exits 2. It looks for the helper through `MYCELIUM_REGISTER_VALUE`, then `analysis/_lib/register_value.py`, then the plugin root. It uses Python from `MYCELIUM_PYTHON`, default `python3`.

## Collisions

Two `register_value` calls in the **same process** with the same `(namespace, key)` but different values raise `ValueRegistrationError`. That's a logic bug in the analysis — two parts of the same run disagree about a value that's supposed to be canonical.

Two calls **across processes / re-runs** with different values silently upsert — the on-disk fragment always reflects the most recent registration. That's the normal case when an analysis re-runs with updated data.

## What happens next (report side)

This is implemented by the `report-generator` convention, but quickly:

1. **Phase 1** of `/mycelium:report` reads every `analysis/<name>/outputs/numbers.json` fragment the report sources from. It merges them into a single `numbers[*]` list in `.manifest.json`, then enriches each entry with framing fields the report-writing agent decides on (canonical label, forbidden aliases, which sections each value appears in). During the merge it **renames the fragment's `key` field to `id`** — the manifest entry is `{"id": "n_samples", ...}`, not `{"key": ...}` — because `render_report_values_tex` keys its macros off `id`. (A fragment entry merged verbatim with its `key` intact emits no macro and silently breaks drift detection.)
2. **`render_report_values_tex`** (`skills/core/scripts/render_report_values_tex.py`) reads `.manifest.json` and writes `build/report_values.tex` — a `\newcommand` per id plus the `\SciVal` / `\SciText` wrappers.
3. **The draft** sources every quoted number via `\SciVal{\Macro}{snapshot}` (numbers) or `\SciText{\Macro}{snapshot}` (text values). The snapshot is what the source file shows for review; LaTeX prints only the macro.
4. **scitexlintr** runs as part of Phase 7's recompile gate. It re-resolves every macro against `.manifest.json` and fails the build if any snapshot disagrees with the manifest value.

For `unit:"percent"` entries, the snapshot is still the stored value. If the manifest stores `0.9653` and renders it as `96.5\%`, the source should read `\SciVal{\FracDated}{0.9653}`, not `\SciVal{\FracDated}{96.5\%}`.

If a value changes between runs, the linter detects drift and `scitexlintr --write` rewrites the snapshots in place. Diff:

```diff
-We analyzed \SciVal{\NSamples}{48} samples.
+We analyzed \SciVal{\NSamples}{47} samples.
```

The PDF was never wrong (LaTeX always printed the macro's current expansion); the source is now also right.

## Naming conventions

- `key` is snake_case: `n_samples`, `fdr_threshold`, `contrast_phrase`. The id→macro transform (documented in scitexlintr's `_manifest.py`) is deterministic:
  - all-letter segments of ≤3 characters uppercase (`fdr` → `FDR`)
  - all-digit segments map each digit to its English word (`05` → `ZeroFive`)
  - everything else (mixed letters+digits, or a longer letter run) title-cases the letters **and spells out any embedded digit** (`c1` → `COne`, `x17` → `XOneSeven`). Digits are always spelled out, never left as digits, because a LaTeX control word is letters-only — a bare digit would terminate the macro name (`\C` followed by a literal `1` rather than `\COne`).
- `n_samples` → `\NSamples`, `fdr_threshold` → `\FDRThreshold`, `n_de_genes_fdr_0_05` → `\NDEGenesFDRZeroZeroFive`, `x17_module_c1_precision` → `\XOneSevenModuleCOnePrecision`.
- Stay snake_case in the key; the macro name is generated. Keys with digit-bearing segments are fine — they produce longer but valid macro names.

## Migrating an existing analysis

1. Open the analysis's scripts.
2. Find every `print(f"n_samples = {n}")`, every hand-typed number in a downstream README or report draft, every threshold value that lives in code.
3. Replace each with a `register_value` call.
4. Re-run the analysis once. Verify `outputs/numbers.json` contains what you expect.
5. The next report draft will see the fragment and use it.

## Pitfalls

- **Don't register transformed-for-display values.** Register the raw number (`15122`); let the report decide whether to show `15,122` or `15122`. scitexlintr's snapshot comparison handles comma grouping at the linter side.
- **Don't register collision-prone narrative constants.** Bare round values (`0`, `1`, `1.0`) and common years (`2020`, `2021`) will match every incidental occurrence in prose and trigger `raw-generated-value`. Leave non-computed labels and narrative years unregistered unless they are load-bearing results; phrase them in words where practical.
- **Don't register lists or dicts.** v1 supports `int` / `float` / `bool` / `str` only. For richer structures (tables, worked examples) use the existing `worked_examples[*]` section of `.manifest.json` — that's the report-writing agent's job, not `register_value`'s.
- **Register confidence intervals as separate scalars, never as a nested `uncertainty` object.** A bound you intend to quote in prose needs its own `register_value` call — `register_value("acc_ci_low", 0.241)` and `register_value("acc_ci_high", 0.322)` — so each gets a `\SciVal` macro and scitexlintr can catch drift. A bound bundled into a `{"ci_low": ..., "ci_high": ...}` dict gets no macro and would be flagged `unsourced-numeric-token` if it appeared in the text. (Older `.manifest.json` examples carried an `uncertainty` object; that shape is retired — `numbers[*]` entries are flat scalars on both the fragment and manifest sides.)
- **Don't quote derived numbers without a register.** If the analysis prints "There were 317 DE genes" but no `register_value("n_de_genes_fdr_0_05", 317)` happened, that number will be flagged as `unsourced-numeric-token` once the linter sees it. Register it at the site that computed it.

## Cross-references

- The end-to-end design rationale lives in mycelium's `latex_report_values_strategy.md` (work-tree only, not yet committed).
- scitexlintr's rule catalog (snapshot-mismatch, raw-generated-value, forbidden-alias, ...) is documented at https://github.com/arjunrajlaboratory/scilintr/tree/main/tex/scitexlintr.
- The report side of this story is described in `network/conventions/report-generator/analysis-conventions.md` Phase 1.
