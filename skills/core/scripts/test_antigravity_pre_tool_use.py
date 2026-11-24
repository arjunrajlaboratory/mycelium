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
                 {"name": "run_command", "args": {"CommandLine": "ls", "Cwd": "/"}},
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


def test_app_bypass_or_daemon_command_takes_no_snapshot(app_project):
    home, repo, payload, db, session = app_project
    for extra in ({"BypassSandbox": True}, {"IsDaemon": True}):
        call = app_command_call(5)
        call["toolCall"]["args"].update(extra)
        assert ag.pre_tool_use(dict(payload, **call), ag.APP_HOST) == []
    assert not (repo / ".mycelium/io").exists()


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
