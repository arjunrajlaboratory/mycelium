---
name: lifecycle-audit
description: >
  Run a black-box Mycelium lifecycle smoke test through a real Claude Code or
  Codex CLI task. Use after plugin installation, hook, SessionStart,
  PostToolUse, Stop, activity tracking, handoff, or data-lineage changes; when
  automatic hooks appear not to fire; or before merging cross-host lifecycle
  work. Verify installed-build identity, natural dispatch, state transitions,
  positive and negative probes, and scientific-tree isolation. Do not use as a
  substitute for unit tests or for ordinary scientific analysis.
---

# Mycelium Lifecycle Audit

Resolve bundled paths relative to the Mycelium plugin root, two directories
above this `SKILL.md`. Read `references/audit-protocol.md` completely before
launching a host process.

This is a black-box integration audit. A manually invoked hook can diagnose hook
logic, but it cannot prove that Claude Code or Codex discovered, trusted, and
dispatched the hook.

## 1. Fix the audit boundary

- Record the host, target repository, source commit, installed plugin version,
  and expected plugin root.
- Use a disposable initialized repository when possible. If using a real
  analytical project, capture a baseline and prohibit scientific code, data,
  output, report, and pre-existing `.living` edits.
- Confirm the requested CLI is installed. Inspect its current help rather than
  guessing flags.
- Freeze the source checkout and installed artifact for the entire host run.
  Do not edit, reinstall, or replace an exercised hook while its process may be
  reading it.
- If the user did not explicitly request a real host run, confirm before sending
  repository context to an external agent service.

For Codex local development, verify `codex plugin list --json`, use the supported
cachebuster/reinstall flow, and start a new task. Hash exercised installed files
against the source candidate. For Claude, identify the plugin source actually
loaded by the CLI and verify its files the same way. A source/cache mismatch is
an audit failure even if hooks otherwise work.

## 2. Capture a pre-run baseline

Record:

- `git status` and relevant file hashes;
- existing session log, registry, handoff, lineage, and runtime markers;
- scientific files and output state;
- expected next log or session identifier when determinable.

Do not delete suspicious state merely to make the test pass. If a clean-room
audit is needed, create a fresh disposable repository.

## 3. Launch one genuinely fresh host task

Run the host CLI from the exact target root. Do not invoke any Mycelium hook
script in the task or immediately afterward. Use the canonical child-task
prompt in `references/child-task-prompt.md` — every probe it carries is
required, including the nested-subagent probe, which may be marked N/A only
when the host exposes no native subagent mechanism. Ask the agent to expose
the exact automatic lifecycle context it receives and perform only the probes
in the audit protocol. The launcher, not the child agent, owns artifact verification
and baseline capture. Where the CLI supports it, disable skills in the child
task so the lifecycle-audit skill does not recursively launch or expand itself.
Keep ordinary file-reading and editing tools available: the child needs them to
observe state and satisfy the expected Stop-compliance retry.

Exercise:

1. SessionStart injection and active-state creation.
2. When the host exposes a native subagent/task mechanism, one read-only child
   start and Stop while the primary remains active.
3. A known-positive harmless analysis command.
4. A near-neighbor negative command that must remain silent.
5. One disposable successful edit using the host's normal edit tool. Remove it
   with that tool when deletion is supported; otherwise, after edit-tracking
   evidence is captured, use the protocol's single explicit shell cleanup.
6. Natural Stop, including the expected enforcement block, compliant retry,
   and a complete five-section handoff using Mycelium's exact headings once
   each, in order, with nonblank section bodies.

Use `python3` unless the target explicitly provides another interpreter; do not
assume `python` exists. Preserve the real command exit status—an optional
dependency failure may still be a valid lineage probe.

## 4. Inspect only after natural Stop

Compare post-run state with the baseline. Verify the first Stop blocked until
the disposable repository's living layer was updated, then verify exact-once finalization,
registry and lineage effects, handoff structure, cleanup, and absence of the
disposable file. Separate lifecycle-owned changes from agent-authored scientific
changes.

Classify every unexpected result as one of:

- **Artifact identity:** wrong source, version, cache, or packaged file.
- **Host dispatch:** registration, trust, discovery, matcher, or host payload
  never reached the hook.
- **Hook logic:** dispatch occurred, but state or context violated the contract.
- **Environment:** missing interpreter/dependency or CLI limitation unrelated to
  Mycelium behavior.
- **Agent compliance:** hook instructed the agent correctly, but it ignored or
  rewrote required state.
- **Inconclusive:** evidence was overwritten, manually stimulated, or never
  captured.

Never relabel an environment or compliance failure as a hook pass without
showing the hook evidence that did pass.

## 5. Report and clean up

Return the evidence table from the audit protocol, exact errors, plugin identity,
and a scientific-state diff. Remove only disposable probe files you created;
retain failing lifecycle state when it is needed to reproduce a retry bug.

Do not repair failures during the audit. Diagnose first so the observed state is
not contaminated. If a fix is requested afterward, switch to
`$mycelium:develop`, add a red regression test, and repeat the black-box audit on
the new installed artifact.
