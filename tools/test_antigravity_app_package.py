"""The App package profile is workspace-local, separately owned, and leaves IDE defaults intact."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SPEC = importlib.util.spec_from_file_location("antigravity_package", Path(__file__).with_name("antigravity.py"))
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)
PYTHON = Path(sys.executable)
CID = "5f0c2b1e-7d4a-4c3e-9a51-2b8e6f0d4c17"  # synthetic


def snapshot(root):
    if not root.exists():
        return None
    return {p.relative_to(root).as_posix(): (p.is_symlink(), p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".living").mkdir()
    return root


def app_target(workspace):
    return workspace / ".agents/plugins/mycelium"


def test_app_package_identity_guidance_and_hooks(workspace):
    target = app_target(workspace)
    report = package.install(target, python=PYTHON, profile="app")
    assert report["ok"] and report["profile"] == "app"
    manifest = json.loads((target / package.OWNERSHIP).read_text())
    assert manifest["owner"] == "mycelium-antigravity-app" and manifest["schema"] == 2
    assert (target / ".antigravity-host").read_text() == "antigravity-app\n"
    hooks = json.loads((target / "hooks.json").read_text())["mycelium"]
    assert hooks["PostToolUse"][0]["matcher"] == "run_command|read_file|write_file|edit_file"
    for event in ("PreInvocation", "PostToolUse", "PostInvocation", "Stop"):
        handler = hooks[event][0]["hooks"][0] if event == "PostToolUse" else hooks[event][0]
        assert handler["command"].endswith(f"skills/core/scripts/antigravity_hooks.py --host antigravity-app {event}")
    skill = (target / "skills/core/SKILL.md").read_text()
    assert "## Antigravity App host adapter" in skill and "## Antigravity IDE host adapter" not in skill
    assert "view_file" not in package.APP_GUIDANCE and "ArtifactMetadata" not in package.APP_GUIDANCE
    assert package.doctor(target, workspace=workspace, profile="app")["ok"]


def test_app_and_ide_identities_are_distinct_and_ide_default_is_unchanged():
    ide = package.candidate_files(package.SOURCE, PYTHON)
    explicit = package.candidate_files(package.SOURCE, PYTHON, "ide")
    app = package.candidate_files(package.SOURCE, PYTHON, "app")
    assert ide == explicit
    ide_manifest = json.loads(ide[package.OWNERSHIP][0])
    app_manifest = json.loads(app[package.OWNERSHIP][0])
    assert ide_manifest["owner"] == "mycelium-antigravity-ide"
    assert ide_manifest["source_digest"] != app_manifest["source_digest"]
    assert ide[".antigravity-host"][0] == b"antigravity-ide\n"
    ide_hooks = json.loads(ide["hooks.json"][0])["mycelium"]
    assert ide_hooks["PostToolUse"][0]["matcher"] == \
        "run_command|view_file|write_to_file|replace_file_content|multi_replace_file_content"
    assert "--host" not in json.dumps(ide_hooks)
    assert set(ide) == set(app)


@pytest.mark.parametrize("where", ["global-default", "plugins-without-agents", "agents-root", "missing-workspace"])
def test_app_destination_must_be_workspace_local(tmp_path, where):
    target = {"global-default": tmp_path / ".gemini/config/plugins/mycelium",
              "plugins-without-agents": tmp_path / "research/plugins/mycelium",
              "agents-root": tmp_path / "research/.agents/mycelium",
              "missing-workspace": tmp_path / "absent/.agents/plugins/mycelium"}[where]
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="workspace"):
        package.install(target, python=PYTHON, profile="app")
    with pytest.raises(ValueError, match="workspace"):
        package.doctor(target, profile="app")
    assert snapshot(tmp_path) == before


def test_cli_app_profile_requires_explicit_destination(tmp_path):
    env = dict(os.environ, HOME=str(tmp_path))
    result = subprocess.run([sys.executable, str(Path(package.__file__)), "install", "--profile", "app",
                             "--dry-run"], capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 2 and "explicit workspace-local --destination" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_profiles_never_update_or_remove_each_other(workspace):
    target = app_target(workspace)
    package.install(target, python=PYTHON)  # IDE-owned package at an App-shaped path
    before = snapshot(target)
    for operation in (lambda: package.install(target, python=PYTHON, profile="app"),
                      lambda: package.install(target, python=PYTHON, profile="app", repair_permissions=True),
                      lambda: package.uninstall(target, profile="app")):
        with pytest.raises(ValueError, match="ownership manifest"):
            operation()
        assert snapshot(target) == before
    package.uninstall(target)
    package.install(target, python=PYTHON, profile="app")
    before = snapshot(target)
    for operation in (lambda: package.install(target, python=PYTHON),
                      lambda: package.uninstall(target)):
        with pytest.raises(ValueError, match="ownership manifest"):
            operation()
        assert snapshot(target) == before


def test_app_profile_has_no_legacy_manifest_generation(workspace):
    target = app_target(workspace)
    package.install(target, python=PYTHON, profile="app")
    receipt = target / package.OWNERSHIP
    manifest = json.loads(receipt.read_text())
    manifest["schema"] = 1
    manifest.pop("modes")
    receipt.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="schema"):
        package.doctor(target, profile="app")
    with pytest.raises(ValueError):
        package.install(target, python=PYTHON, profile="app", repair_permissions=True)


def test_installed_app_hook_command_runs_from_plugin_root_with_exact_native_identity(workspace, tmp_path):
    target = app_target(workspace)
    package.install(target, python=PYTHON, profile="app")
    home = tmp_path / "home"
    artifact = home / ".gemini/antigravity/brain" / CID
    transcript = artifact / ".system_generated/logs/transcript_full.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
                                      "status": "DONE"}) + "\n")
    payload = {"conversationId": CID, "workspacePaths": [str(workspace)], "artifactDirectoryPath": str(artifact),
               "transcriptPath": str(transcript), "invocationNum": 0, "initialNumSteps": 1,
               "lastUserInput": "synthetic", "modelName": "gemini-4-argon-medium"}
    command = json.loads((target / "hooks.json").read_text())["mycelium"]["PreInvocation"][0]["command"]
    env = dict(os.environ, HOME=str(home))
    result = subprocess.run(["bash", "-c", command], input=json.dumps(payload), text=True, capture_output=True,
                            cwd=target, env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "MYCELIUM — Antigravity App" in json.loads(result.stdout)["injectSteps"][0]["ephemeralMessage"]
    assert (workspace / ".mycelium/antigravity-app" / CID / "native-state.json").is_file()
    assert (workspace / ".mycelium/run/antigravity-app" / f"{CID}-turn-0/active-session-log.tmp").is_file()
    assert not (workspace / ".mycelium/antigravity-ide").exists()
    # The same package command refuses the IDE's native root for this conversation.
    ide = home / ".gemini/antigravity-ide/brain" / CID
    (ide / ".system_generated/logs").mkdir(parents=True)
    (ide / ".system_generated/logs/transcript_full.jsonl").write_bytes(transcript.read_bytes())
    foreign = dict(payload, artifactDirectoryPath=str(ide),
                   transcriptPath=str(ide / ".system_generated/logs/transcript_full.jsonl"))
    before = snapshot(workspace / ".mycelium")
    refused = subprocess.run(["bash", "-c", command], input=json.dumps(foreign), text=True, capture_output=True,
                             cwd=target, env=env, timeout=120)
    assert refused.returncode == 1 and refused.stdout.strip() == "{}"
    assert snapshot(workspace / ".mycelium") == before
