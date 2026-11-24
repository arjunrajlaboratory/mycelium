"""Tests for setup_linters — the per-repository linter choice and install."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_linter_versions as clv  # noqa: E402
import setup_linters as sl  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".living").mkdir()
    return tmp_path


@pytest.fixture
def installed(monkeypatch):
    """Control which linters look installed, without touching the machine."""
    state: dict[str, str | None] = {"scilintr": None, "scitexlintr": None, "scilintr-r": None}

    def fake_check(spec, offline, timeout):
        name, minimum = clv.parse_spec(spec)
        version = state[name]
        result = clv.Result(name, minimum, version is not None, version, None)
        if version is None:
            result.problem = "not installed"
        elif minimum and clv.release_order(version) < (minimum, 1):
            result.problem = f"{version} is below the required {clv._fmt(minimum)}"
        return result

    monkeypatch.setattr(clv, "check", fake_check)
    monkeypatch.setattr(sl, "r_package_version", lambda: state["scilintr-r"])
    monkeypatch.setattr(sl, "rscript_available", lambda: False)
    return state


@pytest.fixture
def commands(monkeypatch, installed):
    """Record install commands; a successful command marks the package installed."""
    calls: list[list[str]] = []
    fail: set[str] = set()

    def fake_run(cmd):
        calls.append(cmd)
        tool = Path(cmd[0]).name
        if tool in fail:
            return 1
        name = cmd[-1]
        if "install.packages" in name:
            installed["scilintr-r"] = "0.3.0"
        else:
            installed[name] = "9.9.9"
        return 0

    monkeypatch.setattr(sl, "run_command", fake_run)
    monkeypatch.setattr(sl.shutil, "which", lambda name: f"/usr/bin/{name}" if name in {"pipx", "uv", "Rscript"} else None)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    return calls, fail


def main(repo, *argv):
    return sl.main(["--target-dir", str(repo), *argv])


def config(repo):
    return json.loads((repo / ".living" / "linters.json").read_text())


def test_undecided_status_asks_the_user(repo, installed, capsys):
    assert main(repo, "status") == sl.EXIT_UNDECIDED
    out = capsys.readouterr().out
    assert "undecided" in out
    assert "Ask the user" in out
    assert not (repo / ".living" / "linters.json").exists()


def test_status_json_reports_undecided(repo, installed, capsys):
    main(repo, "status", "--json")
    data = json.loads(capsys.readouterr().out)
    assert data["decided"] is False
    assert data["recommended"] == ["scilintr", "scitexlintr"]


def test_enable_without_names_records_the_recommended_set(repo, installed):
    assert main(repo, "enable") == 0
    data = config(repo)
    assert data["linters"] == {"scilintr": True, "scitexlintr": True, "scilintr-r": False}
    assert data["schema"] == 1


def test_recommended_set_includes_r_when_rscript_is_available(repo, installed, monkeypatch):
    monkeypatch.setattr(sl, "rscript_available", lambda: True)
    main(repo, "enable")
    assert config(repo)["linters"]["scilintr-r"] is True


def test_disable_without_names_opts_out_of_everything(repo, installed, capsys):
    assert main(repo, "disable") == 0
    assert set(config(repo)["linters"].values()) == {False}
    assert main(repo, "status") == 0
    assert "disabled" in capsys.readouterr().out


def test_enable_named_keeps_other_choices(repo, installed):
    main(repo, "disable")
    main(repo, "enable", "scitexlintr")
    assert config(repo)["linters"] == {"scilintr": False, "scitexlintr": True, "scilintr-r": False}


def test_unknown_linter_name_is_rejected(repo, installed):
    with pytest.raises(SystemExit):
        main(repo, "enable", "flake8")
    assert not (repo / ".living" / "linters.json").exists()


def test_enabled_but_missing_status_says_install(repo, installed, capsys):
    main(repo, "enable")
    capsys.readouterr()
    assert main(repo, "status") == sl.EXIT_MISSING
    out = capsys.readouterr().out
    assert "setup_linters.py" in out and "install" in out


def test_enabled_and_installed_status_is_ok(repo, installed, capsys):
    installed.update(scilintr="0.1.2", scitexlintr="0.2.1")
    main(repo, "enable")
    assert main(repo, "status") == 0


def test_install_prefers_pipx_outside_a_virtualenv(repo, commands, installed):
    calls, _ = commands
    main(repo, "enable")
    assert main(repo, "install") == 0
    assert calls == [["/usr/bin/pipx", "install", "scilintr"], ["/usr/bin/pipx", "install", "scitexlintr"]]


def test_install_falls_back_when_an_installer_fails(repo, commands):
    calls, fail = commands
    fail.add("pipx")
    main(repo, "enable", "scilintr")
    assert main(repo, "install") == 0
    assert [c[0] for c in calls] == ["/usr/bin/pipx", "/usr/bin/uv"]
    assert calls[1] == ["/usr/bin/uv", "tool", "install", "scilintr"]


def test_install_uses_the_active_virtualenv(repo, commands, monkeypatch, tmp_path):
    calls, _ = commands
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("")
    monkeypatch.setenv("VIRTUAL_ENV", str(venv))
    main(repo, "enable", "scilintr")
    main(repo, "install")
    assert calls == [[str(python), "-m", "pip", "install", "scilintr"]]


def test_install_skips_disabled_and_already_installed(repo, commands, installed):
    calls, _ = commands
    installed["scilintr"] = "0.1.2"
    main(repo, "disable")
    main(repo, "enable", "scilintr", "scitexlintr")
    assert main(repo, "install") == 0
    assert calls == [["/usr/bin/pipx", "install", "scitexlintr"]]


def test_install_never_upgrades_an_old_version(repo, commands, installed, capsys):
    calls, _ = commands
    installed.update(scilintr="0.1.2", scitexlintr="0.1.0")
    main(repo, "enable")
    assert main(repo, "install") == sl.EXIT_MISSING
    assert calls == []
    assert "Ask the user" in capsys.readouterr().out


def test_install_r_package_with_rscript(repo, commands):
    calls, _ = commands
    main(repo, "enable", "scilintr-r")
    assert main(repo, "install") == 0
    assert calls[0][:2] == ["/usr/bin/Rscript", "-e"]
    assert 'install.packages("scilintr"' in calls[0][2]


def test_install_reports_failure_when_every_installer_fails(repo, commands, capsys):
    calls, fail = commands
    fail.update({"pipx", "uv", Path(sys.executable).name})
    main(repo, "enable", "scilintr")
    assert main(repo, "install") == sl.EXIT_MISSING
    assert "could not install scilintr" in capsys.readouterr().out


def test_install_before_a_decision_refuses(repo, commands, capsys):
    calls, _ = commands
    assert main(repo, "install") == sl.EXIT_UNDECIDED
    assert calls == []


def test_dry_run_prints_without_running(repo, commands, capsys):
    calls, _ = commands
    main(repo, "enable")
    assert main(repo, "install", "--dry-run") == 0
    assert calls == []
    assert "pipx install scilintr" in capsys.readouterr().out


def test_requires_a_mycelium_repository(tmp_path, installed):
    with pytest.raises(SystemExit):
        main(tmp_path, "status")


def test_symlinked_config_is_refused(repo, installed, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (repo / ".living" / "linters.json").symlink_to(outside)
    with pytest.raises(SystemExit):
        main(repo, "enable")
    assert outside.read_text() == "{}"


def test_malformed_config_is_reported_not_overwritten(repo, installed, capsys):
    path = repo / ".living" / "linters.json"
    path.write_text("not json")
    with pytest.raises(SystemExit):
        main(repo, "status")
    assert path.read_text() == "not json"


def test_rewrite_preserves_file_mode(repo, installed):
    main(repo, "enable")
    path = repo / ".living" / "linters.json"
    path.chmod(0o640)
    main(repo, "disable", "scilintr")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o640
