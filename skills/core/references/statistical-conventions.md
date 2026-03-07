# Statistical Conventions

Domain-agnostic statistical best practices for mycelium-enabled repositories. Domain-specific conventions (installed from network convention packs) override these when applicable.

## Effect Sizes

Always report effect sizes alongside p-values. A statistically significant result with a tiny effect size may not be meaningful.

- For group comparisons: Cohen's d, Hedge's g, or log2 fold change
- For correlations: Pearson's r or Spearman's rho (with confidence interval)
- For regression: R², standardized coefficients
- For categorical outcomes: odds ratio, risk ratio

## Multiple Testing

When performing multiple statistical tests:

1. **State the correction method** (Bonferroni, Benjamini-Hochberg, etc.)
2. **Justify the choice** — Benjamini-Hochberg (FDR) is the default for exploratory analyses; Bonferroni for confirmatory
3. **Report both raw and adjusted p-values** in output tables
4. **Specify the significance threshold** (e.g., FDR < 0.05)
5. **Report the number of tests performed**

## Sample Sizes and Power

- Document sample sizes for every comparison
- For planned studies: perform and document power analysis before data collection
- For exploratory analyses: note the effective sample size and any post-hoc power considerations
- Report the number of observations excluded and why

## Confidence Intervals

Report confidence intervals (typically 95%) for all key estimates. CIs convey both the estimate and its precision, which p-values alone do not.

```
# Good
Mean difference: 2.3 (95% CI: 1.1–3.5, p = 0.002)

# Insufficient
Mean difference: 2.3 (p = 0.002)
```

## Reproducibility

- **Random seeds**: Set and document random seeds for any stochastic process (bootstrapping, cross-validation, simulations, train/test splits)
- **Software versions**: Record the versions of statistical packages used
- **Parameters**: Document all parameters, even defaults — defaults change between versions

```python
# Good
import numpy as np
np.random.seed(42)  # Seed documented in README

# Also good
rng = np.random.default_rng(seed=42)
```

## Model Assumptions

For every statistical model or test:

1. **State the assumptions** (normality, homoscedasticity, independence, etc.)
2. **Document how assumptions were checked** (diagnostic plots, formal tests)
3. **Report violations and their impact** — if assumptions are violated, explain what was done (transformation, non-parametric alternative, robust method)

## Visualizations for Statistical Results

- **Distributions**: Use violin plots or box plots with individual data points overlaid (not bar plots with error bars)
- **Comparisons**: Forest plots for multiple comparisons
- **Correlations**: Scatter plots with regression lines and confidence bands
- **Multiple testing**: Volcano plots, MA plots
- **Model diagnostics**: Q-Q plots, residual plots

Always label axes clearly, include units, and use consistent styling across the project.

## Reporting Template

When documenting statistical results in an analysis README or report:

```
### [Test/Analysis Name]

- **Method**: [statistical method used]
- **Software**: [package and version]
- **Sample sizes**: [n per group or total]
- **Key result**: [estimate with CI and p-value]
- **Effect size**: [measure and value]
- **Multiple testing**: [correction method if applicable]
- **Assumptions**: [how checked, any violations]
- **Random seed**: [if applicable]
```
