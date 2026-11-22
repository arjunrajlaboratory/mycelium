"""register_value — append a reportable value to analysis/<ns>/outputs/numbers.json.

Designed to be called from analysis scripts. The fragments produced here are
later merged into ``analysis/<ns>/reports/.manifest.json`` by the
``report-generator`` convention's Phase 1, and consumed by scitexlintr
(https://github.com/arjunrajlaboratory/scilintr/tree/main/tex/scitexlintr)
for drift detection in the report's ``.tex`` source.

Typical use::

    from register_value import register_value

    n_samples = sample_table.shape[0]
    register_value("n_samples", n_samples)
    register_value("fdr_threshold", 0.05)
    register_value("contrast_phrase", "treated versus control")

Auto-inference
--------------
The namespace is auto-inferred from the caller's path: the function walks
up the resolved file path and uses the directory that comes after the
first ``analysis`` segment. A script at
``/repo/analysis/diff-expr/scripts/01_preprocess.py`` writes under
namespace ``diff-expr``. Pass ``namespace=...`` to override.

``computed_at`` is captured automatically as ``<rel-path>:L<line>``,
where the path is relative to the analysis root. ``provenance`` defaults
to ``computed_at`` if not supplied.

Collisions
----------
Within a single process, two ``register_value`` calls with the same
``(namespace, key)`` but a *different* value raise
``ValueRegistrationError`` — that is a logic bug in the analysis itself.
Same value is a no-op.

Across processes / re-runs, ``register_value`` upserts silently — the
on-disk fragment always reflects the most recent registration. This is
intentional: re-running an analysis with updated data should produce an
updated fragment without manual cleanup.

Hash-linked sources
-------------------
Pass ``source=`` to tie a number to the file it was read from::

    table.to_csv("outputs/tables/cell_qc.csv")          # write and close first
    register_value("n_passing", n_passing,
                   source={"path": "outputs/tables/cell_qc.csv",
                           "locator": {"row": "passing", "column": "count"}})

The file is hashed at registration (it must already be written and
closed), and the entry records its repository-relative path, SHA-256,
size, and the locator. ``check_report_values.py`` later flags the number
as stale when the file no longer has that hash: the fix is to rerun the
analysis that registers the number, never to refresh the hash. A relative
``source`` path resolves against the current working directory, as
``open()`` does. The locator is required, is recorded as declared, and is
not evaluated.

Output shape
------------
The fragment at ``analysis/<ns>/outputs/numbers.json`` looks like::

    {
      "namespace": "diff-expr",
      "values": [
        {"key": "fdr_threshold", "value": 0.05,
         "provenance": "config/contrast.yaml:fdr",
         "computed_at": "scripts/02_de.py:L17"},
        {"key": "n_samples", "value": 48,
         "provenance": "scripts/01_preprocess.py:L42",
         "computed_at": "scripts/01_preprocess.py:L42"}
      ]
    }

Entries are sorted by ``key`` for deterministic diffs.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import inspect
import json
import os
import stat
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

try:  # POSIX; elsewhere fragment writes go unlocked, as before
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

_VALID_TYPES = (int, float, bool, str)


class ValueRegistrationError(Exception):
    """Raised on namespace ambiguity, in-process collisions, or invalid fragment files."""


_SESSION: dict[tuple[str, str], tuple[Any, str]] = {}
# Digests of source files already hashed in this process, by the opened
# file's identity: many numbers usually come from one table.
_HASHES: dict[tuple[int, int, int, int, int], str] = {}
_LOCATOR_KEYS = frozenset({"row", "column", "key", "sheet", "cell", "query"})
# One registration at a time per process: the collision check, the fragment
# update and the session record happen together, so two threads can never
# both pass the check and write conflicting values.
_REGISTER_LOCK = threading.RLock()


def register_value(
    key: str,
    value: Any,
    *,
    namespace: str | None = None,
    provenance: str | None = None,
    source: Any = None,
) -> None:
    """Register a reportable value into the analysis's numbers.json fragment.

    Parameters
    ----------
    key:
        Snake-case identifier, e.g., ``"n_samples"``. Becomes the macro
        name (``\\NSamples``) via scitexlintr's documented id→macro
        transform.
    value:
        A JSON-serialisable scalar — ``int``, ``float``, ``bool``, or
        ``str``. Lists / dicts are not supported in v1.
    namespace:
        Override the auto-inferred namespace. Default: the
        ``analysis/<name>`` segment of the caller's path.
    provenance:
        Optional pointer to the data source, e.g.,
        ``"outputs/tables/cell_qc.csv:row=passing,col=count"``. Falls back
        to ``computed_at`` if omitted (or to ``<path>:<locator>`` when a
        ``source`` is given).
    source:
        Optional file the number was read from: a path, or
        ``{"path": ..., "locator": ...}``, or a list of such dicts when the
        number depends on several files (a table and the config that set its
        threshold); the number is stale when any of them changes. The locator (a non-empty string,
        or a dict with any of row/column/key/sheet/cell/query) is required.
        The file is hashed now, so register after writing and closing it.
    """
    if not isinstance(key, str) or not key:
        raise ValueRegistrationError("register_value: key must be a non-empty string")
    if not isinstance(value, _VALID_TYPES) or isinstance(value, bool) and not isinstance(value, int):
        # ``isinstance(True, int)`` is True in Python, so we accept bool via
        # the tuple above; the second clause is a defensive belt.
        pass
    if not isinstance(value, _VALID_TYPES):
        raise TypeError(
            f"register_value: value for key={key!r} must be int / float / "
            f"bool / str (got {type(value).__name__})"
        )

    caller = _caller_frame()
    inferred_ns = _infer_namespace(caller.filename)
    ns = namespace or inferred_ns
    if ns is None:
        raise ValueRegistrationError(
            f"register_value({key!r}): could not infer namespace from caller "
            f"path {caller.filename!r}. The caller must live under an "
            "analysis/<name>/ directory, or pass namespace= explicitly."
        )

    analysis_root = _find_analysis_root(caller.filename, ns)
    if analysis_root is None:
        raise ValueRegistrationError(
            f"register_value({key!r}): could not locate analysis/{ns}/ on "
            f"any ancestor of caller path {caller.filename!r}."
        )

    computed_at = _relative_call_site(caller, analysis_root)
    if isinstance(source, (list, tuple)):
        # Several files the number depends on (the table it was read from and
        # the config that chose its threshold): stale when any changes.
        if not source:
            raise ValueRegistrationError("register_value: an empty source list links nothing")
        source_entry: Any = [_source_entry(item, analysis_root) for item in source]
        paths = [e["path"] for e in source_entry]
        if len(set(paths)) != len(paths):
            raise ValueRegistrationError("register_value: a source is listed twice")
    else:
        source_entry = _source_entry(source, analysis_root) if source is not None else None
    if provenance is not None:
        entry_provenance = provenance
    elif isinstance(source_entry, list):
        entry_provenance = "; ".join(f"{e['path']}:{_locator_text(e['locator'])}"
                                     for e in source_entry)
    elif source_entry is not None:
        entry_provenance = f"{source_entry['path']}:{_locator_text(source_entry['locator'])}"
    else:
        entry_provenance = computed_at

    with _REGISTER_LOCK:
        _register_locked(ns, key, value, computed_at, entry_provenance, source_entry,
                         analysis_root)


def _register_locked(
    ns: str,
    key: str,
    value: Any,
    computed_at: str,
    entry_provenance: str,
    source_entry: Any,
    analysis_root: Path,
) -> None:
    session_key = (ns, key)
    if session_key in _SESSION:
        prev_value, prev_site = _SESSION[session_key]
        if prev_value != value:
            raise ValueRegistrationError(
                f"register_value({key!r}, namespace={ns!r}): collision — "
                f"was registered with value {prev_value!r} at {prev_site} "
                f"and again with {value!r} at {computed_at} in the same "
                f"process. If this is intentional, pass namespace= to "
                f"disambiguate."
            )
        if source_entry is None:
            return  # same value, same key, no new source: a no-op
        # Same value, newly linked (or relinked) source: record it.

    outputs_dir = analysis_root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    _upsert_fragment(
        outputs_dir / "numbers.json",
        namespace=ns,
        key=key,
        value=value,
        provenance=entry_provenance,
        computed_at=computed_at,
        source=source_entry,
    )
    # Only once the fragment holds it: a failed write leaves no false
    # "already registered" behind.
    _SESSION[session_key] = (value, computed_at)


def _repo_root(start: Path, analysis_root: Path) -> Path:
    """The project root number sources are recorded against: the nearest
    ancestor with ``.living/``, else with ``.git``, else the directory that
    holds ``analysis/``."""
    for marker in (".living", ".git"):
        for candidate in (start, *start.parents):
            if (candidate / marker).exists():
                return candidate
    return analysis_root.parent.parent


def _locator_text(locator: Any) -> str:
    if isinstance(locator, dict):
        return ",".join(f"{k}={locator[k]}" for k in sorted(locator))
    return str(locator)


def _source_entry(source: Any, analysis_root: Path) -> dict[str, Any]:
    if isinstance(source, dict):
        raw = source.get("path")
        locator = source.get("locator")
        read_sha = source.get("sha256")
        unknown = set(source) - {"path", "locator", "sha256"}
        if unknown:
            raise ValueRegistrationError(
                f"register_value: unknown source keys {sorted(unknown)}")
    else:
        raw, locator, read_sha = source, None, None
    if not isinstance(raw, (str, os.PathLike)) or not str(raw):
        raise ValueRegistrationError("register_value: source needs a path")
    if isinstance(locator, str):
        if not locator.strip():
            locator = None
    elif isinstance(locator, dict):
        if not locator or set(locator) - _LOCATOR_KEYS or not all(
                isinstance(v, (str, int)) and str(v) != "" for v in locator.values()):
            raise ValueRegistrationError(
                "register_value: a locator dict takes non-empty values for any of "
                f"{sorted(_LOCATOR_KEYS)}")
    elif locator is not None:
        raise ValueRegistrationError("register_value: locator must be a string or a dict")
    if locator is None:
        raise ValueRegistrationError(
            "register_value: a source needs a locator saying where in the file the "
            "number comes from (for example {'row': 'passing', 'column': 'count'})")
    text = os.fspath(raw)
    if ".." in Path(text).parts:
        # `link/../x` names a different file than its collapsed form when
        # `link` is a symlink, so the hashed file might not be the one read.
        raise ValueRegistrationError(
            f"register_value: source path {text!r} must not contain `..`")
    path = Path(text if os.path.isabs(text) else os.path.join(os.getcwd(), text))
    # The frame is the project the fragment belongs to, never wherever the
    # source happens to live: a source in a sibling checkout would be
    # recorded as a path the gate then hashes in this one.
    root = _repo_root(analysis_root, analysis_root)
    rel = None
    for base in (root, Path(os.path.realpath(root))):
        try:
            rel = path.relative_to(base)
            root = base
            break
        except ValueError:
            continue
    if rel is None:
        raise ValueRegistrationError(
            f"register_value: source {path} is outside the project root {root}")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            # The checker refuses symlinked components; registering through
            # one would record a path the gate can never verify.
            raise ValueRegistrationError(
                f"register_value: source path {text!r} goes through a symlink ({part})")
    digest, size = _hash_source(path)
    if read_sha is not None and read_sha != digest:
        # The analysis hashed the file when it read it (snapshot_source); the
        # file has changed since, so the number was computed from bytes that
        # are no longer there.
        raise ValueRegistrationError(
            f"register_value: source {rel.as_posix()} changed after it was read "
            "(its hash differs from the snapshot taken then); rerun the analysis")
    return {
        "path": rel.as_posix(),
        "root": "repo",
        "sha256": digest,
        "size_bytes": size,
        "locator": locator,
        "hashed_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
    }


def snapshot_source(path: Any, locator: Any = None) -> dict[str, Any]:
    """Hash a file when the analysis reads it, for a later `source=`.

    For a file read early (a config) whose number is registered later,
    registration then refuses the file if it changed in between::

        cfg_source = snapshot_source("params.yaml", {"key": "threshold"})
        threshold = yaml.safe_load(open("params.yaml"))["threshold"]
        ...
        register_value("n_up", n_up, source=[table_source, cfg_source])
    """
    digest, _ = _hash_source(Path(os.path.abspath(os.fspath(path))))
    entry: dict[str, Any] = {"path": os.fspath(path), "sha256": digest}
    if locator is not None:
        entry["locator"] = locator
    return entry


def read_source(path: Any, locator: Any = None, *, mode: str = "rb") -> tuple[Any, dict[str, Any]]:
    """Read a file once and return (its content, a source entry for those
    exact bytes). The number is then linked to the bytes the analysis
    parsed, with no window between hashing and reading::

        text, cfg_source = read_source("params.yaml", {"key": "threshold"}, mode="r")
        threshold = yaml.safe_load(text)["threshold"]
        ...
        register_value("n_up", n_up, source=[table_source, cfg_source])

    Registration refuses the file if it no longer has those bytes.
    """
    with open(os.fspath(path), "rb") as handle:
        data = handle.read()
    entry: dict[str, Any] = {"path": os.fspath(path),
                             "sha256": hashlib.sha256(data).hexdigest()}
    if locator is not None:
        entry["locator"] = locator
    return (data.decode("utf-8") if mode == "r" else data), entry


def _hash_source(path: Path) -> tuple[str, int]:
    """SHA-256 of the file, only if it provably did not change while read:
    the opened descriptor and the path must have the same identity before
    and after reading."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError as exc:
        raise ValueRegistrationError(
            f"register_value: source {path} does not exist; register after the "
            "file is written and closed") from exc
    except OSError as exc:
        raise ValueRegistrationError(f"register_value: cannot open source {path}: {exc}") \
            from exc
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueRegistrationError(f"register_value: source {path} is not a regular file")
        ident = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns)
        cached = _HASHES.get(ident)
        if cached is None:
            digest = hashlib.sha256()
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    break
                digest.update(chunk)
            cached = digest.hexdigest()
        after = os.fstat(fd)
        on_path = os.stat(path)
        if any((s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != ident
               for s in (after, on_path)):
            raise ValueRegistrationError(
                f"register_value: source {path} changed while it was hashed; register "
                "after the file is written and closed")
        _HASHES[ident] = cached
        return cached, before.st_size
    finally:
        os.close(fd)


def _caller_frame() -> inspect.FrameInfo:
    """Return the first stack frame outside this module."""
    here = Path(__file__).resolve()
    for frame in inspect.stack()[1:]:
        try:
            if Path(frame.filename).resolve() == here:
                continue
        except (OSError, ValueError):
            continue
        return frame
    raise ValueRegistrationError(
        "register_value: could not locate a calling frame outside the helper."
    )


def _infer_namespace(call_site_filename: str) -> str | None:
    """Return the directory segment that follows the *nearest* ``analysis`` part.

    "Nearest" = closest to the caller's file along the path. Walking
    right-to-left handles nested layouts like
    ``/repo/analysis/outer/.../analysis/inner/scripts/run.py`` correctly:
    the caller belongs to ``inner``, not ``outer``.
    """
    try:
        parts = Path(call_site_filename).resolve().parts
    except (OSError, ValueError):
        return None
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "analysis":
            return parts[i + 1]
    return None


def _find_analysis_root(call_site_filename: str, ns: str) -> Path | None:
    """Locate the ``analysis/<ns>/`` directory for the given namespace.

    Prefers the *nearest* match along the caller's resolved path (right-to-
    left) so nested ``analysis/.../analysis/`` layouts resolve to the deeper
    namespace. Falls back to ancestor search only if no in-path match exists
    (typical when ``namespace=`` is overridden to a sibling analysis).
    """
    try:
        resolved = Path(call_site_filename).resolve()
    except (OSError, ValueError):
        return None
    parts = resolved.parts
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "analysis" and parts[i + 1] == ns:
            return Path(*parts[: i + 2])
    here = resolved.parent
    for ancestor in [here, *here.parents]:
        candidate = ancestor / "analysis" / ns
        if candidate.is_dir():
            return candidate
    return None


def _relative_call_site(frame: inspect.FrameInfo, analysis_root: Path) -> str:
    abs_path = Path(frame.filename).resolve()
    try:
        rel = abs_path.relative_to(analysis_root)
    except ValueError:
        rel = abs_path
    return f"{rel.as_posix()}:L{frame.lineno}"


def _upsert_fragment(
    path: Path,
    *,
    namespace: str,
    key: str,
    value: Any,
    provenance: str,
    computed_at: str,
    source: dict[str, Any] | None = None,
) -> None:
    with _fragment_lock(path):
        _upsert_locked(path, namespace=namespace, key=key, value=value,
                       provenance=provenance, computed_at=computed_at, source=source)


@contextlib.contextmanager
def _fragment_lock(path: Path):
    """Serialize read-modify-replace of one fragment across processes
    through a sidecar lock file, so two scripts registering different keys
    never drop each other's entries."""
    if fcntl is None:
        yield
        return
    fd = os.open(f"{path}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _upsert_locked(
    path: Path,
    *,
    namespace: str,
    key: str,
    value: Any,
    provenance: str,
    computed_at: str,
    source: Any,
) -> None:
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueRegistrationError(
                f"register_value: {path} is not valid JSON: {exc}"
            ) from exc
        if not isinstance(existing, dict):
            raise ValueRegistrationError(
                f"register_value: {path} root must be a JSON object."
            )
    else:
        existing = {"namespace": namespace, "values": []}

    file_ns = existing.get("namespace")
    if file_ns is not None and file_ns != namespace:
        raise ValueRegistrationError(
            f"register_value: {path} has namespace={file_ns!r}, refusing to "
            f"write namespace={namespace!r} into the same file."
        )
    existing["namespace"] = namespace

    values = existing.get("values", [])
    if not isinstance(values, list):
        raise ValueRegistrationError(
            f"register_value: {path}: 'values' must be a list, got "
            f"{type(values).__name__}"
        )

    new_entry: dict[str, Any] = {
        "key": key,
        "value": value,
        "provenance": provenance,
        "computed_at": computed_at,
    }
    if isinstance(source, list):
        new_entry["sources"] = source
    elif source is not None:
        new_entry["source"] = source
    for i, entry in enumerate(values):
        if isinstance(entry, dict) and entry.get("key") == key:
            values[i] = new_entry
            break
    else:
        values.append(new_entry)

    values.sort(key=lambda e: e.get("key", "") if isinstance(e, dict) else "")
    existing["values"] = values

    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(existing, indent=2) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------
# Row and feature access (provenance item 7)
#
# Hooks see files, not what a script did inside them. `record_ids` lets an
# analysis declare which rows (cells, samples) and features (genes) a step
# read, used, wrote or excluded. Only a count and a fingerprint are stored,
# never the ids: two steps that claim the same population can be compared,
# and a QC step's drop is a recorded number, without any barcode leaving the
# analysis. Written to analysis/<ns>/outputs/access.json, beside numbers.json.

_ACCESS_AXES = frozenset({"row", "feature"})
_ACCESS_ROLES = frozenset({"read", "used", "written", "excluded"})
_PROCESS_STARTED = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AccessRecordError(ValueRegistrationError):
    """Raised on an invalid access declaration."""


def id_set_fingerprint(ids: Any) -> tuple[str, int, int, str]:
    """(sha256 of the id set, count, distinct count, sha256 in given order).

    Ids are compared as NFC-normalized text: the set hash is over the sorted
    distinct ids joined by newlines, so the same cells in any order give the
    same fingerprint. An id containing a newline is refused.
    """
    import unicodedata

    texts = []
    for item in ids:
        if not isinstance(item, str):
            # Numbers format differently across languages (R writes 100000
            # as 1e+05): ids are compared as text the caller chose.
            raise AccessRecordError(
                "record_ids: ids must be strings; convert them deliberately "
                f"(got {type(item).__name__})")
        text = unicodedata.normalize("NFC", item)
        if text == "":
            raise AccessRecordError("record_ids: an id is empty")
        if "\n" in text or "\r" in text:
            raise AccessRecordError("record_ids: an id contains a line break")
        texts.append(text)
    distinct = sorted(set(texts))
    set_hash = hashlib.sha256("\n".join(distinct).encode("utf-8")).hexdigest()
    order_hash = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
    return set_hash, len(texts), len(distinct), order_hash


def record_ids(
    name: str,
    ids: Any,
    *,
    axis: str,
    role: str,
    source: Any = None,
    namespace: str | None = None,
    ordered: bool = False,
) -> dict[str, Any]:
    """Record which rows or features a step used, as a count and fingerprint.

    Parameters
    ----------
    name:
        What the set is, e.g. ``"cells_after_qc"`` or ``"genes_tested"``.
    ids:
        Any iterable of ids (``adata.obs_names``, ``df.index``, a list).
    axis:
        ``"row"`` (cells, samples, observations) or ``"feature"`` (genes,
        columns, variables).
    role:
        ``"read"``, ``"used"``, ``"written"`` or ``"excluded"`` (what a
        filter dropped).
    source:
        Optional file the ids came from: a path or ``{"path", "locator"}``;
        hashed now, like a report value's source (the locator is optional).
    ordered:
        Also keep a fingerprint of the given order, for steps where order
        matters (a split, a ranking).

    Returns the stored entry. Re-recording the same name replaces it.
    """
    _validate_access(name, axis, role)
    caller = _caller_frame()
    ns = namespace or _infer_namespace(caller.filename)
    if ns is None:
        raise AccessRecordError(
            f"record_ids({name!r}): could not infer the analysis namespace; pass namespace=")
    analysis_root = _find_analysis_root(caller.filename, ns)
    if analysis_root is None:
        raise AccessRecordError(f"record_ids({name!r}): could not locate analysis/{ns}/")
    return _record_ids_at(name, ids, axis=axis, role=role, source=source, ns=ns,
                          analysis_root=analysis_root, ordered=ordered,
                          computed_at=_relative_call_site(caller, analysis_root))


def _validate_access(name: Any, axis: Any, role: Any) -> None:
    if not isinstance(name, str) or not name:
        raise AccessRecordError("record_ids: name must be a non-empty string")
    if axis not in _ACCESS_AXES:
        raise AccessRecordError(f"record_ids: axis must be one of {sorted(_ACCESS_AXES)}")
    if role not in _ACCESS_ROLES:
        raise AccessRecordError(f"record_ids: role must be one of {sorted(_ACCESS_ROLES)}")


def _record_ids_at(name: str, ids: Any, *, axis: str, role: str, source: Any, ns: str,
                   analysis_root: Path, ordered: bool, computed_at: str,
                   run: dict[str, Any] | None = None) -> dict[str, Any]:
    # Validated here too, so the command line can never replace a good
    # record with a malformed one.
    _validate_access(name, axis, role)
    set_hash, count, distinct, order_hash = id_set_fingerprint(ids)
    entry: dict[str, Any] = {
        "name": name, "axis": axis, "role": role, "count": count, "distinct": distinct,
        "id_set_sha256": set_hash, "computed_at": computed_at,
        # The analysis process: this one, or the caller the CLI names (an R
        # session). `process_started_at` is when the helper was first loaded
        # in that process, close to but not exactly its start.
        "run": run or {"pid": os.getpid(), "process_started_at": _PROCESS_STARTED},
    }
    if ordered:
        entry["order_sha256"] = order_hash
    if source is not None:
        # The file reference and digest only: a locator is free text and
        # could carry an id, which an access record must never hold.
        path_only = source.get("path") if isinstance(source, dict) else source
        hashed = _source_entry({"path": path_only, "locator": "file"}, analysis_root)
        entry["source"] = {k: hashed[k] for k in ("path", "root", "sha256", "size_bytes",
                                                   "hashed_at")}
    path = analysis_root / "outputs" / "access.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with _REGISTER_LOCK, _fragment_lock(path):
        document: dict[str, Any] = {"namespace": ns, "records": []}
        if path.exists():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise AccessRecordError(f"record_ids: {path} is not valid JSON: {exc}") from exc
            if not isinstance(document, dict) or not isinstance(document.get("records"), list):
                raise AccessRecordError(f"record_ids: {path} must hold a 'records' list")
            if document.get("namespace") not in (None, ns):
                raise AccessRecordError(f"record_ids: {path} belongs to another namespace")
        records = [r for r in document["records"]
                   if not (isinstance(r, dict) and r.get("name") == name)]
        records.append(entry)
        records.sort(key=lambda r: r.get("name", "") if isinstance(r, dict) else "")
        document = {"namespace": ns, "records": records}
        _atomic_json(path, document)
    return entry


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _main(argv: list[str] | None = None) -> int:
    """Command line, so other languages (the R helper) share this one
    implementation: ``register_value.py record-ids --analysis-root DIR
    --name N --axis row --role used --ids-file F [--source P] [--ordered]
    [--computed-at SCRIPT:LINE]``. Ids are read one per line."""
    import argparse

    parser = argparse.ArgumentParser(prog="register_value.py")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record-ids")
    rec.add_argument("--analysis-root", required=True)
    rec.add_argument("--name", required=True)
    rec.add_argument("--axis", required=True)
    rec.add_argument("--role", required=True)
    rec.add_argument("--ids-file", required=True)
    rec.add_argument("--source", default=None)
    rec.add_argument("--ordered", action="store_true")
    rec.add_argument("--computed-at", default="unknown")
    rec.add_argument("--run-pid", type=int, default=None,
                     help="The calling analysis process (the R session), not this helper.")
    rec.add_argument("--run-started-at", default=None)
    args = parser.parse_args(argv)
    root = Path(os.path.abspath(args.analysis_root))
    if root.parent.name != "analysis" or not root.is_dir():
        print("register_value: --analysis-root must be an existing analysis/<name> directory",
              file=sys.stderr)
        return 2
    try:
        with open(args.ids_file, encoding="utf-8") as handle:
            ids = handle.read().split("\n")
        if ids and ids[-1] == "":
            ids.pop()  # the final newline ends the last id; every other line is an id
        entry = _record_ids_at(args.name, ids, axis=args.axis, role=args.role,
                               source=args.source, ns=root.name, analysis_root=root,
                               ordered=args.ordered, computed_at=args.computed_at,
                               run={"pid": args.run_pid, "process_started_at": args.run_started_at}
                               if args.run_pid is not None else None)
    except (ValueRegistrationError, OSError) as exc:
        print(f"register_value: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(entry))
    return 0


__all__ = ["register_value", "record_ids", "snapshot_source", "read_source", "id_set_fingerprint",
           "ValueRegistrationError", "AccessRecordError"]


if __name__ == "__main__":
    sys.exit(_main())
