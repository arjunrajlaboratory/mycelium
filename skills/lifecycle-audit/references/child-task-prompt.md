# Canonical Child-Task Prompt

Launch the fresh host task with this prompt, substituting only the
host-specific edit tool. Every probe below is required; the 0.6.1
post-release audits omitted the nested-subagent probe on both hosts, so the
template now carries it — mark its evidence row N/A only when the host
genuinely exposes no native subagent/task mechanism (current Claude Code and
Codex both expose one), and say so explicitly in the report.

Host substitutions:

- **Claude Code**: edit tool is `Write`; deletion uses the narrow
  `rm -- MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp` exception after tracked
  creation; the subagent mechanism is the `Task` tool.
- **Codex**: edit tool is `apply_patch` for both creation and deletion; the
  subagent mechanism is the native subtask/collaborator tool exposed by the
  CLI build under test — if the build exposes none, record N/A with the CLI
  version as evidence.

Prompt body (substitute `{EDIT_TOOL}`, `{DELETE_INSTRUCTION}`,
`{SUBAGENT_TOOL}`):

```text
You are performing a black-box lifecycle observation task. Follow these
steps exactly, in order, and produce a report with the exact section headers
given. Do not invoke or read any skill. Do not inspect plugin installations,
hook registrations, or source checkouts. Never invoke any Mycelium hook
script directly.

STEP 1: Print the output of pwd. If it is not this repository root, stop.

STEP 2: Under a section "SESSIONSTART CONTEXT", report verbatim any
automatic session-start context injected into this conversation before your
first action. If none, write NONE.

STEP 3: Under a section "SUBAGENT PROBE", first report the current contents
of any active-session marker identity you were told about in the session
context (do NOT read .mycelium state directly — quote only what the
injected context already told you). Then use {SUBAGENT_TOOL} to launch one
child task whose entire instruction is: "Print pwd and nothing else. Do not
edit any file or invoke any skill." Wait for it to finish, then report
whether any new session-start or session-end context for the CHILD appeared
in your own conversation, verbatim. If the host has no such tool, write N/A
and the reason.

STEP 4: Run exactly this shell command:
env -C analysis/example python3 run.py --help
Under a section "POSITIVE PROBE", report the command's exit status and then
verbatim any automatic context that appeared after the tool result. If
none, write NONE.

STEP 5: Run exactly this shell command:
env -S 'echo prefix' python3 run.py --help
Under a section "NEGATIVE PROBE", report the exit status and whether ANY
automatic Mycelium context appeared after it. If none, write NONE.

STEP 6: Use {EDIT_TOOL} to create a file named
MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp at the repository root containing the
single line: disposable audit marker
Under a section "EDIT PROBE", report any automatic context that appeared.
Then delete the file: {DELETE_INSTRUCTION}

STEP 7: Do NOT write anything to .living yet. End your turn now by
finishing this report. If a stop-blocking message appears asking you to
update the living repository, comply as follows on your continuation:
append exactly one clearly labeled lifecycle-audit line to
.living/learnings.md, and write the five-section handoff the blocking
message names, using exactly these headings once each in this order, each
with one nonblank bullet: "## What was worked on", "## Key decisions made",
"## Blockers & surprises", "## Current state", "## Next steps". Then end
your turn again and report under "STOP RETRY" what the blocking message
said, verbatim.

Produce the report sections in order. Do not perform any other repository
mutations.
```

Launcher notes:

- Capture the full event stream, not only the final message. Claude Code
  print mode returns only the last message; prefer `--output-format
  stream-json` there. `codex exec` prints the full stream by default.
- The subagent-probe verification is owned by the launcher: after natural
  Stop, confirm the primary session's marker, owner token, active log,
  baselines, and raw lineage were not consumed or duplicated by the child
  (one root transaction, one registry row, one lineage manifest).
