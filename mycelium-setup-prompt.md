# Mycelium Setup Prompt for Claude Code

> **Note**: This is the original setup prompt used to bootstrap the mycelium repository. It is kept for historical reference. The actual architecture has evolved — see README.md for the current state.

---

# Task: Set up the Mycelium monorepo

Mycelium is an open-source "living repository" framework. It's a Claude Code plugin that you can drop into any analytical repository (new or existing) to give it self-documenting, self-improving capabilities. Every action leaves a structured trace — manifests, decision logs, learnings — so that Claude Code (or any AI coding agent) can read the repo's accumulated intelligence on every subsequent invocation.

The repo has three key parts:
1. **`commands/`** — Claude Code slash commands that users invoke. `core.md` is the main orchestrator; `analyze.md`, `report.md`, `ideas.md`, `ingest.md`, and `transfer.md` are dedicated action skills.
2. **`skills/core/`** — Bundled resources: hooks (5 automation scripts), scripts, templates, and references.
3. **`network/`** — A marketplace of convention packs (bioinformatics, image analysis, etc.) that skills consult for methodology guidance. Community-contributed over time.

**Key architectural distinction:** Skills are actions (workflows invoked via `/mycelium:analyze`, `/mycelium:report`, etc.). Convention packs are swappable reference material (markdown files) that skills route to based on what's installed.

## Repository structure

```
mycelium/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── CHANGELOG.md
│
├── commands/                              # CLAUDE CODE SLASH COMMANDS
│   ├── core.md                            # /mycelium:core — main orchestrator
│   ├── analyze.md                         # /mycelium:analyze
│   ├── report.md                          # /mycelium:report
│   ├── ideas.md                           # /mycelium:ideas
│   ├── ingest.md                          # /mycelium:ingest
│   └── transfer.md                        # /mycelium:transfer
│
├── skills/                                # BUNDLED RESOURCES
│   └── core/
│       ├── SKILL.md                       # Skill frontmatter + full protocol
│       ├── scripts/
│       │   ├── init_repo.py
│       │   ├── install_convention.py
│       │   ├── validate_structure.py
│       │   ├── generate_index.py          # INDEX.md generation (--counts-only, --summarize)
│       │   ├── crystallize_findings.py    # Cross-project findings indexing
│       │   └── init_knowledge.py          # Global knowledge system bootstrap
│       ├── references/
│       │   ├── folder-structure.md
│       │   ├── environment-setup.md
│       │   ├── analysis-conventions.md
│       │   ├── statistical-conventions.md
│       │   ├── writing-conventions.md
│       │   ├── data-ingest-conventions.md
│       │   ├── marketplace-guide.md
│       │   └── skill-generation-guide.md
│       ├── templates/
│       │   ├── CLAUDE.md.template
│       │   ├── knowledge/                 # Knowledge system templates
│       │   └── ...
│       └── hooks/
│           ├── mycelium-health.sh         # SessionStart — resume, INDEX.md, audit
│           ├── mycelium-post-action.sh    # PostToolUse (Bash) — post-action enforcement
│           ├── mycelium-activity-tracker.sh # PostToolUse (Edit|Write) — file edit tracking
│           ├── mycelium-read-tracker.sh   # PostToolUse (Read) — .living/ access telemetry
│           └── mycelium-stop-check.sh     # Stop — session finalization + blocking enforcement
│
├── network/                               # THE CONVENTION MARKETPLACE
│   ├── README.md
│   ├── conventions/
│   │   ├── robust-analysis/               # core convention pack
│   │   │   ├── CONVENTION_PACK.yaml
│   │   │   ├── analysis-conventions.md
│   │   │   └── ...
│   │   ├── report-generator/              # core convention pack
│   │   ├── idea-generator/                # core convention pack
│   │   ├── bioinformatics/                # domain convention pack
│   │   └── image-analysis/                # domain convention pack
│   └── community-contributed/
│       └── .gitkeep
│
├── .claude-plugin/
│   └── marketplace.json                   # Registers all skills with Claude Code
│
└── .github/
    ├── ISSUE_TEMPLATE/
    ├── PULL_REQUEST_TEMPLATE.md
    └── workflows/
        └── validate-skill.yml
```

## Architecture notes

- **Commands** are markdown files in `commands/` registered via marketplace.json; `SKILL.md` holds the core protocol + frontmatter
- **Convention packs** are directories of markdown files with a CONVENTION_PACK.yaml metadata file
- Commands route to conventions: `/mycelium:analyze` checks `.living/conventions/` to see what's installed
- Convention cascade: repo-local (.living/conventions.md) > domain pack > core references
- **Lifecycle**: accumulate → crystallize → transfer → contribute
- **Hook chain**: health (SessionStart) → post-action + activity-tracker + read-tracker (PostToolUse) → stop-check (Stop)
- **Knowledge system**: MEMORY.md routing → INDEX.md summaries → `~/.claude/knowledge/` domain files
