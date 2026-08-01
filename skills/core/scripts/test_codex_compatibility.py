"""Cross-platform packaging and Codex hook compatibility tests."""

import json
import os
import subprocess
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import generate_index
import init_repo
import pytest
import yaml


HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
PLUGIN_HOOKS_DIR = PLUGIN_ROOT / "hooks"
KNOWLEDGE_MAP_DIR = Path(__file__).resolve().parent / "knowledge_map"
if str(KNOWLEDGE_MAP_DIR) not in sys.path:
    sys.path.insert(0, str(KNOWLEDGE_MAP_DIR))

from concept_labeler import llm_label
from propose_model import ClusterSummary


def _repo(tmp_path: Path, living: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    if living:
        living_dir = repo / ".living"
        living_dir.mkdir()
        for name in ("learnings.md", "decisions.md", "conventions.md"):
            (living_dir / name).write_text(f"# {name}\n")
    return repo


def _run_hook(
    name: str,
    repo: Path,
    payload: dict,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "codex"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(HOOKS_DIR / name)],
        cwd=repo,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def _run_plugin_hook(
    name: str,
    repo: Path,
    payload: dict,
    plugin_root: Path = PLUGIN_ROOT,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(plugin_root)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(PLUGIN_HOOKS_DIR / "mycelium-codex-dispatch.sh"), name],
        cwd=repo,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_all_skills_have_codex_metadata():
    skill_dirs = sorted(path.parent for path in (PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
    assert {path.name for path in skill_dirs} == {
        "analyze",
        "codex-review",
        "core",
        "ideas",
        "ingest",
        "report",
        "review",
        "transfer",
    }
    for skill_dir in skill_dirs:
        text = (skill_dir / "SKILL.md").read_text()
        assert text.startswith("---\nname:")
        assert "\ndescription:" in text.split("---", 2)[1]
        agent_meta = skill_dir / "agents" / "openai.yaml"
        assert agent_meta.is_file()
        default_prompt = yaml.safe_load(agent_meta.read_text())["interface"][
            "default_prompt"
        ]
        assert f"$mycelium:{skill_dir.name}" in default_prompt


def test_codex_plugin_manifest_points_to_shared_skills():
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "mycelium"
    assert manifest["skills"] == "./skills/"
    assert manifest["version"] == "0.6.0"


def test_codex_plugin_bundles_stable_dynamic_hooks():
    config = json.loads((PLUGIN_HOOKS_DIR / "hooks.json").read_text())
    commands = [
        handler["command"]
        for groups in config["hooks"].values()
        for group in groups
        for handler in group["hooks"]
    ]
    assert len(commands) == 5
    assert all("${PLUGIN_ROOT}" in command for command in commands)
    assert all("mycelium-codex-dispatch.sh" in command for command in commands)
    assert all('${PLUGIN_ROOT:-}' in command for command in commands)
    assert not any("plugins/cache" in command for command in commands)
    assert os.access(PLUGIN_HOOKS_DIR / "mycelium-codex-dispatch.sh", os.X_OK)


def test_codex_plugin_serializes_stop_lineage_and_enforcement():
    config = json.loads((PLUGIN_HOOKS_DIR / "hooks.json").read_text())
    stop_handlers = config["hooks"]["Stop"][0]["hooks"]

    assert len(stop_handlers) == 1
    assert "mycelium-stop-check.sh" in stop_handlers[0]["command"]
    assert "mycelium-data-lineage-stop.sh" not in stop_handlers[0]["command"]


def test_readme_documents_codex_install_update_and_migration():
    readme = (PLUGIN_ROOT / "README.md").read_text()
    normalized = " ".join(readme.split())
    assert "codex plugin marketplace add arjunrajlaboratory/mycelium" in readme
    assert "codex plugin add mycelium@mycelium" in readme
    assert "codex plugin marketplace upgrade mycelium" in readme
    assert "codex plugin list --json" in readme
    assert "Use `$mycelium:core` to migrate" in readme
    assert "Migration is idempotent" in readme
    assert "open `/hooks`" in readme
    assert "trust all five Mycelium command hooks" in normalized
    assert "not a Codex desktop-app slash command" in normalized
    assert "codex update" in readme


def test_hook_mtime_helper_returns_numeric_epoch(tmp_path):
    target = tmp_path / "timestamped.txt"
    target.write_text("test")
    hook_lib = HOOKS_DIR / "mycelium-hook-lib.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mycelium_file_mtime "$2"',
            "mtime-test",
            str(hook_lib),
            str(target),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().isdigit()
    assert abs(int(result.stdout.strip()) - int(target.stat().st_mtime)) <= 1


def test_hook_file_size_helper_returns_numeric_bytes(tmp_path):
    target = tmp_path / "sized.txt"
    target.write_bytes(b"portable-size")
    hook_lib = HOOKS_DIR / "mycelium-hook-lib.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; mycelium_file_size "$2"',
            "size-test",
            str(hook_lib),
            str(target),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().isdigit()
    assert int(result.stdout.strip()) == target.stat().st_size


def test_shared_skill_layout_resolves_convention_network():
    assert init_repo.find_network_conventions_dir() == PLUGIN_ROOT / "network" / "conventions"


def test_codex_cli_can_drive_optional_index_summary(monkeypatch):
    monkeypatch.setenv("MYCELIUM_AGENT_CLI", "/usr/local/bin/codex")
    command, kind = generate_index._agent_cli_command()
    assert kind == "codex"
    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ephemeral" in command
    assert "read-only" in command


def test_codex_cli_can_drive_optional_concept_labeling():
    summary = ClusterSummary(
        cluster_id=1,
        entry_ids=["e-00001"],
        size=1,
        families=["learnings"],
        projects=["demo"],
        rep_titles=["Prompt caching"],
        rep_bodies=["Cache stable prefixes."],
        tfidf_terms=["cache", "prompt"],
    )
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        return types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    "slug": "prompt-caching",
                    "label": "Prompt Caching",
                    "definition": "Caching stable prompt prefixes.",
                    "keywords": ["cache", "prompt", "prefix"],
                    "aliases": [],
                }
            ),
            stderr="",
        )

    result = llm_label(summary, claude_bin="/usr/local/bin/codex", run=fake_run)
    assert result is not None
    assert result.slug == "prompt-caching"
    assert seen["command"][:2] == ["/usr/local/bin/codex", "exec"]


