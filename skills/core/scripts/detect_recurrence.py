#!/usr/bin/env python3
"""Scan .living/learnings.md for near-duplicate entries and output a recurrence report.

This module uses ``from __future__ import annotations`` (PEP 563) so PEP 604
union syntax (``str | None``) in type hints works on Python 3.9+ at parse time.

Usage:
    detect_recurrence.py --living-dir PATH [--threshold 0.5] [--report-out FILE]
                         [--include-git-signals]

Computes near-duplicate clusters using:
  - Tag overlap (Jaccard similarity on tag sets)
  - Body keyword overlap (TF-IDF cosine similarity — stdlib only, no sklearn)

Outputs flagged clusters with suggested promotion actions.

Exit codes:
    0   report generated (zero or more clusters found)
    2   bad arguments
    3   .living/learnings.md not found
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Import shared parsing helpers from sibling generate_index module
# ---------------------------------------------------------------------------
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import generate_index as gi  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MITIGATION_LINE_RE = re.compile(
    r"^[\s>]*\*?\*?mitigation_type\*?\*?\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "that",
        "this",
        "it",
        "its",
        "they",
        "their",
        "there",
        "we",
        "our",
        "you",
        "not",
        "no",
        "if",
        "so",
        "when",
        "then",
        "also",
        "each",
        "all",
        "any",
        "into",
        "which",
        "what",
        "how",
        "why",
        "where",
        "who",
    ]
)


# ---------------------------------------------------------------------------
# Entry parsing (extends generate_index.collect_entries with mitigation_type)
# ---------------------------------------------------------------------------


def _slice_entry_text(path: Path, header_prefix: str, header_line_no: int) -> str:
    """Return the entry block starting at header_line_no up to next header or EOF."""
    lines: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for n, raw in enumerate(fh, start=1):
            if n < header_line_no:
                continue
            if n > header_line_no and raw.startswith(header_prefix):
                break
            lines.append(raw.rstrip("\n"))
    while lines and lines[-1].strip() == "":
        lines.pop()
    return "\n".join(lines)


def _extract_mitigation_type(text: str) -> str:
    """Parse the mitigation_type value from an entry body. Returns 'unknown' if absent."""
    # Strip HTML comments first so commented-out guidance isn't matched
    clean = _COMMENT_RE.sub("", text)
    for line in clean.splitlines():
        m = _MITIGATION_LINE_RE.match(line)
        if m:
            val = m.group(1).strip().strip("*`\"'").lower()
            if val in {"structural", "convention", "ambient-awareness"}:
                return val
            # Accept common typo variants
            if val.startswith("ambient"):
                return "ambient-awareness"
            if val in {"struct", "structual"}:
                return "structural"
            if val in {"conv", "checklist"}:
                return "convention"
            return val  # preserve unknown values verbatim
    return "unknown"


def collect_learning_records(path: Path) -> list[dict]:
    """Parse learnings.md into enriched records including mitigation_type and body text.

    Extends generate_index.collect_entries by also capturing:
    - ``mitigation_type``: value from **mitigation_type**: field, or "unknown"
    - ``body``: full raw entry text (for TF-IDF)
    """
    base = gi.collect_entries(path, "learnings", "L")
    # Now fetch body text + mitigation_type for each entry
    header_prefix = "### "
    records = []
    for e in base:
        body = _slice_entry_text(path, header_prefix, e["line_no"])
        mitigation_type = _extract_mitigation_type(body)
        records.append(
            {
                **e,
                "mitigation_type": mitigation_type,
                "body": body,
            }
        )
    return records


# ---------------------------------------------------------------------------
# TF-IDF cosine similarity (stdlib only)
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip markdown formatting, split into word tokens."""
    # Strip HTML comments and markdown bold/italic markers
    text = _COMMENT_RE.sub(" ", text)
    text = re.sub(r"\*+|`+|_{1,2}|#+", " ", text)
    # Keep only word characters
    tokens = re.findall(r"[a-z][a-z0-9\-]{1,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 2]


def _build_tfidf(records: list[dict]) -> list[dict]:
    """Add a ``tfidf`` field (Counter of term→tf_idf_weight) to each record."""
    n_docs = len(records)
    if n_docs == 0:
        return records

    # Term frequency per document
    tf_per_doc: list[Counter] = []
    for r in records:
        tokens = _tokenize(r["body"])
        tf_per_doc.append(Counter(tokens))

    # Document frequency
    df: Counter = Counter()
    for tf in tf_per_doc:
        for term in tf:
            df[term] += 1

    # IDF (smoothed: log((1+N)/(1+df)) + 1)
    idf: dict[str, float] = {}
    for term, freq in df.items():
        idf[term] = math.log((1 + n_docs) / (1 + freq)) + 1.0

    # TF-IDF vectors (raw counts × idf; not L2-normalised yet — normalise at similarity time)
    for i, r in enumerate(records):
        tf = tf_per_doc[i]
        vec: dict[str, float] = {term: count * idf[term] for term, count in tf.items()}
        r["tfidf"] = vec

    return records


def _cosine(vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
    """Cosine similarity between two sparse TF-IDF vectors."""
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a) & set(vec_b)
    if not common:
        return 0.0
    dot = sum(vec_a[t] * vec_b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# Jaccard tag similarity
# ---------------------------------------------------------------------------


def _jaccard(tags_a: list[str], tags_b: list[str]) -> float:
    """Jaccard similarity between two tag sets. Returns 0.0 if both empty."""
    set_a = {t.lower() for t in tags_a}
    set_b = {t.lower() for t in tags_b}
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


# ---------------------------------------------------------------------------
# Combined similarity
# ---------------------------------------------------------------------------


def _combined_sim(
    a: dict, b: dict, tag_weight: float = 0.5, term_weight: float = 0.5
) -> tuple[float, float, float]:
    """Return (combined, tag_sim, term_sim)."""
    tag_sim = _jaccard(a["tags"], b["tags"])
    term_sim = _cosine(a.get("tfidf", {}), b.get("tfidf", {}))
    combined = tag_weight * tag_sim + term_weight * term_sim
    return combined, tag_sim, term_sim


# ---------------------------------------------------------------------------
# Cluster construction (union-find over threshold pairs)
# ---------------------------------------------------------------------------


def _union_find_cluster(
    records: list[dict], threshold: float
) -> list[list[tuple[dict, float, float, float]]]:
    """Group records into near-duplicate clusters using union-find.

    Returns a list of clusters; each cluster is a list of
    (record, combined_sim, tag_sim, term_sim) tuples relative to the
    cluster representative (entry with lowest index).

    Only clusters with 2+ members are returned.
    """
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    # Similarity matrix (sparse — only pairs exceeding threshold)
    sim_store: dict[tuple[int, int], tuple[float, float, float]] = {}
    for i in range(n):
        for j in range(i + 1, n):
            combined, tag_sim, term_sim = _combined_sim(records[i], records[j])
            if combined >= threshold:
                union(i, j)
                sim_store[(i, j)] = (combined, tag_sim, term_sim)

    # Group by root
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        # Build cluster with similarity to representative (first member)
        rep = members[0]
        cluster = []
        for idx in members:
            if idx == rep:
                cluster.append((records[idx], 1.0, 1.0, 1.0))
            else:
                key = (min(rep, idx), max(rep, idx))
                sims = sim_store.get(key)
                if sims is None:
                    # Compute on-demand if union merged via intermediary
                    sims = _combined_sim(records[rep], records[idx])
                cluster.append((records[idx], sims[0], sims[1], sims[2]))
        clusters.append(cluster)

    return clusters


# ---------------------------------------------------------------------------
# Git signal extraction
# ---------------------------------------------------------------------------


def _git_signals(repo_dir: Path, since: str | None, until: str | None) -> list[str]:
    """Return commit messages near a date range that suggest regressions."""
    pattern = r"fix:|revert:|again|broke|regress"
    try:
        cmd = ["git", "-C", str(repo_dir), "log", "--oneline"]
        if since:
            cmd += [f"--after={since}"]
        if until:
            cmd += [f"--before={until}"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return []
        matches = []
        for line in result.stdout.splitlines():
            if re.search(pattern, line, re.IGNORECASE):
                matches.append(line.strip())
        return matches
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


# ---------------------------------------------------------------------------
# Cluster theme derivation
# ---------------------------------------------------------------------------


def _cluster_theme(cluster: list[tuple[dict, float, float, float]]) -> str:
    """Derive a short theme label from shared tags or top TF-IDF terms."""
    all_tags: list[str] = []
    for rec, *_ in cluster:
        all_tags.extend(t.lower() for t in rec["tags"])
    if all_tags:
        tag_counts = Counter(all_tags)
        # Use the most common tag(s) as the theme
        most_common = tag_counts.most_common(3)
        return " / ".join(tag for tag, _ in most_common)

    # Fall back to top TF-IDF terms across the cluster
    all_terms: Counter = Counter()
    for rec, *_ in cluster:
        all_terms.update(rec.get("tfidf", {}))
    if all_terms:
        return " / ".join(t for t, _ in all_terms.most_common(3))

    return "unclassified"


# ---------------------------------------------------------------------------
# Suggested action
# ---------------------------------------------------------------------------


def _suggest_action(cluster: list[tuple[dict, float, float, float]]) -> str:
    """Return a human-readable suggested action for a cluster."""
    records = [rec for rec, *_ in cluster]
    mitigations = [r["mitigation_type"] for r in records]
    m_set = set(mitigations)
    n = len(records)

    if m_set == {"structural"}:
        return (
            f"All {n} entries are already structural mitigations. "
            "Review whether they cover the same invariant and consolidate if so."
        )
    if m_set == {"convention"}:
        return (
            f"All {n} entries are convention-level mitigations. "
            "Consolidate into a single conventions.md entry, then assess whether "
            "a structural mitigation (test / type constraint) is now warranted."
        )
    if "ambient-awareness" in m_set and "structural" not in m_set:
        # All ambient or mix of ambient+convention
        ambient_count = mitigations.count("ambient-awareness")
        return (
            f"{ambient_count} of {n} entries are ambient-awareness on overlapping topics. "
            "Promote to a single conventions.md entry OR convert to a structural "
            "mitigation (e.g., a test that validates the invariant these entries describe)."
        )
    if "structural" in m_set and "ambient-awareness" in m_set:
        return (
            "Mixed cluster: some entries are structural, some are ambient-awareness. "
            "Check whether the structural mitigation fully covers the ambient-awareness cases. "
            "If so, mark the ambient-awareness entries as superseded and remove them."
        )
    # Generic fallback
    return (
        f"Cluster of {n} related entries. Review for consolidation into a single "
        "convention or structural mitigation."
    )


# ---------------------------------------------------------------------------
# Report formatting
# ---------------------------------------------------------------------------


def _format_report(
    records: list[dict],
    clusters: list[list[tuple[dict, float, float, float]]],
    standalone: list[dict],
    living_dir: Path,
    include_git: bool,
    repo_dir: Path | None,
) -> str:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n_entries = len(records)
    n_flagged = sum(len(c) for c in clusters)
    n_clusters = len(clusters)

    lines: list[str] = [
        "# Recurrence Detection Report",
        f"Generated: {now_iso}",
        f"Living dir: {living_dir}",
        "",
        "## Summary",
        (
            f"{n_entries} entries parsed, {n_flagged} flagged "
            f"in {n_clusters} potential recurrence cluster{'s' if n_clusters != 1 else ''}."
        ),
        "",
    ]

    if clusters:
        lines.append("## Potential recurrence clusters")
        lines.append("")
        for cluster_idx, cluster in enumerate(clusters, start=1):
            theme = _cluster_theme(cluster)
            # Highest pairwise similarity (excluding rep↔rep)
            sims = [s for _, s, _, _ in cluster if s < 1.0]
            max_sim = max(sims) if sims else 0.0
            tag_sims = [ts for _, _, ts, _ in cluster if ts < 1.0]
            term_sims = [ks for _, _, _, ks in cluster if ks < 1.0]
            avg_tag = sum(tag_sims) / len(tag_sims) if tag_sims else 0.0
            avg_term = sum(term_sims) / len(term_sims) if term_sims else 0.0

            lines.append(f"### Cluster {cluster_idx}: {theme}")
            lines.append(
                f"**Similarity**: {max_sim:.2f} (tags={avg_tag:.2f}, terms={avg_term:.2f})"
            )
            lines.append("**Entries**:")
            for rec, sim, tag_sim, term_sim in cluster:
                entry_id = rec["id"]
                date = rec["date"] or "undated"
                title = rec["title"]
                mit = rec["mitigation_type"]
                lines.append(
                    f'- {entry_id} ({date}): "{title}" [mitigation_type: {mit}]'
                )

            # Git signals
            if include_git and repo_dir:
                dates = sorted(r["date"] for r, *_ in cluster if r.get("date"))
                since = dates[0] if dates else None
                until = dates[-1] if dates else None
                git_hits = _git_signals(repo_dir, since, until)
                if git_hits:
                    lines.append(
                        "**Git signals** (fix/revert/regress commits near these dates):"
                    )
                    for g in git_hits[:5]:
                        lines.append(f"  - `{g}`")

            lines.append(f"**Suggested action**: {_suggest_action(cluster)}")
            lines.append("")
    else:
        lines.append("## Potential recurrence clusters")
        lines.append("")
        lines.append("No near-duplicate clusters detected above threshold.")
        lines.append("")

    lines.append("## Stand-alone entries (no recurrence)")
    lines.append("")
    if standalone:
        lines.append(f"{len(standalone)} entries with no near-duplicates.")
        lines.append("")
        for rec in standalone:
            entry_id = rec["id"]
            date = rec["date"] or "undated"
            title = rec["title"]
            mit = rec["mitigation_type"]
            lines.append(f'- {entry_id} ({date}): "{title}" [mitigation_type: {mit}]')
    else:
        lines.append("All entries were grouped into clusters.")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Scan .living/learnings.md for near-duplicate entries and "
            "output a recurrence report with promotion suggestions."
        )
    )
    parser.add_argument(
        "--living-dir",
        required=True,
        type=Path,
        metavar="PATH",
        help="Path to the .living/ directory.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        metavar="FLOAT",
        help=(
            "Combined similarity threshold to flag a pair as near-duplicate "
            "(0.0–1.0, default: 0.5). Combined = 0.5×Jaccard(tags) + 0.5×cosine(terms)."
        ),
    )
    parser.add_argument(
        "--report-out",
        type=Path,
        default=None,
        metavar="FILE",
        help="Write the report to FILE instead of stdout.",
    )
    parser.add_argument(
        "--include-git-signals",
        action="store_true",
        default=False,
        help=(
            "Attempt to query git log in the repo containing --living-dir "
            "for fix/revert/regress commits near clustered entry dates."
        ),
    )

    args = parser.parse_args()

    living_dir: Path = args.living_dir.resolve()
    learnings_path = living_dir / "learnings.md"

    if not learnings_path.exists():
        print(
            f"error: {learnings_path} not found. "
            "Pass a valid --living-dir that contains learnings.md.",
            file=sys.stderr,
        )
        sys.exit(3)

    # Clamp threshold
    threshold = max(0.0, min(1.0, args.threshold))

    # Parse entries
    records = collect_learning_records(learnings_path)
    if not records:
        report = (
            "# Recurrence Detection Report\n"
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"Living dir: {living_dir}\n\n"
            "## Summary\n"
            "0 entries parsed. Nothing to analyse.\n"
        )
        if args.report_out:
            args.report_out.write_text(report, encoding="utf-8")
        else:
            print(report, end="")
        sys.exit(0)

    # Build TF-IDF vectors
    records = _build_tfidf(records)

    # Detect clusters
    clusters = _union_find_cluster(records, threshold)
    # Sort clusters largest-first
    clusters.sort(key=lambda c: -len(c))

    # Identify stand-alone entries
    clustered_ids = {r["id"] for cluster in clusters for r, *_ in cluster}
    standalone = [r for r in records if r["id"] not in clustered_ids]

    # Derive repo_dir for git signals (walk up from living_dir)
    repo_dir: Path | None = None
    if args.include_git_signals:
        candidate = living_dir.parent
        while candidate != candidate.parent:
            if (candidate / ".git").exists():
                repo_dir = candidate
                break
            candidate = candidate.parent

    report = _format_report(
        records, clusters, standalone, living_dir, args.include_git_signals, repo_dir
    )

    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(report, encoding="utf-8")
        print(f"Report written to {args.report_out}", file=sys.stderr)
    else:
        print(report, end="")

    sys.exit(0)


if __name__ == "__main__":
    main()
