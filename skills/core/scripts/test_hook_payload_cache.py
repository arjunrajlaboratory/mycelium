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
    # Nesting at the edge of the recursion limit: the preload process has a
    # few more frames than `python3 -c`, so it must not answer these.
    *(
        b'{"cwd": "ok", "session_id": "s1", "extra": '
        + b"[" * depth + b"0" + b"]" * depth + b"}"
        for depth in (985, 990, 992, 995)
    ),
    json.dumps(
        {"cwd": "ok", "tool_response": json.dumps([[[[{"exit_code": 4}]]]] * 300)}
    ).encode(),
]

COMPARE = r"""
set -uo pipefail
source "$1"
payload_file="$2"
shift 2
payload=$(cat "$payload_file"; printf x)
payload="${payload%x}"
mycelium_payload_preload payload "$@"
for slot in "$@"; do
  kind="${slot%%:*}"
  path="${slot#*:}"
  hit=miss
  mycelium__payload_cached "$slot" payload && hit=hit
  case "$kind" in
    get)
      cached=$(mycelium_json_get "$path" payload; printf 'rc=%s' "$?")
      direct=$(printf '%s' "$payload" | mycelium_json_get "$path"; printf 'rc=%s' "$?") ;;
    opt)
      cached=$(mycelium_json_get_optional_string "$path" payload 2>/dev/null; printf 'rc=%s' "$?")
      direct=$(printf '%s' "$payload" | mycelium_json_get_optional_string "$path" 2>/dev/null; printf 'rc=%s' "$?") ;;
    exit)
      cached=$(mycelium_bash_exit payload; printf 'rc=%s' "$?")
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


def _pythons() -> list[str]:
    found: list[str] = []
    candidates = ["/usr/bin/python3", shutil.which("python3")]
    candidates += [shutil.which(f"python3.{minor}") for minor in range(9, 16)]
    for candidate in candidates:
        if not candidate or not Path(candidate).exists():
            continue
        real = str(Path(candidate).resolve())
        if real not in found:
            found.append(real)
    return found


@pytest.mark.parametrize("python", _pythons())
def test_deep_nesting_matches_on_every_installed_python(tmp_path, python):
    """The recursion boundary moved by a level on Python 3.9 and 3.11."""
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    (shim_dir / "python3").symlink_to(python)
    env = os.environ.copy()
    env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
    payloads = {
        f"deep-{depth}": b'{"cwd": "ok", "session_id": "s1", "extra": '
        + b"[" * depth + b"0" + b"]" * depth + b"}"
        for depth in range(984, 1000)
    }
    # Nesting hidden behind JSON held in a JSON string, with the escapes for
    # the brackets themselves escaped again, so no byte count can see it.
    for depth in (985, 991, 995):
        inner = "[" * depth + '{"\\u0065xit_code":7}' + "]" * depth
        hidden = json.dumps(inner).replace("[", "\\u005b").replace("{", "\\u007b")
        payloads[f"hidden-{depth}"] = (
            json.dumps({"cwd": "ok", "tool_response": hidden})
            .replace("u005b", "\\u0075005b")
            .replace("u007b", "\\u0075007b")
            .encode()
        )
    for name, payload in payloads.items():
        payload_file = tmp_path / f"{name}.json"
        payload_file.write_bytes(payload)
        result = subprocess.run(
            ["bash", "-c", COMPARE, "compare", str(LIB), str(payload_file),
             "get:cwd", "opt:session_id", "exit:"],
            capture_output=True,
            check=True,
            env=env,
        )
        for line in result.stdout.splitlines():
            slot, _hit, cached, direct = line.split(b"\t")
            assert cached == direct, (python, name, slot, cached, direct)


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


@pytest.mark.parametrize(
    ("filler", "cached"),
    [
        ("a" * 60_000, True),
        # 30,000 characters but 90,000 UTF-8 bytes: the limit counts bytes.
        ("\u20ac" * 30_000, False),
        ("a" * 70_000, False),
    ],
)
def test_large_payloads_skip_the_cache(tmp_path, filler, cached):
    """Comparing a large payload on every read costs more than a decoder."""
    payload_file = tmp_path / "payload.json"
    payload_file.write_bytes(
        json.dumps(
            {"session_id": "s1", "cwd": "/w", "tool_response": {"output": filler}},
            ensure_ascii=False,
        ).encode()
    )
    result = subprocess.run(
        ["bash", "-c", COMPARE, "compare", str(LIB), str(payload_file),
         "get:cwd", "opt:session_id", "exit:"],
        capture_output=True,
        check=True,
        env={**os.environ, "LC_ALL": "en_US.UTF-8"},
    )
    rows = [line.split(b"\t") for line in result.stdout.splitlines()]
    assert len(rows) == 3
    for slot, hit, cached_answer, direct in rows:
        assert cached_answer == direct, slot
        assert hit == (b"hit" if cached else b"miss"), (slot, hit)


def test_helpers_take_a_variable_name_not_the_payload():
    """Passing a multi-megabyte payload as an argument is slow on bash 3.2."""
    out = _probe(
        """
