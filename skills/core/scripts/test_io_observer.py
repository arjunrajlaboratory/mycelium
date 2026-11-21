"""Core of the observed-I/O recorder: scope walk, snapshot diff, stable hashing."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import io_observer as io  # noqa: E402


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "data" / "raw").mkdir(parents=True)
    (root / "data" / "raw" / "a.csv").write_text("a\n1\n")
    (root / "analysis").mkdir()
    (root / "analysis" / "fit.py").write_text("print(1)\n")
    return root


def _far_future(seconds: float = 30.0) -> float:
    return time.monotonic() + seconds


# ---------- walk ----------


def test_walk_lists_files_including_gitignored_data(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / ".gitignore").write_text("data/\n")
    snap = io.walk(root)
    assert {"data/raw/a.csv", "analysis/fit.py", ".gitignore"} <= set(snap.entries)
    assert not snap.truncated and not snap.timed_out


@pytest.mark.parametrize(
    "excluded",
    [
        ".git/HEAD",
        ".mycelium/io/calls.ndjson",
        ".living/learnings.md",
        ".claude/worktrees/x/file.csv",
        ".codex/hooks.json",
        "node_modules/p/index.js",
        "analysis/__pycache__/fit.pyc",
        "analysis/.ipynb_checkpoints/n.ipynb",
        ".snakemake/log/x.log",
        "data/.DS_Store",
        "data/._a.csv",
    ],
)
def test_walk_skips_runtime_and_tooling_paths(tmp_path: Path, excluded: str) -> None:
    root = _repo(tmp_path)
    target = root / excluded
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    assert excluded not in io.walk(root).entries


@pytest.mark.parametrize("marker", ["pyvenv.cfg", "conda-meta", ".git"])
def test_walk_skips_environments_and_nested_repositories(tmp_path: Path, marker: str) -> None:
    root = _repo(tmp_path)
    env = root / "envs" / "e"
    env.mkdir(parents=True)
    (env / marker).mkdir() if marker != "pyvenv.cfg" else (env / marker).write_text("")
    (env / "lib.py").write_text("x")
    entries = io.walk(root).entries
    assert "envs/e/lib.py" not in entries


def test_walk_does_not_follow_symlinked_directory_and_reports_it(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "volume"
    outside.mkdir()
    (outside / "big.bam").write_text("x")
    os.symlink(outside, root / "data" / "linked")
    snap = io.walk(root)
    assert not any(path.startswith("data/linked/") for path in snap.entries)
    assert {"path": "data/linked", "why": "symlink", "target": str(outside)} in snap.unwatched


def test_walk_entry_cap_marks_truncated(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    for index in range(20):
        (root / "data" / f"f{index}.txt").write_text("x")
    snap = io.walk(root, entry_cap=5)
    assert snap.truncated
    assert len(snap.entries) == 5


def test_whole_second_mtimes_do_not_make_a_device_coarse(tmp_path: Path) -> None:
    # Data copied with `cp -p` / `rsync -a` / `tar x` keeps whole-second
    # mtimes on a fine-grained filesystem; that must not widen the racy
    # window to two seconds.
    root = _repo(tmp_path)
    for path in root.rglob("*"):
        if path.is_file():
            os.utime(path, (1_700_000_000, 1_700_000_000))
    assert io.walk(root).coarse_devices == frozenset()


def test_walk_time_bound_marks_timed_out(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    snap = io.walk(root, seconds=-1)
    assert snap.timed_out


def test_scope_file_adds_excludes_and_ignores_escapes(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "scratch").mkdir()
    (root / "scratch" / "tmp.csv").write_text("x")
    (root / ".mycelium" / "io").mkdir(parents=True)
    (root / ".mycelium" / "io" / "scope").write_text("exclude scratch\nexclude ../etc\nbogus line\n")
    entries = io.walk(root).entries
    assert "scratch/tmp.csv" not in entries
    assert "data/raw/a.csv" in entries


# ---------- diff ----------


def test_diff_reports_created_modified_deleted_and_rename_candidate(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "data" / "old.csv").write_text("o\n")
    pre = io.walk(root).entries
    (root / "out.csv").write_text("new\n")
    (root / "data" / "raw" / "a.csv").write_text("a\n2\n3\n")
    (root / "analysis" / "fit.py").unlink()
    os.rename(root / "data" / "old.csv", root / "data" / "moved.csv")
    post = io.walk(root).entries
    result = io.diff(pre, post)
    assert "out.csv" in result.created
    assert result.modified == ["data/raw/a.csv"]
    assert "analysis/fit.py" in result.deleted
    assert ("data/old.csv", "data/moved.csv") in result.rename_candidates


def test_diff_between_identical_snapshots_is_empty(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    snap = io.walk(root).entries
    assert io.diff(snap, dict(snap)) == io.Diff([], [], [], [])


# ---------- snapshot round trip ----------


def test_snapshot_round_trip(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    snap = io.walk(root)
    (tmp_path / "state").mkdir()
    path = io.snapshot_path(tmp_path / "state", "claude", "sess-1", "toolu_1")
    io.write_snapshot(path, snap, {"call_id": "toolu_1"}, io_root=tmp_path / "state" / "io")
    loaded = io.read_snapshot(path)
    assert loaded is not None
    assert loaded[0].entries == snap.entries
    assert loaded[1] == {"call_id": "toolu_1"}


@pytest.mark.parametrize("bad", [".", "..", "a/b", "x" * 300, "é"])
def test_snapshot_path_components_never_escape(tmp_path: Path, bad: str) -> None:
    path = io.snapshot_path(tmp_path / "state", bad, bad, bad)
    assert path.resolve().is_relative_to((tmp_path / "state" / "io" / "pre").resolve())
    assert len(path.relative_to(tmp_path / "state" / "io" / "pre").parts) == 3


def test_corrupt_snapshot_reads_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "x.snap"
    path.write_bytes(b"not marshal")
    assert io.read_snapshot(path) is None


def test_symlinked_snapshot_is_not_read(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (tmp_path / "state").mkdir()
    real = io.snapshot_path(tmp_path / "state", "claude", "s", "real")
    io.write_snapshot(real, io.walk(root), {}, io_root=tmp_path / "state" / "io")
    link = real.with_name("link.snap")
    os.symlink(real, link)
    assert io.read_snapshot(link) is None


# ---------- stable hashing ----------


def test_stable_hash_hashes_and_caches_a_settled_file(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    # Hashed in the same timestamp tick as the write: correct, but racy, so
    # the result is not cached.
    racy_cache: dict[str, str] = {}
    result, ident = io.stable_hash(
        path, None, deadline=_far_future(), cache={}, new_cache=racy_cache
    )
    assert result.status == "hashed"
    assert result.sha256 == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert racy_cache == {}
    time.sleep(0.05)
    settled_cache: dict[str, str] = {}
    again, _ = io.stable_hash(
        path, ident, deadline=_far_future(), cache={}, new_cache=settled_cache
    )
    assert again.sha256 == result.sha256
    assert settled_cache == {io._cache_key(ident): result.sha256}


def test_stable_hash_reuses_cache_for_same_identity(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    ident = io.identity_of(os.lstat(path))
    cache = {io._cache_key(ident): "f" * 64}
    result, _ = io.stable_hash(path, ident, deadline=_far_future(), cache=cache, new_cache={})
    assert result == io.HashResult("f" * 64, "cached")


def test_stable_hash_refuses_a_file_changed_since_the_expected_identity(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    ident = io.identity_of(os.lstat(path))
    path.write_bytes(b"abcd")
    result, _ = io.stable_hash(path, ident, deadline=_far_future(), cache={}, new_cache={})
    assert result.status == "unstable" and result.sha256 is None


def test_stable_hash_past_budget_is_pending_not_guessed(tmp_path: Path) -> None:
    path = tmp_path / "a.csv"
    path.write_bytes(b"abc")
    result, ident = io.stable_hash(
        path, None, deadline=time.monotonic() - 1, cache={}, new_cache={}
    )
    assert result == io.HashResult(None, "pending")
    assert ident is not None


@pytest.mark.parametrize("kind", ["symlink", "fifo", "missing"])
def test_stable_hash_never_reads_non_regular_paths(tmp_path: Path, kind: str) -> None:
    path = tmp_path / "x"
    if kind == "symlink":
        (tmp_path / "t").write_text("x")
        os.symlink(tmp_path / "t", path)
    elif kind == "fifo":
        os.mkfifo(path)
    result, _ = io.stable_hash(path, None, deadline=_far_future(), cache={}, new_cache={})
    assert result.status == "unavailable"


def test_cache_round_trip(tmp_path: Path) -> None:
    io.flush_cache(tmp_path, {"1:2:3:4:5": "a" * 64})
    io.flush_cache(tmp_path, {"6:7:8:9:10": "b" * 64})
    assert io.load_cache(tmp_path) == {"1:2:3:4:5": "a" * 64, "6:7:8:9:10": "b" * 64}


# ---------- call journal ----------


def _pre(state: Path, call: str, **kw):
    return io.journal_event(state, "pre", call, host="claude", session="s1", tool="Bash", **kw)


def _post(state: Path, call: str, **kw):
    return io.journal_event(state, "post", call, host="claude", session="s1", tool="Bash", **kw)


def test_overlap_survives_the_other_call_finishing_first(tmp_path: Path) -> None:
    # A and B overlap; A finishes before B's Post. B must still list A.
    _pre(tmp_path, "A")
    _pre(tmp_path, "B")
    _post(tmp_path, "A")
    _, intervals, _ = _post(tmp_path, "B")
    assert [o["call"] for o in io.overlapping(intervals, "B")] == ["A"]
    assert [o["call"] for o in io.overlapping(intervals, "A")] == ["B"]


def test_sequential_calls_do_not_overlap(tmp_path: Path) -> None:
    _pre(tmp_path, "A")
    _post(tmp_path, "A")
    _pre(tmp_path, "B")
    _, intervals, _ = _post(tmp_path, "B")
    assert io.overlapping(intervals, "B") == []


def test_open_interval_overlaps_every_later_call(tmp_path: Path) -> None:
    _pre(tmp_path, "bg", background=True)
    _pre(tmp_path, "C")
    _, intervals, _ = _post(tmp_path, "C")
    (only,) = io.overlapping(intervals, "C")
    assert only["call"] == "bg" and only["open"] and only["background"]


def test_long_running_call_stays_open_past_two_hours(tmp_path: Path) -> None:
    # A Codex command that became a background terminal is never flagged at
    # Pre; closing it early would let its writes pass as another call's.
    start = time.time_ns()
    _pre(tmp_path, "long", now_ns=start)
    three_hours = start + 3 * 3600 * 1_000_000_000
    _, _, expired = _pre(tmp_path, "D", now_ns=three_hours)
    assert expired == []
    _, intervals, _ = _post(tmp_path, "D", now_ns=three_hours)
    assert [o["call"] for o in io.overlapping(intervals, "D")] == ["long"]


def test_open_call_expires_after_a_day_with_a_closed_end(tmp_path: Path) -> None:
    start = time.time_ns()
    _pre(tmp_path, "lost", now_ns=start)
    later = start + (io.OPEN_INTERVAL_MAX_SECONDS + 1) * 1_000_000_000
    _, _, expired = _pre(tmp_path, "D", now_ns=later)
    assert [i.call for i in expired] == ["lost"]
    _, intervals, _ = _post(tmp_path, "D", now_ns=later)
    assert io.overlapping(intervals, "D") == []


def test_turn_end_closes_this_sessions_foreground_calls_only(tmp_path: Path) -> None:
    _pre(tmp_path, "denied")
    _pre(tmp_path, "bg", background=True)
    io.journal_event(tmp_path, "pre", "other", host="claude", session="s2", tool="Bash")
    assert io.turn_end(tmp_path, host="claude", session="s1") == 1
    _pre(tmp_path, "next")
    _, intervals, _ = _post(tmp_path, "next")
    assert sorted(o["call"] for o in io.overlapping(intervals, "next")) == ["bg", "other"]


def test_compaction_keeps_open_intervals_and_a_monotonic_counter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(io, "JOURNAL_COMPACT_LINES", 10)
    _pre(tmp_path, "keep-open")
    last = 0
    for index in range(20):
        seq, _, _ = _pre(tmp_path, f"c{index}")
        assert seq > last
        last = seq
        seq, _, _ = _post(tmp_path, f"c{index}")
        assert seq > last
        last = seq
    records = io._journal_records(io.journal_path(tmp_path))
    assert any(r["call"] == "keep-open" for r in records)
    # Everything closed after the open call started is still needed.
    assert len(records) >= 2


def test_compaction_drops_intervals_no_open_call_can_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(io, "JOURNAL_COMPACT_LINES", 10)
    for index in range(30):
        _pre(tmp_path, f"c{index}")
        _post(tmp_path, f"c{index}")
    records = io._journal_records(io.journal_path(tmp_path))
    assert len(records) <= 11


def test_concurrent_journal_appends_lose_nothing(tmp_path: Path) -> None:
    import multiprocessing

    with multiprocessing.get_context("spawn").Pool(8) as pool:
        pool.starmap(_pre_only, [(str(tmp_path), f"p{i}") for i in range(64)])
    records = io._journal_records(io.journal_path(tmp_path))
    assert sorted(r["call"] for r in records) == sorted(f"p{i}" for i in range(64))
    assert len({r["seq"] for r in records}) == 64


def _pre_only(state: str, call: str) -> None:
    io.journal_event(Path(state), "pre", call, host="h", session="s", tool="Bash")


def test_symlinked_io_directory_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    os.symlink(outside, state / "io")
    with pytest.raises(OSError):
        _pre(state, "A")
    assert list(outside.iterdir()) == []


def test_racy_identity_detection() -> None:
    now = 10_000_000_000_000
    fresh = io.Identity(1, 2, 3, now - 1_000, now - 1_000, 0o100644)
    settled = io.Identity(1, 2, 3, now - 5_000_000_000, now - 5_000_000_000, 0o100644)
    assert io.is_racy(fresh, now, coarse=False)
    assert not io.is_racy(settled, now, coarse=False)
    one_second_ago = io.Identity(1, 2, 3, now - 1_000_000_000, now - 1_000_000_000, 0o100644)
    assert io.is_racy(one_second_ago, now, coarse=True)
