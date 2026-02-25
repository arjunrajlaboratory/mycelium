#!/usr/bin/env python3
"""Post-action hook: update manifests after significant actions.

Scans the repository for changes and updates the relevant MANIFEST.md
files. Intended to be run as part of the post-action hook protocol.

Usage:
    python update_manifests.py [--target-dir PATH] [--scope SCOPE]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Update MANIFEST.md files after repository changes."
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the repository (default: current directory)",
    )
    parser.add_argument(
        "--scope",
        choices=["all", "data", "analysis", "algorithms", "reference_material"],
        default="all",
        help="Which manifests to update (default: all)",
    )
    return parser.parse_args()


def scan_directory(manifest_dir: Path) -> list[dict]:
    """Scan a directory for entries that should be in its manifest."""
    # TODO: Walk subdirectories of the given directory
    # TODO: For each subdirectory, check if it has a README.md
    # TODO: Extract metadata from README.md frontmatter if present
    # TODO: Compare against current MANIFEST.md entries
    # TODO: Return list of new or modified entries
    entries = []
    if manifest_dir.exists():
        for subdir in sorted(manifest_dir.iterdir()):
            if subdir.is_dir() and subdir.name not in ("raw", "processed", "metadata"):
                entries.append({
                    "name": subdir.name,
                    "has_readme": (subdir / "README.md").exists(),
                })
    return entries


def update_manifest(manifest_path: Path, entries: list[dict]):
    """Update a MANIFEST.md with current entries."""
    # TODO: Parse existing MANIFEST.md to find current entries
    # TODO: Merge with scanned entries (don't overwrite user prose)
    # TODO: Add new entries using the appropriate template
    # TODO: Flag entries in manifest that no longer have directories
    print(f"  Would update: {manifest_path}")
    for entry in entries:
        status = "has README" if entry["has_readme"] else "missing README"
        print(f"    - {entry['name']} ({status})")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Manifest Update — Target: {target_dir}")
    print("=" * 50)

    scopes = (
        ["data", "analysis", "algorithms", "reference_material"]
        if args.scope == "all"
        else [args.scope]
    )

    for scope in scopes:
        scope_dir = target_dir / scope
        manifest_path = scope_dir / "MANIFEST.md"

        if not scope_dir.exists():
            print(f"\nSkipping {scope}/ (directory not found)")
            continue

        print(f"\nScanning {scope}/...")
        entries = scan_directory(scope_dir)

        if not entries:
            print(f"  No subdirectories found in {scope}/")
            continue

        update_manifest(manifest_path, entries)

    print("\n" + "=" * 50)
    print("Manifest update complete.")
    print("Review changes and commit when satisfied.")


if __name__ == "__main__":
    main()
