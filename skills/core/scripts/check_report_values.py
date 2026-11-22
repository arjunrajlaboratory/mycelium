#!/usr/bin/env python3
"""check_report_values — flag report numbers whose source file has changed.

A number registered with ``register_value(..., source=...)`` records the
repository-relative path, SHA-256 and locator of the file it was read from.
This checker recomputes each source's hash and reports:

=====================  ========  ===========================================
finding                severity  meaning
=====================  ========  ===========================================
``stale-value``        error     the source file's hash differs from the one
                                 recorded when the number was registered
``source-missing``     error     the source file is gone
``source-invalid``     error     malformed path (absolute, ``..``, a
                                 symlinked component), bad hash, or no
                                 locator
``source-unreadable``  error     the file exists but cannot be hashed (not a
                                 regular file, changed while read)
``unchecked-value``    warning   no ``source`` (legacy, or never linked);
                                 an error under ``--strict``
=====================  ========  ===========================================

The fix for a stale number is always to rerun the analysis that registers
it and redo the report's Phase 1 merge. Refreshing the recorded hash would
bless a number computed from bytes that no longer exist.

Used three ways, with one implementation:

- standalone: ``check_report_values.py --manifest reports/.manifest.json``
  (or ``--fragment outputs/numbers.json``); the TeX gate runs this before
  rendering macros;
- from ``sync_html_report.py``: error findings block the sync;
- from ``check_html_report.py --manifest``: findings join the HTML gate.

Exit codes: 0 clean, 1 any error (or any warning with ``--strict``), 2 bad
input. ``--explain`` names, for a stale source, the recorded call that
wrote its current content, from the lineage index (read-only: it never
writes the index cache).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

SHA_RX = re.compile(r"[0-9a-f]{64}")
LOCATOR_KEYS = frozenset({"row", "column", "key", "sheet", "cell", "query"})
FINDING_SCHEMA = "mycelium.report_finding.v1"


def resolve_repo_root(start: Path, explicit: Path | None = None) -> Path:
    """The frame number sources are recorded in: ``explicit`` if given,
    else the nearest ancestor of ``start`` with ``.living/``, else the git
    toplevel, else ``start``. ``register_value`` uses the same order."""
    if explicit is not None:
        return Path(os.path.abspath(explicit))
    start = Path(os.path.abspath(start))
    for candidate in (start, *start.parents):
        if (candidate / ".living").is_dir():
            return candidate
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    try:
        top = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=5)
        if top.returncode == 0 and top.stdout.strip():
            return Path(top.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def _finding(code: str, severity: str, number_id: Any, message: str, **extra: Any) -> dict:
    return {"schema": FINDING_SCHEMA, "code": code, "severity": severity,
            "id": number_id, "message": message, **extra}


def _valid_locator(locator: Any) -> bool:
    if isinstance(locator, str):
        return bool(locator.strip())
    if isinstance(locator, dict):
        return bool(locator) and not set(locator) - LOCATOR_KEYS and all(
            isinstance(v, (str, int)) and str(v) != "" for v in locator.values())
    return False


def _stable_sha256(path: Path) -> tuple[str | None, str | None]:
    """(digest, problem): the file's hash, only if its identity did not
    change while it was read."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, f"cannot open: {exc.strerror or exc}"
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None, "not a regular file"
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        ident = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)  # noqa: E731
        try:
            on_path = os.stat(path, follow_symlinks=False)
        except OSError:
            return None, "changed while it was read"
        if ident(before) != ident(after) or ident(before) != ident(on_path):
            return None, "changed while it was read"
        return digest.hexdigest(), None
    finally:
        os.close(fd)


def _source_path(repo_root: Path, rel: Any) -> tuple[Path | None, str | None]:
    if not isinstance(rel, str) or not rel:
        return None, "source has no path"
    if os.path.isabs(rel) or "\0" in rel or ".." in Path(rel).parts:
        return None, f"source path {rel!r} must be repository-relative without `..`"
    current = Path(os.path.realpath(repo_root))
    for part in Path(rel).parts:
        current = current / part
        if current.is_symlink():
            return None, f"source path {rel!r} goes through a symlink ({part})"
    return current, None


