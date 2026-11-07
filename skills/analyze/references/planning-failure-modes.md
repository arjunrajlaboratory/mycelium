# Planning failure modes

<!--
Adapted from skills/grill/references/llm-failure-modes.md in mycelium-extra
(https://github.com/mdmanurung/mycelium-extra), Copyright (c) 2026 Mikhael
Manurung, MIT License. See issue #91. The Check column is rewritten for the
checks mycelium ships.
-->

The ways an agent goes wrong while carrying out a settled design, as a
companion to `skills/analyze/references/analysis-decisions.md` (the design
decisions a plan must settle). Walk both while planning. This list is for
before the run: for each mode that applies, the plan names a guard. The
review checklist `skills/core/references/review/llm-failure-modes.md` covers
the same ground after the code is written; it is not repeated here.

For each mode that applies, the plan names a guard (a step, a read-only probe,
or a check) with its source: `repo: <path>`, `user`, or `default: <reason>`.
Put the guard in the plan row it protects or, when it protects the whole plan,
in the Main risk line. Skip modes that do not apply. This is not a question
list: ask the user only under the rules in `skills/analyze/references/planning.md`.

What the Check column means:

- `partial: <check> (<what it covers>)`: a check mycelium ships
  (`data-contract-check` with a kind, `scilintr` with a rule, or
  `register_value` with `scitexlintr`) covers only the part in brackets. No
  shipped check covers a whole mode: these checks read the sample table or the
  code statically, not what the analysis did. The plan's guard covers the rest,
  and the plan must not cite the check for more.
- `review: <checklist>`: a `$mycelium:review` checklist looks for it after the
  code is written. It is not an automatic check, so the plan still names its own
  guard.
- `cross-ref: <decision point>`: the analysis-decisions row of that name covers
  it. Cite that row rather than adding a second one.
- `none`: nothing checks it. The guard is a manual step.

Never write that a check runs when the Check column says `none`, `review`, or
`partial` for the part in question.

## Planning and premise

| Failure mode | How it shows up | Guard the plan must name | Check |
| --- | --- | --- | --- |
| Estimand drift | The plan answers a nearby question: a different contrast, population, or outcome | The plan quotes the user's question; each step's output feeds that estimand | cross-ref: Estimand and claim type |
| Reversed contrast | "Treated vs control" fitted with control as the numerator, or the reference level is not the stated baseline | Name the reference level and the sign of a positive effect; a probe reads the factor levels | partial: data-contract-check (`cohort`: both levels exist, not which one is the reference) |
| Stale evidence as current | A file's existence, or an old finding, is cited as if its outputs were current | Each cited output carries its date or commit; a step re-checks it or marks it unverified | none |
| Silent scope growth | Extra subgroups, cell types, or sensitivity runs appear that nobody asked for | Every step traces to the question; extras go to Parked with a return condition | none |
| Redoing settled work | The repository already holds a finding that answers the task, and the plan re-runs it without saying so | Cite the finding first; make reuse versus re-run an explicit plan row | none |

## Data handling

| Failure mode | How it shows up | Guard the plan must name | Check |
| --- | --- | --- | --- |
| Invented columns or levels | Code uses a column name or factor level taken from the request or from memory, not from the file | A header probe or data contract confirms every column and level the code uses | partial: data-contract-check (`schema`, `cohort`: the columns and levels the contract declares exist in the table, not that the code uses only those) |
| Silent row loss | Missing-value handling, `dropna`, or an inner join removes samples without a count | Log row counts before and after every filter and join; name the expected n | partial: scilintr (`unannotated-filter`, `unannotated-missingness`: a filter or drop with no logged count) |
| Join duplication | A many-to-many join multiplies rows; a cell or sample appears twice | Assert key uniqueness on both sides and the row count after the join | partial: scilintr (`unchecked-merge`: a merge without `validate=`); partial: data-contract-check (`unit_of_replication`: repeated units in the sample table, not joins in the code) |
| Identifier mismatch | Sample IDs differ between tables in case, prefixes, or separators, so a join matches a subset | Report the overlap (matched, left-only, right-only) before joining | none |
| Gene symbol corruption | Spreadsheet-converted symbols (SEPT2 to a date, MARCH1), outdated aliases, or symbols mixed with Ensembl IDs | Map through a versioned annotation; report unmapped and duplicated symbols | cross-ref: Reference and identifiers; review: `bioinformatics` |
| Coordinate base | BED (0-based, half-open) mixed with VCF or GTF (1-based, closed), giving off-by-one intervals | Name each file's coordinate convention and the conversion step | review: `bioinformatics` |
| Chromosome naming and build | `chr1` versus `1`, or a GRCh37 file joined to GRCh38 annotation, so lookups silently miss | A probe of the contig names and the build in each header | cross-ref: Reference and identifiers; review: `bioinformatics` |
| Strand assumptions | Library strandedness assumed rather than read, halving or inverting counts | Read strandedness from the protocol or infer it; record it | review: `bioinformatics` |
| Sample label swap | Metadata row order assumed to match the matrix's column order | Join by explicit ID, never by position; assert the IDs are identical | partial: scilintr (`positional-sample-alignment`: row-order alignment visible in the code) |
| Silent type coercion | IDs read as numbers (lost leading zeros), dates parsed as strings, factors as integers | Name the column types on read; a probe checks that a few known IDs round-trip unchanged | review: `llm-failure-modes` |
| Silent default class | A classifier gives unmatched units a passing label (`np.select(..., default='Pass')`, a final `else`), or a label spelled two ways falls through | Every unit gets exactly one status from a table that lists every input combination; an explicit `unassigned` level is counted and reported | none |
| Labels keyed to cluster numbers | Cell-type names are assigned by cluster number, and a refit or new seed renumbers the clusters | Assign labels from marker evidence on each run, or freeze the clustering the labels were made on and check its hash | none |

## Code and tools

| Failure mode | How it shows up | Guard the plan must name | Check |
| --- | --- | --- | --- |
| Invented API or flags | A function, argument, or CLI option that does not exist in the installed version | Record each tool's version; check unfamiliar calls against its help or docs | review: `llm-failure-modes` |
| Version-specific behaviour | A default or return format that changed between versions | Pin the version, or record it in the plan | cross-ref: Normalization and transformation |
| Unstated defaults | The result depends on a default parameter nobody chose (resolution, number of neighbours, filtering thresholds) | Write consequential parameters out explicitly, each with a source | partial: scilintr (`magic-threshold`: a bare literal in a comparison, not a library default left implicit) |
| Non-determinism | No seed, or parallel code whose result varies run to run; in R, `set.seed` in the parent does not reach `mclapply` or `future` workers | Set and record seeds, inside each worker for parallel code; name the steps that cannot be made deterministic | partial: scilintr (`unseeded-stochastic`: a stochastic call with no seed, not seeds inside parallel workers) |
| Wrong layer or assay | Tests run on scaled or log values when counts were needed, or the reverse | Name the layer at each downstream call | partial: scilintr (`ambiguous-layer-access`: `adata.X` with no named layer); cross-ref: Input and matrix state |
| Formula is not the model | The design formula reads as intended but expands otherwise (`~ age + age^2` in R adds no squared term without `I(age^2)`), or a term is dropped as aliased | Print the design matrix's column names and rank before fitting and compare them with the plan; a rank-deficient design stops the run | partial: scilintr (`hardcoded-design-formula`: a literal formula in code, not how it expands); cross-ref: Model or test |
| Memory blow-up | A sparse matrix densified, or a whole object copied, crashing or truncating a job | Estimate object size; keep sparse operations sparse; name the machine size | none |
| Swallowed errors | `try`/`except: pass`, `tryCatch` returning `NULL`, or a trailing `\|\| true` hides a failing step | No blanket error suppression; a failure stops the run; the runner script fails on any failed step (`set -euo pipefail` in `run.sh`) | partial: scilintr (`broad-exception`, `silent-pass`, `warning-suppression`: Python and R code, not shell runners such as `\|\| true` in `run.sh`) |

## Statistics and inference

| Failure mode | How it shows up | Guard the plan must name | Check |
| --- | --- | --- | --- |
| Retry until significant | Parameters, subsets, or methods changed across runs until a result appears | Pre-specify the primary analysis; count every contrast tried | cross-ref: Multiplicity; review: `llm-failure-modes` |
| Test chosen after results | The test or model changes after the first look at the data | The test is fixed in the confirmed plan; a change needs a revised plan | cross-ref: Model or test |
| p versus padj confusion | Raw p-values reported or thresholded as if adjusted, or the reverse | Name which column each threshold uses | cross-ref: Multiplicity |
| Log base and sign confusion | log2, ln, and log10 mixed; fold-change direction flipped between table and text | Name the log base and the sign convention once; reports use them throughout | partial: scilintr (`unannotated-transform`: a log or other transform with no stated rationale) |
| Absence of evidence as evidence of absence | A non-significant result reported as "no effect" | Report the effect size and interval; call an underpowered null inconclusive | review: `stats-causal` |
| Missing effect sizes | Only p-values reported, with no magnitude or uncertainty | Every reported test carries an effect size and its interval | review: `stats-causal` |
| Causal wording for associations | "Drives", "causes", "protects" for observational contrasts | The claim type matches the estimand; causal wording only with a causal design | cross-ref: Estimand and claim type; review: `stats-causal` |

## Claims and reporting

| Failure mode | How it shows up | Guard the plan must name | Check |
| --- | --- | --- | --- |
| Number transcription | A value in the report text differs from the output it came from | Report values are read from output files, not retyped; register each with `register_value` | partial: `register_value` with `scitexlintr` (values registered in `numbers.json` and quoted in a `.tex` report, not numbers typed elsewhere) |
| Invented citations | A DOI, PMID, or reference that does not exist or does not support the claim | Every citation resolves, and the cited source is read before it is used | none |
| Figure and text disagree | The text describes a trend, n, or direction the figure does not show | Figures and text come from the same output file; n is in the figure | cross-ref: Outputs and reporting |
| Figure from values not on disk | A plot is drawn from numbers typed into the plotting code, or from an object no step wrote | The figure script reads only a table an earlier step wrote under `outputs/`, named in the plan row | none |
| Missing n or units | A figure or table without its sample size, units, or the unit of replication | Each figure and table states n, units, and what one point is | cross-ref: Outputs and reporting |
| Non-detection read as absence | A gene filtered out, below detection, or missing from a resource is written up as unchanged or not expressed | Say "not tested" or "not detected" and give the filter or detection limit; never "no change" | cross-ref: Outputs and reporting |
| Score read as probability | padj, lfsr, an NES, or a set's enrichment is described as the chance a result is true | Name each score's meaning: padj is an FDR-adjusted p-value, lfsr a sign error rate, and a set result says the set is enriched among changed genes | none |
| Missing shown as zero | A heatmap or table fills untestable cells with 0, so "not tested" reads as "no change" | Keep missing as missing and give it its own colour or mark; the legend says what it means | review: `llm-failure-modes` |
