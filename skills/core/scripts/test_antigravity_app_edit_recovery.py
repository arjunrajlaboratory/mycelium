"""Antigravity App edit activity recovery at genuine Stop (work order edit-recovery v2).

The installed smoke credited verified commands but no edit activity although the
App's own store held committed create/overwrite/edit rows. These synthetic
fixtures reproduce a missing edit callback and check that a genuine active App
Stop recovers exactly the current turn's verified edits, once, before shared
finalization, through the same validator as the direct callback.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import antigravity_app_store as store
import antigravity_hooks as ag
from test_antigravity_app import APP_ROOT, CID, USER, app, doubles, home, native_payload, stop, write_transcript  # noqa: F401
from test_antigravity_app_native import (OTHER_CID, Store, command_payload, edit_args, edit_event, edit_payload,
                                         native, replace_args, state_of)  # noqa: F401

ACTIVITY = "mycelium-activity-tracker.sh"


@pytest.fixture
def doubles(monkeypatch):
    """Hook doubles; the activity double acknowledges a batch exactly as the real tracker does."""
    calls = []

    def hook(name, data, root, plugin, **kwargs):
        calls.append((ag.active_host(), name, data))
        if name == "mycelium-health.sh":
            return {"hookSpecificOutput": {"additionalContext": "NATIVE-START"}}
        if name == ACTIVITY and "mycelium_activity_batch" in data:
            return {"mycelium_activity_batch": dict(data["mycelium_activity_batch"], status="recorded")}
        return {}

    monkeypatch.setattr(ag, "run_hook", hook)
    return calls


def through(payload, last):
    """Native transcript whose newest step is ``last`` (the Stop boundary)."""
    write_transcript(Path(payload["transcriptPath"]), [USER, {"step_index": last, "source": "MODEL",
                                                             "type": "PLANNER_RESPONSE", "status": "DONE"}])


def seed_smoke_shape(repo, db):
    """Create, overwrite and edit at ordinary workspace paths, as in the installed smoke."""
    a, b = str(repo / "sub/created.md"), str(repo / "sub/notes.md")
    db.put(15, edit_payload(a, step=15, kind=store.EDIT_CREATE, call_id="call-create"))
    db.put(20, edit_payload(b, step=20, kind=store.EDIT_OVERWRITE, call_id="call-overwrite",
                            args=dict(edit_args(b), Overwrite=True)))
    db.put(27, edit_payload(b, step=27, kind=store.EDIT_EDIT, name="edit_file", call_id="call-edit",
                            args=replace_args(b)))
    return a, b


def activity(calls):
    """Every path handed to the activity tracker, in order (direct and batch)."""
    out = []
    for _, n, d in calls:
        if n == ACTIVITY:
            out.extend(d["tool_input"].get("file_paths") or [d["tool_input"]["file_path"]])
    return out


def tracker_runs(calls):
    return sum(n == ACTIVITY for _, n, _ in calls)


def actions(repo):
    return sorted(k for k in state_of(repo)["actions"] if k.endswith(ACTIVITY))


# ---- Red: no callback means no edit activity -----------------------------------------------------

def test_red_missing_edit_callbacks_are_recovered_at_genuine_stop(native):
    home, repo, payload, calls, db = native
    a, b = seed_smoke_shape(repo, db)
    through(payload, 30)
    assert app("Stop", stop(payload)) == {}
    assert activity(calls) == [a, b] and tracker_runs(calls) == 1  # one batch, paths deduplicated
    assert actions(repo) == [f"{s}:{ACTIVITY}" for s in (15, 20, 27)]
    turn = state_of(repo)
    assert turn["status"] == "accepted"
    assert turn["edit_recovery"] == {"status": "complete", "after": 0, "through": 30, "credited": [15, 20, 27],
                                     "rejected": {}, "reason": ""}
    # Recovery precedes shared finalization.
    names = [n for _, n, _ in calls]
    assert names.index("mycelium-stop-check.sh") > max(i for i, n in enumerate(names) if n == ACTIVITY)


def test_direct_callback_and_stop_recovery_credit_each_step_once(native):
    home, repo, payload, calls, db = native
    a, b = seed_smoke_shape(repo, db)
    app("PostToolUse", edit_event(payload, a, step=15))
    assert activity(calls) == [a]
    through(payload, 30)
    app("Stop", stop(payload))
    assert activity(calls) == [a, b] and tracker_runs(calls) == 2  # direct call, then one batch
    assert actions(repo) == [f"{s}:{ACTIVITY}" for s in (15, 20, 27)]
    assert state_of(repo)["edit_recovery"]["credited"] == [20, 27]  # 15 was the direct callback's


def test_repeat_stop_after_acceptance_does_not_rescan(native, monkeypatch):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    through(payload, 30)
    app("Stop", stop(payload))
    monkeypatch.setattr(store, "recover_edits", lambda *a, **k: pytest.fail("rescanned an accepted turn"))
    assert app("Stop", stop(payload, executionNum=1)) == {}
    assert tracker_runs(calls) == 1


def test_blocked_stop_retry_rescans_without_duplicate_credit(native, monkeypatch):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    through(payload, 30)
    original, results = ag.run_hook, [{"decision": "block", "reason": "triage first"}, {}]

    def hook(name, data, root, plugin, **kw):
        return results.pop(0) if name == "mycelium-stop-check.sh" else original(name, data, root, plugin, **kw)

    monkeypatch.setattr(ag, "run_hook", hook)
    assert app("Stop", stop(payload))["decision"] == "continue"
    assert state_of(repo)["edit_recovery"]["credited"] == [15, 20, 27]
    assert app("Stop", stop(payload, executionNum=1)) == {}
    assert tracker_runs(calls) == 1 and state_of(repo)["status"] == "accepted"
    assert state_of(repo)["edit_recovery"]["credited"] == []  # retry recovered nothing new


def test_insufficient_budget_defers_without_reserving_any_action(native, monkeypatch):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    through(payload, 30)
    # The scan fits, but less than a real tracker allowance would remain afterwards.
    monkeypatch.setattr(ag, "APP_FINALIZE_RESERVE_SECONDS",
                        ag.APP_STOP_BUDGET_SECONDS - ag.APP_RECOVERY_BATCH_MIN_SECONDS + 1)
    first = app("Stop", stop(payload))
    assert first["decision"] == "continue" and "recover native edit activity" in first["reason"]
    turn = state_of(repo)
    assert turn["edit_recovery"]["status"] == "deferred" and turn["edit_recovery"]["credited"] == []
    assert actions(repo) == [] and tracker_runs(calls) == 0 and not turn["uncertain"]
    monkeypatch.setattr(ag, "APP_FINALIZE_RESERVE_SECONDS", 20)
    assert app("Stop", stop(payload, executionNum=1)) == {}
    assert tracker_runs(calls) == 1 and state_of(repo)["edit_recovery"]["credited"] == [15, 20, 27]


def test_batch_timeout_is_a_real_allowance(native, monkeypatch):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    through(payload, 30)
    budgets, original = [], ag.run_hook

    def hook(name, data, root, plugin, **kw):
        if name == ACTIVITY:
            budgets.append(kw["timeout"])
        return original(name, data, root, plugin, **kw)

    monkeypatch.setattr(ag, "run_hook", hook)
    app("Stop", stop(payload))
    assert len(budgets) == 1 and ag.APP_RECOVERY_BATCH_MIN_SECONDS <= budgets[0] <= ag.APP_RECOVERY_ACTION_SECONDS


@pytest.mark.parametrize("kind", ["cancelled", "error", "not-idle"])
def test_abnormal_or_non_idle_stop_never_scans(native, monkeypatch, kind):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    through(payload, 30)
    monkeypatch.setattr(store, "recover_edits", lambda *a, **k: pytest.fail("scanned an abnormal Stop"))
    event = {"cancelled": stop(payload, terminationReason="CANCELLED"), "error": stop(payload, error="limit"),
             "not-idle": stop(payload, fullyIdle=False)}[kind]
    assert app("Stop", event) == {}
    assert activity(calls) == [] and "edit_recovery" not in state_of(repo)


# ---- Current explicit-user turn only ----------------------------------------------------------

def test_scan_covers_only_the_current_turn_up_to_the_stop_boundary(native):
    home, repo, payload, calls, db = native
    path = str(repo / "notes.md")
    for step in (0, 4, 9, 10):
        db.put(step, edit_payload(path, step=step, call_id=f"call-{step}"))
    through(payload, 9)
    app("Stop", stop(payload))
    assert state_of(repo)["edit_recovery"]["credited"] == [4, 9]
    assert actions(repo) == [f"{s}:{ACTIVITY}" for s in (4, 9)]


def test_a_later_turn_never_credits_prior_turn_edits(native):
    home, repo, payload, calls, db = native
    path = str(repo / "notes.md")
    db.put(5, edit_payload(path, step=5, call_id="call-prior"))
    db.put(12, edit_payload(path, step=12, call_id="call-current"))
    later = dict(USER, step_index=10)
    write_transcript(Path(payload["transcriptPath"]), [USER, later, {"step_index": 13, "source": "MODEL",
                     "type": "PLANNER_RESPONSE", "status": "DONE"}])
    app("PreInvocation", dict(payload, initialNumSteps=11))
    app("Stop", stop(payload))
    turns = json.loads((repo / ".mycelium/antigravity-app" / CID / "native-state.json").read_text())["turns"]
    assert turns["10"]["edit_recovery"]["after"] == 10 and turns["10"]["edit_recovery"]["credited"] == [12]
    assert [k for k in turns["10"]["actions"] if ACTIVITY in k] == [f"12:{ACTIVITY}"]
    assert not any(ACTIVITY in k for k in turns["0"]["actions"])


# ---- Unsupported, failed and forged candidates get no credit and no block ---------------------

def forged_command_row(repo, step):
    """A command whose stdout carries the FileEdit type marker: passes the SQL prefilter only."""
    return command_payload(str(repo), step=step, stdout=store.FILE_EDIT_TYPE + " " + str(repo / "notes.md"))


@pytest.mark.parametrize("case", ["failed", "running", "kind-mismatch", "forged-command", "not-builtin",
                                  "other-conversation", "typed-path-differs", "unsupported-tool",
                                  "source-step-differs", "duplicate-rows", "duplicate-unmarked-row"])
def test_unsupported_failed_or_forged_candidates_are_reported_without_credit(native, case):
    home, repo, payload, calls, db = native
    path = str(repo / "notes.md")
    rows = {"failed": dict(status=7, result=False), "running": dict(status=2), "kind-mismatch": dict(kind=3),
            "typed-path-differs": dict(typed_path=str(repo / "other.md")), "unsupported-tool": dict(name="patch_file")}
    if case == "forged-command":
        db.put(6, forged_command_row(repo, 6))
    elif case == "not-builtin":
        db.put(6, edit_payload(path, step=6).replace(ag_builtin(1), ag_builtin(2)))
    elif case == "other-conversation":
        db.put(6, edit_payload(path, step=6).replace(CID.encode(), OTHER_CID.encode()))
    elif case == "source-step-differs":
        db.put(6, edit_payload(path, step=5))
    elif case == "duplicate-rows":
        db.add(6, edit_payload(path, step=6))
        db.add(6, edit_payload(path, step=6))
    elif case == "duplicate-unmarked-row":  # only one copy carries the SQL type marker
        db.add(6, edit_payload(path, step=6))
        db.add(6, command_payload(str(repo), step=6))
    else:
        db.put(6, edit_payload(path, step=6, **rows[case]))
    through(payload, 8)
    assert app("Stop", stop(payload)) == {}
    recovery = state_of(repo)["edit_recovery"]
    assert activity(calls) == [] and recovery["credited"] == [] and recovery["status"] == "complete"
    if case not in {"failed", "running"}:  # non-terminal rows are excluded before validation
        assert list(recovery["rejected"]) == ["6"]
    assert state_of(repo)["status"] == "accepted"


def ag_builtin(value):
    from test_antigravity_app_native import vi
    return vi(38, value)


# ---- Paths: most-specific opted-in root; protected, linked and outside paths excluded -----------

@pytest.mark.parametrize("case", ["outside", "protected", "linked", "traversal"])
def test_other_protected_or_unsafe_paths_are_not_credited(native, tmp_path, case):
    home, repo, payload, calls, db = native
    (tmp_path / "outside").mkdir()
    (repo / "link").symlink_to(tmp_path / "outside")
    path = {"outside": str(tmp_path / "outside/x.md"), "protected": str(repo / ".living/learnings.md"),
            "linked": str(repo / "link/x.md"), "traversal": str(repo / "sub/../x.md")}[case]
    db.put(6, edit_payload(path, step=6))
    through(payload, 8)
    assert app("Stop", stop(payload)) == {}
    assert activity(calls) == [] and state_of(repo)["status"] == "accepted"


def test_recovered_edit_goes_only_to_the_most_specific_root(home, doubles, tmp_path):
    home, repo = home
    nested = repo / "nested"
    nested.mkdir()
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    (nested / ".living").mkdir()
    payload = dict(native_payload(home, repo, APP_ROOT), workspacePaths=[str(repo), str(nested)])
    app("PreInvocation", payload)
    doubles.clear()
    db = Store(home)
    try:
        db.put(6, edit_payload(str(nested / "x.md"), step=6))
        through(payload, 8)
        app("Stop", stop(payload))
    finally:
        db.close()
    assert [(d["cwd"], d["tool_input"]["file_paths"]) for _, n, d in doubles if n == ACTIVITY] == [
        (str(nested), [str(nested / "x.md")])]


def test_recovery_never_credits_commands(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    through(payload, 8)
    app("Stop", stop(payload))
    assert [n for _, n, _ in calls if n != "mycelium-stop-check.sh"] == []


# ---- Bounded, unreadable and deferred scans are explicit, never complete ------------------------

@pytest.mark.parametrize("bound", ["rows", "bytes", "work"])
def test_truncated_scan_is_reported_and_never_complete(native, monkeypatch, bound):
    home, repo, payload, calls, db = native
    path = str(repo / "notes.md")
    for step in range(1, 6):
        db.put(step, edit_payload(path, step=step, call_id=f"call-{step}"))
    monkeypatch.setattr(store, {"rows": "MAX_SCAN_ROWS", "bytes": "MAX_SCAN_BYTES",
                                "work": "MAX_SCAN_VM_STEPS"}[bound], {"rows": 2, "bytes": 300, "work": 1}[bound])
    monkeypatch.setattr(store, "SCAN_CHECK_STEPS", 1)
    through(payload, 8)
    app("Stop", stop(payload))
    recovery = state_of(repo)["edit_recovery"]
    assert recovery["status"] == "truncated"
    assert recovery["credited"] == list(range(1, len(recovery["credited"]) + 1)) and len(recovery["credited"]) < 5


@pytest.mark.parametrize("case", ["absent", "wrong-version", "linked"])
def test_unreadable_store_is_reported_without_credit_or_block(native, tmp_path, case):
    home, repo, payload, calls, db = native
    db.put(6, edit_payload(str(repo / "notes.md"), step=6))
    if case == "wrong-version":
        db.db.execute("PRAGMA user_version=2")
    else:
        db.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        if case == "absent":
            for item in db.path.parent.iterdir():
                item.unlink()
        else:
            moved = tmp_path / "moved.db"
            db.path.rename(moved)
            db.path.symlink_to(moved)
    through(payload, 8)
    assert app("Stop", stop(payload)) == {}
    recovery = state_of(repo)["edit_recovery"]
    assert recovery["status"] == "unavailable" and recovery["reason"] and recovery["credited"] == []
    assert activity(calls) == [] and state_of(repo)["status"] == "accepted"


def test_command_free_stop_without_store_is_unchanged_and_accepted(started):
    home, repo, payload, calls, state = started
    assert app("Stop", stop(payload)) == {}
    turn = json.loads(state.read_text())["turns"]["0"]
    assert turn["status"] == "accepted" and turn["edit_recovery"]["status"] == "unavailable"


def test_deferred_scan_blocks_instead_of_finalizing(native, monkeypatch):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    through(payload, 30)
    monkeypatch.setattr(ag, "APP_FINALIZE_RESERVE_SECONDS", ag.APP_STOP_BUDGET_SECONDS + 1)
    result = app("Stop", stop(payload))
    assert result["decision"] == "continue" and "recover native edit activity" in result["reason"]
    assert activity(calls) == [] and not any(n == "mycelium-stop-check.sh" for _, n, _ in calls)
    assert state_of(repo)["edit_recovery"]["status"] == "deferred" and state_of(repo)["status"] == "active"


def test_interrupted_recovered_activity_holds_stop_without_redispatch(native, monkeypatch):
    home, repo, payload, calls, db = native
    db.put(6, edit_payload(str(repo / "notes.md"), step=6))
    through(payload, 8)
    original, attempts = ag.run_hook, []

    def failing(name, data, root, plugin, **kw):
        if name == ACTIVITY:
            attempts.append(name)
            raise subprocess.TimeoutExpired(name, 12)
        return original(name, data, root, plugin, **kw)

    monkeypatch.setattr(ag, "run_hook", failing)
    first = app("Stop", stop(payload))
    assert first["decision"] == "continue" and first["reason"] == ag.UNRESOLVED_HOOK
    assert app("Stop", stop(payload, executionNum=1))["reason"] == ag.UNRESOLVED_HOOK
    turn = state_of(repo)
    assert attempts == [ACTIVITY] and turn["actions"][f"6:{ACTIVITY}"] == "running" and turn["uncertain"] is True
    assert not any(n == "mycelium-stop-check.sh" for _, n, _ in calls)


def test_store_scan_is_query_only_and_single_select(native, monkeypatch):
    home, repo, payload, calls, db = native
    seed_smoke_shape(repo, db)
    statements, real = [], store.sqlite3.connect

    def spy(*args, **kwargs):
        connection = real(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", spy)
    result = store.recover_edits(CID, 0, 30)
    assert [e["step"] for e in result["edits"]] == [15, 20, 27] and result["status"] == "complete"
    assert statements[0] == "PRAGMA query_only = ON"
    assert sum(s.startswith("SELECT") for s in statements) == 1
    assert all(s.startswith(("PRAGMA query_only", "PRAGMA user_version", "PRAGMA table_info", "SELECT"))
               for s in statements)


@pytest.fixture
def started(home, doubles):
    home, repo = home
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    doubles.clear()
    return home, repo, payload, doubles, repo / ".mycelium/antigravity-app" / CID / "native-state.json"


# ---- Real shared activity tracker and Stop -----------------------------------------------------

def test_real_recovered_edit_activity_is_enforced_then_accepted_once(tmp_path, monkeypatch):
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
    (repo / "sub").mkdir()
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    run = repo / ".mycelium/run" / ag.APP_HOST / f"{CID}-turn-0"
    db = Store(home)
    try:
        a, b = seed_smoke_shape(repo, db)
        Path(a).write_text("created\n")
        Path(b).write_text("edited\n")
        through(payload, 30)
        first = app("Stop", stop(payload))
        assert first["decision"] == "continue" and ".living/ not updated" in first["reason"]
        tracked = (run / "mycelium-session-activity.tmp").read_text()
        assert "sub/created.md" in tracked and "sub/notes.md" in tracked
        (repo / ".living/learnings.md").write_text("# learnings\n\n- Synthetic edits recorded.\n")
        assert app("Stop", stop(payload, executionNum=1)) == {}
    finally:
        db.close()
    receipt = json.loads((run / "accepted-stop.json").read_text())
    assert receipt["host"] == ag.APP_HOST and receipt["status"] == "accepted"
    turn = json.loads((repo / ".mycelium/antigravity-app" / CID / "native-state.json").read_text())["turns"]["0"]
    assert sorted(k for k in turn["actions"] if ACTIVITY in k) == [f"{s}:{ACTIVITY}" for s in (15, 20, 27)]
    assert turn["status"] == "accepted" and turn["edit_recovery"]["status"] == "complete"
    assert app("Stop", stop(payload, executionNum=2)) == {}
