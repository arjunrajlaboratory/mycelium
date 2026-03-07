# Spot Check Protocol

Aggregate statistics hide individual stories. A mean fold-change of 2.0 could mean every data point doubled, or it could mean one point went up 100x while the rest didn't move. Spot-checking individual data points — especially outliers — reveals whether your summary statistics are telling the truth.

---

## Protocol 1: Outlier Deep-Dive

For every main result, identify the most extreme data points and trace them back to their raw data. Produce a detailed, visual narrative for each one explaining *why* it ended up extreme.

**Steps**:
1. Identify outliers in the result (top/bottom 1-5 data points by the metric of interest, or points beyond 2-3 standard deviations)
2. For each outlier, pull all available raw data — not just the summary metric, but the underlying measurements, metadata, and context
3. Plot the details: show the raw data that produced the extreme value. For example, if a gene has an extreme fold-change, show its expression distribution across all samples. If a sample is an outlier, show all its measurements compared to the population
4. Write a brief narrative: "Sample X is an outlier because [specific reason traced to raw data]"
5. Determine whether the outlier is informative (reveals real biology/structure) or artifactual (batch effect, technical failure, data entry error)

```python
def spot_check_outliers(df, metric_col, id_col, n_outliers=3):
    """Identify and extract context for extreme data points."""
    sorted_df = df.sort_values(metric_col)

    # Get top and bottom outliers
    bottom = sorted_df.head(n_outliers)
    top = sorted_df.tail(n_outliers)
    outliers = pd.concat([bottom, top])

    print(f"=== Spot-checking {len(outliers)} outliers in '{metric_col}' ===")
    for _, row in outliers.iterrows():
        print(f"\n--- {id_col}={row[id_col]} | {metric_col}={row[metric_col]:.4f} ---")
        # Print all columns for this data point
        for col in df.columns:
            print(f"  {col}: {row[col]}")

    return outliers
```

**What to plot**:
- The outlier's raw data overlaid on the population distribution
- Any relevant metadata (batch, condition, time point) highlighted
- If applicable, the outlier's trajectory or profile compared to non-outliers

**Save to**: `outputs/figures/diagnostic/spot_checks/outlier_[id].png`

---

## Protocol 2: Baseline Comparison

Outlier deep-dives are only meaningful with a point of comparison. For every outlier you investigate, also investigate 1-2 non-outlier data points from the middle of the distribution. These baselines show what "normal" looks like and make the outlier's deviation interpretable.

**Steps**:
1. After identifying outliers, select 1-2 data points near the median of the metric
2. Produce the exact same plots and narrative for the baseline points as you did for the outliers
3. Present them side by side: "Here's what a typical data point looks like. Here's what the outlier looks like. Here's why they differ."

```python
def select_baseline_comparisons(df, metric_col, id_col, n_baselines=2):
    """Select data points near the median for comparison."""
    median_val = df[metric_col].median()
    df["_dist_to_median"] = (df[metric_col] - median_val).abs()
    baselines = df.nsmallest(n_baselines, "_dist_to_median")
    df.drop(columns=["_dist_to_median"], inplace=True)

    print(f"=== Baseline comparisons (near median={median_val:.4f}) ===")
    for _, row in baselines.iterrows():
        print(f"  {id_col}={row[id_col]} | {metric_col}={row[metric_col]:.4f}")

    return baselines
```

**What to report**: Present the outlier and baseline investigations together. The reader should be able to see at a glance what makes the outlier different — and whether that difference is meaningful or artifactual.

**Save to**: `outputs/figures/diagnostic/spot_checks/baseline_[id].png`

---

## Protocol 3: Spot-Check Across Subgroups

When results are stratified by group (conditions, clusters, categories), spot-check the most extreme member of each group, plus one typical member. This catches cases where a group's summary statistic is driven by a single unusual member.

**Steps**:
1. For each group, identify the most extreme member and one median member
2. Produce the same deep-dive and baseline plots
3. If a group's result is dominated by a single extreme member, flag this: "Group A's mean is driven primarily by sample X; removing it changes the group mean from 5.2 to 2.1"

---

## When to Run Spot Checks

- **Always** for any main finding before reporting it — pick the most extreme cases and investigate them
- **Always** when outliers are flagged during pre-flight QC
- **Always** when an aggregate statistic seems surprising (unexpectedly large effect, unexpected direction)
- **Optionally** as part of exploratory analysis to build intuition about the data

Document spot-check results in the analysis documentation file (UPPER_SNAKE_CASE.md) under a "Spot Checks" section. Include the diagnostic figures inline or by reference. A well-executed spot check often reveals more about the data than any summary statistic.
