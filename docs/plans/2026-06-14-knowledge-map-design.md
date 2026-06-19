# Knowledge Map — Design Spec

**Date:** 2026-06-14
**Status:** Design approved; pre-implementation — **rev 2** (two Codex review rounds folded in)
**Feature:** A concept-centric knowledge map over a multi-project research portfolio, added as a new mycelium capability.
**Review:** Two pre-implementation adversarial reviews by Codex (gpt-5.5), 2026-06-14; P0/P1 findings from both folded in (see §18 changelog).

---

## 1. Problem

A mature `.living/` knowledge base already exists across the Science portfolio, but it is **siloed by folder and connected only by an implicit `source:` string**:

- 5 projects run full `.living/` (some huge: SCKG ≈ 804 KB learnings + ~100 finding files); 5+ have none.
- The `[[wikilinks]]` already in the learnings are **cosmetic prose** — they link to nothing.
- Entries have **no stable IDs** in source; the `L-N`/`D-N` ids exist only in a regenerated `INDEX.md` and are re-derived by order (fragile).
- The controlled tag vocabulary is aspirational; organic tags dominate.

The filesystem forces **one** hierarchy. The thing it cannot express — and the thing knowledge is actually retrieved by — is *"everywhere I have touched concept X, across every project."* That cross-cutting connectivity is the gap.

## 2. Goals / Non-goals

**Goals**
- Make recurring **concepts** first-class, addressable nodes that aggregate evidence across projects.
- Serve **both** a human Obsidian graph view and Claude's retrieval/routing/transfer; optimize **Claude-first** when the two conflict.
- Make cross-project transfer and crystallization/elevation **structural and queryable**, not periodic manual scans.
- Be **deterministic and idempotent**: a re-run on unchanged inputs produces a byte-identical graph.

**Non-goals (this phase)**
- No migration of existing `.living/` source files; they are read in place and stay pristine.
- No automatic concept creation without a human gate.
- No coverage of projects without `.living/` beyond making their absence visible.
- No full automation of elevation-to-global; phase 2.

## 3. Locked decisions

1. **Concept-centric backbone.** Concepts are the primary nodes/edges; project/stage/time/tags are *attributes*, used to generate projections.
2. **Lifecycle stage is a facet, not the tree.** The project→stage "lifecycle" view is a *generated projection*, not the storage structure.
3. **Obsidian-compatible markdown + JSON sidecar.** Vault for humans; `knowledge-graph.json` for Claude. Both from one generation pass.
4. **Two artifact tiers.** **Persistent state (source-of-truth, version-controlled, NOT disposable):** per-project `.living/` entries (read in place), `concepts.yaml`, `overrides.yaml`, `projects.yaml`, the **`entry-ids.json` identity ledger**, and `entry-facets.yaml` (curated stage overrides). **Disposable (regenerable any time):** `knowledge-graph.json`, `vault/`, `views/`, `proposals/`. Identity has history, so the id ledger persists; a *cold* rebuild (empty ledger) legitimately re-mints ids and is an explicit, rare operation — not the steady state.

## 4. Data model

### 4.1 Node types

| Node | Identity | Source of truth | Generated? |
|---|---|---|---|
| **Entry** | stable id (§4.4) | per-project `.living/*.md` (read in place) | extracted |
| **Concept** | `slug` | `concepts.yaml` | curated |
| **Convention** | `conv:<slug>` | `.living/generated-conventions/` | adapted/extracted |
| **GlobalKnowledge** | `global:<domain>/<slug>` | `~/.claude/knowledge/<domain>.md` | adapted/extracted |
| **ProjectHub** | `project_id` | `projects.yaml` | navigational (vault) |

An **Entry** is one of three kinds (`learning` / `decision` / `finding`) plus a **`source_shape`** (§4.3). Convention + GlobalKnowledge are collectively the **elevation nodes** (§7). A **ProjectHub** is a per-project landing node (one per `projects.yaml` entry) that exists for *navigation*: in the analytical `knowledge-graph.json`, project and lifecycle-stage are entry *facets* (§4.6/§9), not nodes; in the Obsidian vault they are materialized as ProjectHub notes so the **project → stage → entry** hierarchy is clickable (§8). Both axes — **project** (vertical "where/whose") and **concept** (horizontal "what about") — thus get equal first-class navigation.

### 4.2 Edge types

