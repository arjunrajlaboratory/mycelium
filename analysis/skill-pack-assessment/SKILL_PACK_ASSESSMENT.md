# Skill Pack Assessment: Integrating scientific-agent-skills and bioSkills into Mycelium

**Date**: 2026-04-11
**Status**: draft
**Purpose**: Assess two external skill packs for integration into mycelium as convention-backed workflows, propose architecture, and design a first validation test.

---

## 1. Executive Summary

Two major open-source skill pack repositories are available for integration into mycelium's convention system:

| | **scientific-agent-skills** (K-Dense) | **bioSkills** |
|---|---|---|
| **Skills** | 135 across 18 domains | 439 across 63 categories |
| **Scope** | Broad scientific (bio, chem, physics, ML, geospatial, clinical, quantum) | Deep bioinformatics (genomics, transcriptomics, epigenomics, proteomics, clinical) |
| **Format** | Agent Skills standard (`SKILL.md` + `references/` + `scripts/`) | Similar (`SKILL.md` + `usage-guide.md` + `examples/`) |
| **Platforms** | Claude Code, Cursor, Codex, Gemini CLI | Claude Code, Codex, Gemini CLI, OpenClaw |
| **Install** | `npx skills add K-Dense-AI/scientific-agent-skills` | `./install-claude.sh` with selective category support |
| **Philosophy** | Breadth-first, multi-domain, database access | Depth-first, bioinformatics accuracy, version-resilient code patterns |

**Key insight**: These repos are complementary, not competing. scientific-agent-skills provides breadth (database access, ML frameworks, scientific communication, lab automation) while bioSkills provides depth (439 skills covering every bioinformatics subdomain with version-pinned code patterns). Together they cover nearly every computational biology workflow a researcher would encounter.

### What the Skill Packs Actually Provide

To be precise about what these repos are and aren't: they are **expert-encoded implementation knowledge** for AI agents. This is more than just API docs, but less than methodology. Specifically:

| What they provide | Example |
|---|---|
| **Correct API signatures** | "Use `lfcShrink(dds, coef=..., type='apeglm')`, not `results()`" |
| **Code patterns** | Copy-paste-ready workflows for common tasks |
| **Tool selection decision trees** | "For < 10 samples use edgeR; for > 10 use DESeq2" |
| **Common errors and fixes** | "If ValueError on SeqIO.parse, check encoding" |
| **Version compatibility** | "This pattern works with Scanpy 1.9+; verify API if older" |
| **Performance tips** | "SimpleFastaParser is 3-6x faster than SeqIO.parse" |

| What they do NOT provide | Why it matters |
|---|---|
| **Scientific judgment** | No guidance on whether results are biologically plausible |
| **QC methodology** | Thresholds listed but no framework for choosing/documenting them |
| **Defensive analysis practices** | No validation sweeps, no adversarial probing of conclusions |
| **Decision documentation** | No framework for recording why choices were made |
| **Disciplinary thinking patterns** | No perspective on how a statistician vs. a biologist would evaluate the same output |
| **Cross-project learning** | Each analysis is isolated; no pattern accumulation |

This creates a clear three-layer stack that our convention system should address:

```
Layer 3: PERSONAS (Autonomous-Science)
  "How would scientist Y think about this problem?"
  → Scientific judgment, disciplinary lenses, adversarial review

Layer 2: CONVENTIONS (mycelium)
  "What defensive practices to follow during analysis?"
  → QC frameworks, sensitivity sweeps, decision documentation, learning capture

Layer 1: SKILLS (bioSkills, scientific-agent-skills)
  "How to write correct code for tool X?"
  → API patterns, tool selection, version-safe code, performance
```

---

## 2. Overlap Analysis

### 2.1 Significant Overlap Areas

These domains have substantial coverage in both repos:

