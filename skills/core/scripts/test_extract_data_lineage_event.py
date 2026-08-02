"""Tests for extract_data_lineage_event.py regex patterns and detection logic."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import extract_data_lineage_event as lineage_event  # noqa: E402
from extract_data_lineage_event import (  # noqa: E402
    build_event_for_detection,
    detect_script,
    detect_scripts,
    is_analysis,
    scan_source,
    write_events,
)

# ---------- is_analysis ----------


@pytest.mark.parametrize(
    "cmd,expected",
    [
        ("python analyze.py", True),
        ("python3 -c 'pd.read_parquet(\"x\")'", True),
        ("Rscript foo.R", True),
        ("R --no-save -e 'read.csv(\"x\")'", True),
        ("jupyter execute notebook.ipynb", True),
        ("conda run -n env python script.py", True),
        ("uv run python script.py", True),
        ("uv run --frozen python -c 'pd.read_csv(\"x\")'", True),
        ("poetry run python script.py", True),
        ("cd /tmp && python script.py", True),
        ("ls -la", False),
        ("pip install pandas", False),
        ("pytest tests/", False),
    ],
)
def test_is_analysis_classification(cmd: str, expected: bool) -> None:
    assert is_analysis(cmd) is expected


# ---------- detect_script ----------


def test_detect_script_path(tmp_path: Path) -> None:
    script, inline = detect_script("python analyze.py --opt", tmp_path)
    assert script == tmp_path / "analyze.py"
    assert inline is None


def test_detect_script_path_honors_preceding_absolute_cd(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis" / "spatial-coorganization"
    analysis_dir.mkdir(parents=True)
    script, inline = detect_script(
        f'cd "{analysis_dir}" && PATH="/tmp/venv/bin:$PATH" python run.py --help',
        tmp_path,
    )
    assert script == analysis_dir / "run.py"
    assert inline is None


def test_detect_scripts_tracks_relative_cd_between_invocations(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = first / "second"
    second.mkdir(parents=True)

    detections = detect_scripts(
        "cd first && python one.py ; cd second && python two.py", tmp_path
    )

    assert detections == [(first / "one.py", None), (second / "two.py", None)]


def test_detect_script_preserves_cwd_when_cd_target_is_missing(tmp_path: Path) -> None:
    script, inline = detect_script("cd missing || python a.py", tmp_path)

    assert script == tmp_path / "a.py"
    assert inline is None


@pytest.mark.parametrize(
    "command",
    [
        "false && cd sub; python a.py",
        "true || cd sub; python a.py",
    ],
)
def test_detect_script_does_not_apply_conditionally_skipped_cd(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "sub").mkdir()

    script, inline = detect_script(command, tmp_path)

    assert script == tmp_path / "a.py"
    assert inline is None


def test_detect_script_applies_cd_proven_by_executed_and_chain(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "sub"
    analysis_dir.mkdir()

    script, inline = detect_script(
        "test -d sub && cd sub && python a.py", tmp_path
    )

    assert script == analysis_dir / "a.py"
    assert inline is None


def test_detect_scripts_treats_newline_as_command_separator(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()

    detections = detect_scripts("cd analysis\npython run.py", tmp_path)

    assert detections == [(analysis_dir / "run.py", None)]


def test_detect_scripts_does_not_leak_cd_out_of_subshell(tmp_path: Path) -> None:
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()

    detections = detect_scripts(
        "(cd analysis && python nested.py); python root.py", tmp_path
    )

    assert detections == [
        (analysis_dir / "nested.py", None),
        (tmp_path / "root.py", None),
    ]


def test_detect_script_inline_python_c() -> None:
    cmd = """python -c "import pandas; pd.read_csv('x.csv')" """
    script, inline = detect_script(cmd, Path("/tmp"))
    assert script is None
    assert inline == "import pandas; pd.read_csv('x.csv')"


def test_detect_script_inline_r_e() -> None:
    cmd = """R --no-save -e "read.csv('x.csv')" """
    script, inline = detect_script(cmd, Path("/tmp"))
    assert script is None
    assert inline == "read.csv('x.csv')"


@pytest.mark.parametrize(
    "command,expected_source",
    [
        (
            r'python -c "pd.read_csv(\"data.csv\")"',
            'pd.read_csv("data.csv")',
        ),
        (
            r'R -e "read.csv(\"data.csv\")"',
            'read.csv("data.csv")',
        ),
        (
            '''python -c "pd.read_"'csv("data.csv")' ''',
            'pd.read_csv("data.csv")',
        ),
    ],
)
def test_detect_script_decodes_complete_inline_shell_word(
    command: str, expected_source: str
) -> None:
    """Inline source must obey shell quoting, escaping, and concatenation."""
    script, inline = detect_script(command, Path("/tmp"))

    assert script is None
    assert inline == expected_source
    if command.startswith("python"):
        assert scan_source(inline)[0] == ["data.csv"]


def test_detect_script_jupyter() -> None:
    script, inline = detect_script("jupyter execute nb.ipynb", Path("/tmp"))
    assert script == Path("/tmp/nb.ipynb")


@pytest.mark.parametrize(
    "command",
    [
        "jupyter nbconvert --to notebook input.ipynb",
        "jupyter nbconvert --output converted.ipynb input.ipynb",
        "jupyter nbconvert --output=converted.ipynb input.ipynb",
    ],
)
def test_detect_script_jupyter_skips_option_values(command: str) -> None:
    script, inline = detect_script(command, Path("/tmp"))

    assert script == Path("/tmp/input.ipynb")
    assert inline is None


def test_detect_script_jupyter_rejects_unknown_separated_option_arity() -> None:
    script, inline = detect_script(
        "jupyter nbconvert --Unknown.option converted.ipynb input.ipynb",
        Path("/tmp"),
    )

    assert script is None
    assert inline is None


@pytest.mark.parametrize(
    "command",
    [
        "jupyter nbconvert a.ipynb # --show-config",
        "jupyter nbconvert a.ipynb > converted.ipynb # --show-config-json",
    ],
)
def test_jupyter_terminal_option_in_unquoted_comment_is_ignored(
    tmp_path: Path, command: str
) -> None:
    """A commented terminal flag is not part of Jupyter's argv."""
    script = tmp_path / "a.ipynb"
    script.write_text("{}\n")

    detected, inline = detect_script(command, tmp_path)

    assert detected == script
    assert inline is None


