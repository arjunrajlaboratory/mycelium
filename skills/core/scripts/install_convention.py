#!/usr/bin/env python3
"""Install a convention pack from the mycelium network into a repository.

Copies convention pack files from network/conventions/ into the target
repository's .living/conventions/ directory and updates ACTIVE_CONVENTIONS.yaml.

Usage:
    python install_convention.py --name NAME [--target-dir PATH] [--network-dir PATH]
"""

import argparse
import json
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
# which is invalid YAML (#129).
#
# The file is machine-written, so this module reads exactly the grammar
# Mycelium's writers produce, and refuses anything else rather than guess:
#
#   - blank lines and `#` comment lines
#   - `[]` (optionally commented), meaning no packs
#   - the legacy `active_conventions:` / `active_conventions: []` key line
#   - an optional leading `---` and a trailing `...`
#   - an entry: `- key: value`, then `  key: value` field lines indented by
#     exactly two spaces, where every value is a one-line scalar (plain, or
#     a complete single- or double-quoted string)
#
# Anchors, aliases, tags, flow collections, block scalars, nested blocks and
# multi-line scalars are all refused. A line-based editor cannot rewrite those
# safely without a YAML parser, and PyYAML is optional here. When PyYAML is
# installed, every rewrite must also load to the expected entries, or it is
# refused (pattern 36 in skills/develop/references/regression-patterns.md).
REGISTRY_RELATIVE = Path(".living") / "conventions" / "ACTIVE_CONVENTIONS.yaml"
REGISTRY_HEADER = "# Active Convention Packs\n# Updated by init_repo.py and install_convention.py\n"
_BOM = "\ufeff"
# YAML 1.1 treats these as line breaks inside a scalar; Python's splitlines
# splits on even more. Refuse them so line structure means one thing.
_EXTRA_BREAKS = ("\x85", "\u2028", "\u2029")
_COMMENT = re.compile(r"^\s*#.*$")
_EMPTY_LIST = re.compile(r"^\[\s*\](?:\s+(?P<comment>#.*))?\s*$")
_LEGACY_KEY = re.compile(r"^active_conventions:(?:\s+\[\s*\])?(?:\s+#.*)?\s*$")
_DOC_START = re.compile(r"^---(?:\s+#.*)?\s*$")
_DOC_END = re.compile(r"^\.\.\.(?:\s+#.*)?\s*$")
_SCALAR = r"""(?:"(?P<dq>[^"\\]*)"|'(?P<sq>[^']*)'|(?P<plain>[A-Za-z0-9._/~+][^#]*?))"""
_FIELD = re.compile(
    rf"^(?P<key>[A-Za-z_][\w-]*):[ \t]+{_SCALAR}(?:[ \t]+#.*)?[ \t]*$"
)
_TRUE = {"true", "yes", "on"}  # the YAML 1.1 spellings safe_load reads as true


class UnrecognizedRegistryError(ValueError):
    """ACTIVE_CONVENTIONS.yaml holds content this module will not rewrite."""


# A name YAML reads back as this same plain string: starts with a letter, no
# special characters, and not one of the YAML 1.1 bool/null spellings.
_PLAIN_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_YAML_SPECIAL = {"y", "n", "yes", "no", "true", "false", "on", "off", "null"}


def _yaml_name(name: str) -> str:
    """Render a pack name so YAML loads it back as the same string."""
    if _PLAIN_NAME.match(name) and name.lower() not in _YAML_SPECIAL:
        return name
    return json.dumps(name)  # a JSON string is a valid YAML double-quoted scalar


def format_registry_entry(name: str, installed: str, core: bool = False) -> str:
    """Render one registry entry; the only place the entry shape is defined."""
    entry = (
        f"- name: {_yaml_name(name)}\n"
        f"  path: .living/conventions/{name}/\n"
        f"  installed: {installed}"
    )
    return entry + "\n  core: true" if core else entry


def _field(text: str, line: str) -> tuple[str, str, bool]:
    """Parse one `key: value` scalar, refusing anything outside the grammar."""
    match = _FIELD.match(text)
    if not match:
        raise UnrecognizedRegistryError(f"unsupported registry line: {line!r}")
    plain = match.group("plain")
    if plain is not None and (": " in plain or plain.endswith(":")):
        raise UnrecognizedRegistryError(f"unsupported registry line: {line!r}")
    value = next(
        group for group in (match.group("dq"), match.group("sq"), plain) if group is not None
    )
    return match.group("key"), value, plain is None


class _Entry:
    """One registry entry: its exact source lines and its own fields."""

    def __init__(self, line: str):
        self.lines = [line]
        key, value, quoted = _field(line[2:], line)
        self.fields = {key: value}
        self.quoted = {key} if quoted else set()

    def add_field(self, line: str) -> None:
        key, value, quoted = _field(line[2:], line)
        if key in self.fields:
            raise UnrecognizedRegistryError(f"duplicate field {key!r}: {line!r}")
        self.fields[key] = value
        if quoted:
            self.quoted.add(key)
        self.lines.append(line)

    @property
    def name(self) -> str | None:
        return self.fields.get("name")

    @property
    def core(self) -> bool:
        # A quoted "true" is a string, not the flag.
        if "core" in self.quoted:
            return False
        return self.fields.get("core", "").lower() in _TRUE


