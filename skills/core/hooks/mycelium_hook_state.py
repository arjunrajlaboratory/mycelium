"""Mycelium lifecycle state checks, run as one process per hook pass.

The bash lifecycle hooks used to start a Python interpreter for every state
helper: shared-state preparation, private run selection, active-marker and
owner-token parsing, ownership authorization, and each lock attempt. This
module holds those helpers and runs an ordered list of them in a single
interpreter. `mycelium-hook-lib.sh` (`mycelium__state`) is its only caller.

Every operation keeps the exact contract of the bash helper it replaces: the
same checks in the same order, the same variable assignments (including the
ones bash made before a later check failed), and fail-closed results. The
security properties are unchanged: repository-controlled symlinks under
`.living` and `.mycelium` are refused, marker and owner files must have their
exact versioned shapes, the private run directory must resolve inside the
shared state directory, and an event revalidates after taking its lock.

Locks keep mkdir(2) as the only arbiter (regression pattern 40): `lock:*`
tries `os.mkdir` once. When the lock already exists, or the create fails, the
op reports `busy` and stops; bash then runs its unchanged retry and dead-owner
reap loop and resumes the remaining ops in a new process. So an uncontended
hook pays for one interpreter, and a contended one behaves as before.

Invocation (always `python3 -I -S`, so neither the untrusted checkout nor
PYTHON* variables can inject modules):

    mycelium_hook_state.py PID HOST SID_STATUS SID REPO_ROOT STATE_DIR
        SHARED_STATE_DIR REQUESTED_STATE PLUGIN_ROOT OP...

The nine positional values are the bash variables of the same meaning, passed
explicitly because bash may hold them unexported. The ops are:

    prepare:MODE      mycelium_prepare_state_dir (write, read-only, bootstrap)
    select:MODE       mycelium_select_session_state (create, read-only)
    owns              mycelium_payload_owns_active_session
    require-marker    fail unless STATE_DIR/active-session-log.tmp exists
    lock:stop         one mkdir(2) attempt at the lifecycle lock
    lock:session      one mkdir(2) attempt at the session lock
    marker            read STATE_DIR's active marker (never fails)
    owner             read STATE_DIR's owner token (never fails)
    marker-at=PATH    mycelium_read_active_log_marker on PATH
    owner-at=PATH     mycelium_read_session_owner_id on PATH

Output is a sequence of NUL-terminated fields:

    set NAME VALUE        bash assigns VALUE to NAME
    lock KIND DIR         bash records the acquired lock directory
    marker STATE PATH TS FORMAT
    owner STATE ID
    ok INDEX | fail INDEX | busy INDEX
    end

Processing stops at the first `fail` or `busy`. A missing `end` means the
process died; bash then treats the next unfinished op as failed.
"""

from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

SESSION_ID_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)
DIGITS = frozenset("0123456789")
LOCK_NAMES = {"stop": "mycelium-stop.lock", "session": "mycelium-session.lock"}


class OpFailed(Exception):
    """The op's bash counterpart would have returned nonzero."""


class Busy(Exception):
    """The lock exists or its mkdir failed; bash runs its retry loop."""


def emit(*fields: str | bytes) -> None:
    out = sys.stdout.buffer
    for field in fields:
        out.write(os.fsencode(field) if isinstance(field, str) else field)
        out.write(b"\0")
    out.flush()


def exists(path: str) -> bool:
    """bash `-e`: follows symlinks."""
    return os.path.exists(path)


def islink(path: str) -> bool:
    """bash `-L`."""
    return os.path.islink(path)


def isfile(path: str) -> bool:
    """bash `-f`: follows symlinks."""
    return os.path.isfile(path)


def isdir(path: str) -> bool:
    """bash `-d`: follows symlinks."""
    return os.path.isdir(path)


def valid_session_id(value: str) -> bool:
    """mycelium_valid_session_id, with the ASCII classes the run path needs."""
    return (
        bool(value)
        and value not in {".", ".."}
        and len(value) <= 200
        and all(ch in SESSION_ID_CHARS for ch in value)
    )


