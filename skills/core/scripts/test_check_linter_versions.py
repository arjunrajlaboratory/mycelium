"""Tests for check_linter_versions — report linter versions, never install."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_linter_versions as clv  # noqa: E402


def fake_cli(tmp_path: Path, name: str, output: str | None, exit_code: int = 0) -> Path:
    """A fake linter executable on a private PATH directory."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    body = f"printf '%s\\n' {json.dumps(output)}\n" if output is not None else ""
    script.write_text(f"#!/bin/sh\n{body}exit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return bin_dir


@pytest.fixture
def no_metadata(monkeypatch):
    """Keep the test interpreter's own installed packages out of the result."""
    monkeypatch.setattr(clv, "metadata_version", lambda name, executable=None: None)


def run(monkeypatch, capsys, argv, path=None, latest=None):
    if path is not None:
        monkeypatch.setenv("PATH", str(path))
    monkeypatch.setattr(clv, "fetch_latest", lambda name, timeout: (latest or {}).get(name))
    code = clv.main(argv)
    return code, capsys.readouterr().out


def test_parse_spec():
    assert clv.parse_spec("scitexlintr>=0.2") == ("scitexlintr", (0, 2))
    assert clv.parse_spec("scilintr") == ("scilintr", None)
    with pytest.raises(ValueError):
        clv.parse_spec("scilintr==0.2")


def test_version_key_orders_numerically():
    assert clv.version_key("0.10.0") > clv.version_key("0.9.1")
    assert clv.version_key("0.2") == clv.version_key("0.2.0")
    assert clv.version_key("not a version") is None


def test_up_to_date(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path, {"scitexlintr": "0.2.0"})
    assert code == 0
    assert "scitexlintr 0.2.0 (latest 0.2.0): ok" in out
    assert "pip install" not in out


def test_newer_release_is_reported_not_installed(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path, {"scitexlintr": "0.3.1"})
    assert code == 0  # an available upgrade is advice, not a failure
    assert "update available: 0.3.1" in out
    assert 'python -m pip install -U "scitexlintr>=0.2"' in out
    assert "Ask the user" in out


def test_below_minimum_fails(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.1.4")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path, {"scitexlintr": "0.2.0"})
    assert code == 1
    assert "below the required 0.2" in out


def test_unknown_version_with_minimum_fails(tmp_path, monkeypatch, capsys, no_metadata):
    # scitexlintr before 0.2 had no --version flag: argparse exits 2.
    path = fake_cli(tmp_path, "scitexlintr", "error: unrecognized arguments: --version", exit_code=2)
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path)
    assert code == 1
    assert "cannot determine" in out


def test_unknown_version_without_minimum_is_ok(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scilintr", "usage: scilintr [-h] paths", exit_code=2)
    code, out = run(monkeypatch, capsys, ["scilintr"], path)
    assert code == 0
    assert "scilintr: installed (version unknown)" in out


def test_metadata_fallback_when_cli_has_no_version_flag(tmp_path, monkeypatch, capsys):
    path = fake_cli(tmp_path, "scilintr", "usage", exit_code=2)
    monkeypatch.setattr(clv, "metadata_version", lambda name, executable=None: "0.1.1")
    code, out = run(monkeypatch, capsys, ["scilintr"], path, {"scilintr": "0.1.1"})
    assert code == 0
    assert "scilintr 0.1.1 (latest 0.1.1): ok" in out


def test_missing_cli_fails_with_install_hint(tmp_path, monkeypatch, capsys, no_metadata):
    (tmp_path / "empty").mkdir()
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], tmp_path / "empty")
    assert code == 1
    assert "not installed" in out
    assert 'python -m pip install "scitexlintr>=0.2"' in out


