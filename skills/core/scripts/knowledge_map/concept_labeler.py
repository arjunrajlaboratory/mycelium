"""
concept_labeler.py — Offline and LLM-backed labelers for concept proposal.

Public API:
  tfidf_label(summary)            -> ProposedConcept   (no network, no LLM)
  llm_label(summary, ...)         -> ProposedConcept | None
  label_cluster(summary, ...)     -> ProposedConcept   (LLM with offline fallback)

The ``run`` parameter on llm_label / label_cluster is dependency-injected so
tests can mock subprocess calls without touching the real filesystem or CLI.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from propose_model import SLUG_RE, ClusterSummary, ProposedConcept, slugify

# ---------------------------------------------------------------------------
# Offline labeler
# ---------------------------------------------------------------------------


def tfidf_label(summary: ClusterSummary) -> ProposedConcept:
    """
    Produce a ProposedConcept from TF-IDF terms alone — no network, no LLM.

    Slug and label are derived from the top 1-2 tfidf_terms.
    """
    top_terms = summary.tfidf_terms[:2] if summary.tfidf_terms else ["concept"]
    joined = "-".join(top_terms)
    slug = slugify(joined)
    label = " ".join(t.title() for t in top_terms)
    keywords = list(summary.tfidf_terms[:6])

    return ProposedConcept(
        slug=slug,
        label=label,
        definition=(
            f"Auto-proposed from {summary.size} related entries; "
            "refine before approving."
        ),
        keywords=keywords,
        aliases=[],
        cluster_id=summary.cluster_id,
        size=summary.size,
        families=list(summary.families),
        projects=list(summary.projects),
        example_entry_ids=list(summary.entry_ids[:8]),
        source="offline-tfidf",
    )


# ---------------------------------------------------------------------------
# LLM prompt builder
# ---------------------------------------------------------------------------


def _build_prompt(summary: ClusterSummary) -> str:
    """
    Build a prompt that asks the model for a concept label in STRICT JSON.

    The model must return ONLY a JSON object — no prose, no markdown fences.
    """
    titles_block = "\n".join(
        f"  {i + 1}. {t}" for i, t in enumerate(summary.rep_titles)
    )

    # Pair each title with its body snippet (truncated)
    bodies_block = ""
    for i, body in enumerate(summary.rep_bodies):
        snippet = body[:400].strip()
        bodies_block += f"\n  [{i + 1}] {snippet}"

    terms_block = ", ".join(summary.tfidf_terms) if summary.tfidf_terms else "(none)"

    return f"""\
You are labeling a cluster of {summary.size} related knowledge-base entries.

Representative titles:
{titles_block}

Body excerpts:
{bodies_block}

Top TF-IDF terms: {terms_block}

Return STRICT JSON only — no prose, no markdown fences, no explanation.
The JSON object must have exactly these keys:
  "slug"        — kebab-case identifier (^[a-z0-9][a-z0-9-]*$), max 40 chars
  "label"       — human-readable name, 1-8 words, Title Case
  "definition"  — one-sentence definition (≤25 words)
  "keywords"    — list of 3-6 whole-word phrases that would appear in related notes
  "aliases"     — list of 0-4 alternate names or abbreviations (may be empty list)

Example output:
{{"slug":"geo-data-access","label":"GEO Data Access","definition":"Retrieving expression matrices from GEO at the GSM or series level.","keywords":["GEO","GSM","GSE","series matrix"],"aliases":["GEO accession","GSM download"]}}
"""


# ---------------------------------------------------------------------------
# LLM labeler
# ---------------------------------------------------------------------------

# Fallback path for the cmux-bundled claude binary
_CMUX_CLAUDE = "/Applications/cmux.app/Contents/Resources/bin/claude"


def llm_label(
    summary: ClusterSummary,
    claude_bin: str | None = None,
    run: object = subprocess.run,
) -> ProposedConcept | None:
    """
    Ask a local claude CLI to label the cluster.

    Resolution order for the claude binary:
      1. ``claude_bin`` parameter (if provided)
      2. ``shutil.which("claude")``
      3. ``/Applications/cmux.app/Contents/Resources/bin/claude`` (if it exists)
      4. Return ``None`` (no binary found)

    Returns ``None`` on any failure so callers can fall back to tfidf_label.
    """
    # Resolve binary
    bin_path: str | None = claude_bin
    if not bin_path:
        bin_path = shutil.which("claude")
    if not bin_path and Path(_CMUX_CLAUDE).exists():
        bin_path = _CMUX_CLAUDE
    if not bin_path:
        return None

    prompt = _build_prompt(summary)

    try:
        result = run(
            [bin_path, "-p", prompt, "--output-format", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception:
        return None

    if result.returncode != 0:
        return None

    # Parse the claude --output-format json wrapper
    try:
        outer = json.loads(result.stdout)
        # The assistant reply is in the "result" field
        inner_text: str = outer.get("result", result.stdout)
    except (json.JSONDecodeError, AttributeError):
        inner_text = result.stdout

    # Extract the first {...} JSON object from the text
    try:
        match = re.search(r"\{.*\}", inner_text, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, TypeError):
        return None

    # Extract required fields; bail on any missing key
    try:
        raw_slug = str(parsed.get("slug", ""))
        label = str(parsed.get("label", "")).strip()
        definition = str(parsed.get("definition", "")).strip()
        keywords = [str(k) for k in parsed.get("keywords", [])]
        aliases = [str(a) for a in parsed.get("aliases", [])]
    except Exception:
        return None

    # Validate or repair slug
    slug = raw_slug.strip()
    if not slug or not SLUG_RE.match(slug):
        slug = slugify(label) if label else None
    if not slug or not SLUG_RE.match(slug):
        return None

    if not label:
        return None

    return ProposedConcept(
        slug=slug,
        label=label,
        definition=definition,
        keywords=keywords,
        aliases=aliases,
        cluster_id=summary.cluster_id,
        size=summary.size,
        families=list(summary.families),
        projects=list(summary.projects),
        example_entry_ids=list(summary.entry_ids[:8]),
        source="llm",
    )


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------


def label_cluster(
    summary: ClusterSummary,
    use_llm: bool = False,
    claude_bin: str | None = None,
    run: object = subprocess.run,
) -> ProposedConcept:
    """
    Label a cluster, optionally trying the LLM path first.

    If ``use_llm=True`` and the LLM call succeeds, the LLM concept is returned.
    Otherwise (LLM disabled, binary not found, or any failure), falls back to
    ``tfidf_label`` — the guaranteed offline default.
    """
    if use_llm:
        proposal = llm_label(summary, claude_bin=claude_bin, run=run)
        if proposal is not None:
            return proposal
    return tfidf_label(summary)
