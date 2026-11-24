"""A live host session keeps its lifecycle after an accepted Stop.

Claude Code and Codex run Stop at the end of every turn, not once per
session. An accepted Stop finalizes and removes the session's transaction, so
before this change every later turn's analysis, edits, and lineage were
dropped silently until the next SessionStart (resume or compaction). Field
evidence: an SNP-tree Claude session ran ~200 detectable analysis commands
and recorded one lineage event.

The PostToolUse hooks that have something to record (an analysis command, a
data command, a project edit) now reopen the same host session's transaction
through SessionStart's own setup. Reads and non-analysis commands never
reopen, and another root's live transaction is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
HOSTS = ["claude", "codex"]


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    living = repo / ".living"
    living.mkdir()
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (living / name).write_text(f"# {name}\n")
    (repo / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    return repo


def _run(host: str, name: str, repo: Path, payload: dict, extra_env=None):
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = host
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(HOOKS_DIR / name)],
        cwd=repo,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _start(host, repo, sid, source="startup"):
    result = _run(
        host, "mycelium-health.sh", repo,
        {"cwd": str(repo), "source": source, "session_id": sid,
         "hook_event_name": "SessionStart"},
    )
    assert result.returncode == 0, result.stderr
    return result


def _stop(host, repo, sid):
    result = _run(
        host, "mycelium-stop-check.sh", repo,
        {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
         "stop_hook_active": False},
    )
    assert result.returncode == 0, result.stderr
    return result


def _bash(host, repo, sid, command, hook, extra_env=None):
    return _run(
        host, hook, repo,
        {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
         "hook_event_name": "PostToolUse",
         "tool_input": {"command": command},
         "tool_response": {"exit_code": 0}},
        extra_env=extra_env,
    )


def _run_dir(repo: Path, host: str, sid: str) -> Path:
    return repo / ".mycelium" / "run" / host / sid


def _events(repo: Path, host: str, sid: str) -> list[dict]:
    path = _run_dir(repo, host, sid) / "mycelium-data-events.tmp"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _unrecorded(repo: Path, host: str, sid: str) -> list[dict]:
    path = _run_dir(repo, host, sid) / "mycelium-unrecorded-events.tmp"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _unowned_skips(repo: Path, host: str, sid: str) -> list[dict]:
    return [
        record for record in _unowned(repo)
        if record.get("kind") == "skip"
        and record.get("host") == host
        and record.get("host_session_id") == sid
    ]


# Hooks poll a busy lock MYCELIUM_*_LOCK_MAX_ATTEMPTS times (default 600, at
# least 50 ms each) before dropping their event with evidence. Tests that run
# many hooks against one session raise both so a loaded runner rarely drops.
_PATIENT_LOCKS = {
    "MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS": "6000",
    "MYCELIUM_STOP_LOCK_MAX_ATTEMPTS": "6000",
}


def _blocked(stop: subprocess.CompletedProcess) -> bool:
    return bool(stop.stdout.strip()) and json.loads(stop.stdout).get(
        "decision"
    ) == "block"


def _accept_idle_turn(host, repo, sid):
    _start(host, repo, sid)
    stop = _stop(host, repo, sid)
    assert not _blocked(stop), stop.stdout
    run_dir = _run_dir(repo, host, sid)
    # The accepted turn leaves only what its successor starts from.
    assert sorted(p.name for p in run_dir.iterdir()) == [
        "next-turn-baseline.json", "next-turn-start-ts.tmp",
    ]


@pytest.mark.parametrize("host", HOSTS)
def test_analysis_after_an_idle_turn_reopens_the_session(tmp_path, host):
    repo = _repo(tmp_path)
    _accept_idle_turn(host, repo, "live-session")

    lineage = _bash(host, repo, "live-session", "python3 analysis.py",
                    "mycelium-data-tracker.sh")
    action = _bash(host, repo, "live-session", "python3 analysis.py",
                   "mycelium-post-action.sh")

    assert lineage.returncode == 0, lineage.stderr
    assert action.returncode == 0, action.stderr
    events = _events(repo, host, "live-session")
    assert len(events) == 1
    assert events[0]["script"].endswith("analysis.py")
    assert "SESSION LOG UPDATE" in action.stdout
    marker = _run_dir(repo, host, "live-session") / "active-session-log.tmp"
    log_path = Path(marker.read_text().splitlines()[0])
    assert log_path.is_file()
    assert log_path.parent == repo / ".living" / "log"
    # Enforcement is back: the turn's Stop asks for bookkeeping again.
    assert _blocked(_stop(host, repo, "live-session"))


@pytest.mark.parametrize("host", HOSTS)
def test_work_after_an_accepted_working_turn_gets_a_fresh_log(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "long-session"
    _start(host, repo, sid)
    _bash(host, repo, sid, "python3 analysis.py", "mycelium-post-action.sh")
    _bash(host, repo, sid, "python3 analysis.py", "mycelium-data-tracker.sh")
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-19] first turn\n- noted\n")
    assert not _blocked(_stop(host, repo, sid))
    first_logs = sorted((repo / ".living" / "log").glob("*-repo.md"))
    assert len(first_logs) == 1
    first_bytes = first_logs[0].read_bytes()
    assert b"\nended: 2" in first_bytes

    _bash(host, repo, sid, "python3 analysis.py", "mycelium-data-tracker.sh")

    logs = sorted((repo / ".living" / "log").glob("*-repo.md"))
    assert len(logs) == 2
    assert first_logs[0].read_bytes() == first_bytes
    assert len(_events(repo, host, sid)) == 1


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    "hook,payload",
    [
        ("mycelium-data-tracker.sh",
         {"tool_name": "Bash", "tool_input": {"command": "ls -la"}}),
        ("mycelium-post-action.sh",
         {"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        ("mycelium-read-tracker.sh",
         {"tool_name": "Read", "tool_input": {"file_path": "README.md"}}),
        ("mycelium-activity-tracker.sh",
         {"tool_name": "Edit",
          "tool_input": {"file_path": ".living/learnings.md"}}),
    ],
)
def test_reads_and_non_work_never_reopen(tmp_path, host, hook, payload):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("readme\n")
    _accept_idle_turn(host, repo, "idle-session")
    logs_before = sorted(p.name for p in (repo / ".living" / "log").iterdir())
    payload = dict(payload, cwd=str(repo), session_id="idle-session",
                   tool_response={"exit_code": 0})
    if payload["tool_input"].get("file_path"):
        payload["tool_input"] = {
            "file_path": str(repo / payload["tool_input"]["file_path"])
        }

    result = _run(host, hook, repo, payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (_run_dir(repo, host, "idle-session") / "active-session-log.tmp").exists()
    assert sorted(
        p.name for p in (repo / ".living" / "log").iterdir()
    ) == logs_before


@pytest.mark.parametrize("host", HOSTS)
def test_project_edit_after_an_idle_turn_reopens(tmp_path, host):
    repo = _repo(tmp_path)
    _accept_idle_turn(host, repo, "edit-session")
    (repo / "model.py").write_text("x = 1\n")

    result = _run(
        host, "mycelium-activity-tracker.sh", repo,
        {"cwd": str(repo), "session_id": "edit-session", "tool_name": "Edit",
         "tool_input": {"file_path": str(repo / "model.py")},
         "tool_response": {"exit_code": 0}},
    )

    assert result.returncode == 0, result.stderr
    activity = _run_dir(repo, host, "edit-session") / "mycelium-session-activity.tmp"
    assert activity.read_text().splitlines() == ["model.py"]
    assert _blocked(_stop(host, repo, "edit-session"))


@pytest.mark.parametrize("host", HOSTS)
def test_parallel_hooks_reopen_exactly_one_transaction(tmp_path, host):
    """Parallel work hooks after an accepted turn reopen one transaction.

    Every analysis command lands in that transaction's event stream once,
    or its hook leaves the evidence it is designed to leave when a lock
    stays busy past its wait: a line in the transaction's unrecorded-events
    file, or an unowned "no-transaction" skip for this session. Both lock
    waits are raised so a loaded runner rarely drops an event at all.
    """
    repo = _repo(tmp_path)
    sid = "parallel-session"
    _accept_idle_turn(host, repo, sid)
    hooks = ["mycelium-post-action.sh", "mycelium-data-tracker.sh"] * 4

    with ThreadPoolExecutor(len(hooks)) as pool:
        results = list(pool.map(
            lambda hook: _bash(host, repo, sid, "python3 analysis.py", hook,
                               extra_env=_PATIENT_LOCKS),
            hooks,
        ))

    assert all(r.returncode == 0 for r in results), [r.stderr for r in results]
    logs = sorted((repo / ".living" / "log").glob("*-repo.md"))
    assert len(logs) == 1
    marker = _run_dir(repo, host, sid) / "active-session-log.tmp"
    assert Path(marker.read_text().splitlines()[0]) == logs[0]
    events = _events(repo, host, sid)
    unrecorded = _unrecorded(repo, host, sid)
    unowned = _unowned_skips(repo, host, sid)
    evidence = {
        "events": events, "unrecorded": unrecorded, "unowned": unowned,
        "stderr": [r.stderr for r in results if r.stderr],
    }
    assert events, evidence
    data_unrecorded = [
        line for line in unrecorded if line["hook"] == "mycelium-data-tracker.sh"
    ]
    assert all(line["kind"] == "lineage" for line in data_unrecorded), evidence
    assert all(
        skip.get("skip_reason") == "no-transaction" for skip in unowned
    ), evidence
    assert len(events) + len(data_unrecorded) + len(unowned) == 4, evidence


@pytest.mark.parametrize("host", HOSTS)
def test_reopen_never_touches_another_live_root(tmp_path, host):
    repo = _repo(tmp_path)
    _start(host, repo, "other-root")
    other = _run_dir(repo, host, "other-root")
    other_before = {p.name: p.read_bytes() for p in other.iterdir() if p.is_file()}
    _accept_idle_turn(host, repo, "this-root")

    _bash(host, repo, "this-root", "python3 analysis.py",
          "mycelium-data-tracker.sh")

    assert len(_events(repo, host, "this-root")) == 1
    assert {
        p.name: p.read_bytes() for p in other.iterdir() if p.is_file()
    } == other_before


@pytest.mark.parametrize("host", HOSTS)
def test_reopen_refuses_unsafe_state(tmp_path, host):
    repo = _repo(tmp_path)
    _accept_idle_turn(host, repo, "unsafe-session")
    outside = tmp_path / "outside"
    outside.mkdir()
    run_root = repo / ".mycelium" / "run"
    shutil.rmtree(run_root)
    run_root.symlink_to(outside, target_is_directory=True)

    result = _bash(host, repo, "unsafe-session", "python3 analysis.py",
                   "mycelium-data-tracker.sh")

    assert result.returncode == 0
    assert list(outside.iterdir()) == []
    # Nothing is written into state that failed its symlink checks, not even
    # an unowned skip record.
    assert not (repo / ".mycelium" / "lineage-skips-unowned.ndjson").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_extractor_failure_is_recorded_as_a_skip_and_warned_at_stop(
    tmp_path, host
):
    repo = _repo(tmp_path)
    sid = "skip-session"
    _start(host, repo, sid)
    broken = tmp_path / "broken_extractor.py"
    broken.write_text("raise SystemExit(3)\n")

    result = _run(
        host, "mycelium-data-tracker.sh", repo,
        {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
         "tool_input": {"command": "python3 analysis.py"},
         "tool_response": {"exit_code": 0}},
        extra_env={"MYCELIUM_DATA_HELPER": str(broken)},
    )

    assert result.returncode == 0, result.stderr
    records = _events(repo, host, sid)
    assert len(records) == 1
    skip = records[0]
    assert skip["kind"] == "skip"
    assert skip["skip_reason"] == "extractor-failed"
    assert skip["bash_cmd"] == "python3 analysis.py"
    assert skip["detail"] == "exit 3"

    stop = _stop(host, repo, sid)
    assert "LINEAGE GAP" in stop.stdout
    assert "extractor-failed" in stop.stdout
    manifests = list((repo / ".living" / "log" / "data-lineage").glob("*.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["n_actions"] == 0
    assert [s["skip_reason"] for s in manifest["skipped"]] == ["extractor-failed"]
    assert manifest["summary"]["skipped_count"] == 1


def _unowned(repo: Path) -> list[dict]:
    path = repo / ".mycelium" / "lineage-skips-unowned.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


@pytest.mark.parametrize("host", HOSTS)
def test_reopen_validates_a_transaction_a_parallel_hook_just_opened(tmp_path, host):
    """The race Codex review found: another hook publishes the marker between
    this hook's failed validation and its reopen check. The hook must validate
    that transaction and record, not give up."""
    repo = _repo(tmp_path)
    sid = "race-session"
    _accept_idle_turn(host, repo, sid)
    payload = json.dumps({"cwd": str(repo), "session_id": sid,
                          "tool_name": "Bash",
                          "tool_input": {"command": "python3 analysis.py"}})
    reopen = json.dumps({"cwd": str(repo), "session_id": sid,
                         "hook_event_name": "SessionStart", "source": "reopen"})
    script = f"""
