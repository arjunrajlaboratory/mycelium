# Cross-Host Compatibility Review Checklist

Use this checklist for changes that can affect Mycelium on Claude Code or
Codex, especially plugin packaging, shared skills, lifecycle hooks,
initialization, migration, session accounting, and data lineage.

The goal is to review the exact release candidate as a system. Passing unit
tests for an individual script is not sufficient when a change crosses host,
hook, filesystem, or lifecycle boundaries.

## How to Use This Checklist

1. Record the exact base commit and head commit being reviewed.
2. Review the complete `base...head` diff, including generated or packaged
   files, rather than only the most recent commit.
3. Mark every item **Pass**, **Fail**, or **N/A**, with a test, command, or code
   reference as evidence.
4. Generalize each defect into an error pattern and search the whole branch for
   other instances.
5. Add a regression test for every fixed defect. Add a checklist item when the
   defect reveals a review category that was previously missing.
6. Re-run the full gate on the final commit after all fixes. Do not rely on CI
   or a review performed against an earlier commit.

## 1. Review Scope and Branch State

- [ ] Record the base commit, head commit, branch, and pull request.
- [ ] Confirm the working tree is clean or account for every local change.
- [ ] Review the complete diff from the merge base to the exact head.
- [ ] Inspect added, deleted, renamed, executable, and symlinked files.
- [ ] Separate current findings from comments made against older commits.
- [ ] Search for every instance of each defect pattern, not only the line that
      exposed it.
- [ ] Confirm that test and documentation changes describe the implemented
      behavior rather than the intended behavior.

## 2. Plugin Packaging and Discovery

### Codex

- [ ] `.codex-plugin/plugin.json` parses and contains the expected identity,
      skill path, interface metadata, and release version.
- [ ] The Codex and Claude plugin manifests use the same package name, version,
      and shared skill directory.
- [ ] Every `skills/*/SKILL.md` has valid `name` and `description` frontmatter,
      and the name matches its directory.
- [ ] Every shared skill has valid `agents/openai.yaml` metadata.
- [ ] `hooks/hooks.json` parses and remains the plugin-level source of Codex
      lifecycle registration.
- [ ] The Codex hook configuration contains the intended five command
      registrations: SessionStart, two Bash PostToolUse handlers, one
      `apply_patch` PostToolUse handler, and Stop.
- [ ] Hook commands dispatch through `${PLUGIN_ROOT}` and do not contain a
      versioned cache path, a developer-machine path, or a repository-local
      installation assumption.
- [ ] The dispatcher resolves only packaged hook scripts and rejects traversal,
      unexpected names, and unsafe roots.
- [ ] A globally dispatched hook performs no project write before the shared
      `.living`/`.mycelium` containment and symlink checks have succeeded.
- [ ] Installation works from the packaged plugin, not only from a source
      checkout.

### Claude Code

- [ ] `.claude-plugin/plugin.json` and marketplace metadata still parse and
      expose the shared skills as expected.
- [ ] Existing Claude commands and skill discovery remain unchanged unless the
      release explicitly documents a breaking change.
- [ ] Repository-local Claude hook registrations retain all required lifecycle
      handlers and do not acquire Codex-only assumptions.
- [ ] Standalone or obsolete lineage Stop handlers are not reintroduced.
- [ ] Custom user instructions, hooks, and existing `.living` content survive
      initialization and migration.

## 3. Installation, Upgrade, and Migration

- [ ] A fresh repository initializes successfully for each supported host.
- [ ] Re-running initialization is idempotent.
- [ ] Dry-run migration performs no writes.
- [ ] Actual migration is idempotent and validates successfully afterward.
- [ ] A migration action reported as skipped does not rewrite byte-identical
      managed configuration or churn its timestamp; deliberate data refreshes
      such as INDEX regeneration are identified separately.
- [ ] Initialization and migration preflight every repository-controlled output
      they may mutate—including guidance, hook configuration, todo, index, and
      runtime state—before the first write, so a later rejection cannot leave a
      partial migration.
