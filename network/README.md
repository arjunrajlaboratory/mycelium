# Mycelium Network — Convention Pack Marketplace

Browse, install, and contribute convention packs for mycelium-enabled repositories.

**Core packs** are auto-installed during `mycelium init` — they provide batteries-included practices every analysis repo should have. **Domain packs** are opt-in specializations for specific fields.

## Available Convention Packs

| Convention Pack | Version | Type | Description |
|----------------|---------|------|-------------|
| [robust-analysis](conventions/robust-analysis/) | 0.1.0 | core | Defensive execution, validation, sensitivity sweeps, null hypothesis testing |
| [report-generator](conventions/report-generator/) | 0.1.0 | core | Structured LaTeX PDF report generation with provenance |
| [idea-generator](conventions/idea-generator/) | 0.1.0 | core | Persona-based creative ideation for new analysis directions |
| [bioinformatics](conventions/bioinformatics/) | 0.1.0 | domain | RNA-seq, single-cell, genomics workflows |
| [image-analysis](conventions/image-analysis/) | 0.1.0 | domain | Segmentation, quantification, imaging QC |

## Installing a Convention Pack

**Core packs** (`robust-analysis`, `report-generator`, `idea-generator`) are installed automatically when you run `mycelium init`. No action needed.

**Domain packs** are installed on demand. The easiest way is to tell Claude:

> "Install bioinformatics conventions"

This uses mycelium's built-in `install-convention` mode. Alternatively, run the script directly:

```bash
python /path/to/mycelium/skills/core/scripts/install_convention.py \
    --name bioinformatics \
    --network-dir /path/to/mycelium/network/conventions
```

## Contributing a New Convention Pack

1. **Start locally**: Work in a mycelium-enabled repo and accumulate learnings in your domain
2. **Crystallize**: Use mycelium's `crystallize` mode to extract patterns into conventions
3. **Package**: Use `contribute` mode to prepare a convention pack
4. **Submit**: Open a PR to this repository, placing your pack in `community-contributed/`
5. **Review**: The community reviews for quality, generality, and domain accuracy

See the main [CONTRIBUTING.md](../CONTRIBUTING.md) for full guidelines.

## Convention Pack Structure

Every convention pack must contain a `CONVENTION_PACK.yaml` and an `analysis-conventions.md` entry point. Beyond that, the structure varies by pack. Common patterns:

```
pack-name/
├── CONVENTION_PACK.yaml       # Required: metadata (name, version, core, description, tags)
├── analysis-conventions.md    # Required: entry point (hub file with progressive disclosure)
├── qc-checklist.md            # Recommended: quality control checklist
├── [detail-files].md          # Topic-specific deep dives linked from the hub
├── references/                # Optional: detailed reference docs
├── templates/                 # Optional: report/analysis templates
└── assets/                    # Optional: LaTeX templates, scripts, etc.
```

Core packs include `core: true` in their `CONVENTION_PACK.yaml`. See existing packs for examples of the progressive disclosure pattern.

## Requesting a New Domain

Don't see your domain? [File a new-domain-request issue](../../../issues/new?template=new-domain-request.md) describing what you need.
