---
description: >
  Self-documenting, self-improving analytical repository framework.
  Scaffolds living repositories where every analysis, dataset, and decision
  is registered and discoverable. Use when user says "set up mycelium",
  "initialize living repo", "ingest dataset", "install convention",
  "crystallize learnings", "todo idea", "knowledge init",
  "set up knowledge", "progressive disclosure", "transfer knowledge",
  "cross-pollinate", or wants to restructure an existing repository into a
  self-documenting framework. Also use when user mentions "living dataset",
  "living repo", or "mycelium".
---

# Mycelium — Living Repository Skill (Core)

When invoked, determine which mode the user needs based on their request, then follow the corresponding protocol.

For analysis, report generation, and idea brainstorming, direct the user to the dedicated skills:
- `/mycelium:analyze` — start or continue an analysis
- `/mycelium:report` — generate a structured report
- `/mycelium:ideas` — brainstorm with disciplinary personas
- `/mycelium:transfer` — cross-pollinate learnings across sibling projects

---

## Mode: `init`

**Trigger**: "set up mycelium", "initialize living repo", "restructure this repo"

**Purpose**: Scaffold a new or existing repo into a mycelium-enabled living repository.

**Steps**:
1. Check if repo already has mycelium structure (look for `.living/` directory).
2. **New repo**: Run full scaffold using `skills/core/scripts/init_repo.py`.
3. **Existing repo**: Run in restructure mode — audit current structure, propose migration plan, ask user to confirm before proceeding.
4. **Auto-install core convention packs**: Scan `network/conventions/*/CONVENTION_PACK.yaml` for packs with `core: true` and install each one using `skills/core/scripts/install_convention.py`. Currently the core packs are `robust-analysis`, `report-generator`, and `idea-generator`. These provide batteries-included practices every repo should have.
5. Ask which **domain** conventions to install from the network (e.g., bioinformatics, image-analysis). Domain packs are those without `core: true`.
6. Generate `CLAUDE.md` for the repo (from `skills/core/templates/CLAUDE.md.template`) that encodes the living repo protocol.
7. Generate `ENVIRONMENTS_INSTALLATIONS.md` at repo root.
8. Create descriptive manifests in each top-level directory (`ANALYSIS_MANIFEST.md`, `DATA_MANIFEST.md`, `ALGORITHM_MANIFEST.md`, `REFERENCE_MANIFEST.md`).
9. Initialize `.living/` with empty `decisions.md`, `learnings.md`, `conventions.md`; create `.living/log/LOG_REGISTRY.md` (session log registry — tracks work across sessions). Create `.living/outputs/knowledge-transfers/` for cross-project transfer audit trail.
10. **Bootstrap knowledge system**: If `~/.claude/knowledge/` does not exist, run `skills/core/scripts/init_knowledge.py` to set up the global progressive disclosure knowledge system. Generate `.living/INDEX.md` for the newly scaffolded project using `skills/core/scripts/generate_index.py`. Append the domain routing table to the project's MEMORY.md if not already present (from `skills/core/templates/knowledge/domain-table.md`).
11. Create `todo/` directory with `TODO_REGISTRY.md` (registry table) and `TODO_ITEM_TEMPLATE.md` (template for individual items). Copy these from the mycelium `todo/` directory.
12. After completion: run `skills/core/scripts/validate_structure.py` to confirm everything is correct.

**References to consult**:
- `skills/core/references/folder-structure.md` — canonical target structure
- `skills/core/references/environment-setup.md` — environment setup conventions

---

## Mode: `ingest`

**Trigger**: "ingest dataset", "add data", "import data"

**Purpose**: Pull a new dataset into the analytical framework.

**Steps**:
1. Consult `skills/core/references/data-ingest-conventions.md`.
2. Determine data type, source, and format.
3. If a domain convention is active, check its conventions for domain-specific validation.
4. Place raw data in `data/raw/[dataset-name]/`.
5. Generate metadata in `data/metadata/[dataset-name]/` using templates: `skills/core/templates/schema.yaml` for column definitions, `skills/core/templates/provenance.md` for source documentation, and `skills/core/templates/summary_stats.md` for statistical overview.
6. Update `data/DATA_MANIFEST.md` with new entry (use `skills/core/templates/dataset-manifest-entry.yaml`).
7. Log any decisions about data cleaning or exclusion to `.living/decisions.md`.
8. Run the post-action hook protocol (see below).

