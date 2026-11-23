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
# A shell variable, or an argument's key (`db.password=X`, `api-key=X`).
_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*\Z")
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
# The value stops before a backslash too, so an escaped quote inside a
# double-quoted header (`"Authorization: Digest a=\"b\""`) keeps its
# escape and the word pass still sees one word, which it redacts whole.
_AUTH_HEADER_RX = re.compile(r"(?i)(authorization\s*:\s*)([^\n'\"\\]+)")
_AUTH_WORD_RX = re.compile(r"(?i)\s*authorization\s*:")
# A header inside one word, possibly behind an attached option.
_AUTH_IN_WORD_RX = re.compile(r"(?i)((?:-{1,2}[A-Za-z][\w-]*=?)?)\s*authorization\s*:")
_AUTH_LINE_RX = re.compile(r"(?im)^(\s*authorization\s*:\s*)(\S.*)$")
_OPERATORS = set(";&|<>()\n")


# A URL span; an earlier pass's `<redacted>` inside it does not end it.
_URL_SPAN_RX = re.compile(r"(?i)\b[a-z][a-z0-9+.-]{0,30}://(?:<redacted>|[^\s'\"<>`])*")


def _redact_url_span(url: str) -> str:
    url = _URL_USERINFO_RX.sub(lambda m: f"{m.group(1)}{REDACTED}@", url)
    if "?" in url:
        url = _URL_QUERY_SECRET_RX.sub(lambda q: f"{q.group(1)}{REDACTED}", url)
    return url


def _redact_urls(text: str) -> str:
    """URL credentials: the whole userinfo, and query parameters named like
    secrets (`?key=…`, `&X-Amz-Signature=…`), both in one pass over each
    URL span of the original text. Query parameters are looked for only
    inside URL spans: text full of `?` that is not a URL (SQL placeholders)
    is never rescanned from each one."""
    if "://" not in text:
        return text
    return _URL_SPAN_RX.sub(lambda m: _redact_url_span(m.group(0)), text)


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
            elif c == "`" or (c == "$" and i + 1 < n and text[i + 1] == "("):
                # A command substitution belongs to the word around it:
                # `TOKEN=$(printf %s x)` is one assignment.
                i = _substitution_end(text, i)
                continue
        elif quote == "'":
            if c == "'":
                quote = None
        else:
            if c == "\\" and i + 1 < n:
                i += 2
                continue
            if c == '"':
                quote = None
            elif c == "`" or (c == "$" and i + 1 < n and text[i + 1] == "("):
                i = _substitution_end(text, i)
                continue
        i += 1
    if start is not None:
        spans.append((start, n))
    return spans


def _substitution_end(text: str, i: int) -> int:
    """The index just past a `$( … )` or backtick substitution starting at
    ``i`` (the end of the text when it is unclosed)."""
    return _substitution_span(text, i)[0]


def _substitution_span(text: str, i: int) -> tuple[int, bool]:
    """(end, closed) for the substitution starting at ``i``. Quotes inside
    are skipped so a `)` in a string does not close it."""
    n = len(text)
    if text[i] == "`":
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == "`":
                return j + 1, True
            j += 1
        return n, False
    depth = 0
    j = i + 1
    quote = None
    while j < n:
        c = text[j]
        if quote:
            if c == "\\" and quote == '"':
                j += 2
                continue
            if c == quote:
                quote = None
        elif c == "\\":
            j += 2
            continue
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1, True
        j += 1
    return n, False


# A secret-named key nested inside a value: `--config=api_token=X`,
# `-C db.password=X`, `opts=a=1,token=X`.
_NESTED_SECRET_RX = re.compile(
    rf"(?i)[=,:;]\s*[A-Za-z0-9_.-]*{_SECRET_WORD}[A-Za-z0-9_.-]*=")


