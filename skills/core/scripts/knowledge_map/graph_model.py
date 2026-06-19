"""
graph_model.py — Versioned data model and serialization helpers for the knowledge-map pipeline.

This is the single source of shape (§4.7).  All other modules import from here.
Python 3.13+, stdlib + (optional) no third-party imports.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Module constants (§4.4, §5.2, §12)
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1
RENAME_TAU: float = 0.80  # Jaccard threshold for rename/title-edit rebind (§4.4)
MASS_LINK_THRESHOLD: int = (
    50  # flag when a single alias newly links > 50 entries (§5.2)
)


# ---------------------------------------------------------------------------
# Enums (str-valued for JSON serialisation)
# ---------------------------------------------------------------------------


class EntryKind(str, Enum):
    learning = "learning"
    decision = "decision"
    finding = "finding"
    convention = "convention"


class SourceShape(str, Enum):
    aggregate_section = "aggregate_section"
    per_entry_file = "per_entry_file"
    finding_topic_ledger = "finding_topic_ledger"
    standalone_finding_file = "standalone_finding_file"


class EdgeType(str, Enum):
    about = "about"
    crystallizes = "crystallizes"
    elevates = "elevates"
    supersedes = "supersedes"
    relates = "relates"
    mentions = "mentions"
    follows = "follows"


class Stage(str, Enum):
    data_registry = "data_registry"
    lit_review = "lit_review"
    planning = "planning"
    analysis = "analysis"
    figure_generation = "figure_generation"
    writing = "writing"
    evaluation = "evaluation"
    infrastructure = "infrastructure"
    unassigned = "unassigned"
    conventions = "conventions"


class ConceptStatus(str, Enum):
    candidate = "candidate"
    confirmed = "confirmed"
    curated_singleton = "curated_singleton"


class Provenance(str, Enum):
    auto = "auto"
    manual = "manual"


class StageSource(str, Enum):
    curated = "curated"
    path = "path"
    keyword = "keyword"
    default = "default"


class EntryStatus(str, Enum):
    active = "active"
    tombstone = "tombstone"


class MatchMode(str, Enum):
    alias = "alias"
    keyword = "keyword"
    hybrid = "hybrid"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectMeta:
    """Canonical project identity loaded from projects.yaml (§9)."""

    id: str
    name: str
    path: str
    family: str
    has_living: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "has_living": self.has_living,
            "id": self.id,
            "name": self.name,
            "path": self.path,
        }


@dataclass
class Entry:
    """A single extracted knowledge entry (learning / decision / finding) (§4.7)."""

    id: str
    kind: EntryKind
    source_shape: SourceShape
    project_id: str
    family: str
    source_path: str
    anchor: str
    line_start: int | None
    line_end: int | None
    title: str
    date: str | None
    tags: list[str]
    body_excerpt: str
    content_hash: str
    status: EntryStatus = EntryStatus.active
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor,
            "body_excerpt": self.body_excerpt,
            "content_hash": self.content_hash,
            "date": self.date,
            "family": self.family,
            "id": self.id,
            "kind": self.kind.value,
            "line_end": self.line_end,
            "line_start": self.line_start,
            "project_id": self.project_id,
            "schema_version": self.schema_version,
            "source_path": self.source_path,
            "source_shape": self.source_shape.value,
            "status": self.status.value,
            "tags": sorted(self.tags),
            "title": self.title,
        }


@dataclass(frozen=True)
class Facet:
    """Stage overlay for one entry, keyed externally by entry id (§4.5/§4.6)."""

    stage: Stage
    stage_source: StageSource

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "stage_source": self.stage_source.value,
        }


@dataclass
class Concept:
    """A curated concept node from concepts.yaml (§5.1)."""

    slug: str
    label: str
    definition: str
    status: ConceptStatus
    aliases: list[str]
    positive_keywords: list[str]
    negative_keywords: list[str]
    required_any: list[str]
    project_scope: list[str] | None
    match_mode: MatchMode
    relates: list[str]
    parent: str | None
    # generated, not stored in source (§4.7 / §11)
    effective_status: ConceptStatus | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "aliases": sorted(self.aliases),
            "definition": self.definition,
            "effective_status": self.effective_status.value
            if self.effective_status
            else None,
            "label": self.label,
            "match_mode": self.match_mode.value,
            "negative_keywords": sorted(self.negative_keywords),
            "parent": self.parent,
            "positive_keywords": sorted(self.positive_keywords),
            "project_scope": sorted(self.project_scope)
            if self.project_scope is not None
            else None,
            "relates": sorted(self.relates),
            "required_any": sorted(self.required_any),
            "slug": self.slug,
            "status": self.status.value,
        }
        return d


@dataclass(frozen=True)
class Edge:
    """A typed, directed relationship between two nodes (§4.2)."""

    from_id: str
    to_id: str
    type: EdgeType
    provenance: Provenance
    trigger: str | None = None
    confidence: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "confidence": self.confidence,
            "from": self.from_id,
            "provenance": self.provenance.value,
            "to": self.to_id,
            "trigger": self.trigger,
            "type": self.type.value,
        }


@dataclass(frozen=True)
class ProjectHub:
    """Navigational vault node — one per project (§4.1, §8)."""

    project_id: str
    name: str
    family: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "name": self.name,
            "project_id": self.project_id,
        }


@dataclass
class LogNode:
    """A single episodic-log entry from a session log file (episodic-log tier)."""

    id: str  # "l-00001" ledger namespace
    project_id: str
    family: str
    session_date: str | None  # "YYYY-MM-DD" from filename, else None
    session_seq: int | None  # NNN ordinal from filename, else None
    title: str
    body_excerpt: str  # first ~500 chars, normalized
    source_path: str
    tags: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)  # concept slugs referenced
    kind: str = "log"  # constant discriminator

    def to_dict(self) -> dict[str, Any]:
        return {
            "body_excerpt": self.body_excerpt,
            "family": self.family,
            "id": self.id,
            "kind": self.kind,
            "mentions": sorted(self.mentions),
            "project_id": self.project_id,
            "session_date": self.session_date,
            "session_seq": self.session_seq,
            "source_path": self.source_path,
            "tags": sorted(self.tags),
            "title": self.title,
        }


@dataclass
class Graph:
    """
    The assembled knowledge graph (§8 build_graph.py output).

    ``conventions`` and ``global_knowledge`` are empty lists in this phase (M0–M4);
    they are reserved for elevation nodes (§7) added in a later milestone.
    ``logs`` holds episodic LogNode records (separate from entries — R1 invariant).
    """

    entries: list[Entry]
    concepts: list[Concept]
    edges: list[Edge]
    project_hubs: list[ProjectHub]
    conventions: list[dict[str, Any]] = field(default_factory=list)
    global_knowledge: list[dict[str, Any]] = field(default_factory=list)
    logs: list[LogNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "concepts": [
                c.to_dict() for c in sorted(self.concepts, key=lambda c: c.slug)
            ],
            "conventions": self.conventions,
            "edges": [
                e.to_dict()
                for e in sorted(
                    self.edges, key=lambda e: (e.from_id, e.to_id, e.type.value)
                )
            ],
            "entries": [e.to_dict() for e in sorted(self.entries, key=lambda e: e.id)],
            "global_knowledge": self.global_knowledge,
            "logs": [l.to_dict() for l in sorted(self.logs, key=lambda l: l.id)],
            "project_hubs": [
                h.to_dict()
                for h in sorted(self.project_hubs, key=lambda h: h.project_id)
            ],
            "schema_version": SCHEMA_VERSION,
        }

    def to_canonical_json(self) -> str:
        return canonical_json(self.to_dict())


# ---------------------------------------------------------------------------
# Helper functions (§12 / §4.4 / §5.2)
# ---------------------------------------------------------------------------


def canonical_json(obj: Any) -> str:
    """
    Single serialization entrypoint for determinism (§12).

    Returns json.dumps with sort_keys=True, ensure_ascii=False, indent=2,
    followed by a trailing newline.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=2) + "\n"


