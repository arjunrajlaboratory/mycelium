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
    assert record["count"] == 3 and record["computed_at"] == "probe.R"


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