def _nested_secret_cut(decoded: str) -> str | None:
    """The decoded word cut after a nested secret name's `=`, or None."""
    match = _NESTED_SECRET_RX.search(decoded)
    if match is None:
        return None
    return decoded[:match.end()] + REDACTED


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
    # Header lines first, on the raw text: a header in a heredoc body is
    # not one shell word, so its value would survive the word pass.
    # A line that is itself a header (a heredoc or here-string body) goes
    # to its end, quotes and all.
    text = _AUTH_LINE_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _AUTH_HEADER_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    replacements: list[tuple[int, int, str]] = []
    spans = _words(text)
    redact_next = False
    for start, end in spans:
        word = text[start:end]
        head = _decode(word)
        header = _AUTH_IN_WORD_RX.match(head)
        if header:
            # `Authorization: …`, `--header=Authorization: …`,
            # `-HAuthorization: …`: the whole decoded value goes.
            replacements.append((start, end, shlex.quote(
                f"{header.group(1)}Authorization: {REDACTED}")))
            redact_next = False
            continue
        if redact_next:
            redact_next = False
            # The value may look like an option (`--password --ab9_Xm3q`
            # is a password to getopt): the next word always goes, except
            # after a flag that plainly takes none.
            replacements.append((start, end, REDACTED))
            continue
        name, eq, _ = head.partition("=")
        if eq and _NAME_RX.match(name) and SECRET_NAME_RX.search(name):
            replacements.append((start, end, f"{name}={REDACTED}"))
        elif eq and name.startswith("-") and SECRET_NAME_RX.search(name):
            replacements.append((start, end, f"{name}={REDACTED}"))
        elif not eq and head.startswith("-") and SECRET_NAME_RX.search(head):
            redact_next = not _NO_VALUE_FLAG_RX.search(head)
        elif eq and (cut := _nested_secret_cut(head)) is not None:
            replacements.append((start, end, shlex.quote(cut)))
        elif "$(" in word or "`" in word:
            # Options inside a substitution are options too:
            # `x=$(fetch --token T)`.
            inner = _redact_substitutions(word)
            if inner != word:
                replacements.append((start, end, inner))
    out = text
    for start, end, new in reversed(replacements):
        out = out[:start] + new + out[end:]
    out = _redact_urls(out)
    out = _AUTH_HEADER_RX.sub(lambda m: f"{m.group(1)}{REDACTED}", out)
    return out


# Secret-named flags that take no value (`--no-password`, `--ask-pass`,
# `--password-stdin`): the word after them is kept.
_NO_VALUE_FLAG_RX = re.compile(r"(?i)^--?(?:no-|ask-|prompt-)|-stdin$")


def _redact_substitutions(word: str, depth: int = 0) -> str:
    """The word with the text inside each `$( … )` or backtick redacted as
    a command of its own. An unclosed substitution (whose end cannot be
    found) has everything after its opening redacted."""
    out: list[str] = []
    i = 0
    n = len(word)
    while i < n:
        c = word[i]
        opens = 2 if word.startswith("$(", i) else 1 if c == "`" else 0
        if not opens:
            out.append(c)
            i += 1
            continue
        end, closed = _substitution_span(word, i)
        if not closed:
            out.append(word[i:i + opens] + REDACTED)
            break
        inner = word[i + opens:end - 1]
        out.append(word[i:i + opens]
                   + (redact_command(inner) if depth < 8 else REDACTED)
                   + word[end - 1])
        i = end
    return "".join(out)


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
        # The decoded body may itself be a command (`bash -c '…'`,
        # `ssh h '…'`): shell-level rules apply to it too. redact_text does
        # not come back here, so this recursion is one level deep.
        redacted = redact_source(redact_text(decoded))
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
    # A parenthesized value, and adjacent literals that concatenate
    # (`("abc" "def")`), go whole.
    rf"""(\(\s*)?({_LITERAL}(?:\s*{_LITERAL})*)"""
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
        lambda m: _keep_quotes(m.group(1) + m.group(2) + (m.group(3) or ""), m.group(4)), out
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
    # A secret value that opens a command substitution a naive split broke
    # up (`TOKEN=$(printf %s x)` as three tokens): its tokens are redacted
    # until the substitution closes.
    open_depth = 0
    for token in argv:
        if open_depth and isinstance(token, str):
            open_depth += token.count("(") - token.count(")")
            if token.count("`") % 2:
                open_depth = 0 if open_depth <= 1 else open_depth - 1
            open_depth = max(open_depth, 0)
            out.append(REDACTED)
            continue
        if not isinstance(token, str):
            out.append(token)
            redact_next = False
            continue
        name_part, eq_part, value_part = token.partition("=")
        if (eq_part and SECRET_NAME_RX.search(name_part)
                and (value_part.startswith("$(") or value_part.startswith("`"))):
            depth = value_part.count("(") - value_part.count(")")
            ticks = value_part.count("`") % 2
            open_depth = max(depth, 1 if ticks else 0)
        if redact_next:
            out.append(REDACTED)
            redact_next = False
            continue
        redact_next = False
        header = _AUTH_IN_WORD_RX.match(token)
        if header:
            out.append(f"{header.group(1)}Authorization: {REDACTED}")
            continue
        name, eq, _ = token.partition("=")
        if eq and name.startswith("-") and SECRET_NAME_RX.search(name):
            out.append(f"{name}={REDACTED}")
        elif eq and _NAME_RX.match(name) and SECRET_NAME_RX.search(name):
            out.append(f"{name}={REDACTED}")
        elif token.startswith("-") and not eq and SECRET_NAME_RX.search(token):
            out.append(token)
            redact_next = not _NO_VALUE_FLAG_RX.search(token)
        elif eq and (cut := _nested_secret_cut(token)) is not None:
            out.append(cut)
        else:
            out.append(redact_command(token))
    return out