---

## Mode: `install-convention`

**Trigger**: "install convention", "add bioinformatics conventions", "install convention pack"

**Purpose**: Install a convention pack from the mycelium network into the current repo.

**Context**: Core packs (`robust-analysis`, `report-generator`, `idea-generator`) are auto-installed during `init`. This mode is primarily for adding domain packs after initialization, but can also be used to manually install or reinstall any pack.

**Steps**:
1. Consult `skills/core/references/marketplace-guide.md`.
2. List available conventions from `network/conventions/`, noting which are core (already installed) and which are domain (available to add).
3. User selects which to install.
4. Run `skills/core/scripts/install_convention.py` to copy conventions into `.living/conventions/[name]/`.
5. Update `.living/conventions/ACTIVE_CONVENTIONS.yaml`.
6. Update `CLAUDE.md` to reference new convention pack.

---

## Mode: `crystallize`

**Trigger**: "crystallize learnings", "review learnings", "extract patterns"

**Purpose**: Review accumulated learnings and propose new conventions.

**Steps**:
1. Consult `skills/core/references/skill-generation-guide.md`.
2. Consult `skills/core/templates/learning-entry.md` for the entry format used in learnings.md (entries start with `## [YYYY-MM-DD]` and have **Category**, **Tags** fields — use Tags for pattern clustering).
3. Read `.living/learnings.md` and `.living/decisions.md`.
4. Identify recurring patterns using the thresholds defined in `skill-generation-guide.md` — minimum 3 related entries sharing tags or themes. See the worked example in that guide for the complete input→output transformation.
5. **Promote transferable knowledge**: For patterns that apply beyond the current project, write entries to the matching global domain file in `~/.claude/knowledge/{domain}.md` using the structured entry template (What/Evidence/When useful/Scope/Status/Last validated). Set `status: unreviewed` — the weekly audit will confirm.
6. Propose new convention documents or checklists for project-specific patterns.
7. Draft them in `.living/generated-conventions/[name]/` using the template at `skills/core/templates/generated-convention.md`.
8. Include `ORIGIN.md` linking back to the learnings that spawned it.
9. Ask user if they want to contribute it back to the network.

---

## Mode: `crystallize-findings`

**Trigger**: "crystallize findings", "extract findings", "log findings"

**Purpose**: Extract scientific findings from recent work and organize them into topic-based files.

**Steps**:
1. Dispatch a **sonnet** subagent (max_turns: 15) with the following instructions:
2. Read the current project's recent session log (`.living/log/` most recent file) and any generated analysis outputs.
3. Identify scientific findings — empirical observations, validated/invalidated hypotheses, quantitative results, or methodological discoveries about the domain. **NOT** tooling/process insights (those go to `learnings.md`).
4. For each finding, check for existing topics:
   a. Walk up from repo root to find meta-project (parent dir with `.living/`).
   b. Read `{meta-project}/.living/findings/INDEX.md` if it exists.
   c. Semantically match the finding against existing topics.
5. Route each finding:
   - **Existing topic match** → append to `{project}/.living/findings/{topic-slug}.md` using the entry template from `skills/core/templates/findings-entry.md`.
   - **No match** → create new `{project}/.living/findings/{topic-slug}.md` using the topic template from `skills/core/templates/findings-topic.md`.
6. **Topic naming principle**: Prefer broad scientific questions. No project names, organ names, species names, or method names in slugs. Test: "Would a researcher in a different system recognize this as relevant?"
7. **After writing each finding**, upsert a row in `.living/findings/FINDINGS_REGISTRY.md`. If the file doesn't exist, create it from `skills/core/templates/findings-registry.md`. Match on finding ID (F-NNN) — update the existing row if the ID is already present, or append a new row. Columns: ID, Claim, Status, Topic (link to topic file), Implications, Tags, Last Updated.
8. Run `python3 skills/core/scripts/crystallize_findings.py` to rebuild the cross-project INDEX.md and regenerate all per-project FINDINGS_REGISTRY.md files from the topic file source of truth.
9. **Consolidation pass** (if invoked explicitly): Scan all topics across all projects, flag potential duplicates (overlapping tags or similar descriptions), and suggest merges to the user.
10. Return single-line summary: "Added N findings to M topics (N new topics created)."

