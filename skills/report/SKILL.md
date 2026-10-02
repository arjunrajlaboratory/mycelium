---
name: report
description: >
  Create a shareable report — a PDF, or a self-contained HTML report with a
  companion slide deck — a manuscript section, or a structured writeup from
  completed Mycelium analyses. Load the project's report templates, provenance,
  section guidance, and QC checklist. Use when the user wants a document artifact
  for collaborators or publication. Do not use for conversational explanation,
  standalone figures, performing analysis, ingestion, setup, or brainstorming.
---

# Mycelium — Report

Resolve bundled `skills/` and `network/` paths relative to the Mycelium plugin
root—the ancestor containing `.codex-plugin/` and `.claude-plugin/`. Resolve
reports and `.living/` paths relative to the user's repository.

Generate a structured report from an analysis, routing to the appropriate report convention pack installed in this repository.

This command is a thin router. The substantive workflow — the planning brief, the memory cheatsheet, the manifest, the draft, the three blind sub-agent reviewers, and the recompile gate — lives in the convention pack's own `analysis-conventions.md`. The router's job is to pick the right convention pack and hand off; do not duplicate or summarize the convention's phases here.

## Routing

1. **Read `.living/conventions/ACTIVE_CONVENTIONS.yaml`** to see what is installed.

2. **If `report-generator` is installed** (check `.living/conventions/report-generator/`): first check for staleness — compare the `version:` field of the installed `.living/conventions/report-generator/CONVENTION_PACK.yaml` against the bundled `network/conventions/report-generator/CONVENTION_PACK.yaml` (resolved relative to the plugin root). Plugin updates refresh only the bundled `network/` tree, never the copy installed in `.living/conventions/`, so an upgraded plugin routinely carries a newer pack than the repository is running. If the bundled version is newer, tell the user which versions differ and offer to refresh before drafting — `python skills/core/scripts/install_convention.py --name report-generator` replaces the installed copy in place (ask rather than overwrite silently: installed packs may carry local edits). Next, check the report linter once per report: `python skills/core/scripts/check_linter_versions.py "scitexlintr>=0.2"` reports the installed version and the latest on PyPI and never installs. If it exits 1 (missing or too old), install with the command it prints. If it reports an update, tell the user and upgrade only with their agreement, before drafting rather than mid-report, because a newer linter can add rules. Then follow the installed pack's `analysis-conventions.md` as the entry point. That file orchestrates the full phase-based flow (planning brief → memory consultation → section outline → manifest → draft → worked-example gate → three blind sub-agent reviewers → recompile + log → optional headline preview). The planning brief also picks the output format — LaTeX PDF (default) or one self-contained HTML file holding the report and a 10–20 slide companion deck — and the pack's `html-conventions.md` covers the HTML format end to end (template, value spans, figure and data inlining, the HTML gate, and the Phase 9 slides). The convention pack carries the three TeX template variants (`overview`, `comprehensive`, `overview-supplement`) and the HTML template with a worked example, the section-by-section craft notes (`references/section-guide.md`), the sub-agent prompts (`references/phase-prompts.md`), and the provenance/style QC checklist (`qc-checklist.md`).

3. **If other report conventions are installed** (future packs like `internal-memo`, etc.): present the user with the available report styles and let them choose. Each pack carries its own `analysis-conventions.md` describing its flow.

4. **If no report convention is installed**: fall back to core `skills/core/references/writing-conventions.md` and `skills/core/templates/report-template.tex`. The fallback structure is a single-document Title / Abstract / Introduction / Methods (Data / Analysis / Statistical Methods) / Results / Discussion / References — no phase-based flow, no sub-agent reviewers. This path exists so the skill is usable in a project that has not yet installed the convention pack; suggest installing `report-generator` if the user expects to write more than one report.

## Post-action

After the chosen convention pack's flow completes (or after a fallback draft is finalized), update the analysis manifest entry with the report status and the path to the PDF or HTML file. Log any decisions or learnings from the writing process to `.living/decisions.md` and `.living/learnings.md` per the core post-action protocol — particularly any new failure modes the sub-agent reviewers surfaced, so the next report can probe for them upfront.

## What this skill is NOT for

- Running the analysis itself — that is `/mycelium:analyze`.
- Reviewing existing code or analysis decisions — that is `/mycelium:review`.
- Open-ended idea generation — that is `/mycelium:ideas`.
- Repo initialisation — that is `/mycelium:core init`.
- Quick chat-based summaries — answer those conversationally without invoking this skill.