def physical_dir(path: str) -> str:
    """`cd "$path" 2>/dev/null && pwd -P` inside a command substitution.

    bash's logical `cd` folds `..` lexically before chdir(2) and retries the
    literal path if that fails; `pwd -P` is getcwd(3); the substitution strips
    trailing newlines. Only absolute paths reach this from the hooks; a
    relative path, an empty one, or an option-looking one fails closed.
    """
    if not path.startswith("/"):
        raise OpFailed
    try:
        origin = os.open(".", os.O_RDONLY)
    except OSError:
        # A deleted or unreadable cwd does not stop bash's cd; every path
        # this module uses afterwards is absolute.
        origin = None
    try:
        try:
            os.chdir(os.path.normpath(path))
        except OSError:
            try:
                os.chdir(path)
            except OSError:
                raise OpFailed from None
        try:
            resolved = os.getcwd()
        except OSError:
            raise OpFailed from None
    finally:
        if origin is not None:
            try:
                os.fchdir(origin)
            except OSError:
                pass
            os.close(origin)
    return resolved.rstrip("\n")


# Errors that mean the entry changed under the walk or cannot be searched:
# find reports these and moves on, so the walk skips the entry. Any other
# error (out of descriptors or memory, I/O) leaves part of the tree unseen,
# so the scan fails closed instead.
_SKIPPABLE = frozenset(
    {errno.ENOENT, errno.ENOTDIR, errno.ELOOP, errno.EACCES, errno.EPERM}
)


class _ScanIncomplete(Exception):
    """Part of the tree could not be examined."""


def _skippable(error: OSError) -> bool:
    if error.errno in _SKIPPABLE:
        return True
    raise _ScanIncomplete from error


def _scan_cwd() -> list[tuple[str, int, int]] | None:
    """Subdirectories of the cwd as (name, dev, inode), or None on a symlink.

    An unreadable directory has no visible entries, as `find` reports it and
    moves on.
    """
    subdirs: list[tuple[str, int, int]] = []
    try:
        entries = list(os.scandir("."))
    except OSError as error:
        _skippable(error)
        return subdirs
    for entry in entries:
        try:
            if entry.is_symlink():
                return None
            if entry.is_dir(follow_symlinks=False):
                info = entry.stat(follow_symlinks=False)
                subdirs.append((entry.name, info.st_dev, info.st_ino))
        except OSError as error:
            _skippable(error)
            continue
    return subdirs


def _same_dir(device: int, inode: int) -> bool:
    try:
        info = os.stat(".")
    except OSError:
        return False
    return (info.st_dev, info.st_ino) == (device, inode)


def _open_dir(name: str) -> int | None:
    """Open NAME (relative to the cwd) as a directory without following a
    symlink, or None if it is gone, not a directory, a symlink now, or not
    readable. Other errors raise _ScanIncomplete."""
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        _skippable(error)
        return None


# Directory descriptors kept for the way back up; deeper levels return by
# ".." and verify the parent's identity instead, so a 1,100-deep tree never
# needs more than this many open descriptors. Running out anyway is an
# incomplete scan, which is unsafe.
_SCAN_FD_DEPTH = 16


def contains_symlink(top: str) -> bool:
    """`find TOP -type l -print -quit` printed something.

    Like find without -H or -L, TOP itself counts when it is a symlink, and
    the walk never follows one. It walks the way fts does: iteratively,
    moving the cwd by relative name, so neither the recursion limit nor
    PATH_MAX bounds the depth (a tree 1,100 directories deep is still
    scanned). A child is opened without following links and entered only if
    it is still the directory that was listed; one that changed in the
    meantime (hooks create and remove lock directories all the time) is
    skipped, as fts skips it. The answer is "unsafe" for a symlink found, a
    root that is not the directory first examined, a walk that cannot get
    back to a verified parent, or any part of the tree it could not examine.
    """
    try:
        origin = os.open(".", os.O_RDONLY)
    except OSError:
        origin = None
    held: list[int] = []
    try:
        return _walk(top, held)
    except _ScanIncomplete:
        return True
    finally:
        for descriptor in held:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if origin is not None:
            try:
                os.fchdir(origin)
            except OSError:
                pass
            os.close(origin)


