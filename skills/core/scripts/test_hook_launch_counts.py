"""Pin how many Python interpreters each lifecycle hook starts.

Every hook used to start `python3` ten to twenty times, about 25 ms each, for
payload decoding, state and ownership checks, lock attempts, and output
encoding. A python3 shim on PATH logs each start through one ordinary
session: SessionStart, an edit, an analysis command, the data and read
trackers, a command that is not analysis, a blocked Stop, then an accepted
Stop. The pins are exact so that a new interpreter in a hook is a deliberate
change to this file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
STATE_MODULE = "mycelium_hook_state.py"

# hook label -> (total interpreters, state-runner interpreters)
EXPECTED = {
    # payload preload, state runner, then generate_index, the session
    # baseline snapshot, and the fence-aware entry counts.
    "SessionStart": (5, 1),
    # payload preload, state runner, edited-path resolution, living snapshot.
    "activity tracker (Edit)": (4, 1),
    # payload preload, analysis detector, state runner, living-changed check.
    "post-action (analysis)": (4, 1),
    # payload preload, state runner, lineage event extractor.
    "data tracker": (3, 1),
    # payload preload, state runner.
    "read tracker": (2, 1),
    # payload preload, analysis detector; not analysis, so no state at all.
    "post-action (not analysis)": (2, 0),
    # Stop preload and state runner, the lineage phase's own preload and state
    # runner, its extractor and status writer, session changes, and
    # living-changed.
    "Stop (blocked)": (8, 2),
    # The accepted Stop repeats the lineage phase and the change checks, then
    # decodes the branch, encodes the summary cell (it holds a space), and
    # runs the registry upsert, both finalizers, and a last living-changed.
    # A turn with work then snapshots the baseline the next turn of the same
    # host session starts from (hosts run Stop after every turn).
    "Stop (accepted)": (15, 2),
}


def _shim(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    log = tmp_path / "python-launches.log"
    real = shutil.which("python3")
    assert real
    shim = bin_dir / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        f'{{ printf "launch"; for arg in "$@"; do case "$arg" in '
        f'*{STATE_MODULE}) printf " state";; esac; done; printf "\\n"; }} >> "{log}"\n'
        f'exec "{real}" "$@"\n'
    )
    shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    for name in ("MYCELIUM_STATE_DIR", "MYCELIUM_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR"):
        env.pop(name, None)
    return env, log


def _run(hook: str, payload: dict, env: dict[str, str], repo: Path, log: Path):
    log.unlink(missing_ok=True)
    result = subprocess.run(
        [str(HOOKS_DIR / hook)],
        cwd=repo,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        timeout=120,
    )
    lines = log.read_text().splitlines() if log.exists() else []
    return result, (len(lines), sum(1 for line in lines if line.endswith(" state")))


def test_each_hook_starts_the_pinned_number_of_interpreters(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # Backdated, so Stop's commit-subject summary never sees it and the
    # registry summary is always the changed-file list.
    dated = {**os.environ, "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z",
             "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"}
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c",
         "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
        env=dated,
    )
    (repo / ".living").mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    (repo / "a.py").write_text("import pandas as pd\npd.read_csv('data/x.csv')\n")
    env, log = _shim(tmp_path)
    base = {"session_id": "s1", "cwd": str(repo)}
    bash = {"tool_name": "Bash", "tool_response": {"exit_code": 0}}
    steps = [
        ("SessionStart", "mycelium-health.sh",
         {**base, "hook_event_name": "SessionStart", "source": "startup"}),
        ("activity tracker (Edit)", "mycelium-activity-tracker.sh",
         {**base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
          "tool_input": {"file_path": str(repo / "a.py")},
          "tool_response": {"success": True}}),
        ("post-action (analysis)", "mycelium-post-action.sh",
         {**base, **bash, "hook_event_name": "PostToolUse",
          "tool_input": {"command": "python a.py"}}),
        ("data tracker", "mycelium-data-tracker.sh",
         {**base, **bash, "hook_event_name": "PostToolUse",
          "tool_input": {"command": "python a.py"}}),
        ("read tracker", "mycelium-read-tracker.sh",
         {**base, "hook_event_name": "PostToolUse", "tool_name": "Read",
          "tool_input": {"file_path": str(repo / ".living" / "learnings.md")}}),
        ("post-action (not analysis)", "mycelium-post-action.sh",
         {**base, **bash, "hook_event_name": "PostToolUse",
          "tool_input": {"command": "ls"}}),
        ("Stop (blocked)", "mycelium-stop-check.sh",
         {**base, "hook_event_name": "Stop"}),
    ]
    observed: dict[str, tuple[int, int]] = {}
    outputs: dict[str, str] = {}
    for label, hook, payload in steps:
        result, counts = _run(hook, payload, env, repo, log)
        assert result.returncode == 0, (label, result.stderr)
        observed[label] = counts
        outputs[label] = result.stdout

    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-14] Read paths as data\nbody\n")
    result, counts = _run(
        "mycelium-stop-check.sh", {**base, "hook_event_name": "Stop"}, env, repo, log
    )
    assert result.returncode == 0, result.stderr
    observed["Stop (accepted)"] = counts
    outputs["Stop (accepted)"] = result.stdout

    # The hooks still did their jobs.
    assert json.loads(outputs["SessionStart"])["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "SESSION LOG UPDATE" in json.loads(outputs["post-action (analysis)"])[
        "hookSpecificOutput"]["additionalContext"]
    assert json.loads(outputs["Stop (blocked)"])["decision"] == "block"
    assert "decision" not in json.loads(outputs["Stop (accepted)"])
    assert outputs["read tracker"] == outputs["data tracker"] == ""
    assert not (repo / ".mycelium" / "run" / "claude" / "s1" / "active-session-log.tmp").exists()

    assert observed == EXPECTED


# The same pin for a session where observation is active (payloads carry
# `tool_use_id`, as Claude Code and Codex send them).
EXPECTED_OBSERVED: dict[str, tuple[int, int]] = {
    "SessionStart": (5, 1),
    # payload preload, state runner (shared-state check), the observer.
    "observer Pre (Bash)": (3, 1),
    "observer Pre (Edit)": (3, 1),
    "observer Post (Edit)": (3, 1),
    # The unobserved tracker's three, plus the shared-state check and the
    # observer's Post (walk, diff, hashing) before session gating.
    "data tracker (observed)": (5, 2),
    # A Stop with lineage to consolidate, plus the shared-state check and
    # the observer's turn-end (Claude only, and only once `io/` exists).
    "Stop (observed)": (13, 3),
}


def test_observation_hooks_start_the_pinned_number_of_interpreters(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    (repo / "a.py").write_text("import pandas as pd\npd.read_csv('data/x.csv')\n")
    env, log = _shim(tmp_path)
    base = {"session_id": "s1", "cwd": str(repo)}
    steps = [
        ("SessionStart", "mycelium-health.sh",
         {**base, "hook_event_name": "SessionStart", "source": "startup"}),
        ("observer Pre (Bash)", "mycelium-io-observer.sh",
         {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash",
          "tool_use_id": "t1", "tool_input": {"command": "python a.py"}}),
        ("data tracker (observed)", "mycelium-data-tracker.sh",
         {**base, "hook_event_name": "PostToolUse", "tool_name": "Bash",
          "tool_use_id": "t1", "tool_input": {"command": "python a.py"},
          "tool_response": {"exit_code": 0}}),
        ("observer Pre (Edit)", "mycelium-io-observer.sh",
         {**base, "hook_event_name": "PreToolUse", "tool_name": "Edit",
          "tool_use_id": "t2", "tool_input": {"file_path": str(repo / "a.py")}}),
        ("observer Post (Edit)", "mycelium-io-observer.sh",
         {**base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
          "tool_use_id": "t2", "tool_input": {"file_path": str(repo / "a.py")}}),
        ("Stop (observed)", "mycelium-stop-check.sh",
         {**base, "hook_event_name": "Stop"}),
    ]
    observed: dict[str, tuple[int, int]] = {}
    for label, hook, payload in steps:
        result, counts = _run(hook, payload, env, repo, log)
        assert result.returncode == 0, (label, result.stderr)
        observed[label] = counts
    assert observed == EXPECTED_OBSERVED, observed
