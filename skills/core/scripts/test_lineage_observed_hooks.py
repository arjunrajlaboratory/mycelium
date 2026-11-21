"""Observed file changes through the real hooks, on both hosts.

PreToolUse runs mycelium-io-observer.sh; the Bash Post runs
mycelium-data-tracker.sh, which observes before any session gating and
links its lineage record to the observation; Stop joins the observations
into the manifest. The command's own file effects are simulated between
the Pre and Post hooks, as the host would run it there.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import io_observer as io  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOSTS = ["claude", "codex"]
FIXTURE = json.loads(
    (Path(__file__).parent / "testdata" / "claude-2.1.325-tool-hooks.json").read_text()
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (repo / "data").mkdir()
    (repo / "data" / "x.csv").write_text("a\n1\n")
    (repo / "analysis.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv('data/x.csv')\n"
        "df.to_csv('out.csv')\n"
    )
    old = 1_700_000_000
    for path in (repo / "data" / "x.csv", repo / "analysis.py"):
        os.utime(path, (old, old))
    return repo


def _run(host: str, name: str, repo: Path, payload: dict):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    return subprocess.run(
        [str(HOOKS_DIR / name)], cwd=repo, input=json.dumps(payload),
        text=True, capture_output=True, env=env, check=False,
    )


def _start(host, repo, sid):
    result = _run(host, "mycelium-health.sh", repo,
                  {"cwd": str(repo), "source": "startup", "session_id": sid,
                   "hook_event_name": "SessionStart"})
    assert result.returncode == 0, result.stderr


def _stop(host, repo, sid):
    result = _run(host, "mycelium-stop-check.sh", repo,
                  {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                   "stop_hook_active": False})
    assert result.returncode == 0, result.stderr
    return result


def _pre(host, repo, sid, call, command, tool="Bash", **tool_input):
    payload = {"cwd": str(repo), "session_id": sid, "hook_event_name": "PreToolUse",
               "tool_name": tool, "tool_use_id": call,
               "tool_input": {"command": command, **tool_input}}
    result = _run(host, "mycelium-io-observer.sh", repo, payload)
    assert result.returncode == 0 and result.stdout == "", (result.stdout, result.stderr)


def _post(host, repo, sid, call, command, *, failure: str | None = None):
    if failure is not None:
        payload = {"cwd": str(repo), "session_id": sid,
                   "hook_event_name": "PostToolUseFailure", "tool_name": "Bash",
                   "tool_use_id": call, "tool_input": {"command": command},
                   "error": failure, "is_interrupt": False}
    else:
        payload = {"cwd": str(repo), "session_id": sid, "hook_event_name": "PostToolUse",
                   "tool_name": "Bash", "tool_use_id": call,
                   "tool_input": {"command": command},
                   "tool_response": {"exit_code": 0} if host == "claude" else ""}
    result = _run(host, "mycelium-data-tracker.sh", repo, payload)
    assert result.returncode == 0, result.stderr
    return result


def _stream(repo, host, sid) -> list[dict]:
    path = repo / ".mycelium" / "run" / host / sid / "mycelium-data-events.tmp"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _store(repo, host, sid) -> list[dict]:
    return io._read_lines(io.observations_path(repo / ".mycelium", host, sid))


def _manifests(repo: Path) -> list[dict]:
    folder = repo / ".living" / "log" / "data-lineage"
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.is_dir() else []


def _unowned(repo: Path) -> list[dict]:
    path = repo / ".mycelium" / "lineage-skips-unowned.ndjson"
    return [json.loads(l) for l in path.read_text().splitlines() if l] if path.exists() else []


@pytest.mark.parametrize("host", HOSTS)
def test_analysis_event_links_its_observation_and_stop_joins_it(tmp_path, host):
    repo, sid = _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "python3 analysis.py"
    _pre(host, repo, sid, "call_1", command)
    (repo / "out.csv").write_text(",a\n0,1\n")  # what the script wrote
    _post(host, repo, sid, "call_1", command)

    (event,) = [e for e in _stream(repo, host, sid) if e.get("kind") is None]
    assert event["observation_id"] == f"{host}:call_1"
    assert event["observation_status"] == "no-known-overlap"
    assert event["observation_session"] == sid
    (inp,) = event["inputs"]
    assert inp["source"] == "inferred-static"
    assert inp["pre_run_proven"] is True and inp["sha256"], (
        inp.get("proof_reason"), inp.get("hash_status"))
    (obs,) = _store(repo, host, sid)
    assert [c["path"] for c in obs["changes"]["created"]] == ["out.csv"]

    _stop(host, repo, sid)
    (manifest,) = _manifests(repo)
    assert [o["observation_id"] for o in manifest["observations"]] == [f"{host}:call_1"]
    (action,) = manifest["actions"]
    assert action["outputs"][0]["observed_change"] == "created"
    assert manifest["summary"]["observations_missing"] == []


@pytest.mark.parametrize("host", HOSTS)
def test_unrecognised_command_that_writes_gets_an_observed_change_record(tmp_path, host):
    repo, sid = _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "./build-index --all"  # nothing the detectors recognise
    _pre(host, repo, sid, "call_2", command)
    (repo / "index.bin").write_bytes(b"\x00\x01")
    _post(host, repo, sid, "call_2", command)
    (record,) = _stream(repo, host, sid)
    assert record["kind"] == "data_command"
    assert record["signals"] == ["observed-change"]
    assert record["observation_id"] == f"{host}:call_2"


@pytest.mark.parametrize("host", HOSTS)
def test_command_that_changes_nothing_and_names_no_data_leaves_no_record(tmp_path, host):
    repo, sid = _repo(tmp_path), "owned"
    _start(host, repo, sid)
    _pre(host, repo, sid, "call_3", "git status")
    _post(host, repo, sid, "call_3", "git status")
    assert _stream(repo, host, sid) == []
    # The observation itself is still stored: "nothing changed" is evidence.
    (obs,) = _store(repo, host, sid)
    assert obs["changes"]["counts"] == {"created": 0, "modified": 0, "deleted": 0}


@pytest.mark.parametrize("host", HOSTS)
def test_observation_after_an_idle_turn_parks_without_reopening(tmp_path, host):
    repo, sid = _repo(tmp_path), "idle"
    _start(host, repo, sid)
    _stop(host, repo, sid)
    command = "./build-index --all"
    _pre(host, repo, sid, "call_4", command)
    (repo / "index.bin").write_bytes(b"\x00")
    _post(host, repo, sid, "call_4", command)
    assert not (repo / ".mycelium" / "run" / host / sid / "active-session-log.tmp").exists()
    (parked,) = _unowned(repo)
    assert parked["kind"] == "data_command"
    assert parked["signals"] == ["observed-change"]
    assert parked["observation_id"] == f"{host}:call_4"
    assert len(_store(repo, host, sid)) == 1
    # The next Stop gives it a data-commands manifest with the observation.
    stop = _stop(host, repo, sid)
    assert "LINEAGE GAP" not in stop.stdout
    (manifest,) = _manifests(repo)
    assert [o["observation_id"] for o in manifest["observations"]] == [f"{host}:call_4"]


def test_claude_failure_event_records_the_real_exit_and_partial_output(tmp_path):
    host, repo, sid = "claude", _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "python3 analysis.py"
    _pre(host, repo, sid, "call_5", command)
    (repo / "out.csv").write_text("partial")
    _post(host, repo, sid, "call_5", command,
          failure=FIXTURE["bash_failure"]["error"].replace("ls", "python3"))
    (event,) = [e for e in _stream(repo, host, sid) if e.get("kind") is None]
    assert event["bash_exit"] == 1
    (obs,) = _store(repo, host, sid)
    assert obs["outcome"] == "failure" and obs["bash_exit"] == 1
    assert [c["path"] for c in obs["changes"]["created"]] == ["out.csv"]


def test_claude_interrupt_without_exit_code_stays_unknown(tmp_path):
    host, repo, sid = "claude", _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "./build-index"
    _pre(host, repo, sid, "call_6", command)
    (repo / "index.bin").write_bytes(b"\x00")
    _post(host, repo, sid, "call_6", command, failure="Interrupted by user")
    (record,) = _stream(repo, host, sid)
    assert record["bash_exit"] is None
    (obs,) = _store(repo, host, sid)
    assert obs["outcome"] == "failure" and obs["bash_exit"] is None


@pytest.mark.parametrize("host", HOSTS)
def test_post_without_a_pre_is_unavailable_not_empty(tmp_path, host):
    repo, sid = _repo(tmp_path), "owned"
    _start(host, repo, sid)
    _post(host, repo, sid, "never_pre", "python3 analysis.py")
    (event,) = [e for e in _stream(repo, host, sid) if e.get("kind") is None]
    assert event["observation_status"] == "unavailable"
    assert event["observation_reason"] == "no-pre-snapshot"


@pytest.mark.parametrize("host", HOSTS)
def test_background_run_is_marked_background(tmp_path, host):
    repo, sid = _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "nohup python3 analysis.py > run.log 2>&1 &"
    _pre(host, repo, sid, "call_7", command)
    _post(host, repo, sid, "call_7", command)
    (obs,) = _store(repo, host, sid)
    assert obs["status"] == "background"


def test_command_the_host_moved_to_the_background_stays_open(tmp_path):
    # Claude Code backgrounds a long foreground command at its timeout: no
    # run_in_background in the input, a backgroundTaskId in the response.
    host, repo, sid = "claude", _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "python3 analysis.py"
    _pre(host, repo, sid, "long", command)
    payload = {"cwd": str(repo), "session_id": sid, "hook_event_name": "PostToolUse",
               "tool_name": "Bash", "tool_use_id": "long",
               "tool_input": {"command": command},
               "tool_response": {"stdout": "", "stderr": "", "interrupted": False,
                                 "backgroundTaskId": "b8ucc9n3i"}}
    assert _run(host, "mycelium-data-tracker.sh", repo, payload).returncode == 0
    (first,) = _store(repo, host, sid)
    assert first["status"] == "background"
    _pre(host, repo, sid, "next", "git status")
    (repo / "out.csv").write_text("still being written by the long job")
    _post(host, repo, sid, "next", "git status")
    second = _store(repo, host, sid)[-1]
    assert second["status"] == "overlap"
    assert [o["call"] for o in second["overlapping"]] == ["long"]


def test_edit_tool_beside_bash_marks_overlap(tmp_path):
    host, repo, sid = "claude", _repo(tmp_path), "owned"
    _start(host, repo, sid)
    command = "python3 analysis.py"
    _pre(host, repo, sid, "call_8", command)
    edit = {"cwd": str(repo), "session_id": sid, "tool_name": "Edit",
            "tool_use_id": "edit_1", "tool_input": {"file_path": str(repo / "notes.md")}}
    assert _run(host, "mycelium-io-observer.sh", repo,
                {**edit, "hook_event_name": "PreToolUse"}).returncode == 0
    (repo / "notes.md").write_text("n")
    assert _run(host, "mycelium-io-observer.sh", repo,
                {**edit, "hook_event_name": "PostToolUse"}).returncode == 0
    _post(host, repo, sid, "call_8", command)
    (obs,) = _store(repo, host, sid)
    assert obs["status"] == "overlap"
    assert [o["call"] for o in obs["overlapping"]] == ["edit_1"]


@pytest.mark.parametrize("host", HOSTS)
def test_repository_without_living_layer_gets_nothing(tmp_path, host):
    repo = tmp_path / "plain"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _pre(host, repo, "s", "c", "python3 a.py")
    assert not (repo / ".mycelium").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_symlinked_state_directory_receives_nothing(tmp_path, host):
    repo, sid = _repo(tmp_path), "owned"
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo / ".mycelium")
    _pre(host, repo, sid, "c", "python3 analysis.py")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("host", HOSTS)
def test_invalid_session_identity_writes_nothing(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "ok")
    io_dir = repo / ".mycelium" / "io"
    before = sorted(p.name for p in io_dir.rglob("*")) if io_dir.exists() else []
    for bad in ("..", "a/b", ""):
        _pre(host, repo, bad, "c", "python3 analysis.py")
    after = sorted(p.name for p in io_dir.rglob("*")) if io_dir.exists() else []
    assert after == before
