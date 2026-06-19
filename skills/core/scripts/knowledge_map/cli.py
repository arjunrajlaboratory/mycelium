"""
cli.py — Orchestrator CLI for the knowledge-map pipeline.

Usage:
    python3 cli.py build --portfolio <root> [--projects id1,id2] [--baseline <path>]

Python 3.13+, stdlib + pyyaml.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_import(module_name: str):
    """Import a pipeline module, returning (module, error_msg)."""
    try:
        mod = __import__(module_name)
        return mod, None
    except ImportError as exc:
        return None, str(exc)


def _warn(msg: str) -> None:
    print(f"WARNING: {msg}", file=sys.stderr)


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# build subcommand
# ---------------------------------------------------------------------------


def cmd_build(args: argparse.Namespace) -> int:
    """
    Full pipeline run:
      extract → link → build_graph → validate → persist → render_views → build_vault
    """
    portfolio = Path(args.portfolio).expanduser().resolve()
    if not portfolio.is_dir():
        _err(f"Portfolio root does not exist: {portfolio}")
        return 1

    graph_dir = portfolio / ".living" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)

    baseline_path: Path | None = None
    if args.baseline:
        baseline_path = Path(args.baseline).expanduser().resolve()
        if not baseline_path.exists():
            _warn(
                f"Baseline path does not exist (continuing without diff): {baseline_path}"
            )
            baseline_path = None

    # ------------------------------------------------------------------
    # Import pipeline modules (fail hard for required ones)
    # ------------------------------------------------------------------
    import concept_registry as _cr
    import extract_entries as _ee
    import link_entries as _le
    import build_graph as _bg

    # Optional modules (may not exist yet)
    render_views_mod, render_views_err = _try_import("render_views")
    build_vault_mod, build_vault_err = _try_import("build_vault")
    extract_logs_mod, extract_logs_err = _try_import("extract_logs")
    link_logs_mod, link_logs_err = _try_import("link_logs")

    if render_views_err:
        _warn(f"render_views not importable (skipping): {render_views_err}")
    if build_vault_err:
        _warn(f"build_vault not importable (skipping): {build_vault_err}")
    if extract_logs_err:
        _warn(
            f"extract_logs not importable (log pipeline disabled): {extract_logs_err}"
        )
    if link_logs_err:
        _warn(f"link_logs not importable (log pipeline disabled): {link_logs_err}")

    # ------------------------------------------------------------------
    # Step 1: Load registry
    # ------------------------------------------------------------------
    print("Loading registry …")
    registry = _cr.load_registry(graph_dir)

    # ------------------------------------------------------------------
    # Step 2: Select projects
    # ------------------------------------------------------------------
    all_projects = registry.projects

    if args.projects:
        requested_ids = [s.strip() for s in args.projects.split(",") if s.strip()]
        known_ids = {p.id for p in all_projects}
        unknown = [pid for pid in requested_ids if pid not in known_ids]
        if unknown:
            _err(
                f"Unknown project id(s): {', '.join(unknown)}. "
                f"Known ids: {', '.join(sorted(known_ids))}"
            )
            return 1
        selected_projects = [p for p in all_projects if p.id in set(requested_ids)]
    else:
        selected_projects = [p for p in all_projects if p.has_living]

    print(
        f"Selected {len(selected_projects)} project(s): {[p.id for p in selected_projects]}"
    )

    # ------------------------------------------------------------------
    # Step 3: Load id ledger (unwrap envelope; pass bare ids dict)
    # ------------------------------------------------------------------
    ledger_path = graph_dir / "entry-ids.json"
    raw_ledger_envelope: dict = {}
    if ledger_path.exists():
        try:
            raw_ledger_envelope = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            _warn(f"Could not read {ledger_path}: {exc}. Starting with empty ledger.")
            raw_ledger_envelope = {}

    # extract_entries expects the bare ids dict (not the envelope)
    # Envelope format on disk: {"schema_version": 1, "ids": {...}}
    if "ids" in raw_ledger_envelope:
        id_ledger: dict = raw_ledger_envelope["ids"]
    else:
        # Either empty or a legacy bare dict — pass as-is
        id_ledger = raw_ledger_envelope

    # ------------------------------------------------------------------
    # Step 4: Extract entries
    # ------------------------------------------------------------------
    print("Extracting entries …")
    ext = _ee.extract_entries(portfolio, selected_projects, id_ledger)

    for msg in ext.report:
        print(f"  [extract] {msg}")

    per_project_counts: dict[str, int] = {}
    for entry in ext.entries:
        per_project_counts[entry.project_id] = (
            per_project_counts.get(entry.project_id, 0) + 1
        )

    # ------------------------------------------------------------------
    # Step 4b: Preserve human-curated stage overrides
    # ------------------------------------------------------------------
    # `_infer_stage` only ever emits path/keyword/default sources, so any facet
    # carrying `stage_source: curated` was hand-edited into entry-facets.yaml.
    # Merge those overrides back into ext.facets before they flow to the graph,
    # the rewritten YAML, and the views/vault — otherwise a normal build
    # silently overwrites manual curation with freshly inferred values.
    _existing_facets_path = graph_dir / "entry-facets.yaml"
    if _existing_facets_path.exists():
        import yaml as _yaml
        from graph_model import Facet, Stage, StageSource

        try:
            _existing_facets_doc = (
                _yaml.safe_load(_existing_facets_path.read_text(encoding="utf-8")) or {}
            )
        except (OSError, _yaml.YAMLError) as exc:
            _warn(
                f"Could not read {_existing_facets_path} "
                f"(curated overrides not preserved): {exc}"
            )
            _existing_facets_doc = {}

        n_preserved = 0
        for eid, fdict in (_existing_facets_doc.get("facets") or {}).items():
            if (
                isinstance(fdict, dict)
                and fdict.get("stage_source") == StageSource.curated.value
                and eid in ext.facets
            ):
                try:
                    ext.facets[eid] = Facet(
                        stage=Stage(fdict["stage"]),
                        stage_source=StageSource.curated,
                    )
                    n_preserved += 1
                except (KeyError, ValueError) as exc:
                    _warn(f"Skipping malformed curated facet for {eid!r}: {exc}")
        if n_preserved:
            print(f"  Preserved {n_preserved} curated stage override(s).")

    # ------------------------------------------------------------------
    # Step 5: Link entries
    # ------------------------------------------------------------------
    print("Linking entries …")
    lr = _le.link_entries(ext.entries, registry)

    for msg in lr.report:
        print(f"  [link] {msg}")

    about_edges = [e for e in lr.edges if e.type.value == "about"]

    # ------------------------------------------------------------------
    # Step 5b: Extract + link logs (R1/R8)
    # ------------------------------------------------------------------
    logs: list = []
    log_edges: list = []
    if extract_logs_mod is not None and link_logs_mod is not None:
        print("Extracting logs …")
        log_ledger_path = graph_dir / "log-ids.json"
        log_ledger = extract_logs_mod.load_log_ledger(log_ledger_path)
        log_result = extract_logs_mod.extract_logs(
            portfolio, selected_projects, log_ledger
        )
        for msg in log_result.report:
            print(f"  [extract_logs] {msg}")
        logs = log_result.logs
        updated_log_ledger = log_result.ledger

        print("Linking logs …")
        link_result = link_logs_mod.link_logs(logs, registry)
        for msg in link_result.report:
            print(f"  [link_logs] {msg}")
        log_edges = link_result.edges

        # Save updated log ledger
        extract_logs_mod.save_log_ledger(log_ledger_path, updated_log_ledger)
        print(f"  Wrote {log_ledger_path}")
    else:
        _warn("Log pipeline skipped (extract_logs or link_logs not available).")

    # ------------------------------------------------------------------
    # Step 6: Build graph
    # ------------------------------------------------------------------
    print("Building graph …")
    graph = _bg.build_graph(
        ext.entries, ext.facets, lr.edges, registry, logs=logs, log_edges=log_edges
    )

    # ------------------------------------------------------------------
    # Step 7: Validate graph
    # ------------------------------------------------------------------
    print("Validating graph …")
    violations = _bg.validate_graph(graph)
    for v in violations:
        _warn(f"[validate] {v}")
    if violations:
        print(f"  {len(violations)} validation warning(s).")
    else:
        print("  No validation warnings.")

    # ------------------------------------------------------------------
    # Step 8: Persist deterministic artifacts
    # ------------------------------------------------------------------
    from graph_model import canonical_json, SCHEMA_VERSION
    import yaml  # pyyaml

    # 8a. knowledge-graph.json
    kg_path = graph_dir / "knowledge-graph.json"
    kg_path.write_text(graph.to_canonical_json(), encoding="utf-8")
    print(f"  Wrote {kg_path}")

    # 8b. entry-ids.json  — always write with the {"schema_version":1,"ids":{...}} envelope
    # ext.ledger is the updated bare ids dict (extract returns it that way)
    updated_ledger = ext.ledger
    # If ext.ledger itself carries the envelope (defensive), unwrap
    if "ids" in updated_ledger and "schema_version" in updated_ledger:
        ids_payload = updated_ledger["ids"]
    else:
        ids_payload = updated_ledger

    ledger_envelope = {"schema_version": SCHEMA_VERSION, "ids": ids_payload}
    ledger_path.write_text(canonical_json(ledger_envelope), encoding="utf-8")
    print(f"  Wrote {ledger_path}")

    # 8c. entry-facets.yaml
    facets_path = graph_dir / "entry-facets.yaml"
    facets_payload = {
        "schema_version": SCHEMA_VERSION,
        "facets": {eid: facet.to_dict() for eid, facet in sorted(ext.facets.items())},
    }
    with facets_path.open("w", encoding="utf-8") as fh:
        yaml.dump(
            facets_payload,
            fh,
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
    print(f"  Wrote {facets_path}")

    # 8d. build-meta.json  — the ONLY place wall-clock is allowed
    mentions_edges = [
        e
        for e in log_edges
        if getattr(e, "type", None) is not None
        and (e.type.value if hasattr(e.type, "value") else str(e.type)) == "mentions"
    ]
    follows_edges = [
        e
        for e in log_edges
        if getattr(e, "type", None) is not None
        and (e.type.value if hasattr(e.type, "value") else str(e.type)) == "follows"
    ]

    build_meta = {
        "build_timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "entries_total": len(ext.entries),
        "entries_per_project": per_project_counts,
        "about_edges": len(about_edges),
        "logs_total": len(logs),
        "log_mentions_edges": len(mentions_edges),
        "log_follows_edges": len(follows_edges),
        "concepts_total": len(graph.concepts),
        "validation_warnings": len(violations),
    }
    build_meta_path = graph_dir / "build-meta.json"
    build_meta_path.write_text(canonical_json(build_meta), encoding="utf-8")
    print(f"  Wrote {build_meta_path}")

    # ------------------------------------------------------------------
    # Step 9: render_views
    # ------------------------------------------------------------------
    views_dir = graph_dir / "views"
    views_dir.mkdir(parents=True, exist_ok=True)

    if render_views_mod is not None:
        print("Rendering views …")
        try:
            render_views_mod.render_views(graph, ext.facets, views_dir, baseline_path)
        except Exception as exc:
            _warn(f"render_views raised an exception (continuing): {exc}")
    else:
        _warn("render_views skipped (module not available).")

    # ------------------------------------------------------------------
    # Step 10: build_vault
    # ------------------------------------------------------------------
    vault_dir = graph_dir / "vault"
    vault_dir.mkdir(parents=True, exist_ok=True)

    if build_vault_mod is not None:
        print("Building vault …")
        try:
            build_vault_mod.build_vault(graph, ext.facets, vault_dir)
        except Exception as exc:
            _warn(f"build_vault raised an exception (continuing): {exc}")
    else:
        _warn("build_vault skipped (module not available).")

    # ------------------------------------------------------------------
    # Step 11: Write unmapped-projects.md (§10)
    # ------------------------------------------------------------------
    _write_unmapped_projects(portfolio, registry, views_dir)

    # ------------------------------------------------------------------
    # Step 12: Summary
    # ------------------------------------------------------------------
    _print_summary(
        graph=graph,
        ext_entries=ext.entries,
        per_project_counts=per_project_counts,
        about_edges=about_edges,
        logs=logs,
        log_edges=log_edges,
        violations=violations,
        kg_path=kg_path,
        ledger_path=ledger_path,
        facets_path=facets_path,
        build_meta_path=build_meta_path,
        views_dir=views_dir,
        vault_dir=vault_dir,
    )

    return 0


def _write_unmapped_projects(
    portfolio: Path,
    registry,
    views_dir: Path,
) -> None:
    """
    List portfolio top-level dirs not in projects.yaml and not dotfiles (§10).
    Writes views/unmapped-projects.md.
    """
    known_paths: set[str] = set()
    for p in registry.projects:
        # Normalise: strip trailing slash, take first component for shallow match
        normed = p.path.strip("/").split("/")[0]
        known_paths.add(normed)

    unmapped: list[str] = []
    try:
        for child in sorted(portfolio.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            if name.startswith("."):
                continue
            if name not in known_paths:
                unmapped.append(name)
    except OSError as exc:
        _warn(f"Could not scan portfolio root for unmapped projects: {exc}")

    lines = [
        "# Unmapped Projects",
        "",
        "Top-level directories in the portfolio root that are **not** listed in `projects.yaml`.",
        "Review and either add them to `projects.yaml` or confirm they are intentionally excluded.",
        "",
    ]
    if unmapped:
        for name in unmapped:
            lines.append(f"- `{name}`")
    else:
        lines.append(
            "_None — all top-level directories are accounted for in projects.yaml._"
        )
    lines.append("")

    out_path = views_dir / "unmapped-projects.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  Wrote {out_path}")


def _print_summary(
    *,
    graph,
    ext_entries: list,
    per_project_counts: dict,
    about_edges: list,
    logs: list,
    log_edges: list,
    violations: list,
    kg_path: Path,
    ledger_path: Path,
    facets_path: Path,
    build_meta_path: Path,
    views_dir: Path,
    vault_dir: Path,
) -> None:
    from graph_model import ConceptStatus

    confirmed_count = sum(
        1 for c in graph.concepts if c.effective_status == ConceptStatus.confirmed
    )
    candidate_count = sum(
        1 for c in graph.concepts if c.effective_status == ConceptStatus.candidate
    )

    # Cross-family confirmed concepts: confirmed + spans >= 2 distinct families
    # (families determined by entries linked to each concept)
    entry_family_by_id: dict[str, str] = {e.id: e.family for e in graph.entries}
    concept_families: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.type.value == "about":
            fam = entry_family_by_id.get(edge.from_id)
            if fam:
                concept_families.setdefault(edge.to_id, set()).add(fam)

    cross_family_confirmed = 0
    for c in graph.concepts:
        if c.effective_status == ConceptStatus.confirmed:
            families = concept_families.get(c.slug, set())
            if len(families) >= 2:
                cross_family_confirmed += 1

    print()
    print("=" * 60)
    print("BUILD SUMMARY")
    print("=" * 60)
    print(f"  Entries extracted (total):  {len(ext_entries)}")
    for pid, cnt in sorted(per_project_counts.items()):
        print(f"    {pid}: {cnt}")
    print(f"  About-edges:                {len(about_edges)}")
    print(f"  Logs extracted:             {len(logs)}")
    _mentions = [
        e
        for e in log_edges
        if getattr(e, "type", None) is not None
        and (e.type.value if hasattr(e.type, "value") else str(e.type)) == "mentions"
    ]
    _follows = [
        e
        for e in log_edges
        if getattr(e, "type", None) is not None
        and (e.type.value if hasattr(e.type, "value") else str(e.type)) == "follows"
    ]
    print(f"  Log mentions-edges:         {len(_mentions)}")
    print(f"  Log follows-edges:          {len(_follows)}")
    print(f"  Concepts confirmed:         {confirmed_count}")
    print(f"  Concepts candidate:         {candidate_count}")
    print(f"  Cross-family (confirmed):   {cross_family_confirmed}")
    print(f"  Validation warnings:        {len(violations)}")
    print()
    print("  Output paths:")
    print(f"    {kg_path}")
    print(f"    {ledger_path}")
    print(f"    {facets_path}")
    print(f"    {build_meta_path}")
    print(f"    {views_dir}/")
    print(f"    {vault_dir}/")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def cmd_propose(args: argparse.Namespace) -> int:
    """
    Propose new concepts from orphaned entries via embedding + clustering.
    Writes a review queue to <graph-dir>/proposals/concepts-candidate.yaml.
    """
    import propose_concepts as _pc

    graph_dir = str(Path(args.graph_dir).expanduser().resolve())

    result = _pc.propose(
        graph_dir=graph_dir,
        min_cluster_size=args.min_cluster_size,
        distance_threshold=args.distance_threshold,
        use_llm=args.llm,
        embed_model=args.embed_model,
        max_llm_labels=args.max_llm_labels,
    )

    cov = result.coverage
    print()
    print("Knowledge-graph coverage summary")
    print(f"  Total entries : {result.n_total}")
    print(f"  Connected now : {result.n_connected} ({cov.get('current_pct', 0.0)}%)")
    print(f"  Orphans       : {result.n_orphans}")
    print(
        f"  Clusters found: {cov.get('n_clusters', 0)} ({cov.get('n_xfam_clusters', 0)} cross-family)"
    )
    print(
        f"  Projected connected (all absorbed)    : {cov.get('projected_all_pct', 0.0)}%"
    )
    print(
        f"  Projected connected (xfam only)       : {cov.get('projected_xfam_pct', 0.0)}%"
    )
    print(f"  Proposals written → {result.out_path}")

    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-map",
        description="Build the mycelium knowledge-map graph from .living/ sources.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # build subcommand
    build_p = sub.add_parser(
        "build",
        help="Run the full pipeline: extract → link → build_graph → persist → render → vault.",
    )
    build_p.add_argument(
        "--portfolio",
        required=True,
        metavar="<path>",
        help="Absolute (or relative) path to the portfolio root directory.",
    )
    build_p.add_argument(
        "--projects",
        default=None,
        metavar="<id1,id2,...>",
        help=(
            "Comma-separated list of project ids to include. "
            "Defaults to all projects with has_living=True."
        ),
    )
    build_p.add_argument(
        "--baseline",
        default=None,
        metavar="<path>",
        help="Path to a prior knowledge-graph.json for link-diff computation.",
    )
    build_p.set_defaults(func=cmd_build)

    # propose subcommand
    propose_p = sub.add_parser(
        "propose",
        help="Propose new concepts from orphaned entries via embedding + clustering.",
    )
    propose_p.add_argument(
        "--graph-dir",
        required=True,
        metavar="<path>",
        help="Path to the .living/graph directory containing knowledge-graph.json.",
    )
    propose_p.add_argument(
        "--min-cluster-size",
        type=int,
        default=5,
        metavar="<n>",
        help="Minimum number of entries to form a cluster (default: 5).",
    )
    propose_p.add_argument(
        "--distance-threshold",
        type=float,
        default=0.55,
        metavar="<f>",
        help="Agglomerative clustering distance threshold, cosine (default: 0.55).",
    )
    propose_p.add_argument(
        "--llm",
        action="store_true",
        default=False,
        help="Use local claude CLI to label clusters (falls back to TF-IDF on failure).",
    )
    propose_p.add_argument(
        "--embed-model",
        default="all-MiniLM-L6-v2",
        metavar="<model>",
        help="SentenceTransformer model name for embedding (default: all-MiniLM-L6-v2).",
    )
    propose_p.add_argument(
        "--max-llm-labels",
        type=int,
        default=30,
        metavar="<n>",
        help="Maximum number of clusters to label with LLM (default: 30).",
    )
    propose_p.set_defaults(func=cmd_propose)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    # Ensure the package directory is on sys.path so flat imports work
    pkg_dir = str(Path(__file__).parent.resolve())
    if pkg_dir not in sys.path:
        sys.path.insert(0, pkg_dir)

    parser = _build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
