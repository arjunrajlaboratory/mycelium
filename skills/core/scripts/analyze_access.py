#!/usr/bin/env python3
"""analyze_access.py — Unified .living/ access log analyzer.

Reads both mycelium-read-access.log and mycelium-bash-access.log from a
project's .claude/ directory and emits a structured plain-text report.

Usage:
    python3 analyze_access.py <project_dir> [--correlate] [--days N]

    project_dir: repo root containing .claude/mycelium-read-access.log
                 and (optionally) .claude/mycelium-bash-access.log
    --correlate: additionally walk Claude Code JSONL session logs under
                 ~/.claude/projects/<slug>/ and compute Read-before-Edit
                 correlation (learnings-consulted-before-implementation metric).
    --days N:    only consider JSONLs modified within the last N days
                 (default 14). Applies to --correlate only.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_read_log(path: Path) -> list[dict]:
    """Parse read-tool log: lines are `TIMESTAMP PATH`."""
    events = []
    if not path.exists():
        return events
    with path.open() as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            ts_str, file_path = parts
            try:
                ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue
            events.append(
                {
                    "ts": ts,
                    "method": "read_tool",
                    "op": "read_tool",
                    "path": file_path,
                }
            )
    return events


def parse_bash_log(path: Path) -> list[dict]:
    """Parse bash-access log: lines are `TIMESTAMP OP PATH`."""
    events = []
    if not path.exists():
        return events
    with path.open() as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            parts = line.split(None, 2)
            if len(parts) != 3:
                continue
            ts_str, op, file_path = parts
            try:
                ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue
            events.append(
                {
                    "ts": ts,
                    "method": f"bash:{op}",
                    "op": op,
                    "path": file_path,
                }
            )
    return events


# ---------------------------------------------------------------------------
# Sessionization
# ---------------------------------------------------------------------------


def sessionize(events: list[dict], gap_minutes: int = 10) -> list[list[dict]]:
    """Group events into sessions separated by >gap_minutes of silence."""
    if not events:
        return []
    sessions: list[list[dict]] = []
    current: list[dict] = [events[0]]
    gap = timedelta(minutes=gap_minutes)
    for ev in events[1:]:
        if ev["ts"] - current[-1]["ts"] > gap:
            sessions.append(current)
            current = [ev]
        else:
            current.append(ev)
    sessions.append(current)
    return sessions


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _col(label: str, value, width: int = 38) -> str:
    return f"  {label:<{width}}{value}"


def _section(title: str) -> str:
    bar = "-" * (len(title) + 4)
    return f"\n{bar}\n  {title}\n{bar}"


def _pct(n: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{100.0 * n / total:.1f}%"


# ---------------------------------------------------------------------------
# Main report
# ---------------------------------------------------------------------------


def report(project_dir: Path) -> None:
    claude_dir = project_dir / ".claude"
    read_log = claude_dir / "mycelium-read-access.log"
    bash_log = claude_dir / "mycelium-bash-access.log"

    read_events = parse_read_log(read_log)
    bash_events = parse_bash_log(bash_log)

    all_events = sorted(read_events + bash_events, key=lambda e: e["ts"])

    if not all_events:
        print("No events found in either log file.")
        return

    sessions = sessionize(all_events)

    # --- Volume ---
    method_counts: Counter[str] = Counter(e["method"] for e in all_events)
    total = len(all_events)

    today = datetime.now().date()
    fourteen_days_ago = today - timedelta(days=13)
    per_day: Counter = Counter()
    for ev in all_events:
        d = ev["ts"].date()
        if d >= fourteen_days_ago:
            per_day[d] += 1

    print(_section("Volume"))
    print(_col("Total events:", total))
    for method in sorted(method_counts):
        print(_col(f"  {method}:", method_counts[method]))

    print()
    print("  Events per day (last 14 days):")
    for delta in range(13, -1, -1):
        d = today - timedelta(days=delta)
        count = per_day.get(d, 0)
        print(f"    {d.isoformat()}  {count:>5}")

    # --- Session-level routing ---
    n_sessions = len(sessions)
    touched_learnings = 0
    first_index = 0
    first_last_session = 0
    index_then_learnings = 0

    for sess in sessions:
        paths = [e["path"] for e in sess]
        # touched learnings or findings
        if any(".living/learnings" in p or ".living/findings" in p for p in paths):
            touched_learnings += 1

        first_path = paths[0] if paths else ""
        if ".living/INDEX.md" in first_path:
            first_index += 1
            # INDEX → learnings/findings in same session
            if any(
                ".living/learnings" in p or ".living/findings" in p for p in paths[1:]
            ):
                index_then_learnings += 1

        if ".living/last-session.md" in first_path:
            first_last_session += 1

    print(_section("Session-level routing"))
    print(_col("Total sessions:", n_sessions))
    print(
        _col(
            "Sessions touching learnings/findings:",
            f"{touched_learnings}  ({_pct(touched_learnings, n_sessions)})",
        )
    )
    print(
        _col(
            "Sessions starting with INDEX.md:",
            f"{first_index}  ({_pct(first_index, n_sessions)})",
        )
    )
    print(
        _col(
            "Sessions starting with last-session.md:",
            f"{first_last_session}  ({_pct(first_last_session, n_sessions)})",
        )
    )
    print(
        _col(
            "INDEX -> learnings/findings routing rate:",
            f"{index_then_learnings}  ({_pct(index_then_learnings, n_sessions)})",
        )
    )

    # --- Top files ---
    file_counts: Counter[str] = Counter(e["path"] for e in all_events)
    print(_section("Top files (all methods)"))
    for path, count in file_counts.most_common(10):
        print(f"  {count:>6}  {path}")

    # --- Write vs read split ---
    op_counts: Counter[str] = Counter()
    for ev in all_events:
        if ev["method"] == "read_tool":
            op_counts["read_tool"] += 1
        else:
            op_counts[f"bash-{ev['op']}"] += 1

    print(_section("Write vs read split"))
    for op_label in ["read_tool", "bash-read", "bash-append", "bash-write"]:
        count = op_counts.get(op_label, 0)
        print(_col(f"{op_label}:", f"{count}  ({_pct(count, total)})"))

    print()


# ---------------------------------------------------------------------------
# Correlate: Read-before-Edit from Claude Code JSONL session logs
# ---------------------------------------------------------------------------


def _project_slug(project_dir: Path) -> str:
    """Map an absolute project dir to Claude Code's project-log slug.

    Claude Code replaces '/' and spaces with '-' in the absolute path.
    """
    s = str(project_dir)
    return s.replace("/", "-").replace(" ", "-")


def _extract_tool_events(jsonl_path: Path) -> list[dict]:
    """Extract ordered (ts, tool_name, file_path) from a Claude Code JSONL.

    Only includes tool_use entries for Read/Edit/Write/MultiEdit with a
    file_path. Timestamps are parsed as timezone-aware UTC.
    """
    out: list[dict] = []
    try:
        raw = jsonl_path.read_text(errors="ignore")
    except OSError:
        return out
    for line in raw.splitlines():
        if not line or '"tool_use"' not in line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("type") != "assistant":
            continue
        ts_str = d.get("timestamp")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        msg = d.get("message", {}) or {}
        content = msg.get("content", []) or []
        if not isinstance(content, list):
            continue
        for c in content:
            if not isinstance(c, dict):
                continue
            if c.get("type") != "tool_use":
                continue
            name = c.get("name")
            if name not in ("Read", "Edit", "Write", "MultiEdit"):
                continue
            fp = (c.get("input") or {}).get("file_path")
            if not fp:
                continue
            out.append({"ts": ts, "tool": name, "path": fp})
    return out


def _is_living_knowledge(path: str) -> bool:
    """True if path points to a learnings or findings knowledge file."""
    return "/.living/learnings" in path or "/.living/findings" in path


def _is_living_any(path: str) -> bool:
    return "/.living/" in path


def correlate(project_dir: Path, lookback_days: int = 14) -> None:
    slug = _project_slug(project_dir)
    projects_root = Path.home() / ".claude" / "projects"
    proj_log_dir = projects_root / slug
    if not proj_log_dir.is_dir():
        print(f"No JSONL log directory at {proj_log_dir}")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    top_level = [f for f in proj_log_dir.glob("*.jsonl")]
    subagent = list(proj_log_dir.glob("*/subagents/*.jsonl"))

    def _recent(files: list[Path]) -> list[Path]:
        out = []
        for f in files:
            try:
                mt = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mt >= cutoff:
                out.append(f)
        return out

    top_level = _recent(top_level)
    subagent = _recent(subagent)

    print(_section(f"Read-before-Edit correlation (last {lookback_days}d)"))
    print(_col("JSONL slug:", slug))
    print(_col("Top-level sessions scanned:", len(top_level)))
    print(_col("Subagent sessions scanned:", len(subagent)))

    # Walk each session independently — do not share state across sessions.
    total_edits = 0
    edits_with_knowledge = 0
    gap_buckets = Counter()  # <1min, 1-5, 5-30, 30+
    unconsulted_files: Counter[str] = Counter()

    all_files = top_level + subagent
    for f in all_files:
        events = _extract_tool_events(f)
        if not events:
            continue
        # Most recent learnings/findings Read in this session (running state)
        last_knowledge_ts: datetime | None = None
        for ev in events:
            if ev["tool"] == "Read" and _is_living_knowledge(ev["path"]):
                last_knowledge_ts = ev["ts"]
                continue
            if ev["tool"] in ("Edit", "Write", "MultiEdit"):
                # Only count edits to non-.living/ files (real code/docs)
                if _is_living_any(ev["path"]):
                    continue
                total_edits += 1
                if last_knowledge_ts is not None:
                    edits_with_knowledge += 1
                    gap = ev["ts"] - last_knowledge_ts
                    mins = gap.total_seconds() / 60.0
                    if mins < 1:
                        gap_buckets["<1 min"] += 1
                    elif mins < 5:
                        gap_buckets["1-5 min"] += 1
                    elif mins < 30:
                        gap_buckets["5-30 min"] += 1
                    else:
                        gap_buckets["30+ min"] += 1
                else:
                    # Track which files get edited with no knowledge consultation
                    rel = ev["path"]
                    # normalize to a relative-ish path for grouping
                    try:
                        rel = str(Path(rel).resolve().relative_to(project_dir))
                    except (ValueError, OSError):
                        pass
                    unconsulted_files[rel] += 1

    print(_col("Total Edit/Write events to non-.living/ files:", total_edits))
    if total_edits == 0:
        print("  (No edits found — nothing to correlate.)")
        return
    print(
        _col(
            "Edits preceded by learnings/findings Read:",
            f"{edits_with_knowledge}  ({_pct(edits_with_knowledge, total_edits)})",
        )
    )
    unconsulted = total_edits - edits_with_knowledge
    print(
        _col(
            "Edits with NO preceding knowledge consultation:",
            f"{unconsulted}  ({_pct(unconsulted, total_edits)})",
        )
    )

    if edits_with_knowledge > 0:
        print()
        print("  Gap from last knowledge Read to Edit:")
        for bucket in ("<1 min", "1-5 min", "5-30 min", "30+ min"):
            n = gap_buckets.get(bucket, 0)
            print(f"    {bucket:<10} {n:>5}  ({_pct(n, edits_with_knowledge)})")

    if unconsulted_files:
        print()
        print("  Top 10 files edited WITHOUT preceding knowledge read:")
        for path, n in unconsulted_files.most_common(10):
            print(f"    {n:>4}  {path}")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(
            f"Usage: python3 {Path(sys.argv[0]).name} "
            "<project_dir> [--correlate] [--days N]"
        )
        sys.exit(1)

    project_dir = Path(args[0]).expanduser().resolve()
    if not project_dir.is_dir():
        print(f"Error: {project_dir} is not a directory.")
        sys.exit(1)

    do_correlate = "--correlate" in args
    days = 14
    if "--days" in args:
        try:
            days = int(args[args.index("--days") + 1])
        except (IndexError, ValueError):
            print("Error: --days requires an integer argument.")
            sys.exit(1)

    report(project_dir)
    if do_correlate:
        correlate(project_dir, lookback_days=days)


if __name__ == "__main__":
    main()
