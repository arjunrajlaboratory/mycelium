#!/usr/bin/env python3
"""Build cross-project Scientific Findings INDEX.md.

Scans all subproject .living/findings/ directories under a meta-project root,
aggregates findings by topic, and writes {meta-project}/.living/findings/INDEX.md.

Usage:
    python crystallize_findings.py [--meta-root PATH]

If --meta-root is not provided, walks up from cwd looking for a parent directory
with .living/ to discover the meta-project root.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


def find_meta_root(start: Path) -> Path | None:
    """Walk up from start looking for a parent directory with .living/."""
    check = start.parent
    while check != check.parent:
        if (check / ".living").is_dir():
            return check
        check = check.parent
    return None


def find_repo_root(start: Path) -> Path | None:
    """Find git repo root from start directory."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def parse_topic_file(path: Path) -> dict:
    """Parse a topic file and extract metadata."""
    content = path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # Parse frontmatter
    topic = path.stem
    description = ""
    last_updated = ""
    status = "active"

    in_frontmatter = False
    for line in lines:
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            else:
                break
        if in_frontmatter:
            if line.startswith("topic:"):
                topic = line.split(":", 1)[1].strip()
            elif line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
            elif line.startswith("last_updated:"):
                last_updated = line.split(":", 1)[1].strip()
            elif line.startswith("status:"):
                status = line.split(":", 1)[1].strip()

    # Count findings by ## F- headers
    finding_count = len([l for l in lines if l.startswith("## F-")])

    # Extract statuses from **Status:** lines
    statuses = []
    for line in lines:
        match = re.match(r"\*\*Status:\*\*\s*(\w+)", line)
        if match:
            statuses.append(match.group(1).lower())

    # Derive strongest status
    status_order = ["contradicted", "robust", "supported", "preliminary"]
    strongest = "preliminary"
    for s in status_order:
        if s in statuses:
            strongest = s
            break

    # Count open questions
    open_questions = 0
    in_questions = False
    for line in lines:
        if line.startswith("### Open Questions"):
            in_questions = True
            continue
        if in_questions:
            if line.startswith("#") or line.startswith("---"):
                in_questions = False
            elif line.strip().startswith("- "):
                open_questions += 1

    return {
        "topic": topic,
        "description": description,
        "finding_count": finding_count,
        "strongest": strongest,
        "open_questions": open_questions,
        "last_updated": last_updated,
        "status": status,
    }


def _find_subproject_findings_dirs(meta_root: Path) -> list[tuple[Path, str]]:
    """Find all subproject findings directories, filtering hidden/irrelevant paths.

    Returns list of (findings_dir, project_name) tuples.
    Only includes direct children of meta_root to avoid misattribution.
    """
    results = []
    for findings_dir in sorted(meta_root.rglob(".living/findings")):
        if not findings_dir.is_dir():
            continue
        # Filter hidden directories and known irrelevant paths
        relative = findings_dir.relative_to(meta_root)
        if any(part.startswith(".") and part != ".living" for part in relative.parts):
            continue
        if any(
            part in {".git", ".venv", "__pycache__", "node_modules"}
            for part in relative.parts
        ):
            continue
        # findings_dir is {project}/.living/findings
        project_root = findings_dir.parent.parent
        # Skip meta-project's own findings dir
        if project_root == meta_root:
            continue
        # Only include direct children of meta_root
        if project_root.parent != meta_root:
            continue
        results.append((findings_dir, project_root.name))
    return results


def build_cross_project_index(meta_root: Path) -> str:
    """Scan all subprojects and build the cross-project INDEX.md content."""
    # Find all subproject findings directories (cached for reuse in staleness check)
    topic_data: dict[str, dict] = {}  # topic_slug -> aggregated data
    subproject_dirs = _find_subproject_findings_dirs(meta_root)

    for findings_dir, project_name in subproject_dirs:
        for topic_file in sorted(findings_dir.glob("*.md")):
            if topic_file.name in {"INDEX.md", "FINDINGS_REGISTRY.md"}:
                continue

            info = parse_topic_file(topic_file)
            slug = topic_file.stem

            if slug not in topic_data:
                topic_data[slug] = {
                    "description": info["description"],
                    "projects": [],
                    "total_findings": 0,
                    "strongest": info["strongest"],
                    "open_questions": 0,
                }

            entry = topic_data[slug]
            if project_name not in entry["projects"]:
                entry["projects"].append(project_name)
            entry["total_findings"] += info["finding_count"]
            entry["open_questions"] += info["open_questions"]

            # Update strongest (contradicted > robust > supported > preliminary)
            order = {"contradicted": 0, "robust": 1, "supported": 2, "preliminary": 3}
            if order.get(info["strongest"], 3) < order.get(entry["strongest"], 3):
                entry["strongest"] = info["strongest"]

            # Use longer description if current is empty
            if not entry["description"] and info["description"]:
                entry["description"] = info["description"]

    if not topic_data:
        return ""

    # --- Staleness detection (90-day threshold) ---
    stale_threshold = 90
    now_ts = datetime.now()
    for slug, data in topic_data.items():
        # Check all topic files for this slug across projects (reuse cached dirs)
        all_stale = True
        for findings_dir, _ in subproject_dirs:
            topic_file = findings_dir / f"{slug}.md"
            if topic_file.exists():
                age_days = (
                    now_ts - datetime.fromtimestamp(topic_file.stat().st_mtime)
                ).days
                if age_days < stale_threshold:
                    all_stale = False
                    break
        if all_stale:
            data["stale"] = True

    # Build INDEX.md content
    now = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")

    lines = [
        "# Scientific Findings Index",
        "",
        "> Cross-project registry of all scientific findings. Read full topic files on demand.",
        "",
        "| Topic | Description | Projects | Findings | Strongest | Open Questions |",
        "|-------|-------------|----------|----------|-----------|----------------|",
    ]

    for slug in sorted(topic_data.keys()):
        data = topic_data[slug]
        projects = ", ".join(data["projects"])
        desc = data["description"] or "(no description)"
        stale_marker = " (STALE)" if data.get("stale") else ""
        lines.append(
            f"| {slug}{stale_marker} | {desc} | {projects} | {data['total_findings']} "
            f"| {data['strongest']} | {data['open_questions']} |"
        )

    lines.extend(["", f"Last rebuilt: {now}"])
    return "\n".join(lines) + "\n"


