"""Observed I/O end to end: one Pre and one Post around a real file change."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import io_observer as io  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "raw").mkdir(parents=True)
    (root / "data" / "raw" / "a.csv").write_text("a\n1\n")
    (root / "analysis").mkdir()
    (root / "analysis" / "fit.py").write_text("print(1)\n")
    return root


def _state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return state


def _call(state: Path, root: Path, call: str, action, *, background_pre: bool = False,
          **post_kw):
    io.pre(state, host="claude", session="s1", call_id=call, tool="Bash",
           repo_root=root, snapshot=True, background=background_pre)
    action()
    return io.post(state, host="claude", session="s1", call_id=call, tool="Bash",
                   repo_root=root, **post_kw)


def _paths(entries: list[dict]) -> list[str]:
    return [e["path"] for e in entries]


def _age(path: Path, seconds: float = 120) -> None:
    """Make a file look settled (written well before the call)."""
    old = time.time() - seconds
    os.utime(path, (old, old))
    time.sleep(0.03)  # let ctime (set by utime) leave the racy window


def test_output_written_by_the_call_is_observed_with_hashes(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    obs = _call(state, root, "c1", lambda: (root / "out.csv").write_text("x\n"),
                outcome="success", bash_exit=0, bash_cmd="awk 1 data/raw/a.csv > out.csv")
    assert obs["status"] == "no-known-overlap"
    (created,) = obs["changes"]["created"]
    assert created["path"] == "out.csv"
    assert created["before"] is None
    assert created["after"]["sha256"] and created["after"]["size_bytes"] == 2
    assert obs["changes"]["counts"] == {"created": 1, "modified": 0, "deleted": 0}
    stored = io._read_lines(io.observations_path(state, "claude", "s1"))
    assert [r["observation_id"] for r in stored] == ["claude:c1"]
    assert not io.snapshot_path(state, "claude", "s1", "c1").exists()


def test_edit_between_calls_is_not_attributed_to_the_next_call(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _call(state, root, "c1", lambda: None)
    (root / "analysis" / "fit.py").write_text("print(2)\n")  # an Edit tool, between calls
    obs = _call(state, root, "c2", lambda: (root / "out.csv").write_text("y"))
    assert _paths(obs["changes"]["created"]) == ["out.csv"]
    assert obs["changes"]["modified"] == []


def test_modified_file_before_hash_is_cached_content_or_not_captured(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    target = root / "data" / "raw" / "a.csv"
    _age(target)
    cache: dict[str, str] = {}
    first, _ = io.stable_hash(target, None, deadline=time.monotonic() + 9, cache={},
                              new_cache=cache)
    io.flush_cache(state, cache)
    obs = _call(state, root, "c1", lambda: target.write_text("v2 longer\n"))
    (entry,) = obs["changes"]["modified"]
    assert entry["before"] == {"sha256": first.sha256, "size_bytes": 4, "hash_status": "cached"}
    assert entry["after"]["sha256"] != first.sha256


def test_modified_file_without_cache_says_not_captured(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    target = root / "data" / "raw" / "a.csv"
    obs = _call(state, root, "c1", lambda: target.write_text("v2 longer\n"))
    (entry,) = obs["changes"]["modified"]
    assert entry["before"]["sha256"] is None
    assert entry["before"]["hash_status"] == "not-captured"


def test_chmod_only_change_is_metadata_not_modification(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    target = root / "analysis" / "fit.py"
    _age(target)
    cache: dict[str, str] = {}
    io.stable_hash(target, None, deadline=time.monotonic() + 9, cache={}, new_cache=cache)
    io.flush_cache(state, cache)
    obs = _call(state, root, "c1", lambda: os.chmod(target, 0o755))
    assert obs["changes"]["modified"] == []
    assert obs["changes"]["metadata_only"] == ["analysis/fit.py"]


def test_rename_is_a_candidate_and_both_sides_are_listed(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "raw" / "a.csv"
    obs = _call(state, root, "c1", lambda: os.rename(src, root / "data" / "b.csv"))
    assert obs["changes"]["rename_candidates"] == [
        {"from": "data/raw/a.csv", "to": "data/b.csv"}
    ]
    assert _paths(obs["changes"]["deleted"]) == ["data/raw/a.csv"]
    assert _paths(obs["changes"]["created"]) == ["data/b.csv"]


def test_unchanged_settled_input_is_proven_pre_run(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "raw" / "a.csv"
    _age(src)
    obs = _call(state, root, "c1", lambda: (root / "o.csv").write_text("z"),
                inputs=[str(src)])
    (proof,) = obs["input_proofs"]
    assert proof["pre_run_proven"] is True
    assert proof["sha256"]


def test_input_rewritten_during_the_call_is_not_proven(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "raw" / "a.csv"
    _age(src)
    obs = _call(state, root, "c1", lambda: src.write_text("rewritten\n"), inputs=[str(src)])
    (proof,) = obs["input_proofs"]
    assert proof["pre_run_proven"] is False
    assert proof["reason"] == "changed-during-call"


def test_input_written_just_before_pre_is_racy_not_proven(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "raw" / "fresh.csv"
    src.write_text("just now\n")
    obs = _call(state, root, "c1", lambda: time.sleep(0.05), inputs=[str(src)])
    (proof,) = obs["input_proofs"]
    assert proof["pre_run_proven"] is False
    assert proof["reason"] == "racy-timestamp"


def test_input_outside_the_repository_is_outside_scope(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    outside = tmp_path / "ext.csv"
    outside.write_text("e")
    obs = _call(state, root, "c1", lambda: None, inputs=[str(outside)])
    assert obs["input_proofs"][0]["reason"] == "outside-scope"


def test_input_under_symlinked_data_directory_is_not_proven(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    vol = tmp_path / "vol"
    vol.mkdir()
    (vol / "x.bam").write_text("b")
    os.symlink(vol, root / "data" / "linked")
    obs = _call(state, root, "c1", lambda: None,
                inputs=[str(root / "data" / "linked" / "x.bam")])
    # Reached through a symlinked directory: never proven.
    assert obs["input_proofs"][0]["reason"] == "symlinked-path"
    assert {"path": "data/linked", "why": "symlink", "target": str(vol)} in obs["scope"]["unwatched"]


def test_missing_pre_snapshot_is_unavailable_not_empty(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    obs = io.post(state, host="claude", session="s1", call_id="nopre", tool="Bash",
                  repo_root=root)
    assert obs["status"] == "unavailable"
    assert obs["reason"] == "no-pre-snapshot"
    assert obs["changes"] is None


def test_overlapping_edit_marks_the_observation(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    io.pre(state, host="claude", session="s1", call_id="A", tool="Bash", repo_root=root,
           snapshot=True)
    io.pre(state, host="claude", session="s1", call_id="E", tool="Edit")
    (root / "out.csv").write_text("x")
    io.post(state, host="claude", session="s1", call_id="E", tool="Edit")
    obs = io.post(state, host="claude", session="s1", call_id="A", tool="Bash", repo_root=root)
    assert obs["status"] == "overlap"
    assert [o["call"] for o in obs["overlapping"]] == ["E"]


def test_edit_tool_post_returns_no_observation(tmp_path: Path) -> None:
    state = _state(tmp_path)
    io.pre(state, host="claude", session="s1", call_id="E", tool="Edit")
    assert io.post(state, host="claude", session="s1", call_id="E", tool="Edit") is None


def test_background_call_is_marked_background(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    obs = _call(state, root, "bg", lambda: None, background_pre=True)
    assert obs["status"] == "background"


def test_huge_change_set_is_capped_with_exact_counts(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)

    def install() -> None:
        (root / "site").mkdir()
        for index in range(30):
            (root / "site" / f"m{index}.py").write_text("x")

    obs = _call(state, root, "c1", install, change_cap=10)
    assert obs["changes"]["capped"] is True
    assert obs["changes"]["counts"]["created"] == 30
    assert len(obs["changes"]["created"]) == 10
    assert obs["changes"]["top_level"] == {"site": 30}


def test_journal_lock_timeout_is_recorded_not_waited_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    io.pre(state, host="claude", session="s1", call_id="c1", tool="Bash", repo_root=root,
           snapshot=True)
    monkeypatch.setattr(io, "LOCK_WAIT_SECONDS", 0.05)
    with open(f"{io.journal_path(state)}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        started = time.monotonic()
        obs = io.post(state, host="claude", session="s1", call_id="c1", tool="Bash",
                      repo_root=root)
        assert time.monotonic() - started < 2
    assert obs["status"] == "unavailable" and obs["reason"] == "lock-timeout"


def test_stale_snapshot_becomes_a_recorded_gap(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    io.pre(state, host="codex", session="old", call_id="lost", tool="Bash", repo_root=root,
           snapshot=True)
    snap = io.snapshot_path(state, "codex", "old", "lost")
    ancient = time.time() - io.SNAPSHOT_MAX_AGE_SECONDS - 10
    os.utime(snap, (ancient, ancient))
    _call(state, root, "c1", lambda: None)
    assert not snap.exists()
    (gap,) = io._read_lines(io.observations_path(state, "codex", "old"))
    assert gap["observation_id"] == "codex:lost"
    assert gap["reason"] == "call-unresolved"


FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "testdata" / "claude-2.1.325-tool-hooks.json").read_text()
)


def _hook(state: Path, root: Path, payload: dict) -> None:
    assert io.hook(state, root, "claude", json.dumps(payload)) == 0


def test_hook_pre_from_real_claude_payload_snapshots_bash(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    payload = dict(FIXTURE["bash_pre"])
    _hook(state, root, payload)
    snap = io.snapshot_path(state, "claude", payload["session_id"], payload["tool_use_id"])
    assert snap.exists()
    (record,) = io._journal_records(io.journal_path(state))
    assert record["ev"] == "pre" and record["tool"] == "Bash" and not record["background"]


def test_hook_pre_for_write_journals_without_snapshot(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    payload = dict(FIXTURE["write_pre"])
    _hook(state, root, payload)
    assert not (state / "io" / "pre").exists()
    (record,) = io._journal_records(io.journal_path(state))
    assert record["tool"] == "Write"


def test_hook_background_payload_opens_a_background_interval(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    pre = dict(FIXTURE["bash_pre"])
    pre["tool_input"] = dict(FIXTURE["bash_post_background"]["tool_input"])
    pre["tool_use_id"] = FIXTURE["bash_post_background"]["tool_use_id"]
    _hook(state, root, pre)
    (record,) = io._journal_records(io.journal_path(state))
    assert record["background"] is True


@pytest.mark.parametrize(
    "session",
    [None, 7, True, {"a": 1}, ["x"], ".", "..", "a/b", "", "x" * 201],
)
def test_hook_refuses_invalid_session_identity(tmp_path: Path, session) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    payload = dict(FIXTURE["bash_pre"])
    if session is None:
        payload.pop("session_id")
    else:
        payload["session_id"] = session
    _hook(state, root, payload)
    assert not (state / "io").exists()


@pytest.mark.parametrize("raw", ["not json", "[]", "null", '{"tool_name": "Bash"}'])
def test_hook_ignores_malformed_payloads(tmp_path: Path, raw: str) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    assert io.hook(state, root, "claude", raw) == 0
    assert not (state / "io").exists()


def test_hook_ignores_other_tools(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    payload = dict(FIXTURE["bash_pre"], tool_name="Read")
    _hook(state, root, payload)
    assert not (state / "io").exists()


@pytest.mark.parametrize(
    "command",
    [
        "python train.py &",
        "nohup python train.py > log 2>&1",
        "cd analysis && nohup Rscript fit.R",
        "sbatch job.sh",
        "FOO=1 setsid ./run.sh",
        "tmux new -d -s x 'python a.py'",
        "python a.py & python b.py",
    ],
)
def test_background_detector_positives(command: str) -> None:
    assert io.starts_background_job(command)


@pytest.mark.parametrize(
    "command",
    [
        "python a.py && python b.py",
        "python a.py > out.log 2>&1",
        "echo 'nohup is a word' > notes.txt",
        'grep "a & b" data.csv',
        "ls data # run with & later",
        "cat file &> both.log",
        "echo sbatchless",
        "python a.py >&2",
    ],
)
def test_background_detector_negatives(command: str) -> None:
    assert not io.starts_background_job(command)


def test_background_post_keeps_the_interval_open(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _call(state, root, "bg", lambda: None, bash_cmd="python train.py &")
    later = _call(state, root, "c2", lambda: None)
    assert later["status"] == "overlap"
    (other,) = later["overlapping"]
    assert other["call"] == "bg" and other["open"] and other["background"]


def test_prove_inputs_against_the_retired_snapshot(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "raw" / "a.csv"
    _age(src)
    _call(state, root, "c1", lambda: None)
    done = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "c1"))
    assert done.exists()
    proofs = io.prove_inputs(state, done, root, [str(src)])
    assert proofs[str(src)]["pre_run_proven"] is True
    assert not done.exists()


def test_prove_inputs_detects_a_write_after_pre(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "raw" / "a.csv"
    _age(src)
    _call(state, root, "c1", lambda: src.write_text("changed\n"))
    done = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "c1"))
    proofs = io.prove_inputs(state, done, root, [str(src)])
    assert proofs[str(src)]["reason"] == "changed-during-call"


def test_prove_inputs_without_snapshot_says_so(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    proofs = io.prove_inputs(state, tmp_path / "missing.done", root, ["x.csv"])
    assert proofs == {"x.csv": {"pre_run_proven": False, "reason": "no-pre-snapshot"}}


def test_old_done_snapshot_is_swept_silently(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _call(state, root, "c1", lambda: None)
    done = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "c1"))
    ancient = time.time() - io.DONE_SNAPSHOT_MAX_AGE_SECONDS - 10
    os.utime(done, (ancient, ancient))
    io.sweep_stale_snapshots(state)
    assert not done.exists()
    records = io._read_lines(io.observations_path(state, "claude", "s1"))
    assert [r["reason"] for r in records] == [None]  # no gap for a published call


def test_cli_prints_one_summary_line(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    script = Path(io.__file__)
    common = ["--state-dir", str(state), "--host", "claude", "--session", "s1",
              "--call-id", "t1", "--tool", "Bash", "--repo-root", str(root)]
    subprocess.run([sys.executable, str(script), "pre", *common, "--snapshot"], check=True)
    (root / "made.csv").write_text("m")
    done = subprocess.run(
        [sys.executable, str(script), "post", *common, "--outcome", "success"],
        check=True, capture_output=True, text=True,
    )
    summary = json.loads(done.stdout)
    retired = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "t1"))
    assert summary == {
        "observation_id": "claude:t1",
        "host_session_id": "s1",
        "status": "no-known-overlap",
        "reason": None,
        "changed": True,
        "pre_snapshot": str(retired),
        "state_dir": str(state),
    }
