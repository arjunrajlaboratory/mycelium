# Mycelium Setup Prompt for Claude Code

Paste everything below the line into Claude Code from within your empty `mycelium` repository.

---

# Task: Set up the Mycelium monorepo

Mycelium is an open-source "living repository" framework. It's a Claude Code skill that you can drop into any analytical repository (new or existing) to give it self-documenting, self-improving capabilities. Every action leaves a structured trace — manifests, decision logs, learnings — so that Claude Code (or any AI coding agent) can read the repo's accumulated intelligence on every subsequent invocation.

The repo has two halves:
1. **`skill/`** — The core Claude Code skill that users install. This is the "AI bomb" you drop into a repo.
2. **`network/`** — A marketplace of domain-specific skill packs (bioinformatics, image analysis, etc.) that users can pull into their projects. Community-contributed over time.

## Repository structure to create

```
mycelium/
├── README.md                              # Project overview, philosophy, quickstart
├── LICENSE                                # MIT
├── CONTRIBUTING.md                        # How to contribute domain skills, file issues, etc.
├── CHANGELOG.md                           # Version history
│
├── skill/                                 # THE CORE SKILL (installable into Claude Code)
│   ├── SKILL.md                           # Main skill file with YAML frontmatter
│   ├── scripts/
│   │   ├── init_repo.py                   # Scaffolds the living repo structure
│   │   ├── update_manifests.py            # Post-action hook: updates all relevant manifests
│   │   ├── ingest_dataset.py              # Dataset ingestion pipeline
│   │   ├── validate_structure.py          # Checks repo conforms to mycelium conventions
│   │   ├── install_domain_skill.py        # Pulls a domain skill from network/ into a project
│   │   ├── crystallize_learnings.py       # Detects patterns in learnings → proposes new conventions
│   │   └── prepare_contribution.py        # Packages a repo-local skill for PR back to network/
│   ├── references/
│   │   ├── folder-structure.md            # Canonical target repo structure spec
│   │   ├── environment-setup.md           # Best practices for env management, installations, gotchas
│   │   ├── analysis-conventions.md        # How analyses are structured (domain-agnostic)
│   │   ├── statistical-conventions.md     # Base statistical methodology standards
│   │   ├── writing-conventions.md         # LaTeX report conventions, citation style, etc.
│   │   ├── data-ingest-conventions.md     # How to bring new data into the framework
│   │   ├── marketplace-guide.md           # How the domain skill ecosystem works
│   │   └── skill-generation-guide.md      # How auto-generation of skills from learnings works
│   └── templates/                         # Templates that init_repo.py and other scripts use
│       ├── CLAUDE.md.template             # Template for the CLAUDE.md placed in target repos
│       ├── analysis-readme.md             # Per-analysis README template
│       ├── dataset-manifest-entry.yaml    # YAML template for data manifest entries
│       ├── analysis-manifest-entry.yaml   # YAML template for analysis manifest entries
│       ├── algorithm-readme.md            # Per-algorithm documentation template
│       ├── decision-log-entry.md          # Template for .living/decisions.md entries
│       ├── learning-entry.md              # Template for .living/learnings.md entries
│       ├── report-template.tex            # LaTeX report skeleton
│       └── marimo-notebook-header.py      # Standard header/imports for marimo notebooks
│
├── network/                               # THE MARKETPLACE
│   ├── README.md                          # How to browse, install, and contribute domain skills
│   ├── skills/
│   │   ├── bioinformatics/                # Example domain skill pack
│   │   │   ├── SKILL_PACK.yaml            # Metadata: name, version, description, dependencies
│   │   │   ├── analysis-conventions.md    # Domain-specific analysis conventions
│   │   │   ├── statistical-conventions.md # Domain-specific stats (e.g., multiple testing, DESeq2)
│   │   │   ├── qc-checklist.md            # Quality control checklist for bioinformatics data
│   │   │   └── templates/
│   │   │       ├── rnaseq-analysis.md     # Template for RNA-seq analysis README
│   │   │       └── deseq2-report.tex      # LaTeX template for differential expression reports
│   │   └── image-analysis/                # Another example domain skill pack
│   │       ├── SKILL_PACK.yaml
│   │       ├── analysis-conventions.md
│   │       ├── segmentation-standards.md
│   │       ├── qc-checklist.md
│   │       └── templates/
│   │           ├── segmentation-benchmark.md
│   │           └── imaging-report.tex
│   └── community-contributed/             # Staging area for community PRs
│       └── .gitkeep
│
└── .github/
    ├── ISSUE_TEMPLATE/
    │   ├── convention-gap.md              # "A convention was wrong or missing"
    │   ├── new-domain-request.md          # "We need a skill pack for domain X"
    │   └── skill-improvement.md           # "This existing skill could be better"
    ├── PULL_REQUEST_TEMPLATE.md
    └── workflows/
        └── validate-skill.yml             # CI: validates SKILL.md format, runs validate_structure.py
```

