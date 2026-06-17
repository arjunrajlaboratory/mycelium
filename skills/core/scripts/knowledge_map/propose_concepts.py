"""Propose new concepts from orphaned knowledge-graph entries via embedding + clustering."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import AgglomerativeClustering
from sklearn.feature_extraction.text import TfidfVectorizer
import yaml

from propose_model import ClusterSummary, ProposedConcept
from concept_labeler import label_cluster


@dataclass
class OrphanEntry:
    id: str
    project_id: str
    family: str
    title: str
    body: str


@dataclass
class ProposeResult:
    summaries: list
    proposals: list
    coverage: dict
    out_path: str
    n_orphans: int
    n_total: int
    n_connected: int


def load_orphans(graph_dir: str) -> tuple[list[OrphanEntry], int, int]:
    """Load knowledge-graph.json. Return (orphans, n_total_entries, n_connected_entries).

    The graph is a dict with top-level keys: entries, edges, concepts, …
    Entry nodes have fields: id, project_id, family, title, body_excerpt.
    About edges use 'from' (entry id) and 'to' (concept slug), type == 'about'.

    orphan = entry node with no outgoing 'about' edge.
    n_connected = n_total - n_orphans.
    """
    kg_path = os.path.join(graph_dir, "knowledge-graph.json")
    with open(kg_path, encoding="utf-8") as f:
        graph = json.load(f)

    entries: list[dict] = graph.get("entries", []) or []
    edges: list[dict] = graph.get("edges", []) or []

    # Collect all entry ids that have at least one outgoing 'about' edge
    connected_ids: set[str] = {
        e["from"] for e in edges if e.get("type") == "about" and "from" in e
    }

    n_total = len(entries)

    orphans: list[OrphanEntry] = []
    for e in entries:
        entry_id = e.get("id", "")
        # Belt-and-suspenders: skip any log node that leaked into entries (R1/R2)
        if e.get("kind") == "log":
            continue
        if entry_id not in connected_ids:
            orphans.append(
                OrphanEntry(
                    id=entry_id,
                    project_id=e.get("project_id", ""),
                    family=e.get("family", ""),
                    title=e.get("title", "") or e.get("anchor", ""),
                    body=e.get("body_excerpt", "") or "",
                )
            )

    n_connected = n_total - len(orphans)
    return orphans, n_total, n_connected


def embed_texts(texts: list[str], model_name: str = "all-MiniLM-L6-v2") -> np.ndarray:
    """Lazy-import SentenceTransformer; encode texts with normalize_embeddings=True."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    return model.encode(
        texts, normalize_embeddings=True, batch_size=64, show_progress_bar=False
    )


def cluster_embeddings(
    emb: np.ndarray, distance_threshold: float, min_cluster_size: int
) -> dict[int, list[int]]:
    """AgglomerativeClustering with cosine metric/average linkage. Return cluster_id -> sorted row indices."""
    if len(emb) < 2:
        return {}
    clust = AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=distance_threshold,
        metric="cosine",
        linkage="average",
    )
    labels = clust.fit_predict(emb)
    raw: dict[int, list[int]] = {}
    for idx, lbl in enumerate(labels):
        raw.setdefault(int(lbl), []).append(idx)
    # drop small clusters
    filtered = {k: sorted(v) for k, v in raw.items() if len(v) >= min_cluster_size}
    # re-key contiguous ints sorted by size desc
    sorted_keys = sorted(filtered, key=lambda k: -len(filtered[k]))
    return {new_id: filtered[old_id] for new_id, old_id in enumerate(sorted_keys)}


def cluster_tfidf_terms(
    orphan_texts: list[str], clusters: dict[int, list[int]], top_n: int = 10
) -> dict[int, list[str]]:
    """Fit TfidfVectorizer over all orphan_texts, return top_n terms per cluster."""
    if not orphan_texts or not clusters:
        return {}
    vec = TfidfVectorizer(
        stop_words="english", ngram_range=(1, 2), min_df=2, max_features=5000
    )
    mat = vec.fit_transform(orphan_texts)
    feature_names = vec.get_feature_names_out()
    result = {}
    for cid, indices in clusters.items():
        member_mat = mat[indices]
        mean_scores = np.asarray(member_mat.mean(axis=0)).flatten()
        top_indices = np.argsort(mean_scores)[::-1][:top_n]
        # sort ties alphabetically
        top_terms = sorted(
            [(feature_names[i], mean_scores[i]) for i in top_indices],
            key=lambda x: (-x[1], x[0]),
        )
        result[cid] = [t for t, _ in top_terms]
    return result


