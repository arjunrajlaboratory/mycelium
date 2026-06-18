"""
build_vault.py — Write Obsidian-style markdown vault notes from a Graph + Facet map.

Generates four directory trees under out_dir:
  projects/<project_id>.md               — one note per ProjectHub
  concepts/bridge/<slug>.md              — cross-project concepts (families >= 2)
  concepts/candidate/<slug>.md           — candidate concepts
  concepts/confirmed/<slug>.md           — confirmed non-bridge concepts
  concepts/curated/<slug>.md             — curated singleton concepts
  entries/decision/<entry_id>.md         — decision entries
  entries/learning/<entry_id>.md         — learning entries
  entries/finding/<entry_id>.md          — finding entries
  entries/other/<entry_id>.md            — convention/feedback/other entries
  logs/<project_id>/<log_id>.md          — one note per LogNode (episodic tier)
  supporting/reviews/<slug>.md           — review reports
  supporting/findings/<review-slug>/F<NN>.md  — individual findings
  supporting/transfers/<slug>.md         — knowledge-transfer reports
  supporting/transfer-items/<transfer-slug>/I<NN>.md  — transfer items

Stale content directories (concepts/, entries/, logs/, projects/, supporting/) are
removed at the start of each build to avoid duplicate graph nodes.
vault/.obsidian/ graph.json is written on first build (or when keep_graph_config=False).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from graph_model import (
    ConceptStatus,
    EdgeType,
    EntryKind,
    EntryStatus,
    Facet,
    Graph,
    LogNode,
    Stage,
    SupportingKind,
    SupportingNode,
)

# ---------------------------------------------------------------------------
# Stage display order (unassigned always last)
# ---------------------------------------------------------------------------

_STAGE_ORDER: list[Stage] = [
    Stage.data_registry,
    Stage.lit_review,
    Stage.planning,
    Stage.analysis,
    Stage.figure_generation,
    Stage.writing,
    Stage.evaluation,
    Stage.infrastructure,
    Stage.unassigned,
]

_STAGE_RANK: dict[Stage, int] = {s: i for i, s in enumerate(_STAGE_ORDER)}


def _stage_display(stage: Stage) -> str:
    """Convert Stage enum value to Title-Case display string."""
    return stage.value.replace("_", " ").title()


# ---------------------------------------------------------------------------
# YAML frontmatter helpers
# ---------------------------------------------------------------------------


def _yaml_escape(value: str) -> str:
    """Escape backslashes then double-quotes for YAML double-quoted strings."""
    value = value.replace("\\", "\\\\")
    value = value.replace('"', '\\"')
    return value


def _yaml_str(value: str | None) -> str:
    """Return a double-quoted YAML string (escaped). Empty string for None."""
    if value is None:
        return '""'
    return f'"{_yaml_escape(value)}"'


def _tag_slug(s: str) -> str:
    """
    Sanitize a family/kind string to a nested-tag slug.

    Lowercases, replaces spaces and underscores with hyphens, strips any
    characters that are not alphanumeric, hyphens, or forward-slashes.
    """
    s = s.lower()
    s = re.sub(r"[ _]+", "-", s)
    s = re.sub(r"[^a-z0-9\-/]", "", s)
    return s


def _yaml_tags(tags: list[str]) -> str:
    """
    Render a list of tag strings as a YAML flow sequence, e.g.:
      tags: [concept, bridge, confirmed]
    """
    inner = ", ".join(tags)
    return f"tags: [{inner}]"


def _make_slug(s: str) -> str:
    """Lowercase and replace non-alphanumeric runs with hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_vault(
    graph: Graph,
    facets: dict[str, Facet],
    out_dir: Path,
    keep_graph_config: bool = False,
) -> None:
    """Write project, concept, entry, log, and supporting markdown notes to out_dir."""

    # ------------------------------------------------------------------
    # Clean stale content directories (flat notes from previous builds).
    # MUST preserve vault/.obsidian/ (graph.json).
    # ------------------------------------------------------------------
    import shutil

    for stale_dir in ("concepts", "entries", "logs", "projects", "supporting"):
        stale_path = out_dir / stale_dir
        if stale_path.exists():
            shutil.rmtree(stale_path)

    # ------------------------------------------------------------------
    # Write vault/.obsidian/graph.json (skipped when keep_graph_config=True
    # and the file already exists).
    # ------------------------------------------------------------------
    obsidian_dir = out_dir / ".obsidian"
    obsidian_dir.mkdir(parents=True, exist_ok=True)

    # Delete stale colorgroups file if present
    stale_cg = obsidian_dir / "colorgroups-by-project.json"
    if stale_cg.exists():
        stale_cg.unlink()

    graph_json_path = obsidian_dir / "graph.json"
    if not keep_graph_config or not graph_json_path.exists():

        def _make_rgb(r: int, g: int, b: int) -> int:
            return r * 65536 + g * 256 + b

        COLOR_GROUPS = [
            {
                "query": "path:projects/",
                "color": {"a": 1.0, "rgb": _make_rgb(70, 130, 180)},
            },
            {
                "query": "path:concepts/bridge/",
                "color": {"a": 1.0, "rgb": _make_rgb(148, 103, 189)},
            },
            {
                "query": "path:concepts/confirmed/",
                "color": {"a": 1.0, "rgb": _make_rgb(44, 160, 44)},
            },
            {
                "query": "path:concepts/curated/",
                "color": {"a": 1.0, "rgb": _make_rgb(23, 190, 207)},
            },
            {
                "query": "path:concepts/candidate/",
                "color": {"a": 0.9, "rgb": _make_rgb(152, 223, 138)},
            },
            {
                "query": "path:supporting/reviews/",
                "color": {"a": 1.0, "rgb": _make_rgb(214, 39, 40)},
            },
            {
                "query": "path:supporting/transfers/",
                "color": {"a": 1.0, "rgb": _make_rgb(255, 187, 120)},
            },
            {
                "query": "path:entries/finding/",
                "color": {"a": 0.30, "rgb": _make_rgb(188, 189, 34)},
            },
            {
                "query": "path:entries/decision/",
                "color": {"a": 0.28, "rgb": _make_rgb(140, 86, 75)},
            },
            {
                "query": "path:entries/learning/",
                "color": {"a": 0.25, "rgb": _make_rgb(31, 119, 180)},
            },
            {
                "query": "path:entries/other/",
                "color": {"a": 0.25, "rgb": _make_rgb(174, 199, 232)},
            },
            {
                "query": "path:logs/",
                "color": {"a": 0.16, "rgb": _make_rgb(127, 127, 127)},
            },
            {
                "query": "tag:severity/critical",
                "color": {"a": 1.0, "rgb": _make_rgb(220, 38, 38)},
            },
            {
                "query": "tag:severity/major",
                "color": {"a": 0.95, "rgb": _make_rgb(234, 88, 12)},
            },
            {
                "query": "tag:severity/minor",
                "color": {"a": 0.8, "rgb": _make_rgb(107, 114, 128)},
            },
            {
                "query": "tag:severity/unknown",
                "color": {"a": 0.7, "rgb": _make_rgb(100, 116, 139)},
            },
        ]

        GRAPH_SETTINGS = {
            "colorGroups": COLOR_GROUPS,
            "nodeSizeMultiplier": 2.2,
            "lineSizeMultiplier": 0.35,
            "textFadeMultiplier": -1.5,
            "showArrow": False,
            "showOrphans": False,
            "hideUnresolved": False,
            "showTags": False,
            "search": "",
            "repelStrength": 13,
            "linkDistance": 120,
            "linkStrength": 0.6,
            "centerStrength": 0.3,
        }
        graph_json_path.write_text(
            json.dumps(GRAPH_SETTINGS, indent=2), encoding="utf-8"
        )

    # Create output subdirectories (top-level; subfolders created on demand)
    projects_dir = out_dir / "projects"
    concepts_dir = out_dir / "concepts"
    entries_dir = out_dir / "entries"
    logs_dir = out_dir / "logs"
    projects_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir.mkdir(parents=True, exist_ok=True)
    entries_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Pre-compute: about-edge lookup tables
    # ------------------------------------------------------------------

    # entry_id → sorted list of concept slugs
    entry_to_concepts: dict[str, list[str]] = {}
    # concept_slug → sorted list of entry_ids
    concept_to_entries: dict[str, list[str]] = {}

    for edge in sorted(graph.edges, key=lambda e: (e.from_id, e.to_id)):
        if edge.type != EdgeType.about:
            continue
        entry_id = edge.from_id
        concept_slug = edge.to_id
        entry_to_concepts.setdefault(entry_id, [])
        if concept_slug not in entry_to_concepts[entry_id]:
            entry_to_concepts[entry_id].append(concept_slug)
        concept_to_entries.setdefault(concept_slug, [])
        if entry_id not in concept_to_entries[concept_slug]:
            concept_to_entries[concept_slug].append(entry_id)

    # Sort all lists for determinism
    for k in entry_to_concepts:
        entry_to_concepts[k].sort()
    for k in concept_to_entries:
        concept_to_entries[k].sort()

    # Build entry lookup by id
    entry_by_id = {e.id: e for e in graph.entries}

    # Change 4: Build known_project_ids set to guard dangling wikilinks.
    known_project_ids: set[str] = {h.project_id for h in graph.project_hubs}

    # ------------------------------------------------------------------
    # Change 5: Standalone log_follows loop (log_to_concepts removed).
    # ------------------------------------------------------------------
    log_follows: dict[str, str] = {}
    for edge in sorted(graph.edges, key=lambda e: (e.from_id, e.to_id)):
        if edge.type == EdgeType.follows:
            log_follows[edge.from_id] = edge.to_id

    # ------------------------------------------------------------------
    # Build log_to_entries: log_id → sorted list[entry_id] from created_in edges.
    # ------------------------------------------------------------------
    log_to_entries: dict[str, list[str]] = {}
    for edge in sorted(graph.edges, key=lambda e: (e.from_id, e.to_id)):
        if edge.type == EdgeType.created_in:
            log_to_entries.setdefault(edge.from_id, [])
            if edge.to_id not in log_to_entries[edge.from_id]:
                log_to_entries[edge.from_id].append(edge.to_id)
    # Ensure deterministic ordering within each list
    for k in log_to_entries:
        log_to_entries[k].sort()

    # ------------------------------------------------------------------
    # Write project notes
    # ------------------------------------------------------------------

    for hub in sorted(graph.project_hubs, key=lambda h: h.project_id):
        # Gather active entries for this project, group by stage
        stage_to_entries: dict[Stage, list] = {}
        for entry in sorted(graph.entries, key=lambda e: e.id):
            if entry.project_id != hub.project_id:
                continue
            if entry.status == EntryStatus.tombstone:
                continue
            facet = facets.get(entry.id)
            stage = facet.stage if facet is not None else Stage.unassigned
            stage_to_entries.setdefault(stage, [])
            stage_to_entries[stage].append(entry)

        # Gather distinct concepts touched by entries in this project
        touched_concepts: set[str] = set()
        for entry in graph.entries:
            if entry.project_id != hub.project_id:
                continue
            if entry.status == EntryStatus.tombstone:
                continue
            for slug in entry_to_concepts.get(entry.id, []):
                touched_concepts.add(slug)

        # Task 2: project tags
        project_tags = ["project", f"fam/{_tag_slug(hub.family)}"]

        lines: list[str] = []
        # Frontmatter
        lines.append("---")
        lines.append("type: project")
        lines.append(f"family: {_yaml_str(hub.family)}")
        lines.append(_yaml_tags(project_tags))
        # Change 3: aliases for project hub
        lines.append(f'aliases: ["{_yaml_escape(hub.name)}"]')
        lines.append("---")
        lines.append("")
        lines.append(f"# {hub.name}")
        lines.append("")

        # Stage sections in enum order
        for stage in _STAGE_ORDER:
            if stage not in stage_to_entries:
                continue
            entries_in_stage = stage_to_entries[stage]
            lines.append(f"## {_stage_display(stage)}")
            for entry in sorted(entries_in_stage, key=lambda e: e.id):
                lines.append(f"- [[{entry.id}]] — {entry.title}")
            lines.append("")

        # Concepts touched section
        if touched_concepts:
            lines.append("## Concepts touched")
            for slug in sorted(touched_concepts):
                lines.append(f"- [[{slug}]]")
            lines.append("")

        content = "\n".join(lines)
        (projects_dir / f"{hub.project_id}.md").write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Write concept notes
    # ------------------------------------------------------------------

    # Change 2: Track bridge/confirmed concepts for MOC generation.
    bridge_concepts: list = []
    confirmed_concepts: list = []

    for concept in sorted(graph.concepts, key=lambda c: c.slug):
        # Find all entries linked to this concept
        linked_entry_ids = concept_to_entries.get(concept.slug, [])

        # Count distinct families among linked entries
        families_set: set[str] = set()
        for eid in linked_entry_ids:
            entry = entry_by_id.get(eid)
            if entry is not None:
                families_set.add(entry.family)
        n_families = len(families_set)

        # Group entries by project_id
        project_to_concept_entries: dict[str, list] = {}
        for eid in linked_entry_ids:
            entry = entry_by_id.get(eid)
            if entry is None:
                continue
            project_to_concept_entries.setdefault(entry.project_id, [])
            project_to_concept_entries[entry.project_id].append(entry)

        # Task 2: concept tags
        # Use effective_status if present, else status
        status_for_tag = (
            concept.effective_status.value
            if concept.effective_status is not None
            else concept.status.value
        )
        concept_tags = ["concept"]
        if n_families >= 2:
            concept_tags.append("bridge")
        concept_tags.append(status_for_tag)

        lines: list[str] = []
        # Frontmatter
        effective_val = (
            concept.effective_status.value
            if concept.effective_status is not None
            else ""
        )
        lines.append("---")
        lines.append("type: concept")
        lines.append(f"status: {_yaml_str(concept.status.value)}")
        lines.append(f"effective_status: {_yaml_str(effective_val)}")
        lines.append(f"families: {n_families}")
        lines.append(_yaml_tags(concept_tags))
        lines.append("---")
        lines.append("")
        lines.append(f"# {concept.label}")
        lines.append("")
        lines.append(concept.definition)
        lines.append("")

        # Cross-project badge
        if n_families >= 2:
            lines.append(f"**🔗 cross-project** — spans {n_families} families")
            lines.append("")

        # Per-project sections
        for project_id in sorted(project_to_concept_entries.keys()):
            lines.append(f"## {project_id}")
            for entry in sorted(
                project_to_concept_entries[project_id], key=lambda e: e.id
            ):
                lines.append(f"- [[{entry.id}]] — {entry.title}")
            lines.append("")

        content = "\n".join(lines)

        # Change 7: Route concept into subfolder: bridge / candidate / curated / confirmed.
        if n_families >= 2:
            concept_subfolder = concepts_dir / "bridge"
            bridge_concepts.append(concept)
        elif status_for_tag == ConceptStatus.candidate.value:
            concept_subfolder = concepts_dir / "candidate"
        elif status_for_tag == "curated_singleton":
            # Curated concepts that span only one family route to concepts/curated/
            concept_subfolder = concepts_dir / "curated"
        else:
            concept_subfolder = concepts_dir / "confirmed"
            confirmed_concepts.append(concept)
        concept_subfolder.mkdir(parents=True, exist_ok=True)
        (concept_subfolder / f"{concept.slug}.md").write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Write entry notes (active only)
    # ------------------------------------------------------------------

    for entry in sorted(graph.entries, key=lambda e: e.id):
        if entry.status == EntryStatus.tombstone:
            continue

        facet = facets.get(entry.id)
        stage_val = facet.stage.value if facet is not None else Stage.unassigned.value

        # Linked concepts
        linked_slugs = entry_to_concepts.get(entry.id, [])

        # Change 6: Build entry_tags first (including optional extra tags), then frontmatter.
        entry_tags = [
            "entry",
            f"kind/{_tag_slug(entry.kind.value)}",
            f"fam/{_tag_slug(entry.family)}",
        ]
        # Append optional extra tags before emitting frontmatter
        if getattr(entry, "finding_status", None) is not None:
            entry_tags.append(f"status/{_tag_slug(entry.finding_status)}")
        if getattr(entry, "mitigation_type", None) is not None:
            entry_tags.append(f"mitigation/{_tag_slug(entry.mitigation_type)}")

        lines: list[str] = []
        # Frontmatter — Change 6: optional fields after source, then tags, then aliases.
        lines.append("---")
        lines.append("type: entry")
        lines.append(f"project: {_yaml_str(entry.project_id)}")
        lines.append(f"family: {_yaml_str(entry.family)}")
        lines.append(f"stage: {_yaml_str(stage_val)}")
        lines.append(f"kind: {_yaml_str(entry.kind.value)}")
        lines.append(f"date: {_yaml_str(entry.date or '')}")
        lines.append(f"source: {_yaml_str(entry.source_path)}")
        if getattr(entry, "finding_status", None) is not None:
            lines.append(f"finding_status: {_yaml_str(entry.finding_status)}")
        if getattr(entry, "mitigation_type", None) is not None:
            lines.append(f"mitigation_type: {_yaml_str(entry.mitigation_type)}")
        if getattr(entry, "source_project", None) is not None:
            lines.append(f"source_project: {_yaml_str(entry.source_project)}")
        lines.append(_yaml_tags(entry_tags))
        # Change 3: aliases for entry notes
        lines.append(f'aliases: ["{_yaml_escape(entry.title)}"]')
        lines.append("---")
        lines.append("")
        lines.append(f"# {entry.title}")
        lines.append("")
        # Change 4: guard project wikilink
        if entry.project_id in known_project_ids:
            lines.append(f"Project: [[{entry.project_id}]]")
        else:
            lines.append(f"Project: {entry.project_id}")
        lines.append("")
        concepts_str = " ".join(f"[[{slug}]]" for slug in linked_slugs)
        lines.append(f"Concepts: {concepts_str}")
        lines.append("")
        lines.append(entry.body_excerpt)
        lines.append("")
        lines.append(f"Source: {entry.source_path}")
        lines.append("")

        content = "\n".join(lines)

        # Route entry into subfolder: decision / learning / finding / other
        _kind_folder_map = {
            EntryKind.decision.value: "decision",
            EntryKind.learning.value: "learning",
            EntryKind.finding.value: "finding",
        }
        entry_subfolder_name = _kind_folder_map.get(entry.kind.value, "other")
        entry_subfolder = entries_dir / entry_subfolder_name
        entry_subfolder.mkdir(parents=True, exist_ok=True)
        (entry_subfolder / f"{entry.id}.md").write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Write log notes (episodic tier)
    # ------------------------------------------------------------------

    _write_log_notes(
        logs=graph.logs,
        logs_dir=logs_dir,
        log_follows=log_follows,
        known_project_ids=known_project_ids,
        log_to_entries=log_to_entries,
    )

    # ------------------------------------------------------------------
    # Write supporting nodes (reviews, findings, transfers, transfer-items)
    # ------------------------------------------------------------------
    if graph.supporting_nodes:
        _write_supporting_notes(
            nodes=graph.supporting_nodes,
            out_dir=out_dir,
            known_project_ids=known_project_ids,
        )

    # ------------------------------------------------------------------
    # Change 2: Generate 000-MAP-OF-CONTENT.md (always regenerated).
    # ------------------------------------------------------------------
    moc_lines: list[str] = []
    moc_lines.append("# Map of Content")
    moc_lines.append("")
    moc_lines.append(
        "This vault contains notes generated from the mycelium knowledge graph. "
        "Notes are organized as follows:"
    )
    moc_lines.append("")
    moc_lines.append(
        "- **concepts/bridge/** — concepts that appear in 2+ project families (cross-project)"
    )
    moc_lines.append("- **concepts/confirmed/** — confirmed non-bridge concepts")
    moc_lines.append("- **concepts/candidate/** — candidate concepts awaiting review")
    moc_lines.append("- **concepts/curated/** — manually curated singleton concepts")
    moc_lines.append("- **entries/decision/** — decision entries")
    moc_lines.append("- **entries/learning/** — learning entries")
    moc_lines.append("- **entries/finding/** — finding entries")
    moc_lines.append("- **logs/** — episodic session logs, grouped by project")
    moc_lines.append("")

    moc_lines.append("## Projects")
    moc_lines.append("")
    for hub in sorted(graph.project_hubs, key=lambda h: h.project_id):
        moc_lines.append(f"- [[{hub.project_id}]]")
    moc_lines.append("")

    if bridge_concepts:
        moc_lines.append("## Concepts (bridge)")
        moc_lines.append("")
        for concept in sorted(bridge_concepts, key=lambda c: c.slug):
            moc_lines.append(f"- [[{concept.slug}]]")
        moc_lines.append("")

    if confirmed_concepts:
        moc_lines.append("## Concepts (confirmed)")
        moc_lines.append("")
        for concept in sorted(confirmed_concepts, key=lambda c: c.slug):
            moc_lines.append(f"- [[{concept.slug}]]")
        moc_lines.append("")

    # Link to views/ if it exists under out_dir
    views_dir = out_dir / "views"
    if views_dir.exists() and views_dir.is_dir():
        view_files = sorted(views_dir.glob("*.md"))
        if view_files:
            moc_lines.append("## Views")
            moc_lines.append("")
            for vf in view_files:
                moc_lines.append(f"- [[../views/{vf.name}]]")
            moc_lines.append("")

    moc_content = "\n".join(moc_lines)
    (out_dir / "000-MAP-OF-CONTENT.md").write_text(moc_content, encoding="utf-8")


