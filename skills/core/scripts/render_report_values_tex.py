"""render_report_values_tex — emit LaTeX macros from a report manifest.

Reads ``analysis/<name>/reports/.manifest.json`` and writes
``analysis/<name>/reports/build/report_values.tex`` containing one
``\\newcommand{\\Macro}{value}`` per entry in ``numbers[*]``, plus the
``\\SciVal`` / ``\\SciText`` wrapper definitions.

The id → macro-name transform is the same one scitexlintr uses (see
``scitexlintr/src/scitexlintr/_manifest.py``). They must agree so that
the lint rule ``snapshot-mismatch`` can resolve ``\\Macro`` against the
manifest entry it came from.

Usage::

    python skills/core/scripts/render_report_values_tex.py \\
        analysis/diff-expr/reports/.manifest.json

By default writes to ``<dirname(manifest)>/build/report_values.tex``.
Pass ``-o PATH`` to override.

Collisions (two manifest ids that resolve to the same local macro name)
are surfaced as a hard error — the user resolves by passing an explicit
``namespace=`` argument to one of the ``register_value`` calls so the
id changes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# id → macro-name transform (mirrors scitexlintr/_manifest.py exactly)
# ---------------------------------------------------------------------------

_DIGIT_WORDS = {
    "0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
    "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine",
}
_NAMESPACE_SPLIT_RE = re.compile(r"[.:/]")


def id_to_macro_name(manifest_id: str) -> str:
    """Apply the documented id→macro transform. Returns name without leading \\."""
    parts = _NAMESPACE_SPLIT_RE.split(manifest_id)
    local = parts[-1] if parts else manifest_id
    if not local:
        return ""

    out: list[str] = []
    for segment in local.split("_"):
        if not segment:
            continue
        if segment.isdigit():
            out.append("".join(_DIGIT_WORDS[d] for d in segment))
        elif segment.isalpha() and len(segment) <= 3:
            out.append(segment.upper())
        else:
            # Mixed or long letter segment. Title-case the letters and spell
            # out any embedded digits: a TeX control word is letters only, so
            # a bare digit would silently terminate the macro name (``\C`` plus
            # literal ``1`` rather than ``\COne``) and break both the emitted
            # \newcommand and scitexlintr's macro lookup.
            chars: list[str] = []
            for j, ch in enumerate(segment):
                if ch.isdigit():
                    chars.append(_DIGIT_WORDS[ch])
                elif j == 0:
                    chars.append(ch.upper())
                else:
                    chars.append(ch.lower())
            out.append("".join(chars))
    return "".join(out)


# ---------------------------------------------------------------------------
# LaTeX escaping for text values
# ---------------------------------------------------------------------------

# Single-pass character table — avoids the classic re-escape bug where
# ``\\`` → ``\\textbackslash{}`` would then have its inserted ``{``/``}``
# re-processed by later rules. Each character is mapped once.
_TEX_ESCAPE_TABLE = {
    "\\": "\\textbackslash{}",
    "&": "\\&",
    "%": "\\%",
    "$": "\\$",
    "#": "\\#",
    "_": "\\_",
    "{": "\\{",
    "}": "\\}",
    "~": "\\textasciitilde{}",
    "^": "\\textasciicircum{}",
}


def tex_escape(s: str) -> str:
    """Escape a plain-text string for safe inclusion as a LaTeX macro body."""
    return "".join(_TEX_ESCAPE_TABLE.get(c, c) for c in s)


# ---------------------------------------------------------------------------
# Value formatting (numbers / bools / strings)
# ---------------------------------------------------------------------------


def format_value(
    value: Any,
    *,
    unit: str | None = None,
    precision: int = 1,
    display: str | None = None,
) -> str:
    """Render a manifest value as it should appear in the generated .tex.

    Display selection, in order:

    1. ``unit`` set    — DERIVE the displayed string from ``value``.
       ``unit="percent"`` turns the stored fraction ``0.978`` into ``97.8\\%``
       at ``precision`` decimal places (default 1; integer precision drops the
       trailing ``.0``). Because the string is a pure function of the canonical
       ``value``, it can never silently disagree with the number the
       verification layer (Phase 6 / scitexlintr) anchors on.
    2. ``display`` set — emit the author-supplied string VERBATIM (no escaping).
       This is the escape hatch for a rare non-derivable one-off (e.g. a
       fold-change with a math-mode ``$\\times$``); the author owns its LaTeX.
    3. neither         — historical behavior: numbers render as their natural
       Python repr (no comma grouping; scitexlintr tolerates grouping at lint
       time but it is not produced by default), strings are LaTeX-escaped.
    """
    if unit is not None:
        if unit != "percent":
            raise ValueError(
                f"render_report_values_tex: unsupported unit {unit!r}. "
                "v1 supports only unit='percent'."
            )
        # bool is an int subclass — exclude it so True/False can't masquerade
        # as a fraction.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                "render_report_values_tex: unit='percent' requires a numeric "
                f"fraction value, got {type(value).__name__!r} (value={value!r})."
            )
        if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
            raise ValueError(
                "render_report_values_tex: precision must be a non-negative int, "
                f"got {precision!r}."
            )
        return f"{value * 100:.{precision}f}" + r"\%"
    if display is not None:
        if not isinstance(display, str):
            raise ValueError(
                "render_report_values_tex: display override must be a string, "
                f"got {type(display).__name__!r} (display={display!r})."
            )
        return display
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        # repr gives the shortest round-trippable form: 0.05 → '0.05'.
        return repr(value)
    if isinstance(value, str):
        return tex_escape(value)
    raise ValueError(
        f"render_report_values_tex: unsupported value type "
        f"{type(value).__name__!r} (value={value!r}). v1 supports only "
        "int / float / bool / str."
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(manifest: dict, *, source_label: str | None = None) -> str:
    numbers = manifest.get("numbers") or []
    if not isinstance(numbers, list):
        raise ValueError("manifest.numbers must be a list")

    # macro_name → (id, value, formatted)
    macros: dict[str, tuple[str, Any, str]] = {}
    collisions: dict[str, list[str]] = {}

    for entry in numbers:
        if not isinstance(entry, dict):
            continue
        manifest_id = entry.get("id")
        if not manifest_id:
            continue
        if "value" not in entry:
            continue
        macro = id_to_macro_name(manifest_id)
        if not macro:
            continue
        value = entry["value"]
        formatted = format_value(
            value,
            unit=entry.get("unit"),
            precision=entry.get("precision", 1),
            display=entry.get("display"),
        )
        if macro in macros:
            collisions.setdefault(macro, [macros[macro][0]]).append(manifest_id)
        else:
            macros[macro] = (manifest_id, value, formatted)

    if collisions:
        details = []
        for macro, ids in collisions.items():
            details.append(f"  \\{macro}  ←  ids: {', '.join(repr(i) for i in ids)}")
        raise ValueError(
            "render_report_values_tex: macro name collisions:\n"
            + "\n".join(details)
            + "\n\nResolve by passing namespace= to one of the register_value calls "
            "so its id (and therefore its macro name) becomes distinct."
        )

    lines: list[str] = []
    lines.append("% auto-generated by render_report_values_tex.py — DO NOT EDIT.")
    if source_label:
        lines.append(f"% source: {source_label}")
    lines.append("")
    lines.append("% Wrapper macros for checked inline snapshots.")
    lines.append("% \\SciVal{\\Macro}{snapshot} prints only \\Macro; the snapshot")
    lines.append("% is reviewed by scitexlintr for drift.")
    lines.append("\\providecommand{\\SciVal}[2]{#1}")
    lines.append("\\providecommand{\\SciText}[2]{#1}")
    lines.append("")
    lines.append("% Generated value macros (sorted by macro name for stable diffs).")
    for macro in sorted(macros):
        manifest_id, _value, formatted = macros[macro]
        lines.append(f"\\newcommand{{\\{macro}}}{{{formatted}}}  % {manifest_id}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="render_report_values_tex",
        description="Emit LaTeX macros from a report .manifest.json",
    )
    parser.add_argument("manifest", help="path to .manifest.json")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="output path (default: <dirname(manifest)>/build/report_values.tex)",
    )
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"{manifest_path}: invalid JSON: {exc}", file=sys.stderr)
        return 2

    if args.output:
        out_path = Path(args.output)
    else:
        out_path = manifest_path.parent / "build" / "report_values.tex"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        rendered = render(manifest, source_label=str(manifest_path))
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    out_path.write_text(rendered, encoding="utf-8")
    n_numbers = len(manifest.get("numbers") or [])
    print(f"wrote {out_path} ({n_numbers} value(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
