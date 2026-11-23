"""Regression tests for four review findings in the step extractor.

1. An interpreter-variable assignment (or use) that only appears inside
   quotes, a comment, a heredoc body or a substitution does not decide what
   `$PY` runs.
2. Interpreter-variable expansion is one linear forward pass.
3. `build_step` reads the operand the detector matched by its position, not
   by re-finding its spelling (`python -X -c -c 'print(1)'`).
4. Snakemake resolves `--profile` and `--configfile` from the invocation
   directory, before it changes into `--directory`.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage_event as ev  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"
PY = "/opt/env/bin/python3"


# 1. Only real shell assignments and uses count ------------------------------

@pytest.mark.parametrize("command", [
    # The review's example: the real assignment is `true`.
    "PY=/usr/bin/true; printf '%s' '; PY=python3; note'; $PY fit.py",
    'PY=/usr/bin/true; echo "x; PY=python3"; $PY fit.py',
    "PY=/usr/bin/true\n# PY=python3\n$PY fit.py",
    "PY=/usr/bin/true\ncat > n.txt <<EOF\nPY=python3\nEOF\n$PY fit.py",
    # A use that is only quoted text, heredoc data, or inside a substitution.
    f"PY={PY}; echo '; $PY fit.py'",
    f"PY={PY}\ncat > n.txt <<EOF\n$PY fit.py\nEOF",
    f"PY={PY}; X=$($PY fit.py)",
    f"PY={PY}; X=`$PY fit.py`",
    # An assignment inside a substitution or subshell voids the name.
    f"PY={PY}; X=$(PY=/bin/sh); $PY fit.py",
    # A control structure's body is not unconditional.
    f"if true; then\nPY={PY}\nfi\n$PY fit.py",
])
def test_text_that_only_looks_like_shell_is_ignored(command: str) -> None:
    assert ev._expand_interpreter_variables(command) == command


@pytest.mark.parametrize(("command", "expected"), [
    (f"PY={PY}; printf '%s' '; PY=/usr/bin/true'; $PY fit.py",
     f"PY={PY}; printf '%s' '; PY=/usr/bin/true'; {PY} fit.py"),
    (f"PY={PY}\n# PY=/usr/bin/true\n$PY fit.py",
     f"PY={PY}\n# PY=/usr/bin/true\n{PY} fit.py"),
    (f"PY={PY}\ncat > n.txt <<EOF\nPY=/bin/sh\nEOF\n$PY fit.py",
     f"PY={PY}\ncat > n.txt <<EOF\nPY=/bin/sh\nEOF\n{PY} fit.py"),
    (f'PY={PY}; echo "$(PY=/bin/sh)"; $PY fit.py',
     f'PY={PY}; echo "$(PY=/bin/sh)"; {PY} fit.py'),
])
def test_the_real_assignment_still_decides(command: str, expected: str) -> None:
    assert ev._expand_interpreter_variables(command) == expected


# 2. One forward pass --------------------------------------------------------

@pytest.mark.parametrize("command", [
    f"PY={PY}; declare PY=/bin/sh; $PY fit.py",
    f"PY={PY}; local PY; $PY fit.py",
    f"PY={PY}; typeset PY=/bin/sh; $PY fit.py",
    f"PY={PY}; readonly PY=/bin/sh; $PY fit.py",
    f"PY={PY}; PY+=x; $PY fit.py",
    f"PY={PY}; true && PY=/bin/sh; $PY fit.py",
    f"PY={PY}; (PY=/bin/sh); $PY fit.py",
    f"PY={PY}; for PY in a; do :; done; $PY fit.py",
    f"PY={PY}; read -r PY < f; $PY fit.py",
    f"PY={PY}; export PY; unset PY; $PY fit.py",
])
def test_an_unmodeled_set_voids_the_name(command: str) -> None:
    assert ev._expand_interpreter_variables(command) == command


def test_a_void_affects_only_later_uses() -> None:
    command = f"PY={PY}; $PY a.py; unset PY; $PY b.py; PY={PY}; $PY c.py"
    assert ev._expand_interpreter_variables(command) == (
        f"PY={PY}; {PY} a.py; unset PY; $PY b.py; PY={PY}; {PY} c.py")


def test_expansion_is_linear_in_the_number_of_stages() -> None:
    stages = 6000
    command = f"PY={PY}; " + "; ".join(["$PY s.py"] * stages)
    started = time.perf_counter()
    expanded = ev._expand_interpreter_variables(command)
    elapsed = time.perf_counter() - started
    assert expanded.count(f"{PY} s.py") == stages
    assert elapsed < 0.5, elapsed


# 3. The detector's operand position reaches build_step ----------------------

def _steps(command: str, cwd: Path) -> list[dict]:
    invocations: list[dict] = []
    detections = ev._detect_scripts_with_cwd(command, cwd, invocations=invocations)
    return [ev.build_step(command, inv, src, io_cwd)
            for (_, src, io_cwd), inv in zip(detections, invocations)]


def test_an_x_option_value_spelled_dash_c_is_not_the_inline_body(tmp_path: Path) -> None:
    (step,) = _steps("python -X -c -c 'print(1)' arg1", tmp_path)
    assert step["argv"][:3] == ["python", "-X", "-c"]
    assert step["argv"][3] == "-c"
    assert step["argv"][4] != "print(1)"  # the body is a placeholder
    assert step["script_args"] == ["arg1"]


def test_an_x_option_value_spelled_like_the_script_is_not_the_script(
        tmp_path: Path) -> None:
    (tmp_path / "fit.py").write_text("print(1)\n")
    (step,) = _steps("python -X fit.py fit.py --seed 3", tmp_path)
    assert step["script_args"] == ["--seed", "3"]
    assert [s["value"] for s in step["seeds"]] == [3]


def test_a_module_after_an_option_spelled_dash_m(tmp_path: Path) -> None:
    (step,) = _steps("python -X -m -m mypkg.cli in.json", tmp_path)
    assert step["script_args"] == ["in.json"]


# 4. Snakemake pre-chdir operands --------------------------------------------

def _run(repo: Path, command: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


@pytest.fixture()
def workflow(tmp_path: Path) -> Path:
    root = tmp_path / "wf"
    (root / "workflow" / "profile").mkdir(parents=True)
    (root / "results").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "Snakefile").write_text("rule all:\n    input: 'a.txt'\n")
    (root / "workflow" / "profile" / "config.yaml").write_text("cores: 1\n")
    (root / "params.yaml").write_text("alpha: 1\n")
    return root


def test_profile_resolves_from_the_invocation_directory(workflow: Path) -> None:
    (event,) = _run(workflow, "snakemake --profile workflow/profile --directory results")
    (config,) = event["step"]["config_files"]
    assert Path(config["path"]) == (workflow / "workflow" / "profile" / "config.yaml").resolve()
    assert config["sha256"] is not None


def test_configfile_resolves_from_the_invocation_directory(workflow: Path) -> None:
    (event,) = _run(workflow, "snakemake --configfile params.yaml --directory results -c1")
    (config,) = event["step"]["config_files"]
    assert Path(config["path"]) == (workflow / "params.yaml").resolve()
    assert config["sha256"] is not None


def test_build_step_uses_the_launch_directory_for_the_profile(tmp_path: Path) -> None:
    (tmp_path / "prof").mkdir()
    (tmp_path / "prof" / "config.yaml").write_text("cores: 1\n")
    (tmp_path / "results").mkdir()
    step = ev.build_step("snakemake --profile prof --directory results",
                         {"offset": 0, "kind": "runner", "launch_cwd": tmp_path}, None,
                         tmp_path / "results")
    (config,) = step["config_files"]
    assert Path(config["path"]) == (tmp_path / "prof" / "config.yaml").resolve()
