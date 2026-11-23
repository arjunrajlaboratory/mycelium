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
import bisect
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Generator, Iterator, NamedTuple

SIZE_LIMIT_BYTES = 100 * 1024 * 1024
EMBED_LIMIT_BYTES = 100 * 1024

# --- Script-path / inline-source extraction ---
_SHELL_DOUBLE_QUOTED_COMPONENT = r'"(?:\\.|[^"\\])*"'
_SHELL_SINGLE_QUOTED_COMPONENT = r"'[^']*'"
# An escape pairs a backslash with whitespace, shell punctuation, or another
# backslash; before any other character, a newline, or the end, the
# backslash may stand alone. `(?=.)` keeps `\` + newline an escape only in
# DOTALL patterns, as `\\.` was. Unlike the old `\\.|[^...]`, an escaped
# backslash (`\\`) can no longer also escape the next character (`\\ a.py`
# as one word), which the shell never does; only `\` + newline (DOTALL) has
# two parses, the second of which ends the word. Letting both alternatives
# match every `\x` made runs
# of backslashes backtrack exponentially (`R ` + 40 backslashes hung the
# hook), while consuming `\.` as one atom would hide the dot in `a\.py` from
# the extension match.
_SHELL_BARE_COMPONENT = (
    r"(?:\\(?=.)[\s|&;()<>\"'\\]"
    r"|\\(?=[^\s|&;()<>\"'\\]|\n|$)|[^\s|&;()<>\"'\\])"
)
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
# `--args` ends Rscript's own options, so a later `-e`/file is an argument;
# `--print-home` makes Rscript print R_HOME and exit.
_NONTERMINAL_LONG_OPTION = (
    r"--(?!(?:args|help(?:-all)?|print-home|version)(?:=|\s|$))\S+"
)
_NONTERMINAL_LONG_OPTIONS = rf"(?:{_NONTERMINAL_LONG_OPTION}\s+)*"
_JUPYTER_VALUE_LONG_OPTION_NAMES = (
    r"(?:config|format|log-level|nbformat|output|output-dir|post|"
    r"reveal-prefix|template|template-file|theme|to|writer)"
)
_JUPYTER_VALUE_OPTION_NAME = rf"(?:-o|--{_JUPYTER_VALUE_LONG_OPTION_NAMES})"
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
# The known value options are excluded here because `_JUPYTER_VALUE_OPTION`
# already consumes `--output=x` identically: two alternatives for one word
# made each option double the backtracking (14 options took seconds).
_JUPYTER_ASSIGNED_OPTION = (
    rf"--(?!(?:help(?:-all)?|version|{_JUPYTER_VALUE_LONG_OPTION_NAMES})=)"
    rf"[^=\s]+={_SHELL_WORD}"
)
_JUPYTER_OPTIONS = (
    rf"(?:(?:{_JUPYTER_VALUE_OPTION}|{_JUPYTER_FLAG_OPTION}|"
    rf"{_JUPYTER_ASSIGNED_OPTION})\s+)*"
)
# R's own startup grammar differs from Rscript's. The `R` front-end script
# takes separated values for -g/--gui and --arch (and -e/-f/-d), and the R
# binary for --encoding and the memory/connection limits; both accept either
# `--opt=VALUE` or `--opt VALUE`. R warns about and ignores any other unknown
# option, short clusters such as `-qs` included, and still runs. Options that
# may never precede a payload: `--args` ends option parsing (a later -e/-f is
# an argument, not code), `-d`/`--debugger` hands the run to a debugger,
# `--print-home` only prints R_HOME, and `-h`/`--help`/`--version` exit.
_R_VALUE_LONG_OPTION_NAME = (
    r"--(?:arch|encoding|gui|max-connections|max-nsize|max-ppsize|max-vsize|"
    r"min-nsize|min-vsize)"
)
_R_OPTION = (
    r"(?:-(?![defgh](?:\s|$))[A-Za-z][A-Za-z0-9]*(?=\s|$)"
    r"|--(?!(?:args|arch|debugger|encoding|file|gui|help|max-connections|"
    r"max-nsize|max-ppsize|max-vsize|min-nsize|min-vsize|print-home|version)"
    r"(?:=|\s|$))\S+"
    rf"|{_R_VALUE_LONG_OPTION_NAME}(?:=|\s+){_SHELL_WORD}"
    rf"|-g\s+{_SHELL_WORD})"
)
_R_OPTIONS = rf"(?:{_R_OPTION}\s+)*"
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
    rf"{_R_OPTIONS}-e\s+{_INLINE_SOURCE_WORD}",
    re.DOTALL,
)
RX_RSCRIPT_E = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<rscript_exe>{_R_SCRIPT_EXE})\s+"
    rf"{_NONTERMINAL_LONG_OPTIONS}-e\s+{_INLINE_SOURCE_WORD}",
    re.DOTALL,
)
RX_R_SCRIPT_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<rscript_exe>{_R_SCRIPT_EXE})\s+"
    rf"{_NONTERMINAL_LONG_OPTIONS}(?P<path>{_R_SCRIPT_PATH})"
)
# `R -f FILE` / `R --file=FILE`; R rejects the separated `--file FILE` form.
RX_R_FILE_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<r_exe>{_R_EXE})\s+"
    rf"{_R_OPTIONS}(?:-f\s+|--file=)(?P<path>{_R_SCRIPT_PATH})"
)
# `R [options] CMD BATCH [options] [--] infile [outfile]`: the R front end
# hands everything after CMD to Rcmd, so R options may precede CMD. This
# pattern only locates a BATCH call with an R-script-shaped word after it;
# `_r_batch_script` then reads the argv the way R's BATCH script does to
# decide whether it runs and which file R actually reads.
# One whitespace-split step (`[^\s;&|]+[ \t]+`) that shell lexing reads as
# whole word components ending in an escaped blank, as in `a\ `: the word
# after it continues the same shell word. A path attempt there would rescan
# the rest of that word, which the attempt one step earlier already failed
# on, so `R CMD BATCH a\ a\ ...` went quadratic. The attempt is skipped
# rather than the step, which keeps every match identical.
_BATCH_CONTINUED_STEP = (
    r"""(?:[^\s|&;()<>"'\\]|\\[()<>"'\\]|\\(?=[^\s|&;()<>"'\\])"""
    r"""|"(?:\\[^\s;&|]|[^"\\\s;&|])*"|'[^'\s;&|]*')*"""
    r"\\[ \t](?![ \t])"
)
RX_R_CMD_BATCH_PATH = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<r_exe>{_R_EXE})\s+{_R_OPTIONS}CMD\s+BATCH\s+"
    # Never cross a command boundary (`;`, `&&`, `|`, newline) to find a path.
    rf"(?:(?:{_BATCH_CONTINUED_STEP})*(?!{_BATCH_CONTINUED_STEP})[^\s;&|]+[ \t]+)*?"
    rf"(?P<path>{_R_SCRIPT_PATH})"
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
_SHELL_SCRIPT_PATH = _shell_path_pattern(r"\.sh")
RX_SHELL_SCRIPT_COMMAND = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<runner_exe>{_SHELL_SCRIPT_PATH})"
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
        "data_contract_check.py",
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


# `P=$(cat .mycelium/plugin-root)`, optionally with a literal subdirectory
# after it (`S="$(cat .mycelium/plugin-root)/skills/core/scripts"`), or a
# literal absolute path.
_ROOT_ASSIGNMENT_RX = re.compile(
    r"""(?:^|[;&|\n(]|&&|\|\|)\s*([A-Za-z_][A-Za-z0-9_]*)="""
    r"""(?:"\$\(cat \.mycelium/plugin-root\)(?P<suffix_q>/[A-Za-z0-9_./-]*)?\""""
    r"""(?P<suffix_after>/[A-Za-z0-9_./-]*)?"""
    r"""|\$\(cat \.mycelium/plugin-root\)(?P<suffix>/[A-Za-z0-9_./-]*)?"""
    r"""|'?(/[^\s;&|$'"`]+)'?)(?=[\s;&|]|$)"""
)


# An interpreter held in a variable: `PY=/x/venv/bin/python; $PY run.py`.
_INTERPRETER_BASENAME_RX = re.compile(r"python(?:\d+(?:\.\d+)*)?|Rscript|R")
_LITERAL_VALUE_RX = re.compile(r"'([^']*)'|\"([^\"$`\\]*)\"|([^\s;&|<>()'\"$`\\*?\[\]{}~]+)")


_VAR_ASSIGNMENT_AT_RX = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)=")
_VAR_USE_AT_RX = re.compile(
    r"(?:\"\$(?:\{(?P<qb>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<qn>[A-Za-z_][A-Za-z0-9_]*))\""
    r"|\$(?:\{(?P<b>[A-Za-z_][A-Za-z0-9_]*)\}|(?P<n>[A-Za-z_][A-Za-z0-9_]*)))(?=[ \t])")
# A bare mention of a name in unquoted shell text: `unset PY`, `read PY`,
# `declare PY=x`, `PY+=x`, `for PY in`, `cond && PY=x`. `$PY`/`${PY}` are
# uses, not mentions (the `$` or `{` before the name excludes them).
_VAR_MENTION_RX = re.compile(
    r"(?<![^\s;&|()`])([A-Za-z_][A-Za-z0-9_]*)(?=[\s;&|()=+\[<>]|$)")
_COMMAND_WORD_AT_RX = re.compile(r"[^\s;&|()<>`]+")
_CONTROL_OPEN_WORDS = frozenset(
    {"if", "while", "until", "for", "case", "select", "{", "function", "coproc"})
_CONTROL_CLOSE_WORDS = frozenset({"fi", "done", "esac", "}"})


def _shell_command_starts(command: str, masked: str,
                          visible: str) -> list[tuple[int, str, int, int]]:
    """(offset, boundary, nesting depth, control depth) for each simple
    command start in ``command``, found on ``masked`` (quotes, comments and
    heredoc bodies blanked, offsets kept) so quoted text never starts one.
    ``visible`` blanks only comments and heredoc bodies: a start whose raw
    character is blanked there is data, not shell. Nesting depth counts
    open parentheses (subshells, `$(`) and backticks; control depth counts
    open `if`/`while`/`for`/`case`/`{` bodies. One linear pass."""
    starts: list[tuple[int, str, int, int]] = []
    length = len(command)
    depth = 0
    in_backtick = False
    control = 0
    boundary = "^"
    at_start = True
    index = 0
    while index < length:
        char = masked[index]
        if at_start:
            raw = command[index]
            if raw in " \t":
                index += 1
                continue
            if raw not in ";&|()`\n" and visible[index] != " ":
                starts.append((index, boundary, depth + in_backtick, control))
                word = _COMMAND_WORD_AT_RX.match(masked, index)
                if word is not None:
                    if word.group() in _CONTROL_OPEN_WORDS:
                        control += 1
                    elif word.group() in _CONTROL_CLOSE_WORDS:
                        control = max(0, control - 1)
            at_start = False
        if char == "(":
            depth += 1
            boundary, at_start = "(", True
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "`":
            in_backtick = not in_backtick
            boundary, at_start = "`", True
        elif char == "\n":
            boundary, at_start = "\n", True
        elif char == ";":
            boundary, at_start = (";;" if masked[index + 1: index + 2] == ";" else ";"), True
            if boundary == ";;":
                index += 1
        elif char in "&|":
            previous = masked[index - 1] if index else ""
            following = masked[index + 1] if index + 1 < length else ""
            if char == "&" and (previous in "<>" or following == ">"):
                pass  # a redirection (`2>&1`, `&>`), not a control operator
            elif char == "|" and previous == ">":
                pass  # `>|`
            elif following == char:
                boundary, at_start = char * 2, True
                index += 1
            else:
                boundary, at_start = char, True
        index += 1
    return starts


def _expand_interpreter_variables(command: str) -> str:
    """The command with each `$VAR` used as an interpreter replaced by the
    literal it was assigned earlier in the same command, so `PY=/x/python;
    $PY run.py` is detected as the analysis run it is. Only a plain,
    unconditional assignment statement counts: `NAME=literal` (optionally
    `export`ed) at the top level, ended by `;` or a newline, the last one to
    that name before the use, with no expansion in its value, and a value
    whose basename is python, Rscript, or R. Anything else is left as
    written (and stays undetected, as before).

    Assignments and uses are only seen at command starts in shell text: not
    in quotes, comments, heredoc bodies, or (for uses) substitutions and
    subshells. Any other unquoted mention of the name (`unset`, `read`,
    `declare`, `local`, `readonly`, `typeset`, `+=`, a conditional, piped
    or nested assignment, a loop variable) voids it until the next plain
    assignment. One forward pass over the command."""
    if "$" not in command or "=" not in command:
        return command
    without_heredocs = _blank_heredoc_bodies(command)
    masked = _unquoted_shell_text(without_heredocs)
    visible = _strip_unquoted_shell_comments(without_heredocs)
    if masked is None or visible is None:
        return command
    starts = _shell_command_starts(command, masked, visible)
    mentions = [(m.start(1), m.group(1)) for m in _VAR_MENTION_RX.finditer(masked)]
    values: dict[str, str | None] = {}
    out: list[str] = []
    last = 0
    consumed = -1  # the offset of the name a modeled assignment just set
    mention_index = 0
    for offset, boundary, depth, control in starts:
        # Mentions before this command start apply first, in order.
        while mention_index < len(mentions) and mentions[mention_index][0] < offset:
            position, name = mentions[mention_index]
            mention_index += 1
            if position != consumed:
                values[name] = None
        assignment = _VAR_ASSIGNMENT_AT_RX.match(command, offset)
        if assignment is not None:
            name = assignment.group(1)
            value = None
            if boundary in ("^", ";", "\n") and depth == 0 and control == 0:
                value = _plain_interpreter_literal(command, assignment.end())
            values[name] = value
            consumed = assignment.start(1)
            continue
        if depth:
            continue
        use = _VAR_USE_AT_RX.match(command, offset)
        if use is None:
            continue
        name = use.group("qb") or use.group("qn") or use.group("b") or use.group("n")
        value = values.get(name)
        if value is None:
            continue
        out.append(command[last:use.start()])
        out.append(shlex.quote(value))
        last = use.end()
    if not out:
        return command
    out.append(command[last:])
    return "".join(out)


def _plain_interpreter_literal(command: str, start: int) -> str | None:
    """The literal value at ``start`` (just after `NAME=`) when it is the
    whole statement, ended by `;` or a newline, and names an interpreter."""
    literal = _LITERAL_VALUE_RX.match(command, start)
    if literal is None:
        return None
    if not re.compile(r"[ \t]*(?:;|\n)").match(command, literal.end()):
        return None  # a prefix assignment (`PY=x cmd`) or more words follow
    value = next(g for g in literal.groups() if g is not None)
    if not value or _INTERPRETER_BASENAME_RX.fullmatch(os.path.basename(value)) is None:
        return None
    return value


def _variable_script_path(command: str, offset: int, raw_path: str,
                          cwd: Path | None) -> Path | None:
    """`$P/skills/...` resolved through an assignment earlier in the same
    command: `P=$(cat .mycelium/plugin-root)` (read from the working
    directory) or `P=/literal/path`. None for anything else; this is used
    only to recognize Mycelium's own helpers, never to infer analysis I/O."""
    match = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?(/.*)", raw_path)
    if match is None:
        return None
    name, rest = match.groups()
    prefix = command[:offset]
    # The value the interpreter receives is the *last* assignment before it:
    # any later one this resolver cannot model (`P=.`, `P=$OTHER`) voids it.
    any_assignment = re.compile(
        rf"(?:^|[;&|\n(]|&&|\|\|)\s*{re.escape(name)}=")
    last_any = None
    for found in any_assignment.finditer(prefix):
        last_any = found.start()
    if last_any is None:
        return None
    modeled = [a for a in _ROOT_ASSIGNMENT_RX.finditer(prefix) if a.group(1) == name]
    if not modeled or modeled[-1].start() != last_any:
        return None
    assignment = modeled[-1]
    literal = assignment.group(5)
    suffix = "".join(assignment.group(g) or "" for g in ("suffix_q", "suffix_after", "suffix"))
    if literal is not None:
        value = literal
    elif cwd is not None:
        # The bounded, link-refusing pointer reader the accessor path uses.
        pointer = _pointer_expansion_value(cwd, "cat")
        value = pointer + suffix if pointer else None
    else:
        value = None
    if not value or "\0" in value or "/../" in f"{suffix}/":
        return None
    return Path(os.path.realpath(value + rest))


IGNORED_SCRIPT_BASENAMES = {"setup.py"}
ANALYSIS_PATTERNS = (
    RX_PYTHON_C,
    RX_PYTHON_M,
    RX_PYTHON_SCRIPT_PATH,
    RX_R_E,
    RX_RSCRIPT_E,
    RX_R_SCRIPT_PATH,
    RX_R_FILE_PATH,
    RX_R_CMD_BATCH_PATH,
    RX_JUPYTER_SCRIPT_PATH,
)

# --- Linear-time candidate scanning ---
# Every analysis and runner pattern has the shape
#   (?<![A-Za-z0-9_.-]) (?P<exe> COMPONENT* ENDING (?=boundary)) TAIL
# A quoted component can span whitespace, so from a start inside a run of
# quoted words (`"$x" "$x" ...`) the leading COMPONENT* reaches the end of the
# run, and `finditer` repeats that walk from every start: quadratic, and
# 5,000 such words took ~58 s inside a hook that runs on every shell command.
# `_scan_word_pattern` yields exactly what `finditer` yields but skips starts
# that provably cannot match. If an attempt at `i` (lookbehind satisfied)
# fails, an attempt at any later component boundary `j` of a parse from `i`
# fails too: every word `j` can form, prefixed by the components between `i`
# and `j`, is a word `i` can form, and TAIL does not depend on the start.
_WORD_LOOKBEHIND = "(?<![A-Za-z0-9_.-])(?P<"
_LOOKBEHIND_BLOCKED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)
_WORD_COMPONENT_RX = {
    False: re.compile(_SHELL_WORD_COMPONENT),
    True: re.compile(_SHELL_WORD_COMPONENT, re.DOTALL),
}


@functools.lru_cache(maxsize=4)
def _double_quote_closes(text: str, dotall: bool) -> tuple[int, ...]:
    """``closes[j]``: the quote closing a ``"`` component whose body starts at j.

    Mirrors ``_SHELL_DOUBLE_QUOTED_COMPONENT``: a backslash pairs with the next
    character (not a newline unless DOTALL), and -1 means unterminated.
    """
    n = len(text)
    closes = [-1] * (n + 2)
    for j in range(n - 1, -1, -1):
        char = text[j]
        if char == '"':
            closes[j] = j
        elif char == "\\":
            if j + 1 < n and (dotall or text[j + 1] != "\n"):
                closes[j] = closes[j + 2]
        else:
            closes[j] = closes[j + 1]
    return tuple(closes)


def _scan_word_pattern(pattern: re.Pattern, text: str) -> Iterator[re.Match[str]]:
    """Yield ``pattern.finditer(text)``'s matches without its quadratic retries.

    Only for patterns built as ``_WORD_LOOKBEHIND`` + a
    ``_shell_word_ending_pattern`` word (see the block comment above).
    """
    n = len(text)
    dotall = bool(pattern.flags & re.DOTALL)
    component = _WORD_COMPONENT_RX[dotall]
    dead = bytearray(n + 1)
    # A start at `"` whose component closes at a quote where an earlier,
    # failed `"` start also closed can only form a subset of that start's
    # words: its body is a suffix of the earlier body on the same parse.
    dead_closes: set[int] = set()
    pos = 0
    while pos <= n:
        if dead[pos] or (pos and text[pos - 1] in _LOOKBEHIND_BLOCKED):
            pos += 1
            continue
        close = -1
        if pos < n and text[pos] == '"':
            close = _double_quote_closes(text, dotall)[pos + 1]
            if close < 0 or close in dead_closes:
                # An unterminated `"` starts no component, and no ending
                # alternative begins with one.
                pos += 1
                continue
        match = pattern.match(text, pos)
        if match is not None:
            yield match
            pos = match.end() if match.end() > pos else pos + 1
            continue
        if close >= 0:
            dead_closes.add(close)
        cursor = pos
        while not dead[cursor]:
            dead[cursor] = 1
            step = component.match(text, cursor)
            if step is None:
                break
            cursor = step.end()
        pos += 1


