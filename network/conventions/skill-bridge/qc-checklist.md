# Skill Bridge QC Checklist

Validation checks that wrap external skill invocations. These extend (not replace) the bioinformatics QC checklist and robust-analysis validation checks.

---

## Pre-Skill Checks

Run before reading any external SKILL.md file:

- [ ] **Source repo exists** at path specified in `skill-sources.yaml`
- [ ] **Specific skill file exists** at the path the workflow references
- [ ] **Input data validated**: row counts, column types, no silent NaN/None infiltration
- [ ] **Input data registered** in `data/DATA_MANIFEST.md` with known issues documented

## Per-Step Checks

Run after executing code guided by an external skill:

- [ ] **Output shape assertion**: row/column counts logged and compared to input
- [ ] **No silent drops**: if rows were removed (filtering, QC), the count difference is logged with reason
- [ ] **Type preservation**: numeric columns didn't silently become strings; categorical levels didn't change
- [ ] **Distribution plausibility**: quick summary stats (min, max, mean, n_zeros) on key output columns
- [ ] **Skill version note**: if the skill references a specific package version, verify installed version matches

## Persona Review Checks

Run when persona review is invoked:

- [ ] **Persona file loaded**: JSON parsed, `prompt_fragment` and `decision_rules` present
- [ ] **Anti-patterns checked**: each persona anti-pattern explicitly evaluated against the analysis outputs
- [ ] **Dissenting views recorded**: if personas disagree, both viewpoints logged in `.living/decisions.md`
- [ ] **Sub-hypotheses captured**: any follow-up ideas from persona review logged in `.living/learnings.md` or `todo/`

## Post-Workflow Checks

Run after the full workflow completes:

- [ ] **All QC thresholds documented** in the analysis README with rationale
- [ ] **Sensitivity analysis completed** for at least one key parameter per workflow
- [ ] **Figures cross-referenced with tables**: every number in a figure caption has a source table
- [ ] **Reproducible entry point**: `run.sh` or `run.py` exists and would reproduce all outputs
- [ ] **Random seeds pinned** for all stochastic processes
- [ ] **Decisions logged** in `.living/decisions.md` (minimum 3 entries for any non-trivial analysis)
- [ ] **Learnings logged** in `.living/learnings.md` if anything surprising was encountered
- [ ] **Skill invocation log** filled out (see `templates/skill-invocation-log.md`)
