#!/usr/bin/env python3
"""Record whether a repository uses Mycelium's scientific linters.

The linters come from https://github.com/arjunrajlaboratory/scilintr:
``scilintr`` (Python analysis code), ``scitexlintr`` (reports) and the CRAN
``scilintr`` (R analysis code, named ``scilintr-r`` here). The choice lives in
``.living/linters.json``, committed so collaborators share it. Installing is
left to the agent: ``check_linter_versions.py`` prints the install command.

Usage::

    python3 setup_linters.py [--target-dir DIR] status            # exit 2: undecided, ask the user
    python3 setup_linters.py [--target-dir DIR] enable [NAME ...]  # no names: all
    python3 setup_linters.py [--target-dir DIR] disable [NAME ...]

Exit status: 0 decided, 2 undecided, 3 not a Mycelium repo / bad file / unknown name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LINTERS = ("scilintr", "scitexlintr", "scilintr-r")
EXIT_UNDECIDED = 2
EXIT_ERROR = 3


def fail(message: str) -> None:
    print(f"setup_linters: {message}", file=sys.stderr)
    raise SystemExit(EXIT_ERROR)


def load(path: Path) -> dict[str, bool] | None:
    if not path.exists():
        return None
    try:
        linters = json.loads(path.read_text(encoding="utf-8"))["linters"]
        return {name: bool(linters.get(name, False)) for name in LINTERS}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        fail(f"cannot read {path} ({exc!r}); fix or delete it, then choose again")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="setup_linters", description=__doc__.split("\n\n")[0])
    parser.add_argument("--target-dir", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=("status", "enable", "disable"))
    parser.add_argument("names", nargs="*", metavar="NAME")
    args = parser.parse_args(argv)

    living = args.target_dir / ".living"
    if not living.is_dir():
        fail(f"{args.target_dir} is not a Mycelium repository (no .living/); pass --target-dir")
    path = living / "linters.json"
    current = load(path)

    if args.command == "status":
        if current is None:
            print("Linters: undecided. Ask the user whether to use Mycelium's scientific linters "
                  "(scilintr, scitexlintr, R scilintr) in this repository, recommending yes; record "
                  "the answer with `setup_linters.py enable` or `setup_linters.py disable`.")
            return EXIT_UNDECIDED
        for name, on in current.items():
            print(f"{name}: {'enabled' if on else 'disabled'}")
        return 0

    unknown = [n for n in args.names if n not in LINTERS]
    if unknown:
        fail(f"unknown linter(s) {', '.join(unknown)}; choose from {', '.join(LINTERS)}")
    current = current or dict.fromkeys(LINTERS, False)
    for name in args.names or LINTERS:
        current[name] = args.command == "enable"
    path.write_text(json.dumps({"linters": current}, indent=2) + "\n", encoding="utf-8")
    print(f"Recorded in {path}: " + ", ".join(f"{n}={'on' if v else 'off'}" for n, v in current.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
