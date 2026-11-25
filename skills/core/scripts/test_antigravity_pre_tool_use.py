"""Antigravity PreToolUse: an observation-only I/O pre-snapshot that never touches approvals.

Antigravity requires a `decision` from every PreToolUse hook. `allow` defers to
the next hook or the default policy, so the handler must print exactly
`{"decision": "allow"}` and exit 0 on every path (a crash, a hang, a malformed
payload, an unsupported tool) and nothing else, within a hard time bound.
The observation itself must correlate with the adapter's PostToolUse so the
lineage index gets the same positive proof as on Claude and Codex.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

import antigravity_hooks as ag
import io_observer as io
import lineage_index as li

pytestmark = pytest.mark.platform

HOOKS = ag.PLUGIN / "skills/core/hooks"
WRAPPER = HOOKS / "mycelium-antigravity-pre.sh"
ALLOW = '{"decision": "allow"}\n'
CID = "a0000000-0000-4000-8000-0000000000a1"
IDE_ROOT = (".gemini", "antigravity-ide", "brain")
APP_ROOT = (".gemini", "antigravity", "brain")
USER = {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"}
# The wrapper's hard bound plus generous process and CI scheduling slack.
HARD_BOUND = 2.0 + 1.0


def tree(path):
    if not path.exists():
        return None
    return {item.relative_to(path).as_posix(): (item.read_bytes() if item.is_file() and not item.is_symlink()
                                                 else "link" if item.is_symlink() else "dir")
            for item in sorted(path.rglob("*"))}


def run_wrapper(stdin, *, wrapper=WRAPPER, host=None, env=None, cwd=None):
    args = ["/bin/bash", str(wrapper), sys.executable] + (["--host", host] if host else [])
    started = time.monotonic()
    result = subprocess.run(args, input=stdin, text=True, capture_output=True, timeout=30,
                            cwd=cwd or ag.PLUGIN, env=dict(os.environ, **(env or {})))
    return result, time.monotonic() - started


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "research α"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (repo / "data").mkdir()
    (repo / "data/x.csv").write_text("a\n1\n")
    (repo / "analysis.py").write_text("import csv\nrows = list(csv.reader(open('data/x.csv')))\n"
                                      "open('out.csv', 'w').write('n\\n%d\\n' % len(rows))\n")
    old = 1_700_000_000
    for path in (repo / "data/x.csv", repo / "analysis.py"):
        os.utime(path, (old, old))
    return home, repo


def native(home, repo, parts=IDE_ROOT, cid=CID):
    artifact = home.joinpath(*parts, cid)
    transcript = artifact / ".system_generated/logs/transcript_full.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps(USER) + "\n")
    return transcript, {"conversationId": cid, "workspacePaths": [str(repo)],
                        "artifactDirectoryPath": str(artifact), "transcriptPath": str(transcript),
                        "invocationNum": 0, "initialNumSteps": 1}


def append(transcript, **record):
    with transcript.open("a") as stream:
        stream.write(json.dumps(record) + "\n")


def command_call(repo, step, command="python3 analysis.py", app=False):
    args = {"CommandLine": command} if app else {"CommandLine": command, "Cwd": str(repo)}
    return {"stepIdx": step, "toolCall": {"name": "run_command", "args": args}}


# ---- Always allow, on every input class, through the packaged wrapper --------------------------

@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null", '{"toolCall": 7}',
                                   json.dumps({"conversationId": "not-a-uuid"}),
                                   json.dumps({"conversationId": CID, "stepIdx": 1,
                                               "toolCall": {"name": "run_command", "args": {}}})])
def test_malformed_payloads_get_exactly_allow(stdin):
    result, elapsed = run_wrapper(stdin)
    assert (result.returncode, result.stdout) == (0, ALLOW)
    assert json.loads(result.stdout) == {"decision": "allow"}
    assert elapsed < HARD_BOUND


@pytest.mark.parametrize("body", [
    "raise RuntimeError('observer crashed')\n",
    "this is not python\n",
    "import os\nos._exit(3)\n",
    "print('{\"decision\": \"auto_approve\"}')\n",
    "print('{\"decision\": \"deny\"}'); raise SystemExit(2)\n",
    # Ignores the soft deadline entirely: only the wrapper's watchdog ends it.
    "import signal, time\nsignal.signal(signal.SIGALRM, signal.SIG_IGN)\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)\n",
    None,  # the adapter script is missing
])
def test_wrapper_prints_allow_whatever_the_helper_does(tmp_path, body):
    package = tmp_path / "pkg"
    (package / "skills/core/hooks").mkdir(parents=True)
    (package / "skills/core/scripts").mkdir(parents=True)
    wrapper = package / "skills/core/hooks" / WRAPPER.name
    shutil.copy2(WRAPPER, wrapper)
    if body is not None:
        (package / "skills/core/scripts/antigravity_hooks.py").write_text(body)
    result, elapsed = run_wrapper('{"conversationId": "x"}', wrapper=wrapper, cwd=package)
    assert (result.returncode, result.stdout) == (0, ALLOW)
    assert elapsed < HARD_BOUND


def test_wrapper_allows_when_the_interpreter_is_missing(tmp_path):
    result = subprocess.run(["/bin/bash", str(WRAPPER), str(tmp_path / "no-python")], input="{}",
                            text=True, capture_output=True, timeout=30, cwd=ag.PLUGIN)
    assert (result.returncode, result.stdout) == (0, ALLOW)


def test_python_entry_point_alone_also_prints_only_allow(project):
    home, repo = project
    _, payload = native(home, repo)
    for stdin in ("", "garbage", json.dumps(dict(payload, **command_call(repo, 1)))):
        result = subprocess.run([sys.executable, str(ag.PLUGIN / "skills/core/scripts/antigravity_hooks.py"),
                                 "PreToolUse"], input=stdin, text=True, capture_output=True, timeout=30,
                                env=dict(os.environ, HOME=str(home)))
        assert (result.returncode, result.stdout) == (0, ALLOW)


@pytest.mark.parametrize("fault", ["crash", "slow", "hang", "exit"])
@pytest.mark.parametrize("via", ["file", "env"])
def test_forced_observer_faults_still_allow_within_the_bound(project, fault, via):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    env = {"HOME": str(home)}
    if via == "file":
        (repo / ".mycelium/antigravity-pre-fault").write_text(fault + "\n")
    else:
        env[ag.PRE_FAULT_ENV] = fault
    result, elapsed = run_wrapper(json.dumps(dict(payload, **command_call(repo, 1))), env=env)
    assert (result.returncode, result.stdout) == (0, ALLOW)
    assert elapsed < HARD_BOUND


def test_unsupported_tools_and_foreign_roots_write_nothing(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    before = tree(repo / ".mycelium")
    for call in ({"name": "view_file", "args": {"AbsolutePath": str(repo / "analysis.py")}},
                 {"name": "write_to_file", "args": {"TargetFile": str(repo / ".living/x.md")}},
                 {"name": "write_to_file", "args": {"TargetFile": "/elsewhere/x.md"}}):
        result, _ = run_wrapper(json.dumps(dict(payload, stepIdx=1, toolCall=call)), env={"HOME": str(home)})
        assert (result.returncode, result.stdout) == (0, ALLOW)
    assert tree(repo / ".mycelium") == before


def test_app_profile_refuses_ide_native_root_and_writes_nothing(project):
    home, repo = project
    _, payload = native(home, repo, parts=IDE_ROOT)
    ag.dispatch("PreInvocation", payload)
    before = tree(repo / ".mycelium")
    result, _ = run_wrapper(json.dumps(dict(payload, **command_call(repo, 1, app=True))),
                            host=ag.APP_HOST, env={"HOME": str(home)})
    assert (result.returncode, result.stdout) == (0, ALLOW)
    assert tree(repo / ".mycelium") == before


def test_no_transaction_and_late_events_take_no_snapshot(project):
    home, repo = project
    transcript, payload = native(home, repo)
    (repo / ".mycelium").mkdir()
    # No PreInvocation yet: no active work cycle, so nothing is observed.
    assert ag.pre_tool_use(dict(payload, **command_call(repo, 1))) == []
    assert not (repo / ".mycelium/io").exists()


def test_pre_snapshot_budget_is_bounded(project, monkeypatch):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    seen = {}
    real_walk = io.walk

    def spy(root, **kw):
        seen.update(kw, lock_wait=io.LOCK_WAIT_SECONDS)
        return real_walk(root, **kw)

    monkeypatch.setattr(io, "walk", spy)
    ag.pre_tool_use(dict(payload, **command_call(repo, 1)), deadline=time.monotonic() + 1.0)
    assert 0 < seen["seconds"] <= 1.0
    assert seen["lock_wait"] <= ag.PRE_LOCK_SECONDS < ag.PRE_TOOL_SECONDS
    assert io.LOCK_WAIT_SECONDS == 2.0  # restored for anything else in this process


# ---- Registration ------------------------------------------------------------------------------

@pytest.mark.parametrize("profile,tools", [
    ("ide", {"run_command", "write_to_file", "replace_file_content", "multi_replace_file_content"}),
    ("app", {"run_command", "write_file", "edit_file"}),
])
def test_package_registers_pre_tool_use_for_commands_and_edits_only(tmp_path, profile, tools):
    sys.path.insert(0, str(ag.PLUGIN / "tools"))
    import antigravity as installer
    files = installer.candidate_files(ag.PLUGIN, Path(sys.executable), profile)
    hooks = json.loads(files["hooks.json"][0])["mycelium"]
    (entry,) = hooks["PreToolUse"]
    assert set(entry["matcher"].split("|")) == tools
    (handler,) = entry["hooks"]
    assert "mycelium-antigravity-pre.sh" in handler["command"]
    assert handler["timeout"] <= 10
    assert files["skills/core/hooks/mycelium-antigravity-pre.sh"][1] & 0o111


# ---- Correlation: Pre + Post produce a positive-proof observation ------------------------------

def run_analysis(repo):
    subprocess.run([sys.executable, "analysis.py"], cwd=repo, check=True)


def index_status(repo, path):
    result = li.query_path(li.load(repo, write=False), path)
    (write,) = result["writes"]
    return write


def test_ide_pre_and_post_produce_an_observed_sole_write(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    result, _ = run_wrapper(json.dumps(dict(payload, **command_call(repo, 1))), env={"HOME": str(home)})
    assert result.stdout == ALLOW
    run_analysis(repo)
    append(transcript, step_index=1, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0)
    ag.dispatch("PostToolUse", dict(payload, error="", **command_call(repo, 1)))
    session = f"{CID}-turn-0"
    (obs,) = io._read_lines(io.observations_path(repo / ".mycelium", ag.HOST, session))
    assert obs["observation_id"] == f"{ag.HOST}:{ag.observer_call_id(1)}"
    assert obs["status"] == "no-known-overlap", obs
    assert [c["path"] for c in obs["changes"]["created"]] == ["out.csv"]
    write = index_status(repo, "out.csv")
    assert write["attribution"] == "sole"
    assert write["record"]["status"] == "current-in-record", write


def test_ide_async_command_correlates_when_it_completes(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    run_wrapper(json.dumps(dict(payload, **command_call(repo, 1))), env={"HOME": str(home)})
    append(transcript, step_index=1, source="MODEL", type="RUN_COMMAND", status="RUNNING")
    ag.dispatch("PostToolUse", dict(payload, error="", **command_call(repo, 1)))
    session = f"{CID}-turn-0"
    assert not io.observations_path(repo / ".mycelium", ag.HOST, session).exists()
    run_analysis(repo)
    transcript.write_text(json.dumps(USER) + "\n" + json.dumps(
        {"step_index": 1, "source": "MODEL", "type": "RUN_COMMAND", "status": "DONE", "exit_code": 0}) + "\n")
    ag.dispatch("PreInvocation", dict(payload, initialNumSteps=2))
    (obs,) = io._read_lines(io.observations_path(repo / ".mycelium", ag.HOST, session))
    assert obs["observation_id"] == f"{ag.HOST}:{ag.observer_call_id(1)}"
    assert [c["path"] for c in obs["changes"]["created"]] == ["out.csv"]


def test_ide_edit_pre_and_post_close_the_interval_with_an_edit_observation(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    target = repo / "notes.txt"
    call = {"stepIdx": 1, "toolCall": {"name": "write_to_file", "args": {"TargetFile": str(target)}}}
    run_wrapper(json.dumps(dict(payload, **call)), env={"HOME": str(home)})
    target.write_text("hello\n")
    append(transcript, step_index=1, source="MODEL", type="CODE_ACTION", status="DONE")
    ag.dispatch("PostToolUse", dict(payload, error="", **call))
    session = f"{CID}-turn-0"
    (obs,) = io._read_lines(io.observations_path(repo / ".mycelium", ag.HOST, session))
    assert obs["status"] == "edit" and obs["outcome"] == "success"
    assert obs["observation_id"] == f"{ag.HOST}:{ag.observer_call_id(1)}"
    # Its interval is closed: a later command is not marked as overlapping it.
    run_wrapper(json.dumps(dict(payload, **command_call(repo, 2))), env={"HOME": str(home)})
    run_analysis(repo)
    append(transcript, step_index=2, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0)
    ag.dispatch("PostToolUse", dict(payload, error="", **command_call(repo, 2)))
    command_obs = io._read_lines(io.observations_path(repo / ".mycelium", ag.HOST, session))[-1]
    assert command_obs["status"] == "no-known-overlap", command_obs


def test_post_without_pre_is_unavailable_not_silent(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    run_analysis(repo)
    append(transcript, step_index=1, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0)
    ag.dispatch("PostToolUse", dict(payload, error="", **command_call(repo, 1)))
    (obs,) = io._read_lines(io.observations_path(repo / ".mycelium", ag.HOST, f"{CID}-turn-0"))
    assert (obs["status"], obs["reason"]) == ("unavailable", "no-pre-snapshot")


def test_denied_call_interval_is_closed_at_stop(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    # Pre ran, but the user denied the call: no PostToolUse ever arrives.
    run_wrapper(json.dumps(dict(payload, **command_call(repo, 1))), env={"HOME": str(home)})
    session = f"{CID}-turn-0"
    assert list((repo / ".mycelium/io/open").rglob("*"))
    ag.dispatch("Stop", dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error=""))
    markers = [p for p in (repo / ".mycelium/io/open").rglob("*") if p.is_file()]
    assert markers == [], markers
    journal = (repo / ".mycelium/io/calls.ndjson").read_text()
    assert "turn-ended" in journal and session in journal


# ---- App profile: the command is settled from the App's own step record ------------------------

def app_command_call(step, command="python3 analysis.py"):
    return {"stepIdx": step, "toolCall": {"name": "run_command", "args": {
        "CommandLine": command, "WaitMsBeforeAsync": 5000, "toolSummary": "synthetic"}}}


@pytest.fixture
def app_project(project):
    from test_antigravity_app import CID as APP_CID
    from test_antigravity_app_native import Store
    home, repo = project
    transcript, payload = native(home, repo, parts=APP_ROOT, cid=APP_CID)
    ag.dispatch("PreInvocation", payload, host=ag.APP_HOST)
    db = Store(home)
    yield home, repo, payload, db, f"{APP_CID}-turn-0"
    db.close()


def test_app_pre_and_post_produce_an_observed_sole_write(app_project):
    from test_antigravity_app_native import command_payload
    home, repo, payload, db, session = app_project
    call = app_command_call(5)
    result, _ = run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    assert result.stdout == ALLOW
    run_analysis(repo)
    db.put(5, command_payload(str(repo), step=5, command="python3 analysis.py"))
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    (obs,) = io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session))
    assert obs["observation_id"] == f"{ag.APP_HOST}:{ag.observer_call_id(5)}"
    assert obs["status"] == "no-known-overlap", obs
    write = index_status(repo, "out.csv")
    assert write["attribution"] == "sole"
    assert write["record"]["status"] == "current-in-record", write


def test_app_bypass_or_daemon_command_takes_no_snapshot_but_is_journaled_background(app_project):
    home, repo, payload, db, session = app_project
    for step, extra in ((5, {"BypassSandbox": True}), (6, {"IsDaemon": True})):
        call = app_command_call(step)
        call["toolCall"]["args"].update(extra)
        assert ag.pre_tool_use(dict(payload, **call), ag.APP_HOST) == []
    assert not list((repo / ".mycelium/io").rglob("*.snap"))
    pres = [r for r in io._read_lines(repo / ".mycelium/io/calls.ndjson") if r.get("ev") == "pre"]
    assert [(r["call"], r["background"]) for r in pres] == [
        (ag.observer_call_id(5), True), (ag.observer_call_id(6), True)]


def test_app_edit_pre_and_post_record_a_successful_edit(app_project):
    from test_antigravity_app_native import edit_args, edit_payload
    home, repo, payload, db, session = app_project
    target = repo / "notes.txt"
    call = {"stepIdx": 7, "toolCall": {"name": "write_file", "args": edit_args(str(target))}}
    run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    target.write_text("synthetic content")
    db.put(7, edit_payload(str(target), step=7))
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    (obs,) = io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session))
    assert (obs["status"], obs["outcome"]) == ("edit", "success")
    assert obs["observation_id"] == f"{ag.APP_HOST}:{ag.observer_call_id(7)}"
    assert not [p for p in (repo / ".mycelium/io/open").rglob("*") if p.is_file()]


@pytest.mark.parametrize("profile", ["ide", "app"])
def test_installed_package_doctors_and_its_pre_command_answers_allow(tmp_path, profile):
    sys.path.insert(0, str(ag.PLUGIN / "tools"))
    import antigravity as installer
    workspace = tmp_path / "ws"
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    target = workspace / ".agents/plugins/mycelium"
    installer.install(target, python=Path(sys.executable), profile=profile)
    assert installer.doctor(target, profile=profile)["ok"]
    (entry,) = json.loads((target / "hooks.json").read_text())["mycelium"]["PreToolUse"]
    command = entry["hooks"][0]["command"]
    for stdin in ("", "{}", "garbage"):
        started = time.monotonic()
        result = subprocess.run(command, shell=True, cwd=target, input=stdin, text=True,
                                capture_output=True, timeout=30)
        assert (result.returncode, result.stdout) == (0, ALLOW)
        assert time.monotonic() - started < HARD_BOUND
    # Running the hook leaves the package byte-identical (no __pycache__ drift breaks doctor).
    assert installer.doctor(target, profile=profile)["ok"]


# ---- A call the Pre does not snapshot but that can still run stays visible ---------------------

def assert_not_sole(repo, path="out.csv"):
    """The write happened while an unobserved call may have been running: never sole/current."""
    write = index_status(repo, path)
    assert not (write["attribution"] == "sole" and write["record"]["status"] == "current-in-record"), write
    return write


@pytest.mark.parametrize("extra", [{"IsDaemon": True}, {"BypassSandbox": True}, {"UnknownArg": 1}])
def test_app_unobserved_command_writes_are_not_credited_to_the_next_call(app_project, extra):
    # Review of #173: a daemon (or sandbox-bypassing) command left no interval, so its later
    # write was credited, sole and current-in-record, to the next foreground call.
    from test_antigravity_app_native import command_payload
    home, repo, payload, db, session = app_project
    daemon = app_command_call(5)
    daemon["toolCall"]["args"].update(extra)
    result, elapsed = run_wrapper(json.dumps(dict(payload, **daemon)), host=ag.APP_HOST, env={"HOME": str(home)})
    assert (result.stdout, result.returncode) == (ALLOW, 0) and elapsed < HARD_BOUND
    ag.dispatch("PostToolUse", dict(payload, error="", **daemon), host=ag.APP_HOST)
    foreground = app_command_call(6, command="echo hi")
    run_wrapper(json.dumps(dict(payload, **foreground)), host=ag.APP_HOST, env={"HOME": str(home)})
    run_analysis(repo)  # the daemon writes out.csv while step 6 runs
    db.put(6, command_payload(str(repo), step=6, command="echo hi"))
    ag.dispatch("PostToolUse", dict(payload, error="", **foreground), host=ag.APP_HOST)
    obs = io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session))[-1]
    assert obs["observation_id"] == f"{ag.APP_HOST}:{ag.observer_call_id(6)}"
    assert [o["call"] for o in obs["overlapping"]] == [ag.observer_call_id(5)], obs
    assert_not_sole(repo)
    # Turn end never claims a possibly still-running unobserved call has finished.
    ag.dispatch("Stop", dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error=""),
                host=ag.APP_HOST)
    assert "turn-ended" not in (repo / ".mycelium/io/calls.ndjson").read_text().split(
        ag.observer_call_id(5))[-1].split("\n")[0]
    assert_not_sole(repo)


def test_ide_command_outside_the_project_writes_are_not_credited_to_the_next_call(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    outside = command_call(repo, 1, command=f"sleep 2; cd '{repo}' && python3 analysis.py")
    outside["toolCall"]["args"]["Cwd"] = str(home)
    result, elapsed = run_wrapper(json.dumps(dict(payload, **outside)), env={"HOME": str(home)})
    assert (result.stdout, result.returncode) == (ALLOW, 0) and elapsed < HARD_BOUND
    append(transcript, step_index=1, source="MODEL", type="RUN_COMMAND", status="RUNNING")
    ag.dispatch("PostToolUse", dict(payload, error="", **outside))
    run_wrapper(json.dumps(dict(payload, **command_call(repo, 2, command="echo hi"))), env={"HOME": str(home)})
    run_analysis(repo)
    append(transcript, step_index=2, source="MODEL", type="RUN_COMMAND", status="DONE", exit_code=0)
    ag.dispatch("PostToolUse", dict(payload, error="", **command_call(repo, 2, command="echo hi")))
    obs = io._read_lines(io.observations_path(repo / ".mycelium", ag.HOST, f"{CID}-turn-0"))[-1]
    assert [o["call"] for o in obs["overlapping"]] == [ag.observer_call_id(1)], obs
    assert_not_sole(repo)


def test_command_after_its_work_cycle_ended_is_journaled_unobserved(project):
    home, repo = project
    transcript, payload = native(home, repo)
    ag.dispatch("PreInvocation", payload)
    state_path = next((repo / ".mycelium").rglob("native-state.json"))
    state = json.loads(state_path.read_text())
    state["turns"]["0"]["status"] = "accepted"
    state_path.write_text(json.dumps(state))
    assert ag.pre_tool_use(dict(payload, **command_call(repo, 1))) == []  # no snapshot
    journal = io._read_lines(repo / ".mycelium/io/calls.ndjson")
    (pre,) = [r for r in journal if r.get("ev") == "pre"]
    assert pre["call"] == ag.observer_call_id(1) and pre["background"] is True
    assert not list((repo / ".mycelium/io").rglob("*.snap"))


@pytest.mark.parametrize("name", ["notes.txt", "deps.lock"])
def test_app_edit_credited_by_stop_recovery_closes_its_interval_with_an_edit_observation(app_project, name):
    """Live App 2.34: the edit's PostToolUse arrived while its step row was still running,
    so only Stop-time recovery credited it. The Pre interval must still end in an edit
    observation, not `expire(turn-ended)`. A tracker-ignored path gets one too, as on
    the direct callback."""
    from test_antigravity_app_native import edit_args, edit_payload
    home, repo, payload, db, session = app_project
    target = repo / name
    call = {"stepIdx": 7, "toolCall": {"name": "write_file", "args": edit_args(str(target))}}
    run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    db.put(7, edit_payload(str(target), step=7), status=2)  # still running at the callback
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    store = io.observations_path(repo / ".mycelium", ag.APP_HOST, session)
    assert io._read_lines(store) == []
    target.write_text("synthetic content")
    db.put(7, edit_payload(str(target), step=7))
    stop = dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="", executionNum=0)
    append(Path(payload["transcriptPath"]), step_index=7, source="MODEL", type="GENERIC", status="DONE")
    ag.dispatch("Stop", stop, host=ag.APP_HOST)
    (obs,) = io._read_lines(store)
    assert (obs["status"], obs["outcome"]) == ("edit", "success"), obs
    assert obs["observation_id"] == f"{ag.APP_HOST}:{ag.observer_call_id(7)}"
    assert [c["path"] for c in obs["changes"]["edited"]] == [name]
    assert not [p for p in (repo / ".mycelium/io/open").rglob("*") if p.is_file()]
    events = [r["ev"] for r in io._read_lines(repo / ".mycelium/io/calls.ndjson")
              if r.get("call") == ag.observer_call_id(7)]
    assert "expire" not in events, events
    ag.dispatch("Stop", dict(stop, executionNum=1), host=ag.APP_HOST)
    assert len(io._read_lines(store)) == 1  # at most once per step


@pytest.mark.parametrize("case", ["failed-edit", "no-stop-time-left"])
def test_stop_recovery_observes_no_edit_it_cannot_prove_or_has_no_time_for(app_project, monkeypatch, case):
    from test_antigravity_app_native import edit_args, edit_payload
    home, repo, payload, db, session = app_project
    target = repo / "notes.txt"
    call = {"stepIdx": 7, "toolCall": {"name": "write_file", "args": edit_args(str(target))}}
    run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    db.put(7, edit_payload(str(target), step=7), status=2)
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    if case == "failed-edit":
        db.put(7, edit_payload(str(target), step=7, status=7, result=False))
    else:
        target.write_text("synthetic content")
        db.put(7, edit_payload(str(target), step=7))
        monkeypatch.setattr(ag, "APP_RECOVERY_OBSERVE_MIN_SECONDS", ag.APP_STOP_BUDGET_SECONDS)
    append(Path(payload["transcriptPath"]), step_index=7, source="MODEL", type="GENERIC", status="DONE")
    ag.dispatch("Stop", dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="",
                             executionNum=0), host=ag.APP_HOST)
    assert io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session)) == []
    turn = json.loads(next((repo / ".mycelium" / ag.APP_HOST).glob("*/native-state.json")).read_text())
    actions = turn["turns"]["0"]["actions"]
    assert "7:io-observer" not in actions
    # The no-time case is not vacuous: recovery did credit the edit's activity.
    assert actions.get(f"7:{ag.ACTIVITY_HOOK}") == (None if case == "failed-edit" else "done")


def test_stop_retry_never_observes_an_edit_whose_interval_an_earlier_stop_ended(app_project, monkeypatch):
    """Review P2: Stop 1 credits the edit but has no time to observe it, closes the
    interval as turn-ended, and is blocked. Stop 2 must not observe it: the file may
    since hold another call's bytes, and the expired interval records no overlap."""
    from test_antigravity_app_native import edit_args, edit_payload
    home, repo, payload, db, session = app_project
    target = repo / "notes.txt"
    call = {"stepIdx": 7, "toolCall": {"name": "write_file", "args": edit_args(str(target))}}
    run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    db.put(7, edit_payload(str(target), step=7), status=2)
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    target.write_text("synthetic content")
    db.put(7, edit_payload(str(target), step=7))
    append(Path(payload["transcriptPath"]), step_index=7, source="MODEL", type="GENERIC", status="DONE")
    stop = dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="", executionNum=0)
    with monkeypatch.context() as patch:
        patch.setattr(ag, "APP_RECOVERY_OBSERVE_MIN_SECONDS", ag.APP_STOP_BUDGET_SECONDS)
        assert ag.dispatch("Stop", stop, host=ag.APP_HOST)["decision"] == "continue"
    events = [r["ev"] for r in io._read_lines(repo / ".mycelium/io/calls.ndjson")
              if r.get("call") == ag.observer_call_id(7)]
    assert events[-1] == "expire", events
    target.write_text("bytes from a later call")
    ag.dispatch("Stop", dict(stop, executionNum=1), host=ag.APP_HOST)
    assert io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session)) == []