| Domain | scientific-agent-skills | bioSkills | Notes |
|--------|------------------------|-----------|-------|
| Single-cell RNA-seq | scanpy, scvi-tools, anndata, scvelo, cellxgene-census | 14 skills (Seurat, Scanpy, Pertpy, Cassiopeia, MeboCost) | bioSkills deeper (lineage tracing, metabolite comm.) |
| Differential expression | pydeseq2 | 6 skills (DESeq2, edgeR, visualization, results) | bioSkills covers R-native DESeq2; sci-skills covers Python pydeseq2 |
| Variant calling | pysam | 13 skills (GATK, DeepVariant, bcftools, Manta, Delly) | bioSkills far deeper |
| Pathway analysis | (via bioservices, networkx) | 6 skills (clusterProfiler, ReactomePA, GSEA) | bioSkills dedicated skills vs. general tools |
| Structural biology | esm, biopython, diffdock, openmm, mdanalysis | 6 skills (Bio.PDB, ESMFold, Chai-1) | sci-skills broader (MD, docking); bioSkills has newer models |
| Proteomics | pyopenms, matchms, pathml | 9 skills (pyOpenMS, limma, DEqMS, QFeatures) | bioSkills deeper on quantitative proteomics |
| Cheminformatics | rdkit, datamol, deepchem, medchem, molfeat | 7 skills (RDKit, DeepChem, AutoDock Vina) | sci-skills has more tools; bioSkills has virtual screening |
| Phylogenetics | etetoolkit, phylogenetics | 8 skills (IQ-TREE2, RAxML-NG, BEAST2) | bioSkills covers Bayesian methods |
| Clinical genomics | clinical-decision-support, clinical-reports | 10 skills (ClinVar, gnomAD, pharmacogenomics) | Complementary: sci-skills for docs, bioSkills for databases |

### 2.2 Unique to scientific-agent-skills

- **Database access** (78+ databases via single skill) -- uniquely powerful
- **Lab automation** (Opentrons, Benchling, protocols.io, LabArchive)
- **Scientific communication** (writing, posters, slides, peer review, grants)
- **Quantum computing** (Qiskit, Cirq, PennyLane, QuTiP)
- **Geospatial science** (GeoPandas, rasterio, geomaster)
- **Financial/economics** (FRED, Treasury data)
- **General ML frameworks** (PyTorch Lightning, stable-baselines3, transformers)
- **Research methodology** (hypothesis generation, critical thinking, brainstorming)

### 2.3 Unique to bioSkills

- **Epigenomics** (ChIP-seq, ATAC-seq, methylation, Hi-C, CLIP-seq -- 25 skills)
- **Spatial transcriptomics** (Squidpy, SpatialData, Visium, Xenium -- 11 skills)
- **Microbial/ecological genomics** (metagenomics, microbiome, eDNA -- 29 skills)
- **Long-read sequencing** (Dorado, minimap2, Clair3, modkit -- 8 skills)
- **CRISPR screens** (MAGeCK, CRISPResso2, guide design -- 13 skills)
- **Flow cytometry** (flowCore, CATALYST, gating -- 8 skills)
- **Liquid biopsy** (cfDNA, fragmentomics, ctDNA -- 6 skills)
- **Alternative splicing** (rMATS, SUPPA2 -- 6 skills)
- **End-to-end workflows** (41 curated multi-step pipelines)
- **Version-resilient code patterns** (Goal/Approach structure surviving package updates)

### 2.4 Integration Strategy

Rather than choosing one, the convention system should:
1. **Route by depth**: For bioinformatics tasks, prefer bioSkills (deeper coverage, version-pinned). For cross-domain or infrastructure tasks, prefer scientific-agent-skills.
2. **Combine for workflows**: A single-cell analysis might use bioSkills for the core Scanpy pipeline but scientific-agent-skills' `cellxgene-census` for reference data and `scientific-visualization` for final figures.
3. **Let conventions arbitrate**: The convention pack decides *which* skill to invoke based on the specific task context, not the agent.

---

## 3. Autonomous-Science Personas: The Methodology Layer

### 3.1 What the Personas Are

The `skillpacks/Autonomous-Science` repository contains **50 researcher personas** — methodologies distilled from publication records of leaders across scientific fields. Each persona is a structured JSON profile synthesized from papers, grants, lab websites, citation patterns, and academic lineage using a 10-phase pipeline (AutoPersona v2).

These are not biographical sketches. They are **operational instruments** designed to steer an AI agent to think and decide like a specific researcher. The key operational fields are:

| Field | Purpose | Example (Arjun Raj persona) |
|---|---|---|
| **`prompt_fragment`** (150-200 words) | Second-person thinking pattern injected into system prompt | "You approach science as a detective of cellular heterogeneity..." |
| **`decision_rules`** (5-8 rules) | "When X, do Y" directives derived from publication record | "When choosing between bulk and single-cell data, always prefer single-cell resolution" |
| **`anti_patterns`** (3-5 rules) | Hard "NEVER" constraints | "NEVER average over cell-to-cell variability without first examining the full distribution" |
| **`key_vocabulary`** (10-15 terms) | Domain jargon for authentic voice | "single-molecule RNA FISH", "clone tracing", "transcriptional bursting" |

Additionally, each persona has quantified research style dimensions (breadth, creativity, risk appetite, boldness, speculation tendency, quantitative emphasis) and question-type distributions (mechanistic vs. descriptive vs. translational vs. methodological).

### 3.2 Persona Inventory (50 researchers)

Organized by the role they could play in mycelium convention workflows:

**Statistical/Methodological Rigor** (ideal as analysis reviewers):
| Persona | Institution | Core Methodology | Key Anti-Pattern |
|---|---|---|---|
| Andrew Gelman | Columbia | Bayesian multilevel models, MRP | "NEVER accept p-values as sufficient evidence" |
| John Ioannidis | Stanford | Meta-analysis, excess significance testing | "NEVER accept p between 0.01-0.05 as strong evidence" |
| Emmanuel Candès | Stanford | Compressed sensing, knockoffs | Statistical elegance + distribution-free inference |
| Daniela Witten | UWash | Penalized regression, selective inference | Sparse modeling, post-selection inference |
| Bin Yu | Berkeley | Stability, veridical data science | PCS framework (predictability, computability, stability) |
| Judea Pearl | UCLA | Causal inference, DAGs, do-calculus | "NEVER treat association as causation without DAG" |

**Single-Cell & Genomics** (ideal for scRNA-seq workflow guidance):
| Persona | Institution | Core Methodology | Key Decision Rule |
|---|---|---|---|
| Arjun Raj | UPenn | Single-molecule FISH, clone tracing | "Always prefer single-cell/single-molecule resolution" |
| Cole Trapnell | UWash | Sci-RNA-seq, trajectory analysis, Monocle | "Choose scale over depth; multiplex hundreds of conditions" |
| Rahul Satija | NYU | Seurat, multimodal integration | Reference-based annotation, integration methods |
| Lior Pachter | Caltech | Kallisto/bustools, RNA velocity math | Rigorous quantification, mathematical foundations |
| Howard Chang | Stanford | ATAC-seq, chromatin accessibility | Epigenomic profiling, regulatory element mapping |
| Xiaowei Zhuang | Harvard | MERFISH, spatial transcriptomics | Spatial resolution, imaging-based methods |

**Systems & Network Biology** (ideal for pathway/network analysis):
| Persona | Institution | Core Methodology | Key Perspective |
|---|---|---|---|
| Daphne Koller | Stanford/insitro | Probabilistic graphical models | Structure learning from data |
| Michael Elowitz | Caltech | Synthetic circuits, stochastic gene expression | Noise as information, not nuisance |
| Simon Levin | Princeton | Ecological modeling, complex systems | Multi-scale dynamics, emergent properties |
| Steven Strogatz | Cornell | Nonlinear dynamics, network science | Coupled oscillators, synchronization |
| Geoffrey West | Santa Fe | Scaling laws, allometry | Universal scaling relationships |

**Protein & Structural Biology**:
| Persona | Institution | Core Methodology |
|---|---|---|
| David Baker | UWash | De novo protein design, RFdiffusion (Nobel 2024) |
| Eva Nogales | Berkeley | Cryo-EM, structural determination |
| Carolyn Bertozzi | Stanford | Bioorthogonal chemistry, glycobiology |

**Translational & Clinical**:
| Persona | Institution | Core Methodology |
|---|---|---|
| Eric Topol | Scripps | Digital medicine, AI in healthcare |
| Carl June | UPenn | CAR-T therapy, immunotherapy |
| Atul Butte | UCSF | Clinical informatics, drug repurposing |
| Pardis Sabeti | Harvard | Genomic epidemiology, outbreak tracking |