---

## Mode: `transfer`

**Trigger**: "transfer knowledge", "cross-pollinate", "sync learnings", "knowledge transfer"

**Purpose**: Cross-pollinate learnings across sibling projects in a meta-project. Identifies insights from one project that would benefit others and **automatically applies** them to target projects' `.living/learnings.md` files.

**Steps**:
1. This mode delegates to the dedicated command file. Run the protocol in `commands/transfer.md`.
2. The transfer command is designed to run as a background subagent — dispatch it with `sonnet` model and do not block on results.
3. Transfers are auto-applied to target projects' `.living/learnings.md` with `source: {project} (auto-transferred by mycelium)` for auditability.
4. Audit reports are written to `{meta-project}/.living/outputs/knowledge-transfers/YYYY-MM-DD.md`.

**When this runs**:
- **Automatic**: `mycelium-health.sh` checks `.living/outputs/knowledge-transfers/.last-run` at session start. If >24h old or missing, it emits a directive to dispatch the transfer as a background subagent.
- **Manual**: User invokes `/mycelium:transfer` or says "transfer knowledge", "cross-pollinate".

**Lifecycle position**: This phase sits between crystallization (patterns → conventions) and contribution (conventions → network). Crystallize extracts patterns within a project; transfer propagates relevant patterns across projects; contribute shares generalized patterns with the network.

**Notes**:
- Transfers are context-adapted for the target project, not copy-pasted verbatim
- Near-duplicate entries in the target are skipped automatically
- "All clear" reports (no transfers needed) are normal and expected most days

---

## Mode: `contribute`

**Trigger**: "contribute convention", "share back", "submit to network"

**Purpose**: Package a repo-local generated convention for PR to the mycelium network.

**Steps**:
1. Verify the generated convention exists in `.living/generated-conventions/[name]/` — if not, run `crystallize` mode first.
2. Read the convention and its `ORIGIN.md` provenance document.
3. **Generalize** the convention for broader use:
   - Replace repo-specific paths, dataset names, and project references with parameterized placeholders
     (e.g., `/home/user/hospital-x/data/` → `[data-source-path]`, "Hospital X CSV exports" → "external tabular data sources")
   - Abstract project-specific context while preserving the core convention
   - Ensure examples are generic enough to apply outside the original project
   - See the encoding validation example in `skills/core/references/skill-generation-guide.md` for reference
4. **Expand into pack structure** if the convention is complex:
   - Simple conventions: the generated convention.md becomes `analysis-conventions.md` directly
   - Complex conventions: split into a hub file (`analysis-conventions.md`) that links to detail files covering specific aspects
   - See `network/conventions/robust-analysis/` for an example of a multi-file pack
5. Create the convention pack directory at `network/community-contributed/[name]/`:
   - `CONVENTION_PACK.yaml` — use template at `skills/core/templates/convention-pack.yaml`
   - `analysis-conventions.md` — the generalized convention (hub file with progressive disclosure)
   - `qc-checklist.md` — quality control checklist for applying the convention correctly
   - Additional reference files for complex conventions (e.g., `statistical-conventions.md`)
   - `templates/` — reusable templates if applicable
6. Generate a PR description with:
   - What the convention covers and why it's useful
   - Anonymized provenance (number of learnings, time span, but no project names)
   - Which domains or workflows benefit
7. Present the pack to the user for review before committing.

---

## Mode: `file-issue`

**Trigger**: "file issue", "report convention gap", "report bug in convention"

**Purpose**: Report a convention gap or error to the mycelium network.

**Steps**:
1. Capture context: what went wrong, what convention was involved.
2. Draft a structured GitHub issue using the appropriate template.
3. Categorize as `convention-gap`, `convention-improvement`, or `new-domain-request`.
4. Present to user for review before filing.

---

## Mode: `todo-idea`

**Trigger**: "todo idea", "add todo", "I have an idea", "track this for later", "/todo-idea"

**Purpose**: Capture a future work item, idea, or planned improvement in the project's `todo/` directory.

