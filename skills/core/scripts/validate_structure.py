#!/usr/bin/env python3
"""Validate that a repository conforms to mycelium conventions.

Checks for required directories, manifests, and structural conventions.
Returns exit code 0 if valid, 1 if issues are found.

Usage:
    python validate_structure.py [--target-dir PATH] [--strict]
"""

import argparse
import re
import sys
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Validate mycelium repository structure."
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the repository (default: current directory)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on warnings (not just errors)",
    )
    return parser.parse_args()


class ValidationResult:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)

    def warning(self, msg: str):
        self.warnings.append(msg)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def print_report(self):
        if self.errors:
            print(f"\nErrors ({len(self.errors)}):")
            for err in self.errors:
                print(f"  ✗ {err}")

        if self.warnings:
            print(f"\nWarnings ({len(self.warnings)}):")
            for warn in self.warnings:
                print(f"  ! {warn}")

        if not self.errors and not self.warnings:
            print("\n  All checks passed.")


def check_living_directory(target_dir: Path, result: ValidationResult):
    """Check that .living/ exists with required files."""
    living_dir = target_dir / ".living"

    if not living_dir.exists():
        result.error(".living/ directory does not exist")
        return

    required_files = ["decisions.md", "learnings.md", "conventions.md"]
    for filename in required_files:
        if not (living_dir / filename).exists():
            result.error(f".living/{filename} does not exist")

    conventions_dir = living_dir / "conventions"
    if not conventions_dir.exists():
        result.warning(".living/conventions/ directory does not exist")
    elif not (conventions_dir / "ACTIVE_CONVENTIONS.yaml").exists():
        result.warning(".living/conventions/ACTIVE_CONVENTIONS.yaml does not exist")

    generated_dir = living_dir / "generated-conventions"
    if not generated_dir.exists():
        result.warning(".living/generated-conventions/ directory does not exist")


# Append-only knowledge logs whose entries generate_index.py parses off a
# literal heading prefix. conventions.md is deliberately excluded: every parser
# reads it at "## ".
ENTRY_LOG_FILES = ("learnings.md", "decisions.md")

# The heading level every generate_index.py parser requires for the files above
# (count_headers_and_topics, collect_entries, extract_entry_snippets). An entry
# at any other level is counted as zero entries and never reaches
# .living/INDEX.md — historically with no error raised anywhere.
ENTRY_HEADING_PREFIX = "### "

# An entry heading *leads* with its date, as both shipped templates do:
# "### [YYYY-MM-DD] Title" or the bare "### YYYY-MM-DD Title". The date must
# follow the hashes immediately -- a structural heading that merely mentions a
# date somewhere ("## Archive (entries before 2025-01-01)") is not an entry, and
# treating it as one both failed validation and let the repair mint a phantom
# entry that renumbered every real one.
_DATED_HEADING_RE = re.compile(r"^#{1,6}\s+\[?\d{4}-\d{2}-\d{2}\]?")

# Fenced blocks are skipped so that an entry documenting the entry format does
# not report itself.
_FENCE_RE = re.compile(r"^\s*(```|~~~)")

_MAX_REPORTED_LINES = 5


def split_log_lines(text: str) -> list[str]:
    """Split a knowledge log the way the parsers read it: on newlines only.

    ``str.splitlines`` additionally breaks on form feed, U+2028 and other
    Unicode boundaries, which would number lines differently from the parsers
    and from any repair keyed to those numbers. Callers that rewrite a log MUST
    use this function so detection and rewriting index identically.
    """
    return text.split("\n")