def test_optional_edit_observations_never_defer_another_roots_recovery(project, tmp_path, monkeypatch):
    """Observations are best effort; a later root's recovery batch is required. Slow
    observations in the first root must leave the second root its batch minimum."""
    from test_antigravity_app import CID as APP_CID
    from test_antigravity_app_native import Store, edit_args, edit_payload
    home, repo = project
    second = tmp_path / "second"
    second.mkdir()
    subprocess.run(["git", "init", "-q", str(second)], check=True)
    (second / ".living").mkdir()
    transcript, payload = native(home, repo, parts=APP_ROOT, cid=APP_CID)
    payload["workspacePaths"] = [str(repo), str(second)]
    ag.dispatch("PreInvocation", payload, host=ag.APP_HOST)
    db = Store(home)
    try:
        for step, root in ((5, repo), (6, repo), (7, second)):
            target = root / f"f{step}.txt"
            call = {"stepIdx": step, "toolCall": {"name": "write_file", "args": edit_args(str(target))}}
            ag.pre_tool_use(dict(payload, **call), ag.APP_HOST)
            target.write_text("synthetic content")
            db.put(step, edit_payload(str(target), step=step, call_id=f"call-{step}"))
        append(transcript, step_index=8, source="MODEL", type="GENERIC", status="DONE")
        clock = [1000.0]
        monkeypatch.setattr(ag.time, "monotonic", lambda: clock[0])
        original = ag.run_hook

        def slow(name, data, root, plugin, **kwargs):
            if name == "mycelium-io-observer.sh":
                clock[0] += kwargs.get("timeout", 12)  # each observation uses its whole allowance
            return original(name, data, root, plugin, **kwargs)
        monkeypatch.setattr(ag, "run_hook", slow)
        ag.dispatch("Stop", dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="",
                                 executionNum=0), host=ag.APP_HOST)
    finally:
        db.close()
    state = json.loads((second / ".mycelium" / ag.APP_HOST / APP_CID / "native-state.json").read_text())
    assert state["turns"]["0"]["edit_recovery"]["credited"] == [7], state["turns"]["0"]["edit_recovery"]


