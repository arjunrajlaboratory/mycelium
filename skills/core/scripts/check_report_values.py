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
                                 symlinked component), bad hash, no
                                 locator, an explicit ``null`` source or
                                 sources, or a number entry that is not
                                 an object
``source-unreadable``  error     the file exists but cannot be hashed (not a
                                 regular file, changed while read)
``unchecked-value``    warning   neither a ``source`` nor a ``sources``
                                 field (legacy, or never linked); an error
                                 under ``--strict``
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
writes the index cache). A call is named only when its index verdict says
it is still current; a write with the same bytes that a later recorded
write superseded is reported (``current-bytes-match-superseded-write``)
but not credited.
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


def _identity(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class _HashCache:
    """Digests already computed in one check, by the file's stable identity
    (its real path plus dev/ino/size/mtime_ns/ctime_ns): many numbers come
    from one table, and each source is hashed once. A cached digest is
    reused only after a fresh stat shows the file still has that identity."""

    def __init__(self) -> None:
        self._digests: dict[tuple[str, tuple[int, int, int, int, int]], str] = {}
        self.hashed = 0  # files actually read, for tests

    def sha256(self, path: Path) -> tuple[str | None, str | None]:
        real = os.path.realpath(path)
        try:
            st = os.stat(path, follow_symlinks=False)
        except FileNotFoundError:
            return None, "missing"
        except OSError:
            st = None
        if st is not None and stat.S_ISREG(st.st_mode):
            cached = self._digests.get((real, _identity(st)))
            if cached is not None:
                try:
                    again = os.stat(path, follow_symlinks=False)
                except OSError:
                    again = None
                if again is not None and _identity(again) == _identity(st):
                    return cached, None
        self.hashed += 1
        digest, problem, ident = _stable_sha256_ident(path)
        if digest is not None and ident is not None:
            self._digests[(real, ident)] = digest
        return digest, problem


def _stable_sha256(path: Path) -> tuple[str | None, str | None]:
    """(digest, problem): the file's hash, only if its identity did not
    change while it was read."""
    digest, problem, _ = _stable_sha256_ident(path)
    return digest, problem


def _stable_sha256_ident(path: Path) -> tuple[str | None, str | None,
                                              tuple[int, int, int, int, int] | None]:
    """(digest, problem, identity) as ``_stable_sha256``, plus the identity
    the file had while it was read."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
                     | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None, "missing", None
    except OSError as exc:
        return None, f"cannot open: {exc.strerror or exc}", None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            return None, "not a regular file", None
        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(fd)
        try:
            on_path = os.stat(path, follow_symlinks=False)
        except OSError:
            return None, "changed while it was read", None
        if _identity(before) != _identity(after) or _identity(before) != _identity(on_path):
            return None, "changed while it was read", None
        return digest.hexdigest(), None, _identity(before)
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


class _Check:
    """State shared by one check: the hash cache and the lineage index."""

    def __init__(self, repo_root: Path, explain: bool) -> None:
        self.repo_root = repo_root
        self.explain = explain
        self.hashes = _HashCache()
        self._index: Any = None
        self._index_loaded = False

    def index(self):
        if not self._index_loaded:
            self._index = _lineage(self.repo_root)
            self._index_loaded = True
        return self._index


def check_numbers(entries: Iterable[Any], repo_root: Path, *,
                  explain: bool = False) -> list[dict[str, Any]]:
    """One finding per number that is not verifiably current. Accepts
    fragment entries (``key``) and manifest entries (``id``). Each source
    file is hashed once per call, however many numbers name it."""
    ctx = _Check(repo_root, explain)
    findings: list[dict[str, Any]] = []
    for entry in entries:
        findings.extend(_check_entry(entry, ctx))
    return findings


def _check_entry(entry: Any, ctx: _Check) -> list[dict[str, Any]]:
    if not isinstance(entry, dict):
        # A null or non-object member of `numbers` is a malformed record, not
        # an absent one: it fails rather than vanishing from the check.
        return [_finding("source-invalid", "error", None,
                         f"a number entry is not an object (got {json.dumps(entry)[:40]})")]
    number_id = entry.get("id", entry.get("key"))
    has_sources, has_source = "sources" in entry, "source" in entry
    if has_sources and has_source:
        # Which one is the dependency? A stale `source` must not hide
        # behind a fresh `sources` (or the reverse).
        return [_finding("source-invalid", "error", number_id,
                         "the entry has both source and sources")]
    if not has_sources and not has_source:
        # Only a record with neither field is an unlinked (legacy) number.
        return [_finding(
            "unchecked-value", "warning", number_id,
            "no source recorded; this number cannot be checked against its data "
            "(register it with source=... to link it)")]
    if has_sources:
        sources = entry["sources"]
        # A number linked to several files: each is checked on its own,
        # and any one stale, missing or invalid fails the number. An
        # explicit null is a broken link, never an unlinked number.
        if not isinstance(sources, list) or not sources:
            return [_finding("source-invalid", "error", number_id,
                             "sources must be a non-empty list"
                             + (" (it is null)" if sources is None else ""))]
        findings: list[dict[str, Any]] = []
        for item in sources:
            if not isinstance(item, dict):
                # A null or malformed member is a missing dependency, not
                # an unlinked number: it fails, it does not warn.
                findings.append(_finding("source-invalid", "error", number_id,
                                         "a member of sources is not a source object"))
            else:
                findings.extend(_check_source(number_id, item, ctx))
        return findings
    source = entry["source"]
    if not isinstance(source, dict):
        return [_finding("source-invalid", "error", number_id,
                         "source must be an object" + (" (it is null)" if source is None else ""))]
    return _check_source(number_id, source, ctx)


def _check_source(number_id: Any, source: dict, ctx: _Check) -> list[dict[str, Any]]:
    rel = source.get("path")
    expected = source.get("sha256")
    if not isinstance(expected, str) or not SHA_RX.fullmatch(expected):
        return [_finding("source-invalid", "error", number_id,
                         f"source {rel!r} has no valid sha256", path=rel)]
    if not _valid_locator(source.get("locator")):
        return [_finding("source-invalid", "error", number_id,
                         f"source {rel!r} has no valid locator", path=rel)]
    path, problem = _source_path(ctx.repo_root, rel)
    if path is None:
        return [_finding("source-invalid", "error", number_id, problem, path=rel)]
    actual, problem = ctx.hashes.sha256(path)
    if problem == "missing":
        finding = _finding(
            "source-missing", "error", number_id,
            f"source {rel} no longer exists; rerun the analysis that registers "
            f"{number_id!r}, then redo the Phase 1 merge", path=rel, expected=expected)
        if ctx.explain:
            finding["lineage"] = _explain(ctx.index(), rel, None)
        return [finding]
    if actual is None:
        return [_finding("source-unreadable", "error", number_id,
                         f"source {rel}: {problem}", path=rel)]
    if actual != expected:
        finding = _finding(
            "stale-value", "error", number_id,
            f"{rel} changed since {number_id!r} was registered "
            f"(recorded {expected[:12]}…, now {actual[:12]}…); rerun the analysis "
            "that registers it and redo the Phase 1 merge. Do not refresh the hash: "
            "that would bless a number computed from bytes that no longer exist.",
            path=rel, expected=expected, actual=actual)
        if ctx.explain:
            finding["lineage"] = _explain(ctx.index(), rel, actual)
        return [finding]
    return []


def _lineage(repo_root: Path):
    try:
        import lineage_index

        return lineage_index.load(repo_root, write=False)
    except Exception:  # noqa: BLE001 - explanation is optional
        return None


# The index verdict (`record.status` of a write, read with disk=False) that
# lets a write be credited with what the path holds now: no later recorded
# write replaced it and nothing left its currency unproven.
_CURRENT_VERDICTS = frozenset({"current-in-record", "current"})


def _verdict(write: dict[str, Any]) -> str | None:
    return (write.get("record") or {}).get("status")


def _uncredited(status: str, writes: list[dict[str, Any]], detail: str) -> dict[str, Any]:
    """Writes that match the evidence but whose verdict does not make them
    the current producer: listed with their verdicts, never as a producer."""
    return {"status": status, "detail": detail,
            "matching_writes": [{"call": w.get("call"), "verdict": _verdict(w)}
                                for w in writes]}


def _resolve_pending_writes(index, rel: str, writes: list[dict[str, Any]],
                            actual: str) -> list[dict[str, Any]]:
    """``writes`` with each observed write whose hash was pending (or
    unavailable) at Post resolved by the index's identity match: when the
    file still has exactly the identity the observer recorded and hashes to
    ``actual``, that write produced the current bytes, and its verdict is
    re-derived with the resolved hash. A write that cannot be resolved keeps
    its unknown hash."""
    try:
        raw = index.writes_at(rel)
    except Exception:  # noqa: BLE001 - no raw writes, nothing to resolve
        return writes
    if len(raw) != len(writes):
        return writes
    out = []
    for summary, write in zip(writes, raw):
        if (summary.get("sha256") is None and summary.get("action") != "deleted"
                and summary.get("evidence") == "observed" and write.get("identity")
                and write.get("frame") == "repo" and write.get("path") == rel):
            try:
                disk = index.disk_status(rel, None, identity=write["identity"])
                if disk.get("resolved_sha256") == actual:
                    record = index.record_status({**write, "sha256": actual})
                    summary = {**summary, "sha256": actual, "record": record,
                               "hash_origin": "query-identity-match"}
            except Exception:  # noqa: BLE001 - unresolved stays unknown
                pass
        out.append(summary)
    return out


def _incomplete_reasons(index, rel: str, writes: list[dict[str, Any]]) -> list[str]:
    """Why the record cannot show it holds every change to ``rel``: a write
    whose resulting bytes are unknown and not provably replaced, a gap that
    applies to the path, or a path Bash observation never watches. Only with
    none of these may a change be called outside the record."""
    reasons = []
    if any(w.get("sha256") is None and w.get("action") != "deleted"
           and _verdict(w) != "superseded" for w in writes):
        reasons.append("write-hash-unknown")
    try:
        import lineage_index

        if any(lineage_index.Index._gap_applies(g, rel) for g in getattr(index, "gaps", [])):
            reasons.append("gap")
    except Exception:  # noqa: BLE001 - cannot rule a gap out
        reasons.append("gap")
    try:
        import io_observer

        if io_observer.watch_status(rel, []) != "watched":
            reasons.append("unwatched-path")
    except Exception:  # noqa: BLE001
        reasons.append("unwatched-path")
    return reasons


def _producer_unknown(reasons: list[str], writes: list[dict[str, Any]],
                      detail: str) -> dict[str, Any]:
    return {"status": "producer-unknown", "reasons": reasons, "detail": detail,
            "writes": [{"call": w.get("call"), "sha256": w.get("sha256"),
                        "hash_status": w.get("hash_status"), "verdict": _verdict(w)}
                       for w in writes]}


def _explain(index, rel: str, actual: str | None) -> dict[str, Any] | None:
    """The recorded call that left the path as it is now, when the record
    can establish it: an observed write (or deletion) whose result is
    exactly what is on disk *and* whose index verdict says no later recorded
    write replaced it. A matching but superseded write is reported as such
    and is not credited: the same bytes may have been restored outside the
    record after a later write replaced them.

    A write whose hash was pending at Post is first resolved by file
    identity. "Outside the record" is said only when the record is complete
    for the path; otherwise the status is ``producer-unknown``."""
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
    if actual is not None:
        writes = _resolve_pending_writes(index, rel, writes, actual)
    incomplete = _incomplete_reasons(index, rel, writes)
    if actual is None:
        deletions = [w for w in writes if w.get("action") == "deleted"
                     and w.get("evidence") == "observed"]
        if not deletions:
            return {"status": "no-recorded-deletion"}
        current = [w for w in deletions if _verdict(w) in _CURRENT_VERDICTS]
        if len(current) == 1:
            last = current[0]
            return {"status": "deleted-in-record", "write_call": last.get("call"),
                    "events": [e.get("event_id") for e in last.get("events") or []],
                    "verdict": _verdict(last)}
        if len(current) > 1:
            return {"status": "producer-ambiguous", "calls": [w.get("call") for w in current]}
        if any(str(_verdict(w)).startswith("superseded") for w in deletions):
            if incomplete:
                return _producer_unknown(incomplete, writes,
                                         "a recorded deletion was followed by a later write, "
                                         "and the record cannot show what removed the file")
            return _uncredited("missing-after-superseded-deletion", deletions,
                               "a recorded deletion was followed by a later recorded write; "
                               "the file was removed again outside the record")
        return _uncredited("deletion-unverified", deletions,
                           "a recorded deletion exists, but the record cannot show it is "
                           "the latest change to the path")
    matching = [w for w in writes if w.get("sha256") == actual and w.get("evidence") == "observed"]
    if not matching:
        if incomplete:
            return _producer_unknown(incomplete, writes,
                                     "no recorded write is known to have produced the bytes now "
                                     "on disk, but the record is incomplete for this path, so "
                                     "the change may be in it")
        return {"status": "current-bytes-not-in-record",
                "detail": "no recorded write produced the bytes now on disk; they changed "
                          "outside the record"}
    current = [w for w in matching if _verdict(w) in _CURRENT_VERDICTS]
    if len(current) > 1:
        return {"status": "producer-ambiguous", "calls": [w.get("call") for w in current]}
    if not current:
        if any(str(_verdict(w)).startswith("superseded") for w in matching):
            if incomplete:
                return _producer_unknown(incomplete, writes,
                                         "a recorded write produced these bytes and a later "
                                         "write replaced them; the record is incomplete for "
                                         "this path, so what restored them is unknown")
            return _uncredited("current-bytes-match-superseded-write", matching,
                               "a recorded write produced these bytes, but a later recorded "
                               "write replaced them; they were restored outside the record")
        return _uncredited("current-bytes-match-unverified-write", matching,
                           "a recorded write produced these bytes, but the record cannot "
                           "show it is the latest change to the path")
    write = current[0]
    out = {"status": "written-by", "write_call": write.get("call"), "sha256": actual,
           "events": [e.get("event_id") for e in write.get("events") or []],
           "verdict": _verdict(write)}
    if write.get("hash_origin") == "query-identity-match":
        out["hash_origin"] = "query-identity-match"
    return out


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
