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
# A word is kept only on EVIDENCE, never because of where it stands or what
# it looks like:
# - a command word only if it is in a fixed vocabulary of common commands
#   and interpreters (`_COMMIT_COMMANDS`; a system-directory spelling such
#   as `/usr/bin/python3` too, any other directory becomes `<v>/python3`),
#   or exactly one of the record's evidence-backed paths. Shell keywords are
#   kept only where a keyword can stand; a `case` subject or pattern and a
#   `for` list are values;
# - the first operand after a vocabulary command only if it is a fixed
#   subcommand (`git status`, `uv run`, `python3 -m pytest`);
# - short options in a fixed shape: `-x`, `-xy`, `-xzf` (three lower-case
#   letters), `-O2`; at most 3 digits;
# - long options (`--[a-z][a-z0-9-]{0,40}`, at most 3 digits) only on
#   evidence of the role, never by shape alone: a common option
#   (`_COMMIT_COMMON_OPTIONS`), a known option of the vocabulary command
#   receiving it (`_COMMIT_COMMAND_OPTIONS`), or, in the words an
#   interpreter or the run's own script receives, a name the run's source
#   spells as a whole token (`commit_source_options`). For `--opt=value`,
#   `--opt=` on the same evidence and the value rule. After a `--` word
#   every word of that simple command is an operand;
# - assignment and loop-variable names only in one case (`OMP_NUM_THREADS`,
#   `lr`, `trainer.max_epochs`), at most 3 digits;
# - values only if they are exactly (after shell quote removal; one leading
#   `./` may be dropped) one of the record's evidence-backed paths, or a
#   short plain number (at most 6 characters) not given to a secret-named
#   option or assignment. Nothing is normalized: the string approved is the
#   string published;
# - operators, redirections and whitespace layout; a heredoc delimiter from
#   a fixed list (`EOF`, `PY`, ...).
# An unquoted comment becomes `<comment>`; a heredoc body becomes
# `<heredoc: N lines>`. Consolidation reads no file system.
# ---------------------------------------------------------------------------

COMMIT_VALUE = "<v>"
COMMIT_COMMENT = "<comment>"
COMMIT_POLICY = "allowlist-v1"

