#!/usr/bin/env python3
"""Tests for migrate_existing_repos.py."""

import json
import os
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
        assert '"$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"' in content
        assert "python3 skills/core/scripts/recall_lessons.py" not in content
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

    def test_repairs_legacy_recall_command_when_already_anchored(
        self, fake_repo: Path
    ) -> None:
        (fake_repo / "CLAUDE.md").write_text(
            "# Project\n\nSee .living/INDEX.md.\n\n"
            "Run `python3 skills/core/scripts/recall_lessons.py --tag test`.\n",
            encoding="utf-8",
        )

        assert mig.reanchor_claude_md(fake_repo) is True
        content = (fake_repo / "CLAUDE.md").read_text()
        assert mig.LEGACY_RECALL_COMMAND not in content
        assert mig.PLUGIN_RECALL_COMMAND in content

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
        # All 6 registered Claude hooks present. Lineage consolidation is an
        # internal phase of mycelium-stop-check.sh, not a sibling Stop handler.
        assert "mycelium-health.sh" in hook_cmds
        assert "mycelium-post-action.sh" in hook_cmds
        assert "mycelium-stop-check.sh" in hook_cmds
        assert "mycelium-activity-tracker.sh" in hook_cmds
        assert "mycelium-read-tracker.sh" in hook_cmds
        assert "mycelium-data-tracker.sh" in hook_cmds
        assert "mycelium-data-lineage-stop.sh" not in hook_cmds

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

    def test_dry_run_detects_missing_data_lineage_hooks(self, fake_repo: Path) -> None:
        mig.topup_hooks(fake_repo)
        settings_path = fake_repo / ".claude" / "settings.local.json"
        settings = json.loads(settings_path.read_text())
        missing = {"mycelium-data-tracker.sh"}
        for groups in settings["hooks"].values():
            for group in groups:
                group["hooks"] = [
                    hook
                    for hook in group.get("hooks", [])
                    if Path(hook.get("command", "")).name not in missing
                ]
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        assert mig.topup_hooks(fake_repo, dry_run=True) is True

    def test_dry_run_and_migration_repair_wrong_matcher(self, fake_repo: Path) -> None:
        mig.topup_hooks(fake_repo)
        settings_path = fake_repo / ".claude" / "settings.local.json"
        settings = json.loads(settings_path.read_text())
        health_group = settings["hooks"]["SessionStart"][0]
        health_group["matcher"] = "startup"
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        assert mig.topup_hooks(fake_repo, dry_run=True) is True
        assert mig.topup_hooks(fake_repo) is True

        repaired = json.loads(settings_path.read_text())
        health_groups = [
            group
            for group in repaired["hooks"]["SessionStart"]
            if group.get("matcher", "") == ""
        ]
        assert len(health_groups) == 1
        assert any(
            mig.ir._hook_basename(handler["command"]) == "mycelium-health.sh"
            for handler in health_groups[0]["hooks"]
        )

    def test_dry_run_and_migration_repair_wrong_event(self, fake_repo: Path) -> None:
        mig.topup_hooks(fake_repo)
        settings_path = fake_repo / ".claude" / "settings.local.json"
        settings = json.loads(settings_path.read_text())
        health_group = settings["hooks"]["SessionStart"][0]
        health = next(
            handler
            for handler in health_group["hooks"]
            if mig.ir._hook_basename(handler["command"]) == "mycelium-health.sh"
        )
        health_group["hooks"].remove(health)
        settings["hooks"]["Stop"][0]["hooks"].append(health)
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        assert mig.topup_hooks(fake_repo, dry_run=True) is True
        assert mig.topup_hooks(fake_repo) is True

        repaired = json.loads(settings_path.read_text())
        locations = [
            (event, group.get("matcher", ""))
            for event, groups in repaired["hooks"].items()
            for group in groups
            for handler in group.get("hooks", [])
            if mig.ir._hook_basename(handler.get("command", ""))
            == "mycelium-health.sh"
        ]
        assert locations == [("SessionStart", "")]

    def test_dry_run_and_migration_replace_stale_path(self, fake_repo: Path) -> None:
        mig.topup_hooks(fake_repo)
        settings_path = fake_repo / ".claude" / "settings.local.json"
        settings = json.loads(settings_path.read_text())
        health = settings["hooks"]["SessionStart"][0]["hooks"][0]
        health["command"] = "/removed/install/mycelium-health.sh"
        settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")

        assert mig.topup_hooks(fake_repo, dry_run=True) is True
        assert mig.topup_hooks(fake_repo) is True

        repaired = json.loads(settings_path.read_text())
        commands = [
            handler["command"]
            for group in repaired["hooks"]["SessionStart"]
            for handler in group.get("hooks", [])
            if mig.ir._hook_basename(handler.get("command", ""))
            == "mycelium-health.sh"
        ]
        assert len(commands) == 1
        assert mig.ir._hook_command_path(commands[0]).exists()


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


