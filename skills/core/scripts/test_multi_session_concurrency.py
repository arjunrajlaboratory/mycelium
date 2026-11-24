"""Cross-host regressions for concurrent root lifecycle transactions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

import pytest

from test_codex_compatibility import HOOKS_DIR, _repo, _run_claude_hook, _run_hook
from test_hook_state_port import _lock_wait_signal, _wait_for

pytestmark = pytest.mark.platform


def _non_exclusive_mkdir_path(tmp_path: Path) -> str:
    """Return a PATH whose `mkdir` reproduces uutils coreutils 0.10.0.

    Ubuntu 26.04 ships uutils as /usr/bin/mkdir. Without -p it pre-checks for
    an existing path, calls mkdir(2), and then treats any failure as success
    when the path is a directory afterwards, so a contender that loses the
    creation race still exits 0. The pre-check-to-create window is widened
    here so contenders started together reliably overlap in it.
    """
    real_mkdir = shutil.which("mkdir")
    assert real_mkdir
    shim_dir = tmp_path / "uutils-0.10-bin"
    shim_dir.mkdir()
    shim = shim_dir / "mkdir"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'real="{real_mkdir}"\n'
        'if [[ "$#" -ne 1 || "$1" == -* ]]; then exec "$real" "$@"; fi\n'
        'if [[ -e "$1" ]]; then echo "mkdir: $1: File exists" >&2; exit 1; fi\n'
        "sleep 0.5\n"
        '"$real" "$1" 2>/dev/null || [[ -d "$1" ]]\n'
    )
    shim.chmod(0o755)
    return f"{shim_dir}{os.pathsep}{os.environ['PATH']}"


# Hooks wait up to MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS polls (default 600, at
# least 50 ms each) for a session lock. Tests that run many hooks against one
# transaction raise it so a loaded runner does not drop events by design.
_PATIENT_SESSION_LOCK = {"MYCELIUM_SESSION_LOCK_MAX_ATTEMPTS": "6000"}
_UNRECORDED_DIAGNOSTIC = (
    "Mycelium: mycelium-activity-tracker.sh could not take the session lock; "
    "this tool event was not recorded; Stop will report it.\n"
)


def _run_state(repo: Path, host: str, session_id: str) -> Path:
    return repo / ".mycelium" / "run" / host / session_id


def _session_logs(repo: Path) -> list[Path]:
    return sorted(
        path
        for path in (repo / ".living" / "log").glob("*.md")
        if path.name != "LOG_REGISTRY.md"
    )


def test_distinct_concurrent_codex_roots_get_independent_transactions(tmp_path):
    """Safety must not exclude the second live root from lifecycle tracking."""
    repo = _repo(tmp_path)
    payloads = [
        {"cwd": str(repo), "source": "startup", "session_id": session_id}
        for session_id in ("codex-root-a", "codex-root-b")
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda payload: _run_hook("mycelium-health.sh", repo, payload),
                payloads,
            )
        )

    assert all(result.returncode == 0 for result in results)
    states = [
        _run_state(repo, "codex", session_id)
        for session_id in ("codex-root-a", "codex-root-b")
    ]
    assert all((state / "active-session-log.tmp").is_file() for state in states)
    assert all((state / "active-session-owner-id.tmp").is_file() for state in states)
    assert [
        (state / "active-session-owner-id.tmp").read_text().strip()
        for state in states
    ] == ["codex-root-a", "codex-root-b"]
    marker_paths = [
        Path((state / "active-session-log.tmp").read_text().splitlines()[0])
        for state in states
    ]
    assert len(set(marker_paths)) == 2
    assert len(_session_logs(repo)) == 2


def test_distinct_roots_get_distinct_logs_when_mkdir_is_not_exclusive(tmp_path):
    """Lifecycle serialization must not depend on the mkdir utility's exit status."""
    repo = _repo(tmp_path)
    extra_env = {"PATH": _non_exclusive_mkdir_path(tmp_path)}
    payloads = [
        {"cwd": str(repo), "source": "startup", "session_id": session_id}
        for session_id in ("codex-root-a", "codex-root-b")
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda payload: _run_hook(
                    "mycelium-health.sh", repo, payload, extra_env=extra_env
                ),
                payloads,
            )
        )

    assert all(result.returncode == 0 for result in results)
    marker_paths = [
        Path(
            (_run_state(repo, "codex", session_id) / "active-session-log.tmp")
            .read_text()
            .splitlines()[0]
        )
        for session_id in ("codex-root-a", "codex-root-b")
    ]
    assert len(set(marker_paths)) == 2
    assert len(_session_logs(repo)) == 2


