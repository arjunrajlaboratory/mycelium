"""Report numbers are linked to the hash of the file they came from.

Item 5 of the provenance work: `register_value(..., source=...)` records the
source's repository-relative path, SHA-256 and locator; one checker
(`check_report_values`) flags numbers whose source changed, and both report
gates (`sync_html_report`, `check_html_report --manifest`) use it.
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
import lineage_index as li  # noqa: E402
import test_lineage_index as tli  # noqa: E402
import sync_html_report as shr  # noqa: E402
import io_observer as io  # noqa: E402

pytestmark = pytest.mark.platform

TABLE = "metric,count\npassing,812\nfailing,40\n"


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs" / "tables").mkdir(parents=True)
    (root / "analysis" / "qc" / "scripts").mkdir(parents=True)
    (root / "analysis" / "qc" / "reports").mkdir(parents=True)
    (root / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text(TABLE)
    return root


def register(project: Path, body: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    script = project / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import register_value, ValueRegistrationError\n" + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=cwd or project / "analysis" / "qc")


def fragment(project: Path) -> dict:
    return json.loads((project / "analysis" / "qc" / "outputs" / "numbers.json").read_text())


LINKED = ('register_value("n_passing", 812, source={"path": "outputs/tables/cell_qc.csv", '
          '"locator": {"row": "passing", "column": "count"}})')


# -- registration --------------------------------------------------------------


def test_a_linked_number_records_path_hash_and_locator(project: Path) -> None:
    result = register(project, LINKED)
    assert result.returncode == 0, result.stderr
    (entry,) = fragment(project)["values"]
    source = entry["source"]
    assert source["path"] == "analysis/qc/outputs/tables/cell_qc.csv"
    assert source["root"] == "repo"
    assert source["sha256"] == hashlib.sha256(TABLE.encode()).hexdigest()
    assert source["size_bytes"] == len(TABLE)
    assert source["locator"] == {"row": "passing", "column": "count"}
    assert entry["provenance"] == ("analysis/qc/outputs/tables/cell_qc.csv:"
                                   "column=count,row=passing")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ('register_value("n", 1, source={"path": "outputs/tables/missing.csv", "locator": "x"})',
         "register after the file is written"),
        ('register_value("n", 1, source="outputs/tables/cell_qc.csv")', "needs a locator"),
        ('register_value("n", 1, source={"path": "outputs/tables/cell_qc.csv", '
         '"locator": {"colour": "red"}})', "locator dict"),
        ('register_value("n", 1, source={"path": "/etc/hosts", "locator": "x"})',
         "outside the project root"),
    ],
)
def test_bad_sources_are_refused(project: Path, body: str, message: str) -> None:
    result = register(project, f"try:\n    {body}\nexcept ValueRegistrationError as e:\n"
                               "    print(e); raise SystemExit(3)")
    assert result.returncode == 3, result.stderr
    assert message in result.stdout


def test_relinking_the_same_value_updates_its_source(project: Path) -> None:
    other = project / "analysis" / "qc" / "outputs" / "tables" / "other.csv"
    other.write_text("metric,count\npassing,812\n")
    result = register(project, 'register_value("n_passing", 812)\n' + LINKED.replace(
        "cell_qc.csv", "other.csv"))
    assert result.returncode == 0, result.stderr
    (entry,) = fragment(project)["values"]
    assert entry["source"]["path"].endswith("other.csv")


def test_concurrent_registrations_keep_every_key(project: Path) -> None:
    scripts = []
    for index in range(8):
        script = project / "analysis" / "qc" / "scripts" / f"p{index}.py"
        script.write_text(
            f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
            "from register_value import register_value\n"
            f"for i in range(20):\n    register_value('k{index}_' + str(i), i)\n")
        scripts.append(script)
    procs = [subprocess.Popen([sys.executable, str(s)]) for s in scripts]
    assert all(p.wait() == 0 for p in procs)
    assert len(fragment(project)["values"]) == 160


def test_the_fragment_keeps_its_mode(project: Path) -> None:
    assert register(project, LINKED).returncode == 0
    path = project / "analysis" / "qc" / "outputs" / "numbers.json"
    os.chmod(path, 0o600)
    assert register(project, 'register_value("other", 2)').returncode == 0
    assert (os.stat(path).st_mode & 0o777) == 0o600


# -- checking ------------------------------------------------------------------


def check(project: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HERE / "check_report_values.py"), *args],
                          capture_output=True, text=True)


def frag_path(project: Path) -> str:
    return str(project / "analysis" / "qc" / "outputs" / "numbers.json")


def test_a_current_number_is_clean(project: Path) -> None:
    assert register(project, LINKED).returncode == 0
    result = check(project, "--fragment", frag_path(project), "--json")
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["findings"] == []


def test_a_changed_source_makes_the_number_stale(project: Path) -> None:
    assert register(project, LINKED).returncode == 0
    (project / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text(
        "metric,count\npassing,799\n")
    result = check(project, "--fragment", frag_path(project), "--json")
    assert result.returncode == 1
    (finding,) = json.loads(result.stdout)["findings"]
    assert finding["code"] == "stale-value"
    assert finding["id"] == "n_passing"
    assert "rerun the analysis" in finding["message"]
    assert "Do not refresh the hash" in finding["message"]


def test_a_missing_source(project: Path) -> None:
    assert register(project, LINKED).returncode == 0
    (project / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").unlink()
    (finding,) = json.loads(check(project, "--fragment", frag_path(project),
                                  "--json").stdout)["findings"]
    assert finding["code"] == "source-missing"


@pytest.mark.parametrize("bad_path", ["/abs/x.csv", "a/../b.csv"])
def test_invalid_source_paths(project: Path, bad_path: str) -> None:
    entries = [{"id": "n", "value": 1, "source": {"path": bad_path, "sha256": "a" * 64,
                                                   "locator": "x"}}]
    (finding,) = crv.check_numbers(entries, project)
    assert finding["code"] == "source-invalid"


def test_a_symlinked_source_is_invalid(project: Path, tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "elsewhere" / "t.csv").write_text(TABLE)
    os.symlink(tmp_path / "elsewhere", project / "linked")
    entries = [{"id": "n", "value": 1, "source": {
        "path": "linked/t.csv", "sha256": hashlib.sha256(TABLE.encode()).hexdigest(),
        "locator": "x"}}]
    (finding,) = crv.check_numbers(entries, project)
    assert finding["code"] == "source-invalid"


def test_unlinked_numbers_are_warnings_unless_strict(project: Path) -> None:
    assert register(project, 'register_value("n_samples", 48)').returncode == 0
    lenient = check(project, "--fragment", frag_path(project))
    assert lenient.returncode == 0
    assert "[unchecked-value] warning" in lenient.stdout
    assert check(project, "--fragment", frag_path(project), "--strict").returncode == 1


def test_manifest_ids_and_bad_input(project: Path) -> None:
    manifest = project / "analysis" / "qc" / "reports" / ".manifest.json"
    manifest.write_text(json.dumps({"numbers": [{"id": "n", "value": 1}]}))
    assert check(project, "--manifest", str(manifest)).returncode == 0
    manifest.write_text("{bad")
    assert check(project, "--manifest", str(manifest)).returncode == 2


# -- report gates ----------------------------------------------------------------


def _report(project: Path, numbers: list[dict]) -> tuple[Path, Path]:
    reports = project / "analysis" / "qc" / "reports"
    report = reports / "report.html"
    report.write_text("<!doctype html><html><body><main><p>812 cells passed.</p></main>"
                      "</body></html>")
    manifest = reports / ".manifest.json"
    manifest.write_text(json.dumps({"numbers": numbers, "figures": [], "data": []}))
    return report, manifest


def _linked_entry(project: Path) -> dict:
    assert register(project, LINKED).returncode == 0
    (entry,) = fragment(project)["values"]
    return {"id": entry["key"], "value": entry["value"], "provenance": entry["provenance"],
            "computed_at": entry["computed_at"], "source": entry["source"]}


def test_sync_refuses_a_stale_number_and_writes_nothing(project: Path) -> None:
    report, manifest = _report(project, [_linked_entry(project)])
    (project / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text("changed\n")
    before = report.read_bytes()
    result = subprocess.run([sys.executable, str(HERE / "sync_html_report.py"), str(report),
                             "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "stale-value" in result.stderr
    assert report.read_bytes() == before


def test_sync_passes_a_current_number(project: Path) -> None:
    report, manifest = _report(project, [_linked_entry(project)])
    result = subprocess.run([sys.executable, str(HERE / "sync_html_report.py"), str(report),
                             "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_html_checker_reports_stale_values_for_one_report(project: Path) -> None:
    report, manifest = _report(project, [_linked_entry(project)])
    (project / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text("changed\n")
    script = str(HERE / "check_html_report.py")
    result = subprocess.run([sys.executable, script, str(report), "--manifest", str(manifest),
                             "--report-only"], capture_output=True, text=True)
    assert "[stale-value] error" in result.stdout
    assert result.returncode == 1
    two = subprocess.run([sys.executable, script, str(report), str(report), "--manifest",
                          str(manifest)], capture_output=True, text=True)
    assert two.returncode == 2


def test_tex_renderer_ignores_sources(project: Path) -> None:
    import render_report_values_tex as tex

    entry = _linked_entry(project)
    plain = {k: v for k, v in entry.items() if k != "source"}
    assert tex.render({"numbers": [entry]}) == tex.render({"numbers": [plain]})


def test_scitexlintr_parses_linked_numbers_unchanged(project: Path) -> None:
    manifest_mod = pytest.importorskip("scitexlintr._manifest")
    entry = _linked_entry(project)
    plain = {k: v for k, v in entry.items() if k != "source"}
    linked_doc = manifest_mod.parse_manifest({"numbers": [entry]})
    plain_doc = manifest_mod.parse_manifest({"numbers": [plain]})
    assert linked_doc == plain_doc


# -- acceptance: a rerun supersedes the output and flags the number -------------


def test_a_rerun_flags_a_number_registered_against_the_old_output(project: Path) -> None:
    import test_lineage_index as tli

    import io_observer as io
    import lineage_index as li

    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / "data").mkdir()
    (project / "out").mkdir()
    (project / ".mycelium").mkdir()
    (project / "data" / "in.csv").write_text("a\n1\n")
    (project / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out/res.csv')\n")
    tli._run_call(project, "t1", "python fit.py",
                  lambda: (project / "out" / "res.csv").write_text("count\n812\n"))
    result = register(project, 'register_value("n_res", 812, source={"path": '
                               f'"{project / "out" / "res.csv"}", '
                               '"locator": {"row": 0, "column": "count"}})')
    assert result.returncode == 0, result.stderr
    tli._run_call(project, "t2", "python fit.py",
                  lambda: (project / "out" / "res.csv").write_text("count\n799\n"))
    import extract_data_lineage as stop

    manifest = stop.extract(project / "events.tmp", "sess", str(project),
                            project / ".mycelium" / "io" / "obs")
    lineage_dir = project / ".living" / "log" / "data-lineage"
    lineage_dir.mkdir(parents=True)
    stop.write_manifest_atomic(lineage_dir / "sess.json", manifest)
    old_sha = hashlib.sha256(b"count\n812\n").hexdigest()
    old = li.query_hash(li.load(project), old_sha)["paths"][0]["writes"][0]
    assert old["status"] == "superseded"
    findings = check(project, "--fragment", frag_path(project), "--json", "--explain")
    (finding,) = json.loads(findings.stdout)["findings"]
    assert finding["code"] == "stale-value"
    assert finding["expected"] == old_sha
    assert finding["lineage"]["status"] == "written-by"
    assert finding["lineage"]["write_call"].endswith("claude:t2")
    _ = io


def test_a_dotdot_or_symlinked_source_is_refused(project: Path, tmp_path: Path) -> None:
    result = register(project, "try:\n    register_value('n', 1, source={'path': "
                               "'outputs/tables/../tables/cell_qc.csv', 'locator': 'x'})\n"
                               "except ValueRegistrationError as e:\n    print(e); raise SystemExit(3)")
    assert result.returncode == 3 and "must not contain" in result.stdout
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "m.csv").write_text("a\n")
    import os

    os.symlink(tmp_path / "shared", project / "analysis" / "qc" / "outputs" / "current")
    result = register(project, "try:\n    register_value('n', 1, source={'path': "
                               "'outputs/current/m.csv', 'locator': 'x'})\n"
                               "except ValueRegistrationError as e:\n    print(e); raise SystemExit(3)")
    assert result.returncode == 3 and "symlink" in result.stdout


def test_threads_cannot_both_register_conflicting_values(project: Path) -> None:
    body = (
        "import threading\n"
        "errors = []\n"
        "def work(v):\n"
        "    try:\n"
        "        register_value('k', v)\n"
        "    except ValueRegistrationError:\n"
        "        errors.append(v)\n"
        "ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]\n"
        "[t.start() for t in ts]; [t.join() for t in ts]\n"
        "print(len(errors))\n")
    result = register(project, body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "7"  # exactly one value won


def test_html_gates_use_the_manifests_checkout(project: Path, tmp_path: Path) -> None:
    report, manifest = _report(project, [_linked_entry(project)])
    (project / "analysis" / "qc" / "outputs" / "tables" / "cell_qc.csv").write_text("changed\n")
    elsewhere = tmp_path / "old-checkout"
    elsewhere.mkdir()
    staged = elsewhere / "report.html"
    staged.write_text(report.read_text())
    result = subprocess.run([sys.executable, str(HERE / "sync_html_report.py"), str(staged),
                             "--manifest", str(manifest)], capture_output=True, text=True)
    assert result.returncode == 1 and "stale-value" in result.stderr


def test_a_figure_only_manifest_with_null_numbers_passes(project: Path) -> None:
    manifest = project / "analysis" / "qc" / "reports" / ".manifest.json"
    manifest.write_text(json.dumps({"numbers": None, "figures": []}))
    assert check(project, "--manifest", str(manifest)).returncode == 0


def test_source_and_sources_together_are_invalid(tmp_path: Path) -> None:
    entry = {"id": "n", "value": 1, "source": {"path": "a", "sha256": "a" * 64, "locator": "x"},
             "sources": [{"path": "b", "sha256": "b" * 64, "locator": "y"}]}
    (finding,) = crv.check_numbers([entry], tmp_path)
    assert finding["code"] == "source-invalid"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def project_2(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def test_explain_names_only_the_writer_of_the_current_bytes(project_2: Path) -> None:
    (project_2 / "out" / "x").write_text("edited by hand")
    tli.write_store(project_2, [tli.observation(project_2, "c1", created=[("out/x", sha("v1"))],
                                              pre=1, post=2)])
    index = li.load(project_2, write=False)
    explanation = crv._explain(index, "out/x", sha("edited by hand"))
    assert explanation["status"] == "current-bytes-not-in-record"


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


TABLE_REL = "analysis/qc/outputs/tables/cell_qc.csv"


TABLE_SHA = hashlib.sha256(TABLE.encode()).hexdigest()


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


def run_py(project: Path, body: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    script = project / "analysis" / "qc" / "scripts" / "probe.py"
    script.write_text(
        f"import os, sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import (register_value, record_ids, read_source, "
        "snapshot_source, ValueRegistrationError, AccessRecordError)\n" + body + "\n")
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          cwd=cwd or project / "analysis" / "qc")


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


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def _pending(repo: Path, call: str, rel: str, identity: dict, *, pre: int, post: int) -> dict:
    record = tli.observation(repo, call, modified=[(rel, None)], pre=pre, post=post)
    record["changes"]["modified"][0]["after"]["identity"] = identity
    return record


def _identity(path: Path) -> dict:
    st = os.stat(path)
    return {"dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


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


def test_explain_names_no_producer_for_shared_attribution(monkeypatch) -> None:
    write = {"call": "s|claude:read", "sha256": sha("B"), "evidence": "observed",
             "action": "written", "attribution": "shared", "overlapping": ["s|claude:w"],
             "record": {"status": "current-in-record"}, "events": []}

    class FakeIndex:
        gaps: list = []

    monkeypatch.setattr(li, "query_path",
                        lambda index, rel, disk=False: {"writes": [write]})
    monkeypatch.setattr(crv, "_incomplete_reasons", lambda index, rel, writes: [])
    monkeypatch.setattr(crv, "_resolve_pending_writes", lambda index, rel, writes, a: writes)
    out = crv._explain(FakeIndex(), "out.csv", sha("B"))
    assert out["status"] == "producer-shared"
    assert "s|claude:w" in out["candidates"] and "write_call" not in out


A, B, C = "aa" * 32, "bb" * 32, "cc" * 32


def test_control_complete_record_still_says_outside(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": {"status": "current-in-record"}},
    ])
    assert crv._explain(object(), "out/res.csv", B)["status"] == "current-bytes-not-in-record"


UNPROVEN = {"status": "unknown", "reasons": ["coverage-unproven"],
            "unaccounted": [{"kind": "journal-compacted", "floor": 7}],
            "unaccounted_count": 1}


def test_coverage_unproven_write_is_not_called_outside_the_record(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": dict(UNPROVEN)},
    ])
    lineage = crv._explain(object(), "out/res.csv", B)
    assert lineage["status"] == "producer-unknown"
    assert "coverage-unproven" in lineage["reasons"]


def test_unproven_later_write_blocks_restored_outside_claim(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": {"status": "superseded"}},
        {"call": "w2", "action": "modified", "sha256": C, "evidence": "observed",
         "record": dict(UNPROVEN)},
    ])
    lineage = crv._explain(object(), "out/res.csv", A)
    assert lineage["status"] == "producer-unknown"
    assert "coverage-unproven" in lineage["reasons"]


def test_unproven_later_write_blocks_removed_outside_claim(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "d1", "action": "deleted", "evidence": "observed",
         "record": {"status": "superseded"}},
        {"call": "w2", "action": "created", "sha256": C, "evidence": "observed",
         "record": dict(UNPROVEN)},
    ])
    lineage = crv._explain(object(), "out/res.csv", None)
    assert lineage["status"] == "producer-unknown"


def test_unknown_verdict_without_reasons_is_still_incomplete(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": {"status": "unknown"}},
    ])
    assert crv._explain(object(), "out/res.csv", B)["status"] == "producer-unknown"


def test_a_source_from_a_sibling_checkout_is_refused(tmp_path: Path) -> None:
    project = tmp_path / "a"
    sibling = tmp_path / "b"
    for root in (project, sibling):
        (root / ".living").mkdir(parents=True)
        (root / "data").mkdir()
        (root / "data" / "counts.csv").write_text("x\n")
    script = project / "analysis" / "qc" / "scripts" / "r.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        f"import sys\nsys.path.insert(0, {str(HERE)!r})\n"
        "from register_value import register_value, ValueRegistrationError\n"
        "try:\n"
        f"    register_value('n', 1, source={{'path': {str(sibling / 'data' / 'counts.csv')!r}, "
        "'locator': 'x'})\n"
        "except ValueRegistrationError as e:\n    print(e); raise SystemExit(3)\n")
    result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    assert result.returncode == 3 and "outside the project root" in result.stdout
