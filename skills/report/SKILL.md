---
name: report
description: >
  Create a shareable report, PDF, manuscript section, or structured writeup from
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

2. **If `report-generator` is installed** (check `.living/conventions/report-generator/`): follow its `analysis-conventions.md` as the entry point. That file orchestrates the full phase-based flow (planning brief → memory consultation → section outline → manifest → draft → worked-example gate → three blind sub-agent reviewers → recompile + log → optional headline preview). The convention pack carries the three template variants (`overview`, `comprehensive`, `overview-supplement`), the section-by-section craft notes (`references/section-guide.md`), the sub-agent prompts (`references/phase-prompts.md`), and the provenance/style QC checklist (`qc-checklist.md`).

3. **If other report conventions are installed** (future packs like `internal-memo`, `slide-deck`, etc.): present the user with the available report styles and let them choose. Each pack carries its own `analysis-conventions.md` describing its flow.

4. **If no report convention is installed**: fall back to core `skills/core/references/writing-conventions.md` and `skills/core/templates/report-template.tex`. The fallback structure is a single-document Title / Abstract / Introduction / Methods (Data / Analysis / Statistical Methods) / Results / Discussion / References — no phase-based flow, no sub-agent reviewers. This path exists so the skill is usable in a project that has not yet installed the convention pack; suggest installing `report-generator` if the user expects to write more than one report.

## Post-action

After the chosen convention pack's flow completes (or after a fallback draft is finalized), update the analysis manifest entry with the report status and the path to the PDF. Log any decisions or learnings from the writing process to `.living/decisions.md` and `.living/learnings.md` per the core post-action protocol — particularly any new failure modes the sub-agent reviewers surfaced, so the next report can probe for them upfront.

## What this skill is NOT for

- Running the analysis itself — that is `/mycelium:analyze`.
- Reviewing existing code or analysis decisions — that is `/mycelium:review`.
- Open-ended idea generation — that is `/mycelium:ideas`.
- Repo initialisation — that is `/mycelium:core init`.
- Quick chat-based summaries — answer those conversationally without invoking this skill.
