"""Regression tests for three P1 findings of a later Codex review.

1. Any directory change the cwd walk cannot apply (`pushd`/`popd`, `cd -`,
   `cd $DIR`, a flagged or launcher-prefixed `cd`, a conditional `cd` the
   walk skips) leaves the cwd unknown, like a CDPATH-searched `cd`: no
   relative script, config or I/O is resolved against the original
   directory. Absolute paths still resolve, and modeled `cd`s still apply.
2. `write_atomic_json(..., replace=False)` on a filesystem without hard
   links never `os.replace`s onto the destination: it creates the file
   exclusively, so a file published by another process is never clobbered.
3. Without an explicit `min_levels`, a pairing behaves as on 0.9.7 for any
   `levels` list, including one that repeats a level.
"""
from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import data_contract_check as dcc  # noqa: E402
import extract_data_lineage_event as ev  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"


# 1. An unmodeled directory change leaves the cwd unknown --------------------

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


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CDPATH"}
    env.update(extra)
    return env


def _run(repo: Path, command: str, **env: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo / ".claude-plugin"),
         "--ts", "2026-11-22T10:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--repo-root", str(repo)],
        capture_output=True, text=True, check=True, env=_env(**env))
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _hashed_configs(records: list[dict]) -> list[str]:
    return [config["path"] for record in records
            for config in (record.get("step") or {}).get("config_files") or []
            if config.get("sha256")]


def _scripts(records: list[dict]) -> list[str]:
    return [record["script"] for record in records if record.get("script")]


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
    records = _run(plugin_repo, before + "python3 -c 'import json' --config plugin.json",
                   OTHER=str(plugin_repo / ".codex-plugin"))
    assert _hashed_configs(records) == []
    configs = [config for record in records
               for config in (record.get("step") or {}).get("config_files") or []]
    if not before.startswith("true ||"):  # that one's run is unproven: no step argv
        assert len(configs) == 1
    for config in configs:
        assert config["hash_status"] == "unresolved"
        assert config.get("pre_run_proven") is not True


