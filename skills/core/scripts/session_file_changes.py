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
import hashlib
import json
import os
import stat as stat_module
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 3
# Schema 2 baselines (a content hash for every dirty and untracked file) are
# still read, so a transaction opened by the previous runtime keeps its precise
# accounting after an upgrade. Schema 3 records metadata for every path and a
# content hash only for a bounded, deterministic subset; earlier runtimes reject
# it and use their documented timestamp fallback, which keeps rollback safe.
SUPPORTED_SCHEMA_VERSIONS = (2, 3)
# A path whose metadata changed this close to the snapshot may change again
# inside one timestamp granule (HFS+ keeps whole seconds) without moving its
# ctime, so its content is fingerprinted rather than trusted to metadata.
RACY_MARGIN_NS = 2_000_000_000
# Reading every untracked byte made SessionStart and Stop exceed their host
# timeouts on large output trees. Content hashing is therefore bounded. A path
# whose ctime or inode moved and that cannot be verified within the bound is
# reported as changed (so a metadata-only touch of an unhashed file costs a
# redundant reminder); a write that moves no timestamp outside the fingerprinted
# set is not seen.
# Each budget can be overridden by the environment variable of the same name
# prefixed with MYCELIUM_ (a non-negative decimal integer of at most 18 digits).
SNAPSHOT_CONTENT_BUDGET_FILES = 20_000
SNAPSHOT_CONTENT_BUDGET_BYTES = 256 * 1024 * 1024
# Stop re-reads every fingerprinted file it can afford, not only the ones whose
# metadata moved: some writes move no timestamp at all (FAT and exFAT mirror
# ctime from mtime; shared mmap stores may never touch either). Keeping these
# at least as large as the snapshot budgets lets Stop re-verify the whole
# fingerprinted set.
COLLECT_CONTENT_BUDGET_FILES = 20_000
COLLECT_CONTENT_BUDGET_BYTES = 256 * 1024 * 1024
LIVING_FILES = ("learnings.md", "decisions.md", "conventions.md")


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


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def _metadata(repo_root: Path, relative_path: str) -> dict[str, Any] | None:
    """Return the cheap identity of one path; reading its bytes is separate.

    ``utime`` can restore mtime after a same-size rewrite, but on ordinary
    filesystems nothing short of changing the clock restores ctime, and an
    atomic replacement changes the inode. Those two fields let unchanged files
    be proven unchanged without reading them. Two kinds of write move neither
    field: FAT and exFAT report ctime as a copy of mtime, and stores through a
    shared mmap may never update either timestamp. Stop therefore also
    re-reads the fingerprinted files it can afford; outside that set such a
    write goes unseen.
    """
    try:
        stat = (repo_root / relative_path).lstat()
    except OSError:
        return None
    return {
        "mode": stat.st_mode,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "ctime_ns": stat.st_ctime_ns,
        "ino": stat.st_ino,
    }


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    # The length cap keeps every interpreter on the same answer: some have no
    # limit on int() digits, others raise ValueError past 4300 (3.11+, and the
    # 3.9.14 / 3.10.7 security releases).
    if raw is None or not (raw.isascii() and raw.isdigit()) or len(raw) > 18:
        return default
    return int(raw)


def _decode_path(raw: bytes) -> str:
    return os.fsdecode(raw)


def _decode_paths(payload: bytes) -> set[str]:
    return {_decode_path(raw) for raw in payload.split(b"\0") if raw}


