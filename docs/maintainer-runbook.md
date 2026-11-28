# Maintainer Runbook

Operational knowledge for developing, testing, and releasing Mycelium itself: the
things that are not visible in the code and were each learned the hard way. It
complements, and does not repeat:

- `docs/release-process.md`: what the release gate checks;
- `docs/cross-host-review-checklist.md`: the validation ladder and review checklist;
- `skills/develop/references/regression-patterns.md`: numbered defect patterns to
  audit for.

Read the section for the task at hand before starting it. When a run teaches
something new and durable, add it here in the same PR.

## 1. Local test environment

- The test ladder needs `pytest`, `pytest-xdist`, `pyyaml` and `pandas`. Some
  `test_antigravity_recovery.py` tests run a real analysis script and fail with
  `ModuleNotFoundError: pandas` without it; that is environmental, not a
  regression. The release gate's knowledge-map step also needs `numpy` and
  `scikit-learn` (or pass `--skip-knowledge-map` and say so in the release notes).
- If the system Python lacks these, make a throwaway venv outside the repository
  and put its `bin` first on `PATH`, so the shell-hook suites use it too. Invoke
  `tools/release_gate.py` with that interpreter. Never install into the repo.
- macOS ships bash 3.2 as `/bin/bash`. To force the hook suites onto it, put a
  directory containing `bash -> /bin/bash` first on `PATH`. Hook code must work
  on both 3.2 and current bash (regression pattern 43).
- macOS has no `timeout` binary.
- To test Stop with Git path bytes that APFS cannot create, stage the raw
  path with `git update-index -z --index-info` and commit it after
  SessionStart. Run Stop in a UTF-8 locale. The commit supplies the path
  even when the worktree cannot materialize it.

## 2. Live-testing a candidate on each host

Unit tests prove hook logic. Only a real host run proves that the host
discovered, trusted, and dispatched the hooks, and that agent-facing text works.
Live runs of skill text have repeatedly found bugs that several clean review
rounds missed, so for agent-facing instructions, prefer one live trace over
another review round. Natural, unscripted prompts find more than scripted
probes.

These recipes are for development checks. The release lifecycle audit
(`$mycelium:lifecycle-audit`) is separate and runs against the installed artifact.

### Claude Code

- **Hooks without installing.** The plugin's `hooks/hooks.json` is Codex-only
  (each command runs only when `PLUGIN_ROOT` is set). Claude's hooks come from
  `init_repo.py`, which writes `.claude/settings.local.json` hooks pointing at
  the hooks directory next to the `init_repo.py` that ran. So running the candidate
  worktree's `init_repo.py` into a scratch git repository makes `claude -p`
  there exercise the candidate hooks, with no version bump or install.
- **Skills without installing.** Skills come from the plugin, not the project.
  Freeze the candidate (`git worktree add --detach <scratch>/cand <sha>`) and run
  `claude -p ... --plugin-dir <scratch>/cand --settings
  '{"enabledPlugins":{"mycelium@mycelium":false}}'` so the installed copy cannot
  answer instead. Prove which copy ran from the trace (the agent reads files
  under `<scratch>/cand`).
- **An uncommitted candidate** can be tried the same way from a copy:
  `git ls-files -z | rsync -a --from0 --files-from=- . <scratch>/cand/`
  (after `git add -N` for new files; stage any deletions first, or rsync
  stops on the missing file).
- With `-p` and a narrow allow-list, the agent's shell cannot read or copy
  files from the plugin directory (the Read tool can). A failed
  `cp .../register_value.py analysis/_lib/` in such a run is the harness,
  not a Mycelium defect.
- Useful flags: `--output-format stream-json --verbose --disable-slash-commands
  < /dev/null`. Allow-list Bash narrowly; `Bash(bash run.sh)` does not cover
  `bash analysis/x/run.sh`.
- **Multi-turn.** For a per-turn Stop with no SessionStart in between, use
  `claude -p --input-format stream-json` with two user turns. `--resume` fires
  SessionStart and hides bugs that depend on a long-lived session.
- Pass `--worktree name` as two arguments. A quoted `"-w name"` makes the name
  ` name`, which the CLI rejects with exit 1 and an empty stream.
- Under the CLI, the hook process directory always equals the payload `cwd`,
  including in worktrees and after a Bash `cd`. The desktop app's "hooks run in
  the main checkout while the session works in a worktree" case cannot be
  reproduced from the CLI; only unit tests cover it.
- Isolate the knowledge layer: launch with `MYCELIUM_KNOWLEDGE_DIR=<scratch>/knowledge`
  (containing a fresh `.last-audit`), or SessionStart may direct the agent to
  audit the real `~/.mycelium/knowledge`.

### Codex

