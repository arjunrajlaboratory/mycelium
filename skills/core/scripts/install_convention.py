#!/usr/bin/env python3
"""Install a convention pack from the mycelium network into a repository.

Copies convention pack files from network/conventions/ into the target
repository's .living/conventions/ directory and updates ACTIVE_CONVENTIONS.yaml.

Usage:
    python install_convention.py --name NAME [--target-dir PATH] [--network-dir PATH]
"""

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Install a convention pack into a mycelium-enabled repository."
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Name of the convention pack to install (e.g., 'bioinformatics')",
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
        help="Path to the mycelium network/conventions/ directory (auto-detected if not specified)",
    )
    return parser.parse_args()


def find_network_dir(network_dir: Path | None) -> Path | None:
    """Locate the network/conventions/ directory."""
    if network_dir and network_dir.exists():
        return network_dir

    # Search common locations
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "network" / "conventions",
        Path.home() / ".mycelium" / "network" / "conventions",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


def list_available_conventions(network_dir: Path) -> list[str]:
    """List available convention packs."""
    conventions = []
    if network_dir and network_dir.exists():
        for subdir in sorted(network_dir.iterdir()):
            if subdir.is_dir() and (subdir / "CONVENTION_PACK.yaml").exists():
                conventions.append(subdir.name)
    return conventions


def copy_convention(network_dir: Path, name: str, target_dir: Path):
    """Copy a convention pack into the target repo."""
    source = network_dir / name
    dest = target_dir / ".living" / "conventions" / name

    if dest.exists():
        print(f"  Updating existing installation at {dest}")
        shutil.rmtree(dest)

    shutil.copytree(source, dest)

    copied = [f for f in sorted(dest.rglob("*")) if f.is_file()]
    print(f"  Copied {len(copied)} files to {dest}")
    for f in copied:
        print(f"    - {f.relative_to(dest)}")


def update_active_conventions(target_dir: Path, name: str):
    """Update .living/conventions/ACTIVE_CONVENTIONS.yaml with the new convention."""
    yaml_path = target_dir / ".living" / "conventions" / "ACTIVE_CONVENTIONS.yaml"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Read existing content or start fresh
    existing_lines = []
    if yaml_path.exists():
        existing_lines = yaml_path.read_text().splitlines()

    # Check if this convention already has an entry and remove it
    filtered = []
    skip = False
    for line in existing_lines:
        if line.strip().startswith(f"- name: {name}"):
            skip = True
            continue
        if skip and line.startswith("  "):
            continue
        skip = False
        filtered.append(line)

    # Add the new entry
    entry = (
        f"- name: {name}\n"
        f"  path: .living/conventions/{name}/\n"
        f"  installed: {now}"
    )

    if not filtered or filtered == [""]:
        filtered = ["# Active convention packs", entry]
    else:
        filtered.append(entry)

    yaml_path.write_text("\n".join(filtered) + "\n")
    print(f"  Updated {yaml_path}")


def update_claude_md(target_dir: Path, name: str):
    """Update CLAUDE.md to reference the new convention pack."""
    claude_md = target_dir / "CLAUDE.md"
    if not claude_md.exists():
        print("  Skipping CLAUDE.md update (file not found)")
        return

    content = claude_md.read_text()
    conv_ref = f"- **{name}** — See `.living/conventions/{name}/analysis-conventions.md`"

    # Already referenced?
    if f".living/conventions/{name}/" in content:
        print(f"  CLAUDE.md already references {name}")
        return

    # Try the new template format first: "### Domain (opt-in)" subsection
    domain_subsection = "### Domain (opt-in)"
    no_conventions_placeholder = "No domain conventions installed yet."
    # Also support legacy format
    legacy_header = "## Active Domain Skills"
    legacy_header_2 = "## Installed Convention Packs"

    if domain_subsection in content:
        if no_conventions_placeholder in content:
            content = content.replace(no_conventions_placeholder, conv_ref)
        else:
            content = content.replace(
                domain_subsection,
                f"{domain_subsection}\n\n{conv_ref}",
            )
    elif legacy_header in content:
        content = content.replace(
            legacy_header,
            f"{legacy_header}\n\n{conv_ref}",
        )
    elif legacy_header_2 in content:
        content = content.replace(
            legacy_header_2,
            f"{legacy_header_2}\n\n{conv_ref}",
        )
    else:
        content += f"\n\n## Installed Convention Packs\n\n{conv_ref}\n"

    claude_md.write_text(content)
    print(f"  Updated {claude_md}")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Install Convention Pack — {args.name}")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled project.")
        print("Run init_repo.py first.")
        sys.exit(1)

    network_dir = find_network_dir(args.network_dir)
    if not network_dir:
        print("\nAvailable conventions could not be listed (network directory not found).")
        print("Specify --network-dir pointing to the mycelium network/conventions/ directory.")
        sys.exit(1)

    available = list_available_conventions(network_dir)
    if args.name not in available:
        print(f"\nConvention pack '{args.name}' not found in network.")
        print(f"Available packs: {', '.join(available) if available else 'none found'}")
        sys.exit(1)

    print(f"\nInstalling {args.name} convention pack...")
    copy_convention(network_dir, args.name, target_dir)

    print("\nUpdating active conventions registry...")
    update_active_conventions(target_dir, args.name)

    print("\nUpdating CLAUDE.md...")
    update_claude_md(target_dir, args.name)

    print("\n" + "=" * 50)
    print(f"Convention pack '{args.name}' installed successfully!")
    print(f"\nConventions available at: .living/conventions/{args.name}/")
    print("Run validate_structure.py to confirm everything is correct.")


if __name__ == "__main__":
    main()
