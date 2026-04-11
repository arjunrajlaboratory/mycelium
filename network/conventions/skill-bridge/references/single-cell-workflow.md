# Single-Cell RNA-seq Workflow

Step-by-step workflow for scRNA-seq analysis with skill routing, persona checkpoints, and convention-enforced validation at each step.

---

## Persona Panel

Load operational fields from these personas at workflow start:

| Role | Persona | File | Load Fields |
|------|---------|------|-------------|
| Primary collaborator | Arjun Raj | `skillpacks/Autonomous-Science/personas/library/arjun_raj.json` | decision_rules, anti_patterns |
| Methods reviewer | Cole Trapnell | `skillpacks/Autonomous-Science/personas/library/cole_trapnell.json` | decision_rules, anti_patterns |
| Statistical reviewer | Andrew Gelman | `skillpacks/Autonomous-Science/personas/library/andrew_gelman.json` | decision_rules, anti_patterns |

Total context cost for persona panel: ~900 words.

---

## Step 1: Data Loading

**Skill**: Read `skillpacks/bioSkills/single-cell/data-io/SKILL.md`
**Fallback**: Read `skillpacks/scientific-agent-skills/scientific-skills/anndata/SKILL.md`

**Actions**:
- Load raw count matrix (10x HDF5, MTX, or h5ad)
- Verify cell and gene counts match expected values from the data manifest
- Check for expected metadata columns (sample, condition, batch)

**Validation**:
- [ ] Cell count matches DATA_MANIFEST.md entry
- [ ] Gene count is plausible for organism (human: 20,000-60,000; mouse: 20,000-55,000)
- [ ] No duplicate barcodes
- [ ] Metadata columns present and complete

**Decision to log**: Source file format, any conversion steps performed.

---

## Step 2: Quality Control

**Skill**: Read `skillpacks/bioSkills/single-cell/preprocessing/SKILL.md`
**QC checklist**: Use `skill-bridge/qc-checklist.md` (always available). If the `bioinformatics` convention pack is also installed, consult its `qc-checklist.md` "Single-Cell QC" section for additional domain-specific checks.

**Actions**:
- Calculate per-cell metrics: n_genes, n_counts, pct_mito, pct_ribo
- **BEFORE applying any cutoffs**: Plot full distributions of all QC metrics (violin plots, histograms)
- Determine thresholds based on distribution shapes, not defaults
- Apply filters and log exact thresholds with rationale

**Persona checkpoint (Raj)**: "NEVER average over cell-to-cell variability without first examining the full distribution." Ensure distributions are examined before any filtering.

**Sensitivity sweep** (from robust-analysis conventions):
- Sweep `min_genes` across at least 3 values (e.g., 200, 300, 500)
- Sweep `max_pct_mito` across at least 3 values (e.g., 5%, 10%, 20%)
- For each combination: record number of cells retained and number of clusters found downstream
- Generate supplementary figure: cells_retained vs. threshold

**Validation**:
- [ ] QC distributions plotted BEFORE filtering
- [ ] Thresholds documented with rationale in `.living/decisions.md`
- [ ] Sensitivity sweep completed (min 3 values per parameter)
- [ ] Cell loss percentage calculated and documented

**Decisions to log**: Each threshold chosen and why. Any outlier samples flagged.

---

## Step 3: Doublet Detection

**Skill**: Read `skillpacks/bioSkills/single-cell/doublet-detection/SKILL.md`

**Actions**:
- Run doublet detection (Scrublet, DoubletFinder, or scDblFinder)
- Document expected doublet rate based on loading density
- Flag predicted doublets; do NOT silently remove them yet

**Validation**:
- [ ] Doublet rate is plausible (typically 1-8% depending on loading)
- [ ] Predicted doublets examined: are they enriched for co-expression of markers from distinct cell types?
- [ ] Decision on whether to remove or flag doublets is logged

**Decision to log**: Doublet detection method, expected vs. observed rate, removal decision.

---

## Step 4: Normalization

**Skill**: Read `skillpacks/bioSkills/single-cell/preprocessing/SKILL.md` (normalization section)

