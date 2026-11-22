"""Regressions from the second Codex review of the provenance PR: report
values, access records and data contracts.

Each test failed on the branch before its fix:

1. explicit-null sources and null number entries are errors, not warnings;
2. one check hashes each source once, and the HTML sync runs one check;
3. ``--explain`` credits a write only when its index verdict is current;
4. ``read_source``/``snapshot_source`` pin the absolute path they read;
5. a symlinked ``outputs`` is never written through;
6. a bare string of ids is refused;
7. ``--run-pid`` needs ``--run-started-at``;
8. pairing levels count distinct values;
9. ``--emit-contract`` never overwrites its inputs and writes a valid
   contract atomically;
10. a 0.9.7 pairing with no levels keeps its behavior; ``min_levels`` opts in.
"""

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
import register_value as rv  # noqa: E402
import sync_html_report as shr  # noqa: E402

TABLE = "metric,count\npassing,812\nfailing,40\n"
TABLE_SHA = hashlib.sha256(TABLE.encode()).hexdigest()
TABLE_REL = "analysis/qc/outputs/tables/cell_qc.csv"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs" / "tables").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "reports").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text(TABLE)
    return root


def run_py(project: Path, body: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    script = project / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(
        f"import os, sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import (register_value, record_ids, read_source, "
        "snapshot_source, ValueRegistrationError, AccessRecordError)\n" + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=cwd or project / "analysis" / "qc")


def linked(number_id: str = "n_passing") -> dict:
    return {"id": number_id, "value": 812,
            "source": {"path": TABLE_REL, "sha256": TABLE_SHA,
                       "locator": {"row": "passing", "column": "count"}}}


def report_with(project: Path, numbers: list) -> tuple[Path, Path]:
    reports = project / "analysis" / "qc" / "reports"
    report = reports / "report.html"
    report.write_text("<!doctype html><html><body><main><p>812 cells passed.</p></main>"
                      "</body></html>")
    manifest = reports / ".manifest.json"
    manifest.write_text(json.dumps({"numbers": numbers, "figures": [], "data": []}))
    return report, manifest


# -- 1. explicit nulls are broken links, not unlinked numbers --------------------


@pytest.mark.parametrize("entry", [
    None,
    "n_passing",
    {"id": "n", "value": 1, "source": None},
    {"id": "n", "value": 1, "sources": None},
    {"id": "n", "value": 1, "sources": [None]},
])
def test_explicit_nulls_and_non_objects_are_source_invalid(project: Path, entry) -> None:
    findings = crv.check_numbers([entry], project)
    assert [(f["code"], f["severity"]) for f in findings] == [("source-invalid", "error")]


def test_only_a_record_with_neither_field_is_unchecked(project: Path) -> None:
    (finding,) = crv.check_numbers([{"id": "n", "value": 1}], project)
    assert (finding["code"], finding["severity"]) == ("unchecked-value", "warning")


@pytest.mark.parametrize("bad", [None, {"id": "n", "value": 1, "source": None},
                                 {"id": "n", "value": 1, "sources": None}])
def test_html_gates_fail_on_explicit_nulls(project: Path, bad) -> None:
    report, manifest = report_with(project, [linked(), bad])
    before = report.read_bytes()
    sync = subprocess.run([sys.executable, str(HERE / "sync_html_report.py"), str(report),
                           "--manifest", str(manifest)], capture_output=True, text=True)
    assert sync.returncode == 1 and "source-invalid" in sync.stderr
    assert report.read_bytes() == before
    gate = subprocess.run([sys.executable, str(HERE / "check_html_report.py"), str(report),
                           "--manifest", str(manifest), "--report-only"],
                          capture_output=True, text=True)
    assert gate.returncode == 1 and "[source-invalid] error" in gate.stdout


# -- 2. one hash per source, one check per sync ---------------------------------


def test_one_check_hashes_each_source_once(project: Path, monkeypatch) -> None:
    calls = []

    class Counting:
        @staticmethod
        def sha256(*args):
            calls.append(1)
            return hashlib.sha256(*args)

    monkeypatch.setattr(crv, "hashlib", Counting())
    numbers = [linked("a"), linked("b"),
               {"id": "c", "value": 1, "sources": [linked()["source"]]}]
    assert crv.check_numbers(numbers, project) == []
    assert len(calls) == 1


def test_the_hash_cache_rechecks_identity(project: Path) -> None:
    cache = crv._HashCache()
    path = project / TABLE_REL
    assert cache.sha256(path) == (TABLE_SHA, None)
    path.write_text(TABLE + "extra,1\n")
    digest, problem = cache.sha256(path)
    assert problem is None and digest == hashlib.sha256((TABLE + "extra,1\n").encode()).hexdigest()


def test_sync_runs_the_number_check_once(project: Path, monkeypatch) -> None:
    report, manifest = report_with(project, [linked(), {"id": "legacy", "value": 3}])
    real = shr.number_findings
    runs = []

    def counted(*args, **kwargs):
        runs.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(shr, "number_findings", counted)
    assert shr.main([str(report), "--manifest", str(manifest)]) == 0
    assert len(runs) == 1


# -- 3. --explain credits only a current write -----------------------------------


def _lineage_project(project: Path):
    import test_lineage_index as tli

    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "data").mkdir()
    (project / "out").mkdir()
    (project / ".mycelium").mkdir()
    (project / "data" / "in.csv").write_text("a\n1\n")
    (project / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out/res.csv')\n")
    return tli


def _finish_lineage(project: Path) -> None:
    import extract_data_lineage as stop

    manifest = stop.extract(project / "events.tmp", "sess", str(project),
                            project / ".mycelium" / "io" / "obs")
    lineage_dir = project / ".living" / "log" / "data-lineage"
    lineage_dir.mkdir(parents=True)
    stop.write_manifest_atomic(lineage_dir / "sess.json", manifest)


def test_explain_does_not_credit_a_superseded_write_with_restored_bytes(project: Path) -> None:
    tli = _lineage_project(project)
    res = project / "out" / "res.csv"
    res.write_text("count\n700\n")  # outside the record
    result = run_py(project, 'register_value("n_res", 700, source={"path": '
                             f'"{res}", "locator": {{"row": 0, "column": "count"}}}})')
    assert result.returncode == 0, result.stderr
    tli._run_call(project, "t1", "python fit.py", lambda: res.write_text("count\n812\n"))
    tli._run_call(project, "t2", "python fit.py", lambda: res.write_text("count\n799\n"))
    res.write_text("count\n812\n")  # A restored outside the record, after B
    _finish_lineage(project)
    out = subprocess.run([sys.executable, str(HERE / "check_report_values.py"), "--fragment",
                          str(project / "analysis" / "qc" / "outputs" / "numbers.json"),
                          "--json", "--explain"], capture_output=True, text=True)
    (finding,) = json.loads(out.stdout)["findings"]
    assert finding["code"] == "stale-value"
    lineage = finding["lineage"]
    assert lineage["status"] == "current-bytes-match-superseded-write"
    assert "write_call" not in lineage
    assert [w["verdict"] for w in lineage["matching_writes"]] == ["superseded"]


def _fake_query(monkeypatch, writes: list[dict]) -> None:
    import lineage_index

    monkeypatch.setattr(lineage_index, "query_path",
                        lambda index, rel, disk=False: {"writes": writes})


def test_explain_does_not_credit_a_superseded_deletion(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "d1", "action": "deleted", "evidence": "observed",
         "record": {"status": "superseded"}},
        {"call": "w2", "action": "created", "sha256": "ab" * 32, "evidence": "observed",
         "record": {"status": "current-in-record"}},
    ])
    lineage = crv._explain(object(), "out/res.csv", None)
    assert lineage["status"] == "missing-after-superseded-deletion"
    assert "write_call" not in lineage


