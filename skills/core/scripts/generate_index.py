#!/usr/bin/env python3
"""Generate .living/INDEX.md for a project.

Scans all .md files in the given .living/ directory and produces a summary
table with entry counts, last-modified dates, and key topics.
"""

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Sentinel constants for structured INDEX.md blocks
# ---------------------------------------------------------------------------
SUMMARY_BEGIN = "<!-- BEGIN KNOWLEDGE SUMMARY -->"
SUMMARY_END = "<!-- END KNOWLEDGE SUMMARY -->"
QUICK_REF_BEGIN = "<!-- BEGIN QUICK REFERENCE -->"
QUICK_REF_END = "<!-- END QUICK REFERENCE -->"


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
        log_files = [
            f
            for f in log_dir.glob("*.md")
            if f.name not in ("REGISTRY.md", "LOG_REGISTRY.md")
        ]
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
        topic_files = [
            f
            for f in findings_dir.glob("*.md")
            if f.name not in {"INDEX.md", "FINDINGS_REGISTRY.md"}
        ]
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


# ---------------------------------------------------------------------------
# --counts-only helpers
# ---------------------------------------------------------------------------


def _collect_rows(living_dir: Path) -> list[tuple[str, str, str, str]]:
    """Collect (name, label, updated, topics) rows for all tracked files/dirs."""
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
        log_files = [
            f
            for f in log_dir.glob("*.md")
            if f.name not in ("REGISTRY.md", "LOG_REGISTRY.md")
        ]
        log_count = len(log_files)
        if log_count > 0:
            last_log = max(log_files, key=lambda f: f.stat().st_mtime)
            last_date = datetime.fromtimestamp(last_log.stat().st_mtime).strftime(
                "%Y-%m-%d"
            )
            project_counts: dict[str, int] = {}
            for f in log_files:
                parts = f.stem.split("-", 4)
                if len(parts) >= 5:
                    slug = parts[4]
                    project_counts[slug] = project_counts.get(slug, 0) + 1
            project_summary = ", ".join(
                f"{slug} ({c})"
                for slug, c in sorted(project_counts.items(), key=lambda x: -x[1])
            )
            rows.append(("log/", f"{log_count} sessions", last_date, project_summary))

    # Findings directory stats
    findings_dir = living_dir / "findings"
    if findings_dir.is_dir():
        topic_files = [
            f
            for f in findings_dir.glob("*.md")
            if f.name not in {"INDEX.md", "FINDINGS_REGISTRY.md"}
        ]
        topic_count = len(topic_files)
        if topic_count > 0:
            last_topic = max(topic_files, key=lambda f: f.stat().st_mtime)
            last_date = datetime.fromtimestamp(last_topic.stat().st_mtime).strftime(
                "%Y-%m-%d"
            )
            total_findings = 0
            topic_names = []
            for tf in sorted(
                topic_files, key=lambda f: f.stat().st_mtime, reverse=True
            ):
                content = tf.read_text(encoding="utf-8", errors="replace")
                total_findings += len(
                    [ln for ln in content.splitlines() if ln.startswith("## F-")]
                )
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

    return rows


