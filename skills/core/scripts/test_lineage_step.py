"""Each recorded step carries the settings it actually ran with.

Item 4 of the provenance work: argv, interpreter, script arguments,
environment assignments, config files (proven against the Pre snapshot),
and literal seeds, read from the matched stage itself; one redaction policy
for every place command text is stored; `filters_detected` gone.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402
import extract_data_lineage_event as ev  # noqa: E402

pytestmark = pytest.mark.platform

EXTRACTOR = HERE / "extract_data_lineage_event.py"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "out").mkdir()
    (root / "config").mkdir()
    (root / ".living").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text(
        "import pandas as pd\nimport numpy as np\nnp.random.seed(11)\n"
        "pd.read_csv('data/in.csv').to_csv('out/res.csv')\n")
    (root / "config" / "params.yaml").write_text("alpha: 0.1\n")
    (root / "config" / "extra.json").write_text('{"beta": 2}\n')
    return root


def run(repo: Path, command: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo), *extra],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_two_runs_with_different_arguments_are_two_steps(repo: Path) -> None:
    events = run(repo, "python fit.py --seed 1 && python fit.py --seed 2")
    assert [e["step"]["script_args"] for e in events] == [["--seed", "1"], ["--seed", "2"]]
    assert [s["value"] for s in events[0]["step"]["seeds"]] == [1, 11]
    assert events[0]["step"]["seeds"][0]["origin"] == "argv"
    assert events[0]["step"]["seeds"][1]["origin"] == "source"


def test_identical_stages_are_one_step(repo: Path) -> None:
    assert len(run(repo, "python fit.py; python fit.py")) == 1


def test_argv_interpreter_and_environment(repo: Path) -> None:
    (event,) = run(repo, "OMP_NUM_THREADS=4 MODE=$X env -u FOO python3 -u fit.py --n 3")
    step = event["step"]
    assert step["argv"] == ["python3", "-u", "fit.py", "--n", "3"]
    assert step["interpreter"] == "python"
    assert step["script_args"] == ["--n", "3"]
    assert step["env"] == {"OMP_NUM_THREADS": "4", "MODE": {"text": "$X", "expanded": False}}
    assert "env" in step["wrappers"]
    assert "filters_detected" not in event


def test_secrets_never_reach_the_committed_record(repo: Path) -> None:
    command = ("HF_TOKEN=hf_abcdef123 python fit.py --api-key sk-live-999 "
               "--password=hunter2 --url https://bob:pw123@example.org/x")
    (event,) = run(repo, command)
    text = json.dumps(stop.commit_event(event))
    for secret in ("hf_abcdef123", "sk-live-999", "hunter2", "pw123"):
        assert secret not in text, secret


def test_inline_bodies_become_placeholders(repo: Path) -> None:
    body = "import pandas as pd; pd.read_csv('data/in.csv')"
    (event,) = run(repo, f'python -c "{body}"')
    placeholder = event["step"]["argv"][2]
    assert placeholder["length"] == len(body)
    assert placeholder["inline_sha256"] == hashlib.sha256(body.encode()).hexdigest()


def test_a_script_keeps_its_file_digest_and_commits_no_source(repo: Path) -> None:
    body = "api_key = 'sk-abcdefghij'\nimport pandas as pd\npd.read_csv('data/in.csv')\n"
    (repo / "secret.py").write_text(body)
    (event,) = run(repo, "python secret.py")
    assert "script_sha256_of" not in event
    assert event["script_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert "sk-abcdefghij" not in json.dumps(stop.commit_event(event))


def test_config_files_by_interpreter_rules(repo: Path) -> None:
    (event,) = run(repo, "python fit.py --config config/params.yaml config/extra.json "
                         "--params=config/extra.json --settings missing.yaml")
    configs = {(c["via"], Path(c["path"]).name): c for c in event["step"]["config_files"]}
    assert configs[("--config", "params.yaml")]["sha256"] == hashlib.sha256(
        b"alpha: 0.1\n").hexdigest()
    assert configs[("--config", "params.yaml")]["rel_path"] == "config/params.yaml"
    # A bare .json operand is not a config; after a config flag it is.
    assert ("suffix", "extra.json") not in configs
    assert ("--params", "extra.json") in configs
    # A flagged config that does not exist is still listed.
    assert configs[("--settings", "missing.yaml")]["hash_status"] == "missing"


def test_python_dash_c_and_snakemake_cores_are_not_configs(repo: Path) -> None:
    (inline,) = run(repo, 'python -c "print(1)" config/params.yaml')
    assert "config_files" not in inline["step"] or all(
        c["via"] == "suffix" for c in inline["step"]["config_files"])
    import extract_data_lineage_event as ev

    assert ev._config_candidates("snakemake", ["-c", "4", "--configfile", "a.yaml"]) == [
        ("a.yaml", "--configfile")]
    assert ev._config_candidates("snakemake", ["--profile", "prof"]) == [
        (str(Path("prof") / "config.yaml"), "--profile")]


def test_config_budget_is_bounded(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import extract_data_lineage_event as ev

    big = repo / "config" / "big.yaml"
    big.write_bytes(b"x" * (ev.CONFIG_MAX_BYTES + 1))
    record = ev._hash_config(big, deadline=10**12)
    assert record["hash_status"] == "too-large" and record["sha256"] is None
    assert ev._hash_config(repo / "config" / "params.yaml", deadline=0)["hash_status"] == (
        "skipped-budget")


def test_data_command_step_is_the_working_stage(repo: Path) -> None:
    (event,) = run(repo, "cd data && cp in.csv copy.csv")
    assert event["kind"] == "data_command"
    assert event["step"]["executable"] == "cp"
    assert event["step"]["argv"] == ["cp", "in.csv", "copy.csv"]


def test_runner_steps_carry_the_runner_argv(tmp_path: Path) -> None:
    import extract_data_lineage_event as ev

    (tmp_path / "config.yml").write_text("a: 1\n")
    command = "echo go && snakemake --snakefile Snakefile --configfile config.yml --config batch=2"
    step = ev.build_step(command, {"offset": None, "kind": "runner"}, None, tmp_path)
    assert step["interpreter"] == "snakemake"
    assert step["argv"][:2] == ["snakemake", "--snakefile"]
    (config,) = step["config_files"]
    assert config["via"] == "--configfile" and config["sha256"]
    assert ev.build_step("true", {"offset": None, "kind": "runner"}, None, None) == {
        "partial": True, "reason": "runner-argv-not-captured"}


def test_a_config_replaced_during_the_call_is_not_the_steps_setting(repo: Path) -> None:
    state = repo / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
           snapshot=True)
    (repo / "config" / "params.yaml").write_text("alpha: 0.9  # replaced mid-call\n")
    command = "python fit.py --config config/params.yaml"
    observation = io.post(state, host="claude", session="s1", call_id="t1", tool="Bash",
                          repo_root=repo, bash_cmd=command, outcome="success", bash_exit=0)
    summary = {"status": observation["status"], "observation_id": observation["observation_id"],
               "host_session_id": "s1", "changed": True,
               "pre_snapshot": str(io.done_snapshot_path(
                   io.snapshot_path(state, "claude", "s1", "t1"))),
               "state_dir": str(state)}
    (event,) = run(repo, command, "--observation-json", json.dumps(summary))
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is False
    assert config["effective"] == "unknown"


def test_an_unchanged_config_is_proven(repo: Path) -> None:
    import time

    state = repo / ".mycelium"
    state.mkdir()
    # The fixture just wrote the file: one touched within the timestamp
    # granularity of the snapshot is (correctly) not provable, so wait.
    time.sleep(0.05)
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo,
           snapshot=True)
    command = "python fit.py --config config/params.yaml"
    observation = io.post(state, host="claude", session="s1", call_id="t1", tool="Bash",
                          repo_root=repo, bash_cmd=command, outcome="success", bash_exit=0)
    summary = {"status": observation["status"], "observation_id": observation["observation_id"],
               "host_session_id": "s1", "changed": False,
               "pre_snapshot": str(io.done_snapshot_path(
                   io.snapshot_path(state, "claude", "s1", "t1"))),
               "state_dir": str(state)}
    (event,) = run(repo, command, "--observation-json", json.dumps(summary))
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is True
    assert config["hash_origin"] == "pre-run-proven"
    assert "effective" not in config


def test_observations_store_allowlisted_commands(tmp_path: Path) -> None:
    repo = tmp_path / "r"
    repo.mkdir()
    state = repo / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s", call_id="c", tool="Bash", repo_root=repo,
           snapshot=True)
    io.post(state, host="claude", session="s", call_id="c", tool="Bash", repo_root=repo,
            bash_cmd="DB_PASSWORD=x9y8z7 make -k 9f8a7b6c5d4e3f2a1b0c")
    (stored,) = [json.loads(line) for line in
                 io.observations_path(state, "claude", "s").read_text().splitlines()]
    assert stored["bash_cmd"] == "DB_PASSWORD=<v> make -k <v>"


def test_consolidation_redacts_historical_records(tmp_path: Path) -> None:
    events = tmp_path / "events.tmp"
    events.write_text(json.dumps({"ts": "2026-10-01T00:00:00Z",
                                  "bash_cmd": "python x.py --token abc123secret",
                                  "inputs": [], "outputs": []}) + "\n"
                      + json.dumps({"ts": "2026-10-01T00:00:01Z", "kind": "skip",
                                    "skip_id": "k", "skip_reason": "detector-failed",
                                    "bash_cmd": "curl -H 'Authorization: Bearer tok999'"})
                      + "\n")
    manifest = stop.extract(events, "s", str(tmp_path))
    text = json.dumps(manifest)
    assert "abc123secret" not in text and "tok999" not in text


def test_secrets_inside_environment_values_and_inline_bodies(repo: Path) -> None:
    (event,) = run(repo, "HTTPS_PROXY=https://user:pa55w0rd@proxy:8080 python fit.py")
    assert "pa55w0rd" not in json.dumps(stop.commit_event(event))
    body = "password = 'example-secret'; import pandas as pd; pd.read_csv('data/in.csv')"
    (inline,) = run(repo, f'python -c "{body}"')
    assert "example-secret" not in json.dumps(stop.commit_event(inline))


def test_the_sanitized_label_survives_consolidation(tmp_path: Path) -> None:
    events = tmp_path / "e.tmp"
    events.write_text(json.dumps({"ts": "2026-11-22T00:00:00Z", "event_id": "e" * 32,
                                  "bash_cmd": "python s.py", "script_sha256": "a" * 64,
                                  "script_sha256_of": "sanitized", "inputs": [],
                                  "outputs": []}) + "\n")
    (action,) = stop.extract(events, "s", str(tmp_path))["actions"]
    assert action["script_sha256_of"] == "sanitized"


def test_a_failed_patch_claims_no_tombstone(tmp_path: Path) -> None:
    assert io.edit_outcome({"exit_code": 1}) == "failure"
    assert io.edit_outcome({"output": "{\"exit_code\": 0}"}) == "success"
    assert io.edit_outcome(None) == "unknown"
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "still.csv").write_text("here")
    written, deleted, refused = io._edit_changes(repo, [(str(repo / "still.csv"), "deleted"),
                                                        (str(repo / "x.csv"), "uncertain")])
    assert deleted == []
    assert {r["reason"] for r in refused} == {"deletion-not-applied", "edit-failed"}


def test_proofs_refuse_a_snapshot_of_another_checkout(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    for root in (a, b):
        (root / ".mycelium").mkdir(parents=True)
        (root / "c.yaml").write_text("x: 1\n")
    io.pre(a / ".mycelium", host="claude", session="s", call_id="t", tool="Bash",
           repo_root=a, snapshot=True)
    snap = io.snapshot_path(a / ".mycelium", "claude", "s", "t")
    proofs = io.prove_inputs(a / ".mycelium", snap, b, [str(b / "c.yaml")])
    assert proofs[str(b / "c.yaml")] == {"pre_run_proven": False, "reason": "cross-root"}


def test_the_same_call_id_in_two_sessions_keeps_two_intervals(tmp_path: Path) -> None:
    io.journal_event(tmp_path, "pre", "same", host="claude", session="A", tool="Bash")
    io.journal_event(tmp_path, "pre", "same", host="claude", session="B", tool="Bash")
    _, intervals, _ = io.journal_event(tmp_path, "post", "same", host="claude", session="A",
                                       tool="Bash")
    a = intervals[io.interval_key("claude", "A", "same")]
    b = intervals[io.interval_key("claude", "B", "same")]
    assert a.post_seq is not None
    assert b.post_seq is None  # A's Post never closes B's interval


def test_many_distinct_stages_are_capped(repo: Path) -> None:
    import time

    command = " && ".join(f"python fit.py --run {i}" for i in range(200))
    started = time.monotonic()
    events = run(repo, command)
    assert time.monotonic() - started < 30
    import extract_data_lineage_event as ev

    assert len(events) == ev.MAX_EVENTS_PER_CALL
    assert events[-1]["stages_not_recorded"] == 200 - ev.MAX_EVENTS_PER_CALL


def test_the_config_budget_is_shared_across_stages(repo: Path) -> None:
    import extract_data_lineage_event as ev

    for i in range(12):
        (repo / "config" / f"c{i}.yaml").write_text(f"i: {i}\n")
    budget = ev.ConfigBudget()
    steps = [ev.build_step(f"python fit.py --config config/c{i}.yaml",
                           {"offset": 0, "kind": "script", "script_word": "fit.py"}, None, repo,
                           budget) for i in range(12)]
    hashed = [c for s in steps for c in s["config_files"] if c.get("sha256")]
    assert len(hashed) == ev.CONFIG_MAX_FILES


def test_data_command_step_prefers_the_stage_naming_data(repo: Path) -> None:
    (event,) = run(repo, "echo Starting && cp data/in.csv data/out.csv")
    assert event["step"]["executable"] == "cp"
    assert event["step"]["stage"] == "names-data"


@pytest.fixture()
def repo_2(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / ".living").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text("import pandas as pd\npd.read_csv('data/in.csv')\n")
    return root


def run_2(repo_2: Path, command: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo_2), "--ts", "2026-11-22T00:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo_2), *extra],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_a_secret_value_never_becomes_a_config_path_or_data_path(repo_2: Path) -> None:
    (repo_2 / "s3cret.yaml").write_text("x: 1\n")
    (event,) = run_2(repo_2, "python fit.py --token s3cret.yaml --config data/in.csv")
    assert "s3cret" not in json.dumps(event["step"]["config_files"])
    assert "s3cret" not in json.dumps(stop.commit_event(event))
    (command,) = run_2(repo_2, "cat data/in.csv --password hunter2.csv")
    assert "hunter2" not in json.dumps(stop.commit_data_command(command))


def test_a_large_script_keeps_its_file_digest(repo_2: Path) -> None:
    body = "password = 'example-secret'\n" + "# pad\n" * 30000 + \
        "import pandas as pd\npd.read_csv('data/in.csv')\n"
    (repo_2 / "big.py").write_text(body)
    (event,) = run_2(repo_2, "python big.py")
    assert event["script_source"] is None
    assert event["script_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert "script_sha256_of" not in event


def test_inline_bodies_stop_at_the_interpreter_boundary(repo_2: Path) -> None:
    (event,) = run_2(repo_2, "python -c 'import pandas as pd; pd.read_csv(\"data/in.csv\")' "
                         "--seed 7 -c app-value")
    step = event["step"]
    assert step["script_args"] == ["--seed", "7", "-c", "app-value"]
    assert [s["value"] for s in step["seeds"]] == [7]


def test_an_oversized_seed_keeps_the_event(repo_2: Path) -> None:
    (event,) = run_2(repo_2, "python fit.py --seed " + "9" * 4400)
    assert "seeds" not in event["step"]


def test_a_comment_line_is_not_the_data_stage(repo_2: Path) -> None:
    (event,) = run_2(repo_2, "# inspect data/in.csv\nhead data/in.csv")
    assert event["step"]["executable"] == "head"


def test_two_runner_stages_with_different_configs_are_two_events(tmp_path: Path) -> None:
    import extract_data_lineage_event as ev

    root = tmp_path / "r"
    root.mkdir()
    (root / "Snakefile").write_text("rule all:\n    input: []\n")
    (root / "a.yml").write_text("a: 1\n")
    (root / "b.yml").write_text("b: 2\n")
    invocations: list[dict] = []
    command = "snakemake --configfile a.yml && snakemake --configfile b.yml"
    found = ev._detect_scripts_with_cwd(command, root, bash_exit=0, invocations=invocations)
    runners = [i for i in invocations if i["kind"] == "runner"]
    assert len(found) == 2 and len(runners) == 2
    steps = [ev.build_step(command, inv, None, root) for inv in runners]
    assert sorted(Path(s["config_files"][0]["path"]).name for s in steps) == ["a.yml", "b.yml"]


def test_a_reassigned_helper_variable_is_not_exempt(tmp_path: Path) -> None:
    import extract_data_lineage_event as ev

    plugin = HERE.parent.parent.parent
    raw = "$P/skills/core/scripts/lineage_index.py"
    reassigned = f"P={plugin}; P=.; python3 {raw}"
    assert ev._variable_script_path(reassigned, reassigned.index(raw), raw, tmp_path) is None
    single = f"P={plugin}; python3 {raw}"
    assert ev._variable_script_path(single, single.index(raw), raw, tmp_path) is not None


def test_an_unproven_config_says_so(repo_2: Path) -> None:
    (repo_2 / "c.yaml").write_text("a: 1\n")
    (event,) = run_2(repo_2, "python fit.py --config c.yaml")
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is False and config["effective"] == "unknown"


def _script(word: str = "fit.py") -> dict:
    return {"offset": 0, "kind": "script", "script_word": word}


@pytest.fixture()
def repo_3(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "config").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out.csv')\n")
    (root / "config" / "params.yaml").write_text("alpha: 0.1\n")
    return root


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_unquoted_tilde_config_is_the_home_file(repo_3: Path, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "c.yaml").write_text("home: 1\n")
    (repo_3 / "~").mkdir()
    (repo_3 / "~" / "c.yaml").write_text("cwd: 1\n")
    monkeypatch.setenv("HOME", str(home))
    step = ev.build_step("python fit.py --config ~/c.yaml", _script(), None, repo_3)
    (config,) = step["config_files"]
    assert config["path"] == str(home / "c.yaml")
    assert config["sha256"] == _sha("home: 1\n")


def test_quoted_tilde_config_is_cwd_relative(repo_3: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (repo_3 / "~").mkdir()
    (repo_3 / "~" / "c.yaml").write_text("cwd: 1\n")
    for command in ("python fit.py --config '~/c.yaml'", 'python fit.py --config "~"/c.yaml',
                    r"python fit.py --config \~/c.yaml"):
        step = ev.build_step(command, _script(), None, repo_3)
        (config,) = step["config_files"]
        assert config["path"] == str(repo_3 / "~" / "c.yaml"), command
        assert config["sha256"] == _sha("cwd: 1\n"), command


@pytest.mark.parametrize("operand", [
    "$HOME/c.yaml", '"$CFG"', "conf/*.yaml", "c?.yaml", "c[12].yaml", "{a,b}.yaml",
    "~+/c.yaml", "~nosuchuser_mycelium_xyz/c.yaml",
])
def test_other_expansions_are_unresolved(repo_3: Path, operand: str) -> None:
    step = ev.build_step(f"python fit.py --config {operand}", _script(), None, repo_3)
    (config,) = step["config_files"]
    assert config["hash_status"] == "unresolved", operand
    assert config["sha256"] is None


def test_a_tilde_after_equals_is_not_guessed(repo_3: Path) -> None:
    # Bash leaves `--config=~/x` alone (not an assignment); zsh may not.
    step = ev.build_step("python fit.py --config=~/c.yaml", _script(), None, repo_3)
    (config,) = step["config_files"]
    assert config["hash_status"] == "unresolved"


def test_a_quoted_dollar_is_a_literal_name(repo_3: Path) -> None:
    (repo_3 / "config" / "$v.yaml").write_text("v: 1\n")
    step = ev.build_step("python fit.py --config 'config/$v.yaml'", _script(), None, repo_3)
    (config,) = step["config_files"]
    assert config["sha256"] == _sha("v: 1\n")


def run_3(repo_3: Path, command: str, *extra: str, env: dict | None = None) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo_3), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo_3), *extra],
        capture_output=True, text=True, check=True, env=env)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_tilde_operand_end_to_end(repo_3: Path, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "c.yaml").write_text("home: 1\n")
    import os

    env = {**os.environ, "HOME": str(home)}
    (event,) = run_3(repo_3, "python fit.py --config ~/c.yaml", env=env)
    (config,) = event["step"]["config_files"]
    assert config["path"] == str(home / "c.yaml")


def test_r_file_equals_keeps_arguments_after_args(repo_3: Path) -> None:
    (repo_3 / "analysis.R").write_text("x <- read.csv('data/in.csv')\n")
    (event,) = run_3(repo_3, "R --file=analysis.R --args --seed 123")
    step = event["step"]
    assert step["script_args"] == ["--seed", "123"]
    assert {"value": 123, "origin": "argv"} in step["seeds"]


def test_r_dash_f_follows_r_grammar(repo_3: Path) -> None:
    (repo_3 / "analysis.R").write_text("x <- read.csv('data/in.csv')\n")
    (event,) = run_3(repo_3, "R -f analysis.R --vanilla --args --seed 7")
    assert event["step"]["script_args"] == ["--seed", "7"]
    (no_args,) = run_3(repo_3, "R --vanilla --file=analysis.R")
    assert no_args["step"]["script_args"] == []


def test_stage_cap_keeps_the_earliest_stages(repo_3: Path) -> None:
    inline = " && ".join(f"python -c 'print({i})'" for i in range(ev.MAX_EVENTS_PER_CALL + 6))
    events = run_3(repo_3, f"python fit.py --first && {inline}")
    assert len(events) == ev.MAX_EVENTS_PER_CALL
    assert events[0]["step"]["script_args"] == ["--first"]
    assert events[-1]["stages_not_recorded"] == 7


def test_config_budget_goes_to_the_earliest_stages(repo_3: Path) -> None:
    for i in range(ev.CONFIG_MAX_FILES + 2):
        (repo_3 / "config" / f"c{i}.yaml").write_text(f"i: {i}\n")
    stages = [f"python -c 'print({i})' --config config/c{i}.yaml"
              for i in range(1, ev.CONFIG_MAX_FILES + 2)]
    events = run_3(repo_3, " && ".join(["python fit.py --config config/c0.yaml", *stages]))
    first = next(e for e in events if e["step"].get("script_args") == [
        "--config", "config/c0.yaml"])
    assert first["step"]["config_files"][0]["sha256"] == _sha("i: 0\n")


def test_proof_pass_does_not_rehash_a_hashed_config(repo_3: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    path = repo_3 / "config" / "params.yaml"
    time.sleep(0.05)
    state = repo_3 / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo_3,
           snapshot=True)
    snapshot = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "t1"))
    io.post(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo_3,
            bash_cmd="python fit.py", outcome="success", bash_exit=0)
    step = ev.build_step("python fit.py --config config/params.yaml", _script(), None, repo_3)
    event = {"inputs": [], "step": step}
    opened: list[str] = []
    real_open = io.os.open

    def counting_open(p, *a, **k):
        opened.append(str(p))
        return real_open(p, *a, **k)

    monkeypatch.setattr(io.os, "open", counting_open)
    args = argparse.Namespace(observation={"pre_snapshot": str(snapshot),
                                           "state_dir": str(state)},
                              repo_root=str(repo_3))
    ev._apply_input_proofs([event], args, repo_3)
    assert str(path) not in opened  # the digest was reused, not re-read
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is True
    assert config["sha256"] == _sha("alpha: 0.1\n")


def test_a_config_changed_after_its_hash_is_not_proven(repo_3: Path) -> None:
    import argparse

    path = repo_3 / "config" / "params.yaml"
    time.sleep(0.05)
    state = repo_3 / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo_3,
           snapshot=True)
    snapshot = io.done_snapshot_path(io.snapshot_path(state, "claude", "s1", "t1"))
    io.post(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo_3,
            bash_cmd="python fit.py", outcome="success", bash_exit=0)
    step = ev.build_step("python fit.py --config config/params.yaml", _script(), None, repo_3)
    path.write_text("alpha: 0.5  # longer, so the identity changes\n")
    event = {"inputs": [], "step": step}
    args = argparse.Namespace(observation={"pre_snapshot": str(snapshot),
                                           "state_dir": str(state)},
                              repo_root=str(repo_3))
    ev._apply_input_proofs([event], args, repo_3)
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is False
    assert config["sha256"] is None


def _observed_run(repo_3: Path, command: str) -> list[dict]:
    state = repo_3 / ".mycelium"
    state.mkdir(exist_ok=True)
    time.sleep(0.05)
    io.pre(state, host="claude", session="s1", call_id="t1", tool="Bash", repo_root=repo_3,
           snapshot=True)
    observation = io.post(state, host="claude", session="s1", call_id="t1", tool="Bash",
                          repo_root=repo_3, bash_cmd=command, outcome="success", bash_exit=0)
    summary = {"status": observation["status"], "observation_id": observation["observation_id"],
               "host_session_id": "s1", "changed": False,
               "pre_snapshot": str(io.done_snapshot_path(
                   io.snapshot_path(state, "claude", "s1", "t1"))),
               "state_dir": str(state)}
    return run_3(repo_3, command, "--observation-json", json.dumps(summary))


def test_proven_config_end_to_end(repo_3: Path) -> None:
    (event,) = _observed_run(repo_3, "python fit.py --config config/params.yaml")
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is True
    assert config["hash_origin"] == "pre-run-proven"


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
    (event,) = run_3(root, "snakemake --cores 1 --config alpha=0.1")
    assert event["step"]["params"] == ["alpha=0.1"]


def _file_paths(record: dict) -> list[str]:
    values = []
    for key in ("inputs", "outputs"):
        for rec in record.get(key) or []:
            values += [rec.get("path"), rec.get("rel_path")]
    for rec in (record.get("step") or {}).get("config_files") or []:
        values += [rec.get("path"), rec.get("rel_path")]
    return [v for v in values if isinstance(v, str)]


@pytest.fixture()
def repo_4(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out.csv')\n")
    return root


def run_4(repo_4: Path, command: str, env: dict | None = None) -> tuple[list[dict], str]:
    """(records, the records as a committed manifest holds them, as text)."""
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo_4), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo_4)],
        capture_output=True, text=True, check=True, env=env)
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return records, json.dumps([
        (stop.commit_data_command if r.get("kind") == "data_command" else stop.commit_event)(r)
        for r in records])


def test_url_input_password_never_reaches_inputs(repo_4: Path) -> None:
    (repo_4 / "fit.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv(\"https://user:TEST_SECRET@example.invalid/data.csv\")\n"
        "df.to_csv('out.csv')\n")
    (record,), stdout = run_4(repo_4, "python fit.py")
    assert "TEST_SECRET" not in stdout
    paths = _file_paths(record)
    assert not any(ev._is_url(p) for p in paths), paths
    assert [Path(rec["path"]).name for rec in record["outputs"]] == ["out.csv"]
    assert record["inputs"] == []
    assert any("Remote URLs" in w for w in record["lineage_warnings"])


def test_url_output_password_never_reaches_outputs(repo_4: Path) -> None:
    (repo_4 / "fit.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv('data/in.csv')\n"
        "df.to_csv(\"s3://bob:OUT_SECRET@bucket/out.csv\")\n")
    (record,), stdout = run_4(repo_4, "python fit.py")
    assert "OUT_SECRET" not in stdout
    assert record["outputs"] == []
    assert [Path(rec["path"]).name for rec in record["inputs"]] == ["in.csv"]


@pytest.mark.parametrize("command", [
    "cat data/in.csv > s3://u:SEC3@b/o.csv",
    "cat < s3://u:SEC3@b/i.csv > data/out.csv",
    "cp data/in.csv s3://u:SEC3@b/o.csv",
    "cp https://u:SEC3@h/a.csv data/b.csv",
])
def test_shell_io_never_records_a_url_shaped_word(command: str, repo_4: Path) -> None:
    records, stdout = run_4(repo_4, command)
    assert "SEC3" not in stdout
    for record in records:
        assert not any("s3:" in p for p in _file_paths(record))


def test_url_config_operand_is_a_remote_reference(repo_4: Path) -> None:
    step = ev.build_step("python fit.py --config https://tok@h.invalid/c.yaml?key=Q1",
                         {"offset": 0, "kind": "script", "script_word": "fit.py"}, None, repo_4)
    (config,) = step["config_files"]
    assert config["hash_status"] == "remote"
    assert config["sha256"] is None
    # No evidence: a committed record keeps none of it.
    (committed,) = stop._commit_config_files([config])
    assert committed["path"] == "<v>"


def test_url_config_never_framed_as_a_repository_path(repo_4: Path) -> None:
    (record,), committed = run_4(repo_4, "python fit.py --config https://tok@h.invalid/c.yaml")
    (config,) = record["step"]["config_files"]
    assert config["rel_path"] is None
    assert "tok@" not in committed


def test_url_shapes() -> None:
    assert ev._is_url("s3:/b/x.csv") and not ev._is_url("C:/data/x.csv")
    assert not ev._is_url("data/x.csv")


def _config(command: str, repo_4: Path) -> dict:
    offset = command.index("python fit.py")
    step = ev.build_step(command, {"offset": offset, "kind": "script", "script_word": "fit.py"},
                         None, repo_4)
    (config,) = step["config_files"]
    return config


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "openai.yaml").write_text("model: a\n")
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize("before", [
    "HOME=/repo/alternate; ",
    "HOME=/repo/alternate ",
    "export HOME=/repo/alternate && ",
    "export HOME; ",
    "env HOME=/repo/alternate ",
    "unset HOME; ",
    "declare HOME=/x; ",
    "typeset -x HOME=/x; ",
    "readonly HOME=/x; ",
    "read HOME < f; ",
    ": ${HOME:=/x}; ",
    "source env.sh; ",
    ". ./env.sh && ",
    "eval \"$SETUP\"; ",
    "declare \"$name=/x\"; ",
    "printf -v \"$v\" /x; ",
])
def test_home_changed_before_the_stage_leaves_tilde_unresolved(
        before: str, repo_4: Path, home: Path) -> None:
    config = _config(before + "python fit.py --config ~/agents/openai.yaml", repo_4)
    assert config["hash_status"] == "unresolved"
    assert config["sha256"] is None
    assert "pre_run_proven" not in config


@pytest.mark.parametrize("before", ["", "echo $HOME; ", "cd data && cd .. && ",
                                    "echo ${HOME}; "])
def test_tilde_still_resolves_when_home_is_untouched(before: str, repo_4: Path,
                                                     home: Path) -> None:
    config = _config(before + "python fit.py --config ~/agents/openai.yaml", repo_4)
    assert config["path"] == str(home / "agents" / "openai.yaml")
    assert config["sha256"] is not None


def test_home_assignment_end_to_end(repo_4: Path, home: Path) -> None:
    env = {**os.environ, "HOME": str(home)}
    (record,), _ = run_4(repo_4, "HOME=/repo/alternate; python fit.py --config ~/agents/openai.yaml",
                       env=env)
    (config,) = record["step"]["config_files"]
    assert config["hash_status"] == "unresolved"
    assert config.get("pre_run_proven") is not True


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CDPATH"}
    env.update(extra)
    return env


def _run(repo: Path, command: str, env: dict | None = None) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True, env=env or _env())
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _claims(records: list[dict], repo: Path) -> list[str]:
    """Paths under repo/run that a record claims: the script, or a hashed
    config."""
    run_dir = str(repo / "run") + os.sep
    claimed = []
    for record in records:
        if (record.get("script") or "").startswith(run_dir):
            claimed.append(record["script"])
        for config in (record.get("step") or {}).get("config_files") or []:
            if config.get("sha256") and str(config.get("path", "")).startswith(run_dir):
                claimed.append(config["path"])
    return claimed


@pytest.fixture()
def cdrepo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for directory in ("run", "experiments/run", "experiments/elsewhere", "tools"):
        (root / directory).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for directory in ("run", "experiments/run"):
        (root / directory / "fit.py").write_text(
            "import pandas as pd\npd.read_csv('in.csv').to_csv('out.csv')\n")
        (root / directory / "settings.yaml").write_text(f"where: {directory}\n")
    (root / "tools" / "tool.py").write_text("print('hi')\n")
    (root / "fit.py").write_text("print('root')\n")
    return root


@pytest.mark.parametrize("before", [
    "export CDPATH=experiments; ",
    "CDPATH=experiments; export CDPATH; ",
    "export CDPATH=experiments && ",
    "source env.sh; ",
    ". ./env.sh; ",
    "eval \"$SETUP\"; ",
    "declare \"$name=experiments\"; ",
])
def test_cd_after_a_possible_cdpath_claims_nothing_relative(
        before: str, cdrepo: Path) -> None:
    records = _run(cdrepo, before + "cd run && python3 fit.py --config settings.yaml")
    assert _claims(records, cdrepo) == []


def test_cdpath_prefix_assignment_on_the_cd_itself(cdrepo: Path) -> None:
    records = _run(cdrepo, "CDPATH=experiments cd run && python3 fit.py --config settings.yaml")
    assert _claims(records, cdrepo) == []


def test_cdpath_in_the_hook_environment(cdrepo: Path) -> None:
    records = _run(cdrepo, "cd run && python3 fit.py --config settings.yaml",
                   env=_env(CDPATH=str(cdrepo / "experiments")))
    assert _claims(records, cdrepo) == []


def test_absolute_script_after_cdpath_cd_keeps_the_event_with_unresolved_config(
        cdrepo: Path) -> None:
    tool = cdrepo / "tools" / "tool.py"
    (record,) = _run(cdrepo, f"export CDPATH=experiments; cd run && python3 {tool} "
                             "--config settings.yaml")
    assert record["script"] == str(tool)
    (config,) = record["step"]["config_files"]
    assert config["hash_status"] == "unresolved"
    assert config["sha256"] is None
    assert config.get("pre_run_proven") is not True


def test_unknown_cwd_is_reported_by_the_cwd_model(cdrepo: Path, monkeypatch) -> None:
    monkeypatch.delenv("CDPATH", raising=False)
    command = "export CDPATH=experiments; cd run && python3 fit.py"
    offset = command.index("python3")
    assert ev._effective_cwd_unknown(command, offset, cdrepo)
    _, modeled = ev._effective_cwd_state(command, offset, cdrepo)
    assert not modeled
    # A subshell's CDPATH cd does not leave the parent's cwd unknown.
    command = "(cd run && true); python3 fit.py"
    assert not ev._effective_cwd_unknown(command, command.index("python3"), cdrepo)


def test_or_fallback_after_cdpath_cd_is_not_proven(cdrepo: Path, monkeypatch) -> None:
    # `elsewhere` exists only under $CDPATH: the cd succeeds there and the
    # fallback never runs, so its failure must not be inferred locally.
    monkeypatch.delenv("CDPATH", raising=False)
    command = "export CDPATH=experiments; cd elsewhere || python3 fit.py"
    assert not ev._or_prefix_is_proven_failed(command, command.index("python3"), cdrepo)
    plain = "cd elsewhere || python3 fit.py"
    assert ev._or_prefix_is_proven_failed(plain, plain.index("python3"), cdrepo)


@pytest.mark.parametrize("command", [
    "cd run && python3 fit.py --config settings.yaml",
    "export CDPATH=experiments; cd ./run && python3 fit.py --config settings.yaml",
    "echo start; cd run && python3 fit.py --config settings.yaml",
])
def test_common_case_still_follows_cd(command: str, cdrepo: Path) -> None:
    records = _run(cdrepo, command)
    assert str(cdrepo / "run" / "settings.yaml") in _claims(records, cdrepo)


def _hashed_configs(records: list[dict]) -> list[str]:
    return [config["path"] for record in records
            for config in (record.get("step") or {}).get("config_files") or []
            if config.get("sha256")]


@pytest.fixture()
def plugin_repo(tmp_path: Path) -> Path:
    """repo/.claude-plugin and repo/.codex-plugin each hold a plugin.json
    and a fit.py; the session starts in .claude-plugin."""
    root = tmp_path / "repo"
    for name in (".claude-plugin", ".codex-plugin", "tools"):
        (root / name).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name in (".claude-plugin", ".codex-plugin"):
        (root / name / "plugin.json").write_text(json.dumps({"where": name}) + "\n")
        (root / name / "fit.py").write_text(
            "import pandas as pd\npd.read_csv('in.csv').to_csv('out.csv')\n")
    (root / "tools" / "tool.py").write_text("print('hi')\n")
    return root


def _run_2(repo: Path, command: str, **env: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo / ".claude-plugin"),
         "--ts", "2026-11-22T10:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--repo-root", str(repo)],
        capture_output=True, text=True, check=True, env=_env(**env))
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


UNMODELED = [
    "pushd ../.codex-plugin >/dev/null && ",
    "pushd ../.codex-plugin; ",
    "builtin pushd ../.codex-plugin && ",
    "command cd ../.codex-plugin && ",
    "cd \"$OTHER\" && ",
    "cd -@ ../.codex-plugin && ",
    ">/dev/null cd ../.codex-plugin && ",
    # A conditional `cd` that may or may not have run.
    "test -d ../.codex-plugin && cd ../.codex-plugin; ",
    "true || cd ../.codex-plugin && ",
    # bash runs a piped `cd` in a subshell, zsh in this shell.
    "echo | cd ../.codex-plugin && ",
]


@pytest.mark.parametrize("before", UNMODELED)
def test_inline_config_after_unmodeled_cd_is_unresolved(before: str, plugin_repo: Path) -> None:
    records = _run_2(plugin_repo, before + "python3 -c 'import json' --config plugin.json",
                   OTHER=str(plugin_repo / ".codex-plugin"))
    assert _hashed_configs(records) == []
    configs = [config for record in records
               for config in (record.get("step") or {}).get("config_files") or []]
    if not before.startswith("true ||"):  # that one's run is unproven: no step argv
        assert len(configs) == 1
    for config in configs:
        assert config["hash_status"] == "unresolved"
        assert config.get("pre_run_proven") is not True


def _scripts(records: list[dict]) -> list[str]:
    return [record["script"] for record in records if record.get("script")]


@pytest.mark.parametrize("before", UNMODELED)
def test_relative_script_after_unmodeled_cd_is_not_claimed(
        before: str, plugin_repo: Path) -> None:
    records = _run_2(plugin_repo, before + "python3 fit.py --config plugin.json",
                   OTHER=str(plugin_repo / ".codex-plugin"))
    assert _scripts(records) == []
    assert _hashed_configs(records) == []
    assert not any(record.get("script_sha256") for record in records)
    if not before.startswith("true ||"):
        # The run is still recorded, under its word alone (no proofs).
        (record,) = records
        assert record["script_candidate"] == "fit.py"
        assert record["io_detection"] == "unresolved"
        assert "working directory unknown" in " ".join(record["lineage_warnings"])


def test_absolute_paths_after_pushd_still_resolve(plugin_repo: Path) -> None:
    tool = plugin_repo / "tools" / "tool.py"
    config = plugin_repo / ".codex-plugin" / "plugin.json"
    (record,) = _run_2(plugin_repo,
                     f"pushd ../.codex-plugin >/dev/null && python3 {tool} --config {config}")
    assert record["script"] == str(tool)
    assert _hashed_configs([record]) == [str(config)]


@pytest.mark.parametrize("command", [
    "cd ../.codex-plugin && python3 fit.py --config plugin.json",
    "cd {repo}/.codex-plugin && python3 fit.py --config plugin.json",
    "cd ../.codex-plugin; python3 fit.py --config plugin.json",
    "true && cd ../.codex-plugin && python3 fit.py --config plugin.json",
    # The text proves these conditional `cd`s ran.
    "true && cd ../.codex-plugin; python3 fit.py --config plugin.json",
    "false || cd ../.codex-plugin; python3 fit.py --config plugin.json",
])
def test_modeled_cd_still_resolves_relative_paths(command: str, plugin_repo: Path) -> None:
    records = _run_2(plugin_repo, command.format(repo=plugin_repo))
    other = plugin_repo / ".codex-plugin"
    assert _scripts(records) == [str(other / "fit.py")]
    assert _hashed_configs(records) == [str(other / "plugin.json")]


def test_cdpath_cd_records_the_relative_script_the_same_way(plugin_repo: Path) -> None:
    (record,) = _run_2(plugin_repo, "export CDPATH=..; cd .codex-plugin && python3 fit.py")
    assert record["script"] is None
    assert record["script_candidate"] == "fit.py"
    assert not record.get("script_sha256")


def test_source_without_cd_keeps_the_relative_script(plugin_repo: Path) -> None:
    records = _run_2(plugin_repo, "source ../.venv/bin/activate && python3 fit.py")
    assert _scripts(records) == [str(plugin_repo / ".claude-plugin" / "fit.py")]


def test_cwd_model_reports_unmodeled_directory_changes(
        plugin_repo: Path, monkeypatch) -> None:
    monkeypatch.delenv("CDPATH", raising=False)
    start = plugin_repo / ".claude-plugin"
    for command in ("pushd ../.codex-plugin && python3 fit.py",
                    "cd - && python3 fit.py",
                    "popd; python3 fit.py"):
        assert ev._effective_cwd_unknown(command, command.index("python3"), start), command
    for command in ("cd ../.codex-plugin && python3 fit.py",
                    "(pushd ../.codex-plugin); python3 fit.py",
                    "false && cd ../missing; python3 fit.py"):
        assert not ev._effective_cwd_unknown(command, command.index("python3"), start), command
    # A subshell's first `cd` runs whenever the subshell does.
    command = "true && (cd ../.codex-plugin; python3 fit.py)"
    offset = command.index("python3")
    assert not ev._effective_cwd_unknown(command, offset, start)
    assert ev._effective_cwd(command, offset, start) == plugin_repo / ".codex-plugin"


def _committed(records: list[dict]) -> str:
    return json.dumps([stop.commit_event(r) for r in records])


@pytest.fixture()
def repo_5(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('in.csv').to_csv('out.csv')\n")
    return root


def _seeds(records: list[dict]) -> list[int]:
    return [seed["value"] for record in records
            for seed in (record.get("step") or {}).get("seeds") or []]


def _run_3(repo_5: Path, command: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo_5),
         "--ts", "2026-11-22T10:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--repo-root", str(repo_5)],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


@pytest.mark.parametrize("command", [
    "python fit.py --password --seed=482731",
    "python fit.py --api-key --seed 482731",
    "python fit.py --token --random-state=482731",
])
def test_a_word_consumed_as_a_secret_is_not_a_seed(repo_5: Path, command: str) -> None:
    records = _run_3(repo_5, command)
    assert records, "the run was not recorded"
    assert 482731 not in _seeds(records)
    if "=482731" in command:
        # The whole word was the secret: the committed record shows none of it.
        assert "482731" not in _committed(records)


def test_a_real_seed_is_still_recorded(repo_5: Path) -> None:
    records = _run_3(repo_5, "python fit.py --password hunter2x --seed=482731")
    assert _seeds(records) == [482731]
    assert "hunter2x" not in _committed(records)


PY = "/opt/env/bin/python3"


@pytest.mark.parametrize("command", [
    # The real assignment is `true`.
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


def _run_4(repo: Path, command: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_profile_resolves_from_the_invocation_directory(workflow: Path) -> None:
    (event,) = _run_4(workflow, "snakemake --profile workflow/profile --directory results")
    (config,) = event["step"]["config_files"]
    assert Path(config["path"]) == (workflow / "workflow" / "profile" / "config.yaml").resolve()
    assert config["sha256"] is not None


def test_configfile_resolves_from_the_invocation_directory(workflow: Path) -> None:
    (event,) = _run_4(workflow, "snakemake --configfile params.yaml --directory results -c1")
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
def repo_6(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / ".living").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "c.yaml").write_text("t: 1\n")
    (root / "run.py").write_text("import pandas as pd\npd.read_csv('data/in.csv')\n")
    return root


def test_a_helper_reached_through_a_parent_pointer_is_mycelium_s_own(tmp_path: Path) -> None:
    # Live run: from analysis/de/reports, `S=$(cat ../../../.mycelium/plugin-root)/
    # skills/core/scripts; python $S/check_report_values.py` was skipped as unreadable.
    plugin = HERE.parent.parent.parent
    repo = tmp_path / "repo"
    sub = repo / "analysis" / "de" / "reports"
    sub.mkdir(parents=True)
    (repo / ".mycelium").mkdir()
    (repo / ".mycelium" / "plugin-root").write_text(str(plugin) + "\n")
    command = ("S=$(cat ../../../.mycelium/plugin-root)/skills/core/scripts\n"
               "python $S/check_report_values.py --manifest x")
    path = ev._variable_script_path(command, command.index("$S/"),
                                    "$S/check_report_values.py", sub)
    assert path == (plugin / "skills" / "core" / "scripts" / "check_report_values.py").resolve()
    assert ev._is_managed_utility_path(path)


@pytest.mark.parametrize("assignment", [
    "R=$(cat .mycelium/plugin-root)/skills/core/scripts",
    'R="$(cat .mycelium/plugin-root)/skills/core/scripts"',
    'R="$(cat .mycelium/plugin-root)"/skills/core/scripts',
])
def test_a_helper_under_a_suffixed_plugin_root_variable_is_mycelium_s_own(
        repo_6: Path, assignment: str) -> None:
    # Live run: `PY=…; R=$(cat .mycelium/plugin-root)/skills/core/scripts;
    # $PY "$R/lineage_index.py"` became a run of an unreadable script.
    plugin = HERE.parent.parent.parent
    (repo_6 / ".mycelium").mkdir()
    (repo_6 / ".mycelium" / "plugin-root").write_text(str(plugin) + "\n")
    command = f'PY={sys.executable}; {assignment}; $PY "$R/lineage_index.py" query'
    path = ev._variable_script_path(command, command.index("$R/"), "$R/lineage_index.py",
                                    repo_6)
    assert path == (plugin / "skills" / "core" / "scripts" / "lineage_index.py").resolve()
    assert ev._is_managed_utility_path(path)


def test_the_event_keeps_the_command_as_written(repo_6: Path) -> None:
    command = f"PY={sys.executable}; $PY run.py --config c.yaml"
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo_6), "--ts", "2026-11-22T00:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo_6)],
        capture_output=True, text=True, check=True)
    (event,) = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert event["bash_cmd"] == command
    assert event["script"].endswith("run.py")
    assert [Path(c["path"]).name for c in event["step"]["config_files"]] == ["c.yaml"]


def test_chained_runners_get_their_own_stages(tmp_path: Path) -> None:
    import extract_data_lineage_event as ev

    (tmp_path / "a.yml").write_text("a: 1\n")
    (tmp_path / "b.yml").write_text("b: 2\n")
    command = ("snakemake -s A.smk --configfile a.yml && echo snakemake-done && "
               "snakemake -s B.smk --configfile b.yml")
    # Three runner words, two runner detections: ambiguous, never guessed.
    assert ev.build_step(command, {"offset": None, "kind": "runner"}, None, tmp_path) == {
        "partial": True, "reason": "runner-stage-ambiguous"}
    clean = "snakemake -s A.smk --configfile a.yml && snakemake -s B.smk --configfile b.yml"
    words = [m.start(1) for m in ev._RUNNER_WORD_RX.finditer(clean)]
    steps = [ev.build_step(clean, {"offset": o, "kind": "runner"}, None, tmp_path) for o in words]
    assert [s["config_files"][0]["path"].endswith(n) for s, n in zip(steps, ("a.yml", "b.yml"))] \
        == [True, True]


def _extract(cwd: Path, command: str) -> str:
    result = subprocess.run(
        [sys.executable, str(HERE / "extract_data_lineage_event.py"), "--cwd", str(cwd),
         "--ts", "2026-11-22T00:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--append-to", os.devnull], capture_output=True, text=True, check=True)
    return result.stdout


def test_helpers_run_through_a_plugin_root_variable_are_managed(tmp_path: Path) -> None:
    plugin = HERE.parent.parent.parent
    (tmp_path / ".mycelium").mkdir()
    (tmp_path / ".mycelium" / "plugin-root").write_text(str(plugin) + "\n")
    for command in (
        "P=$(cat .mycelium/plugin-root); python3 $P/skills/core/scripts/lineage_index.py build",
        'P="$(cat .mycelium/plugin-root)" && python3 "$P/skills/core/scripts/lineage_index.py" build',
        f"P={plugin}; python3 $P/skills/core/scripts/lineage_index.py build",
    ):
        assert _extract(tmp_path, command) == "", command
    # An unresolvable variable is still an unreadable script, reported.
    assert "unreadable_script" in _extract(tmp_path, "python3 $Q/skills/core/scripts/x.py")


def test_a_look_alike_user_script_through_a_variable_is_not_exempt(tmp_path: Path) -> None:
    fake = tmp_path / "fake" / "skills" / "core" / "scripts"
    fake.mkdir(parents=True)
    (fake / "lineage_index.py").write_text("print('mine')\n")
    stdout = _extract(tmp_path, f"P={tmp_path / 'fake'}; python3 $P/skills/core/scripts/lineage_index.py")
    assert "unreadable_script" in stdout  # not silently treated as a Mycelium helper


def _pointer_repo(tmp_path: Path, target: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".mycelium").mkdir(parents=True)
    (repo / ".mycelium" / "plugin-root").write_text(str(target) + "\n")
    return repo


@pytest.mark.parametrize("chain", [
    "S=$P/skills/core/scripts",
    'S="$P/skills/core/scripts"',
    "S=${P}/skills/core/scripts",
    'S="${P}"/skills/core/scripts',
    'S="$P"/skills/core/scripts',
])
def test_a_helper_reached_through_a_variable_chain_is_mycelium_s_own(
        tmp_path: Path, chain: str) -> None:
    # Live run (2026-11-24): `P=$(cat .mycelium/plugin-root); S=$P/skills/core/
    # scripts; python3 $S/data_contract_check.py --help` left a script-unreadable
    # skip per helper, and the LINEAGE GAP at an accepted Stop cost a turn.
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    for helper in ("data_contract_check.py", "register_value.py"):
        command = f"P=$(cat .mycelium/plugin-root); {chain}; python3 $S/{helper} --help"
        assert _extract(repo, command) == "", command


def test_the_live_two_step_chain_with_an_absolute_pointer_is_mycelium_s_own(
        tmp_path: Path) -> None:
    # The live agent read the pointer by its absolute path.
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    pointer = repo / ".mycelium" / "plugin-root"
    command = (f"P=$(cat {pointer}); S=$P/skills/core/scripts; "
               "python3 $S/data_contract_check.py --help; python3 $S/register_value.py --help; "
               "python3 $S/check_report_values.py --help; python3 $S/lineage_index.py --help")
    assert _extract(repo, command) == ""
    # From a subdirectory, an absolute pointer of an ancestor is the same file.
    sub = repo / "analysis" / "de"
    sub.mkdir(parents=True)
    assert _extract(sub, command) == ""


@pytest.mark.parametrize("command", [
    # The chain's root is not the pointer.
    "P=$(cat .mycelium/plugin-root); S=$Q/skills/core/scripts; python3 $S/lineage_index.py",
    "S=$P/skills/core/scripts; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); S=$P/skills/core/scripts; S=$HOME/x; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); P=$HOME; S=$P/skills/core/scripts; python3 $S/lineage_index.py",
    # Traversal out of the managed tree, or an unmodeled expansion.
    "P=$(cat .mycelium/plugin-root); S=$P/../x/skills/core/scripts; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); S=$P$X/skills/core/scripts; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); S=${P/a/b}/skills/core/scripts; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); S='$P/skills/core/scripts'; python3 $S/lineage_index.py",
    # A conditional or piped chain link.
    "P=$(cat .mycelium/plugin-root); false && S=$P/skills/core/scripts; python3 $S/lineage_index.py",
])
def test_a_chain_not_rooted_in_the_pointer_stays_reported(tmp_path: Path, command: str) -> None:
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    assert "unreadable_script" in _extract(repo, command), command


def test_a_chain_rooted_in_a_pointer_to_a_look_alike_tree_stays_reported(tmp_path: Path) -> None:
    fake = tmp_path / "fake"
    (fake / "skills" / "core" / "scripts").mkdir(parents=True)
    (fake / "skills" / "core" / "scripts" / "lineage_index.py").write_text("print(1)\n")
    repo = _pointer_repo(tmp_path, fake)
    command = "P=$(cat .mycelium/plugin-root); S=$P/skills/core/scripts; python3 $S/lineage_index.py"
    assert "lineage_index.py" in _extract(repo, command)


def test_an_absolute_pointer_outside_the_working_tree_stays_reported(tmp_path: Path) -> None:
    # Another repository's pointer: not the one this command's cwd owns.
    other = _pointer_repo(tmp_path / "o", HERE.parent.parent.parent)
    repo = tmp_path / "mine"
    repo.mkdir()
    command = (f"P=$(cat {other / '.mycelium' / 'plugin-root'}); "
               "python3 $P/skills/core/scripts/lineage_index.py")
    assert "unreadable_script" in _extract(repo, command)


@pytest.mark.parametrize("command", [
    "P=$(cat .mycelium/plugin-root) && S=$P/skills/core/scripts && python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root)\nS=$P/skills/core/scripts\npython3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); R=$P/skills; S=$R/core/scripts; python3 $S/lineage_index.py",
])
def test_unconditional_chain_links_are_followed(tmp_path: Path, command: str) -> None:
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    assert _extract(repo, command) == "", command


@pytest.mark.parametrize("command", [
    "P=$(cat .mycelium/plugin-root); true || S=$P/skills/core/scripts; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); (S=$P/skills/core/scripts); python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); echo | S=$P/skills/core/scripts; python3 $S/lineage_index.py",
    "P=$(cat .mycelium/plugin-root); cd x && S=$P/skills/core/scripts; python3 $S/lineage_index.py",
])
def test_conditional_chain_links_stay_reported(tmp_path: Path, command: str) -> None:
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    assert "unreadable_script" in _extract(repo, command), command


@pytest.mark.parametrize("reader", [
    '$(cat "$PWD/.mycelium/plugin-root")',
    "$(cat $PWD/.mycelium/plugin-root)",
    '$(cat "${PWD}/.mycelium/plugin-root")',
    '$(cat ".mycelium/plugin-root")',
    '"$(cat "$PWD/.mycelium/plugin-root")"',
])
def test_the_pointer_read_through_pwd_or_quoted_is_mycelium_s_own(
        tmp_path: Path, reader: str) -> None:
    # Live run (2026-11-24, this branch): `P=$(cat "$PWD/.mycelium/plugin-root");
    # S=$P/skills/core/scripts; python3 $S/x.py --help` still left four skips.
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    command = f"P={reader}; S=$P/skills/core/scripts; python3 $S/lineage_index.py --help"
    assert _extract(repo, command) == "", command


@pytest.mark.parametrize("reader", [
    "$(cat '$PWD/.mycelium/plugin-root')",      # literal `$PWD` directory
    '$(cat "$HOME/.mycelium/plugin-root")',     # another directory's pointer
    '$(cat "$PWD/../.mycelium/plugin-root"x)',  # not a whole word
])
def test_other_pointer_spellings_stay_reported(tmp_path: Path, reader: str) -> None:
    repo = _pointer_repo(tmp_path, HERE.parent.parent.parent)
    command = f"P={reader}; S=$P/skills/core/scripts; python3 $S/lineage_index.py --help"
    assert "unreadable_script" in _extract(repo, command), command