_PATTERN_EXECUTABLES = {
    RX_PYTHON_C: ("python_exe", re.compile(_PYTHON_EXE_NAME)),
    RX_PYTHON_M: ("python_exe", re.compile(_PYTHON_EXE_NAME)),
    RX_PYTHON_SCRIPT_PATH: ("python_exe", re.compile(_PYTHON_EXE_NAME)),
    RX_R_E: ("r_exe", re.compile(r"R")),
    RX_RSCRIPT_E: ("rscript_exe", re.compile(r"Rscript")),
    RX_R_SCRIPT_PATH: ("rscript_exe", re.compile(r"Rscript")),
    RX_R_FILE_PATH: ("r_exe", re.compile(r"R")),
    RX_R_CMD_BATCH_PATH: ("r_exe", re.compile(r"R")),
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
                # A line continuation vanishes and keeps the word-start state.
                if command[index + 1 : index + 2] != "\n":
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


@functools.lru_cache(maxsize=4)
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
R_SEED_REGEXES = [
    re.compile(r"""(?<![\w.])set\.seed\s*\(\s*(?:seed\s*=\s*)?(\d+)L?\s*[,)]"""),
]

# --- R readers and writers ---
#
# Call form only, with a literal path: the first argument for readers (or a
# named path argument), and for writers the argument after a simple object
# (or a named path argument anywhere in the call). Anything computed
# (`file.path(...)`, `paste0(...)`, a call as the object) claims nothing,
# so the event stays unresolved rather than naming a guessed path. The
# lookbehind keeps methods and look-alikes (`obj$read.csv`, `my.read.csv`,
# `pd.read_parquet`) out; an explicit `pkg::` prefix is allowed.
_R_LITERAL = r"""["']([^"'\n]+)["']\s*(?=[,)])"""
_R_OBJECT = r"[A-Za-z.][\w.]*(?:\$[\w.]+|\[[^\[\]()\"'\n]*\])*"


def _r_call(names: str, path_args: str, namespace: str = r"[A-Za-z][\w.]*") -> str:
    return (
        rf"(?<![\w.$@:])(?:{namespace}::)?(?:{names})\s*\(\s*"
        rf"(?:(?:{path_args})\s*=\s*)?{_R_LITERAL}"
    )


def _r_writer(names: str, path_args: str) -> list[re.Pattern]:
    call = rf"(?<![\w.$@:])(?:[A-Za-z][\w.]*::)?(?:{names})\s*\("
    return [
        # write.csv(df, "out.csv"), saveRDS(object = fit, "x.rds")
        re.compile(
            rf"{call}\s*(?:[A-Za-z.][\w.]*\s*=\s*)?{_R_OBJECT}\s*,\s*"
            rf"(?:(?:{path_args})\s*=\s*)?{_R_LITERAL}"
        ),
        # write.csv(df[df$x > 1, ], file = "out.csv"): a named path argument
        # at depth one, after arguments with no nested call or string.
        re.compile(rf"{call}[^()\"'\n]*?\b(?:{path_args})\s*=\s*{_R_LITERAL}"),
    ]


R_INPUT_REGEXES = [
    re.compile(_r_call(r"read\.(?:csv2?|table|delim2?|fwf|dcf)", "file")),
    re.compile(
        _r_call(r"read_(?:csv2?|tsv|delim|table|fwf|lines|rds|file|excel|xlsx?|parquet|feather)",
                "file|path")
    ),
    re.compile(_r_call(r"fread", "file|input")),
    re.compile(_r_call(r"readRDS|load|readMM|h5read|vroom", "file")),
    re.compile(_r_call(r"Read10X(?:_h5)?", r"data\.dir|filename")),
]
R_OUTPUT_REGEXES = [
    *_r_writer(r"write\.(?:csv2?|table)", "file"),
    *_r_writer(
        r"write_(?:csv2?|tsv|delim|rds|lines|excel_csv|parquet|feather)", "file|path|sink"
    ),
    *_r_writer(r"fwrite|saveRDS|h5write", "file"),
    # save(a, b, file = "x.RData"): the path is only ever named.
    re.compile(
        r"(?<![\w.$@:])(?:base::)?save\s*\([^()\"'\n]*?\bfile\s*=\s*" + _R_LITERAL
    ),
    re.compile(_r_call(r"ggsave", "filename")),
    re.compile(
        r"(?<![\w.$@:])(?:[A-Za-z][\w.]*::)?ggsave\s*\([^()\"'\n]*?\bfilename\s*=\s*"
        + _R_LITERAL
    ),
    re.compile(_r_call(r"pdf|png|jpeg|tiff|bmp|svg|cairo_pdf", "file|filename")),
]


def _blank(source: str, spans: list[tuple[int, int]]) -> str:
    """Replace each span with spaces, keeping newlines and offsets."""
    if not spans:
        return source
    chars = list(source)
    for start, end in spans:
        for index in range(start, end):
            if chars[index] != "\n":
                chars[index] = " "
    return "".join(chars)


class _SourceSpans(NamedTuple):
    comments: list[tuple[int, int]]
    strings: list[tuple[int, int]]


def _python_spans(source: str) -> _SourceSpans | None:
    """Comment and string spans from Python's own tokenizer, or None.

    An f-string or t-string counts as one string span from its start token
    to its matching end token, so a call inside its replacement field is not
    claimed (a missed path stays unresolved; it is never invented).
    """
    import io
    import tokenize

    line_starts = [0]
    for line in source.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(position: tuple[int, int]) -> int:
        return line_starts[position[0] - 1] + position[1]

    starts = {getattr(tokenize, name) for name in ("FSTRING_START", "TSTRING_START")
              if hasattr(tokenize, name)}
    ends = {getattr(tokenize, name) for name in ("FSTRING_END", "TSTRING_END")
            if hasattr(tokenize, name)}
    comments: list[tuple[int, int]] = []
    strings: list[tuple[int, int]] = []
    open_starts: list[int] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                comments.append((offset(token.start), offset(token.end)))
            elif token.type == tokenize.STRING and not open_starts:
                strings.append((offset(token.start), offset(token.end)))
            elif token.type in starts:
                open_starts.append(offset(token.start))
            elif token.type in ends and open_starts:
                begin = open_starts.pop()
                if not open_starts:
                    strings.append((begin, offset(token.end)))
    except (tokenize.TokenError, IndentationError, SyntaxError, IndexError):
        return None
    return _SourceSpans(comments, strings)


_R_RAW_STRING_RX = re.compile(r"""[rR](["'])(-*)([(\[{])""")
_R_RAW_CLOSERS = {"(": ")", "[": "]", "{": "}"}


def _r_spans(source: str) -> _SourceSpans | None:
    """Comment and string spans of R source, or None when one never ends.

    Strings ('...', "..." with backslash escapes), raw strings (r"(...)",
    with optional dashes and any of the three bracket pairs, R >= 4.0) and
    backtick names are skipped; `#` outside them starts a comment.
    """
    comments: list[tuple[int, int]] = []
    strings: list[tuple[int, int]] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char == "#":
            end = source.find("\n", index)
            end = length if end == -1 else end
            comments.append((index, end))
            index = end
            continue
        if char in "rR" and (index == 0 or not (source[index - 1].isalnum()
                                                or source[index - 1] in "._")):
            raw = _R_RAW_STRING_RX.match(source, index)
            if raw:
                closer = _R_RAW_CLOSERS[raw.group(3)] + raw.group(2) + raw.group(1)
                end = source.find(closer, raw.end())
                if end == -1:
                    return None
                strings.append((index, end + len(closer)))
                index = end + len(closer)
                continue
        if char in "\"'`":
            begin = index
            index += 1
            while index < length and source[index] != char:
                index += 2 if source[index] == "\\" and char != "`" else 1
            if index >= length:
                return None
            index += 1
            if char != "`":
                strings.append((begin, index))
            continue
        index += 1
    return _SourceSpans(comments, strings)


def _source_spans(source: str, language: str | None) -> _SourceSpans | None:
    """Spans for a known language; None when unknown or the lexer fails.

    A lexer that cannot finish (unterminated string, a tokenizer error)
    yields None, so the source is scanned as written and a real call is
    never hidden by a confused lexer.
    """
    if language == "python":
        return _python_spans(source)
    if language == "r":
        return _r_spans(source)
    return None


def _inside(spans: list[tuple[int, int]], index: int) -> bool:
    position = bisect.bisect_right(spans, (index, float("inf"))) - 1
    return position >= 0 and spans[position][0] <= index < spans[position][1]


def sha256_file(p: Path) -> str | None:
    try:
        info = p.stat()
        # Only a regular file can be read to its end: a FIFO or device named
        # by a command would block this hook forever.
        if not stat.S_ISREG(info.st_mode) or info.st_size > SIZE_LIMIT_BYTES:
            return None
        h = hashlib.sha256()
        with _open_regular(p) as f:
            if f is None:
                return None
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


@contextlib.contextmanager
def _open_regular(p: Path) -> Iterator[Any]:
    """Open ``p`` for reading only if it is a regular file when opened.

    The open itself never blocks (O_NONBLOCK), and the check is on the
    opened descriptor, so a path swapped for a FIFO between a stat and the
    open yields None instead of a hook that waits forever."""
    fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            yield None
            return
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as handle:
        yield handle


def is_analysis(bash_cmd: str) -> bool:
    return any(
        _match_has_expected_executable(match, pattern)
        for pattern in ANALYSIS_PATTERNS
        for match in _scan_word_pattern(pattern, bash_cmd)
    ) or any(
        _runner_word(match) is not None
        for pattern in RUNNER_PATTERNS
        for match in _scan_word_pattern(pattern, bash_cmd)
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


def _cdpath_may_be_set(text: str) -> bool:
    """Whether shell text may set CDPATH for a later ``cd`` in the same shell.

    Any mention of the variable counts (an assignment, ``export``,
    ``unset``, ``declare``, ``${CDPATH:=...}``), as does running code this
    hook cannot see (``source``, ``.``, ``eval``) or a variable-setting
    builtin with a computed name. Errs toward True, like `_home_may_change`.
    """
    if re.search(r"(?<![A-Za-z0-9_])CDPATH(?![A-Za-z0-9_])", text):
        return True
    if re.search(r"(?<![A-Za-z0-9_.-])(source|eval|fc|enable|trap)(?![A-Za-z0-9_.-])", text):
        return True
    if re.search(r"(?:^|[\s;&|(){}!`])\.(?:\s|$)", text):
        return True  # `. file`: the POSIX spelling of source
    for segment in re.split(r"[;&|\n()]+", text):
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", segment))
        if words & _DYNAMIC_ASSIGN_BUILTINS and re.search(r"[$`]", segment):
            return True
    return False


def _cdpath_env_set() -> bool:
    """The hook's own CDPATH: the agent's shell may well share it."""
    return bool(os.environ.get("CDPATH"))


def _cd_operand_searches_cdpath(operand: str) -> bool:
    """bash searches $CDPATH for a relative operand that does not begin
    with ``/``, ``./`` or ``../`` (and is not ``.`` or ``..``)."""
    if operand in (".", "..") or operand.startswith(("/", "./", "../")):
        return False
    expanded = _expanded_static_path(operand)
    return expanded is None or not expanded.is_absolute()


def _cd_is_cdpath_uncertain(segment: list[str], cdpath: bool = False) -> bool:
    """A ``cd`` whose target depends on a CDPATH this hook cannot know.

    ``cdpath`` says the command may have set CDPATH before this segment;
    the hook's own environment and the segment's prefix assignments
    (``CDPATH=x cd run``) are checked here.
    """
    stripped = _strip_assignments(segment)
    if not stripped or stripped[0] != "cd":
        return False
    prefix_sets = any(w.startswith("CDPATH=") for w in segment[: len(segment) - len(stripped)])
    if not (cdpath or prefix_sets or _cdpath_env_set()):
        return False
    operands: list[str] = []
    options_done = False
    for value in stripped[1:]:
        if not options_done and value == "--":
            options_done = True
            continue
        if not options_done and value.startswith("-") and value != "-":
            continue
        operands.append(value)
        options_done = True
    return len(operands) == 1 and _cd_operand_searches_cdpath(operands[0])


def _simple_cd_status(
    segment: list[str], cwd: Path, cdpath: bool = False
) -> tuple[bool | None, Path]:
    """Return a simple cd's known status and resulting cwd without executing it.

    Documented flags are modeled (`cd [-L|[-P [-e]]] [--] dir`): ``-P``
    resolves the target physically through symlinks, ``-L`` (the default)
    resolves lexically, later flags override earlier ones, and ``-e`` only
    affects exit status in cases a known-good directory cannot hit. Any
    other option (``-@``, ``cd -``) stays unknown.

    An unqualified relative operand (``cd run``) is searched for in
    $CDPATH first: when CDPATH may be set (``cdpath``, the hook's own
    environment, or a prefix assignment) the target is unknown, so the cd
    is unmodeled like any other unknown form.
    """
    if _cd_is_cdpath_uncertain(segment, cdpath):
        return None, cwd
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
    target = _expanded_static_path(arguments[0])
    if target is None:
        return None, cwd
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
        except (OSError, ValueError):
            return None
    return resolved


def _expanded_static_path(value: str) -> Path | None:
    """Expand `~` in a command-derived path, or ``None`` if no OS call can take it.

    A NUL byte makes `os.path.expanduser`, `realpath` and `os.access` raise
    ValueError rather than OSError; such a word is never a path the shell
    could have used, so callers treat it as unknown instead of crashing.
    """
    if "\x00" in value:
        return None
    try:
        return Path(os.path.expanduser(value))
    except (OSError, ValueError):
        return None


def _apply_cd(segment: list[str], cwd: Path, cdpath: bool = False) -> Path:
    """Apply a simple shell ``cd`` segment without executing user input."""
    status, target = _simple_cd_status(segment, cwd, cdpath)
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
    word_start = True
    while index < len(command):
        char = command[index]
        at_word_start, word_start = word_start, False
        if quote is None:
            if char == "\\":
                if index + 1 >= len(command):
                    return None
                if command[index + 1] == "\n":
                    # A line continuation vanishes: `\<newline># ...` still
                    # starts a word, so that `#` begins a comment.
                    word_start = at_word_start
                index += 2
                continue
            if char in {"'", '"'}:
                quote = char
            elif char in " \t":
                word_start = True
            elif char in ";\n)":
                break
            elif char == "#" and at_word_start:
                # An unquoted word-initial `#` comments out the rest of the
                # line, so its text (parens, apostrophes) is not shell syntax.
                # An escaped space (`a\ #b`) keeps `#` inside the word.
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


def _join_line_continuations(fragment: str) -> str:
    """Remove backslash-newline pairs the shell removes (not in single quotes)."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(fragment):
        char = fragment[index]
        if quote != "'" and char == "\\" and index + 1 < len(fragment):
            if fragment[index + 1] == "\n":
                index += 2
                continue
            out.append(fragment[index : index + 2])
            index += 2
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
        out.append(char)
        index += 1
    return "".join(out)


_SHELL_PUNCTUATION = ";&|()<>\n"
# Private-use stand-ins for quoted or escaped shell punctuation, so the
# tokenizer cannot mistake a literal `')'` or `\>` argument for an operator.
_PUNCTUATION_SHIELD = {char: chr(0xE000 + ord(char)) for char in _SHELL_PUNCTUATION}
_PUNCTUATION_UNSHIELD = str.maketrans(
    {shield: char for char, shield in _PUNCTUATION_SHIELD.items()}
)


def _shield_quoted_punctuation(fragment: str) -> str:
    """Replace shell punctuation that is quoted or escaped with stand-ins."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(fragment):
        char = fragment[index]
        if quote is None and char == "\\" and index + 1 < len(fragment):
            following = fragment[index + 1]
            out.append("\\" + _PUNCTUATION_SHIELD.get(following, following))
            index += 2
            continue
        if quote == '"' and char == "\\" and index + 1 < len(fragment):
            out.append(fragment[index : index + 2])
            index += 2
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            out.append(char)
        elif quote is not None:
            out.append(_PUNCTUATION_SHIELD.get(char, char))
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _simple_command_argv(fragment: str) -> list[str] | None:
    """Tokenize argv while consuming shell redirection operators and targets."""
    fragment = _join_line_continuations(fragment)
    fragment = _strip_unquoted_shell_comments(_blank_io_numbers(fragment))
    if fragment is None:
        return None
    fragment = _shield_quoted_punctuation(fragment)
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
            argv.append(token.translate(_PUNCTUATION_UNSHIELD))
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


# --- R command lines ---
#
# One rule covers every R form. R's argv is read word by word, redirections
# dropped, up to `--args` (R ignores every later word). A word the shell
# computes -- one with a `$` or backtick outside single quotes -- is an
# unknown word. When the expansion sits inside double quotes (`"$D/a.R"`) it
# is still exactly one word, so reading goes on past it; an unquoted one may
# split into any number of words, and it, an unknown word where an option
# belongs, or shell syntax this reader cannot follow ends the readable argv:
# the words before it are exact and the rest are unknown. Unknown words never
# reject a run; only a proven stop word (help, version, R_HOME, CMD, a
# debugger) or `-e` together with `-f` does. Known `-e` expressions are
# recorded, computed ones never are, and the script of an `R -f`/`R CMD BATCH`
# run is unresolved when its file word is unknown or later words could name a
# different file.

# Words the R front end or binary honors anywhere before `--args`: they exit
# (help, version, R_HOME), hand off to Rcmd (CMD), or run a debugger.
_R_STOP_WORDS = {
    "-h", "--help", "--version", "--print-home", "RHOME", "CMD", "-d", "--debugger",
}
_R_SEPARATED_VALUE_OPTIONS = {
    "-e", "-f", "-g", "-d", "--debugger", "--arch", "--encoding", "--gui",
    "--max-connections", "--max-nsize", "--max-ppsize", "--max-vsize",
    "--min-nsize", "--min-vsize",
}
# `&>` and `&>>` take no descriptor: bash passes the `2` of `2&>f` as a word.
_REDIRECTION_OPERATOR_RX = re.compile(
    r"(?:\d+|\{[A-Za-z_][A-Za-z0-9_]*\})?(?P<op><<<|<<-?|<>|>>|>\||>&|<&|>|<)"
    r"|&>>?"
)
# What may follow a `$` that starts an expansion (a name, positional or
# special parameter, `${`, `$(`, `$[`); any other `$` is a literal character. Outside
# double quotes `$'...'` and `$"..."` also start one.
_EXPANSION_START_RX = re.compile(r"[A-Za-z0-9_@*#?$!{(\[-]")
# `${NAME}`, `${NAME:-default}` and the like with no quotes, escapes, or
# nesting inside: the only braced expansions whose extent this scan follows.
_SIMPLE_BRACED_RX = re.compile(r"\$\{[^{}'\"\\`$\n]*\}")


class _RWord(NamedTuple):
    text: str  # the word after quote removal (as written if it cannot be)
    known: bool  # False when the shell computes part of it
    splits: bool = False  # computed outside quotes: may become 0 or 2+ words


class UnresolvedScript(NamedTuple):
    """A script run whose script this scan cannot name with certainty.

    ``candidate`` is the file word as written on the command line; shell
    syntax (or, for ``R CMD BATCH``, ``R_BATCH_OPTIONS``) may make R read a
    different file. With ``cwd_unknown`` the word is a relative path and
    the directory it resolves against is not modeled (``pushd``, ``cd -``,
    a CDPATH-searched ``cd``, ...), whatever the interpreter.
    """

    candidate: str
    batch: bool = False  # an `R CMD BATCH` run rather than `R -f`
    cwd_unknown: bool = False


def _decode_r_word(raw: str) -> str | None:
    """Decode one shell word, including the `\\$` and `\\`` that bash
    unescapes inside double quotes but shlex leaves alone."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and quote != "'":
            pair = raw[index : index + 2]
            out.append(pair[1:] if quote == '"' and pair[1:] in {"$", "`"} else pair)
            index += 2
            continue
        if char in "'\"" and quote in (None, char):
            quote = None if quote else char
        out.append(char)
        index += 1
    return _decode_shell_word("".join(out))


def _expansion(raw: str) -> tuple[bool, bool]:
    """(computed, splits) for one shell word.

    Computed: an expansion (a `$` that starts one, a backtick, or a process
    substitution) outside '...'. Splits: a `$` expansion or backtick outside
    any quotes, so the shell may make the word into zero or several words. A
    process substitution is one word (a pipe path).
    """
    computed = False
    quote: str | None = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if char in "'\"" and quote in (None, char):
            quote = None if quote else char
        elif quote != "'" and (
            char == "`"
            or (
                char == "$"
                and (
                    _EXPANSION_START_RX.match(raw, index + 1)
                    or (quote is None and raw[index + 1 : index + 2] in {"'", '"'})
                )
            )
        ):
            if quote is None:
                return True, True
            computed = True
        elif char in "<>" and raw[index + 1 : index + 2] == "(" and quote is None:
            computed = True
        index += 1
    return computed, False


def _scan_shell_group(command: str, index: int, closer: str) -> int | None:
    """Offset just past `closer`, skipping nested quotes and substitutions.

    Returns None (unknown) for a comment or a `${...}` expansion with quotes
    or nesting inside, whose quoting rules this scan does not model, and,
    inside `(...)`, for anything beyond one simple pipeline on one line (a
    newline, `;`, a line continuation, or `case`, whose patterns and heredocs
    carry unbalanced parentheses). Ordinary targets like `$(date +%s)`,
    `>(tee log)` and `"${LOG}"` pass.
    """
    while index < len(command):
        char = command[index]
        following = command[index + 1 : index + 2]
        if char == "$" and following == "{":
            braced = _SIMPLE_BRACED_RX.match(command, index)
            if braced is None:
                return None
            index = braced.end()
            continue
        # `#` inside double quotes is literal text, not a comment.
        if char == "#" and closer != '"':
            return None
        if closer == ")" and (
            char in "\n;"
            or (char == "\\" and following == "\n")
            or (
                command.startswith("case", index)
                and command[index - 1 : index] in {"", " ", "\t", "(", "|", "&"}
                and command[index + 4 : index + 5] in {"", " ", "\t"}
            )
        ):
            return None
        if char == closer:
            return index + 1
        if char == "\\":
            index += 2
            continue
        next_index: int | None
        if closer == "`":
            next_index = index + 1
        elif closer == '"':
            if char == "$" and command[index + 1 : index + 2] == "(":
                next_index = _scan_shell_group(command, index + 2, ")")
            elif char == "`":
                next_index = _scan_shell_group(command, index + 1, "`")
            else:
                next_index = index + 1
        elif char == "'":
            end = command.find("'", index + 1)
            next_index = None if end < 0 else end + 1
        elif char == '"':
            next_index = _scan_shell_group(command, index + 1, '"')
        elif char == "`":
            next_index = _scan_shell_group(command, index + 1, "`")
        elif char == "(":
            next_index = _scan_shell_group(command, index + 1, ")")
        else:
            next_index = index + 1
        if next_index is None:
            return None
        index = next_index
    return None


def _shell_word_end(command: str, index: int) -> int | None:
    """End offset of the shell word at index, substitutions included.

    The command must already have its line continuations joined.
    """
    start = index
    while index < len(command):
        char = command[index]
        following = command[index + 1 : index + 2]
        next_index: int | None
        if char in " \t\n;&|)":
            break
        if char in "<>":
            if following != "(":
                break
            next_index = _scan_shell_group(command, index + 2, ")")
        elif char == "$" and following == "{":
            braced = _SIMPLE_BRACED_RX.match(command, index)
            next_index = None if braced is None else braced.end()
        elif char == "$" and following == "(":
            next_index = _scan_shell_group(command, index + 2, ")")
        elif char == "`":
            next_index = _scan_shell_group(command, index + 1, "`")
        elif char == '"':
            next_index = _scan_shell_group(command, index + 1, '"')
        elif char == "'":
            end = command.find("'", index + 1)
            next_index = None if end < 0 else end + 1
        elif char == "\\":
            next_index = index + 2
        elif char == "(":
            return None
        else:
            next_index = index + 1
        if next_index is None:
            return None
        index = next_index
    index = min(index, len(command))
    return index if index > start else None


def _quoted_delimiter_end(command: str, index: int) -> int | None:
    """End of a heredoc delimiter that starts with a quote. Bash never
    expands a delimiter, so `$` and backticks inside it are plain text."""
    quote: str | None = None
    while index < len(command) and (quote or command[index] not in " \t\n;&|()<>"):
        char = command[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if char in "'\"" and quote in (None, char):
            quote = None if quote else char
        index += 1
    return None if quote or index > len(command) else index


def _r_words(command: str, offset: int) -> Generator[_RWord, None, bool]:
    """Yield the words of the simple command at offset, redirections dropped.

    The generator returns True when it read to the end of the command and
    False when it stopped at syntax it cannot follow, such as a subshell.
    A heredoc's body starts after the newline, where the command ends, so its
    delimiter is skipped like any redirection target. Words are decoded only
    as a caller asks for them.
    """
    index = offset
    if command.find("\\\n", offset) >= 0:
        # Bash joins line continuations before it reads words or operators,
        # so `2\<newline>>&1` is the redirection `2>&1`.
        command, index = _join_line_continuations(command[offset:]), 0
    try:
        while True:
            while command[index : index + 1] in {" ", "\t"}:
                index += 1
            if index >= len(command) or command[index] in ";\n|)#":
                return True
            operator = None
            if not command.startswith(("<(", ">("), index):
                operator = _REDIRECTION_OPERATOR_RX.match(command, index)
            if operator is not None:
                index = operator.end()
                while command[index : index + 1] in {" ", "\t"}:
                    index += 1
                if operator.group("op") in {"<<", "<<-"} and command[
                    index : index + 1
                ] in {"'", '"'}:
                    end = _quoted_delimiter_end(command, index)
                else:
                    end = _shell_word_end(command, index)
                if end is None:
                    return False
                index = end
                continue
            if command[index] == "&":
                return True
            end = _shell_word_end(command, index)
            if end is None:
                return False
            raw = command[index:end]
            index = end
            computed, splits = _expansion(raw)
            decoded = _decode_r_word(raw)
            if computed:
                yield _RWord(raw if decoded is None else decoded, False, splits)
            elif decoded is not None:
                yield _RWord(decoded, True)
            else:
                return False
    except RecursionError:
        return False


def _known_words(texts: list[str]) -> Generator[_RWord, None, bool]:
    yield from (_RWord(text, True) for text in texts)
    return True


class _Words:
    """Words read from a word generator only as far as a caller looks."""

    def __init__(self, source: Generator[_RWord, None, bool]) -> None:
        self._source = source
        self._read: list[_RWord] = []
        self.complete: bool | None = None  # set once the source is exhausted

    def get(self, index: int) -> _RWord | None:
        while index >= len(self._read) and self.complete is None:
            try:
                self._read.append(next(self._source))
            except StopIteration as stop:
                self.complete = bool(stop.value)
        return self._read[index] if index < len(self._read) else None


class _RArgv(NamedTuple):
    expressions: list[str]  # every known `-e` value, in order
    file: _RWord | None  # the last `-f`/`--file=` value
    stopped: bool  # a stop word, or `-e` with `-f`: the payload never runs
    complete: bool  # False when unknown words may follow


def _walk_r_argv(words: _Words, *, rscript: bool) -> _RArgv:
    """Read R's (or Rscript's) options from words 1.. by the rule above.

    R honors stop words and the last `-f`/`--file=` anywhere before `--args`
    (checked on R 4.6). Rscript reads consecutive `-e EXPR` pairs and stops at
    the first other word; its stop words are left to its regex.
    """
    expressions: list[str] = []
    file: _RWord | None = None
    has_expression = False  # computed expressions are not recorded, but count
    complete = False
    index = 1  # word 0 is the executable itself
    while (word := words.get(index)) is not None:
        text = word.text
        if not word.known:
            # Where an option belongs, an unknown word may be one that takes
            # the next word as its value, so nothing after it is known. Only
            # a quoted `--file="$F"` is certain to be one word and the file.
            if rscript or not text.startswith("--file="):
                break
            file = _RWord(text[len("--file=") :], False)
            if word.splits:
                break
            index += 1
            continue
        if text == "--args":
            complete = True
            break
        takes_value = text in _R_SEPARATED_VALUE_OPTIONS
        value = words.get(index + 1) if takes_value else None
        if rscript and text != "-e":
            if has_expression:
                break
            index += 1
            continue
        if not rscript and (
            text in _R_STOP_WORDS
            or text.startswith("--debugger=")
            # R exits when -e/-f has no value (not merely an unreadable one).
            or (text in {"-e", "-f"} and value is None and words.complete)
        ):
            return _RArgv(expressions, file, True, True)
        if text == "-e" and value is not None:
            has_expression = True
            if value.known:  # a computed expression is not recorded as source
                expressions.append(value.text)
        elif text == "-f" and value is not None:
            file = value
        elif text.startswith("--file="):
            file = _RWord(text[len("--file=") :], True)
        if value is not None and value.splits:
            break  # an unquoted computed value may split into more words
        index += 2 if takes_value else 1
    else:
        complete = bool(words.complete)
    stopped = not rscript and has_expression and file is not None
    return _RArgv(expressions, file, stopped, complete)


def _r_inline_argv(command: str, offset: int, *, rscript: bool) -> _RArgv:
    return _walk_r_argv(_Words(_r_words(command, offset)), rscript=rscript)


def _r_file_script(command: str, match: re.Match[str]) -> _RWord | None:
    """The script an `R -f`/`R --file=` run reads, or None if R never runs it.

    Returns an unknown word when unknown words may override the file.
    """
    argv = _r_inline_argv(command, match.start(), rscript=False)
    if argv.stopped:
        return None
    if argv.file is None:
        if argv.complete:
            return None
        # An unknown word before the matched `-f` (`$OPTS -f a.R`).
        matched_word = match.group("path")
        return _RWord(_decode_shell_word(matched_word) or matched_word, False)
    return argv.file if argv.complete else argv.file._replace(known=False)


def _ifs_split(text: str) -> list[str]:
    """Field splitting with the shell's default IFS (space, tab, newline),
    not Python's Unicode whitespace."""
    return [field for field in re.split(r"[ \t\n]+", text) if field]


def _literal_batch_options(command: str, offset: int) -> str | None:
    """The literal `R_BATCH_OPTIONS=...` value assigned on the command at
    offset itself, or None when its prefix has anything but assignments or
    the value is computed."""
    prefix = _command_prefix_before(command, offset)
    if not prefix or not all(_ASSIGNMENT_RX.match(token) for token in prefix):
        return None
    values = [t.split("=", 1)[1] for t in prefix if t.startswith("R_BATCH_OPTIONS=")]
    if not values or re.search(r"[$`]", values[-1]):
        return None
    return values[-1]


def _r_batch_script(command: str, match: re.Match[str]) -> _RWord | None:
    """The script R reads for an `R CMD BATCH` match, or None if none runs.

    Mirrors R's BATCH script (R 4.6): it exits on -h/--help/-v/--version,
    drops --no-timing, ends options at `--` or the first non-option word (the
    input), forwards -d/-g with their value and every other `-*` word, then
    runs `R -f INPUT $opts $R_BATCH_OPTIONS` with all three unquoted, so each
    splits into words. R then reads that argv by the rule above. A literal
    `R_BATCH_OPTIONS=...` assignment on this command itself is read exactly.
    Any other mention of the variable (export, `env`, a computed value) is
    not modelled: the run is unresolved unless the options already stop R.
    Otherwise the variable is ignored.
    """
    words = _Words(_r_words(command, match.start()))
    index = 0
    while (word := words.get(index)) is not None and not (
        word.known
        and word.text == "CMD"
        and getattr(words.get(index + 1), "text", None) == "BATCH"
    ):
        index += 1
    matched_word = match.group("path")
    matched = _RWord(_decode_shell_word(matched_word) or matched_word, False)
    if word is None:
        # `CMD BATCH` past syntax the reader cannot follow still runs.
        return None if words.complete else matched
    index += 2
    forwarded: list[str] = []
    infile: _RWord | None = None
    while (word := words.get(index)) is not None:
        if word.text in {"-d", "-g"} and word.known:
            value = words.get(index + 1)
            if value is None or value.known:
                forwarded.extend(w.text for w in (word, value) if w is not None)
                index += 2
                continue
            word = value  # BATCH splits a computed value unquoted
        if not word.known:
            # `$OPTS` may expand to anything, the input included: name the
            # first R-script-shaped word after it as the likely input.
            while (later := words.get(index)) is not None:
                if later.known and re.fullmatch(r"[^-].*\.[Rr]", later.text):
                    return later._replace(known=False)
                index += 1
            return matched
        if word.text in {"-h", "--help", "-v", "--version"}:
            return None
        if word.text == "--":
            infile = words.get(index + 1)
            break
        if word.text.startswith("-"):
            if word.text != "--no-timing":
                forwarded.append(word.text)
            index += 1
            continue
        infile = word
        break
    if infile is None:
        # Input past unreadable syntax: BATCH may well run it.
        return None if words.complete else matched
    if not infile.known:
        return infile
    # R's BATCH script runs `${R_HOME}/bin/R -f ${in} ...` with `${in}`
    # unquoted, so `R CMD BATCH 'my file.R'` reads `my`, never `my file.R`.
    # A spaced input is therefore split, not resolved as one path. The
    # contrived `'x.R y'`, where the first field is itself a script, is
    # deliberately left unrecorded (the locating regex wants an R-shaped word).
    in_words = _ifs_split(infile.text)
    if not in_words:
        return None
    batch_options = _literal_batch_options(command, match.start())
    r_argv = ["R", "-f", *in_words, *_ifs_split(" ".join(forwarded))]
    argv = _walk_r_argv(
        _Words(_known_words([*r_argv, *_ifs_split(batch_options or "")])),
        rscript=False,
    )
    if argv.stopped or argv.file is None:
        return None
    if batch_options is None and "R_BATCH_OPTIONS" in command:
        return _RWord(in_words[0], False)
    if not re.fullmatch(r".+\.[Rr]", argv.file.text):
        return None
    return argv.file


_CWD_AFFECTING_BUILTINS = {"pushd", "popd", "source", ".", "eval"}
# The builtins whose whole job is to move the cwd (`cd` is modeled apart).
_CWD_DIRECTORY_BUILTINS = {"pushd", "popd"}
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


def _segment_cwd_is_modeled(
    segment: list[str], cwd: Path, cdpath: bool = False, *, directory_builtins_only: bool = False
) -> bool:
    """True when a completed segment cannot change the cwd in unmodeled ways.

    ``cd`` with a statically known status is modeled; other cwd-affecting
    builtins (and ``command``-prefixed forms of them) are not — a following
    trust decision must not assume the working directory it computed.

    ``directory_builtins_only`` asks the narrower question the cwd-unknown
    flag needs: only the directory builtins (``cd`` forms the walk cannot
    apply, ``pushd``, ``popd``) count, while code run through ``source`` /
    ``.`` / ``eval`` is left to the CDPATH check (``source venv/bin/activate
    && python a.py`` keeps its relative script).
    """
    builtins = (
        _CWD_DIRECTORY_BUILTINS if directory_builtins_only else _CWD_AFFECTING_BUILTINS
    )
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
        {"cd"} | builtins | _BUILTIN_LAUNCHER_PREFIXES
    ):
        # `>log cd nested` still changes the cwd, but the cwd walker's
        # _apply_cd never tracks redirection-prefixed segments — unmodeled.
        return False
    if name in builtins:
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
            {"cd"} | builtins | _BUILTIN_LAUNCHER_PREFIXES
        )
    if name == "cd":
        status, _ = _simple_cd_status(segment, cwd, cdpath)
        return status is not None
    return True


