#!/usr/bin/env python3
"""Idempotent backfill for repos initialized on earlier mycelium versions.

Performs four upgrade actions per target repo:

1. **CLAUDE.md re-anchor** — inserts a "Knowledge index" callout pointing
   at `.living/INDEX.md` if no INDEX.md reference is present.
2. **Hook top-up** — adds any of the 5-hook default bundle that the repo
   is missing, preserving existing hook entries (no duplicates).
3. **INDEX.md regen** — runs `generate_index.py --summary-heuristic` so
   the freshly-anchored INDEX.md actually has cluster content.
4. **MEMORY.md routing** — appends the Global Knowledge Domains routing
   table to `~/.claude/projects/*/memory/MEMORY.md` files.

All actions are idempotent: re-running on an already-migrated repo is a
no-op (each action prints "skipped" instead of "applied").

Usage:
    migrate_existing_repos.py --repo /path/to/repo
    migrate_existing_repos.py --scan /Users/x/code  # finds all .living/ repos
    migrate_existing_repos.py --repo /path --dry-run
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Bring in helpers from sibling scripts.
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import init_knowledge as ik  # noqa: E402
import init_repo as ir  # noqa: E402

# The "Knowledge index" callout that gets inserted into CLAUDE.md if missing.
# Single-block insertion is safer than rewriting Quick Orientation in
# repos with heavily-customized CLAUDE.md files.
KNOWLEDGE_INDEX_CALLOUT = """\
> **Knowledge index (read first):** [`.living/INDEX.md`](.living/INDEX.md) is an auto-generated map of tag clusters, most-recent entries, and a tag → entry-ID inverted index. The SessionStart hook keeps it fresh — trust it. For targeted lookup: `python3 skills/core/scripts/recall_lessons.py --living-dir .living/ --tag <tag>` (also `--id L-42`, `--since YYYY-MM-DD`).
"""

# Markers used to detect "already migrated" — any of these strings means
# the CLAUDE.md was upgraded and the callout should be skipped.
INDEX_MENTIONED_MARKERS = (
    ".living/INDEX.md",
    "Knowledge index (read first)",
)


def _action_status(applied: bool) -> str:
    return "applied" if applied else "skipped (already up-to-date)"


def reanchor_claude_md(repo_path: Path, dry_run: bool = False) -> bool:
    """Insert the Knowledge index callout into CLAUDE.md.

    Returns True if applied, False if no-op or CLAUDE.md missing.
    """
    claude_md = repo_path / "CLAUDE.md"
    if not claude_md.exists():
        return False

    content = claude_md.read_text(encoding="utf-8")
    if any(marker in content for marker in INDEX_MENTIONED_MARKERS):
        return False

    # Insert the callout right after the first "## Quick Orientation" header
    # if present, otherwise after the first H1.
    lines = content.splitlines(keepends=True)
    insert_idx: int | None = None

    for i, line in enumerate(lines):
        if line.strip().startswith("## Quick Orientation"):
            # Find the next blank line after the header
            for j in range(i + 1, min(i + 5, len(lines))):
                if lines[j].strip() == "":
                    insert_idx = j + 1
                    break
            else:
                insert_idx = i + 1
            break

    if insert_idx is None:
        # Fallback: after the first H1
        for i, line in enumerate(lines):
            if line.startswith("# "):
                # Find next blank line
                for j in range(i + 1, min(i + 5, len(lines))):
                    if lines[j].strip() == "":
                        insert_idx = j + 1
                        break
                else:
                    insert_idx = i + 1
                break

    if insert_idx is None:
        # Empty/atypical CLAUDE.md — prepend
        insert_idx = 0

    new_lines = (
        lines[:insert_idx]
        + [KNOWLEDGE_INDEX_CALLOUT, "\n"]
        + lines[insert_idx:]
    )
    new_content = "".join(new_lines)

    if not dry_run:
        claude_md.write_text(new_content, encoding="utf-8")
    return True


def topup_hooks(repo_path: Path, dry_run: bool = False) -> bool:
    """Top up missing hooks in .claude/settings.local.json.

    Reuses init_repo.install_claude_hooks which is already idempotent.
    Returns True if any hook was added, False if all 5 were already present.
    """
    settings_path = repo_path / ".claude" / "settings.local.json"
    before_signature = ""
    if settings_path.exists():
        before_signature = json.dumps(
            json.loads(settings_path.read_text()).get("hooks", {}),
            sort_keys=True,
        )

    if dry_run:
        # We'd need to simulate without writing — easiest is to return
        # whether the bundle is incomplete by inspecting what's there.
        if not settings_path.exists():
            return True
        existing = json.loads(settings_path.read_text())
        hook_cmds = {
            h.get("command", "")
            for entries in existing.get("hooks", {}).values()
            for entry in entries
            for h in entry.get("hooks", [])
        }
        required = {
            "mycelium-health.sh",
            "mycelium-post-action.sh",
            "mycelium-stop-check.sh",
            "mycelium-activity-tracker.sh",
            "mycelium-read-tracker.sh",
        }
        present = {Path(c).name for c in hook_cmds}
        return bool(required - present)

    # Actual install: reuse init_repo's idempotent installer
    ir.install_claude_hooks(repo_path)
    after_signature = json.dumps(
        json.loads(settings_path.read_text()).get("hooks", {}),
        sort_keys=True,
    )
    return before_signature != after_signature


def regen_index(repo_path: Path, dry_run: bool = False) -> bool:
    """Run generate_index.py --summary-heuristic on .living/.

    Returns True if regenerated, False if no .living/ dir or dry-run.
    """
    living_dir = repo_path / ".living"
    if not living_dir.is_dir():
        return False
    if dry_run:
        return True

    script = _SCRIPT_DIR / "generate_index.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--living-dir",
            str(living_dir),
            "--summary-heuristic",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def append_memory_routing(dry_run: bool = False) -> tuple[int, int]:
    """Append the routing table to MEMORY.md files.

    Returns (appended, skipped). Dry-run reports without writing.
    """
    mycelium_root = (_SCRIPT_DIR / ".." / ".." / "..").resolve()
    if dry_run:
        # Count what we WOULD append by inspecting each file
        projects_dir = Path.home() / ".claude" / "projects"
        candidates = ik._glob_memory_files(projects_dir)
        appended = sum(
            1
            for p in candidates
            if ik.MEMORY_ROUTING_HEADER not in p.read_text(encoding="utf-8")
        )
        return (appended, len(candidates) - appended)
    return ik.append_routing_to_memory_files(mycelium_root)


def migrate_one(repo_path: Path, dry_run: bool = False) -> dict[str, str]:
    """Run all four upgrade actions on a single repo.

    Returns a dict of action → status string.
    """
    repo_path = repo_path.resolve()
    if not (repo_path / ".living").is_dir():
        return {"_skip": f"no .living/ at {repo_path}"}

    claude_md_applied = reanchor_claude_md(repo_path, dry_run=dry_run)
    hooks_applied = topup_hooks(repo_path, dry_run=dry_run)
    index_applied = regen_index(repo_path, dry_run=dry_run)

    return {
        "CLAUDE.md re-anchor": _action_status(claude_md_applied),
        "Hooks top-up": _action_status(hooks_applied),
        "INDEX.md regen": _action_status(index_applied),
    }


def scan_for_repos(scan_root: Path) -> list[Path]:
    """Find all repos with `.living/` directories under scan_root (depth 1)."""
    if not scan_root.is_dir():
        return []
    repos: list[Path] = []
    for child in sorted(scan_root.iterdir()):
        if child.is_dir() and (child / ".living").is_dir():
            repos.append(child)
    return repos


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotent migration for repos started on earlier mycelium "
            "versions. Re-anchors CLAUDE.md, tops up hooks, regenerates "
            "INDEX.md SUMMARY block, and appends MEMORY.md routing tables."
        )
    )
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument(
        "--repo",
        type=Path,
        help="Migrate one repo by absolute path.",
    )
    target_group.add_argument(
        "--scan",
        type=Path,
        help="Scan a parent directory for child dirs containing .living/ and migrate each.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report intended changes without writing.",
    )
    parser.add_argument(
        "--skip-memory",
        action="store_true",
        help="Skip the MEMORY.md routing append step (per-repo migration only).",
    )
    args = parser.parse_args()

    repos: list[Path] = []
    if args.repo:
        repos = [args.repo.expanduser().resolve()]
    elif args.scan:
        repos = scan_for_repos(args.scan.expanduser().resolve())
        if not repos:
            print(f"No repos with .living/ found under {args.scan}", file=sys.stderr)
            sys.exit(1)

    print(f"Migrating {len(repos)} repo(s){' (dry-run)' if args.dry_run else ''}")
    print()

    for repo in repos:
        print(f"=== {repo} ===")
        result = migrate_one(repo, dry_run=args.dry_run)
        if "_skip" in result:
            print(f"  Skipped: {result['_skip']}")
        else:
            for action, status in result.items():
                print(f"  {action}: {status}")
        print()

    if not args.skip_memory:
        print("=== MEMORY.md routing (global) ===")
        appended, skipped = append_memory_routing(dry_run=args.dry_run)
        prefix = "would append" if args.dry_run else "appended"
        print(f"  {prefix} {appended}, skipped {skipped} (already present)")


if __name__ == "__main__":
    main()
