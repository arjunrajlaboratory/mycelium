from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from session_file_changes import build_snapshot, collect_changes


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("initial\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def test_preexisting_dirty_and_untracked_files_are_not_session_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("dirty before session\n")
    (repo / "preexisting.txt").write_text("old untracked work\n")
    baseline = build_snapshot(repo)

    (repo / "new.txt").write_text("created during session\n")

    assert collect_changes(repo, baseline) == ["new.txt"]


def test_changes_to_preexisting_dirty_files_are_detected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    dirty = repo / "README.md"
    dirty.write_text("dirty before session\n")
    baseline = build_snapshot(repo)

    dirty.write_text("changed again during session with a different size\n")

    assert collect_changes(repo, baseline) == ["README.md"]


def test_deleting_a_preexisting_untracked_file_is_detected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    old = repo / "preexisting.txt"
    old.write_text("old untracked work\n")
    baseline = build_snapshot(repo)

    old.unlink()

    assert collect_changes(repo, baseline) == ["preexisting.txt"]


def test_committed_files_since_snapshot_are_detected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = build_snapshot(repo)
    session_start = int(time.time()) - 1

    (repo / "README.md").write_text("committed during session\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "session change")

    assert collect_changes(repo, baseline, start_ts=session_start) == ["README.md"]


def test_switching_to_preexisting_branch_does_not_report_history(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    original_branch = subprocess.check_output(
        ["git", "-C", str(repo), "branch", "--show-current"], text=True
    ).strip()
    _git(repo, "switch", "-q", "-c", "existing-feature")
    (repo / "feature.txt").write_text("pre-existing branch work\n")
    _git(repo, "add", "feature.txt")
    _git(repo, "commit", "-q", "-m", "pre-existing feature")
    _git(repo, "switch", "-q", original_branch)
    baseline = build_snapshot(repo)

    session_start = int(time.time()) + 1
    _git(repo, "switch", "-q", "existing-feature")

    assert collect_changes(repo, baseline, start_ts=session_start) == []


def test_amending_old_commit_reports_only_tree_delta(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "old.txt").write_text("pre-session content\n")
    _git(repo, "add", "old.txt")
    _git(repo, "commit", "-q", "-m", "pre-session commit")
    baseline = build_snapshot(repo)
    session_start = int(time.time()) - 1

    (repo / "new.txt").write_text("added during session\n")
    _git(repo, "add", "new.txt")
    _git(repo, "commit", "-q", "--amend", "--no-edit")

    assert collect_changes(repo, baseline, start_ts=session_start) == ["new.txt"]


def test_session_commit_restoring_baseline_content_is_detected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    config = repo / "config.txt"
    original_branch = subprocess.check_output(
        ["git", "-C", str(repo), "branch", "--show-current"], text=True
    ).strip()
    _git(repo, "switch", "-q", "-c", "existing-feature")
    config.write_text("feature value\n")
    _git(repo, "add", "config.txt")
    _git(repo, "commit", "-q", "-m", "pre-existing feature config")
    _git(repo, "switch", "-q", original_branch)
    config.write_text("baseline\n")
    _git(repo, "add", "config.txt")
    _git(repo, "commit", "-q", "-m", "baseline config")
    baseline = build_snapshot(repo)

    # Reflog position, not timestamp separation, defines the session boundary.
    session_start = int(time.time())
    _git(repo, "switch", "-q", "existing-feature")
    config.write_text("baseline\n")
    _git(repo, "add", "config.txt")
    _git(repo, "commit", "-q", "-m", "align config with baseline")

    assert collect_changes(repo, baseline, start_ts=session_start) == ["config.txt"]


def test_temporary_branch_commit_is_retained_after_returning_to_baseline(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    original_branch = subprocess.check_output(
        ["git", "-C", str(repo), "branch", "--show-current"], text=True
    ).strip()
    baseline = build_snapshot(repo)
    session_start = int(time.time()) - 1

    _git(repo, "switch", "-q", "-c", "temporary-work")
    (repo / "temporary.txt").write_text("committed during session\n")
    _git(repo, "add", "temporary.txt")
    _git(repo, "commit", "-q", "-m", "temporary session work")
    _git(repo, "switch", "-q", original_branch)

    assert collect_changes(repo, baseline, start_ts=session_start) == [
        "temporary.txt"
    ]


def test_rewritten_history_falls_back_when_head_reflog_is_disabled(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "core.logAllRefUpdates", "false")
    (tmp_path / "old.txt").write_text("pre-session content\n")
    _git(tmp_path, "add", "old.txt")
    _git(tmp_path, "commit", "-q", "-m", "pre-session commit")
    baseline = build_snapshot(tmp_path)
    session_start = int(time.time()) - 1

    assert baseline["head_reflog_entries"] is None
    (tmp_path / "new.txt").write_text("added during session\n")
    _git(tmp_path, "add", "new.txt")
    _git(tmp_path, "commit", "-q", "--amend", "--no-edit")

    assert collect_changes(tmp_path, baseline, start_ts=session_start) == ["new.txt"]


def test_activity_file_retains_deleted_paths_and_ignores_external_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = build_snapshot(repo)
    activity = repo / ".mycelium" / "mycelium-session-activity.tmp"
    activity.parent.mkdir()
    activity.write_text(f"{repo / 'disposable.tmp'}\n/tmp/outside.txt\n")

    assert collect_changes(repo, baseline, activity_file=activity) == ["disposable.tmp"]


def test_excluded_hook_owned_paths_are_omitted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = build_snapshot(repo)
    hook_log = repo / ".living" / "log" / "session.md"
    hook_log.parent.mkdir(parents=True)
    hook_log.write_text("hook owned\n")

    assert collect_changes(
        repo,
        baseline,
        excludes={".living/log/session.md"},
        exclude_prefixes=(".mycelium/", ".living/log/"),
    ) == []


def test_mycelium_runtime_state_is_always_omitted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline = build_snapshot(repo)
    state = repo / ".mycelium" / "active-session-log.tmp"
    state.parent.mkdir()
    state.write_text("runtime state\n")

    assert collect_changes(repo, baseline) == []
