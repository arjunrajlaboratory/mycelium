"""The project-level lineage index answers provenance questions honestly.

Item 3b-3d of the provenance work. Writes come from observations, per call;
order comes only from the call journal; gaps and unproven order make the
verdict `unknown`, never `current`; the disk is checked separately.
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

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402

JID = "j" * 32


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "data").mkdir()
    (root / "out").mkdir()
    return root


def write_manifest(repo: Path, name: str, **body) -> Path:
    manifest = {"schema": stop.MANIFEST_SCHEMA, "session_id": name, "repo_root": str(repo),
                "actions": [], "data_commands": [], "skipped": [], "observations": [],
                "summary": {}}
    manifest.update(body)
    path = repo / ".living" / "log" / "data-lineage" / f"{name}.json"
    path.write_text(json.dumps(manifest))
    return path


def write_store(repo: Path, records: list[dict], session: str = "s1") -> None:
    path = io.observations_path(repo / ".mycelium", "claude", session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    synthesize_journal(repo, records)


def synthesize_journal(repo: Path, records: list[dict]) -> None:
    """The journal entries these observations' bounds imply, as the real
    hooks would have written them (the index proves coverage from the
    journal). Only for fixture journals (id JID): a test that drives the
    real journal keeps it as it is."""
    journal = io.journal_path(repo / ".mycelium")
    existing = io._journal_records(journal) if journal.exists() else []
    if any(r.get("jid") not in (None, JID) for r in existing):
        return
    known = {(r.get("call"), r.get("ev")) for r in existing}
    lines = []
    for record in records:
        bound = record.get("journal")
        if not isinstance(bound, dict) or bound.get("id") != JID:
            continue
        call = str(record.get("observation_id") or "").partition(":")[2]
        session = record.get("host_session_id") or "s1"
        tool = record.get("tool") or ("Edit" if record.get("status") == "edit" else "Bash")
        background = record.get("status") == "background"
        base = {"call": call, "host": "claude", "session": session, "tool": tool,
                "jid": JID, "background": background, "wall_ns": 0}
        if isinstance(bound.get("pre_seq"), int) and (call, "pre") not in known:
            lines.append({**base, "seq": bound["pre_seq"], "ev": "pre"})
        post = bound.get("post_seq")
        if isinstance(post, int) and (call, "post") not in known:
            event = "expire" if bound.get("expired") else ("launch" if background else "post")
            lines.append({**base, "seq": post, "ev": event, "observe": True})
    if lines:
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a") as handle:
            for line in lines:
                handle.write(json.dumps(line) + "\n")


def observation(repo: Path, call: str, *, created=(), modified=(), deleted=(), edited=(),
                pre: int, post: int | None, status="no-known-overlap", expired=False,
                jid=JID, root: Path | None = None, **extra) -> dict:
    def entries(items):
        return [{"path": p, "before": None,
                 "after": {"sha256": h, "hash_status": "hashed" if h else "pending"}}
                for p, h in items]

    record = {
        "kind": "observation", "version": 2, "observation_id": f"claude:{call}",
        "host": "claude", "host_session_id": "s1", "status": status,
        "observed_at": f"2026-11-22T10:00:{pre:02d}Z",
        "repo_root": os.path.realpath(root or repo),
        "journal": {"id": jid, "pre_seq": pre, "post_seq": post, "expired": expired},
        "scope": {"unwatched": []},
        "changes": {"created": entries(created), "modified": entries(modified),
                    "deleted": [{"path": p, "before": None, "after": None} for p in deleted],
                    "metadata_only": [], "rename_candidates": [], "counts": {},
                    "capped": False},
    }
    if edited:
        record["status"] = "edit"
        record["outcome"] = "success"
        record["changes"] = {"edited": entries(edited), "deleted": [], "refused": []}
    record.update(extra)
    return record


def action(repo: Path, event_id: str, call: str | None, *, inputs=(), outputs=(), **extra) -> dict:
    def rec(path, digest, origin):
        return {"path": str(repo / path), "rel_path": path, "sha256": digest,
                "hash_origin": origin, "source": "inferred-static",
                "pre_run_proven": origin == "pre-run-proven"}

    record = {"ts": "2026-11-22T10:00:00Z", "event_id": event_id, "host": "claude",
              "host_session_id": "s1", "repo_root": str(repo), "bash_cmd": "python x.py",
              "script": str(repo / "x.py"), "observation_id": f"claude:{call}" if call else None,
              "inputs": [rec(p, h, o) for p, h, o in inputs],
              "outputs": [rec(p, h, "hook-post") for p, h in outputs]}
    record.update(extra)
    return record


def load(repo: Path) -> li.Index:
    return li.load(repo)


# -- supersession ----------------------------------------------------------


def test_a_rerun_with_new_bytes_supersedes_the_earlier_write(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("second\n")
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("first\n"))], pre=1, post=2),
        observation(repo, "c2", modified=[("out/res.csv", sha("second\n"))], pre=3, post=4),
    ])
    write_manifest(repo, "m1", actions=[
        action(repo, "e1", "c1", outputs=[("out/res.csv", sha("first\n"))]),
        action(repo, "e2", "c2", outputs=[("out/res.csv", sha("second\n"))]),
    ])
    result = li.query_hash(load(repo), sha("first\n"))
    (entry,) = result["paths"]
    (write,) = entry["writes"]
    assert write["status"] == "superseded"
    by = write["record"]["superseded_by"]
    assert by["observation_id"] == "claude:c2"
    assert by["call"] == "s1|claude:c2"
    assert [e["event_id"] for e in by["events"]] == ["e2"]
    current = li.query_hash(load(repo), sha("second\n"))["paths"][0]["writes"][0]
    assert current["status"] == "current"
    assert current["record"]["status"] == "current-in-record"
    assert current["disk"] == "match"


def test_restored_content_supersedes_only_the_first_write(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("A")
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("A"))], pre=1, post=2),
        observation(repo, "c2", modified=[("out/res.csv", sha("B"))], pre=3, post=4),
        observation(repo, "c3", modified=[("out/res.csv", sha("A"))], pre=5, post=6),
    ])
    writes = li.query_hash(load(repo), sha("A"))["paths"][0]["writes"]
    assert [w["status"] for w in writes] == ["superseded", "current"]


def test_clock_order_alone_never_supersedes(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("second\n")
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("first\n"))], pre=1, post=2),
        observation(repo, "c2", modified=[("out/res.csv", sha("second\n"))], pre=1, post=2,
                    jid="other" * 6),
    ])
    write = li.query_hash(load(repo), sha("first\n"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert "order-unproven" in write["record"]["reasons"]


def test_an_expired_interval_orders_nothing(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("second\n")
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("first\n"))], pre=1, post=2,
                    expired=True),
        observation(repo, "c2", modified=[("out/res.csv", sha("second\n"))], pre=3, post=4),
    ])
    write = li.query_hash(load(repo), sha("first\n"))["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"


def test_overlapping_calls_are_unordered(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("second\n")
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("first\n"))], pre=1, post=4),
        observation(repo, "c2", modified=[("out/res.csv", sha("second\n"))], pre=2, post=5),
    ])
    write = li.query_hash(load(repo), sha("first\n"))["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"
    assert "order-unproven" in write["record"]["reasons"]


def test_a_later_write_with_unknown_hash_is_superseded_unverified(repo: Path) -> None:
    write_store(repo, [
        observation(repo, "c1", created=[("out/big.h5", sha("x"))], pre=1, post=2),
        observation(repo, "c2", modified=[("out/big.h5", None)], pre=3, post=4),
    ])
    write = li.query_hash(load(repo), sha("x"), disk=False)["paths"][0]["writes"][0]
    assert write["status"] == "superseded-unverified"


def test_a_later_deletion_supersedes(repo: Path) -> None:
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("x"))], pre=1, post=2),
        observation(repo, "c2", deleted=["out/res.csv"], pre=3, post=4),
    ])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "superseded"
    assert write["record"]["superseded_by"]["action"] == "deleted"


# -- gaps --------------------------------------------------------------------


def test_an_unavailable_observation_after_the_write_is_a_gap(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("x")
    gap = observation(repo, "c2", pre=3, post=4, status="unavailable", changes=None,
                      reason="post-walk-timeout")
    write_store(repo, [observation(repo, "c1", created=[("out/res.csv", sha("x"))], pre=1,
                                   post=2), gap])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert "gap" in write["record"]["reasons"]
    assert write["disk"] == "match"  # reported separately, never promoted


def test_a_gap_provably_before_the_write_does_not_count(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("x")
    gap = observation(repo, "c0", pre=1, post=2, status="unavailable", changes=None)
    write_store(repo, [gap, observation(repo, "c1", created=[("out/res.csv", sha("x"))],
                                        pre=3, post=4)])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "current"


def test_an_unwatched_scope_is_a_gap_only_for_its_paths(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("x")
    (repo / "data" / "a.csv").write_text("a")
    later = observation(repo, "c2", pre=3, post=4, scope={"unwatched": [{"path": "data"}]})
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("x")), ("data/a.csv", sha("a"))],
                    pre=1, post=2),
        later,
    ])
    index = load(repo)
    assert li.query_hash(index, sha("x"))["paths"][0]["writes"][0]["status"] == "current"
    data_write = li.query_hash(index, sha("a"))["paths"][0]["writes"][0]
    assert data_write["status"] == "unknown"


# -- readers ------------------------------------------------------------------


def test_two_steps_reading_one_baseline_are_found_by_one_query(repo: Path) -> None:
    (repo / "data" / "base.csv").write_text("baseline")
    base = sha("baseline")
    write_manifest(repo, "m1", actions=[
        action(repo, "e1", "c1", inputs=[("data/base.csv", base, "pre-run-proven")]),
        action(repo, "e2", "c2", inputs=[("data/base.csv", base, "pre-run-proven")]),
        action(repo, "e3", "c3", inputs=[("data/base.csv", base, "hook-post")]),
    ])
    result = li.query_hash(load(repo), base)
    assert result["readers"] == 3
    (entry,) = result["paths"]
    origins = [r["hash_origin"] for r in entry["readers"]]
    assert origins == ["pre-run-proven", "pre-run-proven", "hook-post"]
    assert entry["record"]["status"] == "no-recorded-write"
    assert entry["disk"] == "match"


def test_readers_by_path_include_unhashed_reads(repo: Path) -> None:
    write_manifest(repo, "m1", actions=[
        action(repo, "e1", "c1", inputs=[("data/big.h5", None, "hook-post")]),
    ])
    result = li.query_path(load(repo), "data/big.h5", disk=False)
    assert [r["event_id"] for r in result["readers"]] == ["e1"]


# -- sources -----------------------------------------------------------------


def test_store_only_and_edit_observations_contribute_writes(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("y")
    (repo / "x.py").write_text("print(1)")
    write_store(repo, [
        observation(repo, "c1", created=[("out/res.csv", sha("y"))], pre=1, post=2),
        observation(repo, "e9", edited=[("x.py", sha("print(1)"))], pre=3, post=4, tool="Write"),
    ])
    index = load(repo)
    store_write = li.query_hash(index, sha("y"))["paths"][0]["writes"][0]
    assert store_write["events"] == [] and store_write["status"] == "current"
    edit_write = li.query_path(index, "x.py")["writes"][0]
    assert edit_write["attribution"] == "sole"
    assert edit_write["status"] == "current"


def test_observations_of_another_checkout_are_ignored(repo: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    write_store(repo, [observation(repo, "c1", created=[("out/res.csv", sha("z"))], pre=1,
                                   post=2, root=other)])
    assert li.query_hash(load(repo), sha("z"))["found"] is False


def test_duplicated_records_across_manifests_count_once(repo: Path) -> None:
    base = sha("b")
    record = action(repo, "e1", None, inputs=[("data/b.csv", base, "pre-run-proven")])
    write_manifest(repo, "data-commands-x", actions=[record])
    write_manifest(repo, "m1", actions=[record])
    assert li.query_hash(load(repo), base, disk=False)["readers"] == 1


def test_v1_manifests_load_with_honest_labels(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("v1")
    manifest = {"session_id": "old", "repo_root": str(repo), "actions": [{
        "ts": "2026-10-01T10:00:00Z", "bash_cmd": "python x.py",
        "inputs": [{"path": str(repo / "data" / "in.csv"), "sha256": sha("in")}],
        "outputs": [{"path": str(repo / "out" / "res.csv"), "sha256": sha("v1")}],
    }]}
    (repo / ".living" / "log" / "data-lineage" / "old.json").write_text(json.dumps(manifest))
    index = load(repo)
    read = li.query_hash(index, sha("in"), disk=False)["paths"][0]["readers"][0]
    assert read["hash_origin"] == "unknown"
    assert read["event_id"].startswith("v1:old.json#")
    write = li.query_hash(index, sha("v1"))["paths"][0]["writes"][0]
    assert write["evidence"] == "inferred"
    assert write["status"] == "unknown"  # an inferred write is never certified


def test_a_moved_v1_repository_still_frames_its_paths(repo: Path) -> None:
    manifest = {"session_id": "old", "repo_root": "/somewhere/that/moved", "actions": [{
        "ts": "2026-10-01T10:00:00Z",
        "inputs": [{"path": "/somewhere/that/moved/data/in.csv", "sha256": sha("in")}],
        "outputs": [],
    }]}
    (repo / ".living" / "log" / "data-lineage" / "old.json").write_text(json.dumps(manifest))
    read = li.query_hash(load(repo), sha("in"), disk=False)["paths"][0]
    assert read["path"] == "data/in.csv"


def test_an_unreadable_manifest_is_a_gap_not_a_crash(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("x")
    (repo / ".living" / "log" / "data-lineage" / "broken.json").write_text("{not json")
    write_store(repo, [observation(repo, "c1", created=[("out/res.csv", sha("x"))], pre=1,
                                   post=2)])
    result = li.query_hash(load(repo), sha("x"))
    assert result["coverage"]["gaps"]["manifest-unreadable"] == 1
    assert result["paths"][0]["writes"][0]["status"] == "unknown"


# -- disk ----------------------------------------------------------------------


def test_disk_differs_without_a_later_record(repo: Path) -> None:
    (repo / "out" / "res.csv").write_text("edited by hand")
    write_store(repo, [observation(repo, "c1", created=[("out/res.csv", sha("x"))], pre=1,
                                   post=2)])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "changed-outside-record"
    assert write["record"]["status"] == "current-in-record"


def test_disk_missing(repo: Path) -> None:
    write_store(repo, [observation(repo, "c1", created=[("out/gone.csv", sha("x"))], pre=1,
                                   post=2)])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "missing-on-disk"


def test_record_only_skips_the_disk(repo: Path) -> None:
    write_store(repo, [observation(repo, "c1", created=[("out/res.csv", sha("x"))], pre=1,
                                   post=2)])
    write = li.query_hash(load(repo), sha("x"), disk=False)["paths"][0]["writes"][0]
    assert write["status"] == "current-in-record"
    assert "disk" not in write


def test_a_pending_hash_resolves_only_on_identity_match(repo: Path) -> None:
    target = repo / "out" / "big.bin"
    target.write_bytes(b"0" * 1000)
    st = os.stat(target)
    record = observation(repo, "c1", created=[("out/big.bin", None)], pre=1, post=2)
    record["changes"]["created"][0]["after"]["identity"] = {
        "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
        "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
    write_store(repo, [record])
    write = li.query_path(load(repo), "out/big.bin")["writes"][0]
    assert write["disk"] == "match"
    assert write["resolved_sha256"] == hashlib.sha256(b"0" * 1000).hexdigest()
    assert write["record"]["status"] == "unknown"  # still says the hash came later


def test_a_symlinked_path_is_never_read(repo: Path, tmp_path: Path) -> None:
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "x").write_text("x")
    os.symlink(tmp_path / "secret", repo / "link")
    write_store(repo, [observation(repo, "c1", created=[("link/x", sha("x"))], pre=1, post=2)])
    write = li.query_path(load(repo), "link/x")["writes"][0]
    assert write["disk"] == "unverified"
    assert write["reason"] == "symlinked-path"


# -- cache ---------------------------------------------------------------------


def test_the_cache_is_incremental_and_keyed_by_checkout(repo: Path, monkeypatch) -> None:
    write_manifest(repo, "m1", actions=[action(repo, "e1", None,
                                               inputs=[("data/a", sha("a"), "hook-post")])])
    li.load(repo)
    path = li.cache_file(repo, repo / ".mycelium")
    assert path.exists() and path.parent.name == "lineage"
    calls = []
    real = li.contribution

    def counting(root, state, name):
        calls.append(name)
        return real(root, state, name)

    monkeypatch.setattr(li, "contribution", counting)
    li.load(repo)
    assert calls == []  # nothing changed, nothing re-parsed
    write_manifest(repo, "m2", actions=[action(repo, "e2", None,
                                               inputs=[("data/a", sha("a"), "hook-post")])])
    index = li.load(repo)
    assert calls == ["manifest:m2.json"]
    assert li.query_hash(index, sha("a"), disk=False)["readers"] == 2


def test_a_removed_manifest_leaves_the_index(repo: Path) -> None:
    path = write_manifest(repo, "m1", actions=[action(repo, "e1", None,
                                                      inputs=[("data/a", sha("a"), "hook-post")])])
    li.load(repo)
    path.unlink()
    assert li.query_hash(li.load(repo), sha("a"), disk=False)["found"] is False


def test_no_write_mode_never_creates_the_cache(repo: Path) -> None:
    write_manifest(repo, "m1")
    li.load(repo, write=False)
    assert not li.cache_file(repo, repo / ".mycelium").exists()


# -- CLI -----------------------------------------------------------------------


def test_cli_exit_codes_and_json(repo: Path) -> None:
    (repo / "data" / "base.csv").write_text("baseline")
    write_manifest(repo, "m1", actions=[action(repo, "e1", "c1", inputs=[
        ("data/base.csv", sha("baseline"), "pre-run-proven")])])
    script = HERE / "lineage_index.py"
    found = subprocess.run([sys.executable, str(script), "--root", str(repo), "query", "--hash",
                            sha("baseline"), "--json"], capture_output=True, text=True)
    assert found.returncode == 0
    assert json.loads(found.stdout)["readers"] == 1
    missing = subprocess.run([sys.executable, str(script), "--root", str(repo), "query",
                              "--hash", sha("nope")], capture_output=True, text=True)
    assert missing.returncode == 1
    bad = subprocess.run([sys.executable, str(script), "--root", str(repo), "query", "--hash",
                          "XYZ"], capture_output=True, text=True)
    assert bad.returncode == 2


# -- end to end ------------------------------------------------------------------


def _run_call(repo: Path, call: str, command: str, writer) -> None:
    """One observed Bash call: Pre snapshot, the command's effect, Post, the
    extractor's event appended to the session stream."""
    state = repo / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id=call, tool="Bash", repo_root=repo,
           snapshot=True)
    writer()
    observation_rec = io.post(state, host="claude", session="s1", call_id=call, tool="Bash",
                              repo_root=repo, bash_cmd=command, outcome="success",
                              bash_exit=0, hash_seconds=5)
    summary = json.dumps({"status": observation_rec["status"],
                          "observation_id": observation_rec["observation_id"],
                          "host_session_id": "s1", "changed": True,
                          "pre_snapshot": str(io.done_snapshot_path(
                              io.snapshot_path(state, "claude", "s1", call))),
                          "state_dir": str(state)})
    subprocess.run(
        [sys.executable, str(HERE / "extract_data_lineage_event.py"), "--cwd", str(repo),
         "--ts", "2026-11-22T10:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--host", "claude", "--host-session-id", "s1", "--repo-root", str(repo),
         "--observation-json", summary, "--append-to", str(repo / "events.tmp")],
        check=True, capture_output=True,
    )


