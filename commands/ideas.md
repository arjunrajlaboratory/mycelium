---
description: >
  Generate creative analysis ideas by having diverse disciplinary personas
  review existing data and findings. Uses parallel subagents for efficient
  cross-disciplinary brainstorming. Use when user says "generate ideas",
  "brainstorm", "creative ideas", "persona brainstorm", "idea generation",
  or "new analysis directions".
---

# Mycelium — Ideas

Generate creative analysis ideas by routing to installed idea convention packs, which provide persona catalogs, idea templates, and execution protocols.

## Steps

1. **Check for convention pack**: Verify an idea convention pack is installed (check `.living/conventions/idea-generator/`). If not installed, install it first using `/mycelium:core` install-convention mode.

2. **Context gathering**: Read all manifests (`ANALYSIS_MANIFEST.md`, `DATA_MANIFEST.md`, `ALGORITHM_MANIFEST.md`), analysis documentation files, and any experimental design documents. Compile a comprehensive context summary of what the project contains, what has been analyzed, and what was found.

3. **Route to installed conventions**:
   - Read `.living/conventions/ACTIVE_CONVENTIONS.yaml` to see what's installed.
   - **If `idea-generator` is installed**: Use its convention files:
     - `analysis-conventions.md` — entry point explaining the approach
     - `execution-protocol.md` — detailed step-by-step procedure
     - `persona-catalog.md` — persona descriptions
     - `idea-template.md` — output template for subagents
   - **If other idea conventions are installed** (future packs with different persona sets or brainstorming methods): Present the user with available options.

4. **Persona selection**: Present the persona catalog. Ask the user if they want to:
   - Use all default personas
   - Select a subset
   - Add custom personas
   - Adjust ideas per persona (default: 2)

5. **Directory setup**: Create `analysis/ideas/[session-name]/` where session-name is date-stamped and descriptive (e.g., `2026-03-06-cross-disciplinary-brainstorm`).

6. **Parallel subagent launch**: Launch subagents in batches of ~5. Each subagent receives the context summary, their assigned persona description, and the idea template. Each writes a file like `01_statistical-physicist.md` with their ideas.

7. **Compilation**: After all subagents complete, build `00_index.md` with a table of all ideas (persona, title, feasibility, one-line summary), grouped by feasibility. Follow the index template in the execution protocol.

8. **Register the session**: Add an entry to `analysis/ANALYSIS_MANIFEST.md` for the ideation session.

9. **Present to user**: Show the index and ask if they want to:
   - Promote any ideas to `todo/` items (via `/mycelium:core` todo-idea mode)
   - Deep-dive into any idea
   - Run another session with different personas

10. **Run post-action hook protocol** (see `/mycelium:core` for the full protocol).
