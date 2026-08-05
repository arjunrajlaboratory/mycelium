#!/usr/bin/env python3
"""Initialize a mycelium-enabled living repository.

Scaffolds the directory structure, manifests, and living layer
for a new or existing repository. Creates all required directories,
empty manifests, and the .living/ memory layer.

Usage:
    python init_repo.py [--target-dir PATH] [--restructure]
"""

import argparse
import json
import os
import shlex
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


MANAGED_INIT_DIRECTORIES = (
    ".living",
    ".living/conventions",
    ".living/generated-conventions",
    ".living/log",
    ".living/findings",
    ".living/outputs",
    ".living/outputs/knowledge-transfers",
    ".living/skills",
    ".mycelium",
    ".claude",
    ".codex",
    "algorithms",
    "analysis",
    "data",
    "data/raw",
    "data/processed",
    "data/metadata",
    "reference_material",
    "skillpacks",
    "todo",
)

MANAGED_INIT_FILES = (
    ".mycelium/.gitignore",
    ".mycelium/plugin-root",
    ".living/decisions.md",
    ".living/learnings.md",
    ".living/conventions.md",
    ".living/log/LOG_REGISTRY.md",
    ".living/conventions/ACTIVE_CONVENTIONS.yaml",
    "algorithms/ALGORITHM_MANIFEST.md",
    "algorithms/_README_TEMPLATE.md",
    "analysis/ANALYSIS_MANIFEST.md",
    "analysis/_README_TEMPLATE.md",
    "data/DATA_MANIFEST.md",
    "reference_material/REFERENCE_MANIFEST.md",
    "todo/TODO_REGISTRY.md",
    "todo/TODO_ITEM_TEMPLATE.md",
    "skillpacks/.gitignore",
    "skillpacks/README.md",
    "ENVIRONMENTS_INSTALLATIONS.md",
    "MYCELIUM.md",
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/settings.local.json",
    ".codex/hooks.json",
    ".codex/.gitignore",
)


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
    if living_dir.is_symlink():
        raise ValueError(f"Refusing symlinked Mycelium directory: {living_dir}")
    if living_dir.exists():
        print(f"Found existing .living/ directory at {living_dir}")
        return True
    return False


def create_directory_structure(target_dir: Path):
    """Create the canonical mycelium directory structure."""
    directories = [
        path
        for path in MANAGED_INIT_DIRECTORIES
        if path not in {".claude", ".codex"}
    ]

    for dir_name in directories:
        ensure_safe_project_directory(target_dir, dir_name, create=True)
        print(f"  Created: {dir_name}/")

    state_gitignore = target_dir / ".mycelium" / ".gitignore"
    ensure_safe_regular_file(state_gitignore)
    if not state_gitignore.exists():
        _atomic_write_text(state_gitignore, "*\n!.gitignore\n")
    write_plugin_root_pointer(target_dir)


def mycelium_plugin_root() -> Path:
    """Return the installed Mycelium plugin root for this script."""
    return Path(__file__).resolve().parents[3]


def ensure_safe_project_directory(
    target_dir: Path, relative_path: str | Path, *, create: bool
) -> Path:
    """Validate a project-local directory path without following symlinks."""
    root = target_dir.resolve(strict=True)
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError(f"Expected a project-relative directory: {relative}")
    candidate = Path(os.path.abspath(root / relative))
    try:
        parts = candidate.relative_to(root).parts
    except ValueError as exc:
        raise ValueError(f"Directory escapes project root: {candidate}") from exc
    if not parts:
        raise ValueError("Refusing to use the project root as a managed directory")

    current = root
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Refusing symlinked Mycelium directory: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"Managed directory path is not a directory: {current}")

    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError(f"Directory escapes project root: {candidate}") from exc
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
        try:
            candidate.resolve(strict=True).relative_to(root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f"Directory escapes project root: {candidate}") from exc
    return candidate


