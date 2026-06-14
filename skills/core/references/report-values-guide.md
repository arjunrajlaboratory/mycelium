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
