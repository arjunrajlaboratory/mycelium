"""Regressions for the sixth P1-only Codex pass (PR #154): attached-option
and heredoc Digest headers, `bash -c` bodies, uninspectable entries,
shared attribution in --explain, case probes for digit-named checkouts,
and unverifiable deletions."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import check_report_values as crv  # noqa: E402
import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import lineage_redact as redact  # noqa: E402

SECRET = "FIXTURE_VALUE_7"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


DIGEST = f'Digest username="alice", response="{SECRET}"'


@pytest.mark.parametrize("command", [
    f"curl --header='Authorization: {DIGEST}' https://x/f.csv",
    f"curl -H'Authorization: {DIGEST}' https://x/f.csv",
    f"curl --header @- https://x/f.csv <<'EOF'\nAuthorization: {DIGEST}\nEOF",
    f"bash -c 'curl --oauth2-bearer {SECRET} https://example.test/file.csv'",
    f"ssh host 'export API_TOKEN={SECRET}; run'",
])
def test_attached_heredoc_and_nested_command_credentials_are_redacted(command: str) -> None:
    assert SECRET not in redact.redact_command(command)


def test_attached_header_in_argv_is_redacted() -> None:
    out = redact.redact_argv(shlex.split(f"curl --header='Authorization: {DIGEST}' u"))
    assert SECRET not in json.dumps(out) and out[1].startswith("--header=")


def test_quoted_commands_without_secrets_are_kept() -> None:
    text = "bash -c 'python fit.py --input data/in.csv'"
    assert redact.redact_command(text) == text


def test_an_uninspectable_entry_is_unwatched(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "data" / "out.csv").write_text("x")
    real = os.DirEntry.stat if hasattr(os.DirEntry, "stat") else None
    assert real is not None
    original_scandir = os.scandir

    class Entry:
        def __init__(self, entry):
            self._e = entry
            self.name = entry.name
            self.path = entry.path

        def stat(self, follow_symlinks=True):
            if self.name == "out.csv":
                raise PermissionError("search denied")
            return self._e.stat(follow_symlinks=follow_symlinks)

    class Wrapper:
        def __init__(self, it):
            self._it = it

        def __iter__(self):
            return (Entry(e) for e in self._it)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._it.close()

    monkeypatch.setattr(io.os, "scandir", lambda p: Wrapper(original_scandir(p)))
    snap = io.walk(root)
    assert "data/out.csv" not in snap.entries
    assert io.watch_status("data/out.csv", snap.unwatched) == "unwatched"


def test_explain_names_no_producer_for_shared_attribution(monkeypatch) -> None:
    write = {"call": "s|claude:read", "sha256": sha("B"), "evidence": "observed",
             "action": "written", "attribution": "shared", "overlapping": ["s|claude:w"],
             "record": {"status": "current-in-record"}, "events": []}

    class FakeIndex:
        gaps: list = []

    monkeypatch.setattr(li, "query_path",
                        lambda index, rel, disk=False: {"writes": [write]})
    monkeypatch.setattr(crv, "_incomplete_reasons", lambda index, rel, writes: [])
    monkeypatch.setattr(crv, "_resolve_pending_writes", lambda index, rel, writes, a: writes)
    out = crv._explain(FakeIndex(), "out.csv", sha("B"))
    assert out["status"] == "producer-shared"
    assert "s|claude:w" in out["candidates"] and "write_call" not in out


def test_a_digit_named_checkout_is_probed_through_its_parent(tmp_path: Path) -> None:
    parent = tmp_path / "Projects"
    (parent / "12345").mkdir(parents=True)
    io._CASE_PROBES.clear()
    expected = (tmp_path / "projects").exists()
    assert io._case_insensitive(str((parent / "12345").resolve())) == expected


def test_an_unverifiable_deletion_is_unverified_not_changed() -> None:
    assert li._headline("current-in-record", "unverified", "deleted") == "unverified"
    assert li._headline("current-in-record", "missing", "deleted") == "current"
    assert li._headline("current-in-record", "match", "deleted") == "changed-outside-record"
