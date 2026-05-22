# Section-by-Section Writing Guide

Detailed guidance for writing each section of a computational analysis report. The main conventions file has the structure overview — this file has the craft.

## Table of Contents

1. [Abstract](#abstract)
2. [Problem Statement](#problem-statement)
3. [Methods: Definitions](#methods-definitions)
4. [Methods: Overview](#methods-overview)
5. [Methods: Technical Detail](#methods-technical-detail)
6. [Results](#results)
7. [Conclusions](#conclusions)
8. [Provenance](#provenance)
9. [Appendix](#appendix)
10. [Cross-cutting craft](#cross-cutting-craft)
    - [Acronym discipline](#acronym-discipline)
    - [Intuitive-before-technical](#intuitive-before-technical)
    - [Worked examples per analysis type](#worked-examples-per-analysis-type)
    - [Denominator discipline](#denominator-discipline)
    - [Overloaded-name guard](#overloaded-name-guard)
11. [General Writing Principles](#general-writing-principles)

---

## Abstract

The abstract is the most-read part of any report. Many readers will read only this.

**Structure**: Lead with the question, then the approach (one sentence), then the key finding. End with the implication or next step.

**Audience**: A general technical person who is not in your field. They should understand what you did and why it matters without needing domain expertise.

**Rules**:
- No undefined acronyms — define on first use or avoid entirely
- No figure or table references
- No citations
- State the actual finding, not "results are discussed" — be concrete
- 150-250 words, 1-2 paragraphs

**Example pattern**:
```
[Scientific question in plain language]. To address this, we [approach in one sentence].
We found that [key finding with the most important number]. [Second finding if needed].
These results [implication — what does this mean for the field/project/next step].
```

**Common mistakes**:
- Starting with the method ("We used DESeq2 to...") instead of the question
- Being vague about the finding ("Several genes were differentially expressed") instead of specific ("247 genes were upregulated, enriched for inflammatory pathways")
- Including too much methodological detail

---

## Problem Statement

This section exists so that someone outside the immediate project can understand what you're doing and why. It replaces the traditional "Introduction" with something more focused and accessible.

**What to include**:
- The question, in plain English
- Why the answer matters (what decision does it inform? what understanding does it advance?)
- What a good answer would look like (what would you need to see to be convinced?)
- Every concept a non-specialist needs to follow the rest of the report

**How to write it**:
- Start broad, narrow to specific
- Define terms as you introduce them, not in a separate glossary
- Use concrete examples over abstract descriptions
- If you find yourself writing "it is well known that..." — either cite it or cut it

**Test**: Give this section to someone outside your immediate team. If they can't explain back to you what the report is about, rewrite it.

---

## Methods: Definitions

This subsection is unusual and extremely valuable. Most reports scatter definitions throughout the text or assume the reader knows them. This creates a single reference point.

**What to define**:
- Every mathematical symbol used anywhere in the report (what it represents, units if applicable, range of valid values)
- Every technical term that a smart non-specialist might not know
- Every domain-specific concept (even common ones — "differential expression," "clustering," "normalization")
- Any term used non-standardly — call it out explicitly ("In this report, 'significant' means padj < 0.05 by Benjamini-Hochberg correction, not a subjective judgment of importance")

**Format**:
```latex
\subsection{Definitions}

\textbf{Log2 fold change} ($\log_2 \text{FC}$): The base-2 logarithm of the ratio
of expression between two conditions. A value of 1 means the gene is twice as
expressed in the treatment; $-1$ means half as expressed. Range: $(-\infty, +\infty)$.

\textbf{Adjusted p-value} ($p_{\text{adj}}$): A p-value corrected for multiple
hypothesis testing using the Benjamini-Hochberg procedure. Controls the false
discovery rate (FDR) — the expected proportion of false positives among rejected
hypotheses. Range: $[0, 1]$.
```

**Grouping**: Put related definitions together (all statistical terms, then all biological terms, then all computational terms). Use the order they'll appear in the report.

---

## Methods: Overview

2-3 sentences that a non-specialist can follow. This is the "elevator pitch" for the methodology.

**Example**:
```
We compared gene expression between treatment and control conditions across 12
patient samples using standard RNA-seq differential expression analysis. Genes
with statistically significant changes were tested for enrichment in known
biological pathways to identify the functional impact of treatment.
```

No tool names, no parameters, no version numbers. Those go in Technical Detail.

---

## Methods: Technical Detail

The reproducibility section. Someone should be able to re-run your analysis from this.

**Required elements**:
- **Datasets**: Name, source, size (n samples, n features), any preprocessing applied before your analysis began
- **Software**: Tool name, version, and citation for every tool used (including R/Python version)
- **Parameters**: Every non-default parameter with a rationale. Default parameters should also be stated (defaults change between versions)
- **Statistical methods**: Test name, assumptions checked, correction method, significance threshold
- **Computational environment**: OS, hardware if relevant (GPU for deep learning), runtime for expensive steps

**Rationale for parameters**: Don't just list them — explain why. "We used `min_genes=500` to filter low-quality cells (below the inflection point in the genes-per-cell distribution, Figure S1)" is much more useful than "We used `min_genes=500`."

If parameters were chosen via sensitivity analysis, reference the appendix figure.

---

## Results

Each result should be a self-contained unit that a reader can understand independently.

**The structure for each result**:

1. **Question**: What specific question is this result addressing? ("Does treatment X change the expression of inflammatory genes?")

2. **Discrimination**: What would you expect to see under competing hypotheses? This is the most underused and most valuable part. ("If treatment activates the inflammatory response, we'd expect to see upregulation of known inflammatory markers like IL6 and TNF, and enrichment of the NF-kB pathway. If treatment has no specific inflammatory effect, any differential expression should be distributed across pathways without enrichment.")

3. **Findings**: The actual data — figures, tables, statistics. State the result plainly. ("247 genes were significantly upregulated (padj < 0.05, |log2FC| > 1), with the strongest enrichment in the NF-kB signaling pathway (padj = 2.3e-8, 23/87 pathway genes present).")

4. **Interpretation**: What does this mean for the question? ("This is consistent with treatment activating an inflammatory response via NF-kB signaling.")

This structure is a guideline — some results are simple enough to collapse steps 2 and 4, and complex results might need sub-results. But the question-discrimination-finding-interpretation flow should be the default.

**Figures and tables**:
- Every figure and table must be referenced in the text — if it's not discussed, it shouldn't be there
- Captions must be self-contained: a reader should understand the figure from the caption alone without reading the surrounding text
- Caption pattern: "[What the figure shows]. [How to read it]. [Key takeaway]."
- Example: "Volcano plot of differential expression between treatment and control. Each point is a gene; x-axis shows log2 fold change, y-axis shows -log10 adjusted p-value. Red points exceed both significance thresholds (padj < 0.05, |log2FC| > 1). The 247 significant genes are predominantly upregulated, consistent with treatment-induced activation."

---

## Conclusions

**Rules**:
- Map every conclusion back to the problem statement. If the problem statement asked three questions, the conclusions should address all three (even if the answer is "we couldn't determine this")
- State caveats explicitly: "This analysis does not address...", "A limitation is...", "This result is contingent on..."
- Distinguish what the data shows (direct evidence) from what it suggests (interpretation, extrapolation)
- Do not introduce new results here
- Do not overstate: "is consistent with" not "proves"; "suggests" not "demonstrates" (unless the evidence is truly definitive)

**Pattern**:
```
Our analysis addressed [question from problem statement]. We found [key result],
which [interpretation]. This is subject to [caveat/limitation]. Specifically,
[what the analysis does not address].

[If applicable: This result, combined with [prior knowledge], suggests [broader
implication]. However, [what would need to be true for this interpretation to hold].
```

**CRITICAL — Write order**:
- Conclusions must be the LAST section written, after Results are complete and verified.
- Read back what you wrote in Results before writing this section. Base every conclusion on specific findings you already reported — cite the figure or statistic.
- If results contradict the original hypothesis, say so directly. A null or negative result is a valid conclusion.
- Never write conclusions as a template to be "filled in later" — by the time you write this section, all the data is available. Use it.

---

## Provenance

This section is auto-populated — the agent should fill it programmatically, not write it by hand.

**What to include**:
```latex
\section{Provenance}

\begin{description}
\item[Analysis directory] \texttt{analysis/[name]/}
\item[Key scripts] ~
  \begin{itemize}
    \item \texttt{scripts/01\_preprocess.py} — Data cleaning and QC filtering
    \item \texttt{scripts/02\_analysis.py} — Main differential expression analysis
    \item \texttt{scripts/03\_enrichment.py} — Pathway enrichment
  \end{itemize}
\item[Git commit] \texttt{abc1234} (branch: \texttt{main})
\item[Analysis period] 2024-03-10 to 2024-03-15
\item[Analyst] [Name]
\item[Report generated] \today
\item[Software environment] See \texttt{ENVIRONMENTS\_INSTALLATIONS.md}
\end{description}
```

Pull this information from:
- `git log --oneline` for the analysis directory
- `ls` of the scripts directory
- `git rev-parse HEAD` for the current commit
- `ENVIRONMENTS_INSTALLATIONS.md` for software versions
- The analysis documentation file (UPPER_SNAKE_CASE.md) for the analysis description

---

## Appendix

The appendix is for supplementary material that supports the main narrative but would interrupt it if placed inline. The most common use is sensitivity analysis figures from the robust-analysis convention pack.

**What belongs here**:
- Parameter sweep figures showing how results change across threshold values
- Method comparison figures (e.g., UMAP vs PCA vs t-SNE)
- Subsample stability results
- Alternative visualizations of the same data
- Extended tables too large for the main text
- QC figures (if not already in the results)

**How to structure it**:
```latex
\appendix

\section{Parameter Sensitivity}

\subsection{P-value Threshold Sensitivity}
Figure~\ref{fig:pvalue_sensitivity} shows how the number of significant genes
varies across adjusted p-value thresholds from 0.01 to 0.10. The chosen
threshold of 0.05 falls in a stable region of the curve.

\begin{figure}[H]
    \centering
    \includegraphics[width=0.8\textwidth]{../outputs/figures/supplementary/threshold_sensitivity_pvalue.png}
    \caption{Number of significant genes as a function of the adjusted p-value
    threshold. The dashed red line indicates the chosen threshold (0.05).
    The result is stable across the range 0.03--0.07.}
    \label{fig:pvalue_sensitivity}
\end{figure}
```

Each appendix figure should have:
- A clear title indicating what was varied
- A caption explaining what the figure shows and what conclusion to draw
- A reference from the main text methods section ("Parameter sensitivity is shown in Appendix A")

---

## Cross-cutting craft

The rules below apply across sections. They are checked automatically by the Phase 4 (plain-English lint), Phase 5 (framing critique), and Phase 6 (blind numerical re-verify) sub-agents in the report-generator flow.

### Acronym discipline

The abstract, section titles, and figure captions never carry an undefined acronym. A "BCLRT" in the abstract is a finding — even when the term is defined in Methods later.

Rules:

- **Cap at 4 acronyms per page** (heuristic). A methods section that defines `BCLRT` once and uses it 40 times is fine; what triggers the flag is a sentence stacking three or four undefined acronyms.
- **Spell out the underlying concept on first use *per section*** (not per document). Even when an acronym has been defined earlier, the next section that uses it gets a brief plain-English gloss. Sections are independent surfaces; a reader landing on §3 should not have to walk back to §2.1.
- **No acronyms in section titles or figure captions.** Both of these are skim surfaces and a reader scanning the TOC or flipping through figures should not have to look terms up to decide whether to read further.
- **No acronyms in the abstract** without spelling out the underlying concept on first mention. "We compared a per-cell logistic regression (the branch-coherency log-likelihood ratio test, BCLRT) to ..." is acceptable; "We compared BCLRT to ..." is not.

The per-page budget is a flag for human review, not a hard constraint — it works better as a probe ("does this page feel heavy?") than as a count to enforce mechanically.

### Intuitive-before-technical

For load-bearing methodological choices, non-obvious metrics, and surprising results, **lead with the intuitive explanation before the technical statement**. The length is judged by complexity, not by a fixed rule:

| Concept type | Lead-in form |
|---|---|
| Complex analytic method (new framework, multi-step procedure) | Full intuition paragraph before the formal definition |
| Non-obvious metric or transform | Single intuitive sentence before the formula |
| Routine technical detail (standard test, well-known transform) | No lead-in needed |
| Surprising result | Plain-English summary of what the reader should walk away with, before the table or figure |

Example:

> **Without intuitive lead-in (bad).** *We define BCLRT as `dLL = LL_branch - LL_null` for a per-cell logistic regression with two covariates: `log(1 + total panel coverage)` and an artifact-load covariate `u_i`.*
>
> **With intuitive lead-in (good).** *We score each cell by asking: does this cell's pattern of alternative-allele reads look more like the proposed clone-of-origin or like a uniform background? We use a per-cell logistic regression — with controls for coverage and an artifact-load covariate — and report the log-likelihood difference between the branch model and the null. Formally, BCLRT = LL_branch − LL_null (note: this is not the Wilks chi-square statistic; threshold 10 here corresponds to Wilks 20).*

The plain-English lint sub-agent (Phase 4) flags load-bearing concepts that are introduced technical-first. It does not enforce a sentence count.

### Worked examples per analysis type

For every *new aggregation analysis type* introduced in the report, the main body contains one fully-traced concrete example: pick a single SNP / cell / capsule / row, show its raw inputs, the per-row metric calculation, and how it contributes to the aggregate. The example does the explanatory work once — subsequent claims using the same analysis type don't need their own example.

Format preference: small inline figures or sparkline-style mini-tables over big monolithic figures. Tufte sparklines are a strong reference shape. A five-row inline table with raw N, Y, score, rank, contribution is often clearer than a full plot.

Example pattern:

```latex
\begin{table}[H]
    \centering
    \caption{Worked example: how a single SNP's per-clone score contributes
    to its aggregate rank. Cell \texttt{c\_017} is shown; the full ranking
    aggregates over all 1{,}231 SNPs.}
    \label{tab:worked_example_snp_score}
    \begin{tabular}{lrrrrr}
        \toprule
        Clone & N & Y & VAF & score & rank \\
        \midrule
        c1 & 12 & 6 & 0.50 & 4.81 & 1 \\
        c2 & 9 & 1 & 0.11 & 0.42 & 7 \\
        c3 & 11 & 0 & 0.00 & 0.00 & 9 \\
        \bottomrule
    \end{tabular}
\end{table}
```

**Failure modes** (cases where the analysis breaks) get worked examples too, but those live in the supplement / appendix. The main text shows one healthy example per analysis type; the supplement shows the contrasting failure case.

The Phase-3 worked-example gate also catches the inverse failure (single-example-as-proof): "confirmed" / "verified" / "established" claims that rest on one example without hedging. Both failures share a root cause — sloppy handling of the relationship between concrete instances and general claims — so the same phase checks for both.

### Denominator discipline

Every count or rate in the prose has its denominator within one sentence.

- *Bad.* "Consensus floor of 5."
- *Better.* "Consensus floor of 5 (out of 25 operating-point sweep conditions)."

For cross-universe comparisons, also report the universe-eligible-restricted denominator:

- *Bad.* "baseline_band sticky=1, current_iter sticky=3."
- *Better.* "baseline_band sticky=1 of 1 eligible (X21 and X19 are structurally excluded from baseline_band); current_iter sticky=3 of 3 eligible."

Bare percentages are stated alongside the count (or vice versa) so the reader does not have to back out the denominator from rounding.

### Overloaded-name guard

Any coined term that overlaps an established statistics / genomics / ML term needs an explicit "this is NOT the standard X" disclaimer at first use, naming the specific way it differs.

Common overloads:

- **"Log-likelihood ratio test"** when the statistic is `dLL = LL_branch - LL_null` without the factor of 2 (so a threshold of 10 corresponds to Wilks 20, not Wilks 10).
- **"Penalised log-likelihood ratio"** that a reader trained on Wilks chi-square statistics will mis-extrapolate.
- **`cond_p`** for an ordinal score that looks like a per-SNP p-value but is not.
- **"Validation"** when the validation cohort is downstream of the same upstream labelling decisions (so agreement is a self-consistency check, not external validation).

When an overloaded name is used, the manifest's `terms[*].overloaded_warning` field is mandatory.

---

## General Writing Principles

These apply throughout the report:

- **Past tense** for methods and results ("We computed...", "The analysis showed..."). Present tense for statements that are currently true ("Figure 3 shows...", "These results suggest...").
- **Active voice** when possible ("We filtered cells with fewer than 500 genes" not "Cells with fewer than 500 genes were filtered").
- **Precision over hedging**: "23% of genes were upregulated" not "a significant proportion of genes were upregulated." Use numbers.
- **One idea per paragraph**. If a paragraph covers two topics, split it.
- **Define before use**. Every abbreviation, every technical term, every symbol — define it before or at first use.
- **Concrete over abstract**. "Expression of TP53 increased 3.2-fold" not "There was a notable change in expression levels."
- **Figures do the heavy lifting**. The text should guide the reader through the figures, not duplicate them. "As shown in Figure 2, treatment increased IL6 expression 4.1-fold..." not "Treatment increased IL6 expression 4.1-fold (Figure 2 shows a bar plot of expression levels...)."
