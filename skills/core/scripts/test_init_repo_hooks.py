#!/usr/bin/env python3
"""Tests for hook installation and consolidation in init_repo.py.

Focus: prevent duplicate hooks (same script registered at different paths)
and consolidate any pre-existing duplicates.
"""

import json
import sys
from pathlib import Path


_SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPT_DIR))

import init_repo as ir  # noqa: E402


def _make_hook_dir(parent: Path, label: str) -> Path:
    """Create a directory containing all 5 mycelium hook scripts as real files.
    `label` differentiates marketplace/dev/third in tests."""
    if label == "marketplace":
        d = parent / ".claude" / "plugins" / "marketplaces" / "mycelium" / "hooks"
    elif label == "dev":
        d = parent / "code" / "mycelium" / "skills" / "core" / "hooks"
    else:
        d = parent / "third" / "skills" / "core" / "hooks"
    d.mkdir(parents=True)
    for bn in ir.MYCELIUM_HOOK_BASENAMES:
        (d / bn).write_text("#!/bin/sh\n")
    return d


def _build_settings_with_dup_hooks(marketplace: Path, dev: Path, third: Path) -> dict:
    """Settings with mycelium-health.sh registered twice (marketplace + dev)
    and mycelium-post-action.sh registered three times across different paths.
    """
    return {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(marketplace / "mycelium-health.sh"),
                        },
                        {"type": "command", "command": str(dev / "mycelium-health.sh")},
                    ],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(marketplace / "mycelium-post-action.sh"),
                        },
                        {
                            "type": "command",
                            "command": str(dev / "mycelium-post-action.sh"),
                        },
                        {
                            "type": "command",
                            "command": str(third / "mycelium-post-action.sh"),
                        },
                    ],
                }
            ],
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {
                            "type": "command",
                            "command": str(marketplace / "mycelium-stop-check.sh"),
                        },
                        {
                            "type": "command",
                            "command": str(dev / "mycelium-stop-check.sh"),
                        },
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
    def test_keeps_marketplace_when_both_exist(self, tmp_path: Path) -> None:
        marketplace = _make_hook_dir(tmp_path, "marketplace")
        dev = _make_hook_dir(tmp_path, "dev")
        third = _make_hook_dir(tmp_path, "third")
        settings = _build_settings_with_dup_hooks(marketplace, dev, third)
        removed, kept = ir._consolidate_duplicate_hooks(settings["hooks"])
        # 2+3+2 = 7 entries total before; canonical = 3 (one per basename).
        # So 4 should be removed.
        assert removed == 4

        cmds = _flatten_hook_commands(settings["hooks"])
        assert str(marketplace / "mycelium-health.sh") in cmds
        assert str(dev / "mycelium-health.sh") not in cmds
        assert str(marketplace / "mycelium-post-action.sh") in cmds
        assert str(dev / "mycelium-post-action.sh") not in cmds
        assert str(third / "mycelium-post-action.sh") not in cmds
        assert str(marketplace / "mycelium-stop-check.sh") in cmds
        assert str(dev / "mycelium-stop-check.sh") not in cmds

        assert kept["mycelium-health.sh"] == str(marketplace / "mycelium-health.sh")

    def test_no_op_when_only_one_path_per_basename(self, tmp_path: Path) -> None:
        marketplace = _make_hook_dir(tmp_path, "marketplace")
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(marketplace / "mycelium-health.sh"),
                            },
                        ],
                    }
                ],
            }
        }
        removed, _ = ir._consolidate_duplicate_hooks(settings["hooks"])
        assert removed == 0
        cmds = _flatten_hook_commands(settings["hooks"])
        assert cmds == [str(marketplace / "mycelium-health.sh")]

    def test_keeps_dev_when_no_marketplace_present(self, tmp_path: Path) -> None:
        dev = _make_hook_dir(tmp_path, "dev")
        third = _make_hook_dir(tmp_path, "third")
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(dev / "mycelium-health.sh"),
                            },
                            {
                                "type": "command",
                                "command": str(third / "mycelium-health.sh"),
                            },
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
                            {
                                "type": "command",
                                "command": "/path/a/some-other-hook.sh",
                            },
                            {
                                "type": "command",
                                "command": "/path/b/some-other-hook.sh",
                            },
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

    def test_drops_stale_entry_when_replacement_available(self, tmp_path: Path) -> None:
        """A hook whose path no longer exists is removed if the caller
        supplied a known-good replacement for that basename."""
        # Create a real on-disk replacement so the test's replacement map
        # points at a path that actually exists
        good_dir = tmp_path / "good-hooks"
        good_dir.mkdir()
        good_path = good_dir / "mycelium-health.sh"
        good_path.write_text("#!/bin/sh\n")

        stale = "/path/that/does/not/exist/mycelium-health.sh"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": stale}],
                    }
                ],
            }
        }
        removed, kept = ir._consolidate_duplicate_hooks(
            settings["hooks"],
            valid_replacement_for={"mycelium-health.sh": str(good_path)},
        )
        assert removed == 1
        # No live entries existed, so kept_by_basename has no entry — the
        # install pass will pick up the basename as missing and add the fresh
        # path next.
        assert "mycelium-health.sh" not in kept
        cmds = _flatten_hook_commands(settings["hooks"])
        assert cmds == []

    def test_keeps_stale_entry_when_no_replacement_available(self) -> None:
        """If the caller didn't supply a replacement (e.g. the script can't
        find a valid hooks dir), preserve stale entries to avoid making a
        bad situation worse during transient filesystem hiccups."""
        stale = "/missing/mycelium-health.sh"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": stale}],
                    }
                ],
            }
        }
        removed, _ = ir._consolidate_duplicate_hooks(settings["hooks"])
        assert removed == 0
        cmds = _flatten_hook_commands(settings["hooks"])
        assert cmds == [stale]

    def test_drops_stale_keeps_live_among_duplicates(self, tmp_path: Path) -> None:
        """Mix of stale and live entries for one basename: drop stale, keep
        live. Marketplace preference applies among live entries only."""
        # Build a real "marketplace" path on disk
        marketplace_dir = tmp_path / "marketplaces" / "mycelium" / "hooks"
        marketplace_dir.mkdir(parents=True)
        marketplace_health = marketplace_dir / "mycelium-health.sh"
        marketplace_health.write_text("#!/bin/sh\n")

        stale = "/old/install/mycelium-health.sh"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {"type": "command", "command": stale},
                            {"type": "command", "command": str(marketplace_health)},
                        ],
                    }
                ],
            }
        }
        removed, kept = ir._consolidate_duplicate_hooks(
            settings["hooks"],
            valid_replacement_for={"mycelium-health.sh": str(marketplace_health)},
        )
        assert removed == 1
        cmds = _flatten_hook_commands(settings["hooks"])
        assert cmds == [str(marketplace_health)]
        assert kept["mycelium-health.sh"] == str(marketplace_health)