@pytest.mark.parametrize("case", ["edit-file", "overwritten-later"])
def test_stop_recovery_observes_unprovable_bytes_as_unknown_and_still_proves_other_writes(app_project, case):
    """Live App run ag-observer-7 (step 26): a recovered edit_file got no edit
    observation, so its expired interval left `coverage-unproven` on every earlier
    write in the turn. Stop recovery must observe it, with its bytes unknown (a
    Stop-time hash is never the step's), so other paths keep their proof and its
    own path never reaches current-in-record. The same holds for a write_file whose
    file no longer holds its Content."""
    from test_antigravity_app_native import command_payload, edit_args, edit_payload, replace_args
    from antigravity_app_store import EDIT_EDIT
    home, repo, payload, db, session = app_project
    # An earlier command's write elsewhere, observed directly (step 5).
    command = app_command_call(5)
    run_wrapper(json.dumps(dict(payload, **command)), host=ag.APP_HOST, env={"HOME": str(home)})
    run_analysis(repo)
    db.put(5, command_payload(str(repo), step=5, command="python3 analysis.py"))
    ag.dispatch("PostToolUse", dict(payload, error="", **command), host=ag.APP_HOST)
    # The edit (step 7): its callback arrives while the row is still running.
    target = repo / "MANIFEST.md"
    target.write_text("old text\n")
    name, args = ("edit_file", replace_args(str(target))) if case == "edit-file" else (
        "write_file", edit_args(str(target)))
    call = {"stepIdx": 7, "toolCall": {"name": name, "args": args}}
    run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    row = dict(name=name, args=args, **({"kind": EDIT_EDIT} if case == "edit-file" else {}))
    db.put(7, edit_payload(str(target), step=7, call_id="call-edit", **row), status=2)
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    target.write_text("new text\n" if case == "edit-file" else "bytes from a later call")
    db.put(7, edit_payload(str(target), step=7, call_id="call-edit", **row))
    append(Path(payload["transcriptPath"]), step_index=7, source="MODEL", type="GENERIC", status="DONE")
    ag.dispatch("Stop", dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="",
                             executionNum=0), host=ag.APP_HOST)
    turn = json.loads(next((repo / ".mycelium" / ag.APP_HOST).glob("*/native-state.json")).read_text())
    assert turn["turns"]["0"]["actions"].get(f"7:{ag.ACTIVITY_HOOK}") == "done"  # recovery credited it
    observations = io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session))
    (edit,) = [o for o in observations if o["observation_id"] == f"{ag.APP_HOST}:{ag.observer_call_id(7)}"]
    assert (edit["status"], edit["outcome"]) == ("edit", "success"), edit
    (written,) = edit["changes"]["edited"]
    assert written["path"] == "MANIFEST.md"
    assert written["after"]["sha256"] is None and "identity" not in written["after"], written
    events = [r["ev"] for r in io._read_lines(repo / ".mycelium/io/calls.ndjson")
              if r.get("call") == ag.observer_call_id(7)]
    assert "expire" not in events, events
    # 2. The earlier command's write elsewhere keeps its positive proof.
    write = index_status(repo, "out.csv")
    assert (write["attribution"], write["record"]["status"]) == ("sole", "current-in-record"), write
    # 3. The edited path itself is never current-in-record.
    answer = li.query_path(li.load(repo, write=False), "MANIFEST.md")
    statuses = [w["record"]["status"] for w in answer["writes"]]
    assert statuses and "current-in-record" not in statuses, answer


