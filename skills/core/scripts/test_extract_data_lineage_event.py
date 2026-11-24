"""Tests for extract_data_lineage_event.py regex patterns and detection logic."""

from __future__ import annotations

import argparse
import json
import os
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
    UnresolvedScript,
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


# ---------- scan_source: filters are no longer collected ----------


@pytest.mark.parametrize(
    "src",
    [
        "df.query('a > 5')",
        "subset = df[df.score > 0.5]",
        "out = a.merge(b, on='key', how='left')",
        "subset = df.loc[df.score > 0.5, 'col_a']",
        "all_df = pd.concat([df1, df2], ignore_index=True)",
    ],
)
def test_filters_are_not_collected(src: str) -> None:
    # The v1 regex pack recorded unevaluated text with false positives and
    # nothing read it; v2 drops it (evaluated access is item 7's helper).
    _, _, filters, _ = scan_source(src)
    assert filters == []


def test_seed_inside_a_string_is_not_a_seed() -> None:
    _, _, _, seeds = scan_source("print('np.random.seed(7)')", "python")
    assert seeds == []


def test_r_set_seed_is_a_seed() -> None:
    _, _, _, seeds = scan_source("set.seed(123)\nx <- rnorm(5)\n# set.seed(9)\n", "r")
    assert seeds == [123]


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


def _analysis_events(target: Path) -> list[dict]:
    """Analysis events in an events file. A command with no proven analysis
    may still leave a `data_command` record; that record must never claim
    an execution, so it is checked here and left out of the result."""
    if not target.exists():
        return []
    events = []
    for line in target.read_text().splitlines():
        record = json.loads(line)
        if record.get("kind") == "data_command":
            assert record["io_detection"] == "unresolved"
            assert "script" not in record and "script_sha256" not in record
            continue
        events.append(record)
    return events


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


