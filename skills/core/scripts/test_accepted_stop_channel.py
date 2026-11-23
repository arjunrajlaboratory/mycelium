"""An accepted Stop never hands the agent another turn.

On Claude Code, `hookSpecificOutput.additionalContext` on a Stop hook makes
the conversation continue so the model can act on it (the hooks reference,
"Stop decision control"), with the same loop guard as `decision: "block"`.
Mycelium's accepted-Stop messages are information for the user, so on every
host they go to `systemMessage`, which is shown and does not continue the
turn. Blocks keep `decision: "block"` and their reason.

Each test drives the real hooks through one accept path:

- `.living/` updated after work (the ".living/ updated" confirmation);
- an accepted Stop with no work that still reports a lineage gap;
- a Stop with no transaction that reports skips parked since the last one.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
HOOKS_DIR = HERE.parent / "hooks"
HOSTS = ["claude", "codex"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
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


def _edit(host: str, repo: Path, sid: str) -> None:
    (repo / "model.py").write_text("x = 1\n")
    _run(host, "mycelium-activity-tracker.sh", repo,
         {"cwd": str(repo), "session_id": sid, "tool_name": "Edit",
          "hook_event_name": "PostToolUse",
          "tool_input": {"file_path": str(repo / "model.py")},
          "tool_response": {"exit_code": 0}})


def _park_skip(repo: Path, host: str, sid: str) -> None:
    shared = repo / ".mycelium" / "lineage-skips-unowned.ndjson"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text(json.dumps(
        {"ts": "T", "kind": "skip", "skip_id": "k1", "skip_reason": "detector-failed",
         "host": host, "host_session_id": sid}) + "\n")


def _assert_accepted_message(out: dict, expected: str) -> None:
    """Accepted, shown to the user, and nothing that continues the turn."""
    assert out.get("decision") is None, out
    assert "hookSpecificOutput" not in out, out
    assert "additionalContext" not in json.dumps(out), out
    assert set(out) == {"systemMessage"}, out
    assert expected in out["systemMessage"], out


@pytest.mark.parametrize("host", HOSTS)
def test_living_updated_accept_is_a_user_message(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _edit(host, repo, "s1")
    blocked = _stop(host, repo, "s1")
    assert blocked.get("decision") == "block", blocked
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("- learned something\n")

    accepted = _stop(host, repo, "s1", retry=True)

    _assert_accepted_message(accepted, ".living/ updated.")


@pytest.mark.parametrize("host", HOSTS)
def test_no_work_accept_with_a_lineage_gap_is_a_user_message(tmp_path, host):
    repo = _repo(tmp_path)
    _park_skip(repo, host, "s2")
    _start(host, repo, "s2")

    accepted = _stop(host, repo, "s2")

    _assert_accepted_message(accepted, "LINEAGE GAP")


@pytest.mark.parametrize("host", HOSTS)
def test_gap_reported_without_a_transaction_is_a_user_message(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s3")
    assert _stop(host, repo, "s3") == {}
    _park_skip(repo, host, "s3")

    reported = _stop(host, repo, "s3")

    _assert_accepted_message(reported, "LINEAGE GAP")
    # Reported once: the next Stop has nothing to say.
    assert _stop(host, repo, "s3") == {}


@pytest.mark.parametrize("host", HOSTS)
def test_blocks_are_unchanged(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s4")
    _edit(host, repo, "s4")

    blocked = _stop(host, repo, "s4")

    assert blocked.get("decision") == "block", blocked
    assert blocked["reason"].startswith("STOP BLOCKED"), blocked


# A gap is also something the agent must know: a command whose provenance is
# unknown. The accepted Stop cannot tell it without continuing the turn, so
# the warning is kept and given to the agent by the next SessionStart, once.

def _start_context(host: str, repo: Path, sid: str, source: str) -> str:
    result = _run(host, "mycelium-health.sh", repo,
                  {"cwd": str(repo), "source": source, "session_id": sid,
                   "hook_event_name": "SessionStart"})
    out = json.loads(result.stdout) if result.stdout.strip() else {}
    return out.get("hookSpecificOutput", {}).get("additionalContext", "")


@pytest.mark.parametrize("source", ["resume", "startup"])
@pytest.mark.parametrize("host", HOSTS)
def test_gap_without_a_transaction_reaches_the_next_session_start_once(tmp_path, host, source):
    repo = _repo(tmp_path)
    _start(host, repo, "s5")
    assert _stop(host, repo, "s5") == {}
    _park_skip(repo, host, "s5")
    _assert_accepted_message(_stop(host, repo, "s5"), "detector-failed x1")

    first = _start_context(host, repo, "s5", source)
    second = _start_context(host, repo, "s5", source)

    assert first.count("detector-failed x1") == 1, first
    assert "detector-failed" not in second, second


@pytest.mark.parametrize("host", HOSTS)
def test_gap_at_an_accepting_transaction_reaches_the_next_session_start_once(tmp_path, host):
    repo = _repo(tmp_path)
    _park_skip(repo, host, "s6")
    _start(host, repo, "s6")
    _assert_accepted_message(_stop(host, repo, "s6"), "detector-failed x1")

    first = _start_context(host, repo, "s6", "resume")
    second = _start_context(host, repo, "s6", "resume")

    assert first.count("detector-failed x1") == 1, first
    assert "detector-failed" not in second, second


@pytest.mark.parametrize("host", HOSTS)
def test_a_blocked_stop_queues_nothing(tmp_path, host):
    repo = _repo(tmp_path)
    _park_skip(repo, host, "s7")
    _start(host, repo, "s7")
    _edit(host, repo, "s7")
    assert _stop(host, repo, "s7").get("decision") == "block"
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("- learned\n")
    _stop(host, repo, "s7", retry=True)

    context = _start_context(host, repo, "s7", "resume")

    # Reported once, by the accepting Stop, not again for the block before it.
    assert context.count("detector-failed x1") == 1, context


def _notes(state: Path, *commands: str) -> str:
    script = f'source "{HOOKS_DIR / "mycelium-hook-lib.sh"}"\n' + "\n".join(commands)
    env = dict(os.environ, MYCELIUM_SHARED_STATE_DIR=str(state))
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                            env=env, check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_notes_are_taken_once_and_one_line_each(tmp_path):
    out = _notes(tmp_path, "mycelium_queue_agent_note $'first\\nline'",
                 "mycelium_queue_agent_note second", "mycelium_queue_agent_note ''",
                 "mycelium_take_agent_notes", "echo ---", "mycelium_take_agent_notes")
    assert out == "first line\nsecond\n---\n"
    assert list(tmp_path.iterdir()) == []


def test_a_linked_notes_file_is_never_followed(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("outside\n")
    notes = state / "pending-agent-notes.txt"
    notes.symlink_to(target)

    out = _notes(state, "mycelium_queue_agent_note gap", "mycelium_take_agent_notes")

    assert out == ""
    assert target.read_text() == "outside\n"
    assert list(state.iterdir()) == []
