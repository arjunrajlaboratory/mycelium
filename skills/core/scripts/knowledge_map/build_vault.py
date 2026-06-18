"""
build_vault.py — Write Obsidian-style markdown vault notes from a Graph + Facet map.

Generates four directory trees under out_dir:
  projects/<project_id>.md               — one note per ProjectHub
  concepts/bridge/<slug>.md              — cross-project concepts (families >= 2)
  concepts/candidate/<slug>.md           — candidate concepts
  concepts/confirmed/<slug>.md           — confirmed non-bridge concepts
  entries/decision/<entry_id>.md         — decision entries
  entries/learning/<entry_id>.md         — learning entries
  entries/finding/<entry_id>.md          — finding entries
  entries/other/<entry_id>.md            — convention/feedback/other entries
  logs/<project_id>/<log_id>.md          — one note per LogNode (episodic tier)

Stale content directories (concepts/, entries/, logs/, projects/) are removed at
the start of each build to avoid duplicate graph nodes.  vault/.obsidian/ is
never touched.
"""

from __future__ import annotations

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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_vault(
    graph: Graph,
    facets: dict[str, Facet],
    out_dir: Path,
) -> None:
    """Write project, concept, entry, and log markdown notes to out_dir."""

    # ------------------------------------------------------------------
    # Clean stale content directories (flat notes from previous builds).
    # MUST preserve vault/.obsidian/ (graph.json, colorgroups-by-project.json).
    # ------------------------------------------------------------------
    import shutil

    for stale_dir in ("concepts", "entries", "logs", "projects"):
        stale_path = out_dir / stale_dir
        if stale_path.exists():
            shutil.rmtree(stale_path)

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

    # ------------------------------------------------------------------
    # Pre-compute: log edge lookup tables
    # ------------------------------------------------------------------

    # log_id → sorted list of concept slugs (mentions edges)
    log_to_concepts: dict[str, list[str]] = {}
    # log_id → previous log_id (follows edge, at most one per log)
    log_follows: dict[str, str] = {}

    for edge in sorted(graph.edges, key=lambda e: (e.from_id, e.to_id)):
        if edge.type == EdgeType.mentions:
            log_id = edge.from_id
            concept_slug = edge.to_id
            log_to_concepts.setdefault(log_id, [])
            if concept_slug not in log_to_concepts[log_id]:
                log_to_concepts[log_id].append(concept_slug)
        elif edge.type == EdgeType.follows:
            log_id = edge.from_id
            prev_log_id = edge.to_id
            log_follows[log_id] = prev_log_id

    for k in log_to_concepts:
        log_to_concepts[k].sort()

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

        # Route concept into subfolder: bridge / candidate / confirmed
        if n_families >= 2:
            concept_subfolder = concepts_dir / "bridge"
        elif status_for_tag == ConceptStatus.candidate.value:
            concept_subfolder = concepts_dir / "candidate"
        else:
            concept_subfolder = concepts_dir / "confirmed"
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

        # Task 2: entry tags
        entry_tags = [
            "entry",
            f"kind/{_tag_slug(entry.kind.value)}",
            f"fam/{_tag_slug(entry.family)}",
        ]

        lines: list[str] = []
        # Frontmatter
        lines.append("---")
        lines.append("type: entry")
        lines.append(f"project: {_yaml_str(entry.project_id)}")
        lines.append(f"family: {_yaml_str(entry.family)}")
        lines.append(f"stage: {_yaml_str(stage_val)}")
        lines.append(f"kind: {_yaml_str(entry.kind.value)}")
        lines.append(f"date: {_yaml_str(entry.date or '')}")
        lines.append(f"source: {_yaml_str(entry.source_path)}")
        lines.append(_yaml_tags(entry_tags))
        lines.append("---")
        lines.append("")
        lines.append(f"# {entry.title}")
        lines.append("")
        lines.append(f"Project: [[{entry.project_id}]]")
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
        log_to_concepts=log_to_concepts,
        log_follows=log_follows,
    )


def _write_log_notes(
    logs: list[LogNode],
    logs_dir: Path,
    log_to_concepts: dict[str, list[str]],
    log_follows: dict[str, str],
) -> None:
    """
    Write one markdown note per LogNode into logs/<project_id>/<log_id>.md.

    Frontmatter keys: type, project, family, date, title, tags.
    Body contains the log's title/excerpt and Obsidian wikilinks for:
      - project link    → [[<project_id>]]  (connects log to its project hub)
      - follows edge    → [[<prev_log_id>]] (chronological chain only)
    No concept wikilinks are written from log notes.
    """
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

        # Project wikilink — mirrors entry-note convention (entry notes use same project_id)
        lines.append(f"Project: [[{log.project_id}]]")
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

        # NOTE: "Concepts mentioned" section intentionally removed.
        # Log nodes connect only to their project hub and the chronological chain.

        content = "\n".join(lines)
        (project_log_dir / f"{log.id}.md").write_text(content, encoding="utf-8")