set -euo pipefail
source {HOOKS_DIR / "mycelium-hook-lib.sh"}
INPUT={json.dumps(payload)}
if mycelium_prepare_locked_post_tool_state "$REPO" "$INPUT"; then exit 9; fi
printf '%s' {json.dumps(reopen)} | {HOOKS_DIR / "mycelium-health.sh"} >/dev/null
mycelium_reopen_post_tool_transaction "$REPO" "$INPUT" || exit 3
mycelium_prepare_locked_post_tool_state "$REPO" "$INPUT" || exit 4
mycelium_release_session_lock
"""
    env = dict(os.environ, MYCELIUM_HOOK_HOST=host, REPO=str(repo))
    result = subprocess.run(["bash", "-c", script], cwd=repo, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, (result.returncode, result.stderr)


@pytest.mark.parametrize("host", HOSTS)
def test_reopen_leaves_a_corrupt_marker_untouched(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "corrupt-session"
    _accept_idle_turn(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    run_dir.mkdir(parents=True, exist_ok=True)
    marker = run_dir / "active-session-log.tmp"
    marker.write_bytes(b"not a marker\n")
    logs_before = sorted(p.name for p in (repo / ".living" / "log").iterdir())

    start = _run(host, "mycelium-health.sh", repo,
                 {"cwd": str(repo), "session_id": sid, "source": "reopen",
                  "hook_event_name": "SessionStart"})
    lineage = _bash(host, repo, sid, "python3 analysis.py",
                    "mycelium-data-tracker.sh")

    assert start.returncode == 0 and lineage.returncode == 0
    assert marker.read_bytes() == b"not a marker\n"
    assert sorted(p.name for p in (repo / ".living" / "log").iterdir()) == logs_before
    assert _events(repo, host, sid) == []
    assert [r["skip_reason"] for r in _unowned(repo)] == ["no-transaction"]


@pytest.mark.parametrize("host", HOSTS)
def test_first_command_changes_after_an_idle_turn_still_count(tmp_path, host):
    """An inline one-liner opens no post-action cycle, so only the carried
    baseline makes its file write count as this turn's work."""
    repo = _repo(tmp_path)
    sid = "inline-session"
    _accept_idle_turn(host, repo, sid)
    command = "python3 -c \"open('out.txt', 'w').write('x')\""
    (repo / "out.txt").write_text("x")  # the command's side effect

    lineage = _bash(host, repo, sid, command, "mycelium-data-tracker.sh")

    assert lineage.returncode == 0, lineage.stderr
    assert len(_events(repo, host, sid)) == 1
    stop = _stop(host, repo, sid)
    assert _blocked(stop), stop.stdout
    assert "out.txt" in json.loads(stop.stdout)["reason"]


