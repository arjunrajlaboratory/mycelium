"""Cross-platform packaging and Codex hook compatibility tests."""

import json
import os
import re
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


def _run_claude_hook(
    name: str,
    repo: Path,
    payload: dict,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MYCELIUM_HOOK_HOST"] = "claude"
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


def _host_state(repo: Path, session_id: str, host: str = "codex") -> Path:
    return repo / ".mycelium" / "run" / host / session_id


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
        "develop",
        "ideas",
        "ingest",
        "lifecycle-audit",
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


def test_development_skills_encode_cross_host_regression_workflow():
    develop = " ".join(
        (PLUGIN_ROOT / "skills" / "develop" / "SKILL.md").read_text().split()
    )
    patterns_path = (
        PLUGIN_ROOT
        / "skills"
        / "develop"
        / "references"
        / "regression-patterns.md"
    )
    patterns = " ".join(patterns_path.read_text().split())
    audit = " ".join(
        (PLUGIN_ROOT / "skills" / "lifecycle-audit" / "SKILL.md")
        .read_text()
        .split()
    )
    protocol_path = (
        PLUGIN_ROOT
        / "skills"
        / "lifecycle-audit"
        / "references"
        / "audit-protocol.md"
    )
    protocol = " ".join(protocol_path.read_text().split())

    for required in (
        "base...head",
        "Reproduce with a red test",
        "Generalize the defect pattern",
        "Preflight every source and destination",
        "$mycelium:lifecycle-audit",
    ):
        assert required in develop
    for required in (
        "Source checkout is not the installed artifact",
        "Host process observes a changing artifact",
        "Validation occurs after mutation begins",
        "Cross-host hook discovery invokes the wrong adapter",
        "Host payload omits result metadata",
        "Lock recovery waits longer than acquisition",
        "Repository state is mistaken for invocation identity",
        "manual hook test",
        "Option values are mistaken for positional inputs",
        "Terminal-only mode is treated as execution",
        "A fallback is appended to partial stdout",
        "Compatibility cleanup deletes user state",
        "Unrelated literals are fabricated as data lineage",
        "Resolved I/O triggers repository traversal",
        "Handoff headings are mistaken for complete handoff sections",
        "exact normalized body that finalization will publish",
    ):
        assert required in patterns
    for required in (
        "Do not invoke any Mycelium hook script",
        "Artifact identity",
        "Host dispatch",
        "Agent compliance",
        "$mycelium:develop",
    ):
        assert required in audit
    for required in (
        "env -C analysis/example python3 run.py --help",
        "env -S 'echo prefix' python3 run.py --help",
        "MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp",
        "never use Bash to create it",
        "rm -- MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp",
        "Do not invoke or read any skill",
        "Do not inspect plugin identity",
        "Do not change the source or installed artifact while the host task is running",
        "Keep ordinary file-reading and editing tools available",
        "first Stop",
        "Nested-session isolation",
        "## What was worked on",
        "## Key decisions made",
        "## Blockers & surprises",
        "## Current state",
        "## Next steps",
        "observable host stream and filesystem state outrank",
        "empty `tool_response`",
        "Scientific isolation",
    ):
        assert required in protocol


def test_review_skill_respects_host_subagent_capacity_and_retries():
    review = " ".join(
        (PLUGIN_ROOT / "skills" / "review" / "SKILL.md").read_text().split()
    )

    for required in (
        "host capacity",
        "waves",
        "agent thread limit reached",
        "retry",
        "in-line",
    ):
        assert required in review
    assert "single message with six concurrent" not in review


def test_review_skill_enforces_comparability_deduplication_and_exact_tallies():
    review = " ".join((PLUGIN_ROOT / "skills" / "review" / "SKILL.md").read_text().split())
    synthesis = " ".join(
        (PLUGIN_ROOT / "skills" / "core" / "references" / "review" / "synthesis.md")
        .read_text()
        .split()
    )
    pipeline = " ".join(
        (
            PLUGIN_ROOT
            / "skills"
            / "core"
            / "references"
            / "review"
            / "data-pipeline-leakage.md"
        )
        .read_text()
        .split()
    )
    biology = " ".join(
        (
            PLUGIN_ROOT
            / "skills"
            / "core"
            / "references"
            / "review"
            / "bioinformatics.md"
        )
        .read_text()
        .split()
    )

    assert "One root cause gets one finding ID" in review
    assert "validate_review_report.py" in review
    assert "## Finding tally" in review
    assert "Finding IDs count distinct remediations" in synthesis
    assert "Cross-input comparability" in pipeline
    assert "compare `var_names`" in biology
    assert (PLUGIN_ROOT / "skills" / "core" / "scripts" / "validate_review_report.py").is_file()


def test_codex_plugin_manifest_points_to_shared_skills():
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())
    claude_manifest = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text()
    )
    assert manifest["name"] == "mycelium"
    assert manifest["skills"] == "./skills/"
    base_version, separator, cachebuster = manifest["version"].partition("+")
    assert base_version == claude_manifest["version"]
    if separator:
        assert re.fullmatch(
            r"codex\.[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*", cachebuster
        )


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


def test_readme_documents_claude_update_and_restart():
    readme = (PLUGIN_ROOT / "README.md").read_text()
    normalized = " ".join(readme.split())

    assert "claude plugin update mycelium@mycelium" in readme
    assert "restart Claude Code" in normalized


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


def test_fresh_init_preflights_all_managed_outputs_before_mutation(tmp_path):
    repo = _repo(tmp_path, living=False)
    victim = tmp_path / "outside-agents.md"
    victim.write_text("external guidance\n")
    (repo / "AGENTS.md").symlink_to(victim)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(init_repo.__file__).resolve()),
            "--target-dir",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert victim.read_text() == "external guidance\n"
    assert sorted(path.name for path in repo.iterdir()) == [".git", "AGENTS.md"]


def test_fresh_init_preflights_malformed_hook_config_before_mutation(tmp_path):
    repo = _repo(tmp_path, living=False)
    claude_dir = repo / ".claude"
    claude_dir.mkdir()
    settings = claude_dir / "settings.local.json"
    settings.write_text("{invalid json\n")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(init_repo.__file__).resolve()),
            "--target-dir",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert settings.read_text() == "{invalid json\n"
    assert sorted(path.name for path in repo.iterdir()) == [".claude", ".git"]


