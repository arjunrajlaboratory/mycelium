"""Issue #178: a Claude Code call the host refused before it ran.

Claude fires PreToolUse for a call and only then checks permission. A refused
call (an ask in `-p` mode, a deny rule, a PreToolUse hook's deny) gets no Post
and no other hook event, so its interval used to stay open until Stop and mark
every later call of the turn `overlap`. The session transcript records the
refusal: one `is_error` tool_result for the call on a line whose
`permissionDecision.decision` is `reject`. That record, and nothing weaker,
closes the interval as denied. Every transcript line below has the shape a
real `claude -p` run (Claude Code 2.1.332) wrote for that case.
"""

from __future__ import annotations

import io as stdio
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402

pytestmark = pytest.mark.platform

SID = "11111111-2222-3333-4444-555555555555"


def _result_line(call: str, *, decision: str | None = "reject", is_error: bool = True,
                 session: str = SID, content: str = "'env -C' can't be checked before it runs",
                 extra_results: tuple[str, ...] = (), **fields) -> dict:
    blocks = [{"type": "tool_result", "content": content, "is_error": is_error,
               "tool_use_id": call}]
    blocks += [{"type": "tool_result", "content": "x", "is_error": False, "tool_use_id": other}
               for other in extra_results]
    line = {"parentUuid": "p", "isSidechain": False, "type": "user", "sessionId": session,
            "message": {"role": "user", "content": blocks},
            "toolUseResult": f"Error: {content}" if is_error else {"stdout": content},
            "sourceToolAssistantUUID": "a"}
    if decision is not None:
        line["permissionDecision"] = {"decision": decision, "source": "config",
                                      "reasonCode": "bashMiss:semantics"}
        if decision == "reject":
            line["toolDenialKind"] = "user-rejected"
    line.update(fields)
    return line


def _tool_use_line(call: str, command: str = "env -C sub python3 x.py") -> dict:
    return {"type": "assistant", "sessionId": SID, "isSidechain": False,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": call, "name": "Bash",
                 "input": {"command": command}}]}}


def _transcript(tmp_path: Path, *lines: dict | str) -> Path:
    path = tmp_path / "projects" / f"{SID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write((line if isinstance(line, str) else json.dumps(line)) + "\n")
    return path


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def _state(project: Path) -> Path:
    return project / ".mycelium"


def _hook(project: Path, event: str, call: str, transcript: Path | None,
          *, command: str = "echo hi", session: str = SID, **extra) -> None:
    payload = {"session_id": session, "hook_event_name": event, "tool_name": "Bash",
               "tool_use_id": call, "cwd": str(project),
               "tool_input": {"command": command}, **extra}
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    io.hook(_state(project), project, "claude", json.dumps(payload))


def _post(project: Path, call: str, session: str = SID) -> dict:
    return io.post(_state(project), host="claude", session=session, call_id=call, tool="Bash",
                   repo_root=project, outcome="success", bash_exit=0)


def _events(project: Path, call: str) -> list[str]:
    return ["deny" if r.get("denied") is True else r["ev"]
            for r in io._journal_records(io.journal_path(_state(project)))
            if r.get("call") == call]


def _snap(project: Path, call: str, session: str = SID) -> Path:
    return io.snapshot_path(_state(project), "claude", session, call)


# ---------- the issue's run, replayed ----------


