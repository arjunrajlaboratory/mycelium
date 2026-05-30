"""Tests for extract_data_lineage.py — run with `python3 -m pytest test_extract_data_lineage.py -v`."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from extract_data_lineage import (  # noqa: E402
    SIZE_LIMIT_BYTES,
    build_manifest,
    enrich_file_record,
    extract,
    n_rows_if_tabular,
    normalize_event,
    sha256_file,
)


# ---------- sha256_file ----------


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"hello world")
    assert sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


def test_sha256_file_returns_none_for_missing(tmp_path: Path) -> None:
    assert sha256_file(tmp_path / "nope") is None


def test_sha256_file_returns_none_for_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 100)
    monkeypatch.setattr("extract_data_lineage.SIZE_LIMIT_BYTES", 10)
    assert sha256_file(p) is None


# ---------- n_rows_if_tabular ----------


def test_n_rows_csv(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("a,b,c\n1,2,3\n4,5,6\n7,8,9\n", encoding="utf-8")
    assert n_rows_if_tabular(p) == 3


def test_n_rows_non_tabular_returns_none(tmp_path: Path) -> None:
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG")
    assert n_rows_if_tabular(p) is None


def test_n_rows_missing_file_returns_none(tmp_path: Path) -> None:
    assert n_rows_if_tabular(tmp_path / "nope.csv") is None


# ---------- enrich_file_record ----------


def test_enrich_computes_sha_when_missing(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    rec = enrich_file_record({"path": str(p)})
    assert rec["sha256"] == hashlib.sha256(b"data").hexdigest()
    assert rec["size_bytes"] == 4


def test_enrich_preserves_existing_sha(tmp_path: Path) -> None:
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    rec = enrich_file_record({"path": str(p), "sha256": "deadbeef"})
    assert rec["sha256"] == "deadbeef"


def test_enrich_marks_missing_files(tmp_path: Path) -> None:
    rec = enrich_file_record({"path": str(tmp_path / "ghost.bin")})
    assert rec.get("_missing") is True
    assert "sha256" not in rec


def test_enrich_marks_oversize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "big.bin"
    p.write_bytes(b"x" * 50)
    monkeypatch.setattr("extract_data_lineage.SIZE_LIMIT_BYTES", 10)
    rec = enrich_file_record({"path": str(p)})
    assert rec.get("_skipped_too_large") is True
    assert rec.get("sha256") is None


def test_enrich_fills_n_rows_for_csv(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    rec = enrich_file_record({"path": str(p)})
    assert rec["n_rows"] == 2


def test_enrich_no_path_returns_unchanged() -> None:
    rec = enrich_file_record({"comment": "no path"})
    assert rec == {"comment": "no path"}


# ---------- normalize_event ----------


def test_normalize_minimal_event_with_ts() -> None:
    warnings: list[str] = []
    e = normalize_event({"ts": "2026-05-26T12:00:00Z"}, warnings, 0)
    assert e is not None
    assert e["ts"] == "2026-05-26T12:00:00Z"
    assert e["inputs"] == []
    assert e["outputs"] == []
    assert e["filters_detected"] == []
    assert e["agent_id"] is None
    assert warnings == []


def test_normalize_event_missing_ts_skipped() -> None:
    warnings: list[str] = []
    assert normalize_event({"bash_cmd": "x"}, warnings, 3) is None
    assert any("missing 'ts'" in w for w in warnings)


def test_normalize_non_dict_skipped() -> None:
    warnings: list[str] = []
    assert normalize_event("not a dict", warnings, 5) is None  # type: ignore[arg-type]
    assert any("not a JSON object" in w for w in warnings)


def test_normalize_computes_script_sha_when_missing(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text("print('hi')\n", encoding="utf-8")
    warnings: list[str] = []
    e = normalize_event(
        {"ts": "2026-05-26T12:00:00Z", "script": str(script)},
        warnings,
        0,
    )
    assert e is not None
    assert e["script_sha256"] == hashlib.sha256(b"print('hi')\n").hexdigest()


def test_normalize_preserves_provided_script_sha() -> None:
    warnings: list[str] = []
    e = normalize_event(
        {"ts": "2026-05-26T12:00:00Z", "script": "x.py", "script_sha256": "abc"},
        warnings,
        0,
    )
    assert e is not None
    assert e["script_sha256"] == "abc"


# ---------- build_manifest ----------


def test_build_manifest_empty_events() -> None:
    m = build_manifest([], "sid", "/repo", [])
    assert m["n_actions"] == 0
    assert m["actions"] == []
    assert m["started_at"] is None
    assert m["summary"]["unique_inputs"] == []


def test_build_manifest_orders_chronologically_and_dedupes() -> None:
    events = [
        {
            "ts": "2026-05-26T12:00:02Z",
            "agent_id": "abc",
            "agent_type": "general-purpose",
            "bash_cmd": "python b.py",
            "bash_exit": 0,
            "bash_wall_s": 3.0,
            "script": "b.py",
            "script_sha256": None,
            "script_source": None,
            "git_sha": "deadbeef",
            "inputs": [{"path": "data/x.parquet", "sha256": "aa"}],
            "outputs": [{"path": "out/y.parquet", "sha256": "bb"}],
            "filters_detected": [],
            "seeds_detected": [],
        },
        {
            "ts": "2026-05-26T12:00:01Z",
            "agent_id": None,
            "agent_type": None,
            "bash_cmd": "python a.py",
            "bash_exit": 0,
            "bash_wall_s": 1.0,
            "script": "a.py",
            "script_sha256": None,
            "script_source": None,
            "git_sha": "deadbeef",
            "inputs": [{"path": "data/x.parquet", "sha256": "aa"}],
            "outputs": [{"path": "out/z.parquet", "sha256": "cc"}],
            "filters_detected": ["df.query('a==1')"],
            "seeds_detected": [42],
        },
    ]
    m = build_manifest(events, "sid", "/repo", [])
    assert m["n_actions"] == 2
    assert m["actions"][0]["bash_cmd"] == "python a.py"
    assert m["actions"][1]["bash_cmd"] == "python b.py"
    assert m["started_at"] == "2026-05-26T12:00:01Z"
    assert m["ended_at"] == "2026-05-26T12:00:02Z"
    assert m["git_sha"] == "deadbeef"
    assert sorted(m["agents_seen"]) == ["abc", "main"]
    assert m["summary"]["total_wall_seconds"] == 4  # round(1.0 + 3.0) == 4
    inputs = {r["path"] for r in m["summary"]["unique_inputs"]}
    outputs = {r["path"] for r in m["summary"]["unique_outputs"]}
    assert inputs == {"data/x.parquet"}  # deduped
    assert outputs == {"out/y.parquet", "out/z.parquet"}
    assert m["summary"]["scripts_executed"] == ["a.py", "b.py"]


# ---------- extract (end-to-end) ----------


def _write_events(path: Path, events: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_extract_end_to_end(tmp_path: Path) -> None:
    script = tmp_path / "s.py"
    script.write_text("import pandas\n", encoding="utf-8")
    input_csv = tmp_path / "in.csv"
    input_csv.write_text("a,b\n1,2\n3,4\n", encoding="utf-8")

    events_file = tmp_path / "events.ndjson"
    _write_events(
        events_file,
        [
            {
                "ts": "2026-05-26T12:00:00Z",
                "bash_cmd": f"python {script}",
                "script": str(script),
                "inputs": [{"path": str(input_csv)}],
                "outputs": [],
                "bash_exit": 0,
                "bash_wall_s": 0.1,
            }
        ],
    )

    m = extract(events_file, "test-sid", str(tmp_path))
    assert m["n_actions"] == 1
    a = m["actions"][0]
    assert a["script_sha256"] is not None
    assert a["inputs"][0]["sha256"] is not None
    assert a["inputs"][0]["n_rows"] == 2
    assert m["extraction_warnings"] == []


def test_extract_handles_invalid_json_line(tmp_path: Path) -> None:
    events_file = tmp_path / "events.ndjson"
    events_file.write_text(
        '{"ts": "2026-05-26T12:00:00Z"}\nthis is not json\n{"ts": "2026-05-26T12:00:01Z"}\n',
        encoding="utf-8",
    )
    m = extract(events_file, "sid", str(tmp_path))
    assert m["n_actions"] == 2
    assert any("invalid JSON" in w for w in m["extraction_warnings"])


def test_extract_missing_events_file(tmp_path: Path) -> None:
    m = extract(tmp_path / "nope.ndjson", "sid", str(tmp_path))
    assert m["n_actions"] == 0
    assert any("events file not found" in w for w in m["extraction_warnings"])


def test_extract_handles_missing_input_file(tmp_path: Path) -> None:
    events_file = tmp_path / "events.ndjson"
    _write_events(
        events_file,
        [
            {
                "ts": "2026-05-26T12:00:00Z",
                "inputs": [{"path": str(tmp_path / "ghost.parquet")}],
            }
        ],
    )
    m = extract(events_file, "sid", str(tmp_path))
    assert m["actions"][0]["inputs"][0].get("_missing") is True


def test_constants_size_limit() -> None:
    assert SIZE_LIMIT_BYTES == 100 * 1024 * 1024


# ---------- scripts_executed handling for inline -c (issue C fix) ----------


def test_scripts_executed_includes_inline_with_sha_label() -> None:
    """Inline -c events (script=None) should still appear in scripts_executed via SHA label."""
    events = [
        {
            "ts": "2026-05-26T12:00:00Z",
            "agent_id": None,
            "agent_type": None,
            "bash_cmd": "python -c 'pd.read_csv(...)'",
            "bash_exit": 0,
            "bash_wall_s": 0.5,
            "script": None,
            "script_sha256": "ab12cd34ef56" + "0" * 52,
            "script_source": "inline source here",
            "git_sha": "abc",
            "inputs": [],
            "outputs": [],
            "filters_detected": [],
            "seeds_detected": [],
        },
    ]
    m = build_manifest(events, "sid", "/repo", [])
    assert m["summary"]["scripts_executed"] == ["(inline ab12cd34ef56)"]


def test_scripts_executed_mixes_named_and_inline() -> None:
    events = [
        {
            "ts": "2026-05-26T12:00:00Z",
            "agent_id": None,
            "agent_type": None,
            "bash_cmd": "python a.py",
            "bash_exit": 0,
            "bash_wall_s": 1.0,
            "script": "a.py",
            "script_sha256": "shashashasha" + "0" * 52,
            "script_source": None,
            "git_sha": "abc",
            "inputs": [],
            "outputs": [],
            "filters_detected": [],
            "seeds_detected": [],
        },
        {
            "ts": "2026-05-26T12:00:01Z",
            "agent_id": None,
            "agent_type": None,
            "bash_cmd": "python -c '...'",
            "bash_exit": 0,
            "bash_wall_s": 0.5,
            "script": None,
            "script_sha256": "ab12cd34ef56" + "0" * 52,
            "script_source": "x",
            "git_sha": "abc",
            "inputs": [],
            "outputs": [],
            "filters_detected": [],
            "seeds_detected": [],
        },
    ]
    m = build_manifest(events, "sid", "/repo", [])
    assert m["summary"]["scripts_executed"] == ["a.py", "(inline ab12cd34ef56)"]


def test_scripts_executed_dedupes_repeated_inline() -> None:
    """Two events with the same inline source SHA should appear once."""
    base = {
        "ts": "2026-05-26T12:00:00Z",
        "agent_id": None,
        "agent_type": None,
        "bash_cmd": "python -c '...'",
        "bash_exit": 0,
        "bash_wall_s": 0.1,
        "script": None,
        "script_sha256": "aa" * 32,
        "script_source": "x",
        "git_sha": "abc",
        "inputs": [],
        "outputs": [],
        "filters_detected": [],
        "seeds_detected": [],
    }
    e1 = dict(base)
    e2 = dict(base, ts="2026-05-26T12:00:01Z")
    m = build_manifest([e1, e2], "sid", "/repo", [])
    assert m["summary"]["scripts_executed"] == [f"(inline {'aa' * 6})"]
