"""Tests for setup_linters — the per-repository linter choice."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import setup_linters as sl  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".living").mkdir()
    return tmp_path


def run(repo, *argv):
    return sl.main(["--target-dir", str(repo), *argv])


def choice(repo):
    return json.loads((repo / ".living" / "linters.json").read_text())["linters"]


def test_undecided_until_the_user_answers(repo, capsys):
    assert run(repo, "status") == sl.EXIT_UNDECIDED
    assert "Ask the user" in capsys.readouterr().out


def test_enable_without_names_enables_all(repo, capsys):
    run(repo, "enable")
    assert choice(repo) == {"scilintr": True, "scitexlintr": True, "scilintr-r": True}
    capsys.readouterr()
    assert run(repo, "status") == 0
    assert "scitexlintr: enabled" in capsys.readouterr().out


def test_disable_without_names_disables_all(repo):
    run(repo, "disable")
    assert set(choice(repo).values()) == {False}
    assert run(repo, "status") == 0


def test_named_change_keeps_the_rest(repo):
    run(repo, "enable")
    run(repo, "disable", "scilintr-r")
    assert choice(repo) == {"scilintr": True, "scitexlintr": True, "scilintr-r": False}


def test_unknown_name_is_rejected(repo):
    with pytest.raises(SystemExit) as exc:
        run(repo, "enable", "flake8")
    assert exc.value.code == sl.EXIT_ERROR
    assert not (repo / ".living" / "linters.json").exists()


def test_not_a_mycelium_repository(tmp_path):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, "status")
    assert exc.value.code == sl.EXIT_ERROR


def test_malformed_file_is_reported_not_overwritten(repo):
    path = repo / ".living" / "linters.json"
    path.write_text("not json")
    with pytest.raises(SystemExit) as exc:
        run(repo, "enable")
    assert exc.value.code == sl.EXIT_ERROR
    assert path.read_text() == "not json"