- [ ] Inputs that can be copied or summarized into project or global state (for
      example legacy session context, living-layer entries, legacy global
      knowledge, and provider MEMORY files) reject symlinks in the file itself
      and in every ancestor before any read or write.
- [ ] Global initialization and migration preflight every source and destination
      before the first mutation; command-line path normalization preserves
      symlink evidence instead of resolving it away before validation.
- [ ] Legacy Claude-only repositories continue to work without mandatory
      migration unless a migration is explicitly documented.
- [ ] Early Codex installations with repository-local `.codex/hooks.json` are
      migrated or preserved safely.
- [ ] Mixed user and Mycelium hook configurations preserve unrelated entries.
- [ ] Malformed, partial, and interrupted configurations fail safely without
      truncating user files.
- [ ] Stale versioned plugin paths and obsolete hook registrations are removed
      only when they can be identified unambiguously.
- [ ] Legacy runtime state is migrated, ignored, or cleaned deliberately; it is
      never mistaken for a live current session.
- [ ] The documented install, update, trust, restart, and migration instructions
      match the current Claude Code and Codex interfaces.

## 4. Lifecycle State Machine

- [ ] SessionStart handles fresh, resumed, cleared, compacted, and stale-crash
      sessions.
- [ ] Session identifiers and log names are unique under rapid or concurrent
      starts.
- [ ] The active-log marker has one documented format, and every reader parses
      that format rather than treating the whole file as a path.
- [ ] Every active-log reader validates the marker path as a regular file under
      `.living/log`, validates the owner timestamp, and never emits, follows, or
      trusts a corrupt marker supplied by repository state.
- [ ] A no-work session exits without creating misleading activity or lineage.
- [ ] A lineage-only session reserves and uses a consistent session identifier.
- [ ] The first blocked Stop preserves the active log, baseline, raw events, and
      enforcement state needed by the continuation.
- [ ] A failed registry, lineage, or context write followed by a resumed or
      compacted SessionStart preserves the same unfinalized log, session ID, and
      baselines so Stop can retry the original transaction.
- [ ] Work performed after a blocked Stop appears in the eventual final log and
      lineage output.
- [ ] Only an accepted Stop finalizes the log and registry entry and removes or
      archives active state.
- [ ] Stop continuation messages identify the unmet requirement precisely and
      do not enter an infinite retry loop.
- [ ] Cleanup and retention policies distinguish active, accepted, stale, and
      corrupt state.

## 5. Execution and Working-Directory Inference

- [ ] Direct Python, R, and Jupyter execution is detected.
- [ ] Absolute, versioned, virtual-environment, and PATH-resolved interpreter
      names are detected, including quoted or backslash-escaped paths with
      whitespace.
- [ ] Interpreter flags, `-m` modules, and inline execution forms are handled
      according to the documented policy.
- [ ] Common environment/package wrappers such as `uv`, `conda`, `poetry`, and
      `/usr/bin/env`, plus execution wrappers such as `time`, `exec`, `nice`,
      and `timeout`, are detected (including nested forms) without confusing
      wrapper arguments or option values for executed scripts.
- [ ] Wrapper options that rewrite argv, such as `env -S`/`--split-string`, are
      either modeled completely or rejected conservatively; a later
      interpreter-looking argument is never treated as the executed program.
- [ ] Wrapper options that change cwd, including `env -C`/`--chdir`,
      `conda run --cwd`, and `uv run --directory`, are applied in nesting order
      before script, input, and output paths are resolved.
- [ ] AND, OR, pipelines, subshells, heredocs, comments, and quoted text are
      interpreted conservatively, with tests for both false positives and false
      negatives.
- [ ] Multiline Python/R control words and heredoc-like text inside quoted
      arguments are not mistaken for shell structure; tooling exclusions are
      based on the executed program/module/script rather than substrings in
      ordinary quoted or unquoted arguments.