@pytest.mark.parametrize(
    "relative_path",
    [".claude/settings.local.json", ".codex/hooks.json"],
)
def test_fresh_init_preflights_malformed_hook_schema_before_mutation(
    tmp_path, relative_path
):
    repo = _repo(tmp_path, living=False)
    config_path = repo / relative_path
    config_path.parent.mkdir()
    malformed = {"hooks": {"PostToolUse": ["not-a-hook-group"]}}
    config_path.write_text(json.dumps(malformed) + "\n")

    result = subprocess.run(
        [
            sys.executable,
            str(Path(init_repo.__file__).resolve()),
            "--target-dir",
            str(repo),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "hook group must be an object" in result.stderr
    assert config_path.read_text() == json.dumps(malformed) + "\n"
    assert sorted(path.name for path in repo.iterdir()) == [
        config_path.parent.name,
        ".git",
    ]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ([], "hook configuration must be an object"),
        ({"hooks": []}, "hooks must be an object"),
        ({"hooks": {"Stop": {}}}, "hook event 'Stop' must be a list"),
        (
            {"hooks": {"Stop": [{"hooks": {}}]}},
            "hook handlers must be a list",
        ),
        (
            {"hooks": {"Stop": [{"hooks": ["not-a-handler"]}]}},
            "hook handler must be an object",
        ),
        (
            {"hooks": {"Stop": [{"hooks": [{"command": 7}]}]}},
            "hook command must be a string",
        ),
    ],
)
def test_hook_config_validator_rejects_every_traversed_malformed_type(
    config, message
):
    with pytest.raises(ValueError, match=message):
        init_repo.validate_hook_config(config, "test config")


def test_hook_config_validator_preserves_unknown_and_commandless_handlers():
    config = {
        "permissions": {"allow": ["Read"]},
        "hooks": {
            "Notification": [
                {
                    "matcher": "permission_prompt",
                    "hooks": [
                        {"type": "prompt", "prompt": "Summarize the request."}
                    ],
                    "host_extension": True,
                }
            ]
        },
    }

    assert init_repo.validate_hook_config(config, "test config") is config


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


def test_empty_session_start_counts_are_single_numeric_values(tmp_path):
    repo = _repo(tmp_path)

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "empty-counts"},
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    assert "MYCELIUM SUMMARY: 0 learnings, 0 decisions, 0 conventions" in context
    assert "0\n0" not in context


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


def test_plugin_dispatcher_declines_hooks_cross_loaded_by_claude(tmp_path):
    repo = _repo(tmp_path)

    result = _run_plugin_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "claude-host"},
        extra_env={"CLAUDE_PROJECT_DIR": str(repo)},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (repo / ".mycelium").exists()
    assert not list((repo / ".living" / "log").glob("20*.md"))


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


def test_hook_does_not_import_legacy_session_through_symlinked_claude_dir(tmp_path):
    repo = _repo(tmp_path)
    outside_claude = tmp_path / "outside-claude"
    outside_claude.mkdir()
    legacy = outside_claude / "last-session.md"
    original = "host-private legacy session\n"
    legacy.write_text(original)
    (repo / ".claude").symlink_to(outside_claude, target_is_directory=True)

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "linked-legacy"},
    )

    assert result.returncode == 0, result.stderr
    assert legacy.read_text() == original
    assert not (repo / ".mycelium" / "last-session.md").exists()
    assert (repo / ".mycelium" / "active-session-log.tmp").exists()


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


def test_claude_session_start_uses_nested_context(tmp_path):
    repo = _repo(tmp_path, living=False)
    result = _run_claude_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "claude-session"},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "SessionStart"
    assert "no .living/ directory" in hook_output["additionalContext"]
    assert "additionalContext" not in payload


def test_claude_post_tool_use_uses_nested_context(tmp_path):
    repo = _repo(tmp_path)
    session_id = "claude-session"
    session_start = _run_claude_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    )
    assert session_start.returncode == 0, session_start.stderr

    result = _run_claude_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python analysis.py"},
            "tool_response": {"exit_code": 0},
            "session_id": session_id,
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    hook_output = payload["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PostToolUse"
    assert "MYCELIUM POST-ACTION PROTOCOL" in hook_output["additionalContext"]
    assert "additionalContext" not in payload


def test_codex_post_action_rejects_outside_active_log_marker(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    outside_log = tmp_path / "outside-session.md"
    outside_log.write_text("do not direct writes here\n")
    (state / "active-session-log.tmp").write_text(
        f"{outside_log}\n{int(time.time())}\n"
    )

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python analysis.py"},
            "tool_response": {"exit_code": 0},
            "turn_id": "outside-active-log",
        },
    )

    assert result.returncode == 0, result.stderr
    assert str(outside_log) not in result.stdout
    assert "MYCELIUM POST-ACTION PROTOCOL" in result.stdout
    assert outside_log.read_text() == "do not direct writes here\n"


def test_session_start_replaces_outside_active_log_marker_safely(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    outside_log = tmp_path / "outside-health-session.md"
    outside_log.write_text("external session\n")
    (state / "active-session-log.tmp").write_text(
        f"{outside_log}\n{int(time.time())}\n"
    )

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "corrupt-health"},
    )

    assert result.returncode == 0, result.stderr
    assert str(outside_log) not in result.stdout
    assert "CORRUPT SESSION MARKER" in result.stdout
    marker_lines = (state / "active-session-log.tmp").read_text().splitlines()
    assert len(marker_lines) == 2
    assert Path(marker_lines[0]).parent == repo / ".living" / "log"
    assert outside_log.read_text() == "external session\n"


def test_session_start_recovers_malformed_runtime_timestamps(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    (state / "mycelium-reminded.tmp").write_text("not-a-timestamp\n")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    (knowledge / ".last-audit").write_text("also-not-a-timestamp\n")

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "bad-timestamps"},
        {"MYCELIUM_KNOWLEDGE_DIR": str(knowledge)},
    )

    assert result.returncode == 0, result.stderr
    assert (state / "active-session-log.tmp").is_file()
    assert not (state / "mycelium-reminded.tmp").exists()
    assert "KNOWLEDGE AUDIT DUE" in result.stdout


def test_concurrent_session_start_creates_one_primary_log_and_marker(tmp_path):
    """Concurrent primary/subagent starts must serialize log ownership."""
    repo = _repo(tmp_path)
    payloads = [
        {"cwd": str(repo), "source": "startup", "turn_id": f"start-race-{i}"}
        for i in range(12)
    ]

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda payload: _run_hook("mycelium-health.sh", repo, payload),
                payloads,
            )
        )

    assert all(result.returncode == 0 for result in results)
    logs = sorted(
        path
        for path in (repo / ".living" / "log").glob("*.md")
        if path.name != "LOG_REGISTRY.md"
    )
    assert len(logs) == 1
    marker_lines = (
        repo / ".mycelium" / "active-session-log.tmp"
    ).read_text().splitlines()
    assert len(marker_lines) == 2
    assert marker_lines[0] == str(logs[0])
    assert marker_lines[1].isdigit()