def _write_log_notes(
    logs: list[LogNode],
    logs_dir: Path,
    log_follows: dict[str, str],
    known_project_ids: set[str],
    log_to_entries: dict[str, list[str]] | None = None,
) -> None:
    """
    Write one markdown note per LogNode into logs/<project_id>/<log_id>.md.

    Frontmatter keys: type, project, family, date, title, tags.
    Body contains the log's title/excerpt and Obsidian wikilinks for:
      - project link    → [[<project_id>]]  (connects log to its project hub)
      - follows edge    → [[<prev_log_id>]] (chronological chain only)
      - produced entries → ## Produced in this session section with [[entry_id]] wikilinks
    No concept wikilinks are written from log notes.
    """
    _log_to_entries = log_to_entries or {}
    for log in sorted(logs, key=lambda l: l.id):
        # Create per-project subdirectory
        project_log_dir = logs_dir / log.project_id
        project_log_dir.mkdir(parents=True, exist_ok=True)

        # Tags: [log, fam/<family>]
        log_tags = ["log", f"fam/{_tag_slug(log.family)}"]

        # Date field: use session_date if available
        date_val = log.session_date or ""

        lines: list[str] = []
        # Frontmatter
        lines.append("---")
        lines.append("type: log")
        lines.append(f"project: {_yaml_str(log.project_id)}")
        lines.append(f"family: {_yaml_str(log.family)}")
        lines.append(f"date: {_yaml_str(date_val)}")
        lines.append(f"title: {_yaml_str(log.title)}")
        lines.append(_yaml_tags(log_tags))
        lines.append("---")
        lines.append("")
        lines.append(f"# {log.title}")
        lines.append("")

        # Change 4: guard project wikilink in log notes
        if log.project_id in known_project_ids:
            lines.append(f"Project: [[{log.project_id}]]")
        else:
            lines.append(f"Project: {log.project_id}")
        lines.append("")

        # Body excerpt
        if log.body_excerpt:
            lines.append(log.body_excerpt)
            lines.append("")

        # Follows wikilink (chronological predecessor in same project)
        prev_log_id = log_follows.get(log.id)
        if prev_log_id:
            lines.append(f"Previous session: [[{prev_log_id}]]")
            lines.append("")

        # Produced in this session — created_in entries (non-empty only)
        produced_entry_ids = _log_to_entries.get(log.id, [])
        if produced_entry_ids:
            lines.append("## Produced in this session")
            for eid in produced_entry_ids:
                lines.append(f"- [[{eid}]]")
            lines.append("")

        # NOTE: "Concepts mentioned" section intentionally removed.
        # Log nodes connect only to their project hub and the chronological chain.

        content = "\n".join(lines)
        (project_log_dir / f"{log.id}.md").write_text(content, encoding="utf-8")


