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
        [--skip-knowledge-map] [--output ../release-evidence.md]

The evidence summary must be written outside the candidate tree; an
in-repository --output path is rejected before any check runs.

See docs/release-process.md for the maintainer workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

CACHEBUSTER_RE = re.compile(r"codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*")
# Generated caches are exempt from the installed-artifact comparison on both
# sides: a leftover .pytest_cache from a manual pytest run (the gate's own
# ladder never creates one) or interpreter bytecode is not release content.
_GENERATED_CACHE_DIRS = {"__pycache__", ".pytest_cache"}


def _is_generated_cache_artifact(path: Path) -> bool:
    return (
        bool(_GENERATED_CACHE_DIRS.intersection(path.parts))
        or path.suffix == ".pyc"
    )
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


_BYTECODE_PREFIX = tempfile.mkdtemp(prefix="mycelium-release-gate-pycache-")


def default_runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # Compilation byproducts must never land inside the candidate the gate
    # certifies immutable: redirect all bytecode to a temp cache prefix.
    env = dict(os.environ, PYTHONPYCACHEPREFIX=_BYTECODE_PREFIX)
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, check=False, env=env
    )


def check_output_location(repo: Path, output: Path | None) -> None:
    """Refuse to write evidence into the tree the gate certifies immutable."""
    if output is None:
        return
    resolved = output.expanduser().resolve()
    repo_resolved = repo.resolve()
    if resolved == repo_resolved or repo_resolved in resolved.parents:
        raise GateFailure(
            f"--output {output} is inside the release candidate; the gate "
            "never mutates the tree it validates — write the evidence "
            "summary outside the candidate (e.g. ../release-evidence.md)"
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
    # pytest's cache provider is disabled so .pytest_cache is never created
    # inside the candidate; bytecode goes to the runner's temp cache prefix.
    commands = [
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "skills/core/scripts",
         "--ignore=skills/core/scripts/knowledge_map"],
    ]
    if not skip_knowledge_map:
        commands.append(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "skills/core/scripts/knowledge_map"]
        )
    commands += [
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tools"],
        ["bash", "skills/core/hooks/test_stop_hook.sh"],
        ["bash", "skills/core/hooks/test_hooks_stress.sh"],
        ["bash", "skills/core/tests/test_integration_stress.sh"],
        ["bash", "-c",
         "find hooks skills -type f -name '*.sh' -exec bash -n {} +"],
        [sys.executable, "-m", "compileall", "-q", "skills/core/scripts"],
        [sys.executable, "-m", "json.tool", ".claude-plugin/plugin.json"],
        [sys.executable, "-m", "json.tool", ".codex-plugin/plugin.json"],
        [sys.executable, "-m", "json.tool", "hooks/hooks.json"],
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
    source_files: set[Path] = set()
    for packaged in PACKAGED_DIRS:
        source_dir = repo / packaged
        for source_file in sorted(source_dir.rglob("*")):
            if not source_file.is_file() or _is_generated_cache_artifact(
                source_file
            ):
                continue
            relative = source_file.relative_to(repo)
            source_files.add(relative)
            installed_file = installed_root / relative
            compared += 1
            if installed_file.is_symlink():
                mismatches.append(f"symlink in install: {relative}")
            elif not installed_file.is_file():
                mismatches.append(f"missing in install: {relative}")
            elif _file_digest(source_file) != _file_digest(installed_file):
                mismatches.append(f"hash mismatch: {relative}")
            elif (
                source_file.stat().st_mode & 0o111
                != installed_file.stat().st_mode & 0o111
            ):
                # Same bytes with a lost executable bit still breaks every
                # direct hook dispatch with permission denied.
                mismatches.append(f"mode mismatch (exec bits): {relative}")
    # The reverse direction matters too: a reused install may carry packaged
    # files the candidate no longer ships, and a host smoke could discover
    # or execute that obsolete content. Only generated artifacts are exempt.
    for packaged in PACKAGED_DIRS:
        installed_dir = installed_root / packaged
        if not installed_dir.is_dir():
            continue
        for installed_file in sorted(installed_dir.rglob("*")):
            if not installed_file.is_file():
                continue
            if _is_generated_cache_artifact(installed_file):
                continue
            relative = installed_file.relative_to(installed_root)
            if relative not in source_files:
                mismatches.append(f"stale extra in install: {relative}")
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

    check_output_location(repo, args.output)
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

    # The gate certifies the candidate immutable, so prove it: after every
    # ladder command and smoke, the tree must be exactly as clean as it
    # started.
    check_clean_tree(repo, runner)
    report.record("immutability", "PASS working tree unchanged after the ladder")

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
