"""Regression tests for four P1 findings of the third Codex review.

1. A URL literal (`pd.read_csv("https://user:pass@host/x.csv")`) is never
   recorded as a local path, so its userinfo never reaches `inputs[].path`
   or `rel_path`; the same for outputs, data-command redirections, and
   config operands (kept only as a remote reference).
2. A bare `~` in a config operand is resolved with the hook's HOME only
   when nothing before the stage may have changed HOME.
3. A 0.9.7 pairing listing one level keeps its 0.9.7 behavior; only
   `min_levels` sets a minimum number of distinct levels.
4. `--emit-contract` runs the checks first and never replaces an existing
   destination without `--overwrite`; a missing sample table writes nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import data_contract_check as dcc  # noqa: E402
import extract_data_lineage as stop  # noqa: E402
import extract_data_lineage_event as ev  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"
CHECKER = HERE / "data_contract_check.py"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out.csv')\n")
    return root


def run(repo: Path, command: str, env: dict | None = None) -> tuple[list[dict], str]:
    """(records, the records as a committed manifest holds them, as text)."""
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--repo-root", str(repo)],
        capture_output=True, text=True, check=True, env=env)
    records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return records, json.dumps([
        (stop.commit_data_command if r.get("kind") == "data_command" else stop.commit_event)(r)
        for r in records])


def _file_paths(record: dict) -> list[str]:
    values = []
    for key in ("inputs", "outputs"):
        for rec in record.get(key) or []:
            values += [rec.get("path"), rec.get("rel_path")]
    for rec in (record.get("step") or {}).get("config_files") or []:
        values += [rec.get("path"), rec.get("rel_path")]
    return [v for v in values if isinstance(v, str)]


# 1. URLs are never local paths ---------------------------------------------

def test_url_input_password_never_reaches_inputs(repo: Path) -> None:
    (repo / "fit.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv(\"https://user:TEST_SECRET@example.invalid/data.csv\")\n"
        "df.to_csv('out.csv')\n")
    (record,), stdout = run(repo, "python fit.py")
    assert "TEST_SECRET" not in stdout
    paths = _file_paths(record)
    assert not any(ev._is_url(p) for p in paths), paths
    assert [Path(rec["path"]).name for rec in record["outputs"]] == ["out.csv"]
    assert record["inputs"] == []
    assert any("Remote URLs" in w for w in record["lineage_warnings"])


def test_url_output_password_never_reaches_outputs(repo: Path) -> None:
    (repo / "fit.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv('data/in.csv')\n"
        "df.to_csv(\"s3://bob:OUT_SECRET@bucket/out.csv\")\n")
    (record,), stdout = run(repo, "python fit.py")
    assert "OUT_SECRET" not in stdout
    assert record["outputs"] == []
    assert [Path(rec["path"]).name for rec in record["inputs"]] == ["in.csv"]


@pytest.mark.parametrize("command", [
    "cat data/in.csv > s3://u:SEC3@b/o.csv",
    "cat < s3://u:SEC3@b/i.csv > data/out.csv",
    "cp data/in.csv s3://u:SEC3@b/o.csv",
    "cp https://u:SEC3@h/a.csv data/b.csv",
])
def test_shell_io_never_records_a_url_shaped_word(command: str, repo: Path) -> None:
    records, stdout = run(repo, command)
    assert "SEC3" not in stdout
    for record in records:
        assert not any("s3:" in p for p in _file_paths(record))


def test_url_config_operand_is_a_remote_reference(repo: Path) -> None:
    step = ev.build_step("python fit.py --config https://tok@h.invalid/c.yaml?key=Q1",
                         {"offset": 0, "kind": "script", "script_word": "fit.py"}, None, repo)
    (config,) = step["config_files"]
    assert config["hash_status"] == "remote"
    assert config["sha256"] is None
    # No evidence: a committed record keeps none of it.
    (committed,) = stop._commit_config_files([config])
    assert committed["path"] == "<v>"


def test_url_config_never_framed_as_a_repository_path(repo: Path) -> None:
    (record,), committed = run(repo, "python fit.py --config https://tok@h.invalid/c.yaml")
    (config,) = record["step"]["config_files"]
    assert config["rel_path"] is None
    assert "tok@" not in committed


def test_url_shapes() -> None:
    assert ev._is_url("s3:/b/x.csv") and not ev._is_url("C:/data/x.csv")
    assert not ev._is_url("data/x.csv")


# 2. A bare `~` follows the command's HOME ------------------------------------

@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "openai.yaml").write_text("model: a\n")
    monkeypatch.setenv("HOME", str(home))
    return home


def _config(command: str, repo: Path) -> dict:
    offset = command.index("python fit.py")
    step = ev.build_step(command, {"offset": offset, "kind": "script", "script_word": "fit.py"},
                         None, repo)
    (config,) = step["config_files"]
    return config


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
        before: str, repo: Path, home: Path) -> None:
    config = _config(before + "python fit.py --config ~/agents/openai.yaml", repo)
    assert config["hash_status"] == "unresolved"
    assert config["sha256"] is None
    assert "pre_run_proven" not in config


@pytest.mark.parametrize("before", ["", "echo $HOME; ", "cd data && cd .. && ",
                                    "echo ${HOME}; "])
def test_tilde_still_resolves_when_home_is_untouched(before: str, repo: Path,
                                                     home: Path) -> None:
    config = _config(before + "python fit.py --config ~/agents/openai.yaml", repo)
    assert config["path"] == str(home / "agents" / "openai.yaml")
    assert config["sha256"] is not None


def test_home_assignment_end_to_end(repo: Path, home: Path) -> None:
    env = {**os.environ, "HOME": str(home)}
    (record,), _ = run(repo, "HOME=/repo/alternate; python fit.py --config ~/agents/openai.yaml",
                       env=env)
    (config,) = record["step"]["config_files"]
    assert config["hash_status"] == "unresolved"
    assert config.get("pre_run_proven") is not True


# 3. A one-level pairing keeps its 0.9.7 behavior -------------------------------

def _pairing(tmp_path: Path, rows: str, **check) -> list:
    table = tmp_path / "t.tsv"
    table.write_text("donor\tvisit\n" + rows)
    contract = {"schema": dcc.SCHEMA, "table": str(table),
                "checks": [{"kind": "pairing", "unit": "donor", "within": "visit", **check}]}
    return dcc.run_contract(contract, str(tmp_path))[1]


def _blocks(alerts: list) -> bool:
    return any(a["blocking"] for a in alerts)


def test_one_listed_level_passes_as_on_097(tmp_path: Path) -> None:
    assert not _blocks(_pairing(tmp_path, "d1\tbaseline\nd2\tbaseline\n", levels=["baseline"]))


def test_one_listed_level_with_min_levels_blocks(tmp_path: Path) -> None:
    alerts = _pairing(tmp_path, "d1\tbaseline\nd2\tbaseline\n", levels=["baseline"], min_levels=2)
    assert _blocks(alerts)


def test_min_levels_counts_distinct_levels(tmp_path: Path) -> None:
    alerts = _pairing(tmp_path, "d1\tA\nd2\tA\n", levels=["A", "A"], min_levels=2)
    assert _blocks(alerts)
    alerts = _pairing(tmp_path, "d1\tA\nd1\tB\nd2\tA\nd2\tB\n", levels=["A", "B"], min_levels=2)
    assert not _blocks(alerts)


def test_one_listed_level_cli_exit_zero(tmp_path: Path) -> None:
    table = tmp_path / "t.tsv"
    table.write_text("donor\tvisit\nd1\tbaseline\nd2\tbaseline\n")
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"schema": dcc.SCHEMA, "table": "t.tsv", "checks": [
        {"kind": "pairing", "unit": "donor", "within": "visit", "levels": ["baseline"]}]}))
    assert dcc.main(["--contract", str(contract), "--root", str(tmp_path)]) == 0


# 4. --emit-contract publishes only after the checks, never over a file ------

@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    templates = HERE.parent / "templates"
    shutil.copy(templates / "samples.tsv", tmp_path / "samples.tsv")
    spec = json.loads((templates / "samples.ledger.json").read_text())
    spec.pop("note", None)
    (tmp_path / "samples.ledger.json").write_text(json.dumps(spec))
    return tmp_path


def _emit(ledger: Path, dest: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--ledger", str(ledger / "samples.ledger.json"),
         "--root", str(ledger), "--emit-contract", str(dest), *extra],
        capture_output=True, text=True)


def _no_temp_files(directory: Path) -> bool:
    return not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


def test_missing_table_never_replaces_an_existing_contract(ledger: Path) -> None:
    dest = ledger / "data_contract.json"
    dest.write_text('{"extended": true}\n')
    (ledger / "samples.tsv").unlink()
    result = _emit(ledger, dest)
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert dest.read_text() == '{"extended": true}\n'
    result = _emit(ledger, dest, "--overwrite")
    assert result.returncode == 2
    assert dest.read_text() == '{"extended": true}\n'
    assert _no_temp_files(ledger)


def test_missing_table_writes_no_new_contract(ledger: Path) -> None:
    dest = ledger / "data_contract.json"
    (ledger / "samples.tsv").unlink()
    result = _emit(ledger, dest)
    assert result.returncode == 2
    assert not dest.exists()
    assert "not written" in result.stderr


def test_existing_destination_needs_overwrite(ledger: Path) -> None:
    dest = ledger / "data_contract.json"
    dest.write_text('{"extended": true}\n')
    result = _emit(ledger, dest)
    assert result.returncode == 1
    assert dest.read_text() == '{"extended": true}\n'
    result = _emit(ledger, dest, "--overwrite")
    assert result.returncode == 0, result.stdout + result.stderr
    dcc.validate_contract(json.loads(dest.read_text()))


def test_dangling_symlink_destination_counts_as_existing(ledger: Path) -> None:
    dest = ledger / "data_contract.json"
    dest.symlink_to(ledger / "nowhere.json")
    assert _emit(ledger, dest).returncode == 1
    assert dest.is_symlink() and not (ledger / "nowhere.json").exists()


def test_new_destination_is_written_after_the_checks(ledger: Path) -> None:
    dest = ledger / "data_contract.json"
    result = _emit(ledger, dest)
    assert result.returncode == 0, result.stdout + result.stderr
    dcc.validate_contract(json.loads(dest.read_text()))


def test_overwrite_needs_emit_contract(ledger: Path) -> None:
    code = dcc.main(["--ledger", str(ledger / "samples.ledger.json"), "--overwrite"])
    assert code == 1


def test_no_clobber_publish_refuses_a_file_created_meanwhile(tmp_path: Path) -> None:
    dest = tmp_path / "c.json"
    dest.write_text("theirs\n")
    with pytest.raises(dcc.ContractError):
        dcc.write_atomic_json(str(dest), {"a": 1}, replace=False)
    assert dest.read_text() == "theirs\n"
    assert _no_temp_files(tmp_path)
    fresh = tmp_path / "new.json"
    dcc.write_atomic_json(str(fresh), {"a": 1}, replace=False)
    assert json.loads(fresh.read_text()) == {"a": 1}
    assert _no_temp_files(tmp_path)