@pytest.mark.parametrize("initial_owner", [
    "none",
    pytest.param("dead", marks=pytest.mark.skip(reason=(
        "Known bug, to be fixed in a separate PR: a reap race in "
        "mycelium_acquire_directory_lock (mycelium-hook-lib.sh, reap branch) can "
        "leave the lock directory ownerless with a fresh mtime, so both contenders "
        "wait out the 300 s staleness and exit 3 after 600 attempts. Only on "
        "filesystems where mkdir is not exclusive."))),
])
def test_directory_lock_excludes_contenders_when_mkdir_is_not_exclusive(
    tmp_path, initial_owner
):
    """Acquisition and dead-owner reap claims stay exclusive under uutils mkdir."""
    state = tmp_path / ".mycelium"
    state.mkdir()
    lock = state / "mycelium-stop.lock"
    if initial_owner == "dead":
        lock.mkdir()
        (lock / "owner").write_text(f"999999999 {int(time.time())}\n")
    events = tmp_path / "events.log"
    hook_lib = HOOKS_DIR / "mycelium-hook-lib.sh"
    command = (
        f'source "{hook_lib}"\n'
        f'mycelium_acquire_stop_lock "{state}" || exit 3\n'
        f'printf "enter %s\\n" "$1" >> "{events}"\n'
        "sleep 0.4\n"
        f'printf "exit %s\\n" "$1" >> "{events}"\n'
        "mycelium_release_stop_lock\n"
    )
    env = {**os.environ, "PATH": _non_exclusive_mkdir_path(tmp_path)}

    contenders = [
        subprocess.Popen(
            ["bash", "-c", command, "contender", name],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for name in ("a", "b")
    ]
    outcomes = [process.communicate(timeout=60) for process in contenders]

    assert [process.returncode for process in contenders] == [0, 0], outcomes
    lines = events.read_text().splitlines()
    assert len(lines) == 4
    # Each holder must leave before the other enters.
    first = lines[0].split()[1]
    second = "b" if first == "a" else "a"
    assert lines == [f"enter {first}", f"exit {first}", f"enter {second}", f"exit {second}"]
    assert not lock.exists()


def test_repeated_start_for_same_root_reuses_one_transaction(tmp_path):
    repo = _repo(tmp_path)
    payload = {"cwd": str(repo), "source": "startup", "session_id": "same-root"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _run_hook("mycelium-health.sh", repo, payload), range(2)))

    assert all(result.returncode == 0 for result in results)
    state = _run_state(repo, "codex", "same-root")
    assert (state / "active-session-log.tmp").is_file()
    assert len(_session_logs(repo)) == 1


def test_busy_lifecycle_lock_prevents_shared_start_preparation(tmp_path):
    repo = _repo(tmp_path)
    shared = repo / ".mycelium"
    shared.mkdir()
    pointer = shared / "plugin-root"
    pointer.write_text("frozen-old-root\n")
    lock = shared / "mycelium-stop.lock"
    lock.mkdir()
    (lock / "owner").write_text(f"{os.getpid()} {int(time.time())}\n")
    before = pointer.read_bytes()

    started = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "busy-root"},
        extra_env={
            "MYCELIUM_PLUGIN_ROOT": "/candidate/plugin/root",
            "MYCELIUM_STOP_LOCK_MAX_ATTEMPTS": "1",
        },
    )

    assert started.returncode == 0
    assert pointer.read_bytes() == before
    assert not (shared / "run").exists()
    assert _session_logs(repo) == []