def test_init_uses_plugin_bundled_codex_hooks_and_agent_guidance(tmp_path, capsys):
    repo = _repo(tmp_path, living=False)
    init_repo.create_directory_structure(repo)
    init_repo.create_todo_list(repo)
    init_repo.create_agent_guidance(repo)
    init_repo.install_codex_hooks(repo)
    install_output = capsys.readouterr().out

    assert (repo / "MYCELIUM.md").is_file()
    assert "MYCELIUM:BEGIN" in (repo / "AGENTS.md").read_text()
    assert (repo / ".mycelium" / "plugin-root").read_text().strip() == str(
        PLUGIN_ROOT
    )
    assert (repo / "todo" / "TODO_REGISTRY.md").is_file()
    assert (repo / "todo" / "TODO_ITEM_TEMPLATE.md").is_file()
    assert "Compare with public datasets" not in (
        repo / "todo" / "TODO_REGISTRY.md"
    ).read_text()
    guidance = (repo / "MYCELIUM.md").read_text()
    assert "$(cat .mycelium/plugin-root)" in guidance
    assert "python3 skills/core/scripts/" not in guidance
    assert "plugin-bundled Codex command hooks" in guidance
    assert ".codex/hooks.json` contains their registrations" not in guidance
    assert "Trusted Codex plugin hooks refresh the pointer automatically" in guidance
    assert "Re-run Mycelium migration after a plugin upgrade" not in guidance
    assert not (repo / ".codex" / "hooks.json").exists()
    assert "bundled with the Mycelium plugin via PLUGIN_ROOT" in install_output
    assert "open /hooks" in install_output
    assert "trust all five Mycelium hooks" in install_output
    assert "not the desktop app" in install_output
    assert "codex update" in install_output


def test_existing_guidance_is_carried_into_shared_canonical_file(tmp_path):
    repo = _repo(tmp_path, living=False)
    init_repo.create_directory_structure(repo)
    (repo / "CLAUDE.md").write_text(
        "# Project rules\n\nCUSTOM_SAFETY_RULE: never rewrite raw inputs.\n"
    )
    init_repo.create_agent_guidance(repo)

    canonical = (repo / "MYCELIUM.md").read_text()
    assert "CUSTOM_SAFETY_RULE" in canonical
    assert "Existing project guidance migrated from CLAUDE.md" in canonical
    assert "MYCELIUM:BEGIN" in (repo / "CLAUDE.md").read_text()


@pytest.mark.parametrize("name", ["MYCELIUM.md", "CLAUDE.md", "AGENTS.md"])
def test_agent_guidance_refuses_symlinked_targets_before_any_write(tmp_path, name):
    repo = _repo(tmp_path, living=False)
    victim = tmp_path / f"outside-{name}"
    original = (
        "# External file\n\n"
        "### Automated Enforcement\n\nDo not replace me.\n\n"
        "### Knowledge Transfer (Cross-Project)\n"
    )
    victim.write_text(original)
    (repo / name).symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        init_repo.create_agent_guidance(repo)

    assert victim.read_text() == original
    assert sorted(path.name for path in repo.iterdir()) == [".git", name]


