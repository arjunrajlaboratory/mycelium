# Mycelium

<!-- Badges -->
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Validate Skill](https://img.shields.io/badge/CI-validate--skill-blue.svg)](.github/workflows/validate-skill.yml)

**A self-documenting, self-improving framework for analytical repositories.**

Drop the mycelium skill into Claude Code, point it at any repository, and it scaffolds a living analytical framework. Every analysis, dataset, and decision is registered. Learnings accumulate. Domain-specific best practices flow in from the network.

## Philosophy

Mycelium is named after the underground fungal networks that connect trees in a forest — sharing nutrients, signaling danger, and building collective resilience. Similarly, mycelium-enabled repositories are nodes in a knowledge network:

- **Each repo carries its own memory.** Decisions, learnings, and conventions are recorded as structured traces in the `.living/` directory, so every session starts with the accumulated intelligence of all previous sessions.
- **Repos grow smarter over time.** Gotchas encountered once are never forgotten. Patterns detected in learnings crystallize into conventions. The repository evolves.
- **The network shares nutrients.** Domain-specific best practices (bioinformatics, image analysis, and more) are packaged as skill packs that any repo can install. When a repo discovers something generally useful, it can contribute back.

## Quickstart

### 1. Install the skill

Add the mycelium skill to Claude Code by referencing the `skill/SKILL.md` file in this repository.

### 2. Initialize your repo

Tell Claude Code:

> "Set up mycelium" or "Initialize living repo"

This scaffolds the living repository structure: directories, manifests, the `.living/` memory layer, and a `CLAUDE.md` that encodes the framework's protocols.

### 3. Start working

Work normally — analyze data, write code, build algorithms. Mycelium's post-action hook protocol ensures that every significant step is traced:

- Manifests are updated
- READMEs reflect current status
- Decisions are logged with rationale
- Learnings capture gotchas and insights

## What a Mycelium-Enabled Repo Looks Like

After initialization, your repository has this structure:

```
project-root/
├── CLAUDE.md                     # AI agent instructions
├── ENVIRONMENTS_INSTALLATIONS.md # Environment setup and dependencies
├── .living/                      # The memory layer
│   ├── decisions.md              # Why choices were made
│   ├── learnings.md              # Gotchas, surprises, insights
│   ├── conventions.md            # Repo-specific overrides
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

Mycelium includes a marketplace of domain-specific skill packs:

| Skill Pack | Description |
|------------|-------------|
| [bioinformatics](network/skills/bioinformatics/) | RNA-seq, single-cell, genomics workflows |
| [image-analysis](network/skills/image-analysis/) | Segmentation, quantification, microscopy QC |

Install a domain skill:

> "Install bioinformatics skill"

Domain conventions layer on top of core conventions, providing specialized guidance for your field.

### Contributing Domain Skills

Have domain expertise? You can contribute skill packs:

1. Work in a mycelium-enabled repo — learnings accumulate naturally
2. Run `crystallize` mode to extract patterns into conventions
3. Run `contribute` mode to package them for the network
4. Open a PR — the community reviews and merges

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## The Living Loop

```
Accumulate → Crystallize → Contribute
```

1. **Accumulate**: As you work, log decisions and learnings. The repo's `.living/` directory grows.
2. **Crystallize**: Periodically review accumulated intelligence. Recurring patterns become formal conventions.
3. **Contribute**: Conventions that are generally useful get packaged and shared back to the network.

This is how the ecosystem improves: individual repositories learn, patterns are extracted, and the community benefits.

## Repository Structure

This repo has two halves:

- **[`skill/`](skill/)** — The core Claude Code skill. This is what you install to enable mycelium in your repos.
- **[`network/`](network/)** — The marketplace of domain-specific skill packs.

## License

[MIT](LICENSE)