class TestMigrateRuntimeState:
    def test_refuses_symlinked_legacy_parent_before_writing(
        self, fake_repo: Path
    ) -> None:
        outside_claude = fake_repo.parent / "outside-claude"
        outside_claude.mkdir()
        legacy = outside_claude / "last-session.md"
        original = "host-private legacy session\n"
        legacy.write_text(original)
        (fake_repo / ".claude").rmdir()
        (fake_repo / ".claude").symlink_to(
            outside_claude, target_is_directory=True
        )

        with pytest.raises(ValueError, match="symlink"):
            mig.migrate_runtime_state(fake_repo)

        assert legacy.read_text() == original
        assert not (fake_repo / ".mycelium").exists()

    def test_refuses_symlinked_state_before_writing(self, fake_repo: Path) -> None:
        victim = fake_repo.parent / "outside-state"
        victim.mkdir()
        (victim / "plugin-root").write_text("do-not-overwrite\n")
        (fake_repo / ".mycelium").symlink_to(victim, target_is_directory=True)

        with pytest.raises(ValueError, match="symlink"):
            mig.migrate_runtime_state(fake_repo)

        assert (victim / "plugin-root").read_text() == "do-not-overwrite\n"
        assert sorted(path.name for path in victim.iterdir()) == ["plugin-root"]


