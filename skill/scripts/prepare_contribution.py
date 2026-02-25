#!/usr/bin/env python3
"""Package a repo-local generated skill for PR to the mycelium network.

Takes a generated skill from .living/generated-skills/ and prepares it
as a properly formatted skill pack suitable for a pull request.

Usage:
    python prepare_contribution.py --skill SKILL_NAME [--target-dir PATH] [--output-dir PATH]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Package a generated skill for contribution to the mycelium network."
    )
    parser.add_argument(
        "--skill",
        required=True,
        help="Name of the generated skill to package (from .living/generated-skills/)",
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
        help="Where to write the packaged skill (default: network/community-contributed/)",
    )
    return parser.parse_args()


def read_generated_skill(target_dir: Path, skill_name: str) -> dict | None:
    """Read a generated skill and its origin document."""
    skill_dir = target_dir / ".living" / "generated-skills" / skill_name

    if not skill_dir.exists():
        print(f"  Error: Generated skill '{skill_name}' not found")
        print(f"  Expected at: {skill_dir}")
        return None

    # TODO: Read convention.md
    # TODO: Read ORIGIN.md
    # TODO: Read any examples/
    # TODO: Return structured representation
    print(f"  Found generated skill at: {skill_dir}")
    print("  TODO: Parse generated skill content")
    return {"name": skill_name, "path": skill_dir}


def generalize_skill(skill_data: dict) -> dict:
    """Generalize repo-specific details into parameters."""
    # TODO: Identify repo-specific references (paths, dataset names, etc.)
    # TODO: Replace with parameterized placeholders
    # TODO: Abstract away project-specific context
    # TODO: Keep the core convention intact
    print("  TODO: Generalize repo-specific details")
    print("  Would replace specific dataset/path references with parameters")
    return skill_data


def create_skill_pack(skill_data: dict, output_dir: Path):
    """Create a properly formatted skill pack."""
    pack_dir = output_dir / skill_data["name"]
    # TODO: Create SKILL_PACK.yaml with metadata
    # TODO: Write convention documents
    # TODO: Include templates if applicable
    # TODO: Include test cases derived from learnings
    print(f"  Would create skill pack at: {pack_dir}")
    print("  Would include: SKILL_PACK.yaml, convention docs, templates")


def generate_pr_description(skill_data: dict) -> str:
    """Generate a PR description with anonymized provenance."""
    # TODO: Create structured PR description
    # TODO: Include: what the skill covers, origin summary, test cases
    # TODO: Anonymize: remove repo-specific names, dates, personal info
    description = f"New domain skill: {skill_data['name']}"
    print(f"  Generated PR description ({len(description)} chars)")
    return description


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()
    output_dir = args.output_dir or (target_dir / "network" / "community-contributed")

    print(f"Prepare Contribution — {args.skill}")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled repo.")
        print("Run init_repo.py first.")
        return

    print("\nReading generated skill...")
    skill_data = read_generated_skill(target_dir, args.skill)
    if not skill_data:
        return

    print("\nGeneralizing for broader use...")
    skill_data = generalize_skill(skill_data)

    print("\nCreating skill pack...")
    create_skill_pack(skill_data, output_dir)

    print("\nGenerating PR description...")
    pr_desc = generate_pr_description(skill_data)

    print("\n" + "=" * 50)
    print("Contribution preparation complete!")
    print(f"\nSkill pack ready at: {output_dir / args.skill}")
    print("\nNext steps:")
    print("  1. Review the generated skill pack for accuracy")
    print("  2. Test it in a fresh repo with install_domain_skill.py")
    print("  3. Fork the mycelium network repository")
    print("  4. Copy the skill pack to network/community-contributed/")
    print("  5. Open a PR with the generated description")


if __name__ == "__main__":
    main()
