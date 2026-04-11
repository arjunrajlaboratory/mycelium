# Persona Routing

Which researcher personas to invoke for which analysis workflows. Personas live at `skillpacks/Autonomous-Science/personas/library/` as JSON files.

---

## How to Load a Persona

Read the JSON file for the assigned persona(s). Extract only the operational fields needed for the current mode:

**Collaborator mode** (during analysis — steer methodology):
- `decision_rules` — "When X, do Y" directives
- `anti_patterns` — "NEVER" constraints
- Inject these into the agent's working context alongside the skill

**Reviewer mode** (after analysis — adversarial review):
- `prompt_fragment` — Full thinking pattern (~150-200 words)
- `decision_rules` — Evaluation criteria
- `anti_patterns` — Red flags to check for
- `key_vocabulary` — Domain-specific terminology
- Present analysis outputs and ask the persona to evaluate

**Context cost**: ~300 words per persona in collaborator mode, ~400 in reviewer mode.

---

## Persona Assignments by Workflow

### Single-Cell RNA-seq

| Role | Persona | File | Why |
|------|---------|------|-----|
| Primary collaborator | Arjun Raj | `arjun_raj.json` | Single-cell heterogeneity, variability as signal, clone tracing |
| Methods reviewer | Cole Trapnell | `cole_trapnell.json` | Scalable single-cell methods, trajectory inference, platform engineering |
| Statistical reviewer | Andrew Gelman | `andrew_gelman.json` | Bayesian rigor, uncertainty quantification, multilevel modeling |

**Key persona contributions at each step**:
- **QC**: Raj — "NEVER average over cell-to-cell variability without first examining the full distribution." Forces distributional examination before applying cutoffs.
- **Clustering**: Trapnell — "Choose scale over depth; multiplex hundreds of conditions." Questions whether resolution is sufficient and whether more conditions would reveal more.
- **DE/markers**: Gelman — "NEVER accept p-values as sufficient evidence." Insists on effect sizes, uncertainty intervals, and consideration of multiple comparisons.
- **Interpretation**: All three review from their disciplinary lenses.

### Bulk RNA-seq & Differential Expression

| Role | Persona | File | Why |
|------|---------|------|-----|
| Statistical rigor | John Ioannidis | `john_ioannidis.json` | Meta-research skepticism, replication, effect size inflation |
| Methods reviewer | Lior Pachter | `lior_pachter.json` | Mathematical foundations of quantification, rigorous benchmarking |
| Biological interpretation | Arjun Raj | `arjun_raj.json` | Heterogeneity awareness, mechanism-seeking |

**Key persona contributions**:
- **DE results**: Ioannidis — "Estimate positive predictive value before accepting findings; prefer meta-analytic synthesis." Forces consideration of false discovery risk.
- **Quantification**: Pachter — Mathematical rigor on normalization and count model assumptions.
- **Pathway interpretation**: Raj — Questions whether bulk averages mask important cell-type-specific effects.

### Variant Analysis & Clinical Genomics

| Role | Persona | File | Why |
|------|---------|------|-----|
| Genetic epidemiology | Pardis Sabeti | `pardis_sabeti.json` | Population-scale genomic variation, evolutionary context |
| Human genetics | Mark Daly | `mark_daly.json` | Statistical genetics, GWAS methodology |
| Causal inference | Judea Pearl | `judea_pearl.json` | DAG-based causal reasoning, avoiding association-causation conflation |

### Drug Discovery & Cheminformatics

| Role | Persona | File | Why |
|------|---------|------|-----|
| Protein design | David Baker | `david_baker.json` | De novo design, structural validation requirements |
| Chemical biology | Carolyn Bertozzi | `carolyn_bertozzi.json` | Bioorthogonal chemistry, in vivo applicability |
| Translational | Eric Topol | `eric_topol.json` | Clinical translation, digital medicine integration |

### Systems Biology & Network Analysis

| Role | Persona | File | Why |
|------|---------|------|-----|
| Probabilistic models | Daphne Koller | `daphne_koller.json` | Graphical models, structure learning from data |
| Synthetic biology | Michael Elowitz | `michael_elowitz.json` | Stochastic gene expression, noise as information |
| Complex systems | Simon Levin | `simon_levin.json` | Multi-scale dynamics, emergent properties |

### Proteomics & Mass Spectrometry

| Role | Persona | File | Why |
|------|---------|------|-----|
| MS methodology | Matthias Mann | `matthias_mann.json` | Deep proteome coverage, quantitative MS |
| Statistical analysis | Daniela Witten | `daniela_witten.json` | Penalized methods, selective inference |

### Epigenomics

| Role | Persona | File | Why |
|------|---------|------|-----|
| Chromatin biology | Howard Chang | `howard_chang.json` | ATAC-seq pioneer, regulatory element mapping |
| Spatial methods | Xiaowei Zhuang | `xiaowei_zhuang.json` | Imaging-based spatial profiling |

---

## Standing Reviewers

These personas can be added to ANY workflow for cross-cutting review:

| Persona | File | When to Add |
|---------|------|-------------|
| John Ioannidis | `john_ioannidis.json` | Any time you want to stress-test statistical claims |
| Andrew Gelman | `andrew_gelman.json` | Any analysis with uncertainty quantification |
| Bin Yu | `bin_yu.json` | Any ML/prediction pipeline (stability, veridical data science) |
| Judea Pearl | `judea_pearl.json` | Any causal claim or association-to-mechanism leap |

---

## Custom Persona Panels

For analyses not covered above, compose a panel of 2-3 personas by:

1. Identifying the primary domain (match to closest workflow above)
2. Adding one methodological reviewer (Gelman, Ioannidis, or Bin Yu)
3. Adding one domain expert closest to the biological question

Document the chosen panel and rationale in `.living/decisions.md`.