def test_same_root_session_recovers_an_abandoned_owned_transaction(tmp_path):
    """A restarted root may recover only its own inactive transaction."""
    repo = _repo(tmp_path)
    primary_id = "primary-session"

    primary_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": primary_id,
        },
    )
    assert primary_start.returncode == 0, primary_start.stderr

    state = _host_state(repo, primary_id)
    marker = state / "active-session-log.tmp"
    marker_lines = marker.read_text().splitlines()
    log_path = Path(marker_lines[0])
    assert marker_lines[2] == "owner-id-v1"

    events = state / "mycelium-data-events.tmp"
    events.write_text('{"script":"analysis/old.py"}\n')
    activity = state / "mycelium-session-activity.tmp"
    reminder = state / "mycelium-reminded.tmp"
    activity.write_text("analysis/old.py\n")
    reminder.write_text(f"{int(time.time())}\n")
    abandoned_ts = int(time.time()) - 7300
    marker.write_text(f"{log_path}\n{abandoned_ts}\nowner-id-v1\n")
    os.utime(activity, (abandoned_ts, abandoned_ts))
    os.utime(reminder, (abandoned_ts, abandoned_ts))

    replacement_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": primary_id,
        },
    )
    assert replacement_start.returncode == 0, replacement_start.stderr
    assert "INCOMPLETE SESSION LOG" in replacement_start.stdout
    replacement_marker = marker.read_text().splitlines()
    replacement_log = Path(replacement_marker[0])
    assert replacement_log != log_path
    assert log_path.is_file()
    assert (state / "active-session-owner-id.tmp").read_text().strip() == primary_id
    assert not events.exists()
    assert not (state / "mycelium-session-activity.tmp").exists()
    assert not (state / "mycelium-reminded.tmp").exists()
    archives = list(
        (repo / ".mycelium" / "mycelium-data-events-abandoned").glob("*.tmp")
    )
    assert len(archives) == 1
    assert archives[0].read_text() == '{"script":"analysis/old.py"}\n'

    replacement_stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": primary_id,
        },
    )
    assert replacement_stop.returncode == 0, replacement_stop.stderr
    assert not marker.exists()
    assert not replacement_log.exists()
    assert log_path.exists()
    assert not (state / "active-session-owner-id.tmp").exists()


def test_distinct_root_session_leaves_a_live_owned_transaction_untouched(tmp_path):
    """A concurrent root gets its own state and cannot seize a live transaction."""
    repo = _repo(tmp_path)
    primary_id = "primary-session"
    primary_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": primary_id},
    )
    assert primary_start.returncode == 0, primary_start.stderr

    state = _host_state(repo, primary_id)
    marker = state / "active-session-log.tmp"
    owner = state / "active-session-owner-id.tmp"
    events = state / "mycelium-data-events.tmp"
    activity = state / "mycelium-session-activity.tmp"
    reminder = state / "mycelium-reminded.tmp"
    events.write_text('{"script":"analysis/live.py"}\n')
    activity.write_text("analysis/live.py\n")
    stale_signal_ts = int(time.time()) - 3700
    reminder.write_text(f"{stale_signal_ts}\n")
    os.utime(reminder, (stale_signal_ts, stale_signal_ts))
    marker_lines = marker.read_text().splitlines()
    marker.write_text(
        f"{marker_lines[0]}\n{int(time.time()) - 7300}\nowner-id-v1\n"
    )
    before = {
        path: path.read_bytes()
        for path in (marker, owner, events, activity, reminder)
    }
    logs_before = sorted((repo / ".living" / "log").glob("*.md"))

    competing_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": "competing-session",
        },
    )

    assert competing_start.returncode == 0, competing_start.stderr
    assert "ACTIVE SESSION PRESERVED" not in competing_start.stdout
    assert {path: path.read_bytes() for path in before} == before
    assert len(sorted((repo / ".living" / "log").glob("*.md"))) == len(logs_before) + 1
    assert (
        _host_state(repo, "competing-session") / "active-session-log.tmp"
    ).is_file()
    assert not (
        repo / ".mycelium" / "mycelium-data-events-abandoned"
    ).exists()


def test_new_root_does_not_move_nonregular_abandoned_event_state(tmp_path):
    repo = _repo(tmp_path)
    primary_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "primary-session"},
    )
    assert primary_start.returncode == 0, primary_start.stderr
    state = _host_state(repo, "primary-session")
    marker = state / "active-session-log.tmp"
    marker_lines = marker.read_text().splitlines()
    marker.write_text(
        f"{marker_lines[0]}\n{int(time.time()) - 7300}\nowner-id-v1\n"
    )
    original_marker = marker.read_text()
    events = state / "mycelium-data-events.tmp"
    events.mkdir()
    (events / "user-owned.txt").write_text("keep\n")

    replacement_start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": "primary-session",
        },
    )

    assert replacement_start.returncode == 0, replacement_start.stderr
    assert events.is_dir()
    assert (events / "user-owned.txt").read_text() == "keep\n"
    assert marker.read_text() == original_marker
    assert (
        state / "active-session-owner-id.tmp"
    ).read_text().strip() == "primary-session"


@pytest.mark.parametrize(
    "hook_name,payload,unexpected_state",
    [
        (
            "mycelium-post-action.sh",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python analysis.py"},
                "tool_response": {"exit_code": 0},
            },
            "mycelium-reminded.tmp",
        ),
        (
            "mycelium-data-tracker.sh",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python analysis.py"},
                "tool_response": {"exit_code": 0},
            },
            "mycelium-data-events.tmp",
        ),
        (
            "mycelium-activity-tracker.sh",
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Add File: stale.py\n+x\n*** End Patch"
                },
                "tool_response": {"exit_code": 0},
            },
            "mycelium-session-activity.tmp",
        ),
    ],
)
def test_superseded_root_post_tool_use_cannot_mutate_new_owner_state(
    tmp_path, hook_name, payload, unexpected_state
):
    repo = _repo(tmp_path)
    (repo / "analysis.py").write_text("pd.read_csv('input.csv')\n")
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": "new-owner"},
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, "new-owner")
    payload.update({"cwd": str(repo), "session_id": "old-owner"})

    result = _run_hook(hook_name, repo, payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (state / unexpected_state).exists()


@pytest.mark.parametrize(
    "hook_name,payload,unexpected_state",
    [
        (
            "mycelium-post-action.sh",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python analysis.py"},
                "tool_response": {"exit_code": 0},
            },
            "mycelium-reminded.tmp",
        ),
        (
            "mycelium-data-tracker.sh",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "python analysis.py"},
                "tool_response": {"exit_code": 0},
            },
            "mycelium-data-events.tmp",
        ),
        (
            "mycelium-activity-tracker.sh",
            {
                "tool_name": "apply_patch",
                "tool_input": {
                    "command": "*** Begin Patch\n*** Add File: late.py\n+x\n*** End Patch"
                },
                "tool_response": {"exit_code": 0},
            },
            "mycelium-session-activity.tmp",
        ),
        (
            "mycelium-read-tracker.sh",
            {
                "tool_name": "Read",
                "tool_input": {"file_path": ""},
                "tool_response": {"exit_code": 0},
            },
            "mycelium-read-access.log",
        ),
    ],
)
@pytest.mark.parametrize("state_preexisting", [False, True])
def test_late_host_post_tool_use_cannot_mutate_without_an_active_transaction(
    tmp_path, hook_name, payload, unexpected_state, state_preexisting
):
    repo = _repo(tmp_path)
    (repo / "analysis.py").write_text("pd.read_csv('input.csv')\n")
    state = repo / ".mycelium"
    anchor = state / "last-session.md"
    if state_preexisting:
        state.mkdir()
        anchor.write_text("accepted handoff\n")
    if hook_name == "mycelium-read-tracker.sh":
        payload["tool_input"]["file_path"] = str(repo / ".living" / "INDEX.md")
    payload.update({"cwd": str(repo), "session_id": "completed-session"})

    result = _run_hook(hook_name, repo, payload)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    if state_preexisting:
        assert anchor.read_text() == "accepted handoff\n"
        assert sorted(path.name for path in state.iterdir()) == ["last-session.md"]
        assert not (state / unexpected_state).exists()
    else:
        assert not state.exists()


