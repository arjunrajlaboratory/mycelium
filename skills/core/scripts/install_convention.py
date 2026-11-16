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

from init_repo import (
    _atomic_write_text,
    ensure_safe_project_directory,
    ensure_safe_regular_file,
)


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
# which is invalid YAML (#129). The helpers below drop that legacy key and
# otherwise edit the file line by line, so PyYAML stays optional and authored
# lines survive. When PyYAML is installed, every rewrite must load to the same
# entries as the original (plus or minus the one being installed), and is
# refused otherwise: the line rules are a convenience, the parser is the check.
REGISTRY_RELATIVE = Path(".living") / "conventions" / "ACTIVE_CONVENTIONS.yaml"
REGISTRY_HEADER = "# Active Convention Packs\n# Updated by init_repo.py and install_convention.py\n"
_LEGACY_KEY = re.compile(r"^active_conventions:\s*(\[\s*\])?\s*(#.*)?$")
_EMPTY_LIST = re.compile(r"^\[\s*\]\s*(?P<comment>#.*)?$")
_ENTRY_NAME = re.compile(
    r"""^-\s+name:\s*(?P<q>["']?)(?P<name>.*?)(?P=q)(?:\s+#.*)?\s*$"""
)
_DOC_START = re.compile(r"^---\s*(#.*)?$")
_DOC_END = re.compile(r"^\.\.\.\s*(#.*)?$")
_BOM = "﻿"


class UnrecognizedRegistryError(ValueError):
    """ACTIVE_CONVENTIONS.yaml holds content this module will not rewrite."""


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


def _is_entry_start(line: str) -> bool:
    """A top-level sequence item: `- key: value` or a bare `-` line."""
    return line.startswith("- ") or line.rstrip() == "-"


def has_legacy_key(text: str) -> bool:
    """True when the registry still carries the `active_conventions:` key."""
    return any(_LEGACY_KEY.match(line) for line in text.removeprefix(_BOM).splitlines())


def _registry_lines(text: str) -> list[str]:
    """Return the registry's lines in list shape, with no `[]` marker.

    Drops the legacy `active_conventions:` key line. A block list nested under
    that key, or a top-level list indented as a whole, is dedented by its
    first item's indent; blank and comment lines inside the block keep their
    indent relative to it. Raises UnrecognizedRegistryError for any other
    top-level content.
    """
    lines: list[str] = []
    nested_indent: int | None = None  # indent of the block's first item
    # The document's top level starts out like a key's block, so a list that
    # is indented as a whole is dedented the same way.
    in_key_block = True
    for line in text.removeprefix(_BOM).splitlines():
        if _DOC_END.match(line):
            # Entries appended after an end marker would leave the document.
            continue
        if _DOC_START.match(line):
            # Only comments may precede a document start marker.
            if any(earlier.strip() and not _is_comment(earlier) for earlier in lines):
                raise UnrecognizedRegistryError("multiple YAML documents")
            lines.append(line)
            continue
        key = _LEGACY_KEY.match(line)
        if key:
            # Only a bare `active_conventions:` can own an indented block.
            in_key_block = key.group(1) is None
            nested_indent = None
            continue
        empty = _EMPTY_LIST.match(line.strip() if in_key_block and nested_indent is None else line)
        if empty:
            if empty.group("comment"):
                lines.append(empty.group("comment"))
            continue
        if in_key_block:
            if not line.strip() or _is_comment(line):
                if nested_indent is not None and line[:nested_indent].isspace():
                    lines.append(line[nested_indent:])
                else:
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
        if line.strip() and not (
            line.startswith((" ", "#")) or _is_entry_start(line)
        ):
            raise UnrecognizedRegistryError(
                f"unexpected top-level content: {line!r}"
            )
        lines.append(line)
    return lines


def _render_registry(lines: list[str]) -> str:
    """Join registry lines, writing `[]` when no entry remains."""
    lines = list(lines)
    if not any(_is_entry_start(line) for line in lines):
        lines.append("[]")
    return "\n".join(lines) + "\n"


