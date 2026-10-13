"""Contract tests for the harden skill and the learning fields it edits.

harden rewrites two fields of a learning entry. The template defines them and
detect_recurrence.py reads them, so all three must agree on the spelling.
"""

from __future__ import annotations

import re
from pathlib import Path

import detect_recurrence

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
HARDEN = PLUGIN_ROOT / "skills" / "harden" / "SKILL.md"
TEMPLATE = PLUGIN_ROOT / "skills" / "core" / "templates" / "learning-entry.md"
CORE = PLUGIN_ROOT / "skills" / "core" / "SKILL.md"
GUIDE = PLUGIN_ROOT / "skills" / "core" / "references" / "skill-generation-guide.md"

ENTRY = """### [2026-10-01] Plate 3 barcodes are swapped

**Category**: gotcha

**What happened**: Wells A1 and A2 carry each other's barcodes.

**mitigation_type**: ambient-awareness

**structural_mitigation_candidate**: In `tests/test_plate3.py`, assert that \
`data/plate3.csv` maps A1 to BC01.
"""


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _record_lines() -> list[str]:
    """The literal field lines that harden's record step tells the agent to write."""
    text = HARDEN.read_text(encoding="utf-8")
    section = text.split("## 4.", 1)[1]
    return re.findall(r"^- `(.+)`$", section, re.M)


def test_harden_edits_the_template_field_spellings() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    harden = HARDEN.read_text(encoding="utf-8")
    for field in ("**mitigation_type**:", "**structural_mitigation_candidate**:"):
        assert field in template
        assert field in harden


def test_harden_record_step_reads_back_as_structural() -> None:
    lines = _record_lines()
    assert "**mitigation_type**: structural" in lines
    assert detect_recurrence._extract_mitigation_type(ENTRY) == "ambient-awareness"
    hardened = ENTRY.replace("**mitigation_type**: ambient-awareness", lines[0])
    assert detect_recurrence._extract_mitigation_type(hardened) == "structural"


def test_harden_finds_canonical_and_legacy_learning_headings() -> None:
    # detect_recurrence reads both `### [date]` and legacy `## [date]` entries.
    harden = _flat(HARDEN)
    assert "`### [YYYY-MM-DD]`" in harden
    assert "`## [YYYY-MM-DD]`" in harden


def test_harden_only_selects_ambient_awareness_entries() -> None:
    # "Not structural" would also admit convention entries and overwrite them.
    harden = _flat(HARDEN)
    assert "`mitigation_type` is `ambient-awareness`" in harden
    assert "is not `structural`" not in harden


def test_harden_red_run_matches_the_kind_of_check() -> None:
    # A code regression test passes on the bad input once the fix is in; its
    # red run is against the unmitigated implementation. A data check's red run
    # is a fixture that recreates the bad data.
    harden = _flat(HARDEN)
    assert "unmitigated implementation" in harden
    assert "recreates the bad data" in harden
    # A pre-fix worktree holds only tracked files: the new test must be copied
    # in, and the failure must come from its assertion, not a missing test.
    assert "copy the new test into it" in harden
    assert "targeted assertion" in harden
    assert "pass on the current code" in harden.lower()


def test_harden_keeps_tests_out_of_analysis_code() -> None:
    harden = _flat(HARDEN)
    assert "Never edit analysis code" in harden
    # The source skill referenced mycelium-extra's approval gate; we ship none.
    assert ".mycelium-extra" not in harden
    assert "Mikhael Manurung" in harden


def test_core_never_marks_an_unshipped_candidate_structural() -> None:
    core = _flat(CORE)
    assert "if you can name a concrete test or invariant, upgrade the entry" not in core
    assert "$mycelium:harden" in core
    # Only an unshipped test candidate waits at ambient-awareness; a learning
    # already enforced by a conventions.md entry keeps `convention`.
    assert "already enforced by an entry in `.living/conventions.md`" in core


def test_unshipped_structural_entries_return_to_harden() -> None:
    # The template keeps an unshipped candidate at ambient-awareness; demoting
    # it to convention would strand it outside harden's candidate filter.
    guide = _flat(GUIDE)
    assert "demote to `convention` until the test ships" not in guide
    assert "demote to `ambient-awareness`" in guide
    assert "reclassified at crystallize time" not in _flat(HARDEN)


def test_template_is_the_single_statement_of_the_mitigation_lifecycle() -> None:
    # Every Codex finding on #96 was one document's copy of the lifecycle
    # disagreeing with another's. The template states the transitions; the
    # other documents point to it and must not contradict it.
    template = _flat(TEMPLATE)
    for transition in (
        "ambient-awareness → structural",
        "ambient-awareness → convention",
        "structural → ambient-awareness",
        "convention → ambient-awareness",
        "convention → structural",
    ):
        assert transition in template
    assert "only the user changes an enforced classification" in template
    for path in (HARDEN, CORE, GUIDE):
        assert "skills/core/templates/learning-entry.md" in _flat(path), path
    guide = _flat(GUIDE)
    assert "has been or should be promoted" not in guide  # a proposed convention is still ambient-awareness
    # Crystallize applies convention → ambient-awareness to a missing entry;
    # re-adding a removed convention is the user's decision, not automatic.
    assert "If missing, add it now" not in guide
    assert "If it is missing, demote the learning to `ambient-awareness`" in guide