def check_numbers(entries: Iterable[Any], repo_root: Path, *,
                  explain: bool = False) -> list[dict[str, Any]]:
    """One finding per number that is not verifiably current. Accepts
    fragment entries (``key``) and manifest entries (``id``)."""
    findings: list[dict[str, Any]] = []
    index = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        number_id = entry.get("id", entry.get("key"))
        sources = entry.get("sources")
        if sources is not None and "source" in entry:
            # Which one is the dependency? A stale `source` must not hide
            # behind a fresh `sources` (or the reverse).
            findings.append(_finding("source-invalid", "error", number_id,
                                     "the entry has both source and sources"))
            continue
        if sources is not None:
            # A number linked to several files: each is checked on its own,
            # and any one stale, missing or invalid fails the number.
            if not isinstance(sources, list) or not sources:
                findings.append(_finding("source-invalid", "error", number_id,
                                         "sources must be a non-empty list"))
                continue
            for item in sources:
                if not isinstance(item, dict):
                    # A null or malformed member is a missing dependency, not
                    # an unlinked number: it fails, it does not warn.
                    findings.append(_finding("source-invalid", "error", number_id,
                                             "a member of sources is not a source object"))
                else:
                    findings.extend(check_numbers([{"id": number_id, "source": item}],
                                                  repo_root, explain=explain))
            continue
        source = entry.get("source")
        if source is None:
            findings.append(_finding(
                "unchecked-value", "warning", number_id,
                "no source recorded; this number cannot be checked against its data "
                "(register it with source=... to link it)"))
            continue
        if not isinstance(source, dict):
            findings.append(_finding("source-invalid", "error", number_id,
                                     "source must be an object"))
            continue
        rel = source.get("path")
        expected = source.get("sha256")
        if not isinstance(expected, str) or not SHA_RX.fullmatch(expected):
            findings.append(_finding("source-invalid", "error", number_id,
                                     f"source {rel!r} has no valid sha256", path=rel))
            continue
        if not _valid_locator(source.get("locator")):
            findings.append(_finding("source-invalid", "error", number_id,
                                     f"source {rel!r} has no valid locator", path=rel))
            continue
        path, problem = _source_path(repo_root, rel)
        if path is None:
            findings.append(_finding("source-invalid", "error", number_id, problem, path=rel))
            continue
        actual, problem = _stable_sha256(path)
        if problem == "missing":
            finding = _finding(
                "source-missing", "error", number_id,
                f"source {rel} no longer exists; rerun the analysis that registers "
                f"{number_id!r}, then redo the Phase 1 merge", path=rel, expected=expected)
            if explain:
                index = index or _lineage(repo_root)
                finding["lineage"] = _explain(index, rel, None)
            findings.append(finding)
            continue
        if actual is None:
            findings.append(_finding("source-unreadable", "error", number_id,
                                     f"source {rel}: {problem}", path=rel))
            continue
        if actual != expected:
            finding = _finding(
                "stale-value", "error", number_id,
                f"{rel} changed since {number_id!r} was registered "
                f"(recorded {expected[:12]}…, now {actual[:12]}…); rerun the analysis "
                "that registers it and redo the Phase 1 merge. Do not refresh the hash: "
                "that would bless a number computed from bytes that no longer exist.",
                path=rel, expected=expected, actual=actual)
            if explain:
                index = index or _lineage(repo_root)
                finding["lineage"] = _explain(index, rel, actual)
            findings.append(finding)
    return findings


def _lineage(repo_root: Path):
    try:
        import lineage_index

        return lineage_index.load(repo_root, write=False)
    except Exception:  # noqa: BLE001 - explanation is optional
        return None


