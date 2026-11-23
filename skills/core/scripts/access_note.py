#!/usr/bin/env python3
"""Stop-time access note: a table-writing step that recorded no access.

Reads the lineage manifest the Stop phase just wrote and the project's
``analysis/*/outputs/access.json`` files (written by ``record_ids`` in
``register_value.py`` and by ``record_access.R``). Prints one short advisory
line when a proven analysis step this session wrote a table but no access
record covers it, followed by one ``key\\t<script>\\t<sha256>`` line per
step named in it. Prints nothing otherwise. Stop shows the note only when
it accepts, then remembers the keys so the same script at the same content
is never named again (``--seen``).

What counts as "wrote a table", conservatively:

- a manifest action with a script file (inline runs cannot be joined);
- that did not fail (``bash_exit`` 0 or null, ``tool_outcome`` not failure);
- with an output whose name ends in .csv, .tsv (optionally .gz), .parquet or
  .h5ad, outside ``.living/``, ``.mycelium/`` and ``.git/``;
- that the observer saw created or modified. An unobserved output is no
  evidence of a write, so it never triggers the note.

Only the last such action of each script is judged: ``record_ids`` replaces
a record of the same name, so after a rerun only the last run's record is
left. An action is named only when the observer saw its call alone
(``no-known-overlap``), and an output several scripts of one call declare is
attributed to none of them.

A record covers that action when its process started inside the action's
window: no later than the action ended (plus a small clock slack) and no
earlier than the previous recorded call ended, or, with none, than the
session started (``--since``). Its file and call site do not matter, so a
helper module, R recording into another namespace and ``computed_at: "R"``
all count. With no lower edge at all, any earlier record counts.

Errs toward silence: a file without the ``mycelium.access.v1`` marker is not
Mycelium's and is skipped; no symlink is followed and every file is opened
once (``O_NOFOLLOW``, ``O_NONBLOCK``) and read with a bound; an unreadable
file in the step's own analysis makes that step unknown; going over the
file count, byte or time budget makes every step unknown. Unknown never
produces a note.

Advisory only: never blocks Stop. Every error prints nothing and exits 0.

Usage: access_note.py --manifest PATH --repo-root DIR [--since EPOCH]
                      [--seen FILE]
       access_note.py --remember FILE KEY...   (KEY: "<script>\t<sha256>")
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import signal
import stat
import sys
import time
from pathlib import Path
from typing import Any

ACCESS_SCHEMA = "mycelium.access.v1"
TABLE_SUFFIXES = (".csv", ".tsv", ".csv.gz", ".tsv.gz", ".parquet", ".h5ad")
WRITE_CHANGES = frozenset({"created", "modified"})
EXCLUDED_TOP = frozenset({".living", ".mycelium", ".git"})
CLOCK_SLACK = datetime.timedelta(seconds=2)
# Budgets: past any of them the answer is unknown, and unknown is silent.
MAX_ACCESS_FILES = 100
MAX_ANALYSIS_DIRS = 2000
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
TIME_BUDGET_SECONDS = 1.5
HARD_DEADLINE_SECONDS = 3.0
# The note stays short whatever the names: Stop never cuts it.
MAX_LISTED = 3
MAX_TABLES_LISTED = 2
MAX_NAME_CHARS = 60
MAX_NOTE_BYTES = 1500
SEEN_MAX_BYTES = 1024 * 1024
SEEN_KEEP = 2000
_LINE_SUFFIX = re.compile(r":L\d+$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class _Unknown(Exception):
    """Whether a step recorded access cannot be told."""


def _parse_ts(value: Any) -> datetime.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return Path(os.path.normpath(path))


def _in_repo(path_text: Any, repo: Path) -> Path | None:
    if not isinstance(path_text, str) or not path_text:
        return None
    path = Path(path_text)
    return _resolve(path if path.is_absolute() else repo / path)


def _rel(path: Path, repo: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def _short(text: str) -> str:
    if len(text) <= MAX_NAME_CHARS:
        return text
    keep = MAX_NAME_CHARS - 3
    return text[: keep // 2] + "..." + text[-(keep - keep // 2):]


def _is_table(record: Any, repo: Path) -> Path | None:
    if not isinstance(record, dict) or record.get("observed_change") not in WRITE_CHANGES:
        return None
    path = _in_repo(record.get("path") or record.get("rel_path"), repo)
    if path is None or not path.name.lower().endswith(TABLE_SUFFIXES):
        return None
    try:
        top = path.relative_to(repo).parts[0]
    except (ValueError, IndexError):
        top = ""
    if top in EXCLUDED_TOP:
        return None
    return path


class _Step:
    def __init__(self, script: Path, ended: datetime.datetime,
                 previous_end: datetime.datetime | None, tables: list[Path],
                 script_sha: str) -> None:
        self.script = script
        self.ended = ended
        self.previous_end = previous_end
        self.tables = tables
        self.script_sha = script_sha
        self.root = _analysis_root(script)


def _ended(record: Any) -> datetime.datetime | None:
    return _parse_ts(record.get("ts")) if isinstance(record, dict) else None


def _table_steps(manifest: Any, repo: Path, deadline: float | None = None) -> list[_Step]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("actions"), list):
        return []
    # Each output resolved once: realpath dominates the cost on big manifests.
    tables_seen: dict[tuple[Any, Any], Path | None] = {}

    def table(record: Any) -> Path | None:
        if not isinstance(record, dict):
            return None
        key = (record.get("path") or record.get("rel_path"), record.get("observed_change"))
        if not isinstance(key[0], str):
            return None
        if key not in tables_seen:
            tables_seen[key] = _is_table(record, repo)
        return tables_seen[key]

    def check_deadline() -> None:
        if deadline is not None and time.monotonic() > deadline:
            raise _Unknown("budget")
    # When every recorded call ended: the window of the call after it.
    ends = sorted(t for t in (
        _ended(r) for key in ("actions", "data_commands")
        for r in (manifest.get(key) if isinstance(manifest.get(key), list) else [])
    ) if t is not None)
    # An output several actions of one observed call declare is marked
    # changed on all of them, though one may not have written it: say
    # nothing about it to any of them.
    shared: dict[tuple[str, Path], int] = {}
    for action in manifest["actions"]:
        if isinstance(action, dict) and isinstance(action.get("observation_id"), str):
            outputs = action.get("outputs") if isinstance(action.get("outputs"), list) else []
            check_deadline()
            for path in {t for t in (table(o) for o in outputs) if t}:
                key = (action["observation_id"], path)
                shared[key] = shared.get(key, 0) + 1
    last: dict[Path, _Step] = {}
    for action in manifest["actions"]:
        check_deadline()
        if not isinstance(action, dict):
            continue
        script = _in_repo(action.get("script"), repo)
        ended = _ended(action)
        if script is None or ended is None:
            continue
        failed = (action.get("bash_exit") not in (0, None)
                  or action.get("tool_outcome") == "failure")
        outputs = action.get("outputs") if isinstance(action.get("outputs"), list) else []
        # Name a writer only when the observer saw its call alone: with an
        # overlapping or background call, the change may be the other's.
        alone = action.get("observation_status") == "no-known-overlap"
        tables = [] if failed or not alone else [
            t for t in (table(o) for o in outputs) if t]
        observation = action.get("observation_id")
        if isinstance(observation, str):
            tables = [t for t in tables if shared.get((observation, t), 0) <= 1]
        earlier = last.get(script)
        if earlier is not None and earlier.ended > ended:
            continue
        if not tables:
            # A later run that wrote no table supersedes an earlier one that did.
            last.pop(script, None)
            continue
        previous = [t for t in ends if t < ended]
        sha = action.get("script_sha256")
        last[script] = _Step(script, ended, previous[-1] if previous else None, tables,
                             sha if isinstance(sha, str) and _SHA.match(sha) else "")
    return list(last.values())


def _contained(path: Path, repo: Path) -> bool:
    """`path` is inside `repo` with no symlink on the way (lstat only, so a
    link to an unavailable target is never followed)."""
    try:
        parts = path.relative_to(repo).parts
    except ValueError:
        return False
    current = repo
    for part in parts:
        current = current / part
        if os.path.islink(current):
            return False
    return True


def _candidates(repo: Path, steps: list[_Step]) -> tuple[list[Path], list[Path]]:
    """The steps' own access files, then every other existing one, without
    following any symlink. More than MAX_ACCESS_FILES existing files, or
    more than MAX_ANALYSIS_DIRS directories to look in, is an incomplete
    scan: unknown."""
    own: list[Path] = []
    for step in steps:
        if step.root is not None:
            path = step.root / "outputs" / "access.json"
            if path not in own and _contained(path, repo):
                own.append(path)
    others: list[Path] = []
    analysis = repo / "analysis"
    if _contained(analysis, repo) and os.path.isdir(analysis):
        walked = 0
        with os.scandir(analysis) as entries:
            for entry in entries:
                walked += 1
                if walked > MAX_ANALYSIS_DIRS:
                    raise _Unknown("too many analysis directories")
                if not entry.is_dir(follow_symlinks=False):
                    continue
                path = Path(entry.path) / "outputs" / "access.json"
                if path in own or not _contained(path, repo) or not os.path.lexists(path):
                    continue
                others.append(path)
                if len(own) + len(others) > MAX_ACCESS_FILES:
                    raise _Unknown("too many access files")
    if len(own) + len(others) > MAX_ACCESS_FILES:
        raise _Unknown("too many access files")
    return own, sorted(others)


def _read_bounded(path: Path, limit: int) -> bytes | None:
    """The bytes of a regular file opened once (no symlink, no blocking on a
    FIFO), or None when it is missing, not regular, or larger than `limit`."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            return None
        chunks, remaining = [], limit + 1
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        return None if len(data) > limit else data
    except OSError:
        return None
    finally:
        os.close(fd)


