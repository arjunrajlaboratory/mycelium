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
- **The network shares nutrients.** Domain-specific best practices (bioinformatics, image analysis, and more) are packaged as skill packs that any project can install. When one project discovers something generally useful, it can contribute back.

## Quickstart

### 1. Install the skill

**Option A — Marketplace install (recommended):**

```bash
claude plugin marketplace add arjunrajlaboratory/mycelium
claude plugin install mycelium
```

This permanently registers the mycelium skill with your Claude Code installation.

**Option B — Local / development install:**

```bash
git clone https://github.com/arjunrajlaboratory/mycelium.git
claude --plugin-dir /path/to/mycelium/skill
```

Replace `/path/to/mycelium` with the actual path where you cloned it. This loads the skill for a single session.

### 2. Initialize your project

Open Claude Code in any project directory and say:

> "Set up mycelium" or "Initialize living repo"

This scaffolds the living repository structure: directories, manifests, the `.living/` memory layer, and a `CLAUDE.md` that encodes the framework's protocols. **Core skill packs** (`robust-analysis` and `report-generator`) are installed automatically — every repo gets defensive analysis practices and structured report generation out of the box.

### 3. Install domain skills (optional)

Once mycelium is running in your project, install domain-specific skill packs by telling Claude:

> "Install bioinformatics skill" or "Install image-analysis skill"

This uses mycelium's built-in `install-skill` mode to copy domain conventions into your project's `.living/skills/` directory.

### 4. Start working

Work normally — analyze data, write code, build algorithms. Mycelium's post-action hook protocol ensures that every significant step is traced:

- Manifests are updated
- READMEs reflect current status
- Decisions are logged with rationale
- Learnings capture gotchas and insights

## What a Mycelium-Enabled Project Looks Like

After initialization, your project has this structure:

```
project-root/
├── CLAUDE.md                     # AI agent instructions
├── ENVIRONMENTS_INSTALLATIONS.md # Environment setup and dependencies
├── .living/                      # The memory layer
│   ├── decisions.md              # Why choices were made
│   ├── learnings.md              # Gotchas, surprises, insights
│   ├── conventions.md            # Project-specific overrides
│   └── skills/                   # Installed domain skills
├── algorithms/                   # Reusable methods (with MANIFEST.md)
├── analysis/                     # Analytical work (with MANIFEST.md)
├── data/                         # Data assets (with MANIFEST.md)
│   ├── raw/                      # Immutable originals
│   ├── processed/                # Transformed data
│   └── metadata/                 # Schemas, provenance
└── reference_material/           # External references (with MANIFEST.md)
```

Every directory has a `MANIFEST.md` — a registry of its contents with structured metadata. Nothing is orphaned.

## The Network

Mycelium includes a marketplace of skill packs — some core (auto-installed), some domain-specific (opt-in):

### Core Packs (batteries included)

These are installed automatically during `mycelium init`:

| Skill Pack | Description |
|------------|-------------|
| [robust-analysis](network/skills/robust-analysis/) | Defensive execution, validation checks, sensitivity sweeps, null hypothesis testing |
| [report-generator](network/skills/report-generator/) | Structured LaTeX PDF report generation with provenance |

### Domain Packs (opt-in)

Install these for field-specific conventions:

| Skill Pack | Description |
|------------|-------------|
| [bioinformatics](network/skills/bioinformatics/) | RNA-seq, single-cell, genomics workflows |
| [image-analysis](network/skills/image-analysis/) | Segmentation, quantification, microscopy QC |

Install a domain skill:

> "Install bioinformatics skill"

Domain conventions layer on top of core conventions, providing specialized guidance for your field.

### Contributing Domain Skills

Have domain expertise? You can contribute skill packs:

1. Work in a mycelium-enabled project — learnings accumulate naturally
2. Run `crystallize` mode to extract patterns into conventions
3. Run `contribute` mode to package them for the network
4. Open a PR — the community reviews and merges

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## The Living Loop

```
Accumulate → Crystallize → Contribute
```

1. **Accumulate**: As you work, log decisions and learnings. The project's `.living/` directory grows.
2. **Crystallize**: Periodically review accumulated intelligence. Recurring patterns become formal conventions.
3. **Contribute**: Conventions that are generally useful get packaged and shared back to the network.

This is how the ecosystem improves: individual projects learn, patterns are extracted, and the community benefits.

## Repository Structure

This repository has two halves:

- **[`skill/`](skill/)** — The core Claude Code skill. This is what you install to enable mycelium in your projects.
- **[`network/`](network/)** — The marketplace of domain-specific skill packs.

## License

[MIT](LICENSE)