big="{\\"cwd\\": \\"/w\\", \\"pad\\": \\"$(head -c 200000 /dev/zero | tr '\\0' a)\\"}"
mycelium_payload_preload big get:cwd
mycelium_json_get cwd big
mycelium_json_get cwd 'not a name' || echo rejected
"""
    )
    assert out == "/w\nrejected\n"


def test_cache_never_answers_for_a_different_payload(tmp_path):
    script = r"""
set -uo pipefail
source "$1"
p='{"cwd": "/first"}'
mycelium_payload_preload p get:cwd
p='{"cwd": "/second"}'
mycelium__payload_cached get:cwd p && echo stale-hit
mycelium_json_get cwd p
"""
    result = subprocess.run(
        ["bash", "-c", script, "probe", str(LIB)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "/second\n"


def _probe(script: str) -> str:
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\nsource \"$1\"\n" + script, "probe", str(LIB)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_a_second_preload_drops_every_earlier_answer():
    out = _probe(
        """
p='{"source":"first"}'
mycelium_payload_preload p get:source
p='{"cwd":"new","source":"second"}'
mycelium_payload_preload p get:cwd
mycelium_json_get source p
"""
    )
    assert out == "second\n"


def test_preload_clears_old_answers_whatever_ifs_is():
    out = _probe(
        """
IFS=$'\\n\\t'
p='{"source":"old"}'
mycelium_payload_preload p get:source
p='{"source":"new"}'
mycelium_payload_preload p get:cwd
mycelium_json_get source p
"""
    )
    assert out == "new\n"


def test_cache_compares_payloads_case_sensitively():
    out = _probe(
        """
shopt -s nocasematch
p='{"cwd":"lower"}'
mycelium_payload_preload p get:cwd
p='{"cwd":"LOWER"}'
mycelium_json_get cwd p
shopt -q nocasematch && echo still-set
"""
    )
    assert out == "LOWER\nstill-set\n"


def test_cached_answer_meets_a_closed_stdout_like_the_decoder():
    out = _probe(
        """
p='{"cwd":"x"}'
mycelium_payload_preload p get:cwd opt:cwd exit:
for f in "mycelium_json_get cwd" "mycelium_json_get_optional_string cwd" "mycelium_bash_exit"; do
  if $f p >&- 2>/dev/null; then c=0; else c=$?; fi
  if printf '%s' "$p" | $f >&- 2>/dev/null; then d=0; else d=$?; fi
  echo "$c $d"
done
"""
    )
    lines = out.splitlines()
    assert len(lines) == 3
    for line in lines:
        cached, direct = line.split()
        assert cached == direct, out


def test_cached_answer_meets_a_broken_pipe_like_the_decoder():
    base = (
        "set -euo pipefail\n"
        f'source "{LIB}"\n'
        "p='{\"cwd\":\"x\"}'\n"
        "mycelium_payload_preload p get:cwd opt:cwd exit:\n"
    )
    for cached, direct in (
        ('mycelium_json_get cwd p', 'printf "%s" "$p" | mycelium_json_get cwd'),
        (
            'mycelium_json_get_optional_string cwd p',
            'printf "%s" "$p" | mycelium_json_get_optional_string cwd',
        ),
    ):
        codes = []
        for call in (cached, direct):
            reader, writer = os.pipe()
            os.close(reader)
            result = subprocess.run(
                ["bash", "-c", base + call],
                stdout=writer,
                stderr=subprocess.DEVNULL,
            )
            os.close(writer)
            codes.append(result.returncode)
        assert codes[0] == codes[1], (cached, codes)


def test_sourcing_the_library_again_keeps_old_answers_clearable():
    out = _probe(
        """
p='{"source":"old"}'
mycelium_payload_preload p get:source
source "$1"
p='{"source":"new"}'
mycelium_payload_preload p get:cwd
mycelium_json_get source p
"""
    )
    assert out == "new\n"


def test_distinct_paths_never_share_a_cache_entry():
    out = _probe(
        """
p='{"a":{"b":"nested"},"a_D_b":"literal","a_Db":"other","a_U_Db":"third"}'
mycelium_payload_preload p get:a.b get:a_D_b get:a_Db get:a_U_Db
for path in a.b a_D_b a_Db a_U_Db; do mycelium_json_get "$path" p; done
"""
    )
    assert out == "nested\nliteral\nother\nthird\n"


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