def test_offline_skips_the_index(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    monkeypatch.setenv("PATH", str(path))

    def boom(name, timeout):
        raise AssertionError("--offline must not query the index")

    monkeypatch.setattr(clv, "fetch_latest", boom)
    assert clv.main(["--offline", "scitexlintr>=0.2"]) == 0
    assert "latest unknown" in capsys.readouterr().out


def test_unreachable_index_is_not_an_error(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path, latest={})
    assert code == 0
    assert "latest unknown" in out


def test_fetch_latest_returns_none_on_network_error(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr(clv.urllib.request, "urlopen", fail)
    assert clv.fetch_latest("scitexlintr", timeout=0.1) is None


def test_record_writes_versions_into_manifest(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    manifest = tmp_path / ".manifest.json"
    manifest.write_text(json.dumps({"numbers": [{"id": "n", "value": 3}], "linters": {"old": "1"}}, indent=2) + "\n")
    code, _ = run(monkeypatch, capsys, ["--record", str(manifest), "scitexlintr>=0.2"], path)
    assert code == 0
    data = json.loads(manifest.read_text())
    assert data["numbers"] == [{"id": "n", "value": 3}]  # other content untouched
    assert data["linters"] == {"old": "1", "scitexlintr": "0.2.0"}


def test_record_skipped_when_check_fails(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.1.0")
    manifest = tmp_path / ".manifest.json"
    original = json.dumps({"numbers": []}) + "\n"
    manifest.write_text(original)
    code, _ = run(monkeypatch, capsys, ["--record", str(manifest), "scitexlintr>=0.2"], path)
    assert code == 1
    assert manifest.read_text() == original


def test_record_rejects_non_object_manifest(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    manifest = tmp_path / ".manifest.json"
    manifest.write_text("[]\n")
    code, out = run(monkeypatch, capsys, ["--record", str(manifest), "scitexlintr>=0.2"], path)
    assert code == 1
    assert "JSON object" in out
    assert manifest.read_text() == "[]\n"


# --- review fixes -----------------------------------------------------------


def test_record_preserves_manifest_permissions(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    manifest = tmp_path / ".manifest.json"
    manifest.write_text("{}\n")
    manifest.chmod(0o644)
    code, _ = run(monkeypatch, capsys, ["--record", str(manifest), "scitexlintr>=0.2"], path)
    assert code == 0
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o644  # mkstemp would leave 0600


def test_record_through_a_symlink_updates_the_target(tmp_path, monkeypatch, capsys, no_metadata):
    path = fake_cli(tmp_path, "scitexlintr", "scitexlintr 0.2.0")
    real = tmp_path / "real.json"
    real.write_text("{}\n")
    link = tmp_path / ".manifest.json"
    link.symlink_to(real)
    code, _ = run(monkeypatch, capsys, ["--record", str(link), "scitexlintr>=0.2"], path)
    assert code == 0
    assert link.is_symlink()
    assert json.loads(real.read_text())["linters"] == {"scitexlintr": "0.2.0"}


def test_package_installed_but_cli_not_on_path_fails(tmp_path, monkeypatch, capsys):
    # e.g. a --user install whose bin directory is not on PATH: the skills run
    # the CLI, so "importable here" is not "usable".
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(clv, "metadata_version", lambda name, executable=None: "0.2.0")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], tmp_path / "empty")
    assert code == 1
    assert "not on PATH" in out


def test_prerelease_below_minimum_fails(tmp_path, monkeypatch, capsys):
    path = fake_cli(tmp_path, "scitexlintr", "usage", exit_code=2)
    monkeypatch.setattr(clv, "metadata_version", lambda name, executable=None: "0.1.9rc1")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path)
    assert code == 1
    assert "below the required 0.2" in out


def test_version_key_reads_the_release_prefix():
    assert clv.version_key("0.3.0rc1") == (0, 3)
    assert clv.version_key("1.2.post1") == (1, 2)


def test_metadata_comes_from_the_cli_interpreter(tmp_path, monkeypatch, capsys):
    # The CLI lives in another environment (pipx, a project venv): its version
    # is what that environment has installed, not what this interpreter has.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    other_python = tmp_path / "other-env-python"
    other_python.write_text('#!/bin/sh\nprintf "0.1.1\\n"\n')
    other_python.chmod(0o755)
    cli = bin_dir / "scilintr"
    cli.write_text(f"#!{other_python}\nexit 2\n")
    cli.chmod(0o755)
    monkeypatch.setattr(clv.metadata, "version", lambda name: "9.9.9")
    code, out = run(monkeypatch, capsys, ["scilintr"], bin_dir, {"scilintr": "0.1.1"})
    assert code == 0
    assert "scilintr 0.1.1 (latest 0.1.1): ok" in out


def test_unparseable_metadata_version_with_minimum_fails(tmp_path, monkeypatch, capsys):
    path = fake_cli(tmp_path, "scitexlintr", "usage", exit_code=2)
    monkeypatch.setattr(clv, "metadata_version", lambda name, executable=None: "unknown")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path)
    assert code == 1
    assert "cannot determine" in out


def test_non_python_launcher_does_not_borrow_this_interpreters_metadata(tmp_path, monkeypatch, capsys):
    # Codex P1: an old 0.1 command on PATH (no --version) behind a shell
    # wrapper, while this interpreter has 0.2 metadata. The minimum must not be
    # satisfied by an unrelated environment.
    path = fake_cli(tmp_path, "scitexlintr", "error: unrecognized arguments: --version", exit_code=2)
    monkeypatch.setattr(clv.metadata, "version", lambda name: "0.2.0")
    code, out = run(monkeypatch, capsys, ["scitexlintr>=0.2"], path)
    assert code == 1
    assert "cannot determine" in out


def test_same_interpreter_script_uses_in_process_metadata(tmp_path, monkeypatch, capsys):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "scilintr"
    cli.write_text(f"#!{sys.executable}\nimport sys; sys.exit(2)\n")
    cli.chmod(0o755)
    monkeypatch.setattr(clv.metadata, "version", lambda name: "0.1.1")
    code, out = run(monkeypatch, capsys, ["scilintr"], bin_dir, {"scilintr": "0.1.1"})
    assert code == 0
    assert "scilintr 0.1.1 (latest 0.1.1): ok" in out
