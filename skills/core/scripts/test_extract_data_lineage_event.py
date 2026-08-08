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


# ---------- managed-utility exclusion (control-plane scripts) ----------

_SHIPPED_SCRIPTS_DIR = Path(__file__).parent


def _shipped_helper_relpaths() -> list[str]:
    return sorted(
        path.relative_to(_SHIPPED_SCRIPTS_DIR).as_posix()
        for path in _SHIPPED_SCRIPTS_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
    )


_SOURCE_PLUGIN_ROOT = _SHIPPED_SCRIPTS_DIR.parent.parent.parent


def test_every_bundled_core_helper_is_excluded_from_detection(tmp_path: Path) -> None:
    """No bundled helper may create lineage or post-action work — issue #69."""
    install = tmp_path / "cache" / "mycelium" / "mycelium" / "9.9.9"
    _write_mycelium_manifest(install)
    missing: list[str] = []
    for rel in _shipped_helper_relpaths():
        installed = f"{install}/skills/core/scripts/{rel}"
        source = f"{_SOURCE_PLUGIN_ROOT}/skills/core/scripts/{rel}"
        relative = f"skills/core/scripts/{rel}"
        for form, cwd in ((installed, tmp_path), (source, tmp_path), (relative, _SOURCE_PLUGIN_ROOT)):
            if detect_scripts(f'python3 "{form}" --help', cwd):
                missing.append(form)
    assert missing == []


@pytest.mark.parametrize(
    "helper",
    [
        "recall_lessons.py",
        "detect_recurrence.py",
        "upsert_table_row.py",
        "crystallize_findings.py",
        "extract_data_lineage.py",
        "extract_data_lineage_event.py",
        "knowledge_map/cli.py",
    ],
)
def test_issue_69_omitted_helpers_are_excluded(tmp_path: Path, helper: str) -> None:
    install = tmp_path / "cache" / "mycelium" / "mycelium" / "0.6.0"
    _write_mycelium_manifest(install)
    cmd = f"python3 {install}/skills/core/scripts/{helper} --living-dir .living"
    assert detect_scripts(cmd, tmp_path) == []


def test_relative_managed_helper_after_cd_is_excluded(tmp_path: Path) -> None:
    # A source-checkout-shaped repo: conventional layout plus the manifest
    # that proves the root really is Mycelium.
    _write_mycelium_manifest(tmp_path)
    (tmp_path / "skills" / "core" / "scripts").mkdir(parents=True)
    cmd = "cd skills/core && python3 scripts/recall_lessons.py --id L-6"
    assert detect_scripts(cmd, tmp_path) == []


@pytest.mark.parametrize(
    "user_script",
    [
        "analysis/recall_lessons.py",
        "recall_lessons.py",
        "analysis/crystallize_findings.py",
        "my/skills/detect_recurrence.py",
        # Conventional layout AND a colliding filename, but no Mycelium plugin
        # manifest proves the root — must stay eligible analysis (Codex P2 on
        # PR #70): a path shape alone is not trusted identity.
        "skills/core/scripts/crystallize_findings.py",
        "skills/core/scripts/recall_lessons.py",
        "skills/core/scripts/knowledge_map/cli.py",
    ],
)
def test_same_named_user_script_elsewhere_is_still_analysis(
    tmp_path: Path, user_script: str
) -> None:
    out = detect_scripts(f"python3 {user_script}", tmp_path)
    assert out == [(tmp_path / user_script, None)]


def _write_mycelium_manifest(root: Path) -> None:
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"name": "mycelium", "version": "9.9.9"}\n')


def test_suffix_match_requires_a_path_component_boundary(tmp_path: Path) -> None:
    # "myskills/..." must not satisfy a registry suffix that starts at the
    # "skills/" component, even when the root an unguarded match would derive
    # ("<tmp>/my") carries a verifying manifest.
    _write_mycelium_manifest(tmp_path / "my")
    script = "myskills/core/scripts/recall_lessons.py"
    out = detect_scripts(f"python3 {script}", tmp_path)
    assert out == [(tmp_path / script, None)]