def test_explain_credits_a_current_deletion(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": "ab" * 32, "evidence": "observed",
         "record": {"status": "superseded"}},
        {"call": "d2", "action": "deleted", "evidence": "observed",
         "attribution": "sole", "record": {"status": "current-in-record"}},
    ])
    lineage = crv._explain(object(), "out/res.csv", None)
    assert (lineage["status"], lineage["write_call"]) == ("deleted-in-record", "d2")


def test_explain_does_not_credit_an_unverified_write(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "modified", "sha256": "cd" * 32, "evidence": "observed",
         "record": {"status": "unknown", "reasons": ["gap"]}},
    ])
    lineage = crv._explain(object(), "out/res.csv", "cd" * 32)
    assert lineage["status"] == "current-bytes-match-unverified-write"
    assert "write_call" not in lineage


# -- 4. a source read earlier keeps its absolute path ---------------------------


@pytest.mark.parametrize("helper", [
    'text, cfg = read_source("params.yaml", {"key": "t"}, mode="r")',
    'cfg = snapshot_source("params.yaml", {"key": "t"})',
])
def test_a_cwd_change_cannot_relink_an_identical_file(project: Path, helper: str) -> None:
    qc = project / "analysis" / "qc"
    (qc / "params.yaml").write_text("t: 1\n")
    (qc / "scripts" / "params.yaml").write_text("t: 1\n")  # same bytes, other file
    result = run_py(project, helper + "\nos.chdir('scripts')\n"
                    "register_value('n', 1, source=cfg)")
    assert result.returncode == 0, result.stderr
    (entry,) = json.loads((qc / "outputs" / "numbers.json").read_text())["values"]
    assert entry["source"]["path"] == "analysis/qc/params.yaml"