- **Installing a candidate, approving its hooks, and checking trust:** follow
  the Codex audit recipe in `skills/develop/SKILL.md`, the single source for it:
  a `git archive` export with a fresh `+codex.<timestamp>`, a temporary
  marketplace switch, hashes before and after each app launch, approval of only
  the `mycelium@mycelium` hooks in the desktop app, and the `codex app-server`
  `hooks/list` check. Never install from a working checkout: a local
  marketplace source copies uncommitted files.
- **Hook trust is a content hash** over every file in the installed bundle
  (`.git` included). Any change, including running `git status` inside the bundle
  (it rewrites `.git/index`), makes hooks `modified`, and they are then
  **silently skipped**. Never run git inside an installed bundle, never edit
  trust records by hand, and never use `--dangerously-bypass-hook-trust` in an
  audit.
- `codex exec -C <dir>` appends a `[projects."<dir>"] trust_level` block to
  `~/.codex/config.toml`. Back the file up first and remove the block afterwards.
- Initialize the test project from the **installed** bundle's `init_repo.py`,
  so its hooks point at what you are testing.
- Drive turns with `codex exec --json -s workspace-write '<prompt>'` and
  `codex exec resume --json <thread_id> '<reply>'`. For multi-turn hook checks use
  `codex app-server` (`thread/start`, then `turn/start` twice). Filter
  `turn/completed` by the main thread id, because a subagent's completion arrives
  first. Codex subagent hooks carry the parent `session_id`.
- Under `-s workspace-write`, tools that write outside the project fail.
  DVC and Snakemake both do (their caches); see `workflow-guide.md` for
  the settings. `codex sandbox -c 'sandbox_mode="workspace-write"' --
  <command>` reproduces such a failure without a model turn.
- `codex exec --json` shows only the `wait` item for subagents, not
  `spawn_agent`. Confirm subagent probes from `~/.codex/sessions/<date>/rollout-*.jsonl`.

### Antigravity

- Install with `tools/antigravity.py install --profile app` (or `ide`) into a
  disposable workspace outside system temp directories, then
  `tools/antigravity.py doctor --profile app --workspace <ws>`. Hash the
  installed observer files against the candidate commit.
- **A person has to drive the App.** Agents cannot operate its window. Write the
  prompt to a file next to the workspace and ask the maintainer to open the
  folder, start a new conversation, and paste it. Record the App version for
  every run: it auto-updates.
- **Permission modes test different things.** Use **Request Review** to test
  approvals (Default mode may not prompt at all, depending on the user's allow
  list). But commands run under Request Review carry no sandbox record in the
  App's step store, so their lineage stays unresolved by design; check lineage
  in a separate **Default**-mode run.
- **Diagnose before blaming dispatch.** Read the App's own step record with
  `antigravity_app_store.command(cid, step, cmdline)` or `.edit(...)` (a
  read-only snapshot). It names the exact field or argument that failed, such
  as a model sending `run_command` with an unexpected `IsDaemon` key or a
  missing sandbox sub-message. Run at least two App conversations: models vary
  their tool arguments.
- **The `agy` CLI cannot stand in for the App.** `agy -p` runs commands,
  including `rm`, without approval prompts, and the CLI (data root
  `~/.gemini/antigravity-cli`) is not a supported host profile.

## 3. Reviews

- Run an independent fresh-context reviewer alongside Codex for hook changes.
  Codex-clean has missed real regressions.
- `codex review --base <ref>` takes no custom prompt. For a targeted pass, run
  `codex exec -s read-only "<instructions; run git diff origin/main...HEAD>" <
  /dev/null` next to `codex review -c sandbox_mode='"read-only"' --base
  origin/main < /dev/null`. Use `origin/main`: a local `main` is often stale.
- Reviews of trust and emulation code find progressively narrower issues forever.
  Stop with "P1 findings reachable with realistic inputs", and state the design
  contract in the prompt (for example "refusing exotic input is intended").
- When rounds stop converging, change the design rather than adding another
  heuristic: an exact-grammar reader that refuses everything else, an allowlist
  instead of a blocklist, positive proof instead of gap-by-default, warn-only
  instead of blocking.
- When rewriting a test (for example to remove flakiness), run a mutation check:
  reintroduce the original bug and confirm the new test still fails.
- **Codex on GitHub** (`chatgpt-codex-connector[bot]`) reviews on PR open, on
  draft-ready, or on an `@codex review` comment, usually within 2–6 minutes.
  When polling, a 👀 reaction only means "reviewing". "Done" is a new review or
  new inline comments, a 👍 on the trigger comment, a "Didn't find any major
  issues" comment for the head SHA, or the in-place summary comment showing
  Completed **with the new head SHA**.

## 4. Debugging recipes

- **Lock races.** CPU spinners do not reproduce directory-lock races (the retry
  budget is attempt-counted). Extract `git archive HEAD` to a scratch copy, add
  trace probes on every failure, reap, and release path of the copy's hook lib,
  and run many separate `pytest <nodeid>` processes in parallel (xdist dedups
  identical node IDs). Probes must fire only on the failing branch.
