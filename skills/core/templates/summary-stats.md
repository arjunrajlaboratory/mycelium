<!-- Summary Stats Template -->
<!-- Copy to data/metadata/[dataset-name]/summary_stats.md -->
<!-- Fill in after data is placed in data/raw/[dataset-name]/ -->

# Summary Statistics: [dataset-name]

<!-- Generated: [YYYY-MM-DD] -->
<!-- Script: [path/to/script.py or "manual"] -->

## Overview

| Property | Value |
|----------|-------|
| Rows | <!-- Fill in after data is placed --> |
| Columns | <!-- Fill in after data is placed --> |
| File size | <!-- e.g., "1.4 GB" --> |
| Date range | <!-- YYYY-MM-DD to YYYY-MM-DD, or "N/A" if not temporal --> |
| Format | <!-- CSV / TSV / Parquet / etc. --> |

## Column summaries

<!-- One row per column. For numeric columns: report non-null count, min, max, mean, std. -->
<!-- For categorical columns: report non-null count, number of unique values, top 3 most frequent. -->
<!-- Delete columns that don't apply to each type. -->

| Column | Type | Non-null | Unique | Min | Max | Mean | Top values |
|--------|------|----------|--------|-----|-----|------|------------|
| <!-- column_name --> | <!-- numeric --> | <!-- N --> | <!-- — --> | <!-- val --> | <!-- val --> | <!-- val --> | <!-- — --> |
| <!-- column_name --> | <!-- categorical --> | <!-- N --> | <!-- K --> | <!-- — --> | <!-- — --> | <!-- — --> | <!-- val1 (n), val2 (n) --> |

<!-- Fill in after data is placed -->

## Missing data summary

<!-- List only columns with missing values. If no missingness, write "No missing data." -->

| Column | Missing count | Missing % | Pattern / notes |
|--------|---------------|-----------|-----------------|
| <!-- column_name --> | <!-- N --> | <!-- X.X% --> | <!-- e.g., "Missing only for cohort B" --> |

<!-- Fill in after data is placed -->

## Quality flags

<!-- Document anomalies, outliers, or concerns that affect downstream analyses. -->
<!-- If none, write "No quality flags." -->

- <!-- [Flag 1: e.g., "Column X has 3 values > 3 SD from mean — verify before excluding."] -->
- <!-- [Flag 2: e.g., "Row count differs from expected N=500 in source documentation."] -->

<!-- Fill in after data is placed -->

## Notes

<!-- Any additional observations not captured above. -->

<!-- Fill in after data is placed -->
