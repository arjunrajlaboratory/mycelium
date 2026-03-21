<!-- Generated Convention Template -->
<!-- Output of the crystallize mode: .living/generated-conventions/[name]/convention.md -->
<!-- Fill in all sections. Delete this comment block before committing. -->

---
id: [convention-name]                      # Lowercase with hyphens, e.g., "validate-before-transform"
title: [One sentence: what this convention requires and why it exists.]
status: proposed                           # proposed | active | deprecated
created: [YYYY-MM-DD]
source_learnings:
  - YYYY-MM-DD
  - YYYY-MM-DD
description: [Optional longer description if the title alone is insufficient.]
---

## Statement

<!-- State clearly what to do. Use imperative voice. Be specific enough to be actionable. -->

[What must be done. Start with a verb: "Always validate...", "Never modify...", "When X occurs, do Y."]

## Rationale

<!-- Explain why — trace back to the source learnings. -->

[Why this convention exists. Reference the specific failures or insights that generated it. A reader
who hasn't seen the learnings should understand the real cost of ignoring this convention.]

**Source learnings:**
- [Link or reference to the learning entry that motivated this convention]
- [Additional learning entries if multiple contributed]

## Correct Application

```
<!-- Show what following the convention looks like. Use code, file paths, or prose as appropriate. -->
```

[Brief explanation of why this example is correct.]

## Incorrect Application

```
<!-- Show what violating the convention looks like. -->
```

[Brief explanation of what goes wrong when this is ignored.]

## Exceptions

<!-- Document when this convention does NOT apply. Every rule has limits. -->

- **[Edge case 1]**: [When the convention can be relaxed and what to do instead.]
- **[Edge case 2]**: [Another exception, if any.]

<!-- If there are no known exceptions, write: "No known exceptions." -->

## Review

<!-- Notes for the reviewer before promoting this convention to active status. -->
<!-- e.g., "Confirm preferred library before promoting", "Verify this applies to all environments." -->

[Any open questions or reviewer guidance for promoting this convention from proposed to active.]

---

## About ORIGIN.md

<!-- This section explains the companion file, not the convention itself. -->
<!-- An ORIGIN.md file lives alongside this convention.md in the same directory. -->
<!-- ORIGIN.md records provenance: which learnings and decisions spawned this convention. -->
<!-- Use the prose format below (matching the worked example in skill-generation-guide.md): -->

<!--
# ORIGIN.md format

# Origin

## Source Learnings

- **YYYY-MM-DD**: "[Learning title]" — brief note on what it contributed
- **YYYY-MM-DD**: "[Learning title]" — brief note on what it contributed

## Source Decisions

- **YYYY-MM-DD**: "[Decision title]" — (from decisions.md, if any)

## Pattern

[Describe the recurring pattern that justified crystallization: how many learnings,
which tags they shared, whether multiple projects were involved, and why this
pattern warrants a convention rather than an ad-hoc fix.]

## Contributing Projects

- [Project name or "Current project"] ([number of learnings])
-->
