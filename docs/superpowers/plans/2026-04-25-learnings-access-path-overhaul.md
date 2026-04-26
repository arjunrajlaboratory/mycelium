# Learnings Access Path Overhaul — Implementation Plan

> **For agentic workers:** This plan is checkbox-tracked (`- [ ]`). Execute task-by-task, commit between tasks. TDD where Python is involved; smoke-test for shell.

**Goal:** Make `.living/` learnings reachable from every session via progressive disclosure, and wire up the latent mycelium features that exist but never fire.

**Architecture (3-prong attack):**
1. **Push relevant content into context** — heuristic tag-based cluster summary injected at SessionStart
2. **Make INDEX.md the canonical first hop** — CLAUDE.md tells agents to read it before anything else
3. **Provide cheap drill-down** — per-tag inverted index in INDEX.md and a `recall_lessons.py` query tool

Plus: wire up the dormant `~/.claude/knowledge/` routing layer, install the read-tracker / activity-tracker hooks by default, and provide an idempotent migration script for repos started on earlier mycelium versions.

**Tech Stack:** Python 3 (3.11+ patterns OK), bash, markdown templates, pytest.

**Backwards compat:** Existing `.living/` data formats stay valid. CLAUDE.md edits and hook installs are idempotent. Migration script is safe to re-run. Old learnings using `### [date]` headers and either `**Tags**: [a, b]` or `**Tags**: a, b` are both parsed.

---

## Phase 1 — Core access path

### Task 1: Tag-aware heuristic cluster generator

**Files:**
- Modify: `skills/core/scripts/generate_index.py` — add `--summary-heuristic` mode and tag-extraction helpers
- Test: `skills/core/scripts/test_generate_index.py` — extend with heuristic tests

**Why:** SessionStart hook only injects the `<!-- BEGIN KNOWLEDGE SUMMARY -->` block when present. The existing `--summarize` mode that produces it requires LLM calls and is never invoked. Heuristic mode runs in <1s, ships zero-config.

**Behavior:**
- New CLI flag `--summary-heuristic` (mutually exclusive with `--counts-only` and `--summarize`)
- Parse all `### [YYYY-MM-DD] Title` headers from `learnings.md` and `decisions.md`
- For each entry, pull the immediately following `**Tags**:` line (formats: `[a, b]`, `a, b`, missing)
- Group entries by tag, surface top 6 tags by entry count, ignore tags with <2 entries
- For each cluster: `- **{tag}** ({N} entries) — {comma-separated last 3 entry titles, truncated}`
- Include "Recent (last 7 days)" subsection: bare list of recent entry headers
- Wrap in `<!-- BEGIN KNOWLEDGE SUMMARY --> ... <!-- END KNOWLEDGE SUMMARY -->` sentinels
- Insert above the existing `<!-- BEGIN QUICK REFERENCE -->` block in `INDEX.md`

**Test fixtures:**
- 8 entries across 3 tags (one tag with 1 entry — should be skipped)
- Both tag formats present
- Verify deterministic output, sentinel correctness, idempotent re-run

- [ ] **Step 1:** Add `_parse_tag_line(line) -> list[str]` helper handling `[a, b]` and `a, b` formats
- [ ] **Step 2:** Add `_collect_entries(path) -> list[dict]` returning `{title, date, tags}` per entry
- [ ] **Step 3:** Add `build_heuristic_summary(living_dir) -> str` returning the full sentinel-wrapped block
- [ ] **Step 4:** Add `update_index_summary_heuristic(living_dir)` that preserves QUICK_REFERENCE and inserts/replaces SUMMARY
- [ ] **Step 5:** Add `--summary-heuristic` CLI flag and dispatch
- [ ] **Step 6:** Write tests covering: tag parsing both formats, cluster threshold (≥2), recent-window filtering, idempotent re-run, empty-living handling
- [ ] **Step 7:** Run `pytest skills/core/scripts/test_generate_index.py -v`
- [ ] **Step 8:** Commit

