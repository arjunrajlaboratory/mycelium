"""Tests for setup_workflow — the per-repository workflow-tool choice."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import io_observer  # noqa: E402
import setup_workflow as sw  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".living").mkdir()
    return tmp_path


def run(repo, *argv):
    return sw.main(["--target-dir", str(repo), *argv])


def choice(repo):
    return json.loads((repo / ".living" / "workflow.json").read_text())["workflow"]


def test_undecided_until_the_user_answers(repo, capsys):
    assert run(repo, "status") == sw.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert "Ask the user" in out
    assert not (repo / ".living" / "workflow.json").exists()


@pytest.mark.parametrize("tool", sw.TOOLS)
def test_choose_records_the_tool(repo, capsys, tool):
    assert run(repo, "choose", tool) == 0
    assert choice(repo) == {"tool": tool}
    capsys.readouterr()
    assert run(repo, "status") == 0
    assert f"workflow: {tool}" in capsys.readouterr().out


def test_exit_codes_are_the_documented_ones():
    # Skill text tells agents "exit 2 means undecided"; errors must differ.
    assert (sw.EXIT_UNDECIDED, sw.EXIT_ERROR) == (2, 3)


def test_living_that_is_a_file_is_not_a_repository(tmp_path):
    (tmp_path / ".living").write_text("")
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "status")
    assert exc.value.code == sw.EXIT_ERROR


def test_choice_file_that_is_a_directory_is_an_error(repo):
    (repo / ".living" / "workflow.json").mkdir()
    for argv in (("status",), ("choose", "dvc")):
        with pytest.raises(SystemExit) as exc:
            run(repo, *argv)
        assert exc.value.code == sw.EXIT_ERROR


def test_linked_living_folder_or_choice_file_is_refused(repo, tmp_path_factory):
    # A link could send the write into another project.
    other = tmp_path_factory.mktemp("other")
    (other / ".living").mkdir()
    linked = tmp_path_factory.mktemp("linked")
    (linked / ".living").symlink_to(other / ".living")
    target = other / "elsewhere.json"
    (repo / ".living" / "workflow.json").symlink_to(target)
    for root in (linked, repo):
        for argv in (("status",), ("choose", "dvc")):
            with pytest.raises(SystemExit) as exc:
                run(root, *argv)
            assert exc.value.code == sw.EXIT_ERROR
    assert not target.exists()
    assert not (other / ".living" / "workflow.json").exists()


def test_default_target_is_the_current_directory(repo, monkeypatch):
    monkeypatch.chdir(repo)
    assert sw.main(["choose", "dvc"]) == 0
    assert choice(repo) == {"tool": "dvc"}
    assert sw.main(["status"]) == 0


def test_a_folder_named_like_a_marker_is_not_a_tool_in_use(repo, capsys):
    (repo / "dvc.yaml").mkdir()
    run(repo, "status")
    assert "already has" not in capsys.readouterr().out


def test_none_is_a_decision(repo):
    run(repo, "choose", "none")
    assert run(repo, "status") == 0


def test_choice_can_change(repo):
    run(repo, "choose", "snakemake")
    run(repo, "choose", "dvc")
    assert choice(repo) == {"tool": "dvc"}


def test_unknown_tool_is_rejected(repo):
    with pytest.raises(SystemExit) as exc:
        run(repo, "choose", "nextflow")
    assert exc.value.code == sw.EXIT_ERROR  # never 2, which means "undecided"
    assert not (repo / ".living" / "workflow.json").exists()


def test_choose_needs_a_tool(repo):
    with pytest.raises(SystemExit) as exc:
        run(repo, "choose")
    assert exc.value.code == sw.EXIT_ERROR
    assert not (repo / ".living" / "workflow.json").exists()


def test_not_a_mycelium_repository(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "status")
    assert exc.value.code == sw.EXIT_ERROR


@pytest.mark.parametrize("text", ["not json", '{"workflow": {"tool": "make"}}', '{"workflow": []}', "[]"])
def test_malformed_file_is_reported_not_overwritten(repo, text):
    path = repo / ".living" / "workflow.json"
    path.write_text(text)
    for argv in (("status",), ("choose", "dvc")):
        with pytest.raises(SystemExit) as exc:
            run(repo, *argv)
        assert exc.value.code == sw.EXIT_ERROR
    assert path.read_text() == text


@pytest.mark.parametrize(
    "marker, tool",
    [("Snakefile", "snakemake"), ("workflow/Snakefile", "snakemake"),
     ("analysis/demo/workflow.smk", "snakemake"), ("analysis/demo/Snakefile", "snakemake"),
     ("dvc.yaml", "dvc"), ("dvc.lock", "dvc"), (".dvc/config", "dvc"),
     ("analysis/demo/dvc.yaml", "dvc")],
)
def test_undecided_status_names_a_tool_already_in_use(repo, capsys, marker, tool):
    path = repo / marker
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    assert run(repo, "status") == sw.EXIT_UNDECIDED
    assert f"already has {tool} files" in capsys.readouterr().out


def test_undecided_status_names_no_tool_in_a_plain_repository(repo, capsys):
    run(repo, "status")
    assert "already has" not in capsys.readouterr().out


def test_the_observer_does_not_walk_tool_state():
    # A DVC cache or Snakemake's state can hold many large files.
    assert {".dvc", ".snakemake"} <= io_observer.EXCLUDED_DIR_NAMES


def test_a_decided_repository_is_not_asked_again_even_with_other_tool_files(repo, capsys):
    (repo / "Snakefile").write_text("")
    run(repo, "choose", "none")
    capsys.readouterr()
    assert run(repo, "status") == 0
    assert capsys.readouterr().out.strip() == "workflow: none"


def test_missing_command_is_an_error_not_undecided(repo):
    with pytest.raises(SystemExit) as exc:
        run(repo)
    assert exc.value.code == sw.EXIT_ERROR


def test_new_file_is_readable_and_an_existing_file_keeps_its_mode(repo):
    path = repo / ".living" / "workflow.json"
    run(repo, "choose", "dvc")
    assert path.stat().st_mode & 0o777 == 0o644
    path.chmod(0o600)
    run(repo, "choose", "snakemake")
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_write_keeps_the_old_choice_and_leaves_no_temporary_file(repo, monkeypatch):
    run(repo, "choose", "dvc")

    def refuse(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sw.os, "replace", refuse)
    with pytest.raises(SystemExit) as exc:
        run(repo, "choose", "snakemake")
    assert exc.value.code == sw.EXIT_ERROR
    assert choice(repo) == {"tool": "dvc"}
    assert sorted(p.name for p in (repo / ".living").iterdir()) == ["workflow.json"]


def test_unwritable_folder_is_reported_without_a_traceback(repo, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(sw.tempfile, "mkstemp", refuse)
    with pytest.raises(SystemExit) as exc:
        run(repo, "choose", "dvc")
    assert exc.value.code == sw.EXIT_ERROR
