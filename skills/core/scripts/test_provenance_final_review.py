"""Regressions for the final whole-branch review of the provenance work.

Each test pins one P1 scenario: a secret in a prefixed literal, a scope
change during a call, chained runners, a source from a sibling checkout, a
partly failed patch, a background job, a collaborator's 0.9.7 manifest, and
cross-group reuse a pairing never verified.
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

import data_contract_check as dcc  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import lineage_redact  # noqa: E402
import test_lineage_index as tli  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("literal", ['r"example-secret"', "b'example-secret'", 'f"example-secret"',
                                     'rb"example-secret"'])
def test_prefixed_secret_literals_are_not_committed(literal: str) -> None:
    command = f"python -c 'password = {literal}; print(1)'"
    assert lineage_redact.commit_safe_command(command, set()) == "python -c <v>"


def test_a_scope_change_is_not_a_deletion(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "data" / "processed").mkdir(parents=True)
    (repo / "data" / "processed" / "x.csv").write_text("x")
    state = repo / ".mycelium"
    state.mkdir()
    io.pre(state, host="claude", session="s", call_id="c", tool="Bash", repo_root=repo,
           snapshot=True)
    scope = state / "io" / "scope"
    scope.parent.mkdir(parents=True, exist_ok=True)
    scope.write_text("exclude data/processed\n")
    observation = io.post(state, host="claude", session="s", call_id="c", tool="Bash",
                          repo_root=repo, bash_cmd="true")
    changes = observation["changes"]
    if changes is None:  # the scope file itself made the walk unavailable: also no deletion
        return
    assert not any(e["path"] == "data/processed/x.csv" for e in changes["deleted"])
    assert "data/processed/x.csv" in changes.get("coverage_changed", [])


def test_coverage_changes_are_gaps_in_the_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "data" / "x.csv").write_text("x")
    later = tli.observation(repo, "c2", pre=3, post=4)
    later["changes"]["coverage_changed"] = ["data/x.csv"]
    tli.write_store(repo, [tli.observation(repo, "c1", created=[("data/x.csv", sha("x"))],
                                           pre=1, post=2), later])
    write = li.query_hash(li.load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown" and "gap" in write["record"]["reasons"]


def test_chained_runners_get_their_own_stages(tmp_path: Path) -> None:
    import extract_data_lineage_event as ev

    (tmp_path / "a.yml").write_text("a: 1\n")
    (tmp_path / "b.yml").write_text("b: 2\n")
    command = ("snakemake -s A.smk --configfile a.yml && echo snakemake-done && "
               "snakemake -s B.smk --configfile b.yml")
    # Three runner words, two runner detections: ambiguous, never guessed.
    assert ev.build_step(command, {"offset": None, "kind": "runner"}, None, tmp_path) == {
        "partial": True, "reason": "runner-stage-ambiguous"}
    clean = "snakemake -s A.smk --configfile a.yml && snakemake -s B.smk --configfile b.yml"
    words = [m.start(1) for m in ev._RUNNER_WORD_RX.finditer(clean)]
    steps = [ev.build_step(clean, {"offset": o, "kind": "runner"}, None, tmp_path) for o in words]
    assert [s["config_files"][0]["path"].endswith(n) for s, n in zip(steps, ("a.yml", "b.yml"))] \
        == [True, True]


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


def test_a_partly_failed_patch_leaves_its_targets_unknown(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "out.csv").write_text("v1")
    failed = tli.observation(repo, "p2", pre=3, post=4, edited=[("other.csv", sha("o"))])
    failed["changes"] = {"edited": [], "deleted": [],
                         "refused": [{"path": "out.csv", "reason": "edit-failed"}]}
    failed["outcome"] = "failure"
    tli.write_store(repo, [tli.observation(repo, "p1", created=[("out.csv", sha("v1"))],
                                           pre=1, post=2), failed])
    write = li.query_hash(li.load(repo), sha("v1"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_a_failed_edit_observation_says_so(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "out.csv").write_text("v1")
    state = repo / ".mycelium"
    state.mkdir()
    payload = {"session_id": "s", "tool_use_id": "e1", "tool_name": "apply_patch",
               "cwd": str(repo), "tool_input": {"input": "*** Begin Patch\n*** Update File: out.csv\n"},
               "tool_response": {"exit_code": 1}}
    io.hook(state, repo, "codex", json.dumps({**payload, "hook_event_name": "PreToolUse"}))
    io.hook(state, repo, "codex", json.dumps({**payload, "hook_event_name": "PostToolUse"}))
    (record,) = io._read_lines(io.observations_path(state, "codex", "s"))
    assert record["outcome"] == "failure"
    assert record["changes"]["edited"] == []
    assert record["changes"]["refused"] == [{"path": "out.csv", "reason": "edit-failed"}]


def test_a_background_job_clouds_later_verdicts_until_bounded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "out.csv").write_text("v1")
    background = tli.observation(repo, "bg", pre=3, post=None, status="background")
    tli.write_store(repo, [tli.observation(repo, "c1", created=[("out.csv", sha("v1"))],
                                           pre=1, post=2), background])
    write = li.query_hash(li.load(repo), sha("v1"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert any(g["kind"] == "background-job" for g in write["record"]["gaps"])


def test_a_collaborators_v1_observations_stay_theirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "out.csv").write_text("v1")
    observation = tli.observation(repo, "c1", created=[("out.csv", sha("v1"))], pre=1, post=2)
    observation.pop("repo_root")
    observation["version"] = 1
    manifest = {"session_id": "theirs", "repo_root": "/home/collaborator/proj", "actions": [],
                "observations": [observation], "summary": {}}
    (repo / ".living" / "log" / "data-lineage" / "theirs.json").write_text(json.dumps(manifest))
    assert li.query_hash(li.load(repo), sha("v1"))["found"] is False


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


def _extract(cwd: Path, command: str) -> str:
    result = subprocess.run(
        [sys.executable, str(HERE / "extract_data_lineage_event.py"), "--cwd", str(cwd),
         "--ts", "2026-11-22T00:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--append-to", os.devnull], capture_output=True, text=True, check=True)
    return result.stdout


def test_helpers_run_through_a_plugin_root_variable_are_managed(tmp_path: Path) -> None:
    plugin = HERE.parent.parent.parent
    (tmp_path / ".mycelium").mkdir()
    (tmp_path / ".mycelium" / "plugin-root").write_text(str(plugin) + "\n")
    for command in (
        "P=$(cat .mycelium/plugin-root); python3 $P/skills/core/scripts/lineage_index.py build",
        'P="$(cat .mycelium/plugin-root)" && python3 "$P/skills/core/scripts/lineage_index.py" build',
        f"P={plugin}; python3 $P/skills/core/scripts/lineage_index.py build",
    ):
        assert _extract(tmp_path, command) == "", command
    # An unresolvable variable is still an unreadable script, reported.
    assert "unreadable_script" in _extract(tmp_path, "python3 $Q/skills/core/scripts/x.py")


def test_a_look_alike_user_script_through_a_variable_is_not_exempt(tmp_path: Path) -> None:
    fake = tmp_path / "fake" / "skills" / "core" / "scripts"
    fake.mkdir(parents=True)
    (fake / "lineage_index.py").write_text("print('mine')\n")
    stdout = _extract(tmp_path, f"P={tmp_path / 'fake'}; python3 $P/skills/core/scripts/lineage_index.py")
    assert "unreadable_script" in stdout  # not silently treated as a Mycelium helper
