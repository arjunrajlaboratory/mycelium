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
import shlex
from typing import Any

REDACTED = "<redacted>"

_SECRET_WORD = (r"(?:token|secret|passw(?:or)?d?|pwd|api[_-]?key|private[_-]?key|credential"
                r"|auth(?!or)|cookie|session)")
SECRET_NAME_RX = re.compile(rf"(?i){_SECRET_WORD}")
_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# The whole userinfo goes: a token may sit in the username
# (`https://ghp_x@host`, `https://TOKEN:x-oauth-basic@host`) as easily as
# in the password. And query parameters whose name looks secret.
_URL_USERINFO_RX = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)([^\s/@'\"<>]+)@")
_URL_QUERY_SECRET_RX = re.compile(
    rf"(?i)([?&][^=&\s'\"#]*(?:{_SECRET_WORD[3:-1]}|key|sig(?:nature)?|credential)"
    rf"[^=&\s'\"#]*=)([^&\s'\"#]+)")
# The whole header value goes, not its first token: a Digest header carries
# its response in later fields. Unquoted text stops at a quote or line end;
# a header inside a shell word is handled on the decoded word first.
_AUTH_HEADER_RX = re.compile(r"(?i)(authorization\s*:\s*)([^\n'\"]+)")
_AUTH_WORD_RX = re.compile(r"(?i)\s*authorization\s*:")
_OPERATORS = set(";&|<>()\n")


def _redact_urls(text: str) -> str:
    """URL credentials: the whole userinfo, and query parameters named like
    secrets (`?key=…`, `&X-Amz-Signature=…`)."""
    out = _URL_USERINFO_RX.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
    return _URL_QUERY_SECRET_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", out)


_LONG_OPTION_RX = re.compile(r"--[A-Za-z][A-Za-z0-9_-]*(?:=|\Z)")


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
            if c == "\\" and i + 1 < n and text[i + 1] == "\n":
                # A line continuation: the shell deletes it, joining the
                # lines; between words it separates nothing.
                i += 2
                continue
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


def _decode(word: str) -> str:
    """A shell word with its quoting removed, as the program receives it.
    Expansions are left as written; this only decides what is secret."""
    out: list[str] = []
    quote = None
    i = 0
    n = len(word)
    while i < n:
        c = word[i]
        if quote is None:
            if c == "\\" and i + 1 < n:
                if word[i + 1] != "\n":  # a continuation is deleted
                    out.append(word[i + 1])
                i += 2
                continue
            if c in "'\"":
                quote = c
            else:
                out.append(c)
        elif quote == "'":
            if c == "'":
                quote = None
            else:
                out.append(c)
        else:
            if c == "\\" and i + 1 < n and word[i + 1] in '$`"\\\n':
                out.append(word[i + 1])
                i += 2
                continue
            if c == '"':
                quote = None
            else:
                out.append(c)
        i += 1
    return "".join(out)


def redact_text(text: str) -> str:
    """The text with every secret value replaced; unchanged when none.

    Words are judged on their decoded form, so a quoted option name
    (``"--token" X``, ``'--api-key=X'``) is caught like a bare one."""
    if not text:
        return text
    replacements: list[tuple[int, int, str]] = []
    spans = _words(text)
    redact_next = False
    for start, end in spans:
        word = text[start:end]
        head = _decode(word)
        if _AUTH_WORD_RX.match(head):
            replacements.append((start, end, shlex.quote(f"Authorization: {REDACTED}")))
            redact_next = False
            continue
        if redact_next:
            redact_next = False
            if not _LONG_OPTION_RX.match(head):
                # A value may begin with "-" (`--password -123456`); only a
                # word shaped like the next long option is left alone.
                replacements.append((start, end, REDACTED))
                continue
        name, eq, _ = head.partition("=")
        if eq and _NAME_RX.match(name) and SECRET_NAME_RX.search(name):
            replacements.append((start, end, f"{name}={REDACTED}"))
        elif eq and name.startswith("-") and SECRET_NAME_RX.search(name):
            replacements.append((start, end, f"{name}={REDACTED}"))
        elif not eq and head.startswith("-") and SECRET_NAME_RX.search(head):
            redact_next = True
    out = text
    for start, end, new in reversed(replacements):
        out = out[:start] + new + out[end:]
    out = _redact_urls(out)
    out = _AUTH_HEADER_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    return out


def _redact_decoded_words(text: str) -> str:
    """Secret literals hidden by shell quoting: ``python -c "pw = \\"x\\""``
    holds ``pw = "x"`` only once the shell has decoded it. A word whose
    decoded body changes is replaced by the quoted, redacted body."""
    replacements: list[tuple[int, int, str]] = []
    for start, end in _words(text):
        word = text[start:end]
        if not any(c in word for c in "'\"\\"):
            continue
        decoded = _decode(word)
        redacted = redact_source(decoded)
        if redacted != decoded:
            replacements.append((start, end, shlex.quote(redacted)))
    out = text
    for start, end, new in reversed(replacements):
        out = out[:start] + new + out[end:]
    return out


_LITERAL = (r"(?:[rRbBuUfF]{1,2})?(?:'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\""
            r"|'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\")")
# A secret-named key followed by a literal: `token = "x"`, `token <- 'x'`,
# and the quoted-key forms `{"api_key": "x"}` and `headers["Authorization"]
# = "x"` (the closing quote and bracket of the key are optional).
_SOURCE_LITERAL_RX = re.compile(
    rf"""(?i)\b([A-Za-z_][A-Za-z0-9_]*{_SECRET_WORD}[A-Za-z0-9_]*|{_SECRET_WORD}[A-Za-z0-9_]*"""
    rf"""|authorization)"""
    # An annotation may sit between the name and `=` (`API_TOKEN: str = …`,
    # `key: Final[str] = …`).
    rf"""(["']?\]?(?:\s*:\s*(?:[A-Za-z_][\w.\[\], |]*?|"[^"\n]*"|'[^'\n]*')(?=\s*=))?"""
    rf"""\s*(?:=|:|<-|,)\s*)"""
    rf"""({_LITERAL})"""
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
    out = _redact_urls(text)
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
    return redact_source(_redact_decoded_words(redact_text(text)))


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
        if redact_next and not _LONG_OPTION_RX.match(token):
            out.append(REDACTED)
            redact_next = False
            continue
        redact_next = False
        if _AUTH_WORD_RX.match(token):
            out.append(f"Authorization: {REDACTED}")
            continue
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