### Task 2: Tag inverted index in INDEX.md

**Files:**
- Modify: `skills/core/scripts/generate_index.py` — extend the SUMMARY block with a tag table

**Why:** Agents that read INDEX.md need a cheap way to know which entries match a query without pulling the whole file.

**Behavior:**
- After the cluster bullets, emit a `### By Tag` subsection
- Format: `- {tag}: L-{IDs}, D-{IDs}` — one line per tag with ≥2 entries
- IDs are derived from entry order in their respective files (1-indexed, learnings prefixed `L-`, decisions `D-`)
- The same data is what `recall_lessons.py` (Task 3) consumes

- [ ] **Step 1:** Add ID assignment to `_collect_entries` — pass through the file label (`L` or `D`)
- [ ] **Step 2:** Extend `build_heuristic_summary` with the By Tag table
- [ ] **Step 3:** Test: 5 entries across 3 tags produces correct `L-1, L-3` style references
- [ ] **Step 4:** Run tests
- [ ] **Step 5:** Commit

### Task 3: `recall_lessons.py` query tool

**Files:**
- Create: `skills/core/scripts/recall_lessons.py`
- Create: `skills/core/scripts/test_recall_lessons.py`

**Why:** Whole-file reads are expensive. This script lets agents fetch precise entries without pulling all of `learnings.md`.

**CLI:**
```
recall_lessons.py --living-dir <path> [--tag X] [--id L-042] [--since YYYY-MM-DD] [--file learnings|decisions|all] [--max N]
```

**Behavior:**
- Filter by any combination of `--tag` (multiple OK, ANY-match), `--id`, `--since`
- Default `--file all`, `--max 20`
- Print full entry blocks (header + body until next `### ` or EOF)
- Exit 0 with empty stdout if no matches; exit 2 on bad args

- [ ] **Step 1:** Reuse `_collect_entries` from generate_index by importing as a module
- [ ] **Step 2:** Implement filter chain
- [ ] **Step 3:** Implement entry-block printer (read file once, slice by header offsets)
- [ ] **Step 4:** Write tests: tag filter, ID filter, since filter, combined filters, no-match, max limit
- [ ] **Step 5:** Run tests
- [ ] **Step 6:** Commit

### Task 4: SessionStart hook regenerates SUMMARY block on demand

**Files:**
- Modify: `skills/core/hooks/mycelium-health.sh` — line ~209 area where `--counts-only` runs

**Why:** The hook checks for the SUMMARY block but currently no path produces it. Have the hook call `--summary-heuristic` whenever the block is missing or stale (>24h).

**Behavior:**
- After the `--counts-only` invocation, check if `INDEX.md` lacks `<!-- BEGIN KNOWLEDGE SUMMARY -->` OR if its last-modified is >24h
- If so, run `python3 generate_index.py --summary-heuristic --living-dir $LIVING_DIR` (silent, <1s)
- The existing injection block at line 374-383 then picks up the freshly-written block

- [ ] **Step 1:** Add a staleness/missing-block check in mycelium-health.sh
- [ ] **Step 2:** Conditionally call `--summary-heuristic`
- [ ] **Step 3:** Run a test session: trigger the hook against a fixture `.living/` dir, verify SUMMARY block injected
- [ ] **Step 4:** Commit

### Task 5: CLAUDE.md.template re-anchors on INDEX.md

**Files:**
- Modify: `skills/core/templates/CLAUDE.md.template` — Quick Orientation and Workflow sections

**Behavior:**
- Step 1 of Quick Orientation becomes: *"Read `.living/INDEX.md` first — one-screen knowledge map. Drill into the underlying file only when a row matches your task."*
- Steps 2-3 stay as-is (decisions/learnings/conventions etc.) but reframed as drill-downs
- Workflow "Before Starting Work" similarly re-anchored
- Add: *"For targeted lookup: `python3 skills/core/scripts/recall_lessons.py --tag X --living-dir .living/`"*

