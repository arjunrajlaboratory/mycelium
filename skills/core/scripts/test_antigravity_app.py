"""Antigravity App 2.31.0 foundation: host identity, isolation, reads, and unresolved commands.

Fixtures are synthetic. They use only the summarized A0 interface shape
(a1-observed-interface-r1.json): App native root ``~/.gemini/antigravity/brain/<uuid>``,
nested ``.system_generated/logs/transcript_full.jsonl``, planner ``read_file``/``Path`` and
``run_command`` without ``Cwd``, a MODEL ``GENERIC``/``DONE`` result step with no structured
exit code, and natural Stop ``fullyIdle=true``/``NO_TOOL_CALL``/empty error. The hook-side
read argument key was not captured natively; ``Path`` here is a conditional fixture value.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

import antigravity_hooks as ag

HOOKS = ag.PLUGIN / "skills/core/hooks"
CID = "5f0c2b1e-7d4a-4c3e-9a51-2b8e6f0d4c17"  # synthetic, not a native identifier
APP_ROOT = (".gemini", "antigravity", "brain")
IDE_ROOT = (".gemini", "antigravity-ide", "brain")


def tree(path):
    """Every path, kind, and byte under path (for zero-write assertions)."""
    if not path.exists():
        return None
    out = {}
    for item in sorted(path.rglob("*")):
        kind = "link" if item.is_symlink() else "dir" if item.is_dir() else "file"
        out[item.relative_to(path).as_posix()] = (kind, item.read_bytes() if kind == "file" else None)
    return out


def write_transcript(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


USER = {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "status": "DONE"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "research α"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    return home, repo


def native_payload(home, repo, parts, cid=CID, records=(USER,), name="transcript_full.jsonl"):
    artifact = home.joinpath(*parts, cid)
    transcript = artifact / ".system_generated/logs" / name
    write_transcript(transcript, records)
    return {"conversationId": cid, "workspacePaths": [str(repo)], "artifactDirectoryPath": str(artifact),
            "transcriptPath": str(transcript), "invocationNum": 0, "initialNumSteps": 1,
            "lastUserInput": "synthetic", "modelName": "gemini-4-argon-medium"}


# ---- Red 1: native cross-host transcript acceptance --------------------------------------------

def test_red_ide_profile_refuses_app_native_transcript_before_any_write(home, monkeypatch):
    home, repo = home
    calls = []
    monkeypatch.setattr(ag, "run_hook", lambda *a, **k: calls.append(a) or {})
    payload = native_payload(home, repo, APP_ROOT)
    before = tree(repo)
    with pytest.raises(ValueError):
        ag.dispatch("PreInvocation", payload)  # default (IDE) profile
    assert tree(repo) == before and not (repo / ".mycelium").exists() and calls == []


# ---- Red 2: shared host fallback --------------------------------------------------------------

def shell_host(host):
    env = dict(os.environ, MYCELIUM_HOOK_HOST=host)
    return subprocess.run(["bash", "-c", f'source "{HOOKS}/mycelium-hook-lib.sh"; mycelium_hook_host'],
                          text=True, capture_output=True, env=env, timeout=30).stdout.strip()


def test_red_shared_hooks_recognize_app_host_instead_of_falling_back_to_claude():
    assert shell_host("antigravity-app") == "antigravity-app"
    # Unknown hosts keep the historical Claude default.
    assert shell_host("antigravity") == "claude"


def test_red_app_strict_capture_is_not_lost_through_claude_fallback(home, tmp_path):
    home, repo = home
    session = f"{CID}-turn-0"
    env = dict(os.environ, MYCELIUM_HOOK_HOST="antigravity-app", MYCELIUM_PLUGIN_ROOT=str(ag.PLUGIN),
               MYCELIUM_STRICT_CAPTURE="1", MYCELIUM_DATA_HELPER=str(tmp_path / "missing-extractor.py"))
    start = subprocess.run(["bash", str(HOOKS / "mycelium-health.sh")], input=json.dumps(
        {"cwd": str(repo), "session_id": session, "source": "startup"}), text=True, capture_output=True,
        cwd=repo, env=env, timeout=60)
    assert start.returncode == 0, start.stderr
    assert (repo / ".mycelium/run/antigravity-app" / session / "active-session-log.tmp").is_file()
    assert not (repo / ".mycelium/run/claude").exists()
    data = {"cwd": str(repo), "session_id": session, "tool_name": "Bash",
            "tool_input": {"command": "python3 analysis.py"}, "tool_response": {}}
    result = subprocess.run(["bash", str(HOOKS / "mycelium-data-tracker.sh")], input=json.dumps(data),
                            text=True, capture_output=True, cwd=repo, env=env, timeout=60)
    assert result.returncode == 70 and "extractor not found" in result.stderr


# ---- Host identity matrix: exact native root, UUID, artifact and transcript -------------------

def app(event, payload, **kw):
    return ag.dispatch(event, payload, host=ag.APP_HOST, **kw)


@pytest.fixture
def doubles(monkeypatch):
    calls = []

    def hook(name, data, root, plugin, **kwargs):
        calls.append((ag.active_host(), name, data))
        if name == "mycelium-health.sh":
            return {"hookSpecificOutput": {"additionalContext": "NATIVE-START"}}
        return {}

    monkeypatch.setattr(ag, "run_hook", hook)
    return calls


def mutate(home, payload, case, tmp_path):
    """Return (host, payload) for one negative identity case; files are synthetic."""
    p = dict(payload)
    artifact, transcript = Path(p["artifactDirectoryPath"]), Path(p["transcriptPath"])

    def relocate(parts, cid=CID, name="transcript_full.jsonl"):
        other = native_payload(home, Path(p["workspacePaths"][0]), parts, cid=cid, name=name)
        return dict(p, artifactDirectoryPath=other["artifactDirectoryPath"], transcriptPath=other["transcriptPath"])

    if case == "app-host-ide-root":
        return ag.APP_HOST, relocate(IDE_ROOT)
    if case == "ide-host-app-root":
        return ag.HOST, p
    if case == "cli-root":
        return ag.APP_HOST, relocate((".gemini", "antigravity-cli", "brain"))
    if case == "gemini-tmp-root":
        return ag.APP_HOST, relocate((".gemini", "tmp", "brain"))
    if case == "arbitrary-suffix":
        return ag.APP_HOST, relocate(("elsewhere", ".gemini", "antigravity", "brain"))
    if case == "outside-home-suffix":
        other = tmp_path / "outside/.gemini/antigravity/brain" / CID / ".system_generated/logs/transcript_full.jsonl"
        write_transcript(other, [USER])
        return ag.APP_HOST, dict(p, artifactDirectoryPath=str(other.parents[2]), transcriptPath=str(other))
    if case == "mixed-transcript-ide":
        return ag.APP_HOST, dict(p, transcriptPath=relocate(IDE_ROOT)["transcriptPath"])
    if case == "transcript-outside-artifact":
        stray = artifact / "transcript_full.jsonl"
        write_transcript(stray, [USER])
        return ag.APP_HOST, dict(p, transcriptPath=str(stray))
    if case == "unobserved-transcript-name":
        return ag.APP_HOST, relocate(APP_ROOT, name="transcript.jsonl")
    if case == "trailing-slash":
        return ag.APP_HOST, dict(p, artifactDirectoryPath=str(artifact) + "/")
    if case == "double-slash":
        return ag.APP_HOST, dict(p, transcriptPath=str(transcript).replace("/logs/", "//logs/"))
    if case == "dot-segment":
        return ag.APP_HOST, dict(p, artifactDirectoryPath=str(artifact.parent) + "/./" + CID)
    if case == "other-uuid-artifact":
        return ag.APP_HOST, relocate(APP_ROOT, cid=str(uuid.uuid4()))
    if case == "uppercase-uuid":
        return ag.APP_HOST, dict(p, conversationId=CID.upper())
    if case == "linked-host-root":
        real, moved = home / ".gemini/antigravity", home / "real-antigravity"
        real.rename(moved)
        real.symlink_to(moved)
        return ag.APP_HOST, p
    if case == "linked-conversation":
        moved = home / "real-conversation"
        artifact.rename(moved)
        artifact.symlink_to(moved)
        return ag.APP_HOST, p
    if case == "linked-transcript":
        moved = home / "real-transcript.jsonl"
        transcript.rename(moved)
        transcript.symlink_to(moved)
        return ag.APP_HOST, p
    if case == "missing-paths":
        p.pop("transcriptPath")
        return ag.APP_HOST, p
    if case == "non-string-path":
        return ag.APP_HOST, dict(p, artifactDirectoryPath=[str(artifact)])
    if case == "unknown-host":
        return "antigravity", p
    raise AssertionError(case)


IDENTITY_CASES = ["app-host-ide-root", "ide-host-app-root", "cli-root", "gemini-tmp-root", "arbitrary-suffix",
                  "outside-home-suffix", "mixed-transcript-ide", "transcript-outside-artifact",
                  "unobserved-transcript-name", "trailing-slash", "double-slash", "dot-segment",
                  "other-uuid-artifact", "uppercase-uuid", "linked-host-root", "linked-conversation",
                  "linked-transcript", "missing-paths", "non-string-path", "unknown-host"]


def test_app_exact_native_conversation_is_admitted(home, doubles):
    home, repo = home
    response = app("PreInvocation", native_payload(home, repo, APP_ROOT))
    message = response["injectSteps"][0]["ephemeralMessage"]
    assert "MYCELIUM — Antigravity App" in message and "NATIVE-START" in message
    assert [(h, n) for h, n, _ in doubles] == [(ag.APP_HOST, "mycelium-health.sh")]
    assert (repo / ".mycelium/antigravity-app" / CID / "native-state.json").is_file()
    assert not (repo / ".mycelium/antigravity-ide").exists()


@pytest.mark.parametrize("case", IDENTITY_CASES)
@pytest.mark.parametrize("event", ["PreInvocation", "PostToolUse", "Stop"])
def test_foreign_or_inexact_native_identity_causes_zero_writes(home, doubles, tmp_path, case, event):
    home, repo = home
    host, payload = mutate(home, native_payload(home, repo, APP_ROOT), case, tmp_path)
    payload = dict(payload, stepIdx=2, toolCall={"name": "run_command", "args": {"CommandLine": "true"}},
                   fullyIdle=True, terminationReason="NO_TOOL_CALL", error="")
    before_repo, before_home = tree(repo), tree(home)
    with pytest.raises((ValueError, OSError)):
        ag.dispatch(event, payload, host=host)
    assert tree(repo) == before_repo and tree(home) == before_home and doubles == []


@pytest.mark.parametrize("opt_in", ["missing-living", "not-git", "linked-living"])
def test_missing_or_unsafe_opt_in_is_a_zero_write_no_op_or_refusal(home, doubles, tmp_path, opt_in):
    home, repo = home
    if opt_in == "missing-living":
        (repo / ".living").rmdir()
    elif opt_in == "not-git":
        repo = tmp_path / "scratch"
        (repo / ".living").mkdir(parents=True)
    else:
        (repo / ".living").rmdir()
        (tmp_path / "elsewhere").mkdir()
        (repo / ".living").symlink_to(tmp_path / "elsewhere")
    before = tree(repo)
    payload = native_payload(home, repo, APP_ROOT)
    if opt_in == "linked-living":
        with pytest.raises(ValueError):
            app("PreInvocation", payload)
    else:
        assert app("PreInvocation", payload) == {}
    assert tree(repo) == before and doubles == []


# ---- Same UUID and user step: App and IDE keep separate state, locks, and receipts -------------

def test_same_uuid_and_step_in_app_and_ide_are_isolated(home, doubles, monkeypatch):
    home, repo = home
    locks = []
    real_lock = ag.durable_lock

    def recording(root, name, *a, **k):
        locks.append(name)
        return real_lock(root, name, *a, **k)

    monkeypatch.setattr(ag, "durable_lock", recording)
    app_payload = native_payload(home, repo, APP_ROOT)
    ide_payload = native_payload(home, repo, IDE_ROOT)
    assert ide_payload["conversationId"] == app_payload["conversationId"]
    app("PreInvocation", app_payload)
    app_state = repo / ".mycelium/antigravity-app" / CID / "native-state.json"
    app_bytes = app_state.read_bytes()
    ag.dispatch("PreInvocation", ide_payload)
    ide_state = repo / ".mycelium/antigravity-ide" / CID / "native-state.json"
    assert app_state.read_bytes() == app_bytes and ide_state.is_file()
    assert locks == [f"antigravity-app-{CID}.lock", f"antigravity-{CID}.lock"]
    assert [(h, d["session_id"]) for h, n, d in doubles] == [
        (ag.APP_HOST, f"{CID}-turn-0"), (ag.HOST, f"{CID}-turn-0")]
    session = f"{CID}-turn-0"
    for host in (ag.APP_HOST, ag.HOST):
        (repo / ".mycelium/run" / host / session).mkdir(parents=True)
    # A receipt is bound to its own host: the App receipt never accepts the IDE transaction.
    ag.record_accepted_stop(repo, session, ag.APP_HOST)
    assert ag.accepted_stop(repo, session, ag.APP_HOST)
    assert not ag.accepted_stop(repo, session, ag.HOST)
    receipt = repo / ".mycelium/run" / ag.HOST / session / "accepted-stop.json"
    receipt.write_bytes((repo / ".mycelium/run" / ag.APP_HOST / session / "accepted-stop.json").read_bytes())
    with pytest.raises(ValueError, match="identity mismatch"):
        ag.accepted_stop(repo, session, ag.HOST)


def legacy_ide_state():
    return {"schema": 1, "conversationId": CID, "turns": {"0": {
        "status": "active", "actions": {"startup": "done"}, "retries": 0, "context": [],
        "uncertain": False, "pending_commands": {}}}}


def test_app_ignores_same_id_legacy_ide_state_and_refuses_unowned_legacy_app_state(home, doubles):
    home, repo = home
    ide_state = repo / ".mycelium/antigravity-ide" / CID / "native-state.json"
    ide_state.parent.mkdir(parents=True)
    ide_state.write_text(json.dumps(legacy_ide_state()))
    ide_before = ide_state.read_bytes()
    app("PreInvocation", native_payload(home, repo, APP_ROOT))
    assert ide_state.read_bytes() == ide_before
    app_state = repo / ".mycelium/antigravity-app" / CID / "native-state.json"
    assert json.loads(app_state.read_text())["schema"] == 2
    # A schema-1 file in the App namespace has no App ownership proof: refuse and retain bytes.
    app_state.write_text(json.dumps(legacy_ide_state()))
    before = tree(repo)
    with pytest.raises(ValueError, match="Invalid native state"):
        app("PreInvocation", native_payload(home, repo, APP_ROOT))
    assert tree(repo) == before


def run_shell(hook, data, repo, host, **extra):
    env = dict(os.environ, MYCELIUM_HOOK_HOST=host, MYCELIUM_PLUGIN_ROOT=str(ag.PLUGIN), **extra)
    return subprocess.run(["bash", str(HOOKS / hook)], input=json.dumps(data), text=True,
                          capture_output=True, cwd=repo, env=env, timeout=60)


@pytest.mark.parametrize("host", [ag.APP_HOST, ag.HOST])
def test_flat_legacy_transaction_is_never_adopted_by_the_app(home, host):
    home, repo = home
    session = f"{CID}-turn-0"
    state = repo / ".mycelium"
    (repo / ".living/log").mkdir(parents=True)
    state.mkdir()
    log = repo / ".living/log/2026-01-01-legacy.md"
    log.write_text("# legacy\n")
    (state / "active-session-log.tmp").write_text(f"{log}\n1700000000\nowner-id-v1\n")
    (state / "active-session-owner-id.tmp").write_text(session + "\n")
    flat = {name: (state / name).read_bytes() for name in ("active-session-log.tmp", "active-session-owner-id.tmp")}
    data = {"cwd": str(repo), "session_id": session, "tool_name": "Read",
            "tool_input": {"file_path": str(log)}, "tool_response": {}}
    result = run_shell("mycelium-read-tracker.sh", data, repo, host)
    assert result.returncode == 0, result.stderr
    assert {name: (state / name).read_bytes() for name in flat} == flat
    if host == ag.APP_HOST:  # the App never routes to a flat owner of the same identity
        assert not (state / "mycelium-read-access.log").exists()
    else:  # existing IDE rolling-upgrade compatibility is unchanged
        assert (state / "mycelium-read-access.log").exists()


# ---- Reads: observed read_file/Path only; nothing guessed ------------------------------------

@pytest.fixture
def started(home, doubles):
    home, repo = home
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    doubles.clear()
    state = repo / ".mycelium/antigravity-app" / CID / "native-state.json"
    return home, repo, payload, doubles, state


def post(payload, step, name, args, error=""):
    return dict(payload, stepIdx=step, error=error, toolCall={"name": name, "args": args})


def test_app_read_file_path_inside_opted_in_root_is_tracked(started):
    home, repo, payload, calls, state = started
    target = repo / "data.csv"
    target.write_text("x\n1\n")
    assert app("PostToolUse", post(payload, 2, "read_file", {"Path": str(target)})) == {}
    assert [(h, n, d["tool_name"], d["tool_input"]) for h, n, d in calls] == [
        (ag.APP_HOST, "mycelium-read-tracker.sh", "Read", {"file_path": str(target)})]
    assert json.loads(state.read_text())["turns"]["0"]["actions"]["2:mycelium-read-tracker.sh"] == "done"


READ_SHAPES = ["no-args", "args-not-dict", "path-missing", "path-not-string", "relative", "traversal",
               "outside-root", "linked-ancestor", "absolute-path-key", "native-error", "ide-view-file-name",
               "unrelated-tool", "edit-tool"]


@pytest.mark.parametrize("shape", READ_SHAPES)
def test_unsupported_or_unbound_app_reads_are_not_tracked(started, tmp_path, shape):
    home, repo, payload, calls, state = started
    inside = str(repo / "data.csv")
    (tmp_path / "outside").mkdir()
    (repo / "linked").symlink_to(tmp_path / "outside")
    name, error = "read_file", ""
    args = {"no-args": None, "args-not-dict": [inside], "path-missing": {}, "path-not-string": {"Path": 7},
            "relative": {"Path": "data.csv"}, "traversal": {"Path": str(repo / "sub/../data.csv")},
            "outside-root": {"Path": str(tmp_path / "outside/x.csv")},
            "linked-ancestor": {"Path": str(repo / "linked/x.csv")},
            "absolute-path-key": {"AbsolutePath": inside}}.get(shape, {"Path": inside})
    if shape == "native-error":
        error = "read failed"
    elif shape == "ide-view-file-name":
        name, args = "view_file", {"AbsolutePath": inside}
    elif shape == "unrelated-tool":
        name = "list_dir"
    elif shape == "edit-tool":
        name, args = "write_to_file", {"TargetFile": inside}
    call = {"name": name} if shape == "no-args" else {"name": name, "args": args}
    before = tree(repo)
    assert app("PostToolUse", dict(payload, stepIdx=2, error=error, toolCall=call)) == {}
    assert calls == [] and tree(repo) == before


def test_ide_view_file_is_unchanged_and_ide_ignores_app_read_shape(home, doubles):
    home, repo = home
    payload = native_payload(home, repo, IDE_ROOT)
    ag.dispatch("PreInvocation", payload)
    doubles.clear()
    target = str(repo / "data.csv")
    ag.dispatch("PostToolUse", post(payload, 2, "read_file", {"Path": target}))
    assert doubles == []
    ag.dispatch("PostToolUse", post(payload, 3, "view_file", {"AbsolutePath": target}))
    assert [(h, n, d["tool_input"]) for h, n, d in doubles] == [
        (ag.HOST, "mycelium-read-tracker.sh", {"file_path": target})]


# ---- Commands: no proven workspace, CWD, or exit evidence stays unresolved -----------------------

def stop(payload, **kw):
    return dict(payload, **dict(dict(fullyIdle=True, terminationReason="NO_TOOL_CALL", error="", executionNum=0), **kw))


@pytest.mark.parametrize("variant", ["observed", "planner-cwd", "counterfeit-structured-step", "native-error",
                                     "malformed-call", "nameless-call"])
def test_app_command_without_authoritative_evidence_blocks_clean_stop(started, variant):
    home, repo, payload, calls, state = started
    steps = [USER, {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE", "status": "DONE"},
             {"step_index": 2, "source": "MODEL", "type": "GENERIC", "status": "DONE",
              "content": "Created At: <time>\nCompleted At: <time>\nCommand exited with code 0.\n\n"
                         "No output from the command."}]
    if variant == "counterfeit-structured-step":
        steps[2] = {"step_index": 2, "source": "MODEL", "type": "RUN_COMMAND", "status": "DONE", "exit_code": 0}
    write_transcript(Path(payload["transcriptPath"]), steps)
    args = {"CommandLine": "python3 analysis.py", "WaitMsBeforeAsync": 5000, "toolSummary": "synthetic"}
    if variant == "planner-cwd":
        args["Cwd"] = str(repo)
    event = post(payload, 2, "run_command", args, error="exit 1" if variant == "native-error" else "")
    if variant == "malformed-call":
        event["toolCall"] = "run_command"
    elif variant == "nameless-call":
        event["toolCall"] = {"args": args}
    assert app("PostToolUse", event) == {}
    turn = json.loads(state.read_text())["turns"]["0"]
    assert turn["unresolved_commands"] == {"2": ag.APP_UNRESOLVED} and turn["pending_commands"] == {}
    assert calls == []  # no lineage, no post-action, no success inferred
    message = app("PreInvocation", dict(payload, initialNumSteps=3))["injectSteps"][0]["ephemeralMessage"]
    assert "cannot verify where or how command steps 2 ran" in message
    for attempt in range(ag.MAX_STOP_RETRIES):
        response = app("Stop", stop(payload, executionNum=attempt))
        assert response["decision"] == "continue" and "command steps 2" in response["reason"]
    assert app("Stop", stop(payload, executionNum=9)) == {}
    turn = json.loads(state.read_text())["turns"]["0"]
    assert turn["status"] == "incomplete" and "command steps 2" in turn["reason"]
    assert not any(n == "mycelium-stop-check.sh" for _, n, _ in calls)


def test_app_command_is_unresolved_in_every_opted_in_root(home, doubles, tmp_path):
    home, repo = home
    second = tmp_path / "second"
    second.mkdir()
    subprocess.run(["git", "init", "-q", str(second)], check=True)
    (second / ".living").mkdir()
    payload = dict(native_payload(home, repo, APP_ROOT), workspacePaths=[str(repo), str(second)])
    app("PreInvocation", payload)
    app("PostToolUse", post(payload, 2, "run_command", {"CommandLine": "true"}))
    for root in (repo, second):
        turn = json.loads((root / ".mycelium/antigravity-app" / CID / "native-state.json").read_text())["turns"]["0"]
        assert turn["unresolved_commands"] == {"2": ag.APP_UNRESOLVED}


def test_malformed_unresolved_record_is_refused_and_retained(started):
    home, repo, payload, calls, state = started
    value = json.loads(state.read_text())
    value["turns"]["0"]["unresolved_commands"] = {"two": "x"}
    state.write_text(json.dumps(value))
    before = state.read_bytes()
    with pytest.raises(ValueError, match="unresolved native command"):
        app("Stop", stop(payload))
    assert state.read_bytes() == before


# ---- Real shared hooks: App lifecycle, exact-once Stop, lost response ------------------------------

@pytest.fixture
def real_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    repo = tmp_path / "research α"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    for name in ("learnings", "decisions", "conventions"):
        (repo / f".living/{name}.md").write_text(f"# {name}\n")
    (repo / ".gitignore").write_text(".mycelium/\n")
    (repo / "data.csv").write_text("x\n1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    run = repo / ".mycelium/run" / ag.APP_HOST / f"{CID}-turn-0"
    state = repo / ".mycelium/antigravity-app" / CID / "native-state.json"
    return repo, payload, run, state


def test_real_app_lifecycle_reads_and_accepts_stop_exactly_once(real_cycle, monkeypatch):
    repo, payload, run, state = real_cycle
    assert (run / "active-session-log.tmp").is_file()
    assert not (repo / ".mycelium/run/claude").exists() and not (repo / ".mycelium/run/antigravity-ide").exists()
    # The shared read tracker records knowledge-layer reads only.
    app("PostToolUse", post(payload, 2, "read_file", {"Path": str(repo / ".living/learnings.md")}))
    assert (run / "mycelium-read-access.log").read_text().split()[-1] == ".living/learnings.md"
    actual, checks = ag.subprocess.run, []

    def counting(command, *a, **k):
        if Path(command[-1]).name == "mycelium-stop-check.sh":
            checks.append(k["env"]["MYCELIUM_HOOK_HOST"])
        return actual(command, *a, **k)

    monkeypatch.setattr(ag.subprocess, "run", counting)
    assert app("Stop", stop(payload)) == {}
    receipt = json.loads((run / "accepted-stop.json").read_text())
    assert receipt == {"schema": 1, "host": ag.APP_HOST, "session_id": f"{CID}-turn-0", "status": "accepted"}
    assert not (run / "active-session-log.tmp").exists()
    assert json.loads(state.read_text())["turns"]["0"]["status"] == "accepted"
    assert app("Stop", stop(payload, executionNum=1)) == {}  # duplicate delivery
    assert checks == [ag.APP_HOST]


def test_real_app_stop_allows_slow_log_finalization(real_cycle, monkeypatch, tmp_path):
    repo, payload, run, state = real_cycle
    (repo / ".living/learnings.md").write_text("# learnings\n\n- Recorded work in this test.\n")
    finalizer = tmp_path / "slow_finalizer.py"
    original = ag.PLUGIN / "skills/core/scripts/finalize_session_log.py"
    finalizer.write_text(
        "import os\nimport sys\nimport time\n"
        "time.sleep(13)\n"
        f"os.execv(sys.executable, [sys.executable, {str(original)!r}, *sys.argv[1:]])\n"
    )
    monkeypatch.setenv("MYCELIUM_LOG_FINALIZER_HELPER", str(finalizer))
    assert app("Stop", stop(payload)) == {}
    assert (run / "accepted-stop.json").is_file()
    assert json.loads(state.read_text())["turns"]["0"]["status"] == "accepted"


def test_real_app_stop_unknown_outcome_reports_block_without_redispatch(real_cycle, monkeypatch):
    _, payload, run, state = real_cycle
    actual, calls = ag.subprocess.run, []

    def timed_out(command, *args, **kwargs):
        if Path(command[-1]).name == "mycelium-stop-check.sh":
            calls.append(command)
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return actual(command, *args, **kwargs)

    monkeypatch.setattr(ag.subprocess, "run", timed_out)
    result = app("Stop", stop(payload))
    assert result["decision"] == "continue" and "unresolved" in result["reason"]
    assert app("Stop", stop(payload, executionNum=1))["decision"] == "continue"
    assert len(calls) == 1 and not (run / "accepted-stop.json").exists()
    assert (run / "active-session-log.tmp").is_file()
    assert json.loads(state.read_text())["turns"]["0"]["uncertain"] is True


def test_app_stop_defers_when_time_was_spent_before_finalization(started, monkeypatch):
    _, _, payload, calls, state = started
    monkeypatch.setattr(ag, "APP_STOP_BUDGET_SECONDS", 0)
    before = list(calls)
    result = app("Stop", stop(payload))
    assert result["decision"] == "continue" and "No time remains" in result["reason"]
    assert calls == before
    turn = json.loads(state.read_text())["turns"]["0"]
    assert turn["status"] == "active" and "stop_attempt" not in turn and not turn["uncertain"]


def test_real_app_lost_stop_response_reconciles_without_redispatch(real_cycle, monkeypatch):
    repo, payload, run, state = real_cycle
    actual, calls = ag.subprocess.run, []

    def lose(command, *a, **k):
        result = actual(command, *a, **k)
        if Path(command[-1]).name == "mycelium-stop-check.sh":
            calls.append(result.returncode)
            raise subprocess.TimeoutExpired(command, 12)
        return result

    monkeypatch.setattr(ag.subprocess, "run", lose)
    assert app("Stop", stop(payload)) == {}
    assert app("Stop", stop(payload, executionNum=1)) == {}
    assert json.loads(state.read_text())["turns"]["0"]["status"] == "accepted"
    assert calls == [0]


def test_real_app_unresolved_command_never_reaches_lineage_or_acceptance(real_cycle):
    repo, payload, run, state = real_cycle
    write_transcript(Path(payload["transcriptPath"]), [USER, {"step_index": 2, "source": "MODEL",
                     "type": "GENERIC", "status": "DONE"}])
    app("PostToolUse", post(payload, 2, "run_command", {"CommandLine": "python3 analysis.py"}))
    for attempt in range(ag.MAX_STOP_RETRIES + 1):
        app("Stop", stop(payload, executionNum=attempt))
    assert json.loads(state.read_text())["turns"]["0"]["status"] == "incomplete"
    assert not (run / "accepted-stop.json").exists() and (run / "active-session-log.tmp").is_file()
    assert not (run / "mycelium-data-events.tmp").exists()
    assert not (repo / ".living/log/data-lineage").exists()


@pytest.mark.parametrize("helper", ["missing", "crash"])
def test_app_strict_capture_missing_or_failing_extractor_is_nonzero(real_cycle, tmp_path, helper):
    repo, payload, run, state = real_cycle
    path = tmp_path / "extractor.py"
    if helper == "crash":
        path.write_text("import sys\nprint('helper exploded', file=sys.stderr)\nraise SystemExit(3)\n")
    data = {"cwd": str(repo), "session_id": f"{CID}-turn-0", "tool_name": "Bash",
            "tool_input": {"command": "python3 analysis.py"}, "tool_response": {}}
    result = run_shell("mycelium-data-tracker.sh", data, repo, ag.APP_HOST,
                       MYCELIUM_STRICT_CAPTURE="1", MYCELIUM_DATA_HELPER=str(path))
    assert result.returncode != 0 and result.stdout == ""
    assert ("extractor not found" if helper == "missing" else "helper exploded") in result.stderr


# ---- Command line: explicit host, unknown host, receipt host ---------------------------------------

def cli(*args, stdin="", env=None):
    return subprocess.run([sys.executable, str(Path(ag.__file__)), *args], input=stdin, text=True,
                          capture_output=True, env=env or dict(os.environ), timeout=60)


def test_cli_host_selection_and_receipt_host(home):
    home, repo = home
    env = dict(os.environ, HOME=str(home), PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    payload = json.dumps(native_payload(home, repo, APP_ROOT))
    ide = cli("PreInvocation", stdin=payload, env=env)  # the default IDE profile refuses the App root
    assert ide.returncode == 1 and ide.stdout.strip() == "{}" and not (repo / ".mycelium").exists()
    unknown = cli("--host", "antigravity", "PreInvocation", stdin=payload, env=env)
    assert unknown.returncode == 1 and "Unsupported Antigravity host profile" in unknown.stderr
    ok = cli("--host", ag.APP_HOST, "PreInvocation", stdin=payload, env=env)
    assert ok.returncode == 0, ok.stderr
    assert "Antigravity App" in json.loads(ok.stdout)["injectSteps"][0]["ephemeralMessage"]
    session = f"{CID}-turn-0"
    (repo / ".mycelium/run" / ag.HOST / session).mkdir(parents=True, exist_ok=True)
    assert cli("--record-accepted-stop", str(repo), session, ag.APP_HOST).returncode == 0
    assert cli("--record-accepted-stop", str(repo), session).returncode == 0  # IDE form unchanged
    for host in (ag.APP_HOST, ag.HOST):
        assert json.loads((repo / ".mycelium/run" / host / session / "accepted-stop.json").read_text())["host"] == host
    bad = cli("--record-accepted-stop", str(repo), session, "claude")
    assert bad.returncode == 1 and not (repo / ".mycelium/run/claude").exists()