def test_unsafe_late_shared_target_is_rejected_before_preparation_write(tmp_path):
    repo = _repo(tmp_path)
    shared = repo / ".mycelium"
    shared.mkdir()
    (shared / "plugin-root").mkdir()

    started = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "unsafe-root"},
        extra_env={"MYCELIUM_PLUGIN_ROOT": "/candidate/plugin/root"},
    )

    assert started.returncode == 0
    assert not (shared / ".gitignore").exists()
    assert not (shared / "run").exists()
    assert _session_logs(repo) == []


def test_identified_start_exposes_exact_private_handoff_path(tmp_path):
    repo = _repo(tmp_path)
    session_id = "handoff-guidance-root"
    state = _run_state(repo, "codex", session_id)

    started = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    )

    assert started.returncode == 0
    assert str(state / "last-session.md") in started.stdout


# Both supported schemas stay valid, so the guidance may only say a handoff
# that matches neither gets the fallback, never that these exact headings are
# the only ones accepted.
FALLBACK_RULE = (
    "Use these headings; a handoff that does not match a supported heading "
    "schema is replaced by a generic fallback at Stop."
)
# A section holding only a ### subheading counts as empty to the finalizer.
BODY_RULE = (
    "Each section needs at least one line of prose or a bullet besides its "
    "subheadings; write `- None.` when there is nothing to report."
)
STATE_RULE = "`## Current state` describes the project, not Stop status."


def _named_headings(instruction):
    import re

    # The heading list comes before the body rule, which may mention a heading.
    listed = instruction.split(BODY_RULE)[0]
    return re.findall(r"`(## [^`]+)`", listed)


def test_handoff_guidance_names_the_exact_required_headings(tmp_path):
    # An Antigravity App agent that never read the core skill followed the
    # injected "five-section handoff" instruction with its own numbered
    # headings; Stop then rejected the handoff and published the generic
    # fallback. Every instruction that asks for the handoff must name the
    # exact headings the finalizer accepts.
    from finalize_handoff import HANDOFF_SCHEMAS

    headings = HANDOFF_SCHEMAS[0]
    repo = _repo(tmp_path)
    session_id = "handoff-heading-guidance-root"
    _run_state(repo, "codex", session_id)

    started = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    )

    assert started.returncode == 0
    context = json.loads(started.stdout)["hookSpecificOutput"]["additionalContext"]
    handoff_line = next(
        line for line in context.splitlines() if "SESSION HANDOFF PATH" in line
    )
    assert _named_headings(handoff_line) == list(headings)
    assert FALLBACK_RULE in handoff_line
    assert BODY_RULE in handoff_line
    assert STATE_RULE in handoff_line

    template = (
        Path(__file__).resolve().parent.parent / "templates" / "MYCELIUM.md.template"
    ).read_text(encoding="utf-8")
    summary_rule = next(
        line for line in template.splitlines()
        if line.startswith("10. **Session summary**:")
    )
    assert _named_headings(summary_rule) == list(headings)
    assert FALLBACK_RULE in summary_rule
    assert BODY_RULE in summary_rule
    assert STATE_RULE in summary_rule

    acted = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": session_id,
            "tool_name": "Bash",
            "tool_input": {"command": "python analysis.py"},
        },
    )
    assert acted.returncode == 0, acted.stderr
    directive = json.loads(acted.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MYCELIUM POST-ACTION PROTOCOL" in directive
    summary_step = directive[directive.index("10. SESSION SUMMARY:"):]
    assert _named_headings(summary_step) == list(headings)
    assert FALLBACK_RULE in summary_step
    assert BODY_RULE in summary_step
    assert STATE_RULE in summary_step
    assert "### Locked decisions" in summary_step
    assert "### Dead ends — do not redo" in summary_step


def test_blocked_stop_names_exact_private_handoff_path(tmp_path):
    repo = _repo(tmp_path)
    session_id = "blocked-handoff-guidance-root"
    state = _run_state(repo, "codex", session_id)
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    ).returncode == 0
    (repo / "edit-only.txt").write_text("work\n")

    stopped = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "session_id": session_id},
    )

    assert stopped.returncode == 0
    reason = json.loads(stopped.stdout)["reason"]
    assert str(state / "last-session.md") in reason


