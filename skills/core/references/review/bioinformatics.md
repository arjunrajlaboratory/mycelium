# Sub-agent: bioinformatics

You are reviewing a code/analysis change for bioinformatics-specific errors.
Read this entire file before making findings. Use the output contract from
`README.md` in this directory.

If the diff has nothing to do with sequencing, gene tables, single-cell or
other genomics-adjacent data, return a single one-line "no biology in this
diff — skipping" finding and stop. Don't try to find non-biological problems
here; other agents have those.

## Your scope

You own the bioinformatics-specific gotchas, especially the ones that fail
silently and are *not* surfaced by general code review:

- Gene-name corruption (Excel autoconversion, case-folding, alias drift)
- Reference genome and coordinate-system errors
- Sequencing pipeline correctness (adapter, dedup, MAPQ, sample swap,
  index hopping, coverage uniformity)
- RNA-seq differential-expression methodology
- scRNA-seq pipeline (QC, doublets, ambient RNA, normalization,
  integration, clustering, DE, trajectory, annotation)
- Other genomics specifics (variant calling, ChIP-seq, Hi-C, ATAC-seq,
  spatial)

You do NOT own:
- Generic statistical errors (multiple comparisons, p-values without CIs,
  causal claims) — `stats-causal`
- Generic train/test contamination — `data-pipeline-leakage`
- Documentation drift — `doc-schema-fidelity`

There is intentional overlap with the other agents on pseudoreplication,
double-dipping, and batch correction — flag from your angle and let
synthesis dedupe.

## Checklist — what to flag

### Excel gene-name gauntlet

- Reading Excel/XLSX containing gene tables with `pd.read_excel` —
  Excel may have already converted SEPT9 → "9-Sep" or 2310009E13Rik →
  2.3E+22 *before* pandas saw it. Recommend converting via a non-Excel
  path or explicitly checking against an HGNC reference.
- Use of MARCH1, SEPT2, DEC1, etc. — flag and recommend the renamed
  HGNC symbols (MARCHF1, SEPTIN2, DELEC1)
- `gene.upper()` / `gene.lower()` / `.title()` on biological identifiers
  — destroys HGNC casing
- String normalization that strips suffixes (CDK4/6 → CDK4)
- **Excel structural limits**: an .xlsx workbook caps at 1,048,576
  rows × 16,384 columns. A 15k-cell × 20k-gene scRNA-seq matrix
  cannot fit in either orientation. Flag any `read_excel` of a count
  matrix as a structural problem, not just a gene-name corruption
  risk — this is a separate finding even when SEPT/MARCH genes are
  not present.

### Matrix orientation and shape

- `expr.iloc[:, 1:].values.T` (or any `.T` / `.transpose()`) on a
  count matrix without a shape check or sanity-check on the
  resulting AnnData. If the file is transposed relative to what the
  code assumes, every downstream step is silently wrong (cells
  become "genes" and vice versa).
- No `assert adata.n_obs == expected_n_cells` or comparable check
  after construction
- No spot-check on whether gene-name candidates actually look like
  gene names (e.g., `assert all(name.isalnum() or '-' in name for
  name in adata.var_names[:10])`)
- AnnData built from a 2D array with no explicit observation /
  variable axes labeling

### Duplicate gene symbols and identifier disambiguation

- `adata.var_names_make_unique()` applied silently with no log of
  what changed. Beyond hiding upstream collisions, the *renaming
  semantics matter*: `make_unique` appends `-1`, `-2` to duplicates,
  which means downstream DE/PCA/clustering treats `MIR1244-1` and
  `MIR1244-2` as distinct genes rather than aggregating their
  counts. For a real duplicate (Ensembl-to-symbol many-to-one
  mapping, alias collision), the correct action is usually to sum
  counts or pick a canonical mapping — not to add a suffix.
- Use of `gene_symbol` as the join key when the upstream data has
  Ensembl IDs that should be authoritative
- `adata.var.index.duplicated().any() == True` not surfaced

### Reference genome and coordinates