**Additional researchers**: Andrea Liu (soft matter physics), Bill Hillier (space syntax), Cesar Hidalgo (economic complexity), Cris Moore (computational complexity), David Blei (topic models/VI), David Hogg (astronomical statistics), David Sinclair (aging biology), Deepak Srivastava (cardiac regeneration), Gerald Shulman (metabolic disease), Gunnar Carlsson (topological data analysis), Hadley Wickham (tidy data), Hilary Finucane (genetic epidemiology), Ian Hodder (archaeology), Judith Campisi (cellular senescence), Lakshminarayanan Mahadevan (biophysics), Mark Daly (human genetics), Matthias Mann (proteomics), Michael Batty (urban science), Nigel Goldenfeld (statistical physics), Peter Turnbaugh (microbiome), Sarah Otto (evolutionary theory), Tim Ingold (anthropology), Yann LeCun (deep learning).

### 3.3 How Autonomous-Science Uses Personas

The pipeline uses personas at multiple stages:

1. **Hypothesis Generation**: Persona's question-type distribution steers toward mechanistic vs. translational focus
2. **Approach Design**: Preferred techniques guide method selection
3. **Analysis**: Analytical frameworks determine statistical methods
4. **PI Interpretation Roundtable**: Panel of personas debate biological interpretation of results (not statistics — biology). Includes adversarial tests: "What would disprove this?", "What else explains it?"
5. **Adversarial Review**: 5 field-specific personas assigned as reviewers with 8 failure-mode checks
6. **Multi-Team Mode**: Personas distributed across independent teams; each runs the full pipeline, then findings are reconciled

The system also includes 3 **standing PI archetypes** always available:
- **Dr. Elena Vasquez** — Mechanistic biologist (pathways, networks, causality)
- **Dr. James Chen** — Translational researcher (clinical significance, drug targets)
- **Dr. Sarah Okafor** — Systems biologist (multi-scale integration, emergent properties)

### 3.4 What Personas Add to the Convention Stack

Personas fill the gap between defensive conventions (mycelium) and correct implementation (skills):

| Without Personas | With Personas |
|---|---|
| Sensitivity sweep runs mechanically | Sweep guided by what a domain expert would consider important parameters |
| QC thresholds from generic defaults | Thresholds informed by what experts in this cell type/assay would expect |
| Results described statistically | Results interpreted biologically through disciplinary lens |
| Adversarial probing is generic | Adversarial probing asks discipline-specific questions ("Is this a doublet artifact?" vs. "Is this a batch effect?") |
| One perspective | Multiple perspectives debating the same result |
| Conclusions are conservative boilerplate | Conclusions reflect the boldness/caution profile of the reviewing persona |

### 3.5 How to Integrate Personas into Mycelium Conventions

The convention pack should support two persona usage modes:

**Mode 1: Reviewer Personas** — After analysis, invoke 1-3 personas to review the outputs. The convention specifies which persona types are appropriate for which workflow:
- scRNA-seq → Arjun Raj + Cole Trapnell + Andrew Gelman
- Bulk RNA-seq DE → John Ioannidis + Lior Pachter
- Protein design → David Baker + Carolyn Bertozzi
- Clinical genomics → Eric Topol + Pardis Sabeti

**Mode 2: Collaborator Personas** — During analysis, inject a persona's decision rules and anti-patterns into the agent's system prompt. This steers methodology choices without the overhead of a full roundtable.

Both modes are lightweight: a persona JSON is ~2KB, and only `prompt_fragment` + `decision_rules` + `anti_patterns` + `key_vocabulary` need to be loaded (~300 words total per persona).

---

## 4. Proposed Architecture: Mycelium Skill Bridge Conventions

### 4.1 Design Principles

1. **Three layers, cleanly separated.** Skills = correct code. Conventions = defensive methodology. Personas = scientific judgment. No layer duplicates another.

2. **Progressive disclosure is non-negotiable.** With 574 combined skills and 50 personas, loading everything kills context. The convention hub file must be < 200 lines and route to detail files only when needed.

3. **Repos are inert, conventions are the only trigger.** Skill repos live on disk as plain git clones — never installed as agent skill packs, so they produce zero context cost and no trigger pollution. The convention is the sole routing layer, pointing the agent to read a specific SKILL.md file only when that step needs it.

4. **Persona injection is lightweight.** A persona's operational fields (prompt_fragment + decision_rules + anti_patterns + key_vocabulary) are ~300 words. Loading 2-3 personas for review costs ~900 words — negligible compared to loading even one unnecessary skill pack.