def test_same_host_identity_is_namespaced_between_codex_and_claude(tmp_path):
    repo = _repo(tmp_path)
    payload = {"cwd": str(repo), "source": "startup", "session_id": "shared-id"}

    codex = _run_hook("mycelium-health.sh", repo, payload)
    claude = _run_claude_hook("mycelium-health.sh", repo, payload)

    assert codex.returncode == claude.returncode == 0
    assert (_run_state(repo, "codex", "shared-id") / "active-session-log.tmp").is_file()
    assert (_run_state(repo, "claude", "shared-id") / "active-session-log.tmp").is_file()
    assert len(_session_logs(repo)) == 2


@pytest.mark.parametrize(
    "session_id",
    [
        ".",
        "..",
        "../escape",
        "slash/name",
        "space name",
        "x" * 201,
        123,
        True,
        {"nested": "id"},
        ["list-id"],
    ],
)
def test_invalid_host_identity_cannot_create_a_transaction(tmp_path, session_id):
    repo = _repo(tmp_path)

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    )

    assert result.returncode == 0
    assert not (repo / ".mycelium").exists()
    assert _session_logs(repo) == []


def test_stop_consumes_only_its_scoped_transaction(tmp_path):
    repo = _repo(tmp_path)
    for session_id in ("stop-a", "stop-b"):
        start = _run_hook(
            "mycelium-health.sh",
            repo,
            {"cwd": str(repo), "source": "startup", "session_id": session_id},
        )
        assert start.returncode == 0

    stopped = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "session_id": "stop-a"},
    )

    assert stopped.returncode == 0
    assert not (_run_state(repo, "codex", "stop-a") / "active-session-log.tmp").exists()
    assert (_run_state(repo, "codex", "stop-b") / "active-session-log.tmp").is_file()


def _only_carried_baseline(state: Path) -> bool:
    """An accepted Stop leaves its run directory holding only the baseline
    the next turn of the same host session starts from."""
    return sorted(path.name for path in state.iterdir()) == [
        "next-turn-baseline.json", "next-turn-start-ts.tmp",
    ]


def test_work_after_accepted_stop_opens_a_fresh_transaction(tmp_path):
    """Hosts run Stop after every turn: later work in the same host session
    gets a new transaction, never the accepted one back."""
    repo = _repo(tmp_path)
    session_id = "completed-root"
    payload = {"cwd": str(repo), "source": "startup", "session_id": session_id}
    assert _run_hook("mycelium-health.sh", repo, payload).returncode == 0
    assert _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "session_id": session_id},
    ).returncode == 0
    state = _run_state(repo, "codex", session_id)

    late = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": session_id,
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Add File: late.py\n+x\n*** End Patch"},
            "tool_response": {"exit_code": 0},
        },
    )

    assert late.returncode == 0
    assert (state / "active-session-log.tmp").is_file()
    assert (state / "mycelium-session-activity.tmp").read_text() == "late.py\n"