@pytest.mark.parametrize("host", HOSTS)
def test_detector_failure_without_a_transaction_is_reported_at_stop(
    tmp_path, host
):
    repo = _repo(tmp_path)
    sid = "detector-session"
    _accept_idle_turn(host, repo, sid)
    broken = tmp_path / "broken_detector.py"
    broken.write_text("raise SystemExit(70)\n")

    result = _run(
        host, "mycelium-data-tracker.sh", repo,
        {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
         "tool_input": {"command": "python3 analysis.py"},
         "tool_response": {"exit_code": 0}},
        extra_env={"MYCELIUM_DATA_HELPER": str(broken)},
    )

    assert result.returncode == 0, result.stderr
    skips = _unowned(repo)
    assert [(r["skip_reason"], r["detail"], r["host_session_id"], r["host"])
            for r in skips] == [("detector-failed", "exit 70", sid, host)]
    stop = _stop(host, repo, sid)
    assert "LINEAGE GAP" in stop.stdout
    assert "detector-failed" in stop.stdout
    assert _unowned(repo) == []
    # Reported once: the next Stop has nothing more to say.
    assert "LINEAGE GAP" not in _stop(host, repo, sid).stdout


@pytest.mark.parametrize("host", HOSTS)
def test_unowned_skips_join_the_manifest_when_a_transaction_exists(
    tmp_path, host
):
    repo = _repo(tmp_path)
    sid = "joined-session"
    _start(host, repo, sid)
    shared = repo / ".mycelium"
    other = {"ts": "2026-11-19T00:00:00Z", "kind": "skip",
             "skip_reason": "no-transaction", "host": host,
             "host_session_id": "someone-else"}
    mine = dict(other, host_session_id=sid, skip_reason="detector-failed")
    (shared / "lineage-skips-unowned.ndjson").write_text(
        json.dumps(other) + "\n" + json.dumps(mine) + "\n"
    )

    stop = _stop(host, repo, sid)

    assert "detector-failed" in stop.stdout
    assert _unowned(repo) == [other]
    manifest = json.loads(next(
        (repo / ".living" / "log" / "data-lineage").glob("*.json")
    ).read_text())
    assert [s["skip_reason"] for s in manifest["skipped"]] == ["detector-failed"]