# -- 5. never write through a symlinked outputs ---------------------------------


@pytest.mark.parametrize("call", [
    "record_ids('cells', ['a', 'b'], axis='row', role='used')",
    "register_value('n', 1)",
])
def test_a_symlinked_outputs_is_refused(project: Path, tmp_path: Path, call: str) -> None:
    import shutil

    outputs = project / "analysis" / "qc" / "outputs"
    shutil.rmtree(outputs)
    outside = tmp_path / "outside"
    outside.mkdir()
    outputs.symlink_to(outside, target_is_directory=True)
    result = run_py(project, call)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", ["access.json.lock", "numbers.json.lock"])
def test_a_symlinked_lock_is_refused(project: Path, tmp_path: Path, name: str) -> None:
    victim = tmp_path / "outside.lock"
    (project / "analysis" / "qc" / "outputs" / name).symlink_to(victim)
    call = ("record_ids('cells', ['a'], axis='row', role='used')" if name.startswith("access")
            else "register_value('n', 1)")
    result = run_py(project, call)
    assert result.returncode != 0
    assert not victim.exists()


# -- 6. a bare string is one id, not a list of characters -----------------------


@pytest.mark.parametrize("ids", ["cell_1", b"cell_1"])
def test_a_bare_string_of_ids_is_refused(ids) -> None:
    with pytest.raises(rv.AccessRecordError, match="wrap it in a list"):
        rv.id_set_fingerprint(ids)


# -- 7. the run is identified by pid and start time together -------------------


def _cli(root: Path, ids: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "register_value.py"), "record-ids",
                           "--analysis-root", str(root), "--name", "cells", "--axis", "row",
                           "--role", "used", "--ids-file", str(ids), *extra],
                          capture_output=True, text=True)


@pytest.mark.parametrize("extra", [("--run-pid", "42"), ("--run-started-at", "2026-11-22T10:00:00Z")])
def test_half_a_run_identity_is_refused(project: Path, tmp_path: Path, extra) -> None:
    ids = tmp_path / "ids.txt"
    ids.write_text("a\n")
    result = _cli(project / "analysis" / "qc", ids, *extra)
    assert result.returncode == 2
    assert not (project / "analysis" / "qc" / "outputs" / "access.json").exists()


def test_a_full_run_identity_is_schema_valid(project: Path, tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    ids = tmp_path / "ids.txt"
    ids.write_text("a\n")
    result = _cli(project / "analysis" / "qc", ids, "--run-pid", "42",
                  "--run-started-at", "2026-11-22T10:00:00Z")
    assert result.returncode == 0, result.stderr
    schema = json.loads((HERE.parent / "references" / "provenance-schema.json").read_text())
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/access_fragment",
                                                 "$defs": schema["$defs"]})
    doc = json.loads((project / "analysis" / "qc" / "outputs" / "access.json").read_text())
    assert [e.message for e in validator.iter_errors(doc)] == []
    assert doc["records"][0]["run"] == {"pid": 42, "process_started_at": "2026-11-22T10:00:00Z"}


# -- 8 and 10. pairing levels ------------------------------------------------------


def _pairing(tmp_path: Path, rows: str, **check) -> tuple[list, list]:
    table = tmp_path / "t.tsv"
    table.write_text("participant\tvisit\n" + rows)
    contract = {"schema": dcc.SCHEMA, "table": str(table),
                "checks": [{"kind": "pairing", "unit": "participant", "within": "visit",
                            **check}]}
    return dcc.run_contract(contract, str(tmp_path))


def _blocks(alerts: list) -> bool:
    return any(a["kind"] == "pairing" and a["blocking"] for a in alerts)


@pytest.mark.parametrize("levels", [["A", "A"], ["28", 28]])
def test_a_level_listed_twice_is_one_side(tmp_path: Path, levels) -> None:
    value = str(levels[0])
    # With an explicit minimum, a level listed twice counts once.
    _, alerts = _pairing(tmp_path, f"p1\t{value}\np2\t{value}\n", levels=levels,
                         min_levels=2)
    assert _blocks(alerts)


