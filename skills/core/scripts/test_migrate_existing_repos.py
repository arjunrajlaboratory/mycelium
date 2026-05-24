#!/usr/bin/env python3
"""Tests for migrate_existing_repos.py."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import migrate_existing_repos as mig  # noqa: E402


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """A minimal mycelium repo: .living/, CLAUDE.md, .claude/ with no hooks."""
    repo = tmp_path / "fake-repo"
    repo.mkdir()
    living = repo / ".living"
    living.mkdir()
    (living / "learnings.md").write_text(
        "# Learnings\n\n"
        "### [2026-04-01] Tagged entry\n"
        "**Tags**: [test]\n\n"
        "Body.\n"
        "\n"
        "### [2026-04-02] Another tagged entry\n"
        "**Tags**: [test]\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    (living / "decisions.md").write_text("# Decisions\n", encoding="utf-8")
    (living / "conventions.md").write_text("# Conventions\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text(
        "# Fake Project\n\n"
        "## Quick Orientation\n\n"
        "1. **Read `.living/` first** — accumulated intelligence.\n"
        "2. **Read `ENVIRONMENTS_INSTALLATIONS.md`**.\n",
        encoding="utf-8",
    )
    (repo / ".claude").mkdir()
    return repo


class TestReanchorClaudeMd:
    def test_inserts_callout_when_missing(self, fake_repo: Path) -> None:
        applied = mig.reanchor_claude_md(fake_repo)
        assert applied is True

        content = (fake_repo / "CLAUDE.md").read_text()
        assert ".living/INDEX.md" in content
        assert "Knowledge index" in content
        # Callout sits inside Quick Orientation section, before the numbered list
        assert content.index("Knowledge index") < content.index("Read `.living/` first")

    def test_idempotent(self, fake_repo: Path) -> None:
        mig.reanchor_claude_md(fake_repo)
        applied2 = mig.reanchor_claude_md(fake_repo)
        assert applied2 is False

    def test_skips_when_already_mentioned(self, fake_repo: Path) -> None:
        (fake_repo / "CLAUDE.md").write_text(
            "# Project\n\nSee .living/INDEX.md for the map.\n", encoding="utf-8"
        )
        applied = mig.reanchor_claude_md(fake_repo)
        assert applied is False

    def test_returns_false_when_claude_md_missing(self, tmp_path: Path) -> None:
        empty_repo = tmp_path / "no-claude"
        empty_repo.mkdir()
        applied = mig.reanchor_claude_md(empty_repo)
        assert applied is False

    def test_dry_run_reports_without_writing(self, fake_repo: Path) -> None:
        original = (fake_repo / "CLAUDE.md").read_text()
        applied = mig.reanchor_claude_md(fake_repo, dry_run=True)
        assert applied is True
        # File untouched
        assert (fake_repo / "CLAUDE.md").read_text() == original


class TestTopupHooks:
    def test_installs_all_hooks_on_empty_settings(self, fake_repo: Path) -> None:
        applied = mig.topup_hooks(fake_repo)
        assert applied is True

        settings_path = fake_repo / ".claude" / "settings.local.json"
        assert settings_path.exists()
        settings = json.loads(settings_path.read_text())
        hook_cmds = {
            Path(h["command"]).name
            for entries in settings["hooks"].values()
            for entry in entries
            for h in entry.get("hooks", [])
        }
        # All 5 default hooks present
        assert "mycelium-health.sh" in hook_cmds
        assert "mycelium-post-action.sh" in hook_cmds
        assert "mycelium-stop-check.sh" in hook_cmds
        assert "mycelium-activity-tracker.sh" in hook_cmds
        assert "mycelium-read-tracker.sh" in hook_cmds

    def test_idempotent(self, fake_repo: Path) -> None:
        mig.topup_hooks(fake_repo)
        applied2 = mig.topup_hooks(fake_repo)
        # Second run is a no-op (no hook signature change)
        assert applied2 is False

    def test_preserves_existing_unrelated_settings(self, fake_repo: Path) -> None:
        existing = {
            "permissions": {"allow": ["Bash(git status:*)"]},
            "hooks": {},
        }
        (fake_repo / ".claude" / "settings.local.json").write_text(
            json.dumps(existing, indent=2), encoding="utf-8"
        )
        mig.topup_hooks(fake_repo)
        settings = json.loads(
            (fake_repo / ".claude" / "settings.local.json").read_text()
        )
        # Unrelated permissions preserved
        assert settings["permissions"]["allow"] == ["Bash(git status:*)"]


class TestRegenIndex:
    def test_writes_index_md_with_summary(self, fake_repo: Path) -> None:
        applied = mig.regen_index(fake_repo)
        assert applied is True

        index = (fake_repo / ".living" / "INDEX.md").read_text()
        assert "<!-- BEGIN KNOWLEDGE SUMMARY -->" in index
        # The two tagged entries should produce a tag cluster
        assert "**test** (2 entries)" in index

    def test_returns_false_without_living_dir(self, tmp_path: Path) -> None:
        no_living = tmp_path / "no-living"
        no_living.mkdir()
        applied = mig.regen_index(no_living)
        assert applied is False


class TestMigrateOne:
    def test_runs_all_actions_idempotently(self, fake_repo: Path) -> None:
        # First run: 3 actions (CLAUDE.md, hooks, INDEX.md) all applied
        result1 = mig.migrate_one(fake_repo)
        applied_count1 = sum(1 for v in result1.values() if v == "applied")
        assert applied_count1 == 3

        # Second run: structural changes (CLAUDE.md, hooks) skipped.
        # INDEX.md regen always runs (data refresh), but the structural
        # changes are the ones that signal "still needs migration".
        result2 = mig.migrate_one(fake_repo)
        assert result2["CLAUDE.md re-anchor"] == "skipped (already up-to-date)"
        assert result2["Hooks top-up"] == "skipped (already up-to-date)"

    def test_skips_when_no_living_dir(self, tmp_path: Path) -> None:
        no_living = tmp_path / "not-mycelium"
        no_living.mkdir()
        result = mig.migrate_one(no_living)
        assert "_skip" in result


class TestScanForRepos:
    def test_finds_only_dirs_with_living(self, tmp_path: Path) -> None:
        (tmp_path / "good-repo" / ".living").mkdir(parents=True)
        (tmp_path / "another-good" / ".living").mkdir(parents=True)
        (tmp_path / "no-living").mkdir()
        (tmp_path / "file-not-dir").write_text("hi")

        repos = mig.scan_for_repos(tmp_path)
        names = {r.name for r in repos}
        assert names == {"good-repo", "another-good"}


class TestCli:
    def test_dry_run_does_not_write(self, fake_repo: Path) -> None:
        original_claude = (fake_repo / "CLAUDE.md").read_text()
        result = subprocess.run(
            [
                sys.executable,
                str(_SCRIPT_DIR / "migrate_existing_repos.py"),
                "--repo",
                str(fake_repo),
                "--dry-run",
                "--skip-memory",
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        # CLAUDE.md untouched
        assert (fake_repo / "CLAUDE.md").read_text() == original_claude
        # No INDEX.md written
        assert not (fake_repo / ".living" / "INDEX.md").exists()
        assert "applied" in result.stdout