def _head_reflog(repo_root: Path) -> list[tuple[str, str]] | None:
    exists = subprocess.run(
        ["git", "-C", str(repo_root), "reflog", "exists", "HEAD"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if exists.returncode != 0:
        return None
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "reflog",
            "show",
            "--format=%H%x00%gs",
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        return None
    payload = result.stdout
    records: list[tuple[str, str]] = []
    for record in payload.splitlines():
        raw_oid, separator, raw_action = record.partition(b"\0")
        oid = raw_oid.decode("ascii", errors="ignore").strip()
        if separator and oid:
            records.append((oid, os.fsdecode(raw_action)))
    return records


def _session_reflog_change_paths(
    repo_root: Path,
    baseline_head: str,
    baseline_reflog_entries: int,
) -> set[str] | None:
    """Replay content-producing HEAD transitions after the snapshot.

    Reflog entries are newest-first. Comparing each commit-like transition to
    the HEAD immediately before it captures the actual session delta: an amend
    excludes unchanged historical paths, while a normal commit that restores
    baseline content remains visible. Checkout and rebase bookkeeping update
    the prior HEAD but do not themselves claim historical paths as session work;
    content-producing rebase actions do.
    ``None`` asks callers to use the legacy timestamp fallback when the reflog
    was pruned or cannot be aligned with the snapshot.
    """
    records = _head_reflog(repo_root)
    if records is None:
        return None
    if len(records) < baseline_reflog_entries:
        return None
    new_entry_count = len(records) - baseline_reflog_entries
    session_records = list(reversed(records[:new_entry_count]))
    previous_head = baseline_head
    changed: set[str] = set()
    content_actions = (
        "commit:",
        "commit (initial):",
        "commit (amend):",
        "commit (merge):",
        "merge ",
        "merge:",
        "cherry-pick:",
        "revert:",
        "rebase (pick):",
        "rebase (reword):",
        "rebase (edit):",
        "rebase (squash):",
        "rebase (fixup):",
        "rebase (continue):",
    )
    for current_head, action in session_records:
        if action.startswith(content_actions):
            changed.update(
                _decode_paths(
                    _run_git(
                        repo_root,
                        "diff",
                        "--name-only",
                        "-z",
                        previous_head,
                        current_head,
                    )
                )
            )
        previous_head = current_head
    return changed


def read_worktree_state(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Return porcelain status plus file metadata, keyed by repository path.

    No file content is read here; see ``_record_snapshot_content`` and
    ``_ContentComparator`` for the bounded content checks.
    """
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
            "stat": _metadata(repo_root, path),
        }
    return entries


def _is_racy(stat: dict[str, Any], reference_ns: int, margin_ns: int) -> bool:
    # ctime only: mtime can be set to any value, including the future, and a
    # future mtime would otherwise keep a path racy at every Stop.
    return stat["ctime_ns"] >= reference_ns - margin_ns


def _content_priority(entry: dict[str, Any], racy: bool) -> tuple[int, int]:
    """Order paths for a content budget: tracked dirty, racy, then newest."""
    stat = entry["stat"]
    if entry.get("status") != "??":
        rank = 0
    elif racy:
        rank = 1
    else:
        rank = 2
    return rank, -stat["mtime_ns"]


def _record_snapshot_content(
    repo_root: Path,
    entries: dict[str, dict[str, Any]],
    reference_ns: int,
) -> None:
    """Attach content fingerprints to a bounded, deterministic set of paths.

    Symlinks and non-regular paths are fingerprinted without reading. Regular
    files are hashed in priority order until the file or byte budget runs out:
    tracked dirty files, then racy files, then the most recently modified
    untracked files. Every other path stores ``content: None``; Stop proves it
    unchanged from metadata alone or, when its metadata moved or it was racy
    at the snapshot, counts it as changed because its content cannot be
    verified.
    """
    margin_ns = RACY_MARGIN_NS
    candidates: list[tuple[tuple[int, int], str]] = []
    for path, entry in entries.items():
        stat = entry["stat"]
        if stat is None:
            continue
        stat["content"] = None
        if not stat_module.S_ISREG(stat["mode"]):
            stat["content"] = _content_fingerprint(repo_root / path, scanned=stat)
            continue
        racy = _is_racy(stat, reference_ns, margin_ns)
        candidates.append((_content_priority(entry, racy), path))

    files_left = _env_int(
        "MYCELIUM_SNAPSHOT_CONTENT_BUDGET_FILES", SNAPSHOT_CONTENT_BUDGET_FILES
    )
    bytes_left = _env_int(
        "MYCELIUM_SNAPSHOT_CONTENT_BUDGET_BYTES", SNAPSHOT_CONTENT_BUDGET_BYTES
    )
    for _, path in sorted(candidates):
        stat = entries[path]["stat"]
        if files_left <= 0 or stat["size"] > bytes_left:
            continue
        files_left -= 1
        bytes_left -= stat["size"]
        stat["content"] = _content_fingerprint(repo_root / path, scanned=stat)


_CHANGED = "changed"
_UNCHANGED = "unchanged"
_MUST_VERIFY = "must-verify"
_MAY_VERIFY = "may-verify"


class _ContentComparator:
    """Decide which paths differ from their baseline, reading a bounded amount.

    ``classify`` uses metadata only. A path whose ctime or inode moved (or that
    was racy at the snapshot) must have its content verified; past the budget
    it counts as changed. Every other fingerprinted regular file may be
    verified while budget remains, which catches writes that move no timestamp;
    past the budget it is trusted to its metadata. Reads happen in a fixed
    order so consecutive Stops verify the same files.

    Any malformed or unrecognised baseline value counts as changed: an
    exception here would surface as an empty change list in the Stop hook,
    which reads as "nothing happened".
    """

    def __init__(
        self,
        repo_root: Path,
        baseline: dict[str, Any],
        start_ts: int | None,
        legacy_reference_ns: int | None,
    ) -> None:
        self.repo_root = repo_root
        self.version = baseline.get("schema_version")
        self.margin_ns = RACY_MARGIN_NS
        self.bytes_left = _env_int(
            "MYCELIUM_COLLECT_CONTENT_BUDGET_BYTES", COLLECT_CONTENT_BUDGET_BYTES
        )
        self.files_left = _env_int(
            "MYCELIUM_COLLECT_CONTENT_BUDGET_FILES", COLLECT_CONTENT_BUDGET_FILES
        )
        self.snapshot_ns = baseline.get("snapshot_started_ns")
        # Schema 2 records no snapshot time. The session start (written before
        # SessionStart took the snapshot) bounds it from below; the baseline
        # file's own write time is the fallback when no start is known.
        self.legacy_threshold_ns: int | None = None
        if _is_int(start_ts):
            self.legacy_threshold_ns = start_ts * 1_000_000_000 - self.margin_ns
        elif _is_int(legacy_reference_ns):
            self.legacy_threshold_ns = legacy_reference_ns - self.margin_ns

    def classify(self, before: Any, after: dict[str, Any] | None) -> str:
        try:
            return self._classify(before, after)
        except Exception:  # noqa: BLE001 - a decision must never abort Stop
            return _CHANGED

    def _classify(self, before: Any, after: dict[str, Any] | None) -> str:
        if before is None or after is None:
            return _UNCHANGED if before is after else _CHANGED
        if not isinstance(before, dict):
            return _CHANGED
        if before.get("status") != after["status"]:
            return _CHANGED
        if before.get("previous_path") != after["previous_path"]:
            return _CHANGED
        old = before.get("stat")
        new = after["stat"]
        if old is None or new is None:
            return _UNCHANGED if old is new else _CHANGED
        if not isinstance(old, dict):
            return _CHANGED
        for key in ("mode", "size", "mtime_ns"):
            if not _is_int(old.get(key)) or old[key] != new[key]:
                return _CHANGED
        if self.version == 3:
            if not _is_int(old.get("ctime_ns")) or not _is_int(old.get("ino")):
                return _CHANGED
            suspect = (
                old["ctime_ns"] != new["ctime_ns"]
                or old["ino"] != new["ino"]
                or _is_racy(old, self.snapshot_ns, self.margin_ns)
            )
        else:
            suspect = (
                self.legacy_threshold_ns is None
                or new["ctime_ns"] >= self.legacy_threshold_ns
            )
        if suspect:
            return _MUST_VERIFY
        content = old.get("content")
        if isinstance(content, dict) and content.get("kind") == "file":
            return _MAY_VERIFY
        return _UNCHANGED

    def content_differs(
        self, path: str, before: dict[str, Any], after: dict[str, Any], required: bool
    ) -> bool:
        try:
            return self._content_differs(path, before, after, required)
        except Exception:  # noqa: BLE001 - a decision must never abort Stop
            return True

    def _content_differs(
        self, path: str, before: dict[str, Any], after: dict[str, Any], required: bool
    ) -> bool:
        expected = before["stat"].get("content")
        metadata = after["stat"]
        if not isinstance(expected, dict):
            return required
        full = self.repo_root / path
        is_regular = stat_module.S_ISREG(metadata["mode"])
        if expected.get("kind") == "file":
            if not is_regular:
                return True
            if self.files_left <= 0 or metadata["size"] > self.bytes_left:
                return required
            self.files_left -= 1
            self.bytes_left -= metadata["size"]
        elif is_regular:
            return True
        return _content_fingerprint(full, scanned=metadata) != expected


def _content_fingerprint(
    path: Path, *, scanned: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Return a content identity that does not depend on timestamp precision."""
    try:
        stat = path.lstat()
    except OSError:
        return None
    # The caller charged the size of the scanned file against its budget. If
    # that file changed type, size, or identity in the meantime, leave it
    # unverified. A scanned non-regular path was charged no bytes or files.
    if scanned is not None and (
        stat.st_mode != scanned["mode"]
        or stat.st_size != scanned["size"]
        or stat.st_ino != scanned["ino"]
    ):
        return None
    if stat_module.S_ISLNK(stat.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            target = ""
        return {"kind": "symlink", "target": target}
    if not stat_module.S_ISREG(stat.st_mode):
        return {"kind": "other", "mode": stat.st_mode}
    # The path can be replaced between the checks above and the open. Open
    # without following links or blocking (a FIFO opened for reading with no
    # writer would otherwise wait forever), then hash only the regular file
    # that was inspected.
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        opened = os.fstat(fd)
        if (
            not stat_module.S_ISREG(opened.st_mode)
            or opened.st_ino != stat.st_ino
            or opened.st_dev != stat.st_dev
            or opened.st_size != stat.st_size
        ):
            return None
        # A writer can also append after fstat. Read at most the charged size
        # and discard the hash if the file changed during the read. Unbuffered
        # I/O keeps read-ahead from exceeding the limit.
        handle = os.fdopen(fd, "rb", buffering=0)
        fd = -1
        with handle:
            unread = opened.st_size
            while unread:
                chunk = handle.read(min(65536, unread))
                if not chunk:
                    return None
                digest.update(chunk)
                unread -= len(chunk)
            finished = os.fstat(handle.fileno())
            if (
                finished.st_size != opened.st_size
                or finished.st_mtime_ns != opened.st_mtime_ns
                or finished.st_ctime_ns != opened.st_ctime_ns
            ):
                return None
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    return {
        "kind": "file",
        "size": stat.st_size,
        "sha256": digest.hexdigest(),
    }


def read_living_state(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint lifecycle artifacts whose content satisfies Stop."""
    living_dir = repo_root / ".living"
    candidates = [living_dir / name for name in LIVING_FILES]
    findings_dir = living_dir / "findings"
    if findings_dir.is_dir() and not findings_dir.is_symlink():
        candidates.extend(path for path in findings_dir.rglob("*") if not path.is_dir())
    elif findings_dir.exists() or findings_dir.is_symlink():
        candidates.append(findings_dir)

    state: dict[str, dict[str, Any]] = {}
    for path in candidates:
        fingerprint = _content_fingerprint(path)
        if fingerprint is not None:
            state[path.relative_to(repo_root).as_posix()] = fingerprint
    return state


def build_snapshot(repo_root: Path) -> dict[str, Any]:
    # Taken before any path is observed, so every write that lands after a
    # path's metadata was read has a ctime at or beyond this reference.
    started_ns = time.time_ns()
    reflog = _head_reflog(repo_root)
    files = read_worktree_state(repo_root)
    _record_snapshot_content(repo_root, files, started_ns)
    return {
        "schema_version": SCHEMA_VERSION,
        "snapshot_started_ns": started_ns,
        "head": _head(repo_root),
        "head_reflog_entries": len(reflog) if reflog is not None else None,
        "files": files,
        "living_files": read_living_state(repo_root),
    }


def _write_payload(output: Path, payload: dict[str, Any]) -> None:
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


def write_snapshot(repo_root: Path, output: Path) -> None:
    _write_payload(output, build_snapshot(repo_root))


def write_living_snapshot(repo_root: Path, output: Path) -> None:
    """Write the minimal baseline needed for lifecycle-content comparison."""
    _write_payload(
        output,
        {
            "schema_version": SCHEMA_VERSION,
            "snapshot_started_ns": time.time_ns(),
            "files": {},
            "living_files": read_living_state(repo_root),
        },
    )


def load_snapshot(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    version = value.get("schema_version")
    if not _is_int(version) or version not in SUPPORTED_SCHEMA_VERSIONS:
        return None
    if version == 3:
        started = value.get("snapshot_started_ns")
        if not _is_int(started) or started < 0:
            return None
    if not isinstance(value.get("files"), dict):
        return None
    return value


def living_changed(repo_root: Path, baseline: dict[str, Any] | None) -> bool | None:
    """Return lifecycle-content change state, or None for an old baseline."""
    if baseline is None or not isinstance(baseline.get("living_files"), dict):
        return None
    return read_living_state(repo_root) != baseline["living_files"]


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
    repo_root: Path,
    baseline_head: str | None,
    baseline_reflog_entries: int | None,
    start_ts: int | None,
) -> set[str]:
    current_head = _head(repo_root)
    if current_head is None:
        return set()
    if baseline_head:
        is_descendant = _is_ancestor(repo_root, baseline_head, current_head)
        if (
            (current_head == baseline_head or not is_descendant)
            and isinstance(baseline_reflog_entries, int)
        ):
            reflog_paths = _session_reflog_change_paths(
                repo_root,
                baseline_head,
                baseline_reflog_entries,
            )
            if reflog_paths is not None:
                return reflog_paths

        if current_head == baseline_head:
            return set()

        log_args = ["log", f"{baseline_head}..{current_head}"]
        if start_ts is not None:
            # A HEAD transition may be only a checkout of an existing branch.
            # Restrict the range to commits created during this session so the
            # destination branch's older history is not reported as new work.
            log_args.append(f"--since=@{start_ts}")
        log_args.extend(("--name-only", "-z", "--pretty=format:"))
        committed = _decode_paths(_run_git(repo_root, *log_args))
        if not is_descendant:
            # Amend/rebase/branch-switch transitions rewrite ancestry. Commit
            # path lists can then contain unchanged historical files, while a
            # raw tree diff can contain an existing destination branch. Their
            # intersection retains only recent commits whose final tree content
            # actually differs from the session baseline.
            tree_delta = _decode_paths(
                _run_git(
                    repo_root,
                    "diff",
                    "--name-only",
                    "-z",
                    baseline_head,
                    current_head,
                )
            )
            committed.intersection_update(tree_delta)
        return committed
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
    return _decode_paths(payload)


def collect_changes(
    repo_root: Path,
    baseline: dict[str, Any] | None,
    activity_file: Path | None = None,
    start_ts: int | None = None,
    excludes: set[str] | None = None,
    exclude_prefixes: tuple[str, ...] = (".mycelium/",),
    legacy_reference_ns: int | None = None,
) -> list[str]:
    """Return a sorted, de-duplicated list of session-local paths."""
    excludes = excludes or set()
    current = read_worktree_state(repo_root)
    baseline_files = baseline.get("files", {}) if baseline else {}
    changed: set[str] = _activity_paths(repo_root, activity_file)

    if baseline is not None:
        comparator = _ContentComparator(
            repo_root, baseline, start_ts, legacy_reference_ns
        )
        must_verify: list[str] = []
        may_verify: list[tuple[tuple[int, int], str]] = []
        for path in sorted(set(current) | set(baseline_files)):
            before = baseline_files.get(path)
            decision = comparator.classify(before, current.get(path))
            if decision == _CHANGED:
                changed.add(path)
            elif decision == _MUST_VERIFY:
                must_verify.append(path)
            elif decision == _MAY_VERIFY:
                may_verify.append((_content_priority(before, racy=False), path))
        for path in must_verify:
            if comparator.content_differs(
                path, baseline_files[path], current[path], required=True
            ):
                changed.add(path)
        for _, path in sorted(may_verify):
            if comparator.content_differs(
                path, baseline_files[path], current[path], required=False
            ):
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
            baseline.get("head_reflog_entries") if baseline else None,
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

    living_snapshot_parser = subparsers.add_parser("living-snapshot")
    living_snapshot_parser.add_argument("--repo-root", type=Path, required=True)
    living_snapshot_parser.add_argument("--output", type=Path, required=True)

    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument("--repo-root", type=Path, required=True)
    collect_parser.add_argument("--baseline", type=Path)
    collect_parser.add_argument("--activity-file", type=Path)
    collect_parser.add_argument("--start-ts", type=int)
    collect_parser.add_argument("--exclude", action="append", default=[])
    collect_parser.add_argument("--exclude-prefix", action="append", default=[])

    living_parser = subparsers.add_parser("living-changed")
    living_parser.add_argument("--repo-root", type=Path, required=True)
    living_parser.add_argument("--baseline", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    repo_root = args.repo_root.resolve()
    if args.command == "snapshot":
        write_snapshot(repo_root, args.output)
        return 0
    if args.command == "living-snapshot":
        write_living_snapshot(repo_root, args.output)
        return 0

    if args.command == "living-changed":
        changed = living_changed(repo_root, load_snapshot(args.baseline))
        if changed is None:
            return 2
        return 0 if changed else 1

    baseline = load_snapshot(args.baseline)
    legacy_reference_ns = None
    if baseline is not None and args.baseline is not None:
        try:
            legacy_reference_ns = args.baseline.stat().st_mtime_ns
        except OSError:
            legacy_reference_ns = None
    changes = collect_changes(
        repo_root,
        baseline,
        activity_file=args.activity_file,
        start_ts=args.start_ts,
        excludes=set(args.exclude),
        exclude_prefixes=(".mycelium/", *args.exclude_prefix),
        legacy_reference_ns=legacy_reference_ns,
    )
    for path in changes:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
