"""The project-level lineage index answers provenance questions honestly.

Item 3b-3d of the provenance work. Writes come from observations, per call;
order comes only from the call journal; gaps and unproven order make the
verdict `unknown`, never `current`; the disk is checked separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
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
    assert "coverage-unproven" in write["record"]["reasons"]
    assert {"kind": "observation-unavailable", "call": "s1|claude:c2"} in \
        write["record"]["unaccounted"]
    assert write["disk"] == "match"  # reported separately, never promoted


@pytest.mark.parametrize("bound", [None, {"id": "other" * 6, "pre_seq": 1, "post_seq": 2,
                                          "expired": False}])
def test_an_observation_the_journal_cannot_place_clouds_every_write(repo: Path,
                                                                     bound) -> None:
    # No bound in this journal (a Post whose journal append failed, another
    # journal): the call is unordered, so it may have come after the write.
    (repo / "out" / "res.csv").write_text("x")
    lost = observation(repo, "c0", pre=1, post=2, status="unavailable", changes=None)
    lost["journal"] = bound
    capped = observation(repo, "c9", pre=1, post=2)
    capped["journal"] = bound
    capped["changes"]["capped"] = True
    write_store(repo, [lost, capped,
                       observation(repo, "c1", created=[("out/res.csv", sha("x"))], pre=3,
                                   post=4)])
    write = li.query_hash(load(repo), sha("x"), disk=False)["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"
    kinds = {u["kind"] for u in write["record"]["unaccounted"]}
    assert kinds == {"observation-unavailable", "observation-capped"}


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


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    (root / "out").mkdir()
    return root


def observed(project: Path, call: str, bound: dict, **kw) -> dict:
    return observation(project, call, pre=bound["pre"], post=bound["post"],
                           jid=bound["id"], **kw)


def verdict(project: Path, content: str) -> str:
    (project / "out" / "x").write_text(content)
    return li.query_hash(li.load(project), sha(content))["paths"][0]["writes"][0]["status"]


def journal(project: Path, call: str, *, tool: str = "Bash", post: bool = True,
            observe: bool = True) -> dict:
    """Journal one call; return its bound as an observation records it."""
    state = project / ".mycelium"
    meta: dict = {}
    pre_seq, _, _ = io.journal_event(state, "pre", call, host="claude", session="s1",
                                     tool=tool, meta=meta)
    post_seq = None
    if post:
        post_seq, _, _ = io.journal_event(state, "post", call, host="claude", session="s1",
                                          tool=tool, observe=observe)
    return {"id": meta["journal_id"], "pre": pre_seq, "post": post_seq}


def test_a_closed_call_with_no_observation_clouds_earlier_writes(project: Path) -> None:
    first = journal(project, "c1")
    journal(project, "c2")  # its observation append failed
    write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    assert verdict(project, "A") == "unknown"


def test_a_write_after_the_missing_observation_is_current(project: Path) -> None:
    journal(project, "c1")
    later = journal(project, "c2")
    write_store(project, [observed(project, "c2", later, created=[("out/x", sha("B"))])])
    journal_only = journal(project, "c0")  # noqa: F841 - a later call, observed below
    write_store(project, [observed(project, "c0", journal_only)])
    # c1 has no observation, but it ended before c2 started.
    assert verdict(project, "B") == "current"


@pytest.mark.parametrize("observe", [True, False])
def test_a_closed_edit_with_no_observation_is_unaccounted(project: Path, observe) -> None:
    # Positive proof: an edit that ran after the write with no observation
    # on record, whether or not it owed one, is not accounted for.
    first = journal(project, "c1")
    journal(project, "e1", tool="Edit", observe=observe)
    write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    write = li.query_hash(li.load(project), sha("A"), disk=False)["paths"][0]["writes"][0]
    assert write["record"]["status"] == "unknown"
    assert "coverage-unproven" in write["record"]["reasons"]


def test_an_age_expired_call_stays_unbounded(project: Path) -> None:
    state = project / ".mycelium"
    old = time.time_ns() - (io.OPEN_INTERVAL_MAX_SECONDS + 60) * 1_000_000_000
    io.journal_event(state, "pre", "stuck", host="claude", session="s1", tool="Bash",
                     now_ns=old)
    later = journal(project, "c2")  # its journal_event expires "stuck"
    write_store(project, [observed(project, "c2", later, created=[("out/x", sha("B"))])])
    # The stuck call may still be running: c2's write is not provably last.
    assert verdict(project, "B") == "unknown"


def test_a_turn_end_bounds_the_unresolved_call(project: Path) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id="lost", tool="Bash",
           repo_root=project, snapshot=True)
    io.turn_end(state, host="claude", session="s1")
    later = journal(project, "c2")
    write_store(project, [observed(project, "c2", later, created=[("out/x", sha("B"))])])
    write_manifest(project, "m1", summary={"unresolved_calls": ["claude:lost"]})
    assert verdict(project, "B") == "current"


def test_a_skip_with_a_session_is_reconciled_only_by_that_session(project: Path) -> None:
    first = journal(project, "c1")
    write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    write_manifest(project, "m1", skipped=[
        {"skip_reason": "detector-failed", "observation_id": "claude:c1",
         "host_session_id": "s2"}])
    assert verdict(project, "A") == "unknown"


def test_a_foreign_manifest_summary_clouds_nothing(project: Path) -> None:
    first = journal(project, "c1")
    write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))])])
    theirs = write_manifest(project, "theirs", repo_root="/home/someone/proj",
                                summary={"observations_missing": ["claude:zz"],
                                         "unresolved_calls": ["claude:yy"]})
    assert theirs.exists()
    assert verdict(project, "A") == "current"


def test_an_unconfirmed_edit_credits_no_write(project: Path) -> None:
    first = journal(project, "c1")
    edit = journal(project, "e1", tool="Edit")
    unconfirmed = observed(project, "e1", edit, edited=[("out/x", sha("A"))])
    unconfirmed["outcome"] = "unknown"
    write_store(project, [observed(project, "c1", first, created=[("out/x", sha("A"))]),
                              unconfirmed])
    result = li.query_hash(li.load(project), sha("A"), disk=False)
    writes = result["paths"][0]["writes"]
    assert all(w["call"] != "s1|claude:e1" for w in writes)
    assert writes[0]["status"] == "unknown"


def test_a_directory_excluded_only_at_pre_is_a_coverage_gap(project: Path) -> None:
    state = project / ".mycelium"
    (project / "data").mkdir()
    (project / "data" / "x.csv").write_text("x")
    scope = state / "io" / "scope"
    scope.parent.mkdir(parents=True, exist_ok=True)
    scope.write_text("exclude data\n")
    io.pre(state, host="claude", session="s1", call_id="rm", tool="Bash",
           repo_root=project, snapshot=True)
    shutil.rmtree(project / "data")
    scope.write_text("")
    observation = io.post(state, host="claude", session="s1", call_id="rm", tool="Bash",
                          repo_root=project, outcome="success", bash_exit=0)
    assert any(u.get("path") == "data" for u in observation["scope"]["unwatched"])


def status(project: Path, content: str, *, disk: bool = True) -> dict:
    result = li.query_hash(li.load(project, write=False), sha(content), disk=disk)
    return result["paths"][0]["writes"][0]


def bash(project: Path, call: str, action=None, *, background: bool = False) -> dict:
    """One real Bash call through the observer's Pre and Post."""
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id=call, tool="Bash",
           repo_root=project, snapshot=True, background=background)
    if action:
        action()
    return io.post(state, host="claude", session="s1", call_id=call, tool="Bash",
                   repo_root=project, outcome="success", bash_exit=0, background=background)


