"""Contract tests for decision supersession fields and the resolution rules.

The decision template defines the fields, the decision-status reference states
how to read them, and every instruction that writes a decision points at them.
These tests bind the four together so a field cannot be renamed in one place
and silently keep its old spelling in another, and so an entry written the
documented way still reads as one entry through the INDEX parsers.

The fields and rules are adapted from the decision-status skill in
mycelium-extra (https://github.com/mdmanurung/mycelium-extra), Copyright (c)
2026 Mikhael Manurung, MIT License. See issue #91.
"""

from __future__ import annotations

import re
from pathlib import Path

import generate_index as gi
import init_repo

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
CORE = PLUGIN_ROOT / "skills" / "core"
TEMPLATE = CORE / "templates" / "decision-log-entry.md"
REFERENCE = CORE / "references" / "decision-status.md"
CORE_SKILL = CORE / "SKILL.md"
POST_ACTION = CORE / "hooks" / "mycelium-post-action.sh"

SUPERSESSION_FIELDS = (
    "**Status**:",
    "**Supersedes**:",
    "**Scope**:",
    "**Revisit when**:",
    "**Resolved-by**:",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    return " ".join(_text(path).split())


def _field_names(text: str) -> list[str]:
    """Bold field names that open a line, in order (``**Context**:`` → Context)."""
    return re.findall(r"^\*\*([^*]+)\*\*:", text, re.M)


def _status_vocabulary(text: str) -> list[str]:
    """The values offered on the template's ``**Status**:`` placeholder line."""
    match = re.search(r"^\*\*Status\*\*: \[([a-z|-]+)\]", text, re.M)
    assert match, "no **Status**: [a|b|...] placeholder line"
    return match.group(1).split("|")


def _resolution_example() -> str:
    """The resolution entry the reference tells the agent to append."""
    text = _text(REFERENCE)
    match = re.search(r"```markdown\n(### \[YYYY-MM-DD\] Resolution:.*?)```", text, re.S)
    assert match, "reference has no fenced ### Resolution entry example"
    return match.group(1)


# ---------------------------------------------------------------------------
# One spelling for each field
# ---------------------------------------------------------------------------


def test_template_carries_every_supersession_field() -> None:
    template = _text(TEMPLATE)
    for field in SUPERSESSION_FIELDS:
        assert re.search(rf"^{re.escape(field)}", template, re.M), field


def test_reference_reads_the_template_spellings() -> None:
    reference = _text(REFERENCE)
    for field in SUPERSESSION_FIELDS:
        assert field in reference, f"decision-status.md never names {field}"


def test_reference_and_template_offer_the_same_status_values() -> None:
    values = _status_vocabulary(_text(TEMPLATE))
    assert values[0] == "active"
    # An entry never marks itself superseded; the newer entry says so.
    assert "superseded" not in values
    reference = _text(REFERENCE)
    for value in values:
        assert f"`{value}`" in reference, f"reference never defines status {value}"


def test_resolution_example_uses_only_template_fields() -> None:
    template_fields = set(_field_names(_text(TEMPLATE)))
    example_fields = _field_names(_resolution_example())
    assert example_fields, "resolution example has no fields"
    assert set(example_fields) <= template_fields, (
        set(example_fields) - template_fields
    )


def test_init_header_lists_every_template_field(tmp_path: Path) -> None:
    """The decisions.md header written by init names the template's fields."""
    init_repo.create_directory_structure(tmp_path)
    init_repo.create_living_layer(tmp_path)
    flat = _flat(tmp_path / ".living" / "decisions.md")
    for name in _field_names(_text(TEMPLATE)):
        assert name in flat, f"init's decisions.md header omits {name}"


# ---------------------------------------------------------------------------
# Entries written the documented way stay visible to the parsers
# ---------------------------------------------------------------------------


def _log_with(tmp_path: Path, *entries: str) -> Path:
    path = tmp_path / "decisions.md"
    path.write_text("# Decision Log\n\n" + "\n".join(entries), encoding="utf-8")
    return path


def test_template_entry_still_snippets_from_its_context_line(tmp_path: Path) -> None:
    body = _text(TEMPLATE).replace("[YYYY-MM-DD]", "[2026-10-01]")
    body = body.replace("[Short Decision Title]", "Use limma for DE")
    path = _log_with(tmp_path, body)

    entries = gi.collect_entries(path, "decisions", "D")
    snippets = gi.extract_entry_snippets(path, "decisions")
    assert [e["title"] for e in entries] == ["Use limma for DE"]
    assert snippets[0][1].startswith("**Context**:")


def test_resolution_entry_is_one_tagged_entry(tmp_path: Path) -> None:
    resolution = (
        _resolution_example()
        .replace("[YYYY-MM-DD]", "[2026-10-14]")
        .replace("YYYY-MM-DD", "2026-10-14")
        .replace("<decision point>", "batch correction")
        .replace("<the thread's tags, so the next lookup finds this entry>", "batch, de")
    )
    original = (
        "### [2026-09-01] Use ComBat for batch correction\n\n"
        "**Context**: Two sequencing runs.\n\n**Decision**: ComBat.\n\n"
        "**Status**: active\n\n**Tags**: batch\n"
    )
    path = _log_with(tmp_path, original, resolution)

    entries = gi.collect_entries(path, "decisions", "D")
    assert [e["title"] for e in entries] == [
        "Use ComBat for batch correction",
        "Resolution: batch correction",
    ]
    assert entries[1]["tags"] == ["batch", "de"]


# ---------------------------------------------------------------------------
# The rules are reachable from every place a decision is written or read
# ---------------------------------------------------------------------------


def test_reference_states_the_resolution_rules() -> None:
    flat = _flat(REFERENCE)
    for phrase in (
        "Explicit supersession",
        "Scope match",
        "Contested",
        "never wins by recency alone",
        "date and exact heading",
        "unknown",
    ):
        assert phrase in flat, phrase
    assert "Mikhael Manurung" in flat


def test_core_routes_to_the_reference() -> None:
    core = _text(CORE_SKILL)
    assert "## Mode: `decision-status`" in core
    assert "skills/core/references/decision-status.md" in core
    post_action = core.split("## Post-Action Hook Protocol", 1)[1]
    assert "**Supersedes**" in post_action


def test_post_action_directive_names_the_template_and_supersedes() -> None:
    hook = _text(POST_ACTION)
    line = next(l for l in hook.splitlines() if "5. DECISIONS:" in l)
    step = line.split("5. DECISIONS:", 1)[1].split("\\n", 1)[0]
    assert "skills/core/templates/decision-log-entry.md" in step
    assert "**Supersedes**" in step
