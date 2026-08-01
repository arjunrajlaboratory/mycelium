#!/usr/bin/env python3
"""Bootstrap the provider-neutral ~/.mycelium/knowledge/ directory.

Creates one Markdown file per domain defined in
skills/core/templates/knowledge/domains.yaml, using the header template.
Existing files are never overwritten.
"""

import argparse
import os
import shutil
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# YAML parsing (with graceful fallback if PyYAML is not installed)
# ---------------------------------------------------------------------------


def _load_yaml(text: str) -> dict:
    """Parse YAML text, falling back to a minimal hand-written parser."""
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text)
    except ImportError:
        return _minimal_yaml_parse(text)


def _minimal_yaml_parse(text: str) -> dict:
    """Best-effort YAML parser for the known domains.yaml structure.

    Handles only the subset used by domains.yaml:
      - top-level ``domains:`` key containing a list of mappings
      - simple scalar values (strings, booleans)
    """
    domains = []
    current: dict | None = None

    for raw_line in text.splitlines():
        # Strip comments
        line = raw_line.split("#")[0].rstrip()
        if not line.strip():
            continue

        # Start of a new list item
        if line.lstrip().startswith("- "):
            if current is not None:
                domains.append(current)
            current = {}
            rest = line.lstrip()[2:]
            if ":" in rest:
                key, _, val = rest.partition(":")
                current[key.strip()] = _parse_scalar(val.strip())
            continue

        # Continuation key inside a list item
        if current is not None and ":" in line:
            key, _, val = line.partition(":")
            stripped_key = key.strip()
            if stripped_key and not stripped_key.startswith("-"):
                current[stripped_key] = _parse_scalar(val.strip())

    if current is not None:
        domains.append(current)

    return {"domains": domains}


def _parse_scalar(raw: str):
    """Convert a raw YAML scalar string to a Python value."""
    # Strip surrounding quotes
    raw = raw.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    return raw


# ---------------------------------------------------------------------------
# Domain file content builders
# ---------------------------------------------------------------------------

SKILLS_MD_CONTENT = """\
# Skills

> **When to read:** When starting a task, considering which tool/skill to invoke, or when the mycelium system needs self-maintenance

---

## Mycelium System Skills (self-maintaining)

| Skill | Trigger | What it does |
|-------|---------|-------------|
| mycelium | "set up mycelium", "initialize living repo", "crystallize learnings" | Scaffolds/maintains living repository framework |

<!-- Additional skills will be populated by the weekly audit's skills sync step -->
"""


def _build_standard_content(title: str, trigger: str, header_template: str) -> str:
    return header_template.replace("{{DOMAIN_TITLE}}", title).replace(
        "{{TRIGGER_DESCRIPTION}}", trigger
    )


# ---------------------------------------------------------------------------
# Audit support files
# ---------------------------------------------------------------------------


def _today_str() -> str:
    """Return today's date as YYYY-MM-DD using the real wall clock."""
    import datetime

    return datetime.date.today().isoformat()


AUDIT_LOG_TEMPLATE = """\
# Knowledge Audit Log

> Append-only. Rotated at 50 entries (older moved to .audit-log-archive.md).

---

### [{today}] Initial setup
**Action:** Created knowledge directory with domain files from templates
**Result:** System initialized, first audit scheduled in 7 days
"""


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


MEMORY_ROUTING_HEADER = "## Global Knowledge Domains"


def _glob_memory_files(claude_projects_dir: Path) -> list[Path]:
    """Find all `~/.claude/projects/*/memory/MEMORY.md` files."""
    _reject_symlink_components(claude_projects_dir)
    if not claude_projects_dir.is_dir():
        return []
    memory_files = sorted(claude_projects_dir.glob("*/memory/MEMORY.md"))
    # Validate the complete set before the caller modifies its first file.
    for memory_path in memory_files:
        _reject_symlink_components(memory_path)
        if not memory_path.is_file():
            raise ValueError(f"MEMORY path is not a regular file: {memory_path}")
    return memory_files


