#!/usr/bin/env python3
"""Install a convention pack from the mycelium network into a repository.

Copies convention pack files from network/conventions/ into the target
repository's .living/conventions/ directory and updates ACTIVE_CONVENTIONS.yaml.

Usage:
    python install_convention.py --name NAME [--target-dir PATH] [--network-dir PATH]
"""

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from init_repo import _atomic_write_text, ensure_safe_regular_file


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
        Path(__file__).resolve().parents[3] / "network" / "conventions",
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


# ACTIVE_CONVENTIONS.yaml is a bare top-level YAML list of pack entries:
#
#     - name: robust-analysis
#       path: .living/conventions/robust-analysis/
#       installed: 2026-04-01T20:22:21Z
#       core: true
#
# with `[]` standing for "no packs". Older init_repo.py seeded a mapping
# (`active_conventions: []`) and this installer appended list items under it,
# which is invalid YAML (#129). The helpers below read either legacy shape and
# always write the list. They work line by line so PyYAML stays optional, and
# refuse any other top-level content rather than guess at it.
REGISTRY_RELATIVE = Path(".living") / "conventions" / "ACTIVE_CONVENTIONS.yaml"
REGISTRY_HEADER = "# Active Convention Packs\n# Updated by init_repo.py and install_convention.py\n"
_LEGACY_KEY = re.compile(r"^active_conventions:\s*(\[\s*\])?\s*(#.*)?$")
_EMPTY_LIST = re.compile(r"^\[\s*\]\s*(#.*)?$")
_ENTRY_NAME = re.compile(r"""^-\s+name:\s*(?P<q>["']?)(?P<name>.*?)(?P=q)\s*(#.*)?$""")


class UnrecognizedRegistryError(ValueError):
    """ACTIVE_CONVENTIONS.yaml holds content outside both known shapes."""


def format_registry_entry(name: str, installed: str, core: bool = False) -> str:
    """Render one registry entry; the only place the entry shape is defined."""
    entry = (
        f"- name: {name}\n"
        f"  path: .living/conventions/{name}/\n"
        f"  installed: {installed}"
    )
    return entry + "\n  core: true" if core else entry


def _is_comment(line: str) -> bool:
    return line.lstrip().startswith("#")


def _registry_lines(text: str) -> list[str]:
    """Return the registry's lines in list shape, with no `[]` marker.

    Drops the legacy `active_conventions:` key line. When a bare key holds a
    nested block list, its items are dedented by the first item's indent;
    blank and comment lines inside that block do not end it. Raises
    UnrecognizedRegistryError for any other top-level content.
    """
    lines: list[str] = []
    nested_indent: int | None = None  # indent of the key block's first item
    in_key_block = False
    for line in text.splitlines():
        key = _LEGACY_KEY.match(line)
        if key:
            # Only a bare `active_conventions:` can own an indented block.
            in_key_block = key.group(1) is None
            nested_indent = None
            continue
        if _EMPTY_LIST.match(line):
            continue
        if in_key_block:
            if not line.strip() or _is_comment(line):
                lines.append(line.lstrip(" "))
                continue
            if line.startswith(" "):
                indent = len(line) - len(line.lstrip(" "))
                if nested_indent is None:
                    nested_indent = indent
                if indent < nested_indent:
                    raise UnrecognizedRegistryError(
                        f"inconsistent indentation under active_conventions: {line!r}"
                    )
                lines.append(line[nested_indent:])
                continue
            in_key_block = False
        if line.strip() and not line.startswith((" ", "- ", "#")):
            raise UnrecognizedRegistryError(
                f"unexpected top-level content: {line!r}"
            )
        lines.append(line)
    return lines


def _render_registry(lines: list[str]) -> str:
    """Join registry lines, writing `[]` when no entry remains."""
    lines = _strip_trailing_blank(lines)
    if not any(line.startswith("- ") for line in lines):
        lines.append("[]")
    return "\n".join(lines) + "\n"