def _analysis_root(script: Path) -> Path | None:
    parts = script.parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "analysis":
            return Path(*parts[: i + 2])
    return None


class _Records:
    """Access records read under a byte and time budget."""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.total = 0
        self.files = 0
        # (file's analysis root, script named by computed_at or None, start)
        self.runs: list[tuple[Path, Path | None, datetime.datetime | None]] = []
        self.unknown_roots: set[Path] = set()

    def read(self, path: Path, own: bool) -> None:
        if time.monotonic() > self.deadline:
            raise _Unknown("budget")
        self.files += 1
        root = path.parent.parent
        if not os.path.lexists(path):
            return  # no records here
        limit = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - self.total)
        data = _read_bounded(path, limit)
        if data is None:
            # Not a regular file, unreadable, or past the budget: unknown.
            if own:
                self.unknown_roots.add(root)
                return
            raise _Unknown("unreadable or over budget")
        self.total += len(data)
        try:
            document = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            if own:
                self.unknown_roots.add(root)
            return
        if not isinstance(document, dict) or document.get("schema") != ACCESS_SCHEMA:
            return  # not Mycelium's file (record_ids refuses to touch it)
        records = document.get("records")
        if not isinstance(records, list):
            if own:
                self.unknown_roots.add(root)
            return
        sites: dict[str, Path | None] = {}
        for record in records:
            if not isinstance(record, dict):
                continue
            run = record.get("run")
            started = _parse_ts(run.get("process_started_at")) if isinstance(run, dict) else None
            site = record.get("computed_at")
            if not isinstance(site, str):
                named = None
            elif site in sites:
                named = sites[site]
            else:
                where = _LINE_SUFFIX.sub("", site)
                named = None
                if where:
                    candidate = Path(where)
                    named = Path(os.path.normpath(
                        candidate if candidate.is_absolute() else root / candidate))
                sites[site] = named
            self.runs.append((root, named, started))