- [ ] Exit evidence is associated with the command that actually executed.
- [ ] Failed commands that are provably reached through a known-success AND
      prefix, or started as a pipeline component, still produce lineage; shell
      operators appearing only inside an unquoted comment produce nothing.
- [ ] Nested and changed working directories resolve script and output paths
      correctly.
- [ ] Failed or conditional `cd` commands do not change the inferred directory.
- [ ] Symlink aliases, quoted and backslash-escaped whitespace, shell
      metacharacters inside quotes, and non-ASCII path components do not
      silently drop valid script or notebook activity.
- [ ] Adjacent quoted, unquoted, and backslash-escaped components that form one
      shell word (for example `"analysis script".py` or
      `'analysis'\ script.py`) are decoded as one interpreter, flag value, or
      script path rather than silently dropped.
- [ ] Interpreter and script regexes require a complete shell-word boundary;
      suffixes such as `"analysis.py".bak`, `"a.R".bak`, and
      `"a.ipynb".bak` cannot be attributed to the shorter quoted filename,
      while adjacent shell redirections remain valid word boundaries.
- [ ] Inferred paths are canonicalized and proven to remain within the project
      before they are trusted or written.
- [ ] `apply_patch` activity is recorded only after a successful tool result,
      and relative paths are normalized against the actual repository root.

## 6. Git and Session Accounting

- [ ] The session baseline accounts for pre-existing tracked, dirty, staged,
      untracked, and deleted files.
- [ ] New, modified, deleted, renamed, and committed files are attributed to the
      session correctly.
- [ ] Filenames containing spaces, tabs, Unicode, and leading punctuation are
      handled without line-oriented parsing errors.
- [ ] Commits, amendments, rebases, checkouts to older commits, and branch
      switches do not hide session work.
- [ ] A temporary branch that returns to the original tree is accounted for
      according to the documented policy.
- [ ] Reflog absence, pruning, or an unborn HEAD fails conservatively.
- [ ] Exclusions for `.living`, runtime state, and generated metadata are
      intentional and do not suppress real scientific or source changes.
- [ ] The final file list is deduplicated without losing the strongest evidence
      for a change.
- [ ] Pre-existing dirty and untracked files use a content identity, so a
      same-size rewrite whose mtime is restored still counts as session work.

## 7. Data Lineage Integrity

- [ ] Exactly one lineage manifest is produced for an accepted session that
      executed tracked analysis.
- [ ] Raw events accumulate across a blocked Stop and are not discarded before
      successful extraction.
- [ ] Script hashes represent execution-time content or clearly disclose when
      only final-state content is available.
- [ ] Missing or unresolved scripts remain visible with warnings rather than
      being silently omitted.
- [ ] Event append, extraction, manifest writing, and status-sentinel updates
      are locked or atomic as appropriate.
- [ ] Concurrent events and Stop attempts do not lose, duplicate, or split a
      session's lineage.
- [ ] Accepted cleanup archives or rotates raw events only after the manifest
      and session log have been written successfully.
- [ ] Extractor failure blocks Stop visibly and preserves the active marker,
      baseline, session ID, and raw events for a deterministic retry.

## 8. Concurrency, Atomicity, and Filesystem Safety

- [ ] The entire Stop transaction is serialized, including decision, final log,
      registry update, lineage extraction, and cleanup.
- [ ] Concurrent Stop attempts produce exactly one finalization, one registry
      row, and one lineage archive.
- [ ] Concurrent SessionStart and PostToolUse writes cannot truncate or
      interleave state.
- [ ] Log, registry, manifest, marker, and sentinel replacements are atomic.
- [ ] A log's acceptance marker, duration/file-count frontmatter, and matching
      completion footer are published as one atomic replacement; an injected
      write/replace failure leaves the original unfinalized log and active
      retry markers intact.
- [ ] Lock acquisition has bounded failure behavior and stale-lock handling.
- [ ] `.mycelium`, `.living`, marker files, and output parents cannot redirect
      writes outside the project through symlinks.
