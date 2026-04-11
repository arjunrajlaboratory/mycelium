# Skill Routing Decision Tree

Detailed routing logic for selecting the right skill from the right repo. The hub file (analysis-conventions.md) has the summary table; this file provides the full decision trees.

---

## General Rule

**bioSkills** for bioinformatics depth (version-pinned patterns, R and Python, 439 skills across 63 categories). **scientific-agent-skills** for breadth (database access, ML frameworks, scientific communication, lab automation, 135 skills across 18 domains).

When both cover a tool, prefer **bioSkills** for bioinformatics tasks (deeper coverage, version-resilient patterns) and **scientific-agent-skills** for cross-domain or infrastructure tasks.

---

## Single-Cell RNA-seq

```
Is the task about...
├── Loading data (10x, h5ad, Seurat objects)?
│   → bioSkills: skillpacks/bioSkills/single-cell/data-io/SKILL.md
│   → Fallback: sci-skills: skillpacks/scientific-agent-skills/scientific-skills/anndata/SKILL.md
│
├── QC and preprocessing (filtering, normalization)?
│   → bioSkills: skillpacks/bioSkills/single-cell/preprocessing/SKILL.md
│
├── Doublet detection?
│   → bioSkills: skillpacks/bioSkills/single-cell/doublet-detection/SKILL.md
│
├── Batch integration (Harmony, scVI, ComBat)?
│   → bioSkills: skillpacks/bioSkills/single-cell/batch-integration/SKILL.md
│
├── Clustering (Leiden, resolution selection)?
│   → bioSkills: skillpacks/bioSkills/single-cell/clustering/SKILL.md
│
├── Marker gene identification?
│   → bioSkills: skillpacks/bioSkills/single-cell/markers-annotation/SKILL.md
│
├── Cell type annotation (reference-based)?
│   → bioSkills: skillpacks/bioSkills/single-cell/cell-annotation/SKILL.md
│
├── Trajectory / pseudotime?
│   → bioSkills: skillpacks/bioSkills/single-cell/trajectory-inference/SKILL.md
│   → Complementary: sci-skills: skillpacks/scientific-agent-skills/scientific-skills/scvelo/SKILL.md
│
├── Perturbation analysis (Perturb-seq, CRISPR screens)?
│   → bioSkills: skillpacks/bioSkills/single-cell/perturb-seq/SKILL.md
│
├── Cell-cell communication?
│   → bioSkills: skillpacks/bioSkills/single-cell/cell-communication/SKILL.md
│
├── Multimodal (CITE-seq, multiome)?
│   → bioSkills: skillpacks/bioSkills/single-cell/multimodal-integration/SKILL.md
│
├── Reference data from CELLxGENE Census?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/cellxgene-census/SKILL.md
│
├── Deep learning models (scVI, scANVI)?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/scvi-tools/SKILL.md
│
└── Publication-quality figures?
    → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/scientific-visualization/SKILL.md
    → Complementary: bioSkills: skillpacks/bioSkills/data-visualization/
```

## Bulk RNA-seq & Differential Expression

```
Is the task about...
├── Count matrix generation (featureCounts, Salmon)?
│   → bioSkills: skillpacks/bioSkills/rna-quantification/SKILL.md (check subdirectories)
│
├── Expression matrix handling (normalization, ID mapping)?
│   → bioSkills: skillpacks/bioSkills/expression-matrix/SKILL.md (check subdirectories)
│
├── DESeq2 analysis (R)?
│   → bioSkills: skillpacks/bioSkills/differential-expression/deseq2-basics/SKILL.md
│
├── pyDESeq2 analysis (Python)?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/pydeseq2/SKILL.md
│
├── edgeR analysis?
│   → bioSkills: skillpacks/bioSkills/differential-expression/edger-basics/SKILL.md
│
├── DE visualization (volcano, MA plots)?
│   → bioSkills: skillpacks/bioSkills/differential-expression/de-visualization/SKILL.md
│
├── Gene set enrichment (GSEA)?
│   → bioSkills: skillpacks/bioSkills/pathway-analysis/gsea/SKILL.md
│
├── GO enrichment?
│   → bioSkills: skillpacks/bioSkills/pathway-analysis/go-enrichment/SKILL.md
│
└── KEGG / Reactome pathways?
    → bioSkills: skillpacks/bioSkills/pathway-analysis/kegg-pathways/SKILL.md
    → bioSkills: skillpacks/bioSkills/pathway-analysis/reactome-pathways/SKILL.md
```

