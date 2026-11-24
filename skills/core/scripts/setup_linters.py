#!/usr/bin/env python3
"""setup_linters — choose and install Mycelium's scientific linters for a repo.

Mycelium's analyze and report workflows lint with packages from the
scilintr repository (https://github.com/arjunrajlaboratory/scilintr):

- ``scilintr``     Python analysis-code linter (PyPI)
- ``scitexlintr``  report linter for LaTeX/HTML reports (PyPI)
- ``scilintr-r``   the R ``scilintr`` package (CRAN), for R analysis code

Whether a repository uses them is the user's choice, recorded once in
``.living/linters.json`` (committed, so collaborators share it). Until that
file exists the repository is *undecided*, and the agent asks the user—
recommending yes—at setup or on first use. Once the user opts in, missing
linters are installed without asking again. Upgrades are never done here: a
newer linter can add rules mid-project, so ``check_linter_versions.py``
reports them and the user decides.

Usage::

    python3 setup_linters.py status [--json]
    python3 setup_linters.py enable [NAME ...]    # no names: recommended set
    python3 setup_linters.py disable [NAME ...]   # no names: opt out of all
    python3 setup_linters.py install [--python PATH] [--dry-run]

Exit status: 0 when the choice is recorded and every enabled linter is usable;
1 when an enabled linter is missing or too old; 2 when the repository is
undecided (ask the user).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_linter_versions as clv  # noqa: E402

CONFIG_RELPATH = Path(".living") / "linters.json"
SCHEMA = 1
EXIT_MISSING = 1
EXIT_UNDECIDED = 2

# name -> (description, check_linter_versions spec or None for R)
LINTERS: dict[str, tuple[str, str | None]] = {
    "scilintr": ("Python analysis-code linter (PyPI)", "scilintr"),
    "scitexlintr": ("LaTeX/HTML report linter (PyPI)", "scitexlintr>=0.2"),
    "scilintr-r": ("R analysis-code linter (CRAN scilintr)", None),
}
R_INSTALL = 'install.packages("scilintr", repos = "https://cloud.r-project.org")'
R_VERSION = 'cat(as.character(packageVersion("scilintr")))'
SCRIPT = Path(__file__).resolve()


def run_command(cmd: list[str]) -> int:
    """Run an installer, streaming its output; return its exit status."""
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:
        print(f"  {cmd[0]}: {exc}")
        return 127


def rscript_available() -> bool:
    return shutil.which("Rscript") is not None


def r_package_version() -> str | None:
    rscript = shutil.which("Rscript")
    if rscript is None:
        return None
    try:
        result = subprocess.run(
            [rscript, "-e", R_VERSION], capture_output=True, text=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = result.stdout.strip()
    return text if result.returncode == 0 and clv.version_key(text) else None


def recommended() -> list[str]:
    names = ["scilintr", "scitexlintr"]
    if rscript_available():
        names.append("scilintr-r")
    return names


# --- configuration -----------------------------------------------------------


def fail(message: str) -> None:
    print(f"setup_linters: {message}", file=sys.stderr)
    raise SystemExit(EXIT_MISSING)


def config_path(target: Path) -> Path:
    living = target / ".living"
    if living.is_symlink() or not living.is_dir():
        fail(f"{target} is not a Mycelium repository (no real .living/ directory); initialize it first")
    path = target / CONFIG_RELPATH
    if path.is_symlink():
        fail(f"refusing to use {path}: it is a symlink")
    if path.exists() and not path.is_file():
        fail(f"{path} is not a regular file")
    return path


def load(path: Path) -> dict[str, bool] | None:
    """The recorded choice, or None when undecided."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"cannot read {path}: {exc}; fix or delete it, then choose again")
    linters = data.get("linters") if isinstance(data, dict) else None
    if not isinstance(linters, dict) or not all(
        isinstance(linters.get(name, False), bool) for name in LINTERS
    ):
        fail(f"{path} must be an object whose 'linters' maps linter names to true/false")
    return {name: linters.get(name, False) for name in LINTERS}


