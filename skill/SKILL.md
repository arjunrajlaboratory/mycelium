---
name: mycelium
description: >
  Self-documenting, self-improving analytical repository framework.
  Scaffolds living repositories where every analysis, dataset, and decision
  is registered and discoverable. Use when user says "set up mycelium",
  "initialize living repo", "ingest dataset", "start new analysis",
  "crystallize learnings", "install domain skill", or wants to restructure
  an existing repository into a self-documenting framework. Also use when
  user mentions "living dataset", "living repo", or "mycelium".
---

# Mycelium — Living Repository Skill

When invoked, determine which mode the user needs based on their request, then follow the corresponding protocol.

## Mode: `init`

**Trigger**: "set up mycelium", "initialize living repo", "restructure this repo"

**Purpose**: Scaffold a new or existing repo into a mycelium-enabled living repository.

**Steps**:
1. Check if repo already has mycelium structure (look for `.living/` directory).
2. **New repo**: Run full scaffold using `scripts/init_repo.py`.
3. **Existing repo**: Run in restructure mode — audit current structure, propose migration plan, ask user to confirm before proceeding.
4. **Auto-install core skill packs**: Scan `network/skills/*/SKILL_PACK.yaml` for packs with `core: true` and install each one using `scripts/install_domain_skill.py`. Currently the core packs are `robust-analysis` and `report-generator`. These provide batteries-included practices every repo should have.
5. Ask which **domain** skills to install from the network (e.g., bioinformatics, image-analysis). Domain packs are those without `core: true`.
6. Generate `CLAUDE.md` for the repo (from `templates/CLAUDE.md.template`) that encodes the living repo protocol.
7. Generate `ENVIRONMENTS_INSTALLATIONS.md` at repo root.
8. Create descriptive manifests in each top-level directory (`ANALYSIS_MANIFEST.md`, `DATA_MANIFEST.md`, `ALGORITHM_MANIFEST.md`, `REFERENCE_MANIFEST.md`).
9. Initialize `.living/` with empty `decisions.md`, `learnings.md`, `conventions.md`.
10. After completion: run `scripts/validate_structure.py` to confirm everything is correct.

**References to consult**:
- `references/folder-structure.md` — canonical target structure
- `references/environment-setup.md` — environment setup conventions

---

## Mode: `ingest`

**Trigger**: "ingest dataset", "add data", "import data"

**Purpose**: Pull a new dataset into the analytical framework.

**Steps**:
1. Consult `references/data-ingest-conventions.md`.
2. Determine data type, source, and format.
3. If a domain skill is active, check its conventions for domain-specific validation.
4. Place raw data in `data/raw/[dataset-name]/`.
5. Generate metadata (schema, provenance, summary statistics) in `data/metadata/[dataset-name]/`.
6. Update `data/DATA_MANIFEST.md` with new entry (use `templates/dataset-manifest-entry.yaml`).
7. Log any decisions about data cleaning or exclusion to `.living/decisions.md`.
8. Run the post-action hook protocol (see below).

---

## Mode: `analyze`

**Trigger**: "start new analysis", "continue analysis", "run analysis"

**Purpose**: Start a new analysis or continue an existing one.

**Steps**:
1. Read `analysis/ANALYSIS_MANIFEST.md` to understand existing analyses.
2. Read `data/DATA_MANIFEST.md` to understand available data.
3. Read `algorithms/ALGORITHM_MANIFEST.md` to understand available algorithms.
4. **Continuing existing**: Navigate to `analysis/[name]/` and read its documentation file (UPPER_SNAKE_CASE of folder name, e.g., `SNP_ANALYSIS.md`).
5. **New**: Create `analysis/[name]/` with documentation file (UPPER_SNAKE_CASE.md), `scripts/`, `outputs/`, `reports/`.
6. If building on a parent analysis, record the lineage in the manifest entry.
7. Consult active domain skill conventions if applicable.
8. Consult `references/analysis-conventions.md` for structure.
9. Consult `references/statistical-conventions.md` for methodology.
10. **If `robust-analysis` is installed** (check `.living/skills/robust-analysis/`), follow its conventions: strict execution mode, validation checks, sensitivity sweeps for every decision, null hypothesis testing, and adversarial probing of conclusions. Its `analysis-conventions.md` is the entry point; consult detail files as each step requires them.
11. Use marimo for exploratory work, plain Python scripts for reproducible pipelines.
12. Every analysis must have a `run.sh` or `run.py` that reproduces final outputs.
13. Run post-action hook protocol after significant steps.

