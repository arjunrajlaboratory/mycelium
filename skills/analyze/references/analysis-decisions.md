# Analysis decision points

<!--
Adapted from skills/grill/references/analysis-decisions.md in mycelium-extra
(https://github.com/mdmanurung/mycelium-extra), Copyright (c) 2026 Mikhael
Manurung, MIT License. See issue #91.
-->

A coverage list, not a question list. For each point that applies, the plan
records the choice and its source: `repo: <path>`, `user`, or
`default: <reason>`. Most points should resolve from the repository or a
stated default; ask the user only under the rules in
`skills/analyze/references/planning.md`. Skip points that do not apply.

| Decision point | Settle | Where evidence usually lives |
| --- | --- | --- |
| Estimand and claim type | Exploratory, predictive, or causal; the exact contrast and its direction; the population the claim covers | Analysis documentation, `.living/decisions.md` |
| Unit of replication | Donor, sample, or cell as the test unit; pseudobulk or mixed model versus cell-level tests; paired or repeated structure. Cells are the unit only when the condition was assigned to each cell (a per-cell perturbation); otherwise the donor is. A donor with several samples needs a donor term in the design | Sample metadata, prior decisions, existing model code |
| Cohort and exclusions | Inclusion rules; which samples are excluded and why; whether the rules were set before or after looking at the data | `data/DATA_MANIFEST.md`, dataset provenance, decisions |
| Input and matrix state | Which file or object; `X` versus `layers`, `raw`, or assay; counts, normalized, log, or scaled at each downstream call | Data manifest, loading code, one inline probe |
| Reference and identifiers | Genome and annotation build; gene ID type (Ensembl or symbol); species mapping; marker or gene-set source and version | Ingest provenance, config, learnings |
| QC thresholds | Genes, counts, mitochondrial fraction, doublets, ambient RNA; for cytometry, gating and debris or dead-cell rules; where each threshold came from | Analysis documentation, config, learnings, active conventions |
| Normalization and transformation | The method, and whether every arm or batch gets the same treatment | Pipeline code, conventions |
| Batch versus biology | Whether batch or site is confounded with the contrast; whether integration or correction could remove the signal; batch-aware alternatives | Metadata cross-tabulations, decisions, prior reviews |
| Annotation | Label source, resolution, and reference; whether labels came from the same data that is then tested | Annotation code, findings, decisions |
| Model or test | Why this family; its assumptions and how they are checked; covariates and why (no colliders or mediators) | Model code, `skills/core/references/statistical-conventions.md` |
| Multiplicity | Number of tests; correction method and scope (per cell type or global); pre-specified versus post hoc subgroups | Decisions, conventions |
| Leakage and circularity | Selection or feature choice on the evaluation data; clustering then testing on the same cells; train/test split by subject | Code, prior reviews |
| Validation and sensitivity | Which choices get a sensitivity run; null or permutation checks; the result that would change the conclusion | Active `robust-analysis` conventions, analysis documentation |
| Outputs and reporting | The deliverable (figure, table, report); values a report will cite; n, uncertainty, and units in figures | Analysis documentation, `outputs/numbers.json`, report conventions |

## Declaring a data check in the plan

When a row rests on a property of the sample table, make the plan's Check
column name a data contract check (analyze step 6), with the value taken from
the plan's source. The checker supports five kinds:

| Row | Check kind | What it confirms |
| --- | --- | --- |
| Input and matrix state (columns) | `schema` | The required columns exist |
| Cohort and exclusions | `cohort` | The required levels are present, the row count holds, and the named columns have no missing values |
| Unit of replication | `unit_of_replication` | At most one row per unit in each cell of the design; a minimum number of units per group; no unit in two groups unless allowed |
| Unit of replication (paired) | `pairing` | Every unit is observed at every level being paired |
| Batch versus biology | `batch_confounding` | The contrast is not nested in batch and its levels do not split into groups sharing no batch; optionally, no batch holds more than a set share of a level |

Contract format and run command: `skills/core/references/data-contract-check.md`.
A passing check covers the table, not the code. It does not show that the code
joins by key, uses the planned normalization, or reads the planned layer. When
the plan relies on those, name the step that checks them by hand.