- [ ] **Step 1:** Edit template
- [ ] **Step 2:** Diff-check formatting
- [ ] **Step 3:** Commit

---

## Phase 2 — Knowledge layer wiring

### Task 6: `init_knowledge.py` actually appends MEMORY.md routing table

**Files:**
- Modify: `skills/core/scripts/init_knowledge.py` — add MEMORY.md backfill logic
- Test: extend or add tests

**Why:** SKILL.md claims this happens; the script has zero MEMORY.md code. Result: 15 orphaned `~/.claude/knowledge/` stub files.

**Behavior:**
- After domain files are written, walk `~/.claude/projects/*/memory/MEMORY.md`
- For each: check if `## Global Knowledge Domains` header is present; if not, append the `templates/knowledge/domain-table.md` content (verbatim minus the leading newline if needed)
- Idempotent: re-run is a no-op
- Add CLI flag `--memory-only` to do just the MEMORY.md backfill (used by Task 8 migrator)
- Print one-line summary: `MEMORY.md: appended N, skipped M (already present)`

- [ ] **Step 1:** Add `_glob_memory_files() -> list[Path]` helper
- [ ] **Step 2:** Add `_append_routing_table(memory_path, table_text) -> bool` — returns True if appended
- [ ] **Step 3:** Wire into `init_knowledge` flow
- [ ] **Step 4:** Add `--memory-only` flag
- [ ] **Step 5:** Add tests with `tmp_path` fake home dir layout
- [ ] **Step 6:** Run tests
- [ ] **Step 7:** Commit

---

## Phase 3 — Hook completeness

### Task 7: `init_repo.py` installs activity + read tracker hooks by default

**Files:**
- Modify: `skills/core/scripts/init_repo.py` — `install_claude_hooks` function (line ~327)

**Why:** Both hooks ship in `skills/core/hooks/` and are referenced by other hooks (`mycelium-stop-check.sh` reads `mycelium-session-activity.tmp`), but neither is installed by default — so file-edit-only sessions are invisible to the protocol and read-access metrics never fire.

**Behavior:**
- Add `activity-tracker` to `PostToolUse` with matcher `"Edit|Write"`
- Add `read-tracker` to `PostToolUse` with matcher `"Read"`
- Idempotent: don't duplicate if commands already present

