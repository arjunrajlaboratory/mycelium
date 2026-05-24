# Convention Generation Guide

How the crystallize mode detects patterns in accumulated learnings and generates new convention packs.

## The Lifecycle

```
learning → pattern → convention pack → community contribution
```

1. **Learnings accumulate**: As you work in a mycelium-enabled repo, gotchas, insights, and edge cases are logged in `.living/learnings.md`.
2. **Patterns emerge**: Over time, similar learnings cluster around recurring themes.
3. **Conventions crystallize**: The `crystallize` mode identifies these patterns and proposes formal conventions.
4. **Community benefits**: Via `contribute` mode, repo-local conventions can be generalized and shared back to the network as convention packs.

## How Crystallize Works

When invoked, the `crystallize` mode:

### 1. Reads accumulated intelligence

Scans `.living/learnings.md` and `.living/decisions.md` for entries.

### 2. Identifies patterns

Looks for:
- **Similar tags**: Multiple learnings tagged with the same topic
- **Repeated problems**: The same gotcha encountered more than once
- **Evolving conventions**: Decisions that progressively refine an approach
- **Gap signals**: Learnings that note "there's no convention for this"

### 3. Proposes new conventions

For each identified pattern, drafts a convention document:
- A clear statement of the convention
- The rationale (linked to the learnings that motivated it)
- Examples of correct and incorrect application
- Any exceptions or edge cases

### 4. Stages for review

Generated conventions are placed in `.living/generated-conventions/[name]/`:

```
.living/generated-conventions/
└── csv-encoding-conventions/
    ├── convention.md           # The proposed convention
    ├── ORIGIN.md               # Provenance: which learnings spawned this
    └── examples/               # Optional: examples demonstrating the convention
```

## The ORIGIN.md Provenance Document

Every generated convention must include an `ORIGIN.md` that links back to its source:

```markdown
# Origin

## Source Learnings

- **2024-03-15**: "CSV files from Hospital X use Windows-1252 encoding despite claiming UTF-8"
- **2024-03-22**: "Another batch from Hospital X had mixed encodings within the same file"
- **2024-04-01**: "Discovered that the export tool has a known bug with special characters"

## Source Decisions

- **2024-03-16**: "Decided to always run chardet on incoming CSVs before processing"

## Pattern

Encoding issues with external CSV files are a recurring problem. A convention
for encoding validation during data ingestion would prevent repeated debugging.
```

## Promoting to the Network

If a generated convention seems generally useful (not specific to your repo or data):

1. Use the `contribute` mode
2. The `contribute` mode (agent-driven) will:
   - Generalize repo-specific details into parameters
   - Create a properly formatted convention pack with `CONVENTION_PACK.yaml`
   - Generate a PR description with anonymized provenance
   - Include QC checklist derived from the learnings
3. Review the generated PR and submit to the mycelium network repository

## When to Crystallize

Good times to run crystallize:
- After completing a major analysis phase
- When `.living/learnings.md` has more than 10-15 entries
- When you notice yourself looking up the same thing repeatedly
- Before contributing back to the network

Don't force it — if there aren't enough learnings yet, crystallize will report that no clear patterns have emerged. That's fine. Keep working and logging.

## Quality Signals

A good generated convention:
- Addresses a real, recurring problem
- Is specific enough to be actionable
- Is general enough to apply beyond one dataset or analysis
- Includes examples
- Traces cleanly back to its source learnings

A poor generated convention:
- Is too vague ("be careful with data")
- Is too specific ("always use encoding X for files from source Y")
- Has no clear source learnings
- Duplicates an existing convention

## Pattern Detection Thresholds

Explicit rules for deciding when a cluster of learnings warrants crystallization:

- **Minimum pattern strength**: 3+ learnings sharing a tag or theme before crystallization is warranted. Fewer than 3 is anecdote, not pattern.
- **Cross-project boost**: If 2+ projects have learnings on the same topic, the threshold drops to 2 per project. The cross-project signal itself is evidence of a pattern — the problem is not local.
- **Gap signals**: A learning that explicitly identifies a missing convention — phrases like "there's no convention for", "we lack guidance on", "no standard approach for", or "had to figure this out from scratch" — counts as 2 toward the threshold. The author already recognized the pattern; one more related learning triggers crystallization.
- **Recency weighting**: Learnings from the last 30 days count at full weight toward the threshold. Learnings older than 30 days count at half weight (e.g., 2 old learnings = 1 toward the threshold). This means 6 old learnings on the same topic still trigger crystallization, but 3 recent ones do too.
- **Cross-project learnings**: Entries with `source:` fields from other projects count toward patterns in the current project. If a learning was important enough to propagate cross-project, it is a strong signal that a convention would generalize.