def _write_supporting_notes(
    nodes: list[SupportingNode],
    out_dir: Path,
    known_project_ids: set[str],
) -> None:
    """
    Write supporting knowledge nodes (reviews, findings, transfers, transfer-items).

    Folder routing:
      supporting/reviews/<review-slug>.md
      supporting/findings/<review-slug>/F<NN>.md
      supporting/transfers/<transfer-slug>.md
      supporting/transfer-items/<transfer-slug>/I<NN>.md
    """
    # Group findings and transfer-items by parent for ordinal fallback
    findings_by_parent: dict[str, list[SupportingNode]] = {}
    items_by_parent: dict[str, list[SupportingNode]] = {}
    for n in sorted(nodes, key=lambda x: x.id):
        if n.kind == SupportingKind.finding and n.parent_id:
            findings_by_parent.setdefault(n.parent_id, []).append(n)
        elif n.kind == SupportingKind.transfer_item and n.parent_id:
            items_by_parent.setdefault(n.parent_id, []).append(n)

    for node in sorted(nodes, key=lambda x: x.id):
        slug = _make_slug(node.id)
        date_val = node.date or ""
        sev = node.severity or "unknown"

        if node.kind == SupportingKind.review:
            # Determine project wikilinks
            pids = (
                node.project_ids
                if node.project_ids
                else ([node.project_id] if node.project_id else [])
            )
            lines: list[str] = ["---", "type: review"]
            if len(pids) > 1:
                lines.append(f"projects: [{', '.join(pids)}]")
            elif pids:
                lines.append(f'project: "{pids[0]}"')
            lines.append(f'date: "{date_val}"')
            lines.append(f'aliases: ["{_yaml_escape(node.title)}"]')
            lines.append("tags: [support/review]")
            lines.append("---")
            lines.append("")
            lines.append(f"# {node.title}")
            lines.append("")
            # Project wikilinks
            for pid in pids:
                if pid in known_project_ids:
                    lines.append(f"[[{pid}]]")
                else:
                    lines.append(pid)
            lines.append("")
            if node.body_excerpt:
                lines.append(node.body_excerpt)
                lines.append("")

            dest = out_dir / "supporting" / "reviews"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")

        elif node.kind == SupportingKind.finding:
            parent_id = node.parent_id or ""
            parent_slug = _make_slug(parent_id) if parent_id else ""
            # Extract NN from id: look for -f(\d+) at end
            m = re.search(r"-f(\d+)$", node.id)
            if m:
                nn = m.group(1).zfill(2)
            else:
                # Ordinal fallback
                siblings = findings_by_parent.get(parent_id, [])
                idx = next(
                    (i + 1 for i, s in enumerate(siblings) if s.id == node.id), 1
                )
                nn = str(idx).zfill(2)

            lines = ["---", "type: finding"]
            lines.append(f'severity: "{sev}"')
            if parent_slug:
                lines.append(f'parent: "{parent_slug}"')
            lines.append(f'date: "{date_val}"')
            lines.append(f'aliases: ["{_yaml_escape(node.title)}"]')
            lines.append(f"tags: [support/finding, severity/{sev}]")
            lines.append("---")
            lines.append("")
            lines.append(f"# {node.title}")
            lines.append("")
            if parent_slug:
                lines.append(f"[[{parent_slug}]]")
                lines.append("")
            if node.body_excerpt:
                lines.append(node.body_excerpt)
                lines.append("")

            dest = out_dir / "supporting" / "findings" / parent_slug
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"F{nn}.md").write_text("\n".join(lines), encoding="utf-8")

        elif node.kind == SupportingKind.transfer:
            pids = (
                node.project_ids
                if node.project_ids
                else ([node.project_id] if node.project_id else [])
            )
            lines = ["---", "type: transfer"]
            lines.append(f"projects: [{', '.join(pids)}]")
            lines.append(f'date: "{date_val}"')
            lines.append(f'aliases: ["{_yaml_escape(node.title)}"]')
            lines.append("tags: [support/transfer]")
            lines.append("---")
            lines.append("")
            lines.append(f"# {node.title}")
            lines.append("")
            for pid in pids:
                if pid in known_project_ids:
                    lines.append(f"[[{pid}]]")
                else:
                    lines.append(pid)
            lines.append("")
            if node.body_excerpt:
                lines.append(node.body_excerpt)
                lines.append("")

            dest = out_dir / "supporting" / "transfers"
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"{slug}.md").write_text("\n".join(lines), encoding="utf-8")

        elif node.kind == SupportingKind.transfer_item:
            parent_id = node.parent_id or ""
            parent_slug = _make_slug(parent_id) if parent_id else ""
            m = re.search(r"-i(\d+)$", node.id)
            if m:
                nn = m.group(1).zfill(2)
            else:
                siblings = items_by_parent.get(parent_id, [])
                idx = next(
                    (i + 1 for i, s in enumerate(siblings) if s.id == node.id), 1
                )
                nn = str(idx).zfill(2)

            lines = ["---", "type: transfer-item"]
            if parent_slug:
                lines.append(f'parent: "{parent_slug}"')
            lines.append(f'date: "{date_val}"')
            lines.append(f'aliases: ["{_yaml_escape(node.title)}"]')
            lines.append("tags: [support/transfer-item]")
            lines.append("---")
            lines.append("")
            lines.append(f"# {node.title}")
            lines.append("")
            if parent_slug:
                lines.append(f"[[{parent_slug}]]")
                lines.append("")
            if node.body_excerpt:
                lines.append(node.body_excerpt)
                lines.append("")

            dest = out_dir / "supporting" / "transfer-items" / parent_slug
            dest.mkdir(parents=True, exist_ok=True)
            (dest / f"I{nn}.md").write_text("\n".join(lines), encoding="utf-8")