def save(path: Path, choice: dict[str, bool]) -> None:
    data = {
        "schema": SCHEMA,
        "linters": {name: bool(choice.get(name, False)) for name in LINTERS},
        "decided": datetime.now(timezone.utc).date().isoformat(),
        "about": "Mycelium scientific linters for this repository; "
        "manage with skills/core/scripts/setup_linters.py",
    }
    if path.exists():
        mode = path.stat().st_mode & 0o7777
    else:
        umask = os.umask(0)
        os.umask(umask)
        mode = 0o666 & ~umask
    fd, tmp = tempfile.mkstemp(prefix=".linters.", dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


# --- status ------------------------------------------------------------------


def probe(name: str) -> tuple[str | None, str | None]:
    """(installed version, problem) for one linter; no network."""
    spec = LINTERS[name][1]
    if spec is None:
        version = r_package_version()
        return version, None if version else "not installed"
    result = clv.check(spec, offline=True, timeout=0)
    return result.version, result.problem


def gather(choice: dict[str, bool] | None) -> dict[str, dict]:
    rows = {}
    for name in LINTERS:
        enabled = None if choice is None else choice[name]
        version, problem = probe(name) if enabled is not False else (None, None)
        rows[name] = {"enabled": enabled, "version": version, "problem": problem}
    return rows


def cmd(*parts: str) -> str:
    return " ".join(["python3", str(SCRIPT), *parts])


def status(target: Path, as_json: bool) -> int:
    choice = load(config_path(target))
    rows = gather(choice)
    missing = [n for n, r in rows.items() if r["enabled"] and r["problem"]]
    code = EXIT_UNDECIDED if choice is None else (EXIT_MISSING if missing else 0)
    if as_json:
        print(json.dumps({"decided": choice is not None, "recommended": recommended(), "linters": rows}, indent=2))
        return code
    for name, row in rows.items():
        state = {None: "undecided", True: "enabled", False: "disabled"}[row["enabled"]]
        detail = ""
        if row["enabled"] is not False:
            detail = f"; {row['version']} installed" if row["version"] else "; not installed"
            if row["problem"] and row["version"]:
                detail += f" ({row['problem']})"
        print(f"{name}: {state}{detail} — {LINTERS[name][0]}")
    if choice is None:
        print(
            "\nThis repository has not chosen whether to use Mycelium's scientific linters.\n"
            "Ask the user once, recommending yes (they catch silent scientific choices in\n"
            f"analysis code and drift in reports). Recommended set: {', '.join(recommended())}.\n"
            f"  Yes: {cmd('enable')} && {cmd('install')}\n"
            f"  No:  {cmd('disable')}"
        )
    elif missing:
        print(f"\nEnabled but not usable: {', '.join(missing)}. Run: {cmd('install')}")
    return code


# --- install -----------------------------------------------------------------


def python_installers(name: str, python: str | None) -> list[list[str]]:
    """Install commands to try in order for a PyPI linter."""
    if python:
        return [[python, "-m", "pip", "install", name]]
    venv = os.environ.get("VIRTUAL_ENV")
    if venv:
        venv_python = Path(venv) / "bin" / "python"
        if venv_python.exists():
            return [[str(venv_python), "-m", "pip", "install", name]]
    options = []
    if pipx := shutil.which("pipx"):
        options.append([pipx, "install", name])
    if uv := shutil.which("uv"):
        options.append([uv, "tool", "install", name])
    options.append([sys.executable, "-m", "pip", "install", name])
    return options


def installers(name: str, python: str | None) -> list[list[str]]:
    if LINTERS[name][1] is None:
        rscript = shutil.which("Rscript")
        return [[rscript, "-e", R_INSTALL]] if rscript else []
    return python_installers(name, python)


def install(target: Path, python: str | None, dry_run: bool) -> int:
    choice = load(config_path(target))
    if choice is None:
        print("The linter choice is undecided; ask the user first.")
        status(target, as_json=False)
        return EXIT_UNDECIDED
    code = 0
    for name in (n for n in LINTERS if choice[n]):
        version, problem = probe(name)
        if problem is None:
            print(f"{name}: {version} already installed")
            continue
        if problem != "not installed":
            # Too old, or installed somewhere the command is not on PATH:
            # reinstalling or upgrading is the user's call.
            print(f"{name}: {problem}. Ask the user before upgrading or reinstalling;")
            print("  check_linter_versions.py prints the command.")
            code = EXIT_MISSING
            continue
        options = installers(name, python)
        if not options:
            print(f"{name}: could not install {name}: Rscript is not on PATH (install R first)")
            code = EXIT_MISSING
            continue
        if dry_run:
            first = [Path(options[0][0]).name, *options[0][1:]]
            print(f"{name}: would run: {' '.join(first)}")
            continue
        for option in options:
            print(f"{name}: running {' '.join(option)}", flush=True)
            if run_command(option) == 0:
                break
        version, problem = probe(name)
        if problem:
            print(f"{name}: could not install {name} ({problem}). Tried:")
            for option in options:
                print(f"  {' '.join(option)}")
            code = EXIT_MISSING
        else:
            print(f"{name}: {version} installed")
    return code


# --- choice ------------------------------------------------------------------


def choose(target: Path, names: list[str], enable: bool) -> int:
    path = config_path(target)
    unknown = [n for n in names if n not in LINTERS]
    if unknown:
        fail(f"unknown linter(s): {', '.join(unknown)}; choose from {', '.join(LINTERS)}")
    current = load(path)
    if current is None:
        current = {name: False for name in LINTERS}
    if not names:
        selected = recommended() if enable else list(LINTERS)
        if enable:
            current = {name: False for name in LINTERS}
    else:
        selected = names
    for name in selected:
        current[name] = enable
    save(path, current)
    on = [n for n, v in current.items() if v]
    print(f"Recorded in {CONFIG_RELPATH}: enabled {', '.join(on) if on else 'none'}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="setup_linters", description=__doc__.split("\n\n")[0])
    parser.add_argument("--target-dir", type=Path, default=Path.cwd(), help="repository root (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_status = sub.add_parser("status", help="show the recorded choice and what is installed")
    p_status.add_argument("--json", action="store_true")
    p_enable = sub.add_parser("enable", help="opt in (no names: the recommended set)")
    p_enable.add_argument("names", nargs="*", metavar="NAME")
    p_disable = sub.add_parser("disable", help="opt out (no names: all linters)")
    p_disable.add_argument("names", nargs="*", metavar="NAME")
    p_install = sub.add_parser("install", help="install enabled linters that are missing")
    p_install.add_argument("--python", help="install with this interpreter's pip instead of pipx/uv")
    p_install.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    target = args.target_dir.resolve()
    if args.command == "status":
        return status(target, args.json)
    if args.command == "install":
        return install(target, args.python, args.dry_run)
    return choose(target, args.names, enable=args.command == "enable")


if __name__ == "__main__":
    sys.exit(main())
