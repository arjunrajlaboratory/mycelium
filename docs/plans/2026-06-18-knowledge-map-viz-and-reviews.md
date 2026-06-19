# Knowledge-Map: Graph Re-tiering + Reviews/Transfers as Supporting Nodes

Date: 2026-06-18 · Branch: `feat/knowledge-map-viz-reviews` (stacked on #61) · Status: approved design

## Problem

A real build renders ~5,019 nodes. The navigational backbone (70 concepts + 10 project
hubs) is 1.6% of nodes and is swamped by the detail tier (3,523 entries + 1,416 logs =
98%). The Obsidian global graph is an unreadable hairball; projects are invisible and the
1,416 log nodes (magenta) blend into everything. Separately, the richest *ignored* mycelium
outputs — adversarial **review reports** (`outputs/reviews/`) and **knowledge-transfer
reports** (`outputs/knowledge-transfers/`) — are absent from the graph.

## Decisions (locked)

1. **Keep all nodes visible but re-tier them visually** (not a backbone-only filter).
2. **Reviews → `project → review(primary) → finding(sub-nodes)` hierarchy.** Findings link
   ONLY to their parent review; the review links normally to its project. Transfers mirror
   this (`project → transfer → transfer-item`).

## Part 1 — Re-tier the global graph (`build_vault.py` graph.json writer + `cli.py`)

Obsidian sizes nodes by link-degree only (no per-group size); the degree hierarchy already
favors the backbone (projects/concepts have high in-degree). Levers used:

- **Opacity tiering via color-group alpha** — the key move:
  - Backbone, full/near-full alpha + vivid: `path:projects/` (a=1.0), `path:concepts/bridge/`
    `path:concepts/confirmed/` `path:concepts/curated/` (a=1.0), `path:concepts/candidate/` (a=0.9).
  - Supporting primaries, bold: `path:supporting/reviews/` `path:supporting/transfers/` (a=1.0).
  - Finding sub-nodes by **severity tag** (placed AFTER path groups so they win):
    `tag:severity/critical` red (a=1.0), `tag:severity/major` orange (a=0.95),
    `tag:severity/minor` gray (a=0.8), `tag:severity/unknown` slate (a=0.7).
  - Detail tier, dimmed low alpha so it recedes to "dust": `path:entries/finding/` (a=0.30),
    `path:entries/decision/` (a=0.28), `path:entries/learning/` (a=0.25),
    `path:entries/other/` (a=0.25), `path:logs/` (a=0.16, faintest).
- **`nodeSizeMultiplier`: 2.2** (amplify degree → backbone balloons), **`lineSizeMultiplier`: 0.35**
  (fade the edge mesh haze), **`textFadeMultiplier`: -1.5** (labels appear sooner on zoom),
  **`showArrow`: false**.
- **Forces** (de-clump): `repelStrength` ~12–15, `linkDistance` ~120, `linkStrength` ~0.6,
  `centerStrength` ~0.3 (tune toward spreading the blob).
- **`showOrphans`: false**, **`hideUnresolved`: false**, **`showTags`: false**, **`search`: ""**.
- **Rewrite graph.json on every build by default** (today it is write-once AND Obsidian
  overwrites it on interaction → settings silently reset). Add CLI flag `--keep-graph-config`
  to preserve a hand-tuned config. Also **delete the stale `colorgroups-by-project.json`**
  from `.obsidian/` (superseded; competing config).

Honest scope: this makes the backbone legible against a dimmed detail field; it does not turn
5k nodes into a clean diagram. Detail is read via a node's local graph or the filter box.

## Part 2 — Data model (`graph_model.py`)

New, kind-discriminated dataclass `SupportingNode` (keeps graph_model changes contained):

```
class SupportingKind(Enum): review; finding; transfer; transfer_item
@dataclass SupportingNode:
    id: str                 # deterministic slug-based; NO ledger
    kind: SupportingKind
    title: str
    project_id: str | None  # owning project (review/transfer); None for sub-nodes
    family: str | None
    parent_id: str | None   # finding→review, transfer_item→transfer
    project_ids: list[str]  # projects a primary links to (transfer may list several)
    date: str | None
    severity: str | None    # findings: critical|major|minor|moderate|nit|unknown
    body_excerpt: str       # capped ~2000 chars
    source_path: str
```

Deterministic IDs (stable as long as filename/anchor stable; no mint-ledger needed):
- review: `rv-<project_id>-<file-stem-slug>`
- finding: `<review_id>-f<NN>` (NN = ordinal within report, zero-padded)
- transfer: `tx-<project_id>-<file-stem-slug>`
- transfer_item: `<transfer_id>-i<NN>`

New `EdgeType`s:
- `documents` — primary → project (review→its project; transfer→each project in `project_ids`)
- `detail_of` — sub-node → primary (finding→review; transfer_item→transfer)

Serialize SupportingNode + new edges in canonical JSON (always-emit keys, sorted). Supporting
nodes do NOT participate in concept linking or `effective_status`.

## Part 3 — Extraction (`extract_reviews.py`, new module + `test_extract_reviews.py`)

`extract_reviews(portfolio, projects) -> (supporting_nodes, edges, report)`. Walk each
project's `.living/outputs/reviews/*.md` and `.living/outputs/knowledge-transfers/*.md`.
Skip files ending `-tripwires.md`? No — include; treat as a review. Skip `.pdf`.

Review parsing (tolerant — formats vary):
- **Primary**: H1 (`# Review — …`) → title; capture `**Scope**`, `**Files reviewed**`,
  `**Sub-agents**` and the exec-summary block as body_excerpt. date from filename
  (`YYYY-MM-DD-…`) or `## [YYYY-MM-DD]` heading.
- **Findings**: within/after a `## Findings` section, a finding block is delimited by either
  a `#{2,4} …` heading that contains a finding marker (`F\d+`, `[Critical|Major|Minor|Moderate|Nit]`,
  or a `## [date] title`) OR a block containing a `**Fix**:` / `**Why it matters**` marker.
  Ordinal `NN` assigned in document order. severity = first `\[(Critical|Major|Minor|Moderate|Nit)\]`
  (case-insensitive) in the block, else `unknown`. title = heading text (strip the severity token).
  body_excerpt = the finding block text (capped). Emit `detail_of` finding→review.
  If zero findings parse, emit just the primary + a non-blocking `report` note (don't fail).

Transfer parsing:
- **Primary**: H1 (`# Knowledge Transfer Report — <date>`); `project_ids` = projects named in
  `**Projects scanned**:` resolved against `projects.yaml` (match by name/slug; unresolved → skip
  that link, note in report). body = scan metadata + summary.
- **Items**: `### <n>. <title>` under `## Transfers Identified` → transfer_item sub-nodes,
  `detail_of` → transfer. Ordinal from the number.

Edges: review→project (`documents`), finding→review (`detail_of`), transfer→each project
(`documents`), item→transfer (`detail_of`). NO edges from supporting nodes to concepts/entries.

## Part 4 — Vault rendering (`build_vault.py` + `test_build_vault.py`)

Folders (unambiguous for path color-groups):
- `supporting/reviews/<review-slug>.md` (primaries)
- `supporting/findings/<review-slug>/F<NN>.md` (finding sub-nodes; severity tag in frontmatter)
- `supporting/transfers/<transfer-slug>.md` (primaries)
- `supporting/transfer-items/<transfer-slug>/I<NN>.md` (sub-nodes)

Frontmatter: `type` (review|finding|transfer|transfer-item), `severity` (findings),
`project`/`projects`, `parent`, `date`, `aliases: ["<title>"]`, `tags` incl.
`severity/<sev>` for findings and `support/<kind>`. Links: review → `[[project_id]]` (skip if
hub missing, per existing dangling-link guard); finding → `[[review_id]]`; transfer →
`[[project_id]]` for each; item → `[[transfer_id]]`. Body = excerpt. No concept/entry links.

## Part 5 — Graph assembly (`build_graph.py` + `test_build_graph.py`)

Include supporting nodes + edges in the graph. `validate_graph` invariants:
- `documents` edge: from a review/transfer supporting node, to an existing ProjectHub id.
- `detail_of` edge: from finding→its review (parent exists) / item→its transfer.
- supporting node ids unique; no supporting node feeds `about`/`effective_status`.

## Part 6 — Orchestration (`cli.py`)

`cmd_build`: after `extract_logs`, call `extract_reviews`; pass supporting nodes/edges into
`build_graph`. Add `--keep-graph-config` (preserve graph.json) and `--no-reviews` (skip
supporting ingestion) flags. Reviews ON by default. Fail-closed validation unchanged.

## Testing

- `test_extract_reviews.py` (new): review primary + finding split + severity parse +
  zero-finding fallback; transfer primary + items + project resolution; tolerant of the two
  real formats sampled.
- `test_build_vault.py`: supporting folder routing; graph.json has the alpha-tiered groups +
  severity groups + nodeSize/line multipliers; stale colorgroups-by-project.json removed;
  rewrite-each-build vs `--keep-graph-config`.
- `test_build_graph.py`: supporting node/edge validation invariants.
- Integration: real full build → exit 0, supporting nodes present, `documents`/`detail_of`
  edges present, deterministic rebuild; eyeball the re-tiered graph.

## Out of scope (later)

LOG_REGISTRY metadata, INDEX/registry ingestion, concept↔review cross-links, per-finding
links to the specific entries they critique (kept as content only, per decision #2).
