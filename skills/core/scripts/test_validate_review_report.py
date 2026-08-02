"""Tests for the deterministic scientific-review report validator."""

from pathlib import Path

from validate_review_report import validate_report


def _report(total: str = "| **Total** | **1** | **1** | **2** |") -> str:
    return f"""# Review

## Findings

### Statistics & causal inference

#### Major

##### F1. One root cause

#### Minor

##### F2. Another root cause

## Finding tally

| Category | Major | Minor | Total |
| --- | ---: | ---: | ---: |
| Statistics & causal inference | 1 | 1 | 2 |
| Data pipeline & leakage | 0 | 0 | 0 |
| Bioinformatics | 0 | 0 | 0 |
| LLM coding antipatterns | 0 | 0 | 0 |
| Documentation & schema fidelity | 0 | 0 | 0 |
| Code quality | 0 | 0 | 0 |
{total}
"""


def test_valid_report_has_consecutive_ids_and_matching_tally(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(_report())

    assert validate_report(report) == []


def test_wrong_global_tally_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(_report("| **Total** | **2** | **1** | **3** |"))

    errors = validate_report(report)

    assert any("tally" in error.lower() for error in errors)


def test_duplicate_or_nonconsecutive_finding_ids_are_rejected(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(_report().replace("##### F2.", "##### F1."))

    errors = validate_report(report)

    assert any("consecutive" in error.lower() for error in errors)


def test_missing_category_tally_row_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(_report().replace("| Bioinformatics | 0 | 0 | 0 |\n", ""))

    errors = validate_report(report)

    assert any("missing category" in error.lower() for error in errors)


def test_markdown_headings_inside_fenced_code_are_ignored(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report().replace(
            "## Finding tally",
            """### Documentation & schema fidelity

#### Minor

```markdown
### Setup from scratch

##### F99. Example heading, not a finding

## Finding tally
```

## Finding tally""",
        )
    )

    assert validate_report(report) == []


def test_zero_finding_category_must_have_zero_tally(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report().replace(
            "| Bioinformatics | 0 | 0 | 0 |",
            "| Bioinformatics | 1 | 0 | 1 |",
        )
    )

    errors = validate_report(report)

    assert any("Bioinformatics" in error for error in errors)


def test_unexpected_finding_category_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report()
        .replace(
            "### Statistics & causal inference",
            "### Surprise category",
        )
        .replace(
            "| Statistics & causal inference | 1 | 1 | 2 |",
            "| Statistics & causal inference | 0 | 0 | 0 |\n"
            "| Surprise category | 1 | 1 | 2 |",
        )
    )

    errors = validate_report(report)

    assert any("unexpected" in error.lower() for error in errors)


def test_tables_after_finding_tally_section_are_ignored(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report()
        + """
## Evidence appendix

| Statistics & causal inference | 9 | 9 | 18 |
"""
    )

    assert validate_report(report) == []