def _covered(step: _Step, records: _Records, since: datetime.datetime | None) -> bool:
    """A record whose process started inside this run's window: after the
    previous recorded call ended (or, with none, after the session started)
    and before this run ended. Whatever its file or call site: a helper
    module, R recording into another namespace, `computed_at: "R"`. With
    no lower edge at all, any earlier record may be this run's."""
    if step.root is not None and step.root in records.unknown_roots:
        return True  # unknown: say nothing
    latest = step.ended + CLOCK_SLACK
    lower = step.previous_end if step.previous_end is not None else since
    earliest = lower - CLOCK_SLACK if lower is not None else None
    for _root, _named, started in records.runs:
        if started is None or started > latest:
            continue
        if earliest is None or started >= earliest:
            return True
    return False


def _read_seen(path: Path | None) -> set[str]:
    if path is None:
        return set()
    data = _read_bounded(path, SEEN_MAX_BYTES)
    try:
        return set(data.decode("utf-8").splitlines()) if data else set()
    except UnicodeDecodeError:
        return set()


def remember(seen_path: Path, keys: list[str]) -> None:
    """Add keys to the seen cache, keeping the newest SEEN_KEEP lines. The
    old file is read as a regular file only; the new one is an exclusively
    created temporary file renamed over it. On any doubt, nothing is
    written."""
    keys = [k for k in keys if k and "\n" not in k and "\t" in k]
    if not keys:
        return
    existing: list[str] = []
    if os.path.lexists(seen_path):
        data = _read_bounded(seen_path, SEEN_MAX_BYTES)
        if data is None:
            return  # not a regular file, or too large: leave it alone
        try:
            existing = data.decode("utf-8").splitlines()
        except UnicodeDecodeError:
            return
    lines = [line for line in existing if line not in keys] + keys
    payload = ("\n".join(lines[-SEEN_KEEP:]) + "\n").encode("utf-8")
    temp = seen_path.with_name(f".{seen_path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(temp, flags, 0o644)
    except OSError:
        return
    try:
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)
        os.replace(temp, seen_path)
    except OSError:
        try:
            os.unlink(temp)
        except OSError:
            pass


