"""Antigravity App native command and edit evidence from the App's own step store.

Fixtures are synthetic rows written in the observed App 2.31.0 wire shape: a
``steps`` table (``PRAGMA user_version=1``) whose GENERIC/DONE row carries the
tool call (call ID, name, JSON args), the conversation/source-step origin, the
built-in tool source, a typed ``RunCommand`` result (exit, timeout, sandbox,
command line, cwd, unknown-exit) and the structured terminal block (command,
cwd, explicit exit, stdout). Identifiers are synthetic, not native IDs.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

import antigravity_app_store as store
import antigravity_hooks as ag
from test_antigravity_app import (APP_ROOT, CID, IDE_ROOT, USER, app, doubles, home,  # noqa: F401
                                  native_payload, post, stop, write_transcript)

COMMAND = "python3 analysis.py"
DATA_HOOKS = ["mycelium-data-tracker.sh", "mycelium-post-action.sh"]
OTHER_CID = "0d6c3f8e-2a41-4b7e-8f19-3c5a7e9b1d20"  # synthetic


# ---- Synthetic wire encoding --------------------------------------------------------------------

def varint(n):
    n &= (1 << 64) - 1
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def vi(field, n):
    return varint(field << 3) + varint(n)


def ld(field, payload):
    payload = payload.encode() if isinstance(payload, str) else payload
    return varint(field << 3 | 2) + varint(len(payload)) + payload


def command_payload(cwd, *, step=5, exit_code=0, status=3, call_id="call-synthetic-1", name="run_command",
                    command=COMMAND, args=None, cascade=CID, source=None, builtin=1, sandboxed=True,
                    trusted=False, timed_out=False, unknown=False, type_url=store.RUN_COMMAND_TYPE,
                    typed=True, block=True, block_exit="same", block_cwd=None, block_command=None,
                    stdout="Command exited with code 0.\nNo output from the command.", completed=True,
                    header=132, header_status=None, typed_command=None):
    args = {"CommandLine": command, "WaitMsBeforeAsync": 5000, "toolSummary": "synthetic"} if args is None else args
    source = step if source is None else source
    origin = (vi(2, source) if source else b"") + ld(4, cascade)
    metadata = ld(4, ld(1, call_id) + ld(2, name) + ld(3, json.dumps(args))) + ld(20, origin) + \
        (ld(8, vi(1, 1700000000)) if completed else b"") + (vi(38, builtin) if builtin else b"")
    result = (vi(1, exit_code) if exit_code else b"") + (vi(2, 1) if timed_out else b"") + \
        ld(3, vi(1, 1) if sandboxed else b"") + (vi(4, 1) if trusted else b"") + \
        ld(6, command if typed_command is None else typed_command) + ld(7, cwd) + (vi(8, 1) if unknown else b"")
    terminal = ld(1, command if block_command is None else block_command) + ld(2, cwd if block_cwd is None else block_cwd) + \
        (vi(3, exit_code if block_exit == "same" else block_exit) if block_exit is not None else b"") + ld(4, stdout)
    response = ld(1, "Rendered: " + stdout) + (ld(3, ld(10, ld(3, terminal))) if block else b"") + \
        (ld(6, ld(1, type_url) + ld(2, result)) if typed else b"")
    return (vi(1, header) + vi(4, status if header_status is None else header_status) + ld(5, metadata) +
            ld(140, ld(2, response)))


def edit_args(path):
    return {"Path": path, "Content": "synthetic content", "Overwrite": False}


def edit_payload(path, *, step=7, status=3, call_id="call-synthetic-edit", name="write_file",
                 type_url=store.FILE_EDIT_TYPE, result=True, kind=store.EDIT_CREATE, args=None, typed_path=None):
    metadata = ld(4, ld(1, call_id) + ld(2, name) + ld(3, json.dumps(args or edit_args(path)))) + \
        ld(20, vi(2, step) + ld(4, CID)) + ld(8, vi(1, 1700000000)) + vi(38, 1)
    typed = (vi(1, kind) if kind else b"") + ld(2, typed_path or path)
    response = ld(6, ld(1, type_url) + ld(2, typed)) if result else b""
    return vi(1, 132) + vi(4, status) + ld(5, metadata) + ld(140, ld(2, response))


# ---- Synthetic App conversation store -----------------------------------------------------------

class Store:
    """A live WAL store kept open by a synthetic 'App' writer that never checkpoints."""

    def __init__(self, home, cid=CID):
        self.path = home.joinpath(*store.STORE_ROOT, f"{cid}.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA wal_autocheckpoint=0")
        self.db.execute("PRAGMA user_version=1")
        self.db.execute("CREATE TABLE steps (idx INTEGER, step_type INTEGER, status INTEGER, "
                        "step_payload BLOB, step_format INTEGER, metadata BLOB, render_info BLOB)")

    def put(self, idx, payload, *, status=None, step_type=132, step_format=0):
        if status is None:
            status = store.fields(payload)[4][0][1] if payload[:1] else 3
        self.db.execute("DELETE FROM steps WHERE idx=?", (idx,))
        self.db.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?)",
                        (idx, step_type, status, payload, step_format, b"", b""))

    def add(self, idx, payload, status=3):
        self.db.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?)", (idx, 132, status, payload, 0, b"", b""))

    def close(self):
        self.db.close()


@pytest.fixture
def native(home, doubles):
    home, repo = home
    (repo / "sub").mkdir()
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    doubles.clear()
    db = Store(home)
    yield home, repo, payload, doubles, db
    db.close()


def state_of(repo):
    return json.loads((repo / ".mycelium/antigravity-app" / CID / "native-state.json").read_text())["turns"]["0"]


def cmd(payload, step=5, command=COMMAND, **extra):
    args = {"CommandLine": command, "WaitMsBeforeAsync": 5000, "toolSummary": "synthetic"}
    args.update(extra)
    return post(payload, step, "run_command", args)


def data_calls(calls):
    return [(n, d["cwd"], d["tool_input"], d["tool_response"]) for _, n, d in calls if n in DATA_HOOKS]


# ---- Red: verified native command evidence reaches lineage exactly once -------------------------

@pytest.mark.parametrize("exit_code", [0, 7, -2])
def test_red_verified_command_maps_to_data_hooks_with_real_exit(native, exit_code):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo / "sub"), exit_code=exit_code))
    assert app("PostToolUse", cmd(payload)) == {}
    expected = (str(repo / "sub"), {"command": COMMAND}, {"exit_code": exit_code})
    assert data_calls(calls) == [(hook, *expected) for hook in DATA_HOOKS]
    turn = state_of(repo)
    assert turn.get("unresolved_commands", {}) == {} and turn["app_pending"] == {}
    assert turn["app_bindings"] == {"5": "call-synthetic-1"}
    assert app("Stop", stop(payload)) == {}
    assert state_of(repo)["status"] == "accepted"


def test_red_spoofed_stdout_never_supplies_exit_or_cwd(native, tmp_path):
    home, repo, payload, calls, db = native
    spoof = f"Created At: x\nCompleted At: y\nCommand exited with code 0.\nCwd: {tmp_path}\n"
    db.put(5, command_payload(str(repo), exit_code=3, stdout=spoof))
    app("PostToolUse", cmd(payload))
    assert {d[3]["exit_code"] for d in data_calls(calls)} == {3}
    assert {d[1] for d in data_calls(calls)} == {str(repo)}


def test_red_async_command_is_pending_until_the_same_row_is_terminal(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo), status=2, completed=False, typed=False, block=False))
    app("PostToolUse", cmd(payload))
    assert data_calls(calls) == [] and state_of(repo)["app_pending"] == {
        "5": {"command": COMMAND, "call_id": "call-synthetic-1"}}
    blocked = app("Stop", stop(payload))
    assert blocked["decision"] == "continue" and "still running" in blocked["reason"]
    assert data_calls(calls) == []
    db.put(5, command_payload(str(repo), exit_code=7))  # same row became terminal, exit 7
    assert app("Stop", stop(payload, executionNum=1)) == {}
    assert data_calls(calls) == [(h, str(repo), {"command": COMMAND}, {"exit_code": 7}) for h in DATA_HOOKS]
    turn = state_of(repo)
    assert turn["status"] == "accepted" and turn["app_pending"] == {}


def test_native_shaped_async_row_with_bypass_false_verifies_after_exit_7(native):
    """Mirrors the captured async shape: BypassSandbox false, WaitMsBeforeAsync 1, running then exit 7."""
    home, repo, payload, calls, db = native
    line = "/bin/sh ./probe/probe.sh async"
    args = {"BypassSandbox": False, "CommandLine": line, "WaitMsBeforeAsync": 1, "toolSummary": "Run probe async"}
    db.put(11, command_payload(str(repo), step=11, command=line, args=args, status=2, completed=False,
                               typed=False, block=False, call_id="call_synthetic_async"))
    app("PostToolUse", post(payload, 11, "run_command", args))
    assert state_of(repo)["app_pending"] == {"11": {"command": line, "call_id": "call_synthetic_async"}}
    db.put(11, command_payload(str(repo), step=11, command=line, args=args, exit_code=7,
                               call_id="call_synthetic_async"))
    app("PreInvocation", dict(payload, initialNumSteps=12))
    assert data_calls(calls) == [(h, str(repo), {"command": line}, {"exit_code": 7}) for h in DATA_HOOKS]
    assert state_of(repo)["app_bindings"] == {"11": "call_synthetic_async"}


@pytest.mark.parametrize("flag", [True, "false", None])
def test_hook_sandbox_bypass_request_is_unresolved_without_store_read(native, monkeypatch, flag):
    home, repo, payload, calls, db = native
    monkeypatch.setattr(store, "read_row", lambda *a: pytest.fail("bypass request consulted the store"))
    app("PostToolUse", cmd(payload, BypassSandbox=flag))
    assert_unresolved(repo, calls)


# ---- Rejected evidence stays unresolved, never reaches lineage -----------------------------------

def assert_unresolved(repo, calls, step="5"):
    turn = state_of(repo)
    assert step in turn["unresolved_commands"] and data_calls(calls) == []
    assert turn["app_pending"] == {}
    return turn


REJECTED = {
    "sandbox-false": dict(sandboxed=False),
    "trusted-cli": dict(trusted=True),
    "timed-out": dict(timed_out=True),
    "unknown-exit": dict(unknown=True),
    "no-typed-metadata": dict(typed=False),
    "wrong-type": dict(type_url="type.googleapis.com/exa.cortex.core.tools.Other"),
    "no-terminal-block": dict(block=False),
    "block-no-explicit-exit": dict(block_exit=None),
    "block-exit-disagrees": dict(exit_code=0, block_exit=1),
    "block-cwd-disagrees": dict(block_cwd="/elsewhere"),
    "block-command-disagrees": dict(block_command="python3 other.py"),
    "typed-command-differs": dict(typed_command="python3 other.py"),
    "wrong-call-name": dict(name="read_file"),
    "wrong-call-args": dict(args={"CommandLine": "python3 other.py"}),
    "persistent-or-extra-arg": dict(args={"CommandLine": COMMAND, "Persistent": True}),
    "row-bypass-sandbox-true": dict(args={"CommandLine": COMMAND, "WaitMsBeforeAsync": 1, "BypassSandbox": True,
                                          "toolSummary": "synthetic"}),
    "row-bypass-sandbox-not-bool": dict(args={"CommandLine": COMMAND, "BypassSandbox": 0}),
    "wrong-conversation": dict(cascade=OTHER_CID),
    "source-step-before": dict(source=4),
    "source-step-after": dict(source=6),
    "not-builtin": dict(builtin=2),
    "error-status": dict(status=7),
    "no-completed-at": dict(completed=False),
    "header-type-disagrees": dict(header=133),
    "header-status-disagrees": dict(header_status=2),
}


@pytest.mark.parametrize("case", sorted(REJECTED))
def test_untrusted_native_command_evidence_is_unresolved(native, case):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo), **REJECTED[case]), status=3 if case == "header-status-disagrees" else None)
    app("PostToolUse", cmd(payload))
    assert_unresolved(repo, calls)
    for attempt in range(ag.MAX_STOP_RETRIES + 1):
        app("Stop", stop(payload, executionNum=attempt))
    assert state_of(repo)["status"] == "incomplete"


@pytest.mark.parametrize("case", ["outside-root", "traversal", "relative", "linked", "missing-dir"])
def test_cwd_must_be_canonical_and_inside_an_opted_in_root(native, tmp_path, case):
    home, repo, payload, calls, db = native
    (tmp_path / "outside").mkdir()
    (repo / "link").symlink_to(tmp_path / "outside")
    cwd = {"outside-root": str(tmp_path / "outside"), "traversal": str(repo / "sub/.."), "relative": "sub",
           "linked": str(repo / "link"), "missing-dir": str(repo / "absent")}[case]
    db.put(5, command_payload(cwd))
    app("PostToolUse", cmd(payload))
    assert_unresolved(repo, calls)


@pytest.mark.parametrize("case", ["missing-row", "other-step-row", "hook-command-differs", "native-error",
                                  "duplicate-rows", "row-type-column", "row-format-column"])
def test_wrong_step_call_or_row_evidence_is_unresolved(native, case):
    home, repo, payload, calls, db = native
    row = command_payload(str(repo))
    event = cmd(payload)
    if case == "other-step-row":
        db.put(6, row)
    elif case == "hook-command-differs":
        db.put(5, row)
        event = cmd(payload, command="python3 other.py")
    elif case == "native-error":
        db.put(5, row)
        event["error"] = "denied"
    elif case == "duplicate-rows":
        db.add(5, row)
        db.add(5, row)
    elif case == "row-type-column":
        db.put(5, row, step_type=21)
    elif case == "row-format-column":
        db.put(5, row, step_format=1)
    app("PostToolUse", event)
    assert_unresolved(repo, calls)


@pytest.mark.parametrize("case", ["truncated", "garbage", "empty", "user-version", "no-table", "missing-column",
                                  "not-sqlite"])
def test_corrupt_or_unexpected_store_is_unresolved(native, case):
    home, repo, payload, calls, db = native
    row = command_payload(str(repo))
    if case == "truncated":
        db.put(5, row[:-3], status=3)
    elif case == "garbage":
        db.put(5, b"\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff", status=3)
    elif case == "empty":
        db.put(5, b"", status=3)
    elif case == "user-version":
        db.put(5, row)
        db.db.execute("PRAGMA user_version=2")
    elif case == "no-table":
        db.db.execute("DROP TABLE steps")
    elif case == "missing-column":
        db.db.execute("ALTER TABLE steps RENAME COLUMN step_format TO fmt")
        db.db.execute("INSERT INTO steps (idx, step_type, status, step_payload, fmt) VALUES (5,132,3,?,0)", (row,))
    elif case == "not-sqlite":
        db.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(db.path) + suffix).unlink(missing_ok=True)
        db.path.write_bytes(b"not a database")
    app("PostToolUse", cmd(payload))
    assert_unresolved(repo, calls)


@pytest.mark.parametrize("case", ["absent", "linked-db", "linked-dir", "hardlinked", "group-writable",
                                  "linked-wal", "foreign-conversation-only"])
def test_unsafe_or_missing_store_is_unresolved(native, tmp_path, case):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    db.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    conversations = db.path.parent
    if case == "absent":
        for item in conversations.iterdir():
            item.unlink()
    elif case == "linked-db":
        moved = tmp_path / "elsewhere.db"
        db.path.rename(moved)
        db.path.symlink_to(moved)
    elif case == "linked-dir":
        conversations.rename(tmp_path / "moved")
        conversations.symlink_to(tmp_path / "moved")
    elif case == "hardlinked":
        os.link(db.path, tmp_path / "second-name.db")
    elif case == "group-writable":
        db.path.chmod(0o664)
    elif case == "linked-wal":
        wal = Path(str(db.path) + "-wal")
        wal.unlink(missing_ok=True)
        (tmp_path / "wal").write_bytes(b"")
        wal.symlink_to(tmp_path / "wal")
    elif case == "foreign-conversation-only":
        db.path.rename(conversations / f"{OTHER_CID}.db")
    app("PostToolUse", cmd(payload))
    assert_unresolved(repo, calls)


# ---- Read-only, WAL-visible, bounded store access ------------------------------------------------

def test_store_read_sees_live_committed_wal_rows_without_writing_them(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    wal = Path(str(db.path) + "-wal")
    assert wal.stat().st_size > 0  # the row lives only in the uncheckpointed WAL
    before = (db.path.read_bytes(), wal.read_bytes())
    assert store.command(CID, 5, COMMAND)["exit_code"] == 0
    stale = sqlite3.connect(db.path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:  # immutable access ignores the WAL: the live schema and row are invisible
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            stale.execute("SELECT count(*) FROM steps").fetchone()
    finally:
        stale.close()
    assert (db.path.read_bytes(), wal.read_bytes()) == before


def test_store_connection_is_query_only(native, monkeypatch):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    statements, real = [], sqlite3.connect

    def spy(*args, **kwargs):
        connection = real(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store.sqlite3, "connect", spy)
    store.command(CID, 5, COMMAND)
    assert statements[0] == "PRAGMA query_only = ON"
    assert all(s.startswith(("PRAGMA query_only", "PRAGMA user_version", "PRAGMA table_info", "SELECT"))
               for s in statements)


def test_oversized_payload_is_not_fetched(native, monkeypatch):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    monkeypatch.setattr(store, "MAX_PAYLOAD", 16)
    with pytest.raises(store.Unverified, match="over bound"):
        store.command(CID, 5, COMMAND)


# ---- Replay, deduplication and preserved history ------------------------------------------------

def test_replayed_post_tool_use_runs_hooks_once(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    app("PostToolUse", cmd(payload))
    app("PostToolUse", cmd(payload))
    app("PreInvocation", dict(payload, initialNumSteps=6))
    assert len(data_calls(calls)) == 2


def test_replay_with_a_different_native_call_is_not_reapplied(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    app("PostToolUse", cmd(payload))
    db.put(5, command_payload(str(repo), call_id="call-synthetic-2", exit_code=1))
    app("PostToolUse", cmd(payload))
    assert len(data_calls(calls)) == 2
    assert state_of(repo)["app_bindings"] == {"5": "call-synthetic-1"}
    assert "5" in state_of(repo)["unresolved_commands"]


def test_one_native_call_cannot_verify_two_steps(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo)))
    db.put(8, command_payload(str(repo), step=8))  # same call ID at a second step
    app("PostToolUse", cmd(payload))
    app("PostToolUse", cmd(payload, step=8))
    assert len(data_calls(calls)) == 2 and "8" in state_of(repo)["unresolved_commands"]


def test_async_call_identity_change_is_unresolved(native):
    home, repo, payload, calls, db = native
    db.put(5, command_payload(str(repo), status=2, completed=False, typed=False, block=False))
    app("PostToolUse", cmd(payload))
    db.put(5, command_payload(str(repo), call_id="call-synthetic-other"))
    app("PreInvocation", dict(payload, initialNumSteps=6))
    assert_unresolved(repo, calls)


def test_historical_unresolved_command_is_never_upgraded(native):
    home, repo, payload, calls, db = native
    app("PostToolUse", cmd(payload))  # store has no row yet: unresolved
    db.put(5, command_payload(str(repo)))
    app("PostToolUse", cmd(payload))
    app("PreInvocation", dict(payload, initialNumSteps=6))
    assert_unresolved(repo, calls)


def test_malformed_pending_binding_is_refused_and_retained(native):
    home, repo, payload, calls, db = native
    path = repo / ".mycelium/antigravity-app" / CID / "native-state.json"
    value = json.loads(path.read_text())
    value["turns"]["0"]["app_pending"] = {"5": {"command": COMMAND}}
    path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="pending App command"):
        app("Stop", stop(payload))
    assert path.read_bytes() == before


def test_verified_command_lineage_goes_only_to_its_own_root(home, doubles, tmp_path):
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
        db.put(5, command_payload(str(second)))
        app("PostToolUse", cmd(payload))
    finally:
        db.close()
    assert {d[1] for d in data_calls(doubles)} == {str(second)}
    for root in (repo, second):
        turn = json.loads((root / ".mycelium/antigravity-app" / CID / "native-state.json").read_text())["turns"]["0"]
        assert turn["unresolved_commands"] == {} and turn["app_bindings"] == {"5": "call-synthetic-1"}


def test_ide_profile_never_consults_the_app_store(home, doubles, monkeypatch):
    home, repo = home
    monkeypatch.setattr(store, "read_row", lambda *a: pytest.fail("IDE consulted the App store"))
    payload = native_payload(home, repo, IDE_ROOT)
    ag.dispatch("PreInvocation", payload)
    ag.dispatch("PostToolUse", post(payload, 2, "run_command", {"CommandLine": "true", "Cwd": str(repo)}))
    ag.dispatch("PostToolUse", post(payload, 3, "write_file", {"TargetFile": str(repo / "x")}))


# ---- Edits: typed successful built-in write_file only --------------------------------------------

def edit_event(payload, path, step=7, error="", args=None, name="write_file"):
    return post(payload, step, name, args or edit_args(path), error=error)


def replace_args(path):
    return {"Path": path, "Replacement": "new text", "Target": "old text"}


@pytest.mark.parametrize("name,kind", [("write_file", store.EDIT_CREATE), ("write_file", store.EDIT_OVERWRITE),
                                       ("edit_file", store.EDIT_EDIT)])
def test_verified_native_create_overwrite_and_edit_kinds_are_tracked(native, name, kind):
    home, repo, payload, calls, db = native
    target = str(repo / "sub/notes.md")
    args = replace_args(target) if name == "edit_file" else dict(edit_args(target), Overwrite=kind == 2)
    db.put(7, edit_payload(target, name=name, kind=kind, args=args))
    app("PostToolUse", edit_event(payload, target, args=args, name=name))
    assert [(n, d["tool_input"]) for _, n, d in calls] == [("mycelium-activity-tracker.sh", {"file_path": target})]


@pytest.mark.parametrize("case", ["write-as-edit-kind", "edit-as-create-kind", "edit-as-overwrite-kind",
                                  "edit-with-write-args", "edit-failed", "edit-running", "edit-kind-missing",
                                  "edit-living-path"])
def test_edit_kind_must_match_its_tool_and_succeed(native, case):
    home, repo, payload, calls, db = native
    target = str(repo / (".living/learnings.md" if case == "edit-living-path" else "notes.md"))
    name = "write_file" if case == "write-as-edit-kind" else "edit_file"
    args = edit_args(target) if name == "write_file" or case == "edit-with-write-args" else replace_args(target)
    row = {"write-as-edit-kind": dict(kind=3), "edit-as-create-kind": dict(kind=1),
           "edit-as-overwrite-kind": dict(kind=2), "edit-with-write-args": dict(kind=3),
           "edit-failed": dict(kind=3, status=7, result=False), "edit-running": dict(kind=3, status=2),
           "edit-kind-missing": dict(kind=0), "edit-living-path": dict(kind=3)}[case]
    db.put(7, edit_payload(target, name=name, args=args, **row))
    app("PostToolUse", edit_event(payload, target, args=args, name=name))
    assert calls == [] and state_of(repo)["unresolved_commands"] == {}


def test_verified_native_edit_is_tracked(native):
    home, repo, payload, calls, db = native
    target = str(repo / "sub/notes.md")
    db.put(7, edit_payload(target))
    app("PostToolUse", edit_event(payload, target))
    assert [(n, d["tool_name"], d["tool_input"]) for _, n, d in calls] == [
        ("mycelium-activity-tracker.sh", "Write", {"file_path": target})]
    app("PostToolUse", edit_event(payload, target))
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["failed-edit", "no-result", "wrong-type", "outside-root", "protected",
                                  "no-row", "hook-error", "wrong-name", "running", "kind-unknown",
                                  "kind-missing", "typed-path-differs", "hook-path-differs", "hook-no-path",
                                  "row-extra-arg"])
def test_failed_or_unverified_edit_is_not_tracked(native, tmp_path, case):
    home, repo, payload, calls, db = native
    target = str(tmp_path / "outside.md") if case == "outside-root" else \
        str(repo / ".living/learnings.md") if case == "protected" else str(repo / "notes.md")
    row = {"failed-edit": dict(status=7, result=False), "no-result": dict(result=False),
           "wrong-type": dict(type_url=store.RUN_COMMAND_TYPE), "wrong-name": dict(name="run_command"),
           "running": dict(status=2), "kind-unknown": dict(kind=4), "kind-missing": dict(kind=0),
           "typed-path-differs": dict(typed_path=str(repo / "other.md")),
           "row-extra-arg": dict(args=dict(edit_args(target), TargetFile=target))}.get(case, {})
    if case != "no-row":
        db.put(7, edit_payload(target, **row))
    hook_args = {"hook-path-differs": edit_args(str(repo / "other.md")),
                 "hook-no-path": {"Content": "x"}}.get(case)
    app("PostToolUse", edit_event(payload, target, error="failed" if case == "hook-error" else "", args=hook_args))
    assert calls == []
    assert state_of(repo).get("unresolved_commands", {}) == {}  # edits never block Stop


# ---- Real shared hooks: verified App command reaches lineage and one accepted Stop ---------------

def test_real_app_verified_command_reaches_lineage_and_accepts_once(tmp_path, monkeypatch):
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
    (repo / "analysis.py").write_text("import pandas as pd\ndf = pd.read_csv('data.csv')\n"
                                      "df.to_csv('out.csv')\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c",
                    "user.email=test@example.invalid", "commit", "-qm", "fixture"], check=True)
    payload = native_payload(home, repo, APP_ROOT)
    app("PreInvocation", payload)
    run = repo / ".mycelium/run" / ag.APP_HOST / f"{CID}-turn-0"
    db = Store(home)
    try:
        db.put(5, command_payload(str(repo), exit_code=0))
        write_transcript(Path(payload["transcriptPath"]), [USER, {"step_index": 5, "source": "MODEL",
                         "type": "GENERIC", "status": "DONE"}])
        app("PostToolUse", cmd(payload))
    finally:
        db.close()
    events = [json.loads(line) for line in (run / "mycelium-data-events.tmp").read_text().splitlines()]
    assert len(events) == 1 and events[0]["bash_exit"] == 0
    # The real Stop check requires the session-end knowledge update after analysis work.
    (repo / ".living/learnings.md").write_text("# learnings\n\n- Synthetic analysis recorded.\n")
    assert app("Stop", stop(payload)) == {}
    receipt = json.loads((run / "accepted-stop.json").read_text())
    assert receipt["host"] == ag.APP_HOST and receipt["status"] == "accepted"
    lineage = list((repo / ".living/log/data-lineage").glob("*.json"))
    assert len(lineage) == 1
    manifest = json.dumps(json.loads(lineage[0].read_text()), ensure_ascii=False)
    assert str(repo / "analysis.py") in manifest and "data.csv" in manifest
    assert app("Stop", stop(payload, executionNum=1)) == {}
