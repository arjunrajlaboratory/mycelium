"""A standard sample ledger, checked at ingest.

Item 6 of the provenance work: `samples.ledger.json` names a per-sample
table's id, group, replication nesting and batch variables, and
`data_contract_check.py --ledger` builds and runs the contract it implies.
"""

from __future__ import annotations

import csv
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

TEMPLATES = HERE.parent / "templates"
CHECKER = HERE / "data_contract_check.py"


@pytest.fixture()
def ledger(tmp_path: Path) -> Path:
    shutil.copy(TEMPLATES / "samples.tsv", tmp_path / "samples.tsv")
    spec = json.loads((TEMPLATES / "samples.ledger.json").read_text())
    spec.pop("note")
    (tmp_path / "samples.ledger.json").write_text(json.dumps(spec))
    return tmp_path


def run(spec_dir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECKER), "--ledger",
                           str(spec_dir / "samples.ledger.json"), "--json", *extra],
                          capture_output=True, text=True)


def spec(spec_dir: Path) -> dict:
    return json.loads((spec_dir / "samples.ledger.json").read_text())


def write_spec(spec_dir: Path, data: dict) -> None:
    (spec_dir / "samples.ledger.json").write_text(json.dumps(data))


def rows(spec_dir: Path) -> list[dict]:
    with open(spec_dir / "samples.tsv") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_rows(spec_dir: Path, data: list[dict]) -> None:
    with open(spec_dir / "samples.tsv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(data)


def test_the_template_ledger_passes(ledger: Path) -> None:
    result = run(ledger)
    assert result.returncode == 0, result.stdout + result.stderr
    kinds = [c["kind"] for c in json.loads(result.stdout)["checks"]]
    assert kinds == ["schema", "unique", "nesting", "unit_of_replication",
                     "batch_confounding", "batch_confounding", "batch_estimability"]


def test_a_repeated_sample_id_blocks(ledger: Path) -> None:
    data = rows(ledger)
    data[1]["sample_id"] = data[0]["sample_id"]
    write_rows(ledger, data)
    result = run(ledger)
    assert result.returncode == 2
    assert any(a["kind"] == "unique" for a in json.loads(result.stdout)["alerts"])


def test_a_dish_label_reused_across_preparations_blocks(ledger: Path) -> None:
    data = rows(ledger)
    data[2]["dish"] = data[0]["dish"]  # same dish label under another prep
    write_rows(ledger, data)
    alerts = json.loads(run(ledger).stdout)["alerts"]
    nesting = [a for a in alerts if a["kind"] == "nesting"]
    assert nesting and nesting[0]["blocking"]


def test_local_labels_compose_identity_for_every_check(ledger: Path) -> None:
    data = rows(ledger)
    # Derivations labelled locally: "D1" under both lines is two derivations.
    for row in data:
        row["derivation"] = row["derivation"].split("-")[1]
        row["prep"] = "P1"
        row["dish"] = row["dish"].rsplit("-", 1)[1]
    write_rows(ledger, data)
    global_spec = spec(ledger)
    assert run(ledger).returncode == 2  # global labels: D1 crosses lines
    write_spec(ledger, {**global_spec, "local_labels": True})
    result = run(ledger)
    assert result.returncode == 0, result.stdout
    contract = dcc.ledger_contract({**global_spec, "local_labels": True}, str(ledger))
    unit = next(c for c in contract["checks"] if c["kind"] == "unit_of_replication")
    assert unit["unit_scope"] == ["line"]


def test_a_group_with_one_unit_blocks(ledger: Path) -> None:
    data = [r for r in rows(ledger) if not (r["condition"] == "treated" and r["line"] == "L2")]
    write_rows(ledger, data)
    alerts = json.loads(run(ledger).stdout)["alerts"]
    assert any(a["kind"] == "unit_of_replication" and a["blocking"] for a in alerts)


def test_several_samples_per_unit_are_expected_on_a_ledger(ledger: Path) -> None:
    contract = dcc.ledger_contract(spec(ledger), str(ledger))
    unit = next(c for c in contract["checks"] if c["kind"] == "unit_of_replication")
    assert unit["rows"] == "samples"
    assert "units_may_span_groups" not in unit


def test_a_batch_confounded_with_the_group_blocks(ledger: Path) -> None:
    data = rows(ledger)
    for row in data:
        row["sequencing_run"] = "runA" if row["condition"] == "control" else "runB"
    write_rows(ledger, data)
    alerts = json.loads(run(ledger).stdout)["alerts"]
    assert any(a["kind"] == "batch_confounding" and a["blocking"] for a in alerts)


def test_a_nested_level_listed_as_batch_is_a_spec_error(ledger: Path) -> None:
    write_spec(ledger, {**spec(ledger), "batch": ["prep_date", "dish"]})
    result = run(ledger)
    assert result.returncode == 1
    assert "nested in the group by construction" in result.stderr


def test_spanning_groups_needs_a_matching_pairing(ledger: Path) -> None:
    base = spec(ledger)
    paired = dcc.ledger_contract({**base, "pairing": {"unit": "derivation",
                                                      "within": "condition"}}, str(ledger))
    unit = next(c for c in paired["checks"] if c["kind"] == "unit_of_replication")
    assert unit["units_may_span_groups"] is True
    other = dcc.ledger_contract({**base, "pairing": {"unit": "line", "within": "condition"}},
                                str(ledger))
    unit = next(c for c in other["checks"] if c["kind"] == "unit_of_replication")
    assert "units_may_span_groups" not in unit


def test_emit_contract_writes_a_replayable_contract(ledger: Path, tmp_path: Path) -> None:
    out = tmp_path / "data_contract.json"
    assert run(ledger, "--emit-contract", str(out)).returncode == 0
    contract = json.loads(out.read_text())
    dcc.validate_contract(contract)
    replay = subprocess.run([sys.executable, str(CHECKER), "--contract", str(out)],
                            capture_output=True, text=True, cwd="/")
    assert replay.returncode == 0, replay.stdout


@pytest.mark.parametrize(
    "bad",
    [
        {"schema": "wrong"},
        {"nesting": ["line", "prep"]},          # unit not a nesting level
        {"local_labels": "yes"},
        {"pairing": {"unit": "line"}},
        {"extra_key": 1},
    ],
)
def test_malformed_specs_are_usage_errors(ledger: Path, bad: dict) -> None:
    write_spec(ledger, {**spec(ledger), **bad})
    assert run(ledger).returncode == 1


def test_contract_and_ledger_are_exclusive(ledger: Path) -> None:
    both = subprocess.run([sys.executable, str(CHECKER), "--ledger", "a", "--contract", "b"],
                          capture_output=True, text=True)
    assert both.returncode == 2  # argparse usage error
    emit = subprocess.run([sys.executable, str(CHECKER), "--contract", "x", "--emit-contract",
                           "y"], capture_output=True, text=True)
    assert emit.returncode == 1


def test_unique_and_nesting_report_missing_values(tmp_path: Path) -> None:
    table = tmp_path / "t.tsv"
    table.write_text("sample\tline\tderiv\nS1\tL1\tD1\n\tL1\tD2\nS3\t\tD3\n")
    contract = {"schema": dcc.SCHEMA, "table": str(table), "checks": [
        {"kind": "unique", "columns": ["sample"]},
        {"kind": "nesting", "levels": ["line", "deriv"]},
    ]}
    _, alerts = dcc.run_contract(contract, str(tmp_path))
    kinds = {(a["kind"], a["blocking"]) for a in alerts}
    assert ("unique", True) in kinds
    assert ("nesting", False) in kinds


def test_composed_identities_never_collide() -> None:
    rows = [{"line": "A/B", "d": "C", "g": "x"}, {"line": "A", "d": "B/C", "g": "x"}]
    scoped = dcc.scoped(rows, "d", ["line"])
    assert scoped[0]["d"] != scoped[1]["d"]


def test_a_pairing_with_one_side_missing_blocks(ledger: Path) -> None:
    data = rows(ledger)
    for row in data:
        row["visit"] = "baseline"
    write_rows(ledger, data)
    write_spec(ledger, {**spec(ledger), "pairing": {"unit": "derivation", "within": "visit"}})
    alerts = json.loads(run(ledger).stdout)["alerts"]
    assert any(a["kind"] == "pairing" and a["blocking"] for a in alerts)
    write_spec(ledger, {**spec(ledger), "pairing": {"unit": "derivation", "within": "visit",
                                                    "levels": ["baseline", "week4"]}})
    alerts = json.loads(run(ledger).stdout)["alerts"]
    assert any(a["kind"] == "pairing" and a["blocking"] for a in alerts)


def test_local_pairing_units_are_qualified_by_their_own_ancestors(ledger: Path) -> None:
    base = {**spec(ledger), "local_labels": True,
            "pairing": {"unit": "prep", "within": "condition"}}
    contract = dcc.ledger_contract(base, str(ledger))
    pair = next(c for c in contract["checks"] if c["kind"] == "pairing")
    assert pair["unit_scope"] == ["line", "derivation"]


def test_local_batch_labels_are_qualified(ledger: Path) -> None:
    base = {**spec(ledger), "local_labels": True, "batch": ["line", "prep_date"]}
    contract = dcc.ledger_contract({**base, "unit_of_replication": "derivation"}, str(ledger))
    line = next(c for c in contract["checks"]
                if c["kind"] == "batch_confounding" and c["batch"] == "line")
    assert "batch_scope" not in line  # the top level has no ancestors
    nested = dcc.ledger_contract({**base, "nesting": ["site", "line", "derivation", "prep",
                                                     "dish", "library"]}, str(ledger))
    line = next(c for c in nested["checks"]
                if c["kind"] == "batch_confounding" and c["batch"] == "line")
    assert line["batch_scope"] == ["site"]


def test_jointly_confounded_batches_block(tmp_path: Path) -> None:
    table = tmp_path / "t.tsv"
    lines = ["unit\tg\tdate\trun"]
    design = [("control", "d1", "r1"), ("control", "d1", "r1"),
              ("treated", "d2", "r1"), ("treated", "d1", "r2")]
    for i, (g, d, r) in enumerate(design):
        lines.append(f"u{i}\t{g}\t{d}\t{r}")
    table.write_text("\n".join(lines) + "\n")
    joint = {"schema": dcc.SCHEMA, "table": str(table), "checks": [
        {"kind": "batch_confounding", "batch": "date", "contrast": "g"},
        {"kind": "batch_confounding", "batch": "run", "contrast": "g"},
        {"kind": "batch_estimability", "batch": ["date", "run"], "contrast": "g"},
    ]}
    summaries, alerts = dcc.run_contract(joint, str(tmp_path))
    statuses = [s["status"] for s in summaries]
    assert statuses[:2] != ["block", "block"]  # each column alone does not block
    assert statuses[2] == "block"
    crossed = tmp_path / "c.tsv"
    crossed.write_text("unit\tg\tdate\trun\n" + "\n".join(
        f"u{i}\t{g}\t{d}\t{r}" for i, (g, d, r) in enumerate(
            [(g, d, r) for g in ("control", "treated") for d in ("d1", "d2") for r in ("r1", "r2")]))
        + "\n")
    ok = {**joint, "table": str(crossed), "checks": [joint["checks"][2]]}
    assert dcc.run_contract(ok, str(tmp_path))[0][0]["status"] == "pass"


def test_emitted_tables_are_relative_to_root(ledger: Path) -> None:
    out = ledger / "contract.json"
    result = subprocess.run([sys.executable, str(CHECKER), "--ledger",
                             str(ledger / "samples.ledger.json"), "--root", str(ledger),
                             "--emit-contract", str(out)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout
    assert json.loads(out.read_text())["table"] == "samples.tsv"


@pytest.fixture()
def ledger_2(tmp_path: Path) -> Path:
    import shutil

    templates = HERE.parent / "templates"
    shutil.copy(templates / "samples.tsv", tmp_path / "samples.tsv")
    spec = json.loads((templates / "samples.ledger.json").read_text())
    spec.pop("note")
    (tmp_path / "samples.ledger.json").write_text(json.dumps(spec))
    return tmp_path


def _ledger_run(spec: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "data_contract_check.py"), "--ledger",
                           str(spec), "--json", *extra], capture_output=True, text=True)


def _spec(ledger_2: Path, **changes) -> Path:
    path = ledger_2 / "samples.ledger.json"
    spec = {**json.loads(path.read_text()), **changes}
    path.write_text(json.dumps(spec))
    return path


def test_a_ledger_pairing_needs_two_distinct_levels(ledger_2: Path) -> None:
    spec = _spec(ledger_2, pairing={"unit": "derivation", "within": "condition",
                                  "levels": ["control", "control"]})
    result = _ledger_run(spec)
    assert result.returncode == 1
    assert "distinct" in result.stderr


def test_ledger_contracts_opt_into_min_levels(ledger_2: Path) -> None:
    spec = json.loads(_spec(ledger_2, pairing={"unit": "derivation",
                                             "within": "condition"}).read_text())
    contract = dcc.ledger_contract(spec, str(ledger_2))
    (pair,) = [c for c in contract["checks"] if c["kind"] == "pairing"]
    assert pair["min_levels"] == 2
    dcc.validate_contract(contract)


def test_emit_contract_refuses_the_ledger_spec(ledger_2: Path) -> None:
    spec = ledger_2 / "samples.ledger.json"
    before = spec.read_bytes()
    result = _ledger_run(spec, "--emit-contract", str(spec))
    assert result.returncode == 1
    assert spec.read_bytes() == before


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
def test_emit_contract_refuses_the_sample_table(ledger_2: Path, alias: str) -> None:
    table = ledger_2 / "samples.tsv"
    before = table.read_bytes()
    if alias == "same":
        dest = table
    elif alias == "symlink":
        dest = ledger_2 / "contract.json"
        dest.symlink_to(table)
    else:
        dest = ledger_2 / "contract.json"
        os.link(table, dest)
    result = _ledger_run(ledger_2 / "samples.ledger.json", "--emit-contract", str(dest))
    assert result.returncode == 1
    assert table.read_bytes() == before


def test_emit_contract_validates_before_writing(ledger_2: Path, monkeypatch, capsys) -> None:
    out = ledger_2 / "data_contract.json"

    def broken(spec, spec_dir):
        return {"schema": dcc.SCHEMA, "table": str(ledger_2 / "samples.tsv"),
                "checks": [{"kind": "no-such-kind"}]}

    monkeypatch.setattr(dcc, "ledger_contract", broken)
    code = dcc.main(["--ledger", str(ledger_2 / "samples.ledger.json"),
                     "--emit-contract", str(out), "--root", str(ledger_2)])
    assert code == 1
    assert not out.exists()
    assert [p.name for p in ledger_2.iterdir() if p.name.endswith(".tmp")] == []


@pytest.fixture()
def ledger_3(tmp_path: Path) -> Path:
    templates = HERE.parent / "templates"
    shutil.copy(templates / "samples.tsv", tmp_path / "samples.tsv")
    spec = json.loads((templates / "samples.ledger.json").read_text())
    spec.pop("note", None)
    (tmp_path / "samples.ledger.json").write_text(json.dumps(spec))
    return tmp_path


def _no_temp_files(directory: Path) -> bool:
    return not [p for p in directory.iterdir() if p.name.endswith(".tmp")]


def _emit(ledger_3: Path, dest: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--ledger", str(ledger_3 / "samples.ledger.json"),
         "--root", str(ledger_3), "--emit-contract", str(dest), *extra],
        capture_output=True, text=True)


