"""Guard: every test module that spawns a shell or a hook script is ``platform``.

The macOS CI job runs only ``pytest -m platform`` (see conftest.py and
.github/workflows/validate-skill.yml). A new module that runs Bash or the
lifecycle hooks but forgets the marker would silently drop out of macOS
coverage, so this test reads every test module's syntax tree and fails with
the module name and the call that needs the marker.

A module counts as spawning a shell when it

- calls ``subprocess.*``/``os.*`` with ``shell=True``, or calls ``os.system``,
  ``os.exec*`` or ``os.spawn*``;
- calls ``subprocess.*`` with an argv whose program is a shell (``bash``,
  ``sh``, ``zsh``, with or without a path), a ``*.sh`` script, or an
  expression naming a hook (``HOOKS_DIR / name``, ``hook``); or
- imports helpers from another test module that counts.

Modules may also be marked for other platform reasons (locks, symlinks,
path limits); this guard only enforces the floor.
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


def _program_spawns_shell(expr: ast.expr) -> bool:
    if isinstance(expr, (ast.List, ast.Tuple)):
        elts = list(expr.elts)
        if not elts:
            return False
        first = elts[0]
        if (len(elts) > 1 and isinstance(first, ast.Constant)
                and str(first.value).rsplit("/", 1)[-1] == "env"):
            return _program_spawns_shell(elts[1])
        expr = first
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        program = expr.value.split()[0] if expr.value.split() else ""
        name = program.rsplit("/", 1)[-1]
        return name in SHELLS or name.endswith(".sh")
    if isinstance(expr, ast.Starred):
        return False
    return bool(HOOKISH.search(ast.unparse(expr)))


def shell_spawns(source: str) -> list[str]:
    """Return the source of each call in ``source`` that spawns a shell or hook."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name):
            continue
        attr = node.func.attr
        shell_kw = any(
            kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in node.keywords
        )
        if owner.id == "os" and (attr == "system" or attr.startswith(("exec", "spawn"))):
            hit = True
        elif owner.id == "subprocess" and attr in SUBPROCESS_CALLS:
            hit = shell_kw or bool(node.args and _program_spawns_shell(node.args[0]))
            if not hit:
                hit = any(kw.arg == "args" and _program_spawns_shell(kw.value) for kw in node.keywords)
        else:
            hit = False
        if hit:
            found.append(ast.unparse(node)[:120])
    return found


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
    """Map each module that must be marked to the reason it must be."""
    required = {}
    for name, source in sources.items():
        calls = shell_spawns(source)
        if calls:
            required[name] = f"spawns a shell or hook: {calls[0]}"
    changed = True
    while changed:
        changed = False
        for name, source in sources.items():
            if name in required:
                continue
            for imported in sorted(imported_test_modules(source)):
                if imported in required:
                    required[name] = f"imports helpers from {imported}, which spawns a shell or hook"
                    changed = True
                    break
    return required


def _suite_sources() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(HERE.glob("test_*.py"))}


def test_every_shell_spawning_module_is_marked_platform():
    sources = _suite_sources()
    required = required_modules(sources)
    # A detector that stopped matching would pass vacuously; the hook suites
    # below spawn Bash and the hook scripts directly.
    assert {"test_codex_compatibility", "test_hook_state_port", "test_antigravity_hooks"} <= set(required)
    missing = [
        f"  {name}.py: {reason}"
        for name, reason in sorted(required.items())
        if not is_marked_platform(sources[name])
    ]
    assert not missing, (
        "These test modules run Bash or a hook script but are not marked "
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
    ],
)
def test_detector_flags_shell_spawns(source):
    assert shell_spawns(source)


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess, sys\nsubprocess.run([sys.executable, str(SCRIPT), '--bash-cmd', cmd])",
        "import subprocess\nsubprocess.run(['git', 'init', '-q', str(root)])",
        "import subprocess\nsubprocess.run(['Rscript', str(script)])",
        "text = 'bash run.sh'\nassert 'mycelium-post-action.sh' in text",
    ],
)
def test_detector_ignores_non_shell_programs(source):
    assert not shell_spawns(source)


def test_importing_a_shell_spawning_module_requires_the_marker():
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


def test_shards_partition_the_suite():
    import conftest

    ids = [f"skills/core/scripts/test_x.py::test_{i}[case-{i % 7}]" for i in range(600)]
    for n in (1, 2, 3):
        shards = [{i for i in ids if conftest.shard_of(i, n) == k} for k in range(1, n + 1)]
        assert set().union(*shards) == set(ids)
        assert sum(len(s) for s in shards) == len(ids)
        assert all(len(s) > len(ids) / n * 0.8 for s in shards)


@pytest.mark.parametrize("value", ["0/2", "3/2", "a/b", "2"])
def test_bad_shard_values_are_usage_errors(value):
    import conftest

    with pytest.raises(pytest.UsageError):
        conftest.parse_shard(value)