def test_waiting_post_tool_revalidates_after_transaction_is_accepted(tmp_path):
    """Validation before a lock wait cannot authorize a late state write."""
    repo = _repo(tmp_path)
    session_id = "lock-race-root"
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    ).returncode == 0
    state = _run_state(repo, "codex", session_id)
    lock = state / "mycelium-session.lock"
    lock.mkdir()
    (lock / "owner").write_text(f"{os.getpid()} {int(time.time())}\n")
    payload = {
        "cwd": str(repo),
        "session_id": session_id,
        "tool_name": "apply_patch",
        "tool_input": {"command": "*** Begin Patch\n*** Add File: raced.py\n+x\n*** End Patch"},
        "tool_response": {"exit_code": 0},
    }
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "codex"
    waiting = _lock_wait_signal(tmp_path, env)
    hook = Path(__file__).resolve().parent.parent / "hooks" / "mycelium-activity-tracker.sh"
    process = subprocess.Popen(
        [str(hook)],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(payload))
    process.stdin.close()
    # Its validation before the lock has passed once it polls the held lock;
    # only then does modelling the accepted Stop test the revalidation.
    _wait_for(waiting, process, "the session-lock wait (PostToolUse bypassed the lock)")

    # Model the accepted Stop critical section while PostToolUse is queued.
    accepted_log = Path(
        (state / "active-session-log.tmp").read_text().splitlines()[0]
    )
    accepted_bytes = accepted_log.read_bytes()
    (state / "active-session-log.tmp").unlink()
    (state / "active-session-owner-id.tmp").unlink()
    (lock / "owner").unlink()
    lock.rmdir()
    returncode = process.wait(timeout=60)

    assert returncode == 0
    # The queued event revalidated after the lock and did not write into the
    # accepted transaction; its real edit opened a fresh one.
    assert accepted_log.read_bytes() == accepted_bytes
    fresh_log = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    assert fresh_log != accepted_log
    assert (state / "mycelium-session-activity.tmp").read_text() == "raced.py\n"


def test_accepted_scoped_handoff_is_published_to_shared_resume_pointer(tmp_path):
    repo = _repo(tmp_path)
    session_id = "handoff-root"
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    ).returncode == 0
    (repo / "work.txt").write_text("completed work\n")
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + "\nScoped handoff recorded.\n")

    stopped = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "session_id": session_id},
    )

    assert stopped.returncode == 0
    assert '"decision": "block"' not in stopped.stdout
    scoped_state = _run_state(repo, "codex", session_id)
    shared = repo / ".mycelium" / "last-session.md"
    assert _only_carried_baseline(scoped_state)
    assert shared.is_file()
    assert "Lifecycle status: accepted by Stop" in shared.read_text()


NUMBERED_HANDOFF = Path(__file__).with_name("testdata") / "antigravity-app-numbered-handoff.md"


def _scoped_work_with_handoff(tmp_path, session_id, handoff_text):
    repo = _repo(tmp_path)
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    ).returncode == 0
    (repo / "work.txt").write_text("completed work\n")
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + "\nScoped handoff recorded.\n")
    scoped_state = _run_state(repo, "codex", session_id)
    private = scoped_state / "last-session.md"
    private.write_text(handoff_text)
    future = time.time() + 2
    os.utime(private, (future, future))
    return repo, scoped_state, private


def _stop(repo, session_id, active):
    return _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": active, "session_id": session_id},
    )


def test_fresh_numbered_handoff_blocks_once_then_falls_back(tmp_path):
    # The real Antigravity App handoff from the ag-154-live-2 run: five
    # numbered sections under headings no schema accepts. Stop used to
    # replace it silently; it now says why once, and falls back on a retry
    # that still fails.
    session_id = "numbered-handoff-root"
    authored = NUMBERED_HANDOFF.read_text(encoding="utf-8")
    repo, scoped_state, private = _scoped_work_with_handoff(
        tmp_path, session_id, authored
    )
    shared = repo / ".mycelium" / "last-session.md"
    registry = repo / ".living" / "log" / "LOG_REGISTRY.md"
    registry_before = registry.read_bytes()

    first = _stop(repo, session_id, False)

    assert first.returncode == 0, first.stderr
    reason = json.loads(first.stdout)["reason"]
    assert "\n" not in reason
    assert str(private) in reason
    assert (
        "missing `## What was worked on`, `## Key decisions made`, "
        "`## Blockers & surprises`, `## Current state`, `## Next steps`"
    ) in reason
    assert "`- None.`" in reason
    assert "second" in reason
    assert private.read_text(encoding="utf-8") == authored
    # A blocked attempt writes no registry row, so it cannot read as complete.
    assert registry.read_bytes() == registry_before
    assert not shared.exists() or "1. Accomplished" not in shared.read_text()

    second = _stop(repo, session_id, True)

    assert second.returncode == 0, second.stderr
    assert '"decision": "block"' not in second.stdout
    published = shared.read_text()
    assert "Lifecycle status: accepted by Stop" in published
    assert "Completed the session work recorded in the finalized session log." in published
    assert _only_carried_baseline(scoped_state)


