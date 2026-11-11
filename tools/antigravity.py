#!/usr/bin/env python3
"""Build, inspect, update, or remove the base Mycelium Antigravity IDE or App package.

Owned trees are replaced as a unit. Backups and staging stay outside the
plugin discovery directory. User modifications require explicit reconciliation.
The IDE profile is the default. The App profile is workspace-local, carries its
own owner, and never updates or removes a package owned by the other profile.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid

SOURCE = Path(__file__).resolve().parents[1]
OWNERSHIP = ".mycelium-install.json"
TREES = ("skills", "network", "docs", "hooks", ".claude-plugin", ".codex-plugin")
HOST_GUIDANCE = """\n## Antigravity IDE host adapter

Use this skill with the model currently selected in Antigravity IDE. Native
`run_command`, `view_file`, and file-edit tools replace host-specific shell,
read, and edit names in examples. For repository files (including .living and
.mycelium), omit ArtifactMetadata from write_to_file and edit calls; that field
designates an IDE artifact, not an ordinary workspace file.
Use general coding subagents only when their
native tool definitions are available. `browser_subagent` cannot substitute
for a coding/scientific reviewer or a filesystem lifecycle probe. If general
coding subagents are unavailable, perform every required review axis in-line;
do not search the repository for host capabilities.
Mycelium's scientific requirements and output formats still apply. References
to other Mycelium skills mean load that named skill through native discovery.
Do not invoke an external agent CLI to execute this skill. For knowledge indexing use
`generate_index.py --summary-heuristic`; concept proposals support the offline
default. Improve labels or summaries with the current IDE model and the normal
validation/acceptance workflow when needed. CLI-specific host audits remain
audits of those named products; they do not certify Antigravity IDE.
\n"""
APP_GUIDANCE = """\n## Antigravity App host adapter

Use this skill with the model currently selected in the Antigravity App.
Native `read_file`, `run_command`, `write_file` and `edit_file` replace
host-specific read, shell and edit names in examples. Run commands synchronously in the App sandbox
without `BypassSandbox`. Mycelium records a command's output provenance only
when the App's own step record proves its working directory inside this
project, its sandboxed run and its exit status. Any other command leaves the
work cycle unfinalized; an asynchronous command stays pending until the App
records its result. Only successful native `write_file` creations or
overwrites and `edit_file` edits are tracked as edits; other edit tools are not.
If an edit callback is missed, a normal Stop recovers this request's verified
edits from the App's step record once, before finalizing. Mycelium's scientific
requirements and output formats still apply. References to other Mycelium
skills mean load that named skill through native discovery. Do not invoke an
external agent CLI to execute this skill. For knowledge indexing use
`generate_index.py --summary-heuristic`; concept proposals support the offline
default. IDE, Claude Code, and Codex audits do not certify the Antigravity App.
\n"""


def profile_spec(profile):
    """Packaging contract for one host profile (read at call time)."""
    if profile == "ide":
        return {"owner": "mycelium-antigravity-ide", "host": "antigravity-ide", "guidance": HOST_GUIDANCE,
                "matcher": "run_command|view_file|write_to_file|replace_file_content|multi_replace_file_content",
                "host_argument": ""}
    if profile == "app":
        return {"owner": "mycelium-antigravity-app", "host": "antigravity-app", "guidance": APP_GUIDANCE,
                "matcher": "run_command|read_file|write_file|edit_file", "host_argument": "--host antigravity-app "}
    raise ValueError("Unknown Antigravity package profile")


def app_destination(destination):
    """An App package lives only at <workspace>/.agents/plugins/<name>."""
    target = safe_path(destination)
    if (target.parent.name != "plugins" or target.parent.parent.name != ".agents" or
            target.name in {"", ".", ".."} or not target.parents[2].is_dir()):
        raise ValueError("The App package destination must be <workspace>/.agents/plugins/<name>")
    return target


def safe_path(path):
    path = Path(path).expanduser().absolute()
    if ".." in path.parts:
        raise ValueError("Managed paths must not contain traversal")
    for node in reversed((path, *path.parents)):
        if node.is_symlink():
            raise ValueError(f"Refusing symlinked installation path: {node}")
        if node.exists() and node != path and not node.is_dir():
            raise ValueError(f"Non-directory installation ancestor: {node}")
    return path


def file_map(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in {"__pycache__", ".pytest_cache"} for part in relative.parts) or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"Linked package resource: {relative}")
        if path.is_dir():
            continue
        if not stat.S_ISREG(path.stat().st_mode):
            raise ValueError(f"Non-regular package resource: {relative}")
        if relative.as_posix() != OWNERSHIP:
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def workspace_readiness(workspace, profile="ide"):
    """Check the adapter's Git-root/.living opt-in without creating state."""
    path = safe_path(workspace)
    result = {"ok": False, "path": str(path), "lifecycle_opt_in": False}
    if not path.is_dir():
        return dict(result, reason="Workspace directory does not exist")
    git = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True, timeout=5)
    if git.returncode:
        return dict(result, reason="Workspace is not inside a Git repository")
    root = safe_path(Path(git.stdout.strip()))
    living = safe_path(root / ".living")
    result["git_root"] = str(root)
    if not living.is_dir():
        return dict(result, reason="Git root has no .living opt-in")
    return dict(result, ok=True, lifecycle_opt_in=True,
                note="Workspace opt-in only; native dispatch still requires a fresh "
                     f"{'App' if profile == 'app' else 'IDE'} audit")


