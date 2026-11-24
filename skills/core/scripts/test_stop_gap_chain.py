"""The live chain ends after one accepted Stop.

Live run (claude -p, 2026-11-24): the agent ran Mycelium's helpers through
`P=$(cat .mycelium/plugin-root); S=$P/skills/core/scripts; python3 $S/x.py
--help`. The lineage detector logged them as unreadable scripts, the accepted
Stop handed that LINEAGE GAP to the agent through `additionalContext`, the
agent took one more turn to inspect it, and that turn's Stop published the
generic fallback over the handoff accepted minutes earlier.

Tested here through the real hooks: the helper chain records no skip, and an
accepted Stop with a gap emits no `additionalContext`, records the gap in
the session log and published handoff, and opens no further transaction.
Publication itself is unchanged: a later transaction with no handoff of its
own still publishes the generic fallback, as on main.
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


def _authored_text() -> str:
    return (f"## What was worked on\n{AUTHORED}\n\n## Key decisions made\n- None.\n\n"
            "## Blockers & surprises\n- None.\n\n## Current state\n- Clean.\n\n"
            "## Next steps\n- Add a permutation test.\n")


def _write_handoff(repo: Path, host: str, sid: str) -> None:
    path = repo / ".mycelium" / "run" / host / sid / "last-session.md"
    path.write_text(_authored_text())


def _as_main_publishes(authored: str, published: str) -> str:
    """What main's finalizer publishes for ``authored`` at the acceptance
    time ``published`` records (finalize_handoff.py is main's, unchanged)."""
    import sys
    sys.path.insert(0, str(HERE))
    from finalize_handoff import finalize

    accepted = published.split("accepted by Stop at ", 1)[1].split(".\n", 1)[0]
    return finalize(authored, accepted)


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


def _logs(repo: Path) -> list[str]:
    return sorted(p.name for p in (repo / ".living" / "log").glob("*.md")
                  if p.name != "LOG_REGISTRY.md")


def _skips(repo: Path) -> list[dict]:
    skipped = []
    for manifest in (repo / ".living" / "log" / "data-lineage").glob("*.json"):
        skipped += json.loads(manifest.read_text()).get("skipped", [])
    return skipped


@pytest.mark.parametrize("host", HOSTS)
def test_the_live_chain_ends_with_one_accepted_stop(tmp_path, host):
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
    # ...and is in the finalized session log.
    (log,) = _logs(repo)
    assert "- Lineage gaps: " in (repo / ".living" / "log" / log).read_text()
    # Publication is main's, byte for byte: the authored handoff, finalized.
    assert _published(repo) == _as_main_publishes(_authored_text(), _published(repo))
    # 3. Nothing is left to start another transaction.
    assert _stop(host, repo, "live") == {}
    assert len(_logs(repo)) == 1


@pytest.mark.parametrize("host", HOSTS)
def test_a_later_transaction_without_a_handoff_publishes_the_fallback_as_before(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "s1")
    _accepted_turn(host, repo, "s1", "model.py", handoff=True)
    _accepted_turn(host, repo, "s1", "notes.py", handoff=False)

    published = _published(repo)
    assert "SESSION RESUME" in published and AUTHORED not in published