def test_missing_table_never_replaces_an_existing_contract(ledger_3: Path) -> None:
    dest = ledger_3 / "data_contract.json"
    dest.write_text('{"extended": true}\n')
    (ledger_3 / "samples.tsv").unlink()
    result = _emit(ledger_3, dest)
    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert dest.read_text() == '{"extended": true}\n'
    result = _emit(ledger_3, dest, "--overwrite")
    assert result.returncode == 2
    assert dest.read_text() == '{"extended": true}\n'
    assert _no_temp_files(ledger_3)


def test_missing_table_writes_no_new_contract(ledger_3: Path) -> None:
    dest = ledger_3 / "data_contract.json"
    (ledger_3 / "samples.tsv").unlink()
    result = _emit(ledger_3, dest)
    assert result.returncode == 2
    assert not dest.exists()
    assert "not written" in result.stderr


def test_existing_destination_needs_overwrite(ledger_3: Path) -> None:
    dest = ledger_3 / "data_contract.json"
    dest.write_text('{"extended": true}\n')
    result = _emit(ledger_3, dest)
    assert result.returncode == 1
    assert dest.read_text() == '{"extended": true}\n'
    result = _emit(ledger_3, dest, "--overwrite")
    assert result.returncode == 0, result.stdout + result.stderr
    dcc.validate_contract(json.loads(dest.read_text()))