```
entry      --about-->        concept            core connectivity edge; generated from match rules
concept    --crystallizes--> convention         when distinct-family evidence threshold met (§7)
convention --elevates-->     global-knowledge    when convention proves cross-family
entry      --supersedes-->   entry              parsed from decision prose / explicit field
concept    --relates-->      concept            curated only, minimal in MVP
```

Every edge record:
```json
{ "from": "<node_id>", "to": "<node_id>", "type": "about|crystallizes|elevates|supersedes|relates",
  "provenance": "auto|manual", "trigger": "<alias/keyword that fired, or null>", "confidence": 0.0 }
```
`about` edges are **always recomputed** from the current `concepts.yaml` + current entries on every build. `entry-ids.json` preserves **identity only**, never edge state. Manual edges come solely from the override file (§5.3).

### 4.3 `source_shape` (parser contract)

Real `.living/` files are not uniform. Each entry is parsed under exactly one declared shape; each shape has its own anchor + body-slicing + title/date rules:

| `source_shape` | Example | Anchor rule |
|---|---|---|
| `aggregate-section` | `learnings.md` / `decisions.md` with many `##`/`###` dated sections | heading line + heading text |
| `per-entry-file` | `learnings/<slug>/…`, one entry per file | file path |
| `finding-topic-ledger` | a `findings/<topic>.md` ledger with `## F-NNN` rows | file path + row id |
| `standalone-finding-file` | SCKG `findings/<date>-<slug>.md` titled `# Finding: …` (with or without frontmatter) | file path |

The parser MUST tolerate header variants observed in the corpus: `### [YYYY-MM-DD] Title`, `## [date]`, `## date — title`, `### date: title`, `### D1: …`, `# Finding: …`, and YAML-frontmatter docs. Unparseable entries are reported, never silently dropped.

**Inclusion / exclusion (explicit — a permissive "any dated heading" rule over-extracts):**
- **Include:** `learnings.md`, `decisions.md`, `findings.md`, per-entry files under `learnings/`, topic ledgers under `findings/`, and standalone `findings/<…>.md` files.
- **Exclude:** `.living/log/` (session logs), `INDEX.md`, `MENU.md`, `LOG_REGISTRY.md`, `FINDINGS_REGISTRY.md`, `last-session.md`, `conventions.md` (conventions are elevation nodes, §7), `*_MANIFEST.md`, and any `generated-conventions/` tree.
- **Entry detection (aggregate-section) — by SIGNATURE, not by depth.** Real files mix `##` and `###` entries (SCKG `learnings.md` has dated headings at both depths; AutoReview `decisions.md` ≈73 at `##` + 81 at `###`; Autonomous Science `learnings.md` has `### L-177 …` as real entries). An **entry boundary is any heading whose text matches the entry-signature** — a date (`[YYYY-MM-DD]` or bare `YYYY-MM-DD`), an explicit id (`D\d+`, `L-?\d+`, `F-?\d+`), or a `Finding:` marker — **at any heading depth**. A heading that does NOT match the signature (`### Context`, `### Insight`, `### Alternatives`) is a **body sub-section of the enclosing entry**. A signature heading nested directly under another signature heading is treated as its own entry and reported for review (rare).
- **Template/sample guard:** a section whose title/body matches a template marker (`<placeholder>`, literal "Title", "example", "TEMPLATE", a literal `YYYY-MM-DD`) is excluded and reported.
- **Finding row ids** in a `finding-topic-ledger` are namespaced `project_id + source_path + row_id` (Autonomous Science reuses `F-001` across topic files — project namespacing alone collides).

### 4.4 Stable identity (immutable id + evolving fingerprint)

The **id is an opaque token minted once** and never changed (e.g. `e-00017`). Source content — including the heading/title — may change without changing the id. Identity across builds is resolved by matching an entry to an already-minted id; only if no match is found is a new id minted.