@pytest.mark.parametrize("branch_name", [None, "{yaml-like-branch}"])
def test_session_start_records_one_branch_scalar_for_unborn_head(
    tmp_path, branch_name
):
    """A failing Git query must not concatenate partial output and fallback."""
    repo = _repo(tmp_path)
    if branch_name is not None:
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "symbolic-ref",
                "HEAD",
                f"refs/heads/{branch_name}",
            ],
            check=True,
        )
    expected_branch = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    host_session_id = "unborn-session"
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": host_session_id,
        },
    )

    assert start.returncode == 0, start.stderr
    state = _host_state(repo, host_session_id)
    log_path = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    frontmatter = yaml.safe_load(log_path.read_text().split("---", 2)[1])
    assert frontmatter["branch"] == expected_branch
    assert f"Branch: `{expected_branch}`" in log_path.read_text()

    (repo / "work.txt").write_text("session work\n")
    learnings = repo / ".living" / "learnings.md"
    learnings.write_text(learnings.read_text() + "\nBranch scalar verified.\n")
    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": host_session_id,
        },
    )
    assert stop.returncode == 0, stop.stderr
    registry = (repo / ".living" / "log" / "LOG_REGISTRY.md").read_text()
    assert f"| {expected_branch} |" in registry


def test_missing_session_owner_token_fails_closed(tmp_path):
    """A new-format marker must not silently downgrade when its owner is lost."""
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": "primary-session",
        },
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, "primary-session")
    marker = state / "active-session-log.tmp"
    log_path = Path(marker.read_text().splitlines()[0])
    (state / "active-session-owner-id.tmp").unlink()

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": "primary-session",
        },
    )

    assert stop.returncode == 0, stop.stderr
    assert json.loads(stop.stdout)["decision"] == "block"
    assert marker.is_file()
    assert log_path.is_file()


def test_pre_discriminator_owner_token_remains_upgrade_compatible(tmp_path):
    """An already-active two-line marker still honors its existing owner token."""
    repo = _repo(tmp_path)
    primary_id = "primary-session"
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": primary_id,
        },
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, primary_id)
    marker = state / "active-session-log.tmp"
    marker_lines = marker.read_text().splitlines()
    marker.write_text("\n".join(marker_lines[:2]) + "\n")
    log_path = Path(marker_lines[0])

    child_stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": "child-session",
        },
    )

    assert child_stop.returncode == 0, child_stop.stderr
    assert child_stop.stdout == ""
    assert marker.is_file()
    assert log_path.is_file()
    assert (state / "active-session-owner-id.tmp").read_text().strip() == primary_id


@pytest.mark.parametrize(
    "owner_content",
    ["corrupt/owner\n", "\n", "primary-session\n\ntrailing\n"],
)
def test_corrupt_session_owner_token_fails_closed(tmp_path, owner_content):
    """Invalid new-format ownership must not fall back to shared timestamps."""
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": "primary-session",
        },
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, "primary-session")
    marker = state / "active-session-log.tmp"
    log_path = Path(marker.read_text().splitlines()[0])
    (state / "active-session-owner-id.tmp").write_text(owner_content)

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "session_id": "primary-session",
        },
    )

    assert stop.returncode == 0, stop.stderr
    assert json.loads(stop.stdout)["decision"] == "block"
    assert marker.is_file()
    assert log_path.is_file()


def test_identity_free_stop_cannot_reach_scoped_owned_session(tmp_path):
    """A legacy Stop cannot be authorized against a host-scoped transaction."""
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": "primary-session",
        },
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, "primary-session")
    marker = state / "active-session-log.tmp"
    log_path = Path(marker.read_text().splitlines()[0])

    stop = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False},
    )

    assert stop.returncode == 0, stop.stderr
    assert stop.stdout == ""
    assert marker.is_file()
    assert log_path.is_file()


def test_session_start_does_not_reuse_noncontiguous_session_number(tmp_path):
    repo = _repo(tmp_path)
    log_dir = repo / ".living" / "log"
    log_dir.mkdir()
    today = time.strftime("%Y-%m-%d")
    (log_dir / "LOG_REGISTRY.md").write_text("# Registry\n")
    (log_dir / f"{today}-001-repo.md").write_text("first\n")
    occupied = log_dir / f"{today}-003-repo.md"
    occupied.write_text("must survive\n")

    result = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "number-gap"},
    )

    assert result.returncode == 0, result.stderr
    marker_path = Path(
        (repo / ".mycelium" / "active-session-log.tmp").read_text().splitlines()[0]
    )
    assert marker_path.name == f"{today}-004-repo.md"
    assert occupied.read_text() == "must survive\n"


