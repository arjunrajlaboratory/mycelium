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
not replace the selected launcher.

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
ownership, backup and refusal rules as the IDE package. Start a new App
conversation after any package change.

Limitations of the App preview:

- The adapter reads the App's own step record, whose shape is pinned to App
  2.31.0. Another App version needs fresh native verification before relying
  on it.
- Commands are credited only when that record shows a built-in `run_command`
  that ran in the sandbox, with its working directory inside the project and
  an explicit exit status. Prefer synchronous commands. A supported
  asynchronous command stays pending and is credited only after the same
  native step records a verified terminal result. Do not use `BypassSandbox`
  or terminal-persistent commands. A bypassed command, or one whose evidence
  stays unavailable or inconsistent, remains unresolved and Stop blocks until
  its bounded allowance ends.
- Only successful built-in `write_file` and `edit_file` steps count as edits.
  A missed edit callback is recovered once from the step record at a normal
  Stop; other editing routes are not tracked.
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
| `PostToolUse` | Translate native commands, reads, and successful file edits into the existing base hooks. |
| `PostInvocation` | Observe a supported lifecycle boundary without treating invocation counters as turn identities. |
| `Stop` | Check a fully idle, normal completion; request continuation when base Mycelium blocks; publish only after base finalization succeeds. |

No `PreToolUse` hook is installed. The adapter does not grant command approval
or change the IDE's permissions. The native plugin hook working directory is
the installed package root. The observed `PostToolUse.toolCall` supplies
arguments; `error: ""` does not imply process exit zero. A structured exit code
is retained when the transcript provides one. Commands observed as `RUNNING`
remain pending; they do not snapshot outputs or finalize lineage. Later native
events reconcile the same command step when its transcript status becomes
`DONE`. A completed command without an exit code retains an unknown exit.
This IDE profile can leave a finished asynchronous command marked `RUNNING`;
without terminal evidence Mycelium keeps that cycle unresolved, including after
the bounded Stop allowance. Prefer synchronous native commands for work that
must finalize in this preview. Deferred snapshots describe files observed at
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
undelivered guidance, including the first mandatory scientific protocol. A
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
