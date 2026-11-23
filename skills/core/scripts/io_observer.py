#!/usr/bin/env python3
"""Observe which files one tool call changed, for data lineage.

PreToolUse records the call in a repository call journal and, for Bash, takes
a stat snapshot of the watched scope. The matching Post walks again, diffs
the two snapshots, hashes what changed under a stability protocol, and writes
one observation record. A hook cannot see reads, so an observation lists
changes only; inputs are inferred elsewhere and only their pre-run content is
checked here (``input_proofs``).

No clock is compared with a file timestamp: a change is a difference between
two stat identities taken by this process. What the observation cannot prove
(an overlapping call, a background job, a missing snapshot, an unwatched
directory) is recorded as such, never as "nothing changed".

Subcommands (all exit 0 unless the arguments are malformed; failures are
written into the record or reported on stderr for the calling hook):

  pre   --state-dir D --host H --session S --call-id ID --tool T
        [--agent-id A] [--snapshot] [--repo-root R]
  post  --state-dir D --host H --session S --call-id ID --tool T
        [--agent-id A] [--repo-root R] [--outcome success|failure|unknown]
        [--bash-exit N] [--bash-cmd C] [--background] [--inputs-json P]
        [--record-to F]
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import marshal
import os
import re
import stat
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterator, NamedTuple

SNAPSHOT_VERSION = 2
OBSERVATION_VERSION = 2  # 2: adds `journal` (id, pre_seq, post_seq) for write ordering

DEFAULT_ENTRY_CAP = 250_000
DEFAULT_WALK_SECONDS = 3.0
DEFAULT_HASH_SECONDS = 2.0
DEFAULT_CHANGE_CAP = 1_000
HASH_CHUNK = 1 << 20
# A conservative read+hash throughput used to decide, before opening a
# file, whether the remaining budget can finish it.
HASH_BYTES_PER_SECOND = 300 * 1024 * 1024

# Directory names never walked, wherever they appear.
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".git",
        ".mycelium",
        ".living",
        ".claude",
        ".codex",
        "node_modules",
        "__pycache__",
        ".ipynb_checkpoints",
        ".snakemake",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
    }
)
# File names never listed.
EXCLUDED_FILE_NAMES = frozenset({".DS_Store"})
# A directory holding one of these is an environment or another repository,
# not project data.
_ENVIRONMENT_MARKERS = ("pyvenv.cfg", "conda-meta", ".git")

_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
SF_DATALESS = 0x40000000  # macOS: content not materialized locally


class Identity(NamedTuple):
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int
    mode: int


def identity_of(st: os.stat_result) -> Identity:
    return Identity(
        st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode
    )


class Snapshot(NamedTuple):
    entries: dict[str, Identity]
    unwatched: list[dict[str, Any]]
    truncated: bool
    timed_out: bool
    walk_ms: int
    coarse_devices: frozenset[int]


# --------------------------------------------------------------------------
# Scope and walking


def _scope_overrides(repo_root: Path) -> tuple[list[str], list[str]]:
    """Extra excludes and roots from `.mycelium/io/scope`, one per line:
    `exclude <relative path>` or `root <relative path>`. Anything else, and
    any path that escapes the repository, is ignored."""
    path = repo_root / ".mycelium" / "io" / "scope"
    excludes: list[str] = []
    roots: list[str] = []
    try:
        if path.is_symlink() or not path.is_file():
            return excludes, roots
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return excludes, roots
    for line in text.splitlines():
        word, _, value = line.strip().partition(" ")
        value = value.strip().strip("/")
        if not value or value.startswith("..") or "/../" in f"/{value}/" or "\0" in value:
            continue
        # One repository-relative spelling per directory: `./data`,
        # `data/.`, and `data//x` would otherwise walk the same files under
        # a second name, inventing a creation and a deletion for each.
        value = os.path.normpath(value)
        if value in (".", "") or value.startswith(".."):
            continue
        if word == "exclude":
            excludes.append(value)
        elif word == "root":
            roots.append(value)
    return excludes, roots


def _is_environment_dir(path: str) -> bool:
    return any(os.path.lexists(os.path.join(path, marker)) for marker in _ENVIRONMENT_MARKERS)


def walk(
    repo_root: Path,
    *,
    entry_cap: int = DEFAULT_ENTRY_CAP,
    seconds: float = DEFAULT_WALK_SECONDS,
) -> Snapshot:
    """Stat every regular file and symlink in the watched scope.

    Symlinked directories and other devices are not followed; they are
    listed in ``unwatched`` so a consumer knows changes there are unseen.
    """
    started = time.monotonic()
    deadline = started + seconds
    root = os.path.realpath(repo_root)
    try:
        root_dev = os.lstat(root).st_dev
    except OSError:
        return Snapshot({}, [], False, False, 0, frozenset())
    extra_excludes, extra_roots = _scope_overrides(Path(root))
    excluded_rel = {e.rstrip("/") for e in extra_excludes}
    entries: dict[str, Identity] = {}
    unwatched: list[dict[str, Any]] = []
    truncated = False
    timed_out = False
    sub_second: dict[int, bool] = {}
    stack: list[str] = [root]
    for extra in extra_roots:
        # Walked in the listing's spelling, never through a link: a root
        # named `SKILLS/core` on a case-insensitive volume would otherwise
        # claim those inodes under a second spelling, and the real one
        # would vanish from the walk as if deleted.
        spelled = canonical_spelling(root, extra)
        if spelled is None or not os.path.isdir(os.path.join(root, spelled)):
            continue
        linked = False
        current = root
        for part in spelled.split("/"):
            current = os.path.join(current, part)
            if os.path.islink(current):
                linked = True
                break
        if not linked:
            stack.append(os.path.join(root, spelled))
    seen_dirs: set[tuple[int, int]] = set()
    prefix_len = len(root) + 1
    while stack:
        directory = stack.pop()
        if time.monotonic() > deadline:
            timed_out = True
            break
        try:
            dir_st = os.lstat(directory)
        except OSError:
            # Not silently skipped: its contents would read as deleted.
            unwatched.append({"path": directory[prefix_len:] or ".", "why": "unreadable"})
            continue
        key = (dir_st.st_dev, dir_st.st_ino)
        if key in seen_dirs:
            continue
        seen_dirs.add(key)
        try:
            iterator = os.scandir(directory)
        except OSError:
            unwatched.append({"path": directory[prefix_len:] or ".", "why": "unreadable"})
            continue
        with iterator:
            for count, entry in enumerate(iterator):
                # The bound holds inside a directory too: one huge flat
                # directory on slow storage must not stall the tool call.
                if count % 256 == 255 and time.monotonic() > deadline:
                    timed_out = True
                    break
                name = entry.name
                rel = entry.path[prefix_len:]
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    # Listed but not inspectable (a directory without search
                    # permission): never silently absent, or it would read as
                    # a deletion.
                    unwatched.append({"path": rel, "why": "unreadable"})
                    continue
                mode = st.st_mode
                if stat.S_ISDIR(mode):
                    if name in EXCLUDED_DIR_NAMES:
                        continue  # a fixed rule; `watch_status` applies it too
                    if rel in excluded_rel:
                        unwatched.append({"path": rel, "why": "scope-exclude"})
                        continue
                    if st.st_dev != root_dev:
                        unwatched.append({"path": rel, "why": "other-device"})
                        continue
                    if _is_environment_dir(entry.path):
                        why = ("nested-repository"
                               if os.path.lexists(os.path.join(entry.path, ".git"))
                               else "environment")
                        unwatched.append({"path": rel, "why": why})
                        continue
                    stack.append(entry.path)
                    continue
                if name in EXCLUDED_FILE_NAMES or name.startswith("._"):
                    continue
                if rel in excluded_rel:
                    unwatched.append({"path": rel, "why": "scope-exclude"})
                    continue
                if stat.S_ISLNK(mode):
                    try:
                        if os.path.isdir(entry.path):
                            unwatched.append({"path": rel, "why": "symlink",
                                              "target": os.readlink(entry.path)})
                    except OSError:
                        pass
                elif not stat.S_ISREG(mode):
                    continue
                if len(entries) >= entry_cap:
                    truncated = True
                    break
                entries[rel] = identity_of(st)
                # Judge the device's timestamp granularity by ctime, which no
                # user call can set: `cp -p`, `rsync -a`, `tar x` and utime
                # leave whole-second mtimes on fine-grained filesystems.
                if st.st_ctime_ns % 1_000_000_000:
                    sub_second[st.st_dev] = True
                else:
                    sub_second.setdefault(st.st_dev, False)
        if truncated or timed_out:
            # A walk stopped early is incomplete and is never trusted; one
            # that finished, however long it took, listed everything.
            break
    coarse = frozenset(dev for dev, fine in sub_second.items() if not fine)
    walk_ms = int((time.monotonic() - started) * 1000)
    unwatched.sort(key=lambda item: item["path"])
    return Snapshot(entries, unwatched, truncated, timed_out, walk_ms, coarse)


def unwatched_prefixes(unwatched: list[dict[str, Any]]) -> frozenset[str]:
    """The walk's unwatched directories as a set, so a path is checked by
    its ancestors (O(depth)) instead of against every entry."""
    return frozenset(item["path"] for item in unwatched
                     if isinstance(item, dict) and isinstance(item.get("path"), str))


def watch_status(rel: str, unwatched: list[dict[str, Any]] | frozenset[str]) -> str:
    """Whether a repository-relative path lies in the watched scope.

    `outside-scope` (escapes the repository), `unwatched` (under a fixed
    excluded name, an excluded file name, or any unwatched directory the
    walk recorded), or `watched`. A change to a path that is not watched
    can never appear in an observation, so "absent from the change list"
    means "unchanged" only for a watched path.
    """
    if not rel or rel == "." or rel == ".." or rel.startswith("../") or os.path.isabs(rel):
        return "outside-scope"
    parts = rel.split("/")
    if any(part in EXCLUDED_DIR_NAMES for part in parts[:-1]):
        return "unwatched"
    if parts[-1] in EXCLUDED_FILE_NAMES or parts[-1].startswith("._"):
        return "unwatched"
    prefixes = unwatched if isinstance(unwatched, frozenset) else unwatched_prefixes(unwatched)
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in prefixes:
            return "unwatched"
    return "watched"


# --------------------------------------------------------------------------
# State files (snapshot, journal, cache)


def _check_dir(io_root: Path, path: Path) -> None:
    """Create ``path`` (at or below ``io_root``) one component at a time,
    refusing any component that is a symlink or not a directory *before*
    anything is created beneath it.

    ``io_root`` is ``<state dir>/io``. The state directory above it was
    validated by the calling hook's shared-state check, so only components
    from ``io`` downward are judged here; a repository under a symlinked
    home directory stays usable. The anchor is explicit: a session or host
    named ``io`` cannot move it.
    """
    try:
        relative = path.relative_to(io_root)
    except ValueError:
        raise OSError(f"not below the io state root: {path}") from None
    if any(part in ("", ".", "..") for part in relative.parts):
        raise OSError(f"unsafe io state path: {path}")
    current = io_root.parent
    for part in ("io", *relative.parts):
        current = current / part
        try:
            os.mkdir(current, 0o755)
        except FileExistsError:
            pass
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError(f"refusing unsafe state directory: {current}")


def _safe_component(value: str) -> str:
    if not _SAFE_ID.fullmatch(value) or value in (".", ".."):
        return "h" + hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:32]
    return value


def snapshot_path(state_dir: Path, host: str, session: str, call_id: str) -> Path:
    return (
        state_dir
        / "io"
        / "pre"
        / _safe_component(host)
        / _safe_component(session)
        / f"{_safe_component(call_id)}.snap"
    )


def write_snapshot(
    path: Path, snapshot: Snapshot, meta: dict[str, Any], *, io_root: Path
) -> None:
    _check_dir(io_root, path.parent)
    payload = marshal.dumps(
        {
            "version": SNAPSHOT_VERSION,
            "meta": meta,
            "entries": {k: tuple(v) for k, v in snapshot.entries.items()},
            "unwatched": snapshot.unwatched,
            "truncated": snapshot.truncated,
            "timed_out": snapshot.timed_out,
            "walk_ms": snapshot.walk_ms,
            "coarse_devices": sorted(snapshot.coarse_devices),
        }
    )
    fd, temp = tempfile.mkstemp(prefix=".snap.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def _snapshot_root(path: Path) -> str | None:
    """The physical checkout a Pre snapshot walked, or None if unreadable."""
    loaded = read_snapshot(path)
    if loaded is None:
        return None
    root = loaded[1].get("repo_root")
    return os.path.realpath(root) if isinstance(root, str) else None


def read_snapshot(path: Path) -> tuple[Snapshot, dict[str, Any]] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = marshal.loads(handle.read())
    except (OSError, EOFError, ValueError, TypeError):
        return None
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(data, dict) or data.get("version") != SNAPSHOT_VERSION:
        return None
    try:
        entries = {str(k): Identity(*v) for k, v in data["entries"].items()}
        snapshot = Snapshot(
            entries,
            list(data["unwatched"]),
            bool(data["truncated"]),
            bool(data["timed_out"]),
            int(data["walk_ms"]),
            frozenset(int(d) for d in data["coarse_devices"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    return snapshot, meta


LOCK_WAIT_SECONDS = 2.0


class LockTimeout(OSError):
    """A state lock stayed busy past the bound; the caller records a gap."""


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Hold the stable `<path>.lock` (never through a symlink), waiting at
    most LOCK_WAIT_SECONDS: a hook must not stall behind another hook."""
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(f"{path}.lock", flags, 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("lock is not a regular file")
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise LockTimeout(f"lock busy: {path}.lock") from None
                time.sleep(0.005)
        yield
    finally:
        os.close(fd)


def append_line(path: Path, record: dict[str, Any], *, io_root: Path) -> None:
    """Append one JSON line under the file's stable lock."""
    _check_dir(io_root, path.parent)
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    with _locked(path):
        fd = os.open(
            path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o644
        )
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("not a regular file")
            os.write(fd, line)
        finally:
            os.close(fd)


def _read_lines(path: Path) -> list[dict[str, Any]]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
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
        if isinstance(value, dict):
            records.append(value)
    return records


def journal_path(state_dir: Path) -> Path:
    return state_dir / "io" / "calls.ndjson"


def cache_path(state_dir: Path) -> Path:
    return state_dir / "io" / "hash-cache.marshal"


def observations_path(state_dir: Path, host: str, session: str) -> Path:
    return (
        state_dir / "io" / "obs" / _safe_component(host) / f"{_safe_component(session)}.ndjson"
    )


# --------------------------------------------------------------------------
# Call journal
#
# One line per event: {"seq", "ev": "pre"|"post"|"expire", "call", "host",
# "session", "agent", "tool", "background", "wall_ns"}. `seq` is assigned
# under the journal lock from a counter kept in the same file, so intervals
# from separate processes are ordered without trusting their clocks. A call
# whose `pre` has no `post` is open. Two intervals overlap when each starts
# before the other ends (an open interval never ends).

# One bound for every open interval. A shorter bound for "ordinary" calls
# would close a call that is still running (a Codex command that became a
# background terminal is never flagged at Pre), letting its writes pass as
# another call's. Claude calls are closed earlier, at the end of their
# turn, where no foreground call can still be running (`turn_end`).
OPEN_INTERVAL_MAX_SECONDS = 24 * 3600
JOURNAL_COMPACT_LINES = 4_000


class Interval(NamedTuple):
    call: str
    host: str
    session: str
    tool: str
    background: bool
    pre_seq: int
    post_seq: int | None  # None while open
    expired: bool
    pre_wall_ns: int
    agent: str | None = None  # a subagent's id; None for the main agent
    # The Post said an observation follows (a Bash call, an edit with named
    # targets): a closed interval with none on record is a gap.
    observe: bool = False
    # Why an expired interval closed: "turn-ended" (the host's turn is over,
    # so no foreground call is still running) or "age" (only a bound); for
    # a background job, "task-finished" when the host showed it ended.
    end_reason: str | None = None
    # The host's id for a background job (Claude's backgroundTaskId).
    task: str | None = None


def _journal_records(path: Path) -> list[dict[str, Any]]:
    return [r for r in _read_lines(path) if isinstance(r.get("seq"), int)]


def interval_key(host: str, session: str, call: str) -> str:
    """A call's journal identity: its id within its host session. The same
    tool id in two sessions is two calls, and one session's Post never
    closes the other's interval."""
    return f"{host}\x00{session}\x00{call}"


def _intervals(records: list[dict[str, Any]]) -> dict[str, Interval]:
    calls: dict[str, Interval] = {}
    by_call: dict[str, list[str]] = {}
    for record in sorted(records, key=lambda r: r["seq"]):
        call = str(record.get("call"))
        event = record.get("ev")
        host, session = record.get("host"), record.get("session")
        if event == "pre":
            key = interval_key(str(host or ""), str(session or ""), call)
            by_call.setdefault(call, []).append(key)
        elif isinstance(host, str) and isinstance(session, str):
            key = interval_key(host, session, call)
        else:
            # A record without its session (written before keys were scoped):
            # it applies only where the bare id names exactly one open call.
            open_keys = [k for k in by_call.get(call, []) if k in calls
                         and calls[k].post_seq is None]
            if len(open_keys) != 1:
                continue
            key = open_keys[0]
        if event == "pre":
            calls[key] = Interval(
                call,
                str(record.get("host") or ""),
                str(record.get("session") or ""),
                str(record.get("tool") or ""),
                bool(record.get("background")),
                0 if record.get("start_unknown") else record["seq"],
                None,
                False,
                int(record.get("wall_ns") or 0),
                record.get("agent") if isinstance(record.get("agent"), str) else None,
            )
        elif event == "launch" and key in calls:
            # A background call's own Post: the job may still be running,
            # so the interval stays open.
            calls[key] = calls[key]._replace(
                background=True, observe=calls[key].observe or record.get("observe") is True,
                task=record.get("task") if isinstance(record.get("task"), str)
                else calls[key].task)
        elif event == "end" and key in calls and calls[key].post_seq is None:
            # The host showed the background job ended: a real bound.
            calls[key] = calls[key]._replace(
                post_seq=record["seq"], expired=False, background=True,
                end_reason=str(record.get("reason") or "task-finished"))
        elif event in ("post", "expire") and key in calls and calls[key].post_seq is None:
            current = calls[key]
            calls[key] = current._replace(
                post_seq=record["seq"],
                expired=event == "expire",
                background=current.background or bool(record.get("background")),
                observe=current.observe or record.get("observe") is True,
                end_reason=(("turn-ended" if record.get("reason") == "turn-ended" else "age")
                            if event == "expire" else None),
            )
    return calls


def _rewrite(path: Path, records: list[dict[str, Any]]) -> None:
    fd, temp = tempfile.mkstemp(prefix=".calls.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        os.replace(temp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp)
        raise


def journal_event(
    state_dir: Path,
    event: str,
    call: str,
    *,
    host: str = "",
    session: str = "",
    agent: str | None = None,
    tool: str = "",
    background: bool = False,
    now_ns: int | None = None,
    meta: dict[str, Any] | None = None,
    observe: bool = False,
    task: str | None = None,
) -> tuple[int, dict[str, Interval], list[Interval]]:
    """Append one event under the journal lock.

    Returns its sequence number, every interval known afterwards, and the
    intervals this call expired (open past their age bound). Expiry and
    compaction run here, in whichever hook holds the lock, so no Stop branch
    has to run for the journal to stay bounded.

    Every record carries the journal's id (`jid`), minted when the journal
    starts empty. Sequence numbers are comparable only within one id: a
    deleted journal restarts at 1 under a new id. With ``meta``, the id is
    stored there as ``journal_id``.
    """
    path = journal_path(state_dir)
    _check_dir(state_dir / "io", path.parent)
    now_ns = time.time_ns() if now_ns is None else now_ns
    with _locked(path):
        records = _journal_records(path)
        existing = len(records)
        seq = max((r["seq"] for r in records), default=0) + 1
        jid = next((r["jid"] for r in reversed(records) if isinstance(r.get("jid"), str)), None)
        if jid is None:
            # A fresh journal, or one written before ids existed: a new id,
            # so its sequence numbers are never compared with an older run's.
            jid = uuid.uuid4().hex
        if meta is not None:
            meta["journal_id"] = jid
        intervals = _intervals(records)
        expired: list[Interval] = []
        own_key = interval_key(host, session, call)
        for key, interval in intervals.items():
            if interval.post_seq is not None or key == own_key:
                continue
            if now_ns - interval.pre_wall_ns > OPEN_INTERVAL_MAX_SECONDS * 1_000_000_000:
                records.append({"seq": seq, "ev": "expire", "call": interval.call,
                                "host": interval.host, "session": interval.session,
                                "wall_ns": now_ns, "jid": jid})
                expired.append(interval)
                seq += 1
        if event in ("post", "launch") and own_key not in intervals:
            # This call's Pre never reached the journal (its append timed
            # out). Record it as having started before everything still in
            # the journal: every call it may have run beside then lists it,
            # even after its lock-free open marker is gone.
            records.append({"seq": seq, "ev": "pre", "call": call, "host": host,
                            "session": session, "agent": agent, "tool": tool,
                            "background": background, "wall_ns": now_ns,
                            "start_unknown": True, "jid": jid})
            seq += 1
        records.append(
            {
                "seq": seq,
                "ev": event,
                "call": call,
                "host": host,
                "session": session,
                "agent": agent,
                "tool": tool,
                "background": background,
                "wall_ns": now_ns,
                "jid": jid,
                **({"observe": True} if observe else {}),
                **({"task": task} if task else {}),
            }
        )
        intervals = _intervals(records)
        if expired or len(records) > JOURNAL_COMPACT_LINES:
            # Keep every open interval and every closed one that ends after
            # the earliest open start: only those can still overlap a call
            # whose observation is not yet written.
            open_starts = [i.pre_seq for i in intervals.values() if i.post_seq is None]
            horizon = min(open_starts, default=seq)
            keep = {
                i.call
                for i in intervals.values()
                if i.post_seq is None or i.post_seq >= horizon
            }
            kept = [r for r in records if str(r.get("call")) in keep]
            # Where compaction cut: an interval that started before this may
            # be gone, so the index proves nothing it cannot see past it.
            floor = max([horizon] + [r.get("floor", 0) for r in records
                                     if r.get("ev") == "floor"])
            kept.insert(0, {"seq": 0, "ev": "floor", "call": "", "floor": floor, "jid": jid})
            if not kept or kept[-1]["seq"] != seq:
                # The counter lives in the file: keep the newest line so
                # sequence numbers never restart after compaction.
                kept.append(records[-1])
            _rewrite(path, kept)
        else:
            # Nothing expired: append only the lines this call added.
            line = "".join(json.dumps(r, sort_keys=True) + "\n" for r in records[existing:])
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                         | os.O_NONBLOCK, 0o644)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("journal is not a regular file")
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
    _publish_expired(state_dir, jid, expired)
    return seq, intervals, expired


def _publish_expired(state_dir: Path, jid: str, expired: list[Interval]) -> None:
    """A foreground call the journal closed by age, with no Post: what it
    changed is unknown, and the age bound is no evidence that it stopped.
    The gap is published to the call's store, since compaction drops the
    interval from the journal right away. Best effort, like every append."""
    for interval in expired:
        # A background job too: if its launch observation was lost, this is
        # the only durable trace once compaction drops the interval. Where
        # the launch observation exists, the index prefers it (it has a
        # change list), and either way the job's gap stays open-ended.
        snap = snapshot_path(state_dir, interval.host, interval.session, interval.call)
        record = {
            "kind": "observation",
            "version": OBSERVATION_VERSION,
            "observation_id": f"{interval.host}:{interval.call}",
            "host": interval.host,
            "host_session_id": interval.session,
            "tool": interval.tool,
            "status": "unavailable",
            "reason": "call-expired",
            "detail": "the call had no Post within the journal's age bound",
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "changes": None,
            "journal": {"id": jid, "pre_seq": interval.pre_seq, "post_seq": None,
                        "expired": True, "end_reason": "age"},
            "repo_root": _snapshot_root(snap) if snap.exists() and not snap.is_symlink()
            else None,
        }
        with contextlib.suppress(OSError):
            append_line(observations_path(state_dir, interval.host, interval.session),
                        record, io_root=state_dir / "io")


_TASK_ID_RX = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
# Statuses that mean a background task is over. Anything else (including a
# status this list does not know) leaves the job open.
_TASK_DONE = frozenset({"completed", "complete", "done", "failed", "error", "errored",
                        "killed", "stopped", "cancelled", "canceled", "exited",
                        "terminated", "finished"})


def end_finished_background(state_dir: Path, *, host: str, session: str,
                            tasks: list[Any]) -> int:
    """End background jobs the host's own task list shows are over.

    ``tasks`` is the host's snapshot of this session's background tasks
    (Claude's Stop `background_tasks`). A job of this session whose task id
    is absent from the snapshot, or present with a finished status, gets an
    `end` record: a real bound, unlike an age expiry. A job with no task id,
    or one listed with any other status, stays open. Returns how many ended.
    """
    listed: dict[str, str] = {}
    for task in tasks:
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            listed[task["id"]] = str(task.get("status") or "").lower()
    path = journal_path(state_dir)
    if not path.exists():
        return 0
    _check_dir(state_dir / "io", path.parent)
    ended = 0
    ends: list[tuple[str, int]] = []
    jid = None
    with _locked(path):
        records = _journal_records(path)
        seq = max((r["seq"] for r in records), default=0)
        jid = next((r["jid"] for r in reversed(records) if isinstance(r.get("jid"), str)), None)
        lines = []
        for interval in _intervals(records).values():
            if (interval.post_seq is not None or not interval.background or not interval.task
                    or interval.host != host or interval.session != session):
                continue
            status = listed.get(interval.task)
            if status is not None and status not in _TASK_DONE:
                continue  # still running, or a status we cannot read
            if status is None and interval.agent is not None:
                # Absence proves an end only for the main agent's own jobs:
                # it is stopped while its Stop snapshot is taken, but a
                # background subagent may launch a job after the snapshot.
                continue
            seq += 1
            lines.append(json.dumps({"seq": seq, "ev": "end", "call": interval.call,
                                     "host": host, "session": session,
                                     "reason": "task-finished", "task": interval.task,
                                     "wall_ns": time.time_ns(),
                                     **({"jid": jid} if jid else {})},
                                    sort_keys=True) + "\n")
            ends.append((interval.call, seq))
            ended += 1
        if lines:
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise OSError("journal is not a regular file")
                os.write(fd, "".join(lines).encode("utf-8"))
            finally:
                os.close(fd)
    # The end also goes to the session's store: compaction drops the ended
    # interval from the journal, and the bound must outlive it.
    for call, end_seq in ends:
        with contextlib.suppress(OSError):
            append_line(observations_path(state_dir, host, session),
                        {"kind": "background-end", "observation_id": f"{host}:{call}",
                         "host_session_id": session, "reason": "task-finished",
                         "journal": {"id": jid, "post_seq": end_seq}},
                        io_root=state_dir / "io")
    return ended


def turn_end(state_dir: Path, *, host: str, session: str) -> int:
    """Close a finished turn's foreground calls that never got a Post.

    Only for hosts where no foreground tool call outlives its turn (Claude
    Code: a call is denied, interrupted, or completed before Stop). Each
    still-open, non-background interval of this host session is closed as
    expired, its open marker removed, and a snapshot it left becomes an
    `unavailable: call-unresolved` observation. Background intervals stay
    open. Returns how many calls were closed.
    """
    path = journal_path(state_dir)
    io_root = state_dir / "io"
    closed: list[str] = []
    # The journal bound of each call closed here: a confirmed turn end, so
    # a later call's writes are provably after whatever this one hid.
    bounds: dict[str, dict[str, Any]] = {}
    if path.exists():
        _check_dir(io_root, path.parent)
        with _locked(path):
            records = _journal_records(path)
            seq = max((r["seq"] for r in records), default=0)
            jid = next((r["jid"] for r in reversed(records) if isinstance(r.get("jid"), str)),
                       None)
            now_ns = time.time_ns()
            lines = []
            for interval in _intervals(records).values():
                # Main-agent calls only: a background subagent carries the
                # parent's session and may still be running its calls.
                if (interval.post_seq is None and not interval.background
                        and interval.agent is None
                        and interval.host == host and interval.session == session):
                    seq += 1
                    record = {"seq": seq, "ev": "expire", "call": interval.call,
                              "host": interval.host, "session": interval.session,
                              "wall_ns": now_ns, "reason": "turn-ended",
                              **({"jid": jid} if jid else {})}
                    lines.append(json.dumps(record, sort_keys=True) + "\n")
                    closed.append(interval.call)
                    if jid:
                        bounds[interval.call] = {"id": jid, "pre_seq": interval.pre_seq,
                                                 "post_seq": seq, "expired": True,
                                                 "end_reason": "turn-ended"}
            if lines:
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise OSError("journal is not a regular file")
                    os.write(fd, "".join(lines).encode("utf-8"))
                finally:
                    os.close(fd)
    # Unjournaled foreground calls of this session (their Pre lost the
    # journal lock) cannot be running after the turn either.
    marker_dir = io_root / "open" / _safe_component(host) / _safe_component(session)
    markers = []
    if _io_dir_chain_safe(io_root, marker_dir):
        with contextlib.suppress(OSError):
            markers = list(os.scandir(marker_dir))
    for marker in markers:
        if not marker.is_file(follow_symlinks=False):
            continue
        data = _read_marker(marker.path)
        if data is None:
            continue
        if data.get("background") or data.get("agent"):
            continue
        call = data.get("call") if isinstance(data.get("call"), str) else marker.name
        if call not in closed:
            closed.append(call)
        with contextlib.suppress(OSError):
            os.unlink(marker.path)
    for call in closed:
        clear_open_marker(state_dir, host, session, call)
        snap = snapshot_path(state_dir, host, session, call)
        if snap.exists() and not snap.is_symlink():
            record = {
                "kind": "observation",
                "version": OBSERVATION_VERSION,
                "observation_id": f"{host}:{call}",
                "host": host,
                "host_session_id": session,
                "status": "unavailable",
                "reason": "call-unresolved",
                "detail": "the turn ended without a Post for this call "
                          "(denied, interrupted, or the hook did not run)",
                "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "changes": None,
                "journal": bounds.get(call),
                # The checkout this gap belongs to, from the call's own Pre
                # snapshot, so it never clouds another worktree's verdicts.
                "repo_root": _snapshot_root(snap),
            }
            with contextlib.suppress(OSError):
                append_line(observations_path(state_dir, host, session), record,
                            io_root=io_root)
                # A small stand-in replaces the snapshot, so the session's
                # next manifest still lists the call as unresolved (and then
                # claims it) instead of the gap disappearing here.
                stand_in = snap.with_name(snap.name[: -len(".snap")] + ".unresolved")
                fd = os.open(stand_in, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW
                             | os.O_NONBLOCK, 0o644)
                try:
                    os.write(fd, json.dumps({"call": call, "reason": "turn-ended"}).encode())
                finally:
                    os.close(fd)
                os.unlink(snap)
    return len(closed)


def overlapping(intervals: dict[str, Interval], key: str) -> list[dict[str, Any]]:
    """Every other interval that intersects this call's [pre, post].
    ``key`` is the call's `interval_key`."""
    own = intervals.get(key)
    if own is None and "\x00" not in key:
        # A bare call id: accepted when it names exactly one interval.
        matches = [k for k, i in intervals.items() if i.call == key]
        if len(matches) == 1:
            key = matches[0]
            own = intervals[key]
    if own is None:
        return []
    own_end = own.post_seq if own.post_seq is not None else float("inf")
    found = []
    for other_key, other in intervals.items():
        if other_key == key:
            continue
        other_end = other.post_seq if other.post_seq is not None else float("inf")
        if other.pre_seq < own_end and own.pre_seq < other_end:
            found.append(
                {
                    "call": other.call,
                    "host": other.host,
                    "session": other.session,
                    "tool": other.tool,
                    "background": other.background,
                    "open": other.post_seq is None,
                    "expired": other.expired,
                }
            )
    return sorted(found, key=lambda item: item["call"])


# --------------------------------------------------------------------------
# Hashing


class HashResult(NamedTuple):
    sha256: str | None
    status: str  # hashed | cached | pending | unstable | dataless | unavailable


def _cache_key(identity: Identity) -> str:
    return f"{identity.dev}:{identity.ino}:{identity.size}:{identity.mtime_ns}:{identity.ctime_ns}"


CACHE_MAX_ENTRIES = 50_000


def _read_cache_file(path: Path) -> dict[str, str]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return {}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            data = marshal.loads(handle.read())
    except (OSError, EOFError, ValueError, TypeError):
        return {}
    finally:
        if fd >= 0:
            os.close(fd)
    if not isinstance(data, dict):
        return {}
    return {
        k: v
        for k, v in data.items()
        if isinstance(k, str) and isinstance(v, str) and len(v) == 64
    }


def load_cache(state_dir: Path) -> dict[str, str]:
    return _read_cache_file(cache_path(state_dir))


def is_racy(identity: Identity, now_ns: int, coarse: bool) -> bool:
    """True when a write in the same timestamp tick could hide behind this
    identity (git's racily-clean rule)."""
    granularity = 2_000_000_000 if coarse else 10_000_000
    return identity.ctime_ns >= now_ns - granularity or identity.mtime_ns >= now_ns - granularity


def stable_hash(
    path: Path,
    expected: Identity | None,
    *,
    deadline: float,
    cache: dict[str, str],
    new_cache: dict[str, str],
    coarse: bool = False,
) -> tuple[HashResult, Identity | None]:
    """Hash ``path`` only if its content provably did not change meanwhile.

    The opened descriptor must be a regular file whose identity is the same
    before and after reading, equal to the path's ``lstat`` identity, and
    equal to ``expected`` when given. Anything else is ``unstable``.
    """
    try:
        lst = os.lstat(path)
    except (OSError, ValueError):
        return HashResult(None, "unavailable"), None
    if not stat.S_ISREG(lst.st_mode):
        return HashResult(None, "unavailable"), None
    current = identity_of(lst)
    if expected is not None and current != expected:
        return HashResult(None, "unstable"), current
    if getattr(lst, "st_flags", 0) & SF_DATALESS:
        return HashResult(None, "dataless"), current
    key = _cache_key(current)
    if key in cache:
        return HashResult(cache[key], "cached"), current
    if key in new_cache:
        return HashResult(new_cache[key], "cached"), current
    remaining = deadline - time.monotonic()
    if remaining <= 0 or current.size > remaining * HASH_BYTES_PER_SECOND:
        # Never start a read the budget cannot finish: a hook the host
        # kills for running long loses its whole record, not just a hash.
        return HashResult(None, "pending"), current
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except (OSError, ValueError):
        return HashResult(None, "unavailable"), current
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or identity_of(before) != current:
            return HashResult(None, "unstable"), current
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, HASH_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
            if time.monotonic() > deadline:
                # Slower storage than estimated: stop at the budget.
                return HashResult(None, "pending"), current
        after = os.fstat(fd)
    except OSError:
        return HashResult(None, "unavailable"), current
    finally:
        os.close(fd)
    try:
        relst = os.lstat(path)
    except OSError:
        return HashResult(None, "unstable"), current
    if identity_of(after) != current or identity_of(relst) != current:
        return HashResult(None, "unstable"), current
    value = digest.hexdigest()
    if not is_racy(current, time.time_ns(), coarse):
        new_cache[key] = value
    return HashResult(value, "hashed"), current


def flush_cache(state_dir: Path, new_cache: dict[str, str]) -> None:
    if not new_cache:
        return
    path = cache_path(state_dir)
    _check_dir(state_dir / "io", path.parent)
    with _locked(path):
        # Merge under the lock so parallel Posts never drop each other's
        # entries; the oldest entries go first once the cap is reached.
        merged = _read_cache_file(path)
        for key in new_cache:
            merged.pop(key, None)
        merged.update(new_cache)
        if len(merged) > CACHE_MAX_ENTRIES:
            merged = dict(list(merged.items())[-CACHE_MAX_ENTRIES:])
        fd, temp = tempfile.mkstemp(prefix=".hash-cache.", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(marshal.dumps(merged))
            os.replace(temp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp)
            raise


# --------------------------------------------------------------------------
# Diff


class Diff(NamedTuple):
    created: list[str]
    modified: list[str]
    deleted: list[str]
    rename_candidates: list[tuple[str, str]]


def diff(pre: dict[str, Identity], post: dict[str, Identity]) -> Diff:
    created = sorted(set(post) - set(pre))
    deleted = sorted(set(pre) - set(post))
    modified = sorted(p for p in set(pre) & set(post) if pre[p] != post[p])
    by_identity: dict[tuple[int, int, int, int], list[str]] = {}
    for path in deleted:
        ident = pre[path]
        by_identity.setdefault((ident.dev, ident.ino, ident.size, ident.mtime_ns), []).append(path)
    renames: list[tuple[str, str]] = []
    for path in created:
        ident = post[path]
        sources = by_identity.get((ident.dev, ident.ino, ident.size, ident.mtime_ns))
        if sources and len(sources) == 1:
            renames.append((sources[0], path))
    return Diff(created, modified, deleted, renames)


# --------------------------------------------------------------------------
# Pre and Post


def pre(
    state_dir: Path,
    *,
    host: str,
    session: str,
    call_id: str,
    tool: str,
    agent: str | None = None,
    repo_root: Path | None = None,
    snapshot: bool = False,
    background: bool = False,
) -> None:
    """Open the call's interval; for Bash, snapshot the watched scope.

    A lock-free open-call marker is written first, so a call whose journal
    append fails (a lock timeout under parallel hooks) is still visible to
    every other call's overlap check instead of silently letting another
    call take credit for its writes. The snapshot is taken after the
    journal entry, so any call whose Pre lands while it is being taken
    overlaps this one; it is taken even when the journal failed, and then
    says so.
    """
    io_root = state_dir / "io"
    write_open_marker(state_dir, host, session, call_id, tool=tool, background=background,
                      agent=agent)
    seq: int | None = None
    journal_error: str | None = None
    try:
        seq, _, _ = journal_event(
            state_dir, "pre", call_id, host=host, session=session, agent=agent,
            tool=tool, background=background,
        )
    except LockTimeout:
        journal_error = "lock-timeout"
    if not snapshot or repo_root is None:
        if journal_error is not None:
            raise LockTimeout(journal_error)
        return
    taken_ns = time.time_ns()
    snap = walk(repo_root)
    write_snapshot(
        snapshot_path(state_dir, host, session, call_id),
        snap,
        {"pre_seq": seq, "taken_ns": taken_ns, "repo_root": str(repo_root),
         "journal_error": journal_error},
        io_root=io_root,
    )


# --------------------------------------------------------------------------
# Open-call markers: one empty file per call between its Pre and its Post,
# written without any lock so a Pre whose journal append fails still leaves
# evidence that it is running.

OPEN_MARKER_MAX_SECONDS = 24 * 3600


def _open_marker_path(state_dir: Path, host: str, session: str, call_id: str) -> Path:
    return (
        state_dir / "io" / "open" / _safe_component(host) / _safe_component(session)
        / _safe_component(call_id)
    )


def write_open_marker(
    state_dir: Path, host: str, session: str, call_id: str, *, tool: str, background: bool,
    agent: str | None = None,
) -> None:
    path = _open_marker_path(state_dir, host, session, call_id)
    _check_dir(state_dir / "io", path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_NONBLOCK,
                 0o644)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("open marker is not a regular file")
        os.write(fd, json.dumps({"call": call_id, "host": host, "session": session,
                                 "tool": tool, "background": background,
                                 "agent": agent}).encode("utf-8"))
    finally:
        os.close(fd)


def _io_dir_chain_safe(io_root: Path, path: Path) -> bool:
    """Every component from ``io_root`` down to ``path`` exists as a real
    directory (no symlink, nothing created). For readers and deleters."""
    try:
        relative = path.relative_to(io_root)
    except ValueError:
        return False
    current = io_root
    for part in ("", *relative.parts):
        current = current / part if part else current
        try:
            info = os.lstat(current)
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            return False
    return True


def _read_marker(path: str) -> dict[str, Any] | None:
    """A marker's JSON, read without following links or blocking on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = json.loads(os.read(fd, 4096) or b"{}")
    except (OSError, ValueError):
        return {}
    finally:
        os.close(fd)
    return data if isinstance(data, dict) else {}


def clear_open_marker(state_dir: Path, host: str, session: str, call_id: str) -> None:
    with contextlib.suppress(OSError):
        os.unlink(_open_marker_path(state_dir, host, session, call_id))


def unjournaled_open_calls(
    state_dir: Path, intervals: dict[str, Interval], own_call: str, *, now: float | None = None
) -> list[dict[str, Any]]:
    """Open-call markers for calls the journal does not know.

    Each is a call that is running (or died) without a journal interval;
    its writes could be in anyone's diff, so it overlaps every observation
    while its marker lives (at most OPEN_MARKER_MAX_SECONDS).
    """
    base = state_dir / "io" / "open"
    now = time.time() if now is None else now
    found: list[dict[str, Any]] = []
    if not _io_dir_chain_safe(state_dir / "io", base):
        return found
    for host_entry in os.scandir(base):
        if not host_entry.is_dir(follow_symlinks=False):
            continue
        for session_entry in os.scandir(host_entry.path):
            if not session_entry.is_dir(follow_symlinks=False):
                continue
            for marker in os.scandir(session_entry.path):
                try:
                    info = marker.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                if now - info.st_mtime > OPEN_MARKER_MAX_SECONDS:
                    # Past the journal's own bound: the call is over by any
                    # measure, and the marker would only cost every Post.
                    with contextlib.suppress(OSError):
                        os.unlink(marker.path)
                    continue
                data = _read_marker(marker.path)
                if data is None:
                    continue
                call = data.get("call") if isinstance(data.get("call"), str) else marker.name
                marker_host = str(data.get("host") or host_entry.name)
                marker_session = str(data.get("session") or session_entry.name)
                if call == own_call or interval_key(marker_host, marker_session,
                                                    call) in intervals:
                    continue
                found.append({
                    "call": call,
                    "host": str(data.get("host") or host_entry.name),
                    "session": str(data.get("session") or session_entry.name),
                    "tool": str(data.get("tool") or ""),
                    "background": bool(data.get("background")),
                    "open": True,
                    "expired": False,
                    "unjournaled": True,
                })
    return sorted(found, key=lambda item: item["call"])


def filter_unjournaled(
    markers: list[dict[str, Any]], intervals: dict[str, Interval]
) -> list[dict[str, Any]]:
    """Markers scanned *before* the journal was read, minus the calls the
    journal now knows. A call leaves the marker set only after entering the
    journal, so scanning first means no call can fall between the two."""
    bare = {interval.call for interval in intervals.values()}
    return [marker for marker in markers
            if (interval_key(marker["host"], marker["session"], marker["call"]) not in intervals
                if "host" in marker and "session" in marker else marker["call"] not in bare)]


def _entry(path: str, before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict:
    return {"path": path, "before": before, "after": after}


def _state(ident: Identity | None, result: HashResult | None) -> dict[str, Any] | None:
    if ident is None:
        return None
    state: dict[str, Any] = {
        "sha256": result.sha256 if result else None,
        "size_bytes": ident.size,
        "hash_status": result.status if result else "not-captured",
    }
    if state["sha256"] is not None:
        state["hash_origin"] = "observed"
    # Always kept: a pending hash is resolved later only if the file still
    # has exactly this identity, and an unchanged identity (ctime moves on
    # any change and cannot be set back) proves the file untouched since.
    state["identity"] = {"dev": ident.dev, "ino": ident.ino, "size": ident.size,
                         "mtime_ns": ident.mtime_ns, "ctime_ns": ident.ctime_ns}
    return state


def _before_state(ident: Identity, cache: dict[str, str]) -> dict[str, Any]:
    digest = cache.get(_cache_key(ident))
    return _state(ident, HashResult(digest, "cached") if digest else None)


def repo_relative_unfollowed(raw: str, root_real: str) -> tuple[str | None, str | None]:
    """The repository-relative path of ``raw``, never through a symlink.

    The repository root is found among the path's own ancestors (so an
    alias above it, such as macOS /var for /private/var, still works);
    below the root no component may be a symlink. A symlinked file or
    directory can be retargeted during a call, so the content behind it
    afterwards says nothing about what was read: such a path gets
    `symlinked-path`, never a proof. Returns (rel, None) or (None, reason).
    """
    if ".." in Path(raw).parts:
        # The kernel resolves `..` against a symlink's target, a lexical
        # normalisation against the link's parent: they can name different
        # files, so a path that climbs is never proven.
        return None, "parent-traversal"
    lexical = os.path.normpath(os.path.abspath(raw))
    rel = None
    if lexical == root_real or lexical.startswith(root_real.rstrip(os.sep) + os.sep):
        # Under the root as written: no alias search, so a symlink below the
        # root (`self -> .`) is checked as a component, never taken as one.
        rel = os.path.relpath(lexical, root_real)
    else:
        # An alias above the root (macOS /var for /private/var): the
        # outermost ancestor that resolves to the root. Taking the
        # outermost one keeps any symlink below it inside `rel`, where the
        # component check refuses it.
        ancestors = []
        ancestor = lexical
        while True:
            ancestors.append(ancestor)
            parent = os.path.dirname(ancestor)
            if parent == ancestor:
                break
            ancestor = parent
        for ancestor in reversed(ancestors):
            if os.path.realpath(ancestor) == root_real:
                rel = os.path.relpath(lexical, ancestor)
                break
        if rel is None:
            return None, "outside-scope"
    if rel == ".":
        return None, "outside-scope"
    current = root_real
    for part in rel.split(os.sep):
        current = os.path.join(current, part)
        if os.path.islink(current):
            return None, "symlinked-path"
    return rel, None


def _input_proofs(
    inputs: list[str],
    repo_root: Path,
    pre_snap: Snapshot,
    post_snap: Snapshot,
    taken_ns: int,
    *,
    deadline: float,
    cache: dict[str, str],
    new_cache: dict[str, str],
) -> list[dict[str, Any]]:
    """For each inferred input: its hash and whether that hash is provably
    the content it had when the call started."""
    root = os.path.realpath(repo_root)
    proofs = []
    for raw in inputs:
        record: dict[str, Any] = {"path": raw, "pre_run_proven": False}
        try:
            rel, reason = repo_relative_unfollowed(raw, root)
        except (OSError, ValueError):
            rel, reason = None, "unresolvable"
        if rel is None:
            record["reason"] = reason
            proofs.append(record)
            continue
        before = pre_snap.entries.get(rel)
        after = post_snap.entries.get(rel)
        if before is None or after is None:
            record["reason"] = (
                "outside-scope" if watch_status(rel, pre_snap.unwatched) != "watched"
                else "not-in-snapshot"
            )
            proofs.append(record)
            continue
        coarse = before.dev in pre_snap.coarse_devices
        result, _ = stable_hash(
            Path(root) / rel, after, deadline=deadline, cache=cache,
            new_cache=new_cache, coarse=coarse,
        )
        record.update({"sha256": result.sha256, "hash_status": result.status,
                       "size_bytes": after.size})
        if before != after:
            record["reason"] = "changed-during-call"
        elif is_racy(before, taken_ns, coarse):
            record["reason"] = "racy-timestamp"
        elif result.sha256 is None:
            record["reason"] = f"not-hashed:{result.status}"
        else:
            record["pre_run_proven"] = True
        proofs.append(record)
    return proofs


def _redacted(text: str | None) -> str | None:
    """The command text with secrets removed, by the policy every lineage
    record shares (lineage_redact)."""
    if not text:
        return text
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import lineage_redact

    return lineage_redact.redact_command(text)


def post(
    state_dir: Path,
    *,
    host: str,
    session: str,
    call_id: str,
    tool: str,
    repo_root: Path | None = None,
    agent: str | None = None,
    outcome: str = "unknown",
    bash_exit: int | None = None,
    bash_cmd: str | None = None,
    background: bool = False,
    inputs: list[str] | None = None,
    hash_seconds: float = DEFAULT_HASH_SECONDS,
    change_cap: int = DEFAULT_CHANGE_CAP,
    edited: list[tuple[str, str]] | None = None,
    task_id: str | None = None,
) -> dict[str, Any] | None:
    """Close the call's interval and, for Bash, build its observation.

    The walk runs before the interval is closed: a call whose Pre lands
    during the walk still overlaps this one. Returns the observation (also
    appended to the session's observation store), or None for a tool call
    that only needed its interval closed.
    """
    if bash_cmd and starts_background_job(bash_cmd):
        background = True
    snap_file = snapshot_path(state_dir, host, session, call_id)
    # An edit's targets are hashed while its interval is still open: once it
    # closes, a later call may rewrite them, and those bytes must not be
    # claimed as this edit's. A later call that overlaps the hashing shows
    # up as overlap, not as a false ordering.
    edit_changes = (_edit_changes(repo_root, edited)
                    if edited is not None and repo_root is not None else None)
    loaded = (read_snapshot(snap_file)
              if repo_root is not None and edited is None else None)
    post_snap = walk(repo_root) if loaded is not None else None
    # Open markers are scanned before the journal is read (see
    # `filter_unjournaled`), after the walk so a call starting during the
    # walk is still seen in one of the two.
    markers = unjournaled_open_calls(state_dir, {}, call_id) if repo_root is not None else []
    journal_error: str | None = None
    intervals: dict[str, Interval] = {}
    journal_meta: dict[str, Any] = {}
    try:
        _, intervals, _ = journal_event(
            state_dir, "launch" if background else "post", call_id, host=host,
            session=session, agent=agent, tool=tool, background=background,
            meta=journal_meta,
            # An observation is owed for this call; if its append later fails,
            # the index sees a closed call with none on record: a gap.
            observe=repo_root is not None,
            task=task_id if background else None,
        )
    except LockTimeout:
        journal_error = "lock-timeout"
    except OSError:
        journal_error = "journal-unavailable"
    if not background and journal_error is None:
        # A background job may still be running: its marker stays until
        # the journal expires the interval or a late completion arrives.
        clear_open_marker(state_dir, host, session, call_id)
    if edit_changes is not None and repo_root is not None:
        return _publish_edit(state_dir, host=host, session=session, call_id=call_id,
                             tool=tool, agent=agent, repo_root=repo_root,
                             edit_changes=edit_changes, intervals=intervals,
                             journal_meta=journal_meta, journal_error=journal_error,
                             outcome=outcome,
                             unjournaled=filter_unjournaled(markers, intervals))
    if repo_root is None:
        return None
    with contextlib.suppress(OSError):
        sweep_stale_snapshots(state_dir, keep=snap_file)
    observation: dict[str, Any] = {
        "kind": "observation",
        "version": OBSERVATION_VERSION,
        "observation_id": f"{host}:{call_id}",
        "host": host,
        "host_session_id": session,
        "agent_id": agent,
        "tool": tool,
        "bash_cmd": _redacted(bash_cmd),
        "outcome": outcome,
        "bash_exit": bash_exit,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "overlapping": overlapping(intervals, interval_key(host, session, call_id))
        + filter_unjournaled(markers, intervals),
        "status": None,
        "reason": None,
        "scope": None,
        "changes": None,
        "input_proofs": [],
        "journal": None,
        # The checkout whose files `changes` paths are relative to. A shared
        # state directory can serve several worktrees; the index keeps only
        # observations of its own checkout.
        "repo_root": os.path.realpath(repo_root),
    }
    own = intervals.get(interval_key(host, session, call_id))
    if own is not None and journal_meta.get("journal_id"):
        # Where this call sits in the journal's order: a write by a call
        # whose post_seq precedes another call's pre_seq happened first,
        # with no clock involved. Comparable only under the same id.
        observation["journal"] = {
            "id": journal_meta["journal_id"],
            "pre_seq": own.pre_seq,
            "post_seq": own.post_seq,
            # An expiry is a bound the journal imposed, not the call's end:
            # no order is asserted from it.
            "expired": own.expired,
        }
    if own is not None and own.background:
        background = True
    if loaded is None or journal_error is not None:
        # Without the snapshot there is no "before"; without the journal
        # there is no overlap evidence. Either way, no change list is
        # claimed.
        observation["status"] = "unavailable"
        observation["reason"] = journal_error or "no-pre-snapshot"
        _publish(state_dir, host, session, observation, snap_file)
        return observation
    pre_snap, meta = loaded
    assert post_snap is not None
    pre_root = meta.get("repo_root")
    if isinstance(pre_root, str) and os.path.realpath(pre_root) != observation["repo_root"]:
        # Pre walked another checkout (a session that moved, or a shared
        # state directory): diffing two different trees would invent
        # changes, so none are claimed.
        observation["status"] = "unavailable"
        observation["reason"] = "cross-root"
        append_line(observations_path(state_dir, host, session), observation,
                    io_root=state_dir / "io")
        # The snapshot describes another tree: it is withheld from input and
        # config proofs entirely, not retired for the extractor.
        with contextlib.suppress(OSError):
            os.unlink(snap_file)
        return observation
    if own is None or meta.get("journal_error"):
        # This call's own Pre never reached the journal, so which calls
        # overlapped it is unknown: no change list is claimed as its own.
        observation["status"] = "unavailable"
        observation["reason"] = "own-interval-unjournaled"
        _publish(state_dir, host, session, observation, snap_file)
        return observation
    # A directory unwatched at either walk hides changes: one excluded at
    # Pre and removed (with its exclusion) during the call appears in
    # neither snapshot, so its deletion is visible only as this gap.
    merged_unwatched = list(post_snap.unwatched)
    post_prefixes = unwatched_prefixes(post_snap.unwatched)
    for item in pre_snap.unwatched:
        if isinstance(item, dict) and item.get("path") not in post_prefixes:
            merged_unwatched.append({**item, "at": "pre"})
    observation["scope"] = {
        "entries": len(post_snap.entries),
        "unwatched": merged_unwatched,
        "walk_ms": [pre_snap.walk_ms, post_snap.walk_ms],
    }
    for label, snap in (("pre", pre_snap), ("post", post_snap)):
        if "." in unwatched_prefixes(snap.unwatched):
            # The checkout itself could not be listed (`chmod 711 .`): every
            # path would vanish from that walk and read as a deletion, so
            # no change list is claimed at all.
            observation["status"] = "unavailable"
            observation["reason"] = f"{label}-root-unreadable"
            _publish(state_dir, host, session, observation, snap_file)
            return observation
        if snap.truncated or snap.timed_out:
            observation["status"] = "unavailable"
            observation["reason"] = (
                f"{label}-scope-too-large" if snap.truncated else f"{label}-walk-timeout"
            )
            _publish(state_dir, host, session, observation, snap_file)
            return observation

    deadline = time.monotonic() + hash_seconds
    cache = load_cache(state_dir)
    new_cache: dict[str, str] = {}
    result = diff(pre_snap.entries, post_snap.entries)
    # A path that left (or entered) the watched scope during the call, say
    # because the scope file changed, vanished from one walk without being
    # deleted (or appeared without being created). It is recorded as a
    # coverage change, never as a deletion or creation.
    pre_prefixes = unwatched_prefixes(pre_snap.unwatched)
    coverage_changed = sorted(
        [p for p in result.deleted if watch_status(p, post_prefixes) != "watched"]
        + [p for p in result.created if watch_status(p, pre_prefixes) != "watched"])
    if coverage_changed:
        moved = set(coverage_changed)
        result = Diff([p for p in result.created if p not in moved], result.modified,
                      [p for p in result.deleted if p not in moved],
                      [r for r in result.rename_candidates if r[0] not in moved and r[1] not in moved])
    root = Path(os.path.realpath(repo_root))
    taken_ns = int(meta.get("taken_ns") or 0)

    def after_state(path: str) -> dict[str, Any] | None:
        ident = post_snap.entries[path]
        coarse = ident.dev in post_snap.coarse_devices
        hashed, _ = stable_hash(root / path, ident, deadline=deadline, cache=cache,
                                new_cache=new_cache, coarse=coarse)
        return _state(ident, hashed)

    created = [_entry(p, None, after_state(p)) for p in result.created[:change_cap]]
    deleted = [_entry(p, _before_state(pre_snap.entries[p], cache), None)
               for p in result.deleted[:change_cap]]
    modified: list[dict] = []
    metadata_only: list[str] = []
    for path in result.modified[:change_cap]:
        before = _before_state(pre_snap.entries[path], cache)
        after = after_state(path)
        if (before and after and before["sha256"] and before["sha256"] == after["sha256"]):
            metadata_only.append(path)
        else:
            modified.append(_entry(path, before, after))
    capped = any(
        len(items) > change_cap for items in (result.created, result.deleted, result.modified)
    )
    observation["changes"] = {
        "created": created,
        "modified": modified,
        "deleted": deleted,
        "metadata_only": metadata_only,
        "rename_candidates": [
            {"from": a, "to": b} for a, b in result.rename_candidates[:change_cap]
        ],
        "counts": {
            "created": len(result.created),
            "modified": len(result.modified) - len(metadata_only),
            "deleted": len(result.deleted),
        },
        "capped": capped,
    }
    if coverage_changed:
        observation["changes"]["coverage_changed"] = coverage_changed[:change_cap]
        if len(coverage_changed) > change_cap:
            observation["changes"]["coverage_changed_truncated"] = True
    if capped:
        observation["changes"]["top_level"] = _top_level_counts(result)
        # Readers may scope the capped diff's uncertainty to these
        # directories only when the summary names all of them.
        observation["changes"]["top_level_complete"] = (
            _top_level_total(result) <= TOP_LEVEL_SUMMARY_MAX)
    if pre_snap.coarse_devices:
        # On a coarse-timestamp device a same-size rewrite within the tick
        # of the Pre stat leaves the stat identity unchanged, so these
        # paths' "unchanged" is unknown rather than proven.
        racy = sorted(
            path for path, ident in pre_snap.entries.items()
            if ident.dev in pre_snap.coarse_devices
            and post_snap.entries.get(path) == ident
            and is_racy(ident, taken_ns, coarse=True)
        )
        if racy:
            observation["changes"]["racy_unknown"] = racy[:change_cap]
            observation["changes"]["counts"]["racy_unknown"] = len(racy)
    observation["input_proofs"] = _input_proofs(
        inputs or [], root, pre_snap, post_snap, taken_ns,
        deadline=deadline, cache=cache, new_cache=new_cache,
    )
    if background:
        observation["status"] = "background"
    elif own.expired:
        # The journal gave up on this call while it ran: overlap past the
        # expiry point is unknown.
        observation["status"] = "overlap"
        observation["reason"] = "own-interval-expired"
    elif observation["overlapping"]:
        observation["status"] = "overlap"
    else:
        observation["status"] = "no-known-overlap"
    with contextlib.suppress(OSError):
        flush_cache(state_dir, new_cache)
    _publish(state_dir, host, session, observation, snap_file)
    return observation


def prove_inputs(
    state_dir: Path,
    done_snapshot: Path,
    repo_root: Path,
    inputs: list[str],
    *,
    hash_seconds: float = DEFAULT_HASH_SECONDS,
    known_digests: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Input proofs against a retired Pre snapshot, keyed by input path.

    ``known_digests`` are digests the caller already took this call, keyed
    like the hash cache (``dev:ino:size:mtime_ns:ctime_ns``): a file whose
    identity still matches is not read again. They are not persisted.

    An input whose identity is the same now as in the Pre snapshot (ctime
    only moves forward, so equal identities mean no write in between), and
    was not racy when the snapshot was taken, has its current hash proven
    to be its pre-run content. The snapshot is deleted afterwards.
    """
    loaded = read_snapshot(done_snapshot)
    if loaded is None:
        return {path: {"pre_run_proven": False, "reason": "no-pre-snapshot"} for path in inputs}
    pre_snap, meta = loaded
    current: dict[str, Identity] = {}
    root = os.path.realpath(repo_root)
    snap_root = meta.get("repo_root")
    if isinstance(snap_root, str) and os.path.realpath(snap_root) != root:
        # A snapshot of another checkout proves nothing about this one, even
        # where both hold a file at the same relative path.
        with contextlib.suppress(OSError):
            os.unlink(done_snapshot)
        return {path: {"pre_run_proven": False, "reason": "cross-root"} for path in inputs}
    for path in inputs:
        with contextlib.suppress(OSError, ValueError):
            # The same resolution as `_input_proofs`: no symlink followed.
            rel, _ = repo_relative_unfollowed(path, root)
            if rel is not None:
                current[rel] = identity_of(os.lstat(os.path.join(root, rel)))
    post_like = Snapshot(current, pre_snap.unwatched, False, False, 0, pre_snap.coarse_devices)
    cache = {**load_cache(state_dir), **(known_digests or {})}
    new_cache: dict[str, str] = {}
    proofs = _input_proofs(
        inputs, Path(root), pre_snap, post_like, int(meta.get("taken_ns") or 0),
        deadline=time.monotonic() + hash_seconds, cache=cache, new_cache=new_cache,
    )
    with contextlib.suppress(OSError):
        flush_cache(state_dir, new_cache)
    with contextlib.suppress(OSError):
        os.unlink(done_snapshot)
    return {proof["path"]: proof for proof in proofs}


# --------------------------------------------------------------------------
# Hook entry: decode one host payload in this process.

_SESSION_ID_RX = re.compile(r"[A-Za-z0-9._-]{1,200}")
_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"})
_QUOTED_RX = re.compile(r"""'[^']*'|"(?:\\.|[^"\\])*"|\\.""", re.S)
_BACKGROUND_AMP_RX = re.compile(r"(?<![&>|<])&(?![&>])")
_BACKGROUND_WORD_RX = re.compile(
    r"(?:^|[;&|(\n]|&&|\|\|)\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"(?:nohup|setsid|disown|sbatch|qsub|bsub|screen\s+-d|tmux\s+new(?:-session)?\s+-d)\b"
)


def starts_background_job(command: str) -> bool:
    """True when the shell text may leave a job running after the call.

    Deliberately broad: a false positive only downgrades the observation
    to `background`; a miss would let a still-running job's later writes
    pass as nothing.
    """
    unquoted = _QUOTED_RX.sub(" ", command)
    unquoted = re.sub(r"#[^\n]*", " ", unquoted)
    return bool(_BACKGROUND_AMP_RX.search(unquoted) or _BACKGROUND_WORD_RX.search(unquoted))


def _payload_string(payload: dict[str, Any], *keys: str) -> str | None:
    value: Any = payload
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value if isinstance(value, str) and value and "\0" not in value else None


def hook(state_dir: Path, repo_root: Path, host: str, raw: str) -> int:
    """PreToolUse for Bash and edit tools; Post for edit tools only (the
    Bash Post runs from the data tracker so its record can link the
    observation). Never blocks a tool and never prints."""
    try:
        payload = json.loads(raw)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    session = payload.get("session_id")
    if not isinstance(session, str) or not _SESSION_ID_RX.fullmatch(session) or session in (
        ".", ".."
    ):
        return 0  # a present invalid identity never falls back to anything
    call_id = _payload_string(payload, "tool_use_id")
    tool = _payload_string(payload, "tool_name")
    event = _payload_string(payload, "hook_event_name")
    if call_id is None or tool is None or event is None:
        return 0
    agent = _payload_string(payload, "agent_id")
    is_bash = tool == "Bash"
    if not is_bash and tool not in _EDIT_TOOLS:
        return 0
    if event == "PreToolUse":
        background = False
        if is_bash:
            tool_input = payload.get("tool_input")
            tool_input = tool_input if isinstance(tool_input, dict) else {}
            command = tool_input.get("command")
            background = tool_input.get("run_in_background") is True or (
                isinstance(command, str) and starts_background_job(command)
            )
        pre(state_dir, host=host, session=session, call_id=call_id, tool=tool, agent=agent,
            repo_root=repo_root, snapshot=is_bash, background=background)
    elif event in ("PostToolUse", "PostToolUseFailure") and not is_bash:
        edited = None
        edit_result = "success"
        cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
        if event == "PostToolUseFailure":
            # A failed edit credits no write, but it may have left changed
            # bytes (a partial write): its targets become uncertain.
            edited = edit_targets(tool, payload.get("tool_input"), cwd)
            if edited is not None:
                edited = [(path, "uncertain") for path, _ in edited]
                edit_result = "failure"
        if event == "PostToolUse":
            # A successful edit names the files it wrote.
            edited = edit_targets(tool, payload.get("tool_input"), cwd)
            if edited is not None and host != "claude":
                # Claude sends PostToolUse only for edits that succeeded. Elsewhere
                # the response says; a failed patch may have applied partly, so
                # its targets become uncertain rather than writes or deletions.
                verdict = edit_outcome(payload.get("tool_response"))
                if verdict == "failure":
                    edited = [(path, "uncertain") for path, _ in edited]
                    edit_result = "failure"
                elif verdict == "unknown":
                    # Not shown to have succeeded: the targets' hashes are
                    # recorded, but never credited to this call as its own.
                    edit_result = "unknown"
        post(state_dir, host=host, session=session, call_id=call_id, tool=tool, agent=agent,
             repo_root=repo_root if edited is not None else None, edited=edited,
             outcome=edit_result)
    return 0


def edit_outcome(response: Any) -> str:
    """`failure`, `success`, or `unknown` from an edit tool's structured
    response (an error flag, `success: false`, or a nonzero exit code)."""

    def code(value: Any) -> int | None:
        if isinstance(value, dict):
            if value.get("isError") is True or value.get("success") is False:
                return 1
            for key in ("exit_code", "exitCode", "return_code", "returncode"):
                candidate = value.get(key)
                if isinstance(candidate, int) and not isinstance(candidate, bool):
                    return candidate
            for candidate in value.values():
                found = code(candidate)
                if found is not None:
                    return found
        elif isinstance(value, list):
            for candidate in value:
                found = code(candidate)
                if found is not None:
                    return found
        elif isinstance(value, str) and value[:1] in "{[":
            try:
                decoded = json.loads(value)
            except ValueError:
                return None
            return code(decoded)
        return None

    found = code(response)
    if found is None:
        found = _textual_edit_code(response)
    if found is None:
        return "unknown"
    return "success" if found == 0 else "failure"


_EDIT_FAILURE_TEXT_RX = re.compile(
    r"^\s*(?:error|failed|patch failed|invalid (?:context|patch))\b", re.IGNORECASE)
_EDIT_SUCCESS_TEXT_RX = re.compile(r"^\s*success\b", re.IGNORECASE)


def _textual_edit_code(value: Any) -> int | None:
    """Plain-text edit responses, decoded as the activity tracker does
    (`Error: invalid patch context` is a failure; `Success. Updated ...` a
    success). Printed `stdout`/`stderr` fields are never read as status."""
    if isinstance(value, dict):
        for key, candidate in value.items():
            if key in ("stdout", "stderr"):
                continue
            found = _textual_edit_code(candidate)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _textual_edit_code(candidate)
            if found is not None:
                return found
    elif isinstance(value, str):
        if _EDIT_FAILURE_TEXT_RX.match(value):
            return 1
        if _EDIT_SUCCESS_TEXT_RX.match(value):
            return 0
    return None


_PATCH_FILE_RX = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+?)\s*$", re.M)
_PATCH_MOVE_RX = re.compile(r"^\*\*\* Move to: (.+?)\s*$", re.M)


def edit_targets(
    tool: str, tool_input: Any, cwd: str | None
) -> list[tuple[str, str]] | None:
    """The (path, action) pairs an edit tool call wrote, action being
    `written` or `deleted`; None when the payload names none.

    Claude's tools carry the path directly. Codex's apply_patch carries the
    patch text, whose `*** Add/Update/Delete File:` (and `*** Move to:`)
    lines name paths relative to the call's cwd.
    """
    if not isinstance(tool_input, dict):
        return None
    targets: list[tuple[str, str]] = []
    if tool == "apply_patch":
        patch = next(
            (v for v in tool_input.values() if isinstance(v, str) and "*** Begin Patch" in v),
            None,
        )
        if patch is None:
            return None
        # In order: an `Update File: a` followed by `Move to: b` leaves nothing
        # at a (a tombstone) and the updated content at b.
        for line in patch.splitlines():
            file_match = _PATCH_FILE_RX.match(line)
            if file_match:
                action, raw = file_match.groups()
                targets.append((raw, "deleted" if action == "Delete" else "written"))
                continue
            move_match = _PATCH_MOVE_RX.match(line)
            if move_match and targets and targets[-1][1] == "written":
                source, _ = targets.pop()
                targets.append((source, "deleted"))
                targets.append((move_match.group(1), "written"))
    else:
        key = "notebook_path" if tool == "NotebookEdit" else "file_path"
        raw = tool_input.get(key)
        if isinstance(raw, str) and raw:
            targets.append((raw, "written"))
    resolved = []
    for raw, action in targets:
        if "\0" in raw:
            continue
        path = raw if os.path.isabs(raw) or cwd is None else os.path.join(cwd, raw)
        resolved.append((path, action))
    return resolved or None


EDIT_HASH_SECONDS = 0.5
EDIT_HASH_MAX_BYTES = 50 * 1024 * 1024


_CASE_PROBES: dict[str, bool] = {}


def _case_insensitive(root_real: str) -> bool:
    """Whether the checkout's volume folds case, probed once per root by
    looking up the root's own name with its case swapped."""
    if root_real not in _CASE_PROBES:
        # The nearest ancestor (the root itself first) whose name has a
        # letter: a checkout named `12345` is probed through its parent.
        folds = False
        current = root_real
        while True:
            parent, name = os.path.split(current)
            if not name:
                break
            swapped = name.swapcase()
            if swapped != name:
                with contextlib.suppress(OSError):
                    folds = os.path.samefile(current, os.path.join(parent, swapped))
                break
            current = parent
        _CASE_PROBES[root_real] = folds
    return _CASE_PROBES[root_real]


def canonical_spelling(root_real: str, rel: str) -> str | None:
    """``rel`` spelled as the directory listings spell it (the walk's
    spelling). On a case- or normalization-insensitive volume a tool may
    name `CHANGELOG.md` as `changelog.md`. A component that does not exist
    keeps its spelling (nothing to disagree with); None when a component
    matches no listed name, or several, or cannot be listed."""
    import unicodedata

    def fold(name: str) -> str:
        return unicodedata.normalize("NFC", name).casefold()

    parts = rel.split("/")
    current = root_real
    out: list[str] = []
    for index, part in enumerate(parts):
        candidate = os.path.join(current, part)
        if not os.path.lexists(candidate):
            out.extend(parts[index:])  # created later, or absent: as named
            break
        try:
            names = os.listdir(current)
        except OSError:
            return None
        if part in names:
            chosen = part
        else:
            matches = [n for n in names if fold(n) == fold(part)]
            if len(matches) != 1:
                return None
            chosen = matches[0]
        out.append(chosen)
        current = os.path.join(current, chosen)
    return "/".join(out)


def _edit_referent(raw: str, root_real: str) -> dict[str, Any]:
    """Where an edit through a link landed, as a refusal the index turns
    into a gap: `edit-via-link` at the in-checkout file, `edit-target-
    outside` when it resolved outside, `edit-target-unknown` otherwise."""
    try:
        real = os.path.realpath(raw)
    except (OSError, ValueError):
        return {"path": None, "reason": "edit-target-unknown"}
    prefix = root_real.rstrip(os.sep) + os.sep
    if real.startswith(prefix):
        return {"path": os.path.relpath(real, root_real), "reason": "edit-via-link"}
    if real == root_real:
        return {"path": None, "reason": "edit-target-unknown"}
    return {"path": None, "reason": "edit-target-outside"}


def _edit_changes(
    repo_root: Path, edited: list[tuple[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(written, deleted, refused) entries for an edit call's targets, each
    written entry with the post-edit hash of the file when it is stable and
    within the budget, else a null hash with its status."""
    root_real = os.path.realpath(repo_root)
    deadline = time.monotonic() + EDIT_HASH_SECONDS
    written: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for raw, action in edited:
        rel, reason = repo_relative_unfollowed(raw, root_real)
        if rel is None:
            refused.append({"path": raw, "reason": reason})
            if reason in ("symlinked-path", "parent-traversal"):
                # The edit wrote through a link (or a climbing path): the
                # file it changed is wherever that resolved, which may be in
                # this checkout. Never credited as a write, but never silent:
                # the referent becomes uncertain, or the whole checkout when
                # the referent cannot be established.
                refused.append(_edit_referent(raw, root_real))
            continue
        if action != "deleted":
            # The spelling the filesystem lists, so `changelog.md` and
            # `CHANGELOG.md` on a case-insensitive volume are one history.
            canonical = canonical_spelling(root_real, rel)
            if canonical is None:
                # Which entry the edit wrote cannot be named: the whole
                # checkout is uncertain for this call.
                refused.append({"path": None, "reason": "edit-target-unknown",
                                "detail": "spelling-unknown"})
                continue
            rel = canonical
        if action == "uncertain":
            refused.append({"path": rel, "reason": "edit-failed"})
            continue
        if action == "deleted":
            # A tombstone only for a deletion that took effect: a path that
            # is still there was not deleted by this call.
            if os.path.lexists(os.path.join(root_real, rel)):
                refused.append({"path": rel, "reason": "deletion-not-applied"})
            else:
                deleted.append({"path": rel, "after": None})
                if _case_insensitive(root_real):
                    # The deleted entry can no longer be listed, so the
                    # spelling its writes were recorded under is unknown:
                    # every spelling of the name becomes uncertain.
                    refused.append({"path": rel, "reason": "deleted-spelling-unknown",
                                    "casefold": True})
            continue
        path = Path(root_real) / rel
        try:
            ident = identity_of(os.lstat(path))
        except OSError:
            written.append({"path": rel, "after": {"sha256": None, "hash_status": "unavailable"}})
            continue
        if ident.size > EDIT_HASH_MAX_BYTES or time.monotonic() >= deadline:
            result = HashResult(None, "pending")
        else:
            result, _ = stable_hash(path, ident, deadline=deadline, cache={}, new_cache={})
        after: dict[str, Any] = {"sha256": result.sha256, "size_bytes": ident.size,
                                 "hash_status": result.status}
        if result.sha256 is not None:
            after["hash_origin"] = "observed"
        if result.status == "pending":
            # As for Bash outputs: enough to resolve the hash later only if
            # the file still has exactly this identity.
            after["identity"] = {"dev": ident.dev, "ino": ident.ino, "size": ident.size,
                                 "mtime_ns": ident.mtime_ns, "ctime_ns": ident.ctime_ns}
        written.append({"path": rel, "after": after})
    return written, deleted, refused


TOP_LEVEL_SUMMARY_MAX = 50


def _top_level_counts(result: Diff) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in (*result.created, *result.modified, *result.deleted):
        top = path.split("/", 1)[0]
        counts[top] = counts.get(top, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: -item[1])[:TOP_LEVEL_SUMMARY_MAX])


def _top_level_total(result: Diff) -> int:
    return len({path.split("/", 1)[0]
                for path in (*result.created, *result.modified, *result.deleted)})


SNAPSHOT_MAX_AGE_SECONDS = 24 * 3600
DONE_SNAPSHOT_MAX_AGE_SECONDS = 3600


def sweep_stale_snapshots(
    state_dir: Path, *, keep: Path | None = None, now: float | None = None
) -> int:
    """Turn Pre snapshots no Post ever claimed into recorded gaps.

    Any session's snapshot older than the bound (a denied or interrupted
    call, a crashed session, a background run that never reported back)
    becomes an `unavailable: call-unresolved` observation in that session's
    store, and only then is deleted. Returns how many were swept.
    """
    base = state_dir / "io" / "pre"
    now = time.time() if now is None else now
    swept = 0
    try:
        hosts = list(os.scandir(base))
    except OSError:
        return 0
    for host_entry in hosts:
        if not host_entry.is_dir(follow_symlinks=False):
            continue
        for session_entry in os.scandir(host_entry.path):
            if not session_entry.is_dir(follow_symlinks=False):
                continue
            for snap_entry in os.scandir(session_entry.path):
                path = Path(snap_entry.path)
                if keep is not None and path == keep:
                    continue
                try:
                    info = snap_entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                if snap_entry.name.startswith(".snap."):
                    # A temp file from a writer killed mid-write.
                    if now - info.st_mtime >= DONE_SNAPSHOT_MAX_AGE_SECONDS:
                        with contextlib.suppress(OSError):
                            os.unlink(path)
                    continue
                if snap_entry.name.endswith(".done"):
                    # Its observation is already published; only the
                    # extractor's input proof was still to use it.
                    if now - info.st_mtime >= DONE_SNAPSHOT_MAX_AGE_SECONDS:
                        with contextlib.suppress(OSError):
                            os.unlink(path)
                    continue
                if snap_entry.name.endswith(".unresolved"):
                    # Its gap is already in the observation store; this
                    # stand-in only waited for a manifest to list it.
                    if now - info.st_mtime >= SNAPSHOT_MAX_AGE_SECONDS:
                        with contextlib.suppress(OSError):
                            os.unlink(path)
                    continue
                if not snap_entry.name.endswith(".snap"):
                    continue
                if now - info.st_mtime < SNAPSHOT_MAX_AGE_SECONDS:
                    continue
                call_id = snap_entry.name[: -len(".snap")]
                record = {
                    "kind": "observation",
                    "version": OBSERVATION_VERSION,
                    "observation_id": f"{host_entry.name}:{call_id}",
                    "host": host_entry.name,
                    "host_session_id": session_entry.name,
                    "status": "unavailable",
                    "reason": "call-unresolved",
                    "detail": "a Pre snapshot was never claimed by a Post within 24 h "
                              "(denied, interrupted, or never reported complete)",
                    "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "changes": None,
                    "repo_root": _snapshot_root(path),
                }
                try:
                    append_line(
                        state_dir / "io" / "obs" / host_entry.name
                        / f"{session_entry.name}.ndjson",
                        record,
                        io_root=state_dir / "io",
                    )
                    os.unlink(path)
                    swept += 1
                except OSError:
                    continue
            with contextlib.suppress(OSError):
                os.rmdir(session_entry.path)  # only succeeds when empty
    _sweep_temp_files(state_dir / "io", now)
    return swept


def _sweep_temp_files(io_root: Path, now: float) -> None:
    """Remove temp files a killed writer left beside the journal or cache."""
    with contextlib.suppress(OSError):
        for entry in os.scandir(io_root):
            if not entry.name.startswith((".calls.", ".hash-cache.")):
                continue
            with contextlib.suppress(OSError):
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and now - info.st_mtime >= DONE_SNAPSHOT_MAX_AGE_SECONDS:
                    os.unlink(entry.path)


def done_snapshot_path(snap_file: Path) -> Path:
    return snap_file.with_name(snap_file.name[: -len(".snap")] + ".done")


def _publish_edit(
    state_dir: Path,
    *,
    host: str,
    session: str,
    call_id: str,
    tool: str,
    agent: str | None,
    repo_root: Path,
    edit_changes: tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]],
    intervals: dict[str, Interval],
    journal_meta: dict[str, Any],
    journal_error: str | None,
    outcome: str = "success",
    unjournaled: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record the files an edit tool wrote, by path, with their hashes after
    the edit. No snapshot is involved: the tool names its own targets, so
    an edit is a write of known paths, not a whole-repository gap."""
    written, deleted, refused = edit_changes
    own_key = interval_key(host, session, call_id)
    own = intervals.get(own_key)
    observation: dict[str, Any] = {
        "kind": "observation",
        "version": OBSERVATION_VERSION,
        "observation_id": f"{host}:{call_id}",
        "host": host,
        "host_session_id": session,
        "agent_id": agent,
        "tool": tool,
        "bash_cmd": None,
        "outcome": outcome,
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # A concurrent call whose Pre missed the journal still left a
        # marker: either call could have written these bytes.
        "overlapping": (overlapping(intervals, own_key) if own is not None else [])
        + list(unjournaled or []),
        "status": "edit",
        "reason": journal_error,
        "scope": None,
        "changes": {
            # An edit tool reports what it wrote, not what it found: a
            # written path may be new or modified, so it is not split.
            "edited": written,
            "deleted": deleted,
            "refused": refused,
        },
        "input_proofs": [],
        "journal": (
            {"id": journal_meta["journal_id"], "pre_seq": own.pre_seq,
             "post_seq": own.post_seq, "expired": own.expired}
            if own is not None and journal_meta.get("journal_id") else None
        ),
        "repo_root": os.path.realpath(repo_root),
    }
    with contextlib.suppress(OSError):
        append_line(observations_path(state_dir, host, session), observation,
                    io_root=state_dir / "io")
    return observation


def _publish(
    state_dir: Path, host: str, session: str, observation: dict[str, Any], snap_file: Path
) -> None:
    """Append the observation to its session's store, then retire the
    snapshot: it is renamed to `.done` only once its observation is
    durable, so the extractor can still prove its inferred inputs against
    it, and the sweep never mistakes it for an unresolved call."""
    append_line(observations_path(state_dir, host, session), observation,
                io_root=state_dir / "io")
    with contextlib.suppress(OSError):
        os.replace(snap_file, done_snapshot_path(snap_file))


# --------------------------------------------------------------------------
# CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    h = sub.add_parser("hook", help="Decode a host payload on stdin (Pre, edit-tool Post).")
    h.add_argument("--state-dir", type=Path, required=True)
    h.add_argument("--repo-root", type=Path, required=True)
    h.add_argument("--host", required=True)
    t = sub.add_parser("turn-end", help="Close a finished turn's calls that got no Post.")
    t.add_argument("--state-dir", type=Path, required=True)
    t.add_argument("--host", required=True)
    t.add_argument("--session", required=True)
    t.add_argument("--stop-payload-stdin", action="store_true",
                   help="Read the Stop payload on stdin; its background_tasks end jobs.")
    for name in ("pre", "post"):
        p = sub.add_parser(name)
        p.add_argument("--state-dir", type=Path, required=True)
        p.add_argument("--host", required=True)
        p.add_argument("--session", required=True)
        p.add_argument("--call-id", required=True)
        p.add_argument("--tool", required=True)
        p.add_argument("--agent-id")
        p.add_argument("--repo-root", type=Path)
        p.add_argument("--background", action="store_true")
        if name == "pre":
            p.add_argument("--snapshot", action="store_true")
        else:
            p.add_argument("--outcome", default="unknown",
                           choices=("success", "failure", "unknown"))
            p.add_argument("--bash-exit", type=int)
            p.add_argument("--bash-cmd")
            p.add_argument("--task-id", help="The host's id for a background job.")
            p.add_argument("--inputs-json", type=Path,
                           help="File holding a JSON list of inferred input paths.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "hook":
        return hook(args.state_dir, args.repo_root, args.host, sys.stdin.read())
    if args.command == "turn-end":
        if not _SESSION_ID_RX.fullmatch(args.session) or args.session in (".", ".."):
            return 64
        turn_end(args.state_dir, host=args.host, session=args.session)
        if args.stop_payload_stdin:
            try:
                payload = json.loads(sys.stdin.read())
            except ValueError:
                payload = None
            tasks = payload.get("background_tasks") if isinstance(payload, dict) else None
            if isinstance(tasks, list):
                with contextlib.suppress(OSError, LockTimeout):
                    end_finished_background(args.state_dir, host=args.host,
                                            session=args.session, tasks=tasks)
        return 0
    for value in (args.host, args.session, args.call_id):
        if "\0" in value or not value:
            print("io_observer: empty or NUL identity", file=sys.stderr)
            return 64
    if args.command == "pre":
        pre(args.state_dir, host=args.host, session=args.session, call_id=args.call_id,
            tool=args.tool, agent=args.agent_id, repo_root=args.repo_root,
            snapshot=args.snapshot, background=args.background)
        return 0
    inputs: list[str] = []
    if args.inputs_json is not None:
        try:
            value = json.loads(args.inputs_json.read_text(encoding="utf-8"))
            inputs = [v for v in value if isinstance(v, str) and "\0" not in v]
        except (OSError, ValueError, TypeError):
            inputs = []
    observation = post(
        args.state_dir, host=args.host, session=args.session, call_id=args.call_id,
        tool=args.tool, agent=args.agent_id, repo_root=args.repo_root,
        outcome=args.outcome, bash_exit=args.bash_exit, bash_cmd=args.bash_cmd,
        background=args.background, inputs=inputs,
        task_id=args.task_id if args.task_id and _TASK_ID_RX.fullmatch(args.task_id) else None,
    )
    if observation is not None:
        # The calling hook reads this one line to link its lineage record.
        summary = {
            "observation_id": observation["observation_id"],
            "host_session_id": observation["host_session_id"],
            "status": observation["status"],
            "reason": observation["reason"],
            "changed": bool(observation["changes"]) and any(
                observation["changes"]["counts"].values()
            ),
        }
        done = done_snapshot_path(snapshot_path(args.state_dir, args.host, args.session,
                                                args.call_id))
        if done.exists():
            # The extractor proves its inferred inputs against this, then
            # deletes it.
            summary["pre_snapshot"] = str(done)
            summary["state_dir"] = str(args.state_dir)
        print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - the hook records this as a gap
        print(f"io_observer: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(70)
