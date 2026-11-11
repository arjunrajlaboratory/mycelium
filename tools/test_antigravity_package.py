"""Installation must preserve user files and package all base dependencies."""
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import venv

import pytest


def test_doctor_reports_missing_workspace_opt_in_without_mutation(tmp_path):
    target = tmp_path / 'plugins/mycelium'
    package.install(target, python=Path(sys.executable))
    workspace = tmp_path / 'research'
    workspace.mkdir()
    assert not package.doctor(target, workspace=workspace)['ok']
    assert list(workspace.iterdir()) == []
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()}
    report = package.doctor(target, workspace=workspace)
    assert report['workspace']['reason'] == 'Git root has no .living opt-in'
    assert {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob('*') if p.is_file()} == before
    (workspace / '.living').mkdir()
    report = package.doctor(target, workspace=workspace)
    assert report['ok'] and report['workspace']['git_root'] == str(workspace)
    assert report['workspace']['lifecycle_opt_in'] is True


def test_doctor_rejects_linked_workspace_knowledge(tmp_path):
    target = tmp_path / 'plugins/mycelium'
    package.install(target, python=Path(sys.executable))
    workspace = tmp_path / 'research'
    workspace.mkdir()
    subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / '.living').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='symlink'):
        package.doctor(target, workspace=workspace)
    assert list(outside.iterdir()) == []

SPEC = importlib.util.spec_from_file_location("antigravity_package", Path(__file__).with_name("antigravity.py"))
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


