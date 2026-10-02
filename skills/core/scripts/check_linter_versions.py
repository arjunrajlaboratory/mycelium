"""check_linter_versions — report the installed scilintr / scitexlintr versions.

Mycelium runs whichever ``scilintr`` and ``scitexlintr`` the project
environment provides; neither is bundled or pinned. This preflight tells the
agent (and the user) what is installed, whether it meets the minimum a skill
needs, and whether a newer release exists on PyPI. It **never installs or
upgrades anything**: a linter upgrade can add rules and change gate results
mid-project, so the decision belongs to the user.

Usage::

    python3 check_linter_versions.py "scitexlintr>=0.2"
    python3 check_linter_versions.py scilintr
    python3 check_linter_versions.py --record analysis/<name>/reports/.manifest.json "scitexlintr>=0.2"
    python3 check_linter_versions.py --offline scilintr

Each argument is a package name with an optional ``>=X.Y`` minimum. The
installed version comes from ``<cli> --version``, falling back to the package
metadata of this interpreter (``scilintr`` has no ``--version`` flag).

Exit status: 0 when every package is installed and meets its minimum (an
available update is advice, not a failure); 1 when a package is missing,
below its minimum, or has a minimum but an undeterminable version. The PyPI
lookup is best effort: offline, ``--offline``, or any error reports
"latest unknown" and is never a failure.

``--record MANIFEST`` adds the checked versions to the report manifest's
``linters`` object (other content untouched) so the report states which
linter versions certified it. Nothing is recorded when the check fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

_SPEC_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:>=([0-9]+(?:\.[0-9]+)*))?$")
_VERSION_RE = re.compile(r"\b([0-9]+(?:\.[0-9]+)+)\b")
PYPI_URL = "https://pypi.org/pypi/{name}/json"


def parse_spec(spec: str) -> tuple[str, tuple[int, ...] | None]:
    """``"scitexlintr>=0.2"`` -> ``("scitexlintr", (0, 2))``. Only ``>=`` is supported."""
    match = _SPEC_RE.match(spec.strip())
    if not match:
        raise ValueError(f"unsupported package spec {spec!r}; use NAME or NAME>=X.Y")
    name, minimum = match.groups()
    return name, version_key(minimum) if minimum else None


def version_key(text: str | None) -> tuple[int, ...] | None:
    """Release segments as a comparable tuple, trailing zeros dropped.

    Only the leading release number counts (``0.3.0rc1`` -> ``(0, 3)``), so a
    pre-release compares as its release — close enough for a minimum check,
    and it never lets an unparseable suffix skip the check altogether."""
    match = re.match(r"\s*v?([0-9]+(?:\.[0-9]+)*)", text or "")
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def _fmt(key: tuple[int, ...]) -> str:
    return ".".join(str(p) for p in key)


def cli_version(executable: str) -> str | None:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = _VERSION_RE.search(result.stdout + result.stderr)
    return match.group(1) if match else None


_METADATA_SNIPPET = "import sys, importlib.metadata as m; print(m.version(sys.argv[1]))"


def _script_interpreter(executable: str) -> str | None:
    """The Python interpreter named by a console script's shebang, if any."""
    try:
        with open(executable, "rb") as handle:
            first = handle.readline(512).decode("utf-8", "replace")
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    words = first[2:].split()
    if words and Path(words[0]).name == "env" and len(words) > 1:
        words = [shutil.which(words[1]) or words[1]]
    if words and "python" in Path(words[0]).name:
        return words[0]
    return None


