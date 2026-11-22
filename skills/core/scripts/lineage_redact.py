"""lineage_redact — one redaction policy for every recorded command text.

Lineage records keep command text (`bash_cmd`), parsed settings (`argv`,
`env`, `script_args`), and embedded script source. A secret passed on a
command line must not survive into any of them. Values are replaced by
``<redacted>``; nothing is hashed, because a digest of a short secret can
be reversed by guessing.

Redacted:

- shell assignments whose name looks secret (``HF_TOKEN=…``, ``export
  DB_PASSWORD=…``, ``env API_KEY=… cmd``);
- the values of options whose name looks secret, in ``--token X`` and
  ``--token=X`` form;
- URL userinfo passwords (``https://user:pass@host``);
- ``Authorization:`` header values.

Stdlib only; imported by the hook-path extractor and observer, so it must
stay cheap.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "<redacted>"

_SECRET_WORD = r"(?:token|secret|passw(?:or)?d?|pwd|api[_-]?key|private[_-]?key|credential|auth(?!or)|cookie|session[_-]?key)"
SECRET_NAME_RX = re.compile(rf"(?i){_SECRET_WORD}")
_VALUE = r"""(?:'[^']*'|"(?:\\.|[^"\\])*"|[^\s;&|<>]+)"""
_ASSIGNMENT_RX = re.compile(
    rf"(?<![\w$])([A-Za-z_][A-Za-z0-9_]*{_SECRET_WORD}[A-Za-z0-9_]*|{_SECRET_WORD}[A-Za-z0-9_]*)"
    rf"=({_VALUE})",
    re.I,
)
_OPTION_RX = re.compile(
    rf"(?<![\w-])(--?[A-Za-z0-9_-]*{_SECRET_WORD}[A-Za-z0-9_-]*)(=|[ \t]+)({_VALUE})",
    re.I,
)
_URL_USERINFO_RX = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@'\"]+):([^\s/@'\"]+)@")
_AUTH_HEADER_RX = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic|token|digest)?\s*)([^\s'\"\\]+)"
)


def redact_text(text: str) -> str:
    """The text with every secret value replaced; unchanged when none."""
    if not text:
        return text
    out = _URL_USERINFO_RX.sub(lambda m: f"{m.group(1)}:{REDACTED}@", text)
    out = _AUTH_HEADER_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    out = _ASSIGNMENT_RX.sub(lambda m: f"{m.group(1)}={REDACTED}", out)
    out = _OPTION_RX.sub(
        lambda m: m.group(0) if m.group(3).startswith("-") else f"{m.group(1)}{m.group(2)}{REDACTED}",
        out,
    )
    return out


_SOURCE_LITERAL_RX = re.compile(
    rf"""(?i)\b([A-Za-z_][A-Za-z0-9_]*{_SECRET_WORD}[A-Za-z0-9_]*|{_SECRET_WORD}[A-Za-z0-9_]*)"""
    r"""(\s*(?:=|:|<-)\s*)((?:[rRbBuUfF]{1,2})?(?:'[^'\n]{4,}'|"[^"\n]{4,}"))"""
)
# Group 3 may start with a Python string prefix (r"…", b'…', f"…", rb"…");
# the replacement keeps the prefix and the quote character.


def _keep_quotes(head: str, literal: str) -> str:
    """The literal with its prefix and quotes kept and its body redacted."""
    quote_at = next(i for i, c in enumerate(literal) if c in "'\"")
    prefix, quote = literal[:quote_at], literal[quote_at]
    return f"{head}{prefix}{quote}{REDACTED}{quote}"


def redact_source(text: str) -> str:
    """Script source with secret *literals* redacted.

    Code assigns secret-named variables all the time (``token =
    os.environ[...]``); only a quoted literal value is a secret written
    into the script, plus URL passwords and header values.
    """
    if not text:
        return text
    out = _URL_USERINFO_RX.sub(lambda m: f"{m.group(1)}:{REDACTED}@", text)
    out = _AUTH_HEADER_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    return _SOURCE_LITERAL_RX.sub(
        lambda m: _keep_quotes(m.group(1) + m.group(2), m.group(3)), out
    )


def redact_command(text: str) -> str:
    """Command text: shell-level secrets (assignments, options, URL
    passwords, headers) and secret literals inside inline code bodies
    (``python -c "password = '…'"``) both redacted."""
    if not text:
        return text
    return redact_source(redact_text(text))


def redact_argv(argv: list[Any]) -> list[Any]:
    """An argv list with secret option values and assignments redacted.

    Token-wise, so ``["--token", "abc"]`` and ``["--token=abc"]`` are both
    caught; non-string tokens (an inline-body placeholder) pass through.
    """
    out: list[Any] = []
    redact_next = False
    for token in argv:
        if not isinstance(token, str):
            out.append(token)
            redact_next = False
            continue
        if redact_next and not token.startswith("-"):
            out.append(REDACTED)
            redact_next = False
            continue
        redact_next = False
        name, eq, _ = token.partition("=")
        if eq and name.startswith("-") and SECRET_NAME_RX.search(name):
            out.append(f"{name}={REDACTED}")
        elif eq and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and SECRET_NAME_RX.search(name):
            out.append(f"{name}={REDACTED}")
        elif token.startswith("-") and not eq and SECRET_NAME_RX.search(token):
            out.append(token)
            redact_next = True
        else:
            out.append(redact_text(token))
    return out


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RX.search(name))