---

## Mode: `report`

**Trigger**: "write report", "generate report", "write up results", "create a report", "generate a PDF", "make a LaTeX report"

**Purpose**: Generate a structured LaTeX PDF report from an analysis.

**Steps**:
1. Gather context: read the analysis documentation file (UPPER_SNAKE_CASE.md), `outputs/`, `.living/decisions.md`, and `git log`.
2. **Choose the report pathway**:
   - **If `report-generator` is installed** (check `.living/skills/report-generator/`): Follow its `analysis-conventions.md` as the entry point. Use the template from `.living/skills/report-generator/assets/report-template.tex`. The report structure is: Title, Abstract, TOC, Problem Statement, Methods (Definitions/Overview/Technical Detail), Results, Conclusions, Next Steps, Provenance, Appendix. Consult `references/section-guide.md` for detailed writing guidance per section.
   - **If not installed**: Fall back to `references/writing-conventions.md` and `templates/report-template.tex`. The fallback structure is: Title, Abstract, Introduction, Methods (Data/Analysis/Statistical Methods), Results, Discussion, References.
3. Copy the chosen template to `analysis/[name]/reports/[name]-report.tex`.
4. Fill in each section following the chosen structure.
5. Pull figures from `outputs/figures/` and supplementary figures from `outputs/figures/supplementary/`.
6. Compile: `pdflatex` twice (with `bibtex` between passes if citations are used).
7. Verify the PDF renders correctly (no unresolved references, all figures present).
8. Update analysis manifest entry with report status.

---

## Mode: `install-skill`

**Trigger**: "install domain skill", "add bioinformatics skill", "install skill pack"

**Purpose**: Install a skill pack from the mycelium network into the current repo.

**Context**: Core packs (`robust-analysis`, `report-generator`) are auto-installed during `init`. This mode is primarily for adding domain packs after initialization, but can also be used to manually install or reinstall any pack.

**Steps**:
1. Consult `references/marketplace-guide.md`.
2. List available skills from `network/skills/`, noting which are core (already installed) and which are domain (available to add).
3. User selects which to install.
4. Run `scripts/install_domain_skill.py` to copy conventions into `.living/skills/[domain]/`.
5. Update `.living/skills/ACTIVE_SKILLS.yaml`.
6. Update `CLAUDE.md` to reference new skill conventions.

---

## Mode: `crystallize`

**Trigger**: "crystallize learnings", "review learnings", "extract patterns"

**Purpose**: Review accumulated learnings and propose new conventions.

**Steps**:
1. Consult `references/skill-generation-guide.md`.
2. Read `.living/learnings.md` and `.living/decisions.md`.
3. Identify recurring patterns (look for similar tags, repeated problems, evolving conventions).
4. Propose new convention documents or checklists.
5. Draft them in `.living/generated-skills/[name]/`.
6. Include `ORIGIN.md` linking back to the learnings that spawned it.
7. Ask user if they want to contribute it back to the network.

---

## Mode: `contribute`

**Trigger**: "contribute skill", "share back", "submit to network"

**Purpose**: Package a repo-local generated skill for PR to the mycelium network.

**Steps**:
1. Run `scripts/prepare_contribution.py`.
2. Generalize repo-specific details into parameters.
3. Create a properly formatted skill pack with `SKILL_PACK.yaml`.
4. Generate PR description with provenance (anonymized).
5. Include test cases derived from the learnings.

