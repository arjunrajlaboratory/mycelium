# Antigravity App audit profile

Use the installed Antigravity App, its selected model and native tools, in its
Default sandbox. The App is a separate target from the Antigravity IDE and CLI;
an IDE, Claude Code or Codex audit does not certify it. Follow the IDE profile
(`antigravity-ide.md`) for preparation, freezing and evidence retention,
with the App differences below.

## Install

Install with `tools/antigravity.py install --profile app --destination
/absolute/root/.agents/plugins/mycelium --python /absolute/python3` and check it
with `doctor --profile app --destination ... --workspace /absolute/root`. The
App package is workspace-local and separately owned (`mycelium-antigravity-app`).

## Native tools and evidence

In `child-task-prompt.md`, use native `read_file` (`Path`) for reads,
`run_command` for commands, and `write_file` (`Path`, `Content`, `Overwrite`)
or `edit_file` (`Path`, `Replacement`, `Target`) for `{EDIT_TOOL}`. Do not request `BypassSandbox`, a terminal-persistent
command, or a different working directory outside the opted-in project.

The adapter credits a command only from the App's own step record for that
exact conversation and step: a built-in `run_command` whose typed result and
terminal block agree on command, canonical working directory inside the
project, sandboxed execution and explicit exit status. A nonzero exit is a
real exit. Rendered output and stdout are never evidence. An asynchronous
command is pending until the same native call records its result; any other
or unavailable evidence is unresolved. Unresolved commands never hold Stop:
they are logged as unverified provenance (`unverified_provenance` in the
state), and so is a command still pending at the end of the bounded Stop
allowance. Only successful built-in edits count: `write_file` as a typed
creation or overwrite, `edit_file` as a typed edit, at their exact canonical
path. A delivered edit callback must also match the hook path. At a genuine
natural Stop of the active request, Mycelium also scans that request's own
native steps (after the explicit user step, up to the Stop boundary) in one
bounded read-only snapshot and credits any verified edit whose callback was
missed, using the same per-step activity action. Each project receives one
shared activity-tracker call with the deduplicated eligible paths; its steps
become `done` only after the tracker acknowledges that exact batch. A timeout,
error or missing acknowledgement leaves them `running` (never replayed), and
the Stop is held as unresolved. `edit_recovery` in the state
records the scan: `complete`, `truncated` (a row, byte, work or time bound was
hit), `unavailable` (store unreadable, no credit) or `deferred` (no time left
before finalization; Stop blocks and retries). Only `complete`, with the edit
steps present as real `<step>:mycelium-activity-tracker.sh` actions, is
verified complete recovery; `credited` lists only actions that recovery
started itself.

After natural Stop, inspect `.mycelium/antigravity-app/<conversation>/native-state.json`
and `.mycelium/run/antigravity-app/<conversation>-turn-<step>/accepted-stop.json`.
Require one accepted transaction, empty `unresolved_commands`,
`unverified_provenance` and `app_pending` (a nonempty one is a failed probe
even though Stop accepted),
one `app_bindings` entry per credited command, matching lineage events and
exit codes, and the negative controls. Record the exact App version: the step
store shape is pinned to App 2.31.0, and a different version needs a fresh
native verification before any support claim.

## PreToolUse observer: approvals unchanged, writes observed

The package registers an observation-only `PreToolUse` hook for `run_command`
and the edit tools (see `docs/antigravity-ide.md`). The host's documentation
does not say what a failed, slow or silent hook does to approvals, so every
new App version, and every change to `mycelium-antigravity-pre.sh` or
`antigravity_hooks.pre_tool_use`, needs a live check by a person driving the App:

- A command that normally prompts still prompts, with the same choices.
- A command allowed with "Always Allow" before still runs without a prompt.
- A deny setting still denies.
- An edit behaves as before.
- With `crash`, then `hang`, in `<project>/.mycelium/antigravity-pre-fault`,
  a prompting command still prompts and is not auto-approved. Delete the file
  afterwards.

If any of these differs from the same App without the package, the hook is
changing approvals. Treat that as a release blocker, not an observation gap.

For the lineage side, require the run's synchronous command to leave an
observation `antigravity-app:ag-step-<step>` with status `no-known-overlap`
in `.mycelium/io/obs/antigravity-app/<conversation>-turn-<step>.ndjson`,
and `lineage_index.py query --path <output> --json` to report
`current-in-record` with `attribution: sole`. Check also that no
`.mycelium/io/open/` marker remains after the accepted Stop.
