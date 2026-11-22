"""Regressions for the first post-PR Codex review round (PR #154)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_report_values as crv  # noqa: E402
import data_contract_check as dcc  # noqa: E402
import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import lineage_redact  # noqa: E402
import test_lineage_index as tli  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# -- redaction ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "secret"),
    [
        ('tool --token pre"post secret" next', "secret"),
        (r"TOKEN=abc\ def run", "def"),
        ("SESSION_ID=s3ssi0n run", "s3ssi0n"),
        ("tool --api-key='a b c' x", "a b c"),
    ],
)
def test_whole_shell_words_are_redacted(command: str, secret: str) -> None:
    assert secret not in lineage_redact.redact_text(command)


@pytest.mark.parametrize(
    "source",
    ['token <- "abc"\n', 'password = """multi\nline"""\n', 'api_key = "a\\"b\\"c"\n'],
)
def test_source_literals_of_any_shape_are_redacted(source: str) -> None:
    out = lineage_redact.redact_source(source)
    assert "<redacted>" in out
    for fragment in ("abc", "multi", "line", 'a\\"b'):
        if fragment in source:
            assert fragment not in out


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / ".living").mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text("import pandas as pd\npd.read_csv('data/in.csv')\n")
    return root


def run(repo: Path, command: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T00:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo), *extra],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_a_secret_value_never_becomes_a_config_path_or_data_path(repo: Path) -> None:
    (repo / "s3cret.yaml").write_text("x: 1\n")
    (event,) = run(repo, "python fit.py --token s3cret.yaml --config data/in.csv")
    assert "s3cret" not in json.dumps(event)
    (command,) = run(repo, "cat data/in.csv --password hunter2.csv")
    assert "hunter2" not in json.dumps(command)


def test_a_large_script_with_a_secret_gets_a_sanitized_digest(repo: Path) -> None:
    body = "password = 'example-secret'\n" + "# pad\n" * 30000 + \
        "import pandas as pd\npd.read_csv('data/in.csv')\n"
    (repo / "big.py").write_text(body)
    (event,) = run(repo, "python big.py")
    assert event["script_source"] is None
    assert event["script_sha256"] != hashlib.sha256(body.encode()).hexdigest()
    assert event["script_sha256_of"] == "sanitized"


# -- step settings ---------------------------------------------------------------


def test_inline_bodies_stop_at_the_interpreter_boundary(repo: Path) -> None:
    (event,) = run(repo, "python -c 'import pandas as pd; pd.read_csv(\"data/in.csv\")' "
                         "--seed 7 -c app-value")
    step = event["step"]
    assert step["script_args"] == ["--seed", "7", "-c", "app-value"]
    assert [s["value"] for s in step["seeds"]] == [7]


def test_an_oversized_seed_keeps_the_event(repo: Path) -> None:
    (event,) = run(repo, "python fit.py --seed " + "9" * 4400)
    assert "seeds" not in event["step"]


def test_a_comment_line_is_not_the_data_stage(repo: Path) -> None:
    (event,) = run(repo, "# inspect data/in.csv\nhead data/in.csv")
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


def test_an_unproven_config_says_so(repo: Path) -> None:
    (repo / "c.yaml").write_text("a: 1\n")
    (event,) = run(repo, "python fit.py --config c.yaml")
    (config,) = event["step"]["config_files"]
    assert config["pre_run_proven"] is False and config["effective"] == "unknown"


# -- observer and consolidation ---------------------------------------------------


def test_a_textual_patch_failure_is_a_failure() -> None:
    assert io.edit_outcome("Error: invalid patch context") == "failure"
    assert io.edit_outcome("Success. Updated the following files:\nM a.py") == "success"
    assert io.edit_outcome("") == "unknown"


def test_the_same_call_id_in_two_sessions_consolidates_separately(tmp_path: Path) -> None:
    obs_dir = tmp_path / "obs"
    for session in ("s1", "s2"):
        path = obs_dir / "claude" / f"{session}.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kind": "observation", "observation_id": "claude:t1",
                                    "host_session_id": session, "status": "no-known-overlap",
                                    "changes": {"created": [], "modified": [], "deleted": []}})
                        + "\n")
    records = [{"observation_id": "claude:t1", "observation_session": "s1", "outputs": []},
               {"observation_id": "claude:t1", "observation_session": "s2", "outputs": []}]
    observations, missing, _ = stop.join_observations(records, obs_dir, str(tmp_path), [])
    assert len(observations) == 2 and missing == []


def test_an_output_whose_coverage_changed_is_not_unchanged(tmp_path: Path) -> None:
    record = {"observation_id": "claude:t1",
              "outputs": [{"path": str(tmp_path / "data" / "x.csv"), "rel_path": "data/x.csv"}]}
    observation = {"observation_id": "claude:t1", "status": "no-known-overlap",
                   "scope": {"unwatched": []},
                   "changes": {"created": [], "modified": [], "deleted": [],
                               "coverage_changed": ["data/x.csv"]}}
    stop._mark_observed_changes([record], {"claude:t1": observation}, str(tmp_path))
    assert record["outputs"][0]["observed_change"] == "unknown-coverage"


# -- index -----------------------------------------------------------------------


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def test_a_superseded_write_reports_gaps_without_losing_the_verdict(project: Path) -> None:
    gap = tli.observation(project, "c2", pre=3, post=4, status="unavailable", changes=None)
    tli.write_store(project, [
        tli.observation(project, "c1", created=[("out/x", sha("A"))], pre=1, post=2), gap,
        tli.observation(project, "c3", modified=[("out/x", sha("B"))], pre=5, post=6)])
    write = li.query_hash(li.load(project), sha("A"), disk=False)["paths"][0]["writes"][0]
    assert write["record"]["status"] == "superseded"
    assert write["record"]["gap_count"] >= 1


def test_an_unreconciled_skip_is_a_gap_and_a_reconciled_one_is_not(project: Path) -> None:
    (project / "out" / "x").write_text("x")
    tli.write_store(project, [tli.observation(project, "c1", created=[("out/x", sha("x"))],
                                              pre=1, post=2)])
    tli.write_manifest(project, "m1", skipped=[
        {"skip_reason": "detector-failed", "observation_id": "claude:c1",
         "host_session_id": "s1"}])
    assert li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]["status"] == \
        "current"
    tli.write_manifest(project, "m2", skipped=[{"skip_reason": "extractor-missing"}])
    write = li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_an_expired_background_job_stays_a_gap(project: Path) -> None:
    (project / "out" / "x").write_text("x")
    background = tli.observation(project, "bg", pre=3, post=9, status="background",
                                 expired=True)
    tli.write_store(project, [tli.observation(project, "c1", created=[("out/x", sha("x"))],
                                              pre=1, post=2), background])
    write = li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_open_calls_are_reported_not_counted(project: Path) -> None:
    (project / "out" / "x").write_text("x")
    tli.write_store(project, [tli.observation(project, "c1", created=[("out/x", sha("x"))],
                                              pre=1, post=2)])
    io.journal_event(project / ".mycelium", "pre", "running", host="claude", session="s9",
                     tool="Bash")
    write = li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] in ("current", "unknown")
    assert write["record"].get("open_calls")


def test_a_foreign_v1_manifest_adds_no_local_writes(project: Path) -> None:
    (project / "out" / "x").write_text("mine")
    tli.write_store(project, [tli.observation(project, "c1", created=[("out/x", sha("mine"))],
                                              pre=1, post=2)])
    theirs = {"session_id": "t", "repo_root": "/home/someone/proj", "actions": [
        {"ts": "2026-10-01T00:00:00Z", "inputs": [],
         "outputs": [{"path": "/home/someone/proj/out/x", "sha256": sha("theirs")}]}]}
    (project / ".living" / "log" / "data-lineage" / "t.json").write_text(json.dumps(theirs))
    write = li.query_hash(li.load(project), sha("mine"))["paths"][0]["writes"][0]
    assert write["status"] == "current"


def test_a_linked_cache_directory_is_never_written(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, project / ".mycelium" / "lineage")
    tli.write_manifest(project, "m1")
    li.load(project)
    assert list(outside.iterdir()) == []


def test_corrupt_store_bytes_are_a_gap(project: Path) -> None:
    path = io.observations_path(project / ".mycelium", "claude", "s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"kind": "observation", "x": "\xff\xfe"}\n')
    coverage = li.query_path(li.load(project), "out/x", disk=False)["coverage"]["gaps"]
    assert coverage.get("store-record-unreadable") == 1


# -- report values and ledger ------------------------------------------------------


def test_source_and_sources_together_are_invalid(tmp_path: Path) -> None:
    entry = {"id": "n", "value": 1, "source": {"path": "a", "sha256": "a" * 64, "locator": "x"},
             "sources": [{"path": "b", "sha256": "b" * 64, "locator": "y"}]}
    (finding,) = crv.check_numbers([entry], tmp_path)
    assert finding["code"] == "source-invalid"


def test_explain_names_only_the_writer_of_the_current_bytes(project: Path) -> None:
    (project / "out" / "x").write_text("edited by hand")
    tli.write_store(project, [tli.observation(project, "c1", created=[("out/x", sha("v1"))],
                                              pre=1, post=2)])
    index = li.load(project, write=False)
    explanation = crv._explain(index, "out/x", sha("edited by hand"))
    assert explanation["status"] == "current-bytes-not-in-record"


def test_joint_batches_that_lose_a_group_block(tmp_path: Path) -> None:
    table = tmp_path / "t.tsv"
    table.write_text("u\tg\tdate\trun\nu1\tA\td1\t\nu2\tA\t\tr1\nu3\tB\td1\tr1\nu4\tB\td2\tr2\n")
    contract = {"schema": dcc.SCHEMA, "table": str(table), "checks": [
        {"kind": "batch_estimability", "batch": ["date", "run"], "contrast": "g"}]}
    summaries, _ = dcc.run_contract(contract, str(tmp_path))
    assert summaries[0]["status"] == "block"


def test_joint_batches_respect_local_label_scope(tmp_path: Path) -> None:
    table = tmp_path / "t.tsv"
    table.write_text("site\tline\tdate\tg\nS1\tL1\tD1\tA\nS1\tL1\tD2\tB\nS2\tL1\tD1\tB\n")
    contract = {"schema": dcc.SCHEMA, "table": str(table), "checks": [
        {"kind": "batch_estimability", "batch": ["line", "date"], "contrast": "g",
         "batch_scopes": {"line": ["site"]}}]}
    summaries, _ = dcc.run_contract(contract, str(tmp_path))
    assert summaries[0]["status"] == "block"


def test_pairing_exact_levels_hold_on_the_checked_table(tmp_path: Path) -> None:
    table = tmp_path / "m.tsv"
    table.write_text("donor\tg\nd1\tA\nd1\tB\nd2\tA\nd2\tB\n")  # the modeled table lost C
    contract = {"schema": dcc.SCHEMA, "table": str(table), "checks": [
        {"kind": "pairing", "unit": "donor", "within": "g", "levels": ["A", "B", "C"],
         "exact_levels": True}]}
    summaries, _ = dcc.run_contract(contract, str(tmp_path))
    assert summaries[0]["status"] == "block"