- **Replacing Python with bash in a hook.** Keep a frozen copy of the old code as
  a test oracle and fuzz both across bash 3.2 and current bash (regression
  pattern 43 lists the traps).
- **Silent lineage gaps.** Replay real tool calls from a transcript
  (`~/.claude/projects/<proj>/**/*.jsonl`, or `zstd -dc` Codex rollouts) against
  the historical commit's hooks in a throwaway clone; `tools/lineage_replay.py`
  automates the detector side.
- **Dirty files nobody remembers.** Agents dogfooding Mycelium in other projects
  have patched a source checkout in place. Search both
  `~/.claude/projects/*/*.jsonl` and `~/.codex/sessions/**/*.jsonl` for
  distinctive strings from the diff. Treat such changes as unreviewed.
- **Migration smoke tests** (`migrate_existing_repos.py --repo <tmp>`) update
  every `~/.claude/projects/*/memory/MEMORY.md` unless you pass `--skip-memory`.
  Always pass it for disposable repos, and dry-run first.
- **What Claude Code tells hooks about a refused call.** A real `claude -p`
  run with a narrow allow-list (2.1.332) shows `PreToolUse`, then nothing for
  that call: no Post, no `PostToolUseFailure`, no `PermissionDenied`, which
  fires only for auto-mode classifier denials. A refused ask also fires
  `PermissionRequest`, but with no `tool_use_id`. Only the transcript
  (`transcript_path`) records the outcome. The refusal is a `type: user` line
  with one `is_error` tool_result for the id and `permissionDecision.decision:
  "reject"`. A call that ran is recorded with `accept`, even when it failed.
  To see this, register a logging hook for every event in a scratch repo's
  `.claude/settings.local.json`. Pass `--permission-mode default` too: a
  session otherwise inherits the user's default mode, such as `auto`, and the
  call may simply run.
- **Worktree guards.** Some agent setups refuse shell commands that run `git`
  against another directory, or heredocs that mention git. Put the steps in a
  scratch script and run that.

## 5. Merging

- `main` requires an approving review and is squash-merged; agents cannot
  approve their own PRs, so the maintainer merges.
- **Stacked PRs.** Merging branch N into N+1 is not enough with squash merges.
  After each squash merge, merge `origin/main` into the next branch and
  propagate up the stack. Simulate `git merge --squash` of each PR onto a scratch
  copy of `main` before handing over a merge order.
- Merges happen while agents work. Before amending or force-pushing a PR
  branch, check `gh pr view <n> --json state`; if it merged, put the follow-up on
  a new branch from `origin/main`.
- A PR branched before a version bump silently files its `[Unreleased]`
  CHANGELOG entry under the newly dated version when merged. Trial-merge with
  `main` and check the entry's section before calling it ready.

## 6. Releasing

Follow `docs/release-process.md`. Lessons from past releases:

- **Before bumping**, check that the previous version's tag exists
  (`git ls-remote --tags origin`). A bump merged without its tag lets later PRs
  land changelog entries under an already-dated version.
- **Never reuse a version number after content changes.** The Claude Code plugin
  cache is keyed on the version string: `claude plugin marketplace update` does
  not refresh content, so an unchanged version keeps serving a stale artifact
  while reporting the right label. Hash the installed tree against the source
  (the gate's `--installed-root` check). Refresh with `claude plugin marketplace
  update` then `claude plugin update mycelium@mycelium --scope user`, or remove
  the version directory and reinstall.
- **Run the gate from a fresh clone** of the merged bump commit, not a working
  checkout. Delete any `.pytest_cache` left by earlier test runs in that clone.
  The gate is fail-fast and a flake costs a full rerun: check machine load
  (`uptime`) before starting, since subprocess timeouts under heavy load have
  failed it.
- **Tag explicitly by SHA**, annotated, from the clean clone:
  `git tag -a mycelium--vX.Y.Z -m "Mycelium X.Y.Z" <sha>` after `git fetch`.
  A bare `git tag` tags whatever the local checkout happens to be on. Tags are
  prefixed `mycelium--`, not `v`.
- **Publish a GitHub release for every version** right after tagging. Body: that
  version's `## [X.Y.Z]` CHANGELOG section from the tagged tree, plus a short
  "Release verification" list. `gh release create mycelium--vX.Y.Z --verify-tag
  --latest --title "mycelium X.Y.Z" --notes-file <body>`. Check with
  `gh release list` (`gh release view --json isLatest` is not a valid field).
- **macOS CI.** Jobs that sit queued about 15 minutes for a `macos-latest` runner
  are cancelled with no steps run: a runner shortage, not a test failure. Rerun
  them. macOS also has a 1024-byte `PATH_MAX` that Linux tests never hit.
- **Flaky tests** have fallen into four patterns: wall-clock bounds,
  lock-contention assumptions, clock-second races, and APFS filename limits.
  Fix the pattern, then mutation-check the rewritten test.
