"""Regressions for the fifth P1-only Codex pass (PR #154): a stable allowlist,
failed edits, and case aliases in path queries."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import io_observer as io  # noqa: E402
import lineage_index as li  # noqa: E402
import lineage_redact as redact  # noqa: E402
import test_lineage_index as tli  # noqa: E402
import test_provenance_review_r2_index as r2  # noqa: E402


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("command", [
    "python3 fit.py  # a note",
    "python3 - <<'EOF'\nimport os\nprint(os.environ)\nEOF\necho done",
    "API_TOKEN=$(printf %s x) python3 fit.py && snakemake -C db.password=y",
])
def test_the_allowlist_is_stable_when_applied_twice(command: str) -> None:
    # The observer stores allowlisted text, and Stop applies it again.
    once = redact.commit_safe_command(command, set())
    assert redact.commit_safe_command(once, set()) == once


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".living" / "log" / "data-lineage").mkdir(parents=True)
    (root / ".mycelium").mkdir()
    return root


def test_a_failed_edit_makes_its_target_uncertain(project: Path) -> None:
    (project / "notes.md").write_text("A")
    first = r2.journal(project, "c1")
    tli.write_store(project, [r2.observed(project, "c1", first,
                                          created=[("notes.md", sha("A"))])])
    payload = {"session_id": "s1", "tool_use_id": "w1", "tool_name": "Write",
               "hook_event_name": "PreToolUse", "cwd": str(project),
               "tool_input": {"file_path": str(project / "notes.md"), "content": "B"}}
    io.hook(project / ".mycelium", project, "claude", json.dumps(payload))
    (project / "notes.md").write_text("B?")  # a partial write the tool reported failed
    payload["hook_event_name"] = "PostToolUseFailure"
    io.hook(project / ".mycelium", project, "claude", json.dumps(payload))
    result = li.query_path(li.load(project), "notes.md", disk=False)
    assert all(w["record"]["status"] != "current-in-record" for w in result["writes"])
    assert all(w["call"] != "s1|claude:w1" for w in result["writes"])


def test_a_query_in_another_case_finds_the_history(project: Path) -> None:
    (project / "README.md").write_text("A")
    if not io._case_insensitive(str(project.resolve())):
        pytest.skip("case-sensitive volume")
    first = r2.journal(project, "c1")
    tli.write_store(project, [r2.observed(project, "c1", first,
                                          created=[("README.md", sha("A"))])])
    result = li.query_path(li.load(project), "readme.md")
    assert result["writes"] and result["writes"][0]["status"] == "current"
    assert result["selector"] == {"path": "readme.md"}