def test_end_to_end_rerun_marks_the_old_output_superseded(repo: Path) -> None:
    import time

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "data" / "in.csv").write_text("a\n1\n")
    (repo / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('data/in.csv').to_csv('out/res.csv')\n")
    time.sleep(0.05)  # past the racy-timestamp window, so the input is provable
    _run_call(repo, "t1", "python fit.py",
              lambda: (repo / "out" / "res.csv").write_text("run one\n"))
    _run_call(repo, "t2", "python fit.py",
              lambda: (repo / "out" / "res.csv").write_text("run two, different\n"))
    manifest = stop.extract(repo / "events.tmp", "sess", str(repo),
                            repo / ".mycelium" / "io" / "obs")
    stop.write_manifest_atomic(repo / ".living" / "log" / "data-lineage" / "sess.json", manifest)
    index = li.load(repo)
    old = li.query_hash(index, sha("run one\n"))
    (write,) = old["paths"][0]["writes"]
    assert write["status"] == "superseded"
    assert write["record"]["superseded_by"]["observation_id"] == "claude:t2"
    new = li.query_hash(index, sha("run two, different\n"))["paths"][0]["writes"][0]
    assert new["status"] == "current"
    baseline = li.query_hash(index, sha("a\n1\n"))
    assert baseline["readers"] == 2
    origins = [r["hash_origin"] for r in baseline["paths"][0]["readers"]]
    assert origins == ["pre-run-proven", "pre-run-proven"]
    # The same answer from a cold rebuild (no cache).
    li.cache_file(repo, repo / ".mycelium").unlink()
    rebuilt = li.query_hash(li.load(repo), sha("a\n1\n"))
    assert [r["hash_origin"] for r in rebuilt["paths"][0]["readers"]] == origins


# -- review regressions ------------------------------------------------------


def test_an_unknown_earlier_hash_is_never_proven_different(repo: Path) -> None:
    write_store(repo, [
        observation(repo, "c1", created=[("out/x", None)], pre=1, post=2),
        observation(repo, "c2", modified=[("out/x", sha("H"))], pre=3, post=4),
    ])
    index = load(repo)
    first = [w for w in index.writes if w["call"].endswith("c1")][0]
    status = index.record_status(first)
    assert status["status"] == "superseded-unverified"
    assert status["reasons"] == ["earlier-hash-unknown"]


def test_an_unordered_same_hash_rewrite_leaves_order_unproven(repo: Path) -> None:
    (repo / "out" / "x").write_text("same")
    write_store(repo, [
        observation(repo, "c1", created=[("out/x", sha("same"))], pre=1, post=4),
        observation(repo, "c2", modified=[("out/x", sha("same"))], pre=2, post=5),
    ])
    writes = li.query_hash(load(repo), sha("same"))["paths"][0]["writes"]
    assert all(w["record"]["status"] == "unknown" for w in writes)
    assert all("order-unproven" in w["record"]["reasons"] for w in writes)


def test_unresolved_calls_are_gaps_until_a_store_resolves_them(repo: Path) -> None:
    (repo / "out" / "x").write_text("x")
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2)])
    write_manifest(repo, "m1", summary={"unresolved_calls": ["claude:c9"],
                                        "observations_missing": ["claude:c1"]})
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    kinds = {g["kind"] for g in write["record"]["gaps"]}
    # c9 is still unresolved; c1's "missing" marker is satisfied by the store.
    assert kinds == {"call-unresolved"}


