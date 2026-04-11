# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-04-11

### Added

- **Knowledge transfer lifecycle** (#23): Cross-project knowledge transfer as a native lifecycle phase. New `/mycelium:transfer` command cross-pollinates learnings across sibling projects in a meta-project, with auto-application and audit trail. Lifecycle is now: accumulate → crystallize → transfer → contribute.
- **Skill trigger description overhaul** (#25): Rewrote all 5 skill descriptions for semantic intent matching. Recall jumped from 0–20% to 80–100% across skills, with 90–100% specificity.
- **Knowledge promotion pipeline** (#24): Post-action hook and audit now promote transferable learnings from `.living/learnings.md` to global `~/.claude/knowledge/{domain}.md` files. Audit cadence moved from weekly to daily.

## [0.4.0] - 2026-04-04

### Added

- **Read-path telemetry** (#22): `mycelium-read-tracker.sh` hook logs every `.living/` file access for consumption measurement.
- **INDEX.md LLM summarization** (#22): `generate_index.py --summarize` generates knowledge cluster summaries; `--counts-only` mode for fast refresh at session start.
- **INDEX.md injection at SessionStart** (#22): Health hook injects knowledge summary clusters directly into agent context — no manual INDEX.md reading required.
- **LOG_REGISTRY semantic summaries** (#22): Stop hook instructs Claude to fill Summary and Key Outputs columns with meaningful content instead of filename stubs.
- **Universal conventions crystallization** (#22): Post-action protocol now includes a dedicated step to crystallize recurring learnings (3+) into `.living/conventions.md` with source citations.

### Fixed

- **Stop hook debounce** (#22): 5-minute debounce before blocking session end prevents premature blocks on quick sessions.

## [0.3.2] - 2026-03-28

### Fixed

- **Two-phase report write order** (#21): Reports now enforce data-driven sections first (Problem Statement, Methods, Results), then interpretive sections (Conclusions, Abstract, Next Steps) — prevents conclusions templated from hypotheses before results are read.
- **Stop hook verbosity** (#20): Replaced 30-line inline triage instructions with 1–2 line signal messages. Full protocol lives in the skill definition.

## [0.3.1] - 2026-03-24

### Added

- **Edit/Write activity tracker** (#18): `mycelium-activity-tracker.sh` hook tracks file modifications (not just Bash execution), closing a blind spot where Edit/Write-only sessions went unrecorded.
- **Auto session log finalization** (#18): Stop hook auto-finalizes session logs with computed duration, files changed, and registry row — no Claude compliance needed.
- **Blocking stop hook enforcement** (#18): Stop hook now uses `{"decision": "block"}` JSON instead of non-blocking warnings.

### Fixed

- **8 hook reliability gaps** (#19): Global session log pointer clobbering, unbounded activity file, over-broad `python -m` exclusion, fragile path derivation, session timestamp leak, stale sentinel persistence, dead reminder check.
- **Convention/findings enforcement** (#19): `conventions.md` added to mtime validation; prescriptive triage routing replaces weak "or" phrasing; findings directory enforcement added. Audit of 358-entry learnings.md revealed 19.6% scientific findings and 13.4% conventions misrouted to learnings.

## [0.3.0] - 2026-03-20

### Added

- **Scientific findings crystallization** (#16): Topic-organized findings in `.living/findings/{topic}.md` with evidence ledgers, status tracking (preliminary → supported → robust → contradicted), FINDINGS_REGISTRY, and cross-project indexing via `crystallize_findings.py`.
- **Incremental session logger** (#15): Hook-driven running log throughout each session, persisted in `.living/log/` with LOG_REGISTRY. SessionStart creates log files, PostToolUse injects append directives, Stop auto-cleans short sessions.
- **Cross-session continuity** (#14): Crystallization writes structured 5-section session summary to `.claude/last-session.md`; SessionStart hook loads it for both agent and user; Stop hook warns if not written.
- **Progressive disclosure knowledge system** (#13): Three-tier system with global `~/.claude/knowledge/` domain files, per-project INDEX.md summaries, and MEMORY.md routing tables. Weekly silent audit for staleness, dedup, and regeneration. `init_knowledge.py` and `generate_index.py` scripts.
- **PostToolUse hook for post-action enforcement** (#12): `mycelium-post-action.sh` detects code execution and injects mandatory post-action protocol directives. Debounced per work cycle. Stop hook rewritten to complement (fires only when post-action was ignored).
- **Spot-check and failure-mode-analysis conventions** (#11): Two new reference docs in robust-analysis pack for outlier investigation and algorithm failure characterization.
- **Agent-driven workflow templates** (#17): 5 new templates (`generated-convention.md`, `convention-pack.yaml`, `schema.yaml`, `provenance.md`, `summary_stats.md`). Crystallization guide enriched with worked example and explicit thresholds. Contribute mode rewritten as fully agent-driven. 4 non-functional stub scripts removed.

### Changed

- **Init auto-registers all hooks** (#12): `init_repo.py` registers SessionStart, PostToolUse, and Stop hooks in `.claude/settings.local.json` during initialization.

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