# Command names known to be programs, not values. Kept as a command word
# (and as the first operand of another vocabulary command: `uv run python`).
_COMMIT_COMMANDS = frozenset("""
python python2 python3 pypy pypy3 ipython pytest Rscript R bash sh zsh dash ksh fish
source . cat head tail less more cp mv ls mkdir rmdir rm touch ln chmod chown echo printf
grep egrep fgrep rg sed awk gawk cut paste join sort uniq wc tr tee find xargs tar gzip
gunzip zcat bzip2 bunzip2 xz unxz zstd unzip zip curl wget git make cmake snakemake
nextflow jupyter jupytext papermill quarto pandoc conda mamba micromamba uv uvx poetry pip
pip3 pipx pixi npm npx node env time nohup cd pushd popd export unset set test [ true
false exit return read sleep wait kill date pwd which type command exec builtin nice sudo
timeout srun sbatch squeue scancel sacct module diff cmp md5sum sha1sum sha256sum shasum
du df basename dirname realpath readlink stat file jq yq sqlite3 duckdb samtools bcftools
bedtools tabix bgzip bwa bowtie2 minimap2 STAR salmon kallisto featureCounts fastqc
multiqc cutadapt docker singularity apptainer rsync scp ssh open seq column comm split
""".split())
_COMMIT_VERSIONED_RX = re.compile(r"(?:python|pip|pypy)[23](?:\.[0-9]{1,2})?\Z")
_COMMIT_SYSTEM_DIRS = frozenset({
    "/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin", "/opt/local/bin",
    "/sbin", "/usr/sbin",
})
# First operands of a vocabulary command known to be subcommands.
_COMMIT_SUBCOMMANDS = frozenset("""
run exec install uninstall activate deactivate create remove update upgrade sync lock add
commit status diff log show checkout switch branch merge rebase pull push fetch clone init
build test list info config help version clean reset restore stash tag remote worktree
CMD BATCH notebook nbconvert lab console kernelspec execute render preview load unload
""".split())
# A subcommand after which the next operand may again be a command or
# subcommand (`uv run python`, `R CMD BATCH`).
_COMMIT_CHAIN = frozenset({"run", "exec", "CMD"})
# Fixed operand words of test expressions and loops.
_COMMIT_OPERAND_WORDS = frozenset({"-", "]", "]]", "=", "==", "!=", "in"})
# Fixed values that are never anyone's data (`> /dev/null`).
_COMMIT_FIXED_VALUES = frozenset({"/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr"})
_COMMIT_DELIMITERS = frozenset("""
EOF EOT END EOS STOP DONE HEREDOC DOC TEXT TXT DATA INPUT OUTPUT CODE SCRIPT PY PYTHON R
RSCRIPT SH BASH SQL JSON YAML YML CSV TSV TOML INI CFG CONFIG MD HTML XML MSG BODY PATCH
""".split())
# Long option names are kept only on evidence of their role, never by shape
# alone (`--cinder-lattice-sable-amber` is shaped like one): a fixed list of
# common options, the known options of the vocabulary command that receives
# it, or the literal `--name` in the source of the script the record ran.
_COMMIT_COMMON_OPTIONS = frozenset("""
--help --version --verbose --quiet --silent --debug --dry-run --force --yes --all
--output --out --outdir --output-dir --input --input-dir --config --configfile --profile
--seed --random-seed --random-state --threads --jobs --cores --workers --cpus --gpus
--device --batch-size --epochs --lr --learning-rate --format --overwrite --resume
--recursive --directory --file --log-level --log-file --no-cache --color --no-color
--json --limit --sample --samples --name --mode --model --data --prefix --tmpdir
""".split())
_COMMIT_COMMAND_OPTIONS: dict[str, frozenset[str]] = {name: frozenset(words.split()) for
                                                      names, words in (
    ("git", """--amend --oneline --stat --cached --staged --no-verify --hard --soft --short
     --branch --porcelain --name-only --name-status --graph --decorate --message --patch
     --global --list --tags --rebase --ff-only --no-ff --depth --set-upstream --delete
     --pretty --since --until --author --reverse --abbrev-commit --no-pager --show-current
     --untracked-files --show-toplevel --abbrev-ref --no-edit --continue --abort --prune"""),
    ("grep egrep fgrep rg", """--ignore-case --line-number --count --files-with-matches
     --invert-match --word-regexp --fixed-strings --extended-regexp --include --exclude
     --exclude-dir --context --after-context --before-context --max-count --only-matching
     --hidden --glob --type --files --no-ignore --smart-case --perl-regexp --no-filename
     --with-filename --null --binary-files"""),
    ("cat head tail less more", "--lines --bytes --number --follow --show-all --squeeze-blank"),
    ("ls", "--long --human-readable --sort --reverse --almost-all --classify"),
    ("cp mv rm ln mkdir rmdir touch chmod chown", """--interactive --preserve --no-clobber
     --parents --archive --symbolic --reference --no-dereference --target-directory"""),
    ("sort uniq wc cut paste join tr comm split column seq", """--numeric-sort --reverse
     --unique --key --field-separator --general-numeric-sort --human-numeric-sort --stable
     --repeated --ignore-case --words --chars --bytes --fields --delimiter --characters
     --delimiters --serial --delete --squeeze-repeats --complement --table --separator
     --equal-width --numeric-suffixes --additional-suffix --lines --check-order"""),
    ("tar gzip gunzip zcat bzip2 bunzip2 xz unxz zstd zip unzip bgzip tabix", """--create
     --extract --list --gzip --bzip2 --xz --exclude --decompress --stdout --keep --best
     --fast --to-stdout --preset"""),
    ("curl wget", """--location --fail --header --request --insecure --remote-name --retry
     --compressed --show-error --max-time --output-document --continue
     --no-check-certificate --directory-prefix --head --include --progress-bar"""),
    ("pip pip3 pipx uv uvx poetry conda mamba micromamba pixi npm npx", """--upgrade --user
     --editable --requirement --no-deps --channel --env --override-channels --frozen
     --locked --no-sync --project --with --python --save-dev --global --system --dev
     --group --extra --all-extras --no-install-project --offline"""),
    ("pytest", """--maxfail --tb --cov --cov-report --durations --lf --ff --exitfirst
     --capture --collect-only --deselect --ignore --junitxml --no-header --runxfail
     --strict-markers --basetemp --rootdir --import-mode --showlocals --last-failed
     --failed-first --keyword --markers --no-cov --numprocesses --dist --timeout"""),
    ("Rscript R", """--vanilla --no-save --no-restore --slave --default-packages --args
     --no-echo --no-environ --no-site-file --no-init-file --restore --save"""),
    ("snakemake", """--snakefile --use-conda --use-singularity --use-apptainer --forceall
     --forcerun --until --printshellcmds --reason --keep-going --rerun-incomplete --unlock
     --dag --rulegraph --latency-wait --cluster --conda-frontend --conda-prefix --sdm
     --executor --software-deployment-method --rerun-triggers --list-rules --summary
     --touch --notemp --keep-incomplete --nolock --show-failed-logs"""),
    ("jupyter jupytext papermill quarto pandoc", """--to --execute --inplace --kernel
     --parameters --output-format --standalone --from --log-output --set-kernel --sync
     --update --pipe --template --toc"""),
    ("jq yq", """--raw-output --compact-output --slurp --arg --argjson --null-input
     --sort-keys --exit-status --tab --indent --join-output"""),
    ("sed awk gawk", """--in-place --expression --regexp-extended --assign
     --null-data --posix --sandbox"""),
    ("xargs", "--null --max-args --max-procs --replace --no-run-if-empty --arg-file"),
    ("env", "--unset --ignore-environment --chdir --split-string --null"),
    ("timeout", "--signal --kill-after --preserve-status --foreground"),
    ("nice", "--adjustment"),
    ("sudo", "--user --group --preserve-env --login --non-interactive"),
    ("srun sbatch squeue scancel sacct", """--ntasks --cpus-per-task --mem --time
     --partition --nodes --gres --job-name --error --array --account --qos --wrap
     --dependency --mem-per-cpu --exclusive --pty --me --state --format"""),
    ("docker singularity apptainer", """--rm --volume --workdir --entrypoint --interactive
     --tty --network --user --platform --bind --nv --cleanenv --containall --pwd"""),
    ("rsync scp", "--archive --compress --delete --progress --exclude --include --partial "
                  "--human-readable --checksum --update --times"),
    ("diff cmp", "--unified --brief --side-by-side --ignore-all-space --new-file"),
    ("md5sum sha1sum sha256sum shasum", "--check --algorithm --status --tag --binary"),
    ("du df", "--human-readable --summarize --max-depth --total --block-size"),
    ("make cmake", "--keep-going --always-make --build --target --parallel --install"),
    ("date", "--utc --date --reference"),
    ("realpath readlink basename dirname stat file", """--canonicalize
     --canonicalize-existing --relative-to --relative-base --suffix --multiple --zero
     --printf --mime-type --brief --dereference"""),
    ("tee", "--append --ignore-interrupts"),
    ("which type command", "--skip-alias --read-alias"),
    ("samtools bcftools bedtools", "--threads --output-fmt --reference --write-index"),
    ("nextflow", "--outdir --input --genome --max-cpus --max-memory"),
) for name in names.split()}
# Interpreters: the long options of the run's own source (a script or an
# inline body) may be lent to the words such a command receives.
_COMMIT_SOURCE_RECEIVERS = frozenset({
    "python", "python2", "python3", "pypy", "pypy3", "ipython", "Rscript", "R",
    "bash", "sh", "zsh", "dash", "ksh", "snakemake",
})
# A whole `--name` token in source text (a quote, space or `=` around it).
_SOURCE_OPT_RX = re.compile(r"(?<![A-Za-z0-9_-])(--[a-z][a-z0-9-]{0,40})(?![A-Za-z0-9_-])")
_LONG_OPT_RX = re.compile(r"--[a-z][a-z0-9-]{0,40}\Z")
_LONG_OPT_EQ_RX = re.compile(r"(--[a-z][a-z0-9-]{0,40})=")
_SHORT_OPT_RX = re.compile(r"-(?:[A-Za-z]{1,2}|[a-z]{3}|[A-Za-z][0-9]{1,2})\Z")
_NAME_SHAPE_RX = re.compile(r"(?:[a-z_][a-z0-9_.-]{0,40}|[A-Z_][A-Z0-9_.-]{0,40})\Z")
_ASSIGN_RX = re.compile(r"([A-Za-z_][A-Za-z0-9_.-]{0,63})=")
_NUMBER_RX = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?\Z")
# Numeric one-time codes are secrets too, though no denylist name covers them.
_EXTRA_SECRET_RX = re.compile(r"(?i)(?:^|[-_])(?:pin|otp|totp|mfa|passcode|2fa)(?:$|[-_])")
_SHELL_KEYWORDS = frozenset({
    "if", "then", "else", "elif", "fi", "do", "done", "while", "until", "for",
    "case", "esac", "function", "{", "}", "!", "[[", "]]", "select", "coproc",
})
_WRAPPERS = frozenset({"env", "nohup", "time", "exec", "command", "builtin", "nice",
                       "sudo", "timeout", "xargs", "srun"})
