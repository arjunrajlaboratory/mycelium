"""Encoders that skip the interpreter print exactly what the interpreter prints.

`mycelium_emit_context`, `mycelium_emit_stop_block`, `mycelium_json_dumps_stdin`
and `mycelium_registry_cell` build their output in bash when that output is
provably the same as their Python bodies' output, and otherwise run Python.
Each case runs both paths on the same bytes and requires identical stdout and
exit status, across bash versions, locales, Python I/O settings, and the shell
options that change pattern substitution.
"""

from __future__ import annotations

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "hooks" / "mycelium-hook-lib.sh"

SAMPLES: list[bytes] = [
    b"",
    b"plain ascii text",
    b"MYCELIUM POST-ACTION PROTOCOL \xe2\x80\x94 MANDATORY\\n\\nliteral backslash-n",
    b'quotes " and \\ backslashes \\\\ and & ampersands \\& and |pipes|',
    b"tab\there\nnewline\r\ncrlf\rcr\x08backspace\x0cformfeed\x01ctl\x1f\x7fdel",
    "en – em — arrow → dots … quotes ‘’“”".encode(),
    "accents éè cjk 中 emoji \U0001f600".encode(),
    b"invalid \xff\xfe bytes",
    b"lead byte before dash \xe2\xe2\x80\x94",
    b"four-byte lead \xf0\xe2\x80\x94",
    b"truncated dash \xe2\x80",
    b"dash then stray \xe2\x80\x94\x80",
    b"\xe2\x80\x94",
    b"  leading and trailing spaces  ",
    b"a.py, b.py, c.py (+3 more)",
    b"repo-name",
    b"feature/branch_name-1.2",
    b"name\n\n\n",
    b"\n",
    b"x" * 5000 + "—".encode() * 200,
    b"[log](2026-11-14-001-repo.md) <tag> 'single'",
    # Long values: bash 3.2 encodes in pieces cut between UTF-8 characters,
    # so dashes and escapes land on every piece boundary; past the size cap
    # every bash hands the value to the interpreter.
    "\u2014".encode() * 3000,
    (b'\\"' + "\u2014".encode() + b"a\n") * 1500,
    b"y" * 511 + "\u2014".encode() * 5 + b"z" * 1000,
    (b'\\"' + "\u2014".encode() + b"a\n") * 6000,
]


def _random_samples(count: int) -> list[bytes]:
    rng = random.Random(1729)
    alphabet = [bytes([b]) for b in range(1, 128)] + [
        "—".encode(), "–".encode(), "→".encode(), "“".encode(),
        b"\xe2", b"\x80", b"\x94", b"\xff", "é".encode(),
    ]
    return [b"".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) for _ in range(count)]


ALL_SAMPLES = SAMPLES + _random_samples(25)

COMPARE = r"""
set -uo pipefail
source "$1"
dir="$2"
probe_payload='{"cwd": "x"}'
mycelium_payload_preload probe_payload get:cwd
[[ -n "$MYCELIUM__PC_ENC" ]] || { echo "no encodings reported" >&2; exit 9; }
shopt -s nocasematch
if [[ "${PATSUB:-}" == on ]]; then shopt -s patsub_replacement 2>/dev/null || true; fi
enc="$MYCELIUM__PC_ENC"
for file in "$dir"/*.bin; do
  value=$(cat "$file"; printf x); value="${value%x}"
  for kind in context stop stdin cell codex claudestop; do
    for path in fast slow; do
      if [[ "$path" == slow ]]; then MYCELIUM__PC_ENC=""; else MYCELIUM__PC_ENC="$enc"; fi
      case "$kind" in
        context) out=$(MYCELIUM_HOOK_HOST=claude mycelium_emit_context PostToolUse "$value" "$value" 2>/dev/null; printf 'rc=%s' "$?") ;;
        codex) out=$(MYCELIUM_HOOK_HOST=codex mycelium_emit_context Stop "$value" 2>/dev/null; printf 'rc=%s' "$?") ;;
        claudestop) out=$(MYCELIUM_HOOK_HOST=claude mycelium_emit_context Stop "$value" "$value" 2>/dev/null; printf 'rc=%s' "$?") ;;
        stop) out=$(mycelium_emit_stop_block "$value" 2>/dev/null; printf 'rc=%s' "$?") ;;
        stdin) out=$(mycelium_json_dumps_stdin "$value" 2>/dev/null; printf 'rc=%s' "$?") ;;
        cell)
          if [[ "$path" == slow ]]; then
            out=$(printf '%s' "$value" | python3 -c "$MYCELIUM__PY_REGISTRY_CELL" 2>/dev/null; printf 'rc=%s' "$?")
          else
            out=$(printf '%s' "$value" | mycelium_registry_cell 2>/dev/null; printf 'rc=%s' "$?")
          fi ;;
      esac
      printf '%s\t%s\t%s\t%q\n' "${file##*/}" "$kind" "$path" "$out"
    done
  done
done
"""


