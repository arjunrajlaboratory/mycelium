# Skill Bridge Conventions

Routes analysis workflows to external skill repositories and researcher personas. Skills live as inert git clones — never installed as agent skill packs. This convention is the sole trigger layer.

---

## How It Works

1. **Convention determines the workflow** (this file routes to a detail file)
2. **Workflow detail file names specific skills** with file paths into the repos
3. **Agent reads ONE skill file at a time** (~150-200 lines per step)
4. **Robust-analysis conventions wrap every step** (validation, sensitivity, logging)
5. **Personas review at checkpoints** (optional, ~300 words per persona)

## Skill Routing

Before invoking external skills, determine which repo to consult:

| Task Domain | Primary Repo | Path Prefix | Fallback |
|-------------|-------------|-------------|----------|
| Bioinformatics (seq, expression, variants, epigenomics) | bioSkills | `skillpacks/bioSkills/` | scientific-agent-skills |
| Single-cell RNA-seq | bioSkills | `skillpacks/bioSkills/single-cell/` | scientific-agent-skills (scanpy) |
| Differential expression | bioSkills | `skillpacks/bioSkills/differential-expression/` | scientific-agent-skills (pydeseq2) |
| Pathway / enrichment analysis | bioSkills | `skillpacks/bioSkills/pathway-analysis/` | — |
| Database queries (PubChem, ChEMBL, FRED, 78+ DBs) | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/database-lookup/` | — |
| Cheminformatics / drug discovery | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/rdkit/` | bioSkills (chemoinformatics/) |
| ML/DL frameworks (PyTorch, sklearn) | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/` | — |
| Scientific writing / communication | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/scientific-writing/` | — |
| Protein structure / design | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/esm/` | bioSkills (structural-biology/) |
| Lab automation (Opentrons, Benchling) | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/` | — |
| Visualization | scientific-agent-skills | `skillpacks/scientific-agent-skills/scientific-skills/scientific-visualization/` | bioSkills (data-visualization/) |

> For full routing details: [skill-routing.md](skill-routing.md)

## Persona Review Protocol

After completing analysis (or at designated checkpoints), invoke domain-appropriate personas as reviewers. Two modes:

**Collaborator mode**: Load a persona's `decision_rules` and `anti_patterns` during analysis to steer methodology.

**Reviewer mode**: After analysis, present results to a persona panel for adversarial review.

> For persona assignments per workflow: [persona-routing.md](persona-routing.md)

## Workflow Entry Points

| Workflow | Detail File |
|----------|------------|
| Single-cell RNA-seq | [references/single-cell-workflow.md](references/single-cell-workflow.md) |
| Bulk RNA-seq & DE | (planned) |
| Variant analysis | (planned) |
| Drug discovery | (planned) |
| Multi-omics integration | (planned) |

## Skill Invocation Protocol

For every step in a workflow:

1. **Before**: Check input data passes the relevant QC checklist section
2. **Read**: Load the specific SKILL.md file named in the workflow detail
3. **Execute**: Write code following the skill's patterns, applying robust-analysis strict execution rules
4. **Validate**: Check outputs against expected schema (row counts, types, distributions)
5. **Persona check** (if assigned): Apply persona's anti-patterns as constraints
6. **Record**: Log skill used, parameters, and any surprises to `.living/decisions.md` and `.living/learnings.md`

## Source Verification

Before first use in a project, verify all repos are cloned:

```bash
# Check that repos exist at expected paths
cat .living/conventions/skill-bridge/skill-sources.yaml
# Verify canary files exist
ls skillpacks/bioSkills/single-cell/data-io/SKILL.md
ls skillpacks/scientific-agent-skills/scientific-skills/scanpy/SKILL.md
ls skillpacks/Autonomous-Science/personas/library/arjun_raj.json
```

If a repo is missing, clone it. Do NOT install it as a skill pack.
