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

import inspect
import json
import os
from pathlib import Path
from typing import Any

_VALID_TYPES = (int, float, bool, str)


class ValueRegistrationError(Exception):
    """Raised on namespace ambiguity, in-process collisions, or invalid fragment files."""


_SESSION: dict[tuple[str, str], tuple[Any, str]] = {}


def register_value(
    key: str,
    value: Any,
    *,
    namespace: str | None = None,
    provenance: str | None = None,
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
        to ``computed_at`` if omitted.
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
    entry_provenance = provenance if provenance is not None else computed_at

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
        return  # same value, same key — no-op
    _SESSION[session_key] = (value, computed_at)

    outputs_dir = analysis_root / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    _upsert_fragment(
        outputs_dir / "numbers.json",
        namespace=ns,
        key=key,
        value=value,
        provenance=entry_provenance,
        computed_at=computed_at,
    )


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

    new_entry = {
        "key": key,
        "value": value,
        "provenance": provenance,
        "computed_at": computed_at,
    }
    for i, entry in enumerate(values):
        if isinstance(entry, dict) and entry.get("key") == key:
            values[i] = new_entry
            break
    else:
        values.append(new_entry)

    values.sort(key=lambda e: e.get("key", "") if isinstance(e, dict) else "")
    existing["values"] = values

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


__all__ = ["register_value", "ValueRegistrationError"]