_HEREDOC_RX = re.compile(r"<<(?!<)(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([^\s;&|<>()'\"]+))")
# Placeholders already in the text (`<redacted>` from the denylist, `<v>`)
# are values; they are hidden from the word scanner, which splits on `<>`.
_HIDDEN = "\x01"
# An unquoted comment, while the shell scanner runs.
_COMMENT = "\x02"


def _commit_secret_name(name: str) -> bool:
    return is_secret_name(name) or bool(_EXTRA_SECRET_RX.search(name))


def _digits(text: str) -> int:
    return sum(c.isdigit() for c in text)


def _option_name_ok(name: str) -> bool:
    return bool(_LONG_OPT_RX.match(name) or _SHORT_OPT_RX.match(name)) and _digits(name) <= 3


def commit_source_options(source: Any) -> set[str]:
    """The long option names (`--min-count`) the run's own source text
    names as whole tokens: evidence that such a word is one of the script's
    options. Only names of the option shape are returned."""
    if not isinstance(source, str):
        return set()
    return {name for name in _SOURCE_OPT_RX.findall(source) if _option_name_ok(name)}


def _prog_key(base: str) -> str:
    """`python3.11` and `python3` share the options of `python`."""
    if _COMMIT_VERSIONED_RX.match(base) and not base.startswith("pip"):
        return "python"
    return base


