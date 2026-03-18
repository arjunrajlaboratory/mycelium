---
description: >
  Self-documenting, self-improving analytical repository framework.
  Scaffolds living repositories where every analysis, dataset, and decision
  is registered and discoverable. Use when user says "set up mycelium",
  "initialize living repo", "ingest dataset", "install convention",
  "crystallize learnings", "todo idea", or wants to restructure an existing
  repository into a self-documenting framework. Also use when user mentions
  "living dataset", "living repo", or "mycelium".
---

# Mycelium — Living Repository Skill (Core)

When invoked, determine which mode the user needs based on their request, then follow the corresponding protocol.

For analysis, report generation, and idea brainstorming, direct the user to the dedicated skills:
- `/mycelium:analyze` — start or continue an analysis
- `/mycelium:report` — generate a structured report
- `/mycelium:ideas` — brainstorm with disciplinary personas

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
9. Initialize `.living/` with empty `decisions.md`, `learnings.md`, `conventions.md`.
10. Create `todo/` directory with `TODO_REGISTRY.md` (registry table) and `TODO_ITEM_TEMPLATE.md` (template for individual items). Copy these from the mycelium `todo/` directory.
11. After completion: run `skills/core/scripts/validate_structure.py` to confirm everything is correct.

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
5. Generate metadata (schema, provenance, summary statistics) in `data/metadata/[dataset-name]/`.
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
2. Read `.living/learnings.md` and `.living/decisions.md`.
3. Identify recurring patterns (look for similar tags, repeated problems, evolving conventions).
4. **Promote transferable knowledge**: For patterns that apply beyond the current project, write entries to the matching global domain file in `~/.claude/knowledge/{domain}.md` using the structured entry template (What/Evidence/When useful/Scope/Status/Last validated). Set `status: unreviewed` — the weekly audit will confirm.
5. Propose new convention documents or checklists for project-specific patterns.
6. Draft them in `.living/generated-conventions/[name]/`.
7. Include `ORIGIN.md` linking back to the learnings that spawned it.
8. Ask user if they want to contribute it back to the network.

---

## Mode: `contribute`

**Trigger**: "contribute convention", "share back", "submit to network"

**Purpose**: Package a repo-local generated convention for PR to the mycelium network.

**Steps**:
1. Run `skills/core/scripts/prepare_contribution.py`.
2. Generalize repo-specific details into parameters.
3. Create a properly formatted convention pack with `CONVENTION_PACK.yaml`.
4. Generate PR description with provenance (anonymized).
5. Include test cases derived from the learnings.

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

## Post-Action Hook Protocol

**This is what makes the repo "living." Execute after ANY significant action** (analysis step, data ingestion, algorithm implementation, report generation):

1. **Update manifests**: Update the relevant manifest (`ANALYSIS_MANIFEST.md`, `DATA_MANIFEST.md`, etc.) with new/changed entries.
2. **Update documentation**: Update or create the UPPER_SNAKE_CASE.md file in the affected subfolder with current status, key findings, open questions.
3. **Log decisions**: If a non-obvious choice was made, append to `.living/decisions.md` using the decision log template.
4. **Log learnings**: If something unexpected was learned (gotcha, edge case, failure), append to `.living/learnings.md` using the learning entry template. **Knowledge promotion**: If the learning is transferable (would help in any project), also append to the matching global domain file in `~/.claude/knowledge/{domain}.md` with `status: unreviewed`. Use the structured entry template with a `when_useful` trigger condition.
5. **Log todos**: If future work is identified during the action, add items to `todo/TODO_REGISTRY.md` (and create detailed `todo/[item].md` files for complex items).
6. **Validate**: Run `skills/core/scripts/validate_structure.py` to confirm repo still conforms.
7. **Convention feedback**: If any convention pack practices were relevant, note whether they were helpful or had gaps.

### Automated Enforcement (Claude Code Hooks)

Mycelium ships optional hook scripts in `hooks/` that enforce the post-action protocol automatically:

| Hook | Event | Purpose |
|------|-------|---------|
| `mycelium-health.sh` | SessionStart | Warns if `.living/` is missing or incomplete; records session timestamp; triggers weekly knowledge audit if `~/.claude/knowledge/.last-audit` is >7 days old |
| `mycelium-post-action.sh` | PostToolUse (Bash) | Detects code execution and directs Claude to run the full post-action protocol |
| `mycelium-stop-check.sh` | Stop | Blocks session end if significant work was done without updating `.living/` |

**Post-action enforcement**: The PostToolUse hook detects Python/R/Jupyter execution in Bash calls (excluding tests, linting, pip, one-liners) and injects a mandatory directive for Claude to execute the full post-action protocol — saving outputs, updating manifests, and logging to `.living/`. It is **debounced**: fires once per work cycle, then stays silent until `.living/` is updated (completing the cycle). This means Claude receives exactly one directive per burst of analysis work, not one per Bash call. The Stop hook serves as a safety net for non-analysis sessions.

**Stop hook logic**: The stop hook only blocks if `mycelium-post-action.sh` fired during the session (indicated by the presence of `.claude/mycelium-reminded.tmp`) AND `.living/` was not updated afterward. Read-only sessions, config-only sessions, and sessions without code execution are never blocked. When `.living/` is updated after the post-action hook fires, the reminder file is cleaned up automatically at session end.

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
2. **After all subagent batches complete**, the main context dispatches a crystallization subagent (lightweight model) that:
   - Reviews the summary of what was accomplished
   - Appends entries to `.living/learnings.md` and `.living/decisions.md`
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