def test_codex_post_action_rejects_unproven_shell_text_matches(tmp_path):
    repo = _repo(tmp_path)
    reminder = repo / ".mycelium" / "mycelium-reminded.tmp"

    for index, command in enumerate(
        (
            "if false; then\npython analysis.py\nfi",
            "echo python analysis.py",
            "uv run echo python analysis.py",
            "true # && python analysis.py",
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


@pytest.mark.parametrize(
    "argument,quoted",
    [
        ("setup\nif True:\n    print(1)", True),
        ("pytest", True),
        ("pip install package", True),
        ("python -m pip", True),
        ("ruff", True),
        ("setup.py", True),
        ("skills/core/scripts/validate_structure.py", True),
        ("pytest", False),
        ("pip install package", False),
        ("python -m pip", False),
        ("ruff", False),
        ("setup.py", False),
        ("skills/core/scripts/validate_structure.py", False),
    ],
)
def test_codex_post_action_ignores_exclusion_text_in_script_arguments(
    tmp_path, argument, quoted
):
    repo = _repo(tmp_path)
    (repo / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )

    rendered_argument = f'"{argument}"' if quoted else argument
    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {
                "command": f"python analysis.py --expression {rendered_argument}"
            },
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-quoted-control-word",
        },
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MYCELIUM POST-ACTION PROTOCOL" in context
    assert (repo / ".mycelium" / "mycelium-reminded.tmp").is_file()


@pytest.mark.parametrize(
    "command",
    [
        'python -c "import pandas as pd; pd.read_csv(\'input.csv\')"',
        "python -m pytest",
        "python -m ruff",
        'python "setup.py"',
    ],
)
def test_codex_post_action_ignores_actual_tooling_execution(tmp_path, command):
    repo = _repo(tmp_path)
    (repo / "setup.py").write_text("from setuptools import setup\nsetup()\n")

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-tooling-execution",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (repo / ".mycelium" / "mycelium-reminded.tmp").exists()


@pytest.mark.parametrize(
    "prefix",
    [
        'python -c "print(1)"',
        "python -m pytest",
        "python -m ruff",
        'python "setup.py"',
    ],
)
def test_codex_post_action_retains_analysis_after_tooling_prefix(tmp_path, prefix):
    repo = _repo(tmp_path)
    (repo / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    (repo / "analysis.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": f"{prefix} && python analysis.py"},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-analysis-after-tooling",
        },
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MYCELIUM POST-ACTION PROTOCOL" in context
    assert (repo / ".mycelium" / "mycelium-reminded.tmp").is_file()


def test_codex_post_tool_use_ignores_mycelium_structure_validator(tmp_path):
    repo = _repo(tmp_path)
    plugin_root = tmp_path / "plugin"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name": "mycelium", "version": "9.9.9"}\n')
    state = repo / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_text(f"{plugin_root}\n")
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


@pytest.mark.parametrize(
    "utility",
    [
        "generate_index.py",
        "upsert_registry_row.py",
        "finalize_handoff.py",
        "finalize_session_log.py",
        "session_file_changes.py",
        "validate_review_report.py",
        # Helpers omitted from the registry before issue #69: they opened a
        # post-action cycle and appeared in scientific lineage manifests.
        "recall_lessons.py",
        "detect_recurrence.py",
        "upsert_table_row.py",
        "crystallize_findings.py",
        "extract_data_lineage.py",
        "knowledge_map/cli.py",
    ],
)
def test_codex_post_tool_use_ignores_mycelium_control_plane_utilities(
    tmp_path, utility
):
    repo = _repo(tmp_path)
    plugin_root = repo / "plugin"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name": "mycelium", "version": "9.9.9"}\n')
    utility_path = plugin_root / "skills" / "core" / "scripts" / utility
    utility_path.parent.mkdir(parents=True)
    utility_path.write_text("print('managed')\n")

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": f'python3 "{utility_path}" --help'},
            "tool_response": {"exit_code": 0},
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not (repo / ".mycelium" / "mycelium-reminded.tmp").exists()
    lineage = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": f'python3 "{utility_path}" --help'},
            "tool_response": {"exit_code": 0},
        },
    )
    assert lineage.returncode == 0, lineage.stderr
    assert not (repo / ".mycelium" / "mycelium-data-events.tmp").exists()


def test_conventional_layout_user_script_is_still_analysis(tmp_path):
    """Codex P2 on PR #70: a path shape alone must not suppress bookkeeping.

    A user project may contain `skills/core/scripts/<colliding-name>.py`
    without being a Mycelium plugin tree; only a verified plugin root (or the
    documented plugin-root accessor) silences lineage and post-action work.
    """
    repo = _repo(tmp_path)
    script = repo / "skills" / "core" / "scripts" / "crystallize_findings.py"
    script.parent.mkdir(parents=True)
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {
                "command": "python3 skills/core/scripts/crystallize_findings.py"
            },
            "tool_response": {"exit_code": 0},
        },
    )

    assert result.returncode == 0, result.stderr
    assert "MYCELIUM POST-ACTION PROTOCOL" in result.stdout
    assert (repo / ".mycelium" / "mycelium-reminded.tmp").is_file()


def test_user_script_named_generate_index_is_still_analysis(tmp_path):
    repo = _repo(tmp_path)
    script = repo / "analysis" / "generate_index.py"
    script.parent.mkdir()
    script.write_text("pd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python3 analysis/generate_index.py"},
            "tool_response": {"exit_code": 0},
        },
    )

    assert result.returncode == 0, result.stderr
    assert "MYCELIUM POST-ACTION PROTOCOL" in result.stdout


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


def test_codex_post_action_accepts_quoted_script_path_with_spaces(tmp_path):
    repo = _repo(tmp_path)
    script = repo / "analysis script.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": 'python "analysis script.py"'},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-quoted-script-path",
        },
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MYCELIUM POST-ACTION PROTOCOL" in context


@pytest.mark.parametrize(
    "command",
    [
        'time python "analysis script".py',
        'exec nice -n 5 timeout 10s python "analysis script".py',
    ],
)
def test_codex_post_action_accepts_wrapped_concatenated_script_paths(
    tmp_path, command
):
    repo = _repo(tmp_path)
    script = repo / "analysis script.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-post-action.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-wrapped-concatenated-script-path",
        },
    )

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MYCELIUM POST-ACTION PROTOCOL" in context


@pytest.mark.parametrize(
    "command",
    [
        'time python "analysis script".py',
        'exec nice -n 5 timeout 10s python "analysis script".py',
    ],
)
def test_codex_data_tracker_accepts_wrapped_concatenated_script_paths(
    tmp_path, command
):
    repo = _repo(tmp_path)
    script = repo / "analysis script.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-wrapped-concatenated-lineage",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(script)


def test_codex_data_tracker_applies_env_working_directory(tmp_path):
    repo = _repo(tmp_path)
    workdir = repo / "sub"
    workdir.mkdir()
    script = workdir / "analysis.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "env -C sub python analysis.py"},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-env-working-directory",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(script)
    assert event["inputs"][0]["path"] == str(workdir / "input.csv")


def test_codex_data_tracker_accepts_escaped_script_path_with_spaces(tmp_path):
    repo = _repo(tmp_path)
    script = repo / "analysis script.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": r"python analysis\ script.py"},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-escaped-script-path",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(script)
    assert event["inputs"][0]["path"] == str(repo / "input.csv")


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


def test_codex_data_tracker_records_multiline_inline_language_control_flow(tmp_path):
    repo = _repo(tmp_path)
    command = (
        "python -c \"import pandas as pd\n"
        "if True:\n"
        "    pd.read_csv('input.csv')\n"
        "for value in [1]:\n"
        "    pass\""
    )

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"exit_code": 0},
            "turn_id": "turn-multiline-inline-lineage",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] is None
    assert "if True:" in event["script_source"]
    assert event["inputs"][0]["path"] == str(repo / "input.csv")


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


def test_codex_data_tracker_reads_exit_from_code_mode_model_output(tmp_path):
    repo = _repo(tmp_path)
    script = repo / "run.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    model_output = [
        {
            "type": "input_text",
            "text": "Script completed\nWall time 0.5 seconds\nOutput:\n",
        },
        {
            "type": "input_text",
            "text": json.dumps(
                {
                    "chunk_id": "abc123",
                    "wall_time_seconds": 0.5,
                    "exit_code": 1,
                    "output": "ModuleNotFoundError: No module named 'h5py'\n",
                }
            ),
        },
    ]

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python run.py"},
            "tool_response": model_output,
            "turn_id": "turn-code-mode-exit",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(script)
    assert event["bash_exit"] == 1


