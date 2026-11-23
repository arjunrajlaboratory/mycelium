#!/usr/bin/env python3
"""extract_data_lineage — consolidate per-session data lineage events into a manifest.

Inputs:
    --events-file: path to NDJSON events file (one event per line, written by
        the future mycelium-data-tracker.sh PostToolUse hook). Each event
        captures one execution of an analysis script (python/R/Rscript/jupyter)
        or pipeline entry point (shell script, Snakefile, sbatch job, main.nf)
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
            "total_wall_seconds": N | null,   # null when no action was timed
            "timed_action_count": N,
            "total_action_count": N,
            "known_exit_count": N,
            "failed_action_count": N,
            "scripts_executed": [...],
            "skipped_count": N,              # commands the recorder could not record
            "data_command_count": N          # other Bash calls that touched data
          },
          "skipped": [<skip 1>, ...],
          "data_commands": [<data command 1>, ...],
          "extraction_warnings": [...]
        }

Skip shape (NDJSON, `"kind": "skip"`) — written by the tracker hook when it
meant to record a command and could not, so a missing event is never read as
"nothing ran":
    {"ts", "kind": "skip", "skip_id", "skip_reason": "extractor-failed" | ...,
     "detail": "exit 3", "bash_cmd", "bash_exit", "agent_id", "agent_type"}
A skip_id is counted once even if a retried transfer repeated the record.
Manifests written before skips existed have no `skipped` key; read it as
unknown, not as zero.

Data command shape (NDJSON, `"kind": "data_command"`) — one per Bash call that
touched data without a proven analysis run (`head data/x.csv`, `cp a.tsv
outputs/`, an inline read the detector rejects). It is NOT an action: it never
claims that an analysis ran, so it is kept apart from `actions`:
    {"ts", "kind": "data_command", "event_id", "bash_cmd", "bash_exit", "cwd",
     "agent_id", "agent_type",
     "signals": ["data-path", "shell-io", "analysis-unproven"],
     "named_paths": [...],            # literal data-like words in the text;
                                      # mentions, not accesses; no direction
     "named_paths_truncated": bool,
     "analysis_candidates": [...],    # interpreter runs named but not proven
     "inputs":  [{"path", "sha256", "evidence": "redirection"|"cp"|"mv"}],
     "outputs": [{"path", "sha256", "evidence": ...}],  # only where shell
                                      # syntax proves direction, exit 0
     "io_detection": "unresolved"}
An event_id is counted once even if a retried transfer repeated the record.
Manifests written before data commands existed have no `data_commands` key;
read it as unknown, not as zero.

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
      "script_candidate": null,              # R run with an uncertain script:
                                             # the file word the command names
      "git_sha": "c3d4...",
      "inputs":  [{"path", "sha256", "n_rows"?}],
      "outputs": [{"path", "sha256", "n_rows"?}],
      "io_detection": "static" | "unresolved",
      "lineage_warnings": ["..."],
      "filters_detected": ["df.query(...)", ...],
      "seeds_detected": [42, ...]
    }

Observation fields (events and data commands from an observing recorder):
    "observation_id": "<host>:<tool_use_id>" | null,
    "observation_status": "no-known-overlap" | "overlap" | "background"
                          | "unavailable" | null,
    "observation_session": host session id | null,
    "observation_reason": why unavailable (only when present),
  and on each inferred input/output record:
    "source": "inferred-static" | "inferred-repository" | "inferred-shell",
    "hash_status": "hashed" | "cached" | "pending" | "unstable" | ...,
    "pre_run_proven": bool,          # inputs: hash is the pre-run content
    "proof_reason": "changed-during-call" | "racy-timestamp" | ...,
    "observed_change": "created" | "modified" | "deleted" | "unchanged"
                       | "unwatched" | "outside-scope"
                       | "unknown-capped",  # joined at Stop; absent when
                                            # the observation has no changes
  A null `observation_id` means the call was not observed (older recorder,
  host without PreToolUse): unknown, never "nothing changed".

Manifest additions:
    "observations": [<observation record>, ...],   # each referenced one,
                                    # from .mycelium/io/obs/<host>/<sid>.ndjson
    "summary": {..., "observation_counts": {"<status>": N},
                "observations_missing": ["<observation_id>", ...]}
  The observation record is documented in io_observer.py. It lists the
  files the call changed (created / modified / deleted, rename candidates,
  metadata-only changes) with before/after hashes, the overlapping calls,
  and the watched scope; it never lists reads.

Design notes:
- The extractor is FORGIVING. Missing fields default to safe values. Invalid
  JSON lines are logged as warnings and skipped. Files that no longer exist
  on disk are recorded with sha256=null and a `_missing` marker.
- For records from recorders older than the observation protocol, the
  extractor COMPUTES missing SHAs from disk at Stop. A record that carries
  `hash_status`, `source`, or `pre_run_proven` is never enriched: its hash,
  or its explicit absence, describes the content at the time of the call.
- Files >100 MB get path+size only (sha256: null, _skipped_too_large: true)
  in old-format records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
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
        fd = _open_regular_fd(path)
        if fd is None:
            return None
        with os.fdopen(fd, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _open_regular_fd(path: Path) -> int | None:
    """A read descriptor for ``path`` only if it is a regular file once
    opened. The open never blocks, so a path swapped for a FIFO after the
    caller's check cannot hang Stop."""
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    if stat.S_ISREG(os.fstat(fd).st_mode):
        return fd
    os.close(fd)
    return None


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
            fd = _open_regular_fd(path)
            if fd is None:
                return None
            with os.fdopen(fd, "r", encoding="utf-8", errors="replace") as f:
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


