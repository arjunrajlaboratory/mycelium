---
name: develop
description: >
  Develop, refactor, or review the Mycelium plugin itself across Claude Code and
  Codex. Use for shared skills, plugin manifests, lifecycle hooks, installers,
  migrations, session accounting, shell execution detection, data lineage, or
  cross-host compatibility changes in the Mycelium source repository. Apply
  branch-wide error-pattern review, TDD, compatibility checks, and real-host
  smoke testing. Do not use for maintaining an ordinary Mycelium-enabled
  project, performing scientific analysis, or responding to an existing Codex
  PR comment (use codex-review for that).
---

# Mycelium Development

Resolve paths in this skill relative to the Mycelium plugin root, two
directories above this `SKILL.md`. Resolve project paths relative to the source
checkout being changed.

Use this workflow to keep changes correct as a system on both supported hosts.
Before editing, read `references/regression-patterns.md` and
`../../docs/cross-host-review-checklist.md` completely. The checklist is the
release gate; the regression patterns explain why its most important checks
exist.

## Workflow

### 1. Establish the release-candidate scope

- Record the branch, working-tree state, merge base, exact head, and PR base.
- Account for every local change. Preserve unrelated and untracked user files.
- Review `base...head`, not only the newest commit.
- Classify each affected behavior as shared, Claude-only, Codex-only, or
  packaging/discovery. Shared behavior must remain host-neutral.
- Write down the invariants the change must preserve before designing the fix.

### 2. Reproduce with a red test

- Add the smallest regression test that fails for the actual reason, then run it
  and capture the expected failure.
- Test the externally visible contract, not an implementation accident.
- For mutation workflows, assert the complete failure boundary: original bytes,
  permissions, unrelated configuration, and absence of earlier partial writes.
- For parser fixes, add paired positive and negative cases. Include quoting,
  wrapper, cwd, exit-status, or shell-structure neighbors that distinguish the
  intended behavior from a false positive.
- For lifecycle fixes, test retry, cleanup, and concurrency where relevant—not
  only the happy path.

If a focused test cannot run because the environment lacks a dependency,
bootstrap it outside the repository or report the constraint. Do not claim a
red/green TDD cycle without observing both states.

### 3. Generalize the defect pattern

Name the underlying mistake in one sentence. Search every branch-touched module
for the pattern, then search unchanged code if the pattern may be systemic.
Reason about each match; do not rewrite false positives. Fix all in-scope
instances in the same pass and record the search coverage.

If the finding exposes a new review category, add it to the cross-host checklist
and its adversarial matrix. A repeated error pattern belongs in
`references/regression-patterns.md` as well.

### 4. Implement the narrow cross-host fix

- Keep host payload decoding and emission at explicit boundaries.
- Preserve user-authored guidance, hooks, settings, and `.living` content.
- Preflight every source and destination before the first write in a multi-file
  operation.
- Reject linked or escaping managed paths before reading or writing them.
- Use atomic replacement for durable state and preserve existing permissions.
- Preserve active retry evidence until the transaction is actually accepted.
- Do not embed versioned cache paths or developer-machine paths in packaged
  configuration.

Avoid opportunistic cleanup that expands the branch beyond the named defect
pattern.

### 5. Run the test ladder

Run from narrowest to broadest:

1. New regression tests.
2. The touched module's test file or hook harness.
3. All Python compatibility tests under `skills/core/scripts`, excluding the
   separately provisioned knowledge-map tests when required.
4. `test_stop_hook.sh`, `test_hooks_stress.sh`, and
   `test_integration_stress.sh` for lifecycle changes.
5. Fresh initialization plus `validate_structure.py` in a temporary repository.
6. Manifest, skill metadata, executable-bit, JSON/YAML, diff, and documentation
   checks represented by CI.

Run `git diff --check` and inspect the final diff after tests. A command sequence
whose last command passes does not prove earlier commands passed; retain each
exit status or use a fail-fast runner.

### 6. Exercise real hosts when the boundary changed

Use `$mycelium:lifecycle-audit` when changes touch plugin installation,
discovery, dispatch, lifecycle hooks, lineage, or host payloads. Unit tests that
invoke hook scripts directly do not prove automatic host dispatch.

Verify the installed artifact is the exact candidate under test, and do it
**before** launching the host process. The Claude plugin cache is keyed on the
version string, so any content change under an unchanged version is served
stale and `claude plugin marketplace update` does not fix it — see
`references/regression-patterns.md` patterns 1 and 34 for the mechanism and the
refresh. Codex local development requires a cachebuster and reinstall into a new
task. Source and installed files must hash identically; report the file count
compared, not the version label. Run a Claude Code CLI smoke for shared or
Claude-facing changes. Keep environmental failures distinct from Mycelium
failures.

### 7. Close compatibility and documentation

- State whether existing Mycelium projects continue unchanged and whether an
  upgrade or migration is required.
- Update install, update, trust, restart, and migration instructions when their
  actual interface changes.
- Update the changelog for user-visible behavior.
- Re-run the complete gate on the exact final tree after documentation and skill
  changes.

Do not commit, push, post review replies, or retrigger external review unless the
user authorized those outward actions.

### 8. Release, in this order

`docs/release-process.md` documents the gate. The ordering below is what makes
its results mean anything, and every step has been got wrong at least once:

1. **Merge first.** The gate's installed-artifact check compares against a
   published, installed version, so a candidate that is not on the default
   branch cannot satisfy it. Confirm the merge actually reached the default
   branch — see pattern 35 before stacking anything.
2. **Refresh and hash-verify the install.** Pattern 1's refresh, then the hash
   comparison. Skipping this makes every later step certify the wrong tree.
3. **Host audits, after the refresh.** `$mycelium:lifecycle-audit` on both
   hosts. The gate requires evidence for Claude *and* Codex, or
   `--waive-host-audits REASON` for both — it rejects one-sided evidence.
4. **Run the gate from a clean disposable clone.** It refuses a dirty or
   untracked tree with no bypass.
5. **Tag last**, only after the gate passes, and tag the exact commit the gate
   certified rather than whatever the default branch has moved to.

Version bumps live in their own `release/X.Y.Z` change: three-way agreement
across `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
(`metadata.version`), and the base of `.codex-plugin/plugin.json`, plus a fresh
`+codex.<timestamp>` cachebuster and a dated changelog section. Fold any
`## [Unreleased]` entries into that section — the gate only checks the dated
section *exists*, so entries left under Unreleased ship as missing release notes.

Operational gotchas, all of which have cost a full cycle:

- `release_gate.py` runs the ladder via `sys.executable`. Invoke it with the
  interpreter that actually has `pytest`, or the ladder fails on an import.
- `codex exec` blocks reading stdin; redirect `< /dev/null` for a
  non-interactive audit.
- Codex plugin hooks need a one-time interactive trust step (`/hooks`, trust,
  restart). A fresh `codex exec` in an untrusted project shows zero dispatch,
  which is an environment result, not a hook defect.
- `timeout` does not exist on macOS. A wrapper that silently fails to run makes
  "no output" look like "no dispatch".

## Review output

Report findings by severity with file and line evidence. When no actionable
findings remain, say so explicitly and list residual risks or unexercised host
boundaries. For fixes, include the red test, generalized pattern sweep, final
test evidence, real-host evidence, and compatibility impact.
