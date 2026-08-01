#!/usr/bin/env python3
"""extract_data_lineage_event — produce NDJSON event(s) from a Bash command.

Invoked by mycelium-data-tracker.sh after each Bash tool call. Detects
analysis invocations (python/R/Rscript/jupyter/uv-run/poetry-run/conda-run,
including inline -c and -e), extracts script source, regex-scans for data
I/O and seeds, SHAs the script and the touched files AT EXECUTION TIME.

If the command isn't an analysis or its script is unreadable, exits 0 silently.
Otherwise emits one NDJSON line per detected script (so `python a.py && python
b.py` produces up to two events). Executions whose file paths are dynamic or
hidden behind imports are retained with `io_detection: "unresolved"` and an
explicit warning rather than being silently discarded.

With --append-to, lines are appended to the file under fcntl.flock(LOCK_EX)
to make parallel-tool appends safe (shell `>>` is only atomic up to PIPE_BUF;
embedded script source can exceed that). Without --append-to, lines go to
stdout (legacy mode, used by tests).

Usage (from the tracker hook):
    extract_data_lineage_event.py --cwd <session_cwd> --ts <iso8601> \\
        --bash-cmd <full bash command> \\
        [--bash-exit <status>] \\
        [--agent-id <id>] [--agent-type <type>] \\
        [--append-to <events.tmp path>]
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SIZE_LIMIT_BYTES = 100 * 1024 * 1024
EMBED_LIMIT_BYTES = 100 * 1024

# --- Script-path / inline-source extraction ---
_SHELL_DOUBLE_QUOTED_WORD = r'"(?:\\.|[^"\\])*"'
_SHELL_SINGLE_QUOTED_WORD = r"'[^']*'"
_SHELL_BARE_WORD = r"(?:\\.|[^\s|&;()\"'])+"
_SHELL_WORD = (
    rf"(?:{_SHELL_DOUBLE_QUOTED_WORD}|{_SHELL_SINGLE_QUOTED_WORD}|{_SHELL_BARE_WORD})"
)


def _shell_executable_pattern(name: str) -> str:
    """Return a regex fragment for a bare or wholly quoted executable path."""
    double_quoted = rf'"(?:(?:\\.|[^"\\])*/)?{name}"'
    single_quoted = rf"'(?:[^']*/)?{name}'"
    bare = rf"(?:(?:\\.|[^\s|&;()\"'])*/)?{name}"
    return rf"(?:{double_quoted}|{single_quoted}|{bare})"


def _shell_path_pattern(extension: str) -> str:
    """Return a regex fragment for one shell word ending in ``extension``."""
    double_quoted = rf'"(?:\\.|[^"\\])*{extension}"'
    single_quoted = rf"'[^']*{extension}'"
    bare = rf"(?:\\.|[^\s|&;()\"'])+{extension}"
    return rf"(?:{double_quoted}|{single_quoted}|{bare})"


_PYTHON_EXE = _shell_executable_pattern(r"python(?:\d+(?:\.\d+)*)?")
_PYTHON_COMMAND = rf"(?<![A-Za-z0-9_.-]){_PYTHON_EXE}"
_PYTHON_FLAG = (
    r"(?:-[bBdEhiIOPqRsStuUvVx]+"
    rf"|-W\s+{_SHELL_WORD}"
    rf"|-W{_SHELL_WORD}"
    rf"|-X\s+{_SHELL_WORD}"
    rf"|-X{_SHELL_WORD}"
    r"|--"
    rf"|--check-hash-based-pycs(?:=|\s+){_SHELL_WORD}"
    r"|--(?:debug|inspect|interactive|isolated|optimize|dont-write-bytecode"
    r"|no-user-site|no-site|unbuffered|verbose|version|help))"
)
_PYTHON_FLAGS = rf"(?:{_PYTHON_FLAG}\s+)*"
_PYTHON_SCRIPT_PATH = _shell_path_pattern(r"\.py")
_R_EXE = _shell_executable_pattern("R")
_R_SCRIPT_EXE = _shell_executable_pattern("Rscript")
_R_SCRIPT_PATH = _shell_path_pattern(r"\.(?:R|r)")
_JUPYTER_EXE = _shell_executable_pattern("jupyter")
_JUPYTER_SCRIPT_PATH = _shell_path_pattern(r"\.ipynb")

RX_PYTHON_C = re.compile(
    rf"{_PYTHON_COMMAND}\s+{_PYTHON_FLAGS}-c\s+(?P<quote>['\"])(?P<source>.+?)(?P=quote)",
    re.DOTALL,
)
RX_PYTHON_M = re.compile(
    rf"{_PYTHON_COMMAND}\s+{_PYTHON_FLAGS}-m\s+(?P<module>[A-Za-z_][A-Za-z0-9_.]*)"
)
RX_PYTHON_SCRIPT_PATH = re.compile(
    rf"{_PYTHON_COMMAND}\s+{_PYTHON_FLAGS}(?P<path>{_PYTHON_SCRIPT_PATH})"
)
RX_R_E = re.compile(
    rf"(?<![A-Za-z0-9_.-]){_R_EXE}\s+"
    rf"(?:--\S+\s+)*-e\s+(?P<quote>['\"])(?P<source>.+?)(?P=quote)",
    re.DOTALL,
)
RX_R_SCRIPT_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-]){_R_SCRIPT_EXE}\s+"
    rf"(?:--\S+\s+)*(?P<path>{_R_SCRIPT_PATH})"
)
RX_JUPYTER_SCRIPT_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-]){_JUPYTER_EXE}\s+"
    rf"(?:nbconvert|execute)\s+(?:--\S+\s+)*(?P<path>{_JUPYTER_SCRIPT_PATH})"
)
IGNORED_PYTHON_MODULES = {
    "black",
    "compileall",
    "cProfile",
    "doctest",
    "ensurepip",
    "flake8",
    "http.server",
    "isort",
    "json.tool",
    "mypy",
    "pdb",
    "pip",
    "pydoc",
    "pyright",
    "pytest",
    "ruff",
    "site",
    "tarfile",
    "timeit",
    "unittest",
    "venv",
    "zipfile",
}
IGNORED_SCRIPT_SUFFIXES = {
    "skills/core/scripts/validate_structure.py",
}
IGNORED_SCRIPT_BASENAMES = {"setup.py"}
ANALYSIS_PATTERNS = (
    RX_PYTHON_C,
    RX_PYTHON_M,
    RX_PYTHON_SCRIPT_PATH,
    RX_R_E,
    RX_R_SCRIPT_PATH,
    RX_JUPYTER_SCRIPT_PATH,
)

# The hook receives a shell command string and one overall exit status, not an
# execution trace. AND/OR lists can be reasoned about conservatively, but shell
# compound commands and heredocs cannot: an interpreter token may live in a
# skipped branch, a zero-iteration loop, a function body, or heredoc content.
# Reject the whole command in those cases rather than fabricate provenance.
_CONTROL_BOUNDARY = r"(?:^|&&|\|\||[;|\n])"
RX_UNSUPPORTED_SHELL_STRUCTURE = re.compile(
    rf"(?m){_CONTROL_BOUNDARY}[ \t]*"
    r"(?:if|then|elif|else|fi|for|while|until|do|done|case|esac|select|function|coproc)\b"
    rf"|{_CONTROL_BOUNDARY}[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*\([ \t]*\)[ \t]*\{{"
    rf"|{_CONTROL_BOUNDARY}[ \t]*\{{"
    r"|<<-?"
)


def _unquoted_shell_text(command: str) -> str | None:
    """Mask quoted arguments while preserving unquoted shell structure.

    The returned text has the same length as ``command`` so boundaries remain
    intact, but quoted language source cannot be mistaken for shell syntax.
    Return ``None`` for an unterminated quote so callers can fail closed.
    """
    masked: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote is None:
            if char == "\\":
                masked.append(" ")
                if index + 1 < len(command):
                    masked.append(" ")
                    index += 2
                    continue
                return None
            elif char in {"'", '"'}:
                quote = char
                masked.append(" ")
                index += 1
                continue
            else:
                masked.append(char)
                index += 1
                continue
        elif quote == "'":
            masked.append(" ")
            if char == "'":
                quote = None
            index += 1
            continue
        else:
            masked.append(" ")
            if char == "\\" and index + 1 < len(command):
                masked.append(" ")
                index += 2
                continue
            if char == '"':
                quote = None
            index += 1
            continue

    return None if quote is not None else "".join(masked)

# --- Data I/O source-scanning regexes ---
INPUT_REGEXES = [
    re.compile(
        r"""pd\.read_(?:parquet|csv|tsv|feather|hdf|h5|json|excel|stata|sas|orc|pickle|table)\s*\(\s*["']([^"']+)["']"""
    ),
    re.compile(r"""ad\.read_h5ad\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""ad\.read_csv\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""np\.load\s*\(\s*["']([^"']+)["']"""),
    re.compile(
        r"""xr\.open_(?:dataset|dataarray|zarr|mfdataset)\s*\(\s*["']([^"']+)["']"""
    ),
    re.compile(
        r"""sc\.read(?:_h5ad|_csv|_mtx|_10x_h5|_10x_mtx)?\s*\(\s*["']([^"']+)["']"""
    ),
]
OUTPUT_REGEXES = [
    re.compile(
        r"""\.to_(?:parquet|csv|tsv|feather|hdf|h5|json|excel|stata|sas|orc|pickle|table)\s*\(\s*["']([^"']+)["']"""
    ),
    re.compile(r"""\.write_(?:csv|parquet|json|h5ad)\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""np\.save(?:_compressed|z)?\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""(?:plt|fig|ax)\.savefig\s*\(\s*["']([^"']+)["']"""),
    re.compile(r"""\.to_netcdf\s*\(\s*["']([^"']+)["']"""),
]
FILTER_REGEXES = [
    re.compile(r"""(\.query\s*\(\s*["'][^"']*["']\s*\))"""),
    re.compile(r"""(\.sample\s*\([^)]*\))"""),
    re.compile(r"""(\.filter\s*\([^)]*\))"""),
    # Boolean-mask subset: `df[df.col CMP val]` or `df[df['col'] CMP val]`,
    # with optional leading ~ for negation. Conservative — single bracket
    # depth only, won't capture deeply-nested boolean algebra.
    re.compile(r"""(\w+\s*\[\s*~?\w+(?:\.\w+|\[\s*["'][^"']+["']\s*\])[^\]]*\])"""),
    # .loc[...] / .iloc[...] — capture the full bracket contents. The bracket
    # may include a column selector after a comma (e.g., .loc[mask, 'col']) —
    # the manifest preserves all of it so replicators see the exact slice.
    re.compile(r"""(\.[il]oc\[[^\]]+\])"""),
    # Joins / merges / concat as subset-like operations (replicators care
    # which other table got merged in, with what keys).
    re.compile(r"""(\.merge\s*\([^)]*\))"""),
    re.compile(r"""(\.join\s*\([^)]*\))"""),
    re.compile(r"""(pd\.concat\s*\([^)]*\))"""),
]
SEED_REGEXES = [
    re.compile(
        r"""(?:np\.random\.seed|random\.seed|torch\.manual_seed)\s*\(\s*(\d+)\s*\)"""
    ),
    re.compile(r"""np\.random\.default_rng\s*\(\s*(\d+)\s*\)"""),
]


def sha256_file(p: Path) -> str | None:
    try:
        size = p.stat().st_size
        if size > SIZE_LIMIT_BYTES:
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def is_analysis(bash_cmd: str) -> bool:
    return any(p.search(bash_cmd) for p in ANALYSIS_PATTERNS)


def detect_script(bash_cmd: str, cwd: Path) -> tuple[Path | None, str | None]:
    """Return (script_path, inline_source). At most one is non-None.

    Kept for compatibility — returns only the FIRST detection. Use
    detect_scripts() for `&&`-chained commands with multiple scripts.
    """
    detections = detect_scripts(bash_cmd, cwd)
    return detections[0] if detections else (None, None)


def _strip_assignments(segment: list[str]) -> list[str]:
    """Remove leading POSIX-style environment assignments from a command."""
    segment = list(segment)
    while segment and ("=" in segment[0] and not segment[0].startswith(("./", "/"))):
        name, _, _ = segment[0].partition("=")
        if not name.replace("_", "a").isalnum() or name[:1].isdigit():
            break
        segment = segment[1:]
    return segment


def _simple_cd_status(segment: list[str], cwd: Path) -> tuple[bool | None, Path]:
    """Return a simple cd's known status and resulting cwd without executing it."""
    segment = _strip_assignments(segment)
    if not segment or segment[0] != "cd":
        return None, cwd
    arguments = [value for value in segment[1:] if value != "--"]
    if len(arguments) != 1 or arguments[0] == "-" or "$" in arguments[0]:
        return None, cwd
    target = Path(os.path.expanduser(arguments[0]))
    if not target.is_absolute():
        target = cwd / target
    target = Path(os.path.abspath(target))
    # The hook sees the command only after the shell ran. Do not propagate a
    # directory change that the shell could not have made: in
    # ``cd missing || python a.py`` the Python command runs from the old cwd.
    if not target.is_dir() or not os.access(target, os.X_OK):
        return False, cwd
    return True, target


