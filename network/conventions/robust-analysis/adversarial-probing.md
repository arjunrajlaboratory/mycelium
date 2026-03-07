# Adversarial Self-Challenge Protocol

The strongest analysis is one that has survived deliberate attempts to break it. After reaching a conclusion, systematically try to undermine it. If the conclusion survives, you can report it with confidence. If it doesn't, you've learned something more important than the original finding.

---

## Protocol 1: Find the Breaking Subset

For any main conclusion, search for a subset of the data where the conclusion doesn't hold.

**Steps**:
1. Identify the main finding (e.g., "Gene X is upregulated in treatment vs control")
2. Stratify by every available covariate: batch, time point, cell type, patient, site
3. Re-run the analysis within each stratum
4. Report any strata where the finding reverses, vanishes, or weakens substantially

```python
main_effect = compute_effect(full_data)

for covariate in ["batch", "timepoint", "cell_type", "patient_id"]:
    for group_name, group_data in full_data.groupby(covariate):
        group_effect = compute_effect(group_data)

        if np.sign(group_effect) != np.sign(main_effect):
            print(f"REVERSAL in {covariate}={group_name}: "
                  f"effect={group_effect:.3f} vs overall={main_effect:.3f}")
        elif abs(group_effect) < abs(main_effect) * 0.1:
            print(f"VANISHES in {covariate}={group_name}: "
                  f"effect={group_effect:.3f} vs overall={main_effect:.3f}")
```

**What to report**: "The main effect holds in 8/10 patients. In patient 3, the effect reverses (log2FC = -0.4 vs overall +1.2). In patient 7, the effect is near zero. See supplementary Figure S5."

This is not failure — this is thorough science. Knowing where a finding breaks is as informative as the finding itself.

---

## Protocol 2: Strongest Counterargument

Before reporting a finding, articulate the strongest argument against it. Then evaluate whether the analysis has addressed it.

**Steps**:
1. State the finding
2. Ask: "What's the most plausible alternative explanation?"
3. For each alternative, identify what evidence would distinguish it from the claimed finding
4. Check whether that evidence exists in the data
5. Document the counterarguments and your responses in the analysis README

**Common counterarguments to check**:
- "This is a batch effect, not a biological effect" — Check by stratifying by batch
- "This is driven by outliers" — Re-run with outliers removed
- "This is a sample size artifact" — Check with subsample stability
- "This is confounded by [covariate]" — Include the covariate in the model
- "This is multiple testing inflation" — Verify correction was applied correctly

---

## Protocol 3: Directionality Verification

Sign errors are shockingly common. If gene X is supposed to be upregulated in condition A, verify the sign is correct in the actual coefficient or fold-change.

**Steps**:
1. For every reported direction (up/down, positive/negative, increase/decrease), trace it back to the raw numbers
2. Compute the group means or medians directly and verify the direction matches
3. Check that the sign convention of the statistical method matches your expectation (some tools report condition - control, others report control - condition)

```python
# Direct verification of fold-change direction
treatment_mean = df[df["condition"] == "treatment"]["expression"].mean()
control_mean = df[df["condition"] == "control"]["expression"].mean()
direct_log2fc = np.log2(treatment_mean / control_mean)

# Compare with what the DE tool reported
reported_log2fc = de_results.loc["GeneX", "log2FoldChange"]

assert np.sign(direct_log2fc) == np.sign(reported_log2fc), (
    f"Sign mismatch for GeneX: direct computation gives log2FC={direct_log2fc:.3f} "
    f"but DE tool reports {reported_log2fc:.3f}. Check reference level in the model."
)
```

---

## Protocol 4: Control Verification

If your experimental design includes positive or negative controls, verify they behave as expected before trusting the experimental results.

**Steps**:
1. Identify all controls in the data (positive controls that should show an effect, negative controls that should not)
2. Run the analysis on controls first
3. If positive controls don't show the expected effect, the pipeline has a problem regardless of what experimental conditions show
4. If negative controls show an unexpected effect, investigate before proceeding

**What to report**: "Positive control (housekeeping gene panel) showed expected stable expression across conditions (CV < 5%). Negative control (empty wells) showed no signal above background. These confirm the pipeline is functioning correctly before examining experimental conditions."

---

## Protocol 5: Predictive Check

If your analysis produces a model or pattern, use it to make a prediction, then check.

**Steps**:
1. Hold out a portion of the data (or use an independent dataset)
2. Use the model/pattern from the main analysis to predict what the held-out data should look like
3. Compare prediction to reality
4. Report the agreement

This is the ultimate test: if the model can't predict new data, it's describing noise in the training data, not real structure.

```python
# Example: if clustering identified 5 cell types, predict cluster labels for held-out cells
from sklearn.neighbors import KNeighborsClassifier

knn = KNeighborsClassifier(n_neighbors=15)
knn.fit(train_embedding, train_labels)
predicted_labels = knn.predict(test_embedding)

agreement = np.mean(predicted_labels == test_labels)
print(f"Held-out prediction accuracy: {agreement:.1%}")
# If this is near chance (1/n_clusters), the clusters aren't real structure
```

---

## When to Run Adversarial Probing

Run all five protocols for any finding that will be reported as a main result. For intermediate or exploratory findings, protocols 1 (breaking subset) and 3 (directionality) are the minimum.

Document the results of adversarial probing in the analysis documentation file (UPPER_SNAKE_CASE.md) under a "Robustness Checks" section. Findings that survive adversarial probing are reported with higher confidence. Findings that don't survive should be reported as preliminary or exploratory, with the limitations clearly stated.