def test_selected_virtual_environment_is_preserved_for_hooks_and_children(tmp_path):
    selected = tmp_path / "selected venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(selected)
    python = selected / "bin/python3"
    site = subprocess.check_output([str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip()
    (Path(site) / "mycelium_venv_marker.py").write_text("VALUE = 42\n")
    target = tmp_path / "plugins/mycelium"
    package.install(target, python=python)
    hooks = json.loads((target / "hooks.json").read_text())["mycelium"]
    command = hooks["PreInvocation"][0]["command"]
    # Replace only the program payload, retaining both the exact launcher and
    # its PATH setup from the actual generated hook command.
    probe = target / "probe.py"
    probe.write_text("import json, subprocess, sys, mycelium_venv_marker\n"
                     "child = subprocess.check_output(['python3', '-c', 'import sys; import mycelium_venv_marker; print(sys.prefix)'], text=True).strip()\n"
                     "print(json.dumps([sys.prefix, child]))\n")
    command = command.replace("skills/core/scripts/antigravity_hooks.py PreInvocation", "probe.py")
    result = subprocess.run(command, shell=True, cwd=target, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(selected), str(selected)]


def test_native_package_contains_all_base_skills_and_no_personal_coordination(tmp_path):
    target = tmp_path / ".agents/plugins/mycelium"
    package.install(target, python=Path(sys.executable))
    report = package.doctor(target)
    assert report["ok"]
    assert report["skills"] == 11
    assert sorted(p.parent.name for p in (target / "skills").glob("*/SKILL.md")) == [
        "analyze", "codex-review", "core", "develop", "harden", "ideas",
        "ingest", "lifecycle-audit", "report", "review", "transfer",
    ]
    assert not (target / "coordination").exists()
    assert not (target / "mcp_config.json").exists()
    assert (target / "network/conventions/robust-analysis/CONVENTION_PACK.yaml").exists()
    hooks = json.loads((target / "hooks.json").read_text())["mycelium"]
    assert "PreToolUse" not in hooks
    assert set(hooks) == {"enabled", "PreInvocation", "PostToolUse", "PostInvocation", "Stop"}
    assert (target / "skills/core/templates/MYCELIUM.md.template").is_file()
    assert (target / "todo/TODO_ITEM_TEMPLATE.md").is_file()
    before = (target / ".mycelium-install.json").read_bytes()
    package.install(target, python=Path(sys.executable))
    assert (target / ".mycelium-install.json").read_bytes() == before


def test_dry_run_and_unowned_directory_are_preserved(tmp_path):
    target = tmp_path / "plugins/mycelium"
    package.install(target, python=Path(sys.executable), dry_run=True)
    assert not target.parent.exists()
    target.mkdir(parents=True)
    custom = target / "custom.txt"
    custom.write_text("owner data")
    with pytest.raises(ValueError):
        package.install(target, python=Path(sys.executable))
    assert custom.read_text() == "owner data"


def test_edited_or_linked_packages_are_not_overwritten_or_uninstalled(tmp_path):
    target = tmp_path / "plugins/mycelium"
    package.install(target, python=Path(sys.executable))
    skill = target / "skills/core/SKILL.md"
    skill.write_text(skill.read_text() + "\nOwner amendment\n")
    with pytest.raises(ValueError):
        package.install(target, python=Path(sys.executable))
    with pytest.raises(ValueError):
        package.uninstall(target)
    assert skill.read_text().endswith("Owner amendment\n")
    alias = tmp_path / "alias"
    alias.symlink_to(target.parent)
    with pytest.raises(ValueError):
        package.install(alias / "mycelium", python=Path(sys.executable), dry_run=True)


def test_uninstall_moves_only_owned_tree_out_of_discovery(tmp_path):
    target = tmp_path / ".agents/plugins/mycelium"
    package.install(target, python=Path(sys.executable))
    neighbor = target.parent / "other-plugin"
    neighbor.mkdir()
    (neighbor / "user.txt").write_text("keep")
    backup = package.uninstall(target)
    assert not target.exists()
    assert Path(backup).is_dir()
    assert Path(backup).parent != target.parent
    assert (neighbor / "user.txt").read_text() == "keep"


# ---- Python preflight: explicit comparison survives inherited optimization ----------------------------------------

OLD_PYTHON = Path("/usr/bin/python3")
CLI = Path(__file__).with_name("antigravity.py")


def tree_identity(root):
    import hashlib, os, stat as st
    out = {}
    for path in sorted(root.rglob("*")):
        info = os.lstat(path)
        kind = "link" if st.S_ISLNK(info.st_mode) else "dir" if st.S_ISDIR(info.st_mode) else "file"
        out[path.relative_to(root).as_posix()] = (kind, st.S_IMODE(info.st_mode),
                                                  hashlib.sha256(path.read_bytes()).hexdigest() if kind == "file" else None)
    return out


def cli(*args, optimize=None):
    import os
    env = {k: v for k, v in os.environ.items() if k != "PYTHONOPTIMIZE"}
    if optimize:
        env["PYTHONOPTIMIZE"] = optimize
    return subprocess.run([sys.executable, str(CLI), *map(str, args)], capture_output=True, text=True, env=env, timeout=300)


old_python_available = pytest.mark.skipif(
    not OLD_PYTHON.is_file() or subprocess.run([str(OLD_PYTHON), "-c", "import sys; raise SystemExit(sys.version_info >= (3, 11))"]).returncode,
    reason="needs an actual pre-3.11 interpreter at /usr/bin/python3")


@old_python_available
@pytest.mark.parametrize("optimize", [None, "1", "2"])
def test_old_interpreter_refused_before_mutation_even_when_optimized(tmp_path, optimize):
    fresh = tmp_path / "fresh/plugins/mycelium"
    result = cli("install", "--destination", fresh, "--python", OLD_PYTHON, optimize=optimize)
    assert result.returncode == 1 and "requires Python 3.11" in result.stderr
    assert not (tmp_path / "fresh").exists()
    existing = tmp_path / "existing/plugins/mycelium"
    package.install(existing, python=Path(sys.executable))
    before = tree_identity(existing.parent.parent)
    result = cli("install", "--destination", existing, "--python", OLD_PYTHON, optimize=optimize)
    assert result.returncode == 1 and "requires Python 3.11" in result.stderr
    assert tree_identity(existing.parent.parent) == before


@pytest.mark.parametrize("optimize", [None, "1"])
def test_selected_modern_venv_installs_doctors_and_initializes(tmp_path, optimize):
    selected = tmp_path / "selected venv"
    venv.EnvBuilder(with_pip=False, symlinks=True, system_site_packages=True).create(selected)
    python = selected / "bin/python3"
    target = tmp_path / "plugins/mycelium"
    result = cli("install", "--destination", target, "--python", python, optimize=optimize)
    assert result.returncode == 0 and json.loads(result.stdout)["ok"], result.stderr
    manifest = json.loads((target / ".mycelium-install.json").read_text())
    assert manifest["python"] == str(python)  # launcher spelling preserved, not resolved
    assert str(selected / "bin") in json.loads((target / "hooks.json").read_text())["mycelium"]["Stop"][0]["command"]
    assert json.loads(cli("doctor", "--destination", target, optimize=optimize).stdout)["ok"]
    workspace = tmp_path / "research"
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    import os
    env = {k: v for k, v in os.environ.items() if k != "PYTHONOPTIMIZE"}
    if optimize:
        env["PYTHONOPTIMIZE"] = optimize
    init = subprocess.run([manifest["python"], str(target / "skills/core/scripts/init_repo.py"), "--target-dir", str(workspace)],
                          capture_output=True, text=True, env=env, timeout=300)
    assert init.returncode == 0, init.stderr[-2000:]
    valid = subprocess.run([manifest["python"], str(target / "skills/core/scripts/validate_structure.py"), "--target-dir", str(workspace)],
                           capture_output=True, text=True, env=env, timeout=300)
    assert valid.returncode == 0, valid.stdout[-2000:] + valid.stderr[-2000:]


# ---- Publication interval: catchable failures restore the previous owned generation -----------------------------

def owned_update(tmp_path):
    """An existing owned installation whose candidate differs, so install takes the backup/publication path."""
    old_python = tmp_path / "old venv"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(old_python)
    target = tmp_path / "plugins/mycelium"
    package.install(target, python=old_python / "bin/python3")
    return target, tree_identity(target)


def inject(monkeypatch, target, point, error, *, fail_restore=False):
    real = Path.rename
    fired = []

    def rename(self, dest):
        dest = Path(dest)
        kind = ("backup" if self == target and dest.parent.name == ".mycelium-backups" else
                "publish" if dest == target and self.parent.name == ".mycelium-staging" else
                "restore" if dest == target and self.parent.name == ".mycelium-backups" else None)
        if kind == "restore" and fail_restore:
            raise OSError("injected restore failure")
        if kind and not fired and point == f"before-{kind}":
            fired.append(kind)
            raise error
        result = real(self, dest)
        if kind and not fired and point == f"after-{kind}":
            fired.append(kind)
            raise error
        return result
    monkeypatch.setattr(Path, "rename", rename)
    return fired


ERRORS = [KeyboardInterrupt(), SystemExit(7), OSError("injected publication failure")]


@pytest.mark.parametrize("point", ["after-backup", "before-publish", "after-publish"])
@pytest.mark.parametrize("error", ERRORS, ids=lambda e: type(e).__name__)
def test_interrupted_update_restores_previous_installation_and_propagates(tmp_path, monkeypatch, point, error):
    target, before = owned_update(tmp_path)
    fired = inject(monkeypatch, target, point, error)
    with pytest.raises(type(error)) as raised:
        package.install(target, python=Path(sys.executable))
    assert fired and raised.value is error
    if isinstance(error, SystemExit):
        assert raised.value.code == 7
    assert tree_identity(target) == before and package.doctor(target)["ok"]
    assert list((tmp_path / "plugins").iterdir()) == [target]
    assert list((tmp_path / ".mycelium-staging").iterdir()) == []
    assert list((tmp_path / ".mycelium-backups").iterdir()) == []
    monkeypatch.undo()
    report = package.install(target, python=Path(sys.executable))  # ordinary reinstall converges
    assert report["ok"] and json.loads((target / ".mycelium-install.json").read_text())["python"] == sys.executable
    assert Path(report["backup"]).is_dir() and tree_identity(Path(report["backup"])) == before


@pytest.mark.parametrize("point", ["after-backup", "after-publish"])
@pytest.mark.parametrize("error", ERRORS[:2], ids=lambda e: type(e).__name__)
def test_failed_restoration_retains_recoverable_generations_and_evidence(tmp_path, monkeypatch, capsys, point, error):
    target, before = owned_update(tmp_path)
    inject(monkeypatch, target, point, error, fail_restore=True)
    with pytest.raises(type(error)) as raised:
        package.install(target, python=Path(sys.executable))
    assert raised.value is error
    monkeypatch.undo()
    backups = [p for p in (tmp_path / ".mycelium-backups").iterdir() if p.is_dir()]
    assert [tree_identity(p) for p in backups] == [before]  # the only old generation survives intact
    [record] = (tmp_path / ".mycelium-backups").glob("recovery-*.json")
    evidence = json.loads(record.read_text())
    assert evidence["backup"] == str(backups[0]) and "injected restore failure" in evidence["rollback_error"]
    assert type(error).__name__ in evidence["original_error"]
    assert "injected restore failure" in capsys.readouterr().err
    retained = [Path(p) for p in evidence["retained"]]
    assert retained and all(p.exists() for p in retained)


def test_restoration_never_overwrites_unknown_content_at_target(tmp_path, monkeypatch):
    target, before = owned_update(tmp_path)
    real = Path.rename

    def intruder(self, dest):
        result = real(self, dest)
        if self == target and Path(dest).parent.name == ".mycelium-backups":
            target.mkdir()
            (target / "foreign.txt").write_text("not ours")  # unknown content appears; publication then fails
        return result
    monkeypatch.setattr(Path, "rename", intruder)
    with pytest.raises(OSError):
        package.install(target, python=Path(sys.executable))
    monkeypatch.undo()
    assert (target / "foreign.txt").read_text() == "not ours" and [p.name for p in target.iterdir()] == ["foreign.txt"]
    backups = [p for p in (tmp_path / ".mycelium-backups").iterdir() if p.is_dir()]
    assert [tree_identity(p) for p in backups] == [before]
    assert list((tmp_path / ".mycelium-backups").glob("recovery-*.json"))


# ---- After publication, only the exact owned candidate may be moved aside and deleted -----------------------------

@pytest.mark.parametrize("change", ["foreign-added", "foreign-replaced"])
@pytest.mark.parametrize("error", ERRORS[:2], ids=lambda e: type(e).__name__)
def test_unexpected_target_after_publication_is_never_moved_or_deleted(tmp_path, monkeypatch, capsys, change, error):
    target, before = owned_update(tmp_path)
    real = Path.rename

    def rename(self, dest):
        result = real(self, dest)
        if Path(dest) == target and self.parent.name == ".mycelium-staging":  # actual publication took effect
            if change == "foreign-added":
                (target / "foreign.txt").write_text("not ours")
            else:
                real(target, tmp_path / "displaced-candidate")
                target.mkdir()
                (target / "foreign.txt").write_text("not ours")
            raise error
        return result
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(type(error)) as raised:
        package.install(target, python=Path(sys.executable))
    monkeypatch.undo()
    assert raised.value is error
    assert (target / "foreign.txt").read_text() == "not ours"  # unknown content stays where it was
    if change == "foreign-replaced":
        assert [p.name for p in target.iterdir()] == ["foreign.txt"]
    backups = [p for p in (tmp_path / ".mycelium-backups").iterdir() if p.is_dir()]
    assert [tree_identity(p) for p in backups] == [before]  # old generation intact for manual recovery
    [record] = (tmp_path / ".mycelium-backups").glob("recovery-*.json")
    assert str(target) in json.loads(record.read_text())["retained"]
    assert "unexpected" in capsys.readouterr().err
    assert not list((tmp_path / ".mycelium-staging").glob("rolled-back-*"))


def test_content_changed_after_move_aside_is_retained_not_deleted(tmp_path, monkeypatch):
    target, before = owned_update(tmp_path)
    real = Path.rename

    def rename(self, dest):
        dest = Path(dest)
        result = real(self, dest)
        if dest == target and self.parent.name == ".mycelium-staging":
            raise KeyboardInterrupt()
        if self == target and dest.name.startswith("rolled-back-"):
            (dest / "late-foreign.txt").write_text("appeared after the move")  # changed between check and cleanup
        return result
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        package.install(target, python=Path(sys.executable))
    monkeypatch.undo()
    assert tree_identity(target) == before and package.doctor(target)["ok"]  # previous installation restored
    [aside] = (tmp_path / ".mycelium-staging").glob("rolled-back-*")
    assert (aside / "late-foreign.txt").read_text() == "appeared after the move"
    [record] = (tmp_path / ".mycelium-backups").glob("recovery-*.json")
    assert str(aside) in json.loads(record.read_text())["retained"]


def test_extra_empty_directory_after_publication_is_unexpected_and_retained(tmp_path, monkeypatch):
    target, before = owned_update(tmp_path)
    real = Path.rename

    def rename(self, dest):
        result = real(self, dest)
        if Path(dest) == target and self.parent.name == ".mycelium-staging":
            (target / "owner-folder").mkdir()
            raise KeyboardInterrupt()
        return result
    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(KeyboardInterrupt):
        package.install(target, python=Path(sys.executable))
    monkeypatch.undo()
    assert (target / "owner-folder").is_dir()
    assert [tree_identity(p) for p in (tmp_path / ".mycelium-backups").iterdir() if p.is_dir()] == [before]
