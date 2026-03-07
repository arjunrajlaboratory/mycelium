#!/usr/bin/env python3
"""Post-action hook: update manifests after significant actions.

Scans the repository for changes and updates the relevant manifest
files (ANALYSIS_MANIFEST.md, DATA_MANIFEST.md, etc.). Intended to
be run as part of the post-action hook protocol.

Usage:
    python update_manifests.py [--target-dir PATH] [--scope SCOPE]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Update manifest files after repository changes."
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
    # TODO: For each subdirectory, check if it has its UPPER_SNAKE_CASE.md doc
    # TODO: Extract metadata from doc frontmatter if present
    # TODO: Compare against current manifest entries
    # TODO: Return list of new or modified entries
    entries = []
    if manifest_dir.exists():
        for subdir in sorted(manifest_dir.iterdir()):
            if subdir.is_dir() and subdir.name not in ("raw", "processed", "metadata"):
                doc_name = subdir.name.upper().replace("-", "_") + ".md"
                entries.append({
                    "name": subdir.name,
                    "has_doc": (subdir / doc_name).exists(),
                })
    return entries


def update_manifest(manifest_path: Path, entries: list[dict]):
    """Update a MANIFEST.md with current entries."""
    # TODO: Parse existing manifest to find current entries
    # TODO: Merge with scanned entries (don't overwrite user prose)
    # TODO: Add new entries using the appropriate template
    # TODO: Flag entries in manifest that no longer have directories
    print(f"  Would update: {manifest_path}")
    for entry in entries:
        status = "has doc" if entry["has_doc"] else "missing doc"
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
        manifest_names = {
            "algorithms": "ALGORITHM_MANIFEST.md",
            "analysis": "ANALYSIS_MANIFEST.md",
            "data": "DATA_MANIFEST.md",
            "reference_material": "REFERENCE_MANIFEST.md",
        }
        manifest_path = scope_dir / manifest_names.get(scope, "MANIFEST.md")

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