**Per-build resolution (deterministic, entries processed in sorted order):**
1. **Explicit-id match.** If the source carries an explicit id (`## F-012` row, `### D1:` decision), its *namespaced* form `project_id + source_path + row_id` is the lookup key → reuse the bound id.
2. **Fingerprint match.** Else compute `current_fingerprint = project_id + source_path + heading_anchor + type + date`. If it equals any id's `current_fingerprint` **or** any entry in its `previous_fingerprints[]` → reuse that id.
3. **Rename/title-edit match (fuzzy, one-to-one, bounded).** Else attempt a rebind. Candidate set = ids that were `active` in the prior `entry-ids.json` (NOT tombstoned), still unmatched this build, with identical `project_id + source_path + type + date`. Similarity = **token-set Jaccard over the normalized body** (NFC + casefold + whitespace-collapsed); rebind only if Jaccard ≥ `RENAME_TAU` (fixed, default 0.80). When several entries/candidates compete, assignment is **greedy one-to-one by descending (Jaccard, then lexicographic entry id)** — so no two entries can claim the same prior id (kills the "B steals A's id" hazard). A rebind within 0.05 of the threshold is flagged in the report. On rebind: push the old fingerprint into `previous_fingerprints[]`, set the new `current_fingerprint`. This makes a **title edit id-stable** (the §16 test). (`content_hash` stays an exact-match change-detector; the Jaccard metric is separate.)
4. **Mint.** Else mint a new opaque id. A genuine duplicate (same heading/date/path as another *new* entry this build) gets a deterministic ordinal suffix.

`content_hash` is a **change detector only** (drives re-link + "updated" flags + the rename matcher), never identity.

**Tombstones.** `entry-ids.json` keeps every id ever minted. An id whose entry is absent for a build is marked `status: tombstone` (kept, not deleted); a later reappearance reactivates it. Tombstoned ids are never reused for a different entry.

**Tombstone exclusion.** Fuzzy rename (step 3) never matches a tombstoned id — only steps 1–2 (explicit id / exact `current`/`previous` fingerprint) can reactivate a tombstone. So a deleted entry's id cannot be stolen by a coincidentally-similar new entry; a tombstone returns only on an exact-key match.

```json
// entry-ids.json  (keyed by the IMMUTABLE id, not by fingerprint)
{ "schema_version": 1,
  "ids": {
    "e-00017": {
      "current_fingerprint": "sckg|Scientific Claims Knowledge Graph/.living/learnings.md|geo-gsm-matrices|L|2026-04-23",
      "previous_fingerprints": [],
      "content_hash": "sha256:…",
      "status": "active"
    } } }
```
(`last_seen_build` is intentionally **not** stored here — a monotonic counter would leak nondeterminism into a tested artifact, §12. Build bookkeeping lives in the snapshot-excluded `build-meta.json`.)

**Cold rebuild.** Opaque ids are minted against the persistent `entry-ids.json` ledger; a cold rebuild (empty ledger) re-mints fresh ids and does **not** reproduce historical ids once deletions/renames have occurred. Expected: `entry-ids.json` is persistent state (§3.4); the §12 byte-identical guarantee is scoped to builds sharing the same ledger, not cold-vs-incremental.

### 4.5 Facets (overlay, not in source)

Facets do **not** get written back into `.living/` source (that would be a migration and would fork the data). They live in a generated/curated overlay keyed by stable id:

```yaml
# Science/.living/graph/entry-facets.yaml
schema_version: 1
facets:
  "e-00017":          # keyed by the immutable entry id (§4.4), never a content-derived string
    stage: data-registry        # controlled vocab (§4.6); auto-assigned, human-overridable
    stage_source: auto|curated
```
`project`, `time`, and `tags` are read directly from the entry; **`stage`** is the one facet the generator assigns and a human can override.

**Stale keys.** When an entry id tombstones (§4.4), its facet entry moves to a `tombstoned:` block in `entry-facets.yaml` (retained for reactivation, not silently dropped) and a warning is emitted to the validation report. A facet key matching no known id is reported and ignored.

### 4.6 Stage vocabulary (controlled) + assignment rules

Vocabulary: `data-registry · lit-review · planning · analysis · figure-generation · writing · evaluation · infrastructure · unassigned`

Controlled because the **generator** validates it (an unknown stage fails validation), unlike the voluntary tag vocab that rotted.

**Assignment is a deterministic ordered ruleset** (first match wins) — not an open-ended heuristic:
1. **Override** — a `stage_source: curated` value in `entry-facets.yaml` always wins.
2. **Source-path map** — a fixed `path-substring → stage` table (`figures/`→figure-generation, `data/`→data-registry, `analysis/`→analysis, `docs/plans/`→planning, `eval`→evaluation, `tests/`→infrastructure). Part of the spec config, not invented per-run.
3. **Keyword table** — a fixed `keyword → stage` table over title+tags ("figure"/"panel"/"dpi"→figure-generation, "prereg"/"protocol"→planning, "calibration"/"benchmark"→evaluation).
4. **Default** — `unassigned`.

