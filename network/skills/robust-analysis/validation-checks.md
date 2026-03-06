# Data Validation Checks

Systematic checks to run during analysis. These catch the errors that strict execution rules alone won't — problems that aren't crashes but are still wrong.

## Table of Contents

1. [Row Count Preservation](#row-count-preservation)
2. [Column Type Integrity](#column-type-integrity)
3. [Categorical Variable Levels](#categorical-variable-levels)
4. [Train/Test Leakage](#traintest-leakage)
5. [Distribution Plausibility](#distribution-plausibility)
6. [Simpson's Paradox Check](#simpsons-paradox-check)
7. [Output Consistency](#output-consistency)

---

## Row Count Preservation

Silent row duplication from bad joins is one of the most common agentic coding errors. Check row counts at every step where they could change.

**When to check**: After every merge, join, filter, groupby, pivot, melt, or drop operation.

**What to verify**:
- After a left/right join: result should have the same number of rows as the left/right table (unless you expect expansion)
- After a filter: the number of dropped rows should be plausible given the filter condition
- After a groupby-aggregate: the number of groups should match the number of unique values in the grouping column(s)
- After a pivot: rows x columns should account for all original observations

**Pattern**:
```python
def check_row_count(df_before, df_after, operation_name, expected_delta=None):
    """Call after every operation that could change row count."""
    delta = len(df_after) - len(df_before)
    print(f"[{operation_name}] {len(df_before)} -> {len(df_after)} (delta: {delta:+d})")

    if expected_delta is not None and delta != expected_delta:
        raise ValueError(
            f"Unexpected row count change in {operation_name}: "
            f"expected delta {expected_delta:+d}, got {delta:+d}"
        )
    return delta
```

---

## Column Type Integrity

Agents silently convert things to strings or floats in ways that change semantics. Check column types before and after every transformation.

**Common failure modes**:
- Integer IDs becoming floats after a merge (because NaN forces float promotion in older pandas)
- Categorical columns becoming strings after concatenation
- Datetime columns becoming strings after CSV round-trip
- Boolean columns becoming int after aggregation

**When to check**: After merges, CSV reads, concatenation, and any transformation that restructures the dataframe.

**Pattern**:
```python
def check_types(df, expected_types, step_name):
    """Assert column types match expectations."""
    for col, expected in expected_types.items():
        actual = str(df[col].dtype)
        if actual != expected:
            raise TypeError(
                f"[{step_name}] Column '{col}': expected {expected}, got {actual}. "
                f"Sample values: {df[col].head(3).tolist()}"
            )
```

---

## Categorical Variable Levels

The NaN-as-string problem: a column that should have 3 levels actually has 4 because some NaN values were coerced to the string `"nan"` or `"NaN"`. This silently creates a phantom category.

**What to verify**:
- The number of unique levels matches expectation
- No level is a string representation of a missing value (`"nan"`, `"NaN"`, `"None"`, `"null"`, `"NA"`, `""`, `"N/A"`, `"undetermined"`)
- No unexpected whitespace variants (leading/trailing spaces creating duplicate levels)

**Pattern**:
```python
suspect_values = {"nan", "NaN", "None", "null", "NA", "N/A", "", "none", "undetermined"}

for col in categorical_columns:
    levels = set(df[col].dropna().unique())
    suspicious = levels & suspect_values
    if suspicious:
        raise ValueError(
            f"Column '{col}' contains string representations of missing values: "
            f"{suspicious}. These should be actual NaN, not strings."
        )

    # Check for whitespace variants
    stripped = {str(v).strip() for v in levels}
    if len(stripped) < len(levels):
        raise ValueError(
            f"Column '{col}' has whitespace variants creating duplicate levels. "
            f"Original levels: {sorted(levels)}"
        )
```

---

## Train/Test Leakage

When performing feature engineering, it's easy to inadvertently compute statistics using the full dataset (including test data). This inflates performance metrics and makes results unreliable.

**What to verify**:
- Normalization statistics (mean, std) were computed on training data only
- Feature selection was done on training data only
- Any derived features (e.g., target encoding, polynomial features) were fit on training data only
- No shuffling was done before a time-based split
- No data augmentation was applied to test data

**Pattern**:
```python
# After any train/test split, verify no overlap
train_ids = set(train_df["sample_id"])
test_ids = set(test_df["sample_id"])
overlap = train_ids & test_ids
assert len(overlap) == 0, (
    f"Train/test overlap: {len(overlap)} shared sample IDs. "
    f"Examples: {list(overlap)[:5]}"
)

# After normalization, verify test stats differ from train stats
# (if they're identical, normalization was likely done on the full dataset)
train_mean = train_df["feature"].mean()
test_mean = test_df["feature"].mean()
# These should NOT be exactly 0 (or exactly equal to each other) after normalization
```

---

## Distribution Plausibility

For any derived quantity, check that its range, mean, and variance are plausible given domain knowledge. If gene expression values are negative or cell counts are fractional, something went wrong upstream.

**What to verify**:
- Range: are min/max values physically possible?
- Scale: is the order of magnitude correct?
- Shape: is the distribution roughly what you'd expect (unimodal? skewed? zero-inflated?)
- Transformation artifacts: are there signs of double-transformation?

**Double-transformation detection** — agents will sometimes z-score data that's already z-scored, or log-transform data that's already log-transformed:

```python
def check_for_double_transform(series, name):
    """Detect likely double z-score or double log-transform."""
    mean, std = series.mean(), series.std()

    # Double z-score: mean ~ 0, std ~ 1 but range is very tight
    # (original z-score has std=1, double z-score compresses further)
    if abs(mean) < 0.01 and abs(std - 1.0) < 0.01:
        print(f"WARNING: '{name}' appears already z-scored (mean={mean:.4f}, std={std:.4f}). "
              f"Verify this isn't being z-scored again.")

    # Double log-transform: values are very compressed, many negative
    if series.min() < -10 and std < 2:
        print(f"WARNING: '{name}' has very negative values (min={series.min():.2f}) "
              f"with low variance (std={std:.2f}). Check for double log-transform.")
```

**Always plot raw distributions** of key variables before and after transformations. Save these as diagnostic figures in the outputs directory.

---

## Simpson's Paradox Check

A trend that appears in pooled data can reverse within subgroups. This is especially relevant when the agent pools heterogeneous data (multiple batches, conditions, cell types, patient cohorts).

**When to check**: Whenever you're reporting a trend, correlation, or comparison on pooled data that could contain meaningful subgroups.

**Pattern**:
```python
# Check overall trend
overall_corr = df["x"].corr(df["y"])

# Check within each subgroup
for group_name, group_df in df.groupby("batch"):
    group_corr = group_df["x"].corr(group_df["y"])
    if np.sign(group_corr) != np.sign(overall_corr):
        raise ValueError(
            f"Simpson's paradox detected: overall correlation is "
            f"{overall_corr:.3f} but {group_name} has {group_corr:.3f}. "
            f"The pooled trend reverses within this subgroup."
        )
```

---

## Output Consistency

The numbers in a summary table should be recoverable from the underlying data shown in plots. When multiple outputs are generated, cross-reference them.

**What to verify**:
- Counts in a summary table match the number of points in the corresponding scatter plot
- Percentages in text match the data in figures
- Reported n's match expectations at every stage of the analysis
- If a figure shows group A has 150 points and group B has 200 points, the summary table should say n_A=150, n_B=200

**Pattern**:
```python
# After generating both a summary table and a figure, cross-check
summary_n = summary_table["n"].sum()
figure_n = len(plot_data)
assert summary_n == figure_n, (
    f"Summary table reports n={summary_n} but figure data has {figure_n} points. "
    f"Check for filtering discrepancies between the two code paths."
)
```

This often catches cases where the figure-generation code and the table-generation code apply slightly different filters — a common bug when analysis code evolves incrementally.
