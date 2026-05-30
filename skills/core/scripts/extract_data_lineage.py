#!/usr/bin/env python3
"""extract_data_lineage — consolidate per-session data lineage events into a manifest.

Inputs:
    --events-file: path to NDJSON events file (one event per line, written by
        the future mycelium-data-tracker.sh PostToolUse hook). Each event
        captures one execution of an analysis script (python/R/Rscript/jupyter)
        and the data files it touched.
    --output: path to write the consolidated manifest JSON.
    --session-id: session ID for the manifest header.
    --repo-root: repo root used for relativizing paths in the manifest.

Output:
    A JSON manifest at --output with the shape:
        {
          "session_id": "...",
          "repo_root": "...",
          "git_sha": "...",
          "started_at": "...",
          "ended_at": "...",
          "n_actions": N,
          "agents_seen": ["main", "<subagent-id>", ...],
          "actions": [<event 1>, <event 2>, ...],
          "summary": {
            "unique_inputs": [{"path", "sha256"}, ...],
            "unique_outputs": [{"path", "sha256"}, ...],
            "total_wall_seconds": N,
            "scripts_executed": [...]
          },
          "extraction_warnings": [...]
        }

Event shape (NDJSON, one line each) — produced by the tracker hook:
    {
      "ts": "2026-05-26T15:32:11Z",
      "agent_id": null,                      # subagent id or null
      "agent_type": null,                    # subagent type or null
      "bash_cmd": "python analysis/foo.py",
      "bash_exit": 0,
      "bash_wall_s": 4.7,
      "script": "analysis/foo.py",           # or null for `-c` inline
      "script_sha256": "ab12...",            # may be null → compute from disk
      "script_source": "...",                # embedded if <100KB else null
      "git_sha": "c3d4...",
      "inputs":  [{"path", "sha256", "n_rows"?}],
      "outputs": [{"path", "sha256", "n_rows"?}],
      "filters_detected": ["df.query(...)", ...],
      "seeds_detected": [42, ...]
    }

Design notes:
- The extractor is FORGIVING. Missing fields default to safe values. Invalid
  JSON lines are logged as warnings and skipped. Files that no longer exist
  on disk are recorded with sha256=null and a `_missing` marker.
- The extractor will COMPUTE SHAs when an event has them missing, for
  standalone-testing use. In production (events written by the tracker hook),
  SHAs should already be populated — computed at execution time, not at
  Stop time, for fidelity across iterative edit-run-edit-run cycles.
- Files >100 MB get path+size only (sha256: null, _skipped_too_large: true).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

SIZE_LIMIT_BYTES = 100 * 1024 * 1024  # 100 MB
TABULAR_SUFFIXES = {".parquet", ".csv", ".tsv", ".feather", ".arrow", ".h5ad"}


def sha256_file(path: Path) -> str | None:
    """SHA256 of a file, chunked. Returns None if file too large or unreadable."""
    if not path.exists() or not path.is_file():
        return None
    size = path.stat().st_size
    if size > SIZE_LIMIT_BYTES:
        return None
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def n_rows_if_tabular(path: Path) -> int | None:
    """Best-effort row count for tabular formats. Returns None on any failure."""
    if not path.exists() or path.suffix.lower() not in TABULAR_SUFFIXES:
        return None
    try:
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            try:
                import pyarrow.parquet as pq

                return pq.ParquetFile(path).metadata.num_rows
            except ImportError:
                return None
        if suffix in (".csv", ".tsv"):
            sep = "," if suffix == ".csv" else "\t"
            with path.open("r", encoding="utf-8", errors="replace") as f:
                return sum(1 for _ in f) - 1  # minus header
        if suffix == ".h5ad":
            try:
                import anndata as ad

                return ad.read_h5ad(path, backed="r").n_obs
            except ImportError:
                return None
        if suffix in (".feather", ".arrow"):
            try:
                import pyarrow.feather as fea

                return fea.read_table(path).num_rows
            except ImportError:
                return None
    except Exception:
        return None
    return None


def enrich_file_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Fill in sha256/n_rows for a file record if missing and file exists on disk."""
    out = dict(rec)
    path_str = out.get("path")
    if not path_str:
        return out
    path = Path(path_str)
    if not path.exists():
        out["_missing"] = True
        return out
    size = path.stat().st_size
    out.setdefault("size_bytes", size)
    if size > SIZE_LIMIT_BYTES:
        out.setdefault("sha256", None)
        out["_skipped_too_large"] = True
        return out
    if "sha256" not in out or out["sha256"] is None:
        out["sha256"] = sha256_file(path)
    if "n_rows" not in out:
        n = n_rows_if_tabular(path)
        if n is not None:
            out["n_rows"] = n
    return out


