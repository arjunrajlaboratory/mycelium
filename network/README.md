# Mycelium Network — Domain Skill Marketplace

Browse, install, and contribute skill packs for mycelium-enabled repositories.

**Core packs** are auto-installed during `mycelium init` — they provide batteries-included practices every analysis repo should have. **Domain packs** are opt-in specializations for specific fields.

## Available Skill Packs

| Skill Pack | Version | Type | Description |
|------------|---------|------|-------------|
| [robust-analysis](skills/robust-analysis/) | 0.1.0 | core | Defensive execution, validation, sensitivity sweeps, null hypothesis testing |
| [report-generator](skills/report-generator/) | 0.1.0 | core | Structured LaTeX PDF report generation with provenance |
| [bioinformatics](skills/bioinformatics/) | 0.1.0 | domain | RNA-seq, single-cell, genomics workflows |
| [image-analysis](skills/image-analysis/) | 0.1.0 | domain | Segmentation, quantification, imaging QC |

## Installing a Skill Pack

**Core packs** (`robust-analysis`, `report-generator`) are installed automatically when you run `mycelium init`. No action needed.

**Domain packs** are installed on demand. From within a mycelium-enabled repository:

```bash
python /path/to/mycelium/skill/scripts/install_domain_skill.py \
    --domain bioinformatics \
    --network-dir /path/to/mycelium/network/skills
```

Or use mycelium's `install-skill` mode — just say "install bioinformatics skill."

## Contributing a New Skill Pack

1. **Start locally**: Work in a mycelium-enabled repo and accumulate learnings in your domain
2. **Crystallize**: Use mycelium's `crystallize` mode to extract patterns into conventions
3. **Package**: Use `contribute` mode to prepare a skill pack
4. **Submit**: Open a PR to this repository, placing your pack in `community-contributed/`
5. **Review**: The community reviews for quality, generality, and domain accuracy

See the main [CONTRIBUTING.md](../CONTRIBUTING.md) for full guidelines.

## Skill Pack Structure

Every skill pack must contain a `SKILL_PACK.yaml` and an `analysis-conventions.md` entry point. Beyond that, the structure varies by pack. Common patterns:

```
pack-name/
├── SKILL_PACK.yaml            # Required: metadata (name, version, core, description, tags)
├── analysis-conventions.md    # Required: entry point (hub file with progressive disclosure)
├── qc-checklist.md            # Recommended: quality control checklist
├── [detail-files].md          # Topic-specific deep dives linked from the hub
├── references/                # Optional: detailed reference docs
├── templates/                 # Optional: report/analysis templates
└── assets/                    # Optional: LaTeX templates, scripts, etc.
```

Core packs include `core: true` in their `SKILL_PACK.yaml`. See existing packs for examples of the progressive disclosure pattern.

## Requesting a New Domain

Don't see your domain? [File a new-domain-request issue](../../../issues/new?template=new-domain-request.md) describing what you need.
