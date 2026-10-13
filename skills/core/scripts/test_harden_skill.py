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


def test_harden_keeps_tests_out_of_analysis_code() -> None:
    harden = _flat(HARDEN)
    assert "Never edit analysis code" in harden
    assert "fail on a minimal reproduction" in harden.lower()
    assert "pass on the current code" in harden.lower()
    # The source skill referenced mycelium-extra's approval gate; we ship none.
    assert ".mycelium-extra" not in harden
    assert "Mikhael Manurung" in harden


def test_core_never_marks_an_unshipped_candidate_structural() -> None:
    core = _flat(CORE)
    assert "if you can name a concrete test or invariant, upgrade the entry" not in core
    assert "$mycelium:harden" in core
