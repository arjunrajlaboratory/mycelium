#!/usr/bin/env python3
"""Replay recorded Bash commands through the data-lineage detector.

Measures item 1 of the provenance work: of the commands in past host
transcripts that touch data, how many would the PostToolUse recorder now
leave an event for, how many a logged skip, and how many nothing at all.

Every command is fed to ``skills/core/scripts/extract_data_lineage_event.py``
as ``mycelium-data-tracker.sh`` calls it on an owned transaction
(``--cwd --ts --bash-cmd --append-to --bash-exit 0``), in-process, and the
outcome is read the way the tracker reads it. The live hook also passes the
call's observation summary (``--observation-json``); transcripts do not
record it, so the replay omits it, and coverage is measured without it (an
`unavailable` observation alone would make the live hook write a record):

* ``event``: the detector appended at least one record (an analysis event or
  a ``data_command``);
* ``skip``: it exited nonzero (``extractor-failed``) or named a detected
  script it could not read (``script-unreadable``) and appended nothing;
* ``none``: it appended nothing and asked for no skip.

Only calls with positive evidence that they ran and succeeded are replayed:
a Claude call with a non-error result, and a Codex direct call whose status
header says `Process exited with code 0`. Codex code-mode calls, read from
completed snippets' JavaScript, are reported apart as unverified.

Whether a command touches data is decided by :func:`classify`, an
independent rule written against the command text (see its docstring and
``docs/plans/provenance-index.md``), never by the detector itself. A
data-touching command whose outcome is ``none`` is a silent miss.

Read-only on the replayed projects: the detector only reads files and runs
``git rev-parse``; its appends go to a scratch file. Transcripts may hold
private data, so the report holds counts and allowlisted shape labels only.
``--show-misses`` prints raw command text to stderr for local debugging; never
commit that output.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

REPO = Path(__file__).resolve().parent.parent
DETECTOR_DIR = REPO / "skills" / "core" / "scripts"


# ---------------------------------------------------------------- extraction


@dataclass
class Command:
    project: str
    host: str  # "claude" | "codex"
    command: str
    cwd: str
    bash_exit: int | None
    call_id: str
    subagent: bool = False
    # How we know the call ran: "hook-result" (a Claude tool result that is
    # not a refusal), "exit-status" (a Codex direct call whose output reports
    # an exit or a running process), or "code-mode-source" (a Codex code-mode
    # call read from the JavaScript source: the snippet ran, but whether this
    # call inside it did is not recorded per call).
    evidence: str = "hook-result"
    workdir: str = "literal"  # Codex: "literal", "omitted", or "computed"


def _claude_files(projects_dir: Path, root: Path) -> list[Path]:
    """Claude Code transcripts for ``root`` and its worktrees, subagents
    included. Claude encodes a cwd by replacing every ``/`` and ``.``
    with ``-``."""
    encoded = re.sub(r"[/.]", "-", str(root))
    files: list[Path] = []
    if not projects_dir.is_dir():
        return files
    for entry in sorted(projects_dir.iterdir()):
        name = entry.name
        if entry.is_dir() and (name == encoded or name.startswith(encoded + "--")):
            files.extend(sorted(entry.rglob("*.jsonl")))
    return files


def extract_claude(path: Path, project: str,
                   unproven: Counter | None = None) -> Iterator[Command]:
    """Bash calls with a non-error result. Each call left out (a failure, a
    refusal, a hook block, or no result) is counted in ``unproven``."""
    uses: dict[str, tuple[str, str]] = {}
    results: dict[str, dict] = {}
    order: list[str] = []
    subagent = "subagents" in path.parts
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                continue
            for block in msg["content"]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Bash":
                    cmd = (block.get("input") or {}).get("command")
                    if isinstance(cmd, str) and block.get("id") not in uses:
                        uses[block["id"]] = (cmd, rec.get("cwd") or "")
                        order.append(block["id"])
                elif block.get("type") == "tool_result":
                    results[block.get("tool_use_id")] = block
    for call_id in order:
        result = results.get(call_id)
        if result is None:
            if unproven is not None:
                unproven["claude"] += 1
            continue  # interrupted before a result: no PostToolUse
        cmd, cwd = uses[call_id]
        if result.get("is_error"):
            # A failure, a permission refusal, or a hook block (`Blocked:`):
            # without positive evidence that the call ran and succeeded it
            # stays out of the replay.
            if unproven is not None:
                unproven["claude"] += 1
            continue
        # PostToolUse fires only for successful Bash calls.
        yield Command(project, "claude", cmd, cwd, 0, call_id, subagent)


def _read_rollout(path: Path) -> list[bytes]:
    if path.suffix == ".zst":
        out = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True)
        return out.stdout.splitlines()
    return path.read_bytes().splitlines()


def _js_string(text: str, i: int) -> tuple[str | None, int]:
    """Decode the JavaScript string literal starting at ``text[i]``.

    Returns ``(None, end)`` for a template literal with ``${...}``
    interpolation: its value is computed, not written in the transcript.
    """
    quote = text[i]
    out: list[str] = []
    j = i + 1
    computed = False
    escapes = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
    while j < len(text):
        ch = text[j]
        if ch == "\\" and j + 1 < len(text):
            nxt = text[j + 1]
            if nxt == "u" and text[j + 2:j + 3] == "{":
                end = text.index("}", j)
                out.append(chr(int(text[j + 3:end], 16)))
                j = end + 1
                continue
            if nxt == "u":
                out.append(chr(int(text[j + 2:j + 6], 16)))
                j += 6
                continue
            if nxt == "x":
                out.append(chr(int(text[j + 2:j + 4], 16)))
                j += 4
                continue
            if nxt == "\n":
                j += 2
                continue
            out.append(escapes.get(nxt, nxt))
            j += 2
            continue
        if quote == "`" and ch == "$" and text[j + 1:j + 2] == "{":
            computed = True
        if ch == quote:
            return (None if computed else "".join(out)), j + 1
        out.append(ch)
        j += 1
    return None, len(text)


_IDENT_RX = re.compile(r"[A-Za-z_$][\w$]*")


def _skip_value(js: str, pos: int) -> int:
    """Index of the ``,`` or ``}`` that ends the object value at ``pos``."""
    depth = 0
    while pos < len(js):
        ch = js[pos]
        if ch in "\"'`":
            _, pos = _js_string(js, pos)
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                return pos
            depth -= 1
        elif ch == "," and depth == 0:
            return pos
        pos += 1
    return pos


def strip_js_comments(js: str) -> str:
    """Blank `//` and `/* */` comments outside string literals, so a call
    shown in a comment is not read as one. Regular-expression literals are
    not modelled (a `/'/` could hide the rest of a line)."""
    out: list[str] = []
    i = 0
    while i < len(js):
        ch = js[i]
        if ch in "\"'`":
            _, end = _js_string(js, i)
            out.append(js[i:end])
            i = end
        elif js.startswith("//", i):
            end = js.find("\n", i)
            end = len(js) if end < 0 else end
            out.append(" " * (end - i))
            i = end
        elif js.startswith("/*", i):
            end = js.find("*/", i + 2)
            end = len(js) if end < 0 else end + 2
            out.append(re.sub(r"[^\n]", " ", js[i:end]))
            i = end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def mask_js(js: str) -> str:
    """``js`` with string-literal contents and comments blanked, same
    length, so a call written inside a string or comment is not a call
    site. A template literal is blanked whole, interpolations included."""
    out = list(js)
    i = 0
    while i < len(js):
        ch = js[i]
        if ch in "\"'`":
            _, end = _js_string(js, i)
            for k in range(i + 1, max(i + 1, end - 1)):
                if out[k] != "\n":
                    out[k] = " "
            i = end
        elif js.startswith("//", i):
            end = js.find("\n", i)
            end = len(js) if end < 0 else end
            for k in range(i, end):
                out[k] = " "
            i = end
        elif js.startswith("/*", i):
            end = js.find("*/", i + 2)
            end = len(js) if end < 0 else end + 2
            for k in range(i, end):
                if out[k] != "\n":
                    out[k] = " "
            i = end
        else:
            i += 1
    return "".join(out)


_CALL_SITE_RX = re.compile(r"\btools\s*\.\s*exec_command\s*\(\s*\{")


def parse_code_mode(js: str) -> tuple[list[tuple[str, str | None, str]], int]:
    """``tools.exec_command({cmd: ..., workdir: ...})`` calls in a Codex
    code-mode ``exec`` snippet, comments excluded. Returns (cmd, workdir,
    workdir state) for each call with a literal command, and how many calls
    had a computed (non-literal) command. The state is "literal",
    "omitted", or "computed". A call in a branch that never ran is still
    read: the source does not say which branches ran."""
    js = strip_js_comments(js)
    calls: list[tuple[str, str | None, str]] = []
    unparsed = 0
    # Call sites come from the masked text (outside strings and comments);
    # their arguments are read from the source at the same offsets.
    for m in _CALL_SITE_RX.finditer(mask_js(js)):
        pos = m.end()
        values: dict[str, str | None] = {}
        while pos < len(js):
            while pos < len(js) and js[pos] in " \t\r\n,":
                pos += 1
            if pos >= len(js) or js[pos] == "}":
                break
            if js[pos] in "\"'":
                key, pos = _js_string(js, pos)
            else:
                km = _IDENT_RX.match(js, pos)
                if not km:
                    break
                key, pos = km.group(0), km.end()
            while pos < len(js) and js[pos] in " \t\r\n":
                pos += 1
            if pos >= len(js) or js[pos] != ":":
                if key in ("cmd", "workdir"):
                    values.setdefault(key, None)  # shorthand: computed
                break
            pos += 1
            while pos < len(js) and js[pos] in " \t\r\n":
                pos += 1
            value = None
            if pos < len(js) and js[pos] in "\"'`":
                value, end = _js_string(js, pos)
                if js[end:_skip_value(js, end)].strip():
                    # `"a" + b`: a concatenation is computed.
                    value = None
                pos = _skip_value(js, end)
            else:
                pos = _skip_value(js, pos)
            if key in ("cmd", "workdir") and key not in values:
                values[key] = value
        if values.get("cmd"):
            if "workdir" not in values:
                state = "omitted"
            else:
                state = "literal" if values["workdir"] else "computed"
            calls.append((values["cmd"], values.get("workdir"), state))
        else:
            unparsed += 1
    return calls, unparsed


_CODEX_EXIT_LINE_RX = re.compile(r"Process exited with code (-?\d+)")


def codex_exit_status(output: str | None) -> int | None:
    """The exit status in a direct Codex call's own status header (the
    lines before `Output:`), or None. Text in the command's output or an
    echoed command never counts."""
    if not output:
        return None
    header, sep, _ = output.partition("\nOutput:")
    lines = header.splitlines()
    # The tool's header opens with its chunk id; anything else is not one.
    if not sep or not lines or not re.fullmatch(r"Chunk ID: \w+", lines[0].strip()):
        return None
    for line in lines[1:]:
        m = _CODEX_EXIT_LINE_RX.fullmatch(line.strip())
        if m:
            return int(m.group(1))
    return None


def code_mode_completed(output: str | None) -> bool:
    """Whether a code-mode snippet's result says the script completed (not
    `Script failed`, a compile error, or a script still running)."""
    if not output:
        return False
    text = output
    try:
        value = json.loads(output)
    except ValueError:
        value = None
    if isinstance(value, list):
        text = "".join(v.get("text", "") for v in value if isinstance(v, dict))
    elif isinstance(value, dict) and isinstance(value.get("output"), str):
        text = value["output"]
    return text.startswith("Script completed")


def _join_workdir(workdir: str | None, session_cwd: str) -> str:
    """A Codex workdir as the tool used it: relative to the session cwd."""
    if not workdir:
        return session_cwd
    return os.path.normpath(os.path.join(session_cwd, workdir))


@dataclass
class CodexStats:
    computed_cmd: int = 0
    not_run: int = 0
    computed_workdir: int = 0


def extract_codex(path: Path, project: str, root: Path) -> tuple[list[Command], CodexStats]:
    lines = _read_rollout(path)
    commands: list[Command] = []
    stats = CodexStats()
    try:
        meta = json.loads(lines[0]).get("payload", {})
    except (ValueError, IndexError):
        return commands, stats
    session_cwd = meta.get("cwd") or str(root)
    outputs: dict[str, str] = {}
    # (call_id, cmd, workdir, workdir state, code mode)
    calls: list[tuple[str, str, str | None, str, bool]] = []
    for raw in lines:
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        pl = rec.get("payload") or {}
        kind = pl.get("type")
        if kind == "function_call" and pl.get("name") in ("exec_command", "shell", "local_shell"):
            try:
                args = json.loads(pl.get("arguments") or "{}")
            except ValueError:
                stats.computed_cmd += 1
                continue
            cmd = args.get("cmd") or args.get("command")
            if isinstance(cmd, list):
                cmd = shlex.join(cmd) if cmd[:2] not in (["bash", "-lc"], ["bash", "-c"]) else cmd[2]
            if not isinstance(cmd, str):
                stats.computed_cmd += 1
                continue
            workdir = args.get("workdir")
            state = "omitted" if workdir is None else "literal" if isinstance(workdir, str) else "computed"
            calls.append((pl.get("call_id", ""), cmd, workdir if isinstance(workdir, str) else None,
                          state, False))
        elif kind == "custom_tool_call" and pl.get("name") == "exec":
            found, bad = parse_code_mode(pl.get("input") or "")
            stats.computed_cmd += bad
            for n, (cmd, workdir, state) in enumerate(found):
                calls.append((f"{pl.get('call_id', '')}#{n}", cmd, workdir, state, True))
        elif kind in ("function_call_output", "custom_tool_call_output"):
            out = pl.get("output")
            outputs[pl.get("call_id", "")] = out if isinstance(out, str) else json.dumps(out)
    for call_id, cmd, workdir, state, code_mode in calls:
        bash_exit = None
        if code_mode:
            # Only a snippet that completed; a call inside it still has no
            # result of its own, so this population is unverified.
            if not code_mode_completed(outputs.get(call_id.split("#", 1)[0])):
                stats.not_run += 1
                continue
            evidence = "code-mode-source"
        else:
            # Positive evidence of success only: exit 0 in the status
            # header. A rejection, a failure, or a call still running when
            # its result was returned is unproven and left out.
            if codex_exit_status(outputs.get(call_id)) != 0:
                stats.not_run += 1
                continue
            bash_exit = 0
            evidence = "exit-status"
        stats.computed_workdir += state == "computed"
        commands.append(Command(project, "codex", cmd, _join_workdir(workdir, session_cwd),
                                bash_exit, call_id, evidence=evidence, workdir=state))
    return commands, stats


def codex_rollouts(sessions_dirs: Iterable[Path], root: Path,
                   unreadable: list[Path] | None = None) -> list[Path]:
    """Rollouts whose session cwd is inside ``root``. One that cannot be
    read is appended to ``unreadable`` (when given), never skipped silently."""
    found = []
    for base in sessions_dirs:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("rollout-*")):
            if not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.zst")):
                continue
            try:
                first = _read_rollout(path)[0]
                cwd = json.loads(first).get("payload", {}).get("cwd") or ""
            except (subprocess.CalledProcessError, ValueError, IndexError, OSError):
                if unreadable is not None:
                    unreadable.append(path)
                continue
            if cwd == str(root) or cwd.startswith(str(root) + "/"):
                found.append(path)
    return found


# ------------------------------------------------------------ classification

# The rule, in one place. A command touches data when, outside heredoc bodies
# and outside quoted text:
#   (script)     an interpreter runs a program file: python/python3[.N],
#                Rscript, R -f/--file/CMD BATCH/< file, jupyter nbconvert
#                --execute or jupyter execute, snakemake, nextflow run;
#   (inline)     an interpreter runs code from the command: python -c,
#                Rscript -e, R -e, or an interpreter fed a heredoc or pipe;
#   (shell-data) any word names a data-like path: a data file extension
#                (DATA_EXTENSIONS, optionally compressed) or a path under a
#                `data`, `outputs`, `output`, or `results` directory (or that
#                directory itself, written with a trailing slash). A search
#                tool's pattern operand (grep, rg, git grep) is not a path.
# A word that is a search pattern rather than a path (see is_pattern) names
# no data. Not data-touching: terminal-only modes (--version, --help, -V, -h),
# tooling modules (TOOLING_MODULES), Mycelium's own helper scripts, and paths
# under tooling or Mycelium state (EXCLUDED_COMPONENTS) or URLs.

DATA_EXTENSIONS = frozenset("""
csv tsv tab psv txt dat parquet feather arrow h5 hdf5 hdf h5ad h5mu loom zarr
npy npz mat pkl pickle rds rda rdata qs fst xlsx xls ods sav dta sas7bdat
vcf bcf bam bai sam cram fastq fq fasta fa fna faa bed bedgraph bigwig bw
gtf gff gff3 mtx nwk newick tre tree treefile nex nexus phy aln maf
mzml fcs tif tiff czi nd2
""".split())
COMPRESSION = ("gz", "bz2", "xz", "zst", "zip")
TOOLING_BASENAMES = re.compile(
    r"^(requirements[\w.-]*\.txt|constraints\.txt|cmakelists\.txt|license\.txt"
    r"|readme\.txt|robots\.txt|notice\.txt|authors\.txt)$", re.I)
EXCLUDED_COMPONENTS = frozenset({
    ".living", ".mycelium", ".git", ".claude", ".codex", ".github",
    "node_modules", "__pycache__", ".venv", "venv", "site-packages",
    ".pytest_cache", ".tox",
})
DATA_DIRS = frozenset({"data", "outputs", "output", "results"})
TOOLING_MODULES = frozenset({
    "pip", "pytest", "venv", "ensurepip", "py_compile", "compileall",
    "json.tool", "http.server", "pydoc", "unittest", "mypy", "ruff", "black",
    "pyflakes", "flake8", "build", "twine", "site", "platform", "sysconfig",
    "tokenize", "ast", "pip_audit", "isort", "pylint", "doctest", "timeit",
    "webbrowser", "zipfile", "tarfile", "base64",
})
TERMINAL_FLAGS = frozenset({"--version", "-V", "-VV", "--help", "-h"})
WRAPPERS = frozenset({"time", "nohup", "exec", "sudo", "command", "builtin",
                      "caffeinate", "stdbuf", "!", "do", "then", "else",
                      "{", "(", "if", "while", "until"})


def _mycelium_helpers() -> frozenset[str]:
    names = set()
    for scripts in (REPO / "skills").glob("*/scripts"):
        names.update(p.name for p in scripts.glob("*.py"))
        names.update(p.name for p in scripts.glob("*.R"))
    names.update(p.name for p in (REPO / "skills").glob("*/hooks/*.sh"))
    return frozenset(names)


MYCELIUM_HELPERS = _mycelium_helpers()


def mask_quotes(text: str, quote: str | None = None) -> tuple[str, str | None]:
    """``text`` with quoted characters (and the quotes) blanked, so shell
    syntax inside a string (`echo "a <<EOF | b"`) is not read as syntax.
    Takes and returns the open quote, for text that spans lines."""
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\" and quote == '"' and i + 1 < len(text):
                out.append("  ")
                i += 2
                continue
            if ch == quote:
                quote = None
            out.append(" " if ch != "\n" else ch)
        elif ch == "\\" and i + 1 < len(text):
            out.append("  ")
            i += 2
            continue
        elif ch in "'\"":
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    return "".join(out), quote


def strip_heredoc_bodies(command: str) -> tuple[str, bool]:
    """Remove heredoc bodies; report whether any heredoc was present. A
    `<<` inside quotes is text, and so is everything in a body (its quotes
    may not balance)."""
    lines = command.split("\n")
    out: list[str] = []
    pending: list[tuple[str, bool]] = []
    had = False
    quote: str | None = None
    for line in lines:
        if pending:
            delim, strip_tabs = pending[0]
            if (line.lstrip("\t") if strip_tabs else line) == delim:
                pending.pop(0)
            continue
        out.append(line)
        masked, quote = mask_quotes(line, quote)
        # `<<<` is a here-string, not a heredoc. The delimiter may itself be
        # quoted, so it is read from the original line at the same offset.
        for m in re.finditer(r"(?<!<)<<(?!<)-?", masked):
            dm = re.match(r"(-?)\s*(['\"]?)([A-Za-z_][\w.-]*)\2", line[m.start() + 2:])
            if dm:
                pending.append((dm.group(3), dm.group(1) == "-"))
                had = True
    return "\n".join(out), had


def split_segments(text: str) -> list[str]:
    """Split shell text into simple-command segments at unquoted
    operators (``; & | && || ( ) { }`` newlines, ``$(`` and backticks).
    Quoted text stays inside its segment."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < len(text):
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "\\" and i + 1 < len(text):
            buf.append(ch + text[i + 1])
            i += 2
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch == "#" and (not buf or buf[-1][-1:] in " \t\n"):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif ch in "{}" and not ((i == 0 or text[i - 1] in " \t\n;&|(")
                                 and (i + 1 == len(text) or text[i + 1] in " \t\n;")):
            buf.append(ch)  # a brace inside a word (`a.{csv,tsv}`, `${x}`)
        elif ch in ";&|()\n`{}" or (ch == "$" and text[i + 1:i + 2] == "("):
            segments.append("".join(buf))
            buf = []
            if ch == "$":
                i += 1
        else:
            buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def _words(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _unwrap(words: list[str]) -> list[str]:
    """Drop assignments and execution wrappers before the command word."""
    i = 0
    while i < len(words):
        w = words[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w) or w in WRAPPERS:
            i += 1
        elif w == "env":
            i += 1
            while i < len(words) and (words[i].startswith("-") or "=" in words[i]):
                i += 1
        elif w in ("nice", "timeout", "gtimeout"):
            i += 1
            while i < len(words) and (words[i].startswith("-") or re.match(r"^\d", words[i])):
                i += 1
        elif w == "xargs":
            i += 1
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-I", "-n", "-P", "-L", "-d", "-E", "-s") else 1
        elif w in ("uv", "poetry", "pixi", "pipenv") and words[i + 1:i + 2] == ["run"]:
            i += 2
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("--with", "--python", "-p", "--project",
                                       "--directory", "-e", "--env-file") else 1
        elif w in ("conda", "mamba", "micromamba") and words[i + 1:i + 2] == ["run"]:
            i += 2
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in ("-n", "--name", "-p", "--prefix", "--cwd") else 1
        else:
            break
    return words[i:]


def _is_helper(path: str) -> bool:
    """A Mycelium helper: one of the plugin's own script names, reached
    through a plugin layout (`skills/<skill>/scripts/`, `skills/<skill>/
    hooks/`, or `.mycelium/plugin-root`). A project's own
    `scripts/register_value.py` is not one, nor is anything else under a
    plugin cache."""
    if os.path.basename(path) not in MYCELIUM_HELPERS:
        return False
    return (".mycelium/plugin-root" in path
            or re.search(r"(^|/)skills/[\w-]+/(scripts|hooks)/[^/]+$", path) is not None)


def _python_kind(args: list[str], fed: bool) -> str | None:
    i = 0
    while i < len(args):
        a = args[i]
        if a in TERMINAL_FLAGS:
            return None
        if a == "-c":
            return "inline"
        if a == "-m":
            mod = args[i + 1] if i + 1 < len(args) else ""
            return None if mod.split(".")[0] in TOOLING_MODULES or mod in TOOLING_MODULES else "script"
        if a in ("-W", "-X"):
            i += 2
            continue
        if a == "-":
            return "inline"
        if a.startswith("-") and len(a) > 1:
            i += 1
            continue
        if a.startswith("<"):
            return "script"
        return None if _is_helper(a) else "script"
    return "inline" if fed else None


def _r_kind(name: str, args: list[str], fed: bool) -> str | None:
    if any(a in TERMINAL_FLAGS or a in ("RHOME", "--print-home") for a in args):
        return None
    if name == "R" and "CMD" in args:
        k = args.index("CMD")
        return "script" if args[k + 1:k + 2] == ["BATCH"] else None
    for k, a in enumerate(args):
        if a in ("-e", "--expr") or a.startswith("--expr="):
            return "inline"
        if a in ("-f",) or a.startswith("--file="):
            target = a.split("=", 1)[1] if "=" in a else (args[k + 1] if k + 1 < len(args) else "")
            return None if _is_helper(target) else "script"
        if a == "-":
            return "inline"
        if not a.startswith("-"):
            if name == "Rscript":
                return None if _is_helper(a) else "script"
            break
    return "inline" if fed else None


def interpreter_kind(segment: str, fed: bool) -> str | None:
    words = _unwrap(_words(segment))
    if not words:
        return None
    name = os.path.basename(words[0])
    args = words[1:]
    if re.fullmatch(r"python(\d+(\.\d+)?)?", name):
        return _python_kind(args, fed or any(a.startswith("<") for a in args))
    if name in ("Rscript", "R"):
        return _r_kind(name, args, fed or any(a.startswith("<") for a in args))
    if name == "jupyter" and args:
        if args[0] == "execute" or (args[0] == "nbconvert" and "--execute" in args):
            return "script"
    if name == "snakemake" and not any(a in ("-n", "--dry-run", "--dryrun", "--version", "-h", "--help", "--lint") for a in args):
        return "script"
    if name == "nextflow" and args[:1] == ["run"]:
        return "script"
    return None


# Top-level directories an absolute path on a workstation or cluster starts
# with. Fixed, so classification never depends on this machine's mounts.
ROOT_DIRS = frozenset({
    "Users", "home", "private", "tmp", "var", "opt", "usr", "etc", "Volumes",
    "mnt", "media", "srv", "root", "data", "scratch", "work", "project",
    "projects", "groups", "group", "nfs", "lustre", "gpfs", "fs", "storage",
    "shared", "lab", "Library", "Applications", "System", "workspace",
})


def is_pattern(word: str) -> bool:
    """A search pattern or exclusion, not a path: a negated glob
    (`'!**/output/**'`), git pathspec magic (`':!x'`, `':(exclude)x'`),
    regex alternation, anchors or escapes (`'a|b'`, `'^x'`, `x$`, `\\.`), or
    an absolute path whose top-level directory is not one paths start
    with (`grep -v '/output/runs/'`; see ROOT_DIRS)."""
    if word.startswith(("!", ":!", ":(", ":^")):
        return True
    if any(c in word for c in "|^\\") or word.endswith("$"):
        return True
    if word.startswith("/"):
        top = word.split("/", 2)[1]
        return bool(top) and top not in ROOT_DIRS
    return False


_BRACE_RX = re.compile(r"\{([^{}\s]*,[^{}\s]*)\}")


def brace_alternatives(word: str) -> list[str]:
    """The words a single comma brace group expands to (`a.{csv,tsv}`), or
    the word itself."""
    m = _BRACE_RX.search(word)
    if not m:
        return [word]
    return [word[:m.start()] + alt + word[m.end():] for alt in m.group(1).split(",")]


def _path_text(word: str) -> str:
    """A word as the path it may name: quotes, an `--opt=` or `VAR=`
    prefix, and a redirection prefix (`2>/dev/null`) removed."""
    word = word.strip("'\"")
    if "=" in word and not word.startswith(("/", ".")):
        word = word.split("=", 1)[1]
    return word.lstrip("<>&|0123456789")


def is_data_path(word: str) -> bool:
    if is_pattern(word.strip("'\"")):
        return False
    word = _path_text(word)
    if "{" in word and _BRACE_RX.search(word):
        return any(is_data_path(alt) for alt in brace_alternatives(word))
    # A quoted file name may hold spaces (`'data/my file.tsv'`); a space
    # before the last `/` is prose (`git commit -m "Add data/x.csv"`).
    if not word or "://" in word:
        return False
    head = word.rsplit("/", 1)[0] if "/" in word else word
    if any(c.isspace() for c in head):
        return False
    parts = [p for p in word.split("/") if p]
    if not parts or EXCLUDED_COMPONENTS.intersection(parts):
        return False
    base = parts[-1].lower()
    if TOOLING_BASENAMES.match(base):
        return False
    stem = base
    for comp in COMPRESSION:
        if stem.endswith("." + comp):
            stem = stem[: -len(comp) - 1]
            break
    if "." in stem and stem.rsplit(".", 1)[1] in DATA_EXTENSIONS and not stem.startswith("*"):
        return True
    # A path under a data or analysis-output directory, or that directory
    # itself written with a trailing slash (`ls data/`); a bare `data` is a
    # common word, not a path.
    under = DATA_DIRS.intersection(parts[:-1])
    return bool(under or (word.endswith("/") and parts[-1] in DATA_DIRS))


_SEARCH_TOOLS = frozenset({"grep", "egrep", "fgrep", "rg", "ag"})
# Search options whose value is the next word (not a pattern, not a path
# that matters here); `-e`/`--regexp` values are patterns.
_SEARCH_VALUE_OPTIONS = frozenset({
    "-m", "--max-count", "-A", "-B", "-C", "--context", "--after-context",
    "--before-context", "-g", "--glob", "--iglob", "-t", "--type", "-T",
    "--type-not", "-M", "--max-columns", "-j", "--threads", "--color",
    "--colors", "-d", "--directories", "-D", "--devices", "--label",
    "--include", "--exclude", "--exclude-dir", "-f", "--file",
})


def path_words(segment: str) -> list[str]:
    """The words of a simple command that may name paths. A search tool's
    pattern operand (`grep -v 'outputs/x'`, `rg -e PAT`) is not one."""
    words = _words(segment)
    run = _unwrap(words)
    if not run:
        return words
    name = os.path.basename(run[0])
    args = run[1:]
    if name == "git" and args[:1] == ["grep"]:
        name, args = "grep", args[1:]
    if name not in _SEARCH_TOOLS:
        return words
    kept: list[str] = []
    pattern_given = False
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("-e", "--regexp"):
            pattern_given = True
            i += 2
            continue
        if a in ("-f", "--file"):
            pattern_given = True  # patterns come from a file: all operands are paths
            kept.extend(args[i:i + 2])
            i += 2
            continue
        if a.startswith("--regexp="):
            pattern_given = True
        elif a in _SEARCH_VALUE_OPTIONS:
            kept.extend(args[i:i + 2])
            i += 2
            continue
        elif a == "--":
            kept.extend(args[i + 1 + (0 if pattern_given else 1):])
            break
        elif not a.startswith("-") and not pattern_given:
            pattern_given = True  # the first operand is the pattern
        else:
            kept.append(a)
        i += 1
    return kept


def _relative_data(word: str, base: str | None, root: str | None) -> bool:
    """A relative path word read from ``base`` (`head raw/counts` run in
    `data/`), judged by its path inside the project. Lexical only."""
    word = _path_text(word)
    if (not word or not base or not root or "/" not in word or "://" in word
            or is_pattern(word) or word.startswith(("/", "~", "$", "-"))):
        return False
    joined = os.path.normpath(os.path.join(base, word))
    if not joined.startswith(root.rstrip("/") + "/"):
        return False
    rel = os.path.relpath(joined, root)
    return is_data_path(rel + ("/" if word.endswith("/") else ""))


def classify(command: str, cwd: str | None = None, root: str | None = None) -> str | None:
    """``"script"``, ``"inline"``, ``"shell-data"``, or None (not data).

    With ``cwd`` and ``root``, a relative path word is also judged as read
    from the call's directory, moved by each literal `cd` in command
    position."""
    stripped, heredoc = strip_heredoc_bodies(command)
    segments = split_segments(stripped)
    kinds = set()
    piped = "|" in mask_quotes(stripped)[0]
    for seg in segments:
        kind = interpreter_kind(seg, fed=heredoc or piped)
        if kind:
            kinds.add(kind)
    if "script" in kinds:
        return "script"
    if "inline" in kinds:
        return "inline"
    base = cwd
    for seg in segments:
        words = path_words(seg)
        run = _unwrap(_words(seg))
        if run[:1] == ["cd"]:
            target = run[1] if len(run) > 1 else None
            if target is None or target.startswith(("~", "$", "-")):
                base = None
            elif target.startswith("/"):
                base = os.path.normpath(target)
            elif base:
                base = os.path.normpath(os.path.join(base, target))
        for w in words:
            if is_data_path(w) or _relative_data(w, base, root):
                return "shell-data"
    return None


def shape(command: str) -> str:
    """An allowlisted, content-free description of a command's structure."""
    stripped, heredoc = strip_heredoc_bodies(command)
    feats = []
    if heredoc:
        feats.append("heredoc")
    if re.search(r"(^|[^|])\|([^|]|$)", stripped):
        feats.append("pipe")
    if re.search(r"\b(for|while)\b.*\bdo\b", stripped, re.S):
        feats.append("loop")
    if re.search(r"(^|[;&|\s])cd\s", stripped):
        feats.append("cd")
    if "$(" in stripped or "`" in stripped:
        feats.append("substitution")
    if re.search(r"(^|[^&])&([^&>]|$)", stripped):
        feats.append("background")
    if ";" in stripped or "\n" in stripped.strip():
        feats.append("sequence")
    return "+".join(feats) or "simple"


# ------------------------------------------------------------------- replay


def _load_detector():
    sys.path.insert(0, os.environ.get("LINEAGE_REPLAY_DETECTOR_DIR") or str(DETECTOR_DIR))
    import extract_data_lineage_event as detector  # noqa: E402
    return detector


def resolve_cwd(cwd: str, root: Path) -> tuple[str, bool]:
    """The command's cwd, or for a removed worktree the matching directory
    in the main checkout (then the project root). Returns (cwd, remapped)."""
    if cwd and os.path.isdir(cwd):
        return cwd, False
    m = re.match(r"^(.*?)/\.(?:claude|codex)/worktrees/[^/]+(/.*)?$", cwd or "")
    if m:
        candidate = m.group(1) + (m.group(2) or "")
        while candidate and not os.path.isdir(candidate):
            candidate = os.path.dirname(candidate)
        if candidate and candidate.startswith(str(root)):
            return candidate, True
    return str(root), True


@dataclass
class Outcome:
    result: str  # "event" | "skip" | "none"
    kinds: list[str] = field(default_factory=list)
    skip_reasons: list[str] = field(default_factory=list)
    # A data_command whose shell syntax proved an input or output direction.
    directed: bool = False


def replay_one(detector, command: str, cwd: str, bash_exit: int | None,
               scratch: Path) -> Outcome:
    events_file = scratch / "events.ndjson"
    events_file.write_text("")
    argv = ["extract_data_lineage_event.py", "--cwd", cwd, "--ts", "1970-01-01T00:00:00Z",
            "--bash-cmd", command, "--append-to", str(events_file)]
    if bash_exit is not None:
        argv += ["--bash-exit", str(bash_exit)]
    out, err = io.StringIO(), io.StringIO()
    old_argv = sys.argv
    sys.argv = argv
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                status = detector._main_guarded()
            except SystemExit as exc:  # argparse error
                status = exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.argv = old_argv
    kinds = []
    directed = False
    for line in events_file.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        kinds.append(record.get("kind") or "analysis")
        if record.get("kind") == "data_command" and (record.get("inputs") or record.get("outputs")):
            directed = True
    events_file.write_text("")  # the scratch file never keeps command text
    if kinds:
        return Outcome("event", kinds, directed=directed)
    if status != 0:
        return Outcome("skip", skip_reasons=["extractor-failed"])
    unreadable = [l for l in out.getvalue().splitlines() if "unreadable_script" in l]
    if unreadable:
        return Outcome("skip", skip_reasons=["script-unreadable"] * len(unreadable))
    return Outcome("none")


# ------------------------------------------------------------------- report


_WORKER: dict = {}


def _worker_init(scratch_root: str) -> None:
    _WORKER["detector"] = _load_detector()
    scratch = Path(scratch_root) / f"worker-{os.getpid()}"
    scratch.mkdir(exist_ok=True)
    _WORKER["scratch"] = scratch


def _worker_replay(job: tuple[str, str, int | None]) -> Outcome:
    command, cwd, bash_exit = job
    return replay_one(_WORKER["detector"], command, cwd, bash_exit, _WORKER["scratch"])


def collect(name: str, root: Path, claude_dir: Path,
            codex_dirs: list[Path]) -> tuple[list[Command], dict]:
    commands: list[Command] = []
    files = _claude_files(claude_dir, root)
    unproven: Counter = Counter()
    for f in files:
        commands.extend(extract_claude(f, name, unproven))
    unreadable: list[Path] = []
    rollouts = codex_rollouts(codex_dirs, root, unreadable)
    stats = CodexStats()
    for r in rollouts:
        found, one = extract_codex(r, name, root)
        commands.extend(found)
        stats.computed_cmd += one.computed_cmd
        stats.not_run += one.not_run
        stats.computed_workdir += one.computed_workdir
    seen: set[tuple[str, str]] = set()
    unique: list[Command] = []
    for c in commands:
        key = (c.host, c.call_id) if c.call_id else (c.host, c.command + c.cwd)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    meta = {"claude_transcripts": len(files), "codex_rollouts": len(rollouts),
            # Rollouts that could not be read at all, so their project is
            # unknown: counted for every project, never skipped silently.
            "codex_rollouts_unreadable": len(unreadable),
            "codex_calls_with_computed_cmd": stats.computed_cmd,
            # Calls left out for want of positive evidence that they ran
            # and succeeded (an error result, a rejection, no result, a
            # call still running, a snippet that failed or never finished).
            "claude_calls_unproven": unproven["claude"],
            "codex_calls_unproven": stats.not_run,
            "codex_calls_with_computed_workdir": stats.computed_workdir}
    return unique, meta


POPULATIONS = {
    # Calls with positive evidence that they ran and succeeded: what the
    # criterion is judged on, in total and per host.
    "executed": ("hook-result", "exit-status"),
    "executed-claude": ("hook-result",),
    "executed-codex-direct": ("exit-status",),
    # Codex code-mode calls read from source: the snippet ran, the call may
    # not have (a branch not taken), and its exit status is unknown.
    "code-mode-source": ("code-mode-source",),
}


def _summary(rows: list[tuple[Command, str | None, Outcome]]) -> dict:
    by_cat: dict[str, Counter] = {}
    misses: Counter = Counter()
    total: Counter = Counter()
    not_data_events = 0
    for c, cat, outcome in rows:
        if cat is None:
            not_data_events += outcome.result == "event"
            continue
        bucket = by_cat.setdefault(f"{c.host}:{cat}", Counter())
        bucket[outcome.result] += 1
        total[outcome.result] += 1
        if outcome.result == "event":
            # One call yields analysis events or one data_command, never both.
            kind = "analysis" if "data_command" not in outcome.kinds else "data_command"
            bucket["event:" + kind] += 1
            total["event:" + kind] += 1
            if outcome.directed:
                total["data_command_with_direction"] += 1
        for reason in set(outcome.skip_reasons):
            bucket["skip:" + reason] += 1
            total["skip:" + reason] += 1
        if outcome.result == "none":
            misses[f"{cat}:{shape(c.command)}"] += 1
    data = total["event"] + total["skip"] + total["none"]
    return {
        "data_touching": data,
        "event": total["event"],
        "event_analysis": total["event:analysis"],
        "event_data_command": total["event:data_command"],
        "data_command_with_direction": total["data_command_with_direction"],
        "skip": total["skip"],
        "skip_reasons": {k[5:]: v for k, v in sorted(total.items()) if k.startswith("skip:")},
        "silent_miss": total["none"],
        "event_share": round(total["event"] / data, 4) if data else None,
        "events_on_non_data_commands": not_data_events,
        "by_category": {k: dict(sorted(v.items())) for k, v in sorted(by_cat.items())},
        "silent_miss_shapes": dict(misses.most_common()),
    }


def run(projects: list[tuple[str, Path]], claude_dir: Path, codex_dirs: list[Path],
        show_misses: int = 0, jobs: int = 1) -> dict:
    report: dict = {"projects": {}, "rule": "tools/lineage_replay.py:classify"}
    with tempfile.TemporaryDirectory(prefix="lineage-replay-") as scratch_root:
        for name, root in projects:
            report["projects"][name] = _run_project(
                name, root, claude_dir, codex_dirs, show_misses, jobs, scratch_root)
    return report


def _run_project(name: str, root: Path, claude_dir: Path, codex_dirs: list[Path],
                 show_misses: int, jobs: int, scratch_root: str) -> dict:
    unique, meta = collect(name, root, claude_dir, codex_dirs)
    resolved = [resolve_cwd(c.cwd, root) for c in unique]
    work = [(c.command, cwd, c.bash_exit) for c, (cwd, _) in zip(unique, resolved)]
    if jobs > 1 and len(work) > 1:
        import multiprocessing
        with multiprocessing.get_context("spawn").Pool(
                jobs, _worker_init, (scratch_root,)) as pool:
            outcomes = []
            for n, o in enumerate(pool.imap(_worker_replay, work, chunksize=8), 1):
                outcomes.append(o)
                if n % 1000 == 0:
                    print(f"{name}: {n}/{len(work)}", file=sys.stderr, flush=True)
    else:
        _worker_init(scratch_root)
        outcomes = [_worker_replay(w) for w in work]
    rows = []
    counts: Counter = Counter()
    shown = 0
    for c, (cwd, _), outcome in zip(unique, resolved, outcomes):
        cat = classify(c.command, cwd, str(root))
        counts[(c.host, c.evidence)] += 1
        rows.append((c, cat, outcome))
        if cat is not None and outcome.result == "none" and shown < show_misses:
            shown += 1
            print(f"--- MISS ({c.host}, {c.evidence}, {cat}, cwd={cwd}) "
                  f"[local only; do not commit]\n{c.command}", file=sys.stderr)
    populations = {
        pop: _summary([r for r in rows if r[0].evidence in kinds])
        for pop, kinds in POPULATIONS.items()
    }
    return {
        **meta,
        "commands": len(unique),
        "commands_by_evidence": {f"{h}:{e}": n for (h, e), n in sorted(counts.items())},
        "cwd_remapped": sum(moved for _, moved in resolved),
        "populations": populations,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--project", action="append", required=True, metavar="NAME=ROOT",
                    help="A project to replay, by its checkout root (repeatable).")
    ap.add_argument("--claude-projects", type=Path,
                    default=Path.home() / ".claude" / "projects")
    ap.add_argument("--codex-sessions", type=Path, action="append",
                    help="Codex rollout directories (default: ~/.codex/sessions and "
                         "~/.codex/archived_sessions).")
    ap.add_argument("--json", type=Path, help="Write the count report here.")
    ap.add_argument("--detector-dir", type=Path,
                    help="Replay another detector checkout's scripts directory "
                         "(e.g. a base branch, for a before/after comparison).")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2),
                    help="Detector worker processes.")
    ap.add_argument("--show-misses", type=int, default=0,
                    help="Print up to N silent-miss commands to stderr (raw "
                         "transcript text: local debugging only, never commit).")
    args = ap.parse_args(argv)
    if args.detector_dir:
        # Read by every worker process at import (spawned workers inherit it).
        os.environ["LINEAGE_REPLAY_DETECTOR_DIR"] = str(args.detector_dir.resolve())
    projects = []
    for spec in args.project:
        name, _, root = spec.partition("=")
        projects.append((name, Path(root).expanduser().resolve()))
    codex = args.codex_sessions or [Path.home() / ".codex" / "sessions",
                                    Path.home() / ".codex" / "archived_sessions"]
    report = run(projects, args.claude_projects, codex, args.show_misses, args.jobs)
    text = json.dumps(report, indent=2)
    if args.json:
        args.json.write_text(text + "\n")
    print(text)
    for name, p in report["projects"].items():
        if not p["commands"]:
            print(f"{name}: no transcripts found", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