def is_secret_name(name: str) -> bool:
    return bool(SECRET_NAME_RX.search(name))


# ---------------------------------------------------------------------------
# Commit allowlist (text_policy "allowlist-v1")
#
# The functions above are a denylist: they find secrets they know the shape
# of. Command text is unbounded (every shell, CLI and language), so a
# denylist is never complete. Records that are COMMITTED (the per-session
# manifests under .living/log/data-lineage/) therefore keep only words known
# to be safe; everything else becomes `<v>`. Full (denylist-redacted) text
# stays in the machine-local stores under .mycelium/.
#
# Kept verbatim:
# - the command word of each simple command (path characters only), shell
#   keywords, and the command after a known wrapper (`env`, `nohup`, ...);
# - option names (`-x`, `-abc`, `--long-name`; `--opt=` of `--opt=value`);
# - assignment names (`NAME=` of `NAME=value`);
# - values that, decoded, are one of the record's own paths (`keep_paths`);
# - short plain numbers (at most 6 characters), except as the value of a
#   secret-named option or assignment;
# - operators, redirections and whitespace layout.
# A heredoc body becomes `<heredoc: N lines>`.
# ---------------------------------------------------------------------------

COMMIT_VALUE = "<v>"
COMMIT_POLICY = "allowlist-v1"

# A command name: path characters, not led by a dash (that is an option).
_CMD_WORD_RX = re.compile(r"[A-Za-z0-9_./+][A-Za-z0-9_./+-]*\Z")
_LONG_OPT_RX = re.compile(r"--[A-Za-z][A-Za-z0-9_-]{0,63}\Z")
_LONG_OPT_EQ_RX = re.compile(r"(--[A-Za-z][A-Za-z0-9_-]{0,63})=")
# Single-dash option names: a short cluster (`-x`, `-abc`, `-pdf`), a digit
# level (`-O2`, `-j8`), or an all-lowercase word (`-resume`, `-params-file`).
# Anything else glued to a single dash may be a value (`-pS3cr3t`).
_SHORT_OPT_RX = re.compile(r"-(?:[A-Za-z]{1,4}|[A-Za-z][0-9]{1,3}|[a-z]+(?:-[a-z]+)*)\Z")
_ASSIGN_RX = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]{0,63})=")
_NUMBER_RX = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\Z")
# Numeric one-time codes are secrets too, though no denylist name covers them.
_EXTRA_SECRET_RX = re.compile(r"(?i)(?:^|[-_])(?:pin|otp|totp|mfa|passcode|2fa)(?:$|[-_])")
_SHELL_KEYWORDS = frozenset({
    "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for",
    "case", "esac", "function", "{", "}", "!", "[[", "]]", "select", "coproc",
})
_WRAPPERS = frozenset({"env", "nohup", "time", "exec", "command", "builtin", "nice"})
_HEREDOC_RX = re.compile(r"<<(?!<)(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s;&|<>()'\"]+))")
_DELIM_RX = re.compile(r"-?[A-Za-z_][A-Za-z0-9_]{0,15}\Z")
# Placeholders already in the text (`<redacted>` from the denylist, `<v>`)
# are values; they are hidden from the word scanner, which splits on `<>`.
_HIDDEN = "\x01"


