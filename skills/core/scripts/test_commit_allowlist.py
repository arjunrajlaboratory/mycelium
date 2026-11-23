"""The commit allowlist (text_policy allowlist-v1).

A committed manifest keeps only words known to be safe; no list of secret
shapes is ever complete. These tests replay the secret corpus of every
earlier review round through a full consolidation and assert that no
sentinel reaches the manifest, and check that the allowlist still leaves a
readable command.
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
HEX = "a" * 64  # a digest: file evidence for a record path


# -- the corpus ----------------------------------------------------------------
#
# (case id, text, sentinel): the secret corpus of every earlier review round
# (the denylist-era cases of test_provenance_review_*.py), and a few that no
# denylist could catch.

CORPUS = [
    ('p1b.test_a_secret_value_led_by_a_dash_is_redacted[1]',
     '-xTEST_SECRET_123', 'TEST_SECRET_123'),
    ('p1b.test_url_tokens_and_secret_query_values_are_redacted[0]',
     'git clone https://ghp_TEST_SECRET_123@github.com/o/r.git', 'TEST_SECRET_123'),
    ('p1b.test_url_tokens_and_secret_query_values_are_redacted[1]',
     "wget 'https://h/c.yaml?key=TEST_SECRET_123&format=csv'", 'TEST_SECRET_123'),
    ('p1b.test_url_tokens_and_secret_query_values_are_redacted[2]',
     "aws s3 cp 's3://b/k?X-Amz-Signature=TEST_SECRET_123&X-Amz-Date=1' .", 'TEST_SECRET_123'),
    ('p2b.test_annotated_secret_literals_are_redacted[0]',
     'API_TOKEN: str = "FAKE_TOKEN_42"\n', 'FAKE_TOKEN_42'),
    ('p2b.test_annotated_secret_literals_are_redacted[1]',
     'secret: Final[str] = "FAKE_TOKEN_42"\n', 'FAKE_TOKEN_42'),
    ('p2b.test_annotated_secret_literals_are_redacted[2]',
     'password: "str | None" = "FAKE_TOKEN_42"\n', 'FAKE_TOKEN_42'),
    ('p2b.test_the_whole_url_userinfo_is_redacted[0]',
     'python fit.py --config https://FAKE_TOKEN_42:x-oauth-basic@host.invalid/settings.yaml', 'FAKE_TOKEN_42'),
    ('p2b.test_the_whole_url_userinfo_is_redacted[1]',
     'git clone https://user:FAKE_TOKEN_42@host/x.git', 'FAKE_TOKEN_42'),
    ('p2b.test_the_whole_url_userinfo_is_redacted[2]',
     'git clone https://FAKE_TOKEN_42@host/x.git', 'FAKE_TOKEN_42'),
    ('p3b.test_parenthesized_and_concatenated_literals_are_redacted[0]',
     'password = ("abcMY_FAKE_SECRET"\n    "def")\n', 'MY_FAKE_SECRET'),
    ('p3b.test_parenthesized_and_concatenated_literals_are_redacted[1]',
     'token = "a" "MY_FAKE_SECRET"\n', 'MY_FAKE_SECRET'),
    ('p3b.test_substitutions_and_nested_keys_are_redacted[0]',
     "API_TOKEN=$(printf %s MY_FAKE_SECRET) python3 -c 'print(1)'", 'MY_FAKE_SECRET'),
    ('p3b.test_substitutions_and_nested_keys_are_redacted[1]',
     'API_TOKEN=`printf %s MY_FAKE_SECRET` python3 x.py', 'MY_FAKE_SECRET'),
    ('p3b.test_substitutions_and_nested_keys_are_redacted[2]',
     'snakemake --config=api_token=MY_FAKE_SECRET', 'MY_FAKE_SECRET'),
    ('p3b.test_substitutions_and_nested_keys_are_redacted[3]',
     'snakemake -C db.password=MY_FAKE_SECRET', 'MY_FAKE_SECRET'),
    ('p3b.test_substitutions_and_nested_keys_are_redacted[4]',
     'tool --opts=a=1,token=MY_FAKE_SECRET', 'MY_FAKE_SECRET'),
    ('p4b.test_secrets_in_heredocs_substitutions_and_option_shapes_are_redacted[0]',
     "curl -H @- https://example.invalid/c.csv <<'EOF'\nAuthorization: Bearer synthetic-secret\nEOF", 'synthetic-secret'),
    ('p4b.test_secrets_in_heredocs_substitutions_and_option_shapes_are_redacted[1]',
     'trait=$(python fetch.py --token synthetic-secret); python fit.py "$trait"', 'synthetic-secret'),
    ('p4b.test_secrets_in_heredocs_substitutions_and_option_shapes_are_redacted[2]',
     'x=`fetch --password synthetic-secret` && run', 'synthetic-secret'),
    ('p4b.test_secrets_in_heredocs_substitutions_and_option_shapes_are_redacted[3]',
     'echo "$(fetch --api-key synthetic-secret)"', 'synthetic-secret'),
    ('p4b.test_secrets_in_heredocs_substitutions_and_option_shapes_are_redacted[4]',
     'y=$(fetch --token synthetic-secret', 'synthetic-secret'),
    ('p4b.test_secrets_in_heredocs_substitutions_and_option_shapes_are_redacted[5]',
     'python app.py --password --ab9_synthetic-secret', 'synthetic-secret'),
    ('p5b.test_digest_headers_and_combined_url_credentials_are_redacted[0]',
     'curl -H "Authorization: Digest username=\\"my user\\", response=\\"sentinel_value_9\\"" https://x', 'sentinel_value_9'),
    ('p5b.test_digest_headers_and_combined_url_credentials_are_redacted[1]',
     "curl 'https://user:pass@example.com/file?api_key=sentinel_value_9'", 'sentinel_value_9'),
    ('p5b.test_digest_headers_and_combined_url_credentials_are_redacted[2]',
     'wget https://tok@example.com/f.csv?token=sentinel_value_9&page=2', 'sentinel_value_9'),
    ('p6.test_attached_heredoc_and_nested_command_credentials_are_redacted[0]',
     'curl --header=\'Authorization: Digest username="alice", response="FIXTURE_VALUE_7"\' https://x/f.csv', 'FIXTURE_VALUE_7'),
    ('p6.test_attached_heredoc_and_nested_command_credentials_are_redacted[1]',
     'curl -H\'Authorization: Digest username="alice", response="FIXTURE_VALUE_7"\' https://x/f.csv', 'FIXTURE_VALUE_7'),
    ('p6.test_attached_heredoc_and_nested_command_credentials_are_redacted[2]',
     'curl --header @- https://x/f.csv <<\'EOF\'\nAuthorization: Digest username="alice", response="FIXTURE_VALUE_7"\nEOF', 'FIXTURE_VALUE_7'),
    ('p6.test_attached_heredoc_and_nested_command_credentials_are_redacted[3]',
     "bash -c 'curl --oauth2-bearer FIXTURE_VALUE_7 https://example.test/file.csv'", 'FIXTURE_VALUE_7'),
    ('p6.test_attached_heredoc_and_nested_command_credentials_are_redacted[4]',
     "ssh host 'export API_TOKEN=FIXTURE_VALUE_7; run'", 'FIXTURE_VALUE_7'),
    ('r1.test_whole_shell_words_are_redacted[0]',
     'tool --token pre"post secret" next', 'secret'),
    ('r1.test_whole_shell_words_are_redacted[1]',
     'TOKEN=abc\\ def run', 'def'),
    ('r1.test_whole_shell_words_are_redacted[2]',
     'SESSION_ID=s3ssi0n run', 's3ssi0n'),
    ('r1.test_whole_shell_words_are_redacted[3]',
     "tool --api-key='a b c' x", 'a b c'),
    ('r2_index.test_commands_are_redacted_after_shell_decoding[0]',
     "python fit.py '--token' s3cret-value", 's3cret-value'),
    ('r2_index.test_commands_are_redacted_after_shell_decoding[1]',
     'python fit.py "--api-key=s3cret-value" data/in.csv', 's3cret-value'),
    ('r2_index.test_commands_are_redacted_after_shell_decoding[2]',
     'python -c "password = \\"s3cret-value\\"; print(1)"', 's3cret-value'),
    ('r2_index.test_commands_are_redacted_after_shell_decoding[3]',
     'curl -H \'Authorization: Digest username="u", response="s3cret-value"\' https://x', 's3cret-value'),
    ('r2_index.test_commands_are_redacted_after_shell_decoding[4]',
     'curl -H "Authorization: Bearer s3cret-value" https://x/data.csv', 's3cret-value'),
    ('r2_index.test_quoted_secret_keys_in_source_are_redacted[0]',
     'h = {"Authorization": "Bearer s3cret-value"}\n', 's3cret-value'),
    ('r2_index.test_quoted_secret_keys_in_source_are_redacted[1]',
     'cfg = {"api_key": "s3cret-value"}\n', 's3cret-value'),
    ('r2_index.test_quoted_secret_keys_in_source_are_redacted[2]',
     'headers["Authorization"] = "Digest a=1, response=s3cret-value"\n', 's3cret-value'),
    ('p1a.url-io',
     'cp https://u:SEC3@h/a.csv data/b.csv', 'SEC3'),
    ('p1b.dash-led',
     'python fit.py --password -123456 x', '123456'),
    ('random-token',
     'tool -k 9f8a7b6c5d4e3f2a1b0c', '9f8a7b6c5d4e3f2a1b0c'),
    ('random-token-eq',
     'tool --key-id=9f8a7b6c5d4e3f2a1b0c data/in.csv', '9f8a7b6c5d4e3f2a1b0c'),
    ('random-token-env',
     'X_ID=9f8a7b6c5d4e3f2a1b0c python fit.py', '9f8a7b6c5d4e3f2a1b0c'),
    ('glued-short',
     'mysql -pS3cr3tValue db', 'S3cr3tValue'),
    ('heredoc',
     'cat <<EOF > cfg.txt\nkey: 9f8a7b6c5d4e3f2a1b0c\nEOF', '9f8a7b6c5d4e3f2a1b0c'),
    ('pin',
     'tool --pin 482913', '482913'),
    ("p1a.url-io", "cp https://u:SEC3@h/a.csv data/b.csv", "SEC3"),
    ("p1b.dash-led", "python fit.py --password -123456 x", "123456"),
    ("random-token", f"tool -k {RANDOM_TOKEN}", RANDOM_TOKEN),
    ("random-token-eq", f"tool --key-id={RANDOM_TOKEN} data/in.csv", RANDOM_TOKEN),
    ("random-token-env", f"X_ID={RANDOM_TOKEN} python fit.py", RANDOM_TOKEN),
    ("glued-short", "mysql -pS3cr3tValue db", "S3cr3tValue"),
    ("heredoc", f"cat <<EOF > cfg.txt\nkey: {RANDOM_TOKEN}\nEOF", RANDOM_TOKEN),
    ("pin", "tool --pin 482913", "482913"),
    ("p1b.continued", "python fit.py --api-key \\\n  'TEST_SECRET_123' --input data/in.csv",
     "TEST_SECRET_123"),
]


def _write_session(root: Path, text: str, source: str | None = None) -> Path:
    """One session whose every free-text field carries ``text``."""
    words = text.split()
    event = {
        "ts": "2026-11-22T10:00:00Z", "event_id": "e1", "host": "claude",
        "host_session_id": "s1", "repo_root": str(root),
        "bash_cmd": text, "bash_exit": 0, "script": None, "script_sha256": None,
        "script_source": text if source is None else source, "script_candidate": text,
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
    """A corpus fragment as an argument of a vocabulary command: several
    corpus entries are bare values (`-xSECRET`) or a script body."""
    return f"cat {text}"


def _manifest_text(root: Path, text: str, source: str | None = None) -> str:
    events = _write_session(root, text, source)
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
        "script_sha256": HEX, "inputs": [], "outputs": [],
        "step": {"argv": command.split(), "config_files": [
            {"path": f"{root}/analysis/de/params.yaml", "rel_path": "analysis/de/params.yaml",
             "via": "--config", "sha256": HEX, "hash_status": "hashed"}]},
        **extra,
    }


def test_a_command_naming_only_its_own_paths_is_kept_whole() -> None:
    command = "python3 analysis/de/run.py --config analysis/de/params.yaml --seed 3"
    out = stop.commit_event(_event(command))
    assert out["bash_cmd"] == command
    assert out["step"]["argv"] == command.split()


def test_unknown_words_become_placeholders() -> None:
    command = "python3 analysis/de/run.py --label 'treated cells' --url https://h/x notes.txt"
    out = stop.commit_event(_event(command))
    # `--label` and `--url` are no common options, and no source names them.
    assert out["bash_cmd"] == f"python3 analysis/de/run.py {V} {V} {V} {V} {V}"
    sourced = stop.commit_event(_event(
        command, script_source="p.add_argument('--label')\np.add_argument('--url')\n"))
    assert sourced["bash_cmd"] == f"python3 analysis/de/run.py --label {V} --url {V} {V}"


def test_a_heredoc_body_collapses() -> None:
    command = "python3 - <<'EOF'\nimport os\nprint(os.environ)\nEOF\necho done"
    assert lr.commit_safe_command(command, set()) == \
        f"python3 - <<'EOF'\n<heredoc: 2 lines>\nEOF\necho {V}"


def test_a_number_after_a_secret_option_is_dropped() -> None:
    assert lr.commit_safe_command("curl --password 123456 --retry 5", set()) == \
        f"curl {V} {V} --retry 5"
    # Even a name the source spells keeps its secret value masked.
    assert lr.commit_safe_argv(["--api-key=42", "--threads=5"], set(), False,
                               {"--api-key"}) == [f"--api-key={V}", "--threads=5"]
    assert lr.commit_safe_argv(["--api-key", "42"], set(), False, {"--api-key"}) == \
        ["--api-key", V]
    assert lr.commit_safe_command("DB_PASSWORD=1234 python3 x", set()) == \
        f"DB_PASSWORD={V} python3 {V}"


def test_a_random_token_with_an_innocent_name_is_dropped() -> None:
    assert lr.commit_safe_command(f"curl -k {RANDOM_TOKEN}", set()) == f"curl -k {V}"


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
    # A candidate is never read, so it is no evidence: shaped like a script
    # or not, it is kept only when it is exactly an evidence-backed path.
    plain = stop.commit_event(_event("R -f a.R", script=None, script_candidate="a.R"))
    assert plain["script_candidate"] == V and plain["bash_cmd"] == f"R -f {V}"
    own = stop.commit_event(_event("R -f analysis/de/params.yaml", script=None,
                                   script_candidate="analysis/de/params.yaml"))
    assert own["script_candidate"] == "analysis/de/params.yaml"


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


# -- evidence, not shape (allowlist hardening) -----------------------------------
#
# A word is kept only on evidence: it is a fixed vocabulary word in a role
# the scanner can prove (a command name, a shell keyword, a subcommand), an
# option or name of the fixed option/name shape, a short number, or exactly
# one of the record's own evidence-backed paths. Shape and position alone
# never prove a word safe.


def test_a_missing_config_operand_is_not_a_record_path() -> None:
    # The config was never found, so its "path" is only the operand as typed.
    secret = "v61cSk8Rl-test"
    command = f"python3 -c 'print(0)' --config 'client:{secret}'"
    event = {"ts": "t", "bash_cmd": command, "script": None, "script_sha256": HEX,
             "inputs": [], "outputs": [],
             "step": {"argv": ["python3", "-c", "print(0)", "--config", f"client:{secret}"],
                      "config_files": [{"path": f"/r/client:{secret}",
                                        "rel_path": f"client:{secret}", "via": "--config",
                                        "sha256": None, "hash_status": "missing"}]}}
    out = stop.commit_event(event)
    assert secret not in json.dumps(out)
    assert out["bash_cmd"] == f"python3 -c {V} --config {V}"
    (config,) = out["step"]["config_files"]
    assert config["path"] == V and config["rel_path"] == V
    assert config["hash_status"] == "missing"


def test_a_hashed_config_keeps_its_path_and_its_word() -> None:
    command = "python3 -c 'print(0)' --config cfg/p.yaml"
    event = {"ts": "t", "bash_cmd": command, "script": None, "inputs": [], "outputs": [],
             "step": {"config_files": [{"path": "/r/cfg/p.yaml", "rel_path": "cfg/p.yaml",
                                        "via": "--config", "sha256": HEX,
                                        "hash_status": "hashed"}]}}
    out = stop.commit_event(event)
    assert out["bash_cmd"] == f"python3 -c {V} --config cfg/p.yaml"
    assert out["step"]["config_files"][0]["path"] == "/r/cfg/p.yaml"


def test_an_unhashed_input_lends_no_word_to_the_command() -> None:
    event = {"ts": "t", "bash_cmd": "python3 fit.py data/qR4LoP16xZ.csv", "script": None,
             "inputs": [{"path": "/r/data/qR4LoP16xZ.csv", "rel_path": "data/qR4LoP16xZ.csv",
                         "sha256": None, "hash_status": "pending"}], "outputs": []}
    assert "qR4LoP16xZ" not in stop.commit_event(event)["bash_cmd"]
    observed = {**event, "inputs": [{**event["inputs"][0], "observed_change": "modified"}]}
    assert stop.commit_event(observed)["bash_cmd"].endswith("data/qR4LoP16xZ.csv")


def test_a_respelled_path_is_published_only_as_approved() -> None:
    keep = lr.commit_keep_paths(["/r/run.py"])
    command = "python3 run.py qR4LoP16xZ/../run.py"
    assert lr.commit_safe_command(command, keep) == f"python3 run.py {V}"
    assert lr.commit_safe_argv(command.split(), keep) == ["python3", "run.py", V]
    assert "qR4LoP16xZ" not in lr.commit_safe_prose(f"the command names {command}.", keep)
    # A single leading `./` is the only respelling, and only when the rest
    # matches exactly.
    assert lr.commit_safe_command("python3 ./run.py", keep) == "python3 ./run.py"
    assert lr.commit_safe_command("python3 .//run.py", keep) == f"python3 {V}"
    assert lr.commit_safe_command("python3 run.py/.", keep) == f"python3 {V}"
    event = {"ts": "t", "bash_cmd": command, "script": "/r/run.py", "script_sha256": HEX,
             "inputs": [], "outputs": []}
    assert "qR4LoP16xZ" not in json.dumps(stop.commit_event(event))


def test_a_recorded_path_with_dot_segments_lends_none_of_them() -> None:
    keep = lr.commit_keep_paths(["/r/a/../b.csv"])
    assert ".." not in keep and "." not in keep
    assert "/r/a/../b.csv" in keep and "b.csv" in keep and "a/../b.csv" not in keep


def test_an_unquoted_comment_is_one_placeholder() -> None:
    token = "qxcvBXOCddjH01-7Aw"
    command = f"python3 -c 'print(0)' # request example; {token}"
    assert lr.commit_safe_command(command, set()) == f"python3 -c {V} <comment>"
    # Quoted and mid-word `#` are not comments; they follow the value rule.
    assert lr.commit_safe_command("echo '# x' a#b", set()) == f"echo {V} {V}"
    multi = f"echo 'a\n# {token}' # x; {token}\nls"
    out = lr.commit_safe_command(multi, set())
    assert token not in out and out.endswith("<comment>\nls")


def test_words_after_double_dash_are_operands() -> None:
    token = "--Q0t3SGc0aFpwblJad2M"
    command = f"python3 -c 'print(0)' -- {token} --seed"
    assert lr.commit_safe_command(command, set()) == f"python3 -c {V} -- {V} {V}"
    assert lr.commit_safe_argv(command.replace("'", "").split(), set()) == \
        ["python3", "-c", V, "--", V, V]
    # A new simple command starts fresh.
    assert lr.commit_safe_command("ls -- x; ls --all", set()) == f"ls -- {V}; ls --all"


@pytest.mark.parametrize(("command", "expected"), [
    ("--Q0t3SGc0aFpwblJad2M x", f"{V} {V}"),
    ("tool --Q0t3SGc0aFpwblJad2M", f"{V} {V}"),
    # The shape is necessary, never sufficient: `--min-count` is no common
    # option and no option of `ls`.
    ("ls --min-count 3 --q0t3sgc0afpwbljad2m9", f"ls {V} 3 {V}"),
    ("ls --all --human-readable --q0t3sgc0afpwbljad2m9", f"ls --all --human-readable {V}"),
    ("ls --a1234", f"ls {V}"),
    ("ls --abc_def", f"ls {V}"),
    ("ls -O2 -xzf -la -Ab -pS3cr3t -resume", f"ls -O2 -xzf -la -Ab {V} {V}"),
    ("ls --threads=4 --Bad=4 --label=x", f"ls --threads=4 {V} {V}"),
    ("ls --output=x --format=4", f"ls --output={V} --format=4"),
])
def test_option_names_are_kept_only_in_the_fixed_shape(command: str, expected: str) -> None:
    assert lr.commit_safe_command(command, set()) == expected


def test_a_source_spelled_option_of_the_fixed_shape_is_kept_for_the_interpreter() -> None:
    known = {"--min-count", "--a1234", "--Bad"}
    assert lr.commit_safe_command("python3 x --min-count 3 --a1234 --Bad=4", set(), known) == \
        f"python3 {V} --min-count 3 {V} {V}"


@pytest.mark.parametrize(("command", "expected"), [
    ("qZ8xLmN3pQ7r --help", f"{V} --help"),
    ("python3.11 -m pytest -q", "python3.11 -m pytest -q"),
    ("/usr/bin/python3 -V", "/usr/bin/python3 -V"),
    ("/opt/qZ8xLmN3pQ7r/bin/python3 -V", f"{V}/python3 -V"),
    ("git status && git qZ8xLmN3pQ7r", f"git status && git {V}"),
    ("env OMP_NUM_THREADS=2 nohup Rscript a.R", f"env OMP_NUM_THREADS=2 nohup Rscript {V}"),
    ("if test -f x; then echo; fi", f"if test -f {V}; then echo; fi"),
])
def test_a_command_word_is_kept_only_from_the_vocabulary(command: str, expected: str) -> None:
    assert lr.commit_safe_command(command, set()) == expected


def test_a_command_word_is_kept_when_it_is_an_evidence_path() -> None:
    keep = lr.commit_keep_paths(["/r/tools/qZ8xLmN3pQ7r.sh"])
    assert lr.commit_safe_command("./tools/qZ8xLmN3pQ7r.sh -v", keep) == \
        "./tools/qZ8xLmN3pQ7r.sh -v"
    assert lr.commit_safe_command("tools/qZ8xLmN3pQ7r.sh -v", keep) == \
        "tools/qZ8xLmN3pQ7r.sh -v"
    assert lr.commit_safe_command("tools/qZ8xLmN3pQ7r -v", keep) == f"{V} -v"


def test_case_subjects_and_patterns_are_values() -> None:
    token = "qZ8xLmN3pQ7r"
    assert lr.commit_safe_command(f"case {token} in a) echo;; esac", set()) == \
        f"case {V} in {V}) echo;; esac"
    out = lr.commit_safe_command(f"case $x in\n{token}) echo 1;;\n*) ls;;\nesac", set())
    assert token not in out
    assert out == f"case {V} in\n{V}) echo 1;;\n{V}) ls;;\nesac"


def test_loop_names_assignment_names_and_heredoc_delimiters_need_their_shape() -> None:
    token = "qZ8xLmN3pQ7r"
    assert lr.commit_safe_command(f"for {token} in a; do echo; done", set()) == \
        f"for {V} in {V}; do echo; done"
    assert lr.commit_safe_command("for f in a; do echo; done", set()) == \
        f"for f in {V}; do echo; done"
    assert lr.commit_safe_command(f"{token}=1 python3 x", set()) == f"{V} python3 {V}"
    assert lr.commit_safe_command(f"python3 x {token}=1 lr=0.1", set()) == \
        f"python3 {V} {V} lr=0.1"
    out = lr.commit_safe_command(f"cat <<{token}\nhello\n{token}\nls", set())
    assert token not in out and out.endswith("<heredoc: 1 lines>\n" + V + "\nls")
    assert lr.commit_safe_command("cat <<'EOF'\nx\nEOF", set()) == \
        "cat <<'EOF'\n<heredoc: 1 lines>\nEOF"


def test_consolidating_a_data_command_never_touches_the_disk(monkeypatch) -> None:
    class NoDisk:
        """`os` (and `os.path`) with every call that can reach a file system
        refused; string operations pass through."""

        _PURE = {"join", "basename", "dirname", "split", "splitext", "normpath", "sep",
                 "isabs", "relpath", "commonpath", "path", "environ", "fspath", "fsencode",
                 "fsdecode", "getpid"}

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            if name == "path":
                return NoDisk(self._real.path)
            if name in self._PURE and name != "relpath":
                return getattr(self._real, name)
            raise AssertionError(f"committed text consolidation called os.{name}")

    monkeypatch.setattr(stop, "os", NoDisk(os))
    record = {"kind": "data_command", "cwd": "/r", "bash_cmd": "head data/x.csv data/y.csv",
              "named_paths": ["data/x.csv", "data/y.csv"],
              "analysis_candidates": ["(inline)", "data/y.csv"],
              "inputs": [{"path": "/r/data/x.csv", "rel_path": "data/x.csv", "sha256": HEX}],
              "outputs": []}
    out = stop.commit_data_command(record)
    assert out["named_paths"] == ["data/x.csv", V]
    assert out["analysis_candidates"] == ["(inline)", V]
    assert out["bash_cmd"] == f"head data/x.csv {V}"


def test_an_unreadable_script_lends_no_word() -> None:
    skip = {"skip_reason": "script-unreadable", "detail": "qZ8xLmN3pQ7r.py",
            "bash_cmd": "python3 qZ8xLmN3pQ7r.py"}
    assert "qZ8xLmN3pQ7r" not in json.dumps(stop.commit_skip(skip))


# -- property: a random token in any slot never reaches the manifest -------------


def _tokens(count: int = 40) -> list[str]:
    """Seeded random credential-like tokens (letters, digits, `-`, `_`; 12-40
    long). Each has an upper- and a lower-case letter: an all-lower-case word
    in an option-name or variable-name slot is, by design, a name."""
    import random
    import string

    rng = random.Random(20261122)
    alphabet = string.ascii_letters + string.digits + "-_"
    out: list[str] = []
    while len(out) < count:
        token = rng.choice(string.ascii_letters) + "".join(
            rng.choice(alphabet) for _ in range(rng.randint(11, 39)))
        if any(c.isupper() for c in token) and any(c.islower() for c in token):
            out.append(token)
    return out


_SLOTS = {
    "command-word": "{t} --help",
    "command-word-path": "/opt/{t}/bin/python3 -V",
    "after-wrapper": "env {t} -x",
    "subcommand": "git {t}",
    "long-option": "python3 a.py --{t}",
    "short-option": "python3 a.py -{t}",
    "option-eq": "python3 a.py --{t}=3",
    "option-value": "python3 a.py --label={t}",
    "after-double-dash": "python3 -c x -- --{t}",
    "case-subject": "case {t} in a) echo;; esac",
    "case-pattern": "case $x in\n{t}) echo 1;;\nesac",
    "case-pattern-alt": "case $x in a|{t}) echo;; esac",
    "comment": "python3 -c x # note; {t}",
    "comment-line": "# {t}\nls",
    "heredoc-body": "cat <<EOF\n{t}\nEOF",
    "heredoc-delimiter": "cat <<{t}\nx\n{t}",
    "assignment-value": "X={t} python3 a.py",
    "assignment-name": "{t}=1 python3 a.py",
    "operand-assignment": "python3 a.py lr={t}",
    "loop-name": "for {t} in a; do echo; done",
    "function-name": "function {t} {{ echo; }}",
    "url": "curl https://h/{t}?a=1",
    "config-operand": "python3 -c x --config {t}",
    "named-path": "head data/{t}.csv",
    "redirect": "echo 1 > {t}",
    "after-semicolon": "ls; {t}",
    "after-pipe": "ls | {t} -n",
    # All-lower-case tokens of the option-name shape: kept only on evidence
    # of the role (a common option, the command's own, the source's).
    "lower-long-option": "python3 -c x --proof --{t}",
    "lower-long-option-eq": "python3 a.py --{t}=3",
    "lower-long-option-vocab": "git log --oneline --{t}",
    "lower-long-option-wrapper": "timeout --{t} 9 python3 a.py",
    # Long digit strings: a seed value the argv masks is never republished.
    "seed-after-double-dash": "python3 -c x -- --seed {t}",
    "seed-long": "python3 a.py --seed {t}",
    "seed-eq": "python3 a.py --seed={t}",
}
_LOWER_SLOTS = frozenset(s for s in _SLOTS if s.startswith("lower-"))
_DIGIT_SLOTS = frozenset(s for s in _SLOTS if s.startswith("seed-"))


def _lower_tokens(count: int = 40) -> list[str]:
    """Seeded all-lower-case hyphenated tokens of the option-name shape
    (`cinder-lattice-sable-amber`; at most 3 digits): shape alone must never
    keep them."""
    import random
    import string

    rng = random.Random(20261123)
    out: list[str] = []
    while len(out) < count:
        parts = ["".join(rng.choice(string.ascii_lowercase + string.digits)
                         for _ in range(rng.randint(5, 9))) for _ in range(rng.randint(2, 4))]
        token = "-".join(parts)
        if token[0].isalpha() and sum(c.isdigit() for c in token) <= 3 \
                and lr._option_name_ok(f"--{token}") \
                and f"--{token}" not in lr._COMMIT_COMMON_OPTIONS:
            out.append(token)
    return out


def _digit_tokens(count: int = 40) -> list[str]:
    """Seeded digit strings too long for the short-number rule (7-19)."""
    import random

    rng = random.Random(20261124)
    return [str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9))
                                             for _ in range(rng.randint(6, 18)))
            for _ in range(count)]


def _slot_tokens(slot: str, count: int = 40) -> list[str]:
    if slot in _LOWER_SLOTS:
        return _lower_tokens(count)
    if slot in _DIGIT_SLOTS:
        return _digit_tokens(count)
    return _tokens(count)


def _records_for(slot: str, token: str) -> list[dict]:
    command = _SLOTS[slot].format(t=token)
    words = command.split()
    seeds = ({"seeds": [{"value": int(token), "origin": "argv"},
                        {"value": int(token), "origin": "source"}]}
             if token.isdigit() else {})
    event = {"ts": "t", "bash_cmd": command, "script": None, "script_sha256": HEX,
             "script_candidate": None, "inputs": [], "outputs": [],
             "lineage_warnings": [f"the command names {command}."],
             "seeds_detected": [int(token)] if token.isdigit() else [],
             "step": {"argv": words, "executable": words[0], "interpreter": words[0],
                      "script_args": words[1:], "params": words[1:], "wrappers": words[:2],
                      "env": {"X": token, "Y": {"text": token, "expanded": False}},
                      **seeds,
                      "config_files": [
                          {"path": f"/r/{token}", "rel_path": token, "via": "--config",
                           "sha256": None, "hash_status": "missing"},
                          {"path": token, "via": "--config", "sha256": None,
                           "hash_status": "unresolved"},
                          {"path": f"https://h/{token}", "via": "--config", "sha256": None,
                           "hash_status": "remote"}]}}
    data = {"kind": "data_command", "cwd": "/r", "bash_cmd": command,
            "named_paths": [f"data/{token}.csv", token, *words],
            "analysis_candidates": [f"{token}.py", *words], "inputs": [], "outputs": [],
            "step": {"argv": words, "executable": words[0]}}
    skip = {"skip_reason": "script-unreadable", "detail": f"{token}.py", "bash_cmd": command}
    observation = {"bash_cmd": command, "repo_root": "/r",
                   "changes": {"created": [], "modified": [], "deleted": []}}
    return [stop.commit_event(event), stop.commit_data_command(data), stop.commit_skip(skip),
            stop.commit_observation(observation)]


@pytest.mark.parametrize("slot", sorted(_SLOTS))
def test_no_random_token_survives_in_any_slot(slot: str) -> None:
    for token in _slot_tokens(slot):
        committed = json.dumps(_records_for(slot, token))
        assert token not in committed, (slot, token)
        # Nor a long piece of it (a split at `-` or `_` publishing half).
        for piece in __import__("re").split(r"[-_]", token):
            if len(piece) >= 8:
                assert piece not in committed, (slot, token, piece)


def test_a_random_token_in_a_slot_does_not_reach_a_written_manifest(tmp_path: Path) -> None:
    for index, slot in enumerate(sorted(_SLOTS)):
        token = _slot_tokens(slot, len(_SLOTS))[index]
        root = tmp_path / f"repo{index}"
        written = _manifest_text(root, _SLOTS[slot].format(t=token), "import sys\n")
        assert token not in written.replace(str(root), "<root>"), slot


# -- file records for files that never existed ---------------------------------


def test_a_missing_input_path_is_not_committed() -> None:
    token = "Qz8vTxK2mPlR9wYc"
    record = {"kind": "data_command", "bash_cmd": f"cp {token} out.csv",
              "inputs": [{"path": f"/repo/{token}", "rel_path": token}],
              "outputs": [{"path": "/repo/out.csv", "rel_path": "out.csv",
                           "sha256": "ab" * 32}]}
    committed = stop.commit_data_command(record)
    assert token not in json.dumps(committed)
    assert committed["outputs"][0]["rel_path"] == "out.csv"


def test_an_existing_file_with_a_pending_hash_keeps_its_path() -> None:
    event = {"ts": "t", "bash_cmd": "python3 run.py", "script": None,
             "inputs": [{"path": "/repo/big.h5", "rel_path": "big.h5", "size_bytes": 10,
                         "hash_status": "skipped-budget"}], "outputs": []}
    assert stop.commit_event(event)["inputs"][0]["rel_path"] == "big.h5"


# -- stores that outlive a session ---------------------------------------------


def test_an_archived_event_stream_holds_only_allowlisted_text(tmp_path: Path) -> None:
    # An accepted or abandoned session's queue is archived, not deleted.
    events = _write_session(tmp_path, f"tool -k {RANDOM_TOKEN} --pin 482913")
    archive = tmp_path / "archive.tmp"
    stop.commit_stream(events, archive)
    text = archive.read_text()
    assert RANDOM_TOKEN not in text and "482913" not in text
    assert [json.loads(line).get("kind") for line in text.splitlines()] == \
        [None, "data_command", "skip"]


def test_the_observation_store_holds_only_allowlisted_text() -> None:
    observation = {"kind": "observation", "repo_root": "/r",
                   "bash_cmd": f"python3 fit.py -k {RANDOM_TOKEN} > out/x.csv",
                   "changes": {"created": [{"path": "out/x.csv"}]}}
    assert lr.commit_observation_command(observation) == f"python3 {V} -k {V} > out/x.csv"