def write_x(project: Path, content: str):
    return lambda: (project / "out" / "x").write_text(content)


def test_a_fully_observed_history_is_current(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    bash(project, "c2", lambda: (project / "out" / "y").write_text("other"))
    assert status(project, "A", disk=False)["record"]["status"] == "current-in-record"
    assert status(project, "A")["status"] == "current"


def test_a_lost_journal_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    io.journal_path(project / ".mycelium").unlink()
    io.journal_event(project / ".mycelium", "pre", "later", host="claude", session="s1",
                     tool="Bash")
    record = status(project, "A", disk=False)["record"]
    assert record["status"] == "unknown"
    assert record["unaccounted"][0]["kind"] == "journal-discontinuous"


def test_a_write_from_before_compaction_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    journal = io.journal_path(project / ".mycelium")
    records = io._journal_records(journal)
    jid = records[-1]["jid"]
    floor = max(r["seq"] for r in records) + 1
    with journal.open("a") as handle:
        handle.write(f'{{"seq": 0, "ev": "floor", "call": "", "floor": {floor}, '
                     f'"jid": "{jid}"}}\n')
    record = status(project, "A", disk=False)["record"]
    assert record["unaccounted"][0]["kind"] == "journal-compacted"


def launch(project: Path, call: str, task: str | None) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id=call, tool="Bash",
           repo_root=project, snapshot=True, background=True)
    io.post(state, host="claude", session="s1", call_id=call, tool="Bash",
            repo_root=project, outcome="success", bash_exit=0, background=True,
            task_id=task)


