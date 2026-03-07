# Robust Analysis QC Checklist

Run the pre-flight checklist before starting any analysis. Run the post-flight checklist before reporting results.

## Pre-Flight Checklist

Run before any analysis begins. These checks establish that the data is what you think it is.

### Data Integrity

- [ ] **Row count** matches expected sample/observation count
- [ ] **Column count** matches expected variables
- [ ] **Column types** are correct (numeric columns are numeric, categoricals are categorical, dates are dates)
- [ ] **No phantom categories** in categorical columns (check for "nan", "None", "N/A" as string values)
- [ ] **No unexpected NaN** — document the NaN pattern (which columns, how many, what fraction)
- [ ] **No duplicate rows** unless duplicates are expected
- [ ] **Sample IDs** are unique (or if not, understand why)
- [ ] **Metadata matches data** — sample IDs in metadata align with sample IDs in data

### Distribution Sanity

- [ ] **Key variables** have plausible ranges (no negative counts, no expression values of 1e15, etc.)
- [ ] **Raw distributions plotted** for all key variables — saved to `outputs/figures/diagnostic/`
- [ ] **No obvious outliers** that would dominate the analysis (or if present, documented and a decision made)
- [ ] **Expected relationships hold** at the raw data level (e.g., positive control behaves as expected)

### Analysis Setup

- [ ] **Random seed** set and documented
- [ ] **Software versions** recorded in `ENVIRONMENTS_INSTALLATIONS.md`
- [ ] **Parameters** documented, including defaults
- [ ] **Full dataset** is being used (no accidental subsetting from a previous exploration step)

---

## Post-Flight Checklist

Run after the analysis is complete, before reporting results.

### Output Consistency

- [ ] **Figures match tables** — numbers in summary tables are recoverable from the data shown in plots
- [ ] **Reported n's match** at every stage (raw data -> filtered -> analyzed -> reported)
- [ ] **No n drift** — if you started with 1000 samples, account for every sample (800 passed QC, 50 were outliers, etc.)
- [ ] **Percentages sum correctly** and match the underlying counts

### Robustness

- [ ] **Sensitivity analysis** run for every major decision (thresholds, methods, preprocessing)
- [ ] **Supplementary sensitivity figures** generated and saved to `outputs/figures/supplementary/`
- [ ] **Random seed stability** checked (at least 3 seeds)
- [ ] **Subsample stability** checked (at least 3 x 80% subsets)

### Null Hypothesis

- [ ] **Permutation or shuffle test** run for the main finding
- [ ] **Null distribution** plotted with observed statistic marked
- [ ] **Bootstrap CIs** computed for key estimates
- [ ] **The null story** articulated — what would the data look like with no effect?

### Spot Checks

- [ ] **Outlier deep-dives** — top/bottom extreme data points traced back to raw data with diagnostic plots
- [ ] **Baseline comparisons** — 1-2 non-outlier data points investigated with the same detail for contrast
- [ ] **Outlier narratives written** — each outlier has a brief explanation of why it's extreme (informative vs. artifactual)
- [ ] **Spot-check figures saved** to `outputs/figures/diagnostic/spot_checks/`

### Failure Mode Analysis

- [ ] **Failures characterized** — if any algorithm step failed or produced implausible values, the failing subset is identified and compared to successes
- [ ] **Causal chain traced** — root cause of failure identified by step-by-step tracing on representative cases
- [ ] **Impact assessed** — results compared with and without failing cases; non-random failure enrichment flagged
- [ ] **Fix-or-flag decision documented** — failures either fixed at root cause, adapted for, or excluded with full documentation

### Adversarial Checks

- [ ] **Directionality verified** — signs of effects are correct
- [ ] **Controls verified** — positive and negative controls behave as expected
- [ ] **Breaking subset search** — checked whether the conclusion holds across subgroups
- [ ] **Strongest counterargument** articulated and addressed

### Documentation

- [ ] **All decisions logged** in `.living/decisions.md`
- [ ] **All filtering/subsetting operations** justified with row counts
- [ ] **Analysis documentation file** (UPPER_SNAKE_CASE.md) updated with methods, results, and robustness checks
- [ ] **ANALYSIS_MANIFEST.md** updated with current analysis status
