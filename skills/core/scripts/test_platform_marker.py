"""Guard: every test module that can behave differently on macOS is ``platform``.

The macOS CI job runs only ``pytest -m platform`` (see conftest.py and
.github/workflows/validate-skill.yml). A module that needs macOS coverage but
forgets the marker would silently drop out of it, so this test reads every
test module's syntax tree and fails with the module name and the reason it
needs ``pytestmark = pytest.mark.platform``. A module needs it when it

- spawns a shell or hook: ``shell=True``, ``os.system``/``os.exec*``/
  ``os.spawn*``, or a ``subprocess`` call (through any alias) whose program is
  a shell (``bash``, ``sh``, ``zsh``, ``env bash``), a ``*.sh`` script, an
  expression naming a hook (``HOOKS_DIR / name``), or anything that is not
  ``sys.executable`` or a literal program name;
- skips or xfails on a platform condition (``sys.platform``, ``darwin``, a
  case-sensitive volume, ...): such a test may run *only* on macOS;
- takes locks itself (imports ``fcntl`` or ``mycelium_locks``) or runs work
  concurrently (threads, process pools, ``multiprocessing``, ``Popen`` in a
  loop), since lock contention and scheduling differ by platform;
- depends on timestamp semantics (``os.utime``, ``st_ctime``), which differ
  between APFS and ext4; or
- imports helpers from another test module that needs it.

conftest.py also fails, at run time, any unmarked test that is skipped for a
platform reason, which catches skips this static reading cannot see.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent

SUBPROCESS_CALLS = {"run", "Popen", "call", "check_call", "check_output"}
SHELLS = {"bash", "sh", "zsh", "dash"}
HOOKISH = re.compile(r"hook|\.sh\b|\b(?:ba|z|da)?sh\b", re.IGNORECASE)
PLATFORM_HINT = re.compile(
    r"sys\.platform|platform\.system|os\.uname|darwin|macos|linux|win32"
    r"|case[-_ ]?(?:in)?sensitiv|apfs|ext4",
    re.IGNORECASE,
)
SKIP_CALLS = {"pytest.skip", "pytest.xfail", "pytest.mark.skipif", "pytest.mark.xfail"}
CONCURRENCY = re.compile(
    r"\b(?:ThreadPoolExecutor|ProcessPoolExecutor|threading\.Thread|multiprocessing)\b"
)
LOCKS = re.compile(r"^\s*(?:import|from)\s+(?:fcntl|mycelium_locks)\b|\bfcntl\.", re.M)
TIMESTAMPS = re.compile(r"\bos\.utime\b|\bst_ctime(?:_ns)?\b")


class _Aliases:
    """Names that refer to the ``subprocess`` module or its spawning functions."""

    def __init__(self, tree: ast.Module):
        self.modules = {"subprocess"}
        self.functions: dict[str, str] = {}  # local name -> subprocess function
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "subprocess":
                        self.modules.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
                for alias in node.names:
                    if alias.name in SUBPROCESS_CALLS:
                        self.functions[alias.asname or alias.name] = alias.name

    def spawn_name(self, func: ast.expr) -> str | None:
        """The subprocess function ``func`` calls, or None."""
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id in self.modules and func.attr in SUBPROCESS_CALLS:
                return func.attr
        elif isinstance(func, ast.Name):
            return self.functions.get(func.id)
        return None

    def is_spawn(self, func: ast.expr) -> bool:
        return self.spawn_name(func) is not None


def _list_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """Map names assigned a list or tuple literal anywhere in ``tree``."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, node.value)
    return found