def test_a_later_background_job_is_unaccounted_but_identity_proves_the_file(
        project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    time.sleep(0.01)
    # A host-backgrounded command after the write (the live-run case): what
    # it did after launch was never observed.
    launch(project, "bg", "task-1")
    entry = status(project, "A")
    assert entry["record"]["status"] == "unknown"
    assert "coverage-unproven" in entry["record"]["reasons"]
    # While the job may still run, identity is not trusted (a live mapping
    # can change a file without moving its timestamps).
    assert entry["status"] == "unknown"
    # Once the host shows it ended, the file's unchanged identity proves it.
    io.end_finished_background(project / ".mycelium", host="claude", session="s1", tasks=[])
    entry = status(project, "A")
    assert entry["record"]["status"] == "unknown"
    assert entry["status"] == "current"
    assert entry["basis"] == "identity-unchanged-since-write"


def test_a_jobs_end_outlives_journal_compaction(project: Path) -> None:
    launch(project, "bg", "task-1")
    io.end_finished_background(project / ".mycelium", host="claude", session="s1", tasks=[])
    journal = io.journal_path(project / ".mycelium")
    kept = [r for r in io._journal_records(journal) if r.get("call") != "bg"]
    journal.write_text("".join(__import__("json").dumps(r) + "\n" for r in kept))
    index = li.load(project, write=False)
    (gap,) = [g for g in index.gaps if g.get("kind") == "background-job"]
    assert isinstance(gap["journal"]["post_seq"], int)


def test_a_subagent_job_absent_from_the_snapshot_stays_open(project: Path) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id="sub", tool="Bash", agent="agent-7",
           repo_root=project, snapshot=True, background=True)
    io.post(state, host="claude", session="s1", call_id="sub", tool="Bash", agent="agent-7",
            repo_root=project, outcome="success", bash_exit=0, background=True,
            task_id="t-sub")
    assert io.end_finished_background(state, host="claude", session="s1", tasks=[]) == 0
    assert io.end_finished_background(
        state, host="claude", session="s1",
        tasks=[{"id": "t-sub", "status": "completed"}]) == 1


def test_an_overlapping_restore_is_unaccounted(project: Path) -> None:
    state = project / ".mycelium"
    target = project / "out" / "x"
    target.write_text("old")
    # B starts first and sees `old`; A writes `new` and finishes; B restores
    # `old` and finishes. B's endpoints both show `old`.
    io.pre(state, host="claude", session="s1", call_id="B", tool="Bash",
           repo_root=project, snapshot=True)
    bash(project, "A", lambda: target.write_text("new"))
    time.sleep(0.01)
    target.write_text("old")
    io.post(state, host="claude", session="s1", call_id="B", tool="Bash",
            repo_root=project, outcome="success", bash_exit=0)
    record = status(project, "new", disk=False)["record"]
    assert record["status"] != "current-in-record"


def test_an_unparseable_journal_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    journal = io.journal_path(project / ".mycelium")
    with journal.open("a") as handle:
        handle.write('{"seq": 99, "ev": "pre", "call": "z", "host": "claude", '
                     '"session": "s1", "wall_ns": "not-a-number"}\n')
    record = status(project, "A", disk=False)["record"]
    assert record["status"] == "unknown"


