"""Stop survives long changed-file and activity lists, whatever their names.

mycelium-stop-check.sh runs under `set -euo pipefail`. It built its short
file lists (the block reason, the registry summary, the fallback handoff and
the session-end triage) with pipelines in which one stage could fail:

- `head -N` exits early. Once the list holds more path text than a pipe
  buffer (64 KiB on macOS), the producer fails with EPIPE or dies of SIGPIPE.
- `xargs -I {} basename {}` fails on a line of 255 bytes or more, or on a
  line with a quote character (BSD xargs).
- `basename` fails on a name that starts with "-"; when that is the last name
  read, the loop fails too, and otherwise the name is lost.

Any of these made Stop exit 1 with no output. The host logs a non-blocking
hook error, so the block that was due is lost, or finalization stops halfway.
Nothing resets the work cycle, so every later Stop fails the same way. A
session can reach such a list without doing anything unusual: Stop charges
every path that changed in the repository since the work cycle began, so a
bulk copy or a job that writes a few thousand outputs is enough.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
SID = "long-list-session"
# More than 128 KiB of path text. On macOS a pipe holds at most 64 KiB; from
# about 128 KiB an early-exiting `head` failed the hook on every run.
N_FILES = 1500
LONG_DIR = "outputs/" + "d" * 100
# Sorts first, so it is always among the names Stop shows.
DASH_NAME = "-dash.py"


def _git(*args: str) -> None:
    # Backdate commits: Stop prefers commit subjects since the session start
    # (to the second) over the file list these tests check.
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
           "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    subprocess.run(["git", *args], check=True, capture_output=True, env=env)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", "-b", "main", str(root))
    _git("-C", str(root), "config", "user.email", "test@test.com")
    _git("-C", str(root), "config", "user.name", "Test")
    living = root / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (root / "analysis.py").write_text("x = 1\n")
    (root / ".gitignore").write_text(".mycelium/\n.claude/\n")
    _git("-C", str(root), "add", "-A")
    _git("-C", str(root), "commit", "-q", "-m", "init")
    return root


@pytest.fixture(params=["sigpipe-default", "sigpipe-ignored"])
def restore_signals(request) -> bool:
    # Hosts differ in whether hooks inherit SIGPIPE ignored. Either way the
    # hook must not fail: ignored gives EPIPE, default gives SIGPIPE.
    return request.param == "sigpipe-default"


def _run(name: str, root: Path, payload: dict, restore_signals: bool,
         host: str = "claude"):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    return subprocess.run(
        [str(HOOKS_DIR / name)],
        cwd=root,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
        restore_signals=restore_signals,
    )


def _start(root: Path, restore_signals: bool, host: str = "claude") -> None:
    result = _run("mycelium-health.sh", root,
                  {"cwd": str(root), "session_id": SID, "source": "startup",
                   "hook_event_name": "SessionStart"}, restore_signals, host)
    assert result.returncode == 0, result.stderr


def _edit(root: Path, path: Path, text: str, restore_signals: bool,
          host: str = "claude") -> None:
    path.write_text(text)
    result = _run("mycelium-activity-tracker.sh", root,
                  {"cwd": str(root), "session_id": SID, "tool_name": "Edit",
                   "hook_event_name": "PostToolUse",
                   "tool_input": {"file_path": str(path)},
                   "tool_response": {"exit_code": 0}}, restore_signals, host)
    assert result.returncode == 0, result.stderr


def _stop(root: Path, active: bool, restore_signals: bool,
          host: str = "claude"):
    result = _run("mycelium-stop-check.sh", root,
                  {"cwd": str(root), "session_id": SID,
                   "hook_event_name": "Stop", "stop_hook_active": active},
                  restore_signals, host)
    # The failure this guards against: exit 1 (or 141, or 2 from sort) with
    # empty stdout, and e.g. "printf: write error: Broken pipe" on stderr.
    assert result.returncode == 0, result.stderr
    assert "Broken pipe" not in result.stderr, result.stderr
    return result


def _blocked(result: subprocess.CompletedProcess) -> bool:
    return bool(result.stdout.strip()) and json.loads(result.stdout).get(
        "decision"
    ) == "block"


def _bulk_outputs(root: Path) -> list[Path]:
    """Write N_FILES untracked outputs from the shell, as a job would."""
    out = root / LONG_DIR
    out.mkdir(parents=True)
    paths = [out / f"result_{i:05d}.json" for i in range(N_FILES)]
    for path in paths:
        path.write_text("{}\n")
    assert sum(len(str(p.relative_to(root))) + 1 for p in paths) > 128 * 1024
    return paths


def _run_dir(root: Path, host: str = "claude") -> Path:
    return root / ".mycelium" / "run" / host / SID


def _finalized(root: Path, host: str = "claude") -> str:
    logs = [p for p in (root / ".living" / "log").glob("*.md")
            if p.name != "LOG_REGISTRY.md"]
    assert len(logs) == 1, logs
    assert re.search(r"^ended: \d", logs[0].read_text(), re.M), logs[0].read_text()
    assert not (_run_dir(root, host) / "active-session-log.tmp").exists()
    return (root / ".living" / "log" / "LOG_REGISTRY.md").read_text()


def test_a_long_change_list_still_blocks_then_finalizes(tmp_path, restore_signals):
    root = _repo(tmp_path)
    _start(root, restore_signals)
    _edit(root, root / "analysis.py", "x = 2\n", restore_signals)
    (root / DASH_NAME).write_text("y = 1\n")  # a shell write, no hook
    _bulk_outputs(root)

    stop = _stop(root, False, restore_signals)
    assert _blocked(stop), stop.stdout
    reason = json.loads(stop.stdout)["reason"]
    assert f"{N_FILES + 2} files changed" in reason
    named = re.search(r"files changed \((.*?)\) but", reason)
    assert named, reason
    names = named.group(1).split(", ")
    assert len(names) == 15 and DASH_NAME in names

    # A retry before .living/ changes blocks again instead of failing open.
    stop = _stop(root, True, restore_signals)
    assert _blocked(stop), stop.stdout

    with (root / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n- A job wrote many outputs; Stop must still finalize.\n")
    stop = _stop(root, True, restore_signals)
    assert not _blocked(stop), stop.stdout

    registry = _finalized(root)
    # The summary names the first three files and counts the rest; the count
    # now includes .living/learnings.md as well.
    row = re.search(r"\| (\d+) \| [^|]*\(\+(\d+) more\)", registry)
    assert row, registry
    assert int(row.group(1)) >= N_FILES + 2
    assert int(row.group(2)) == int(row.group(1)) - 3
    published = [p for p in root.rglob("last-session.md")
                 if "run" not in p.relative_to(root).parts]
    assert published, "the accepted handoff was not published"
    handoff = published[0].read_text()
    assert handoff.count("- Modified `") == 10
    assert f"- Modified `{DASH_NAME}`" in handoff


def test_odd_names_and_a_long_activity_list_do_not_fail_triage(
    tmp_path, restore_signals
):
    """The session-end triage names the first 15 sorted activity paths."""
    root = _repo(tmp_path)
    _start(root, restore_signals)
    deep = root / ("a" * 100) / ("b" * 100) / ("c" * 60)
    deep.mkdir(parents=True)
    odd = [root / DASH_NAME, root / "Bob's notes.py", deep / "result.py"]
    assert len(str(odd[2].relative_to(root))) >= 255
    for path in [root / "analysis.py", *odd]:
        _edit(root, path, "x = 2\n", restore_signals)
    activity = _run_dir(root) / "mycelium-session-activity.tmp"
    recorded = activity.read_text().splitlines()
    assert len(recorded) == 4, recorded
    # Recording 1500 edits through the tracker would take minutes, so append
    # lines in the format the tracker just used for these four.
    absolute = recorded[0].startswith("/")
    paths = _bulk_outputs(root)
    with activity.open("a") as handle:
        handle.writelines(
            f"{p if absolute else p.relative_to(root)}\n" for p in paths
        )
    assert activity.stat().st_size > 128 * 1024

    with (root / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n- Recorded before Stop.\n")
    stop = _stop(root, False, restore_signals)
    assert not _blocked(stop), stop.stdout
    _finalized(root)


def test_a_long_change_list_blocks_on_codex(tmp_path):
    root = _repo(tmp_path)
    _start(root, True, "codex")
    _edit(root, root / "analysis.py", "x = 2\n", True, "codex")
    _bulk_outputs(root)
    for active in (False, True):
        stop = _stop(root, active, True, "codex")
        assert _blocked(stop), stop.stdout
        assert f"{N_FILES + 1} files changed" in json.loads(stop.stdout)["reason"]


def test_a_lone_name_starting_with_a_dash_blocks_then_finalizes(tmp_path):
    """`basename -only.py` fails, and as the last name read it failed the
    loop: Stop exited 1 with no block, and the triage failed on every retry."""
    root = _repo(tmp_path)
    _start(root, True)
    _edit(root, root / "-only.py", "x = 1\n", True)
    stop = _stop(root, False, True)
    assert _blocked(stop), stop.stdout
    assert "1 files changed (-only.py)" in json.loads(stop.stdout)["reason"]
    with (root / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n- A name can start with a dash.\n")
    stop = _stop(root, True, True)
    assert not _blocked(stop), stop.stdout
    _finalized(root)