def test_handoff_repaired_after_the_block_is_preserved(tmp_path):
    session_id = "repaired-handoff-root"
    repo, scoped_state, private = _scoped_work_with_handoff(
        tmp_path, session_id, NUMBERED_HANDOFF.read_text(encoding="utf-8")
    )
    assert json.loads(_stop(repo, session_id, False).stdout)["decision"] == "block"

    private.write_text(
        "## What was worked on\n- Mean-ratio analysis.\n\n"
        "## Key decisions made\n- None.\n\n### Locked decisions\n\n"
        "## Blockers & surprises\n- None.\n\n"
        "## Current state\n- Threshold 1.3; outputs current.\n\n"
        "## Next steps\n- Review the lineage gaps.\n"
    )
    future = time.time() + 2
    os.utime(private, (future, future))
    second = _stop(repo, session_id, True)

    assert second.returncode == 0, second.stderr
    assert '"decision": "block"' not in second.stdout
    published = (repo / ".mycelium" / "last-session.md").read_text()
    assert "- Threshold 1.3; outputs current." in published
    assert "Completed the session work recorded" not in published
    assert _only_carried_baseline(scoped_state)


def test_handoff_block_is_not_inherited_by_a_later_transaction(tmp_path):
    session_id = "later-transaction-root"
    repo, scoped_state, private = _scoped_work_with_handoff(
        tmp_path, session_id, NUMBERED_HANDOFF.read_text(encoding="utf-8")
    )
    assert json.loads(_stop(repo, session_id, False).stdout)["decision"] == "block"
    assert '"decision": "block"' not in _stop(repo, session_id, True).stdout

    # The next turn of the same host session is a new transaction: its own
    # bad handoff gets its own one-time explanation.
    (repo / "more-work.txt").write_text("more\n")
    # The tracker records the edit as the next turn's work.
    assert _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": session_id,
            "tool_name": "Write",
            "tool_input": {"file_path": str(repo / "more-work.txt")},
        },
    ).returncode == 0
    time.sleep(1.1)
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + "\nSecond turn recorded.\n")
    private.write_text(NUMBERED_HANDOFF.read_text(encoding="utf-8"))
    future = time.time() + 4
    os.utime(private, (future, future))

    third = _stop(repo, session_id, False)

    assert third.returncode == 0, third.stderr
    assert "would be replaced by a generic fallback" in json.loads(third.stdout)["reason"]


def test_a_directory_at_the_handoff_marker_path_never_fails_stop(tmp_path):
    session_id = "marker-directory-root"
    repo, scoped_state, private = _scoped_work_with_handoff(
        tmp_path, session_id, NUMBERED_HANDOFF.read_text(encoding="utf-8")
    )
    (scoped_state / "handoff-check-blocked.tmp").mkdir()

    stopped = _stop(repo, session_id, False)

    # Fails open: no block it cannot record, and the accepted cleanup's
    # removal of the marker path is not fatal.
    assert stopped.returncode == 0, stopped.stderr
    assert '"decision": "block"' not in stopped.stdout
    assert "Completed the session work recorded" in (
        repo / ".mycelium" / "last-session.md"
    ).read_text()


def test_matching_preupgrade_flat_transaction_completes_without_migration(tmp_path):
    repo = _repo(tmp_path)
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup"},
    ).returncode == 0
    state = repo / ".mycelium"
    marker = state / "active-session-log.tmp"
    marker.write_text(marker.read_text() + "owner-id-v1\n")
    (state / "active-session-owner-id.tmp").write_text("preupgrade-root\n")

    stopped = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": "preupgrade-root",
        },
    )

    assert stopped.returncode == 0
    assert not marker.exists()
    assert not (state / "run" / "codex" / "preupgrade-root").exists()