def _walk(top: str, held: list[int]) -> bool:
    """contains_symlink's walk. Every descriptor it keeps is in HELD, which
    the caller closes; the caller also restores the cwd."""
    try:
        root = os.lstat(top)
    except OSError:
        return False
    if stat.S_ISLNK(root.st_mode):
        return True
    if not stat.S_ISDIR(root.st_mode):
        return False
    # find reports an unreadable or vanished root and prints nothing; a root
    # that became a symlink or a file since the lstat was swapped.
    try:
        descriptor = os.open(top, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        return error.errno not in {errno.EACCES, errno.EPERM, errno.ENOENT}
    held.append(descriptor)
    try:
        opened = os.fstat(descriptor)
    except OSError:
        return True
    if (opened.st_dev, opened.st_ino) != (root.st_dev, root.st_ino):
        return True
    try:
        os.fchdir(descriptor)
    except OSError as error:
        return error.errno not in {errno.EACCES, errno.EPERM}
    found = _scan_cwd()
    if found is None:
        return True
    # Each frame: (remaining subdirectories, identity, descriptor or None).
    stack = [(iter(found), (root.st_dev, root.st_ino), descriptor)]
    while stack:
        pending = stack[-1][0]
        descended = False
        for name, device, inode in pending:
            child = _open_dir(name)
            if child is None:
                continue
            held.append(child)
            try:
                info = os.fstat(child)
                same = (info.st_dev, info.st_ino) == (device, inode)
                if same:
                    os.fchdir(child)
            except OSError as error:
                held.remove(child)
                os.close(child)
                _skippable(error)
                continue
            if not same:
                # Replaced since it was listed: skip it, as fts does.
                held.remove(child)
                os.close(child)
                continue
            found = _scan_cwd()
            if found is None:
                return True
            if len(stack) < _SCAN_FD_DEPTH:
                stack.append((iter(found), (device, inode), child))
            else:
                held.remove(child)
                os.close(child)
                stack.append((iter(found), (device, inode), None))
            descended = True
            break
        if descended:
            continue
        _, _, own = stack.pop()
        if own is not None:
            held.remove(own)
            os.close(own)
        if not stack:
            break
        parent_identity, parent_fd = stack[-1][1], stack[-1][2]
        try:
            if parent_fd is not None:
                os.fchdir(parent_fd)
            else:
                os.chdir("..")
        except OSError:
            return True
        if parent_fd is None and not _same_dir(*parent_identity):
            return True
    return False


def read_lines(path: str) -> list[bytes] | None:
    """The lines `sed -n 'Np'` and `awk 'END { print NR }'` see.

    A file holding a NUL byte is refused: bash drops NULs from a command
    substitution and the sed implementations disagree about them, so no
    exact reading exists, and every caller treats a refusal as corrupt.
    """
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    if b"\0" in data:
        return None
    if not data:
        return []
    lines = data.split(b"\n")
    if data.endswith(b"\n"):
        lines.pop()
    return lines


def fails_closed(function):
    """An unexpected error is OpFailed, as an inline interpreter's crash was.

    The bash helpers ran these checks in `python3 -` and treated any nonzero
    exit, a traceback included, as an invalid file. Callers that catch
    OpFailed to choose a compatibility path must see the same for an
    unexpected error (for example RuntimeError from a symlink loop).
    """

    def wrapper(*args):
        try:
            return function(*args)
        except OpFailed:
            raise
        except Exception:
            raise OpFailed from None

    wrapper.__name__ = function.__name__
    wrapper.__doc__ = function.__doc__
    return wrapper


@fails_closed
def read_owner_id(owner_file: str) -> str:
    """mycelium_read_session_owner_id."""
    if not (isfile(owner_file) and not islink(owner_file)):
        raise OpFailed
    lines = read_lines(owner_file)
    if lines is None or len(lines) != 1:
        raise OpFailed
    owner_id = os.fsdecode(lines[0])
    if not valid_session_id(owner_id):
        raise OpFailed
    return owner_id


@fails_closed
def read_marker(repo_root: str, marker_file: str) -> tuple[str, str, str]:
    """mycelium_read_active_log_marker: (safe log path, owner ts, format)."""
    if not (isfile(marker_file) and not islink(marker_file)):
        raise OpFailed
    lines = read_lines(marker_file)
    if lines is None:
        raise OpFailed
    raw_path = os.fsdecode(lines[0]) if len(lines) > 0 else ""
    owner_ts = os.fsdecode(lines[1]) if len(lines) > 1 else ""
    ownership_format = os.fsdecode(lines[2]) if len(lines) > 2 else ""
    if not raw_path or not (
        1 <= len(owner_ts) <= 18 and all(ch in DIGITS for ch in owner_ts)
    ):
        raise OpFailed
    if len(lines) == 2:
        pass
    elif len(lines) == 3:
        if ownership_format != "owner-id-v1":
            raise OpFailed
    else:
        raise OpFailed

    try:
        root = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError):
        raise OpFailed from None
    raw = Path(raw_path)
    if not raw.is_absolute():
        raise OpFailed
    log_root = (root / ".living" / "log").resolve(strict=False)
    candidate = Path(os.path.abspath(raw))
    try:
        relative = candidate.resolve(strict=False).relative_to(log_root)
    except (OSError, RuntimeError, ValueError):
        raise OpFailed from None
    if len(relative.parts) != 1:
        raise OpFailed
    try:
        mode = candidate.lstat().st_mode
    except FileNotFoundError:
        pass
    except OSError:
        raise OpFailed from None
    else:
        if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
            raise OpFailed
    safe_path = str(candidate).rstrip("\n")
    if not safe_path:
        raise OpFailed
    return safe_path, owner_ts, ownership_format