def _long_option_ok(name: str, state: "_CommitState") -> bool:
    """A long option name is kept on evidence of its role: a common option,
    a known option of the command receiving it, or (in words an interpreter
    or the run's own script receives) a name the run's source spells."""
    if not _option_name_ok(name) or not name.startswith("--"):
        return False
    if name in _COMMIT_COMMON_OPTIONS:
        return True
    if state.prog is not None and name in _COMMIT_COMMAND_OPTIONS.get(state.prog, ()):
        return True
    return state.receiver and name in state.known


def _option_ok(word: str, state: "_CommitState") -> bool:
    """An option word: a long option on evidence, a short one by shape."""
    if word.startswith("--"):
        return _long_option_ok(word, state)
    return bool(_SHORT_OPT_RX.match(word)) and _digits(word) <= 3


def _name_ok(name: str) -> bool:
    return bool(_NAME_SHAPE_RX.match(name)) and _digits(name) <= 3


def _delim_ok(word: str) -> bool:
    return word.upper() in _COMMIT_DELIMITERS


def _is_command(word: str) -> bool:
    return word in _COMMIT_COMMANDS or bool(_COMMIT_VERSIONED_RX.match(word))


def commit_keep_paths(paths: Any) -> set[str]:
    """Every spelling of the record's own evidence-backed paths a command
    may use: the path exactly as recorded, and each run of its whole
    components (`/r/analysis/de/run.py` gives `analysis/de/run.py`,
    `de/run.py`, `run.py`, `analysis/de`, ...). Runs never contain an empty,
    `.` or `..` component, and nothing is normalized: every entry is a
    substring of a path the record already holds. The caller passes only
    paths with file evidence (a digest taken or an observed change)."""
    out: set[str] = set()
    for path in paths or ():
        if not isinstance(path, str) or not path or "\n" in path or _HIDDEN in path \
                or REDACTED in path or COMMIT_VALUE in path:
            continue
        out.add(path)
        parts = path.split("/")
        if len(parts) > 64:
            continue
        absolute = parts[0] == "" and len(parts) > 1
        for i in range(len(parts)):
            for j in range(i + 1, len(parts) + 1):
                run = parts[i:j]
                if any(p in ("", ".", "..") for p in run):
                    continue
                joined = "/".join(run)
                out.add(joined)
                if i == 1 and absolute:
                    out.add("/" + joined)
    out.discard("")
    return out