def test_a_refused_call_stops_marking_later_calls_overlap(project: Path,
                                                          tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", transcript, command="env -C sub python3 x.py")
    assert _snap(project, "toolu_A").exists()
    # The host refuses A and writes the refusal; the next call's Pre sees it.
    _transcript(tmp_path, _result_line("toolu_A"), _tool_use_line("toolu_B", "echo after"))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    observation = _post(project, "toolu_B")
    assert observation["status"] == "no-known-overlap"
    assert observation["overlapping"] == []
    assert _events(project, "toolu_A") == ["pre", "deny"]
    assert not _snap(project, "toolu_A").exists()
    open_dir = _state(project) / "io" / "open" / "claude" / SID
    assert not (open_dir / "toolu_A").exists()


def test_stop_closes_a_refused_call_as_denied_and_leaves_no_claim_file(
        project: Path, tmp_path: Path, monkeypatch) -> None:
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"), _tool_use_line("toolu_C"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _hook(project, "PreToolUse", "toolu_C", transcript)  # interrupted: no record at all
    _transcript(tmp_path, _result_line("toolu_A"))
    stop_payload = {"session_id": SID, "hook_event_name": "Stop",
                    "transcript_path": str(transcript), "background_tasks": []}
    monkeypatch.setattr(sys, "stdin", stdio.StringIO(json.dumps(stop_payload)))
    assert io.main(["turn-end", "--state-dir", str(_state(project)), "--host", "claude",
                    "--session", SID, "--stop-payload-stdin"]) == 0
    assert _events(project, "toolu_A") == ["pre", "deny"]
    pre_dir = _snap(project, "toolu_A").parent
    assert sorted(p.name for p in pre_dir.iterdir()) == ["toolu_C.unresolved"]
    # The call with no refusal on record is still unresolved, as before.
    journal = io._journal_records(io.journal_path(_state(project)))
    assert any(r.get("call") == "toolu_C" and r.get("reason") == "turn-ended"
               for r in journal)
    store = io.observations_path(_state(project), "claude", SID).read_text()
    assert "toolu_C" in store and "toolu_A" not in store


# ---------- only a positive record closes a call ----------


@pytest.mark.parametrize("lines", [
    pytest.param([], id="no-result-yet"),
    pytest.param([_result_line("toolu_A", decision="accept", is_error=True,
                               content="Exit code 1")], id="ran-and-failed"),
    pytest.param([_result_line("toolu_A", decision=None, content="Interrupted")],
                 id="interrupted-no-decision"),
    pytest.param([_result_line("toolu_A", is_error=False)], id="reject-but-not-an-error"),
    pytest.param([_result_line("toolu_A", session="99999999-0000-0000-0000-000000000000")],
                 id="another-sessions-line"),
    pytest.param([_result_line("toolu_A", extra_results=("toolu_Z",))],
                 id="decision-shared-by-two-results"),
    pytest.param([_result_line("toolu_A", isSidechain=True)], id="sidechain"),
    pytest.param([_result_line("toolu_A"),
                  _result_line("toolu_A", decision="accept", is_error=False, content="ok")],
                 id="a-later-result-says-it-ran"),
    pytest.param([_result_line("toolu_A", permissionDecision="reject")],
                 id="decision-not-an-object"),
    pytest.param([json.dumps(_result_line("toolu_A"))[:-5]], id="torn-line"),
])
def test_anything_short_of_a_refusal_record_leaves_the_call_open(
        project: Path, tmp_path: Path, lines: list) -> None:
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    if lines:
        _transcript(tmp_path, *lines)
    _hook(project, "PreToolUse", "toolu_B", transcript)
    observation = _post(project, "toolu_B")
    assert [o["call"] for o in observation["overlapping"]] == ["toolu_A"]
    assert observation["status"] == "overlap"
    assert _events(project, "toolu_A") == ["pre"]
    assert _snap(project, "toolu_A").exists()


@pytest.mark.parametrize("transcript_value", [
    None, "", "relative/path.jsonl", 7, "/nonexistent/dir/x.jsonl",
])
def test_a_missing_or_unusable_transcript_path_changes_nothing(
        project: Path, tmp_path: Path, transcript_value) -> None:
    _transcript(tmp_path, _tool_use_line("toolu_A"), _result_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", None)
    payload = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_use_id": "toolu_B", "cwd": str(project),
               "tool_input": {"command": "echo"}, "transcript_path": transcript_value}
    io.hook(_state(project), project, "claude", json.dumps(payload))
    assert _events(project, "toolu_A") == ["pre"]


def test_a_symlinked_transcript_is_not_read(project: Path, tmp_path: Path) -> None:
    real = _transcript(tmp_path, _tool_use_line("toolu_A"), _result_line("toolu_A"))
    link = tmp_path / "link.jsonl"
    os.symlink(real, link)
    _hook(project, "PreToolUse", "toolu_A", link)
    _hook(project, "PreToolUse", "toolu_B", link)
    assert _events(project, "toolu_A") == ["pre"]


def test_a_refused_background_request_closes_but_a_subagents_call_does_not(
        project: Path, tmp_path: Path) -> None:
    """Refused, a background request never launched; turn end skips
    background calls, so without this it stayed open for a day. A subagent's
    refusal is in its own transcript, never the main one."""
    transcript = _transcript(tmp_path)
    payload = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
               "tool_use_id": "toolu_bg", "cwd": str(project),
               "tool_input": {"command": "sleep 9", "run_in_background": True},
               "transcript_path": str(transcript)}
    io.hook(_state(project), project, "claude", json.dumps(payload))
    _hook(project, "PreToolUse", "toolu_sub", transcript, agent_id="agent-1")
    _transcript(tmp_path, _result_line("toolu_bg"), _result_line("toolu_sub"))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    assert _events(project, "toolu_bg") == ["pre", "deny"]
    assert _events(project, "toolu_sub") == ["pre"]
    observation = _post(project, "toolu_B")
    assert [o["call"] for o in observation["overlapping"]] == ["toolu_sub"]


