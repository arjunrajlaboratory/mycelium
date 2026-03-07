# Sensitivity Analysis Protocol

Every analytical decision is a parameter. If the conclusion changes when you wiggle a parameter, the conclusion is about the parameter choice, not about the data. Sensitivity analysis turns implicit decisions into explicit, documented ones.

**The core rule**: for every decision point in an analysis, sweep it and generate a supplementary figure showing how the result changes. These figures go into the analysis outputs and are referenced in the report.

---

## When to Run Sensitivity Analysis

Run a sensitivity sweep whenever:
- A threshold or cutoff is chosen (p-value, fold-change, quality score, cluster count)
- A method is selected from alternatives (dimensionality reduction, clustering, statistical test)
- A preprocessing step has tunable parameters (normalization method, imputation strategy, QC threshold)
- A random seed controls the outcome (train/test split, clustering, bootstrapping)

If the analysis involves no decisions, sensitivity analysis isn't needed. But most analyses involve many decisions — be honest about which ones are choices vs. which are forced by the data.

---

## Threshold Sensitivity

When a cutoff is chosen, sweep it across a reasonable range and plot the downstream result as a function of the threshold.

**Protocol**:
1. Identify the threshold and its reasonable range (e.g., p-value cutoff from 0.01 to 0.10)
2. Run the downstream analysis at each threshold value
3. Plot: x-axis = threshold, y-axis = the result of interest (number of significant genes, classification accuracy, cluster count, etc.)
4. Note where the chosen threshold falls on the curve
5. Report whether the conclusion is stable across the range or sensitive to the specific choice

```python
thresholds = np.arange(0.01, 0.11, 0.01)
results = []
for thresh in thresholds:
    significant = de_results[de_results["padj"] < thresh]
    results.append({"threshold": thresh, "n_significant": len(significant)})

results_df = pd.DataFrame(results)

fig, ax = plt.subplots()
ax.plot(results_df["threshold"], results_df["n_significant"], marker="o")
ax.axvline(chosen_threshold, color="red", linestyle="--", label=f"Chosen: {chosen_threshold}")
ax.set_xlabel("Adjusted p-value threshold")
ax.set_ylabel("Number of significant genes")
ax.set_title("Threshold Sensitivity: Significant Gene Count")
ax.legend()
fig.savefig("outputs/figures/supplementary/threshold_sensitivity_pvalue.png", dpi=150, bbox_inches="tight")
```

**What to look for**:
- A smooth curve suggests the result is robust — the exact threshold matters less
- A sharp cliff or jump means the result is fragile at that point
- If the conclusion flips within the reasonable range, the threshold choice is the story, not the biology

---

## Method Sensitivity

If a method was chosen from alternatives, run the key alternatives and compare.

**Common comparisons**:

| Decision | Run these alternatives |
|----------|----------------------|
| Dimensionality reduction | UMAP, PCA, t-SNE |
| Clustering | Leiden, k-means, hierarchical |
| Statistical test | Wilcoxon, t-test, permutation test |
| Normalization | SCTransform, log-normalize, scran |
| Differential expression | DESeq2, edgeR, limma-voom |
| Batch correction | Harmony, ComBat, scVI, no correction |

**Protocol**:
1. Run each alternative method with otherwise identical parameters
2. Compare the key output (cluster assignments, significant gene lists, embedding structure)
3. Quantify agreement (e.g., Adjusted Rand Index for clustering, Jaccard index for gene lists)
4. Generate a comparison figure (side-by-side embeddings, Venn diagram of significant genes, etc.)

```python
# Example: compare clustering methods
from sklearn.metrics import adjusted_rand_score

methods = {
    "Leiden (chosen)": leiden_labels,
    "K-means": kmeans_labels,
    "Hierarchical": hierarchical_labels,
}

# Pairwise ARI
for (name1, labels1), (name2, labels2) in combinations(methods.items(), 2):
    ari = adjusted_rand_score(labels1, labels2)
    print(f"ARI({name1} vs {name2}): {ari:.3f}")
```

**What to look for**:
- High agreement (ARI > 0.8) across methods: the structure is real and robust
- Low agreement: the result depends on the method, which means it's not a reliable finding without additional evidence

---

## Preprocessing Sensitivity

Different preprocessing choices can dramatically change downstream results. Test the key alternatives.

**Protocol**:
1. Identify preprocessing decisions: normalization, QC thresholds, imputation, feature selection
2. Define 2-3 reasonable alternatives for each
3. Run the full downstream analysis with each alternative
4. Compare final results

**Common preprocessing sweeps**:
- QC thresholds: try strict, moderate, and lenient cutoffs
- Normalization: try the chosen method plus one alternative
- Imputation: try the chosen method, no imputation, and one alternative
- Feature selection: vary the number of features (e.g., top 1000, 2000, 5000 variable genes)

---

## Random Seed Stability

Re-run the entire analysis with 3-5 different random seeds. If conclusions change, something depends on a stochastic outcome that happened to be favorable.

**Protocol**:
1. Identify all stochastic steps (train/test split, clustering initialization, bootstrapping, UMAP embedding)
2. Run with seeds [42, 123, 456, 789, 2024]
3. Compare key results across seeds
4. Report the range of variation

```python
seeds = [42, 123, 456, 789, 2024]
seed_results = []

for seed in seeds:
    np.random.seed(seed)
    result = run_analysis(data, seed=seed)
    seed_results.append({"seed": seed, "n_clusters": result.n_clusters,
                         "accuracy": result.accuracy})

seed_df = pd.DataFrame(seed_results)
print(f"Accuracy across seeds: {seed_df['accuracy'].mean():.3f} +/- {seed_df['accuracy'].std():.3f}")
print(f"Cluster count range: {seed_df['n_clusters'].min()} - {seed_df['n_clusters'].max()}")
```

**What to look for**: If the standard deviation across seeds is large relative to the effect size, the result is not stable.

---

## Subsample Stability

Run the analysis on random 80% subsets of the data. If conclusions flip, you're probably overfitting to noise.

**Protocol**:
1. Draw 5 random 80% subsets (without replacement)
2. Run the full analysis on each subset
3. Compare conclusions across subsets
4. Report the fraction of subsets where the main conclusion holds

```python
n_subsamples = 5
subsample_results = []

for i in range(n_subsamples):
    subset = df.sample(frac=0.8, random_state=i)
    result = run_analysis(subset)
    subsample_results.append({
        "subsample": i,
        "conclusion_holds": result.main_effect_significant,
        "effect_size": result.effect_size
    })

stability = sum(r["conclusion_holds"] for r in subsample_results) / n_subsamples
print(f"Conclusion holds in {stability*100:.0f}% of subsamples")
```

**Interpretation**: If the conclusion holds in fewer than 4 out of 5 subsamples, it's fragile.

---

## Supplementary Figure Organization

All sensitivity analysis figures go in `outputs/figures/supplementary/` with clear, descriptive names:

```
outputs/figures/supplementary/
  threshold_sensitivity_pvalue.png
  threshold_sensitivity_foldchange.png
  method_comparison_clustering.png
  method_comparison_de.png
  preprocessing_sensitivity_normalization.png
  random_seed_stability.png
  subsample_stability.png
```

Each figure should be self-contained: title, axis labels, legend, and a note indicating what was varied and what was held constant. Use the sensitivity report template for organizing these into a coherent supplementary section.

> See [templates/sensitivity-report.md](templates/sensitivity-report.md) for the supplementary report template.