def _commit_secret_name(name: str) -> bool:
    return is_secret_name(name) or bool(_EXTRA_SECRET_RX.search(name))


def _norm_path(value: str) -> str:
    import posixpath

    while value.startswith("./") and len(value) > 2:
        value = value[2:]
    value = posixpath.normpath(value) if value else value
    return value


def commit_keep_paths(paths: Any) -> set[str]:
    """Every spelling of the record's own paths a command may use: the
    path, and each run of its components (`/r/analysis/de/run.py` gives
    `analysis/de/run.py`, `de/run.py`, `run.py`, `analysis/de`, ...). All of
    these are already in the record, so keeping them discloses nothing."""
    out: set[str] = set()
    for path in paths or ():
        if not isinstance(path, str) or not path or "\n" in path:
            continue
        norm = _norm_path(path)
        out.add(norm)
        absolute = norm.startswith("/")
        parts = [p for p in norm.split("/") if p]
        if len(parts) > 64:
            continue
        for i in range(len(parts)):
            for j in range(i + 1, len(parts) + 1):
                joined = "/".join(parts[i:j])
                out.add(joined)
                if i == 0 and absolute:
                    out.add("/" + joined)
    out.discard("")
    out.discard(".")
    out.discard("..")
    return out


def _is_number(value: str) -> bool:
    return bool(_NUMBER_RX.match(value)) and len(value.lstrip("-")) <= 6


def commit_safe_value(value: str, keep_paths: set[str], secret: bool = False) -> str:
    """One decoded value under the allowlist: a record path or a short
    number is kept, anything else (and any secret-named value) is `<v>`."""
    if secret or not isinstance(value, str) or _HIDDEN in value or REDACTED in value \
            or COMMIT_VALUE in value:
        return COMMIT_VALUE
    if value and _norm_path(value) in keep_paths:
        return shlex.quote(value)
    if _is_number(value):
        return value
    return COMMIT_VALUE


class _CommitState:
    __slots__ = ("cmd", "wrapper", "secret_next", "loop_name")

    def __init__(self, cmd: bool) -> None:
        self.cmd = cmd
        self.wrapper = False
        self.secret_next = False
        self.loop_name = False


def _commit_word(raw: str, dec: str, keep_paths: set[str], state: _CommitState) -> str:
    """One word (as written, and decoded) under the allowlist."""
    if state.secret_next:
        state.secret_next = False
        state.cmd = state.wrapper = False
        return COMMIT_VALUE
    if _HIDDEN in dec:
        if state.cmd and not state.wrapper:
            state.cmd = False
        match = _ASSIGN_RX.match(dec)
        if match:
            return match.group(0) + COMMIT_VALUE
        long_eq = _LONG_OPT_EQ_RX.match(dec)
        if long_eq:
            return long_eq.group(0) + COMMIT_VALUE
        return COMMIT_VALUE
    assign = _ASSIGN_RX.match(dec)
    if state.cmd:
        if assign:
            name = assign.group(1)
            return assign.group(0) + commit_safe_value(
                dec[assign.end():], keep_paths, _commit_secret_name(name))
        if raw == dec and dec in _SHELL_KEYWORDS:
            return dec
        if raw == dec and dec in ("for", "select", "case"):
            # `for NAME in WORDS`: a variable name, then values, not a command.
            state.cmd = state.wrapper = False
            state.loop_name = dec != "case"
            return dec
        if state.wrapper and (_LONG_OPT_RX.match(dec) or _SHORT_OPT_RX.match(dec)
                              or _is_number(dec)):
            return dec
        state.cmd = False
        state.wrapper = False
        if _CMD_WORD_RX.match(raw):
            if raw in _WRAPPERS:
                state.cmd = state.wrapper = True
            return raw
        return COMMIT_VALUE
    if state.loop_name:
        state.loop_name = False
        if raw == dec and _NAME_RX.match(dec) and "." not in dec and "-" not in dec:
            return dec
        return COMMIT_VALUE
    if dec in ("-", "--") or (raw == dec == "in"):
        return dec
    if _LONG_OPT_RX.match(dec):
        if _commit_secret_name(dec) and not _NO_VALUE_FLAG_RX.search(dec):
            state.secret_next = True
        return dec
    long_eq = _LONG_OPT_EQ_RX.match(dec)
    if long_eq:
        return long_eq.group(0) + commit_safe_value(
            dec[long_eq.end():], keep_paths, _commit_secret_name(long_eq.group(1)))
    if _is_number(dec):
        return dec
    if _SHORT_OPT_RX.match(dec):
        if _commit_secret_name(dec) and not _NO_VALUE_FLAG_RX.search(dec):
            state.secret_next = True
        return dec
    if assign:
        return assign.group(0) + commit_safe_value(
            dec[assign.end():], keep_paths, _commit_secret_name(assign.group(1)))
    return commit_safe_value(dec, keep_paths)