def normalize_text(s: str) -> str:
    """
    NFC-normalize, casefold, collapse whitespace runs to a single space, strip.

    Used by the rename matcher and token-set Jaccard (§4.4) and the linker (§5.2).
    """
    s = unicodedata.normalize("NFC", s)
    s = s.casefold()
    s = " ".join(s.split())
    return s.strip()


def sha256_hash(s: str) -> str:
    """Return ``sha256:<hexdigest>`` of the UTF-8 encoding of *s*."""
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def confidence_for(level: str) -> str:
    """
    Map a match-level label to a fixed 2-decimal confidence string (§5.2 / §12).

    ``"alias"``        → ``"1.00"``
    ``"required_any"`` → ``"0.80"``
    ``"positive"``     → ``"0.50"``
    """
    _MAP: dict[str, str] = {
        "alias": "1.00",
        "required_any": "0.80",
        "positive": "0.50",
    }
    return _MAP[level]


def token_set(s: str) -> set[str]:
    """
    Return the set of whitespace-delimited tokens after normalize_text.

    Used by the rename/title-edit Jaccard matcher (§4.4).
    """
    return set(normalize_text(s).split())


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    entry = Entry(
        id="e-00001",
        kind=EntryKind.learning,
        source_shape=SourceShape.aggregate_section,
        project_id="sckg",
        family="claims-graph",
        source_path="Scientific Claims Knowledge Graph/.living/learnings.md",
        anchor="[2026-04-23] GEO GSM matrix access",
        line_start=42,
        line_end=55,
        title="GEO GSM matrix access",
        date="2026-04-23",
        tags=["geo", "data-access"],
        body_excerpt="Retrieving expression matrices from GEO at the GSM level is faster than series-level for sparse corpora.",
        content_hash=sha256_hash("GEO GSM matrix access body"),
        status=EntryStatus.active,
        schema_version=SCHEMA_VERSION,
    )

    concept = Concept(
        slug="geo-data-access",
        label="GEO data access",
        definition="Retrieving expression matrices from GEO; GSM-level vs series-level access.",
        status=ConceptStatus.confirmed,
        aliases=["GEO", "GSM", "GEO accession"],
        positive_keywords=["CellRanger", "series matrix", "GSE", "supplementary file"],
        negative_keywords=["geometry", "geographic"],
        required_any=["GEO", "GSM", "GSE"],
        project_scope=None,
        match_mode=MatchMode.hybrid,
        relates=[],
        parent=None,
        effective_status=None,
    )

    edge = Edge(
        from_id="e-00001",
        to_id="geo-data-access",
        type=EdgeType.about,
        provenance=Provenance.auto,
        trigger="GEO",
        confidence=confidence_for("alias"),
    )

    hub = ProjectHub(
        project_id="sckg",
        name="Scientific Claims Knowledge Graph",
        family="claims-graph",
    )

    graph = Graph(
        entries=[entry],
        concepts=[concept],
        edges=[edge],
        project_hubs=[hub],
    )

    output = graph.to_canonical_json()
    print(output)
