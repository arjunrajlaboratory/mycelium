#!/usr/bin/env python3
"""Initialize a mycelium-enabled living repository.

Scaffolds the directory structure, manifests, and living layer
for a new or existing repository. Creates all required directories,
empty manifests, and the .living/ memory layer.

Usage:
    python init_repo.py [--target-dir PATH] [--restructure]
"""

import argparse
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scaffold a mycelium-enabled living repository."
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the repository to initialize (default: current directory)",
    )
    parser.add_argument(
        "--restructure",
        action="store_true",
        help="Restructure an existing repo instead of creating from scratch",
    )
    return parser.parse_args()


def check_existing_structure(target_dir: Path) -> bool:
    """Check if the target directory already has a mycelium structure."""
    living_dir = target_dir / ".living"
    if living_dir.exists():
        print(f"Found existing .living/ directory at {living_dir}")
        return True
    return False


def create_directory_structure(target_dir: Path):
    """Create the canonical mycelium directory structure."""
    directories = [
        ".living",
        ".living/conventions",
        ".living/generated-conventions",
        ".living/log",
        "algorithms",
        "analysis",
        "data",
        "data/raw",
        "data/processed",
        "data/metadata",
        "reference_material",
        "todo",
    ]

    for dir_name in directories:
        dir_path = target_dir / dir_name
        dir_path.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {dir_name}/")


def dir_to_manifest_name(dir_name: str) -> str:
    """Convert a directory name to its manifest filename.

    E.g., 'analysis' -> 'ANALYSIS_MANIFEST.md', 'reference_material' -> 'REFERENCE_MANIFEST.md'
    """
    prefix = dir_name.upper().replace("-", "_")
    # Use singular form for readability
    singular = {
        "ALGORITHMS": "ALGORITHM",
        "REFERENCE_MATERIAL": "REFERENCE",
    }
    prefix = singular.get(prefix, prefix)
    return f"{prefix}_MANIFEST.md"


def create_manifests(target_dir: Path):
    """Create descriptive manifest files in each top-level directory."""
    manifest_dirs = ["algorithms", "analysis", "data", "reference_material"]

    for dir_name in manifest_dirs:
        manifest_filename = dir_to_manifest_name(dir_name)
        manifest_path = target_dir / dir_name / manifest_filename
        if not manifest_path.exists():
            manifest_path.write_text(
                f"# {dir_name.replace('_', ' ').title()} Manifest\n\n"
                "<!-- Add entries below using the appropriate manifest entry template. -->\n"
            )
            print(f"  Created: {dir_name}/{manifest_filename}")


def create_todo_list(target_dir: Path):
    """Create todo/TODOLIST.md for tracking future work items."""
    todolist_path = target_dir / "todo" / "TODOLIST.md"
    if not todolist_path.exists():
        todolist_path.write_text(
            "# Todo List\n\n"
            "Master list of future work items. Each item can have a detailed writeup\n"
            "in a separate `.md` file in this directory.\n\n"
            "## Items\n\n"
            "<!-- Add todo items below. Link to detailed writeups as needed. -->\n"
        )
        print("  Created: todo/TODOLIST.md")


def create_living_layer(target_dir: Path):
    """Initialize the .living/ memory layer with empty files."""
    living_dir = target_dir / ".living"

    files = {
        "decisions.md": (
            "# Decision Log\n\n"
            "Append-only log of non-obvious decisions and their rationale.\n\n"
            "<!-- Use the decision-log-entry template for new entries. -->\n"
        ),
        "learnings.md": (
            "# Learnings\n\n"
            "Append-only log of gotchas, surprises, and insights.\n\n"
            "<!-- Use the learning-entry template for new entries. -->\n"
        ),
        "conventions.md": (
            "# Repo-Specific Conventions\n\n"
            "Overrides to mycelium defaults or convention pack conventions.\n\n"
            "<!-- Document any project-specific convention overrides here. -->\n"
        ),
    }

    for filename, content in files.items():
        file_path = living_dir / filename
        if not file_path.exists():
            file_path.write_text(content)
            print(f"  Created: .living/{filename}")

    # Session log registry
    registry_path = living_dir / "log" / "LOG_REGISTRY.md"
    if not registry_path.exists():
        registry_path.write_text(
            "# Session Log Registry\n\n"
            "| Date | Session ID | Project | Branch | Duration | Files Changed "
            "| Summary | Key Outputs | Status | Tags | Log |\n"
            "|------|-----------|---------|--------|----------|---------------"
            "|---------|-------------|--------|------|-----|\n"
        )
        print("  Created: .living/log/LOG_REGISTRY.md")

    # Create ACTIVE_CONVENTIONS.yaml
    conventions_yaml = living_dir / "conventions" / "ACTIVE_CONVENTIONS.yaml"
    if not conventions_yaml.exists():
        conventions_yaml.write_text(
            "# Active Convention Packs\n# Updated by install_convention.py\n\nactive_conventions: []\n"
        )
        print("  Created: .living/conventions/ACTIVE_CONVENTIONS.yaml")


