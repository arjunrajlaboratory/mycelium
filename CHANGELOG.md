# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **`report-generator` convention pack (0.2.0 → 0.3.0): worked-example provenance, audience tiers, narrative-vs-structured Results, and shape-budget check.** Surfaced from comparing the v0.2.0 baseline output against the legacy report on a real A191 analysis. Six follow-ups: (1) Phase 1 manifest gains a `worked_examples[]` section with row-level provenance so Phase 6 can catch confabulated worked-example values (the failure mode the v0.2.0 worked-example gate could not detect — presence was enforced, contents were not); (2) Phase 0 gains an audience-tier ladder (A lay / B adjacent-field default / C in-field PI), recorded in `manifest.policies.acronym_*` and `intuition_leadin_default_form`, which the Phase 4 plain-English lint reads to modulate strictness; (3) Phase 0 gains a Results-structure question (narrative default vs structured Q/F/I headers), recorded in `manifest.policies.results_structure`; (4) Phase 5 framing critique gains an explicit subsection-title-states-finding test (topic-only Results titles get flagged); (5) Phase 6 numerical re-verify gains a Provenance-section completeness check that lists analysis scripts and flags any missing from the report's Provenance; (6) Phase 7 records main-text page count and flags shape-budget overruns (overview > 6 pages; overview+supplement > 14 pages; comprehensive < 5 pages) in `.compile-log.md`. Manifest example extended with `policies`, `worked_examples`, and a failure-mode worked example for the supplement.

- **`report-generator` convention pack (0.1.0 → 0.2.0): phase-based agentic redesign.** The old 5-step procedural flow (gather context → copy template → fill sections → compile → verify) is replaced by a 9-phase flow that orchestrates the work and pushes most of the review burden onto three blind sub-agents. The user is in the loop only at Phase 0 (planning brief — headline question, baseline of comparison, primary metric, audience, standalone-vs-addendum, report shape) and optionally Phase 8 (headline preview). Internal phases: 0.5 memory consultation (reads `.living/` and emits a names+concepts cheatsheet); 0.75 section outline + main/supplement designation; 1 source-of-truth manifest (every number and coined term registered with provenance); 2 draft (sources values from the manifest, terms from the glossary); 3 worked-example gate (one healthy example per new aggregation analysis type in main text; failure-mode examples in supplement); 4–6 blind sub-agent reviewers for plain-English/glossary lint, framing critique, and numerical re-verify with provenance/style split (mirroring `mycelium:review`'s synthesis structure); 7 recompile with PDF sha + reviewer verdicts in `.compile-log.md`. Three template variants: `report-template-overview.tex`, `report-template-comprehensive.tex`, `report-template-overview-supplement.tex` (default). Sub-agent prompts live in `references/phase-prompts.md`; cross-cutting craft (acronym discipline, intuitive-before-technical, worked-examples, denominator discipline, overloaded-name guard) lives in `references/section-guide.md`; QC checklist restructured into provenance/style sections.

### Fixed

- **Migrator no longer creates duplicate hook entries** when the same script is already registered at a different path (e.g. marketplace install vs. dev-repo checkout). `install_claude_hooks` in `init_repo.py` now matches existing hooks by script *basename* (`mycelium-health.sh`, etc.), not full command path. A new pre-pass also consolidates pre-existing duplicates, preferring the marketplace path. The pre-pass also detects entries whose command path no longer exists on disk (stale install dirs) and replaces them with a fresh path — but only when a known-good replacement is available, so transient filesystem hiccups don't make a bad situation worse. Run the migrator on a previously-migrated repo to clean up. New `_consolidate_duplicate_hooks` helper + 10 unit tests.

### Added

- **Heuristic INDEX.md summary** (`generate_index.py --summary-heuristic`): Tag-aware clustering that produces a `<!-- BEGIN KNOWLEDGE SUMMARY -->` block in <1s without LLM calls. Three subsections — tag clusters (≥2 entries), 10 most-recent entries, and a tag → entry-ID inverted index. Closes the gap that left every existing `INDEX.md` sentinel-less and the SessionStart injection path silently dead.
- **`recall_lessons.py`** — query `.living/learnings.md` and `decisions.md` by tag, ID, or date. Cheap progressive-disclosure tool: fetches matching entries instead of pulling whole files into context. Supports ANY-match within `--tag` and `--id`, AND across filter types.
- **`migrate_existing_repos.py`** — idempotent backfill for repos started on earlier mycelium versions. Re-anchors CLAUDE.md on `.living/INDEX.md`, tops up missing hooks (especially read-tracker), regenerates the heuristic SUMMARY block, and appends the Global Knowledge Domains routing table to MEMORY.md. Supports `--repo`, `--scan`, `--dry-run`.
- **CLAUDE.md.template re-anchor**: New repos point at `.living/INDEX.md` as the *first* knowledge entry point and explicitly mention `recall_lessons.py` for targeted lookup. Old "read learnings.md / decisions.md directly" pattern is dropped.
- **MEMORY.md routing table append in `init_knowledge.py`**: SKILL.md previously claimed this happened, but the script had no MEMORY.md code. Now actually appends the table to `~/.claude/projects/*/memory/MEMORY.md` (idempotent — checks for `## Global Knowledge Domains` header). New `--memory-only` flag for the migrator.
- **`recall` and `migrate` modes documented in SKILL.md**, plus a "How to verify" section listing the seven commands an agent can run to confirm the system is wired correctly.

### Fixed

- **`mycelium-read-tracker.sh` is now installed by default** in `init_repo.py`'s 5-hook bundle. Previously the hook shipped but had to be installed manually per project — meaning `.living/` access metrics required ad-hoc setup. The bundle now is: SessionStart→health, PostToolUse(Bash)→post-action, PostToolUse(Edit\|Write)→activity-tracker, PostToolUse(Read)→read-tracker (new default), Stop→stop-check.
- **SessionStart hook calls `--summary-heuristic` by default**, with fallback to `--counts-only` if the local copy of `generate_index.py` predates the new flag. Previously the hook only called `--counts-only`, which never produces a SUMMARY block — leaving the injection path at line ~376 of `mycelium-health.sh` permanently dead for every project on the machine.

### Changed

- **SKILL.md honest about dormant modes**: `transfer` (requires meta-project layout), `contribute` (requires prior `crystallize`), and `file-issue` (manual workflow only) now carry "Dormant by design" callouts explaining what triggers them and what does not. Prevents agents from inferring they fire automatically.

### Wired (previously orphan templates)

- `algorithm-readme.md` and `analysis-readme.md` are now copied as `_README_TEMPLATE.md` into `algorithms/` and `analysis/` at init.
- `decision-log-entry.md` and `learning-entry.md` are now cited by name in the `.living/decisions.md` and `.living/learnings.md` stub content. The learnings stub also notes that `**Tags**:` annotations feed `--summary-heuristic`.
- `marimo-notebook-header.py` is now referenced in the CLAUDE.md.template Workflow section.

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
