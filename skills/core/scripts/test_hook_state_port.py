"""The batched state runner keeps the contract of the bash helpers it replaced.

`mycelium__state` (mycelium-hook-lib.sh) runs the lifecycle state helpers in
one `mycelium_hook_state.py` process. `fixtures/hook_state_reference.sh` holds
those helpers as they were before the move. Every scenario below builds the
same adversarial repository twice at the same path, runs one helper sequence
against the reference and against the live library, and requires identical
return statuses, shell variables, printed output, and filesystem effects.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import stat
import subprocess
import time
from pathlib import Path
from typing import Callable

import pytest

SCRIPTS = Path(__file__).resolve().parent
HOOKS_DIR = SCRIPTS.parent / "hooks"
LIB = HOOKS_DIR / "mycelium-hook-lib.sh"
REFERENCE = SCRIPTS / "fixtures" / "hook_state_reference.sh"

VARIABLES = (
    "STATE_DIR",
    "MYCELIUM_SHARED_STATE_DIR",
    "MYCELIUM_SESSION_SCOPED",
    "MYCELIUM_SESSION_HOST",
    "MYCELIUM_HOST_SESSION_ID",
    "MYCELIUM_ACTIVE_FLAT_FALLBACK",
    "MYCELIUM_SESSION_LOCK_DIR",
)

DRIVER = r"""
set -u
source "$1"
shift
# %q output depends on the locale on bash 3.2; format in the C locale so runs
# under different locales compare only what the helpers did.
quote() {
  local LC_ALL=C
  printf '%s=%q\n' "$1" "$2"
}
dump() {
  local name
  printf 'rc=%s\n' "$1"
  for name in VARIABLES; do
    # The library starts with empty lock variables (an inherited one is not
    # ours); empty and unset both mean "holds no lock".
    if [[ -n "${!name+set}" && ( "$name" != *_LOCK_DIR || -n "${!name}" ) ]]; then
      quote "$name" "${!name}"
    else
      printf '%s unset\n' "$name"
    fi
  done
}
while (( $# > 0 )); do
  step="$1"
  shift
  case "$step" in
    prepare) out=$(mycelium_prepare_state_dir "$REPO" "$1" 2>/dev/null; printf 'x'); mycelium_prepare_state_dir "$REPO" "$1" >/dev/null 2>&1; rc=$?; shift ;;
    select) mycelium_select_session_state "$REPO" "$PAYLOAD" "$1" >/dev/null 2>&1; rc=$?; shift ;;
    owns) mycelium_payload_owns_active_session "$REPO" "$PAYLOAD" >/dev/null 2>&1; rc=$? ;;
    post) mycelium_prepare_post_tool_state "$REPO" "$PAYLOAD" >/dev/null 2>&1; rc=$? ;;
    locked) mycelium_prepare_locked_post_tool_state "$REPO" "$PAYLOAD" >/dev/null 2>&1; rc=$? ;;
    marker) out=$(mycelium_read_active_log_marker "$REPO" "$1" 2>/dev/null); rc=$?; quote out "$out"; shift ;;
    owner) out=$(mycelium_read_session_owner_id "$1" 2>/dev/null); rc=$?; quote out "$out"; shift ;;
  esac
  printf '== %s\n' "$step"
  dump "$rc"