def test_agent_guidance_atomic_update_preserves_existing_permissions(tmp_path):
    repo = _repo(tmp_path, living=False)
    claude = repo / "CLAUDE.md"
    claude.write_text("# Private project guidance\n")
    claude.chmod(0o600)

    init_repo.create_agent_guidance(repo)

    assert claude.stat().st_mode & 0o777 == 0o600
    assert "MYCELIUM:BEGIN" in claude.read_text()


def test_claude_hook_install_refuses_symlinked_settings(tmp_path):
    repo = _repo(tmp_path, living=False)
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    victim = tmp_path / "outside-claude-settings.json"
    original = "{}\n"
    victim.write_text(original)
    (claude_dir / "settings.local.json").symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        init_repo.install_claude_hooks(repo)

    assert victim.read_text() == original


def test_codex_hook_cleanup_refuses_symlinked_config(tmp_path):
    repo = _repo(tmp_path, living=False)
    codex_dir = repo / ".codex"
    codex_dir.mkdir()
    victim = tmp_path / "outside-codex-hooks.json"
    original = json.dumps(
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/old/mycelium-post-action.sh",
                            }
                        ],
                    }
                ]
            }
        }
    )
    victim.write_text(original)
    (codex_dir / "hooks.json").symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        init_repo.install_codex_hooks(repo)

    assert victim.read_text() == original


def test_existing_codex_gitignore_is_not_changed_for_plugin_hooks(tmp_path):
    repo = _repo(tmp_path, living=False)
    codex_dir = repo / ".codex"
    codex_dir.mkdir()
    (codex_dir / ".gitignore").write_text("config.toml\n")

    init_repo.install_codex_hooks(repo)

    ignored = (codex_dir / ".gitignore").read_text().splitlines()
    assert ignored == ["config.toml"]


def test_existing_project_mycelium_hooks_are_removed(tmp_path):
    repo = _repo(tmp_path, living=False)
    codex_dir = repo / ".codex"
    codex_dir.mkdir()
    hooks = {
        "hooks": {
            "PostToolUse": [
                {
                    "matcher": "exec_command",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "MYCELIUM_HOOK_HOST=codex /old/mycelium-post-action.sh",
                        },
                        {
                            "type": "command",
                            "command": "MYCELIUM_HOOK_HOST=codex /old/mycelium-data-tracker.sh",
                        },
                    ],
                }
            ],
            "PreToolUse": [{"matcher": "custom", "hooks": []}],
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(hooks), encoding="utf-8")

    init_repo.install_codex_hooks(repo)

    config = json.loads((codex_dir / "hooks.json").read_text())
    assert "PostToolUse" not in config["hooks"]
    assert config["hooks"]["PreToolUse"] == [
        {"matcher": "custom", "hooks": []}
    ]


def test_legacy_only_codex_config_and_ignore_entry_are_removed(tmp_path):
    repo = _repo(tmp_path, living=False)
    codex_dir = repo / ".codex"
    codex_dir.mkdir()
    (codex_dir / ".gitignore").write_text("config.toml\nhooks.json\n")
    config = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "MYCELIUM_HOOK_HOST=codex "
                                "/removed/cache/mycelium-health.sh"
                            ),
                        }
                    ],
                }
            ]
        }
    }
    (codex_dir / "hooks.json").write_text(json.dumps(config))

    assert init_repo.install_codex_hooks(repo) is True

    assert not (codex_dir / "hooks.json").exists()
    assert (codex_dir / ".gitignore").read_text().splitlines() == ["config.toml"]


def test_plugin_dispatcher_noops_outside_mycelium_repo(tmp_path):
    repo = _repo(tmp_path, living=False)
    result = _run_plugin_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "turn-noop"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (repo / ".mycelium").exists()


def test_plugin_dispatcher_refreshes_pointer_and_runs_hook(tmp_path):
    repo = _repo(tmp_path)
    state_dir = repo / ".mycelium"
    state_dir.mkdir()
    (state_dir / "plugin-root").write_text("/removed/cache/path\n")

    result = _run_plugin_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "turn-plugin"},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert (state_dir / "plugin-root").read_text().strip() == str(PLUGIN_ROOT)
    assert (state_dir / "active-session-log.tmp").is_file()