def subst_bytes(path: str) -> bytes:
    """`$(cat PATH 2>/dev/null || true)`: NULs dropped, trailing newlines cut."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        data = b""
    return data.replace(b"\0", b"").rstrip(b"\n")


class State:
    def __init__(self, argv: list[str]) -> None:
        (
            self.pid,
            self.host,
            self.sid_status,
            self.sid,
            self.repo_root,
            self.state_dir,
            self.shared_state_dir,
            self.requested_state,
            self.plugin_root,
        ) = argv[:9]
        self.ops = argv[9:]

    def assign(self, name: str, value: str) -> None:
        if name == "STATE_DIR":
            self.state_dir = value
        elif name == "MYCELIUM_SHARED_STATE_DIR":
            self.shared_state_dir = value
        emit("set", name, value)

    # -- mycelium_prepare_state_dir ---------------------------------------

    def prepare(self, mode: str) -> None:
        if mode not in {"write", "read-only", "bootstrap"}:
            raise OpFailed
        repo_root = physical_dir(self.repo_root)
        living_dir = repo_root + "/.living"
        if islink(living_dir):
            raise OpFailed
        if isdir(living_dir) and contains_symlink(living_dir):
            raise OpFailed

        requested = self.requested_state or repo_root + "/.mycelium"
        requested = self.validate_requested_state(repo_root, requested)
        if mode == "read-only":
            if not isdir(requested):
                raise OpFailed
        else:
            try:
                os.makedirs(requested, exist_ok=True)
            except OSError:
                raise OpFailed from None
        try:
            state_dir = physical_dir(requested)
        except OpFailed:
            # `STATE_DIR=$(cd ... && pwd -P) || return 1` still assigned.
            self.assign("STATE_DIR", "")
            raise
        self.assign("STATE_DIR", state_dir)
        if not state_dir.startswith(repo_root + "/"):
            raise OpFailed
        self.assign("MYCELIUM_SHARED_STATE_DIR", state_dir)
        if contains_symlink(state_dir):
            raise OpFailed
        if mode in {"read-only", "bootstrap"}:
            return
        self.prepare_shared_writes(repo_root, state_dir)

    @staticmethod
    def validate_requested_state(repo_root: str, requested: str) -> str:
        try:
            root = Path(repo_root).resolve(strict=True)
        except (OSError, RuntimeError):
            raise OpFailed from None
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = Path(os.path.abspath(candidate))
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            raise OpFailed from None
        if not relative.parts:
            raise OpFailed
        # Reject every existing symlink or non-directory component before
        # mkdir, so creation cannot follow a repository-controlled link.
        current = root
        for part in relative.parts:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                continue
            except OSError:
                raise OpFailed from None
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise OpFailed
        try:
            candidate.resolve(strict=False).relative_to(root)
        except (OSError, RuntimeError, ValueError):
            raise OpFailed from None
        result = str(candidate).rstrip("\n")
        if not result:
            raise OpFailed
        return result

    def prepare_shared_writes(self, repo_root: str, state_dir: str) -> None:
        plugin_pointer = state_dir + "/plugin-root"
        legacy_dir = repo_root + "/.claude"
        legacy_session = legacy_dir + "/last-session.md"
        for managed in (state_dir + "/.gitignore", state_dir + "/last-session.md"):
            if (exists(managed) or islink(managed)) and (
                not isfile(managed) or islink(managed)
            ):
                raise OpFailed
        if (
            self.plugin_root
            and (exists(plugin_pointer) or islink(plugin_pointer))
            and (not isfile(plugin_pointer) or islink(plugin_pointer))
        ):
            raise OpFailed

        gitignore = state_dir + "/.gitignore"
        if not isfile(gitignore):
            # bash ignored a failed redirection here.
            try:
                descriptor = os.open(
                    gitignore,
                    os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                    0o666,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(b"*\n!.gitignore\n")
            except OSError:
                pass

        # Codex plugin hooks carry their live bundle root through the
        # dispatcher. Refresh the guidance pointer only after the checks
        # above, by atomic replacement rather than writing through a path.
        if self.plugin_root:
            wanted = os.fsencode(self.plugin_root)
            if not isfile(plugin_pointer) or subst_bytes(plugin_pointer) != wanted:
                try:
                    descriptor, temp_name = tempfile.mkstemp(
                        prefix=".plugin-root.tmp.", dir=state_dir
                    )
                except OSError:
                    raise OpFailed from None
                try:
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(wanted + b"\n")
                    os.replace(temp_name, plugin_pointer)
                except OSError:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
                    raise OpFailed from None

        # Preserve cross-session context from projects initialized before
        # v0.4. bash ignored a failed copy here.
        session_file = state_dir + "/last-session.md"
        if (
            not isfile(session_file)
            and isdir(legacy_dir)
            and not islink(legacy_dir)
            and isfile(legacy_session)
            and not islink(legacy_session)
        ):
            try:
                with open(legacy_session, "rb") as source:
                    data = source.read()
                    mode = os.fstat(source.fileno()).st_mode & 0o777
                descriptor = os.open(
                    session_file,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    mode,
                )
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
            except OSError:
                pass

    # -- mycelium_select_session_state ------------------------------------

    def select(self, mode: str) -> None:
        shared = self.shared_state_dir or self.state_dir
        if not (shared and isdir(shared) and not islink(shared)):
            raise OpFailed
        repo_root = physical_dir(self.repo_root)
        if self.sid_status != "ok":
            raise OpFailed
        sid = self.sid

        # Identity-free hosts keep the pre-0.7 flat transaction layout.
        if not sid:
            self.assign("STATE_DIR", shared)
            emit("set", "MYCELIUM_SESSION_SCOPED", "false")
            emit("set", "MYCELIUM_SESSION_HOST", "legacy")
            emit("set", "MYCELIUM_HOST_SESSION_ID", "")
            emit("set", "MYCELIUM_ACTIVE_FLAT_FALLBACK", "false")
            return
        if not valid_session_id(sid):
            raise OpFailed
        host = self.host
        if host not in {"claude", "codex"}:
            raise OpFailed

        run_state = f"{shared}/run/{host}/{sid}"
        flat_marker_file = shared + "/active-session-log.tmp"
        # Rolling-upgrade compatibility: a task started by the prior build
        # may still own the flat transaction. Route only an exact owner match.
        if (
            not exists(run_state)
            and not islink(run_state)
            and isfile(flat_marker_file)
            and not islink(flat_marker_file)
        ):
            try:
                flat_owner = read_owner_id(shared + "/active-session-owner-id.tmp")
            except OpFailed:
                flat_owner = ""
            try:
                read_marker(repo_root, flat_marker_file)
                flat_marker = True
            except OpFailed:
                flat_marker = False
            if flat_marker and flat_owner == sid:
                self.assign("STATE_DIR", shared)
                emit("set", "MYCELIUM_SESSION_SCOPED", "false")
                emit("set", "MYCELIUM_SESSION_HOST", host)
                emit("set", "MYCELIUM_HOST_SESSION_ID", sid)
                emit("set", "MYCELIUM_ACTIVE_FLAT_FALLBACK", "true")
                return

        if mode not in {"create", "read-only"}:
            raise OpFailed
        try:
            shared_path = Path(shared).resolve(strict=True)
        except (OSError, RuntimeError):
            raise OpFailed from None
        current = shared_path
        for part in ("run", host, sid):
            current = current / part
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if mode != "create":
                    raise OpFailed from None
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    try:
                        metadata = current.lstat()
                    except OSError:
                        raise OpFailed from None
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(
                        metadata.st_mode
                    ):
                        raise OpFailed from None
                except OSError:
                    raise OpFailed from None
            except OSError:
                raise OpFailed from None
            else:
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise OpFailed
        try:
            candidate = current.resolve(strict=True)
            candidate.relative_to(shared_path)
        except (OSError, RuntimeError, ValueError):
            raise OpFailed from None
        run_dir = str(candidate).rstrip("\n")
        if not run_dir:
            raise OpFailed
        self.assign("STATE_DIR", run_dir)
        emit("set", "MYCELIUM_SESSION_SCOPED", "true")
        emit("set", "MYCELIUM_SESSION_HOST", host)
        emit("set", "MYCELIUM_HOST_SESSION_ID", sid)
        emit("set", "MYCELIUM_ACTIVE_FLAT_FALLBACK", "false")

    # -- mycelium_payload_owns_active_session -----------------------------

    def owns(self) -> None:
        marker_file = self.state_dir + "/active-session-log.tmp"
        owner_file = self.state_dir + "/active-session-owner-id.tmp"
        if self.sid_status != "ok":
            raise OpFailed
        host_identified = False
        if self.sid:
            if not valid_session_id(self.sid):
                raise OpFailed
            host_identified = True

        def owner_absent() -> bool:
            return not exists(owner_file) and not islink(owner_file)

        # A host-identified event with no active transaction is a delayed
        # event from a completed or superseded task. Only identity-free
        # legacy payloads keep the pre-owner compatibility behavior.
        if not isfile(marker_file):
            if host_identified or not owner_absent():
                raise OpFailed
            return
        try:
            _, _, owner_format = read_marker(self.repo_root, marker_file)
        except OpFailed:
            # A corrupt marker never authorizes a log write, but must not
            # disable independent activity enforcement for legacy payloads.
            if host_identified or not owner_absent():
                raise
            return
        if owner_format != "owner-id-v1" and owner_absent():
            return
        owner_id = read_owner_id(owner_file)
        if not (host_identified and self.sid == owner_id):
            raise OpFailed

    # -- locks --------------------------------------------------------------

    def lock(self, kind: str) -> None:
        """One mkdir(2) attempt of mycelium_acquire_directory_lock."""
        directory = self.shared_state_dir if kind == "stop" else self.state_dir
        if not (directory and isdir(directory) and not islink(directory)):
            raise OpFailed
        lock_dir = directory + "/" + LOCK_NAMES[kind]
        if exists(lock_dir) or islink(lock_dir):
            raise Busy
        try:
            os.mkdir(lock_dir)
        except OSError:
            raise Busy from None
        try:
            descriptor = os.open(
                lock_dir + "/owner",
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW,
                0o666,
            )
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(f"{self.pid} {int(time.time())}\n".encode())
        except OSError:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass
            raise OpFailed from None
        emit("lock", kind, lock_dir)

    # -- reads that never fail the sequence ---------------------------------

    def marker(self, marker_file: str) -> None:
        try:
            present = isfile(marker_file)
        except Exception:
            present = True
        if not present:
            emit("marker", "absent", "", "", "")
            return
        try:
            path, owner_ts, owner_format = read_marker(self.repo_root, marker_file)
        except Exception:
            emit("marker", "invalid", "", "", "")
            return
        emit("marker", "valid", path, owner_ts, owner_format)

    def owner(self, owner_file: str) -> None:
        try:
            owner_id = read_owner_id(owner_file)
        except Exception:
            emit("owner", "invalid", "")
            return
        emit("owner", "valid", owner_id)

    # -- driver -------------------------------------------------------------

    def run(self) -> None:
        for index, op in enumerate(self.ops, start=1):
            try:
                if op.startswith("prepare:"):
                    self.prepare(op.split(":", 1)[1])
                elif op.startswith("select:"):
                    self.select(op.split(":", 1)[1])
                elif op == "owns":
                    self.owns()
                elif op == "require-marker":
                    marker_file = self.state_dir + "/active-session-log.tmp"
                    if not exists(marker_file) and not islink(marker_file):
                        raise OpFailed
                elif op in {"lock:stop", "lock:session"}:
                    self.lock(op.split(":", 1)[1])
                elif op == "marker":
                    self.marker(self.state_dir + "/active-session-log.tmp")
                elif op == "owner":
                    self.owner(self.state_dir + "/active-session-owner-id.tmp")
                elif op.startswith("marker-at="):
                    self.marker(op[len("marker-at="):])
                elif op.startswith("owner-at="):
                    self.owner(op[len("owner-at="):])
                else:
                    raise OpFailed
            except Busy:
                emit("busy", str(index))
                break
            except Exception:
                emit("fail", str(index))
                break
            emit("ok", str(index))
        emit("end")


def main(argv: list[str]) -> int:
    if len(argv) < 9:
        emit("fail", "1")
        emit("end")
        return 0
    State(argv).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
