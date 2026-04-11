# Validation Report

Post-workflow validation summary. Copy this template to `analysis/[name]/validation-report.md`.

## Analysis: [name]
## Date: [YYYY-MM-DD]

---

## Data Integrity

| Check | Status | Notes |
|-------|--------|-------|
| Input cell/sample count matches manifest | | |
| No silent row drops during pipeline | | |
| Column types preserved through transforms | | |
| Output dimensions plausible | | |

## QC Summary

| Metric | Threshold | Rationale | Cells/Samples Affected |
|--------|-----------|-----------|----------------------|
| | | | |

## Sensitivity Analyses

| Parameter | Values Swept | Conclusion Stable? | Supplementary Figure |
|-----------|-------------|-------------------|---------------------|
| | | | |

## Reproducibility

| Check | Status |
|-------|--------|
| `run.sh` / `run.py` exists | |
| Random seeds pinned | |
| Package versions recorded | |
| All outputs generated from raw data | |

## Convention Compliance

| Item | Done? |
|------|-------|
| `.living/decisions.md` updated (min 3 entries) | |
| `.living/learnings.md` updated if surprises found | |
| Skill invocation log completed | |
| Persona review completed (if assigned) | |
| Figures cross-referenced with tables | |
| QC thresholds documented with rationale | |