# Keys written at hook time under the observation protocol. A record that has
# any of them was captured by an observing recorder: its hash (or its
# explicit absence) describes the content at the time of the call, so Stop
# never fills or replaces it from what the file holds now.
_PROTOCOL_KEYS = ("hash_status", "source", "pre_run_proven", "rel_path")
_OBSERVATION_FIELDS = (
    "observation_id",
    "observation_status",
    "observation_session",
    "observation_reason",
)


def enrich_file_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Fill in sha256/n_rows for a file record if missing and file exists on disk.

    Only for records from recorders older than the observation protocol: a
    newer record keeps exactly what it was given at hook time.
    """
    out = dict(rec)
    if any(key in out for key in _PROTOCOL_KEYS):
        return out
    path_str = out.get("path")
    if not path_str or out.get("_missing"):
        # The hook found nothing at this path when the command ran; a file
        # created there later is not what the command touched.
        return out
    path = Path(path_str)
    if not path.exists():
        out["_missing"] = True
        return out
    if not path.is_file():
        # A FIFO, device, or directory: opening it to hash or count rows
        # could block Stop forever, and it has no content snapshot anyway.
        out.setdefault("sha256", None)
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


def _redact(text: Any) -> Any:
    """Command text through the shared redaction policy. v2 records were
    redacted at hook time; this also covers v1 records re-consolidated
    here and skips written by the shell hooks."""
    if not isinstance(text, str) or not text:
        return text
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lineage_redact

    return lineage_redact.redact_command(text)


def _redact_observation(observation: dict[str, Any]) -> dict[str, Any]:
    if isinstance(observation, dict) and isinstance(observation.get("bash_cmd"), str):
        return {**observation, "bash_cmd": _redact(observation["bash_cmd"])}
    return observation


# --- the commit allowlist ---------------------------------------------------
#
# A manifest is committed; the stores under .mycelium/ are machine-local.
# Every free-text field of every record a manifest holds keeps only words
# the allowlist in lineage_redact knows to be safe (TEXT_POLICY): vocabulary
# command words, option and assignment names of the fixed shape, short
# numbers, operators, and exactly the record's own EVIDENCE-BACKED paths.
# Anything else becomes `<v>`. Embedded script source is never committed
# (its digest is).
#
# A path is evidence-backed when the record holds proof the file was there:
# a digest actually taken (`sha256`, `observed_sha256`, or the script's
# `script_sha256`) or an observed change entry. An operand as typed, a
# config that was missing or unresolved, an unreadable script, a named path
# nobody hashed: none of these proves anything, so none lends a word to the
# command text. A config record without evidence also has its `path` and
# `rel_path` replaced by `<v>`. Consolidating committed text reads no file
# system (a named path is never checked on disk: an unresponsive mount would
# hang Stop).

TEXT_POLICY = "allowlist-v1"
_HEX64_RX = re.compile(r"[0-9a-f]{64}\Z")
_OBSERVED_CHANGES = frozenset({"created", "modified", "deleted"})
_LR: Any = None
_HERE = os.path.dirname(os.path.abspath(__file__))


def _lr() -> Any:
    global _LR
    if _LR is None:
        here = _HERE
        if here not in sys.path:
            sys.path.insert(0, here)
        import lineage_redact

        _LR = lineage_redact
    return _LR


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX64_RX.match(value))


def _has_evidence(rec: Any) -> bool:
    """A file record whose file was provably there: hashed, or in an
    observation's change list."""
    if not isinstance(rec, dict):
        return False
    return (_digest(rec.get("sha256")) or _digest(rec.get("observed_sha256"))
            or rec.get("observed_change") in _OBSERVED_CHANGES)


def _commit_file_records(records: Any) -> Any:
    """Input and output records in a committed manifest. A record with no
    evidence that its file was ever there (no hash, no observed change, no
    size) names a file that does not exist: its path may be any word the
    command held (`cp TOKEN out`), so it becomes `<v>`. A file that exists
    with a pending or skipped hash keeps its path; the index matches on it."""
    if not isinstance(records, list):
        return records
    out = []
    for rec in records:
        if isinstance(rec, dict) and not _has_evidence(rec) \
                and not isinstance(rec.get("size_bytes"), int):
            rec = {**rec, **{k: _lr().COMMIT_VALUE for k in ("path", "rel_path")
                             if isinstance(rec.get(k), str)}}
        out.append(rec)
    return out


def _evidence_paths(records: Any) -> list[str]:
    out: list[str] = []
    for rec in records if isinstance(records, list) else []:
        if _has_evidence(rec):
            out.extend(rec[k] for k in ("path", "rel_path") if isinstance(rec.get(k), str))
    return out


def _commit_config_files(configs: Any) -> Any:
    """Config records under the allowlist: one with evidence keeps its path;
    any other (missing, unresolved, remote, unhashed) has `path` and
    `rel_path` as `<v>`, since they are only the operand as typed."""
    lr = _lr()
    if not isinstance(configs, list):
        return configs
    out = []
    for config in configs:
        if not isinstance(config, dict):
            out.append(config)
            continue
        config = dict(config)
        if not _has_evidence(config):
            for key in ("path", "rel_path"):
                if isinstance(config.get(key), str):
                    config[key] = lr.COMMIT_VALUE
        out.append(config)
    return out