def test_codex_data_tracker_preserves_unknown_exit_from_native_empty_response(
    tmp_path,
):
    """Current Codex PostToolUse runs before the outer CLI exit event exists."""
    repo = _repo(tmp_path)
    script = repo / "run.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    session_id = "codex-native-payload"
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": session_id},
    )
    assert start.returncode == 0, start.stderr

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "session_id": session_id,
            "turn_id": "turn-native-empty-response",
            "cwd": str(repo),
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python3 run.py"},
            "tool_response": "",
            "tool_use_id": "exec-native-empty-response",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (_host_state(repo, session_id) / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(script)
    assert event["bash_exit"] is None
    assert event["bash_wall_s"] is None


def test_codex_data_tracker_prefers_structured_exit_over_earlier_output_text(tmp_path):
    repo = _repo(tmp_path)
    script = repo / "run.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    model_output = [
        {
            "type": "input_text",
            "text": "Script completed\nOutput:\nexample text: exit code 99\n",
        },
        {
            "type": "input_text",
            "text": json.dumps({"exit_code": 0, "output": "exit code 99\n"}),
        },
    ]

    result = _run_hook(
        "mycelium-data-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "Bash",
            "tool_input": {"command": "python run.py"},
            "tool_response": model_output,
            "turn_id": "turn-structured-exit-precedence",
        },
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(
        (repo / ".mycelium" / "mycelium-data-events.tmp").read_text()
    )
    assert event["script"] == str(script)
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


def test_codex_apply_patch_activity_ignores_failed_code_mode_output(tmp_path):
    repo = _repo(tmp_path)
    patch = """*** Begin Patch
*** Add File: should-not-count.py
+content
*** End Patch"""
    model_output = [
        {"type": "input_text", "text": "Script completed\nOutput:\n"},
        {
            "type": "input_text",
            "text": json.dumps(
                {"exit_code": 1, "output": "Invalid Context 0:\n-old\n"}
            ),
        },
    ]

    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": model_output,
            "turn_id": "turn-failed-code-mode-patch",
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (repo / ".mycelium" / "mycelium-session-activity.tmp").exists()
    assert not (repo / ".mycelium" / "mycelium-reminded.tmp").exists()


def test_codex_apply_patch_activity_prefers_structured_success_over_output_text(
    tmp_path,
):
    repo = _repo(tmp_path)
    patch = """*** Begin Patch
*** Add File: should-count.py
+content
*** End Patch"""
    model_output = [
        {
            "type": "input_text",
            "text": "Script completed\nOutput:\nexample text: exit code 99\n",
        },
        {
            "type": "input_text",
            "text": json.dumps({"exit_code": 0, "output": "exit code 99\n"}),
        },
    ]

    result = _run_hook(
        "mycelium-activity-tracker.sh",
        repo,
        {
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {"command": patch},
            "tool_response": model_output,
            "turn_id": "turn-structured-success-precedence",
        },
    )

    assert result.returncode == 0, result.stderr
    activity = repo / ".mycelium" / "mycelium-session-activity.tmp"
    assert activity.read_text().splitlines() == ["should-count.py"]


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


def test_invalid_active_log_marker_cannot_bypass_stop_enforcement(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    work_started = int(time.time()) + 60
    outside_log = tmp_path / "outside-session.md"
    outside_log.write_text("external\n")
    active_marker = state / "active-session-log.tmp"
    active_marker.write_text(f"{outside_log}\n{work_started}\n")
    (state / "session-start-ts.tmp").write_text(f"{work_started}\n")
    (state / "mycelium-reminded.tmp").write_text(f"{work_started}\n")
    (state / "mycelium-session-activity.tmp").write_text("analysis.py\n")

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "invalid-log"},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block"
    assert str(outside_log) not in result.stdout
    assert outside_log.read_text() == "external\n"
    assert (state / "mycelium-reminded.tmp").is_file()


def test_malformed_session_start_timestamp_cannot_trigger_subagent_bypass(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "owner-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    (state / "session-start-ts.tmp").write_text("not-a-timestamp\n")
    work_started = int(time.time()) + 60
    (state / "mycelium-reminded.tmp").write_text(f"{work_started}\n")
    (state / "mycelium-session-activity.tmp").write_text("analysis.py\n")

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "owner-stop"},
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "block"
    assert (state / "active-session-log.tmp").is_file()


def test_stop_sanitizes_registry_cells_with_pipe_characters(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "pipe-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    log_path = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    session_id = log_path.name[:14]
    (repo / "changed|file.txt").write_text("work\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\nPipe-containing paths are valid.\n"
    )

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "pipe-stop"},
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    registry = repo / ".living" / "log" / "LOG_REGISTRY.md"
    assert registry.read_text().count(f"| {session_id} |") == 1
    assert "changed&#124;file.txt" in registry.read_text()
    assert not (repo / ".living" / "log" / ".upsert_registry_row.err").exists()


def test_stop_preserves_agent_authored_registry_metadata(tmp_path):
    repo = _repo(tmp_path)
    host_id = "registry-owner"
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "session_id": host_id},
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, host_id)
    log_path = Path((state / "active-session-log.tmp").read_text().splitlines()[0])
    session_id = log_path.name[:14]
    registry = repo / ".living" / "log" / "LOG_REGISTRY.md"
    with registry.open("a") as handle:
        handle.write(
            f"| 2026-08-02 | {session_id} | repo | draft | 1m | 1 | "
            "Compared ROI-level cell-type proportions | tables/counts.csv; "
            f"figures/overview.png | active | gastruloid; review | [log]({log_path.name}) |\n"
        )
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\n### Registry metadata\nStop preserves authored semantics.\n"
    )

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "session_id": host_id},
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    content = registry.read_text()
    assert content.count(f"| {session_id} |") == 1
    assert "Compared ROI-level cell-type proportions" in content
    assert "tables/counts.csv; figures/overview.png" in content
    assert "gastruloid; review" in content
    assert "| complete |" in content


def test_stop_preserves_transaction_when_registry_upsert_fails(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "upsert-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    active_marker = state / "active-session-log.tmp"
    log_path = Path(active_marker.read_text().splitlines()[0])
    session_id = log_path.name[:14]
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "decisions.md").write_text(
        "# Decision Log\n\nRegistry failure must be retried.\n"
    )
    failing_helper = tmp_path / "failing-upsert.py"
    failing_helper.write_text("raise SystemExit(23)\n")

    failed = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "upsert-fail"},
        {"MYCELIUM_REGISTRY_UPSERT_HELPER": str(failing_helper)},
    )

    assert failed.returncode == 0, failed.stderr
    failed_payload = json.loads(failed.stdout)
    assert failed_payload["decision"] == "block"
    assert "registry finalization failed" in failed_payload["reason"]
    assert active_marker.is_file()
    assert (state / "session-file-baseline.json").is_file()
    assert "ended:\n" in log_path.read_text()
    assert "Session ended" not in log_path.read_text()

    resumed = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "resume", "turn_id": "upsert-resume"},
    )
    assert resumed.returncode == 0, resumed.stderr
    assert active_marker.is_file()
    assert Path(active_marker.read_text().splitlines()[0]) == log_path
    assert log_path.name[:14] == session_id

    retried = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": True, "turn_id": "upsert-retry"},
    )
    assert retried.returncode == 0, retried.stderr
    assert '"decision": "block"' not in retried.stdout
    assert log_path.read_text().count("Session ended") == 1
    registry = repo / ".living" / "log" / "LOG_REGISTRY.md"
    assert registry.read_text().count(f"| {session_id} |") == 1
    assert not active_marker.exists()