def test_plugin_dispatcher_refuses_symlinked_state_before_pointer_refresh(tmp_path):
    repo = _repo(tmp_path)
    victim = tmp_path / "outside-state"
    victim.mkdir()
    pointer = victim / "plugin-root"
    pointer.write_text("do-not-overwrite\n")
    (repo / ".mycelium").symlink_to(victim, target_is_directory=True)

    result = _run_plugin_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "unsafe-dispatch"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert pointer.read_text() == "do-not-overwrite\n"
    assert sorted(path.name for path in victim.iterdir()) == ["plugin-root"]


def test_plugin_dispatcher_refuses_symlinked_pointer_without_clobbering_target(
    tmp_path,
):
    repo = _repo(tmp_path)
    state_dir = repo / ".mycelium"
    state_dir.mkdir()
    victim = tmp_path / "outside-pointer"
    victim.write_text("do-not-overwrite\n")
    (state_dir / "plugin-root").symlink_to(victim)

    result = _run_plugin_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "unsafe-pointer"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert victim.read_text() == "do-not-overwrite\n"
    assert not (state_dir / "active-session-log.tmp").exists()


def test_plugin_dispatcher_refuses_symlinked_living_before_creating_state(tmp_path):
    repo = _repo(tmp_path, living=False)
    victim = tmp_path / "outside-living"
    victim.mkdir()
    (repo / ".living").symlink_to(victim, target_is_directory=True)

    result = _run_plugin_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "unsafe-living"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert list(victim.iterdir()) == []
    assert not (repo / ".mycelium").exists()


def test_init_pointer_writer_refuses_symlinked_state_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    victim = tmp_path / "outside-init-state"
    victim.mkdir()
    (repo / ".mycelium").symlink_to(victim, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        init_repo.write_plugin_root_pointer(repo)

    assert list(victim.iterdir()) == []


def test_init_pointer_writer_refuses_symlinked_pointer(tmp_path):
    repo = tmp_path / "repo"
    state_dir = repo / ".mycelium"
    state_dir.mkdir(parents=True)
    victim = tmp_path / "outside-init-pointer"
    victim.write_text("do-not-overwrite\n")
    (state_dir / "plugin-root").symlink_to(victim)

    with pytest.raises(ValueError, match="symlink"):
        init_repo.write_plugin_root_pointer(repo)

    assert victim.read_text() == "do-not-overwrite\n"


def test_hooks_reject_outside_state_override_before_creating_directory(tmp_path):
    repo = _repo(tmp_path)
    victim = tmp_path / "outside-override"

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "outside-override"},
        {"MYCELIUM_STATE_DIR": "../outside-override"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not victim.exists()


def test_hooks_reject_state_override_through_symlinked_parent(tmp_path):
    repo = _repo(tmp_path)
    victim = tmp_path / "outside-parent"
    victim.mkdir()
    (repo / "redirect").symlink_to(victim, target_is_directory=True)

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "linked-override"},
        {"MYCELIUM_STATE_DIR": "redirect/state"},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert list(victim.iterdir()) == []


