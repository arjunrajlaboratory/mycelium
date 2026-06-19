# Knowledge Map — Inter-Module Interface Contract

**Rev:** 1 (2026-06-14)
**Status:** Frozen for M1–M4 implementation.

All downstream modules import exclusively from `graph_model` (flat intra-package imports —
the package is run with its own directory on `sys.path`, so no relative-import prefix is needed):

```python
from graph_model import Entry, Concept, Edge, Facet, Graph, ProjectMeta, ProjectHub
from graph_model import EdgeType, EntryKind, EntryStatus, MatchMode, Provenance
from graph_model import Stage, StageSource, SourceShape, ConceptStatus
from graph_model import canonical_json, confidence_for, normalize_text, sha256_hash, token_set
from graph_model import SCHEMA_VERSION, RENAME_TAU, MASS_LINK_THRESHOLD
```

`graph_model` has zero third-party dependencies (stdlib only).

---

## Canonical output directory

```
<portfolio_root>/.living/graph/
  concepts.yaml        # human-edited source of truth — NEVER rewritten by the normal build
  projects.yaml        # human-edited source of truth — NEVER rewritten by the normal build
  overrides.yaml       # human-edited source of truth — NEVER rewritten by the normal build
  entry-ids.json       # persistent identity ledger (generated, version-controlled)
  entry-facets.yaml    # stage overlay (auto-generated + human override; version-controlled)
  knowledge-graph.json # full node+edge graph for Claude (generated, disposable)
  vault/               # Obsidian-compatible markdown (generated, disposable)
  views/               # projections (lifecycle, cross-project, elevation, link-diff, …) (disposable)
```

All JSON written by the pipeline MUST go through `canonical_json()` from `graph_model`.
This is the single serialization entrypoint; it guarantees `sort_keys=True`,
`ensure_ascii=False`, `indent=2`, and a trailing newline — required for byte-identical
determinism across builds (§12).

Hand-edited YAML source files (`concepts.yaml`, `overrides.yaml`, `projects.yaml`)
are **read-only inputs** to the normal build — they are never re-serialized (to preserve
human comments and ordering). Only the explicit `map migrate` command may rewrite them.

---

## Module signatures

Every module below defines a result dataclass local to that module (not in `graph_model`)
and exposes exactly one primary public function.  The signatures below are VERBATIM — do not
rename parameters, change argument order, or alter return types.

---

### `extract_entries.py`

Parses `.living/` source files for every project with `has_living=True`, resolves stable ids
against the persistent ledger, and assigns stage facets.

```python
from dataclasses import dataclass
from pathlib import Path
from graph_model import Entry, Facet, ProjectMeta

@dataclass
class ExtractResult:
    entries: list[Entry]
    facets:  dict[str, Facet]   # keyed by entry id
    ledger:  dict               # the updated entry-ids.json payload (ready to write)
    report:  list[str]          # human-readable warnings / flags from extraction

def extract_entries(
    portfolio_root: Path,
    projects: list[ProjectMeta],
    id_ledger: dict,            # the current entry-ids.json payload (may be empty on cold build)
) -> ExtractResult:
    ...
```

**Contract:**
- Entries are returned sorted by `(project_id, source_path, id)`.
- Every `Entry.content_hash` is produced by `sha256_hash()` from `graph_model`.
- The returned `ledger` dict is JSON-safe and ready to be written via `canonical_json()`.
- Unparseable sections are appended to `report` and never silently dropped.
- Template/sample guards, exclusion rules (log/, INDEX.md, etc.), and tombstone handling
  are all the responsibility of this module (see §4.3/§4.4 of the design spec).

---

### `concept_registry.py`

Loads and validates the curated concept registry and project list.

```python
from dataclasses import dataclass
from pathlib import Path
from graph_model import Concept, ProjectMeta

@dataclass
class Registry:
    concepts:     list[Concept]
    projects:     list[ProjectMeta]
    force_about:  list[dict]    # raw overrides from overrides.yaml — [{entry, concept}, ...]
    block_about:  list[dict]    # raw overrides from overrides.yaml — [{entry, concept}, ...]

def load_registry(graph_dir: Path) -> Registry:
    ...
```

**Contract:**
- Raises `SystemExit(1)` with an actionable message on any `schema_version` mismatch.
- Validates every `Concept.slug` against `^[a-z0-9][a-z0-9-]*$`; invalid slugs are errors.
- `force_about` and `block_about` are returned as raw dicts (not typed); downstream consumers
  validate stale references and emit warnings rather than hard-failing (§5.3).
- Projects with duplicate resolved paths are a hard error (§9).

---

### `link_entries.py`

Applies typed match rules from the registry to produce `about` edges.

```python
from dataclasses import dataclass
from graph_model import Edge, Entry
from concept_registry import Registry

@dataclass
class LinkResult:
    edges:  list[Edge]
    report: list[str]   # warnings: stale overrides, mass-link flags (> MASS_LINK_THRESHOLD)

def link_entries(entries: list[Entry], registry: Registry) -> LinkResult:
    ...
```

