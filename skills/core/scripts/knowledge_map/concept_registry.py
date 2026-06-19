"""
concept_registry.py — Load and validate the curated concept registry and project list.

Flat intra-package imports (the directory is on sys.path when run).
Python 3.13+, stdlib + pyyaml only.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from graph_model import (
    Concept,
    ConceptStatus,
    MatchMode,
    ProjectMeta,
    SCHEMA_VERSION,
)

# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Registry:
    concepts: list[Concept]
    projects: list[ProjectMeta]
    force_about: list[
        dict
    ]  # raw overrides from overrides.yaml — [{entry, concept}, ...]
    block_about: list[
        dict
    ]  # raw overrides from overrides.yaml — [{entry, concept}, ...]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return parsed content as a dict."""
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _check_schema_version(data: dict, filename: str) -> None:
    """Raise SystemExit(1) if schema_version != SCHEMA_VERSION."""
    actual = data.get("schema_version")
    if actual != SCHEMA_VERSION:
        print(
            f"[concept_registry] {filename}: schema_version={actual} != "
            f"{SCHEMA_VERSION}; run 'map migrate' to upgrade",
            file=sys.stderr,
        )
        sys.exit(1)


def _parse_concepts(data: dict, filename: str) -> list[Concept]:
    """Parse and validate concepts from concepts.yaml payload."""
    raw_concepts: list[dict] = data.get("concepts", []) or []
    seen_slugs: dict[str, int] = {}
    result: list[Concept] = []

    for idx, raw in enumerate(raw_concepts):
        # --- slug ---
        slug = raw.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            raise ValueError(
                f"[concept_registry] invalid slug {slug!r}: must match ^[a-z0-9][a-z0-9-]*$"
            )

        # --- duplicate slug ---
        if slug in seen_slugs:
            raise ValueError(f"[concept_registry] duplicate slug {slug!r}")
        seen_slugs[slug] = idx

        # --- label ---
        label = raw.get("label")
        if not label or not isinstance(label, str):
            raise ValueError(
                f"[concept_registry] concept {slug!r}: 'label' is required and must be non-empty"
            )

        # --- definition ---
        definition = raw.get("definition")
        if not definition or not isinstance(definition, str):
            raise ValueError(
                f"[concept_registry] concept {slug!r}: 'definition' is required and must be non-empty"
            )

        # --- status ---
        status_val = raw.get("status")
        try:
            status = ConceptStatus(status_val)
        except (ValueError, KeyError):
            raise ValueError(
                f"[concept_registry] concept {slug!r}: invalid status {status_val!r}"
            )

        # --- match_mode ---
        match_mode_val = raw.get("match_mode")
        try:
            match_mode = MatchMode(match_mode_val)
        except (ValueError, KeyError):
            raise ValueError(
                f"[concept_registry] concept {slug!r}: invalid match_mode {match_mode_val!r}"
            )

        # --- optional list fields ---
        aliases: list[str] = raw.get("aliases") or []
        positive_keywords: list[str] = raw.get("positive_keywords") or []
        negative_keywords: list[str] = raw.get("negative_keywords") or []
        required_any: list[str] = raw.get("required_any") or []
        relates: list[str] = raw.get("relates") or []

        # --- project_scope: null → None ---
        project_scope_raw = raw.get("project_scope", None)
        project_scope: list[str] | None = (
            None if project_scope_raw is None else list(project_scope_raw)
        )

        # --- parent: null → None ---
        parent: str | None = raw.get("parent", None)

        result.append(
            Concept(
                slug=slug,
                label=label,
                definition=definition,
                status=status,
                aliases=aliases,
                positive_keywords=positive_keywords,
                negative_keywords=negative_keywords,
                required_any=required_any,
                project_scope=project_scope,
                match_mode=match_mode,
                relates=relates,
                parent=parent,
                effective_status=None,  # never stored in source
            )
        )

    return result


