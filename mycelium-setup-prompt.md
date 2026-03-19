# Mycelium Setup Prompt for Claude Code

> **Note**: This is the original setup prompt used to bootstrap the mycelium repository. It is kept for historical reference. The actual architecture has evolved — see README.md for the current state.

---

# Task: Set up the Mycelium monorepo

Mycelium is an open-source "living repository" framework. It's a Claude Code plugin that you can drop into any analytical repository (new or existing) to give it self-documenting, self-improving capabilities. Every action leaves a structured trace — manifests, decision logs, learnings — so that Claude Code (or any AI coding agent) can read the repo's accumulated intelligence on every subsequent invocation.

The repo has two halves:
1. **`skills/`** — Claude Code skills (actions) that users invoke via slash commands. `core/` is the main orchestrator; `analyze/`, `report/`, and `ideas/` are dedicated action skills.
2. **`network/`** — A marketplace of convention packs (bioinformatics, image analysis, etc.) that skills consult for methodology guidance. Community-contributed over time.

**Key architectural distinction:** Skills are actions (workflows invoked via `/mycelium:analyze`, `/mycelium:report`, etc.). Convention packs are swappable reference material (markdown files) that skills route to based on what's installed.

## Repository structure

```
mycelium/
├── README.md
├── LICENSE
├── CONTRIBUTING.md
├── CHANGELOG.md
│
├── skills/                                # CLAUDE CODE SKILLS (actions)
│   ├── core/                              # /mycelium:skill — main orchestrator
│   │   ├── SKILL.md                       # init, ingest, install-convention, crystallize, etc.
│   │   ├── scripts/
│   │   │   ├── init_repo.py
│   │   │   ├── install_convention.py
│   │   │   └── validate_structure.py
│   │   ├── references/
│   │   │   ├── folder-structure.md
│   │   │   ├── environment-setup.md
│   │   │   ├── analysis-conventions.md
│   │   │   ├── statistical-conventions.md
│   │   │   ├── writing-conventions.md
│   │   │   ├── data-ingest-conventions.md
│   │   │   ├── marketplace-guide.md
│   │   │   └── skill-generation-guide.md
│   │   ├── templates/
│   │   │   ├── CLAUDE.md.template
│   │   │   └── ...
│   │   └── hooks/
│   │       ├── mycelium-health.sh
│   │       └── mycelium-stop-check.sh
│   ├── analyze/                           # /mycelium:analyze
│   │   └── SKILL.md
│   ├── report/                            # /mycelium:report
│   │   └── SKILL.md
│   └── ideas/                             # /mycelium:ideas
│       └── SKILL.md
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

- **Skills** are SKILL.md files with YAML frontmatter registered via marketplace.json
- **Convention packs** are directories of markdown files with a CONVENTION_PACK.yaml metadata file
- Skills route to conventions: `/mycelium:analyze` checks `.living/conventions/` to see what's installed
- Convention cascade: repo-local (.living/conventions.md) > domain pack > core references
- The contribution pipeline produces convention packs, not skills: learnings -> crystallize -> contribute -> PR
