"""Tests for register_value.

The tests build a temporary directory tree that looks like an actual
mycelium project (with an ``analysis/<ns>/scripts/`` script that calls
``register_value``), then invoke the script via subprocess so each test
gets a clean process-level session.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest


HELPER_PATH = Path(__file__).resolve().parent / "register_value.py"


def _make_project(tmp_path: Path, ns: str, script_body: str) -> Path:
    """Write a minimal analysis/<ns>/scripts/probe.py that imports the helper."""
    analysis_dir = tmp_path / "analysis" / ns / "scripts"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    script = analysis_dir / "probe.py"
    prefix = (
        "import sys\n"
        f"sys.path.insert(0, {str(HELPER_PATH.parent)!r})\n"
        "from register_value import register_value, ValueRegistrationError\n"
        "\n"
    )
    script.write_text(prefix + script_body + "\n", encoding="utf-8")
    return script


def _run(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
    )


def _read_fragment(tmp_path: Path, ns: str) -> dict:
    return json.loads(
        (tmp_path / "analysis" / ns / "outputs" / "numbers.json").read_text()
    )


def test_basic_register_and_namespace_inference(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        'register_value("n_samples", 48)',
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr

    data = _read_fragment(tmp_path, "diff-expr")
    assert data["namespace"] == "diff-expr"
    assert len(data["values"]) == 1
    entry = data["values"][0]
    assert entry["key"] == "n_samples"
    assert entry["value"] == 48
    assert entry["computed_at"].startswith("scripts/probe.py:L")
    assert entry["provenance"] == entry["computed_at"]


def test_explicit_provenance(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        'register_value("n_samples", 48, provenance="outputs/tables/qc.csv:row=passing")',
    )
    assert _run(script).returncode == 0
    data = _read_fragment(tmp_path, "diff-expr")
    assert data["values"][0]["provenance"] == "outputs/tables/qc.csv:row=passing"


def test_multiple_keys_sorted(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        textwrap.dedent(
            """
            register_value("zebra", 1)
            register_value("alpha", 2)
            register_value("middle", 3)
            """
        ).strip(),
    )
    assert _run(script).returncode == 0
    data = _read_fragment(tmp_path, "diff-expr")
    keys = [e["key"] for e in data["values"]]
    assert keys == ["alpha", "middle", "zebra"]


def test_same_value_is_noop(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        textwrap.dedent(
            """
            register_value("n_samples", 48)
            register_value("n_samples", 48)
            """
        ).strip(),
    )
    assert _run(script).returncode == 0
    data = _read_fragment(tmp_path, "diff-expr")
    assert len(data["values"]) == 1


def test_in_process_collision_raises(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        textwrap.dedent(
            """
            register_value("n_samples", 48)
            try:
                register_value("n_samples", 49)
            except ValueRegistrationError as exc:
                print("CAUGHT:", exc)
                raise SystemExit(0)
            raise SystemExit(1)
            """
        ).strip(),
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "collision" in result.stdout


def test_cross_run_upsert(tmp_path: Path) -> None:
    """A second process call with a different value should silently update."""
    script1 = _make_project(
        tmp_path,
        "diff-expr",
        'register_value("n_samples", 48)',
    )
    _run(script1)

    script1.write_text(
        textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(HELPER_PATH.parent)!r})
            from register_value import register_value
            register_value("n_samples", 47)
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    assert _run(script1).returncode == 0
    data = _read_fragment(tmp_path, "diff-expr")
    assert len(data["values"]) == 1
    assert data["values"][0]["value"] == 47


def test_explicit_namespace_override(tmp_path: Path) -> None:
    """If we override namespace=, the fragment lands under that directory."""
    (tmp_path / "analysis" / "qc").mkdir(parents=True)
    script = _make_project(
        tmp_path,
        "diff-expr",
        'register_value("n_samples", 48, namespace="qc")',
    )
    assert _run(script).returncode == 0
    data = _read_fragment(tmp_path, "qc")
    assert data["namespace"] == "qc"
    assert data["values"][0]["value"] == 48


def test_text_value(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        'register_value("contrast_phrase", "treated versus control")',
    )
    assert _run(script).returncode == 0
    data = _read_fragment(tmp_path, "diff-expr")
    assert data["values"][0]["value"] == "treated versus control"


def test_float_value(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        'register_value("fdr_threshold", 0.05)',
    )
    assert _run(script).returncode == 0
    data = _read_fragment(tmp_path, "diff-expr")
    assert data["values"][0]["value"] == 0.05


def test_rejects_unsupported_type(tmp_path: Path) -> None:
    script = _make_project(
        tmp_path,
        "diff-expr",
        textwrap.dedent(
            """
            try:
                register_value("things", [1, 2, 3])
            except TypeError as exc:
                print("CAUGHT:", exc)
                raise SystemExit(0)
            raise SystemExit(1)
            """
        ).strip(),
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr


def test_nested_analysis_segments_uses_nearest(tmp_path: Path) -> None:
    """Regression: a project laid out as
    ``analysis/outer/.../analysis/inner/scripts/probe.py`` must resolve to
    ``inner``, not ``outer``. The earlier left-to-right walk silently
    mis-routed the fragment to the wrong namespace."""
    script_dir = tmp_path / "analysis" / "outer" / "subdir" / "analysis" / "inner" / "scripts"
    script_dir.mkdir(parents=True)
    script = script_dir / "probe.py"
    prefix = (
        "import sys\n"
        f"sys.path.insert(0, {str(HELPER_PATH.parent)!r})\n"
        "from register_value import register_value\n"
        "\n"
    )
    script.write_text(prefix + 'register_value("n_samples", 48)\n', encoding="utf-8")
    assert _run(script).returncode == 0
    inner_fragment = (
        tmp_path
        / "analysis" / "outer" / "subdir" / "analysis" / "inner"
        / "outputs" / "numbers.json"
    )
    assert inner_fragment.exists(), "fragment should land under the inner analysis"
    data = json.loads(inner_fragment.read_text())
    assert data["namespace"] == "inner"
    assert data["values"][0]["value"] == 48
    outer_fragment = tmp_path / "analysis" / "outer" / "outputs" / "numbers.json"
    assert not outer_fragment.exists(), "fragment must not land under the outer namespace"


def test_mixed_namespace_in_file_rejected(tmp_path: Path) -> None:
    """If outputs/numbers.json exists with a different namespace, refuse."""
    outputs = tmp_path / "analysis" / "diff-expr" / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "numbers.json").write_text(
        json.dumps({"namespace": "other-ns", "values": []})
    )
    script = _make_project(
        tmp_path,
        "diff-expr",
        textwrap.dedent(
            """
            try:
                register_value("n", 1)
            except Exception as exc:
                print("CAUGHT:", exc)
                raise SystemExit(0)
            raise SystemExit(1)
            """
        ).strip(),
    )
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert "namespace" in result.stdout


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