def test_an_event_whose_observer_failed_is_a_gap(repo: Path) -> None:
    (repo / "out" / "x").write_text("x")
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2)])
    write_manifest(repo, "m1", actions=[action(repo, "e9", None,
                                               observation_status="unavailable")])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert "gap" in write["record"]["reasons"]


def test_the_same_call_id_in_two_sessions_is_two_calls(repo: Path) -> None:
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("one"))], pre=1, post=2)],
                session="s1")
    other = observation(repo, "c1", created=[("out/y", sha("two"))], pre=3, post=4)
    other["host_session_id"] = "s2"
    write_store(repo, [other], session="s2")
    index = load(repo)
    assert len(index.observations) == 2
    assert {w["path"] for w in index.writes} == {"out/x", "out/y"}


def test_a_capped_diff_clouds_only_its_top_level_directories(repo: Path) -> None:
    (repo / "out" / "x").write_text("x")
    capped = observation(repo, "c2", pre=3, post=4)
    capped["changes"]["capped"] = True
    capped["changes"]["top_level"] = {"scratch": 5000}
    capped["changes"]["top_level_complete"] = True
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2),
                       capped])
    index = load(repo)
    assert li.query_hash(index, sha("x"))["paths"][0]["writes"][0]["status"] == "current"
    later = observation(repo, "c3", pre=5, post=6)
    later["changes"]["capped"] = True
    write_store(repo, [later])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"