def _program(expr: ast.expr, lists: dict[str, ast.expr], depth: int = 0) -> ast.expr | None:
    """The expression naming the program an argv runs, or None if unknown."""
    if depth > 5:
        return None
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        return _program(expr.left, lists, depth + 1)
    if isinstance(expr, ast.Name) and expr.id in lists:
        return _program(lists[expr.id], lists, depth + 1)
    if isinstance(expr, (ast.List, ast.Tuple)):
        if not expr.elts:
            return None
        first = expr.elts[0]
        if isinstance(first, ast.Starred):
            return _program(first.value, lists, depth + 1)
        if (len(expr.elts) > 1 and isinstance(first, ast.Constant)
                and str(first.value).rsplit("/", 1)[-1] == "env"):
            return expr.elts[1]
        return first
    return expr


def _spawn_reason(expr: ast.expr | None) -> str | None:
    if expr is None:
        return "runs a program that cannot be read statically"
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        words = expr.value.split()
        name = words[0].rsplit("/", 1)[-1] if words else ""
        if name in SHELLS or name.endswith(".sh"):
            return "spawns a shell or hook"
        return None  # a literal program name such as git, tar or Rscript
    if ast.unparse(expr) == "sys.executable":
        return None
    if HOOKISH.search(ast.unparse(expr)):
        return "spawns a shell or hook"
    return "runs a program that cannot be read statically"


def shell_spawns(source: str) -> list[str]:
    """Describe each call in ``source`` that spawns a shell, hook or unknown program."""
    tree = ast.parse(source)
    aliases = _Aliases(tree)
    lists = _list_assignments(tree)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "os"
                and (func.attr == "system" or func.attr.startswith(("exec", "spawn")))):
            found.append(f"spawns a shell or hook: {ast.unparse(node)[:120]}")
            continue
        if not aliases.is_spawn(func):
            continue
        if any(kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
               for kw in node.keywords):
            found.append(f"spawns a shell or hook: {ast.unparse(node)[:120]}")
            continue
        argv = node.args[0] if node.args else next(
            (kw.value for kw in node.keywords if kw.arg == "args"), None)
        if argv is None:
            continue
        reason = _spawn_reason(_program(argv, lists))
        if reason:
            found.append(f"{reason}: {ast.unparse(node)[:120]}")
    return found


