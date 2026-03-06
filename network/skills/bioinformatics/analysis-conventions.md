# Bioinformatics Analysis Conventions

Domain-specific conventions for bioinformatics analyses. These extend the core mycelium analysis conventions.

## RNA-seq Analysis

### Standard Workflow

1. **Quality control** — Run FastQC/MultiQC on raw reads before any processing
2. **Trimming** — Trim adapters and low-quality bases (Trim Galore, fastp, or cutadapt)
3. **Alignment** — Align to reference genome (STAR for splice-aware, HISAT2 as alternative) or pseudo-align (Salmon, kallisto)
4. **Quantification** — Generate count matrix (featureCounts for alignment-based, tximport for pseudo-alignment)
5. **Differential expression** — DESeq2 (preferred) or edgeR
6. **Pathway analysis** — Gene set enrichment analysis (GSEA, fgsea) or over-representation analysis (clusterProfiler)

### Directory Structure

```
analysis/rnaseq-experiment-name/
├── RNASEQ_EXPERIMENT_NAME.md
├── run.sh
├── scripts/
│   ├── 01_qc.sh                    # FastQC + MultiQC
│   ├── 02_trim.sh                  # Adapter trimming
│   ├── 03_align_or_quant.sh        # STAR/Salmon
│   ├── 04_count_matrix.py          # Generate count matrix
│   ├── 05_differential_expression.R # DESeq2
│   └── 06_enrichment.R             # Pathway analysis
├── outputs/
│   ├── qc/                         # MultiQC report
│   ├── counts/                     # Count matrices
│   ├── de_results/                 # DE tables
│   └── figures/                    # Plots
└── reports/
```

### Reference Genome Convention

- Document the genome build (e.g., GRCh38, mm39) and annotation version (e.g., GENCODE v44) in the analysis README
- Store genome paths in `ENVIRONMENTS_INSTALLATIONS.md`, not in scripts
- Use consistent genome builds across all analyses in a project

## Single-Cell Analysis

### Standard Workflow

1. **Raw data processing** — Cell Ranger, STARsolo, or alevin-fry
2. **Quality control** — Filter cells by gene count, UMI count, and mitochondrial percentage
3. **Normalization** — scran or Seurat's SCTransform
4. **Dimensionality reduction** — PCA then UMAP (not t-SNE for publication figures unless justified)
5. **Clustering** — Leiden (preferred over Louvain) with resolution parameter documented
6. **Marker genes** — Wilcoxon rank-sum or MAST for differential expression between clusters
7. **Cell type annotation** — Reference-based (SingleR, Azimuth) or marker-based, always with manual verification

### QC Thresholds

Document all QC thresholds in the analysis README. Common defaults (adjust per dataset):

| Metric | Typical Threshold | Notes |
|--------|-------------------|-------|
| Min genes/cell | 200–500 | Lower for certain cell types |
| Max genes/cell | 2500–8000 | Dataset-dependent, check for doublets |
| Max % mito | 5–20% | Tissue-dependent |
| Min cells/gene | 3–10 | Filter rarely detected genes |

### Doublet Detection

Always run doublet detection (DoubletFinder, Scrublet, or scDblFinder) and document the doublet rate.

## General Conventions

### Gene Identifiers

- Use Ensembl gene IDs as the primary identifier (stable across annotations)
- Include gene symbols as a secondary column for readability
- Document the mapping version used

### Batch Effects

- Check for batch effects before merging datasets (PCA colored by batch)
- If present, use Harmony, scVI, or ComBat for correction
- Always show before/after batch correction in QC figures
- Document the correction method and parameters

### Organism and Assembly

Always specify at the top of the analysis README:
- Organism (e.g., Homo sapiens, Mus musculus)
- Genome assembly (e.g., GRCh38)
- Gene annotation (e.g., GENCODE v44, Ensembl 110)