When in doubt, err toward proposing a convention and letting the reviewer reject it. False positives (unnecessary conventions) are cheaper than false negatives (re-encountering the same problem without guidance).

## Using `mitigation_type` to Guide Promotion Decisions

Every learning entry carries a `mitigation_type` field that directly informs how — and how urgently — it should be promoted at crystallize time.

### The three types and their recurrence risk

| mitigation_type | Description | Recurrence risk | Crystallize action |
|---|---|---|---|
| `structural` | A test, assertion, type constraint, frozenset, or schema validation was added to make this class of error impossible or immediately detectable. | Near-zero — the code enforces it. | No promotion needed; record for provenance. |
| `convention` | The learning has been or should be promoted to a mandatory checklist item in `.living/conventions.md`. | Moderate — violation requires bypassing the convention. | Add or strengthen the conventions.md entry; cite the learning. |
| `ambient-awareness` | General "watch for X" with no enforcement mechanism. | High — depends entirely on memory. | **Highest urgency**: either convert to `structural` (add a test) or promote to `convention`. |

### Decision procedure at crystallize time

1. **Audit ambient-awareness entries first.** These carry the highest recurrence risk. For each one, ask: *"What test or invariant would have caught this?"* (the `structural_mitigation_candidate` field should already answer this). If the answer is concrete, open a task to add the test and reclassify to `structural`. If structural mitigation is genuinely impractical, promote to `convention`.

2. **Cluster ambient-awareness entries before promoting.** Two ambient-awareness learnings on the same topic should produce *one* convention, not two. Use `detect_recurrence.py` to identify clusters: `python3 skills/core/scripts/detect_recurrence.py --living-dir <path>`. Merge related entries into a single, generalized convention.

3. **Convention entries verify the checklist.** If a learning is `convention`, confirm the corresponding entry exists in `.living/conventions.md`. If missing, add it now. If present, check whether the convention text needs strengthening based on the new learning.

4. **Structural entries close the loop.** If a learning is `structural`, confirm the test or invariant actually exists in the codebase. If not (i.e., the mitigation_type was aspirational), demote to `convention` until the test ships.

### Audit finding that motivated this field

An observational audit of 12 sampled learnings found a 33% recurrence rate overall (67% when the topic recurred at all). The pattern was consistent: `ambient-awareness` learnings recur; `structural` mitigations do not. The `mitigation_type` field forces this distinction at write time, when the author has the most context.

## Worked Example

### Input: 4 learnings in `.living/learnings.md`

```markdown
## [2024-03-15] CSV from Hospital X uses Windows-1252 despite claiming UTF-8
**Category**: gotcha
**What happened**: Ingested a CSV delivered by Hospital X. The file header declared UTF-8 but chardet reported Windows-1252 with 99% confidence. Pandas silently mangled all accented characters.
**Why it matters**: Silent data corruption — no error raised, wrong values written downstream.
**Resolution**: Added `chardet` detection step before passing to `pd.read_csv`. Force `encoding=detected_encoding`.
**Tags**: data-ingestion, encoding

## [2024-03-22] Mixed encodings in same file from Hospital X
**Category**: edge-case
**What happened**: A second batch from Hospital X had rows in two encodings within the same file. chardet reported the majority encoding; the minority rows still failed.
**Why it matters**: Single-pass encoding detection is not sufficient for files from this source. Need row-level validation or a more robust detection strategy.
**Resolution**: Switched to `ftfy.fix_text()` on each row after initial read with `errors='replace'`.
**Tags**: data-ingestion, encoding

## [2024-04-01] Export tool has known bug with special characters
**Category**: failure
**What happened**: Traced encoding issues back to the vendor's export tool (version < 3.2.1). It declares UTF-8 in the header but writes Windows-1252 for any character outside ASCII.
**Why it matters**: This is a vendor bug, not a one-off. Every file from this tool before v3.2.1 is suspect.
**Resolution**: Filed bug with vendor. Added version check to ingestion script; if version unknown, treat as potentially mis-labeled.
**Tags**: data-ingestion, encoding, vendor-bug

## [2024-04-10] Lab Y data also had encoding issues — ISO-8859-1 mislabeled as UTF-8
**Category**: gotcha
**What happened**: Lab Y delivered tabular data with the same symptom: declared UTF-8, actual ISO-8859-1. Caught it because the Hospital X incident was still fresh.
**Why it matters**: This is now a second independent source with the same failure mode. Encoding mislabeling is a systematic problem with external data, not a Hospital X quirk.
**Resolution**: Extended ingestion validation to all external tabular sources, not just Hospital X.
**Tags**: data-ingestion, encoding
```

