# Working on the Mycelium repository

This repository is the source of the Mycelium plugin for Claude Code, Codex, and
Antigravity. These notes are for agents changing the plugin itself, not for
projects that use it.

- Use the `develop` skill (`skills/develop/SKILL.md`) for plugin changes: shared
  skills, hooks, installers, lineage, and cross-host behaviour.
- Before live-testing on a host, running reviews, debugging hooks, merging
  stacked PRs, or releasing, read the matching section of
  [`docs/maintainer-runbook.md`](docs/maintainer-runbook.md). It holds the
  operational knowledge that the code does not show (host trust and cache
  quirks, live-test recipes, review stopping rules, release lessons).
- The release gate and tagging: [`docs/release-process.md`](docs/release-process.md).
  The validation ladder and review checklist:
  [`docs/cross-host-review-checklist.md`](docs/cross-host-review-checklist.md).
- When a task teaches something durable about developing, testing, or releasing
  Mycelium, add it to the runbook in the same PR instead of keeping it only in
  private agent memory.
- This repository is public: keep personal paths, names, and machine details
  out of committed files.
