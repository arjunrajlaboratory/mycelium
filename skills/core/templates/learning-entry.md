<!-- Learning Entry Template -->
<!-- Copy this block and append to .living/learnings.md -->

### [YYYY-MM-DD] [Short Learning Title]

**Category**: [gotcha|edge-case|insight|failure|tip]

**What happened**: [Describe what was observed or encountered.]

**Why it matters**: [Why this is worth recording — what could go wrong if forgotten?]

**Resolution**: [How it was handled, if applicable.]

**Tags**: [relevant, tags, for, searchability]

**mitigation_type**: [structural|convention|ambient-awareness]

<!-- mitigation_type guidance:
  structural       — A test, assertion, type constraint, frozenset, or schema
                     validation has SHIPPED in the codebase and enforces this
                     class of error. The mitigation is currently active.
                     Recurrence rate near-zero.
                     If the mitigation is only a candidate (not yet shipped),
                     describe it in `structural_mitigation_candidate` and set
                     `mitigation_type: ambient-awareness` until the test lands.
  convention       — The learning has been promoted to a mandatory checklist
                     item in `.living/conventions.md`. Moderate effectiveness;
                     recurrence requires an active convention violation. If
                     the convention is only proposed, keep type as
                     `ambient-awareness` until the conventions.md entry is
                     actually added.
  ambient-awareness — General "watch out for X" with no enforcement mechanism.
                     Weakest; high recurrence risk. Use this for any learning
                     whose mitigation has not yet been implemented, even if a
                     candidate is described below.
-->

**structural_mitigation_candidate**: [What test or invariant would have caught this? Be specific — name the function, file, or assertion. If the mitigation has actually shipped, set `mitigation_type: structural`. If it is only a candidate, keep type as `ambient-awareness` until the test lands.]

source: [optional — source project name, for cross-project learnings only]