def _parse_registry(text: str) -> tuple[list, bool]:
    """Split registry text into items: str lines kept verbatim, and _Entry.

    Returns (items, had_legacy_key). The legacy key, `[]` markers and a
    trailing `...` are dropped (a `[]` comment is kept as a comment line).
    Raises UnrecognizedRegistryError for anything outside the grammar.
    """
    text = text.removeprefix(_BOM)
    if any(char in text for char in _EXTRA_BREAKS):
        raise UnrecognizedRegistryError("registry contains a Unicode line separator")
    raw_lines = [line.removesuffix("\r") for line in text.split("\n")]
    if raw_lines and raw_lines[-1] == "":
        raw_lines.pop()  # the final newline
    items: list = []
    pending: list[str] = []  # blank/comment lines not yet placed
    entry: _Entry | None = None  # entry that a following field line extends
    had_legacy_key = False
    seen_content = False
    ended = False
    for line in raw_lines:
        if not line.strip() or _COMMENT.match(line):
            pending.append(line)
            continue
        if ended:
            raise UnrecognizedRegistryError("content after the `...` end marker")
        if line.startswith("  ") and not line.startswith("   "):
            if entry is None:
                raise UnrecognizedRegistryError(f"field outside an entry: {line!r}")
            entry.lines.extend(pending)
            pending = []
            entry.add_field(line)
            continue
        items.extend(pending)
        pending = []
        entry = None
        if line.startswith("- "):
            entry = _Entry(line)
            items.append(entry)
        elif _LEGACY_KEY.match(line):
            had_legacy_key = True
        elif empty := _EMPTY_LIST.match(line):
            if empty.group("comment"):
                items.append(empty.group("comment"))
        elif _DOC_START.match(line) and not seen_content:
            items.append(line)
        elif _DOC_END.match(line):
            ended = True
        else:
            raise UnrecognizedRegistryError(f"unsupported registry line: {line!r}")
        seen_content = True
    items.extend(pending)
    return items, had_legacy_key


def _render_registry(items: list) -> str:
    """Join parsed items, writing `[]` when no entry remains."""
    lines: list[str] = []
    for item in items:
        lines.extend(item.lines if isinstance(item, _Entry) else [item])
    if not any(isinstance(item, _Entry) for item in items):
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
    A no-op when PyYAML is not installed; the grammar above is the guard then.
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

    Raises UnrecognizedRegistryError when the text is outside the grammar or
    the rewrite would not preserve its entries.
    """
    items, _ = _parse_registry(text)
    result = _render_registry(items)
    _check_rewrite(text, result)
    return result


def repaired_registry_text(text: str) -> str | None:
    """Return the repaired text, or None when the file needs no repair.

    Only a file carrying the legacy `active_conventions:` key line is
    rewritten; every other file is left byte-identical. Raises
    UnrecognizedRegistryError when a legacy file is outside the grammar.
    """
    if not any(_LEGACY_KEY.match(line) for line in text.removeprefix(_BOM).split("\n")):
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


def _plan_update(target_dir: Path, name: str, now: str) -> str:
    """Build the registry text that installing `name` would write.

    Raises ValueError (UnrecognizedRegistryError, or an unsafe-path error)
    when the update would be refused, without writing anything.
    """
    original = read_registry_text(target_dir)
    items, _ = _parse_registry(original) if original else ([], False)

    # Remove this convention's existing entry (only its own lines), matching
    # the name exactly, and carry its core flag over to the replacement.
    was_core = any(
        isinstance(item, _Entry) and item.name == name and item.core for item in items
    )
    kept = [item for item in items if not (isinstance(item, _Entry) and item.name == name)]

    entry = _Entry.__new__(_Entry)
    entry.lines = format_registry_entry(name, now, core=was_core).split("\n")
    entry.fields = {}
    if not any(isinstance(item, _Entry) or item.strip() for item in kept):
        kept = [REGISTRY_HEADER, entry]
    else:
        kept.append(entry)
    result = _render_registry(kept)

    def expected(old: list) -> list:
        remaining = [e for e in old if not (isinstance(e, dict) and e.get("name") == name)]
        new_entry = {"name": name, "path": f".living/conventions/{name}/"}
        new_entry["installed"] = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        if was_core:
            new_entry["core"] = True
        return remaining + [new_entry]

    _check_rewrite(original, result, expected)
    return result


def preflight_update(target_dir: Path, name: str) -> None:
    """Raise ValueError if update_active_conventions would refuse `name`."""
    _plan_update(target_dir, name, _utc_now())


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def update_active_conventions(target_dir: Path, name: str):
    """Update .living/conventions/ACTIVE_CONVENTIONS.yaml with the new convention."""
    result = _plan_update(target_dir, name, _utc_now())
    yaml_path = target_dir / REGISTRY_RELATIVE
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
        preflight_update(target_dir, args.name)
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