def _parse_projects(data: dict, graph_dir: Path) -> list[ProjectMeta]:
    """Parse and validate projects from projects.yaml payload."""
    raw_projects: list[dict] = data.get("projects", []) or []
    portfolio_root = graph_dir.parent.parent  # graph_dir is <root>/.living/graph

    seen_ids: dict[str, str] = {}
    seen_resolved: dict[Path, str] = {}
    result: list[ProjectMeta] = []

    required_fields = ("id", "name", "path", "family", "has_living")

    for raw in raw_projects:
        # --- required fields ---
        for field in required_fields:
            if field not in raw:
                raise ValueError(
                    f"[concept_registry] project missing required field {field!r}: {raw!r}"
                )

        proj_id: str = raw["id"]
        name: str = raw["name"]
        path_str: str = raw["path"]
        family: str = raw["family"]
        has_living: bool = raw["has_living"]

        # --- duplicate id ---
        if proj_id in seen_ids:
            raise ValueError(f"[concept_registry] duplicate project id {proj_id!r}")
        seen_ids[proj_id] = path_str

        # --- duplicate resolved path ---
        resolved = (portfolio_root / path_str).resolve()
        if resolved in seen_resolved:
            other_id = seen_resolved[resolved]
            raise ValueError(
                f"[concept_registry] duplicate resolved path {resolved} "
                f"for projects {other_id!r} and {proj_id!r}"
            )
        seen_resolved[resolved] = proj_id

        result.append(
            ProjectMeta(
                id=proj_id,
                name=name,
                path=path_str,  # original string from YAML
                family=family,
                has_living=has_living,
            )
        )

    return result


def _parse_overrides(data: dict) -> tuple[list[dict], list[dict]]:
    """Parse force_about and block_about from overrides.yaml payload."""
    force_about: list[dict] = data.get("force_about") or []
    block_about: list[dict] = data.get("block_about") or []
    return force_about, block_about


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_registry(graph_dir: Path) -> Registry:
    """
    Load and validate the curated concept registry and project list from graph_dir.

    graph_dir is expected to be <portfolio_root>/.living/graph/ and must contain:
      - concepts.yaml
      - projects.yaml
      - overrides.yaml

    Raises:
        SystemExit(1): on schema_version mismatch in any YAML file.
        ValueError: on invalid slugs, missing required fields, or duplicates.
    """
    # --- concepts.yaml ---
    concepts_path = graph_dir / "concepts.yaml"
    concepts_data = _load_yaml(concepts_path)
    _check_schema_version(concepts_data, concepts_path.name)
    concepts = _parse_concepts(concepts_data, concepts_path.name)

    # --- projects.yaml ---
    projects_path = graph_dir / "projects.yaml"
    projects_data = _load_yaml(projects_path)
    _check_schema_version(projects_data, projects_path.name)
    projects = _parse_projects(projects_data, graph_dir)

    # --- overrides.yaml ---
    overrides_path = graph_dir / "overrides.yaml"
    overrides_data = _load_yaml(overrides_path)
    _check_schema_version(overrides_data, overrides_path.name)
    force_about, block_about = _parse_overrides(overrides_data)

    return Registry(
        concepts=concepts,
        projects=projects,
        force_about=force_about,
        block_about=block_about,
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    graph_dir = Path("/Users/mst36/Desktop/Projects/Science/.living/graph")
    registry = load_registry(graph_dir)
    confirmed = sum(1 for c in registry.concepts if c.status == ConceptStatus.confirmed)
    candidate = sum(1 for c in registry.concepts if c.status == ConceptStatus.candidate)
    curated = sum(
        1 for c in registry.concepts if c.status == ConceptStatus.curated_singleton
    )
    families = {p.family for p in registry.projects}
    print(
        f"Concepts: {len(registry.concepts)} "
        f"({confirmed} confirmed, {candidate} candidate, {curated} curated_singleton)"
    )
    print(
        f"Projects: {len(registry.projects)}, "
        f"Families: {len(families)} {sorted(families)}"
    )
    print(
        f"force_about: {len(registry.force_about)}, block_about: {len(registry.block_about)}"
    )
