"""Regressions for the second P1-only Codex pass (PR #154): scope-root
spelling and edits through links."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import test_lineage_index as tli  # noqa: E402
import test_provenance_review_r2_index as r2  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium" / "io").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "out").mkdir()
    return root


@pytest.mark.parametrize("spelling", ["./data", "data/.", "data//", "./data/"])
def test_a_scope_root_has_one_spelling(project: Path, spelling: str) -> None:
    (project / "data" / "result.csv").write_text("r")
    (project / ".mycelium" / "io" / "scope").write_text(f"root {spelling}\n")
    _, roots = io._scope_overrides(project)
    assert roots == ["data"]
    snap = io.walk(project)
    assert [p for p in snap.entries if p.endswith("result.csv")] == ["data/result.csv"]


def test_an_edit_through_a_link_makes_its_referent_uncertain(project: Path) -> None:
    (project / "data" / "v1.csv").write_text("A")
    os.symlink("v1.csv", project / "data" / "current.csv")
    written, deleted, refused = io._edit_changes(
        project, [(str(project / "data" / "current.csv"), "written")])
    assert written == [] and deleted == []
    assert {"path": "data/v1.csv", "reason": "edit-via-link"} in refused

    first = r2.journal(project, "c1")
    edit = r2.journal(project, "e1", tool="Edit")
    via_link = r2.observed(project, "e1", edit, edited=[])
    via_link["changes"]["edited"] = []
    via_link["changes"]["refused"] = refused
    tli.write_store(project, [
        r2.observed(project, "c1", first, created=[("data/v1.csv", sha("A"))]), via_link])
    write = li.query_hash(li.load(project), sha("A"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_an_edit_whose_target_cannot_be_established_clouds_the_checkout(
        project: Path) -> None:
    index_gaps: list = []
    fake = li.Index.__new__(li.Index)
    fake.gaps = index_gaps
    fake._refusal_gaps({"refused": [{"path": None, "reason": "edit-target-unknown"}]},
                       {"call": "s1|claude:e1"}, any_reason=False)
    assert index_gaps and index_gaps[0]["scope"] == "repo"
