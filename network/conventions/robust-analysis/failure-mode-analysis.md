# Failure Mode Analysis

When an algorithm or analysis step fails on a subset of the data — producing errors, wrong values, or nonsensical results — the instinct is to filter those cases out and move on. Resist this instinct. Understanding *why* something fails is often more scientifically valuable than the cases where it succeeds. A failure mode can reveal data structure, edge cases, or violated assumptions that affect the entire analysis, not just the failing subset.

---

## Protocol 1: Characterize the Failure

Before trying to fix a failure, understand it completely. Produce a detailed characterization of what went wrong and for which data points.

**Steps**:
1. Identify all data points where the algorithm fails, produces errors, or gives implausible results
2. Quantify the failure: what fraction of data is affected? Is it random or systematic?
3. Characterize the failing subset: what do these data points have in common? Compare their metadata, distributions, and raw data to the succeeding subset
4. Produce diagnostic plots showing the failing cases alongside succeeding cases

```python
def characterize_failures(df, result_col, id_col, is_failure_fn):
    """Separate failures from successes and compare their properties."""
    df["_failed"] = df.apply(is_failure_fn, axis=1)
    failures = df[df["_failed"]]
    successes = df[~df["_failed"]]

    print(f"=== Failure characterization for '{result_col}' ===")
    print(f"  Total: {len(df)} | Failed: {len(failures)} ({len(failures)/len(df):.1%})")

    # Compare distributions of all numeric columns
    for col in df.select_dtypes(include="number").columns:
        if col.startswith("_"):
            continue
        f_mean, f_std = failures[col].mean(), failures[col].std()
        s_mean, s_std = successes[col].mean(), successes[col].std()
        print(f"  {col}: failures={f_mean:.3f}+/-{f_std:.3f} | "
              f"successes={s_mean:.3f}+/-{s_std:.3f}")

    # Compare categorical distributions
    for col in df.select_dtypes(include=["object", "category"]).columns:
        print(f"\n  {col} distribution:")
        f_dist = failures[col].value_counts(normalize=True)
        s_dist = successes[col].value_counts(normalize=True)
        for val in set(f_dist.index) | set(s_dist.index):
            f_pct = f_dist.get(val, 0)
            s_pct = s_dist.get(val, 0)
            if abs(f_pct - s_pct) > 0.05:  # Flag imbalances > 5%
                print(f"    {val}: failures={f_pct:.1%} | successes={s_pct:.1%} ***")

    return failures, successes
```

**What to plot**:
- Distribution of key variables for failures vs. successes (side-by-side histograms or violin plots)
- Scatter plots colored by failure/success to see spatial patterns
- If temporal data: timeline showing when failures occur

**Save to**: `outputs/figures/diagnostic/failure_analysis/`

---

## Protocol 2: Trace the Causal Chain

Once you know *which* data points fail, trace *why* they fail. Walk through the algorithm step by step on a failing case and identify exactly where things go wrong.

**Steps**:
1. Pick 2-3 representative failure cases (not just the most extreme — pick ones that represent the most common failure pattern)
2. Run the algorithm step-by-step on each case, printing intermediate values
3. Identify the specific step where the output becomes wrong or implausible
4. Determine the root cause: is it a data property (e.g., too few observations, extreme values, missing data), an algorithm limitation (e.g., convergence failure, numerical instability), or a code bug?

```python
def trace_failure(data_point, algorithm_steps):
    """Run algorithm step-by-step on a single data point, logging intermediates."""
    print(f"=== Tracing failure for {data_point['id']} ===")
    intermediate = data_point

    for step_name, step_fn in algorithm_steps:
        try:
            result = step_fn(intermediate)
            print(f"  Step '{step_name}': input={intermediate} -> output={result}")

            # Check for implausible intermediates
            if isinstance(result, (int, float)) and (np.isnan(result) or np.isinf(result)):
                print(f"  *** NaN/Inf detected at step '{step_name}' ***")

            intermediate = result
        except Exception as e:
            print(f"  *** Exception at step '{step_name}': {e} ***")
            return step_name, e

    return None, intermediate
```

**What to document**: "The algorithm fails for samples with fewer than 5 observations because the variance estimate becomes unstable at step 3 (normalization), producing NaN values that propagate through the remaining steps."

---

## Protocol 3: Assess Impact on Conclusions

Failures are not just data quality problems — they can bias your results. Determine whether the failing cases are missing at random or whether their exclusion systematically changes the conclusion.

**Steps**:
1. Run the full analysis with and without the failing cases
2. Compare the results: does the conclusion change? By how much?
3. Check whether the failing cases are enriched in a particular condition, group, or covariate
4. If failures are non-random (e.g., disproportionately from one condition), the analysis is biased by their exclusion — report this explicitly

```python
# Compare results with and without failures
result_all = run_analysis(full_data)
result_no_failures = run_analysis(full_data[~full_data["_failed"]])

print(f"With all data:      effect = {result_all:.3f}")
print(f"Without failures:   effect = {result_no_failures:.3f}")
print(f"Delta:              {result_no_failures - result_all:.3f}")

# Check if failures are enriched in a condition
for condition in full_data["condition"].unique():
    cond_data = full_data[full_data["condition"] == condition]
    failure_rate = cond_data["_failed"].mean()
    print(f"  {condition}: {failure_rate:.1%} failure rate")
```

**What to report**: "12% of samples (n=24) failed the normalization step. These failures are enriched in the treatment group (18% failure rate) vs. control (6% failure rate). Excluding them shifts the treatment effect from 1.8 to 2.3, suggesting the failures attenuate the observed effect. The failures occur in samples with low read counts (<1000), which are more common in treated samples due to [reason]."

---

## Protocol 4: Fix or Flag

Based on the causal chain and impact assessment, decide on the appropriate action.

**Options** (in order of preference):
1. **Fix the root cause**: If the failure is due to a code bug or an overly strict assumption, fix it. Re-run on all data
2. **Adapt the algorithm**: If certain data points require different treatment (e.g., a different normalization for low-count samples), implement the adaptation explicitly and document it
3. **Exclude with full documentation**: If exclusion is truly necessary, document exactly what was excluded, why, and what impact it has on the results. Include the failure analysis figures in the report
4. **Never silently exclude**: Filtering out failing cases without investigation and documentation is not acceptable

---

## When to Run Failure Mode Analysis

- **Always** when an algorithm fails on any subset of the data (errors, NaN, implausible values)
- **Always** when you notice that results look "cleaner" after dropping certain data points — investigate why those points were problematic before dropping them
- **Always** when a QC step flags data points for removal — understand what they have in common before removing them
- **Optionally** when algorithm performance varies across subgroups — even without outright failure, understanding why performance differs is valuable

Document failure mode analysis in the analysis documentation file (UPPER_SNAKE_CASE.md) under a "Failure Mode Analysis" section. Include the characterization, causal chain, impact assessment, and the decision made. This documentation is essential for reproducibility — another analyst needs to understand not just what was excluded, but why.