def test_a_truncated_racy_list_clouds_every_path(repo: Path) -> None:
    (repo / "out" / "x").write_text("x")
    racy = observation(repo, "c2", pre=3, post=4)
    racy["changes"]["racy_unknown"] = ["a"]
    racy["changes"]["counts"] = {"racy_unknown": 40}
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2),
                       racy])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"


def test_a_path_bash_never_watches_is_never_certified(repo: Path) -> None:
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "x.js").write_text("x")
    write_store(repo, [observation(repo, "e1", edited=[("node_modules/x.js", sha("x"))],
                                   pre=1, post=2, tool="Write")])
    write = li.query_path(load(repo), "node_modules/x.js")["writes"][0]
    assert "unwatched-path" in write["record"]["reasons"]


def test_hash_queries_find_a_resolvable_pending_producer(repo: Path) -> None:
    target = repo / "out" / "big.bin"
    target.write_bytes(b"1" * 2048)
    st = os.stat(target)
    record = observation(repo, "c1", created=[("out/big.bin", None)], pre=1, post=2)
    record["changes"]["created"][0]["after"]["identity"] = {
        "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size,
        "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}
    write_store(repo, [record])
    result = li.query_hash(load(repo), hashlib.sha256(b"1" * 2048).hexdigest())
    assert result["producers"] == 1
    assert result["paths"][0]["writes"][0]["disk_hash_origin"] == "query-identity-match"


def test_malformed_sources_become_gaps(repo: Path) -> None:
    (repo / ".living" / "log" / "data-lineage" / "odd.json").write_text(
        json.dumps({"schema": stop.MANIFEST_SCHEMA, "actions": 1, "observations": "x"}))
    path = io.observations_path(repo / ".mycelium", "claude", "s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"kind": "observation", "observation_id": "claude:c1"\n')
    coverage = li.query_path(load(repo), "out/x", disk=False)["coverage"]["gaps"]
    assert coverage["manifest-invalid"] == 1
    assert coverage["store-record-unreadable"] == 1


def test_query_cli_refuses_climbing_paths(repo: Path) -> None:
    script = HERE / "lineage_index.py"
    bad = subprocess.run([sys.executable, str(script), "--root", str(repo), "query", "--path",
                          "link/../x.csv"], capture_output=True, text=True)
    assert bad.returncode == 2


def test_write_summaries_carry_their_evidence(repo: Path) -> None:
    record = observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2,
                         status="overlap", overlapping=[{"call": "c7"}], outcome="failure")
    write_store(repo, [record])
    (write,) = li.query_path(load(repo), "out/x", disk=False)["writes"]
    assert write["attribution"] == "shared"
    assert write["overlapping"] == ["c7"]
    assert write["tool_outcome"] == "failure"
    assert write["source"].startswith("store:")
    assert write["order_basis"] == "journal"


def test_an_incomplete_top_level_summary_clouds_every_path(repo: Path) -> None:
    (repo / "out" / "x").write_text("x")
    capped = observation(repo, "c2", pre=3, post=4)
    capped["changes"]["capped"] = True
    capped["changes"]["top_level"] = {"scratch": 5000}
    capped["changes"]["top_level_complete"] = False
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2),
                       capped])
    write = li.query_hash(load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"


def test_a_foreign_checkouts_records_add_readers_but_no_writes(repo: Path) -> None:
    (repo / "out" / "x").write_text("x")
    write_store(repo, [observation(repo, "c1", created=[("out/x", sha("x"))], pre=1, post=2)])
    foreign = action(repo, "f1", None, inputs=[("out/x", sha("x"), "pre-run-proven")],
                     outputs=[("out/x", sha("other"))])
    foreign["repo_root"] = "/some/other/clone"
    write_manifest(repo, "theirs", actions=[foreign])
    index = load(repo)
    result = li.query_hash(index, sha("x"))
    (entry,) = result["paths"]
    assert entry["writes"][0]["status"] == "current"
    assert entry["readers"][0]["checkout"] == "foreign"
    assert li.query_hash(index, sha("other"), disk=False)["found"] is False


def test_an_unlabelled_legacy_digest_stays_unknown_in_a_v2_manifest(repo: Path) -> None:
    legacy = {"ts": "2026-10-01T00:00:00Z", "bash_cmd": "python x.py",
              "inputs": [{"path": str(repo / "data" / "in.csv"), "sha256": sha("in")}],
              "outputs": []}
    write_manifest(repo, "m1", actions=[legacy])
    (read,) = li.query_hash(load(repo), sha("in"), disk=False)["paths"][0]["readers"]
    assert read["hash_origin"] == "unknown"