done
""".replace("VARIABLES", " ".join(VARIABLES))


def _payload(session_id: object = "s1", **extra: object) -> str:
    payload: dict[str, object] = dict(extra)
    if session_id is not _MISSING:
        payload["session_id"] = session_id
    return json.dumps(payload)


_MISSING = object()


def _snapshot(root: Path) -> list[tuple[str, ...]]:
    """Every path under root with its type, mode, and content or target."""
    rows: list[tuple[str, ...]] = []
    # Iterative: os.walk recurses before Python 3.12, and the deep fixtures
    # are 1,100 directories deep.
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            names = os.listdir(current)
        except OSError:
            continue
        for name in sorted(names):
            path = Path(current) / name
            relative = str(path.relative_to(root))
            try:
                info = path.lstat()
            except OSError:
                # Deeper than PATH_MAX: identical in both runs, unreadable here.
                rows.append((relative, "unreachable"))
                continue
            if stat.S_ISLNK(info.st_mode):
                rows.append((relative, "link", os.readlink(path)))
            elif stat.S_ISDIR(info.st_mode):
                rows.append((relative, "dir", oct(info.st_mode & 0o777)))
                pending.append(path)
            else:
                content = path.read_bytes()
                if name == "owner":
                    content = re.sub(rb"^\d+ \d+\n$", b"<pid> <ts>\n", content)
                rows.append((relative, "file", oct(info.st_mode & 0o777), content.hex()))
    return sorted(rows)


def _base(repo: Path) -> None:
    (repo / ".living" / "log").mkdir(parents=True)
    (repo / ".living" / "learnings.md").write_text("# learnings\n")


def _run_dir(repo: Path, host: str = "claude", sid: str = "s1") -> Path:
    run = repo / ".mycelium" / "run" / host / sid
    run.mkdir(parents=True)
    return run


def _marker(repo: Path, where: Path, body: bytes | None = None) -> None:
    log = repo / ".living" / "log" / "2026-11-14-001-repo.md"
    log.write_text("---\nsession_id: 2026-11-14-001\n---\n")
    if body is None:
        body = f"{log}\n1700000000\nowner-id-v1\n".encode()
    (where / "active-session-log.tmp").write_bytes(body)


def _owner(where: Path, body: bytes = b"s1\n") -> None:
    (where / "active-session-owner-id.tmp").write_bytes(body)


def _active(repo: Path, sid: str = "s1", owner: bytes | None = None) -> Path:
    if not (repo / ".living").exists():
        _base(repo)
    run = _run_dir(repo, sid=sid)
    _marker(repo, run)
    _owner(run, owner if owner is not None else f"{sid}\n".encode())
    return run


def _outside(repo: Path) -> Path:
    target = repo.parent / "outside"
    target.mkdir(exist_ok=True)
    return target


Builder = Callable[[Path], None]


def _scenarios() -> dict[str, tuple[Builder, dict[str, str], list[str]]]:
    full = ["prepare", "write", "select", "read-only", "owns"]
    locked = ["locked"]
    out: dict[str, tuple[Builder, dict[str, str], list[str]]] = {}

    def add(name: str, build: Builder, env: dict[str, str], steps: list[str]) -> None:
        out[name] = (build, env, steps)

    for mode in ("write", "read-only", "bootstrap", "bogus"):
        add(f"fresh-prepare-{mode}", _base, {}, ["prepare", mode])
    add("fresh-select-create", _base, {}, ["prepare", "write", "select", "create"])
    add("fresh-select-bogus-mode", _base, {}, ["prepare", "write", "select", "bogus"])
    add("fresh-full", _base, {}, full)
    add("fresh-locked", _base, {}, locked)
    add("fresh-locked-legacy", _base, {"PAYLOAD": _payload(_MISSING)}, locked)

    def living_link(repo: Path) -> None:
        repo.mkdir(exist_ok=True)
        (repo / ".living").symlink_to(_outside(repo))

    def link_inside_living(repo: Path) -> None:
        _base(repo)
        (repo / ".living" / "log" / "evil").symlink_to(_outside(repo))

    def state_link(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium").symlink_to(_outside(repo))

    def state_file(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium").write_text("not a directory\n")

    def link_inside_state(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium" / "deep").mkdir(parents=True)
        (repo / ".mycelium" / "deep" / "evil").symlink_to(_outside(repo))

    def state_component_link(repo: Path) -> None:
        _base(repo)
        (repo / "real").mkdir()
        (repo / "alias").symlink_to(repo / "real")

    for name, build in {
        "living-link": living_link,
        "link-inside-living": link_inside_living,
        "state-link": state_link,
        "state-file": state_file,
        "link-inside-state": link_inside_state,
    }.items():
        for mode in ("write", "read-only", "bootstrap"):
            add(f"{name}-{mode}", build, {}, ["prepare", mode])
        add(f"{name}-locked", build, {}, locked)

    def state_unsearchable(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium").mkdir()
        (repo / ".mycelium").chmod(0o600)

    for mode in ("write", "read-only", "bootstrap"):
        add(f"state-unsearchable-{mode}", state_unsearchable, {}, ["prepare", mode])

    def living_mode(mode: int, where: str) -> Builder:
        def build(repo: Path) -> None:
            _base(repo)
            (repo / where).chmod(mode)
        return build

    # find cannot read or enter these and prints nothing, so they are safe.
    for label, mode in {"unreadable": 0o300, "unsearchable": 0o600}.items():
        for where in (".living", ".living/log"):
            add(f"living-{where.strip('.').replace('/', '-')}-{label}", living_mode(mode, where), {},
                ["prepare", "write", "prepare", "read-only"])

    def deep(where: str, link: bool) -> Builder:
        """1,100 nested directories: past Python's recursion limit and,
        with the path, past macOS PATH_MAX."""
        def build(repo: Path) -> None:
            _base(repo)
            top = repo / where
            top.mkdir(parents=True, exist_ok=True)
            fd = os.open(top, os.O_RDONLY)
            try:
                for _ in range(1100):
                    os.mkdir("d", dir_fd=fd)
                    child = os.open("d", os.O_RDONLY, dir_fd=fd)
                    os.close(fd)
                    fd = child
                if link:
                    os.symlink("/tmp", "evil", dir_fd=fd)
            finally:
                os.close(fd)
        return build

    for where in (".mycelium", ".living/findings"):
        for link in (False, True):
            label = f"deep-{where.strip('.').replace('/', '-')}-{'link' if link else 'plain'}"
            add(label, deep(where, link), {}, ["prepare", "bootstrap", "prepare", "read-only"])

    add("state-env-outside", _base, {"MYCELIUM_STATE_DIR": "/tmp"}, ["prepare", "write"])
    add("state-env-relative", _base, {"MYCELIUM_STATE_DIR": "custom/state"}, full)
    add("state-env-root", _base, {"MYCELIUM_STATE_DIR": "."}, ["prepare", "write"])
    add("state-env-dotdot", _base, {"MYCELIUM_STATE_DIR": "a/../../x"}, ["prepare", "write"])
    add(
        "state-env-through-link",
        state_component_link,
        {"MYCELIUM_STATE_DIR": "alias/state"},
        ["prepare", "write"],
    )

    def managed(name: str, kind: str) -> Builder:
        def build(repo: Path) -> None:
            _base(repo)
            (repo / ".mycelium").mkdir()
            target = repo / ".mycelium" / name
            if kind == "dir":
                target.mkdir()
            elif kind == "link":
                target.symlink_to(_outside(repo) / "x")
            elif kind == "custom":
                target.write_text("custom\n")
            elif kind == "nul":
                target.write_bytes(b"/plugin\0/root\n\n")

        return build

    for name in (".gitignore", "last-session.md", "plugin-root"):
        for kind in ("dir", "link", "custom"):
            add(
                f"managed-{name}-{kind}",
                managed(name, kind),
                {"MYCELIUM_PLUGIN_ROOT": "/plugin/root"},
                ["prepare", "write", "prepare", "read-only"],
            )
    add(
        "plugin-pointer-current",
        managed("plugin-root", "custom"),
        {"MYCELIUM_PLUGIN_ROOT": "custom"},
        ["prepare", "write"],
    )
    add(
        "plugin-pointer-nul",
        managed("plugin-root", "nul"),
        {"MYCELIUM_PLUGIN_ROOT": "/plugin/root"},
        ["prepare", "write"],
    )
    add(
        "plugin-pointer-no-env",
        managed("plugin-root", "dir"),
        {},
        ["prepare", "write"],
    )

    def legacy_handoff(repo: Path) -> None:
        _base(repo)
        (repo / ".claude").mkdir()
        handoff = repo / ".claude" / "last-session.md"
        handoff.write_text("legacy handoff\n")
        handoff.chmod(0o640)

    def legacy_handoff_link(repo: Path) -> None:
        _base(repo)
        (repo / ".claude").symlink_to(_outside(repo))
        (_outside(repo) / "last-session.md").write_text("outside\n")

    add("legacy-handoff", legacy_handoff, {}, ["prepare", "write"])
    add("legacy-handoff-read-only", legacy_handoff, {}, ["prepare", "bootstrap"])
    add("legacy-handoff-link", legacy_handoff_link, {}, ["prepare", "write"])

    for label, session_id in {
        "missing": _MISSING,
        "null": None,
        "empty": "",
        "dot": ".",
        "dotdot": "..",
        "space": "a b",
        "slash": "a/b",
        "long": "x" * 201,
        "max": "x" * 200,
        "number": 12,
        "list": ["s1"],
        "unicode": "sé",
        "newline": "s1\n",
    }.items():
        payload = _payload(session_id)
        add(f"sid-{label}-full", _base, {"PAYLOAD": payload}, full + ["select", "create"])
        add(f"sid-{label}-active", lambda repo: _active(repo), {"PAYLOAD": payload}, locked)
    add("payload-not-json", _base, {"PAYLOAD": "{not json"}, full)
    add("payload-list", _base, {"PAYLOAD": "[1]"}, full)

    for host in ("codex", "CODEX", "other"):
        add(f"host-{host}", lambda repo: _active(repo), {"MYCELIUM_HOOK_HOST": host}, full)
        add(f"host-{host}-create", _base, {"MYCELIUM_HOOK_HOST": host}, ["prepare", "write", "select", "create"])

    def run_link(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium").mkdir()
        (repo / ".mycelium" / "run").symlink_to(_outside(repo))

    def host_file(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium" / "run").mkdir(parents=True)
        (repo / ".mycelium" / "run" / "claude").write_text("file\n")

    def session_link(repo: Path) -> None:
        _base(repo)
        (repo / ".mycelium" / "run" / "claude").mkdir(parents=True)
        (repo / ".mycelium" / "other").mkdir()
        (repo / ".mycelium" / "run" / "claude" / "s1").symlink_to(repo / ".mycelium" / "other")

    for name, build in {"run-link": run_link, "host-file": host_file, "session-link": session_link}.items():
        add(f"{name}-create", build, {}, ["prepare", "bootstrap", "select", "create"])
        add(f"{name}-read-only", build, {}, ["prepare", "bootstrap", "select", "read-only"])

    def flat(owner: bytes, marker: bytes | None = None, with_run: bool = False) -> Builder:
        def build(repo: Path) -> None:
            _base(repo)
            shared = repo / ".mycelium"
            shared.mkdir()
            _marker(repo, shared, marker)
            _owner(shared, owner)
            if with_run:
                _run_dir(repo)

        return build

    add("flat-match", flat(b"s1\n"), {}, full)
    add("flat-match-locked", flat(b"s1\n"), {}, locked)
    add("flat-mismatch", flat(b"s2\n"), {}, full + ["select", "create"])
    add("flat-owner-two-lines", flat(b"s1\nextra\n"), {}, full)
    add("flat-owner-blank-tail", flat(b"s1\n\n"), {}, full)
    add("flat-marker-invalid", flat(b"s1\n", b"relative\n1\n"), {}, full)
    add("flat-run-exists", flat(b"s1\n", None, True), {}, full)
    add("flat-select-bogus-mode", flat(b"s1\n"), {}, ["prepare", "write", "select", "bogus"])
    add("flat-legacy-payload", flat(b"s1\n"), {"PAYLOAD": _payload(_MISSING)}, full + locked)

    def marker_case(body_for: Callable[[Path], bytes]) -> Builder:
        def build(repo: Path) -> None:
            _base(repo)
            run = _run_dir(repo)
            log = repo / ".living" / "log" / "2026-11-14-001-repo.md"
            log.write_text("log\n")
            (run / "active-session-log.tmp").write_bytes(body_for(repo))
            _owner(run)

        return build

    def log_path(repo: Path) -> str:
        return str(repo / ".living" / "log" / "2026-11-14-001-repo.md")

    marker_bodies: dict[str, Callable[[Path], bytes]] = {
        "two-lines": lambda r: f"{log_path(r)}\n1700000000\n".encode(),
        "three-lines": lambda r: f"{log_path(r)}\n1700000000\nowner-id-v1\n".encode(),
        "no-newline": lambda r: f"{log_path(r)}\n1700000000\nowner-id-v1".encode(),
        "bad-format": lambda r: f"{log_path(r)}\n1700000000\nowner-id-v2\n".encode(),
        "four-lines": lambda r: f"{log_path(r)}\n1700000000\nowner-id-v1\nmore\n".encode(),
        "blank-third": lambda r: f"{log_path(r)}\n1700000000\n\n".encode(),
        "ts-letters": lambda r: f"{log_path(r)}\nabc\n".encode(),
        "ts-19-digits": lambda r: f"{log_path(r)}\n{'1' * 19}\n".encode(),
        "ts-18-digits": lambda r: f"{log_path(r)}\n{'1' * 18}\n".encode(),
        "crlf": lambda r: f"{log_path(r)}\r\n1700000000\r\n".encode(),
        "empty": lambda r: b"",
        "only-newline": lambda r: b"\n",
        "relative": lambda r: b".living/log/x.md\n1700000000\n",
        "outside": lambda r: b"/tmp/x.md\n1700000000\n",
        "nested": lambda r: f"{r}/.living/log/sub/x.md\n1700000000\n".encode(),
        "log-root": lambda r: f"{r}/.living/log\n1700000000\n".encode(),
        "dotdot": lambda r: f"{r}/.living/log/../log/x.md\n1700000000\n".encode(),
        "double-slash": lambda r: f"{r}/.living//log/./x.md\n1700000000\n".encode(),
        "missing-log": lambda r: f"{r}/.living/log/missing.md\n1700000000\n".encode(),
        "trailing-space": lambda r: f"{log_path(r)} \n1700000000\n".encode(),
        "invalid-utf8": lambda r: f"{r}/.living/log/".encode() + b"\xff.md\n1700000000\n",
    }
    for label, body in marker_bodies.items():
        add(f"marker-{label}", marker_case(body), {}, ["locked", "marker", "@run/active-session-log.tmp"])
        add(f"marker-{label}-legacy", marker_case(body), {"PAYLOAD": _payload(_MISSING)}, ["locked"])

    def marker_target(kind: str) -> Builder:
        def build(repo: Path) -> None:
            _base(repo)
            run = _run_dir(repo)
            target = repo / ".living" / "log" / "x.md"
            if kind == "link":
                target.symlink_to(_outside(repo) / "x.md")
            elif kind == "dir":
                target.mkdir()
            (run / "active-session-log.tmp").write_text(f"{target}\n1700000000\nowner-id-v1\n")
            _owner(run)

        return build

    for kind in ("link", "dir"):
        add(f"marker-target-{kind}", marker_target(kind), {}, ["locked", "marker", "@run/active-session-log.tmp"])

    def marker_object(kind: str) -> Builder:
        def build(repo: Path) -> None:
            _base(repo)
            run = _run_dir(repo)
            _marker(repo, repo / ".living" / "log")
            marker = run / "active-session-log.tmp"
            if kind == "dir":
                marker.mkdir()
            _owner(run)

        return build

    add("marker-is-dir", marker_object("dir"), {}, locked)
    add("marker-missing", marker_object("missing"), {}, locked)
    add("marker-missing-legacy", marker_object("missing"), {"PAYLOAD": _payload(_MISSING)}, locked)

    owner_bodies = {
        "valid": b"s1\n",
        "no-newline": b"s1",
        "two-lines": b"s1\ns2\n",
        "blank-tail": b"s1\n\n",
        "space": b"s 1\n",
        "empty": b"",
        "other": b"s2\n",
        "dot": b".\n",
        "crlf": b"s1\r\n",
    }
    for label, body in owner_bodies.items():
        add(
            f"owner-{label}",
            lambda repo, body=body: _active(repo, owner=body),
            {},
            ["locked", "owner", "@run/active-session-owner-id.tmp"],
        )
    add("owner-missing", lambda repo: (_active(repo), os.unlink(repo / ".mycelium/run/claude/s1/active-session-owner-id.tmp")) and None, {}, locked)
    add("owner-missing-legacy-format", lambda repo: (_active(repo), _marker(repo, repo / ".mycelium/run/claude/s1", f"{repo}/.living/log/2026-11-14-001-repo.md\n1700000000\n".encode()), os.unlink(repo / ".mycelium/run/claude/s1/active-session-owner-id.tmp")) and None, {}, locked)
    add("owner-is-dir", lambda repo: (_active(repo), os.unlink(repo / ".mycelium/run/claude/s1/active-session-owner-id.tmp"), (repo / ".mycelium/run/claude/s1/active-session-owner-id.tmp").mkdir()) and None, {}, locked)
    def linked_state_files(repo: Path) -> None:
        run = _active(repo)
        (repo / "marker.tmp").symlink_to(run / "active-session-log.tmp")
        (repo / "owner.tmp").symlink_to(run / "active-session-owner-id.tmp")

    add("marker-link", linked_state_files, {}, ["marker", "@repo/marker.tmp", "marker", "@run/active-session-log.tmp"])
    add("owner-link", linked_state_files, {}, ["owner", "@repo/owner.tmp", "owner", "@run/active-session-owner-id.tmp"])
    add("active-select-bogus-mode", lambda repo: _active(repo), {}, ["prepare", "write", "select", "bogus"])
    add("owner-path-missing", _base, {}, ["owner", "@repo/none"])
    add("marker-path-missing", _base, {}, ["marker", "@repo/none"])

    def lock_held(repo: Path) -> None:
        run = _active(repo)
        lock = run / "mycelium-session.lock"
        lock.mkdir()
        (lock / "owner").write_text("999999 1\n")

    def lock_link(repo: Path) -> None:
        run = _active(repo)
        (run / "mycelium-session.lock").symlink_to(_outside(repo))

    add("lock-dead-owner", lock_held, {}, locked)
    add("lock-is-link", lock_link, {"MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS": "2"}, locked)
    add("active-owned", lambda repo: _active(repo), {}, locked + full)
    add("active-other-owner", lambda repo: _active(repo, owner=b"s2\n"), {}, locked + full)
    return out


SCENARIOS = _scenarios()


def _resolve(steps: list[str], repo: Path) -> list[str]:
    run = repo / ".mycelium" / "run" / "claude" / "s1"
    resolved = []
    for step in steps:
        if step.startswith("@run/"):
            resolved.append(str(run / step[len("@run/"):]))
        elif step.startswith("@repo/"):
            resolved.append(str(repo / step[len("@repo/"):]))
        else:
            resolved.append(step)
    return resolved


def _execute(tmp_path: Path, library: Path, build: Builder, env_extra: dict[str, str], steps: list[str], locale: str = "C") -> tuple[str, list[tuple[str, ...]]]:
    work = tmp_path / "work"
    if work.exists():
        # chmod -R and rm -rf walk trees deeper than PATH_MAX.
        subprocess.run(["chmod", "-R", "u+rwx", str(work)], check=False)
        subprocess.run(["rm", "-rf", str(work)], check=True)
    work.mkdir()
    repo = work / "repo"
    repo.mkdir()
    build(repo)
    env = os.environ.copy()
    env.pop("MYCELIUM_STATE_DIR", None)
    env.pop("MYCELIUM_PLUGIN_ROOT", None)
    for name in ("LANG", "LC_CTYPE", "PYTHONIOENCODING", "PYTHONUTF8"):
        env.pop(name, None)
    env["LC_ALL"] = locale
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["REPO"] = str(repo.resolve())
    env["PAYLOAD"] = _payload()
    env["MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS"] = "3"
    env.update(env_extra)
    result = subprocess.run(
        ["bash", "-c", DRIVER, "driver", str(library), *_resolve(steps, repo.resolve())],
        capture_output=True,
        env=env,
        cwd=work,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout.decode(errors="surrogateescape"), _snapshot(work)


# Python's stdout uses surrogateescape in the C and C.UTF-8 locales but is
# strict in a locale such as en_US.UTF-8, so the old helpers' print() of a
# path holding non-UTF-8 bytes crashed there and the marker read as corrupt.
# The runner writes bytes and accepts such a marker in every locale (a
# documented change), which is what the old helpers did in the C locale.
LOCALES = ("C", "C.UTF-8", "en_US.UTF-8")
LOCALE_DEPENDENT_IN_REFERENCE = {"marker-invalid-utf8", "marker-invalid-utf8-legacy"}


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_state_runner_matches_reference_helpers(tmp_path, name, locale):
    build, env_extra, steps = SCENARIOS[name]
    reference_locale = "C" if name in LOCALE_DEPENDENT_IN_REFERENCE else locale
    reference = _execute(tmp_path, REFERENCE, build, env_extra, steps, reference_locale)
    live = _execute(tmp_path, LIB, build, env_extra, steps, locale)
    assert live[0] == reference[0]
    assert live[1] == reference[1]


def test_the_reference_rejected_a_non_utf8_marker_only_in_strict_locales(tmp_path):
    """Pins the behaviour change above: the old helpers called this marker
    corrupt under en_US.UTF-8, the runner accepts it as they did under C."""
    build, env_extra, steps = SCENARIOS["marker-invalid-utf8"]
    strict = _execute(tmp_path, REFERENCE, build, env_extra, steps, "en_US.UTF-8")
    lenient = _execute(tmp_path, REFERENCE, build, env_extra, steps, "C")
    live = _execute(tmp_path, LIB, build, env_extra, steps, "en_US.UTF-8")
    if strict[0] == lenient[0]:
        pytest.skip("en_US.UTF-8 is not installed here, so stdout is not strict")
    assert live[0] == lenient[0] != strict[0]


@pytest.mark.parametrize(
    ("label", "write"),
    [
        ("marker", lambda run, repo: (run / "active-session-log.tmp").write_bytes(
            f"{repo}/.living/log/2026-11-14-001-repo.md\n17000\x0000000\nowner-id-v1\n".encode())),
        ("owner", lambda run, repo: (run / "active-session-owner-id.tmp").write_bytes(b"s\x001\n")),
    ],
)
def test_state_files_holding_nul_bytes_are_refused(tmp_path, label, write):
    """bash dropped NUL bytes and the sed builds disagreed; refuse instead."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _base(repo)
    run = _active(repo)
    write(run, repo.resolve())
    script = r"""
source "$1"
mycelium_read_active_log_marker "$2" "$3/active-session-log.tmp" >/dev/null; printf 'marker=%s\n' "$?"
mycelium_read_session_owner_id "$3/active-session-owner-id.tmp" >/dev/null; printf 'owner=%s\n' "$?"
"""
    result = subprocess.run(
        ["bash", "-c", script, "probe", str(LIB), str(repo.resolve()), str(run.resolve())],
        capture_output=True,
        text=True,
        check=True,
    )
    assert f"{label}=1" in result.stdout
    other = "owner" if label == "marker" else "marker"
    assert f"{other}=0" in result.stdout


