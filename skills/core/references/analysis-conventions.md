# Analysis Conventions

How analyses are structured and managed in a mycelium-enabled repository.

## Structure

Every analysis gets its own subfolder under `analysis/`:

```
analysis/
├── ANALYSIS_MANIFEST.md
└── my-analysis-name/
    ├── MY_ANALYSIS_NAME.md   # UPPER_SNAKE_CASE of folder name
    ├── scripts/              # Marimo notebooks and/or Python scripts
    ├── outputs/              # Figures, tables, intermediate results
    └── reports/              # LaTeX writeups
```

## Analysis Documentation Requirements

Every analysis must have a documentation file named in UPPER_SNAKE_CASE of the folder name (e.g., `analysis/snp-analysis/` → `SNP_ANALYSIS.md`). Use the analysis-readme template and rename accordingly. The file must contain:

- **Purpose**: What question does this analysis answer?
- **Status**: One of `draft`, `active`, `complete`, `archived`
- **Datasets used**: References to `data/DATA_MANIFEST.md` entries
- **Algorithms used**: References to `algorithms/ALGORITHM_MANIFEST.md` entries
- **Key findings**: Bullet-point summary of results (updated as work progresses)
- **Open questions**: What remains unresolved or needs follow-up
- **Parent analysis**: If this builds on prior work, reference it by name

## Reproducibility

Every analysis must have a reproducible entry point:

- **`run.sh`** or **`run.py`** at the analysis root that reproduces all final outputs from raw data
- This script should be runnable by someone with a fresh clone and the correct environment
- Use relative paths from the analysis directory
- Pin random seeds for any stochastic processes

```bash
# Example run.sh
#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

python scripts/01_preprocess.py
python scripts/02_analyze.py
python scripts/03_generate_figures.py
echo "Analysis complete. Outputs in outputs/"
```

## Exploration vs. Pipelines

- **Marimo notebooks**: Use for interactive exploration, prototyping, and visualization during development. Great for iterating on parameters and understanding data.
- **Python scripts**: Use for the reproducible pipeline. Once you know what works, encode it in scripts.
- **Both are fine**, but reproducibility requires a script path. A marimo notebook alone is not sufficient — it must be accompanied by a `run.sh` or `run.py` that doesn't require interactive execution.

## Output Conventions

Place all outputs in the `outputs/` subdirectory:

- **Figures**: Use descriptive names, e.g., `volcano_plot_treatment_vs_control.pdf`
- **Tables**: CSV or TSV format with headers, e.g., `significant_genes_fdr05.csv`
- **Intermediate results**: Prefix with step number, e.g., `01_filtered_counts.parquet`
- **Reportable values**: `outputs/numbers.json` is written automatically by `register_value` (see `report-values-guide.md`). Do not edit by hand.

Prefer vector formats (PDF, SVG) for figures. Use PNG only when vector is impractical (e.g., heatmaps with many elements).

## Reportable Values

Any number or short phrase a future report would quote — sample counts, applied thresholds, contrast phrases, headline metrics — should be captured at the site that computes it using the `register_value` helper at `skills/core/scripts/register_value.py`. The helper writes `outputs/numbers.json`; the `report-generator` convention's Phase 1 merges that fragment into the report manifest, and `scitexlintr` catches drift between the manifest and the `.tex` source.

Full guide: `report-values-guide.md`.

Short rule: if a number would force you to edit the report when the data changes, `register_value` it.

## Analysis Lifecycle

```
draft → active → complete → archived
```

- **draft**: Analysis is planned but work hasn't started or is very early
- **active**: Analysis is in progress with ongoing work
- **complete**: Analysis has produced final results and a report
- **archived**: Analysis is no longer actively maintained but preserved for reference

Update the status in both the analysis documentation file and the `analysis/ANALYSIS_MANIFEST.md` entry.

## Building on Parent Analyses

When an analysis extends or refines a previous one:

1. Reference the parent analysis by name in the documentation file and manifest entry
2. Do not copy data or code from the parent — reference it by path
3. Document what's different and why in the documentation file
4. The manifest entry should include `parent_analysis: parent-name`

This creates an analysis lineage that's easy to trace.

## Naming Convention

Use lowercase with hyphens: `differential-expression-v2`, `pathway-enrichment-inflammatory`, `dose-response-modeling`.

Include a version suffix (`-v2`, `-v3`) when iterating on the same question, rather than overwriting. The previous version stays as a reference.
