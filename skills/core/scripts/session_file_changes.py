#!/usr/bin/env python3
"""Snapshot and report repository changes made during one Mycelium session.

The Stop hook cannot treat the current ``git status`` as a session delta: a
repository may already contain uncommitted or untracked work when Codex starts.
SessionStart therefore records the dirty-state metadata and HEAD revision, and
Stop compares the current state against that baseline.  Explicit activity paths
are included even when the file was subsequently removed (for example, a
create/delete ``apply_patch`` probe).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def _run_git(repo_root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
    )
    return result.stdout if result.returncode == 0 else b""


def _head(repo_root: Path) -> str | None:
    value = _run_git(repo_root, "rev-parse", "--verify", "HEAD").decode(
        "ascii", errors="ignore"
    ).strip()
    return value or None


def _stat_fingerprint(repo_root: Path, relative_path: str) -> dict[str, int] | None:
    try:
        stat = (repo_root / relative_path).lstat()
    except OSError:
        return None
    return {
        "mode": stat.st_mode,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _decode_path(raw: bytes) -> str:
    return os.fsdecode(raw)


def read_worktree_state(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Return porcelain status plus file metadata, keyed by repository path."""
    payload = _run_git(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    fields = payload.split(b"\0")
    entries: dict[str, dict[str, Any]] = {}
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record or len(record) < 4:
            continue
        status = record[:2].decode("ascii", errors="replace")
        path = _decode_path(record[3:])
        previous_path = None
        if "R" in status or "C" in status:
            if index < len(fields) and fields[index]:
                previous_path = _decode_path(fields[index])
                index += 1
        entries[path] = {
            "status": status,
            "previous_path": previous_path,
            "stat": _stat_fingerprint(repo_root, path),
        }
    return entries


def build_snapshot(repo_root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "head": _head(repo_root),
        "files": read_worktree_state(repo_root),
    }


def write_snapshot(repo_root: Path, output: Path) -> None:
    payload = build_snapshot(repo_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_name, output)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def load_snapshot(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") != SCHEMA_VERSION:
        return None
    if not isinstance(value.get("files"), dict):
        return None
    return value


def _normalize_repo_path(repo_root: Path, raw_path: str) -> str | None:
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    try:
        return candidate.resolve(strict=False).relative_to(
            repo_root.resolve(strict=False)
        ).as_posix()
    except (OSError, ValueError):
        return None


def _activity_paths(repo_root: Path, activity_file: Path | None) -> set[str]:
    if activity_file is None:
        return set()
    try:
        lines = activity_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    return {
        normalized
        for line in lines
        if (normalized := _normalize_repo_path(repo_root, line)) is not None
    }


def _committed_paths(
    repo_root: Path, baseline_head: str | None, start_ts: int | None
) -> set[str]:
    current_head = _head(repo_root)
    if current_head is None or current_head == baseline_head:
        return set()
    if baseline_head:
        log_args = ["log", f"{baseline_head}..{current_head}"]
        if start_ts is not None:
            # A HEAD transition may be only a checkout of an existing branch.
            # Restrict the range to commits created during this session so the
            # destination branch's older history is not reported as new work.
            log_args.append(f"--since=@{start_ts}")
        log_args.extend(("--name-only", "-z", "--pretty=format:"))
        payload = _run_git(repo_root, *log_args)
    elif start_ts is not None:
        payload = _run_git(
            repo_root,
            "log",
            f"--since=@{start_ts}",
            "--name-only",
            "-z",
            "--pretty=format:",
        )
    else:
        payload = _run_git(repo_root, "ls-tree", "-r", "--name-only", "-z", current_head)
    return {_decode_path(raw) for raw in payload.split(b"\0") if raw}


def collect_changes(
    repo_root: Path,
    baseline: dict[str, Any] | None,
    activity_file: Path | None = None,
    start_ts: int | None = None,
    excludes: set[str] | None = None,
    exclude_prefixes: tuple[str, ...] = (".mycelium/",),
) -> list[str]:
    """Return a sorted, de-duplicated list of session-local paths."""
    excludes = excludes or set()
    current = read_worktree_state(repo_root)
    baseline_files = baseline.get("files", {}) if baseline else {}
    changed: set[str] = _activity_paths(repo_root, activity_file)

    if baseline is not None:
        for path in set(current) | set(baseline_files):
            if current.get(path) != baseline_files.get(path):
                changed.add(path)
    elif start_ts is not None:
        threshold_ns = start_ts * 1_000_000_000
        for path, entry in current.items():
            stat = entry.get("stat")
            if isinstance(stat, dict) and int(stat.get("mtime_ns", 0)) > threshold_ns:
                changed.add(path)

    changed.update(
        _committed_paths(
            repo_root,
            baseline.get("head") if baseline else None,
            start_ts,
        )
    )
    return sorted(
        path
        for path in changed
        if path
        and path not in excludes
        and not any(path.startswith(prefix) for prefix in exclude_prefixes)
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot")
    snapshot_parser.add_argument("--repo-root", type=Path, required=True)
    snapshot_parser.add_argument("--output", type=Path, required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--repo-root", type=Path, required=True)
    collect_parser.add_argument("--baseline", type=Path)
    collect_parser.add_argument("--activity-file", type=Path)
    collect_parser.add_argument("--start-ts", type=int)
    collect_parser.add_argument("--exclude", action="append", default=[])
    collect_parser.add_argument("--exclude-prefix", action="append", default=[])
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    repo_root = args.repo_root.resolve()
    if args.command == "snapshot":
        write_snapshot(repo_root, args.output)
        return 0

    baseline = load_snapshot(args.baseline)
    changes = collect_changes(
        repo_root,
        baseline,
        activity_file=args.activity_file,
        start_ts=args.start_ts,
        excludes=set(args.exclude),
        exclude_prefixes=(".mycelium/", *args.exclude_prefix),
    )
    for path in changes:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
