# Data contract check — test a plan's assumptions about its sample table

<!--
Adapted from the data-contract-check skill in mycelium-extra
(https://github.com/mdmanurung/mycelium-extra), Copyright (c) 2026 Mikhael
Manurung, MIT License. See issue #91.
-->

Before an analysis runs, write down what it assumes about its sample table,
check those assumptions against the file, and bring every failure to the user.
The checker decides what blocks; the contract only states the assumptions.
It reads the table and never writes.

This catches before a run what the review checklists catch after one: two
acquisitions of one participant counted as two units, a pairing that is missing
a timepoint, a contrast that is nested in batch.

## When to use it

Use it before any statistical test or model whose validity depends on the
sample table's structure: cohort sizes, unit of replication, pairing across
timepoints, or batch versus contrast. Skip it for exploratory plots and for
tables that hold no samples (expression matrices, layers, Seurat objects).

## 1. Write the contract

- Find the sample-level table the analysis will use (one row per sample,
  acquisition, or pseudobulk unit) from `data/DATA_MANIFEST.md`, the analysis
  documentation, or the loading code. Name columns by header, spelled as the
  table spells them.
- **Take each value from the plan or the repository, not from a first look at
  the data.** A contract fitted to the data checks nothing. State only the
  assumptions the analysis relies on.
- The unit column must hold resolved identity. The checker cannot follow a
  crosswalk: if one person carries two IDs (a repeat-donor map, for example),
  a unit check on the raw ID column undercounts repeats. Say so in the report,
  or use a column that already resolves identity.
- Save the contract as `analysis/<name>/data_contract.json` so it is recorded
  with the analysis and can be re-run when the data changes.

```json
{
  "schema": "mycelium.data_contract.v1",
  "table": "data/processed/<dataset>/sample_metadata.tsv",
  "filters": [{"column": "preferred_acquisition", "equals": "TRUE"}],
  "checks": [
    {"kind": "schema", "columns": ["participant", "cohort", "visit", "batch"]},
    {"kind": "cohort", "column": "cohort", "levels": ["A", "B"], "min_rows": 40, "no_missing": ["batch"]},
    {"kind": "unit_of_replication", "unit": "participant", "group": "cohort", "within": ["visit"], "min_units_per_group": 3},
    {"kind": "pairing", "unit": "participant", "within": "visit", "levels": ["0", "28"]},
    {"kind": "batch_confounding", "batch": "batch", "contrast": "cohort", "max_share": 0.7}
  ]
}
```

### Tables

- `.csv` is read as comma-separated; `.tsv`, `.tab`, and `.txt` as
  tab-separated; `.h5ad` through its `obs` (with h5py, when it is importable;
  `X` and layers are never read). Any other format is a gap: export it to CSV
  or TSV.
- A header that names a column twice, or a row with more fields than the
  header, blocks: no check on that table can be trusted. (An R `write.table`
  export with row names writes one fewer header field than each row; re-export
  it with `row.names = FALSE`.) A short row's missing trailing fields count as
  missing values.
- Values compare as the strings a CSV export would hold. In an `.h5ad`,
  categories are compared as written, integers as `28`, floats as `28.0`, and
  booleans as `True`/`False`. A missing category, masked value, or NaN counts
  as missing. The index is a column named as the file names it (`_index`
  unless the index had a name). An `.h5ad` `obs` is usually one row per cell,
  so a unit check on it counts cells and `max_share` is a share of cells.

### Checks

- `filters` (`equals`, `in`, `not_in`; exactly one per filter) apply to every
  check. A check may add its own `filters`, `table`, and `label`. Filter values
  and levels are strings or numbers, compared as text (`"TRUE"`, not `true`).
- `schema`: every column in `columns` exists.
- `cohort`: the required `levels` of `column` are present; `min_rows` or exact
  `rows` holds; and the `no_missing` columns have no missing values.
- `unit_of_replication`: at most one row per `unit` in each `group` × `within`
  cell, and at least `min_units_per_group` distinct units per group (default
  2). No unit appears in two groups unless `units_may_span_groups` is `true`
  (a crossover or paired contrast).
- `pairing`: every unit is observed at every `within` level (the listed
  `levels`, or every level present).
- `batch_confounding`: blocks when the contrast is fully nested in batch, or
  when a level shares no batch with another level. Warns when some batches hold
  one level, or a level sits in one batch. With `max_share` (above 0, at most
  1, such as `0.7`) it also warns when a batch holds more than that share of
  one contrast level's rows; the plan sets `max_share` with its reason. Every
  run prints the batch-by-contrast table of row counts and shares.
- Missing values (`""`, `NA`, `NaN`, `NULL`, `None`, `<NA>`; any case) in a check's key columns
  are left out of that check and reported as a warning.

## 2. Run it

From the project root:

```bash
python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/data_contract_check.py" \
  --contract analysis/<name>/data_contract.json
```

The script ships with Mycelium as a managed utility, so running it opens no
post-action cycle and leaves no lineage event.

The output has one line per check (`PASS`, `WARN`, `GAP`, or `BLOCK`, with the
rows it ran on). A `batch_confounding` check adds its batch-by-contrast table,
and each check's alerts follow. `--json` gives `{"checks": [...], "alerts":
[...]}`, with the table as `cross_table` on its check. `--root <dir>` resolves
relative table paths from another directory.

| Exit | Meaning |
| --- | --- |
| 0 | Clean, or warnings only |
| 2 | A blocking alert |
| 3 | A gap, with nothing blocking |
| 1 | A malformed contract: bad JSON, unknown kind, missing key, or a wrongly typed value. Fix its syntax, never its assumptions. |

A missing table or column, or filters that leave no rows, is a failed
assumption and blocks. A table or column the checker cannot read (an `.h5ad`
without h5py, a file that is not HDF5, an `obs` encoding it does not recognise,
an unsupported format) is a gap: the check did not run, so it is neither a pass
nor a failure.

## 3. Act on the alerts

- **Blocking alert**: report it to the user with its expected, observed, and
  evidence lines, and what it means for the plan (for example: "20
  participants have two acquisitions at the same visit, so a row-level test
  would count them twice"). Do not start the analysis while a blocking alert
  stands.
- **Never loosen the contract to make it pass** (drop a check, change a
  threshold, add a filter, switch the unit column) without the user agreeing.
  That silent adjustment is the failure this check exists to catch. When the
  user agrees to a change, log it to `.living/decisions.md`, re-run, and cite
  both runs.
- **Warning**: list it at the end with its count; it does not stop the
  analysis. For a `max_share` warning, quote the batch-by-contrast table so the
  user sees how unbalanced the design is.
- **Gap**: say which checks did not run and why, and how to close the gap
  (install h5py where the check runs, or export `obs` to a TSV and name that
  table). A gap never counts as a pass; the assumptions it covers stay
  unverified.
- **Relaxations** (`units_may_span_groups`) are echoed next to their check.
  Use one only when the design calls for it (a crossover or paired contrast),
  and name it in the report.

## 4. Record

Report one line per check: the kind, the assumption, the status, and the
observed counts. Record the run in the analysis documentation file next to the
contract path, so a later re-run on new data can be compared with it.
