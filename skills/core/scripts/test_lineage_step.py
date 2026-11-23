"""Each recorded step carries the settings it actually ran with.

Item 4 of the provenance work: argv, interpreter, script arguments,
environment assignments, config files (proven against the Pre snapshot),
and literal seeds, read from the matched stage itself; one redaction policy
for every place command text is stored; `filters_detected` gone.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402

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
