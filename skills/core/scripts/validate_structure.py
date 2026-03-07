#!/usr/bin/env python3
"""Validate that a repository conforms to mycelium conventions.

Checks for required directories, manifests, and structural conventions.
Returns exit code 0 if valid, 1 if issues are found.

Usage:
    python validate_structure.py [--target-dir PATH] [--strict]
"""

import argparse
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
    """Check that todo/TODOLIST.md exists."""
    todolist_path = target_dir / "todo" / "TODOLIST.md"
    if not todolist_path.exists():
        result.error("todo/TODOLIST.md does not exist")


def check_environments_file(target_dir: Path, result: ValidationResult):
    """Check that ENVIRONMENTS_INSTALLATIONS.md exists at root."""
    env_path = target_dir / "ENVIRONMENTS_INSTALLATIONS.md"
    if not env_path.exists():
        result.error("ENVIRONMENTS_INSTALLATIONS.md does not exist at repo root")


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