# The punctuation ``_shell_tokens`` hands to shlex (redirections are words).
_SHELL_TOKEN_PUNCTUATION = frozenset(";&|()\n")


def _shell_token_cuts(command: str) -> list[int]:
    """Offsets where ``_shell_tokens`` restarts cleanly.

    A cut follows a whole unquoted, uncommented punctuation run, so the
    comment stripper and the lexer are both between words, outside any
    quote, at a word start. For every ``offset`` at or after a cut ``c``,
    ``_shell_tokens(command[:offset])`` equals the tokens of ``command[:c]``
    followed by those of ``command[c:offset]``.
    """
    cuts: list[int] = []
    quote: str | None = None
    at_word_start = True
    after_punctuation = False
    index = 0
    while index < len(command):
        char = command[index]
        if quote is None:
            if char in _SHELL_TOKEN_PUNCTUATION:
                after_punctuation = True
                at_word_start = True
                index += 1
                continue
            if after_punctuation:
                cuts.append(index)
                after_punctuation = False
            if char == "\\":
                if command[index + 1 : index + 2] != "\n":
                    at_word_start = False
                index += 2
                continue
            if char in {"'", '"'}:
                quote = char
                at_word_start = False
                index += 1
                continue
            if char == "#" and at_word_start:
                newline = command.find("\n", index)
                index = len(command) if newline < 0 else newline
                continue
            at_word_start = char.isspace()
            index += 1
            continue
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char == '"':
            quote = None
        index += 1
    return cuts


class _PrefixFolds:
    """Answer token folds over ``command[:offset]`` for many offsets.

    Each match used to re-tokenize its whole prefix, so a command with many
    interpreter calls (800 ``R -e`` lines) cost quadratic time. A fold's
    state is cached at each ``_shell_token_cuts`` cut, and a query tokenizes
    only the tail from the last cut, giving the same tokens in the same order.
    """

    def __init__(self, command: str) -> None:
        self.command = command
        self.cuts = _shell_token_cuts(command)
        self.segments: list[list[str] | None] = []
        self.states: dict[object, list[object]] = {}
        self.depth_index: _DepthIndex | None = None

    def _segment(self, index: int) -> list[str] | None:
        """Tokens between cut ``index - 1`` (or 0) and cut ``index`` (or the end)."""
        while len(self.segments) <= index:
            k = len(self.segments)
            start = self.cuts[k - 1] if k else 0
            end = self.cuts[k] if k < len(self.cuts) else len(self.command)
            self.segments.append(_shell_tokens(self.command[start:end]))
        return self.segments[index]

    def fold(self, offset: int, key: object, initial, step) -> object | None:
        """Fold ``step`` over the prefix tokens; ``None`` if they don't lex."""
        count = bisect.bisect_right(self.cuts, offset)
        states = self.states.setdefault(key, [initial()])
        while len(states) <= count:
            k = len(states) - 1
            tokens = self._segment(k)
            if tokens is None:
                # Unreachable for a clean cut; stay exact regardless.
                tokens = _shell_tokens(self.command[:offset])
                return None if tokens is None else step(initial(), tokens)
            states.append(step(states[-1], tokens))
        start = self.cuts[count - 1] if count else 0
        tail = _shell_tokens(self.command[start:offset])
        if tail is None:
            return None
        return step(states[count], tail)

    def _depth_index(self) -> _DepthIndex:
        """Index the control tokens of every segment by absolute paren depth."""
        if self.depth_index is None:
            raw_before: list[int] = []
            stoppers: dict[int, list[int]] = {}
            operators: dict[tuple[str, int], list[int]] = {}
            last_unlexed = -1
            raw = 0
            for k in range(len(self.cuts) + 1):
                raw_before.append(raw)
                tokens = self._segment(k)
                if tokens is None:
                    last_unlexed = k
                    continue
                for token in tokens:
                    if token == "(":
                        raw += 1
                    elif token == ")":
                        stoppers.setdefault(raw, []).append(k)
                        raw -= 1
                    elif token in {";", "\n", "&"}:
                        stoppers.setdefault(raw, []).append(k)
                    elif token in {"&&", "||"}:
                        operators.setdefault((token, raw), []).append(k)
            self.depth_index = _DepthIndex(
                raw_before, stoppers, operators, raw, last_unlexed
            )
        return self.depth_index

    def list_operators_after(self, offset: int) -> list[str] | None | object:
        """Fast ``_top_level_control_context(command[offset:])`` for a caller.

        Returns ``None`` where the context reports a later list or ambiguity,
        else operators equal as a set to the context's, or ``_UNANSWERED``
        when the shortcut does not apply. Lexing ``command[offset:]`` on its
        own matches the whole-command lexing from ``offset`` on when the
        prefix lexes cleanly, ``offset`` is outside a comment, and the first
        character cannot start a comment or a line continuation; the suffix
        then splits at the same cuts. Relative to ``offset``, a token is at
        depth 0 exactly when its absolute depth equals ``base`` below, and
        the context is unusable once any ``;``, newline, ``&`` or ``)`` sits
        there or the parentheses do not balance.
        """
        char = self.command[offset : offset + 1]
        if not char or char in "#\\" or char.isspace():
            return _UNANSWERED
        after = bisect.bisect_right(self.cuts, offset)
        if after == len(self.cuts):
            return _UNANSWERED
        start = self.cuts[after - 1] if after else 0
        masked = _strip_unquoted_shell_comments(self.command[start : offset + 1])
        if masked is None or masked[-1] != char:
            return _UNANSWERED
        if _shell_tokens(self.command[start:offset]) is None:
            return _UNANSWERED
        piece = _shell_tokens(self.command[offset : self.cuts[after]])
        if piece is None:
            return _UNANSWERED
        state = _fold_control_context(_CONTROL_FOLD_INITIAL, piece)
        if state.later_list or state.current_list_ambiguous or state.malformed:
            return None
        index = self._depth_index()
        rest = after + 1
        if index.last_unlexed >= rest:
            return None
        base = index.raw_before[rest] - state.depth
        if index.final_raw != base:
            return None
        stoppers = index.stoppers.get(base)
        if stoppers and stoppers[-1] >= rest:
            return None
        operators = list(state.operators)
        for operator in ("&&", "||"):
            found = index.operators.get((operator, base))
            if found and found[-1] >= rest:
                operators.append(operator)
        return operators


class _DepthIndex(NamedTuple):
    # raw_before[k]: unclamped paren depth before segment k.
    raw_before: list[int]
    # Segments holding a list-ending token (`;`, newline, `&`) at a depth, or
    # a `)` that closes back below it; both end a suffix's usable context.
    stoppers: dict[int, list[int]]
    # Segments holding `&&` / `||` at a depth.
    operators: dict[tuple[str, int], list[int]]
    final_raw: int
    last_unlexed: int


_UNANSWERED = object()


@functools.lru_cache(maxsize=4)
def _prefix_folds(command: str) -> _PrefixFolds:
    return _PrefixFolds(command)


# A checkpoint is cached after every shell operator, so fold states must not
# copy histories: a stack is a cons list ``(top, rest)`` (``None`` when empty)
# that later states share, and operator histories are reduced to the
# summaries their callers read. Otherwise a 6,000-command ``&&`` chain kept
# 6,000 growing copies (quadratic memory).
def _cons_items(cons: object) -> list:
    """The cons list's items, bottom first."""
    items = []
    while cons is not None:
        items.append(cons[0])
        cons = cons[1]
    items.reverse()
    return items


class _CwdFoldState(NamedTuple):
    cwd: Path
    fully_modeled: bool
    # Cons list of (cwd, segment_is_conditional, chain_has_or, cwd_unknown)
    # per open "(".
    subshell_states: object
    segment: tuple[str, ...]
    segment_is_conditional: bool
    chain_has_or: bool
    # An earlier segment may have set CDPATH (never cleared: conservative).
    cdpath: bool = False
    # A directory change the walk cannot apply ran (or may have run) in this
    # shell — a CDPATH-searched `cd`, `pushd`/`popd`, `cd -`, `cd $DIR`, a
    # skipped conditional `cd` — so the remembered cwd is a guess and nothing
    # relative may be proven against it.
    cwd_unknown: bool = False
    # The operator before the current segment in its AND-OR list (``None``
    # for the list's first command), and the list's exit status so far when
    # statically known: together they say whether a conditional `cd` ran.
    pending_op: str | None = None
    chain_status: bool | None = None
    # The current segment follows a `|`: in bash it runs in a subshell.
    in_pipeline: bool = False


def _fold_cwd_state(state: _CwdFoldState, tokens: list[str]) -> _CwdFoldState:
    """Advance ``_effective_cwd_state``'s walk over ``tokens``."""
    cwd = state.cwd
    fully_modeled = state.fully_modeled
    subshell_states = state.subshell_states
    segment = list(state.segment)
    segment_is_conditional = state.segment_is_conditional
    chain_has_or = state.chain_has_or
    cdpath = state.cdpath
    cwd_unknown = state.cwd_unknown
    pending_op = state.pending_op
    chain_status = state.chain_status
    in_pipeline = state.in_pipeline

    def complete_segment() -> None:
        nonlocal fully_modeled, cwd_unknown
        if not _segment_cwd_is_modeled(segment, cwd, cdpath):
            fully_modeled = False
        # The remembered cwd is a guess once this shell ran a directory change
        # the walk cannot apply: a CDPATH-searched `cd`, `pushd`/`popd`,
        # `cd -`, `cd $DIR`, a flagged or launcher-prefixed `cd`, ...
        if _cd_is_cdpath_uncertain(segment, cdpath) or not _segment_cwd_is_modeled(
            segment, cwd, cdpath, directory_builtins_only=True
        ):
            cwd_unknown = True

    def segment_runs() -> bool | None:
        """Whether the current segment ran, as far as the text proves."""
        if pending_op is None:
            return True
        if chain_status is None:
            return None
        return chain_status == (pending_op == "&&")

    def advance_chain(runs: bool | None) -> None:
        nonlocal chain_status
        status = _simple_command_status(segment, cwd, cdpath) if segment else None
        if runs is True:
            chain_status = status
        elif runs is None and status != chain_status:
            chain_status = None

    def unapplied_cd(runs: bool | None) -> None:
        # A `cd` the walk does not apply: when it ran (`true && cd sub;
        # python a.py` runs Python in sub) and is not inside a pipeline, it
        # moves the shell; when it may have run, the cwd is a guess.
        nonlocal cwd, cwd_unknown, fully_modeled
        if runs is False:
            return
        status, target = _simple_cd_status(segment, cwd, cdpath)
        if status is not True or target == cwd:
            return
        if runs is True and not in_pipeline:
            cwd = target
        else:
            cwd_unknown = True
            fully_modeled = False

    def after_segment() -> None:
        nonlocal cdpath
        if not cdpath and _cdpath_may_be_set(" ".join(segment)):
            cdpath = True

    for token in tokens:
        if token in {"&&", "||"}:
            complete_segment()
            runs = segment_runs()
            advance_chain(runs)
            if in_pipeline:
                # `echo | cd sub && ...`: bash runs the cd in a subshell,
                # zsh in this shell.
                unapplied_cd(None)
            elif not segment_is_conditional:
                # The first segment in an AND-OR list always executes.
                cwd = _apply_cd(segment, cwd, cdpath)
            elif token == "&&" and not chain_has_or:
                # Reaching the matched command at the end of an uninterrupted
                # AND chain proves that each earlier segment in that chain ran
                # successfully. A mixed OR chain is ambiguous: for
                # ``true || cd sub && python a.py``, Python runs while cd does
                # not, so it is applied only when the text proves it ran.
                cwd = _apply_cd(segment, cwd, cdpath)
            else:
                unapplied_cd(runs)
            after_segment()
            segment = []
            segment_is_conditional = True
            pending_op = token
            in_pipeline = False
            if token == "||":
                chain_has_or = True
        elif token in {";", "\n"}:
            complete_segment()
            runs = segment_runs()
            if in_pipeline:
                unapplied_cd(None)
            elif not segment_is_conditional:
                cwd = _apply_cd(segment, cwd, cdpath)
            else:
                unapplied_cd(runs)
            after_segment()
            segment = []
            segment_is_conditional = False
            chain_has_or = False
            pending_op = None
            chain_status = None
            in_pipeline = False
        elif token == "|":
            # A cd in a pipeline does not reliably affect the parent. The
            # pipeline runs exactly when its first command does, so the
            # AND-OR state carries over to its last command.
            complete_segment()
            after_segment()
            segment = []
            in_pipeline = True
        elif token == "&":
            # A backgrounded command runs in a subshell and ends the list.
            complete_segment()
            after_segment()
            segment = []
            pending_op = None
            chain_status = None
            in_pipeline = False
        elif token == "(":
            complete_segment()
            after_segment()
            segment = []
            subshell_states = (
                (cwd, segment_is_conditional, chain_has_or, cwd_unknown,
                 pending_op, chain_status, in_pipeline),
                subshell_states,
            )
            # A subshell's first command runs whenever the subshell does, so
            # `a && (cd sub; python x.py)` runs Python in sub.
            segment_is_conditional = False
            chain_has_or = False
            pending_op = None
            chain_status = None
            in_pipeline = False
        elif token == ")":
            complete_segment()
            after_segment()
            segment = []
            if subshell_states is not None:
                (
                    (cwd, segment_is_conditional, chain_has_or, cwd_unknown,
                     pending_op, chain_status, in_pipeline),
                    subshell_states,
                ) = subshell_states
        else:
            segment.append(token)
    return _CwdFoldState(
        cwd,
        fully_modeled,
        subshell_states,
        tuple(segment),
        segment_is_conditional,
        chain_has_or,
        cdpath,
        cwd_unknown,
        pending_op,
        chain_status,
        in_pipeline,
    )


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
    state = _cwd_fold(bash_cmd, offset, initial_cwd)
    if state is None:
        return initial_cwd, False
    return state.cwd, state.fully_modeled


def _cwd_fold(bash_cmd: str, offset: int, initial_cwd: Path) -> _CwdFoldState | None:
    # The hook's CDPATH is part of the key: a cached walk must not outlive it.
    return _prefix_folds(bash_cmd).fold(
        offset,
        ("cwd", initial_cwd, _cdpath_env_set()),
        lambda: _CwdFoldState(initial_cwd, True, None, (), False, False),
        _fold_cwd_state,
    )


def _effective_cwd_unknown(bash_cmd: str, offset: int, initial_cwd: Path) -> bool:
    """True when a directory change before ``offset`` is not modeled (a
    CDPATH-searched ``cd``, ``pushd``/``popd``, ``cd -``, ``cd $DIR``, a
    flagged or skipped conditional ``cd``), so the inferred cwd is a guess:
    relative paths resolved against it prove nothing (an unlexable prefix
    counts as unknown too)."""
    state = _cwd_fold(bash_cmd, offset, initial_cwd)
    return state is None or state.cwd_unknown


def _known_io_cwd(
    bash_cmd: str, offset: int, initial_cwd: Path, effective_cwd: Path
) -> Path | None:
    """The directory a detection's relative I/O resolves against, or
    ``None`` when an unmodeled directory change made it a guess."""
    return None if _effective_cwd_unknown(bash_cmd, offset, initial_cwd) else effective_cwd


def _effective_cwd(bash_cmd: str, offset: int, initial_cwd: Path) -> Path:
    """Resolve preceding top-level ``cd`` commands for a command match."""
    cwd, _ = _effective_cwd_state(bash_cmd, offset, initial_cwd)
    return cwd


class _ControlFoldState(NamedTuple):
    depth: int
    # The current list's distinct top-level operators (`&&`, `||`), first-seen
    # order: callers only ask whether there are any and whether all agree.
    operators: tuple[str, ...]
    later_list: bool
    current_list_ambiguous: bool
    malformed: bool


_CONTROL_FOLD_INITIAL = _ControlFoldState(0, (), False, False, False)


def _fold_control_context(
    state: _ControlFoldState, tokens: list[str]
) -> _ControlFoldState:
    """Advance ``_top_level_control_context``'s walk over ``tokens``."""
    depth, operators, later_list, current_list_ambiguous, malformed = state
    for token in tokens:
        if token == "(":
            depth += 1
        elif token == ")":
            if depth == 0:
                malformed = True
            else:
                depth -= 1
        elif depth == 0 and token in {";", "\n"}:
            operators = ()
            later_list = True
            # A completed list is an execution boundary. Unsupported control
            # operators in an earlier list cannot make the next list's first
            # command conditional or uncertain.
            current_list_ambiguous = False
        elif depth == 0 and token in {"&&", "||"}:
            if token not in operators:
                operators += (token,)
        elif depth == 0 and token == "&":
            current_list_ambiguous = True
    return _ControlFoldState(
        depth, operators, later_list, current_list_ambiguous, malformed
    )


def _finish_control_context(
    state: _ControlFoldState | None,
) -> tuple[list[str], bool, bool]:
    if state is None:
        return [], False, True
    malformed = state.malformed or bool(state.depth)
    return list(state.operators), state.later_list, (
        state.current_list_ambiguous or malformed
    )


def _top_level_control_context(fragment: str) -> tuple[list[str], bool, bool]:
    """Return current-list operators, whether a later list exists, and ambiguity.

    The operators are the distinct ``&&``/``||`` of the current list.
    """
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return _finish_control_context(None)
    return _finish_control_context(
        _fold_control_context(_CONTROL_FOLD_INITIAL, tokens)
    )


def _control_context_before(bash_cmd: str, offset: int) -> tuple[list[str], bool, bool]:
    """``_top_level_control_context(bash_cmd[:offset])`` from cached folds."""
    return _finish_control_context(
        _prefix_folds(bash_cmd).fold(
            offset,
            "control",
            lambda: _CONTROL_FOLD_INITIAL,
            _fold_control_context,
        )
    )


class _CommandPrefixFoldState(NamedTuple):
    malformed: bool  # an unmatched ``)`` made the prefix unreadable
    # The innermost current-command segment as a cons list, last token on
    # top, and whether its context is dynamic (inside a command substitution).
    segment: object
    dynamic: bool
    # Cons list of (segment, dynamic) for each enclosing open ``(``.
    outer: object


_COMMAND_PREFIX_FOLD_INITIAL = _CommandPrefixFoldState(False, None, False, None)


def _fold_command_prefix(
    state: _CommandPrefixFoldState, tokens: list[str]
) -> _CommandPrefixFoldState:
    """Advance ``_current_command_prefix``'s walk over ``tokens``."""
    if state.malformed:
        return state
    # Keep one current-command segment per subshell depth. The fragment ends at
    # the candidate, so a legitimate command inside ``( ... )`` necessarily
    # has an unmatched opening parenthesis here. Command substitutions remain
    # conservative because their child context is marked dynamic.
    _, segment, dynamic, outer = state
    for token in tokens:
        if token == "(":
            command_substitution = segment is not None and segment[0].endswith("$")
            outer = ((segment, dynamic), outer)
            segment = None
            dynamic = dynamic or command_substitution
        elif token == ")":
            if outer is None:
                return _CommandPrefixFoldState(True, None, False, None)
            (segment, dynamic), outer = outer
            # A completed compound command is not an execution wrapper for a
            # later token in the same simple command.
            segment = ("__mycelium_compound_command__", segment)
        elif token in {";", "\n", "&&", "||", "|", "&"}:
            segment = None
        else:
            segment = (token, segment)
    return _CommandPrefixFoldState(False, segment, dynamic, outer)


def _finish_command_prefix(
    state: _CommandPrefixFoldState | None,
) -> list[str] | None:
    if state is None or state.malformed or state.dynamic:
        return None
    return _cons_items(state.segment)


def _current_command_prefix(fragment: str) -> list[str] | None:
    """Return tokens before a candidate in its current simple command."""
    tokens = _shell_tokens(fragment)
    if tokens is None:
        return None
    return _finish_command_prefix(
        _fold_command_prefix(_COMMAND_PREFIX_FOLD_INITIAL, tokens)
    )


