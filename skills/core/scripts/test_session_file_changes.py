from __future__ import annotations

import subprocess
import sys
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

    (repo / "README.md").write_text("committed during session\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "session change")

    assert collect_changes(repo, baseline) == ["README.md"]


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