def test_two_distinct_listed_levels_still_pass(tmp_path: Path) -> None:
    _, alerts = _pairing(tmp_path, "p1\tA\np1\tB\np2\tA\np2\tB\n", levels=["A", "A", "B"])
    assert not _blocks(alerts)


def test_a_097_pairing_without_levels_keeps_its_behavior(tmp_path: Path) -> None:
    _, alerts = _pairing(tmp_path, "p1\tA\np2\tA\n")
    assert not _blocks(alerts)


def test_min_levels_opts_into_the_two_level_minimum(tmp_path: Path) -> None:
    _, alerts = _pairing(tmp_path, "p1\tA\np2\tA\n", min_levels=2)
    assert _blocks(alerts)
    _, alerts = _pairing(tmp_path, "p1\tA\np1\tB\np2\tA\np2\tB\n", min_levels=2)
    assert not _blocks(alerts)


@pytest.mark.parametrize("bad", [1, True, "2", 0])
def test_min_levels_must_be_a_whole_number_of_at_least_two(bad) -> None:
    contract = {"schema": dcc.SCHEMA, "table": "t.tsv",
                "checks": [{"kind": "pairing", "unit": "u", "within": "w", "min_levels": bad}]}
    with pytest.raises(dcc.ContractError):
        dcc.validate_contract(contract)


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    import shutil

    templates = HERE.parent / "templates"
    shutil.copy(templates / "samples.tsv", tmp_path / "samples.tsv")
    spec = json.loads((templates / "samples.ledger.json").read_text())
    spec.pop("note")
    (tmp_path / "samples.ledger.json").write_text(json.dumps(spec))
    return tmp_path


def _spec(ledger: Path, **changes) -> Path:
    path = ledger / "samples.ledger.json"
    spec = {**json.loads(path.read_text()), **changes}
    path.write_text(json.dumps(spec))
    return path


def _ledger_run(spec: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "data_contract_check.py"), "--ledger",
                           str(spec), "--json", *extra], capture_output=True, text=True)


def test_a_ledger_pairing_needs_two_distinct_levels(ledger: Path) -> None:
    spec = _spec(ledger, pairing={"unit": "derivation", "within": "condition",
                                  "levels": ["control", "control"]})
    result = _ledger_run(spec)
    assert result.returncode == 1
    assert "distinct" in result.stderr


def test_ledger_contracts_opt_into_min_levels(ledger: Path) -> None:
    spec = json.loads(_spec(ledger, pairing={"unit": "derivation",
                                             "within": "condition"}).read_text())
    contract = dcc.ledger_contract(spec, str(ledger))
    (pair,) = [c for c in contract["checks"] if c["kind"] == "pairing"]
    assert pair["min_levels"] == 2
    dcc.validate_contract(contract)


# -- 9. --emit-contract never destroys an input -----------------------------------


def test_emit_contract_refuses_the_ledger_spec(ledger: Path) -> None:
    spec = ledger / "samples.ledger.json"
    before = spec.read_bytes()
    result = _ledger_run(spec, "--emit-contract", str(spec))
    assert result.returncode == 1
    assert spec.read_bytes() == before


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_emit_contract_refuses_the_sample_table(ledger: Path, alias: str) -> None:
    table = ledger / "samples.tsv"
    before = table.read_bytes()
    if alias == "same":
        dest = table
    elif alias == "symlink":
        dest = ledger / "contract.json"
        dest.symlink_to(table)
    else:
        dest = ledger / "contract.json"
        os.link(table, dest)
    result = _ledger_run(ledger / "samples.ledger.json", "--emit-contract", str(dest))
    assert result.returncode == 1
    assert table.read_bytes() == before


def test_emit_contract_validates_before_writing(ledger: Path, monkeypatch, capsys) -> None:
    out = ledger / "data_contract.json"

    def broken(spec, spec_dir):
        return {"schema": dcc.SCHEMA, "table": str(ledger / "samples.tsv"),
                "checks": [{"kind": "no-such-kind"}]}

    monkeypatch.setattr(dcc, "ledger_contract", broken)
    code = dcc.main(["--ledger", str(ledger / "samples.ledger.json"),
                     "--emit-contract", str(out), "--root", str(ledger)])
    assert code == 1
    assert not out.exists()
    assert [p.name for p in ledger.iterdir() if p.name.endswith(".tmp")] == []