def _command_prefix_before(bash_cmd: str, offset: int) -> list[str] | None:
    """``_current_command_prefix(bash_cmd[:offset])`` from cached folds."""
    return _finish_command_prefix(
        _prefix_folds(bash_cmd).fold(
            offset,
            "command_prefix",
            lambda: _COMMAND_PREFIX_FOLD_INITIAL,
            _fold_command_prefix,
        )
    )


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
    target = _expanded_static_path(value)
    if target is None:
        return None
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
    prefix = _command_prefix_before(bash_cmd, offset)
    if prefix is None:
        return None
    base_cwd = _effective_cwd(bash_cmd, offset, initial_cwd)
    return _prefix_execution_cwd(prefix, base_cwd)


def _simple_command_status(
    segment: list[str], cwd: Path, cdpath: bool = False
) -> bool | None:
    """Return a statically knowable simple-command status, if available."""
    if _cd_is_cdpath_uncertain(segment, cdpath):
        return None  # `CDPATH=x cd run`: the prefix assignment counts too
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
    status, _ = _simple_cd_status(segment, cwd, cdpath)
    return status


def _touches_cwd(segment: list[str]) -> bool:
    stripped = _strip_assignments(segment)
    return bool(stripped) and Path(stripped[0]).name in {
        "cd", "pushd", "popd", "source", ".", "eval"
    }


class _OrFoldState(NamedTuple):
    failed: bool
    cwd: Path
    segment: tuple[str, ...]
    # The current list's completed alternatives, summarized: the final
    # status check reads them with the cwd they were completed under, which
    # only a completed list (that also clears them) can change.
    alternatives: int
    alternatives_failed: bool
    alternatives_touch_cwd: bool
    operators_are_or: bool
    cdpath: bool = False  # an earlier segment may have set CDPATH


def _fold_or_prefix(state: _OrFoldState, tokens: list[str]) -> _OrFoldState:
    """Advance the pure-OR proof over ``tokens``."""
    if state.failed:
        return state
    cwd = state.cwd
    segment = list(state.segment)
    alternatives = state.alternatives
    alternatives_failed = state.alternatives_failed
    alternatives_touch_cwd = state.alternatives_touch_cwd
    operators_are_or = state.operators_are_or
    cdpath = state.cdpath
    for token in tokens:
        if token in {"(", ")"}:
            return state._replace(failed=True)
        if token in {";", "\n", "&&", "||", "|", "&"}:
            this_cdpath = cdpath
            cdpath = cdpath or _cdpath_may_be_set(" ".join(segment))
        if token in {";", "\n"}:
            if not alternatives:
                status, target = _simple_cd_status(segment, cwd, this_cdpath)
                if status is True:
                    cwd = target
            elif alternatives_touch_cwd or _touches_cwd(segment):
                # A completed conditional list may have changed the parent
                # shell's cwd, but the one overall status supplied to this
                # hook cannot say which branch ran. Refuse to infer a later
                # relative fallback.
                return state._replace(failed=True)
            segment = []
            alternatives = 0
            alternatives_failed = True
            alternatives_touch_cwd = False
            operators_are_or = True
        elif token in {"&&", "||", "|", "&"}:
            alternatives += 1
            alternatives_failed = (
                alternatives_failed
                and _simple_command_status(segment, cwd, this_cdpath) is False
            )
            alternatives_touch_cwd = alternatives_touch_cwd or _touches_cwd(segment)
            operators_are_or = operators_are_or and token == "||"
            segment = []
        else:
            segment.append(token)
    return _OrFoldState(
        False,
        cwd,
        tuple(segment),
        alternatives,
        alternatives_failed,
        alternatives_touch_cwd,
        operators_are_or,
        cdpath,
    )


def _or_prefix_is_proven_failed(
    bash_cmd: str, offset: int, initial_cwd: Path
) -> bool:
    """Prove that every alternative before a pure-OR candidate failed.

    ``segment`` ends as the wrapper/assignment prefix of the candidate itself;
    only the completed alternatives before its final || determine whether it
    ran. Each must have a statically known failure status.
    """
    return _or_fold_proves(
        _prefix_folds(bash_cmd).fold(
            offset,
            ("or", initial_cwd, _cdpath_env_set()),
            lambda: _OrFoldState(False, initial_cwd, (), 0, True, False, True),
            _fold_or_prefix,
        )
    )


def _or_fold_proves(state: _OrFoldState | None) -> bool:
    return (
        state is not None
        and not state.failed
        and state.operators_are_or
        and state.alternatives > 0
        and state.alternatives_failed
    )


class _AndFoldState(NamedTuple):
    failed: bool
    saw_operator: bool
    segment: tuple[str, ...]
    cwd: Path


def _fold_and_prefix(state: _AndFoldState, tokens: list[str]) -> _AndFoldState:
    """Advance the pure-AND proof over ``tokens``."""
    if state.failed:
        return state
    saw_operator = state.saw_operator
    segment = list(state.segment)
    cwd = state.cwd
    for token in tokens:
        if token in {"(", ")", ";", "\n", "||", "|", "&"}:
            return state._replace(failed=True)
        if token == "&&":
            saw_operator = True
            if _simple_command_status(segment, cwd) is not True:
                return state._replace(failed=True)
            _, cwd = _simple_cd_status(segment, cwd)
            segment = []
        else:
            segment.append(token)
    return _AndFoldState(False, saw_operator, tuple(segment), cwd)


def _and_prefix_is_proven_successful(
    bash_cmd: str, offset: int, initial_cwd: Path
) -> bool:
    """Prove that every simple command before a pure-AND candidate succeeded."""
    return _and_fold_proves(
        _prefix_folds(bash_cmd).fold(
            offset,
            ("and", initial_cwd, _cdpath_env_set()),
            lambda: _AndFoldState(False, False, (), initial_cwd),
            _fold_and_prefix,
        )
    )


def _and_fold_proves(state: _AndFoldState | None) -> bool:
    return state is not None and not state.failed and state.saw_operator


def _list_operators_after(bash_cmd: str, offset: int) -> list[str] | None:
    """Operators after ``offset`` in its AND-OR list; ``None`` if unusable."""
    operators = _prefix_folds(bash_cmd).list_operators_after(offset)
    if operators is not _UNANSWERED:
        return operators
    operators, has_later_list, ambiguous = _top_level_control_context(
        bash_cmd[offset:]
    )
    return None if has_later_list or ambiguous else operators


# Whole-command views every candidate match consults: cache them per command
# instead of rescanning the command once per match.
@functools.lru_cache(maxsize=4)
def _command_without_comments(bash_cmd: str) -> str | None:
    return _strip_unquoted_shell_comments(bash_cmd)


@functools.lru_cache(maxsize=4)
def _has_unsupported_shell_structure(bash_cmd: str) -> bool:
    unquoted_shell = _unquoted_shell_text(bash_cmd)
    return unquoted_shell is None or bool(
        RX_UNSUPPORTED_SHELL_STRUCTURE.search(unquoted_shell)
    )


def _match_is_proven_executed(
    bash_cmd: str, offset: int, bash_exit: int | None, initial_cwd: Path
) -> bool:
    """Conservatively decide whether a textual script match actually ran."""
    command_without_comments = _command_without_comments(bash_cmd)
    if command_without_comments is None:
        return False
    if command_without_comments[offset : offset + 1] != bash_cmd[offset : offset + 1]:
        return False
    if _has_unsupported_shell_structure(bash_cmd):
        return False
    if _command_effective_cwd(bash_cmd, offset, initial_cwd) is None:
        return False
    before_ops, _, before_ambiguous = _control_context_before(bash_cmd, offset)
    if before_ambiguous:
        return False
    if not before_ops:
        # The first command in an AND-OR list always executes.
        return True
    after_ops = _list_operators_after(bash_cmd, offset)
    if after_ops is None:
        # The overall exit status belongs to a later list or an unsupported
        # compound construct, so it cannot prove this conditional branch ran.
        return False
    all_ops = before_ops + after_ops
    if all(operator == "&&" for operator in all_ops):
        return bash_exit == 0 or _and_prefix_is_proven_successful(
            bash_cmd, offset, initial_cwd
        )
    if all(operator == "||" for operator in all_ops):
        # A nonzero final status proves every OR alternative ran and failed.
        # A successful list is normally ambiguous, but a final fallback is
        # still proven to run when every preceding alternative has a known
        # failure (notably ``cd missing || python a.py``).
        return (bash_exit is not None and bash_exit != 0) or _or_prefix_is_proven_failed(
            bash_cmd, offset, initial_cwd
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
        if token in {"--", "-"}:
            # A lone `-` ends option processing exactly like `--`.
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
# A top-level YAML `key: value` line; nested keys are indented and skipped.
_SNAKEMAKE_PROFILE_LINE_RX = re.compile(
    r"""^["']?(?P<key>[A-Za-z][A-Za-z0-9_-]*)["']?[ \t]*:[ \t]*(?P<value>.*)$"""
)
_PROFILE_FALSE_VALUES = frozenset({"", "false", "no", "off", "0", "null", "~", "none"})


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


def _profile_settings(content: str) -> dict[str, list[str]]:
    """Top-level profile keys (normalized to CLI spelling) and their values."""
    settings: dict[str, list[str]] = {}
    for line in content.splitlines():
        match = _SNAKEMAKE_PROFILE_LINE_RX.match(line)
        if match is None:
            continue
        value = match.group("value").split(" #", 1)[0].strip().strip("'\"")
        key = match.group("key").replace("_", "-")
        settings.setdefault(key, []).append(value)
    return settings


def _snakemake_profile_overrides(
    profiles: list[str],
    workflow_profile: str | None,
    cwd: Path,
    snakefile: str | None,
    env: dict[str, str | None],
) -> dict[str, list[str]] | None:
    """Return the settings active profiles apply, or ``None`` when an active
    profile cannot be inspected.

    Mirrors snakemake.cli: ``--profile`` (else ``$SNAKEMAKE_PROFILE``; the
    value ``none`` disables both) is a path or a name searched in the cwd and
    the user/site config dirs; the workflow profile defaults to ``default``
    under ``profiles/`` beside the cwd or the Snakefile unless ``none``.
    Every candidate location is inspected and every value kept, so a
    divergence from Snakemake's own choice can only leave the run
    unresolved, never wrong.
    """
    if not profiles and "SNAKEMAKE_PROFILE" in env:
        env_profile = env["SNAKEMAKE_PROFILE"]
        if env_profile is None:
            return None
        if env_profile:
            profiles = [env_profile]
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
        # Snakemake stops at the first existing candidate (erroring if it
        # holds no profile file); inspecting all of them is a safe superset.
        for candidate in candidates:
            location = candidate if candidate.is_absolute() else cwd / candidate
            if location.exists():
                files.extend(_snakemake_profile_files(location))
    settings: dict[str, list[str]] = {}
    for profile_file in files:
        try:
            content = profile_file.read_bytes()[: 1024 * 1024]
        except OSError:
            return None
        for key, values in _profile_settings(
            content.decode("utf-8", errors="replace")
        ).items():
            settings.setdefault(key, []).extend(values)
    return settings


def _snakemake_detection(
    args: list[str], cwd: Path, env: dict[str, str | None]
) -> _RunnerRun | None:
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
        profiles,
        values.get("workflow_profile"),
        cwd,
        snakefile or default_snakefile,
        env,
    )
    if overrides is not None:
        # Profile keys are command-line options; the same modes run nothing.
        # Merge order across profiles is not modelled, so a mode decides only
        # when every active value agrees: a disagreement keeps the run.
        for key, settings in overrides.items():
            if f"--{key}" in _SNAKEMAKE_NONEXECUTING_LONG and all(
                value.lower() not in _PROFILE_FALSE_VALUES for value in settings
            ):
                return None
        executors = overrides.get("executor", [])
        if (
            "executor" not in values
            and executors
            and all(value in _SNAKEMAKE_NONEXECUTING_EXECUTORS for value in executors)
        ):
            return None
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
    if options.wrap is not None and options.script is not None:
        # Slurm rejects a script name or arguments alongside --wrap.
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


def _bash_script_path(script: str, cwd: Path, env: dict[str, str | None]) -> str:
    """Bash opens its script operand in the cwd, then searches ``PATH``."""
    if "/" in script or (cwd / script).is_file():
        return script
    search_path = env.get("PATH")
    if not search_path:
        return script
    for directory in search_path.split(":"):
        candidate = Path(directory or ".")
        if not candidate.is_absolute():
            candidate = cwd / candidate
        candidate = candidate / script
        if candidate.is_file():
            return str(candidate)
    return script


def _runner_environment(
    bash_cmd: str, prefix: list[str]
) -> dict[str, str | None]:
    """The environment a runner sees: the hook's, plus this command's prefix
    assignments (``VAR=x cmd``, ``env VAR=x cmd``). A variable the command
    sets any other way (``export``, an expansion) is unknown (``None``).
    """
    env: dict[str, str | None] = dict(os.environ)
    assigned: dict[str, int] = {}
    for token in prefix:
        if not _ASSIGNMENT_RX.match(token):
            continue
        key, value = token.split("=", 1)
        env[key] = None if any(marker in value for marker in ("$", "`")) else value
        assigned[key] = assigned.get(key, 0) + 1
    counts = _environment_key_counts(bash_cmd)
    for key in ("PATH", "SNAKEMAKE_PROFILE"):
        if counts[key] > assigned.get(key, 0):
            env[key] = None
    return env


@functools.lru_cache(maxsize=4)
def _environment_key_counts(bash_cmd: str) -> dict[str, int]:
    """How often the command names each modelled variable, counted once per
    command rather than once per runner match (pattern 42)."""
    return {key: bash_cmd.count(key) for key in ("PATH", "SNAKEMAKE_PROFILE")}


def _runner_detection(
    argv: list[str], cwd: Path, env: dict[str, str | None] | None = None
) -> _RunnerRun | None:
    """Dispatch a runner argv to what it executes, or ``None`` for a non-run."""
    env = dict(os.environ) if env is None else env
    name = Path(argv[0]).name
    args = argv[1:]
    if name in _SHELL_RUNNER_NAMES:
        script = _shell_runner_script(name, args)
        if script is None:
            return None
        if name == "bash":
            script = _bash_script_path(script, cwd, env)
        return _RunnerRun(script, None)
    if name == "snakemake":
        return _snakemake_detection(args, cwd, env)
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
    offsets: list[int] | None = None,
    launch_cwds: list[Path | None] | None = None,
) -> list[tuple[Path | None, str | None, Path | None]]:
    """Detect proven pipeline-runner and shell-script executions.

    The third element is the directory the run's I/O resolves against, or
    ``None`` when a runner option moves the run somewhere unknowable. With
    ``offsets``, the position of each detection's runner word is appended
    in the same order, so its stage settings come from its own argv. With
    ``launch_cwds``, the directory each runner was started in (before any
    `--directory` change; None when unknown) is appended likewise.
    """
    out: list[tuple[Path | None, str | None, Path | None]] = []
    for pattern in RUNNER_PATTERNS:
        for m in _scan_word_pattern(pattern, bash_cmd):
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
            prefix = _command_prefix_before(bash_cmd, m.start()) or []
            run = _runner_detection(
                argv, effective_cwd, _runner_environment(bash_cmd, prefix)
            )
            if run is None:
                continue
            # An unmodeled directory change (a CDPATH-searched `cd`, `pushd`,
            # `cd -`, ...): the run's directory is a
            # guess, so its relative I/O and entry file prove nothing.
            cwd_unknown = _effective_cwd_unknown(bash_cmd, m.start(), cwd)
            if run.run_dir_unknown or cwd_unknown:
                io_cwd = None
            elif run.run_dir is None:
                io_cwd = effective_cwd
            else:
                io_cwd = _resolve_runner_directory(run.run_dir, effective_cwd)
            if run.entry is None:
                out.append((None, run.source, io_cwd))
                if offsets is not None:
                    offsets.append(m.start())
                if launch_cwds is not None:
                    launch_cwds.append(None if cwd_unknown else effective_cwd)
                continue
            unresolved = Path(run.entry)
            if not unresolved.is_absolute():
                if cwd_unknown:
                    continue
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
            if offsets is not None:
                offsets.append(m.start())
            if launch_cwds is not None:
                launch_cwds.append(None if cwd_unknown else effective_cwd)
    return out


def _detect_scripts_with_cwd(
    bash_cmd: str,
    cwd: Path,
    *,
    bash_exit: int | None = None,
    require_execution_evidence: bool = False,
    include_inline: bool = True,
    invocations: list[dict[str, Any]] | None = None,
) -> list[tuple[Path | UnresolvedScript | None, str | None, Path | None]]:
    """Return (script_path, inline_source, effective_cwd) detections.

    ``script_path`` is an ``UnresolvedScript`` for an R script run whose
    script cannot be named with certainty. ``include_inline=False`` drops
    inline one-liners (``python -c``, ``R -e``, ``Rscript -e``) for the
    post-action check; lineage keeps them.

    Detections are deduplicated per shell stage: ``python a.py --seed 1 &&
    python a.py --seed 2`` is two runs. With ``invocations``, one dict per
    detection (same order) records where it matched (``offset``), its kind,
    and the script word, so the step's settings can be read from that
    stage's own argv.
    """
    out: list[tuple[Path | UnresolvedScript | None, str | None, Path | None]] = []
    seen_paths: set[tuple[Path, Any]] = set()
    seen_unresolved: set[tuple[UnresolvedScript, Path, str]] = set()
    seen_inline: set[tuple[str, Path | None, Any]] = set()

    def record(offset: int | None, kind: str, word: str | None = None,
               form: str | None = None, *, operand: int | None = None,
               launch_cwd: Path | None = None) -> None:
        if invocations is not None:
            entry: dict[str, Any] = {"offset": offset, "kind": kind, "script_word": word}
            if form is not None:
                # `r-file`: an `R -f`/`R --file=` run, whose script arguments
                # follow `--args` rather than the script word.
                entry["form"] = form
            if operand is not None:
                # Where the matched operand (inline body, module, script
                # word) begins: a startup option may consume a word spelled
                # like it (`python -X -c -c 'print(1)'`), so build_step must
                # not re-find it by spelling.
                entry["operand_offset"] = operand
            if launch_cwd is not None:
                # The directory the runner was started in, before any
                # `--directory` change (Snakemake reads its profile and
                # config files there).
                entry["launch_cwd"] = launch_cwd
            invocations.append(entry)
    inline_patterns = (RX_PYTHON_C, RX_R_E, RX_RSCRIPT_E) if include_inline else ()
    for pattern in inline_patterns:
        inline_language = "python" if pattern is RX_PYTHON_C else "r"
        for m in _scan_word_pattern(pattern, bash_cmd):
            if not _match_has_expected_executable(m, pattern):
                continue
            r_argv = None
            if pattern in (RX_R_E, RX_RSCRIPT_E):
                r_argv = _r_inline_argv(
                    bash_cmd, m.start(), rscript=pattern is RX_RSCRIPT_E
                )
                if r_argv.stopped:
                    continue
            effective_cwd = _command_effective_cwd(bash_cmd, m.start(), cwd)
            if effective_cwd is None:
                continue
            if require_execution_evidence and not _match_is_proven_executed(
                bash_cmd, m.start(), bash_exit, cwd
            ):
                continue
            source_word = m.group("source_word")
            if r_argv is not None:
                if not r_argv.expressions and _expansion(source_word)[0]:
                    continue  # a computed expression is never recorded
                src = _decode_r_word(source_word)
            else:
                src = _decode_shell_word(source_word)
            if src is None:
                continue
            if r_argv is not None and r_argv.expressions:
                src = "\n".join(r_argv.expressions)
            src = InlineSource(src, inline_language)
            identity = (src, effective_cwd, _stage_key(bash_cmd, m.start()))
            if identity not in seen_inline:
                out.append((None, src, _known_io_cwd(bash_cmd, m.start(), cwd, effective_cwd)))
                record(m.start(), "inline", operand=m.start("source_word"))
                seen_inline.add(identity)
    for m in _scan_word_pattern(RX_PYTHON_M, bash_cmd):
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
        identity = (source, effective_cwd, _stage_key(bash_cmd, m.start()))
        if identity not in seen_inline:
            out.append((None, source, _known_io_cwd(bash_cmd, m.start(), cwd, effective_cwd)))
            record(m.start(), "module", module, operand=m.start("module"))
            seen_inline.add(identity)
    for pattern in (
        RX_PYTHON_SCRIPT_PATH,
        RX_R_SCRIPT_PATH,
        RX_R_FILE_PATH,
        RX_R_CMD_BATCH_PATH,
        RX_JUPYTER_SCRIPT_PATH,
    ):
        for m in _scan_word_pattern(pattern, bash_cmd):
            if not _match_has_expected_executable(m, pattern):
                continue
            r_script = None
            if pattern is RX_R_FILE_PATH:
                r_script = _r_file_script(bash_cmd, m)
            elif pattern is RX_R_CMD_BATCH_PATH:
                r_script = _r_batch_script(bash_cmd, m)
            if pattern in (RX_R_FILE_PATH, RX_R_CMD_BATCH_PATH) and r_script is None:
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
            path_word = m.group("path")
            # The script word's own position; an R `-f`/BATCH script is read
            # from its argv instead (the matched word may not be the file).
            path_operand = m.start("path") if r_script is None else None
            if r_script is not None:
                if not r_script.known:
                    unresolved_key = (
                        UnresolvedScript(
                            r_script.text, pattern is RX_R_CMD_BATCH_PATH
                        ),
                        effective_cwd,
                        _stage_key(bash_cmd, m.start()),
                    )
                    if unresolved_key not in seen_unresolved:
                        out.append((unresolved_key[0], None,
                                    _known_io_cwd(bash_cmd, m.start(), cwd, effective_cwd)))
                        record(m.start(), "unresolved", r_script.text,
                               "r-file" if pattern is RX_R_FILE_PATH else None)
                        seen_unresolved.add(unresolved_key)
                    continue
                path_word = shlex.quote(r_script.text)
            try:
                path_words = shlex.split(path_word, comments=False, posix=True)
            except ValueError:
                continue
            if len(path_words) != 1:
                continue
            raw_path = path_words[0]
            if Path(raw_path).name in IGNORED_SCRIPT_BASENAMES:
                continue
            if "$" in raw_path:
                # `P=$(cat .mycelium/plugin-root); python3 $P/skills/core/...`
                # runs a bundled helper: managed, not an unreadable script.
                via_variable = _variable_script_path(bash_cmd, m.start(), raw_path, effective_cwd)
                if via_variable is not None and _is_managed_utility_path(via_variable):
                    continue
            # The accessor dereferences a pointer relative to the effective
            # cwd, so it may resolve only when every cwd change in the prefix
            # was fully modeled; an unmodeled `pushd`/`source`/flagged-cd
            # form means the pointer's directory is unknown (issue #71).
            expanded = None
            _, prefix_cwd_modeled = _effective_cwd_state(bash_cmd, m.start(), cwd)
            cwd_unknown = _effective_cwd_unknown(bash_cmd, m.start(), cwd)
            if prefix_cwd_modeled and not cwd_unknown:
                expanded = _accessor_expanded_script_path(
                    m.group("path"), raw_path, effective_cwd
                )
            if expanded is not None:
                unresolved = expanded
            else:
                unresolved = Path(raw_path)
                if not unresolved.is_absolute():
                    if cwd_unknown:
                        # `cd run` may have gone to $CDPATH/run, `pushd`
                        # somewhere the walk does not follow: which file
                        # ran is unknown, so the run is recorded under its
                        # word alone, with nothing read, hashed or proven.
                        unresolved_key = (
                            UnresolvedScript(raw_path, cwd_unknown=True),
                            effective_cwd,
                            _stage_key(bash_cmd, m.start()),
                        )
                        if unresolved_key not in seen_unresolved:
                            out.append((unresolved_key[0], None, None))
                            record(m.start(), "unresolved", raw_path,
                                   "r-file" if pattern is RX_R_FILE_PATH else None,
                                   operand=path_operand)
                            seen_unresolved.add(unresolved_key)
                        continue
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
            stage_key = (path, _stage_key(bash_cmd, m.start()))
            if stage_key not in seen_paths:
                out.append((path, None, None if cwd_unknown else effective_cwd))
                record(m.start(), "script", raw_path,
                       "r-file" if pattern is RX_R_FILE_PATH else None,
                       operand=path_operand)
                seen_paths.add(stage_key)
    runner_offsets: list[int] = []
    runner_launch_cwds: list[Path | None] = []
    runners = _detect_runners(
        bash_cmd,
        cwd,
        bash_exit=bash_exit,
        require_execution_evidence=require_execution_evidence,
        offsets=runner_offsets,
        launch_cwds=runner_launch_cwds,
    )
    for (path, source, io_cwd), offset, launch_cwd in zip(
            runners, runner_offsets, runner_launch_cwds):
        # Each runner stage is its own run: the same Snakefile with two
        # different config files is two events, each with its own settings.
        stage = _stage_key(bash_cmd, offset)
        if path is not None:
            if (path, stage) not in seen_paths:
                out.append((path, None, io_cwd))
                record(offset, "runner", None, launch_cwd=launch_cwd)
                seen_paths.add((path, stage))
        elif (source, io_cwd, stage) not in seen_inline:
            out.append((None, source, io_cwd))
            record(offset, "runner", None, launch_cwd=launch_cwd)
            seen_inline.add((source, io_cwd, stage))
    return out