**Steps**:
1. Ask the user (if not already provided):
   - **Title**: Brief name for the item
   - **Description**: What is this about?
   - **Priority**: critical, high, medium, low, or idea (default: idea)
   - **Category**: e.g., validation, feature, refactor, analysis, infrastructure
   - **Motivation**: Why is this worth doing?
2. Generate a kebab-case filename from the title (e.g., "Compare Public Data" -> `compare-public-data.md`).
3. Create `todo/[filename].md` using the template at `todo/TODO_ITEM_TEMPLATE.md`. Fill in all fields from user input. Set status to `open`. Set date to today. Set author to the user's name (ask if unknown).
4. Add a row to the registry table in `todo/TODO_REGISTRY.md` with: item title, priority, status, category, date, author, and a link to the file.
5. Confirm the item was added and show the user the registry entry.

**Notes**:
- If `todo/` or `todo/TODO_REGISTRY.md` doesn't exist yet, create them first (use the structure from the mycelium `todo/` directory as reference).
- Users can also update existing items by asking to change their status or priority — edit both the item file and registry row.

---

## Mode: `knowledge-init`

**Trigger**: "knowledge init", "set up knowledge", "initialize knowledge system", "progressive disclosure"

**Purpose**: Bootstrap the global progressive disclosure knowledge system (`~/.claude/knowledge/`).

**Steps**:
1. Run `skills/core/scripts/init_knowledge.py` to create domain files from templates. Existing files are preserved (idempotent).
2. For each project with a `.living/` directory: run `skills/core/scripts/generate_index.py` to create/update `.living/INDEX.md`.
3. Check each project's MEMORY.md (in `~/.claude/projects/*/memory/`). If missing the "Global Knowledge Domains" table, append it from `skills/core/templates/knowledge/domain-table.md`.
4. Verify the knowledge system is functional: check `~/.claude/knowledge/.last-audit` exists, confirm domain file count, report summary.

**Notes**:
- This mode is **global** — it sets up `~/.claude/knowledge/` which is shared across all projects.
- Safe to run multiple times. Existing domain files and their entries are never overwritten.
- The weekly audit (triggered by `mycelium-health.sh`) handles ongoing maintenance: staleness checks, INDEX.md regeneration, skills sync, dedup.
- Domain files start empty (header + trigger only). Entries accumulate through the post-action protocol's knowledge promotion step and the crystallize mode.

**References to consult**:
- `skills/core/templates/knowledge/domains.yaml` — domain registry (single source of truth for domain definitions)
- `skills/core/templates/knowledge/domain-table.md` — MEMORY.md routing table template
- `skills/core/templates/knowledge/entry-template.md` — entry format reference

---

## Post-Action Hook Protocol

**This is what makes the repo "living." Execute after ANY significant action** (analysis step, data ingestion, algorithm implementation, report generation):

1. **Update manifests**: Update the relevant manifest (`ANALYSIS_MANIFEST.md`, `DATA_MANIFEST.md`, etc.) with new/changed entries.
2. **Update documentation**: Update or create the UPPER_SNAKE_CASE.md file in the affected subfolder with current status, key findings, open questions.
3. **Log decisions**: If a non-obvious choice was made, append to `.living/decisions.md` using the decision log template.
4. **Log learnings**: If something unexpected was learned (gotcha, edge case, failure), append to `.living/learnings.md` using the learning entry template. **Knowledge promotion**: If the learning is transferable (would help in any project), also append to the matching global domain file in `~/.claude/knowledge/{domain}.md` with `status: unreviewed`. Use the structured entry template with a `when_useful` trigger condition.
5. **Log todos**: If future work is identified during the action, add items to `todo/TODO_REGISTRY.md` (and create detailed `todo/[item].md` files for complex items).
6. **Validate**: Run `skills/core/scripts/validate_structure.py` to confirm repo still conforms.
7. **Convention feedback**: If any convention pack practices were relevant, note whether they were helpful or had gaps.
8. **Write session summary**: Write or update `.claude/last-session.md` with a 5-section summary covering ALL work since session start. Use the session summary template:

   ```markdown
   SESSION RESUME — Last session (YYYY-MM-DD HH:MM):

   ## What was worked on
   - [Semantic summary of accomplishments — what was built/fixed/analyzed, not file lists]

   ## Key decisions made
   - [Decision]: [rationale] (see .living/decisions.md for full context)

   ## Blockers & surprises
   - [Resolved/Unresolved]: [what happened, resolution or current status]

   ## Current state
   - Branch: X | Tests: N passing | [environment notes]
   - [Key metrics, uncommitted changes, data state]

   ## Next steps
   - [Actionable items with specific commands where relevant]
   ```

   **Full-session coverage**: Run `git log --since=<session-start-timestamp>` and `git diff --stat` to capture all work since session start. If crystallization fires multiple times in a session, each write rebuilds the summary covering the entire session (cumulative, not incremental). The summary should get more expansive as the session progresses.

