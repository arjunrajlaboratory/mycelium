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
    # The copied test leaves the worktree dirty; remove it, metadata included.
    assert "git worktree remove --force" in harden
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
    assert "otherwise `convention` if a `.living/conventions.md` entry covers it" in core


def test_unshipped_structural_entries_return_to_harden() -> None:
    # The template keeps an unshipped candidate at ambient-awareness; demoting
    # it to convention would strand it outside harden's candidate filter.
    guide = _flat(GUIDE)
    assert "demote to `convention` until the test ships" not in guide
    assert "demote to `ambient-awareness`" in guide
    assert "reclassified at crystallize time" not in _flat(HARDEN)


def test_template_is_the_single_statement_of_the_mitigation_lifecycle() -> None:
    # Every Codex finding on #96 was a restated copy of the lifecycle drifting
    # from another, including a transition list that could never be complete.
    # The template states one precedence rule over current enforcement; the
    # other documents defer to it, and steps that change enforcement record it.
    template = _flat(TEMPLATE)
    for clause in (
        "derive it in this order",
        "re-derive it whenever enforcement changes",
        "otherwise convention",
        "otherwise ambient-awareness",
    ):
        assert clause in template, clause
    assert "Transitions" not in template  # an enumerated list drifts; the rule does not
    for path in (HARDEN, CORE, GUIDE):
        assert "skills/core/templates/learning-entry.md" in _flat(path), path
    guide = _flat(GUIDE)
    assert "has been or should be promoted" not in guide  # a proposed convention is still ambient-awareness
    assert "If missing, add it now" not in guide  # a removed entry may be intentional
    assert "Add or strengthen the conventions.md entry" not in guide  # same, in the table
    assert "re-derive its type" in guide
    # Steps that add a convention re-derive the cited learnings' type: pinned
    # by test_every_instruction_that_writes_a_learning_or_convention_applies_the_rule.
    transfer = _flat(PLUGIN_ROOT / "skills" / "transfer" / "SKILL.md")
    assert "see the transitions" not in transfer


def _template_fields() -> list[str]:
    fields = re.findall(r"^\*\*([A-Za-z_ ]+)\*\*:", TEMPLATE.read_text(encoding="utf-8"), re.M)
    assert "mitigation_type" in fields and "structural_mitigation_candidate" in fields
    return fields


def test_every_learning_format_names_every_template_field() -> None:
    # Each place that tells an agent how to write a learning restates the
    # format; a copy missing the mitigation fields yields untyped learnings
    # that harden skips (Codex on #96). The field list comes from the template.
    sources = {
        "post-action hook": (PLUGIN_ROOT / "skills" / "core" / "hooks" / "mycelium-post-action.sh").read_text(),
        "init_repo learnings.md header": _init_learnings_header(),
        "transfer skill": (PLUGIN_ROOT / "skills" / "transfer" / "SKILL.md").read_text(),
    }
    for name, text in sources.items():
        missing = [f for f in _template_fields() if f not in text]
        assert missing == [], f"{name} omits {missing}"


def _init_learnings_header() -> str:
    source = (PLUGIN_ROOT / "skills" / "core" / "scripts" / "init_repo.py").read_text()
    start = source.index('"learnings.md": (')
    return source[start:source.index("),", start)]


def test_structural_means_any_shipped_check_not_only_a_test() -> None:
    # The template counts a shipped test, assertion, type constraint, or schema
    # validation as structural; a copy that says "unless a test has shipped"
    # misfiles shipped assertions as ambient-awareness (Codex on #96).
    # Read the mechanism list from the template's structural definition, so a
    # hand-copied list cannot drop one (Codex on #96 caught a missing frozenset).
    template = _flat(TEMPLATE)
    listed = re.search(r"structural — A (.+?) has SHIPPED", template).group(1)
    mechanisms = [m.strip() for m in re.split(r",\s*(?:or\s+)?", listed)]
    assert "frozenset" in mechanisms and len(mechanisms) == 5, mechanisms
    hook = (PLUGIN_ROOT / "skills" / "core" / "hooks" / "mycelium-post-action.sh").read_text()
    directive = next(line for line in hook.splitlines() if "LIVING_DIRECTIVE=\"MYCELIUM" in line)
    for mechanism in mechanisms:
        assert mechanism in directive, mechanism
    assert "unless a test has shipped" not in directive
    core = _flat(CORE)
    assert "while the test is only a candidate" not in core
    for name, text in (("core", core), ("guide", _flat(GUIDE))):
        for mechanism in mechanisms:
            assert mechanism in text, (name, mechanism)


