"""Drift protection between entry-writing guidance and the INDEX.md parsers.

Every parser in ``generate_index.py`` keys ``learnings.md``/``decisions.md``
entries off a literal ``"### "`` prefix. Guidance that tells an agent to write
an entry at any other level produces a file that is on disk, greppable, and
completely absent from ``.living/INDEX.md`` — with no error raised anywhere
(issue #76: the decision template said ``##`` while all three parsers read
``###``).

These tests bind the shipped guidance to the parsers themselves rather than to a
hardcoded string, so the two cannot drift apart again silently.
"""

import re
from pathlib import Path

import generate_index as gi
from validate_structure import (
    ENTRY_HEADING_PREFIX,
    ValidationResult,
    check_entry_heading_levels,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "skills" / "core"
TEMPLATES = CORE / "templates"

# (template file, destination log, generate_index file_type, id prefix)
ENTRY_TEMPLATES = [
    ("learning-entry.md", "learnings.md", "learnings", "L"),
    ("decision-log-entry.md", "decisions.md", "decisions", "D"),
]


def _template_body(name: str) -> str:
    """Template text with the date placeholder replaced by a real date."""
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return text.replace("[YYYY-MM-DD]", "[2026-08-25]")


# ---------------------------------------------------------------------------
# The parsers must agree with each other
# ---------------------------------------------------------------------------


def test_all_three_parsers_agree_on_the_entry_heading_level(tmp_path: Path) -> None:
    """count_headers_and_topics / collect_entries / extract_entry_snippets agree.

    Asserted behaviourally: a log holding one correctly-levelled entry and one
    ``##`` entry must read as exactly one entry through every parser. If any
    parser were relaxed to accept ``##`` without the others, this fails.
    """
    for log_name, file_type, prefix in [
        ("learnings.md", "learnings", "L"),
        ("decisions.md", "decisions", "D"),
    ]:
        path = tmp_path / log_name
        path.write_text(
            f"# Log\n\n"
            f"{ENTRY_HEADING_PREFIX}[2026-08-01] Parseable\n\n**Tags**: [t]\n\n"
            f"## [2026-08-25] Wrong level\n\n**Tags**: [t]\n",
            encoding="utf-8",
        )
        count, _ = gi.count_headers_and_topics(path, file_type)
        entries = gi.collect_entries(path, file_type, prefix)
        snippets = gi.extract_entry_snippets(path, file_type)

        assert count == len(entries) == len(snippets) == 1, (
            f"{log_name}: parsers disagree — "
            f"count={count} entries={len(entries)} snippets={len(snippets)}"
        )
        assert entries[0]["title"] == "Parseable"


def test_validate_structure_pins_the_level_the_parsers_use(tmp_path: Path) -> None:
    """The new validator and the parsers cannot disagree about the level."""
    living = tmp_path / ".living"
    living.mkdir()
    (living / "learnings.md").write_text(
        f"{ENTRY_HEADING_PREFIX}[2026-08-25] Entry\n\n**Tags**: [t]\n",
        encoding="utf-8",
    )
    result = ValidationResult()
    check_entry_heading_levels(tmp_path, result)
    assert result.warnings == []
    assert gi.collect_entries(living / "learnings.md", "learnings", "L")


# ---------------------------------------------------------------------------
# Shipped templates must round-trip through every parser
# ---------------------------------------------------------------------------


def test_shipped_templates_parse_as_exactly_one_entry(tmp_path: Path) -> None:
    """Copying a template verbatim must yield one indexed entry, not zero.

    This is the regression test for the reported defect: the decision template
    instructed ``##`` and produced ``0 entries``.
    """
    for template, log_name, file_type, prefix in ENTRY_TEMPLATES:
        path = tmp_path / log_name
        path.write_text(_template_body(template), encoding="utf-8")

        count, _ = gi.count_headers_and_topics(path, file_type)
        entries = gi.collect_entries(path, file_type, prefix)
        snippets = gi.extract_entry_snippets(path, file_type)

        assert count == 1, f"{template}: count_headers_and_topics saw {count}"
        assert len(entries) == 1, f"{template}: collect_entries saw {len(entries)}"
        assert len(snippets) == 1, f"{template}: extract_entry_snippets saw {len(snippets)}"
        assert entries[0]["date"] == "2026-08-25", f"{template}: date not parsed"


def test_shipped_templates_pass_the_heading_level_validator(tmp_path: Path) -> None:
    living = tmp_path / ".living"
    living.mkdir()
    for template, log_name, _, _ in ENTRY_TEMPLATES:
        (living / log_name).write_text(_template_body(template), encoding="utf-8")

    result = ValidationResult()
    check_entry_heading_levels(tmp_path, result)
    assert result.warnings == []


def test_both_entry_templates_use_the_same_heading_level() -> None:
    """The learning and decision templates must not diverge from each other."""
    levels = set()
    for template, _, _, _ in ENTRY_TEMPLATES:
        for line in (TEMPLATES / template).read_text(encoding="utf-8").splitlines():
            if re.match(r"^#{1,6} \[YYYY-MM-DD\]", line):
                levels.add(line.split(" ", 1)[0])
                break
        else:  # pragma: no cover - template lost its entry heading
            raise AssertionError(f"{template} has no dated entry heading")
    assert levels == {ENTRY_HEADING_PREFIX.strip()}


# ---------------------------------------------------------------------------
# Guidance that dictates the entry format must match the parsers
# ---------------------------------------------------------------------------


def test_post_action_hook_dictates_the_parser_heading_level() -> None:
    """The hook fires after every analysis run — the primary capture path."""
    text = (CORE / "hooks" / "mycelium-post-action.sh").read_text(encoding="utf-8")
    assert "Format: ### [YYYY-MM-DD] Title" in text
    assert "Format: ## [YYYY-MM-DD] Title" not in text


def test_transfer_skill_dictates_the_parser_heading_level() -> None:
    """The transfer skill appends to learnings.md automatically."""
    text = (REPO_ROOT / "skills" / "transfer" / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"^### \[YYYY-MM-DD\] \[Short Learning Title\]$", text, re.M)
    assert not re.search(r"^## \[YYYY-MM-DD\]", text, re.M)


def test_skill_generation_guide_examples_use_the_parser_heading_level() -> None:
    """Worked examples are copied as-is by agents, so they must be parseable."""
    text = (
        CORE / "references" / "skill-generation-guide.md"
    ).read_text(encoding="utf-8")
    assert not re.search(r"^## \[\d{4}-\d{2}-\d{2}\]", text, re.M), (
        "worked-example learnings entries must use '###'"
    )
    assert re.search(r"^### \[\d{4}-\d{2}-\d{2}\]", text, re.M)
