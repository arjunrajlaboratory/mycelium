---
description: >
  Generate a structured report (typically LaTeX PDF) from an analysis in a
  mycelium-enabled repository. Routes to installed report convention packs
  for structure and style guidance. Use when user says "write report",
  "generate report", "create a report", "generate a PDF", "make a LaTeX report",
  or "write up results".
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

4. **Fill in each section** following the chosen structure and writing guidance.

5. **Pull figures** from `outputs/figures/` and supplementary figures from `outputs/figures/supplementary/`.

6. **Compile** to PDF:
   ```bash
   cd analysis/[name]/reports/
   pdflatex [name]-report.tex
   pdflatex [name]-report.tex
   ```
   If using BibTeX citations, insert `bibtex [name]-report` between the two passes.

7. **Verify** the PDF renders correctly (no unresolved references, all figures present).

8. **Run post-action protocol**: Update analysis manifest entry with report status. Log any decisions or learnings from the writing process.
