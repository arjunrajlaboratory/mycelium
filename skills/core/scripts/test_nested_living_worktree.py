"""A subproject's own .living/ inside a linked git worktree session.

The session works in a linked worktree's subproject folder
(<main>/.claude/worktrees/w/sub, which holds its own .living/). The repository
root is the worktree (resolved from the payload cwd), and the session's folder
is the worktree's subfolder. Notes in the worktree's sub/.living satisfy Stop;
notes in the main checkout's same-named sub/.living do not.

Claude Code runs hooks in the session folder; the Codex dispatcher runs them at
the worktree root with the payload cwd pointing at the subfolder.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
# (host, hook process runs in the session folder?)
HOSTS = [("claude", True), ("codex", False)]
SID = "wt-sub-session"


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
        handle.write("\n### [2026-11-21] worktree subproject note\n- noted\n")


def _stop_after_edit(tmp_path: Path, host: str, in_folder: bool, notes: str):
    main, worktree = _checkouts(tmp_path)
    sub = worktree / "sub"
    proc_cwd = sub if in_folder else worktree
    base = {"cwd": str(sub), "session_id": SID}
    start = _run(host, "mycelium-health.sh", proc_cwd,
                 {**base, "source": "startup", "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    (sub / "work.py").write_text("x = 1\n")
    edit = _run(host, "mycelium-activity-tracker.sh", proc_cwd,
                {**base, "tool_name": "Edit", "hook_event_name": "PostToolUse",
                 "tool_input": {"file_path": str(sub / "work.py")},
                 "tool_response": {"exit_code": 0}})
    assert edit.returncode == 0, edit.stderr
    _note({"worktree": sub / ".living", "main": main / "sub" / ".living"}[notes])
    stop = _run(host, "mycelium-stop-check.sh", proc_cwd,
                {**base, "hook_event_name": "Stop", "stop_hook_active": False})
    assert stop.returncode == 0, stop.stderr
    return stop


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_worktree_subproject_notes_satisfy_stop(tmp_path, host, in_folder):
    stop = _stop_after_edit(tmp_path, host, in_folder, "worktree")
    assert not _blocked(stop), stop.stdout


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_main_checkout_subproject_notes_do_not_count(tmp_path, host, in_folder):
    stop = _stop_after_edit(tmp_path, host, in_folder, "main")
    assert _blocked(stop), stop.stdout
