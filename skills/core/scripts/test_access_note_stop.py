"""The access note through the real hooks, on both hosts.

A script that writes a table and calls no `record_ids` gets an advisory
ACCESS NOTE when Stop accepts: never in a block reason, and only once per
script at the same content. The same script with a real `record_ids` call
gets none. The note never changes Stop's decision, and nothing about it
can fail Stop: a failing helper, or a directory squatting on its file,
changes nothing at all.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
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
                          text=True, capture_output=True, env=env, check=False, timeout=120)


def _call(host: str, repo: Path, sid: str, call: str) -> None:
    pre = _run(host, "mycelium-io-observer.sh", repo,
               {"cwd": str(repo), "session_id": sid, "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_use_id": call,
                "tool_input": {"command": COMMAND}})
    assert pre.returncode == 0, pre.stderr
    # What the host runs between Pre and Post: the real script.
    ran = subprocess.run([sys.executable, "analysis/qc/scripts/filter.py"], cwd=repo,
                         capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    post = _run(host, "mycelium-data-tracker.sh", repo,
                {"cwd": str(repo), "session_id": sid, "hook_event_name": "PostToolUse",
                 "tool_name": "Bash", "tool_use_id": call,
                 "tool_input": {"command": COMMAND},
                 "tool_response": {"exit_code": 0} if host == "claude" else ""})
    assert post.returncode == 0, post.stderr


def _stop(host, repo, sid, env_extra=None, *, retry=False) -> subprocess.CompletedProcess:
    stop = _run(host, "mycelium-stop-check.sh", repo,
                {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                 "stop_hook_active": retry}, env_extra)
    assert stop.returncode == 0, stop.stderr
    return stop


def _turn(host: str, repo: Path, sid: str, env_extra=None) -> subprocess.CompletedProcess:
    """SessionStart, one run of the script, then Stop (which blocks: .living/
    was not updated)."""
    start = _run(host, "mycelium-health.sh", repo,
                 {"cwd": str(repo), "source": "startup", "session_id": sid,
                  "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    _call(host, repo, sid, "call_1")
    return _stop(host, repo, sid, env_extra)


def _accept(host: str, repo: Path, sid: str, env_extra=None) -> subprocess.CompletedProcess:
    """Update .living/ and retry Stop, which then accepts."""
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + f"- learned {time.time_ns()}\n")
    return _stop(host, repo, sid, env_extra, retry=True)


def _out(stop: subprocess.CompletedProcess) -> dict:
    return json.loads(stop.stdout) if stop.stdout.strip() else {}


def _text(stop: subprocess.CompletedProcess) -> str:
    out = _out(stop)
    parts = [out.get("reason"), out.get("hookSpecificOutput", {}).get("additionalContext"),
             out.get("systemMessage")]
    return "\n\n".join(p for p in parts if p)


def _silent_helper(tmp_path: Path) -> dict:
    helper = tmp_path / "silent_note.py"
    helper.write_text("")
    return {"MYCELIUM_ACCESS_NOTE_HELPER": str(helper)}


def _without_note(text: str) -> str:
    return "\n\n".join(p for p in text.split("\n\n") if not p.startswith("ACCESS NOTE:"))


@pytest.mark.parametrize("host", HOSTS)
def test_note_appears_only_when_stop_accepts(tmp_path, host):
    repo = _repo(tmp_path, records=False)
    blocked = _turn(host, repo, "s1")
    assert _out(blocked).get("decision") == "block"
    assert "ACCESS NOTE" not in blocked.stdout
    accepted = _accept(host, repo, "s1")
    assert _out(accepted).get("decision") is None
    text = _text(accepted)
    assert "ACCESS NOTE: 1 script(s) this session" in text
    assert "analysis/qc/scripts/filter.py -> analysis/qc/outputs/kept.csv" in text
    # Only `systemMessage` carries it. On Claude, `additionalContext` on an
    # accepted Stop would make Claude take another turn on advice alone.
    assert "ACCESS NOTE" in _out(accepted)["systemMessage"]
    if host == "claude":
        context = _out(accepted).get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "ACCESS NOTE" not in context
    assert not list((repo / ".mycelium").rglob("access-note.tmp"))


@pytest.mark.parametrize("host", HOSTS)
def test_same_script_at_the_same_content_is_named_once(tmp_path, host):
    repo = _repo(tmp_path, records=False)
    _turn(host, repo, "s1")
    assert "ACCESS NOTE" in _accept(host, repo, "s1").stdout
    # The next transaction runs the unchanged script again.
    _call(host, repo, "s1", "call_2")
    _stop(host, repo, "s1")
    assert "ACCESS NOTE" not in _accept(host, repo, "s1").stdout


@pytest.mark.parametrize("host", HOSTS)
def test_real_record_ids_call_joins_and_suppresses_the_note(tmp_path, host):
    repo = _repo(tmp_path, records=True)
    _turn(host, repo, "s1")
    accepted = _accept(host, repo, "s1")
    assert (repo / "analysis" / "qc" / "outputs" / "access.json").exists()
    assert "ACCESS NOTE" not in accepted.stdout


@pytest.mark.parametrize("host", HOSTS)
def test_rerun_with_record_ids_both_times_gets_no_note(tmp_path, host):
    repo = _repo(tmp_path, records=True)
    _turn(host, repo, "s1")
    time.sleep(3)  # past the clock slack: run 2 replaces run 1's record
    _call(host, repo, "s1", "call_2")
    assert "ACCESS NOTE" not in _accept(host, repo, "s1").stdout


@pytest.mark.parametrize("host", HOSTS)
def test_note_never_changes_the_stop_decision(tmp_path, host):
    noted_repo = _repo(tmp_path / "a", records=False)
    silent_repo = _repo(tmp_path / "b", records=False)
    silent_env = _silent_helper(tmp_path)
    blocked_noted = _turn(host, noted_repo, "s1")
    blocked_silent = _turn(host, silent_repo, "s1", silent_env)
    assert (blocked_noted.stdout.replace(str(noted_repo), "<repo>")
            == blocked_silent.stdout.replace(str(silent_repo), "<repo>"))
    noted = _accept(host, noted_repo, "s1")
    silent = _accept(host, silent_repo, "s1", silent_env)
    assert "ACCESS NOTE:" in noted.stdout and "ACCESS NOTE" not in silent.stdout
    assert _out(noted).get("decision") == _out(silent).get("decision")
    assert (_without_note(_text(noted)).replace(str(noted_repo), "<repo>")
            == _text(silent).replace(str(silent_repo), "<repo>"))
    if host == "claude":
        # What Claude itself receives is unchanged (often nothing at all).
        silent_out = json.loads(silent.stdout.replace(str(silent_repo), str(noted_repo))
                                or "{}")
        assert _out(noted).get("hookSpecificOutput") == silent_out.get("hookSpecificOutput")


@pytest.mark.parametrize("host", HOSTS)
def test_silent_accepting_stop_gets_only_a_user_message(tmp_path, host):
    """A Stop that would say nothing to the model still says nothing to it."""
    repo = _repo(tmp_path, records=False)
    _turn(host, repo, "s1")
    _accept(host, repo, "s1")
    # A later idle turn whose only work is a rerun of an edited script.
    script = repo / "analysis" / "qc" / "scripts" / "filter.py"
    # New content that writes a different table.
    script.write_text(script.read_text().replace("df.n > 2", "df.n > 6"))
    _call(host, repo, "s1", "call_2")
    stop = _stop(host, repo, "s1")
    if _out(stop).get("decision") == "block":
        stop = _accept(host, repo, "s1")
    out = _out(stop)
    assert "ACCESS NOTE" in out.get("systemMessage", "")
    if host == "claude":
        assert "ACCESS NOTE" not in out.get("hookSpecificOutput", {}).get(
            "additionalContext", "")


@pytest.mark.parametrize("host", HOSTS)
def test_failing_helper_fails_open(tmp_path, host):
    broken = tmp_path / "broken_note.py"
    broken.write_text("import sys\nprint('garbage {')\nsys.exit(3)\n")
    broken_env = {"MYCELIUM_ACCESS_NOTE_HELPER": str(broken)}
    failed_repo = _repo(tmp_path / "a", records=False)
    silent_repo = _repo(tmp_path / "b", records=False)
    silent_env = _silent_helper(tmp_path)
    for step in (_turn, _accept):
        failed = step(host, failed_repo, "s1", broken_env)
        silent = step(host, silent_repo, "s1", silent_env)
        assert "garbage" not in failed.stdout and "ACCESS NOTE" not in failed.stdout
        assert (failed.stdout.replace(str(failed_repo), "<repo>")
                == silent.stdout.replace(str(silent_repo), "<repo>"))


@pytest.mark.parametrize("host", HOSTS)
def test_directory_squatting_on_the_note_file_cannot_fail_stop(tmp_path, host):
    repo = _repo(tmp_path, records=False)
    _turn(host, repo, "s1")
    for state in {p.parent for p in (repo / ".mycelium").rglob("*.tmp")}:
        (state / "access-note.tmp").mkdir(exist_ok=True)
    blocked = _stop(host, repo, "s1", retry=True)
    assert "consolidation failed" not in blocked.stdout
    accepted = _accept(host, repo, "s1")
    assert "consolidation failed" not in accepted.stdout
    assert _out(accepted).get("decision") is None


@pytest.mark.parametrize("host", HOSTS)
def test_fifo_at_the_seen_cache_cannot_stall_stop(tmp_path, host):
    repo = _repo(tmp_path, records=False)
    _turn(host, repo, "s1")
    os.mkfifo(repo / ".mycelium" / "access-note-seen.tsv")
    started = time.monotonic()
    accepted = _accept(host, repo, "s1")
    assert time.monotonic() - started < 60
    assert _out(accepted).get("decision") is None
    assert "ACCESS NOTE" in _out(accepted).get("systemMessage", "")