def mislevelled_entry_lines(path: Path) -> list[int]:
    """Return 1-based line numbers of dated headings the parsers will not read.

    Read-only, so undecodable bytes are replaced rather than raising. Callers
    that rewrite the file must decode strictly and pass the text to
    `mislevelled_entry_lines_in_text` instead, so a replacement character is
    never written back over real content.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    return mislevelled_entry_lines_in_text(text)


def mislevelled_entry_lines_in_text(text: str) -> list[int]:
    """Line numbers (1-based, per `split_log_lines`) of unparseable entries.

    Headings inside fenced code blocks are ignored: they are illustrative
    markdown, not entries.
    """
    mislevelled: list[int] = []
    fence_marker: str | None = None

    for lineno, raw in enumerate(split_log_lines(text), start=1):
        # Tolerate CRLF text even though every in-tree caller decodes with
        # universal newlines: this is a public entry point.
        line = raw.rstrip("\r")

        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            # Only the marker that opened a fence can close it, so a "~~~"
            # inside a "```" block does not end the block.
            if fence_marker is None:
                fence_marker = marker
            elif marker == fence_marker:
                fence_marker = None
            continue
        if fence_marker is not None:
            continue

        # Anything the parsers accept is exempt; everything reaching the
        # dated-heading test below is by definition unparseable.
        if line.startswith(ENTRY_HEADING_PREFIX):
            continue

        if _DATED_HEADING_RE.match(line):
            mislevelled.append(lineno)

    return mislevelled


def check_entry_heading_levels(target_dir: Path, result: ValidationResult):
    """Flag knowledge logs whose entries use a heading level no parser reads.

    Reported as an error, not a warning: the entries are on disk and greppable,
    but `.living/INDEX.md` reports "0 entries" and the SessionStart hook
    surfaces an empty knowledge log, so agents are told to trust an index that
    is missing real knowledge. `migrate_existing_repos.py` repairs the headings
    in place and regenerates the index.
    """
    living_dir = target_dir / ".living"
    if not living_dir.is_dir():
        return

    for filename in ENTRY_LOG_FILES:
        path = living_dir / filename
        if not path.is_file():
            continue

        mislevelled = mislevelled_entry_lines(path)
        if not mislevelled:
            continue

        shown = ", ".join(f"line {n}" for n in mislevelled[:_MAX_REPORTED_LINES])
        if len(mislevelled) > _MAX_REPORTED_LINES:
            shown += f", and {len(mislevelled) - _MAX_REPORTED_LINES} more"

        result.error(
            f".living/{filename} has {len(mislevelled)} dated "
            f"{'heading' if len(mislevelled) == 1 else 'headings'} that "
            f"generate_index.py cannot parse ({shown}); entries must use "
            f"'{ENTRY_HEADING_PREFIX.strip()}' or they are silently omitted "
            f"from .living/INDEX.md. Fix with: "
            f"migrate_existing_repos.py --repo <repo>"
        )


def check_top_level_directories(target_dir: Path, result: ValidationResult):
    """Check that all four top-level directories exist."""
    required_dirs = ["algorithms", "analysis", "data", "reference_material", "todo"]

    for dir_name in required_dirs:
        dir_path = target_dir / dir_name
        if not dir_path.exists():
            result.error(f"{dir_name}/ directory does not exist")
        elif not dir_path.is_dir():
            result.error(f"{dir_name} exists but is not a directory")


MANIFEST_NAMES = {
    "algorithms": "ALGORITHM_MANIFEST.md",
    "analysis": "ANALYSIS_MANIFEST.md",
    "data": "DATA_MANIFEST.md",
    "reference_material": "REFERENCE_MANIFEST.md",
}


def check_manifests(target_dir: Path, result: ValidationResult):
    """Check that each top-level directory has its descriptive manifest."""
    for dir_name, manifest_name in MANIFEST_NAMES.items():
        manifest_path = target_dir / dir_name / manifest_name
        # Also check for legacy MANIFEST.md
        legacy_path = target_dir / dir_name / "MANIFEST.md"
        if not manifest_path.exists():
            if legacy_path.exists():
                result.warning(
                    f"{dir_name}/MANIFEST.md exists but should be renamed to {manifest_name}"
                )
            else:
                result.error(f"{dir_name}/{manifest_name} does not exist")
        elif manifest_path.stat().st_size == 0:
            result.warning(f"{dir_name}/{manifest_name} is empty")


def check_manifest_format(target_dir: Path, result: ValidationResult):
    """Check that manifest files have valid content."""
    for dir_name, manifest_name in MANIFEST_NAMES.items():
        manifest_path = target_dir / dir_name / manifest_name
        if not manifest_path.exists():
            continue

        content = manifest_path.read_text()
        if not content.strip():
            continue

        # Check for a heading
        if not content.startswith("#"):
            result.warning(f"{dir_name}/{manifest_name} does not start with a heading")


def folder_to_doc_name(folder_name: str) -> str:
    """Convert a folder name to its UPPER_SNAKE_CASE documentation filename."""
    return folder_name.upper().replace("-", "_") + ".md"


def check_analysis_docs(target_dir: Path, result: ValidationResult):
    """Check that any analysis subdirectory has its documentation file."""
    analysis_dir = target_dir / "analysis"
    if not analysis_dir.exists():
        return

    for subdir in analysis_dir.iterdir():
        if subdir.is_dir() and subdir.name != ".git":
            expected_doc = folder_to_doc_name(subdir.name)
            doc_path = subdir / expected_doc
            legacy_readme = subdir / "README.md"
            if not doc_path.exists():
                if legacy_readme.exists():
                    result.warning(
                        f"analysis/{subdir.name}/README.md should be renamed to {expected_doc}"
                    )
                else:
                    result.warning(f"analysis/{subdir.name}/ has no {expected_doc}")


def check_todo_directory(target_dir: Path, result: ValidationResult):
    """Check that the todo registry and item template exist."""
    for name in ("TODO_REGISTRY.md", "TODO_ITEM_TEMPLATE.md"):
        if not (target_dir / "todo" / name).exists():
            result.error(f"todo/{name} does not exist")


def check_environments_file(target_dir: Path, result: ValidationResult):
    """Check that ENVIRONMENTS_INSTALLATIONS.md exists at root."""
    env_path = target_dir / "ENVIRONMENTS_INSTALLATIONS.md"
    if not env_path.exists():
        result.error("ENVIRONMENTS_INSTALLATIONS.md does not exist at repo root")


def check_agent_guidance(target_dir: Path, result: ValidationResult):
    """Check canonical guidance and host adapters."""
    if not (target_dir / "MYCELIUM.md").exists():
        result.error("MYCELIUM.md does not exist at repo root")
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = target_dir / name
        if not path.exists():
            result.error(f"{name} does not exist at repo root")
        elif "MYCELIUM" not in path.read_text(encoding="utf-8"):
            result.warning(f"{name} does not route the agent to MYCELIUM.md")

    state_gitignore = target_dir / ".mycelium" / ".gitignore"
    if not state_gitignore.exists():
        result.warning(".mycelium/.gitignore does not exist")

    if not (target_dir / ".claude" / "settings.local.json").exists():
        result.warning("Claude hook configuration is not installed")
    codex_hooks = target_dir / ".codex" / "hooks.json"
    if codex_hooks.exists() and "mycelium-" in codex_hooks.read_text(
        encoding="utf-8"
    ):
        result.warning(
            "Legacy project-local Mycelium Codex hooks are installed; "
            "run the Mycelium migration to use stable plugin-bundled hooks"
        )


def check_data_structure(target_dir: Path, result: ValidationResult):
    """Check data directory subdirectories."""
    data_dir = target_dir / "data"
    if not data_dir.exists():
        return

    for subdir_name in ["raw", "processed", "metadata"]:
        subdir = data_dir / subdir_name
        if not subdir.exists():
            result.warning(f"data/{subdir_name}/ does not exist")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()
    result = ValidationResult()

    print(f"Mycelium Structure Validation — {target_dir}")
    print("=" * 50)

    print("\nChecking .living/ directory...")
    check_living_directory(target_dir, result)

    print("Checking knowledge log entry heading levels...")
    check_entry_heading_levels(target_dir, result)

    print("Checking top-level directories...")
    check_top_level_directories(target_dir, result)

    print("Checking manifests...")
    check_manifests(target_dir, result)

    print("Checking manifest format...")
    check_manifest_format(target_dir, result)

    print("Checking analysis documentation files...")
    check_analysis_docs(target_dir, result)

    print("Checking todo directory...")
    check_todo_directory(target_dir, result)

    print("Checking ENVIRONMENTS_INSTALLATIONS.md...")
    check_environments_file(target_dir, result)

    print("Checking agent guidance and hooks...")
    check_agent_guidance(target_dir, result)

    print("Checking data structure...")
    check_data_structure(target_dir, result)

    result.print_report()

    print("\n" + "=" * 50)
    if result.is_valid and (not args.strict or not result.warnings):
        print("Validation PASSED")
        sys.exit(0)
    elif result.is_valid and args.strict and result.warnings:
        print("Validation FAILED (strict mode — warnings treated as errors)")
        sys.exit(1)
    else:
        print("Validation FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
