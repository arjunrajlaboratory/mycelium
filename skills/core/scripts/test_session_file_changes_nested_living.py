"""Stop accepts reflection recorded in a subfolder's own ``.living/``.

One repository can hold several projects, each with its own notes folder
(``<repo>/<project>/.living/``). A session started in such a project records
its reflection there, and Stop used to block it because only the root
``.living/`` counted. These tests pin the repaired contract: notes folders on
the path from the session's folder up to the repository root count, nothing
else does, and baselines written by earlier runtimes keep their meaning.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import session_file_changes as sfc  # noqa: E402

HELPER = Path(__file__).parent / "session_file_changes.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _notes(folder: Path) -> None:
    (folder / ".living").mkdir(parents=True, exist_ok=True)
    for name in ("learnings.md", "decisions.md", "conventions.md"):
        (folder / ".living" / name).write_text(f"# {name}\n")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _notes(repo)
    _notes(repo / "sub")
    _notes(repo / "other")
    (repo / "sub" / "deep" / "deeper").mkdir(parents=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture(autouse=True)
def _no_host_project_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    # The suite itself may run under a host that exports its own folder.
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)


def _baseline_from(repo: Path, folder: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.chdir(folder)
    return sfc.build_snapshot(repo.resolve())


def _append(path: Path) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n### a lesson\n")


# --- discovery ---------------------------------------------------------------


def test_session_folder_and_its_ancestors_are_discovered(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _notes(repo / "sub" / "deep")
    anchor = repo / "sub" / "deep" / "deeper"
    assert sfc.discover_nested_living_roots(repo, [str(anchor)]) == ["sub", "sub/deep"]


def test_root_session_discovers_no_subfolder(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert sfc.discover_nested_living_roots(repo, [str(repo)]) == []


def test_anchor_outside_the_repository_contributes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    outside = tmp_path / "elsewhere"
    _notes(outside)
    assert sfc.discover_nested_living_roots(repo, [str(outside)]) == []


def test_anchor_inside_a_nested_repository_contributes_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "sub" / ".git").write_text("gitdir: /nowhere\n")
    assert sfc.discover_nested_living_roots(repo, [str(repo / "sub" / "deep")]) == []


def test_symlinked_notes_folder_is_not_discovered(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    linked = repo / "linked"
    linked.mkdir()
    (linked / ".living").symlink_to(repo / "other" / ".living")
    assert sfc.discover_nested_living_roots(repo, [str(linked)]) == []


def test_symlinked_paths_resolve_on_both_sides(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    # Repository named through the link, anchor named directly, and vice versa.
    assert sfc.discover_nested_living_roots(alias, [str(repo / "sub")]) == ["sub"]
    assert sfc.discover_nested_living_roots(repo, [str(alias / "sub")]) == ["sub"]


def test_missing_anchor_is_ignored(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert sfc.discover_nested_living_roots(repo, [str(repo / "gone"), ""]) == []


def test_default_anchors_are_cwd_and_host_project_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert sfc.discover_nested_living_roots(repo) == []
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo / "sub"))
    assert sfc.discover_nested_living_roots(repo) == ["sub"]
    monkeypatch.chdir(repo / "other")
    assert sfc.discover_nested_living_roots(repo) == ["other", "sub"]


# --- baseline shape ----------------------------------------------------------


def test_root_mapping_stays_root_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    for baseline in (
        _baseline_from(repo, repo / "sub", monkeypatch),
        _living_snapshot(repo, repo / "sub", tmp_path, monkeypatch),
    ):
        # Earlier runtimes compare ``living_files`` exactly; subfolder entries
        # there would read as a change on every check after a rollback.
        assert baseline["living_files"]
        assert all(key.startswith(".living/") for key in baseline["living_files"])
        assert baseline["nested_living_roots"] == ["sub"]
        assert set(baseline["nested_living_files"]) == {
            "sub/.living/learnings.md",
            "sub/.living/decisions.md",
            "sub/.living/conventions.md",
        }


def _living_snapshot(
    repo: Path, folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict:
    monkeypatch.chdir(folder)
    output = tmp_path / "living-reminder-baseline.json"
    sfc.write_living_snapshot(repo.resolve(), output)
    return json.loads(output.read_text())


# --- comparison --------------------------------------------------------------


@pytest.mark.parametrize("taker", ["snapshot", "living-snapshot"])
def test_subfolder_notes_update_counts(
    taker: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    if taker == "snapshot":
        baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    else:
        baseline = _living_snapshot(repo, repo / "sub", tmp_path, monkeypatch)
    assert sfc.living_changed(repo.resolve(), baseline) is False
    (repo / "sub" / "work.py").write_text("print(1)\n")
    assert sfc.living_changed(repo.resolve(), baseline) is False
    _append(repo / "sub" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is True


def test_subfolder_findings_directory_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    target = repo / "sub" / ".living" / "findings" / "result.md"
    target.parent.mkdir(parents=True)
    target.write_text("new\n")
    assert sfc.living_changed(repo.resolve(), baseline) is True


def test_notes_folder_off_the_session_path_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    _append(repo / "other" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is False


def test_root_notes_still_count_for_a_subfolder_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    _append(repo / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is True


def test_check_uses_the_recorded_folders_not_the_current_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    # The session moved to another folder before Stop: the recorded folder is
    # still compared, and the new folder's absence is not read as a change.
    monkeypatch.chdir(repo / "other")
    assert sfc.living_changed(repo.resolve(), baseline) is False
    _append(repo / "other" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is False
    _append(repo / "sub" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is True


def test_baseline_from_an_earlier_runtime_keeps_root_only_meaning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    del baseline["nested_living_roots"]
    del baseline["nested_living_files"]
    _append(repo / "sub" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is False
    _append(repo / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is True


@pytest.mark.parametrize(
    "roots",
    [
        "sub",
        ["../outside"],
        ["/abs"],
        ["sub/"],
        ["sub//x"],
        ["./sub"],
        ["sub/../other"],
        [""],
        [None],
        [7],
        ["sub", "../x"],
    ],
)
def test_malformed_folder_list_falls_back_to_root_only(
    roots: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    baseline["nested_living_roots"] = roots
    _append(repo / "sub" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is False


@pytest.mark.parametrize("files", [None, [], "x", 3])
def test_malformed_folder_state_falls_back_to_root_only(
    files: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    baseline = _baseline_from(repo, repo / "sub", monkeypatch)
    baseline["nested_living_files"] = files
    _append(repo / "sub" / ".living" / "learnings.md")
    assert sfc.living_changed(repo.resolve(), baseline) is False


def test_cli_records_the_hook_folder(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    subprocess.run(
        [sys.executable, str(HELPER), "snapshot", "--repo-root", str(repo),
         "--output", str(baseline_path)],
        cwd=repo / "sub", env=env, check=True,
    )
    assert json.loads(baseline_path.read_text())["nested_living_roots"] == ["sub"]

    def living_changed_exit() -> int:
        return subprocess.run(
            [sys.executable, str(HELPER), "living-changed", "--repo-root", str(repo),
             "--baseline", str(baseline_path)],
            cwd=repo, env=env, check=False,
        ).returncode

    assert living_changed_exit() == 1
    _append(repo / "sub" / ".living" / "learnings.md")
    assert living_changed_exit() == 0


# --- explicit session folder (hosts whose dispatcher runs hooks from the root) ---


def test_reported_session_folder_is_an_anchor_from_the_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.chdir(repo)
    assert sfc.discover_nested_living_roots(repo) == []
    assert sfc.discover_nested_living_roots(repo, session_dirs=[str(repo / "sub")]) == ["sub"]


@pytest.mark.parametrize("command", ["snapshot", "living-snapshot"])
def test_cli_session_dir_records_the_reported_folder(command: str, tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    subprocess.run(
        [sys.executable, str(HELPER), command, "--repo-root", str(repo),
         "--session-dir", str(repo / "sub"), "--output", str(baseline_path)],
        cwd=repo, env=env, check=True,
    )
    assert json.loads(baseline_path.read_text())["nested_living_roots"] == ["sub"]


def test_unresolved_repository_path_still_reads_subfolder_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(repo)
    monkeypatch.chdir(repo / "sub")
    baseline = sfc.build_snapshot(alias)
    assert baseline["nested_living_roots"] == ["sub"]
    assert "sub/.living/learnings.md" in baseline["nested_living_files"]
    _append(repo / "sub" / ".living" / "learnings.md")
    assert sfc.living_changed(alias, baseline) is True
