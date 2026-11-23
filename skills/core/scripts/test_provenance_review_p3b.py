"""Regressions for the third P1-only Codex pass (PR #154): a bounded scan of
long command text, filename spelling on case-insensitive volumes, and
unlistable record directories."""

from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import lineage_redact as redact  # noqa: E402
import test_lineage_index as tli  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_sql_placeholders_do_not_stall_the_allowlist() -> None:
    sql = "INSERT INTO t VALUES " + ",".join(["(?,?,?)"] * 24000)
    command = f"python3 -c \"c.execute('{sql}')\""
    started = time.perf_counter()
    redact.commit_safe_command(command, set())
    assert time.perf_counter() - started < 1.0


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def _case_insensitive(path: Path) -> bool:
    probe = path / "CaseProbe"
    probe.write_text("x")
    try:
        return (path / "caseprobe").exists()
    finally:
        probe.unlink()


def test_an_edit_is_keyed_by_the_listed_spelling(project: Path) -> None:
    if not _case_insensitive(project):
        pytest.skip("case-sensitive volume")
    (project / "CHANGELOG.md").write_text("B")
    written, _, refused = io._edit_changes(project, [(str(project / "changelog.md"),
                                                     "written")])
    assert [w["path"] for w in written] == ["CHANGELOG.md"] and refused == []


def test_canonical_spelling_keeps_new_paths_and_existing_names(project: Path) -> None:
    (project / "out" / "x.csv").write_text("x")
    assert io.canonical_spelling(str(project), "out/x.csv") == "out/x.csv"
    assert io.canonical_spelling(str(project), "out/new/y.csv") == "out/new/y.csv"


def test_an_unlistable_store_directory_is_a_gap(project: Path, monkeypatch) -> None:
    (project / "out" / "x").write_text("A")
    first = tli.observation(project, "c1", created=[("out/x", sha("A"))], pre=1, post=2)
    tli.write_manifest(project, "m1", observations=[first])
    obs = project / ".mycelium" / "io" / "obs"
    (obs / "claude").mkdir(parents=True)
    real_scandir = os.scandir

    def failing(path="."):
        if str(path).endswith(os.path.join("obs", "claude")):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(li.os, "scandir", failing)
    result = li.query_hash(li.load(project, write=False), sha("A"))
    write = result["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert result["coverage"]["gaps"].get("source-unreadable") == 1