**Contract:**
- All text matching uses `normalize_text()` + whole-word boundaries (no substring matching).
- Match precedence: alias > required_any + positive > positive-only (§5.2).
- `confidence` values MUST be produced via `confidence_for()` — never raw floats.
- `block_about` always suppresses an auto match; a stale `block_about` is warned and skipped.
- A `force_about` to a tombstoned/unknown entry or removed concept is warned and skipped;
  the build does NOT fail (§5.3).
- Edges are returned sorted by `(from_id, to_id)`.
- A single alias newly linking > `MASS_LINK_THRESHOLD` entries is appended to `report`
  (the diff comparison uses this to flag loud changes; §5.2).

---

### `build_graph.py`

Assembles the full node+edge graph, enforces invariants, and writes `knowledge-graph.json`.

```python
from pathlib import Path
from graph_model import Entry, Facet, Edge, Graph
from concept_registry import Registry

def build_graph(
    entries:  list[Entry],
    facets:   dict[str, Facet],
    edges:    list[Edge],
    registry: Registry,
) -> Graph:
    ...

def validate_graph(graph: Graph) -> list[str]:
    """
    Enforce build invariants (§12).  Returns invariant-violation messages; empty list = ok.

    Invariants checked:
    - No dangling edges: every from_id and to_id resolves to a live, non-tombstoned node.
    - Every `confirmed` concept has >= 2 entries spanning >= 2 distinct project families.
    - `curated_singleton` concepts are exempt from the family-count rule but must carry
      that status explicitly.
    - No orphan elevation nodes (conventions/global_knowledge with no backing edge).
    - Stale force_about/block_about (tombstoned entry or removed concept) produce
      warnings in the caller's report, NOT violations here.
    """
    ...
```

**Contract:**
- `build_graph` is a **pure function**: given the same inputs it always returns the
  same `Graph` (§12).  It never reads from disk or from a previous build.
- `effective_status` on each `Concept` is computed here from live entry evidence and
  written into the returned `Graph` (it is NOT stored in `concepts.yaml`).
- `knowledge-graph.json` is written by the orchestrator (`map` command), not by this
  module — this module only returns the `Graph`.

---

### `render_views.py`

Generates all human-readable projection views from an assembled graph.

```python
from pathlib import Path
from graph_model import Graph, Facet

def render_views(
    graph:         Graph,
    facets:        dict[str, Facet],
    out_dir:       Path,
    baseline_path: Path | None = None,
) -> None:
    ...
```

**Contract:**
- Writes all views under `out_dir/` (typically `<portfolio_root>/.living/graph/views/`).
- Views produced: `lifecycle.md`, `cross-project-concept.md`, `elevation-ladder.md`,
  `stale-concepts.md`, `unmapped-projects.md`, `link-diff.md`.
- `link-diff.md` is only populated when `baseline_path` is provided (§5.2).
  With no baseline, the diff section is empty but the file is still written.
- `stale-concepts.md` is **report-only** — this module never mutates `concepts.yaml`.
- `effective_status` on demoted concepts is read from `graph.concepts`; the view labels
  its lifecycle projection *heuristic* wherever `unassigned` entries appear.
- All intermediate collections are sorted before iteration (never rely on dict/set order).

---

### `build_vault.py`

Generates an Obsidian-compatible markdown vault with real `[[wikilinks]]`.

```python
from pathlib import Path
from graph_model import Graph, Facet

def build_vault(
    graph:   Graph,
    facets:  dict[str, Facet],
    out_dir: Path,
) -> None:
    ...
```

**Contract:**
- Writes vault under `out_dir/` (typically `<portfolio_root>/.living/graph/vault/`).
- Structure:
  - `vault/projects/<project_id>.md` — ProjectHub note: project overview → lifecycle stages
    → its entries (clickable hierarchy, §4.1/§8).
  - `vault/concepts/<slug>.md` — Concept note: definition, status, all linked entries
    across projects.
  - `vault/entries/<entry_id>.md` — Entry stub: facets, backlinks to ProjectHub and
    concepts, link to source `.living/` file.
  - `vault/_tombstoned/<entry_id>.md` — Tombstoned entry stubs (kept for backlink
    integrity, visibly marked).
- All internal links use `[[wikilink]]` syntax (Obsidian-compatible).
- The vault is fully disposable and regenerated on every build; it is never the source
  of truth for any data.

---

## Serialization rules (§12)

1. All JSON output goes through `canonical_json(obj)` from `graph_model`.
2. Confidence values are always `str` with exactly 2 decimal places, produced by
   `confidence_for()` — never raw `float`.
3. No `datetime.now()` or wall-clock calls in any deterministic artifact.
   Build timestamps live only in `build-meta.json` (snapshot-excluded).
4. Sort every intermediate collection before iteration; never iterate `set` or `dict`
   in insertion/hash order.
5. Two consecutive builds on unchanged inputs (same `entry-ids.json` ledger + registries)
   MUST produce byte-identical `knowledge-graph.json` and views.

---

## Schema versioning (§13)

Every generated/curated file carries `schema_version: SCHEMA_VERSION` (currently `1`).
The normal build **fails closed** on any version mismatch with an actionable message.
Schema migration is a separate `map migrate` command — the only operation that rewrites
a versioned file.  The normal build never rewrites hand-edited source files.