def test_verified_install_root_is_excluded(tmp_path: Path) -> None:
    install = tmp_path / "cache" / "mycelium" / "mycelium" / "9.9.9"
    _write_mycelium_manifest(install)
    cmd = f'python3 "{install}/skills/core/scripts/recall_lessons.py" --id L-6'
    assert detect_scripts(cmd, tmp_path) == []


def test_codex_manifest_also_verifies_the_root(tmp_path: Path) -> None:
    install = tmp_path / "install"
    manifest = install / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name": "mycelium", "version": "9.9.9"}\n')
    cmd = f'python3 "{install}/skills/core/scripts/recall_lessons.py"'
    assert detect_scripts(cmd, tmp_path) == []


def _seed_plugin_pointer(repo: Path, target: Path | str) -> None:
    state = repo / ".mycelium"
    state.mkdir(parents=True, exist_ok=True)
    (state / "plugin-root").write_text(f"{target}\n")


def _seed_verified_install(tmp_path: Path) -> Path:
    install = tmp_path / "verified-install"
    _write_mycelium_manifest(install)
    return install


@pytest.mark.parametrize(
    "accessor",
    [
        "$(sed -n '1p' .mycelium/plugin-root)",
        "$(cat .mycelium/plugin-root)",
    ],
)
def test_documented_plugin_root_accessor_is_trusted(
    tmp_path: Path, accessor: str
) -> None:
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = f'python3 "{accessor}/skills/core/scripts/recall_lessons.py" --id L-6'
    assert detect_scripts(cmd, tmp_path) == []


def test_accessor_with_unverified_pointer_target_is_not_trusted(
    tmp_path: Path,
) -> None:
    """Codex P2, round 4 on PR #70: the pointer is repository-controlled.

    A pointer aimed at a user tree (no Mycelium manifest) must not let the
    accessor suppress lineage for a same-named script under that tree.
    """
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    _seed_plugin_pointer(tmp_path, user_tree)
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_accessor_without_pointer_file_is_not_trusted(tmp_path: Path) -> None:
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_accessor_resolves_pointer_under_the_effective_cwd(tmp_path: Path) -> None:
    # `cd nested && …` must dereference nested/.mycelium/plugin-root, and an
    # unverified nested pointer must not be trusted.
    nested = tmp_path / "nested"
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    _seed_plugin_pointer(nested, user_tree)
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = (
        "cd nested && "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cmd, tmp_path)) == 1

    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    assert detect_scripts(cmd, tmp_path) == []


@pytest.mark.parametrize(
    "accessor",
    ["$(cat .mycelium/plugin-root)", "$(sed -n '1p' .mycelium/plugin-root)"],
)
def test_pointer_with_trailing_space_verifies_the_executed_path(
    tmp_path: Path, accessor: str
) -> None:
    """Codex P2, round 5 on PR #70: verify the exact expanded value.

    The shell preserves a trailing space in the pointer line, so execution
    happens under the space-suffixed directory — verifying the stripped path
    would suppress lineage for an unverified tree.
    """
    install = _seed_verified_install(tmp_path)
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_text(f"{install} \n")
    cmd = f'python3 "{accessor}/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_genuinely_space_suffixed_verified_root_is_trusted(tmp_path: Path) -> None:
    install = tmp_path / "install "
    _write_mycelium_manifest(install)
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_text(f"{install}\n")
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert detect_scripts(cmd, tmp_path) == []


def test_multiline_pointer_diverges_between_cat_and_sed(tmp_path: Path) -> None:
    install = _seed_verified_install(tmp_path)
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_text(f"{install}\nsecond line\n")
    # `$(cat …)` preserves the embedded newline, so the executed word is not
    # the verified root — must stay eligible analysis.
    cat_cmd = (
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cat_cmd, tmp_path)) == 1
    # `$(sed -n '1p' …)` reads exactly the first line — the verified root.
    sed_cmd = (
        "python3 \"$(sed -n '1p' .mycelium/plugin-root)"
        '/skills/core/scripts/recall_lessons.py"'
    )
    assert detect_scripts(sed_cmd, tmp_path) == []