- [ ] Machine-local pointer refreshes reject existing links and use atomic
      replacement instead of truncating a repository-controlled path.
- [ ] Atomic replacement preserves stricter existing file permissions instead
      of resetting every updated file to a permissive default mode.
- [ ] Repository containment checks use canonical paths and reject traversal,
      prefix collisions, and nonexistent-parent tricks.
- [ ] Cleanup cannot delete a path outside the project, even when state files
      are corrupt or attacker-controlled.
- [ ] Same-second writes and coarse filesystem timestamps cannot cause false
      enforcement failures.
- [ ] Updating an existing finding or learning is detected; directory mtime is
      not used as the sole evidence of content change.
- [ ] A Bash-only file mutation triggers the same lifecycle enforcement as an
      equivalent editor or `apply_patch` mutation.

## 9. Portability and Failure Handling

- [ ] Shell scripts pass `bash -n` and run under the project's minimum supported
      Bash version.
- [ ] `stat`, `date`, `sed`, `mktemp`, hashing, and locking behavior is tested on
      both macOS and Linux where it differs.
- [ ] Python availability and version requirements produce actionable errors;
      hooks do not assume a `python` alias exists when only `python3` is present.
- [ ] Missing helpers, missing optional dependencies, invalid JSON, malformed
      timestamps, and partially written state fail safely.
- [ ] Malformed reminder, audit, owner, and session-start timestamps cannot
      abort SessionStart or trigger the subagent Stop bypass.
- [ ] Paths with spaces and non-ASCII characters work in shell, Python, JSON,
      and Markdown output.
- [ ] Hook failures do not silently erase evidence or leave the repository in a
      state that appears successfully finalized.
- [ ] Registry values derived from filenames, branches, commits, or frontmatter
      cannot break the Markdown table; a rejected registry upsert blocks Stop
      and an eventual retry does not duplicate the session-end footer.
- [ ] Security-sensitive failures prefer a visible safe refusal over an
      out-of-project write or destructive cleanup.

## 10. Documentation and Release Consistency

- [ ] README install and update instructions cover both Claude Code and Codex.
- [ ] CLI-only instructions are labeled separately from Codex app behavior.
- [ ] Hook names, counts, paths, and lifecycle descriptions match the shipped
      manifests and scripts.
- [ ] Plugin versions, changelog entries, marketplace metadata, and examples
      agree.
- [ ] The backward-compatibility and upgrade path is explicit, including whether
      existing repositories need to run a migration or reapprove hooks.
- [ ] Architecture and troubleshooting documentation contains no stale command
      names, paths, or assumptions from a previous host integration.

## Required Adversarial Regression Matrix

These cases are mandatory because ordinary happy-path tests are unlikely to
exercise them.

