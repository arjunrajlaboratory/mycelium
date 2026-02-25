# Bioinformatics Statistical Conventions

Domain-specific statistical methods for bioinformatics. These extend the core statistical conventions.

## Differential Expression

### DESeq2 (Preferred)

- Use DESeq2 for bulk RNA-seq differential expression. It handles count data directly (no TPM/FPKM normalization first).
- **Pre-filtering**: Remove genes with fewer than 10 total counts across all samples before running DESeq2.
- **Design formula**: Include all relevant covariates. Document the formula in the README.
- **Contrasts**: Define contrasts explicitly; don't rely on default alphabetical ordering.
- **Shrinkage**: Use `lfcShrink()` with `type="apeglm"` for log fold change estimates in downstream analysis and visualization.
- **Significance**: Report adjusted p-values (Benjamini-Hochberg). Default threshold: padj < 0.05.
- **Effect size**: Report shrunken log2 fold changes. Consider an additional fold change threshold (e.g., |log2FC| > 1) for biological significance.

```r
# Standard DESeq2 workflow
dds <- DESeqDataSetFromMatrix(countData, colData, design = ~ batch + condition)
dds <- dds[rowSums(counts(dds)) >= 10, ]
dds <- DESeq(dds)
res <- lfcShrink(dds, coef = "condition_treated_vs_control", type = "apeglm")
```

### edgeR

Use when DESeq2 is not appropriate (e.g., very small sample sizes with known dispersion):
- Use the quasi-likelihood framework (`glmQLFit` + `glmQLFTest`)
- Document why edgeR was chosen over DESeq2

### For Single-Cell

- Use Wilcoxon rank-sum test for marker gene detection (fast, reasonable)
- Use MAST for formal DE between conditions (handles zero inflation)
- Report both logFC and percentage of cells expressing the gene

## Multiple Testing

- **Default**: Benjamini-Hochberg (FDR) for genome-wide analyses
- **Report**: Number of genes tested, number significant at FDR < 0.05, number significant at FDR < 0.01
- **Independent filtering**: DESeq2's independent filtering is preferred over manual filtering after testing
- For candidate gene studies (< 20 genes): Bonferroni is acceptable

## Gene Set Enrichment

### GSEA (Ranked Method, Preferred)

- Use fgsea or clusterProfiler's GSEA on the full ranked gene list (by signed -log10(pvalue) or stat from DESeq2)
- Do not threshold genes before running GSEA — the method works on the full ranking
- Report: NES (normalized enrichment score), p-value, adjusted p-value, leading edge genes
- Use MSigDB gene sets (Hallmark, C2, C5) or KEGG/Reactome pathways
- Document the gene set database and version

### ORA (Over-Representation Analysis)

- Use only when you have a well-defined gene set (e.g., FDR < 0.05 and |log2FC| > 1)
- Use the full expressed gene set as the background (not the whole genome)
- Report: fold enrichment, p-value, adjusted p-value, genes in overlap

## Sample Size Considerations

- **Bulk RNA-seq**: Minimum 3 biological replicates per condition. 6+ recommended for detecting moderate effect sizes.
- **Single-cell**: Document number of cells per condition/cluster. Be cautious about DE with fewer than 50 cells per group.
- Report effective sample sizes (biological replicates, not technical replicates)

## Visualization Standards

| Analysis Type | Recommended Plot |
|---------------|-----------------|
| DE results overview | Volcano plot (log2FC vs -log10(padj)) |
| Global expression | MA plot (log2FC vs mean expression) |
| Sample relationships | PCA plot colored by condition/batch |
| Gene set enrichment | Dot plot (top enriched terms) or ridge plot |
| Heatmaps | Use z-scored expression, cluster rows and columns, annotate with metadata |
| Single-cell clusters | UMAP with Leiden clusters, feature plots for markers |

### Volcano Plot Standards

- X-axis: log2 fold change (shrunken if available)
- Y-axis: -log10(adjusted p-value)
- Highlight significant genes (padj < 0.05 and |log2FC| > 1)
- Label top N genes by significance or fold change
- Include dashed lines at significance thresholds
