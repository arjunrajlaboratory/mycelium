#!/usr/bin/env python3
"""lineage_index — a project-level content-hash index over Mycelium lineage.

Answers, across sessions, for one checkout:

- which recorded events read the content with hash H;
- which call produced H, and at which path;
- whether a write is still current, or a later recorded write superseded it.

Sources, deduplicated by event and observation identity:

- every lineage manifest in ``.living/log/data-lineage/*.json`` (v1 from
  0.9.7 and earlier, v2 from 0.9.8): events, data commands, skips, and the
  observations each manifest joined. Manifests are project history: their
  repository-relative paths hold across clones and worktrees.
- every observation store in ``<state>/io/obs/**/*.ndjson`` of this
  checkout: Bash observations no event referenced, and edit-tool
  observations, which never reach a manifest. Stores are local evidence; an
  observation naming another checkout (a shared state directory) is ignored.

Writes come from observations, one set per tool call. Inferred outputs of
calls with no observation are kept as *candidate* writes that never
supersede anything on their own. Reads are always inferred (a hook cannot
see a read); their hashes say how they were taken (``hash_origin``).

Order is asserted only from the observer's call journal (same journal id,
neither interval expired, one call's end before the other's start). Clock
time orders display, never a verdict. A content difference is asserted only
between two known hashes. A gap the record cannot rule out (an unavailable,
missing or unresolved observation, a capped diff, an unwatched scope) makes
the lineage status ``unknown`` and says why. The disk is checked
separately; the headline is ``current`` only when both agree.

The index is derived state: it is cached per checkout under the shared
state directory, rebuilt incrementally from changed sources on every query,
and never committed. Nothing here runs on the hook path.

Usage::

    lineage_index.py query --hash <sha256> [--json] [--record-only]
    lineage_index.py query --path data/processed/x.csv [--json]
    lineage_index.py query --event <event_id> [--json]
    lineage_index.py build [--json]

Exit codes: 0 answered (whatever the status), 1 nothing recorded for the
selector, 2 bad input.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import io_observer  # noqa: E402

BUILDER_VERSION = 2
INDEX_SCHEMA = "mycelium.lineage_index.v1"
QUERY_SCHEMA = "mycelium.lineage_query.v1"
DEFAULT_MAX_BYTES = 4 * 1024**3
LOCK_TIMEOUT_SECONDS = 2.0
MANIFEST_DIR = Path(".living") / "log" / "data-lineage"
V2_SCHEMA = "mycelium.data_lineage.manifest.v2"

# Observation statuses that leave the call's file changes unknown.
_GAP_STATUSES = frozenset({"unavailable"})


def _obs_key(host_session_id: Any, observation_id: Any) -> str | None:
    """A call's identity: its observation id within its host session (the
    same tool id in two sessions is two calls)."""
    if not isinstance(observation_id, str) or not observation_id:
        return None
    return f"{host_session_id if isinstance(host_session_id, str) else ''}|{observation_id}"


# --------------------------------------------------------------------------
# Locating sources


def resolve_state_dir(root: Path, explicit: Path | None = None) -> Path:
    """The shared state directory the hooks use for this checkout."""
    if explicit is not None:
        return explicit
    requested = os.environ.get("MYCELIUM_STATE_DIR")
    if requested:
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = root / candidate
        real = os.path.realpath(candidate)
        if real.startswith(os.path.realpath(root) + os.sep):
            return Path(real)
    return root / ".mycelium"


def _identity_key(path: Path) -> str | None:
    try:
        st = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}"


def source_path(root: Path, state_dir: Path, name: str) -> Path:
    kind, _, rest = name.partition(":")
    if kind == "manifest":
        return root / MANIFEST_DIR / rest
    return state_dir / "io" / "obs" / rest


def list_sources(root: Path, state_dir: Path) -> dict[str, str]:
    """{source name: identity key} for every manifest and observation store."""
    sources: dict[str, str] = {}
    manifest_dir = root / MANIFEST_DIR
    with contextlib.suppress(OSError):
        for entry in sorted(os.scandir(manifest_dir), key=lambda e: e.name):
            if entry.name.endswith(".json") and not entry.name.startswith("."):
                key = _identity_key(Path(entry.path))
                if key:
                    sources[f"manifest:{entry.name}"] = key
    obs_root = state_dir / "io" / "obs"
    with contextlib.suppress(OSError):
        for host in sorted(os.scandir(obs_root), key=lambda e: e.name):
            if not host.is_dir(follow_symlinks=False):
                continue
            for store in sorted(os.scandir(host.path), key=lambda e: e.name):
                if store.name.endswith(".ndjson"):
                    key = _identity_key(Path(store.path))
                    if key:
                        sources[f"store:{host.name}/{store.name}"] = key
    return sources


# --------------------------------------------------------------------------
# Paths


def _lexical_under(path: str, base: str) -> str | None:
    if path == base:
        return None
    prefix = base.rstrip(os.sep) + os.sep
    return os.path.relpath(path, base) if path.startswith(prefix) else None


def _frame(raw: Any, rel: Any, record_root: Any, root_real: str,
           *, framed_at_hook: bool = False) -> tuple[str | None, str]:
    """(path key, frame) for a file record.

    v2 records carry `rel_path`; one whose hook refused a frame (a
    symlinked component, a climbing path) is never re-framed here. A v1
    path is made relative to its manifest's recorded root: lexically first,
    then through an alias *above* that root (macOS /var for /private/var).
    A path that cannot be framed keeps its absolute form, marked
    `unresolved`, and is never joined to a repository path.
    """
    if isinstance(rel, str) and rel:
        return rel, "repo"
    if not isinstance(raw, str) or not raw:
        return None, "none"
    if framed_at_hook or ".." in Path(raw).parts or not os.path.isabs(raw):
        return raw, "unresolved"
    lexical = os.path.normpath(raw)
    for base in filter(None, (record_root, root_real)):
        if not isinstance(base, str):
            continue
        for candidate in (os.path.normpath(base), os.path.realpath(base)):
            rel_path = _lexical_under(lexical, candidate)
            if rel_path:
                return rel_path, "repo"
        # An alias above the recorded root: the outermost ancestor of the
        # path that resolves to that root, when both still exist.
        base_real = os.path.realpath(base)
        ancestors = []
        ancestor = lexical
        while True:
            parent = os.path.dirname(ancestor)
            if parent == ancestor:
                break
            ancestors.append(parent)
            ancestor = parent
        for ancestor in reversed(ancestors):
            if os.path.realpath(ancestor) == base_real:
                rel_path = os.path.relpath(lexical, ancestor)
                if rel_path not in (".", "..") and not rel_path.startswith(".." + os.sep):
                    return rel_path, "repo"
                break
    return raw, "unresolved"


# --------------------------------------------------------------------------
# Per-source contributions


def _event_ref(record: dict[str, Any], *, kind: str, manifest: str, session_id: Any,
               ident: str, synthetic: bool) -> dict[str, Any]:
    host_session = record.get("host_session_id") or record.get("observation_session")
    ref = {
        "event_id": ident,
        "kind": kind,
        "manifest": manifest,
        "session_id": session_id,
        "host": record.get("host"),
        "host_session_id": host_session,
        "agent_id": record.get("agent_id"),
        "ts": record.get("ts"),
        "ts_ns": record.get("ts_ns"),
        "script": record.get("script"),
        "bash_exit": record.get("bash_exit"),
        "tool_outcome": record.get("tool_outcome"),
        "observation_id": record.get("observation_id"),
        "observation_status": record.get("observation_status"),
        "call": _obs_key(host_session, record.get("observation_id")),
    }
    if synthetic:
        ref["synthetic"] = True
    return ref


def _read_origin(record: dict[str, Any]) -> str:
    """How a recorded hash was taken. Only a v2 producer's explicit label,
    or an explicit pre-run proof, says; an unlabelled digest (a 0.9.7
    record, even one re-consolidated into a v2 manifest) is `unknown`."""
    if isinstance(record.get("hash_origin"), str):
        return record["hash_origin"]
    if record.get("pre_run_proven") is True:
        return "pre-run-proven"
    return "unknown"


def _empty() -> dict[str, Any]:
    return {"events": [], "reads": [], "candidates": [], "observations": [], "gaps": [],
            "pending_gaps": []}


def _manifest_contribution(path: Path, name: str, root_real: str) -> dict[str, Any]:
    out = _empty()
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["gaps"].append({"kind": "manifest-unreadable", "scope": "repo", "source": name,
                            "detail": type(exc).__name__})
        return out
    if not isinstance(manifest, dict):
        out["gaps"].append({"kind": "manifest-invalid", "scope": "repo", "source": name,
                            "detail": "not an object"})
        return out
    manifest_root = manifest.get("repo_root") if isinstance(manifest.get("repo_root"),
                                                            str) else None
    session_id = manifest.get("session_id")
    for kind, records in (("analysis", manifest.get("actions")),
                          ("data_command", manifest.get("data_commands"))):
        if records is None:
            continue
        if not isinstance(records, list):
            out["gaps"].append({"kind": "manifest-invalid", "scope": "repo", "source": name,
                                "detail": f"{kind} records are not a list"})
            continue
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                out["gaps"].append({"kind": "manifest-invalid", "scope": "repo",
                                    "source": name, "detail": f"{kind}[{index}]"})
                continue
            ident = record.get("event_id")
            synthetic = not isinstance(ident, str) or not ident
            if synthetic:
                ident = f"v1:{name}#{kind}:{index}"
            ref = _event_ref(record, kind=kind, manifest=name, session_id=session_id,
                             ident=ident, synthetic=synthetic)
            out["events"].append(ref)
            own_root = record.get("repo_root") if isinstance(record.get("repo_root"),
                                                             str) else manifest_root
            foreign = isinstance(own_root, str) and os.path.realpath(own_root) != root_real
            if foreign:
                ref["checkout"] = "foreign"
            if not foreign and ref["call"] is None and \
                    record.get("observation_status") == "unavailable":
                # The observer failed outright for this call (no id): its
                # file changes are unknown, anywhere in the repository.
                out["gaps"].append({"kind": "observation-unavailable", "scope": "repo",
                                    "source": name, "event_id": ident,
                                    "reason": record.get("observation_reason"),
                                    "ts": record.get("ts"), "ts_ns": record.get("ts_ns")})
            record_root = record.get("repo_root") if isinstance(record.get("repo_root"),
                                                                str) else manifest_root
            # A record made in another checkout (a manifest committed from a
            # worktree or a collaborator's clone) still says which content
            # its steps read, but its writes went to that checkout's files:
            # they never enter this checkout's write history.
            effective_root = record.get("repo_root") if isinstance(record.get("repo_root"),
                                                                   str) else manifest_root
            foreign = isinstance(effective_root, str) and \
                os.path.realpath(effective_root) != root_real
            if foreign:
                ref["checkout"] = "foreign"
            for direction in ("inputs", "outputs"):
                files = record.get(direction)
                if not isinstance(files, list):
                    continue
                for file_rec in files:
                    if not isinstance(file_rec, dict):
                        continue
                    key, frame = _frame(file_rec.get("path"), file_rec.get("rel_path"),
                                        record_root, root_real,
                                        framed_at_hook="rel_path" in file_rec)
                    if key is None:
                        continue
                    sha = file_rec.get("sha256") if isinstance(file_rec.get("sha256"),
                                                               str) else None
                    if direction == "inputs":
                        read = {
                            "event_id": ident, "path": key, "frame": frame, "sha256": sha,
                            "hash_origin": _read_origin(file_rec),
                            "hash_status": file_rec.get("hash_status"),
                            "pre_run_proven": file_rec.get("pre_run_proven"),
                            "source": file_rec.get("source"),
                        }
                        if foreign:
                            read["checkout"] = "foreign"
                        out["reads"].append(read)
                    elif not foreign:
                        out["candidates"].append({
                            "event_id": ident, "path": key, "frame": frame, "sha256": sha,
                            "hash_origin": file_rec.get("hash_origin") or "unknown",
                            "call": ref["call"],
                            "observed_change": file_rec.get("observed_change"),
                            "ts": record.get("ts"), "ts_ns": record.get("ts_ns"),
                        })
    observations = manifest.get("observations")
    for observation in observations if isinstance(observations, list) else []:
        if isinstance(observation, dict):
            # A 0.9.7 observation names no checkout; the manifest holding it
            # does, so a collaborator's committed record stays theirs.
            out["observations"].append({"record": observation, "source": name,
                                        "manifest_root": manifest_root})
    skipped = manifest.get("skipped")
    local_manifest = not isinstance(manifest_root, str) or \
        os.path.realpath(manifest_root) == root_real
    for skip in skipped if isinstance(skipped, list) else []:
        if isinstance(skip, dict) and local_manifest:
            # Reconciled after loading: a skip whose call has an observation
            # with a change list is covered; any other skip is a gap.
            out["pending_gaps"].append({
                "kind": "skip-unreconciled", "scope": "repo", "source": name,
                "reason": skip.get("skip_reason"), "ts": skip.get("ts"),
                "ts_ns": skip.get("ts_ns"),
                "observation_id": skip.get("observation_id"),
                "host_session_id": skip.get("host_session_id")})
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    # Reconciled after every source is loaded: a store may hold the record.
    for kind, field in (("observation-missing", "observations_missing"),
                        ("call-unresolved", "unresolved_calls")):
        items = summary.get(field)
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                out["pending_gaps"].append({"kind": kind, "scope": "repo", "source": name,
                                            "observation_id": item})
    return out


def _store_contribution(path: Path, name: str) -> dict[str, Any]:
    out = _empty()
    try:
        lines = path.read_bytes().split(b"\n")
    except OSError as exc:
        out["gaps"].append({"kind": "store-unreadable", "scope": "repo", "source": name,
                            "detail": type(exc).__name__})
        return out
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # A torn or corrupt record may have been an edit or a change
            # list: its evidence is lost, so the gap is recorded.
            out["gaps"].append({"kind": "store-record-unreadable", "scope": "repo",
                                "source": name, "detail": f"line {number}"})
            continue
        if isinstance(record, dict) and record.get("kind") == "observation":
            out["observations"].append({"record": record, "source": name})
    return out


def contribution(root: Path, state_dir: Path, name: str) -> dict[str, Any]:
    root_real = os.path.realpath(root)
    path = source_path(root, state_dir, name)
    if name.startswith("manifest:"):
        return _manifest_contribution(path, name.partition(":")[2], root_real)
    return _store_contribution(path, name)


# --------------------------------------------------------------------------
# Cache


def cache_file(root: Path, state_dir: Path) -> Path:
    digest = hashlib.sha256(os.path.realpath(root).encode()).hexdigest()[:12]
    return state_dir / "lineage" / f"index-{digest}.json"


def _load_cache(path: Path, root_real: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (not isinstance(data, dict) or data.get("builder_version") != BUILDER_VERSION
            or data.get("root") != root_real or not isinstance(data.get("sources"), dict)):
        return {}
    return data


def _write_cache(path: Path, data: dict[str, Any], root: Path) -> bool:
    """Publish the cache under a short lock; give up quietly when busy or
    read-only (the query still answers from memory)."""
    try:
        import mycelium_locks

        if not _cache_dir_safe(path.parent, root):
            return False
        if not path.parent.exists():
            os.mkdir(path.parent, 0o755)
        if not _cache_dir_safe(path.parent, root):
            return False
        with mycelium_locks.durable_lock(root, "lineage-index", timeout=LOCK_TIMEOUT_SECONDS):
            fd, temp = tempfile.mkstemp(prefix=".index.", suffix=".tmp", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                os.replace(temp, path)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(temp)
                raise
        return True
    except Exception:  # noqa: BLE001 - a cache that cannot be written is only slower
        return False


def _cache_dir_safe(directory: Path, root: Path) -> bool:
    """The cache directory and every ancestor up to the root are real
    directories inside the checkout (no symlink, nothing outside), or the
    cache directory does not exist yet under such a parent."""
    root_real = os.path.realpath(root)
    current = Path(os.path.abspath(directory))
    while True:
        if current.is_symlink():
            return False
        if current.exists() and not current.is_dir():
            return False
        if os.path.realpath(current) == root_real:
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def load(root: Path, state_dir: Path | None = None, *, write: bool = True) -> "Index":
    """The index for ``root``, refreshed from every changed source."""
    root = Path(os.path.abspath(root))
    state_dir = resolve_state_dir(root, state_dir)
    root_real = os.path.realpath(root)
    path = cache_file(root, state_dir)
    cached = _load_cache(path, root_real)
    old_sources: dict[str, Any] = cached.get("sources", {}) if cached else {}
    current = list_sources(root, state_dir)
    sources: dict[str, Any] = {}
    changed = False
    for name, key in current.items():
        entry = old_sources.get(name)
        if isinstance(entry, dict) and entry.get("key") == key:
            sources[name] = entry
            continue
        part = contribution(root, state_dir, name)
        # A source that changed while it was read is used for this answer
        # but not cached under the old identity: the next query re-reads it.
        settled = _identity_key(source_path(root, state_dir, name)) == key
        sources[name] = {"key": key if settled else None, "contribution": part}
        changed = True
    if set(old_sources) - set(current):
        changed = True
    if changed and write:
        _write_cache(path, {"builder_version": BUILDER_VERSION, "root": root_real,
                            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "sources": sources}, root)
    return Index(root, state_dir, sources)


# --------------------------------------------------------------------------
# The aggregated index


def _before(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool | None:
    """True if call a provably ended before call b started, False if b
    ended before a started, None when the journal cannot say (different
    journal, an expired bound, an open interval, or overlap)."""
    if not a or not b or a.get("id") != b.get("id") or a.get("expired") or b.get("expired"):
        return None
    a_pre, a_post, b_pre, b_post = (a.get("pre_seq"), a.get("post_seq"),
                                    b.get("pre_seq"), b.get("post_seq"))
    if isinstance(a_post, int) and isinstance(b_pre, int) and a_post < b_pre:
        return True
    if isinstance(b_post, int) and isinstance(a_pre, int) and b_post < a_pre:
        return False
    return None


def _ended_before(gap: dict[str, Any] | None, write: dict[str, Any] | None) -> bool:
    """Whether a gap's call provably ended before a write's call started.
    Unlike write order, an expiry bound counts here: the journal closes an
    interval no later than that, so nothing the gap hides comes after it."""
    if not gap or not write or gap.get("id") != write.get("id"):
        return False
    g_post, w_pre = gap.get("post_seq"), write.get("pre_seq")
    return isinstance(g_post, int) and isinstance(w_pre, int) and g_post < w_pre


def _clock(entry: dict[str, Any]) -> tuple:
    journal = entry.get("journal") or {}
    return (entry.get("ts_ns") or 0, str(entry.get("ts") or entry.get("observed_at") or ""),
            journal.get("pre_seq") or 0)


class Index:
    def __init__(self, root: Path, state_dir: Path, sources: dict[str, Any]) -> None:
        self.root = root
        self.root_real = os.path.realpath(root)
        self.state_dir = state_dir
        self.sources = sources
        self.events: dict[str, dict[str, Any]] = {}
        self.reads: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.gaps: list[dict[str, Any]] = []
        self.observations: dict[str, dict[str, Any]] = {}
        self.journal_now: dict[str, Any] = {}
        self._disk_memo: dict[str, tuple[Any, str | None, str | None]] = {}
        try:
            records = io_observer._journal_records(io_observer.journal_path(state_dir))
            jid = next((r["jid"] for r in reversed(records) if isinstance(r.get("jid"), str)),
                       None)
            for key, interval in io_observer._intervals(records).items():
                self.journal_now[key] = {"id": jid, "pre_seq": interval.pre_seq,
                                         "post_seq": interval.post_seq,
                                         "expired": interval.expired,
                                         "tool": interval.tool,
                                         "background": interval.background,
                                         "host": interval.host,
                                         "session": interval.session,
                                         "call": interval.call}
        except Exception:  # noqa: BLE001 - no journal: background bounds stay open
            pass
        self._aggregate()

    # -- building ----------------------------------------------------------

    def _aggregate(self) -> None:
        candidates: list[dict[str, Any]] = []
        pending_gaps: list[dict[str, Any]] = []
        reads: dict[tuple, dict[str, Any]] = {}
        for name in sorted(self.sources):
            part = self.sources[name].get("contribution") or {}
            for ref in part.get("events", []):
                self.events.setdefault(ref["event_id"], ref)
            for read in part.get("reads", []):
                reads.setdefault((read["event_id"], read["path"], read.get("sha256")), read)
            candidates.extend(part.get("candidates", []))
            self.gaps.extend(part.get("gaps", []))
            pending_gaps.extend(part.get("pending_gaps", []))
            for item in part.get("observations", []):
                record = item["record"]
                key = _obs_key(record.get("host_session_id"), record.get("observation_id"))
                if key is None:
                    continue
                root = record.get("repo_root")
                if not isinstance(root, str):
                    root = item.get("manifest_root")
                if isinstance(root, str) and os.path.realpath(root) != self.root_real:
                    continue  # another checkout (a shared state dir, a committed record)
                prior = self.observations.get(key)
                # A manifest's copy and the store's are the same record;
                # prefer the one with a change list.
                if prior is None or (prior["record"].get("changes") is None
                                     and record.get("changes") is not None):
                    self.observations[key] = {"record": record, "source": item["source"]}
        self.reads = list(reads.values())
        events_by_call: dict[str, list[str]] = {}
        for ref in self.events.values():
            if ref.get("call"):
                events_by_call.setdefault(ref["call"], []).append(ref["event_id"])
        for key, item in self.observations.items():
            self._add_observation(key, item, sorted(set(events_by_call.get(key, []))))
        # Missing and unresolved calls: a gap unless some source holds the
        # call's observation with a change list after all.
        resolved_ids = {item["record"].get("observation_id")
                        for item in self.observations.values()
                        if isinstance(item["record"].get("changes"), dict)}
        known_ids = {item["record"].get("observation_id") for item in self.observations.values()}
        for gap in pending_gaps:
            if gap["kind"] == "skip-unreconciled":
                key = _obs_key(gap.get("host_session_id"), gap.get("observation_id"))
                observed = self.observations.get(key) if key else None
                if observed is None and gap.get("observation_id") in resolved_ids:
                    continue  # a call id known with a change list in some session
                if observed is not None and isinstance(observed["record"].get("changes"), dict):
                    continue
                self.gaps.append(gap)
                continue
            target = resolved_ids if gap["kind"] == "call-unresolved" else known_ids
            if gap.get("observation_id") not in target:
                self.gaps.append(gap)
        for ref in self.events.values():
            call = ref.get("call")
            if ref.get("checkout") == "foreign":
                continue  # another checkout's missing observation clouds nothing here
            if call and call not in self.observations and ref.get("observation_status"):
                self.gaps.append({"kind": "observation-missing", "scope": "repo",
                                  "source": ref.get("manifest"), "event_id": ref["event_id"],
                                  "observation_id": ref.get("observation_id")})
        observed_calls = {key for key, item in self.observations.items()
                          if isinstance(item["record"].get("changes"), dict)}
        # A Bash call whose Pre is journaled but whose Post has not been
        # observed may still be changing files. It is reported with every
        # verdict that follows it (`open_calls`), not counted as a gap: the
        # command running this very query is always such a call.
        for interval in self.journal_now.values():
            if interval.get("tool") != "Bash" or interval.get("post_seq") is not None:
                continue
            call_key = _obs_key(interval.get("session"),
                                f"{interval.get('host')}:{interval.get('call')}")
            if call_key in self.observations:
                continue
            self.gaps.append({"kind": "call-open", "scope": "info", "call": call_key,
                              "journal": {"id": interval["id"], "pre_seq": interval["pre_seq"],
                                          "post_seq": None, "expired": False}})
        seen_candidates = set()
        for cand in candidates:
            if cand.get("call") in observed_calls:
                continue  # the observation is the evidence for this call's writes
            key = (cand["event_id"], cand["path"])
            if key in seen_candidates:
                continue
            seen_candidates.add(key)
            self.writes.append({
                "path": cand["path"], "frame": cand["frame"], "sha256": cand.get("sha256"),
                "hash_origin": cand.get("hash_origin"), "action": "written",
                "evidence": "inferred", "call": cand.get("call"),
                "events": [cand["event_id"]], "attribution": "inferred", "journal": None,
                "ts": cand.get("ts"), "ts_ns": cand.get("ts_ns"),
                "observed_change": cand.get("observed_change"),
            })

    def _add_observation(self, key: str, item: dict[str, Any], events: list[str]) -> None:
        record = item["record"]
        status = record.get("status")
        journal = record.get("journal") if isinstance(record.get("journal"), dict) else None
        base = {"call": key, "observation_id": record.get("observation_id"),
                "events": events, "journal": journal,
                "observed_at": record.get("observed_at"), "source": item["source"],
                "tool": record.get("tool"), "tool_outcome": record.get("outcome")}
        changes = record.get("changes")
        if status in _GAP_STATUSES or not isinstance(changes, dict):
            self.gaps.append({"kind": f"observation-{status or 'unknown'}", "scope": "repo",
                              "reason": record.get("reason"), **base})
            return
        attribution = {"no-known-overlap": "sole", "overlap": "shared",
                       "background": "uncertain", "edit": "sole"}.get(status, "uncertain")
        if status == "edit" and record.get("outcome") != "success":
            attribution = "uncertain"  # an edit whose success was not established
        overlapping = [o.get("call") for o in record.get("overlapping") or []
                       if isinstance(o, dict)]
        if overlapping and attribution == "sole":
            attribution = "shared"
        # Targets an edit could not confirm (a failed or partly applied
        # patch, a deletion that did not happen): their content is unknown.
        for refused in changes.get("refused") or []:
            if (isinstance(refused, dict) and isinstance(refused.get("path"), str)
                    and not os.path.isabs(refused["path"])
                    and refused.get("reason") in ("edit-failed", "deletion-not-applied")):
                self.gaps.append({"kind": "edit-uncertain", "scope": "path",
                                  "path": refused["path"], "reason": refused["reason"], **base})
        # Paths that left or entered the watched scope during the call.
        for moved in changes.get("coverage_changed") or []:
            if isinstance(moved, str):
                self.gaps.append({"kind": "coverage-changed", "scope": "path", "path": moved,
                                  **base})
        if changes.get("coverage_changed_truncated"):
            self.gaps.append({"kind": "coverage-changed", "scope": "repo", **base})
        if status == "background":
            # A background job may keep writing after its launch snapshot;
            # its writes are unknown until the journal bounds its interval.
            host, _, call = str(record.get("observation_id") or "").partition(":")
            now = self.journal_now.get(io_observer.interval_key(
                host, str(record.get("host_session_id") or ""), call))
            bound = dict(now or journal or {})
            if bound.get("expired"):
                # The journal's timeout is not evidence the job stopped.
                bound["post_seq"] = None
            self.gaps.append({"kind": "background-job", "scope": "repo",
                              **{**base, "journal": bound or None}})
        incomplete = bool(changes.get("capped"))
        if incomplete:
            # A capped diff lists only some changes; its top-level counts
            # say where the rest are. Without them, anywhere.
            tops = changes.get("top_level")
            if isinstance(tops, dict) and tops and changes.get("top_level_complete") is True:
                for top in tops:
                    self.gaps.append({"kind": "observation-capped", "scope": "path",
                                      "path": str(top), "prefix": True, **base})
            else:
                self.gaps.append({"kind": "observation-capped", "scope": "repo", **base})
        scope = record.get("scope") if isinstance(record.get("scope"), dict) else {}
        for unwatched in scope.get("unwatched") or []:
            if isinstance(unwatched, dict) and isinstance(unwatched.get("path"), str):
                self.gaps.append({"kind": "unwatched", "scope": "path",
                                  "path": unwatched["path"], "prefix": True, **base})
        racy = [r for r in changes.get("racy_unknown") or [] if isinstance(r, str)]
        counts = changes.get("counts") if isinstance(changes.get("counts"), dict) else {}
        racy_count = counts.get("racy_unknown")
        if isinstance(racy_count, int) and racy_count > len(racy):
            self.gaps.append({"kind": "racy-truncated", "scope": "repo", **base})
        for path in racy:
            self.gaps.append({"kind": "racy", "scope": "path", "path": path, **base})
        for kind, action in (("created", "written"), ("modified", "written"),
                             ("edited", "written"), ("deleted", "deleted")):
            entries = changes.get(kind)
            for entry in entries if isinstance(entries, list) else []:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    continue
                after = entry.get("after") if isinstance(entry.get("after"), dict) else {}
                sha = after.get("sha256") if action == "written" else None
                self.writes.append({
                    "path": entry["path"], "frame": "repo",
                    "sha256": sha if isinstance(sha, str) else None,
                    "hash_status": after.get("hash_status"),
                    "identity": after.get("identity"),
                    "hash_origin": "observed" if isinstance(sha, str) else None,
                    "action": action, "change": kind, "evidence": "observed",
                    "attribution": attribution, "overlapping": overlapping,
                    "incomplete": incomplete, **base,
                })

    # -- answering -----------------------------------------------------------

    def writes_at(self, path: str) -> list[dict[str, Any]]:
        return sorted((w for w in self.writes if w["path"] == path), key=_clock)

    @staticmethod
    def _gap_applies(gap: dict[str, Any], path: str) -> bool:
        if gap.get("scope") == "repo":
            return True
        if gap.get("scope") != "path":
            return False
        gpath = gap.get("path") or ""
        if gap.get("prefix"):
            return path == gpath or path.startswith(gpath.rstrip("/") + "/")
        return path == gpath

    def record_status(self, write: dict[str, Any]) -> dict[str, Any]:
        """The lineage verdict for one write, from the record alone."""
        path = write["path"]
        if write.get("evidence") != "observed":
            return {"status": "unknown", "reasons": ["inferred-write"]}
        mine = write.get("sha256")
        deleted = write.get("action") == "deleted"
        later_diff: list[dict[str, Any]] = []
        later_maybe: list[dict[str, Any]] = []
        unordered: list[dict[str, Any]] = []
        for other in self.writes_at(path):
            if other is write or other["call"] == write["call"]:
                continue
            theirs = other.get("sha256")
            other_deleted = other.get("action") == "deleted"
            if other.get("evidence") != "observed":
                if theirs != mine or theirs is None:
                    unordered.append(other)
                continue
            order = _before(write.get("journal"), other.get("journal"))
            if order is False:
                continue
            if order is None:
                # Unordered, even with the same bytes: which call left the
                # current content is then unproven.
                unordered.append(other)
                continue
            if deleted:
                if not other_deleted:
                    later_diff.append(other)  # recreated after the deletion
                continue
            if other_deleted or (mine is not None and theirs is not None and mine != theirs):
                later_diff.append(other)
            elif theirs is None or mine is None:
                later_maybe.append(other)  # a difference cannot be proven
        gaps = [g for g in self.gaps if self._gap_applies(g, path)
                and (g.get("call") != write["call"] or g.get("kind") == "background-job")
                and not _ended_before(g.get("journal"), write.get("journal"))]
        if later_diff:
            # A journal-ordered later write with different known bytes (or a
            # deletion) replaced these bytes, whatever else a gap may hide;
            # the gaps are reported with the verdict, not instead of it.
            first = min(later_diff, key=lambda w: (w["journal"] or {}).get("pre_seq") or 0)
            result = {"status": "superseded", "superseded_by": _write_summary(first, self)}
            if gaps:
                result["gaps"] = [_gap_summary(g) for g in gaps[:10]]
                result["gap_count"] = len(gaps)
            return result
        if later_maybe:
            first = min(later_maybe, key=lambda w: (w["journal"] or {}).get("pre_seq") or 0)
            reason = "earlier-hash-unknown" if mine is None else "later-write-hash-unknown"
            return {"status": "superseded-unverified",
                    "superseded_by": _write_summary(first, self), "reasons": [reason]}
        reasons = []
        if unordered:
            reasons.append("order-unproven")
        if io_observer.watch_status(path, []) != "watched":
            # A Bash observation never sees this path (an excluded
            # directory or name): a later Bash call could have changed it.
            reasons.append("unwatched-path")
        if gaps:
            reasons.append("gap")
        if mine is None and not deleted:
            reasons.append("write-hash-unknown")
        if write.get("incomplete"):
            reasons.append("incomplete-observation")
        open_calls = [g["call"] for g in self.gaps if g.get("kind") == "call-open"
                      and not _ended_before(g.get("journal"), write.get("journal"))]
        if reasons:
            result: dict[str, Any] = {"status": "unknown", "reasons": reasons}
            if unordered:
                result["unordered_writes"] = [_write_summary(w, self) for w in unordered[:10]]
            if gaps:
                result["gaps"] = [_gap_summary(g) for g in gaps[:10]]
                result["gap_count"] = len(gaps)
        else:
            result = {"status": "current-in-record"}
        if open_calls:
            result["open_calls"] = open_calls[:10]
        return result

    def disk_status(self, path: str, expected: str | None, *,
                    identity: dict[str, Any] | None = None,
                    max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
        """What the file at ``path`` holds now, compared with ``expected``."""
        if os.path.isabs(path) or ".." in Path(path).parts:
            return {"disk": "unverified", "reason": "unframed-path"}
        rel, reason = io_observer.repo_relative_unfollowed(
            os.path.join(self.root_real, path), self.root_real)
        if rel is None:
            return {"disk": "unverified", "reason": reason}
        full = Path(self.root_real) / rel
        # The cache is loaded before the file's identity is sampled, and the
        # identity is checked again after any lookup or hash: a file
        # replaced meanwhile is never certified with the old digest.
        cache = io_observer.load_cache(self.state_dir)
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            return {"disk": "missing"}
        except OSError as exc:
            return {"disk": "unverified", "reason": type(exc).__name__}
        if not stat.S_ISREG(st.st_mode):
            return {"disk": "unverified", "reason": "not-a-regular-file"}
        ident = io_observer.identity_of(st)
        cached = cache.get(io_observer._cache_key(ident))
        memo = self._disk_memo.get(path)
        if memo is not None and memo[0] == ident:
            digest, origin = memo[1], memo[2]
        elif cached:
            digest, origin = cached, "hash-cache"
        elif st.st_size > max_bytes:
            return {"disk": "unverified", "reason": "too-large", "size_bytes": st.st_size}
        else:
            result, _ = io_observer.stable_hash(full, ident, deadline=time.monotonic() + 3600,
                                                cache={}, new_cache={})
            digest, origin = result.sha256, "hashed"
            if digest is None:
                return {"disk": "unverified", "reason": result.status}
        try:
            if io_observer.identity_of(os.lstat(full)) != ident:
                return {"disk": "unverified", "reason": "changed-during-check"}
        except OSError:
            return {"disk": "unverified", "reason": "changed-during-check"}
        self._disk_memo[path] = (ident, digest, origin)
        out: dict[str, Any] = {"disk_sha256": digest, "disk_hash_origin": origin}
        if expected is None:
            out["disk"] = "unverified"
            out["reason"] = "nothing-to-compare"
            if identity and all(identity.get(k) == getattr(ident, k)
                                for k in ("dev", "ino", "size", "mtime_ns", "ctime_ns")):
                # A pending hash resolved: the file still has exactly the
                # identity the observer saw after the call.
                out["disk"] = "match"
                out["resolved_sha256"] = digest
                out["disk_hash_origin"] = "query-identity-match"
                out.pop("reason")
            return out
        out["disk"] = "match" if digest == expected else "differs"
        return out

    def write_status(self, write: dict[str, Any], *, disk: bool = True,
                     max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
        record = self.record_status(write)
        result: dict[str, Any] = {"record": record}
        if disk and write.get("frame") == "repo":
            result.update(self.disk_status(write["path"], write.get("sha256"),
                                           identity=write.get("identity"),
                                           max_bytes=max_bytes))
        result["status"] = _headline(record["status"], result.get("disk") if disk else None,
                                     write.get("action"))
        return result


def _headline(record: str, disk: str | None, action: str | None) -> str:
    if record in ("superseded", "superseded-unverified"):
        return record
    if record == "current-in-record":
        if disk is None:
            return "current-in-record"
        if action == "deleted":
            return "current" if disk == "missing" else "changed-outside-record"
        return {"match": "current", "differs": "changed-outside-record",
                "missing": "missing-on-disk"}.get(disk, "unverified")
    return "unknown"


def _write_summary(write: dict[str, Any], index: Index) -> dict[str, Any]:
    journal = write.get("journal") or None
    return {
        "path": write["path"], "call": write.get("call"),
        "observation_id": write.get("observation_id"), "action": write.get("action"),
        "change": write.get("change"), "sha256": write.get("sha256"),
        "hash_status": write.get("hash_status"), "hash_origin": write.get("hash_origin"),
        "evidence": write.get("evidence"), "attribution": write.get("attribution"),
        "overlapping": write.get("overlapping") or [],
        "incomplete": bool(write.get("incomplete")), "source": write.get("source"),
        "tool": write.get("tool"), "tool_outcome": write.get("tool_outcome"),
        "events": [index.events.get(e, {"event_id": e}) for e in write.get("events") or []],
        "journal": journal, "order_basis": "journal" if journal else "clock",
        "observed_at": write.get("observed_at"), "ts": write.get("ts"),
    }


def _gap_summary(gap: dict[str, Any]) -> dict[str, Any]:
    return {k: gap.get(k) for k in ("kind", "scope", "path", "call", "observation_id",
                                    "event_id", "reason", "source", "observed_at", "ts")
            if gap.get(k) is not None}


# --------------------------------------------------------------------------
# Queries


def _resolve_pending(index: Index, sha256: str, max_bytes: int) -> list[dict[str, Any]]:
    """Observed writes whose hash was pending at Post but whose file still
    has the identity the observer recorded, and now hashes to ``sha256``."""
    resolved = []
    for write in index.writes:
        if write.get("sha256") is None and write.get("identity") and write.get("frame") == "repo":
            # A cheap identity check first: only a file that still has the
            # observed identity is worth hashing.
            try:
                st = os.lstat(os.path.join(index.root_real, write["path"]))
            except (OSError, ValueError):
                continue
            ident = write["identity"]
            if any(ident.get(k) != getattr(st, f"st_{k}") for k in
                   ("dev", "ino", "size", "mtime_ns", "ctime_ns")):
                continue
            disk = index.disk_status(write["path"], None, identity=write["identity"],
                                     max_bytes=max_bytes)
            if disk.get("resolved_sha256") == sha256:
                resolved.append(write)
    return resolved


def query_hash(index: Index, sha256: str, *, disk: bool = True,
               max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    producers = [w for w in index.writes if w.get("sha256") == sha256]
    if disk:
        producers.extend(_resolve_pending(index, sha256, max_bytes))
    readers = [r for r in index.reads if r.get("sha256") == sha256]
    paths: dict[str, dict[str, Any]] = {}
    for write in sorted(producers, key=_clock):
        entry = paths.setdefault(write["path"], {"path": write["path"], "writes": [],
                                                 "readers": []})
        entry["writes"].append({**_write_summary(write, index),
                                **index.write_status(write, disk=disk, max_bytes=max_bytes)})
    order = {"pre-run-proven": 0, "hashed-at-post": 1, "observed": 1, "hook-post": 2,
             "unknown": 3}
    for read in sorted(readers, key=lambda r: order.get(r.get("hash_origin"), 4)):
        entry = paths.setdefault(read["path"], {"path": read["path"], "writes": [],
                                                "readers": []})
        entry["readers"].append({**read, "event": index.events.get(read["event_id"])})
    for entry in paths.values():
        if not entry["writes"]:
            entry["record"] = {"status": "no-recorded-write"}
            if disk:
                entry.update(index.disk_status(entry["path"], sha256, max_bytes=max_bytes))
    return {"schema": QUERY_SCHEMA, "selector": {"hash": sha256},
            "found": bool(producers or readers),
            "readers": len({r["event_id"] for r in readers}),
            "producers": len({w["call"] or w["events"][0] for w in producers}),
            "paths": list(paths.values()), "coverage": _coverage(index)}


def query_path(index: Index, path: str, *, disk: bool = True,
               max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    writes = index.writes_at(path)
    readers = [r for r in index.reads if r["path"] == path]
    history = [{**_write_summary(w, index), **index.write_status(w, disk=disk,
                                                                 max_bytes=max_bytes)}
               for w in writes]
    result: dict[str, Any] = {
        "schema": QUERY_SCHEMA, "selector": {"path": path},
        "found": bool(writes or readers), "writes": history,
        "readers": [{**r, "event": index.events.get(r["event_id"])} for r in readers],
        "coverage": _coverage(index),
    }
    if not writes:
        result["record"] = {"status": "no-recorded-write"}
        if disk:
            result.update(index.disk_status(path, None, max_bytes=max_bytes))
    return result


def query_event(index: Index, event_id: str, *, disk: bool = True,
                max_bytes: int = DEFAULT_MAX_BYTES) -> dict[str, Any]:
    ref = index.events.get(event_id)
    reads = [r for r in index.reads if r["event_id"] == event_id]
    writes = [w for w in index.writes if event_id in (w.get("events") or [])]
    return {
        "schema": QUERY_SCHEMA, "selector": {"event": event_id},
        "found": ref is not None, "event": ref, "reads": reads,
        "writes": [{**_write_summary(w, index), **index.write_status(w, disk=disk,
                                                                     max_bytes=max_bytes)}
                   for w in writes],
        "coverage": _coverage(index),
    }


def _coverage(index: Index) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for gap in index.gaps:
        kinds[gap.get("kind", "unknown")] = kinds.get(gap.get("kind", "unknown"), 0) + 1
    return {
        "manifests": sum(1 for s in index.sources if s.startswith("manifest:")),
        "stores": sum(1 for s in index.sources if s.startswith("store:")),
        "events": len(index.events), "observed_calls": len(index.observations),
        "writes": len(index.writes), "reads": len(index.reads), "gaps": kinds,
    }


def summary(index: Index) -> dict[str, Any]:
    return {"schema": INDEX_SCHEMA, "root": index.root_real,
            "builder_version": BUILDER_VERSION, "coverage": _coverage(index)}


# --------------------------------------------------------------------------
# CLI


def _repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / ".living").is_dir():
            return candidate
    return start


def _print_text(result: dict[str, Any]) -> None:
    selector = result.get("selector", {})
    print(f"query {json.dumps(selector)}: {'found' if result.get('found') else 'nothing recorded'}")
    for entry in result.get("paths", []):
        print(f"  {entry['path']}")
        for write in entry.get("writes", []):
            print(f"    written by call {write.get('call')} -> {write['status']}"
                  f" (record: {write['record']['status']}, disk: {write.get('disk', '-')})")
            sup = write["record"].get("superseded_by")
            if sup:
                print(f"      superseded by call {sup.get('call')} ({sup.get('action')})")
            for reason in write["record"].get("reasons") or []:
                print(f"      reason: {reason}")
        for read in entry.get("readers", []):
            event = read.get("event") or {}
            print(f"    read by {read['event_id']} [{read.get('hash_origin')}]"
                  f" {event.get('script') or ''}")
        if not entry.get("writes"):
            print(f"    no recorded write; disk: {entry.get('disk', '-')}")
    for write in result.get("writes", []):
        print(f"  write {write.get('call')} {write.get('action')} {write.get('sha256')}"
              f" -> {write['status']}")
    for read in result.get("readers", []):
        print(f"  read by {read['event_id']} [{read.get('hash_origin')}]")
    gaps = result.get("coverage", {}).get("gaps") or {}
    if gaps:
        print("  gaps in the record: " + ", ".join(f"{k}={v}" for k, v in sorted(gaps.items())))


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=None,
                        help="Repository root (default: nearest ancestor with .living/).")
    parser.add_argument("--state-dir", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="Refresh the cached index and print a summary.")
    build.add_argument("--json", action="store_true")
    query = sub.add_parser("query", help="Answer one provenance question.")
    selector = query.add_mutually_exclusive_group(required=True)
    selector.add_argument("--hash")
    selector.add_argument("--path")
    selector.add_argument("--event")
    query.add_argument("--json", action="store_true")
    query.add_argument("--record-only", action="store_true",
                       help="Do not read the disk; report the record's verdict only.")
    query.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    query.add_argument("--no-write", action="store_true",
                       help="Never write the cache (read-only callers).")
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(os.path.abspath(args.root)) if args.root else _repo_root(Path.cwd())
    if args.command == "build":
        index = load(root, args.state_dir)
        result = summary(index)
        print(json.dumps(result, indent=2) if args.json else
              f"lineage index for {result['root']}: {json.dumps(result['coverage'])}")
        return 0
    if args.hash is not None and (len(args.hash) != 64
                                  or any(c not in "0123456789abcdef" for c in args.hash)):
        print("lineage_index: --hash must be a lowercase 64-hex sha256", file=sys.stderr)
        return 2
    path = args.path
    if path is not None:
        if "\0" in path or ".." in Path(path).parts:
            print("lineage_index: --path must not climb (`..`)", file=sys.stderr)
            return 2
    index = load(root, args.state_dir, write=not args.no_write)
    disk = not args.record_only
    if args.hash is not None:
        result = query_hash(index, args.hash, disk=disk, max_bytes=args.max_bytes)
    elif path is not None:
        if os.path.isabs(path):
            rel, _ = io_observer.repo_relative_unfollowed(path, index.root_real)
            path = rel or path
        result = query_path(index, os.path.normpath(path), disk=disk, max_bytes=args.max_bytes)
    else:
        result = query_event(index, args.event, disk=disk, max_bytes=args.max_bytes)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_text(result)
    return 0 if result.get("found") else 1


if __name__ == "__main__":
    sys.exit(main())
