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
dump() {
  local name
  printf 'rc=%s\n' "$1"
  for name in VARIABLES; do
    if [[ -n "${!name+set}" ]]; then
      printf '%s=%q\n' "$name" "${!name}"
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
    marker) out=$(mycelium_read_active_log_marker "$REPO" "$1" 2>/dev/null); rc=$?; printf 'out=%q\n' "$out"; shift ;;
    owner) out=$(mycelium_read_session_owner_id "$1" 2>/dev/null); rc=$?; printf 'out=%q\n' "$out"; shift ;;
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
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(current) / name
            info = path.lstat()
            relative = str(path.relative_to(root))
            if stat.S_ISLNK(info.st_mode):
                rows.append((relative, "link", os.readlink(path)))
            elif stat.S_ISDIR(info.st_mode):
                rows.append((relative, "dir", oct(info.st_mode & 0o777)))
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


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink()
        return
    path.chmod(0o755)
    for child in path.iterdir():
        _remove(child)
    path.rmdir()


def _execute(tmp_path: Path, library: Path, build: Builder, env_extra: dict[str, str], steps: list[str]) -> tuple[str, list[tuple[str, ...]]]:
    work = tmp_path / "work"
    if work.exists():
        _remove(work)
    work.mkdir()
    repo = work / "repo"
    repo.mkdir()
    build(repo)
    env = os.environ.copy()
    env.pop("MYCELIUM_STATE_DIR", None)
    env.pop("MYCELIUM_PLUGIN_ROOT", None)
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


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_state_runner_matches_reference_helpers(tmp_path, name):
    build, env_extra, steps = SCENARIOS[name]
    reference = _execute(tmp_path, REFERENCE, build, env_extra, steps)
    live = _execute(tmp_path, LIB, build, env_extra, steps)
    assert live[0] == reference[0]
    assert live[1] == reference[1]


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
    The event must take the lock, find no transaction, release, and write
    nothing.
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
    assert not (run / "mycelium-session-activity.tmp").exists()
    assert not (run / "mycelium-reminded.tmp").exists()
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


def test_session_start_releases_both_locks_when_the_state_process_dies(tmp_path):
    """A state process that dies after taking both locks leaves neither."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    _base(repo)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real = shutil.which("python3")
    shim = bin_dir / "python3"
    # Run the real interpreter; for the state module, cut its report right
    # after the session lock record, as if it had been killed there.
    shim.write_text(
        f"#!{real}\n"
        "import subprocess, sys\n"
        f"argv = [{real!r}] + sys.argv[1:]\n"
        "if not any(a.endswith('mycelium_hook_state.py') for a in sys.argv[1:]):\n"
        "    raise SystemExit(subprocess.call(argv))\n"
        "out = subprocess.run(argv, stdout=subprocess.PIPE).stdout\n"
        "cut = out.find(b'lock\\0session\\0')\n"
        "if cut >= 0:\n"
        "    end = out.index(b'\\0', cut + len(b'lock\\0session\\0')) + 1\n"
        "    out = out[:end]\n"
        "sys.stdout.buffer.write(out)\n"
    )
    shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["MYCELIUM_HOOK_HOST"] = "claude"
    env["MYCELIUM_KNOWLEDGE_DIR"] = str(tmp_path / "knowledge")
    start = {"session_id": "s1", "cwd": str(repo), "hook_event_name": "SessionStart", "source": "startup"}
    result = subprocess.run(
        [str(HOOKS_DIR / "mycelium-health.sh")],
        cwd=repo, input=json.dumps(start), text=True, capture_output=True, env=env, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    run = repo / ".mycelium" / "run" / "claude" / "s1"
    assert run.is_dir()
    assert not (run / "mycelium-session.lock").exists()
    assert not (repo / ".mycelium" / "mycelium-stop.lock").exists()
    assert not (run / "active-session-log.tmp").exists()