def _append_routing_table(memory_path: Path, table_text: str) -> bool:
    """Append the routing table to MEMORY.md if not already present.

    Returns True if appended, False if header already present (no-op).
    Always idempotent — re-running on a populated MEMORY.md is a no-op.
    """
    _reject_symlink_components(memory_path)
    if not memory_path.exists():
        return False
    if not memory_path.is_file():
        raise ValueError(f"MEMORY path is not a regular file: {memory_path}")
    existing = memory_path.read_text(encoding="utf-8")
    if MEMORY_ROUTING_HEADER in existing:
        return False

    # Ensure exactly one blank line between existing content and the table
    sep = "\n\n" if existing and not existing.endswith("\n\n") else ""
    if existing and not existing.endswith("\n"):
        sep = "\n\n"
    memory_path.write_text(existing + sep + table_text.lstrip("\n"), encoding="utf-8")
    return True


def append_routing_to_memory_files(
    mycelium_root: Path,
    claude_projects_dir: Path | None = None,
) -> tuple[int, int]:
    """Append the Global Knowledge Domains routing table to all MEMORY.md files.

    Skips files that already contain the routing header.

    Returns (appended_count, skipped_count).
    """
    if claude_projects_dir is None:
        claude_projects_dir = Path.home() / ".claude" / "projects"

    table_path = (
        mycelium_root / "skills" / "core" / "templates" / "knowledge" / "domain-table.md"
    )
    if not table_path.exists():
        raise FileNotFoundError(f"domain-table.md not found at {table_path}")
    table_text = table_path.read_text(encoding="utf-8")

    appended = 0
    skipped = 0
    for memory_path in _glob_memory_files(claude_projects_dir):
        if _append_routing_table(memory_path, table_text):
            appended += 1
        else:
            skipped += 1
    return appended, skipped


