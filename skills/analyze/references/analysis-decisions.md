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
| Unit of replication | The test unit is the unit independently assigned to the condition in an experiment, or independently sampled from the population for an observational contrast such as disease status (organism, participant, donor, well, culture, or sample); note any paired or repeated structure, such as visits within a participant. Measurements below it (cells, technical replicates) share that unit and are not independent: aggregate them to it (pseudobulk), or fit a mixed model with a random effect for the assigned unit they share (for cells from donors, `(1 | donor)`). A cell or replicate effect never stands in place of the assigned-unit effect; a nested one for repeated measures of the same cell can be added to it. Nuisance levels above the assigned unit (batch, plate, a donor that gave several wells) stay shared after aggregating, so keep each the design shares as a blocking term or random effect; never the condition being tested itself (in a pooled screen, a guide is often the condition). Assigning a condition to each cell does not make cells independent: cells that share a donor or well stay correlated | Sample metadata, prior decisions, existing model code |
| Cohort and exclusions | Inclusion rules; which samples are excluded and why; whether the rules were set before or after looking at the data | `data/DATA_MANIFEST.md`, dataset provenance, decisions |
| Input and matrix state | Which file or object; `X` versus `layers`, `raw`, or assay; counts, normalized, log, or scaled at each downstream call | Data manifest, loading code, one inline probe |
| Reference and identifiers | Genome and annotation build; gene ID type (Ensembl or symbol); species mapping; marker or gene-set source and version | Ingest provenance, config, learnings |
| QC thresholds | Genes, counts, mitochondrial fraction, doublets, ambient RNA; for cytometry, gating and debris or dead-cell rules; where each threshold came from | Analysis documentation, config, learnings, active conventions |
| Normalization and transformation | The method, and whether every arm or batch gets the same treatment | Pipeline code, conventions |
| Batch versus biology | Whether batch or site is confounded with the contrast; whether integration or correction could remove the signal; batch-aware alternatives | Metadata cross-tabulations, decisions, prior reviews |
| Annotation | Label source, resolution, and reference; whether labels came from the same data that is then tested | Annotation code, findings, decisions |
| Model or test | Why this family; its assumptions and how they are checked; covariates and why, chosen for the estimand. For a causal estimand: never a collider, and a mediator only when the estimand is a direct effect, not the total effect. For a predictive model: a feature only if it is available at prediction time and does not leak the outcome | Model code, `skills/core/references/statistical-conventions.md` |
| Multiplicity | Number of tests; correction method and scope (per cell type or global); pre-specified versus post hoc subgroups | Decisions, conventions |
| Leakage and circularity | Selection or feature choice on the evaluation data; clustering then testing on the same cells; train/test split by subject | Code, prior reviews |
| Validation and sensitivity | Which choices get a sensitivity run; null or permutation checks; the result that would change the conclusion | Active `robust-analysis` conventions, analysis documentation |
| Outputs and reporting | The deliverable (figure, table, report); values a report will cite; n, uncertainty, and units in figures | Analysis documentation, `outputs/numbers.json`, report conventions |

## Declaring a data check in the plan

When a statistical test or model's validity rests on a property of the
sample table (the cases analyze step 6 covers), make that row's Check column
name a data contract check, with the value taken from the plan's source.
Exploratory plots and tables that hold no samples get no contract; name a
manual check instead. `unit_of_replication` allows at most one row per unit in
each cell of the design, where `within` names the repeated-measure columns. Run
it on the table at the level the model observes: for repeated measures such as
one row per participant per visit, keep that table and set `within` to the visit
column, so a duplicated visit or an under-replicated group still blocks. Rows
below that level with nothing to tell them apart (many cells per donor) fail it
by design; check those on the per-unit table (one row per donor and condition). The checker supports five kinds:

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
