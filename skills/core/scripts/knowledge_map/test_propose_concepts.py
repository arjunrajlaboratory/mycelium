"""
test_propose_concepts.py — Unit tests for the concept-proposer pipeline.

Covers: load_orphans, cluster_embeddings, cluster_tfidf_terms,
        summarize_clusters, project_coverage, write_proposals,
        concept_labeler.label_cluster (offline + llm + fallback).

Run with:
    python -m pytest test_propose_concepts.py -v
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import numpy as np
import pytest
import yaml

# ── inline sys.path fix so the package's flat imports resolve ──────────────
import sys
import os

_PKG = os.path.dirname(os.path.abspath(__file__))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

from propose_model import ClusterSummary, ProposedConcept
from propose_concepts import (
    cluster_embeddings,
    cluster_tfidf_terms,
    load_orphans,
    project_coverage,
    summarize_clusters,
    write_proposals,
)
from concept_labeler import label_cluster


# ── tiny fixtures ──────────────────────────────────────────────────────────


def _minimal_entry(
    eid: str, project_id: str = "proj-a", family: str = "learnings"
) -> dict:
    return {
        "id": eid,
        "project_id": project_id,
        "family": family,
        "title": f"Title of {eid}",
        "body_excerpt": f"Body of {eid}.",
        "kind": "learning",
        "date": "2026-06-01",
        "tags": [],
        "source_path": f"{project_id}/.living/learnings.md",
        "anchor": f"[2026-06-01] {eid}",
    }


def _write_graph(tmp_path: Path, entries: list[dict], edges: list[dict]) -> Path:
    graph = {"entries": entries, "edges": edges}
    kg = tmp_path / "knowledge-graph.json"
    kg.write_text(json.dumps(graph), encoding="utf-8")
    return tmp_path


def _make_summary(
    cluster_id: int = 0,
    size: int = 5,
    families: list[str] | None = None,
    projects: list[str] | None = None,
    tfidf_terms: list[str] | None = None,
    entry_ids: list[str] | None = None,
    rep_titles: list[str] | None = None,
    rep_bodies: list[str] | None = None,
) -> ClusterSummary:
    return ClusterSummary(
        cluster_id=cluster_id,
        entry_ids=entry_ids or [f"e-{i:05d}" for i in range(size)],
        size=size,
        families=families or ["learnings"],
        projects=projects or ["proj-a"],
        rep_titles=rep_titles or [f"Title {i}" for i in range(min(size, 8))],
        rep_bodies=rep_bodies or [f"Body {i}" for i in range(min(size, 8))],
        tfidf_terms=tfidf_terms or ["cache", "ttl", "embedding"],
    )


# ============================================================================
# 1. load_orphans
# ============================================================================


class TestLoadOrphans:
    def test_orphan_count_and_connected(self, tmp_path):
        """3 entries, 1 about-edge → 2 orphans, n_total=3, n_connected=1."""
        entries = [
            _minimal_entry("e-1"),
            _minimal_entry("e-2"),
            _minimal_entry("e-3"),
        ]
        edges = [{"from": "e-1", "to": "caching", "type": "about", "confidence": 0.9}]
        graph_dir = _write_graph(tmp_path, entries, edges)

        orphans, n_total, n_connected = load_orphans(str(graph_dir))

        assert n_total == 3
        assert n_connected == 1
        orphan_ids = {o.id for o in orphans}
        assert "e-1" not in orphan_ids
        assert {"e-2", "e-3"} == orphan_ids

    def test_no_edges_all_orphaned(self, tmp_path):
        entries = [_minimal_entry("e-1"), _minimal_entry("e-2")]
        graph_dir = _write_graph(tmp_path, entries, [])

        orphans, n_total, n_connected = load_orphans(str(graph_dir))

        assert n_total == 2
        assert n_connected == 0
        assert len(orphans) == 2

    def test_all_connected(self, tmp_path):
        entries = [_minimal_entry("e-1"), _minimal_entry("e-2")]
        edges = [
            {"from": "e-1", "to": "concept-a", "type": "about"},
            {"from": "e-2", "to": "concept-b", "type": "about"},
        ]
        graph_dir = _write_graph(tmp_path, entries, edges)

        orphans, n_total, n_connected = load_orphans(str(graph_dir))

        assert n_total == 2
        assert n_connected == 2
        assert orphans == []

    def test_non_about_edges_dont_connect(self, tmp_path):
        """A 'related' edge must NOT count as a connection."""
        entries = [_minimal_entry("e-1")]
        edges = [{"from": "e-1", "to": "concept-x", "type": "related"}]
        graph_dir = _write_graph(tmp_path, entries, edges)

        orphans, n_total, n_connected = load_orphans(str(graph_dir))

        assert n_total == 1
        assert n_connected == 0
        assert len(orphans) == 1

    def test_empty_graph(self, tmp_path):
        graph_dir = _write_graph(tmp_path, [], [])
        orphans, n_total, n_connected = load_orphans(str(graph_dir))
        assert n_total == 0
        assert n_connected == 0
        assert orphans == []


# ============================================================================
# 2. cluster_embeddings
# ============================================================================


class TestClusterEmbeddings:
    """
    Build 8 unit vectors in 2 obvious groups (4 near +x, 4 near -x).
    With cosine distance, vectors in the same quadrant are close (distance ~0)
    and vectors across groups are far (distance ~1).  A threshold of 0.5 should
    cleanly separate them.
    """

    def _two_group_emb(self) -> np.ndarray:
        np.random.seed(42)
        # Group A: near (1, 0, 0, …)
        base_a = np.zeros(8)
        base_a[0] = 1.0
        # Group B: near (0, 1, 0, …)
        base_b = np.zeros(8)
        base_b[1] = 1.0
        rows = []
        for _ in range(4):
            v = base_a + np.random.randn(8) * 0.05
            v /= np.linalg.norm(v)
            rows.append(v)
        for _ in range(4):
            v = base_b + np.random.randn(8) * 0.05
            v /= np.linalg.norm(v)
            rows.append(v)
        return np.stack(rows)

    def test_two_clusters_detected(self):
        emb = self._two_group_emb()
        clusters = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=2)
        assert len(clusters) == 2

    def test_cluster_sizes_sum_to_n(self):
        emb = self._two_group_emb()
        clusters = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=2)
        total = sum(len(v) for v in clusters.values())
        assert total == 8

    def test_largest_cluster_is_id_0(self):
        """Cluster keys are re-assigned size-desc so cluster 0 is the biggest."""
        emb = self._two_group_emb()
        clusters = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=2)
        sizes = [len(clusters[k]) for k in sorted(clusters)]
        assert sizes == sorted(sizes, reverse=True)

    def test_deterministic(self):
        emb = self._two_group_emb()
        c1 = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=2)
        c2 = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=2)
        assert c1 == c2

    def test_min_cluster_size_filters(self):
        """With min_cluster_size=5 and only 4-member groups, result should be empty."""
        emb = self._two_group_emb()
        clusters = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=5)
        assert clusters == {}

    def test_single_vector_returns_empty(self):
        emb = np.array([[1.0, 0.0, 0.0]])
        clusters = cluster_embeddings(emb, distance_threshold=0.5, min_cluster_size=1)
        assert clusters == {}


# ============================================================================
# 3. cluster_tfidf_terms
# ============================================================================


class TestClusterTfidfTerms:
    def test_distinctive_terms_surface(self):
        """Cluster 0 docs are about 'neural network' ; cluster 1 about 'genome sequencing'."""
        texts = [
            "neural network deep learning training loss backprop neural network",
            "neural network layer weights gradient descent neural network",
            "neural network hidden units activation function neural network",
            "genome sequencing DNA variant calling SNP genome sequencing",
            "genome sequencing reference assembly alignment genome sequencing",
            "genome sequencing coverage depth reads genome sequencing",
        ]
        clusters = {0: [0, 1, 2], 1: [3, 4, 5]}
        terms = cluster_tfidf_terms(texts, clusters, top_n=5)

        assert 0 in terms and 1 in terms
        # top terms for cluster 0 must mention 'neural' or 'network'
        c0 = " ".join(terms[0]).lower()
        assert any(kw in c0 for kw in ("neural", "network"))
        # top terms for cluster 1 must mention 'genome' or 'sequencing'
        c1 = " ".join(terms[1]).lower()
        assert any(kw in c1 for kw in ("genome", "sequencing"))

    def test_empty_clusters_returns_empty(self):
        assert cluster_tfidf_terms(["doc1", "doc2"], {}) == {}

    def test_empty_texts_returns_empty(self):
        assert cluster_tfidf_terms([], {0: [0, 1]}) == {}

    def test_respects_top_n(self):
        texts = ["alpha beta gamma delta epsilon zeta eta theta"] * 10
        clusters = {0: list(range(10))}
        terms = cluster_tfidf_terms(texts, clusters, top_n=3)
        assert len(terms[0]) <= 3


# ============================================================================
# 4. summarize_clusters
# ============================================================================


class TestSummarizeClusters:
    def _make_orphans(self):
        from propose_concepts import OrphanEntry

        return [
            OrphanEntry(
                id="e-1", project_id="p1", family="learnings", title="T1", body="B1"
            ),
            OrphanEntry(
                id="e-2", project_id="p1", family="learnings", title="T2", body="B2"
            ),
            OrphanEntry(
                id="e-3", project_id="p2", family="decisions", title="T3", body="B3"
            ),
            OrphanEntry(
                id="e-4", project_id="p2", family="decisions", title="T4", body="B4"
            ),
            OrphanEntry(
                id="e-5", project_id="p1", family="learnings", title="T5", body="B5"
            ),
            OrphanEntry(
                id="e-6", project_id="p3", family="findings", title="T6", body="B6"
            ),
        ]

    def _make_emb(self, n: int) -> np.ndarray:
        """Normalised random embeddings."""
        np.random.seed(0)
        emb = np.random.randn(n, 8).astype(float)
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        return emb / norms

    def test_returns_one_summary_per_cluster(self):
        orphans = self._make_orphans()
        emb = self._make_emb(len(orphans))
        clusters = {0: [0, 1, 2], 1: [3, 4, 5]}
        terms = {0: ["alpha", "beta"], 1: ["gamma", "delta"]}

        summaries = summarize_clusters(orphans, emb, clusters, terms)
        assert len(summaries) == 2

    def test_families_are_sorted_distinct(self):
        orphans = self._make_orphans()
        emb = self._make_emb(len(orphans))
        clusters = {0: [0, 2, 5]}  # p1/learnings, p2/decisions, p3/findings
        terms = {0: ["x"]}

        summaries = summarize_clusters(orphans, emb, clusters, terms)
        s = summaries[0]
        assert s.families == sorted(set(s.families))
        assert len(s.families) == 3

    def test_rep_titles_at_most_8(self):
        from propose_concepts import OrphanEntry

        orphans = [
            OrphanEntry(
                id=f"e-{i}",
                project_id="p1",
                family="learnings",
                title=f"Title {i}",
                body=f"Body {i}",
            )
            for i in range(12)
        ]
        emb = self._make_emb(12)
        clusters = {0: list(range(12))}
        terms = {0: ["t"]}
        summaries = summarize_clusters(orphans, emb, clusters, terms)
        assert len(summaries[0].rep_titles) <= 8

    def test_sorted_by_nfamilies_desc(self):
        """Multi-family clusters should sort before single-family clusters."""
        orphans = self._make_orphans()
        emb = self._make_emb(len(orphans))
        # cluster 0: single-family; cluster 1: two families
        clusters = {0: [0, 1, 4], 1: [2, 3, 5]}
        terms = {0: ["a"], 1: ["b"]}

        summaries = summarize_clusters(orphans, emb, clusters, terms)
        assert len(summaries[0].families) >= len(summaries[-1].families)


# ============================================================================
# 5. project_coverage
# ============================================================================


class TestProjectCoverage:
    def test_absorbed_all_math(self):
        s1 = _make_summary(cluster_id=0, size=10, families=["learnings", "decisions"])
        s2 = _make_summary(cluster_id=1, size=5, families=["learnings"])
        # n_total=100, n_connected=20, absorb 15 more → 35/100 = 35%
        cov = project_coverage(n_total=100, n_connected=20, summaries=[s1, s2])

        assert cov["n_total"] == 100
        assert cov["absorbed_all"] == 15
        assert cov["projected_all_pct"] == pytest.approx(35.0)

    def test_projected_xfam_excludes_single_family(self):
        s1 = _make_summary(cluster_id=0, size=10, families=["learnings", "decisions"])
        s2 = _make_summary(cluster_id=1, size=5, families=["learnings"])
        cov = project_coverage(n_total=100, n_connected=20, summaries=[s1, s2])

        # Only s1 is cross-family (2 families)
        assert cov["n_xfam_clusters"] == 1
        assert cov["absorbed_xfam"] == 10
        assert cov["projected_xfam_pct"] == pytest.approx(30.0)

    def test_no_summaries(self):
        cov = project_coverage(n_total=50, n_connected=10, summaries=[])
        assert cov["projected_all_pct"] == pytest.approx(20.0)
        assert cov["n_clusters"] == 0
        assert cov["n_xfam_clusters"] == 0

    def test_zero_total_no_crash(self):
        cov = project_coverage(n_total=0, n_connected=0, summaries=[])
        assert cov["projected_all_pct"] == 0.0


# ============================================================================
# 6. write_proposals
# ============================================================================


class TestWriteProposals:
    def _make_proposal(self, slug: str = "prompt-caching") -> ProposedConcept:
        return ProposedConcept(
            slug=slug,
            label="Prompt Caching",
            definition="Storing reusable prompt prefixes to reduce token costs.",
            keywords=["cache", "ttl", "prompt"],
            aliases=["prefix-caching"],
            cluster_id=0,
            size=7,
            families=["learnings", "decisions"],
            projects=["proj-a"],
            example_entry_ids=["e-00001", "e-00002"],
            source="offline-tfidf",
        )

    def test_yaml_written_with_header(self, tmp_path):
        proposals = [self._make_proposal("prompt-caching")]
        out = str(tmp_path / "proposals" / "concepts-candidate.yaml")
        write_proposals(proposals, out)

        content = Path(out).read_text(encoding="utf-8")
        assert content.startswith("# REVIEW QUEUE")

    def test_slug_key_present(self, tmp_path):
        proposals = [self._make_proposal("prompt-caching")]
        out = str(tmp_path / "proposals" / "concepts-candidate.yaml")
        write_proposals(proposals, out)

        content = Path(out).read_text(encoding="utf-8")
        # Strip header comments before yaml.safe_load
        body = "\n".join(
            line for line in content.splitlines() if not line.startswith("#")
        )
        loaded = yaml.safe_load(body)
        assert "prompt-caching" in loaded

    def test_required_fields_present(self, tmp_path):
        proposals = [self._make_proposal("prompt-caching")]
        out = str(tmp_path / "proposals" / "concepts-candidate.yaml")
        write_proposals(proposals, out)

        body = "\n".join(
            line
            for line in Path(out).read_text().splitlines()
            if not line.startswith("#")
        )
        loaded = yaml.safe_load(body)
        block = loaded["prompt-caching"]
        # to_yaml_block emits registry-compatible keys: positive_keywords
        # (NOT keywords) + match_mode, so approved blocks paste straight in.
        for key in (
            "label",
            "positive_keywords",
            "match_mode",
            "definition",
            "_candidate_meta",
        ):
            assert key in block, f"missing key: {key}"
        assert block["match_mode"] == "hybrid"
        meta = block["_candidate_meta"]
        assert "source" in meta

    def test_two_proposals_both_present(self, tmp_path):
        proposals = [
            self._make_proposal("prompt-caching"),
            self._make_proposal("llm-cost"),
        ]
        out = str(tmp_path / "proposals" / "concepts-candidate.yaml")
        write_proposals(proposals, out)

        body = "\n".join(
            line
            for line in Path(out).read_text().splitlines()
            if not line.startswith("#")
        )
        loaded = yaml.safe_load(body)
        assert "prompt-caching" in loaded
        assert "llm-cost" in loaded


# ============================================================================
# 7. concept_labeler.label_cluster
# ============================================================================


class TestLabelCluster:
    def test_offline_source(self):
        """label_cluster with use_llm=False must return offline-tfidf."""
        summary = _make_summary(tfidf_terms=["cache", "ttl"])
        result = label_cluster(summary, use_llm=False)
        assert result.source == "offline-tfidf"
        assert result.slug  # non-empty
        assert result.label

    def test_offline_slug_from_terms(self):
        summary = _make_summary(tfidf_terms=["neural", "network"])
        result = label_cluster(summary, use_llm=False)
        # slug should incorporate the top terms
        assert "neural" in result.slug or "network" in result.slug

    def test_llm_success_path(self):
        """Inject a fake `run` returning valid JSON; expect source=='llm'."""
        summary = _make_summary(
            tfidf_terms=["cache", "ttl"],
            rep_titles=["Fast cache eviction"],
            rep_bodies=["Reducing latency via TTL-based prompt caching."],
        )

        inner = {
            "slug": "prompt-caching",
            "label": "Prompt Caching",
            "definition": "Caching prompt prefixes to reduce latency.",
            "keywords": ["cache", "ttl"],
            "aliases": [],
        }
        outer = {"result": json.dumps(inner)}

        FakeResult = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(outer),
            stderr="",
        )

        def fake_run(*args, **kwargs):
            return FakeResult

        result = label_cluster(
            summary,
            use_llm=True,
            claude_bin="/usr/bin/true",  # any non-None string passes shutil.which guard
            run=fake_run,
        )
        assert result.source == "llm"
        assert result.slug == "prompt-caching"
        assert result.label == "Prompt Caching"

    def test_llm_failure_falls_back_to_offline(self):
        """If fake_run raises, label_cluster must return offline-tfidf."""
        summary = _make_summary(tfidf_terms=["embedding", "vector"])

        def bad_run(*args, **kwargs):
            raise RuntimeError("no CLI")

        result = label_cluster(
            summary,
            use_llm=True,
            claude_bin="/usr/bin/true",
            run=bad_run,
        )
        assert result.source == "offline-tfidf"

    def test_llm_bad_json_falls_back_to_offline(self):
        """Bad JSON from the LLM must fall back to offline."""
        summary = _make_summary(tfidf_terms=["foo", "bar"])

        FakeBadResult = types.SimpleNamespace(
            returncode=0,
            stdout="not json at all",
            stderr="",
        )

        def bad_json_run(*args, **kwargs):
            return FakeBadResult

        result = label_cluster(
            summary,
            use_llm=True,
            claude_bin="/usr/bin/true",
            run=bad_json_run,
        )
        assert result.source == "offline-tfidf"

    def test_llm_nonzero_returncode_falls_back(self):
        summary = _make_summary()

        FakeError = types.SimpleNamespace(returncode=1, stdout="", stderr="err")

        result = label_cluster(
            summary,
            use_llm=True,
            claude_bin="/usr/bin/true",
            run=lambda *a, **k: FakeError,
        )
        assert result.source == "offline-tfidf"
