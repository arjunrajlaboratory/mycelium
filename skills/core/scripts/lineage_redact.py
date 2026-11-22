"""lineage_redact — one redaction policy for every recorded command text.

Lineage records keep command text (`bash_cmd`), parsed settings (`argv`,
`env`, `script_args`), and embedded script source. A secret passed on a
command line must not survive into any of them. Values are replaced by
``<redacted>``; nothing is hashed, because a digest of a short secret can
be reversed by guessing.

Redacted:

- shell assignments whose name looks secret (``HF_TOKEN=…``, ``export
  DB_PASSWORD=…``, ``env API_KEY=… cmd``), as whole shell words, so adjacent
  quoted fragments and escaped spaces go too;
- the values of options whose name looks secret, in ``--token X`` and
  ``--token=X`` form, again as whole words;
- URL userinfo passwords (``https://user:pass@host``);
- ``Authorization:`` header values;
- quoted secret literals in code, of any length, including escaped quotes
  and triple-quoted strings.

Stdlib only; imported by the hook-path extractor and observer, so it must
stay cheap.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "<redacted>"

_SECRET_WORD = (r"(?:token|secret|passw(?:or)?d?|pwd|api[_-]?key|private[_-]?key|credential"
                r"|auth(?!or)|cookie|session)")
SECRET_NAME_RX = re.compile(rf"(?i){_SECRET_WORD}")
_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_URL_USERINFO_RX = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@'\"]+):([^\s/@'\"]+)@")
_AUTH_HEADER_RX = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic|token|digest)?\s*)([^\s'\"\\]+)"
)
_OPERATORS = set(";&|<>()\n")


def _words(text: str) -> list[tuple[int, int]]:
    """(start, end) spans of the shell words in ``text``: quotes and
    backslash escapes stay inside a word; whitespace and operators end it."""
    spans: list[tuple[int, int]] = []
    start = None
    quote = None
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if quote is None:
            if c == "\\" and i + 1 < n:
                if start is None:
                    start = i
                i += 2
                continue
            if c in " \t" or c in _OPERATORS:
                if start is not None:
                    spans.append((start, i))
                    start = None
                i += 1
                continue
            if start is None:
                start = i
            if c in "'\"":
                quote = c
        elif quote == "'":
            if c == "'":
                quote = None
        else:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                quote = None
        i += 1
    if start is not None:
        spans.append((start, n))
    return spans


def _unquoted_head(word: str) -> str:
    """The part of a word before its first quote or escape (where a name or
    an option name lives)."""
    for i, c in enumerate(word):
        if c in "'\"\\":
            return word[:i]
    return word


def redact_text(text: str) -> str:
    """The text with every secret value replaced; unchanged when none."""
    if not text:
        return text
    replacements: list[tuple[int, int, str]] = []
    spans = _words(text)
    redact_next = False
    for start, end in spans:
        word = text[start:end]
        head = _unquoted_head(word)
        if redact_next:
            redact_next = False
            if not head.startswith("-"):
                replacements.append((start, end, REDACTED))
                continue
        name, eq, _ = head.partition("=")
        if eq and _NAME_RX.match(name) and SECRET_NAME_RX.search(name):
            replacements.append((start, end, f"{name}={REDACTED}"))
        elif eq and name.startswith("-") and SECRET_NAME_RX.search(name):
            replacements.append((start, end, f"{name}={REDACTED}"))
        elif (not eq and head == word and head.startswith("-")
              and SECRET_NAME_RX.search(head)):
            redact_next = True
    out = text
    for start, end, new in reversed(replacements):
        out = out[:start] + new + out[end:]
    out = _URL_USERINFO_RX.sub(lambda m: f"{m.group(1)}:{REDACTED}@", out)
    out = _AUTH_HEADER_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    return out


_LITERAL = (r"(?:[rRbBuUfF]{1,2})?(?:'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\""
            r"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\")")
_SOURCE_LITERAL_RX = re.compile(
    rf"""(?i)\b([A-Za-z_][A-Za-z0-9_]*{_SECRET_WORD}[A-Za-z0-9_]*|{_SECRET_WORD}[A-Za-z0-9_]*)"""
    rf"""(\s*(?:=|:|<-)\s*)({_LITERAL})"""
)


def _keep_quotes(head: str, literal: str) -> str:
    """The literal with its prefix and quotes kept and its body redacted."""
    quote_at = next(i for i, c in enumerate(literal) if c in "'\"")
    prefix = literal[:quote_at]
    quote = literal[quote_at:quote_at + 3]
    if quote not in ("'''", '"""'):
        quote = literal[quote_at]
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
        elif eq and _NAME_RX.match(name) and SECRET_NAME_RX.search(name):
            out.append(f"{name}={REDACTED}")
        elif token.startswith("-") and not eq and SECRET_NAME_RX.search(token):
            out.append(token)
            redact_next = True
        else:
            out.append(redact_command(token))
    return out


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RX.search(name))
