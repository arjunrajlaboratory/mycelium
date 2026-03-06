# Idea Generator Conventions

Creative ideation engine for analytical projects. Generates novel analysis directions by having diverse disciplinary personas review your existing data and findings, then propose structured ideas you might never have considered.

The core philosophy: **the best ideas come from looking at familiar data through unfamiliar lenses.** A physicist sees phase transitions where a biologist sees differentiation. An economist sees resource allocation where an ecologist sees niche competition. By systematically rotating through disciplinary perspectives, you surface connections that no single viewpoint would find.

---

## How It Works

1. **Context gathering** — Read existing analyses, data, and findings to build a comprehensive summary.
2. **Persona selection** — Choose personas relevant to the project from the catalog (or define custom ones).
3. **Parallel brainstorming** — Launch subagents, each adopting a persona, to generate structured ideas.
4. **Compilation** — Collect all ideas into an indexed directory for review and prioritization.

> See [execution-protocol.md](execution-protocol.md) for the step-by-step procedure.

---

## Persona Catalog

The skill ships with a default catalog of 15 personas spanning quantitative sciences, biology/biomedicine, computational/ML, and cross-disciplinary fields. These are starting points — the user can add, remove, or customize personas for their domain.

> See [persona-catalog.md](persona-catalog.md) for the full catalog with descriptions.

---

## Idea Template

Every idea follows a consistent structure: persona context, title, motivation, connection to existing data, concrete approach, expected insights, and feasibility estimate. This structure ensures ideas are actionable, not just hand-wavy.

> See [idea-template.md](idea-template.md) for the template and guidance.

---

## When to Use

- After completing a round of analyses and wanting fresh directions
- When a project feels stuck or is converging too narrowly
- During experiment planning to identify creative uses of existing data
- When onboarding collaborators from different disciplines (use their discipline as a persona)
- As a structured alternative to unstructured brainstorming

---

## Integration with Mycelium

- Ideas are stored in `analysis/ideas/[session-name]/` within the project
- Each session gets an index file (`00_index.md`) linking to all generated ideas
- High-priority ideas can be promoted to `todo/` items via the `todo-idea` mode
- The session and its outputs are registered in `ANALYSIS_MANIFEST.md`