def _keep_match(value: str, keep_paths: set[str]) -> bool:
    """Exactly a keep path, or `./` and exactly a keep path."""
    if not value:
        return False
    return value in keep_paths or (value.startswith("./") and value[2:] in keep_paths)


def _is_number(value: str) -> bool:
    return bool(_NUMBER_RX.match(value)) and len(value.lstrip("-")) <= 6


def commit_safe_value(value: str, keep_paths: set[str], secret: bool = False) -> str:
    """One decoded value under the allowlist: exactly a record path, or a
    short number, is kept; anything else (and any secret-named value) is
    `<v>`."""
    if secret or not isinstance(value, str) or _HIDDEN in value or _COMMENT in value \
            or REDACTED in value or COMMIT_VALUE in value:
        return COMMIT_VALUE
    if _keep_match(value, keep_paths):
        return shlex.quote(value)
    if _is_number(value) or value in _COMMIT_FIXED_VALUES:
        return value
    return COMMIT_VALUE


def _commit_command(dec: str, keep_paths: set[str]) -> tuple[str, bool]:
    """A command word: (its committed text, whether it is a vocabulary
    command)."""
    if _HIDDEN in dec or _COMMENT in dec:
        return COMMIT_VALUE, False
    if _is_command(dec):
        return dec, True
    if _keep_match(dec, keep_paths):
        return shlex.quote(dec), False
    head, slash, base = dec.rpartition("/")
    if slash and _is_command(base):
        return (dec if head in _COMMIT_SYSTEM_DIRS else f"{COMMIT_VALUE}/{base}"), True
    return COMMIT_VALUE, False


class _CommitState:
    __slots__ = ("cmd", "wrapper", "secret_next", "loop_name", "operands", "sub",
                 "prog", "receiver", "known")

    def __init__(self, cmd: bool, known: frozenset[str] | set[str] = frozenset()) -> None:
        self.cmd = cmd
        self.wrapper = False
        self.secret_next = False
        self.loop_name = False
        self.operands = False  # after `--`: every word is an operand
        self.sub = False  # the next operand may be a subcommand
        # The vocabulary command whose options the next words may be.
        self.prog: str | None = None
        # Whether the words go to an interpreter or the run's own script, so
        # that the source's option names (``known``) may be lent to them. A
        # list of operands only (`script_args`) is the script's own.
        self.receiver = not cmd
        self.known = known


def _commit_assignment(dec: str, match: re.Match, keep_paths: set[str]) -> str:
    name = match.group(1)
    if not _name_ok(name):
        return COMMIT_VALUE
    return match.group(0) + commit_safe_value(
        dec[match.end():], keep_paths, _commit_secret_name(name))


def _set_prog(state: _CommitState, dec: str, command: bool, word: str) -> None:
    """Record which command the next words go to: a vocabulary command (its
    known options), the run's own script named by an evidence path (the
    source's options), or an unknown word (common options only)."""
    if command:
        base = _prog_key(dec.rpartition("/")[2])
        state.prog = base
        state.receiver = base in _COMMIT_SOURCE_RECEIVERS
    else:
        state.prog = None
        state.receiver = word != COMMIT_VALUE


