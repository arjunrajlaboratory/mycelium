"""Tests for the lineage transcript replay. Synthetic transcripts only."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("lineage_replay.py")
SPEC = importlib.util.spec_from_file_location("lineage_replay", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
replay = importlib.util.module_from_spec(SPEC)
sys.modules["lineage_replay"] = replay
SPEC.loader.exec_module(replay)


# ---- classification: paired positives and negatives -------------------------

@pytest.mark.parametrize("command, expected", [
    ("python3 analysis/run.py", "script"),
    ("cd analysis && python run.py --seed 3", "script"),
    ("uv run python scripts/fit.py", "script"),
    ("conda run -n env python fit.py", "script"),
    ("Rscript analysis/tree.R", "script"),
    ("R CMD BATCH fit.R", "script"),
    ("R --vanilla -f fit.R", "script"),
    ("jupyter nbconvert --to html --execute nb.ipynb", "script"),
    ("python -m mypkg.pipeline", "script"),
    ("python3 -c 'import pandas as pd; print(1)'", "inline"),
    ("Rscript -e 'print(1)'", "inline"),
    ("python3 - <<'PY'\nprint(1)\nPY", "inline"),
    ("cat x.py | python3", "inline"),
    ("head -5 data/raw/x.csv", "shell-data"),
    ("zcat reads.fastq.gz | wc -l", "shell-data"),
    ("ls analysis/a1/outputs/", "shell-data"),
    ("cp table.tsv backup.tsv", "shell-data"),
    ("wc -l results/summary.txt", "shell-data"),
    ("wc -l registry/{samples,studies}.tsv", "shell-data"),
    ("head $HOME/proj/data/x.csv", "shell-data"),
    ("ls /tmp/run/output/", "shell-data"),
    ("grep -c TP53 data/genes.tsv", "shell-data"),
    ("rg -n foo -g '*.R' analysis/a1/outputs/", "shell-data"),
    ("grep -e x -- data/y.csv", "shell-data"),
])
def test_classify_positive(command, expected):
    assert replay.classify(command) == expected


@pytest.mark.parametrize("command", [
    "python3 --version",
    "python -m pytest -q",
    "python3 -m pip install pandas",
    "Rscript --version",
    "R CMD INSTALL pkg",
    "snakemake -n",
    "git status",
    "pip install -r requirements.txt",
    "ls .living/log/data-lineage/",
    "cat .mycelium/state.json",
    "curl -O https://example.org/data/x.csv",
    "echo 'python3 run.py'",
    "python3 /x/plugins/cache/mycelium/mycelium/0.9.7/skills/core/scripts/register_value.py",
    "python3 skills/core/scripts/init_repo.py --help",
    # A heredoc body that writes a script is not a data access.
    "cat > fit.py <<'EOF'\nimport pandas as pd\npd.read_csv('data/x.csv')\nEOF",
    "sed -n '1,40p' README.md",
    # Search patterns and exclusions are not paths.
    "rg -n foo analysis -g '!**/output/**'",
    "git grep -n x -- ':!.living/outputs/reviews/*' ':(exclude)data/raw'",
    "git diff --name-only | rg 'outputs/intermediate|__pycache__'",
    "grep -v -E '^analysis/(a|b)/output'",
    "find . -type f | grep -v '/output/runs/'",
    "git ls-files analysis | grep -v \"output/runs\"",
    "rg -n -e 'data/x.csv' src",
    "git grep -n 'outputs/panels' -- '*.R'",
])
def test_classify_negative(command):
    assert replay.classify(command) is None


def test_heredoc_with_here_string_is_not_a_body():
    text, had = replay.strip_heredoc_bodies("grep x <<< 'a'\nhead data/x.csv")
    assert not had and "head data/x.csv" in text


def test_shape_is_content_free():
    s = replay.shape("cd /secret/place && for f in a b; do head $f; done | sort")
    assert set(s.split("+")) <= {"heredoc", "pipe", "loop", "cd", "substitution",
                                 "background", "sequence", "simple"}
    assert "secret" not in s


# ---- Codex code-mode parsing ------------------------------------------------

def test_parse_code_mode_literal_and_computed_calls():
    js = ('const a = await tools.exec_command({cmd:"head -2 \\"data/x.csv\\"",'
          '"workdir":"/p","yield_time_ms":1});\n'
          "const b = tools.exec_command({cmd:`ls data/`, workdir: '/p/sub'});\n"
          "const c = tools.exec_command({cmd:`cat ${f}`});\n"
          "const d = tools.exec_command({cmd: someVar});\n"
          "const e = tools.exec_command({cmd: 'cat ' + f, workdir: '/p'});\n"
          "const g = tools.exec_command({ cmd : 'wc -l data/y.csv' , yield_time_ms: 5 });\n")
    calls, unparsed = replay.parse_code_mode(js)
    assert calls == [('head -2 "data/x.csv"', "/p"), ("ls data/", "/p/sub"),
                     ("wc -l data/y.csv", None)]
    assert unparsed == 3


# ---- extraction ---------------------------------------------------------------

def _claude_line(content, role="assistant", cwd="/p"):
    return json.dumps({"type": role, "cwd": cwd, "message": {"role": role, "content": content}})


def test_extract_claude_exit_status_and_unrun_calls(tmp_path):
    lines = [
        _claude_line([{"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "head data/x.csv"}}]),
        _claude_line([{"type": "tool_result", "tool_use_id": "a", "content": "ok"}], "user"),
        _claude_line([{"type": "tool_use", "id": "b", "name": "Bash", "input": {"command": "cat data/y.csv"}}]),
        _claude_line([{"type": "tool_result", "tool_use_id": "b", "is_error": True, "content": "Exit code 2\nnope"}], "user"),
        _claude_line([{"type": "tool_use", "id": "c", "name": "Bash", "input": {"command": "rm data/z.csv"}}]),
        _claude_line([{"type": "tool_result", "tool_use_id": "c", "is_error": True,
                       "content": "Permission for this action was denied by the classifier."}], "user"),
        _claude_line([{"type": "tool_use", "id": "d", "name": "Bash", "input": {"command": "sleep 100"}}]),
    ]
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(lines) + "\n")
    got = [(c.command, c.bash_exit) for c in replay.extract_claude(path, "P")]
    assert got == [("head data/x.csv", 0), ("cat data/y.csv", 2)]


def test_claude_files_include_worktrees_and_subagents(tmp_path):
    root = Path("/Users/me/code/Proj")
    base = tmp_path / "projects"
    main = base / "-Users-me-code-Proj"
    wt = base / "-Users-me-code-Proj--claude-worktrees-x"
    other = base / "-Users-me-code-Project2"
    for d in (main / "sid" / "subagents", wt, other):
        d.mkdir(parents=True)
    for f in (main / "a.jsonl", main / "sid" / "subagents" / "b.jsonl", wt / "c.jsonl", other / "d.jsonl"):
        f.write_text("")
    names = sorted(p.name for p in replay._claude_files(base, root))
    assert names == ["a.jsonl", "b.jsonl", "c.jsonl"]


def test_extract_codex_function_and_code_mode_calls(tmp_path):
    rec = [
        {"type": "session_meta", "payload": {"cwd": "/p"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                              "call_id": "1",
                                              "arguments": json.dumps({"cmd": "head data/x.csv"})}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "1",
                                              "output": "Process exited with code 0\n"}},
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "2",
                                              "input": 'tools.exec_command({cmd:"ls data/", workdir:"/p/w"})'}},
    ]
    path = tmp_path / "rollout-1.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rec) + "\n")
    cmds, unparsed = replay.extract_codex(path, "P", Path("/p"))
    assert [(c.command, c.cwd, c.bash_exit) for c in cmds] == [
        ("head data/x.csv", "/p", 0), ("ls data/", "/p/w", None)]
    assert unparsed == 0
    assert replay.codex_rollouts([tmp_path], Path("/p")) == [path]
    assert replay.codex_rollouts([tmp_path], Path("/q")) == []


def test_resolve_cwd_maps_a_removed_worktree(tmp_path):
    root = tmp_path / "Proj"
    (root / "analysis").mkdir(parents=True)
    gone = root / ".claude" / "worktrees" / "w1" / "analysis"
    assert replay.resolve_cwd(str(gone), root) == (str(root / "analysis"), True)
    assert replay.resolve_cwd(str(root), root) == (str(root), False)


# ---- end to end ---------------------------------------------------------------

def test_replay_counts_events_skips_and_misses(tmp_path):
    """Drives the real detector: a script run and a data look are events, a
    deleted script is a logged skip, and nothing data-touching is silent."""
    root = tmp_path / "Proj"
    (root / "data").mkdir(parents=True)
    (root / "data" / "x.csv").write_text("a\n1\n")
    (root / "fit.py").write_text("import pandas as pd\npd.read_csv('data/x.csv')\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    projects = tmp_path / "projects"
    tdir = projects / ("-" + str(root).strip("/").replace("/", "-").replace(".", "-"))
    tdir.mkdir(parents=True)
    cmds = ["python3 fit.py", "head -2 data/x.csv", "python3 gone.py", "git status"]
    lines = []
    for n, c in enumerate(cmds):
        lines.append(_claude_line([{"type": "tool_use", "id": str(n), "name": "Bash",
                                    "input": {"command": c}}], cwd=str(root)))
        lines.append(_claude_line([{"type": "tool_result", "tool_use_id": str(n), "content": ""}],
                                  "user", cwd=str(root)))
    (tdir / "s.jsonl").write_text("\n".join(lines) + "\n")
    report = replay.run([("P", root)], projects, [tmp_path / "none"])
    p = report["projects"]["P"]
    assert p["commands"] == 4
    assert p["data_touching"] == 3
    assert (p["event"], p["skip"], p["silent_miss"]) == (2, 1, 0)
    assert p["by_category"]["claude:script"]["skip:script-unreadable"] == 1
    # No command text reaches the report.
    text = json.dumps(report)
    for c in cmds:
        assert c not in text
