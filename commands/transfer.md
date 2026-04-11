---
description: >
  Cross-pollinate learnings across Science subprojects by scanning recent
  .living/learnings.md entries and identifying actionable knowledge transfers.
  Use when user says "transfer knowledge", "cross-pollinate", "sync learnings",
  or "knowledge transfer".
---

# Mycelium — Knowledge Transfer

Scan recent learnings across all subprojects, identify actionable cross-pollination opportunities, and **automatically apply** transfers to target projects' `.living/learnings.md` files. Generates an audit report afterward. This command is designed to run AS a subagent (background task), not in the main context.

**Total runtime target: <2 minutes.**

## Steps

### 1. Inventory projects

Glob for all `.living/learnings.md` files under the meta-project root:

```bash
find {meta-project-root} -path "*/.living/learnings.md" -not -path "*/node_modules/*"
```

The meta-project is found by walking up from the current repo to find the nearest parent directory that contains a `.living/` folder. If the current directory IS the meta-project (e.g., `Science/`), use it directly.

List all subproject learnings files found. The meta-project's own `.living/learnings.md` is the baseline of already-transferred knowledge — treat it separately.

### 2. Read recent learnings (context-efficient)

- Read each subproject's `.living/INDEX.md` first to understand project context and entry counts.
- Use `tail -80` via Bash on each subproject's `learnings.md` to get only recent entries — **NEVER read full files** (some are 300KB+).
- Read the meta-project's `.living/learnings.md` last 100 lines as the baseline of already-transferred knowledge.

```bash
# For each subproject:
tail -80 {subproject}/.living/learnings.md

# For meta-project baseline:
tail -100 {meta-project}/.living/learnings.md
```

### 3. Cross-reference for transfer opportunities

For each recent learning in each subproject, evaluate:

- Does this apply to any **other** subproject? (shared infrastructure, similar analysis patterns, common pitfalls)
- Is it **already** in the meta-project's cross-project learnings (baseline check)?
- Would transferring this **concretely prevent a future issue** or **improve a workflow**?

Focus ONLY on actionable transfers:
- Debugging patterns that prevent bugs in similar code
- API/infrastructure insights affecting shared dependencies
- Workflow patterns improving similar processes
- Scientific methodology insights relevant to related analyses

IGNORE:
- Project-specific implementation details with no generalizable lesson
- Learnings already transferred to the meta-project (present in baseline)
- Trivial or obvious connections without concrete benefit
- Theoretical similarities without an actionable outcome

### 4. Apply transfers

For each identified transfer, **automatically append** the learning to the target project's `.living/learnings.md` using the learning entry template format:

```markdown
## [YYYY-MM-DD] [Short Learning Title]

**Category**: [gotcha|edge-case|insight|failure|tip]

**What happened**: [Adapted description — rewritten for the target project's context, not copy-pasted verbatim from source]

**Why it matters**: [Why this is relevant to THIS project specifically]

**Resolution**: [The fix or pattern to apply]

**Tags**: [relevant, tags, for, searchability]

source: [source project name] (auto-transferred by mycelium)
```

**Transfer rules**:
- Rewrite the learning for the target project's context — don't dump the source project's jargon
- Keep entries concise (5-8 lines max)
- The `source:` field with `(auto-transferred by mycelium)` tag makes transfers auditable and distinguishable from manual entries
- Also append each transfer to the meta-project's `.living/learnings.md` if not already present there (the cross-project aggregate)
- Use `printf >> file` to append — do NOT read the full target file first

### 5. Generate report

Write an audit report to:
```
{meta-project}/.living/outputs/knowledge-transfers/YYYY-MM-DD.md
```

Use the template at `skills/core/templates/knowledge-transfer-report.md`.

If the output directory does not exist, create it:
```bash
mkdir -p {meta-project}/.living/outputs/knowledge-transfers/
```

The report serves as an **audit trail** of what was transferred and why. It is written AFTER transfers are applied.

Report constraints:
- **Max 50 lines** unless there are exceptional findings
- If no transfers are found, write a brief "all clear" report — this is expected most days
- Mark each transfer as `APPLIED` in the report

### 6. Update timestamp

```bash
date -u +"%Y-%m-%dT%H:%M:%SZ" > {meta-project}/.living/outputs/knowledge-transfers/.last-run
```

## Constraints

- Total runtime target: <2 minutes
- NEVER read full learnings files — always use `tail -80`
- Use `printf >> file` to append entries — never read target files before writing
- Keep transferred entries concise (5-8 lines each)
- The `(auto-transferred by mycelium)` tag in the source field makes transfers auditable
- If a transfer would be a near-duplicate of an existing recent entry in the target (check with `tail -30`), skip it

## When this runs

- **Automatic**: Dispatched as a background sonnet subagent at session start when >24h since last run (triggered by `mycelium-health.sh` checking `.last-run`)
- **Manual**: User invokes `/mycelium:transfer` or says "transfer knowledge", "cross-pollinate", "sync learnings", or "knowledge transfer"
