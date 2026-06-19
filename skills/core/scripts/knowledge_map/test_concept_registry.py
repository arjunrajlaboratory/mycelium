"""
test_concept_registry.py — pytest tests for concept_registry.py

Flat intra-package imports: the script directory is added to sys.path.
Python 3.13+, stdlib + pyyaml + pytest only.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent))

from concept_registry import load_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: object) -> None:
    path.write_text(yaml.dump(data, sort_keys=False), encoding="utf-8")


def _make_graph_dir(
    tmp_path: Path,
    concepts_data: dict,
    projects_data: dict,
    overrides_data: dict,
) -> Path:
    graph_dir = tmp_path / ".living" / "graph"
    graph_dir.mkdir(parents=True)
    _write_yaml(graph_dir / "concepts.yaml", concepts_data)
    _write_yaml(graph_dir / "projects.yaml", projects_data)
    _write_yaml(graph_dir / "overrides.yaml", overrides_data)
    return graph_dir


# ---------------------------------------------------------------------------
# Test 1: happy path
# ---------------------------------------------------------------------------


def test_happy_path(tmp_path: Path) -> None:
    """A minimal valid registry loads without errors."""
    concepts_data = {
        "schema_version": 1,
        "concepts": [
            {
                "slug": "test-concept",
                "label": "Test",
                "definition": "A test.",
                "status": "candidate",
                "match_mode": "keyword",
            }
        ],
    }
    projects_data = {
        "schema_version": 1,
        "projects": [
            {
                "id": "proj-a",
                "name": "Project A",
                "path": "ProjA",
                "family": "alpha",
                "has_living": True,
            },
            {
                "id": "proj-b",
                "name": "Project B",
                "path": "ProjB",
                "family": "beta",
                "has_living": False,
            },
        ],
    }
    overrides_data = {
        "schema_version": 1,
        "force_about": [],
        "block_about": [],
    }

    graph_dir = _make_graph_dir(tmp_path, concepts_data, projects_data, overrides_data)
    registry = load_registry(graph_dir)

    assert len(registry.concepts) == 1
    assert len(registry.projects) == 2
    assert registry.force_about == []
    assert registry.block_about == []

    # Verify defaults for optional list fields
    concept = registry.concepts[0]
    assert concept.slug == "test-concept"
    assert concept.aliases == []
    assert concept.positive_keywords == []
    assert concept.negative_keywords == []
    assert concept.required_any == []
    assert concept.relates == []
    assert concept.project_scope is None
    assert concept.parent is None
    assert concept.effective_status is None


# ---------------------------------------------------------------------------
# Test 2: schema_version mismatch
# ---------------------------------------------------------------------------


def test_schema_version_mismatch(tmp_path: Path) -> None:
    """A concepts.yaml with schema_version=2 triggers SystemExit(1)."""
    concepts_data = {
        "schema_version": 2,
        "concepts": [],
    }
    projects_data = {
        "schema_version": 1,
        "projects": [],
    }
    overrides_data = {
        "schema_version": 1,
        "force_about": [],
        "block_about": [],
    }

    graph_dir = _make_graph_dir(tmp_path, concepts_data, projects_data, overrides_data)

    with pytest.raises(SystemExit) as exc_info:
        load_registry(graph_dir)

    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Test 3: bad slug
# ---------------------------------------------------------------------------


def test_bad_slug(tmp_path: Path) -> None:
    """A concept with an invalid slug raises ValueError with 'invalid slug'."""
    concepts_data = {
        "schema_version": 1,
        "concepts": [
            {
                "slug": "Bad_Slug",
                "label": "Bad",
                "definition": "Invalid slug.",
                "status": "candidate",
                "match_mode": "keyword",
            }
        ],
    }
    projects_data = {
        "schema_version": 1,
        "projects": [],
    }
    overrides_data = {
        "schema_version": 1,
        "force_about": [],
        "block_about": [],
    }

    graph_dir = _make_graph_dir(tmp_path, concepts_data, projects_data, overrides_data)

    with pytest.raises(ValueError, match="invalid slug"):
        load_registry(graph_dir)


# ---------------------------------------------------------------------------
# Test 4: duplicate resolved paths
# ---------------------------------------------------------------------------


def test_duplicate_resolved_paths(tmp_path: Path) -> None:
    """Two projects with identical path strings raise ValueError about duplicate resolved path."""
    concepts_data = {
        "schema_version": 1,
        "concepts": [],
    }
    projects_data = {
        "schema_version": 1,
        "projects": [
            {
                "id": "proj-x",
                "name": "Project X",
                "path": "SameProject",
                "family": "alpha",
                "has_living": True,
            },
            {
                "id": "proj-y",
                "name": "Project Y",
                "path": "SameProject",
                "family": "alpha",
                "has_living": False,
            },
        ],
    }
    overrides_data = {
        "schema_version": 1,
        "force_about": [],
        "block_about": [],
    }

    graph_dir = _make_graph_dir(tmp_path, concepts_data, projects_data, overrides_data)

    with pytest.raises(ValueError, match="duplicate resolved path"):
        load_registry(graph_dir)