@functools.lru_cache(maxsize=8)
def _stage_starts(command: str) -> tuple[int, ...]:
    """Every index where a simple command can begin: 0, and just after each
    unquoted control operator (``;``, newline, ``&&``, ``||``, ``|``,
    ``&``, a parenthesis). One linear scan per command, cached, so many
    matches in one long command cost a bisect each."""
    starts = [0]
    quote: str | None = None
    index = 0
    length = len(command)
    while index < length:
        char = command[index]
        if quote is None:
            if char == "\\":
                index += 2
                continue
            if char in ("'", '"'):
                quote = char
            elif char in ";\n()":
                starts.append(index + 1)
            elif char == "&":
                previous = command[index - 1] if index > 0 else ""
                following = command[index + 1] if index + 1 < length else ""
                if previous not in "<>" and following != ">":
                    starts.append(index + 1)
            elif char == "|":
                if index == 0 or command[index - 1] != ">":
                    starts.append(index + 1)
        elif quote == "'":
            if char == "'":
                quote = None
        elif char == "\\":
            index += 2
            continue
        elif char == '"':
            quote = None
        index += 1
    return tuple(starts)


def _simple_command_start(command: str, offset: int) -> int:
    """Where the simple command containing ``offset`` begins."""
    starts = _stage_starts(command)
    return starts[bisect.bisect_right(starts, offset) - 1]


def _stage_key(command: str, offset: int) -> str:
    """The full text of the stage a match sits in (assignments and wrappers
    included). Identical stages are one run's settings repeated (a loop
    body); stages that differ in any argument are separate runs."""
    start = _simple_command_start(command, offset)
    starts = _stage_starts(command)
    following = bisect.bisect_right(starts, offset)
    stop = starts[following] if following < len(starts) else len(command)
    return command[start:stop].strip(" \t;&|\n()")


def detect_scripts(
    bash_cmd: str, cwd: Path
) -> list[tuple[Path | UnresolvedScript | None, str | None]]:
    """Return every (script_path, inline_source) detection in the command.

    A `python a.py && python b.py` chain yields two tuples; mixed inline and
    file-based forms work too. Each tuple has exactly one non-None field;
    ``script_path`` is an ``UnresolvedScript`` when an R run's script is
    uncertain.
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


class InlineSource(str):
    """Inline source (`python -c`, `R -e`) that remembers its language, so
    the scan applies only that language's lexer and reader/writer calls."""

    language: str | None

    def __new__(cls, value: str, language: str | None) -> "InlineSource":
        instance = super().__new__(cls, value)
        instance.language = language
        return instance


def source_language(script_path: Path | None) -> str | None:
    """The language whose comments can be stripped exactly, if known."""
    if script_path is None:
        return None
    suffix = script_path.suffix
    if suffix == ".py":
        return "python"
    if suffix in (".R", ".r"):
        return "r"
    return None


def scan_source(
    source: str, language: str | None = None
) -> tuple[list[str], list[str], list[str], list[int]]:
    """Literal I/O paths, filters, and seeds named by reader/writer calls.

    With a known ``language``, comments are blanked first, so a commented-
    out call is not evidence. Without one, the source is scanned as written.
    Python and R patterns both apply either way: each matches only its own
    call forms.
    """
    spans = _source_spans(source, language)
    strings: list[tuple[int, int]] = []
    if spans is not None:
        source = _blank(source, spans.comments)
        strings = spans.strings

    def calls(regexes: list[re.Pattern]) -> list[str]:
        # A call that starts inside a string literal is text, not a call.
        return [
            m.group(1)
            for rx in regexes
            for m in rx.finditer(source)
            if not _inside(strings, m.start())
        ]

    # Each language's calls only on its own source: a Python helper named
    # `read_csv` is not R's readr. An unknown language gets both.
    input_rx = {"python": INPUT_REGEXES, "r": R_INPUT_REGEXES}.get(
        language, [*INPUT_REGEXES, *R_INPUT_REGEXES])
    output_rx = {"python": OUTPUT_REGEXES, "r": R_OUTPUT_REGEXES}.get(
        language, [*OUTPUT_REGEXES, *R_OUTPUT_REGEXES])
    inputs: list[str] = calls(input_rx)
    outputs: list[str] = calls(output_rx)
    # Filters are no longer collected: the regex text was never evaluated
    # and had false positives. The slot stays in the return value so callers
    # keep their shape; it is always empty.
    filters: list[str] = []
    seeds: list[int] = []
    seed_rx = {"python": SEED_REGEXES, "r": R_SEED_REGEXES}.get(
        language, [*SEED_REGEXES, *R_SEED_REGEXES])
    for rx in seed_rx:
        for m in rx.finditer(source):
            if _inside(strings, m.start()):
                continue  # a seed call quoted in a string is text
            try:
                seeds.append(int(m.group(1)))
            except ValueError:
                pass
    return _dedupe(inputs), _dedupe(outputs), filters, sorted(set(seeds))


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


# Values a script receives at run time. A path built from one may point outside
# the repository, so repository recovery must not match its literal suffix
# against a same-named repository file.
_RUNTIME_PATH_SOURCES = {
    "fileinput.input",
    "input",
    "os.environ",
    "os.environb",
    "os.getenv",
    "os.getenvb",
    "sys.argv",
    "sys.stdin",
}
# ``parser.parse_args()`` and friends return the parsed command line.
_RUNTIME_ARGUMENT_PARSERS = {
    "parse_args",
    "parse_intermixed_args",
    "parse_known_args",
    "parse_known_intermixed_args",
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


def _call_root(node: ast.AST) -> str | None:
    """The name a dotted callee starts from, or None for an unnamed receiver."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _io_call_direction(
    node: ast.Call,
    import_aliases: dict[str, str],
    locally_bound_roots: set[str],
) -> str | None:
    raw_name = _call_name(node.func)
    if not raw_name:
        return None
    parts = raw_name.split(".")
    # ``_call_name`` reduces ``read_csv(...).to_csv`` to the bare ``to_csv``.
    # Only a callee rooted at a name may be resolved through an import alias;
    # otherwise ``from helpers import x as to_csv`` would rename the method.
    if _call_root(node.func) is not None and parts[0] in import_aliases:
        parts = import_aliases[parts[0]].split(".") + parts[1:]
    name = ".".join(parts)
    leaf = name.rsplit(".", 1)[-1]
    if leaf in _OUTPUT_CALL_LEAVES or name in {
        "np.save",
        "np.savez",
        "np.savez_compressed",
    }:
        # Object methods such as ``frame.to_csv`` are conventional writers,
        # whatever the receiver: a name, a call result such as
        # ``pd.read_csv(...).to_csv`` or ``frame.head(n).to_csv``, or a
        # subscript. ``_call_name`` drops an unnamed receiver, so test the AST
        # node rather than the dotted name. As for a named receiver, a
        # receiver whose type cannot be known statically counts as a writer.
        # A bare writer name, however, is evidence only when an import maps
        # it to a known data-I/O package.
        if (
            isinstance(node.func, ast.Attribute)
            or parts[0] in _KNOWN_IO_MODULE_ROOTS
        ):
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

    def canonical_name(node: ast.AST) -> str | None:
        raw_name = _call_name(node)
        if not raw_name:
            return None
        parts = raw_name.split(".")
        if parts[0] in import_aliases:
            parts = import_aliases[parts[0]].split(".") + parts[1:]
        return ".".join(parts)

    def reads_runtime_value(expression: ast.AST, tainted: set[str]) -> bool:
        for node in ast.walk(expression):
            if isinstance(node, ast.Name) and node.id in tainted:
                return True
            if isinstance(node, (ast.Attribute, ast.Name)):
                root = _call_name(node)
                root = root.split(".", 1)[0] if root else None
                if (
                    canonical_name(node) in _RUNTIME_PATH_SOURCES
                    and (root in import_aliases or root not in locally_bound_roots)
                ):
                    return True
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _RUNTIME_ARGUMENT_PARSERS
            ):
                return True
        return False

    # Names any of whose assignments draw on a runtime value, propagated
    # through further assignments until nothing changes. A name with several
    # definitions is tainted by any one of them: recovery should then stay
    # unresolved rather than guess which definition ran. Function and lambda
    # parameters are runtime values too: every caller, and any default, can
    # pass a directory outside the repository. Matching by name across the
    # file only ever leaves more paths unresolved, never records a wrong file.
    # Unpacked names (``a, b = dirs``) carry their source's taint too, though
    # recovery never resolves them to a value.
    taint_sources = {name: list(values) for name, values in assignments.items()}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, (ast.List, ast.Tuple)):
                    for item in ast.walk(target):
                        if isinstance(item, ast.Name):
                            taint_sources.setdefault(item.id, []).append(node.value)
    runtime_names: set[str] = set(shadowed_names)
    changed = True
    while changed:
        changed = False
        for name, definitions in taint_sources.items():
            if name in runtime_names:
                continue
            if any(reads_runtime_value(item, runtime_names) for item in definitions):
                runtime_names.add(name)
                changed = True

    def runtime_dependent(expression: ast.AST) -> bool:
        """Whether a path expression draws on a value chosen at run time.

        A command-line argument, environment value or function parameter
        can name a directory outside the repository, so the literal filename
        appended to it says nothing about which repository file, if any, was
        read or written.
        """
        return reads_runtime_value(expression, runtime_names)

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
            expression_values = (
                None if runtime_dependent(expression) else collect(expression)
            )
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


# A URL scheme (`https://`, `s3://`, `gs://`, `file://`). Two or more
# characters before the colon, so a Windows drive (`C:/`) is not one; the
# single-slash form is what `Path` leaves of `scheme://` once normalized.
_URL_RX = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]+:/")


def _is_url(value: object) -> bool:
    """Whether a literal names a URL rather than a filesystem path. A URL is
    never resolved against a working directory, hashed, or recorded as a
    local path."""
    return isinstance(value, str) and bool(_URL_RX.match(value.strip()))


def _redact_url(value: str) -> str:
    """A URL as it may be kept: the shared redaction policy first (userinfo
    passwords, `https://user:pass@host`), then any userinfo left (a bare
    token, `https://ghp_x@host`) and any query or fragment (`?api_key=`),
    which can carry credentials the policy does not name."""
    import lineage_redact

    text = lineage_redact.redact_text(value)
    match = re.match(r"([A-Za-z][A-Za-z0-9+.-]*://)([^/?#]*)(.*)\Z", text, re.S)
    if not match:
        return text
    scheme, authority, rest = match.groups()
    if "@" in authority:
        authority = lineage_redact.REDACTED + "@" + authority.rpartition("@")[2]
    for mark in ("?", "#"):
        if mark in rest:
            rest = rest.split(mark, 1)[0] + mark + lineage_redact.REDACTED
            break
    return scheme + authority + rest


def _split_urls(paths: list[str]) -> tuple[list[str], list[str]]:
    """(local paths, redacted URLs) from scanned literals."""
    local: list[str] = []
    remote: list[str] = []
    for value in paths:
        if _is_url(value):
            redacted = _redact_url(value.strip())
            if redacted not in remote:
                remote.append(redacted)
        else:
            local.append(value)
    return local, remote


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


# --- Data commands: a minimal record for every other Bash call on data ---
#
# The analysis detector above records only interpreter runs it can prove.
# `head data/x.csv`, `cp a.tsv outputs/`, and inline reads it rejects (a
# heredoc it cannot model, a compound `cd ...; Rscript x.R`) used to leave
# nothing. Each such call now leaves one `"kind": "data_command"` record.
# The record is a different kind from an analysis event on purpose: it never
# claims that an analysis ran. It keeps the literal data-like words the
# command text names (`named_paths`, no direction, nothing read or hashed
# because a word in text is not evidence of access), and lists `inputs` or
# `outputs` only where shell syntax alone makes the direction certain: a
# redirection or a `cp`/`mv` operand in a command that exited 0.

# Extensions a shell command on data commonly names, beyond the repository
# scan's own list (which stays unchanged: it decides hashing, pattern 26).
_DATA_COMMAND_SUFFIXES = _DATA_FILE_SUFFIXES | {
    ".tab",
    ".dat",
    ".arrow",
    ".rda",
    ".mtx",
    ".fa",
    ".fasta",
    ".fna",
    ".fq",
    ".fastq",
    ".bam",
    ".sam",
    ".cram",
    ".bai",
    ".vcf",
    ".bcf",
    ".bed",
    ".bedgraph",
    ".bw",
    ".bigwig",
    ".gtf",
    ".gff",
    ".gff3",
    ".nwk",
    ".newick",
    ".tre",
    ".tree",
    ".phy",
    ".aln",
    ".maf",
    ".tif",
    ".tiff",
    ".fcs",
    ".zarr",
    ".mat",
    ".sqlite",
    ".db",
}
_DATA_COMMAND_COMPRESSION_SUFFIXES = {".gz", ".bgz", ".bz2", ".xz", ".zst", ".zip"}
# Path segments that hold tooling or Mycelium's own state, never project data.
_DATA_COMMAND_EXCLUDED_SEGMENTS = {
    ".git",
    ".living",
    ".mycelium",
    ".claude",
    ".codex",
    ".venv",
    "venv",
    "node_modules",
    "site-packages",
    "__pycache__",
}
# Configuration files that carry a data-like extension.
_DATA_COMMAND_TOOLING_BASENAMES = {
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "jsconfig.json",
    "composer.json",
    "plugin.json",
    "marketplace.json",
    "hooks.json",
    "settings.json",
    "settings.local.json",
    "launch.json",
    "pyrightconfig.json",
    "requirements.txt",
    "constraints.txt",
    "cmakelists.txt",
    "license.txt",
}
# A candidate word: a maximal run of characters that cannot delimit a path
# in shell or in the code a command embeds. One negated character class, so
# `finditer` is linear in the command (pattern 42).
_DATA_COMMAND_WORD_RX = re.compile(r"[^\s`;|&<>(){}\[\],=:]+")
DATA_COMMAND_NAMED_PATH_LIMIT = 64
UNPROVEN_DETECTION_LIMIT_CHARS = 64 * 1024


@functools.lru_cache(maxsize=8)
def _project_root(cwd: str) -> str | None:
    """The nearest ancestor of ``cwd`` holding `.git` (a checkout or a
    worktree), found without starting a process."""
    path = Path(cwd)
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return str(candidate)
    return None


def _is_data_like_path(word: str, root: str | None = None) -> bool:
    """Whether a literal word names data: a data file extension, or a path
    under `data/` or `analysis/**/outputs/`. Tooling and Mycelium state are
    not data. Under the project root, only the part inside it is judged, so
    a project that itself lives in `.claude/worktrees/<name>` still has
    data."""
    if not word or "://" in word or word.startswith("//"):
        # A URL; the word scan splits `https:` from `//host/...`.
        return False
    if root and word.startswith(root + "/"):
        word = word[len(root) + 1 :]
    segments = [segment for segment in word.split("/") if segment not in ("", ".")]
    if not segments:
        return False
    if any(segment in _DATA_COMMAND_EXCLUDED_SEGMENTS for segment in segments):
        return False
    name = segments[-1].lower()
    if name in _DATA_COMMAND_TOOLING_BASENAMES:
        return False
    stem, suffix = os.path.splitext(name)
    if suffix in _DATA_COMMAND_COMPRESSION_SUFFIXES and stem:
        return True
    if suffix in _DATA_COMMAND_SUFFIXES and stem:
        return True
    # A data directory itself (`ls data/`, `find analysis/x/outputs`) or
    # anything beneath one. A bare `data` with no slash is too common a word
    # (a variable, a column) to count.
    analysis_seen = False
    for index, segment in enumerate(segments):
        if segment == "data" and (index < len(segments) - 1 or "/" in word):
            return True
        if segment == "outputs" and analysis_seen:
            return True
        analysis_seen = analysis_seen or segment == "analysis"
    return False


def _data_path_mentions(command: str, root: str | None = None) -> tuple[list[str], bool]:
    """Literal data-like words the command text names, in first-seen order.

    This is a mention list, not an access list: the word may sit in a
    comment, an `echo`, or an unexecuted branch. Returns (words, truncated).
    """
    seen: dict[str, None] = {}

    for match in _DATA_COMMAND_WORD_RX.finditer(command):
        # Quote removal joins a partly quoted word (`"data"/x.csv`). A
        # quoted name holding a space is read in pieces (`data/my`), which
        # still marks the call as touching data.
        word = match.group(0).replace("'", "").replace('"', "")
        if word in seen or not _is_data_like_path(word, root):
            continue
        if len(seen) >= DATA_COMMAND_NAMED_PATH_LIMIT:
            return list(seen), True
        seen[word] = None
    return list(seen), False


# Flags that leave `cp SRC... DST` / `mv SRC... DST` operand roles unchanged
# and always perform the copy or move when the command exits 0. Anything else
# makes the argv unmodelled, and no direction is claimed (pattern 13):
# `-t DIR`, `-T`, `-s`, `-S SUFFIX`, any long option, and `-n`/`-i`/`-u`,
# which can skip an existing destination and still exit 0.
_CP_FLAGS = frozenset("fpRrvaLHP")
_MV_FLAGS = frozenset("fv")
_INPUT_REDIRECTIONS = {"<"}
# Command words after which a zero exit no longer proves that this or a later
# command in the `&&` list ran, or that its redirections opened.
_STATUS_UNPROVEN_WORDS = frozenset(
    {"!", "exit", "return", "logout", "exec", "set", "shopt", "trap", "kill",
     "eval", "source", ".", "builtin", "command", "enable", "alias",
     "break", "continue",
     # A wrapper or reserved word runs something this parser does not read
     # (`time exit 0`, `coproc cmd` whose status is the shell's, `env -S`).
     "time", "coproc", "nohup", "env", "nice", "timeout", "stdbuf", "taskset",
     "xargs", "sudo", "doas", "function", "select", "wait", "disown",
     # These change which program a later name runs (`hash -p /bin/true cp`,
     # `unset -f`, `export PATH=...`), so a later `cp` may not be cp.
     "hash", "unset", "export", "declare", "typeset", "readonly", "local",
     "unalias"}
)
# Commands that cannot move or remove a directory, so the remembered working
# directory still names the shell's own after they run.
_CWD_STABLE_COMMANDS = frozenset(
    {"head", "tail", "cat", "wc", "ls", "echo", "printf", "true", "stat", "file",
     "md5", "md5sum", "shasum", "sha256sum", "sha1sum", "du", "diff", "cmp",
     "pwd", "cut", "uniq", "grep", "rg", "column", "zcat", "gzcat", "mkdir",
     "touch", "sort", "tr", "paste", "join", "tee", "jq", "test", "["}
)  # not cp: `cp -P` can replace a symlink the working directory went through
# Commands that, later in the same `&&` list, cannot change which file a
# `cp`/`mv` wrote or whether it is still a directory; anything else after a
# copy makes its destination, read from the final filesystem, uncertain.
_INERT_AFTER_COPY = frozenset(
    {"head", "tail", "cat", "wc", "ls", "echo", "printf", "true", "stat", "file",
     "md5", "md5sum", "shasum", "sha256sum", "sha1sum", "du", "diff", "cmp", "cd",
     "pwd", "cut", "uniq", "grep", "rg", "column", "zcat", "gzcat"}
)
_OUTPUT_REDIRECTIONS = {">", ">>", ">|", "&>", "&>>"}
# Unquoted characters that make a word's value depend on the shell (globs,
# braces, tilde) rather than on its literal text.
_EXPANDING_UNQUOTED = frozenset("*?[]{}~")


class _ShellIO(NamedTuple):
    inputs: list[tuple[str, str]]
    outputs: list[tuple[str, str]]


def _literal_and_list(command: str) -> list[str] | None:
    """Split a command that is only simple commands joined by `&&`.

    Any other control operator, subshell, substitution, expansion, heredoc,
    or unquoted glob returns None: the shell decides what those run or
    name, so no direction is certain.
    """
    if "$" in command or "`" in command:
        return None
    masked = _unquoted_shell_text(command)
    if masked is None:
        return None
    parts: list[str] = []
    start = 0
    index = 0
    length = len(masked)
    while index < length:
        char = masked[index]
        if char == "&" and masked[index + 1 : index + 2] == "&":
            parts.append(command[start:index])
            index += 2
            start = index
            continue
        previous = masked[index - 1] if index else ""
        if char == "&":
            # `&>`, `&>>`, and descriptor duplication (`2>&1`, `<&3`) are
            # redirections; a lone `&` backgrounds a command.
            if masked[index + 1 : index + 2] != ">" and previous not in ("<", ">"):
                return None
        elif char == "|" and previous == ">":
            pass  # `>|` is a redirection, not a pipe.
        elif char in ";|()\n\r":
            return None
        elif char in _EXPANDING_UNQUOTED:
            return None
        elif char == "<" and masked[index + 1 : index + 2] == "<":
            return None
        elif char == "#":
            # `_unquoted_shell_text` already blanked comments, so this `#`
            # sits inside a word; harmless.
            pass
        index += 1
    parts.append(command[start:])
    return [part for part in parts]


_LITERAL_OPERATORS = ("&>>", ">>", ">|", "&>", "<&", ">&", "<>", "<<", "<", ">")


def _simple_command_words(fragment: str) -> tuple[list[str], list[tuple[str, str]]] | None:
    """Return (argv, [(redirection operator, target)]) for one simple command
    of literal words (the caller already refused `$`, backticks, globs, and
    control operators). A single linear pass: `shlex` rebuilds each token one
    character at a time, which is quadratic on a long literal argument."""
    fragment = _strip_unquoted_shell_comments(_blank_io_numbers(fragment))
    if fragment is None:
        return None
    tokens: list[tuple[str, bool]] = []  # (text, is_operator)
    word: list[str] = []
    in_word = False
    index = 0
    length = len(fragment)
    while index < length:
        char = fragment[index]
        if char in " \t\r":
            if in_word:
                tokens.append(("".join(word), False))
                word, in_word = [], False
            index += 1
        elif char == "\\":
            if index + 1 >= length:
                return None
            word.append(fragment[index + 1])
            in_word = True
            index += 2
        elif char == "'":
            close = fragment.find("'", index + 1)
            if close < 0:
                return None
            word.append(fragment[index + 1 : close])
            in_word = True
            index = close + 1
        elif char == '"':
            index += 1
            while index < length and fragment[index] != '"':
                if fragment[index] == "\\" and fragment[index + 1 : index + 2] in ('"', "\\"):
                    index += 1
                word.append(fragment[index])
                index += 1
            if index >= length:
                return None
            in_word = True
            index += 1
        elif char in "<>&|":
            if in_word:
                tokens.append(("".join(word), False))
                word, in_word = [], False
            operator = next(
                (op for op in _LITERAL_OPERATORS if fragment.startswith(op, index)),
                None,
            )
            if operator is None:
                return None
            tokens.append((operator, True))
            index += len(operator)
        else:
            word.append(char)
            in_word = True
            index += 1
    if in_word:
        tokens.append(("".join(word), False))
    argv: list[str] = []
    redirections: list[tuple[str, str]] = []
    pending: str | None = None
    for text, is_operator in tokens:
        if pending is not None:
            if is_operator:
                return None
            redirections.append((pending, text))
            pending = None
        elif is_operator:
            pending = text
        else:
            argv.append(text)
    if pending is not None:
        return None
    return argv, redirections


def _resolve_shell_path(word: str, cwd: Path | None) -> Path | None:
    path = Path(word)
    if path.is_absolute():
        return path
    if cwd is None:
        return None
    return cwd / path


def _copy_move_pairs(argv: list[str], cwd: Path | None) -> list[tuple[Path, Path]] | None:
    """(source, destination file) pairs for a modelled `cp`/`mv` argv, after
    the command ran. None when the argv or a destination is not certain."""
    flags = _CP_FLAGS if argv[0] == "cp" else _MV_FLAGS
    operands: list[str] = []
    options_done = False
    for word in argv[1:]:
        if not options_done and word == "--":
            options_done = True
        elif not options_done and word.startswith("-") and word != "-":
            if word.startswith("--") or not set(word[1:]) <= flags:
                return None
        else:
            operands.append(word)
    if len(operands) < 2:
        return None
    resolved = [_resolve_shell_path(word, cwd) for word in operands]
    if any(path is None for path in resolved):
        return None
    *sources, destination = resolved
    assert destination is not None
    if len(sources) > 1 and not destination.is_dir():
        return None
    pairs: list[tuple[Path, Path]] = []
    for source in sources:
        assert source is not None
        if source.is_symlink():
            # `cp -a`/`-P` copies the link itself; plain `cp` reads its
            # target. Which one happened depends on flags and platform.
            return None
        into = destination / source.name
        if argv[0] == "cp":
            # A copied directory's destination name depends on whether the
            # destination existed beforehand, which is no longer knowable.
            if not source.is_file():
                return None
            target = into if destination.is_dir() else destination
            if target.is_symlink() or (
                target.exists() and os.path.samefile(source, target)
            ):
                return None
        else:
            # The source is gone after a real move; if it is still there
            # (`mv x ./x` is a no-op that exits 0) nothing moved.
            if source.exists() or source.is_symlink():
                return None
            # Only a plain rename onto a regular file is certain. After the
            # move, a file inside a destination directory could equally be a
            # child of a renamed directory, and a moved symlink would hash
            # its referent, so both claim nothing.
            if (
                len(sources) == 1
                and destination.is_file()
                and not destination.is_symlink()
            ):
                target = destination
            else:
                return None
        pairs.append((source, target))
    return pairs


def _certain_shell_io(command: str, cwd: Path, bash_exit: int | None) -> _ShellIO:
    """Paths whose direction shell syntax alone proves.

    Only for a command that exited 0 and is a plain `&&` list of literal
    simple commands: then every command ran and succeeded, so its
    redirections were opened and a modelled `cp`/`mv` read its sources and
    wrote its destinations. `cd DIR` with a literal existing directory moves
    the cwd; any other cwd-changing builtin makes later relative paths
    unknown. Everything else claims nothing.
    """
    io = _ShellIO([], [])
    if bash_exit != 0:
        return io
    root = _project_root(str(cwd))
    parts = _literal_and_list(command)
    if parts is None:
        return io
    parsed = []
    for part in parts:
        words = _simple_command_words(_join_line_continuations(part))
        if words is None:
            return io
        parsed.append(words)
    # inert_after[i]: every command after the i-th leaves files alone.
    inert_after = [True] * len(parsed)
    for position in range(len(parsed) - 2, -1, -1):
        argv_next, redirections_next = parsed[position + 1]
        words_next = [w for w in argv_next if not _ASSIGNMENT_RX.match(w)][:1]
        inert_after[position] = inert_after[position + 1] and bool(words_next) and (
            words_next[0] in _INERT_AFTER_COPY
            and not any(op in _OUTPUT_REDIRECTIONS or op == "<>" for op, _ in redirections_next)
        )
    cdpath_possible = _cdpath_may_be_set(command) or _cdpath_env_set()
    current: Path | None = cwd
    for position, (argv, redirections) in enumerate(parsed):
        first = 0
        while first < len(argv) and _ASSIGNMENT_RX.match(argv[first]):
            if argv[first].startswith(("PATH=", "BASH_ENV=", "ENV=")):
                return io  # the command name may now resolve elsewhere
            first += 1
        argv = argv[first:]
        if argv and argv[0] in _STATUS_UNPROVEN_WORDS:
            # `exit 0 && ...`, `set -n && ...`, `! cmd < missing`: the final
            # status no longer proves this or any later command ran (or
            # that its redirections opened). Earlier commands' claims stand.
            return io
        for operator, target in redirections:
            if _is_url(target):
                # A URL-shaped word (`> s3://user:pass@b/o.csv`) is never
                # recorded as a local path: it would carry its userinfo.
                continue
            if not _is_data_like_path(target, root) and not _is_data_like_path(
                str(_resolve_shell_path(target, current) or ""), root
            ):
                continue
            path = _resolve_shell_path(target, current)
            if path is None:
                continue
            if operator in _INPUT_REDIRECTIONS:
                io.inputs.append((str(path), "redirection"))
            elif operator in _OUTPUT_REDIRECTIONS:
                io.outputs.append((str(path), "redirection"))
        if not argv:
            continue
        if argv[0] in ("cp", "mv"):
            # The destination's kind is read from the final filesystem, so
            # only when nothing later in the list could have changed it.
            pairs = _copy_move_pairs(argv, current) if inert_after[position] and not any(
                _is_url(word) for word in argv[1:]) else None
            for source, target in pairs or []:
                if _is_data_like_path(str(source), root) or _is_data_like_path(
                    str(target), root
                ):
                    io.inputs.append((str(source), argv[0]))
                    io.outputs.append((str(target), argv[0]))
        elif argv[0] == "cd":
            relative_name = not argv[1].startswith(("/", "./", "../")) and argv[1] not in (
                ".", "..") if len(argv) == 2 else False
            if len(argv) == 2 and relative_name and cdpath_possible:
                # `cd name` searches $CDPATH first, which the agent's shell
                # may set; `./name`, `../name`, and absolute paths do not.
                current = None
            elif len(argv) == 2 and not argv[1].startswith("-"):
                target_dir = _resolve_shell_path(argv[1], current)
                # Plain `cd` is logical: `..` drops the last component of
                # the path as written, not of a symlink's target.
                if target_dir is not None:
                    target_dir = Path(os.path.normpath(target_dir))
                current = target_dir if target_dir is not None and target_dir.is_dir() else None
            else:
                current = None
        elif argv[0] in _CWD_AFFECTING_BUILTINS or argv[0] in ("exec", "command", "builtin"):
            current = None
        if argv[0] != "cd" and argv[0].rsplit("/", 1)[-1] not in _CWD_STABLE_COMMANDS:
            # Anything else may have moved or removed the working directory
            # (`mv`, `/bin/mv`, `git mv`, a script): the shell stays in the
            # renamed directory while the remembered path names another, so
            # later relative paths are unresolved. Absolute paths still are.
            current = None
    return io


def _io_records(paths: list[tuple[str, str]], cwd: Path) -> list[dict]:
    records: list[dict] = []
    seen: set[str] = set()
    for path, evidence in paths:
        if path in seen or _is_url(path):
            # A URL-shaped word (`> s3://user:pass@b/o.csv`) is never
            # recorded as a local path: it would carry its userinfo along.
            continue
        seen.add(path)
        record = file_record(path, cwd)
        record["evidence"] = evidence
        records.append(record)
    return records


# Words that may precede the command word of a simple command without
# changing which program runs.
_COMMAND_POSITION_PREFIXES = frozenset(
    {"do", "then", "else", "elif", "if", "while", "until", "!", "{"}
)
_SEGMENT_WORD_LIMIT = 256
_SEGMENT_TEXT_LIMIT = 16 * 1024
# Segments the detector reads per command (distinct ones; repeats are free).
_SEGMENT_DETECTION_LIMIT = 256


def _segment_run_labels(run_words: list[str], cwd: Path) -> list[str]:
    """What the detector's own grammar says one simple command runs, without
    requiring proof that it ran. Shared grammar, not an imitation of it
    (pattern 36): terminal and non-executing modes, payload boundaries,
    tooling modules, and Mycelium's helpers are excluded exactly as there."""
    text = " ".join(shlex.quote(word) for word in run_words)
    if len(text) > _SEGMENT_TEXT_LIMIT or not is_analysis(text):
        # A simple command this long is a data payload (`printf '<800 KB>'`),
        # not a run to name; reading it would cost more than the label.
        return []
    try:
        detections = _detect_scripts_with_cwd(
            text, cwd, require_execution_evidence=False
        )
    except Exception:  # noqa: BLE001 - a label only; never fail the record
        return []
    labels: list[str] = []
    for script_path, inline_source, _ in detections:
        if isinstance(script_path, UnresolvedScript):
            label = script_path.candidate
        elif script_path is not None:
            label = str(script_path)
            if _names_managed_utility(label):
                continue
        elif inline_source is not None:
            label = f"{run_words[0]} (inline)"
        else:
            continue
        labels.append(label)
    return labels


def _stdin_fed_run_label(
    run_words: list[str], via_xargs: bool, heredoc: bool
) -> list[str]:
    """An interpreter that reads its program or file names from stdin, which
    the detector cannot see: `python3 - <<'PY'`, `R --no-save <<'RS'`,
    `... | xargs python3`. Only a bare interpreter (options, or `-`, and no
    other operand) qualifies, so `python3 --version` and the like do not."""
    name = run_words[0].rsplit("/", 1)[-1]
    if not (heredoc or via_xargs) or not _STDIN_INTERPRETER_RX.fullmatch(name):
        return []
    rest = run_words[1:]
    python = name not in ("R", "Rscript")
    position = 0
    while position < len(rest):
        word = rest[position]
        if word in ("-W", "-X", "--encoding"):
            position += 2  # an option and its separate value
            continue
        if word == "-":
            position += 1
            continue
        if not word.startswith("-") or word in ("-c", "-m", "-e", "-f", "--file"):
            return []  # a payload operand: the detector's case, not stdin's
        if word in _STDIN_TERMINAL_FLAGS:
            return []
        if python and not word.startswith("--"):
            cluster = word[1:]
            # A short cluster ends at a value-taking letter; `c`/`m` start a
            # payload and `V`/`h` print and exit (`-uV`).
            for letter in cluster:
                if letter in "cm" or letter in "Vh?":
                    return []
                if letter in "WX":
                    break
        position += 1
    label = " ".join(run_words[:2]) if rest else name
    return [label + (" <<heredoc" if heredoc else "")]


_STDIN_INTERPRETER_RX = re.compile(rf"(?:{_PYTHON_EXE_NAME}|R|Rscript)")
_STDIN_TERMINAL_FLAGS = frozenset({"--version", "-V", "--help", "-h", "-?"})


_LABELLED_SCRIPT_SUFFIXES = (".sh", ".bash", ".py", ".R", ".r")
# Wrappers that run the command after their own options, and the options of
# theirs that take a separate value (labels only; nothing here is proof).
_LABEL_WRAPPERS = frozenset(
    {"time", "nohup", "nice", "timeout", "env", "stdbuf", "taskset", "xargs",
     "uv", "poetry", "pipenv", "pixi", "hatch", "conda", "mamba", "micromamba",
     "srun", "sudo", "exec", "command", "builtin"}
)
_CONDA_VALUE_OPTIONS = frozenset({"-n", "--name", "-p", "--prefix"})
_LABEL_WRAPPER_VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "nice": frozenset({"-n", "--adjustment"}),
    "timeout": frozenset({"-s", "--signal", "-k", "--kill-after"}),
    "env": frozenset({"-u", "--unset", "-C", "--chdir"}),
    "sudo": frozenset({"-u", "--user", "-g", "--group"}),
    "xargs": frozenset({"-n", "-P", "-L", "-I", "-s", "-d", "-a", "-E",
                        "--max-args", "--max-procs", "--delimiter", "--arg-file"}),
    "uv": frozenset({"--with", "--python", "-p", "--project", "--directory",
                     "--env-file"}),
    "poetry": frozenset({"-C", "--directory", "-P", "--project"}),
    "conda": _CONDA_VALUE_OPTIONS,
    "mamba": _CONDA_VALUE_OPTIONS,
    "micromamba": _CONDA_VALUE_OPTIONS,
    "pixi": frozenset({"-e", "--environment", "--manifest-path"}),
    "hatch": frozenset({"-e", "--env"}),
}
_DURATION_RX = re.compile(r"\d+(?:\.\d+)?[smhd]?")


