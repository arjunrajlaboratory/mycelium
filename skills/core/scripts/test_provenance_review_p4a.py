"""Codex P1 pass 4a on the provenance PR.

1. ``record_ids`` never rewrites an access.json Mycelium did not write: an
   existing file must carry the ``mycelium.access.v1`` marker and no other
   top-level keys, or the call fails (Python, command line and R) and leaves
   the file untouched. numbers.json from 0.9.7 has no marker and still
   updates.
2. ``record_ids(source=...)`` given a ``read_source``/``snapshot_source``
   entry keeps its read-time digest and fails if the file changed since,
   instead of silently linking the ids to the replacement's hash.
3. ``check_report_values --explain`` resolves a write whose hash was pending
   at Post by file identity before attributing, and says "outside the
   record" only when the record is complete for the path; otherwise it
   reports ``producer-unknown``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_report_values as crv  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import register_value as rv  # noqa: E402
import test_lineage_index as tli  # noqa: E402

SCHEMA = json.loads((HERE.parent / "references" / "provenance-schema.json").read_text())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs").mkdir()
    (root / "analysis" / "qc" / "cells.csv").write_text("cell\nc1\nc2\n")
    return root


def access_path(project: Path) -> Path:
    return project / "analysis" / "qc" / "outputs" / "access.json"


def run_py(project: Path, body: str) -> subprocess.CompletedProcess:
    script = project / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import (register_value, record_ids, read_source, "
        "snapshot_source, ValueRegistrationError, AccessRecordError)\n" + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=project / "analysis" / "qc")


# -- 1. a user-owned access.json is never rewritten -------------------------------

USER_AUDIT = {
    "generated_by": "qc_audit.py",
    "parameters": {"min_genes": 200},
    "records": [{"name": "cells_after_qc", "kept": 812, "note": "hand-checked"}],
}

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
def test_record_ids_refuses_an_access_json_it_did_not_write(project: Path, existing) -> None:
    path = access_path(project)
    path.write_text(json.dumps(existing, indent=1))
    before = path.read_bytes()
    result = run_py(project, REFUSE)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "refusing to overwrite" in result.stdout
    assert path.read_bytes() == before


def test_the_command_line_refuses_it_too(project: Path, tmp_path: Path) -> None:
    path = access_path(project)
    path.write_text(json.dumps(USER_AUDIT))
    before = path.read_bytes()
    ids = tmp_path / "ids.txt"
    ids.write_text("c1\nc2\n")
    result = subprocess.run(
        [sys.executable, str(HERE / "register_value.py"), "record-ids", "--analysis-root",
         str(project / "analysis" / "qc"), "--name", "cells_after_qc", "--axis", "row",
         "--role", "used", "--ids-file", str(ids)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "schema marker" in result.stderr
    assert path.read_bytes() == before


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R is not installed")
def test_the_r_helper_fails_on_it(project: Path) -> None:
    lib = project / "analysis" / "_lib"
    lib.mkdir()
    shutil.copy(HERE / "register_value.py", lib / "register_value.py")
    shutil.copy(HERE / "record_access.R", lib / "record_access.R")
    path = access_path(project)
    path.write_text(json.dumps(USER_AUDIT))
    before = path.read_bytes()
    script = project / "analysis" / "qc" / "scripts" / "probe.R"
    script.write_text(f'Sys.setenv(MYCELIUM_PYTHON = "{sys.executable}")\n'
                      'source("../_lib/record_access.R")\n'
                      'record_ids("cells_after_qc", c("c1"), axis = "row", role = "used")\n')
    result = subprocess.run(["Rscript", "scripts/probe.R"], capture_output=True, text=True,
                            cwd=project / "analysis" / "qc")
    assert result.returncode != 0
    assert "schema marker" in result.stdout + result.stderr
    assert path.read_bytes() == before


def test_a_marked_file_keeps_updating_and_is_schema_valid(project: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    body = ("record_ids('cells', ['c1', 'c2'], axis='row', role='used')\n"
            "record_ids('genes', ['g1'], axis='feature', role='used')\n"
            "record_ids('cells', ['c1'], axis='row', role='used')\n")
    result = run_py(project, body)
    assert result.returncode == 0, result.stderr
    doc = json.loads(access_path(project).read_text())
    assert doc["schema"] == rv.ACCESS_SCHEMA == "mycelium.access.v1"
    assert [(r["name"], r["count"]) for r in doc["records"]] == [("cells", 1), ("genes", 1)]
    validator = jsonschema.Draft202012Validator({"$ref": "#/$defs/access_fragment",
                                                 "$defs": SCHEMA["$defs"]})
    assert list(validator.iter_errors(doc)) == []
    assert list(validator.iter_errors({"namespace": "qc", "records": []})) != []


def test_a_097_numbers_json_without_a_marker_still_updates(project: Path) -> None:
    numbers = project / "analysis" / "qc" / "outputs" / "numbers.json"
    numbers.write_text(json.dumps({"namespace": "qc", "values": [
        {"key": "n_old", "value": 3, "provenance": "x", "computed_at": "run.py:L1"}]}))
    result = run_py(project, "register_value('n_new', 5)")
    assert result.returncode == 0, result.stderr
    doc = json.loads(numbers.read_text())
    assert [v["key"] for v in doc["values"]] == ["n_new", "n_old"]
    assert "schema" not in doc


# -- 2. a read-time source keeps its digest ----------------------------------------


@pytest.mark.parametrize("helper", [
    "_, src = read_source('cells.csv')",
    "src = snapshot_source('cells.csv')",
])
def test_record_ids_refuses_a_source_that_changed_after_it_was_read(project: Path,
                                                                    helper: str) -> None:
    body = (helper + "\n"
            "open('cells.csv', 'w').write('cell\\nc9\\n')  # replaced after the read\n"
            "try:\n"
            "    record_ids('cells', ['c1', 'c2'], axis='row', role='read', source=src)\n"
            "except AccessRecordError as e:\n"
            "    print(e); raise SystemExit(3)\n")
    result = run_py(project, body)
    assert result.returncode == 3, result.stdout + result.stderr
    assert "changed after it was read" in result.stdout
    assert not access_path(project).exists()


@pytest.mark.parametrize("helper", [
    "_, src = read_source('cells.csv', 'whole file')",
    "src = snapshot_source('cells.csv')",
])
def test_an_unchanged_read_time_source_records_that_digest(project: Path, helper: str) -> None:
    result = run_py(project, helper + "\n"
                    "record_ids('cells', ['c1', 'c2'], axis='row', role='read', source=src)\n")
    assert result.returncode == 0, result.stderr
    (record,) = json.loads(access_path(project).read_text())["records"]
    assert record["source"]["sha256"] == sha("cell\nc1\nc2\n")
    assert record["source"]["path"] == "analysis/qc/cells.csv"
    assert "locator" not in record["source"]


def test_a_source_with_unknown_keys_is_refused(project: Path) -> None:
    with pytest.raises(rv.AccessRecordError, match="unknown source keys"):
        rv._access_source({"path": "cells.csv", "row": "c1"}, project / "analysis" / "qc")


# -- 3. --explain resolves pending hashes and never over-claims --------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def _identity(path: Path) -> dict:
    st = os.stat(path)
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


def _pending(repo: Path, call: str, rel: str, identity: dict, *, pre: int, post: int) -> dict:
    record = tli.observation(repo, call, modified=[(rel, None)], pre=pre, post=post)
    record["changes"]["modified"][0]["after"]["identity"] = identity
    return record


def test_a_pending_later_write_is_resolved_by_identity_and_credited(repo: Path) -> None:
    res = repo / "out" / "res.csv"
    res.write_text("count\n812\n")
    older = tli.observation(repo, "c1", modified=[("out/res.csv", sha("count\n700\n"))],
                            pre=1, post=2)
    tli.write_store(repo, [older, _pending(repo, "c2", "out/res.csv", _identity(res),
                                           pre=3, post=4)])
    lineage = crv._explain(li.load(repo, write=False), "out/res.csv", sha("count\n812\n"))
    assert lineage["status"] == "written-by", lineage
    assert "c2" in lineage["write_call"]
    assert lineage["hash_origin"] == "query-identity-match"
    assert lineage["verdict"] == "current-in-record"


def test_an_unresolvable_pending_write_is_producer_unknown(repo: Path) -> None:
    res = repo / "out" / "res.csv"
    res.write_text("count\n812\n")
    seen = _identity(res)
    res.write_text("count\n8120\n")  # a different identity: the pending hash stays unknown
    older = tli.observation(repo, "c1", modified=[("out/res.csv", sha("count\n700\n"))],
                            pre=1, post=2)
    tli.write_store(repo, [older, _pending(repo, "c2", "out/res.csv", seen, pre=3, post=4)])
    lineage = crv._explain(li.load(repo, write=False), "out/res.csv", sha("count\n8120\n"))
    assert lineage["status"] == "producer-unknown", lineage
    assert "write-hash-unknown" in lineage["reasons"]
    assert "outside the record" not in json.dumps(lineage)


def test_an_applicable_gap_is_producer_unknown(repo: Path) -> None:
    (repo / "out" / "x").write_text("edited")
    tli.write_store(repo, [tli.observation(repo, "c1", created=[("out/x", sha("v1"))],
                                           pre=1, post=2)])
    store = io.observations_path(repo / ".mycelium", "claude", "s1")
    with store.open("ab") as handle:
        handle.write(b'{"kind": "observation", "x": "\xff\xfe"}\n')  # a lost record: a gap
    lineage = crv._explain(li.load(repo, write=False), "out/x", sha("edited"))
    assert lineage["status"] == "producer-unknown", lineage
    assert "gap" in lineage["reasons"]


def test_restored_bytes_with_a_later_unknown_write_are_producer_unknown(repo: Path) -> None:
    res = repo / "out" / "res.csv"
    res.write_text("v1")
    seen = _identity(res)
    res.write_text("v1")  # rewritten after the pending write: its identity no longer matches
    os.utime(res, ns=(seen["mtime_ns"] + 10**9, seen["mtime_ns"] + 10**9))
    tli.write_store(repo, [
        tli.observation(repo, "c1", modified=[("out/res.csv", sha("v1"))], pre=1, post=2),
        tli.observation(repo, "c2", modified=[("out/res.csv", sha("v2"))], pre=3, post=4),
        _pending(repo, "c3", "out/res.csv", seen, pre=5, post=6),
    ])
    lineage = crv._explain(li.load(repo, write=False), "out/res.csv", sha("v1"))
    assert lineage["status"] == "producer-unknown", lineage


def test_a_complete_record_still_says_outside_the_record(repo: Path) -> None:
    (repo / "out" / "x").write_text("edited by hand")
    tli.write_store(repo, [tli.observation(repo, "c1", created=[("out/x", sha("v1"))],
                                           pre=1, post=2)])
    lineage = crv._explain(li.load(repo, write=False), "out/x", sha("edited by hand"))
    assert lineage["status"] == "current-bytes-not-in-record"
