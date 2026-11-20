"""Every Bash call that touches data leaves a lineage record.

The analysis detector records only interpreter runs it can prove. In the
SNP-tree and AP1 transcripts, shell data operations (`head`, `zcat`, `cp` on
data files) were 15-37% of data-touching commands and inline reads the
detector rejects were most of the rest; none left anything. Each now leaves
one `"kind": "data_command"` record. The record never claims that an
analysis ran: data-like words in the text are mentions (`named_paths`, no
direction, nothing hashed), and `inputs`/`outputs` hold only paths whose
direction shell syntax alone proves in a command that exited 0.

Data commands do not reopen a transaction and do not count as session work:
a look at a data file is not something the lifecycle must account for.
Without a transaction they wait in the shared unowned file until the same
host session's next owned Stop moves them into its stream.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import extract_data_lineage as consolidator  # noqa: E402
import extract_data_lineage_event as lineage_event  # noqa: E402

EXTRACTOR = Path(__file__).parent / "extract_data_lineage_event.py"
HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOSTS = ["claude", "codex"]
TIME_LIMIT_SECONDS = 5.0


def _event(command: str, cwd: Path, bash_exit: int | None = 0) -> dict | None:
    args = argparse.Namespace(
        ts="2026-11-20T00:00:00Z",
        bash_cmd=command,
        agent_id=None,
        agent_type=None,
        bash_exit=bash_exit,
    )
    return lineage_event.build_data_command_event(args, cwd)


def _has_label(candidates: list[str], want: str) -> bool:
    """Whether an unproven-run label matches: labels are the detector's
    resolved script path (`/repo/fit.R`), `<interpreter> (inline)`, or a
    stdin-fed interpreter (`python3 - <<heredoc`, `xargs python3`)."""
    token = want.split()[-1]
    for label in candidates:
        if label == want or label.endswith(want):
            return True
        if token in ("-c", "-e", "-uc", "-m") and label.endswith("(inline)"):
            return True
        if label.endswith("/" + token.rsplit("/", 1)[-1]):
            return True
    return False


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "data" / "raw").mkdir(parents=True)
    (tmp_path / "data" / "x.csv").write_text("a,b\n1,2\n")
    (tmp_path / "data" / "raw" / "a.tsv.gz").write_bytes(b"\x1f\x8b")
    (tmp_path / "analysis" / "x" / "outputs").mkdir(parents=True)
    (tmp_path / "analysis" / "x" / "scripts").mkdir(parents=True)
    (tmp_path / "README.md").write_text("readme\n")
    return tmp_path


# ---------- which calls touch data (paired positive / negative) ----------


@pytest.mark.parametrize(
    ("command", "named"),
    [
        ("head data/x.csv", ["data/x.csv"]),
        ("zcat data/raw/a.tsv.gz | cut -f1 | sort | uniq -c", ["data/raw/a.tsv.gz"]),
        ("awk -F, '{print $1}' results/table.csv", ["results/table.csv"]),
        ("ls data/", ["data/"]),
        ("ls ./data", ["./data"]),
        ("find analysis/x/outputs -type f", ["analysis/x/outputs"]),
        ("wc -l analysis/x/outputs/run1/log", ["analysis/x/outputs/run1/log"]),
        ("samtools view -c aln.bam", ["aln.bam"]),
        ("cat tree.nwk", ["tree.nwk"]),
        ("rg --files analysis/x/outputs", ["analysis/x/outputs"]),
        ("cut -d, -f2 --output-delimiter=: data/x.csv", ["data/x.csv"]),
        ("echo 'see data/x.csv'", ["data/x.csv"]),
    ],
)
def test_a_command_naming_data_leaves_a_data_command(project, command, named):
    event = _event(command, project)
    assert event is not None
    assert event["kind"] == "data_command"
    assert event["io_detection"] == "unresolved"
    assert event["named_paths"] == named
    assert "data-path" in event["signals"]
    # Never an analysis claim.
    assert "script" not in event and "script_sha256" not in event


@pytest.mark.parametrize(
    "command",
    [
        "head README.md",
        "ls analysis/x/scripts",
        "ls data",  # a bare word, not a path
        "cat package.json",
        "pip install -r requirements.txt",
        "cat .living/findings/notes.csv",
        "jq . .mycelium/run/codex/x/events.json",
        "git status --short .claude/settings.local.json",
        "curl -sS https://example.org/files/a.csv -o /tmp/download",
        "echo hi",
        "git log --oneline -3",
    ],
)
def test_a_command_naming_no_data_leaves_nothing(project, command):
    assert _event(command, project) is None


def test_named_paths_are_capped_and_marked_truncated(project):
    command = "ls " + " ".join(f"data/f{i}.csv" for i in range(200))
    event = _event(command, project)
    assert len(event["named_paths"]) == lineage_event.DATA_COMMAND_NAMED_PATH_LIMIT
    assert event["named_paths_truncated"] is True


# ---------- inline and script runs the detector rejects ----------


@pytest.mark.parametrize(
    ("command", "candidate"),
    [
        (
            "python3 - <<'PY'\nimport pandas as pd\nprint(pd.read_csv('data/x.csv'))\nPY",
            "python3 - <<heredoc",
        ),
        (
            "R --no-save <<'RS'\nx <- read.csv('data/x.csv')\nRS",
            "R --no-save <<heredoc",
        ),
        (
            "for n in a b; do nohup Rscript run.R configs/$n.R > logs/$n.log 2>&1 & done",
            "Rscript run.R",
        ),
        (
            "cd /nonexistent/dir; .venv/bin/python -u fit.py",
            "fit.py",
        ),
        (
            # The run after a heredoc body is still shell text.
            "cat <<A <<B > notes.md\npython q.py\nA\nit's\nB\nfor f in a; do Rscript s.R; done",
            "Rscript s.R",
        ),
        (
            'echo "a << b"\nfor f in a; do python z.py; done',
            "python z.py",
        ),
        (
            "cat > /tmp/run_one.sh <<'EOF'\nRscript run.R $1\nEOF\n"
            "chmod +x /tmp/run_one.sh\nprintf 'a\\nb' | xargs -P 3 -n 1 /tmp/run_one.sh",
            "xargs /tmp/run_one.sh",
        ),
        ("for n in a b; do ./run_all.sh $n; done", "./run_all.sh"),
    ],
)
def test_an_unproven_analysis_run_is_labelled_not_claimed(project, command, candidate):
    event = _event(command, project)
    assert event is not None
    assert "analysis-unproven" in event["signals"]
    assert _has_label(event["analysis_candidates"], candidate), event["analysis_candidates"]
    assert event["inputs"] == [] and event["outputs"] == []


@pytest.mark.parametrize(
    "command",
    [
        'echo "python a.py"',
        "grep -n 'Rscript x.R' README.md",
        "python -m pytest tests -q",
        "python3 /opt/mycelium/skills/core/scripts/validate_structure.py .",
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/finalize_handoff.py"',
        "x=1; R = 2",
        "cat > NOTES.md <<'EOF'\npython fit.py\nRscript run.R\nEOF",
        "cat <<-EOF > notes.md\n\tpython x.py\n\tEOF",
        "chmod +x ./run.sh",
        "ls -l /tmp/run_one.sh",
    ],
)
def test_interpreter_text_that_is_not_a_run_leaves_nothing(project, command):
    assert _event(command, project) is None


def test_a_proven_analysis_run_is_an_action_not_a_data_command(project):
    (project / "fit.py").write_text("import pandas as pd\npd.read_csv('data/x.csv')\n")
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(project), "--ts", "T",
         "--bash-cmd", "python fit.py data/x.csv", "--bash-exit", "0"],
        capture_output=True, text=True, check=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(records) == 1
    assert "kind" not in records[0]
    assert records[0]["script"] == str(project / "fit.py")


# ---------- direction only where shell syntax proves it ----------


def _paths(records: list[dict]) -> list[tuple[str, str]]:
    return [(r["path"], r["evidence"]) for r in records]


def test_an_output_redirection_in_a_successful_command_is_an_output(project):
    event = _event("sort data/x.csv > data/sorted.csv 2>&1", project)
    assert _paths(event["outputs"]) == [(str(project / "data/sorted.csv"), "redirection")]
    # sort's own argv is not modelled, so its operand is not an input.
    assert event["inputs"] == []


def test_an_input_redirection_is_an_input_and_hashed(project):
    event = _event("wc -l < data/x.csv", project)
    [record] = event["inputs"]
    assert record["path"] == str(project / "data/x.csv")
    assert record["evidence"] == "redirection"
    assert len(record["sha256"]) == 64


@pytest.mark.parametrize(
    ("command", "bash_exit"),
    [
        ("sort data/x.csv > data/o.csv", 1),
        ("sort data/x.csv > data/o.csv", None),
        ('echo "> data/o.csv"', 0),
        ("sort data/x.csv # > data/o.csv", 0),
        ("false || sort data/x.csv > data/o.csv", 0),
        ("sort data/x.csv > data/o.csv; true", 0),
        ("sort data/x.csv | head > data/o.csv", 0),
        ("sort data/x.csv > $OUT/o.csv", 0),
        ('sort data/x.csv > "$OUT"/o.csv', 0),
        ("sort data/x.csv > data/o*.csv", 0),
        ("sort data/x.csv > data/{a,b}.csv", 0),
        ("sort data/x.csv > data/o.csv &", 0),
        ("cat > data/o.csv <<EOF\nx\nEOF", 0),
        ("(sort data/x.csv > data/o.csv)", 0),
        ("sort data/x.csv > `pwd`/o.csv", 0),
        ("sort data/x.csv > data/o.csv 'unterminated", 0),
    ],
)
def test_no_direction_is_claimed_without_proof(project, command, bash_exit):
    event = _event(command, project, bash_exit)
    assert event is not None
    assert event["outputs"] == [] and event["inputs"] == []


def test_a_cd_in_an_and_list_moves_where_outputs_resolve(project):
    event = _event("cd data && sort x.csv > sorted.csv", project)
    assert _paths(event["outputs"]) == [(str(project / "data/sorted.csv"), "redirection")]


def test_an_unresolvable_cd_leaves_relative_targets_unclaimed(project):
    event = _event(
        f"cd missing && sort x.csv > o.csv > {project}/data/abs.csv", project
    )
    assert _paths(event["outputs"]) == [(str(project / "data/abs.csv"), "redirection")]


def test_a_cp_reads_its_source_and_writes_its_destination(project):
    shutil.copy(project / "data/x.csv", project / "data/y.csv")
    event = _event("cp data/x.csv data/y.csv", project)
    assert _paths(event["inputs"]) == [(str(project / "data/x.csv"), "cp")]
    assert _paths(event["outputs"]) == [(str(project / "data/y.csv"), "cp")]
    assert event["inputs"][0]["sha256"] == event["outputs"][0]["sha256"]


def test_a_cp_into_a_directory_writes_beneath_it(project):
    shutil.copy(project / "data/x.csv", project / "analysis/x/outputs/x.csv")
    event = _event("cp -p data/x.csv analysis/x/outputs", project)
    assert _paths(event["outputs"]) == [
        (str(project / "analysis/x/outputs/x.csv"), "cp")
    ]


def test_a_mv_into_a_directory_claims_nothing(project):
    os.replace(project / "data/x.csv", project / "analysis/x/outputs/x.csv")
    event = _event("mv data/x.csv analysis/x/outputs", project)
    assert event["inputs"] == [] and event["outputs"] == []


def test_a_mv_records_the_gone_source_and_the_moved_file(project):
    os.replace(project / "data/x.csv", project / "data/z.csv")
    event = _event("mv data/x.csv data/z.csv", project)
    assert event["inputs"][0]["path"] == str(project / "data/x.csv")
    assert event["inputs"][0]["_missing"] is True
    assert _paths(event["outputs"]) == [(str(project / "data/z.csv"), "mv")]


@pytest.mark.parametrize(
    "command",
    [
        "cp -t data data/x.csv",          # target-directory option value
        "cp -S .bak data/x.csv data/y.csv",
        "cp --backup data/x.csv data/y.csv",
        "cp -s data/x.csv data/y.csv",    # a symlink, not a copy
        "cp -r data analysis/x/outputs",  # directory copy: name unknowable
        "cp data/x.csv README.md data/y.csv",  # many sources, file destination
        "echo cp data/x.csv data/y.csv",
        "command cp data/x.csv data/y.csv",
        "cp -n data/x.csv data/y.csv",   # no-clobber: may copy nothing, exit 0
        "cp -i data/x.csv data/y.csv",
        "mv -n data/x.csv data/y.csv",
        "mv -i data/x.csv data/y.csv",
        "cp -u data/x.csv data/y.csv",
    ],
)
def test_a_cp_argv_that_is_not_modelled_claims_no_direction(project, command):
    (project / "data/y.csv").write_text("y\n")
    event = _event(command, project)
    assert event is not None
    assert event["inputs"] == [] and event["outputs"] == []


# ---------- extractor modes ----------


def _main(project: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(project), "--ts", "T", *extra],
        capture_output=True, text=True, check=False,
    )


def test_append_mode_writes_the_data_command_to_the_stream(project):
    stream = project / "events.tmp"
    result = _main(project, "--bash-cmd", "head data/x.csv", "--bash-exit", "0",
                   "--append-to", str(stream))
    assert result.returncode == 0 and result.stdout == ""
    [record] = [json.loads(line) for line in stream.read_text().splitlines()]
    assert record["kind"] == "data_command"


@pytest.mark.parametrize("mode", ["--check-execution", "--check-post-action"])
def test_a_data_command_never_answers_yes_to_a_check(project, mode):
    result = _main(project, "--bash-cmd", "head data/x.csv", mode)
    assert result.returncode == 1


def test_check_execution_parks_the_record_keyed_by_host_session(project):
    shared = project / "unowned.ndjson"
    result = _main(project, "--bash-cmd", "head data/x.csv", "--check-execution",
                   "--data-command-to", str(shared), "--host", "codex",
                   "--host-session-id", "s1", "--agent-id", "sub-1")
    assert result.returncode == 1, result.stderr
    [record] = [json.loads(line) for line in shared.read_text().splitlines()]
    assert (record["kind"], record["host"], record["host_session_id"],
            record["agent_id"]) == ("data_command", "codex", "s1", "sub-1")
    assert (project / "unowned.ndjson.lock").exists()


def test_check_execution_parks_nothing_for_a_command_without_data(project):
    shared = project / "unowned.ndjson"
    result = _main(project, "--bash-cmd", "git status", "--check-execution",
                   "--data-command-to", str(shared), "--host", "claude",
                   "--host-session-id", "s1")
    assert result.returncode == 1
    assert not shared.exists()


def test_parking_refuses_a_symlinked_shared_file(project, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere") / "target.ndjson"
    elsewhere.write_text("")
    link = project / "unowned.ndjson"
    link.symlink_to(elsewhere)
    result = _main(project, "--bash-cmd", "head data/x.csv", "--check-execution",
                   "--data-command-to", str(link), "--host", "claude",
                   "--host-session-id", "s1")
    # Refused, and reported as a detector gap (70) rather than dropped.
    assert result.returncode == 70
    assert elsewhere.read_text() == ""


def test_a_park_that_cannot_be_written_is_a_detector_gap(project):
    result = _main(project, "--bash-cmd", "head data/x.csv", "--check-execution",
                   "--data-command-to", str(project / "missing" / "u.ndjson"),
                   "--host", "claude", "--host-session-id", "s1")
    # 70, not 1: the tracker records a detector-failed skip, never silence.
    assert result.returncode == 70


def test_parking_needs_check_execution_and_keys(project):
    result = _main(project, "--bash-cmd", "head data/x.csv",
                   "--data-command-to", str(project / "u.ndjson"))
    assert result.returncode == 2
    assert not (project / "u.ndjson").exists()


# ---------- consolidation ----------


def test_the_manifest_keeps_data_commands_apart_from_actions(tmp_path):
    action = {"ts": "2026-11-20T00:00:02Z", "bash_cmd": "python a.py",
              "script": "a.py", "inputs": [], "outputs": []}
    data = {"ts": "2026-11-20T00:00:01Z", "kind": "data_command",
            "event_id": "e1", "bash_cmd": "head data/x.csv",
            "named_paths": ["data/x.csv"], "signals": ["data-path"],
            "inputs": [], "outputs": [], "io_detection": "unresolved"}
    stream = tmp_path / "events.tmp"
    # A retried transfer can repeat a record: count its event_id once.
    stream.write_text("".join(json.dumps(r) + "\n" for r in (action, data, data)))

    manifest = consolidator.extract(stream, "s", str(tmp_path))

    assert manifest["n_actions"] == 1
    assert [a["script"] for a in manifest["actions"]] == ["a.py"]
    assert [d["bash_cmd"] for d in manifest["data_commands"]] == ["head data/x.csv"]
    assert manifest["summary"]["data_command_count"] == 1
    assert manifest["summary"]["unique_inputs"] == []
    assert manifest["started_at"] == "2026-11-20T00:00:02Z"


# ---------- linear-time scans (pattern 42) ----------


def _timed_event(command: str, cwd: Path, bash_exit: int | None = 0):
    started = time.monotonic()
    event = _event(command, cwd, bash_exit)
    return event, time.monotonic() - started


@pytest.mark.parametrize(
    "command",
    [
        "head " + "x" * 400_000 + ".csv",
        "echo " + " ".join(['"a"'] * 20_000) + " data/x.csv",
        " && ".join(["sort data/x.csv > data/o.csv"] * 3000),
        " && ".join(["cd data"] * 3000) + " && head x.csv",
        "cp " + " ".join(f"data/f{i}.csv" for i in range(20_000)) + " data/",
        "R -f a.R --args " + " ".join(['"$x"'] * 5000) + " data/x.csv",
        "\n".join(["R -q -e 'read.csv(\"data/x.csv\")'"] * 800),
        "python3 - <<'PY'\n" + "print('data/x.csv')\n" * 20_000 + "PY",
        "for f in a; do " + "Rscript s.R; " * 5000 + "done",
        "a/" * 100_000 + "data/x.csv",
        ";" * 200_000 + " head data/x.csv",
    ],
)
def test_long_commands_scan_in_linear_time(project, command):
    event, seconds = _timed_event(command, project)
    assert event is not None
    assert seconds < TIME_LIMIT_SECONDS


def test_doubling_a_command_roughly_doubles_the_scan(project):
    def run(n: int) -> float:
        command = " && ".join(["sort data/x.csv > data/o.csv"] * n)
        best = min(_timed_event(command, project)[1] for _ in range(3))
        return best

    small, large = run(1500), run(3000)
    # Quadratic growth would be ~4x; allow noise well short of that.
    assert large < max(small * 3.2, 0.05)


# ---------- lifecycle: no reopen, no work, parked until an owned Stop ----------


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (repo / "data").mkdir()
    (repo / "data" / "x.csv").write_text("a\n1\n")
    (repo / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('data/x.csv')\n"
    )
    return repo


def _run(host: str, name: str, repo: Path, payload: dict):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    return subprocess.run(
        [str(HOOKS_DIR / name)], cwd=repo, input=json.dumps(payload),
        text=True, capture_output=True, env=env, check=False,
    )


def _start(host, repo, sid):
    result = _run(host, "mycelium-health.sh", repo,
                  {"cwd": str(repo), "source": "startup", "session_id": sid,
                   "hook_event_name": "SessionStart"})
    assert result.returncode == 0, result.stderr


def _stop(host, repo, sid):
    result = _run(host, "mycelium-stop-check.sh", repo,
                  {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                   "stop_hook_active": False})
    assert result.returncode == 0, result.stderr
    return result


def _bash(host, repo, sid, command, hook="mycelium-data-tracker.sh"):
    result = _run(host, hook, repo,
                  {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
                   "hook_event_name": "PostToolUse",
                   "tool_input": {"command": command},
                   "tool_response": {"exit_code": 0}})
    assert result.returncode == 0, result.stderr
    return result


def _run_dir(repo: Path, host: str, sid: str) -> Path:
    return repo / ".mycelium" / "run" / host / sid


def _blocked(stop) -> bool:
    return bool(stop.stdout.strip()) and json.loads(stop.stdout).get("decision") == "block"


def _unowned(repo: Path) -> list[dict]:
    path = repo / ".mycelium" / "lineage-skips-unowned.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _manifests(repo: Path) -> list[dict]:
    folder = repo / ".living" / "log" / "data-lineage"
    if not folder.is_dir():
        return []
    return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))]


def _accept_idle_turn(host, repo, sid):
    _start(host, repo, sid)
    assert not _blocked(_stop(host, repo, sid))


@pytest.mark.parametrize("host", HOSTS)
def test_a_data_command_inside_a_transaction_joins_the_manifest(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "owned"
    _start(host, repo, sid)

    _bash(host, repo, sid, "head data/x.csv")
    post = _bash(host, repo, sid, "head data/x.csv", "mycelium-post-action.sh")
    stop = _stop(host, repo, sid)

    # Not a post-action cycle, and not work: an idle turn with a look at
    # data is still accepted without a session log.
    assert "SESSION LOG UPDATE" not in post.stdout
    assert not _blocked(stop), stop.stdout
    assert [p.name for p in (repo / ".living" / "log").glob("*.md")
            if p.name != "LOG_REGISTRY.md"] == []
    registry = repo / ".living" / "log" / "LOG_REGISTRY.md"
    assert sid not in (registry.read_text() if registry.exists() else "")
    [manifest] = _manifests(repo)
    assert manifest["n_actions"] == 0
    assert [d["named_paths"] for d in manifest["data_commands"]] == [["data/x.csv"]]


@pytest.mark.parametrize("host", HOSTS)
def test_a_data_command_after_an_idle_turn_does_not_reopen(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "idle"
    _accept_idle_turn(host, repo, sid)
    logs_before = sorted(p.name for p in (repo / ".living" / "log").iterdir())

    result = _bash(host, repo, sid, "zcat data/x.csv | head")

    assert result.stdout == ""
    assert not (_run_dir(repo, host, sid) / "active-session-log.tmp").exists()
    assert sorted(p.name for p in (repo / ".living" / "log").iterdir()) == logs_before
    [parked] = _unowned(repo)
    assert (parked["kind"], parked["host"], parked["host_session_id"]) == (
        "data_command", host, sid,
    )


def _data_only_manifests(repo: Path) -> list[Path]:
    folder = repo / ".living" / "log" / "data-lineage"
    return sorted(folder.glob("data-commands-*.json")) if folder.is_dir() else []


@pytest.mark.parametrize("host", HOSTS)
def test_a_stop_without_a_transaction_writes_parked_data_commands(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "parked"
    _accept_idle_turn(host, repo, sid)
    _bash(host, repo, sid, "head data/x.csv")
    [parked] = _unowned(repo)

    # Not a gap: nothing is reported, and the record gets a manifest of its
    # own instead of waiting for a transaction that may never come.
    quiet = _stop(host, repo, sid)

    assert "LINEAGE GAP" not in quiet.stdout
    assert _unowned(repo) == []
    [path] = _data_only_manifests(repo)
    assert path.name == f"data-commands-{host}-{sid}-{parked['event_id'][:12]}.json"
    manifest = json.loads(path.read_text())
    assert manifest["n_actions"] == 0
    assert [d["bash_cmd"] for d in manifest["data_commands"]] == ["head data/x.csv"]
    assert not (_run_dir(repo, host, sid) / "active-session-log.tmp").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_a_parked_data_command_joins_a_reopened_session(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "joined"
    _accept_idle_turn(host, repo, sid)
    _bash(host, repo, sid, "head data/x.csv")
    _bash(host, repo, sid, "python3 analysis.py")
    _bash(host, repo, sid, "python3 analysis.py", "mycelium-post-action.sh")
    _stop(host, repo, sid)

    assert _unowned(repo) == []
    assert _data_only_manifests(repo) == []
    data_commands = [d for m in _manifests(repo) for d in m["data_commands"]]
    assert [d["bash_cmd"] for d in data_commands] == ["head data/x.csv"]


@pytest.mark.parametrize("host", HOSTS)
def test_a_data_only_turn_cannot_lose_its_manifest_to_a_reused_session_number(
    tmp_path, host
):
    """A data-only turn is not work, so its session log and number are
    released; the next session takes the same number. Its manifest must
    not be named by that number, or the next one overwrites it."""
    repo = _repo(tmp_path)
    _start(host, repo, "first")
    _bash(host, repo, "first", "head data/x.csv")
    assert not _blocked(_stop(host, repo, "first"))
    [data_only] = _data_only_manifests(repo)
    before = data_only.read_bytes()

    _start(host, repo, "second")
    _bash(host, repo, "second", "python3 analysis.py")
    _bash(host, repo, "second", "python3 analysis.py", "mycelium-post-action.sh")
    _stop(host, repo, "second")

    assert data_only.read_bytes() == before
    session_manifests = [
        m for m in _manifests(repo) if not m["session_id"].startswith("data-commands-")
    ]
    assert [m["n_actions"] for m in session_manifests] == [1]


@pytest.mark.parametrize("host", HOSTS)
def test_a_session_manifest_replaces_the_data_only_one_after_a_blocked_stop(
    tmp_path, host
):
    repo = _repo(tmp_path)
    sid = "blocked"
    _start(host, repo, sid)
    _bash(host, repo, sid, "head data/x.csv")
    edit = _run(host, "mycelium-activity-tracker.sh", repo,
                {"cwd": str(repo), "session_id": sid, "tool_name": "Write",
                 "hook_event_name": "PostToolUse",
                 "tool_input": {"file_path": str(repo / "model.py")},
                 "tool_response": {}})
    assert edit.returncode == 0, edit.stderr
    (repo / "model.py").write_text("x = 1\n")
    assert _blocked(_stop(host, repo, sid))
    assert len(_data_only_manifests(repo)) == 1

    _bash(host, repo, sid, "python3 analysis.py")
    _stop(host, repo, sid)

    assert _data_only_manifests(repo) == []
    [manifest] = _manifests(repo)
    assert manifest["n_actions"] == 1
    assert [d["bash_cmd"] for d in manifest["data_commands"]] == ["head data/x.csv"]


@pytest.mark.parametrize("host", HOSTS)
def test_a_stop_without_a_transaction_reports_skips_and_writes_data_commands(
    tmp_path, host
):
    repo = _repo(tmp_path)
    sid = "mixed"
    _accept_idle_turn(host, repo, sid)
    shared = repo / ".mycelium" / "lineage-skips-unowned.ndjson"
    skip = {"ts": "T", "kind": "skip", "skip_id": "k1",
            "skip_reason": "detector-failed", "host": host, "host_session_id": sid}
    data = {"ts": "T", "kind": "data_command", "event_id": "e1",
            "bash_cmd": "head data/x.csv", "host": host, "host_session_id": sid}
    other = dict(data, event_id="e2", host_session_id="someone-else")
    shared.write_text("".join(json.dumps(r) + "\n" for r in (skip, data, other)))

    stop = _stop(host, repo, sid)

    assert "LINEAGE GAP: 1 data command(s)" in stop.stdout
    assert "detector-failed x1" in stop.stdout
    assert _unowned(repo) == [other]
    [path] = _data_only_manifests(repo)
    assert path.name == f"data-commands-{host}-{sid}-e1.json"


@pytest.mark.parametrize("host", HOSTS)
def test_data_commands_stay_parked_when_their_manifest_cannot_be_written(
    tmp_path, host
):
    repo = _repo(tmp_path)
    sid = "unwritable"
    _accept_idle_turn(host, repo, sid)
    _bash(host, repo, sid, "head data/x.csv")
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "elsewhere"
    target.mkdir()
    shutil.rmtree(repo / ".living" / "log" / "data-lineage")
    (repo / ".living" / "log" / "data-lineage").symlink_to(target)

    stop = _stop(host, repo, sid)

    assert "LINEAGE GAP" not in stop.stdout
    assert [r["kind"] for r in _unowned(repo)] == ["data_command"]
    assert list(target.iterdir()) == []


# ---------- Codex review round 1 ----------


def test_a_fifo_target_is_recorded_without_being_opened(project):
    fifo = project / "data" / "stream.csv"
    os.mkfifo(fifo)
    event, seconds = _timed_event("wc -l < data/stream.csv", project)
    [record] = event["inputs"]
    assert record["path"] == str(fifo)
    assert record.get("sha256") is None
    assert seconds < TIME_LIMIT_SECONDS
    stream = project / "events.tmp"
    stream.write_text(json.dumps(dict(event, ts="T")) + "\n")
    started = time.monotonic()
    manifest = consolidator.extract(stream, "s", str(project))
    assert time.monotonic() - started < TIME_LIMIT_SECONDS
    assert manifest["data_commands"][0]["inputs"][0].get("sha256") is None


@pytest.mark.parametrize(
    "command",
    [
        "exit 0 && cat < data/x.csv",
        "! cat < data/missing.csv",
        "set -n && sort data/x.csv > data/never.csv",
        "exec true && sort data/x.csv > data/never.csv",
        "return 0 && cat < data/x.csv",
        "true && ! cp data/x.csv data/y.csv",
    ],
)
def test_a_form_whose_status_does_not_prove_execution_claims_nothing(project, command):
    (project / "data/y.csv").write_text("y\n")
    event = _event(command, project)
    assert event is not None
    assert event["inputs"] == [] and event["outputs"] == []


def test_a_logical_cd_through_a_symlink_is_followed_as_bash_does(project, tmp_path_factory):
    other = tmp_path_factory.mktemp("other") / "child"
    other.mkdir()
    (project / "link").symlink_to(other)
    (project / "result.csv").write_text("r\n")
    event = _event("cd link && cd .. && sort data/x.csv > result.csv", project)
    [record] = event["outputs"]
    assert Path(record["path"]).resolve() == (project / "result.csv").resolve()
    assert record["sha256"] is not None


def test_a_line_continuation_inside_a_target_is_joined(project):
    (project / "data" / "longname.csv").write_text("x\n")
    event = _event("sort data/x.csv > data/long\\\nname.csv", project)
    [record] = event["outputs"]
    assert record["path"] == str(project / "data/longname.csv")


def test_a_quoted_interpreter_word_is_still_labelled(project):
    event = _event('for n in a; do "Rscript" fit.R; done', project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], "fit.R"), event["analysis_candidates"]


@pytest.mark.parametrize(
    "command",
    [
        "printf %s '" + "a" * 800_000 + "' > data/input.csv",
        "for f in a; do " + " ".join(f"./s{i}.sh;" for i in range(32_000)) + " done",
        "echo " + "/outputs" * 100_000 + "/x",
        "for f in a; do " + " ".join(f"xargs ./s{i}.sh;" for i in range(32_000)) + " done",
    ],
)
def test_round_one_long_commands_scan_in_linear_time(project, command):
    _event_value, seconds = _timed_event(command, project)
    assert seconds < TIME_LIMIT_SECONDS


def test_a_long_command_still_gets_labels_past_the_detection_bound(project):
    command = (
        "for f in a; do ./first.sh; done; echo "
        + "x" * (lineage_event.UNPROVEN_DETECTION_LIMIT_CHARS + 10)
    )
    event = _event(command, project)
    assert [label.rsplit("/", 1)[-1] for label in event["analysis_candidates"]] == ["first.sh"]


def test_unproven_candidates_are_capped(project):
    command = "for f in a; do " + " ".join(f"./s{i}.sh;" for i in range(500)) + " done"
    event = _event(command, project)
    assert len(event["analysis_candidates"]) <= lineage_event.DATA_COMMAND_NAMED_PATH_LIMIT


@pytest.mark.parametrize("host", HOSTS)
def test_a_long_host_session_id_still_names_a_writable_manifest(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "s" * 200
    _start(host, repo, sid)
    _bash(host, repo, sid, "head data/x.csv")
    stop = _stop(host, repo, sid)
    assert not _blocked(stop), stop.stdout
    [path] = _data_only_manifests(repo)
    assert len(path.name) < 120
    status = list((repo / ".living" / "log").glob(".data-lineage-status-*.json"))
    assert all(len(p.name) < 200 for p in status)


@pytest.mark.parametrize("host", HOSTS)
def test_a_long_host_session_id_flushes_parked_records(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "p" * 200
    _accept_idle_turn(host, repo, sid)
    _bash(host, repo, sid, "head data/x.csv")
    _stop(host, repo, sid)
    assert _unowned(repo) == []
    [path] = _data_only_manifests(repo)
    assert len(path.name) < 120


# ---------- Codex review round 2 ----------


@pytest.mark.parametrize(
    "command",
    [
        "time exit 0 && cat < data/x.csv",
        "coproc cat < data/missing.csv",
        "nohup exit 0 && cat < data/x.csv",
        "env true && cat < data/x.csv",
    ],
)
def test_a_wrapper_before_a_status_word_claims_nothing(project, command):
    event = _event(command, project)
    assert event is not None
    assert event["inputs"] == [] and event["outputs"] == []


def test_a_here_string_does_not_start_a_heredoc(project):
    event = _event("cat <<< hello\nfor f in a; do Rscript fit.R; done", project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], "Rscript fit.R"), event["analysis_candidates"]


@pytest.mark.parametrize(
    "command",
    ["CDPATH=/elsewhere cd data && cat < x.csv",
     "CDPATH=/elsewhere && cd data && cat < x.csv"],
)
def test_cdpath_leaves_a_relative_cd_unresolved(project, command):
    event = _event(command, project)
    assert event["inputs"] == []


def test_an_explicitly_relative_cd_ignores_cdpath(project):
    event = _event("CDPATH=/elsewhere && cd ./data && cat < x.csv", project)
    assert [r["path"] for r in event["inputs"]] == [str(project / "data/x.csv")]


@pytest.mark.parametrize(
    ("command", "label"),
    [
        ("for n in a; do uv run python fit.py; done", "python fit.py"),
        ("for n in a; do timeout 10 Rscript fit.R; done", "Rscript fit.R"),
        ("for n in a; do conda run -n env python fit.py; done", "python fit.py"),
        ("printf '%s\\n' fit.py | xargs python3", "python3"),
        ("for n in a; do nice -n 5 python3 fit.py; done", "python3 fit.py"),
    ],
)
def test_a_wrapped_unproven_run_is_labelled(project, command, label):
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], label), event


def test_a_helper_named_in_an_argument_does_not_hide_the_run(project):
    command = (
        "for n in a; do Rscript fit.R --helper "
        "/opt/mycelium/skills/core/scripts/validate_structure.py; done"
    )
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], "Rscript fit.R"), event["analysis_candidates"]


@pytest.mark.parametrize(
    "tail",
    ["rm data/stage && mkdir data/stage", "mv data/stage data/old", "touch data/stage"],
)
def test_a_cp_followed_by_more_file_changes_claims_no_destination(project, tail):
    (project / "data" / "stage").mkdir()
    event = _event(f"cp data/x.csv data/stage && {tail}", project)
    assert event["inputs"] == [] and event["outputs"] == []


def test_a_cp_followed_only_by_reads_still_claims(project):
    shutil.copy(project / "data/x.csv", project / "data/y.csv")
    event = _event("cp data/x.csv data/y.csv && head data/y.csv && wc -l data/y.csv", project)
    assert [r["path"] for r in event["outputs"]] == [str(project / "data/y.csv")]


# ---------- Codex review round 3 ----------


@pytest.mark.parametrize("command", ["cp -a data/link.csv data/copy.csv", "cp data/link.csv data/copy.csv"])
def test_a_copied_symlink_claims_nothing(project, command):
    (project / "data" / "link.csv").symlink_to(project / "data" / "x.csv")
    (project / "data" / "copy.csv").write_text("a,b\n1,2\n")
    event = _event(command, project)
    assert event["inputs"] == [] and event["outputs"] == []


def test_a_no_op_mv_claims_nothing(project):
    event = _event("mv data/x.csv ./data/x.csv", project)
    assert event["inputs"] == [] and event["outputs"] == []


@pytest.mark.parametrize(
    ("command", "label"),
    [
        ("for n in a; do bash ./run.sh; done", "bash ./run.sh"),
        ("for n in a; do sh -e run.sh; done", "sh run.sh"),
        ("for n in a; do env -i python fit.py; done", "python fit.py"),
        ("for n in a; do time -p Rscript fit.R; done", "Rscript fit.R"),
    ],
)
def test_round_three_unproven_runs_are_labelled(project, command, label):
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], label), event["analysis_candidates"]


def test_many_leading_assignments_scan_linearly(project):
    command = " ".join(f"V{i}=1" for i in range(32_000)) + " sort data/x.csv > data/o.csv"
    event, seconds = _timed_event(command, project)
    assert event["outputs"]
    assert seconds < TIME_LIMIT_SECONDS


# ---------- Codex review round 4 ----------


def test_a_renamed_directory_with_a_same_named_child_claims_nothing(project):
    (project / "data" / "archive").mkdir()
    (project / "data" / "archive" / "raw").write_text("child\n")
    event = _event("mv data/raw data/archive", project)
    assert event["inputs"] == [] and event["outputs"] == []


def test_a_moved_symlink_claims_nothing(project):
    os.symlink("x.csv", project / "data" / "moved.csv")
    event = _event("mv data/link.csv data/moved.csv", project)
    assert event["inputs"] == [] and event["outputs"] == []


@pytest.mark.parametrize(
    "command",
    ["for n in a; do python3 -u -m pytest tests -q; done",
     "for n in a; do python3 -W ignore -m pytest; done"],
)
def test_tooling_after_interpreter_flags_is_not_a_run(project, command):
    assert _event(command, project) is None


# ---------- Codex review round 5 ----------


@pytest.mark.parametrize(
    "command",
    ["for n in a; do python3 --version; done", "for n in a; do python3 -h; done",
     "for n in a; do jupyter --show-config; done", "for n in a; do R --version; done"],
)
def test_terminal_only_interpreter_calls_are_not_runs(project, command):
    assert _event(command, project) is None


@pytest.mark.parametrize(
    ("command", "label"),
    [
        ("for x in a; do '/opt/my env/bin/python3' fit.py; done", "/opt/my env/bin/python3 fit.py"),
        ("for x in a; do /opt/my\\ env/bin/Rscript fit.R; done", "/opt/my env/bin/Rscript fit.R"),
        ("echo 'line one\ncat <<EOF'\nfor x in a; do Rscript fit.R; done", "Rscript fit.R"),
        ('cat <<E"OF" > notes.md\npython q.py\nEOF\nfor x in a; do Rscript fit.R; done', "Rscript fit.R"),
    ],
)
def test_round_five_unproven_runs_are_labelled(project, command, label):
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], label), event["analysis_candidates"]
    assert not _has_label(event["analysis_candidates"], "q.py")


# ---------- Codex review round 6 ----------


def test_a_shared_file_replaced_by_a_fifo_fails_instead_of_hanging(project):
    shared = project / "unowned.ndjson"
    os.mkfifo(shared)
    started = time.monotonic()
    result = _main(project, "--bash-cmd", "head data/x.csv", "--check-execution",
                   "--data-command-to", str(shared), "--host", "claude",
                   "--host-session-id", "s1")
    assert result.returncode == 70
    assert time.monotonic() - started < TIME_LIMIT_SECONDS


def test_a_path_swapped_for_a_fifo_after_stat_is_not_read(project, monkeypatch):
    fifo = project / "data" / "swapped.csv"
    os.mkfifo(fifo)
    real = os.stat_result((0o100644, 0, 0, 1, 0, 0, 4, 0, 0, 0))
    # The stat says "regular file" (as before a parallel swap); the open
    # must still refuse the FIFO it finds.
    monkeypatch.setattr(Path, "stat", lambda self, **kw: real)
    started = time.monotonic()
    assert lineage_event.sha256_file(fifo) is None
    assert consolidator.sha256_file(fifo) is None
    assert time.monotonic() - started < TIME_LIMIT_SECONDS


@pytest.mark.parametrize(
    ("command", "label"),
    [
        ('for n in a; do python -c "import analysis; analysis.run()" --help; done',
         "python -c"),
        ("for n in a; do python -m analysis.fit --helper "
         "/opt/mycelium/skills/core/scripts/validate_structure.py; done",
         "python -m"),
    ],
)
def test_payload_arguments_do_not_hide_a_run(project, command, label):
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], label), event["analysis_candidates"]


def test_a_renamed_working_directory_leaves_later_relative_paths_unclaimed(project):
    (project / "data" / "x.csv").write_text("replacement\n")
    event = _event(
        "cd data && mv ../data ../old && mkdir ../data && wc -l < x.csv", project
    )
    assert event["inputs"] == []


# ---------- Codex review round 7 ----------


@pytest.mark.parametrize(
    "mutation",
    ["/bin/mv ../data ../old", "git mv ../data ../old", "python3 rotate.py"],
)
def test_any_command_that_may_move_the_cwd_unresolves_later_paths(project, mutation):
    event = _event(f"cd data && {mutation} && mkdir ../data && wc -l < x.csv", project)
    assert event["inputs"] == []


def test_cwd_stable_commands_keep_relative_paths(project):
    event = _event("cd data && mkdir -p sub && head x.csv && wc -l < x.csv", project)
    assert [r["path"] for r in event["inputs"]] == [str(project / "data/x.csv")]


def test_a_heredoc_terminator_named_like_an_interpreter_is_not_a_run(project):
    assert _event("cat <<R > notes.md\nnotes\nR", project) is None


# ---------- Codex review round 8 ----------


@pytest.mark.parametrize(
    ("command", "named"),
    [
        ('head "data"/sample', "data/sample"),
        ("printf x > 'data'/sample", "data/sample"),
        ('head "data/my file.csv"', "data/my"),
    ],
)
def test_partly_quoted_and_spaced_words_are_named(project, command, named):
    event = _event(command, project)
    assert event is not None
    assert named in event["named_paths"]


@pytest.mark.parametrize(
    "command",
    ["for n in a; do bash -n run.sh; done", "for n in a; do snakemake -n; done",
     "for n in a; do sbatch --test-only job.sh; done",
     "for n in a; do nextflow log; done"],
)
def test_non_executing_runner_modes_are_not_runs(project, command):
    assert _event(command, project) is None


# ---------- Codex review round 9 ----------


@pytest.mark.parametrize(
    ("command", "label"),
    [("for n in a; do bash fit.sh -n 100; done", "bash fit.sh"),
     ("for n in a; do sbatch job.sh --test-only; done", "sbatch job.sh")],
)
def test_script_arguments_are_not_runner_options(project, command, label):
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], label), event["analysis_candidates"]


def test_bash_dash_o_noexec_is_not_a_run(project):
    assert _event("for n in a; do bash -o noexec fit.sh; done", project) is None


@pytest.mark.parametrize("host", HOSTS)
def test_a_data_only_turn_keeps_its_raw_archive_after_number_reuse(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "first")
    _bash(host, repo, "first", "head data/x.csv")
    assert not _blocked(_stop(host, repo, "first"))
    prev = repo / ".mycelium" / "mycelium-data-events-prev"
    [archive] = list(prev.glob("*.tmp"))
    before = archive.read_bytes()

    _start(host, repo, "second")
    _bash(host, repo, "second", "python3 analysis.py")
    _bash(host, repo, "second", "python3 analysis.py", "mycelium-post-action.sh")
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-20] worked\n- noted\n")
    assert not _blocked(_stop(host, repo, "second"))

    assert archive.read_bytes() == before
    assert len(list(prev.glob("*.tmp"))) == 2


# ---------- Codex review round 10 ----------


@pytest.mark.parametrize(
    "command",
    ["for n in a; do jupyter nbconvert --show-config; done",
     "for n in a; do R -e '1' --version; done",
     "for n in a; do python3 -uV; done"],
)
def test_interpreter_specific_terminal_modes_are_not_runs(project, command):
    assert _event(command, project) is None


@pytest.mark.parametrize(
    ("command", "label"),
    [("for n in a; do R -e 'run()' --args --version; done", "R -e"),
     # (The detector does not read a `-uc` cluster as `-c`; labels share
     # its grammar, so the separated form is used.)
     ("for n in a; do python3 -u -c 'import fit' -h; done", "python3 -c")],
)
def test_terminal_words_in_the_payload_do_not_hide_a_run(project, command, label):
    event = _event(command, project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], label), event["analysis_candidates"]


def test_a_cp_may_move_the_cwd(project):
    event = _event("cd data && cp -Pf /tmp/elsewhere .. && cat < x.csv", project)
    assert event["inputs"] == []


# ---------- Codex review round 11: labels share the detector's grammar ----------


@pytest.mark.parametrize(
    "command",
    ["for n in a; do bash --help run.sh; done",
     "for n in a; do bash -en run.sh; done",
     "for n in a; do snakemake --executor dryrun; done"],
)
def test_detector_non_executing_modes_are_not_runs(project, command):
    assert _event(command, project) is None


def test_a_shell_script_after_many_options_is_labelled(project):
    event = _event("for n in a; do bash -e -u -o pipefail -x fit.sh; done", project)
    assert event is not None
    assert _has_label(event["analysis_candidates"], "fit.sh"), event["analysis_candidates"]


def test_many_distinct_segments_stay_bounded(project):
    command = "for f in a; do " + " ".join(f"python3 s{i}.py;" for i in range(3000)) + " done"
    event, seconds = _timed_event(command, project)
    assert event is not None
    assert seconds < TIME_LIMIT_SECONDS


# ---------- Codex review round 12 ----------


@pytest.mark.parametrize(
    "command",
    ["python3 -W ignore - <<'PY'\nprint(1)\nPY", "python3 -X utf8 - <<'PY'\nprint(1)\nPY"],
)
def test_stdin_fed_python_with_valued_options_is_labelled(project, command):
    event = _event(command, project)
    assert event is not None
    assert any(label.endswith("<<heredoc") for label in event["analysis_candidates"])


def test_stdin_fed_python_printing_its_version_is_not_a_run(project):
    assert _event("printf x | xargs python3 -uV", project) is None


# ---------- Codex review round 13 ----------


def test_a_path_missing_at_hook_time_stays_missing_at_stop(project):
    os.replace(project / "data/x.csv", project / "data/z.csv")
    event = _event("mv data/x.csv data/z.csv", project)
    assert event["inputs"][0]["_missing"] is True
    (project / "data/x.csv").write_text("a different file\n")
    stream = project / "events.tmp"
    stream.write_text(json.dumps(event) + "\n")
    manifest = consolidator.extract(stream, "s", str(project))
    [record] = manifest["data_commands"][0]["inputs"]
    assert record.get("_missing") is True
    assert record.get("sha256") is None and "n_rows" not in record


# ---------- Codex review round 14 ----------


def test_a_project_inside_a_claude_worktree_still_has_data(tmp_path):
    project = tmp_path / "repo" / ".claude" / "worktrees" / "wt"
    (project / "data").mkdir(parents=True)
    (project / ".git").write_text("gitdir: elsewhere\n")
    (project / "data" / "x.csv").write_text("a\n")
    shutil.copy(project / "data/x.csv", project / "data/y.csv")
    absolute = _event(f"head {project}/data/x.csv", project)
    assert absolute is not None and absolute["named_paths"] == [f"{project}/data/x.csv"]
    copied = _event("cp data/x.csv data/y.csv", project)
    assert [r["path"] for r in copied["outputs"]] == [str(project / "data/y.csv")]


def test_tooling_inside_the_project_is_still_excluded(tmp_path):
    project = tmp_path / "repo"
    (project / ".living").mkdir(parents=True)
    (project / ".git").mkdir()
    assert _event(f"cat {project}/.living/notes.csv", project) is None
