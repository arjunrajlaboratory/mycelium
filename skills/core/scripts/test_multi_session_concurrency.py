"""Cross-host regressions for concurrent root lifecycle transactions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from test_codex_compatibility import _repo, _run_claude_hook, _run_hook


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


def test_late_identified_event_does_not_recreate_accepted_transaction(tmp_path):
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
    assert not (state / "active-session-log.tmp").exists()
    assert not (state / "mycelium-session-activity.tmp").exists()


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
    time.sleep(1.0)
    assert process.poll() is None, "PostToolUse bypassed the session event lock"

    # Model the accepted Stop critical section while PostToolUse is queued.
    (state / "active-session-log.tmp").unlink()
    (state / "active-session-owner-id.tmp").unlink()
    (lock / "owner").unlink()
    lock.rmdir()
    returncode = process.wait(timeout=5)

    assert returncode == 0
    assert not (state / "mycelium-session-activity.tmp").exists()


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
    assert not scoped_state.exists()
    assert shared.is_file()
    assert "Lifecycle status: accepted by Stop" in shared.read_text()


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
    assert not state.exists()


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
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(record_child, child_paths))

    assert all(result.returncode == 0 for result in results)
    assert all(result.stderr == "" for result in results), [
        result.stderr for result in results if result.stderr
    ]
    recorded = (parent_state / "mycelium-session-activity.tmp").read_text().splitlines()
    assert sorted(recorded) == child_paths
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
    assert all(not _run_state(repo, "codex", session_id).exists() for session_id in session_ids)
    shared_handoff = (repo / ".mycelium" / "last-session.md").read_text()
    assert shared_handoff.count("Lifecycle status: accepted by Stop") == 1
