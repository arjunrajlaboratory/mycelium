"""Positive proof for `current` (PR #154, structural fix 2).

A write is `current-in-record` only when everything since it is accounted
for: the same journal, nothing compacted away, and every later call
covered at the path by a complete observation. Anything else is `unknown`,
including failure modes nobody listed. A disk query may still prove the
write current from the file itself: an identity (dev, inode, size, mtime,
ctime) unchanged since the write means nothing has touched it."""

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


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def bash(project: Path, call: str, action=None, *, background: bool = False) -> dict:
    """One real Bash call through the observer's Pre and Post."""
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id=call, tool="Bash",
           repo_root=project, snapshot=True, background=background)
    if action:
        action()
    return io.post(state, host="claude", session="s1", call_id=call, tool="Bash",
                   repo_root=project, outcome="success", bash_exit=0, background=background)


def status(project: Path, content: str, *, disk: bool = True) -> dict:
    result = li.query_hash(li.load(project, write=False), sha(content), disk=disk)
    return result["paths"][0]["writes"][0]


def write_x(project: Path, content: str):
    return lambda: (project / "out" / "x").write_text(content)


def test_a_fully_observed_history_is_current(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    bash(project, "c2", lambda: (project / "out" / "y").write_text("other"))
    assert status(project, "A", disk=False)["record"]["status"] == "current-in-record"
    assert status(project, "A")["status"] == "current"


def test_a_lost_journal_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    io.journal_path(project / ".mycelium").unlink()
    io.journal_event(project / ".mycelium", "pre", "later", host="claude", session="s1",
                     tool="Bash")
    record = status(project, "A", disk=False)["record"]
    assert record["status"] == "unknown"
    assert record["unaccounted"][0]["kind"] == "journal-discontinuous"


def test_a_write_from_before_compaction_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    journal = io.journal_path(project / ".mycelium")
    records = io._journal_records(journal)
    jid = records[-1]["jid"]
    floor = max(r["seq"] for r in records) + 1
    with journal.open("a") as handle:
        handle.write(f'{{"seq": 0, "ev": "floor", "call": "", "floor": {floor}, '
                     f'"jid": "{jid}"}}\n')
    record = status(project, "A", disk=False)["record"]
    assert record["unaccounted"][0]["kind"] == "journal-compacted"


def test_a_later_background_job_is_unaccounted_but_identity_proves_the_file(
        project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    time.sleep(0.01)
    # A host-backgrounded command after the write (the live-run case): what
    # it did after launch was never observed.
    launch(project, "bg", "task-1")
    entry = status(project, "A")
    assert entry["record"]["status"] == "unknown"
    assert "coverage-unproven" in entry["record"]["reasons"]
    # While the job may still run, identity is not trusted (a live mapping
    # can change a file without moving its timestamps).
    assert entry["status"] == "unknown"
    # Once the host shows it ended, the file's unchanged identity proves it.
    io.end_finished_background(project / ".mycelium", host="claude", session="s1", tasks=[])
    entry = status(project, "A")
    assert entry["record"]["status"] == "unknown"
    assert entry["status"] == "current"
    assert entry["basis"] == "identity-unchanged-since-write"


def test_a_jobs_end_outlives_journal_compaction(project: Path) -> None:
    launch(project, "bg", "task-1")
    io.end_finished_background(project / ".mycelium", host="claude", session="s1", tasks=[])
    journal = io.journal_path(project / ".mycelium")
    kept = [r for r in io._journal_records(journal) if r.get("call") != "bg"]
    journal.write_text("".join(__import__("json").dumps(r) + "\n" for r in kept))
    index = li.load(project, write=False)
    (gap,) = [g for g in index.gaps if g.get("kind") == "background-job"]
    assert isinstance(gap["journal"]["post_seq"], int)


def test_a_subagent_job_absent_from_the_snapshot_stays_open(project: Path) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id="sub", tool="Bash", agent="agent-7",
           repo_root=project, snapshot=True, background=True)
    io.post(state, host="claude", session="s1", call_id="sub", tool="Bash", agent="agent-7",
            repo_root=project, outcome="success", bash_exit=0, background=True,
            task_id="t-sub")
    assert io.end_finished_background(state, host="claude", session="s1", tasks=[]) == 0
    assert io.end_finished_background(
        state, host="claude", session="s1",
        tasks=[{"id": "t-sub", "status": "completed"}]) == 1


def test_an_overlapping_restore_is_unaccounted(project: Path) -> None:
    state = project / ".mycelium"
    target = project / "out" / "x"
    target.write_text("old")
    # B starts first and sees `old`; A writes `new` and finishes; B restores
    # `old` and finishes. B's endpoints both show `old`.
    io.pre(state, host="claude", session="s1", call_id="B", tool="Bash",
           repo_root=project, snapshot=True)
    bash(project, "A", lambda: target.write_text("new"))
    time.sleep(0.01)
    target.write_text("old")
    io.post(state, host="claude", session="s1", call_id="B", tool="Bash",
            repo_root=project, outcome="success", bash_exit=0)
    record = status(project, "new", disk=False)["record"]
    assert record["status"] != "current-in-record"


def test_an_unparseable_journal_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    journal = io.journal_path(project / ".mycelium")
    with journal.open("a") as handle:
        handle.write('{"seq": 99, "ev": "pre", "call": "z", "host": "claude", '
                     '"session": "s1", "wall_ns": "not-a-number"}\n')
    record = status(project, "A", disk=False)["record"]
    assert record["status"] == "unknown"


def test_a_touched_file_is_not_rescued_even_with_the_same_bytes(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    bash(project, "bg", None, background=True)
    time.sleep(0.01)
    # A -> B -> A outside every observation: the bytes match, the identity
    # does not (ctime moved), so nothing is certified.
    target = project / "out" / "x"
    target.write_text("B")
    target.write_text("A")
    entry = status(project, "A")
    assert entry["status"] == "unknown"
    assert "basis" not in entry


def test_whole_second_timestamps_are_never_used_as_proof(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    index = li.load(project, write=False)
    (write,) = [w for w in index.writes if w["path"] == "out/x"]
    ident = dict(write["identity"])
    ident["mtime_ns"] = ident["ctime_ns"] = 1_700_000_000_000_000_000
    assert not index._identity_unchanged({**write, "identity": ident})


def launch(project: Path, call: str, task: str | None) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id=call, tool="Bash",
           repo_root=project, snapshot=True, background=True)
    io.post(state, host="claude", session="s1", call_id=call, tool="Bash",
            repo_root=project, outcome="success", bash_exit=0, background=True,
            task_id=task)


def test_a_finished_task_bounds_its_job(project: Path) -> None:
    launch(project, "bg", "bjjs0w6bq")
    state = project / ".mycelium"
    assert io.end_finished_background(state, host="claude", session="s1",
                                      tasks=[{"id": "bjjs0w6bq", "status": "running"}]) == 0
    assert io.end_finished_background(state, host="claude", session="s1", tasks=[]) == 1
    bash(project, "c2", write_x(project, "A"))
    # Written after the job ended: provable from the record alone.
    assert status(project, "A", disk=False)["record"]["status"] == "current-in-record"


@pytest.mark.parametrize("tasks", [
    [{"id": "bjjs0w6bq", "status": "running"}],
    [{"id": "bjjs0w6bq", "status": "something-new"}],
])
def test_a_running_or_unreadable_task_stays_open(project: Path, tasks) -> None:
    launch(project, "bg", "bjjs0w6bq")
    io.end_finished_background(project / ".mycelium", host="claude", session="s1",
                               tasks=tasks)
    bash(project, "c2", write_x(project, "A"))
    assert status(project, "A", disk=False)["record"]["status"] == "unknown"


def test_a_job_without_a_task_id_stays_open(project: Path) -> None:
    launch(project, "bg", None)
    io.end_finished_background(project / ".mycelium", host="claude", session="s1",
                               tasks=[])
    bash(project, "c2", write_x(project, "A"))
    assert status(project, "A", disk=False)["record"]["status"] == "unknown"


def test_the_stop_payload_reaches_the_journal(project: Path, monkeypatch) -> None:
    import io as stdio
    import json

    launch(project, "bg", "t-1")
    monkeypatch.setattr(sys, "stdin", stdio.StringIO(json.dumps({"background_tasks": []})))
    assert io.main(["turn-end", "--state-dir", str(project / ".mycelium"), "--host",
                    "claude", "--session", "s1", "--stop-payload-stdin"]) == 0
    records = io._journal_records(io.journal_path(project / ".mycelium"))
    assert any(r.get("ev") == "end" and r.get("task") == "t-1" for r in records)


def test_every_new_observation_records_identity(project: Path) -> None:
    observation = bash(project, "c1", write_x(project, "A"))
    (created,) = observation["changes"]["created"]
    assert set(created["after"]["identity"]) == {"dev", "ino", "size", "mtime_ns", "ctime_ns"}
    assert created["after"]["identity"]["ino"] == os.lstat(project / "out" / "x").st_ino
