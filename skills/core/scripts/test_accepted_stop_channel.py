"""A routine accepted Stop never hands the agent another turn.

On Claude Code, `hookSpecificOutput.additionalContext` on a Stop hook makes
the conversation continue so the model can act on it (the hooks reference,
"Stop decision control"), with the same loop guard as `decision: "block"`.

- Routine accepted-Stop output (the ".living/ updated" confirmation, the
  access note) is for the user: `systemMessage` only, on every host.
- A provenance gap (lineage gap, detector failure, unrecorded events) is for
  the agent, which may need to say its results' provenance is unknown. On
  Claude it stays in `additionalContext`, as before; such Stops are rare.
  Codex has only `systemMessage`, which never continues a turn.
- Blocks keep `decision: "block"` and their reason.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

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


def _routine(out: dict, expected: str) -> None:
    """Accepted, shown to the user, and nothing that continues the turn."""
    assert out.get("decision") is None, out
    assert set(out) == {"systemMessage"}, out
    assert expected in out["systemMessage"], out


def _gap(host: str, out: dict, gap: str, user: str = "") -> None:
    """Accepted; on Claude the gap reaches the agent, and only the gap."""
    assert out.get("decision") is None, out
    if host == "codex":
        assert set(out) == {"systemMessage"}, out
        assert gap in out["systemMessage"] and user in out["systemMessage"], out
        return
    context = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop", out
    assert gap in context, out
    assert ".living/ updated" not in context, out
    if user:
        assert user in out["systemMessage"] and gap not in out["systemMessage"], out
    else:
        assert "systemMessage" not in out, out


@pytest.mark.parametrize("host", HOSTS)
def test_living_updated_accept_without_a_gap_is_only_a_user_message(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _edit(host, repo, "s1")
    assert _stop(host, repo, "s1").get("decision") == "block"
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("- learned something\n")

    _routine(_stop(host, repo, "s1", retry=True), ".living/ updated.")


@pytest.mark.parametrize("host", HOSTS)
def test_living_updated_accept_with_a_gap_gives_the_gap_to_the_agent(tmp_path, host):
    repo = _repo(tmp_path)
    _park_skip(repo, host, "s2")
    _start(host, repo, "s2")
    _edit(host, repo, "s2")
    assert _stop(host, repo, "s2").get("decision") == "block"
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("- learned something\n")

    accepted = _stop(host, repo, "s2", retry=True)

    _gap(host, accepted, "detector-failed x1", ".living/ updated.")


@pytest.mark.parametrize("host", HOSTS)
def test_no_work_accept_with_a_gap_gives_the_gap_to_the_agent(tmp_path, host):
    repo = _repo(tmp_path)
    _park_skip(repo, host, "s3")
    _start(host, repo, "s3")

    _gap(host, _stop(host, repo, "s3"), "detector-failed x1")


@pytest.mark.parametrize("host", HOSTS)
def test_gap_reported_without_a_transaction_goes_to_the_agent(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s4")
    assert _stop(host, repo, "s4") == {}
    _park_skip(repo, host, "s4")

    _gap(host, _stop(host, repo, "s4"), "detector-failed x1")
    # Reported once: the next Stop has nothing to say.
    assert _stop(host, repo, "s4") == {}


@pytest.mark.parametrize("host", HOSTS)
def test_no_work_accept_without_a_gap_is_silent(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s5")

    assert _stop(host, repo, "s5") == {}


@pytest.mark.parametrize("host", HOSTS)
def test_blocks_are_unchanged(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s6")
    _edit(host, repo, "s6")

    blocked = _stop(host, repo, "s6")

    assert blocked.get("decision") == "block", blocked
    assert blocked["reason"].startswith("STOP BLOCKED"), blocked
    assert set(blocked) == {"decision", "reason"}, blocked
