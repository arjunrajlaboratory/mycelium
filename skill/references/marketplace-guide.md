# Mycelium Network — Marketplace Guide

How skill packs work and how to use them.

## What Are Skill Packs?

Skill packs are collections of conventions, templates, and checklists that layer on top of mycelium's core references. There are two types:

- **Core packs** (`core: true` in `SKILL_PACK.yaml`): Auto-installed during `mycelium init`. These provide batteries-included practices every analysis repo should have. Currently: `robust-analysis` (defensive execution, validation, sensitivity sweeps) and `report-generator` (structured LaTeX PDF reports).
- **Domain packs**: Opt-in specializations for specific fields (e.g., bioinformatics, image analysis). Installed manually via the `install-skill` mode.

A skill pack typically includes:
- **Analysis conventions**: How analyses in this domain are structured (hub file with progressive disclosure)
- **Statistical conventions**: Domain-specific methodology standards
- **QC checklists**: Quality control checks specific to the data type or practice
- **Templates**: Report and analysis templates for common workflows
- **Reference files**: Detailed guidance consulted on demand

## Browsing Available Skills

Available skill packs are stored in the mycelium repository under `network/skills/`:

```
network/skills/
├── robust-analysis/           # core — defensive analysis practices
│   ├── SKILL_PACK.yaml
│   ├── analysis-conventions.md
│   ├── strict-execution-rules.md
│   ├── validation-checks.md
│   ├── sensitivity-analysis.md
│   ├── null-hypothesis-protocol.md
│   ├── adversarial-probing.md
│   ├── qc-checklist.md
│   └── templates/
├── report-generator/          # core — LaTeX PDF report generation
│   ├── SKILL_PACK.yaml
│   ├── analysis-conventions.md
│   ├── qc-checklist.md
│   ├── references/
│   └── assets/
├── bioinformatics/            # domain — genomics workflows
│   ├── SKILL_PACK.yaml
│   ├── analysis-conventions.md
│   ├── statistical-conventions.md
│   ├── qc-checklist.md
│   └── templates/
└── image-analysis/            # domain — microscopy and segmentation
    ├── SKILL_PACK.yaml
    ├── analysis-conventions.md
    ├── segmentation-standards.md
    ├── qc-checklist.md
    └── templates/
```

Each pack has a `SKILL_PACK.yaml` with metadata: name, version, description, dependencies, tags, and `core: true/false`.

## Installing Skill Packs

### Core packs (automatic)

Core packs are installed automatically during `mycelium init`. After initialization, your `.living/skills/` directory includes them:

```
.living/skills/
├── ACTIVE_SKILLS.yaml
├── robust-analysis/
│   ├── analysis-conventions.md    # Start here — links to detail files
│   ├── strict-execution-rules.md
│   ├── validation-checks.md
│   └── ...
└── report-generator/
    ├── analysis-conventions.md    # Start here — workflow and section guide
    ├── references/
    └── assets/
```

### Domain packs (manual)

Use mycelium's `install-skill` mode:

1. Run `scripts/install_domain_skill.py` specifying the domain
2. The script copies conventions into `.living/skills/[domain]/`
3. `ACTIVE_SKILLS.yaml` is updated to register the new skill
4. `CLAUDE.md` is updated to reference the new domain conventions

After installing a domain pack:

```
.living/skills/
├── ACTIVE_SKILLS.yaml
├── robust-analysis/           # core (auto-installed)
├── report-generator/          # core (auto-installed)
└── bioinformatics/            # domain (manually installed)
    ├── analysis-conventions.md
    ├── statistical-conventions.md
    ├── qc-checklist.md
    └── templates/
```

## Convention Cascade

When conventions exist at multiple levels, they cascade with this priority (this applies to both core and domain packs):

```
Repo-local (.living/conventions.md)  ← highest priority
    ↓
Domain skill (.living/skills/[domain]/)
    ↓
Core mycelium (skill/references/)     ← lowest priority
```

This means:
- Core conventions apply everywhere by default
- Domain skills can override core conventions for domain-specific needs
- Repo-local conventions override everything for project-specific exceptions

Document all overrides in `.living/conventions.md` with a reason.

## Requesting a New Domain

If your domain doesn't have a skill pack:

1. Use mycelium's `file-issue` mode to create a `new-domain-request` issue
2. Include: domain name, key conventions that should be covered, common workflows, and whether you'd volunteer to help create it
3. Community members or the mycelium team will respond

You can also start building conventions locally (they'll accumulate in `.living/learnings.md` and `.living/conventions.md`) and later use `crystallize` and `contribute` modes to package them into a formal skill pack.

## Multiple Skills

You can install multiple skill packs. If they conflict (rare), the convention cascade applies — repo-local overrides resolve any ambiguity. Active skills are all listed in `ACTIVE_SKILLS.yaml`:

```yaml
# .living/skills/ACTIVE_SKILLS.yaml
active_skills:
  - name: robust-analysis
    version: 0.1.0
    installed: 2024-03-10
    path: .living/skills/robust-analysis/
    core: true
  - name: report-generator
    version: 0.1.0
    installed: 2024-03-10
    path: .living/skills/report-generator/
    core: true
  - name: bioinformatics
    version: 0.1.0
    installed: 2024-03-15
    path: .living/skills/bioinformatics/
  - name: image-analysis
    version: 0.1.0
    installed: 2024-03-20
    path: .living/skills/image-analysis/
```