def metadata_version(name: str, executable: str | None = None) -> str | None:
    """The package version from the environment the CLI runs in.

    A console script's shebang names its own interpreter (a pipx or project
    venv), whose installed version can differ from this interpreter's."""
    interpreter = _script_interpreter(executable) if executable else None
    if interpreter and Path(interpreter).resolve() != Path(sys.executable).resolve():
        try:
            result = subprocess.run(
                [interpreter, "-c", _METADATA_SNIPPET, name],
                capture_output=True, text=True, timeout=20, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        text = result.stdout.strip()
        return text if result.returncode == 0 and version_key(text) else None
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def fetch_latest(name: str, timeout: float) -> str | None:
    """Latest release on PyPI, or None when the index cannot be reached."""
    try:
        with urllib.request.urlopen(PYPI_URL.format(name=name), timeout=timeout) as response:
            return json.load(response)["info"]["version"]
    except Exception:  # offline, proxy, HTTP error, malformed JSON: all "unknown"
        return None


@dataclass
class Result:
    name: str
    minimum: tuple[int, ...] | None
    installed: bool
    version: str | None
    latest: str | None
    problem: str | None = None

    @property
    def requirement(self) -> str:
        return f"{self.name}>={_fmt(self.minimum)}" if self.minimum else self.name

    @property
    def update_available(self) -> bool:
        current, newest = version_key(self.version), version_key(self.latest)
        return bool(current and newest and newest > current)


def check(spec: str, offline: bool, timeout: float) -> Result:
    name, minimum = parse_spec(spec)
    executable = shutil.which(name)
    version = (cli_version(executable) if executable else None) or metadata_version(name, executable)
    latest = None if offline else fetch_latest(name, timeout)
    result = Result(name, minimum, executable is not None, version, latest)
    if executable is None:
        # The skills run the command, so a package importable here but with no
        # command on PATH (a --user install, an inactive venv) is not usable.
        result.problem = "not installed" if version is None else "not on PATH"
    elif minimum and version_key(version) is None:
        result.problem = f"cannot determine the installed version; {_fmt(minimum)} or later is required"
    elif minimum and version_key(version) < minimum:
        result.problem = f"{version} is below the required {_fmt(minimum)}"
    return result


def describe(result: Result) -> list[str]:
    latest = f"latest {result.latest}" if result.latest else "latest unknown"
    if result.problem == "not installed":
        return [
            f"{result.name}: not installed.",
            f'  Install: python -m pip install "{result.requirement}"',
        ]
    if result.problem == "not on PATH":
        return [
            f"{result.name} {result.version} is installed for this Python, but the "
            f"`{result.name}` command is not on PATH.",
            "  Activate the environment that has it, or add its bin directory to PATH.",
        ]
    if result.problem:
        return [
            f"{result.name}: {result.problem} ({latest}).",
            f'  Upgrade: python -m pip install -U "{result.requirement}"',
        ]
    head = f"{result.name} {result.version} ({latest})" if result.version else f"{result.name}: installed (version unknown)"
    if result.update_available:
        return [
            f"{head}: update available: {result.latest}.",
            f'  Ask the user before upgrading (python -m pip install -U "{result.requirement}");',
            "  a newer linter can add rules, so upgrade between reports, not mid-report.",
        ]
    return [f"{head}: ok" if result.version else head]


def record(manifest_path: Path, results: list[Result]) -> None:
    target = manifest_path.resolve()  # write through a symlink, not over it
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{manifest_path} must contain a JSON object")
    linters = data.get("linters")
    if not isinstance(linters, dict):
        linters = {}
    for result in results:
        if result.version:
            linters[result.name] = result.version
    data["linters"] = linters
    mode = target.stat().st_mode & 0o7777
    fd, tmp = tempfile.mkstemp(prefix=".manifest.", dir=target.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.chmod(tmp, mode)  # mkstemp creates 0600; keep the manifest's mode
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_linter_versions",
        description="Report installed scilintr/scitexlintr versions; never installs.",
    )
    parser.add_argument("packages", nargs="+", help='package specs, e.g. "scitexlintr>=0.2" scilintr')
    parser.add_argument("--offline", action="store_true", help="skip the PyPI latest-release lookup")
    parser.add_argument("--timeout", type=float, default=5.0, help="PyPI lookup timeout in seconds")
    parser.add_argument("--record", type=Path, default=None, metavar="MANIFEST",
                        help="add the checked versions to this report manifest's 'linters' object")
    args = parser.parse_args(argv)

    try:
        results = [check(spec, args.offline, args.timeout) for spec in args.packages]
    except ValueError as exc:
        parser.error(str(exc))
    for result in results:
        print("\n".join(describe(result)))
    if any(result.problem for result in results):
        return 1
    if args.record is not None:
        try:
            record(args.record, results)
        except (OSError, ValueError) as exc:
            print(f"cannot record linter versions: {exc}")
            return 1
        print(f"Recorded linter versions in {args.record}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
