# Real-Host Lifecycle Audit Protocol

Use this protocol to produce comparable evidence across Claude Code and Codex.
Adapt only the target path and harmless positive script; preserve the positive
versus negative distinction.

## Preconditions

- The target root is exact and under the user's authorized scope.
- The project is initialized and validates structurally.
- The tested plugin artifact is identified and hashes match the candidate.
- The host task is fresh; a running task cannot prove SessionStart loading of a
  newly installed plugin.
- Do not change the source or installed artifact while the host task is running.
  Build and verify it first, then treat it as immutable until natural Stop.
- Keep ordinary file-reading and editing tools available. Disable recursive
  skill loading when supported, but do not prevent the agent from reading and
  updating the disposable repository during the expected Stop retry.
- A full Stop audit uses a fresh disposable initialized repository. A real
  scientific repository may be used for nonmutating dispatch diagnosis, but do
  not add audit-only living knowledge to it.
- No hook script will be called manually during the black-box phase.

## Probe selection

Choose an existing harmless analysis entry point whose help or validation path
does not intentionally write scientific outputs. Prefer a nested script because
it also tests cwd resolution.

Positive example from a project root:

```bash
env -C analysis/example python3 run.py --help
```

The command may exit nonzero because of a missing optional dependency. Record
that exact exit from the observable host stream. Require lineage to retain it
only when the hook payload includes authoritative exit evidence; never invent a
status that was unavailable when the hook ran. Current Codex Bash PostToolUse
supplies an empty `tool_response`, so `bash_exit` and `bash_wall_s` remain null
even though the later outer CLI event reports the command's exit. Do not install
dependencies in the audit merely to force exit zero.

Negative argv-rewrite neighbor:

```bash
env -S 'echo prefix' python3 run.py --help
```

This executes `echo` with interpreter-looking arguments. It must not produce an
analysis reminder or lineage event.

For activity, create and remove a uniquely named root-level disposable file
through the host's normal edit tool:

```text
MYCELIUM_HOOK_AUDIT_DISPOSABLE.tmp
```

Codex should use `apply_patch`; Claude Code should use `Write` or `Edit`. Do not
create the file with Bash because that would conflate edit and Bash tracking.

## Host task instructions

The fresh agent should:

1. Do not invoke or read any skill. The parent audit orchestrator already read
   this protocol, captured the baseline, and verified the artifact.
2. Do not inspect plugin identity, source checkouts, installations, hook
   registrations, or the pre-run baseline. Such probes contaminate PostToolUse
   and lineage evidence.
3. Print `pwd` and stop if it differs from the exact target.
4. Report the exact automatic SessionStart context already visible to it.
5. If the host provides a native subagent/task tool, record the active marker,
   owner token, and active log identity; launch one read-only child that only
   reports `pwd`; wait for its natural Stop; then verify the primary identities
   are unchanged. Do not ask the child to edit or invoke a skill.
6. Run the positive command literally and report exit plus exact automatic
   PostToolUse context.
7. Run the negative command literally and report whether any automatic context
   appeared.
8. Create and remove the disposable file with its normal edit tool.
9. Before the first Stop, write no living knowledge merely to satisfy the
   audit. Let activity enforcement block that Stop and preserve the exact
   automatic reason.
10. After that expected block, only in the fresh disposable repository, append
   one clearly labeled lifecycle-audit observation to `.living/learnings.md`.
   Do not modify scientific code, data, outputs, reports, or pre-existing
   living content.
11. Never invoke a Mycelium hook directly and never repair any result other than
   the expected Stop-compliance step above.
12. Produce a genuine five-section handoff before the accepted retry, using
   these exact headings: `## What was worked on`, `## Key decisions made`,
   `## Blockers & surprises`, `## Current state`, and `## Next steps`.

## Evidence table

Report each row as Pass, Fail, or Inconclusive.

| Boundary | Required evidence |
| --- | --- |
| Artifact identity | Source commit, installed version/root, and matching hashes for exercised files. |
| SessionStart | Exact injected context, one active log, valid marker path plus owner timestamp, and session baseline. |
| Nested-session isolation | When supported, the child starts and stops naturally while the primary marker, owner token, active log, baselines, and raw lineage remain unchanged. Otherwise mark N/A. |
| Positive PostToolUse | Exact context, reminder/activity as applicable, script path resolved from effective cwd, the real exit from the host stream, and either matching lineage status or explicit evidence that the hook payload omitted it. |
| Negative PostToolUse | No reminder, activity misclassification, or lineage from interpreter-looking echo arguments. |
| Edit tracking | Successful host edit recorded; failed edits are not; disposable file is absent at end. |
| Stop | The first Stop blocks for untriaged activity; one compliant retry produces one finalized log, one completion footer, one registry row, one lineage consolidation when applicable, and accepted cleanup. |
| Handoff | All five required headings exist in the durable handoff after Stop. |
| Scientific isolation | No scientific code, data, output, report, or pre-existing living knowledge changed; any audit-only living entry exists solely in the fresh disposable repository. |

## State inspection

Inspect, when present:

- `.mycelium/active-session-log.tmp` (line 1 path, line 2 owner timestamp, and
  optional line 3 `owner-id-v1` ownership-format discriminator);
- `.mycelium/active-session-owner-id.tmp` (one validated host-session token);
- `.mycelium/session-start-ts.tmp` and activity/reminder markers;
- `.mycelium/mycelium-data-events.tmp` and lineage status/session markers;
- `.living/log/LOG_REGISTRY.md` and the new session log;
- the consolidated lineage JSON;
- `.mycelium/last-session.md`;
- `git status`, hashes, and the disposable file path.

A natural accepted Stop should clean transient state. If Stop blocks or a helper
fails, retained state may be correct retry behavior; classify it using the hook
context rather than declaring cleanup failure automatically.

## Interpretation guardrails

- The observable host stream and filesystem state outrank an agent's narrative
  summary. Report contradictions as agent-compliance or evidence-quality
  findings instead of repeating the narrative.
- No automatic context plus no state means dispatch failure, not hook-logic
  failure.
- Automatic context with incorrect state means hook-logic failure.
- A command exit caused by a missing package is an environment result. The
  lineage exit field must match authoritative hook input when present. If the
  real hook input omits status, null is correct and the limitation must be
  reported explicitly.
- A missing handoff is hook logic if Stop accepted incomplete content or failed
  to create the required fallback. It is agent compliance if Stop rejected and
  clearly instructed the agent to complete it.
- Manual hook output is useful follow-up diagnosis but makes the black-box row
  inconclusive; keep it in a separate section.
- Report lifecycle bookkeeping changes explicitly. They are expected and must
  not be described as scientific changes.