@pytest.mark.parametrize("host", HOSTS)
def test_missing_extractor_is_one_gap_not_one_per_command(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "missing-session"
    _start(host, repo, sid)
    env = {"MYCELIUM_DATA_HELPER": str(tmp_path / "absent.py")}
    for command in ("ls -la", "python3 analysis.py", "git status"):
        result = _run(
            host, "mycelium-data-tracker.sh", repo,
            {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
             "tool_input": {"command": command},
             "tool_response": {"exit_code": 0}},
            extra_env=env,
        )
        assert result.returncode == 0, result.stderr

    records = _events(repo, host, sid)
    assert [r["skip_reason"] for r in records] == ["extractor-missing"]


@pytest.mark.parametrize("host", HOSTS)
def test_an_aged_carryover_survives_other_startups_and_still_counts(
    tmp_path, host
):
    """Codex round 2: a week-old sweep deleted a live idle session's baseline,
    so its next inline write counted as pre-existing. Nothing deletes it now."""
    repo = _repo(tmp_path)
    sid = "idle-for-a-week"
    _accept_idle_turn(host, repo, sid)
    carried = _run_dir(repo, host, sid) / "next-turn-baseline.json"
    old = time.time() - 8 * 86400
    os.utime(carried, (old, old))

    _start(host, repo, "another-root")
    assert carried.is_file()
    (repo / "out.txt").write_text("x")
    _bash(host, repo, sid, "python3 -c \"open('out.txt', 'w').write('x')\"",
          "mycelium-data-tracker.sh")

    stop = _stop(host, repo, sid)
    assert _blocked(stop), stop.stdout
    assert "out.txt" in json.loads(stop.stdout)["reason"]


@pytest.mark.parametrize("host", HOSTS)
def test_a_commit_by_the_reopening_command_still_counts(tmp_path, host):
    """Codex round 2: the reopened log's start time came after the command,
    so a commit it made fell outside the window commits are read from."""
    repo = _repo(tmp_path)
    sid = "committing-session"
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "base"], check=True)
    _accept_idle_turn(host, repo, sid)
    time.sleep(1.1)
    (repo / "result.txt").write_text("42\n")
    subprocess.run(["git", "-C", str(repo), "add", "result.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "result"], check=True)
    time.sleep(1.1)

    _bash(host, repo, sid,
          "python3 -c \"open('result.txt', 'w').write('42')\" && git commit -am r",
          "mycelium-data-tracker.sh")

    stop = _stop(host, repo, sid)
    assert _blocked(stop), stop.stdout
    assert "result.txt" in json.loads(stop.stdout)["reason"]


@pytest.mark.parametrize("host", HOSTS)
def test_gaps_behind_a_corrupt_marker_are_reported_not_archived(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "corrupt-stop-session"
    _accept_idle_turn(host, repo, sid)
    marker = _run_dir(repo, host, sid) / "active-session-log.tmp"
    marker.write_bytes(b"not a marker\n")
    _bash(host, repo, sid, "python3 analysis.py", "mycelium-data-tracker.sh")
    assert [r["skip_reason"] for r in _unowned(repo)] == ["no-transaction"]

    stop = _stop(host, repo, sid)

    assert "no lineage transaction was open" in stop.stdout
    assert "listed under `skipped`" not in stop.stdout
    assert _unowned(repo) == []


@pytest.mark.parametrize("host", HOSTS)
def test_a_foreign_owner_does_not_hide_this_sessions_gaps(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "displaced-session"
    _accept_idle_turn(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    log = repo / ".living" / "log" / "2026-01-01-001-repo.md"
    log.write_text("---\nsession_id: 2026-01-01-001\nended:\n---\n")
    (run_dir / "active-session-owner-id.tmp").write_text("someone-else\n")
    (run_dir / "active-session-log.tmp").write_text(f"{log}\n1700000000\nowner-id-v1\n")
    foreign_before = {p.name: p.read_bytes() for p in run_dir.iterdir()}

    _bash(host, repo, sid, "python3 analysis.py", "mycelium-data-tracker.sh")
    assert [r["skip_reason"] for r in _unowned(repo)] == ["no-transaction"]
    stop = _stop(host, repo, sid)

    assert "LINEAGE GAP" in stop.stdout
    assert _unowned(repo) == []
    assert {p.name: p.read_bytes() for p in run_dir.iterdir()} == foreign_before


@pytest.mark.parametrize("host", HOSTS)
def test_a_failed_rewrite_keeps_every_unowned_record(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "rewrite-session"
    _accept_idle_turn(host, repo, sid)
    shared = repo / ".mycelium"
    records = [
        {"ts": "t", "kind": "skip", "skip_reason": "no-transaction",
         "host": host, "host_session_id": owner}
        for owner in (sid, "other-session")
    ]
    path = shared / "lineage-skips-unowned.ndjson"
    original = "".join(json.dumps(r) + "\n" for r in records)
    path.write_text(original)
    shared.chmod(0o555)  # the atomic replacement's temporary file cannot be made
    try:
        stop = _stop(host, repo, sid)
    finally:
        shared.chmod(0o755)

    assert path.read_text() == original
    assert "LINEAGE GAP" not in stop.stdout


def test_once_records_are_kept_per_host(tmp_path):
    repo = _repo(tmp_path)
    env = {"MYCELIUM_DATA_HELPER": str(tmp_path / "absent.py")}
    for host in HOSTS:
        _accept_idle_turn(host, repo, "shared-id")
        for _ in range(2):
            _run(host, "mycelium-data-tracker.sh", repo,
                 {"cwd": str(repo), "session_id": "shared-id", "tool_name": "Bash",
                  "tool_input": {"command": "python3 analysis.py"}},
                 extra_env=env)
    assert sorted((r["host"], r["skip_reason"]) for r in _unowned(repo)) == [
        ("claude", "extractor-missing"), ("codex", "extractor-missing"),
    ]


@pytest.mark.parametrize("host", HOSTS)
def test_no_unowned_record_is_written_into_a_symlinked_run_tree(tmp_path, host):
    repo = _repo(tmp_path)
    _accept_idle_turn(host, repo, "linked-session")
    outside = tmp_path / "outside"
    outside.mkdir()
    run_root = repo / ".mycelium" / "run"
    shutil.rmtree(run_root)
    run_root.symlink_to(outside, target_is_directory=True)

    _run(host, "mycelium-data-tracker.sh", repo,
         {"cwd": str(repo), "session_id": "linked-session", "tool_name": "Bash",
          "tool_input": {"command": "python3 analysis.py"}},
         extra_env={"MYCELIUM_DATA_HELPER": str(tmp_path / "absent.py")})

    assert list(outside.iterdir()) == []
    assert not (repo / ".mycelium" / "lineage-skips-unowned.ndjson").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_a_shell_only_edit_after_an_idle_turn_is_enforced(tmp_path, host):
    """Codex round 3: `printf > model.py` reopens nothing from PostToolUse, so
    Stop compares against the carried baseline and reopens itself."""
    repo = _repo(tmp_path)
    sid = "shell-edit-session"
    _accept_idle_turn(host, repo, sid)
    (repo / "model.py").write_text("changed\n")

    stop = _stop(host, repo, sid)

    assert _blocked(stop), stop.stdout
    assert "model.py" in json.loads(stop.stdout)["reason"]
    assert (_run_dir(repo, host, sid) / "active-session-log.tmp").is_file()


@pytest.mark.parametrize("host", HOSTS)
def test_an_idle_turn_after_an_idle_turn_opens_nothing(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "twice-idle-session"
    _accept_idle_turn(host, repo, sid)
    logs_before = sorted(p.name for p in (repo / ".living" / "log").iterdir())
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\nnote\n")  # .living edits are never shell work

    stop = _stop(host, repo, sid)

    assert stop.stdout == ""
    assert not (_run_dir(repo, host, sid) / "active-session-log.tmp").exists()
    assert sorted(p.name for p in (repo / ".living" / "log").iterdir()) == logs_before


@pytest.mark.parametrize("host", HOSTS)
def test_a_script_that_deleted_itself_is_a_recorded_gap(tmp_path, host):
    """A missing script is no evidence of a run (`python #a.py` names a
    comment), so it never becomes an event; it is recorded as a gap."""
    repo = _repo(tmp_path)
    sid = "temp-script-session"
    _start(host, repo, sid)

    result = _bash(host, repo, sid, "python3 scratch.py && rm scratch.py",
                   "mycelium-data-tracker.sh")

    assert result.returncode == 0, result.stderr
    records = _events(repo, host, sid)
    assert [(r.get("kind"), r.get("skip_reason")) for r in records] == [
        ("skip", "script-unreadable")
    ]
    assert records[0]["detail"].endswith("scratch.py")


@pytest.mark.parametrize("host", HOSTS)
def test_a_repeated_skip_is_counted_once(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "repeat-session"
    _start(host, repo, sid)
    record = {"ts": "2026-11-19T00:00:00Z", "kind": "skip", "skip_id": "abc",
              "skip_reason": "no-transaction"}
    events = _run_dir(repo, host, sid) / "mycelium-data-events.tmp"
    events.write_text(json.dumps(record) + "\n" + json.dumps(record) + "\n")

    stop = _stop(host, repo, sid)

    assert "LINEAGE GAP: 1 data command" in stop.stdout
    manifest = json.loads(next(
        (repo / ".living" / "log" / "data-lineage").glob("*.json")
    ).read_text())
    assert manifest["summary"]["skipped_count"] == 1


@pytest.mark.parametrize("host", HOSTS)
def test_a_line_separator_in_a_command_keeps_one_skip_record(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "separator-session"
    _start(host, repo, sid)
    broken = tmp_path / "broken_extractor.py"
    broken.write_text("raise SystemExit(3)\n")
    command = "python3 analysis.py  #     \u0085"

    _run(host, "mycelium-data-tracker.sh", repo,
         {"cwd": str(repo), "session_id": sid, "tool_name": "Bash",
          "tool_input": {"command": command}},
         extra_env={"MYCELIUM_DATA_HELPER": str(broken)})
    _stop(host, repo, sid)

    manifest = json.loads(next(
        (repo / ".living" / "log" / "data-lineage").glob("*.json")
    ).read_text())
    # One record, its command whole (not split at the separators), in the
    # committed manifest's allowlisted form.
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lineage_redact

    assert [s["bash_cmd"] for s in manifest["skipped"]] == [
        lineage_redact.commit_safe_command(command, set())]
    # The unquoted comment (through the real line end) is one placeholder.
    assert manifest["skipped"][0]["bash_cmd"] == "python3 <v>  <comment>"
    assert manifest["extraction_warnings"] == []


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("bad_id", [False, 7, "..", ""])
def test_no_unowned_skip_for_a_malformed_session_id(tmp_path, host, bad_id):
    repo = _repo(tmp_path)
    (repo / ".mycelium").mkdir()
    _run(host, "mycelium-data-tracker.sh", repo,
         {"cwd": str(repo), "session_id": bad_id, "tool_name": "Bash",
          "tool_input": {"command": "python3 analysis.py"}},
         extra_env={"MYCELIUM_DATA_HELPER": str(tmp_path / "absent.py")})
    assert not (repo / ".mycelium" / "lineage-skips-unowned.ndjson").exists()


@pytest.mark.parametrize("host", HOSTS)
def test_a_reopen_after_a_long_idle_is_marked_live_now(tmp_path, host):
    """Codex round 4: the carried start time is an accounting boundary, not the
    owner's liveness clock; copying it into the marker made a later resume
    treat the fresh transaction as abandoned."""
    repo = _repo(tmp_path)
    sid = "long-idle-session"
    _accept_idle_turn(host, repo, sid)
    run_dir = _run_dir(repo, host, sid)
    (run_dir / "next-turn-start-ts.tmp").write_text(f"{int(time.time()) - 3 * 3600}\n")

    _bash(host, repo, sid, "python3 -c \"print(1)\"", "mycelium-data-tracker.sh")
    marker_ts = int((run_dir / "active-session-log.tmp").read_text().splitlines()[1])
    assert abs(marker_ts - time.time()) < 120
    events_before = (run_dir / "mycelium-data-events.tmp").read_bytes()

    _start(host, repo, sid, source="resume")

    assert (run_dir / "mycelium-data-events.tmp").read_bytes() == events_before
    assert (run_dir / "active-session-log.tmp").is_file()


@pytest.mark.parametrize("host", HOSTS)
def test_an_earlier_note_cannot_excuse_later_shell_work(tmp_path, host):
    """Codex round 4: a .living-only turn left the old baseline, so its note
    later counted as reflection for a shell-only project edit."""
    repo = _repo(tmp_path)
    sid = "note-then-edit-session"
    _accept_idle_turn(host, repo, sid)
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-19] an unrelated note\n- earlier turn\n")
    assert not _blocked(_stop(host, repo, sid))
    time.sleep(1.1)

    (repo / "model.py").write_text("changed\n")
    stop = _stop(host, repo, sid)

    assert _blocked(stop), stop.stdout
    assert "model.py" in json.loads(stop.stdout)["reason"]


@pytest.mark.parametrize("host", HOSTS)
def test_a_relative_state_override_is_found_from_a_subdirectory(tmp_path, host):
    repo = _repo(tmp_path)
    sid = "override-session"
    sub = repo / "analysis"
    sub.mkdir()
    env = {"MYCELIUM_STATE_DIR": "custom/state"}
    for hook, payload in (
        ("mycelium-health.sh", {"source": "startup", "hook_event_name": "SessionStart"}),
        ("mycelium-stop-check.sh", {"hook_event_name": "Stop", "stop_hook_active": False}),
    ):
        _run(host, hook, repo, dict(payload, cwd=str(repo), session_id=sid),
             extra_env=env)
    shared = repo / "custom" / "state"
    record = {"ts": "t", "kind": "skip", "skip_id": "x", "host": host,
              "host_session_id": sid, "skip_reason": "detector-failed"}
    (shared / "lineage-skips-unowned.ndjson").write_text(json.dumps(record) + "\n")

    stop = subprocess.run(
        [str(HOOKS_DIR / "mycelium-stop-check.sh")], cwd=sub,
        input=json.dumps({"cwd": str(sub), "session_id": sid,
                          "hook_event_name": "Stop", "stop_hook_active": False}),
        text=True, capture_output=True,
        env=dict(os.environ, MYCELIUM_HOOK_HOST=host, **env),
    )

    assert stop.returncode == 0, stop.stderr
    assert "detector-failed" in stop.stdout


def _working_turn(host, repo, sid):
    _start(host, repo, sid)
    _bash(host, repo, sid, "python3 analysis.py", "mycelium-post-action.sh")
    with (repo / ".living" / "learnings.md").open("a") as handle:
        handle.write("\n### [2026-11-19] worked\n- noted\n")


@pytest.mark.parametrize("host", HOSTS)
def test_a_failed_successor_snapshot_blocks_and_keeps_state(tmp_path, host):
    """Codex round 5: a failed snapshot used to accept with no successor
    baseline, so the next turn's shell-only edits went unenforced."""
    repo = _repo(tmp_path)
    sid = "snapshot-fail-session"
    _working_turn(host, repo, sid)
    real = HOOKS_DIR.parent / "scripts" / "session_file_changes.py"
    wrapper = tmp_path / "changes_wrapper.py"
    wrapper.write_text(
        "import runpy, sys\n"
        "if sys.argv[1:2] == ['snapshot']:\n"
        "    raise SystemExit(1)\n"
        f"sys.argv[0] = {str(real)!r}\n"
        f"runpy.run_path({str(real)!r}, run_name='__main__')\n"
    )
    run_dir = _run_dir(repo, host, sid)

    blocked = _run(host, "mycelium-stop-check.sh", repo,
                   {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                    "stop_hook_active": False},
                   extra_env={"MYCELIUM_SESSION_CHANGES_HELPER": str(wrapper)})

    assert _blocked(blocked), blocked.stdout
    assert "next turn starts from could not be written" in blocked.stdout
    assert (run_dir / "active-session-log.tmp").is_file()
    assert (run_dir / "session-file-baseline.json").is_file()

    accepted = _stop(host, repo, sid)
    assert not _blocked(accepted), accepted.stdout
    assert (run_dir / "next-turn-baseline.json").is_file()


@pytest.mark.parametrize("host", HOSTS)
def test_a_failed_skip_transfer_blocks_and_the_retry_counts_once(tmp_path, host):
    """Codex round 5: the move appended to the session stream, the shared
    rewrite failed, and Stop accepted, so a later turn reported the gap again."""
    repo = _repo(tmp_path)
    sid = "transfer-fail-session"
    _start(host, repo, sid)
    shared = repo / ".mycelium"
    record = {"ts": "t", "kind": "skip", "skip_id": "only-once", "host": host,
              "host_session_id": sid, "skip_reason": "detector-failed"}
    unowned = shared / "lineage-skips-unowned.ndjson"
    unowned.write_text(json.dumps(record) + "\n")
    inject = tmp_path / "inject"
    inject.mkdir()
    (inject / "sitecustomize.py").write_text(
        "import os\n"
        "_replace = os.replace\n"
        "def replace(src, dst, *a, **k):\n"
        "    if str(dst).endswith('lineage-skips-unowned.ndjson'):\n"
        "        raise OSError(28, 'injected ENOSPC')\n"
        "    return _replace(src, dst, *a, **k)\n"
        "os.replace = replace\n"
    )
    stop_payload = {"cwd": str(repo), "session_id": sid, "hook_event_name": "Stop",
                    "stop_hook_active": False}

    blocked = _run(host, "mycelium-stop-check.sh", repo, stop_payload,
                   extra_env={"PYTHONPATH": str(inject)})

    assert _blocked(blocked), blocked.stdout
    assert "could not be moved" in blocked.stdout
    assert unowned.read_text() == json.dumps(record) + "\n"

    accepted = _stop(host, repo, sid)
    assert not _blocked(accepted), accepted.stdout
    assert "LINEAGE GAP: 1 data command" in accepted.stdout
    assert _unowned(repo) == []
    manifest = json.loads(next(
        (repo / ".living" / "log" / "data-lineage").glob("*.json")
    ).read_text())
    assert manifest["summary"]["skipped_count"] == 1
    # A later turn has nothing left to report.
    _start(host, repo, sid, source="resume")
    assert "LINEAGE GAP" not in _stop(host, repo, sid).stdout
