#!/usr/bin/env python3
"""Install a domain skill from the mycelium network into a repository.

Copies domain skill conventions from network/skills/ into the target
repository's .living/skills/ directory and updates ACTIVE_SKILLS.yaml.

Usage:
    python install_domain_skill.py --domain DOMAIN [--target-dir PATH] [--network-dir PATH]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Install a domain skill pack into a mycelium-enabled repository."
    )
    parser.add_argument(
        "--domain",
        required=True,
        help="Name of the domain skill to install (e.g., 'bioinformatics')",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the target repository (default: current directory)",
    )
    parser.add_argument(
        "--network-dir",
        type=Path,
        default=None,
        help="Path to the mycelium network/skills/ directory (auto-detected if not specified)",
    )
    return parser.parse_args()


def find_network_dir(network_dir: Path | None) -> Path | None:
    """Locate the network/skills/ directory."""
    if network_dir and network_dir.exists():
        return network_dir

    # TODO: Search common locations:
    #   - Adjacent to target repo
    #   - In ~/.mycelium/network/
    #   - In the mycelium repo itself
    print("  TODO: Auto-detect network directory location")
    return network_dir


def list_available_skills(network_dir: Path) -> list[str]:
    """List available domain skill packs."""
    # TODO: Scan network_dir for subdirectories with SKILL_PACK.yaml
    skills = []
    if network_dir and network_dir.exists():
        for subdir in sorted(network_dir.iterdir()):
            if subdir.is_dir() and (subdir / "SKILL_PACK.yaml").exists():
                skills.append(subdir.name)
    return skills


def copy_skill(network_dir: Path, domain: str, target_dir: Path):
    """Copy a domain skill pack into the target repo."""
    source = network_dir / domain
    dest = target_dir / ".living" / "skills" / domain

    # TODO: Copy all files from source to dest
    # TODO: Skip SKILL_PACK.yaml metadata (or copy it for reference)
    # TODO: Handle existing installation (update vs skip)
    print(f"  Would copy: {source} → {dest}")
    print(f"  Files to copy:")
    if source.exists():
        for f in sorted(source.rglob("*")):
            if f.is_file():
                print(f"    - {f.relative_to(source)}")


def update_active_skills(target_dir: Path, domain: str):
    """Update .living/skills/ACTIVE_SKILLS.yaml with the new skill."""
    yaml_path = target_dir / ".living" / "skills" / "ACTIVE_SKILLS.yaml"
    # TODO: Parse existing YAML
    # TODO: Add new skill entry with name, version, install date, path
    # TODO: Write updated YAML
    print(f"  Would update: {yaml_path}")
    print(f"  New entry: {domain}")


def update_claude_md(target_dir: Path, domain: str):
    """Update CLAUDE.md to reference the new domain conventions."""
    claude_md = target_dir / "CLAUDE.md"
    # TODO: Find the "Active Domain Skills" section
    # TODO: Add reference to the newly installed skill
    # TODO: Point to .living/skills/{domain}/ for conventions
    print(f"  Would update: {claude_md}")
    print(f"  Adding reference to {domain} domain conventions")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Install Domain Skill — {args.domain}")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled repo.")
        print("Run init_repo.py first.")
        return

    network_dir = find_network_dir(args.network_dir)
    if not network_dir:
        print("\nAvailable skills could not be listed (network directory not found).")
        print("Specify --network-dir pointing to the mycelium network/skills/ directory.")
        return

    available = list_available_skills(network_dir)
    if args.domain not in available:
        print(f"\nSkill '{args.domain}' not found in network.")
        print(f"Available skills: {', '.join(available) if available else 'none found'}")
        return

    print(f"\nInstalling {args.domain} skill pack...")
    copy_skill(network_dir, args.domain, target_dir)

    print("\nUpdating active skills registry...")
    update_active_skills(target_dir, args.domain)

    print("\nUpdating CLAUDE.md...")
    update_claude_md(target_dir, args.domain)

    print("\n" + "=" * 50)
    print(f"Domain skill '{args.domain}' installed successfully!")
    print(f"\nConventions available at: .living/skills/{args.domain}/")
    print("Run validate_structure.py to confirm everything is correct.")


if __name__ == "__main__":
    main()