def summarize_clusters(
    orphans: list[OrphanEntry],
    emb: np.ndarray,
    clusters: dict[int, list[int]],
    terms: dict[int, list[str]],
) -> list[ClusterSummary]:
    """Build ClusterSummary for each cluster; sort by (n_distinct_families desc, size desc, cluster_id asc)."""
    summaries = []
    for cid, indices in clusters.items():
        members = [orphans[i] for i in indices]
        families = sorted(set(o.family for o in members))
        projects = sorted(set(o.project_id for o in members))
        entry_ids = [orphans[i].id for i in indices]
        # centroid
        member_embs = emb[indices]
        centroid = member_embs.mean(axis=0)
        # cosine similarity to centroid (embeddings already normalized)
        sims = member_embs @ centroid / (np.linalg.norm(centroid) + 1e-10)
        # sort members by similarity desc, take top 8
        ranked = sorted(zip(indices, sims), key=lambda x: -x[1])[:8]
        rep_titles = [orphans[i].title for i, _ in ranked]
        rep_bodies = [orphans[i].body[:400] for i, _ in ranked]
        summaries.append(
            ClusterSummary(
                cluster_id=cid,
                entry_ids=entry_ids,
                size=len(indices),
                families=families,
                projects=projects,
                tfidf_terms=terms.get(cid, []),
                rep_titles=rep_titles,
                rep_bodies=rep_bodies,
            )
        )
    summaries.sort(key=lambda s: (-len(s.families), -s.size, s.cluster_id))
    return summaries


def project_coverage(
    n_total: int, n_connected: int, summaries: list[ClusterSummary]
) -> dict:
    """Compute coverage statistics."""
    absorbed_all = sum(s.size for s in summaries)
    absorbed_xfam = sum(s.size for s in summaries if len(s.families) >= 2)
    n_xfam = sum(1 for s in summaries if len(s.families) >= 2)

    def pct(num, den):
        return round(100.0 * num / den, 1) if den > 0 else 0.0

    return {
        "current_connected": n_connected,
        "current_pct": pct(n_connected, n_total),
        "n_total": n_total,
        "absorbed_all": absorbed_all,
        "projected_all_pct": pct(n_connected + absorbed_all, n_total),
        "n_clusters": len(summaries),
        "n_xfam_clusters": n_xfam,
        "absorbed_xfam": absorbed_xfam,
        "projected_xfam_pct": pct(n_connected + absorbed_xfam, n_total),
    }


def write_proposals(proposals: list[ProposedConcept], out_path: str) -> None:
    """Write proposals to a YAML review queue file."""
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    header = (
        "# REVIEW QUEUE — auto-generated by `map propose`\n"
        "# Instructions: paste approved blocks into concepts.yaml,\n"
        "#   drop the `_candidate_meta` key from each block,\n"
        "#   then re-run `map build`.\n\n"
    )
    blocks = [p.to_yaml_block() for p in proposals]
    combined = {}
    for block in blocks:
        combined.update(block)
    content = header + yaml.safe_dump(
        combined, sort_keys=False, allow_unicode=True, default_flow_style=False
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)


def propose(
    graph_dir: str,
    min_cluster_size: int = 5,
    distance_threshold: float = 0.55,
    use_llm: bool = False,
    embed_model: str = "all-MiniLM-L6-v2",
    max_llm_labels: int = 30,
    run=None,
) -> ProposeResult:
    """Orchestrate: load orphans → embed → cluster → label → write proposals."""
    orphans, n_total, n_connected = load_orphans(graph_dir)
    n_orphans = len(orphans)
    out_path = os.path.join(graph_dir, "proposals", "concepts-candidate.yaml")

    if n_orphans == 0:
        return ProposeResult([], [], {}, out_path, 0, n_total, n_connected)

    texts = [f"{o.title}\n{o.body[:2000]}" for o in orphans]
    emb = embed_texts(texts, model_name=embed_model)
    clusters = cluster_embeddings(emb, distance_threshold, min_cluster_size)
    terms = cluster_tfidf_terms(texts, clusters)
    summaries = summarize_clusters(orphans, emb, clusters, terms)

    proposals = []
    for i, summary in enumerate(summaries):
        use_llm_this = use_llm and i < max_llm_labels
        kwargs = {}
        if run is not None:
            kwargs["run"] = run
        proposal = label_cluster(summary, use_llm=use_llm_this, **kwargs)
        proposals.append(proposal)

    coverage = project_coverage(n_total, n_connected, summaries)
    write_proposals(proposals, out_path)

    return ProposeResult(
        summaries=summaries,
        proposals=proposals,
        coverage=coverage,
        out_path=out_path,
        n_orphans=n_orphans,
        n_total=n_total,
        n_connected=n_connected,
    )
