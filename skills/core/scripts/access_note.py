#!/usr/bin/env python3
"""Stop-time access note: a table-writing step that recorded no access.

Reads the lineage manifest the Stop phase just wrote and the project's
``analysis/*/outputs/access.json`` files (written by ``record_ids`` in
``register_value.py`` and by ``record_access.R``). Prints one advisory
paragraph when a proven analysis step this transaction wrote a table but no
access record joins to it, and nothing otherwise.

What counts as "wrote a table", conservatively:

- a manifest action with a script file (inline runs cannot be joined);
- that did not fail (``bash_exit`` 0 or null, ``tool_outcome`` not failure);
- with an output whose name ends in .csv, .tsv (optionally .gz), .parquet or
  .h5ad, outside ``.living/`` and ``.mycelium/``;
- that the observer saw created or modified. An unobserved output is not
  evidence of a write, so it never triggers the note.

A record joins an action when its ``computed_at`` (``<path>[:L<line>]``,
relative to its ``analysis/<ns>/`` directory, or absolute) names the
action's script and its ``run.process_started_at`` is no later than the
action's ``ts`` (when the call ended) plus a small clock slack. Records
carry no per-call id, so an older record from the same script also joins:
the note errs toward silence. An unreadable access.json makes the answer
unknown, so it suppresses the note too.

Advisory only: the caller appends the text to Stop's output and never
blocks on it. Every error prints nothing and exits 0.

Usage: access_note.py --manifest PATH --repo-root DIR
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

TABLE_SUFFIXES = (".csv", ".tsv", ".csv.gz", ".tsv.gz", ".parquet", ".h5ad")
WRITE_CHANGES = frozenset({"created", "modified"})
EXCLUDED_TOP = frozenset({".living", ".mycelium", ".git"})
CLOCK_SLACK = datetime.timedelta(seconds=2)
MAX_ACCESS_FILES = 200
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_LISTED = 5
_LINE_SUFFIX = re.compile(r":L\d+$")


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
        return path


def _in_repo(path_text: Any, repo: Path) -> Path | None:
    if not isinstance(path_text, str) or not path_text:
        return None
    path = Path(path_text)
    return _resolve(path if path.is_absolute() else repo / path)


def _display(path: Path, repo: Path) -> str:
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


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


def _table_steps(manifest: Any, repo: Path) -> list[tuple[Path, datetime.datetime, list[Path]]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("actions"), list):
        return []
    steps = []
    for action in manifest["actions"]:
        if not isinstance(action, dict):
            continue
        if action.get("bash_exit") not in (0, None) or action.get("tool_outcome") == "failure":
            continue
        script = _in_repo(action.get("script"), repo)
        ended = _parse_ts(action.get("ts"))
        outputs = action.get("outputs")
        if script is None or ended is None or not isinstance(outputs, list):
            continue
        tables = [t for t in (_is_table(o, repo) for o in outputs) if t is not None]
        if tables:
            steps.append((script, ended, tables))
    return steps


def _analysis_root(script: Path) -> Path | None:
    parts = script.parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "analysis":
            return Path(*parts[: i + 2])
    return None


def _access_files(repo: Path, scripts: set[Path]) -> list[Path]:
    found: list[Path] = []
    for script in sorted(scripts):
        root = _analysis_root(script)
        if root is not None:
            found.append(root / "outputs" / "access.json")
    analysis = repo / "analysis"
    if analysis.is_dir():
        found.extend(sorted(analysis.glob("*/outputs/access.json")))
    unique: list[Path] = []
    for path in found:
        if path not in unique:
            unique.append(path)
    return unique[:MAX_ACCESS_FILES]


def _joined_runs(path: Path) -> list[tuple[Path, datetime.datetime | None]]:
    """(script, process start) of each record in one access.json."""
    if not path.exists():
        return []
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES:
            raise _Unknown(path)
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise _Unknown(path) from exc
    if not isinstance(document, dict) or not isinstance(document.get("records"), list):
        raise _Unknown(path)
    analysis_root = path.parent.parent
    runs = []
    for record in document["records"]:
        if not isinstance(record, dict) or not isinstance(record.get("computed_at"), str):
            continue
        site = _LINE_SUFFIX.sub("", record["computed_at"])
        if not site:
            continue
        where = Path(site)
        script = _resolve(where if where.is_absolute() else analysis_root / where)
        run = record.get("run")
        started = _parse_ts(run.get("process_started_at")) if isinstance(run, dict) else None
        runs.append((script, started))
    return runs


def note_for(manifest_path: Path, repo_root: Path) -> str:
    """The advisory note for one manifest, or "" (also on any error)."""
    try:
        repo = _resolve(Path(repo_root))
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        steps = _table_steps(manifest, repo)
        if not steps:
            return ""
        runs: list[tuple[Path, datetime.datetime | None]] = []
        for path in _access_files(repo, {script for script, _, _ in steps}):
            runs.extend(_joined_runs(path))
        missing: dict[Path, list[Path]] = {}
        for script, ended, tables in steps:
            if any(s == script and (started is None or started <= ended + CLOCK_SLACK)
                   for s, started in runs):
                continue
            listed = missing.setdefault(script, [])
            listed.extend(t for t in tables if t not in listed)
        if not missing:
            return ""
        items = [f"{_display(script, repo)} -> {', '.join(_display(t, repo) for t in tables[:3])}"
                 + (f" (+{len(tables) - 3})" if len(tables) > 3 else "")
                 for script, tables in list(missing.items())[:MAX_LISTED]]
        more = f" (+{len(missing) - MAX_LISTED} more)" if len(missing) > MAX_LISTED else ""
        return (
            f"ACCESS NOTE: {len(missing)} step(s) this turn wrote a table but recorded no "
            f"row or feature access: {'; '.join(items)}{more}. If a step filters, subsets or "
            "splits rows or features, call record_ids there (register_value.py; "
            "record_access.R in R) so the sets it used and excluded are on record. "
            "Advisory only; Stop is not blocked."
        )
    except _Unknown:
        return ""
    except Exception:  # noqa: BLE001 — Stop must fail open.
        return ""


def main(argv: list[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--manifest", required=True)
        parser.add_argument("--repo-root", required=True)
        args, _ = parser.parse_known_args(argv)
        note = note_for(Path(args.manifest), Path(args.repo_root))
        if note:
            sys.stdout.write(note + "\n")
    except BaseException:  # noqa: BLE001 — argparse exits; Stop must fail open.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
