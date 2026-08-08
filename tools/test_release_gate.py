"""Tests for the release gate (issue #67). Pure logic only — the validation
ladder is exercised through a stub runner, never by running real suites."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).with_name("release_gate.py")
SPEC = importlib.util.spec_from_file_location("release_gate", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
gate = importlib.util.module_from_spec(SPEC)
sys.modules["release_gate"] = gate
SPEC.loader.exec_module(gate)


def _repo(tmp_path: Path, version: str = "0.7.0",
          codex_version: str | None = None,
          marketplace_version: str | None = None,
          changelog: str | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".claude-plugin").mkdir(parents=True)
    (repo / ".codex-plugin").mkdir(parents=True)
    (repo / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "mycelium", "version": version})
    )
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps(
            {"name": "mycelium",
             "metadata": {"version": marketplace_version or version}}
        )
    )
    (repo / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "mycelium",
                    "version": codex_version or version})
    )
    (repo / "CHANGELOG.md").write_text(
        changelog
        if changelog is not None
        else f"# Changelog\n\n## [{version}] - 2026-08-08\n\n- entry\n"
    )
    return repo


def _result(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------- version agreement ----------


def test_versions_agree_without_cachebuster(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert gate.check_versions(repo, "0.7.0") is None


def test_versions_accept_valid_codex_cachebuster(tmp_path: Path) -> None:
    repo = _repo(tmp_path, codex_version="0.7.0+codex.20260808a")
    assert gate.check_versions(repo, "0.7.0") == "codex.20260808a"


@pytest.mark.parametrize(
    "codex_version",
    ["0.7.0+codex.", "0.7.0+build.1", "0.7.0+", "0.7.1+codex.x", "0.7.1"],
)
def test_versions_reject_divergent_or_malformed_codex(
    tmp_path: Path, codex_version: str
) -> None:
    repo = _repo(tmp_path, codex_version=codex_version)
    with pytest.raises(gate.GateFailure):
        gate.check_versions(repo, "0.7.0")


def test_versions_reject_marketplace_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path, marketplace_version="0.6.0")
    with pytest.raises(gate.GateFailure, match="marketplace"):
        gate.check_versions(repo, "0.7.0")


def test_versions_reject_claude_manifest_drift(tmp_path: Path) -> None:
    repo = _repo(tmp_path, version="0.6.0")
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"metadata": {"version": "0.7.0"}})
    )
    (repo / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"version": "0.7.0"})
    )
    with pytest.raises(gate.GateFailure, match="plugin.json"):
        gate.check_versions(repo, "0.7.0")


def test_versions_reject_non_semver_input(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(gate.GateFailure, match="semantic version"):
        gate.check_versions(repo, "v0.7")


# ---------- changelog ----------


def test_changelog_requires_dated_section(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert gate.check_changelog(repo, "0.7.0") == "2026-08-08"


@pytest.mark.parametrize(
    "changelog",
    [
        "# Changelog\n\n## [Unreleased]\n",
        "# Changelog\n\n## [0.7.0]\n",
        "# Changelog\n\n## [0.7.0] - soon\n",
    ],
)
def test_changelog_rejects_missing_or_undated_section(
    tmp_path: Path, changelog: str
) -> None:
    repo = _repo(tmp_path, changelog=changelog)
    with pytest.raises(gate.GateFailure, match="CHANGELOG"):
        gate.check_changelog(repo, "0.7.0")


# ---------- clean tree ----------


def test_dirty_tree_is_a_hard_failure(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    def runner(command, cwd):
        return _result(stdout="?? notes.md\n")

    with pytest.raises(gate.GateFailure, match="disposable clone"):
        gate.check_clean_tree(repo, runner)


# ---------- ladder ----------


def test_ladder_fails_fast_on_first_failing_command(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    report = gate.GateReport(version="0.7.0")
    calls: list[str] = []

    def runner(command, cwd):
        calls.append(" ".join(command))
        if len(calls) == 2:
            return _result(returncode=1, stderr="boom")
        return _result()

    with pytest.raises(gate.GateFailure, match="boom"):
        gate.run_ladder(repo, runner, False, report)
    assert len(calls) == 2  # nothing after the failure ran


def test_ladder_skips_knowledge_map_only_when_asked(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with_km = gate.ladder_commands(repo, skip_knowledge_map=False)
    without_km = gate.ladder_commands(repo, skip_knowledge_map=True)
    rendered = ["knowledge_map" in " ".join(c) for c in with_km]
    assert any(
        "pytest" in " ".join(c) and "knowledge_map" in " ".join(c)
        and "--ignore" not in " ".join(c)
        for c in with_km
    )
    assert len(without_km) == len(with_km) - 1
    assert rendered  # sanity: commands rendered


# ---------- installed artifact comparison ----------


def _packaged_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = _repo(tmp_path)
    (repo / "skills" / "core").mkdir(parents=True)
    (repo / "skills" / "core" / "SKILL.md").write_text("skill\n")
    (repo / "hooks").mkdir()
    (repo / "hooks" / "hooks.json").write_text("{}\n")
    install = tmp_path / "install"
    for relative in (
        "skills/core/SKILL.md",
        "hooks/hooks.json",
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        ".codex-plugin/plugin.json",
    ):
        target = install / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repo / relative).read_bytes())
    return repo, install


def test_matching_installed_artifact_passes(tmp_path: Path) -> None:
    repo, install = _packaged_repo(tmp_path)
    report = gate.GateReport(version="0.7.0")
    gate.compare_installed(repo, install, report)
    assert any("installed-artifact" in name for name, _ in report.checks)


def test_stale_installed_artifact_fails(tmp_path: Path) -> None:
    repo, install = _packaged_repo(tmp_path)
    (install / "skills" / "core" / "SKILL.md").write_text("older content\n")
    with pytest.raises(gate.GateFailure, match="hash mismatch"):
        gate.compare_installed(repo, install, gate.GateReport(version="0.7.0"))


def test_missing_installed_file_fails(tmp_path: Path) -> None:
    repo, install = _packaged_repo(tmp_path)
    (install / "hooks" / "hooks.json").unlink()
    with pytest.raises(gate.GateFailure, match="missing in install"):
        gate.compare_installed(repo, install, gate.GateReport(version="0.7.0"))


def test_stale_extra_installed_file_fails(tmp_path: Path) -> None:
    """PR #72 P2: a reused install carrying a file removed from the
    candidate must fail — host smokes could execute the obsolete content."""
    repo, install = _packaged_repo(tmp_path)
    (install / "skills" / "core" / "obsolete_helper.py").write_text("old\n")
    with pytest.raises(gate.GateFailure, match="stale extra"):
        gate.compare_installed(repo, install, gate.GateReport(version="0.7.0"))


def test_pycache_in_install_is_tolerated(tmp_path: Path) -> None:
    repo, install = _packaged_repo(tmp_path)
    pycache = install / "skills" / "core" / "__pycache__"
    pycache.mkdir()
    (pycache / "mod.cpython-311.pyc").write_bytes(b"\x00")
    report = gate.GateReport(version="0.7.0")
    gate.compare_installed(repo, install, report)
    assert any("installed-artifact" in name for name, _ in report.checks)


# ---------- host audits ----------


def _audit_args(**overrides):
    base = {
        "claude_audit_evidence": None,
        "codex_audit_evidence": None,
        "waive_host_audits": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_host_audits_required_without_waiver() -> None:
    with pytest.raises(gate.GateFailure, match="required release step"):
        gate.resolve_host_audits(_audit_args(), gate.GateReport(version="0.7.0"))


def test_waiver_is_recorded(tmp_path: Path) -> None:
    report = gate.GateReport(version="0.7.0")
    gate.resolve_host_audits(
        _audit_args(waive_host_audits="hosts audited in PR #70"), report
    )
    assert report.host_audit.startswith("WAIVED")


def test_waiver_and_evidence_are_mutually_exclusive(tmp_path: Path) -> None:
    evidence = tmp_path / "claude.md"
    evidence.write_text("evidence\n")
    with pytest.raises(gate.GateFailure, match="not both"):
        gate.resolve_host_audits(
            _audit_args(
                waive_host_audits="x", claude_audit_evidence=str(evidence)
            ),
            gate.GateReport(version="0.7.0"),
        )


def test_empty_audit_evidence_fails(tmp_path: Path) -> None:
    claude = tmp_path / "claude.md"
    claude.write_text("evidence\n")
    codex = tmp_path / "codex.md"
    codex.write_text("")
    with pytest.raises(gate.GateFailure, match="missing or empty"):
        gate.resolve_host_audits(
            _audit_args(
                claude_audit_evidence=str(claude),
                codex_audit_evidence=str(codex),
            ),
            gate.GateReport(version="0.7.0"),
        )


def test_ladder_never_embeds_the_interpreter_in_shell_strings(
    tmp_path: Path,
) -> None:
    """PR #72 round-4 P2: a spaced interpreter path must survive — pass it
    as an argv element, never inside a `bash -c` string."""
    import sys as _sys

    for command in gate.ladder_commands(_repo(tmp_path), skip_knowledge_map=True):
        if command[:2] == ["bash", "-c"]:
            assert _sys.executable not in command[2]


# ---------- output location ----------


def test_output_inside_the_candidate_is_rejected(tmp_path: Path) -> None:
    """PR #72 round-2 P2: the gate must never dirty the tree it certified."""
    repo = _repo(tmp_path)
    with pytest.raises(gate.GateFailure, match="outside the candidate"):
        gate.check_output_location(repo, repo / "release-evidence.md")
    with pytest.raises(gate.GateFailure, match="outside the candidate"):
        gate.check_output_location(repo, repo / "docs" / "evidence.md")


def test_output_outside_the_candidate_is_accepted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    gate.check_output_location(repo, tmp_path / "release-evidence.md")
    gate.check_output_location(repo, None)


# ---------- summary ----------


def test_summary_identifies_release_facts() -> None:
    report = gate.GateReport(version="0.7.0")
    report.commit = "abc1234"
    report.cachebuster = "codex.20260808a"
    report.host_audit = "WAIVED: reason"
    report.tag = "mycelium--v0.7.0"
    report.record("versions", "PASS")
    summary = gate.render_summary(report)
    assert "`0.7.0`" in summary
    assert "`codex.20260808a`" in summary
    assert "`abc1234`" in summary
    assert "mycelium--v0.7.0" in summary
    assert "claude plugin tag --dry-run" in summary
    assert "git tag mycelium--v0.7.0" in summary
