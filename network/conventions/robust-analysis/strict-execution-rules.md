# Strict Execution Rules

In data analysis, silent recovery is where the worst errors hide. These rules enforce "strict analysis mode" — code that crashes informatively rather than producing wrong results quietly.

## Rule 1: Assert After Every Major Operation

After every filter, join, groupby, pivot, or transformation, assert the expected shape, types, and value ranges. Use actual `assert` statements or `raise ValueError` with informative messages — not just print statements you might miss.

```python
# After a merge
merged = left.merge(right, on="sample_id", how="left")
assert merged.shape[0] == left.shape[0], (
    f"Merge changed row count: {left.shape[0]} -> {merged.shape[0]}. "
    f"Check for duplicates in right table on 'sample_id'."
)

# After a transformation
assert df["expression"].dtype == np.float64, (
    f"Expected float64 for expression column, got {df['expression'].dtype}"
)
assert df["expression"].min() >= 0, (
    f"Negative expression values found (min={df['expression'].min():.4f}). "
    f"Check upstream normalization."
)
```

## Rule 2: Log Row Counts at Every Step

Before and after every filter, join, groupby, or drop operation, log the row count. If the delta is unexpected, halt execution — do not proceed.

```python
n_before = len(df)
df = df[df["quality_score"] >= threshold]
n_after = len(df)
n_dropped = n_before - n_after
pct_dropped = 100 * n_dropped / n_before

print(f"Filter on quality_score >= {threshold}: {n_before} -> {n_after} "
      f"({n_dropped} dropped, {pct_dropped:.1f}%)")

# Halt if we lost more than expected
if pct_dropped > 50:
    raise ValueError(
        f"Unexpectedly dropped {pct_dropped:.1f}% of rows in quality filter. "
        f"Review threshold={threshold} against data distribution."
    )
```

## Rule 3: No Silent Coercion

Never use `errors='coerce'`, `errors='ignore'`, `on_error='skip'`, or equivalent silent-failure flags without an explicit comment justifying why. If a column isn't the expected type, stop and report why — don't silently convert it.

```python
# BAD - silently converts unparseable values to NaN
df["value"] = pd.to_numeric(df["value"], errors="coerce")

# GOOD - detect the problem, then decide explicitly
non_numeric = df[pd.to_numeric(df["value"], errors="coerce").isna() & df["value"].notna()]
if len(non_numeric) > 0:
    raise ValueError(
        f"Column 'value' contains {len(non_numeric)} non-numeric entries: "
        f"{non_numeric['value'].unique()[:5].tolist()}"
    )
df["value"] = df["value"].astype(float)
```

## Rule 4: No Silent NaN Handling

If NaN appears where it wasn't expected, raise an error that reports which column, how many NaNs, and what fraction of the data is affected. Never fill NaN as an automatic recovery step.

```python
# Check for unexpected NaN after any operation
for col in critical_columns:
    n_nan = df[col].isna().sum()
    if n_nan > 0:
        raise ValueError(
            f"Unexpected NaN in '{col}': {n_nan}/{len(df)} "
            f"({100*n_nan/len(df):.1f}%) values are missing."
        )
```

If imputation is the right analytical decision, it must be an explicit, justified step — never a fallback in a try/except block:

```python
# GOOD - explicit imputation with justification
# Imputing median for 3 missing values in 'age' (0.1% of data).
# Median chosen because age distribution is right-skewed.
# Decision logged in .living/decisions.md
median_age = df["age"].median()
df["age"] = df["age"].fillna(median_age)
```

## Rule 5: No Bare Exception Handling

Do not use bare `except` or `except Exception` in analysis code. If you must catch an exception, log the full traceback and re-raise unless the recovery strategy is explicitly justified.

```python
# BAD - swallows errors, analysis continues with wrong state
try:
    result = complex_analysis(data)
except Exception:
    result = default_value

# GOOD - catch specific exceptions, log and re-raise
try:
    result = complex_analysis(data)
except np.linalg.LinAlgError as e:
    print(f"Linear algebra error in complex_analysis: {e}")
    print(f"Data shape: {data.shape}, condition number: {np.linalg.cond(data):.2e}")
    raise
```

## Rule 6: Guard Against Many-to-Many Joins

If a merge produces more rows than the larger input table, that is almost certainly an unintended many-to-many join. Halt and report.

```python
n_left, n_right = len(left), len(right)
merged = left.merge(right, on=key)
n_merged = len(merged)

if n_merged > max(n_left, n_right):
    raise ValueError(
        f"Merge on '{key}' produced {n_merged} rows from inputs of "
        f"{n_left} and {n_right}. This suggests a many-to-many join. "
        f"Check for duplicate keys: "
        f"left has {left[key].duplicated().sum()} duplicates, "
        f"right has {right[key].duplicated().sum()} duplicates."
    )
```

## Rule 7: Treat Warnings as Signals

Warnings like `SettingWithCopyWarning`, `RuntimeWarning` for divide-by-zero, or `FutureWarning` about deprecated behavior are telling you something real. Do not suppress them. Where feasible, promote them to errors:

```python
import warnings
warnings.filterwarnings("error", category=RuntimeWarning)

# Or at minimum, investigate every warning:
# If you see SettingWithCopyWarning, use .loc[] or .copy() explicitly
# If you see RuntimeWarning for divide-by-zero, check for zero denominators
```

## Rule 8: No Default Parameter Substitution

If a parameter can't be used as specified, halt execution. Do not silently fall back to a default.

```python
# BAD - silently reduces n_clusters because data doesn't support the requested value
n_clusters = min(requested_clusters, max_possible_clusters)

# GOOD - halt and explain
if requested_clusters > max_possible_clusters:
    raise ValueError(
        f"Requested n_clusters={requested_clusters} but data only supports "
        f"up to {max_possible_clusters} clusters (n_samples={n_samples}). "
        f"Reduce n_clusters or provide more data."
    )
```

## Rule 9: Explicit Column Type Contracts

At the boundaries of each analysis step, assert the expected types of key columns. Type drift (e.g., integers becoming floats after a merge, or categoricals becoming strings) is a common source of subtle bugs.

```python
expected_types = {
    "sample_id": "object",
    "gene_count": "int64",
    "expression": "float64",
    "condition": "category",
}

for col, expected in expected_types.items():
    actual = str(df[col].dtype)
    assert actual == expected, (
        f"Column '{col}' has type '{actual}', expected '{expected}'. "
        f"Check upstream operations for unintended type changes."
    )
```

## Summary

The overarching principle: **if something unexpected happens, stop and tell the human.** Every rule above is a specific instance of this principle. A crash with a clear error message takes 5 minutes to fix. A silent error that propagates into a figure can take weeks to discover — if it's discovered at all.