| Case | Expected result |
| --- | --- |
| `.mycelium` or `.living` is a symlink outside the project | The hook refuses the unsafe state and writes nothing outside the project. |
| Session work updates `.living` within the same timestamp second | A valid update is accepted. |
| Session work updates an existing finding file | The content change is detected even if the parent directory mtime is unchanged. |
| `apply_patch` fails | No successful activity is recorded for the failed patch. |
| Tool cwd uses a symlinked or nested path | Valid in-project changes are attributed to the canonical project root. |
| Bash creates or modifies a project file without `apply_patch` | Stop enforces the living-repository update requirement. |
| First Stop is blocked, more work occurs, then Stop succeeds | State remains active after the block, and the final log includes the later work. |
| Multiple Stop hooks run concurrently | Exactly one finalization, registry row, lineage extraction, and cleanup occurs. |
| Python runs as `python3 -u`, `python3 -W`, an absolute or venv interpreter, `/usr/bin/env python3`, or `python -m` | Execution reminders and lineage events are emitted according to policy. |
| Project path contains spaces or Unicode | Hooks, state, logs, and lineage remain correct. |
| Runtime state is malformed or partially written | The hook fails visibly without destructive cleanup or out-of-project writes. |
| Active-log marker points outside `.living/log` | PostToolUse omits the unsafe log directive; Stop still enforces outstanding work and never reveals or follows the path. |
| A dirty or untracked file is rewritten with the same size and restored mtime | Session accounting detects the content change. |
| `true && python failed.py` exits nonzero, or Python is a pipeline component | The provably executed analysis is recorded; an unknown failed AND prefix remains omitted. |
| Shell operators and Python text occur only after an unquoted `#` comment marker | No reminder or lineage event is created; quoted and escaped hashes remain valid arguments. |
| Registry summary contains `|`, or the registry/lineage helper fails | Table cells remain valid; helper failure blocks Stop and preserves retry state without duplicate finalization. |
| Registry finalization fails, then SessionStart resumes or compacts before Stop retries | The same unfinalized log and session ID remain active; the retry produces one final log and registry row. |
| Interpreter or script paths concatenate quoted, bare, and escaped shell-word components | Python, R, and Jupyter reminders and lineage use the single decoded argv value. |
| A quoted interpreter/script suffix is followed by more characters in the same shell word, such as `python "a.py".bak` | The longer argv value is evaluated as a whole; no event is attributed to `a.py`. |
| `env -S 'echo prefix' python a.py` or another argv-rewriting wrapper precedes an interpreter-looking argument | The parser rejects the ambiguous execution unless it fully models the wrapper expansion. |
| `env -C sub`, `conda run --cwd sub`, or `uv run --directory sub` wraps an analysis command | Scripts and data paths resolve relative to the wrapper-selected directory; missing or dynamic directories are rejected. |
| `time`, `exec`, `nice`, and `timeout` wrap or nest around an interpreter | Real executions are captured, while malformed options and an unrelated wrapped command containing interpreter text are rejected. |
| Atomic session-log replacement fails after registry/context preparation | Stop blocks, frontmatter remains unaccepted with no footer, active state survives resume/compact, and one retry produces one footer. |
| Fresh initialization encounters an unsafe later managed target | Preflight rejects it before creating any Mycelium directory or file. |
| Integration stress suite runs in CI | CI invokes `skills/core/tests/test_integration_stress.sh` and fails if it fails. |

## Validation Gate

Adapt individual commands when the repository layout changes, but preserve the
coverage represented by every line.

```bash
python3 -m pytest -q skills/core/scripts --ignore=skills/core/scripts/knowledge_map
python3 -m pytest -q skills/core/scripts/knowledge_map
bash skills/core/hooks/test_stop_hook.sh
bash skills/core/hooks/test_hooks_stress.sh
bash skills/core/tests/test_integration_stress.sh
find hooks skills -type f -name '*.sh' -exec bash -n {} +
python3 -m compileall -q skills/core/scripts
python3 -m json.tool .claude-plugin/plugin.json >/dev/null
python3 -m json.tool .codex-plugin/plugin.json >/dev/null
python3 -m json.tool hooks/hooks.json >/dev/null
git diff --check
```

Also perform:

- [ ] A fresh initialization and structure-validation smoke test.
- [ ] Dry-run and actual migrations from representative legacy Claude and Codex
      repositories, followed by validation and a second idempotency run.
- [ ] A packaged-plugin smoke test in a disposable real repository on both
      supported hosts.
- [ ] A lifecycle smoke test that lets hooks dispatch naturally; do not invoke
      the hook scripts manually as a substitute.
- [ ] CI verification against the exact final head commit.
- [ ] Codex review against the exact final head, with no unresolved P1 or P2
      findings.

## Merge Gate

A cross-host lifecycle change is ready to merge only when:

- [ ] Every applicable checklist item has evidence and no unexplained failure.
- [ ] All required regression and stress tests pass locally and in CI.
- [ ] Fresh install, migration, and natural-dispatch smoke tests pass.
- [ ] The exact final commit has been reviewed, not merely an earlier revision.
- [ ] There are no unresolved P1 or P2 findings.
- [ ] Any remaining lower-priority risk is recorded and explicitly accepted.
