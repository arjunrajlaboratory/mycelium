# Mycelium in Antigravity IDE

This development preview packages base Mycelium for Antigravity IDE. It includes
all eleven upstream skills, their references and scripts, convention packs, and
native lifecycle hooks. The adapter uses the current IDE model and tools.
It has no provider SDK, API-key requirement, external coding-agent dependency,
or personal coordination service.

## Install and initialize

Use Python 3.11 or newer from a source checkout containing `tools/antigravity.py`.
The installer pins that interpreter's resolved path into the native hook
commands so the IDE and terminal use the same Python installation. A selected
virtual-environment launcher is preserved, including its `sys.prefix`, packages,
and the `python3` used by child hooks; validating its symlink destination does
not replace the selected launcher. The shared hooks call plain `python3`, so the
package contains a generated `bin/python3` shim that runs the selected launcher,
and the adapter puts it first on the hooks' `PATH`. Any `--python` spelling
works, including `python3.12`, even when a different `python3` sits beside it.

```bash
# Preview, install globally, then verify every installed file.
python3 tools/antigravity.py install --dry-run
python3 tools/antigravity.py install
python3 tools/antigravity.py doctor
```

The default destination is `~/.gemini/config/plugins/mycelium`. To install only
for one workspace, supply its absolute path instead:

```bash
python3 tools/antigravity.py install \
  --destination /absolute/project/.agents/plugins/mycelium
```

Use one installation per IDE workspace. Start a new conversation after changing
the package. In a Git repository, ask the IDE agent to use the Mycelium core
skill to initialize the project, or to migrate an existing Mycelium project.
Existing `.living/` content remains authoritative. Lifecycle hooks activate
only for Git roots containing `.living/`; ordinary folders receive no Mycelium
state. Initialization and feature-specific work still require their normal
Python, R, notebook, rendering, or scientific-library dependencies.

After initialization, check workspace readiness separately:

```bash
python3 tools/antigravity.py doctor --workspace /absolute/project
# For a workspace-local package, use its destination for both checks:
python3 tools/antigravity.py doctor \
  --destination /absolute/project/.agents/plugins/mycelium \
  --workspace /absolute/project
```

A fresh Git project without `.living/` is not ready yet, even when its package
passes the earlier package-only check. Initialization uses
`skills/core/scripts/init_repo.py --target-dir /absolute/project` from the
installed package; the native plugin already provides its IDE hooks.
The core skill's [native initialization sequence](../skills/core/references/antigravity-init.md)
uses the installed interpreter, creates the scaffold once, builds its index,
and validates the result in one native command. Existing Git files need not be
moved to add Mycelium; restructuring remains a separate migration operation.

The package preserves the shared skill layout. Its generated skill guidance
maps shell, file-reading, and editing examples to native IDE tools. When a
general coding subagent is unavailable, carry out each required scientific
review axis in the current model. A browser subagent cannot perform a general
coding or filesystem audit.

For repository files, including `.living` and `.mycelium`, omit
`ArtifactMetadata` from native write/edit calls. Supplying that field asks the
IDE to treat the file as an artifact and can reject an ordinary workspace path.

## Update, inspect, or remove

Run `install` again from the desired source revision. An identical package is
left in place; a changed owned package is replaced atomically. Backups and
temporary staging live beside the plugin discovery directory, under
`.mycelium-backups` and `.mycelium-staging`. A locally edited, linked, or unowned
destination is preserved and reported for reconciliation.

The ownership manifest now uses schema 2 and records file permissions as well
as hashes. `doctor` fails on permission drift and on legacy schema-1 packages
whose permissions cannot be verified. For a byte-intact owned package, use this
explicit restoration/upgrade path:

```bash
python3 tools/antigravity.py install --repair-permissions --dry-run
python3 tools/antigravity.py install --repair-permissions
```

This restores the selected candidate's permissions through staged replacement
and retains the previous package, including its original permissions, in a
backup. Changed content, extra files, links and malformed ownership still
refuse replacement. The flag does not override those protections. Repeat
`--destination` and `--python` when using a workspace package or selected venv.
No `.living` migration is required; restart the IDE conversation after upgrade.

```bash
python3 tools/antigravity.py doctor
python3 tools/antigravity.py uninstall --dry-run
python3 tools/antigravity.py uninstall
```

Repeat `--destination` for a workspace installation. Removal moves the owned
package to a backup; it preserves project knowledge, neighboring plugins, and
user configuration. `doctor` checks installed hashes, permission modes, and missing and extra files,
and reports the pinned interpreter, source identity, and skill count. With
`--workspace /absolute/project`, it also requires an existing Git root with a
real `.living/` directory. Missing opt-in, linked paths, and absent workspaces
fail without creating or editing project files. This option is only accepted
by `doctor`; initialization remains an explicit core-skill operation. A package
check alone does not certify workspace opt-in or native hook dispatch. The source
digest covers original packaged bytes and modes, the generator's source bytes,
and effective host guidance, distinguishing development builds with the same
Git commit and base version. The selected interpreter path is recorded separately
and does not change source identity; compare generated file hashes and modes
to verify the complete installed artifact. Older digest evidence is stale after
this identity-format change and must not be relabeled as a new candidate pass.

