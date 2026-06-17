"""
test_link_entries.py — Unit tests for link_entries.py

Standalone: adds the knowledge_map directory to sys.path so flat imports work.
Python 3.13+, stdlib unittest only.
"""

import sys
import unittest

sys.path.insert(0, "/Users/mst36/tools/mycelium-main/skills/core/scripts/knowledge_map")

from graph_model import (
    ConceptStatus,
    Entry,
    EntryKind,
    EntryStatus,
    MatchMode,
    Provenance,
    SourceShape,
)
from concept_registry import Registry
from graph_model import Concept
from link_entries import link_entries


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_entry(
    id: str,
    title: str,
    body_excerpt: str = "",
    tags: list[str] | None = None,
    project_id: str = "proj-a",
) -> Entry:
    return Entry(
        id=id,
        kind=EntryKind.learning,
        source_shape=SourceShape.aggregate_section,
        project_id=project_id,
        family="test",
        source_path="test.md",
        anchor="",
        line_start=None,
        line_end=None,
        title=title,
        date=None,
        tags=tags or [],
        body_excerpt=body_excerpt,
        content_hash="sha256:abc",
        status=EntryStatus.active,
    )


def make_concept(
    slug: str,
    aliases: list[str] | None = None,
    positive_keywords: list[str] | None = None,
    negative_keywords: list[str] | None = None,
    required_any: list[str] | None = None,
    project_scope: list[str] | None = None,
    match_mode: MatchMode = MatchMode.hybrid,
) -> Concept:
    return Concept(
        slug=slug,
        label=slug,
        definition="test",
        status=ConceptStatus.candidate,
        aliases=aliases or [],
        positive_keywords=positive_keywords or [],
        negative_keywords=negative_keywords or [],
        required_any=required_any or [],
        project_scope=project_scope,
        match_mode=match_mode,
        relates=[],
        parent=None,
    )


def make_registry(
    concepts: list[Concept],
    force_about: list[dict] | None = None,
    block_about: list[dict] | None = None,
) -> Registry:
    return Registry(
        concepts=concepts,
        projects=[],
        force_about=force_about or [],
        block_about=block_about or [],
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestLinkEntries(unittest.TestCase):
    def test_required_any_gate(self):
        """required_any gate blocks when the mandatory term is absent."""
        entry = make_entry("e1", "we use a novel extraction technique")
        concept = make_concept(
            "test-concept",
            required_any=["mandatory_term"],
            positive_keywords=["extraction"],
        )
        registry = make_registry([concept])
        result = link_entries([entry], registry)
        self.assertEqual(len(result.edges), 0)

    def test_negative_keywords_veto(self):
        """A matching negative keyword vetoes the concept entirely."""
        entry = make_entry("e2", "claim extraction method")
        concept = make_concept(
            "test-concept",
            positive_keywords=["claim"],
            negative_keywords=["extraction"],
        )
        registry = make_registry([concept])
        result = link_entries([entry], registry)
        self.assertEqual(len(result.edges), 0)

    def test_whole_word_no_substring(self):
        """'reclaimed' does NOT match the alias 'claim' (whole-word only)."""
        entry = make_entry("e3", "data reclaimed from archives")
        concept = make_concept(
            "test-concept",
            aliases=["claim"],
        )
        registry = make_registry([concept])
        result = link_entries([entry], registry)
        self.assertEqual(len(result.edges), 0)

    def test_alias_beats_keyword_for_trigger(self):
        """Alias match wins over keyword match: trigger='llm', confidence='1.00'."""
        entry = make_entry("e4", "LLM extraction alias test")
        concept = make_concept(
            "test-concept",
            aliases=["llm"],
            positive_keywords=["extraction"],
            required_any=[],
            match_mode=MatchMode.hybrid,
        )
        registry = make_registry([concept])
        result = link_entries([entry], registry)
        self.assertEqual(len(result.edges), 1)
        edge = result.edges[0]
        self.assertEqual(edge.trigger, "llm")
        self.assertEqual(edge.confidence, "1.00")

    def test_force_about_adds_manual_edge(self):
        """force_about adds a manual edge even when no alias/keyword matches."""
        entry = make_entry("e5", "unrelated content")
        concept = make_concept(
            "test-concept",
            aliases=["nomatch"],
            positive_keywords=[],
            required_any=[],
        )
        registry = make_registry(
            [concept],
            force_about=[{"entry": "e5", "concept": "test-concept"}],
        )
        result = link_entries([entry], registry)
        self.assertEqual(len(result.edges), 1)
        edge = result.edges[0]
        self.assertEqual(edge.provenance, Provenance.manual)
        self.assertIsNone(edge.trigger)

    def test_block_about_removes_auto_edge(self):
        """block_about removes the auto edge for a matching (entry, concept) pair."""
        entry = make_entry("e6", "llm extraction pipeline")
        concept = make_concept(
            "test-concept",
            aliases=["llm"],
            required_any=[],
            positive_keywords=[],
        )
        registry = make_registry(
            [concept],
            block_about=[{"entry": "e6", "concept": "test-concept"}],
        )
        result = link_entries([entry], registry)
        self.assertEqual(len(result.edges), 0)

    def test_stale_force_about_no_crash(self):
        """Stale force_about overrides emit a warning but don't crash."""
        entry = make_entry("e7", "some real entry")
        concept = make_concept("real-concept", aliases=["real"])
        registry = make_registry(
            [concept],
            force_about=[{"entry": "nonexistent-id", "concept": "nonexistent-slug"}],
        )
        result = link_entries([entry], registry)
        # Must not raise; report must mention "stale"
        stale_msgs = [m for m in result.report if "stale" in m]
        self.assertGreater(len(stale_msgs), 0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