def _strip_trailing_blank(lines: list[str]) -> list[str]:
    lines = list(lines)
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def normalize_active_conventions(text: str) -> str:
    """Rewrite ACTIVE_CONVENTIONS.yaml text into the canonical list shape.

    Raises UnrecognizedRegistryError when the text is neither shape.
    """
    return _render_registry(_registry_lines(text))


def registry_needs_repair(text: str) -> bool:
    """True when normalizing would change more than trailing blank lines."""
    normalized = normalize_active_conventions(text)
    return _strip_trailing_blank(normalized.splitlines()) != _strip_trailing_blank(
        text.splitlines()
    )


def read_registry_lines(target_dir: Path) -> list[str]:
    """Validate and read the registry; [] when it does not exist yet."""
    yaml_path = target_dir / REGISTRY_RELATIVE
    ensure_safe_regular_file(yaml_path)
    if not yaml_path.exists():
        return []
    return _registry_lines(yaml_path.read_text(encoding="utf-8"))


def update_active_conventions(target_dir: Path, name: str):
    """Update .living/conventions/ACTIVE_CONVENTIONS.yaml with the new convention."""
    yaml_path = target_dir / REGISTRY_RELATIVE
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Read existing content (repairing a legacy mapping shape) or start fresh
    existing_lines = read_registry_lines(target_dir)

    # Remove this convention's existing entry, matching the name exactly, and
    # carry its core flag over to the replacement.
    filtered = []
    skip = False
    was_core = False
    for line in existing_lines:
        match = _ENTRY_NAME.match(line)
        if match and match.group("name") == name:
            skip = True
            continue
        if skip and line.startswith("  "):
            if line.strip() == "core: true":
                was_core = True
            continue
        skip = False
        filtered.append(line)

    filtered = _strip_trailing_blank(filtered)
    entry = format_registry_entry(name, now, core=was_core)
    if not any(line.strip() for line in filtered):
        filtered = [REGISTRY_HEADER, entry]
    else:
        filtered.append(entry)

    _atomic_write_text(yaml_path, _render_registry(filtered))
    print(f"  Updated {yaml_path}")


def update_claude_md(target_dir: Path, name: str):
    """Update canonical guidance, falling back to legacy CLAUDE.md."""
    guidance_path = target_dir / "MYCELIUM.md"
    if not guidance_path.exists():
        guidance_path = target_dir / "CLAUDE.md"
    if not guidance_path.exists():
        print("  Skipping guidance update (MYCELIUM.md not found)")
        return

    content = guidance_path.read_text()
    conv_ref = f"- **{name}** — See `.living/conventions/{name}/analysis-conventions.md`"

    # Already referenced?
    if f".living/conventions/{name}/" in content:
        print(f"  {guidance_path.name} already references {name}")
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

    guidance_path.write_text(content)
    print(f"  Updated {guidance_path}")


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

    # Validate the registry before copying anything, so an unreadable or
    # unsafe registry cannot leave a pack installed but unregistered.
    try:
        read_registry_lines(target_dir)
    except ValueError as error:
        print(f"\nError: cannot update {REGISTRY_RELATIVE}: {error}")
        print("Fix the file by hand (it should be a YAML list of pack entries).")
        sys.exit(1)

    print(f"\nInstalling {args.name} convention pack...")
    copy_convention(network_dir, args.name, target_dir)

    print("\nUpdating active conventions registry...")
    update_active_conventions(target_dir, args.name)

    print("\nUpdating Mycelium guidance...")
    update_claude_md(target_dir, args.name)

    print("\n" + "=" * 50)
    print(f"Convention pack '{args.name}' installed successfully!")
    print(f"\nConventions available at: .living/conventions/{args.name}/")
    print("Run validate_structure.py to confirm everything is correct.")


if __name__ == "__main__":
    main()
