# Robust Analysis Conventions

Defensive execution practices for data analysis. These conventions govern how analysis code is written and run — not what statistical methods to use (see `statistical-conventions.md` in core references), but how to avoid the silent errors that make results wrong without anyone noticing.

The core philosophy: **in data analysis, a crash is informative and a silent recovery is dangerous.** A crash tells you exactly where your assumptions broke. A silent recovery hides the breakage and lets it propagate into your results.

Treat analysis code like a scientific instrument — if the instrument malfunctions, you want it to stop and flash a warning, not quietly interpolate over the bad readings.

---

## 1. Strict Execution Mode

Write analysis code that fails loudly on unexpected data and never silently "fixes" problems. Agents are trained to be helpful, which means their default is to handle unexpected data and keep going. In a web app, that's good engineering. In a data analysis pipeline, it's how you get a beautiful figure that's completely wrong.

**Key rules**: Assert shapes/types/ranges after every operation. Log row counts at every step. No silent coercion, no silent dropping, no bare exception handling.

> See [strict-execution-rules.md](strict-execution-rules.md) for the complete rule set with code examples.

---

## 2. Data Validation Checks

Verify data integrity at every transformation step. The most common agentic coding errors are silent row duplication from bad joins, unnoticed type coercion, and train/test leakage during feature engineering.

**Key checks**: Row count preservation, column type assertions, distribution plausibility, cross-referencing figures with tables, checking for Simpson's paradox in pooled data.

> See [validation-checks.md](validation-checks.md) for the full checklist with detection patterns.

---

## 3. Sensitivity Analysis

Every analytical decision is a parameter. If the conclusion depends on a specific cutoff, a specific method, or a specific preprocessing choice, the conclusion is fragile. Sweep every decision point and generate supplementary figures documenting the sensitivity.

**Key practices**: Threshold sweeps with result-vs-threshold plots, method comparisons (UMAP vs PCA, Wilcoxon vs t-test), preprocessing sensitivity, random seed stability, subsample stability on 80% subsets.

> See [sensitivity-analysis.md](sensitivity-analysis.md) for protocols and the supplementary figure template.

---

## 4. Null Hypothesis Testing

Before concluding that an effect exists, verify that the data actually departs from what you'd expect under no effect. Use permutation tests, bootstrap confidence intervals, and explicit null models — not just p-values from parametric assumptions.

**Key practices**: Permutation/shuffle tests, bootstrap CIs, articulating the "null story," checking whether observed data actually departs from null expectation.

> See [null-hypothesis-protocol.md](null-hypothesis-protocol.md) for implementation details.

---

## 5. Adversarial Self-Challenge

Actively try to break your own conclusions. The strongest analysis is one that has survived deliberate attempts to undermine it.

**Key practices**: Find the subset where the conclusion doesn't hold. Articulate the strongest counterargument. Verify directionality/sign of effects. Confirm controls behave as expected. Predict held-out behavior and check.

> See [adversarial-probing.md](adversarial-probing.md) for the challenge protocol.

---

## 6. Spot Checks

Aggregate statistics hide individual stories. For every main result, deep-dive into the most extreme data points (outliers) and trace them back to raw data to understand *why* they're extreme. Always include 1-2 baseline (non-outlier) comparisons so the reader can see what "normal" looks like alongside the extreme case.

**Key practices**: Outlier deep-dives with full raw data narratives, baseline comparisons from the median, per-subgroup spot checks to catch results driven by a single extreme member.

> See [spot-check-protocol.md](spot-check-protocol.md) for the full protocol with code examples.

---

## 7. Failure Mode Analysis

When an algorithm fails or produces wrong values for a subset of the data, understanding *why* it fails is often more valuable than the cases where it succeeds. Never silently exclude failing cases — characterize the failure, trace the causal chain, and assess whether exclusion biases the results.

**Key practices**: Characterize the failing subset vs. successes, step-by-step causal tracing on representative failures, impact assessment (does excluding failures change the conclusion?), fix-or-flag decision framework.

> See [failure-mode-analysis.md](failure-mode-analysis.md) for the full protocol with code examples.

---

## 8. Data Subsetting

Do not subset data unless absolutely necessary. Subsetting discards information, can introduce selection bias, and makes results harder to reproduce. If subsetting seems necessary, confirm with the user before proceeding and document the justification.

**Rules**:
- Default to using the full dataset for every analysis step
- If a subset is needed (e.g., focusing on a subpopulation), ask the user for confirmation first
- Log every subsetting operation: what was removed, why, and how many rows/observations were affected
- After subsetting, re-run key checks from the validation checklist on the remaining data
- Consider whether the analysis question can be answered without subsetting (e.g., using a covariate instead of filtering)

---

## Pre-Flight and Post-Flight

Before starting any analysis and after completing it, run through the QC checklist to catch issues early and verify the final output is internally consistent.

> See [qc-checklist.md](qc-checklist.md) for the pre-flight and post-flight checklists.
