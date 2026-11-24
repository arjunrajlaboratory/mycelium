"""Regressions from the first code reviews of observed I/O (2026-11-21).

Each test reproduces a reviewer's failure scenario: a Claude subagent review
(R*) and a read-only Codex review (C*).
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_data_lineage as stop  # noqa: E402
import extract_data_lineage_event as lineage_event  # noqa: E402
import io_observer as io  # noqa: E402

pytestmark = pytest.mark.platform

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
EXTRACTOR = Path(__file__).resolve().parent / "extract_data_lineage_event.py"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    old = time.time() - 300
    os.utime(root / "data" / "in.csv", (old, old))
    time.sleep(0.03)
    return root


def _state(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    return state


def _pre(state, root, call, session="s1", **kw):
    io.pre(state, host="claude", session=session, call_id=call, tool="Bash",
           repo_root=root, snapshot=True, **kw)


def _post(state, root, call, session="s1", **kw):
    return io.post(state, host="claude", session=session, call_id=call, tool="Bash",
                   repo_root=root, **kw)


# ---------- R1 / C3: a Pre whose journal append fails stays visible ----------


def test_pre_journal_timeout_is_still_visible_to_other_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "Z")
    monkeypatch.setattr(io, "LOCK_WAIT_SECONDS", 0.05)
    with open(f"{io.journal_path(state)}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _pre(state, root, "X")  # its journal append times out
    (root / "x_output.csv").write_text("written by X")
    z = _post(state, root, "Z")
    assert z["status"] == "overlap"
    assert any(o["call"] == "X" and o.get("unjournaled") for o in z["overlapping"])
    x = _post(state, root, "X")
    assert x["status"] == "unavailable" and x["reason"] == "own-interval-unjournaled"


def test_unjournaled_call_stays_in_overlap_history_after_its_marker_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    (state / "io").mkdir()
    monkeypatch.setattr(io, "LOCK_WAIT_SECONDS", 0.05)
    with open(f"{io.journal_path(state)}.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _pre(state, root, "A")  # unjournaled
    _pre(state, root, "B")
    (root / "out.csv").write_text("A wrote this")
    _post(state, root, "A")  # backdates A and removes its marker
    b = _post(state, root, "B")
    assert b["status"] == "overlap"
    assert [o["call"] for o in b["overlapping"]] == ["A"]


# ---------- R2: an interval the journal gave up on ----------


def test_own_expired_interval_never_reports_no_known_overlap(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "X")
    later = time.time_ns() + (io.OPEN_INTERVAL_MAX_SECONDS + 5) * 1_000_000_000
    io.journal_event(state, "pre", "W", host="claude", session="s1", tool="Bash", now_ns=later)
    io.journal_event(state, "post", "W", host="claude", session="s1", tool="Bash", now_ns=later)
    x = _post(state, root, "X")
    # Either the expiry is still in the journal (reason own-interval-expired)
    # or compaction dropped it and the Post backdated X; never a clean claim.
    assert x["status"] == "overlap"
    assert x["reason"] == "own-interval-expired" or any(
        o["call"] == "W" for o in x["overlapping"])


# ---------- R3: unwatched outputs are not "unchanged" ----------


def _observation_for(tmp_path, root, state, make_output):
    _pre(state, root, "c1")
    make_output()
    return _post(state, root, "c1")


def _mark(root: Path, observation: dict, output: Path) -> str | None:
    record = {"observation_id": observation["observation_id"],
              "outputs": [{"path": str(output)}]}
    stop._mark_observed_changes([record], {observation["observation_id"]: observation}, str(root))
    return record["outputs"][0].get("observed_change")


def test_output_under_symlinked_results_is_unwatched(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    os.symlink(scratch, root / "results")
    obs = _observation_for(tmp_path, root, state, lambda: (scratch / "r.csv").write_text("r"))
    assert _mark(root, obs, root / "results" / "r.csv") == "unwatched"


def test_output_in_nested_repository_is_unwatched_and_recorded(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    (root / "tool" / ".git").mkdir(parents=True)
    obs = _observation_for(tmp_path, root, state,
                           lambda: (root / "tool" / "out.csv").write_text("o"))
    assert {"path": "tool", "why": "nested-repository"} in obs["scope"]["unwatched"]
    assert _mark(root, obs, root / "tool" / "out.csv") == "unwatched"


def test_watched_output_not_written_is_unchanged(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    obs = _observation_for(tmp_path, root, state, lambda: None)
    assert _mark(root, obs, root / "data" / "in.csv") == "unchanged"
    assert _mark(root, obs, tmp_path / "elsewhere.csv") == "outside-scope"


# ---------- R4: one huge file never blows the hash budget ----------


def test_huge_file_is_pending_without_being_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    big = tmp_path / "huge.bam"
    with open(big, "wb") as handle:
        handle.truncate(40 * 1024**3)  # sparse 40 GiB
    # Counted, not timed: a file the budget cannot finish is never opened
    # or read at all, however fast the read would have been abandoned.
    touched: list[str] = []
    real_open, real_read = os.open, os.read

    def counting_open(path, *args, **kwargs):
        if os.fspath(path) == os.fspath(big):
            touched.append("open")
        return real_open(path, *args, **kwargs)

    def counting_read(fd, n):
        touched.append("read")
        return real_read(fd, n)

    monkeypatch.setattr(io.os, "open", counting_open)
    monkeypatch.setattr(io.os, "read", counting_read)
    started = time.monotonic()
    result, ident = io.stable_hash(big, None, deadline=time.monotonic() + 0.5,
                                   cache={}, new_cache={})
    monkeypatch.undo()
    assert touched == []
    assert time.monotonic() - started < 60  # sanity only
    assert result.status == "pending"
    state = io._state(ident, result)
    assert state["identity"]["size"] == 40 * 1024**3


# ---------- R5: no retired snapshot left on the unowned path ----------


def test_check_path_discards_the_retired_snapshot(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "c1")
    _post(state, root, "c1")
    done = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "c1"))
    assert done.exists()
    summary = {"observation_id": "claude:c1", "status": "no-known-overlap",
               "changed": False, "pre_snapshot": str(done), "state_dir": str(state)}
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(root), "--ts", "2026-11-21T00:00:00Z",
         "--bash-cmd", "git status", "--check-execution", "--bash-exit", "0",
         "--observation-json", json.dumps(summary)],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert not done.exists()


# ---------- R6: a failure without an exit code is still a failure ----------


def test_tool_outcome_reaches_the_lineage_record(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    args = argparse.Namespace(ts="t", bash_cmd="./build", agent_id=None, agent_type=None,
                              bash_exit=None, outcome="failure",
                              observation={"status": "no-known-overlap", "changed": True,
                                           "observation_id": "claude:x",
                                           "host_session_id": "s1"})
    record = lineage_event.build_data_command_event(args, root)
    assert record["tool_outcome"] == "failure" and record["bash_exit"] is None
    normalized = stop.normalize_data_command(record)
    assert normalized["tool_outcome"] == "failure"


# ---------- R7: a racy identity on a coarse device is unknown ----------


def test_coarse_device_racy_paths_are_listed_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    (root / "data" / "fresh.csv").write_text("just written")
    real_walk = io.walk

    def coarse_walk(repo, **kw):
        snap = real_walk(repo, **kw)
        return snap._replace(coarse_devices=frozenset(i.dev for i in snap.entries.values()))

    monkeypatch.setattr(io, "walk", coarse_walk)
    obs = _observation_for(tmp_path, root, state, lambda: None)
    assert "data/fresh.csv" in obs["changes"]["racy_unknown"]
    assert obs["changes"]["counts"]["racy_unknown"] >= 1


# ---------- R9: a session named "io" ----------


def test_session_named_io_is_handled(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "c1", session="io")
    obs = _post(state, root, "c1", session="io")
    assert obs["status"] == "no-known-overlap"


def test_symlink_under_io_pre_is_refused_even_for_session_io(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    (state / "io" / "pre").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, state / "io" / "pre" / "claude")
    with pytest.raises(OSError):
        _pre(state, root, "c1", session="io")
    assert list(outside.iterdir()) == []


# ---------- R10: temp files of killed writers are swept ----------


def test_old_temp_files_are_swept(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "c1")
    session_dir = io.snapshot_path(state, "claude", "s1", "c1").parent
    stale = [session_dir / ".snap.abc", state / "io" / ".calls.def", state / "io" / ".hash-cache.x"]
    for path in stale:
        path.write_text("partial")
        ancient = time.time() - io.DONE_SNAPSHOT_MAX_AGE_SECONDS - 10
        os.utime(path, (ancient, ancient))
    _post(state, root, "c1")
    assert [p.exists() for p in stale] == [False, False, False]


# ---------- C1: a retargeted symlink input is not proven ----------


def test_symlinked_input_is_never_proven(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    (root / "data" / "b.csv").write_text("b\n")
    link = root / "data" / "current.csv"
    os.symlink("in.csv", link)
    time.sleep(0.03)

    def retarget() -> None:
        link.unlink()
        os.symlink("b.csv", link)

    _pre(state, root, "c1")
    retarget()  # the command read in.csv through the link, then moved it
    obs = io.post(state, host="claude", session="s1", call_id="c1", tool="Bash",
                  repo_root=root, inputs=[str(link)])
    (proof,) = obs["input_proofs"]
    assert proof["pre_run_proven"] is False and proof["reason"] == "symlinked-path"


# ---------- C2: printed text is not an exit code ----------


def _decoded_exit(payload: dict) -> str:
    script = f'source "{HOOKS_DIR / "mycelium-hook-lib.sh"}"; INPUT=$(cat); mycelium_bash_exit INPUT'
    result = subprocess.run(["bash", "-c", script], input=json.dumps(payload),
                            capture_output=True, text=True, check=False)
    return result.stdout.strip()


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("Exit code: 1\nWall time: 0.2 seconds\nOutput:\nboom", "1"),
        ("Process exited with code 3", "3"),
        ({"output": "Exit code: 0"}, "0"),
        ({"exit_code": 2, "output": "exit code 0"}, "2"),
        ("all good, exit code 0\n", ""),
        ("Output:\nfinished with exit code 0\n", ""),
        # Codex unified exec: the code sits in the header block, line 3.
        ("Chunk ID: 1a2b\nWall time: 0.4 seconds\nProcess exited with code 3\n"
         "Original token count: 5\nOutput:\nboom\n", "3"),
        # Header block present: words after "Output:" never count.
        ("Chunk ID: 1a2b\nWall time: 0.4 seconds\nOutput:\nexit code 0\n", ""),
        ({"output": "tests passed\nreturn code: 0"}, ""),
    ],
)
def test_exit_comes_only_from_a_header_or_structured_field(response, expected) -> None:
    assert _decoded_exit({"tool_response": response}) == expected


# ---------- C4: an unavailable observation is never silent ----------


def test_unavailable_observation_of_unknown_command_leaves_a_record(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    args = argparse.Namespace(ts="t", bash_cmd="./build-index --all", agent_id=None,
                              agent_type=None, bash_exit=0, outcome="success",
                              observation={"status": "unavailable", "reason": "no-pre-snapshot",
                                           "observation_id": "claude:x",
                                           "host_session_id": "s1", "changed": False})
    record = lineage_event.build_data_command_event(args, root)
    assert record is not None
    assert record["signals"] == ["observation-unavailable"]
    assert record["observation_reason"] == "no-pre-snapshot"


def test_manifest_lists_calls_still_unresolved(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "running", session="s1")
    events = tmp_path / "e.ndjson"
    events.write_text(json.dumps({
        "ts": "t", "kind": "data_command", "event_id": "e1", "bash_cmd": "x",
        "signals": ["observed-change"], "observation_id": "claude:done",
        "observation_status": "no-known-overlap", "observation_session": "s1",
    }) + "\n")
    manifest = stop.extract(events, "x", str(root), state / "io" / "obs")
    assert manifest["summary"]["unresolved_calls"] == ["claude:running"]


# ---------- C5: each language's calls on its own source ----------


def test_python_helper_named_like_readr_is_not_an_input() -> None:
    source = "def read_csv(value):\n    return value\n\nread_csv('ghost.csv')\n"
    inputs, _, _, _ = lineage_event.scan_source(source, language="python")
    assert inputs == []


def test_inline_r_comment_is_not_an_input(tmp_path: Path) -> None:
    detections = lineage_event._detect_scripts_with_cwd(
        "Rscript -e '1 # read.csv(\"ghost.csv\")'", tmp_path, bash_exit=0
    )
    ((_, inline, _),) = detections
    assert inline.language == "r"
    inputs, _, _, _ = lineage_event.scan_source(inline, inline.language)
    assert inputs == []


# ---------- C6: the walk bound holds inside one directory ----------


def test_walk_bound_holds_inside_a_flat_directory(tmp_path: Path) -> None:
    root = tmp_path / "flat"
    root.mkdir()
    for index in range(3000):
        (root / f"f{index}.txt").write_text("")
    snap = io.walk(root, seconds=0.0001)
    assert snap.timed_out
    assert len(snap.entries) < 3000


# ---------- C7: an unstable proof drops the unchecked digest ----------


def test_unstable_proof_replaces_the_unchecked_digest(tmp_path: Path, monkeypatch) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    src = root / "data" / "in.csv"
    events = [{"inputs": [{"path": str(src), "sha256": "f" * 64}]}]
    args = argparse.Namespace(observation={"pre_snapshot": str(tmp_path / "x.done"),
                                           "state_dir": str(state)})
    (tmp_path / "x.done").write_text("")
    monkeypatch.setattr(
        io, "prove_inputs",
        lambda *a, **k: {str(src): {"path": str(src), "pre_run_proven": False,
                                    "reason": "not-hashed:unstable", "sha256": None,
                                    "hash_status": "unstable", "size_bytes": 4}},
    )
    monkeypatch.setattr(lineage_event, "_repository_root", lambda cwd: root)
    lineage_event._apply_input_proofs(events, args, root)
    record = events[0]["inputs"][0]
    assert record["sha256"] is None and record["hash_status"] == "unstable"


# ---------- round 2 ----------


def test_turn_end_leaves_a_subagents_running_call_open(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    io.pre(state, host="claude", session="s1", call_id="X", tool="Bash", repo_root=root,
           snapshot=True, agent="sub-1")
    assert io.turn_end(state, host="claude", session="s1") == 0
    _pre(state, root, "Z")
    (root / "x_result.csv").write_text("written by the subagent")
    z = _post(state, root, "Z")
    assert z["status"] == "overlap"
    assert [o["call"] for o in z["overlapping"]] == ["X"]
    assert io.snapshot_path(state, "claude", "s1", "X").exists()


def test_turn_end_never_deletes_through_a_symlinked_marker_directory(tmp_path: Path) -> None:
    state = _state(tmp_path)
    (state / "io" / "open" / "claude").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep")
    os.symlink(victim, state / "io" / "open" / "claude" / "s1")
    io.turn_end(state, host="claude", session="s1")
    assert (victim / "precious.txt").read_text() == "keep"


def test_turn_end_does_not_hang_on_a_fifo_marker(tmp_path: Path) -> None:
    state = _state(tmp_path)
    marker_dir = state / "io" / "open" / "claude" / "s1"
    marker_dir.mkdir(parents=True)
    os.mkfifo(marker_dir / "fifo-call")
    # A blocking open of a FIFO with no writer never returns, so the bug is a
    # hang, not slowness: run it on a thread and give it a generous deadline.
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            io.turn_end(state, host="claude", session="s1")
        except BaseException as exc:  # surfaced below
            errors.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(60), "turn_end blocked on a FIFO marker"
    assert errors == []


def test_retargeted_parent_directory_symlink_is_never_proven(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    for version in ("v1", "v2"):
        (root / version).mkdir()
        (root / version / "in.csv").write_text(version)
    os.symlink("v1", root / "current")
    time.sleep(0.03)
    _pre(state, root, "c1")
    (root / "current").unlink()
    os.symlink("v2", root / "current")  # the command read v1, then retargeted
    obs = io.post(state, host="claude", session="s1", call_id="c1", tool="Bash",
                  repo_root=root, inputs=[str(root / "current" / "in.csv")])
    (proof,) = obs["input_proofs"]
    assert proof["pre_run_proven"] is False and proof["reason"] == "symlinked-path"


def test_repo_reached_through_an_alias_above_it_still_resolves(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    alias = tmp_path / "alias"
    os.symlink(tmp_path, alias)
    rel, reason = io.repo_relative_unfollowed(
        str(alias / "repo" / "data" / "in.csv"), os.path.realpath(root))
    assert (rel, reason) == ("data/in.csv", None)


def test_claude_stdout_that_looks_like_a_header_is_not_an_exit() -> None:
    payload = {"tool_response": {"stdout": "Exit code 7\nall fine", "stderr": "",
                                 "interrupted": False}}
    assert _decoded_exit(payload) == ""


def test_claude_post_is_exit_zero_whatever_the_output_says(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    env = {**os.environ, "MYCELIUM_HOOK_HOST": "claude"}
    run = lambda hook, payload: subprocess.run(  # noqa: E731
        [str(HOOKS_DIR / hook)], cwd=repo, input=json.dumps(payload), text=True,
        capture_output=True, env=env, check=False)
    run("mycelium-health.sh", {"cwd": str(repo), "session_id": "s1",
                               "hook_event_name": "SessionStart", "source": "startup"})
    run("mycelium-data-tracker.sh", {
        "cwd": str(repo), "session_id": "s1", "hook_event_name": "PostToolUse",
        "tool_name": "Bash", "tool_input": {"command": "head data/x.csv"},
        "tool_response": {"stdout": "Exit code 7\n", "stderr": ""}})
    stream = repo / ".mycelium" / "run" / "claude" / "s1" / "mycelium-data-events.tmp"
    (record,) = [json.loads(line) for line in stream.read_text().splitlines()]
    assert record["bash_exit"] == 0


def test_markers_scanned_before_the_journal_cover_the_handover() -> None:
    marker = {"call": "A", "open": True, "unjournaled": True}
    # Scanned while A was unjournaled, journal read after A was backdated:
    # A is reported once, through the journal.
    journaled = {"A": io.Interval("A", "claude", "s1", "Bash", False, 0, 9, False, 0)}
    assert io.filter_unjournaled([marker], journaled) == []
    # Journal read before A entered it: A is reported through its marker.
    assert io.filter_unjournaled([marker], {}) == [marker]


def test_turn_ended_call_is_listed_by_one_transaction_and_its_retries(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "B")  # never gets a Post
    io.turn_end(state, host="claude", session="s1")
    stand_in = io.snapshot_path(state, "claude", "s1", "B").with_name("B.unresolved")
    assert stand_in.exists()
    events = tmp_path / "e.ndjson"
    events.write_text(json.dumps({
        "ts": "t", "kind": "data_command", "event_id": "e1", "bash_cmd": "x",
        "signals": ["observed-change"], "observation_id": "claude:A",
        "observation_status": "no-known-overlap", "observation_session": "s1",
    }) + "\n")
    out = tmp_path / "manifest.json"

    def write_manifest(session_id: str) -> list[str]:
        subprocess.run(
            [sys.executable, str(Path(stop.__file__)), "--events-file", str(events),
             "--output", str(out), "--session-id", session_id, "--repo-root", str(root),
             "--observations-dir", str(state / "io" / "obs")],
            check=True, capture_output=True,
        )
        return json.loads(out.read_text())["summary"]["unresolved_calls"]

    assert write_manifest("2026-11-21-001") == ["claude:B"]
    # A blocked Stop retries and rewrites the same manifest: still listed.
    assert write_manifest("2026-11-21-001") == ["claude:B"]
    # A later transaction does not report it again.
    assert write_manifest("2026-11-21-002") == []
    assert stand_in.exists()  # left for the sweep, claimed


def test_claims_carry_over_when_a_data_only_manifest_is_promoted(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    _pre(state, root, "B")
    io.turn_end(state, host="claude", session="s1")
    events = tmp_path / "e.ndjson"
    events.write_text(json.dumps({
        "ts": "t", "kind": "data_command", "event_id": "e1", "bash_cmd": "x",
        "signals": ["data-path"], "observation_id": "claude:A",
        "observation_status": "no-known-overlap", "observation_session": "s1",
    }) + "\n")
    out = tmp_path / "manifest.json"

    def write_manifest(session_id: str, *extra: str) -> list[str]:
        subprocess.run(
            [sys.executable, str(Path(stop.__file__)), "--events-file", str(events),
             "--output", str(out), "--session-id", session_id, "--repo-root", str(root),
             "--observations-dir", str(state / "io" / "obs"), *extra],
            check=True, capture_output=True,
        )
        return json.loads(out.read_text())["summary"]["unresolved_calls"]

    data_only = "data-commands-claude-s1-e1"
    assert write_manifest(data_only) == ["claude:B"]
    # The continuation ran analysis: the session manifest replaces it.
    assert write_manifest("2026-11-21-001", "--replaces", data_only) == ["claude:B"]


def test_racy_unknown_output_is_not_reported_unchanged(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    observation = {
        "observation_id": "claude:x", "status": "no-known-overlap",
        "scope": {"unwatched": []},
        "changes": {"created": [], "modified": [], "deleted": [], "capped": False,
                    "racy_unknown": ["data/in.csv"],
                    "counts": {"created": 0, "modified": 0, "deleted": 0, "racy_unknown": 1}},
    }
    assert _mark(root, observation, root / "data" / "in.csv") == "unknown-racy"


def test_parent_traversal_after_a_symlink_is_never_proven(tmp_path: Path) -> None:
    root, state = _repo(tmp_path), _state(tmp_path)
    (root / "actual" / "child").mkdir(parents=True)
    (root / "actual" / "in.csv").write_text("the file the kernel reads")
    (root / "in.csv").write_text("an unrelated file at the root")
    os.symlink("actual/child", root / "link")
    time.sleep(0.03)
    _pre(state, root, "c1")
    obs = io.post(state, host="claude", session="s1", call_id="c1", tool="Bash",
                  repo_root=root, inputs=[str(root / "link" / ".." / "in.csv")])
    (proof,) = obs["input_proofs"]
    assert proof["pre_run_proven"] is False and proof["reason"] == "parent-traversal"


def test_aged_markers_are_deleted_by_the_overlap_scan(tmp_path: Path) -> None:
    state = _state(tmp_path)
    io.write_open_marker(state, "codex", "s9", "old", tool="Bash", background=False)
    marker = io._open_marker_path(state, "codex", "s9", "old")
    ancient = time.time() - io.OPEN_MARKER_MAX_SECONDS - 10
    os.utime(marker, (ancient, ancient))
    assert io.unjournaled_open_calls(state, {}, "me") == []
    assert not marker.exists()
