"""Regression tests for the second Codex review of `step` settings.

1. Config operands follow shell expansion: an unquoted `~/c.yaml` is the
   home file, a quoted one is cwd-relative, other expansions are unresolved.
2. `R --file=X --args ...` keeps the arguments after `--args`.
3. The 64-stage cap applies in command order, not detector order.
4. Config digests are not taken again by the proof pass.
5. Snakemake profiles with only `config.v8+.yaml`, and `--config` overrides
   in `step.params`.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import extract_data_lineage_event as ev  # noqa: E402
import io_observer as io  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "config").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out.csv')\n")
    (root / "config" / "params.yaml").write_text("alpha: 0.1\n")
    return root


def run(repo: Path, command: str, *extra: str, env: dict | None = None) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo), *extra],
        capture_output=True, text=True, check=True, env=env)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _script(word: str = "fit.py") -> dict:
    return {"offset": 0, "kind": "script", "script_word": word}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# 1. Shell expansion of config operands -------------------------------------

def test_unquoted_tilde_config_is_the_home_file(repo: Path, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "c.yaml").write_text("home: 1\n")
    (repo / "~").mkdir()
    (repo / "~" / "c.yaml").write_text("cwd: 1\n")
    monkeypatch.setenv("HOME", str(home))
    step = ev.build_step("python fit.py --config ~/c.yaml", _script(), None, repo)
    (config,) = step["config_files"]
    assert config["path"] == str(home / "c.yaml")
    assert config["sha256"] == _sha("home: 1\n")


def test_quoted_tilde_config_is_cwd_relative(repo: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (repo / "~").mkdir()
    (repo / "~" / "c.yaml").write_text("cwd: 1\n")
    for command in ("python fit.py --config '~/c.yaml'", 'python fit.py --config "~"/c.yaml',
                    r"python fit.py --config \~/c.yaml"):
        step = ev.build_step(command, _script(), None, repo)
        (config,) = step["config_files"]
        assert config["path"] == str(repo / "~" / "c.yaml"), command
        assert config["sha256"] == _sha("cwd: 1\n"), command


@pytest.mark.parametrize("operand", [
    "$HOME/c.yaml", '"$CFG"', "conf/*.yaml", "c?.yaml", "c[12].yaml", "{a,b}.yaml",
    "~+/c.yaml", "~nosuchuser_mycelium_xyz/c.yaml",
])
def test_other_expansions_are_unresolved(repo: Path, operand: str) -> None:
    step = ev.build_step(f"python fit.py --config {operand}", _script(), None, repo)
    (config,) = step["config_files"]
    assert config["hash_status"] == "unresolved", operand
    assert config["sha256"] is None


def test_a_tilde_after_equals_is_not_guessed(repo: Path) -> None:
    # Bash leaves `--config=~/x` alone (not an assignment); zsh may not.
    step = ev.build_step("python fit.py --config=~/c.yaml", _script(), None, repo)
    (config,) = step["config_files"]
    assert config["hash_status"] == "unresolved"


def test_a_quoted_dollar_is_a_literal_name(repo: Path) -> None:
    (repo / "config" / "$v.yaml").write_text("v: 1\n")
    step = ev.build_step("python fit.py --config 'config/$v.yaml'", _script(), None, repo)
    (config,) = step["config_files"]
    assert config["sha256"] == _sha("v: 1\n")


def test_tilde_operand_end_to_end(repo: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "c.yaml").write_text("home: 1\n")
    import os

    env = {**os.environ, "HOME": str(home)}
    (event,) = run(repo, "python fit.py --config ~/c.yaml", env=env)
    (config,) = event["step"]["config_files"]
    assert config["path"] == str(home / "c.yaml")


# 2. R --file=X --args ---------------------------------------------------------

def test_r_file_equals_keeps_arguments_after_args(repo: Path) -> None:
    (repo / "analysis.R").write_text("x <- read.csv('data/in.csv')\n")
    (event,) = run(repo, "R --file=analysis.R --args --seed 123")
    step = event["step"]
    assert step["script_args"] == ["--seed", "123"]
    assert {"value": 123, "origin": "argv"} in step["seeds"]


def test_r_dash_f_follows_r_grammar(repo: Path) -> None:
    (repo / "analysis.R").write_text("x <- read.csv('data/in.csv')\n")
    (event,) = run(repo, "R -f analysis.R --vanilla --args --seed 7")
    assert event["step"]["script_args"] == ["--seed", "7"]
    (no_args,) = run(repo, "R --vanilla --file=analysis.R")
    assert no_args["step"]["script_args"] == []


# 3. Stage cap in command order ---------------------------------------------

def test_stage_cap_keeps_the_earliest_stages(repo: Path) -> None:
    inline = " && ".join(f"python -c 'print({i})'" for i in range(ev.MAX_EVENTS_PER_CALL + 6))
    events = run(repo, f"python fit.py --first && {inline}")
    assert len(events) == ev.MAX_EVENTS_PER_CALL
    assert events[0]["step"]["script_args"] == ["--first"]
    assert events[-1]["stages_not_recorded"] == 7


def test_config_budget_goes_to_the_earliest_stages(repo: Path) -> None:
    for i in range(ev.CONFIG_MAX_FILES + 2):
        (repo / "config" / f"c{i}.yaml").write_text(f"i: {i}\n")
    stages = [f"python -c 'print({i})' --config config/c{i}.yaml"
              for i in range(1, ev.CONFIG_MAX_FILES + 2)]
    events = run(repo, " && ".join(["python fit.py --config config/c0.yaml", *stages]))
    first = next(e for e in events if e["step"].get("script_args") == [
        "--config", "config/c0.yaml"])
    assert first["step"]["config_files"][0]["sha256"] == _sha("i: 0\n")


# 4. Proof pass reuses the config digest ------------------------------------

def _observed_run(repo: Path, command: str) -> list[dict]:
    state = repo / ".mycelium"
    state.mkdir(exist_ok=True)
    time.sleep(0.05)
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
           snapshot=True)
    observation = io.post(state, host="claude", session="s1", call_id="t1", tool="Bash",
                          repo_root=repo, bash_cmd=command, outcome="success", bash_exit=0)
    summary = {"status": observation["status"], "observation_id": observation["observation_id"],
               "host_session_id": "s1", "changed": False,
               "pre_snapshot": str(io.done_snapshot_path(
                   io.snapshot_path(state, "claude", "s1", "t1"))),
               "state_dir": str(state)}
    return run(repo, command, "--observation-json", json.dumps(summary))


def test_proof_pass_does_not_rehash_a_hashed_config(repo: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    path = repo / "config" / "params.yaml"
    time.sleep(0.05)
    state = repo / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
           snapshot=True)
    snapshot = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "t1"))
    io.post(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
            bash_cmd="python fit.py", outcome="success", bash_exit=0)
    step = ev.build_step("python fit.py --config config/params.yaml", _script(), None, repo)
    event = {"inputs": [], "step": step}
    opened: list[str] = []
    real_open = io.os.open

    def counting_open(p, *a, **k):
        opened.append(str(p))
        return real_open(p, *a, **k)

    monkeypatch.setattr(io.os, "open", counting_open)
    args = argparse.Namespace(observation={"pre_snapshot": str(snapshot),
                                           "state_dir": str(state)},
                              repo_root=str(repo))
    ev._apply_input_proofs([event], args, repo)
    assert str(path) not in opened  # the digest was reused, not re-read
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is True
    assert config["sha256"] == _sha("alpha: 0.1\n")


def test_a_config_changed_after_its_hash_is_not_proven(repo: Path) -> None:
    import argparse

    path = repo / "config" / "params.yaml"
    time.sleep(0.05)
    state = repo / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
           snapshot=True)
    snapshot = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "t1"))
    io.post(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
            bash_cmd="python fit.py", outcome="success", bash_exit=0)
    step = ev.build_step("python fit.py --config config/params.yaml", _script(), None, repo)
    path.write_text("alpha: 0.5  # longer, so the identity changes\n")
    event = {"inputs": [], "step": step}
    args = argparse.Namespace(observation={"pre_snapshot": str(snapshot),
                                           "state_dir": str(state)},
                              repo_root=str(repo))
    ev._apply_input_proofs([event], args, repo)
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is False
    assert config["sha256"] is None


def test_proven_config_end_to_end(repo: Path) -> None:
    (event,) = _observed_run(repo, "python fit.py --config config/params.yaml")
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is True
    assert config["hash_origin"] == "pre-run-proven"


# 5. Snakemake profiles and --config params ---------------------------------

def test_profile_with_only_a_versioned_config(tmp_path: Path) -> None:
    profile = tmp_path / "p"
    profile.mkdir()
    (profile / "config.v8+.yaml").write_text("cores: 2\n")
    step = ev.build_step("snakemake --profile p", {"offset": 0, "kind": "runner"}, None,
                         tmp_path)
    (config,) = step["config_files"]
    assert Path(config["path"]).name == "config.v8+.yaml"
    assert config["sha256"] == _sha("cores: 2\n")


def test_profile_lists_every_versioned_config(tmp_path: Path) -> None:
    profile = tmp_path / "p"
    profile.mkdir()
    (profile / "config.yaml").write_text("cores: 1\n")
    (profile / "config.v8+.yaml").write_text("cores: 2\n")
    step = ev.build_step("snakemake --profile=p", {"offset": 0, "kind": "runner"}, None,
                         tmp_path)
    names = sorted(Path(c["path"]).name for c in step["config_files"])
    assert names == ["config.v8+.yaml", "config.yaml"]


def test_a_missing_profile_still_names_config_yaml(tmp_path: Path) -> None:
    step = ev.build_step("snakemake --profile nope_mycelium_profile",
                         {"offset": 0, "kind": "runner"}, None, tmp_path)
    (config,) = step["config_files"]
    assert config["hash_status"] == "missing"
    assert Path(config["path"]).name == "config.yaml"


def test_snakemake_config_overrides_are_params(tmp_path: Path) -> None:
    step = ev.build_step(
        "snakemake --config alpha=0.1 api_token=s3cr3t out=x.yaml --cores 2 -C beta=2",
        {"offset": 0, "kind": "runner"}, None, tmp_path)
    assert step["params"] == ["alpha=0.1", "api_token=s3cr3t", "out=x.yaml", "beta=2"]
    assert stop._commit_step(step, set())["params"] == ["alpha=0.1", "api_token=<v>",
                                                         "out=<v>", "beta=2"]
    # A --config value is not a config file operand.
    assert not any(c.get("via") == "suffix" for c in step.get("config_files", []))


def test_snakemake_config_equals_form(tmp_path: Path) -> None:
    step = ev.build_step("snakemake --config=alpha=0.1 beta=3 -Cgamma=4",
                         {"offset": 0, "kind": "runner"}, None, tmp_path)
    assert step["params"] == ["alpha=0.1", "beta=3", "gamma=4"]


def test_snakemake_params_end_to_end(tmp_path: Path) -> None:
    root = tmp_path / "wf"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "Snakefile").write_text("rule all:\n    input: 'a.txt'\n")
    (event,) = run(root, "snakemake --cores 1 --config alpha=0.1")
    assert event["step"]["params"] == ["alpha=0.1"]
