#!/usr/bin/env python3
"""Record which workflow tool, if any, a repository declares its analysis steps in.

``snakemake`` and ``dvc`` both run a step from a declaration of its inputs
and outputs, so what an analysis read and wrote is stated instead of
inferred. ``none`` keeps Mycelium's own inferred record. The choice lives in
``.living/workflow.json``, committed so collaborators share it. Installing
the tool is left to the agent; ``references/workflow-guide.md`` says how the
tool is used in a Mycelium repository.

Usage::

    python3 setup_workflow.py [--target-dir DIR] status   # exit 2: undecided, ask the user
    python3 setup_workflow.py [--target-dir DIR] choose snakemake|dvc|none

Exit status: 0 decided, 2 undecided, 3 not a Mycelium repo / bad file /
bad arguments / the choice could not be written. Only ``status`` returns 2.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

TOOLS = ("snakemake", "dvc", "none")
EXIT_UNDECIDED = 2
EXIT_ERROR = 3

# Files that show a tool is already in use, looked for at the root and one
# analysis level down (`analysis/<name>/`), never by walking the tree.
_MARKERS = {
    "snakemake": ("Snakefile", "snakefile", "workflow/Snakefile", "workflow/snakefile",
                  "analysis/*/workflow.smk", "analysis/*/Snakefile"),
    "dvc": ("dvc.yaml", "dvc.lock", ".dvc/config", "analysis/*/dvc.yaml"),
}


def fail(message: str) -> None:
    print(f"setup_workflow: {message}", file=sys.stderr)
    raise SystemExit(EXIT_ERROR)


def load(path: Path) -> str | None:
    """The recorded tool, or None when the repository never chose."""
    if path.is_symlink():
        fail(f"{path} is a symbolic link; replace it with a regular file")
    if not path.exists():
        return None
    try:
        tool = json.loads(path.read_text(encoding="utf-8"))["workflow"]["tool"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        fail(f"cannot read {path} ({exc!r}); fix or delete it, then choose again")
    if tool not in TOOLS:
        fail(f"{path} names an unknown tool {tool!r}; fix or delete it, then choose again")
    return tool


def tools_in_use(root: Path) -> list[str]:
    return [tool for tool, patterns in _MARKERS.items()
            if any(p.is_file() for pattern in patterns for p in root.glob(pattern))]


def write(path: Path, tool: str) -> None:
    """Replace the file atomically: a failed write leaves the old choice and
    no temporary file, and an existing file keeps its permissions."""
    tmp = None
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".workflow.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"workflow": {"tool": tool}}, indent=2) + "\n")
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError as exc:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        fail(f"cannot write {path} ({exc!r}); the previous choice, if any, is unchanged")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # usage errors must not look like "undecided"
        self.print_usage(sys.stderr)
        fail(message)


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(prog="setup_workflow", description=__doc__.split("\n\n")[0])
    parser.add_argument("--target-dir", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("choose").add_argument("tool", choices=TOOLS)
    args = parser.parse_args(argv)

    living = args.target_dir / ".living"
    if living.is_symlink():
        fail(f"{living} is a symbolic link; the choice is only written inside the repository")
    if not living.is_dir():
        fail(f"{args.target_dir} is not a Mycelium repository (no .living/); pass --target-dir")
    path = living / "workflow.json"
    current = load(path)

    if args.command == "status":
        if current is None:
            print("Workflow: undecided. Ask the user how this repository should track analysis "
                  "steps: Snakemake (declared rules; standard in genomics, handles cluster runs), "
                  "DVC (declared stages with content hashes in a committed lock file), or neither "
                  "(Mycelium records what it can infer). Record the answer with "
                  "`setup_workflow.py choose snakemake`, `choose dvc`, or `choose none`, "
                  "then commit .living/workflow.json.")
            for tool in tools_in_use(args.target_dir):
                print(f"This repository already has {tool} files; propose {tool}.")
            return EXIT_UNDECIDED
        print(f"workflow: {current}")
        return 0

    write(path, args.tool)
    print(f"Recorded in {path}: workflow={args.tool}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
