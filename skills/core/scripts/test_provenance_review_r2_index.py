"""Regressions for the second Codex review round (PR #154): legacy source,
the index's gap accounting, and the observer's scope handling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import test_lineage_index as tli  # noqa: E402

SECRET = "s3cret-value"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_a_legacy_events_source_is_never_committed() -> None:
    body = f'password = "{SECRET}"\nimport pandas\n'
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "python fit.py", "script": None,
           "script_source": body, "script_sha256": hashlib.sha256(body.encode()).hexdigest()}
    assert SECRET not in json.dumps(stop.commit_event(stop.normalize_event(raw, [], 0)))


# -- index -------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def journal(project: Path, call: str, *, tool: str = "Bash", post: bool = True,
            observe: bool = True) -> dict:
    """Journal one call; return its bound as an observation records it."""
    state = project / ".mycelium"
    meta: dict = {}
    pre_seq, _, _ = io.journal_event(state, "pre", call, host="claude", session="s1",
                                     tool=tool, meta=meta)
    post_seq = None
    if post:
        post_seq, _, _ = io.journal_event(state, "post", call, host="claude", session="s1",
                                          tool=tool, observe=observe)
    return {"id": meta["journal_id"], "pre": pre_seq, "post": post_seq}


def observed(project: Path, call: str, bound: dict, **kw) -> dict:
    return tli.observation(project, call, pre=bound["pre"], post=bound["post"],
                           jid=bound["id"], **kw)


def verdict(project: Path, content: str) -> str:
    (project / "out" / "x").write_text(content)
    return li.query_hash(li.load(project), sha(content))["paths"][0]["writes"][0]["status"]


def test_a_closed_call_with_no_observation_clouds_earlier_writes(project: Path) -> None:
    first = journal(project, "c1")
    journal(project, "c2")  # its observation append failed
    tli.write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    assert verdict(project, "A") == "unknown"


def test_a_write_after_the_missing_observation_is_current(project: Path) -> None:
    journal(project, "c1")
    later = journal(project, "c2")
    tli.write_store(project, [observed(project, "c2", later, created=[("out/x", sha("B"))])])
    journal_only = journal(project, "c0")  # noqa: F841 - a later call, observed below
    tli.write_store(project, [observed(project, "c0", journal_only)])
    # c1 has no observation, but it ended before c2 started.
    assert verdict(project, "B") == "current"


def test_an_edit_that_owed_an_observation_is_a_gap(project: Path) -> None:
    first = journal(project, "c1")
    journal(project, "e1", tool="Edit", observe=True)
    tli.write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    assert verdict(project, "A") == "unknown"


def test_a_closed_edit_with_no_observation_is_unaccounted(project: Path) -> None:
    # Positive proof: an edit whose targets are unknown (nothing to observe)
    # still ran after the write, so it is not accounted for.
    first = journal(project, "c1")
    journal(project, "e1", tool="Edit", observe=False)
    tli.write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    write = li.query_hash(li.load(project), sha("A"), disk=False)["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"
    assert "coverage-unproven" in write["record"]["reasons"]


def test_an_age_expired_call_stays_unbounded(project: Path) -> None:
    state = project / ".mycelium"
    old = time.time_ns() - (io.OPEN_INTERVAL_MAX_SECONDS + 60) * 1_000_000_000
    io.journal_event(state, "pre", "stuck", host="claude", session="s1", tool="Bash",
                     now_ns=old)
    later = journal(project, "c2")  # its journal_event expires "stuck"
    tli.write_store(project, [observed(project, "c2", later, created=[("out/x", sha("B"))])])
    # The stuck call may still be running: c2's write is not provably last.
    assert verdict(project, "B") == "unknown"


def test_a_turn_end_bounds_the_unresolved_call(project: Path) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id="lost", tool="Bash",
           repo_root=project, snapshot=True)
    io.turn_end(state, host="claude", session="s1")
    later = journal(project, "c2")
    tli.write_store(project, [observed(project, "c2", later, created=[("out/x", sha("B"))])])
    tli.write_manifest(project, "m1", summary={"unresolved_calls": ["claude:lost"]})
    assert verdict(project, "B") == "current"


def test_a_skip_with_a_session_is_reconciled_only_by_that_session(project: Path) -> None:
    first = journal(project, "c1")
    tli.write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    tli.write_manifest(project, "m1", skipped=[
        {"skip_reason": "detector-failed", "observation_id": "claude:c1",
         "host_session_id": "s2"}])
    assert verdict(project, "A") == "unknown"


def test_a_foreign_manifest_summary_clouds_nothing(project: Path) -> None:
    first = journal(project, "c1")
    tli.write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    theirs = tli.write_manifest(project, "theirs", repo_root="/home/someone/proj",
                                summary={"observations_missing": ["claude:zz"],
                                         "unresolved_calls": ["claude:yy"]})
    assert theirs.exists()
    assert verdict(project, "A") == "current"


def test_an_unconfirmed_edit_credits_no_write(project: Path) -> None:
    first = journal(project, "c1")
    edit = journal(project, "e1", tool="Edit")
    unconfirmed = observed(project, "e1", edit, edited=[("out/x", sha("A"))])
    unconfirmed["outcome"] = "unknown"
    tli.write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))]),
                              unconfirmed])
    result = li.query_hash(li.load(project), sha("A"), disk=False)
    writes = result["paths"][0]["writes"]
    assert all(w["call"] != "s1|claude:e1" for w in writes)
    assert writes[0]["status"] == "unknown"


# -- observer ----------------------------------------------------------------


def test_a_directory_excluded_only_at_pre_is_a_coverage_gap(project: Path) -> None:
    state = project / ".mycelium"
    (project / "data").mkdir()
    (project / "data" / "x.csv").write_text("x")
    scope = state / "io" / "scope"
    scope.parent.mkdir(parents=True, exist_ok=True)
    scope.write_text("exclude data\n")
    io.pre(state, host="claude", session="s1", call_id="rm", tool="Bash",
           repo_root=project, snapshot=True)
    shutil.rmtree(project / "data")
    scope.write_text("")
    observation = io.post(state, host="claude", session="s1", call_id="rm", tool="Bash",
                          repo_root=project, outcome="success", bash_exit=0)
    assert any(u.get("path") == "data" for u in observation["scope"]["unwatched"])


def test_scope_checks_are_not_quadratic() -> None:
    unwatched = [{"path": f"ex{i}/sub", "why": "scope-exclude"} for i in range(3000)]
    prefixes = io.unwatched_prefixes(unwatched)
    paths = [f"keep{i}/a/b/file.csv" for i in range(3000)]
    started = time.perf_counter()
    statuses = [io.watch_status(p, prefixes) for p in paths]
    assert time.perf_counter() - started < 0.2
    assert set(statuses) == {"watched"}
    assert io.watch_status("ex7/sub/f.csv", prefixes) == "unwatched"
    assert io.watch_status("ex7/subway.csv", unwatched) == "watched"
