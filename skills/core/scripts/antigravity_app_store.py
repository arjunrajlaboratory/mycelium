"""Read one Antigravity App 2.31.0 native step from the App's own conversation store.

The App writes each conversation to ``~/.gemini/antigravity/conversations/<uuid>.db``
(SQLite, ``PRAGMA user_version=1``, table ``steps``). This module opens that exact
file read-only (``mode=ro``, never ``immutable``: committed WAL rows must be visible),
sets ``query_only``, selects one row by native step index, and decodes only the few
protobuf fields of the observed wire shape. It never decodes model text, rendered
output or stdout, and never executes anything. Tool arguments are parsed as one
JSON object to compare their keys and paths; edit content in them is neither
returned nor exposed. ``mode=ro`` with
``query_only`` prevents database and WAL mutation; SQLite may still update the
store's shared-memory (``-shm``) index, as any WAL reader does.

Every outcome is one of: :class:`Pending` (the same native call has not reached a
terminal state), :class:`Unverified` (anything else that is not exact evidence), or
a small dict of verified values. Callers treat ``Unverified`` as unresolved.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import time

STORE_ROOT = (".gemini", "antigravity", "conversations")
USER_VERSION = 1
COLUMNS = {"idx", "step_type", "status", "step_payload", "step_format"}
STEP_GENERIC, STEP_FORMAT = 132, 0
STATUS_RUNNING, STATUS_DONE = 2, 3
TOOL_SOURCE_BUILTIN = 1
RUN_COMMAND_TYPE = "type.googleapis.com/exa.cortex.core.tools.RunCommand"
# Assumed from the "FileEdit typed Any" evidence by analogy with RunCommand; an
# App row with any other type URL is simply not tracked.
FILE_EDIT_TYPE = "type.googleapis.com/exa.cortex.core.tools.FileEdit"
# Observed App run_command arguments. BypassSandbox is accepted only as false; the
# typed result must still prove the command ran in the sandbox.
COMMAND_ARGS = frozenset({"CommandLine", "WaitMsBeforeAsync", "toolSummary", "BypassSandbox"})
# FileEdit kinds from the App's descriptor: CREATE=1, OVERWRITE=2, EDIT=3. Each
# edit tool is accepted only with its captured argument keys and kinds.
EDIT_CREATE, EDIT_OVERWRITE, EDIT_EDIT = 1, 2, 3
EDIT_TOOLS = {"write_file": (frozenset({"Path", "Content", "Overwrite"}), frozenset({EDIT_CREATE, EDIT_OVERWRITE})),
              "edit_file": (frozenset({"Path", "Replacement", "Target"}), frozenset({EDIT_EDIT}))}
MAX_PAYLOAD = 1024 * 1024
MAX_FIELDS = 4096
MAX_TEXT = 65536
# Serialized JSON arguments of a supported, identified FileEdit (they carry file
# content), bounded within the per-row payload cap.
MAX_EDIT_ARGS = 768 * 1024


class Unverified(Exception):
    """The native store does not prove this exact step."""


class Pending(Exception):
    """The exact native call exists but has not reached a terminal state."""

    def __init__(self, call_id):
        super().__init__("native step still running")
        self.call_id = call_id


# ---- Bounded protobuf wire decoding (only the fields named below) -----------------------------

def _varint(blob, i):
    result = 0
    for n in range(10):
        if i >= len(blob):
            raise Unverified("truncated varint")
        b = blob[i]
        i += 1
        result |= (b & 0x7F) << (7 * n)
        if b < 0x80:
            return result, i
    raise Unverified("overlong varint")


def fields(blob):
    """{field: [(wire, value), ...]} for one message; malformed input is Unverified."""
    if not isinstance(blob, (bytes, bytearray)) or len(blob) > MAX_PAYLOAD:
        raise Unverified("message missing or over bound")
    out, i, count = {}, 0, 0
    while i < len(blob):
        tag, i = _varint(blob, i)
        number, wire = tag >> 3, tag & 7
        if not number:
            raise Unverified("zero field number")
        if wire == 0:
            value, i = _varint(blob, i)
        elif wire in (1, 2, 5):
            size = 8 if wire == 1 else 4 if wire == 5 else None
            if size is None:
                size, i = _varint(blob, i)
            if i + size > len(blob):
                raise Unverified("truncated field")
            value, i = bytes(blob[i:i + size]), i + size
        else:
            raise Unverified("unsupported wire type")
        count += 1
        if count > MAX_FIELDS:
            raise Unverified("field bound exceeded")
        out.setdefault(number, []).append((wire, value))
    return out


def _one(message, number, wire, *, required=True):
    values = message.get(number, [])
    if len(values) > 1:
        raise Unverified(f"duplicate field {number}")
    if not values:
        if required:
            raise Unverified(f"missing field {number}")
        return None
    if values[0][0] != wire:
        raise Unverified(f"wrong wire type for field {number}")
    return values[0][1]


def _message(message, number, *, required=True):
    raw = _one(message, number, 2, required=required)
    return None if raw is None else fields(raw)


def _text(message, number, *, required=True, limit=None):
    raw = _one(message, number, 2, required=required)
    if raw is None:
        return None
    if len(raw) > (MAX_TEXT if limit is None else limit):
        raise Unverified(f"text field {number} over bound")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise Unverified(f"text field {number} is not UTF-8") from error


def _flag(message, number):
    value = _one(message, number, 0, required=False)
    if value not in (None, 0, 1):
        raise Unverified(f"invalid boolean field {number}")
    return value == 1


def _int32(value):
    if value >= 1 << 63:
        value -= 1 << 64
    if not -(1 << 31) <= value < 1 << 31:
        raise Unverified("int32 out of range")
    return value


# ---- Exact store access ------------------------------------------------------------------------

def store_path(cid):
    return Path.home().joinpath(*STORE_ROOT, f"{cid}.db")


def _check_owned(path, *, directory=False):
    info = os.lstat(path)
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode):
        raise Unverified(f"unexpected native store entry type: {path.name}")
    if not directory and info.st_nlink != 1:
        raise Unverified(f"hard-linked native store file: {path.name}")
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise Unverified(f"unexpected native store ownership or permissions: {path.name}")
    return info.st_dev, info.st_ino


def _validate(path):
    if not path.is_absolute() or ".." in path.parts:
        raise Unverified("native store path is not exact")
    for parent in reversed((path, *path.parents)):
        if parent.is_symlink():
            raise Unverified("linked native store path")
    if not path.exists():
        raise Unverified("native store unavailable")
    _check_owned(path.parent, directory=True)
    identity = _check_owned(path)
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.is_symlink() or side.exists():
            _check_owned(side)
    return identity


def _session(cid, work):
    """Run work(connection) on one validated read-only, query-only connection to the exact store."""
    path = store_path(cid)
    try:
        before = _validate(path)
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1, isolation_level=None)
    except (OSError, sqlite3.Error) as error:
        raise Unverified(f"native store unavailable ({type(error).__name__})") from error
    try:
        connection.execute("PRAGMA query_only = ON")
        if connection.execute("PRAGMA query_only").fetchone() != (1,):
            raise Unverified("native store connection is not query-only")
        if connection.execute("PRAGMA user_version").fetchone() != (USER_VERSION,):
            raise Unverified("unexpected native store version")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(steps)")}
        if not COLUMNS <= columns:
            raise Unverified("unexpected native store schema")
        result = work(connection)
    except sqlite3.Error as error:
        raise Unverified(f"native store read failed ({type(error).__name__})") from error
    finally:
        connection.close()
    try:
        if _validate(path) != before:
            raise Unverified("native store changed identity during read")
    except OSError as error:
        raise Unverified("native store disappeared during read") from error
    return result


def read_row(cid, step):
    """(status, payload bytes) for exactly one native step row, or Unverified."""
    if not isinstance(step, int) or isinstance(step, bool) or step < 0:
        raise Unverified("invalid native step index")
    rows = _session(cid, lambda connection: connection.execute(
        "SELECT step_type, status, step_format, length(step_payload), "
        "CASE WHEN length(step_payload) <= ? THEN step_payload END FROM steps WHERE idx = ? LIMIT 2",
        (MAX_PAYLOAD, step)).fetchall())
    if not rows:
        raise Unverified("native step row missing")
    if len(rows) != 1:
        raise Unverified("duplicate native step rows")
    step_type, status, step_format, size, payload = rows[0]
    if step_type != STEP_GENERIC or step_format != STEP_FORMAT or not isinstance(status, int):
        raise Unverified("unexpected native step type or format")
    if payload is None or not isinstance(payload, bytes) or size != len(payload):
        raise Unverified("native step payload missing or over bound")
    return status, payload


def command_args_supported(args):
    """Only the observed argument keys, a command line, and no sandbox bypass request."""
    return (isinstance(args, dict) and set(args) <= COMMAND_ARGS and isinstance(args.get("CommandLine"), str) and
            bool(args["CommandLine"]) and args.get("BypassSandbox", False) is False)


def _tool_step(cid, step, tool_name, expected_type):
    """Read one exact row, then apply the shared identity checks."""
    status, payload = read_row(cid, step)
    return _decode_tool(cid, step, status, payload, {tool_name}, expected_type)[1:]


def _decode_tool(cid, step, status, payload, tool_names, expected_type):
    """Shared identity checks: (name, call_id, args, typed fields, generic response, source step)."""
    root = fields(payload)
    if _one(root, 1, 0) != STEP_GENERIC or _one(root, 4, 0) != status:
        raise Unverified("native step header disagrees with its row")
    metadata = _message(root, 5)
    call = _message(metadata, 4)
    call_id, name = _text(call, 1), _text(call, 2)
    if not call_id or name not in tool_names:
        raise Unverified("native step is a different tool call")
    origin = _message(metadata, 20)
    source_step = _one(origin, 2, 0, required=False) or 0
    # Exact captured rows carry their own result index here (row 4 -> 4, row 6 -> 6).
    if _text(origin, 4) != cid or source_step != step:
        raise Unverified("native step belongs to a different conversation or step")
    if _one(metadata, 38, 0) != TOOL_SOURCE_BUILTIN:
        raise Unverified("native tool is not built in")
    if status == STATUS_RUNNING:
        raise Pending(call_id)
    if status != STATUS_DONE:
        raise Unverified("native step did not complete")
    _message(metadata, 8)  # completed_at must be present on a terminal row
    response = _message(_message(root, 140), 2)
    typed = _message(response, 6)
    if _text(typed, 1) != expected_type:
        raise Unverified("native result has an unexpected type")
    # Arguments are parsed only after tool and result type are identified; only a
    # supported FileEdit gets the larger bound. Their content is never returned.
    limit = MAX_EDIT_ARGS if name in EDIT_TOOLS and expected_type == FILE_EDIT_TYPE else MAX_TEXT
    try:
        args = json.loads(_text(call, 3, limit=limit))
    except (TypeError, ValueError) as error:
        raise Unverified("native tool arguments are not JSON") from error
    if not isinstance(args, dict):
        raise Unverified("native tool arguments are not an object")
    return name, call_id, args, fields(_one(typed, 2, 2)), response, source_step


def command(cid, step, expected):
    """Verified {call_id, cwd, exit_code, source_step} for one exact App run_command step."""
    call_id, args, result, response, source_step = _tool_step(cid, step, "run_command", RUN_COMMAND_TYPE)
    if not command_args_supported(args) or args.get("CommandLine") != expected:
        raise Unverified("native command arguments differ from the hook call")
    exit_code = _int32(_one(result, 1, 0, required=False) or 0)
    sandbox = _message(result, 3)
    if _flag(result, 2) or _flag(result, 8):
        raise Unverified("native command timed out or has an unknown exit")
    if not _flag(sandbox, 1) or _flag(result, 4):
        raise Unverified("native command did not run in the App sandbox")
    command_line, cwd = _text(result, 6), _text(result, 7)
    if command_line != expected:
        raise Unverified("native command line differs from the hook call")
    # Structured terminal block; field 4 (stdout) is never decoded.
    block = _message(_message(_message(response, 3), 10), 3)
    explicit = _one(block, 3, 0)
    if (_text(block, 1) != command_line or _text(block, 2) != cwd or
            _int32(explicit) != exit_code):
        raise Unverified("typed command result and terminal block disagree")
    if not cwd or not os.path.isabs(cwd) or os.path.normpath(cwd) != cwd:
        raise Unverified("native working directory is not canonical")
    return {"call_id": call_id, "cwd": cwd, "exit_code": exit_code, "source_step": source_step}


def _edit_from(cid, step, status, payload, tool_names, expected_path=None):
    """The shared edit validator for callback and recovery evidence.

    Only captured shapes are supported: write_file {Path, Content, Overwrite} as
    CREATE or OVERWRITE, and edit_file {Path, Replacement, Target} as EDIT, each a
    built-in, terminal, typed FileEdit whose kind (field 1) and path (field 2)
    match the tool and its exact canonical ``Path`` argument. A callback also
    binds the hook's own path (``expected_path``).
    """
    name, call_id, args, result, _, source_step = _decode_tool(
        cid, step, status, payload, set(tool_names) & set(EDIT_TOOLS), FILE_EDIT_TYPE)
    keys, kinds = EDIT_TOOLS[name]
    path = args.get("Path")
    if set(args) - keys or not isinstance(path, str) or (expected_path is not None and path != expected_path):
        raise Unverified("native edit arguments differ from the hook call")
    kind = _one(result, 1, 0, required=False)
    if kind not in kinds:
        raise Unverified("native edit kind does not match its tool")
    if _text(result, 2) != path or not os.path.isabs(path) or os.path.normpath(path) != path:
        raise Unverified("native edit path is not canonical or differs from its arguments")
    return {"call_id": call_id, "path": path, "kind": kind, "tool": name, "source_step": source_step}


def edit(cid, step, tool_name, expected_path):
    """Verified edit evidence for one exact App PostToolUse callback."""
    if tool_name not in EDIT_TOOLS:
        raise Unverified("unsupported native edit tool")
    status, payload = read_row(cid, step)
    return _edit_from(cid, step, status, payload, {tool_name}, expected_path)


# Stop-time recovery bounds: rows, materialized bytes, and SQLite work/time.
MAX_SCAN_ROWS = 64
MAX_SCAN_BYTES = 4 * 1024 * 1024
MAX_SCAN_VM_STEPS = 20_000_000
SCAN_CHECK_STEPS = 1000  # SQLite instructions between work/time checks
SCAN_SECONDS = 2.0
MAX_SCAN_REASONS = 32


def recover_edits(cid, after, through, *, seconds=SCAN_SECONDS):
    """Committed verified edits with ``after < idx <= through``, from one read-only snapshot.

    Returns {"status": "complete" | "truncated", "edits": [...], "rejected": {idx: reason}}.
    The type marker is only a coarse SQL prefilter; every candidate passes the shared
    validator. A store that cannot be read raises Unverified; a scan that hits a row,
    byte, work, or time bound reports ``truncated`` and is never complete.
    """
    for value in (after, through):
        if not isinstance(value, int) or isinstance(value, bool) or value < -1:
            raise Unverified("invalid recovery range")
    marker = FILE_EDIT_TYPE.encode()
    deadline = time.monotonic() + max(0.0, seconds)
    work = {"steps": 0}

    def scan(connection):
        def guard():
            work["steps"] += SCAN_CHECK_STEPS
            return 1 if work["steps"] > MAX_SCAN_VM_STEPS or time.monotonic() > deadline else 0

        connection.set_progress_handler(guard, SCAN_CHECK_STEPS)
        rows, size, status = [], 0, "complete"
        try:
            # One SELECT is one committed read snapshot.
            cursor = connection.execute(
                "SELECT idx, status, length(step_payload), CASE WHEN length(step_payload) <= ? THEN step_payload END, "
                # Uniqueness over every row with this idx, in the same statement snapshot.
                "(SELECT count(*) FROM steps AS other WHERE other.idx = steps.idx) "
                "FROM steps WHERE idx > ? AND idx <= ? AND step_type = ? AND step_format = ? AND status = ? "
                "AND instr(step_payload, ?) > 0 ORDER BY idx LIMIT ?",
                (MAX_PAYLOAD, after, through, STEP_GENERIC, STEP_FORMAT, STATUS_DONE, marker, MAX_SCAN_ROWS + 1))
            for row in cursor:
                if len(rows) == MAX_SCAN_ROWS:
                    status = "truncated"
                    break
                size += row[2] or 0
                if size > MAX_SCAN_BYTES:
                    status = "truncated"
                    break
                rows.append(row)
        except sqlite3.OperationalError as error:
            if "interrupted" not in str(error):
                raise
            status = "truncated"
        return status, rows

    status, rows = _session(cid, scan)
    edits, rejected = [], {}
    for idx, row_status, size, payload, copies in rows:
        try:
            if copies != 1:
                raise Unverified("duplicate native step rows")
            if payload is None or size != len(payload):
                raise Unverified("native step payload missing or over bound")
            edits.append(dict(_edit_from(cid, idx, row_status, payload, EDIT_TOOLS), step=idx))
        except (Unverified, Pending) as error:
            if len(rejected) < MAX_SCAN_REASONS:
                rejected[str(idx)] = str(error)[:200]
    return {"status": status, "edits": edits, "rejected": rejected}
