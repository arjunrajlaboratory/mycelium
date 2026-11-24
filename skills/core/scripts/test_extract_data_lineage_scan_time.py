"""Scan-time regressions for extract_data_lineage_event.py.

The extractor runs in a lifecycle hook on every shell command, with no hook
timeout, so super-linear scanning stalls the agent. Each timing test below
took between several seconds and about a minute before the linear-scan fix.
They assert how the scan scales (CPU at size n against n/2), not how long it
takes: an absolute CPU bound failed on loaded CI runners. The equivalence
tests pin the fast paths to the plain computations they replace.
"""

from __future__ import annotations

import random
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import extract_data_lineage_event as lineage_event  # noqa: E402
from extract_data_lineage_event import detect_scripts, is_analysis  # noqa: E402

# See test_lineage_data_commands.py: linear growth is about 2x per doubling,
# the regressions these tests caught were 4x to exponential; below the floor
# a scan is too fast to judge. The only absolute bound is a sanity limit.
SCALING_RATIO_LIMIT = 3.2
SCALING_FLOOR_SECONDS = 0.5
SANITY_CPU_SECONDS = 60.0

SCANNED_PATTERNS = lineage_event.ANALYSIS_PATTERNS + lineage_event.RUNNER_PATTERNS


def _timed(function, *args):
    started = time.process_time()
    result = function(*args)
    return result, time.process_time() - started


