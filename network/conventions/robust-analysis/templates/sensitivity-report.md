# Sensitivity Analysis Report: [Analysis Name]

## Overview

This supplementary document reports the sensitivity of the main findings to analytical choices. Each section corresponds to a decision point in the analysis and shows how the result changes when that decision is varied.

---

## Threshold Sensitivity

### [Threshold 1 Name] (e.g., "Adjusted p-value cutoff")

- **Chosen value**: [value]
- **Range tested**: [min] to [max] in steps of [step]
- **Result**: [Stable / Sensitive / Highly sensitive]
- **Figure**: `outputs/figures/supplementary/threshold_sensitivity_[name].png`

**Summary**: [1-2 sentences on whether the conclusion holds across the range]

### [Threshold 2 Name] (e.g., "Log2 fold-change cutoff")

- **Chosen value**: [value]
- **Range tested**: [min] to [max]
- **Result**: [Stable / Sensitive / Highly sensitive]
- **Figure**: `outputs/figures/supplementary/threshold_sensitivity_[name].png`

**Summary**: [1-2 sentences]

---

## Method Sensitivity

### [Decision Name] (e.g., "Clustering algorithm")

- **Chosen method**: [method]
- **Alternatives tested**: [list]
- **Agreement metric**: [e.g., Adjusted Rand Index]
- **Figure**: `outputs/figures/supplementary/method_comparison_[name].png`

| Method | Key Result | Agreement with Chosen |
|--------|-----------|----------------------|
| [Chosen] | [result] | -- |
| [Alt 1] | [result] | [agreement] |
| [Alt 2] | [result] | [agreement] |

**Summary**: [1-2 sentences on whether conclusion is method-dependent]

---

## Preprocessing Sensitivity

### [Preprocessing Decision] (e.g., "Normalization method")

- **Chosen approach**: [approach]
- **Alternatives tested**: [list]
- **Figure**: `outputs/figures/supplementary/preprocessing_sensitivity_[name].png`

**Summary**: [1-2 sentences]

---

## Random Seed Stability

- **Seeds tested**: [list of seeds]
- **Key metric**: [metric name]
- **Result**: [mean] +/- [std] across seeds
- **Figure**: `outputs/figures/supplementary/random_seed_stability.png`

**Summary**: [1-2 sentences]

---

## Subsample Stability

- **Subsample fraction**: 80%
- **Number of subsamples**: [N]
- **Conclusion holds in**: [X/N] subsamples
- **Effect size range**: [min] to [max]
- **Figure**: `outputs/figures/supplementary/subsample_stability.png`

**Summary**: [1-2 sentences]

---

## Overall Assessment

[2-3 sentences summarizing the overall robustness of the findings. Which aspects are most and least robust? Are there any caveats that should be highlighted in the main text?]