5. **Mycelium's robust-analysis conventions always apply.** Every skill invocation passes through strict execution, validation checks, and sensitivity analysis.

### 4.2 Convention Pack Structure

```
network/conventions/skill-bridge/
├── CONVENTION_PACK.yaml
├── analysis-conventions.md           # Hub: < 200 lines, routes to detail files
├── qc-checklist.md                   # Skill-output validation checklist
├── skill-routing.md                  # Decision tree: which skill pack for which task
├── persona-routing.md                # Which personas to invoke for which workflows
├── references/
│   ├── single-cell-workflow.md       # Step-by-step with skill + persona routing
│   ├── bulk-rnaseq-workflow.md
│   ├── variant-analysis-workflow.md
│   ├── drug-discovery-workflow.md
│   └── [domain]-workflow.md
└── templates/
    ├── skill-invocation-log.md       # Template for logging which skills were used
    ├── persona-review-log.md         # Template for recording persona review feedback
    └── validation-report.md          # Template for post-skill QC
```

### 4.3 The Hub File Pattern (Progressive Disclosure)

The `analysis-conventions.md` hub should follow this pattern:

```markdown
# Skill Bridge Conventions

## Skill Routing

Before invoking external skills, determine which pack to consult:

| Task Domain | Primary Pack | Fallback Pack |
|-------------|-------------|---------------|
| Bioinformatics (seq, expression, variants) | bioSkills | scientific-agent-skills |
| Database queries (PubChem, ChEMBL, FRED) | scientific-agent-skills | — |
| ML/DL frameworks | scientific-agent-skills | — |
| Scientific writing/communication | scientific-agent-skills | — |
| Lab automation | scientific-agent-skills | — |

## Persona Review Protocol

After completing analysis, invoke domain-appropriate personas as reviewers:
> See [persona-routing.md] for which personas to use per workflow type.

Two modes:
- **Collaborator mode**: Inject persona decision_rules during analysis
- **Reviewer mode**: After analysis, run persona-guided adversarial review

## Workflow Entry Points

For specific workflows, consult the detail file:
> See [references/single-cell-workflow.md] for scRNA-seq
> See [references/bulk-rnaseq-workflow.md] for bulk RNA-seq

## Skill Invocation Protocol

1. **Before invoking**: Check that input data passes the relevant QC checklist
2. **During execution**: Apply robust-analysis strict execution rules
3. **After completion**: Validate outputs against expected schema
4. **Review**: Run persona review if workflow specifies it
5. **Record**: Log skill used, parameters, persona feedback, and surprises to .living/
```

### 4.4 Workflow Detail File Pattern (with Persona Integration)

Each workflow file maps analysis steps to specific skills AND specifies persona checkpoints:

```markdown
# Single-Cell RNA-seq Workflow

## Persona Panel for This Workflow
- **Primary collaborator**: Arjun Raj (single-cell heterogeneity, clone tracing)
- **Methods reviewer**: Cole Trapnell (scale, trajectory, platform engineering)
- **Statistical reviewer**: Andrew Gelman (Bayesian, multilevel, uncertainty)
- Load: prompt_fragment + decision_rules + anti_patterns from each persona JSON

## Step 1: Data Loading
- **bioSkills**: single-cell/data-io (Scanpy/Seurat I/O)
- **sci-skills**: anndata (if working with .h5ad directly)
- **Validation**: Check cell count matches expected, no empty genes

## Step 2: QC Filtering
- **bioSkills**: single-cell/quality-control
- **Convention override**: Use thresholds from bioinformatics QC checklist,
  not defaults. Document thresholds in decisions.md.
- **Persona check (Raj)**: "NEVER average over cell-to-cell variability
  without first examining the full distribution" — before filtering,
  plot full distributions, don't just apply cutoffs.
- **Sensitivity**: Sweep min_genes (200, 300, 500) and max_mito (5%, 10%, 20%)

## Step 3: Normalization & Integration
...

## Step N: Post-Analysis Persona Review
- Run adversarial review using the persona panel
- Each persona evaluates results through their lens:
  - Raj: "Are we losing important heterogeneity by clustering too coarsely?"
  - Trapnell: "Would multiplexing more conditions reveal more?"
  - Gelman: "Are the uncertainty intervals on these DE results reasonable?"
- Log feedback in .living/decisions.md
```

