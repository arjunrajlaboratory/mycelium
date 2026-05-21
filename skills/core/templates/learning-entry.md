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
                     validation was added (or could be added) to make this class
                     of error impossible or immediately detectable. Best outcome;
                     recurrence rate near-zero.
  convention       — The learning has been (or should be) promoted to a mandatory
                     checklist item in .living/conventions.md. Moderate effectiveness;
                     recurrence requires an active convention violation.
  ambient-awareness — General "watch out for X" with no enforcement mechanism.
                     Weakest; high recurrence risk. Use only when structural and
                     convention mitigations are genuinely impractical.
-->

**structural_mitigation_candidate**: [What test or invariant would have caught this? Be specific — name the function, file, or assertion. If you can describe it concretely, the learning should ship as `structural` instead of `ambient-awareness`.]

source: [optional — source project name, for cross-project learnings only]