def test_cd_dash_p_fails_on_missing_intermediate_component(
    tmp_path: Path,
) -> None:
    """PR #72 round-3 P1: bash `cd -P nested/missing/..` fails because the
    physical walk hits the missing component before `..`; non-strict
    realpath must not collapse it into a successful cd at `nested`."""
    nested = tmp_path / "nested"
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    _seed_plugin_pointer(tmp_path, user_tree)
    cmd = (
        "cd -P nested/missing/..; "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_cd_dash_p_fails_on_file_component_before_dotdot(tmp_path: Path) -> None:
    """PR #72 round-4 P1: `cd -P nested/file/..` is ENOTDIR in bash; the
    lexical `file/..` collapse must not turn it into a success at nested."""
    nested = tmp_path / "nested"
    user_tree = tmp_path / "user-tree"
    user_tree.mkdir()
    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    _seed_plugin_pointer(tmp_path, user_tree)
    (nested / "file").write_text("just a file\n")
    cmd = (
        "cd -P nested/file/..; "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert len(detect_scripts(cmd, tmp_path)) == 1


def test_cd_dash_p_with_existing_intermediate_component_succeeds(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    (nested / "sub").mkdir(parents=True)
    _seed_plugin_pointer(nested, _seed_verified_install(tmp_path))
    cmd = (
        "cd -P nested/sub/.. && "
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
        # Launcher options must be consumed before classifying the follower
        # (PR #72 round-2 P1): these still run cd in the parent shell.
        "command -p cd nested",
        "command -- cd nested",
        "command -p -- cd nested",
        "builtin -- cd nested",
        # `--` is a valid option terminator for the time keyword too
        # (PR #72 round-3 P1).
        "time -- cd nested",
        "time -p -- cd nested",
        # time's operand is a pipeline, so assignment prefixes still reach
        # the builtin (PR #72 round-4 P1).
        "time X=1 cd nested",
        "time -p X=1 cd nested",
        "time -- X=1 Y=2 cd nested",
        # Leading redirections leave the builtin running in the parent shell
        # (PR #72 round-5 P1) — for time, launchers, and plain segments.
        "time >timing.log cd nested",
        "time -p 2>/dev/null cd nested",
        "time > timing.log cd nested",
        "time X=1 >log 2>&1 cd nested",
        ">log cd nested",
        "2>&1 cd nested",
        "command >log cd nested",
        "builtin -- >log cd nested",
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


def test_command_describe_mode_does_not_block_accessor_trust(
    tmp_path: Path,
) -> None:
    # `command -v cd nested` only PRINTS; nothing changes the cwd, so the
    # verified outer pointer remains trustworthy.
    nested = tmp_path / "nested"
    nested.mkdir()
    _seed_plugin_pointer(tmp_path, _seed_verified_install(tmp_path))
    cmd = (
        "command -v cd nested && "
        'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py"'
    )
    assert detect_scripts(cmd, tmp_path) == []


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


_PANDAS_PATH_PRELUDE = "import os\nimport sys\nfrom pathlib import Path\nimport pandas as pd\n"


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "frame = pd.read_csv(Path(__file__).parent / 'input.csv')\n"
            "frame.to_csv(Path(__file__).parent / 'result.csv')\n",
            id="file-anchored",
        ),
        pytest.param(
            "ROOT = Path(__file__).resolve().parent\n"
            "frame = pd.read_csv(ROOT / 'input.csv')\n"
            "frame.to_csv(ROOT.joinpath('result.csv'))\n",
            id="module-constant",
        ),
        pytest.param(
            "limit = int(sys.argv[1])\n"
            "data_dir = os.environ.get('UNUSED', 'x')\n"
            "frame = pd.read_csv(Path(__file__).parent / 'input.csv')\n"
            "top = frame.head(limit)\n"
            "top.to_csv(Path(__file__).parent / 'result.csv')\n",
            id="runtime-values-unrelated-to-path",
        ),
        pytest.param(
            "input = Path(__file__).parent\n"
            "frame = pd.read_csv(input / 'input.csv')\n"
            "frame.to_csv(input / 'result.csv')\n",
            id="locally-rebound-builtin-input",
        ),
        pytest.param(
            "p = Path(__file__).parent\n"
            "pd.read_csv(p / 'input.csv').to_csv(p / 'result.csv')\n",
            id="writer-chained-on-reader",
        ),
        pytest.param(
            "limit = int(sys.argv[1])\n"
            "frame = pd.read_csv(Path(__file__).parent / 'input.csv')\n"
            "frame.head(limit).to_csv(Path(__file__).parent / 'result.csv')\n",
            id="writer-chained-on-head",
        ),
        pytest.param(
            "p = Path(__file__).parent\n"
            "pd.read_csv(p / 'input.csv').to_parquet(p / 'result.csv')\n",
            id="parquet-writer-chained-on-reader",
        ),
        pytest.param(
            "frames = [pd.read_csv(Path(__file__).parent / 'input.csv')]\n"
            "frames[0].to_csv(Path(__file__).parent / 'result.csv')\n",
            id="writer-on-subscript",
        ),
        pytest.param(
            "from helpers import export_table as to_csv\n"
            "p = Path(__file__).parent\n"
            "pd.read_csv(p / 'input.csv').to_csv(p / 'result.csv')\n",
            id="chained-writer-despite-unrelated-same-named-import",
        ),
        pytest.param(
            "class Preview:\n"
            "    def to_csv(self, path):\n"
            "        print(path)\n"
            "p = Path(__file__).parent\n"
            "pd.read_csv(p / 'input.csv').to_csv(p / 'result.csv')\n",
            id="chained-writer-beside-unrelated-custom-method",
        ),
        pytest.param(
            "def to_csv(path):\n"
            "    print(path)\n"
            "frame = pd.read_csv(Path(__file__).parent / 'input.csv')\n"
            "frame.to_csv(Path(__file__).parent / 'result.csv')\n",
            id="named-writer-beside-unrelated-custom-function",
        ),
    ],
)
def test_repository_recovery_keeps_static_path_prefixes(
    tmp_path: Path, source: str
) -> None:
    """A static prefix with a literal filename still recovers the repo file."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sample = tmp_path / "input.csv"
    result = tmp_path / "result.csv"
    sample.write_text("value\n1\n")
    result.write_text("value\n1\n")
    script = tmp_path / "run.py"
    script.write_text(_PANDAS_PATH_PRELUDE + source)
    args = argparse.Namespace(
        ts="2026-11-15T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py 5",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static+repository"
    assert [item["path"] for item in event["inputs"]] == [str(sample)]
    assert [item["path"] for item in event["outputs"]] == [str(result)]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import matplotlib.pyplot as plt\n"
            "plt.figure().savefig(Path(__file__).parent / 'figure.png')\n",
            id="chained-on-call",
        ),
        pytest.param(
            "import matplotlib.pyplot as plt\n"
            "class Preview:\n"
            "    def savefig(self, path):\n"
            "        print(path)\n"
            "plt.figure().savefig(Path(__file__).parent / 'figure.png')\n",
            id="chained-beside-unrelated-custom-method",
        ),
        pytest.param(
            "from matplotlib.pyplot import savefig\n"
            "def preview(savefig):\n"
            "    pass\n"
            "savefig(Path(__file__).parent / 'figure.png')\n",
            id="imported-beside-parameter-in-other-scope",
        ),
        pytest.param(
            "from matplotlib.pyplot import savefig\n"
            "class Panel:\n"
            "    savefig = None\n"
            "    def draw(self):\n"
            "        savefig(Path(__file__).parent / 'figure.png')\n",
            id="imported-inside-method-despite-class-attribute",
        ),
    ],
)
def test_repository_recovery_records_savefig(
    tmp_path: Path, source: str
) -> None:
    """A real figure write survives chaining and unrelated same-named code."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    figure = tmp_path / "figure.png"
    figure.write_bytes(b"png")
    script = tmp_path / "run.py"
    script.write_text("from pathlib import Path\n" + source)
    args = argparse.Namespace(
        ts="2026-11-15T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert [item["path"] for item in event["outputs"]] == [str(figure)]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def to_csv(path):\n"
            "    print(path)\n"
            "to_csv(Path(__file__).parent / 'result.csv')\n",
            id="local-function",
        ),
        pytest.param(
            "from helpers import to_csv\n"
            "to_csv(Path(__file__).parent / 'result.csv')\n",
            id="non-io-import",
        ),
        pytest.param(
            "def savefig(path):\n"
            "    print(path)\n"
            "savefig(Path(__file__).parent / 'result.csv')\n",
            id="local-savefig",
        ),
    ],
)
def test_repository_recovery_ignores_unqualified_custom_writer(
    tmp_path: Path, source: str
) -> None:
    """A bare writer name is a user's own function, not proof of a write."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = tmp_path / "result.csv"
    result.write_text("value\n1\n")
    script = tmp_path / "run.py"
    script.write_text(_PANDAS_PATH_PRELUDE + source)
    args = argparse.Namespace(
        ts="2026-11-15T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["outputs"] == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "frame = pd.read_csv(Path(sys.argv[1]) / 'input.csv')\n"
            "frame.to_csv(Path(sys.argv[1]) / 'result.csv', index=False)\n",
            id="argv-inline",
        ),
        pytest.param(
            "base = Path(sys.argv[1])\n"
            "path = base / 'input.csv'\n"
            "frame = pd.read_csv(path)\n"
            "frame.to_csv(base / 'result.csv')\n",
            id="argv-through-assignments",
        ),
        pytest.param(
            "from sys import argv\n"
            "frame = pd.read_csv(Path(argv[1]) / 'input.csv')\n"
            "frame.to_csv(Path(argv[1]).joinpath('result.csv'))\n",
            id="argv-imported-name",
        ),
        pytest.param(
            "import argparse\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('indir')\n"
            "args = parser.parse_args()\n"
            "frame = pd.read_csv(Path(args.indir) / 'input.csv')\n"
            "frame.to_csv(Path(args.indir) / 'result.csv')\n",
            id="argparse-namespace",
        ),
        pytest.param(
            "frame = pd.read_csv(Path(os.environ['DATA_DIR']) / 'input.csv')\n"
            "frame.to_csv(os.path.join(os.environ['DATA_DIR'], 'result.csv'))\n",
            id="environ-subscript",
        ),
        pytest.param(
            "frame = pd.read_csv(os.path.join(sys.argv[1], 'input.csv'))\n"
            "frame.to_csv(Path(sys.argv[1], 'result.csv'))\n",
            id="argv-path-join-arguments",
        ),
    ],
)
def test_repository_recovery_ignores_decoys_for_runtime_path_prefixes(
    tmp_path: Path, source: str
) -> None:
    """A runtime-chosen directory must not resolve to a same-named repo file.

    The script reads ``<external>/input.csv`` from a command-line or
    environment value; the repository's own ``input.csv`` is a decoy.
    """
    repo = tmp_path / "repo"
    external = tmp_path / "external"
    repo.mkdir()
    external.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "input.csv").write_text("value\n999\n")
    (repo / "result.csv").write_text("stale\n999\n")
    (external / "input.csv").write_text("value\n42\n")
    script = repo / "run.py"
    script.write_text(_PANDAS_PATH_PRELUDE + source)
    args = argparse.Namespace(
        ts="2026-11-15T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd=f"python run.py {external}",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, repo, "abc123")

    assert event is not None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []
    assert event["lineage_warnings"]


def _decoy_repository_event(tmp_path: Path, source: str) -> dict:
    """Run ``source`` against a repository holding same-named decoys."""
    repo = tmp_path / "repo"
    external = tmp_path / "external"
    repo.mkdir()
    external.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "input.csv").write_text("value\n999\n")
    (repo / "result.csv").write_text("stale\n999\n")
    (external / "input.csv").write_text("value\n42\n")
    script = repo / "run.py"
    script.write_text(_PANDAS_PATH_PRELUDE + source)
    args = argparse.Namespace(
        ts="2026-11-15T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd=f"python run.py {external}",
        bash_exit=0,
    )
    event = build_event_for_detection((script, None), args, repo, "abc123")
    assert event is not None
    return event


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def load(d):\n"
            "    frame = pd.read_csv(d / 'input.csv')\n"
            "    frame.to_csv(d / 'result.csv')\n"
            "load(Path(sys.argv[1]))\n",
            id="parameter-division",
        ),
        pytest.param(
            "async def load(d):\n"
            "    frame = pd.read_csv(Path(d, 'input.csv'))\n"
            "    frame.to_csv(Path(d) / 'result.csv')\n",
            id="async-parameter-path-call",
        ),
        pytest.param(
            "def load(*, d):\n"
            "    frame = pd.read_csv(os.path.join(d, 'input.csv'))\n"
            "    frame.to_csv(os.path.join(d, 'result.csv'))\n"
            "load(d=sys.argv[1])\n",
            id="keyword-only-parameter-path-join",
        ),
        pytest.param(
            "def load(*dirs, **opts):\n"
            "    frame = pd.read_csv(dirs[0] / 'input.csv')\n"
            "    frame.to_csv(opts['out'] / 'result.csv')\n",
            id="star-args-and-kwargs",
        ),
        pytest.param(
            "load = lambda d: pd.read_csv(d / 'input.csv')\n"
            "save = lambda frame, d: frame.to_csv(d / 'result.csv')\n"
            "save(load(Path(sys.argv[1])), Path(sys.argv[1]))\n",
            id="lambda-parameter",
        ),
        pytest.param(
            "def outer(d):\n"
            "    def inner():\n"
            "        frame = pd.read_csv(d / 'input.csv')\n"
            "        frame.to_csv(d / 'result.csv')\n"
            "    inner()\n"
            "outer(Path(sys.argv[1]))\n",
            id="enclosing-function-parameter",
        ),
        pytest.param(
            "class Job:\n"
            "    def __init__(self, d):\n"
            "        self.d = Path(d)\n"
            "    def run(self):\n"
            "        frame = pd.read_csv(self.d / 'input.csv')\n"
            "        frame.to_csv(self.d / 'result.csv')\n"
            "Job(sys.argv[1]).run()\n",
            id="method-self-attribute",
        ),
        pytest.param(
            "def load(dirs):\n"
            "    input_dir, output_dir = dirs\n"
            "    frame = pd.read_csv(input_dir / 'input.csv')\n"
            "    frame.to_csv(output_dir / 'result.csv')\n"
            "load((Path(sys.argv[1]), Path(sys.argv[1])))\n",
            id="parameter-tuple-unpacking",
        ),
    ],
)
def test_repository_recovery_ignores_decoys_for_parameter_path_prefixes(
    tmp_path: Path, source: str
) -> None:
    """A directory passed in as a parameter must not match a repo decoy (#131).

    The caller decides the directory at run time, so the literal filename
    joined to it does not identify a repository file.
    """
    event = _decoy_repository_event(tmp_path, source)

    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []
    assert event["lineage_warnings"]


def test_repository_recovery_ignores_decoys_for_parameter_aliases(
    tmp_path: Path,
) -> None:
    """A local alias built from a parameter inherits its runtime taint."""
    event = _decoy_repository_event(
        tmp_path,
        "def load(d):\n"
        "    p = d / 'input.csv'\n"
        "    q = Path(d, 'result.csv')\n"
        "    frame = pd.read_csv(p)\n"
        "    frame.to_csv(q)\n"
        "load(Path(sys.argv[1]))\n",
    )

    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []
    assert event["lineage_warnings"]


def test_repository_recovery_leaves_static_parameter_defaults_unresolved(
    tmp_path: Path,
) -> None:
    """A static default is still a parameter: any call may override it.

    Even when every visible call relies on the default, recovery does not
    follow it, because proving that no caller (including an importer of the
    module) overrides the default is beyond this static scan. Unresolved
    lineage with a warning is preferred to a confidently wrong input.
    """
    event = _decoy_repository_event(
        tmp_path,
        "ROOT = Path(__file__).parent\n"
        "def load(root=ROOT):\n"
        "    frame = pd.read_csv(root / 'input.csv')\n"
        "    frame.to_csv(root / 'result.csv')\n"
        "load()\n",
    )

    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == []
    assert event["outputs"] == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def main(limit):\n"
            "    frame = pd.read_csv(Path(__file__).parent / 'input.csv')\n"
            "    top = frame.head(limit)\n"
            "    top.to_csv(Path(__file__).parent / 'result.csv')\n"
            "main(int(sys.argv[1]))\n",
            id="function-with-unrelated-parameter",
        ),
        pytest.param(
            "ROOT = Path(__file__).parent\n"
            "def main(limit):\n"
            "    frame = pd.read_csv(ROOT / 'input.csv')\n"
            "    top = frame.head(limit)\n"
            "    top.to_csv(ROOT / 'result.csv')\n"
            "main(int(sys.argv[1]))\n",
            id="module-constant-beside-parameter",
        ),
    ],
)
def test_repository_recovery_keeps_static_paths_beside_parameters(
    tmp_path: Path, source: str
) -> None:
    """Parameters only block recovery for paths that actually use them."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    sample = tmp_path / "input.csv"
    result = tmp_path / "result.csv"
    sample.write_text("value\n1\n")
    result.write_text("value\n1\n")
    script = tmp_path / "run.py"
    script.write_text(_PANDAS_PATH_PRELUDE + source)
    args = argparse.Namespace(
        ts="2026-11-15T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py 5",
        bash_exit=0,
    )

    event = build_event_for_detection((script, None), args, tmp_path, "abc123")

    assert event is not None
    assert event["io_detection"] == "static+repository"
    assert [item["path"] for item in event["inputs"]] == [str(sample)]
    assert [item["path"] for item in event["outputs"]] == [str(result)]


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
    assert bool(_analysis_events(target)) is expected_event
    if expected_event:
        assert _analysis_events(target)[0]["script"] == str(script)


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
    assert _analysis_events(target) == []


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
    assert _analysis_events(target) == []


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
    assert _analysis_events(target) == []


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
    assert _analysis_events(target) == []


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
    assert _analysis_events(target) == []


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


# ---------- pipeline and shell-script runners ----------


def _runner_detections(command: str, cwd: Path, bash_exit: int | None = 0):
    """Post-action view: detections proven to execute, inline one-liners excluded."""
    return [
        (script, inline)
        for script, inline, _ in lineage_event._detect_scripts_with_cwd(
            command,
            cwd,
            bash_exit=bash_exit,
            require_execution_evidence=True,
            include_inline=False,
        )
    ]


@pytest.mark.parametrize(
    "command,expected_script",
    [
        ("bash run.sh", "run.sh"),
        ("sh run.sh", "run.sh"),
        ("zsh run.sh --cores 4", "run.sh"),
        ("bash -eux run.sh", "run.sh"),
        ("bash -o pipefail run.sh", "run.sh"),
        ("bash -eo pipefail run.sh", "run.sh"),
        ("bash --norc run.sh", "run.sh"),
        ("/bin/bash run.sh", "run.sh"),
        ('bash "run.sh"', "run.sh"),
        ("./run.sh", "run.sh"),
        ("./run.sh -n", "run.sh"),
        ("time bash run.sh", "run.sh"),
        ("env FOO=1 bash run.sh", "run.sh"),
        ("conda run -n env bash run.sh", "run.sh"),
        ("bash run.sh > logs/run.log 2>&1", "run.sh"),
        ("sbatch job.sh", "job.sh"),
        ("sbatch -J name -p short --mem=4G job.sh", "job.sh"),
        ("sbatch --parsable --hold job.sh", "job.sh"),
        ("sbatch --job-name x -t 01:00:00 job.sh arg1", "job.sh"),
        ("nextflow run main.nf -profile test", "main.nf"),
        ("nextflow -C extra.config run main.nf", "main.nf"),
        ("nextflow -q run main.nf --input samples.csv", "main.nf"),
        ("snakemake -s alt.smk -c1", "alt.smk"),
        ("snakemake --snakefile=alt.smk --cores 1", "alt.smk"),
    ],
)
def test_runner_detects_entry_script(
    tmp_path: Path, command: str, expected_script: str
) -> None:
    (tmp_path / expected_script).write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [
        (tmp_path / expected_script, None)
    ]


def test_runner_shell_script_resolves_through_cd(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis" / "rnaseq"
    analysis.mkdir(parents=True)
    (analysis / "run.sh").write_text("snakemake -c1\n")
    assert _runner_detections("cd analysis/rnaseq && bash run.sh", tmp_path) == [
        (analysis / "run.sh", None)
    ]
    assert _runner_detections("analysis/rnaseq/run.sh", tmp_path) == [
        (analysis / "run.sh", None)
    ]


@pytest.mark.parametrize(
    "layout",
    ["Snakefile", "snakefile", "workflow/Snakefile", "workflow/snakefile"],
)
def test_runner_snakemake_finds_default_snakefile(
    tmp_path: Path, layout: str
) -> None:
    snakefile = tmp_path / layout
    snakefile.parent.mkdir(parents=True, exist_ok=True)
    snakefile.write_text("rule all:\n    input: []\n")
    for command in ("snakemake -c1", "snakemake --cores 4 all", "snakemake -pk -j 8"):
        detections = _runner_detections(command, tmp_path)
        assert len(detections) == 1 and detections[0][1] is None, command
        # Case-insensitive filesystems resolve `Snakefile` to a lowercase file,
        # exactly as snakemake's own existence-ordered lookup does.
        assert detections[0][0].samefile(snakefile), command


def test_runner_without_resolvable_entry_is_still_detected(tmp_path: Path) -> None:
    """A run whose entry file cannot be named is work, not silence."""
    snake = _runner_detections("snakemake -c1", tmp_path)
    assert len(snake) == 1 and snake[0][0] is None
    assert "Snakemake" in snake[0][1]

    remote = _runner_detections("nextflow run nf-core/rnaseq -r 3.14", tmp_path)
    assert len(remote) == 1 and remote[0][0] is None
    assert "nf-core/rnaseq" in remote[0][1]

    wrapped = _runner_detections('sbatch -c 4 --wrap "python fit.py"', tmp_path)
    assert len(wrapped) == 1 and wrapped[0][0] is None
    assert "python fit.py" in wrapped[0][1]

    assigned = _runner_detections("sbatch --wrap='Rscript fit.R'", tmp_path)
    assert len(assigned) == 1 and "Rscript fit.R" in assigned[0][1]


def test_runner_nextflow_directory_pipeline_uses_main_nf(tmp_path: Path) -> None:
    (tmp_path / "main.nf").write_text("workflow {}\n")
    assert _runner_detections("nextflow run . -profile test", tmp_path) == [
        (tmp_path / "main.nf", None)
    ]


@pytest.mark.parametrize(
    "command",
    [
        # Shell: syntax check, inline source, stdin, help, no script.
        "bash -n run.sh",
        "bash -en run.sh",
        "bash -c 'echo run.sh'",
        "bash -s < run.sh",
        "bash --version",
        "bash --help run.sh",
        "bash",
        "bash notes.txt",
        # Shell scripts mentioned rather than executed.
        "cat run.sh",
        "chmod +x run.sh",
        "git add run.sh",
        "echo bash run.sh",
        "echo ./run.sh",
        "run.sh",
        "vim analysis/run.sh",
        "if false; then\nbash run.sh\nfi",
        "true # && bash run.sh",
        # Snakemake: dry runs and non-executing modes.
        "snakemake -n",
        "snakemake -np -c1",
        "snakemake --dry-run -c1",
        "snakemake --dryrun",
        "snakemake --help",
        "snakemake --version",
        "snakemake --lint",
        "snakemake --list",
        "snakemake --unlock",
        "snakemake --dag | dot -Tpdf > dag.pdf",
        "snakemake --rulegraph",
        "snakemake --summary",
        "snakemake --touch -c1",
        "grep rule Snakefile",
        "echo snakemake -c1",
        # Nextflow: anything other than an executing `run`.
        "nextflow -version",
        "nextflow pull nf-core/rnaseq",
        "nextflow log",
        "nextflow run main.nf -preview",
        "nextflow run -help",
        "nextflow run main.nf -stub-run",
        # sbatch: help and validation without submission.
        "sbatch --help",
        "sbatch --version",
        "sbatch --usage",
        "sbatch --test-only job.sh",
        "squeue -u me",
    ],
)
def test_runner_rejects_non_executing_forms(tmp_path: Path, command: str) -> None:
    for name in ("run.sh", "job.sh", "Snakefile", "main.nf", "notes.txt"):
        (tmp_path / name).write_text("echo hi\n")
    (tmp_path / "analysis").mkdir()
    (tmp_path / "analysis" / "run.sh").write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [], command


def test_runner_failed_conditional_branch_is_not_proven(tmp_path: Path) -> None:
    (tmp_path / "run.sh").write_text("echo hi\n")
    assert _runner_detections("false && bash run.sh", tmp_path, bash_exit=1) == []
    assert _runner_detections("true && bash run.sh", tmp_path, bash_exit=1) == [
        (tmp_path / "run.sh", None)
    ]


def test_runner_is_analysis_gate(tmp_path: Path) -> None:
    for command in (
        "bash run.sh",
        "./run.sh",
        "snakemake -c1",
        "sbatch job.sh",
        "nextflow run main.nf",
    ):
        assert is_analysis(command), command
    for command in ("ls -la", "cat run.sh", "git status"):
        assert not is_analysis(command), command


def test_runner_skips_shell_scripts_shipped_in_the_plugin(tmp_path: Path) -> None:
    hook = lineage_event._EXECUTING_PLUGIN_ROOT / "skills/core/hooks/mycelium-health.sh"
    assert hook.is_file()
    assert _runner_detections(f"bash {hook}", tmp_path) == []
    assert _runner_detections(str(hook), tmp_path) == []


def test_main_records_runner_entry_script_as_lineage(tmp_path: Path) -> None:
    import json
    import subprocess

    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    (tmp_path / "run.sh").write_text("#!/bin/bash\nsnakemake -c1\n")
    target = tmp_path / "events.tmp"
    base = [
        sys.executable,
        str(extractor),
        "--cwd",
        str(tmp_path),
        "--ts",
        "2026-10-11T12:00:00Z",
        "--bash-exit",
        "0",
    ]

    post_action = subprocess.run(
        [*base, "--bash-cmd", "bash run.sh", "--check-post-action"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert post_action.returncode == 0, post_action.stderr

    dry_run = subprocess.run(
        [*base, "--bash-cmd", "snakemake -n", "--check-post-action"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert dry_run.returncode == 1, dry_run.stderr

    result = subprocess.run(
        [*base, "--bash-cmd", "bash run.sh", "--append-to", str(target)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert event["script"] == str(tmp_path / "run.sh")
    assert "snakemake -c1" in event["script_source"]


@pytest.mark.parametrize(
    "command,expected_script",
    [
        # Option values that look like scripts are not the job script.
        ("sbatch -o out.sh job.sh", "job.sh"),
        ("sbatch --output out.sh job.sh", "job.sh"),
        ("sbatch --output=out.sh job.sh", "job.sh"),
        ("bash --rcfile rc.sh run.sh", "run.sh"),
        # Arguments after the script belong to the script, not the runner.
        ("bash run.sh --help", "run.sh"),
        ("bash run.sh -n", "run.sh"),
        ("sbatch job.sh --test-only", "job.sh"),
        # Pipelines and quoting.
        ("bash run.sh | tee logs/run.log", "run.sh"),
        ('bash "my run.sh"', "my run.sh"),
        ('"./my run.sh"', "my run.sh"),
    ],
)
def test_runner_option_arity_and_script_arguments(
    tmp_path: Path, command: str, expected_script: str
) -> None:
    (tmp_path / expected_script).write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [
        (tmp_path / expected_script, None)
    ], command


@pytest.mark.parametrize(
    "command",
    [
        'echo "bash run.sh"',
        "echo 'snakemake -c1'",
        'git commit -m "sbatch job.sh"',
        "python -c 'print(\"nextflow run main.nf\")'",
        "bashful run.sh",
        "mysnakemake -c1",
    ],
)
def test_runner_ignores_runner_text_in_arguments(tmp_path: Path, command: str) -> None:
    for name in ("run.sh", "job.sh", "Snakefile", "main.nf"):
        (tmp_path / name).write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [], command


# ---------- runner semantics: option values and execution directory ----------


def _runner_cwds(command: str, cwd: Path):
    return [
        (script, inline, io_cwd)
        for script, inline, io_cwd in lineage_event._detect_scripts_with_cwd(
            command,
            cwd,
            bash_exit=0,
            require_execution_evidence=True,
            include_inline=False,
        )
    ]


@pytest.mark.parametrize(
    "command",
    [
        "snakemake -d work -c1",
        "snakemake --directory work -c1",
        "snakemake --directory=work -c1",
        "snakemake -dwork -c1",
    ],
)
def test_runner_snakemake_directory_sets_io_cwd_not_entry(
    tmp_path: Path, command: str
) -> None:
    """Snakemake resolves the Snakefile before changing into --directory."""
    (tmp_path / "Snakefile").write_text("rule all:\n    input: []\n")
    (tmp_path / "work").mkdir()
    assert _runner_cwds(command, tmp_path) == [
        (tmp_path / "Snakefile", None, tmp_path / "work")
    ]


@pytest.mark.parametrize(
    "command",
    [
        "sbatch -D work job.sh",
        "sbatch --chdir work job.sh",
        "sbatch --chdir=work job.sh",
        "sbatch -Dwork job.sh",
        "sbatch -J x -D work -p short job.sh",
    ],
)
def test_runner_sbatch_chdir_sets_io_cwd_and_script_base(
    tmp_path: Path, command: str
) -> None:
    """Slurm resolves the job script and runs the job under --chdir."""
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "job.sh").write_text("echo hi\n")
    assert _runner_cwds(command, tmp_path) == [
        (tmp_path / "work" / "job.sh", None, tmp_path / "work")
    ]


def test_runner_sbatch_wrap_carries_chdir(tmp_path: Path) -> None:
    (tmp_path / "work").mkdir()
    detections = _runner_cwds('sbatch --chdir work --wrap "python fit.py"', tmp_path)
    assert len(detections) == 1
    assert detections[0][2] == tmp_path / "work"


@pytest.mark.parametrize(
    "command",
    [
        "snakemake --directory $WORK -c1",
        "snakemake -d ~/work -c1",
    ],
)
def test_runner_dynamic_directory_keeps_run_but_not_io_cwd(
    tmp_path: Path, command: str
) -> None:
    """An unknowable run directory is still a run; its I/O cwd is unknown."""
    for name in ("job.sh", "Snakefile"):
        (tmp_path / name).write_text("echo hi\n")
    detections = _runner_cwds(command, tmp_path)
    assert len(detections) == 1, command
    assert detections[0][0] is not None
    assert detections[0][2] is None


def test_main_resolves_runner_io_under_its_directory(tmp_path: Path) -> None:
    import json
    import subprocess

    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    (tmp_path / "Snakefile").write_text(
        "import pandas as pd\npd.read_csv('inputs.csv')\n"
    )
    work = tmp_path / "work"
    work.mkdir()
    (work / "inputs.csv").write_text("a\n1\n")
    target = tmp_path / "events.tmp"

    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-10-11T12:00:00Z",
            "--bash-exit",
            "0",
            "--bash-cmd",
            "snakemake --directory work -c1",
            "--append-to",
            str(target),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    event = json.loads(target.read_text())
    assert event["script"] == str(tmp_path / "Snakefile")
    assert event["inputs"][0]["path"] == str(work / "inputs.csv")
    assert "_missing" not in event["inputs"][0]


def test_runner_event_with_unknown_directory_records_no_io(tmp_path: Path) -> None:
    script = tmp_path / "Snakefile"
    script.write_text("import pandas as pd\npd.read_csv('inputs.csv')\n")
    (tmp_path / "inputs.csv").write_text("a\n1\n")
    args = argparse.Namespace(
        ts="2026-10-11T12:00:00Z",
        agent_id=None,
        agent_type=None,
        bash_cmd="snakemake --directory $WORK -c1",
        bash_exit=0,
    )
    event = build_event_for_detection((script, None), args, None, None)
    assert event is not None
    assert event["script"] == str(script)
    assert event["inputs"] == [] and event["outputs"] == []
    assert event["io_detection"] == "unresolved"
    assert any("working directory" in w for w in event["lineage_warnings"])


@pytest.mark.parametrize(
    "command",
    [
        # Bash modes that parse or print but run no command.
        "bash -D run.sh",
        "bash -eD run.sh",
        "bash --dump-strings run.sh",
        "bash --dump-po-strings run.sh",
        "bash --pretty-print run.sh",
        "bash -o noexec run.sh",
        "bash -euo noexec run.sh",
        "zsh -o no_exec run.sh",
        # Options bash rejects with an error before running anything.
        "bash --bogus run.sh",
        "bash -Z run.sh",
        # Snakemake executor values that run no jobs.
        "snakemake --executor dryrun",
        "snakemake --executor=dryrun -c1",
        "snakemake -e dryrun",
        "snakemake -edryrun",
        "snakemake --executor touch",
        "snakemake --draft-notebook out.txt",
    ],
)
def test_runner_rejects_value_selected_non_executing_modes(
    tmp_path: Path, command: str
) -> None:
    for name in ("run.sh", "Snakefile"):
        (tmp_path / name).write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [], command


@pytest.mark.parametrize(
    "command,expected_script",
    [
        ("bash +o noexec run.sh", "run.sh"),
        ("bash -o pipefail -o errexit run.sh", "run.sh"),
        ("bash --login --noprofile run.sh", "run.sh"),
        ("bash -O extglob run.sh", "run.sh"),
        ("sh -l run.sh", "run.sh"),
        ("zsh -f run.sh", "run.sh"),
        ("dash -e run.sh", "run.sh"),
        ("snakemake --executor slurm -j 10", "Snakefile"),
        ("snakemake -e local -c1", "Snakefile"),
        ("snakemake --executor=cluster-generic -j 4", "Snakefile"),
    ],
)
def test_runner_keeps_executing_option_values(
    tmp_path: Path, command: str, expected_script: str
) -> None:
    (tmp_path / expected_script).write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [
        (tmp_path / expected_script, None)
    ], command


# ---------- runner resolution rules and redirection I/O numbers ----------


def test_runner_sbatch_resolves_script_under_chdir(tmp_path: Path) -> None:
    """Slurm resolves a relative job script against --chdir when given."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "job.sh").write_text("echo hi\n")
    (tmp_path / "job.sh").write_text("echo decoy\n")
    for command in ("sbatch --chdir work job.sh", "sbatch -D work ./job.sh"):
        assert _runner_cwds(command, tmp_path) == [(work / "job.sh", None, work)]
    absolute = tmp_path / "job.sh"
    assert _runner_cwds(f"sbatch -D work {absolute}", tmp_path) == [
        (absolute, None, work)
    ]


@pytest.mark.parametrize(
    "command",
    ["sbatch --chdir=$SCRATCH job.sh", "sbatch -D `pwd`/run job.sh"],
)
def test_runner_sbatch_script_under_dynamic_chdir_is_not_guessed(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "job.sh").write_text("echo decoy\n")
    detections = _runner_cwds(command, tmp_path)
    assert len(detections) == 1
    script, source, io_cwd = detections[0]
    assert script is None and io_cwd is None
    assert "job.sh" in source


@pytest.mark.parametrize(
    "directive",
    [
        "#SBATCH --chdir=work",
        "#SBATCH --chdir work",
        "#SBATCH -D work",
        "#SBATCH -Dwork",
        "#SBATCH -J fit -D work  # run under work",
    ],
)
def test_runner_sbatch_honors_script_chdir_directive(
    tmp_path: Path, directive: str
) -> None:
    (tmp_path / "work").mkdir()
    (tmp_path / "job.sh").write_text(
        f"#!/bin/bash\n#SBATCH -p short\n\n{directive}\npython fit.py\n"
    )
    assert _runner_cwds("sbatch job.sh", tmp_path) == [
        (tmp_path / "job.sh", None, tmp_path / "work")
    ]


def test_runner_sbatch_cli_chdir_overrides_directive(tmp_path: Path) -> None:
    (tmp_path / "cli").mkdir()
    (tmp_path / "cli" / "job.sh").write_text("#!/bin/bash\n#SBATCH -D other\n")
    assert _runner_cwds("sbatch -D cli job.sh", tmp_path) == [
        (tmp_path / "cli" / "job.sh", None, tmp_path / "cli")
    ]


def test_runner_sbatch_ignores_directives_after_first_command(
    tmp_path: Path,
) -> None:
    (tmp_path / "job.sh").write_text(
        "#!/bin/bash\nmodule load R\n#SBATCH -D work\nRscript fit.R\n"
    )
    assert _runner_cwds("sbatch job.sh", tmp_path) == [
        (tmp_path / "job.sh", None, tmp_path)
    ]


def test_runner_sbatch_dynamic_directive_leaves_io_unresolved(
    tmp_path: Path,
) -> None:
    (tmp_path / "job.sh").write_text("#!/bin/bash\n#SBATCH --chdir=$HOME/run\n")
    assert _runner_cwds("sbatch job.sh", tmp_path) == [
        (tmp_path / "job.sh", None, None)
    ]


def test_runner_nextflow_directory_with_manifest_main_script_is_not_guessed(
    tmp_path: Path,
) -> None:
    pipeline = tmp_path / "pipe"
    pipeline.mkdir()
    (pipeline / "main.nf").write_text("workflow {}\n")
    (pipeline / "nextflow.config").write_text(
        "manifest {\n  mainScript = 'pipeline.nf'\n}\n"
    )
    detections = _runner_detections("nextflow run pipe", tmp_path)
    assert len(detections) == 1 and detections[0][0] is None
    assert "pipe" in detections[0][1]


@pytest.mark.parametrize(
    "command,expected_script",
    [
        ("bash 2>err.log run.sh", "run.sh"),
        ("bash 2>&1 run.sh", "run.sh"),
        ("bash 1>out.log 2>err.log run.sh", "run.sh"),
        ("sbatch 2>err.log job.sh", "job.sh"),
        ("sbatch 2>/dev/null -J fit job.sh", "job.sh"),
        ("nextflow 2>err.log run main.nf", "main.nf"),
        ("snakemake 2>err.log -s alt.smk -c1", "alt.smk"),
    ],
)
def test_runner_consumes_redirection_io_numbers(
    tmp_path: Path, command: str, expected_script: str
) -> None:
    (tmp_path / expected_script).write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [
        (tmp_path / expected_script, None)
    ], command


def test_simple_command_argv_io_numbers_only_when_adjacent() -> None:
    argv = lineage_event._simple_command_argv
    assert argv("bash 2>err run.sh") == ["bash", "run.sh"]
    assert argv("bash {fd}>err run.sh") == ["bash", "run.sh"]
    # A separated or quoted digit is an argument, not an I/O number.
    assert argv("cmd 2 >err x") == ["cmd", "2", "x"]
    assert argv('cmd "2">err x') == ["cmd", "2", "x"]
    assert argv("cmd a2>err x") == ["cmd", "a2", "x"]
    assert argv("cmd x 2>&1") == ["cmd", "x"]


@pytest.mark.parametrize(
    "command",
    [
        "bash -o definitely_not_an_option run.sh",
        "bash -O definitely_not_an_option run.sh",
        "bash +O definitely_not_an_option run.sh",
        "sh -o definitely_not_an_option run.sh",
    ],
)
def test_runner_rejects_invalid_shell_option_names(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "run.sh").write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [], command


@pytest.mark.parametrize(
    "command",
    [
        "bash -o errexit -o nounset -o pipefail run.sh",
        "bash -o interactive-comments run.sh",
        "bash -O extglob -O globstar -O nullglob run.sh",
        "bash -O inherit_errexit run.sh",
        "sh -o errexit run.sh",
        "zsh -o extended_glob run.sh",
    ],
)
def test_runner_accepts_valid_shell_option_names(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "run.sh").write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [
        (tmp_path / "run.sh", None)
    ], command


def _snakemake_tree(tmp_path: Path) -> None:
    (tmp_path / "Snakefile").write_text("rule all:\n    input: []\n")


def _profile(directory: Path, body: str, name: str = "config.yaml") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body)


@pytest.mark.parametrize(
    "location,name",
    [
        ("profiles/default", "config.yaml"),
        ("profiles/default", "profile.yaml"),
        ("workflow/profiles/default", "config.v8+.yaml"),
    ],
)
def test_runner_snakemake_default_profile_directory_is_not_guessed(
    tmp_path: Path, location: str, name: str
) -> None:
    _snakemake_tree(tmp_path)
    if location.startswith("workflow/"):
        (tmp_path / "Snakefile").unlink()
        (tmp_path / "workflow").mkdir()
        (tmp_path / "workflow" / "Snakefile").write_text("rule all:\n    input: []\n")
    _profile(tmp_path / location, "executor: slurm\ndirectory: /scratch/run\n", name)
    detections = _runner_cwds("snakemake -j 10", tmp_path)
    assert len(detections) == 1 and detections[0][0] is not None
    assert detections[0][2] is None


def test_runner_snakemake_profile_without_directory_keeps_cwd(
    tmp_path: Path,
) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", "executor: slurm\njobs: 10\n")
    assert _runner_cwds("snakemake", tmp_path) == [
        (tmp_path / "Snakefile", None, tmp_path)
    ]


def test_runner_snakemake_cli_directory_overrides_profile(tmp_path: Path) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", "directory: elsewhere\n")
    assert _runner_cwds("snakemake -d work -c1", tmp_path) == [
        (tmp_path / "Snakefile", None, tmp_path / "work")
    ]


def test_runner_snakemake_workflow_profile_none_disables_default(
    tmp_path: Path,
) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", "directory: elsewhere\n")
    assert _runner_cwds("snakemake --workflow-profile none -c1", tmp_path) == [
        (tmp_path / "Snakefile", None, tmp_path)
    ]


def test_runner_snakemake_explicit_profiles(tmp_path: Path, monkeypatch) -> None:
    _snakemake_tree(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    _profile(tmp_path / "local", "directory: elsewhere\n")
    _profile(tmp_path / "quiet", "jobs: 4\n")
    _profile(tmp_path / "home/.config/snakemake/slurm", "executor: slurm\n")

    def io_cwd(command: str) -> Path | None:
        detections = _runner_cwds(command, tmp_path)
        assert len(detections) == 1, command
        return detections[0][2]

    assert io_cwd("snakemake --profile local") is None
    assert io_cwd("snakemake --profile=quiet") == tmp_path
    assert io_cwd("snakemake --profile slurm") == tmp_path
    assert io_cwd("snakemake --workflow-profile local") is None
    # A named profile that cannot be found here may set anything.
    assert io_cwd("snakemake --profile cluster") is None


def test_runner_snakemake_profile_snakefile_is_not_guessed(tmp_path: Path) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", "snakefile: workflow/main.smk\n")
    detections = _runner_cwds("snakemake -c1", tmp_path)
    assert len(detections) == 1 and detections[0][0] is None
    assert "profile" in detections[0][1]
    assert detections[0][2] == tmp_path
    # An explicit --snakefile still wins over the profile.
    (tmp_path / "alt.smk").write_text("rule all:\n    input: []\n")
    assert _runner_cwds("snakemake -s alt.smk -c1", tmp_path) == [
        (tmp_path / "alt.smk", None, tmp_path)
    ]


# ---------- round 3: environment-selected profiles and grammar gaps ----------


@pytest.fixture
def clean_profile_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("SNAKEMAKE_PROFILE", raising=False)
    return monkeypatch


def test_runner_snakemake_env_profile_is_honored(
    tmp_path: Path, clean_profile_env
) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "moved", "directory: elsewhere\n")
    _profile(tmp_path / "plain", "jobs: 4\n")

    def io_cwd(command: str) -> Path | None:
        detections = _runner_cwds(command, tmp_path)
        assert len(detections) == 1, command
        return detections[0][2]

    clean_profile_env.setenv("SNAKEMAKE_PROFILE", "moved")
    assert io_cwd("snakemake -c1") is None
    # --profile on the command line replaces the environment variable.
    assert io_cwd("snakemake --profile plain -c1") == tmp_path
    assert io_cwd("snakemake --profile none -c1") == tmp_path
    clean_profile_env.delenv("SNAKEMAKE_PROFILE")
    # A prefix assignment or `env` sets it for this command only.
    assert io_cwd("SNAKEMAKE_PROFILE=moved snakemake -c1") is None
    assert io_cwd("env SNAKEMAKE_PROFILE=moved snakemake -c1") is None
    assert io_cwd("SNAKEMAKE_PROFILE=plain snakemake -c1") == tmp_path
    # An assignment the parser cannot follow is not guessed.
    assert io_cwd("export SNAKEMAKE_PROFILE=moved; snakemake -c1") is None
    assert io_cwd("SNAKEMAKE_PROFILE=$P snakemake -c1") is None


def test_runner_snakemake_workflow_profile_checks_every_candidate(
    tmp_path: Path, clean_profile_env
) -> None:
    _snakemake_tree(tmp_path)
    (tmp_path / "default").mkdir()
    _profile(tmp_path / "profiles/default", "directory: elsewhere\n")
    detections = _runner_cwds("snakemake -c1", tmp_path)
    assert len(detections) == 1 and detections[0][2] is None


@pytest.mark.parametrize(
    "body",
    [
        "executor: dryrun\n",
        "executor: 'touch'\n",
        "dry-run: true\n",
        "dryrun: True\n",
        "touch: yes\n",
        "lint: true\n",
    ],
)
def test_runner_snakemake_profile_non_executing_modes(
    tmp_path: Path, clean_profile_env, body: str
) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", body)
    assert _runner_detections("snakemake -c1", tmp_path) == []


@pytest.mark.parametrize(
    "body",
    ["executor: slurm\n", "dry-run: false\n", "touch: no\n", "jobs: 10\n"],
)
def test_runner_snakemake_profile_executing_settings(
    tmp_path: Path, clean_profile_env, body: str
) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", body)
    assert _runner_detections("snakemake -c1", tmp_path) == [
        (tmp_path / "Snakefile", None)
    ]


def test_runner_snakemake_cli_executor_overrides_profile(
    tmp_path: Path, clean_profile_env
) -> None:
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", "executor: dryrun\n")
    assert _runner_detections("snakemake --executor slurm -j 4", tmp_path) == [
        (tmp_path / "Snakefile", None)
    ]


def test_runner_bash_falls_back_to_path_lookup(tmp_path: Path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "job.sh").write_text("echo hi\n")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("PATH", f"{tmp_path / 'empty'}:{bin_dir}")
    assert _runner_detections("bash job.sh", work) == [(bin_dir / "job.sh", None)]
    # The current directory wins over PATH.
    (work / "job.sh").write_text("echo local\n")
    assert _runner_detections("bash job.sh", work) == [(work / "job.sh", None)]
    # A PATH this command changes is not guessed from the hook's PATH.
    (work / "job.sh").unlink()
    assert _runner_detections(f"PATH=/nowhere bash job.sh", work) == [
        (work / "job.sh", None)
    ]
    # Only bash searches PATH for its script operand.
    assert _runner_detections("sh job.sh", work) == [(work / "job.sh", None)]


@pytest.mark.parametrize("command", ["bash - run.sh", "bash -e - run.sh", "sh - run.sh"])
def test_runner_lone_dash_ends_shell_options(tmp_path: Path, command: str) -> None:
    (tmp_path / "run.sh").write_text("echo hi\n")
    assert _runner_detections(command, tmp_path) == [(tmp_path / "run.sh", None)]


@pytest.mark.parametrize(
    "command",
    [
        "sbatch --wrap 'echo hi' job.sh",
        "sbatch --wrap='echo hi' job.sh arg",
        "sbatch job.sh --wrap 'echo hi'",
    ],
)
def test_runner_sbatch_wrap_with_script_is_an_error(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "job.sh").write_text("echo hi\n")
    if command.startswith("sbatch job.sh"):
        # After the script, `--wrap` is the job's own argument.
        assert _runner_detections(command, tmp_path) == [(tmp_path / "job.sh", None)]
    else:
        assert _runner_detections(command, tmp_path) == []


def test_runner_snakemake_conflicting_profiles_keep_the_run(
    tmp_path: Path, clean_profile_env
) -> None:
    """Merge order across profiles is not modelled, so a non-executing value
    decides only when every active profile agrees."""
    _snakemake_tree(tmp_path)
    _profile(tmp_path / "profiles/default", "executor: dryrun\ndry-run: true\n")
    _profile(tmp_path / "hpc", "executor: slurm\ndry-run: false\n")
    assert _runner_detections("snakemake --profile hpc", tmp_path) == [
        (tmp_path / "Snakefile", None)
    ]


# ---------- R command-line grammar: inline, file, and BATCH forms ----------


def _run_check(tmp_path: Path, command: str, mode: str) -> int:
    import subprocess

    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    result = subprocess.run(
        [
            sys.executable,
            str(extractor),
            "--cwd",
            str(tmp_path),
            "--ts",
            "2026-11-06T00:00:00",
            "--bash-cmd",
            command,
            "--bash-exit",
            "0",
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stderr == "", result.stderr
    return result.returncode


_R_INLINE_ONE_LINERS = [
    "R -e 'print(1)'",
    "R --vanilla -e 'print(1)'",
    "R --vanilla -q -e 'print(1)'",
    "R -q -e 'print(1)'",
    "R -s -e 'print(1)'",
    "R --no-echo --quiet -e 'print(1)'",
    "R -q --vanilla -s -e 'print(1)'",
    "R --encoding UTF-8 -e 'print(1)'",
    "R --encoding=UTF-8 -q -e 'print(1)'",
    # Separated-value options and short clusters R accepts (checked on R 4.6).
    "R --max-ppsize 10000 -e 'print(1)'",
    "R --gui X11 -q -e 'print(1)'",
    "R -qs -e 'print(1)'",
    # A trailing comment is not shell syntax, whatever it contains.
    "R -e 'print(1)' # (quick check)",
    "R -e 'print(1)' # it's fine",
    "R -e 'print(1)' --args a b",
    "R -q -e 'print(1)' --args --version",
    "R -e 'x <- 1' -e 'print(x)'",
    "/usr/local/bin/R -q -e 'print(1)'",
    "Rscript -e 'print(1)'",
    "Rscript --vanilla -e 'print(1)'",
    "Rscript -e 'print(1)' --version",
    "python3 -c 'print(1)'",
]

_R_SCRIPT_RUNS = [
    "Rscript script.R",
    "Rscript --vanilla script.R",
    "R -f script.R",
    "R --vanilla -q -f script.R",
    "R -s --file=script.R",
    'R -s --file="script.R" --args a b',
    "R -f script.R --args --help",
    "R CMD BATCH script.R",
    "R CMD BATCH --vanilla --no-timing script.R script.Rout",
    "R CMD BATCH -q --no-save script.R",
    # Real R 4.6 runs each of these.
    "R --max-ppsize 10000 -f script.R",
    "R --min-vsize 10M --gui X11 -f script.R",
    "R -qs -f script.R",
    "R -f script.R # (run)",
    "R -f script.R # it's the run",
    "R CMD BATCH --args script.R",
    "R CMD BATCH --args --print-home script.R",
    # Quoted or escaped punctuation is an argument, not shell syntax.
    "R -f script.R --args ')' '>' \\;",
    "R -f script.R \\\n# it's a comment line",
    "R -f script.R --args $(date +%F)",
    "R CMD BATCH -- script.R",
    "R CMD BATCH -g X11 --vanilla script.R",
    "R --vanilla CMD BATCH script.R",
    "R -q -e 'print(1)' && Rscript script.R",
]


@pytest.mark.parametrize("command", _R_INLINE_ONE_LINERS)
def test_post_action_exempts_every_inline_r_one_liner(
    tmp_path: Path, command: str
) -> None:
    """`R [opts] -e` is a one-liner like `Rscript -e` and `python -c`, whatever
    startup options precede the expression or trail it."""
    assert _run_check(tmp_path, command, "--check-post-action") == 1, command


@pytest.mark.parametrize("command", _R_SCRIPT_RUNS)
def test_post_action_fires_for_r_script_runs(tmp_path: Path, command: str) -> None:
    (tmp_path / "script.R").write_text("read.csv('input.csv')\n")
    assert _run_check(tmp_path, command, "--check-post-action") == 0, command


@pytest.mark.parametrize("command", _R_INLINE_ONE_LINERS[:-1])
def test_inline_r_one_liners_are_still_recorded_as_lineage(
    tmp_path: Path, command: str
) -> None:
    """Exempting one-liners from post-action must not hide them from lineage:
    short startup options before `-e` are parsed, not a reason to drop it."""
    assert _run_check(tmp_path, command, "--check-execution") == 0, command
    detections = detect_scripts(command, tmp_path)
    assert detections and all(script is None for script, _ in detections), command


@pytest.mark.parametrize(
    "command,expected_source",
    [
        ("R -q -e \"read.csv('x.csv')\"", "read.csv('x.csv')"),
        ("R --vanilla -s -e \"read.csv('x.csv')\" --args 1", "read.csv('x.csv')"),
        ("R --encoding UTF-8 -e \"read.csv('x.csv')\"", "read.csv('x.csv')"),
        ("Rscript -e \"read.csv('x.csv')\"", "read.csv('x.csv')"),
        ("Rscript --vanilla -e \"read.csv('x.csv')\"", "read.csv('x.csv')"),
    ],
)
def test_inline_r_source_is_the_expression_not_an_option_value(
    command: str, expected_source: str
) -> None:
    assert detect_scripts(command, Path("/tmp")) == [(None, expected_source)]


@pytest.mark.parametrize("command", _R_SCRIPT_RUNS[:-1])
def test_r_script_runs_record_the_script_path(tmp_path: Path, command: str) -> None:
    (tmp_path / "script.R").write_text("read.csv('input.csv')\n")
    assert detect_scripts(command, tmp_path) == [(tmp_path / "script.R", None)]


@pytest.mark.parametrize(
    "command",
    [
        # Terminal options stop R anywhere before `--args`, even after the payload.
        "R -e 'print(1)' --version",
        "R -q -e 'print(1)' -h",
        "R -f script.R --help",
        "R --file=script.R --version",
        # `--args` ends option parsing: a later `-e`/`-f` is never run.
        "R --args -e 'print(1)'",
        "R --args -f script.R",
        # `-d` hands the remaining argv to a debugger.
        "R -d lldb -e 'print(1)'",
        "R -e 'print(1)' -d lldb",
        "R --debugger=gdb -f script.R",
        "R -f script.R --debugger lldb",
        # These only print R_HOME, or hand the rest to Rcmd.
        "R --print-home -f script.R",
        "R -f script.R RHOME",
        "R -f script.R CMD INSTALL pkg",
        # R refuses to combine an expression with an input file.
        "R -e 'print(1)' -f script.R",
        # Spellings R rejects or misreads.
        "R --file script.R",
        "Rscript -q script.R",
        "Rscript -q -e 'print(1)'",
        # Non-executing R CMD tools and mere mentions.
        "R CMD INSTALL pkg",
        "R CMD BATCH --help script.R",
        "R CMD BATCH -v script.R",
        "R CMD BATCH -d lldb script.R",
        "R CMD BATCH --debugger=lldb script.R",
        "R CMD BATCH --print-home script.R",
        "R CMD BATCH --args --version script.R",
        'R CMD BATCH "--debugger=lldb" script.R',
        'R CMD BATCH "--vanilla --help" script.R',
        'R CMD BATCH "--vanilla --version" script.R',
        # R refuses -e alongside BATCH's own -f (checked on R 4.6).
        'R CMD BATCH "-e print(1)" script.R',
        # BATCH forwards a bare -e, which R refuses alongside -f.
        "R CMD BATCH -e script.R",
        'R -e "print(1) # note" --version',
        # R exits when -e or -f lacks its value.
        "R -e 1 -f",
        # R refuses -e with -f even when the expression is computed.
        'R -f script.R -e "$CODE"',
        "R -f script.R -e",
        "R CMD BATCH --no-timing",
        # A terminal option before `--args` still wins over a trailing $(...).
        "R -f script.R --version --args $(date)",
        # `--args'x'` is the single word `--argsx`, not the --args boundary.
        "R --vanilla -f script.R --args'ignored' --print-home",
        "R -f script.R \\\n  --version",
        "Rscript --args -e 'print(1)'",
        "Rscript --vanilla --args script.R",
        "Rscript --print-home -e 'print(1)'",
        "Rscript --vanilla --print-home script.R",
        "R CMD check pkg_1.0.tar.gz",
        "echo \"R -q -e 'print(1)'\"",
        "echo 'R -f script.R'",
        "MyR -f script.R",
    ],
)
def test_r_non_executing_forms_record_nothing(tmp_path: Path, command: str) -> None:
    (tmp_path / "script.R").write_text("read.csv('input.csv')\n")
    assert detect_scripts(command, tmp_path) == [], command
    assert _run_check(tmp_path, command, "--check-execution") == 1, command
    assert _run_check(tmp_path, command, "--check-post-action") == 1, command


@pytest.mark.parametrize(
    "command,expected_source",
    [
        # Rscript runs consecutive -e pairs; R runs every -e before --args.
        (
            "Rscript -e 'library(utils)' -e \"read.csv('x.csv')\"",
            "library(utils)\nread.csv('x.csv')",
        ),
        (
            "R -q -e 'library(utils)' --vanilla -e \"read.csv('x.csv')\" --args -e z",
            "library(utils)\nread.csv('x.csv')",
        ),
        # Rscript stops at the first word after its -e pairs (checked on R 4.6).
        ("Rscript -e 'a' --vanilla -e 'b'", "a"),
        # A backslash-newline continuation is removed by the shell.
        ("R -q -e 'a' \\\n  --args foo", "a"),
        ("R -e 'print(1)' --args ')' \"<\"", "print(1)"),
        ("R -q -e 'a' \\\n# it's a probe", "a"),
        # Words after `--args` are R's arguments, whatever shell syntax they use.
        ("R --no-save -e 'a' --args $(date)", "a"),
        ("R -e 'a' -e 'b' --args \"$(date)\" -e c", "a\nb"),
        # An `--args` in a neighboring command or a redirection target is not R's.
        ("R -e 'print(1)'; printf %s --args", "print(1)"),
        ("R -e 'print(1)' && echo --args", "print(1)"),
        ("R -e 'a' > --args", "a"),
        # Shell syntax the argv scan cannot read keeps the proven payload.
        ("R -e 'print(1)' > >(cat)", "print(1)"),
        # Named file descriptors are redirection syntax, not argv words.
        ("Rscript -e 'a' {fd}>&1 -e 'b'", "a\nb"),
        ("R -e 'a' {log}>run.log -e 'b'", "a\nb"),
        # A `#` inside double quotes is literal, not a comment.
        ('R -e "print(1) # note" -e "print(2)"', "print(1) # note\nprint(2)"),
        # A computed expression is not presented as executed R source.
        ("R -e 'print(1)' -e $(cat analysis.R)", "print(1)"),
        # `$ARGS` may expand to --args: a later -e may never run.
        ("ARGS=--args; R -e 'print(1)' $ARGS -e 'stop(1)'", "print(1)"),
    ],
)
def test_inline_r_source_keeps_every_expression(
    command: str, expected_source: str
) -> None:
    assert detect_scripts(command, Path("/tmp")) == [(None, expected_source)]


def test_escaped_space_before_hash_is_not_a_comment(tmp_path: Path) -> None:
    """`run\\ #1.sh` is one word: `#` there does not start a comment."""
    (tmp_path / "run #1.sh").write_text("echo hi\n")
    assert detect_scripts("bash run\\ #1.sh", tmp_path) == [
        (tmp_path / "run #1.sh", None)
    ]


@pytest.mark.parametrize(
    "command",
    [
        # R runs the last file option before --args (checked on R 4.6).
        "R -q -f first.R --file=second.R",
        "R -f first.R -f second.R",
        "R -f first.R -f second.R --args -f third.R",
        # BATCH appends a forwarded --file= after `-f infile`.
        "R CMD BATCH --file=second.R first.R",
        'R CMD BATCH "--file=second.R" first.R',
        "R CMD BATCH '--args x=1' second.R",
        'R CMD BATCH "--vanilla --args x=1" -- second.R out.Rout',
        "R CMD BATCH second.R $(date +%F).Rout",
        "R CMD BATCH --file=second.R first.R $(date +%F).Rout",
        # BATCH expands its input unquoted: R reads the first word.
        'R CMD BATCH "second.R first.R"',
        "env R CMD BATCH second.R $(date).Rout",
        "R CMD BATCH second.R > >(cat)",
    ],
)
def test_r_records_the_file_r_actually_runs(tmp_path: Path, command: str) -> None:
    for name in ("first.R", "second.R", "third.R"):
        (tmp_path / name).write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == [(tmp_path / "second.R", None)]


def _assert_unresolved(detections: list, candidate: str) -> None:
    assert [
        (type(script), getattr(script, "candidate", None), inline)
        for script, inline in detections
    ] == [(UnresolvedScript, candidate, None)], detections


@pytest.mark.parametrize(
    "command",
    [
        # BATCH appends $R_BATCH_OPTIONS. Only a literal assignment directly
        # on the command is read; any other mention keeps the run but leaves
        # its script unresolved.
        "env R_BATCH_OPTIONS='--vanilla --file=second.R' R CMD BATCH script.R",
        "export R_BATCH_OPTIONS=--file=second.R; R CMD BATCH script.R",
        "R_BATCH_OPTIONS=--version env -u R_BATCH_OPTIONS R CMD BATCH script.R",
        'R_BATCH_OPTIONS="$OPTS" R CMD BATCH script.R',
        "R_BATCH_OPTIONS=-q; R CMD BATCH script.R",
    ],
)
def test_batch_that_mentions_batch_options_is_unresolved(
    tmp_path: Path, command: str
) -> None:
    for name in ("script.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    _assert_unresolved(detect_scripts(command, tmp_path), "script.R")
    assert _run_check(tmp_path, command, "--check-post-action") == 0, command


def test_batch_ignores_the_hook_process_batch_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook's own environment is not the agent shell's, so it is not read."""
    monkeypatch.setenv("R_BATCH_OPTIONS", "--file=other.R")
    for name in ("first.R", "other.R"):
        (tmp_path / name).write_text("print(1)\n")
    assert detect_scripts("R CMD BATCH first.R", tmp_path) == [
        (tmp_path / "first.R", None)
    ]


@pytest.mark.parametrize(
    ("command", "candidate"),
    [
        # Words past unreadable syntax may override the file or stop R, so the
        # run is kept but the script is not guessed.
        ("R -f first.R $(printf ignored) --file=second.R", "first.R"),
        ("R -f first.R $(printf ignored) --print-home", "first.R"),
        # The shell picks the file: record the run, not a guessed file.
        ('R -f first.R --file="$SCRIPT"', "$SCRIPT"),
        ("R -f first.R --file=`pick`", "`pick`"),
        ('R -f "$DIR/run.R"', "$DIR/run.R"),
        # A variable among R's options may expand to another --file=.
        ("OPTS=--file=second.R; R -f first.R $OPTS", "first.R"),
        # A computed option value may split into words that override a
        # later --file= (`actual.R --args`), so the scan stops there.
        (
            "R -f first.R -f $(printf 'a.R --args') --file=second.R",
            "$(printf 'a.R --args')",
        ),
        # BATCH forwards -g/-d values unquoted, so a computed one may add
        # `--file=`.
        ('R CMD BATCH -g "$GUI" first.R', "first.R"),
        # A process substitution is a pipe, not a file R can name.
        ("R -f first.R -f <(cat second.R)", "<(cat second.R)"),
        ("R -f first.R -f <(cat)", "<(cat)"),
        # An unquoted computed value before the file option may split into
        # more words, so it still leaves an unresolved run.
        ("R --encoding $ENC -f first.R", "first.R"),
    ],
)
def test_r_file_run_with_unreadable_later_words_is_unresolved(
    tmp_path: Path, command: str, candidate: str
) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    _assert_unresolved(detect_scripts(command, tmp_path), candidate)


@pytest.mark.parametrize(
    ("command", "script"),
    [
        # A redirection target never reaches argv, so a target the scanner
        # cannot tokenize must not make the script unresolved.
        ("R -f first.R > run_$(date +%s).log", "first.R"),
        ("R -f first.R 2> >(tee err.log) --vanilla", "first.R"),
        ("R -f first.R --file=second.R > >(tee run.log)", "second.R"),
        ("R -f first.R > >(tee run.log) --file=second.R", "second.R"),
        ("R -f first.R &> run_$(date).log", "first.R"),
        ("R CMD BATCH first.R > batch_$(date).log", "first.R"),
        # A line continuation before the target is not the target.
        ("R -f first.R > \\\n  --file=second.R", "first.R"),
    ],
)
def test_unreadable_redirection_target_keeps_the_r_script(
    tmp_path: Path, command: str, script: str
) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == [(tmp_path / script, None)]


def test_unresolved_r_run_event_names_the_candidate_without_hashing(
    tmp_path: Path,
) -> None:
    """An uncertain script is not read or hashed: the event keeps the run,
    the candidate file word, and a warning, never a hash of prose."""
    (tmp_path / "first.R").write_text("read.csv('input.csv')\n")
    command = "R -f first.R $(printf x)"
    [detection] = detect_scripts(command, tmp_path)
    args = argparse.Namespace(
        ts="2026-11-13T00:00:00Z", agent_id=None, agent_type=None,
        bash_cmd=command, bash_exit=0,
    )
    event = build_event_for_detection(detection, args, tmp_path, "abc123")
    assert event["script"] is None
    assert event["script_candidate"] == "first.R"
    assert event["script_sha256"] is None and event["script_source"] is None
    assert event["io_detection"] == "unresolved"
    assert event["inputs"] == [] and event["outputs"] == []
    assert event["lineage_warnings"]


@pytest.mark.parametrize(
    "command",
    [
        # A comment or `${...}` inside a substitution target has quoting this
        # scan does not model, so the later words stay unknown.
        "R -f first.R > $(echo log # )\n) --file=second.R",
        'R -f first.R > $(echo "${x:-")"}") --file=second.R',
        # `case` patterns and heredocs carry unbalanced parentheses.
        "R -f first.R >$(case x in x) echo /dev/null;; e\\\nsac) --file=second.R",
        "R -f first.R >$(case x in x) esac) --file=second.R",
        "R -f first.R >$(cat <\\\n<EOF\n(\nEOF\n) --file=second.R",
    ],
)
def test_unmodelled_redirection_target_stays_unresolved(
    tmp_path: Path, command: str
) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    _assert_unresolved(detect_scripts(command, tmp_path), "first.R")


@pytest.mark.parametrize(
    ("command", "script"),
    [
        ("python a\\.py", "a.py"),
        ("Rscript a\\.R", "a.R"),
        ("jupyter execute a\\.ipynb", "a.ipynb"),
        ("\\python a.py", "a.py"),
        ("/venv/bin/\\python a.py", "a.py"),
        ("Rscript b.R \\  python", "b.R"),
    ],
)
def test_escaped_ordinary_characters_still_match(
    tmp_path: Path, command: str, script: str
) -> None:
    assert detect_scripts(command, tmp_path) == [(tmp_path / script, None)]


def test_backslash_runs_do_not_backtrack_exponentially() -> None:
    """Both bare-word alternatives used to match a backslash, so `R ` plus
    40 backslashes took hours to reject."""
    import time

    # Exponential, not merely slow: 20 backslashes against 40 must cost about
    # the same (2^20 times more before the fix), measured as CPU.
    def cpu(count: int) -> float:
        started = time.process_time()
        assert not is_analysis("R " + "\\" * count + "x")
        return time.process_time() - started

    pairs = [(cpu(20), cpu(40)) for _ in range(3)]  # interleaved against load spikes
    small = min(pair[0] for pair in pairs)
    large = min(pair[1] for pair in pairs)
    assert large < 60
    assert large < max(small * 3.2, 0.5), (small, large)


def test_quoted_suffix_on_args_is_not_the_boundary(tmp_path: Path) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    command = "R --vanilla -f first.R --args'ignored' --file=second.R"
    assert detect_scripts(command, tmp_path) == [(tmp_path / "second.R", None)]


def test_batch_file_override_from_a_variable_is_unresolved(tmp_path: Path) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    _assert_unresolved(
        detect_scripts('R CMD BATCH --file="$SCRIPT" first.R', tmp_path), "first.R"
    )


def test_expansion_among_r_options_keeps_the_payload() -> None:
    """`$ARGS` may expand to `--args`, so `-e` with a later `-f` is not
    proven to be R's refused combination."""
    assert detect_scripts(
        "ARGS=--args; R -e 'print(1)' $ARGS -f script.R", Path("/tmp")
    ) == [(None, "print(1)")]


@pytest.mark.parametrize("operator", [">|", ">&", ">>", "&>", "<"])
def test_args_as_a_redirection_target_is_not_the_boundary(
    tmp_path: Path, operator: str
) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    command = f"R -f first.R {operator} --args --file=second.R"
    assert detect_scripts(command, tmp_path) == [(tmp_path / "second.R", None)]


def test_batch_input_keeps_a_nonbreaking_space(tmp_path: Path) -> None:
    name = "sample\u00a01.R"
    (tmp_path / name).write_text("print(1)\n")
    assert detect_scripts(f"R CMD BATCH '{name}'", tmp_path) == [
        (tmp_path / name, None)
    ]


@pytest.mark.parametrize(
    "command",
    [
        r"R CMD BATCH my\ file.R",
        'R CMD BATCH "my file.R"',
        "R CMD BATCH 'my file.R'",
        "R CMD BATCH 'my file.R' 'out file.Rout'",
    ],
)
def test_batch_never_runs_an_input_with_a_space(
    tmp_path: Path, command: str
) -> None:
    """R's BATCH script runs `R -f ${in}` unquoted, so `my file.R` becomes
    `-f my file.R` and R reads `my` (checked on R 4.6): nothing runs the
    named script. `R -f 'my file.R'` does run it."""
    (tmp_path / "my file.R").write_text("print(1)\n")
    (tmp_path / "first.R").write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == []
    assert detect_scripts("R CMD BATCH first.R", tmp_path) == [
        (tmp_path / "first.R", None)
    ]
    assert detect_scripts("R -f 'my file.R'", tmp_path) == [
        (tmp_path / "my file.R", None)
    ]


def test_batch_candidate_does_not_cross_command_boundaries(tmp_path: Path) -> None:
    (tmp_path / "script.R").write_text("print(1)\n")
    for joiner in ("&&", ";", "||", "\n"):
        command = f"R CMD BATCH --help {joiner} R CMD BATCH script.R"
        assert detect_scripts(command, tmp_path) == [
            (tmp_path / "script.R", None)
        ], command


def test_batch_option_expansion_is_unresolved(tmp_path: Path) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    command = 'OPTS=--args; R CMD BATCH "$OPTS --file=second.R" first.R'
    _assert_unresolved(detect_scripts(command, tmp_path), "first.R")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A `$` in single quotes is literal, so the word is known.
        ("R -f 'cost$model.R'", "path"),
        # One expansion rule for every R form: a computed word ends the argv.
        ("R -q -e 'df$x' $F -f script.R", "inline"),
        # Inside double quotes, bash removes the backslash of `\\$`.
        ('R -f "cost\\$model.R"', "path"),
    ],
)
def test_single_quoted_dollar_is_not_an_expansion(
    tmp_path: Path, command: str, expected: str
) -> None:
    (tmp_path / "cost$model.R").write_text("print(1)\n")
    detections = detect_scripts(command, tmp_path)
    if expected == "path":
        assert detections == [(tmp_path / "cost$model.R", None)]
    else:
        assert detections == [(None, "df$x")]


# ---------- PR #121 review regressions ----------


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A fully double-quoted computed value is exactly one word, so the
        # words after it are still read (R 4.6 behavior per review).
        ('R -f "$D/a.R" --version', []),
        ("R -f \"$D/a.R\" -e 'x'", []),
        ('R --encoding "$ENC" -f first.R --version', []),
        ('R -f "$D/a.R" -f second.R', ["second.R"]),
        ('R --encoding "$ENC" -f first.R', ["first.R"]),
        ('R -e "$CODE" -f first.R', []),
        ('R -e "$CODE" --version', []),
        ("R -e 'print(1)' -g \"$G\" --version", []),
    ],
)
def test_quoted_computed_value_is_one_unknown_word(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == [
        (tmp_path / name, None) for name in expected
    ]


def test_quoted_computed_file_value_stays_unresolved(tmp_path: Path) -> None:
    _assert_unresolved(detect_scripts('R -f "$D/a.R"', tmp_path), "$D/a.R")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ('R -f first.R > "${LOG}"', ["first.R"]),
        ("R -f first.R > ${LOG}", ["first.R"]),
        ('R -f first.R 2>"${LOG:-x.log}"', ["first.R"]),
        ('R -f first.R >"${LOG}" --version', []),
    ],
)
def test_braced_redirection_target_does_not_hide_later_words(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == [
        (tmp_path / name, None) for name in expected
    ]


@pytest.mark.parametrize(
    ("command", "source"),
    [
        (
            "LOG=/dev/stdout; Rscript -e 'cat(1)' >\"${LOG}\" -e 'cat(2)'",
            "cat(1)\ncat(2)",
        ),
        # `&>` takes no descriptor: bash passes the `2` (or `{fd}`) as an
        # argument, so Rscript stops collecting expressions there.
        ("Rscript -e '1' 2&>/dev/null -e 'stop(9)'", "1"),
        ("Rscript -e '1' {fd}&>/dev/null -e 'stop(9)'", "1"),
        ("Rscript -e '1' 2&>>/dev/null -e 'stop(9)'", "1"),
        # Bash joins a continuation before it reads operators: `2\<nl>>&1`
        # is the redirection `2>&1`.
        ("Rscript -e 'cat(1)' 2\\\n>&1 -e 'cat(2)'", "cat(1)\ncat(2)"),
        ("Rscript -e 'cat(1)' {fd}\\\n>/dev/null -e 'cat(2)'", "cat(1)\ncat(2)"),
    ],
)
def test_rscript_redirection_forms(command: str, source: str) -> None:
    assert detect_scripts(command, Path("/tmp")) == [(None, source)]


def _unresolved_event(command: str, cwd: Path) -> dict:
    [detection] = detect_scripts(command, cwd)
    args = argparse.Namespace(
        ts="2026-11-13T00:00:00Z", agent_id=None, agent_type=None,
        bash_cmd=command, bash_exit=0,
    )
    return build_event_for_detection(detection, args, cwd, "abc123")


def test_unresolved_r_file_message_does_not_blame_batch_options(
    tmp_path: Path,
) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    [warning] = _unresolved_event("R -f first.R $(printf x)", tmp_path)[
        "lineage_warnings"
    ]
    assert "R_BATCH_OPTIONS" not in warning
    assert "first.R" in warning and "'first.R'" not in warning


def test_unresolved_batch_message_names_batch_options(tmp_path: Path) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    [warning] = _unresolved_event(
        "export R_BATCH_OPTIONS=-q; R CMD BATCH first.R", tmp_path
    )["lineage_warnings"]
    assert "R_BATCH_OPTIONS" in warning and "first.R" in warning


@pytest.mark.parametrize(
    "command", ["Rscript first.R", "R -f first.R", "Rscript -e 'print(1)'"]
)
def test_resolved_event_has_no_script_candidate(tmp_path: Path, command: str) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    [detection] = detect_scripts(command, tmp_path)
    args = argparse.Namespace(
        ts="2026-11-13T00:00:00Z", agent_id=None, agent_type=None,
        bash_cmd=command, bash_exit=0,
    )
    event = build_event_for_detection(detection, args, tmp_path, "abc123")
    assert "script_candidate" in event and event["script_candidate"] is None


@pytest.mark.parametrize(
    "command",
    ['R -e "$CODE"', "R -e $CODE", 'Rscript -e "$CODE"', "R -q -e `cat x.R`"],
)
def test_computed_expression_is_never_recorded_as_source(command: str) -> None:
    assert detect_scripts(command, Path("/tmp")) == []


@pytest.mark.parametrize(
    "command",
    [
        # Forwarded stop options are proven whatever R_BATCH_OPTIONS adds.
        "R_BATCH_OPTIONS=-q R CMD BATCH -e first.R",
        "R_BATCH_OPTIONS=-q R CMD BATCH --print-home first.R",
        'R CMD BATCH -e "$IN"',
    ],
)
def test_batch_forwarded_stop_options_beat_unresolved(
    tmp_path: Path, command: str
) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == []


def test_batch_after_unreadable_startup_value_is_unresolved(tmp_path: Path) -> None:
    """A nested `${...}` the reader cannot follow before `CMD BATCH` still
    leaves a run, so it must not vanish from lineage and the post-action
    check."""
    (tmp_path / "script.R").write_text("print(1)\n")
    command = 'R --encoding "${ENC:-$DEFAULT_ENC}" CMD BATCH script.R'
    _assert_unresolved(detect_scripts(command, tmp_path), "script.R")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A literal R_BATCH_OPTIONS assignment on the command itself is read
        # exactly: BATCH appends it, unquoted, after the forwarded options.
        ("R_BATCH_OPTIONS=--file=second.R R CMD BATCH script.R", ["second.R"]),
        ("R_BATCH_OPTIONS=--vanilla R CMD BATCH script.R", ["script.R"]),
        ("LANG=C R_BATCH_OPTIONS='--vanilla -q' R CMD BATCH script.R", ["script.R"]),
        ("R_BATCH_OPTIONS=--version R CMD BATCH script.R", []),
        ("R_BATCH_OPTIONS='-e 1' R CMD BATCH script.R", []),
        ("R_BATCH_OPTIONS='--args --version' R CMD BATCH script.R", ["script.R"]),
    ],
)
def test_batch_reads_a_literal_batch_options_prefix(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    for name in ("script.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == [
        (tmp_path / name, None) for name in expected
    ]


@pytest.mark.parametrize(
    ("command", "source"),
    [
        # A `$` that starts no expansion is literal, as in bash.
        ("Rscript -e \"cat('$')\"", "cat('$')"),
        ("Rscript -e \"cat('a$ b')\"", "cat('a$ b')"),
        ("R -e 'x' -e \"y$\"", "x\ny$"),
        ("R -e \"paste0('$', 1)\"", "paste0('$', 1)"),
    ],
)
def test_literal_dollar_is_not_an_expansion(command: str, source: str) -> None:
    assert detect_scripts(command, Path("/tmp")) == [(None, source)]


@pytest.mark.parametrize(
    "command",
    [
        # The heredoc body starts after the newline; its delimiter is just a
        # redirection target, so the rest of the line is still read.
        "R -q -f first.R <<'EOF'\nyes\nEOF",
        "R --vanilla --file=first.R <<-EOF\nx\nEOF",
        "R CMD BATCH <<EOF first.R\nx\nEOF",
        "R -f first.R <<EOF --vanilla\nx\nEOF",
    ],
)
def test_heredoc_does_not_hide_a_known_script(tmp_path: Path, command: str) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    assert detect_scripts(command, tmp_path) == [(tmp_path / "first.R", None)]


def test_heredoc_delimiter_line_still_reads_stop_words(tmp_path: Path) -> None:
    (tmp_path / "first.R").write_text("print(1)\n")
    assert detect_scripts("R -f first.R <<EOF --version\nx\nEOF", tmp_path) == []


@pytest.mark.parametrize(
    ("command", "literal"),
    [
        # `$[expr]` is (deprecated) arithmetic expansion: bash expands it.
        ('R -f "a$[1].R"', "a$[1].R"),
        ("R -f a$[1].R", "a$[1].R"),
        ('R --file="script$[1+1].R"', "script$[1+1].R"),
        ('R CMD BATCH "a$[1].R"', "a$[1].R"),
        # Special and positional parameters expand.
        ('R -f "a$$.R"', "a$$.R"),
        ('R -f "a$@.R"', "a$@.R"),
        ('R -f "a$*.R"', "a$*.R"),
        ('R -f "a$#.R"', "a$#.R"),
        ('R -f "a$?.R"', "a$?.R"),
        ('R -f "a$!.R"', "a$!.R"),
        ('R -f "a$-b.R"', "a$-b.R"),
        ('R -f "a$1.R"', "a$1.R"),
    ],
)
def test_expanding_dollar_forms_never_resolve_to_a_literal_path(
    tmp_path: Path, command: str, literal: str
) -> None:
    (tmp_path / literal).write_text("print(1)\n")
    detections = detect_scripts(command, tmp_path)
    assert detections and all(
        isinstance(script, UnresolvedScript) for script, _ in detections
    ), detections


@pytest.mark.parametrize(
    "command",
    [
        "Rscript -e $'cat(1)'",
        'Rscript -e $"cat(1)"',
        'Rscript -e "x <- $[1+1]"',
        'R -e "cat($1)"',
        'R -e "cat($#)"',
    ],
)
def test_expanding_dollar_forms_are_never_recorded_as_code(command: str) -> None:
    assert detect_scripts(command, Path("/tmp")) == []


def test_quoted_heredoc_delimiter_is_not_expanded(tmp_path: Path) -> None:
    """A quoted heredoc delimiter is taken literally, so `$` inside it does
    not make the later `--file=` unreadable."""
    for name in ("first.R", "second.R"):
        (tmp_path / name).write_text("print(1)\n")
    command = 'R -f first.R <<"${EOF:-$DEFAULT}" --file=second.R\nx\n${EOF:-$DEFAULT}'
    assert detect_scripts(command, tmp_path) == [(tmp_path / "second.R", None)]


# ---------- standard-library static I/O ----------


def _stdlib_event(tmp_path: Path, source: str, script_args: list[str] | None = []):
    script = tmp_path / "run.py"
    script.write_text(source)
    args = argparse.Namespace(
        ts="2026-11-23T12:00:00Z",
        agent_id="",
        agent_type="",
        bash_cmd="python run.py",
        bash_exit=0,
        repo_root=str(tmp_path),
    )
    event = build_event_for_detection(
        (script, None), args, tmp_path, "abc123", script_args=script_args
    )
    assert event is not None
    return (
        event,
        sorted(item["path"] for item in event["inputs"]),
        sorted(item["path"] for item in event["outputs"]),
    )


def test_stdlib_open_and_pathlib_literals_are_resolved(tmp_path: Path) -> None:
    event, inputs, outputs = _stdlib_event(
        tmp_path,
        "import csv, os\n"
        "from pathlib import Path\n"
        "DATA = Path('data') / 'raw'\n"
        "COUNTS = os.path.join('data', 'counts.tsv')\n"
        "with open(COUNTS) as handle:\n"
        "    rows = list(csv.DictReader(handle, delimiter='\\t'))\n"
        "meta = list(csv.reader(open(DATA / 'meta.csv', newline='')))\n"
        "cfg = Path('params.yaml').read_text()\n"
        "blob = (DATA / 'blob.bin').read_bytes()\n"
        "with open('out/table.csv', 'w', newline='') as out:\n"
        "    csv.writer(out).writerow(['a'])\n"
        "Path('out/notes.txt').write_text('done')\n"
        "with DATA.joinpath('log.txt').open(mode='a') as log:\n"
        "    log.write('x')\n"
        "with open('state.json', 'r+') as state:\n"
        "    state.read()\n",
    )
    assert event["io_detection"] == "static"
    assert inputs == sorted(
        str(tmp_path / p)
        for p in ("data/counts.tsv", "data/raw/meta.csv", "params.yaml",
                  "data/raw/blob.bin")
    )
    assert outputs == sorted(
        str(tmp_path / p)
        for p in ("out/table.csv", "out/notes.txt", "data/raw/log.txt")
    )


@pytest.mark.parametrize(
    "source",
    [
        # A run-time value: the command line, the environment, input().
        "import sys\nopen(sys.argv[1])\n",
        "import os\nopen(os.environ['IN'])\n",
        # A name bound more than once may hold either value.
        "p = 'a.csv'\nif x:\n    p = 'b.csv'\nopen(p)\n",
        "p = 'a.csv'\np += '.bak'\nopen(p)\n",
        # `open` rebound to something else.
        "from gzip import open\nopen('a.csv')\n",
        "def open(p):\n    return p\nopen('a.csv')\n",
        # A str has no `.open`/`.read_text`: the call fails, nothing is read.
        "'a.csv'.read_text()\n",
        "P = 'a.csv'\nP.open()\n",
        # Unmodelled path pieces: f-strings, __file__, resolve(), a call.
        "name = 'a'\nopen(f'{name}.csv')\n",
        "from pathlib import Path\n(Path(__file__).parent / 'a.csv').read_text()\n",
        "from pathlib import Path\nPath('a.csv').resolve().read_text()\n",
        "open(helper('a.csv'))\n",
        # A dynamic mode.
        "open('a.csv', mode)\n",
        # A parameter whose function is passed around, so its callers are unknown.
        "def load(p):\n    return open(p).read()\nload('a.csv')\nmap(load, names)\n",
        # A parameter with a star-args call site.
        "def load(p):\n    return open(p).read()\nload(*names)\n",
        # Code that can rebind any name unseen.
        "from helpers import *\nopen('a.csv')\n",
        "exec(code)\nopen('a.csv')\n",
    ],
)
def test_stdlib_io_that_is_not_statically_certain_stays_unresolved(
    tmp_path: Path, source: str
) -> None:
    event, inputs, outputs = _stdlib_event(tmp_path, source)
    assert (inputs, outputs) == ([], [])
    assert event["io_detection"] == "unresolved"


def test_stdlib_parameter_resolves_from_its_single_call_site(tmp_path: Path) -> None:
    _, inputs, outputs = _stdlib_event(
        tmp_path,
        "from pathlib import Path\n"
        "def load(path, mode='r'):\n"
        "    with open(path, mode) as handle:\n"
        "        return handle.read()\n"
        "def save(text, dest=Path('out') / 'x.txt'):\n"
        "    dest.write_text(text)\n"
        "def main():\n"
        "    save(load(path=Path('b.csv')))\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
    )
    assert inputs == [str(tmp_path / "b.csv")]
    assert outputs == [str(tmp_path / "out/x.txt")]


def test_stdlib_parameter_with_one_unknown_call_site_stays_unresolved(
    tmp_path: Path,
) -> None:
    event, inputs, _ = _stdlib_event(
        tmp_path,
        "import sys\n"
        "def load(path):\n"
        "    return open(path).read()\n"
        "load('a.csv')\n"
        "load(sys.argv[1])\n",
    )
    assert inputs == []
    assert event["io_detection"] == "unresolved"


_ARGPARSE_SCRIPT = (
    "import argparse\n"
    "import csv\n"
    "from pathlib import Path\n"
    "from register_value import read_source\n"
    "\n"
    "def load_config(config_path: Path):\n"
    "    text, source = read_source(config_path, {'key': 't'}, mode='r')\n"
    "    return text\n"
    "\n"
    "def load_counts(counts_path: Path):\n"
    "    with counts_path.open(newline='') as handle:\n"
    "        return list(csv.DictReader(handle, delimiter='\\t'))\n"
    "\n"
    "def load_samples(samples_path: Path):\n"
    "    text, source = read_source(samples_path, {'column': 'id'}, mode='r')\n"
    "    return text\n"
    "\n"
    "def main(argv=None):\n"
    "    parser = argparse.ArgumentParser(description='demo')\n"
    "    parser.add_argument('--config', type=Path, required=True, help='cfg')\n"
    "    parser.add_argument('--counts', type=Path,\n"
    "                        default=Path('data/raw/counts.tsv'))\n"
    "    parser.add_argument('--samples', type=Path,\n"
    "                        default=Path('data/meta/samples.tsv'))\n"
    "    parser.add_argument('--output', type=Path,\n"
    "                        default=Path('analysis/de/outputs/gene_means.csv'))\n"
    "    parser.add_argument('--threshold', type=float, default=2.0)\n"
    "    args = parser.parse_args(argv)\n"
    "    load_config(args.config)\n"
    "    load_counts(args.counts)\n"
    "    load_samples(args.samples)\n"
    "    args.output.parent.mkdir(parents=True, exist_ok=True)\n"
    "    with args.output.open('w', encoding='utf-8', newline='') as handle:\n"
    "        csv.writer(handle).writerow(['gene'])\n"
    "    return 0\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    raise SystemExit(main())\n"
)


@pytest.mark.parametrize(
    "script_args,expected_inputs,expected_outputs",
    [
        (
            ["--config", "analysis/de/params.yaml"],
            ["analysis/de/params.yaml", "data/meta/samples.tsv", "data/raw/counts.tsv"],
            ["analysis/de/outputs/gene_means.csv"],
        ),
        (
            # `=` spelling, an argparse abbreviation, and a non-path option.
            ["--config=p.yaml", "--coun", "other.tsv", "--threshold", "3"],
            ["data/meta/samples.tsv", "other.tsv", "p.yaml"],
            ["analysis/de/outputs/gene_means.csv"],
        ),
    ],
)
def test_stdlib_argparse_values_come_from_the_command_line_or_defaults(
    tmp_path: Path, script_args, expected_inputs, expected_outputs
) -> None:
    event, inputs, outputs = _stdlib_event(tmp_path, _ARGPARSE_SCRIPT, script_args)
    assert event["io_detection"] == "static"
    assert inputs == sorted(str(tmp_path / p) for p in expected_inputs)
    assert outputs == sorted(str(tmp_path / p) for p in expected_outputs)


@pytest.mark.parametrize(
    "script_args",
    [
        None,  # the argv is unknown (an expansion, an unparsed command)
        ["--help"],  # argparse prints help and exits: no I/O
        ["--output", "o.csv"],  # --config is required: argparse exits
        ["--config", "p.yaml", "--bogus"],  # an unknown option: argparse exits
    ],
)
def test_stdlib_argparse_values_unknown_or_exiting_stay_unresolved(
    tmp_path: Path, script_args
) -> None:
    event, inputs, outputs = _stdlib_event(tmp_path, _ARGPARSE_SCRIPT, script_args)
    assert (inputs, outputs) == ([], [])
    assert event["io_detection"] == "unresolved"


@pytest.mark.parametrize(
    "mutation",
    [
        # Anything that changes argparse beyond the modelled calls.
        ("    args = parser.parse_args(argv)\n",
         "    parser.set_defaults(counts=Path('x.tsv'))\n"
         "    args = parser.parse_args(argv)\n"),
        ("    args = parser.parse_args(argv)\n",
         "    args = parser.parse_args(['--config', 'c.yaml'] + extra)\n"),
        ("    parser = argparse.ArgumentParser(description='demo')\n",
         "    parser = argparse.ArgumentParser(fromfile_prefix_chars='@')\n"),
        ("    raise SystemExit(main())\n", "    raise SystemExit(main(sys.argv))\n"),
        ("    args = parser.parse_args(argv)\n",
         "    args = parser.parse_args(argv)\n    args.counts = Path('y.tsv')\n"),
    ],
)
def test_stdlib_argparse_unmodelled_parsers_stay_unresolved(
    tmp_path: Path, mutation
) -> None:
    old, new = mutation
    assert old in _ARGPARSE_SCRIPT
    source = _ARGPARSE_SCRIPT.replace(old, new)
    _, inputs, outputs = _stdlib_event(tmp_path, source, ["--config", "p.yaml"])
    assert (inputs, outputs) == ([], [])


def test_main_resolves_stdlib_argparse_script_from_command_line(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis" / "de"
    analysis.mkdir(parents=True)
    (analysis / "run.py").write_text(_ARGPARSE_SCRIPT)
    (analysis / "params.yaml").write_text("t: 2\n")
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"

    def run(command: str) -> dict:
        target.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(extractor), "--cwd", str(tmp_path),
             "--ts", "2026-11-23T12:00:00Z", "--bash-cmd", command,
             "--bash-exit", "0", "--append-to", str(target), "--repo-root", str(tmp_path)],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        [event] = [json.loads(line) for line in target.read_text().splitlines()]
        return event

    event = run("python3 analysis/de/run.py --config analysis/de/params.yaml")
    assert event["io_detection"] == "static"
    assert sorted(i["path"] for i in event["inputs"]) == sorted(
        str(tmp_path / p) for p in
        ("analysis/de/params.yaml", "data/meta/samples.tsv", "data/raw/counts.tsv"))
    assert [o["path"] for o in event["outputs"]] == [
        str(tmp_path / "analysis/de/outputs/gene_means.csv")]
    assert {i["source"] for i in event["inputs"]} == {"inferred-static"}

    # A shell expansion in the script's argv: its value is unknown.
    event = run('python3 analysis/de/run.py --config "$CFG"')
    assert event["inputs"] == [] and event["outputs"] == []
    assert event["io_detection"] == "unresolved"


def test_stdlib_argparse_positional_argument_is_resolved(tmp_path: Path) -> None:
    _, inputs, outputs = _stdlib_event(
        tmp_path,
        "import argparse\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('table')\n"
        "parser.add_argument('-o', '--out', default='result.txt')\n"
        "args = parser.parse_args()\n"
        "rows = open(args.table).read()\n"
        "open(args.out, 'w').write(rows)\n",
        ["data/t.csv"],
    )
    assert inputs == [str(tmp_path / "data/t.csv")]
    assert outputs == [str(tmp_path / "result.txt")]



# Review probes (PR #155): each case names its source, the script's literal
# arguments, and the (inputs, outputs) the narrowed grammar must record. A
# wrong path is worse than none, so most stay unresolved.
_STDLIB_REVIEW_CASES = {
    "chdir": ('import os\nos.chdir("sub")\nopen("x.csv","w")\nopen("/abs/y.csv","w")\n', [],
              ([], ["/abs/y.csv"])),
    "uncalled_fn": ('def never():\n    open("debug.csv","w")\nopen("in.csv")\n', [],
                    (["in.csv"], [])),
    "dict_update": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="out.csv")\nargs=p.parse_args()\nargs.__dict__.update({"out":"other.csv"})\nopen(args.out,"w")\n', [], ([], [])),
    "vars_update": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="out.csv")\nargs=p.parse_args()\nvars(args).update({"out":"other.csv"})\nopen(args.out,"w")\n', [], ([], [])),
    "mode_kw": ('open("a.csv", mode="a")\nopen(file="b.csv", mode="r+")\nm="x"\nopen("c.csv", m)\n', [],
                ([], ["a.csv", "c.csv"])),
    "mode_var_reassigned": ('import sys\nm="r"\nif len(sys.argv)>1:\n    m="w"\nopen("c.csv", m)\n', [], ([], [])),
    "cond_path": ('import sys\nif sys.argv[1:]:\n    out="a.csv"\nelse:\n    out="b.csv"\nopen(out,"w")\n', [], ([], [])),
    "argv_override": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nopen(a.out,"w")\n', ["--out", "given.csv"], ([], ["given.csv"])),
    "argv_abbrev": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--output", default="d.csv")\na=p.parse_args()\nopen(a.output,"w")\n', ["--out=given.csv"], ([], ["given.csv"])),
    "argv_none": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nopen(a.out,"w")\n', None, ([], [])),
    "getattr_ns": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nopen(getattr(a,"out"),"w")\n', [], ([], [])),
    "alias_mut": ('import argparse,os\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nb=a\nb.out="x.csv"\nopen(a.out,"w")\n', [], ([], [])),
    "obj_setattr": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nobject.__setattr__(a, "out", "z.csv")\nopen(a.out,"w")\n', [], ([], [])),
    "escape_helper": ('import argparse\nfrom cfg import merge\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nmerge(a)\nopen(a.out,"w")\n', [], ([], [])),
    "fromfile": ('import argparse\np=argparse.ArgumentParser(fromfile_prefix_chars="@")\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nopen(a.out,"w")\n', [], ([], [])),
    "envvar_dflt_via_name": ('import argparse,os\nD=os.environ.get("O","d.csv")\np=argparse.ArgumentParser()\np.add_argument("--out", default=D)\na=p.parse_args()\nopen(a.out,"w")\n', [], ([], [])),
    "path_open_str": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\na.out.open("w")\n', [], ([], [])),
    "param_dead_branch": ('import sys\ndef save(p):\n    open(p,"w")\nif "--dry" in sys.argv:\n    save("dry.csv")\nelse:\n    save("real.csv")\n', [], ([], [])),
    "class_attr": ('class C:\n    out="c.csv"\nopen(C.out,"w")\n', [], ([], [])),
    "module_attr_store": ('import sys\nOUT="a.csv"\nsys.modules[__name__].OUT="b.csv"\nopen(OUT,"w")\n', [], ([], [])),
    "fstring": ('x="a"\nopen(f"{x}.csv","w")\n', [], ([], [])),
    "gzip": ('import gzip\ngzip.open("a.gz","wt")\n', [], ([], [])),
    "walrus_after": ('p="a.csv"\nopen(p,"w")\n[(p:=1)]\n', [], ([], [])),
    "with_as_rebind": ('p="a.csv"\nwith open("b.csv") as p:\n    pass\nopen(p)\n', [], (["b.csv"], [])),
    "exec_string": ('exec("x=1")\nopen("a.csv")\n', [], ([], [])),
    "Path_mode_kw": ('from pathlib import Path\nPath("a.txt").open(mode="a")\nPath("b.txt").open("x")\n', [], ([], ["a.txt", "b.txt"])),
    "open_modes": ('open("a.csv","rU")\nopen("b.csv","rt")\nopen("c.csv","wb+")\n', [], (["b.csv"], [])),
    "path_join_abs": ('import os\nopen(os.path.join("data","/etc/hosts"))\n', [], (["/etc/hosts"], [])),
    "star_kwargs": ('kw={"mode":"w"}\nopen("a.csv", **kw)\n', [], ([], [])),
    "default_param": ('def save(p="def.csv"):\n    open(p,"w")\nsave()\nsave(p="x.csv")\n', [], ([], [])),
    "str_dot_open": ('import os\np=os.path.join("a","b")\np.open("w")\n', [], ([], [])),
    "nargs_q_const": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", nargs="?", const="c.csv", default="d.csv")\na=p.parse_args()\nopen(a.out,"w")\n', ["--out"], ([], [])),
    "positional": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("inp")\np.add_argument("out")\na=p.parse_args()\nopen(a.inp)\nopen(a.out,"w")\n', ["i.csv", "o.csv"], (["i.csv"], ["o.csv"])),
    "dash_dash": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\np.add_argument("rest", nargs="*")\na=p.parse_args()\nopen(a.out,"w")\n', ["--", "--out", "x.csv"], ([], ["d.csv"])),
    "main_never_called": ('import argparse\ndef main():\n    p=argparse.ArgumentParser()\n    p.add_argument("--out", default="d.csv")\n    a=p.parse_args()\n    open(a.out,"w")\nif __name__=="__main__":\n    pass\n', [], ([], [])),
    "Path_write_on_str": ('p="a.txt"\np.write_text("x")\n', [], ([], [])),
    "type_path_default": ('import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser()\np.add_argument("--out", type=Path, default="d.csv")\na=p.parse_args()\na.out.open("w")\n', [], ([], ["d.csv"])),
    "type_int_string_default": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--n", type=int, default="01")\na=p.parse_args()\nopen(str(a.n) + ".txt","w")\n', [], ([], [])),
    "duplicate_dest": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\np.add_argument("--o2", dest="out", default="e.csv")\na=p.parse_args()\nopen(a.out,"w")\n', [], ([], [])),
    "sys_argv_mutated": ('import argparse, sys\nsys.argv.append("--out=x.csv")\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\na=p.parse_args()\nopen(a.out,"w")\n', [], ([], [])),
    "func_attr_store": ('def f(p):\n    open(p,"w")\nf.__defaults__ = ("x",)\nf("a.csv")\n', [], ([], [])),
    "func_defaults_rebind": ('def f(p="a.csv"):\n    open(p,"w")\nf.__defaults__ = ("x.csv",)\nf()\n', [], ([], [])),
    "if_main_args": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="d.csv")\nif __name__=="__main__":\n    a=p.parse_args()\n    open(a.out,"w")\n', ["--out", "g.csv"], ([], ["g.csv"])),
    "mode_param": ('def f(p, mode):\n    open(p, mode)\nf("a.csv","w")\nf("b.csv","r")\n', [], ([], [])),
    "cross_join": ('import os\ndef f(d, name):\n    open(os.path.join(d, name), "w")\nf("a", "x.csv")\nf("b", "y.csv")\n', [], ([], [])),
    "cross_path_mode": ('def save(p, m):\n    open(p, m)\nsave("raw.csv", "r")\nsave("clean.csv", "w")\n', [], ([], [])),
    "single_call_site": ('import os\ndef save(d, name, m):\n    open(os.path.join(d, name), m)\nsave("out", "x.csv", "w")\n', [], ([], ["out/x.csv"])),
    "io_open_rebound": ('import io\nio.open = other\nio.open("a.csv")\n', [], ([], [])),
    "custom_opener": ('open("a.csv", "w", opener=my_opener)\n', [], ([], [])),
    "pure_path": ('from pathlib import PurePath\nPurePath("a.csv").read_text()\n', [], ([], [])),
    "tilde_literal": ('open("~sample.csv")\n', [], (["~sample.csv"], [])),
    "dotdot": ('open("link/../data.csv")\n', [], ([], [])),
}


