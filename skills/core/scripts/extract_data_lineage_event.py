#!/usr/bin/env python3
"""extract_data_lineage_event — produce NDJSON event(s) from a Bash command.

Invoked by mycelium-data-tracker.sh after each Bash tool call. Detects
analysis invocations (python/R/Rscript/jupyter/uv-run/poetry-run/conda-run,
including inline -c and -e) and pipeline runners (`bash`/`sh` scripts,
`./run.sh`, snakemake, sbatch, `nextflow run`), extracts script source, regex-scans for data
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
import ast
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

SIZE_LIMIT_BYTES = 100 * 1024 * 1024
EMBED_LIMIT_BYTES = 100 * 1024

# --- Script-path / inline-source extraction ---
_SHELL_DOUBLE_QUOTED_COMPONENT = r'"(?:\\.|[^"\\])*"'
_SHELL_SINGLE_QUOTED_COMPONENT = r"'[^']*'"
_SHELL_BARE_COMPONENT = r"(?:\\.|[^\s|&;()<>\"'])"
_SHELL_WORD_COMPONENT = (
    rf"(?:{_SHELL_DOUBLE_QUOTED_COMPONENT}|{_SHELL_SINGLE_QUOTED_COMPONENT}"
    rf"|{_SHELL_BARE_COMPONENT})"
)
# A POSIX shell word may concatenate adjacent quoted and unquoted components:
# ``"analysis script".py`` and ``'analysis'\ script.py`` are each one argv
# value. Model the whole word, not just one of its lexical components.
_SHELL_WORD = rf"(?:{_SHELL_WORD_COMPONENT})+"


def _shell_word_ending_pattern(ending: str) -> str:
    """Return a shell-word regex whose final component ends in ``ending``."""
    double_quoted = rf'"(?:\\.|[^"\\])*{ending}"'
    single_quoted = rf"'[^']*{ending}'"
    # Bare atoms are already represented by the prefix repetition. Keeping
    # another variable-length bare repetition here creates catastrophic
    # backtracking for ordinary escaped paths.
    bare = ending
    candidate = (
        rf"(?:{_SHELL_WORD_COMPONENT})*"
        rf"(?:{double_quoted}|{single_quoted}|{bare})"
    )
    # A suffix inside a quoted component is not necessarily the end of argv:
    # ``"analysis.py".bak`` is one shell word. Require a control/whitespace
    # boundary so the regex cannot hash or scan an argv fragment.
    return rf"{candidate}(?=$|[\s|&;()<>])"


def _shell_executable_pattern(name: str) -> str:
    """Return a shell-word candidate ending in an executable basename."""
    return _shell_word_ending_pattern(name)


def _shell_path_pattern(extension: str) -> str:
    """Return a regex fragment for one shell word ending in ``extension``."""
    return _shell_word_ending_pattern(extension)


_PYTHON_EXE_NAME = r"python(?:\d+(?:\.\d+)*)?"
_PYTHON_EXE = _shell_executable_pattern(_PYTHON_EXE_NAME)
_PYTHON_COMMAND = rf"(?<![A-Za-z0-9_.-])(?P<python_exe>{_PYTHON_EXE})"
_PYTHON_FLAG = (
    # -h/-V and --help/--version terminate startup without executing a later
    # payload. Excluding them also rejects short-option clusters containing a
    # terminal option (for example -uV and -Vu).
    r"(?:-[bBdEiIOPqRsStuUvx]+"
    rf"|-W\s+{_SHELL_WORD}"
    rf"|-W{_SHELL_WORD}"
    rf"|-X\s+{_SHELL_WORD}"
    rf"|-X{_SHELL_WORD}"
    r"|--"
    rf"|--check-hash-based-pycs(?:=|\s+){_SHELL_WORD}"
    r"|--(?:debug|inspect|interactive|isolated|optimize|dont-write-bytecode"
    r"|no-user-site|no-site|unbuffered|verbose))"
)
_PYTHON_FLAGS = rf"(?:{_PYTHON_FLAG}\s+)*"
_PYTHON_SCRIPT_PATH = _shell_path_pattern(r"\.py")
_R_EXE = _shell_executable_pattern("R")
_R_SCRIPT_EXE = _shell_executable_pattern("Rscript")
_R_SCRIPT_PATH = _shell_path_pattern(r"\.(?:R|r)")
_JUPYTER_EXE = _shell_executable_pattern("jupyter")
_JUPYTER_SCRIPT_PATH = _shell_path_pattern(r"\.ipynb")
# These interpreters accept many long startup options, but help/version forms
# stop before any later source path or expression. Keep the generic option
# support while excluding terminal forms and their value/help-all variants.
_NONTERMINAL_LONG_OPTION = r"--(?!(?:help(?:-all)?|version)(?:=|\s|$))\S+"
_NONTERMINAL_LONG_OPTIONS = rf"(?:{_NONTERMINAL_LONG_OPTION}\s+)*"
_JUPYTER_VALUE_OPTION_NAME = (
    r"(?:-o|--(?:config|format|log-level|nbformat|output|output-dir|post|"
    r"reveal-prefix|template|template-file|theme|to|writer))"
)
_JUPYTER_VALUE_OPTION = (
    rf"{_JUPYTER_VALUE_OPTION_NAME}(?:={_SHELL_WORD}|\s+{_SHELL_WORD})"
)
_JUPYTER_FLAG_OPTION = (
    r"(?:-y|--(?:allow-chromium-download|allow-errors|clear-output|"
    r"coalesce-streams|debug|disable-chromium-sandbox|embed-images|execute|"
    r"inplace|no-input|no-prompt|sanitize-html|show-input|stdin|stdout|yes))"
)
# Traitlets accepts arbitrary configurable options in --Class.name=value form.
# Unknown separated-value options are rejected conservatively because their
# arity is unknowable here and an .ipynb-looking value is not the input file.
_JUPYTER_ASSIGNED_OPTION = (
    rf"--(?!(?:help(?:-all)?|version)=)[^=\s]+={_SHELL_WORD}"
)
_JUPYTER_OPTIONS = (
    rf"(?:(?:{_JUPYTER_VALUE_OPTION}|{_JUPYTER_FLAG_OPTION}|"
    rf"{_JUPYTER_ASSIGNED_OPTION})\s+)*"
)
_INLINE_SOURCE_WORD = rf"(?P<source_word>{_SHELL_WORD})(?=$|[\s|&;()<>])"

RX_PYTHON_C = re.compile(
    rf"{_PYTHON_COMMAND}\s+{_PYTHON_FLAGS}-c\s+{_INLINE_SOURCE_WORD}",
    re.DOTALL,
)
RX_PYTHON_M = re.compile(
    rf"{_PYTHON_COMMAND}\s+{_PYTHON_FLAGS}-m\s+(?P<module>[A-Za-z_][A-Za-z0-9_.]*)"
)
RX_PYTHON_SCRIPT_PATH = re.compile(
    rf"{_PYTHON_COMMAND}\s+{_PYTHON_FLAGS}(?P<path>{_PYTHON_SCRIPT_PATH})"
)
RX_R_E = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<r_exe>{_R_EXE})\s+"
    rf"{_NONTERMINAL_LONG_OPTIONS}-e\s+{_INLINE_SOURCE_WORD}",
    re.DOTALL,
)
RX_R_SCRIPT_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<rscript_exe>{_R_SCRIPT_EXE})\s+"
    rf"{_NONTERMINAL_LONG_OPTIONS}(?P<path>{_R_SCRIPT_PATH})"
)
RX_JUPYTER_SCRIPT_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<jupyter_exe>{_JUPYTER_EXE})\s+"
    rf"(?:nbconvert|execute)\s+{_JUPYTER_OPTIONS}"
    rf"(?P<path>{_JUPYTER_SCRIPT_PATH})"
)
# Workflow engines and shell wrappers run analysis the interpreter patterns
# never see: `bash run.sh`, `./run.sh`, `snakemake`, `sbatch`, `nextflow run`.
# These regexes only locate candidate command words; each candidate must pass
# the shared command-position and control-flow proofs, and its parsed argv
# must not select a non-executing mode (dry run, syntax check, help, lint).
_SHELL_RUNNER_NAMES = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
_PIPELINE_RUNNER_NAMES = frozenset({"snakemake", "sbatch", "nextflow"})
RX_RUNNER_COMMAND = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<runner_exe>"
    rf"{_shell_executable_pattern(r'(?:bash|sh|zsh|dash|ksh|snakemake|sbatch|nextflow)')})"
)
RX_SHELL_SCRIPT_COMMAND = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<runner_exe>{_shell_path_pattern(r'\.sh')})"
)
RUNNER_PATTERNS = (RX_RUNNER_COMMAND, RX_SHELL_SCRIPT_COMMAND)

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
# Mycelium control-plane helpers must never be classified as analysis: they
# would contaminate scientific lineage and re-open the post-action cycle that
# the lifecycle itself asked the agent to complete (issue #69). The registry
# is the union of a static inventory (kept complete by a regression test that
# walks the shipped tree) and an import-time scan of this module's own
# directory, so a future helper is excluded even if the inventory lags.
_MANAGED_SCRIPTS_DIR_SUFFIX = "skills/core/scripts/"
_STATIC_MANAGED_SCRIPT_RELPATHS = frozenset(
    {
        "crystallize_findings.py",
        "detect_recurrence.py",
        "extract_data_lineage.py",
        "extract_data_lineage_event.py",
        "finalize_handoff.py",
        "finalize_session_log.py",
        "generate_index.py",
        "init_knowledge.py",
        "init_repo.py",
        "install_convention.py",
        "migrate_existing_repos.py",
        "mycelium_locks.py",
        "recall_lessons.py",
        "register_value.py",
        "render_report_values_tex.py",
        "session_file_changes.py",
        "upsert_registry_row.py",
        "upsert_table_row.py",
        "validate_review_report.py",
        "validate_structure.py",
        "knowledge_map/__main__.py",
        "knowledge_map/build_graph.py",
        "knowledge_map/build_vault.py",
        "knowledge_map/cli.py",
        "knowledge_map/concept_labeler.py",
        "knowledge_map/concept_registry.py",
        "knowledge_map/extract_entries.py",
        "knowledge_map/extract_logs.py",
        "knowledge_map/graph_model.py",
        "knowledge_map/link_entries.py",
        "knowledge_map/link_logs.py",
        "knowledge_map/propose_concepts.py",
        "knowledge_map/propose_model.py",
        "knowledge_map/render_views.py",
    }
)


def _shipped_managed_script_relpaths() -> frozenset[str]:
    """Every .py file shipped beside this module, as scripts-dir relpaths."""
    scripts_dir = Path(__file__).resolve().parent
    found: set[str] = set()
    try:
        for path in scripts_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            found.add(path.relative_to(scripts_dir).as_posix())
    except OSError:
        pass
    return frozenset(found)


IGNORED_SCRIPT_SUFFIXES = frozenset(
    _MANAGED_SCRIPTS_DIR_SUFFIX + relpath
    for relpath in _STATIC_MANAGED_SCRIPT_RELPATHS | _shipped_managed_script_relpaths()
)


# Path shape alone is not trusted identity: a user project may legitimately
# contain `skills/core/scripts/<colliding-name>.py`. A registry suffix match
# only suppresses bookkeeping when the derived candidate root proves it is a
# real Mycelium plugin tree — the root this extractor itself ships in, or a
# root carrying a Mycelium plugin manifest (versioned installs and source
# checkouts both ship these manifests).
_EXECUTING_PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PLUGIN_MANIFEST_RELPATHS = (
    ".claude-plugin/plugin.json",
    ".codex-plugin/plugin.json",
)
_PLUGIN_MANIFEST_SIZE_LIMIT_BYTES = 64 * 1024
# Project guidance instructs agents to reach bundled helpers through the
# Mycelium-written pointer file, e.g. "$(sed -n '1p' .mycelium/plugin-root)" or
# "$(cat .mycelium/plugin-root)". The substitution cannot be resolved
# statically, but a command that actually READS this project's pointer is by
# construction invoking the managed install. Only the documented reader forms
# with the exact pointer argument are trusted: a substitution that merely
# mentions the pointer path ("$(basename .mycelium/plugin-root)" expands to
# the plain directory name "plugin-root") proves nothing. The accessor must
# also anchor the ENTIRE shell word: with any preceding component
# ("foo/$(cat .mycelium/plugin-root)/…") the expansion resolves under the
# cwd as a user-controlled relative path, not under the managed install.
# Finally, the pointer itself is repository-controlled state, so the accessor
# is trusted only after dereferencing `<effective cwd>/.mycelium/plugin-root`
# and verifying that its target is a real Mycelium plugin root.
_PLUGIN_ROOT_ACCESSOR_WORD_RE = re.compile(
    r"^(?P<accessor>\$\((?P<reader>cat|sed[ \t]+-n[ \t]+['\"]?1p['\"]?)"
    r"[ \t]+\.mycelium/plugin-root\))/(?P<rel>.+)$"
)
_PLUGIN_ROOT_POINTER_SIZE_LIMIT_BYTES = 4096


def _pointer_expansion_value(effective_cwd: Path, reader: str) -> str | None:
    """Return the exact value a pointer-read substitution would produce.

    The pointer must be a non-symlink regular file inside a non-symlink
    `.mycelium` directory. Expansion is emulated exactly — no normalization,
    because the shell performs none: `cat` yields the whole content minus
    trailing newlines (embedded newlines mean the executed word is not a
    plain root, so fail closed), while `sed -n '1p'` yields exactly the
    first line, trailing spaces included. Only a nonblank absolute-path
    value is returned; anything else yields None.
    """
    state_dir = effective_cwd / ".mycelium"
    pointer = state_dir / "plugin-root"
    try:
        if state_dir.is_symlink() or not state_dir.is_dir():
            return None
        if pointer.is_symlink() or not pointer.is_file():
            return None
        if pointer.lstat().st_size > _PLUGIN_ROOT_POINTER_SIZE_LIMIT_BYTES:
            return None
        # read_bytes, not read_text: universal-newline decoding would hide a
        # CR that the shell preserves in the expanded value.
        content = pointer.read_bytes().decode("utf-8")
    except (OSError, ValueError):
        return None
    # Bash removes NUL bytes from command-substitution output (with a
    # warning), so the executed value never contains them; keeping one here
    # would instead crash path resolution and silently kill the hook.
    content = content.replace("\x00", "")
    if reader == "cat":
        value = content
        while value.endswith("\n"):
            value = value[:-1]
        if "\n" in value:
            return None
    else:
        value = content.split("\n", 1)[0]
    if not value or not value.startswith("/"):
        return None
    return value


def _accessor_expanded_script_path(
    raw_text: str, decoded_word: str, effective_cwd: Path
) -> Path | None:
    """Resolve a word-initial plugin-root accessor to its executed path.

    Returns the absolute path the shell would actually execute, or None when
    the word is not a well-formed, expansion-enabled accessor invocation.
    Only double-quoted spellings resolve: fully quoted, or quoted around the
    accessor component. Double quotes make the substitution expand without
    field splitting or pathname expansion, so the joined path below is
    byte-identical to the argv word bash builds. A bare (unquoted) accessor
    would field-split on IFS and glob — its executed argv is statically
    unknowable, so it is rejected here and, because the unquoted spaces also
    break the single-word shape upstream, never attributed at all. Single
    quotes and escapes make `$(` a literal path and likewise fall back to
    normal detection. Wrong-quoting fallbacks at worst cost a redundant
    bookkeeping reminder, never a suppression. This function only RESOLVES
    the path — whether the result is a managed utility is decided by the
    same `_is_managed_utility_path` gate as every directly written path, so
    there is exactly one trust decision.
    """
    match = _PLUGIN_ROOT_ACCESSOR_WORD_RE.match(decoded_word)
    if not match:
        return None
    accessor = match.group("accessor")
    expansion_enabled_spellings = (
        f'"{decoded_word}"',
        f'"{accessor}"{decoded_word[len(accessor):]}',
    )
    if raw_text not in expansion_enabled_spellings:
        return None
    reader = "cat" if match.group("reader") == "cat" else "sed"
    value = _pointer_expansion_value(effective_cwd, reader)
    if value is None:
        return None
    # Returned unnormalized: the caller derives both the recorded lexical
    # identity and the canonical (symlink-resolved) path judged by the gate.
    return Path(value) / match.group("rel")
_verified_plugin_roots: dict[str, bool] = {}


def _is_mycelium_plugin_root(root: Path) -> bool:
    key = root.as_posix()
    cached = _verified_plugin_roots.get(key)
    if cached is not None:
        return cached
    verdict = False
    try:
        if root.resolve() == _EXECUTING_PLUGIN_ROOT:
            verdict = True
    except OSError:
        pass
    if not verdict:
        for relpath in _PLUGIN_MANIFEST_RELPATHS:
            manifest = root / relpath
            try:
                if not manifest.is_file():
                    continue
                if manifest.stat().st_size > _PLUGIN_MANIFEST_SIZE_LIMIT_BYTES:
                    continue
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("name") == "mycelium":
                verdict = True
                break
    _verified_plugin_roots[key] = verdict
    return verdict


def _is_managed_utility_path(resolved: Path) -> bool:
    """True when an absolute script path is a verified bundled Mycelium helper.

    The `skills/core/scripts/<relpath>` suffix must start at a path-component
    boundary of the normalized absolute path, and the remaining prefix must be
    a verified Mycelium plugin root. Source checkouts, versioned plugin-cache
    installs, and relative invocations all verify; a same-named user script in
    a conventionally shaped project directory remains eligible analysis.
    """
    posix = resolved.as_posix()
    for suffix in IGNORED_SCRIPT_SUFFIXES:
        if not posix.endswith(suffix):
            continue
        prefix = posix[: -len(suffix)]
        if prefix and not prefix.endswith("/"):
            continue
        root = Path(prefix.rstrip("/") or "/")
        if _is_mycelium_plugin_root(root):
            return True
    return False


IGNORED_SCRIPT_BASENAMES = {"setup.py"}
ANALYSIS_PATTERNS = (
    RX_PYTHON_C,
    RX_PYTHON_M,
    RX_PYTHON_SCRIPT_PATH,
    RX_R_E,
    RX_R_SCRIPT_PATH,
    RX_JUPYTER_SCRIPT_PATH,
)

_PATTERN_EXECUTABLES = {
    RX_PYTHON_C: ("python_exe", re.compile(_PYTHON_EXE_NAME)),
    RX_PYTHON_M: ("python_exe", re.compile(_PYTHON_EXE_NAME)),
    RX_PYTHON_SCRIPT_PATH: ("python_exe", re.compile(_PYTHON_EXE_NAME)),
    RX_R_E: ("r_exe", re.compile(r"R")),
    RX_R_SCRIPT_PATH: ("rscript_exe", re.compile(r"Rscript")),
    RX_JUPYTER_SCRIPT_PATH: ("jupyter_exe", re.compile(r"jupyter")),
}


def _decode_shell_word(raw: str) -> str | None:
    """Decode exactly one POSIX shell word, failing closed on malformed input."""
    try:
        words = shlex.split(raw, comments=False, posix=True)
    except ValueError:
        return None
    return words[0] if len(words) == 1 else None


def _match_has_expected_executable(match: re.Match[str], pattern: re.Pattern) -> bool:
    """Validate a permissive raw shell-word candidate after quote decoding."""
    group, basename_pattern = _PATTERN_EXECUTABLES[pattern]
    word = _decode_shell_word(match.group(group))
    return word is not None and basename_pattern.fullmatch(Path(word).name) is not None

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


def _strip_unquoted_shell_comments(command: str) -> str | None:
    """Mask shell comments while preserving offsets and quoted/escaped hashes."""
    masked = list(command)
    quote: str | None = None
    at_word_start = True
    index = 0
    while index < len(command):
        char = command[index]
        if quote is None:
            if char == "\\":
                at_word_start = False
                index += 2
                continue
            if char in {"'", '"'}:
                quote = char
                at_word_start = False
                index += 1
                continue
            if char == "#" and at_word_start:
                while index < len(command) and command[index] != "\n":
                    masked[index] = " "
                    index += 1
                at_word_start = True
                continue
            at_word_start = char.isspace() or char in ";|&()"
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\" and index + 1 < len(command):
            index += 2
            continue
        if char == '"':
            quote = None
        index += 1
    return None if quote is not None else "".join(masked)


def _unquoted_shell_text(command: str) -> str | None:
    """Mask quoted arguments while preserving unquoted shell structure.

    The returned text has the same length as ``command`` so boundaries remain
    intact, but quoted language source cannot be mistaken for shell syntax.
    Return ``None`` for an unterminated quote so callers can fail closed.
    """
    command_without_comments = _strip_unquoted_shell_comments(command)
    if command_without_comments is None:
        return None
    masked: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command_without_comments):
        char = command_without_comments[index]
        if quote is None:
            if char == "\\":
                masked.append(" ")
                if index + 1 < len(command_without_comments):
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
            if char == "\\" and index + 1 < len(command_without_comments):
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
        r"""pd\.read_(?:parquet|csv|tsv|feather|hdf|h5|json|excel|stata|sas|orc|pickle|table)\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""
    ),
    re.compile(r"""ad\.read_h5ad\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
    re.compile(r"""ad\.read_csv\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
    re.compile(r"""np\.load\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
    re.compile(
        r"""xr\.open_(?:dataset|dataarray|zarr|mfdataset)\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""
    ),
    re.compile(
        r"""sc\.read(?:_h5ad|_csv|_mtx|_10x_h5|_10x_mtx)?\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""
    ),
]
OUTPUT_REGEXES = [
    re.compile(
        r"""\.to_(?:parquet|csv|tsv|feather|hdf|h5|json|excel|stata|sas|orc|pickle|table)\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""
    ),
    re.compile(r"""\.write_(?:csv|parquet|json|h5ad)\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
    re.compile(r"""np\.save(?:_compressed|z)?\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
    re.compile(r"""(?:plt|fig|ax)\.savefig\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
    re.compile(r"""\.to_netcdf\s*\(\s*["']([^"']+)["']\s*(?=[,)])"""),
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
    return any(
        _match_has_expected_executable(match, pattern)
        for pattern in ANALYSIS_PATTERNS
        for match in pattern.finditer(bash_cmd)
    ) or any(
        _runner_word(match) is not None
        for pattern in RUNNER_PATTERNS
        for match in pattern.finditer(bash_cmd)
    )


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
    """Return a simple cd's known status and resulting cwd without executing it.

    Documented flags are modeled (`cd [-L|[-P [-e]]] [--] dir`): ``-P``
    resolves the target physically through symlinks, ``-L`` (the default)
    resolves lexically, later flags override earlier ones, and ``-e`` only
    affects exit status in cases a known-good directory cannot hit. Any
    other option (``-@``, ``cd -``) stays unknown.
    """
    segment = _strip_assignments(segment)
    if not segment or segment[0] != "cd":
        return None, cwd
    physical = False
    options_done = False
    arguments: list[str] = []
    for value in segment[1:]:
        if not options_done and value == "--":
            options_done = True
            continue
        if not options_done and value.startswith("-") and value != "-":
            flags = value[1:]
            if not flags or set(flags) - {"L", "P", "e"}:
                return None, cwd
            for flag in flags:
                if flag == "P":
                    physical = True
                elif flag == "L":
                    physical = False
            continue
        arguments.append(value)
        # Options end at the first operand: bash treats a later dash word as
        # another operand (`cd nested -P` is "too many arguments" on current
        # bash), so it must land in `arguments` and force the unknown path.
        options_done = True
    if len(arguments) != 1 or arguments[0] == "-" or "$" in arguments[0]:
        return None, cwd
    target = Path(os.path.expanduser(arguments[0]))
    if not target.is_absolute():
        target = cwd / target
    if physical:
        resolved = _physical_cd_target(target)
        if resolved is None:
            return False, cwd
        target = resolved
    else:
        target = Path(os.path.abspath(target))
    # The hook sees the command only after the shell ran. Do not propagate a
    # directory change that the shell could not have made: in
    # ``cd missing || python a.py`` the Python command runs from the old cwd.
    if not target.is_dir() or not os.access(target, os.X_OK):
        return False, cwd
    return True, target


def _physical_cd_target(target: Path) -> Path | None:
    """Emulate bash `cd -P`: resolve each component physically, in order.

    Every component is resolved (symlinks included) before a following
    `..` is applied, so a missing intermediate or a regular file followed
    by `..` is a cd failure — never a lexical collapse. Implemented as an
    explicit walk because `os.path.realpath(strict=True)` only rejects the
    non-directory-before-`..` case on newer Python versions.
    """
    resolved = Path(target.anchor or "/")
    for part in target.parts[1:]:
        if part == ".":
            continue
        if part == "..":
            if not resolved.is_dir():
                return None
            resolved = resolved.parent
            continue
        try:
            resolved = Path(os.path.realpath(resolved / part, strict=True))
        except OSError:
            return None
    return resolved


def _apply_cd(segment: list[str], cwd: Path) -> Path:
    """Apply a simple shell ``cd`` segment without executing user input."""
    status, target = _simple_cd_status(segment, cwd)
    return target if status is True else cwd


def _shell_tokens(fragment: str) -> list[str] | None:
    """Tokenize shell control punctuation while preserving quoted content."""
    fragment = _strip_unquoted_shell_comments(fragment)
    if fragment is None:
        return None
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


_JUPYTER_TERMINAL_OPTIONS = {
    "--generate-config",
    "--help",
    "--help-all",
    "--show-config",
    "--show-config-json",
    "--version",
}

_SHELL_REDIRECTION_OPERATORS = {
    "<",
    "<<",
    "<<<",
    "<>",
    "<&",
    ">",
    ">>",
    ">&",
    ">|",
    "&>",
    "&>>",
}


def _simple_command_suffix(command: str, offset: int) -> str | None:
    """Return one simple command from offset, respecting shell quoting."""
    quote: str | None = None
    index = offset
    while index < len(command):
        char = command[index]
        if quote is None:
            if char == "\\":
                if index + 1 >= len(command):
                    return None
                index += 2
                continue
            if char in {"'", '"'}:
                quote = char
            elif char in ";\n)":
                break
            elif char == "(":
                # Process substitutions and other nested shell structures can
                # rejoin the same simple command after their closing paren.
                # Treat unsupported structure as terminal rather than missing
                # a later option that prevents notebook execution.
                return None
            elif char == "&":
                previous = command[index - 1] if index > offset else ""
                following = command[index + 1] if index + 1 < len(command) else ""
                if previous not in "<>" and following != ">":
                    break
            elif char == "|":
                if index == offset or command[index - 1] != ">":
                    break
        elif quote == "'":
            if char == "'":
                quote = None
        else:
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == '"':
                quote = None
        index += 1
    return None if quote is not None else command[offset:index]


_IO_NUMBER_RX = re.compile(r"\d+|\{[A-Za-z_][A-Za-z0-9_]*\}")


def _blank_io_numbers(fragment: str) -> str:
    """Blank redirection I/O numbers (``2>err``, ``{fd}>log``).

    In ``[n]>word`` the number belongs to the redirection only when it is a
    whole unquoted word immediately followed by the operator; ``cmd 2 >x``,
    ``cmd "2">x``, and ``cmd a2>x`` keep their argument. Offsets are kept so
    later shell scanning is unchanged.
    """
    out = list(fragment)
    quote: str | None = None
    word_start = True
    index = 0
    while index < len(fragment):
        char = fragment[index]
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                index += 2
                continue
            if char == '"':
                quote = None
            index += 1
            continue
        if char == "\\":
            word_start = False
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            word_start = False
            index += 1
            continue
        if char in " \t\r\n;&|()":
            word_start = True
            index += 1
            continue
        if char in "<>":
            # The next word is the redirection target, never an I/O number.
            word_start = False
            index += 1
            continue
        if word_start:
            match = _IO_NUMBER_RX.match(fragment, index)
            if match and fragment[match.end() : match.end() + 1] in {"<", ">"}:
                for position in range(match.start(), match.end()):
                    out[position] = " "
                index = match.end()
                continue
        word_start = False
        index += 1
    return "".join(out)


def _simple_command_argv(fragment: str) -> list[str] | None:
    """Tokenize argv while consuming shell redirection operators and targets."""
    fragment = _strip_unquoted_shell_comments(_blank_io_numbers(fragment))
    if fragment is None:
        return None
    try:
        lexer = shlex.shlex(
            fragment,
            posix=True,
            punctuation_chars=";&|()<>\n",
        )
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return None

    argv: list[str] = []
    consume_redirection_target = False
    punctuation = set(";&|()<>\n")
    for token in tokens:
        if consume_redirection_target:
            consume_redirection_target = False
        elif token in _SHELL_REDIRECTION_OPERATORS:
            consume_redirection_target = True
        elif token and set(token) <= punctuation:
            # The suffix scanner should already have bounded one simple
            # command. Any other control operator is unsupported structure.
            return None
        else:
            argv.append(token)
    if consume_redirection_target:
        return None
    return argv


def _jupyter_match_has_terminal_option(command: str, offset: int) -> bool:
    """Whether this Jupyter simple command exits before notebook execution."""
    fragment = _simple_command_suffix(command, offset)
    if fragment is None:
        return True
    tokens = _simple_command_argv(fragment)
    if tokens is None:
        return True
    return any(
        token.split("=", 1)[0] in _JUPYTER_TERMINAL_OPTIONS for token in tokens
    )


_CWD_AFFECTING_BUILTINS = {"pushd", "popd", "source", ".", "eval"}
_BUILTIN_LAUNCHER_PREFIXES = {"command", "builtin", "time"}
_REDIRECTION_TOKEN_RE = re.compile(r"^(?:\d+|&)?(?:>>|>\||<>|>|<)")


def _strip_redirection_prefix(
    tokens: list[str],
) -> tuple[list[str] | None, bool]:
    """Consume leading redirection words and their separated targets.

    A leading redirection leaves the following builtin running in the
    parent shell (`>log cd nested` still changes the cwd), so callers must
    classify what remains — and know that a redirection was present. A
    dangling operator with no target in this segment (the tokenizer splits
    `2>&1` at the `&`) makes the tail unknowable: None is returned and the
    caller must treat the segment as unmodeled.
    """
    index = 0
    stripped_any = False
    while index < len(tokens):
        match = _REDIRECTION_TOKEN_RE.match(tokens[index])
        if match is None:
            break
        stripped_any = True
        if len(tokens[index]) > match.end():
            index += 1  # target or fd attached to the operator word
        elif index + 1 < len(tokens):
            index += 2  # separated target word
        else:
            return None, True
    return tokens[index:], stripped_any


def _strip_command_prefix_words(
    tokens: list[str],
) -> tuple[list[str] | None, bool]:
    """Strip interleaved assignment and redirection words to a fixpoint."""
    stripped_any = False
    before: list[str] | None = None
    while tokens != before:
        before = tokens
        tokens = _strip_assignments(tokens)
        tokens, had_redirection = _strip_redirection_prefix(tokens)
        if tokens is None:
            return None, True
        stripped_any = stripped_any or had_redirection or tokens != before
    return tokens, stripped_any


def _segment_cwd_is_modeled(segment: list[str], cwd: Path) -> bool:
    """True when a completed segment cannot change the cwd in unmodeled ways.

    ``cd`` with a statically known status is modeled; other cwd-affecting
    builtins (and ``command``-prefixed forms of them) are not — a following
    trust decision must not assume the working directory it computed.
    """
    stripped = _strip_assignments(segment)
    if not stripped:
        return True
    stripped, had_redirection = _strip_redirection_prefix(stripped)
    if stripped is None:
        return False
    if not stripped:
        return True
    name = stripped[0]
    if had_redirection and name in (
        {"cd"} | _CWD_AFFECTING_BUILTINS | _BUILTIN_LAUNCHER_PREFIXES
    ):
        # `>log cd nested` still changes the cwd, but the cwd walker's
        # _apply_cd never tracks redirection-prefixed segments — unmodeled.
        return False
    if name in _CWD_AFFECTING_BUILTINS:
        return False
    if name in _BUILTIN_LAUNCHER_PREFIXES:
        # `command cd`, `builtin cd`, `builtin source`, and the `time`
        # keyword all still run the builtin in the parent shell, so any
        # cwd-affecting continuation (including a nested launcher) leaves
        # the working directory unmodeled. Each launcher's own options are
        # consumed first: `command [-pVv] [--]` (where -v/-V only describe —
        # nothing executes), `builtin [--]`, `time [-p]`. An invalid option
        # makes bash error without running anything, which keeps the cwd.
        rest = stripped[1:]
        if name == "time":
            if rest[:1] == ["-p"]:
                rest = rest[1:]
            if rest[:1] == ["--"]:
                rest = rest[1:]
            # time's operand is a pipeline, so assignment prefixes and
            # leading redirections still reach the timed builtin:
            # `time X=1 >log cd nested` changes the cwd.
            rest, _ = _strip_command_prefix_words(rest)
            if rest is None:
                return False
        else:
            while rest and rest[0].startswith("-") and rest[0] not in {"-", "--"}:
                flags = set(rest[0][1:])
                if name != "command" or not flags or flags - {"p", "V", "v"}:
                    break
                if flags & {"V", "v"}:
                    return True
                rest = rest[1:]
            if rest[:1] == ["--"]:
                rest = rest[1:]
            rest, _ = _strip_command_prefix_words(rest)
            if rest is None:
                return False
        follower = rest[0] if rest else ""
        return follower not in (
            {"cd"} | _CWD_AFFECTING_BUILTINS | _BUILTIN_LAUNCHER_PREFIXES
        )
    if name == "cd":
        status, _ = _simple_cd_status(segment, cwd)
        return status is not None
    return True


def _effective_cwd_state(
    bash_cmd: str, offset: int, initial_cwd: Path
) -> tuple[Path, bool]:
    """Resolve preceding ``cd`` commands and report cwd-provenance fidelity.

    Returns the inferred cwd plus a flag that is True only when every
    completed segment in the prefix was fully modeled. The flag is
    deliberately conservative — an unmodeled builtin inside an already
    closed subshell also clears it — because its only consumer is the
    accessor trust decision, where a false negative merely costs a
    redundant reminder.
    """
    tokens = _shell_tokens(bash_cmd[:offset])
    if tokens is None:
        return initial_cwd, False

    cwd = initial_cwd
    fully_modeled = True
    subshell_states: list[tuple[Path, bool, bool]] = []
    segment: list[str] = []
    segment_is_conditional = False
    chain_has_or = False

    def complete_segment() -> None:
        nonlocal fully_modeled
        if not _segment_cwd_is_modeled(segment, cwd):
            fully_modeled = False

    for token in tokens:
        if token in {"&&", "||"}:
            complete_segment()
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
            complete_segment()
            if not segment_is_conditional:
                cwd = _apply_cd(segment, cwd)
            segment = []
            segment_is_conditional = False
            chain_has_or = False
        elif token in {"|", "&"}:
            # A cd in a pipeline/subshell does not reliably affect the parent.
            complete_segment()
            segment = []
        elif token == "(":
            complete_segment()
            segment = []
            subshell_states.append((cwd, segment_is_conditional, chain_has_or))
        elif token == ")":
            complete_segment()
            segment = []
            if subshell_states:
                cwd, segment_is_conditional, chain_has_or = subshell_states.pop()
        else:
            segment.append(token)
    return cwd, fully_modeled


def _effective_cwd(bash_cmd: str, offset: int, initial_cwd: Path) -> Path:
    """Resolve preceding top-level ``cd`` commands for a command match."""
    cwd, _ = _effective_cwd_state(bash_cmd, offset, initial_cwd)
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
        elif depth == 0 and token == "&":
            current_list_ambiguous = True
    if depth:
        malformed = True
    return operators, later_list, current_list_ambiguous or malformed


def _current_command_prefix(fragment: str) -> list[str] | None:
    """Return tokens before a candidate in its current simple command."""
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return None

    # Keep one current-command segment per subshell depth. The fragment ends at
    # the candidate, so a legitimate command inside ``( ... )`` necessarily
    # has an unmatched opening parenthesis here. Command substitutions remain
    # conservative because their child context is marked dynamic.
    segments: list[list[str]] = [[]]
    dynamic_contexts = [False]
    for token in tokens:
        if token == "(":
            command_substitution = bool(
                segments[-1] and segments[-1][-1].endswith("$")
            )
            segments.append([])
            dynamic_contexts.append(dynamic_contexts[-1] or command_substitution)
        elif token == ")":
            if len(segments) == 1:
                return None
            segments.pop()
            dynamic_contexts.pop()
            # A completed compound command is not an execution wrapper for a
            # later token in the same simple command.
            segments[-1].append("__mycelium_compound_command__")
        elif token in {";", "\n", "&&", "||", "|", "&"}:
            segments[-1] = []
        else:
            segments[-1].append(token)
    if dynamic_contexts[-1]:
        return None
    return segments[-1]


_ASSIGNMENT_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
_EXECUTION_WRAPPERS = {
    "command",
    "env",
    "time",
    "exec",
    "nice",
    "timeout",
    "conda",
    "uv",
    "poetry",
}


def _consume_value_option(
    tokens: list[str], index: int, value_options: set[str]
) -> int | None:
    """Consume one option plus its separate value, failing if it is absent."""
    if tokens[index] not in value_options:
        return None
    return index + 2 if index + 1 < len(tokens) else -1


def _resolve_wrapper_directory(value: str, cwd: Path) -> Path | None:
    """Resolve a static wrapper cwd, rejecting dynamic or unusable targets."""
    if not value or any(marker in value for marker in ("$", "`")):
        return None
    target = Path(os.path.expanduser(value))
    if not target.is_absolute():
        target = cwd / target
    target = Path(os.path.abspath(target))
    if not target.is_dir() or not os.access(target, os.X_OK):
        return None
    return target


def _consume_execution_wrapper(
    tokens: list[str], index: int, cwd: Path
) -> tuple[int, Path] | None:
    """Return the next wrapper index/cwd, or ``None`` on misuse.

    ``tokens`` contains only text before the candidate interpreter. Therefore
    consuming the complete prefix proves that every preceding token is wrapper
    syntax and that the interpreter is the command those wrappers will execute.
    """
    wrapper = Path(tokens[index]).name
    index += 1

    if wrapper == "command":
        while index < len(tokens):
            token = tokens[index]
            if token in {"-v", "-V", "--help"}:
                return None
            if token == "--":
                next_index = index + 1 if index + 1 < len(tokens) else len(tokens)
                return next_index, cwd
            if token == "-p":
                index += 1
                continue
            break
        return index, cwd

    if wrapper == "env":
        value_options = {"-u", "--unset"}
        flag_options = {"-i", "-0", "--ignore-environment", "--null"}
        while index < len(tokens):
            token = tokens[index]
            if token in {"--help", "--version"}:
                return None
            if token in {"-S", "--split-string"} or token.startswith(
                ("-S", "--split-string=")
            ):
                # env expands this value into multiple argv entries. Without
                # reconstructing that command, a later interpreter token may
                # merely be an argument to the expanded executable.
                return None
            if token in {"-C", "--chdir"}:
                if index + 1 >= len(tokens):
                    return None
                updated_cwd = _resolve_wrapper_directory(tokens[index + 1], cwd)
                if updated_cwd is None:
                    return None
                cwd = updated_cwd
                index += 2
                continue
            if token.startswith("--chdir=") or (
                token.startswith("-C") and token != "-C"
            ):
                value = token.split("=", 1)[1] if "=" in token else token[2:]
                updated_cwd = _resolve_wrapper_directory(value, cwd)
                if updated_cwd is None:
                    return None
                cwd = updated_cwd
                index += 1
                continue
            consumed = _consume_value_option(tokens, index, value_options)
            if consumed == -1:
                return None
            if consumed is not None:
                index = consumed
                continue
            if any(token.startswith(f"{option}=") for option in value_options):
                index += 1
                continue
            if token in flag_options:
                index += 1
                continue
            if token == "--":
                next_index = index + 1 if index + 1 < len(tokens) else len(tokens)
                return next_index, cwd
            if _ASSIGNMENT_RX.match(token):
                index += 1
                continue
            break
        return index, cwd

    if wrapper == "time":
        value_options = {"-f", "--format", "-o", "--output"}
        flag_options = {
            "-p",
            "--portability",
            "-a",
            "--append",
            "-v",
            "--verbose",
            "--quiet",
        }
        while index < len(tokens):
            token = tokens[index]
            consumed = _consume_value_option(tokens, index, value_options)
            if consumed == -1:
                return None
            if consumed is not None:
                index = consumed
                continue
            if any(token.startswith(f"{option}=") for option in value_options):
                index += 1
                continue
            if token in flag_options:
                index += 1
                continue
            if token == "--":
                next_index = index + 1 if index + 1 < len(tokens) else len(tokens)
                return next_index, cwd
            break
        return index, cwd

    if wrapper == "exec":
        while index < len(tokens):
            token = tokens[index]
            if token == "-a":
                if index + 1 >= len(tokens):
                    return None
                index += 2
                continue
            if token.startswith("--argv0="):
                index += 1
                continue
            if token == "--":
                next_index = index + 1 if index + 1 < len(tokens) else len(tokens)
                return next_index, cwd
            if re.fullmatch(r"-[cl]+", token):
                index += 1
                continue
            break
        return index, cwd

    if wrapper == "nice":
        while index < len(tokens):
            token = tokens[index]
            if token in {"--help", "--version"}:
                return None
            if token in {"-n", "--adjustment"}:
                if index + 1 >= len(tokens):
                    return None
                index += 2
                continue
            if token.startswith("--adjustment=") or re.fullmatch(r"-\d+", token):
                index += 1
                continue
            if token == "--":
                next_index = index + 1 if index + 1 < len(tokens) else len(tokens)
                return next_index, cwd
            if token.startswith("-"):
                return None
            break
        return index, cwd

    if wrapper == "timeout":
        value_options = {"-k", "--kill-after", "-s", "--signal"}
        flag_options = {"--foreground", "--preserve-status", "--verbose"}
        while index < len(tokens):
            token = tokens[index]
            if token in {"--help", "--version"}:
                return None
            consumed = _consume_value_option(tokens, index, value_options)
            if consumed == -1:
                return None
            if consumed is not None:
                index = consumed
                continue
            if any(token.startswith(f"{option}=") for option in value_options):
                index += 1
                continue
            if token in flag_options:
                index += 1
                continue
            if token == "--":
                index += 1
                break
            if token.startswith("-"):
                return None
            break
        # timeout requires a duration before the command it executes.
        if index >= len(tokens):
            return None
        return index + 1, cwd

    if wrapper in {"conda", "uv", "poetry"}:
        if index >= len(tokens) or tokens[index] != "run":
            return None
        index += 1
        value_options = {
            "conda": {"-n", "--name", "-p", "--prefix"},
            "uv": {
                "--project",
                "--python",
                "--with",
                "--with-editable",
                "--with-requirements",
                "--env-file",
            },
            "poetry": set(),
        }[wrapper]
        cwd_option = {"conda": "--cwd", "uv": "--directory"}.get(wrapper)
        while index < len(tokens):
            token = tokens[index]
            if token in {"-h", "--help", "-V", "--version"}:
                return None
            if cwd_option and token == cwd_option:
                if index + 1 >= len(tokens):
                    return None
                updated_cwd = _resolve_wrapper_directory(tokens[index + 1], cwd)
                if updated_cwd is None:
                    return None
                cwd = updated_cwd
                index += 2
                continue
            if cwd_option and token.startswith(f"{cwd_option}="):
                updated_cwd = _resolve_wrapper_directory(token.split("=", 1)[1], cwd)
                if updated_cwd is None:
                    return None
                cwd = updated_cwd
                index += 1
                continue
            consumed = _consume_value_option(tokens, index, value_options)
            if consumed == -1:
                return None
            if consumed is not None:
                index = consumed
                continue
            if any(token.startswith(f"{option}=") for option in value_options):
                index += 1
                continue
            if token == "--":
                next_index = index + 1 if index + 1 < len(tokens) else len(tokens)
                return next_index, cwd
            if token.startswith("-"):
                index += 1
                continue
            break
        return index, cwd

    return None


def _prefix_execution_cwd(prefix: list[str], initial_cwd: Path) -> Path | None:
    """Return the cwd when a prefix is only assignments/execution wrappers."""
    index = 0
    while index < len(prefix) and _ASSIGNMENT_RX.match(prefix[index]):
        index += 1
    if index == len(prefix):
        return initial_cwd

    saw_wrapper = False
    cwd = initial_cwd
    while index < len(prefix):
        if Path(prefix[index]).name not in _EXECUTION_WRAPPERS:
            return None
        saw_wrapper = True
        consumed = _consume_execution_wrapper(prefix, index, cwd)
        if consumed is None or consumed[0] <= index:
            return None
        index, cwd = consumed
    return cwd if saw_wrapper else None


def _command_effective_cwd(
    bash_cmd: str, offset: int, initial_cwd: Path
) -> Path | None:
    """Return the candidate command cwd, or ``None`` if execution is unproven."""
    prefix = _current_command_prefix(bash_cmd[:offset])
    if prefix is None:
        return None
    base_cwd = _effective_cwd(bash_cmd, offset, initial_cwd)
    return _prefix_execution_cwd(prefix, base_cwd)


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


def _and_prefix_is_proven_successful(fragment: str, initial_cwd: Path) -> bool:
    """Prove that every simple command before a pure-AND candidate succeeded."""
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return False

    cwd = initial_cwd
    segment: list[str] = []
    completed: list[list[str]] = []
    operators: list[str] = []
    depth = 0
    for token in tokens:
        if token == "(":
            depth += 1
            if depth == 1:
                return False
        elif token == ")":
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and token in {";", "\n", "||", "|", "&"}:
            return False
        elif depth == 0 and token == "&&":
            completed.append(segment)
            operators.append(token)
            status, target = _simple_cd_status(segment, cwd)
            if status is True:
                cwd = target
            segment = []
        else:
            segment.append(token)
    if depth or not operators:
        return False
    cwd = initial_cwd
    for part in completed:
        status = _simple_command_status(part, cwd)
        if status is not True:
            return False
        _, cwd = _simple_cd_status(part, cwd)
    return True


def _match_is_proven_executed(
    bash_cmd: str, offset: int, bash_exit: int | None, initial_cwd: Path
) -> bool:
    """Conservatively decide whether a textual script match actually ran."""
    command_without_comments = _strip_unquoted_shell_comments(bash_cmd)
    if command_without_comments is None:
        return False
    if command_without_comments[offset : offset + 1] != bash_cmd[offset : offset + 1]:
        return False
    unquoted_shell = _unquoted_shell_text(bash_cmd)
    if unquoted_shell is None or RX_UNSUPPORTED_SHELL_STRUCTURE.search(
        unquoted_shell
    ):
        return False
    if _command_effective_cwd(bash_cmd, offset, initial_cwd) is None:
        return False
    before_ops, _, before_ambiguous = _top_level_control_context(bash_cmd[:offset])
    if before_ambiguous:
        return False
    if not before_ops:
        # The first command in an AND-OR list always executes.
        return True
    after_ops, has_later_list, after_ambiguous = _top_level_control_context(
        bash_cmd[offset:]
    )
    if has_later_list or after_ambiguous:
        # The overall exit status belongs to a later list or an unsupported
        # compound construct, so it cannot prove this conditional branch ran.
        return False
    all_ops = before_ops + after_ops
    if all(operator == "&&" for operator in all_ops):
        return bash_exit == 0 or _and_prefix_is_proven_successful(
            bash_cmd[:offset], initial_cwd
        )
    if all(operator == "||" for operator in all_ops):
        # A nonzero final status proves every OR alternative ran and failed.
        # A successful list is normally ambiguous, but a final fallback is
        # still proven to run when every preceding alternative has a known
        # failure (notably ``cd missing || python a.py``).
        return (bash_exit is not None and bash_exit != 0) or _or_prefix_is_proven_failed(
            bash_cmd[:offset], initial_cwd
        )
    return False


def _runner_word(match: re.Match[str]) -> str | None:
    """Return a decoded runner command word, or ``None`` for a near miss.

    A bare ``x.sh`` is resolved through PATH rather than the cwd, so direct
    execution counts only for a word containing a directory separator.
    """
    word = _decode_shell_word(match.group("runner_exe"))
    if word is None:
        return None
    name = Path(word).name
    if name in _SHELL_RUNNER_NAMES or name in _PIPELINE_RUNNER_NAMES:
        return word
    if name.endswith(".sh") and "/" in word:
        return word
    return None


class _RunnerRun(NamedTuple):
    """What one runner invocation executes.

    ``entry`` is the entry-point path as written (resolved against the
    invocation cwd) and ``source`` a descriptive pseudo-source when no entry
    file can be named; exactly one is set. ``run_dir`` is the directory the
    runner executes the workflow in when an option changes it
    (``snakemake --directory``, ``sbatch --chdir``), else ``None``;
    ``run_dir_unknown`` marks configuration outside argv (a Snakemake
    profile) that may move the run somewhere this parser cannot see.
    """

    entry: str | None
    source: str | None
    run_dir: str | None = None
    run_dir_unknown: bool = False


# Shell options form a small, closed set. Model it completely: an option
# outside it makes the shell exit with an error before running anything, so
# an unknown option is not execution evidence.
_SHELL_EXECUTING_SHORT = {
    "bash": frozenset("abefhkmptuvxBCEHPTilr"),
    # POSIX set options plus the login/privileged flags sh, dash, ksh, and
    # zsh all accept.
    "default": frozenset("abCefhiklmpuvx"),
}
# -c runs inline source, -n and -D only parse, -s reads the script from stdin.
_SHELL_NONEXECUTING_SHORT = frozenset("cnsD")
_BASH_EXECUTING_LONG = frozenset(
    {
        "--debugger",
        "--login",
        "--noediting",
        "--noprofile",
        "--norc",
        "--posix",
        "--restricted",
        "--verbose",
    }
)
_BASH_VALUE_LONG = frozenset({"--rcfile", "--init-file"})
_SHELL_SCRIPT_SUFFIXES = (".sh", ".bash")
# `set -o` and `shopt -s` names across Bash 3.2 (macOS) through 5.3. Bash
# exits before reading the script when `-o`/`-O` names anything else.
_BASH_SET_O_NAMES = frozenset(
    {
        "allexport", "braceexpand", "emacs", "errexit", "errtrace",
        "functrace", "hashall", "histexpand", "history", "ignoreeof",
        "interactive-comments", "keyword", "monitor", "noclobber", "noexec",
        "noglob", "nolog", "notify", "nounset", "onecmd", "physical",
        "pipefail", "posix", "privileged", "verbose", "vi", "xtrace",
    }
)
_BASH_SHOPT_NAMES = frozenset(
    {
        "array_expand_once", "assoc_expand_once", "autocd",
        "bash_source_fullpath", "cdable_vars", "cdspell", "checkhash",
        "checkjobs", "checkwinsize", "cmdhist", "compat31", "compat32",
        "compat40", "compat41", "compat42", "compat43", "compat44",
        "complete_fullquote", "direxpand", "dirspell", "dotglob", "execfail",
        "expand_aliases", "extdebug", "extglob", "extquote", "failglob",
        "force_fignore", "globasciiranges", "globskipdots", "globstar",
        "gnu_errfmt", "histappend", "histreedit", "histverify",
        "hostcomplete", "huponexit", "inherit_errexit", "interactive_comments",
        "lastpipe", "lithist", "localvar_inherit", "localvar_unset",
        "login_shell", "mailwarn", "no_empty_cmd_completion", "nocaseglob",
        "nocasematch", "noexpand_translation", "nullglob",
        "patsub_replacement", "progcomp", "progcomp_alias", "promptvars",
        "restricted_shell", "shift_verbose", "sourcepath", "varredir_close",
        "xpg_echo",
    }
)
# `sh` is bash in POSIX mode on macOS and dash on Debian; accept either's
# `set -o` names. zsh and ksh have large, version-specific option sets, so
# only their no-exec spelling is checked.
_SH_SET_O_NAMES = _BASH_SET_O_NAMES | frozenset({"debug", "interactive", "stdin"})
_SHELL_SET_O_NAMES = {"bash": _BASH_SET_O_NAMES, "sh": _SH_SET_O_NAMES, "dash": _SH_SET_O_NAMES}


def _shell_option_is_noexec(name: str) -> bool:
    """``-o noexec`` (zsh also spells it ``NO_EXEC``) parses without running."""
    return name.lower().replace("_", "") == "noexec"


def _shell_runner_script(shell: str, args: list[str]) -> str | None:
    """Return the script a shell argv executes, if it runs one."""
    executing_short = _SHELL_EXECUTING_SHORT.get(
        shell, _SHELL_EXECUTING_SHORT["default"]
    )
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            index += 1
            break
        if token.startswith("--"):
            if shell != "bash":
                return None
            if token in _BASH_VALUE_LONG:
                index += 2
            elif token in _BASH_EXECUTING_LONG:
                index += 1
            else:
                # --help, --version, --dump-strings, --dump-po-strings,
                # --pretty-print, and unknown options all exit without
                # running the script.
                return None
            continue
        if len(token) > 1 and token[0] in "-+":
            sign, flags = token[0], token[1:]
            takes_value = flags[-1] in "oO"
            letters = flags[:-1] if takes_value else flags
            if not letters.isalpha() and letters:
                return None
            if sign == "-" and _SHELL_NONEXECUTING_SHORT & set(letters):
                return None
            if set(letters) - executing_short:
                return None
            if takes_value:
                if index + 1 >= len(args):
                    return None
                value = args[index + 1]
                if flags[-1] == "O":
                    # shopt names exist only in bash.
                    if shell != "bash" or value not in _BASH_SHOPT_NAMES:
                        return None
                else:
                    if sign == "-" and _shell_option_is_noexec(value):
                        return None
                    known = _SHELL_SET_O_NAMES.get(shell)
                    if known is not None and value not in known:
                        return None
                index += 2
            else:
                index += 1
            continue
        break
    if index >= len(args):
        return None
    script = args[index]
    return script if script.endswith(_SHELL_SCRIPT_SUFFIXES) else None


_SNAKEMAKE_NONEXECUTING_LONG = frozenset(
    {
        "--archive",
        "--bash-completion",
        "--cleanup-metadata",
        "--cleanup-shadow",
        "--cm",
        "--conda-cleanup-envs",
        "--conda-cleanup-pkgs",
        "--conda-create-envs-only",
        "--containerize",
        "--create-envs-only",
        "--d3dag",
        "--dag",
        "--delete-all-output",
        "--delete-temp-output",
        "--detailed-summary",
        "--draft-notebook",
        "--dry-run",
        "--dryrun",
        "--export-cwl",
        "--filegraph",
        "--generate-unit-tests",
        "--help",
        "--lc",
        "--li",
        "--lint",
        "--list",
        "--list-changes",
        "--list-code-changes",
        "--list-conda-envs",
        "--list-input-changes",
        "--list-params-changes",
        "--list-rules",
        "--list-target-rules",
        "--list-untracked",
        "--list-version-changes",
        "--lp",
        "--lt",
        "--lu",
        "--lv",
        "--print-compilation",
        "--report",
        "--rulegraph",
        "--summary",
        "--touch",
        "--unlock",
        "--version",
    }
)
# Boolean short options that may be clustered (`-np`, `-pk`). A cluster made
# only of these letters is parsed; anything else is left as an option value.
_SNAKEMAKE_FLAG_SHORT = frozenset("nprkFSDlthv")
_SNAKEMAKE_NONEXECUTING_SHORT = frozenset("nSDlthv")
# Value options whose value changes what runs or where. Snakemake's built-in
# `dryrun` and `touch` executors are the dry-run and --touch modes.
_SNAKEMAKE_VALUE_OPTIONS = {
    "-s": "snakefile",
    "--snakefile": "snakefile",
    "-d": "directory",
    "--directory": "directory",
    "-e": "executor",
    "--executor": "executor",
    "--profile": "profile",
    "--workflow-profile": "workflow_profile",
}
_SNAKEMAKE_NONEXECUTING_EXECUTORS = frozenset({"dryrun", "touch"})
_SNAKEMAKE_DEFAULT_SNAKEFILES = (
    "Snakefile",
    "snakefile",
    "workflow/Snakefile",
    "workflow/snakefile",
)


_SNAKEMAKE_PROFILE_FILE_RX = re.compile(r"(?:profile|config)(?:\.v\d+\+)?\.yaml")
_SNAKEMAKE_PROFILE_KEY_RX = re.compile(
    rb"""^["']?(?P<key>directory|snakefile)["']?[ \t]*:""", re.M
)


def _snakemake_user_profile_roots() -> list[Path]:
    """Every user/site config dir Snakemake's appdirs may search, any OS."""
    home = Path.home()
    roots = [
        home / ".config" / "snakemake",
        home / "Library" / "Application Support" / "snakemake",
        Path("/etc/xdg/snakemake"),
        Path("/Library/Application Support/snakemake"),
    ]
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        roots.insert(0, Path(xdg) / "snakemake")
    return roots


def _snakemake_profile_files(location: Path) -> list[Path]:
    if location.is_file():
        return [location]
    try:
        return [
            child
            for child in location.iterdir()
            if _SNAKEMAKE_PROFILE_FILE_RX.fullmatch(child.name) and child.is_file()
        ]
    except OSError:
        return []


def _snakemake_profile_overrides(
    profiles: list[str], workflow_profile: str | None, cwd: Path, snakefile: str | None
) -> set[str] | None:
    """Return the run-locating keys active profiles set (``directory``,
    ``snakefile``), or ``None`` when an active profile cannot be inspected.

    Mirrors snakemake.cli: ``--profile`` is a path or a name searched in the
    cwd and the user/site config dirs; the workflow profile defaults to
    ``profiles/default`` beside the cwd or the Snakefile unless ``none``.
    Every candidate location is inspected, so a divergence from Snakemake's
    own choice can only leave the run unresolved, never wrong.
    """
    files: list[Path] = []
    for profile in profiles:
        if profile == "none":
            continue
        path = Path(profile)
        if not path.is_absolute():
            path = cwd / path
        if path.exists():
            candidates = [path]
        else:
            candidates = [
                root / profile
                for root in [cwd, *_snakemake_user_profile_roots()]
                if (root / profile).exists()
            ]
        found = [f for candidate in candidates for f in _snakemake_profile_files(candidate)]
        if not found:
            return None
        files.extend(found)
    if workflow_profile != "none":
        name = workflow_profile or "default"
        candidates = [Path(name), Path("profiles") / name]
        if snakefile:
            candidates.append(Path(snakefile).parent / "profiles" / name)
        for candidate in candidates:
            location = candidate if candidate.is_absolute() else cwd / candidate
            if location.exists():
                files.extend(_snakemake_profile_files(location))
                break
    keys: set[str] = set()
    for profile_file in files:
        try:
            content = profile_file.read_bytes()[: 1024 * 1024]
        except OSError:
            return None
        keys.update(
            m.group("key").decode() for m in _SNAKEMAKE_PROFILE_KEY_RX.finditer(content)
        )
    return keys


def _snakemake_detection(args: list[str], cwd: Path) -> _RunnerRun | None:
    """Describe an executing snakemake argv, or ``None`` for a non-run.

    Snakemake resolves the Snakefile in the invocation directory and only
    then changes into ``--directory`` (snakemake.api.WorkflowApi), so the
    entry stays cwd-relative while the workflow's I/O uses the directory.
    """
    values: dict[str, str] = {}
    profiles: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        name, has_value, value = token.partition("=")
        if token.startswith("--"):
            if name in _SNAKEMAKE_NONEXECUTING_LONG:
                return None
            if name in _SNAKEMAKE_VALUE_OPTIONS:
                if not has_value:
                    if index + 1 >= len(args):
                        return None
                    value = args[index + 1]
                    index += 1
                values[_SNAKEMAKE_VALUE_OPTIONS[name]] = value
                if name == "--profile":
                    profiles.append(value)
        elif token in _SNAKEMAKE_VALUE_OPTIONS:
            if index + 1 >= len(args):
                return None
            values[_SNAKEMAKE_VALUE_OPTIONS[token]] = args[index + 1]
            index += 1
        elif (
            len(token) > 1
            and token[0] == "-"
            and token[1:].isalpha()
            and set(token[1:]) <= _SNAKEMAKE_FLAG_SHORT
        ):
            if _SNAKEMAKE_NONEXECUTING_SHORT & set(token[1:]):
                return None
        elif len(token) > 2 and token[:2] in _SNAKEMAKE_VALUE_OPTIONS:
            values[_SNAKEMAKE_VALUE_OPTIONS[token[:2]]] = token[2:]
        index += 1
    if values.get("executor") in _SNAKEMAKE_NONEXECUTING_EXECUTORS:
        return None
    snakefile = values.get("snakefile")
    default_snakefile = next(
        (c for c in _SNAKEMAKE_DEFAULT_SNAKEFILES if (cwd / c).is_file()), None
    )
    overrides = _snakemake_profile_overrides(
        profiles, values.get("workflow_profile"), cwd, snakefile or default_snakefile
    )
    run_dir = values.get("directory")
    run_dir_unknown = run_dir is None and (
        overrides is None or "directory" in overrides
    )
    if not snakefile:
        if overrides is None or "snakefile" in overrides:
            # A profile may name the Snakefile; do not hash a guessed one.
            return _RunnerRun(
                None,
                "# Snakemake workflow run (Snakefile set by a profile)",
                run_dir,
                run_dir_unknown,
            )
        snakefile = default_snakefile
    if snakefile:
        return _RunnerRun(snakefile, None, run_dir, run_dir_unknown)
    return _RunnerRun(
        None,
        "# Snakemake workflow run (no Snakefile found)",
        run_dir,
        run_dir_unknown,
    )


_NEXTFLOW_GLOBAL_VALUE_OPTIONS = {"-C", "-c", "-config", "-log", "-syslog", "-trace"}
_NEXTFLOW_GLOBAL_FLAG_OPTIONS = {"-q", "-quiet", "-bg", "-background", "-d", "-dockerize"}
_NEXTFLOW_NONEXECUTING_RUN_OPTIONS = {"-h", "-help", "-preview", "-stub", "-stub-run"}


def _nextflow_sets_main_script(pipeline_dir: Path) -> bool:
    """Whether ``manifest.mainScript`` may name an entry other than main.nf."""
    try:
        config = (pipeline_dir / "nextflow.config").read_bytes()[:1024 * 1024]
    except OSError:
        return False
    return b"mainScript" in config


def _nextflow_detection(args: list[str], cwd: Path) -> _RunnerRun | None:
    """Describe ``nextflow run``; any other subcommand is not a run."""
    index = 0
    while index < len(args):
        token = args[index]
        if token in _NEXTFLOW_GLOBAL_VALUE_OPTIONS:
            index += 2
        elif token in _NEXTFLOW_GLOBAL_FLAG_OPTIONS or token.startswith("-D"):
            index += 1
        else:
            break
    if index >= len(args) or args[index] != "run":
        return None
    run_args = args[index + 1 :]
    if any(token in _NEXTFLOW_NONEXECUTING_RUN_OPTIONS for token in run_args):
        return None
    if not run_args or run_args[0].startswith("-"):
        return _RunnerRun(None, "# Nextflow pipeline run")
    pipeline = run_args[0]
    if pipeline.endswith(".nf"):
        return _RunnerRun(pipeline, None)
    if (cwd / pipeline / "main.nf").is_file() and not _nextflow_sets_main_script(
        cwd / pipeline
    ):
        return _RunnerRun(str(Path(pipeline) / "main.nf"), None)
    return _RunnerRun(None, f"# Nextflow pipeline run: {pipeline}")


# getopt optional arguments must be attached, so these are flags when bare.
_SBATCH_FLAG_SHORT = {"-H", "-I", "-k", "-O", "-Q", "-s", "-v", "-W"}
_SBATCH_FLAG_LONG = {
    "--contiguous",
    "--exclusive",
    "--get-user-env",
    "--hold",
    "--ignore-pbs",
    "--immediate",
    "--nice",
    "--no-kill",
    "--no-requeue",
    "--overcommit",
    "--oversubscribe",
    "--parsable",
    "--quiet",
    "--requeue",
    "--spread-job",
    "--use-min-nodes",
    "--verbose",
    "--wait",
}
_SBATCH_NONEXECUTING = {"-h", "--help", "-V", "--version", "-u", "--usage", "--test-only"}


class _SbatchOptions(NamedTuple):
    executes: bool
    chdir: str | None
    wrap: str | None
    script: str | None


def _parse_sbatch_options(args: list[str]) -> _SbatchOptions:
    """Parse sbatch options up to the job script (CLI or ``#SBATCH`` line)."""
    chdir: str | None = None
    wrap: str | None = None
    index = 0
    while index < len(args):
        token = args[index]
        name, has_value, value = token.partition("=")
        if name in _SBATCH_NONEXECUTING:
            return _SbatchOptions(False, chdir, wrap, None)
        if token.startswith("--"):
            if name in _SBATCH_FLAG_LONG or has_value:
                index += 1
            elif index + 1 < len(args):
                value = args[index + 1]
                index += 2
            else:
                return _SbatchOptions(False, chdir, wrap, None)
            if name == "--wrap":
                wrap = value
            elif name == "--chdir":
                chdir = value
        elif token.startswith("-") and len(token) > 1:
            # `-J name` takes a value; `-Jname` carries it attached.
            if len(token) > 2:
                value = token[2:]
                index += 1
            elif token in _SBATCH_FLAG_SHORT:
                index += 1
                continue
            elif index + 1 < len(args):
                value = args[index + 1]
                index += 2
            else:
                return _SbatchOptions(False, chdir, wrap, None)
            if token[:2] == "-D":
                chdir = value
        else:
            return _SbatchOptions(True, chdir, wrap, token)
    return _SbatchOptions(True, chdir, wrap, None)


_SBATCH_DIRECTIVE_READ_LIMIT_BYTES = 1024 * 1024


def _sbatch_script_directives(script: Path) -> list[str] | None:
    """Return the option words of a job script's ``#SBATCH`` header.

    Slurm reads ``#SBATCH`` lines only before the first command; blank and
    other comment lines do not end the header. ``None`` means the header
    could not be read or parsed.
    """
    words: list[str] = []
    try:
        with script.open("rb") as handle:
            raw = handle.read(_SBATCH_DIRECTIVE_READ_LIMIT_BYTES)
    except OSError:
        return None
    for line in raw.decode("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        if not re.match(r"#SBATCH(?:\s|$)", line):
            continue
        try:
            words.extend(shlex.split(line[len("#SBATCH") :], comments=True))
        except ValueError:
            return None
    return words


def _sbatch_detection(args: list[str], cwd: Path) -> _RunnerRun | None:
    """Describe an ``sbatch`` submission, or ``None`` when nothing is queued.

    Slurm resolves a relative job script against ``--chdir`` when given, else
    the submission directory, and runs the job in the command line's
    ``--chdir``, else the script header's, else the submission directory.
    """
    options = _parse_sbatch_options(args)
    if not options.executes:
        return None
    if options.wrap is not None:
        return _RunnerRun(None, f"# sbatch --wrap: {options.wrap}", options.chdir)
    if options.script is None:
        return _RunnerRun(
            None, "# sbatch job submission (script from stdin)", options.chdir
        )
    script = Path(options.script)
    if not script.is_absolute():
        base = (
            cwd
            if options.chdir is None
            else _resolve_runner_directory(options.chdir, cwd)
        )
        if base is None:
            return _RunnerRun(
                None,
                f"# sbatch job script {options.script} "
                "(submission directory unresolved)",
                options.chdir,
            )
        script = base / script
    chdir = options.chdir
    directives = _sbatch_script_directives(script) if script.is_file() else []
    if directives is None:
        directives = []
    header = _parse_sbatch_options(directives)
    if not header.executes:
        return None
    if chdir is None and header.chdir is not None:
        chdir = header.chdir
    return _RunnerRun(str(script), None, chdir)


def _runner_detection(argv: list[str], cwd: Path) -> _RunnerRun | None:
    """Dispatch a runner argv to what it executes, or ``None`` for a non-run."""
    name = Path(argv[0]).name
    args = argv[1:]
    if name in _SHELL_RUNNER_NAMES:
        script = _shell_runner_script(name, args)
        return _RunnerRun(script, None) if script is not None else None
    if name == "snakemake":
        return _snakemake_detection(args, cwd)
    if name == "nextflow":
        return _nextflow_detection(args, cwd)
    if name == "sbatch":
        return _sbatch_detection(args, cwd)
    return _RunnerRun(argv[0], None)


def _resolve_runner_directory(value: str, cwd: Path) -> Path | None:
    """Resolve a runner's static run directory; ``None`` when unknowable.

    The directory need not exist yet (snakemake creates it), but a value the
    shell would expand (``$VAR``, a backtick substitution, ``~``) cannot be
    reproduced from argv, so I/O under it is left unresolved, not guessed.
    """
    if not value or any(marker in value for marker in ("$", "`", "~")):
        return None
    target = Path(value)
    if not target.is_absolute():
        target = cwd / target
    try:
        return Path(os.path.abspath(target))
    except ValueError:
        return None


def _is_bundled_plugin_file(canonical: Path) -> bool:
    """True for a file shipped inside the running Mycelium plugin tree."""
    try:
        return canonical.is_relative_to(_EXECUTING_PLUGIN_ROOT)
    except ValueError:
        return False


def _detect_runners(
    bash_cmd: str,
    cwd: Path,
    *,
    bash_exit: int | None,
    require_execution_evidence: bool,
) -> list[tuple[Path | None, str | None, Path | None]]:
    """Detect proven pipeline-runner and shell-script executions.

    The third element is the directory the run's I/O resolves against, or
    ``None`` when a runner option moves the run somewhere unknowable.
    """
    out: list[tuple[Path | None, str | None, Path | None]] = []
    for pattern in RUNNER_PATTERNS:
        for m in pattern.finditer(bash_cmd):
            word = _runner_word(m)
            if word is None:
                continue
            effective_cwd = _command_effective_cwd(bash_cmd, m.start(), cwd)
            if effective_cwd is None:
                continue
            if require_execution_evidence and not _match_is_proven_executed(
                bash_cmd, m.start(), bash_exit, cwd
            ):
                continue
            fragment = _simple_command_suffix(bash_cmd, m.start())
            if fragment is None:
                continue
            argv = _simple_command_argv(fragment)
            if not argv or argv[0] != word:
                continue
            run = _runner_detection(argv, effective_cwd)
            if run is None:
                continue
            if run.run_dir_unknown:
                io_cwd = None
            elif run.run_dir is None:
                io_cwd = effective_cwd
            else:
                io_cwd = _resolve_runner_directory(run.run_dir, effective_cwd)
            if run.entry is None:
                out.append((None, run.source, io_cwd))
                continue
            unresolved = Path(run.entry)
            if not unresolved.is_absolute():
                unresolved = effective_cwd / unresolved
            try:
                path = Path(os.path.abspath(unresolved))
                canonical = Path(os.path.realpath(unresolved))
            except ValueError:
                continue
            if _is_bundled_plugin_file(canonical) or _is_managed_utility_path(
                canonical
            ):
                continue
            out.append((path, None, io_cwd))
    return out


def _detect_scripts_with_cwd(
    bash_cmd: str,
    cwd: Path,
    *,
    bash_exit: int | None = None,
    require_execution_evidence: bool = False,
    include_python_inline: bool = True,
) -> list[tuple[Path | None, str | None, Path | None]]:
    out: list[tuple[Path | None, str | None, Path | None]] = []
    seen_paths: set[Path] = set()
    seen_inline: set[tuple[str, Path | None]] = set()
    for m in RX_PYTHON_C.finditer(bash_cmd):
        if not _match_has_expected_executable(m, RX_PYTHON_C):
            continue
        if not include_python_inline:
            continue
        effective_cwd = _command_effective_cwd(bash_cmd, m.start(), cwd)
        if effective_cwd is None:
            continue
        if require_execution_evidence and not _match_is_proven_executed(
            bash_cmd, m.start(), bash_exit, cwd
        ):
            continue
        src = _decode_shell_word(m.group("source_word"))
        if src is None:
            continue
        identity = (src, effective_cwd)
        if identity not in seen_inline:
            out.append((None, src, effective_cwd))
            seen_inline.add(identity)
    for m in RX_R_E.finditer(bash_cmd):
        if not _match_has_expected_executable(m, RX_R_E):
            continue
        effective_cwd = _command_effective_cwd(bash_cmd, m.start(), cwd)
        if effective_cwd is None:
            continue
        if require_execution_evidence and not _match_is_proven_executed(
            bash_cmd, m.start(), bash_exit, cwd
        ):
            continue
        src = _decode_shell_word(m.group("source_word"))
        if src is None:
            continue
        identity = (src, effective_cwd)
        if identity not in seen_inline:
            out.append((None, src, effective_cwd))
            seen_inline.add(identity)
    for m in RX_PYTHON_M.finditer(bash_cmd):
        if not _match_has_expected_executable(m, RX_PYTHON_M):
            continue
        effective_cwd = _command_effective_cwd(bash_cmd, m.start(), cwd)
        if effective_cwd is None:
            continue
        if require_execution_evidence and not _match_is_proven_executed(
            bash_cmd, m.start(), bash_exit, cwd
        ):
            continue
        module = m.group("module")
        if module in IGNORED_PYTHON_MODULES:
            continue
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
            if not _match_has_expected_executable(m, pattern):
                continue
            if (
                pattern is RX_JUPYTER_SCRIPT_PATH
                and _jupyter_match_has_terminal_option(bash_cmd, m.start())
            ):
                continue
            effective_cwd = _command_effective_cwd(bash_cmd, m.start(), cwd)
            if effective_cwd is None:
                continue
            if require_execution_evidence and not _match_is_proven_executed(
                bash_cmd, m.start(), bash_exit, cwd
            ):
                continue
            try:
                path_words = shlex.split(m.group("path"), comments=False, posix=True)
            except ValueError:
                continue
            if len(path_words) != 1:
                continue
            raw_path = path_words[0]
            if Path(raw_path).name in IGNORED_SCRIPT_BASENAMES:
                continue
            # The accessor dereferences a pointer relative to the effective
            # cwd, so it may resolve only when every cwd change in the prefix
            # was fully modeled; an unmodeled `pushd`/`source`/flagged-cd
            # form means the pointer's directory is unknown (issue #71).
            expanded = None
            _, prefix_cwd_modeled = _effective_cwd_state(bash_cmd, m.start(), cwd)
            if prefix_cwd_modeled:
                expanded = _accessor_expanded_script_path(
                    m.group("path"), raw_path, effective_cwd
                )
            if expanded is not None:
                unresolved = expanded
            else:
                unresolved = Path(raw_path)
                if not unresolved.is_absolute():
                    unresolved = effective_cwd / unresolved
            # Judge trust on the canonical filesystem path of the UNCOLLAPSED
            # word: lexical abspath folds "symlink/.." differently than the
            # shell resolves it, so a link inside a verified tree must not
            # smuggle a user script past the gate (while a symlinked alias of
            # a verified root still verifies). A word no shell could execute
            # (e.g. an embedded NUL from a corrupt payload) is skipped rather
            # than allowed to crash the surrounding hook.
            try:
                path = Path(os.path.abspath(unresolved))
                canonical = Path(os.path.realpath(unresolved))
            except ValueError:
                continue
            if _is_managed_utility_path(canonical):
                continue
            if path not in seen_paths:
                out.append((path, None, effective_cwd))
                seen_paths.add(path)
    for path, source, io_cwd in _detect_runners(
        bash_cmd,
        cwd,
        bash_exit=bash_exit,
        require_execution_evidence=require_execution_evidence,
    ):
        if path is not None:
            if path not in seen_paths:
                out.append((path, None, io_cwd))
                seen_paths.add(path)
        elif (source, io_cwd) not in seen_inline:
            out.append((None, source, io_cwd))
            seen_inline.add((source, io_cwd))
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


_DATA_FILE_SUFFIXES = {
    ".csv",
    ".tsv",
    ".txt",
    ".json",
    ".jsonl",
    ".parquet",
    ".feather",
    ".h5",
    ".hdf5",
    ".h5ad",
    ".loom",
    ".xlsx",
    ".xls",
    ".rds",
    ".rdata",
    ".npy",
    ".npz",
    ".pkl",
    ".pickle",
    ".png",
    ".jpg",
    ".jpeg",
    ".svg",
    ".pdf",
}
_REPOSITORY_SCAN_EXCLUDES = {
    ".git",
    ".mycelium",
    ".living",
    ".venv",
    "node_modules",
    "__pycache__",
}
_REPOSITORY_SCAN_ENTRY_LIMIT = 50_000

_INPUT_CALL_LEAVES = {
    "open_dataarray",
    "open_dataset",
    "open_mfdataset",
    "open_zarr",
    "read_10x_h5",
    "read_10x_mtx",
    "read_csv",
    "read_excel",
    "read_feather",
    "read_h5",
    "read_h5ad",
    "read_hdf",
    "read_json",
    "read_loom",
    "read_mtx",
    "read_orc",
    "read_parquet",
    "read_pickle",
    "read_sas",
    "read_stata",
    "read_table",
    "read_tsv",
}
_OUTPUT_CALL_LEAVES = {
    "savefig",
    "to_csv",
    "to_excel",
    "to_feather",
    "to_h5",
    "to_hdf",
    "to_json",
    "to_netcdf",
    "to_orc",
    "to_parquet",
    "to_pickle",
    "to_sas",
    "to_stata",
    "to_table",
    "to_tsv",
    "write_csv",
    "write_h5ad",
    "write_json",
    "write_parquet",
}
_PATH_KEYWORDS = {
    "file",
    "filename",
    "filepath_or_buffer",
    "fname",
    "path",
    "path_or_buf",
    "store",
}
_KNOWN_IO_MODULE_ROOTS = {
    "anndata",
    "dask",
    "geopandas",
    "matplotlib",
    "numpy",
    "pandas",
    "polars",
    "pyarrow",
    "scanpy",
    "xarray",
}
_DETERMINISTIC_PATH_CALLS = {
    "Path",
    "PurePath",
    "os.path.join",
    "pathlib.Path",
    "pathlib.PurePath",
}


def _repository_root(cwd: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    root = result.stdout.strip()
    return Path(root) if result.returncode == 0 and root else None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _io_call_direction(
    node: ast.Call,
    import_aliases: dict[str, str],
    locally_bound_roots: set[str],
) -> str | None:
    raw_name = _call_name(node.func)
    if not raw_name:
        return None
    parts = raw_name.split(".")
    if parts[0] in import_aliases:
        parts = import_aliases[parts[0]].split(".") + parts[1:]
    name = ".".join(parts)
    leaf = name.rsplit(".", 1)[-1]
    if leaf in _OUTPUT_CALL_LEAVES or name in {
        "np.save",
        "np.savez",
        "np.savez_compressed",
    }:
        # Object methods such as ``frame.to_csv`` are conventional writers.
        # A bare writer name, however, is evidence only when an import maps it
        # to a known data-I/O package.
        if "." in raw_name or parts[0] in _KNOWN_IO_MODULE_ROOTS:
            return "output"
    if leaf in _INPUT_CALL_LEAVES or name in {
        "ad.read",
        "anndata.read",
        "np.load",
        "sc.read",
        "scanpy.read",
    }:
        # Readers are module functions, not arbitrary object methods. Require
        # an actual supported import and reject any alias rebound elsewhere in
        # the source; familiar spellings alone do not prove data I/O.
        raw_root = raw_name.split(".", 1)[0]
        if (
            raw_root in import_aliases
            and raw_root not in locally_bound_roots
            and "." in name
            and parts[0] in _KNOWN_IO_MODULE_ROOTS
        ):
            return "input"
    return None


def _io_literal_filenames(source: str) -> tuple[set[str], set[str]]:
    """Collect data-like literals reachable from actual I/O call arguments."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), set()

    import_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname or alias.name.split(".", 1)[0]
                import_aliases[local_name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local_name = alias.asname or alias.name
                import_aliases[local_name] = f"{node.module}.{alias.name}"

    assignments: dict[str, list[ast.AST]] = {}
    shadowed_names = {
        node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)
    }
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and value is not None:
                    assignments.setdefault(target.id, []).append(value)
    locally_bound_roots = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    locally_bound_roots.update(shadowed_names)
    locally_bound_roots.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.ClassDef, ast.FunctionDef))
    )
    locally_bound_roots.update(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler) and node.name
    )
    for node in ast.walk(tree):
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
            locally_bound_roots.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            locally_bound_roots.add(node.rest)

    unresolved = object()

    def static_value(
        expression: ast.AST, resolving: frozenset[str] = frozenset()
    ) -> object:
        if isinstance(expression, ast.Constant):
            return expression.value
        if isinstance(expression, ast.Name):
            if expression.id in shadowed_names or expression.id in resolving:
                return unresolved
            definitions = assignments.get(expression.id, [])
            if len(definitions) != 1:
                return unresolved
            return static_value(definitions[0], resolving | {expression.id})
        if isinstance(expression, (ast.List, ast.Tuple)):
            values = [static_value(item, resolving) for item in expression.elts]
            if any(value is unresolved for value in values):
                return unresolved
            return values if isinstance(expression, ast.List) else tuple(values)
        if isinstance(expression, ast.Dict):
            keys = [static_value(key, resolving) for key in expression.keys]
            values = [static_value(value, resolving) for value in expression.values]
            if any(value is unresolved for value in keys + values):
                return unresolved
            try:
                return dict(zip(keys, values, strict=True))
            except (TypeError, ValueError):
                return unresolved
        if isinstance(expression, ast.Subscript):
            container = static_value(expression.value, resolving)
            index = static_value(expression.slice, resolving)
            if container is unresolved or index is unresolved:
                return unresolved
            try:
                return container[index]
            except (IndexError, KeyError, TypeError):
                return unresolved
        if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
            left = static_value(expression.left, resolving)
            right = static_value(expression.right, resolving)
            if isinstance(left, str) and isinstance(right, str):
                return left + right
            return unresolved
        return unresolved

    def collect(
        expression: ast.AST, resolving: frozenset[str] = frozenset()
    ) -> set[str] | None:
        if isinstance(expression, ast.Constant) and isinstance(expression.value, str):
            return {expression.value}
        if isinstance(expression, ast.Name):
            if expression.id in shadowed_names:
                return set()
            definitions = assignments.get(expression.id, [])
            if len(definitions) != 1 or expression.id in resolving:
                return set()
            return collect(definitions[0], resolving | {expression.id})
        if isinstance(
            expression,
            (
                ast.BoolOp,
                ast.DictComp,
                ast.GeneratorExp,
                ast.IfExp,
                ast.Lambda,
                ast.ListComp,
                ast.SetComp,
            ),
        ):
            return None
        if isinstance(expression, ast.Subscript):
            value = static_value(expression, resolving)
            if isinstance(value, str):
                return {value}
            if value is not unresolved:
                return set()
            # Dynamic selection from a literal container can choose among
            # multiple paths at runtime. Treat it as ambiguous rather than
            # collecting every branch from the container.
            if isinstance(expression.value, (ast.List, ast.Tuple, ast.Dict)):
                return None
            if isinstance(expression.value, ast.Name):
                definitions = assignments.get(expression.value.id, [])
                if len(definitions) == 1 and isinstance(
                    definitions[0], (ast.List, ast.Tuple, ast.Dict)
                ):
                    return None
            return set()
        if isinstance(expression, ast.BinOp):
            if isinstance(expression.op, ast.Add):
                value = static_value(expression, resolving)
                return {value} if isinstance(value, str) else None
            if not isinstance(expression.op, ast.Div):
                return None
            left = collect(expression.left, resolving)
            right = collect(expression.right, resolving)
            if left is None or right is None:
                return None
            return left | right
        if isinstance(expression, ast.Call):
            call_name = _call_name(expression.func)
            deterministic = call_name in _DETERMINISTIC_PATH_CALLS or (
                call_name is not None and call_name.endswith(".joinpath")
            )
            children = list(expression.args) + [
                keyword.value for keyword in expression.keywords
            ]
            if not deterministic:
                return set() if not children else None
            values: set[str] = set()
            for child in children:
                child_values = collect(child, resolving)
                if child_values is None:
                    return None
                values.update(child_values)
            return values
        if isinstance(expression, ast.Attribute):
            return collect(expression.value, resolving)
        return set()

    inputs: set[str] = set()
    outputs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        direction = _io_call_direction(node, import_aliases, locally_bound_roots)
        if direction is None:
            continue
        path_expressions = list(node.args[:1])
        path_expressions.extend(
            keyword.value
            for keyword in node.keywords
            if keyword.arg in _PATH_KEYWORDS
        )
        values: set[str] = set()
        for expression in path_expressions:
            expression_values = collect(expression)
            if expression_values is None:
                values.clear()
                break
            values.update(expression_values)
        destination = inputs if direction == "input" else outputs
        destination.update(values)

    def eligible(value: str) -> bool:
        value = value.strip()
        if not value or "\x00" in value or "$" in value:
            return False
        if Path(value).is_absolute() or re.match(
            r"^[A-Za-z][A-Za-z0-9+.-]*://", value
        ):
            return False
        return Path(value).suffix.lower() in _DATA_FILE_SUFFIXES

    return (
        {value.strip() for value in inputs if eligible(value)},
        {value.strip() for value in outputs if eligible(value)},
    )


def _repository_literal_io(
    source: str,
    cwd: Path,
    static_inputs: list[str],
    static_outputs: list[str],
) -> tuple[list[str], list[str]]:
    """Recover unique existing paths named by dynamic Path expressions.

    This is a conservative post-execution fallback. It never guesses when a
    basename occurs more than once in the repository. Direction comes from the
    reader/writer call, never from a directory name. The literal regex scanner
    remains authoritative for direct reader/writer calls, which are removed
    before repository discovery so resolved I/O never pays for a tree scan.
    """
    input_literals, output_literals = _io_literal_filenames(source)

    def identity(value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        return os.path.normpath(os.path.abspath(path))

    known_inputs = {identity(value) for value in static_inputs}
    known_outputs = {identity(value) for value in static_outputs}
    input_literals = {
        value for value in input_literals if identity(value) not in known_inputs
    }
    output_literals = {
        value for value in output_literals if identity(value) not in known_outputs
    }
    literals = input_literals | output_literals
    if not literals:
        return [], []
    root = _repository_root(cwd)
    if root is None:
        return [], []

    wanted_basenames = {Path(value).name for value in literals}
    matches: dict[str, list[Path]] = {name: [] for name in wanted_basenames}
    directories = [root]
    entries_seen = 0
    while directories:
        directory = directories.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    entries_seen += 1
                    if entries_seen > _REPOSITORY_SCAN_ENTRY_LIMIT:
                        return [], []
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in _REPOSITORY_SCAN_EXCLUDES:
                            directories.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False) and entry.name in matches:
                        matches[entry.name].append(Path(entry.path))
        except OSError:
            # An incomplete walk cannot prove basename uniqueness.
            return [], []

    inputs: list[str] = []
    outputs: list[str] = []
    for value in sorted(input_literals):
        candidates = matches.get(Path(value).name, [])
        if len(candidates) == 1:
            inputs.append(str(candidates[0]))
    for value in sorted(output_literals):
        candidates = matches.get(Path(value).name, [])
        if len(candidates) == 1:
            outputs.append(str(candidates[0]))
    return _dedupe(inputs), _dedupe(outputs)


def _merge_recovered_paths(
    static_paths: list[str], recovered_paths: list[str], cwd: Path
) -> tuple[list[str], list[str]]:
    """Merge repository recovery without duplicating equivalent static paths."""

    def identity(value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            path = cwd / path
        return os.path.normpath(os.path.abspath(path))

    merged = list(static_paths)
    seen = {identity(value) for value in static_paths}
    added: list[str] = []
    for value in recovered_paths:
        canonical = identity(value)
        if canonical in seen:
            continue
        seen.add(canonical)
        merged.append(value)
        added.append(value)
    return merged, added


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
    cwd: Path | None,
    git_sha: str | None,
) -> dict | None:
    """Build one NDJSON event dict for a single (script_path, inline) detection.

    ``cwd`` is the directory the run's relative I/O resolves against, or
    ``None`` when a runner moved the run somewhere unknowable; its literal
    paths are then left unresolved rather than recorded under a wrong root.
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
    if cwd is None:
        return _event_dict(
            args,
            script_path,
            script_sha,
            script_source_embed,
            git_sha,
            inputs=[],
            outputs=[],
            io_detection="unresolved",
            lineage_warnings=[
                "The runner's working directory could not be resolved "
                "statically, so the script's literal I/O paths were not "
                "recorded."
            ],
            filters=filters,
            seeds=seeds,
        )
    recovered_inputs, recovered_outputs = _repository_literal_io(
        source, cwd, inputs, outputs
    )
    inputs, added_inputs = _merge_recovered_paths(inputs, recovered_inputs, cwd)
    outputs, added_outputs = _merge_recovered_paths(outputs, recovered_outputs, cwd)
    if added_inputs or added_outputs:
        io_detection = "static+repository"
    else:
        io_detection = "static" if inputs or outputs else "unresolved"
    lineage_warnings = []
    if io_detection == "unresolved":
        lineage_warnings.append(
            "No literal input/output paths were detected; the script may "
            "resolve paths dynamically or delegate I/O through imports."
        )

    return _event_dict(
        args,
        script_path,
        script_sha,
        script_source_embed,
        git_sha,
        inputs=[file_record(p, cwd) for p in inputs],
        outputs=[file_record(p, cwd) for p in outputs],
        io_detection=io_detection,
        lineage_warnings=lineage_warnings,
        filters=filters,
        seeds=seeds,
    )


def _event_dict(
    args: argparse.Namespace,
    script_path: Path | None,
    script_sha: str | None,
    script_source_embed: str | None,
    git_sha: str | None,
    *,
    inputs: list[dict],
    outputs: list[dict],
    io_detection: str,
    lineage_warnings: list[str],
    filters: list[str],
    seeds: list[int],
) -> dict:
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
        "inputs": inputs,
        "outputs": outputs,
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
