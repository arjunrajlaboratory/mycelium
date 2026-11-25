"""Bounded-cost contract for session accounting on large dirty trees.

SessionStart and Stop must not read the content of every untracked file: a
repository with hundreds of thousands of untracked outputs made both hooks
exceed their host timeouts, so Stop was killed before finalizing and repeated
the same scan on every turn. These tests pin the cost bound and the detection
guarantees that must survive it.
"""

from __future__ import annotations

import builtins
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import session_file_changes as sfc
from session_file_changes import build_snapshot, collect_changes, load_snapshot

pytestmark = pytest.mark.platform

SCRIPT = Path(__file__).with_name("session_file_changes.py")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("initial\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def _untracked_tree(repo: Path, count: int) -> list[Path]:
    data = repo / "outputs"
    data.mkdir()
    files = []
    for index in range(count):
        path = data / f"result-{index:03d}.txt"
        path.write_text(f"result {index:03d}\n")
        files.append(path)
    # Let every creation ctime fall strictly before the snapshot reference.
    time.sleep(0.02)
    return files


class _ReadSpy:
    """Record every file under ``root`` opened by path.

    Covers ``open``/``Path.open`` and ``os.open`` (the hashing helper opens a
    descriptor and wraps it with ``os.fdopen``, which never sees the path).
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
        self.root = str(root.resolve())
        self.opened: list[str] = []
        real_open = io.open
        real_os_open = os.open

        def record(file: Any) -> None:
            if isinstance(file, (str, os.PathLike)):
                resolved = os.path.realpath(os.fspath(file))
                if resolved.startswith(self.root + os.sep):
                    self.opened.append(resolved)

        def spy(file: Any, *args: Any, **kwargs: Any) -> Any:
            record(file)
            return real_open(file, *args, **kwargs)

        def os_spy(file: Any, *args: Any, **kwargs: Any) -> Any:
            record(file)
            return real_os_open(file, *args, **kwargs)

        monkeypatch.setattr(io, "open", spy)
        monkeypatch.setattr(builtins, "open", spy)
        monkeypatch.setattr(os, "open", os_spy)

    def relative(self, repo: Path) -> set[str]:
        base = str(repo.resolve()) + os.sep
        return {path[len(base):] for path in self.opened}


def _no_racy_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Files created by the test are milliseconds old; a zero margin lets them
    # count as settled so the metadata path, not the racy path, is exercised.
    monkeypatch.setattr(sfc, "RACY_MARGIN_NS", 0, raising=False)


def _snapshot_budget(monkeypatch: pytest.MonkeyPatch, files: int, bytes_: int) -> None:
    monkeypatch.setattr(sfc, "SNAPSHOT_CONTENT_BUDGET_FILES", files, raising=False)
    monkeypatch.setattr(sfc, "SNAPSHOT_CONTENT_BUDGET_BYTES", bytes_, raising=False)


def _collect_file_budget(monkeypatch: pytest.MonkeyPatch, files: int) -> None:
    monkeypatch.setattr(sfc, "COLLECT_CONTENT_BUDGET_FILES", files, raising=False)


def _fingerprinted(snapshot: dict[str, Any]) -> set[str]:
    return {
        path
        for path, entry in snapshot["files"].items()
        if isinstance(entry["stat"].get("content"), dict)
    }


def _pin_metadata(
    monkeypatch: pytest.MonkeyPatch, baseline: dict[str, Any], relative: str
) -> None:
    """Report the baseline ctime and inode for one path, as FAT/exFAT or an
    unflushed shared mmap would after a same-size rewrite with mtime restored."""
    real = sfc._metadata
    old = baseline["files"][relative]["stat"]

    def pinned(repo_root: Path, relative_path: str) -> Any:
        result = real(repo_root, relative_path)
        if result is not None and relative_path == relative:
            result = {**result, "ctime_ns": old["ctime_ns"], "ino": old["ino"]}
        return result

    monkeypatch.setattr(sfc, "_metadata", pinned)


def _same_size_rewrite_restoring_mtime(path: Path, text: str) -> None:
    before = path.stat()
    assert len(text.encode()) == before.st_size
    path.write_text(text)
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def _legacy_v2_snapshot(repo: Path) -> dict[str, Any]:
    """Frozen copy of the schema-2 baseline written by earlier releases."""

    def content(path: Path) -> dict[str, Any] | None:
        stat = path.lstat()
        if path.is_symlink():
            return {"kind": "symlink", "target": os.readlink(path)}
        if not path.is_file():
            return {"kind": "other", "mode": stat.st_mode}
        digest = hashlib.sha256(path.read_bytes())
        return {"kind": "file", "size": stat.st_size, "sha256": digest.hexdigest()}

    payload = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        check=True,
        capture_output=True,
    ).stdout
    files: dict[str, Any] = {}
    for record in payload.split(b"\0"):
        if len(record) < 4:
            continue
        relative = record[3:].decode()
        stat = (repo / relative).lstat()
        files[relative] = {
            "status": record[:2].decode(),
            "previous_path": None,
            "stat": {
                "mode": stat.st_mode,
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "content": content(repo / relative),
            },
        }
    head = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return {
        "schema_version": 2,
        "head": head,
        "head_reflog_entries": None,
        "files": files,
        "living_files": {},
    }


# --- cost bound -----------------------------------------------------------


def test_collect_on_unchanged_tree_reads_only_fingerprinted_files_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 40)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=10, bytes_=1 << 20)
    baseline = build_snapshot(repo)
    fingerprinted = _fingerprinted(baseline)
    assert len(fingerprinted) == 10
    _collect_file_budget(monkeypatch, 4)

    spy = _ReadSpy(monkeypatch, repo / "outputs")
    assert collect_changes(repo, baseline) == []
    # Positive control: Stop does re-verify, and only up to its budget.
    assert len(spy.opened) == 4
    assert spy.relative(repo) <= fingerprinted


def test_collect_with_zero_content_budget_opens_nothing_on_unchanged_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 40)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    monkeypatch.setenv("MYCELIUM_COLLECT_CONTENT_BUDGET_FILES", "0")

    spy = _ReadSpy(monkeypatch, repo / "outputs")
    assert collect_changes(repo, baseline) == []
    assert spy.opened == []


def test_snapshot_reads_at_most_its_content_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 40)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=5, bytes_=1 << 20)

    spy = _ReadSpy(monkeypatch, repo / "outputs")
    build_snapshot(repo)

    assert len(spy.opened) == 5


def test_snapshot_budget_can_be_set_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 12)
    _no_racy_margin(monkeypatch)
    monkeypatch.setenv("MYCELIUM_SNAPSHOT_CONTENT_BUDGET_FILES", "3")

    assert len(_fingerprinted(build_snapshot(repo))) == 3


@pytest.mark.parametrize(
    "raw", ["", "-1", "1e3", " 5", "5 ", "+5", "\u0663", "9" * 19, "9" * 5000]
)
def test_malformed_budget_environment_value_keeps_the_default(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("MYCELIUM_TEST_BUDGET", raw)
    assert sfc._env_int("MYCELIUM_TEST_BUDGET", 17) == 17


def test_budget_environment_value_is_read_as_decimal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYCELIUM_TEST_BUDGET", "0")
    assert sfc._env_int("MYCELIUM_TEST_BUDGET", 17) == 0
    monkeypatch.setenv("MYCELIUM_TEST_BUDGET", "012")
    assert sfc._env_int("MYCELIUM_TEST_BUDGET", 17) == 12
    monkeypatch.setenv("MYCELIUM_TEST_BUDGET", "9" * 18)
    assert sfc._env_int("MYCELIUM_TEST_BUDGET", 17) == int("9" * 18)


def test_snapshot_budget_selects_the_same_files_every_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 12)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=4, bytes_=1 << 20)

    def hashed(snapshot: dict[str, Any]) -> list[str]:
        return sorted(
            path
            for path, entry in snapshot["files"].items()
            if entry["stat"].get("content") is not None
        )

    first = hashed(build_snapshot(repo))
    second = hashed(build_snapshot(repo))
    assert len(first) == 4
    assert first == second


def test_dirty_tracked_files_are_hashed_before_untracked_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("dirty tracked edit\n")
    _untracked_tree(repo, 10)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=1, bytes_=1 << 20)

    snapshot = build_snapshot(repo)

    assert snapshot["files"]["README.md"]["stat"]["content"]["sha256"]


# --- detection guarantees that must survive the bound ---------------------


def test_restored_mtime_rewrite_of_unhashed_file_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=0, bytes_=0)
    baseline = build_snapshot(repo)
    assert baseline["files"]["outputs/result-000.txt"]["stat"]["content"] is None

    _same_size_rewrite_restoring_mtime(first, "result 999\n")

    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


def test_restored_mtime_rewrite_of_hashed_file_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    assert baseline["files"]["outputs/result-000.txt"]["stat"]["content"]["sha256"]

    _same_size_rewrite_restoring_mtime(first, "result 999\n")

    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


def test_metadata_only_change_of_hashed_file_is_not_session_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # macOS attaches provenance attributes to files without changing them,
    # which moves ctime. Stored content clears that false alarm.
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    before = first.stat()

    os.chmod(first, before.st_mode & 0o7777)
    assert first.stat().st_ctime_ns != before.st_ctime_ns

    assert collect_changes(repo, baseline) == []


def test_metadata_only_change_of_unhashed_file_counts_conservatively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=0, bytes_=0)
    baseline = build_snapshot(repo)

    os.chmod(first, first.stat().st_mode & 0o7777)

    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


def test_racy_file_beyond_the_budget_counts_conservatively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A file that changed just before the snapshot may change again without
    # moving its ctime. Without stored content Stop cannot verify it, so it
    # is charged even when the session never touches it (documented limit).
    repo = _repo(tmp_path)
    _untracked_tree(repo, 2)
    _snapshot_budget(monkeypatch, files=0, bytes_=0)
    baseline = build_snapshot(repo)
    assert baseline["files"]["outputs/result-000.txt"]["stat"]["content"] is None

    assert collect_changes(repo, baseline) == [
        "outputs/result-000.txt",
        "outputs/result-001.txt",
    ]


@pytest.mark.parametrize("collect_files", [None, "0"], ids=["re-read", "racy-rule-only"])
def test_racy_file_changed_within_one_timestamp_granule_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collect_files: str | None
) -> None:
    # A coarse filesystem can give a later rewrite the same ctime. Files whose
    # metadata is within the racy margin must have their content verified at
    # Stop. With no budget for Stop's optional re-read pass, only the racy rule
    # can catch the rewrite: the file must be verified, cannot be within the
    # budget, and so counts as changed.
    repo = _repo(tmp_path)
    [first] = _untracked_tree(repo, 1)
    monkeypatch.setattr(sfc, "RACY_MARGIN_NS", 60 * 1_000_000_000, raising=False)
    _snapshot_budget(monkeypatch, files=2, bytes_=1 << 20)
    baseline = build_snapshot(repo)
    entry = baseline["files"]["outputs/result-000.txt"]["stat"]
    assert entry["content"]["sha256"]

    _same_size_rewrite_restoring_mtime(first, "result 999\n")
    # Simulate a coarse filesystem: the rewrite did not move ctime.
    real_lstat = Path.lstat

    def coarse_lstat(self: Path) -> Any:
        result = real_lstat(self)
        if self == first:
            return _with_ctime(result, entry["ctime_ns"])
        return result

    if collect_files is not None:
        monkeypatch.setenv("MYCELIUM_COLLECT_CONTENT_BUDGET_FILES", collect_files)
    monkeypatch.setattr(Path, "lstat", coarse_lstat)
    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


class _StatWithCtime:
    def __init__(self, base: os.stat_result, ctime_ns: int) -> None:
        self._base = base
        self.st_ctime_ns = ctime_ns

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _with_ctime(base: os.stat_result, ctime_ns: int) -> Any:
    return _StatWithCtime(base, ctime_ns)


def test_write_that_moves_no_timestamp_is_detected_in_fingerprinted_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    assert "outputs/result-000.txt" in _fingerprinted(baseline)

    _same_size_rewrite_restoring_mtime(first, "result 999\n")
    _pin_metadata(monkeypatch, baseline, "outputs/result-000.txt")

    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


def test_write_that_moves_no_timestamp_outside_fingerprinted_set_is_unseen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Documented limit of the bound: with identical metadata and no stored
    # content there is nothing left to compare. This pins the gap so a change
    # to it is deliberate.
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=0, bytes_=0)
    baseline = build_snapshot(repo)

    _same_size_rewrite_restoring_mtime(first, "result 999\n")
    _pin_metadata(monkeypatch, baseline, "outputs/result-000.txt")

    assert collect_changes(repo, baseline) == []


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        # No budget: the path whose ctime moved cannot be verified, so it is
        # charged; the path with unchanged metadata is trusted.
        (0, ["outputs/result-000.txt"]),
        # One read goes to the path whose metadata moved, and clears it.
        (1, []),
        # A second read reaches the newest file, the timestamp-free rewrite.
        (2, ["outputs/result-002.txt"]),
    ],
)
def test_collect_budget_verifies_moved_metadata_before_unchanged_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: int, expected: list[str]
) -> None:
    repo = _repo(tmp_path)
    first, _, newest = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)

    os.chmod(first, first.stat().st_mode & 0o7777)  # ctime moves, content same
    _same_size_rewrite_restoring_mtime(newest, "result 999\n")
    _pin_metadata(monkeypatch, baseline, "outputs/result-002.txt")
    _collect_file_budget(monkeypatch, files)

    assert collect_changes(repo, baseline) == expected


def test_file_with_future_mtime_is_not_charged_at_every_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    tomorrow = time.time_ns() + 86_400 * 1_000_000_000
    os.utime(first, ns=(tomorrow, tomorrow))
    time.sleep(0.02)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=0, bytes_=0)
    baseline = build_snapshot(repo)

    assert collect_changes(repo, baseline) == []
    assert collect_changes(repo, baseline) == []


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
@pytest.mark.parametrize("replacement", ["fifo", "symlink"])
def test_hashing_never_blocks_on_or_follows_a_swapped_in_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    # A path can be replaced between the type check and the open. A FIFO with
    # no writer would block a plain open forever; a symlink would be followed.
    path = tmp_path / "input.txt"
    path.write_text("original\n")
    target = tmp_path / "target.txt"
    target.write_text("outside\n")
    original_open = os.open

    def swapped_open(name, *args, **kwargs):
        if name == path:
            path.unlink()
            if replacement == "fifo":
                os.mkfifo(path)
            else:
                path.symlink_to(target)
        return original_open(name, *args, **kwargs)

    monkeypatch.setattr(os, "open", swapped_open)
    def timed_out(*_: Any) -> None:
        raise TimeoutError("hashing blocked on a FIFO")

    previous = signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(5)
    try:
        assert sfc._content_fingerprint(path) is None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def test_deleted_and_new_untracked_files_are_still_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    files = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    _snapshot_budget(monkeypatch, files=0, bytes_=0)
    baseline = build_snapshot(repo)

    files[1].unlink()
    (repo / "outputs" / "fresh.txt").write_text("new\n")

    assert collect_changes(repo, baseline) == [
        "outputs/fresh.txt",
        "outputs/result-001.txt",
    ]


def test_collect_budget_exhaustion_counts_unverified_files_as_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    files = _untracked_tree(repo, 3)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    for path in files:
        os.chmod(path, path.stat().st_mode & 0o7777)

    monkeypatch.setattr(sfc, "COLLECT_CONTENT_BUDGET_BYTES", 0, raising=False)
    assert collect_changes(repo, baseline) == [
        "outputs/result-000.txt",
        "outputs/result-001.txt",
        "outputs/result-002.txt",
    ]


# --- schema-2 baselines written by the runtime being replaced ------------


def test_schema_2_baseline_is_still_accepted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "preexisting.txt").write_text("old\n")
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(_legacy_v2_snapshot(repo)))

    assert load_snapshot(path) is not None


def test_schema_2_unchanged_tree_reports_nothing_and_reads_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 20)
    legacy = _legacy_v2_snapshot(repo)
    _no_racy_margin(monkeypatch)
    reference = time.time_ns()
    _collect_file_budget(monkeypatch, 5)

    spy = _ReadSpy(monkeypatch, repo / "outputs")
    assert collect_changes(repo, legacy, legacy_reference_ns=reference) == []
    assert len(spy.opened) == 5


def test_schema_2_write_that_moves_no_timestamp_is_detected_within_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Schema 2 stored content for every path, so the re-read covers a write
    # whose ctime falls before the session start (clock step, FAT, mmap).
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    legacy = _legacy_v2_snapshot(repo)
    _no_racy_margin(monkeypatch)
    _same_size_rewrite_restoring_mtime(first, "result 999\n")
    later = time.time_ns() + 60 * 1_000_000_000

    assert collect_changes(repo, legacy, legacy_reference_ns=later) == [
        "outputs/result-000.txt"
    ]


def test_schema_2_restored_mtime_rewrite_is_detected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    legacy = _legacy_v2_snapshot(repo)
    _no_racy_margin(monkeypatch)
    reference = time.time_ns()
    time.sleep(0.02)

    _same_size_rewrite_restoring_mtime(first, "result 999\n")

    assert collect_changes(repo, legacy, legacy_reference_ns=reference) == [
        "outputs/result-000.txt"
    ]


def test_schema_2_metadata_only_change_is_cleared_by_stored_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    [first, *_] = _untracked_tree(repo, 3)
    legacy = _legacy_v2_snapshot(repo)
    _no_racy_margin(monkeypatch)
    reference = time.time_ns()
    time.sleep(0.02)

    os.chmod(first, first.stat().st_mode & 0o7777)

    assert collect_changes(repo, legacy, legacy_reference_ns=reference) == []


def test_schema_2_cli_without_start_ts_does_not_charge_the_whole_tree(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 30)
    # Runtime state lives under .mycelium/, which collect always omits.
    baseline = repo / ".mycelium" / "baseline.json"
    baseline.parent.mkdir()
    baseline.write_text(json.dumps(_legacy_v2_snapshot(repo)))
    # The baseline file's own write time stands in for the missing session
    # start; make that reference clearly later than every untracked file.
    later = time.time_ns() + 5 * 1_000_000_000
    os.utime(baseline, ns=(later, later))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "collect",
            "--repo-root",
            str(repo),
            "--baseline",
            str(baseline),
        ],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "MYCELIUM_COLLECT_CONTENT_BUDGET_BYTES": "0"},
    )
    assert result.stdout == ""


# --- malformed baselines never raise and never read as "unchanged" --------


@pytest.mark.parametrize(
    "stat",
    [
        "not-a-dict",
        {"mode": "33188", "size": 11, "mtime_ns": 1, "ctime_ns": 1, "ino": 1},
        {"size": 11, "mtime_ns": 1, "ctime_ns": 1, "ino": 1},
        {"mode": True, "size": 11, "mtime_ns": 1, "ctime_ns": 1, "ino": 1},
        {"mode": 33188, "size": 11, "mtime_ns": 1, "ino": 1},
        {"mode": 33188, "size": 11, "mtime_ns": 1, "ctime_ns": 1, "ino": 1, "content": []},
    ],
)
def test_malformed_baseline_entry_counts_as_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stat: Any
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 2)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    real = baseline["files"]["outputs/result-000.txt"]["stat"]
    if isinstance(stat, dict) and "content" in stat:
        stat = {**real, "content": stat["content"]}
        # A bad fingerprint matters only when the metadata calls for content.
        os.chmod(
            repo / "outputs" / "result-000.txt",
            (repo / "outputs" / "result-000.txt").stat().st_mode & 0o7777,
        )
    baseline["files"]["outputs/result-000.txt"]["stat"] = stat

    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


@pytest.mark.parametrize("entry", ["text", None, 7, {"status": "??"}])
def test_malformed_baseline_record_counts_as_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: Any
) -> None:
    repo = _repo(tmp_path)
    _untracked_tree(repo, 2)
    _no_racy_margin(monkeypatch)
    baseline = build_snapshot(repo)
    baseline["files"]["outputs/result-000.txt"] = entry

    assert collect_changes(repo, baseline) == ["outputs/result-000.txt"]


@pytest.mark.parametrize("started", [None, "1", True, -1])
def test_schema_3_without_valid_snapshot_time_is_rejected(
    tmp_path: Path, started: Any
) -> None:
    repo = _repo(tmp_path)
    snapshot = build_snapshot(repo)
    if started is None:
        snapshot.pop("snapshot_started_ns", None)
    else:
        snapshot["snapshot_started_ns"] = started
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(snapshot))

    assert load_snapshot(path) is None


def test_unknown_future_schema_is_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    snapshot = build_snapshot(repo)
    snapshot["schema_version"] = 99
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(snapshot))

    assert load_snapshot(path) is None


@pytest.mark.parametrize("phase", ["snapshot", "collect"])
@pytest.mark.parametrize("grow_after_open", [False, True])
def test_content_budget_caps_reads_from_a_growing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, grow_after_open: bool
) -> None:
    repo = _repo(tmp_path)
    path = repo / "output.bin"
    path.write_bytes(b"x")
    baseline = build_snapshot(repo)
    monkeypatch.setenv("MYCELIUM_SNAPSHOT_CONTENT_BUDGET_BYTES", "1")
    monkeypatch.setenv("MYCELIUM_COLLECT_CONTENT_BUDGET_BYTES", "1")
    original_open = os.open
    original_fstat = os.fstat
    original_fdopen = os.fdopen
    opened_fd = None
    bytes_read = 0
    grew = False

    def grow() -> None:
        nonlocal grew
        with path.open("ab") as handle:
            handle.write(b"y" * (1024 * 1024))
        grew = True

    def open_file(name, *args, **kwargs):
        nonlocal opened_fd
        if name != path:
            return original_open(name, *args, **kwargs)
        if not grow_after_open:
            grow()
        opened_fd = original_open(name, *args, **kwargs)
        return opened_fd

    def stat_open_file(fd):
        state = original_fstat(fd)
        if fd == opened_fd and grow_after_open and not grew:
            grow()
        return state

    class ObserveReads:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            return self.handle.__exit__(*args)

        def fileno(self):
            return self.handle.fileno()

        def read(self, size):
            nonlocal bytes_read
            data = self.handle.read(size)
            bytes_read += len(data)
            return data

    def observe_reads(fd, *args, **kwargs):
        handle = original_fdopen(fd, *args, **kwargs)
        return ObserveReads(handle) if fd == opened_fd else handle

    monkeypatch.setattr(os, "open", open_file)
    monkeypatch.setattr(os, "fstat", stat_open_file)
    monkeypatch.setattr(os, "fdopen", observe_reads)
    if phase == "snapshot":
        fingerprint = build_snapshot(repo)["files"]["output.bin"]["stat"]["content"]
    else:
        assert collect_changes(repo, baseline) == ["output.bin"]
    assert grew
    assert bytes_read <= 1
    if phase == "snapshot":
        assert fingerprint is None