def build_quick_reference(living_dir: Path) -> str:
    """Build the Quick Reference table wrapped in sentinel markers.

    Args:
        living_dir: Path to the .living/ directory.

    Returns:
        String starting with QUICK_REF_BEGIN and ending with QUICK_REF_END.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    rows = _collect_rows(living_dir)

    lines: list[str] = [
        QUICK_REF_BEGIN,
        "# .living/ Index",
        f"Last audit: {today}",
        "",
        "| File | Entries | Last updated | Key topics |",
        "|------|---------|--------------|------------|",
    ]
    for name, label, updated, topics in rows:
        lines.append(f"| {name} | {label} | {updated} | {topics} |")

    skills = skills_section(living_dir)
    if skills:
        lines.append("")
        lines.append(skills.rstrip("\n"))

    lines.append(QUICK_REF_END)
    return "\n".join(lines)


def update_index_counts_only(living_dir: Path) -> None:
    """Update INDEX.md with fresh counts, preserving any existing summary block.

    - Fresh directory: creates minimal INDEX.md with QUICK_REFERENCE block only.
    - Existing with sentinels: replaces QUICK_REFERENCE block, preserves rest.
    - Legacy (no sentinels): replaces entire file (safe — auto-generated content).

    Args:
        living_dir: Path to the .living/ directory.
    """
    index_path = living_dir / "INDEX.md"
    quick_ref = build_quick_reference(living_dir)

    if not index_path.exists():
        index_path.write_text(quick_ref + "\n", encoding="utf-8")
        print(f"Written: {index_path}")
        return

    existing = index_path.read_text(encoding="utf-8")

    if QUICK_REF_BEGIN in existing and QUICK_REF_END in existing:
        # Replace only the QUICK_REFERENCE block
        before = existing[: existing.index(QUICK_REF_BEGIN)]
        after = existing[existing.index(QUICK_REF_END) + len(QUICK_REF_END) :]
        new_content = before + quick_ref + after
        index_path.write_text(new_content, encoding="utf-8")
    else:
        # Legacy migration: INDEX.md without QUICK_REF sentinels is treated as
        # fully machine-managed and replaced entirely. All existing INDEX.md
        # files were auto-generated by generate_index.py and contain no manually
        # authored content worth preserving.
        #
        # NOTE: This path also fires when INDEX.md has KNOWLEDGE_SUMMARY
        # sentinels but no QUICK_REF sentinels — the summary block is lost.
        # In practice this cannot happen because --summarize always writes both
        # sentinel pairs. If a future code path creates SUMMARY-only files,
        # this branch should be updated to preserve the summary block.
        new_content = quick_ref + "\n"
        index_path.write_text(new_content, encoding="utf-8")

    print(f"Written: {index_path}")


# ---------------------------------------------------------------------------
# --summary-heuristic helpers (no LLM)
# ---------------------------------------------------------------------------

# Matches lines like "**Tags**: foo, bar", "Tags: [foo, bar]", "Tags: foo".
# The [\s>]* prefix allows blockquoted entries.
_TAG_LINE_RE = re.compile(
    r"^[\s>]*\*?\*?Tags\*?\*?\s*:\s*(.+?)\s*$", re.IGNORECASE
)
_DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")

# Sentinel-wrapped advisory lines below the cluster table. Used to keep the
# heuristic block self-explanatory without the agent needing to read SKILL.md.
_HEURISTIC_FOOTER = (
    "_Heuristic clustering: tags with ≥2 entries, top 6 by count. "
    "To fetch matching entries: "
    "`python3 skills/core/scripts/recall_lessons.py --living-dir <path> --tag <tag>` "
    "or `--id L-N`._"
)


def parse_tag_line(text: str) -> list[str]:
    """Extract tag names from a 'Tags:' line.

    Handles all observed formats:
    - **Tags**: [tag1, tag2]
    - **Tags**: tag1, tag2
    - Tags: tag1
    - **Tags**:  (empty — returns [])

    Returns a list of stripped tag strings; empty list if the line is not a
    Tags line or contains no tags.
    """
    m = _TAG_LINE_RE.match(text)
    if not m:
        return []
    raw = m.group(1).strip()
    # Strip surrounding brackets if present
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1].strip()
    if not raw:
        return []
    return [t.strip() for t in raw.split(",") if t.strip()]


def collect_entries(path: Path, file_type: str, prefix: str) -> list[dict]:
    """Walk a learnings/decisions/conventions file and emit one record per entry.

    Each record is `{id, title, date, tags, line_no}`:
    - `id`: f"{prefix}-{N}" where N is 1-indexed file order (e.g. L-1, D-7)
    - `title`: header text with the `[YYYY-MM-DD]` date stripped
    - `date`: extracted date string or "" if absent
    - `tags`: list[str], from the first `**Tags**:` line within the entry body
    - `line_no`: 1-indexed line number of the header (for downstream slicing)

    Entries are returned in file order (oldest first) — match the on-disk shape
    so callers can derive recency by reversing or sorting on `date`.
    """
    if file_type in ("learnings", "decisions"):
        header_prefix = "### "
    elif file_type == "conventions":
        header_prefix = "## "
    else:
        header_prefix = "### "

    entries: list[dict] = []
    current: dict | None = None

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.rstrip()
            if line.startswith(header_prefix):
                if current is not None:
                    entries.append(current)
                title = line[len(header_prefix) :].strip()
                m = _DATE_RE.search(title)
                date = m.group(1) if m else ""
                clean_title = _DATE_RE.sub("", title).strip(" :-–—")
                current = {
                    "id": f"{prefix}-{len(entries) + 1}",
                    "title": clean_title or title,
                    "date": date,
                    "tags": [],
                    "line_no": line_no,
                }
            elif current is not None and not current["tags"]:
                tags = parse_tag_line(line)
                if tags:
                    current["tags"] = tags

    if current is not None:
        entries.append(current)
    return entries


def _cluster_by_tag(entries: list[dict], min_count: int = 2) -> list[tuple[str, list[dict]]]:
    """Group entries by tag, keep tags with ≥min_count, sort by count desc."""
    by_tag: dict[str, list[dict]] = {}
    for e in entries:
        for tag in e["tags"]:
            by_tag.setdefault(tag, []).append(e)
    clusters = [(tag, ents) for tag, ents in by_tag.items() if len(ents) >= min_count]
    clusters.sort(key=lambda x: (-len(x[1]), x[0]))
    return clusters


def build_heuristic_summary(living_dir: Path, top_n: int = 6, recent_n: int = 10) -> str:
    """Build a self-contained KNOWLEDGE SUMMARY block from tag clusters.

    No LLM — purely heuristic. Three subsections:
    1. **Tag clusters** — top N tags by entry count (threshold ≥2)
    2. **Most recent** — N most recent entries by date
    3. **By tag** — full inverted index `tag → L-1, L-3, ...`

    Always emits a valid sentinel-wrapped block (even when empty) so the
    SessionStart hook injection isn't gated forever on missing content.
    """
    today = datetime.now().strftime("%Y-%m-%d")

    learnings = (
        collect_entries(living_dir / "learnings.md", "learnings", "L")
        if (living_dir / "learnings.md").exists()
        else []
    )
    decisions = (
        collect_entries(living_dir / "decisions.md", "decisions", "D")
        if (living_dir / "decisions.md").exists()
        else []
    )
    all_entries = learnings + decisions

    lines: list[str] = [
        SUMMARY_BEGIN,
        f"Last summarized: {today} (heuristic)",
        "",
    ]

    if not all_entries:
        lines.extend(
            [
                "_No `### [YYYY-MM-DD]` entries yet — this block populates as "
                "`.living/learnings.md` and `.living/decisions.md` accumulate "
                "tagged entries._",
                SUMMARY_END,
            ]
        )
        return "\n".join(lines)

    # Two views of the same data:
    # - clusters (≥2 entries) feed the headline summary at the top
    # - all_tags (no min_count) feeds the full inverted index — singletons
    #   are valid recall targets too, so excluding them defeats the purpose
    clusters = _cluster_by_tag(all_entries, min_count=2)
    top_clusters = clusters[:top_n]
    all_tags = _cluster_by_tag(all_entries, min_count=1)

    lines.append("## Tag clusters")
    lines.append("")
    if top_clusters:
        for tag, ents in top_clusters:
            ids = ", ".join(e["id"] for e in ents[-5:])
            lines.append(f"- **{tag}** ({len(ents)} entries) — {ids}")
    else:
        lines.append("_No tag clusters yet (need ≥2 entries sharing a tag)._")

    lines.append("")
    lines.append(f"## Most recent ({recent_n})")
    lines.append("")
    sorted_entries = sorted(
        all_entries, key=lambda e: (e["date"] or "0000-00-00"), reverse=True
    )
    recent = sorted_entries[:recent_n]
    for e in recent:
        date_label = e["date"] if e["date"] else "—"
        lines.append(f"- [{date_label}] {e['id']}: {e['title']}")

    lines.append("")
    lines.append("## By tag")
    lines.append("")
    if all_tags:
        for tag, ents in all_tags:
            ids = ", ".join(e["id"] for e in ents)
            lines.append(f"- `{tag}`: {ids}")
    else:
        lines.append("_(empty — no tagged entries)_")

    lines.append("")
    lines.append(_HEURISTIC_FOOTER)
    lines.append(SUMMARY_END)
    return "\n".join(lines)


def update_index_summary_heuristic(living_dir: Path) -> None:
    """Write the heuristic SUMMARY block into INDEX.md, preserving QUICK_REFERENCE.

    - Fresh INDEX.md: writes both QUICK_REFERENCE and SUMMARY blocks.
    - Existing with both sentinels: replaces SUMMARY in-place.
    - Existing with QUICK_REFERENCE only: inserts SUMMARY after it.
    - Legacy (no sentinels): rebuilds from scratch.
    """
    index_path = living_dir / "INDEX.md"
    summary_block = build_heuristic_summary(living_dir)
    quick_ref = build_quick_reference(living_dir)

    if not index_path.exists():
        index_path.write_text(quick_ref + "\n\n" + summary_block + "\n", encoding="utf-8")
        print(f"Written: {index_path}")
        return

    existing = index_path.read_text(encoding="utf-8")

    # Legacy migration: any INDEX.md without QUICK_REF sentinels is
    # auto-generated content (the schema has always been managed by this
    # script). Rebuild from scratch — matches update_index_counts_only's
    # legacy behavior and avoids stranding a stale pre-sentinel table at
    # the bottom of the file. Migrator targets this case specifically.
    if QUICK_REF_BEGIN not in existing or QUICK_REF_END not in existing:
        index_path.write_text(
            quick_ref + "\n\n" + summary_block + "\n", encoding="utf-8"
        )
        print(f"Written: {index_path}")
        return

    before = existing[: existing.index(QUICK_REF_BEGIN)]
    after = existing[existing.index(QUICK_REF_END) + len(QUICK_REF_END) :]
    existing = before + quick_ref + after

    if SUMMARY_BEGIN in existing and SUMMARY_END in existing:
        before = existing[: existing.index(SUMMARY_BEGIN)]
        after = existing[existing.index(SUMMARY_END) + len(SUMMARY_END) :]
        existing = before + summary_block + after
    else:
        # Insert SUMMARY after QUICK_REFERENCE block
        anchor = existing.find(QUICK_REF_END)
        if anchor >= 0:
            insert_at = anchor + len(QUICK_REF_END)
            existing = (
                existing[:insert_at] + "\n\n" + summary_block + existing[insert_at:]
            )
        else:
            existing = existing.rstrip() + "\n\n" + summary_block

    if not existing.endswith("\n"):
        existing += "\n"
    index_path.write_text(existing, encoding="utf-8")
    print(f"Written: {index_path}")


# ---------------------------------------------------------------------------
# --summarize helpers
# ---------------------------------------------------------------------------


def extract_entry_snippets(path: Path, file_type: str) -> list[tuple[str, str]]:
    """Extract (header, first_content_line) pairs for each entry, newest-first.

    For files with more than 500 entries, samples 250 most recent + 250 evenly
    spaced from the remainder.

    Args:
        path: Path to the markdown file.
        file_type: One of 'learnings', 'decisions', 'conventions', 'other'.

    Returns:
        List of (header_text, first_content_line) tuples.
    """
    if file_type in ("learnings", "decisions"):
        header_prefix = "### "
    elif file_type == "conventions":
        header_prefix = "## "
    else:
        header_prefix = "### "

    entries: list[tuple[str, str]] = []  # (header_text, first_content_line)
    current_header: str | None = None
    first_content: str | None = None

    def _flush() -> None:
        if current_header is not None:
            entries.append((current_header, first_content or ""))

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw_line in fh:
            line = raw_line.rstrip()
            if line.startswith(header_prefix):
                _flush()
                current_header = line[len(header_prefix) :].strip()
                first_content = None
            elif current_header is not None and first_content is None and line.strip():
                first_content = line.strip()

    _flush()

    # Entries are in file order (oldest first for append-style files).
    # Reverse to get newest-first.
    entries.reverse()

    if len(entries) <= 500:
        return entries

    # Sample: 250 most recent + 250 evenly spaced from the remainder
    recent = entries[:250]
    remainder = entries[250:]
    step = max(1, len(remainder) // 250)
    sampled = remainder[::step][:250]
    return recent + sampled


def build_llm_prompt(
    learnings_snippets: list[tuple[str, str]],
    decisions_snippets: list[tuple[str, str]],
    learnings_count: int,
    decisions_count: int,
) -> str:
    """Construct the summarization prompt for the LLM.

    Args:
        learnings_snippets: List of (header, first_line) from learnings.md.
        decisions_snippets: List of (header, first_line) from decisions.md.
        learnings_count: Total entry count in learnings.md.
        decisions_count: Total entry count in decisions.md.

    Returns:
        Prompt string ready for passing to claude CLI.
    """

    def _format_snippets(snippets: list[tuple[str, str]]) -> str:
        parts = []
        for header, content in snippets:
            line = f"- {header}"
            if content:
                line += f": {content}"
            parts.append(line)
        return "\n".join(parts)

    learnings_text = _format_snippets(learnings_snippets)
    decisions_text = _format_snippets(decisions_snippets)

    return f"""You are summarizing a .living/ knowledge base for a software/science project.