def test_stop_preserves_transaction_when_atomic_log_finalization_fails(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "log-finalize-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    active_marker = state / "active-session-log.tmp"
    log_path = Path(active_marker.read_text().splitlines()[0])
    session_id = log_path.name[:14]
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\nAtomic finalization must be retryable.\n"
    )
    failing_helper = tmp_path / "failing-log-finalizer.py"
    failing_helper.write_text("raise SystemExit(31)\n")

    failed = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "log-finalize-fail"},
        {"MYCELIUM_LOG_FINALIZER_HELPER": str(failing_helper)},
    )

    assert failed.returncode == 0, failed.stderr
    payload = json.loads(failed.stdout)
    assert payload["decision"] == "block"
    assert "session log finalization failed" in payload["reason"]
    assert active_marker.is_file()
    assert "ended:\n" in log_path.read_text()
    assert "Session ended" not in log_path.read_text()
    assert "accepted by Stop" not in (state / "last-session.md").read_text()

    resumed = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "compact", "turn_id": "log-finalize-compact"},
    )
    assert resumed.returncode == 0, resumed.stderr
    assert Path(active_marker.read_text().splitlines()[0]) == log_path
    assert log_path.name[:14] == session_id

    retried = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": True, "turn_id": "log-finalize-retry"},
    )
    assert retried.returncode == 0, retried.stderr
    assert '"decision": "block"' not in retried.stdout
    finalized = log_path.read_text()
    assert "ended:\n" not in finalized
    assert finalized.count("Session ended") == 1
    assert not active_marker.exists()


def test_stop_retries_ended_log_when_handoff_finalization_fails(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "handoff-fail-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    active_marker = state / "active-session-log.tmp"
    log_path = Path(active_marker.read_text().splitlines()[0])
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\n### Retry handoff\nHandoff publication is retryable.\n"
    )
    failing_helper = tmp_path / "failing-handoff-finalizer.py"
    failing_helper.write_text("raise SystemExit(41)\n")

    failed = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False},
        {"MYCELIUM_HANDOFF_FINALIZER_HELPER": str(failing_helper)},
    )

    assert failed.returncode == 0, failed.stderr
    assert json.loads(failed.stdout)["decision"] == "block"
    assert active_marker.is_file()
    assert log_path.read_text().count("Session ended") == 1
    pending = state / "handoff-finalization-pending.tmp"
    assert pending.is_file()
    accepted_at = pending.read_text().splitlines()[0]
    assert "accepted by Stop" not in (state / "last-session.md").read_text()

    resumed = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "compact", "turn_id": "handoff-fail-resume"},
    )
    assert resumed.returncode == 0, resumed.stderr
    assert active_marker.is_file()
    assert pending.is_file()

    retried = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": True},
    )

    assert retried.returncode == 0, retried.stderr
    assert '"decision": "block"' not in retried.stdout
    assert log_path.read_text().count("Session ended") == 1
    assert accepted_at in (state / "last-session.md").read_text()
    assert not active_marker.exists()
    assert not pending.exists()


def test_stop_preserves_fresh_complete_five_section_handoff(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "handoff-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\nA complete handoff must survive Stop.\n"
    )
    handoff = state / "last-session.md"
    complete = (
        "SESSION RESUME — Last session (2026-08-01 20:15):\n\n"
        "## What was worked on\n- Lifecycle smoke audit.\n\n"
        "## Key decisions made\n- Preserve the authored handoff.\n\n"
        "## Blockers & surprises\n- None.\n\n"
        "## Current state\n- Tests passed.\n\n"
        "## Next steps\n- Natural Stop finalization remains pending.\n"
        "- Attempt natural Stop now.\n"
        "- Continue from the accepted handoff.\n"
    )
    handoff.write_text(complete)
    future = time.time() + 2
    os.utime(handoff, (future, future))

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "handoff-stop"},
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    finalized = handoff.read_text()
    assert "Lifecycle status: accepted by Stop" in finalized
    assert "remains pending" not in finalized
    assert "Attempt natural Stop" not in finalized
    assert "Lifecycle smoke audit." in finalized
    assert "Preserve the authored handoff." in finalized
    assert "Continue from the accepted handoff." in finalized


def test_stop_falls_back_when_cleanup_empties_required_handoff_sections(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "cleanup-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\nHandoff cleanup must preserve the completeness contract.\n"
    )
    handoff = state / "last-session.md"
    handoff.write_text(
        "## What was worked on\n- Lifecycle cleanup regression.\n\n"
        "## Key decisions made\n- Fall back if cleanup empties a section.\n\n"
        "## Blockers & surprises\n- None.\n\n"
        "## Current state\n- Natural Stop finalization remains pending.\n\n"
        "## Next steps\n- Attempt natural Stop now.\n"
    )
    future = time.time() + 2
    os.utime(handoff, (future, future))

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "cleanup-stop"},
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    finalized = handoff.read_text()
    headings = (
        "## What was worked on",
        "## Key decisions made",
        "## Blockers & surprises",
        "## Current state",
        "## Next steps",
    )
    offsets = [finalized.index(heading) for heading in headings]
    assert offsets == sorted(offsets)
    for index, offset in enumerate(offsets):
        assert finalized.count(headings[index]) == 1
        body_start = offset + len(headings[index])
        body_end = offsets[index + 1] if index + 1 < len(offsets) else len(finalized)
        assert finalized[body_start:body_end].strip()
    assert "Natural Stop finalization remains pending." not in finalized
    assert "Attempt natural Stop now." not in finalized
    assert "Review the finalized session log" in finalized