def test_dangling_symlink_destination_counts_as_existing(ledger_3: Path) -> None:
    dest = ledger_3 / "data_contract.json"
    dest.symlink_to(ledger_3 / "nowhere.json")
    assert _emit(ledger_3, dest).returncode == 1
    assert dest.is_symlink() and not (ledger_3 / "nowhere.json").exists()


def test_new_destination_is_written_after_the_checks(ledger_3: Path) -> None:
    dest = ledger_3 / "data_contract.json"
    result = _emit(ledger_3, dest)
    assert result.returncode == 0, result.stdout + result.stderr
    dcc.validate_contract(json.loads(dest.read_text()))


def test_overwrite_needs_emit_contract(ledger_3: Path) -> None:
    code = dcc.main(["--ledger", str(ledger_3 / "samples.ledger.json"), "--overwrite"])
    assert code == 1


def test_pairing_must_cover_every_group_it_licenses(tmp_path: Path) -> None:
    table = tmp_path / "samples.tsv"
    rows = ["sample_id\tcondition\tdonor"]
    for donor in ("d1", "d2", "d3"):
        rows += [f"{donor}-c\tcontrol\t{donor}", f"{donor}-t\ttreated\t{donor}"]
    rows += ["d1-r\trescue\td1", "d2-r\trescue\td2"]
    table.write_text("\n".join(rows) + "\n")
    spec = {"schema": dcc.LEDGER_SCHEMA, "file": "samples.tsv", "sample_id": "sample_id",
            "group": "condition", "unit_of_replication": "donor", "nesting": ["donor"],
            "batch": [], "pairing": {"unit": "donor", "within": "condition",
                                     "levels": ["control", "treated"]}}
    contract = dcc.ledger_contract(spec, str(tmp_path))
    summaries, alerts = dcc.run_contract(contract, str(tmp_path))
    assert any(a["kind"] == "pairing" and a["blocking"] for a in alerts)


