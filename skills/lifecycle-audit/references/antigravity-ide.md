# Antigravity IDE audit profile

Use the installed IDE, its selected model and native tools. This profile has
no provider SDK, external coding-agent runtime or coordination-service
dependency. Antigravity's separate application and CLI are separate targets.

## Prepare and freeze

1. Create a disposable directory, initialize Git, then run the candidate's
   `skills/core/scripts/init_repo.py --target-dir /absolute/root`. Seed a
   harmless nested analysis entry point and commit the fixture baseline before
   launch. Never mutate historical audit fixtures to obtain a passing result.
2. Install the candidate with `tools/antigravity.py install --destination
   /absolute/root/.agents/plugins/mycelium --python /absolute/python3`.
   Run `doctor` with that destination and `--workspace /absolute/root`.
   Record the complete `.mycelium-install.json` and verify package hashes.
   The source digest covers packaged bytes/modes, generator bytes and effective
   IDE guidance. Compare transformed installed bytes and modes with
   `candidate_files`, not raw skill text. Require schema-2 doctor success;
   explicitly upgrade a legacy package with `install --repair-permissions`.
   Record source commit and digest even for development builds.
3. Capture baseline hashes and lifecycle state. Freeze source and installation
   until all declared native cases finish. Declare an invocation/time allowance
   and preserve failed, cancelled or unknown attempts without redispatch.
4. Open the exact workspace in Antigravity IDE and start a fresh conversation.
   Verify the workspace and model in the UI. Retain the UI selection text
   (`Select model, current: ...`) and exact submitted prompt acceptance from
   the native `USER_EXPLICIT` / `USER_INPUT` transcript event. Do not silently
   replace an unavailable requested model.

## Run and verify

Use `child-task-prompt.md` with only documented host/path/probe substitutions.
For `{EDIT_TOOL}`, specify native `write_to_file` with `ArtifactMetadata`
omitted for all repository paths, including `.living` and `.mycelium`.
This is native tool-argument guidance; all probes and success criteria remain.
Keep all canonical probes: startup context, applicable nested child, positive
command, negative neighbor, native edit and cleanup, first Stop block, learning
and five-section handoff, accepted Stop. Prefer synchronous native shell
commands: an asynchronous command that the host still marks `RUNNING` is
unresolved, even if a file exists or an outer process appears finished.

The current IDE may expose only a browser child. That is insufficient for the
coding-child isolation probe: record `not_available` and the actual tool
limitation. Do not substitute a browser child or an external agent runtime.

After natural Stop, inspect `.mycelium/antigravity-ide/<conversation>/native-state.json`
and `.mycelium/run/antigravity-ide/<conversation>-turn-<step>/accepted-stop.json`.
Require one accepted transaction, no uncertain or pending work, and matching
conversation/explicit-user-turn identities. Lifecycle cases must show the
untriaged-activity block followed by accepted compliance, not merely a pending
command retry. Verify the exact automatic block in native tool/public response
evidence. Check final log/footer/registry cardinality, lineage positive and
negative controls, edit tracking, cleanup and scientific baseline hashes.
Retain public/tool evidence without copying model reasoning.

For a recall case, copy previously validated knowledge and relevant outputs
into a new initialized fixture, preserving their provenance. Use a fresh model
conversation to retrieve predetermined facts without rerunning the analysis.
Verify the facts against known answers, the native read evidence and unchanged
scientific hashes. Require accepted Stop and a valid durable handoff. Recall
does not replace the canonical lifecycle cases.

## Retain evidence

Keep the installed package manifest, native state, accepted Stop receipt,
handoff, model selection and probe results with the audit. Record the source
commit and digest so a later audit can distinguish builds. Record each
unavailable or unresolved probe with its observed limitation.