def validate_ownership_manifest(manifest, *, allow_legacy=False, profile="ide"):
    """Validate retained ownership fields without trusting paths or mode types."""
    if not isinstance(manifest, dict) or manifest.get("owner") != profile_spec(profile)["owner"]:
        raise ValueError("Invalid installation ownership manifest")
    schema = manifest.get("schema")
    # The App profile has no earlier package generation to adopt.
    legacy = (1, 2) if allow_legacy and profile == "ide" else (2,)
    if type(schema) is not int or schema not in legacy:
        raise ValueError("Unsupported installation ownership schema")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Invalid installation file manifest")
    for name, digest in files.items():
        if (not isinstance(name, str) or not name or "\x00" in name or Path(name).is_absolute() or
                any(part in {"", ".", ".."} for part in name.split("/")) or
                name == OWNERSHIP or not isinstance(digest, str) or
                not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Invalid installation file identity")
    python = manifest.get("python")
    if (not isinstance(python, str) or not Path(python).is_absolute() or
            ".." in Path(python).parts or "\x00" in python):
        raise ValueError("Invalid installation interpreter")
    modes = manifest.get("modes")
    if schema == 2 and (not isinstance(modes, dict) or set(modes) != set(files) or
                       any(type(mode) is not int or not 0 <= mode <= 0o7777 for mode in modes.values())):
        raise ValueError("Invalid installation permission manifest")


def doctor(destination, *, workspace=None, profile="ide"):
    target = app_destination(destination) if profile == "app" else safe_path(destination)
    if not target.is_dir():
        return {"ok": False, "reason": "Package is not installed", "root": str(target)}
    receipt = target / OWNERSHIP
    safe_path(receipt)
    if not receipt.is_file():
        return {"ok": False, "reason": "No Mycelium ownership manifest", "root": str(target)}
    manifest = json.loads(receipt.read_text())
    validate_ownership_manifest(manifest, allow_legacy=True, profile=profile)
    schema = manifest.get("schema")
    actual, expected = file_map(target), manifest["files"]
    modes = manifest.get("modes")
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    changed = sorted(k for k in actual.keys() & expected.keys() if actual[k] != expected[k])
    mode_changed = sorted(k for k in actual.keys() & expected.keys()
                          if schema == 2 and stat.S_IMODE((target / k).stat().st_mode) != modes[k])
    content_ok = not (missing or extra or changed)
    report = {"ok": content_ok and schema == 2 and not mode_changed, "root": str(target),
            "content_ok": content_ok, "mode_changed": mode_changed,
            "mode_verification": "verified" if schema == 2 else "unavailable in legacy schema 1",
            "repair_permissions_available": content_ok and (schema == 1 or bool(mode_changed)),
            "version": manifest.get("version"), "source_commit": manifest.get("source_commit"),
            "source_digest": manifest.get("source_digest"), "python": manifest.get("python"),
            "skills": len(list((target / "skills").glob("*/SKILL.md"))),
            "files_compared": len(expected), "missing": missing, "changed": changed, "unowned": extra,
            "live_validation": "Package identity only; requires a separate natural-dispatch receipt"}
    if profile != "ide":
        report["profile"] = profile
    if workspace is not None:
        report["workspace"] = workspace_readiness(workspace, profile)
        report["ok"] = report["ok"] and report["workspace"]["ok"]
    return report


def require_owned(target, *, repair_permissions=False, profile="ide"):
    report = doctor(target, profile=profile)
    if not report["ok"] and not (repair_permissions and report.get("content_ok")):
        raise ValueError("Refusing to replace/remove an unowned or modified package: " + json.dumps(report))
    return report


def candidate_files(source, python, profile="ide"):
    spec = profile_spec(profile)
    source = safe_path(source)
    # The full source set is preflighted and read before a destination changes.
    files = {}
    for tree in TREES:
        root = source / tree
        safe_path(root)
        if not root.is_dir():
            raise ValueError(f"Missing base Mycelium resource tree: {tree}")
        for relative in file_map(root):
            path = root / relative
            files[f"{tree}/{relative}"] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    for relative in ("LICENSE", "README.md", "CHANGELOG.md", "skillpacks/README.md", "todo/TODO_ITEM_TEMPLATE.md"):
        path = source / relative
        safe_path(path)
        files[relative] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
    version = json.loads((source / ".claude-plugin/plugin.json").read_text())["version"]
    generator = safe_path(source / "tools/antigravity.py")
    if not generator.is_file():
        raise ValueError("Missing or non-regular Antigravity package generator")
    # Identity covers the recipe and source modes as well as original bytes.
    # Effective guidance also participates when a caller configures the module.
    # The selected machine-local interpreter stays separate in the manifest.
    identity = {"files": {k: {"sha256": hashlib.sha256(v[0]).hexdigest(), "mode": v[1]}
                          for k, v in sorted(files.items())},
                "generator_sha256": hashlib.sha256(generator.read_bytes()).hexdigest(),
                "host_guidance": spec["guidance"]}
    if profile != "ide":  # The IDE identity recipe is unchanged.
        identity["profile"] = profile
    original_digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    for name, (content, mode) in list(files.items()):
        if Path(name).name == "SKILL.md" and len(Path(name).parts) == 3:
            text = content.decode()
            _, frontmatter, body = text.split("---", 2)
            files[name] = (("---" + frontmatter + "---" + spec["guidance"] + body).encode(), mode)
    files["plugin.json"] = (json.dumps({"name": "mycelium", "version": version}).encode() + b"\n", 0o644)
    files[".antigravity-host"] = (spec["host"].encode() + b"\n", 0o644)
    handlers = {"enabled": True}
    for event in ("PreInvocation", "PostToolUse", "PostInvocation", "Stop"):
        # The native plugin cwd is the package root on both the IDE and the App.
        # The --host argument selects a profile; the adapter still requires the
        # exact native conversation root of that host before acting.
        command = ("PATH=" + shlex.quote(str(python.parent)) + ':"$PATH" ' + shlex.quote(str(python)) +
                   " skills/core/scripts/antigravity_hooks.py " + spec["host_argument"] + event)
        handler = {"type": "command", "command": command, "timeout": 45}
        handlers[event] = ([{"matcher": spec["matcher"], "hooks": [handler]}]
                           if event == "PostToolUse" else [handler])
    files["hooks.json"] = (json.dumps({"mycelium": handlers}, indent=2).encode() + b"\n", 0o644)
    commit = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
    manifest = {"owner": spec["owner"], "schema": 2, "version": version,
                "source_commit": commit.stdout.strip() if not commit.returncode else None,
                "source_digest": original_digest, "python": str(python),
                "files": {name: hashlib.sha256(value[0]).hexdigest() for name, value in sorted(files.items())},
                "modes": {name: value[1] for name, value in sorted(files.items())}}
    files[OWNERSHIP] = (json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n", 0o600)
    return files


def install(destination, *, python=Path(sys.executable), source=SOURCE, dry_run=False,
            repair_permissions=False, profile="ide"):
    profile_spec(profile)
    target = app_destination(destination) if profile == "app" else safe_path(destination)
    # Validate the executable's destination, but invoke the selected launcher:
    # a venv symlink's spelling determines sys.prefix and site-packages.
    python = Path(os.path.abspath(Path(python).expanduser()))
    safe_path(python.resolve(strict=True))
    # An explicit exit status, not assert: inherited PYTHONOPTIMIZE strips asserts.
    check = subprocess.run([str(python), "-c", "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"],
                           capture_output=True, timeout=5)
    if check.returncode:
        raise ValueError("Antigravity Mycelium requires Python 3.11 or newer")
    if target.exists():
        require_owned(target, repair_permissions=repair_permissions, profile=profile)
    staging_parent = safe_path(target.parent.parent / ".mycelium-staging")
    backup_parent = safe_path(target.parent.parent / ".mycelium-backups")
    if target.name in {"", ".", ".."} or (target.exists() and not target.is_dir()):
        raise ValueError("Installation target must be a plugin directory")
    files = candidate_files(source, python, profile)
    if dry_run:
        return {"dry_run": True, "target": str(target), "file_count": len(files)}
    if (target.exists() and (target / OWNERSHIP).read_bytes() == files[OWNERSHIP][0]
            and doctor(target, profile=profile)["ok"]):
        return doctor(target, profile=profile)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging_parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="candidate-", dir=staging_parent))
    backup = None
    retain = False
    try:
        for name, (content, mode) in files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(mode)
        if target.exists():
            require_owned(target, repair_permissions=repair_permissions, profile=profile)
            backup_parent.mkdir(parents=True, exist_ok=True)
            backup = backup_parent / f"{target.name}-{uuid.uuid4().hex}"
            target.rename(backup)
        stage.rename(target)
    except BaseException as error:
        # Either rename may already have taken effect when a catchable
        # interruption arrives; the filesystem, not a flag, says which.
        if backup is not None and backup.exists():
            try:
                restore_previous(target, stage, backup, staging_parent, files)
            except BaseException as rollback_error:
                retain = True
                report_failed_rollback(target, stage, backup, backup_parent, error, rollback_error)
        raise
    finally:
        if stage.exists() and not retain:
            shutil.rmtree(stage)
    result = doctor(target, profile=profile)
    if backup:
        result["backup"] = str(backup)
    return result


