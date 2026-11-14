"""Scan-time regressions for extract_data_lineage_event.py.

The extractor runs in a lifecycle hook on every shell command, with no hook
timeout, so super-linear scanning stalls the agent. Each timing test below
took between several seconds and about a minute before the linear-scan fix;
the bounds are generous so that only a quadratic regression trips them. The
equivalence tests pin the fast paths to the plain computations they replace.
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

TIME_LIMIT_SECONDS = 5.0

SCANNED_PATTERNS = lineage_event.ANALYSIS_PATTERNS + lineage_event.RUNNER_PATTERNS


def _timed(function, *args):
    started = time.monotonic()
    result = function(*args)
    return result, time.monotonic() - started


def _detect_proven(command: str, cwd: Path, bash_exit: int | None):
    return lineage_event._detect_scripts_with_cwd(
        command, cwd, bash_exit=bash_exit, require_execution_evidence=True
    )


# ---------- timing ----------


def test_r_args_followed_by_many_quoted_words_scans_linearly(tmp_path: Path) -> None:
    """5,000 `"$x"` words after `R -f a.R --args` took ~58 s in detection."""
    (tmp_path / "a.R").write_text("print(1)\n")
    command = "R -f a.R --args " + " ".join(['"$x"'] * 5000)
    analysis, analysis_seconds = _timed(is_analysis, command)
    detections, detect_seconds = _timed(detect_scripts, command, tmp_path)
    assert analysis is True
    assert detections == [(tmp_path / "a.R", None)]
    assert analysis_seconds < TIME_LIMIT_SECONDS
    assert detect_seconds < TIME_LIMIT_SECONDS


@pytest.mark.parametrize(
    "command",
    [
        "echo " + " ".join(['"a"'] * 4000),
        "echo " + " ".join(["'a'"] * 4000),
        "echo " + '`"' * 4000,
        "R " + '\\"' * 4000 + "x",
        'R "' + '\\"' * 4000 + 'x"',
        "python " + "a" * 20000,
    ],
)
def test_quote_and_escape_runs_scan_linearly(tmp_path: Path, command: str) -> None:
    analysis, analysis_seconds = _timed(is_analysis, command)
    detections, detect_seconds = _timed(detect_scripts, command, tmp_path)
    assert analysis is False
    assert detections == []
    assert analysis_seconds < TIME_LIMIT_SECONDS
    assert detect_seconds < TIME_LIMIT_SECONDS


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
    command = "\n".join([line] * 2000)
    detections, seconds = _timed(_detect_proven, command, tmp_path, 0)
    assert [(path, source) for path, source, _ in detections] == [expected]
    assert seconds < TIME_LIMIT_SECONDS


@pytest.mark.parametrize(
    "joiner,bash_exit",
    [(" && ", 0), (" && ", 1), (" || ", 1), (" || ", 0), ("; ", 0), (" | ", 0)],
)
def test_long_and_or_chains_do_not_rescan(
    tmp_path: Path, joiner: str, bash_exit: int
) -> None:
    """The AND/OR proofs and the after-candidate scan were linear per match."""
    (tmp_path / "a.py").write_text("print(1)\n")
    command = joiner.join(["python a.py"] * 1500)
    _, seconds = _timed(_detect_proven, command, tmp_path, bash_exit)
    assert seconds < TIME_LIMIT_SECONDS


def test_or_fallback_chain_is_still_proven(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("print(1)\n")
    command = "false || " + " || ".join(["python a.py"] * 1500)
    detections, seconds = _timed(_detect_proven, command, tmp_path, 0)
    assert [path for path, _, _ in detections] == [tmp_path / "a.py"]
    assert seconds < TIME_LIMIT_SECONDS


def test_many_runner_lines_scan_linearly(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text("echo hi\n")
    command = "\n".join(["bash run.sh"] * 2000)
    detections, seconds = _timed(_detect_proven, command, tmp_path, 0)
    assert [path for path, _, _ in detections] == [tmp_path / "run.sh"]
    assert seconds < TIME_LIMIT_SECONDS


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
    """Checkpoint states must share stacks rather than copy them per cut."""
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
