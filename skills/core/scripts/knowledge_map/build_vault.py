"""
build_vault.py — Write Obsidian-style markdown vault notes from a Graph + Facet map.

Generates three directories under out_dir:
  projects/<project_id>.md  — one note per ProjectHub
  concepts/<slug>.md        — one note per Concept (all, including unlinked)
  entries/<entry_id>.md     — one note per active Entry
"""

from __future__ import annotations

from pathlib import Path

from graph_model import (
    EdgeType,
    EntryStatus,
    Facet,
    Graph,
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


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_vault(
    graph: Graph,
    facets: dict[str, Facet],
    out_dir: Path,
) -> None:
    """Write project, concept, and entry markdown notes to out_dir."""

    # Create output subdirectories
    projects_dir = out_dir / "projects"
    concepts_dir = out_dir / "concepts"
    entries_dir = out_dir / "entries"
    projects_dir.mkdir(parents=True, exist_ok=True)
    concepts_dir.mkdir(parents=True, exist_ok=True)
    entries_dir.mkdir(parents=True, exist_ok=True)

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

        lines: list[str] = []
        # Frontmatter
        lines.append("---")
        lines.append("type: project")
        lines.append(f"family: {_yaml_str(hub.family)}")
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
        (concepts_dir / f"{concept.slug}.md").write_text(content, encoding="utf-8")

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
        (entries_dir / f"{entry.id}.md").write_text(content, encoding="utf-8")
