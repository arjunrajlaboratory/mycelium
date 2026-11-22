"""Codex P1 pass 5a on the provenance PR.

1. A word a secret flag consumed never becomes a seed: in
   ``python fit.py --password --seed=482731`` the option parser takes
   ``--seed=482731`` as the password, and the extractor already masks it in
   ``argv``/``script_args``; ``step.seeds`` is now read from that same
   redacted list, so 482731 appears nowhere in the record.
2. A file-backed shell script (``bash run.sh``, ``./run.sh``) is embedded
   and hashed after the command redaction, not only the quoted-literal one:
   ``export HF_TOKEN=...`` is a shell word. A changed body is stored with
   the digest of the sanitized text and ``script_sha256_of: "sanitized"``,
   including a large script that is not embedded, and manifest
   consolidation applies the same rule to a ``.sh`` event.
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
import extract_data_lineage_event as ev  # noqa: E402
import lineage_redact  # noqa: E402

EXTRACTOR = HERE / "extract_data_lineage_event.py"
SENTINEL = "sentinel_file_token"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        # The whole word was the secret: it appears nowhere in the record.
        assert "482731" not in json.dumps(records)


def test_a_real_seed_is_still_recorded(repo: Path) -> None:
    records = _run(repo, "python fit.py --password hunter2x --seed=482731")
    assert _seeds(records) == [482731]
    assert "hunter2x" not in json.dumps(records)


# -- 2. a shell script body takes the command redaction -------------------------


SHELL_BODY = f"#!/bin/bash\nexport HF_TOKEN={SENTINEL}\npython fit.py\n"


@pytest.mark.parametrize("name,command", [
    ("run.sh", "bash run.sh"),
    ("run.sh", "sh run.sh"),
    ("run.bash", "bash run.bash"),
    ("run.sh", "./run.sh"),
])
def test_an_embedded_shell_script_is_command_redacted(
        repo: Path, name: str, command: str) -> None:
    script = repo / name
    script.write_text(SHELL_BODY)
    script.chmod(0o755)
    records = [r for r in _run(repo, command) if (r.get("script") or "").endswith(name)]
    assert records, "the shell script run was not recorded"
    record = records[0]
    assert SENTINEL not in json.dumps(record)
    assert "HF_TOKEN=" in record["script_source"]
    assert record["script_sha256_of"] == "sanitized"
    assert record["script_sha256"] == sha(record["script_source"])
    assert record["script_sha256"] != hashlib.sha256(script.read_bytes()).hexdigest()


def test_a_large_shell_script_digest_is_of_the_sanitized_text(repo: Path) -> None:
    padding = "# " + "x" * 98 + "\n"
    body = SHELL_BODY + padding * (ev.EMBED_LIMIT_BYTES // len(padding) + 10)
    script = repo / "run.sh"
    script.write_text(body)
    records = [r for r in _run(repo, "bash run.sh") if (r.get("script") or "").endswith("run.sh")]
    assert records
    record = records[0]
    assert record["script_source"] is None
    assert record["script_sha256_of"] == "sanitized"
    assert record["script_sha256"] != sha(body)
    assert record["script_sha256"] == sha(lineage_redact.redact_command(body))


def test_a_clean_shell_script_keeps_its_file_digest(repo: Path) -> None:
    script = repo / "run.sh"
    script.write_text("#!/bin/bash\npython fit.py\n")
    records = [r for r in _run(repo, "bash run.sh") if (r.get("script") or "").endswith("run.sh")]
    assert records
    record = records[0]
    assert record["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()
    assert "script_sha256_of" not in record


def test_shell_detection_by_suffix_and_shebang() -> None:
    assert ev._is_shell_source("job/run.zsh", "echo")
    assert ev._is_shell_source("job.slurm", "#!/usr/bin/env bash\n#SBATCH -n 1\n")
    assert ev._is_shell_source("job.slurm", "#!/bin/sh\n")
    assert not ev._is_shell_source("fit.py", "#!/usr/bin/env python3\n")
    assert not ev._is_shell_source("fit.R", 'Sys.setenv(GITHUB_TOKEN = "x")\n')


def test_a_python_script_is_not_shell_redacted(repo: Path) -> None:
    # Code names secret variables all the time; only a quoted literal is a
    # secret written into a Python script, so its body is left as written.
    (repo / "fit.py").write_text(
        "import os\nHF_TOKEN=os.environ['HF_TOKEN']\nprint(HF_TOKEN)\n")
    records = _run(repo, "python fit.py")
    assert records[0]["script_source"] == (repo / "fit.py").read_text()
    assert "script_sha256_of" not in records[0]


def test_consolidation_shell_redacts_an_embedded_sh_source(tmp_path: Path) -> None:
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "bash run.sh",
           "script": str(tmp_path / "run.sh"), "script_sha256": sha(SHELL_BODY),
           "script_source": SHELL_BODY}
    event = stop.normalize_event(raw, [], 0)
    assert SENTINEL not in json.dumps(event)
    assert event["script_sha256_of"] == "sanitized"
    assert event["script_sha256"] == sha(event["script_source"])


def test_consolidation_shell_redacts_a_shebang_job_file(tmp_path: Path) -> None:
    body = f"#!/usr/bin/env bash\n#SBATCH -n 1\nexport HF_TOKEN={SENTINEL}\n"
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "sbatch job.slurm",
           "script": str(tmp_path / "job.slurm"), "script_sha256": sha(body),
           "script_source": body}
    event = stop.normalize_event(raw, [], 0)
    assert SENTINEL not in json.dumps(event)
    assert event["script_sha256_of"] == "sanitized"
    py = "#!/usr/bin/env python3\nHF_TOKEN=os.environ['X']\n"
    event = stop.normalize_event({**raw, "script": str(tmp_path / "fit.py"),
                                  "script_source": py, "script_sha256": sha(py)}, [], 0)
    assert event["script_source"] == py


def test_consolidation_shell_redacts_a_recomputed_sh_digest(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text(SHELL_BODY)
    raw = {"ts": "2026-11-01T00:00:00Z", "bash_cmd": "bash run.sh",
           "script": str(script), "script_sha256": None}
    event = stop.normalize_event(raw, [], 0)
    assert event["script_sha256_of"] == "sanitized"
    assert event["script_sha256"] != hashlib.sha256(script.read_bytes()).hexdigest()
    clean = tmp_path / "clean.sh"
    clean.write_text("#!/bin/bash\npython fit.py\n")
    event = stop.normalize_event({**raw, "script": str(clean)}, [], 0)
    assert event["script_sha256"] == hashlib.sha256(clean.read_bytes()).hexdigest()
    assert "script_sha256_of" not in event