def _commit_word(raw: str, dec: str, keep_paths: set[str], state: _CommitState) -> str:
    """One word (as written, and decoded) under the allowlist."""
    if state.secret_next:
        state.secret_next = False
        state.cmd = state.wrapper = state.sub = False
        return COMMIT_VALUE
    if state.operands:
        return commit_safe_value(dec, keep_paths)
    assign = _ASSIGN_RX.match(dec)
    if state.cmd:
        if assign:
            # Leading assignments leave the command word still to come.
            return _commit_assignment(dec, assign, keep_paths)
        if raw == dec and dec in ("for", "select", "case"):
            # `for NAME in WORDS`: a variable name, then values; `case WORD
            # in PATTERN)`: a value, then patterns (values). Not commands.
            state.cmd = state.wrapper = False
            state.loop_name = dec != "case"
            return dec
        if raw == dec and dec in _SHELL_KEYWORDS:
            return dec
        if state.wrapper and (_option_ok(dec, state) or _is_number(dec)):
            return dec
        wrapped = state.wrapper
        state.cmd = state.wrapper = False
        word, command = _commit_command(dec, keep_paths)
        if wrapped and word == COMMIT_VALUE:
            # An unknown word after a wrapper (`env -u NAME cmd`, `sudo -u
            # USER cmd`) may be an option value: the command may still come,
            # and it is still judged as a command word.
            state.cmd = state.wrapper = True
            return word
        _set_prog(state, dec, command, word)
        if command:
            if dec.rpartition("/")[2] in _WRAPPERS:
                state.cmd = state.wrapper = True
            else:
                state.sub = True
        return word
    if state.loop_name:
        state.loop_name = False
        if raw == dec and _name_ok(dec) and "." not in dec and "-" not in dec:
            return dec
        return COMMIT_VALUE
    if raw == dec == "--":
        state.operands = True
        state.sub = False
        return dec
    if raw == dec and dec in _COMMIT_OPERAND_WORDS:
        return dec
    if dec.startswith("-") and not _is_number(dec):
        long_eq = _LONG_OPT_EQ_RX.match(dec)
        if long_eq and _long_option_ok(long_eq.group(1), state):
            return long_eq.group(0) + commit_safe_value(
                dec[long_eq.end():], keep_paths, _commit_secret_name(long_eq.group(1)))
        if "=" not in dec and _commit_secret_name(dec) and not _NO_VALUE_FLAG_RX.search(dec):
            state.secret_next = True
        if _option_ok(dec, state):
            return dec
        return commit_safe_value(dec, keep_paths)
    if state.sub:
        state.sub = False
        if raw == dec and (dec in _COMMIT_SUBCOMMANDS or _is_command(dec)):
            state.sub = dec in _COMMIT_CHAIN or _is_command(dec)
            if _is_command(dec):
                # `uv run python`, `python3 -m pytest`: the options that
                # follow are that command's.
                _set_prog(state, dec, True, dec)
            return dec
    if _is_number(dec) or _keep_match(dec, keep_paths) or _HIDDEN in dec:
        return commit_safe_value(dec, keep_paths)
    if assign:
        return _commit_assignment(dec, assign, keep_paths)
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


def _commit_shell(text: str, keep_paths: set[str],
                  known: frozenset[str] | set[str] = frozenset()) -> str:
    out: list[str] = []
    state = _CommitState(cmd=True, known=known)
    pos = 0
    for start, end in _words(text):
        gap = text[pos:start]
        out.append(gap)
        sep, redir, heredoc = _gap_kind(gap)
        if sep:
            state = _CommitState(cmd=True, known=known)
        raw = text[start:end]
        pos = end
        if raw == _COMMENT:
            out.append(raw)
            continue
        dec = _decode(raw)
        if heredoc:
            plain = _delim_ok(dec) and raw in (dec, f"'{dec}'", f'"{dec}"', "\\" + dec)
            out.append(raw if plain else COMMIT_VALUE)
        elif redir:
            out.append(commit_safe_value(dec, keep_paths))
        else:
            out.append(_commit_word(raw, dec, keep_paths, state))
    out.append(text[pos:])
    return "".join(out)