**Actions**:
- Normalize (log-normalize or scran or SCTransform)
- Identify highly variable genes (HVGs)
- Document method and parameters

**Persona checkpoint (Trapnell)**: Consider whether the normalization method preserves the biological signal you care about. If studying rare populations, more permissive HVG selection may be needed.

**Validation**:
- [ ] Post-normalization distribution is approximately log-normal
- [ ] Number of HVGs documented (typical: 2000-5000)
- [ ] No genes with zero variance remain after HVG selection

**Decision to log**: Normalization method, HVG count, any manual gene additions/exclusions.

---

## Step 5: Batch Assessment & Integration

**Skill**: Read `skillpacks/bioSkills/single-cell/batch-integration/SKILL.md`

**Actions**:
- Run PCA and plot colored by batch/sample
- If batches separate: apply integration (Harmony, scVI, or bbknn)
- Generate before/after integration plots

**Even for single-batch data**: Run PCA colored by any available covariate (lane, replicate). Document that batch effects were checked.

**Validation**:
- [ ] PCA plot colored by batch generated
- [ ] If integration applied: before/after comparison shows mixing
- [ ] If no integration needed: documented why with evidence

**Decision to log**: Whether integration was needed, method chosen if so, evidence for decision.

---

## Step 6: Dimensionality Reduction

**Skill**: Read `skillpacks/bioSkills/single-cell/preprocessing/SKILL.md` (dimensionality reduction section)

**Actions**:
- PCA: determine number of PCs (elbow plot)
- UMAP for visualization (document parameters: n_neighbors, min_dist)
- Do NOT use t-SNE for publication figures unless specifically justified

**Validation**:
- [ ] Elbow plot generated, number of PCs chosen documented
- [ ] UMAP parameters documented
- [ ] UMAP colored by QC metrics (n_genes, pct_mito) to check for technical artifacts

---

## Step 7: Clustering

**Skill**: Read `skillpacks/bioSkills/single-cell/clustering/SKILL.md`

**Actions**:
- Leiden clustering (preferred over Louvain)
- **Sensitivity sweep on resolution**: Run at minimum 5 values (0.2, 0.4, 0.6, 0.8, 1.0, 1.5)
- For each resolution: plot UMAP, count clusters, note whether known cell types separate

**Persona checkpoint (Trapnell)**: "When choosing between profiling few cells deeply versus many cells shallowly, always choose scale." Consider whether the resolution captures the biology at the right granularity.

**Persona checkpoint (Raj)**: Are there rare populations being merged at lower resolutions that might be biologically important?

**Sensitivity output**: Generate multi-panel figure showing UMAP at each resolution.

**Validation**:
- [ ] Resolution sweep completed (minimum 5 values)
- [ ] Final resolution chosen with documented rationale
- [ ] Cluster sizes documented (flag any cluster < 50 cells for cautious interpretation)
- [ ] Supplementary figure: resolution vs. cluster count

**Decision to log**: Final resolution, rationale, any clusters that were manually merged or split.

---

## Step 8: Marker Gene Identification

**Skill**: Read `skillpacks/bioSkills/single-cell/markers-annotation/SKILL.md`

**Actions**:
- Wilcoxon rank-sum test for marker genes per cluster
- Report both logFC and pct_expressed
- Filter markers by adjusted p-value AND fold change AND expression percentage

**Persona checkpoint (Gelman)**: "NEVER accept p-values as sufficient evidence." Report effect sizes (logFC) and the percentage of cells expressing the marker alongside p-values. Consider whether the effect size is biologically meaningful, not just statistically significant.

**Validation**:
- [ ] Markers reported with logFC, pct_in, pct_out, and padj
- [ ] Top markers checked: do they make biological sense for expected cell types?
- [ ] Any cluster with no strong markers flagged for investigation

---

## Step 9: Cell Type Annotation

**Skill**: Read `skillpacks/bioSkills/single-cell/cell-annotation/SKILL.md`
**Complementary**: Read `skillpacks/scientific-agent-skills/scientific-skills/cellxgene-census/SKILL.md` for reference data