## Detailed content specifications

### README.md (repo root)

Write a compelling README that covers:
- **Philosophy**: The "mycelium" metaphor — distributed intelligence, every repo is a node, the network shares nutrients (knowledge). Mention that this is inspired by the idea of "living datasets" where the repository carries its own memory and grows smarter over time.
- **What it does**: Drop the core skill into Claude Code, point it at any repo, and it scaffolds a self-documenting analytical framework. Every analysis, dataset, and decision is registered. Learnings accumulate. Domain-specific best practices flow in from the network.
- **Quickstart**: 3-step instructions (install skill → run init → start working)
- **The target repo structure**: Show what a mycelium-enabled repo looks like after init
- **The network**: Explain domain skill packs, how to install them, how to contribute
- **The living loop**: Explain the accumulate → crystallize → contribute cycle
- Use a clean, modern open-source README style. Include badges placeholders.

### skill/SKILL.md

This is the most important file. It must follow Claude Code skill conventions exactly.

**YAML Frontmatter:**
```yaml
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
```

**Body structure — write this as clear, actionable instructions organized by MODE:**

The skill operates in several modes. When invoked, determine which mode the user needs based on their request.

**Mode: `init`**
- Purpose: Scaffold a new or existing repo into a mycelium-enabled living repository
- Steps:
  1. Check if repo already has mycelium structure (look for `.living/` directory)
  2. If new repo: run full scaffold using `scripts/init_repo.py`
  3. If existing repo: run in restructure mode — audit current structure, propose migration plan, ask user to confirm before proceeding
  4. Ask which domain skills to install from the network
  5. Generate CLAUDE.md for the repo (from template) that encodes the living repo protocol
  6. Generate ENVIRONMENTS_INSTALLATIONS.md at repo root
  7. Create initial empty manifests in each top-level directory
  8. Initialize `.living/` with empty decisions.md, learnings.md, conventions.md
- After completion: run `validate_structure.py` to confirm everything is correct
- Consult `references/folder-structure.md` for the canonical structure
- Consult `references/environment-setup.md` for environment setup conventions

**Mode: `ingest`**
- Purpose: Pull a new dataset into the analytical framework
- Steps:
  1. Consult `references/data-ingest-conventions.md`
  2. Determine data type, source, and format
  3. If a domain skill is active, check its conventions for domain-specific validation
  4. Place raw data in `data/raw/[dataset-name]/`
  5. Generate metadata (schema, provenance, summary statistics) in `data/metadata/[dataset-name]/`
  6. Update `data/MANIFEST.md` with new entry (use template)
  7. Log any decisions about data cleaning or exclusion to `.living/decisions.md`
  8. Run the post-action hook protocol (see below)

**Mode: `analyze`**
- Purpose: Start a new analysis or continue an existing one
- Steps:
  1. Read `analysis/MANIFEST.md` to understand existing analyses
  2. Read `data/MANIFEST.md` to understand available data
  3. Read `algorithms/MANIFEST.md` to understand available algorithms
  4. If continuing existing: navigate to `analysis/[name]/` and read its README.md
  5. If new: create `analysis/[name]/` with README.md, scripts/, outputs/, reports/
  6. If building on a parent analysis, record the lineage in the manifest entry
  7. Consult active domain skill conventions if applicable
  8. Consult `references/analysis-conventions.md` for structure
  9. Consult `references/statistical-conventions.md` for methodology
  10. Use marimo for exploratory work, plain Python scripts for reproducible pipelines
  11. Every analysis must have a `run.sh` or `run.py` that reproduces final outputs
  12. Run post-action hook protocol after significant steps

