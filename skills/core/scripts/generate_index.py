#!/usr/bin/env python3
"""Generate .living/INDEX.md for a project.

Scans all .md files in the given .living/ directory and produces a summary
table with entry counts, last-modified dates, and key topics.
"""

import argparse
import os
import re
from datetime import datetime
from pathlib import Path


def count_headers_and_topics(path: Path, file_type: str) -> tuple[int, list[str]]:
    """Count relevant headers and extract top keywords from first 5 headers.

    Args:
        path: Path to the markdown file.
        file_type: One of 'learnings', 'decisions', 'conventions', 'other'.

    Returns:
        Tuple of (entry_count, keywords_list).
    """
    if file_type in ("learnings", "decisions"):
        header_prefix = "### "
    elif file_type == "conventions":
        header_prefix = "## "
    else:
        # For other files, prefer ### over ##; we collect both and count all
        header_prefix = None

    count = 0
    raw_headers: list[str] = []

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip()
            if header_prefix:
                if line.startswith(header_prefix):
                    count += 1
                    raw_headers.append(line[len(header_prefix) :])
            else:
                # other files: count both ## and ### but not #
                if line.startswith("### "):
                    count += 1
                    raw_headers.append(line[4:])
                elif line.startswith("## "):
                    count += 1
                    raw_headers.append(line[3:])

    keywords = _extract_keywords(raw_headers[:5])
    return count, keywords


def _extract_keywords(raw_headers: list[str]) -> list[str]:
    """Strip markdown formatting and dates from headers, return 3-5 topic words.

    Handles:
    - [YYYY-MM-DD] prefix
    - [domain-tag] prefix
    - **bold** markers
    - Leading # characters
    """
    keywords: list[str] = []
    date_re = re.compile(r"\[\d{4}-\d{2}-\d{2}\]")
    tag_re = re.compile(r"\[[^\]]+\]")
    bold_re = re.compile(r"\*\*([^*]+)\*\*")
    leading_hash_re = re.compile(r"^#+\s*")

    for header in raw_headers:
        # Remove date brackets
        cleaned = date_re.sub("", header)
        # Replace bold with bare text
        cleaned = bold_re.sub(r"\1", cleaned)
        # Remove any remaining bracket tags
        cleaned = tag_re.sub("", cleaned)
        # Remove leading hashes (shouldn't be present after split, but defensive)
        cleaned = leading_hash_re.sub("", cleaned)
        cleaned = cleaned.strip(" :-–—")

        if cleaned:
            keywords.append(cleaned)

    # Return up to 5, but at least return what we have
    return keywords[:5]


def last_modified(path: Path) -> str:
    """Return last-modified date of path formatted as YYYY-MM-DD."""
    mtime = os.path.getmtime(path)
    return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")


def classify_file(name: str) -> str:
    stem = Path(name).stem.lower()
    if stem == "learnings":
        return "learnings"
    if stem == "decisions":
        return "decisions"
    if stem == "conventions":
        return "conventions"
    return "other"


def entry_label(file_type: str, count: int) -> str:
    if file_type == "conventions":
        return f"{count} section{'s' if count != 1 else ''}"
    return f"{count} entr{'ies' if count != 1 else 'y'}"


def skills_section(living_dir: Path) -> str | None:
    """Return skills section string if .living/skills/ exists, else None."""
    skills_dir = living_dir / "skills"
    if not skills_dir.is_dir():
        return None

    entries = sorted(p.name for p in skills_dir.iterdir())
    if not entries:
        return (
            "## Local skills\nSee `.living/skills/` for project-specific skill packs.\n"
        )

    lines = [
        "## Local skills",
        "See `.living/skills/` for project-specific skill packs.",
        "",
    ]
    for entry in entries:
        lines.append(f"- `{entry}`")
    return "\n".join(lines) + "\n"