class TestInstallClaudeHooksIdempotent:
    def test_basename_match_prevents_double_install(self, tmp_path: Path) -> None:
        """If a hook is already registered at any path, don't add another
        entry pointing at a different path."""
        marketplace = _make_hook_dir(tmp_path, "marketplace")
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)

        # Pre-seed settings with real marketplace-path entries for all 5 hooks
        def _entry(bn: str) -> dict:
            return {"type": "command", "command": str(marketplace / bn)}

        settings = {
            "hooks": {
                "SessionStart": [
                    {"matcher": "", "hooks": [_entry("mycelium-health.sh")]}
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            _entry("mycelium-post-action.sh"),
                            _entry("mycelium-data-tracker.sh"),
                        ],
                    },
                    {
                        "matcher": "Edit|Write",
                        "hooks": [_entry("mycelium-activity-tracker.sh")],
                    },
                    {"matcher": "Read", "hooks": [_entry("mycelium-read-tracker.sh")]},
                ],
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            _entry("mycelium-stop-check.sh"),
                            _entry("mycelium-data-lineage-stop.sh"),
                        ],
                    }
                ],
            }
        }
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )

        # Run install — should be a complete no-op (all 7 hooks already
        # live at marketplace paths)
        ir.install_claude_hooks(repo)

        result = json.loads((repo / ".claude" / "settings.local.json").read_text())
        cmds = _flatten_hook_commands(result["hooks"])
        # Still exactly 7 entries, all marketplace paths
        assert len(cmds) == len(ir.MYCELIUM_HOOK_BASENAMES)
        for cmd in cmds:
            assert "/marketplaces/" in cmd

    def test_consolidates_existing_duplicates_then_installs_missing(
        self, tmp_path: Path
    ) -> None:
        """The SNP-tree scenario: pre-existing marketplace+dev duplicates for
        3 hooks, missing activity-tracker and read-tracker entirely.
        Expected: duplicates collapse to marketplace path; missing ones get
        installed at the script's hooks_dir."""
        marketplace = _make_hook_dir(tmp_path, "marketplace")
        dev_dir = _make_hook_dir(tmp_path, "dev")

        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(marketplace / "mycelium-health.sh"),
                            },
                            {
                                "type": "command",
                                "command": str(dev_dir / "mycelium-health.sh"),
                            },
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(marketplace / "mycelium-post-action.sh"),
                            },
                            {
                                "type": "command",
                                "command": str(dev_dir / "mycelium-post-action.sh"),
                            },
                        ],
                    }
                ],
                "Stop": [
                    {
                        "matcher": "",
                        "hooks": [
                            {
                                "type": "command",
                                "command": str(marketplace / "mycelium-stop-check.sh"),
                            },
                            {
                                "type": "command",
                                "command": str(dev_dir / "mycelium-stop-check.sh"),
                            },
                        ],
                    }
                ],
            }
        }
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )

        ir.install_claude_hooks(repo)

        result = json.loads((repo / ".claude" / "settings.local.json").read_text())
        cmds = _flatten_hook_commands(result["hooks"])
        basenames = {Path(c).name for c in cmds}
        # All mycelium hooks present, exactly once each
        assert basenames == ir.MYCELIUM_HOOK_BASENAMES
        assert len(cmds) == len(ir.MYCELIUM_HOOK_BASENAMES)

        # Pre-existing 3 hooks consolidated to marketplace path
        for bn in (
            "mycelium-health.sh",
            "mycelium-post-action.sh",
            "mycelium-stop-check.sh",
        ):
            matches = [c for c in cmds if Path(c).name == bn]
            assert len(matches) == 1
            assert "/marketplaces/" in matches[0]

    def test_stale_existing_path_replaced_with_fresh_install(
        self, tmp_path: Path
    ) -> None:
        """Codex P1 case: existing entry points at a path that no longer
        exists. Old behavior pre-fix: full-path mismatch added a new entry
        (broken hook stayed). Mid-fix: basename match treated stale as
        registered, leaving the hook non-functional. New behavior: stale
        entry is dropped, fresh one installed at the resolved hooks_dir."""
        repo = tmp_path / "repo"
        (repo / ".claude").mkdir(parents=True)
        # Pre-seed with a stale path (file does not exist)
        stale = "/old/removed/install/mycelium-health.sh"
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "matcher": "",
                        "hooks": [{"type": "command", "command": stale}],
                    }
                ],
            }
        }
        (repo / ".claude" / "settings.local.json").write_text(
            json.dumps(settings, indent=2), encoding="utf-8"
        )

        ir.install_claude_hooks(repo)

        result = json.loads((repo / ".claude" / "settings.local.json").read_text())
        cmds = _flatten_hook_commands(result["hooks"])
        # Stale path must be gone
        assert stale not in cmds
        # mycelium-health.sh must be registered at the resolved hooks_dir
        # (which is the real mycelium repo's hooks dir under this test run)
        health_entries = [c for c in cmds if Path(c).name == "mycelium-health.sh"]
        assert len(health_entries) == 1
        assert Path(health_entries[0]).exists()
