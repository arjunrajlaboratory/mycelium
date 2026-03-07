# Contributing to Mycelium

Thank you for your interest in contributing to mycelium! This project thrives on community contributions, especially domain-specific convention packs.

## Ways to Contribute

### 1. Contribute a New Convention Pack

This is the most impactful contribution. If you have expertise in a domain (proteomics, epidemiology, geospatial analysis, NLP, etc.), you can package your conventions into a convention pack.

**From scratch:**

1. Create a directory under `network/community-contributed/your-domain/`
2. Include:
   - `CONVENTION_PACK.yaml` — metadata (name, version, description, author, tags)
   - `analysis-conventions.md` — domain-specific analysis conventions
   - `qc-checklist.md` — quality control checklist for the domain
   - `templates/` — at least one domain-specific template
   - Optionally: `statistical-conventions.md`, additional reference docs
3. Open a PR using the [pull request template](.github/PULL_REQUEST_TEMPLATE.md)

**From a mycelium-enabled repo:**

1. Work in your repo, accumulating learnings and decisions
2. Use mycelium's `crystallize` mode to extract patterns
3. Use `contribute` mode to package them
4. Open a PR with the generated convention pack

### 2. Improve Existing Convention Packs

- Fix errors in conventions
- Add missing guidance
- Update for new tools or methods
- Add templates for common workflows

Open an issue first to discuss the change, then submit a PR.

### 3. Report Convention Gaps

Found a convention that was wrong, missing, or led you astray? [File an issue](.github/ISSUE_TEMPLATE/convention-gap.md). These reports directly improve the framework.

### 4. Request New Domains

Need a convention pack for your domain? [Request it](.github/ISSUE_TEMPLATE/new-domain-request.md). Include example workflows and key conventions to help someone create it.

### 5. Improve Core Infrastructure

The core skills (`skills/`), scripts, templates, and documentation are all open for improvement. For significant changes, open an issue to discuss the approach before submitting a PR.

## Development Guidelines

### Markdown

- Use consistent heading levels (H1 for title, H2 for sections, H3 for subsections)
- Keep lines readable
- Use fenced code blocks with language identifiers
- Tables should use the pipe format with alignment

### Python Scripts

- Target Python 3.10+
- Use `pathlib` for file operations
- Use `argparse` for CLI arguments
- Include docstrings
- Keep dependencies minimal (`pyyaml` is the only required external package)

### Conventions Content

- Be specific and actionable — "do X" not "consider X"
- Include examples of correct and incorrect application
- Explain *why*, not just *what*
- Keep the tone technical but approachable
- Don't duplicate core conventions in domain packs — extend them

## Review Process

### Convention Packs (network/)

1. PR is submitted to `network/community-contributed/`
2. Community review for quality, generality, and accuracy
3. At least one domain expert should review
4. After approval, moved to `network/conventions/`

### Core Skills (skills/)

1. Open an issue discussing the change
2. PR with the change
3. Maintainer review
4. Changes to SKILL.md files require extra scrutiny — they affect all users

## Code of Conduct

Be respectful, constructive, and welcoming. We're building a community resource. Disagree on conventions, not on people.