def is_candidate(root, files):
    """True only if root holds exactly the candidate files, bytes and modes."""
    try:
        if root.is_symlink() or not root.is_dir():
            return False
        expected_dirs = {parent.as_posix() for name in files for parent in Path(name).parents if parent != Path(".")}
        seen_files, seen_dirs = set(), set()
        for directory, dirs, names in os.walk(root, followlinks=False):
            for name in dirs + names:
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    seen_dirs.add(relative)
                elif (not stat.S_ISREG(info.st_mode) or relative not in files or
                      stat.S_IMODE(info.st_mode) != files[relative][1] or path.read_bytes() != files[relative][0]):
                    return False
                else:
                    seen_files.add(relative)
        return seen_files == set(files) and seen_dirs == expected_dirs
    except OSError:
        return False


def restore_previous(target, stage, backup, staging_parent, files):
    """Put the displaced owned generation back at the active path."""
    if os.path.lexists(target):
        # Stage absence alone does not prove the target is still our candidate.
        if stage.exists() or not is_candidate(target, files):
            raise OSError(f"Refusing to replace unexpected content at {target}")
        aside = staging_parent / f"rolled-back-{uuid.uuid4().hex}"
        target.rename(aside)
        backup.rename(target)
        if not is_candidate(aside, files):
            raise OSError(f"Previous installation restored; unexpected content retained at {aside}")
        shutil.rmtree(aside, ignore_errors=True)
    else:
        backup.rename(target)


