"""Stop-time consolidation keeps observation evidence intact.

Manifests must carry each record's observation link, join the referenced
observations from the host session's store, and never replace a hash taken
under the observation protocol with one computed from later disk content.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import extract_data_lineage as stop  # noqa: E402
import io_observer as io  # noqa: E402


def _write_events(path: Path, records: list[dict]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def _event(repo: Path, **extra) -> dict:
    record = {
        "ts": "2026-11-21T10:00:00Z",
        "bash_cmd": "python fit.py",
        "bash_exit": 0,
        "script": str(repo / "fit.py"),
        "script_sha256": "a" * 64,
        "inputs": [
            {"path": str(repo / "data" / "in.csv"), "sha256": "b" * 64, "source": "inferred-static",
             "pre_run_proven": True, "hash_status": "hashed", "size_bytes": 4}
        ],
        "outputs": [
            {"path": str(repo / "out" / "res.csv"), "sha256": None, "source": "inferred-static",
             "hash_status": "pending", "size_bytes": 9}
        ],
        "io_detection": "static",
        "observation_id": "claude:toolu_1",
        "observation_status": "no-known-overlap",
        "observation_session": "s1",
    }
    record.update(extra)
    return record


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / "out").mkdir()
    (repo / "data" / "in.csv").write_text("NEW CONTENT AT STOP\n")
    (repo / "out" / "res.csv").write_text("LATER\n")
    (repo / ".mycelium").mkdir()
    return repo


def _store(repo: Path, records: list[dict], host: str = "claude", session: str = "s1") -> None:
    path = io.observations_path(repo / ".mycelium", host, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _observation(**extra) -> dict:
    record = {
        "kind": "observation",
        "observation_id": "claude:toolu_1",
        "host": "claude",
        "host_session_id": "s1",
        "status": "no-known-overlap",
        "changes": {
            "created": [{"path": "out/res.csv", "before": None,
                         "after": {"sha256": "c" * 64, "size_bytes": 9, "hash_status": "hashed"}}],
            "modified": [],
            "deleted": [],
            "metadata_only": [],
            "rename_candidates": [],
            "counts": {"created": 1, "modified": 0, "deleted": 0},
            "capped": False,
        },
    }
    record.update(extra)
    return record


def test_new_keys_survive_normalization(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    manifest = stop.extract(events, "2026-11-21-001", str(repo))
    (action,) = manifest["actions"]
    assert action["observation_id"] == "claude:toolu_1"
    assert action["observation_status"] == "no-known-overlap"
    assert action["inputs"][0]["source"] == "inferred-static"
    assert action["inputs"][0]["pre_run_proven"] is True


def test_protocol_hashes_are_never_replaced_from_disk_at_stop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    (action,) = stop.extract(events, "x", str(repo))["actions"]
    # The input hash was taken under the protocol; the file changed since.
    assert action["inputs"][0]["sha256"] == "b" * 64
    # A pending output stays pending: a Stop-time hash would be later content.
    assert action["outputs"][0]["sha256"] is None
    assert action["outputs"][0]["hash_status"] == "pending"
    assert "n_rows" not in action["outputs"][0]


def test_old_format_records_keep_their_previous_enrichment(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old = {
        "ts": "2026-01-01T00:00:00Z",
        "bash_cmd": "python fit.py",
        "script": None,
        "inputs": [{"path": str(repo / "data" / "in.csv")}],
        "outputs": [],
    }
    events = _write_events(tmp_path / "e.ndjson", [old])
    (action,) = stop.extract(events, "x", str(repo))["actions"]
    assert action["inputs"][0]["sha256"]  # filled from disk, as before
    assert action["observation_id"] is None


def test_referenced_observations_are_joined(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _store(repo, [_observation(), _observation(observation_id="claude:other")])
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    manifest = stop.extract(events, "x", str(repo))
    assert [o["observation_id"] for o in manifest["observations"]] == ["claude:toolu_1"]
    assert manifest["summary"]["observation_counts"] == {"no-known-overlap": 1}
    assert manifest["summary"]["observations_missing"] == []
    (action,) = manifest["actions"]
    assert action["outputs"][0]["observed_change"] == "created"


def test_real_observation_wins_over_a_swept_gap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gap = {"kind": "observation", "observation_id": "claude:toolu_1", "host": "claude",
           "host_session_id": "s1", "status": "unavailable", "reason": "call-unresolved"}
    _store(repo, [gap, _observation(status="background")])
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    (joined,) = stop.extract(events, "x", str(repo))["observations"]
    assert joined["status"] == "background"


def test_missing_observation_is_reported_not_dropped(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    manifest = stop.extract(events, "x", str(repo))
    assert manifest["observations"] == []
    assert manifest["summary"]["observations_missing"] == ["claude:toolu_1"]
    assert any("observation" in w for w in manifest["extraction_warnings"])
    (action,) = manifest["actions"]
    assert "observed_change" not in action["outputs"][0]


def test_output_named_but_not_written_is_marked_not_observed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    obs = _observation()
    obs["changes"]["created"] = []
    obs["changes"]["counts"]["created"] = 0
    _store(repo, [obs])
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    (action,) = stop.extract(events, "x", str(repo))["actions"]
    assert action["outputs"][0]["observed_change"] == "unchanged"


def test_unavailable_observation_claims_nothing_about_outputs(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _store(repo, [_observation(status="unavailable", reason="no-pre-snapshot", changes=None)])
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    (action,) = stop.extract(events, "x", str(repo))["actions"]
    assert "observed_change" not in action["outputs"][0]


def test_data_command_keeps_observation_link_and_is_joined(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _store(repo, [_observation(observation_id="claude:toolu_9")])
    record = {
        "ts": "2026-11-21T10:00:00Z",
        "kind": "data_command",
        "event_id": "e" * 32,
        "bash_cmd": "./tool --make",
        "bash_exit": 0,
        "signals": ["observed-change"],
        "named_paths": [],
        "inputs": [],
        "outputs": [],
        "observation_id": "claude:toolu_9",
        "observation_status": "no-known-overlap",
        "observation_session": "s1",
    }
    events = _write_events(tmp_path / "e.ndjson", [record])
    manifest = stop.extract(events, "x", str(repo))
    (command,) = manifest["data_commands"]
    assert command["signals"] == ["observed-change"]
    assert command["observation_id"] == "claude:toolu_9"
    assert [o["observation_id"] for o in manifest["observations"]] == ["claude:toolu_9"]


def test_symlinked_observation_store_is_not_read(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    elsewhere = tmp_path / "elsewhere.ndjson"
    elsewhere.write_text(json.dumps(_observation()) + "\n")
    store = io.observations_path(repo / ".mycelium", "claude", "s1")
    store.parent.mkdir(parents=True)
    store.symlink_to(elsewhere)
    events = _write_events(tmp_path / "e.ndjson", [_event(repo)])
    manifest = stop.extract(events, "x", str(repo))
    assert manifest["observations"] == []
    assert manifest["summary"]["observations_missing"] == ["claude:toolu_1"]