- [ ] **Step 1:** Extend `install_claude_hooks` to register the two additional hook entries
- [ ] **Step 2:** Verify idempotency (run twice, settings.local.json doesn't grow)
- [ ] **Step 3:** Update README hook table if any
- [ ] **Step 4:** Commit

---

## Phase 4 — Migration for existing repos

### Task 8: `migrate_existing_repos.py` — idempotent backfill

**Files:**
- Create: `skills/core/scripts/migrate_existing_repos.py`

**Why:** Repos initialized on earlier mycelium versions need their CLAUDE.md re-anchored, hooks topped up, and MEMORY.md routing appended. Without this, all our improvements only help newly-init'd repos.

**CLI:**
```
migrate_existing_repos.py [--repo PATH] [--scan PATH] [--dry-run]
```

**Behavior:**
- `--repo`: migrate one repo by path
- `--scan`: walk a parent directory looking for `.living/` repos
- For each repo:
  1. **CLAUDE.md re-anchor:** if "Read `.living/INDEX.md` first" not present, insert it as step 1 of Quick Orientation (regex-based, surgical)
  2. **Hook top-up:** if `.claude/settings.local.json` exists and lacks activity-tracker or read-tracker entries, add them (preserve existing config)
  3. **INDEX.md regen:** run `generate_index.py --counts-only` then `--summary-heuristic`
  4. **MEMORY.md routing:** delegate to `init_knowledge.py --memory-only`
- Print per-repo report
- `--dry-run`: print intended changes, don't write

- [ ] **Step 1:** Implement `migrate_one(repo_path, dry_run) -> dict[str, str]` returning per-action status
- [ ] **Step 2:** Implement CLAUDE.md re-anchor regex (idempotent — match presence check)
- [ ] **Step 3:** Implement hooks merge (preserve existing entries)
- [ ] **Step 4:** Wire scan mode
- [ ] **Step 5:** Test: tmp repo with old layout, run migrator, assert all four actions, run again, assert no-op
- [ ] **Step 6:** Commit

---

## Phase 5 — Dead code cleanup

### Task 9: Wire orphan templates

**Files:**
- Modify: `skills/core/scripts/init_repo.py` — copy `analysis-readme.md` and `algorithm-readme.md` as `analysis/_README_TEMPLATE.md` and `algorithms/_README_TEMPLATE.md`
- Modify: `SKILL.md` — reference `decision-log-entry.md` in post-action protocol description
- Modify: `skills/core/references/analysis-conventions.md` (if exists) or `CLAUDE.md.template` — reference `marimo-notebook-header.py`
- Decide: `templates/knowledge/domain-header.md` — used internally? wire into init_knowledge or delete

**Behavior per template:**
- `algorithm-readme.md`, `analysis-readme.md` → copied at init as `_README_TEMPLATE.md` stubs new analyses can copy
- `decision-log-entry.md` → cited in SKILL.md mode `crystallize` and post-action protocol step 3
- `marimo-notebook-header.py` → cited in CLAUDE.md.template "Use marimo for exploration" line
- `knowledge/domain-header.md` → already used by `init_knowledge.py` via `_build_standard_content`; verify and confirm no action needed

- [ ] **Step 1:** Update init_repo.py to drop README templates
- [ ] **Step 2:** Update SKILL.md and CLAUDE.md.template references
- [ ] **Step 3:** Verify domain-header.md usage with grep
- [ ] **Step 4:** Commit

### Task 10: Honest SKILL.md — mark dormant modes

**Files:**
- Modify: `SKILL.md`

**Why:** `transfer`, `contribute`, `file-issue` either need a meta-project layout (transfer) or are manual user-driven workflows that should be documented as such, not implied to fire automatically.

**Behavior:**
- Add a "Dormant by design" badge to those mode headers with a one-line caveat
- Add new `recall` mode pointing at `recall_lessons.py`
- Add note to `crystallize-findings` mode that `--summary-heuristic` runs automatically
- Add a "How to verify" subsection at the bottom: how to check that hooks are firing, summary block exists, etc.

- [ ] **Step 1:** Edit SKILL.md
- [ ] **Step 2:** Commit

---

## Phase 6 — Ship

### Task 11: CHANGELOG, push, PR

**Files:**
- Modify: `CHANGELOG.md`
- New: PR description

- [ ] **Step 1:** Add CHANGELOG entry summarizing all changes by phase
- [ ] **Step 2:** Run final test suite: `pytest skills/core/scripts/`
- [ ] **Step 3:** Run migrator in dry-run mode against `~/code/SNP-tree/`, `~/code/Autonomous-Science/`, `~/code/GBM2/` and review output (don't apply — that's the user's call)
- [ ] **Step 4:** Commit changelog
- [ ] **Step 5:** Push branch
- [ ] **Step 6:** `gh pr create` with structured body

---

## Self-review checklist

- [ ] Spec coverage: each of the 4 originally-identified fixes maps to ≥1 task (Fix 1→T5, Fix 2→T1+T4, Fix 3→T2+T3, Fix 4→T6+T8)
- [ ] Unreachable paths: read-tracker (T7), activity-tracker (T7), `--full` regen path (T1 supersedes via heuristic), MEMORY.md append (T6+T8), orphan templates (T9), dormant modes (T10)
- [ ] Backwards compat: T8 migrator brings old repos forward; all edits are idempotent
- [ ] No placeholders: every task has concrete files and behavior described
