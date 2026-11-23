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
    ("wc -l tables/{first,second}.tsv", "shell-data"),
    ("cat sample.{csv,tsv}", "shell-data"),
    ("head 'data/my file.tsv'", "shell-data"),
    ("grep -f patterns.list data/x", "shell-data"),
    ("echo 'a <<EOF' && head data/x.csv", "shell-data"),
    ("head $HOME/proj/data/x.csv", "shell-data"),
    ("ls /tmp/run/output/", "shell-data"),
    ("grep -c TP53 data/genes.tsv", "shell-data"),
    ("rg -n foo -g '*.R' analysis/a1/outputs/", "shell-data"),
    ("grep -e x -- data/y.csv", "shell-data"),
])
def test_classify_positive(command, expected):
    assert replay.classify(command) == expected


@pytest.mark.parametrize("command", [
    # A project's own script with a helper's name, or anything else under a
    # plugin cache, is not Mycelium's.
    "python3 scripts/register_value.py",
    "python3 /x/plugins/cache/other/tool/run.py",
])
def test_only_mycelium_layouts_are_helpers(command):
    assert replay.classify(command) == "script"


def test_relative_paths_are_judged_from_the_call_directory(tmp_path):
    root = str(tmp_path)
    assert replay.classify("head raw/counts", f"{root}/data", root) == "shell-data"
    assert replay.classify("cd data && head raw/counts", root, root) == "shell-data"
    assert replay.classify("head raw/counts", root, root) is None
    assert replay.classify("echo cd data; head raw/counts", root, root) is None
    # From inside a data directory, URLs and redirections are not its files.
    inside = f"{root}/data/raw"
    assert replay.classify("curl -fsSL https://example.org/suppl/list.html", inside, root) is None
    assert replay.classify("ps aux 2>/dev/null | head -3", inside, root) is None


def test_absolute_patterns_do_not_depend_on_this_machine():
    assert replay.is_pattern("/output/runs/")
    assert not replay.is_pattern("/scratch/run/output/")
    assert not replay.is_pattern("/Users/x/data/a.csv")


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
    'python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/finalize_handoff.py"',
    "git commit -m 'Add data/x.csv to the notes'",
    # A quoted pipe or heredoc operator is text, not syntax.
    "echo 'cat x | python3'",
    "echo 'python3 <<EOF'",
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
    assert calls == [('head -2 "data/x.csv"', "/p", "literal"), ("ls data/", "/p/sub", "literal"),
                     ("wc -l data/y.csv", None, "omitted")]
    assert unparsed == 3


def test_parse_code_mode_skips_comments_and_marks_computed_workdirs():
    js = ("// tools.exec_command({cmd: 'head data/a.csv'})\n"
          "/* tools.exec_command({cmd: 'head data/b.csv'}) */\n"
          "const s = '// not a comment'; tools.exec_command({cmd: 'ls data/', workdir: dir});\n"
          "tools.exec_command({cmd: 'ls data/', workdir});\n")
    calls, unparsed = replay.parse_code_mode(js)
    assert calls == [("ls data/", None, "computed"), ("ls data/", None, "computed")]
    assert unparsed == 0


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
        {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "2",
                                              "output": "Script completed"}},
        # A relative workdir is the session's; a background run has no exit.
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                              "call_id": "3",
                                              "arguments": json.dumps({"cmd": "wc -l data/x.csv",
                                                                       "workdir": "sub"})}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "3",
                                              "output": "Process running with session ID 7"}},
        # Rejected, or no result: no hook ran.
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                              "call_id": "4",
                                              "arguments": json.dumps({"cmd": "head data/z.csv"})}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "4",
                                              "output": "rejected by user"}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command",
                                              "call_id": "5",
                                              "arguments": json.dumps({"cmd": "head data/w.csv"})}},
        # A snippet with no output did not run.
        {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "6",
                                              "input": 'tools.exec_command({cmd:"ls data/"})'}},
    ]
    path = tmp_path / "rollout-1.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rec) + "\n")
    cmds, stats = replay.extract_codex(path, "P", Path("/p"))
    assert [(c.command, c.cwd, c.bash_exit, c.evidence) for c in cmds] == [
        ("head data/x.csv", "/p", 0, "exit-status"),
        ("ls data/", "/p/w", None, "code-mode-source"),
        ("wc -l data/x.csv", "/p/sub", None, "exit-status")]
    assert (stats.computed_cmd, stats.not_run) == (0, 3)
    assert replay.codex_rollouts([tmp_path], Path("/p")) == [path]
    assert replay.codex_rollouts([tmp_path], Path("/q")) == []


def test_an_unreadable_rollout_is_reported(tmp_path):
    (tmp_path / "rollout-bad.jsonl.zst").write_bytes(b"not zstd")
    unreadable: list[Path] = []
    assert replay.codex_rollouts([tmp_path], Path("/p"), unreadable) == []
    assert unreadable == [tmp_path / "rollout-bad.jsonl.zst"]


def test_resolve_cwd_maps_a_removed_worktree(tmp_path):
    root = tmp_path / "Proj"
    (root / "analysis").mkdir(parents=True)
    gone = root / ".claude" / "worktrees" / "w1" / "analysis"
    assert replay.resolve_cwd(str(gone), root) == (str(root / "analysis"), True)
    assert replay.resolve_cwd(str(root), root) == (str(root), False)


# ---- end to end ---------------------------------------------------------------

def test_replay_counts_events_skips_and_misses(tmp_path, monkeypatch):
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
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(replay.tempfile, "tempdir", str(scratch))
    report = replay.run([("P", root)], projects, [tmp_path / "none"])
    project = report["projects"]["P"]
    assert project["commands"] == 4
    p = project["populations"]["executed"]
    assert p["data_touching"] == 3
    assert (p["event"], p["skip"], p["silent_miss"]) == (2, 1, 0)
    assert (p["event_analysis"], p["event_data_command"]) == (1, 1)
    assert p["skip_reasons"] == {"script-unreadable": 1}
    assert project["populations"]["code-mode-source"]["data_touching"] == 0
    # No command text reaches the report, and no scratch file is left.
    text = json.dumps(report)
    for c in cmds:
        assert c not in text
    assert list(scratch.iterdir()) == []