# Paired designs: the within-unit contrast against batch.

def _paired(tmp_path: Path, design: list[tuple], within: str = "condition",
            group: str = "condition", batch: tuple = ("run",)) -> tuple[list, list]:
    """Run the ledger contract for (donor, condition, visit, run) samples."""
    table = tmp_path / "samples.tsv"
    lines = ["sample_id\tcondition\tdonor\tvisit\trun"]
    lines += [f"s{i}\t{c}\t{d}\t{v}\t{r}" for i, (d, c, v, r) in enumerate(design)]
    table.write_text("\n".join(lines) + "\n")
    spec = {"schema": dcc.LEDGER_SCHEMA, "file": "samples.tsv", "sample_id": "sample_id",
            "group": group, "unit_of_replication": "donor", "nesting": ["donor"],
            "batch": list(batch), "pairing": {"unit": "donor", "within": within}}
    contract = dcc.ledger_contract(spec, str(tmp_path))
    return dcc.run_contract(contract, str(tmp_path))


def _paired_status(summaries: list) -> list:
    return [s["status"] for s in summaries if s["kind"] == "paired_batch"]


def test_a_ledger_pairing_checks_each_batch_within_units(ledger: Path) -> None:
    base = {**spec(ledger), "pairing": {"unit": "derivation", "within": "condition"}}
    checks = dcc.ledger_contract(base, str(ledger))["checks"]
    paired = [c for c in checks if c["kind"] == "paired_batch"]
    assert [c["batch"] for c in paired] == [["prep_date"], ["sequencing_run"],
                                            ["prep_date", "sequencing_run"]]
    assert all(c["unit"] == "derivation" and c["within"] == "condition" for c in paired)
    assert all("group" not in c for c in paired)  # within is the group itself
    unpaired = dcc.ledger_contract(spec(ledger), str(ledger))["checks"]
    assert not any(c["kind"] == "paired_batch" for c in unpaired)


