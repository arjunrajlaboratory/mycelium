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
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

SCHEMA_VERSION = 2
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


def _stat_fingerprint(repo_root: Path, relative_path: str) -> dict[str, Any] | None:
    try:
        path = repo_root / relative_path
        stat = path.lstat()
    except OSError:
        return None
    return {
        "mode": stat.st_mode,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        # Metadata alone is not a content identity: a same-size rewrite can
        # restore its original mtime (intentionally or through a coarse
        # filesystem) and would otherwise disappear from the session delta.
        "content": _content_fingerprint(path),
    }


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


def _content_fingerprint(path: Path) -> dict[str, Any] | None:
    """Return a content identity that does not depend on timestamp precision."""
    try:
        stat = path.lstat()
    except OSError:
        return None
    if path.is_symlink():
        try:
            target = os.readlink(path)
        except OSError:
            target = ""
        return {"kind": "symlink", "target": target}
    if not path.is_file():
        return {"kind": "other", "mode": stat.st_mode}
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return {
        "kind": "file",
        "size": stat.st_size,
        "sha256": digest.hexdigest(),
    }


def _living_candidates(living_dir: Path) -> list[Path]:
    """Return the reflection artifacts one ``.living/`` directory can hold."""
    candidates = [living_dir / name for name in LIVING_FILES]
    findings_dir = living_dir / "findings"
    if findings_dir.is_dir() and not findings_dir.is_symlink():
        candidates.extend(path for path in findings_dir.rglob("*") if not path.is_dir())
    elif findings_dir.exists() or findings_dir.is_symlink():
        candidates.append(findings_dir)
    return candidates


def _fingerprint_candidates(
    repo_root: Path, candidates: Iterable[Path]
) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for path in candidates:
        fingerprint = _content_fingerprint(path)
        if fingerprint is not None:
            state[path.relative_to(repo_root).as_posix()] = fingerprint
    return state