---

## Mode: `file-issue`

**Trigger**: "file issue", "report convention gap", "report bug in skill"

**Purpose**: Report a convention gap or error to the mycelium network.

**Steps**:
1. Capture context: what went wrong, what convention was involved.
2. Draft a structured GitHub issue using the appropriate template.
3. Categorize as `convention-gap`, `skill-improvement`, or `new-domain-request`.
4. Present to user for review before filing.

---

## Post-Action Hook Protocol

**This is what makes the repo "living." Execute after ANY significant action** (analysis step, data ingestion, algorithm implementation, report generation):

1. **Update manifests**: Update the relevant manifest (`ANALYSIS_MANIFEST.md`, `DATA_MANIFEST.md`, etc.) with new/changed entries.
2. **Update documentation**: Update or create the UPPER_SNAKE_CASE.md file in the affected subfolder with current status, key findings, open questions.
3. **Log decisions**: If a non-obvious choice was made, append to `.living/decisions.md` using the decision log template.
4. **Log learnings**: If something unexpected was learned (gotcha, edge case, failure), append to `.living/learnings.md` using the learning entry template.
5. **Validate**: Run `scripts/validate_structure.py` to confirm repo still conforms.
6. **Skill feedback**: If any domain skill conventions were relevant, note whether they were helpful or had gaps.

### Automated Enforcement (Claude Code Hooks)

Mycelium ships optional hook scripts in `hooks/` that enforce the post-action protocol automatically:

| Hook | Event | Purpose |
|------|-------|---------|
| `mycelium-health.sh` | SessionStart | Warns if `.living/` is missing or incomplete; records session timestamp |
| `mycelium-stop-check.sh` | Stop | Blocks session end if significant work was done without updating `.living/` |

**Significance heuristic**: The stop hook counts files changed during the session — both uncommitted changes (`git diff HEAD`) and files touched in commits made since session start (`git log --after=@$TIMESTAMP`). If the total is 3+ files and neither `.living/learnings.md` nor `.living/decisions.md` was modified after the session-start timestamp, the hook blocks.

**Installation**: Register the hooks in your project's `.claude/settings.local.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/mycelium/skill/hooks/mycelium-health.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/mycelium/skill/hooks/mycelium-stop-check.sh"
          }
        ]
      }
    ]
  }
}
```

Replace `/path/to/mycelium/` with the absolute path to your mycelium clone.

### Subagent-Driven Sessions

When work is dispatched to subagents (main context = coordination only):

1. **Subagents do not need mycelium awareness** — they focus on their implementation task and report results back.
2. **After all subagent batches complete**, the main context dispatches a crystallization subagent (lightweight model) that:
   - Reviews the summary of what was accomplished
   - Appends entries to `.living/learnings.md` and `.living/decisions.md`
   - Checks cross-project relevance (if applicable)
3. **The Stop hook enforces this** — it blocks session end if `.living/` wasn't updated after significant work, catching sessions where the crystallization step was forgotten.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `validate_structure.py` fails after init | Check that all four top-level directories exist and each has its manifest (`ANALYSIS_MANIFEST.md`, etc.). Re-run init if needed. |
| Domain skill conventions conflict with core | Domain conventions take precedence. Update `.living/conventions.md` to document the override. |
| `.living/` directory missing | Run `init` mode to scaffold the living layer. Never create it manually — the script sets up required files. |
| Manifest entry format errors | Check `templates/dataset-manifest-entry.yaml` or `templates/analysis-manifest-entry.yaml` for the correct format. |
| `ENVIRONMENTS_INSTALLATIONS.md` not found | Run `init` mode or create it manually following `references/environment-setup.md`. |
| Crystallize finds no patterns | This is normal for young repos. Continue logging learnings and decisions — patterns emerge over time. |
| Install-skill can't find a domain | Check `network/skills/` for available packs. File a `new-domain-request` issue if your domain isn't covered. |