def _commit_env(env: Any, keep: set[str]) -> Any:
    lr = _lr()
    if not isinstance(env, dict):
        return env
    out: dict[str, Any] = {}
    for name, value in env.items():
        secret = lr._commit_secret_name(str(name))
        if isinstance(value, str):
            out[name] = lr.commit_safe_value(value, keep, secret)
        elif isinstance(value, dict) and isinstance(value.get("text"), str):
            out[name] = {**value, "text": lr.commit_safe_value(value["text"], keep, secret)}
        else:
            out[name] = value
    return out


_COMMIT_SEED_FLAGS = frozenset({"--seed", "--random-state", "--random_state", "--random-seed"})


def _short_seed(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and \
        _lr()._is_number(str(value))


def _argv_seed_survives(value: int, lists: list[list[Any]]) -> bool:
    """A seed read from the arguments is published only where the committed
    argv still shows it: a seed flag and its value both kept, adjacent
    (`--seed 42`) or joined (`--seed=42`). A value the argv masked (too long,
    after `--`, consumed by a secret flag) is never republished here."""
    text = str(value)
    for words in lists:
        for index, word in enumerate(words):
            if not isinstance(word, str):
                continue
            name, eq, rest = word.partition("=")
            if name not in _COMMIT_SEED_FLAGS:
                continue
            if eq and rest == text:
                return True
            if not eq and index + 1 < len(words) and words[index + 1] == text:
                return True
    return False


def _commit_seeds(step: dict[str, Any]) -> None:
    """`step.seeds` under the committed policy (after the argv is committed):
    an argv seed survives only as the committed argv shows it; a source seed
    (the source is not committed) only under the short-number rule."""
    seeds = step.get("seeds")
    if not isinstance(seeds, list):
        step.pop("seeds", None)
        return
    lists = [step[k] for k in ("script_args", "argv") if isinstance(step.get(k), list)]
    kept = []
    for seed in seeds:
        if not isinstance(seed, dict) or not _short_seed(seed.get("value")):
            continue
        if seed.get("origin") == "argv" and not _argv_seed_survives(seed["value"], lists):
            continue
        kept.append(seed)
    if kept:
        step["seeds"] = kept
    else:
        step.pop("seeds", None)


def _commit_step(step: Any, keep: set[str], known: set[str] | None = None) -> Any:
    """``known``: the long option names the run's own source spells (the
    evidence that such a word in its arguments is one of its options)."""
    lr = _lr()
    if not isinstance(step, dict):
        return step
    step = dict(step)
    if "argv" in step:
        step["argv"] = lr.commit_safe_argv(step["argv"], keep, True, known)
    for key in ("script_args", "params"):
        if key in step:
            step[key] = lr.commit_safe_argv(step[key], keep, False, known)
    if "wrappers" in step:
        step["wrappers"] = lr.commit_safe_argv(step["wrappers"], keep, command_word=True)
    for key in ("executable", "interpreter"):
        if isinstance(step.get(key), str):
            step[key] = lr.commit_safe_argv([step[key]], keep, command_word=True)[0]
    if "env" in step:
        step["env"] = _commit_env(step["env"], keep)
    if "seeds" in step:
        _commit_seeds(step)
    return step


def _commit_word_list(words: Any, keep: set[str], allowed: tuple[str, ...] = ()) -> list[Any]:
    """Recorded words (named paths, candidates): each kept only if it is
    exactly an evidence-backed path (or a fixed marker), never by shape."""
    lr = _lr()
    return [w if (w in allowed or (isinstance(w, str) and lr._keep_match(w, keep)))
            else lr.COMMIT_VALUE for w in words or []]


def commit_event(event: dict[str, Any]) -> dict[str, Any]:
    """An analysis event as a committed manifest holds it."""
    lr = _lr()
    event = dict(event)
    for direction in ("inputs", "outputs"):
        event[direction] = _commit_file_records(event.get(direction))
    step = dict(event["step"]) if isinstance(event.get("step"), dict) else None
    if step is not None and "config_files" in step:
        step["config_files"] = _commit_config_files(step["config_files"])
    paths = [*_evidence_paths(event.get("inputs")), *_evidence_paths(event.get("outputs"))]
    if isinstance(event.get("script"), str) and _digest(event.get("script_sha256")):
        paths.append(event["script"])
    if step is not None:
        paths.extend(_evidence_paths(step.get("config_files")))
    keep = lr.commit_keep_paths(paths)
    candidate = event.get("script_candidate")
    # A candidate is never read (R may have run another file), so it is
    # kept only if it is exactly one of the record's evidence-backed paths.
    safe_candidate = candidate if not isinstance(candidate, str) or lr._keep_match(
        candidate, keep) else lr.COMMIT_VALUE
    source = event.pop("script_source", None)
    # The run's own source (an inline body, or a script small enough to be
    # embedded) is the evidence that a long option word is one of its
    # options; nothing is read from disk here.
    known = lr.commit_source_options(source)
    event["bash_cmd"] = lr.commit_safe_command(event.get("bash_cmd"), keep, known)
    if "script_candidate" in event:
        event["script_candidate"] = safe_candidate
    if isinstance(source, str):
        # The source itself stays in the machine-local event queue; its
        # digest (script_sha256) identifies it here.
        event["script_source_bytes"] = len(source.encode("utf-8"))
        event["script_source_lines"] = source.count("\n") + (
            0 if source.endswith("\n") else 1)
    replace = ((candidate, safe_candidate),) if isinstance(candidate, str) else ()
    event["lineage_warnings"] = [
        lr.commit_safe_prose(w, keep, replace) for w in event.get("lineage_warnings") or []
    ]
    # Unevaluated regex text from 0.9.7 recorders: free source text.
    event.pop("filters_detected", None)
    if step is not None:
        event["step"] = _commit_step(step, keep, known)
    if "seeds_detected" in event:
        # Source literals (the source is not committed): short numbers only.
        event["seeds_detected"] = [v for v in event.get("seeds_detected") or []
                                   if _short_seed(v)]
    return event


def commit_skip(skip: dict[str, Any]) -> dict[str, Any]:
    """A skip has no file records: nothing in it is evidence (an unreadable
    script's path is only the word the command used)."""
    lr = _lr()
    skip = dict(skip)
    keep: set[str] = set()
    skip["bash_cmd"] = lr.commit_safe_command(skip.get("bash_cmd"), keep)
    skip["detail"] = lr.commit_safe_prose(skip.get("detail"), keep)
    return skip


def commit_data_command(record: dict[str, Any]) -> dict[str, Any]:
    lr = _lr()
    record = dict(record)
    for direction in ("inputs", "outputs"):
        if direction in record:
            record[direction] = _commit_file_records(record.get(direction))
    # A path shape is not proof (`data/<token>` is shaped like one), and
    # neither is a check on disk at Stop (it can hang on a dead mount): a
    # named word is kept only if it is exactly one of the record's own
    # evidence-backed file records.
    evidence = lr.commit_keep_paths(
        [*_evidence_paths(record.get("inputs")), *_evidence_paths(record.get("outputs"))])
    record["named_paths"] = _commit_word_list(record.get("named_paths"), evidence)
    record["analysis_candidates"] = _commit_word_list(
        record.get("analysis_candidates"), evidence, ("(inline)",))
    # The cwd and repository root are host-supplied structured fields the
    # record publishes anyway; their spellings may stay in the command.
    roots = [record[k] for k in ("cwd", "repo_root") if isinstance(record.get(k), str)]
    keep = evidence | lr.commit_keep_paths(roots)
    record["bash_cmd"] = lr.commit_safe_command(record.get("bash_cmd"), keep)
    if isinstance(record.get("step"), dict):
        record["step"] = _commit_step(record["step"], keep)
    return record


def commit_observation(observation: Any) -> Any:
    lr = _lr()
    if not isinstance(observation, dict) or not isinstance(observation.get("bash_cmd"), str):
        return observation
    # An observation's change entries are evidence: the walk saw each file.
    paths: list[str] = []
    root = observation.get("repo_root")
    changes = observation.get("changes")
    if isinstance(changes, dict):
        for kind in ("created", "modified", "deleted"):
            for entry in changes.get(kind) or []:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    paths.append(entry["path"])
                    if isinstance(root, str):
                        paths.append(os.path.join(root, entry["path"]))
    keep = lr.commit_keep_paths(paths)
    return {**observation, "bash_cmd": lr.commit_safe_command(observation["bash_cmd"], keep)}


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
        "bash_cmd": _redact(raw.get("bash_cmd")),
        "bash_exit": raw.get("bash_exit"),
        "bash_wall_s": raw.get("bash_wall_s"),
        "script": raw.get("script"),
        "script_sha256": raw.get("script_sha256"),
        "script_source": raw.get("script_source"),
        "script_candidate": raw.get("script_candidate"),
        "git_sha": raw.get("git_sha"),
        "inputs": [enrich_file_record(r) for r in (raw.get("inputs") or [])],
        "outputs": [enrich_file_record(r) for r in (raw.get("outputs") or [])],
        "io_detection": raw.get("io_detection")
        or ("static" if raw.get("inputs") or raw.get("outputs") else "unresolved"),
        "lineage_warnings": list(raw.get("lineage_warnings") or []),
        "filters_detected": list(raw.get("filters_detected") or []),
        "seeds_detected": list(raw.get("seeds_detected") or []),
        **_observation_link(raw),
        **_identity(raw),
    }
    if "filters_detected" not in raw:
        # v2 recorders no longer collect it (unevaluated regex text); a
        # historical record keeps whatever it had.
        del event["filters_detected"]
    if isinstance(raw.get("step"), dict):
        event["step"] = raw["step"]
    if raw.get("script_sha256_of") == "sanitized":
        # The digest is of the redacted source: the label must travel with it.
        event["script_sha256_of"] = "sanitized"
    if isinstance(raw.get("stages_not_recorded"), int):
        event["stages_not_recorded"] = raw["stages_not_recorded"]

    if event["script"] and event["script_sha256"] is None:
        script_path = Path(event["script"])
        _digest_script_file(event, script_path)
    _sanitize_embedded_source(event)

    return event


