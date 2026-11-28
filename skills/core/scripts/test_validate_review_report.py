"""Tests for the deterministic scientific-review report validator."""

import re
from pathlib import Path

from validate_review_report import EXPECTED_CATEGORIES, validate_report

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
REVIEW_REFS = PLUGIN_ROOT / "skills" / "core" / "references" / "review"


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
| Empirical claims & results | 0 | 0 | 0 |
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


def test_empirical_claims_category_is_validated(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report(total="| **Total** | **2** | **1** | **3** |")
        .replace(
            "## Finding tally",
            """### Empirical claims & results

#### Major

##### F3. Results prose omits two opposite-sign segments

## Finding tally""",
            1,
        )
        .replace(
            "| Empirical claims & results | 0 | 0 | 0 |",
            "| Empirical claims & results | 1 | 0 | 1 |",
        )
    )

    assert validate_report(report) == []


def test_missing_empirical_claims_tally_row_is_rejected(tmp_path: Path) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report().replace("| Empirical claims & results | 0 | 0 | 0 |\n", "")
    )

    errors = validate_report(report)

    assert any(
        "missing category" in error.lower() and "Empirical claims & results" in error
        for error in errors
    )


def test_self_skipped_empirical_claims_specialist_keeps_zero_row(
    tmp_path: Path,
) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report().replace(
            "## Finding tally",
            """## What was checked but is fine

- **Empirical claims & results**: no empirical results or output tables in scope.

## Finding tally""",
        )
    )

    assert validate_report(report) == []


def test_nonzero_tally_for_empty_empirical_claims_category_is_rejected(
    tmp_path: Path,
) -> None:
    report = tmp_path / "review.md"
    report.write_text(
        _report().replace(
            "| Empirical claims & results | 0 | 0 | 0 |",
            "| Empirical claims & results | 0 | 1 | 1 |",
        )
    )

    errors = validate_report(report)

    assert any("Empirical claims & results" in error for error in errors)