def test_nonmatching_new_root_does_not_touch_preupgrade_flat_owner(tmp_path):
    repo = _repo(tmp_path)
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup"},
    ).returncode == 0
    shared = repo / ".mycelium"
    marker = shared / "active-session-log.tmp"
    marker.write_text(marker.read_text() + "owner-id-v1\n")
    owner = shared / "active-session-owner-id.tmp"
    owner.write_text("preupgrade-root\n")
    before = (marker.read_bytes(), owner.read_bytes())

    started = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "new-root"},
    )

    assert started.returncode == 0
    assert (marker.read_bytes(), owner.read_bytes()) == before
    assert (_run_state(repo, "codex", "new-root") / "active-session-log.tmp").is_file()


def test_concurrent_stops_finalize_one_scoped_transaction_exactly_once(tmp_path):
    repo = _repo(tmp_path)
    session_id = "stop-race-root"
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    ).returncode == 0
    state = _run_state(repo, "codex", session_id)
    log_path = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    log_session_id = log_path.name[:14]
    (repo / "race-work.txt").write_text("work\n")
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + "\nRace documented.\n")
    payloads = [
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": session_id,
        }
        for _ in range(8)
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda payload: _run_hook("mycelium-stop-check.sh", repo, payload),
                payloads,
            )
        )

    assert all(result.returncode == 0 for result in results)
    assert log_path.read_text().count("Session ended") == 1
    registry = (repo / ".living" / "log" / "LOG_REGISTRY.md").read_text()
    assert registry.count(f"| {log_session_id} |") == 1
    assert _only_carried_baseline(state)


def test_blocked_stop_preserves_its_state_and_live_sibling(tmp_path):
    repo = _repo(tmp_path)
    for session_id in ("blocked-root", "sibling-root"):
        assert _run_hook(
            "mycelium-health.sh",
            repo,
            {"cwd": str(repo), "source": "startup", "session_id": session_id},
        ).returncode == 0
    (repo / "undocumented.txt").write_text("work\n")

    blocked = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": "blocked-root",
        },
    )

    assert json.loads(blocked.stdout)["decision"] == "block"
    assert (_run_state(repo, "codex", "blocked-root") / "active-session-log.tmp").is_file()
    assert (_run_state(repo, "codex", "sibling-root") / "active-session-log.tmp").is_file()


def test_linked_session_directory_is_rejected_without_outside_writes(tmp_path):
    repo = _repo(tmp_path)
    shared = repo / ".mycelium"
    host_root = shared / "run" / "codex"
    host_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (host_root / "linked-root").symlink_to(outside, target_is_directory=True)

    started = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "linked-root"},
    )

    assert started.returncode == 0
    assert list(outside.iterdir()) == []
    assert _session_logs(repo) == []


def test_parent_identity_collects_subagent_activity_without_a_new_transaction(tmp_path):
    repo = _repo(tmp_path)
    parent_id = "parent-root"
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": parent_id},
    ).returncode == 0
    parent_state = _run_state(repo, "codex", parent_id)

    child_event = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": parent_id,
            "agent_id": "child-1",
            "agent_type": "worker",
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: child.py\n+work\n*** End Patch"
            },
            "tool_response": {"exit_code": 0},
        },
    )
    unrelated_stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": "child-1",
        },
    )

    assert child_event.returncode == unrelated_stop.returncode == 0
    assert (parent_state / "mycelium-session-activity.tmp").read_text().strip() == "child.py"
    assert (parent_state / "active-session-log.tmp").is_file()
    assert not _run_state(repo, "codex", "child-1").exists()


