---
description: >
  ALWAYS invoke this skill when a user wants to produce a written document from
  analysis results in this repository. This skill loads project-specific report
  templates, section-by-section writing guidance, provenance records, and a QC
  checklist that are not available from general knowledge — you cannot write a
  proper report without consulting it first. Trigger for: creating reports,
  writeups, PDFs, or summary documents from completed analyses; drafting methods
  or results sections for papers or manuscripts; producing any document artifact
  the user wants to share, send, or hand off to someone (PI, collaborator,
  team). The user's intent must be to create a downloadable/sharable document —
  not to get a verbal explanation. Do NOT trigger for: answering questions about
  results conversationally, explaining or interpreting findings in chat, creating
  standalone charts or figures without a surrounding document, performing the
  analysis itself, ingesting data, setting up repositories, or brainstorming.
---

# Mycelium — Report

Generate a structured report from an analysis, routing to the appropriate report convention pack installed in this repository.

## Steps

1. **Gather context** from the analysis:
   - Read the analysis documentation file (UPPER_SNAKE_CASE.md) and `specification.md` (if present)
   - Read `.living/decisions.md` for key analytical choices
   - Inventory `outputs/figures/` and `outputs/tables/` for available assets
   - Check `git log` for provenance information
   - Check for sensitivity analysis outputs in `outputs/figures/supplementary/`

2. **Route to installed report conventions**:
   - Read `.living/conventions/ACTIVE_CONVENTIONS.yaml` to see what's installed.
   - **If `report-generator` is installed** (check `.living/conventions/report-generator/`): Follow its `analysis-conventions.md` as the entry point. Use the template from `.living/conventions/report-generator/assets/report-template.tex`. The report structure is: Title, Abstract, TOC, Problem Statement, Methods (Definitions/Overview/Technical Detail), Results, Conclusions, Next Steps, Provenance, Appendix. Consult `references/section-guide.md` for detailed writing guidance per section. Run through `qc-checklist.md` before finalizing.
   - **If other report conventions are installed** (future packs like `internal-memo`, `slide-deck`, etc.): Present the user with the available report styles and let them choose.
   - **If no report convention is installed**: Fall back to core `skills/core/references/writing-conventions.md` and `skills/core/templates/report-template.tex`. The fallback structure is: Title, Abstract, Introduction, Methods (Data/Analysis/Statistical Methods), Results, Discussion, References.

3. **Copy the chosen template** to `analysis/[name]/reports/[name]-report.tex`.

4. **Fill in data-driven sections first** (Problem Statement, Methods, Results):
   - Write these sections by reading actual analysis outputs, figures, and data — not from expectations or hypotheses.
   - For Results: read the actual output files (`.rds`, `.csv`, logs) and figures to extract concrete numbers, statistics, and findings. Never infer or assume what the results "should" show.

5. **Pull figures** from `outputs/figures/` and supplementary figures from `outputs/figures/supplementary/`.

6. **Write interpretive sections last** (Conclusions, Abstract, Next Steps):
   - Write these ONLY after steps 4-5 are complete and you have verified the actual results.
   - Conclusions must be derived from what was written in the Results section, not from the hypothesis or problem statement.
   - The Abstract must summarize the actual findings, not the expected ones.
   - **NEVER template or pre-write conclusions before results are finalized.** If results contradict the original hypothesis, the conclusions must reflect that.

7. **Compile** to PDF:
   ```bash
   cd analysis/[name]/reports/
   pdflatex [name]-report.tex
   pdflatex [name]-report.tex
   ```
   If using BibTeX citations, insert `bibtex [name]-report` between the two passes.

8. **Verify** the PDF renders correctly (no unresolved references, all figures present).

9. **Run post-action protocol**: Update analysis manifest entry with report status. Log any decisions or learnings from the writing process.