def test_exact_bundled_hook_command_survives_relocated_cache_path(tmp_path):
    repo = _repo(tmp_path)
    relocated_root = tmp_path / "cache with spaces" / "0.6.0+codex.test"
    relocated_root.parent.mkdir()
    relocated_root.symlink_to(PLUGIN_ROOT, target_is_directory=True)
    config = json.loads((PLUGIN_HOOKS_DIR / "hooks.json").read_text())
    command = config["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    env = os.environ.copy()
    env["PLUGIN_ROOT"] = str(relocated_root)

    result = subprocess.run(
        command,
        cwd=repo,
        input=json.dumps(
            {"cwd": str(repo), "source": "startup", "turn_id": "turn-relocated"}
        ),
        text=True,
        capture_output=True,
        env=env,
        shell=True,
        executable="/bin/bash",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"] == (
        "SessionStart"
    )
    pointer = repo / ".mycelium" / "plugin-root"
    assert pointer.read_text().strip() == str(relocated_root)


def test_bundled_codex_hook_command_noops_without_plugin_root(tmp_path):
    repo = _repo(tmp_path)
    config = json.loads((PLUGIN_HOOKS_DIR / "hooks.json").read_text())
    command = config["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    env = os.environ.copy()
    env.pop("PLUGIN_ROOT", None)

    result = subprocess.run(
        command,
        cwd=repo,
        input=json.dumps(
            {"cwd": str(repo), "source": "startup", "turn_id": "turn-noncodex"}
        ),
        text=True,
        capture_output=True,
        env=env,
        shell=True,
        executable="/bin/bash",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (repo / ".mycelium").exists()


def test_codex_session_start_uses_nested_context(tmp_path):
    repo = _repo(tmp_path, living=False)
    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "turn-1"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "SessionStart"
    assert "no .living/ directory" in hook_output["additionalContext"]
    assert (repo / ".mycelium" / ".gitignore").is_file()


def test_codex_post_tool_use_uses_nested_context(tmp_path):
    repo = _repo(tmp_path)
    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python analysis.py"},
            "turn_id": "turn-2",
        },
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PostToolUse"
    assert "MYCELIUM POST-ACTION PROTOCOL" in hook_output["additionalContext"]
    assert (repo / ".mycelium" / "mycelium-reminded.tmp").is_file()


def test_codex_post_action_rejects_unproven_shell_text_matches(tmp_path):
    repo = _repo(tmp_path)
    reminder = repo / ".mycelium" / "mycelium-reminded.tmp"

    for index, command in enumerate(
        (
            "if false; then\npython analysis.py\nfi",
            "echo python analysis.py",
            "uv run echo python analysis.py",
        )
    ):
        result = _run_hook(
            "mycelium-post-action.sh",
            repo,
            {
                "cwd": str(repo),
                "tool_name": "Bash",
                "tool_input": {"command": command},
                "tool_response": {"exit_code": 0},
                "turn_id": f"turn-unproven-{index}",
            },
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert not reminder.exists()


def test_codex_post_tool_use_ignores_mycelium_structure_validator(tmp_path):
    repo = _repo(tmp_path)
    command = (
        "python3 \"$(sed -n '1p' .mycelium/plugin-root)/skills/core/"
        "scripts/validate_structure.py\" --target-dir ."
    )

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "turn_id": "turn-structure-validator",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (repo / ".mycelium" / "mycelium-reminded.tmp").exists()


def test_codex_post_tool_use_reads_only_log_path_marker_line(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    log_path = repo / ".living" / "log" / "session.md"
    log_path.parent.mkdir()
    log_path.write_text("# Session\n")
    owner_timestamp = "1785528000"
    (state / "active-session-log.tmp").write_text(
        f"{log_path}\n{owner_timestamp}\n"
    )

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python analysis.py"},
            "turn_id": "turn-log-marker",
        },
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert str(log_path) in context
    assert owner_timestamp not in context


def test_codex_data_tracker_preserves_unresolved_wrapper_execution(tmp_path):
    repo = _repo(tmp_path)
    (repo / "run.py").write_text(
        "from analysis import main\n\nif __name__ == '__main__':\n    main()\n"
    )

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python run.py --help"},
            "turn_id": "turn-lineage-wrapper",
        },
    )

    assert result.returncode == 0, result.stderr
    events_path = repo / ".mycelium" / "mycelium-data-events.tmp"
    event = json.loads(events_path.read_text().strip())
    assert event["script"] == str(repo / "run.py")
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []
    assert event["lineage_warnings"]


def test_codex_data_tracker_skips_analysis_in_failed_and_chain(tmp_path):
    repo = _repo(tmp_path)
    analysis_dir = repo / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "run.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {
                "command": "false && cd analysis && python run.py"
            },
            "tool_response": {"exit_code": 1, "output": ""},
            "turn_id": "turn-skipped-lineage",
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / ".mycelium" / "mycelium-data-events.tmp").exists()


def test_codex_data_tracker_records_proven_successful_and_chain(tmp_path):
    repo = _repo(tmp_path)
    analysis_dir = repo / "analysis"
    analysis_dir.mkdir()
    (analysis_dir / "run.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {
                "command": "test -d analysis && cd analysis && python run.py"
            },
            "tool_response": {"exit_code": 0, "output": ""},
            "turn_id": "turn-proven-lineage",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(analysis_dir / "run.py")
    assert event["bash_exit"] == 0


def test_codex_apply_patch_activity_tracks_each_file(tmp_path):
    repo = _repo(tmp_path)
    patch = """*** Begin Patch
*** Update File: src/alpha.py
@@
-old
+new
*** Add File: src/beta.py
+content
*** Update File: .living/learnings.md
@@
+ignored
*** End Patch"""
    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "turn_id": "turn-3",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    activity = (repo / ".mycelium" / "mycelium-session-activity.tmp").read_text()
    assert "src/alpha.py" in activity
    assert "src/beta.py" in activity
    assert ".living/learnings.md" not in activity


def test_codex_apply_patch_activity_resolves_paths_from_nested_cwd(tmp_path):
    repo = _repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    patch = """*** Begin Patch
*** Add File: a.py
+content
*** End Patch"""

    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(nested),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "turn_id": "turn-nested-patch",
        },
    )

    assert result.returncode == 0, result.stderr
    activity = repo / ".mycelium" / "mycelium-session-activity.tmp"
    assert activity.read_text().splitlines() == ["nested/a.py"]