def test_transfer_length_rule_fits_the_full_entry_format() -> None:
    # Heading + five fields + two mitigation fields + source: is nine lines.
    transfer = _flat(PLUGIN_ROOT / "skills" / "transfer" / "SKILL.md")
    assert "5-8 lines" not in transfer


_WRITES_LEARNING = re.compile(
    # Active verb forms only ("are logged" describes, it does not instruct),
    # with the path allowed on a later line of the same step.
    r"\b(append|appends|printf|add|adds|log|logs|record|records|write|writes)\b"
    r"[\s\S]{0,160}?learnings\.md"
    r"|\badds? a new convention|promote to `?\.living/conventions\.md|automatically append",
    re.I,
)


def _norm(text: str) -> str:
    return " ".join(text.replace("`", "").split()).lower()


def _canonical_rule() -> str:
    """The one-line rule the template asks every writer to quote verbatim."""
    found = re.search(
        r"adds a convention, so no copy can paraphrase it partially: "
        r"(Derive mitigation_type .+? otherwise ambient-awareness\.)",
        _flat(TEMPLATE),
    )
    assert found, "template lost its one-line rule"
    return found.group(1)


def _instruction_units(path: Path) -> list[str]:
    """Numbered steps or bullets outside code fences, or the hook directive's lines."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".sh":
        text = text.replace("\\n", "\n")
    text = re.sub(r"^```.*?^```", "", text, flags=re.M | re.S)
    return [u for u in re.split(r"\n(?=\s*(?:\d+\.|-)\s)|\n\s*\n", text) if u.strip()]


def test_every_instruction_that_writes_a_learning_or_convention_applies_the_rule() -> None:
    # Root cause of the later Codex findings on #96: each writer restated the
    # type rule partially (tests only, convention without structural first),
    # and a narrow detector missed writers entirely. Every shipped step that
    # writes a learning or adds a convention must quote the template's
    # one-line rule verbatim, read from the template itself.
    skip = {
        TEMPLATE: "the canonical statement itself",
        PLUGIN_ROOT / "skills" / "develop" / "references" / "regression-patterns.md": "describes past defects",
        PLUGIN_ROOT / "skills" / "core" / "templates" / "knowledge-transfer-report.md": "a report placeholder; writes nothing",
        PLUGIN_ROOT / "skills" / "lifecycle-audit" / "references" / "child-task-prompt.md": "forbids writing to .living",
        PLUGIN_ROOT / "skills" / "lifecycle-audit" / "references" / "audit-protocol.md": "a labeled probe in a disposable repo, not a learning",
    }
    rule = _norm(_canonical_rule())
    # Every shipped text file an agent may follow, not a guessed subtree:
    # convention packs under network/ carry instructions too (Codex on #96).
    # Other hooks only warn or block and route to the post-action directive.
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=PLUGIN_ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    paths = [
        PLUGIN_ROOT / f for f in tracked
        if not f.startswith(("docs/", "analysis/", ".github/"))
        and f not in {"CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"}
        and not (PLUGIN_ROOT / f).is_symlink()
        and (Path(f).suffix in {".md", ".template", ".yaml", ".yml"}
             or Path(f).name == "mycelium-post-action.sh")
    ]
    assert any(p.parts[-4:-2] == ("network", "conventions") for p in paths)
    writers, offenders = 0, []
    for path in paths:
        if path in skip:
            continue
        for unit in _instruction_units(path):
            if _WRITES_LEARNING.search(unit):
                writers += 1
                if rule not in _norm(unit):
                    offenders.append(f"{path.relative_to(PLUGIN_ROOT)}: {' '.join(unit.split())[:90]}")
    assert writers >= 12, writers  # the sweep must keep seeing the known writers
    assert offenders == []
