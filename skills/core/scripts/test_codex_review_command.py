"""Content-contract test for the /mycelium:codex-review command (issue #60).

CI validates command frontmatter inline but does not run pytest, so this test is
both the TDD driver for the command and a regression guard. It pins the
behaviors issue #60 requires: address the specific Codex comment AND audit the
whole branch for other instances of the same error pattern, with a
Codex-access-gated `@codex review` re-trigger and a confirm-before-post reply.

The assertions are concept-level (keyword/substring), not exact prose, so the
command can be reworded freely as long as the required behaviors remain.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMAND_PATH = REPO_ROOT / "commands" / "codex-review.md"


def _split_frontmatter(text):
    assert text.startswith("---"), "command must start with YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, "command missing closing frontmatter delimiter"
    return yaml.safe_load(parts[1]), parts[2]


@pytest.fixture
def command_text():
    assert COMMAND_PATH.exists(), f"missing command file: {COMMAND_PATH}"
    return COMMAND_PATH.read_text()


def test_frontmatter_has_nonempty_description(command_text):
    meta, _ = _split_frontmatter(command_text)
    assert "description" in meta, "frontmatter missing 'description'"
    assert meta["description"] and meta["description"].strip(), "description is empty"


def test_description_triggers_on_codex(command_text):
    meta, _ = _split_frontmatter(command_text)
    desc = meta["description"].lower()
    assert "codex" in desc, "description should mention Codex so the skill triggers"


def test_body_requires_branch_wide_audit(command_text):
    """Core of issue #60: fix the flagged instance AND audit the whole branch."""
    _, body = _split_frontmatter(command_text)
    low = body.lower()
    assert "audit" in low, "body must describe auditing the branch"
    assert "branch" in low, "body must scope the audit to the whole branch"
    assert "pattern" in low, "body must generalize the instance into an error pattern"
    assert "git diff" in low, "body must diff the branch to scope the audit"
    assert "base ref" in low, (
        "body must diff against the PR base ref (baseRefName), not a hardcoded "
        "branch like main (Codex PR #63 finding 1)"
    )


def test_body_identifies_codex_by_login_not_bot_type_alone(command_text):
    """Codex PR #63 finding 2: bot-type alone over-matches Dependabot etc."""
    _, body = _split_frontmatter(command_text)
    low = body.lower()
    assert "login" in low, "body must identify Codex by author login"
    assert any(k in low for k in ("dependabot", "github-actions", "release bot")), (
        "body must warn that matching on bot type alone over-matches unrelated "
        "bots (Dependabot, github-actions, release bots)"
    )


def test_body_auto_detects_comment_scope(command_text):
    _, body = _split_frontmatter(command_text)
    low = body.lower()
    assert "all open" in low, "body must handle fetching all open Codex comments"
    assert "specific comment" in low, "body must handle a user-specified single comment"


def test_body_gates_codex_retrigger_on_access(command_text):
    _, body = _split_frontmatter(command_text)
    low = body.lower()
    assert "@codex review" in low, "body must mention the @codex review re-trigger"
    assert any(k in low for k in ("prior", "previously", "already")), (
        "body must gate the re-trigger on detecting prior Codex activity on the PR"
    )


def test_body_drafts_then_confirms_before_posting(command_text):
    _, body = _split_frontmatter(command_text)
    low = body.lower()
    assert "reply" in low or "comment" in low, "body must produce a reply"
    assert "confirm" in low, "body must confirm before posting (outward-facing action)"