def test_another_sessions_call_is_never_closed(project: Path, tmp_path: Path) -> None:
    other = "99999999-0000-0000-0000-000000000000"
    transcript = _transcript(tmp_path)
    _hook(project, "PreToolUse", "toolu_A", transcript, session=other)
    # Even this session's own transcript naming the id cannot close it.
    _transcript(tmp_path, _result_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    assert _events(project, "toolu_A") == ["pre"]


def test_a_codex_call_is_never_closed_from_a_transcript(project: Path,
                                                        tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, _result_line("toolu_A"))
    for call in ("toolu_A", "toolu_B"):
        payload = {"session_id": SID, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_use_id": call, "cwd": str(project),
                   "tool_input": {"command": "echo"}, "transcript_path": str(transcript)}
        io.hook(_state(project), project, "codex", json.dumps(payload))
    assert _events(project, "toolu_A") == ["pre"]


def test_a_refusal_past_the_transcript_tail_is_not_seen(
        project: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(io, "TRANSCRIPT_TAIL_BYTES", 4096)
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _transcript(tmp_path, _result_line("toolu_A"),
                *({"type": "assistant", "pad": "x" * 500} for _ in range(20)))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    # Past the tail the refusal is not seen: the call stays open (safe).
    assert _events(project, "toolu_A") == ["pre"]


def test_a_denied_call_closed_once_is_not_closed_again(project: Path,
                                                       tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _transcript(tmp_path, _result_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    _hook(project, "PreToolUse", "toolu_C", transcript)
    io.turn_end(_state(project), host="claude", session=SID)
    assert _events(project, "toolu_A") == ["pre", "deny"]


# ---------- what a denial means to the journal readers ----------


def _released_observer(tmp_path: Path):
    """io_observer.py as released in 0.10.1, the last release without denials."""
    import importlib.util
    import subprocess

    source = subprocess.run(
        ["git", "-C", str(HERE), "show", "mycelium--v0.10.1:skills/core/scripts/io_observer.py"],
        capture_output=True, text=True)
    if source.returncode != 0:
        pytest.skip("the mycelium--v0.10.1 tag is not in this clone")
    path = tmp_path / "released" / "io_observer_0_10_1.py"
    path.parent.mkdir()
    path.write_text(source.stdout)
    spec = importlib.util.spec_from_file_location("io_observer_0_10_1", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_both_readers_end_a_denied_call(project: Path, tmp_path: Path) -> None:
    """This release reads the denial as a call that never ran; 0.10.1, sharing
    the journal (another session, another host), still reads it as an end, so
    it never expires the call a day later as an unbounded gap."""
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _transcript(tmp_path, _result_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    records = io._journal_records(io.journal_path(_state(project)))
    key = io.interval_key("claude", SID, "toolu_A")
    interval = io._intervals(records)[key]
    assert interval.post_seq is not None and interval.end_reason == "denied"
    assert not interval.expired and not interval.observe
    old = _released_observer(tmp_path)._intervals(records)[key]
    assert old.post_seq == interval.post_seq and not old.expired


def test_a_refusal_written_after_the_parallel_calls_pre_is_seen_at_its_post(
        project: Path, tmp_path: Path) -> None:
    """Parallel calls: B's Pre runs before A's refusal is written, B's Post
    after it. The Post closes A first, so B is not marked overlap."""
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"), _tool_use_line("toolu_B"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _hook(project, "PreToolUse", "toolu_B", transcript)
    _transcript(tmp_path, _result_line("toolu_A"))
    observation = io.post(_state(project), host="claude", session=SID, call_id="toolu_B",
                          tool="Bash", repo_root=project, outcome="success", bash_exit=0,
                          transcript=str(transcript))
    assert observation["overlapping"] == [] and observation["status"] == "no-known-overlap"
    assert _events(project, "toolu_A") == ["pre", "deny"]


def test_without_the_transcript_the_parallel_call_still_overlaps(
        project: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"), _tool_use_line("toolu_B"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _hook(project, "PreToolUse", "toolu_B", transcript)
    _transcript(tmp_path, _result_line("toolu_A"))
    assert _post(project, "toolu_B")["status"] == "overlap"


def test_a_tail_starting_on_a_line_boundary_keeps_that_line(
        project: Path, tmp_path: Path, monkeypatch) -> None:
    accept = json.dumps(_result_line("toolu_A", decision="accept", is_error=False,
                                     content="ok"))
    reject = json.dumps(_result_line("toolu_A"))
    transcript = _transcript(tmp_path, {"type": "summary", "pad": "p" * 200})
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _transcript(tmp_path, accept, reject)
    # The tail begins exactly on the accept line's first byte.
    monkeypatch.setattr(io, "TRANSCRIPT_TAIL_BYTES", len(accept) + len(reject) + 2)
    _hook(project, "PreToolUse", "toolu_B", transcript)
    assert _events(project, "toolu_A") == ["pre"]


def test_an_unreadable_stop_payload_still_ends_the_turn(project: Path, monkeypatch) -> None:
    class Broken:
        def read(self) -> str:
            raise OSError("stdin closed")

    _hook(project, "PreToolUse", "toolu_A", None)
    monkeypatch.setattr(sys, "stdin", Broken())
    assert io.main(["turn-end", "--state-dir", str(_state(project)), "--host", "claude",
                    "--session", SID, "--stop-payload-stdin"]) == 0
    assert _events(project, "toolu_A") == ["pre", "expire"]


def test_compaction_drops_a_denied_call_once_nothing_open_precedes_it(
        project: Path, tmp_path: Path, monkeypatch) -> None:
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_A", transcript)
    _transcript(tmp_path, _result_line("toolu_A"))
    _hook(project, "PreToolUse", "toolu_B", transcript)
    _post(project, "toolu_B")
    monkeypatch.setattr(io, "JOURNAL_COMPACT_LINES", 3)
    io.journal_event(_state(project), "pre", "toolu_C", host="claude", session=SID,
                     tool="Bash")
    # The denied call no longer holds the horizon back.
    assert _events(project, "toolu_A") == []
    assert _events(project, "toolu_C") == ["pre"]


def test_a_denied_call_never_clouds_a_write_in_the_lineage_index(
        project: Path, tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    _hook(project, "PreToolUse", "toolu_c1", transcript)
    (project / "out" / "x").write_text("A")
    _post(project, "toolu_c1")
    _hook(project, "PreToolUse", "toolu_D", transcript)
    _transcript(tmp_path, _result_line("toolu_D"))
    _hook(project, "PreToolUse", "toolu_c2", transcript)
    (project / "out" / "y").write_text("other")
    _post(project, "toolu_c2")
    io.turn_end(_state(project), host="claude", session=SID)
    write = li.query_hash(li.load(project, write=False), _sha("A"),
                          disk=False)["paths"][0]["writes"][0]["record"]
    assert write["status"] == "current-in-record", write


def test_an_unrecorded_call_still_clouds_the_write(project: Path, tmp_path: Path) -> None:
    """The control: the same history with no refusal on record stays unknown."""
    transcript = _transcript(tmp_path)
    _hook(project, "PreToolUse", "toolu_c1", transcript)
    (project / "out" / "x").write_text("A")
    _post(project, "toolu_c1")
    _hook(project, "PreToolUse", "toolu_D", transcript)
    _hook(project, "PreToolUse", "toolu_c2", transcript)
    (project / "out" / "y").write_text("other")
    _post(project, "toolu_c2")
    io.turn_end(_state(project), host="claude", session=SID)
    write = li.query_hash(li.load(project, write=False), _sha("A"),
                          disk=False)["paths"][0]["writes"][0]["record"]
    assert write["status"] == "unknown"


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


def test_the_manifest_lists_no_denied_call_as_unresolved(project: Path,
                                                         tmp_path: Path) -> None:
    transcript = _transcript(tmp_path)
    _hook(project, "PreToolUse", "toolu_D", transcript)
    _transcript(tmp_path, _result_line("toolu_D"))
    _hook(project, "PreToolUse", "toolu_c1", transcript)
    summary = _post(project, "toolu_c1")
    io.turn_end(_state(project), host="claude", session=SID)
    events = tmp_path / "e.ndjson"
    events.write_text(json.dumps({
        "ts": "t", "kind": "data_command", "event_id": "e1", "bash_cmd": "echo hi",
        "signals": ["observed-change"], "observation_id": summary["observation_id"],
        "observation_status": summary["status"], "observation_session": SID,
    }) + "\n")
    out = tmp_path / "manifest.json"
    import subprocess

    subprocess.run(
        [sys.executable, str(Path(stop.__file__)), "--events-file", str(events),
         "--output", str(out), "--session-id", "2026-11-25-001", "--repo-root", str(project),
         "--observations-dir", str(_state(project) / "io" / "obs")],
        check=True, capture_output=True, timeout=60,
    )
    manifest = json.loads(out.read_text())
    assert manifest["summary"].get("unresolved_calls", []) == []


# ---------- through the real hooks ----------


def test_the_real_hooks_close_a_refused_call(tmp_path: Path) -> None:
    import subprocess

    hooks = HERE.parent / "hooks"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    (repo / "out").mkdir()
    transcript = _transcript(tmp_path, _tool_use_line("toolu_A"))
    env = {**os.environ, "MYCELIUM_HOOK_HOST": "claude"}

    def run(name: str, payload: dict) -> None:
        payload = {"cwd": str(repo), "session_id": SID,
                   "transcript_path": str(transcript), **payload}
        done = subprocess.run([str(hooks / name)], cwd=repo, input=json.dumps(payload),
                              text=True, capture_output=True, env=env, timeout=120)
        assert done.returncode == 0, done.stderr

    def bash(event: str, call: str, command: str, **extra) -> None:
        run("mycelium-io-observer.sh" if event == "PreToolUse" else "mycelium-data-tracker.sh",
            {"hook_event_name": event, "tool_name": "Bash", "tool_use_id": call,
             "tool_input": {"command": command}, **extra})

    run("mycelium-health.sh", {"hook_event_name": "SessionStart", "source": "startup"})
    bash("PreToolUse", "toolu_A", "env -C out python3 x.py")
    _transcript(tmp_path, _tool_use_line("toolu_B", "touch out/b"))
    bash("PreToolUse", "toolu_B", "touch out/b")  # in parallel: A not refused yet
    _transcript(tmp_path, _result_line("toolu_A"))
    (repo / "out" / "b").write_text("")
    bash("PostToolUse", "toolu_B", "touch out/b", tool_response={"exit_code": 0})
    state = repo / ".mycelium"
    # The data tracker's Post closed A before it computed B's overlap.
    assert _events(repo, "toolu_A") == ["pre", "deny"]
    run("mycelium-stop-check.sh", {"hook_event_name": "Stop", "stop_hook_active": False})
    store = [json.loads(line) for line in
             io.observations_path(state, "claude", SID).read_text().splitlines()]
    (observation,) = [r for r in store if r.get("observation_id") == "claude:toolu_B"]
    assert observation["status"] == "no-known-overlap"
    assert not any(r.get("observation_id") == "claude:toolu_A" for r in store)
    pre_dir = io.snapshot_path(state, "claude", SID, "toolu_A").parent
    assert not pre_dir.exists() or not any(pre_dir.iterdir())