def test_parallel_subagent_events_are_serialized_into_parent_transaction(tmp_path):
    """Parallel events for one transaction are serialized, never lost.

    Each event either lands in the parent's activity file exactly once or,
    when its hook could not take the session lock, is accounted for: a
    stderr diagnostic plus one line in the transaction's unrecorded-events
    file, which Stop reports. A silent loss (a lost update in the activity
    file, or a dropped event with no evidence) fails. The session-lock wait
    is raised so a loaded runner rarely drops an event at all.
    """
    repo = _repo(tmp_path)
    parent_id = "parallel-parent-root"
    assert _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": parent_id},
    ).returncode == 0
    parent_state = _run_state(repo, "codex", parent_id)
    child_paths = [f"child-{index:02d}.py" for index in range(24)]

    def record_child(path: str) -> subprocess.CompletedProcess:
        return _run_hook(
            "mycelium-activity-tracker.sh",
            repo,
            {
                "cwd": str(repo),
                "session_id": parent_id,
                "agent_id": f"agent-{path}",
                "agent_type": "worker",
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": (
                        "*** Begin Patch\n"
                        f"*** Add File: {path}\n"
                        "+work\n"
                        "*** End Patch"
                    )
                },
                "tool_response": {"exit_code": 0},
            },
            extra_env=_PATIENT_SESSION_LOCK,
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(record_child, child_paths))

    assert all(result.returncode == 0 for result in results)
    dropped = {
        path
        for path, result in zip(child_paths, results)
        if result.stderr == _UNRECORDED_DIAGNOSTIC
    }
    assert all(
        result.stderr in ("", _UNRECORDED_DIAGNOSTIC) for result in results
    ), [result.stderr for result in results if result.stderr]
    recorded = (parent_state / "mycelium-session-activity.tmp").read_text().splitlines()
    assert len(recorded) == len(set(recorded)), recorded
    assert sorted(recorded) == sorted(set(child_paths) - dropped)
    # With the wait raised to minutes, a drop is a freak event: accept at most
    # one, so a hook that stops waiting (19 of 24 dropped at 2 attempts) fails.
    assert len(dropped) <= 1, sorted(dropped)
    unrecorded = parent_state / "mycelium-unrecorded-events.tmp"
    lines = (
        [json.loads(line) for line in unrecorded.read_text().splitlines() if line]
        if unrecorded.exists()
        else []
    )
    assert len(lines) == len(dropped), (lines, sorted(dropped))
    assert all(
        line["kind"] == "work" and line["tool_name"] == "apply_patch"
        for line in lines
    ), lines
    assert len(_session_logs(repo)) == 1


def test_two_concurrent_accepted_roots_keep_both_registry_rows(tmp_path):
    repo = _repo(tmp_path)
    session_ids = ("accepted-a", "accepted-b")
    log_ids: list[str] = []
    for session_id in session_ids:
        assert _run_hook(
            "mycelium-health.sh",
            repo,
            {"cwd": str(repo), "source": "startup", "session_id": session_id},
        ).returncode == 0
        marker = _run_state(repo, "codex", session_id) / "active-session-log.tmp"
        log_ids.append(Path(marker.read_text().splitlines()[0]).name[:14])
    (repo / "accepted-a.txt").write_text("a\n")
    (repo / "accepted-b.txt").write_text("b\n")
    decisions = repo / ".living" / "decisions.md"
    decisions.write_text(decisions.read_text() + "\nConcurrent roots accepted.\n")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda session_id: _run_hook(
                    "mycelium-stop-check.sh",
                    repo,
                    {
                        "cwd": str(repo),
                        "stop_hook_active": False,
                        "session_id": session_id,
                    },
                ),
                session_ids,
            )
        )

    assert all(result.returncode == 0 for result in results)
    assert all('"decision": "block"' not in result.stdout for result in results)
    registry = (repo / ".living" / "log" / "LOG_REGISTRY.md").read_text()
    assert all(registry.count(f"| {log_id} |") == 1 for log_id in log_ids)
    assert all(
        _only_carried_baseline(_run_state(repo, "codex", session_id))
        for session_id in session_ids
    )
    shared_handoff = (repo / ".mycelium" / "last-session.md").read_text()
    assert shared_handoff.count("Lifecycle status: accepted by Stop") == 1