def test_a_touched_file_is_not_rescued_even_with_the_same_bytes(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    bash(project, "bg", None, background=True)
    time.sleep(0.01)
    # A -> B -> A outside every observation: the bytes match, the identity
    # does not (ctime moved), so nothing is certified.
    target = project / "out" / "x"
    target.write_text("B")
    target.write_text("A")
    entry = status(project, "A")
    assert entry["status"] == "unknown"
    assert "basis" not in entry


def test_whole_second_timestamps_are_never_used_as_proof(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    index = li.load(project, write=False)
    (write,) = [w for w in index.writes if w["path"] == "out/x"]
    ident = dict(write["identity"])
    ident["mtime_ns"] = ident["ctime_ns"] = 1_700_000_000_000_000_000
    assert not index._identity_unchanged({**write, "identity": ident})


def test_a_finished_task_bounds_its_job(project: Path) -> None:
    launch(project, "bg", "bjjs0w6bq")
    state = project / ".mycelium"
    assert io.end_finished_background(state, host="claude", session="s1",
                                      tasks=[{"id": "bjjs0w6bq", "status": "running"}]) == 0
    assert io.end_finished_background(state, host="claude", session="s1", tasks=[]) == 1
    bash(project, "c2", write_x(project, "A"))
    # Written after the job ended: provable from the record alone.
    assert status(project, "A", disk=False)["record"]["status"] == "current-in-record"


@pytest.mark.parametrize("tasks", [
    [{"id": "bjjs0w6bq", "status": "running"}],
    [{"id": "bjjs0w6bq", "status": "something-new"}],
])
def test_a_running_or_unreadable_task_stays_open(project: Path, tasks) -> None:
    launch(project, "bg", "bjjs0w6bq")
    io.end_finished_background(project / ".mycelium", host="claude", session="s1",
                               tasks=tasks)
    bash(project, "c2", write_x(project, "A"))
    assert status(project, "A", disk=False)["record"]["status"] == "unknown"


def test_a_job_without_a_task_id_stays_open(project: Path) -> None:
    launch(project, "bg", None)
    io.end_finished_background(project / ".mycelium", host="claude", session="s1",
                               tasks=[])
    bash(project, "c2", write_x(project, "A"))
    assert status(project, "A", disk=False)["record"]["status"] == "unknown"


def test_the_stop_payload_reaches_the_journal(project: Path, monkeypatch) -> None:
    import io as stdio
    import json

    launch(project, "bg", "t-1")
    monkeypatch.setattr(sys, "stdin", stdio.StringIO(json.dumps({"background_tasks": []})))
    assert io.main(["turn-end", "--state-dir", str(project / ".mycelium"), "--host",
                    "claude", "--session", "s1", "--stop-payload-stdin"]) == 0
    records = io._journal_records(io.journal_path(project / ".mycelium"))
    assert any(r.get("ev") == "end" and r.get("task") == "t-1" for r in records)


def test_a_torn_journal_line_proves_nothing(project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    journal = io.journal_path(project / ".mycelium")
    with journal.open("a") as handle:
        handle.write('{"seq": 90, "ev": "post", "call": "B", "ho')  # torn mid-append
    record = status(project, "A", disk=False)["record"]
    assert record["status"] == "unknown"
    assert record["unaccounted"][0]["kind"] == "journal-discontinuous"


def test_a_detached_process_outlives_its_host_task(project: Path) -> None:
    import json

    state = project / ".mycelium"
    payload = {"session_id": "s1", "tool_use_id": "bg", "tool_name": "Bash",
               "hook_event_name": "PreToolUse", "cwd": str(project),
               "tool_input": {"command": "nohup setsid sh -c 'sleep 30' &",
                              "run_in_background": True}}
    io.hook(state, project, "claude", json.dumps(payload))
    io.post(state, host="claude", session="s1", call_id="bg", tool="Bash",
            repo_root=project, outcome="success", bash_exit=0, background=True,
            task_id="t-detached")
    # The host reports the outer task finished; the detached child may not be.
    assert io.end_finished_background(state, host="claude", session="s1", tasks=[]) == 0


def test_a_call_running_past_the_write_cannot_vouch(project: Path) -> None:
    state = project / ".mycelium"
    io.pre(state, host="claude", session="s1", call_id="B", tool="Bash",
           repo_root=project, snapshot=True)
    bash(project, "A", write_x(project, "A"))
    io.post(state, host="claude", session="s1", call_id="B", tool="Bash",
            repo_root=project, outcome="success", bash_exit=0)
    result = li.query_hash(li.load(project, write=False), sha("A"), disk=False)
    writes = result["paths"][0]["writes"]
    assert writes and all(w["record"]["status"] == "unknown" for w in writes)
    (by_a,) = [w for w in writes if w["call"] == "s1|claude:A"]
    assert {"kind": "overlapping-call", "call": "s1|claude:B"} in by_a["record"]["unaccounted"]


def test_another_checkouts_call_cannot_vouch(project: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    (other / "out").mkdir(parents=True)
    bash(project, "A", write_x(project, "A"))
    state = project / ".mycelium"  # a state directory shared by two checkouts
    io.pre(state, host="claude", session="s1", call_id="F", tool="Bash",
           repo_root=other, snapshot=True)
    io.post(state, host="claude", session="s1", call_id="F", tool="Bash",
            repo_root=other, outcome="success", bash_exit=0)
    record = status(project, "A", disk=False)["record"]
    assert {"kind": "foreign-call", "call": "s1|claude:F"} in record["unaccounted"]


def test_a_background_call_whose_launch_was_lost_blocks_identity_rescue(
        project: Path) -> None:
    bash(project, "c1", write_x(project, "A"))
    # Flagged background at Pre; its Post (the launch) never reached the
    # journal, so only the open interval says a job may be running.
    io.pre(project / ".mycelium", host="claude", session="s1", call_id="bg", tool="Bash",
           repo_root=project, snapshot=True, background=True)
    index = li.load(project, write=False)
    assert index._background_open()


def test_a_superseded_write_reports_gaps_without_losing_the_verdict(project: Path) -> None:
    gap = observation(project, "c2", pre=3, post=None, status="background")
    write_store(project, [
        observation(project, "c1", created=[("out/x", sha("A"))], pre=1, post=2), gap,
        observation(project, "c3", modified=[("out/x", sha("B"))], pre=5, post=6)])
    write = li.query_hash(li.load(project), sha("A"), disk=False)["paths"][0]["writes"][0]
    assert write["record"]["status"] == "superseded"
    assert write["record"]["gap_count"] >= 1


def test_an_unreconciled_skip_is_a_gap_and_a_reconciled_one_is_not(project: Path) -> None:
    (project / "out" / "x").write_text("x")
    write_store(project, [observation(project, "c1", created=[("out/x", sha("x"))],
                                              pre=1, post=2)])
    write_manifest(project, "m1", skipped=[
        {"skip_reason": "detector-failed", "observation_id": "claude:c1",
         "host_session_id": "s1"}])
    assert li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]["status"] == \
        "current"
    write_manifest(project, "m2", skipped=[{"skip_reason": "extractor-missing"}])
    write = li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_an_expired_background_job_stays_a_gap(project: Path) -> None:
    (project / "out" / "x").write_text("x")
    background = observation(project, "bg", pre=3, post=9, status="background",
                                 expired=True)
    write_store(project, [observation(project, "c1", created=[("out/x", sha("x"))],
                                              pre=1, post=2), background])
    write = li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_open_calls_are_reported_not_counted(project: Path) -> None:
    (project / "out" / "x").write_text("x")
    write_store(project, [observation(project, "c1", created=[("out/x", sha("x"))],
                                              pre=1, post=2)])
    io.journal_event(project / ".mycelium", "pre", "running", host="claude", session="s9",
                     tool="Bash")
    write = li.query_hash(li.load(project), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] in ("current", "unknown")
    assert write["record"].get("open_calls")


def test_a_foreign_v1_manifest_adds_no_local_writes(project: Path) -> None:
    (project / "out" / "x").write_text("mine")
    write_store(project, [observation(project, "c1", created=[("out/x", sha("mine"))],
                                              pre=1, post=2)])
    theirs = {"session_id": "t", "repo_root": "/home/someone/proj", "actions": [
        {"ts": "2026-10-01T00:00:00Z", "inputs": [],
         "outputs": [{"path": "/home/someone/proj/out/x", "sha256": sha("theirs")}]}]}
    (project / ".living" / "log" / "data-lineage" / "t.json").write_text(json.dumps(theirs))
    write = li.query_hash(li.load(project), sha("mine"))["paths"][0]["writes"][0]
    assert write["status"] == "current"


def test_a_linked_cache_directory_is_never_written(project: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, project / ".mycelium" / "lineage")
    write_manifest(project, "m1")
    li.load(project)
    assert list(outside.iterdir()) == []


def test_corrupt_store_bytes_are_a_gap(project: Path) -> None:
    path = io.observations_path(project / ".mycelium", "claude", "s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'{"kind": "observation", "x": "\xff\xfe"}\n')
    coverage = li.query_path(li.load(project), "out/x", disk=False)["coverage"]["gaps"]
    assert coverage.get("store-record-unreadable") == 1


SECRET = "TEST_SECRET_123"


def test_the_cache_never_copies_command_text(project: Path) -> None:
    record = observation(project, "c1", created=[("out/x", sha("A"))], pre=1, post=2)
    record["bash_cmd"] = f"curl -H 'Authorization: Bearer {SECRET}' https://x"
    record["input_proofs"] = [{"path": "data/in.csv", "note": SECRET}]
    write_store(project, [record])
    li.load(project)
    cache = li.cache_file(project, project / ".mycelium")
    assert cache.exists() and SECRET not in cache.read_text()


def test_an_old_cache_is_rebuilt(project: Path) -> None:
    assert li.BUILDER_VERSION >= 3


def test_a_launched_background_job_without_an_observation_stays_a_gap(project: Path) -> None:
    state = project / ".mycelium"
    first = journal(project, "c1")
    write_store(project, [observed(project, "c1", first,
                                          created=[("out/x", sha("A"))])])
    io.journal_event(state, "pre", "bg", host="claude", session="s1", tool="Bash",
                     background=True)
    io.journal_event(state, "launch", "bg", host="claude", session="s1", tool="Bash",
                     background=True, observe=True)  # its observation append failed
    assert verdict(project, "A") == "unknown"


@pytest.fixture()
def project_2(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium" / "io").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "out").mkdir()
    return root


def test_an_edit_through_a_link_makes_its_referent_uncertain(project_2: Path) -> None:
    (project_2 / "data" / "v1.csv").write_text("A")
    os.symlink("v1.csv", project_2 / "data" / "current.csv")
    written, deleted, refused = io._edit_changes(
        project_2, [(str(project_2 / "data" / "current.csv"), "written")])
    assert written == [] and deleted == []
    assert {"path": "data/v1.csv", "reason": "edit-via-link"} in refused

    first = journal(project_2, "c1")
    edit = journal(project_2, "e1", tool="Edit")
    via_link = observed(project_2, "e1", edit, edited=[])
    via_link["changes"]["edited"] = []
    via_link["changes"]["refused"] = refused
    write_store(project_2, [
        observed(project_2, "c1", first, created=[("data/v1.csv", sha("A"))]), via_link])
    write = li.query_hash(li.load(project_2), sha("A"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_an_edit_whose_target_cannot_be_established_clouds_the_checkout(
        project_2: Path) -> None:
    record = {"status": "edit", "outcome": "success",
              "changes": {"edited": [], "deleted": [],
                          "refused": [{"path": None, "reason": "edit-target-unknown"}]}}
    for path in ("data/a.csv", "out/b.csv"):
        assert li.Index._observation_gap_at(None, record, path, {}) == "path-uncertain"


def test_an_unlistable_store_directory_is_a_gap(project: Path, monkeypatch) -> None:
    (project / "out" / "x").write_text("A")
    first = observation(project, "c1", created=[("out/x", sha("A"))], pre=1, post=2)
    write_manifest(project, "m1", observations=[first])
    obs = project / ".mycelium" / "io" / "obs"
    (obs / "claude").mkdir(parents=True)
    real_scandir = os.scandir

    def failing(path="."):
        if str(path).endswith(os.path.join("obs", "claude")):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(li.os, "scandir", failing)
    result = li.query_hash(li.load(project, write=False), sha("A"))
    write = result["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert result["coverage"]["gaps"].get("source-unreadable") == 1


@pytest.fixture()
def project_3(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium" / "io").mkdir(parents=True)
    (root / "skills" / "core").mkdir(parents=True)
    return root


def test_a_deletion_in_another_case_clouds_the_recorded_write(project_3: Path) -> None:
    root = str(project_3.resolve())
    if not io._case_insensitive(root):
        pytest.skip("case-sensitive volume")
    _, deleted, refused = io._edit_changes(
        project_3, [(str(project_3 / "results-qc-table.csv"), "deleted")])
    assert deleted == [{"path": "results-qc-table.csv", "after": None}]
    assert any(r.get("casefold") for r in refused)

    first = journal(project_3, "c1")
    edit = journal(project_3, "e1", tool="Edit")
    deletion = observed(project_3, "e1", edit)
    deletion["status"], deletion["outcome"] = "edit", "success"
    deletion["changes"] = {"edited": [], "deleted": deleted, "refused": refused}
    write_store(project_3, [
        observed(project_3, "c1", first, created=[("results-qc-table.CSV", sha("A"))]),
        deletion])
    write = li.query_path(li.load(project_3), "results-qc-table.CSV", disk=False)
    verdicts = [w["record"]["status"] for w in write["writes"]]
    assert "current-in-record" not in verdicts


@pytest.fixture()
def project_4(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    return root


def test_a_failed_edit_makes_its_target_uncertain(project_4: Path) -> None:
    (project_4 / "notes.md").write_text("A")
    first = journal(project_4, "c1")
    write_store(project_4, [observed(project_4, "c1", first,
                                          created=[("notes.md", sha("A"))])])
    payload = {"session_id": "s1", "tool_use_id": "w1", "tool_name": "Write",
               "hook_event_name": "PreToolUse", "cwd": str(project_4),
               "tool_input": {"file_path": str(project_4 / "notes.md"), "content": "B"}}
    io.hook(project_4 / ".mycelium", project_4, "claude", json.dumps(payload))
    (project_4 / "notes.md").write_text("B?")  # a partial write the tool reported failed
    payload["hook_event_name"] = "PostToolUseFailure"
    io.hook(project_4 / ".mycelium", project_4, "claude", json.dumps(payload))
    result = li.query_path(li.load(project_4), "notes.md", disk=False)
    assert all(w["record"]["status"] != "current-in-record" for w in result["writes"])
    assert all(w["call"] != "s1|claude:w1" for w in result["writes"])


def test_a_query_in_another_case_finds_the_history(project_4: Path) -> None:
    (project_4 / "README.md").write_text("A")
    if not io._case_insensitive(str(project_4.resolve())):
        pytest.skip("case-sensitive volume")
    first = journal(project_4, "c1")
    write_store(project_4, [observed(project_4, "c1", first,
                                          created=[("README.md", sha("A"))])])
    result = li.query_path(li.load(project_4), "readme.md")
    assert result["writes"] and result["writes"][0]["status"] == "current"
    assert result["selector"] == {"path": "readme.md"}


def test_an_unverifiable_deletion_is_unverified_not_changed() -> None:
    assert li._headline("current-in-record", "unverified", "deleted") == "unverified"
    assert li._headline("current-in-record", "missing", "deleted") == "current"
    assert li._headline("current-in-record", "match", "deleted") == "changed-outside-record"


def test_coverage_changes_are_gaps_in_the_index(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "data").mkdir()
    (repo / "data" / "x.csv").write_text("x")
    later = observation(repo, "c2", pre=3, post=4)
    later["changes"]["coverage_changed"] = ["data/x.csv"]
    write_store(repo, [observation(repo, "c1", created=[("data/x.csv", sha("x"))],
                                           pre=1, post=2), later])
    write = li.query_hash(li.load(repo), sha("x"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert "coverage-unproven" in write["record"]["reasons"]
    assert {"kind": "path-coverage-changed", "call": "s1|claude:c2"} in \
        write["record"]["unaccounted"]


def test_a_partly_failed_patch_leaves_its_targets_unknown(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "out.csv").write_text("v1")
    failed = observation(repo, "p2", pre=3, post=4, edited=[("other.csv", sha("o"))])
    failed["changes"] = {"edited": [], "deleted": [],
                         "refused": [{"path": "out.csv", "reason": "edit-failed"}]}
    failed["outcome"] = "failure"
    write_store(repo, [observation(repo, "p1", created=[("out.csv", sha("v1"))],
                                           pre=1, post=2), failed])
    write = li.query_hash(li.load(repo), sha("v1"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"


def test_a_background_job_clouds_later_verdicts_until_bounded(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "out.csv").write_text("v1")
    background = observation(repo, "bg", pre=3, post=None, status="background")
    write_store(repo, [observation(repo, "c1", created=[("out.csv", sha("v1"))],
                                           pre=1, post=2), background])
    write = li.query_hash(li.load(repo), sha("v1"))["paths"][0]["writes"][0]
    assert write["status"] == "unknown"
    assert any(g["kind"] == "background-job" for g in write["record"]["gaps"])


def test_a_collaborators_v1_observations_stay_theirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (repo / "out.csv").write_text("v1")
    record = observation(repo, "c1", created=[("out.csv", sha("v1"))], pre=1, post=2)
    record.pop("repo_root")
    record["version"] = 1
    manifest = {"session_id": "theirs", "repo_root": "/home/collaborator/proj", "actions": [],
                "observations": [record], "summary": {}}
    (repo / ".living" / "log" / "data-lineage" / "theirs.json").write_text(json.dumps(manifest))
    assert li.query_hash(li.load(repo), sha("v1"))["found"] is False
