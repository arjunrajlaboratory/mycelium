"""Shipped code must run on the Python floor in CONTRIBUTING.md (3.10+)."""

from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
# `datetime.UTC` arrived in Python 3.11; `datetime.timezone.utc` works on 3.10.
UTC_ALIAS_RX = re.compile(r"\bdatetime\.UTC\b|^\s*from\s+datetime\s+import\s+[^\n]*\bUTC\b", re.M)


def _shipped_sources() -> list[Path]:
    paths: list[Path] = []
    for root in ("skills", "hooks", "tools"):
        for path in (PLUGIN_ROOT / root).rglob("*"):
            if path.suffix in {".py", ".sh"} and "__pycache__" not in path.parts:
                if path.name != Path(__file__).name:
                    paths.append(path)
    return paths


def test_shipped_code_avoids_python_311_utc_alias() -> None:
    offenders = [
        f"{path.relative_to(PLUGIN_ROOT)}:{text.count(chr(10), 0, match.start()) + 1}"
        for path in _shipped_sources()
        for text in [path.read_text(encoding="utf-8", errors="replace")]
        for match in UTC_ALIAS_RX.finditer(text)
    ]
    assert offenders == [], f"use datetime.timezone.utc (3.10 floor): {offenders}"
