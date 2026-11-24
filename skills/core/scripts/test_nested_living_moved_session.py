"""A session that moves into a linked worktree and keeps notes in its
subproject's own .living/.

The session starts in the main checkout (which holds sub/.living), then moves
to <main>/.claude/worktrees/w/sub. A shell edit left behind in the main
checkout is carried to the worktree's Stop (a MOVED SESSION triage), and Stop
starts a work cycle for it. That cycle must fingerprint the session folder's
.living/ as well as the root's, or notes recorded in sub/.living afterwards
would never satisfy Stop.

Claude Code runs hooks in the session folder; the Codex dispatcher runs them at
the worktree root with the payload cwd pointing at the subfolder.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
# (host, hook process runs in the session folder?)
HOSTS = [("claude", True), ("codex", False)]
SID = "moved-sub-session"


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True)


def _checkouts(tmp_path: Path) -> tuple[Path, Path]:
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "-b", "main", str(main))
    _git("-C", str(main), "config", "user.email", "t@t")
    _git("-C", str(main), "config", "user.name", "T")
    for folder in (main, main / "sub"):
        (folder / ".living").mkdir(parents=True)
        for name in ("learnings.md", "decisions.md", "conventions.md"):
            (folder / ".living" / name).write_text(f"# {name}\n")
    (main / "sub" / "work.py").write_text("x = 0\n")
    (main / "other.py").write_text("y = 0\n")
    (main / ".gitignore").write_text(".mycelium/\n.claude/\n")
    _git("-C", str(main), "add", "-A")
    _git("-C", str(main), "commit", "-q", "-m", "init")
    worktree = main / ".claude" / "worktrees" / "w"
    _git("-C", str(main), "worktree", "add", "-q", "-b", "w", str(worktree))
    return main, worktree


def _run(host: str, name: str, proc_cwd: Path, payload: dict):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    env.pop("CLAUDE_PROJECT_DIR", None)
    return subprocess.run(
        [str(HOOKS_DIR / name)], cwd=proc_cwd, input=json.dumps(payload),
        text=True, capture_output=True, env=env, check=False, timeout=120,
    )


def _blocked(result) -> bool:
    return (bool(result.stdout.strip())
            and json.loads(result.stdout).get("decision") == "block")


def _note(living: Path) -> None:
    with (living / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-23] moved subproject note\n- noted\n")


def _moved_session(tmp_path: Path, host: str, in_folder: bool):
    main, worktree = _checkouts(tmp_path)
    sub = worktree / "sub"
    start_cwd = main / "sub" if in_folder else main
    proc_cwd = sub if in_folder else worktree
    start = _run(host, "mycelium-health.sh", start_cwd,
                 {"cwd": str(main / "sub"), "session_id": SID,
                  "source": "startup", "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    # A shell edit in the old checkout: no hook sees it.
    (main / "other.py").write_text("y = 1\n")
    base = {"cwd": str(sub), "session_id": SID}
    (sub / "work.py").write_text("x = 1\n")
    edit = _run(host, "mycelium-activity-tracker.sh", proc_cwd,
                {**base, "tool_name": "Edit", "hook_event_name": "PostToolUse",
                 "tool_input": {"file_path": str(sub / "work.py")},
                 "tool_response": {"exit_code": 0}})
    assert edit.returncode == 0, edit.stderr
    return sub, base, proc_cwd


def _stop(host: str, proc_cwd: Path, base: dict, active: bool):
    stop = _run(host, "mycelium-stop-check.sh", proc_cwd,
                {**base, "hook_event_name": "Stop", "stop_hook_active": active})
    assert stop.returncode == 0, stop.stderr
    return stop


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_moved_session_subproject_notes_satisfy_stop(tmp_path, host, in_folder):
    sub, base, proc_cwd = _moved_session(tmp_path, host, in_folder)
    first = _stop(host, proc_cwd, base, active=False)
    assert _blocked(first), first.stdout
    assert "MOVED SESSION" in json.loads(first.stdout)["reason"]
    _note(sub / ".living")
    second = _stop(host, proc_cwd, base, active=True)
    assert not _blocked(second), second.stdout


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_moved_session_root_notes_still_satisfy_stop(tmp_path, host, in_folder):
    sub, base, proc_cwd = _moved_session(tmp_path, host, in_folder)
    first = _stop(host, proc_cwd, base, active=False)
    assert _blocked(first), first.stdout
    _note(sub.parent / ".living")
    second = _stop(host, proc_cwd, base, active=True)
    assert not _blocked(second), second.stdout
