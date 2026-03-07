# Execution Protocol

Step-by-step procedure for running a persona-based ideation session. This protocol is designed for the `generate-ideas` mode in SKILL.md.

---

## Phase 1: Context Gathering

Before any persona can brainstorm, they need a comprehensive understanding of what exists.

1. **Read the project manifests**:
   - `analysis/ANALYSIS_MANIFEST.md` — what analyses exist
   - `data/DATA_MANIFEST.md` — what data is available
   - `algorithms/ALGORITHM_MANIFEST.md` — what methods are in use
2. **Read analysis documentation**: For each completed analysis, read its UPPER_SNAKE_CASE.md file and any conclusions/reports.
3. **Read the experimental design**: If there's a design document or README describing the experiment, read it.
4. **Compile a context summary**: Write a comprehensive summary (1-2 pages) of:
   - What the project is about
   - What data exists and its structure
   - What analyses have been done and their key findings
   - What questions remain open

This summary will be passed to every subagent as shared context.

---

## Phase 2: Persona Selection

1. **Default**: Use all 15 personas from `persona-catalog.md`.
2. **User customization**: If the user specifies:
   - A subset of personas to use
   - Custom personas to add
   - Personas to skip (e.g., "skip pharmacologist, not relevant")
   - A theme focus (e.g., "focus on computational personas")
3. **Determine ideas per persona**: Default is 2 ideas per persona. User can adjust.
4. **Calculate expected output**: [number of personas] x [ideas per persona] = total ideas.

---

## Phase 3: Directory Setup

Create the output directory structure:

```
analysis/ideas/[session-name]/
├── 00_index.md          # Master index (created after all ideas are in)
├── 01_statistical-physicist.md
├── 02_information-theorist.md
├── ...
└── 15_philosopher-of-biology.md
```

The session name should be descriptive and date-stamped, e.g., `2026-03-06-cross-disciplinary-brainstorm`.

---

## Phase 4: Parallel Subagent Launch

Launch subagents in batches of ~5 for parallelism. Each subagent receives:

1. **The context summary** from Phase 1
2. **Their assigned persona** description from the catalog
3. **The idea template** from `idea-template.md`
4. **The output path** where they should write their ideas

### Subagent Prompt Template

```
You are adopting the persona of a [PERSONA_NAME].

[PERSONA_DESCRIPTION from catalog]

You have been given a comprehensive summary of a research project, its data, and
its existing analyses. Your task is to review everything through the lens of your
discipline and propose [N] creative ideas for new analyses.

## Project Context
[CONTEXT_SUMMARY]

## Your Task
Generate [N] ideas following this template for each:
[IDEA_TEMPLATE]

Write your ideas to: [OUTPUT_PATH]

Remember:
- Be specific and concrete — reference actual data and analyses from the project
- Lean heavily into your disciplinary perspective
- One idea can be ambitious, one more tractable
- Be honest about feasibility
```

### Batch Strategy

- **Batch 1** (personas 1-5): Launch in parallel, wait for completion
- **Batch 2** (personas 6-10): Launch in parallel, wait for completion
- **Batch 3** (personas 11-15): Launch in parallel, wait for completion

If the user has fewer personas, adjust batch sizes. The goal is to avoid overwhelming the system while maintaining parallelism.

---

## Phase 5: Compilation

After all subagents complete:

1. **Review each output** for completeness and template adherence.
2. **Build the master index** (`00_index.md`) containing:
   - Session metadata (date, project, number of personas, total ideas)
   - A table of all ideas with: persona, idea title, feasibility, one-line summary
   - Quick-reference groupings (e.g., by feasibility, by theme)
3. **Register the session** in `analysis/ANALYSIS_MANIFEST.md` as an ideation session.
4. **Present the index to the user** for review.

### Index Template

```markdown
# Idea Generation Session: [Session Name]

**Date**: [date]
**Project**: [project name]
**Personas used**: [N]
**Total ideas generated**: [M]

## Ideas at a Glance

| # | Persona | Idea Title | Effort | Data Ready | One-Line Summary |
|---|---------|-----------|--------|------------|-----------------|
| 1 | Statistical Physicist | [title] | Medium | Yes | [summary] |
| ... | ... | ... | ... | ... | ... |

## By Feasibility

### Low Effort
- [list]

### Medium Effort
- [list]

### High Effort
- [list]

## Files
- [01_statistical-physicist.md](01_statistical-physicist.md)
- ...
```

---

## Phase 6: Follow-Up (Optional)

After the user reviews the index:

- **Promote to todo**: Convert selected ideas to `todo/` items via `todo-idea` mode
- **Deep dive**: Pick an idea and flesh it out into a full analysis plan
- **Second round**: Run another session with different or refined personas
- **Cross-pollinate**: Use insights from one persona's ideas to refine another's