def test_pairs_split_across_batches_in_step_block(tmp_path: Path) -> None:
    # d1: treated in r1, control in r2; d2: treated in r2, control in r3.
    # r2 holds both conditions, so the per-column check only warns, yet every
    # within-donor difference is also a batch difference.
    design = [("d1", "treated", "v", "r1"), ("d1", "control", "v", "r2"),
              ("d2", "treated", "v", "r2"), ("d2", "control", "v", "r3")]
    summaries, alerts = _paired(tmp_path, design)
    confounding = [s["status"] for s in summaries if s["kind"] == "batch_confounding"]
    assert confounding == ["warn"]
    assert _paired_status(summaries) == ["block"]
    alert = next(a for a in alerts if a["kind"] == "paired_batch")
    assert "treated" in alert["observed"] and any("d1" in e for e in alert["evidence"])


def test_counterbalanced_or_unsplit_pairs_pass(tmp_path: Path) -> None:
    swapped = [("d1", "treated", "v", "r1"), ("d1", "control", "v", "r2"),
               ("d2", "treated", "v", "r2"), ("d2", "control", "v", "r1")]
    assert _paired_status(_paired(tmp_path, swapped)[0]) == ["pass"]
    together = [("d1", "treated", "v", "r1"), ("d1", "control", "v", "r1"),
                ("d2", "treated", "v", "r2"), ("d2", "control", "v", "r2")]
    assert _paired_status(_paired(tmp_path, together)[0]) == ["pass"]


