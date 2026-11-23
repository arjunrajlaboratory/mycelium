"""Regressions from the second structural review of the commit allowlist.

Each test failed on the branch before its fix:

1. A word shaped like a long option was kept by shape alone, so
   ``python3 -c '...' --proof --cinder-lattice-sable-amber`` published the
   credential in ``bash_cmd``, ``step.argv`` and ``step.script_args``. A long
   option is now kept only on evidence of its role: a fixed list of common
   options, the known options of the vocabulary command that receives it,
   or the literal ``--name`` in the source of the script or inline body the
   record ran (its ``script_source``). Short options stay by shape.
2. Seeds republished a value the committed argv masked:
   ``python3 -c '...' -- --seed 483921783046129`` published all digits in
   ``step.seeds``. The extractor's seed scan stops at a ``--`` operand
   boundary, and consolidation keeps an argv seed only when its flag and
   value both survive in the committed argv, and a source seed only under
   the short-number rule.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import extract_data_lineage_event as ev  # noqa: E402
import lineage_redact as lr  # noqa: E402

V = lr.COMMIT_VALUE
EXTRACTOR = HERE / "extract_data_lineage_event.py"
CREDENTIAL = "--cinder-lattice-sable-amber"
LONG_SEED = "483921783046129"


def _run(repo: Path, command: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _manifest(repo: Path, command: str) -> tuple[dict, str]:
    """The committed manifest for one real extractor run of ``command``."""
    events = _run(repo, command)
    assert events, command
    queue = repo / ".mycelium" / "events.ndjson"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("".join(json.dumps(e) + "\n" for e in events))
    manifest = stop.extract(queue, "sess-1", str(repo))
    out = repo / ".living" / "log" / "data-lineage" / "sess-1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    stop.write_manifest_atomic(out, manifest)
    text = out.read_text()
    return json.loads(text), text


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


# -- 1. an option-shaped credential --------------------------------------------


def test_the_reviewed_option_shaped_credential_is_not_published(repo: Path) -> None:
    command = f"python3 -c 'import sys; assert len(sys.argv) == 3' --proof {CREDENTIAL}"
    manifest, text = _manifest(repo, command)
    assert CREDENTIAL not in text
    assert "cinder" not in text
    (action,) = manifest["actions"]
    assert action["bash_cmd"] == f"python3 -c {V} {V} {V}"
    assert action["step"]["script_args"] == [V, V]
    assert action["step"]["argv"][-2:] == [V, V]


def test_an_option_the_inline_body_names_is_kept(repo: Path) -> None:
    body = ("import argparse; p = argparse.ArgumentParser(); "
            "p.add_argument('--min-count', type=int); p.parse_args()")
    manifest, _ = _manifest(repo, f'python3 -c "{body}" --min-count 3 --cinder-x 4')
    (action,) = manifest["actions"]
    assert action["step"]["script_args"] == ["--min-count", "3", V, "4"]
    assert action["bash_cmd"].endswith(f"--min-count 3 {V} 4")


def test_an_option_the_script_file_names_is_kept(repo: Path) -> None:
    (repo / "fit.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\n"
        "p.add_argument('--min-count')\np.parse_args()\n")
    manifest, text = _manifest(repo, f"python3 fit.py --min-count 3 {CREDENTIAL}")
    (action,) = manifest["actions"]
    assert action["step"]["script_args"] == ["--min-count", "3", V]
    assert action["bash_cmd"] == f"python3 fit.py --min-count 3 {V}"
    assert CREDENTIAL not in text


def test_common_options_stay_without_source_evidence() -> None:
    assert lr.commit_safe_command("tool --verbose --dry-run --threads=4 --output x", set()) \
        == f"{V} --verbose --dry-run --threads=4 --output {V}"
    assert lr.commit_safe_argv(["--seed", "3", "--help"], set(), command_word=False) == \
        ["--seed", "3", "--help"]


@pytest.mark.parametrize(("command", "expected"), [
    ("git log --oneline --cinder-lattice", f"git log --oneline {V}"),
    ("grep --ignore-case --cinder-lattice x", f"grep --ignore-case {V} {V}"),
    # A command's own options are not lent to another command.
    ("cat --oneline", f"cat {V}"),
    ("curl --insecure --cinder-lattice=1", f"curl --insecure {V}"),
    ("timeout --signal 9 python3 --cinder-lattice", f"timeout --signal 9 python3 {V}"),
    # Short options stay by shape.
    ("ls -la -xzf -O2", "ls -la -xzf -O2"),
])
def test_vocabulary_commands_keep_only_their_known_long_options(
        command: str, expected: str) -> None:
    assert lr.commit_safe_command(command, set()) == expected


def test_source_options_are_lent_only_to_the_receiving_interpreter() -> None:
    known = lr.commit_source_options("p.add_argument('--min-count')")
    assert known == {"--min-count"}
    assert lr.commit_safe_command("python3 a.py --min-count 3; curl --min-count 3",
                                  set(), known) == \
        f"python3 {V} --min-count 3; curl {V} 3"
    # After `--` it is an operand, whatever the source names.
    assert lr.commit_safe_argv(["--", "--min-count"], set(), False, known) == ["--", V]


def test_source_options_need_the_whole_token() -> None:
    # `--min-count-x` names neither `--min-count` nor `--min`.
    assert lr.commit_source_options("x = '--min-count-x'") == {"--min-count-x"}
    assert lr.commit_source_options("--Foo --a_b") == set()
    assert lr.commit_source_options(None) == set()


def test_the_option_list_holds_no_word_outside_the_option_shape() -> None:
    lists = [lr._COMMIT_COMMON_OPTIONS, *lr._COMMIT_COMMAND_OPTIONS.values()]
    for names in lists:
        for name in names:
            assert lr._option_name_ok(name), name


# -- 2. seeds republishing a masked value --------------------------------------


def test_the_reviewed_seed_after_double_dash_is_not_published(repo: Path) -> None:
    command = f"python3 -c 'import sys; assert sys.argv[1] == \"--\"' -- --seed {LONG_SEED}"
    manifest, text = _manifest(repo, command)
    assert LONG_SEED not in text
    (action,) = manifest["actions"]
    assert "seeds" not in action["step"]


def test_the_seed_scan_stops_at_double_dash(tmp_path: Path) -> None:
    invocations: list[dict] = []
    command = "python3 -c 'print(1)' -- --seed 42"
    detections = ev._detect_scripts_with_cwd(command, tmp_path, invocations=invocations)
    ((_, src, cwd),), (inv,) = detections, invocations
    step = ev.build_step(command, inv, src, cwd)
    assert "seeds" not in step
    before = "python3 -c 'print(1)' --seed 42 --"
    invocations = []
    detections = ev._detect_scripts_with_cwd(before, tmp_path, invocations=invocations)
    step = ev.build_step(before, invocations[0], detections[0][1], detections[0][2])
    assert step["seeds"] == [{"value": 42, "origin": "argv"}]


def test_a_long_argv_seed_is_dropped_at_consolidation(repo: Path) -> None:
    (repo / "fit.py").write_text("print(1)\n")
    manifest, text = _manifest(repo, f"python3 fit.py --seed {LONG_SEED}")
    assert LONG_SEED not in text
    (action,) = manifest["actions"]
    assert "seeds" not in action["step"]


def test_a_short_argv_seed_is_kept(repo: Path) -> None:
    (repo / "fit.py").write_text("print(1)\n")
    manifest, _ = _manifest(repo, "python3 fit.py --seed 42")
    (action,) = manifest["actions"]
    assert action["step"]["seeds"] == [{"value": 42, "origin": "argv"}]


def test_committed_seeds_follow_the_committed_argv() -> None:
    event = {"ts": "t", "bash_cmd": "python3 -c x -- --seed 42", "script": None,
             "inputs": [], "outputs": [], "seeds_detected": [7, int(LONG_SEED)],
             "step": {"argv": ["python3", "-c", "x", "--", "--seed", "42"],
                      "script_args": ["--", "--seed", "42"],
                      "seeds": [{"value": 42, "origin": "argv"},
                                {"value": int(LONG_SEED), "origin": "argv"},
                                {"value": 7, "origin": "source"},
                                {"value": int(LONG_SEED), "origin": "source"}]}}
    out = stop.commit_event(event)
    # `--seed 42` after `--` is masked in the argv, so it is no seed here.
    assert out["step"]["seeds"] == [{"value": 7, "origin": "source"}]
    assert out["seeds_detected"] == [7]
    assert LONG_SEED not in json.dumps(out)
    eq = stop.commit_event({**event, "step": {"script_args": ["--seed=42"],
                                              "seeds": [{"value": 42, "origin": "argv"}]}})
    assert eq["step"]["seeds"] == [{"value": 42, "origin": "argv"}]
