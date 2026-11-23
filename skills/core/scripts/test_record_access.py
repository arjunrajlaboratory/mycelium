"""Row and feature access records (provenance item 7) and multi-file sources.

`record_ids` (in register_value.py) stores a count and fingerprint of the
ids a step used, never the ids; the R helper produces the identical record
through the same Python command line. A report number may also be linked to
several files and is stale when any of them changes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_report_values as crv  # noqa: E402
import register_value as rv  # noqa: E402


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs").mkdir()
    (root / "analysis" / "qc" / "outputs" / "table.csv").write_text("gene,ratio\ng1,1.4\n")
    (root / "analysis" / "qc" / "params.yaml").write_text("threshold: 1.3\n")
    return root


def run_py(project: Path, body: str) -> subprocess.CompletedProcess:
    script = project / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
                      "from register_value import register_value, record_ids, ValueRegistrationError\n"
                      + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=project / "analysis" / "qc")


def access(project: Path) -> dict:
    return json.loads((project / "analysis" / "qc" / "outputs" / "access.json").read_text())


def test_fingerprints_ignore_order_and_never_store_ids() -> None:
    a = rv.id_set_fingerprint(["c2", "c1", "c3"])
    b = rv.id_set_fingerprint(["c3", "c1", "c2"])
    assert a[0] == b[0] and a[3] != b[3]
    assert a[0] == hashlib.sha256(b"c1\nc2\nc3").hexdigest()
    assert rv.id_set_fingerprint(["c1", "c1"])[1:3] == (2, 1)


def test_record_ids_writes_counts_and_fingerprints_only(project: Path) -> None:
    result = run_py(project, "record_ids('cells_after_qc', ['AAAC-1', 'AAAG-1', 'AACT-1'], "
                             "axis='row', role='used')\n"
                             "record_ids('cells_dropped', ['AATT-1'], axis='row', role='excluded')")
    assert result.returncode == 0, result.stderr
    doc = access(project)
    assert doc["namespace"] == "qc"
    used, dropped = sorted(doc["records"], key=lambda r: r["role"], reverse=True)
    assert used["count"] == 3 and used["distinct"] == 3 and used["axis"] == "row"
    assert dropped["role"] == "excluded" and dropped["count"] == 1
    assert "AAAC-1" not in json.dumps(doc)
    assert used["computed_at"].startswith("scripts/probe.py:L")


def test_rerecording_a_name_replaces_it_and_validation_holds(project: Path) -> None:
    assert run_py(project, "record_ids('genes', ['g1'], axis='feature', role='used')\n"
                           "record_ids('genes', ['g1', 'g2'], axis='feature', role='used')"
                  ).returncode == 0
    (record,) = access(project)["records"]
    assert record["count"] == 2
    bad = run_py(project, "try:\n    record_ids('x', ['a'], axis='column', role='used')\n"
                          "except ValueRegistrationError as e:\n    print(e); raise SystemExit(3)")
    assert bad.returncode == 3 and "axis must be" in bad.stdout


def test_a_source_is_hashed_with_the_access_record(project: Path) -> None:
    assert run_py(project, "record_ids('genes', ['g1'], axis='feature', role='read', "
                           "source='outputs/table.csv')").returncode == 0
    (record,) = access(project)["records"]
    assert record["source"]["path"] == "analysis/qc/outputs/table.csv"
    assert record["source"]["sha256"] == hashlib.sha256(b"gene,ratio\ng1,1.4\n").hexdigest()


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R is not installed")
def test_the_r_helper_writes_the_identical_record(project: Path) -> None:
    script = project / "analysis" / "qc" / "scripts" / "probe.R"
    script.write_text(
        f'Sys.setenv(MYCELIUM_REGISTER_VALUE = "{HERE / "register_value.py"}",'
        f' MYCELIUM_PYTHON = "{sys.executable}")\n'
        f'source("{HERE / "record_access.R"}")\n'
        'record_ids("cells_after_qc", c("AAAG-1", "AAAC-1", "AACT-1"), axis = "row", role = "used")\n')
    result = subprocess.run(["Rscript", str(script)], capture_output=True, text=True, cwd=project)
    assert result.returncode == 0, result.stdout + result.stderr
    (record,) = access(project)["records"]
    assert record["id_set_sha256"] == rv.id_set_fingerprint(["AAAC-1", "AAAG-1", "AACT-1"])[0]
    assert record["count"] == 3 and record["computed_at"] == "scripts/probe.R"


# -- multi-file report sources ---------------------------------------------------


MULTI = ("register_value('n_up', 1, source=["
         "{'path': 'outputs/table.csv', 'locator': {'column': 'ratio'}}, "
         "{'path': 'params.yaml', 'locator': {'key': 'threshold'}}])")


def test_a_number_can_depend_on_several_files(project: Path) -> None:
    assert run_py(project, MULTI).returncode == 0
    (entry,) = json.loads((project / "analysis" / "qc" / "outputs" / "numbers.json").read_text())[
        "values"]
    assert [s["path"] for s in entry["sources"]] == ["analysis/qc/outputs/table.csv",
                                                    "analysis/qc/params.yaml"]
    assert "source" not in entry
    assert crv.check_numbers([entry], project) == []
    # The table is unchanged, but the threshold moved: the number is stale.
    (project / "analysis" / "qc" / "params.yaml").write_text("threshold: 1.5\n")
    (finding,) = crv.check_numbers([entry], project)
    assert finding["code"] == "stale-value" and finding["path"] == "analysis/qc/params.yaml"


def test_bad_source_lists_are_refused(project: Path) -> None:
    for body in ("register_value('n', 1, source=[])",
                 "register_value('n', 1, source=[{'path': 'params.yaml', 'locator': 'a'}, "
                 "{'path': 'params.yaml', 'locator': 'b'}])"):
        result = run_py(project, f"try:\n    {body}\nexcept ValueRegistrationError as e:\n"
                                 "    print(e); raise SystemExit(3)")
        assert result.returncode == 3, result.stderr
    assert crv.check_numbers([{"id": "n", "value": 1, "sources": []}], project)[0]["code"] == \
        "source-invalid"


# -- review regressions ------------------------------------------------------


def test_ids_must_be_nonempty_strings() -> None:
    for bad in ([100000], [""], [None]):
        with pytest.raises(rv.AccessRecordError):
            rv.id_set_fingerprint(bad)
    assert rv.id_set_fingerprint([])[1] == 0  # an empty set is allowed


def _cli(root: Path, ids: Path, axis: str = "row", name: str = "cells"):
    return subprocess.run([sys.executable, str(HERE / "register_value.py"), "record-ids",
                           "--analysis-root", str(root), "--name", name, "--axis", axis,
                           "--role", "used", "--ids-file", str(ids)], capture_output=True, text=True)


def test_the_cli_never_writes_a_malformed_record(project: Path, tmp_path: Path) -> None:
    root = project / "analysis" / "qc"
    ids = tmp_path / "ids.txt"
    ids.write_text("a\nb\n")
    assert _cli(root, ids).returncode == 0
    assert _cli(root, ids, axis="rows").returncode == 1
    (record,) = access(project)["records"]
    assert record["axis"] == "row" and record["count"] == 2
    ids.write_text("a\n\nb\n")
    assert _cli(root, ids, name="c2").returncode == 1  # a blank id is refused, never dropped
    assert _cli(project / "analysis" / "nope", ids).returncode == 2


def test_access_sources_never_keep_a_locator(project: Path) -> None:
    assert run_py(project, "record_ids('genes', ['g1'], axis='feature', role='read', "
                           "source={'path': 'outputs/table.csv', 'locator': {'row': 'S01'}})"
                  ).returncode == 0
    (record,) = access(project)["records"]
    assert "locator" not in record["source"] and "S01" not in json.dumps(record)


def test_a_config_changed_after_it_was_read_is_refused(project: Path) -> None:
    body = ("from register_value import snapshot_source\n"
            "cfg = snapshot_source('params.yaml', {'key': 'threshold'})\n"
            "open('params.yaml', 'w').write('threshold: 9\\n')\n"
            "try:\n"
            "    register_value('n', 1, source=[{'path': 'outputs/table.csv', 'locator': 'r'},"
            " cfg])\n"
            "except ValueRegistrationError as e:\n    print(e); raise SystemExit(3)")
    result = run_py(project, body)
    assert result.returncode == 3 and "changed after it was read" in result.stdout


def test_a_null_member_of_sources_fails() -> None:
    (finding,) = crv.check_numbers([{"id": "n", "value": 1, "sources": [None]}], Path("/"))
    assert finding["code"] == "source-invalid" and finding["severity"] == "error"


def _r_project(tmp_path: Path) -> Path:
    project = tmp_path / "my project"  # a space in the path
    lib = project / "analysis" / "_lib"
    lib.mkdir(parents=True)
    (project / ".living").mkdir()
    (project / "analysis" / "qc" / "scripts").mkdir(parents=True)
    shutil.copy(HERE / "register_value.py", lib / "register_value.py")
    shutil.copy(HERE / "record_access.R", lib / "record_access.R")
    return project


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R is not installed")
def test_the_r_helper_works_from_inside_the_analysis_with_spaces(tmp_path: Path) -> None:
    project = _r_project(tmp_path)
    script = project / "analysis" / "qc" / "scripts" / "probe.R"
    script.write_text(
        f'Sys.setenv(MYCELIUM_PYTHON = "{sys.executable}")\n'
        'source("../_lib/record_access.R")\n'
        'setwd(tempdir())\n'  # a later setwd must not move the record
        'record_ids("cells", c("b", "a"), axis = "row", role = "used")\n'
        'record_ids("genes", c("g1"), axis = "feature", role = "used", namespace = "qc")\n')
    result = subprocess.run(["Rscript", "scripts/probe.R"], capture_output=True, text=True,
                            cwd=project / "analysis" / "qc")
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads((project / "analysis" / "qc" / "outputs" / "access.json").read_text())
    assert sorted(r["name"] for r in doc["records"]) == ["cells", "genes"]
    assert not (project / "analysis" / "qc" / "analysis").exists()


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R is not installed")
def test_the_r_helper_refuses_numbers_and_an_old_helper(tmp_path: Path) -> None:
    project = _r_project(tmp_path)
    script = project / "analysis" / "qc" / "scripts" / "probe.R"
    script.write_text(f'Sys.setenv(MYCELIUM_PYTHON = "{sys.executable}")\n'
                      'source("analysis/_lib/record_access.R")\n'
                      'record_ids("cells", c(100000, 100001), axis = "row", role = "used")\n')
    numeric = subprocess.run(["Rscript", str(script)], capture_output=True, text=True, cwd=project)
    assert numeric.returncode != 0 and "character" in numeric.stderr
    (project / "analysis" / "_lib" / "register_value.py").write_text("print('old')\n")
    script.write_text(f'Sys.setenv(MYCELIUM_PYTHON = "{sys.executable}")\n'
                      'source("analysis/_lib/record_access.R")\n'
                      'record_ids("cells", c("a"), axis = "row", role = "used")\n')
    old = subprocess.run(["Rscript", str(script)], capture_output=True, text=True, cwd=project)
    assert old.returncode != 0 and "wrote no record" in old.stderr


TABLE = "metric,count\npassing,812\nfailing,40\n"


@pytest.fixture()
def project_2(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs" / "tables").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "reports").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text(TABLE)
    return root


def run_py_2(project_2: Path, body: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    script = project_2 / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(
        f"import os, sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import (register_value, record_ids, read_source, "
        "snapshot_source, ValueRegistrationError, AccessRecordError)\n" + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=cwd or project_2 / "analysis" / "qc")


@pytest.mark.parametrize("helper", [
    'text, cfg = read_source("params.yaml", {"key": "t"}, mode="r")',
    'cfg = snapshot_source("params.yaml", {"key": "t"})',
])
def test_a_cwd_change_cannot_relink_an_identical_file(project_2: Path, helper: str) -> None:
    qc = project_2 / "analysis" / "qc"
    (qc / "params.yaml").write_text("t: 1\n")
    (qc / "scripts" / "params.yaml").write_text("t: 1\n")  # same bytes, other file
    result = run_py_2(project_2, helper + "\nos.chdir('scripts')\n"
                    "register_value('n', 1, source=cfg)")
    assert result.returncode == 0, result.stderr
    (entry,) = json.loads((qc / "outputs" / "numbers.json").read_text())["values"]
    assert entry["source"]["path"] == "analysis/qc/params.yaml"


@pytest.mark.parametrize("call", [
    "record_ids('cells', ['a', 'b'], axis='row', role='used')",
    "register_value('n', 1)",
])
def test_a_symlinked_outputs_is_refused(project_2: Path, tmp_path: Path, call: str) -> None:
    import shutil

    outputs = project_2 / "analysis" / "qc" / "outputs"
    shutil.rmtree(outputs)
    outside = tmp_path / "outside"
    outside.mkdir()
    outputs.symlink_to(outside, target_is_directory=True)
    result = run_py_2(project_2, call)
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("name", ["access.json.lock", "numbers.json.lock"])
def test_a_symlinked_lock_is_refused(project_2: Path, tmp_path: Path, name: str) -> None:
    victim = tmp_path / "outside.lock"
    (project_2 / "analysis" / "qc" / "outputs" / name).symlink_to(victim)
    call = ("record_ids('cells', ['a'], axis='row', role='used')" if name.startswith("access")
            else "register_value('n', 1)")
    result = run_py_2(project_2, call)
    assert result.returncode != 0
    assert not victim.exists()


@pytest.mark.parametrize("ids", ["cell_1", b"cell_1"])
def test_a_bare_string_of_ids_is_refused(ids) -> None:
    with pytest.raises(rv.AccessRecordError, match="wrap it in a list"):
        rv.id_set_fingerprint(ids)


def _cli_2(root: Path, ids: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "register_value.py"), "record-ids",
                           "--analysis-root", str(root), "--name", "cells", "--axis", "row",
                           "--role", "used", "--ids-file", str(ids), *extra],
                          capture_output=True, text=True)


@pytest.mark.parametrize("extra", [("--run-pid", "42"), ("--run-started-at", "2026-11-22T10:00:00Z")])
def test_half_a_run_identity_is_refused(project_2: Path, tmp_path: Path, extra) -> None:
    ids = tmp_path / "ids.txt"
    ids.write_text("a\n")
    result = _cli_2(project_2 / "analysis" / "qc", ids, *extra)
    assert result.returncode == 2
    assert not (project_2 / "analysis" / "qc" / "outputs" / "access.json").exists()


def test_a_full_run_identity_is_schema_valid(project_2: Path, tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    ids = tmp_path / "ids.txt"
    ids.write_text("a\n")
    result = _cli_2(project_2 / "analysis" / "qc", ids, "--run-pid", "42",
                  "--run-started-at", "2026-11-22T10:00:00Z")
    assert result.returncode == 0, result.stderr
    schema = json.loads((HERE.parent / "references" / "provenance-schema.json").read_text())
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/access_fragment",
                                                 "$defs": schema["$defs"]})
    doc = json.loads((project_2 / "analysis" / "qc" / "outputs" / "access.json").read_text())
    assert [e.message for e in validator.iter_errors(doc)] == []
    assert doc["records"][0]["run"] == {"pid": 42, "process_started_at": "2026-11-22T10:00:00Z"}


@pytest.fixture()
def project_3(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs").mkdir()
    (root / "analysis" / "qc" / "cells.csv").write_text("cell\nc1\nc2\n")
    return root


def access_path(project_3: Path) -> Path:
    return project_3 / "analysis" / "qc" / "outputs" / "access.json"


USER_AUDIT = {
    "generated_by": "qc_audit.py",
    "parameters": {"min_genes": 200},
    "records": [{"name": "cells_after_qc", "kept": 812, "note": "hand-checked"}],
}


def run_py_3(project_3: Path, body: str) -> subprocess.CompletedProcess:
    script = project_3 / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import (register_value, record_ids, read_source, "
        "snapshot_source, ValueRegistrationError, AccessRecordError)\n" + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=project_3 / "analysis" / "qc")


REFUSE = ("try:\n"
          "    record_ids('cells_after_qc', ['c1', 'c2'], axis='row', role='used')\n"
          "except AccessRecordError as e:\n"
          "    print(e); raise SystemExit(3)\n")


@pytest.mark.parametrize("existing", [
    USER_AUDIT,  # someone else's audit file with its own metadata
    {"namespace": "qc", "records": []},  # the right shape but no marker
    {"schema": "mycelium.access.v1", "namespace": "qc", "records": [],
     "generated_by": "qc_audit.py"},  # the marker, plus keys the format lacks
    {"schema": "other.format.v1", "namespace": "qc", "records": []},
])
def test_record_ids_refuses_an_access_json_it_did_not_write(project_3: Path, existing) -> None:
    path = access_path(project_3)
    path.write_text(json.dumps(existing, indent=1))
    before = path.read_bytes()
    result = run_py_3(project_3, REFUSE)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "refusing to overwrite" in result.stdout
    assert path.read_bytes() == before


def test_the_command_line_refuses_it_too(project_3: Path, tmp_path: Path) -> None:
    path = access_path(project_3)
    path.write_text(json.dumps(USER_AUDIT))
    before = path.read_bytes()
    ids = tmp_path / "ids.txt"
    ids.write_text("c1\nc2\n")
    result = subprocess.run(
        [sys.executable, str(HERE / "register_value.py"), "record-ids", "--analysis-root",
         str(project_3 / "analysis" / "qc"), "--name", "cells_after_qc", "--axis", "row",
         "--role", "used", "--ids-file", str(ids)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "schema marker" in result.stderr
    assert path.read_bytes() == before


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R is not installed")
def test_the_r_helper_fails_on_it(project_3: Path) -> None:
    lib = project_3 / "analysis" / "_lib"
    lib.mkdir()
    shutil.copy(HERE / "register_value.py", lib / "register_value.py")
    shutil.copy(HERE / "record_access.R", lib / "record_access.R")
    path = access_path(project_3)
    path.write_text(json.dumps(USER_AUDIT))
    before = path.read_bytes()
    script = project_3 / "analysis" / "qc" / "scripts" / "probe.R"
    script.write_text(f'Sys.setenv(MYCELIUM_PYTHON = "{sys.executable}")\n'
                      'source("../_lib/record_access.R")\n'
                      'record_ids("cells_after_qc", c("c1"), axis = "row", role = "used")\n')
    result = subprocess.run(["Rscript", "scripts/probe.R"], capture_output=True, text=True,
                            cwd=project_3 / "analysis" / "qc")
    assert result.returncode != 0
    assert "schema marker" in result.stdout + result.stderr
    assert path.read_bytes() == before


SCHEMA = json.loads((HERE.parent / "references" / "provenance-schema.json").read_text())


def test_a_marked_file_keeps_updating_and_is_schema_valid(project_3: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    body = ("record_ids('cells', ['c1', 'c2'], axis='row', role='used')\n"
            "record_ids('genes', ['g1'], axis='feature', role='used')\n"
            "record_ids('cells', ['c1'], axis='row', role='used')\n")
    result = run_py_3(project_3, body)
    assert result.returncode == 0, result.stderr
    doc = json.loads(access_path(project_3).read_text())
    assert doc["schema"] == rv.ACCESS_SCHEMA == "mycelium.access.v1"
    assert [(r["name"], r["count"]) for r in doc["records"]] == [("cells", 1), ("genes", 1)]
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/access_fragment",
                                                 "$defs": SCHEMA["$defs"]})
    assert list(validator.iter_errors(doc)) == []
    assert list(validator.iter_errors({"namespace": "qc", "records": []})) != []


def test_a_097_numbers_json_without_a_marker_still_updates(project_3: Path) -> None:
    numbers = project_3 / "analysis" / "qc" / "outputs" / "numbers.json"
    numbers.write_text(json.dumps({"namespace": "qc", "values": [
        {"key": "n_old", "value": 3, "provenance": "x", "computed_at": "run.py:L1"}]}))
    result = run_py_3(project_3, "register_value('n_new', 5)")
    assert result.returncode == 0, result.stderr
    doc = json.loads(numbers.read_text())
    assert [v["key"] for v in doc["values"]] == ["n_new", "n_old"]
    assert "schema" not in doc


@pytest.mark.parametrize("helper", [
    "_, src = read_source('cells.csv')",
    "src = snapshot_source('cells.csv')",
])
def test_record_ids_refuses_a_source_that_changed_after_it_was_read(project_3: Path,
                                                                    helper: str) -> None:
    body = (helper + "\n"
            "open('cells.csv', 'w').write('cell\\nc9\\n')  # replaced after the read\n"
            "try:\n"
            "    record_ids('cells', ['c1', 'c2'], axis='row', role='read', source=src)\n"
            "except AccessRecordError as e:\n"
            "    print(e); raise SystemExit(3)\n")
    result = run_py_3(project_3, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "changed after it was read" in result.stdout
    assert not access_path(project_3).exists()


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("helper", [
    "_, src = read_source('cells.csv', 'whole file')",
    "src = snapshot_source('cells.csv')",
])
def test_an_unchanged_read_time_source_records_that_digest(project_3: Path, helper: str) -> None:
    result = run_py_3(project_3, helper + "\n"
                    "record_ids('cells', ['c1', 'c2'], axis='row', role='read', source=src)\n")
    assert result.returncode == 0, result.stderr
    (record,) = json.loads(access_path(project_3).read_text())["records"]
    assert record["source"]["sha256"] == sha("cell\nc1\nc2\n")
    assert record["source"]["path"] == "analysis/qc/cells.csv"
    assert "locator" not in record["source"]


def test_a_source_with_unknown_keys_is_refused(project_3: Path) -> None:
    with pytest.raises(rv.AccessRecordError, match="unknown source keys"):
        rv._access_source({"path": "cells.csv", "row": "c1"}, project_3 / "analysis" / "qc")


@pytest.fixture()
def analysis(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    analysis_root = root / "analysis" / "qc"
    (analysis_root / "scripts").mkdir(parents=True)
    return analysis_root


def _race(monkeypatch, create) -> None:
    """Another process creates ``outputs`` between the existence check and
    this process's mkdir."""
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name == "outputs":
            create(self, real_mkdir)
            raise FileExistsError(17, "File exists", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)


@pytest.mark.parametrize("error", [rv.ValueRegistrationError, rv.AccessRecordError])
def test_outputs_created_concurrently_is_tolerated(monkeypatch, analysis: Path, error) -> None:
    _race(monkeypatch, lambda path, real_mkdir: real_mkdir(path))
    target = rv._outputs_file(analysis, "numbers.json", error=error)
    assert target == analysis / "outputs" / "numbers.json"
    assert (analysis / "outputs").is_dir()


def test_symlink_planted_during_creation_is_refused(monkeypatch, analysis: Path,
                                                    tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _race(monkeypatch,
          lambda path, real_mkdir: path.symlink_to(outside, target_is_directory=True))
    with pytest.raises(rv.ValueRegistrationError, match="symlink"):
        rv._outputs_file(analysis, "numbers.json")


def test_file_planted_during_creation_is_refused(monkeypatch, analysis: Path) -> None:
    _race(monkeypatch, lambda path, real_mkdir: path.write_text("x"))
    with pytest.raises(rv.ValueRegistrationError, match="not a directory"):
        rv._outputs_file(analysis, "numbers.json")
