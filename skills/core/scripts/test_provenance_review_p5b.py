"""Regressions for the fifth P1-only Codex pass (PR #154): Digest headers
with escaped quotes, URLs with both userinfo and a secret query, failed
edits, and case aliases in path queries."""

from __future__ import annotations

import hashlib
import json
import shlex
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

SECRET = "sentinel_value_9"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("command", [
    f'curl -H "Authorization: Digest username=\\"my user\\", response=\\"{SECRET}\\"" https://x',
    f"curl 'https://user:pass@example.com/file?api_key={SECRET}'",
    f"wget https://tok@example.com/f.csv?token={SECRET}&page=2",
])
def test_digest_headers_and_combined_url_credentials_are_redacted(command: str) -> None:
    assert SECRET not in redact.redact_command(command)
    assert SECRET not in json.dumps(redact.redact_argv(shlex.split(command)))


def test_redaction_is_stable_when_applied_twice() -> None:
    once = redact.redact_command(f"curl 'https://u:p@h/f?api_key={SECRET}'")
    assert redact.redact_command(once) == once


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