### 4.5 How Progressive Disclosure Works in Practice

```
Agent receives task: "Analyze this 10x scRNA-seq dataset"

1. Agent reads analysis-conventions.md (hub, <200 lines)
   → Routes to: bioinformatics domain, single-cell workflow

2. Agent reads references/single-cell-workflow.md (~400 lines)
   → Gets step-by-step with skill routing + persona panel per step
   → Loads persona operational fields (~900 words for 3 personas)

3. For Step 1, agent loads ONLY:
   - bioSkills single-cell/data-io SKILL.md (~150 lines)
   - NOT all 439 bioSkills skills

4. After Step 1, agent moves to Step 2, loads:
   - bioSkills single-cell/quality-control SKILL.md
   - Bioinformatics QC checklist (already installed in .living/conventions/)
   - Applies Raj persona anti-pattern: examines full distributions before filtering

5. Total context used: ~1,100 lines across 5 files + 3 persona fragments
   vs. naive approach: 50,000+ lines loading all skills
   → ~45x reduction in context usage
```

---

## 5. Implementation Plan

### Phase 1: Skill Bridge Convention Pack

Create `network/conventions/skill-bridge/` with:
- Hub file routing to both skill packs and persona library
- Persona routing table (which personas for which workflows)
- Single-cell workflow as the first detailed workflow (with persona panel)
- QC checklist extending the existing bioinformatics QC checklist with skill-specific validations
- Skill routing decision tree

### Phase 2: Test Case Validation

Run a controlled before/after test (see Section 6 below). Three conditions:
1. Skills only (no conventions, no personas)
2. Skills + conventions (mycelium defensive practices)
3. Skills + conventions + personas (full stack)

### Phase 3: Expand Workflow Coverage

Add workflow detail files for:
- Bulk RNA-seq with Ioannidis + Lior Pachter persona review
- Variant analysis with Pardis Sabeti + Mark Daly personas
- Drug discovery with David Baker + Carolyn Bertozzi personas
- Multi-omics integration with Daphne Koller persona

### Phase 4: Knowledge Feedback Loop

As conventions are used in real projects:
- Learnings accumulate about which skills and personas work well together
- Persona review feedback crystallizes into new conventions
- Community contributions add new workflow files and persona assignments

---

## 6. First Test Case: Single-Cell RNA-seq with 10x PBMC Data

### 6.1 Why This Test

Single-cell analysis is the ideal first test because:
- Both skill packs have strong coverage
- 10x Genomics provides free, small, well-characterized datasets
- The workflow has clear QC checkpoints where conventions add value
- Common failure modes are well-known (making before/after differences measurable)
- The existing bioinformatics convention pack already has single-cell QC thresholds

### 6.2 Dataset

**10x Genomics PBMC 3k** (or PBMC 8k):
- ~3,000 peripheral blood mononuclear cells
- Well-characterized cell types (T cells, B cells, monocytes, NK cells, dendritic cells)
- Small enough to run quickly (~15 min on a laptop)
- Available at: 10x Genomics datasets page (filtered gene-barcode matrix)
- Can be loaded directly via `scanpy.datasets.pbmc3k()` or downloaded as .h5 file

### 6.3 Before/After Design (Three Conditions)

**Condition 1: Skills only (no conventions, no personas)**

Run the analysis using only the raw skill packs. Expected failure modes:
- Default QC thresholds applied without documentation or justification
- No sensitivity analysis on clustering resolution
- No doublet detection
- Gene IDs not standardized
- No validation that cluster markers make biological sense
- No adversarial testing of conclusions
- No record of decisions

**Condition 2: Skills + conventions (mycelium, no personas)**

Add mycelium's defensive analysis practices. Expected improvements over Condition 1:
- QC thresholds documented with rationale in decisions.md
- Sensitivity sweep on resolution parameter (0.2, 0.5, 0.8, 1.0, 1.5)
- Doublet detection run and documented
- Gene identifier mapping documented
- Strict execution mode (assertions on shapes, row counts)
- Full .living/ trail: decisions, learnings, QC results
- Reproducible run.sh with pinned seeds