### Automated Enforcement (Claude Code Hooks)

Mycelium ships optional hook scripts in `hooks/` that enforce the post-action protocol automatically:

| Hook | Event | Purpose |
|------|-------|---------|
| `mycelium-health.sh` | SessionStart | Loads `.claude/last-session.md` for session resume (agent + user); warns if `.living/` is missing or incomplete; records session timestamp; triggers weekly knowledge audit if `~/.claude/knowledge/.last-audit` is >7 days old |
| `mycelium-post-action.sh` | PostToolUse (Bash) | Detects code execution and directs Claude to run the full post-action protocol |
| `mycelium-stop-check.sh` | Stop | Checks `.living/` was updated after significant work; warns if session summary (`.claude/last-session.md`) was not written |

**Post-action enforcement**: The PostToolUse hook detects Python/R/Jupyter execution in Bash calls (excluding tests, linting, pip, one-liners) and injects a mandatory directive for Claude to execute the full post-action protocol — saving outputs, updating manifests, and logging to `.living/`. It is **debounced**: fires once per work cycle, then stays silent until `.living/` is updated (completing the cycle). This means Claude receives exactly one directive per burst of analysis work, not one per Bash call. The Stop hook serves as a safety net for non-analysis sessions.

**Stop hook logic**: The stop hook checks if `mycelium-post-action.sh` fired during the session (indicated by the presence of `.claude/mycelium-reminded.tmp`). If `.living/` was not updated afterward, it warns. If `.living/` was updated but `.claude/last-session.md` was not written (or is older than the session start), it emits a non-blocking warning reminding you to write the session summary. Read-only sessions, config-only sessions, and sessions without code execution are never checked.

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
            "command": "/path/to/mycelium/skills/core/hooks/mycelium-health.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/mycelium/skills/core/hooks/mycelium-post-action.sh"
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
            "command": "/path/to/mycelium/skills/core/hooks/mycelium-stop-check.sh"
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
2. **After all subagent batches complete**, the main context dispatches a crystallization subagent that:
   - Reviews the summary of what was accomplished
   - Appends entries to `.living/learnings.md` and `.living/decisions.md`
   - Writes `.claude/last-session.md` using the 5-section session summary template (covering ALL work since session start, not just the latest batch — run `git log --since=<session-start-ts>` and `git diff --stat` to ground the summary in facts)
   - Checks cross-project relevance (if applicable)
3. **The Stop hook enforces this** — it blocks session end if `.living/` wasn't updated after significant work, catching sessions where the crystallization step was forgotten.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `validate_structure.py` fails after init | Check that all four top-level directories exist and each has its manifest (`ANALYSIS_MANIFEST.md`, etc.). Re-run init if needed. |
| Domain conventions conflict with core | Domain conventions take precedence. Update `.living/conventions.md` to document the override. |
| `.living/` directory missing | Run `init` mode to scaffold the living layer. Never create it manually — the script sets up required files. |
| Manifest entry format errors | Check `skills/core/templates/dataset-manifest-entry.yaml` or `skills/core/templates/analysis-manifest-entry.yaml` for the correct format. |
| `ENVIRONMENTS_INSTALLATIONS.md` not found | Run `init` mode or create it manually following `skills/core/references/environment-setup.md`. |
| Crystallize finds no patterns | This is normal for young repos. Continue logging learnings and decisions — patterns emerge over time. |
| Install-convention can't find a domain | Check `network/conventions/` for available packs. File a `new-domain-request` issue if your domain isn't covered. |