### Pattern detected

4 learnings tagged `encoding` + `data-ingestion` (exceeds threshold of 3). Two independent sources (Hospital X, Lab Y) exhibit the same failure mode. All describe external data with incorrect encoding declarations. The 2024-04-10 entry explicitly notes this is now a cross-source pattern — strong signal.

### Output: Generated convention

**`.living/generated-conventions/encoding-validation/convention.md`**

```markdown
---
id: encoding-validation
title: Always detect encoding on incoming tabular data before processing
status: proposed
created: 2024-04-11
source_learnings:
  - 2024-03-15
  - 2024-03-22
  - 2024-04-01
  - 2024-04-10
---

# Convention: Encoding Validation for External Tabular Data

## Statement

Always run encoding detection on all incoming tabular data from external sources before passing to any parser. Never trust the declared encoding in the file header or delivery metadata.

## Rationale

At least two independent data sources (Hospital X and Lab Y) have delivered files with incorrect encoding declarations. In both cases, pandas silently accepted the incorrect declaration and produced corrupted data without raising an error. The Hospital X case also revealed that vendor tooling may systematically mislabel encodings — meaning this is a structural risk for any externally-sourced data, not an isolated incident.

Source learnings: 2024-03-15, 2024-03-22, 2024-04-01, 2024-04-10.

## Correct Application

```python
import chardet

def read_external_csv(path: str) -> pd.DataFrame:
    with open(path, "rb") as f:
        detected = chardet.detect(f.read())
    encoding = detected["encoding"] or "utf-8"
    df = pd.read_csv(path, encoding=encoding)
    # Optionally apply ftfy for row-level normalization
    return df
```

## Incorrect Application

```python
# Do not do this — trusts declared encoding
df = pd.read_csv(path, encoding="utf-8")
```

## Exceptions

- Data produced internally by this pipeline (we control the encoding)
- Binary formats (Parquet, HDF5) — encoding is not relevant
- Files accompanied by a verified checksum and encoding certificate from a trusted source

## Review

Proposed from crystallization. Reviewer should confirm whether `chardet` or `ftfy` is the preferred detection library before promoting to active.
```

**`.living/generated-conventions/encoding-validation/ORIGIN.md`**

```markdown
# Origin

## Source Learnings

- **2024-03-15**: "CSV from Hospital X uses Windows-1252 despite claiming UTF-8" — initial discovery, silent corruption
- **2024-03-22**: "Mixed encodings in same file from Hospital X" — revealed single-pass detection is insufficient
- **2024-04-01**: "Export tool has known bug with special characters" — identified vendor as systemic source
- **2024-04-10**: "Lab Y data also had encoding issues — ISO-8859-1 mislabeled as UTF-8" — confirmed cross-source pattern

## Source Decisions

- **2024-03-16**: "Decided to always run chardet on incoming CSVs before processing" (from decisions.md, if present)

## Pattern

4 learnings tagged `encoding` + `data-ingestion` across 2 independent data sources. All describe the same failure class: external tabular data with incorrect encoding declarations causing silent data corruption. Threshold of 3 met; cross-source signal strengthens confidence.

## Contributing Projects

- Current project (all 4 learnings)
```

## Cross-Project Pattern Detection

Learnings that arrive from other projects via the cross-project propagation flow carry a `source:` field in `.living/learnings.md`:

```markdown
## [2024-04-15] Encoding issues with partner lab data
**Category**: gotcha
**What happened**: ...
**Tags**: data-ingestion, encoding
source: AutoReview
```

Rules for handling these entries:

- **They count toward local thresholds**: A cross-project learning tagged `encoding` counts toward the local `encoding` cluster. If it pushes the cluster to 3, crystallization is warranted.
- **Same tag cluster across 2+ projects = strong signal**: If `encoding` + `data-ingestion` appears in learnings from both the current project and a `source:` entry from another project, that alone may justify crystallization even with fewer than 3 local entries (cross-project boost applies).
- **ORIGIN.md must list contributing projects**: When a convention is generated from a mix of local and cross-project learnings, the `ORIGIN.md` "Contributing Projects" section must list all source projects by name. This preserves the provenance chain and helps reviewers assess generalizability.
- **Prefer general conventions**: Conventions crystallized from cross-project learnings are stronger candidates for network contribution via `contribute` mode, since they already demonstrate multi-project applicability.