**Condition 3: Skills + conventions + personas (full stack)**

Add persona-guided review. Expected improvements over Condition 2:
- **Raj persona**: Forces examination of full gene expression distributions before applying cutoffs; flags if heterogeneity is being averaged away; asks whether rare cell states are being lost
- **Trapnell persona**: Asks whether the analysis could scale (multiplex more conditions); checks trajectory inference assumptions; evaluates whether resolution is sufficient
- **Gelman persona**: Questions whether uncertainty on DE results is properly quantified; flags garden-of-forking-paths risks in parameter choices; insists on priors for Bayesian methods if used
- Persona disagreements logged as distinct viewpoints in findings, not averaged
- Post-analysis roundtable generates testable sub-hypotheses

### 6.4 Measurable Outcomes

| Metric | Condition 1 (Skills) | Condition 2 (+Conventions) | Condition 3 (+Personas) |
|--------|---------------------|---------------------------|------------------------|
| QC thresholds documented | No | Yes | Yes, with distributional justification |
| Sensitivity analysis | No | Yes, swept | Yes, swept + persona-guided parameter choice |
| Doublet detection | Likely skipped | Run, rate documented | Run, biological interpretation of doublets |
| Cell type validation | Labels assigned | Cross-referenced markers | Multi-expert validation with dissent logged |
| Decisions logged | 0 | 5-10 entries | 10-15 entries (includes persona feedback) |
| Learnings captured | 0 | 3-5 entries | 5-8 entries (includes cross-disciplinary insights) |
| Sub-hypotheses generated | 0 | 0 | 3-5 testable follow-ups from roundtable |
| Adversarial challenges | 0 | Generic (from robust-analysis) | Domain-specific (from persona anti-patterns) |

### 6.5 Test Execution Plan

```
1. Initialize a test project:
   mycelium init test-skill-bridge

2. Install conventions:
   - robust-analysis (core, auto-installed)
   - bioinformatics (domain)
   - skill-bridge (new, from this work)

3. Ingest the PBMC 3k dataset:
   /mycelium:ingest → downloads and registers in DATA_MANIFEST.md

4. Run Condition 1 (skills only):
   - Use only scanpy skill directly, no convention loading
   - Record what the agent produces
   - Save outputs

5. Run Condition 2 (skills + conventions):
   - Same dataset, same question
   - Convention-guided workflow active
   - Record all .living/ entries

6. Run Condition 3 (skills + conventions + personas):
   - Same dataset, same question
   - Convention-guided + persona panel (Raj, Trapnell, Gelman)
   - Record persona review feedback and sub-hypotheses

7. Compare across all three conditions:
   - Document quality differences at each layer
   - Measure convention coverage (checklist items hit)
   - Assess whether persona feedback identified genuine issues
   - Identify the value-add at each layer
```

---

## 7. Structural Considerations

### 7.1 Where Do the Skill Packs and Personas Live?

**Critical design decision: Download repos, do NOT install as skill packs.**

If you install bioSkills and scientific-agent-skills as agent skill packs (via `npx skills add` or `./install-claude.sh`), every one of the 574 combined skills becomes a trigger candidate. The agent evaluates hundreds of potential skill matches on every prompt, wasting context and creating confusion from overlapping triggers.

Instead, the repos should exist as **inert code on disk** — cloned via `git clone`, never installed. They have zero context cost at rest. The mycelium conventions then act as the sole routing layer, surgically reading only the specific SKILL.md file needed for the current analysis step.

```
Repos on disk (inert, zero context cost):
  skillpacks/scientific-agent-skills/   # 135 skills, 0 tokens loaded
  skillpacks/bioSkills/                 # 439 skills, 0 tokens loaded
  skillpacks/Autonomous-Science/        # 50 personas, 0 tokens loaded

Convention reads one file at a time:
  "Read skillpacks/bioSkills/single-cell/quality-control/SKILL.md"
  → Agent reads ~150 lines for THIS step only
  → Executes with convention methodology wrapped around it
  → Moves to next step, reads next specific skill file
```

The convention pack includes a `skill-sources.yaml` manifest that records repo locations and verifies availability:

