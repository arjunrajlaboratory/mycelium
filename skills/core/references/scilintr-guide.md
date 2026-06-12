# scilintr — Scientific Linting for Analysis Code

`scilintr` is the static analysis linter you run after writing or modifying any analysis code. It is fast, cheap, and complementary to (not a replacement for) `/mycelium:review`: review catches things linting cannot, and linting catches things review tends to miss.

**Repo:** https://github.com/arjunrajlaboratory/scilintr

## Why this exists

Coding agents (and humans) writing analysis code tend to make decisions that look like reasonable software engineering but are actually **silent scientific commitments**. Examples that recur across projects:

- `except Exception: pass` swallows a failure that should have aborted the run.
- `df.iloc[:, 3]` quietly assumes a column order that may change.
- `pd.merge(a, b, on="id")` joins many-to-many without anyone noticing the row explosion.
- `df.dropna()` removes rows with no count of what was dropped or why.
- `glob.glob("*.csv")[0]` picks "a file" whose identity depends on filesystem order.
- A hardcoded `padj < 0.05` decides what counts as a hit, with the threshold buried in the middle of a script.
- A literal design formula `"~ condition"` codes a contrast choice with no rationale.

None of these are bugs in a software sense. They are **anonymous scientific choices**. The core principle of scilintr is:

> Scientifically meaningful choices must not be anonymous.
> They must be named, declared, checkpointed, or explicitly justified.

The linter is **agent-first**: it prefers high recall over high precision because the cost of an agent reading a finding and deciding "fix or waive" is near zero. Many findings will be legitimate — that is expected. The goal is not to prove the analysis correct; it is to force every consequential choice into a reviewable form.

## When to run it

Run it whenever analysis code is written or modified — this is non-negotiable:

1. **After writing a new script or notebook.** Lint the file before declaring the work done.
2. **After editing existing analysis code.** Lint the file again.
3. **Before declaring an analysis complete.** Lint the whole `analysis/<name>/` directory to catch cross-file rules (duplicate parameters across files, shadow overwrites, definition drift).
4. **Before opening a PR or producing final outputs.** Lint the entire repository.

The linter is fast (sub-second on most files), so there is no reason to defer it.

## Install

If `scilintr` is not already importable on the PATH, install it.

**Python (Python analysis code)** — published on PyPI (no runtime dependencies):

```bash
pip install scilintr
```

After install, the `scilintr` CLI is on `$PATH`. Verify with `scilintr --help`.

**R (R analysis code)** — published on CRAN:

```r
install.packages("scilintr")
```

CLI:

```bash
Rscript -e 'scilintr::main()' path/to/project
```

Pick the language that matches the code you wrote. A project with both R and Python should install both.

## Usage

```bash
# Lint a single file
scilintr path/to/script.py

# Lint a directory (catches cross-file rules)
scilintr analysis/my-analysis/scripts/

# Per-rule summary, not individual findings
scilintr --summary analysis/my-analysis/

# Restrict to specific rules
scilintr --rules broad-exception,unchecked-merge path/to/file.py

# Audit mode: show findings even where waivers exist
scilintr --no-waivers analysis/
```

Exit code is `1` if any findings remain after waivers, `0` otherwise. The CLI lists each finding as `path:line:col: [rule-code] message`.

## The waiver mechanism

Every finding has two valid resolutions: **fix the code**, or **add a structured waiver**. Both are first-class outcomes. A waiver is a one-line declaration of intent that future readers can grep for.

Waiver syntax (placed on the offending line or up to four lines above):

```python
# ANALYSIS_OK[category]: explanation; check/ledger/assertion location
df.loc[bad_rows, "value"] = -999
```

A useful waiver answers three questions:

1. **What is being done?**
2. **Why is it scientifically valid?**
3. **Where is it recorded, asserted, or checked?**

Examples (from the canonical spec):

```python
# ANALYSIS_OK[filtering]: drop genes below MIN_TOTAL_COUNTS_PER_GENE;
# summary written to build/gene_filter_summary.tsv
expr = expr.loc[gene_filter, :]
```

```python
# ANALYSIS_OK[join]: one-to-one sample_id join; validate='one_to_one';
# sample set asserted immediately below
metadata = metadata.merge(batch_info, on="sample_id", how="left", validate="one_to_one")
```

```python
# ANALYSIS_OK[label-annotation-only]: treatment joined only after PCA coords are fixed;
# used for plot color only, not for computing PCA
pca_plot = pca_coords.merge(labels[["sample_id", "treatment"]], on="sample_id", validate="one_to_one")
```