def _key(step: _Step, repo: Path) -> str:
    return f"{_rel(step.script, repo)}\t{step.script_sha}"


def _note(missing: list[_Step], repo: Path) -> tuple[str, int]:
    """The note and how many scripts it names."""
    for listed in range(min(MAX_LISTED, len(missing)), 0, -1):
        items = []
        for step in missing[:listed]:
            tables = ", ".join(_short(_rel(t, repo)) for t in step.tables[:MAX_TABLES_LISTED])
            if len(step.tables) > MAX_TABLES_LISTED:
                tables += f" (+{len(step.tables) - MAX_TABLES_LISTED})"
            items.append(f"{_short(_rel(step.script, repo))} -> {tables}")
        more = f" (+{len(missing) - listed} more)" if len(missing) > listed else ""
        note = (
            f"ACCESS NOTE: {len(missing)} script(s) this session wrote a table and recorded "
            f"no row or feature access: {'; '.join(items)}{more}. Where a script filters, "
            "subsets or splits rows or features, consider record_ids there "
            "(register_value.py; record_access.R in R). Advisory; nothing to redo."
        )
        if len(note.encode("utf-8")) <= MAX_NOTE_BYTES:
            return note, listed
    return "", 0


def note_lines(manifest_path: Path, repo_root: Path, since: float | None = None,
               seen_path: Path | None = None) -> list[str]:
    """The note and its key lines, or [] (also on any error or overrun)."""
    deadline = time.monotonic() + TIME_BUDGET_SECONDS
    try:
        repo = _resolve(Path(repo_root))
        data = _read_bounded(Path(manifest_path), MAX_TOTAL_BYTES)
        if data is None:
            return []
        manifest = json.loads(data.decode("utf-8"))
        seen = _read_seen(seen_path)
        steps = [s for s in _table_steps(manifest, repo, deadline)
                 if _key(s, repo) not in seen]
        if not steps:
            return []
        since_at = (datetime.datetime.fromtimestamp(since, datetime.timezone.utc)
                    if since is not None else None)
        records = _Records(deadline)
        own, others = _candidates(repo, steps)
        for step in steps:
            # A step whose own access file sits behind a symlink or outside
            # the repository cannot be judged.
            if step.root is not None and step.root / "outputs" / "access.json" not in own:
                records.unknown_roots.add(step.root)
        for path in own:
            records.read(path, own=True)
        for path in others:
            records.read(path, own=False)
        missing = [s for s in steps if not _covered(s, records, since_at)]
        missing.sort(key=lambda s: s.ended)
        note, listed = _note(missing, repo) if missing else ("", 0)
        if not note:
            return []
        # Only the scripts the note names are remembered as told.
        return [note] + [f"key\t{_key(s, repo)}" for s in missing[:listed]]
    except Exception:  # noqa: BLE001 — Stop must fail open; _Unknown included.
        return []


def note_for(manifest_path: Path, repo_root: Path, since: float | None = None,
             seen_path: Path | None = None) -> str:
    """Just the note text, or ""."""
    lines = note_lines(manifest_path, repo_root, since, seen_path)
    return lines[0] if lines else ""


def _deadline(*_: Any) -> None:
    os._exit(0)  # a hard stop prints nothing: the note is unknown


def main(argv: list[str] | None = None) -> int:
    try:
        if hasattr(signal, "setitimer"):
            signal.signal(signal.SIGALRM, _deadline)
            signal.setitimer(signal.ITIMER_REAL, HARD_DEADLINE_SECONDS)
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--remember")
        parser.add_argument("--manifest")
        parser.add_argument("--repo-root")
        parser.add_argument("--since", type=float)
        parser.add_argument("--seen")
        args, rest = parser.parse_known_args(argv)
        if args.remember:
            # The accepting Stop passes the keys of the note it showed.
            remember(Path(args.remember), rest)
            return 0
        if not args.manifest or not args.repo_root:
            return 0
        lines = note_lines(Path(args.manifest), Path(args.repo_root), args.since,
                           Path(args.seen) if args.seen else None)
        if lines:
            # One write, so a deadline cannot leave half a note.
            sys.stdout.write("".join(line + "\n" for line in lines))
            sys.stdout.flush()
    except BaseException:  # noqa: BLE001 — argparse exits; Stop must fail open.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
