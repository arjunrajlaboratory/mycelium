"""A subproject's own .living/ keeps satisfying Stop after the first turn.

Hosts run Stop after every turn, and an accepted Stop leaves the next turn a
carried baseline. Stop takes that baseline, refreshes it after a notes-only
turn, and starts a work cycle for unrecorded work, so each of those snapshots
must record the notes folders on the session's path. The Codex dispatcher runs
hooks from the repository root, so the folder comes from the payload's cwd.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
# (host, hook process runs in the session folder?)
HOSTS = [("claude", True), ("codex", False)]
FAST_TIMEOUT = {"MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS": "2"}


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for folder in (repo, repo / "sub", repo / "other"):
        living = folder / ".living"
        living.mkdir(parents=True)
        for name in ("learnings.md", "decisions.md", "conventions.md"):
            (living / name).write_text(f"# {name}\n")
    (repo / "sub" / "work.py").write_text("x = 0\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
         "commit", "-q", "-m", "projects"],
        check=True,
    )
    return repo


class Session:
    def __init__(self, repo: Path, host: str, in_folder: bool, sid: str):
        self.repo = repo
        self.host = host
        self.folder = repo / "sub"
        self.hook_cwd = self.folder if in_folder else repo
        self.sid = sid

    def run(self, name: str, payload: dict, extra_env=None):
        env = os.environ.copy()
        env["MYCELIUM_HOOK_HOST"] = self.host
        env.pop("CLAUDE_PROJECT_DIR", None)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [str(HOOKS_DIR / name)],
            cwd=self.hook_cwd,
            input=json.dumps({"cwd": str(self.folder), "session_id": self.sid,
                              **payload}),
            text=True,
            capture_output=True,
            env=env,
            check=False,
            timeout=120,
        )

    def start(self):
        result = self.run("mycelium-health.sh",
                          {"source": "startup", "hook_event_name": "SessionStart"})
        assert result.returncode == 0, result.stderr

    def stop(self):
        result = self.run("mycelium-stop-check.sh",
                          {"hook_event_name": "Stop", "stop_hook_active": False})
        assert result.returncode == 0, result.stderr
        return result

    def edit(self, extra_env=None):
        path = self.folder / "work.py"
        path.write_text(path.read_text() + "x += 1\n")
        result = self.run("mycelium-activity-tracker.sh",
                          {"tool_name": "Edit", "hook_event_name": "PostToolUse",
                           "tool_input": {"file_path": str(path)},
                           "tool_response": {"exit_code": 0}},
                          extra_env=extra_env)
        assert result.returncode == 0, result.stderr

    @property
    def run_dir(self) -> Path:
        return self.repo / ".mycelium" / "run" / self.host / self.sid


def _note(living: Path, text: str) -> None:
    with (living / "learnings.md").open("a") as handle:
        handle.write(f"\n### [2026-11-21] {text}\n- noted\n")


def _blocked(stop) -> bool:
    return bool(stop.stdout.strip()) and json.loads(stop.stdout).get(
        "decision"
    ) == "block"


def _working_turn(session: Session, text: str):
    session.edit()
    _note(session.folder / ".living", text)
    return session.stop()


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_subproject_notes_satisfy_stop_on_later_turns(tmp_path, host, in_folder):
    session = Session(_repo(tmp_path), host, in_folder, "sub-session")
    session.start()

    results = [_working_turn(session, f"turn {n}") for n in (1, 2, 3)]

    assert [_blocked(r) for r in results] == [False, False, False], [
        r.stdout for r in results
    ]


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_a_later_turn_without_notes_still_blocks(tmp_path, host, in_folder):
    session = Session(_repo(tmp_path), host, in_folder, "sub-session")
    session.start()
    assert not _blocked(_working_turn(session, "first"))

    session.edit()

    assert _blocked(session.stop())


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_a_later_turn_with_notes_off_the_session_path_still_blocks(
    tmp_path, host, in_folder
):
    session = Session(_repo(tmp_path), host, in_folder, "sub-session")
    session.start()
    assert not _blocked(_working_turn(session, "first"))

    session.edit()
    _note(session.repo / "other" / ".living", "wrong project")

    assert _blocked(session.stop())


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_a_notes_only_turn_in_the_subproject_opens_nothing(tmp_path, host, in_folder):
    session = Session(_repo(tmp_path), host, in_folder, "sub-session")
    session.start()
    assert not _blocked(session.stop())
    logs_before = sorted(p.name for p in (session.repo / ".living" / "log").iterdir())

    _note(session.folder / ".living", "a lesson recorded on its own")
    stop = session.stop()

    assert stop.stdout == "", stop.stdout
    assert not (session.run_dir / "active-session-log.tmp").exists()
    assert sorted(
        p.name for p in (session.repo / ".living" / "log").iterdir()
    ) == logs_before


@pytest.mark.parametrize("host,in_folder", HOSTS)
def test_unrecorded_work_is_satisfied_by_subproject_notes(tmp_path, host, in_folder):
    session = Session(_repo(tmp_path), host, in_folder, "sub-session")
    session.start()
    lock = session.run_dir / "mycelium-session.lock"
    lock.mkdir()
    (lock / "owner").write_text(f"{os.getpid()} {int(time.time())}\n")
    session.edit(extra_env=FAST_TIMEOUT)
    (lock / "owner").unlink()
    lock.rmdir()

    first = session.stop()
    assert _blocked(first) and "UNRECORDED WORK" in json.loads(first.stdout)["reason"]
    _note(session.folder / ".living", "triaged")

    assert not _blocked(session.stop())