def _reject_symlink_components(path: Path) -> None:
    """Reject any existing symlink in a path without resolving through it."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Refusing symlinked managed path: {current}")


def migrate_legacy_knowledge(legacy_dir: Path, knowledge_dir: Path) -> int:
    """Copy missing files from the pre-v0.4 Claude-specific knowledge store."""
    _reject_symlink_components(legacy_dir)
    _reject_symlink_components(knowledge_dir)
    if not legacy_dir.is_dir() or legacy_dir.resolve() == knowledge_dir.resolve():
        return 0

    if knowledge_dir.exists() and not knowledge_dir.is_dir():
        raise ValueError(f"Knowledge path is not a directory: {knowledge_dir}")

    pending: list[tuple[Path, Path]] = []
    for source in legacy_dir.iterdir():
        _reject_symlink_components(source)
        if not source.is_file():
            continue
        destination = knowledge_dir / source.name
        _reject_symlink_components(destination)
        if destination.exists():
            if not destination.is_file():
                raise ValueError(
                    f"Knowledge destination is not a regular file: {destination}"
                )
            continue
        pending.append((source, destination))

    # Preflight the complete copy set before creating the destination or
    # importing any content, avoiding partial migrations on a later rejection.
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source, destination in pending:
        shutil.copy2(source, destination)
        copied += 1
    return copied


def init_knowledge(knowledge_dir: Path, mycelium_root: Path) -> None:
    templates_dir = mycelium_root / "skills" / "core" / "templates" / "knowledge"
    domains_yaml_path = templates_dir / "domains.yaml"
    header_template_path = templates_dir / "domain-header.md"

    # --- Load templates ---------------------------------------------------
    if not domains_yaml_path.exists():
        raise FileNotFoundError(f"domains.yaml not found at {domains_yaml_path}")
    if not header_template_path.exists():
        raise FileNotFoundError(f"domain-header.md not found at {header_template_path}")

    domains_data = _load_yaml(domains_yaml_path.read_text())
    header_template = header_template_path.read_text()

    domains: list[dict] = domains_data.get("domains", [])

    # Validate every managed destination before creating or changing any of
    # them. This prevents a later linked entry from producing a partial init.
    _reject_symlink_components(knowledge_dir)
    if knowledge_dir.exists() and not knowledge_dir.is_dir():
        raise ValueError(f"Knowledge path is not a directory: {knowledge_dir}")
    domain_paths = [
        knowledge_dir / f"{domain.get('name', '')}.md"
        for domain in domains
        if domain.get("name", "")
    ]
    managed_paths = domain_paths + [
        knowledge_dir / ".last-audit",
        knowledge_dir / ".audit-log.md",
    ]
    for managed_path in managed_paths:
        _reject_symlink_components(managed_path)
        if managed_path.exists() and not managed_path.is_file():
            raise ValueError(
                f"Knowledge destination is not a regular file: {managed_path}"
            )

    # --- Create knowledge directory ----------------------------------------
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    created = 0
    skipped = 0

    for domain in domains:
        name: str = domain.get("name", "")
        title: str = domain.get("title", name)
        trigger: str = domain.get("trigger", "")
        fmt: str = domain.get("format", "")

        if not name:
            continue

        dest = knowledge_dir / f"{name}.md"

        if dest.exists():
            skipped += 1
            continue

        if fmt == "table":
            content = SKILLS_MD_CONTENT
        else:
            content = _build_standard_content(title, trigger, header_template)

        dest.write_text(content)
        created += 1

    # --- .last-audit ------------------------------------------------------
    last_audit_path = knowledge_dir / ".last-audit"
    if not last_audit_path.exists():
        timestamp = int(time.time())
        last_audit_path.write_text(f"{timestamp} initial-setup\n")

    # --- .audit-log.md ----------------------------------------------------
    audit_log_path = knowledge_dir / ".audit-log.md"
    if not audit_log_path.exists():
        today = _today_str()
        audit_log_path.write_text(AUDIT_LOG_TEMPLATE.format(today=today))

    total = created + skipped
    print(f"{created} created, {skipped} skipped (already exist), {total} total")

    # --- Append routing table to MEMORY.md files --------------------------
    try:
        appended, mem_skipped = append_routing_to_memory_files(mycelium_root)
        mem_total = appended + mem_skipped
        print(
            f"MEMORY.md: appended {appended}, skipped {mem_skipped} (already present), "
            f"{mem_total} files scanned"
        )
    except FileNotFoundError as exc:
        print(f"Warning: skipping MEMORY.md routing append ({exc})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _auto_detect_mycelium_root(script_path: Path) -> Path:
    """Resolve ../../.. from the script's location."""
    return (script_path.parent / ".." / ".." / "..").resolve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap ~/.mycelium/knowledge/ from mycelium domain templates."
    )
    parser.add_argument(
        "--knowledge-dir",
        type=Path,
        default=Path.home() / ".mycelium" / "knowledge",
        help="Directory to create domain knowledge files in (default: ~/.mycelium/knowledge/)",
    )
    parser.add_argument(
        "--mycelium-root",
        type=Path,
        default=None,
        help=(
            "Root of the mycelium repository (default: auto-detect as ../../../ "
            "relative to this script)"
        ),
    )
    parser.add_argument(
        "--memory-only",
        action="store_true",
        help=(
            "Only append the routing table to existing MEMORY.md files. "
            "Skip the domain file creation step. Used by the migrator."
        ),
    )
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=None,
        help=(
            "Override the Claude Code projects directory "
            "(default: ~/.claude/projects/). Mostly for testing."
        ),
    )
    args = parser.parse_args()

    # Keep user-controlled paths lexical so validation can still see links.
    knowledge_dir = Path(os.path.abspath(args.knowledge_dir.expanduser()))

    mycelium_root: Path
    if args.mycelium_root is None:
        mycelium_root = _auto_detect_mycelium_root(Path(__file__).resolve())
    else:
        mycelium_root = args.mycelium_root.expanduser().resolve()

    projects_dir = (
        Path(os.path.abspath(args.projects_dir.expanduser()))
        if args.projects_dir
        else None
    )

    if args.memory_only:
        appended, skipped = append_routing_to_memory_files(
            mycelium_root, claude_projects_dir=projects_dir
        )
        total = appended + skipped
        print(
            f"MEMORY.md: appended {appended}, skipped {skipped} (already present), "
            f"{total} files scanned"
        )
    else:
        migrated = migrate_legacy_knowledge(
            Path.home() / ".claude" / "knowledge", knowledge_dir
        )
        if migrated:
            print(f"Migrated {migrated} legacy knowledge files into {knowledge_dir}")
        init_knowledge(knowledge_dir, mycelium_root)


if __name__ == "__main__":
    main()