def platform_skips(source: str) -> list[str]:
    """Skips or xfails in ``source`` whose condition or reason names a platform."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and ast.unparse(node.func) in SKIP_CALLS:
            if PLATFORM_HINT.search(ast.unparse(node)):
                found.append(ast.unparse(node)[:120])
        elif isinstance(node, ast.If) and PLATFORM_HINT.search(ast.unparse(node.test)):
            if any(isinstance(inner, ast.Call) and ast.unparse(inner.func) in SKIP_CALLS
                   for stmt in node.body for inner in ast.walk(stmt)):
                found.append(f"if {ast.unparse(node.test)[:100]}: skip")
    return found


def _popen_in_loop(tree: ast.Module, aliases: _Aliases) -> bool:
    loops = (ast.For, ast.While, ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)
    for loop in ast.walk(tree):
        if isinstance(loop, loops):
            for node in ast.walk(loop):
                if isinstance(node, ast.Call) and aliases.spawn_name(node.func) == "Popen":
                    return True
    return False


def other_reasons(source: str) -> list[str]:
    """Lock, concurrency and timestamp reasons a module needs the marker."""
    reasons = []
    if LOCKS.search(source):
        reasons.append("takes locks itself (fcntl or mycelium_locks)")
    tree = ast.parse(source)
    if CONCURRENCY.search(source) or _popen_in_loop(tree, _Aliases(tree)):
        reasons.append("runs work concurrently (threads, process pools or Popen in a loop)")
    if TIMESTAMPS.search(source):
        reasons.append("depends on timestamp semantics (os.utime or st_ctime)")
    return reasons


def imported_test_modules(source: str) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("test_"):
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names if a.name.startswith("test_"))
    return names


def is_marked_platform(source: str) -> bool:
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in targets):
                if "mark.platform" in ast.unparse(node.value):
                    return True
    return False


def required_modules(sources: dict[str, str]) -> dict[str, str]:
    """Map each module that must be marked to the first reason it must be."""
    required = {}
    for name, source in sources.items():
        spawns = shell_spawns(source)
        skips = platform_skips(source)
        if spawns:
            required[name] = spawns[0]
        elif skips:
            required[name] = f"skips on a platform condition, so may run only on macOS: {skips[0]}"
        else:
            reasons = other_reasons(source)
            if reasons:
                required[name] = reasons[0]
    changed = True
    while changed:
        changed = False
        for name, source in sources.items():
            if name in required:
                continue
            for imported in sorted(imported_test_modules(source)):
                if imported in required:
                    required[name] = f"imports helpers from {imported}, which needs the marker"
                    changed = True
                    break
    return required


def _suite_sources() -> dict[str, str]:
    """Every test module except this guard, whose fixtures quote what it detects."""
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(HERE.glob("test_*.py"))
            if p.name != Path(__file__).name}


def test_every_platform_sensitive_module_is_marked_platform():
    sources = _suite_sources()
    required = required_modules(sources)
    # A detector that stopped matching would pass vacuously.
    assert {"test_codex_compatibility", "test_hook_state_port", "test_antigravity_hooks",
            "test_lineage_index", "test_mycelium_locks"} <= set(required)
    missing = [
        f"  {name}.py: {reason}"
        for name, reason in sorted(required.items())
        if not is_marked_platform(sources[name])
    ]
    assert not missing, (
        "These test modules can behave differently on macOS but are not marked "
        "`platform`, so the macOS CI job (pytest -m platform) would skip them. "
        "Add `pytestmark = pytest.mark.platform` at module level:\n" + "\n".join(missing)
    )


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\nsubprocess.run(['bash', str(p)])",
        "import subprocess\nsubprocess.run(['/bin/bash', '-c', 'x'])",
        "import subprocess\nsubprocess.run(['sh', '-c', 'true'])",
        "import subprocess\nsubprocess.run(['/usr/bin/env', 'bash', 'x.sh'])",
        "import subprocess\nsubprocess.run([str(HOOKS_DIR / name)], input=p)",
        "import subprocess\nsubprocess.Popen([str(hook)])",
        "import subprocess\nsubprocess.run('echo hi', shell=True)",
        "import subprocess\nsubprocess.check_output(['./run.sh'])",
        "import subprocess\nsubprocess.run([bash, script])",
        "import os\nos.system('ls')",
        "import subprocess as sp\nsp.run(['bash', 'x'])",
        "from subprocess import run\nrun(['bash', 'x'])",
        "from subprocess import check_output as co\nco(['zsh', '-c', 'x'])",
        "import subprocess\nsubprocess.run(command)",
        "import subprocess\nsubprocess.run([shutil.which('python3'), 'x.py'])",
        "import subprocess\nbase = ['bash', 'x']\nsubprocess.run([*base, '--y'])",
    ],
)
def test_detector_flags_shell_and_unknown_spawns(source):
    assert shell_spawns(source)


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess, sys\nsubprocess.run([sys.executable, str(SCRIPT), '--bash-cmd', cmd])",
        "import subprocess, sys\nsubprocess.run([sys.executable, '-'] + extra)",
        "import subprocess, sys\nbase = [sys.executable, 'x.py']\nsubprocess.run([*base, '--y'])",
        "import subprocess\nsubprocess.run(['git', 'init', '-q', str(root)])",
        "import subprocess\nsubprocess.run(['Rscript', str(script)])",
        "text = 'bash run.sh'\nassert 'mycelium-post-action.sh' in text",
    ],
)
def test_detector_ignores_python_and_literal_programs(source):
    assert not shell_spawns(source)


@pytest.mark.parametrize(
    "source",
    [
        "import pytest, sys\n@pytest.mark.skipif(sys.platform != 'darwin', reason='x')\ndef test_x(): pass",
        "import pytest\ndef test_x(tmp_path):\n    if not _case_insensitive(tmp_path):\n"
        "        pytest.skip('case-sensitive volume')",
        "import pytest\ndef test_x():\n    pytest.skip('needs macOS')",
        "import pytest, platform\ndef test_x():\n    if platform.system() == 'Linux':\n"
        "        pytest.xfail('ext4')",
    ],
)
def test_platform_skips_are_detected(source):
    assert platform_skips(source)


def test_non_platform_skips_are_not_flagged():
    source = ("import pytest, shutil\n"
              "@pytest.mark.skipif(shutil.which('Rscript') is None, reason='R is not installed')\n"
              "def test_x(): pass\n"
              "def test_y():\n    pytest.skip('origin/main is not available')")
    assert not platform_skips(source)


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("import fcntl", "locks"),
        ("import mycelium_locks as locks", "locks"),
        ("from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(8)", "concurrently"),
        ("import threading\nthreading.Thread(target=f)", "concurrently"),
        ("import subprocess\nprocs = [subprocess.Popen([sys.executable, s]) for s in xs]", "concurrently"),
        ("import os\nos.utime(p, ns=(1, 1))", "timestamp"),
        ("x = p.stat().st_ctime_ns", "timestamp"),
    ],
)
def test_lock_concurrency_and_timestamp_reasons(source, reason):
    assert any(reason in r for r in other_reasons(source))


def test_plain_logic_module_needs_no_marker():
    source = "import json\n\ndef test_x():\n    assert json.loads('1') == 1\n"
    assert not required_modules({"test_plain": source})


def test_importing_a_marked_helper_module_requires_the_marker():
    sources = {
        "test_a": "import subprocess\nsubprocess.run(['bash', 'x'])",
        "test_b": "from test_a import helper",
        "test_c": "from test_b import other",
        "test_d": "import json",
    }
    assert set(required_modules(sources)) == {"test_a", "test_b", "test_c"}


def test_marker_detection():
    assert is_marked_platform("import pytest\npytestmark = pytest.mark.platform")
    assert is_marked_platform("import pytest\npytestmark = [pytest.mark.platform, pytest.mark.slow]")
    assert not is_marked_platform("import pytest\n@pytest.mark.platform\ndef test_x(): pass")


def test_name_based_marking_reaches_symlink_tests_in_unmarked_modules():
    import conftest

    assert conftest.PLATFORM_NAME.search("test_refuses_symlinked_living_before_writing")
    assert conftest.PLATFORM_NAME.search("test_repo_root_uses_realpath")
    assert not conftest.PLATFORM_NAME.search("test_redaction_allowlist_keeps_plain_paths")


@pytest.mark.parametrize(
    "reason",
    ["case-sensitive volume", "needs macOS", "darwin only", "not supported on Linux", "APFS clones"],
)
def test_platform_skip_reasons_are_recognised(reason):
    import conftest

    assert conftest.PLATFORM_SKIP.search(reason)


@pytest.mark.parametrize(
    "reason",
    ["R is not installed", "origin/main is not available", "h5py is not installed",
     "en_US.UTF-8 is not installed here, so stdout is not strict"],
)
def test_ordinary_skip_reasons_are_not_platform_reasons(reason):
    import conftest

    assert not conftest.PLATFORM_SKIP.search(reason)


def test_shards_partition_the_suite():
    import conftest

    ids = [f"skills/core/scripts/test_x.py::test_{i}[case-{i % 7}]" for i in range(600)]
    for n in (1, 2, 3):
        shards = [{i for i in ids if conftest.shard_of(i, n) == k} for k in range(1, n + 1)]
        assert set().union(*shards) == set(ids)
        assert sum(len(s) for s in shards) == len(ids)
        assert all(len(s) > len(ids) / n * 0.8 for s in shards)


@pytest.mark.parametrize("value", ["0/2", "3/2", "a/b", "2", "", " "])
def test_bad_shard_values_are_usage_errors(value):
    import conftest

    with pytest.raises(pytest.UsageError):
        conftest.parse_shard(value)