@pytest.mark.parametrize(
    "command",
    [
        r"jupyter nbconvert a.ipynb --output prefix\#value",
        "jupyter nbconvert a.ipynb --output 'prefix # --show-config'",
    ],
)
def test_jupyter_hash_in_argv_is_not_treated_as_comment(
    tmp_path: Path, command: str
) -> None:
    script = tmp_path / "a.ipynb"
    script.write_text("{}\n")

    detected, inline = detect_script(command, tmp_path)

    assert detected == script
    assert inline is None


# ---------- scan_source: inputs ----------


def test_scan_inputs_parquet() -> None:
    src = "df = pd.read_parquet('data/edges.parquet')"
    inputs, _, _, _ = scan_source(src)
    assert inputs == ["data/edges.parquet"]


def test_scan_inputs_h5ad() -> None:
    src = "adata = ad.read_h5ad('atlas.h5ad')"
    inputs, _, _, _ = scan_source(src)
    assert inputs == ["atlas.h5ad"]


def test_scan_inputs_scanpy() -> None:
    src = "adata = sc.read('matrix.csv')"
    inputs, _, _, _ = scan_source(src)
    assert "matrix.csv" in inputs


def test_scan_inputs_multiple_dedupe() -> None:
    src = """
df1 = pd.read_csv('a.csv')
df2 = pd.read_csv('a.csv')
df3 = pd.read_parquet('b.parquet')
"""
    inputs, _, _, _ = scan_source(src)
    assert inputs == ["a.csv", "b.parquet"]


# ---------- scan_source: outputs ----------


def test_scan_outputs_to_csv() -> None:
    src = "df.to_csv('out.csv', index=False)"
    _, outputs, _, _ = scan_source(src)
    assert outputs == ["out.csv"]


def test_scan_outputs_savefig() -> None:
    src = "plt.savefig('figures/heatmap.png', dpi=300)"
    _, outputs, _, _ = scan_source(src)
    assert outputs == ["figures/heatmap.png"]


def test_scan_outputs_h5ad() -> None:
    src = "adata.write_h5ad('processed.h5ad')"
    _, outputs, _, _ = scan_source(src)
    assert outputs == ["processed.h5ad"]


# ---------- scan_source: filters (NEW v2 patterns) ----------


def test_filter_query() -> None:
    src = "df.query('a > 5')"
    _, _, filters, _ = scan_source(src)
    assert any(".query(" in f for f in filters)


def test_filter_boolean_mask_attribute() -> None:
    src = "subset = df[df.score > 0.5]"
    _, _, filters, _ = scan_source(src)
    assert any("df.score" in f for f in filters), (
        f"expected boolean-mask match, got: {filters}"
    )


