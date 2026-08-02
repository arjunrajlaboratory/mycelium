#!/usr/bin/env python3
"""Validate finding IDs and severity tallies in a Mycelium review report."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

FINDING = re.compile(r"^##### F(\d+)\.\s+\S")
TABLE_ROW = re.compile(r"^\|(.+)\|\s*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
EXPECTED_CATEGORIES = {
    "Statistics & causal inference",
    "Data pipeline & leakage",
    "Bioinformatics",
    "LLM coding antipatterns",
    "Documentation & schema fidelity",
    "Code quality",
}


def _clean_cell(value: str) -> str:
    return value.strip().replace("**", "")


def _visible_lines(lines: list[str]) -> list[tuple[int, str]]:
    """Return Markdown lines outside fenced code blocks with source indices."""
    visible: list[tuple[int, str]] = []
    fence_char = ""
    fence_length = 0
    for index, line in enumerate(lines):
        match = FENCE.match(line)
        if not fence_char:
            if match:
                marker = match.group(1)
                fence_char = marker[0]
                fence_length = len(marker)
                continue
            visible.append((index, line))
            continue

        if match:
            marker = match.group(1)
            remainder = line[match.end() :]
            if (
                marker[0] == fence_char
                and len(marker) >= fence_length
                and not remainder.strip()
            ):
                fence_char = ""
                fence_length = 0
    return visible


def validate_report(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    visible = _visible_lines(lines)
    ids: list[int] = []
    counts: Counter[tuple[str, str]] = Counter()
    categories_seen: set[str] = set()
    category = ""
    severity = ""
    in_findings = False
    tally_index = next(
        (index for index, line in visible if line == "## Finding tally"),
        None,
    )
    scan_end = tally_index if tally_index is not None else len(lines)

    for index, line in visible:
        if index >= scan_end:
            break
        if line == "## Findings":
            in_findings = True
        elif in_findings and line.startswith("### ") and not line.startswith("#### "):
            category = line[4:].strip()
            categories_seen.add(category)
            severity = ""
        elif in_findings and line in {"#### Major", "#### Minor"}:
            severity = line[5:].strip()
        else:
            match = FINDING.match(line)
            if match:
                ids.append(int(match.group(1)))
                if not category or severity not in {"Major", "Minor"}:
                    return [f"Finding {match.group(1)} has no category/severity context"]
                counts[(category, severity)] += 1

    errors: list[str] = []
    if ids != list(range(1, len(ids) + 1)):
        errors.append("Finding IDs must be unique and consecutive from F1")
    if tally_index is None:
        errors.append("Missing ## Finding tally section")
        return errors

    unexpected_categories = sorted(categories_seen - EXPECTED_CATEGORIES)
    if unexpected_categories:
        errors.append(
            "Unexpected finding categories: " + ", ".join(unexpected_categories)
        )

    tally: dict[str, tuple[int, int, int]] = {}
    for index, line in visible:
        if index <= tally_index:
            continue
        if line.startswith("## "):
            break
        match = TABLE_ROW.match(line)
        if not match:
            continue
        cells = [_clean_cell(cell) for cell in match.group(1).split("|")]
        if len(cells) != 4 or cells[0] in {"Category", "---"}:
            continue
        if set(cells[0]) <= {"-", ":"}:
            continue
        try:
            if cells[0] in tally:
                errors.append(f"Duplicate tally row for {cells[0]}")
            tally[cells[0]] = tuple(int(value) for value in cells[1:])  # type: ignore[assignment]
        except ValueError:
            errors.append(f"Non-numeric tally row for {cells[0]}")

    unexpected_tally_rows = sorted(tally.keys() - EXPECTED_CATEGORIES - {"Total"})
    if unexpected_tally_rows:
        errors.append("Unexpected tally rows: " + ", ".join(unexpected_tally_rows))

    expected_total = (
        sum(value for (name, sev), value in counts.items() if sev == "Major"),
        sum(value for (name, sev), value in counts.items() if sev == "Minor"),
        len(ids),
    )
    if tally.get("Total") != expected_total:
        errors.append(
            f"Global tally {tally.get('Total')} does not match findings {expected_total}"
        )
    missing_categories = sorted(EXPECTED_CATEGORIES - tally.keys())
    if missing_categories:
        errors.append(
            "Missing category tally rows: " + ", ".join(missing_categories)
        )
    for name in sorted(EXPECTED_CATEGORIES):
        expected = (
            counts[(name, "Major")],
            counts[(name, "Minor")],
            counts[(name, "Major")] + counts[(name, "Minor")],
        )
        if tally.get(name) != expected:
            errors.append(
                f"Category tally for {name!r} {tally.get(name)} does not match {expected}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    errors = validate_report(args.report)
    for error in errors:
        print(f"error: {error}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
