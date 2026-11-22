"""Regression tests for three P1 findings of a later Codex review.

1. `--emit-contract --overwrite` resolves the ledger's table, the ledger, and
   the destination the way the OS opens them (symlinks before `..`), so a
   table named `latest/../samples.tsv` through a symlinked `latest` is the
   file read, checked, alias-guarded and emitted; it is never overwritten.
2. An unqualified relative `cd` operand is searched in `$CDPATH` first: when
   CDPATH may be set (hook environment, a prefix, or anything earlier in the
   command), the cwd is unknown, so no relative script, config or I/O is
   claimed against the guessed directory.
3. argv / script_args / data-command argv are redacted whole before each
   word is clipped to 4 KiB, and a clipped word that looked secret keeps no
   partial literal.
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
import extract_data_lineage_event as ev  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"


# 1. --emit-contract resolves symlinks before `..` ----------------------------

@pytest.fixture()
def linked(tmp_path: Path) -> Path:
    """root/ledger/latest -> root/snapshot/staging; the ledger names its
    table `latest/../samples.tsv`, which the OS opens as
    root/snapshot/samples.tsv. A decoy root/ledger/samples.tsv is what a
    lexical collapse of `..` would name instead."""
    root = tmp_path / "root"
    templates = HERE.parent / "templates"
    (root / "ledger").mkdir(parents=True)
    (root / "snapshot" / "staging").mkdir(parents=True)
    (root / "ledger" / "latest").symlink_to(root / "snapshot" / "staging")
    shutil.copy(templates / "samples.tsv", root / "snapshot" / "samples.tsv")
    shutil.copy(templates / "samples.tsv", root / "ledger" / "samples.tsv")
    spec = json.loads((templates / "samples.ledger.json").read_text())
    spec.pop("note", None)
    spec["file"] = "latest/../samples.tsv"
    (root / "ledger" / "samples.ledger.json").write_text(json.dumps(spec))
    return root


def _emit(root: Path, dest: Path, *extra: str) -> int:
    return dcc.main(["--ledger", str(root / "ledger" / "samples.ledger.json"),
                     "--root", str(root), "--emit-contract", str(dest), *extra])


def test_overwrite_never_destroys_the_table_read_through_a_symlink(linked: Path) -> None:
    table = linked / "snapshot" / "samples.tsv"
    before = table.read_bytes()
    assert _emit(linked, table, "--overwrite") == 1
    assert table.read_bytes() == before


def test_overwrite_refuses_the_table_by_its_dotdot_spelling(linked: Path) -> None:
    table = linked / "snapshot" / "samples.tsv"
    before = table.read_bytes()
    dest = linked / "ledger" / "latest" / ".." / "samples.tsv"
    assert _emit(linked, dest, "--overwrite") == 1
    assert table.read_bytes() == before


def test_emitted_contract_names_the_table_that_was_read(linked: Path) -> None:
    dest = linked / "data_contract.json"
    assert _emit(linked, dest) == 0
    emitted = json.loads(dest.read_text())
    assert emitted["table"] == os.path.join("snapshot", "samples.tsv")
    # The contract replays against the same file.
    assert dcc.main(["--contract", str(dest), "--root", str(linked)]) == 0


def test_checks_read_the_resolved_table(linked: Path, capsys) -> None:
    # Break the decoy: a lexical reading would now fail the schema check.
    (linked / "ledger" / "samples.tsv").write_text("nothing\n1\n")
    assert dcc.main(["--ledger", str(linked / "ledger" / "samples.ledger.json"),
                     "--root", str(linked), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert {s["table"] for s in out["checks"]} == {
        os.path.realpath(linked / "snapshot" / "samples.tsv")}


def test_ledger_spec_path_resolves_symlinks_before_dotdot(linked: Path) -> None:
    # The ledger itself named through `latest/..`: its directory is
    # root/snapshot, so a relative table `samples.tsv` is snapshot's.
    spec = json.loads((linked / "ledger" / "samples.ledger.json").read_text())
    spec["file"] = "samples.tsv"
    (linked / "snapshot" / "samples.ledger.json").write_text(json.dumps(spec))
    (linked / "ledger" / "samples.tsv").write_text("nothing\n1\n")
    via = linked / "ledger" / "latest" / ".." / "samples.ledger.json"
    dest = linked / "data_contract.json"
    assert dcc.main(["--ledger", str(via), "--root", str(linked),
                     "--emit-contract", str(dest)]) == 0
    assert json.loads(dest.read_text())["table"] == os.path.join("snapshot", "samples.tsv")
    table = linked / "snapshot" / "samples.tsv"
    before = table.read_bytes()
    assert dcc.main(["--ledger", str(via), "--root", str(linked),
                     "--emit-contract", str(table), "--overwrite"]) == 1
    assert table.read_bytes() == before


def test_destination_through_a_symlinked_dotdot_lands_where_the_os_puts_it(
        linked: Path) -> None:
    (linked / "out").mkdir()
    (linked / "out" / "latest").symlink_to(linked / "snapshot" / "staging")
    dest = linked / "out" / "latest" / ".." / "c.json"
    assert _emit(linked, dest) == 0
    assert (linked / "snapshot" / "c.json").is_file()
    assert not (linked / "out" / "c.json").exists()
    for directory in (linked / "out", linked / "snapshot"):
        assert not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


# 2. CDPATH makes an unqualified `cd` target unknown ------------------------

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


# 3. Redact whole arguments, then clip -------------------------------------

LONG = "A" * 6000


def _step(command: str, word: str = "fit.py") -> dict:
    offset = command.index("python")
    return ev.build_step(command, {"offset": offset, "kind": "script", "script_word": word},
                         None, None)


def test_long_json_secret_is_redacted_before_the_clip() -> None:
    arg = '{"api_key": "SECRETPREFIX' + LONG + '"}'
    step = _step(f"python fit.py --params '{arg}'")
    text = json.dumps(step)
    assert "SECRETPREFIX" not in text
    assert step["argv"][-1] == '{"api_key": "<redacted>"}'
    assert step["script_args"][-1] == '{"api_key": "<redacted>"}'


def test_unterminated_secret_literal_leaves_no_partial_value() -> None:
    arg = '{"api_key": "SECRETPREFIX' + LONG
    step = _step(f"python fit.py --params '{arg}'")
    assert "SECRETPREFIX" not in json.dumps(step)
    assert step["truncated"] is True
    assert step["argv"][-1].endswith("…[truncated]")
    assert step["script_args"][-1].endswith("…[truncated]")


def test_secret_after_a_redacted_one_in_a_long_word() -> None:
    arg = '{"token": "S3CRET", "password": "PW_PREFIX' + LONG
    step = _step(f"python fit.py '{arg}'")
    text = json.dumps(step)
    assert "S3CRET" not in text and "PW_PREFIX" not in text


def test_long_plain_word_is_still_clipped_verbatim() -> None:
    step = _step(f"python fit.py --blob {LONG}")
    assert step["argv"][-1] == "A" * ev.STEP_TOKEN_MAX + "…[truncated]"
    assert step["truncated"] is True


def test_data_command_argv_redacts_before_the_clip() -> None:
    arg = '{"api_key": "SECRETPREFIX' + LONG + '"}'
    step = ev.data_command_step(f"curl -d '{arg}' -o data/out.csv https://example.invalid/x")
    assert "SECRETPREFIX" not in json.dumps(step)
    unterminated = '{"api_key": "SECRETPREFIX' + LONG
    step = ev.data_command_step(f"curl -d '{unterminated}' -o data/out.csv")
    assert "SECRETPREFIX" not in json.dumps(step)


def test_snakemake_param_unterminated_secret_leaves_no_partial_value() -> None:
    values = ev._snakemake_params(["--config", 'blob={"api_key": "SECRETPREFIX' + LONG])
    assert "SECRETPREFIX" not in json.dumps(values)


def test_extractor_end_to_end_never_records_the_secret_prefix(cdrepo: Path) -> None:
    arg = '{"api_key": "SECRETPREFIX' + LONG + '"}'
    records = _run(cdrepo, f"python3 tools/tool.py --params '{arg}'")
    assert records
    for record in records:
        step = record["step"]
        assert "SECRETPREFIX" not in json.dumps(step)