@pytest.mark.parametrize(
    "handoff_body",
    (
        (
            "SESSION RESUME — Last session (2026-08-01 20:15):\n\n"
            "## What was worked on\n- Lifecycle smoke audit.\n\n"
            "## Key decisions made\n- Preserve authored handoffs only when complete.\n\n"
            "## Blockers & surprises\n\n"
            "## Current state\n- Tests passed.\n\n"
            "## Next steps\n- Attempt natural Stop.\n"
        ),
        (
            "## What was worked on\n- Lifecycle smoke audit.\n\n"
            "## Key decisions made\n- Preserve authored handoffs only when complete.\n\n"
            "## Current state\n- Tests passed.\n\n"
            "## Blockers & surprises\n- None.\n\n"
            "## Next steps\n- Attempt natural Stop.\n"
        ),
        (
            "## What was worked on\n- Lifecycle smoke audit.\n\n"
            "## Key decisions made\n- Preserve authored handoffs only when complete.\n\n"
            "## Blockers & surprises\n- None.\n\n"
            "## Current state\n- Tests passed.\n\n"
            "## Next steps\n- Attempt natural Stop.\n\n"
            "## Blockers & surprises\n- Duplicate heading.\n"
        ),
    ),
    ids=("empty-section", "misordered-sections", "duplicate-heading"),
)
def test_stop_replaces_fresh_incomplete_handoff(tmp_path, handoff_body):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "empty-handoff-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\nEvery handoff section needs substantive content.\n"
    )
    handoff = state / "last-session.md"
    handoff.write_text(handoff_body)
    future = time.time() + 2
    os.utime(handoff, (future, future))

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "turn_id": "empty-handoff-stop",
        },
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    finalized = handoff.read_text()
    headings = (
        "## What was worked on",
        "## Key decisions made",
        "## Blockers & surprises",
        "## Current state",
        "## Next steps",
    )
    offsets = [finalized.index(heading) for heading in headings]
    assert offsets == sorted(offsets)
    for index, offset in enumerate(offsets):
        assert finalized.count(headings[index]) == 1
        body_start = offset + len(headings[index])
        body_end = offsets[index + 1] if index + 1 < len(offsets) else len(finalized)
        assert finalized[body_start:body_end].strip()


def test_stop_preserves_fresh_complete_alternate_five_section_handoff(
    tmp_path,
):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "alt-handoff-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "learnings.md").write_text(
        "# Learnings\n\nA complete alternate handoff must survive Stop.\n"
    )
    handoff = state / "last-session.md"
    complete = (
        "## Current State\n- Tests passed.\n\n"
        "## What Was Done\n- Lifecycle smoke audit.\n\n"
        "## Key Decisions\n- Preserve the authored handoff.\n\n"
        "## Next Steps\n- Continue review.\n\n"
        "## Relevant Files\n- `.living/learnings.md`.\n"
    )
    handoff.write_text(complete)
    future = time.time() + 2
    os.utime(handoff, (future, future))

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "stop_hook_active": False,
            "turn_id": "alt-handoff-stop",
        },
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    finalized = handoff.read_text()
    assert "Lifecycle status: accepted by Stop" in finalized
    assert complete in finalized


def test_stop_fallback_handoff_contains_all_five_sections(tmp_path):
    repo = _repo(tmp_path)
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {"cwd": str(repo), "source": "startup", "turn_id": "fallback-start"},
    )
    assert start.returncode == 0, start.stderr
    state = repo / ".mycelium"
    (repo / "work.py").write_text("print('work')\n")
    (repo / ".living" / "decisions.md").write_text(
        "# Decisions\n\nGenerate a complete deterministic fallback.\n"
    )
    handoff = state / "last-session.md"
    handoff.unlink(missing_ok=True)

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "fallback-stop"},
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' not in result.stdout
    fallback = handoff.read_text()
    for heading in (
        "## What was worked on",
        "## Key decisions made",
        "## Blockers & surprises",
        "## Current state",
        "## Next steps",
    ):
        assert fallback.count(heading) == 1
    assert "`.living/decisions.md`" in fallback
    assert "`.living/learnings.md`" in fallback


def test_stop_preserves_lineage_state_when_consolidation_fails(tmp_path):
    repo = _repo(tmp_path)
    host_session_id = "host-lineage-failure"
    start = _run_hook(
        "mycelium-health.sh",
        repo,
        {
            "cwd": str(repo),
            "source": "startup",
            "session_id": host_session_id,
            "turn_id": "lineage-fail-start",
        },
    )
    assert start.returncode == 0, start.stderr
    state = _host_state(repo, host_session_id)
    active_marker = state / "active-session-log.tmp"
    events = state / "mycelium-data-events.tmp"
    events.write_text(
        json.dumps(
            {
                "ts": "2026-08-01T12:00:00Z",
                "script": "analysis/fail.py",
                "inputs": [],
                "outputs": [],
            }
        )
        + "\n"
    )
    failing_extractor = tmp_path / "failing-extractor.py"
    failing_extractor.write_text("raise SystemExit(29)\n")

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": host_session_id,
            "stop_hook_active": False,
            "turn_id": "lineage-fail-stop",
        },
        {"MYCELIUM_DATA_EXTRACTOR": str(failing_extractor)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "lineage consolidation failed" in payload["reason"]
    assert events.is_file()
    assert (state / "data-lineage-session-id.tmp").is_file()
    assert active_marker.is_file()


def test_stop_rejects_unsafe_host_lineage_session_id(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    state.mkdir()
    events = state / "mycelium-data-events.tmp"
    events.write_text(
        json.dumps(
            {
                "ts": "2026-08-01T12:00:00Z",
                "script": "analysis/run.py",
                "inputs": [],
                "outputs": [],
            }
        )
        + "\n"
    )

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {
            "cwd": str(repo),
            "session_id": "../../outside-lineage",
            "stop_hook_active": False,
            "turn_id": "unsafe-lineage-id",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert events.is_file()
    assert not (state / "run").exists()
    assert not (repo / ".living" / "outside-lineage.json").exists()


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


def test_stop_lock_recovers_recent_lock_owned_by_dead_process(tmp_path):
    state = tmp_path / ".mycelium"
    lock = state / "mycelium-stop.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"999999999 {int(time.time())}\n")
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


def test_stop_lock_does_not_steal_recent_ownerless_publication(tmp_path):
    state = tmp_path / ".mycelium"
    lock = state / "mycelium-stop.lock"
    lock.mkdir(parents=True)
    hook_lib = HOOKS_DIR / "mycelium-hook-lib.sh"
    command = (
        f'source "{hook_lib}"\n'
        f'mycelium_acquire_stop_lock "{state}"\n'
    )

    with pytest.raises(subprocess.TimeoutExpired):
        subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            timeout=0.2,
            check=False,
        )

    assert lock.is_dir()
    assert not (lock / "owner").exists()


def test_stop_fails_closed_when_live_lock_outlasts_retry_budget(tmp_path):
    repo = _repo(tmp_path)
    state = repo / ".mycelium"
    lock = state / "mycelium-stop.lock"
    lock.mkdir(parents=True)
    (lock / "owner").write_text(f"{os.getpid()} {int(time.time())}\n")

    result = _run_hook(
        "mycelium-stop-check.sh",
        repo,
        {"cwd": str(repo), "stop_hook_active": False, "turn_id": "busy-lock"},
        extra_env={"MYCELIUM_STOP_LOCK_MAX_ATTEMPTS": "2"},
    )

    assert result.returncode == 0, result.stderr
    assert '"decision": "block"' in result.stdout
    assert "lifecycle transaction lock" in result.stdout
    assert lock.is_dir()


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
    (log_path.parent / "LOG_REGISTRY.md").write_text(
        "# Session Log Registry\n\n"
        "| Date | Session ID | Project | Branch | Duration | Files Changed | "
        "Summary | Key Outputs | Status | Tags | Log |\n"
        "|------|-----------|---------|--------|----------|---------------|"
        "---------|-------------|--------|------|-----|\n"
    )
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