@pytest.mark.parametrize("before", UNMODELED)
def test_relative_script_after_unmodeled_cd_is_not_claimed(
        before: str, plugin_repo: Path) -> None:
    records = _run(plugin_repo, before + "python3 fit.py --config plugin.json",
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
    (record,) = _run(plugin_repo,
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
    records = _run(plugin_repo, command.format(repo=plugin_repo))
    other = plugin_repo / ".codex-plugin"
    assert _scripts(records) == [str(other / "fit.py")]
    assert _hashed_configs(records) == [str(other / "plugin.json")]


def test_cdpath_cd_records_the_relative_script_the_same_way(plugin_repo: Path) -> None:
    (record,) = _run(plugin_repo, "export CDPATH=..; cd .codex-plugin && python3 fit.py")
    assert record["script"] is None
    assert record["script_candidate"] == "fit.py"
    assert not record.get("script_sha256")


def test_source_without_cd_keeps_the_relative_script(plugin_repo: Path) -> None:
    records = _run(plugin_repo, "source ../.venv/bin/activate && python3 fit.py")
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


# 2. No-replace publication without hard links -------------------------------

def _no_hard_links(monkeypatch, before_failing=None) -> None:
    def link(src, dst, *args, **kwargs):
        if before_failing is not None:
            before_failing(dst)
        raise OSError(errno.EPERM, "hard links not supported")
    monkeypatch.setattr(dcc.os, "link", link)


def test_fallback_never_replaces_onto_the_destination(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "contract.json"
    _no_hard_links(monkeypatch)
    real_replace = os.replace

    def replace(src, dst, *args, **kwargs):
        assert os.path.realpath(dst) != os.path.realpath(dest), "os.replace onto a new-only path"
        return real_replace(src, dst, *args, **kwargs)
    monkeypatch.setattr(dcc.os, "replace", replace)
    dcc.write_atomic_json(str(dest), {"a": 1}, replace=False)
    assert json.loads(dest.read_text()) == {"a": 1}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_fallback_keeps_a_file_published_concurrently(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "contract.json"
    real_lexists = os.path.lexists
    published = []

    def check_then_publish(path):
        # Another process publishes right after any existence check.
        result = real_lexists(path)
        if os.path.realpath(path) == os.path.realpath(dest) and not real_lexists(dest):
            dest.write_text('{"theirs": true}\n')
            published.append(True)
        return result
    monkeypatch.setattr(dcc.os.path, "lexists", check_then_publish)
    _no_hard_links(monkeypatch)
    try:
        dcc.write_atomic_json(str(dest), {"a": 1}, replace=False)
    except dcc.ContractError:
        pass
    if published:
        assert dest.read_text() == '{"theirs": true}\n'
    else:
        assert json.loads(dest.read_text()) == {"a": 1}


def test_fallback_refuses_an_existing_destination(tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "contract.json"

    def appear(dst):
        Path(dst).write_text('{"theirs": true}\n')
    _no_hard_links(monkeypatch, before_failing=appear)
    with pytest.raises(dcc.ContractError, match="already exists"):
        dcc.write_atomic_json(str(dest), {"a": 1}, replace=False)
    assert dest.read_text() == '{"theirs": true}\n'
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_fallback_removes_its_partial_file_on_a_failed_write(
        tmp_path: Path, monkeypatch) -> None:
    dest = tmp_path / "contract.json"
    _no_hard_links(monkeypatch)

    def fsync(fd):
        raise OSError(errno.EIO, "disk error")
    monkeypatch.setattr(dcc.os, "fsync", fsync)
    with pytest.raises(OSError):
        dcc.write_atomic_json(str(dest), {"a": 1}, replace=False)
    assert list(tmp_path.iterdir()) == []


def test_fallback_through_the_cli_exits_one_on_an_existing_file(
        tmp_path: Path, monkeypatch) -> None:
    templates = HERE.parent / "templates"
    root = tmp_path / "root"
    root.mkdir()
    (root / "samples.tsv").write_text((templates / "samples.tsv").read_text())
    spec = json.loads((templates / "samples.ledger.json").read_text())
    spec.pop("note", None)
    (root / "samples.ledger.json").write_text(json.dumps(spec))
    dest = tmp_path / "contract.json"

    def appear(dst):
        Path(dst).write_text('{"theirs": true}\n')
    _no_hard_links(monkeypatch, before_failing=appear)
    code = dcc.main(["--ledger", str(root / "samples.ledger.json"), "--root", str(root),
                     "--emit-contract", str(dest)])
    assert code == 1
    assert dest.read_text() == '{"theirs": true}\n'


# 3. Duplicate levels without min_levels behave as 0.9.7 ---------------------

def _pairing(tmp_path: Path, rows: str, **check) -> list:
    table = tmp_path / "t.tsv"
    table.write_text("participant\tvisit\n" + rows)
    contract = {"schema": dcc.SCHEMA, "table": str(table),
                "checks": [{"kind": "pairing", "unit": "participant", "within": "visit",
                            **check}]}
    _, alerts = dcc.run_contract(contract, str(tmp_path))
    return [a for a in alerts if a["kind"] == "pairing"]


@pytest.mark.parametrize("levels", [["control", "control"], ["28", 28]])
def test_a_repeated_level_without_min_levels_passes_as_on_097(tmp_path: Path, levels) -> None:
    value = str(levels[0])
    assert _pairing(tmp_path, f"p1\t{value}\np2\t{value}\n", levels=levels) == []


def test_a_repeated_level_without_min_levels_still_checks_completeness(tmp_path: Path) -> None:
    # As on 0.9.7: a unit missing the listed level is incomplete.
    alerts = _pairing(tmp_path, "p1\tcontrol\np2\ttreated\n", levels=["control", "control"])
    assert [a["blocking"] for a in alerts] == [True]
    assert "missing a level" in alerts[0]["message"]
