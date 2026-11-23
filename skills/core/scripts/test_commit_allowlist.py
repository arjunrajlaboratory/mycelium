"""The commit allowlist (text_policy allowlist-v1).

A committed manifest keeps only words known to be safe; the denylist in
lineage_redact can never be complete. These tests replay the secret corpus
of every earlier review round (the parametrized commands of
test_provenance_review_*.py) through a full consolidation and assert that
no sentinel reaches the manifest, and check that the allowlist still leaves
a readable command.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import extract_data_lineage as stop  # noqa: E402
import lineage_redact as lr  # noqa: E402

V = lr.COMMIT_VALUE
RANDOM_TOKEN = "9f8a7b6c5d4e3f2a1b0c"
_SENTINEL_NAMES = ("SECRET", "TOKEN", "SENTINEL")


# -- the corpus ----------------------------------------------------------------


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (tuple, list)):
        return [s for item in value for s in _strings(item)]
    return []


def _corpus() -> list[tuple[str, str, str]]:
    """(case id, text, sentinel) for every parametrized string of the review
    tests that holds that module's secret sentinel, or that a `(command,
    secret)` pair names as its secret."""
    cases: list[tuple[str, str, str]] = []
    for path in sorted(HERE.glob("test_provenance_review_*.py")):
        module = importlib.import_module(path.stem)
        sentinels = [getattr(module, n) for n in _SENTINEL_NAMES
                     if isinstance(getattr(module, n, None), str)]
        for name, func in sorted(vars(module).items()):
            for mark in getattr(func, "pytestmark", []) or []:
                if mark.name != "parametrize":
                    continue
                for index, param in enumerate(mark.args[1]):
                    values = getattr(param, "values", param)
                    strings = _strings(values)
                    found = [(text, s) for text in strings for s in sentinels if s in text]
                    if isinstance(values, tuple) and len(strings) == 2 and \
                            strings[1] and strings[1] in strings[0] and strings[1] != strings[0]:
                        found.append((strings[0], strings[1]))
                    for text, sentinel in found:
                        cases.append((f"{path.stem}.{name}[{index}]", text, sentinel))
    # Rounds that inline their sentinels rather than naming them.
    cases += [
        ("p1a.url-io", "cp https://u:SEC3@h/a.csv data/b.csv", "SEC3"),
        ("p1b.dash-led", "python fit.py --password -123456 x", "123456"),
        ("random-token", f"tool -k {RANDOM_TOKEN}", RANDOM_TOKEN),
        ("random-token-eq", f"tool --key-id={RANDOM_TOKEN} data/in.csv", RANDOM_TOKEN),
        ("random-token-env", f"X_ID={RANDOM_TOKEN} python fit.py", RANDOM_TOKEN),
        ("glued-short", "mysql -pS3cr3tValue db", "S3cr3tValue"),
        ("heredoc", f"cat <<EOF > cfg.txt\nkey: {RANDOM_TOKEN}\nEOF", RANDOM_TOKEN),
        ("pin", "tool --pin 482913", "482913"),
    ]
    return cases


CORPUS = _corpus()


def test_the_corpus_was_collected() -> None:
    # Every earlier round contributed; a refactor of the review tests that
    # hides their parameters would silently empty this file's main test.
    rounds = {case.split(".")[0] for case, _, _ in CORPUS}
    assert len(CORPUS) >= 30
    assert len([r for r in rounds if r.startswith("test_provenance_review_")]) >= 5


def _write_session(root: Path, text: str) -> Path:
    """One session whose every free-text field carries ``text``."""
    words = text.split()
    event = {
        "ts": "2026-11-22T10:00:00Z", "event_id": "e1", "host": "claude",
        "host_session_id": "s1", "repo_root": str(root),
        "bash_cmd": text, "bash_exit": 0, "script": None, "script_sha256": None,
        "script_source": text, "script_candidate": text,
        "inputs": [{"path": str(root / "data" / "in.csv"), "rel_path": "data/in.csv",
                    "sha256": None, "hash_status": "pending"}],
        "outputs": [],
        "lineage_warnings": [f"R ran a script; the command names {text}.",
                             f"Remote URLs were not recorded as local files: {text}."],
        "filters_detected": [text],
        "observation_id": "claude:c1", "observation_session": "s1",
        "observation_status": "no-known-overlap",
        "step": {"argv": words, "executable": words[0] if words else text,
                 "interpreter": "python", "script_args": words, "params": words,
                 "wrappers": words, "env": {"OPT": text, "OPT_X": {"text": text,
                                                                     "expanded": False}},
                 "config_files": [{"path": text, "via": "--config", "sha256": None,
                                   "hash_status": "unresolved"},
                                  {"path": text, "via": "--config", "sha256": None,
                                   "hash_status": "remote"}]},
    }
    data_command = {
        "ts": "2026-11-22T10:00:01Z", "kind": "data_command", "event_id": "d1",
        "bash_cmd": text, "cwd": str(root), "signals": ["data-path"],
        "named_paths": words, "analysis_candidates": words,
        "inputs": [], "outputs": [], "step": {"argv": words, "executable": text},
    }
    skip = {"ts": "2026-11-22T10:00:02Z", "kind": "skip", "skip_id": "k1",
            "skip_reason": "extractor-failed", "detail": text, "bash_cmd": text}
    events = root / ".mycelium" / "events.ndjson"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("".join(json.dumps(r) + "\n" for r in (event, data_command, skip)))
    store = root / ".mycelium" / "io" / "obs" / "claude" / "s1.ndjson"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps({
        "kind": "observation", "observation_id": "claude:c1", "host": "claude",
        "host_session_id": "s1", "bash_cmd": text, "status": "no-known-overlap",
        "repo_root": str(root), "changes": {"created": [], "modified": [], "deleted": []},
    }) + "\n")
    return events


def _as_command(text: str) -> str:
    """A corpus fragment as an argument of a command. The command word of a
    simple command is kept by design (a secret is never a program name), and
    several corpus entries are bare values (`-xSECRET`) or a script body."""
    return f"tool {text}"


def _manifest_text(root: Path, text: str) -> str:
    events = _write_session(root, text)
    manifest = stop.extract(events, "sess-1", str(root))
    out = root / ".living" / "log" / "data-lineage" / "sess-1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    stop.write_manifest_atomic(out, manifest)
    return out.read_text()


@pytest.mark.parametrize(("case", "text", "sentinel"), CORPUS, ids=[c[0] for c in CORPUS])
def test_no_corpus_secret_reaches_a_written_manifest(
        tmp_path: Path, case: str, text: str, sentinel: str) -> None:
    root = tmp_path / "repo"
    written = _manifest_text(root, _as_command(text))
    # The test's own directory name is not command text (`...secret_reaches0`).
    assert sentinel not in written.replace(str(root), "<root>")
    assert json.loads(written)["text_policy"] == "allowlist-v1"


@pytest.mark.parametrize(("case", "text", "sentinel"), CORPUS, ids=[c[0] for c in CORPUS])
def test_the_allowlist_alone_drops_every_corpus_secret(case: str, text: str,
                                                      sentinel: str) -> None:
    # No denylist pass first: the allowlist must not depend on it.
    assert sentinel not in lr.commit_safe_command(_as_command(text), set())
    assert sentinel not in json.dumps(lr.commit_safe_argv(_as_command(text).split(), set()))
    assert sentinel not in json.dumps(lr.commit_safe_argv(text.split(), set(), False))
    assert sentinel not in lr.commit_safe_prose(f"the command names {text}.", set(),
                                                ((text, V),))


# -- readability ---------------------------------------------------------------


def _event(command: str, root: str = "/r", **extra) -> dict:
    return {
        "ts": "t", "bash_cmd": command, "script": f"{root}/analysis/de/run.py",
        "inputs": [], "outputs": [],
        "step": {"argv": command.split(), "config_files": [
            {"path": f"{root}/analysis/de/params.yaml", "rel_path": "analysis/de/params.yaml",
             "via": "--config", "sha256": None, "hash_status": "pending"}]},
        **extra,
    }


def test_a_command_naming_only_its_own_paths_is_kept_whole() -> None:
    command = "python3 analysis/de/run.py --config analysis/de/params.yaml --seed 3"
    out = stop.commit_event(_event(command))
    assert out["bash_cmd"] == command
    assert out["step"]["argv"] == command.split()


def test_unknown_words_become_placeholders() -> None:
    out = stop.commit_event(_event(
        "python3 analysis/de/run.py --label 'treated cells' --url https://h/x notes.txt"))
    assert out["bash_cmd"] == f"python3 analysis/de/run.py --label {V} --url {V} {V}"


def test_a_heredoc_body_collapses() -> None:
    command = "python3 - <<'EOF'\nimport os\nprint(os.environ)\nEOF\necho done"
    assert lr.commit_safe_command(command, set()) == \
        f"python3 - <<'EOF'\n<heredoc: 2 lines>\nEOF\necho {V}"


def test_a_number_after_a_secret_option_is_dropped() -> None:
    assert lr.commit_safe_command("tool --password 123456 --n 5", set()) == \
        f"tool --password {V} --n 5"
    assert lr.commit_safe_argv(["tool", "--api-key=42", "--n=5"], set()) == \
        ["tool", f"--api-key={V}", "--n=5"]
    assert lr.commit_safe_command("DB_PASSWORD=1234 python3 x", set()) == \
        f"DB_PASSWORD={V} python3 {V}"


def test_a_random_token_with_an_innocent_name_is_dropped() -> None:
    # Exactly what the denylist could never catch.
    assert lr.redact_command(f"tool -k {RANDOM_TOKEN}") == f"tool -k {RANDOM_TOKEN}"
    assert lr.commit_safe_command(f"tool -k {RANDOM_TOKEN}", set()) == f"tool -k {V}"


def test_operators_layout_and_assignment_names_stay() -> None:
    command = "cd analysis && OMP_NUM_THREADS=4 python3 de/run.py 2>&1 | tee x.log; echo $?"
    keep = lr.commit_keep_paths(["/r/analysis/de/run.py"])
    assert lr.commit_safe_command(command, keep) == \
        f"cd analysis && OMP_NUM_THREADS=4 python3 de/run.py 2>&1 | tee {V}; echo {V}"


def test_script_source_is_not_committed_but_its_digest_is() -> None:
    event = _event("python3 -c x", script=None, script_sha256="a" * 64,
                   script_source="import os\nprint(1)\n")
    out = stop.commit_event(event)
    assert "script_source" not in out
    assert out["script_sha256"] == "a" * 64
    assert out["script_source_lines"] == 2
    assert out["script_source_bytes"] == len("import os\nprint(1)\n")


def test_env_names_stay_and_values_follow_the_value_rule() -> None:
    event = _event("x", step={"env": {"OMP_NUM_THREADS": "4", "MODE": "fast",
                                      "HF_TOKEN": {"redacted": True},
                                      "DIR": {"text": "$HOME/x", "expanded": False}}})
    env = stop.commit_event(event)["step"]["env"]
    assert env == {"OMP_NUM_THREADS": "4", "MODE": V, "HF_TOKEN": {"redacted": True},
                   "DIR": {"text": V, "expanded": False}}


def test_warnings_keep_their_prose_and_lose_their_variable_parts() -> None:
    event = _event("R -f $SCRIPT", script=None, script_candidate="$SCRIPT",
                   lineage_warnings=["R ran a script, but shell syntax may have changed "
                                     "which one; the command names $SCRIPT."])
    out = stop.commit_event(event)
    assert out["script_candidate"] == V
    assert out["lineage_warnings"] == [
        f"R ran a script, but shell syntax may have changed which one; the command names {V}."]
    plain = stop.commit_event(_event("R -f a.R", script=None, script_candidate="a.R"))
    assert plain["script_candidate"] == "a.R" and plain["bash_cmd"] == "R -f a.R"


def test_a_written_manifest_validates_and_declares_its_policy(tmp_path: Path) -> None:
    jsonschema = pytest.importorskip("jsonschema") \
        if os.environ.get("MYCELIUM_REQUIRE_JSONSCHEMA") != "1" else importlib.import_module(
            "jsonschema")
    root = tmp_path / "repo"
    manifest = json.loads(_manifest_text(root, f"python3 fit.py --seed 3 -k {RANDOM_TOKEN}"))
    schema = json.loads((HERE.parent / "references" / "provenance-schema.json").read_text())
    validator = jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/manifest_v2", "$defs": schema["$defs"]})
    assert [e.message for e in validator.iter_errors(manifest)] == []
    assert manifest["text_policy"] == "allowlist-v1"
    assert all("script_source" not in a for a in manifest["actions"])
    assert "filters_detected" not in manifest["actions"][0]


def test_a_manifest_without_a_policy_still_validates() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((HERE.parent / "references" / "provenance-schema.json").read_text())
    validator = jsonschema.Draft202012Validator(
        {"$ref": "#/$defs/manifest", "$defs": schema["$defs"]})
    legacy = {"session_id": "s", "actions": [{"ts": "t", "inputs": [], "outputs": [],
                                              "bash_cmd": "python a.py",
                                              "script_source": "print(1)"}],
              "summary": {}}
    assert list(validator.iter_errors(legacy)) == []
