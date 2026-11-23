"""The Stop-time access note (provenance item 7 follow-up).

When a step this turn wrote a table but no `record_ids` record joins to it,
Stop adds an advisory note. `access_note.py` reads the lineage manifest the
Stop phase just wrote and the project's `analysis/*/outputs/access.json`
files. A record joins an action when its `computed_at` names the action's
script and its `run.process_started_at` is not after the action ended.
The note never blocks Stop, and the helper fails open: any error prints
nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import access_note as an  # noqa: E402

TS = "2026-11-23T12:00:10Z"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".living").mkdir(parents=True)
    scripts = root / "analysis" / "qc" / "scripts"
    scripts.mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs").mkdir()
    (scripts / "filter.py").write_text("print('filter')\n")
    return root


def _action(repo: Path, *, script: str | None = "analysis/qc/scripts/filter.py",
            outputs=("analysis/qc/outputs/kept.csv",), change="created",
            ts=TS, **extra) -> dict:
    records = []
    for rel in outputs:
        record = {"path": str(repo / rel), "rel_path": rel}
        if change is not None:
            record["observed_change"] = change
        records.append(record)
    return {"ts": ts, "script": str(repo / script) if script else None,
            "inputs": [], "outputs": records, "bash_exit": 0,
            "tool_outcome": "success", **extra}


def _manifest(tmp_path: Path, actions: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": "mycelium.data_lineage.manifest.v2",
                                "actions": actions}))
    return path


def _access(repo: Path, records: list[dict], ns: str = "qc") -> None:
    path = repo / "analysis" / ns / "outputs" / "access.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": "mycelium.access.v1", "namespace": ns,
                                "records": records}))


def _record(computed_at="scripts/filter.py:L12", started="2026-11-23T12:00:05Z") -> dict:
    return {"name": "cells_after_qc", "axis": "row", "role": "used", "count": 3,
            "distinct": 3, "id_set_sha256": "0" * 64, "computed_at": computed_at,
            "run": {"pid": 4242, "process_started_at": started}}


def _note(repo: Path, manifest: Path) -> str:
    return an.note_for(manifest, repo)


def test_table_written_without_access_record_gets_a_note(tmp_path, repo):
    note = _note(repo, _manifest(tmp_path, [_action(repo)]))
    assert note.startswith("ACCESS NOTE:")
    assert "analysis/qc/scripts/filter.py" in note
    assert "analysis/qc/outputs/kept.csv" in note
    assert "record_ids" in note
    assert "filters, subsets or splits" in note


def test_matching_access_record_suppresses_the_note(tmp_path, repo):
    _access(repo, [_record()])
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_r_record_without_a_line_number_joins_too(tmp_path, repo):
    _access(repo, [_record(computed_at="scripts/filter.py")])
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_absolute_computed_at_from_a_script_outside_analysis_joins(tmp_path, repo):
    (repo / "tools").mkdir()
    (repo / "tools" / "split.py").write_text("")
    _access(repo, [_record(computed_at=f"{repo / 'tools' / 'split.py'}:L3")])
    action = _action(repo, script="tools/split.py", outputs=("data/train.parquet",))
    assert _note(repo, _manifest(tmp_path, [action])) == ""


def test_record_from_another_script_does_not_count(tmp_path, repo):
    _access(repo, [_record(computed_at="scripts/other.py:L3")])
    assert "filter.py" in _note(repo, _manifest(tmp_path, [_action(repo)]))


def test_record_from_a_later_process_does_not_count(tmp_path, repo):
    _access(repo, [_record(started="2026-11-23T12:05:00Z")])
    assert "filter.py" in _note(repo, _manifest(tmp_path, [_action(repo)]))


@pytest.mark.parametrize("output", ["analysis/qc/outputs/kept.tsv",
                                    "analysis/qc/outputs/kept.csv.gz",
                                    "data/cells.h5ad", "data/x.parquet"])
def test_each_table_format_counts(tmp_path, repo, output):
    assert _note(repo, _manifest(tmp_path, [_action(repo, outputs=(output,))]))


@pytest.mark.parametrize("output", ["analysis/qc/outputs/plot.png",
                                    "analysis/qc/outputs/numbers.json",
                                    "analysis/qc/outputs/model.pkl",
                                    ".living/log/x.csv", ".mycelium/x.csv"])
def test_non_tables_and_mycelium_state_do_not_count(tmp_path, repo, output):
    assert _note(repo, _manifest(tmp_path, [_action(repo, outputs=(output,))])) == ""


@pytest.mark.parametrize("change", ["unchanged", "deleted", "unwatched", "unknown-racy", None])
def test_only_observed_writes_count(tmp_path, repo, change):
    assert _note(repo, _manifest(tmp_path, [_action(repo, change=change)])) == ""


def test_modified_table_counts(tmp_path, repo):
    assert _note(repo, _manifest(tmp_path, [_action(repo, change="modified")]))


@pytest.mark.parametrize("extra", [{"bash_exit": 1}, {"tool_outcome": "failure"}])
def test_failed_runs_do_not_count(tmp_path, repo, extra):
    assert _note(repo, _manifest(tmp_path, [_action(repo, **extra)])) == ""


def test_inline_runs_cannot_be_joined_and_do_not_count(tmp_path, repo):
    assert _note(repo, _manifest(tmp_path, [_action(repo, script=None)])) == ""


def test_relative_script_in_a_committed_manifest_is_framed_in_the_repo(tmp_path, repo):
    action = _action(repo)
    action["script"] = "analysis/qc/scripts/filter.py"
    _access(repo, [_record()])
    assert _note(repo, _manifest(tmp_path, [action])) == ""


def test_note_lists_each_step_once_and_caps_the_list(tmp_path, repo):
    actions = []
    for i in range(8):
        (repo / "analysis" / "qc" / "scripts" / f"s{i}.py").write_text("")
        actions.append(_action(repo, script=f"analysis/qc/scripts/s{i}.py",
                               outputs=(f"analysis/qc/outputs/t{i}.csv",)))
    actions.append(_action(repo, script="analysis/qc/scripts/s0.py",
                           outputs=("analysis/qc/outputs/t0.csv",)))
    note = _note(repo, _manifest(tmp_path, actions))
    assert note.count("s0.py") == 1
    assert "8 step(s)" in note
    assert "(+3 more)" in note


@pytest.mark.parametrize("bad", ["not json", "[]", '{"actions": "x"}', '{"actions": [1, null]}'])
def test_malformed_manifest_prints_nothing(tmp_path, repo, bad):
    path = tmp_path / "manifest.json"
    path.write_text(bad)
    assert _note(repo, path) == ""


def test_malformed_access_file_reads_as_unknown_not_missing(tmp_path, repo):
    path = repo / "analysis" / "qc" / "outputs" / "access.json"
    path.write_text("{broken")
    # Whether the step recorded access is unknown, so no note is claimed.
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_command_line_fails_open_and_always_exits_zero(tmp_path, repo):
    script = HERE / "access_note.py"
    ok = subprocess.run([sys.executable, str(script), "--manifest",
                         str(_manifest(tmp_path, [_action(repo)])), "--repo-root", str(repo)],
                        capture_output=True, text=True)
    assert ok.returncode == 0 and ok.stdout.startswith("ACCESS NOTE:")
    missing = subprocess.run([sys.executable, str(script), "--manifest",
                              str(tmp_path / "nope.json"), "--repo-root", str(repo)],
                             capture_output=True, text=True)
    assert missing.returncode == 0 and missing.stdout == ""
    garbage = subprocess.run([sys.executable, str(script), "--bogus"],
                             capture_output=True, text=True)
    assert garbage.returncode == 0 and garbage.stdout == ""
