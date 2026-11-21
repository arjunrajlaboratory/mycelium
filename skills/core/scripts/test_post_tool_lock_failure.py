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
treats that file as work of unknown extent: it blocks until `.living/` is
updated and says why. An intentional no-op leaves neither.
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
@pytest.mark.parametrize("hook", [
    "mycelium-activity-tracker.sh",
    "mycelium-post-action.sh",
    "mycelium-data-tracker.sh",
    "mycelium-read-tracker.sh",
])
def test_lock_timeout_leaves_marker_and_diagnostic(tmp_path, host, hook):
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
    assert "could not be recorded" in reason
    assert "0 files changed" not in reason
    assert (run_dir / UNRECORDED).is_file()

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
    assert "UNRECORDED WORK: 1 tool event(s)" in reason


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
