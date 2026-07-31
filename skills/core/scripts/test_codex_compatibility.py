"""Cross-platform packaging and Codex hook compatibility tests."""

import json
import os
import subprocess
import sys
import time
import types
from pathlib import Path

import generate_index
import init_repo
import yaml


HOOKS_DIR = Path(__file__).resolve().parent.parent / "hooks"
PLUGIN_ROOT = Path(__file__).resolve().parents[3]
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


def _run_hook(name: str, repo: Path, payload: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "codex"
    return subprocess.run(
        [str(HOOKS_DIR / name)],
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


def test_readme_documents_codex_install_update_and_migration():
    readme = (PLUGIN_ROOT / "README.md").read_text()
    assert "codex plugin marketplace add arjunrajlaboratory/mycelium" in readme
    assert "codex plugin add mycelium@mycelium" in readme
    assert "codex plugin marketplace upgrade mycelium" in readme
    assert "codex plugin list --json" in readme
    assert "Use `$mycelium:core` to migrate" in readme
    assert "Migration is idempotent" in readme


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


def test_init_writes_codex_hooks_and_agent_guidance(tmp_path):
    repo = _repo(tmp_path, living=False)
    init_repo.create_directory_structure(repo)
    init_repo.create_todo_list(repo)
    init_repo.create_agent_guidance(repo)
    init_repo.install_codex_hooks(repo)

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
    config = json.loads((repo / ".codex" / "hooks.json").read_text())
    commands = [
        handler["command"]
        for groups in config["hooks"].values()
        for group in groups
        for handler in group["hooks"]
    ]
    assert len(commands) == 6
    assert all(command.startswith("MYCELIUM_HOOK_HOST=codex ") for command in commands)
    assert not any("mycelium-read-tracker.sh" in command for command in commands)
    assert any(
        group["matcher"] == "Bash"
        for group in config["hooks"]["PostToolUse"]
    )
    assert not any(
        group["matcher"] == "exec_command"
        for group in config["hooks"]["PostToolUse"]
    )
    assert any(
        group["matcher"] == "apply_patch"
        for group in config["hooks"]["PostToolUse"]
    )
    stop_handlers = config["hooks"]["Stop"][0]["hooks"]
    assert "mycelium-data-lineage-stop.sh" in stop_handlers[0]["command"]


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


def test_existing_codex_gitignore_is_extended(tmp_path):
    repo = _repo(tmp_path, living=False)
    codex_dir = repo / ".codex"
    codex_dir.mkdir()
    (codex_dir / ".gitignore").write_text("config.toml\n")

    init_repo.install_codex_hooks(repo)

    ignored = (codex_dir / ".gitignore").read_text().splitlines()
    assert ignored == ["config.toml", "hooks.json"]


def test_existing_exec_command_hooks_are_moved_to_bash(tmp_path):
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
    post_tool = config["hooks"]["PostToolUse"]
    assert not any(group["matcher"] == "exec_command" for group in post_tool)
    bash_handlers = next(group for group in post_tool if group["matcher"] == "Bash")[
        "hooks"
    ]
    basenames = {
        init_repo._hook_basename(handler["command"]) for handler in bash_handlers
    }
    assert {"mycelium-post-action.sh", "mycelium-data-tracker.sh"} <= basenames
    assert config["hooks"]["PreToolUse"] == [
        {"matcher": "custom", "hooks": []}
    ]


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


def test_data_lineage_keeps_canonical_id_after_stop_state_cleanup(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    (state / ".gitignore").write_text("*\n!.gitignore\n")
    (state / "data-lineage-session-id.tmp").write_text("2026-07-10-007\n")
    (state / "mycelium-data-events.tmp").write_text(
        json.dumps(
            {
                "ts": "2026-07-10T12:00:00Z",
                "script": "analysis/demo.py",
                "inputs": [],
                "outputs": [],
            }
        )
        + "\n"
    )
    assert not (state / "active-session-log.tmp").exists()

    result = _run_hook(
        "mycelium-data-lineage-stop.sh",
        repo,
        {"cwd": str(repo), "session_id": "host-uuid", "turn_id": "turn-5"},
    )

    assert result.returncode == 0, result.stderr
    output = repo / ".living" / "log" / "data-lineage" / "2026-07-10-007.json"
    assert output.is_file()
    assert json.loads(output.read_text())["session_id"] == "2026-07-10-007"
    assert not (state / "data-lineage-session-id.tmp").exists()
