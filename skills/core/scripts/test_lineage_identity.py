"""v2 lineage records carry identity, a path frame and hash origins.

Item 3a of the provenance work: every record says which event it is, which
host session made it, which checkout its paths are relative to, and how
each hash was taken; manifests are versioned and published atomically; and
observations carry the journal position that orders writes without clocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "out").mkdir()
    (repo / ".living").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "data" / "in.csv").write_text("a,b\n1,2\n")
    (repo / "fit.py").write_text(
        "import pandas as pd\n"
        "df = pd.read_csv('data/in.csv')\n"
        "df.to_csv('out/res.csv')\n"
    )
    return repo


def _run_extractor(repo: Path, command: str, *extra: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo), "--ts", "2026-11-22T10:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", *extra],
        capture_output=True, text=True, check=True,
    )
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_analysis_event_carries_identity_and_frame(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (event,) = _run_extractor(
        repo, "python fit.py", "--host", "claude", "--host-session-id", "s-1",
        "--repo-root", str(repo),
    )
    assert len(event["event_id"]) == 32
    assert event["host"] == "claude"
    assert event["host_session_id"] == "s-1"
    assert isinstance(event["ts_ns"], int)
    assert event["repo_root"] == os.path.realpath(repo)
    (inp,) = event["inputs"]
    (out,) = event["outputs"]
    assert inp["rel_path"] == "data/in.csv"
    assert out["rel_path"] == "out/res.csv"
    # No proof was attempted (no observer here): the hook's own digest.
    assert inp["hash_origin"] == "hook-post"


def test_two_runs_get_distinct_event_ids(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    first = _run_extractor(repo, "python fit.py", "--repo-root", str(repo))[0]
    second = _run_extractor(repo, "python fit.py", "--repo-root", str(repo))[0]
    assert first["event_id"] != second["event_id"]


def test_host_is_kept_without_a_session_id(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (event,) = _run_extractor(repo, "python fit.py", "--host", "codex", "--repo-root", str(repo))
    assert event["host"] == "codex"
    assert event["host_session_id"] is None


def test_repo_root_falls_back_to_git(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (event,) = _run_extractor(repo, "python fit.py")
    assert event["repo_root"] == os.path.realpath(repo)


def test_paths_outside_or_climbing_are_not_framed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    outside = tmp_path / "elsewhere.csv"
    outside.write_text("x\n")
    record = {"path": str(outside)}
    import extract_data_lineage_event as ev

    ev._stamp_file_frame(record, os.path.realpath(repo))
    assert record["rel_path"] is None
    assert record["rel_path_reason"] == "outside-scope"
    climbing = {"path": str(repo / "out" / ".." / "data" / "in.csv")}
    ev._stamp_file_frame(climbing, os.path.realpath(repo))
    assert climbing["rel_path"] is None
    assert climbing["rel_path_reason"] == "parent-traversal"


def test_symlinked_component_is_not_framed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path)
    (tmp_path / "scratch").mkdir()
    (tmp_path / "scratch" / "x.csv").write_text("x\n")
    os.symlink(tmp_path / "scratch", repo / "results")
    import extract_data_lineage_event as ev

    record = {"path": str(repo / "results" / "x.csv")}
    ev._stamp_file_frame(record, os.path.realpath(repo))
    assert record["rel_path"] is None
    assert record["rel_path_reason"] == "symlinked-path"


@pytest.mark.parametrize(
    ("record", "is_input", "origin"),
    [
        ({"sha256": "a" * 64, "pre_run_proven": True, "hash_status": "hashed"}, True,
         "pre-run-proven"),
        ({"sha256": "a" * 64, "pre_run_proven": False, "hash_status": "changed-during-call"},
         True, "hashed-at-post"),
        ({"sha256": "a" * 64, "pre_run_proven": False, "hash_status": "hashed-at-post"}, True,
         "hook-post"),
        ({"sha256": "a" * 64}, True, "hook-post"),
        ({"sha256": "a" * 64, "pre_run_proven": True}, False, "hook-post"),
        ({"sha256": None, "hash_status": "pending"}, True, None),
    ],
)
def test_hash_origin_never_overstates(record: dict, is_input: bool, origin: str | None) -> None:
    import extract_data_lineage_event as ev

    ev._stamp_hash_origin(record, is_input=is_input)
    assert record.get("hash_origin") == origin


def _events_file(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def test_manifest_is_versioned_and_keeps_identity(tmp_path: Path) -> None:
    events = _events_file(tmp_path / "events.tmp", [
        {"ts": "2026-11-22T10:00:00Z", "event_id": "e" * 32, "host": "claude",
         "host_session_id": "s-1", "ts_ns": 5, "repo_root": "/r", "bash_cmd": "python fit.py",
         "inputs": [], "outputs": [], "step": {"argv": ["python", "fit.py"]}},
        {"ts": "2026-11-22T10:00:01Z", "kind": "data_command", "event_id": "d" * 32,
         "host": "claude", "host_session_id": "s-1", "ts_ns": 6, "repo_root": "/r",
         "bash_cmd": "cp a b", "inputs": [], "outputs": []},
        {"ts": "2026-11-22T10:00:02Z", "ts_ns": 7, "kind": "skip", "skip_id": "k" * 32,
         "skip_reason": "detector-failed", "host": "claude", "host_session_id": "s-1"},
    ])
    manifest = stop.extract(events, "sess", str(tmp_path))
    assert manifest["schema"] == stop.MANIFEST_SCHEMA
    (action,) = manifest["actions"]
    assert action["event_id"] == "e" * 32
    assert action["host"] == "claude"
    assert action["host_session_id"] == "s-1"
    assert action["ts_ns"] == 5
    # The step travels into the manifest under the commit allowlist:
    # `fit.py` is not one of the record's own paths, so it is a placeholder.
    assert action["step"] == {"argv": ["python", "<v>"]}
    assert "filters_detected" not in action
    (command,) = manifest["data_commands"]
    assert command["host_session_id"] == "s-1" and command["repo_root"] == "/r"
    (skip,) = manifest["skipped"]
    assert skip["ts_ns"] == 7 and skip["host"] == "claude"


def test_v1_event_reconsolidated_stays_v1_shaped(tmp_path: Path) -> None:
    events = _events_file(tmp_path / "events.tmp", [
        {"ts": "2026-10-01T10:00:00Z", "bash_cmd": "python fit.py", "inputs": [], "outputs": [],
         "filters_detected": [".query("]},
    ])
    (action,) = stop.extract(events, "sess", str(tmp_path))["actions"]
    assert "event_id" not in action and "host" not in action
    # Free regex text from a 0.9.7 recorder is not committed (allowlist-v1).
    assert "filters_detected" not in action


def test_analysis_events_are_deduped_by_event_id(tmp_path: Path) -> None:
    record = {"ts": "2026-11-22T10:00:00Z", "event_id": "e" * 32, "bash_cmd": "python fit.py",
              "inputs": [], "outputs": []}
    legacy = {"ts": "2026-11-22T10:00:00Z", "bash_cmd": "python fit.py", "inputs": [],
              "outputs": []}
    events = _events_file(tmp_path / "events.tmp", [record, record, legacy, legacy])
    manifest = stop.extract(events, "sess", str(tmp_path))
    # The repeated v2 record counts once; id-less v1 records are kept as before.
    assert manifest["n_actions"] == 3


def test_manifest_write_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "m.json"
    target.write_text('{"old": true}\n')

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        stop.write_manifest_atomic(target, {"new": True})
    # The old manifest is intact and no temp file is left behind.
    assert json.loads(target.read_text()) == {"old": True}
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_outputs_get_the_observed_protocol_hash(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "out").mkdir(parents=True)
    (repo / "out" / "res.csv").write_text("LATER\n")
    record = {
        "observation_id": "claude:t1",
        "outputs": [{"path": str(repo / "out" / "res.csv"), "sha256": "f" * 64,
                     "source": "inferred-static"}],
    }
    observation = {
        "observation_id": "claude:t1", "status": "no-known-overlap",
        "scope": {"unwatched": []},
        "changes": {"created": [{"path": "out/res.csv", "before": None,
                                 "after": {"sha256": None, "hash_status": "pending"}}],
                    "modified": [], "deleted": []},
    }
    stop._mark_observed_changes([record], {"claude:t1": observation}, str(repo))
    (out,) = record["outputs"]
    assert out["observed_change"] == "created"
    # Pending stays pending: the hook digest is never promoted to observed.
    assert out["observed_sha256"] is None
    assert out["observed_hash_status"] == "pending"
    assert out["sha256"] == "f" * 64


def test_journal_records_carry_one_id(tmp_path: Path) -> None:
    meta: dict = {}
    io.journal_event(tmp_path, "pre", "A", host="claude", session="s", tool="Bash", meta=meta)
    first = meta["journal_id"]
    io.journal_event(tmp_path, "post", "A", host="claude", session="s", tool="Bash", meta=meta)
    assert meta["journal_id"] == first
    records = [json.loads(line) for line in io.journal_path(tmp_path).read_text().splitlines()]
    assert {r["jid"] for r in records} == {first}


def test_a_fresh_journal_gets_a_new_id(tmp_path: Path) -> None:
    meta: dict = {}
    io.journal_event(tmp_path, "pre", "A", host="claude", session="s", tool="Bash", meta=meta)
    first = meta["journal_id"]
    io.journal_path(tmp_path).unlink()
    io.journal_event(tmp_path, "pre", "B", host="claude", session="s", tool="Bash", meta=meta)
    # Sequence numbers restart at 1, so they must never be compared with
    # the old journal's: a new id says so.
    assert meta["journal_id"] != first


def _observe(state: Path, repo: Path, call: str, **post_kwargs) -> dict:
    io.pre(state, host="claude", session="s", call_id=call, tool="Bash", repo_root=repo,
           snapshot=True)
    return io.post(state, host="claude", session="s", call_id=call, tool="Bash",
                   repo_root=post_kwargs.pop("post_root", repo), bash_cmd="true",
                   **post_kwargs)


def test_observation_records_journal_position_and_root(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state = repo / ".mycelium"
    state.mkdir()
    first = _observe(state, repo, "c1")
    second = _observe(state, repo, "c2")
    assert first["version"] == 2
    assert first["repo_root"] == os.path.realpath(repo)
    assert first["journal"]["id"] == second["journal"]["id"]
    assert first["journal"]["post_seq"] < second["journal"]["pre_seq"]


def test_post_against_another_checkout_claims_nothing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    (other / "x.txt").write_text("x")
    state = tmp_path / "state"
    state.mkdir()
    observation = _observe(state, repo, "c1", post_root=other)
    assert observation["status"] == "unavailable"
    assert observation["reason"] == "cross-root"
    assert observation["changes"] is None


def test_edit_targets_from_claude_and_codex_payloads() -> None:
    assert io.edit_targets("Write", {"file_path": "/r/a.py", "content": "x"}, "/r") == [
        ("/r/a.py", "written")]
    assert io.edit_targets("NotebookEdit", {"notebook_path": "/r/n.ipynb"}, "/r") == [
        ("/r/n.ipynb", "written")]
    patch = ("*** Begin Patch\n*** Add File: notes/new.md\n+hi\n"
             "*** Update File: src/a.py\n*** Move to: src/b.py\n@@\n-x\n+y\n"
             "*** Delete File: old.txt\n*** End Patch\n")
    # A move leaves a tombstone at the source and the content at the target.
    assert io.edit_targets("apply_patch", {"input": patch}, "/r") == [
        ("/r/notes/new.md", "written"), ("/r/src/a.py", "deleted"),
        ("/r/src/b.py", "written"), ("/r/old.txt", "deleted")]
    assert io.edit_targets("Edit", {}, "/r") is None
    assert io.edit_targets("apply_patch", {"input": "no patch"}, "/r") is None


def test_a_symlink_below_the_root_is_never_an_alias(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "x.csv").write_text("x")
    os.symlink(repo, repo / "self")
    rel, reason = io.repo_relative_unfollowed(str(repo / "self" / "x.csv"),
                                              os.path.realpath(repo))
    assert rel is None and reason == "symlinked-path"


def test_an_alias_above_the_root_still_frames(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "repo").mkdir(parents=True)
    (real / "repo" / "x.csv").write_text("x")
    os.symlink(real, tmp_path / "alias")
    rel, reason = io.repo_relative_unfollowed(str(tmp_path / "alias" / "repo" / "x.csv"),
                                              os.path.realpath(real / "repo"))
    assert rel == "x.csv" and reason is None


def test_atomic_manifest_keeps_a_private_mode(tmp_path: Path) -> None:
    target = tmp_path / "m.json"
    target.write_text("{}")
    os.chmod(target, 0o600)
    stop.write_manifest_atomic(target, {"new": True})
    assert (os.stat(target).st_mode & 0o777) == 0o600


def test_edit_observation_refuses_paths_outside_the_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.txt").write_text("a")
    written, deleted, refused = io._edit_changes(
        repo, [(str(repo / "a.txt"), "written"), (str(tmp_path / "x.txt"), "written"),
               (str(repo / "gone.txt"), "deleted")])
    assert written[0]["path"] == "a.txt" and written[0]["after"]["hash_status"] == "hashed"
    assert deleted == [{"path": "gone.txt", "after": None}]
    # On a case-insensitive volume the deletion also covers every spelling.
    assert [r for r in refused if not r.get("casefold")] == \
        [{"path": str(tmp_path / "x.txt"), "reason": "outside-scope"}]


def test_the_same_call_id_in_two_sessions_consolidates_separately(tmp_path: Path) -> None:
    obs_dir = tmp_path / "obs"
    for session in ("s1", "s2"):
        path = obs_dir / "claude" / f"{session}.ndjson"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kind": "observation", "observation_id": "claude:t1",
                                    "host_session_id": session, "status": "no-known-overlap",
                                    "changes": {"created": [], "modified": [], "deleted": []}})
                        + "\n")
    records = [{"observation_id": "claude:t1", "observation_session": "s1", "outputs": []},
               {"observation_id": "claude:t1", "observation_session": "s2", "outputs": []}]
    observations, missing, _ = stop.join_observations(records, obs_dir, str(tmp_path), [])
    assert len(observations) == 2 and missing == []


def test_an_output_whose_coverage_changed_is_not_unchanged(tmp_path: Path) -> None:
    record = {"observation_id": "claude:t1",
              "outputs": [{"path": str(tmp_path / "data" / "x.csv"), "rel_path": "data/x.csv"}]}
    observation = {"observation_id": "claude:t1", "status": "no-known-overlap",
                   "scope": {"unwatched": []},
                   "changes": {"created": [], "modified": [], "deleted": [],
                               "coverage_changed": ["data/x.csv"]}}
    stop._mark_observed_changes([record], {"claude:t1": observation}, str(tmp_path))
    assert record["outputs"][0]["observed_change"] == "unknown-coverage"


def test_a_fifo_script_path_does_not_hang_consolidation(tmp_path: Path) -> None:
    fifo = tmp_path / "fit.py"
    os.mkfifo(fifo)
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "python fit.py", "script": str(fifo),
           "script_sha256": None}
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(
        event=stop.normalize_event(raw, [], 0)), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "normalize_event blocked on a FIFO"
    assert result["event"]["script_sha256"] is None


SECRET = "TEST_SECRET_123"


def test_a_recomputed_script_digest_is_the_file_digest(tmp_path: Path) -> None:
    script = tmp_path / "fit.py"
    script.write_text(f'token = "{SECRET}"\n')
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "python fit.py", "script": str(script),
           "script_sha256": None}
    event = stop.normalize_event(raw, [], 0)
    assert "script_sha256_of" not in event
    assert event["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