## Standalone Antigravity App (separate profile)

The standalone Antigravity App uses its own package profile. It does not share
an installation, owner, state directory or validation evidence with the IDE,
Claude Code or Codex. The App package is workspace-local only. Install it into
an existing Git project, with Python 3.11 or newer, from this checkout:

```bash
WS=/absolute/project            # existing Git root you will open in the App
PY=/absolute/python3            # interpreter the hooks will use (venv allowed)
python3 tools/antigravity.py install --profile app --dry-run \
  --destination "$WS/.agents/plugins/mycelium" --python "$PY"
python3 tools/antigravity.py install --profile app \
  --destination "$WS/.agents/plugins/mycelium" --python "$PY"
python3 tools/antigravity.py doctor --profile app \
  --destination "$WS/.agents/plugins/mycelium"
```

Then open `$WS` in the App, start a new conversation under the Default
permission mode, and ask the agent to use the Mycelium core skill to set up the
project. The core skill runs the same
[initialization sequence](../skills/core/references/antigravity-init.md) with
the installed package root and pinned interpreter. Afterwards confirm opt-in:

```bash
python3 tools/antigravity.py doctor --profile app \
  --destination "$WS/.agents/plugins/mycelium" --workspace "$WS"
```

Update by rerunning `install --profile app` with the same destination, and
remove with `uninstall --profile app --destination ...`; both follow the same
ownership and refusal rules as the IDE package. Because the App package lives
inside the repository, it, `.agents/.mycelium-staging` and
`.agents/.mycelium-backups` each carry a generated `.gitignore` containing `*`,
so `git add -A` never commits them and your own `.gitignore` is not edited. An
ordinary App update deletes the previous generation once the new one passes
the doctor check and the old one holds exactly its own manifest's files, bytes
and modes (plus regenerable `__pycache__/*.pyc`); if it holds anything else,
its backup is kept and reported. `--repair-permissions` and `uninstall` still keep a backup
under `.agents/.mycelium-backups` (ignored), which you can delete by hand.
Start a new App conversation after any package change.

Limitations of the App preview:

- The adapter reads the App's own step record in the shape of App 2.31.0.
  Live audits confirmed that shape on App 2.32.1 and 2.33.0. Another App
  version needs fresh native verification before relying on it.
- Commands are credited only when that record shows a built-in `run_command`
  that ran in the sandbox, with its working directory inside the project and
  an explicit exit status. Prefer synchronous commands. A supported
  asynchronous command stays pending and is credited only after the same
  native step records a verified terminal result. Do not use `BypassSandbox`
  or terminal-persistent commands; an explicit `false` for `BypassSandbox` or
  `IsDaemon` is accepted. A bypassed or daemon command, or one whose evidence
  is unavailable or inconsistent, is unresolved: no lineage is recorded for it,
  and at Stop it is recorded as unverified provenance (in the session log, and
  in `native-state.json` even when a session with no other work keeps no log)
  instead of holding Stop. A command still running when the bounded Stop
  allowance ends is recorded the same way.
- Only successful built-in `write_file` and `edit_file` steps count as edits.
  A missed edit callback is recovered once from the step record at a normal
  Stop; other editing routes are not tracked.
- Edit tracking assumes the App records a completed edit as a typed
  `exa.cortex.core.tools.FileEdit` result with the CREATE/OVERWRITE/EDIT kinds
  and argument keys listed in `antigravity_app_store.py`. That type name was
  first inferred; live audits on App 2.32.1 and 2.33.0 then recorded real edits
  in exactly this shape. If a real edit record has another shape, the edit is not tracked, the
  model is told so at its next step (naming the step), and
  `doctor --profile app` reports `app_edit_records.status: "unrecognized"` with
  a warning after reading the newest App conversation stores read-only. Run
  that doctor check after your first App edit.
- Lifecycle state lives under `.mycelium/antigravity-app/` and
  `.mycelium/run/antigravity-app/`. Only Git roots containing `.living/`
  receive state.
- Available models depend on the App account. Model identity does not change
  lifecycle behavior. Selecting a model is not evidence that it follows the
  skills or Stop requirements; that compliance is observed separately in its
  own native run.
- Use the [App audit profile](../skills/lifecycle-audit/references/antigravity-app.md)
  to verify native hook dispatch on the installed package.

## Native lifecycle contract