Every assignment records `stage_source: curated|path|keyword|default`. In the lifecycle projection (§8), `unassigned` entries are grouped in their own bucket and the view is labeled *heuristic*. This keeps the lifecycle facet without pretending auto-assignment is authoritative.

### 4.7 Field-level schemas (graph_model.py — single source of shape)

**Entry**
| field | type | notes |
|---|---|---|
| `id` | str | immutable opaque token (§4.4) |
| `kind` | enum `learning\|decision\|finding` | |
| `source_shape` | enum (§4.3) | |
| `project_id` | str | FK → projects.yaml |
| `family` | str | denormalized from projects.yaml |
| `source_path` | str | portfolio-relative, normalized |
| `anchor` | str | heading text or file marker |
| `line_start` / `line_end` | int\|null | aggregate-section slicing |
| `title` | str | |
| `date` | str(ISO)\|null | |
| `tags` | list[str] | sorted |
| `body_excerpt` | str | trimmed, fixed length |
| `content_hash` | str | `sha256:` change-detector |
| `status` | enum `active\|tombstone` | |
| `schema_version` | int | |

**Concept** — as §5.1 (`slug` matches `^[a-z0-9][a-z0-9-]*$`; `status ∈ {candidate,confirmed,curated_singleton}`; `effective_status` is generated, not stored in source).

**Edge** — `{from:str, to:str, type:enum, provenance:enum auto|manual, trigger:str|null, confidence:str(2dp)|null}`.

**Facet** — `{stage:enum(§4.6), stage_source:enum curated|path|keyword|default}` keyed by entry id.

All node ids share a namespace; endpoints in `knowledge-graph.json` are validated to resolve (§12).

## 5. Concepts & linking hygiene

### 5.1 `concepts.yaml` (source of truth)

```yaml
schema_version: 1
concepts:
  - slug: geo-data-access
    label: "GEO data access"
    definition: "Retrieving expression matrices from GEO; GSM-level vs series-level access."
    status: confirmed | candidate | curated_singleton
    match:
      aliases: ["GEO", "GSM", "GEO accession"]
      positive_keywords: ["CellRanger", "series matrix", "GSE", "supplementary file"]
      negative_keywords: ["geometry", "geographic"]
      required_any: ["GEO", "GSM", "GSE"]      # at least one must appear → precision guard
      project_scope: null                       # or [project_ids] to restrict
      match_mode: hybrid                         # alias | keyword | hybrid
    relates: []
    parent: null
```

Plain aliases are insufficient (`GEO`, `claim`, `edge`, `cache`, `review`, `graph` are context-dependent and would mass-mislink). The `required_any` / `negative_keywords` fields are the precision guard.

### 5.2 Linking & relinking

