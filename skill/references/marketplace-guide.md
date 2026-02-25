# Mycelium Network — Marketplace Guide

How domain skill packs work and how to use them.

## What Are Domain Skill Packs?

Domain skill packs are collections of conventions, templates, and checklists tailored to a specific analytical domain (e.g., bioinformatics, image analysis, epidemiology). They layer on top of mycelium's core conventions to provide domain-specific guidance.

A skill pack typically includes:
- **Analysis conventions**: How analyses in this domain are structured
- **Statistical conventions**: Domain-specific methodology standards
- **QC checklists**: Quality control checks specific to the data type
- **Templates**: Report and analysis templates for common workflows

## Browsing Available Skills

Available skill packs are stored in the mycelium repository under `network/skills/`:

```
network/skills/
├── bioinformatics/
│   ├── SKILL_PACK.yaml
│   ├── analysis-conventions.md
│   ├── statistical-conventions.md
│   ├── qc-checklist.md
│   └── templates/
└── image-analysis/
    ├── SKILL_PACK.yaml
    ├── analysis-conventions.md
    ├── segmentation-standards.md
    ├── qc-checklist.md
    └── templates/
```

Each pack has a `SKILL_PACK.yaml` with metadata: name, version, description, dependencies, and tags.

## Installing a Skill Pack

Use mycelium's `install-skill` mode:

1. Run `scripts/install_domain_skill.py` specifying the domain
2. The script copies conventions into `.living/skills/[domain]/`
3. `ACTIVE_SKILLS.yaml` is updated to register the new skill
4. `CLAUDE.md` is updated to reference the new domain conventions

After installation, your `.living/skills/` directory looks like:

```
.living/skills/
├── ACTIVE_SKILLS.yaml
└── bioinformatics/
    ├── analysis-conventions.md
    ├── statistical-conventions.md
    ├── qc-checklist.md
    └── templates/
```

## Convention Cascade

When conventions exist at multiple levels, they cascade with this priority:

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
  - name: bioinformatics
    version: 0.1.0
    installed: 2024-03-15
    path: .living/skills/bioinformatics/
  - name: image-analysis
    version: 0.1.0
    installed: 2024-03-20
    path: .living/skills/image-analysis/
```