def test_codex_apply_patch_activity_ignores_failed_tool_result(tmp_path):
    repo = _repo(tmp_path)
    patch = """*** Begin Patch
*** Add File: should-not-count.py
+content
*** End Patch"""

    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": {"exit_code": 1, "output": "patch failed"},
            "turn_id": "turn-failed-patch",
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / ".mycelium" / "mycelium-session-activity.tmp").exists()
    assert not (repo / ".mycelium" / "mycelium-reminded.tmp").exists()


def test_codex_apply_patch_activity_ignores_failed_string_response(tmp_path):
    repo = _repo(tmp_path)
    patch = """*** Begin Patch
*** Add File: should-not-count.py
+content
*** End Patch"""

    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": "Error: invalid patch context",
            "turn_id": "turn-failed-string-patch",
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / ".mycelium" / "mycelium-session-activity.tmp").exists()


def test_codex_apply_patch_activity_canonicalizes_symlinked_cwd(tmp_path):
    repo = _repo(tmp_path)
    nested = repo / "nested"
    nested.mkdir()
    alias = tmp_path / "repo-alias"
    alias.symlink_to(repo, target_is_directory=True)
    patch = """*** Begin Patch
*** Add File: a.py
+content
*** End Patch"""

    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(alias / "nested"),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-symlinked-cwd",
        },
    )

    assert result.returncode == 0, result.stderr
    activity = repo / ".mycelium" / "mycelium-session-activity.tmp"
    assert activity.read_text().splitlines() == ["nested/a.py"]


def test_hooks_refuse_symlinked_mycelium_state_directory(tmp_path):
    repo = _repo(tmp_path)
    victim = tmp_path / "outside-state"
    victim.mkdir()
    (repo / ".mycelium").symlink_to(victim, target_is_directory=True)

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "unsafe-state"},
    )

    assert result.returncode == 0, result.stderr
    assert list(victim.iterdir()) == []


def test_hooks_refuse_symlinked_living_directory(tmp_path):
    repo = _repo(tmp_path, living=False)
    victim = tmp_path / "outside-living"
    victim.mkdir()
    (repo / ".living").symlink_to(victim, target_is_directory=True)

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "unsafe-living"},
    )

    assert result.returncode == 0, result.stderr
    assert list(victim.iterdir()) == []


def test_bash_only_repository_change_blocks_without_living_update(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "bash-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    log_path = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    (repo / "bash-only.txt").write_text("created by a Bash command\n")

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "bash-stop"},
    )

    assert json.loads(stop.stdout)["decision"] == "block"
    assert (state / "active-session-log.tmp").is_file()
    assert "ended:\n" in log_path.read_text()


def test_blocked_stop_preserves_transaction_and_final_log_includes_continuation(
    tmp_path,
):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "block-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    active_marker = state / "active-session-log.tmp"
    baseline = state / "session-file-baseline.json"
    log_path = Path(active_marker.read_text().splitlines()[0])

    (repo / "first.py").write_text("print('first')\n")
    activity = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: first.py\n+first\n*** End Patch"
            },
            "tool_response": {"exit_code": 0},
            "turn_id": "block-activity",
        },
    )
    assert activity.returncode == 0, activity.stderr

    first_stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "block-one"},
    )
    assert json.loads(first_stop.stdout)["decision"] == "block"
    assert active_marker.is_file()
    assert baseline.is_file()
    assert "ended:\n" in log_path.read_text()
    assert "Session ended" not in log_path.read_text()

    (repo / "second.py").write_text("print('second')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# learnings.md\n\nContinuation recorded.\n"
    )
    accepted = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": True, "turn_id": "block-two"},
    )

    assert accepted.returncode == 0, accepted.stderr
    finalized = log_path.read_text()
    assert finalized.count("Session ended") == 1
    assert "- `first.py`" in finalized
    assert "- `second.py`" in finalized
    assert not active_marker.exists()


def test_same_second_living_content_update_is_accepted(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "same-second-start"},
    )
    assert start.returncode == 0, start.stderr
    (repo / "work.py").write_text("print('work')\n")
    activity = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: work.py\n+work\n*** End Patch"
            },
            "tool_response": {"exit_code": 0},
            "turn_id": "same-second-activity",
        },
    )
    assert activity.returncode == 0, activity.stderr
    reminder = int(
        (repo / ".mycelium" / "mycelium-reminded.tmp").read_text().strip()
    )
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text("# learnings.md\n\nUpdated in the same second.\n")
    os.utime(learnings, (reminder, reminder))

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "same-stop"},
    )

    assert stop.returncode == 0, stop.stderr
    assert '"decision": "block"' not in stop.stdout