@pytest.mark.parametrize("case", sorted(_STDLIB_REVIEW_CASES))
def test_stdlib_review_probe(case: str) -> None:
    source, script_args, expected = _STDLIB_REVIEW_CASES[case]
    assert lineage_event._stdlib_static_io(source, script_args) == expected


# The stdlib scan's bounds are asserted on how its CPU cost SCALES, with its
# wall-clock deadline switched off so the deadline cannot hide the growth: a
# tight wall-clock limit (1.5 s, once 0.5 s) failed on loaded CI runners. CPU
# at size n against n/2, interleaved, best of three, must grow at most about
# linearly (a quadratic or worse regression is 4x or far more); below the
# floor a scan is too fast to judge. The only absolute bound is a sanity limit.
STDLIB_SANITY_SECONDS = 60.0
STDLIB_SCALING_RATIO = 3.2
STDLIB_SCALING_FLOOR = 0.5


@pytest.fixture
def stdlib_no_deadline(monkeypatch):
    """Switch off the scan's wall-clock deadline (its work limit stays)."""
    monkeypatch.setattr(lineage_event, "_STDLIB_DEADLINE_SECONDS", 1e9)


def _assert_scan_scales(build, n: int):
    """``build(size) -> (source, script_args)``; returns the result at n."""
    import time

    def cpu(case):
        started = time.process_time()
        result = lineage_event._stdlib_static_io(*case)
        return result, time.process_time() - started

    small_case, large_case = build(n // 2), build(n)
    smalls, larges, result = [], [], None
    for _ in range(3):  # interleaved, so a load spike cannot hit one side only
        smalls.append(cpu(small_case)[1])
        result, seconds = cpu(large_case)
        larges.append(seconds)
    small, large = min(smalls), min(larges)
    assert large < STDLIB_SANITY_SECONDS, large
    assert large < max(small * STDLIB_SCALING_RATIO, STDLIB_SCALING_FLOOR), (small, large)
    return result


def _chain(prefix: str, step: str):
    """A source whose path doubles at each of ``levels`` assignments."""
    def build(levels: int):
        return (prefix + "".join(step.format(i=i, j=i - 1) for i in range(1, levels))
                + f'open(a{levels - 1},"w")\n', [])
    return build


@pytest.mark.parametrize(
    ("build", "n"),
    [
        (lambda n: ('def f(p):\n    open(p+p+p+p,"w")\n'
                    + "".join(f'f("p{i}")\n' for i in range(n)), []), 300),
        (_chain('a0="x"\n', "a{i}=a{j}+a{j}\n"), 40),
        (_chain('import os\na0="x"\n', "a{i}=os.path.join(a{j},a{j})\n"), 40),
        (_chain('from pathlib import Path\na0=Path("x")\n', "a{i}=a{j}/a{j}\n"), 40),
        (lambda n: ("".join(f"x{i}='a'\nopen(x{i} + x{i} + x{i}, 'w')\n" for i in range(n)), []),
         3000),
    ],
    ids=["call-sites", "doubling", "join-chain", "div-chain", "many-sinks"],
)
def test_stdlib_pathological_script_is_bounded(stdlib_no_deadline, build, n) -> None:
    _assert_scan_scales(build, n)


@pytest.mark.parametrize(
    "command,expected",
    [
        ("python3 run.py --out a.csv > log.txt 2>&1", ["a.csv"]),
        ("timeout 50 python3 run.py --out t.csv", ["t.csv"]),
        ("python3 run.py --out a.csv # --out z.csv", ["a.csv"]),
        ("python3 run.py --out={a,b}.csv", []),
        ("python3 run.py --out a.csv {x,y}", []),
        ("python3 run.py --out a\\ b.csv", []),
        ('python3 run.py --out "sample\\$1.txt"', []),
        ("python3 run.py --out=$'a\\x2ecsv'", []),
        ("python3 run.py --out `echo q`.csv", []),
        ("python3 run.py --out a.csv >(cat)", []),
    ],
)
def test_main_stdlib_argparse_needs_exactly_decoded_words(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    (tmp_path / "run.py").write_text(
        "import argparse\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--out', default='default.csv')\n"
        "p.add_argument('rest', nargs='*')\n"
        "a = p.parse_args()\n"
        "open(a.out, 'w').write('x')\n"
    )
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"
    result = subprocess.run(
        [sys.executable, str(extractor), "--cwd", str(tmp_path),
         "--ts", "2026-11-23T12:00:00Z", "--bash-cmd", command,
         "--bash-exit", "0", "--append-to", str(target), "--repo-root", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in target.read_text().splitlines()]
    assert len(events) == 1
    assert [Path(o["path"]).name for o in events[0]["outputs"]] == expected



# Round-2 review probes (PR #155).
_STDLIB_REVIEW_CASES_2 = {
    "builtins_exec": ('import builtins\nOUT="a.csv"\nbuiltins.exec("OUT=\'b.csv\'")\nopen(OUT,"w")\n', [], ([], [])),
    "main_dict_sub": ('import sys\nOUT="a.csv"\nsys.modules[__name__].__dict__["OUT"]="b.csv"\nopen(OUT,"w")\n', [], ([], [])),
    "getframe_globals": ('import sys\nOUT="a.csv"\nsys._getframe().f_globals["OUT"]="b.csv"\nopen(OUT,"w")\n', [], ([], [])),
    "getattr_chdir": ('import os\ngetattr(os, "ch"+"dir")("sub")\nopen("x.csv","w")\n', [], ([], [])),
    "getattr_chdir_literal": ('import os\ngetattr(os, "chdir")("sub")\nopen("x.csv","w")\n', [], ([], [])),
    "chdir_alias": ('from os import chdir as cd\ncd("sub")\nopen("x.csv","w")\n', [], ([], [])),
    "contextlib_chdir": ('import contextlib\nwith contextlib.chdir("sub"):\n    open("x.csv","w")\n', [], ([], [])),
    "builtins_dict_open": ('import builtins, io\nbuiltins.__dict__["open"]=lambda p,m="r": io.open("z"+p,m)\nopen("x.csv","w")\n', [], ([], [])),
    "argparse_patch": ('import argparse\nargparse.ArgumentParser.parse_args=lambda self, a=None: argparse.Namespace(out="b.csv")\np=argparse.ArgumentParser()\np.add_argument("--out", default="a.csv")\nns=p.parse_args()\nopen(ns.out,"w")\n', [], ([], [])),
    "ns_dict_sub": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="a.csv")\nns=p.parse_args()\nns.__dict__["out"]="b.csv"\nopen(ns.out,"w")\n', [], ([], [])),
    "action_dest_patch": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", default="a.csv")\np.add_argument("--x", dest="out", action="store_const", const="b.csv")\nns=p.parse_args()\nopen(ns.out,"w")\n', ["--x"], ([], [])),
    "prefix_chars_plus": ('import argparse\np=argparse.ArgumentParser(prefix_chars="+")\np.add_argument("+o", default="a.csv")\nns=p.parse_args()\nopen(ns.o,"w")\n', ["+o", "b.csv"], ([], ["b.csv"])),
    "required_missing": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out", required=True)\nns=p.parse_args()\nopen(ns.out,"w")\n', [], ([], [])),
    "path_dotdot_join": ('import os\nopen(os.path.join("a","..","b.csv"))\n', [], ([], [])),
    "dest_from_flag_dash": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--out-file", default="a.csv")\nns=p.parse_args()\nopen(ns.out_file,"w")\n', ["--out-file", "g.csv"], ([], ["g.csv"])),
    "ann_default_type": ('import argparse\nfrom pathlib import Path\np=argparse.ArgumentParser()\np.add_argument("--o", type=Path, default=Path("d"))\nns=p.parse_args()\n(ns.o / "x.csv").write_text("")\n', [], ([], ["d/x.csv"])),
    "str_div": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--o", default="d")\nns=p.parse_args()\nopen(ns.o / "x.csv","w")\n', [], ([], [])),
    "int_type_given": ('import argparse\np=argparse.ArgumentParser()\np.add_argument("--n", type=int)\np.add_argument("--o", default="d.csv")\nns=p.parse_args()\nopen(ns.o,"w")\n', ["--n", "x"], ([], [])),
    "sys_alias_argv": ('import argparse, sys\ns = sys\ns.argv[:] = ["x", "--out", "b.csv"]\np=argparse.ArgumentParser()\np.add_argument("--out", default="a.csv")\nns=p.parse_args()\nopen(ns.out,"w")\n', [], ([], [])),
    "module_attr_patch": ('import io\nio.DEFAULT = 1\nopen("a.csv")\n', [], ([], [])),
    "chdir_in_comment": ('# no need for chdir here\nx = "chdir"\nopen("a.csv")\n', [], (["a.csv"], [])),
    "environ_store_is_fine": ('import os\nos.environ["OMP_NUM_THREADS"] = "1"\nopen("a.csv")\n', [], (["a.csv"], [])),
}


@pytest.mark.parametrize("case", sorted(_STDLIB_REVIEW_CASES_2))
def test_stdlib_review_probe_round_2(case: str) -> None:
    source, script_args, expected = _STDLIB_REVIEW_CASES_2[case]
    assert lineage_event._stdlib_static_io(source, script_args) == expected


def _argparse_options_source(count: int) -> str:
    return (
        "import argparse\np=argparse.ArgumentParser()\n"
        + "".join(f'p.add_argument("--opt{i}", default="d{i}")\n' for i in range(count))
        + 'ns=p.parse_args()\nopen(ns.opt1,"w")\n'
    )


@pytest.mark.parametrize(
    ("build", "n"),
    [
        (lambda n: ("x = " + "open(p)+" * n + "+".join(["0"] * (n * 8 // 3)) + "\n", []), 3000),
        (lambda n: ("x = " + "q.read_text()+" * n + "+".join(["0"] * (n * 8 // 3)) + "\n", []),
         3000),
        (lambda n: ("".join(f"def f{i}():\n    pass\n" for i in range(n))
                    + "x = " + "+".join(f"f{i}()" for i in range(n)) + "+"
                    + "+".join(["0"] * (n * 5)) + "\n", []), 2000),
        (lambda n: ("x=1\n" * n, []), 1_000_000),
        (lambda n: ("open(p)\n" * n, []), 30_000),
        (lambda n: (_argparse_options_source(n), ["--opt"] * n), 5000),
        (lambda n: (_argparse_options_source(n),
                    [x for i in range(n // 10) for x in (f"--opt{i}", "v")]), 20000),
        (lambda n: (_argparse_options_source(250), ["--opt"] * n), 5000),
    ],
    ids=["deep-open", "deep-read-text", "deep-reach", "huge-flat", "many-sinks",
         "argparse-5k-by-5k", "argparse-20k", "argparse-many-args"],
)
def test_stdlib_scan_work_is_bounded_globally(stdlib_no_deadline, build, n) -> None:
    _assert_scan_scales(build, n)


def test_argparse_replay_still_resolves_a_realistic_parser() -> None:
    assert lineage_event._stdlib_static_io(
        _argparse_options_source(200), ["--opt1", "given.csv"]
    ) == ([], ["given.csv"])


def test_chdir_mentioned_only_in_text_keeps_relative_reader_paths(tmp_path: Path) -> None:
    _, inputs, _ = _stdlib_event(
        tmp_path,
        "import pandas as pd\n# the shell wrapper does the chdir\n"
        "pd.read_csv('a.csv')\nprint('no chdir needed')\n",
    )
    assert inputs == [str(tmp_path / "a.csv")]


def test_unstattable_inferred_path_does_not_drop_the_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_exists = Path.exists

    def exists(self, *args, **kwargs):
        if self.name.startswith("toolong"):
            raise OSError(63, "File name too long")
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    _, inputs, outputs = _stdlib_event(
        tmp_path, "open('toolong.csv')\nopen('ok.csv', 'w')\n"
    )
    assert inputs == [str(tmp_path / "toolong.csv")]
    assert outputs == [str(tmp_path / "ok.csv")]



# Round-3 (Codex P1) reproductions (PR #155).
_STDLIB_REVIEW_CASES_3 = {
    "repeated_import_hides_hazards": (
        "import argparse, os, sys\n"
        "def prepare():\n"
        "    import os, sys\n"
        "    os.chdir('work')\n"
        "    sys.argv.extend(['--input', os.path.abspath('actual.csv')])\n"
        "prepare()\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--input', default='/dev/null')\n"
        "args = parser.parse_args()\n"
        "with open(args.input) as src:\n"
        "    open('report.csv', 'w').write(src.read())\n",
        [], ([], []),
    ),
    "argv_method_any_receiver": (
        "import argparse\nimport sys as s2\ndef f():\n    import sys as s2\n"
        "    s2.argv.append('--out=b.csv')\nf()\n"
        "p = argparse.ArgumentParser()\np.add_argument('--out', default='/abs/a.csv')\n"
        "a = p.parse_args()\nopen(a.out, 'w')\n",
        [], ([], []),
    ),
    "chdir_any_receiver": ("helper.chdir('x')\nopen('a.csv')\nopen('/abs/b.csv')\n", [],
                           (["/abs/b.csv"], [])),
    "positional_opener": (
        "import os\n"
        "directory = os.open('data', os.O_RDONLY)\n"
        "def in_data(path, flags):\n"
        "    return os.open(path, flags, dir_fd=directory)\n"
        "with open('sample.csv', 'r', -1, None, None, None, True, in_data) as src:\n"
        "    print(src.read())\n",
        [], ([], []),
    ),
    "open_third_positional": ("open('a.csv', 'r', -1)\n", [], ([], [])),
    "open_literal_keywords": (
        "open('a.csv', 'r', encoding='utf-8', newline='', errors='strict', buffering=-1)\n",
        [], (["a.csv"], []),
    ),
    "open_dynamic_encoding": ("open('a.csv', encoding=enc)\n", [], ([], [])),
    "path_open_extra_positional": (
        "from pathlib import Path\nPath('a.csv').open('r', -1, None, None, None)\n",
        [], ([], []),
    ),
    "encoding_cookie": ("# coding: latin-1\nopen('café.csv', 'w')\n", [], ([], [])),
    "replacement_char": ("open('caf�.csv', 'w')\n", [], ([], [])),
    "variable_positionals": (
        "import argparse\np = argparse.ArgumentParser()\np.add_argument('--output')\n"
        "p.add_argument('samples', nargs='*')\np.add_argument('columns', nargs=3)\n"
        "a = p.parse_args()\nopen(a.output, 'w')\n",
        ["s", "--output", "r.txt", "c", "c", "c"], ([], []),
    ),
}


@pytest.mark.parametrize("case", sorted(_STDLIB_REVIEW_CASES_3))
def test_stdlib_review_probe_round_3(case: str) -> None:
    source, script_args, expected = _STDLIB_REVIEW_CASES_3[case]
    assert lineage_event._stdlib_static_io(source, script_args) == expected


def test_argparse_backtracking_layout_is_declined_quickly(stdlib_no_deadline) -> None:
    source = (
        "import argparse\np = argparse.ArgumentParser()\np.add_argument('--output')\n"
        + "".join(f"p.add_argument('{n}', nargs='*')\n"
                  for n in ("samples", "controls", "weights", "labels"))
        + "p.add_argument('columns', nargs=101)\na = p.parse_args()\nopen(a.output, 'w')\n"
    )

    def build(samples: int):
        return source, ["sample"] * samples + ["--output", "report.txt"] + ["col"] * 101

    assert _assert_scan_scales(build, 100) == ([], [])


def test_main_argparse_declines_carriage_return_in_shell_word(tmp_path: Path) -> None:
    (tmp_path / "run.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\np.add_argument('--out')\n"
        "p.add_argument('--label')\na = p.parse_args()\nopen(a.out, 'w').write('x')\n"
    )
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = tmp_path / "events.tmp"
    result = subprocess.run(
        [sys.executable, str(extractor), "--cwd", str(tmp_path),
         "--ts", "2026-11-23T12:00:00Z",
         "--bash-cmd", "python3 run.py --out=out.csv\r --label report",
         "--bash-exit", "0", "--append-to", str(target), "--repo-root", str(tmp_path)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    [event] = [json.loads(line) for line in target.read_text().splitlines()]
    assert event["outputs"] == []


def test_oversized_script_chdir_in_comment_keeps_reader_paths(tmp_path: Path) -> None:
    source = (
        "# Previous versions used os.chdir elsewhere.\n"
        "import pandas as pd\npd.read_csv('samples.csv')\n"
        "CALIBRATION = (\n" + "    0.123456789,\n" * 17_000 + ")\n"
    )
    assert len(source) > lineage_event._STDLIB_SOURCE_LIMIT
    _, inputs, _ = _stdlib_event(tmp_path, source)
    assert inputs == [str(tmp_path / "samples.csv")]


def _run_extractor(cwd: Path, command: str) -> list[dict]:
    extractor = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    target = cwd / "events.tmp"
    target.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, str(extractor), "--cwd", str(cwd),
         "--ts", "2026-11-23T12:00:00Z", "--bash-cmd", command,
         "--bash-exit", "0", "--append-to", str(target), "--repo-root", str(cwd)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in target.read_text().splitlines()]


def test_chdir_keeps_reader_paths_with_an_absolute_base(tmp_path: Path) -> None:
    """Passes on main (repository recovery) and here (absolute base)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "data").mkdir()
    (tmp_path / "work").mkdir()
    (tmp_path / "data" / "samples.csv").write_text("x\n1\n")
    (tmp_path / "run.py").write_text(
        "import os\nfrom pathlib import Path\nimport pandas as pd\n\n"
        "os.chdir('work')\n"
        f"pd.read_csv(Path({str(tmp_path / 'data')!r}) / 'samples.csv')\n"
        "pd.read_csv('relative.csv')\n"
    )
    [event] = _run_extractor(tmp_path, "python3 run.py")
    paths = [i["path"] for i in event["inputs"]]
    assert str(tmp_path / "data" / "samples.csv") in paths
    # Main also records `relative.csv`, resolved against the wrong
    # directory; this branch drops it (asserted separately).


def test_stdlib_inferred_hashing_is_budgeted(tmp_path: Path, monkeypatch) -> None:
    import time

    # The byte budget (128 MiB of 768 MiB here) decides what is skipped, not
    # the clock: switch the deadlines off so a slow runner cannot change it.
    monkeypatch.setattr(lineage_event, "_STDLIB_DEADLINE_SECONDS", 1e9)
    monkeypatch.setattr(lineage_event._HashBudget.__init__, "__defaults__",
                        (128 * 1024**2, 1e9))

    names = [f"shard-{i:03}.bin" for i in range(12)]
    for name in names:
        with open(tmp_path / name, "wb") as handle:
            handle.truncate(64 * 1024**2)
    (tmp_path / "run.py").write_text("".join(
        f"with open({name!r}, 'rb') as f:\n    f.read(16)\n" for name in names))
    start = time.perf_counter()
    [event] = _run_extractor(tmp_path, "python3 run.py")
    elapsed = time.perf_counter() - start
    records = {Path(r["path"]).name: r for r in event["inputs"]}
    assert sorted(records) == names
    skipped = [r for r in records.values() if r.get("hash_status") == "skipped-budget"]
    hashed = [r for r in records.values() if r.get("sha256")]
    assert skipped and all(r["sha256"] is None for r in skipped)
    assert all("size_bytes" in r for r in skipped)
    assert len(hashed) + len(skipped) == len(names)
    assert 0 < len(hashed) <= 2  # 64 MiB each against the 128 MiB budget
    assert elapsed < STDLIB_SANITY_SECONDS



def test_stdlib_relative_paths_after_chdir_are_not_added(tmp_path: Path) -> None:
    """The reader scan's own result is main's (chdir does not filter it);
    the stdlib scan adds no relative path after a chdir."""
    _, inputs, outputs = _stdlib_event(
        tmp_path,
        "import os\nimport pandas as pd\nos.chdir('sub')\n"
        "pd.read_csv('a.csv')\nopen('b.csv')\nopen('c.csv', 'w')\n",
    )
    assert inputs == [str(tmp_path / "a.csv")]
    assert outputs == []


# Regressions in main's detection found in review: HEAD must record at
# least what main records on each.
def _symlink_basename_repro(root: Path) -> str:
    raw = root.parent / f"{root.name}-raw.csv"
    raw.write_text("x\n1\n")
    (root / "samples.csv").symlink_to(raw)
    (root / "data").mkdir()
    (root / "data" / "samples.csv").write_text("x\n2\n")
    return ("from pathlib import Path\nimport pandas as pd\n"
            "with open('samples.csv') as raw:\n    print(raw.read())\n"
            "print(pd.read_csv(Path('data') / 'samples.csv'))\n")


def _chdir_options_repro(root: Path) -> str:
    (root / "work").mkdir()
    (root / "samples.csv").write_text("x\n1\n")
    return ("import os\nfrom pathlib import Path\nimport pandas as pd\n"
            "options = {'usecols': ['x']}\nos.chdir('work')\n"
            f"print(pd.read_csv(Path({str(root)!r}) / 'samples.csv', **options))\n")


def _chdir_absolute_repro(root: Path) -> str:
    (root / "data").mkdir()
    (root / "work").mkdir()
    (root / "data" / "samples.csv").write_text("x\n1\n")
    return ("import os\nfrom pathlib import Path\nimport pandas as pd\n\n"
            "os.chdir('work')\n"
            f"pd.read_csv(Path({str(root / 'data')!r}) / 'samples.csv')\n"
            "pd.read_csv('relative.csv')\n")


def _chdir_comment_repro(root: Path) -> str:
    (root / "samples.csv").write_text("x\n1\n")
    return ("# Previous versions used os.chdir elsewhere.\n"
            "import pandas as pd\npd.read_csv('samples.csv')\n"
            "CALIBRATION = (\n" + "    0.123456789,\n" * 17_000 + ")\n")


_MAIN_REGRESSION_REPROS = {
    "symlink-basename": _symlink_basename_repro,
    "chdir-options": _chdir_options_repro,
    "chdir-absolute": _chdir_absolute_repro,
    "chdir-comment": _chdir_comment_repro,
}


def _repro_event(root: Path, make) -> dict:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "run.py").write_text(make(root))
    args = argparse.Namespace(
        ts="2026-11-23T12:00:00Z", agent_id="", agent_type="",
        bash_cmd="python run.py", bash_exit=0, repo_root=str(root),
    )
    event = build_event_for_detection((root / "run.py", None), args, root, "abc123")
    assert event is not None
    return event


@pytest.mark.parametrize("name", sorted(_MAIN_REGRESSION_REPROS))
def test_stdlib_scan_only_adds_to_the_reader_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Runs in CI: the result with the stdlib scan contains, record for
    record, the result without it."""
    make = _MAIN_REGRESSION_REPROS[name]
    full = _repro_event(tmp_path / "full", make) if (tmp_path / "full").mkdir() is None else None
    monkeypatch.setattr(lineage_event, "_stdlib_additions", lambda *a, **k: ([], []))
    (tmp_path / "base").mkdir()
    base = _repro_event(tmp_path / "base", make)

    def paths(event: dict, key: str, root: Path) -> set[str]:
        return {r["path"].replace(str(root), "$ROOT") for r in event[key]}

    for key in ("inputs", "outputs"):
        assert paths(base, key, tmp_path / "base") <= paths(full, key, tmp_path / "full")


def _main_extractor(tmp_path: Path) -> Path | None:
    repo = Path(__file__).resolve().parents[3]
    probe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q",
                            "origin/main"], capture_output=True, text=True)
    if probe.returncode != 0:
        return None
    archive = subprocess.run(["git", "-C", str(repo), "archive", "origin/main",
                              "skills/core/scripts"], capture_output=True)
    if archive.returncode != 0:
        return None
    dest = tmp_path / "main-src"
    dest.mkdir()
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, check=True)
    return dest / "skills" / "core" / "scripts" / "extract_data_lineage_event.py"


@pytest.mark.parametrize("name", sorted(_MAIN_REGRESSION_REPROS))
def test_head_records_at_least_what_main_records(tmp_path: Path, name: str) -> None:
    """main's own extractor (origin/main) against HEAD's, on each review
    reproduction. Skipped where origin/main is not fetched (shallow CI)."""
    main = _main_extractor(tmp_path)
    if main is None:
        pytest.skip("origin/main is not available")
    head = (Path(__file__).parent / "extract_data_lineage_event.py").resolve()
    results = {}
    for label, extractor in (("main", main), ("head", head)):
        root = tmp_path / label
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        (root / "run.py").write_text(_MAIN_REGRESSION_REPROS[name](root))
        target = root / "events.tmp"
        result = subprocess.run(
            [sys.executable, str(extractor), "--cwd", str(root),
             "--ts", "2026-11-23T12:00:00Z", "--bash-cmd", "python3 run.py",
             "--bash-exit", "0", "--append-to", str(target), "--repo-root", str(root)],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, result.stderr
        [event] = [json.loads(line) for line in target.read_text().splitlines()]
        results[label] = {key: {r["path"].replace(str(root), "$ROOT") for r in event[key]}
                          for key in ("inputs", "outputs")}
    for key in ("inputs", "outputs"):
        assert results["main"][key] <= results["head"][key]


def test_stdlib_additions_are_capped_all_or_nothing(tmp_path: Path) -> None:
    source = "".join(f"open('f{i}.csv')\n" for i in range(lineage_event._STDLIB_PATH_CAP + 1))
    event, inputs, _ = _stdlib_event(tmp_path, source)
    assert inputs == []
    source = "".join(f"open('f{i}.csv')\n" for i in range(lineage_event._STDLIB_PATH_CAP))
    _, inputs, _ = _stdlib_event(tmp_path, source)
    assert len(inputs) == lineage_event._STDLIB_PATH_CAP


def test_stdlib_additions_stay_inside_the_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _, inputs, outputs = _stdlib_event(
        repo, f"open({str(tmp_path / 'outside.csv')!r})\nopen('../up.csv')\n"
              "open('inside.csv', 'w')\n")
    assert inputs == []
    assert outputs == [str(repo / "inside.csv")]


def test_stdlib_framing_of_many_outside_paths_is_bounded(
        tmp_path: Path, monkeypatch, stdlib_no_deadline) -> None:
    import collections
    import itertools
    import os
    import time

    # The filesystem calls made for N outside paths do not grow with N (once
    # one stat/realpath per path), and CPU grows at most about linearly.
    calls: collections.Counter = collections.Counter()
    for module, name in ((os.path, "realpath"), (os.path, "commonpath"),
                         (os.path, "exists"), (os, "stat"), (os, "lstat")):
        original = getattr(module, name)

        def counted(*args, _original=original, _name=name, **kwargs):
            calls[_name] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(module, name, counted)

    out = tmp_path / "shared/pipeline/published/country/routine/archive/sequencing/experiment"
    out.mkdir(parents=True)
    serial = itertools.count()

    def framed(count: int):
        repo = tmp_path / f"repo{next(serial)}"
        repo.mkdir()
        source = f"BASE = {str(out) + '/'!r}\n" + "".join(
            f"open(BASE + '{i:05}.csv', 'w').close()\n" for i in range(count))
        calls.clear()
        started = time.process_time()
        _, _, outputs = _stdlib_event(repo, source)
        seconds = time.process_time() - started
        assert outputs == []
        return collections.Counter(calls), seconds

    runs = [(framed(4000), framed(8000)) for _ in range(3)]  # interleaved
    assert all(small[0] == large[0] for small, large in runs)
    small = min(pair[0][1] for pair in runs)
    large = min(pair[1][1] for pair in runs)
    assert large < STDLIB_SANITY_SECONDS, large
    assert large < max(small * STDLIB_SCALING_RATIO, STDLIB_SCALING_FLOOR), (small, large)


@pytest.mark.parametrize(
    "source",
    [
        "from unittest.mock import mock_open, patch\n"
        "with patch('builtins.open', mock_open(read_data='m')):\n"
        "    print(open('test.csv').read())\n",
        "import unittest.mock\nunittest.mock.patch('builtins.open')\nopen('test.csv')\n",
        "try:\n    with open('report.csv', 'x') as out:\n        out.write('n')\n"
        "except FileExistsError:\n    pass\n",
        "import contextlib\nwith contextlib.suppress(FileExistsError):\n"
        "    open('report.csv', 'x')\n",
    ],
    ids=["mock-patch", "unittest-mock", "try-exclusive", "suppress"],
)
def test_stdlib_patched_or_recoverable_opens_are_not_added(source: str) -> None:
    assert lineage_event._stdlib_static_io(source, []) == ([], [])


@pytest.mark.parametrize(
    "command,expected",
    [
        ("python3 run.py --input =python3", []),
        ("python3 run.py --input '=python3'", ["=python3"]),
        ("python3 run.py --input a^b.csv", []),
        ("python3 run.py --input a#b.csv", []),
        ("python3 run.py --input 'a#b.csv'", ["a#b.csv"]),
        ("python3 run.py --input=data.csv", ["data.csv"]),
    ],
)
def test_main_argparse_replay_needs_words_literal_in_zsh_and_bash(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    (tmp_path / "run.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\n"
        "p.add_argument('--input', required=True)\na = p.parse_args()\n"
        "open(a.input, 'rb').read(4)\n"
    )
    [event] = _run_extractor(tmp_path, command)
    assert [Path(r["path"]).name for r in event["inputs"]] == expected


# Final-round (Codex P1) reproductions (PR #155).
@pytest.mark.parametrize(
    "source,expected",
    [
        ("from contextlib import suppress as ignore\n"
         "def read_optional():\n"
         "    with open('optional.csv') as source:\n"
         "        return source.read()\n"
         "try:\n    read_optional()\nexcept FileNotFoundError:\n    pass\n"
         "with ignore(FileExistsError):\n    open('report.csv', 'x').write('new')\n",
         ([], [])),
        ("def helper():\n    open('h.csv')\ndef outer():\n    helper()\n"
         "try:\n    outer()\nexcept OSError:\n    pass\n", ([], [])),
        ("import thing\nwith thing.guard():\n    open('g.csv')\n", ([], [])),
        ("with open('a.csv') as a, open('b.csv', 'w') as b:\n    b.write(a.read())\n",
         (["a.csv"], ["b.csv"])),
        ("from pathlib import Path\nwith Path('p.csv').open() as f:\n    f.read()\n",
         (["p.csv"], [])),
        ("import csv\nwith open('rows.csv', 'a+', newline='') as out:\n"
         "    csv.writer(out).writerow(['next', 1])\n", ([], [])),
        # An update handle may only read (`seek(0)` then `read()`), only
        # write, or both: its direction is unknown.
        ("with open('rows.csv', 'a+') as f:\n    f.seek(0)\n    print(f.read())\n",
         ([], [])),
        ("open('s.json', 'r+')\nopen('t.json', 'w+')\nopen('u.bin', 'rb+')\n", ([], [])),
        ("open('log.txt', 'a')\n", ([], ["log.txt"])),
        ("import builtins\nfrom builtins import setattr as set_attr\n"
         "original_open = builtins.open\n"
         "def redirect(name, mode):\n    return original_open('actual.csv', mode)\n"
         "set_attr(__builtins__, 'open', redirect)\nopen('declared.csv', 'w').write('r')\n",
         ([], [])),
        ("__builtins__.open = None\nopen('x.csv')\n", ([], [])),
    ],
    ids=["guarded-helper-and-alias", "guarded-transitive", "other-context-manager",
         "plain-with-open", "path-open-with", "append-plus", "append-plus-read-only",
         "update-modes", "append",
         "builtins-alias", "builtins-name"],
)
def test_stdlib_final_round_probe(source: str, expected) -> None:
    assert lineage_event._stdlib_static_io(source, []) == expected


@pytest.mark.parametrize(
    "command,expected",
    [
        ("python3 run.py --input==cat", []),
        ("python3 run.py --input='=cat'", ["=cat"]),
        ("python3 run.py --input=x==y.csv", []),
    ],
)
def test_main_argparse_declines_zsh_magic_equal_subst(
    tmp_path: Path, command: str, expected: list[str]
) -> None:
    (tmp_path / "run.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\n"
        "p.add_argument('--input', required=True)\na = p.parse_args()\n"
        "open(a.input, 'rb').read(4)\n"
    )
    [event] = _run_extractor(tmp_path, command)
    assert [Path(r["path"]).name for r in event["inputs"]] == expected


# --- `__file__`-anchored paths ---------------------------------------------
#
# Agent-written analysis scripts anchor every path on the script's own
# location (`Path(__file__).resolve().parents[2] / "data" / ...`). `__file__`
# is bound only when the script path is certain; every other rule of the
# stdlib scan still applies.

_FILE = lineage_event._ScriptFile("/r/analysis/de/run.py", absolute=False)
_FILE_ABS = lineage_event._ScriptFile("/r/analysis/de/run.py", absolute=True)

_FILE_ANCHORED_CASES = {
    # (source, script_args, script_file, expected)
    "resolve_parents": (
        "from pathlib import Path\n"
        "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
        "ANALYSIS_DIR = Path(__file__).resolve().parent\n"
        "COUNTS = REPO_ROOT / 'data' / 'raw' / 'expr' / 'counts.tsv'\n"
        "OUT = ANALYSIS_DIR / 'outputs' / 'gene_ratios.csv'\n"
        "with COUNTS.open('r', encoding='utf-8') as h:\n    pass\n"
        "with OUT.open('w') as h:\n    pass\n",
        [], _FILE,
        (["/r/data/raw/expr/counts.tsv"], ["/r/analysis/de/outputs/gene_ratios.csv"])),
    "here_repo": (
        "from pathlib import Path\n"
        "HERE = Path(__file__).resolve().parent\n"
        "REPO = HERE.parents[1]\n"
        "SAMPLES = REPO / 'data' / 'metadata' / 'expr' / 'samples.tsv'\n"
        "OUT = HERE / 'outputs' / 'sample_counts.csv'\n"
        "def main():\n    with SAMPLES.open(newline='') as fh:\n        pass\n"
        "    OUT.parent.mkdir(parents=True, exist_ok=True)\n"
        "    with OUT.open('w', newline='') as fh:\n        pass\n"
        "if __name__ == '__main__':\n    main()\n",
        [], _FILE,
        (["/r/data/metadata/expr/samples.tsv"], ["/r/analysis/de/outputs/sample_counts.csv"])),
    "argparse_default": (
        "import argparse\nfrom pathlib import Path\n"
        "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
        "DEFAULT_COUNTS = REPO_ROOT / 'data' / 'counts.tsv'\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--counts', type=Path, default=DEFAULT_COUNTS)\n"
        "p.add_argument('--config', required=True)\n"
        "a = p.parse_args()\n"
        "with a.counts.open('r') as h:\n    pass\n"
        "open(a.config)\n",
        ["--config", "analysis/de/params.yaml"], _FILE,
        (["/r/data/counts.tsv", "analysis/de/params.yaml"], [])),
    "os_path_nested": (
        "import os\n"
        "HERE = os.path.dirname(os.path.abspath(__file__))\n"
        "ROOT = os.path.dirname(os.path.dirname(HERE))\n"
        "open(os.path.join(ROOT, 'data', 'a.csv'))\n"
        "open(os.path.join(HERE, 'out.csv'), 'w')\n",
        [], _FILE, (["/r/data/a.csv"], ["/r/analysis/de/out.csv"])),
    "realpath_absolute": (
        "import os\nfrom pathlib import Path\n"
        "A = os.path.realpath(__file__)\nB = Path(__file__).absolute().parent\n"
        "open(os.path.dirname(A) + '/x.csv')\nopen(B / 'y.csv', 'w')\n",
        [], _FILE, (["/r/analysis/de/x.csv"], ["/r/analysis/de/y.csv"])),
    "absolute_word_bare": (
        "import os\nfrom pathlib import Path\n"
        "open(Path(__file__).parent / 'a.csv')\n"
        "open(os.path.join(os.path.dirname(__file__), 'b.csv'), 'w')\n",
        [], _FILE_ABS, (["/r/analysis/de/a.csv"], ["/r/analysis/de/b.csv"])),
    "chdir_absolute_kept": (
        "import os\nfrom pathlib import Path\n"
        "OUT = Path(__file__).resolve().parent / 'out.csv'\n"
        "os.chdir('/tmp')\nopen(OUT, 'w')\nopen('rel.csv', 'w')\n",
        [], _FILE_ABS, ([], ["/r/analysis/de/out.csv"])),
    # -- negatives --
    "no_script_file": (
        "from pathlib import Path\nopen(Path(__file__).resolve().parent / 'a.csv')\n",
        [], None, ([], [])),
    "file_reassigned": (
        "from pathlib import Path\n__file__ = '/elsewhere/x.py'\n"
        "open(Path(__file__).resolve().parent / 'a.csv')\n",
        [], _FILE, ([], [])),
    "file_param": (
        "from pathlib import Path\ndef f(__file__):\n    pass\n"
        "open(Path(__file__).resolve().parent / 'a.csv')\n",
        [], _FILE, ([], [])),
    "file_deleted": (
        "from pathlib import Path\nA = Path(__file__).resolve().parent\ndel __file__\n"
        "open(A / 'a.csv')\n",
        [], _FILE, ([], [])),
    "parents_nonliteral": (
        "from pathlib import Path\nN = 2\n"
        "open(Path(__file__).resolve().parents[N] / 'a.csv')\n",
        [], _FILE, ([], [])),
    "parents_negative": (
        "from pathlib import Path\nopen(Path(__file__).resolve().parents[-1] / 'a.csv')\n",
        [], _FILE, ([], [])),
    "parents_bool": (
        "from pathlib import Path\nopen(Path(__file__).resolve().parents[True] / 'a.csv')\n",
        [], _FILE, ([], [])),
    "parents_out_of_range": (
        "from pathlib import Path\nopen(Path(__file__).resolve().parents[9] / 'a.csv')\n",
        [], _FILE, ([], [])),
    "relative_word_unresolved_parent": (
        # Before Python 3.9 `__file__` is the relative word itself.
        "import os\nfrom pathlib import Path\n"
        "open(Path(__file__).parent / 'a.csv')\n"
        "open(os.path.join(os.path.dirname(__file__), 'b.csv'))\n",
        [], _FILE, ([], [])),
    "relative_word_with_chdir": (
        "import os\nfrom pathlib import Path\n"
        "os.chdir('/tmp')\nOUT = Path(__file__).resolve().parent / 'out.csv'\n"
        "open(OUT, 'w')\n",
        [], _FILE, ([], [])),
    "resolve_after_join": (
        # A joined component may be a symlink: `resolve()` follows it.
        "from pathlib import Path\n"
        "open((Path(__file__).resolve().parent / 'link' / 'a.csv').resolve())\n",
        [], _FILE, ([], [])),
    "resolve_strict_keyword": (
        "from pathlib import Path\nopen(Path(__file__).resolve(strict=True).parent / 'a.csv')\n",
        [], _FILE, ([], [])),
    "parent_on_literal": (
        "from pathlib import Path\nopen(Path('data/x.csv').parent / 'a.csv')\n",
        [], _FILE, ([], [])),
    "resolve_on_literal": (
        "from pathlib import Path\nopen(Path('data').resolve() / 'a.csv')\n",
        [], _FILE, ([], [])),
    "parent_on_str": (
        "import os\nopen(os.path.abspath(__file__).parent / 'a.csv')\n",
        [], _FILE, ([], [])),
    "opaque_still_opaque": (
        "from pathlib import Path\nexec('x=1')\n"
        "open(Path(__file__).resolve().parent / 'a.csv')\n",
        [], _FILE, ([], [])),
    "dotdot_join": (
        "from pathlib import Path\nopen(Path(__file__).resolve().parent / '..' / 'a.csv')\n",
        [], _FILE, ([], [])),
}


@pytest.mark.parametrize("case", sorted(_FILE_ANCHORED_CASES))
def test_stdlib_file_anchored_paths(case: str) -> None:
    source, script_args, script_file, expected = _FILE_ANCHORED_CASES[case]
    assert lineage_event._stdlib_static_io(source, script_args, script_file) == expected


def test_certain_script_file(tmp_path: Path) -> None:
    root = Path(os.path.realpath(tmp_path))
    (root / "a" / "b").mkdir(parents=True)
    script = root / "a" / "b" / "run.py"
    script.write_text("")
    certain = lineage_event._certain_script_file
    assert certain(script, "a/b/run.py", root) == lineage_event._ScriptFile(str(script), False)
    assert certain(script, str(script), root) == lineage_event._ScriptFile(str(script), True)
    assert certain(script, "run.py", root / "a" / "b") == lineage_event._ScriptFile(str(script), False)
    # Not certain: no word (a runner, an inline body), a word Python would
    # spell differently in `__file__`, or a word that names another path.
    assert certain(script, None, root) is None
    assert certain(script, "./a/b/run.py", root) is None
    assert certain(script, "a/x/../b/run.py", root) is None
    assert certain(script, "a//b/run.py", root) is None
    assert certain(script, "other.py", root) is None
    assert certain(script, "a/b/run.py", None) is None
    # A symlink anywhere in the path: the script, a directory, the cwd.
    (root / "link.py").symlink_to(script)
    assert certain(root / "link.py", "link.py", root) is None
    (root / "alias").symlink_to(root / "a")
    assert certain(root / "alias" / "b" / "run.py", "alias/b/run.py", root) is None
    assert certain(root / "alias" / "b" / "run.py", "run.py", root / "alias" / "b") is None


def _file_anchored_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for rel, text in {
        "data/raw/expr/counts.tsv": "gene\ts1\ng1\t5\n",
        "data/metadata/expr/samples.ledger.json": "{}\n",
        "data/metadata/expr/samples.tsv": "sample\tarm\ns1\tdrug\n",
        "analysis/de/params.yaml": "ratio_threshold: 1.5\n",
    }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)


# Minimized from the 2026-11-24 live runs (Claude twice, Antigravity once).
_FILE_ANCHORED_SCRIPTS = {
    "claude-de": (
        "analysis/de/run.py", "python3 analysis/de/run.py --config analysis/de/params.yaml",
        "import argparse\nimport sys\nfrom pathlib import Path\n\n"
        "HERE = Path(__file__).resolve().parent\nROOT = HERE.parents[1]\n"
        "sys.path.insert(0, str(ROOT / 'analysis' / '_lib'))\n"
        "COUNTS = ROOT / 'data/raw/expr/counts.tsv'\n"
        "LEDGER = ROOT / 'data/metadata/expr/samples.tsv'\n"
        "OUT = HERE / 'outputs' / 'gene_ratios.csv'\n\n"
        "def main() -> int:\n"
        "    ap = argparse.ArgumentParser()\n"
        "    ap.add_argument('--config', required=True)\n"
        "    args = ap.parse_args()\n"
        "    with open(args.config) as fh:\n        fh.read()\n"
        "    with open(LEDGER, newline='') as fh:\n        fh.read()\n"
        "    with open(COUNTS, newline='') as fh:\n        fh.read()\n"
        "    OUT.parent.mkdir(parents=True, exist_ok=True)\n"
        "    with open(OUT, 'w', newline='') as fh:\n        fh.write('x\\n')\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n",
        ["analysis/de/params.yaml", "data/metadata/expr/samples.tsv",
         "data/raw/expr/counts.tsv"],
        ["analysis/de/outputs/gene_ratios.csv"]),
    "claude-summary": (
        "analysis/summary/run.py", "python3 analysis/summary/run.py",
        "import csv\nfrom pathlib import Path\n\n"
        "HERE = Path(__file__).resolve().parent\nREPO = HERE.parents[1]\n"
        "SAMPLES = REPO / 'data' / 'metadata' / 'expr' / 'samples.tsv'\n"
        "OUT = HERE / 'outputs' / 'sample_counts.csv'\n\n"
        "def main() -> None:\n"
        "    with SAMPLES.open(newline='') as fh:\n        list(csv.reader(fh))\n"
        "    OUT.parent.mkdir(parents=True, exist_ok=True)\n"
        "    with OUT.open('w', newline='') as fh:\n        fh.write('x\\n')\n\n"
        "if __name__ == '__main__':\n    main()\n",
        ["data/metadata/expr/samples.tsv"], ["analysis/summary/outputs/sample_counts.csv"]),
    "argparse-defaults": (
        # The Antigravity script's constants and argparse defaults, in the
        # `args = parser.parse_args()` form the replay models.
        "analysis/de/run.py", "python3 analysis/de/run.py --config analysis/de/params.yaml",
        "import argparse\nfrom pathlib import Path\n\n"
        "REPO_ROOT = Path(__file__).resolve().parents[2]\n"
        "ANALYSIS_DIR = Path(__file__).resolve().parent\n"
        "DEFAULT_COUNTS = REPO_ROOT / 'data' / 'raw' / 'expr' / 'counts.tsv'\n"
        "DEFAULT_LEDGER = REPO_ROOT / 'data' / 'metadata' / 'expr' / 'samples.ledger.json'\n"
        "DEFAULT_OUTPUT = ANALYSIS_DIR / 'outputs' / 'gene_ratios.csv'\n\n"
        "def run(counts_path, ledger_spec_path, config_path, output_csv):\n"
        "    config_path.read_text(encoding='utf-8')\n"
        "    ledger_spec_path.read_text(encoding='utf-8')\n"
        "    with counts_path.open('r', encoding='utf-8', newline='') as handle:\n"
        "        handle.read()\n"
        "    output_csv.parent.mkdir(parents=True, exist_ok=True)\n"
        "    with output_csv.open('w', encoding='utf-8', newline='') as handle:\n"
        "        handle.write('x\\n')\n\n"
        "def main() -> int:\n"
        "    parser = argparse.ArgumentParser(description='Ratios.')\n"
        "    parser.add_argument('--config', required=True, type=Path, help='YAML config.')\n"
        "    parser.add_argument('--counts', type=Path, default=DEFAULT_COUNTS,\n"
        "                        help=f'Counts (default: {DEFAULT_COUNTS}).')\n"
        "    parser.add_argument('--ledger', type=Path, default=DEFAULT_LEDGER)\n"
        "    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)\n"
        "    args = parser.parse_args()\n"
        "    run(counts_path=args.counts, ledger_spec_path=args.ledger,\n"
        "        config_path=args.config, output_csv=args.output)\n"
        "    return 0\n\n"
        "if __name__ == '__main__':\n    raise SystemExit(main())\n",
        ["analysis/de/params.yaml", "data/metadata/expr/samples.ledger.json",
         "data/raw/expr/counts.tsv"],
        ["analysis/de/outputs/gene_ratios.csv"]),
}


def _run_extractor_cli(root: Path, command: str, cwd: Path | None = None) -> dict:
    target = root / "events.tmp"
    target.unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "extract_data_lineage_event.py"),
         "--cwd", str(cwd or root), "--ts", "2026-11-24T12:00:00Z",
         "--bash-cmd", command, "--bash-exit", "0", "--append-to", str(target),
         "--repo-root", str(root)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    [event] = [json.loads(line) for line in target.read_text().splitlines()]
    return event


@pytest.mark.parametrize("name", sorted(_FILE_ANCHORED_SCRIPTS))
def test_main_resolves_file_anchored_scripts(tmp_path: Path, name: str) -> None:
    rel, command, source, expected_in, expected_out = _FILE_ANCHORED_SCRIPTS[name]
    root = Path(os.path.realpath(tmp_path))
    _file_anchored_repo(root)
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(source)
    event = _run_extractor_cli(root, command)
    assert event["io_detection"] == "static"
    assert sorted(r["rel_path"] for r in event["inputs"]) == expected_in
    assert sorted(r["rel_path"] for r in event["outputs"]) == expected_out
    assert {r["source"] for r in event["inputs"] + event["outputs"]} == {"inferred-static"}


def test_main_leaves_file_anchored_paths_unresolved_when_script_is_uncertain(
        tmp_path: Path) -> None:
    rel, _, source, _, _ = _FILE_ANCHORED_SCRIPTS["claude-summary"]
    root = Path(os.path.realpath(tmp_path))
    _file_anchored_repo(root)
    (root / rel).parent.mkdir(parents=True)
    (root / rel).write_text(source)
    # A symlinked script: `resolve()` follows the link to another directory.
    (root / "linked.py").symlink_to(root / rel)
    # A symlinked working directory.
    (root / "alias").symlink_to(root / "analysis")
    for command, cwd in (
        ("python3 linked.py", root),
        ("python3 ./analysis/summary/run.py", root),
        ("python3 summary/run.py", root / "alias"),
    ):
        event = _run_extractor_cli(root, command, cwd)
        assert event["io_detection"] == "unresolved", command
        assert event["inputs"] == [] and event["outputs"] == [], command


# --- argparse through a `parse_args(argv=None)` wrapper ---------------------
#
# The common agent layout: a wrapper returning `parser.parse_args(argv)`,
# called once as `args = parse_args(argv)` from `main(argv=None)`, which
# the main guard calls with no arguments. `argv` is then provably None.

_WRAPPER_PARSER = (
    "import argparse\nimport sys\n\n"
    "def parse_args(argv=None):\n"
    "    parser = argparse.ArgumentParser(description='x')\n"
    "    parser.add_argument('--out', default='d.csv')\n"
    "    return parser.parse_args(argv)\n\n"
)
_WRAPPER_MAIN = (
    "def main(argv=None):\n"
    "    args = parse_args(argv)\n"
    "    open(args.out, 'w')\n"
    "    return 0\n\n"
)
_WRAPPER_GUARD = "if __name__ == '__main__':\n    raise SystemExit(main())\n"

_WRAPPER_CASES = {
    # (source, script_args, expected outputs)
    "raise_system_exit": (_WRAPPER_PARSER + _WRAPPER_MAIN + _WRAPPER_GUARD,
                          ["--out", "given.csv"], ["given.csv"]),
    "default": (_WRAPPER_PARSER + _WRAPPER_MAIN + _WRAPPER_GUARD, [], ["d.csv"]),
    "sys_exit": (_WRAPPER_PARSER + _WRAPPER_MAIN
                 + "if __name__ == '__main__':\n    sys.exit(main())\n", [], ["d.csv"]),
    "bare_call": (_WRAPPER_PARSER + _WRAPPER_MAIN
                  + "if __name__ == '__main__':\n    main()\n", [], ["d.csv"]),
    "no_argument": (_WRAPPER_PARSER
                    + "def main():\n    args = parse_args()\n    open(args.out, 'w')\n\n"
                    + "if __name__ == '__main__':\n    main()\n", [], ["d.csv"]),
    "kwonly_argv": (_WRAPPER_PARSER
                    + "def main(*, argv=None):\n    args = parse_args(argv)\n"
                    "    open(args.out, 'w')\n\n" + _WRAPPER_GUARD, [], ["d.csv"]),
    # -- declines --
    "second_caller": (_WRAPPER_PARSER + _WRAPPER_MAIN + _WRAPPER_GUARD
                      + "def other():\n    return parse_args(['--out', 'x.csv'])\n",
                      [], []),
    "wrapper_default_not_none": (
        _WRAPPER_PARSER.replace("argv=None", "argv=['--out', 'x.csv']")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
    "main_default_not_none": (
        _WRAPPER_PARSER + _WRAPPER_MAIN.replace("argv=None", "argv=()")
        + _WRAPPER_GUARD, [], []),
    "main_no_default": (
        _WRAPPER_PARSER + _WRAPPER_MAIN.replace("argv=None", "argv")
        + "if __name__ == '__main__':\n    main(None)\n", [], []),
    "main_called_with_argument": (
        _WRAPPER_PARSER + _WRAPPER_MAIN
        + "if __name__ == '__main__':\n    main(['--out', 'x.csv'])\n", [], []),
    "main_called_twice": (
        _WRAPPER_PARSER + _WRAPPER_MAIN + _WRAPPER_GUARD + "main()\n", [], []),
    "main_called_outside_guard": (_WRAPPER_PARSER + _WRAPPER_MAIN + "main()\n", [], []),
    "main_called_in_expression": (
        _WRAPPER_PARSER + _WRAPPER_MAIN
        + "if __name__ == '__main__':\n    print(main())\n", [], []),
    "argv_reassigned": (
        _WRAPPER_PARSER
        + _WRAPPER_MAIN.replace("    args = parse_args(argv)\n",
                                "    argv = ['--out', 'x.csv']\n    args = parse_args(argv)\n")
        + _WRAPPER_GUARD, [], []),
    "argv_mutated": (
        _WRAPPER_PARSER
        + _WRAPPER_MAIN.replace("    args = parse_args(argv)\n",
                                "    argv = argv or []\n    argv.append('--out=x.csv')\n"
                                "    args = parse_args(argv)\n")
        + _WRAPPER_GUARD, [], []),
    "wrapper_argv_reassigned": (
        _WRAPPER_PARSER.replace("    return parser.parse_args(argv)\n",
                                "    argv = ['--out', 'x.csv']\n"
                                "    return parser.parse_args(argv)\n")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
    "argv_shadowed_nested": (
        _WRAPPER_PARSER
        + _WRAPPER_MAIN.replace("    args = parse_args(argv)\n",
                                "    f = lambda argv: argv\n    args = parse_args(argv)\n")
        + _WRAPPER_GUARD, [], []),
    "sys_argv_touched": (
        _WRAPPER_PARSER + _WRAPPER_MAIN
        + "if __name__ == '__main__':\n    sys.argv.append('--out=x.csv')\n"
        "    raise SystemExit(main())\n", [], []),
    "second_parse_args": (
        _WRAPPER_PARSER.replace("    return parser.parse_args(argv)\n",
                                "    parser.parse_args(argv)\n"
                                "    return parser.parse_args(argv)\n")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
    "parse_known_args_elsewhere": (
        _WRAPPER_PARSER + _WRAPPER_MAIN + _WRAPPER_GUARD
        + "def other(p):\n    return p.parse_known_args()\n", [], []),
    "wrapper_returns_known_args": (
        _WRAPPER_PARSER.replace("parser.parse_args(argv)", "parser.parse_known_args(argv)[0]")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
    "wrapper_extra_parameter": (
        _WRAPPER_PARSER.replace("def parse_args(argv=None)", "def parse_args(argv=None, x=1)")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
    "wrapper_passes_other_name": (
        _WRAPPER_PARSER.replace("    return parser.parse_args(argv)\n",
                                "    v = None\n    return parser.parse_args(v)\n")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
    "wrapper_keyword_call": (
        _WRAPPER_PARSER + _WRAPPER_MAIN.replace("parse_args(argv)", "parse_args(argv=argv)")
        + _WRAPPER_GUARD, [], []),
    "wrapper_decorated": (
        _WRAPPER_PARSER.replace("def parse_args", "@staticmethod\ndef parse_args")
        + _WRAPPER_MAIN + _WRAPPER_GUARD, [], []),
}


@pytest.mark.parametrize("case", sorted(_WRAPPER_CASES))
def test_stdlib_argparse_wrapper(case: str) -> None:
    source, script_args, expected = _WRAPPER_CASES[case]
    assert lineage_event._stdlib_static_io(source, script_args) == ([], expected)


def test_main_resolves_the_antigravity_script(tmp_path: Path) -> None:
    """The 2026-11-24 Antigravity run's script, unmodified: `__file__`
    anchored argparse defaults through a `parse_args(argv)` wrapper.

    Known limits, which stay unresolved: the config is read through a
    loader-returned `read_source`, and counts and ledger pass through
    parameters named alike in two functions (`run_analysis` and
    `load_counts`/`load_ledger`), which the module-unique name rule
    declines."""
    root = Path(os.path.realpath(tmp_path))
    _file_anchored_repo(root)
    script = root / "analysis" / "de" / "run.py"
    script.write_text((Path(__file__).parent / "testdata"
                       / "antigravity-0.10.0-de-run.py").read_text())
    event = _run_extractor_cli(
        root, "python3 analysis/de/run.py --config analysis/de/params.yaml")
    assert event["io_detection"] == "static"
    # The script re-reads the table it wrote, so the output is also an input.
    assert sorted(r["rel_path"] for r in event["inputs"]) == [
        "analysis/de/outputs/gene_ratios.csv"]
    assert [r["rel_path"] for r in event["outputs"]] == [
        "analysis/de/outputs/gene_ratios.csv"]