def test_a_within_factor_apart_from_the_group_is_checked(tmp_path: Path) -> None:
    # Genotype between donors, visit within: every baseline in r1 and every
    # week-4 sample in r2. Each run holds both genotypes, so batch against
    # the group passes, but the visit effect is the run effect.
    design = [(d, g, v, "r1" if v == "base" else "r2")
              for d, g in (("d1", "WT"), ("d2", "WT"), ("d3", "KO"), ("d4", "KO"))
              for v in ("base", "wk4")]
    summaries, _ = _paired(tmp_path, design, within="visit")
    assert [s["status"] for s in summaries if s["kind"] == "batch_confounding"] == ["pass"]
    checks = dcc.ledger_contract({"schema": dcc.LEDGER_SCHEMA, "file": "samples.tsv",
                                  "sample_id": "sample_id", "group": "condition",
                                  "unit_of_replication": "donor", "nesting": ["donor"],
                                  "batch": ["run"],
                                  "pairing": {"unit": "donor", "within": "visit"}},
                                 str(tmp_path))["checks"]
    assert next(c for c in checks if c["kind"] == "paired_batch")["group"] == "condition"
    assert _paired_status(summaries) == ["block"]


def test_the_group_by_within_interaction_is_checked(tmp_path: Path) -> None:
    # KO donors have both visits in one run, so the visit effect alone is
    # estimable; WT donors have each visit in its own run, so the WT visit
    # effect (and the genotype-by-visit interaction) is the run effect.
    design = [(d, "WT", v, "r1" if v == "base" else "r2") for d in ("d1", "d2")
              for v in ("base", "wk4")]
    design += [(d, "KO", v, "r3") for d in ("d3", "d4") for v in ("base", "wk4")]
    summaries, alerts = _paired(tmp_path, design, within="visit")
    assert _paired_status(summaries) == ["block"]
    alert = next(a for a in alerts if a["kind"] == "paired_batch")
    assert "WT" in alert["observed"] and "KO" not in alert["observed"]


def test_an_incomplete_pair_is_not_blamed_on_batch(tmp_path: Path) -> None:
    # No donor has both conditions: the pairing check blocks; the batch
    # check has no within-donor contrast to lose and stays quiet.
    design = [("d1", "treated", "v", "r1"), ("d2", "control", "v", "r2"),
              ("d3", "treated", "v", "r1"), ("d4", "control", "v", "r2")]
    summaries, alerts = _paired(tmp_path, design)
    assert any(a["kind"] == "pairing" and a["blocking"] for a in alerts)
    assert _paired_status(summaries) == ["pass"]


