"""The provenance schema file describes the records Mycelium really writes.

Real outputs produced here (a v2 manifest from the observer and extractor,
index query results, a report fragment with a hash-linked number, checker
findings, the ledger template) must validate against their entry points in
skills/core/references/provenance-schema.json, and malformed records must
not. CI sets MYCELIUM_REQUIRE_JSONSCHEMA=1, so a missing validator fails
there instead of skipping.
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

SCHEMA_PATH = HERE.parent / "references" / "provenance-schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text())

try:
    import jsonschema
except ImportError:  # pragma: no cover - CI requires it
    jsonschema = None
    if os.environ.get("MYCELIUM_REQUIRE_JSONSCHEMA") == "1":
        raise
pytestmark = [
    pytest.mark.platform,
    pytest.mark.skipif(jsonschema is None, reason="jsonschema is not installed"),
]

ENTRY_POINTS = {
    "manifest_v1", "manifest_v2", "analysis_event", "data_command", "skip", "observation",
    "file_record", "step", "lineage_index", "index_query_result", "report_fragment",
    "report_number", "value_source", "report_finding", "sample_ledger",
}


def validator(entry: str):
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{entry}", "$defs": SCHEMA["$defs"]})


def valid(entry: str, document) -> list[str]:
    return [error.message for error in validator(entry).iter_errors(document)]


def test_the_schema_is_well_formed_and_exposes_its_entry_points() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert ENTRY_POINTS <= set(SCHEMA["$defs"])


@pytest.fixture(scope="module")
def produced(tmp_path_factory) -> dict:
    """One realistic project run: two observed calls rewrite an output, a
    number is registered against the first version, the index is queried."""
    import extract_data_lineage as stop
    import lineage_index as li
    import test_lineage_index as tli

    root = tmp_path_factory.mktemp("proj") / "repo"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "data").mkdir()
    (root / "out").mkdir()
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "data" / "in.csv").write_text("a\n1\n")
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out/res.csv')\n")
    import time

    time.sleep(0.05)
    tli._run_call(root, "t1", "OMP_NUM_THREADS=2 python fit.py --seed 3",
                  lambda: (root / "out" / "res.csv").write_text("v1\n"))
    script = root / "analysis" / "qc" / "scripts" / "reg.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(HERE)!r})\nfrom register_value import register_value\n"
        f"register_value('n', 1, source={{'path': {str(root / 'out' / 'res.csv')!r}, "
        "'locator': {'row': 0}})\n"
        "register_value('plain', 2)\n")
    subprocess.run([sys.executable, str(script)], check=True, cwd=root / "analysis" / "qc")
    tli._run_call(root, "t2", "python fit.py", lambda: (root / "out" / "res.csv").write_text("v2\n"))
    manifest = stop.extract(root / "events.tmp", "sess", str(root), root / ".mycelium" / "io" / "obs")
    stop.write_manifest_atomic(root / ".living" / "log" / "data-lineage" / "sess.json", manifest)
    index = li.load(root)
    fragment = json.loads((root / "analysis" / "qc" / "outputs" / "numbers.json").read_text())
    findings = json.loads(subprocess.run(
        [sys.executable, str(HERE / "check_report_values.py"), "--fragment",
         str(root / "analysis" / "qc" / "outputs" / "numbers.json"), "--json"],
        capture_output=True, text=True).stdout)["findings"]
    return {
        "manifest": manifest,
        "by_hash": li.query_hash(index, hashlib.sha256(b"v1\n").hexdigest()),
        "by_path": li.query_path(index, "out/res.csv"),
        "summary": li.summary(index),
        "fragment": fragment,
        "findings": findings,
    }


def test_a_real_v2_manifest_validates(produced: dict) -> None:
    manifest = produced["manifest"]
    assert valid("manifest_v2", manifest) == []
    action = manifest["actions"][0]
    assert action["step"]["env"] == {"OMP_NUM_THREADS": "2"}
    for observation in manifest["observations"]:
        assert valid("observation", observation) == []


def test_real_index_results_validate(produced: dict) -> None:
    assert valid("index_query_result", json.loads(json.dumps(produced["by_hash"],
                                                              default=str))) == []
    assert valid("index_query_result", json.loads(json.dumps(produced["by_path"],
                                                              default=str))) == []
    assert valid("lineage_index", produced["summary"]) == []


def test_real_report_records_validate(produced: dict) -> None:
    assert valid("report_fragment", produced["fragment"]) == []
    codes = {f["code"] for f in produced["findings"]}
    assert codes == {"stale-value", "unchecked-value"}
    for finding in produced["findings"]:
        assert valid("report_finding", finding) == []


def test_the_ledger_template_validates() -> None:
    template = json.loads((HERE.parent / "templates" / "samples.ledger.json").read_text())
    assert valid("sample_ledger", template) == []


def test_a_v1_manifest_validates_as_v1_not_v2() -> None:
    v1 = {"session_id": "old", "repo_root": "/r", "actions": [
        {"ts": "2026-10-01T00:00:00Z", "inputs": [{"path": "/r/a", "sha256": "a" * 64}],
         "outputs": [], "filters_detected": [".query(x)"]}], "summary": {}}
    assert valid("manifest_v1", v1) == []
    assert valid("manifest_v2", v1) != []
    assert valid("manifest_v1", {**v1, "schema": "mycelium.data_lineage.manifest.v2"}) != []


@pytest.mark.parametrize(
    ("entry", "document"),
    [
        ("analysis_event", {}),
        ("file_record", {"path": "/a", "sha256": "NOT-A-HASH"}),
        ("value_source", {"path": "x.csv", "sha256": "a" * 64}),
        ("value_source", {"path": "x.csv", "sha256": "a" * 64, "locator": {"colour": "red"}}),
        ("observation", {"kind": "observation", "observation_id": "c", "status": "fine"}),
        ("sample_ledger", {"schema": "mycelium.sample_ledger.v1", "file": "s.tsv"}),
        ("report_finding", {"schema": "mycelium.report_finding.v1", "code": "ok",
                            "severity": "error", "message": "m"}),
        ("step", {"env": {"X": 5}}),
    ],
)
def test_malformed_records_are_rejected(entry: str, document) -> None:
    assert valid(entry, document) != []


def test_access_records_and_multi_source_numbers_validate(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "t.csv").write_text("a\n")
    (root / "analysis" / "qc" / "c.yaml").write_text("k: 1\n")
    script = root / "analysis" / "qc" / "scripts" / "s.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import register_value, record_ids\n"
        "record_ids('cells', ['a', 'b'], axis='row', role='used', source='t.csv', ordered=True)\n"
        "register_value('n', 2, source=[{'path': 't.csv', 'locator': 'rows'},"
        " {'path': 'c.yaml', 'locator': {'key': 'k'}}])\n")
    subprocess.run([sys.executable, str(script)], check=True, cwd=root / "analysis" / "qc")
    out = root / "analysis" / "qc" / "outputs"
    assert valid("access_fragment", json.loads((out / "access.json").read_text())) == []
    assert valid("report_fragment", json.loads((out / "numbers.json").read_text())) == []
    assert valid("access_record", {"name": "x", "axis": "column", "role": "used", "count": 1,
                                   "distinct": 1, "id_set_sha256": "a" * 64}) != []
