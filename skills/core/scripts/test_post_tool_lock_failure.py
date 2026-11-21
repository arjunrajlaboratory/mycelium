"""A post-tool hook that cannot take its session lock leaves evidence (#137).

Every post-tool hook starts with `mycelium_prepare_locked_post_tool_state`.
It returns 1 both for an intentional no-op (no transaction, or the event is
another session's) and for a failure (the session lock could not be taken).
The hooks used to `exit 0` silently in both cases, so a contended lock made
an edit, read, or analysis command disappear and Stop accepted the turn
without asking for triage of work it never heard of.

A failure after ownership is proven now appends one line to the owning
transaction's `mycelium-unrecorded-events.tmp`, prints a diagnostic on
stderr, and still exits 0 so the host tool call is never blocked. Stop
treats a `work` line (an edit or proven analysis run) as work of unknown
extent: it blocks until `.living/` is updated and says why. A `lineage` or
`read` line is not session work, as its recorded counterpart is not; Stop
reports it as unknown provenance. An intentional no-op leaves neither.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOSTS = ["claude", "codex"]
UNRECORDED = "mycelium-unrecorded-events.tmp"


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (repo / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    return repo


def _run(host: str, name: str, repo: Path, payload: dict, extra_env=None):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(HOOKS_DIR / name)],
        cwd=repo,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=60,
    )


def _run_dir(repo: Path, host: str, sid: str) -> Path:
    return repo / ".mycelium" / "run" / host / sid


def _start(host: str, repo: Path, sid: str) -> None:
    result = _run(
        host, "mycelium-health.sh", repo,
        {"cwd": str(repo), "source": "startup", "session_id": sid,
         "hook_event_name": "SessionStart"},
    )
    assert result.returncode == 0, result.stderr
    assert (_run_dir(repo, host, sid) / "active-session-log.tmp").is_file()


def _stop(host: str, repo: Path, sid: str) -> subprocess.CompletedProcess:
    result = _run(
        host, "mycelium-stop-check.sh", repo,
        {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
         "stop_hook_active": False},
    )
    assert result.returncode == 0, result.stderr
    return result


def _block_reason(stop: subprocess.CompletedProcess) -> str | None:
    if not stop.stdout.strip():
        return None
    output = json.loads(stop.stdout)
    return output.get("reason") if output.get("decision") == "block" else None


def _hold_session_lock(run_dir: Path) -> Path:
    """A live holder (this test process) that never releases in time."""
    lock = run_dir / "mycelium-session.lock"
    lock.mkdir()
    (lock / "owner").write_text(f"{os.getpid()} {int(time.time())}\n")
    return lock


def _payloads(repo: Path, sid: str) -> dict[str, dict]:
    bash = {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
            "hook_event_name": "PostToolUse",
            "tool_input": {"command": "python3 analysis.py"},
            "tool_response": {"exit_code": 0}}
    return {
        "mycelium-activity-tracker.sh": {
            "cwd": str(repo), "session_id": sid, "tool_name": "Edit",
            "hook_event_name": "PostToolUse",
            "tool_input": {"file_path": str(repo / "model.py")},
            "tool_response": {"exit_code": 0}},
        "mycelium-post-action.sh": bash,
        "mycelium-data-tracker.sh": bash,
        "mycelium-read-tracker.sh": {
            "cwd": str(repo), "session_id": sid, "tool_name": "Read",
            "hook_event_name": "PostToolUse",
            "tool_input": {"file_path": str(repo / ".living" / "learnings.md")}},
    }


FAST_TIMEOUT = {"MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS": "2"}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(("hook", "kind"), [
    ("mycelium-activity-tracker.sh", "work"),
    ("mycelium-post-action.sh", "work"),
    ("mycelium-data-tracker.sh", "lineage"),
    ("mycelium-read-tracker.sh", "read"),
])
def test_lock_timeout_leaves_marker_and_diagnostic(tmp_path, host, hook, kind):
    repo = _repo(tmp_path)
    sid = "contended-session"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    lock = _hold_session_lock(run_dir)

    result = _run(host, hook, repo, _payloads(repo, sid)[hook],
                  extra_env=FAST_TIMEOUT)

    # Fail open: the host tool call is never blocked by a tracking failure.
    assert result.returncode == 0
    assert "could not take the session lock" in result.stderr
    assert hook in result.stderr
    lines = (run_dir / UNRECORDED).read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["hook"] == hook
    assert record["kind"] == kind
    assert record["reason"] == "session-lock-unavailable"
    # The other hook's lock is untouched: still held by its live owner.
    assert lock.is_dir()
    # Nothing was recorded through the lock this hook never held.
    assert not (run_dir / "mycelium-session-activity.tmp").exists()
    assert not (run_dir / "mycelium-data-events.tmp").exists()
    assert not (run_dir / "mycelium-read-access.log").exists()
    # The transaction existed: this is not a no-transaction lineage gap.
    assert not (repo / ".mycelium" / "lineage-skips-unowned.ndjson").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_stop_asks_for_triage_after_an_unrecorded_event(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "unrecorded-stop"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    lock = _hold_session_lock(run_dir)
    edit = _payloads(repo, sid)["mycelium-activity-tracker.sh"]
    assert _run(host, "mycelium-activity-tracker.sh", repo, edit,
                extra_env=FAST_TIMEOUT).returncode == 0
    (lock / "owner").unlink()
    lock.rmdir()

    reason = _block_reason(_stop(host, repo, sid))
    assert reason is not None, "Stop accepted a turn with an unrecorded edit"
    assert "UNRECORDED WORK: 1 edit or analysis hook run(s)" in reason
    assert "0 files changed" not in reason
    # Taken into a fresh work cycle: only a later .living update satisfies it.
    assert not (run_dir / UNRECORDED).exists()
    assert (run_dir / "mycelium-reminded.tmp").is_file()
    assert _block_reason(_stop(host, repo, sid)) is not None

    # Triage clears it like any other work signal.
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-20] triaged\n- noted\n")
    assert _block_reason(_stop(host, repo, sid)) is None
    assert not (run_dir / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_unrecorded_note_joins_a_files_changed_block(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "unrecorded-with-edit"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    edit = _payloads(repo, sid)["mycelium-activity-tracker.sh"]
    (repo / "model.py").write_text("x = 1\n")
    assert _run(host, "mycelium-activity-tracker.sh", repo, edit).returncode == 0
    lock = _hold_session_lock(run_dir)
    assert _run(host, "mycelium-read-tracker.sh", repo,
                _payloads(repo, sid)["mycelium-read-tracker.sh"],
                extra_env=FAST_TIMEOUT).returncode == 0
    (lock / "owner").unlink()
    lock.rmdir()

    reason = _block_reason(_stop(host, repo, sid))
    assert reason is not None
    assert "model.py" in reason
    assert "UNRECORDED EVENTS: 1 data-lineage or .living read record(s)" in reason
    assert "UNRECORDED WORK" not in reason


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("hook", ["mycelium-data-tracker.sh", "mycelium-read-tracker.sh"])
def test_dropped_lineage_or_read_is_reported_without_forcing_triage(
    tmp_path, host, hook
):
    """A data command or `.living` read is not session work when recorded,
    so losing its record does not oblige triage; Stop still says it was lost."""
    repo = _repo(tmp_path)
    sid = "dropped-record"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    lock = _hold_session_lock(run_dir)
    payload = dict(_payloads(repo, sid)[hook])
    if hook == "mycelium-data-tracker.sh":
        payload["tool_input"] = {"command": "head analysis.py"}
    assert _run(host, hook, repo, payload, extra_env=FAST_TIMEOUT).returncode == 0
    (lock / "owner").unlink()
    lock.rmdir()

    stop = _stop(host, repo, sid)

    assert _block_reason(stop) is None, stop.stdout
    assert "UNRECORDED EVENTS: 1" in stop.stdout
    assert not (run_dir / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_another_sessions_event_under_contention_is_still_a_no_op(tmp_path, host):
    """Only an owned transaction's failure is evidence; a foreign or orphan
    event never waits on the lock and leaves nothing behind."""
    repo = _repo(tmp_path)
    owner = "owner-session"
    _start(host, repo, owner)
    owner_dir = _run_dir(repo, host, owner)
    _hold_session_lock(owner_dir)

    result = _run(host, "mycelium-read-tracker.sh", repo,
                  _payloads(repo, "stranger")["mycelium-read-tracker.sh"],
                  extra_env=FAST_TIMEOUT)

    assert result.returncode == 0
    assert result.stderr == ""
    assert not (owner_dir / UNRECORDED).exists()
    assert not _run_dir(repo, host, "stranger").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_uncontended_events_leave_no_marker(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "quiet-session"
    _start(host, repo, sid)
    for hook, payload in _payloads(repo, sid).items():
        result = _run(host, hook, repo, payload)
        assert result.returncode == 0, (hook, result.stderr)
    assert not (_run_dir(repo, host, sid) / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_unrecorded_marker_is_not_followed_through_a_symlink(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "linked-marker"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    _hold_session_lock(run_dir)
    outside = tmp_path / "outside.txt"
    outside.write_text("untouched\n")
    (run_dir / UNRECORDED).symlink_to(outside)

    result = _run(host, "mycelium-read-tracker.sh", repo,
                  _payloads(repo, sid)["mycelium-read-tracker.sh"],
                  extra_env=FAST_TIMEOUT)

    assert result.returncode == 0
    assert outside.read_text() == "untouched\n"


SESSION_CHANGES = HOOKS_DIR.parent / "scripts" / "session_file_changes.py"


@pytest.mark.parametrize("host", HOSTS)
def test_evidence_appended_while_stop_runs_is_not_accepted(tmp_path, host):
    """A hook that times out while a slow Stop holds the lock appends after
    Stop took the file. That Stop must not accept; the retry enforces it."""
    repo = _repo(tmp_path)
    sid = "late-evidence"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    once = tmp_path / "appended"
    wrapper = tmp_path / "slow_changes.py"
    wrapper.write_text(
        "import json, os, sys\n"
        f"once = {str(once)!r}\n"
        "if not os.path.exists(once):\n"
        "    open(once, 'w').close()\n"
        f"    with open({str(run_dir / UNRECORDED)!r}, 'a') as handle:\n"
        "        handle.write(json.dumps({'kind': 'work', 'hook': 'late'}) + '\\n')\n"
        f"os.execv(sys.executable, [sys.executable, {str(SESSION_CHANGES)!r}] + sys.argv[1:])\n"
    )
    env = {"MYCELIUM_SESSION_CHANGES_HELPER": str(wrapper)}
    stop = _run(host, "mycelium-stop-check.sh", repo,
                {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                 "stop_hook_active": False}, extra_env=env)
    assert once.exists(), "the helper never ran inside Stop"
    reason = _block_reason(stop)
    assert reason is not None and "while this Stop ran" in reason, stop.stdout

    retry = _block_reason(_stop(host, repo, sid))
    assert retry is not None and "UNRECORDED WORK: 1" in retry
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-20] triaged late work\n- noted\n")
    assert _block_reason(_stop(host, repo, sid)) is None
    assert not (run_dir / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_earlier_triage_does_not_cover_later_unrecorded_work(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "stale-triage"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    payloads = _payloads(repo, sid)
    (repo / "model.py").write_text("x = 1\n")
    assert _run(host, "mycelium-activity-tracker.sh", repo,
                payloads["mycelium-activity-tracker.sh"]).returncode == 0
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-20] first edit\n- noted\n")
    lock = _hold_session_lock(run_dir)
    second = dict(payloads["mycelium-activity-tracker.sh"],
                  tool_input={"file_path": str(repo / "other.py")})
    (repo / "other.py").write_text("y = 2\n")
    assert _run(host, "mycelium-activity-tracker.sh", repo, second,
                extra_env=FAST_TIMEOUT).returncode == 0
    (lock / "owner").unlink()
    lock.rmdir()

    reason = _block_reason(_stop(host, repo, sid))
    assert reason is not None, "an earlier triage satisfied later lost work"
    assert "UNRECORDED WORK: 1" in reason


@pytest.mark.parametrize("host", HOSTS)
def test_fresh_unrecorded_evidence_keeps_an_old_transaction_alive(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "old-but-alive"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    marker = run_dir / "active-session-log.tmp"
    lines = marker.read_text().splitlines()
    marker.write_text(f"{lines[0]}\n{int(time.time()) - 7300}\n{lines[2]}\n")
    (run_dir / UNRECORDED).write_text('{"kind": "work"}\n')

    result = _run(host, "mycelium-health.sh", repo,
                  {"cwd": str(repo), "source": "resume", "session_id": sid,
                   "hook_event_name": "SessionStart"})

    assert result.returncode == 0, result.stderr
    assert "INCOMPLETE SESSION LOG" not in result.stdout
    assert marker.read_text().splitlines()[0] == lines[0]
    assert (run_dir / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_record_refuses_a_linked_ancestor_swapped_in_during_the_wait(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "swapped-ancestor"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    outside = tmp_path / "outside"
    (outside / sid).mkdir(parents=True)
    host_dir = run_dir.parent
    host_dir.rename(tmp_path / "moved-host-dir")
    host_dir.symlink_to(outside)
    command = (
        f'source "{HOOKS_DIR / "mycelium-hook-lib.sh"}"\n'
        f'STATE_DIR="{run_dir}"\n'
        f'mycelium__record_unrecorded_event "{repo}" \'{{"tool_name": "Edit"}}\'\n'
    )
    result = subprocess.run(["bash", "-c", command], capture_output=True, text=True,
                            env={**os.environ, "MYCELIUM_HOOK_HOST": host},
                            check=False, timeout=30)
    assert result.returncode == 0
    assert "could not take the session lock" in result.stderr
    assert not (outside / sid / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_work_left_after_an_accepted_stop_reopens_and_is_enforced(tmp_path, host):
    """A hook whose wait outlasted the previous Stop's acceptance writes into
    a run directory with no transaction; the next Stop must not ignore it."""
    repo = _repo(tmp_path)
    sid = "after-accept"
    _start(host, repo, sid)
    assert _block_reason(_stop(host, repo, sid)) is None
    run_dir = _run_dir(repo, host, sid)
    assert not (run_dir / "active-session-log.tmp").exists()
    (run_dir / UNRECORDED).write_text(
        '{"kind": "work", "hook": "mycelium-activity-tracker.sh"}\n')

    reason = _block_reason(_stop(host, repo, sid))
    assert reason is not None and "UNRECORDED WORK: 1" in reason
    assert (run_dir / "active-session-log.tmp").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_lineage_note_survives_a_blocked_stop_until_reported(tmp_path, host):
    """A Stop that blocks (or fails finalization) must not consume a note it
    has not delivered on an accepting Stop."""
    repo = _repo(tmp_path)
    sid = "note-kept"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    payloads = _payloads(repo, sid)
    (repo / "model.py").write_text("x = 1\n")
    assert _run(host, "mycelium-activity-tracker.sh", repo,
                payloads["mycelium-activity-tracker.sh"]).returncode == 0
    lock = _hold_session_lock(run_dir)
    assert _run(host, "mycelium-read-tracker.sh", repo,
                payloads["mycelium-read-tracker.sh"],
                extra_env=FAST_TIMEOUT).returncode == 0
    (lock / "owner").unlink()
    lock.rmdir()

    assert _block_reason(_stop(host, repo, sid)) is not None
    assert (run_dir / UNRECORDED).exists()
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-20] triaged\n- noted\n")
    accepted = _stop(host, repo, sid)
    assert _block_reason(accepted) is None, accepted.stdout
    assert "UNRECORDED EVENTS: 1" in accepted.stdout
    assert not (run_dir / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_a_fifo_at_the_evidence_name_cannot_hang_the_hook(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "fifo-evidence"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    _hold_session_lock(run_dir)
    os.mkfifo(run_dir / UNRECORDED)

    started = time.monotonic()
    result = _run(host, "mycelium-activity-tracker.sh", repo,
                  _payloads(repo, sid)["mycelium-activity-tracker.sh"],
                  extra_env=FAST_TIMEOUT)

    assert result.returncode == 0
    assert time.monotonic() - started < 30


@pytest.mark.parametrize("host", HOSTS)
def test_stale_transaction_cleanup_keeps_unrecorded_work(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "stale-evidence"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    marker = run_dir / "active-session-log.tmp"
    lines = marker.read_text().splitlines()
    old = int(time.time()) - 7300
    marker.write_text(f"{lines[0]}\n{old}\n{lines[2]}\n")
    evidence = run_dir / UNRECORDED
    evidence.write_text('{"kind": "work", "hook": "mycelium-post-action.sh"}\n')
    os.utime(evidence, (old, old))

    resumed = _run(host, "mycelium-health.sh", repo,
                   {"cwd": str(repo), "source": "resume", "session_id": sid,
                    "hook_event_name": "SessionStart"})
    assert resumed.returncode == 0, resumed.stderr
    assert evidence.exists()

    reason = _block_reason(_stop(host, repo, sid))
    assert reason is not None and "UNRECORDED WORK: 1" in reason


@pytest.mark.parametrize("host", HOSTS)
def test_note_survives_a_failed_successor_snapshot(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "snapshot-fails"
    _start(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    payloads = _payloads(repo, sid)
    (repo / "model.py").write_text("x = 1\n")
    assert _run(host, "mycelium-activity-tracker.sh", repo,
                payloads["mycelium-activity-tracker.sh"]).returncode == 0
    lock = _hold_session_lock(run_dir)
    assert _run(host, "mycelium-read-tracker.sh", repo,
                payloads["mycelium-read-tracker.sh"],
                extra_env=FAST_TIMEOUT).returncode == 0
    (lock / "owner").unlink()
    lock.rmdir()
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-20] triaged\n- noted\n")
    wrapper = tmp_path / "no_snapshot.py"
    wrapper.write_text(
        "import os, sys\n"
        "if sys.argv[1:2] == ['snapshot']:\n"
        "    raise SystemExit(3)\n"
        f"os.execv(sys.executable, [sys.executable, {str(SESSION_CHANGES)!r}] + sys.argv[1:])\n"
    )
    failed = _run(host, "mycelium-stop-check.sh", repo,
                  {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                   "stop_hook_active": False},
                  extra_env={"MYCELIUM_SESSION_CHANGES_HELPER": str(wrapper)})
    assert _block_reason(failed) is not None, failed.stdout
    assert (run_dir / UNRECORDED).exists()

    accepted = _stop(host, repo, sid)
    assert _block_reason(accepted) is None, accepted.stdout
    assert "UNRECORDED EVENTS: 1" in accepted.stdout
    assert not (run_dir / UNRECORDED).exists()


@pytest.mark.parametrize("host", HOSTS)
def test_an_empty_evidence_file_is_not_work(tmp_path, host):
    """A writer that created the file but has not yet taken its flock."""
    repo = _repo(tmp_path)
    sid = "empty-evidence"
    _start(host, repo, sid)
    (_run_dir(repo, host, sid) / UNRECORDED).write_text("")
    assert _block_reason(_stop(host, repo, sid)) is None


@pytest.mark.parametrize("host", HOSTS)
def test_read_record_left_after_an_accepted_stop_is_reported(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "late-read"
    _start(host, repo, sid)
    assert _block_reason(_stop(host, repo, sid)) is None
    run_dir = _run_dir(repo, host, sid)
    (run_dir / UNRECORDED).write_text(
        '{"kind": "read", "hook": "mycelium-read-tracker.sh"}\n')

    stop = _stop(host, repo, sid)

    assert _block_reason(stop) is None, stop.stdout
    assert "UNRECORDED EVENTS: 1" in stop.stdout
    assert not (run_dir / UNRECORDED).exists()
    assert not (run_dir / "active-session-log.tmp").exists()
