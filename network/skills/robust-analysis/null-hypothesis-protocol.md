# Null Hypothesis Protocol

Before concluding that an effect exists, verify that the data actually departs from what you'd expect under no effect. Parametric p-values rest on assumptions that may not hold. Permutation and bootstrap methods let you test your specific data against your specific null hypothesis without those assumptions.

---

## The Null Story

Before running any statistical test, articulate what the data would look like if there were no effect. This forces clarity about what "no effect" means in your specific context and prevents the common mistake of testing the wrong null.

**Protocol**:
1. State the null hypothesis in plain language (not just "H0: no difference")
2. Describe what the distributions, plots, and summary statistics would look like under the null
3. Run the analysis
4. Compare the observed result to the null expectation — does the data actually depart from it?

**Example**:
```
Null story: If treatment has no effect on gene expression, then the fold-changes
between treatment and control should be centered around 0 with variance determined
only by technical noise. The volcano plot should show a symmetric cloud around (0, 0)
with no genes in the upper-left or upper-right corners beyond what we'd expect by
chance given the number of tests.

Observed: The volcano plot shows 247 genes with |log2FC| > 1 and padj < 0.05,
heavily enriched on the upregulated side. This clearly departs from the null.
```

---

## Permutation / Shuffle Tests

Scramble the labels or key variable and re-run the analysis. If the effect size doesn't meaningfully change, something is wrong — the "effect" isn't associated with the variable you think it is.

**Protocol**:
1. Record the observed test statistic (effect size, correlation, accuracy, etc.)
2. Permute the variable of interest (e.g., shuffle condition labels across samples)
3. Re-run the same analysis pipeline on the permuted data
4. Repeat for N permutations (1000 is a reasonable default; 10000 for publication)
5. Compute the permutation p-value: fraction of permuted statistics as extreme as the observed
6. Plot the null distribution with the observed statistic marked

```python
observed_stat = compute_effect(data, labels)

n_permutations = 1000
null_distribution = []

for i in range(n_permutations):
    permuted_labels = np.random.permutation(labels)
    null_stat = compute_effect(data, permuted_labels)
    null_distribution.append(null_stat)

null_distribution = np.array(null_distribution)
perm_p_value = np.mean(np.abs(null_distribution) >= np.abs(observed_stat))

# Plot
fig, ax = plt.subplots()
ax.hist(null_distribution, bins=50, alpha=0.7, label="Null distribution")
ax.axvline(observed_stat, color="red", linewidth=2, label=f"Observed ({observed_stat:.3f})")
ax.set_xlabel("Test statistic")
ax.set_ylabel("Count")
ax.set_title(f"Permutation test (p = {perm_p_value:.4f}, n_perm = {n_permutations})")
ax.legend()
fig.savefig("outputs/figures/permutation_test.png", dpi=150, bbox_inches="tight")
```

**When to use**:
- When parametric assumptions may not hold
- When the test statistic is complex or non-standard
- As a sanity check alongside parametric tests — if they disagree, investigate why
- When the sample size is small and asymptotic approximations are unreliable

---

## Bootstrap Confidence Intervals

Bootstrap to estimate the uncertainty of any statistic — means, medians, correlations, regression coefficients, derived quantities. This is especially valuable for statistics that don't have clean analytical confidence intervals.

**Protocol**:
1. Resample the data with replacement (same size as original)
2. Compute the statistic of interest on the resample
3. Repeat B times (2000 is a reasonable default; 10000 for publication)
4. Report the percentile confidence interval (2.5th and 97.5th percentiles for 95% CI)
5. Plot the bootstrap distribution

```python
n_bootstrap = 2000
bootstrap_stats = []

for i in range(n_bootstrap):
    resample_idx = np.random.choice(len(data), size=len(data), replace=True)
    resample = data[resample_idx]
    bootstrap_stats.append(compute_statistic(resample))

bootstrap_stats = np.array(bootstrap_stats)
ci_lower = np.percentile(bootstrap_stats, 2.5)
ci_upper = np.percentile(bootstrap_stats, 97.5)
point_estimate = compute_statistic(data)

print(f"Estimate: {point_estimate:.3f} (95% Bootstrap CI: [{ci_lower:.3f}, {ci_upper:.3f}])")
```

**When to use**:
- For any derived quantity where analytical CIs are complex or unavailable
- When the sampling distribution is non-normal (skewed, heavy-tailed)
- For ratios, differences of medians, or other non-standard statistics
- As a complement to parametric CIs — bootstrap CIs that disagree with parametric CIs suggest violated assumptions

---

## Practical Guidelines

**Choosing between permutation and bootstrap**:
- Permutation tests answer: "Is this effect real?" (hypothesis testing)
- Bootstrap answers: "How precise is this estimate?" (confidence intervals)
- Use both when you need both answers

**Computational considerations**:
- For expensive analyses, start with 100 permutations/bootstraps to check feasibility, then scale up
- Set random seeds for reproducibility
- Store the null/bootstrap distributions — don't just store the summary statistics, because you may want to re-examine them later

**Reporting**:
```
Permutation test: observed effect size = 0.45, permutation p-value = 0.003
(1000 permutations, seed=42). The observed effect exceeds 99.7% of the null
distribution (Figure S3).

Bootstrap CI: mean difference = 2.3 (95% Bootstrap CI: [1.1, 3.8], 2000 resamples).
```

**Red flags**:
- Permutation p-value is very different from parametric p-value: investigate the discrepancy (violated assumptions? wrong test?)
- Bootstrap CI includes zero but parametric test is significant (or vice versa): one of them is wrong — figure out which
- Null distribution is not centered at zero: your permutation scheme may not be correctly implementing the null
