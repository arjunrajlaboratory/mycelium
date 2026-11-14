"""The hook payload cache returns exactly what each separate decoder would.

`mycelium_payload_preload` runs the JSON decoders of a hook in one Python
process. These tests compare every cached answer with the answer of the
decoder run on its own, byte for byte and exit status for exit status, and
count how many Python processes a hook starts.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"
LIB = HOOKS_DIR / "mycelium-hook-lib.sh"

SLOTS = [
    "get:cwd",
    "get:source",
    "get:tool_input.command",
    "get:agent_id",
    "get:agent_type",
    "opt:session_id",
    "opt:cwd",
    "opt:tool_input.command",
    "exit:",
]

PAYLOADS: list[bytes] = [
    json.dumps(
        {
            "session_id": "abc-1",
            "cwd": "/tmp/a b/ü",
            "source": "startup",
            "tool_input": {"command": "python a.py\n\n"},
            "agent_id": True,
            "agent_type": 1.5,
            "tool_response": {"exit_code": 3},
        }
    ).encode(),
    json.dumps({"session_id": None, "cwd": 7, "tool_input": []}).encode(),
    json.dumps({"session_id": 12, "cwd": {"x": 1}, "agent_id": None}).encode(),
    json.dumps({"session_id": "a\u0000b", "cwd": "x\u0000y"}).encode(),
    json.dumps({"session_id": "\ud800", "cwd": "\udfff"}).encode(),
    json.dumps({"cwd": "trailing\n\n\n", "session_id": "\r\n"}).encode(),
    json.dumps({"tool_response": "Process exited with code 2"}).encode(),
    json.dumps(
        {"tool_response": [{"type": "input_text", "text": json.dumps({"exitCode": 5})}]}
    ).encode(),
    json.dumps({"tool_response": {"exit_code": True, "returncode": -1}}).encode(),
    json.dumps({"tool_response": {}}).encode(),
    json.dumps(["not", "an", "object"]).encode(),
    b'"just a string"',
    b"null",
    b"",
    b"{not json",
    b'{"cwd": "a"} trailing',
    b'{"cwd": 1' + b"0" * 5000 + b"}",
    b'{"cwd": "\xff\xfe invalid utf-8"}',
    b'{"cwd": "dup", "cwd": "last"}',
    b'{"session_id": "x", "cwd": NaN, "agent_type": Infinity}',
]

COMPARE = r"""
set -uo pipefail
source "$1"
payload_file="$2"
shift 2
payload=$(cat "$payload_file"; printf x)
payload="${payload%x}"
mycelium_payload_preload "$payload" "$@"
for slot in "$@"; do
  kind="${slot%%:*}"
  path="${slot#*:}"
  hit=miss
  mycelium__payload_cached "$slot" "$payload" && hit=hit
  case "$kind" in
    get)
      cached=$(mycelium_json_get "$path" "$payload"; printf 'rc=%s' "$?")
      direct=$(printf '%s' "$payload" | mycelium_json_get "$path"; printf 'rc=%s' "$?") ;;
    opt)
      cached=$(mycelium_json_get_optional_string "$path" "$payload" 2>/dev/null; printf 'rc=%s' "$?")
      direct=$(printf '%s' "$payload" | mycelium_json_get_optional_string "$path" 2>/dev/null; printf 'rc=%s' "$?") ;;
    exit)
      cached=$(mycelium_bash_exit "$payload"; printf 'rc=%s' "$?")
      direct=$(printf '%s' "$payload" | mycelium_bash_exit; printf 'rc=%s' "$?") ;;
  esac
  printf '%s\t%s\t%q\t%q\n' "$slot" "$hit" "$cached" "$direct"
done
"""


def _bashes() -> list[str]:
    found = []
    for candidate in ("/bin/bash", shutil.which("bash")):
        if candidate and Path(candidate).exists() and candidate not in found:
            found.append(candidate)
    return found


@pytest.mark.parametrize("bash", _bashes())
@pytest.mark.parametrize("index", range(len(PAYLOADS)))
def test_cached_decoders_match_separate_decoders(tmp_path, bash, index):
    payload_file = tmp_path / "payload.json"
    payload_file.write_bytes(PAYLOADS[index])
    result = subprocess.run(
        [bash, "-c", COMPARE, "compare", str(LIB), str(payload_file), *SLOTS],
        capture_output=True,
        check=True,
    )
    rows = [line.split(b"\t") for line in result.stdout.splitlines()]
    assert [row[0].decode() for row in rows] == SLOTS
    for slot, _hit, cached, direct in rows:
        assert cached == direct, (slot.decode(), cached, direct)


def test_cache_answers_ordinary_fields_without_new_interpreters(tmp_path):
    payload_file = tmp_path / "payload.json"
    payload_file.write_bytes(PAYLOADS[0])
    result = subprocess.run(
        ["bash", "-c", COMPARE, "compare", str(LIB), str(payload_file), *SLOTS],
        capture_output=True,
        check=True,
    )
    hits = {
        line.split(b"\t")[0].decode(): line.split(b"\t")[1].decode()
        for line in result.stdout.splitlines()
    }
    assert set(hits.values()) == {"hit"}, hits


def test_cache_never_answers_for_a_different_payload(tmp_path):
    script = r"""
set -uo pipefail
source "$1"
mycelium_payload_preload '{"cwd": "/first"}' get:cwd
mycelium__payload_cached get:cwd '{"cwd": "/second"}' && echo stale-hit
mycelium_json_get cwd '{"cwd": "/second"}'
"""
    result = subprocess.run(
        ["bash", "-c", script, "probe", str(LIB)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "/second\n"


def _python_shim(tmp_path: Path) -> tuple[dict[str, str], Path]:
    bin_dir = tmp_path / "shim-bin"
    bin_dir.mkdir()
    log = tmp_path / "python-launches.log"
    real = shutil.which("python3")
    assert real
    shim = bin_dir / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        f'printf "launch\\n" >> "{log}"\n'
        f'exec "{real}" "$@"\n'
    )
    shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["MYCELIUM_HOOK_HOST"] = "claude"
    return env, log


def _launches(log: Path) -> int:
    return len(log.read_text().splitlines()) if log.exists() else 0


@pytest.mark.parametrize(
    ("hook", "limit"),
    [
        # Before the payload cache these started 15 and 20 interpreters.
        ("mycelium-activity-tracker.sh", 10),
        ("mycelium-post-action.sh", 13),
    ],
)
def test_post_tool_hooks_decode_the_payload_once(tmp_path, hook, limit):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".living").mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    env, log = _python_shim(tmp_path)

    start = {
        "session_id": "s1",
        "cwd": str(repo),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    subprocess.run(
        [str(HOOKS_DIR / "mycelium-health.sh")],
        cwd=repo,
        input=json.dumps(start),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert (repo / ".mycelium" / "run" / "claude" / "s1").is_dir()
    log.unlink(missing_ok=True)

    event = {
        "session_id": "s1",
        "cwd": str(repo),
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "python a.py"},
        "tool_response": {"exit_code": 0},
    }
    subprocess.run(
        [str(HOOKS_DIR / hook)],
        cwd=repo,
        input=json.dumps(event),
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )
    assert 0 < _launches(log) <= limit
