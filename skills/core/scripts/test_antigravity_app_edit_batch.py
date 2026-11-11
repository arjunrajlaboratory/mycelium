"""App Report Stop batch repair (work order edit-batch v3).

The native Report run recovered seven edits with one tracker subprocess each and
timed out on the eighth, leaving its action running; a valid 96 KiB write_file
row was rejected by the generic 64 KiB text bound. These synthetic fixtures check
one acknowledged shared-tracker batch per root, exact batch validation in the
real tracker, and the FileEdit argument bound. Content canaries must never leave
the native row.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import antigravity_app_store as store
import antigravity_hooks as ag
from test_antigravity_app import APP_ROOT, CID, USER, app, home, native_payload, run_shell, stop  # noqa: F401
from test_antigravity_app_edit_recovery import (ACTIVITY, activity, actions, doubles, through,  # noqa: F401
                                                tracker_runs)
from test_antigravity_app_native import (Store, command_payload, edit_args, edit_event, edit_payload, native,  # noqa: F401
                                         state_of)

CANARY = "CONTENT-CANARY-7f3a"


def many(repo, db, count=9, start=60):
    paths = []
    for n in range(count):
        path = str(repo / f"reports/part-{n}.html")
        db.put(start + n, edit_payload(path, step=start + n, call_id=f"call-{n}"))
        paths.append(path)
    return paths


def large_args(path, size=96617):
    content = (CANARY + "<p>synthetic</p>") * (size // 30)
    return {"Path": path, "Content": content, "Overwrite": False}


def no_canary(*places):
    for place in places:
        if isinstance(place, Path):
            texts = [p.read_text(errors="replace") for p in place.rglob("*") if p.is_file()] if place.is_dir() \
                else [place.read_text(errors="replace")]
        else:
            texts = [json.dumps(place, default=str)]
        assert not any(CANARY in text for text in texts)


# ---- Red: many recovered edits use one tracker invocation ---------------------------------------

def test_red_nine_recovered_edits_use_one_acknowledged_tracker_invocation(native):
    home, repo, payload, calls, db = native
    paths = many(repo, db)
    through(payload, 140)
    assert app("Stop", stop(payload)) == {}
    assert tracker_runs(calls) == 1 and activity(calls) == paths
    turn = state_of(repo)
    assert actions(repo) == sorted(f"{s}:{ACTIVITY}" for s in range(60, 69))
    assert all(turn["actions"][f"{s}:{ACTIVITY}"] == "done" for s in range(60, 69))
    assert turn["edit_recovery"]["credited"] == list(range(60, 69)) and turn["status"] == "accepted"
    batch = next(d for _, n, d in calls if n == ACTIVITY)["mycelium_activity_batch"]
    assert batch["count"] == 9 and batch["sha256"] == hashlib.sha256("\n".join(paths).encode()).hexdigest()


def test_red_large_report_write_file_is_verified_without_exposing_content(native):
    home, repo, payload, calls, db = native
    path = str(repo / "analysis/report/report.html")
    args = large_args(path)
    assert len(json.dumps(args)) > 65536
    db.put(68, edit_payload(path, step=68, args=args, call_id="call-report"))
    found = store.edit(CID, 68, "write_file", path)
    assert found["path"] == path and found["kind"] == store.EDIT_CREATE
    no_canary(found)
    through(payload, 70)
    assert app("Stop", stop(payload)) == {}
    turn = state_of(repo)
    assert turn["edit_recovery"]["credited"] == [68] and turn["edit_recovery"]["rejected"] == {}
    no_canary(calls, repo / ".mycelium")


def test_large_write_file_direct_callback_is_credited_once_then_not_recovered(native):
    home, repo, payload, calls, db = native
    path = str(repo / "analysis/report/report.html")
    args = large_args(path)
    db.put(68, edit_payload(path, step=68, args=args))
    app("PostToolUse", edit_event(payload, path, step=68, args=args))
    through(payload, 70)
    app("Stop", stop(payload))
    assert tracker_runs(calls) == 1 and state_of(repo)["edit_recovery"]["credited"] == []
    no_canary([d for _, n, d in calls if n == ACTIVITY], repo / ".mycelium")


# ---- Argument bounds stay narrow ------------------------------------------------------------------

def test_overbound_edit_arguments_and_large_command_arguments_are_rejected(native, monkeypatch):
    home, repo, payload, calls, db = native
    path = str(repo / "report.html")
    db.put(6, edit_payload(path, step=6, args=large_args(path, 2000)))
    monkeypatch.setattr(store, "MAX_EDIT_ARGS", 1024)
    with pytest.raises(store.Unverified, match="text field 3 over bound"):
        store.edit(CID, 6, "write_file", path)
    line = "python3 analysis.py " + "x" * 70000
    db.put(7, command_payload(str(repo), step=7, command=line, args={"CommandLine": line}))
    with pytest.raises(store.Unverified, match="over bound"):
        store.command(CID, 7, line)


def test_large_arguments_of_an_unsupported_type_keep_the_generic_bound(native):
    home, repo, payload, calls, db = native
    path = str(repo / "report.html")
    db.put(6, edit_payload(path, step=6, args=large_args(path), type_url=store.RUN_COMMAND_TYPE))
    with pytest.raises(store.Unverified, match="unexpected type"):
        store.edit(CID, 6, "write_file", path)


# ---- Interrupted, unacknowledged or malformed batches are never replayed ---------------------------

@pytest.mark.parametrize("outcome", ["no-ack", "wrong-digest", "wrong-id", "partial-count", "timeout", "failure"])
def test_unconfirmed_batch_keeps_every_action_running_and_never_redispatches(native, monkeypatch, outcome):
    home, repo, payload, calls, db = native
    many(repo, db, count=3)
    through(payload, 70)
    attempts = []

    def hook(name, data, root, plugin, **kw):
        calls.append((ag.active_host(), name, data))
        if name != ACTIVITY:
            return {}
        attempts.append(data["mycelium_activity_batch"]["id"])
        batch = dict(data["mycelium_activity_batch"], status="recorded")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(name, kw["timeout"])
        if outcome == "failure":
            raise RuntimeError("mycelium-activity-tracker.sh failed (70)")
        return {"no-ack": {}, "wrong-digest": {"mycelium_activity_batch": dict(batch, sha256="0" * 64)},
                "wrong-id": {"mycelium_activity_batch": dict(batch, id="other")},
                "partial-count": {"mycelium_activity_batch": dict(batch, count=2)}}[outcome]

    monkeypatch.setattr(ag, "run_hook", hook)
    first = app("Stop", stop(payload))
    assert first["decision"] == "continue" and first["reason"] == ag.UNRESOLVED_HOOK
    for attempt in range(1, ag.MAX_STOP_RETRIES + 1):
        app("Stop", stop(payload, executionNum=attempt))
    turn = state_of(repo)
    assert len(attempts) == 1 and turn["uncertain"] is True and turn["status"] == "incomplete"
    assert all(turn["actions"][f"{s}:{ACTIVITY}"] == "running" for s in (60, 61, 62))
    assert turn["edit_recovery"]["credited"] == [] and "recovery batch" in turn["last_hook_error"]
    assert not any(n == "mycelium-stop-check.sh" for _, n, _ in calls)


def test_tracker_ignored_paths_are_not_sent_and_do_not_hold_stop(native):
    home, repo, payload, calls, db = native
    keep = str(repo / "reports/keep.html")
    for step, path in ((6, str(repo / "deps.lock")), (7, str(repo / "pkg/node_modules/x.js")),
                       (8, str(repo / ".codex/config.toml")), (9, keep)):
        db.put(step, edit_payload(path, step=step, call_id=f"call-{step}"))
    through(payload, 12)
    assert app("Stop", stop(payload)) == {}
    assert activity(calls) == [keep] and state_of(repo)["edit_recovery"]["credited"] == [9]
    assert actions(repo) == [f"9:{ACTIVITY}"]


def test_multi_root_recovery_runs_one_batch_per_root(home, doubles, tmp_path):
    home, repo = home
    second = tmp_path / "second"
    second.mkdir()
    subprocess.run(["git", "init", "-q", str(second)], check=True)
    (second / ".living").mkdir()
    payload = dict(native_payload(home, repo, APP_ROOT), workspacePaths=[str(repo), str(second)])
    app("PreInvocation", payload)
    doubles.clear()
    db = Store(home)
    try:
        for step, root in ((5, repo), (6, second), (7, repo), (8, second)):
            db.put(step, edit_payload(str(root / f"f{step}.md"), step=step, call_id=f"call-{step}"))
        through(payload, 10)
        app("Stop", stop(payload))
    finally:
        db.close()
    batches = {d["cwd"]: d["tool_input"]["file_paths"] for _, n, d in doubles if n == ACTIVITY}
    assert batches == {str(repo): [str(repo / "f5.md"), str(repo / "f7.md")],
                       str(second): [str(second / "f6.md"), str(second / "f8.md")]}


# ---- The real shared tracker: exact batch validation, legacy compatibility -------------------------

@pytest.fixture
def tracked(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    repo = (tmp_path / "research").resolve()
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    (repo / "reports").mkdir()
    session = f"{CID}-turn-0"
    start = run_shell("mycelium-health.sh", {"cwd": str(repo), "session_id": session, "source": "startup"},
                      repo, ag.APP_HOST)
    assert start.returncode == 0, start.stderr
    run = repo / ".mycelium/run" / ag.APP_HOST / session
    return repo, session, run


def batch_input(repo, session, paths, **override):
    digest = hashlib.sha256("\n".join(p for p in paths if isinstance(p, str)).encode()).hexdigest()
    batch = dict({"id": f"{session}-recovery-x", "count": len(paths), "sha256": digest}, **override)
    return {"cwd": str(repo), "session_id": session, "tool_name": "Write", "tool_input": {"file_paths": paths},
            "tool_response": {"success": True}, "mycelium_activity_batch": batch}


def test_real_tracker_records_a_valid_batch_and_acknowledges_exactly(tracked):
    repo, session, run = tracked
    paths = [str(repo / "reports/a.html"), str(repo / "reports/b.html")]
    data = batch_input(repo, session, paths)
    result = run_shell(ACTIVITY, data, repo, ag.APP_HOST)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"mycelium_activity_batch": dict(data["mycelium_activity_batch"],
                                                                         status="recorded")}
    assert (run / "mycelium-session-activity.tmp").read_text().split() == ["reports/a.html", "reports/b.html"]


def invalid_batches(repo, session, tmp_path):
    good = str(repo / "reports/a.html")
    link = repo / "linked"
    link.symlink_to(repo / "reports")
    return {
        "outside-entry": batch_input(repo, session, [good, str(tmp_path / "outside.html")]),
        "filtered-entry": batch_input(repo, session, [good, str(repo / ".living/learnings.md")]),
        "lock-entry": batch_input(repo, session, [good, str(repo / "deps.lock")]),
        "non-canonical": batch_input(repo, session, [str(link / "a.html")]),
        "relative": batch_input(repo, session, ["reports/a.html"]),
        "newline": batch_input(repo, session, [good + "\nreports/b.html"]),
        "carriage-return": batch_input(repo, session, [good + "\r"]),
        "repo-root": batch_input(repo, session, [str(repo)]),
        "bool-count": batch_input(repo, session, [good], count=True),
        "wrong-count": batch_input(repo, session, [good], count=2),
        "wrong-digest": batch_input(repo, session, [good], sha256="0" * 64),
        "non-string": batch_input(repo, session, [good, 7]),
        "unhashable": batch_input(repo, session, [good, ["x"]]),
        "duplicate": batch_input(repo, session, [good, good]),
        "empty": batch_input(repo, session, []),
        "mixed-legacy": dict(batch_input(repo, session, [good]), tool_input={"file_paths": [good], "file_path": good}),
        "extra-identity": batch_input(repo, session, [good], extra=1),
        "aggregate-bytes": batch_input(repo, session, [str(repo / "reports" / ("x" * 250 + f"{n}.html"))
                                                      for n in range(256)][:256]),
    }


def test_real_tracker_refuses_invalid_batches_before_any_write(tracked, tmp_path):
    repo, session, run = tracked
    cases = invalid_batches(repo, session, tmp_path)
    cases["aggregate-bytes"]["tool_input"]["file_paths"] = [
        str(repo / "reports" / (("x" * 1000) + f"{n}.html")) for n in range(256)]
    paths = cases["aggregate-bytes"]["tool_input"]["file_paths"]
    cases["aggregate-bytes"]["mycelium_activity_batch"].update(
        count=len(paths), sha256=hashlib.sha256("\n".join(paths).encode()).hexdigest())
    for case, data in cases.items():
        before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
        result = run_shell(ACTIVITY, data, repo, ag.APP_HOST)
        assert result.returncode != 0 and result.stdout == "", case
        assert "batch rejected" in result.stderr, case
        assert {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()} == before, case


@pytest.mark.parametrize("host", ["antigravity-ide", "claude", "codex"])
def test_real_tracker_accepts_batch_input_only_from_the_app_host(tracked, host):
    repo, session, run = tracked
    result = run_shell(ACTIVITY, batch_input(repo, session, [str(repo / "reports/a.html")]), repo, host)
    assert result.returncode != 0 and result.stdout == "" and "only for the Antigravity App" in result.stderr


@pytest.mark.parametrize("shape", ["file_path", "patch-headers"])
def test_real_tracker_legacy_inputs_are_unchanged_and_silent(tracked, shape):
    repo, session, run = tracked
    tool_input = {"file_path": str(repo / "reports/a.html")} if shape == "file_path" else {
        "command": "*** Begin Patch\n*** Add File: reports/a.html\n*** Update File: reports/b.html\n*** End Patch"}
    data = {"cwd": str(repo), "session_id": session, "tool_name": "Write", "tool_input": tool_input,
            "tool_response": {"success": True}}
    result = run_shell(ACTIVITY, data, repo, ag.APP_HOST)
    assert result.returncode == 0 and result.stdout == ""
    expected = ["reports/a.html"] if shape == "file_path" else ["reports/a.html", "reports/b.html"]
    assert (run / "mycelium-session-activity.tmp").read_text().split() == expected


# ---- Real shared hooks: >= 8 recovered edits, enforced then accepted, one invocation ----------------

def test_real_report_batch_is_enforced_then_accepted_with_one_tracker_run(tmp_path, monkeypatch):
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
    (repo / "reports").mkdir()
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    run = repo / ".mycelium/run" / ag.APP_HOST / f"{CID}-turn-0"
    runs, actual = [], ag.subprocess.run

    def counting(command, *a, **k):
        if Path(command[-1]).name == ACTIVITY:
            runs.append(command)
        return actual(command, *a, **k)

    monkeypatch.setattr(ag.subprocess, "run", counting)
    db = Store(home)
    try:
        paths = many(repo, db, count=8)
        large = str(repo / "reports/report.html")
        db.put(68, edit_payload(large, step=68, args=large_args(large), call_id="call-report"))
        for path in paths + [large]:
            Path(path).write_text(CANARY)
        through(payload, 140)
        first = app("Stop", stop(payload))
        assert first["decision"] == "continue" and ".living/ not updated" in first["reason"]
        assert len(runs) == 1
        tracked = (run / "mycelium-session-activity.tmp").read_text().split()
        assert tracked == [f"reports/part-{n}.html" for n in range(8)] + ["reports/report.html"]
        (repo / ".living/learnings.md").write_text("# learnings\n\n- Synthetic report recorded.\n")
        assert app("Stop", stop(payload, executionNum=1)) == {}
    finally:
        db.close()
    assert len(runs) == 1
    turn = json.loads((repo / ".mycelium/antigravity-app" / CID / "native-state.json").read_text())["turns"]["0"]
    steps = list(range(60, 68)) + [68]
    assert sorted(k for k in turn["actions"] if ACTIVITY in k) == sorted(f"{s}:{ACTIVITY}" for s in steps)
    assert turn["status"] == "accepted" and not turn["uncertain"]
    assert json.loads((run / "accepted-stop.json").read_text())["status"] == "accepted"
    no_canary(repo / ".mycelium/antigravity-app")
