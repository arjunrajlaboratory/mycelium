# Mycelium Domain Vocabulary

Closed vocabulary for classifying learnings. Push-active domains participate in PreToolUse/Edit injection; pull-only domains are discoverable via MENU and Readable on demand but never auto-pushed.

| Domain | Push-active | Description | Example |
|---|---|---|---|
| figures | ✓ | color, DPI, typography, layout for publication plots | "use constrained_layout" |
| extraction | ✓ | KG claim extraction prompts, schemas, model selection | "Sonnet > Haiku for complex abstracts" |
| pipelines | ✓ | DAG orchestration, retry, error handling | "snapshot before retry" |
| statistics | ✓ | test selection, assumption checking, effect-size | "Welch's t is default over Student's" |
| writing | ✓ | IMRAD, citations, prose, figure legends | "reporting guidelines per journal" |
| debugging | — | runtime failures, env issues, backend problems | "matplotlib Agg backend for scripts" |
| tooling | — | conda, pytest, LSP, hook patterns | "warm LSP before Grep" |

**Classification rule for writers**: pick the domain a reader would look in first. If a learning could plausibly fit two, pick the one whose `matches:` globs better align with the files where the learning matters. When truly ambiguous, append to `learnings.md` (monolith) with a `migration-todo` note in the body.
