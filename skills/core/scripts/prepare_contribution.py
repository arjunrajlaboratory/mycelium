#!/usr/bin/env python3
"""Package a repo-local generated convention for PR to the mycelium network.

Takes a generated convention from .living/generated-conventions/ and prepares it
as a properly formatted convention pack suitable for a pull request.

Usage:
    python prepare_contribution.py --name CONVENTION_NAME [--target-dir PATH] [--output-dir PATH]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Package a generated convention for contribution to the mycelium network."
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name of the generated convention to package (from .living/generated-conventions/)",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the repository (default: current directory)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write the packaged convention (default: network/community-contributed/)",
    )
    return parser.parse_args()


def read_generated_convention(target_dir: Path, name: str) -> dict | None:
    """Read a generated convention and its origin document."""
    conv_dir = target_dir / ".living" / "generated-conventions" / name

    if not conv_dir.exists():
        print(f"  Error: Generated convention '{name}' not found")
        print(f"  Expected at: {conv_dir}")
        return None

    # TODO: Read convention.md
    # TODO: Read ORIGIN.md
    # TODO: Read any examples/
    # TODO: Return structured representation
    print(f"  Found generated convention at: {conv_dir}")
    print("  TODO: Parse generated convention content")
    return {"name": name, "path": conv_dir}


def generalize_convention(conv_data: dict) -> dict:
    """Generalize repo-specific details into parameters."""
    # TODO: Identify repo-specific references (paths, dataset names, etc.)
    # TODO: Replace with parameterized placeholders
    # TODO: Abstract away project-specific context
    # TODO: Keep the core convention intact
    print("  TODO: Generalize repo-specific details")
    print("  Would replace specific dataset/path references with parameters")
    return conv_data


def create_convention_pack(conv_data: dict, output_dir: Path):
    """Create a properly formatted convention pack."""
    pack_dir = output_dir / conv_data["name"]
    # TODO: Create CONVENTION_PACK.yaml with metadata
    # TODO: Write convention documents
    # TODO: Include templates if applicable
    # TODO: Include test cases derived from learnings
    print(f"  Would create convention pack at: {pack_dir}")
    print("  Would include: CONVENTION_PACK.yaml, convention docs, templates")


def generate_pr_description(conv_data: dict) -> str:
    """Generate a PR description with anonymized provenance."""
    # TODO: Create structured PR description
    # TODO: Include: what the convention covers, origin summary, test cases
    # TODO: Anonymize: remove repo-specific names, dates, personal info
    description = f"New convention pack: {conv_data['name']}"
    print(f"  Generated PR description ({len(description)} chars)")
    return description


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()
    output_dir = args.output_dir or (target_dir / "network" / "community-contributed")

    print(f"Prepare Contribution — {args.name}")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled repo.")
        print("Run init_repo.py first.")
        return

    print("\nReading generated convention...")
    conv_data = read_generated_convention(target_dir, args.name)
    if not conv_data:
        return

    print("\nGeneralizing for broader use...")
    conv_data = generalize_convention(conv_data)

    print("\nCreating convention pack...")
    create_convention_pack(conv_data, output_dir)

    print("\nGenerating PR description...")
    pr_desc = generate_pr_description(conv_data)

    print("\n" + "=" * 50)
    print("Contribution preparation complete!")
    print(f"\nConvention pack ready at: {output_dir / args.name}")
    print("\nNext steps:")
    print("  1. Review the generated convention pack for accuracy")
    print("  2. Test it in a fresh repo with install_convention.py")
    print("  3. Fork the mycelium network repository")
    print("  4. Copy the convention pack to network/community-contributed/")
    print("  5. Open a PR with the generated description")


if __name__ == "__main__":
    main()
