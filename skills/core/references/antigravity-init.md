# Initialize Mycelium in Antigravity IDE or App

Use this sequence when the user has requested initialization in a Git project
that does not yet contain `.living/`. Existing project files can stay in place;
initialization does not require restructuring them. If `.living/` already
exists, follow the core skill's maintenance or migration instructions instead
of rerunning initialization or deleting the living layer.

## Resolve the three paths

Use the native workspace path and the installed core skill's location:

- `MYCELIUM_WORKSPACE`: the absolute Git root of the user's intended project.
  Confirm the native command's working directory and Git root once. If they
  do not identify the intended project, resolve that mismatch before writing.
- `MYCELIUM_PLUGIN_ROOT`: the installed package root, three parent directories
  above `skills/core/SKILL.md`. Use the package that supplied this skill; it may
  be workspace-local or global. Do not search another checkout for scripts.
- `MYCELIUM_PYTHON`: the `python` value in that package's
  `.mycelium-install.json`. Keep the selected launcher's spelling, including a
  virtual environment. A different shell `python3` may load different packages.

Read the project's relevant existing guidance. Set these three shell variables
to the resolved absolute paths in a native `run_command` call, then run the
following sequence in that same call. It stops on the first failed step.

```bash
set -e
: "${MYCELIUM_WORKSPACE:?Set the absolute intended Git root}"
: "${MYCELIUM_PLUGIN_ROOT:?Set the installed Mycelium package root}"
: "${MYCELIUM_PYTHON:?Use python from the installed ownership manifest}"
"$MYCELIUM_PYTHON" "$MYCELIUM_PLUGIN_ROOT/skills/core/scripts/init_repo.py" \
  --target-dir "$MYCELIUM_WORKSPACE"
"$MYCELIUM_PYTHON" "$MYCELIUM_PLUGIN_ROOT/skills/core/scripts/generate_index.py" \
  --living-dir "$MYCELIUM_WORKSPACE/.living" --summary-heuristic
"$MYCELIUM_PYTHON" "$MYCELIUM_PLUGIN_ROOT/skills/core/scripts/validate_structure.py" \
  --target-dir "$MYCELIUM_WORKSPACE"
```

The initializer creates the standard directories and manifests, living layer,
portable guidance, core convention packs, skillpacks directory and existing-host
compatibility hooks. Do not manually recreate those outputs or reinstall the
same packs. The native Antigravity IDE or App plugin already supplies its hooks;
initialization does not need another entry in the user's `.agents/hooks.json`.
Preserve unrelated `.agents/hooks.json` entries, `.vscode/settings.json`, and
user-authored guidance. Inspect a relevant error if a command fails; routine
setup does not require reading the hook implementation or its test suite.

## Finish the requested setup

Check the command results and initialized files before reporting success. Record
any convention the user requested, generate the knowledge index again if that
changes its inputs, and complete the normal session protocol if a native
transaction started. The core skill's global knowledge bootstrap applies if
needed; domain choices and optional external skillpack clones follow their
normal authorization and can be listed as deferred choices after base setup.
Do not invoke external coding agents to generate the index or initialize the
project. An offline setup pass does not prove native skill selection, automatic
hook dispatch or session completion.
