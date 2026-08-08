import importlib.util
import os
import re
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
    first = path.read_text()
    _finalize(path)

    assert path.read_text() == first
    assert path.read_text().count("Session ended") == 1


def _finalization_views(content: str) -> tuple[tuple, tuple, list[str]]:
    """Parse (frontmatter, footer, file list) finalization values from a log."""
    front = re.search(
        r"ended: (?P<ended>[^\n]+)\n.*?"
        r"duration_minutes: (?P<duration>\d+)\n"
        r"files_changed: (?P<files>\d+)\n",
        content,
        re.DOTALL,
    )
    assert front is not None, content
    footer = re.search(
        r"^### (?P<time>[^\n]+) — Session ended "
        r"\((?P<duration>\d+)m, (?P<files>\d+) files\)$",
        content,
        re.MULTILINE,
    )
    assert footer is not None, content
    listed = re.findall(r"^- `([^\n`]+)`$", content, re.MULTILINE)
    return (
        (front.group("duration"), front.group("files")),
        (footer.group("duration"), footer.group("files")),
        listed,
    )


def test_retry_with_new_values_rewrites_footer_and_file_list(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    _finalize(path)

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:44:00-0400",
        duration_minutes=9,
        files_changed=3,
        end_time="18:44",
        changed_paths=["src/a.py", "data/input.csv", "results/out.csv"],
    )

    content = path.read_text()
    assert content.count("Session ended") == 1
    assert content.count("### Files Modified") == 1
    frontmatter, footer, listed = _finalization_views(content)
    assert frontmatter == ("9", "3")
    assert footer == frontmatter
    assert listed == ["src/a.py", "data/input.csv", "results/out.csv"]
    assert "ended: 2026-08-01T18:44:00-0400" in content
    assert "### 18:44 — Session ended" in content
    assert "7m" not in content
    assert "18:42" not in content


def test_retry_with_fewer_files_shrinks_file_list(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    _finalize(path)

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:44:00-0400",
        duration_minutes=9,
        files_changed=0,
        end_time="18:44",
        changed_paths=[],
    )

    content = path.read_text()
    assert content.count("Session ended") == 1
    assert "(9m, 0 files)" in content
    assert "Files Modified" not in content
    assert "- `src/a.py`" not in content