def normalize_event(
    raw: dict[str, Any], warnings: list[str], idx: int
) -> dict[str, Any] | None:
    """Normalize one event record. Returns None if the event is unusable."""
    if not isinstance(raw, dict):
        warnings.append(f"event[{idx}]: not a JSON object, skipped")
        return None
    if "ts" not in raw:
        warnings.append(f"event[{idx}]: missing 'ts', skipped")
        return None

    event = {
        "ts": raw["ts"],
        "agent_id": raw.get("agent_id"),
        "agent_type": raw.get("agent_type"),
        "bash_cmd": raw.get("bash_cmd"),
        "bash_exit": raw.get("bash_exit"),
        "bash_wall_s": raw.get("bash_wall_s"),
        "script": raw.get("script"),
        "script_sha256": raw.get("script_sha256"),
        "script_source": raw.get("script_source"),
        "git_sha": raw.get("git_sha"),
        "inputs": [enrich_file_record(r) for r in (raw.get("inputs") or [])],
        "outputs": [enrich_file_record(r) for r in (raw.get("outputs") or [])],
        "filters_detected": list(raw.get("filters_detected") or []),
        "seeds_detected": list(raw.get("seeds_detected") or []),
    }

    if event["script"] and event["script_sha256"] is None:
        script_path = Path(event["script"])
        if script_path.exists():
            event["script_sha256"] = sha256_file(script_path)

    return event


def build_manifest(
    events: list[dict[str, Any]],
    session_id: str,
    repo_root: str,
    warnings: list[str],
) -> dict[str, Any]:
    """Assemble the consolidated manifest from normalized events."""
    if events:
        sorted_events = sorted(events, key=lambda e: e["ts"])
        started_at = sorted_events[0]["ts"]
        ended_at = sorted_events[-1]["ts"]
    else:
        sorted_events = []
        started_at = ended_at = None

    agents_seen = sorted({(e["agent_id"] or "main") for e in sorted_events})

    unique_inputs: dict[str, dict[str, Any]] = {}
    unique_outputs: dict[str, dict[str, Any]] = {}
    scripts_executed: list[str] = []
    total_wall = 0.0
    for e in sorted_events:
        # Prefer the script path; fall back to a label for inline `-c` / `-e`
        # invocations so the summary records every action, not just file-backed
        # scripts. Inline label uses the script SHA so distinct inline snippets
        # show as distinct entries.
        entry = e["script"] or (
            f"(inline {e.get('script_sha256', '')[:12]})"
            if e.get("script_sha256")
            else None
        )
        if entry and entry not in scripts_executed:
            scripts_executed.append(entry)
        if isinstance(e.get("bash_wall_s"), (int, float)):
            total_wall += float(e["bash_wall_s"])
        for rec in e["inputs"]:
            key = rec.get("path", "")
            if key and key not in unique_inputs:
                unique_inputs[key] = {"path": key, "sha256": rec.get("sha256")}
        for rec in e["outputs"]:
            key = rec.get("path", "")
            if key and key not in unique_outputs:
                unique_outputs[key] = {"path": key, "sha256": rec.get("sha256")}

    git_sha = next((e.get("git_sha") for e in sorted_events if e.get("git_sha")), None)

    return {
        "session_id": session_id,
        "repo_root": repo_root,
        "git_sha": git_sha,
        "started_at": started_at,
        "ended_at": ended_at,
        "n_actions": len(sorted_events),
        "agents_seen": agents_seen,
        "actions": sorted_events,
        "summary": {
            "unique_inputs": list(unique_inputs.values()),
            "unique_outputs": list(unique_outputs.values()),
            "total_wall_seconds": int(round(total_wall)),
            "scripts_executed": scripts_executed,
        },
        "extraction_warnings": warnings,
    }


def extract(events_file: Path, session_id: str, repo_root: str) -> dict[str, Any]:
    """End-to-end: read NDJSON events, normalize, build manifest."""
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    if events_file.exists():
        for idx, line in enumerate(
            events_file.read_text(encoding="utf-8").splitlines()
        ):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                warnings.append(f"event[{idx}]: invalid JSON ({exc}), skipped")
                continue
            normalized = normalize_event(raw, warnings, idx)
            if normalized is not None:
                events.append(normalized)
    else:
        warnings.append(f"events file not found: {events_file}")
    return build_manifest(events, session_id, repo_root, warnings)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events-file", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--repo-root", required=True)
    args = ap.parse_args()

    manifest = extract(args.events_file, args.session_id, args.repo_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(
        f"extract_data_lineage: wrote {args.output} "
        f"({manifest['n_actions']} actions, "
        f"{len(manifest['summary']['unique_inputs'])} inputs, "
        f"{len(manifest['summary']['unique_outputs'])} outputs, "
        f"{len(manifest['extraction_warnings'])} warnings)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
