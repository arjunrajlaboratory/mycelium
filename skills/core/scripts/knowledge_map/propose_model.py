"""
propose_model.py — Dataclasses and helpers for the concept-proposal pipeline.

ClusterSummary describes a cluster of related entries (from clustering/TF-IDF).
ProposedConcept is the output of a labeler (offline or LLM); its to_yaml_block()
method returns a concepts.yaml-compatible mapping ready for human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """
    Normalise *text* into a kebab-case slug that matches SLUG_RE.

    Steps:
      1. Lowercase + strip.
      2. Replace any run of non-alphanumeric characters with a single hyphen.
      3. Collapse repeated hyphens.
      4. Strip leading/trailing hyphens.
      5. Truncate to 40 characters (on a hyphen boundary when possible).
      6. Fall back to "concept" if the result is empty.
    """
    s = text.lower().strip()
    # Replace non-alphanumeric runs with hyphen
    s = re.sub(r"[^a-z0-9]+", "-", s)
    # Collapse repeated hyphens
    s = re.sub(r"-{2,}", "-", s)
    # Strip leading/trailing hyphens
    s = s.strip("-")

    # Truncate to ~40 chars on a hyphen boundary where possible
    if len(s) > 40:
        truncated = s[:40].rstrip("-")
        # Back up to the last hyphen if one exists in the truncated segment
        last_hyphen = truncated.rfind("-")
        if last_hyphen > 0:
            truncated = truncated[:last_hyphen]
        s = truncated.strip("-")

    if not s or not SLUG_RE.match(s):
        return "concept"
    return s


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ClusterSummary:
    """Summary of a single entry cluster produced by the clustering stage."""

    cluster_id: int
    entry_ids: list[str]  # e-NNNNN ids in cluster
    size: int
    families: list[str]  # distinct families spanned, sorted
    projects: list[str]  # distinct project_ids, sorted
    rep_titles: list[str]  # representative titles (<=8), closest-to-centroid first
    rep_bodies: list[str]  # parallel body snippets (<=400 chars each)
    tfidf_terms: list[str]  # top TF-IDF terms, sorted by weight desc


@dataclass
class ProposedConcept:
    """
    A concept candidate produced by an offline or LLM labeler.

    ``to_yaml_block()`` returns a concepts.yaml-compatible mapping so an
    approved block can be pasted straight into the registry.
    """

    slug: str
    label: str
    definition: str
    keywords: list[str]
    aliases: list[str]
    cluster_id: int
    size: int
    families: list[str]
    projects: list[str]
    example_entry_ids: list[str]  # up to 8
    source: str  # "llm" or "offline-tfidf"

    def to_yaml_block(self) -> dict:
        """
        Return a concepts.yaml-compatible mapping for this candidate.

        The top-level key is the slug; the value contains all fields needed
        to paste directly into concepts.yaml after human review.  The
        ``_candidate_meta`` sub-block is informational only.
        """
        return {
            self.slug: {
                "label": self.label,
                "definition": self.definition,
                "status": "candidate",
                "match_mode": "hybrid",
                # NOTE: the registry/linker reads `positive_keywords` (and
                # `aliases`), NOT `keywords`. Emitting the wrong key silently
                # drops all match rules → zero edges. Keep this aligned with
                # concept_registry.py.
                "positive_keywords": list(self.keywords),
                "aliases": list(self.aliases),
                "project_scope": None,
                "relates": [],
                "parent": None,
                "_candidate_meta": {
                    "size": self.size,
                    "families": list(self.families),
                    "projects": list(self.projects),
                    "example_entry_ids": list(self.example_entry_ids),
                    "source": self.source,
                    "cluster_id": self.cluster_id,
                },
            }
        }