def test_a_held_lock_falls_back_and_revalidates_after_acquiring(tmp_path):
    """A busy lock runs the bash wait loop; the checks after it still run.

    The session lock is held by a live process. While the event waits, Stop
    accepts the transaction (the marker disappears) and releases the lock.
    The event must take the lock, find no transaction, and release it; it
    never writes into the accepted transaction. Its real edit then opens a
    fresh one for the same host session.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _base(repo)
    run = _active(repo)
    holder = subprocess.Popen(["sleep", "30"])
    try:
        lock = run / "mycelium-session.lock"
        lock.mkdir()
        (lock / "owner").write_text(f"{holder.pid} {int(time.time())}\n")
        env = os.environ.copy()
        env["MYCELIUM_HOOK_HOST"] = "claude"
        event = {
            "session_id": "s1",
            "cwd": str(repo),
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "a.py")},
            "tool_response": {"success": True},
        }
        hook = subprocess.Popen(
            [str(HOOKS_DIR / "mycelium-activity-tracker.sh")],
            cwd=repo,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        hook.stdin.write(json.dumps(event).encode())
        hook.stdin.close()
        time.sleep(1.0)
        assert hook.poll() is None, "the event did not wait for the held lock"
        (run / "active-session-log.tmp").unlink()
        (run / "active-session-owner-id.tmp").unlink()
        (lock / "owner").unlink()
        lock.rmdir()
        assert hook.wait(timeout=30) == 0
    finally:
        holder.send_signal(signal.SIGTERM)
        holder.wait()
    marker = (run / "active-session-log.tmp").read_text().splitlines()
    assert Path(marker[0]).parent == repo / ".living" / "log"
    assert (run / "mycelium-session-activity.tmp").read_text() == "a.py\n"
    assert not (run / "mycelium-session.lock").exists()


def test_reference_fixture_is_the_pre_port_library():
    """The oracle must stay the code the port replaced."""
    text = REFERENCE.read_text()
    assert "commit e3090da" in text
    assert "python3 - \"$repo_root\" \"$requested_state\" <<'PY'" in text
    assert shutil.which("bash")


def _state_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "mycelium_hook_state_under_test", HOOKS_DIR / "mycelium_hook_state.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_unexpected_error_reading_state_reads_as_invalid(tmp_path, monkeypatch, capsysbinary):
    """The old inline interpreters crashed to a nonzero exit, which bash read
    as an invalid marker or owner. A RuntimeError from a symlink loop (Python
    3.12 and older) must do the same here, not abort the whole sequence: an
    aborted Stop sequence used to exit silently instead of blocking."""
    module = _state_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    run = _active(repo)
    real_resolve = Path.resolve

    def looping_resolve(self, strict=False):
        if self.name == "log":
            raise RuntimeError("Symlink loop from " + str(self))
        return real_resolve(self, strict=strict)

    monkeypatch.setattr(Path, "resolve", looping_resolve)
    with pytest.raises(module.OpFailed):
        module.read_marker(str(repo), str(run / "active-session-log.tmp"))

    def state(*ops, sid="s1"):
        return module.State(
            ["1", "claude", "ok", sid, str(repo), str(run), str(repo / ".mycelium"), "", "", *ops]
        )

    state("marker", "owner", "owns").run()
    out = capsysbinary.readouterr().out.split(b"\0")
    assert out[:5] == [b"marker", b"invalid", b"", b"", b""]
    assert out[5:7] == [b"ok", b"1"]
    assert out[7:11] == [b"owner", b"valid", b"s1", b"ok"]
    # An identified event cannot be authorized by an unreadable marker.
    assert out[12:14] == [b"fail", b"3"]

    # An identity-free payload with no owner token keeps the reference's
    # compatibility path for a corrupt marker.
    (run / "active-session-owner-id.tmp").unlink()
    state("owns", sid="").run()
    assert capsysbinary.readouterr().out.split(b"\0")[:2] == [b"ok", b"1"]


def _dying_state_python(tmp_path: Path, cut: bytes, keep_cut: bool) -> dict[str, str]:
    """PATH whose python3 runs normally, except that the state module's
    report stops at the first `cut` record (kept or not), as if the process
    had been killed there."""
    bin_dir = tmp_path / "dying-bin"
    bin_dir.mkdir()
    real = shutil.which("python3")
    shim = bin_dir / "python3"
    shim.write_text(
        f"#!{real}\n"
        "import subprocess, sys\n"
        f"argv = [{real!r}] + sys.argv[1:]\n"
        "if not any(a.endswith('mycelium_hook_state.py') for a in sys.argv[1:]):\n"
        "    raise SystemExit(subprocess.call(argv))\n"
        "out = subprocess.run(argv, stdout=subprocess.PIPE).stdout\n"
        f"cut, keep = {cut!r}, {keep_cut!r}\n"
        "at = out.find(cut)\n"
        "if at >= 0:\n"
        "    out = out[:out.index(b'\\0', at + len(cut)) + 1] if keep else out[:at]\n"
        "sys.stdout.buffer.write(out)\n"
    )
    shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    return env


def _hook(name: str, payload: dict, repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(HOOKS_DIR / name)], cwd=repo, input=json.dumps(payload), text=True,
        capture_output=True, env=env, timeout=60,
    )


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _base(repo)
    for name in ("decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    return repo


def test_session_start_releases_both_locks_when_the_state_process_dies(tmp_path):
    """A state process that dies after taking both locks leaves neither."""
    repo = _git_repo(tmp_path)
    env = _dying_state_python(tmp_path, b"lock\0session\0", keep_cut=True)
    start = {"session_id": "s1", "cwd": str(repo), "hook_event_name": "SessionStart", "source": "startup"}
    result = _hook("mycelium-health.sh", start, repo, env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    run = repo / ".mycelium" / "run" / "claude" / "s1"
    assert run.is_dir()
    assert not (run / "mycelium-session.lock").exists()
    assert not (repo / ".mycelium" / "mycelium-stop.lock").exists()
    assert not (run / "active-session-log.tmp").exists()


def test_a_dead_marker_read_blocks_stop_and_still_tracks_edits(tmp_path):
    """The marker and owner reads never fail, so a failure there is a dead
    process. Stop must block rather than accept; an authorized PostToolUse
    event carries on with the marker unread, as the old helpers did when
    their marker reader crashed."""
    repo = _git_repo(tmp_path)
    plain = os.environ.copy()
    plain["MYCELIUM_HOOK_HOST"] = "claude"
    plain["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    base = {"session_id": "s1", "cwd": str(repo)}
    assert _hook("mycelium-health.sh", {**base, "hook_event_name": "SessionStart", "source": "startup"},
                 repo, plain).returncode == 0
    run = repo / ".mycelium" / "run" / "claude" / "s1"
    marker = (run / "active-session-log.tmp").read_bytes()

    dying = _dying_state_python(tmp_path, b"marker\0", keep_cut=False)
    edit = {**base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "a.py")}, "tool_response": {"success": True}}
    result = _hook("mycelium-activity-tracker.sh", edit, repo, dying)
    assert result.returncode == 0, result.stderr
    assert (run / "mycelium-session-activity.tmp").read_text() == "a.py\n"
    assert not (run / "mycelium-session.lock").exists()

    result = _hook("mycelium-stop-check.sh", {**base, "hook_event_name": "Stop"}, repo, dying)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block"
    assert (run / "active-session-log.tmp").read_bytes() == marker
    assert not (run / "mycelium-session.lock").exists()
    assert not (repo / ".mycelium" / "mycelium-stop.lock").exists()


def test_a_closed_output_pipe_still_releases_every_lock(tmp_path):
    """A host that stops reading before a hook writes must not leave locks.

    SessionStart and Stop write their JSON while holding both locks. A
    builtin write that died of SIGPIPE skipped the EXIT trap that releases
    them; the fast paths now fail like the interpreter they skip.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _base(repo)
    for name in ("decisions.md", "conventions.md"):
        (repo / ".living" / name).write_text(f"# {name}\n")
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    payloads = tmp_path / "payloads"
    payloads.mkdir()
    base = {"session_id": "s1", "cwd": str(repo)}
    (payloads / "start.json").write_text(json.dumps({**base, "hook_event_name": "SessionStart", "source": "startup"}))
    (payloads / "edit.json").write_text(json.dumps({
        **base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": str(repo / "a.py")}, "tool_response": {"success": True}}))
    (payloads / "stop.json").write_text(json.dumps({**base, "hook_event_name": "Stop"}))
    run = repo / ".mycelium" / "run" / "claude" / "s1"

    def closed(hook: str, payload: str) -> None:
        # The reader exits at once, so the hook's first write meets a
        # closed pipe.
        subprocess.run(
            ["bash", "-c", '"$1" < "$2" | true', "closed", str(HOOKS_DIR / hook), str(payloads / payload)],
            cwd=repo, env=env, timeout=60, check=False,
        )
        assert not (run / "mycelium-session.lock").exists(), hook
        assert not (repo / ".mycelium" / "mycelium-stop.lock").exists(), hook

    closed("mycelium-health.sh", "start.json")
    assert (run / "active-session-log.tmp").exists()
    subprocess.run([str(HOOKS_DIR / "mycelium-activity-tracker.sh")], cwd=repo, env=env,
                   input=(payloads / "edit.json").read_text(), text=True, check=True)
    closed("mycelium-stop-check.sh", "stop.json")
    # The Stop was blocked (its reason never reached anyone); state survives.
    assert (run / "active-session-log.tmp").exists()


def test_a_dead_marker_read_at_session_start_still_opens_the_transaction(tmp_path):
    """On a fresh root there is no marker to read, so a state process that
    dies at that read must not leave the task without a transaction (and so
    without Stop enforcement). With a marker present, nothing is touched."""
    repo = _git_repo(tmp_path)
    dying = _dying_state_python(tmp_path, b"marker\0", keep_cut=False)
    base = {"session_id": "s1", "cwd": str(repo)}
    start = {**base, "hook_event_name": "SessionStart", "source": "startup"}
    result = _hook("mycelium-health.sh", start, repo, dying)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    run = repo / ".mycelium" / "run" / "claude" / "s1"
    marker = (run / "active-session-log.tmp").read_bytes()
    assert (run / "active-session-owner-id.tmp").read_text() == "s1\n"
    assert not (run / "mycelium-session.lock").exists()
    assert not (repo / ".mycelium" / "mycelium-stop.lock").exists()

    # A resumed start whose marker read dies leaves the live transaction be.
    before = _snapshot(repo / ".mycelium")
    result = _hook("mycelium-health.sh", {**start, "source": "resume"}, repo, dying)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert _snapshot(repo / ".mycelium") == before
    assert (run / "active-session-log.tmp").read_bytes() == marker

    plain = os.environ.copy()
    plain["MYCELIUM_HOOK_HOST"] = "claude"
    plain["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    edit = {**base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "a.py")}, "tool_response": {"success": True}}
    assert _hook("mycelium-activity-tracker.sh", edit, repo, plain).returncode == 0
    result = _hook("mycelium-stop-check.sh", {**base, "hook_event_name": "Stop"}, repo, plain)
    assert json.loads(result.stdout)["decision"] == "block"


def test_a_very_deep_state_tree_still_lets_stop_block(tmp_path):
    """A 1,100-level directory tree under .mycelium is legal; the symlink
    scan must finish (as find does) so Stop still enforces."""
    repo = _git_repo(tmp_path)
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    base = {"session_id": "s1", "cwd": str(repo)}
    assert _hook("mycelium-health.sh", {**base, "hook_event_name": "SessionStart", "source": "startup"},
                 repo, env).returncode == 0
    edit = {**base, "hook_event_name": "PostToolUse", "tool_name": "Edit",
            "tool_input": {"file_path": str(repo / "a.py")}, "tool_response": {"success": True}}
    assert _hook("mycelium-activity-tracker.sh", edit, repo, env).returncode == 0
    fd = os.open(repo / ".mycelium", os.O_RDONLY)
    for _ in range(1100):
        os.mkdir("d", dir_fd=fd)
        child = os.open("d", os.O_RDONLY, dir_fd=fd)
        os.close(fd)
        fd = child
    os.close(fd)
    result = _hook("mycelium-stop-check.sh", {**base, "hook_event_name": "Stop"}, repo, env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block"
    subprocess.run(["rm", "-rf", str(repo / ".mycelium" / "d")], check=True)


def test_a_symlinked_or_swapped_scan_root_is_unsafe(tmp_path, monkeypatch):
    """find without -H/-L reports TOP itself when it is a symlink. A
    `.living` or `.mycelium` swapped for a symlink after preflight must not
    be walked through and called safe."""
    module = _state_module()
    real = tmp_path / "real"
    (real / "sub").mkdir(parents=True)
    link = tmp_path / "state"
    link.symlink_to(real)
    assert module.contains_symlink(str(real)) is False
    assert module.contains_symlink(str(link)) is True

    # Swapped between the lstat and the open: the opened directory is not
    # the one examined.
    real_lstat = os.lstat

    def stale_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        if str(path) == str(real):
            fake = list(info)
            fake[1] = info.st_ino + 1  # st_ino
            return os.stat_result(fake)
        return info

    monkeypatch.setattr(module.os, "lstat", stale_lstat)
    assert module.contains_symlink(str(real)) is True


def test_directories_churning_during_the_scan_are_skipped_not_unsafe(tmp_path):
    """Hooks create and remove lock directories constantly. Like fts, the
    scan skips a directory that changed after it was listed; reading that as
    "unsafe" made prepare fail, so a Stop could exit without enforcing."""
    module = _state_module()
    state = tmp_path / ".mycelium"
    session = state / "run" / "claude" / "s2"
    session.mkdir(parents=True)
    (state / "run" / "claude" / "s1").mkdir()
    churners = [
        subprocess.Popen(
            ["bash", "-c", 'while :; do mkdir "$1/$2" 2>/dev/null; mkdir "$1/$2/inner" 2>/dev/null; rmdir "$1/$2/inner" "$1/$2" 2>/dev/null; done',
             "churn", str(session), f"mycelium-session.lock{index % 2}"])
        for index in range(3)
    ]
    try:
        deadline = time.time() + 6
        scans = unsafe = 0
        while time.time() < deadline:
            scans += 1
            unsafe += module.contains_symlink(str(state))
    finally:
        for churner in churners:
            churner.kill()
            churner.wait()
    assert scans > 200
    assert unsafe == 0, f"{unsafe}/{scans} scans called a churning tree unsafe"
    # A real symlink is still found.
    (session / "evil").symlink_to(tmp_path)
    assert module.contains_symlink(str(state)) is True


@pytest.mark.parametrize("hook", ["mycelium-stop-check.sh", "mycelium-health.sh"])
def test_a_hook_killed_while_waiting_for_the_session_lock_releases_the_lifecycle_lock(tmp_path, hook):
    """SessionStart and Stop hold the lifecycle lock while they wait for a
    busy session lock; SIGTERM during that wait must still release it."""
    repo = _git_repo(tmp_path)
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    base = {"session_id": "s1", "cwd": str(repo)}
    start = {**base, "hook_event_name": "SessionStart", "source": "startup"}
    assert _hook("mycelium-health.sh", start, repo, env).returncode == 0
    run = repo / ".mycelium" / "run" / "claude" / "s1"
    holder = subprocess.Popen(["sleep", "60"])
    try:
        lock = run / "mycelium-session.lock"
        lock.mkdir()
        (lock / "owner").write_text(f"{holder.pid} {int(time.time())}\n")
        payload = {**base, "hook_event_name": "Stop"} if hook.endswith("stop-check.sh") else {**start, "source": "resume"}
        process = subprocess.Popen(
            [str(HOOKS_DIR / hook)], cwd=repo, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(payload).encode())
        process.stdin.close()
        stop_lock = repo / ".mycelium" / "mycelium-stop.lock"
        deadline = time.time() + 20
        while not stop_lock.exists() and time.time() < deadline:
            time.sleep(0.05)
        assert stop_lock.exists(), "the hook never took the lifecycle lock"
        time.sleep(0.5)
        assert process.poll() is None, "the hook did not wait for the session lock"
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
        assert not stop_lock.exists()
        assert lock.exists()  # the live holder's lock is untouched
    finally:
        holder.kill()
        holder.wait()



def test_running_out_of_descriptors_makes_the_scan_unsafe(tmp_path):
    """A walk that cannot open part of the tree has not shown it is free of
    symlinks; descriptor exhaustion must not read as an empty directory."""
    top = tmp_path / ".living"
    top.mkdir()
    fd = os.open(top, os.O_RDONLY)
    for _ in range(70):
        os.mkdir("d", dir_fd=fd)
        child = os.open("d", os.O_RDONLY, dir_fd=fd)
        os.close(fd)
        fd = child
    os.symlink("/tmp", "evil", dir_fd=fd)
    os.close(fd)
    probe = (
        "import importlib.util, os, resource, sys\n"
        "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "limit = int(sys.argv[3])\n"
        "if limit:\n"
        "    resource.setrlimit(resource.RLIMIT_NOFILE, (limit, resource.getrlimit(resource.RLIMIT_NOFILE)[1]))\n"
        "    held = []\n"
        "    while True:\n"
        "        try: held.append(os.open('/dev/null', os.O_RDONLY))\n"
        "        except OSError: break\n"
        "    for fd in held[:3]: os.close(fd)\n"
        "print(m.contains_symlink(sys.argv[2]))\n"
    )
    for limit in ("0", "64"):
        result = subprocess.run(
            [shutil.which("python3"), "-I", "-c", probe, str(HOOKS_DIR / "mycelium_hook_state.py"), str(top), limit],
            capture_output=True, text=True, check=True,
        )
        assert result.stdout == "True\n", (limit, result.stdout, result.stderr)


@pytest.mark.parametrize("hook", ["mycelium-stop-check.sh", "mycelium-health.sh"])
def test_an_inherited_lock_variable_is_never_released(tmp_path, hook):
    """A lock directory named in the environment belongs to someone else;
    a hook that fails before taking any lock must leave it alone."""
    repo = _git_repo(tmp_path)
    foreign = tmp_path / "foreign"
    for name in ("stop", "session"):
        (foreign / name).mkdir(parents=True)
        (foreign / name / "owner").write_text("1 1\n")
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    env["MYCELIUM_STATE_DIR"] = "/"  # fails preparation before any lock
    env["MYCELIUM_STOP_LOCK_DIR"] = str(foreign / "stop")
    env["MYCELIUM_SESSION_LOCK_DIR"] = str(foreign / "session")
    base = {"session_id": "s1", "cwd": str(repo)}
    payload = {**base, "hook_event_name": "Stop"} if hook.endswith("stop-check.sh") else {
        **base, "hook_event_name": "SessionStart", "source": "startup"}
    result = _hook(hook, payload, repo, env)
    assert result.returncode == 0, result.stderr
    for name in ("stop", "session"):
        assert (foreign / name / "owner").read_text() == "1 1\n"


def test_a_root_that_cannot_be_examined_is_unsafe_but_a_missing_one_is_not(tmp_path, monkeypatch):
    module = _state_module()
    top = tmp_path / ".mycelium"
    top.mkdir()
    real_lstat = os.lstat

    def failing(code):
        def lstat(path, *args, **kwargs):
            if str(path) == str(top):
                raise OSError(code, os.strerror(code), str(path))
            return real_lstat(path, *args, **kwargs)
        return lstat

    import errno
    for code, unsafe in ((errno.EIO, True), (errno.ESTALE, True), (errno.ENOENT, False), (errno.EACCES, False)):
        monkeypatch.setattr(module.os, "lstat", failing(code))
        assert module.contains_symlink(str(top)) is unsafe, errno.errorcode[code]


def test_the_scan_never_leaves_the_process_inside_the_tree(tmp_path):
    """If the original cwd cannot be re-entered, the scan still leaves the
    scanned tree (for /), whatever it answers."""
    top = tmp_path / ".living"
    (top / "a" / "b").mkdir(parents=True)
    start = tmp_path / "start"
    start.mkdir()
    probe = (
        "import importlib.util, os, sys\n"
        "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "os.chdir(sys.argv[3])\n"
        "real = os.fchdir\n"
        "def fchdir(fd):\n"
        "    if os.path.samefile(f'/dev/fd/{fd}', sys.argv[3]): raise PermissionError(13, 'denied')\n"
        "    return real(fd)\n"
        "m.os.fchdir = fchdir\n"
        "os.chmod(sys.argv[3], 0o000)\n"
        "try:\n"
        "    answer = m.contains_symlink(sys.argv[2])\n"
        "finally:\n"
        "    os.chmod(sys.argv[3], 0o755)\n"
        "print(answer, os.getcwd())\n"
    )
    result = subprocess.run(
        [shutil.which("python3"), "-I", "-c", probe, str(HOOKS_DIR / "mycelium_hook_state.py"), str(top), str(start)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    answer, cwd = result.stdout.split()
    assert answer == "False"
    assert not cwd.startswith(str(top.resolve()))


def test_releasing_a_lock_forgets_it_before_removing_it():
    """A hook killed between removing its lock and forgetting it would let
    its EXIT trap release a successor's lock at the same path."""
    script = r"""
source "$1"
mycelium_release_directory_lock() { printf 'stop=[%s] session=[%s]\n' "$MYCELIUM_STOP_LOCK_DIR" "$MYCELIUM_SESSION_LOCK_DIR"; }
MYCELIUM_STOP_LOCK_DIR=/x/stop.lock
mycelium_release_stop_lock
MYCELIUM_SESSION_LOCK_DIR=/x/session.lock
mycelium_release_session_lock
"""
    result = subprocess.run(["bash", "-c", script, "probe", str(LIB)], capture_output=True, text=True, check=True)
    assert result.stdout == "stop=[] session=[]\nstop=[] session=[]\n"


@pytest.mark.parametrize("where", [".mycelium/run/scratch/node_modules", ".living/log/link"])
def test_session_start_names_the_symlink_that_disabled_tracking(tmp_path, where):
    """Issue #145: a symlink in the state or living tree used to turn lifecycle
    tracking off with no output at all. Start now says which path did it."""
    repo = _git_repo(tmp_path)
    link = repo / where
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(repo / ".living" / "learnings.md")
    env = os.environ.copy()
    env.pop("MYCELIUM_STATE_DIR", None)
    env["MYCELIUM_HOOK_HOST"] = "claude"
    start = {"session_id": "s1", "cwd": str(repo), "hook_event_name": "SessionStart", "source": "startup"}
    result = _hook("mycelium-health.sh", start, repo, env)
    assert result.returncode == 0, result.stderr
    assert f"symlink found at {where}" in result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "lifecycle tracking is off" in context and where in context
    assert not (repo / ".mycelium" / "run" / "claude" / "s1" / "active-session-log.tmp").exists()
    # The diagnostic never changes the decision: removing the link restores startup.
    link.unlink()
    result = _hook("mycelium-health.sh", start, repo, env)
    assert result.returncode == 0 and "symlink" not in result.stderr
    assert (repo / ".mycelium" / "run" / "claude" / "s1" / "active-session-log.tmp").exists()