def test_filter_boolean_mask_bracket() -> None:
    src = "subset = resolved[resolved['pmid'] != '']"
    _, _, filters, _ = scan_source(src)
    assert any("resolved['pmid']" in f or "'pmid'" in f for f in filters), filters


def test_filter_boolean_mask_negated() -> None:
    src = "subset = df[~df.dropped]"
    _, _, filters, _ = scan_source(src)
    assert any("~df.dropped" in f for f in filters), filters


def test_filter_merge() -> None:
    src = "out = a.merge(b, on='key', how='left')"
    _, _, filters, _ = scan_source(src)
    assert any(".merge(" in f for f in filters), filters


def test_filter_join() -> None:
    src = "result = df1.join(df2, on='id')"
    _, _, filters, _ = scan_source(src)
    assert any(".join(" in f for f in filters), filters


def test_filter_pd_concat() -> None:
    src = "all_df = pd.concat([df1, df2], ignore_index=True)"
    _, _, filters, _ = scan_source(src)
    assert any("pd.concat(" in f for f in filters), filters


def test_filter_loc_mask() -> None:
    src = "subset = df.loc[df.score > 0.5, 'col_a']"
    _, _, filters, _ = scan_source(src)
    assert any(".loc[" in f for f in filters), filters


def test_filter_combination_real_kg_pattern() -> None:
    # Pattern from the real captured KG event[3]
    src = """
resolved_pmids = resolved[resolved['pmid'] != ''][['doi','pmid']].copy()
merged = lookup.merge(resolved_pmids[['doi_lc','pmid_new']], on='doi_lc', how='left')
mask = (merged['pmid'] == '') & (merged['pmid_new'].notna())
merged.loc[mask, 'pmid'] = merged.loc[mask, 'pmid_new']
"""
    _, _, filters, _ = scan_source(src)
    # Must catch: the boolean-mask subset, the merge
    has_mask = any("resolved['pmid']" in f or "resolved[resolved" in f for f in filters)
    has_merge = any(".merge(" in f for f in filters)
    assert has_mask, f"missed boolean mask in {filters}"
    assert has_merge, f"missed merge in {filters}"


# ---------- scan_source: seeds ----------


def test_seed_numpy() -> None:
    _, _, _, seeds = scan_source("np.random.seed(42)")
    assert seeds == [42]


def test_seed_default_rng() -> None:
    _, _, _, seeds = scan_source("rng = np.random.default_rng(7)")
    assert seeds == [7]


def test_seeds_dedupe_sort() -> None:
    src = "np.random.seed(99); random.seed(42); torch.manual_seed(99)"
    _, _, _, seeds = scan_source(src)
    assert seeds == [42, 99]


# ---------- scan_source: no false positives ----------


def test_no_filters_in_pure_io_script() -> None:
    src = "df = pd.read_parquet('x.parquet'); df.to_csv('y.csv')"
    _, _, filters, _ = scan_source(src)
    assert filters == []


def test_assignment_not_caught_as_filter() -> None:
    # `df['col'] = value` is an assignment, not a filter — should not match
    src = "df['new_col'] = df.old_col * 2"
    _, _, filters, _ = scan_source(src)
    # Allow no matches; if any, they should not look like the assignment
    for f in filters:
        assert "=" not in f.split("[")[-1] or "==" in f or "!=" in f, (
            f"false positive on assignment: {f}"
        )


# ---------- detect_scripts (multi-script detection) ----------


def test_detect_scripts_single_path(tmp_path: Path) -> None:
    out = detect_scripts("python analyze.py", tmp_path)
    assert out == [(tmp_path / "analyze.py", None)]


def test_detect_scripts_chained_paths(tmp_path: Path) -> None:
    out = detect_scripts("python a.py && python b.py", tmp_path)
    assert out == [(tmp_path / "a.py", None), (tmp_path / "b.py", None)]


def test_detect_scripts_chained_inline_and_path(tmp_path: Path) -> None:
    cmd = """python -c "pd.read_csv('x.csv')" && python after.py"""
    out = detect_scripts(cmd, tmp_path)
    assert out[0] == (None, "pd.read_csv('x.csv')")
    assert out[1] == (tmp_path / "after.py", None)


def test_detect_scripts_dedupes_same_path(tmp_path: Path) -> None:
    out = detect_scripts("python a.py ; python a.py", tmp_path)
    assert out == [(tmp_path / "a.py", None)]


def test_detect_scripts_empty_for_non_analysis(tmp_path: Path) -> None:
    assert detect_scripts("ls -la", tmp_path) == []


# ---------- write_events (atomic append) ----------


