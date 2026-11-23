"""Regressions from a review of the provenance PR: report values, value
registration and data contracts.

Each test failed on the branch before its fix:

1. ``--explain`` honors the index's own record verdict: a write whose
   status is ``unknown`` (``coverage-unproven`` with an ``unaccounted``
   list, and no explicit gap) makes the record incomplete, so the change
   is ``producer-unknown``, never "outside the record";
2. two processes creating ``outputs/`` at once do not fail with
   FileExistsError, and a symlink planted in that window is still refused;
3. ``--emit-contract --overwrite`` keeps the replaced contract's mode; the
   umask applies only to a new file.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_report_values as crv  # noqa: E402
import data_contract_check as dcc  # noqa: E402
import register_value as rv  # noqa: E402

A, B, C = "aa" * 32, "bb" * 32, "cc" * 32
UNPROVEN = {"status": "unknown", "reasons": ["coverage-unproven"],
            "unaccounted": [{"kind": "journal-compacted", "floor": 7}],
            "unaccounted_count": 1}


# -- 1. the record verdict's reasons make the record incomplete -----------------


def _fake_query(monkeypatch, writes: list[dict]) -> None:
    import lineage_index

    monkeypatch.setattr(lineage_index, "query_path",
                        lambda index, rel, disk=False: {"writes": writes})


def test_control_complete_record_still_says_outside(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": {"status": "current-in-record"}},
    ])
    assert crv._explain(object(), "out/res.csv", B)["status"] == "current-bytes-not-in-record"


def test_coverage_unproven_write_is_not_called_outside_the_record(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": dict(UNPROVEN)},
    ])
    lineage = crv._explain(object(), "out/res.csv", B)
    assert lineage["status"] == "producer-unknown"
    assert "coverage-unproven" in lineage["reasons"]


def test_unproven_later_write_blocks_restored_outside_claim(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": {"status": "superseded"}},
        {"call": "w2", "action": "modified", "sha256": C, "evidence": "observed",
         "record": dict(UNPROVEN)},
    ])
    lineage = crv._explain(object(), "out/res.csv", A)
    assert lineage["status"] == "producer-unknown"
    assert "coverage-unproven" in lineage["reasons"]


def test_unproven_later_write_blocks_removed_outside_claim(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "d1", "action": "deleted", "evidence": "observed",
         "record": {"status": "superseded"}},
        {"call": "w2", "action": "created", "sha256": C, "evidence": "observed",
         "record": dict(UNPROVEN)},
    ])
    lineage = crv._explain(object(), "out/res.csv", None)
    assert lineage["status"] == "producer-unknown"


def test_unknown_verdict_without_reasons_is_still_incomplete(monkeypatch) -> None:
    _fake_query(monkeypatch, [
        {"call": "w1", "action": "created", "sha256": A, "evidence": "observed",
         "record": {"status": "unknown"}},
    ])
    assert crv._explain(object(), "out/res.csv", B)["status"] == "producer-unknown"


# -- 2. concurrent creation of outputs/ ------------------------------------------


@pytest.fixture()
def analysis(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living").mkdir(parents=True)
    analysis_root = root / "analysis" / "qc"
    (analysis_root / "scripts").mkdir(parents=True)
    return analysis_root


def _race(monkeypatch, create) -> None:
    """Another process creates ``outputs`` between the existence check and
    this process's mkdir."""
    real_mkdir = Path.mkdir

    def mkdir(self, *args, **kwargs):
        if self.name == "outputs":
            create(self, real_mkdir)
            raise FileExistsError(17, "File exists", str(self))
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", mkdir)


@pytest.mark.parametrize("error", [rv.ValueRegistrationError, rv.AccessRecordError])
def test_outputs_created_concurrently_is_tolerated(monkeypatch, analysis: Path, error) -> None:
    _race(monkeypatch, lambda path, real_mkdir: real_mkdir(path))
    target = rv._outputs_file(analysis, "numbers.json", error=error)
    assert target == analysis / "outputs" / "numbers.json"
    assert (analysis / "outputs").is_dir()


def test_symlink_planted_during_creation_is_refused(monkeypatch, analysis: Path,
                                                    tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    _race(monkeypatch,
          lambda path, real_mkdir: path.symlink_to(outside, target_is_directory=True))
    with pytest.raises(rv.ValueRegistrationError, match="symlink"):
        rv._outputs_file(analysis, "numbers.json")


def test_file_planted_during_creation_is_refused(monkeypatch, analysis: Path) -> None:
    _race(monkeypatch, lambda path, real_mkdir: path.write_text("x"))
    with pytest.raises(rv.ValueRegistrationError, match="not a directory"):
        rv._outputs_file(analysis, "numbers.json")


# -- 3. --overwrite keeps the replaced contract's mode ---------------------------


@pytest.fixture()
def umask_022():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_overwrite_preserves_existing_mode(tmp_path: Path, umask_022) -> None:
    dest = tmp_path / "data_contract.json"
    dest.write_text("{}\n")
    os.chmod(dest, 0o600)
    dcc.write_atomic_json(str(dest), {"a": 1}, replace=True)
    assert json.loads(dest.read_text()) == {"a": 1}
    assert _mode(dest) == 0o600


def test_new_contract_uses_umask(tmp_path: Path, umask_022) -> None:
    dest = tmp_path / "data_contract.json"
    dcc.write_atomic_json(str(dest), {"a": 1}, replace=False)
    assert _mode(dest) == 0o644
    other = tmp_path / "other.json"
    dcc.write_atomic_json(str(other), {"a": 1}, replace=True)
    assert _mode(other) == 0o644