def _bashes() -> list[str]:
    found = []
    for candidate in ("/bin/bash", shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash"):
        if candidate and Path(candidate).exists():
            real = str(Path(candidate).resolve())
            if real not in [str(Path(f).resolve()) for f in found]:
                found.append(candidate)
    return found


ENVIRONMENTS = {
    "utf8": {"LC_ALL": "en_US.UTF-8"},
    "c-utf8": {"LC_ALL": "C.UTF-8"},
    "c": {"LC_ALL": "C"},
    "latin1": {"LC_ALL": "en_US.ISO8859-1"},
    "io-latin1": {"LC_ALL": "en_US.UTF-8", "PYTHONIOENCODING": "latin-1"},
    "io-utf16": {"LC_ALL": "en_US.UTF-8", "PYTHONIOENCODING": "utf-16"},
    "io-utf8sig": {"LC_ALL": "en_US.UTF-8", "PYTHONIOENCODING": "utf-8-sig"},
    "utf8-mode": {"LC_ALL": "C", "PYTHONUTF8": "1"},
    "patsub": {"LC_ALL": "en_US.UTF-8", "PATSUB": "on"},
}


def _write_samples(directory: Path, samples: list[bytes]) -> None:
    directory.mkdir()
    for index, sample in enumerate(samples):
        (directory / f"{index:03d}.bin").write_bytes(sample)


@pytest.mark.parametrize("bash", _bashes())
@pytest.mark.parametrize("environment", sorted(ENVIRONMENTS))
def test_fast_encoders_match_the_interpreter(tmp_path, bash, environment):
    samples = tmp_path / "samples"
    _write_samples(samples, ALL_SAMPLES)
    env = os.environ.copy()
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8"):
        env.pop(name, None)
    env.update(ENVIRONMENTS[environment])
    result = subprocess.run(
        [bash, "-c", COMPARE, "compare", str(LIB), str(samples)],
        capture_output=True,
        env=env,
        timeout=600,
    )
    assert result.returncode == 0, result.stderr
    rows: dict[tuple[bytes, bytes], dict[bytes, bytes]] = {}
    for line in result.stdout.splitlines():
        name, kind, path, out = line.split(b"\t")
        rows.setdefault((name, kind), {})[path] = out
    assert len(rows) == len(ALL_SAMPLES) * 6
    mismatches = [
        (name, kind, outs[b"fast"], outs[b"slow"])
        for (name, kind), outs in rows.items()
        if outs[b"fast"] != outs[b"slow"]
    ]
    assert mismatches == []


def test_ordinary_messages_need_no_interpreter(tmp_path):
    """Mycelium's own directives, with their em dashes, skip Python."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "launches.log"
    real = shutil.which("python3")
    shim = bin_dir / "python3"
    shim.write_text(f'#!/bin/sh\nprintf "x\\n" >> "{log}"\nexec "{real}" "$@"\n')
    shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["LC_ALL"] = "en_US.UTF-8"
    env.pop("PYTHONIOENCODING", None)
    env.pop("PYTHONUTF8", None)
    script = r"""
source "$1"
probe_payload='{"cwd": "x"}'
mycelium_payload_preload probe_payload get:cwd
: > "$2"
MYCELIUM_HOOK_HOST=claude mycelium_emit_context PostToolUse 'SESSION LOG UPDATE: ### HH:MM — title "quoted" \n done' >/dev/null
MYCELIUM_HOOK_HOST=codex mycelium_emit_context Stop '.living/ updated.' >/dev/null
MYCELIUM_HOOK_HOST=claude mycelium_emit_context Stop '.living/ updated.' 'ACCESS NOTE: 1 script(s) — x' >/dev/null
mycelium_emit_stop_block 'STOP BLOCKED — 2 files changed (a.py, b.py)' >/dev/null
mycelium_json_dumps_stdin 'main' >/dev/null
printf 'my-project\n' | mycelium_registry_cell >/dev/null
"""
    result = subprocess.run(
        ["bash", "-c", script, "probe", str(LIB), str(log)],
        capture_output=True,
        env=env,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text() == ""


def test_adversarial_long_messages_encode_quickly_on_every_bash(tmp_path):
    """Pattern substitution is quadratic on bash 3.2; a hook holding its
    locks must not spend seconds escaping a long handoff full of quotes."""
    script = r"""
source "$1"
probe_payload='{"cwd": "x"}'
mycelium_payload_preload probe_payload get:cwd
unit=$'\\"\xe2\x80\x94a\n'
value=""
while (( ${#value} < 45000 )); do value="$value$unit"; done
small="${value:0:8000}"
start=$SECONDS
for _ in 1 2 3; do
  MYCELIUM_HOOK_HOST=claude mycelium_emit_context SessionStart "$value" "$value" >/dev/null
  MYCELIUM_HOOK_HOST=claude mycelium_emit_context SessionStart "$small" >/dev/null
  mycelium_emit_stop_block "$small" >/dev/null
done
echo "elapsed=$((SECONDS - start))"
"""
    env = os.environ.copy()
    env["LC_ALL"] = "en_US.UTF-8"
    for bash in _bashes():
        result = subprocess.run(
            [bash, "-c", script, "probe", str(LIB)],
            capture_output=True, text=True, env=env, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        elapsed = int(result.stdout.strip().split("=")[1])
        assert elapsed <= 5, (bash, elapsed)
