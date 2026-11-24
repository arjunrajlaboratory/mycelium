"""A later transaction with no handoff of its own keeps the accepted one.

Live run (claude -p, 2026-11-24): the agent ran Mycelium's helpers through
`P=$(cat .mycelium/plugin-root); S=$P/skills/core/scripts; python3 $S/x.py
--help`. The lineage detector logged them as unreadable scripts, the accepted
Stop handed that LINEAGE GAP to the agent through `additionalContext`, the
agent took one more turn to inspect it, and that turn's Stop published the
generic fallback over the handoff accepted minutes earlier.

Each link is tested here through the real hooks: the helper chain records no
skip, an accepted Stop with a gap emits no `additionalContext` and records
the gap durably, and a follow-up transaction of the same host session that
wrote no handoff keeps the accepted one with a one-line note.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

HERE = Path(__file__).resolve().parent
HOOKS_DIR = HERE.parent / "hooks"
PLUGIN = HERE.parent.parent.parent
HOSTS = ["claude", "codex"]
AUTHORED = "- Built the ratio analysis; the drug arm has 3 genes above 1.3."
HELPERS = ("P=$(cat .mycelium/plugin-root); S=$P/skills/core/scripts; "
           "python3 $S/data_contract_check.py --help; python3 $S/register_value.py --help")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (repo / ".mycelium").mkdir()
    (repo / ".mycelium" / "plugin-root").write_text(f"{PLUGIN}\n")
    (repo / "data.csv").write_text("a\n1\n2\n")
    subprocess.run(["git", "-C", str(repo), "add", "data.csv"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid", "-c",
                    "user.name=t", "commit", "-qm", "data"], check=True)
    return repo


def _run(host: str, name: str, repo: Path, payload: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    result = subprocess.run([str(HOOKS_DIR / name)], cwd=repo, input=json.dumps(payload),
                            text=True, capture_output=True, env=env, check=False, timeout=120)
    assert result.returncode == 0, result.stderr
    return result


def _start(host: str, repo: Path, sid: str) -> None:
    _run(host, "mycelium-health.sh", repo,
         {"cwd": str(repo), "source": "startup", "session_id": sid,
          "hook_event_name": "SessionStart"})


def _stop(host: str, repo: Path, sid: str, *, retry: bool = False) -> dict:
    result = _run(host, "mycelium-stop-check.sh", repo,
                  {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                   "stop_hook_active": retry})
    return json.loads(result.stdout) if result.stdout.strip() else {}


def _bash(host: str, repo: Path, sid: str, command: str) -> None:
    _run(host, "mycelium-data-tracker.sh", repo,
         {"cwd": str(repo), "session_id": sid, "hook_event_name": "PostToolUse",
          "tool_name": "Bash", "tool_use_id": f"call_{time.time_ns()}",
          "tool_input": {"command": command},
          "tool_response": {"exit_code": 0} if host == "claude" else ""})


def _edit(host: str, repo: Path, sid: str, name: str) -> None:
    (repo / name).write_text(f"x = {time.time_ns()}\n")
    _run(host, "mycelium-activity-tracker.sh", repo,
         {"cwd": str(repo), "session_id": sid, "tool_name": "Edit",
          "hook_event_name": "PostToolUse",
          "tool_input": {"file_path": str(repo / name)},
          "tool_response": {"exit_code": 0}})


def _reflect(repo: Path) -> None:
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + f"- learned {time.time_ns()}\n")


def _write_handoff(repo: Path, host: str, sid: str) -> None:
    path = repo / ".mycelium" / "run" / host / sid / "last-session.md"
    path.write_text(
        f"## What was worked on\n{AUTHORED}\n\n## Key decisions made\n- None.\n\n"
        "## Blockers & surprises\n- None.\n\n## Current state\n- Clean.\n\n"
        "## Next steps\n- Add a permutation test.\n")


def _park_skip(repo: Path, host: str, sid: str) -> None:
    shared = repo / ".mycelium" / "lineage-skips-unowned.ndjson"
    shared.write_text(json.dumps(
        {"ts": "T", "kind": "skip", "skip_id": "k1", "skip_reason": "detector-failed",
         "host": host, "host_session_id": sid}) + "\n")


def _published(repo: Path) -> str:
    return (repo / ".mycelium" / "last-session.md").read_text()


def _accepted_turn(host: str, repo: Path, sid: str, name: str, *, handoff: bool) -> dict:
    _edit(host, repo, sid, name)
    assert _stop(host, repo, sid).get("decision") == "block"
    _reflect(repo)
    if handoff:
        _write_handoff(repo, host, sid)
    out = _stop(host, repo, sid, retry=True)
    assert out.get("decision") is None, out
    return out


LOOK = "python3 -c \"print(sum(1 for _ in open('data.csv')))\""


def _follow_up_turn(host: str, repo: Path, sid: str) -> dict:
    """A genuine follow-up: a read-only look at the data, then (if Stop asks)
    a note under .living/; no project file changes, no handoff."""
    _bash(host, repo, sid, LOOK)
    out = _stop(host, repo, sid)
    if out.get("decision") == "block":
        _reflect(repo)
        out = _stop(host, repo, sid, retry=True)
    assert out.get("decision") is None, out
    return out


def _logs(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / ".living" / "log").glob("*.md")
                  if p.name != "LOG_REGISTRY.md")


def _skips(repo: Path) -> list[dict]:
    skipped = []
    for manifest in (repo / ".living" / "log" / "data-lineage").glob("*.json"):
        skipped += json.loads(manifest.read_text()).get("skipped", [])
    return skipped


@pytest.mark.parametrize("host", HOSTS)
def test_the_live_chain_ends_with_one_turn_and_the_accepted_handoff_kept(tmp_path, host):
    repo = _repo(tmp_path)
    _park_skip(repo, host, "live")  # a real gap, so the accepted Stop has one to report
    _start(host, repo, "live")
    _bash(host, repo, "live", HELPERS)
    first = _accepted_turn(host, repo, "live", "model.py", handoff=True)

    # 1. Mycelium's own helpers through the variable chain are not data commands.
    assert [s.get("skip_reason") for s in _skips(repo)] == ["detector-failed"]
    # 2. The gap is reported without continuing the conversation...
    assert set(first) == {"systemMessage"}, first
    assert "detector-failed x1" in first["systemMessage"], first
    # ...and is in the records the next session reads.
    accepted = _published(repo)
    assert AUTHORED in accepted
    assert "Lineage gaps (" in accepted and "detector-failed x1" in accepted

    # 3. A follow-up turn opens a new transaction and writes no handoff.
    second = _follow_up_turn(host, repo, "live")
    assert "hookSpecificOutput" not in second, second
    logs = _logs(repo)
    assert len(logs) == 2, logs
    kept = _published(repo)
    assert AUTHORED in kept, kept
    assert "SESSION RESUME" not in kept, kept
    follow_up = [line for line in kept.splitlines() if line.startswith("Follow-up activity")]
    assert len(follow_up) == 1 and logs[1] in follow_up[0], kept
    assert "detector-failed x1" in kept  # the first transaction's gap survives

    # A third such turn still keeps it, and both follow-ups are named.
    _follow_up_turn(host, repo, "live")
    again = _published(repo)
    logs = _logs(repo)
    assert AUTHORED in again and len(logs) == 3, logs
    follow_up = [line for line in again.splitlines() if line.startswith("Follow-up activity")]
    assert len(follow_up) == 2 and logs[1] in follow_up[0] and logs[2] in follow_up[1], again


@pytest.mark.parametrize("host", HOSTS)
def test_a_follow_up_with_its_own_handoff_replaces_the_published_one(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    path = repo / ".mycelium" / "run" / host / "s1" / "last-session.md"
    _edit(host, repo, "s1", "notes.py")
    assert _stop(host, repo, "s1").get("decision") == "block"
    _reflect(repo)
    path.write_text(_published(repo).split("\n\n", 1)[1].replace(AUTHORED, "- Second turn."))
    _stop(host, repo, "s1", retry=True)

    published = _published(repo)
    assert "- Second turn." in published and AUTHORED not in published
    assert "Follow-up activity:" not in published


@pytest.mark.parametrize("host", HOSTS)
def test_another_host_session_does_not_keep_the_published_handoff(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    _start(host, repo, "s2")
    _accepted_turn(host, repo, "s2", "notes.py", handoff=False)

    published = _published(repo)
    assert "SESSION RESUME" in published and AUTHORED not in published


@pytest.mark.parametrize("host", HOSTS)
def test_a_published_fallback_is_not_kept(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=False)
    first = _published(repo)
    assert "SESSION RESUME" in first and "source fallback;" in first
    _follow_up_turn(host, repo, "s1")

    second = _published(repo)
    assert "SESSION RESUME" in second and "Follow-up activity" not in second


@pytest.mark.parametrize("host", HOSTS)
def test_a_hand_edited_published_handoff_without_an_owner_is_not_kept(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    published = repo / ".mycelium" / "last-session.md"
    published.write_text("\n".join(line for line in published.read_text().splitlines()
                                   if not line.startswith("Handoff owner:")) + "\n")
    _follow_up_turn(host, repo, "s1")

    assert "SESSION RESUME" in _published(repo)


@pytest.mark.parametrize("host", HOSTS)
def test_a_follow_up_without_a_handoff_keeps_the_accepted_one(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    _follow_up_turn(host, repo, "s1")

    published = _published(repo)
    assert AUTHORED in published and "SESSION RESUME" not in published
    assert _logs(repo)[1] in published  # the follow-up line names its log


@pytest.mark.parametrize("host", HOSTS)
def test_new_work_in_the_same_session_publishes_the_fallback(tmp_path, host):
    # PR #163 review: a resumed session's new root task with real work must
    # not republish the old handoff with only a follow-up line.
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    _accepted_turn(host, repo, "s1", "notes.py", handoff=False)  # an uncommitted edit

    published = _published(repo)
    assert "SESSION RESUME" in published and AUTHORED not in published
    assert "notes.py" in published


@pytest.mark.parametrize("host", HOSTS)
def test_committed_work_in_the_same_session_publishes_the_fallback(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    (repo / "x.py").write_text("x = 2\n")
    commit = ("git add x.py && git -c user.email=t@example.invalid -c user.name=t "
              "commit -qm 'Add x'")
    subprocess.run(commit, shell=True, cwd=repo, check=True)
    _bash(host, repo, "s1", commit)
    _bash(host, repo, "s1", LOOK)
    out = _stop(host, repo, "s1")
    if out.get("decision") == "block":
        _reflect(repo)
        out = _stop(host, repo, "s1", retry=True)
    assert out.get("decision") is None, out

    published = _published(repo)
    assert "SESSION RESUME" in published and AUTHORED not in published
    assert "Add x" in published


@pytest.mark.parametrize("host", HOSTS)
def test_a_fresh_partial_handoff_gets_the_fallback_after_its_one_block(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    _bash(host, repo, "s1", LOOK)
    partial = repo / ".mycelium" / "run" / host / "s1" / "last-session.md"
    partial.write_text("## What was worked on\n- Looked at the data.\n")
    _reflect(repo)
    blocked = _stop(host, repo, "s1")
    assert blocked.get("decision") == "block" and "generic fallback" in blocked["reason"]
    assert _stop(host, repo, "s1", retry=True).get("decision") is None

    published = _published(repo)
    assert "SESSION RESUME" in published and AUTHORED not in published


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("age", ["3 hours", "yesterday"])
def test_a_follow_up_after_the_window_publishes_the_fallback(tmp_path, host, age):
    from datetime import datetime, timedelta

    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    published = repo / ".mycelium" / "last-session.md"
    now = datetime.now().astimezone()
    then = now - (timedelta(hours=3) if age == "3 hours" else timedelta(days=1))
    text = published.read_text()
    import re
    text = re.sub(r"(Handoff owner: .*; accepted )[^.]+(\.)",
                  lambda m: m.group(1) + then.strftime("%Y-%m-%dT%H:%M:%S%z") + m.group(2), text)
    published.write_text(text)
    _follow_up_turn(host, repo, "s1")

    assert "SESSION RESUME" in _published(repo)
