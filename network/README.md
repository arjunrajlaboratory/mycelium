# Mycelium Network — Domain Skill Marketplace

Browse, install, and contribute domain-specific skill packs for mycelium-enabled repositories.

## Available Skill Packs

| Skill Pack | Version | Description |
|------------|---------|-------------|
| [bioinformatics](skills/bioinformatics/) | 0.1.0 | RNA-seq, single-cell, genomics workflows |
| [image-analysis](skills/image-analysis/) | 0.1.0 | Segmentation, quantification, imaging QC |

## Installing a Skill Pack

From within a mycelium-enabled repository:

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

Each skill pack contains:

```
domain-name/
├── SKILL_PACK.yaml            # Metadata (name, version, description, tags)
├── analysis-conventions.md    # Domain-specific analysis conventions
├── statistical-conventions.md # Domain-specific statistical methods (optional)
├── qc-checklist.md            # Quality control checklist
└── templates/                 # Domain-specific templates
```

## Requesting a New Domain

Don't see your domain? [File a new-domain-request issue](../../../issues/new?template=new-domain-request.md) describing what you need.