def _assert_linear(function, build, n: int, *args) -> None:
    """CPU for ``function(build(n), *args)`` grows about linearly from n/2."""
    small_input, large_input = build(n // 2), build(n)
    smalls, larges = [], []
    for _ in range(3):  # interleaved, so a load spike cannot hit one side only
        smalls.append(_timed(function, small_input, *args)[1])
        larges.append(_timed(function, large_input, *args)[1])
    small, large = min(smalls), min(larges)
    assert large < SANITY_CPU_SECONDS, large
    assert large < max(small * SCALING_RATIO_LIMIT, SCALING_FLOOR_SECONDS), (small, large)


def _detect_proven(command: str, cwd: Path, bash_exit: int | None):
    return lineage_event._detect_scripts_with_cwd(
        command, cwd, bash_exit=bash_exit, require_execution_evidence=True
    )


# ---------- timing ----------


def test_r_args_followed_by_many_quoted_words_scans_linearly(tmp_path: Path) -> None:
    """5,000 `"$x"` words after `R -f a.R --args` took ~58 s in detection."""
    (tmp_path / "a.R").write_text("print(1)\n")
    def build(n: int) -> str:
        return "R -f a.R --args " + " ".join(['"$x"'] * n)

    assert is_analysis(build(5000)) is True
    assert detect_scripts(build(5000), tmp_path) == [(tmp_path / "a.R", None)]
    _assert_linear(is_analysis, build, 5000)
    _assert_linear(detect_scripts, build, 5000, tmp_path)


@pytest.mark.parametrize(
    "build",
    [
        lambda n: "echo " + " ".join(['"a"'] * n),
        lambda n: "echo " + " ".join(["'a'"] * n),
        lambda n: "echo " + '`"' * n,
        lambda n: "R " + '\\"' * n + "x",
        lambda n: 'R "' + '\\"' * n + 'x"',
    ],
)
def test_quote_and_escape_runs_scan_linearly(tmp_path: Path, build) -> None:
    assert is_analysis(build(4000)) is False
    assert detect_scripts(build(4000), tmp_path) == []
    _assert_linear(is_analysis, build, 4000)
    _assert_linear(detect_scripts, build, 4000, tmp_path)


@pytest.mark.parametrize(
    "line,expected",
    [
        ("R -q -e 'x <- 1'", (None, "x <- 1")),
        ("python -c 'x = 1'", (None, "x = 1")),
    ],
)
def test_many_command_lines_do_not_rescan_their_prefix(
    tmp_path: Path, line: str, expected: tuple
) -> None:
    """Each match re-tokenized the whole prefix: 800 R lines took seconds."""
    def build(n: int) -> str:
        return "\n".join([line] * n)

    detections = _detect_proven(build(2000), tmp_path, 0)
    assert [(path, source) for path, source, _ in detections] == [expected]
    _assert_linear(_detect_proven, build, 2000, tmp_path, 0)


@pytest.mark.parametrize(
    "joiner,bash_exit",
    [(" && ", 0), (" && ", 1), (" || ", 1), (" || ", 0), ("; ", 0), (" | ", 0)],
)
def test_long_and_or_chains_do_not_rescan(
    tmp_path: Path, joiner: str, bash_exit: int
) -> None:
    """The AND/OR proofs and the after-candidate scan were linear per match."""
    (tmp_path / "a.py").write_text("print(1)\n")
    _assert_linear(_detect_proven, lambda n: joiner.join(["python a.py"] * n), 1500,
                   tmp_path, bash_exit)


def test_or_fallback_chain_is_still_proven(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)\n")
    def build(n: int) -> str:
        return "false || " + " || ".join(["python a.py"] * n)

    detections = _detect_proven(build(1500), tmp_path, 0)
    assert [path for path, _, _ in detections] == [tmp_path / "a.py"]
    _assert_linear(_detect_proven, build, 1500, tmp_path, 0)


@pytest.mark.parametrize("subcommand", ["nbconvert", "execute"])
def test_jupyter_value_options_do_not_backtrack_exponentially(
    tmp_path: Path, subcommand: str
) -> None:
    """`--output=x` matched two option forms, doubling the work per option.

    16 options with a long tail took about 31 s before the fix.
    """
    def build(options: int) -> str:
        return f"jupyter {subcommand} " + "--output=x " * options + " " * 10000

    assert is_analysis(build(16)) is False
    assert detect_scripts(build(16), tmp_path) == []
    # Doubling should cost about nothing; the exponential form was 2^8 more.
    _assert_linear(is_analysis, build, 16)
    _assert_linear(detect_scripts, build, 16, tmp_path)


def test_jupyter_value_options_still_find_the_notebook(tmp_path: Path) -> None:
    (tmp_path / "n.ipynb").write_text("{}\n")
    command = (
        "jupyter nbconvert --output=x --to html --Exporter.x=1 "
        "--execute n.ipynb"
    )
    assert is_analysis(command) is True
    assert detect_scripts(command, tmp_path) == [(tmp_path / "n.ipynb", None)]


def test_r_cmd_batch_escaped_blanks_scan_linearly(tmp_path: Path) -> None:
    """Each `a\\ ` step re-tried the path over the rest of the word: ~10 s."""
    (tmp_path / "a.R").write_text("print(1)\n")
    def build(n: int) -> str:
        return "R CMD BATCH " + "a\\ " * n + " a.R"

    _assert_linear(is_analysis, build, 13300)
    _assert_linear(detect_scripts, build, 13300, tmp_path)


@pytest.mark.parametrize(
    "command,path",
    [
        ("R CMD BATCH a\\ b.R", "a\\ b.R"),
        ("R CMD BATCH x\\ y\\  b.R", "b.R"),
        ('R CMD BATCH "a b.R', "b.R"),
        ('R CMD BATCH "a\\ b" c.R', "c.R"),
        ("R CMD BATCH a\\ (b\\ c.R", "c.R"),
        ("R CMD BATCH --vanilla a\\\\ b.R out", "b.R"),
    ],
)
def test_r_cmd_batch_skips_only_continued_word_starts(command: str, path: str) -> None:
    """Skipping attempts inside an escaped-blank word keeps every match."""
    match = lineage_event.RX_R_CMD_BATCH_PATH.search(command)
    assert match is not None and match.group("path") == path


def test_many_runner_lines_scan_linearly(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text("echo hi\n")
    def build(n: int) -> str:
        return "\n".join(["bash run.sh"] * n)

    detections = _detect_proven(build(2000), tmp_path, 0)
    assert [path for path, _, _ in detections] == [tmp_path / "run.sh"]
    _assert_linear(_detect_proven, build, 2000, tmp_path, 0)


# ---------- equivalence of the fast paths ----------

_ATOMS = [
    '"', "'", "\\", " ", "\n", "\t", "a", "$", "`", ";", "&&", "||", "|", "&",
    "(", ")", ">", "<", "#", "=", "-", ".", "python", "python3", "R", "Rscript",
    "jupyter", "bash", "sh", "run.sh", " -e ", " -c ", " -m ", " -f ", "--file=",
    " CMD BATCH ", "nbconvert ", "execute ", "a.py", "a.R", "n.ipynb", "--args",
    " -q ", "-W", '"a b"', "'a b'", '\\"', "\\\\", "\\\n", "cd sub", "true",
    "false", "$(", "snakemake", "sbatch", "nextflow run ", "--", "-g ",
]


def _random_commands(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    return [
        "".join(rng.choice(_ATOMS) for _ in range(rng.randint(1, 24)))
        for _ in range(count)
    ]


def test_scanned_patterns_have_the_shape_the_pruning_assumes() -> None:
    """The pruning proof needs the lookbehind, then the shell word, first."""
    for pattern in SCANNED_PATTERNS:
        assert pattern.pattern.startswith(lineage_event._WORD_LOOKBEHIND)
    lookbehind_class = re.compile(r"[A-Za-z0-9_.-]")
    for code in range(0x3000):
        char = chr(code)
        assert (lookbehind_class.fullmatch(char) is not None) == (
            char in lineage_event._LOOKBEHIND_BLOCKED
        ), repr(char)


def test_pruned_scan_yields_exactly_what_finditer_yields() -> None:
    for command in _random_commands(seed=1, count=3000):
        for pattern in SCANNED_PATTERNS:
            expected = [
                (m.start(), m.end(), m.groupdict()) for m in pattern.finditer(command)
            ]
            actual = [
                (m.start(), m.end(), m.groupdict())
                for m in lineage_event._scan_word_pattern(pattern, command)
            ]
            assert actual == expected, (command, pattern.pattern[:60])


def test_prefix_folds_match_whole_prefix_tokenization(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    for command in _random_commands(seed=2, count=600):
        cuts = lineage_event._shell_token_cuts(command)
        for offset in range(len(command) + 1):
            prefix = command[:offset]
            tokens = lineage_event._shell_tokens(prefix)
            last_cut = max((cut for cut in cuts if cut <= offset), default=0)
            head = lineage_event._shell_tokens(command[:last_cut])
            tail = lineage_event._shell_tokens(command[last_cut:offset])
            assert tokens == (None if tail is None else head + tail), (command, offset)
            assert lineage_event._command_prefix_before(
                command, offset
            ) == lineage_event._current_command_prefix(prefix)
            assert lineage_event._control_context_before(
                command, offset
            ) == lineage_event._top_level_control_context(prefix)
            for name, fold, proves, initial in (
                (
                    "and",
                    lineage_event._fold_and_prefix,
                    lineage_event._and_fold_proves,
                    lineage_event._AndFoldState(False, False, (), tmp_path),
                ),
                (
                    "or",
                    lineage_event._fold_or_prefix,
                    lineage_event._or_fold_proves,
                    lineage_event._OrFoldState(
                        False, tmp_path, (), 0, True, False, True
                    ),
                ),
            ):
                cached = lineage_event._prefix_folds(command).fold(
                    offset, (name, tmp_path), lambda: initial, fold
                )
                direct = None if tokens is None else fold(initial, tokens)
                assert proves(cached) == proves(direct), (command, offset, name)


def test_list_operators_shortcut_matches_the_suffix_scan() -> None:
    answered = 0
    for command in _random_commands(seed=3, count=600):
        folds = lineage_event._prefix_folds(command)
        for offset in range(len(command) + 1):
            if lineage_event._command_prefix_before(command, offset) is None:
                continue  # the shortcut is only consulted once this holds
            fast = folds.list_operators_after(offset)
            if fast is lineage_event._UNANSWERED:
                continue
            answered += 1
            ops, later, ambiguous = lineage_event._top_level_control_context(
                command[offset:]
            )
            expected = None if later or ambiguous else set(ops)
            assert (None if fast is None else set(fast)) == expected, (command, offset)
    assert answered > 1000


# ---------- memory ----------


@pytest.mark.parametrize(
    "command",
    [
        "true && " * 6000 + "python a.py",
        "true || " * 6000 + "python a.py",
        "( " * 3000 + "python a.py" + " )" * 3000,
        "(true) " * 6000 + "; python a.py",
        "$( " * 3000 + "python a.py",
    ],
)
def test_cached_checkpoints_do_not_copy_histories(
    tmp_path: Path, command: str
) -> None:
    """Checkpoint states must share stacks rather than copy them per cut.

    This guards the prefix-fold checkpoint cache this file's fixes added, not
    a regression on main: main keeps no checkpoints and peaks at 0.4-1.8 MB
    here. An earlier version of the cache copied histories per cut and
    peaked at 110-325 MB.
    """
    import tracemalloc

    (tmp_path / "a.py").write_text("print(1)\n")
    lineage_event._prefix_folds.cache_clear()
    tracemalloc.start()
    try:
        for bash_exit in (0, 1):
            _detect_proven(command, tmp_path, bash_exit)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        lineage_event._prefix_folds.cache_clear()
    # Linear state is a few MB here; per-cut copies took well over 100 MB.
    assert peak < 64 * 1024 * 1024, peak


# ---------- robustness ----------


@pytest.mark.parametrize(
    "command",
    [
        'cd -P "a\x00"; python a.py',
        "(cd -P a\x00/..) && python a.py",
        "cd ~a\x00; python a.py",
        "env --chdir=~a\x00 python a.py",
        "env -C ~\x00 python a.py",
    ],
)
def test_null_bytes_in_cd_targets_do_not_crash(tmp_path: Path, command: str) -> None:
    """os calls raise ValueError, not OSError, on an embedded NUL."""
    (tmp_path / "a.py").write_text("print(1)\n")
    assert is_analysis(command) is True
    detect_scripts(command, tmp_path)
    for bash_exit in (0, 1, None):
        _detect_proven(command, tmp_path, bash_exit)