**Mode: `report`**
- Purpose: Generate a LaTeX writeup from an analysis
- Steps:
  1. Consult `references/writing-conventions.md`
  2. Read the analysis README.md and outputs/
  3. Use `templates/report-template.tex` as the skeleton
  4. Pull figures from the analysis `outputs/` directory
  5. Follow statistical reporting conventions from `references/statistical-conventions.md`
  6. Place report in `analysis/[name]/reports/`
  7. Update analysis manifest entry with report status

**Mode: `install-skill`**
- Purpose: Install a domain skill from the mycelium network into the current repo
- Steps:
  1. Consult `references/marketplace-guide.md`
  2. List available skills from `network/skills/`
  3. User selects which to install
  4. Run `scripts/install_domain_skill.py` to copy conventions into `.living/skills/[domain]/`
  5. Update `.living/skills/ACTIVE_SKILLS.yaml`
  6. Update CLAUDE.md to reference new domain conventions

**Mode: `crystallize`**
- Purpose: Review accumulated learnings and propose new conventions
- Steps:
  1. Consult `references/skill-generation-guide.md`
  2. Read `.living/learnings.md` and `.living/decisions.md`
  3. Identify recurring patterns (look for similar tags, repeated problems, evolving conventions)
  4. Propose new convention documents or checklists
  5. Draft them in `.living/generated-skills/[name]/`
  6. Include `ORIGIN.md` linking back to the learnings that spawned it
  7. Ask user if they want to contribute it back to the network

**Mode: `contribute`**
- Purpose: Package a repo-local generated skill for PR to the mycelium network
- Steps:
  1. Run `scripts/prepare_contribution.py`
  2. Generalize repo-specific details into parameters
  3. Create a properly formatted skill pack with SKILL_PACK.yaml
  4. Generate PR description with provenance (anonymized)
  5. Include test cases derived from the learnings

**Mode: `file-issue`**
- Purpose: Report a convention gap or error to the mycelium network
- Steps:
  1. Capture context: what went wrong, what convention was involved
  2. Draft a structured GitHub issue using the appropriate template
  3. Categorize as convention-gap, skill-improvement, or new-domain-request
  4. Present to user for review before filing

**POST-ACTION HOOK PROTOCOL (critical — this is what makes the repo "living"):**
After completing ANY significant action (analysis step, data ingestion, algorithm implementation, report generation), execute:
1. Update the relevant MANIFEST.md with new/changed entries
2. Update or create the README.md in the affected subfolder with current status, key findings, open questions
3. If a non-obvious choice was made, append to `.living/decisions.md` using the decision log template
4. If something unexpected was learned (gotcha, edge case, failure), append to `.living/learnings.md` using the learning entry template
5. Run `scripts/validate_structure.py` to confirm repo still conforms
6. If any domain skill conventions were relevant, note whether they were helpful or had gaps

Include a troubleshooting section at the end for common issues.

### skill/references/folder-structure.md

Document the canonical target repo structure (what a repo looks like AFTER mycelium init). This is the structure that `init_repo.py` creates:

```
project-root/
├── CLAUDE.md                              # AI agent instructions (generated from template)
├── ENVIRONMENTS_INSTALLATIONS.md          # All env setup, dependencies, gotchas
├── .living/                               # The memory layer
│   ├── decisions.md                       # Append-only log: why choices were made
│   ├── learnings.md                       # Append-only log: gotchas, surprises, insights
│   ├── conventions.md                     # Repo-specific overrides to defaults
│   ├── skills/                            # Installed domain skill conventions
│   │   └── ACTIVE_SKILLS.yaml
│   └── generated-skills/                  # Auto-generated from crystallize mode
├── algorithms/
│   ├── MANIFEST.md                        # Registry with YAML entries
│   └── [algorithm-name]/
│       ├── README.md
│       └── ...
├── analysis/
│   ├── MANIFEST.md
│   └── [analysis-name]/
│       ├── README.md                      # Summary, status, findings, open questions
│       ├── scripts/                       # Marimo notebooks and/or Python scripts
│       ├── outputs/                       # Figures, tables, intermediate results
│       └── reports/                       # LaTeX writeups
├── data/
│   ├── MANIFEST.md
│   ├── raw/                               # Immutable originals
│   ├── processed/                         # Cleaned/transformed
│   └── metadata/                          # Schemas, data dictionaries, provenance
└── reference_material/
    ├── MANIFEST.md
    └── ...
```