```python
# ANALYSIS_OK[random-seed-only]: seed for stochastic UMAP; no synthetic data generated
RANDOM_SEED = 20260523
```

**What does not count as a waiver:**

- `# ANALYSIS_OK` (no category, no explanation)
- `# ANALYSIS_OK: fine` (no category, vacuous explanation)
- `# ANALYSIS_OK[junk]: shut up linter` (the structure exists to force thought; bypassing it is failure)

If a waiver is hard to write honestly, that is the linter doing its job — the choice probably needs to be reconsidered, not justified.

## The required workflow

After writing or editing analysis code, you **must** drive findings to zero by either fixing the pattern or adding a structured waiver. Concretely:

1. Run `scilintr <file_or_dir>`.
2. For each finding:
   - **Prefer fixing.** Most findings have a cleaner code path (named constants, `validate=` on merges, explicit `layer=` on `adata.X`, named column access instead of positional, audited filters, seeded RNGs, schema-checked configs).
   - **Add a waiver only when the pattern is genuinely intentional** and you can write the three-part justification honestly. Cite where the check/assertion/ledger lives.
3. Re-run `scilintr`. Iterate until findings are zero **or** every remaining finding has a structured waiver.
4. Before declaring the analysis done, run `scilintr` on the whole analysis directory to catch cross-file findings.

Findings are not advisory. They are part of the deliverable.

## What the rules catch (and why each matters)