def ensure_safe_regular_file(path: Path) -> None:
    """Reject a managed file that is a symlink or a non-regular object."""
    if path.is_symlink():
        raise ValueError(f"Refusing symlinked Mycelium file: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"Managed file path is not a regular file: {path}")


def ensure_directory_tree_has_no_symlinks(directory: Path) -> None:
    """Reject links nested anywhere below a managed directory."""
    if not directory.exists():
        return
    if directory.is_symlink():
        raise ValueError(f"Refusing symlinked Mycelium directory: {directory}")
    for current_root, directory_names, file_names in os.walk(
        directory, followlinks=False
    ):
        root = Path(current_root)
        for name in directory_names + file_names:
            candidate = root / name
            if candidate.is_symlink():
                raise ValueError(f"Refusing symlinked Mycelium path: {candidate}")


def validate_hook_config(config: object, source: str | Path) -> dict:
    """Validate the hook structures traversed by the installers.

    Unknown top-level, group, and handler fields remain valid so user-authored
    configuration is preserved. Only the container and command types that the
    Mycelium installers inspect are constrained here.
    """
    label = str(source)
    if not isinstance(config, dict):
        raise ValueError(f"{label}: hook configuration must be an object")

    if "hooks" not in config:
        return config
    hooks = config["hooks"]
    if not isinstance(hooks, dict):
        raise ValueError(f"{label}: hooks must be an object")

    for event, groups in hooks.items():
        if not isinstance(groups, list):
            raise ValueError(f"{label}: hook event {event!r} must be a list")
        for group_index, group in enumerate(groups):
            if not isinstance(group, dict):
                raise ValueError(
                    f"{label}: hook group must be an object "
                    f"({event!r}[{group_index}])"
                )
            if "hooks" not in group:
                continue
            handlers = group["hooks"]
            if not isinstance(handlers, list):
                raise ValueError(
                    f"{label}: hook handlers must be a list "
                    f"({event!r}[{group_index}])"
                )
            for handler_index, handler in enumerate(handlers):
                if not isinstance(handler, dict):
                    raise ValueError(
                        f"{label}: hook handler must be an object "
                        f"({event!r}[{group_index}].hooks[{handler_index}])"
                    )
                command = handler.get("command", "")
                if not isinstance(command, str):
                    raise ValueError(
                        f"{label}: hook command must be a string "
                        f"({event!r}[{group_index}].hooks[{handler_index}])"
                    )
    return config


def load_hook_config(config_path: Path) -> dict:
    """Parse and validate an existing Claude or Codex hook config."""
    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    return validate_hook_config(parsed, config_path)


def preflight_hook_config_files(target_dir: Path) -> None:
    """Validate all existing host hook configs before any managed write."""
    for config_path in (
        target_dir / ".claude" / "settings.local.json",
        target_dir / ".codex" / "hooks.json",
    ):
        if config_path.exists():
            load_hook_config(config_path)


def preflight_initialization(target_dir: Path) -> None:
    """Validate every repository-controlled initialization output before writes."""
    target_dir.resolve(strict=True)
    for relative_path in MANAGED_INIT_DIRECTORIES:
        ensure_safe_project_directory(target_dir, relative_path, create=False)
    for relative_path in MANAGED_INIT_FILES:
        ensure_safe_regular_file(target_dir / relative_path)

    preflight_hook_config_files(target_dir)

    network_dir = find_network_conventions_dir()
    if network_dir:
        for pack_name in get_core_convention_packs(network_dir):
            destination = target_dir / ".living" / "conventions" / pack_name
            ensure_safe_project_directory(
                target_dir,
                destination.relative_to(target_dir),
                create=False,
            )
            ensure_directory_tree_has_no_symlinks(destination)


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a validated project-local text file without following links."""
    ensure_safe_regular_file(path)
    mode = path.stat().st_mode & 0o7777 if path.exists() else 0o644
    descriptor, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.tmp.", dir=path.parent, text=True
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_text_if_missing(path: Path, content: str) -> bool:
    """Create a managed text file atomically, rejecting links and special files."""
    ensure_safe_regular_file(path)
    if path.exists():
        return False
    _atomic_write_text(path, content)
    return True


def write_plugin_root_pointer(target_dir: Path) -> bool:
    """Write the machine-local path used by generated project guidance."""
    state_dir = ensure_safe_project_directory(target_dir, ".mycelium", create=True)
    pointer = state_dir / "plugin-root"
    ensure_safe_regular_file(pointer)
    expected = f"{mycelium_plugin_root()}\n"
    before = pointer.read_text(encoding="utf-8") if pointer.exists() else None
    if before != expected:
        _atomic_write_text(pointer, expected)
        return True
    return False


def refresh_generated_guidance(content: str, canonical: str) -> str:
    """Refresh narrowly managed generated guidance without replacing custom text."""
    heading = "### Automated Enforcement"
    next_heading = "### Knowledge Transfer (Cross-Project)"
    current_start = content.find(heading)
    current_end = content.find(next_heading, current_start + len(heading))
    canonical_start = canonical.find(heading)
    canonical_end = canonical.find(next_heading, canonical_start + len(heading))
    refreshed = content
    if min(current_start, current_end, canonical_start, canonical_end) >= 0:
        replacement = canonical[canonical_start:canonical_end]
        refreshed = content[:current_start] + replacement + content[current_end:]

    # This generated rule lives immediately before Automated Enforcement, so it
    # was outside the managed section above. Refresh only its exact numbered
    # line: replacing the whole post-action section would overwrite repository
    # customizations, while leaving the legacy shared path contradicts the
    # session-scoped path injected by SessionStart.
    session_summary_prefix = "10. **Session summary**:"
    canonical_lines = canonical.splitlines(keepends=True)
    replacement_lines = [
        line for line in canonical_lines if line.startswith(session_summary_prefix)
    ]
    if len(replacement_lines) != 1:
        return refreshed

    current_lines = refreshed.splitlines(keepends=True)
    for index, line in enumerate(current_lines):
        if line.startswith(session_summary_prefix):
            current_lines[index] = replacement_lines[0]
            return "".join(current_lines)
    return refreshed


def create_agent_guidance(target_dir: Path):
    """Create shared Mycelium guidance plus thin Claude and Codex adapters."""
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    canonical = templates_dir / "MYCELIUM.md.template"
    guidance_targets = {
        name: target_dir / name for name in ("MYCELIUM.md", "CLAUDE.md", "AGENTS.md")
    }
    # Validate every target up front so discovering an unsafe later adapter
    # cannot leave earlier guidance partially modified.
    for target in guidance_targets.values():
        ensure_safe_regular_file(target)
    canonical_target = guidance_targets["MYCELIUM.md"]
    if canonical.exists():
        canonical_text = canonical.read_text(encoding="utf-8")
    else:
        canonical_text = ""
    if canonical_text and not canonical_target.exists():
        legacy_claude = guidance_targets["CLAUDE.md"]
        if legacy_claude.exists():
            legacy_text = legacy_claude.read_text(encoding="utf-8").replace(
                ".claude/last-session.md", ".mycelium/last-session.md"
            )
            canonical_text = (
                canonical_text.rstrip()
                + "\n\n## Existing project guidance migrated from CLAUDE.md\n\n"
                + "The content below is preserved from the pre-migration project. "
                + "Resolve its bundled `skills/` and `network/` references through "
                + "`.mycelium/plugin-root` as described above.\n\n"
                + legacy_text.rstrip()
                + "\n"
            )
        _atomic_write_text(canonical_target, canonical_text)
        print("  Created: MYCELIUM.md")
    elif canonical_text and canonical_target.exists():
        current = canonical_target.read_text(encoding="utf-8")
        refreshed = refresh_generated_guidance(current, canonical_text)
        if refreshed != current:
            _atomic_write_text(canonical_target, refreshed)
            print("  Updated: MYCELIUM.md automated enforcement guidance")

    for template_name, target_name in (
        ("CLAUDE.md.template", "CLAUDE.md"),
        ("AGENTS.md.template", "AGENTS.md"),
    ):
        template = templates_dir / template_name
        target = guidance_targets[target_name]
        adapter = template.read_text(encoding="utf-8")
        if not target.exists():
            _atomic_write_text(target, adapter)
            print(f"  Created: {target_name}")
        elif "<!-- MYCELIUM:BEGIN -->" not in target.read_text(encoding="utf-8"):
            callout = adapter[adapter.index("<!-- MYCELIUM:BEGIN -->") :]
            current = target.read_text(encoding="utf-8")
            _atomic_write_text(target, current + "\n\n" + callout.rstrip() + "\n")
            print(f"  Updated: {target_name} with Mycelium routing")


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
    """Create descriptive manifest files in each top-level directory.

    Also drops a `_README_TEMPLATE.md` into algorithms/ and analysis/ so
    new entries have a concrete starting point.
    """
    manifest_dirs = ["algorithms", "analysis", "data", "reference_material"]

    for dir_name in manifest_dirs:
        manifest_filename = dir_to_manifest_name(dir_name)
        manifest_path = target_dir / dir_name / manifest_filename
        if _write_text_if_missing(
            manifest_path,
            f"# {dir_name.replace('_', ' ').title()} Manifest\n\n"
            "<!-- Add entries below using the appropriate manifest entry template. -->\n",
        ):
            print(f"  Created: {dir_name}/{manifest_filename}")

    # Drop README templates so new analyses/algorithms have a concrete start
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    for src_name, target_subdir in (
        ("algorithm-readme.md", "algorithms"),
        ("analysis-readme.md", "analysis"),
    ):
        src = templates_dir / src_name
        dst = target_dir / target_subdir / "_README_TEMPLATE.md"
        if src.exists() and _write_text_if_missing(
            dst, src.read_text(encoding="utf-8")
        ):
            print(f"  Created: {target_subdir}/_README_TEMPLATE.md")


def create_todo_list(target_dir: Path):
    """Create the todo registry and item template used by the core skill."""
    todo_dir = ensure_safe_project_directory(target_dir, "todo", create=True)
    registry = todo_dir / "TODO_REGISTRY.md"
    if _write_text_if_missing(
        registry,
        "# TODO Registry\n\n"
        "| Item | Priority | Status | Category | Date | Author | File |\n"
        "|------|----------|--------|----------|------|--------|------|\n\n"
        "<!-- Add new entries above this line -->\n",
    ):
        print("  Created: todo/TODO_REGISTRY.md")

    item_template = todo_dir / "TODO_ITEM_TEMPLATE.md"
    bundled_template = mycelium_plugin_root() / "todo" / "TODO_ITEM_TEMPLATE.md"
    if bundled_template.exists() and _write_text_if_missing(
        item_template, bundled_template.read_text(encoding="utf-8")
    ):
        print("  Created: todo/TODO_ITEM_TEMPLATE.md")


def create_living_layer(target_dir: Path):
    """Initialize the .living/ memory layer with empty files."""
    living_dir = ensure_safe_project_directory(target_dir, ".living", create=True)

    files = {
        "decisions.md": (
            "# Decision Log\n\n"
            "Append-only log of non-obvious decisions and their rationale.\n\n"
            "**Entry template:** copy from "
            "`skills/core/templates/decision-log-entry.md` "
            "(includes Context, Decision, Alternatives considered, Rationale, "
            "Consequences, Tags fields).\n"
        ),
        "learnings.md": (
            "# Learnings\n\n"
            "Append-only log of gotchas, surprises, and insights.\n\n"
            "**Entry template:** copy from "
            "`skills/core/templates/learning-entry.md` "
            "(includes Category, What happened, Why it matters, Resolution, "
            "Tags fields). The `**Tags**:` line is consumed by "
            "`generate_index.py --summary-heuristic` to build the cluster "
            "summary in INDEX.md — use them.\n"
        ),
        "conventions.md": (
            "# Repo-Specific Conventions\n\n"
            "Overrides to mycelium defaults or convention pack conventions.\n\n"
            "<!-- Document any project-specific convention overrides here. -->\n"
        ),
    }

    for filename, content in files.items():
        file_path = living_dir / filename
        if _write_text_if_missing(file_path, content):
            print(f"  Created: .living/{filename}")

    # Session log registry
    registry_path = living_dir / "log" / "LOG_REGISTRY.md"
    if _write_text_if_missing(
        registry_path,
        "# Session Log Registry\n\n"
        "| Date | Session ID | Project | Branch | Duration | Files Changed "
        "| Summary | Key Outputs | Status | Tags | Log |\n"
        "|------|-----------|---------|--------|----------|---------------"
        "|---------|-------------|--------|------|-----|\n",
    ):
        print("  Created: .living/log/LOG_REGISTRY.md")

    # Create ACTIVE_CONVENTIONS.yaml
    conventions_yaml = living_dir / "conventions" / "ACTIVE_CONVENTIONS.yaml"
    if _write_text_if_missing(
        conventions_yaml,
        "# Active Convention Packs\n# Updated by install_convention.py\n\nactive_conventions: []\n",
    ):
        print("  Created: .living/conventions/ACTIVE_CONVENTIONS.yaml")


def create_skillpacks(target_dir: Path):
    """Create the skillpacks/ directory with .gitignore and README.

    Skill packs are external git repos cloned into skillpacks/ for use by
    the skill-bridge convention. They are NOT installed as agent skill packs —
    they sit inert on disk and are read on demand by convention-routed workflows.
    """
    skillpacks_dir = ensure_safe_project_directory(
        target_dir, "skillpacks", create=True
    )

    gitignore_path = skillpacks_dir / ".gitignore"
    if _write_text_if_missing(
        gitignore_path,
        "# Skill pack repos are cloned here but NOT tracked by this project's git.\n"
        "# They are their own git repos and should be updated independently.\n"
        "#\n"
        "# To set up:\n"
        "#   cd skillpacks/\n"
        "#   git clone https://github.com/K-Dense-AI/scientific-agent-skills.git\n"
        "#   git clone https://github.com/GPTomics/bioSkills.git\n"
        "#   git clone https://github.com/arjunrajlaboratory/Autonomous-Science.git\n"
        "#\n"
        "# These repos are inert reference libraries. Do NOT install them as\n"
        "# agent skill packs. The skill-bridge convention reads specific files\n"
        "# from them on demand.\n\n"
        "*\n"
        "!.gitignore\n"
        "!README.md\n",
    ):
        print("  Created: skillpacks/.gitignore")

    readme_path = skillpacks_dir / "README.md"
    if _write_text_if_missing(
        readme_path,
        "# Skill Packs\n\n"
        "External skill repositories cloned here for use by the `skill-bridge` convention pack. "
        "These are **inert reference libraries** — never installed as agent skill packs.\n\n"
        "## Setup\n\n"
        "```bash\n"
        "cd skillpacks/\n"
        "git clone https://github.com/K-Dense-AI/scientific-agent-skills.git\n"
        "git clone https://github.com/GPTomics/bioSkills.git\n"
        "git clone https://github.com/arjunrajlaboratory/Autonomous-Science.git\n"
        "```\n\n"
        "## Updating\n\n"
        "```bash\n"
        "cd skillpacks/scientific-agent-skills && git pull\n"
        "cd ../bioSkills && git pull\n"
        "cd ../Autonomous-Science && git pull\n"
        "```\n\n"
        "## How These Are Used\n\n"
        "The `skill-bridge` convention pack (in `.living/conventions/skill-bridge/` or "
        "`network/conventions/skill-bridge/`) routes analysis workflows to specific "
        "SKILL.md files within these repos. The agent reads one file at a time "
        "(~150-200 lines per analysis step), never loading the full repos into context.\n",
    ):
        print("  Created: skillpacks/README.md")


def find_network_conventions_dir() -> Path | None:
    """Locate the network/conventions/ directory relative to this script."""
    candidates = [
        Path(__file__).resolve().parents[3] / "network" / "conventions",
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
    _atomic_write_text(yaml_path, yaml_content)
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


MYCELIUM_HOOK_BASENAMES = {
    "mycelium-health.sh",
    "mycelium-post-action.sh",
    "mycelium-stop-check.sh",
    "mycelium-activity-tracker.sh",
    "mycelium-read-tracker.sh",
    "mycelium-data-tracker.sh",
}

LEGACY_MYCELIUM_HOOK_BASENAMES = {"mycelium-data-lineage-stop.sh"}
ALL_MYCELIUM_HOOK_BASENAMES = (
    MYCELIUM_HOOK_BASENAMES | LEGACY_MYCELIUM_HOOK_BASENAMES
)

CLAUDE_HOOK_SPECS = (
    ("SessionStart", "", "mycelium-health.sh"),
    ("PostToolUse", "Bash", "mycelium-post-action.sh"),
    ("PostToolUse", "Edit|Write", "mycelium-activity-tracker.sh"),
    ("PostToolUse", "Read", "mycelium-read-tracker.sh"),
    ("PostToolUse", "Bash", "mycelium-data-tracker.sh"),
    ("Stop", "", "mycelium-stop-check.sh"),
)


def _hook_basename(command: str) -> str:
    """Return the script basename from a plain or env-prefixed command."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return Path(parts[-1]).name if parts else ""


def _hook_command_path(command: str) -> Path:
    """Return the executable path from a hook command string."""
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    return Path(parts[-1]) if parts else Path()


def _put_hook_first(handlers: list[dict], basename: str) -> None:
    """Move a named hook ahead of sibling handlers without disturbing others."""
    matching = [
        handler
        for handler in handlers
        if _hook_basename(handler.get("command", "")) == basename
    ]
    if matching:
        handlers[:] = matching + [handler for handler in handlers if handler not in matching]


def _ensure_gitignore_entry(path: Path, entry: str) -> None:
    """Append one exact ignore entry while preserving existing content."""
    ensure_safe_regular_file(path)
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if entry in content.splitlines():
        return
    separator = "" if not content or content.endswith("\n") else "\n"
    _atomic_write_text(path, f"{content}{separator}{entry}\n")


def _remove_gitignore_entry(path: Path, entry: str) -> bool:
    """Remove one exact ignore entry while preserving every other line."""
    ensure_safe_regular_file(path)
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    retained = [line for line in lines if line != entry]
    if retained == lines:
        return False
    if retained:
        _atomic_write_text(path, "\n".join(retained) + "\n")
    else:
        path.unlink()
    return True


def _consolidate_duplicate_hooks(
    hooks: dict,
    valid_replacement_for: dict[str, str] | None = None,
) -> tuple[int, dict[str, str]]:
    """Remove duplicate or stale mycelium-hook entries.

    For each mycelium hook basename, group existing entries:
    - Entries whose command path no longer exists on disk are "stale"
    - Entries whose path exists are "live"

    If a valid replacement path is supplied (in `valid_replacement_for`)
    for a basename, stale entries for that basename are dropped — the
    install pass will then register the fresh path. This handles repos
    whose old install directory was moved or deleted.

    Without a valid replacement (e.g. the script can't locate a hooks
    dir at all, or the hook isn't shipped), stale entries are preserved
    rather than risk making a bad situation worse during transient
    filesystem hiccups (network drives, etc.).

    Among live entries, pick canonical: prefer `/marketplaces/`, otherwise
    longest path. Drop the rest.

    Mutates `hooks` in place. Returns `(removed_count, kept_by_basename)`.
    """
    valid_replacement_for = valid_replacement_for or {}
    removed = 0
    kept_by_basename: dict[str, str] = {}

    for entries in hooks.values():
        for entry in entries:
            basename_to_cmds: dict[str, list[str]] = {}
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                bn = _hook_basename(cmd)
                if bn in MYCELIUM_HOOK_BASENAMES:
                    basename_to_cmds.setdefault(bn, []).append(cmd)

            # Pick canonical for each basename
            canonical: dict[str, str] = {}
            droppable_stale: set[str] = set()
            for bn, cmds in basename_to_cmds.items():
                live = [c for c in cmds if _hook_command_path(c).exists()]
                stale = [c for c in cmds if not _hook_command_path(c).exists()]

                if live:
                    marketplace = [c for c in live if "/marketplaces/" in c]
                    if marketplace:
                        canonical[bn] = sorted(marketplace)[0]
                    else:
                        canonical[bn] = sorted(live, key=lambda c: (-len(c), c))[0]
                    kept_by_basename[bn] = canonical[bn]
                    # All stale paths for this basename are droppable when at
                    # least one live entry exists
                    droppable_stale.update(stale)
                elif bn in valid_replacement_for:
                    # No live entries, but we have a known-good replacement.
                    # Drop ALL stale entries; install pass will add the fresh one.
                    droppable_stale.update(stale)
                else:
                    # No live entries and no replacement available — keep
                    # everything to avoid making things worse on transient
                    # filesystem issues. canonical stays unset, no drops.
                    pass

            # Apply: drop non-canonical entries (duplicates) and droppable stale
            new_hook_list = []
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                bn = _hook_basename(cmd)
                if bn in canonical and cmd != canonical[bn]:
                    removed += 1
                    continue
                if cmd in droppable_stale and bn not in canonical:
                    removed += 1
                    continue
                new_hook_list.append(h)
            entry["hooks"] = new_hook_list

    return removed, kept_by_basename


def _normalize_claude_hook_locations(hooks: dict) -> None:
    """Keep each Mycelium Claude hook once, under its canonical matcher."""
    for expected_event, expected_matcher, basename in CLAUDE_HOOK_SPECS:
        candidates: list[tuple[str, dict]] = []
        emptied_group_ids: set[int] = set()

        for event, groups in hooks.items():
            for group in groups:
                handlers = group.get("hooks", [])
                retained = []
                for handler in handlers:
                    if _hook_basename(handler.get("command", "")) == basename:
                        candidates.append((event, handler))
                    else:
                        retained.append(handler)
                if len(retained) != len(handlers):
                    group["hooks"] = retained
                    if not retained:
                        emptied_group_ids.add(id(group))

        target_groups = hooks.setdefault(expected_event, [])
        target = next(
            (
                group
                for group in target_groups
                if group.get("matcher", "") == expected_matcher
            ),
            None,
        )
        if target is None:
            target = {"matcher": expected_matcher, "hooks": []}
            target_groups.append(target)

        live = [
            item for item in candidates if _hook_command_path(item[1]["command"]).exists()
        ]
        marketplace = [
            item for item in live if "/marketplaces/" in item[1]["command"]
        ]
        correctly_scoped = [item for item in live if item[0] == expected_event]
        selected = (marketplace or correctly_scoped or live or candidates)[0][1]
        target.setdefault("hooks", []).append(selected)

        # Remove only groups this pass emptied. User-authored empty groups are
        # preserved, and the target group remains even if it was moved in place.
        for event, groups in hooks.items():
            hooks[event] = [
                group
                for group in groups
                if group is target
                or id(group) not in emptied_group_ids
                or group.get("hooks")
            ]


def _remove_claude_hook_basenames(hooks: dict, basenames: set[str]) -> int:
    """Remove deprecated Mycelium handlers without disturbing user hooks."""
    removed = 0
    for event, groups in list(hooks.items()):
        retained_groups = []
        for group in groups:
            handlers = group.get("hooks", [])
            retained_handlers = [
                handler
                for handler in handlers
                if _hook_basename(handler.get("command", "")) not in basenames
            ]
            removed += len(handlers) - len(retained_handlers)
            if retained_handlers:
                group["hooks"] = retained_handlers
                retained_groups.append(group)
            elif not handlers:
                retained_groups.append(group)
        if retained_groups:
            hooks[event] = retained_groups
        else:
            del hooks[event]
    return removed


def install_claude_hooks(target_dir: Path):
    """Create or update .claude/settings.local.json with mycelium hooks.

    Two-pass:
    1. Consolidate any pre-existing duplicate entries (same script, different
       paths — e.g. marketplace + dev-repo). Prefers marketplace path.
    2. Install the complete mycelium hook bundle that are missing entirely. Match
       by script *basename* not full path so a re-run with a different
       hooks-dir does not double-install.

    Handles the innermost-wins rule: subproject settings must include
    the complete hook bundle or parent hooks won't fire.
    """
    hooks_dir = find_mycelium_hooks_dir()
    if not hooks_dir:
        print("  Warning: Could not locate mycelium hooks directory.")
        print("  Hooks were not auto-installed. Install them manually.")
        return

    claude_dir = ensure_safe_project_directory(target_dir, ".claude", create=True)
    settings_path = claude_dir / "settings.local.json"
    ensure_safe_regular_file(settings_path)
    original_content: str | None = None

    # Load existing settings if present
    if settings_path.exists():
        original_content = settings_path.read_text()
        settings = validate_hook_config(json.loads(original_content), settings_path)
    else:
        settings = {}

    hooks = settings.setdefault("hooks", {})

    deprecated_removed = _remove_claude_hook_basenames(
        hooks, LEGACY_MYCELIUM_HOOK_BASENAMES
    )
    if deprecated_removed:
        # The previously registered stop-check may come from an older live
        # marketplace cache and therefore may not contain the new synchronous
        # lineage phase. Replace it from this installer, not merely by basename.
        _remove_claude_hook_basenames(hooks, {"mycelium-stop-check.sh"})
        print(
            "  Removed deprecated standalone data-lineage Stop hook; "
            "mycelium-stop-check.sh now serializes consolidation."
        )

    # --- Pass 1: consolidate duplicates and drop stale entries ---
    # Build the replacement map: basename → known-good path on disk.
    # The consolidation pass uses this to determine when it's safe to drop
    # entries whose path no longer exists.
    valid_replacement_for = {
        bn: str(hooks_dir / bn)
        for bn in MYCELIUM_HOOK_BASENAMES
        if (hooks_dir / bn).exists()
    }
    removed, kept = _consolidate_duplicate_hooks(
        hooks, valid_replacement_for=valid_replacement_for
    )
    if removed > 0:
        print(
            f"  Consolidated: removed {removed} duplicate or stale hook entr"
            f"{'y' if removed == 1 else 'ies'}"
        )

    # --- Pass 2: install missing hooks ---
    # Use the path resolved from this script's location for any hook NOT
    # already present (the consolidation pass picked existing paths).
    health_hook = str(hooks_dir / "mycelium-health.sh")
    post_action_hook = str(hooks_dir / "mycelium-post-action.sh")
    stop_hook = str(hooks_dir / "mycelium-stop-check.sh")
    activity_tracker_hook = str(hooks_dir / "mycelium-activity-tracker.sh")
    read_tracker_hook = str(hooks_dir / "mycelium-read-tracker.sh")
    data_tracker_hook = str(hooks_dir / "mycelium-data-tracker.sh")

    def _hook_entry(cmd: str) -> dict:
        return {"type": "command", "command": cmd}

    def _has_hook(hook_list: list, basename: str) -> bool:
        """Check if any entry registers the named script (path-agnostic)."""
        return any(
            _hook_basename(h.get("command", "")) == basename
            for entry in hook_list
            for h in entry.get("hooks", [])
        )

    # --- SessionStart: mycelium-health.sh ---
    session_start = hooks.setdefault("SessionStart", [])
    if not _has_hook(session_start, "mycelium-health.sh"):
        catch_all = next((e for e in session_start if e.get("matcher", "") == ""), None)
        if catch_all is None:
            catch_all = {"matcher": "", "hooks": []}
            session_start.append(catch_all)
        catch_all["hooks"].append(_hook_entry(health_hook))
        print("  Registered: SessionStart → mycelium-health.sh")

    # --- PostToolUse: mycelium-post-action.sh (matcher: Bash) ---
    post_tool = hooks.setdefault("PostToolUse", [])
    if not _has_hook(post_tool, "mycelium-post-action.sh"):
        bash_entry = next((e for e in post_tool if e.get("matcher") == "Bash"), None)
        if bash_entry is None:
            bash_entry = {"matcher": "Bash", "hooks": []}
            post_tool.append(bash_entry)
        bash_entry["hooks"].append(_hook_entry(post_action_hook))
        print("  Registered: PostToolUse (Bash) → mycelium-post-action.sh")

    # --- PostToolUse: mycelium-activity-tracker.sh (matcher: Edit|Write) ---
    if not _has_hook(post_tool, "mycelium-activity-tracker.sh"):
        edit_write_entry = next(
            (e for e in post_tool if e.get("matcher") == "Edit|Write"), None
        )
        if edit_write_entry is None:
            edit_write_entry = {"matcher": "Edit|Write", "hooks": []}
            post_tool.append(edit_write_entry)
        edit_write_entry["hooks"].append(_hook_entry(activity_tracker_hook))
        print("  Registered: PostToolUse (Edit|Write) → mycelium-activity-tracker.sh")

    # --- PostToolUse: mycelium-read-tracker.sh (matcher: Read) ---
    # Logs each .living/ file read to .mycelium/mycelium-read-access.log so we
    # can measure access rates over time. Silent — no agent-facing context.
    if not _has_hook(post_tool, "mycelium-read-tracker.sh"):
        read_entry = next((e for e in post_tool if e.get("matcher") == "Read"), None)
        if read_entry is None:
            read_entry = {"matcher": "Read", "hooks": []}
            post_tool.append(read_entry)
        read_entry["hooks"].append(_hook_entry(read_tracker_hook))
        print("  Registered: PostToolUse (Read) → mycelium-read-tracker.sh")

    # --- PostToolUse: mycelium-data-tracker.sh (matcher: Bash) ---
    # Detects analysis invocations and appends one NDJSON event per detected
    # script to .mycelium/mycelium-data-events.tmp under fcntl.flock. Consumed
    # at Stop by mycelium-data-lineage-stop.sh.
    if not _has_hook(post_tool, "mycelium-data-tracker.sh"):
        bash_entry = next((e for e in post_tool if e.get("matcher") == "Bash"), None)
        if bash_entry is None:
            bash_entry = {"matcher": "Bash", "hooks": []}
            post_tool.append(bash_entry)
        bash_entry["hooks"].append(_hook_entry(data_tracker_hook))
        print("  Registered: PostToolUse (Bash) → mycelium-data-tracker.sh")

    # --- Stop: mycelium-stop-check.sh ---
    stop = hooks.setdefault("Stop", [])
    if not _has_hook(stop, "mycelium-stop-check.sh"):
        catch_all = next((e for e in stop if e.get("matcher", "") == ""), None)
        if catch_all is None:
            catch_all = {"matcher": "", "hooks": []}
            stop.append(catch_all)
        catch_all["hooks"].append(_hook_entry(stop_hook))
        print("  Registered: Stop → mycelium-stop-check.sh")

    # Older installs may have a valid script under the wrong matcher or event.
    # Normalize after filling gaps so migration both detects and repairs them.
    _normalize_claude_hook_locations(hooks)

    rendered = json.dumps(settings, indent=2) + "\n"
    if rendered != original_content:
        _atomic_write_text(settings_path, rendered)
        print("  Wrote: .claude/settings.local.json")
        return True
    return False


def _remove_codex_hooks_config(config: dict) -> bool:
    """Remove deprecated project-local Mycelium hook registrations.

    Codex plugins provide ``PLUGIN_ROOT`` only to plugin-bundled hooks. Older
    Mycelium versions wrote resolved cache paths into project hooks.json files;
    those paths break whenever Codex replaces the plugin cache. Preserve every
    unrelated user hook and remove only handlers whose command resolves to a
    known Mycelium hook basename.
    """
    validate_hook_config(config, "Codex hook configuration")
    hooks = config.get("hooks")
    if hooks is None:
        return False

    changed = False
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        retained_groups = []
        for group in groups:
            handlers = group.get("hooks", [])
            retained_handlers = [
                handler
                for handler in handlers
                if _hook_basename(handler.get("command", ""))
                not in ALL_MYCELIUM_HOOK_BASENAMES
            ]
            removed_here = len(retained_handlers) != len(handlers)
            changed = changed or removed_here
            if removed_here:
                group["hooks"] = retained_handlers
            # Drop groups emptied by this cleanup, but preserve unrelated
            # user-authored empty matcher groups exactly as they were.
            if retained_handlers or not removed_here:
                retained_groups.append(group)
        if retained_groups:
            hooks[event] = retained_groups
        else:
            del hooks[event]
            changed = True

    if not hooks:
        config.pop("hooks", None)
    return changed


def install_codex_hooks(target_dir: Path):
    """Remove obsolete repo-local hooks; Codex hooks ship with the plugin."""
    codex_dir = ensure_safe_project_directory(target_dir, ".codex", create=False)
    hooks_path = codex_dir / "hooks.json"
    gitignore = codex_dir / ".gitignore"
    ensure_safe_regular_file(hooks_path)
    ensure_safe_regular_file(gitignore)
    changed = False
    if hooks_path.exists():
        config = load_hook_config(hooks_path)
        changed = _remove_codex_hooks_config(config)
        if changed:
            if config:
                _atomic_write_text(hooks_path, json.dumps(config, indent=2) + "\n")
            else:
                hooks_path.unlink()
                _remove_gitignore_entry(gitignore, "hooks.json")
            print("  Removed deprecated project-local Mycelium Codex hooks.")

    if codex_dir.exists() and not any(codex_dir.iterdir()):
        codex_dir.rmdir()

    print("  Codex hooks are bundled with the Mycelium plugin via PLUGIN_ROOT.")
    print(
        "  Codex action required after plugin install or upgrade: in a current "
        "CLI (not the desktop app), open /hooks, trust all five Mycelium hooks, "
        "exit Codex, and restart it. If /hooks is absent, run codex update first."
    )
    return changed


def create_environments_file(target_dir: Path):
    """Create ENVIRONMENTS_INSTALLATIONS.md at repo root."""
    env_path = target_dir / "ENVIRONMENTS_INSTALLATIONS.md"
    if _write_text_if_missing(
        env_path,
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
            "<!-- Add system-level dependencies here. -->\n",
    ):
        print("  Created: ENVIRONMENTS_INSTALLATIONS.md")


def audit_existing_structure(target_dir: Path) -> dict:
    """Audit an existing repo and report what needs to change."""
    # Directories to skip entirely during traversal
    SKIP_DIRS = {
        ".git",
        "__pycache__",
        ".venv",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
    }

    # Extension sets for classification
    DATA_EXTS = {
        ".csv",
        ".tsv",
        ".parquet",
        ".h5",
        ".h5ad",
        ".hdf5",
        ".zarr",
        ".npy",
        ".npz",
        ".feather",
        ".arrow",
        ".xlsx",
        ".xls",
        ".fasta",
        ".fastq",
        ".bam",
        ".bed",
        ".vcf",
        ".gff",
        ".gtf",
        ".mzML",
        ".mzXML",
    }
    SCRIPT_EXTS = {".py", ".R", ".Rmd", ".ipynb", ".jl"}
    DOC_EXTS = {".md", ".rst", ".txt", ".pdf", ".docx"}
    ALGORITHM_DIR_NAMES = {"methods", "utils", "lib", "tools", "algorithms"}

    # Mycelium-managed top-level directories (already placed)
    PLACED_PREFIXES = {
        "data",
        "analysis",
        "algorithms",
        "reference_material",
        ".living",
        "todo",
    }

    # Sub-classification hints for data files
    PROCESSED_HINTS = {"processed", "clean", "filtered", "normalized"}
    META_HINTS = {"meta", "metadata"}

    def classify_data_destination(path: Path) -> str:
        parts_lower = {p.lower() for p in path.parts}
        if parts_lower & META_HINTS:
            return "data_metadata"
        if parts_lower & PROCESSED_HINTS:
            return "data_processed"
        return "data_raw"

    def get_analysis_group(path: Path) -> str:
        """Group analysis scripts by parent directory name or file stem."""
        parent = path.parent.name
        if parent and parent not in {".", ""} and parent != target_dir.name:
            return parent
        return path.stem

    def get_algorithm_group(path: Path) -> str:
        parent = path.parent.name
        if parent and parent not in {".", ""} and parent != target_dir.name:
            return parent
        return path.stem

    # Accumulate results
    plan: dict = {
        "data_raw": [],
        "data_processed": [],
        "data_metadata": [],
        "analysis": {},
        "reference_material": [],
        "algorithms": {},
        "already_placed": [],
        "unclassified": [],
        "total_scanned": 0,
        "total_moves": 0,
    }

    print("  Auditing existing structure...")

    for path in sorted(target_dir.rglob("*")):
        if not path.is_file():
            continue

        # Skip hidden directories and known noise dirs
        rel = path.relative_to(target_dir)
        parts = rel.parts
        if any(p.startswith(".") and p not in {".living"} for p in parts[:-1]):
            continue
        if any(p in SKIP_DIRS for p in parts):
            continue

        plan["total_scanned"] += 1
        ext = path.suffix.lower()
        top_level = parts[0] if len(parts) > 1 else ""

        # Already placed inside mycelium structure
        if top_level in PLACED_PREFIXES:
            plan["already_placed"].append(str(rel))
            continue

        # Data files
        if ext in DATA_EXTS:
            dest = classify_data_destination(rel)
            suggested = f"{dest.replace('_', '/')}/{path.name}"
            plan[dest].append((str(rel), suggested))
            continue

        # Algorithm/method files: .py in algorithm-named dirs
        if ext == ".py" and top_level.lower() in ALGORITHM_DIR_NAMES:
            group = get_algorithm_group(rel)
            plan["algorithms"].setdefault(group, []).append(str(rel))
            continue

        # Analysis scripts
        if ext in SCRIPT_EXTS:
            # Skip setup.py and similar repo-level Python files at root
            if (
                len(parts) == 1
                and ext == ".py"
                and path.stem in {"setup", "conftest", "noxfile"}
            ):
                plan["unclassified"].append(str(rel))
                continue
            group = get_analysis_group(rel)
            plan["analysis"].setdefault(group, []).append(str(rel))
            continue

        # Documentation
        if ext in DOC_EXTS:
            # Skip top-level READMEs and changelogs
            if len(parts) == 1 and path.stem.upper() in {
                "README",
                "CHANGELOG",
                "LICENSE",
                "CONTRIBUTING",
                "AUTHORS",
            }:
                plan["unclassified"].append(str(rel))
                continue
            plan["reference_material"].append(
                (str(rel), f"reference_material/{path.name}")
            )
            continue

        # Everything else
        plan["unclassified"].append(str(rel))

    # Compute total_moves
    moves = (
        len(plan["data_raw"])
        + len(plan["data_processed"])
        + len(plan["data_metadata"])
        + sum(len(v) for v in plan["analysis"].values())
        + len(plan["reference_material"])
        + sum(len(v) for v in plan["algorithms"].values())
    )
    plan["total_moves"] = moves

    # --- Print structured report ---
    print("\n=== Audit Report ===")

    # Data files
    data_count = (
        len(plan["data_raw"]) + len(plan["data_processed"]) + len(plan["data_metadata"])
    )
    print(f"\nDATA FILES ({data_count} files → data/)")
    for bucket, label in [
        ("data_raw", "data/raw/"),
        ("data_processed", "data/processed/"),
        ("data_metadata", "data/metadata/"),
    ]:
        if plan[bucket]:
            print(f"  {label}:")
            for current, _ in plan[bucket]:
                parent = (
                    str(Path(current).parent)
                    if str(Path(current).parent) != "."
                    else "root"
                )
                print(f"    - {current} (currently: {parent})")

    # Analysis scripts
    script_count = sum(len(v) for v in plan["analysis"].values())
    print(f"\nANALYSIS SCRIPTS ({script_count} files → analysis/)")
    for group, files in sorted(plan["analysis"].items()):
        print(f"  analysis/{group}/:")
        for f in files:
            parent = str(Path(f).parent) if str(Path(f).parent) != "." else "root"
            print(f"    - {f} (currently: {parent})")

    # Reference material
    ref_count = len(plan["reference_material"])
    print(f"\nREFERENCE MATERIAL ({ref_count} files → reference_material/)")
    for current, _ in plan["reference_material"]:
        parent = (
            str(Path(current).parent) if str(Path(current).parent) != "." else "root"
        )
        print(f"    - {current} (currently: {parent})")

    # Algorithms
    algo_count = sum(len(v) for v in plan["algorithms"].values())
    print(f"\nALGORITHMS ({algo_count} files → algorithms/)")
    for group, files in sorted(plan["algorithms"].items()):
        print(f"  algorithms/{group}/:")
        for f in files:
            parent = str(Path(f).parent) if str(Path(f).parent) != "." else "root"
            print(f"    - {f} (currently: {parent})")

    # Already placed
    placed_count = len(plan["already_placed"])
    print(f"\nALREADY IN PLACE ({placed_count} files)")
    for f in plan["already_placed"]:
        print(f"    - {f}")

    # Unclassified
    unclass_count = len(plan["unclassified"])
    print(f"\nUNCLASSIFIED ({unclass_count} files)")
    for f in plan["unclassified"]:
        print(f"    - {f}")

    print(
        f"\nSummary: {plan['total_scanned']} files scanned, "
        f"{plan['total_moves']} would be moved, "
        f"{placed_count} already placed, "
        f"{unclass_count} unclassified"
    )

    return plan


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print(f"Mycelium Init — Target: {target_dir}")
    print("=" * 50)

    if args.restructure:
        print("\nMode: Restructure existing repository")
        plan = audit_existing_structure(target_dir)
        print(
            f"\nRestructure plan: {plan['total_moves']} files to move, {len(plan['unclassified'])} unclassified."
        )
        print("\nRestructure mode requires user confirmation before proceeding.")
        print("TODO: Implement interactive restructure workflow")
        return

    if check_existing_structure(target_dir):
        print("\nThis repo already has a mycelium structure.")
        print(
            "Use --restructure to audit and update, or remove .living/ to start fresh."
        )
        sys.exit(1)

    preflight_initialization(target_dir)

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

    print("\nCreating agent guidance...")
    create_agent_guidance(target_dir)

    print("\nInstalling core convention packs...")
    install_core_convention_packs(target_dir)

    print("\nSetting up skillpacks directory...")
    create_skillpacks(target_dir)

    print("\nInstalling Claude Code hooks...")
    install_claude_hooks(target_dir)

    print("\nConfiguring Codex hook compatibility...")
    install_codex_hooks(target_dir)

    print("\n" + "=" * 50)
    print("Mycelium initialization complete!")
    print("\nNext steps:")
    print("  1. Review MYCELIUM.md, CLAUDE.md, and AGENTS.md")
    print(
        "  2. Install domain conventions if needed "
        "(/mycelium:core or $mycelium:core)"
    )
    print("  3. Run validate_structure.py to confirm setup")
    print("  4. Start working — the repo is now alive!")


if __name__ == "__main__":
    main()