Explain the purpose of each directory. Emphasize that `data/raw/` is IMMUTABLE — originals are never modified. Explain the MANIFEST.md format (YAML entries with prose annotations). Provide a complete example manifest entry for each type.

### skill/references/environment-setup.md

Cover:
- Python environment management (recommend uv or conda, explain tradeoffs)
- R environment management if relevant
- How to document installations in ENVIRONMENTS_INSTALLATIONS.md
- Common gotchas (system dependencies, CUDA, platform-specific issues)
- Convention: every dependency must be documented with the exact install command AND any gotchas encountered
- Template for ENVIRONMENTS_INSTALLATIONS.md entries

### skill/references/analysis-conventions.md

Cover:
- Every analysis gets its own subfolder under `analysis/`
- Must have a README.md (use template) with: purpose, status, datasets used, algorithms used, key findings, open questions, parent analysis (if any)
- Must have a reproducible entry point (`run.sh` or `run.py`)
- Marimo for exploration, scripts for pipelines — both are fine, but reproducibility requires a script path
- Outputs go in `outputs/` with clear naming conventions
- Reports go in `reports/`
- Analysis status lifecycle: `draft` → `active` → `complete` → `archived`
- How to build on a parent analysis (reference, don't copy)

### skill/references/statistical-conventions.md

Cover domain-agnostic statistical best practices:
- Always report effect sizes alongside p-values
- Specify multiple testing correction method and justify
- Document sample sizes and power considerations
- Report confidence intervals
- Specify random seeds for reproducibility
- Document model assumptions and how they were checked
- Use appropriate visualizations for statistical results
- Domain-specific conventions override these when installed from network

### skill/references/writing-conventions.md

Cover:
- LaTeX as the default for reports
- Standard document structure (abstract, introduction, methods, results, discussion)
- Figure conventions (vector formats preferred, consistent styling)
- Table conventions
- Citation management (BibTeX)
- The report template and how to use it
- How to reference analyses and datasets within the repo

### skill/references/data-ingest-conventions.md

Cover:
- Raw data is immutable — never modify files in `data/raw/`
- Every dataset gets a metadata record in `data/metadata/`
- Metadata must include: source, date acquired, schema/column descriptions, any known issues
- Processing scripts go alongside the processed data or in the analysis that uses them
- Manifest entry is required for every dataset
- Large files: document how to obtain them rather than committing (use .gitignore + instructions)

### skill/references/marketplace-guide.md

Cover:
- What domain skill packs are and how they work
- How to browse available skills in `network/skills/`
- How `install-skill` mode works (copies conventions into `.living/skills/`)
- How conventions cascade: repo-local > domain skill > core defaults
- How to request a new domain skill pack (file an issue)

### skill/references/skill-generation-guide.md

Cover:
- How the crystallize mode works
- What patterns it looks for in learnings and decisions
- How generated skills are structured in `.living/generated-skills/`
- The ORIGIN.md provenance document
- How to promote a generated skill to the network via contribute mode
- The lifecycle: learning → pattern → convention → community contribution

### skill/templates/

Create all templates referenced above. Each should be well-commented with placeholder text explaining what goes where. Key templates:

**CLAUDE.md.template**: This is critical — it's the file placed in target repos that ensures Claude Code follows mycelium conventions even without the skill loaded. It should:
- Explain the repo is a mycelium-enabled living repository
- List the active domain skills and where to find their conventions
- Encode the post-action hook protocol
- Point to `.living/` for accumulated intelligence
- Reference ENVIRONMENTS_INSTALLATIONS.md for setup
- Include the analysis workflow (read manifests → understand context → do work → update everything)

**dataset-manifest-entry.yaml and analysis-manifest-entry.yaml**: Structured YAML templates with all fields and comments explaining each.

### skill/scripts/

Create **stub implementations** for all scripts. Each should:
- Have a proper docstring explaining its purpose
- Parse command-line arguments with argparse
- Have a `main()` function with TODO comments explaining the logic
- Include error handling structure
- Print helpful messages about what it would do

The scripts don't need to be fully functional yet — they're scaffolds that will be filled in iteratively. But they should be runnable (just printing what they'd do).

`validate_structure.py` should be the MOST complete — it should actually check:
- `.living/` directory exists with required files
- All four top-level dirs exist (algorithms, analysis, data, reference_material)
- Each has a MANIFEST.md
- MANIFEST.md files have valid YAML frontmatter
- Any analysis subdirectory has a README.md
- ENVIRONMENTS_INSTALLATIONS.md exists at root

### network/skills/bioinformatics/ and network/skills/image-analysis/

Create substantive starter content for these two domain skill packs. They should be real and useful, not just placeholders. Draw on domain knowledge:

**Bioinformatics** should cover: RNA-seq analysis conventions, single-cell analysis conventions, differential expression methodology (DESeq2, etc.), gene set enrichment, QC metrics (read quality, mapping rates, library complexity), multiple testing, visualization conventions for genomics data.

**Image analysis** should cover: segmentation conventions, image preprocessing standards, quantification methodology, benchmarking protocols, handling of different modalities (fluorescence, brightfield, etc.), statistical analysis of image-derived measurements, QC for imaging data.

### network/skills/*/SKILL_PACK.yaml

Format:
```yaml
---
name: bioinformatics
version: 0.1.0
description: >
  Domain conventions for bioinformatics analyses including RNA-seq,
  single-cell, and genomics workflows.
author: mycelium
dependencies: []  # Other skill packs this depends on
tags: [bioinformatics, genomics, rnaseq, single-cell]
---
```

### .github/ISSUE_TEMPLATE/

Create three issue templates:

**convention-gap.md**: For reporting when a convention was wrong, missing, or led to a bad outcome. Fields: domain skill affected, what happened, what should have happened, suggested fix.

**new-domain-request.md**: For requesting a new domain skill pack. Fields: domain name, why it's needed, key conventions that should be included, volunteer to contribute?

**skill-improvement.md**: For suggesting improvements to existing skills. Fields: skill affected, current behavior, desired behavior, context.

### .github/PULL_REQUEST_TEMPLATE.md

Template for PRs, especially community-contributed domain skills. Checklist: SKILL_PACK.yaml present, conventions are domain-specific not generic, templates included, tested in a real repo.

### .github/workflows/validate-skill.yml

A GitHub Actions workflow that on PR:
- Checks that any new skill pack has a valid SKILL_PACK.yaml
- Checks that the core SKILL.md has valid frontmatter
- Runs validate_structure.py against the repo itself
- Lints markdown files

### CONTRIBUTING.md

Cover:
- How to contribute a new domain skill pack
- How to file issues for convention gaps
- How to promote a repo-local generated skill
- Code of conduct reference
- Review process (core skill vs network skills)

## Implementation notes

- Use Python 3.10+ for all scripts
- Use `pathlib` for all file operations
- Use `pyyaml` for YAML parsing (but note it may need to be installed — document in environment setup)
- All markdown should be clean, well-formatted, and use consistent heading levels
- Include meaningful .gitignore at repo root (Python, LaTeX, common data formats, .DS_Store, etc.)
- Every file should have a brief comment or header explaining its purpose
- Write for an audience of computational scientists who are comfortable with code but may not be DevOps experts

## Style and tone

- Technical but approachable
- Use the mycelium metaphor tastefully — it should enrich understanding, not feel forced
- Assume readers are smart and busy
- Prefer concrete examples over abstract descriptions
- When explaining "why," keep it to one sentence unless the reasoning is non-obvious

## What to do now

Please create this entire repository structure with all files populated. Start with the core skill (SKILL.md and references), then the templates, then the scripts, then the network domain skills, then the GitHub configuration, and finally the root-level documentation.

Take your time and be thorough. Quality matters more than speed. This is a foundational project.