def generate_index(living_dir: Path) -> str:
    """Build and return INDEX.md content."""
    today = datetime.now().strftime("%Y-%m-%d")

    md_files = sorted(
        p for p in living_dir.glob("*.md") if p.name.lower() != "index.md"
    )

    rows: list[tuple[str, str, str, str]] = []

    for md_path in md_files:
        file_type = classify_file(md_path.name)
        line_count = sum(1 for _ in md_path.open(encoding="utf-8", errors="replace"))
        large_note = " (large — read selectively)" if line_count > 500 else ""

        count, keywords = count_headers_and_topics(md_path, file_type)
        label = entry_label(file_type, count) + large_note
        updated = last_modified(md_path)
        topics = ", ".join(keywords) if keywords else "—"

        rows.append((md_path.name, label, updated, topics))

    # Log directory stats
    log_dir = living_dir / "log"
    if log_dir.is_dir():
        log_files = [f for f in log_dir.glob("*.md") if f.name != "REGISTRY.md"]
        log_count = len(log_files)
        if log_count > 0:
            last_log = max(log_files, key=lambda f: f.stat().st_mtime)
            last_date = datetime.fromtimestamp(last_log.stat().st_mtime).strftime(
                "%Y-%m-%d"
            )

            # Count sessions per project from filenames
            project_counts: dict[str, int] = {}
            for f in log_files:
                parts = f.stem.split("-", 4)  # YYYY-MM-DD-NNN-slug
                if len(parts) >= 5:
                    slug = parts[4]
                    project_counts[slug] = project_counts.get(slug, 0) + 1

            project_summary = ", ".join(
                f"{slug} ({count})"
                for slug, count in sorted(project_counts.items(), key=lambda x: -x[1])
            )

            rows.append(("log/", f"{log_count} sessions", last_date, project_summary))

    # --- Findings directory stats ---
    findings_dir = living_dir / "findings"
    if findings_dir.is_dir():
        topic_files = [f for f in findings_dir.glob("*.md") if f.name != "INDEX.md"]
        topic_count = len(topic_files)
        if topic_count > 0:
            last_topic = max(topic_files, key=lambda f: f.stat().st_mtime)
            last_date = datetime.fromtimestamp(last_topic.stat().st_mtime).strftime(
                "%Y-%m-%d"
            )

            # Count findings across all topics by counting ## F- headers
            total_findings = 0
            topic_names = []
            for tf in sorted(
                topic_files, key=lambda f: f.stat().st_mtime, reverse=True
            ):
                content = tf.read_text(encoding="utf-8", errors="replace")
                finding_count = len(
                    [line for line in content.splitlines() if line.startswith("## F-")]
                )
                total_findings += finding_count
                topic_names.append(tf.stem)

            topic_summary = ", ".join(topic_names[:5])
            if len(topic_names) > 5:
                topic_summary += f", +{len(topic_names) - 5} more"

            rows.append(
                (
                    "findings/",
                    f"{total_findings} findings across {topic_count} topics",
                    last_date,
                    topic_summary,
                )
            )

    # Build table
    lines: list[str] = [
        "# .living/ Index",
        f"Last audit: {today}",
        "",
        "| File | Entries | Last updated | Key topics |",
        "|------|---------|--------------|------------|",
    ]
    for name, label, updated, topics in rows:
        lines.append(f"| {name} | {label} | {updated} | {topics} |")

    lines.append("")

    skills = skills_section(living_dir)
    if skills:
        lines.append(skills)

    # --- Rebuild cross-project findings INDEX.md if meta-project exists ---
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    script_dir = Path(__file__).parent
    crystallize_script = script_dir / "crystallize_findings.py"
    if crystallize_script.exists():
        subprocess.run(
            [
                sys.executable,
                str(crystallize_script),
                "--project-root",
                str(living_dir.parent),
            ],
            capture_output=True,
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate .living/INDEX.md for a project."
    )
    parser.add_argument(
        "--living-dir",
        required=True,
        type=Path,
        help="Path to the .living/ directory to scan.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated INDEX.md to stdout instead of writing it.",
    )
    args = parser.parse_args()

    living_dir: Path = args.living_dir.resolve()
    if not living_dir.is_dir():
        parser.error(f"--living-dir '{living_dir}' is not a directory.")

    content = generate_index(living_dir)

    if args.dry_run:
        print(content)
    else:
        index_path = living_dir / "INDEX.md"
        index_path.write_text(content, encoding="utf-8")
        print(f"Written: {index_path}")


if __name__ == "__main__":
    main()