def report_failed_rollback(target, stage, backup, backup_parent, error, rollback_error):
    retained = [str(p) for p in (backup, stage, target) if os.path.lexists(p)]
    retained += [str(p) for p in stage.parent.glob("rolled-back-*")]
    kept = backup if os.path.lexists(backup) else target
    message = (f"Mycelium installation rollback incomplete ({rollback_error!r}) after {error!r}; "
               f"the previous installation is at {kept}. Retained: {', '.join(retained)}")
    try:
        record = backup_parent / f"recovery-{uuid.uuid4().hex}.json"
        record.write_text(json.dumps({"target": str(target), "backup": str(backup), "stage": str(stage),
                                      "original_error": repr(error), "rollback_error": repr(rollback_error),
                                      "retained": retained}, indent=2) + "\n")
        message += f". Recovery record: {record}"
    except OSError as record_error:
        message += f". Recovery record could not be written: {record_error!r}"
    if hasattr(error, "add_note"):
        error.add_note(message)
    print(message, file=sys.stderr)


def uninstall(destination, *, dry_run=False, profile="ide"):
    target = app_destination(destination) if profile == "app" else safe_path(destination)
    require_owned(target, profile=profile)
    parent = safe_path(target.parent.parent / ".mycelium-backups")
    if dry_run:
        return {"dry_run": True, "target": str(target)}
    parent.mkdir(parents=True, exist_ok=True)
    backup = parent / f"{target.name}-{uuid.uuid4().hex}"
    target.rename(backup)
    return str(backup)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("install", "doctor", "uninstall"))
    parser.add_argument("--profile", choices=("ide", "app"), default="ide",
                        help="ide (default) or app; the App package is workspace-local and needs --destination")
    parser.add_argument("--destination", type=Path, default=None,
                        help="IDE default ~/.gemini/config/plugins/mycelium; App: <workspace>/.agents/plugins/mycelium")
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--workspace", type=Path,
                        help="With doctor, also verify Git-root and .living lifecycle opt-in")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-permissions", action="store_true",
                        help="With install, restore candidate modes or upgrade a byte-intact legacy package; retain a backup")
    args = parser.parse_args()
    if args.workspace is not None and args.operation != "doctor":
        parser.error("--workspace is a read-only doctor check; it does not initialize or install")
    if args.repair_permissions and args.operation != "install":
        parser.error("--repair-permissions is only valid with install")
    if args.destination is None:
        if args.profile == "app":
            parser.error("--profile app requires an explicit workspace-local --destination")
        args.destination = Path.home() / ".gemini/config/plugins/mycelium"
    try:
        if args.operation == "doctor":
            result = doctor(args.destination, workspace=args.workspace, profile=args.profile)
        elif args.operation == "uninstall":
            result = {"backup": uninstall(args.destination, dry_run=args.dry_run, profile=args.profile)}
        else:
            result = install(args.destination, python=args.python, dry_run=args.dry_run,
                             repair_permissions=args.repair_permissions, profile=args.profile)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok", True) else 1
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