```yaml
# skill-sources.yaml
sources:
  scientific-agent-skills:
    path: skillpacks/scientific-agent-skills
    verify: scientific-skills/scanpy/SKILL.md
  bioSkills:
    path: skillpacks/bioSkills
    verify: single-cell/data-io/SKILL.md
  autonomous-science-personas:
    path: skillpacks/Autonomous-Science/personas/library
    verify: arjun_raj.json
```

This gives us the best of all worlds: skills stay up to date (git pull), no duplication, no trigger pollution, and context cost is exactly proportional to what the current step actually needs.

### 7.2 Context Budget

With mycelium's progressive disclosure, the context budget for a typical analysis step:

| Component | Lines | When Loaded |
|-----------|-------|-------------|
| Hub file (analysis-conventions.md) | ~150 | Always |
| Workflow detail file | ~400 | When workflow type is determined |
| Skill SKILL.md for current step | ~200 | Per step, unloaded after |
| Persona operational fields (2-3 personas) | ~100 | When persona panel is assigned |
| QC checklist (relevant section) | ~50 | Per QC checkpoint |
| robust-analysis rules (relevant section) | ~50 | Per validation point |
| **Total per step** | **~950** | |

Compare to naive loading: 574 skills x ~200 lines + 50 personas x ~100 lines = ~120,000 lines. Progressive disclosure achieves a **~125x reduction** in context usage.

### 7.3 Skill Version Management

Both repos evolve. The convention pack should:
- Pin to a commit hash or tag in `skill-sources.yaml`
- Include a `check-updates.sh` script that compares installed vs. latest
- Log version info in the analysis provenance

### 7.4 What the Full Stack Adds Over Raw Skills

| Raw Skill Usage | + Conventions | + Personas |
|-----------------|---------------|------------|
| Agent picks a skill and runs it | Convention routes to the right skill | Persona decision_rules guide parameter choices |
| Default parameters used | QC thresholds from checklist, documented | Thresholds informed by domain expert expectations |
| Output accepted at face value | Validation checks, sensitivity sweeps | Adversarial probing from disciplinary perspectives |
| No record of why | Full .living/ trail | Persona disagreements logged as distinct viewpoints |
| No biological interpretation | — | Roundtable generates mechanistic explanations |
| No follow-up ideas | — | Sub-hypotheses generated from persona panels |
| All skills loaded into context | Progressive disclosure (~750 lines/step) | +~100 lines for persona fragments |

---

## 8. Open Questions

1. **Should we create one convention pack or several?** A single `skill-bridge` pack vs. domain-specific packs (`skill-bridge-scrna`, `skill-bridge-variants`, etc.). The hub pattern supports either, but domain-specific packs enable selective installation.

2. **How do we handle skill conflicts?** When both packs cover the same tool (e.g., Scanpy), which takes precedence? Proposed: bioSkills for bioinformatics depth, scientific-agent-skills for breadth/infrastructure. Document in routing table.

3. **Should the convention pack auto-detect installed skills?** Rather than assuming both repos are installed, the convention could gracefully degrade — using whichever pack is available.

4. **How do we handle persona versioning?** The Autonomous-Science personas are synthesized from publication records at a point in time. As researchers publish more, personas go stale. Should the convention pack trigger re-synthesis periodically?

5. **Can we create new personas from conventions?** As mycelium accumulates learnings about what works in a domain, could those learnings be distilled into a "synthetic persona" that represents crystallized community knowledge rather than a single researcher? This would be the inverse flow: conventions → persona rather than persona → conventions.

6. **How do workflows handle R vs. Python?** bioSkills has deep R coverage (DESeq2, Seurat, edgeR). The convention pack needs to handle mixed-language workflows gracefully.

7. **What's the right number of personas per review?** More perspectives = more context cost and more potentially contradictory advice. The Autonomous-Science PI roundtable uses 3 standing + field-specific. For mycelium conventions, 2-3 per workflow may be the sweet spot.

---

## 9. Recommended Next Steps

1. **Create the skill-bridge convention pack** with the hub file, routing table, persona routing, and single-cell workflow detail file.
2. **Download the PBMC 3k dataset** and set up the test project.
3. **Run the three-condition test** as described in Section 6.
4. **Document results** as a formal report using the report-generator convention.
5. **Iterate** on the convention pack based on what the test reveals — especially which persona contributions were genuinely valuable vs. noise.
