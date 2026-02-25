#!/usr/bin/env python3
"""Detect patterns in accumulated learnings and propose new conventions.

Reads .living/learnings.md and .living/decisions.md, identifies recurring
patterns, and generates draft convention documents.

Usage:
    python crystallize_learnings.py [--target-dir PATH] [--min-pattern-count N]
"""

import argparse
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Crystallize learnings into proposed conventions."
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=Path.cwd(),
        help="Root directory of the repository (default: current directory)",
    )
    parser.add_argument(
        "--min-pattern-count",
        type=int,
        default=3,
        help="Minimum occurrences to consider something a pattern (default: 3)",
    )
    return parser.parse_args()


def parse_learnings(learnings_path: Path) -> list[dict]:
    """Parse .living/learnings.md into structured entries."""
    # TODO: Read the file and parse entries by heading
    # TODO: Extract date, title, category, tags from each entry
    # TODO: Return list of structured entries
    entries = []
    if learnings_path.exists():
        content = learnings_path.read_text()
        print(f"  Read {len(content)} characters from learnings.md")
        # TODO: Parse markdown entries
    else:
        print("  learnings.md not found")
    return entries


def parse_decisions(decisions_path: Path) -> list[dict]:
    """Parse .living/decisions.md into structured entries."""
    # TODO: Read the file and parse entries by heading
    # TODO: Extract date, title, context, decision, tags from each entry
    # TODO: Return list of structured entries
    entries = []
    if decisions_path.exists():
        content = decisions_path.read_text()
        print(f"  Read {len(content)} characters from decisions.md")
        # TODO: Parse markdown entries
    else:
        print("  decisions.md not found")
    return entries


def identify_patterns(
    learnings: list[dict], decisions: list[dict], min_count: int
) -> list[dict]:
    """Identify recurring patterns in learnings and decisions."""
    # TODO: Group entries by tags
    # TODO: Look for similar titles/content using keyword overlap
    # TODO: Identify repeated problems (same gotcha encountered multiple times)
    # TODO: Find evolving conventions (decisions that refine an approach over time)
    # TODO: Detect gap signals ("there's no convention for this")
    # TODO: Return patterns that appear at least min_count times
    patterns = []
    print(f"  Analyzing {len(learnings)} learnings and {len(decisions)} decisions")
    print(f"  Looking for patterns with at least {min_count} occurrences")
    print("  TODO: Pattern detection implementation")
    return patterns


def generate_convention(pattern: dict, target_dir: Path):
    """Generate a draft convention document from a pattern."""
    # TODO: Create .living/generated-skills/[name]/ directory
    # TODO: Generate convention.md with:
    #   - Clear convention statement
    #   - Rationale linked to source learnings
    #   - Examples of correct/incorrect application
    #   - Exceptions and edge cases
    # TODO: Generate ORIGIN.md with provenance:
    #   - Source learnings (dates and titles)
    #   - Source decisions
    #   - Pattern description
    print(f"  Would generate convention for pattern: {pattern}")


def main():
    args = parse_args()
    target_dir = args.target_dir.resolve()

    print("Crystallize Learnings")
    print("=" * 50)

    # Verify mycelium structure exists
    if not (target_dir / ".living").exists():
        print("Error: This doesn't appear to be a mycelium-enabled repo.")
        print("Run init_repo.py first.")
        return

    print("\nParsing learnings...")
    learnings = parse_learnings(target_dir / ".living" / "learnings.md")

    print("\nParsing decisions...")
    decisions = parse_decisions(target_dir / ".living" / "decisions.md")

    if not learnings and not decisions:
        print("\nNo learnings or decisions found yet.")
        print("Keep working and logging — patterns emerge over time.")
        return

    print("\nIdentifying patterns...")
    patterns = identify_patterns(learnings, decisions, args.min_pattern_count)

    if not patterns:
        print("\nNo clear patterns detected yet.")
        print(f"Need at least {args.min_pattern_count} related entries to identify a pattern.")
        print("Keep logging learnings and decisions — crystallize again later.")
        return

    print(f"\nFound {len(patterns)} patterns. Generating conventions...")
    for pattern in patterns:
        generate_convention(pattern, target_dir)

    print("\n" + "=" * 50)
    print("Crystallization complete!")
    print("Review generated conventions in .living/generated-skills/")
    print("Use contribute mode to share useful conventions with the network.")


if __name__ == "__main__":
    main()
