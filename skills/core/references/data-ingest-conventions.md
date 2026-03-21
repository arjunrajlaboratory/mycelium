# Data Ingestion Conventions

How to bring new data into a mycelium-enabled repository.

## Core Principle: Raw Data Is Immutable

Files in `data/raw/` are **never modified** after initial placement. If raw data has issues, document them in the metadata and create a corrected version in `data/processed/`. This ensures you can always trace back to the original source.

## Ingestion Workflow

### 1. Place raw data

```
data/raw/[dataset-name]/
├── file1.csv
├── file2.csv
└── DATASET_NAME.md    # UPPER_SNAKE_CASE of folder name
```

Each dataset gets its own subdirectory. Include a documentation file (named in UPPER_SNAKE_CASE of the folder name, e.g., `PATIENT_COHORT_2024.md`) noting where the data came from and when.

### 2. Create metadata

```
data/metadata/[dataset-name]/
├── schema.yaml        # Column descriptions, types, units
├── provenance.md      # Full source details, acquisition method, contact
└── summary_stats.md   # Row counts, column distributions, missing data summary
```

Use the templates at `skills/core/templates/schema.yaml`, `skills/core/templates/provenance.md`, and `skills/core/templates/summary_stats.md` as starting points for each file.

**Required metadata fields:**
- **Source**: Where the data came from (URL, database, collaborator, instrument)
- **Date acquired**: When the data was obtained
- **Schema/column descriptions**: What each field means, including units
- **Known issues**: Missing data, encoding problems, known errors
- **Access restrictions**: Any data use agreements or privacy considerations

See `skills/core/templates/provenance.md` for the complete field list and format.

### 3. Update the manifest

Add an entry to `data/DATA_MANIFEST.md` using the `dataset-manifest-entry.yaml` template. Include both the YAML block and a prose description.

### 4. Log decisions

If any choices were made during ingestion (e.g., excluding certain records, choosing one format over another), log them in `.living/decisions.md`.

## Processing Conventions

Processed data lives in `data/processed/[dataset-name]/`:

- Document the processing steps in the dataset's documentation file or processing script
- Record the provenance: which raw dataset, what transformations, any parameters
- If the processing is tied to a specific analysis, the processing script can live in the analysis's `scripts/` directory instead

## Large Files

For files too large to commit to git:

1. Add them to `.gitignore`
2. Document how to obtain them in the dataset's UPPER_SNAKE_CASE.md file
3. Include download scripts if possible
4. Note the expected file sizes and checksums (SHA256) for verification

```markdown
# data/raw/large-dataset/LARGE_DATASET.md

## Large Dataset

This dataset is too large for git. To obtain:

1. Download from: https://example.com/data/large-dataset.tar.gz
2. Expected size: 4.2 GB
3. SHA256: abc123...
4. Extract into this directory: `tar xzf large-dataset.tar.gz`
```

## Domain-Specific Validation

If a domain skill is active, check its conventions for additional validation requirements:

- **Bioinformatics**: QC metrics (read quality, mapping rates, library complexity)
- **Image analysis**: Image quality checks, metadata extraction, format validation

Domain skills may add required metadata fields or validation steps to the standard ingestion workflow.

## Manifest Entry Checklist

Before considering ingestion complete:

- [ ] Raw data placed in `data/raw/[dataset-name]/`
- [ ] Metadata created in `data/metadata/[dataset-name]/`
- [ ] Schema documented with column descriptions and types
- [ ] Metadata files created from templates (`schema.yaml`, `provenance.md`, `summary_stats.md`)
- [ ] Provenance documented (source, date, method)
- [ ] Known issues documented
- [ ] `data/DATA_MANIFEST.md` updated with new entry
- [ ] Any decisions logged in `.living/decisions.md`
- [ ] Large files handled appropriately (gitignored + documented)
