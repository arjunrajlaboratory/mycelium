# [RNA-seq Analysis Name]

## Purpose

[What biological question does this RNA-seq analysis address?]

## Status

**Status**: draft

## Organism and Genome

- **Organism**: [e.g., Homo sapiens]
- **Genome assembly**: [e.g., GRCh38]
- **Gene annotation**: [e.g., GENCODE v44]

## Experimental Design

- **Conditions**: [e.g., treated vs control]
- **Replicates**: [N per condition]
- **Library prep**: [e.g., poly-A selection, stranded]
- **Sequencing**: [e.g., paired-end 150bp, NovaSeq]
- **Batch information**: [any known batches]

## Datasets

- `[dataset-name]` — [reference to data/MANIFEST.md entry]

## Quality Control

### Raw Read QC

| Sample | Total Reads | % Q30 | % Adapter | % GC |
|--------|------------|-------|-----------|------|
| | | | | |

### Alignment QC

| Sample | % Mapped | % Unique | % Exonic | % rRNA | % Dup |
|--------|----------|----------|----------|--------|-------|
| | | | | | |

### Filtering

- Genes removed (low counts): [N genes with < 10 total counts]
- Samples removed: [none / list with reason]

## Differential Expression

- **Method**: DESeq2 (v[version])
- **Design formula**: `~ [covariates + condition]`
- **Contrasts**: [treated vs control]
- **Shrinkage**: apeglm
- **Thresholds**: padj < 0.05, |log2FC| > 1

### Results Summary

| Comparison | Up | Down | Total DE |
|-----------|-----|------|----------|
| | | | |

## Enrichment Analysis

- **Method**: [fgsea / clusterProfiler GSEA / ORA]
- **Gene sets**: [MSigDB Hallmark / KEGG / Reactome]
- **Key enriched pathways**: [list top findings]

## Key Findings

- [Finding 1]
- [Finding 2]

## Open Questions

- [Question 1]

## Reproducibility

```bash
cd analysis/[analysis-name]
bash run.sh
```

## Outputs

| File | Description |
|------|-------------|
| `outputs/qc/multiqc_report.html` | MultiQC report |
| `outputs/counts/raw_counts.csv` | Raw count matrix |
| `outputs/de_results/[comparison].csv` | DE results table |
| `outputs/figures/volcano_[comparison].pdf` | Volcano plot |
| `outputs/figures/pca_plot.pdf` | PCA of samples |