def test_run_hook_kill_group_reaps_a_child_left_running_at_timeout(tmp_path):
    """Codex review P2: a timed-out observer hook must not leave its Python child
    writing after the adapter has moved on."""
    plugin = tmp_path / "plugin"
    hooks = plugin / "skills/core/hooks"
    hooks.mkdir(parents=True)
    marker = tmp_path / "late-write"
    (hooks / "slow.sh").write_text(
        f'"{sys.executable}" -c "import time; time.sleep(1.5); open({str(marker)!r}, \'w\').write(\'late\')"\n')
    root = tmp_path / "repo"
    root.mkdir()
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ag.run_hook("slow.sh", {"session_id": "s"}, root, plugin, timeout=0.3, kill_group=True)
    assert time.monotonic() - started < 1.5
    time.sleep(2.0)
    assert not marker.exists()


def _recovered_write(app_project, content_on_disk):
    from test_antigravity_app_native import edit_args, edit_payload
    home, repo, payload, db, session = app_project
    target = repo / "notes.txt"
    call = {"stepIdx": 7, "toolCall": {"name": "write_file", "args": edit_args(str(target))}}
    run_wrapper(json.dumps(dict(payload, **call)), host=ag.APP_HOST, env={"HOME": str(home)})
    db.put(7, edit_payload(str(target), step=7), status=2)
    ag.dispatch("PostToolUse", dict(payload, error="", **call), host=ag.APP_HOST)
    content_on_disk(target)
    db.put(7, edit_payload(str(target), step=7))
    append(Path(payload["transcriptPath"]), step_index=7, source="MODEL", type="GENERIC", status="DONE")
    started = time.monotonic()
    ag.dispatch("Stop", dict(payload, fullyIdle=True, terminationReason="NO_TOOL_CALL", error="",
                             executionNum=0), host=ag.APP_HOST)
    return io._read_lines(io.observations_path(repo / ".mycelium", ag.APP_HOST, session)), \
        time.monotonic() - started


