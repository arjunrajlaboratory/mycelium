# Mycelium Network — Convention Pack Guide

How convention packs work and how to use them.

## What Are Convention Packs?

Convention packs are collections of conventions, templates, and checklists that layer on top of mycelium's core references. They are reference material consumed by mycelium's action skills (`/mycelium:analyze`, `/mycelium:report`, `/mycelium:ideas`). There are two types:

- **Core packs** (`core: true` in `CONVENTION_PACK.yaml`): Auto-installed during `mycelium init`. These provide batteries-included practices every analysis repo should have. Currently: `robust-analysis` (defensive execution, validation, sensitivity sweeps), `report-generator` (structured LaTeX PDF reports), and `idea-generator` (persona-based creative ideation).
- **Domain packs**: Opt-in specializations for specific fields (e.g., bioinformatics, image analysis). Installed manually via the `install-convention` mode.

A convention pack typically includes:
- **Analysis conventions**: How analyses in this domain are structured (hub file with progressive disclosure)
- **Statistical conventions**: Domain-specific methodology standards
- **QC checklists**: Quality control checks specific to the data type or practice
- **Templates**: Report and analysis templates for common workflows
- **Reference files**: Detailed guidance consulted on demand

## Browsing Available Convention Packs

Available convention packs are stored in the mycelium repository under `network/conventions/`:

```
network/conventions/
├── robust-analysis/           # core — defensive analysis practices
│   ├── CONVENTION_PACK.yaml
│   ├── analysis-conventions.md
│   ├── strict-execution-rules.md
│   ├── validation-checks.md
│   ├── sensitivity-analysis.md
│   ├── null-hypothesis-protocol.md
│   ├── adversarial-probing.md
│   ├── qc-checklist.md
│   └── templates/
├── report-generator/          # core — LaTeX PDF report generation
│   ├── CONVENTION_PACK.yaml
│   ├── analysis-conventions.md
│   ├── qc-checklist.md
│   ├── references/
│   └── assets/
├── idea-generator/            # core — persona-based ideation
│   ├── CONVENTION_PACK.yaml
│   ├── analysis-conventions.md
│   ├── execution-protocol.md
│   ├── persona-catalog.md
│   └── idea-template.md
├── bioinformatics/            # domain — genomics workflows
│   ├── CONVENTION_PACK.yaml
│   ├── analysis-conventions.md
│   ├── statistical-conventions.md
│   ├── qc-checklist.md
│   └── templates/
└── image-analysis/            # domain — microscopy and segmentation
    ├── CONVENTION_PACK.yaml
    ├── analysis-conventions.md
    ├── segmentation-standards.md
    ├── qc-checklist.md
    └── templates/
```

Each pack has a `CONVENTION_PACK.yaml` with metadata: name, version, description, dependencies, tags, and `core: true/false`.

## Installing Convention Packs

### Core packs (automatic)

Core packs are installed automatically during `mycelium init`. After initialization, your `.living/conventions/` directory includes them:

```
.living/conventions/
├── ACTIVE_CONVENTIONS.yaml
├── robust-analysis/
│   ├── analysis-conventions.md    # Start here — links to detail files
│   ├── strict-execution-rules.md
│   ├── validation-checks.md
│   └── ...
├── report-generator/
│   ├── analysis-conventions.md    # Start here — workflow and section guide
│   ├── references/
│   └── assets/
└── idea-generator/
    ├── analysis-conventions.md    # Start here — ideation approach
    ├── execution-protocol.md
    ├── persona-catalog.md
    └── idea-template.md
```

### Domain packs (manual)

Use mycelium's `install-convention` mode:

1. Run `scripts/install_convention.py` specifying the convention pack name
2. The script copies conventions into `.living/conventions/[name]/`
3. `ACTIVE_CONVENTIONS.yaml` is updated to register the new pack
4. `CLAUDE.md` is updated to reference the new conventions

After installing a domain pack:

```
.living/conventions/
├── ACTIVE_CONVENTIONS.yaml
├── robust-analysis/           # core (auto-installed)
├── report-generator/          # core (auto-installed)
├── idea-generator/            # core (auto-installed)
└── bioinformatics/            # domain (manually installed)
    ├── analysis-conventions.md
    ├── statistical-conventions.md
    ├── qc-checklist.md
    └── templates/
```

## Convention Cascade

When conventions exist at multiple levels, they cascade with this priority (this applies to both core and domain packs):

```
Repo-local (.living/conventions.md)  <- highest priority
    |
Domain convention (.living/conventions/[domain]/)
    |
Core mycelium (skills/core/references/)     <- lowest priority
```

This means:
- Core conventions apply everywhere by default
- Domain packs can override core conventions for domain-specific needs
- Repo-local conventions override everything for project-specific exceptions

Document all overrides in `.living/conventions.md` with a reason.

## Requesting a New Domain

If your domain doesn't have a convention pack:

1. Use mycelium's `file-issue` mode to create a `new-domain-request` issue
2. Include: domain name, key conventions that should be covered, common workflows, and whether you'd volunteer to help create it
3. Community members or the mycelium team will respond

You can also start building conventions locally (they'll accumulate in `.living/learnings.md` and `.living/conventions.md`) and later use `crystallize` and `contribute` modes to package them into a formal convention pack.

## Multiple Convention Packs

You can install multiple convention packs. If they conflict (rare), the convention cascade applies — repo-local overrides resolve any ambiguity. Active conventions are all listed in `ACTIVE_CONVENTIONS.yaml`:

```yaml
# .living/conventions/ACTIVE_CONVENTIONS.yaml
- name: robust-analysis
  path: .living/conventions/robust-analysis/
  installed: 2024-03-10
  core: true
- name: report-generator
  path: .living/conventions/report-generator/
  installed: 2024-03-10
  core: true
- name: idea-generator
  path: .living/conventions/idea-generator/
  installed: 2024-03-10
  core: true
- name: bioinformatics
  path: .living/conventions/bioinformatics/
  installed: 2024-03-15
- name: image-analysis
  path: .living/conventions/image-analysis/
  installed: 2024-03-20
```