## Variant Calling & Clinical Genomics

```
Is the task about...
├── BAM/SAM file handling?
│   → bioSkills: skillpacks/bioSkills/alignment-files/ (check subdirectories)
│   → Complementary: sci-skills: skillpacks/scientific-agent-skills/scientific-skills/pysam/SKILL.md
│
├── GATK variant calling?
│   → bioSkills: skillpacks/bioSkills/variant-calling/ (check subdirectories)
│
├── Variant annotation (VEP, ClinVar)?
│   → bioSkills: skillpacks/bioSkills/clinical-databases/ (check subdirectories)
│
├── Population genetics (GWAS, PCA)?
│   → bioSkills: skillpacks/bioSkills/population-genetics/ (check subdirectories)
│
└── Clinical reports / decision support?
    → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/clinical-decision-support/SKILL.md
    → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/clinical-reports/SKILL.md
```

## Cheminformatics & Drug Discovery

```
Is the task about...
├── Molecular manipulation (SMILES, fingerprints)?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/rdkit/SKILL.md
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/datamol/SKILL.md
│
├── Molecular docking?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/diffdock/SKILL.md
│
├── ADMET / medicinal chemistry?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/medchem/SKILL.md
│
├── Molecular ML?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/deepchem/SKILL.md
│
└── Database queries (ChEMBL, PubChem, DrugBank)?
    → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/database-lookup/SKILL.md
```

## Epigenomics

```
Is the task about...
├── ChIP-seq?
│   → bioSkills: skillpacks/bioSkills/chip-seq/ (check subdirectories)
│
├── ATAC-seq?
│   → bioSkills: skillpacks/bioSkills/atac-seq/ (check subdirectories)
│   → Complementary: sci-skills: skillpacks/scientific-agent-skills/scientific-skills/deeptools/SKILL.md
│
├── Methylation?
│   → bioSkills: skillpacks/bioSkills/methylation-analysis/ (check subdirectories)
│
├── Hi-C / 3D genome?
│   → bioSkills: skillpacks/bioSkills/hi-c-analysis/ (check subdirectories)
│
└── CLIP-seq (protein-RNA)?
    → bioSkills: skillpacks/bioSkills/clip-seq/ (check subdirectories)
```

## Database & Literature Access

```
Is the task about...
├── Multi-database lookup (78+ databases)?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/database-lookup/SKILL.md
│
├── Literature search / paper lookup?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/paper-lookup/SKILL.md
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/literature-review/SKILL.md
│
├── NCBI / Entrez queries?
│   → bioSkills: skillpacks/bioSkills/database-access/ (check subdirectories)
│
├── Bioservices (40+ bioinformatics APIs)?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/bioservices/SKILL.md
│
└── Cancer Dependency Map?
    → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/depmap/SKILL.md
```

## Scientific Communication

```
Is the task about...
├── Writing a paper / manuscript?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/scientific-writing/SKILL.md
│
├── Creating figures?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/scientific-visualization/SKILL.md
│
├── Making a poster (LaTeX)?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/latex-posters/SKILL.md
│
├── Making slides?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/scientific-slides/SKILL.md
│
├── Peer review?
│   → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/peer-review/SKILL.md
│
└── Grant writing?
    → sci-skills: skillpacks/scientific-agent-skills/scientific-skills/research-grants/SKILL.md
```