_UNPARSEABLE = object()


def _yaml():
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _entries_as_parsed(yaml, text: str):
    """The entry list YAML reads from either registry shape.

    Returns _UNPARSEABLE when the text is not valid YAML (the #129 shape),
    and raises UnrecognizedRegistryError for valid YAML in neither shape.
    """
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return _UNPARSEABLE
    if isinstance(value, dict) and set(value) == {"active_conventions"}:
        value = value["active_conventions"]
    if value is None:
        return []
    if not isinstance(value, list):
        raise UnrecognizedRegistryError("registry is not a list of entries")
    return value


def _check_rewrite(original: str, result: str, expected_change=None) -> None:
    """Refuse a rewrite that is not a list or changes entries it should not.

    `expected_change` maps the original entry list to the expected new one.
    A no-op when PyYAML is not installed.
    """
    yaml = _yaml()
    if yaml is None:
        return
    try:
        new = yaml.safe_load(result)
    except yaml.YAMLError as error:
        raise UnrecognizedRegistryError(f"rewrite would not be valid YAML: {error}")
    if not isinstance(new, list):
        raise UnrecognizedRegistryError("rewrite would not be a YAML list")
    old = _entries_as_parsed(yaml, original) if original.strip() else []
    if old is _UNPARSEABLE:
        return  # nothing to compare against; the result at least parses
    expected = expected_change(old) if expected_change else old
    if new != expected:
        raise UnrecognizedRegistryError("rewrite would change existing entries")


def normalize_active_conventions(text: str) -> str:
    """Rewrite ACTIVE_CONVENTIONS.yaml text into the canonical list shape.

    Raises UnrecognizedRegistryError when the text is in neither shape or the
    rewrite would not preserve its entries.
    """
    result = _render_registry(_registry_lines(text))
    _check_rewrite(text, result)
    return result


def repaired_registry_text(text: str) -> str | None:
    """Return the repaired text, or None when the file has no legacy key.

    Only the legacy `active_conventions:` shape is rewritten; every other file
    is left byte-identical. Raises UnrecognizedRegistryError as normalize does.
    """
    if not has_legacy_key(text):
        return None
    return normalize_active_conventions(text)


def read_registry_text(target_dir: Path) -> str:
    """Read the registry after checking its directory chain and file are safe.

    Returns "" when it does not exist yet.
    """
    ensure_safe_project_directory(target_dir, REGISTRY_RELATIVE.parent, create=False)
    yaml_path = target_dir / REGISTRY_RELATIVE
    ensure_safe_regular_file(yaml_path)
    if not yaml_path.exists():
        return ""
    return yaml_path.read_text(encoding="utf-8")


def preflight_registry(target_dir: Path) -> None:
    """Raise ValueError if update_active_conventions would refuse this repo."""
    text = read_registry_text(target_dir)
    if text:
        normalize_active_conventions(text)


def update_active_conventions(target_dir: Path, name: str):
    """Update .living/conventions/ACTIVE_CONVENTIONS.yaml with the new convention."""
    yaml_path = target_dir / REGISTRY_RELATIVE
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Read existing content (repairing a legacy mapping shape) or start fresh
    original = read_registry_text(target_dir)
    existing_lines = _registry_lines(original) if original else []

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

    entry = format_registry_entry(name, now, core=was_core)
    if not any(line.strip() for line in filtered):
        filtered = [REGISTRY_HEADER, entry]
    else:
        filtered.append(entry)

    result = _render_registry(filtered)

    def expected(old: list) -> list:
        kept = [e for e in old if not (isinstance(e, dict) and e.get("name") == name)]
        new_entry = {"name": name, "path": f".living/conventions/{name}/"}
        new_entry["installed"] = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if was_core:
            new_entry["core"] = True
        return kept + [new_entry]

    _check_rewrite(original, result, expected)
    _atomic_write_text(yaml_path, result)
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
        preflight_registry(target_dir)
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
