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


def git_would_add(workspace):
    out = subprocess.run(["git", "-C", str(workspace), "add", "-A", "--dry-run"],
                         capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if ".agents" in line]


def test_app_install_update_and_uninstall_stay_out_of_git(workspace, tmp_path):
    """The workspace-local package, its staging area and its backups are never
    picked up by `git add -A`, and a verified update leaves no backup behind."""
    (workspace / ".gitignore").write_text("results/\n")
    user_ignore = (workspace / ".gitignore").read_bytes()
    target = app_target(workspace)
    assert package.install(target, python=PYTHON, profile="app")["ok"]
    assert git_would_add(workspace) == []
    other = tmp_path / "other venv"
    import venv
    venv.EnvBuilder(with_pip=False, symlinks=True).create(other)
    report = package.install(target, python=other / "bin/python3", profile="app")  # a real update
    assert report["ok"] and "backup" not in report
    backups = workspace / ".agents/.mycelium-backups"
    assert not backups.exists() or [p for p in backups.iterdir() if p.is_dir()] == []
    assert git_would_add(workspace) == []
    package.uninstall(target, profile="app")
    assert git_would_add(workspace) == []
    assert (workspace / ".gitignore").read_bytes() == user_ignore  # the user's file is untouched


def test_app_doctor_reports_unrecognised_edit_records_read_only(workspace, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    target = app_target(workspace)
    package.install(target, python=PYTHON, profile="app")
    report = package.doctor(target, workspace=workspace, profile="app")
    assert report["ok"] and report["app_edit_records"]["status"] == "unavailable" and "warnings" not in report
    assert not (home / ".gemini").exists()
    sys.path.insert(0, str(package.SOURCE / "skills/core/scripts"))
    try:
        from test_antigravity_app_native import Store, edit_payload
        import antigravity_app_store as store
    finally:
        sys.path.pop(0)
    db = Store(home, cid=CID)
    try:
        db.put(3, edit_payload(str(workspace / "a.md"), step=3, type_url="type.googleapis.com/x.FileWrite"))
        report = package.doctor(target, workspace=workspace, profile="app")
    finally:
        db.close()
    assert report["ok"]  # package identity is fine; the host-format gap is a warning
    assert report["app_edit_records"]["status"] == "unrecognized"
    assert report["app_edit_records"]["assumed_type"] == store.FILE_EDIT_TYPE
    assert report["warnings"] and "not tracked" in report["warnings"][0]


@pytest.mark.parametrize("change", ["unowned-in-cache-dir", "unowned-file", "edited-owned-file", "mode-change"])
def test_app_update_keeps_a_backup_holding_anything_but_its_own_package(workspace, tmp_path, change):
    """The displaced generation is deleted only if it holds exactly its manifest
    (plus regenerable bytecode); otherwise the backup and the content survive."""
    target = app_target(workspace)
    package.install(target, python=PYTHON, profile="app")
    (target / "skills/core/scripts/__pycache__").mkdir(exist_ok=True)
    (target / "skills/core/scripts/__pycache__/generate_index.cpython-399.pyc").write_bytes(b"bytecode")
    marker = {"unowned-in-cache-dir": ".pytest_cache/user-notes.txt",
              "unowned-file": "skills/core/user-notes.txt",
              "edited-owned-file": "README.md", "mode-change": "README.md"}[change]
    real_doctor = package.doctor

    def tamper_after_backup(destination, **kw):  # the displaced copy changes after the identity check
        backups = workspace / ".agents/.mycelium-backups"
        for old in (p for p in backups.iterdir() if p.is_dir()) if backups.exists() else ():
            path = old / marker
            path.parent.mkdir(parents=True, exist_ok=True)
            if change == "mode-change":
                path.chmod(0o600)
            elif not path.exists() or change == "edited-owned-file":
                path.write_text("user content\n")
        return real_doctor(destination, **kw)

    other = tmp_path / "other venv"
    import venv
    venv.EnvBuilder(with_pip=False, symlinks=True).create(other)
    import unittest.mock
    with unittest.mock.patch.object(package, "doctor", tamper_after_backup):
        report = package.install(target, python=other / "bin/python3", profile="app")
    assert report["ok"] and "backup" in report
    kept = Path(report["backup"]) / marker
    if change == "mode-change":
        assert kept.stat().st_mode & 0o777 == 0o600
    else:
        assert kept.read_text() == "user content\n"
    assert git_would_add(workspace) == []


def test_app_update_still_drops_a_backup_with_only_bytecode_added(workspace, tmp_path):
    target = app_target(workspace)
    package.install(target, python=PYTHON, profile="app")
    (target / "skills/core/scripts/__pycache__").mkdir(exist_ok=True)
    (target / "skills/core/scripts/__pycache__/x.cpython-399.pyc").write_bytes(b"bytecode")
    other = tmp_path / "other venv"
    import venv
    venv.EnvBuilder(with_pip=False, symlinks=True).create(other)
    report = package.install(target, python=other / "bin/python3", profile="app")
    assert report["ok"] and "backup" not in report
    backups = workspace / ".agents/.mycelium-backups"
    assert not backups.exists() or [p for p in backups.iterdir() if p.is_dir()] == []


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
    assert set(ide) | {".gitignore"} == set(app)  # only the workspace-local App package self-ignores
    assert app[".gitignore"][0] == package.GIT_IGNORE_ALL


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