def test_local_labels_qualify_paired_batch_units_and_batches(ledger: Path) -> None:
    base = {**spec(ledger), "local_labels": True, "batch": ["line", "prep_date"],
            "nesting": ["site", "line", "derivation", "prep", "dish", "library"],
            "pairing": {"unit": "prep", "within": "condition"}}
    paired = [c for c in dcc.ledger_contract(base, str(ledger))["checks"]
              if c["kind"] == "paired_batch"]
    assert all(c["unit_scope"] == ["site", "line", "derivation"] for c in paired)
    assert paired[0]["batch_scopes"] == {"line": ["site"]}
    assert "batch_scopes" not in paired[1]


def test_a_few_hundred_paired_units_check_quickly() -> None:
    # Unit indicators are absorbed by differencing within units, so the rank
    # work grows with the cells, not with units squared.
    import random
    import time

    rng = random.Random(1)
    rows = [{"donor": f"d{i}", "g": "WK"[i % 2], "visit": v, "run": f"r{rng.randrange(12)}",
             "date": f"x{rng.randrange(10)}"} for i in range(300) for v in ("v0", "v1", "v2")]
    check = {"unit": "donor", "within": "visit", "group": "g", "batch": ["run", "date"]}
    start = time.perf_counter()
    assert dcc.check_paired_batch(check, [], rows) == []
    assert time.perf_counter() - start < 5


def test_pairs_mostly_split_in_step_warn(tmp_path: Path) -> None:
    # Eight donors with base in r1 and week 4 in r2, two with both visits in
    # r3: estimable (blocking stays exact), but the visit effect rests on two
    # donors, so the ledger's max_share warns.
    design = [(f"d{i}", "WK"[i % 2], v, "r1" if v == "base" else "r2")
              for i in range(8) for v in ("base", "wk4")]
    design += [(d, g, v, "r3") for d, g in (("d8", "W"), ("d9", "K")) for v in ("base", "wk4")]
    summaries, alerts = _paired(tmp_path, design, within="visit")
    assert _paired_status(summaries) == ["warn"]
    alert = next(a for a in alerts if a["kind"] == "paired_batch")
    assert not alert["blocking"] and "4 of 5" in alert["observed"]


def test_counterbalanced_splits_do_not_warn(tmp_path: Path) -> None:
    design = []
    for i in range(6):
        first, second = ("r1", "r2") if i % 2 else ("r2", "r1")
        design += [(f"d{i}", "treated", "v", first), (f"d{i}", "control", "v", second)]
    assert _paired_status(_paired(tmp_path, design)[0]) == ["pass"]
    lopsided = design[:]
    for i in range(6, 14):
        lopsided += [(f"d{i}", "treated", "v", "r1"), (f"d{i}", "control", "v", "r2")]
    assert _paired_status(_paired(tmp_path, lopsided)[0]) == ["warn"]


def test_ledger_paired_batch_sets_a_share_and_validates_it(ledger: Path) -> None:
    base = {**spec(ledger), "pairing": {"unit": "derivation", "within": "condition"}}
    paired = [c for c in dcc.ledger_contract(base, str(ledger))["checks"]
              if c["kind"] == "paired_batch"]
    assert all(c["max_share"] == 0.5 for c in paired)
    for bad in (0, 1.5, True, "0.5"):
        with pytest.raises(dcc.ContractError, match="max_share"):
            dcc.validate_contract({"schema": dcc.SCHEMA, "table": "t.tsv", "checks": [
                {"kind": "paired_batch", "unit": "u", "within": "w", "batch": ["b"],
                 "max_share": bad}]})


def test_a_crossover_groups_by_treatment_not_sequence() -> None:
    # AB/BA: period is the batch. Grouped by treatment, counterbalancing
    # separates treatment from period; grouped by sequence, each sequence's
    # treatment effect is period by design, which the docs warn about.
    rows = [{"subject": f"s{i}", "seq": "AB" if i % 2 else "BA", "treatment": t, "period": p}
            for i in range(6) for t, p in ((("A", "p1"), ("B", "p2")) if i % 2
                                            else (("B", "p1"), ("A", "p2")))]
    by_treatment = {"unit": "subject", "within": "treatment", "batch": ["period"],
                    "max_share": 0.5}
    assert dcc.check_paired_batch(by_treatment, [], rows) == []
    by_sequence = {**by_treatment, "group": "seq"}
    assert any(a["blocking"] for a in dcc.check_paired_batch(by_sequence, [], rows))
