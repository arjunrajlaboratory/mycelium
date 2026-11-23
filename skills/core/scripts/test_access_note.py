"""The Stop-time access note (provenance item 7 follow-up).

When a script this session wrote a table but no `record_ids` record covers
it, an accepting Stop adds an advisory note. `access_note.py` reads the
lineage manifest the Stop phase just wrote and the project's
`analysis/*/outputs/access.json` files. It errs toward silence, fails open,
and keeps the note short.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import access_note as an  # noqa: E402

TS = "2026-11-23T12:00:10Z"
SHA = "a" * 64


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
            "script_sha256": SHA, "inputs": [], "outputs": records, "bash_exit": 0,
            "tool_outcome": "success", **extra}


def _manifest(tmp_path: Path, actions: list[dict], data_commands=()) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": "mycelium.data_lineage.manifest.v2",
                                "actions": actions, "data_commands": list(data_commands)}))
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


def _epoch(text: str) -> float:
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def _note(repo: Path, manifest: Path, **kw) -> str:
    return an.note_for(manifest, repo, **kw)


def test_table_written_without_access_record_gets_a_short_note(tmp_path, repo):
    note = _note(repo, _manifest(tmp_path, [_action(repo)]))
    assert note.startswith("ACCESS NOTE: 1 script(s) this session")
    assert "analysis/qc/scripts/filter.py -> analysis/qc/outputs/kept.csv" in note
    assert "record_ids" in note and "filters" in note
    assert "this turn" not in note and "not blocked" not in note


def test_note_lines_carry_one_key_per_named_script(tmp_path, repo):
    lines = an.note_lines(_manifest(tmp_path, [_action(repo)]), repo)
    assert lines[1:] == [f"key\tanalysis/qc/scripts/filter.py\t{SHA}"]


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


def test_helper_module_in_the_same_analysis_covers_the_script(tmp_path, repo):
    _access(repo, [_record(computed_at="scripts/lib/qc_utils.py:L12")])
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_record_in_another_namespace_inside_the_call_window_covers_it(tmp_path, repo):
    # An R script in analysis/qc recording with namespace = "B", or "R".
    _access(repo, [_record(computed_at="R", started="2026-11-23T12:00:05Z")], ns="B")
    earlier = _action(repo, script="analysis/qc/scripts/other.py", ts="2026-11-23T12:00:01Z",
                      outputs=())
    (repo / "analysis/qc/scripts/other.py").write_text("")
    assert _note(repo, _manifest(tmp_path, [earlier, _action(repo)])) == ""


def test_record_in_another_namespace_with_no_lower_edge_covers_it(tmp_path, repo):
    # The reviewer's case: R in analysis/qc records with namespace = "B".
    _access(repo, [_record(computed_at="scripts/filter.R", started="2026-11-23T12:00:00Z")],
            ns="B")
    manifest = _manifest(tmp_path, [_action(repo)])
    assert _note(repo, manifest) == ""
    # A session start after that record bounds the window again.
    assert "filter.py" in _note(repo, manifest, since=_epoch("2026-11-23T12:00:05Z"))


def test_record_in_another_namespace_before_the_previous_call_does_not_count(tmp_path, repo):
    _access(repo, [_record(computed_at="scripts/x.py:L1", started="2026-11-23T11:59:00Z")],
            ns="B")
    previous = {"ts": "2026-11-23T12:00:00Z", "kind": "data_command"}
    note = _note(repo, _manifest(tmp_path, [_action(repo)], [previous]))
    assert "filter.py" in note


def test_record_from_a_later_process_does_not_count(tmp_path, repo):
    _access(repo, [_record(started="2026-11-23T12:05:00Z")])
    assert "filter.py" in _note(repo, _manifest(tmp_path, [_action(repo)]))


def test_record_from_before_the_session_does_not_count(tmp_path, repo):
    _access(repo, [_record(started="2026-11-22T09:00:00Z")])
    manifest = _manifest(tmp_path, [_action(repo)])
    assert _note(repo, manifest) == ""  # no session start known: silent
    assert "filter.py" in _note(repo, manifest, since=_epoch("2026-11-23T11:00:00Z"))


def test_rerun_is_judged_by_its_last_run_only(tmp_path, repo):
    # record_ids replaced run 1's record with run 2's.
    _access(repo, [_record(started="2026-11-23T12:05:00Z")])
    runs = [_action(repo, ts="2026-11-23T12:00:10Z"), _action(repo, ts="2026-11-23T12:05:10Z")]
    assert _note(repo, _manifest(tmp_path, runs)) == ""


def test_a_later_run_without_a_table_supersedes_an_earlier_table_run(tmp_path, repo):
    runs = [_action(repo, ts="2026-11-23T12:00:10Z"),
            _action(repo, ts="2026-11-23T12:05:10Z", outputs=())]
    assert _note(repo, _manifest(tmp_path, runs)) == ""


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


def test_seen_script_at_the_same_content_is_not_named_again(tmp_path, repo):
    seen = tmp_path / "seen.tsv"
    seen.write_text(f"analysis/qc/scripts/filter.py\t{SHA}\n")
    manifest = _manifest(tmp_path, [_action(repo)])
    assert _note(repo, manifest, seen_path=seen) == ""
    seen.write_text(f"analysis/qc/scripts/filter.py\t{'b' * 64}\n")  # edited since
    assert _note(repo, manifest, seen_path=seen)


def test_note_caps_its_list(tmp_path, repo):
    actions = []
    for i in range(8):
        (repo / "analysis" / "qc" / "scripts" / f"s{i}.py").write_text("")
        actions.append(_action(repo, script=f"analysis/qc/scripts/s{i}.py",
                               outputs=(f"analysis/qc/outputs/t{i}.csv",),
                               ts=f"2026-11-23T12:00:1{i}Z"))
    note = _note(repo, _manifest(tmp_path, actions))
    assert "8 script(s)" in note and "(+5 more)" in note
    assert note.count("s0.py") == 1


def test_note_with_long_non_ascii_names_stays_under_the_byte_cap(tmp_path, repo):
    actions = []
    for s in range(6):
        d = repo / f"analysis/qc_é{s}/scripts"
        d.mkdir(parents=True)
        (d / "step.py").write_text("x")
        actions.append(_action(repo, script=f"analysis/qc_é{s}/scripts/step.py",
                               outputs=[f"analysis/qc_é{s}/outputs/" + "é" * 140 + f"_{k}.csv"
                                        for k in range(3)]))
    note = _note(repo, _manifest(tmp_path, actions))
    assert note.startswith("ACCESS NOTE:")
    assert len(note.encode("utf-8")) <= an.MAX_NOTE_BYTES
    assert note.endswith("nothing to redo.")


@pytest.mark.parametrize("bad", ["not json", "[]", '{"actions": "x"}', '{"actions": [1, null]}'])
def test_malformed_manifest_prints_nothing(tmp_path, repo, bad):
    path = tmp_path / "manifest.json"
    path.write_text(bad)
    assert _note(repo, path) == ""


def test_malformed_access_file_in_the_steps_analysis_reads_as_unknown(tmp_path, repo):
    (repo / "analysis" / "qc" / "outputs" / "access.json").write_text("{broken")
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


@pytest.mark.parametrize("content", ['{"audit": "qc", "rows": 3}', "{broken"])
def test_foreign_or_broken_file_elsewhere_does_not_silence_the_note(tmp_path, repo, content):
    other = repo / "analysis" / "other" / "outputs"
    other.mkdir(parents=True)
    (other / "access.json").write_text(content)
    assert "filter.py" in _note(repo, _manifest(tmp_path, [_action(repo)]))


def test_foreign_file_in_the_steps_analysis_is_not_a_record(tmp_path, repo):
    (repo / "analysis" / "qc" / "outputs" / "access.json").write_text('{"audit": "qc"}')
    assert "filter.py" in _note(repo, _manifest(tmp_path, [_action(repo)]))


def test_over_budget_reads_as_unknown_and_stays_fast(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(an, "MAX_TOTAL_BYTES", 2000)
    big = [_record(computed_at=f"scripts/s{i}.py:L1") for i in range(40)]
    for i in range(5):
        _access(repo, big, ns=f"ns{i}")
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_time_budget_reads_as_unknown(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(an, "TIME_BUDGET_SECONDS", -1.0)
    _access(repo, [_record(computed_at="scripts/x.py:L1")], ns="B")
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_command_line_prints_note_and_keys_and_always_exits_zero(tmp_path, repo):
    script = HERE / "access_note.py"
    ok = subprocess.run([sys.executable, str(script), "--manifest",
                         str(_manifest(tmp_path, [_action(repo)])), "--repo-root", str(repo),
                         "--since", str(_epoch("2026-11-23T11:00:00Z"))],
                        capture_output=True, text=True)
    assert ok.returncode == 0
    first, *rest = ok.stdout.splitlines()
    assert first.startswith("ACCESS NOTE:") and rest == [
        f"key\tanalysis/qc/scripts/filter.py\t{SHA}"]
    missing = subprocess.run([sys.executable, str(script), "--manifest",
                              str(tmp_path / "nope.json"), "--repo-root", str(repo)],
                             capture_output=True, text=True)
    assert missing.returncode == 0 and missing.stdout == ""
    garbage = subprocess.run([sys.executable, str(script), "--bogus"],
                             capture_output=True, text=True)
    assert garbage.returncode == 0 and garbage.stdout == ""


def test_symlinked_outputs_in_the_steps_analysis_read_as_unknown(tmp_path, repo):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "access.json").write_text(json.dumps(
        {"schema": "mycelium.access.v1", "namespace": "qc", "records": []}))
    outputs = repo / "analysis" / "qc" / "outputs"
    outputs.rmdir()
    outputs.symlink_to(outside, target_is_directory=True)
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_symlinked_analysis_elsewhere_is_never_read(tmp_path, repo, monkeypatch):
    outside = tmp_path / "outside" / "outputs"
    outside.mkdir(parents=True)
    _access(repo, [], ns="qc")
    (outside / "access.json").write_text(json.dumps(
        {"schema": "mycelium.access.v1", "namespace": "x", "records": [_record()]}))
    (repo / "analysis" / "linked").symlink_to(outside.parent, target_is_directory=True)
    read = []
    real = an._Records.read
    monkeypatch.setattr(an._Records, "read",
                        lambda self, path, own: (read.append(path), real(self, path, own))[1])
    assert "filter.py" in _note(repo, _manifest(tmp_path, [_action(repo)]))
    assert all(p.parent.parent.name != "linked" for p in read)


def test_too_many_access_files_is_an_incomplete_scan(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(an, "MAX_ACCESS_FILES", 3)
    for i in range(5):
        _access(repo, [], ns=f"ns{i}")
    assert _note(repo, _manifest(tmp_path, [_action(repo)])) == ""


def test_output_shared_by_two_scripts_in_one_call_is_not_attributed(tmp_path, repo):
    (repo / "analysis" / "qc" / "scripts" / "other.py").write_text("")
    a = _action(repo, observation_id="claude:call_1")
    b = _action(repo, script="analysis/qc/scripts/other.py", observation_id="claude:call_1")
    assert _note(repo, _manifest(tmp_path, [a, b])) == ""
    # In separate calls each run is judged on its own.
    b["observation_id"] = "claude:call_2"
    assert "filter.py" in _note(repo, _manifest(tmp_path, [a, b]))


def test_unencodable_name_prints_nothing_rather_than_broken_text(tmp_path, repo):
    action = _action(repo, outputs=("analysis/qc/outputs/bad\udcff.csv",))
    assert _note(repo, _manifest(tmp_path, [action])) == ""