def test_write_events_append_creates_file(tmp_path: Path) -> None:
    target = tmp_path / "events.tmp"
    write_events(['{"a":1}\n', '{"b":2}\n'], target)
    assert target.read_text() == '{"a":1}\n{"b":2}\n'


def test_write_events_append_concatenates(tmp_path: Path) -> None:
    target = tmp_path / "events.tmp"
    write_events(['{"x":1}\n'], target)
    write_events(['{"y":2}\n'], target)
    assert target.read_text() == '{"x":1}\n{"y":2}\n'


def test_write_events_no_lines_noop(tmp_path: Path) -> None:
    target = tmp_path / "events.tmp"
    write_events([], target)
    assert not target.exists()


def test_write_events_creates_parent_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "deep" / "events.tmp"
    write_events(['{"z":3}\n'], target)
    assert target.read_text() == '{"z":3}\n'


def test_dynamic_or_imported_io_is_retained_as_unresolved(tmp_path: Path) -> None:
    script = tmp_path / "run.py"
    script.write_text(
        "from pipeline import main\n\nif __name__ == '__main__':\n    main()\n"
    )
    args = argparse.Namespace(
        ts="2026-07-31T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py --help",
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []
    assert "resolve paths dynamically" in event["lineage_warnings"][0]


def test_dynamic_literal_filenames_are_recovered_from_repository(tmp_path: Path) -> None:
    """Existing uniquely named data files recover Path-composed I/O."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    raw = tmp_path / "data" / "raw" / "sample.h5ad"
    output = tmp_path / "analysis" / "demo" / "outputs" / "summary.csv"
    raw.parent.mkdir(parents=True)
    output.parent.mkdir(parents=True)
    raw.write_bytes(b"raw-data")
    output.write_text("value\n1\n")
    script = tmp_path / "analysis" / "demo" / "run.py"
    script.write_text(
        "from pathlib import Path\n"
        "import anndata as ad\n"
        "RAW = Path(__file__).parents[2] / 'data' / 'raw'\n"
        "OUT = Path(__file__).parent / 'outputs'\n"
        "SPECS = [{'filename': 'sample.h5ad'}]\n"
        "path = RAW / SPECS[0]['filename']\n"
        "adata = ad.read_h5ad(path)\n"
        "table.to_csv(OUT / 'summary.csv')\n"
    )
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python analysis/demo/run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static+repository"
    assert [item["path"] for item in event["inputs"]] == [str(raw)]
    assert [item["path"] for item in event["outputs"]] == [str(output)]
    assert event["lineage_warnings"] == []


def test_dynamic_literal_recovery_rejects_ambiguous_basenames(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for directory in (tmp_path / "data" / "raw", tmp_path / "data" / "other"):
        directory.mkdir(parents=True)
        (directory / "sample.csv").write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text("pd.read_csv(root / 'sample.csv')\n")
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []


def test_repository_recovery_ignores_unrelated_data_like_literals(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ghost = tmp_path / "data" / "ghost.csv"
    ghost.parent.mkdir()
    ghost.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text('"""ghost.csv"""\nprint("no data I/O")\n')
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []


def test_repository_recovery_does_not_follow_a_shadowed_global_assignment(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ghost = tmp_path / "data" / "ghost.csv"
    ghost.parent.mkdir()
    ghost.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text(
        "path = 'ghost.csv'\n"
        "def load(path):\n"
        "    return pd.read_csv(path)\n"
        "load(runtime_path)\n"
    )
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []


@pytest.mark.parametrize(
    "path_expression",
    [
        "ROOT / ('train.csv' if use_train else 'test.csv')",
        "ROOT / ['train.csv', 'test.csv'][selected_index]",
    ],
)
def test_repository_recovery_rejects_runtime_path_branches(
    tmp_path: Path, path_expression: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for filename in ("train.csv", "test.csv"):
        candidate = tmp_path / "data" / filename
        candidate.parent.mkdir(exist_ok=True)
        candidate.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text(
        "import pandas as pd\n"
        f"pd.read_csv({path_expression})\n"
    )
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []


@pytest.mark.parametrize(
    "source",
    [
        (
            "def read_csv(path):\n"
            "    print(path)\n"
            "read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "class Reader:\n"
            "    def read_csv(self, path):\n"
            "        print(path)\n"
            "pd = Reader()\n"
            "pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
    ],
)
def test_repository_recovery_rejects_unproven_custom_reader(
    tmp_path: Path, source: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ghost = tmp_path / "data" / "ghost.csv"
    ghost.parent.mkdir()
    ghost.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text(source)
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []


def test_repository_recovery_accepts_imported_unqualified_reader(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sample = tmp_path / "data" / "sample.csv"
    sample.parent.mkdir()
    sample.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text(
        "from pandas import read_csv\n"
        "read_csv(ROOT / 'sample.csv')\n"
    )
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static+repository"
    assert [item["path"] for item in event["inputs"]] == [str(sample)]


@pytest.mark.parametrize(
    "source,relative_path,expected_key",
    [
        (
            "frame = pd.read_csv(ROOT / 'summary.csv')\n",
            "analysis/demo/results/summary.csv",
            "inputs",
        ),
        (
            "frame.to_csv(ROOT / 'seed.csv')\n",
            "data/processed/seed.csv",
            "outputs",
        ),
    ],
)
def test_repository_recovery_takes_direction_from_io_expression(
    tmp_path: Path, source: str, relative_path: str, expected_key: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    recovered = tmp_path / relative_path
    recovered.parent.mkdir(parents=True)
    recovered.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text(source)
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static+repository"
    other_key = "outputs" if expected_key == "inputs" else "inputs"
    assert [item["path"] for item in event[expected_key]] == [str(recovered)]
    assert event[other_key] == []


def test_resolved_literal_io_does_not_search_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = tmp_path / "data" / "raw" / "sample.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text("pd.read_csv('data/raw/sample.csv')\n")
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    def unexpected_repository_query(*_args, **_kwargs):
        raise AssertionError("resolved I/O must not query repository paths")

    monkeypatch.setattr(lineage_event.subprocess, "run", unexpected_repository_query)

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static"
    assert [item["path"] for item in event["inputs"]] == [str(raw)]


def test_dynamic_repository_recovery_fails_safe_at_scan_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    raw = tmp_path / "data" / "raw" / "sample.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text("pd.read_csv(ROOT / 'sample.csv')\n")
    monkeypatch.setattr(lineage_event, "_REPOSITORY_SCAN_ENTRY_LIMIT", 1)
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []


def test_repository_recovery_does_not_duplicate_direct_static_path(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    raw = tmp_path / "data" / "raw" / "sample.csv"
    raw.parent.mkdir(parents=True)
    raw.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text("pd.read_csv('data/raw/sample.csv')\n")
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static"
    assert [item["path"] for item in event["inputs"]] == [str(raw)]


def test_repository_recovery_does_not_alias_absolute_external_literal(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    in_repo = tmp_path / "data" / "raw" / "sample.csv"
    in_repo.parent.mkdir(parents=True)
    in_repo.write_text("x\n")
    script = tmp_path / "run.py"
    script.write_text("pd.read_csv('/external/source/sample.csv')\n")
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static"
    assert [item["path"] for item in event["inputs"]] == [
        "/external/source/sample.csv"
    ]


# ---------- end-to-end main() with multi-script + --append-to ----------


def test_main_emits_two_events_for_chained_inline(tmp_path: Path) -> None:
    """python -c 'read X' && python -c 'read Y' should produce two NDJSON lines."""
    import json
    import subprocess

    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"
    cmd = (
        """python -c "pd.read_parquet('a.parquet')" """
        """&& python -c "pd.read_csv('b.csv')" """
    )
    r = subprocess.run(
        [
            "python3",
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-05-26T20:00:00Z",
            "--bash-cmd",
            cmd,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert r.returncode == 0, r.stderr
    lines = [json.loads(line) for line in target.read_text().splitlines() if line]
    assert len(lines) == 2
    scripts = [l.get("script_source") for l in lines]
    assert "pd.read_parquet('a.parquet')" in scripts[0]
    assert "pd.read_csv('b.csv')" in scripts[1]


def test_main_honors_cd_for_script_and_relative_data_paths(tmp_path: Path) -> None:
    import json
    import subprocess

    analysis_dir = tmp_path / "analysis" / "spatial-coorganization"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "run.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    (analysis_dir / "input.csv").write_text("value\n1\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"
    command = (
        f'cd "{analysis_dir}" && PYTHONDONTWRITEBYTECODE=1 '
        'PATH="/tmp/venv/bin:$PATH" python run.py --help'
    )

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-07-31T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert event["script"] == str(analysis_dir / "run.py")
    assert event["inputs"][0]["path"] == str(analysis_dir / "input.csv")


@pytest.mark.parametrize(
    "command,expected_event",
    [
        ("cd missing || python a.py", True),
        ("false || python a.py", True),
        ("cd existing || python a.py", False),
        ("true || python a.py", False),
        ("unknown-command || python a.py", False),
    ],
)
def test_main_tracks_only_proven_executed_successful_or_alternatives(
    tmp_path: Path, command: str, expected_event: bool
) -> None:
    import json
    import subprocess

    (tmp_path / "existing").mkdir()
    script = tmp_path / "a.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert target.exists() is expected_event
    if expected_event:
        assert json.loads(target.read_text())["script"] == str(script)


def test_main_resets_execution_context_after_list_separator(tmp_path: Path) -> None:
    """An earlier pipeline must not make a later unconditional list ambiguous."""
    import json
    import subprocess

    script = tmp_path / "run.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            "printf x | cat; python run.py",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert event["script"] == str(script)
    assert event["bash_exit"] is None


@pytest.mark.parametrize(
    "command",
    [
        "if false; then\ncd sub\npython a.py\nfi",
        "for item in; do\npython a.py\ndone",
        "case x in\ny) python a.py ;;\nesac",
        "analyze() {\npython a.py\n}",
        "cat <<'EOF'\npython a.py\nEOF",
        "# python a.py",
        "python #a.py",
        "true # && python a.py",
        "false # || python a.py",
        "echo python a.py",
        "uv run echo python a.py",
        "conda run echo python a.py",
        "poetry run echo python a.py",
        "time echo python a.py",
        "exec echo python a.py",
        "nice echo python a.py",
        "timeout 1s echo python a.py",
        "exec -a python echo a.py",
        "nice -n python echo a.py",
        "timeout --signal python 1s echo a.py",
        "notpython a.py",
        'echo "python" a.py',
    ],
)
def test_main_rejects_unproven_shell_text_matches(
    tmp_path: Path, command: str
) -> None:
    """Only interpreter tokens in supported, executed command positions count."""
    import subprocess

    (tmp_path / "a.py").write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.py").write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


@pytest.mark.parametrize(
    "command",
    [
        "python --help a.py",
        "python --version a.py",
        "python -h a.py",
        "python -V a.py",
        "python -uV a.py",
        "python -Vu a.py",
        "R --help -e \"read.csv('input.csv')\"",
        "R --version -e \"read.csv('input.csv')\"",
        "Rscript --help a.R",
        "Rscript --version a.R",
        "jupyter execute --help a.ipynb",
        "jupyter execute --help-all a.ipynb",
        "jupyter execute --version a.ipynb",
        "jupyter nbconvert --generate-config a.ipynb",
        "jupyter nbconvert --show-config a.ipynb",
        "jupyter nbconvert --show-config-json a.ipynb",
        "jupyter nbconvert --show-config=true a.ipynb",
        "jupyter nbconvert --debug --show-config a.ipynb",
        "jupyter nbconvert a.ipynb --show-config",
        "jupyter nbconvert a.ipynb >/dev/null --show-config",
        "jupyter nbconvert a.ipynb 2>&1 --show-config-json",
        "jupyter nbconvert a.ipynb &>/dev/null --generate-config",
        "jupyter nbconvert a.ipynb <input --show-config",
        "jupyter nbconvert a.ipynb >|output --show-config",
        "jupyter execute a.ipynb --generate-config",
    ],
)
def test_main_rejects_terminal_interpreter_options_before_analysis_payload(
    tmp_path: Path, command: str
) -> None:
    """Help/version options terminate the interpreter instead of running payload."""
    import subprocess

    (tmp_path / "a.py").write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    (tmp_path / "a.R").write_text("read.csv('input.csv')\n")
    (tmp_path / "a.ipynb").write_text("{}\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


def test_jupyter_terminal_option_text_in_a_value_is_not_a_terminal_mode(
    tmp_path: Path,
) -> None:
    """Only complete argv options terminate; text within a value does not."""
    script = tmp_path / "a.ipynb"
    script.write_text("{}\n")

    detected, inline = detect_script(
        "jupyter nbconvert --output 'prefix --show-config' a.ipynb",
        tmp_path,
    )

    assert detected == script
    assert inline is None


@pytest.mark.parametrize(
    "redirection",
    [
        "> --show-config",
        "2> --show-config-json",
        "< --generate-config",
        "&> --show-config",
        ">| --show-config",
    ],
)
def test_jupyter_terminal_option_used_as_redirection_target_is_not_argv(
    tmp_path: Path, redirection: str
) -> None:
    """A redirection filename is shell syntax, not an option passed to Jupyter."""
    script = tmp_path / "a.ipynb"
    script.write_text("{}\n")

    detected, inline = detect_script(
        f"jupyter nbconvert a.ipynb {redirection}",
        tmp_path,
    )

    assert detected == script
    assert inline is None


def test_jupyter_terminal_option_in_later_command_does_not_hide_execution(
    tmp_path: Path,
) -> None:
    """Terminal-mode checks are scoped to the matched simple command."""
    script = tmp_path / "a.ipynb"
    script.write_text("{}\n")

    detected, inline = detect_script(
        "jupyter execute a.ipynb; jupyter nbconvert --show-config",
        tmp_path,
    )

    assert detected == script
    assert inline is None


@pytest.mark.parametrize(
    "command",
    [
        'python "a.py".bak',
        'Rscript "a.R".bak',
        'jupyter execute "a.ipynb".bak',
        '"/tmp/python".bak a.py',
        "env -S 'echo prefix' python a.py",
        "env --split-string='echo prefix' python a.py",
        "env -S 'python -u' a.py",
    ],
)
def test_main_rejects_partial_shell_words_and_env_split_strings(
    tmp_path: Path, command: str
) -> None:
    """Never attribute an argv fragment or env-expanded argument as a command."""
    import subprocess

    for filename, content in (
        ("a.py", "import pandas as pd\npd.read_csv('python.csv')\n"),
        ("a.py.bak", "import pandas as pd\npd.read_csv('backup.csv')\n"),
        ("a.R", "read.csv('r.csv')\n"),
        ("a.R.bak", "read.csv('r-backup.csv')\n"),
        ("a.ipynb", "{}\n"),
        ("a.ipynb.bak", "{}\n"),
    ):
        (tmp_path / filename).write_text(content)
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


@pytest.mark.parametrize(
    "command,relative_directory",
    [
        ("env -C sub python a.py", "sub"),
        ("env --chdir=sub python a.py", "sub"),
        ("conda run --cwd sub python a.py", "sub"),
        ("uv run --directory sub python a.py", "sub"),
        ("env -C sub env -C deeper python a.py", "sub/deeper"),
    ],
)
def test_main_applies_wrapper_working_directories(
    tmp_path: Path, command: str, relative_directory: str
) -> None:
    import json
    import subprocess

    workdir = tmp_path / relative_directory
    workdir.mkdir(parents=True)
    script = workdir / "a.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert event["script"] == str(script)
    assert event["inputs"][0]["path"] == str(workdir / "input.csv")


def test_main_rejects_missing_wrapper_working_directory(tmp_path: Path) -> None:
    import subprocess

    (tmp_path / "a.py").write_text(
        "import pandas as pd\npd.read_csv('input.csv')\n"
    )
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            "env -C missing python a.py",
            "--bash-exit",
            "1",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


def test_detect_script_accepts_redirection_after_complete_shell_word(
    tmp_path: Path,
) -> None:
    script, inline = detect_script("python analysis.py>run.log", tmp_path)

    assert script == tmp_path / "analysis.py"
    assert inline is None


@pytest.mark.parametrize(
    "command,exit_code",
    [
        ("true && python a.py", 1),
        ("printf x | python a.py", 1),
        ("printf x | python a.py", 0),
    ],
)
def test_main_tracks_proven_execution_in_failed_and_and_pipeline_lists(
    tmp_path: Path, command: str, exit_code: int
) -> None:
    """A failed analysis is still provenance when its execution is provable."""
    import json
    import subprocess

    script = tmp_path / "a.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            str(exit_code),
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text())["script"] == str(script)


def test_main_does_not_infer_failed_and_branch_after_unknown_prefix(
    tmp_path: Path,
) -> None:
    import subprocess

    (tmp_path / "a.py").write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            "unknown-command && python a.py",
            "--bash-exit",
            "1",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not target.exists()


@pytest.mark.parametrize(
    "command,source_fragment",
    [
        (
            "python -c \"import pandas as pd\n"
            "if True:\n"
            "    pd.read_csv('input.csv')\n"
            "for value in [1]:\n"
            "    pass\"",
            "if True:",
        ),
        (
            "R -e 'values <- 1\n"
            "for (value in values) {\n"
            '  read.csv("input.csv")\n'
            "}'",
            "for (value in values)",
        ),
        (
            "python -c \"import pandas as pd\n"
            "text = '<<EOF'\n"
            "pd.read_csv('input.csv')\"",
            "text = '<<EOF'",
        ),
    ],
)
def test_main_accepts_shell_syntax_inside_quoted_inline_source(
    tmp_path: Path, command: str, source_fragment: str
) -> None:
    """Quoted language syntax must not be parsed as shell control flow."""
    import json
    import subprocess

    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert source_fragment in event["script_source"]


@pytest.mark.parametrize(
    "command",
    [
        "uv run --frozen python a.py",
        "conda run -n analysis python a.py",
        "poetry run python a.py",
        "time python a.py",
        "time -p python a.py",
        "exec python a.py",
        "exec -a analysis python a.py",
        "nice python a.py",
        "nice -n 5 python a.py",
        "timeout 10s python a.py",
        "timeout --signal=KILL --kill-after=2s 10s python a.py",
        "time nice -n 5 timeout 10s python a.py",
    ],
)
def test_main_accepts_supported_execution_wrappers(
    tmp_path: Path, command: str
) -> None:
    import json
    import subprocess

    script = tmp_path / "a.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text())["script"] == str(script)


@pytest.mark.parametrize(
    "command",
    [
        "python3 -u a.py",
        "python3 -W ignore a.py",
        "python3 -Wignore a.py",
        "python3 -Xdev a.py",
        "python3 -- a.py",
        "/usr/bin/python3 a.py",
        "/tmp/project/.venv/bin/python a.py",
        "/usr/bin/env python3 a.py",
    ],
)
def test_main_accepts_common_python_executable_and_flag_forms(
    tmp_path: Path, command: str
) -> None:
    """Interpreter paths and startup flags must not hide real analysis."""
    import json
    import subprocess

    script = tmp_path / "a.py"
    script.write_text("import pandas as pd\npd.read_csv('input.csv')\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text())["script"] == str(script)


@pytest.mark.parametrize(
    "command,filename,content",
    [
        (
            'python "analysis script.py"',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            "python 'analysis script.py'",
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            r"python analysis\ script.py",
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            r"python 'analysis'\ script.py",
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            'python "analysis script".py',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            'python "analysis #1.py"',
            "analysis #1.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            r"python analysis\#1.py",
            "analysis#1.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            "python analysis#1.py",
            "analysis#1.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            'uv run python "analysis script.py"',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            '"/tmp/venv with space/bin/python" "analysis script.py"',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            r"/tmp/venv\ with\ space/bin/python analysis\ script.py",
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            '"/tmp/venv with space"/bin/python "analysis script".py',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            'python -W "ignore: message" "analysis script.py"',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            'python -W "ignore: "message "analysis script".py',
            "analysis script.py",
            "import pandas as pd\npd.read_csv('input.csv')\n",
        ),
        (
            'Rscript "analysis script.R"',
            "analysis script.R",
            "read.csv('input.csv')\n",
        ),
        (
            r"Rscript analysis\ script.R",
            "analysis script.R",
            "read.csv('input.csv')\n",
        ),
        (
            'Rscript "analysis script".R',
            "analysis script.R",
            "read.csv('input.csv')\n",
        ),
        (
            'nice "/tmp/R env"/bin/Rscript "analysis script".R',
            "analysis script.R",
            "read.csv('input.csv')\n",
        ),
        (
            '"/tmp/R env/Rscript" "analysis script.R"',
            "analysis script.R",
            "read.csv('input.csv')\n",
        ),
        (
            'jupyter execute "analysis notebook.ipynb"',
            "analysis notebook.ipynb",
            "{}\n",
        ),
        (
            r"jupyter execute analysis\ notebook.ipynb",
            "analysis notebook.ipynb",
            "{}\n",
        ),
        (
            'jupyter execute "analysis notebook".ipynb',
            "analysis notebook.ipynb",
            "{}\n",
        ),
        (
            'exec timeout 10s "/tmp/Jupyter env"/bin/jupyter execute '
            '"analysis notebook".ipynb',
            "analysis notebook.ipynb",
            "{}\n",
        ),
        (
            '"/tmp/Jupyter env/jupyter" execute "analysis; Ω notebook.ipynb"',
            "analysis; Ω notebook.ipynb",
            "{}\n",
        ),
    ],
)
def test_main_accepts_shell_encoded_whitespace_in_script_paths(
    tmp_path: Path, command: str, filename: str, content: str
) -> None:
    """Quoted and escaped spaces must survive script-path extraction."""
    import json
    import subprocess

    script = tmp_path / filename
    script.write_text(content)
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text())["script"] == str(script)


def test_main_retains_python_module_execution_as_unresolved_lineage(
    tmp_path: Path,
) -> None:
    """A module invocation is work even when its source cannot be resolved."""
    import json
    import subprocess

    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"
    command = "python3 -u -m analysis_pipeline --input input.csv"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-08-01T12:00:00Z",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert event["bash_cmd"] == command
    assert event["io_detection"] == "unresolved"
    assert "analysis_pipeline" in event["script_source"]
