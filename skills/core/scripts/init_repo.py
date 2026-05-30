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
        ".living/findings",
        ".living/outputs",
        ".living/outputs/knowledge-transfers",
        ".living/skills",
        "algorithms",
        "analysis",
        "data",
        "data/raw",
        "data/processed",
        "data/metadata",
        "reference_material",
        "skillpacks",
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
    """Create descriptive manifest files in each top-level directory.

    Also drops a `_README_TEMPLATE.md` into algorithms/ and analysis/ so
    new entries have a concrete starting point.
    """
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

    # Drop README templates so new analyses/algorithms have a concrete start
    templates_dir = Path(__file__).resolve().parent.parent / "templates"
    for src_name, target_subdir in (
        ("algorithm-readme.md", "algorithms"),
        ("analysis-readme.md", "analysis"),
    ):
        src = templates_dir / src_name
        dst = target_dir / target_subdir / "_README_TEMPLATE.md"
        if src.exists() and not dst.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"  Created: {target_subdir}/_README_TEMPLATE.md")


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


def create_skillpacks(target_dir: Path):
    """Create the skillpacks/ directory with .gitignore and README.

    Skill packs are external git repos cloned into skillpacks/ for use by
    the skill-bridge convention. They are NOT installed as agent skill packs —
    they sit inert on disk and are read on demand by convention-routed workflows.
    """
    skillpacks_dir = target_dir / "skillpacks"
    skillpacks_dir.mkdir(exist_ok=True)

    gitignore_path = skillpacks_dir / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text(
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
            "!README.md\n"
        )
        print("  Created: skillpacks/.gitignore")

    readme_path = skillpacks_dir / "README.md"
    if not readme_path.exists():
        readme_path.write_text(
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
            "(~150-200 lines per analysis step), never loading the full repos into context.\n"
        )
        print("  Created: skillpacks/README.md")


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


MYCELIUM_HOOK_BASENAMES = {
    "mycelium-health.sh",
    "mycelium-post-action.sh",
    "mycelium-stop-check.sh",
    "mycelium-activity-tracker.sh",
    "mycelium-read-tracker.sh",
    "mycelium-data-tracker.sh",
    "mycelium-data-lineage-stop.sh",
}


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
                bn = Path(cmd).name
                if bn in MYCELIUM_HOOK_BASENAMES:
                    basename_to_cmds.setdefault(bn, []).append(cmd)

            # Pick canonical for each basename
            canonical: dict[str, str] = {}
            droppable_stale: set[str] = set()
            for bn, cmds in basename_to_cmds.items():
                live = [c for c in cmds if Path(c).exists()]
                stale = [c for c in cmds if not Path(c).exists()]

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
                bn = Path(cmd).name
                if bn in canonical and cmd != canonical[bn]:
                    removed += 1
                    continue
                if cmd in droppable_stale and bn not in canonical:
                    removed += 1
                    continue
                new_hook_list.append(h)
            entry["hooks"] = new_hook_list

    return removed, kept_by_basename


def install_claude_hooks(target_dir: Path):
    """Create or update .claude/settings.local.json with mycelium hooks.

    Two-pass:
    1. Consolidate any pre-existing duplicate entries (same script, different
       paths — e.g. marketplace + dev-repo). Prefers marketplace path.
    2. Install any of the 5 mycelium hooks that are missing entirely. Match
       by script *basename* not full path so a re-run with a different
       hooks-dir does not double-install.

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
    data_lineage_stop_hook = str(hooks_dir / "mycelium-data-lineage-stop.sh")

    def _hook_entry(cmd: str) -> dict:
        return {"type": "command", "command": cmd}

    def _has_hook(hook_list: list, basename: str) -> bool:
        """Check if any entry registers the named script (path-agnostic)."""
        return any(
            Path(h.get("command", "")).name == basename
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
    # Logs each .living/ file read to .claude/mycelium-read-access.log so we
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
    # script to .claude/mycelium-data-events.tmp under fcntl.flock. Consumed
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

    # --- Stop: mycelium-data-lineage-stop.sh ---
    # Consolidates per-session data lineage events into a manifest.
    if not _has_hook(stop, "mycelium-data-lineage-stop.sh"):
        catch_all = next((e for e in stop if e.get("matcher", "") == ""), None)
        if catch_all is None:
            catch_all = {"matcher": "", "hooks": []}
            stop.append(catch_all)
        catch_all["hooks"].append(_hook_entry(data_lineage_stop_hook))
        print("  Registered: Stop → mycelium-data-lineage-stop.sh")

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

    print("\nSetting up skillpacks directory...")
    create_skillpacks(target_dir)

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