def test_stop_recovery_digest_check_never_blocks_on_a_fifo(app_project):
    """Codex review P2: the digest check runs in the Stop process; a FIFO swapped in
    for the written file must not block it on open."""
    import signal

    def hung(*_):
        raise TimeoutError("Stop blocked on the FIFO")
    previous = signal.signal(signal.SIGALRM, hung)
    signal.setitimer(signal.ITIMER_REAL, 30)  # a regression fails here instead of hanging the worker
    try:
        observations, elapsed = _recovered_write(app_project, lambda t: os.mkfifo(t))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert elapsed < 20, elapsed
    # Unprovable bytes: observed as an edit of the path, with no hash.
    assert all(w["after"]["sha256"] is None for o in observations for w in o["changes"]["edited"])


def test_observer_publishes_no_hash_when_the_file_changed_after_the_digest_check(app_project, monkeypatch):
    """Codex review P2: a rewrite between the adapter's digest check and the
    observer's own hash must not publish the rewriter's bytes as the step's."""
    import hashlib
    expected = hashlib.sha256(b"synthetic content").hexdigest()
    monkeypatch.setattr(ag, "file_sha256", lambda path, **_: expected)  # the check saw the step's bytes
    (obs,), _ = _recovered_write(app_project, lambda t: t.write_text("bytes from a later call"))
    (written,) = obs["changes"]["edited"]
    assert written["path"] == "notes.txt"
    assert written["after"]["sha256"] is None and written["after"]["hash_status"] == "unavailable"


