<!-- Template for a new .living/learnings/{domain}.md file. -->
<!-- Copy, set domain/description/matches, then start appending entries. -->
---
domain: <DOMAIN_NAME>
description: <one-line description — appears in MENU.md>
push_active: true
matches:
  - "<glob-pattern-1>"
  - "<glob-pattern-2>"
top_k_push: 3      # optional; default 3
token_cap: 2000    # optional; default 2000 (≈8000 bytes)
---

## [YYYY-MM-DD] Short learning title

**Domain**: <DOMAIN_NAME>
**Category**: gotcha | edge-case | insight | failure | tip
**Tags**: comma, separated, keywords
**Triggers**: ["optional", "keywords", "for", "per-entry", "relevance"]

**What happened**: one-sentence observation

**Resolution / Rule**: what to do instead

**Reference**: (optional commit hash, link, or paper citation)