**Actions**:
- Automated annotation via reference-based method (SingleR, Azimuth, or CELLxGENE Census)
- Manual verification: check that automated labels are consistent with known marker genes
- For any ambiguous clusters: document the uncertainty

**Persona checkpoint (Raj)**: "Treat heterogeneity as functional signal, not noise." If a cluster contains cells with mixed annotations, investigate whether this represents a transition state or a biological mixture rather than a technical artifact.

**Validation**:
- [ ] Automated annotation cross-referenced with marker genes
- [ ] Known cell types for this tissue accounted for (e.g., PBMCs: T cells, B cells, monocytes, NK, DC)
- [ ] Any unexpected or novel annotations flagged and investigated
- [ ] Annotation confidence scores documented

**Decision to log**: Annotation method, reference dataset used, any manual overrides and why.

---

## Step 10: Differential Expression (Optional)

**Skill**: Read `skillpacks/bioSkills/differential-expression/deseq2-basics/SKILL.md` (for pseudobulk)
**Or**: Wilcoxon/MAST from the markers skill (for single-cell level)

**Actions**:
- For between-condition DE: use pseudobulk approach (aggregate to sample level) with DESeq2
- For within-cluster DE: use Wilcoxon or MAST
- Visualize with volcano plots

**Persona checkpoint (Ioannidis)** (if assigned as standing reviewer): "NEVER accept p between 0.01-0.05 as strong evidence for a novel finding."

**Validation**:
- [ ] DE method appropriate for the comparison type (pseudobulk for conditions, cell-level for markers)
- [ ] Multiple testing correction applied
- [ ] Effect sizes reported alongside p-values

---

## Step 11: Post-Analysis Persona Review

**Load full persona profiles** for the panel (prompt_fragment + decision_rules + anti_patterns + key_vocabulary).

**Adversarial review protocol** (adapted from Autonomous-Science PI Roundtable):

For each major finding:
1. **Raj perspective**: Is cell-to-cell variability being properly respected? Are rare states being lost? Is the heterogeneity informative?
2. **Trapnell perspective**: Would this analysis benefit from more conditions or higher throughput? Are trajectory assumptions valid?
3. **Gelman perspective**: Are the statistical conclusions properly hedged? Is uncertainty quantified? Are there garden-of-forking-paths risks in the parameter choices?

**Mandatory adversarial tests** per finding:
- **Refutation test**: What specific observation would disprove this conclusion?
- **Confounding alternative**: What else could explain this result?
- **Evidence gap**: What single additional piece of data would most strengthen or weaken this claim?

**Record in `.living/`**:
- Persona agreements → `.living/findings/` as validated findings
- Persona disagreements → `.living/decisions.md` with both viewpoints
- Sub-hypotheses → `todo/` as future work items
- Surprises → `.living/learnings.md`

---

## Workflow Summary

| Step | Skill Source | Persona Checkpoint | Key Validation |
|------|-------------|-------------------|----------------|
| 1. Data loading | bioSkills: data-io | — | Cell/gene count match |
| 2. QC | bioSkills: preprocessing | Raj: examine distributions | Threshold sensitivity sweep |
| 3. Doublets | bioSkills: doublet-detection | — | Rate plausibility |
| 4. Normalization | bioSkills: preprocessing | Trapnell: signal preservation | Distribution shape |
| 5. Batch | bioSkills: batch-integration | — | Before/after plots |
| 6. Dim. reduction | bioSkills: preprocessing | — | Elbow plot, artifact check |
| 7. Clustering | bioSkills: clustering | Trapnell + Raj: granularity | Resolution sweep (5+ values) |
| 8. Markers | bioSkills: markers-annotation | Gelman: effect sizes | Bio plausibility |
| 9. Annotation | bioSkills: cell-annotation + sci-skills: cellxgene | Raj: heterogeneity | Reference cross-check |
| 10. DE | bioSkills: DE / deseq2 | Ioannidis (optional) | Pseudobulk for conditions |
| 11. Review | — | Full panel | Adversarial tests |