def _apply_cd(segment: list[str], cwd: Path) -> Path:
    """Apply a simple shell ``cd`` segment without executing user input."""
    status, target = _simple_cd_status(segment, cwd)
    return target if status is True else cwd


def _shell_tokens(fragment: str) -> list[str] | None:
    """Tokenize shell control punctuation while preserving quoted content."""
    try:
        lexer = shlex.shlex(fragment, posix=True, punctuation_chars=";&|()\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw_tokens = list(lexer)
    except ValueError:
        return None

    tokens: list[str] = []
    punctuation = set(";&|()\n")
    for token in raw_tokens:
        if token and set(token) <= punctuation:
            index = 0
            while index < len(token):
                pair = token[index : index + 2]
                if pair in {"&&", "||"}:
                    tokens.append(pair)
                    index += 2
                else:
                    tokens.append(token[index])
                    index += 1
        else:
            tokens.append(token)
    return tokens


def _effective_cwd(bash_cmd: str, offset: int, initial_cwd: Path) -> Path:
    """Resolve preceding top-level ``cd`` commands for a command match."""
    tokens = _shell_tokens(bash_cmd[:offset])
    if tokens is None:
        return initial_cwd

    cwd = initial_cwd
    subshell_states: list[tuple[Path, bool, bool]] = []
    segment: list[str] = []
    segment_is_conditional = False
    chain_has_or = False
    for token in tokens:
        if token in {"&&", "||"}:
            if not segment_is_conditional:
                # The first segment in an AND-OR list always executes.
                cwd = _apply_cd(segment, cwd)
            elif token == "&&" and not chain_has_or:
                # Reaching the matched command at the end of an uninterrupted
                # AND chain proves that each earlier segment in that chain ran
                # successfully. A mixed OR chain is ambiguous: for
                # ``true || cd sub && python a.py``, Python runs while cd does
                # not, so retain the prior cwd in that case.
                cwd = _apply_cd(segment, cwd)
            segment = []
            segment_is_conditional = True
            if token == "||":
                chain_has_or = True
        elif token in {";", "\n"}:
            if not segment_is_conditional:
                cwd = _apply_cd(segment, cwd)
            segment = []
            segment_is_conditional = False
            chain_has_or = False
        elif token in {"|", "&"}:
            # A cd in a pipeline/subshell does not reliably affect the parent.
            segment = []
        elif token == "(":
            segment = []
            subshell_states.append((cwd, segment_is_conditional, chain_has_or))
        elif token == ")":
            segment = []
            if subshell_states:
                cwd, segment_is_conditional, chain_has_or = subshell_states.pop()
        else:
            segment.append(token)
    return cwd


def _top_level_control_context(fragment: str) -> tuple[list[str], bool, bool]:
    """Return current-list operators, whether a later list exists, and ambiguity."""
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return [], False, True

    depth = 0
    operators: list[str] = []
    later_list = False
    current_list_ambiguous = False
    malformed = False
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            if depth == 0:
                malformed = True
            else:
                depth -= 1
        elif depth == 0 and token in {";", "\n"}:
            operators = []
            later_list = True
            # A completed list is an execution boundary. Unsupported control
            # operators in an earlier list cannot make the next list's first
            # command conditional or uncertain.
            current_list_ambiguous = False
        elif depth == 0 and token in {"&&", "||"}:
            operators.append(token)
        elif depth == 0 and token in {"|", "&"}:
            current_list_ambiguous = True
    if depth:
        malformed = True
    return operators, later_list, current_list_ambiguous or malformed


def _current_command_prefix(fragment: str) -> list[str] | None:
    """Return tokens before a candidate in its current simple command."""
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return None

    depth = 0
    segment: list[str] = []
    for token in tokens:
        if token == "(":
            depth += 1
            segment.append(token)
        elif token == ")":
            if depth == 0:
                return None
            depth -= 1
            segment.append(token)
        elif depth == 0 and token in {";", "\n", "&&", "||", "|", "&"}:
            segment = []
        else:
            segment.append(token)
    if depth:
        return None
    return segment


def _match_is_command_invocation(bash_cmd: str, offset: int) -> bool:
    """Reject interpreter text that is an argument, comment, or quoted body."""
    prefix = _current_command_prefix(bash_cmd[:offset])
    if prefix is None:
        return False
    while prefix and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", prefix[0]):
        prefix.pop(0)
    if not prefix or prefix == ["command"]:
        return True
    if Path(prefix[0]).name == "env":
        # `env` may contain options and assignments before the executable, but
        # another ordinary command token means Python/R is merely an argument.
        ordinary = [
            token
            for token in prefix[1:]
            if not token.startswith("-")
            and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token)
        ]
        return not ordinary
    if len(prefix) < 2 or prefix[1] != "run":
        return False
    wrapper = prefix[0]
    if wrapper not in {"conda", "uv", "poetry"}:
        return False

    value_options = {
        "conda": {"-n", "--name", "-p", "--prefix", "--cwd"},
        "uv": {
            "--project",
            "--directory",
            "--python",
            "--with",
            "--with-editable",
            "--with-requirements",
            "--env-file",
        },
        "poetry": set(),
    }[wrapper]
    arguments = prefix[2:]
    index = 0
    while index < len(arguments):
        token = arguments[index]
        if token in {"-h", "--help", "-V", "--version"}:
            return False
        if token == "--":
            return index == len(arguments) - 1
        if token in value_options:
            index += 2
            if index > len(arguments):
                return False
            continue
        if any(token.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        # A non-option token before Python/R is another executable, so the
        # interpreter is merely one of its arguments.
        return False
    return True


def _simple_command_status(segment: list[str], cwd: Path) -> bool | None:
    """Return a statically knowable simple-command status, if available."""
    segment = _strip_assignments(segment)
    if segment[:1] == ["command"]:
        segment = segment[1:]
    if not segment:
        return None
    name = Path(segment[0]).name
    if len(segment) == 1 and name == "false":
        return False
    if len(segment) == 1 and name in {"true", ":"}:
        return True
    status, _ = _simple_cd_status(segment, cwd)
    return status


def _or_prefix_is_proven_failed(fragment: str, initial_cwd: Path) -> bool:
    """Prove that every alternative before a pure-OR candidate failed."""
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return False

    cwd = initial_cwd
    segment: list[str] = []
    alternatives: list[list[str]] = []
    operators: list[str] = []
    depth = 0

    def finish_completed_list() -> bool:
        nonlocal cwd, segment, alternatives, operators
        parts = alternatives + [segment]
        if len(parts) == 1 and not operators:
            status, target = _simple_cd_status(parts[0], cwd)
            if status is True:
                cwd = target
        elif any(
            _strip_assignments(part)[:1]
            and Path(_strip_assignments(part)[0]).name
            in {"cd", "pushd", "popd", "source", ".", "eval"}
            for part in parts
        ):
            # A completed conditional list may have changed the parent shell's
            # cwd, but the one overall status supplied to this hook cannot say
            # which branch ran. Refuse to infer a later relative fallback.
            return False
        segment = []
        alternatives = []
        operators = []
        return True

    for token in tokens:
        if token == "(":
            depth += 1
            if depth == 1:
                return False
        elif token == ")":
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and token in {";", "\n"}:
            if not finish_completed_list():
                return False
        elif depth == 0 and token in {"&&", "||", "|", "&"}:
            alternatives.append(segment)
            operators.append(token)
            segment = []
        else:
            segment.append(token)
    if depth or any(operator != "||" for operator in operators):
        return False

    # ``segment`` is the wrapper/assignment prefix of the candidate itself;
    # only the completed alternatives before its final || determine whether it
    # ran. Each must have a statically known failure status.
    if not alternatives:
        return False
    return all(_simple_command_status(part, cwd) is False for part in alternatives)


def _match_is_proven_executed(
    bash_cmd: str, offset: int, bash_exit: int | None, initial_cwd: Path
) -> bool:
    """Conservatively decide whether a textual script match actually ran."""
    unquoted_shell = _unquoted_shell_text(bash_cmd)
    if unquoted_shell is None or RX_UNSUPPORTED_SHELL_STRUCTURE.search(
        unquoted_shell
    ):
        return False
    if not _match_is_command_invocation(bash_cmd, offset):
        return False
    before_ops, _, before_ambiguous = _top_level_control_context(bash_cmd[:offset])
    if before_ambiguous:
        return False
    if not before_ops:
        # The first command in an AND-OR list always executes.
        return True
    if bash_exit is None:
        return False

    after_ops, has_later_list, after_ambiguous = _top_level_control_context(
        bash_cmd[offset:]
    )
    if has_later_list or after_ambiguous:
        # The overall exit status belongs to a later list or an unsupported
        # compound construct, so it cannot prove this conditional branch ran.
        return False
    all_ops = before_ops + after_ops
    if all(operator == "&&" for operator in all_ops):
        return bash_exit == 0
    if all(operator == "||" for operator in all_ops):
        # A nonzero final status proves every OR alternative ran and failed.
        # A successful list is normally ambiguous, but a final fallback is
        # still proven to run when every preceding alternative has a known
        # failure (notably ``cd missing || python a.py``).
        return bash_exit != 0 or _or_prefix_is_proven_failed(
            bash_cmd[:offset], initial_cwd
        )
    return False


def _detect_scripts_with_cwd(
    bash_cmd: str,
    cwd: Path,
    *,
    bash_exit: int | None = None,
    require_execution_evidence: bool = False,
    include_python_inline: bool = True,
) -> list[tuple[Path | None, str | None, Path]]:
    out: list[tuple[Path | None, str | None, Path]] = []
    seen_paths: set[Path] = set()
    seen_inline: set[tuple[str, Path]] = set()
    for m in RX_PYTHON_C.finditer(bash_cmd):
        if not include_python_inline:
            continue
        if require_execution_evidence and not _match_is_proven_executed(
            bash_cmd, m.start(), bash_exit, cwd
        ):
            continue
        src = m.group("source")
        effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
        identity = (src, effective_cwd)
        if identity not in seen_inline:
            out.append((None, src, effective_cwd))
            seen_inline.add(identity)
    for m in RX_R_E.finditer(bash_cmd):
        if require_execution_evidence and not _match_is_proven_executed(
            bash_cmd, m.start(), bash_exit, cwd
        ):
            continue
        src = m.group("source")
        effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
        identity = (src, effective_cwd)
        if identity not in seen_inline:
            out.append((None, src, effective_cwd))
            seen_inline.add(identity)
    for m in RX_PYTHON_M.finditer(bash_cmd):
        if require_execution_evidence and not _match_is_proven_executed(
            bash_cmd, m.start(), bash_exit, cwd
        ):
            continue
        module = m.group("module")
        if module in IGNORED_PYTHON_MODULES:
            continue
        effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
        source = f"# Python module execution: {module}"
        identity = (source, effective_cwd)
        if identity not in seen_inline:
            out.append((None, source, effective_cwd))
            seen_inline.add(identity)
    for pattern in (
        RX_PYTHON_SCRIPT_PATH,
        RX_R_SCRIPT_PATH,
        RX_JUPYTER_SCRIPT_PATH,
    ):
        for m in pattern.finditer(bash_cmd):
            if require_execution_evidence and not _match_is_proven_executed(
                bash_cmd, m.start(), bash_exit, cwd
            ):
                continue
            effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
            try:
                path_words = shlex.split(m.group("path"), comments=False, posix=True)
            except ValueError:
                continue
            if len(path_words) != 1:
                continue
            raw_path = path_words[0]
            if Path(raw_path).name in IGNORED_SCRIPT_BASENAMES or any(
                raw_path.endswith(suffix) for suffix in IGNORED_SCRIPT_SUFFIXES
            ):
                continue
            path = Path(raw_path)
            if not path.is_absolute():
                path = effective_cwd / path
            path = Path(os.path.abspath(path))
            if path not in seen_paths:
                out.append((path, None, effective_cwd))
                seen_paths.add(path)
    return out


def detect_scripts(bash_cmd: str, cwd: Path) -> list[tuple[Path | None, str | None]]:
    """Return every (script_path, inline_source) detection in the command.

    A `python a.py && python b.py` chain yields two tuples; mixed inline and
    file-based forms work too. Each tuple has exactly one non-None field.
    Deduped by identity (same path or same inline source appears once).
    """
    return [
        (script_path, inline_source)
        for script_path, inline_source, _ in _detect_scripts_with_cwd(bash_cmd, cwd)
    ]


def _dedupe(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in seq:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def scan_source(source: str) -> tuple[list[str], list[str], list[str], list[int]]:
    inputs: list[str] = []
    outputs: list[str] = []
    filters: list[str] = []
    seeds: list[int] = []
    for rx in INPUT_REGEXES:
        inputs.extend(m.group(1) for m in rx.finditer(source))
    for rx in OUTPUT_REGEXES:
        outputs.extend(m.group(1) for m in rx.finditer(source))
    for rx in FILTER_REGEXES:
        filters.extend(m.group(1) for m in rx.finditer(source))
    for rx in SEED_REGEXES:
        for m in rx.finditer(source):
            try:
                seeds.append(int(m.group(1)))
            except ValueError:
                pass
    return _dedupe(inputs), _dedupe(outputs), _dedupe(filters), sorted(set(seeds))


def file_record(path_str: str, cwd: Path) -> dict:
    path = Path(path_str)
    if not path.is_absolute():
        path = cwd / path
    rec: dict = {"path": str(path)}
    if not path.exists():
        rec["_missing"] = True
        return rec
    try:
        rec["size_bytes"] = path.stat().st_size
    except OSError:
        pass
    rec["sha256"] = sha256_file(path)
    return rec


def get_git_sha(cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def build_event_for_detection(
    detection: tuple[Path | None, str | None],
    args: argparse.Namespace,
    cwd: Path,
    git_sha: str | None,
) -> dict | None:
    """Build one NDJSON event dict for a single (script_path, inline) detection.

    Returns None only if the script is unreadable or the detection is empty.
    """
    script_path, inline_source = detection
    source = ""
    script_sha: str | None = None
    script_source_embed: str | None = None

    if script_path:
        if not script_path.exists():
            return None
        try:
            source = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        script_sha = sha256_file(script_path)
        if len(source.encode("utf-8")) < EMBED_LIMIT_BYTES:
            script_source_embed = source
    elif inline_source:
        source = inline_source
        script_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        script_source_embed = source
    else:
        return None

    inputs, outputs, filters, seeds = scan_source(source)
    io_detection = "static" if inputs or outputs else "unresolved"
    lineage_warnings = []
    if io_detection == "unresolved":
        lineage_warnings.append(
            "No literal input/output paths were detected; the script may "
            "resolve paths dynamically or delegate I/O through imports."
        )

    return {
        "ts": args.ts,
        "agent_id": args.agent_id or None,
        "agent_type": args.agent_type or None,
        "bash_cmd": args.bash_cmd,
        "bash_exit": getattr(args, "bash_exit", None),
        "bash_wall_s": None,
        "script": str(script_path) if script_path else None,
        "script_sha256": script_sha,
        "script_source": script_source_embed,
        "git_sha": git_sha,
        "inputs": [file_record(p, cwd) for p in inputs],
        "outputs": [file_record(p, cwd) for p in outputs],
        "io_detection": io_detection,
        "lineage_warnings": lineage_warnings,
        "filters_detected": filters,
        "seeds_detected": seeds,
    }


def write_events(lines: list[str], append_to: Path | None) -> None:
    """Emit NDJSON lines. With --append-to, append under fcntl.flock(LOCK_EX)
    so parallel-tool invocations can't interleave large lines."""
    if not lines:
        return
    payload = "".join(lines)
    if append_to is None:
        sys.stdout.write(payload)
        return
    append_to.parent.mkdir(parents=True, exist_ok=True)
    with append_to.open("ab") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(payload.encode("utf-8"))
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--ts", required=True)
    ap.add_argument("--bash-cmd", required=True)
    ap.add_argument("--agent-id", default=None)
    ap.add_argument("--agent-type", default=None)
    ap.add_argument("--bash-exit", type=int, default=None)
    check_group = ap.add_mutually_exclusive_group()
    check_group.add_argument(
        "--check-execution",
        action="store_true",
        help="Exit 0 only when a supported analysis invocation is proven to run.",
    )
    check_group.add_argument(
        "--check-post-action",
        action="store_true",
        help=(
            "Exit 0 only for a proven analysis that should open a Mycelium "
            "bookkeeping cycle; excludes Python inline, tooling modules, and "
            "managed utility scripts."
        ),
    )
    ap.add_argument(
        "--append-to",
        type=Path,
        default=None,
        help="Append NDJSON to this file under fcntl.flock. Default: stdout.",
    )
    args = ap.parse_args()

    if not is_analysis(args.bash_cmd):
        return 1 if args.check_execution or args.check_post_action else 0

    cwd = Path(args.cwd)
    detections = _detect_scripts_with_cwd(
        args.bash_cmd,
        cwd,
        bash_exit=args.bash_exit,
        require_execution_evidence=True,
        include_python_inline=not args.check_post_action,
    )
    if args.check_execution or args.check_post_action:
        return 0 if detections else 1
    if not detections:
        return 0

    # git_sha is the same for every detection in this command — compute once.
    git_sha = get_git_sha(cwd)

    lines: list[str] = []
    for script_path, inline_source, effective_cwd in detections:
        event = build_event_for_detection(
            (script_path, inline_source), args, effective_cwd, git_sha
        )
        if event is not None:
            lines.append(json.dumps(event) + "\n")

    write_events(lines, args.append_to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