def rebuild_project_registry(findings_dir: Path) -> None:
    """Rebuild FINDINGS_REGISTRY.md for a single project's findings directory.

    Scans all topic files, extracts per-finding metadata, and writes/overwrites
    .living/findings/FINDINGS_REGISTRY.md with a sorted table of all findings.
    """
    rows: list[dict] = []

    for topic_file in sorted(findings_dir.glob("*.md")):
        if topic_file.name in {"INDEX.md", "FINDINGS_REGISTRY.md"}:
            continue

        topic_slug = topic_file.stem
        content = topic_file.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()

        # Split into per-finding sections by ## F-NNN headers
        current_id: str | None = None
        current_lines: list[str] = []
        sections: list[tuple[str, list[str]]] = []

        for line in lines:
            match = re.match(r"^## (F-\d+)", line)
            if match:
                if current_id is not None:
                    sections.append((current_id, current_lines))
                current_id = match.group(1)
                current_lines = []
            elif current_id is not None:
                current_lines.append(line)

        if current_id is not None:
            sections.append((current_id, current_lines))

        for finding_id, finding_lines in sections:
            claim = ""
            status = "preliminary"
            implications = ""
            tags = ""
            last_evidence_date = ""

            in_evidence = False
            for line in finding_lines:
                claim_match = re.match(r"\*\*Claim:\*\*\s*(.*)", line)
                if claim_match:
                    claim = claim_match.group(1).strip()

                status_match = re.match(r"\*\*Status:\*\*\s*(\w+)", line)
                if status_match:
                    status = status_match.group(1).lower()

                impl_match = re.match(r"\*\*Implications:\*\*\s*(.*)", line)
                if impl_match:
                    implications = impl_match.group(1).strip()

                tags_match = re.match(r"\*\*Tags:\*\*\s*(.*)", line)
                if tags_match:
                    tags = tags_match.group(1).strip()

                # Detect Evidence Ledger table rows (| date | ... |)
                if line.startswith("### Evidence Ledger"):
                    in_evidence = True
                    continue
                if in_evidence and line.startswith("#"):
                    in_evidence = False
                if in_evidence:
                    # Match table data rows: | YYYY-MM-DD | ...
                    date_match = re.match(r"\|\s*(\d{4}-\d{2}-\d{2})\s*\|", line)
                    if date_match:
                        last_evidence_date = date_match.group(1)

            rows.append(
                {
                    "id": finding_id,
                    "claim": claim or "(no claim)",
                    "status": status,
                    "topic_slug": topic_slug,
                    "implications": implications or "",
                    "tags": tags or "",
                    "last_updated": last_evidence_date,
                }
            )

    if not rows:
        return

    # Sort by finding ID numerically
    rows.sort(key=lambda r: int(r["id"].split("-")[1]))

    template_header = (
        "# Findings Registry\n"
        "\n"
        "> Per-project index of all scientific findings."
        " See topic files for full evidence ledgers.\n"
        "\n"
        "| ID | Claim | Status | Topic | Implications | Tags | Last Updated |\n"
        "|----|-------|--------|-------|--------------|------|--------------|"
    )

    table_rows = []
    for r in rows:
        table_rows.append(
            f"| {r['id']} | {r['claim']} | {r['status']}"
            f" | [{r['topic_slug']}]({r['topic_slug']}.md)"
            f" | {r['implications']} | {r['tags']} | {r['last_updated']} |"
        )

    registry_path = findings_dir / "FINDINGS_REGISTRY.md"
    registry_path.write_text(
        template_header + "\n" + "\n".join(table_rows) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build cross-project findings INDEX.md"
    )
    parser.add_argument("--meta-root", type=Path, help="Meta-project root directory")
    parser.add_argument(
        "--project-root",
        type=Path,
        help="Current project root (used to find meta-root if not specified)",
    )
    args = parser.parse_args()

    # Determine meta-project root
    meta_root = args.meta_root
    if meta_root is None:
        start = args.project_root or Path.cwd()
        repo_root = find_repo_root(start)
        if repo_root:
            meta_root = find_meta_root(repo_root)

    if meta_root is None:
        # Standalone project — no cross-project index needed
        print("No meta-project found. Cross-project index not generated.")
        return 0

    # Ensure meta-project findings directory exists
    findings_dir = meta_root / ".living" / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)

    # Build and write index
    content = build_cross_project_index(meta_root)
    if not content:
        print("No findings found across subprojects.")
        return 0

    index_path = findings_dir / "INDEX.md"
    index_path.write_text(content, encoding="utf-8")
    print(f"INDEX.md written to {index_path}")

    # Rebuild per-project FINDINGS_REGISTRY.md for each subproject
    subproject_dirs = _find_subproject_findings_dirs(meta_root)
    for subproject_findings_dir, project_name in subproject_dirs:
        rebuild_project_registry(subproject_findings_dir)
        print(f"FINDINGS_REGISTRY.md rebuilt for {project_name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
