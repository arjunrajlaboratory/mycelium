#!/usr/bin/env python3
"""Mycelium cross-host release gate (issue #67).

One documented entry point that validates and prepares a release candidate
for both Claude Code and Codex from one immutable tree. It never mutates the
repository: a dirty or untracked working tree is a hard failure (release
from a clean disposable clone or worktree instead), version or changelog
drift fails before any test runs, the full validation ladder runs
fail-fast, and the final evidence summary identifies the exact commit,
semantic version, cachebuster, test results, artifact comparison, host-audit
status, and intended tag.

Usage:
    python3 tools/release_gate.py --version 0.7.0 \
        [--installed-root ~/.claude/plugins/cache/mycelium/mycelium/0.7.0] \
        [--claude-audit-evidence PATH --codex-audit-evidence PATH \
         | --waive-host-audits REASON] \
        [--skip-knowledge-map] [--output SUMMARY.md]

See docs/release-process.md for the maintainer workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CACHEBUSTER_RE = re.compile(r"codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")
PACKAGED_DIRS = ("skills", "hooks", ".claude-plugin", ".codex-plugin")


@dataclass
class GateReport:
    version: str
    commit: str = ""
    cachebuster: str | None = None
    checks: list[tuple[str, str]] = field(default_factory=list)
    host_audit: str = ""
    tag: str = ""

    def record(self, name: str, detail: str) -> None:
        self.checks.append((name, detail))


class GateFailure(Exception):
    """A release-gate check failed; the message is actionable."""


def default_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False
    )


def check_clean_tree(repo: Path, runner) -> None:
    result = runner(["git", "status", "--porcelain"], repo)
    if result.returncode != 0:
        raise GateFailure("git status failed; run the gate inside the repository")
    if result.stdout.strip():
        raise GateFailure(
            "working tree is not clean — the gate never includes, deletes, or "
            "bypasses maintainer files; release from a clean disposable clone "
            "or worktree:\n" + result.stdout
        )


def check_versions(repo: Path, version: str) -> str | None:
    """Validate the three version locations; return the cachebuster, if any."""
    if not SEMVER_RE.fullmatch(version):
        raise GateFailure(f"--version {version!r} is not a semantic version")
    claude = json.loads((repo / ".claude-plugin" / "plugin.json").read_text())
    marketplace = json.loads(
        (repo / ".claude-plugin" / "marketplace.json").read_text()
    )
    codex = json.loads((repo / ".codex-plugin" / "plugin.json").read_text())

    if claude.get("version") != version:
        raise GateFailure(
            f".claude-plugin/plugin.json version {claude.get('version')!r} "
            f"!= {version!r}"
        )
    marketplace_version = marketplace.get("metadata", {}).get("version")
    if marketplace_version != version:
        raise GateFailure(
            f".claude-plugin/marketplace.json metadata.version "
            f"{marketplace_version!r} != {version!r}"
        )
    codex_version = codex.get("version", "")
    base, separator, cachebuster = codex_version.partition("+")
    if base != version:
        raise GateFailure(
            f".codex-plugin/plugin.json base version {base!r} != {version!r}"
        )
    if separator:
        if not CACHEBUSTER_RE.fullmatch(cachebuster):
            raise GateFailure(
                f"Codex cachebuster {cachebuster!r} is not a valid "
                "+codex.<token> suffix"
            )
        return cachebuster
    return None


def check_changelog(repo: Path, version: str) -> str:
    """Require a dated `## [version] - YYYY-MM-DD` section; return the date."""
    changelog = (repo / "CHANGELOG.md").read_text()
    match = re.search(
        rf"^## \[{re.escape(version)}\] - (\d{{4}}-\d{{2}}-\d{{2}})$",
        changelog,
        re.MULTILINE,
    )
    if match is None:
        raise GateFailure(
            f"CHANGELOG.md has no dated '## [{version}] - YYYY-MM-DD' section"
        )
    return match.group(1)


def ladder_commands(repo: Path, skip_knowledge_map: bool) -> list[list[str]]:
    commands = [
        [sys.executable, "-m", "pytest", "-q", "skills/core/scripts",
         "--ignore=skills/core/scripts/knowledge_map"],
    ]
    if not skip_knowledge_map:
        commands.append(
            [sys.executable, "-m", "pytest", "-q",
             "skills/core/scripts/knowledge_map"]
        )
    commands += [
        [sys.executable, "-m", "pytest", "-q", "tools"],
        ["bash", "skills/core/hooks/test_stop_hook.sh"],
        ["bash", "skills/core/hooks/test_hooks_stress.sh"],
        ["bash", "skills/core/tests/test_integration_stress.sh"],
        ["bash", "-c",
         "find hooks skills -type f -name '*.sh' -exec bash -n {} +"],
        [sys.executable, "-m", "compileall", "-q", "skills/core/scripts"],
        ["bash", "-c",
         f"{sys.executable} -m json.tool .claude-plugin/plugin.json >/dev/null"],
        ["bash", "-c",
         f"{sys.executable} -m json.tool .codex-plugin/plugin.json >/dev/null"],
        ["bash", "-c",
         f"{sys.executable} -m json.tool hooks/hooks.json >/dev/null"],
        ["git", "diff", "--check"],
    ]
    return commands


def run_ladder(repo: Path, runner, skip_knowledge_map: bool,
               report: GateReport) -> None:
    for command in ladder_commands(repo, skip_knowledge_map):
        result = runner(command, repo)
        rendered = " ".join(command)
        if result.returncode != 0:
            raise GateFailure(
                f"ladder command failed ({rendered}):\n"
                f"{result.stdout}\n{result.stderr}"
            )
        report.record("ladder", f"PASS `{rendered}`")


def run_fresh_init(repo: Path, runner, report: GateReport) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        for command in (
            [sys.executable, "skills/core/scripts/init_repo.py",
             "--target-dir", tmpdir],
            [sys.executable, "skills/core/scripts/validate_structure.py",
             "--target-dir", tmpdir],
        ):
            result = runner(command, repo)
            if result.returncode != 0:
                raise GateFailure(
                    f"fresh-init smoke failed ({' '.join(command)}):\n"
                    f"{result.stdout}\n{result.stderr}"
                )
    report.record("fresh-init", "PASS init + validate_structure in a temp repo")


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def compare_installed(repo: Path, installed_root: Path,
                      report: GateReport) -> None:
    """Require the installed artifact to be byte-identical to the candidate."""
    if not installed_root.is_dir():
        raise GateFailure(
            f"--installed-root {installed_root} is not a directory; a stale "
            "or missing installed artifact makes host smoke results "
            "inconclusive"
        )
    mismatches: list[str] = []
    compared = 0
    for packaged in PACKAGED_DIRS:
        source_dir = repo / packaged
        for source_file in sorted(source_dir.rglob("*")):
            if not source_file.is_file() or "__pycache__" in source_file.parts:
                continue
            relative = source_file.relative_to(repo)
            installed_file = installed_root / relative
            compared += 1
            if not installed_file.is_file():
                mismatches.append(f"missing in install: {relative}")
            elif _file_digest(source_file) != _file_digest(installed_file):
                mismatches.append(f"hash mismatch: {relative}")
    if mismatches:
        raise GateFailure(
            "installed artifact does not match the candidate — reinstall "
            "before running host smokes:\n" + "\n".join(mismatches[:20])
        )
    report.record(
        "installed-artifact",
        f"PASS {compared} packaged files hash-identical at {installed_root}",
    )


def resolve_host_audits(args: argparse.Namespace, report: GateReport) -> None:
    if args.waive_host_audits:
        if args.claude_audit_evidence or args.codex_audit_evidence:
            raise GateFailure(
                "provide audit evidence or a waiver, not both"
            )
        report.host_audit = f"WAIVED: {args.waive_host_audits}"
        return
    missing = [
        flag
        for flag, value in (
            ("--claude-audit-evidence", args.claude_audit_evidence),
            ("--codex-audit-evidence", args.codex_audit_evidence),
        )
        if value is None
    ]
    if missing:
        raise GateFailure(
            "natural-dispatch host audits are a required release step: pass "
            + " and ".join(missing)
            + " (paths to lifecycle-audit evidence), or explicitly "
            "--waive-host-audits REASON"
        )
    for label, evidence in (
        ("Claude", Path(args.claude_audit_evidence)),
        ("Codex", Path(args.codex_audit_evidence)),
    ):
        if not evidence.is_file() or evidence.stat().st_size == 0:
            raise GateFailure(
                f"{label} audit evidence {evidence} is missing or empty"
            )
    report.host_audit = (
        f"Claude: {args.claude_audit_evidence}; "
        f"Codex: {args.codex_audit_evidence}"
    )


def render_summary(report: GateReport) -> str:
    lines = [
        "# Mycelium release gate evidence",
        "",
        f"- Version: `{report.version}`",
        f"- Codex cachebuster: `{report.cachebuster or '(none)'}`",
        f"- Source commit: `{report.commit}`",
        f"- Host audits: {report.host_audit}",
        f"- Intended tag: `{report.tag}`",
        "",
        "## Checks",
        "",
    ]
    lines += [f"- **{name}** — {detail}" for name, detail in report.checks]
    lines += [
        "",
        "## Next steps",
        "",
        f"1. Verify `claude plugin tag --dry-run` resolves `{report.tag}`.",
        f"2. After merge: `git tag {report.tag} && "
        f"git push origin {report.tag}`.",
    ]
    return "\n".join(lines) + "\n"


def run_gate(args: argparse.Namespace, repo: Path, runner) -> GateReport:
    report = GateReport(version=args.version)
    report.tag = f"mycelium--v{args.version}"

    check_clean_tree(repo, runner)
    report.record("clean-tree", "PASS working tree clean")

    report.cachebuster = check_versions(repo, args.version)
    report.record(
        "versions",
        "PASS plugin.json, marketplace.json, and Codex base agree on "
        f"{args.version}",
    )
    date = check_changelog(repo, args.version)
    report.record("changelog", f"PASS dated section [{args.version}] - {date}")

    commit = runner(["git", "rev-parse", "HEAD"], repo)
    if commit.returncode != 0:
        raise GateFailure("could not resolve HEAD commit")
    report.commit = commit.stdout.strip()

    run_ladder(repo, runner, args.skip_knowledge_map, report)
    run_fresh_init(repo, runner, report)

    if args.installed_root:
        compare_installed(repo, Path(args.installed_root).expanduser(), report)
    else:
        report.record(
            "installed-artifact",
            "SKIPPED — pass --installed-root to prove the installed plugin "
            "matches this candidate before host smokes",
        )

    resolve_host_audits(args, report)
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--installed-root", default=None)
    parser.add_argument("--claude-audit-evidence", default=None)
    parser.add_argument("--codex-audit-evidence", default=None)
    parser.add_argument("--waive-host-audits", default=None, metavar="REASON")
    parser.add_argument("--skip-knowledge-map", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    repo = Path(__file__).resolve().parent.parent
    try:
        report = run_gate(args, repo, default_runner)
    except GateFailure as failure:
        print(f"RELEASE GATE FAILED: {failure}", file=sys.stderr)
        return 1
    summary = render_summary(report)
    if args.output:
        args.output.write_text(summary, encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
