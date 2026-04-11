# Mycelium

<!-- Badges -->
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Validate Skill](https://img.shields.io/badge/CI-validate--skill-blue.svg)](.github/workflows/validate-skill.yml)

**A self-documenting, self-improving framework for analytical projects.**

Most analytical work disappears. You spend weeks figuring out the right normalization for a tricky dataset, discover that a particular clustering method fails silently on sparse data, or learn that a specific file format needs a workaround — and none of that knowledge is captured anywhere durable. The next person (or you, six months later) starts from scratch.

Mycelium changes this. It gives every analytical project a memory — a structured layer that records decisions, captures hard-won insights, and tracks what was done and why. Drop the mycelium skill into [Claude Code](https://docs.anthropic.com/en/docs/claude-code), point it at any project, and it scaffolds a living analytical framework. Every analysis, dataset, and decision is registered. Learnings accumulate. Domain-specific best practices flow in from the network.

**The bigger vision:** analytical projects shouldn't be isolated silos. A lab that works on RNA-seq, image analysis, and spatial transcriptomics is generating overlapping knowledge across all of those efforts — but that knowledge stays trapped in individual folders and the heads of the people who did the work. Mycelium is building toward a world where projects are nodes in a knowledge network: insights discovered in one project flow automatically to others that need them, domain expertise is packaged and shared, and the collective intelligence of a research group compounds over time instead of evaporating.

## Philosophy

Mycelium is named after the underground fungal networks that connect trees in a forest — sharing nutrients, signaling danger, and building collective resilience. Similarly, mycelium-enabled projects are nodes in a knowledge network:

- **Each project carries its own memory.** Decisions, learnings, and conventions are recorded as structured traces in the `.living/` directory, so every session starts with the accumulated intelligence of all previous sessions.
- **Projects grow smarter over time.** Gotchas encountered once are never forgotten. Patterns detected in learnings crystallize into conventions. The project evolves.
- **The network shares nutrients.** Domain-specific best practices (bioinformatics, image analysis, and more) are packaged as convention packs that any project can install. When one project discovers something generally useful, it can contribute back.

## Quickstart

### 1. Install the plugin

**Option A — Marketplace install (recommended):**

```bash
# Add the mycelium marketplace (one-time)
claude plugin marketplace add arjunrajlaboratory/mycelium

# Install the plugin
claude plugin install mycelium@mycelium
```

This permanently registers the mycelium plugin with your Claude Code installation. The slash commands (`/mycelium:core`, `/mycelium:analyze`, `/mycelium:report`, `/mycelium:ideas`, `/mycelium:ingest`, `/mycelium:transfer`) become available in all sessions.

**Option B — Local / development install:**

```bash
git clone https://github.com/arjunrajlaboratory/mycelium.git
claude --plugin-dir /path/to/mycelium
```

Replace `/path/to/mycelium` with the actual path where you cloned it. This loads the plugin for a single session only.

### 2. Initialize your project

Open Claude Code in any project directory and say:

> "Set up mycelium" or "Initialize living repo"

This scaffolds the living repository structure: directories, manifests, the `.living/` memory layer, and a `CLAUDE.md` that encodes the framework's protocols. **Core convention packs** (`robust-analysis`, `report-generator`, and `idea-generator`) are installed automatically — every repo gets defensive analysis practices, structured report generation, and creative ideation out of the box.

### 3. Install domain conventions (optional)

Once mycelium is running in your project, install domain-specific convention packs by telling Claude:

> "Install bioinformatics conventions" or "Install image-analysis conventions"

This uses mycelium's built-in `install-convention` mode to copy domain conventions into your project's `.living/conventions/` directory.

### 4. Start working

Work normally — analyze data, write code, build algorithms. Use the dedicated action skills:

- `/mycelium:analyze` — start or continue an analysis (routes to installed conventions)
- `/mycelium:report` — generate a structured report
- `/mycelium:ideas` — brainstorm with disciplinary personas
- `/mycelium:ingest` — import new data with metadata and provenance
- `/mycelium:transfer` — cross-pollinate learnings across sibling projects

Mycelium's hooks enforce the post-action protocol automatically after every significant action:

- Manifests are updated
- Documentation reflects current status
- Decisions are logged with rationale
- Learnings capture gotchas and insights
- Scientific findings are routed to topic-based evidence ledgers
- Recurring patterns crystallize into conventions
- Transferable knowledge is promoted to global domain files

## What a Mycelium-Enabled Project Looks Like

After initialization, your project has this structure:

```
project-root/
├── CLAUDE.md                     # AI agent instructions
├── ENVIRONMENTS_INSTALLATIONS.md # Environment setup and dependencies
├── todo/                         # Future work tracking
│   ├── TODO_REGISTRY.md          # Master registry of all items
│   └── [item].md                 # Detailed writeup per item
├── .claude/                      # Session state (gitignored)
│   └── last-session.md           # Cross-session resume context
├── .living/                      # The memory layer
│   ├── INDEX.md                  # Knowledge summary with cluster routing
│   ├── decisions.md              # Why choices were made
│   ├── learnings.md              # Gotchas, surprises, insights
│   ├── conventions.md            # Project-specific conventions (crystallized from learnings)
│   ├── conventions/              # Installed convention packs
│   ├── generated-conventions/    # Conventions packaged for contribution
│   ├── log/                      # Session-by-session event log
│   │   ├── LOG_REGISTRY.md       # Scannable registry with semantic summaries
│   │   └── YYYY-MM-DD-NNN-*.md   # Individual session logs
│   ├── findings/                 # Scientific findings by topic
│   │   ├── FINDINGS_REGISTRY.md
│   │   └── {topic-slug}.md       # Evidence ledger per topic
│   └── outputs/
│       └── knowledge-transfers/  # Cross-project transfer audit trail
├── algorithms/                   # Reusable methods (with ALGORITHM_MANIFEST.md)
├── analysis/                     # Analytical work (with ANALYSIS_MANIFEST.md)
├── data/                         # Data assets (with DATA_MANIFEST.md)
│   ├── raw/                      # Immutable originals
│   ├── processed/                # Transformed data
│   └── metadata/                 # Schemas, provenance
└── reference_material/           # External references (with REFERENCE_MANIFEST.md)
```

Every directory has a descriptive manifest — a registry of its contents with structured metadata. Nothing is orphaned. Every subdirectory has a documentation file named in UPPER_SNAKE_CASE of the folder name (e.g., `analysis/snp-analysis/SNP_ANALYSIS.md`), making documents instantly discoverable in search.

## Hooks — Automated Enforcement

Mycelium ships 5 Claude Code hooks that enforce the living repo protocol automatically:

| Hook | Event | Purpose |
|------|-------|---------|
| `mycelium-health.sh` | SessionStart | Loads session resume context, refreshes INDEX.md counts, injects knowledge summaries, triggers daily knowledge audit, checks for pending cross-project transfers |
| `mycelium-post-action.sh` | PostToolUse (Bash) | Detects code execution (Python/R/Jupyter) and injects the full 10-step post-action protocol. Debounced per work cycle. |
| `mycelium-activity-tracker.sh` | PostToolUse (Edit\|Write) | Silently tracks file modifications so Edit/Write-only sessions are also enforced |
| `mycelium-read-tracker.sh` | PostToolUse (Read) | Logs `.living/` file access for consumption telemetry |
| `mycelium-stop-check.sh` | Stop | Auto-finalizes session logs, blocks session end if `.living/` wasn't updated after significant work, reminds about session summary |

Hooks are auto-registered by `init_repo.py` during project initialization. No manual configuration needed.

## Progressive Disclosure Knowledge System

Mycelium includes a three-tier knowledge system that routes agents to the right information at the right time:

1. **MEMORY.md routing table** (always in context) — lightweight domain pointers that tell the agent where to look.
2. **INDEX.md summaries** (injected at session start) — LLM-generated knowledge clusters per project, refreshed automatically.
3. **Global domain files** (`~/.claude/knowledge/`) — cross-project knowledge organized by domain (Python, statistics, data engineering, etc.). Transferable learnings are promoted here automatically.

This replaces the naive approach of loading all `.living/` files at session start, which doesn't scale past the first few sessions.

## Architecture

Mycelium separates **skills** (actions) from **conventions** (reference material):

- **Skills** are Claude Code slash commands that execute workflows: `/mycelium:core` (core orchestrator), `/mycelium:analyze`, `/mycelium:report`, `/mycelium:ideas`, `/mycelium:ingest`, `/mycelium:transfer`
- **Convention packs** are collections of markdown files that skills consult for methodology guidance. They're swappable — different report conventions, different analysis approaches.
- **Hooks** enforce the framework automatically — detecting code execution, tracking file edits, and ensuring `.living/` stays current without manual intervention.

Skills route to whatever conventions are installed. Adding a new report style or analysis methodology is just adding markdown files — no plugin changes needed.

## The Network

Mycelium includes a marketplace of convention packs — some core (auto-installed), some domain-specific (opt-in):

### Core Packs (batteries included)

These are installed automatically during `mycelium init`:

| Convention Pack | Description |
|----------------|-------------|
| [robust-analysis](network/conventions/robust-analysis/) | Defensive execution, validation checks, sensitivity sweeps, null hypothesis testing |
| [report-generator](network/conventions/report-generator/) | Structured LaTeX PDF report generation with provenance |
| [idea-generator](network/conventions/idea-generator/) | Persona-based creative ideation for new analysis directions |

### Domain Packs (opt-in)

Install these for field-specific conventions:

| Convention Pack | Description |
|----------------|-------------|
| [bioinformatics](network/conventions/bioinformatics/) | RNA-seq, single-cell, genomics workflows |
| [image-analysis](network/conventions/image-analysis/) | Segmentation, quantification, microscopy QC |

Install a domain convention pack:

> "Install bioinformatics conventions"

Domain conventions layer on top of core conventions, providing specialized guidance for your field.

### Contributing Convention Packs

Have domain expertise? You can contribute convention packs:

1. Work in a mycelium-enabled project — learnings accumulate naturally
2. Run `crystallize` mode to extract patterns into conventions
3. Run `contribute` mode to package them for the network
4. Open a PR — the community reviews and merges

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## The Living Loop

```
Accumulate -> Crystallize -> Transfer -> Contribute
```

1. **Accumulate**: As you work, log decisions and learnings. The project's `.living/` directory grows. Scientific findings are captured in topic-organized files with evidence tracking.
2. **Crystallize**: Periodically review accumulated intelligence. Recurring patterns become formal conventions. Transferable knowledge is promoted to global domain files (`~/.claude/knowledge/`).
3. **Transfer**: Cross-pollinate learnings across sibling projects. Insights discovered in one project are automatically adapted and applied to others that would benefit (`/mycelium:transfer`).
4. **Contribute**: Conventions that are generally useful get packaged and shared back to the network.

This is how the ecosystem improves: individual projects learn, patterns are extracted, knowledge flows across projects, and the community benefits.

## Repository Structure

- **[`commands/`](commands/)** — Claude Code slash commands: `core.md` (orchestrator), `analyze.md`, `report.md`, `ideas.md`, `ingest.md`, `transfer.md`.
- **[`skills/core/`](skills/core/)** — Bundled resources used by the commands:
  - `hooks/` — 5 automation hooks (health, post-action, activity-tracker, read-tracker, stop-check)
  - `scripts/` — Python scripts for initialization, validation, index generation, findings crystallization, knowledge bootstrap
  - `templates/` — Templates for manifests, metadata, findings, conventions, reports, knowledge entries
  - `references/` — Reference docs for analysis, data ingestion, environment setup, and more
- **[`network/`](network/)** — The marketplace of convention packs.

## License

[MIT](LICENSE)