def _gap_kind(gap: str) -> tuple[bool, bool, bool]:
    """(separator, redirection, heredoc) for the text between two words."""
    sep = redir = False
    gap = gap.replace("\\\n", " ")  # a line continuation separates nothing
    for i, c in enumerate(gap):
        if c in "<>":
            redir = True
        elif c == "&":
            if (i > 0 and gap[i - 1] in "<>") or (i + 1 < len(gap) and gap[i + 1] == ">"):
                redir = True
            else:
                sep, redir = True, False
        elif c in ";|()\n":
            sep, redir = True, False
    tail = gap.rstrip(" \t")
    heredoc = redir and tail.endswith("<<") and not tail.endswith("<<<")
    return sep, redir, heredoc


def _commit_shell(text: str, keep_paths: set[str]) -> str:
    out: list[str] = []
    state = _CommitState(cmd=True)
    pos = 0
    for start, end in _words(text):
        gap = text[pos:start]
        out.append(gap)
        sep, redir, heredoc = _gap_kind(gap)
        if sep:
            state = _CommitState(cmd=True)
        raw = text[start:end]
        dec = _decode(raw)
        if heredoc:
            plain = _DELIM_RX.match(dec) and raw in (dec, f"'{dec}'", f'"{dec}"', "\\" + dec)
            out.append(raw if plain else COMMIT_VALUE)
        elif redir:
            out.append(commit_safe_value(dec, keep_paths))
        else:
            out.append(_commit_word(raw, dec, keep_paths, state))
        pos = end
    out.append(text[pos:])
    return "".join(out)


def _hide_placeholders(text: str) -> str:
    return text.replace(REDACTED, _HIDDEN).replace(COMMIT_VALUE, _HIDDEN)


def _show_placeholders(text: str) -> str:
    return text.replace(_HIDDEN, COMMIT_VALUE)


def commit_safe_command(text: Any, keep_paths: set[str] | None = None) -> Any:
    """Command text for a committed record: only allowlisted words survive
    (see the block comment above); separators and layout are kept, and
    each heredoc body collapses to `<heredoc: N lines>`."""
    if not isinstance(text, str) or not text:
        return text
    keep = keep_paths or set()
    text = _hide_placeholders(text)
    out: list[str] = []
    shell: list[str] = []
    pending: list[tuple[str, bool]] = []  # (delimiter, strip leading tabs)
    body = 0

    def flush() -> None:
        if shell:
            out.append(_commit_shell("".join(shell), keep))
            shell.clear()

    def delim_text(word: str) -> str:
        return word if _DELIM_RX.match(word) else COMMIT_VALUE

    for line in text.splitlines(keepends=True):
        if pending:
            delimiter, strip = pending[0]
            content = line.rstrip("\n")
            if (content.lstrip("\t") if strip else content) == delimiter:
                out.append(f"<heredoc: {body} lines>\n")
                out.append(delim_text(delimiter) + ("\n" if line.endswith("\n") else ""))
                pending.pop(0)
                body = 0
            else:
                body += 1
            continue
        shell.append(line)
        for match in _HEREDOC_RX.finditer(line):
            word = next(g for g in match.groups()[1:] if g is not None)
            pending.append((word, bool(match.group(1))))
        if pending:
            flush()
            if not line.endswith("\n"):
                out.append("\n")
    flush()
    if pending:
        out.append(f"<heredoc: {body} lines>")
    return _show_placeholders("".join(out))