class TestMigrateOne:
    def test_runs_all_actions_idempotently(self, fake_repo: Path) -> None:
        # First run applies guidance, state, Claude hooks, and INDEX refresh.
        # Codex hooks are plugin-bundled, so a repo with no legacy registrations
        # needs no project-local Codex change.
        result1 = mig.migrate_one(fake_repo)
        applied_count1 = sum(1 for v in result1.values() if v == "applied")
        assert applied_count1 == 6
        canonical = (fake_repo / "MYCELIUM.md").read_text()
        assert "# Fake Project" in canonical
        assert "python3 skills/core/scripts/recall_lessons.py" not in canonical
        assert "$(cat .mycelium/plugin-root)" in canonical
        assert (fake_repo / ".mycelium" / "plugin-root").is_file()

        # Second run: structural changes (CLAUDE.md, hooks) skipped.
        # INDEX.md regen always runs (data refresh), but the structural
        # changes are the ones that signal "still needs migration".
        result2 = mig.migrate_one(fake_repo)
        assert result2["CLAUDE.md re-anchor"] == "skipped (already up-to-date)"
        assert result2["Cross-agent guidance"] == "skipped (already up-to-date)"
        assert result2["Runtime state migration"] == "skipped (already up-to-date)"
        assert result2["Claude hooks top-up"] == "skipped (already up-to-date)"
        assert result2["Legacy Codex hook cleanup"] == (
            "skipped (already up-to-date)"
        )
        assert result2["Todo contract"] == "skipped (already up-to-date)"

    def test_idempotent_run_does_not_rewrite_claude_settings(
        self, fake_repo: Path
    ) -> None:
        mig.migrate_one(fake_repo)
        settings = fake_repo / ".claude" / "settings.local.json"
        old_ns = 946_684_800_000_000_000
        os.utime(settings, ns=(old_ns, old_ns))

        result = mig.migrate_one(fake_repo)

        assert result["Claude hooks top-up"] == "skipped (already up-to-date)"
        assert settings.stat().st_mtime_ns == old_ns

    @pytest.mark.parametrize("name", ["MYCELIUM.md", "CLAUDE.md", "AGENTS.md"])
    def test_refuses_symlinked_guidance_before_any_migration_write(
        self, fake_repo: Path, name: str
    ) -> None:
        target = fake_repo / name
        target.unlink(missing_ok=True)
        victim = fake_repo.parent / f"outside-{name}"
        original = "# External file\n\nDo not modify me.\n"
        victim.write_text(original)
        target.symlink_to(victim)
        original_claude = (
            (fake_repo / "CLAUDE.md").read_text()
            if name != "CLAUDE.md"
            else None
        )

        with pytest.raises(ValueError, match="symlink"):
            mig.migrate_one(fake_repo)

        assert victim.read_text() == original
        if original_claude is not None:
            assert (fake_repo / "CLAUDE.md").read_text() == original_claude
        assert not (fake_repo / ".mycelium").exists()

    @pytest.mark.parametrize(
        ("relative_path", "victim_content"),
        [
            (".claude/settings.local.json", "{}\n"),
            (
                ".codex/hooks.json",
                json.dumps(
                    {
                        "hooks": {
                            "PostToolUse": [
                                {
                                    "matcher": "Bash",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/old/mycelium-post-action.sh",
                                        },
                                        {"type": "command", "command": "/user/hook.sh"},
                                    ],
                                }
                            ]
                        }
                    }
                )
                + "\n",
            ),
            (".claude/last-session.md", "host-private-session-content\n"),
            ("todo/TODO_REGISTRY.md", None),
            (".living/INDEX.md", "# External index\n"),
        ],
    )
    def test_refuses_other_symlinked_managed_files_before_any_migration_write(
        self,
        fake_repo: Path,
        relative_path: str,
        victim_content: str | None,
    ) -> None:
        target = fake_repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        victim = fake_repo.parent / relative_path.replace("/", "-").lstrip(".")
        if victim_content is not None:
            victim.write_text(victim_content)
        target.symlink_to(victim)
        original_claude = (fake_repo / "CLAUDE.md").read_text()

        with pytest.raises(ValueError, match="symlink"):
            mig.migrate_one(fake_repo)

        if victim_content is None:
            assert not victim.exists()
        else:
            assert victim.read_text() == victim_content
        assert (fake_repo / "CLAUDE.md").read_text() == original_claude
        assert not (fake_repo / ".mycelium").exists()

    def test_refuses_nested_living_symlink_before_any_migration_write(
        self, fake_repo: Path
    ) -> None:
        findings = fake_repo / ".living" / "findings"
        findings.mkdir()
        victim = fake_repo.parent / "outside-living-input.md"
        original = "host-private-living-content\n"
        victim.write_text(original)
        (findings / "linked.md").symlink_to(victim)
        original_claude = (fake_repo / "CLAUDE.md").read_text()

        with pytest.raises(ValueError, match="symlink"):
            mig.migrate_one(fake_repo)

        assert victim.read_text() == original
        assert (fake_repo / "CLAUDE.md").read_text() == original_claude
        assert not (fake_repo / ".mycelium").exists()

    def test_repairs_guidance_and_removes_legacy_project_codex_hooks(
        self, fake_repo: Path
    ) -> None:
        mig.migrate_one(fake_repo)
        for name in ("CLAUDE.md", "MYCELIUM.md"):
            path = fake_repo / name
            path.write_text(
                path.read_text().replace(
                    mig.PLUGIN_RECALL_COMMAND, mig.LEGACY_RECALL_COMMAND
                ),
                encoding="utf-8",
            )
        codex_dir = fake_repo / ".codex"
        codex_dir.mkdir(exist_ok=True)
        hooks_path = codex_dir / "hooks.json"
        hooks = {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "exec_command",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    "MYCELIUM_HOOK_HOST=codex "
                                    "/removed/cache/mycelium-post-action.sh"
                                ),
                            }
                        ],
                    }
                ],
                "PreToolUse": [{"matcher": "custom", "hooks": []}],
            }
        }
        hooks_path.write_text(json.dumps(hooks, indent=2), encoding="utf-8")

        assert mig.topup_codex_hooks(fake_repo, dry_run=True) is True
        result = mig.migrate_one(fake_repo)

        assert result["Cross-agent guidance"] == "applied"
        assert result["Legacy Codex hook cleanup"] == "applied"
        for name in ("CLAUDE.md", "MYCELIUM.md"):
            content = (fake_repo / name).read_text()
            assert mig.LEGACY_RECALL_COMMAND not in content
            assert mig.PLUGIN_RECALL_COMMAND in content
        repaired_hooks = json.loads(hooks_path.read_text())
        assert "PostToolUse" not in repaired_hooks["hooks"]
        assert repaired_hooks["hooks"]["PreToolUse"] == [
            {"matcher": "custom", "hooks": []}
        ]

    def test_repairs_obsolete_project_local_codex_hook_guidance(
        self, fake_repo: Path
    ) -> None:
        stale_guidance = (
            "# Existing Mycelium guidance\n\n"
            "### Automated Enforcement\n\n"
            "Mycelium hooks are auto-installed for both Claude Code and Codex during `init`.\n\n"
            "Codex requires a one-time approval for each exact project command hook.\n"
            "Until that approval, Codex intentionally skips the commands even though\n"
            "`.codex/hooks.json` contains their registrations.\n\n"
            "### Knowledge Transfer (Cross-Project)\n\nExisting transfer guidance.\n"
        )
        (fake_repo / "MYCELIUM.md").write_text(stale_guidance, encoding="utf-8")

        assert mig.ensure_cross_agent_guidance(fake_repo, dry_run=True) is True
        assert mig.ensure_cross_agent_guidance(fake_repo) is True

        repaired = (fake_repo / "MYCELIUM.md").read_text()
        assert "plugin-bundled Codex command hooks" in repaired
        assert ".codex/hooks.json` contains their registrations" not in repaired
        assert "Existing transfer guidance." in repaired

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