The R package has 40+ rules (R001–R044); the Python package has ~27 rules with kebab-case codes. The two share a unified rule spec. The categories below summarize the failure modes — see [`analysis_lint_strategy.md`](https://github.com/arjunrajlaboratory/scilintr/blob/main/analysis_lint_strategy.md) and [`docs/failure-modes.md`](https://github.com/arjunrajlaboratory/scilintr/blob/main/docs/failure-modes.md) for the canonical list.

**Silent error swallowing** (`broad-exception`, `silent-pass`, R007, R030, `return-none-on-missing-input` / R041)
A bare `except Exception:` or `tryCatch(..., error = function(e) NA)` lets a real failure look like a normal result. `if not path.exists(): return None` silently propagates "no data" downstream and contaminates whatever it touches. Fix: catch the specific exception you expect, or fail loudly. Waive only for known transient errors with a documented retry strategy.

**Anonymous positional access** (`positional-metadata-access`, R001)
`df.iloc[:, 3]` or `df[, 4]` works until someone reorders columns. Fix: use the column name. If you must use position, name a constant and assert the column identity.

**Unchecked joins** (`unchecked-merge`, R003)
`pd.merge(a, b, on="id")` without `validate=` will silently produce a many-to-many cartesian product if either side has duplicate keys. Fix: pass `validate="one_to_one"` (or `"one_to_many"`, etc.) and assert expected row counts.

**Sample alignment by row order** (`positional-sample-alignment`, R004)
Operating on two DataFrames as if their rows correspond when they were never aligned by key. Fix: align by an explicit ID column, never by row order.

**Unannotated filtering and missingness** (`unannotated-filter`, `unannotated-missingness`, R005, R006)
`df = df[df.padj < 0.05]` or `df.dropna()` without logging how many rows were dropped. Fix: log counts before/after, write a small filter-ledger TSV, or assert expected counts.

**Magic thresholds and constants** (`magic-threshold`, R002, R031, R037)
Bare numeric literals in scientific comparisons (`padj < 0.05`, `lfc > 1`, `eps = 1e-6` in BIC formulas). Fix: lift to named constants at the top of the module with a comment on provenance.

**Implicit file selection** (`implicit-file-selection`, R008)
`glob.glob("*.csv")[0]` or "the file with the latest mtime." Fix: name the file. If picking by date is real, log the picked path and assert content.

**Unchecked cache reuse** (`unchecked-cache`, R009, R028)
Reading a cached intermediate without checking its fingerprint against current inputs. Fix: store an input hash next to the cache and refuse stale caches.

**Synthetic data in main analysis** (`synthetic-data-generation`, R010)
`np.random.randn(...)` outside an explicit test/simulation context. This is the canonical "lost track of which data is which" failure. Fix: confine synthetic data to `tests/` or files explicitly named simulation/canary; waive only with `[synthetic-test-fixture]` or `[simulation-only]`.

**Unseeded stochasticity** (`unseeded-stochastic`, R011)
`np.random.*`, `KMeans()`, `UMAP()` without a seed. Fix: pass a seed and record it.

**Label leakage in blind/selection stages** (`label-in-blind-stage`, R012, R033, R034, R035, R036)
Reading or referencing the outcome label in code that is supposed to be blind to it (PCA before annotation, feature selection that "happens to" use the label). The largest class of silent scientific bug. Fix: physically separate label-blind and label-aware code; waive only with `[label-annotation-only]` and an assertion that the label was joined after the blind step.

**Hardcoded design formulas and sample IDs** (`hardcoded-design-formula`, `hardcoded-sample-ids`, R013, R016, R021)
A literal `"~ condition + batch"` or `if sample == "S123"` in the middle of a script. Fix: lift to config; document why this design or these IDs.

**Unannotated transforms** (`unannotated-transform`, R014, R023)
`np.log2(x + 1)`, `np.clip(...)`, `zscore(...)` with no rationale. Fix: add the rationale (why log? why pseudocount = 1?). These choices materially change downstream answers.

**Ambiguous layer access** (`ambiguous-layer-access`, R015)
`adata.X` in scanpy code without an explicit `layer=`. Fix: name the layer; `.X` may be raw counts, log-normalized, or something else depending on pipeline stage.

**Warning suppression** (`warning-suppression`, R017)
`warnings.filterwarnings("ignore")` or `suppressWarnings()` globally. Fix: narrow to a specific category and document.

**Unchecked model fit** (`unchecked-model-fit`, R018)
`.fit()` whose return value is ignored — no convergence check, no fit-quality reporting. Fix: capture and inspect the fit object; assert convergence.

**Plot-side-effect filters** (`plot-side-effect-filter`, R019)
Plotting code that mutates the DataFrame in place. Fix: never mutate state from a plotting function.

**Shadow overwrites and definition drift** (R020, R025, R026, `duplicate-parameter-source`)
The same name defined in two places with disagreeing values, or a sourced helper overwritten by a local re-declaration. Fix: name parameters in one place only; assert across files.

**Unconsumed CLI flags** (`unconsumed-cli-flag`, R042)
A `--top-k` flag declared but never read — the value being used is a hardcoded default elsewhere, so the flag is a lie. Fix: wire it through, or remove it.

**Unvalidated config** (`unvalidated-config`, R043)
A YAML or JSON config loaded and used without a schema check. Fix: validate (pydantic, jsonschema, or hand-rolled assertions on required keys and types).

**Sentinel mask assignments** (`sentinel-mask-assignment`, R044)
`df.loc[mask, col] = -999` or `""` as a "missing" sentinel. These leak into downstream numeric code. Fix: use `NaN` and audit missingness; or document the sentinel explicitly.

**Runtime asserts in production** (`runtime-assert`, Python-only)
`assert` is stripped under `python -O`. Fix: use `if ... raise ...` for invariant checks meant to survive optimization.

**Selective reporting** (R032, R038, R039, R040)
"Best of either side" tie-breaking against a label; reporting whichever direction is significant; gates held constant across recursion that should adapt. Fix: pre-register the direction or the gate; if you must choose, multiple-test correct.

**Magic-eps floors** (R031)
`log(x + 1e-6)` or BIC + `eps` with an undocumented epsilon. Fix: name the epsilon and document the regime where it matters.

## Stage awareness

Some rules behave differently depending on the analysis stage of a file (blind QC vs. selection vs. plotting). The linter supports a YAML config that declares which files belong to which stage. For most analyses, the default is fine; consult the canonical spec when you need stage-aware tuning.

## Relationship to `/mycelium:review`

| | scilintr | /mycelium:review |
|---|---|---|
| Speed | sub-second | minutes |
| Cost | near zero | non-trivial (multi-agent) |
| Coverage | structural patterns, high recall | semantic + statistical + domain reasoning |
| When | every code change | major milestones, before merge |
| Audit trail | structured waivers in code | review report |

They are complementary. Lint catches the silent-commitment patterns the review may not pattern-match on; review catches the semantic issues the lint cannot see. **Both should run before declaring an analysis complete.**

## Further reading

- Rule spec and rationale: [`analysis_lint_strategy.md`](https://github.com/arjunrajlaboratory/scilintr/blob/main/analysis_lint_strategy.md)
- Catalogued failure modes: [`docs/failure-modes.md`](https://github.com/arjunrajlaboratory/scilintr/blob/main/docs/failure-modes.md)
- Python rule list: [`py/scilintr/README.md`](https://github.com/arjunrajlaboratory/scilintr/blob/main/py/scilintr/README.md)
- R rule list (R001–R044): [`r/scilintr/README.md`](https://github.com/arjunrajlaboratory/scilintr/blob/main/r/scilintr/README.md)