def test_existing_finding_content_update_is_accepted(tmp_path):
    repo = _repo(tmp_path)
    finding = repo / ".living" / "findings" / "topic.md"
    finding.parent.mkdir()
    finding.write_text("# Existing finding\n\nBefore.\n")
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "finding-start"},
    )
    assert start.returncode == 0, start.stderr
    (repo / "work.py").write_text("print('work')\n")
    activity = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: work.py\n+work\n*** End Patch"
            },
            "tool_response": {"exit_code": 0},
            "turn_id": "finding-activity",
        },
    )
    assert activity.returncode == 0, activity.stderr
    old_directory_times = finding.parent.stat().st_atime_ns, finding.parent.stat().st_mtime_ns
    finding.write_text("# Existing finding\n\nAfter.\n")
    os.utime(finding.parent, ns=old_directory_times)

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "finding-stop"},
    )

    assert stop.returncode == 0, stop.stderr
    assert '"decision": "block"' not in stop.stdout


def test_concurrent_stop_finalizes_session_exactly_once(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "race-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    log_path = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    session_id = log_path.name[:14]
    (repo / "race.py").write_text("print('race')\n")
    activity = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {
                "command": "*** Begin Patch\n*** Add File: race.py\n+race\n*** End Patch"
            },
            "tool_response": {"exit_code": 0},
            "turn_id": "race-activity",
        },
    )
    assert activity.returncode == 0, activity.stderr
    (repo / ".living" / "decisions.md").write_text(
        "# decisions.md\n\nRace lifecycle recorded.\n"
    )
    payloads = [
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": f"race-{i}"}
        for i in range(8)
    ]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda payload: _run_hook("mycelium-stop-check.sh", repo, payload),
                payloads,
            )
        )

    assert all(result.returncode == 0 for result in results)
    assert log_path.read_text().count("Session ended") == 1
    registry = repo / ".living" / "log" / "LOG_REGISTRY.md"
    assert registry.read_text().count(f"| {session_id} |") == 1
    assert not (state / "active-session-log.tmp").exists()


