---
description: >
  Ingest a new dataset into a mycelium-enabled repository. Handles placement,
  metadata generation, manifest updates, and decision logging. Use when user says
  "ingest dataset", "add data", "import data", "new dataset", "ingest data",
  or "add a dataset".
---

# Mycelium — Ingest

Pull a new dataset into the analytical framework, ensuring it is properly documented, validated, and registered.

## Steps

1. **Read manifests** to understand existing data:
   - `data/DATA_MANIFEST.md` — existing datasets
   - `data/raw/` — current raw data directories

2. **Consult ingestion conventions**: Read `skills/core/references/data-ingest-conventions.md` for the full workflow, metadata requirements, large file handling, and manifest entry checklist.

3. **Check for domain-specific validation**:
   - Read `.living/conventions/ACTIVE_CONVENTIONS.yaml` to see what's installed.
   - If a domain convention is installed (e.g., `bioinformatics`, `image-analysis`), consult its conventions for domain-specific validation requirements (e.g., QC metrics for sequencing data, format validation for images).
   - Domain conventions may add required metadata fields or validation steps beyond the standard workflow.

4. **Determine data type, source, and format**:
   - Ask the user if not already clear: What is the data? Where did it come from? What format is it in?
   - For large files (too large for git), plan for `.gitignore` + download documentation.

5. **Place raw data** in `data/raw/[dataset-name]/`:
   - Each dataset gets its own subdirectory.
   - Include a documentation file named in UPPER_SNAKE_CASE of the folder name (e.g., `PATIENT_COHORT_2024.md`).
   - Raw data is **immutable** — never modify files after initial placement.

6. **Generate metadata** in `data/metadata/[dataset-name]/`:
   - `schema.yaml` — column descriptions, types, units
   - `provenance.md` — full source details, acquisition method, contact
   - `summary_stats.md` — row counts, column distributions, missing data summary
   - Required fields: source, date acquired, schema/column descriptions, known issues, access restrictions.

7. **Update `data/DATA_MANIFEST.md`** with a new entry using `skills/core/templates/dataset-manifest-entry.yaml`.

8. **Log decisions**: If any choices were made during ingestion (excluding records, choosing formats, handling encoding issues), append to `.living/decisions.md`.

9. **Run the post-action hook protocol** after ingestion is complete (see `/mycelium:core` for the full protocol):
   - Update `data/DATA_MANIFEST.md` (done in step 7)
   - Update the dataset documentation file
   - Log decisions to `.living/decisions.md`
   - Log learnings to `.living/learnings.md`
   - Run `skills/core/scripts/validate_structure.py`