def read_living_state(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Fingerprint lifecycle artifacts whose content satisfies Stop.

    Only the repository root's ``.living/`` is read here; earlier runtimes
    compare this mapping exactly, so it must keep meaning the root alone.
    Subfolder notes are recorded separately (see ``discover_nested_living_roots``).
    """
    return _fingerprint_candidates(repo_root, _living_candidates(repo_root / ".living"))


def _session_anchors(session_dirs: Iterable[str] = ()) -> list[str]:
    """Folders that identify where this session works.

    ``session_dirs`` is the working directory the host reported in the hook
    payload (hooks pass it with ``--session-dir``); it is the only anchor on
    hosts whose dispatcher changes to the repository root before running a
    hook (Codex). The hook process's own working directory and Claude Code's
    ``CLAUDE_PROJECT_DIR`` are added as well. All of them describe where the
    session runs, not which files it touches.
    """
    anchors: list[str] = [d for d in session_dirs if d]
    try:
        anchors.append(os.getcwd())
    except OSError:
        pass
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        anchors.append(project_dir)
    return anchors


def discover_nested_living_roots(
    repo_root: Path,
    anchors: Iterable[str] | None = None,
    session_dirs: Iterable[str] = (),
) -> list[str]:
    """Return subfolders on the session's path that keep their own ``.living/``.

    One repository can hold several projects, each with its own notes folder
    (``<repo>/<project>/.living/``). A session started inside such a project
    records its reflection there, so those folders count for Stop alongside
    the root. Only folders between a session anchor and the repository root
    are considered: notes folders elsewhere in the tree (another project, or a
    copied fixture) do not. Anchors outside the repository, or inside a nested
    repository below it, contribute nothing; a symlinked ``.living`` is ignored.
    Paths are compared after resolving symlinks on both sides.
    """
    if anchors is None:
        anchors = _session_anchors(session_dirs)
    try:
        root = repo_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    found: set[str] = set()
    for anchor in anchors:
        try:
            current = Path(anchor).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if current != root and root not in current.parents:
            continue
        chain: list[str] = []
        while current != root:
            if os.path.lexists(current / ".git"):
                chain = []  # the anchor belongs to a nested repository
                break
            living = current / ".living"
            if living.is_dir() and not living.is_symlink():
                chain.append(current.relative_to(root).as_posix())
            current = current.parent
        found.update(chain)
    return sorted(found)


def read_nested_living_state(
    repo_root: Path, roots: Iterable[str]
) -> dict[str, dict[str, Any]]:
    """Fingerprint the ``.living/`` artifacts of the given subfolders."""
    candidates: list[Path] = []
    try:
        root = repo_root.resolve(strict=True)
    except (OSError, RuntimeError):
        return {}
    for relative in roots:
        base = repo_root / relative
        living = base / ".living"
        try:
            # A subfolder replaced by a link (or removed) contributes nothing,
            # so its earlier files read as removed rather than followed.
            if base.resolve(strict=True) != root / relative:
                continue
        except (OSError, RuntimeError):
            continue
        if living.is_symlink() or not living.is_dir():
            continue
        candidates.extend(_living_candidates(living))
    return _fingerprint_candidates(repo_root, candidates)


def _nested_living_baseline(
    repo_root: Path, session_dirs: Iterable[str] = ()
) -> dict[str, Any]:
    roots = discover_nested_living_roots(repo_root, session_dirs=session_dirs)
    return {
        "nested_living_roots": roots,
        "nested_living_files": read_nested_living_state(repo_root, roots),
    }


def _baseline_nested_roots(baseline: dict[str, Any]) -> list[str]:
    """Validated subfolder list from a baseline; empty when absent or malformed."""
    raw = baseline.get("nested_living_roots")
    if not isinstance(raw, list):
        return []
    roots: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not item or "\x00" in item:
            return []
        path = PurePosixPath(item)
        if path.is_absolute() or path.as_posix() != item:
            return []
        if any(part in ("", ".", "..") for part in path.parts):
            return []
        roots.append(item)
    return roots


def build_snapshot(repo_root: Path, session_dirs: Iterable[str] = ()) -> dict[str, Any]:
    reflog = _head_reflog(repo_root)
    return {
        "schema_version": SCHEMA_VERSION,
        "head": _head(repo_root),
        "head_reflog_entries": len(reflog) if reflog is not None else None,
        "files": read_worktree_state(repo_root),
        "living_files": read_living_state(repo_root),
        **_nested_living_baseline(repo_root, session_dirs),
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


def write_snapshot(
    repo_root: Path, output: Path, session_dirs: Iterable[str] = ()
) -> None:
    _write_payload(output, build_snapshot(repo_root, session_dirs))


def write_living_snapshot(
    repo_root: Path, output: Path, session_dirs: Iterable[str] = ()
) -> None:
    """Write the minimal baseline needed for lifecycle-content comparison."""
    _write_payload(
        output,
        {
            "schema_version": SCHEMA_VERSION,
            "files": {},
            "living_files": read_living_state(repo_root),
            **_nested_living_baseline(repo_root, session_dirs),
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
    if value.get("schema_version") != SCHEMA_VERSION:
        return None
    if not isinstance(value.get("files"), dict):
        return None
    return value


def living_changed(repo_root: Path, baseline: dict[str, Any] | None) -> bool | None:
    """Return lifecycle-content change state, or None for an old baseline."""
    if baseline is None or not isinstance(baseline.get("living_files"), dict):
        return None
    if read_living_state(repo_root) != baseline["living_files"]:
        return True
    # Subfolder notes are compared over the folder list the baseline recorded,
    # never one re-derived now, so a session that moves between folders cannot
    # turn a folder's absence into a change. Baselines from earlier runtimes
    # carry no list and keep their root-only meaning.
    roots = _baseline_nested_roots(baseline)
    nested = baseline.get("nested_living_files")
    if not roots or not isinstance(nested, dict):
        return False
    return read_nested_living_state(repo_root, roots) != nested


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

    for name in ("snapshot", "living-snapshot"):
        baseline_parser = subparsers.add_parser(name)
        baseline_parser.add_argument("--repo-root", type=Path, required=True)
        baseline_parser.add_argument("--output", type=Path, required=True)
        baseline_parser.add_argument(
            "--session-dir",
            action="append",
            default=[],
            help=(
                "working directory the host reported for the session (repeatable); "
                "locates a subproject's own .living/ when the hook runs from the "
                "repository root"
            ),
        )

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

    # Prints the subproject notes roots a baseline recorded, one per line
    # (none when absent or malformed), so a shell caller can tell a notes
    # write on the session's path from project work.
    roots_parser = subparsers.add_parser("nested-living-roots")
    roots_parser.add_argument("--repo-root", type=Path, required=True)
    roots_parser.add_argument("--baseline", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    repo_root = args.repo_root.resolve()
    if args.command == "snapshot":
        write_snapshot(repo_root, args.output, args.session_dir)
        return 0
    if args.command == "living-snapshot":
        write_living_snapshot(repo_root, args.output, args.session_dir)
        return 0

    if args.command == "nested-living-roots":
        baseline = load_snapshot(args.baseline)
        if baseline is not None:
            for root in _baseline_nested_roots(baseline):
                if "\n" not in root:
                    print(root)
        return 0

    if args.command == "living-changed":
        changed = living_changed(repo_root, load_snapshot(args.baseline))
        if changed is None:
            return 2
        return 0 if changed else 1

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