def _skip_command_wrappers(words: list[str]) -> tuple[int, bool]:
    """Index of the command a segment's wrappers finally run, and whether
    `xargs` was one of them. Linear in the segment's words."""
    index = 0
    via_xargs = False
    while index < len(words):
        word = words[index]
        name = word.rsplit("/", 1)[-1]
        if word in _COMMAND_POSITION_PREFIXES or _ASSIGNMENT_RX.match(word):
            index += 1
            continue
        if name not in _LABEL_WRAPPERS:
            break
        via_xargs = via_xargs or name == "xargs"
        value_options = _LABEL_WRAPPER_VALUE_OPTIONS.get(name, frozenset())
        index += 1
        while index < len(words):
            arg = words[index]
            if arg in ("run", "exec", "--"):
                index += 1
            elif arg.startswith("-") and arg != "-":
                index += 2 if arg in value_options else 1
            elif _ASSIGNMENT_RX.match(arg) or _DURATION_RX.fullmatch(arg):
                index += 1
            else:
                break
    return index, via_xargs
_SEGMENT_SPLIT_RX = re.compile(r"[;&|()\n]")


def _names_managed_utility(text: str) -> bool:
    """Whether text names a bundled Mycelium helper by its managed suffix.

    Only gates an unproven label, never lineage: Mycelium's own tool calls
    are not data commands (pattern 18). The plugin root is not verified, so
    a same-named user script that is not proven to run also gets no label.
    """
    if _MANAGED_SCRIPTS_DIR_SUFFIX not in text:
        return False
    return any(suffix in text for suffix in IGNORED_SCRIPT_SUFFIXES)


def _heredoc_delimiter(line: str, index: int) -> tuple[str | None, int]:
    """Read the delimiter word after `<<` / `<<-` at ``index`` with quote
    removal (`EOF`, `'EOF'`, `E"OF"`, `\\EOF`), returning (word, end). The
    word is None when it is missing or a quote does not close on the line."""
    while index < len(line) and line[index] in " \t":
        index += 1
    parts: list[str] = []
    while index < len(line) and line[index] not in " \t;&|<>()":
        char = line[index]
        if char == "\\":
            parts.append(line[index + 1 : index + 2])
            index += 2
        elif char in "'\"":
            close = line.find(char, index + 1)
            if close < 0:
                return None, len(line)
            parts.append(line[index + 1 : close])
            index = close + 1
        else:
            parts.append(char)
            index += 1
    word = "".join(parts)
    return (word or None), index


def _heredoc_starts(line: str, quote: str | None) -> tuple[list[tuple[str, bool]], str | None]:
    """Heredoc delimiters opened by unquoted `<<` on one line, in order, and
    the quote still open at its end. ``quote`` is the quote open at its
    start, so a quoted multi-line argument that shows `cat <<EOF` opens
    nothing."""
    starts: list[tuple[str, bool]] = []
    index = 0
    while index < len(line):
        char = line[index]
        if quote is None:
            if char == "\\":
                index += 2
                continue
            if char in "'\"":
                quote = char
            elif char == "#" and (index == 0 or line[index - 1] in " \t;&|()"):
                break
            elif line.startswith("<<<", index):
                index += 3  # a here-string: its word is data, not a delimiter
                continue
            elif line.startswith("<<", index):
                strip_tabs = line.startswith("<<-", index)
                word, index = _heredoc_delimiter(line, index + (3 if strip_tabs else 2))
                if word is not None:
                    starts.append((word, strip_tabs))
                continue
        elif char == quote:
            quote = None
        elif quote == '"' and char == "\\":
            index += 1
        index += 1
    return starts, quote


def _blank_heredoc_bodies(command: str) -> str:
    """The command with heredoc body lines blanked, offsets preserved.

    A body is data for the program reading it (a script, a README), not
    shell text, so `cat > NOTES.md <<EOF` followed by `python fit.py` names
    no run. Quote state carries across lines. One pass: linear.
    """
    lines = command.split("\n")
    pending: list[tuple[str, bool]] = []
    quote: str | None = None
    for number, line in enumerate(lines):
        if pending:
            delimiter, strip_tabs = pending[0]
            candidate = line.lstrip("\t") if strip_tabs else line
            if candidate == delimiter:
                pending.pop(0)
            # The terminator is not shell text either (`R` closing a heredoc
            # is not an R run).
            lines[number] = " " * len(line)
            continue
        pending, quote = _heredoc_starts(line, quote)
    return "\n".join(lines)


def _interpreter_runs_named(command: str, cwd: Path) -> list[str]:
    """Runs named by the simple commands inside structure the detector does
    not model as a whole (`for ...; do Rscript x.R; done`, `python3 - <<'PY'`).
    Each segment, unwrapped, goes through the detector's own grammar without
    requiring execution proof; stdin-fed interpreters it cannot see get a
    label of their own. A label of what the command names, not proof that it
    ran. Heredoc bodies are not shell text and are skipped. Segment
    splitting is linear; the detector reads at most a bounded number of
    distinct segments."""
    command = _blank_heredoc_bodies(command)
    masked = _unquoted_shell_text(command)
    if masked is None:
        # An apostrophe in a heredoc body leaves an "unterminated" quote to
        # this masker. Missing a label is the unsafe direction (pattern 33),
        # so read the raw text instead and accept a spurious label.
        masked = command
    found: list[str] = []
    seen: set[str] = set()
    examined: set[str] = set()
    detector_budget = _SEGMENT_DETECTION_LIMIT

    def add(label: str) -> bool:
        """Record a label; True once the cap is reached."""
        if label not in seen:
            seen.add(label)
            found.append(label)
        return len(found) >= DATA_COMMAND_NAMED_PATH_LIMIT

    start = 0
    for boundary in _SEGMENT_SPLIT_RX.finditer(masked + "\n"):
        segment = masked[start : boundary.start()]
        raw_segment = command[start : boundary.start()]
        start = boundary.end()
        # The masked text decides where segments end (a quoted `;` is not
        # one); the raw words, quotes removed, decide what runs, so a
        # quoted command word (`"Rscript" fit.R`) is still read.
        decoded = _simple_command_words(raw_segment)
        words = (
            decoded[0]
            if decoded is not None
            else [word.replace('"', "").replace("'", "") for word in raw_segment.split()]
        )
        index, via_xargs = _skip_command_wrappers(words)
        if index >= len(words):
            continue
        run_words = words[index : index + _SEGMENT_WORD_LIMIT]
        key = "\0".join(run_words)
        if key in examined:
            continue
        examined.add(key)
        if detector_budget > 0:
            detector_budget -= 1
            labels = _segment_run_labels(run_words, cwd)
        else:
            labels = []
        if not labels:
            labels = _stdin_fed_run_label(run_words, via_xargs, "<<" in segment)
        prefix = "xargs " if via_xargs else ""
        if any(add(prefix + label) for label in labels):
            break
    return found


def _unproven_analysis_candidates(
    command: str, cwd: Path, analysis_named: bool | None = None
) -> list[str]:
    """Analysis runs the command names when the detector proves none: what
    the detector finds without execution proof, else interpreter commands
    in shell structure it does not model. The command may not run them."""
    # Heredoc bodies are input to a program, not shell text: a README
    # written with `cat <<EOF` that shows `python fit.py` names no run.
    shell_text = _blank_heredoc_bodies(command)
    if len(shell_text) > UNPROVEN_DETECTION_LIMIT_CHARS:
        # The detector's per-runner work is linear but heavy (about 0.2 ms a
        # match), and these are only labels: past the bound, the cheap
        # command-position scan names them instead (pattern 42).
        return _interpreter_runs_named(command, cwd)
    if shell_text != command or analysis_named is None:
        analysis_named = is_analysis(shell_text)
    try:
        detections = (
            _detect_scripts_with_cwd(shell_text, cwd, require_execution_evidence=False)
            if analysis_named
            else []
        )
    except Exception:  # noqa: BLE001 - a label only; never fail the record
        detections = []
    if not detections:
        return _interpreter_runs_named(command, cwd)
    candidates: list[str] = []
    seen: set[str] = set()
    for script_path, inline_source, _ in detections:
        if len(candidates) >= DATA_COMMAND_NAMED_PATH_LIMIT:
            break
        if isinstance(script_path, UnresolvedScript):
            label = script_path.candidate
        elif script_path is not None:
            label = str(script_path)
            if _names_managed_utility(label):
                continue
        elif inline_source is not None:
            label = "(inline)"
        else:
            continue
        if label not in seen:
            seen.add(label)
            candidates.append(label)
    return candidates


def build_data_command_event(
    args: argparse.Namespace, cwd: Path, analysis_named: bool | None = None
) -> dict | None:
    """One minimal `data_command` record for a Bash call that touches data
    and has no analysis event, or None when the call names no data and no
    analysis run."""
    named, truncated = _data_path_mentions(_redact_command_cached(args.bash_cmd),
                                           _project_root(str(cwd)))
    candidates = _unproven_analysis_candidates(args.bash_cmd, cwd, analysis_named)
    io = _certain_shell_io(args.bash_cmd, cwd, getattr(args, "bash_exit", None))
    observation = getattr(args, "observation", None) or {}
    observed_change = bool(observation.get("changed"))
    observation_failed = observation.get("status") == "unavailable"
    if (not named and not candidates and not io.inputs and not io.outputs
            and not observed_change and not observation_failed):
        return None
    signals = []
    if observed_change:
        # The call changed watched files. Without this record an output
        # written by a command nothing else recognises would leave no
        # lineage at all; the changes themselves are in the observation.
        signals.append("observed-change")
    if observation_failed:
        # Whether this call changed files is unknown (no Pre snapshot, a
        # walk over its bound, a lock timeout). Recorded so the gap is
        # visible in the manifest rather than reading as "nothing happened".
        signals.append("observation-unavailable")
    if named:
        signals.append("data-path")
    if io.inputs or io.outputs:
        # A redirection or copy proven to touch a data path, which may only
        # be data-like once resolved (`cd data && wc -l < counts`).
        signals.append("shell-io")
    if candidates:
        signals.append("analysis-unproven")
    return {
        "ts": args.ts,
        "kind": "data_command",
        "event_id": uuid.uuid4().hex,
        "agent_id": args.agent_id or None,
        "agent_type": args.agent_type or None,
        "bash_cmd": getattr(args, "recorded_bash_cmd", args.bash_cmd),
        "bash_exit": getattr(args, "bash_exit", None),
        "cwd": str(cwd),
        "signals": signals,
        "named_paths": named,
        "named_paths_truncated": truncated,
        "analysis_candidates": candidates,
        "inputs": _label_sources(_io_records(io.inputs, cwd), "inferred-shell"),
        "outputs": _label_sources(_io_records(io.outputs, cwd), "inferred-shell"),
        "io_detection": "unresolved",
        **_observation_fields(args),
    }


def build_event_for_detection(
    detection: tuple[Path | UnresolvedScript | None, str | None],
    args: argparse.Namespace,
    cwd: Path | None,
    git_sha: str | None,
) -> dict | None:
    """Build one NDJSON event dict for a single (script_path, inline) detection.

    ``cwd`` is the directory the run's relative I/O resolves against, or
    ``None`` when a runner moved the run somewhere unknowable; its literal
    paths are then left unresolved rather than recorded under a wrong root.
    Returns None if the script is missing or unreadable (main() reports it so
    the tracker records a skip) or the detection is empty.
    """
    script_path, inline_source = detection
    if isinstance(script_path, UnresolvedScript):
        # The run happened, but the interpreter may have read a file other
        # than the one named, so nothing is read, hashed, or scanned.
        if script_path.cwd_unknown:
            warning = (
                "A script ran, but an earlier directory change (pushd, cd -, "
                "a CDPATH search, ...) left the working directory unknown, so "
                f"which file {script_path.candidate} names is unknown."
            )
        else:
            warning = (
                "R ran a script, but shell syntax"
                + (" or R_BATCH_OPTIONS" if script_path.batch else "")
                + " may have changed which one; the command names "
                + f"{script_path.candidate}."
            )
        return _event_dict(
            args,
            None,
            None,
            None,
            git_sha,
            inputs=[],
            outputs=[],
            io_detection="unresolved",
            lineage_warnings=[warning],
            filters=[],
            seeds=[],
            script_candidate=script_path.candidate,
        )
    source = ""
    script_sha: str | None = None
    script_source_embed: str | None = None
    sanitized_digest = False
    args._sanitized_digest = False

    if script_path:
        # A script that does not exist is no evidence of an execution: the
        # existence check is part of the trust gate (`python #a.py` names a
        # comment, not a script). main() reports such a path to the tracker,
        # which records a skip, not an event.
        if not script_path.exists():
            return None
        try:
            source = script_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        script_sha = sha256_file(script_path)
        if len(source.encode("utf-8")) < EMBED_LIMIT_BYTES:
            # Sanitized, with its digest, by _redact_record (a shell body by
            # the command redaction, which `export HF_TOKEN=…` needs).
            script_source_embed = source
        else:
            # Not embedded: the digest alone, under the same rule.
            sanitized = _sanitize_script_text(script_path, source)
            if sanitized != source:
                script_sha = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
                sanitized_digest = True
                args._sanitized_digest = True
    elif inline_source:
        source = inline_source
        script_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        script_source_embed = source
    else:
        return None

    inputs, outputs, filters, seeds = scan_source(
        source,
        source_language(script_path if isinstance(script_path, Path) else None)
        or getattr(inline_source, "language", None),
    )
    # A URL literal (`pd.read_csv("https://...")`) is remote I/O, not a
    # file under the cwd: it is never resolved, hashed, or recorded as a
    # path, and is named only in a warning with its credentials redacted.
    inputs, remote_inputs = _split_urls(inputs)
    outputs, remote_outputs = _split_urls(outputs)
    remote_warnings = []
    if remote_inputs or remote_outputs:
        remote_warnings.append(
            "Remote URLs were not recorded as local files: "
            + ", ".join(remote_inputs + [u for u in remote_outputs if u not in remote_inputs])
            + "."
        )
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
            ] + remote_warnings,
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
    lineage_warnings.extend(remote_warnings)

    recovered = set(added_inputs) | set(added_outputs)

    def labelled(path: str) -> dict:
        # Which inference produced the path: the reader/writer call scan,
        # or the repository recovery of a dynamic path. Never "observed":
        # observations live in the call's observation record.
        record = file_record(path, cwd)
        record["source"] = "inferred-repository" if path in recovered else "inferred-static"
        return record

    return _event_dict(
        args,
        script_path,
        script_sha,
        script_source_embed,
        git_sha,
        inputs=[labelled(p) for p in inputs],
        outputs=[labelled(p) for p in outputs],
        io_detection=io_detection,
        lineage_warnings=lineage_warnings,
        filters=filters,
        seeds=seeds,
    )


# --- Step settings (v2): what the matched stage actually ran with ---
#
# Read from the detected stage's own text: its argv, the assignments and
# wrappers before the interpreter, config files it names, and literal seeds.
# Nothing is expanded or guessed; what the parser cannot model is marked
# `partial`. Every string passes through one redaction policy first.

