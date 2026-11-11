"""Package identity must cover execution metadata and generation inputs."""
import json
from pathlib import Path
import shutil
import stat
import sys

import pytest

import antigravity as package

HOOK = 'skills/core/hooks/mycelium-data-lineage-stop.sh'


def snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in root.rglob('*') if p.is_file()}


def test_doctor_and_normal_install_reject_permission_drift(tmp_path):
    target = tmp_path / 'plugins/mycelium'
    package.install(target)
    (target / HOOK).chmod(0o644)
    before = snapshot(target)
    report = package.doctor(target)
    assert not report['ok']
    assert report['mode_changed'] == [HOOK]
    with pytest.raises(ValueError, match='modified package'):
        package.install(target)
    assert snapshot(target) == before


def test_explicit_permission_repair_preserves_backup_and_is_idempotent(tmp_path):
    target = tmp_path / 'plugins/mycelium'
    neighbor = tmp_path / 'plugins/other'
    neighbor.mkdir(parents=True)
    (neighbor / 'custom.txt').write_text('owner data')
    package.install(target)
    expected = snapshot(target)
    (target / HOOK).chmod(0o644)
    damaged = snapshot(target)
    dry = package.install(target, repair_permissions=True, dry_run=True)
    assert dry['dry_run'] and snapshot(target) == damaged
    report = package.install(target, repair_permissions=True)
    assert report['ok']
    assert snapshot(target) == expected
    assert snapshot(Path(report['backup'])) == damaged
    assert (neighbor / 'custom.txt').read_text() == 'owner data'
    assert 'backup' not in package.install(target, repair_permissions=True)
    assert snapshot(target) == expected


@pytest.mark.parametrize('damage', ['changed', 'extra', 'linked', 'malformed-modes'])
def test_permission_repair_never_bypasses_content_or_manifest_checks(tmp_path, damage):
    target = tmp_path / 'plugins/mycelium'
    package.install(target)
    (target / HOOK).chmod(0o644)
    if damage == 'changed':
        (target / HOOK).write_text('owner amendment\n')
    elif damage == 'extra':
        (target / 'user.txt').write_text('owner data')
    elif damage == 'linked':
        outside = tmp_path / 'outside.txt'
        outside.write_text('owner data')
        (target / 'link.txt').symlink_to(outside)
    else:
        receipt = target / package.OWNERSHIP
        manifest = json.loads(receipt.read_text())
        manifest['modes'] = {HOOK: True}
        receipt.write_text(json.dumps(manifest))
    before = snapshot(target)
    with pytest.raises(ValueError):
        package.install(target, repair_permissions=True)
    assert snapshot(target) == before


def test_legacy_manifest_requires_explicit_upgrade_and_preserves_backup(tmp_path):
    target = tmp_path / 'plugins/mycelium'
    package.install(target)
    receipt = target / package.OWNERSHIP
    manifest = json.loads(receipt.read_text())
    manifest['schema'] = 1
    manifest.pop('modes', None)
    receipt.write_text(json.dumps(manifest))
    before = snapshot(target)
    assert not package.doctor(target)['ok']
    with pytest.raises(ValueError):
        package.install(target)
    assert snapshot(target) == before
    report = package.install(target, repair_permissions=True)
    assert report['ok']
    assert json.loads(receipt.read_text())['schema'] == 2
    assert snapshot(Path(report['backup'])) == before


def manifest(source=package.SOURCE, python=Path(sys.executable)):
    return json.loads(package.candidate_files(source, python)[package.OWNERSHIP][0])


def test_development_identity_covers_effective_host_guidance(monkeypatch):
    before = manifest()
    monkeypatch.setattr(package, 'HOST_GUIDANCE', package.HOST_GUIDANCE + '\nAdditional native guidance.\n')
    after = manifest()
    assert before['files']['skills/core/SKILL.md'] != after['files']['skills/core/SKILL.md']
    assert before['version'] == after['version']
    assert before['source_commit'] == after['source_commit']
    assert before['source_digest'] != after['source_digest']


def test_source_identity_covers_generator_bytes_and_permissions(tmp_path):
    source = tmp_path / 'source'
    for tree in (*package.TREES, 'skillpacks', 'todo'):
        shutil.copytree(package.SOURCE / tree, source / tree,
                        ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'))
    (source / 'tools').mkdir()
    for name in ('LICENSE', 'README.md', 'CHANGELOG.md', 'tools/antigravity.py'):
        shutil.copy2(package.SOURCE / name, source / name)
    before = manifest(source)
    generator = source / 'tools/antigravity.py'
    generator.write_text(generator.read_text() + '\n# Build recipe changed.\n')
    recipe = manifest(source)
    assert before['source_digest'] != recipe['source_digest']
    hook = source / HOOK
    hook.chmod(stat.S_IMODE(hook.stat().st_mode) ^ 0o100)
    modes = manifest(source)
    assert modes['source_digest'] != recipe['source_digest']
    assert modes['modes'][HOOK] != recipe['modes'][HOOK]


def test_source_identity_does_not_depend_on_selected_python_path():
    first = manifest(python=Path('/first/venv/bin/python3'))
    second = manifest(python=Path('/second/venv/bin/python3'))
    assert first['files']['hooks.json'] != second['files']['hooks.json']
    assert first['source_digest'] == second['source_digest']
