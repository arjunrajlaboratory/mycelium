<!-- Generated Convention Template -->
<!-- Output of the crystallize mode: .living/generated-conventions/[name]/convention.md -->
<!-- Fill in all sections. Delete this comment block before committing. -->

---
name: [convention-name]                    # Lowercase with hyphens, e.g., "validate-before-transform"
version: 0.1.0
description: [One sentence: what this convention requires and why it exists.]
source_learnings:
  - "[YYYY-MM-DD] [Learning title from .living/learnings.md]"
  - "[YYYY-MM-DD] [Learning title from .living/learnings.md]"
created: [YYYY-MM-DD]
---

## Convention

<!-- State clearly what to do. Use imperative voice. Be specific enough to be actionable. -->

[What must be done. Start with a verb: "Always validate...", "Never modify...", "When X occurs, do Y."]

## Rationale

<!-- Explain why — trace back to the source learnings. -->

[Why this convention exists. Reference the specific failures or insights that generated it. A reader
who hasn't seen the learnings should understand the real cost of ignoring this convention.]

**Source learnings:**
- [Link or reference to the learning entry that motivated this convention]
- [Additional learning entries if multiple contributed]

## Examples

### Correct application

```
<!-- Show what following the convention looks like. Use code, file paths, or prose as appropriate. -->
```

[Brief explanation of why this example is correct.]

### Incorrect application

```
<!-- Show what violating the convention looks like. -->
```

[Brief explanation of what goes wrong when this is ignored.]

## Exceptions and edge cases

<!-- Document when this convention does NOT apply. Every rule has limits. -->

- **[Edge case 1]**: [When the convention can be relaxed and what to do instead.]
- **[Edge case 2]**: [Another exception, if any.]

<!-- If there are no known exceptions, write: "No known exceptions." -->

---

## About ORIGIN.md

<!-- This section explains the companion file, not the convention itself. -->
<!-- An ORIGIN.md file lives alongside this convention.md in the same directory. -->
<!-- ORIGIN.md records provenance: which project generated this convention, -->
<!-- when, and what the crystallize run looked like. Format: -->

<!--
# ORIGIN.md format

source_project: [project name or "anonymous"]
crystallized_on: [YYYY-MM-DD]
learnings_count: [number of .living/learnings.md entries reviewed]
pattern_strength: [how many learnings exhibited the same pattern]
contributed_to_network: [true|false]
network_pack: [pack name if contributed, else omit]
-->