def test_stop_lock_recovers_empty_stale_lock_directory(tmp_path):
    state = tmp_path / ".mycelium"
    lock = state / "mycelium-stop.lock"
    lock.mkdir(parents=True)
    stale = time.time() - 600
    os.utime(lock, (stale, stale))
    hook_lib = HOOKS_DIR / "mycelium-hook-lib.sh"
    command = (
        f'source "{hook_lib}"\n'
        f'mycelium_acquire_stop_lock "{state}"\n'
        'printf "acquired\\n"\n'
        "mycelium_release_stop_lock\n"
    )

    result = subprocess.run(
        ["bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=3,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "acquired\n"
    assert not lock.exists()


def test_ci_runs_integration_stress_suite():
    workflow = (PLUGIN_ROOT / ".github" / "workflows" / "validate-skill.yml").read_text()
    assert "bash skills/core/tests/test_integration_stress.sh" in workflow


def test_codex_stop_legacy_block_shape_remains_supported(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    work_started = int(time.time()) - 3600
    for path in (repo / ".living").iterdir():
        os.utime(path, (work_started - 3600, work_started - 3600))
    (state / "mycelium-reminded.tmp").write_text(str(work_started))
    (state / "mycelium-session-activity.tmp").write_text("src/alpha.py\n")
    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "turn-4"},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "STOP BLOCKED" in payload["reason"]


def test_codex_stop_counts_unique_tracked_untracked_and_activity_paths(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    (state / ".gitignore").write_text("*\n!.gitignore\n")
    log_path = repo / ".living" / "log" / "2026-07-31-001-repo.md"
    log_path.parent.mkdir()
    log_path.write_text(
        "---\n"
        "session_id: 2026-07-31-001\n"
        "project: repo\n"
        "branch: main\n"
        "started:\n"
        "ended:\n"
        "duration_minutes:\n"
        "files_changed:\n"
        "---\n"
    )
    (repo / "tracked.txt").write_text("before\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    commit_env = os.environ.copy()
    commit_env["GIT_AUTHOR_DATE"] = "2000-01-01T00:00:00+0000"
    commit_env["GIT_COMMITTER_DATE"] = "2000-01-01T00:00:00+0000"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Mycelium Test",
            "-c",
            "user.email=mycelium@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        check=True,
        env=commit_env,
    )

    session_start = int(time.time()) - 60
    (state / "active-session-log.tmp").write_text(
        f"{log_path}\n{session_start}\n"
    )
    (state / "session-start-ts.tmp").write_text(str(session_start))
    (state / "mycelium-reminded.tmp").write_text(str(session_start))
    (repo / "tracked.txt").write_text("after\n")
    (repo / "alpha.txt").write_text("alpha\n")
    (repo / "beta.txt").write_text("beta\n")
    # beta overlaps with git's untracked signal; gamma exists only in the
    # activity tracker. The final total must be a unique union of four paths.
    (state / "mycelium-session-activity.tmp").write_text(
        f"{repo / 'beta.txt'}\ngamma.txt\n"
    )

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "turn-count"},
    )

    assert result.returncode == 0, result.stderr
    finalized = log_path.read_text()
    assert "files_changed: 4" in finalized
    assert finalized.count("- `beta.txt`") == 1
    assert "- `alpha.txt`" in finalized
    assert "- `gamma.txt`" in finalized
    assert "- `tracked.txt`" in finalized


def test_data_lineage_state_survives_blocked_stop_until_acceptance(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    (state / ".gitignore").write_text("*\n!.gitignore\n")
    session_id = "2026-07-10-007"
    session_marker = state / "data-lineage-session-id.tmp"
    events_file = state / "mycelium-data-events.tmp"
    session_marker.write_text(f"{session_id}\n")
    first_event = (
        json.dumps(
            {
                "ts": "2026-07-10T12:00:00Z",
                "script": "analysis/first.py",
                "inputs": [],
                "outputs": [],
            }
        )
        + "\n"
    )
    events_file.write_text(first_event)
    work_started = int(time.time()) - 60
    (state / "mycelium-reminded.tmp").write_text(str(work_started))
    (state / "mycelium-session-activity.tmp").write_text("analysis/first.py\n")
    for path in (repo / ".living").iterdir():
        os.utime(path, (work_started - 60, work_started - 60))
    assert not (state / "active-session-log.tmp").exists()

    first_stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": "host-uuid",
            "stop_hook_active": False,
            "turn_id": "turn-5",
        },
    )
    assert json.loads(first_stop.stdout)["decision"] == "block"
    output = repo / ".living" / "log" / "data-lineage" / f"{session_id}.json"
    assert output.is_file()
    assert json.loads(output.read_text())["session_id"] == session_id
    assert session_marker.read_text().strip() == session_id
    assert events_file.read_text() == first_event

    second_event = (
        json.dumps(
            {
                "ts": "2026-07-10T12:01:00Z",
                "script": "analysis/second.py",
                "inputs": [],
                "outputs": [],
            }
        )
        + "\n"
    )
    with events_file.open("a") as handle:
        handle.write(second_event)
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text("# learnings\n\n- lifecycle updated\n")
    accepted_stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": "host-uuid",
            "stop_hook_active": True,
            "turn_id": "turn-7",
        },
    )
    assert accepted_stop.returncode == 0, accepted_stop.stderr
    assert '"decision": "block"' not in accepted_stop.stdout
    manifest = json.loads(output.read_text())
    assert manifest["session_id"] == session_id
    assert [action["script"] for action in manifest["actions"]] == [
        "analysis/first.py",
        "analysis/second.py",
    ]
    assert not session_marker.exists()
    assert not events_file.exists()
    archived_events = state / "mycelium-data-events-prev" / f"{session_id}.tmp"
    assert archived_events.read_text() == first_event + second_event


def test_lineage_only_session_preserves_log_and_reserves_session_id(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "lineage-start-1"},
    )
    assert start.returncode == 0, start.stderr

    state = repo / ".mycelium"
    first_session_id = (state / "data-lineage-session-id.tmp").read_text().strip()
    first_log = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    event = {
        "ts": "2026-08-01T12:00:00Z",
        "script": None,
        "script_source": "import pandas as pd; pd.read_csv('input.csv')",
        "inputs": [{"path": "input.csv", "_missing": True}],
        "outputs": [],
    }
    (state / "mycelium-data-events.tmp").write_text(json.dumps(event) + "\n")

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": "host-lineage-only",
            "stop_hook_active": False,
            "turn_id": "lineage-stop-1",
        },
    )

    assert stop.returncode == 0, stop.stderr
    assert first_log.is_file()
    manifest = repo / ".living" / "log" / "data-lineage" / f"{first_session_id}.json"
    assert manifest.is_file()
    archive = (
        state / "mycelium-data-events-prev" / f"{first_session_id}.tmp"
    )
    assert archive.is_file()

    second_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "lineage-start-2"},
    )
    assert second_start.returncode == 0, second_start.stderr
    second_session_id = (state / "data-lineage-session-id.tmp").read_text().strip()
    assert second_session_id != first_session_id
    assert int(second_session_id.rsplit("-", 1)[1]) == int(
        first_session_id.rsplit("-", 1)[1]
    ) + 1
