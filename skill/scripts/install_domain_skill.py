#!/usr/bin/env python3
"""Install a domain skill from the mycelium network into a repository.

Copies domain skill conventions from network/skills/ into the target
repository's .living/skills/ directory and updates ACTIVE_SKILLS.yaml.

Usage:
    python install_domain_skill.py --domain DOMAIN [--target-dir PATH] [--network-dir PATH]
"""

import argparse
import shutil
import sys
from datetime import datetime, timezone
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

    # Search common locations
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "network" / "skills",
        Path.home() / ".mycelium" / "network" / "skills",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def list_available_skills(network_dir: Path) -> list[str]:
    """List available domain skill packs."""
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

    if dest.exists():
        print(f"  Updating existing installation at {dest}")
        shutil.rmtree(dest)

    shutil.copytree(source, dest)

    copied = [f for f in sorted(dest.rglob("*")) if f.is_file()]
    print(f"  Copied {len(copied)} files to {dest}")
    for f in copied:
        print(f"    - {f.relative_to(dest)}")


def update_active_skills(target_dir: Path, domain: str):
    """Update .living/skills/ACTIVE_SKILLS.yaml with the new skill."""
    yaml_path = target_dir / ".living" / "skills" / "ACTIVE_SKILLS.yaml"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Read existing content or start fresh
    existing_lines = []
    if yaml_path.exists():
        existing_lines = yaml_path.read_text().splitlines()

    # Check if this domain already has an entry and remove it
    filtered = []
    skip = False
    for line in existing_lines:
        if line.strip().startswith(f"- name: {domain}"):
            skip = True
            continue
        if skip and line.startswith("  "):
            continue
        skip = False
        filtered.append(line)

    # Add the new entry
    entry = (
        f"- name: {domain}\n"
        f"  path: .living/skills/{domain}/\n"
        f"  installed: {now}"
    )

    if not filtered or filtered == [""]:
        filtered = ["# Active domain skills", entry]
    else:
        filtered.append(entry)

    yaml_path.write_text("\n".join(filtered) + "\n")
    print(f"  Updated {yaml_path}")


def update_claude_md(target_dir: Path, domain: str):
    """Update CLAUDE.md to reference the new domain conventions."""
    claude_md = target_dir / "CLAUDE.md"
    if not claude_md.exists():
        print(f"  Skipping CLAUDE.md update (file not found)")
        return

    content = claude_md.read_text()
    skill_ref = f"- [{domain}](.living/skills/{domain}/)"

    # Already referenced?
    if skill_ref in content:
        print(f"  CLAUDE.md already references {domain}")
        return

    # Insert into Active Domain Skills section if it exists
    section_header = "## Active Domain Skills"
    if section_header in content:
        content = content.replace(
            section_header,
            f"{section_header}\n\n{skill_ref}",
        )
    else:
        content += f"\n\n{section_header}\n\n{skill_ref}\n"

    claude_md.write_text(content)
    print(f"  Updated {claude_md}")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Install Domain Skill — {args.domain}")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled project.")
        print("Run init_repo.py first.")
        sys.exit(1)

    network_dir = find_network_dir(args.network_dir)
    if not network_dir:
        print("\nAvailable skills could not be listed (network directory not found).")
        print("Specify --network-dir pointing to the mycelium network/skills/ directory.")
        sys.exit(1)

    available = list_available_skills(network_dir)
    if args.domain not in available:
        print(f"\nSkill '{args.domain}' not found in network.")
        print(f"Available skills: {', '.join(available) if available else 'none found'}")
        sys.exit(1)

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