_SHELL_SOURCE_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh")
_SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})


def _shell_shebang(text: str) -> bool:
    """`#!/bin/bash`, `#!/usr/bin/env -S zsh`, … (an sbatch job file)."""
    if not text.startswith("#!"):
        return False
    words = text[2:].split("\n", 1)[0].split()
    if not words:
        return False
    names = [os.path.basename(words[0])]
    if names[0] == "env":
        names = [w for w in words[1:] if not w.startswith("-")][:1]
    return bool(names) and names[0] in _SHELL_NAMES


def _redact_script_text(script: Any, text: str) -> str:
    """A shell script's body (`run.sh`) takes the command redaction, since
    `export HF_TOKEN=…` is a shell word, not a quoted literal; any other
    script the source-literal one. The extractor applies the same rule."""
    import lineage_redact

    if (isinstance(script, str) and script.endswith(_SHELL_SOURCE_SUFFIXES)) or _shell_shebang(
            text):
        return lineage_redact.redact_command(text)
    return lineage_redact.redact_source(text)


def _sanitize_embedded_source(event: dict) -> None:
    """A pre-v2 event (recorded by 0.9.7, finalized after an upgrade) still
    carries its raw source and digest: redact it here, and hash and label
    the sanitized text, so no secret or digest of one is published."""
    source = event.get("script_source")
    if not isinstance(source, str):
        return
    sanitized = _redact_script_text(event.get("script"), source)
    if sanitized != source:
        event["script_source"] = sanitized
        event["script_sha256"] = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        event["script_sha256_of"] = "sanitized"


