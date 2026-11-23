"""Codex P1 pass 5a on the provenance PR.

1. A word a secret flag consumed never becomes a seed: in
   ``python fit.py --password --seed=482731`` the option parser takes
   ``--seed=482731`` as the password, so ``step.seeds`` is read from a list
   where that word is masked, and the committed record shows it nowhere.
2. A shell script's body (``export HF_TOKEN=...``) never reaches a
   committed record: its source is not committed, and its digest is the
   plain digest of the file.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"
SENTINEL = "sentinel_file_token"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "fit.py").write_text(
        "import pandas as pd\npd.read_csv('in.csv').to_csv('out.csv')\n")
    return root


def _run(repo: Path, command: str) -> list[dict]:
    result = subprocess.run(
        [sys.executable, str(EXTRACTOR), "--cwd", str(repo),
         "--ts", "2026-11-22T10:00:00Z", "--bash-cmd", command, "--bash-exit", "0",
         "--repo-root", str(repo)],
        capture_output=True, text=True, check=True)
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def _seeds(records: list[dict]) -> list[int]:
    return [seed["value"] for record in records
            for seed in (record.get("step") or {}).get("seeds") or []]


def _committed(records: list[dict]) -> str:
    return json.dumps([stop.commit_event(r) for r in records])


# -- 1. a consumed secret value is never a seed --------------------------------


@pytest.mark.parametrize("command", [
    "python fit.py --password --seed=482731",
    "python fit.py --api-key --seed 482731",
    "python fit.py --token --random-state=482731",
])
def test_a_word_consumed_as_a_secret_is_not_a_seed(repo: Path, command: str) -> None:
    records = _run(repo, command)
    assert records, "the run was not recorded"
    assert 482731 not in _seeds(records)
    if "=482731" in command:
        # The whole word was the secret: the committed record shows none of it.
        assert "482731" not in _committed(records)


def test_a_real_seed_is_still_recorded(repo: Path) -> None:
    records = _run(repo, "python fit.py --password hunter2x --seed=482731")
    assert _seeds(records) == [482731]
    assert "hunter2x" not in _committed(records)


# -- 2. a shell script body is never committed ----------------------------------


@pytest.mark.parametrize("name,command", [
    ("run.sh", "bash run.sh"),
    ("run.bash", "bash run.bash"),
    ("run.sh", "./run.sh"),
])
def test_a_shell_script_body_is_never_committed(repo: Path, name: str, command: str) -> None:
    script = repo / name
    script.write_text(f"#!/bin/bash\nexport HF_TOKEN={SENTINEL}\npython fit.py\n")
    script.chmod(0o755)
    records = [r for r in _run(repo, command) if (r.get("script") or "").endswith(name)]
    assert records, "the shell script run was not recorded"
    assert records[0]["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    assert "script_sha256_of" not in records[0]
    assert SENTINEL not in _committed(records)
