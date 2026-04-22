#!/usr/bin/env python3
"""Minimal YAML frontmatter parser — stdlib only.

Supports scalars (strings, bool, int), lists of quoted strings.
Rejects malformed input by returning an empty dict (does not raise).
"""

from __future__ import annotations

from pathlib import Path


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _coerce_scalar(value: str) -> object:
    v = _strip_quotes(value)
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    try:
        return int(v)
    except ValueError:
        pass
    return v


def parse(path: Path) -> tuple[dict, str]:
    """Return (metadata_dict, body_str) for a markdown file.

    Returns ({}, full_text) if no `---` fence at start.
    Returns ({}, body_or_raw) if malformed.
    """
    try:
        text = path.read_text()
    except OSError:
        return {}, ""
    if not text.startswith("---\n"):
        return {}, text

    # Find closing fence
    rest = text[4:]
    idx = rest.find("\n---\n")
    if idx == -1:
        return {}, text
    fm_block = rest[:idx]
    body = rest[idx + 5 :]

    meta: dict = {}
    lines = fm_block.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            i += 1
            continue
        # List item (extension of previous key)
        if line.startswith("  - ") or line.startswith("- "):
            # Should have been consumed by key handler; treat as malformed
            return {}, body
        if ":" not in line:
            return {}, body
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            # Multi-line list follows
            items: list[str] = []
            i += 1
            while i < len(lines) and (
                lines[i].startswith("  - ") or lines[i].startswith("- ")
            ):
                item = lines[i].split("-", 1)[1].strip()
                items.append(_strip_quotes(item))
                i += 1
            meta[key] = items
            continue
        meta[key] = _coerce_scalar(val)
        i += 1
    return meta, body
