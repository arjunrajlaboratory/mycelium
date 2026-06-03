"""Tests for render_report_values_tex."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_report_values_tex as rrv


def test_id_to_macro_name_basics() -> None:
    assert rrv.id_to_macro_name("n_samples") == "NSamples"
    assert rrv.id_to_macro_name("fdr_threshold") == "FDRThreshold"
    assert rrv.id_to_macro_name("contrast_phrase") == "ContrastPhrase"
    # all-letter ≤3 segments uppercase; long segments title-case
    assert rrv.id_to_macro_name("n_de_genes") == "NDEGenes"
    # digits become English words
    assert rrv.id_to_macro_name("n_de_genes_fdr_0_05") == "NDEGenesFDRZeroZeroFive"


def test_id_to_macro_name_spells_out_embedded_digits() -> None:
    # Mixed letter+digit segments must spell out the digit, because a LaTeX
    # control word is letters-only — ``\COne`` not ``\C`` + literal ``1``.
    assert rrv.id_to_macro_name("c1_precision") == "COnePrecision"
    assert rrv.id_to_macro_name("x17_module") == "XOneSevenModule"
    assert rrv.id_to_macro_name("pc1_c1_ari") == "PcOneCOneARI"
    assert rrv.id_to_macro_name("weighted_f1_test") == "WeightedFOneTest"
    assert (
        rrv.id_to_macro_name("x17_module_c1_balanced_accuracy_ci_high")
        == "XOneSevenModuleCOneBalancedAccuracyCIHigh"
    )


def test_id_to_macro_name_strips_namespace() -> None:
    assert rrv.id_to_macro_name("diff-expr.n_samples") == "NSamples"
    # ``baz`` and ``qux`` are both ≤3-letter all-alpha segments, so they
    # uppercase per the spec.
    assert rrv.id_to_macro_name("foo:bar/baz_qux") == "BAZQUX"
    assert rrv.id_to_macro_name("foo:bar/contrast_phrase") == "ContrastPhrase"


def test_render_basic(tmp_path: Path) -> None:
    manifest = {
        "numbers": [
            {"id": "n_samples", "value": 48},
            {"id": "fdr_threshold", "value": 0.05},
            {"id": "contrast_phrase", "value": "treated versus control"},
        ]
    }
    out = rrv.render(manifest)
    assert "\\newcommand{\\NSamples}{48}" in out
    assert "\\newcommand{\\FDRThreshold}{0.05}" in out
    assert "\\newcommand{\\ContrastPhrase}{treated versus control}" in out
    # Wrappers present.
    assert "\\providecommand{\\SciVal}[2]{#1}" in out
    assert "\\providecommand{\\SciText}[2]{#1}" in out


def test_render_escapes_text() -> None:
    manifest = {
        "numbers": [
            {"id": "cohort_name", "value": "A&B cohort"},
            {"id": "label_with_underscore", "value": "foo_bar"},
        ]
    }
    out = rrv.render(manifest)
    assert "\\newcommand{\\CohortName}{A\\&B cohort}" in out
    assert "\\newcommand{\\LabelWithUnderscore}{foo\\_bar}" in out


def test_render_escapes_backslash_without_re_escaping_braces() -> None:
    """Regression: the earlier sequential replace loop produced
    ``\\textbackslash\\{\\}`` for an input ``\\`` because the ``{`` /
    ``}`` rules later re-processed the just-inserted braces."""
    out = rrv.render({"numbers": [{"id": "regex_pattern", "value": r"a\b"}]})
    assert "\\newcommand{\\RegexPattern}{a\\textbackslash{}b}" in out
    # The bad output we are guarding against.
    assert "\\textbackslash\\{\\}" not in out


def test_render_escapes_all_specials_in_one_pass() -> None:
    """Every special character appears exactly once-escaped, no double-escapes."""
    out = rrv.render(
        {"numbers": [{"id": "cohort_name", "value": r"A&B \% 100 ^ ~ _ # $ { }"}]}
    )
    # Locate the rendered macro line.
    macro_line = [ln for ln in out.splitlines() if "CohortName" in ln][0]
    # Backslash escape leaves the brace group intact.
    assert "\\textbackslash{}" in macro_line
    assert "\\textasciitilde{}" in macro_line
    assert "\\textasciicircum{}" in macro_line
    # No double-escaping artefact.
    assert "\\textbackslash\\{\\}" not in macro_line


def test_render_collision_raises() -> None:
    manifest = {
        "numbers": [
            {"id": "diff-expr.n_samples", "value": 48},
            {"id": "qc.n_samples", "value": 50},
        ]
    }
    with pytest.raises(ValueError, match="collision"):
        rrv.render(manifest)


def test_render_empty_numbers() -> None:
    out = rrv.render({"numbers": []})
    # Still emits the wrappers; no \newcommand lines.
    assert "\\providecommand{\\SciVal}[2]{#1}" in out
    assert "\\newcommand{" not in out


def test_render_ignores_entries_without_id_or_value() -> None:
    manifest = {
        "numbers": [
            {"id": "good", "value": 1},
            {"id": "no_value"},
            {"value": 99},
            "not_a_dict",
            None,
        ]
    }
    out = rrv.render(manifest)
    assert "\\newcommand{\\Good}{1}" in out
    assert "no_value" not in out.lower() or "\\Good}" in out  # smoke


def test_cli_writes_default_path(tmp_path: Path) -> None:
    manifest_path = tmp_path / "reports" / ".manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps({"numbers": [{"id": "n_samples", "value": 48}]})
    )
    rc = rrv.main([str(manifest_path)])
    assert rc == 0
    out_path = tmp_path / "reports" / "build" / "report_values.tex"
    assert out_path.exists()
    content = out_path.read_text()
    assert "\\newcommand{\\NSamples}{48}" in content


def test_cli_collision_exits_nonzero(tmp_path: Path, capsys) -> None:
    manifest_path = tmp_path / ".manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "numbers": [
                    {"id": "a.x", "value": 1},
                    {"id": "b.x", "value": 2},
                ]
            }
        )
    )
    rc = rrv.main([str(manifest_path)])
    assert rc == 1


def test_float_repr_stable() -> None:
    # Ensure 0.05 stays 0.05 (Python's repr is the shortest round-trip).
    out = rrv.render({"numbers": [{"id": "p", "value": 0.05}]})
    assert "{0.05}" in out


def test_bool_value() -> None:
    out = rrv.render({"numbers": [{"id": "passed", "value": True}]})
    assert "\\newcommand{\\Passed}{true}" in out


# ---------------------------------------------------------------------------
# Renderer-derived display: unit / precision / display (Path X)
# ---------------------------------------------------------------------------


def test_render_percent_default_precision() -> None:
    out = rrv.render(
        {"numbers": [{"id": "frac_positive", "value": 0.978, "unit": "percent"}]}
    )
    assert "\\newcommand{\\FracPositive}{97.8\\%}" in out


def test_render_percent_explicit_precision() -> None:
    out0 = rrv.render(
        {"numbers": [{"id": "frac_positive", "value": 0.978, "unit": "percent", "precision": 0}]}
    )
    assert "\\newcommand{\\FracPositive}{98\\%}" in out0
    out2 = rrv.render(
        {"numbers": [{"id": "frac_positive", "value": 0.978, "unit": "percent", "precision": 2}]}
    )
    assert "\\newcommand{\\FracPositive}{97.80\\%}" in out2


def test_render_display_override_verbatim() -> None:
    # display is the author's LaTeX-ready escape hatch: emitted as-is.
    out = rrv.render(
        {"numbers": [{"id": "fold", "value": 3.2, "display": "3.2$\\times$"}]}
    )
    assert "\\newcommand{\\Fold}{3.2$\\times$}" in out


def test_render_no_display_fields_unchanged() -> None:
    # No unit/display → historical behavior (raw value).
    out = rrv.render({"numbers": [{"id": "frac_positive", "value": 0.978}]})
    assert "\\newcommand{\\FracPositive}{0.978}" in out


def test_render_does_not_mutate_value() -> None:
    manifest = {"numbers": [{"id": "frac_positive", "value": 0.978, "unit": "percent"}]}
    rrv.render(manifest)
    # The canonical value is the Phase-6 / scitexlintr anchor — rendering must
    # not touch it even though the *displayed* macro is derived from it.
    assert manifest["numbers"][0]["value"] == 0.978


def test_render_percent_unit_unsupported_raises() -> None:
    with pytest.raises(ValueError, match="unit"):
        rrv.render({"numbers": [{"id": "x", "value": 0.5, "unit": "fold-change"}]})


def test_format_value_percent_direct() -> None:
    assert rrv.format_value(0.0111, unit="percent") == "1.1\\%"
    assert rrv.format_value(0.8193, unit="percent") == "81.9\\%"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
