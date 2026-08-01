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

# --- Analysis-invocation detection ---
# Note: bash_exit/bash_wall_s remain None until PostToolUse stdin exposes them.
ANALYSIS_PATTERNS = [
    re.compile(r"(?:^|&&|\||;|\s)python3?\s+"),
    re.compile(r"(?:^|&&|\||;|\s)Rscript\s+"),
    re.compile(r"(?:^|&&|\||;|\s)R\s+(?:--\S+\s+)*-e\s+"),
    re.compile(r"(?:^|&&|\||;|\s)jupyter\s+(?:nbconvert|execute)\s+"),
    re.compile(r"(?:^|&&|\||;|\s)conda\s+run\s+.*python"),
    re.compile(r"(?:^|&&|\||;|\s)uv\s+run\s+.*python"),
    re.compile(r"(?:^|&&|\||;|\s)poetry\s+run\s+.*python"),
]

# --- Script-path / inline-source extraction ---
RX_PYTHON_C = re.compile(r"""python3?\s+-c\s+(['"])(.+?)\1""", re.DOTALL)
RX_R_E = re.compile(r"""R\s+(?:--\S+\s+)*-e\s+(['"])(.+?)\1""", re.DOTALL)
RX_SCRIPT_PATH = re.compile(
    r"(?:python3?|Rscript|jupyter\s+(?:nbconvert|execute))\s+(?:--\S+\s+)*([^\s|&;]+\.(?:py|R|r|ipynb))"
)

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


def _apply_cd(segment: list[str], cwd: Path) -> Path:
    """Apply a simple shell ``cd`` segment without executing user input."""
    while segment and ("=" in segment[0] and not segment[0].startswith(("./", "/"))):
        name, _, _ = segment[0].partition("=")
        if not name.replace("_", "a").isalnum() or name[:1].isdigit():
            break
        segment = segment[1:]
    if not segment or segment[0] != "cd":
        return cwd
    arguments = [value for value in segment[1:] if value != "--"]
    if len(arguments) != 1 or arguments[0] == "-" or "$" in arguments[0]:
        return cwd
    target = Path(os.path.expanduser(arguments[0]))
    if not target.is_absolute():
        target = cwd / target
    target = Path(os.path.abspath(target))
    # The hook sees the command only after the shell ran. Do not propagate a
    # directory change that the shell could not have made: in
    # ``cd missing || python a.py`` the Python command runs from the old cwd.
    if not target.is_dir() or not os.access(target, os.X_OK):
        return cwd
    return target


def _effective_cwd(bash_cmd: str, offset: int, initial_cwd: Path) -> Path:
    """Resolve preceding top-level ``cd`` commands for a command match."""
    try:
        lexer = shlex.shlex(
            bash_cmd[:offset], posix=True, punctuation_chars=";&|()\n"
        )
        # Preserve unquoted newlines as command separators. Quoted newlines
        # stay inside their token and therefore cannot be mistaken for one.
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw_tokens = list(lexer)
    except ValueError:
        return initial_cwd

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


def _detect_scripts_with_cwd(
    bash_cmd: str, cwd: Path
) -> list[tuple[Path | None, str | None, Path]]:
    out: list[tuple[Path | None, str | None, Path]] = []
    seen_paths: set[Path] = set()
    seen_inline: set[tuple[str, Path]] = set()
    for m in RX_PYTHON_C.finditer(bash_cmd):
        src = m.group(2)
        effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
        identity = (src, effective_cwd)
        if identity not in seen_inline:
            out.append((None, src, effective_cwd))
            seen_inline.add(identity)
    for m in RX_R_E.finditer(bash_cmd):
        src = m.group(2)
        effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
        identity = (src, effective_cwd)
        if identity not in seen_inline:
            out.append((None, src, effective_cwd))
            seen_inline.add(identity)
    for m in RX_SCRIPT_PATH.finditer(bash_cmd):
        effective_cwd = _effective_cwd(bash_cmd, m.start(), cwd)
        path = Path(m.group(1))
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
        "bash_exit": None,  # not currently exposed in PostToolUse stdin
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
    ap.add_argument(
        "--append-to",
        type=Path,
        default=None,
        help="Append NDJSON to this file under fcntl.flock. Default: stdout.",
    )
    args = ap.parse_args()

    if not is_analysis(args.bash_cmd):
        return 0

    cwd = Path(args.cwd)
    detections = _detect_scripts_with_cwd(args.bash_cmd, cwd)
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
