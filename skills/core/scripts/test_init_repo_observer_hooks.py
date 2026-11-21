"""Claude registration of the observer and the data tracker's failure event."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import init_repo as ir  # noqa: E402


def _placements(settings: dict) -> list[tuple[str, str, str]]:
    return sorted(
        (event, group.get("matcher", ""), Path(handler["command"]).name)
        for event, groups in settings["hooks"].items()
        for group in groups
        for handler in group.get("hooks", [])
    )


def _settings(repo: Path) -> dict:
    return json.loads((repo / ".claude" / "settings.local.json").read_text())


def test_fresh_install_places_every_spec_exactly_once(tmp_path: Path) -> None:
    ir.install_claude_hooks(tmp_path)
    assert _placements(_settings(tmp_path)) == sorted(ir.CLAUDE_HOOK_SPECS)


def test_install_is_byte_idempotent(tmp_path: Path) -> None:
    ir.install_claude_hooks(tmp_path)
    first = (tmp_path / ".claude" / "settings.local.json").read_bytes()
    assert ir.install_claude_hooks(tmp_path) is False
    assert (tmp_path / ".claude" / "settings.local.json").read_bytes() == first


def test_user_pre_and_failure_hooks_survive(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    user = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/guard.sh"}]}
            ],
            "PostToolUseFailure": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": "/opt/notify.sh"}]}
            ],
        },
        "permissions": {"allow": ["Bash(ls:*)"]},
    }
    (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps(user))
    ir.install_claude_hooks(tmp_path)
    settings = _settings(tmp_path)
    placements = _placements(settings)
    assert ("PreToolUse", "Bash", "guard.sh") in placements
    assert ("PostToolUseFailure", "Bash", "notify.sh") in placements
    assert settings["permissions"] == {"allow": ["Bash(ls:*)"]}
    mycelium = [p for p in placements if p[2].startswith("mycelium-")]
    assert sorted(mycelium) == sorted(ir.CLAUDE_HOOK_SPECS)


def test_observer_under_the_wrong_event_is_moved_not_duplicated(tmp_path: Path) -> None:
    hooks_dir = ir.find_mycelium_hooks_dir()
    (tmp_path / ".claude").mkdir()
    misplaced = {
        "hooks": {
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [
                    {"type": "command", "command": str(hooks_dir / "mycelium-io-observer.sh")}
                ]}
            ]
        }
    }
    (tmp_path / ".claude" / "settings.local.json").write_text(json.dumps(misplaced))
    ir.install_claude_hooks(tmp_path)
    placements = _placements(_settings(tmp_path))
    observer = [p for p in placements if p[2] == "mycelium-io-observer.sh"]
    assert observer == sorted(s for s in ir.CLAUDE_HOOK_SPECS if s[2] == "mycelium-io-observer.sh")


def test_observer_never_emits_a_permission_decision(tmp_path: Path) -> None:
    """The registered PreToolUse command prints nothing, whatever it gets."""
    import subprocess

    hooks_dir = ir.find_mycelium_hooks_dir()
    for payload in ("", "{}", "not json", json.dumps({"hook_event_name": "PreToolUse",
                                                       "tool_name": "Bash", "cwd": "/"})):
        result = subprocess.run(
            [str(hooks_dir / "mycelium-io-observer.sh")], input=payload, text=True,
            capture_output=True, check=False, cwd=tmp_path,
        )
        assert result.returncode == 0
        assert result.stdout == ""