- The build **always recomputes** `about` edges from current `concepts.yaml` + current entries. **No edge state is persisted in the graph build** (keeps it pure/byte-identical, §12).
- **Match semantics (fixed, not the implementer's choice):** text normalized to NFC + casefolded; aliases/keywords match on **whole-word boundaries** (no substring); `required_any` must have ≥1 hit or the concept does not match; any `negative_keywords` hit vetoes; `aliases` outrank `keywords` when both fire (recorded `trigger` = highest-precedence hit; ties broken lexicographically). No stemming in MVP.
- **Confidence** is a fixed enum → fixed decimals (`alias=1.00`, `required_any+positive=0.80`, `positive-only=0.50`), serialized as 2-decimal strings, never a free float (§12).
- Each `about` edge stores `trigger` + `confidence` + `provenance`.
- **`trigger` is per `(entry, concept)` edge, not per entry** — each matching concept yields its own edge with its own highest-precedence trigger. Edges serialize sorted by `(entry_id, concept_slug)`.
- **Link-diff is a *view*, not build state (§8/§12):** `render_views.py` takes an explicit `--baseline <previous knowledge-graph.json>`; the diff (`views/link-diff.md`) is `current ⊖ baseline`. No baseline → empty diff section. The graph build never reads a previous build → determinism holds. A single alias newly linking **> 50** entries is flagged loud in the diff.
- **Relink:** the build is stateless, so linking always reflects current `concepts.yaml` + entries; `content_hash` changes only matter for the rename matcher and "updated" flags, not edge correctness.

### 5.3 Overrides

```yaml
# Science/.living/graph/overrides.yaml
schema_version: 1
force_about: [ { entry: "<id>", concept: "<slug>" } ]   # manual edge the matcher missed
block_about: [ { entry: "<id>", concept: "<slug>" } ]   # suppress a false match
```
Overrides are the only source of `provenance: manual` edges. `block_about` always wins over an auto match.

**Stale overrides.** A `force_about`/`block_about` whose `entry` is tombstoned/unknown, or whose `concept` no longer exists, is **dropped with a warning in the validation report — the build does not hard-fail** (a source edit must not break the build). Stale overrides never produce a dangling edge (§12).

### 5.4 Status invariant

A concept is `confirmed` only with **≥2 entries from ≥2 distinct project families** (§9). A human-curated concept with one entry is `curated_singleton` (explicitly allowed — resolves the §3/§8 conflict Codex flagged). Auto-proposed single-entry concepts stay `candidate` and are excluded from the confirmed graph until curated.

`status` is exactly one of `{candidate, confirmed, curated_singleton}` (mutually exclusive). `curated_singleton` is **not** `confirmed`; both are "live" (in the graph), while `candidate` is excluded from the confirmed graph.

## 6. The hard part: concept lifecycle

```
seed    organic tags + entry-title noun-phrases ──(propose_concepts, LLM)──▶ proposals/   (human prunes into concepts.yaml)
link    deterministic typed match rules ──▶ about edges (+ trigger, confidence, diff)
grow    new/unmatched entries ──▶ candidate-concept report (clustering signal)            (human reviews)
merge   near-duplicate concepts ──(propose_concepts, LLM)──▶ proposals/                    (human approves via registry edit)
gc      tombstoned entries / removed concepts ──▶ stale-concept report (§11)
```
The LLM never edits `concepts.yaml`. It writes to `graph/proposals/`; a human copies approved items into the registry.

## 7. Crystallization & elevation

Represented as edges climbing a concept's evidence stack:

```
entries on a concept ──crystallizes──▶ convention ──elevates──▶ global-knowledge
```

- **Threshold (crystallizes):** a concept with ≥ `CRYSTALLIZE_MIN` (default 3) corroborating entries spanning ≥ `CRYSTALLIZE_FAMILIES` (default 2) **distinct project families**.
- **elevates:** a convention whose backing concept spans ≥2 families and that already exists in `~/.claude/knowledge/` is linked as elevated.

**Important correction (Codex P0):** existing mycelium scripts are **adapted/referenced, not depended on** for graph correctness:
- `detect_recurrence.py` only reads `learnings.md` and assumes `ambient-awareness`/`structural` mitigation types — usable as a *candidate signal* only.
- `crystallize_findings.py` is a findings *registry builder*, not the learning→convention flow.
- `generate_index.py` derives `L-N`/`D-N` by order and scans only top-level files — not reusable for stable extraction.

The map ships its **own** extract/link/build/view machinery and treats these as optional signals. Crystallization/elevation here is *re-representing flows the operator already runs as graph edges* — it does not invoke or require those scripts to be correct.

## 8. Generation pipeline

Deterministic Python, with the single LLM step fully fenced out:

| Component | Role | Deterministic? |
|---|---|---|
| `graph_model.py` | versioned schemas (Entry/Concept/Edge/Facet) — single source of shape | n/a |
| `extract_entries.py` | parse `.living/*` per `source_shape` → Entry objs + stable ids + tombstones | yes |
| `concept_registry.py` | load + validate `concepts.yaml`, `overrides.yaml` | yes |
| `link_entries.py` | typed-rule matcher → `about` edges + trigger/confidence + link-diff | yes |
| `build_graph.py` | assemble nodes+edges → `knowledge-graph.json`; enforce invariants (§12) | yes |
| `render_views.py` | projections (incl. `link-diff` vs an explicit `--baseline` snapshot §5.2; `would-demote`/`effective-status` report §11): lifecycle · cross-project-concept · elevation ladder · stale-concept · unmapped-projects · link-diff | yes |
| `build_vault.py` | Obsidian vault: **ProjectHub notes** (per project → lifecycle stages → its entries), concept notes, and entry-stub notes — all `[[wikilinked]]` | yes |
| `propose_concepts.py` | **LLM, on-demand, separate command**; writes `proposals/` only — never read by build | NO (fenced) |
| `map` (command + hooks) | orchestration; SessionStart injects concept summary; **hooks added LAST** | n/a |

## 9. Project identity & family dedup

Path-derived names break on rename/symlink. A canonical registry is the source:

```yaml
# Science/.living/graph/projects.yaml
schema_version: 1
projects:
  - id: sckg
    name: "Scientific Claims Knowledge Graph"
    path: "Scientific Claims Knowledge Graph"
    family: claims-graph
    has_living: true
  - id: prefilter
    name: "scientific-claims-prefilter"
    path: "scientific-claims-prefilter"
    family: claims-graph        # SAME family as SCKG (copied corpora — NOT independent evidence)
    has_living: true
  - id: autoreview
    name: "AutoReview"
    path: "AutoReview"
    family: autoreview
    has_living: true
  - id: autosci
    name: "Autonomous Science"
    path: "Autonomous Science"
    family: autonomous-science
    has_living: true
```

**Cross-project thresholds count distinct `family`, never raw project.** SCKG + prefilter (mirrors) count as **one** family — so a copied-corpus match cannot fake a transfer edge.

`path` is resolved/normalized (realpath, trailing slash stripped) and validated unique at load; two project entries resolving to the same real path is a hard error. Project ids are the canonical handle downstream; `path` only locates `.living/` and is never an identity.

## 10. No-`.living/` projects

Projects without `.living/` (Gastruloids, SpaceBar, Loic, MetaBioBench, Hughes Lab) are listed in `views/unmapped-projects.md` so their **absence is visible** rather than silent. No extraction is attempted for them this phase.

## 11. GC, staleness, deletion

- Tombstoned entries: their `about` edges drop on the next build; the entry-stub note moves to `vault/_tombstoned/` (kept for backlink integrity, marked).
- A concept whose live evidence falls below threshold gets a generated **effective status** (`effective_status` in `knowledge-graph.json`) and a **"would-demote" line in `views/stale-concepts.md`**. The build **never mutates** the hand-edited `concepts.yaml` — demotion is report-only; the human edits the registry if they agree.
- A concept removed from `concepts.yaml` removes its node + edges; its vault note is deleted on rebuild (vault is disposable).

## 12. Determinism & idempotency rules

- **No wall-clock** (`now()`) in any tested artifact. Build bookkeeping (build number, timestamp) lives only in a separate, **snapshot-excluded** `build-meta.json`; never an input to sorting or to `entry-ids.json` (§4.4).
- **Canonical serialization:** JSON with `sort_keys=True`, `ensure_ascii=False`, `\n` newlines, no trailing whitespace; YAML with sorted keys, block style, no anchors/aliases, LF. Hand-edited source files (`concepts.yaml`, `overrides.yaml`, `projects.yaml`) are **read-only inputs**, never re-serialized by the build (preserves human comments/order, avoids round-trip drift). **Only the explicit `map migrate` command (§13) ever rewrites a versioned file; the normal build never does.**
- **Sort every intermediate collection** (never iterate a `set`/`dict` in insertion/hash order): entries, edges, triggers, aliases, keywords. Multi-trigger ties broken lexicographically (§5.2).
- **Confidence** serialized as fixed 2-decimal strings, never raw floats.
- **LLM output is never an input** to `build_graph.py` (§6/§8).
- **Link-diff baseline is the only cross-build state, and it is a view input, not build state** (§5.2) — `build_graph.py` is a pure function of (entries, `concepts.yaml`, `overrides.yaml`, `projects.yaml`).
- **Caching is an optional optimization, not a correctness mechanism:** any mtime/content-hash cache must produce output identical to a non-cached recompute **against the same `entry-ids.json` ledger**; the byte-identical acceptance test fixes the ledger + registries and re-runs (it does not start from an empty ledger).
- **Build invariants (hard assertions):** no dangling edges (every endpoint resolves to a live, non-tombstoned node — catches stale overrides §5.3); every `confirmed` concept has ≥2 entries spanning ≥2 families; `curated_singleton` concepts are exempt from that count but must be human-flagged; no orphan elevation nodes.
- **Acceptance:** two consecutive builds on unchanged inputs (entries + the hand-edited registries + the same `entry-ids.json` ledger) produce **byte-identical** `knowledge-graph.json` and views.

## 13. Schema versioning

Every generated/curated file carries an integer `schema_version`. The **normal build is read-only on the hand-edited registries and fails closed on ANY version mismatch** (newer or older) with an actionable message telling the operator to migrate. **Schema migration is a separate explicit `map migrate` command** — the only thing that rewrites a versioned file: it applies the deterministic upgrade transform from `graph/migrations/NNN-*.md` (one note per bump: what changed + the transform) and rewrites at the new version. This keeps the normal build's read-only/determinism guarantee (§12) intact. Versioned files: `concepts.yaml`, `overrides.yaml`, `projects.yaml`, `entry-ids.json`, `entry-facets.yaml`, `knowledge-graph.json`.

## 14. File layout

```
Science/.living/graph/
  projects.yaml          # canonical project identity + family (human-edited)
  concepts.yaml          # concept registry + typed match rules (human-edited, source of truth)
  overrides.yaml         # force_about / block_about (human-edited)
  entry-facets.yaml      # stage overlay keyed by stable id (auto + human override)
  entry-ids.json         # stable identity map + tombstones (generated, persistent)
  knowledge-graph.json   # full node+edge graph (generated, for Claude)
  proposals/             # LLM concept/merge proposals (generated; never auto-consumed)
  vault/                 # generated Obsidian vault (real [[links]]); disposable
    projects/<id>.md     #   ProjectHub: project → lifecycle stages → its entries (the "deconstruct the paper" view)
    concepts/<slug>.md   #   Concept: definition + every entry that attaches, across projects
    entries/<id>.md      #   Entry stub: facets + backlinks to its ProjectHub + concepts + source .living file
  views/                 # generated projections (lifecycle, cross-project, elevation, stale, unmapped, link-diff)
```
Per-project `.living/` source files are **read in place and never modified.**

## 15. Pilot scope

**Pilot projects:** `sckg` + `autoreview` + `autosci` — genuinely independent project families.

**Cross-family concepts to demonstrate the thesis** (verified present in the real corpora during review, not aspirational): **`llm-extraction`**, **`evidence`**, **`knowledge-graph`/`dedup`**, **`claim-governance`**. A **golden labeled fixture must confirm** each links to entries in ≥2 of the three families before the pilot "passes." `geo-data-access` is **demoted to an intra-family SCKG signal** — review found it project-local to AutoSci, not a verified cross-family concept; it must not be the headline cross-family example.

**Dedup test fixture:** the `sckg ↔ prefilter` mirror relationship proves `project_family` dedup correctly **refuses** to count copied corpora as cross-project evidence.

**Deferred to phase 2:** no-`.living/` projects; full elevation-to-global automation; concept-to-concept `relates` enrichment; stemming/fuzzy matching.

## 16. Testing

- **Parser round-trip** per `source_shape`, with **golden fixtures from real AutoReview / SCKG / AutoSci variants** (not synthetic): exclusion cases (§4.3: log/registry/template sections must NOT extract); non-signature sub-headings (`### Context`/`### Insight`) NOT extracted; and **mixed-depth real entries** — dated/`D-N`/`L-N` headings at both `##` and `###` in the same file (from the real SCKG/AutoReview/AutoSci files) correctly extracted as separate entries.
- **Stable-ID persistence** (§4.4): edit body → id unchanged; edit **title/heading** → id unchanged via rename matcher; two similar same-date entries → **do NOT cross-bind** (one-to-one assignment); delete → tombstone; reappear → reactivates same id ONLY via exact-key match (a new similar entry does NOT steal a tombstoned id); two genuine duplicate headings → distinct ids via ordinal suffix; `F-001` in two topic files → distinct ids via path+row namespacing.
- **Linker** precision/recall vs a hand-labeled golden entry→concept set; assert `required_any`/`negative_keywords` suppress known false matches; assert whole-word boundary (no substring) and alias>keyword precedence.
- **Stale overrides** (§5.3): `force_about` to a tombstoned entry / removed concept → dropped + reported, build succeeds, no dangling edge.
- **Pilot cross-family** (§15): fixture confirms each named concept links ≥2 families; the `sckg↔prefilter` mirror does **not** satisfy the threshold.
- **Graph invariants** (§12) as hard assertions.
- **Determinism**: byte-identical re-run; sorted-output check; confidence as fixed decimals.
- **View snapshots** including `unmapped-projects`, `link-diff` (with and without `--baseline`), and `stale-concepts` (report-only, no source mutation).

## 17. Build order (hooks last)

1. **M0** — `graph_model.py` schemas (§4.7) + the §4.3 inclusion/exclusion + collision policy + the §4.6 stage ruleset config + golden fixtures (incl. exclusion/nesting cases).
2. **M1** — `extract_entries.py`: immutable-id resolution + rename matcher + tombstones (§4.4); facet overlay incl. stage assignment (§4.6) and stale-key handling (§4.5).
3. **M2** — `concept_registry.py` (load/validate/migrate) + `link_entries.py` (match semantics §5.2) + golden link set + overrides (§5.3). (Link-*diff* deferred to M4 — needs the baseline graph format from M3.)
4. **M3** — `build_graph.py`: `knowledge-graph.json` + `effective_status` + invariants + determinism (§12). Defines the baseline snapshot format the diff consumes.
5. **M4** — `render_views.py` (incl. `link-diff` vs `--baseline`, stale-concepts report, unmapped-projects, lifecycle projection) + `build_vault.py` (**ProjectHub** + concept + entry notes).
6. **M5** — `propose_concepts.py` (LLM, fenced; consumes M2/M4 unmatched-entry + candidate reports).
7. **M6** — `map` command + SessionStart/Stop hooks (**last**, so parser bugs are not session noise).

Each milestone is independently testable against the golden fixtures; the only cross-milestone artifact dependency is the baseline-snapshot format (defined M3, consumed M4), called out above.

## 18. Changelog

### rev 1 (Codex review #1, 2026-06-14)
- P0: reuse language → **adapt/reference**, own machinery (§7); facets → **overlay** (§4.5); `source_shape` parser contract (§4.3); fingerprint identity + tombstones (§4.4); typed concept match rules + link provenance + diff + relink semantics (§5); **LLM step fenced** + determinism rules (§8, §12); `curated_singleton` invariant (§5.4).
- P1: schema versioning (§13); edge overrides (§5.3); GC/tombstone/stale (§11); canonical project ids + family dedup (§9); `unmapped_projects` visibility (§10); performance via content-hash caching.
- Pilot: prefilter → autosci as the independent third; prefilter retained as dedup test fixture (§15).

### rev 2 (Codex review #2, 2026-06-14 — review of the written spec)
- **P0 identity** (§4.4): replaced self-contradicting "heading-in-fingerprint + title-stable" with an **immutable assigned id** + `current/previous_fingerprints` + rename matcher; finding ids namespaced `project+path+row_id`; removed `last_seen_build` (determinism leak).
- **P0 parser** (§4.3): explicit include/exclude lists, entry-level nesting rule, template guard.
- **P0 stale state**: facets (§4.5) and overrides (§5.3) vs tombstoned/removed → dropped + reported, build does not fail.
- **P0 link-diff vs determinism**: diff moved to a `render_views.py` **view against an explicit `--baseline`** (§5.2/§12); build stays pure.
- **P0 stage**: defined the deterministic ordered ruleset (§4.6) instead of an undefined heuristic; `unassigned` added.
- **P0 pilot**: replaced aspirational concepts with **review-verified cross-family concepts** + golden-fixture gate; demoted `geo-data-access` (§15).
- **P1**: field-level schemas (§4.7); matcher semantics — normalization/word-boundary/precedence/confidence quantization (§5.2); schema_version fail-closed + migrations (§13); canonical serialization, hand-edited files read-only (§12); path normalization (§9).
- **P2**: concept demotion is **report-only** via `effective_status` (§11/§8); performance = caching as a verified optimization over the byte-identical baseline.

### rev 3 (Codex review #3, 2026-06-14 — narrow pass on interlocking sections, ground-checked vs real files)
- **P0 parser** (§4.3): depth-based entry detection was demonstrably wrong (real files mix `##`/`###` entries) → **signature-based** detection at any depth.
- **P0 rename matcher** (§4.4): defined the body-similarity metric (token-set Jaccard, τ=0.80), bounded candidate set, **greedy one-to-one assignment**, near-threshold flagging.
- **P0 tombstone theft + cold rebuild** (§4.4/§3.4/§12): fuzzy rename excludes tombstoned ids; `entry-ids.json` reclassified as **persistent identity state**; byte-identical scoped to a shared ledger.
- **P0 facet key** (§4.5): example corrected to the immutable `e-00017` id.
- **P0 §12↔§13**: schema migration is a separate explicit `map migrate` command; the normal build never rewrites versioned files.
- Tests (§16) extended for mixed-depth entries, one-to-one rename, and tombstone non-theft.

### post-review refinement (user, 2026-06-14)
- Added **ProjectHub** navigational nodes to the vault (§4.1 / §8 / §14 / §17 M4) so the **project → stage → entry** hierarchy is clickable, matching the original "project base node, deconstruct underneath" vision. Clarified that project + lifecycle-stage are analytical *facets* but materialized as navigable nodes in the vault; **concept (what about) and lifecycle-stage (which phase) are orthogonal axes**, every entry carrying both plus its project.