@pytest.mark.parametrize(
    "quoted_word",
    [
        # Single quotes disable expansion: the shell executes the LITERAL
        # path "$(cat .mycelium/plugin-root)/…" (Codex P2, round 6 on PR #70).
        "'$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py'",
        "'$(cat .mycelium/plugin-root)'/skills/core/scripts/recall_lessons.py",
    ],
)
def test_expansion_disabled_accessor_word_is_not_trusted(
    tmp_path: Path, quoted_word: str
) -> None:
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    out = detect_scripts(f"python3 {quoted_word}", tmp_path)
    assert len(out) == 1


def test_accessor_component_quoting_is_expansion_enabled(tmp_path: Path) -> None:
    """Codex P2, round 7: `"$(cat …)"/rest` expands even though only the
    accessor component is quoted — it must be treated like the full form."""
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = (
        'python3 "$(cat .mycelium/plugin-root)"'
        "/skills/core/scripts/recall_lessons.py"
    )
    assert detect_scripts(cmd, tmp_path) == []


@pytest.mark.parametrize(
    "accessor",
    ["$(cat .mycelium/plugin-root)", "$(sed -n '1p' .mycelium/plugin-root)"],
)
def test_crlf_pointer_preserves_cr_in_executed_path(
    tmp_path: Path, accessor: str
) -> None:
    """Codex P2, round 7: the shell keeps the \\r of a CRLF pointer; the
    verified path must match the executed CR-suffixed one, not a stripped one."""
    install = _seed_verified_install(tmp_path)
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_bytes(f"{install}\r\n".encode())
    cmd = f'python3 "{accessor}/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_nul_byte_pointer_is_stripped_like_bash_and_stays_detected(
    tmp_path: Path,
) -> None:
    """Codex P2, round 8: bash removes NUL bytes from substitution output,
    so "/tmp/user\\0\\n" executes under /tmp/user — an unverified tree. The
    decoder must neither keep the NUL (crashing realpath and silently
    killing the hook) nor suppress the event."""
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_bytes(f"{user_tree}\x00\n".encode())
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_nul_byte_pointer_to_verified_root_is_excluded(tmp_path: Path) -> None:
    install = _seed_verified_install(tmp_path)
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_bytes(f"{install}\x00\n".encode())
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert detect_scripts(cmd, tmp_path) == []


def test_symlink_dotdot_traversal_is_resolved_before_the_trust_gate(
    tmp_path: Path,
) -> None:
    """Codex P2, round 7: `link/../x` resolves through the symlink target on
    the filesystem, not lexically — the gate must judge the real path."""
    install = _seed_verified_install(tmp_path)
    scripts = install / "skills" / "core" / "scripts"
    scripts.mkdir(parents=True)
    outside_deep = tmp_path / "outside" / "deep"
    outside_deep.mkdir(parents=True)
    (scripts / "link").symlink_to(outside_deep)
    word = f"{scripts}/link/../recall_lessons.py"
    assert len(detect_scripts(f'python3 "{word}"', tmp_path)) == 1


def test_symlinked_alias_of_verified_root_is_still_excluded(tmp_path: Path) -> None:
    install = _seed_verified_install(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(install)
    cmd = f'python3 "{alias}/skills/core/scripts/recall_lessons.py"'
    assert detect_scripts(cmd, tmp_path) == []


def test_flagged_cd_resolves_pointer_against_the_real_directory(
    tmp_path: Path,
) -> None:
    """Issue #71: `cd -P nested` must resolve the pointer under nested/, not
    silently retain the outer cwd whose pointer is verified."""
    nested = tmp_path / "nested"
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    _seed_plugin_pointer(nested, user_tree)
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = (
        "cd -P nested && "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cmd, tmp_path)) == 1

    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    assert detect_scripts(cmd, tmp_path) == []


