---
name: ingest
description: >
  Add or import files and datasets into a Mycelium project with metadata,
  provenance, validation, and DATA_MANIFEST updates. Use for collaborator or
  facility outputs and formats such as CSV, FASTQ, XLSX, TIFF, JSON, FCS, TSV,
  or images. Do not use for analyzing existing data, listing datasets, deleting
  data, downloading URLs, or editing code.
---

# Mycelium — Ingest

Resolve bundled `skills/` and `network/` paths relative to the Mycelium plugin
root—the ancestor containing `.codex-plugin/` and `.claude-plugin/`. Resolve
data and `.living/` paths relative to the user's repository.

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
   - Use the templates at `skills/core/templates/schema.yaml`, `skills/core/templates/provenance.md`, and `skills/core/templates/summary_stats.md` as starting points.
   - Required fields: source, date acquired, schema/column descriptions, known issues, access restrictions.
   - **When the data has samples, write a sample ledger.** Write `samples.tsv` with one row per sample: its id, its group, every replication level from coarse to fine (for example line, derivation, preparation, dish, library), and the crossed batch variables (preparation date, sequencing run). Write `samples.ledger.json` to name those columns, starting from `skills/core/templates/samples.ledger.json` and `samples.tsv`. Point `schema.yaml`'s `sample_ledger:` at the spec. Then run:
     `python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/data_contract_check.py" --ledger data/metadata/[dataset-name]/samples.ledger.json`
     The run checks unique sample ids, that each level nests in the one above it, at least two replication units per group, each batch variable against the group, and any declared pairing, including each batch variable against the within-unit contrast. Fix a block in the ledger or the data, or record it as a known issue. Never loosen the spec to make it pass. A batch variable listed as a replication level at or below the unit is a spec error: nested preparation instances belong in `nesting`, and calendar batches belong in `batch`.

7. **Update `data/DATA_MANIFEST.md`** with a new entry using `skills/core/templates/dataset-manifest-entry.yaml`.

8. **Log decisions**: If any choices were made during ingestion (excluding records, choosing formats, handling encoding issues), append to `.living/decisions.md`.

9. **Run the post-action hook protocol** after ingestion is complete (see `/mycelium:core` for the full protocol):
   - Update `data/DATA_MANIFEST.md` (done in step 7)
   - Update the dataset documentation file
   - Log decisions to `.living/decisions.md`
   - Log learnings to `.living/learnings.md`. Derive `mitigation_type` by the rule in `skills/core/templates/learning-entry.md`: `structural` if a shipped test, assertion, type constraint, frozenset, or schema validation enforces it; otherwise `convention` if a `.living/conventions.md` entry covers it; otherwise `ambient-awareness`.
   - Run `skills/core/scripts/validate_structure.py`
