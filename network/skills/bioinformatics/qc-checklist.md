# Bioinformatics QC Checklist

Quality control checks for bioinformatics data. Run these before proceeding with analysis.

## RNA-seq QC

### Raw Read Quality

- [ ] **FastQC** run on all samples
- [ ] **Per-base quality** scores > Q30 across read length
- [ ] **Adapter content** < 5% (trim if higher)
- [ ] **Overrepresented sequences** investigated (rRNA contamination?)
- [ ] **GC content** distribution unimodal and matching expected for organism
- [ ] **MultiQC** report generated aggregating all samples

### Alignment Quality

- [ ] **Mapping rate** > 70% (> 80% preferred for well-annotated genomes)
- [ ] **Uniquely mapped** > 60%
- [ ] **rRNA mapping rate** < 10% (< 5% preferred; high rates suggest incomplete rRNA depletion)
- [ ] **Exonic rate** consistent across samples (> 60% for poly-A selected)
- [ ] **Strand specificity** matches library prep (check with infer_experiment.py)
- [ ] **Insert size** distribution consistent with library prep protocol

### Library Complexity

- [ ] **Duplication rate** documented (PCR duplicates)
- [ ] **Library complexity** sufficient (estimated unique fragments)
- [ ] **Saturation analysis** — are we detecting new genes with more reads?

### Count Matrix Quality

- [ ] **Total counts per sample** within expected range and consistent across samples
- [ ] **Detected genes per sample** consistent (flag outliers)
- [ ] **PCA/MDS plot** shows expected grouping (batch vs condition)
- [ ] **Sample correlation heatmap** confirms expected relationships
- [ ] **Outlier samples** identified and decision documented

## Single-Cell QC

### Per-Cell Metrics

- [ ] **Genes per cell** distribution — document threshold
- [ ] **UMIs per cell** distribution — document threshold
- [ ] **Mitochondrial %** distribution — document threshold
- [ ] **Ribosomal %** checked (high may indicate specific cell types)
- [ ] **Doublet detection** run — document doublet rate and method
- [ ] **Ambient RNA** estimation (SoupX or DecontX if needed)

### Per-Gene Metrics

- [ ] **Cells per gene** filter applied — document threshold
- [ ] **Mitochondrial genes** identified correctly for organism
- [ ] **Gene type distribution** checked (protein-coding vs lncRNA etc.)

### Batch and Integration

- [ ] **Batch effects** assessed by PCA/UMAP colored by batch
- [ ] **Integration method** applied if batches don't overlap (document method)
- [ ] **Integration quality** — cell types mixed across batches post-integration

## General Data QC

### Before Analysis

- [ ] **Sample metadata** complete (no missing conditions, covariates)
- [ ] **Sample IDs** match between metadata and count data
- [ ] **Expected vs actual** sample count matches
- [ ] **File integrity** — checksums verified for transferred files

### During Analysis

- [ ] **Normalization** method documented
- [ ] **Filtering decisions** logged in `.living/decisions.md`
- [ ] **Known issues** from data manifest reviewed before analysis

## Recording QC Results

Document all QC metrics in the analysis README under a "Quality Control" section. Include:

1. Summary table of key metrics per sample
2. Pass/fail status for each check
3. Any samples excluded and why (log in `.living/decisions.md`)
4. Link to the MultiQC report or equivalent in `outputs/qc/`