@pytest.mark.parametrize(
    "cd_form",
    ["cd -L nested", "cd -- nested", "cd -P -e nested", "cd -LP nested"],
)
def test_modeled_cd_flag_forms_follow_the_directory(
    tmp_path: Path, cd_form: str
) -> None:
    nested = tmp_path / "nested"
    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    cmd = (
        f"{cd_form} && "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert detect_scripts(cmd, tmp_path) == []


def test_cd_dash_p_resolves_symlinks_physically(tmp_path: Path) -> None:
    real = tmp_path / "real"
    _seed_plugin_pointer(real, _seed_verified_install(tmp_path))
    (tmp_path / "linkdir").symlink_to(real)
    cmd = (
        "cd -P linkdir && "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert detect_scripts(cmd, tmp_path) == []


def test_flags_after_the_cd_operand_are_not_options(tmp_path: Path) -> None:
    """PR #72 P1: `cd nested -P` is an error (or version-dependent) in bash —
    the hook must not follow `nested` while bash stays in the outer dir."""
    nested = tmp_path / "nested"
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    _seed_plugin_pointer(tmp_path, user_tree)
    cmd = (
        "cd nested -P; "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cmd, tmp_path)) == 1


@pytest.mark.parametrize(
    "prefix",
    [
        "pushd nested",
        "cd -@ nested",
        "source setup.sh",
        ". setup.sh",
        "eval 'cd nested'",
        # Launcher-prefixed builtins still change the parent shell's cwd
        # (PR #72 P1): `builtin cd`, `command cd`, and the `time` keyword.
        "builtin cd nested",
        "command cd nested",
        "time cd nested",
        "time -p cd nested",
        "builtin source setup.sh",
        "command builtin cd nested",
    ],
)
def test_unmodeled_cwd_change_fails_closed_for_accessor_trust(
    tmp_path: Path, prefix: str
) -> None:
    """Issue #71: an unmodeled cwd-affecting builtin before the accessor
    means the pointer's directory is unknown — never trust the outer one."""
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "setup.sh").write_text("cd nested\n")
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = (
        f"{prefix} && "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_bare_accessor_is_never_attributed_or_suppressed(tmp_path: Path) -> None:
    """Codex P2, round 11 on PR #70: an unquoted substitution field-splits,
    so its executed argv is statically unknowable. It must be rejected
    conservatively — no suppression, and no guessed attribution either —
    even when the pointer names a verified root containing a space."""
    install = tmp_path / "analysis.py plugin"
    _write_mycelium_manifest(install)
    _seed_plugin_pointer(tmp_path, install)
    bare = (
        "python3 $(cat .mycelium/plugin-root)"
        "/skills/core/scripts/recall_lessons.py"
    )
    assert detect_scripts(bare, tmp_path) == []


def test_backslash_escaped_accessor_is_never_suppressed_or_fabricated(
    tmp_path: Path,
) -> None:
    # Unquoted "\$(" is a bash syntax error mid-word — the command can never
    # execute, so recording nothing (rather than trusting anything) is right.
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    word = "\\$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"
    assert detect_scripts(f"python3 {word}", tmp_path) == []


def test_pointer_with_only_trailing_newlines_is_trusted(tmp_path: Path) -> None:
    install = _seed_verified_install(tmp_path)
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").write_text(f"{install}\n\n\n")
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert detect_scripts(cmd, tmp_path) == []


def test_accessor_with_relative_pointer_target_is_not_trusted(
    tmp_path: Path,
) -> None:
    install = _seed_verified_install(tmp_path)
    _seed_plugin_pointer(tmp_path, install.relative_to(tmp_path))
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_accessor_with_symlinked_pointer_file_is_not_trusted(
    tmp_path: Path,
) -> None:
    install = _seed_verified_install(tmp_path)
    real = tmp_path / "elsewhere.txt"
    real.write_text(f"{install}\n")
    state = tmp_path / ".mycelium"
    state.mkdir()
    (state / "plugin-root").symlink_to(real)
    cmd = 'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    assert len(detect_scripts(cmd, tmp_path)) == 1


@pytest.mark.parametrize(
    "substitution",
    [
        "$(cat some/other/pointer)",
        # Mentions the pointer file without reading it — `basename` expands to
        # "plugin-root", a plain user directory (Codex P2, round 2 on PR #70).
        "$(basename .mycelium/plugin-root)",
        "$(echo .mycelium/plugin-root)",
        "$(dirname .mycelium/plugin-root)",
        # Reads a different repository's pointer, not this project's.
        "$(cat other/.mycelium/plugin-root)",
        "$(sed -n '1p' nested/.mycelium/plugin-root)",
    ],
)
def test_non_reader_command_substitution_prefix_is_not_trusted(
    tmp_path: Path, substitution: str
) -> None:
    # Even with a valid pointer to a verified install, non-reader forms and
    # foreign pointers stay untrusted.
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = f'python3 "{substitution}/skills/core/scripts/recall_lessons.py"'
    out = detect_scripts(cmd, tmp_path)
    assert len(out) == 1


@pytest.mark.parametrize(
    "word",
    [
        # User-controlled components before a real pointer read resolve under
        # the cwd, not the managed install (Codex P2, round 3 on PR #70).
        'foo/$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py',
        './$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py',
        "sub/$(sed -n '1p' .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py",
        # Traversal after the accessor escapes the managed tree.
        '$(cat .mycelium/plugin-root)/skills/core/scripts/../../../evil_analysis.py',
    ],
)
def test_accessor_not_anchoring_the_word_is_not_trusted(
    tmp_path: Path, word: str
) -> None:
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    out = detect_scripts(f'python3 "{word}"', tmp_path)
    assert len(out) == 1


def test_accessor_with_normalizable_inner_dot_components_is_trusted(
    tmp_path: Path,
) -> None:
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    word = "$(cat .mycelium/plugin-root)/skills/core/./scripts/recall_lessons.py"
    assert detect_scripts(f'python3 "{word}"', tmp_path) == []


def test_non_mycelium_manifest_does_not_verify_the_root(tmp_path: Path) -> None:
    install = tmp_path / "install"
    manifest = install / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name": "other-plugin"}\n')
    script = install / "skills" / "core" / "scripts" / "recall_lessons.py"
    out = detect_scripts(f'python3 "{script}"', tmp_path)
    assert out == [(script, None)]


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
            "import pandas as pd\n"
            "class Reader:\n"
            "    def read_csv(self, path):\n"
            "        print(path)\n"
            "pd = Reader()\n"
            "pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "import pandas as pd\n"
            "class Reader:\n"
            "    def read_csv(self, path):\n"
            "        print(path)\n"
            "for pd in [Reader()]:\n"
            "    pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "import pandas as pd\n"
            "with reader_context() as pd:\n"
            "    pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "import pandas as pd\n"
            "pd, other = Reader(), None\n"
            "pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "import pandas as pd\n"
            "try:\n"
            "    operation()\n"
            "except Exception as pd:\n"
            "    pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "import pandas as pd\n"
            "if (pd := reader_factory()):\n"
            "    pd.read_csv(ROOT / 'ghost.csv')\n"
        ),
        (
            "import pandas as pd\n"
            "[pd.read_csv(ROOT / 'ghost.csv') for pd in readers]\n"
        ),
        (
            "import pandas as pd\n"
            "match value:\n"
            "    case {'reader': pd}:\n"
            "        pd.read_csv(ROOT / 'ghost.csv')\n"
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


def test_repository_recovery_rejects_runtime_filename_concatenation(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    decoy = tmp_path / "data" / "sample.csv"
    actual = tmp_path / "data" / "actual_sample.csv"
    decoy.parent.mkdir()
    decoy.write_text("decoy\n")
    actual.write_text("actual\n")
    script = tmp_path / "run.py"
    script.write_text(
        "import sys\n"
        "import pandas as pd\n"
        "prefix = sys.argv[1]\n"
        "pd.read_csv(prefix + 'sample.csv')\n"
    )
    args = argparse.Namespace(
        ts="2026-08-02T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py actual_",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []


@pytest.mark.parametrize(
    "source,expected_key",
    [
        (
            "import pandas as pd\n"
            "pd.read_csv('sample' + '.csv')\n",
            "inputs",
        ),
        ("frame.to_csv('sample' + '.csv')\n", "outputs"),
    ],
)
def test_repository_recovery_accepts_static_filename_concatenation(
    tmp_path: Path, source: str, expected_key: str
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sample = tmp_path / "data" / "sample.csv"
    sample.parent.mkdir()
    sample.write_text("x\n")
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
    assert [item["path"] for item in event[expected_key]] == [str(sample)]


@pytest.mark.parametrize(
    "source,relative_path,expected_key",
    [
        (
            "import pandas as pd\n"
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
