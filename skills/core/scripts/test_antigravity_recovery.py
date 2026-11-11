"""Real base-hook regressions for async provenance and uncertain finalization."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

import antigravity_hooks as ag


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    # Match the generated launcher's child interpreter resolution.
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    repo = tmp_path / "research α"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    for name in ("learnings", "decisions", "conventions"):
        (repo / f".living/{name}.md").write_text(f"# {name}\n")
    (repo / ".gitignore").write_text(".mycelium/\n")
    (repo / "analysis.py").write_text(
        'import pandas as pd\npd.DataFrame({"x": [2]}).to_csv("output.csv", index=False)\n')
    (repo / "output.csv").write_text("x\n1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    cid = str(uuid.uuid4())
    # Exact IDE native root under an isolated home: the adapter refuses any other root.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    brain = tmp_path / "home/.gemini/antigravity-ide/brain" / cid
    logs = brain / ".system_generated/logs"
    logs.mkdir(parents=True)
    transcript = logs / "transcript_full.jsonl"
    records = [{"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"}]

    def write():
        transcript.write_text("".join(json.dumps(record) + "\n" for record in records))

    write()
    payload = dict(conversationId=cid, workspacePaths=[str(repo)], artifactDirectoryPath=str(brain),
                   transcriptPath=str(transcript), initialNumSteps=1, invocationNum=0)
    ag.dispatch("PreInvocation", payload)
    run = repo / ".mycelium/run/antigravity-ide" / f"{cid}-turn-0"
    state_path = repo / ".mycelium/antigravity-ide" / cid / "native-state.json"
    return repo, payload, records, write, run, state_path


def stop(payload):
    return dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="", executionNum=0)


@pytest.mark.parametrize("deferred", [False, True])
def test_batched_positive_reminders_survive_until_individual_acknowledgement(cycle, deferred):
    repo, payload, records, write, run, state_path = cycle
    posts = []
    for step in (3, 5):
        subprocess.run([sys.executable, str(repo / "analysis.py")], cwd=repo, check=True)
        records.append(dict(step_index=step, source="MODEL", type="RUN_COMMAND",
                            status="RUNNING" if deferred else "DONE", exit_code=0))
        write()
        post = dict(payload, stepIdx=step, error="", toolCall={"name": "run_command", "args": {
            "Cwd": str(repo), "CommandLine": "python3 analysis.py"}})
        posts.append(post)
        ag.dispatch("PostToolUse", post)
    if deferred:
        for record in records:
            if record.get("type") == "RUN_COMMAND":
                record["status"] = "DONE"
        write()
    first = ag.dispatch("PreInvocation", dict(payload, initialNumSteps=6))["injectSteps"][0]["ephemeralMessage"]
    assert "MYCELIUM POST-ACTION PROTOCOL — MANDATORY" in first
    tokens = [f"{payload['conversationId']}-turn-0-step-{step}" for step in (3, 5)]
    for token in tokens:
        assert first.count(f"[Mycelium reminder {token}]") == 1
    # Only the first message was observed as delivered. Re-reading persisted
    # state on each dispatch also exercises restart between delivery and ack.
    records.append(dict(step_index=6, source="SYSTEM_SDK", type="EPHEMERAL_MESSAGE", status="DONE",
                        content=f"[Mycelium reminder {tokens[0]}]"))
    write()
    second = ag.dispatch("PreInvocation", dict(payload, initialNumSteps=7))["injectSteps"][0]["ephemeralMessage"]
    assert tokens[0] not in second and tokens[1] in second
    # Duplicate tool delivery cannot resurrect acknowledged guidance.
    ag.dispatch("PostToolUse", posts[0])
    records.append(dict(step_index=7, source="SYSTEM_SDK", type="EPHEMERAL_MESSAGE", status="DONE",
                        content=f"[Mycelium reminder {tokens[1]}]"))
    records.append(dict(step_index=8, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0))
    write()
    ag.dispatch("PostToolUse", dict(payload, stepIdx=8, error="", toolCall={"name": "run_command", "args": {
        "Cwd": str(repo), "CommandLine": "env -S 'echo prefix' python3 analysis.py"}}))
    final = ag.dispatch("PreInvocation", dict(payload, initialNumSteps=9))["injectSteps"][0]["ephemeralMessage"]
    assert "[Mycelium reminder" not in final


def test_async_outputs_are_captured_after_completion_once(cycle):
    repo, payload, records, write, run, state_path = cycle
    records.append(dict(step_index=3, source="MODEL", type="RUN_COMMAND", status="RUNNING"))
    write()
    post = dict(payload, stepIdx=3, error="", toolCall={"name": "run_command", "args": {
        "Cwd": str(repo), "CommandLine": "python3 analysis.py", "WaitMsBeforeAsync": 0}})
    ag.dispatch("PostToolUse", post)
    assert not (run / "mycelium-data-events.tmp").exists(), "RUNNING must not snapshot old outputs"
    assert ag.dispatch("Stop", stop(payload))["decision"] == "continue"
    assert (run / "active-session-log.tmp").exists()
    subprocess.run([sys.executable, str(repo / "analysis.py")], cwd=repo, check=True)
    records[-1].update(status="DONE", exit_code=0)
    write()
    # Native terminal evidence can become visible on the next invocation;
    # do not depend on a second PostToolUse for the original command.
    ag.dispatch("PreInvocation", dict(payload, initialNumSteps=4))
    ag.dispatch("PostToolUse", post)
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted fixture.\n")
    assert ag.dispatch("Stop", stop(payload)) == {}
    assert json.loads(state_path.read_text())["turns"]["0"]["status"] == "accepted"
    lineage = json.loads(next((repo / ".living/log/data-lineage").glob("*.json")).read_text())
    assert len(lineage["actions"]) == 1
    action = lineage["actions"][0]
    assert action["bash_exit"] == 0
    assert action["outputs"][0]["sha256"] == hashlib.sha256((repo / "output.csv").read_bytes()).hexdigest()


def test_lost_stop_result_reconciles_real_finalization_without_redispatch(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    actual = ag.subprocess.run
    calls = []

    def lose_result(command, *args, **kwargs):
        result = actual(command, *args, **kwargs)
        if Path(command[-1]).name == "mycelium-stop-check.sh":
            calls.append(result.returncode)
            assert not (run / "active-session-log.tmp").exists()
            raise subprocess.TimeoutExpired(command, 12)
        return result

    monkeypatch.setattr(ag.subprocess, "run", lose_result)
    with pytest.raises(subprocess.TimeoutExpired):
        ag.dispatch("Stop", stop(payload))
    monkeypatch.setattr(ag.subprocess, "run", actual)
    assert ag.dispatch("Stop", stop(payload)) == {}
    state = json.loads(state_path.read_text())["turns"]["0"]
    assert state["status"] == "accepted"
    assert calls == [0]


def test_unknown_stop_without_receipt_is_not_redispatched(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    actual = ag.subprocess.run
    calls = []

    def unavailable(command, *args, **kwargs):
        if Path(command[-1]).name == "mycelium-stop-check.sh":
            calls.append(command)
            raise subprocess.TimeoutExpired(command, 12)
        return actual(command, *args, **kwargs)

    monkeypatch.setattr(ag.subprocess, "run", unavailable)
    with pytest.raises(subprocess.TimeoutExpired):
        ag.dispatch("Stop", stop(payload))
    assert ag.dispatch("Stop", stop(payload))["decision"] == "continue"
    assert len(calls) == 1
    assert (run / "active-session-log.tmp").exists()
    assert json.loads(state_path.read_text())["turns"]["0"]["uncertain"] is True


def test_receipt_cannot_accept_a_different_conversation(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    state = json.loads(state_path.read_text())
    state["turns"]["0"]["stop_attempt"] = {"number": 1, "status": "running"}
    state_path.write_text(json.dumps(state))
    (run / "accepted-stop.json").write_text(json.dumps({"schema": 1, "host": ag.HOST,
        "session_id": str(uuid.uuid4()) + "-turn-0", "status": "accepted"}))
    before = state_path.read_bytes()
    with pytest.raises(ValueError, match="identity mismatch"):
        ag.dispatch("Stop", stop(payload))
    assert state_path.read_bytes() == before
    assert (run / "active-session-log.tmp").exists()


def test_linked_late_root_receipt_is_rejected_before_starting_earlier_root(tmp_path, monkeypatch):
    cid = str(uuid.uuid4())
    roots = [tmp_path / "a", tmp_path / "z"]
    for repo in roots:
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / ".living").mkdir()
    receipt = roots[1] / ".mycelium/run" / ag.HOST / f"{cid}-turn-0/accepted-stop.json"
    receipt.parent.mkdir(parents=True)
    other = tmp_path / "untouched.json"
    other.write_text("owner bytes")
    receipt.symlink_to(other)
    # Exact IDE native root under an isolated home: the adapter refuses any other root.
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    brain = tmp_path / "home/.gemini/antigravity-ide/brain" / cid
    transcript = brain / ".system_generated/logs/transcript_full.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT"}) + "\n")
    payload = dict(conversationId=cid, workspacePaths=[str(root) for root in roots],
                   artifactDirectoryPath=str(brain), transcriptPath=str(transcript), initialNumSteps=1)
    with pytest.raises(ValueError):
        ag.dispatch("PreInvocation", payload)
    assert not (roots[0] / ".mycelium").exists()
    assert other.read_text() == "owner bytes"


def test_concurrent_stop_publishes_one_real_lineage_and_receipt(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    subprocess.run([sys.executable, str(repo / "analysis.py")], cwd=repo, check=True)
    records.append(dict(step_index=3, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0))
    write()
    ag.dispatch("PostToolUse", dict(payload, stepIdx=3, error="", toolCall={"name": "run_command",
        "args": {"Cwd": str(repo), "CommandLine": "python3 analysis.py"}}))
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted concurrent fixture.\n")
    actual = ag.subprocess.run
    calls = []

    def observed(command, *args, **kwargs):
        result = actual(command, *args, **kwargs)
        if Path(command[-1]).name == "mycelium-stop-check.sh":
            calls.append(result.returncode)
        return result

    monkeypatch.setattr(ag.subprocess, "run", observed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda _: ag.dispatch("Stop", stop(payload)), range(2))) == [{}, {}]
    assert calls == [0]
    manifests = list((repo / ".living/log/data-lineage").glob("*.json"))
    assert len(manifests) == 1
    assert len(json.loads(manifests[0].read_text())["actions"]) == 1
    assert (repo / ".living/log/LOG_REGISTRY.md").read_text().count(manifests[0].stem + " |") == 1
    assert (run / "accepted-stop.json").is_file()
    assert not (run / "active-session-log.tmp").exists()


# Native capture outcome: a failed default extractor is an unresolved hook
# outcome, never a completed action or an accepted transaction.

def run_command(cycle, step, command="python3 analysis.py", script=None):
    repo, payload, records, write, run, state_path = cycle
    subprocess.run([sys.executable, str(repo / (script or "analysis.py"))], cwd=repo, check=True)
    records.append(dict(step_index=step, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0))
    write()
    return dict(payload, stepIdx=step, error="", toolCall={"name": "run_command", "args": {
        "Cwd": str(repo), "CommandLine": command}})


def tracker_calls(monkeypatch):
    actual, calls = ag.subprocess.run, []

    def counting(command, *args, **kwargs):
        if Path(command[-1]).name in {"mycelium-data-tracker.sh", "mycelium-stop-check.sh"}:
            calls.append(Path(command[-1]).name)
        return actual(command, *args, **kwargs)
    monkeypatch.setattr(ag.subprocess, "run", counting)
    return calls


def test_literal_io_capture_positive_control(cycle):
    repo, payload, records, write, run, state_path = cycle
    ag.dispatch("PostToolUse", run_command(cycle, 3))
    assert len((run / "mycelium-data-events.tmp").read_text().splitlines()) == 1
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted fixture.\n")
    assert ag.dispatch("Stop", stop(payload)) == {}
    turn = json.loads(state_path.read_text())["turns"]["0"]
    assert turn["status"] == "accepted" and turn["actions"]["3:mycelium-data-tracker.sh"] == "done"
    lineage = json.loads(next((repo / ".living/log/data-lineage").glob("*.json")).read_text())
    assert [Path(a["outputs"][0]["path"]) for a in lineage["actions"]] == [repo / "output.csv"]


def test_default_extractor_failure_is_retained_never_redispatched_or_accepted(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    events = run / "mycelium-data-events.tmp"
    events.write_text("")
    events.chmod(0o400)  # the unmodified default extractor now fails with PermissionError
    calls = tracker_calls(monkeypatch)
    post = run_command(cycle, 3)
    with pytest.raises(RuntimeError, match="mycelium-data-tracker.sh failed"):
        ag.dispatch("PostToolUse", post)
    assert (repo / "output.csv").read_text() == "x\n2\n"  # scientific output untouched
    turn = json.loads(state_path.read_text())["turns"]["0"]
    assert turn["actions"]["3:mycelium-data-tracker.sh"] == "running"
    assert "3:mycelium-post-action.sh" not in turn["actions"] and "3" in turn["pending_commands"]
    assert turn["uncertain"] is True and "PermissionError" in turn["last_hook_error"]
    ag.dispatch("PostToolUse", post)  # repeated native delivery
    injected = ag.dispatch("PreInvocation", dict(payload, initialNumSteps=4))["injectSteps"][0]["ephemeralMessage"]
    assert "capture failed" in injected and "PermissionError" in injected
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted fixture.\n")
    decisions = [ag.dispatch("Stop", stop(payload)).get("decision") for _ in range(ag.MAX_STOP_RETRIES + 2)]
    assert decisions == ["continue"] * ag.MAX_STOP_RETRIES + [None, None]
    turn = json.loads(state_path.read_text())["turns"]["0"]
    assert turn["status"] == "incomplete" and turn["actions"]["3:mycelium-data-tracker.sh"] == "running"
    assert calls == ["mycelium-data-tracker.sh"], "failed capture must not be redispatched; Stop never finalizes"
    assert events.read_text() == "" and not (run / "accepted-stop.json").exists()
    assert not list((repo / ".living/log/data-lineage").glob("*.json"))

    # After the environment is corrected, only a NEW explicit transaction may succeed.
    events.chmod(0o600)
    records.append({"step_index": 5, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"})
    write()
    ag.dispatch("PreInvocation", dict(payload, initialNumSteps=6))
    ag.dispatch("PostToolUse", run_command(cycle, 7))
    (repo / ".living/learnings.md").write_text("# Learnings\n\nSecond transaction.\n")
    assert ag.dispatch("Stop", stop(payload)) == {}
    turns = json.loads(state_path.read_text())["turns"]
    assert turns["5"]["status"] == "accepted" and turns["0"]["status"] == "incomplete"
    assert not (run / "accepted-stop.json").exists()


def test_crashed_custom_helper_is_an_unresolved_outcome(cycle, monkeypatch, tmp_path):
    repo, payload, records, write, run, state_path = cycle
    helper = tmp_path / "crash.py"
    helper.write_text("raise SystemExit(3)\n")
    monkeypatch.setenv("MYCELIUM_DATA_HELPER", str(helper))
    with pytest.raises(RuntimeError, match="mycelium-data-tracker.sh failed"):
        ag.dispatch("PostToolUse", run_command(cycle, 3))
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted fixture.\n")
    assert ag.dispatch("Stop", stop(payload))["decision"] == "continue"
    assert json.loads(state_path.read_text())["turns"]["0"]["uncertain"] is True


@pytest.mark.parametrize("kind", ["non_analysis", "unresolved_dynamic_io"])
def test_valid_no_op_and_unresolved_capture_still_complete(cycle, kind):
    repo, payload, records, write, run, state_path = cycle
    if kind == "non_analysis":
        records.append(dict(step_index=3, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0))
        write()
        post = dict(payload, stepIdx=3, error="", toolCall={"name": "run_command", "args": {"Cwd": str(repo), "CommandLine": "ls -la"}})
    else:
        (repo / "dynamic.py").write_text('import pandas as pd\nname = "".join(["out", "put.csv"])\npd.DataFrame({"x": [2]}).to_csv(name, index=False)\n')
        post = run_command(cycle, 3, "python3 dynamic.py", "dynamic.py")
    ag.dispatch("PostToolUse", post)
    events = (run / "mycelium-data-events.tmp")
    lines = events.read_text().splitlines() if events.exists() else []
    if kind == "non_analysis":
        assert lines == []
    else:
        assert [json.loads(x)["io_detection"] for x in lines] == ["unresolved"]
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted fixture.\n")
    assert ag.dispatch("Stop", stop(payload)) == {}
    turn = json.loads(state_path.read_text())["turns"]["0"]
    assert turn["status"] == "accepted" and turn["uncertain"] is False and "last_hook_error" not in turn


@pytest.mark.parametrize("strict", [None, "1"])
def test_shared_tracker_default_remains_fail_open_and_strict_is_opt_in(cycle, monkeypatch, tmp_path, strict):
    repo, payload, records, write, run, state_path = cycle
    helper = tmp_path / "crash.py"  # records that the tracker really reached it, then fails
    helper.write_text("import os, sys\nopen(os.environ['PROBE_REACHED'], 'w').close()\n"
                      "print('helper exploded', file=sys.stderr)\nraise SystemExit(3)\n")
    hooks = ag.PLUGIN / "skills/core/hooks"
    for host in ("antigravity-ide", "claude", "codex"):
        env = dict(os.environ, MYCELIUM_HOOK_HOST=host, MYCELIUM_PLUGIN_ROOT=str(ag.PLUGIN), MYCELIUM_DATA_HELPER=str(helper),
                   PROBE_REACHED=str(tmp_path / f"reached-{host}"))
        env.pop("MYCELIUM_STRICT_CAPTURE", None)
        session = f"{payload['conversationId']}-turn-0" if host == "antigravity-ide" else f"{host}-strict-probe"
        if host != "antigravity-ide":  # a live transaction of that host, as its own SessionStart creates
            start = subprocess.run(["bash", str(hooks / "mycelium-health.sh")], input=json.dumps(
                {"cwd": str(repo), "session_id": session, "source": "startup"}), text=True, capture_output=True, cwd=repo, env=env, timeout=30)
            assert start.returncode == 0 and (repo / ".mycelium/run" / host / session / "active-session-log.tmp").is_file(), start.stderr
        if strict:
            env["MYCELIUM_STRICT_CAPTURE"] = strict
        data = {"cwd": str(repo), "session_id": session, "tool_name": "Bash", "tool_input": {"command": "python3 analysis.py"},
                "tool_response": {"exit_code": 0}}
        result = subprocess.run(["bash", str(hooks / "mycelium-data-tracker.sh")], input=json.dumps(data),
                                text=True, capture_output=True, cwd=repo, env=env, timeout=30)
        assert (tmp_path / f"reached-{host}").is_file(), host
        if strict and host == "antigravity-ide":
            assert result.returncode != 0 and "helper exploded" in result.stderr and result.stdout == ""
        else:
            assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), host


# Concurrent Stop lock policy. The conversation lock must wait for legitimate
# held work under the shared bounded policy (mycelium_locks: default 30 s, valid
# configured value up to 300 s), not a shorter adapter-only override, and a real
# timeout must stay a failure. Delays are explicit and scoped to the holder
# thread; nothing depends on ambient machine load.

import contextlib
import fcntl
import threading
import time

import mycelium_locks

HOLD_PAST_OLD_OVERRIDE = 6.5  # seconds: beyond the former 5 s wait, well within the 30 s default


class StopRace:
    """A holder Stop inside the real conversation lock, raced by a duplicate."""

    def __init__(self, monkeypatch, repo, payload, delays=None, block_stop_check=False, after=None):
        self.repo, self.payload = repo, payload
        self.delays = delays or {}
        self.after = after  # optional holder-side callback after each real hook
        self.holder_in_lock = threading.Event()
        self.follower_waiting = threading.Event()
        self.release = threading.Event()
        self.hooks = []  # (thread role, hook) for every real hook dispatch
        self.block_stop_check = block_stop_check
        self.lock_path = repo / ".mycelium/locks" / f"antigravity-{payload['conversationId']}.lock"
        real_hook, real_lock = ag.run_hook, ag.durable_lock
        local = threading.local()
        self.local = local

        def hook(name, data, root, plugin):
            role = getattr(local, "role", "main")
            self.hooks.append((role, name))
            if role == "holder":
                self.holder_in_lock.set()
                # The duplicate must already be blocked in acquisition before the
                # holder's (delayed) work proceeds.
                assert self.follower_waiting.wait(30), "follower never reached lock acquisition"
                if name == "mycelium-stop-check.sh" and self.block_stop_check:
                    assert self.release.wait(60), "holder was never released"
                time.sleep(self.delays.get(name, 0))
            result = real_hook(name, data, root, plugin)
            if role == "holder" and self.after:
                self.after(name)
            return result

        @contextlib.contextmanager
        def lock(root, name, *args, **kwargs):
            if getattr(local, "role", "main") == "follower":
                self.follower_waiting.set()
            with real_lock(root, name, *args, **kwargs) as path:
                yield path

        monkeypatch.setattr(ag, "run_hook", hook)
        monkeypatch.setattr(ag, "durable_lock", lock)

    def run(self, role):
        self.local.role = role
        started = time.monotonic()
        try:
            return ("ok", ag.dispatch("Stop", stop(self.payload)), time.monotonic() - started)
        except Exception as exc:  # observed explicitly; never treated as success
            return ("error", exc, time.monotonic() - started)

    def lock_is_held(self):
        with self.lock_path.open("r+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle, fcntl.LOCK_UN)
            return False

    def race(self, between=None):
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            holder = pool.submit(self.run, "holder")
            assert self.holder_in_lock.wait(60), "holder never entered a hook under the lock"
            assert self.lock_is_held(), "holder must hold the real conversation lock before the duplicate starts"
            follower = pool.submit(self.run, "follower")
            if between is not None:
                between(follower)
            return holder.result(timeout=240), follower.result(timeout=240)
        finally:
            self.follower_waiting.set()
            self.release.set()
            pool.shutdown(wait=True)


def _finished_once(repo, run, state_path, actions):
    turn = json.loads(state_path.read_text())["turns"]["0"]
    assert turn["status"] == "accepted" and turn["uncertain"] is False and turn["pending_commands"] == {}
    assert turn["stop_attempt"] == {"status": "accepted", "number": 1} and turn["retries"] == 0
    manifests = list((repo / ".living/log/data-lineage").glob("*.json"))
    assert len(manifests) == 1
    assert len(json.loads(manifests[0].read_text())["actions"]) == actions
    assert (repo / ".living/log/LOG_REGISTRY.md").read_text().count(manifests[0].stem + " |") == 1
    assert (run / "accepted-stop.json").is_file()
    assert not (run / "active-session-log.tmp").exists()
    return turn


def test_duplicate_stop_waits_for_holder_beyond_five_seconds(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    monkeypatch.delenv("MYCELIUM_DURABLE_LOCK_TIMEOUT", raising=False)  # shared default policy
    subprocess.run([sys.executable, str(repo / "analysis.py")], cwd=repo, check=True)
    records.append(dict(step_index=3, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0))
    write()
    ag.dispatch("PostToolUse", dict(payload, stepIdx=3, error="", toolCall={"name": "run_command",
        "args": {"Cwd": str(repo), "CommandLine": "python3 analysis.py"}}))
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted delayed-holder fixture.\n")
    race = StopRace(monkeypatch, repo, payload, delays={"mycelium-stop-check.sh": HOLD_PAST_OLD_OVERRIDE})
    holder, follower = race.race()
    assert holder[0] == "ok" and holder[1] == {}, holder
    assert follower[0] == "ok" and follower[1] == {}, follower  # was LockError at acquisition with a 5 s wait
    assert follower[2] > 5, "the duplicate must really have waited past the former override"
    assert race.hooks == [("holder", "mycelium-stop-check.sh")]  # exactly one real stop-check, none by the follower
    _finished_once(repo, run, state_path, actions=1)


def test_deferred_commands_complete_once_under_one_lock_beyond_one_hook_allowance(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    # A valid configured value inside the shared bound covers the cumulative work.
    monkeypatch.setenv("MYCELIUM_DURABLE_LOCK_TIMEOUT", "120")
    steps = (3, 5, 7)
    for step in steps:
        records.append(dict(step_index=step, source="MODEL", type="RUN_COMMAND", status="RUNNING"))
        write()
        ag.dispatch("PostToolUse", dict(payload, stepIdx=step, error="", toolCall={"name": "run_command",
            "args": {"Cwd": str(repo), "CommandLine": "python3 analysis.py", "WaitMsBeforeAsync": 0}}))
    assert sorted(json.loads(state_path.read_text())["turns"]["0"]["pending_commands"]) == ["3", "5", "7"]
    subprocess.run([sys.executable, str(repo / "analysis.py")], cwd=repo, check=True)
    for record in records:
        if record.get("type") == "RUN_COMMAND":
            record.update(status="DONE", exit_code=0)
    write()
    # 6 deferred hook runs x 2.2 s = 13.2 s injected, more than one 12 s hook allowance.
    delays = {"mycelium-data-tracker.sh": 2.2, "mycelium-post-action.sh": 2.2}
    done = []

    def triage(name):
        # Deferred post-action reminders reset the living baseline, so the fixture
        # records its triage after the last one. The real stop-check still decides;
        # this isolates lock behaviour from Stop-retry semantics tested elsewhere.
        done.append(name)
        if done.count("mycelium-post-action.sh") == len(steps):
            (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted deferred-commands fixture.\n")

    race = StopRace(monkeypatch, repo, payload, delays=delays, after=triage)
    holder, follower = race.race()
    assert holder[0] == "ok" and holder[1] == {}, holder
    assert follower[0] == "ok" and follower[1] == {}, follower
    assert follower[2] > 12
    expected = [("holder", hook) for _ in steps for hook in ("mycelium-data-tracker.sh", "mycelium-post-action.sh")]
    assert race.hooks == expected + [("holder", "mycelium-stop-check.sh")]  # each capture exactly once
    turn = _finished_once(repo, run, state_path, actions=3)
    for step in steps:
        for hook in ("mycelium-data-tracker.sh", "mycelium-post-action.sh"):
            assert turn["actions"][f"{step}:{hook}"] == "done"


def test_configured_short_lock_deadline_fails_closed_without_follower_effects(cycle, monkeypatch):
    repo, payload, records, write, run, state_path = cycle
    monkeypatch.setenv("MYCELIUM_DURABLE_LOCK_TIMEOUT", "1")
    (repo / ".living/learnings.md").write_text("# Learnings\n\nCompleted short-deadline fixture.\n")
    race = StopRace(monkeypatch, repo, payload, block_stop_check=True)
    seen = {}

    def while_holder_unresolved(follower):
        result = follower.result(timeout=60)
        # Observed while the holder is still blocked inside the lock.
        seen.update(result=result, state=state_path.read_bytes(), receipt=(run / "accepted-stop.json").exists(),
                    held=race.lock_is_held())
        race.release.set()

    holder, follower = race.race(between=while_holder_unresolved)
    kind, error, elapsed = seen["result"]
    assert kind == "error" and isinstance(error, mycelium_locks.LockError), seen["result"]
    assert elapsed < 4, "the configured 1 s policy must govern, not a longer adapter override"
    assert seen["held"] and not seen["receipt"], "no acceptance may be invented while the holder is unresolved"
    during = json.loads(seen["state"])["turns"]["0"]
    assert during["stop_attempt"] == {"status": "running", "number": 1} and during["retries"] == 0
    assert all(role == "holder" for role, _ in race.hooks)  # no follower hook dispatch
    # The released holder then finishes its own single attempt normally.
    assert holder[0] == "ok" and holder[1] == {}, holder
    assert race.hooks == [("holder", "mycelium-stop-check.sh")]
    turn = json.loads(state_path.read_text())["turns"]["0"]
    assert turn["status"] == "accepted" and turn["stop_attempt"] == {"status": "accepted", "number": 1} and turn["retries"] == 0
    assert (run / "accepted-stop.json").is_file() and not (run / "active-session-log.tmp").exists()


@pytest.mark.parametrize("raw, expected", [(None, 30.0), ("45", 45.0), ("300", 300.0), ("0", 30.0),
                                           ("301", 30.0), ("-2", 30.0), ("soon", 30.0)])
def test_shared_lock_wait_policy_default_configured_and_fallback(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("MYCELIUM_DURABLE_LOCK_TIMEOUT", raising=False)
    else:
        monkeypatch.setenv("MYCELIUM_DURABLE_LOCK_TIMEOUT", raw)
    assert mycelium_locks._timeout_seconds() == expected
