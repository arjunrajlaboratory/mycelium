# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2024-01-01

### Added

- Core mycelium skill (`skill/SKILL.md`) with modes: init, ingest, analyze, report, install-skill, crystallize, contribute, file-issue
- Post-action hook protocol for living repository maintenance
- Reference documents: folder structure, environment setup, analysis conventions, statistical conventions, writing conventions, data ingestion conventions, marketplace guide, skill generation guide
- Templates: CLAUDE.md, analysis README, manifest entries, decision log, learning entry, LaTeX report, marimo notebook header, algorithm README
- Scripts (stubs): init_repo, update_manifests, ingest_dataset, validate_structure, install_domain_skill, crystallize_learnings, prepare_contribution
- `validate_structure.py` — functional validation of mycelium repo structure
- Bioinformatics domain skill pack: RNA-seq and single-cell conventions, statistical methods, QC checklist, templates
- Image analysis domain skill pack: segmentation standards, preprocessing conventions, QC checklist, templates
- GitHub issue templates: convention gap, new domain request, skill improvement
- GitHub PR template with checklists for different contribution types
- CI workflow for validating skill packs and repo structure
