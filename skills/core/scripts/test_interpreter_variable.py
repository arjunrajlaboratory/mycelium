"""An interpreter held in a shell variable (`PY=/x/python; $PY run.py`) is
detected as the analysis run it is, found in a live run on PR #154. Only a
plain, unconditional, literal assignment of an interpreter counts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage_event as ev  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"
PY = "/opt/env/bin/python3"


@pytest.mark.parametrize(("command", "expected"), [
    (f"PY={PY}; $PY run.py", f"PY={PY}; {PY} run.py"),
    (f"PY={PY}\n$PY run.py --seed 3", f"PY={PY}\n{PY} run.py --seed 3"),
    (f"export PY={PY}; \"$PY\" run.py", f"export PY={PY}; {PY} run.py"),
    (f"PY='{PY}'; ${{PY}} run.py", f"PY='{PY}'; {PY} run.py"),
    (f"R=/usr/bin/Rscript; $R fit.R", "R=/usr/bin/Rscript; /usr/bin/Rscript fit.R"),
    (f"PY={PY}; lint && $PY run.py", f"PY={PY}; lint && {PY} run.py"),
])
def test_a_plain_assignment_is_expanded(command: str, expected: str) -> None:
    assert ev._expand_interpreter_variables(command) == expected


@pytest.mark.parametrize("command", [
    f"false && PY={PY}; $PY run.py",          # conditional assignment
    f"PY={PY} $PY run.py",                     # prefix assignment: expanded first
    "PY=/usr/bin/cat; $PY run.py",             # not an interpreter
    "PY=$(which python3); $PY run.py",         # computed value
    f"(PY={PY}); $PY run.py",                  # set in a subshell
    f"PY={PY}; PY=/bin/sh; $PY run.py",        # a later reassignment wins
    f"PY={PY}; unset PY; $PY run.py",          # unset
    f"PY={PY}; read PY; $PY run.py",           # read
    "$PY run.py",                              # never assigned here
    f"PY={PY}*; $PY run.py",                   # a glob
])
def test_anything_else_is_left_as_written(command: str) -> None:
    assert ev._expand_interpreter_variables(command) == command


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / ".living").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "c.yaml").write_text("t: 1\n")
    (root / "run.py").write_text("import pandas as pd\npd.read_csv('data/in.csv')\n")
    return root


@pytest.mark.parametrize("assignment", [
    "R=$(cat .mycelium/plugin-root)/skills/core/scripts",
    'R="$(cat .mycelium/plugin-root)/skills/core/scripts"',
    'R="$(cat .mycelium/plugin-root)"/skills/core/scripts',
])
def test_a_helper_under_a_suffixed_plugin_root_variable_is_mycelium_s_own(
        repo: Path, assignment: str) -> None:
    # Live run: `PY=…; R=$(cat .mycelium/plugin-root)/skills/core/scripts;
    # $PY "$R/lineage_index.py"` became a run of an unreadable script.
    plugin = HERE.parent.parent.parent
    (repo / ".mycelium").mkdir()
    (repo / ".mycelium" / "plugin-root").write_text(str(plugin) + "\n")
    command = f'PY={sys.executable}; {assignment}; $PY "$R/lineage_index.py" query'
    path = ev._variable_script_path(command, command.index("$R/"), "$R/lineage_index.py",
                                    repo)
    assert path == (plugin / "skills" / "core" / "scripts" / "lineage_index.py").resolve()
    assert ev._is_managed_utility_path(path)


def test_the_event_keeps_the_command_as_written(repo: Path) -> None:
    command = f"PY={sys.executable}; $PY run.py --config c.yaml"
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T00:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True)
    (event,) = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert event["bash_cmd"] == command
    assert event["script"].endswith("run.py")
    assert [Path(c["path"]).name for c in event["step"]["config_files"]] == ["c.yaml"]
