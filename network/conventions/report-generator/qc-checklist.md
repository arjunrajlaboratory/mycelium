# Report QC Checklist

Run before finalizing any report PDF.

## Compilation

- [ ] **pdflatex** completes without errors (warnings are OK to review but not block on)
- [ ] **Two passes** run (TOC and cross-references resolved)
- [ ] **No "??"** in the rendered PDF (unresolved references)
- [ ] **All figures** render (no missing file errors)
- [ ] **BibTeX** pass run if citations are used

## Content

- [ ] **Abstract** leads with the question, not the method
- [ ] **Abstract** has no undefined acronyms
- [ ] **Problem statement** is understandable by a non-specialist
- [ ] **Definitions** section covers every symbol and technical term used in the report
- [ ] **Methods overview** is 2-3 sentences with no tool names
- [ ] **Methods detail** includes software versions and parameter rationale
- [ ] **Every result** states the question being tested
- [ ] **Every figure/table** is referenced in the text
- [ ] **Every figure/table** has a self-contained caption
- [ ] **Conclusions** map back to the problem statement
- [ ] **Caveats** are stated explicitly
- [ ] **Provenance** section is populated (git hash, scripts, dates)

## Formatting

- [ ] **Gene names** in italics (`\textit{}`)
- [ ] **Special characters** escaped (`_`, `%`, `&`, `#`, `$`)
- [ ] **Tables** use booktabs (no vertical lines, no `\hline`)
- [ ] **Figures** have readable labels at printed size
- [ ] **Cross-references** use `~` before `\ref{}` (no line breaks before numbers)

## Appendix (if present)

- [ ] **Sensitivity figures** referenced from main text methods section
- [ ] **Each figure** has a caption explaining what was varied and the conclusion
- [ ] **Appendix** uses `\appendix` command for correct numbering
