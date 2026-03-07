---
description: >
  Start or continue a data analysis in a mycelium-enabled repository.
  Routes to installed convention packs (robust-analysis, domain conventions)
  for methodology guidance. Use when user says "start analysis", "run analysis",
  "continue analysis", "new analysis", or "analyze".
---

# Mycelium — Analyze

Start a new analysis or continue an existing one, routing to the appropriate convention packs installed in this repository.

## Steps

1. **Read manifests** to understand the project:
   - `analysis/ANALYSIS_MANIFEST.md` — existing analyses
   - `data/DATA_MANIFEST.md` — available data
   - `algorithms/ALGORITHM_MANIFEST.md` — available algorithms

2. **Continuing existing**: Navigate to `analysis/[name]/` and read its documentation file (UPPER_SNAKE_CASE of folder name, e.g., `SNP_ANALYSIS.md`).

3. **New analysis**: Create `analysis/[name]/` with documentation file (UPPER_SNAKE_CASE.md), `scripts/`, `outputs/`, `reports/`. If building on a parent analysis, record the lineage in the manifest entry.

4. **Route to installed conventions**:
   - Read `.living/conventions/ACTIVE_CONVENTIONS.yaml` to see what's installed.
   - **Always consult** the core references: `skills/core/references/analysis-conventions.md` (structure) and `skills/core/references/statistical-conventions.md` (methodology). These are the baseline.
   - **If `robust-analysis` is installed** (check `.living/conventions/robust-analysis/`): Follow its conventions as the primary execution guide. Read `.living/conventions/robust-analysis/analysis-conventions.md` as the entry point — it links to detail files for strict execution, validation checks, sensitivity sweeps, null hypothesis testing, and adversarial probing. These conventions are non-negotiable when installed.
   - **If a domain convention is installed** (e.g., `bioinformatics`, `image-analysis`): Consult its `analysis-conventions.md` for domain-specific methodology. Domain conventions layer on top of robust-analysis.
   - **If multiple conventions apply**, follow the cascade: repo-local (`.living/conventions.md`) > domain > core.

5. **Execution standards**:
   - Use marimo for exploratory work, plain Python scripts for reproducible pipelines.
   - Every analysis must have a `run.sh` or `run.py` that reproduces final outputs.

6. **Run the post-action hook protocol** after significant steps (see `/mycelium:core` for the full protocol):
   - Update `analysis/ANALYSIS_MANIFEST.md`
   - Update the analysis documentation file
   - Log decisions to `.living/decisions.md`
   - Log learnings to `.living/learnings.md`
   - Run `skills/core/scripts/validate_structure.py`
