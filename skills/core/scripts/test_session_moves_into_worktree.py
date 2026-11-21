"""A session that moves from the main checkout into a linked worktree keeps
one transaction.

A Claude Code session can start in the main checkout and then move into a
linked worktree partway through (a Bash `cd .claude/worktrees/<name>`, or the
EnterWorktree tool). SessionStart reserves a session log and run state in the
main checkout; every later hook's payload `cwd` is the worktree. Before this
fix the first work hook in the worktree opened a second transaction there,
Stop finalized that one, and the main checkout kept an untracked stub log and
stale `.mycelium/run/<host>/<sid>/` state that nothing finalized or removed.
That stub landed in the working tree of whichever session owned the main
checkout.

Now the hook that opens the transaction in the new checkout takes over the
session's reservation in the sibling checkout: it carries the start time and
any handoff written there, removes the stub log and run state, and carries
files changed there since the session started for the new checkout's Stop to
triage. A reservation that already recorded work is left in place.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOSTS = ["claude", "codex"]
SID = "moving-session"
OTHER = "main-owner-session"
EXTRA_ENV: dict[str, str] = {}


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _checkouts(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", str(main))
    _git("-C", str(main), "config", "user.email", "test@test.com")
    _git("-C", str(main), "config", "user.name", "Test")
    living = main / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (main / "analysis.py").write_text("x = 1\n")
    (main / "report.py").write_text("y = 1\n")
    (main / ".gitignore").write_text(".mycelium/\n.claude/\n")
    _git("-C", str(main), "add", "-A")
    _git("-C", str(main), "commit", "-q", "-m", "init")
    worktree = main / ".claude" / "worktrees" / "side"
    _git("-C", str(main), "worktree", "add", "-q", "-b", "side", str(worktree))
    return main, worktree


def _run(host: str, name: str, cwd: Path, payload: dict):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    env.update(EXTRA_ENV)
    # The host runs hooks in the session's current directory.
    return subprocess.run(
        [str(HOOKS_DIR / name)],
        cwd=cwd,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _start(host: str, cwd: Path, sid: str = SID):
    result = _run(host, "mycelium-health.sh", cwd,
                  {"cwd": str(cwd), "session_id": sid, "source": "startup",
                   "hook_event_name": "SessionStart"})
    assert result.returncode == 0, result.stderr
    return result


def _edit(host: str, cwd: Path, path: Path, text: str, sid: str = SID):
    path.write_text(text)
    result = _run(host, "mycelium-activity-tracker.sh", cwd,
                  {"cwd": str(cwd), "session_id": sid, "tool_name": "Edit",
                   "hook_event_name": "PostToolUse",
                   "tool_input": {"file_path": str(path)},
                   "tool_response": {"exit_code": 0}})
    assert result.returncode == 0, result.stderr


def _stop(host: str, cwd: Path, active: bool, sid: str = SID):
    result = _run(host, "mycelium-stop-check.sh", cwd,
                  {"cwd": str(cwd), "session_id": sid,
                   "hook_event_name": "Stop", "stop_hook_active": active})
    assert result.returncode == 0, result.stderr
    return result


def _blocked(result: subprocess.CompletedProcess) -> bool:
    return bool(result.stdout.strip()) and json.loads(result.stdout).get(
        "decision"
    ) == "block"


def _snapshot(root: Path) -> dict[str, bytes]:
    files = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if ".git" in rel.parts or ".claude" in rel.parts:
            continue
        # Lock bookkeeping: each holder rewrites its pid and time.
        if rel.parts[:2] == (".mycelium", "locks"):
            continue
        if path.is_file():
            files[str(rel)] = path.read_bytes()
        elif path.is_dir() and not any(path.iterdir()):
            files[str(rel) + "/"] = b""
    return files


def _run_dir(root: Path, host: str, sid: str = SID) -> Path:
    return root / ".mycelium" / "run" / host / sid


def _session_logs(root: Path) -> list[Path]:
    log_dir = root / ".living" / "log"
    if not log_dir.is_dir():
        return []
    return [p for p in sorted(log_dir.glob("*.md")) if p.name != "LOG_REGISTRY.md"]


def _finalized(paths: list[Path]) -> list[Path]:
    return [p for p in paths if re.search(r"^ended: \d", p.read_text(), re.M)]


def _owned_log(root: Path, host: str, sid: str = SID) -> Path:
    marker = _run_dir(root, host, sid) / "active-session-log.tmp"
    return Path(marker.read_text().splitlines()[0])


@pytest.mark.parametrize("host", HOSTS)
def test_moving_into_a_worktree_keeps_one_transaction(tmp_path, host):
    main, worktree = _checkouts(tmp_path)
    # Another session owns the main checkout's working tree: it has an open
    # transaction and uncommitted work there.
    _start(host, main, sid=OTHER)
    (main / "report.py").write_text("y = 2\n")

    main_before = _snapshot(main)
    _start(host, main)
    start_ts = int((_run_dir(main, host) / "session-start-ts.tmp").read_text())
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(start_ts))
    handoff = _run_dir(main, host) / "last-session.md"
    handoff.write_text("# Handoff written before the move\n")

    # The session moves into the worktree and works there.
    _edit(host, worktree, worktree / "analysis.py", "x = 2\n")

    # The transaction now lives in the worktree, carrying the session's start
    # and the handoff written at the path SessionStart announced.
    assert not _run_dir(main, host).exists()
    assert (_run_dir(worktree, host) / "last-session.md").read_text() \
        == "# Handoff written before the move\n"
    log = _owned_log(worktree, host)
    assert re.search(r"^started: (.+)$", log.read_text(), re.M).group(1) \
        == started

    stop = _stop(host, worktree, active=False)
    assert _blocked(stop), stop.stdout
    reason = json.loads(stop.stdout)["reason"]
    assert "analysis.py" in reason
    assert "report.py" not in reason
    assert str(_run_dir(worktree, host) / "last-session.md") in reason
    assert str(main) + "/.mycelium" not in reason

    with (worktree / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### Worktree learning\n- noted\n")
    stop = _stop(host, worktree, active=True)
    assert not _blocked(stop), stop.stdout

    finalized = _finalized(_session_logs(main) + _session_logs(worktree))
    assert finalized == [log]
    assert _snapshot(main) == main_before
    assert not _run_dir(main, host).exists()
    # The other session's transaction is untouched.
    assert (_run_dir(main, host, OTHER) / "active-session-log.tmp").is_file()


@pytest.mark.parametrize("host", HOSTS)
def test_a_stop_in_the_worktree_drops_an_idle_reservation(tmp_path, host):
    """A session that moved but did no work leaves nothing in either root."""
    main, worktree = _checkouts(tmp_path)
    _start(host, main, sid=OTHER)
    main_before = _snapshot(main)
    _start(host, main)

    stop = _stop(host, worktree, active=False)
    assert not _blocked(stop), stop.stdout

    assert _snapshot(main) == main_before
    assert not _run_dir(main, host).exists()
    assert _session_logs(worktree) == []
    assert not (_run_dir(worktree, host) / "active-session-log.tmp").exists()


@pytest.mark.parametrize("who", ["other session", "this session's shell"])
def test_changes_left_in_the_main_checkout_are_triaged_once(tmp_path, who):
    """A shell edit records no activity, so files that changed in the main
    checkout after this session started there are carried to the worktree's
    Stop: it asks for triage once, naming them, and the main checkout still
    loses this session's reservation. Whoever made the change, it is left as
    it is."""
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    (main / "report.py").write_text("y = 2\n")  # no hook sees this write

    _edit("claude", worktree, worktree / "analysis.py", "x = 2\n")

    assert not _run_dir(main, "claude").exists()
    assert _session_logs(main) == []
    stop = _stop("claude", worktree, active=False)
    assert _blocked(stop), stop.stdout
    reason = json.loads(stop.stdout)["reason"]
    assert "MOVED SESSION" in reason and "report.py" in reason
    assert str(main) in reason

    with (worktree / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### Worktree learning\n- noted\n")
    stop = _stop("claude", worktree, active=True)
    assert not _blocked(stop), stop.stdout
    assert (main / "report.py").read_text() == "y = 2\n"
    assert len(_finalized(_session_logs(worktree))) == 1


def test_an_idle_move_still_triages_changes_left_behind(tmp_path):
    """Stop in the worktree with no work there still takes over a reservation
    whose checkout changed, and asks for triage."""
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    (main / "report.py").write_text("y = 2\n")

    stop = _stop("claude", worktree, active=False)
    assert _blocked(stop), stop.stdout
    assert "report.py" in json.loads(stop.stdout)["reason"]
    assert not _run_dir(main, "claude").exists()
    assert _session_logs(main) == []


def test_a_reservation_with_recorded_work_is_kept(tmp_path):
    """Work recorded in the main checkout before the move is never dropped:
    that transaction stays where it is for its own checkout to judge."""
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    _edit("claude", main, main / "report.py", "y = 2\n")
    stub = _owned_log(main, "claude")

    _edit("claude", worktree, worktree / "analysis.py", "x = 2\n")

    assert stub.is_file()
    assert (_run_dir(main, "claude") / "active-session-log.tmp").is_file()
    stop = _stop("claude", main, active=False)
    assert _blocked(stop), stop.stdout
    assert "report.py" in json.loads(stop.stdout)["reason"]


def test_an_unrelated_repository_is_not_searched(tmp_path):
    """Only linked worktrees of the same repository share a transaction."""
    main, _ = _checkouts(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    other, _ = _checkouts(tmp_path / "elsewhere")
    _start("claude", main)
    stub = _owned_log(main, "claude")

    _edit("claude", other, other / "analysis.py", "x = 2\n")

    assert stub.is_file()
    assert (_run_dir(main, "claude") / "active-session-log.tmp").is_file()


def _resume(host: str, cwd: Path, sid: str = SID):
    result = _run(host, "mycelium-health.sh", cwd,
                  {"cwd": str(cwd), "session_id": sid, "source": "resume",
                   "hook_event_name": "SessionStart"})
    assert result.returncode == 0, result.stderr
    return result


def test_stop_survives_a_long_moved_change_list(tmp_path):
    """A moved-change list larger than a pipe buffer must not kill Stop with
    SIGPIPE (exit 141, empty stdout), which would fail open on every retry."""
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    _edit("claude", worktree, worktree / "analysis.py", "x = 2\n")
    long_dir = "d" * 100
    (_run_dir(worktree, "claude") / "moved-checkout-changes.tmp").write_text(
        "".join(f"{main}/{long_dir}/file_{i:05d}.py\n" for i in range(3000))
    )
    stop = _stop("claude", worktree, active=False)
    assert _blocked(stop), stop.stdout
    reason = json.loads(stop.stdout)["reason"]
    assert "3000 project file(s)" in reason and "(+2985 more)" in reason
    # The record is consumed into a work cycle: a retry still blocks.
    stop = _stop("claude", worktree, active=True)
    assert _blocked(stop), stop.stdout


def test_moving_back_keeps_owed_reflection(tmp_path):
    """main -> worktree -> main: the reminder the worktree's Stop started for
    changes left in main must survive the move back."""
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    (main / "report.py").write_text("y = 2\n")  # shell edit, no hook
    stop = _stop("claude", worktree, active=False)
    assert _blocked(stop), stop.stdout

    stop = _stop("claude", main, active=True)
    assert _blocked(stop), stop.stdout
    assert "report.py" in json.loads(stop.stdout)["reason"] \
        or "MOVED SESSION" in json.loads(stop.stdout)["reason"]
    assert not _run_dir(worktree, "claude").exists()


def test_moving_back_keeps_pending_moved_changes(tmp_path):
    """A moved-change record not yet enforced is carried on the next move."""
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    (main / "report.py").write_text("y = 2\n")
    _resume("claude", worktree)
    assert (_run_dir(worktree, "claude") / "moved-checkout-changes.tmp").exists()
    _resume("claude", main)

    stop = _stop("claude", main, active=False)
    assert _blocked(stop), stop.stdout
    assert "report.py" in json.loads(stop.stdout)["reason"]


def test_a_handoff_that_cannot_move_is_kept(tmp_path):
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    (_run_dir(main, "claude") / "last-session.md").write_text("FROM MAIN\n")
    dest = _run_dir(worktree, "claude")
    dest.mkdir(parents=True)
    (dest / "last-session.md").write_text("ALREADY HERE\n")

    _edit("claude", worktree, worktree / "analysis.py", "x = 2\n")

    text = (dest / "last-session.md").read_text()
    assert "ALREADY HERE" in text and "FROM MAIN" in text


def test_a_relative_state_dir_override_is_followed(tmp_path, monkeypatch):
    monkeypatch.setitem(EXTRA_ENV, "MYCELIUM_STATE_DIR", ".mycelium-alt")
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    alt_run = main / ".mycelium-alt" / "run" / "claude" / SID
    assert (alt_run / "active-session-log.tmp").is_file()

    _edit("claude", worktree, worktree / "analysis.py", "x = 2\n")

    assert not alt_run.exists()
    assert _session_logs(main) == []


def test_a_malformed_owner_token_is_not_taken(tmp_path):
    main, worktree = _checkouts(tmp_path)
    _start("claude", main)
    stub = _owned_log(main, "claude")
    owner = _run_dir(main, "claude") / "active-session-owner-id.tmp"
    owner.write_text(f"{SID}\nextra\n")

    _edit("claude", worktree, worktree / "analysis.py", "x = 2\n")

    assert stub.is_file()
    assert owner.is_file()