The verified development target is macOS Antigravity IDE 2.5.5, editor 1.107.0,
extension 0.2.0. The separate Antigravity application and CLI require their own
validation. The implementation follows the IDE's
[plugin](https://antigravity.google/docs/ide/plugins/),
[skill](https://antigravity.google/docs/ide/skills/), and
[hook](https://antigravity.google/docs/ide/hooks/) contracts, with native event
captures used to resolve documentation differences.

| Native event | Mycelium behavior |
| --- | --- |
| `PreInvocation` | Start one lifecycle transaction per explicit user turn and inject project and private-handoff context. |
| `PreToolUse` | Observation only: take the I/O observer's pre-snapshot for `run_command`, or open the journal interval for an edit, then always answer `{"decision": "allow"}`. |
| `PostToolUse` | Translate native commands, reads, and successful file edits into the existing base hooks. |
| `PostInvocation` | Observe a supported lifecycle boundary without treating invocation counters as turn identities. |
| `Stop` | Check a fully idle, normal completion; request continuation when base Mycelium blocks; publish only after base finalization succeeds. |

### PreToolUse: observation only, always `allow`

Antigravity requires every `PreToolUse` hook to return a `decision`. Mycelium's
handler always returns exactly `{"decision": "allow"}` and exits 0. The host's
hook documentation defines `allow` as letting the call proceed by "delegating
to the next hook or default policy", so approval prompts, "Always Allow"
grants and denials stay the user's and the host's. The handler never returns
`auto_approve`, `deny`, `ask`, `force_ask` or `deny_unless_prior_grant`, and
never sets `overwrite` or `permissionOverrides`. Mycelium does not grant
command approval or change the host's permissions.

The guarantee does not depend on the observer working. The registered command
is a shell wrapper, `skills/core/hooks/mycelium-antigravity-pre.sh`. It
discards the Python helper's output and prints the `allow` itself after the
helper exits, crashes, prints something else, or cannot start. A watchdog
kills a helper still running after about 1.8 s, so the hook ends in about 2 s,
well inside its registered 10 s timeout and the host's 30 s default. The
helper has its own 1.5 s deadline. It waits at most 0.5 s for the
observation journal lock and spends the rest, at most the observer's usual
3 s, on the snapshot walk. A walk cut short leaves an `unavailable` observation
(`…-walk-timeout`), never "nothing changed".

It is matched only to `run_command` and the profile's edit tools
(`write_to_file`, `replace_file_content` and `multi_replace_file_content` in
the IDE; `write_file` and `edit_file` in the App). It observes a call only
when the PostToolUse path would credit it: the same host-profile proof and
exact native conversation directory, an opted-in Git root, a supported tool
shape, and an active work cycle. A late event never starts one, and it never
writes native state. A command it cannot snapshot but that can still run (an
App daemon, sandbox bypass or unrecognised argument, an IDE `Cwd` outside the
project, or a command arriving after its work cycle ended) is journaled
without a snapshot as a background call in every opted-in root. Calls that run
beside it are then marked overlapping, so none of them is credited with its
writes. Turn end leaves such an interval open; only the journal's age bound
closes it. In the IDE, a command is observed in the root that holds
its `Cwd`. In the App, a command without `BypassSandbox` or `IsDaemon` is
snapshotted in every opted-in root, because only the App's step record later
proves where it ran. An edit is observed only outside `.living`, `.mycelium`
and `.agents`. The observer's call id is `ag-step-<stepIdx>` in the session
`<conversation>-turn-<user step>`, the same keys the adapter's PostToolUse
derives, so the data tracker's Post closes the same call. That includes a
`RUNNING` command settled later, an App command settled from its step record,
and a successful edit, which gets an edit observation. The App can deliver an
edit's PostToolUse while its step is still running; Stop-time recovery then
credits the edit and records its edit observation. That observation
vouches for every other path, because an App edit changes only its one
verified `Path`. Its own path is hashed only for a `write_file` whose file
still holds exactly its `Content`, since the observation is made at Stop.
An `edit_file`, or a file changed since then, is recorded as written with
its bytes unknown (`write-hash-unknown`). When a work cycle
reaches Stop finalization, the intervals of calls that never got a Post
(denied, failed or unrecorded) are closed as `turn-ended`. A command still
`RUNNING` at the final Stop allowance keeps its interval open. With this, an
Antigravity run gets the same observed I/O lineage as Claude Code and Codex
(`lineage_index.py query --path …` → `current-in-record` with sole
attribution) for synchronous commands that ran in one opted-in root.

For live verification only, a word in `<project>/.mycelium/antigravity-pre-fault`
(or the `MYCELIUM_ANTIGRAVITY_PRE_FAULT` environment variable) makes the
helper fail on purpose. `crash` raises, `exit` exits without output, `slow`
sleeps past its own deadline, and `hang` ignores that deadline so only the
wrapper's watchdog ends it. Each still answers `allow`. Delete the file
afterwards: while it is present, nothing is observed.

The host documentation does not say what Antigravity does when a hook fails,
times out or prints nothing, or whether a hook's presence changes the
approval UI. That is why the wrapper never lets those cases happen and why
each App version needs the approval-preservation check in the
[App audit profile](../skills/lifecycle-audit/references/antigravity-app.md).

### Other events

The native plugin hook working directory is
the installed package root. The observed `PostToolUse.toolCall` supplies
arguments; `error: ""` does not imply process exit zero. A structured exit code
is retained when the transcript provides one. Commands observed as `RUNNING`
remain pending; they do not snapshot outputs or finalize lineage. Later native
events reconcile the same command step when its transcript status becomes
`DONE`. A completed command without an exit code retains an unknown exit.
This IDE profile can leave a finished asynchronous command marked `RUNNING`;
without terminal evidence Mycelium keeps it pending within the bounded Stop
allowance, then records it in the session log as unverified provenance and
finalizes. Prefer synchronous native commands for work whose lineage matters. Deferred snapshots describe files observed at
completion, not instrumented read/write-time provenance.

The native conversation UUID and explicit `USER_INPUT` step identify a work
cycle. Invocation counters can reset without a new user turn. State is stored
at `.mycelium/antigravity-ide/<conversation>/native-state.json`; base transaction
files use `.mycelium/run/antigravity-ide/<conversation>-turn-<step>/`. Locks,
path preflight, durable action records, and late-event rejection protect those
boundaries. A helper with an uncertain outcome is retained and never silently
dispatched again. Cancellation, native errors, and exhausted Stop retries leave
the cycle incomplete. There are at most three requested Stop continuations.

Each Stop attempt is journaled before dispatch. The base finalizer writes an
identity-bound `accepted-stop.json` in that turn's runtime directory only after
acceptance effects complete. If the response is lost, the adapter reconciles
this receipt without rerunning Stop. A missing receipt leaves the attempt
unknown; marker disappearance alone is insufficient proof. Receipts are
retained even after transient per-turn files are cleaned.

Native journals now use schema 2. Accepted and incomplete schema-1 history is
preserved. An active cycle from the older preview is retained as uncertain
because its earlier asynchronous snapshots cannot be certified retroactively;
inspect its evidence and start a fresh IDE conversation after updating the
package. Existing Claude Code and Codex projects require no migration for these
Antigravity-specific repairs.

The adapter reads a bounded native transcript tail for event identity and
structured execution status. It does not retain model reasoning. Post-action
reminders are queued by event ID and cleared individually after native delivery
is observed. Several analysis commands in one native invocation retain all
undelivered guidance, including the first mandatory scientific protocol.
Guidance still undelivered at a natural Stop (for example from a command that
finished during that Stop) is delivered once as the Stop reason; at the final
Stop allowance it is written to the session log as undelivered instead of
holding Stop. A
schema-2 preview's retained singleton reminder migrates into that queue;
messages already overwritten by an older build cannot be reconstructed or
retrospectively certified. No project knowledge migration is needed.

Knowledge indexing uses `generate_index.py --summary-heuristic` and the existing
offline concept-labeling fallback. The selected IDE model can author improved
summaries or labels through the normal validation and acceptance workflow.
It is never replaced with a separately selected agent CLI.

## Validation and support boundary

Model identity is not used in lifecycle routing, storage, permissions, or
provider selection. Gemini, Claude, and GPT-family models available in the IDE
share the same adapter. Availability is controlled by the IDE account; the
OpenAI-family model observed during development was GPT-OSS 120B Medium.
This does not establish availability of every ChatGPT model.

Native probes have demonstrated skill discovery, automatic hook dispatch,
analysis execution, structured synchronous failure tracking, asynchronous
unknown-exit handling, file-edit tracking, and retention after native limits.
Gemini and GPT-OSS completed natural Stop block, repair, and acceptance with
verified input, script, and output hashes in the finalized lineage. Some broader
model runs exhausted their declared allowance before normal finalization.
Passing component tests and package hashes alone do not establish full model
or workflow certification.

The [IDE lifecycle audit](../skills/lifecycle-audit/references/antigravity-ide.md)
documents the native probes and receipt checks. The [App lifecycle audit](../skills/lifecycle-audit/references/antigravity-app.md)
covers the separate App step-record format and sandboxed command evidence.

## Scientific validation boundaries

The shared lineage tracker detects literal I/O paths. Composed paths, CLI-bound
paths and imported I/O may remain unresolved; a uniquely named repository file
does not prove runtime use and is no longer substituted. Captured missing or
unknown hashes stay unchanged at finalization. For dynamic workflows, inspect
the analysis-produced input hash and final outputs alongside command evidence;
do not describe an unresolved hook record as complete I/O provenance.