def _strip_comment(line: str, quote: str | None) -> tuple[str, str | None]:
    """The line with an unquoted comment (a `#` that starts a word, through
    the end of the line) replaced by one marker, and the quote still open
    at its end. Over-detection only hides more text."""
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if quote is None:
            if c == "\\":
                i += 2
                continue
            if c in "'\"":
                quote = c
            elif c == "#" and (i == 0 or line[i - 1] in " \t;&|()<>"):
                end = line.find("\n", i)
                return line[:i] + _COMMENT + (line[end:] if end >= 0 else ""), None
        elif quote == "'":
            if c == "'":
                quote = None
        else:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                quote = None
        i += 1
    return line, quote


def _hide_placeholders(text: str) -> str:
    return text.replace(REDACTED, _HIDDEN).replace(COMMIT_VALUE, _HIDDEN).replace(
        _COMMENT, _HIDDEN)


def _show_placeholders(text: str) -> str:
    return text.replace(_HIDDEN, COMMIT_VALUE).replace(_COMMENT, COMMIT_COMMENT)


def commit_safe_command(text: Any, keep_paths: set[str] | None = None,
                        known_options: frozenset[str] | set[str] | None = None) -> Any:
    """Command text for a committed record: only allowlisted words survive
    (see the block comment above); separators and layout are kept, each
    unquoted comment becomes `<comment>`, and each heredoc body collapses to
    `<heredoc: N lines>`. ``known_options`` are the long option names the
    run's own source spells (commit_source_options): kept in the words an
    interpreter or that script receives."""
    if not isinstance(text, str) or not text:
        return text
    keep = keep_paths or set()
    known = known_options or frozenset()
    text = _hide_placeholders(text)
    out: list[str] = []
    shell: list[str] = []
    pending: list[tuple[str, bool]] = []  # (delimiter, strip leading tabs)
    body = 0
    quote: str | None = None

    def flush() -> None:
        if shell:
            out.append(_commit_shell("".join(shell), keep, known))
            shell.clear()

    def delim_text(word: str) -> str:
        return word if _delim_ok(word) else COMMIT_VALUE

    # Shell lines end only at `\n` (not at `\r`, `\x85`, ` `, which
    # str.splitlines also splits at): a comment runs to the real line end.
    for line in (piece for piece in re.split(r"(?<=\n)", text) if piece):
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
        line, quote = _strip_comment(line, quote)
        shell.append(line)
        for match in _HEREDOC_RX.finditer(line):
            word = next(g for g in match.groups()[1:] if g is not None)
            pending.append((word, bool(match.group(1))))
        if pending:
            flush()
            quote = None
            if not line.endswith("\n"):
                out.append("\n")
    flush()
    if pending:
        out.append(f"<heredoc: {body} lines>")
    return _show_placeholders("".join(out))


def commit_safe_argv(argv: Any, keep_paths: set[str] | None = None,
                     command_word: bool = True,
                     known_options: frozenset[str] | set[str] | None = None) -> Any:
    """An argv list for a committed record, word by word under the same
    allowlist. ``command_word`` says whether the list starts with a command
    (a full argv, wrappers) or is only operands (`script_args`, `params`:
    the script's own, so ``known_options`` apply to them).
    Non-string tokens (an inline-body placeholder) pass through."""
    if not isinstance(argv, list):
        return argv
    keep = keep_paths or set()
    state = _CommitState(cmd=command_word, known=known_options or frozenset())
    out: list[Any] = []
    for token in argv:
        if not isinstance(token, str):
            out.append(token)
            state.secret_next = False
            state.cmd = state.wrapper = state.sub = False
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
                or (_HIDDEN not in core and _keep_match(core, keep))):
            out.append(piece)
        elif _HIDDEN in core and core.strip(_HIDDEN) == "":
            out.append(piece)
            word = False
        else:
            out.append(f"{lead}{COMMIT_VALUE}{trail}")
        prev_word = word
    return _show_placeholders("".join(out))
