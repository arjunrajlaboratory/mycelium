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
