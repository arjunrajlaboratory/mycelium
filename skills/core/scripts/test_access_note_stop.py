"""The access note through the real hooks, on both hosts.

A step that writes a table and calls no `record_ids` gets an advisory
ACCESS NOTE at Stop; the same step with a real `record_ids` call does not.
The note never changes Stop's decision, and a failing helper changes
nothing at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOKS_DIR = HERE.parent / "hooks"
HOSTS = ["claude", "codex"]
COMMAND = "python3 analysis/qc/scripts/filter.py"


def _repo(tmp_path: Path, *, records: bool) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    scripts = repo / "analysis" / "qc" / "scripts"
    scripts.mkdir(parents=True)
    (repo / "analysis" / "qc" / "outputs").mkdir()
    (repo / "data").mkdir()
    (repo / "data" / "cells.csv").write_text("cell,n\nc1,5\nc2,1\nc3,9\n")
    body = [
        "import sys",
        f"sys.path.insert(0, {str(HERE)!r})",
        "import pandas as pd",
        "from register_value import record_ids",
        "df = pd.read_csv('data/cells.csv')",
        "kept = df[df.n > 2]",
    ]
    if records:
        body.append("record_ids('cells_after_qc', kept.cell, axis='row', role='used')")
    body.append("kept.to_csv('analysis/qc/outputs/kept.csv')")
    (scripts / "filter.py").write_text("\n".join(body) + "\n")
    old = 1_700_000_000
    for path in (repo / "data" / "cells.csv", scripts / "filter.py"):
        os.utime(path, (old, old))
    return repo


def _run(host: str, name: str, repo: Path, payload: dict, env_extra=None):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    env.update(env_extra or {})
    return subprocess.run([str(HOOKS_DIR / name)], cwd=repo, input=json.dumps(payload),
                          text=True, capture_output=True, env=env, check=False)


def _turn(host: str, repo: Path, sid: str, env_extra=None) -> subprocess.CompletedProcess:
    start = _run(host, "mycelium-health.sh", repo,
                 {"cwd": str(repo), "source": "startup", "session_id": sid,
                  "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    pre = _run(host, "mycelium-io-observer.sh", repo,
               {"cwd": str(repo), "session_id": sid, "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_use_id": "call_1",
                "tool_input": {"command": COMMAND}})
    assert pre.returncode == 0, pre.stderr
    # What the host runs between Pre and Post: the real script.
    ran = subprocess.run([sys.executable, "analysis/qc/scripts/filter.py"], cwd=repo,
                         capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    post = _run(host, "mycelium-data-tracker.sh", repo,
                {"cwd": str(repo), "session_id": sid, "hook_event_name": "PostToolUse",
                 "tool_name": "Bash", "tool_use_id": "call_1",
                 "tool_input": {"command": COMMAND},
                 "tool_response": {"exit_code": 0} if host == "claude" else ""})
    assert post.returncode == 0, post.stderr
    stop = _run(host, "mycelium-stop-check.sh", repo,
                {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                 "stop_hook_active": False}, env_extra)
    assert stop.returncode == 0, stop.stderr
    return stop


def _decision(stop: subprocess.CompletedProcess) -> str | None:
    return json.loads(stop.stdout).get("decision") if stop.stdout.strip() else None


def _without_note(text: str) -> str:
    return "\n\n".join(p for p in text.split("\n\n") if not p.startswith("ACCESS NOTE:"))


@pytest.mark.parametrize("host", HOSTS)
def test_table_step_without_record_ids_gets_the_note(tmp_path, host):
    repo = _repo(tmp_path, records=False)
    stop = _turn(host, repo, "s1")
    assert "ACCESS NOTE:" in stop.stdout
    assert "analysis/qc/scripts/filter.py" in stop.stdout
    assert "analysis/qc/outputs/kept.csv" in stop.stdout
    # The note leaves no state behind for a later Stop to repeat.
    assert not list((repo / ".mycelium").rglob("access-note.tmp"))


@pytest.mark.parametrize("host", HOSTS)
def test_real_record_ids_call_joins_and_suppresses_the_note(tmp_path, host):
    repo = _repo(tmp_path, records=True)
    stop = _turn(host, repo, "s1")
    assert (repo / "analysis" / "qc" / "outputs" / "access.json").exists()
    assert "ACCESS NOTE" not in stop.stdout


def _silent_helper(tmp_path: Path) -> dict:
    helper = tmp_path / "silent_note.py"
    helper.write_text("")
    return {"MYCELIUM_ACCESS_NOTE_HELPER": str(helper)}


@pytest.mark.parametrize("host", HOSTS)
def test_note_never_changes_the_stop_decision(tmp_path, host):
    noted_repo = _repo(tmp_path / "a", records=False)
    silent_repo = _repo(tmp_path / "b", records=False)
    noted = _turn(host, noted_repo, "s1")
    silent = _turn(host, silent_repo, "s1", _silent_helper(tmp_path))
    assert "ACCESS NOTE:" in noted.stdout and "ACCESS NOTE" not in silent.stdout
    assert _decision(noted) == _decision(silent)
    # Same output apart from the note paragraph itself.
    for key in ("reason", "systemMessage"):
        a = json.loads(noted.stdout).get(key)
        b = json.loads(silent.stdout).get(key)
        if a is None and b is None:
            continue
        a = _without_note(a.replace(str(noted_repo), "<repo>"))
        b = b.replace(str(silent_repo), "<repo>")
        assert a == b


@pytest.mark.parametrize("host", HOSTS)
def test_failing_helper_fails_open(tmp_path, host):
    broken = tmp_path / "broken_note.py"
    broken.write_text("import sys\nprint('garbage {')\nsys.exit(3)\n")
    failed_repo = _repo(tmp_path / "a", records=False)
    silent_repo = _repo(tmp_path / "b", records=False)
    failed = _turn(host, failed_repo, "s1", {"MYCELIUM_ACCESS_NOTE_HELPER": str(broken)})
    silent = _turn(host, silent_repo, "s1", _silent_helper(tmp_path))
    assert "garbage" not in failed.stdout and "ACCESS NOTE" not in failed.stdout
    assert (failed.stdout.replace(str(failed_repo), "<repo>")
            == silent.stdout.replace(str(silent_repo), "<repo>"))
