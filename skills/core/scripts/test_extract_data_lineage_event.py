"""Tests for extract_data_lineage_event.py regex patterns and detection logic."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from extract_data_lineage_event import (  # noqa: E402
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


def test_detect_script_jupyter() -> None:
    script, inline = detect_script("jupyter execute nb.ipynb", Path("/tmp"))
    assert script == Path("/tmp/nb.ipynb")


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