_CONFIG_SUFFIXES = frozenset({".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"})
_SCRIPT_CONFIG_FLAGS = frozenset({
    "--config", "--config-file", "--configfile", "--params", "--params-file", "--settings",
})
_SNAKEMAKE_CONFIG_FLAGS = frozenset({"--configfile", "--configfiles"})
# Operands Snakemake opens before it changes into `--directory`: profiles
# are resolved while parsing arguments, and `--configfile`s are loaded when
# its ConfigSettings are built (snakemake.settings.types), both relative to
# the invocation directory. Positional targets and the workflow's own
# I/O stay relative to `--directory`.
_SNAKEMAKE_LAUNCH_RELATIVE = frozenset({"--profile", "--configfile", "--configfiles"})
_NEXTFLOW_CONFIG_FLAGS = frozenset({"-params-file", "-c", "-config", "-C"})
_SEED_FLAGS = frozenset({"--seed", "--random-state", "--random_state", "--random-seed"})
CONFIG_MAX_FILES = 8
MAX_EVENTS_PER_CALL = 64
CONFIG_MAX_BYTES = 5 * 1024 * 1024
CONFIG_BUDGET_SECONDS = 0.25
STEP_TOKEN_MAX = 4096
_TRUNCATED_MARK = "…[truncated]"
# A secret-like key followed by a value separator (`"api_key": `, `token=`).
_SECRET_KEY_VALUE_RX = re.compile(
    r"(?i)(?:token|secret|passw(?:or)?d?|pwd|api[_-]?key|private[_-]?key|credential"
    r"|auth(?!or)|cookie|session)[A-Za-z0-9_-]*[\"']?\]?\s*(?:=|:)")


def _clip_redacted_token(original: Any, redacted: Any) -> tuple[Any, bool]:
    """Clip one already-redacted token to STEP_TOKEN_MAX; (token, clipped).

    Redaction ran on the whole token first, so a secret whose closing quote
    lies past the cap was still recognized. When the token looked secret
    at all (a secret-like name anywhere in the unclipped text, or redaction
    changed it), the kept prefix also stops before the first secret-like
    name or redaction marker in it: no partial literal can survive a cut
    that redaction did not foresee (an unterminated quote, say).
    """
    import lineage_redact

    if not isinstance(redacted, str) or len(redacted) <= STEP_TOKEN_MAX:
        return redacted, False
    kept = redacted[:STEP_TOKEN_MAX]
    if isinstance(original, str) and (
        lineage_redact.is_secret_name(original) or redacted != original
    ):
        cuts = [m.start() for m in [lineage_redact.SECRET_NAME_RX.search(kept)] if m]
        marker = kept.find(lineage_redact.REDACTED)
        if marker >= 0:
            cuts.append(marker)
        if cuts:
            kept = kept[: min(cuts)] + lineage_redact.REDACTED
    return kept + _TRUNCATED_MARK, True


def _redacted_clipped_argv(argv: list[Any]) -> tuple[list[Any], bool]:
    """Redact the complete argv, then clip each long token; (argv, any clipped).

    Clipping first would cut a secret's closing quote off before redaction
    could see it, leaving the secret's prefix in the record."""
    import lineage_redact

    redacted = lineage_redact.redact_argv(list(argv))
    out: list[Any] = []
    truncated = False
    for original, token in zip(argv, redacted):
        token, clipped = _clip_redacted_token(original, token)
        truncated = truncated or clipped
        out.append(token)
    return out, truncated


_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NON_RUN_WORDS = frozenset({"cd", "pushd", "popd", "export", "set", "source", ".", "unset"})


_RUNNER_WORD_RX = re.compile(r"(?:^|[\s;&|(])((?:[\w./-]*/)?(?:snakemake|nextflow|sbatch))(?=\s|$)")


def _interpreter_name(token: str) -> str:
    name = os.path.basename(token)
    for prefix in ("python", "Rscript", "R", "jupyter", "snakemake", "nextflow", "sbatch"):
        if name == prefix or (prefix == "python" and re.fullmatch(r"python[0-9.]*", name)):
            return "python" if prefix == "python" else prefix
    return name


def _prefix_settings(prefix: str) -> tuple[dict[str, Any], list[str], bool]:
    """(env, wrappers, partial) from the words before the interpreter."""
    env: dict[str, Any] = {}
    wrappers: list[str] = []
    try:
        words = shlex.split(prefix, comments=False, posix=True)
    except ValueError:
        return env, wrappers, True
    in_env = False
    for word in words:
        name, eq, value = word.partition("=")
        if eq and _NAME_RX.fullmatch(name):
            env[name] = _env_value(name, value)
            continue
        if word == "env":
            in_env = True
            wrappers.append(word)
            continue
        if in_env and word.startswith("-"):
            wrappers.append(word)
            continue
        wrappers.append(word)
    return env, wrappers, False


def _env_value(name: str, value: str) -> Any:
    import lineage_redact

    if lineage_redact.is_secret_name(name):
        return {"redacted": True}
    value = lineage_redact.redact_command(value)
    if "$" in value or "`" in value:
        return {"text": value, "expanded": False}
    return value


def _inline_placeholder(body: str) -> dict[str, Any]:
    import lineage_redact

    sanitized = lineage_redact.redact_source(body)
    placeholder: dict[str, Any] = {
        "inline_sha256": hashlib.sha256(sanitized.encode("utf-8")).hexdigest(),
        "length": len(body),
    }
    if sanitized != body:
        placeholder["sha256_of"] = "sanitized"
    return placeholder


def _stage_raw_words(fragment: str) -> list[str] | None:
    """The words of one simple command as written (quotes kept), with
    redirections dropped: the same words `_simple_command_argv` decodes, so
    a caller can tell `~/c.yaml` from `'~/c.yaml'`. None where the scan
    cannot follow the syntax."""
    command = _join_line_continuations(fragment)
    words: list[str] = []
    index = 0
    try:
        while True:
            while command[index: index + 1] in {" ", "\t"}:
                index += 1
            if index >= len(command) or command[index] in ";\n|)#&":
                return words
            operator = None
            if not command.startswith(("<(", ">("), index):
                operator = _REDIRECTION_OPERATOR_RX.match(command, index)
            if operator is not None:
                index = operator.end()
                while command[index: index + 1] in {" ", "\t"}:
                    index += 1
                if operator.group("op") in {"<<", "<<-"} and command[
                        index: index + 1] in {"'", '"'}:
                    end = _quoted_delimiter_end(command, index)
                else:
                    end = _shell_word_end(command, index)
                if end is None:
                    return None
                index = end
                continue
            end = _shell_word_end(command, index)
            if end is None:
                return None
            words.append(command[index:end])
            index = end
    except RecursionError:
        return None


def _unquoted_chars(raw: str) -> Iterator[tuple[int, str]]:
    """(index, char) for every character of a shell word outside quotes
    and not escaped."""
    quote: str | None = None
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and quote != "'":
            index += 2
            continue
        if char in "'\"" and quote in (None, char):
            quote = None if quote else char
        elif quote is None:
            yield index, char
        index += 1


def _has_unquoted_pattern(raw: str) -> bool:
    """A glob (`*`, `?`, `[`) or brace expansion the shell would apply."""
    chars = [c for _, c in _unquoted_chars(raw)]
    if any(c in "*?[" for c in chars):
        return True
    return "{" in chars and "}" in chars[chars.index("{"):]


# Builtins that can set a variable whose name is computed at run time
# (`declare "$v=..."`, `read "$name"`, `printf -v "$x"`), so a line that
# runs one with any expansion may have set HOME without naming it.
_DYNAMIC_ASSIGN_BUILTINS = frozenset({
    "declare", "typeset", "export", "readonly", "local", "read", "printf",
    "mapfile", "readarray", "unset", "let", "getopts",
})


def _home_may_change(before: str, stage: str = "") -> bool:
    """Whether the command text may have changed HOME before the stage's
    words are expanded, so the hook's own HOME may not be the one bash used
    for a bare `~`.

    ``before`` is the command up to the stage's command word (earlier
    stages and the stage's own prefix assignments); ``stage`` is the stage
    itself. Any mention of the HOME variable other than a plain `$HOME` or
    `${HOME}` read counts (`HOME=`, `export HOME`, `env HOME=`, `unset
    HOME`, `declare/typeset/readonly HOME`, `read HOME`, `${HOME:=x}`), as
    does running code this hook cannot see (`source`, `.`, `eval`) or a
    variable-setting builtin with a computed name. Errs toward True.
    """
    for text in (before, stage):
        for match in re.finditer(r"(?<![A-Za-z0-9_])HOME(?![A-Za-z0-9_])", text):
            start, end = match.start(), match.end()
            if text[max(0, start - 1):start] == "$":
                continue  # `$HOME`: a read
            if text[max(0, start - 2):start] == "${" and text[end:end + 1] == "}":
                continue  # `${HOME}`: a read
            return True
    if re.search(r"(?<![A-Za-z0-9_.-])(source|eval|fc|enable|trap)(?![A-Za-z0-9_.-])", before):
        return True
    if re.search(r"(?:^|[\s;&|(){}!`])\.(?:\s|$)", before):
        return True  # `. file`: the POSIX spelling of source
    for segment in re.split(r"[;&|\n()]+", before):
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", segment))
        if words & _DYNAMIC_ASSIGN_BUILTINS and re.search(r"[$`]", segment):
            return True
    return False


def _resolve(token: str, base: Path | None, raw: str | None = None, *,
             tilde: bool = True, home: bool = True) -> Path | None:
    """The path a shell operand names, by bash's expansion rules, or None
    where it depends on an expansion this hook cannot reproduce.

    ``raw`` is the word as written. An unquoted leading `~` or `~/` is the
    home directory (and `~user` that user's, when known), a quoted one is a
    literal; a parameter, command or arithmetic expansion, a glob or a
    brace expansion is unresolved. Without ``raw`` the quoting is unknown,
    so anything the shell might have expanded is unresolved. ``tilde``
    False (the value after `--flag=`, which bash does not tilde-expand but
    other shells may) leaves a leading `~` unresolved rather than guessed.
    ``home`` False (the command may have changed HOME first, see
    `_home_may_change`) leaves a bare `~` or `~/` unresolved, since bash
    expands it with the command's HOME, not the hook's. A URL
    (`scheme://...`) is never a local path, so it is unresolved too.
    """
    if _is_url(token):
        return None
    if not token or "\0" in token:
        return None
    if raw is None:
        if token.startswith("~") or "$" in token or "`" in token or _has_unquoted_pattern(token):
            return None
    else:
        if _expansion(raw)[0] or _has_unquoted_pattern(raw):
            return None
        if raw.startswith("~"):
            slash = raw.find("/")
            prefix = raw if slash < 0 else raw[:slash]
            if not any(c in prefix for c in "'\"\\"):
                # An unquoted tilde prefix: expanded by the shell.
                if not tilde:
                    return None
                user = prefix[1:]
                if not user and not home:
                    return None  # HOME may differ from the hook's
                if user and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9._-]*", user):
                    return None  # `~+`, `~-`, `~1`: the shell's directory stack
                home = os.path.expanduser(prefix)
                if home.startswith("~") or not os.path.isabs(home):
                    return None
                token = home + token[len(prefix):]
    path = Path(token)
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    return path


class _ConfigOperand(NamedTuple):
    token: str  # decoded value
    via: str  # the flag, "suffix", or "--profile" (a profile directory)
    index: int  # position in the scanned args of the word holding it
    eq: str | None  # the `--flag` before `=` when the value shares its word


_SNAKEMAKE_PARAM_FLAGS = frozenset({"--config", "-C"})


def _config_operands(interpreter: str, args: list[Any]) -> list[_ConfigOperand]:
    """Operands naming config files, by interpreter-specific rules. `.json`
    counts only after a config flag (a JSON operand is as often data);
    python's `-c` is code and snakemake's `-c` is cores, so neither is a
    config flag. Snakemake's `--config`/`-C` values are KEY=VALUE params,
    never files."""
    flags = {
        "snakemake": _SNAKEMAKE_CONFIG_FLAGS,
        "nextflow": _NEXTFLOW_CONFIG_FLAGS,
    }.get(interpreter, _SCRIPT_CONFIG_FLAGS)
    found: list[_ConfigOperand] = []
    expect: str | None = None
    for index, token in enumerate(args):
        if not isinstance(token, str):
            expect = None
            continue
        if expect is not None:
            if token.startswith("-"):
                expect = None
            else:
                if expect != "params":
                    found.append(_ConfigOperand(token, expect, index, None))
                if expect not in ("--configfiles", "params"):
                    expect = None
                continue
        name, eq, value = token.partition("=")
        if interpreter == "snakemake" and (
                name in _SNAKEMAKE_PARAM_FLAGS or token.startswith("-C")):
            expect = "params"
            continue
        if name in flags:
            if eq:
                found.append(_ConfigOperand(value, name, index, name))
            else:
                expect = name
            continue
        if interpreter == "snakemake" and name == "--profile":
            if eq:
                if value:
                    found.append(_ConfigOperand(value, "--profile", index, name))
            else:
                expect = "--profile"
            continue
        if not token.startswith("-") and Path(token).suffix.lower() in _CONFIG_SUFFIXES:
            found.append(_ConfigOperand(token, "suffix", index, None))
    return found


def _config_candidates(interpreter: str, args: list[str]) -> list[tuple[str, str]]:
    """(token, via) pairs naming config files; a profile is shown by its
    `config.yaml` (build_step lists the profile's actual config files)."""
    return [(str(Path(o.token) / "config.yaml"), o.via) if o.via == "--profile"
            else (o.token, o.via) for o in _config_operands(interpreter, args)]


def _snakemake_params(args: list[str]) -> list[str]:
    """Snakemake's `--config`/`-C` KEY=VALUE overrides, verbatim, with a
    secret-named key's value redacted."""
    import lineage_redact

    values: list[str] = []
    collecting = False
    for token in args:
        if not isinstance(token, str):
            collecting = False
            continue
        if token in _SNAKEMAKE_PARAM_FLAGS:
            collecting = True
            continue
        if token.startswith("--config="):
            collecting = True
            token = token[len("--config="):]
        elif token.startswith("-C") and len(token) > 2:
            collecting = True
            token = token[2:]
        elif token.startswith("-"):
            collecting = False
            continue
        elif not collecting:
            continue
        key, eq, _ = token.partition("=")
        if eq and lineage_redact.is_secret_name(key):
            values.append(f"{key}={lineage_redact.REDACTED}")
        else:
            (redacted,) = lineage_redact.redact_argv([token])
            values.append(_clip_redacted_token(token, redacted)[0])
    return values


def _snakemake_profile_configs(directory: Path, token: str) -> list[Path]:
    """The config files of a `--profile` directory: every
    `config.yaml`/`config.vN+.yaml` in it (which one Snakemake reads depends
    on its version), searched like Snakemake does for a bare profile name.
    A profile with none is named by its `config.yaml`, listed as missing."""
    location = directory
    if not location.is_dir() and "/" not in token:
        for root in _snakemake_user_profile_roots():
            if (root / token).is_dir():
                location = root / token
                break
    try:
        files = sorted(
            child for child in location.iterdir()
            if _SNAKEMAKE_PROFILE_FILE_RX.fullmatch(child.name)
            and child.name.startswith("config") and child.is_file())
    except OSError:
        files = []
    return files or [location / "config.yaml"]


class ConfigBudget:
    """One budget for every config file a call names, across all its stages:
    at most CONFIG_MAX_FILES files and CONFIG_BUDGET_SECONDS in total."""

    def __init__(self) -> None:
        self.deadline = time.monotonic() + CONFIG_BUDGET_SECONDS
        self.files_left = CONFIG_MAX_FILES

    def take(self) -> bool:
        if self.files_left <= 0 or time.monotonic() >= self.deadline:
            return False
        self.files_left -= 1
        return True


# Every config digest this process took, by path: (dev, ino, size, mtime_ns,
# ctime_ns) when read, and the digest. The proof pass reuses a digest whose
# file still has that identity instead of reading it again, so the whole
# config budget is the one ConfigBudget spends.
_HASHED_CONFIGS: dict[str, tuple[tuple[int, int, int, int, int], str]] = {}


def _hash_config(path: Path, deadline: float) -> dict[str, Any]:
    """Hash one config file: regular file only, opened without blocking or
    following a final symlink, within the size cap and the call's budget,
    and only if it provably did not change while it was read."""
    record: dict[str, Any] = {"path": str(path)}
    if time.monotonic() >= deadline:
        record.update(sha256=None, hash_status="skipped-budget")
        return record
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        record.update(sha256=None, hash_status="missing")
        return record
    except OSError:
        record.update(sha256=None, hash_status="unavailable")
        return record
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            record.update(sha256=None, hash_status="not-a-regular-file")
            return record
        record["size_bytes"] = st.st_size
        if st.st_size > CONFIG_MAX_BYTES:
            record.update(sha256=None, hash_status="too-large")
            return record
        digest = hashlib.sha256()
        read = 0
        while True:
            if time.monotonic() >= deadline:
                record.update(sha256=None, hash_status="skipped-budget")
                return record
            chunk = os.read(fd, min(1 << 20, CONFIG_MAX_BYTES + 1 - read))
            if not chunk:
                break
            read += len(chunk)
            if read > CONFIG_MAX_BYTES:
                record.update(sha256=None, hash_status="too-large")
                return record
            digest.update(chunk)
        ident = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        after = os.fstat(fd)
        try:
            on_path = os.stat(path, follow_symlinks=False)
        except OSError:
            on_path = None
        if on_path is None or any(
                (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns) != ident
                for x in (after, on_path)):
            record.update(sha256=None, hash_status="unstable")
            return record
        record.update(sha256=digest.hexdigest(), hash_status="hashed-at-post",
                      hash_origin="hook-post")
        _HASHED_CONFIGS[str(path)] = (ident, record["sha256"])
        return record
    finally:
        os.close(fd)


def _matched_operand_index(bash_cmd: str, offset: int, operand_offset: Any,
                           argv_length: int) -> int | None:
    """The argv index of the word starting at ``operand_offset`` in the
    simple command starting at ``offset``: the number of words before it.
    None when unknown or out of range (callers then search by spelling)."""
    if not isinstance(operand_offset, int) or operand_offset <= offset:
        return None
    words = _stage_raw_words(bash_cmd[offset:operand_offset])
    if words is None or not 0 < len(words) < argv_length:
        return None
    return len(words)


def build_step(
    bash_cmd: str,
    invocation: dict[str, Any] | None,
    inline_source: str | None,
    io_cwd: Path | None,
    budget: "ConfigBudget | None" = None,
) -> dict[str, Any]:
    """The `step` object for one detection: argv, interpreter, script
    arguments, environment assignments, config files, and literal seeds of
    the stage that ran it. Partial, with a reason, where it cannot tell."""
    import lineage_redact

    if invocation and invocation.get("kind") == "runner" and invocation.get("offset") is None:
        # Only an unambiguous runner stage is used: with several runner
        # words, which stage this detection ran is not known here.
        found = list(_RUNNER_WORD_RX.finditer(bash_cmd))
        if len(found) == 1:
            invocation = {**invocation, "offset": found[0].start(1)}
        elif len(found) > 1:
            return {"partial": True, "reason": "runner-stage-ambiguous"}
    if not invocation or invocation.get("offset") is None:
        kind = (invocation or {}).get("kind") or "unknown"
        return {"partial": True, "reason": f"{kind}-argv-not-captured"}
    offset = invocation["offset"]
    start = _simple_command_start(bash_cmd, offset)
    suffix = _simple_command_suffix(bash_cmd, offset)
    argv = _simple_command_argv(suffix) if suffix is not None else None
    step: dict[str, Any] = {}
    if not argv:
        return {"partial": True, "reason": "unmodeled-shell-structure"}
    env, wrappers, prefix_partial = _prefix_settings(bash_cmd[start:offset])
    interpreter = _interpreter_name(argv[0])
    kind = invocation.get("kind")
    # Where the step's own operands begin.
    operand_index: int | None = None
    shown: list[Any] = list(argv)
    # The word the detector matched, by position: a startup option may take
    # a value spelled like the operand (`python -X -c -c 'print(1)'`,
    # `python -X fit.py fit.py`), so a search by spelling finds the wrong one.
    matched = _matched_operand_index(bash_cmd, offset, invocation.get("operand_offset"),
                                     len(argv))
    if matched is not None and kind == "inline" and interpreter == "python" \
            and argv[matched - 1] == "-c":
        shown[matched] = _inline_placeholder(argv[matched])
        operand_index = matched + 1
    elif matched is not None and kind == "inline" and interpreter != "python" \
            and argv[matched - 1] in ("-e", "--eval"):
        # R evaluates every `-e` from the matched one up to `--args`.
        shown[matched] = _inline_placeholder(argv[matched])
        operand_index = matched + 1
        for index in range(matched + 1, len(argv) - 1):
            token = argv[index]
            if token == "--args":
                operand_index = index + 1
                break
            if token in ("-e", "--eval"):
                shown[index + 1] = _inline_placeholder(argv[index + 1])
                operand_index = index + 2
    elif matched is not None and kind == "module" and argv[matched - 1] == "-m" \
            and argv[matched] == invocation.get("script_word"):
        operand_index = matched + 1
    elif matched is not None and kind in ("script", "unresolved") \
            and invocation.get("form") != "r-file" \
            and argv[matched] == invocation.get("script_word"):
        operand_index = matched + 1
    elif kind == "inline":
        if interpreter == "python":
            # Python takes one `-c` body; everything after it is sys.argv.
            for index, token in enumerate(argv[:-1]):
                if token == "-c":
                    shown[index + 1] = _inline_placeholder(argv[index + 1])
                    operand_index = index + 2
                    break
        else:
            # R evaluates every `-e` up to `--args`; after it, arguments.
            for index, token in enumerate(argv[:-1]):
                if token == "--args":
                    operand_index = index + 1
                    break
                if token in ("-e", "--eval"):
                    shown[index + 1] = _inline_placeholder(argv[index + 1])
                    operand_index = index + 2
    elif kind == "module":
        for index, token in enumerate(argv[:-1]):
            if token == "-m" and argv[index + 1] == invocation.get("script_word"):
                operand_index = index + 2
                break
    elif kind in ("script", "unresolved") and invocation.get("form") == "r-file":
        # `R -f X` / `R --file=X`: R reads its own options anywhere before
        # `--args`; the script's arguments are only the words after it.
        operand_index = len(argv)
        for index, token in enumerate(argv[1:], start=1):
            if token == "--args":
                operand_index = index + 1
                break
    elif kind in ("script", "unresolved"):
        word = invocation.get("script_word")
        for index, token in enumerate(argv[1:], start=1):
            if token == word:
                operand_index = index + 1
                break
    script_args = argv[operand_index:] if operand_index is not None else []
    # Every field read from the arguments reads the redacted list, the one
    # stored: a word a secret flag consumed (`--password --seed=482731`,
    # where the option parser takes `--seed=482731` as the password) is
    # masked there, so it never surfaces as a seed or a parameter.
    redacted_script_args = lineage_redact.redact_argv(list(script_args))
    step["argv"], truncated = _redacted_clipped_argv(shown)
    step["executable"] = argv[0]
    step["interpreter"] = interpreter
    step["script_args"], _ = _redacted_clipped_argv(script_args)
    if env:
        step["env"] = env
    if wrappers:
        step["wrappers"] = lineage_redact.redact_argv(wrappers)
    if interpreter == "snakemake":
        params = _snakemake_params(lineage_redact.redact_argv(argv[1:]))
        if params:
            step["params"] = params
    # Config files: the operands after the script for script runners, the
    # whole argv for runners driven by flags. Each is resolved by the shell's
    # expansion of the word as written, so the raw words ride along.
    scan_from = operand_index if operand_index else 1
    raws = _stage_raw_words(suffix)
    if raws is not None and len(raws) != len(argv):
        raws = None  # the two scans disagree: treat the quoting as unknown
    resolved: list[tuple[str, str, Path | None]] = []
    remote: list[dict[str, Any]] = []
    # bash expands a bare `~` with the command's HOME: when anything before
    # this stage may have set it, the hook's own HOME proves nothing.
    home_stable = not _home_may_change(bash_cmd[:offset], suffix or "")
    for operand in _config_operands(
            interpreter, lineage_redact.redact_argv(argv[scan_from:])):
        if _is_url(operand.token):
            # A remote config is not a local file: never resolved against
            # the cwd or hashed. One a flag names is kept as a reference with
            # its userinfo redacted; a bare `.yaml` URL may be anything.
            if operand.via != "suffix":
                remote.append({"path": _redact_url(operand.token), "via": operand.via,
                               "sha256": None, "hash_status": "remote"})
            continue
        if lineage_redact.REDACTED in operand.token:
            continue  # a redacted local value is never a config path
        if _SECRET_KEY_VALUE_RX.search(operand.token):
            # `--params '{"api_key": "…'` with no closing quote: redaction
            # cannot delimit the literal, and no config path looks like
            # this, so it is never recorded as one.
            continue
        raw = raws[scan_from + operand.index] if raws is not None else None
        if raw is not None and operand.eq is not None:
            prefix = operand.eq + "="
            raw = raw[len(prefix):] if raw.startswith(prefix) else None
        base = io_cwd
        if interpreter == "snakemake" and operand.via in _SNAKEMAKE_LAUNCH_RELATIVE \
                and "launch_cwd" in invocation:
            # Snakemake reads its profile (argument parsing) and its
            # `--configfile`s (ConfigSettings, built before WorkflowApi
            # changes into `--directory`) from the invocation directory.
            base = invocation["launch_cwd"]
        path = _resolve(operand.token, base, raw, tilde=operand.eq is None,
                        home=home_stable)
        if operand.via == "--profile":
            if path is None:
                resolved.append((str(Path(operand.token) / "config.yaml"), operand.via, None))
            else:
                resolved.extend((str(p), operand.via, p)
                                for p in _snakemake_profile_configs(path, operand.token))
            continue
        resolved.append((operand.token, operand.via, path))
    budget = budget or ConfigBudget()
    configs: list[dict[str, Any]] = list(remote)
    skipped = 0
    for token, via, path in resolved:
        if path is None:
            configs.append({"path": token, "via": via, "sha256": None,
                            "hash_status": "unresolved"})
            continue
        if via == "suffix" and not path.exists():
            continue  # a config-like operand that is not a file may be an output
        if not budget.take():
            skipped += 1
            configs.append({"path": str(path), "via": via, "sha256": None,
                            "hash_status": "skipped-budget"})
            continue
        record = _hash_config(path, budget.deadline)
        record["via"] = via
        if record.get("sha256") is not None:
            # Unproven until the Pre snapshot says otherwise: with no
            # snapshot (no observer, a failed Pre) the digest describes the
            # file after the call, so the settings the step read are unknown.
            record["pre_run_proven"] = False
            record["proof_reason"] = "no-pre-snapshot"
        configs.append(record)
    if skipped:
        step["config_files_over_budget"] = skipped
    if configs:
        step["config_files"] = configs
    seeds: list[dict[str, Any]] = []
    expect_seed = False
    for token in redacted_script_args:
        if not isinstance(token, str):
            expect_seed = False
            continue
        if token == "--":
            # An operand boundary: after it, an option parser (argparse,
            # getopt, optparse) reads every word as a positional value, so
            # `-- --seed 4839…` names no seed.
            break
        if expect_seed:
            expect_seed = False
            if re.fullmatch(r"-?\d{1,19}", token):
                seeds.append({"value": int(token), "origin": "argv"})
            continue
        name, eq, value = token.partition("=")
        if name in _SEED_FLAGS:
            if eq and re.fullmatch(r"-?\d{1,19}", value):
                seeds.append({"value": int(value), "origin": "argv"})
            elif not eq:
                expect_seed = True
    if seeds:
        step["seeds"] = seeds
    if truncated:
        step["truncated"] = True
    if prefix_partial:
        step["partial"] = True
        step["reason"] = "unparsed-prefix"
    return step