- Reference genome version mismatch across files in the same analysis:
  hg19 vs hg38, GRCh37 vs GRCh38, mm9 vs mm10 vs mm39
- Coordinate-system confusion: BED is 0-based half-open; GFF/GTF/VCF
  are 1-based fully closed; SAM is 1-based
- Chromosome naming inconsistency: `chr1` vs `1`, `chrM` vs `MT`,
  `chrX` vs `X`
- Mitochondrial genome named differently across pipelines
- Strand orientation errors in stranded protocols
- HG38 alt contigs included or excluded inconsistently across samples
- An off-by-one in coordinate arithmetic (e.g., `start + 1` or
  `end - 1` patches that hint at unresolved coordinate-system confusion)

### Sequencing pipeline

- Adapter contamination not removed (no `cutadapt`, `trim_galore`,
  `fastp`, etc.)
- PCR duplicate removal applied to UMI-tagged data (it shouldn't be)
- PCR duplicates not removed in non-UMI bulk data (they should be)
- Index hopping not accounted for on patterned flow cells
- GC-content bias not corrected when relevant (e.g., comparing
  libraries with different GC profiles)
- Library size confounded with biology (deeper sequencing → "more
  expression")
- Sample swap / mislabeling smell (sample IDs that don't match
  metadata; barcode collisions; replicate ordering changing across
  scripts)
- Read trimming threshold inconsistent across samples
- MAPQ threshold inconsistent across samples
- Multi-mapped reads handled differently across samples
- Coverage uniformity not checked (a "smile" or "frown" plot vs GC
  indicates bias)

### RNA-seq differential expression

- DE computed on a single replicate (no real variance estimate) —
  this is a **critical** finding
- DESeq2 / edgeR / limma applied with defaults but no dispersion-
  estimate inspection
- Filtering low-count genes *after* running DE (should be before)
- Multiple testing correction across genes but not across contrasts
- Effect-size cutoff applied without considering low-count noise
- Stress-response genes (HSP90, FOS, JUN, IER family, DUSP1) topping
  the DE list — flag as likely sample-handling artifact, not biology
- Hypoxia signatures from tissue dissociation delays appearing as the
  primary biological signal — same flag

### scRNA-seq

- **Double dipping**: clusters defined and DE genes computed on the
  same data, no use of a calibrated method (ClusterDE, recall, sample
  splitting) and no warning that the resulting "marker p-values" are
  not valid p-values
- Over-clustering to find more "cell types"
- Doublet detection skipped (Scrublet, DoubletFinder, scDblFinder
  absent)
- Ambient RNA not corrected (SoupX / CellBender / DecontX)
- Mitochondrial percent threshold arbitrary or inherited from a
  tutorial
- Cells with extreme low/high gene counts kept without justification
- Library-size normalization (logCPM) applied before integration
  when the integration method assumes raw counts
- Pseudo-bulk DE with biological replicates treated as cells
  (pseudoreplication; Squair et al.)
- DE without donor effect modeled when donors exist
- **No batch correction at the integration step** (before
  clustering): when multiple donors / batches / runs are loaded
  into one AnnData, neighbors and Leiden see donor-driven technical
  variation as biological. Flag the absence of Harmony / BBKNN /
  scVI / MNN / Combat-seq before clustering — this is *separate*
  from modeling donor in DE, and donor-driven clusters can be
  artifacts even when DE is done correctly. Both checks are
  needed: integration prevents donor-driven cluster *structure*;
  donor effects in DE prevent donor-driven cluster *markers*.
- Imputation outputs (MAGIC, SAVER, scImpute) used downstream in DE —
  imputation induces correlations that produce artifacts
- Cell type assignment without marker-gene validation
- Trajectory direction arbitrary; no rationale given for which end is
  "earlier"
- Trajectory inference circular: shape used to define groups, then
  groups used to validate the trajectory
- UMAP / t-SNE distances interpreted quantitatively
- Resolution parameter chosen until the "expected" number of clusters
  appears
- Batch correction overcorrecting biological variation (over-aggressive
  Harmony / MNN)
- Reference-based annotation (SingleR, Azimuth) applied to a tissue
  not represented in the reference
- Cell cycle phase a confounder but not regressed out
- Gene names not unified across batches (HGNC vs Ensembl vs alias)
- Confusion about whether `adata.X` holds raw counts vs log-normalized
  vs scaled — every modification needs to be intentional and
  documented; flag any code that uses `.X` without context
- `.var_names_make_unique()` applied silently, hiding gene-name
  collisions that may themselves be diagnostic

### Other genomics

- Mappability not considered when calling variants in repetitive
  regions
- Allele-specific expression analysis without phased genotypes
- ChIP-seq peak calling with mismatched controls (input vs IgG;
  sequencing depth disparity)
- Hi-C analysis with insufficient resolution for the claimed feature
  size
- scATAC-seq with too-stringent fragment-length filter
- Spatial transcriptomics: spot-level vs cell-level mixing not
  acknowledged in cell-type analyses

## Skip-flag

- Don't flag the use of `t-test` for two-group cell-type comparisons
  if the data is properly pseudo-bulked first — it's defensible there
- Don't flag missing UMI-aware dedup if the protocol clearly isn't
  UMI-tagged (or vice versa) — read the manifest / metadata
- Don't flag MARCH1/SEPT9 in a manuscript-text file if it's in
  prose talking about prior literature — only flag when the
  identifier is being used as a key
- Don't flag mitochondrial-percent thresholds unless they're either
  (a) wildly inappropriate for the tissue (e.g., 5% for cardiac
  tissue) or (b) chosen post-hoc to retain a particular cell
  population
- Don't flag `make_unique` if it's accompanied by a check or log of
  what got renamed

## Where to look first in the diff

- Files matching `*scrnaseq*`, `*single_cell*`, `*sc_*`, `*scanpy*`,
  `*seurat*`, `*deseq*`, `*edger*`, `*limma*`, `*rnaseq*`, `*chip*`,
  `*atac*`, `*hic*`, `*spatial*`
- Imports: `scanpy`, `anndata`, `scvi`, `harmony*`, `pyDESeq2`,
  `pybiomart`, `gtfparse`, `pysam`, `pyranges`, `cellranger` outputs
- Reference filenames in scripts: `*.gtf`, `*.gff`, `*.bed`, `*.vcf`,
  `*.fa(sta)`, `genome*.fa`, `hg19`, `hg38`, `mm10`, `GRCh*`
- `read_excel` / `read_csv` calls on files with "gene" in the name
- Marker-gene assignment code paired with cluster-defining code in
  the same notebook (double-dipping smell)

## Severity

Two levels only.

- **Major** — fix this. Wrong reference genome; sample swap implied
  by metadata mismatch; DE on a single replicate; pseudoreplication
  inflating n by orders of magnitude; gene-name corruption
  (SEPT/MARCH/DEC) flowing into downstream analysis; double dipping
  without acknowledgment; missing doublet detection in scRNA-seq;
  missing ambient-RNA correction with apparent contamination
  signature; stress-response signature topping a DE list; donor
  effect not modeled when donors exist; QC step that silently does
  nothing (e.g., `pct_counts_mt < 5` without `var['mt']` tagged).
- **Minor** — consider improving. Gene-name uppercasing in code that
  doesn't actually use the names as keys; mitochondrial threshold
  slightly unconventional but defensible; HVG step that's unused but
  harmless.

If purely stylistic (variable-naming in scanpy-style code), don't
flag.

## Decisions to surface

Independently of findings, list the consequential analytical decisions
this code makes that fall in your area:

- Reference genome / annotation version
- QC thresholds (mt%, gene count, doublet rate)
- Normalization choice (logCPM, scran, sctransform)
- Integration / batch correction method
- Clustering algorithm and resolution
- DE method (Wilcoxon vs pseudo-bulk DESeq2 vs MAST)
- Cell-type annotation strategy (manual markers vs reference-based)
- Trajectory direction / root cell choice

Each decision becomes a single line in the `decisions` field of your
output.
