#!/usr/bin/env python3
"""Initialize a mycelium-enabled living repository.

Scaffolds the directory structure, manifests, and living layer
for a new or existing repository. Creates all required directories,
empty manifests, and the .living/ memory layer.

Usage:
    python init_repo.py [--target-dir PATH] [--restructure]
"""

import argparse
import sys
from pathlib import Path


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
        ".living/skills",
        ".living/generated-skills",
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


def create_manifests(target_dir: Path):
    """Create empty MANIFEST.md files in each top-level directory."""
    manifest_dirs = ["algorithms", "analysis", "data", "reference_material"]

    for dir_name in manifest_dirs:
        manifest_path = target_dir / dir_name / "MANIFEST.md"
        if not manifest_path.exists():
            manifest_path.write_text(
                f"# {dir_name.replace('_', ' ').title()} Manifest\n\n"
                "<!-- Add entries below using the appropriate manifest entry template. -->\n"
            )
            print(f"  Created: {dir_name}/MANIFEST.md")


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
            "Overrides to mycelium defaults or domain skill conventions.\n\n"
            "<!-- Document any project-specific convention overrides here. -->\n"
        ),
    }

    for filename, content in files.items():
        file_path = living_dir / filename
        if not file_path.exists():
            file_path.write_text(content)
            print(f"  Created: .living/{filename}")

    # Create ACTIVE_SKILLS.yaml
    skills_yaml = living_dir / "skills" / "ACTIVE_SKILLS.yaml"
    if not skills_yaml.exists():
        skills_yaml.write_text(
            "# Active Domain Skills\n"
            "# Updated by install_domain_skill.py\n\n"
            "active_skills: []\n"
        )
        print("  Created: .living/skills/ACTIVE_SKILLS.yaml")


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
        print("Use --restructure to audit and update, or remove .living/ to start fresh.")
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

    print("\n" + "=" * 50)
    print("Mycelium initialization complete!")
    print("\nNext steps:")
    print("  1. Generate CLAUDE.md from the template")
    print("  2. Install domain skills if needed (install-skill mode)")
    print("  3. Run validate_structure.py to confirm setup")
    print("  4. Start working — the repo is now alive!")


if __name__ == "__main__":
    main()
