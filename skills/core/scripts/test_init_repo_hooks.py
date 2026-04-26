#!/usr/bin/env python3
"""Tests for hook installation and consolidation in init_repo.py.

Focus: prevent duplicate hooks (same script registered at different paths)
and consolidate any pre-existing duplicates.
"""

import json
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import init_repo as ir  # noqa: E402

MARKETPLACE_PREFIX = (
    "/Users/test/.claude/plugins/marketplaces/mycelium/skills/core/hooks"
)
DEV_PREFIX = "/Users/test/code/mycelium/skills/core/hooks"
THIRD_PREFIX = "/some/other/path/skills/core/hooks"


def _build_settings_with_dup_hooks() -> dict:
    """Settings with mycelium-health.sh registered twice (marketplace + dev)
    and mycelium-post-action.sh registered three times across different paths.
    """
    return {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": f"{MARKETPLACE_PREFIX}/mycelium-health.sh"},
                        {"type": "command", "command": f"{DEV_PREFIX}/mycelium-health.sh"},
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {"type": "command", "command": f"{MARKETPLACE_PREFIX}/mycelium-post-action.sh"},
                        {"type": "command", "command": f"{DEV_PREFIX}/mycelium-post-action.sh"},
                        {"type": "command", "command": f"{THIRD_PREFIX}/mycelium-post-action.sh"},
                    ],
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": f"{MARKETPLACE_PREFIX}/mycelium-stop-check.sh"},
                        {"type": "command", "command": f"{DEV_PREFIX}/mycelium-stop-check.sh"},
                    ],
                }
            ],
        }
    }


def _flatten_hook_commands(hooks: dict) -> list[str]:
    """All hook command strings, flattened."""
    return [
        h["command"]
        for entries in hooks.values()
        for entry in entries
        for h in entry.get("hooks", [])
    ]


class TestConsolidateDuplicateHooks:
    def test_keeps_marketplace_when_both_exist(self) -> None:
        settings = _build_settings_with_dup_hooks()
        removed, kept = ir._consolidate_duplicate_hooks(settings["hooks"])
        # 2+3+2 = 7 entries total before; canonical = 3 (one per basename).
        # So 4 should be removed.
        assert removed == 4

        cmds = _flatten_hook_commands(settings["hooks"])
        assert f"{MARKETPLACE_PREFIX}/mycelium-health.sh" in cmds
        assert f"{DEV_PREFIX}/mycelium-health.sh" not in cmds
        assert f"{MARKETPLACE_PREFIX}/mycelium-post-action.sh" in cmds
        assert f"{DEV_PREFIX}/mycelium-post-action.sh" not in cmds
        assert f"{THIRD_PREFIX}/mycelium-post-action.sh" not in cmds
        assert f"{MARKETPLACE_PREFIX}/mycelium-stop-check.sh" in cmds
        assert f"{DEV_PREFIX}/mycelium-stop-check.sh" not in cmds

        assert kept["mycelium-health.sh"] == f"{MARKETPLACE_PREFIX}/mycelium-health.sh"

    def test_no_op_when_only_one_path_per_basename(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": f"{MARKETPLACE_PREFIX}/mycelium-health.sh"},
                        ],
                    }
                ],
            }
        }
        removed, _ = ir._consolidate_duplicate_hooks(settings["hooks"])
        assert removed == 0
        cmds = _flatten_hook_commands(settings["hooks"])
        assert cmds == [f"{MARKETPLACE_PREFIX}/mycelium-health.sh"]

    def test_keeps_dev_when_no_marketplace_present(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": f"{DEV_PREFIX}/mycelium-health.sh"},
                            {"type": "command", "command": f"{THIRD_PREFIX}/mycelium-health.sh"},
                        ],
                    }
                ],
            }
        }
        removed, kept = ir._consolidate_duplicate_hooks(settings["hooks"])
        assert removed == 1
        # Longest path wins as fallback (more specific)
        cmds = _flatten_hook_commands(settings["hooks"])
        assert len(cmds) == 1
        assert kept["mycelium-health.sh"] == cmds[0]

    def test_ignores_non_mycelium_hooks(self) -> None:
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": "/path/a/some-other-hook.sh"},
                            {"type": "command", "command": "/path/b/some-other-hook.sh"},
                        ],
                    }
                ],
            }
        }
        removed, _ = ir._consolidate_duplicate_hooks(settings["hooks"])
        assert removed == 0
        # Both non-mycelium entries preserved
        cmds = _flatten_hook_commands(settings["hooks"])
        assert len(cmds) == 2


class TestInstallClaudeHooksIdempotent:
    def test_basename_match_prevents_double_install(self, tmp_path: Path) -> None:
        """If a hook is already registered at any path, don't add another
        entry pointing at a different path."""
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        # Pre-seed settings with marketplace-path entries for all 5 hooks
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{MARKETPLACE_PREFIX}/mycelium-health.sh",
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{MARKETPLACE_PREFIX}/mycelium-post-action.sh",
                            }
                        ],
                    },
                    {
                        "matcher": "Edit|Write",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{MARKETPLACE_PREFIX}/mycelium-activity-tracker.sh",
                            }
                        ],
                    },
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{MARKETPLACE_PREFIX}/mycelium-read-tracker.sh",
                            }
                        ],
                    },
                ],
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"{MARKETPLACE_PREFIX}/mycelium-stop-check.sh",
                            }
                        ],
                    }
                ],
            }
        }
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )

        # Run install — should be a complete no-op (5 hooks already registered
        # at marketplace paths, even though the script's hooks_dir resolves to
        # the dev repo's location)
        ir.install_claude_hooks(repo)

        result = json.loads(
            (repo / ".claude" / "settings.local.json").read_text()
        )
        cmds = _flatten_hook_commands(result["hooks"])
        # Still exactly 5 entries, all marketplace paths
        assert len(cmds) == 5
        for cmd in cmds:
            assert "/marketplaces/" in cmd

    def test_consolidates_existing_duplicates_then_installs_missing(
        self, tmp_path: Path
    ) -> None:
        """The SNP-tree scenario: pre-existing marketplace+dev duplicates for
        3 hooks, missing activity-tracker and read-tracker entirely.
        Expected: duplicates collapse to marketplace path; missing ones get
        installed at the script's hooks_dir."""
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps(_build_settings_with_dup_hooks(), indent=2),
            encoding="utf-8",
        )

        ir.install_claude_hooks(repo)

        result = json.loads(
            (repo / ".claude" / "settings.local.json").read_text()
        )
        cmds = _flatten_hook_commands(result["hooks"])
        basenames = {Path(c).name for c in cmds}
        # All 5 mycelium hooks present, exactly once each
        assert basenames == ir.MYCELIUM_HOOK_BASENAMES
        assert len(cmds) == 5

        # Pre-existing 3 hooks consolidated to marketplace path
        for bn in (
            "mycelium-health.sh",
            "mycelium-post-action.sh",
            "mycelium-stop-check.sh",
        ):
            matches = [c for c in cmds if Path(c).name == bn]
            assert len(matches) == 1
            assert "/marketplaces/" in matches[0]
