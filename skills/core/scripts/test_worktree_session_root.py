"""Lifecycle hooks follow the session into a linked git worktree.

Issue #144: the Claude desktop app runs side sessions in a linked worktree
(`.claude/worktrees/<name>/`) while it runs hook processes in the main
checkout. SessionStart, the edit tracker and the lineage hook already took the
repository root from the payload's `cwd`. Stop and the Bash post-action hook
took it from the hook process's own directory instead. That judged the session
against the main checkout: other sessions' uncommitted work there counted as
this session's, reflection written in the worktree's `.living/` was invisible,
and Stop blocked until the agent wrote into the main checkout.

Every hook now resolves the root from the payload's `cwd`. When that
repository is not opted in, Stop and the Bash post-action hook take an
enclosing opted-in repository only if it already holds the session's run
directory, and otherwise fall back to the process directory.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOSTS = ["claude", "codex"]
SID = "worktree-session"


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


def _run(host: str, name: str, process_cwd: Path, payload: dict):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    return subprocess.run(
        [str(HOOKS_DIR / name)],
        cwd=process_cwd,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _blocked(result: subprocess.CompletedProcess) -> bool:
    return bool(result.stdout.strip()) and json.loads(result.stdout).get(
        "decision"
    ) == "block"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and ".git" not in path.relative_to(root).parts
        and ".claude" not in path.relative_to(root).parts
    }


@pytest.mark.parametrize("host", HOSTS)
def test_stop_judges_the_worktree_the_session_works_in(tmp_path, host):
    main, worktree = _checkouts(tmp_path)
    base = {"cwd": str(worktree), "session_id": SID}

    start = _run(host, "mycelium-health.sh", main,
                 {**base, "source": "startup", "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr

    # Another session's uncommitted work in the main checkout, written after
    # this session started. None of it is this session's.
    (main / "report.py").write_text("y = 2\n")
    (main / ".living" / "decisions.md").write_text("# decisions.md\nother\n")
    main_before = _snapshot(main)

    (worktree / "analysis.py").write_text("x = 2\n")
    edit = _run(host, "mycelium-activity-tracker.sh", main,
                {**base, "tool_name": "Edit", "hook_event_name": "PostToolUse",
                 "tool_input": {"file_path": str(worktree / "analysis.py")},
                 "tool_response": {"exit_code": 0}})
    assert edit.returncode == 0, edit.stderr

    stop = _run(host, "mycelium-stop-check.sh", main,
                {**base, "hook_event_name": "Stop", "stop_hook_active": False})
    assert stop.returncode == 0, stop.stderr
    assert _blocked(stop), stop.stdout
    reason = json.loads(stop.stdout)["reason"]
    assert "analysis.py" in reason
    assert "report.py" not in reason

    # Reflection lands in the worktree's own .living/, and that is enough.
    with (worktree / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### Worktree learning\n- noted\n")
    stop = _run(host, "mycelium-stop-check.sh", main,
                {**base, "hook_event_name": "Stop", "stop_hook_active": True})
    assert stop.returncode == 0, stop.stderr
    assert not _blocked(stop), stop.stdout

    assert _snapshot(main) == main_before
    assert not (main / ".mycelium" / "run").exists()
    logs = list((worktree / ".living" / "log").glob("*.md"))
    assert any(path.name != "LOG_REGISTRY.md" for path in logs)


@pytest.mark.parametrize("host", HOSTS)
def test_post_action_directs_writes_into_the_worktree(tmp_path, host):
    main, worktree = _checkouts(tmp_path)
    (worktree / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    base = {"cwd": str(worktree), "session_id": SID}
    start = _run(host, "mycelium-health.sh", main,
                 {**base, "source": "startup", "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr

    result = _run(host, "mycelium-post-action.sh", main,
                  {**base, "tool_name": "Bash", "hook_event_name": "PostToolUse",
                   "tool_input": {"command": "python3 analysis.py"},
                   "tool_response": {"exit_code": 0}})

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert str(worktree / ".living" / "log") in context
    assert str(main / ".living" / "log") + "/" not in context
    assert not (main / ".mycelium" / "run").exists()


def test_stop_falls_back_when_the_session_cwd_is_not_opted_in(tmp_path):
    """A turn that ended after `cd` into a nested repository without .living/
    is still judged in the project the hook runs in, as before."""
    main, _ = _checkouts(tmp_path)
    nested = main / "vendor" / "lib"
    nested.mkdir(parents=True)
    _git("init", "-q", str(nested))
    start = _run("claude", "mycelium-health.sh", main,
                 {"cwd": str(main), "session_id": SID, "source": "startup",
                  "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    (main / "analysis.py").write_text("x = 2\n")
    _run("claude", "mycelium-activity-tracker.sh", main,
         {"cwd": str(main), "session_id": SID, "tool_name": "Edit",
          "hook_event_name": "PostToolUse",
          "tool_input": {"file_path": str(main / "analysis.py")},
          "tool_response": {"exit_code": 0}})

    for cwd in (nested, tmp_path):
        stop = _run("claude", "mycelium-stop-check.sh", main,
                    {"cwd": str(cwd), "session_id": SID,
                     "hook_event_name": "Stop", "stop_hook_active": False})
        assert stop.returncode == 0, stop.stderr
        assert _blocked(stop), (cwd, stop.stdout)
    assert not (nested / ".mycelium").exists()


def test_stop_keeps_the_worktree_after_cd_into_a_nested_repository(tmp_path):
    """A worktree session whose turn ends after `cd` into a nested repository
    without .living/ (a vendored checkout, a submodule) is still judged on the
    enclosing worktree, not on the main checkout the hook process runs in,
    which holds no transaction for it and would let Stop pass silently."""
    main, worktree = _checkouts(tmp_path)
    nested = worktree / "vendor" / "lib"
    nested.mkdir(parents=True)
    _git("init", "-q", str(nested))
    base = {"cwd": str(worktree), "session_id": SID}
    start = _run("claude", "mycelium-health.sh", main,
                 {**base, "source": "startup", "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    (worktree / "analysis.py").write_text("x = 2\n")
    _run("claude", "mycelium-activity-tracker.sh", main,
         {**base, "tool_name": "Edit", "hook_event_name": "PostToolUse",
          "tool_input": {"file_path": str(worktree / "analysis.py")},
          "tool_response": {"exit_code": 0}})

    stop = _run("claude", "mycelium-stop-check.sh", main,
                {"cwd": str(nested), "session_id": SID,
                 "hook_event_name": "Stop", "stop_hook_active": False})
    assert stop.returncode == 0, stop.stderr
    assert _blocked(stop), stop.stdout
    assert "analysis.py" in json.loads(stop.stdout)["reason"]
    assert not (main / ".mycelium" / "run").exists()
    assert not (nested / ".mycelium").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_lineage_is_published_in_the_worktree_after_cd_into_a_nested_repository(
    tmp_path, host
):
    """Stop runs the lineage finalizer in-process with the same payload; it
    must land on the root Stop chose, or Stop accepts and archives the raw
    events without publishing a manifest."""
    main, worktree = _checkouts(tmp_path)
    (worktree / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    nested = worktree / "vendor" / "lib"
    nested.mkdir(parents=True)
    _git("init", "-q", str(nested))
    base = {"cwd": str(worktree), "session_id": SID}
    start = _run(host, "mycelium-health.sh", main,
                 {**base, "source": "startup", "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    tracked = _run(host, "mycelium-data-tracker.sh", main,
                   {**base, "tool_name": "Bash", "hook_event_name": "PostToolUse",
                    "tool_input": {"command": "python3 analysis.py"},
                    "tool_response": {"exit_code": 0}})
    assert tracked.returncode == 0, tracked.stderr
    with (worktree / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### Worktree learning\n- noted\n")

    stop = _run(host, "mycelium-stop-check.sh", main,
                {"cwd": str(nested), "session_id": SID,
                 "hook_event_name": "Stop", "stop_hook_active": False})
    assert stop.returncode == 0, stop.stderr
    assert not _blocked(stop), stop.stdout
    manifests = list((worktree / ".living" / "log" / "data-lineage").glob("*.json"))
    assert manifests, "Stop accepted without publishing the lineage manifest"
    assert not (nested / ".living").exists()


def test_post_action_does_not_adopt_an_unrelated_enclosing_project(tmp_path):
    """A session working in one project that runs analysis inside a
    non-opted repository nested in a different opted-in project must not
    open state in that other project: it holds nothing of this session's."""
    main, _ = _checkouts(tmp_path)
    other = tmp_path / "other"
    (other / ".living").mkdir(parents=True)
    _git("init", "-q", str(other))
    nested = other / "vendor" / "lib"
    nested.mkdir(parents=True)
    _git("init", "-q", str(nested))
    (nested / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    start = _run("claude", "mycelium-health.sh", main,
                 {"cwd": str(main), "session_id": SID, "source": "startup",
                  "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr

    result = _run("claude", "mycelium-post-action.sh", main,
                  {"cwd": str(nested), "session_id": SID,
                   "tool_name": "Bash", "hook_event_name": "PostToolUse",
                   "tool_input": {"command": "python3 analysis.py"},
                   "tool_response": {"exit_code": 0}})
    assert result.returncode == 0, result.stderr
    assert not (other / ".mycelium").exists()
    assert not (other / ".living" / "log").exists()
    assert not (nested / ".mycelium").exists()


def test_session_start_seeds_a_missing_plugin_root_pointer(tmp_path):
    main, worktree = _checkouts(tmp_path)
    plugin_root = HOOKS_DIR.resolve().parents[2]
    start = _run("claude", "mycelium-health.sh", main,
                 {"cwd": str(worktree), "session_id": SID, "source": "startup",
                  "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    pointer = worktree / ".mycelium" / "plugin-root"
    assert pointer.read_text() == f"{plugin_root}\n"
    assert not pointer.is_symlink()
    assert (Path(pointer.read_text().strip()) / "skills/core/scripts").is_dir()


def test_session_start_keeps_an_existing_plugin_root_pointer(tmp_path):
    main, _ = _checkouts(tmp_path)
    (main / ".mycelium").mkdir()
    pointer = main / ".mycelium" / "plugin-root"
    pointer.write_text("/opt/elsewhere/mycelium\n")
    start = _run("claude", "mycelium-health.sh", main,
                 {"cwd": str(main), "session_id": SID, "source": "startup",
                  "hook_event_name": "SessionStart"})
    assert start.returncode == 0, start.stderr
    assert pointer.read_text() == "/opt/elsewhere/mycelium\n"


def test_session_start_refuses_a_linked_plugin_root_pointer(tmp_path):
    main, worktree = _checkouts(tmp_path)
    (worktree / ".mycelium").mkdir()
    target = tmp_path / "elsewhere"
    target.write_text("untouched\n")
    (worktree / ".mycelium" / "plugin-root").symlink_to(target)
    _run("claude", "mycelium-health.sh", main,
         {"cwd": str(worktree), "session_id": SID, "source": "startup",
          "hook_event_name": "SessionStart"})
    assert target.read_text() == "untouched\n"
    assert (worktree / ".mycelium" / "plugin-root").is_symlink()