def commit_safe_argv(argv: Any, keep_paths: set[str] | None = None,
                     command_word: bool = True) -> Any:
    """An argv list for a committed record, word by word under the same
    allowlist. ``command_word`` says whether the list starts with a command
    (a full argv, wrappers) or is only operands (`script_args`, `params`).
    Non-string tokens (an inline-body placeholder) pass through."""
    if not isinstance(argv, list):
        return argv
    keep = keep_paths or set()
    state = _CommitState(cmd=command_word)
    out: list[Any] = []
    for token in argv:
        if not isinstance(token, str):
            out.append(token)
            state.secret_next = False
            state.cmd = state.wrapper = False
            continue
        token = _hide_placeholders(token)
        out.append(_show_placeholders(_commit_word(token, token, keep, state)))
    return out


_PROSE_CORE_RX = re.compile(r"([(\[\"'`]*)(.*?)([)\]\"'`,.;:!?]*)\Z", re.S)
# The words of the messages the recorders write (lineage warnings, skip
# details), compared case-insensitively. A word outside it, even one of
# plain letters (`MY_SECRET`), is `<v>`: a new message degrades to
# placeholders until its words are added here, never the other way.
COMMIT_PROSE_WORDS = frozenset("""
a an the and or but so of to in on at by for from with without into than then
is are was were be been has have had not no nor may might could can cannot
will would its it this that these those their there which what when where who
one more each every any some all other only also still yet as if
script scripts ran run runs runner runner's script's call calls command commands
names named name file files path paths directory working change changed
earlier unknown left search shell syntax r_batch_options r cd pushd popd cdpath
remote urls url recorded record local resolved statically literal i/o
input/output detected resolve dynamically delegate through imports
analysis stage stage(s recorded separately listed exit checked reopen refused
failed missing extractor helper unreadable lock unavailable redaction omitted
observation observations session sessions completed reported treat changes -
""".split())


def commit_safe_prose(text: Any, keep_paths: set[str] | None = None,
                      replace: tuple[tuple[str, str], ...] = ()) -> Any:
    """A message (a lineage warning, a skip detail) for a committed record.
    Words of the recorders' messages (COMMIT_PROSE_WORDS) stay; numbers,
    record paths and `...` stay; any other token (a URL, a command word, an
    unexpanded `$X`, a name) becomes `<v>`. ``replace`` substitutes known
    variable parts first."""
    if not isinstance(text, str) or not text:
        return text
    keep = keep_paths or set()
    for old, new in replace:
        if old and old != new:
            # Whole tokens only, so a short variable part never cuts a word.
            text = re.sub(rf"(?<!\S){re.escape(old)}(?=[\s)\]\"'`,.;:!?]|\Z)",
                          lambda _m: _hide_placeholders(new), text)
    text = _hide_placeholders(text)
    out: list[str] = []
    # A count is kept only at the start or after a message word (`exit 3`,
    # `ran 3 more`), never after a dropped token (`--pin 4829`).
    prev_word = True
    for piece in re.split(r"(\s+)", text):
        if not piece or piece.isspace():
            out.append(piece)
            continue
        lead, core, trail = _PROSE_CORE_RX.match(piece).groups()
        word = core.lower() in COMMIT_PROSE_WORDS
        if (not core or word or core == "..."
                or (prev_word and _is_number(core))
                or (_HIDDEN not in core and _norm_path(core) in keep)):
            out.append(piece)
        elif _HIDDEN in core and core.strip(_HIDDEN) == "":
            out.append(piece)
            word = False
        else:
            out.append(f"{lead}{COMMIT_VALUE}{trail}")
        prev_word = word
    return _show_placeholders("".join(out))
