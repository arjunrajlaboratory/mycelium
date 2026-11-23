"""Regressions for the fourth P1-only Codex pass (PR #154): flags that take
no value, scope roots spelled in another case, and deletions on
case-insensitive volumes."""

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
import lineage_redact as redact  # noqa: E402
import test_lineage_index as tli  # noqa: E402
import test_provenance_review_r2_index as r2  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("flag", ["--ask-password", "--password-stdin", "--no-password"])
def test_flags_that_take_no_value_mask_nothing(flag: str) -> None:
    assert not redact.takes_secret_value(flag)
    assert redact.takes_secret_value("--password")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium" / "io").mkdir(parents=True)
    (root / "skills" / "core").mkdir(parents=True)
    return root


def test_a_scope_root_in_another_case_walks_the_listed_spelling(project: Path) -> None:
    (project / "skills" / "core" / "a.txt").write_text("a")
    if not io._case_insensitive(str(project.resolve())):
        pytest.skip("case-sensitive volume")
    (project / ".mycelium" / "io" / "scope").write_text("root SKILLS/core\n")
    entries = io.walk(project).entries
    assert "skills/core/a.txt" in entries
    assert not any(p.startswith("SKILLS/") for p in entries)


def test_a_scope_root_through_a_link_is_not_walked(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "b.txt").write_text("b")
    os.symlink(outside, project / "linked")
    (project / ".mycelium" / "io" / "scope").write_text("root linked\n")
    assert not any(p.startswith("linked/") for p in io.walk(project).entries)


def test_a_deletion_in_another_case_clouds_the_recorded_write(project: Path) -> None:
    root = str(project.resolve())
    if not io._case_insensitive(root):
        pytest.skip("case-sensitive volume")
    _, deleted, refused = io._edit_changes(
        project, [(str(project / "results-qc-table.csv"), "deleted")])
    assert deleted == [{"path": "results-qc-table.csv", "after": None}]
    assert any(r.get("casefold") for r in refused)

    first = r2.journal(project, "c1")
    edit = r2.journal(project, "e1", tool="Edit")
    deletion = r2.observed(project, "e1", edit)
    deletion["status"], deletion["outcome"] = "edit", "success"
    deletion["changes"] = {"edited": [], "deleted": deleted, "refused": refused}
    tli.write_store(project, [
        r2.observed(project, "c1", first, created=[("results-qc-table.CSV", sha("A"))]),
        deletion])
    write = li.query_path(li.load(project), "results-qc-table.CSV", disk=False)
    verdicts = [w["record"]["status"] for w in write["writes"]]
    assert "current-in-record" not in verdicts