I'll give you entry snippets (header + first content line) from learnings.md ({learnings_count} total entries) and decisions.md ({decisions_count} total entries).

Your task: identify 3-6 thematic clusters per section. Each cluster groups related entries under a descriptive name.

Output format (STRICT — no other text):

### Key Knowledge Clusters — Learnings
- **cluster name** (N entries) — one-sentence description of what this cluster covers

### Key Knowledge Clusters — Decisions
- **cluster name** (N entries) — one-sentence description of what this cluster covers

Rules:
- Output ONLY the two sections above, nothing else (no preamble, no conclusion)
- Every cluster line must match exactly: `- **name** (N entries) — description`
- At least 1 cluster per section
- N should be a rough estimate based on the snippets

=== LEARNINGS SNIPPETS ({len(learnings_snippets)} shown of {learnings_count} total) ===
{learnings_text}

=== DECISIONS SNIPPETS ({len(decisions_snippets)} shown of {decisions_count} total) ===
{decisions_text}
"""


def call_llm(prompt: str) -> str:
    """Call `claude -p` with --model sonnet and return the output.

    Args:
        prompt: The prompt string to pass via stdin.

    Returns:
        The LLM's response string.

    Raises:
        RuntimeError: If the claude CLI call fails or returns non-zero exit code.
    """
    result = subprocess.run(
        ["claude", "-p", "--model", "sonnet"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}): {stderr or '(no stderr)'}"
        )
    return result.stdout


def parse_llm_clusters(llm_output: str) -> str | None:
    """Validate and clean LLM cluster output.

    MUST have both '### Key Knowledge Clusters — Learnings' and
    '### Key Knowledge Clusters — Decisions' headers. Each section must have
    at least 1 cluster line matching: `- **name** (N entries) — description`.
    At least 2 valid cluster lines total. Strips trailing chatter.

    Args:
        llm_output: Raw LLM output string.

    Returns:
        Cleaned cluster string, or None if malformed.
    """
    cluster_re = re.compile(r"^- \*\*[^*]+\*\* \(\d+ entr(?:y|ies)\) — .+$")
    learnings_header = "### Key Knowledge Clusters — Learnings"
    decisions_header = "### Key Knowledge Clusters — Decisions"

    lines = llm_output.splitlines()

    if learnings_header not in llm_output:
        return None
    if decisions_header not in llm_output:
        return None

    # Find the start of the first relevant header
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == learnings_header or line.strip() == decisions_header:
            start_idx = i
            break

    if start_idx is None:
        return None

    # Work only from first header onward
    relevant = lines[start_idx:]

    # Strip trailing chatter: keep only headers, cluster lines, and blank lines
    cleaned: list[str] = []
    for line in relevant:
        stripped = line.rstrip()
        if stripped == "" or stripped.startswith("### ") or cluster_re.match(stripped):
            cleaned.append(stripped)
        # Skip any non-matching line (trailing prose, preamble that crept in)

    # Remove trailing blank lines
    while cleaned and cleaned[-1] == "":
        cleaned.pop()

    result_text = "\n".join(cleaned)

    # Validate: both headers present
    if learnings_header not in result_text:
        return None
    if decisions_header not in result_text:
        return None

    # Validate: each section has at least 1 cluster line
    learnings_idx = result_text.index(learnings_header)
    decisions_idx = result_text.index(decisions_header)

    if learnings_idx < decisions_idx:
        learnings_section = result_text[learnings_idx:decisions_idx]
        decisions_section = result_text[decisions_idx:]
    else:
        decisions_section = result_text[decisions_idx:learnings_idx]
        learnings_section = result_text[learnings_idx:]

    learnings_clusters = [
        ln for ln in learnings_section.splitlines() if cluster_re.match(ln.rstrip())
    ]
    decisions_clusters = [
        ln for ln in decisions_section.splitlines() if cluster_re.match(ln.rstrip())
    ]

    if not learnings_clusters or not decisions_clusters:
        return None

    return result_text


def update_index_summarize(living_dir: Path) -> None:
    """Full LLM summarization mode.

    On success: generates both KNOWLEDGE_SUMMARY and QUICK_REFERENCE blocks,
    updates 'Last summarized' date.

    On LLM failure or malformed output: preserves previous summary block
    byte-for-byte (preserving old 'Last summarized' date), still updates counts.

    Dual-date semantics:
    - 'Last audit' (in page header inside QUICK_REFERENCE): always updates.
    - 'Last summarized' (inside KNOWLEDGE_SUMMARY block): only updates on
      successful LLM call.

    Args:
        living_dir: Path to the .living/ directory.
    """
    index_path = living_dir / "INDEX.md"
    today = datetime.now().strftime("%Y-%m-%d")

    # --- Collect previous summary block (for fallback) ---
    old_summary_block: str | None = None
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        if SUMMARY_BEGIN in existing and SUMMARY_END in existing:
            s = existing.index(SUMMARY_BEGIN)
            e = existing.index(SUMMARY_END) + len(SUMMARY_END)
            old_summary_block = existing[s:e]

    # --- Gather snippets for learnings and decisions ---
    learnings_path = living_dir / "learnings.md"
    decisions_path = living_dir / "decisions.md"

    learnings_snippets: list[tuple[str, str]] = []
    learnings_count = 0
    if learnings_path.exists():
        learnings_count, _ = count_headers_and_topics(learnings_path, "learnings")
        learnings_snippets = extract_entry_snippets(learnings_path, "learnings")

    decisions_snippets: list[tuple[str, str]] = []
    decisions_count = 0
    if decisions_path.exists():
        decisions_count, _ = count_headers_and_topics(decisions_path, "decisions")
        decisions_snippets = extract_entry_snippets(decisions_path, "decisions")

    # --- Attempt LLM summarization ---
    new_summary_block: str | None = None
    if learnings_snippets or decisions_snippets:
        prompt = build_llm_prompt(
            learnings_snippets,
            decisions_snippets,
            learnings_count,
            decisions_count,
        )
        try:
            llm_output = call_llm(prompt)
            clusters = parse_llm_clusters(llm_output)
            if clusters is not None:
                new_summary_block = "\n".join(
                    [
                        SUMMARY_BEGIN,
                        f"Last summarized: {today}",
                        "",
                        clusters,
                        SUMMARY_END,
                    ]
                )
            else:
                print(
                    "Warning: LLM output was malformed — falling back to previous summary.",
                    file=sys.stderr,
                )
        except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
            print(
                f"Warning: LLM call failed ({exc}) — falling back to previous summary.",
                file=sys.stderr,
            )

    # Use new summary if generated, otherwise fall back to old
    summary_block = (
        new_summary_block if new_summary_block is not None else old_summary_block
    )

    # --- Build quick reference ---
    quick_ref = build_quick_reference(living_dir)

    # --- Assemble the full INDEX.md ---
    parts: list[str] = [quick_ref]
    if summary_block:
        parts.append("")
        parts.append(summary_block)

    new_content = "\n".join(parts) + "\n"
    index_path.write_text(new_content, encoding="utf-8")
    print(f"Written: {index_path}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


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

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--counts-only",
        action="store_true",
        help="Update entry counts and Quick Reference table only (no LLM).",
    )
    mode_group.add_argument(
        "--summary-heuristic",
        action="store_true",
        help="No-LLM tag-based clustering. Updates counts AND emits a "
        "knowledge summary block from `**Tags**:` annotations.",
    )
    mode_group.add_argument(
        "--summarize",
        action="store_true",
        help="Full LLM summarization: update counts AND generate knowledge clusters.",
    )

    args = parser.parse_args()

    living_dir: Path = args.living_dir.resolve()
    if not living_dir.is_dir():
        parser.error(f"--living-dir '{living_dir}' is not a directory.")

    if args.counts_only:
        if args.dry_run:
            print(build_quick_reference(living_dir))
        else:
            update_index_counts_only(living_dir)
    elif args.summary_heuristic:
        if args.dry_run:
            print(build_quick_reference(living_dir))
            print()
            print(build_heuristic_summary(living_dir))
        else:
            update_index_summary_heuristic(living_dir)
    elif args.summarize:
        if args.dry_run:
            # For summarize dry-run, show what would be written without saving
            # We still call the LLM but print instead of write
            index_path = living_dir / "INDEX.md"
            _original_write = index_path.write_text

            def _dry_write(text: str, **_kwargs: object) -> None:
                print(text)

            index_path.write_text = _dry_write  # type: ignore[method-assign]
            update_index_summarize(living_dir)
            index_path.write_text = _original_write  # type: ignore[method-assign]
        else:
            update_index_summarize(living_dir)
    else:
        content = generate_index(living_dir)
        if args.dry_run:
            print(content)
        else:
            index_path = living_dir / "INDEX.md"
            index_path.write_text(content, encoding="utf-8")
            print(f"Written: {index_path}")


if __name__ == "__main__":
    main()
