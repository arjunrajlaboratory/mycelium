---
description: >
  Invoke this skill for ANY request involving brainstorming, ideation, or
  generating new research ideas. This is the brainstorming and idea generation
  skill. Use it when the user wants to brainstorm, generate ideas, get fresh
  perspectives, hear what experts from different fields would think, find
  creative angles they're missing, explore new analysis directions, or get
  unstuck. Trigger phrases include: "brainstorm", "ideas", "fresh perspectives",
  "what would a [expert] think", "I'm stuck", "creative angles", "wild ideas",
  "what are we missing", "open to ideas", "before we write it up",
  "persona brainstorm", "new directions", "where to go next". If the user's
  request is open-ended and exploratory rather than a specific concrete task,
  use this skill. Do NOT use for defined tasks like running a specific analysis,
  fixing code, setting up projects, or ingesting data.
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