def _explain(index, rel: str, actual: str | None) -> dict[str, Any] | None:
    """The recorded call that wrote the bytes now on disk, when the record
    can establish it: an observed write whose hash is exactly ``actual``.
    Otherwise says so, rather than naming the last write it happens to see."""
    if index is None:
        return None
    try:
        import lineage_index

        result = lineage_index.query_path(index, rel, disk=False)
    except Exception:  # noqa: BLE001
        return None
    writes = result.get("writes") or []
    if not writes:
        return {"status": "no-recorded-write"}
    if actual is None:
        deletions = [w for w in writes if w.get("action") == "deleted"]
        if deletions:
            last = deletions[-1]
            return {"status": "deleted-in-record", "write_call": last.get("call"),
                    "events": [e.get("event_id") for e in last.get("events") or []]}
        return {"status": "no-recorded-deletion"}
    matching = [w for w in writes if w.get("sha256") == actual and w.get("evidence") == "observed"]
    if not matching:
        return {"status": "current-bytes-not-in-record",
                "detail": "no recorded write produced the bytes now on disk; they changed "
                          "outside the record"}
    if len(matching) > 1:
        return {"status": "producer-ambiguous", "calls": [w.get("call") for w in matching]}
    write = matching[0]
    return {"status": "written-by", "write_call": write.get("call"), "sha256": actual,
            "events": [e.get("event_id") for e in write.get("events") or []],
            "verdict": write.get("record", {}).get("status")}


def number_entries(document: Any) -> list[Any]:
    """The number entries of a report manifest (``numbers``) or a fragment
    (``values``)."""
    if not isinstance(document, dict):
        raise ValueError("expected a JSON object")
    for key in ("numbers", "values"):
        if key in document:
            entries = document[key]
            if entries is None:
                return []  # a legacy figure-only manifest: no numbers
            if not isinstance(entries, list):
                raise ValueError(f"'{key}' must be a list")
            return entries
    return []


def has_errors(findings: list[dict[str, Any]], *, strict: bool = False) -> bool:
    return any(f["severity"] == "error" or (strict and f["severity"] == "warning")
               for f in findings)


def format_finding(finding: dict[str, Any], label: str) -> str:
    return f"{label}: [{finding['code']}] {finding['severity']}: {finding['message']}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--manifest", type=Path, help="report .manifest.json")
    target.add_argument("--fragment", type=Path, help="analysis outputs/numbers.json")
    parser.add_argument("--repo-root", type=Path, default=None,
                        help="Project root the source paths are relative to "
                             "(default: nearest ancestor with .living/).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true",
                        help="Treat unchecked (unlinked) numbers as errors.")
    parser.add_argument("--explain", action="store_true",
                        help="For stale or missing sources, name the recorded call that "
                             "wrote the current content (read-only lineage lookup).")
    args = parser.parse_args(argv)
    doc_path = args.manifest or args.fragment
    try:
        document = json.loads(doc_path.read_text(encoding="utf-8"))
        entries = number_entries(document)
    except FileNotFoundError:
        print(f"not found: {doc_path}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"{doc_path}: {exc}", file=sys.stderr)
        return 2
    repo_root = resolve_repo_root(doc_path.parent, args.repo_root)
    findings = check_numbers(entries, repo_root, explain=args.explain)
    if args.json:
        print(json.dumps({"document": str(doc_path), "repo_root": str(repo_root),
                          "numbers": len(entries), "findings": findings}, indent=2))
    else:
        for finding in findings:
            print(format_finding(finding, f"{doc_path}:{finding.get('id')}"))
            lineage = finding.get("lineage")
            if lineage:
                calls = lineage.get("write_call") or ", ".join(lineage.get("calls") or [])
                print(f"    lineage: {lineage.get('status')}"
                      + (f" ({calls})" if calls else ""))
        errors = sum(f["severity"] == "error" for f in findings)
        warnings = sum(f["severity"] == "warning" for f in findings)
        print(f"{doc_path}: {len(entries)} number(s), {errors} error(s), {warnings} warning(s)",
              file=sys.stderr)
    return 1 if has_errors(findings, strict=args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