def test_retry_preserves_body_written_after_first_footer(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    _finalize(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n### 18:43 — Continued work after blocked Stop\n- more\n")

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:50:00-0400",
        duration_minutes=15,
        files_changed=3,
        end_time="18:50",
        changed_paths=["src/a.py", "data/input.csv", "results/out.csv"],
    )

    content = path.read_text()
    assert "Continued work after blocked Stop" in content
    assert content.count("Session ended") == 1
    frontmatter, footer, _ = _finalization_views(content)
    assert frontmatter == footer == ("15", "3")
    assert content.index("Continued work") < content.index("Session ended")


def test_authored_session_ended_heading_survives_finalization(tmp_path):
    """Codex P2, round 9 on PR #70: only the machine-emitted footer syntax
    (HH:MM time, numeric duration and file count) is machine-owned."""
    path = tmp_path / "session.md"
    _log(path)
    authored = (
        "\n### Retrospective — Session ended (unexpectedly)\n"
        "- Modified: my authored note about what changed\n"
        "\n### Files Modified\n"
        "- `authored-observation.md`\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(authored)

    _finalize(path)
    _finalize(path)

    content = path.read_text()
    assert "### Retrospective — Session ended (unexpectedly)" in content
    assert "- Modified: my authored note about what changed" in content
    assert "- `authored-observation.md`" in content
    assert content.count("### 18:42 — Session ended (7m, 2 files)") == 1


def test_machine_shaped_footer_inside_code_fence_survives(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    fenced = (
        "\nExample of the finalization format:\n"
        "```markdown\n"
        "### 12:34 — Session ended (5m, 4 files)\n"
        "- Modified: example.py\n"
        "\n### Files Modified\n"
        "- `example.py`\n"
        "```\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(fenced)

    _finalize(path)
    _finalize(path)

    content = path.read_text()
    assert "### 12:34 — Session ended (5m, 4 files)" in content
    assert "- `example.py`" in content
    assert content.count("### 18:42 — Session ended (7m, 2 files)") == 1


def test_inner_fence_lines_do_not_close_a_longer_fence(tmp_path):
    """Codex P2, round 10 on PR #70: a three-backtick or tilde line inside a
    four-backtick fence is content, not a closing delimiter."""
    path = tmp_path / "session.md"
    _log(path)
    fenced = (
        "\n````text\n"
        "```\n"
        "### 12:34 — Session ended (5m, 4 files)\n"
        "~~~\n"
        "### 12:35 — Session ended (6m, 5 files)\n"
        "````\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(fenced)

    _finalize(path)
    _finalize(path)

    content = path.read_text()
    assert "### 12:34 — Session ended (5m, 4 files)" in content
    assert "### 12:35 — Session ended (6m, 5 files)" in content
    assert content.count("### 18:42 — Session ended (7m, 2 files)") == 1


def test_stale_footer_after_properly_closed_long_fence_is_rebuilt(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    fenced = "\n````text\n```\n````\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(fenced)
    _finalize(path)

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:44:00-0400",
        duration_minutes=9,
        files_changed=2,
        end_time="18:44",
        changed_paths=["src/a.py", "data/input.csv"],
    )

    content = path.read_text()
    assert content.count("Session ended") == 1
    assert "(9m, 2 files)" in content
    assert "(7m, 2 files)" not in content


def test_backtick_info_string_with_backtick_is_not_a_fence(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n```python`not-a-fence\n")
    _finalize(path)

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:44:00-0400",
        duration_minutes=9,
        files_changed=2,
        end_time="18:44",
        changed_paths=["src/a.py", "data/input.csv"],
    )

    content = path.read_text()
    assert content.count("Session ended") == 1
    assert "(9m, 2 files)" in content


def test_authored_bullet_after_generated_list_survives_retry(tmp_path):
    """Codex P2, round 11 on PR #70: consumption is bounded by the count the
    machine heading records, not by line shape."""
    path = tmp_path / "session.md"
    _log(path)
    _finalize(path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("- `follow-up.md`\n")

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:44:00-0400",
        duration_minutes=9,
        files_changed=2,
        end_time="18:44",
        changed_paths=["src/a.py", "data/input.csv"],
    )

    content = path.read_text()
    assert "- `follow-up.md`" in content
    assert content.count("Session ended") == 1
    assert content.count("- `src/a.py`") == 1


def test_authored_lines_after_zero_file_footer_survive_retry(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:42:00-0400",
        duration_minutes=7,
        files_changed=0,
        end_time="18:42",
        changed_paths=[],
    )
    authored = (
        "- Modified: my authored recap line\n"
        "\n### Files Modified\n"
        "- `authored-note.md`\n"
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(authored)

    MODULE.finalize_session_log(
        path,
        ended="2026-08-01T18:44:00-0400",
        duration_minutes=9,
        files_changed=0,
        end_time="18:44",
        changed_paths=[],
    )

    content = path.read_text()
    assert "- Modified: my authored recap line" in content
    assert "- `authored-note.md`" in content
    assert "### Files Modified" in content
    assert content.count("Session ended") == 1
    assert "(9m, 0 files)" in content


def test_retry_collapses_duplicate_legacy_footers(tmp_path):
    path = tmp_path / "session.md"
    _log(path)
    _finalize(path)
    duplicated = path.read_text()
    footer_start = duplicated.index("\n### 18:42 — Session ended")
    path.write_text(duplicated + duplicated[footer_start:])
    assert path.read_text().count("Session ended") == 2

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
