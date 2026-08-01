import importlib.util
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("finalize_session_log.py")
SPEC = importlib.util.spec_from_file_location("finalize_session_log", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _log(path: Path) -> str:
    content = (
        "---\n"
        "session_id: 2026-08-01-001\n"
        "ended:\n"
        "duration_minutes:\n"
        "files_changed:\n"
        "---\n"
        "\n# Session\n"
    )
    path.write_text(content)
    return content


def _finalize(path: Path) -> None:
    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:42:00-0400",
        duration_minutes=7,
        files_changed=2,
        end_time="18:42",
        changed_paths=["src/a.py", "data/input.csv"],
    )


def test_finalizer_publishes_frontmatter_and_footer_together(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    path.chmod(0o640)

    _finalize(path)

    completed = path.read_text()
    assert "ended: 2026-08-01T18:42:00-0400" in completed
    assert "duration_minutes: 7" in completed
    assert "files_changed: 2" in completed
    assert "### 18:42 — Session ended (7m, 2 files)" in completed
    assert "- Modified: a.py, input.csv" in completed
    assert "- `src/a.py`" in completed
    assert path.stat().st_mode & 0o777 == 0o640


def test_finalizer_is_idempotent(tmp_path):
    path = tmp_path / "session.md"
    _log(path)

    _finalize(path)
    _finalize(path)

    assert path.read_text().count("Session ended") == 1


def test_failed_atomic_replace_preserves_original_and_removes_temp(
    tmp_path, monkeypatch
):
    path = tmp_path / "session.md"
    original = _log(path)

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(MODULE.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        _finalize(path)

    assert path.read_text() == original
    assert list(tmp_path.glob(f".{path.name}.*.tmp")) == []


def test_finalizer_rejects_symlink(tmp_path):
    target = tmp_path / "target.md"
    _log(target)
    link = tmp_path / "session.md"
    os.symlink(target, link)

    with pytest.raises(ValueError, match="regular file"):
        _finalize(link)

    assert "ended:\n" in target.read_text()
