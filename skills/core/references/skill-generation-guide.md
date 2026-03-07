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
2. The `prepare_contribution.py` script will:
   - Generalize repo-specific details into parameters
   - Create a properly formatted convention pack with `CONVENTION_PACK.yaml`
   - Generate a PR description with anonymized provenance
   - Include test cases derived from the learnings
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
