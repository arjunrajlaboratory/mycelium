# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-03-07

### Changed

- **Architecture**: Separated skills (actions) from conventions (reference material)
  - Skills are Claude Code slash commands: `/mycelium:skill`, `/mycelium:analyze`, `/mycelium:report`, `/mycelium:ideas`
  - Convention packs are swappable markdown reference docs that skills route to
- **Directory structure**: `skill/` -> `skills/core/`, `network/skills/` -> `network/conventions/`
- **Convention packs**: `SKILL_PACK.yaml` -> `CONVENTION_PACK.yaml`
- **In repos**: `.living/skills/` -> `.living/conventions/`, `ACTIVE_SKILLS.yaml` -> `ACTIVE_CONVENTIONS.yaml`
- **Scripts**: `install_domain_skill.py` -> `install_convention.py`, `prepare_contribution.py` updated for conventions

### Added

- `/mycelium:analyze` — dedicated analysis skill that routes to installed analysis conventions
- `/mycelium:report` — dedicated report skill that routes to installed report conventions
- `/mycelium:ideas` — dedicated ideation skill that routes to installed idea conventions
- `marketplace.json` now registers all four skills

## [0.1.0] - 2024-01-01

### Added

- Core mycelium skill (`skill/SKILL.md`) with modes: init, ingest, analyze, report, install-skill, crystallize, contribute, file-issue
- Post-action hook protocol for living repository maintenance
- Reference documents: folder structure, environment setup, analysis conventions, statistical conventions, writing conventions, data ingestion conventions, marketplace guide, skill generation guide
- Templates: CLAUDE.md, analysis README, manifest entries, decision log, learning entry, LaTeX report, marimo notebook header, algorithm README
- Scripts (functional): init_repo, validate_structure, install_domain_skill
- Scripts (stubs, later removed): update_manifests, ingest_dataset, crystallize_learnings, prepare_contribution — these workflows are now handled agent-driven via mode definitions in core.md
- `validate_structure.py` — functional validation of mycelium repo structure
- Bioinformatics domain skill pack: RNA-seq and single-cell conventions, statistical methods, QC checklist, templates
- Image analysis domain skill pack: segmentation standards, preprocessing conventions, QC checklist, templates
- GitHub issue templates: convention gap, new domain request, skill improvement
- GitHub PR template with checklists for different contribution types
- CI workflow for validating skill packs and repo structure