def test_observer_publishes_the_hash_when_it_matches_the_expected_digest(app_project):
    import hashlib
    (obs,), _ = _recovered_write(app_project, lambda t: t.write_text("synthetic content"))
    (written,) = obs["changes"]["edited"]
    assert written["after"]["sha256"] == hashlib.sha256(b"synthetic content").hexdigest()


def test_expected_digest_is_honoured_only_on_the_antigravity_app_host(tmp_path):
    """Codex review P3: a native (Claude) edit response carrying the adapter's key
    must not change what the shared observer records."""
    import hashlib
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "f.txt").write_text("real bytes")
    (repo / ".mycelium/io").mkdir(parents=True)
    for host in ("claude", "antigravity-app"):
        payload = {"hook_event_name": "PostToolUse", "session_id": f"s-{host}", "tool_name": "Write",
                   "tool_use_id": "t1", "cwd": str(repo), "tool_input": {"file_path": str(repo / "f.txt")},
                   "tool_response": {"success": True, "exit_code": 0, "mycelium_expected_sha256": "0" * 64,
                                     "mycelium_bytes_unknown": True}}
        io.hook(repo / ".mycelium", repo, host, json.dumps(dict(payload, hook_event_name="PreToolUse")))
        io.hook(repo / ".mycelium", repo, host, json.dumps(payload))
    real = hashlib.sha256(b"real bytes").hexdigest()
    hashes = {}
    for host in ("claude", "antigravity-app"):
        for obs in io._read_lines(io.observations_path(repo / ".mycelium", host, f"s-{host}")):
            hashes[host] = obs["changes"]["edited"][0]["after"]["sha256"]
    assert hashes == {"claude": real, "antigravity-app": None}, hashes


def test_slow_digest_check_leaves_finalization_its_reserve(app_project, monkeypatch):
    """Codex review P2: the observer's allowance is recomputed after the digest
    check; a check that used the spare time skips the optional observation."""
    clock = [1000.0]
    monkeypatch.setattr(ag.time, "monotonic", lambda: clock[0])
    original = ag.file_sha256

    def slow(path, **kwargs):
        clock[0] += ag.APP_STOP_BUDGET_SECONDS - ag.APP_FINALIZE_RESERVE_SECONDS
        return original(path, **kwargs)
    monkeypatch.setattr(ag, "file_sha256", slow)
    observations, _ = _recovered_write(app_project, lambda t: t.write_text("synthetic content"))
    assert observations == []
