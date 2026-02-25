#!/usr/bin/env python3
"""Dataset ingestion pipeline.

Guides the process of bringing a new dataset into the mycelium framework:
places raw data, creates metadata, updates the manifest, and logs decisions.

Usage:
    python ingest_dataset.py --name DATASET_NAME --source SOURCE [--target-dir PATH]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Ingest a new dataset into the mycelium framework."
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name for the dataset (lowercase, hyphens, e.g., 'patient-cohort-2024')",
    )
    parser.add_argument(
        "--source",
        required=True,
        help="Description of where the data came from",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the repository (default: current directory)",
    )
    parser.add_argument(
        "--type",
        choices=["clinical", "genomic", "imaging", "survey", "simulation", "other"],
        default="other",
        help="Type of dataset",
    )
    return parser.parse_args()


def create_raw_directory(target_dir: Path, name: str, source: str):
    """Create the raw data directory with a README."""
    raw_dir = target_dir / "data" / "raw" / name
    raw_dir.mkdir(parents=True, exist_ok=True)

    readme_path = raw_dir / "README.md"
    # TODO: Generate README with source info, acquisition date, instructions
    print(f"  Created: data/raw/{name}/")
    print(f"  Would create README at: {readme_path}")
    print(f"  Source: {source}")
    print(f"  Place raw data files in: {raw_dir}")


def create_metadata(target_dir: Path, name: str, dataset_type: str):
    """Create the metadata directory and files."""
    meta_dir = target_dir / "data" / "metadata" / name
    meta_dir.mkdir(parents=True, exist_ok=True)

    # TODO: Create schema.yaml with column descriptions
    # TODO: Create provenance.md with full source details
    # TODO: Create summary_stats.md (to be filled after data is placed)
    print(f"  Created: data/metadata/{name}/")
    print(f"  Would create: schema.yaml, provenance.md, summary_stats.md")
    print(f"  Dataset type: {dataset_type}")


def update_data_manifest(target_dir: Path, name: str, dataset_type: str, source: str):
    """Add an entry to data/MANIFEST.md."""
    manifest_path = target_dir / "data" / "MANIFEST.md"
    # TODO: Read existing manifest
    # TODO: Append new entry using dataset-manifest-entry.yaml template
    # TODO: Fill in known fields (name, type, source, date, paths)
    # TODO: Leave unknown fields (rows, columns, size) for user to fill
    print(f"  Would update: {manifest_path}")
    print(f"  New entry: {name} (type: {dataset_type}, source: {source})")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Dataset Ingestion — {args.name}")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled repo.")
        print("Run init_repo.py first.")
        return

    print("\nCreating raw data directory...")
    create_raw_directory(target_dir, args.name, args.source)

    print("\nCreating metadata...")
    create_metadata(target_dir, args.name, args.type)

    print("\nUpdating data manifest...")
    update_data_manifest(target_dir, args.name, args.type, args.source)

    print("\n" + "=" * 50)
    print("Ingestion scaffold complete!")
    print("\nNext steps:")
    print(f"  1. Place raw data files in data/raw/{args.name}/")
    print(f"  2. Fill in data/metadata/{args.name}/schema.yaml")
    print(f"  3. Update data/metadata/{args.name}/summary_stats.md")
    print(f"  4. Review the manifest entry in data/MANIFEST.md")
    print(f"  5. Log any decisions to .living/decisions.md")


if __name__ == "__main__":
    main()