def data_command_step(bash_cmd: str, named_paths: list[str] | None = None) -> dict[str, Any] | None:
    """The argv of the data command's working stage: the first stage that
    names one of the data paths the record lists, else the first stage that
    does something other than change directory or set variables."""
    named =[p for p in named_paths or [] if isinstance(p, str) and p]
    fallback: dict[str, Any] | None = None
    offset = 0
    stages = 0
    while offset < len(bash_cmd) and stages < 256:
        stages += 1
        suffix = _simple_command_suffix(bash_cmd, offset)
        if suffix is None:
            return fallback or {"partial": True, "reason": "unmodeled-shell-structure"}
        argv = _simple_command_argv(suffix)
        if argv:
            words = [w for w in argv if not (_NAME_RX.fullmatch(w.partition("=")[0])
                                              and "=" in w)]
            if words and words[0] not in _NON_RUN_WORDS:
                record = {"argv": _redacted_clipped_argv(argv)[0],
                          "executable": words[0]}
                if named and any(any(p in w or w in p for p in named) for w in argv[1:]
                                 if len(w) > 1):
                    return {**record, "stage": "names-data"}
                if fallback is None:
                    fallback = {**record, "stage": "first-run-stage"}
                if not named:
                    return fallback
        offset += len(suffix)
        if offset < len(bash_cmd) and bash_cmd[offset] == "#":
            newline = bash_cmd.find("\n", offset)
            offset = len(bash_cmd) if newline < 0 else newline
        offset += 1
        while offset < len(bash_cmd) and bash_cmd[offset] in " \t;&|\n":
            offset += 1
    return fallback


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
    script_candidate: str | None = None,
) -> dict:
    return {
        "ts": args.ts,
        "agent_id": args.agent_id or None,
        "agent_type": args.agent_type or None,
        "bash_cmd": getattr(args, "recorded_bash_cmd", args.bash_cmd),
        "bash_exit": getattr(args, "bash_exit", None),
        "bash_wall_s": None,
        "script": str(script_path) if script_path else None,
        "script_sha256": script_sha,
        "script_source": script_source_embed,
        "script_candidate": script_candidate,
        "git_sha": git_sha,
        "inputs": inputs,
        "outputs": outputs,
        "io_detection": io_detection,
        "lineage_warnings": lineage_warnings,
        # `filters_detected` is no longer written (v2): it held unevaluated
        # regex text. Evaluated row/feature access is a separate, in-process
        # record (docs/plans/row-feature-access.md).
        "seeds_detected": seeds,
        **_observation_fields(args),
        # A large script whose source held a secret literal: its digest is
        # of the sanitized text, never of the secret-bearing body.
        **({"script_sha256_of": "sanitized"} if getattr(args, "_sanitized_digest", False)
           else {}),
    }


def _record_root(args: argparse.Namespace, cwd: Path) -> str | None:
    """The physical repository root this record's paths are relative to.

    The tracker passes the root it already resolved; without it (a direct
    call), git is asked once and the answer is cached on ``args``.
    """
    cached = getattr(args, "_record_root", False)
    if cached is not False:
        return cached
    raw = getattr(args, "repo_root", None)
    root = Path(raw) if raw else _repository_root(cwd)
    resolved = os.path.realpath(root) if root is not None else None
    args._record_root = resolved
    return resolved


def _stamp_file_frame(record: dict, root: str | None) -> None:
    """Add `rel_path`, the record's path relative to its own root, with the
    observer's rules: no `..`, no symlinked component below the root. A
    path that cannot be framed keeps `rel_path: null` and says why, so the
    index never joins it to a repository path by guesswork."""
    raw = record.get("path")
    if not isinstance(raw, str):
        return
    if _is_url(raw):
        # A remote reference (a config URL): nothing under any root. The
        # schema's reason for a path outside the repository fits it.
        record["rel_path"] = None
        record["rel_path_reason"] = "outside-scope"
        return
    if root is None:
        record["rel_path"] = None
        record["rel_path_reason"] = "no-repository-root"
        return
    import io_observer

    rel, reason = io_observer.repo_relative_unfollowed(raw, root)
    record["rel_path"] = rel
    if reason is not None:
        record["rel_path_reason"] = reason


def _stamp_v2(record: dict, args: argparse.Namespace, cwd: Path) -> dict:
    """Identity and frame every v2 lineage record carries.

    `event_id` is minted here unless the builder already set one (data
    commands do). `host_session_id` falls back to the observation's, which
    the observer took from the same payload. `ts_ns` orders records within
    a second; the observation's journal sequence orders writes without any
    clock.
    """
    record.setdefault("event_id", uuid.uuid4().hex)
    observation = getattr(args, "observation", None) or {}
    record["host"] = getattr(args, "host", None) or record.get("host")
    record["host_session_id"] = (
        getattr(args, "host_session_id", None)
        or observation.get("host_session_id")
        or record.get("host_session_id")
    )
    record["ts_ns"] = time.time_ns()
    root = _record_root(args, cwd)
    record["repo_root"] = root
    for key in ("inputs", "outputs"):
        for file_rec in record.get(key) or []:
            if isinstance(file_rec, dict):
                _stamp_file_frame(file_rec, root)
                _stamp_hash_origin(file_rec, is_input=key == "inputs")
    step = record.get("step") if isinstance(record.get("step"), dict) else None
    for config in (step or {}).get("config_files") or []:
        if isinstance(config, dict) and isinstance(config.get("path"), str):
            _stamp_file_frame(config, root)
            if config.get("sha256") is not None:
                _stamp_hash_origin(config, is_input=True)
                if config.get("pre_run_proven") is False:
                    # Changed (or unprovable) across the call: the digest is
                    # of the file afterwards, not what the step read.
                    config["effective"] = "unknown"
    _redact_record(record)
    return record


@functools.lru_cache(maxsize=4)
def _redact_command_cached(text: str) -> str:
    """One redaction per command text, however many records repeat it."""
    import lineage_redact

    return lineage_redact.redact_command(text)


_SHELL_SOURCE_SUFFIXES = (".sh", ".bash", ".zsh", ".ksh")
_SHELL_SHEBANG_RX = re.compile(r"#![ \t]*\S*/(?:env[ \t]+(?:-\S+[ \t]+)*)?(?:ba|z|da|k)?sh\b")


def _is_shell_source(script: Any, source: str | None) -> bool:
    """A script body the shell runs: named `.sh`/`.bash`/`.zsh`/`.ksh`, or
    opening with a shell shebang (an sbatch job file). Its secrets are shell
    words (`export HF_TOKEN=…`), not only quoted literals, so it takes the
    command redaction rather than the source-literal one."""
    if isinstance(script, (str, Path)) and str(script).endswith(_SHELL_SOURCE_SUFFIXES):
        return True
    return isinstance(source, str) and bool(_SHELL_SHEBANG_RX.match(source))


def _sanitize_script_text(script: Any, source: str) -> str:
    """The one redaction a script body gets before it is embedded or hashed."""
    import lineage_redact

    if _is_shell_source(script, source):
        return lineage_redact.redact_command(source)
    return lineage_redact.redact_source(source)


def _redact_record(record: dict) -> None:
    """Apply the one redaction policy to every command text the record
    keeps. A changed embedded source gets the hash of the sanitized text,
    labelled, so no digest of a secret-bearing body is stored."""
    if isinstance(record.get("bash_cmd"), str):
        record["bash_cmd"] = _redact_command_cached(record["bash_cmd"])
    source = record.get("script_source")
    if isinstance(source, str):
        sanitized = _sanitize_script_text(record.get("script"), source)
        if sanitized != source:
            record["script_source"] = sanitized
            record["script_sha256"] = hashlib.sha256(sanitized.encode("utf-8")).hexdigest()
            record["script_sha256_of"] = "sanitized"


def _stamp_hash_origin(record: dict, *, is_input: bool) -> None:
    """Say how a recorded hash was taken, so no reader overstates it.

    - `pre-run-proven`: the bytes at the path matched the Pre snapshot (an
      input the observer proved unchanged across the call).
    - `hashed-at-post`: the observer's stable protocol hash, taken at Post,
      without a pre-run proof.
    - `hook-post`: the hook's plain digest at Post, with no stability or
      timing guarantee (outputs, and inputs no proof was attempted for,
      which 0.9.7 labelled `hash_status: hashed-at-post`).
    """
    if record.get("sha256") is None:
        return
    if is_input and record.get("pre_run_proven") is True:
        record["hash_origin"] = "pre-run-proven"
    elif is_input and record.get("hash_status") not in (None, "hashed-at-post"):
        record["hash_origin"] = "hashed-at-post"
    else:
        record["hash_origin"] = "hook-post"


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


def _append_with_stable_lock(path: Path, line: str) -> None:
    """Append under `<path>.lock`, the lock Stop takes before it rewrites
    the shared file by atomic replacement; never through a symlink."""
    # Non-blocking opens and a regular-file check on each descriptor: a FIFO
    # in place of either file must fail (exit 70, a recorded gap), not hang.
    flags = os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CREAT
    lock_fd = os.open(f"{path}.lock", os.O_RDWR | flags, 0o644)
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise OSError("shared lock is not a regular file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | flags, 0o644)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError("shared file is not a regular file")
        with os.fdopen(fd, "ab") as handle:
            handle.write(line.encode("ascii"))
            handle.flush()
    finally:
        os.close(lock_fd)


def _record_unowned_data_command(
    args: argparse.Namespace, cwd: Path, analysis_named: bool | None = None
) -> None:
    """Keep a data_command for a host session that has no transaction.

    It waits in the shared file until that session's next Stop. A record
    that cannot be built becomes a skip there instead, so the gap is still
    reported; a record that cannot be written at all exits 70, a gap the
    tracker records.
    """
    keys = {"host": args.host, "host_session_id": args.host_session_id}
    try:
        event = build_data_command_event(args, cwd, analysis_named)
    except Exception as exc:  # noqa: BLE001 - recorded as a gap below
        event = {
            "ts": args.ts,
            "kind": "skip",
            "skip_id": uuid.uuid4().hex,
            "skip_reason": "data-command-failed",
            "detail": type(exc).__name__,
            "bash_cmd": getattr(args, "recorded_bash_cmd", args.bash_cmd),
            "agent_id": args.agent_id or None,
            "agent_type": args.agent_type or None,
            "bash_exit": args.bash_exit,
        }
    if event is None:
        return
    event.update(keys)
    if event.get("kind") == "data_command":
        step = data_command_step(args.bash_cmd, event.get("named_paths"))
        if step is not None:
            event["step"] = step
        _stamp_v2(event, args, cwd)
    else:
        event["ts_ns"] = time.time_ns()
        _redact_record(event)
    # A failed append propagates: the guarded main exits 70, which the
    # tracker records as a `detector-failed` gap rather than silence.
    _append_with_stable_lock(args.data_command_to, json.dumps(event) + "\n")


_OBSERVATION_STATUSES = frozenset({"no-known-overlap", "overlap", "background", "unavailable"})


def _parse_observation(raw: str | None) -> dict | None:
    """The observer's one-line summary, reduced to typed known fields.

    A summary that does not parse still says an observation was attempted:
    it becomes `unavailable` with a reason, never silently nothing.
    """
    if raw is None:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        value = None
    if not isinstance(value, dict):
        return {"status": "unavailable", "reason": "observation-summary-unreadable"}
    status = value.get("status")
    observation = {
        "observation_id": value.get("observation_id")
        if isinstance(value.get("observation_id"), str) else None,
        "host_session_id": value.get("host_session_id")
        if isinstance(value.get("host_session_id"), str) else None,
        "status": status if status in _OBSERVATION_STATUSES else "unavailable",
        "reason": value.get("reason") if isinstance(value.get("reason"), str) else None,
        "changed": value.get("changed") is True,
    }
    for key in ("pre_snapshot", "state_dir"):
        if isinstance(value.get(key), str) and "\0" not in value[key]:
            observation[key] = value[key]
    return observation


def _observation_fields(args: argparse.Namespace) -> dict:
    """The fields every lineage record carries about its call's observation.

    Absent (no PreToolUse support, an older install) is null, which readers
    must treat as "not observed", never as "nothing changed".
    """
    observation = getattr(args, "observation", None)
    outcome = getattr(args, "outcome", None)
    if not observation:
        fields = {"observation_id": None, "observation_status": None,
                  "observation_session": None}
        if outcome:
            fields["tool_outcome"] = outcome
        return fields
    fields = {
        "observation_id": observation.get("observation_id"),
        "observation_status": observation.get("status"),
        "observation_session": observation.get("host_session_id"),
    }
    if observation.get("reason"):
        fields["observation_reason"] = observation["reason"]
    if outcome:
        # "failure" with a null bash_exit: the host reported a failed call
        # (interrupt, timeout) without an exit code.
        fields["tool_outcome"] = outcome
    return fields


def _discard_retired_snapshot(args: argparse.Namespace) -> None:
    """Delete the call's retired Pre snapshot when nothing will prove
    inputs against it (its observation is already published)."""
    snapshot = (getattr(args, "observation", None) or {}).get("pre_snapshot")
    if snapshot and snapshot.endswith(".done"):
        try:
            os.unlink(snapshot)
        except OSError:
            pass


def _label_sources(records: list[dict], source: str) -> list[dict]:
    for record in records:
        record.setdefault("source", source)
    return records


def _apply_input_proofs(events: list[dict], args: argparse.Namespace, cwd: Path) -> None:
    """Prove each inferred input's pre-run content against the call's
    retired Pre snapshot, where the observer left one."""
    observation = getattr(args, "observation", None) or {}
    snapshot = observation.get("pre_snapshot")
    state_dir = observation.get("state_dir")
    if not snapshot or not state_dir:
        return
    root_text = _record_root(args, cwd)
    if root_text is None:
        return
    root = Path(root_text)

    def proved_records(event: dict) -> list[dict]:
        # Inputs, and the config files a step named: a config replaced
        # during the call must not pass as the settings the step read.
        step = event.get("step") if isinstance(event.get("step"), dict) else {}
        configs = [c for c in step.get("config_files") or []
                   if isinstance(c, dict) and c.get("sha256") is not None]
        return [*event.get("inputs", []), *configs]

    # The config budget was spent in build_step: reuse each config digest
    # whose file still has the identity it was read with, so the proof pass
    # reads no config bytes again. A config that changed since is unstable
    # and is not proved (proving it would mean reading it a second time).
    reused: dict[tuple[int, int, int, int, int], str] = {}
    for event in events:
        step = event.get("step") if isinstance(event.get("step"), dict) else {}
        for config in step.get("config_files") or []:
            if not isinstance(config, dict) or config.get("sha256") is None:
                continue
            known = _HASHED_CONFIGS.get(config.get("path"))
            if known is None or known[1] != config["sha256"]:
                continue  # not a digest this process took: proved as before
            try:
                st = os.stat(config["path"], follow_symlinks=False)
                ident = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
            except (OSError, ValueError):
                ident = None
            if ident != known[0]:
                config.update(sha256=None, hash_status="unstable", pre_run_proven=False,
                              proof_reason="changed-after-hash")
                config.pop("hash_origin", None)
                continue
            reused[ident] = known[1]

    paths = [r["path"] for e in events for r in proved_records(e) if r.get("path")]
    if not paths:
        try:
            os.unlink(snapshot)
        except OSError:
            pass
        return
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import io_observer

    # Configs hashed this call are offered by identity, so the proof pass
    # reads none of them again (one config budget, not two).
    seeded = {":".join(str(part) for part in ident): digest
              for ident, digest in (reused or {}).items()}
    proofs = io_observer.prove_inputs(Path(state_dir), Path(snapshot), root, paths,
                                      known_digests=seeded)
    for event in events:
        for record in proved_records(event):
            proof = proofs.get(record.get("path"))
            if not proof:
                continue
            record["pre_run_proven"] = proof["pre_run_proven"]
            if proof.get("reason"):
                record["proof_reason"] = proof["reason"]
            if proof.get("hash_status"):
                # The protocol's result replaces the unchecked digest,
                # including when it is null (unstable, pending): a digest
                # the protocol could not stand behind is not kept.
                record["hash_status"] = proof["hash_status"]
                record["sha256"] = proof.get("sha256")
                if "size_bytes" in proof:
                    record["size_bytes"] = proof["size_bytes"]
            elif record.get("sha256"):
                # No protocol hash was attempted (outside the watched scope,
                # a symlinked path): the digest is of the content at Post.
                record["hash_status"] = "hashed-at-post"


def _in_command_order(
    detections: list[Any], invocations: list[dict[str, Any]]
) -> tuple[list[Any], list[dict[str, Any] | None]]:
    """Detections, each with its invocation, sorted by shell offset. A
    detection without a known offset keeps its place relative to the others
    without one, after every positioned stage."""
    paired = [(d, invocations[i] if i < len(invocations) else None)
              for i, d in enumerate(detections)]
    order = sorted(range(len(paired)), key=lambda i: (
        (paired[i][1] or {}).get("offset") is None,
        (paired[i][1] or {}).get("offset") or 0,
        i))
    return [paired[i][0] for i in order], [paired[i][1] for i in order]


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
            "bookkeeping cycle; excludes inline one-liners (python -c, R/Rscript "
            "-e), tooling modules, and "
            "managed utility scripts."
        ),
    )
    ap.add_argument(
        "--append-to",
        type=Path,
        default=None,
        help="Append NDJSON to this file under fcntl.flock. Default: stdout.",
    )
    ap.add_argument(
        "--data-command-to",
        type=Path,
        default=None,
        help=(
            "With --check-execution: when no analysis run is proven, append "
            "this call's data_command record (if it touches data) to this "
            "shared file under its stable `.lock` sibling, keyed by --host "
            "and --host-session-id. The exit status is unchanged."
        ),
    )
    ap.add_argument("--host", default=None)
    ap.add_argument("--host-session-id", default=None)
    ap.add_argument(
        "--repo-root",
        default=None,
        help=(
            "The repository root the hook resolved. Every record stores it "
            "and frames its file paths relative to it (`rel_path`); without "
            "it, git is asked."
        ),
    )
    ap.add_argument(
        "--observation-json",
        default=None,
        help=(
            "The io_observer summary for this call: every record names its "
            "observation, and a call that changed files without any other "
            "record gets an observed-change data_command."
        ),
    )
    ap.add_argument("--outcome", choices=("success", "failure", "unknown"), default=None)
    args = ap.parse_args()
    args.observation = _parse_observation(args.observation_json)
    # Detection reads `$PY run.py` as the interpreter it was assigned; the
    # record keeps the command as written.
    args.recorded_bash_cmd = args.bash_cmd
    args.bash_cmd = _expand_interpreter_variables(args.bash_cmd)
    if args.data_command_to is not None and not (
        args.check_execution and args.host and args.host_session_id
    ):
        ap.error("--data-command-to needs --check-execution, --host, and --host-session-id")

    cwd = Path(args.cwd)
    detections = []
    invocations: list[dict[str, Any]] = []
    analysis_named = is_analysis(args.bash_cmd)
    if analysis_named:
        detections = _detect_scripts_with_cwd(
            args.bash_cmd,
            cwd,
            bash_exit=args.bash_exit,
            require_execution_evidence=True,
            include_inline=not args.check_post_action,
            invocations=invocations,
        )
    if args.check_execution or args.check_post_action:
        if detections:
            # The tracker reopens and runs the owned extraction next; it
            # proves inputs against the retired snapshot and deletes it.
            return 0
        if args.data_command_to is not None:
            _record_unowned_data_command(args, cwd, analysis_named)
        _discard_retired_snapshot(args)
        return 1
    if not detections:
        # No proven analysis: a call that still touches data leaves one
        # minimal record of its own kind, never an analysis event.
        event = build_data_command_event(args, cwd, analysis_named)
        if event is not None:
            step = data_command_step(args.bash_cmd, event.get("named_paths"))
            if step is not None:
                event["step"] = step
            _apply_input_proofs([event], args, cwd)
            _stamp_v2(event, args, cwd)
            write_events([json.dumps(event) + "\n"], args.append_to)
        else:
            _apply_input_proofs([], args, cwd)
        return 0

    # git_sha is the same for every detection in this command — compute once.
    git_sha = get_git_sha(cwd)

    events: list[dict] = []
    unreadable: list[str] = []
    budget = ConfigBudget()
    # Several runner stages in one call: the n-th runner detection is the
    # n-th runner word, but only when the counts agree (a runner name in an
    # echo would otherwise shift every stage).
    runner_matches = list(_RUNNER_WORD_RX.finditer(args.bash_cmd))
    runner_words = [m.start(1) for m in runner_matches]
    runner_names = {os.path.basename(m.group(1)) for m in runner_matches}
    runner_slots = [i for i, inv in enumerate(invocations) if inv.get("kind") == "runner"]
    # Detection lists one runner type's stages in command order, but not
    # different types relative to each other: assign only for one type.
    if (len(runner_slots) > 1 and len(runner_words) == len(runner_slots)
            and len(runner_names) == 1):
        for slot, offset in zip(runner_slots, runner_words):
            invocations[slot] = {**invocations[slot], "offset": offset}
    # Detection groups runs by interpreter and form; the cap and the shared
    # config budget go to the stages that ran first, so order by position.
    detections, invocations = _in_command_order(detections, invocations)
    dropped = max(0, len(detections) - MAX_EVENTS_PER_CALL)
    detections = detections[:MAX_EVENTS_PER_CALL]
    for index, (script_path, inline_source, effective_cwd) in enumerate(detections):
        event = build_event_for_detection(
            (script_path, inline_source), args, effective_cwd, git_sha
        )
        if event is not None:
            invocation = invocations[index] if index < len(invocations) else None
            step = build_step(args.bash_cmd, invocation, inline_source, effective_cwd, budget)
            source_seeds = [{"value": v, "origin": "source"}
                            for v in event.get("seeds_detected") or []]
            if source_seeds:
                step["seeds"] = [*step.get("seeds", []), *source_seeds]
            event["step"] = step
            events.append(event)
        elif isinstance(script_path, Path):
            unreadable.append(str(script_path))

    if dropped and events:
        # A loop or generated chain of distinct runs: recording each with the
        # whole command text would grow quadratically, so the rest is named.
        events[-1]["lineage_warnings"].append(
            f"This call ran {dropped} more analysis stage(s) than the "
            f"{MAX_EVENTS_PER_CALL} recorded; their runs are not separately listed.")
        events[-1]["stages_not_recorded"] = dropped
    _apply_input_proofs(events, args, cwd)
    for event in events:
        _stamp_v2(event, args, cwd)
    write_events([json.dumps(event) + "\n" for event in events], args.append_to)
    if args.append_to is not None:
        # A detected script that is gone or unreadable at hook time (often a
        # temporary script the command itself removed) is neither an event
        # nor nothing: name it on stdout so the tracker records a skip.
        for path in unreadable:
            sys.stdout.write(json.dumps({"unreadable_script": path}) + "\n")
    return 0


# Exit status for a crash. `--check-*` modes use 1 for "not analysis", and
# Python's own uncaught-exception status is also 1, so a detector failure was
# indistinguishable from a command that needs no lineage. The tracker records
# this status as a skip instead of reading it as "nothing to record".
EXIT_INTERNAL_ERROR = 70


def _main_guarded() -> int:
    try:
        return main()
    except Exception:  # noqa: BLE001 - any crash must be reported, not read as "no"
        import traceback

        traceback.print_exc()
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(_main_guarded())
