---
description: >
  ALWAYS invoke this skill when a user wants to perform computational analysis
  on data in this repository. This skill loads project-specific analysis
  conventions, dataset registries, and methodology requirements (validation
  checks, sensitivity sweeps, null hypothesis testing) that are not available
  from general knowledge — you cannot do the analysis correctly without
  consulting it first. Trigger for: running any named analysis technique
  (clustering, PCA, UMAP, differential expression, survival analysis,
  dose-response, model fitting, time-series, batch correction, statistical
  tests), exploring or investigating patterns in data, continuing or extending
  a previous analysis, debugging or troubleshooting analytical results (wrong
  clusters, unexpected patterns, parameter tuning). The user's intent must be
  to computationally process or model data — not just read it, not just look
  at a file. Do NOT trigger for: reading or previewing data files without
  analysis, writing reports or documents, brainstorming ideas, setting up or
  initializing repositories, installing conventions, ingesting or importing
  data files, fixing code bugs unrelated to analytical results, or updating
  documentation.
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
   - **Register reportable values with `register_value`** at the site that computes them. Any number or short phrase the analysis would expect a future report to quote (sample counts, thresholds, contrast phrases, headline metrics) goes through `skills/core/scripts/register_value.py`. The call writes `analysis/<name>/outputs/numbers.json`; the report's Phase-1 merge picks up the fragment automatically, and `scitexlintr` then catches drift between the manifest value and any quoted snapshot in the `.tex` source. Full guide: `skills/core/references/report-values-guide.md`. Skip this only for values the report will never cite.

6. **Lint with `scilintr` after writing or modifying analysis code** (non-negotiable). This is a fast static analyzer that flags silent scientific commitments — things that look like ordinary software (broad `except`, positional indexing, unchecked joins, unannotated filters, label leakage, magic thresholds, unseeded RNGs, etc.). The core principle is: *scientifically meaningful choices must not be anonymous.* Read `skills/core/references/scilintr-guide.md` before invoking it the first time in a session — it explains the philosophy, the rule catalogue, and (critically) the `ANALYSIS_OK[category]: explanation` waiver mechanism. Workflow:
   - **Install if missing.** Python (on PyPI): `pip install scilintr`. R (not on CRAN): `remotes::install_github("arjunrajlaboratory/scilintr", subdir = "r/scilintr")`.
   - **After every code write or edit**, run `scilintr <file_or_dir>` on the touched files. The CLI is fast (sub-second on most files), so there is no reason to defer.
   - **Drive findings to zero**, either by fixing the pattern (preferred — most findings have a cleaner code path) or by adding a structured waiver (`# ANALYSIS_OK[category]: explanation; where the check/assertion lives`). A waiver must answer *what / why scientifically valid / where it is recorded*. Vacuous waivers (`# ANALYSIS_OK: fine`) are not acceptable.
   - **Before declaring the analysis complete**, run `scilintr` on the full analysis directory (e.g., `scilintr analysis/<name>/`) to catch cross-file rules (duplicate-parameter-source, shadow-overwrite, definition drift).
   - This is complementary to `/mycelium:review`, not a substitute. Lint catches structural patterns cheaply; review catches semantic and statistical issues.

7. **Run the post-action hook protocol** after significant steps (see `/mycelium:core` for the full protocol):
   - Update `analysis/ANALYSIS_MANIFEST.md`
   - Update the analysis documentation file
   - Log decisions to `.living/decisions.md`
   - Log learnings to `.living/learnings.md`
   - Run `skills/core/scripts/validate_structure.py`