def _digest_script_file(event: dict, script_path: Path) -> None:
    """A digest recomputed from the file, under the same rule as embedded
    source: one bounded read from a descriptor proven to be a regular file
    (a FIFO or device never blocks Stop), and a sanitized, labelled digest
    when the text holds a secret."""
    try:
        fd = _open_regular_fd(script_path)
    except OSError:
        return
    if fd is None:
        return
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(SIZE_LIMIT_BYTES + 1)
    except OSError:
        return
    if len(data) > SIZE_LIMIT_BYTES:
        return
    text = data.decode("utf-8", errors="replace")
    sanitized = _redact_script_text(event.get("script"), text)
    if sanitized != text:
        event["script_sha256"] = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
        event["script_sha256_of"] = "sanitized"
    else:
        event["script_sha256"] = hashlib.sha256(data).hexdigest()


def normalize_skip(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep a skip record's known fields; it is evidence of a gap, not I/O."""
    return {
        "ts": raw.get("ts"),
        "skip_id": raw.get("skip_id"),
        "skip_reason": raw.get("skip_reason") or "unknown",
        # The call the skip belongs to, when known: the index reconciles a
        # skip with that call's observation instead of counting a gap.
        **({"observation_id": raw["observation_id"]}
           if isinstance(raw.get("observation_id"), str) else {}),
        "detail": raw.get("detail"),
        "bash_cmd": _redact(raw.get("bash_cmd")),
        "bash_exit": raw.get("bash_exit"),
        "agent_id": raw.get("agent_id"),
        "agent_type": raw.get("agent_type"),
        **_identity(raw, minted=False),
    }


def normalize_data_command(raw: dict[str, Any]) -> dict[str, Any]:
    """Keep a data command's known fields. Its file records were taken at
    hook time; a hash is filled only where none was recorded, and a path
    recorded as missing stays missing."""
    record = {
        "ts": raw.get("ts"),
        "event_id": raw.get("event_id"),
        "agent_id": raw.get("agent_id"),
        "agent_type": raw.get("agent_type"),
        "bash_cmd": _redact(raw.get("bash_cmd")),
        "bash_exit": raw.get("bash_exit"),
        "cwd": raw.get("cwd"),
        "signals": list(raw.get("signals") or []),
        "named_paths": list(raw.get("named_paths") or []),
        "named_paths_truncated": bool(raw.get("named_paths_truncated")),
        "analysis_candidates": list(raw.get("analysis_candidates") or []),
        "inputs": [enrich_file_record(r) for r in (raw.get("inputs") or [])],
        "outputs": [enrich_file_record(r) for r in (raw.get("outputs") or [])],
        "io_detection": "unresolved",
        **_observation_link(raw),
        **_identity(raw, minted=False),
    }
    if isinstance(raw.get("step"), dict):
        record["step"] = raw["step"]
    return record


# v2 identity and frame fields. A field the record does not carry is left
# out rather than written as null, so a v1 record re-consolidated into a v2
# manifest still reads as v1 (no identity), never as "no host".
_IDENTITY_FIELDS = ("host", "host_session_id", "ts_ns", "repo_root")


def _identity(raw: dict[str, Any], *, minted: bool = True) -> dict[str, Any]:
    out = {key: raw[key] for key in _IDENTITY_FIELDS if key in raw}
    if minted and isinstance(raw.get("event_id"), str):
        out["event_id"] = raw["event_id"]
    return out


def _observation_link(raw: dict[str, Any]) -> dict[str, Any]:
    """The record's observation link: null (not observed) when absent."""
    link = {key: raw.get(key) for key in _OBSERVATION_FIELDS[:3]}
    if raw.get("observation_reason"):
        link["observation_reason"] = raw["observation_reason"]
    if raw.get("tool_outcome") in ("success", "failure", "unknown"):
        link["tool_outcome"] = raw["tool_outcome"]
    return link


def _safe_component(value: str) -> str:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import io_observer

    return io_observer._safe_component(value)


def _read_store(path: Path) -> list[dict[str, Any]]:
    """Records of one observation store, never through a symlink."""
    import os
    import stat as stat_module

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        if not stat_module.S_ISREG(os.fstat(fd).st_mode):
            return []
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read()
    finally:
        if fd >= 0:
            os.close(fd)
    records = []
    for line in raw.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("kind") == "observation":
            records.append(value)
    return records


def _store_path_safe(obs_dir: Path, path: Path) -> bool:
    """Every directory from the observation root down is a real directory."""
    current = obs_dir
    for part in path.relative_to(obs_dir).parts[:-1]:
        current = current / part
        if current.is_symlink() or not current.is_dir():
            return False
    return not obs_dir.is_symlink()


def _stand_in_claim(path: Path) -> str | None:
    """The manifest session that claimed an unresolved-call stand-in."""
    import os
    import stat as stat_module

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat_module.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = json.loads(os.read(fd, 4096) or b"{}")
    except (OSError, ValueError):
        return None
    finally:
        os.close(fd)
    claimed = data.get("claimed_by") if isinstance(data, dict) else None
    return claimed if isinstance(claimed, str) else None


def claim_stand_ins(paths: list[Path], session_id: str) -> None:
    """Mark stand-ins as listed by this manifest (atomic, never through a
    symlink); a later transaction then skips them."""
    import os
    import tempfile

    for path in paths:
        try:
            if path.is_symlink() or not path.is_file():
                continue
            data = {"call": path.name[: -len(".unresolved")], "claimed_by": session_id}
            fd, temp = tempfile.mkstemp(prefix=".unresolved.", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                os.replace(temp, path)
            except BaseException:
                try:
                    os.unlink(temp)
                except OSError:
                    pass
                raise
        except OSError:
            continue


def join_observations(
    records: list[dict[str, Any]],
    obs_dir: Path,
    repo_root: str,
    warnings: list[str],
    consume: list[Path] | None = None,
    session_id: str | None = None,
    claim_aliases: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Load each observation the records reference from its session store.

    A real observation is preferred over a swept `call-unresolved` record
    with the same id. Referenced but absent observations are returned as
    missing (and warned about), never silently dropped.
    """
    wanted: dict[tuple[str, str], set[str]] = {}
    for record in records:
        observation_id = record.get("observation_id")
        session = record.get("observation_session")
        if not isinstance(observation_id, str) or ":" not in observation_id:
            continue
        if not isinstance(session, str) or not session:
            continue
        host = observation_id.split(":", 1)[0]
        wanted.setdefault((host, session), set()).add(observation_id)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for (host, session), ids in wanted.items():
        path = obs_dir / _safe_component(host) / f"{_safe_component(session)}.ndjson"
        if not _store_path_safe(obs_dir, path):
            continue
        for observation in _read_store(path):
            observation_id = observation.get("observation_id")
            if observation_id not in ids:
                continue
            key = (session, observation_id)
            previous = found.get(key)
            if previous is None or previous.get("reason") == "call-unresolved":
                found[key] = observation
    referenced = sorted({(session, i) for (_, session), ids in wanted.items() for i in ids})
    missing = [i for key in referenced for i in [key[1]] if key not in found]
    # Calls of these host sessions whose Pre snapshot no Post has claimed
    # yet: still running (a Codex terminal not yet polled to completion), or
    # never completed. Their file changes are unknown so far.
    unresolved: list[str] = []
    consumable: list[Path] = []
    pre_dir = obs_dir.parent / "pre"
    for host, session in wanted:
        session_dir = pre_dir / _safe_component(host) / _safe_component(session)
        if session_dir.is_symlink() or not session_dir.is_dir():
            continue
        for entry in sorted(session_dir.iterdir()):
            if entry.is_symlink() or not entry.is_file():
                continue
            if entry.name.endswith(".snap"):
                unresolved.append(f"{host}:{entry.name[: -len('.snap')]}")
            elif entry.name.endswith(".unresolved"):
                # A call the turn ended without (turn_end). The first
                # manifest to list it claims it; a rewrite of that same
                # manifest (a blocked Stop's retry) lists it again, a later
                # transaction's manifest does not. The sweep removes it.
                claimed = _stand_in_claim(entry)
                if claimed is not None and claimed not in (session_id, *claim_aliases):
                    continue
                unresolved.append(f"{host}:{entry.name[: -len('.unresolved')]}")
                consumable.append(entry)
    if unresolved:
        warnings.append(
            f"{len(unresolved)} call(s) of this session have not completed an "
            "observation yet (still running or never reported); their file "
            "changes are unknown."
        )
    if missing:
        warnings.append(
            f"{len(missing)} referenced observation(s) were not found in the "
            "observation store; treat those calls' file changes as unknown."
        )
    _mark_observed_changes(records, found, repo_root)
    if consume is not None:
        consume.extend(consumable)
    return [found[k] for k in referenced if k in found], missing, unresolved


def _mark_observed_changes(
    records: list[dict[str, Any]], found: dict[Any, dict[str, Any]], repo_root: str
) -> None:
    """Say, per inferred output, whether the call's observation saw it change.

    Only for an observation that has a change list; an unavailable one
    claims nothing. The observation covers the whole call, so for a command
    running several scripts the mark says the call changed the path, not
    which script did.
    """
    import os

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import io_observer

    root = os.path.realpath(repo_root)
    for record in records:
        observation = found.get((record.get("observation_session"), record.get("observation_id")))
        if observation is None:
            observation = found.get(record.get("observation_id"))  # a caller keyed by id
        changes = observation.get("changes") if observation else None
        if not isinstance(changes, dict):
            continue
        scope = observation.get("scope") if isinstance(observation.get("scope"), dict) else {}
        unwatched = [u for u in scope.get("unwatched") or [] if isinstance(u, dict)]
        kinds: dict[str, str] = {}
        after: dict[str, dict[str, Any]] = {}
        for kind in ("created", "modified", "deleted"):
            for entry in changes.get(kind) or []:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    kinds[entry["path"]] = kind
                    if isinstance(entry.get("after"), dict):
                        after[entry["path"]] = entry["after"]
        for output in record.get("outputs") or []:
            path = output.get("path")
            if not isinstance(path, str):
                continue
            if "rel_path" in output:
                # A v2 record's own frame, set at hook time with the
                # observer's rules; an explicit null means it could not be
                # framed, so no observation entry is matched to it.
                rel = output["rel_path"]
                if not isinstance(rel, str):
                    continue
                _mark_one(output, rel, kinds, after, changes, unwatched, io_observer)
                continue
            try:
                # Lexically first: a symlinked directory inside the
                # repository (results -> scratch) is an unwatched part of
                # the repository, not a path outside it.
                rel = os.path.relpath(os.path.abspath(path), os.path.abspath(repo_root))
                if rel == ".." or rel.startswith("../"):
                    rel = os.path.relpath(os.path.realpath(path), root)
            except ValueError:
                continue
            _mark_one(output, rel, kinds, after, changes, unwatched, io_observer)


def _mark_one(
    output: dict[str, Any],
    rel: str,
    kinds: dict[str, str],
    after: dict[str, dict[str, Any]],
    changes: dict[str, Any],
    unwatched: list[dict[str, Any]],
    io_observer: Any,
) -> None:
    if rel in kinds:
        output["observed_change"] = kinds[rel]
        side = after.get(rel)
        if side is not None:
            # The protocol hash of the bytes the call left, taken only when
            # the file was provably stable; null (with its status) when it
            # was not. Never the hook digest.
            output["observed_sha256"] = side.get("sha256")
            output["observed_hash_status"] = side.get("hash_status")
        return
    # A path whose coverage changed during the call (the scope file moved)
    # could have been rewritten unseen.
    moved = changes.get("coverage_changed") or []
    if rel in moved or changes.get("coverage_changed_truncated"):
        output["observed_change"] = "unknown-coverage"
        return
    # Absent from the change list means "unchanged" only where the
    # observation could have seen a change.
    status = io_observer.watch_status(rel, unwatched)
    racy = changes.get("racy_unknown") or []
    racy_count = (changes.get("counts") or {}).get("racy_unknown", 0)
    if status != "watched":
        output["observed_change"] = status
    elif rel in racy:
        # A coarse-timestamp identity the observer could not prove
        # unchanged: a same-size rewrite in the same tick hides.
        output["observed_change"] = "unknown-racy"
    elif changes.get("capped") or (isinstance(racy_count, int) and racy_count > len(racy)):
        output["observed_change"] = "unknown-capped"
    else:
        output["observed_change"] = "unchanged"


def build_manifest(
    events: list[dict[str, Any]],
    session_id: str,
    repo_root: str,
    warnings: list[str],
    skipped: list[dict[str, Any]] | None = None,
    data_commands: list[dict[str, Any]] | None = None,
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
    # Hosts may legitimately omit wall time and exit status (the current Codex
    # Bash PostToolUse payload carries neither). Unknown telemetry must stay
    # unknown: totals cover only timed actions, coverage counters make partial
    # telemetry explicit, and an unknown exit is neither success nor failure.
    total_wall = 0.0
    timed_action_count = 0
    known_exit_count = 0
    failed_action_count = 0
    for e in sorted_events:
        # Prefer the script path; fall back to a label for inline `-c` / `-e`
        # invocations so the summary records every action, not just file-backed
        # scripts. Inline label uses the script SHA so distinct inline snippets
        # show as distinct entries; an R run whose script is uncertain shows
        # the file the command names.
        entry = e["script"]
        if not entry and e.get("script_sha256"):
            entry = f"(inline {e['script_sha256'][:12]})"
        elif not entry and e.get("script_candidate"):
            entry = f"(unresolved {e['script_candidate']})"
        if entry and entry not in scripts_executed:
            scripts_executed.append(entry)
        wall = e.get("bash_wall_s")
        if isinstance(wall, (int, float)) and not isinstance(wall, bool):
            total_wall += float(wall)
            timed_action_count += 1
        exit_code = e.get("bash_exit")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool):
            known_exit_count += 1
            if exit_code != 0:
                failed_action_count += 1
        for warning in e.get("lineage_warnings", []):
            label = e.get("script") or e.get("bash_cmd") or f"event at {e['ts']}"
            rendered = f"{label}: {warning}"
            if rendered not in warnings:
                warnings.append(rendered)
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
            "total_wall_seconds": (
                int(round(total_wall)) if timed_action_count else None
            ),
            "timed_action_count": timed_action_count,
            "total_action_count": len(sorted_events),
            "known_exit_count": known_exit_count,
            "failed_action_count": failed_action_count,
            "scripts_executed": scripts_executed,
            "skipped_count": len(skipped or []),
            "data_command_count": len(data_commands or []),
        },
        "skipped": sorted(skipped or [], key=lambda s: str(s.get("ts") or "")),
        "data_commands": sorted(
            data_commands or [], key=lambda d: str(d.get("ts") or "")
        ),
        "extraction_warnings": warnings,
    }


# The manifest's record shape. Absent means v1 (0.9.7 and earlier): no
# event identity, no path frame, no step settings.
MANIFEST_SCHEMA = "mycelium.data_lineage.manifest.v2"


def write_manifest_atomic(path: Path, manifest: dict[str, Any]) -> None:
    """Publish the manifest whole: a reader (the lineage index, another
    session's query) sees the old file or the new one, never a torn one."""
    import os
    import stat
    import tempfile

    try:
        # An existing manifest keeps its mode (a private 0600 stays private);
        # a new one gets what a plain create would under the umask.
        mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(manifest, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def extract(
    events_file: Path,
    session_id: str,
    repo_root: str,
    observations_dir: Path | None = None,
    consume: list[Path] | None = None,
    claim_aliases: tuple[str, ...] = (),
) -> dict[str, Any]:
    """End-to-end: read NDJSON events, normalize, build manifest.

    ``claim_aliases`` are earlier manifest ids of this same transaction (a
    data-only manifest this one replaces): their claims count as this one's.

    ``consume`` receives the unresolved-call stand-ins this manifest lists;
    the caller marks them claimed by this manifest only after it is written."""
    warnings: list[str] = []
    events: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_skip_ids: set[str] = set()
    data_commands: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
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
            if isinstance(raw, dict) and raw.get("kind") == "skip":
                # A retried transfer can repeat a record; count each skip_id once.
                skip_id = raw.get("skip_id")
                if skip_id and skip_id in seen_skip_ids:
                    continue
                if skip_id:
                    seen_skip_ids.add(skip_id)
                skipped.append(normalize_skip(raw))
                continue
            if isinstance(raw, dict) and raw.get("kind") == "data_command":
                event_id = raw.get("event_id")
                if event_id and event_id in seen_event_ids:
                    continue
                if event_id:
                    seen_event_ids.add(event_id)
                data_commands.append(normalize_data_command(raw))
                continue
            normalized = normalize_event(raw, warnings, idx)
            if normalized is not None:
                # v2 events carry an id: a record repeated by a retried
                # transfer counts once. v1 events have none and are kept.
                event_id = normalized.get("event_id")
                if event_id and event_id in seen_event_ids:
                    continue
                if event_id:
                    seen_event_ids.add(event_id)
                events.append(normalized)
    else:
        warnings.append(f"events file not found: {events_file}")
    if observations_dir is None:
        observations_dir = Path(repo_root) / ".mycelium" / "io" / "obs"
    observations, missing, unresolved = join_observations(
        [*events, *data_commands], observations_dir, repo_root, warnings, consume, session_id,
        claim_aliases,
    )
    # The manifest is committed: every record's text goes through the
    # allowlist (after the joins above, which read only paths and ids).
    events = [commit_event(e) for e in events]
    skipped = [commit_skip(s) for s in skipped]
    data_commands = [commit_data_command(d) for d in data_commands]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "text_policy": TEXT_POLICY,
        **build_manifest(events, session_id, repo_root, warnings, skipped, data_commands),
    }
    manifest["observations"] = [commit_observation(_redact_observation(o))
                                for o in observations]
    counts: dict[str, int] = {}
    for observation in observations:
        status = str(observation.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    manifest["summary"]["observation_counts"] = counts
    manifest["summary"]["observations_missing"] = missing
    manifest["summary"]["unresolved_calls"] = unresolved
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--events-file", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--repo-root", required=True)
    ap.add_argument(
        "--observations-dir",
        type=Path,
        default=None,
        help="Observation stores (default: <repo-root>/.mycelium/io/obs).",
    )
    ap.add_argument(
        "--replaces",
        action="append",
        default=[],
        help="An earlier manifest id of this transaction that this manifest "
        "replaces; its claims on unresolved calls carry over.",
    )
    args = ap.parse_args()

    consumed: list[Path] = []
    manifest = extract(
        args.events_file, args.session_id, args.repo_root, args.observations_dir, consumed,
        tuple(args.replaces),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_manifest_atomic(args.output, manifest)
    # Listed in a written manifest: claimed by it, so a blocked Stop's retry
    # (which rewrites this manifest) still lists them and a later
    # transaction does not report them again.
    claim_stand_ins(consumed, args.session_id)
    print(
        f"extract_data_lineage: wrote {args.output} "
        f"({manifest['n_actions']} actions, "
        f"{len(manifest['summary']['unique_inputs'])} inputs, "
        f"{len(manifest['summary']['unique_outputs'])} outputs, "
        f"{len(manifest['extraction_warnings'])} warnings, "
        f"{manifest['summary']['skipped_count']} skipped, "
        f"{manifest['summary']['data_command_count']} data commands)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
