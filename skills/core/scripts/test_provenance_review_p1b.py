"""Regressions for the first P1-only Codex pass (PR #154): the index
cache, background launches without an observation, unreadable checkouts,
and FIFO script paths."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import test_lineage_index as tli  # noqa: E402
import test_provenance_review_r2_index as r2  # noqa: E402

SECRET = "TEST_SECRET_123"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


# -- index cache -------------------------------------------------------------


def test_the_cache_never_copies_command_text(project: Path) -> None:
    record = tli.observation(project, "c1", created=[("out/x", sha("A"))], pre=1, post=2)
    record["bash_cmd"] = f"curl -H 'Authorization: Bearer {SECRET}' https://x"
    record["input_proofs"] = [{"path": "data/in.csv", "note": SECRET}]
    tli.write_store(project, [record])
    li.load(project)
    cache = li.cache_file(project, project / ".mycelium")
    assert cache.exists() and SECRET not in cache.read_text()


def test_an_old_cache_is_rebuilt(project: Path) -> None:
    assert li.BUILDER_VERSION >= 3


# -- background launch -------------------------------------------------------


def test_a_launched_background_job_without_an_observation_stays_a_gap(project: Path) -> None:
    state = project / ".mycelium"
    first = r2.journal(project, "c1")
    tli.write_store(project, [r2.observed(project, "c1", first,
                                          created=[("out/x", sha("A"))])])
    io.journal_event(state, "pre", "bg", host="claude", session="s1", tool="Bash",
                     background=True)
    io.journal_event(state, "launch", "bg", host="claude", session="s1", tool="Bash",
                     background=True, observe=True)  # its observation append failed
    assert r2.verdict(project, "A") == "unknown"


# -- unreadable checkout -----------------------------------------------------


def test_an_unreadable_root_claims_no_deletions(project: Path, monkeypatch) -> None:
    state = project / ".mycelium"
    (project / "out" / "x").write_text("A")
    io.pre(state, host="claude", session="s1", call_id="c1", tool="Bash",
           repo_root=project, snapshot=True)
    real_scandir = os.scandir
    root = os.path.realpath(project)

    def failing(path="."):
        if os.path.realpath(path) == root:
            raise PermissionError("listing denied")
        return real_scandir(path)

    monkeypatch.setattr(io.os, "scandir", failing)
    observation = io.post(state, host="claude", session="s1", call_id="c1", tool="Bash",
                          repo_root=project, outcome="success", bash_exit=0)
    assert observation["status"] == "unavailable"
    assert observation["reason"] == "post-root-unreadable"


# -- Stop never blocks on a FIFO ---------------------------------------------


def test_a_fifo_script_path_does_not_hang_consolidation(tmp_path: Path) -> None:
    fifo = tmp_path / "fit.py"
    os.mkfifo(fifo)
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "python fit.py", "script": str(fifo),
           "script_sha256": None}
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(
        event=stop.normalize_event(raw, [], 0)), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "normalize_event blocked on a FIFO"
    assert result["event"]["script_sha256"] is None


def test_a_recomputed_script_digest_is_the_file_digest(tmp_path: Path) -> None:
    script = tmp_path / "fit.py"
    script.write_text(f'token = "{SECRET}"\n')
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "python fit.py", "script": str(script),
           "script_sha256": None}
    event = stop.normalize_event(raw, [], 0)
    assert "script_sha256_of" not in event
    assert event["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