def _template_tally_tables(text: str) -> list[list[str]]:
    """Category rows of each Finding tally table template in ``text``, preserving multiplicity."""
    tables: list[list[str]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.strip() != "## Finding tally":
            continue
        rows: list[str] = []
        for row in lines[index + 1 :]:
            if row.startswith("## ") or row.startswith("```"):
                break
            match = re.match(r"^\|([^|]+)\|", row)
            if not match:
                continue
            name = match.group(1).strip().replace("**", "")
            if name in {"Category", "Total"} or set(name) <= {"-", ":", " "}:
                continue
            rows.append(name)
        tables.append(rows)
    return tables


def test_report_templates_list_exactly_the_validated_categories() -> None:
    for path in (
        PLUGIN_ROOT / "skills" / "review" / "SKILL.md",
        REVIEW_REFS / "synthesis.md",
    ):
        tables = _template_tally_tables(path.read_text())
        assert tables, f"no Finding tally tables found in {path}"
        for table_idx, table in enumerate(tables):
            assert len(table) == len(set(table)), (path, table_idx, table)
            assert set(table) == EXPECTED_CATEGORIES, (path, table_idx)


def test_every_dispatched_specialist_has_a_checklist_file() -> None:
    skill = (PLUGIN_ROOT / "skills" / "review" / "SKILL.md").read_text()
    row_re = re.compile(
        r"^\|\s*\d+\s*\|\s*\*\*([a-z-]+)\*\*\s*\|\s*"
        r"`skills/core/references/review/([a-z-]+\.md)`\s*\|",
        re.MULTILINE,
    )
    rows = row_re.findall(skill)
    assert len(rows) == len(EXPECTED_CATEGORIES), rows
    specialists = [specialist for specialist, _ in rows]
    assert len(set(specialists)) == len(EXPECTED_CATEGORIES), rows
    assert "results-and-claims" in specialists
    for specialist, checklist in rows:
        assert checklist == f"{specialist}.md", (specialist, checklist)
        assert (REVIEW_REFS / checklist).is_file(), checklist


def _flat(path: Path) -> str:
    return " ".join(path.read_text().split())


def test_results_and_claims_is_exempt_from_the_diff_only_rules() -> None:
    readme = _flat(REVIEW_REFS / "README.md")
    checklist = _flat(REVIEW_REFS / "results-and-claims.md")
    skill = _flat(PLUGIN_ROOT / "skills" / "review" / "SKILL.md")

    assert "The `results-and-claims` specialist is exempt" in readme
    assert "its own read-only recomputations are evidence" in readme
    assert "Exempt from the diff-only rule." in checklist
    # The exemption must sit in the shared skip-flag section and be cited
    # back from the specialist's own checklist.
    readme_text = (REVIEW_REFS / "README.md").read_text()
    skip_section = readme_text.split("## What every sub-agent should NOT flag", 1)[1]
    assert "`results-and-claims` specialist is exempt" in " ".join(skip_section.split())
    assert '"What every sub-agent should NOT flag" in `README.md`' in checklist
    assert "not as evidence that the tables are clean" in checklist
    assert "those two always run, on the primary tier" in skill
    assert "never modify, regenerate, or delete a file in the repository" in checklist
    assert "no empirical results or output tables in scope" in checklist
    assert "do not give it any other specialist's findings" in skill
    assert "run `results-and-claims` first" in skill


def test_review_skill_documents_scoped_and_tier_options() -> None:
    skill = _flat(PLUGIN_ROOT / "skills" / "review" / "SKILL.md")
    synthesis = _flat(REVIEW_REFS / "synthesis.md")

    for option in ("--focus", "--cleared", "--all-frontier", "--no-tripwires"):
        assert option in skill, option
    assert "boundary:" in skill
    assert "### Scoped reviews" in synthesis
    assert "Fall back to the primary model, without failing the review" in skill


def test_review_instructions_name_no_versioned_model_ids() -> None:
    versioned = re.compile(
        r"\b(?:claude|gpt|gemini|o\d)-[a-z0-9.-]*\d", re.IGNORECASE
    )
    for path in [PLUGIN_ROOT / "skills" / "review" / "SKILL.md", *REVIEW_REFS.glob("*.md")]:
        assert not versioned.search(path.read_text()), path


def test_review_specialists_share_a_context_pack_and_effort_budget() -> None:
    skill = _flat(PLUGIN_ROOT / "skills" / "review" / "SKILL.md")
    readme = _flat(REVIEW_REFS / "README.md")
    claims = _flat(REVIEW_REFS / "results-and-claims.md")

    # One shared index of the scope, so sub-agents do not rediscover the tree.
    assert "context pack" in skill and "context pack" in readme
    assert "## Working within an effort budget" in readme
    assert "Batch reads" in readme
    # The three mechanical checklists share one lighter-tier sub-agent.
    assert "lighter pass" in skill
    # A spend cap is not a reason to give up results-and-claims' independence
    # in a full review; a scoped review runs in-line with that pass first.
    assert "In a full review, do not run the checklists in-line to save cost" in skill
    assert "Run the `results-and-claims` checklist first" in skill
    # Synthesis merges findings; it does not rerun the specialists' checks.
    synthesis = _flat(REVIEW_REFS / "synthesis.md")
    assert "does not redo the specialists' work" in synthesis
    # Cross-conclusion checks come first, with per-conclusion coverage recorded.
    assert "## Order of work" in claims
    assert claims.index("Cross-join the ledger") < claims.index("Targeted recomputation")
    assert "Record coverage" in claims


def test_review_validator_resolves_from_active_skill_bundle_not_stale_pointer() -> None:
    for path in (
        PLUGIN_ROOT / "skills" / "review" / "SKILL.md",
        REVIEW_REFS / "synthesis.md",
    ):
        text = path.read_text()
        assert "validate_review_report.py" in text
        assert ".mycelium/plugin-root)/skills/core/scripts/validate_review_report.py" not in text