def find_network_conventions_dir() -> Path | None:
    """Locate the network/conventions/ directory relative to this script."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "network" / "conventions",
        Path.home() / ".mycelium" / "network" / "conventions",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def get_core_convention_packs(network_dir: Path) -> list[str]:
    """Return names of convention packs marked core: true in the network."""
    core_packs = []
    for conv_dir in sorted(network_dir.iterdir()):
        pack_yaml = conv_dir / "CONVENTION_PACK.yaml"
        if not pack_yaml.exists():
            continue
        # Parse YAML front matter (between --- delimiters) or plain YAML
        content = pack_yaml.read_text()
        if yaml:
            # Strip YAML front matter delimiters if present
            text = content.strip()
            if text.startswith("---"):
                text = text[3:]
                end = text.find("---")
                if end != -1:
                    text = text[:end]
            data = yaml.safe_load(text)
            if isinstance(data, dict) and data.get("core") is True:
                core_packs.append(conv_dir.name)
        else:
            # Fallback: simple text check
            if "core: true" in content:
                core_packs.append(conv_dir.name)
    return core_packs


def install_core_convention_packs(target_dir: Path):
    """Auto-install all core convention packs from the network."""
    network_dir = find_network_conventions_dir()
    if not network_dir:
        print("  Warning: Could not locate mycelium network/conventions/ directory.")
        print("  Core convention packs were not auto-installed.")
        print("  Install them manually with install_convention.py.")
        return

    core_packs = get_core_convention_packs(network_dir)
    if not core_packs:
        print("  No core convention packs found in network.")
        return

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conventions_dir = target_dir / ".living" / "conventions"
    yaml_path = conventions_dir / "ACTIVE_CONVENTIONS.yaml"

    entries = []
    for pack_name in core_packs:
        source = network_dir / pack_name
        dest = conventions_dir / pack_name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        copied = [f for f in sorted(dest.rglob("*")) if f.is_file()]
        print(f"  Installed {pack_name} ({len(copied)} files)")
        entries.append(
            f"- name: {pack_name}\n"
            f"  path: .living/conventions/{pack_name}/\n"
            f"  installed: {now}\n"
            f"  core: true"
        )

    # Write ACTIVE_CONVENTIONS.yaml with core entries
    yaml_content = (
        "# Active Convention Packs\n"
        "# Updated by init_repo.py and install_convention.py\n\n"
        + "\n".join(entries)
        + "\n"
    )
    yaml_path.write_text(yaml_content)
    print(f"  Updated ACTIVE_CONVENTIONS.yaml with {len(core_packs)} core packs")


def find_mycelium_hooks_dir() -> Path | None:
    """Locate the mycelium hooks directory relative to this script."""
    candidates = [
        Path(__file__).resolve().parent.parent / "hooks",
        Path.home() / ".mycelium" / "skills" / "core" / "hooks",
    ]
    for candidate in candidates:
        if candidate.exists() and (candidate / "mycelium-health.sh").exists():
            return candidate
    return None


def install_claude_hooks(target_dir: Path):
    """Create or update .claude/settings.local.json with mycelium hooks.

    Handles the innermost-wins rule: subproject settings must include
    the complete hook set or parent hooks won't fire.
    """
    import json

    hooks_dir = find_mycelium_hooks_dir()
    if not hooks_dir:
        print("  Warning: Could not locate mycelium hooks directory.")
        print("  Hooks were not auto-installed. Install them manually.")
        return

    claude_dir = target_dir / ".claude"
    claude_dir.mkdir(exist_ok=True)
    settings_path = claude_dir / "settings.local.json"

    # Load existing settings if present
    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    # Define the three mycelium hooks with absolute paths
    health_hook = str(hooks_dir / "mycelium-health.sh")
    post_action_hook = str(hooks_dir / "mycelium-post-action.sh")
    stop_hook = str(hooks_dir / "mycelium-stop-check.sh")

    def _hook_entry(cmd: str) -> dict:
        return {"type": "command", "command": cmd}

    def _has_hook(hook_list: list, cmd: str) -> bool:
        """Check if a hook command is already registered."""
        return any(
            h.get("command") == cmd
            for entry in hook_list
            for h in entry.get("hooks", [])
        )

    # --- SessionStart: mycelium-health.sh ---
    session_start = hooks.setdefault("SessionStart", [])
    if not _has_hook(session_start, health_hook):
        # Find existing catch-all matcher entry or create one
        catch_all = next((e for e in session_start if e.get("matcher", "") == ""), None)
        if catch_all is None:
            catch_all = {"matcher": "", "hooks": []}
            session_start.append(catch_all)
        catch_all["hooks"].append(_hook_entry(health_hook))
        print("  Registered: SessionStart → mycelium-health.sh")

    # --- PostToolUse: mycelium-post-action.sh (matcher: Bash) ---
    post_tool = hooks.setdefault("PostToolUse", [])
    if not _has_hook(post_tool, post_action_hook):
        # Find existing Bash matcher entry or create one
        bash_entry = next((e for e in post_tool if e.get("matcher") == "Bash"), None)
        if bash_entry is None:
            bash_entry = {"matcher": "Bash", "hooks": []}
            post_tool.append(bash_entry)
        bash_entry["hooks"].append(_hook_entry(post_action_hook))
        print("  Registered: PostToolUse (Bash) → mycelium-post-action.sh")

    # --- Stop: mycelium-stop-check.sh ---
    stop = hooks.setdefault("Stop", [])
    if not _has_hook(stop, stop_hook):
        catch_all = next((e for e in stop if e.get("matcher", "") == ""), None)
        if catch_all is None:
            catch_all = {"matcher": "", "hooks": []}
            stop.append(catch_all)
        catch_all["hooks"].append(_hook_entry(stop_hook))
        print("  Registered: Stop → mycelium-stop-check.sh")

    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    print("  Wrote: .claude/settings.local.json")


def create_environments_file(target_dir: Path):
    """Create ENVIRONMENTS_INSTALLATIONS.md at repo root."""
    env_path = target_dir / "ENVIRONMENTS_INSTALLATIONS.md"
    if not env_path.exists():
        env_path.write_text(
            "# Environments & Installations\n\n"
            "## Primary Environment\n\n"
            "- **Manager**: \n"
            "- **Python version**: \n"
            "- **Created**: \n\n"
            "### Setup from scratch\n\n"
            "```bash\n"
            "# Add setup commands here\n"
            "```\n\n"
            "## Dependencies\n\n"
            "<!-- Add dependencies as they are installed. -->\n\n"
            "## System Dependencies\n\n"
            "<!-- Add system-level dependencies here. -->\n"
        )
        print("  Created: ENVIRONMENTS_INSTALLATIONS.md")


def audit_existing_structure(target_dir: Path):
    """Audit an existing repo and report what needs to change."""
    # TODO: Walk the directory tree and identify:
    #   - Existing data directories that should map to data/raw/ or data/processed/
    #   - Existing analysis scripts that should become analysis/[name]/ directories
    #   - Existing documentation that could become reference_material/
    #   - Files that don't fit the mycelium structure
    print("  Auditing existing structure...")
    print("  TODO: Full audit implementation")
    print("  Would scan for existing data, analyses, and documentation")
    print("  Would propose a migration plan for user confirmation")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Mycelium Init — Target: {target_dir}")
    print("=" * 50)

    if args.restructure:
        print("\nMode: Restructure existing repository")
        audit_existing_structure(target_dir)
        print("\nRestructure mode requires user confirmation before proceeding.")
        print("TODO: Implement interactive restructure workflow")
        return

    if check_existing_structure(target_dir):
        print("\nThis repo already has a mycelium structure.")
        print(
            "Use --restructure to audit and update, or remove .living/ to start fresh."
        )
        sys.exit(1)

    print("\nCreating directory structure...")
    create_directory_structure(target_dir)

    print("\nCreating manifests...")
    create_manifests(target_dir)

    print("\nCreating todo list...")
    create_todo_list(target_dir)

    print("\nInitializing living layer...")
    create_living_layer(target_dir)

    print("\nCreating environment documentation...")
    create_environments_file(target_dir)

    print("\nInstalling core convention packs...")
    install_core_convention_packs(target_dir)

    print("\nInstalling Claude Code hooks...")
    install_claude_hooks(target_dir)

    print("\n" + "=" * 50)
    print("Mycelium initialization complete!")
    print("\nNext steps:")
    print("  1. Generate CLAUDE.md from the template")
    print(
        "  2. Install domain conventions if needed (/mycelium:skill install-convention)"
    )
    print("  3. Run validate_structure.py to confirm setup")
    print("  4. Start working — the repo is now alive!")


if __name__ == "__main__":
    main()
