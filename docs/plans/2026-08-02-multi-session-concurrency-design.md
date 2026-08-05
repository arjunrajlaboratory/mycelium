# Cross-Host Multi-Session Concurrency — Design Spec

**Date:** 2026-08-02
**Status:** Approved for implementation on `feat/multi-session-concurrency`
**Base:** `b780b47148435c3d50e3a8f865b50c4e4ed87207`
**Source idea:** [PR #64 — Make multi-chat concurrent work safe on a shared `.living/`](https://github.com/arjunrajlaboratory/mycelium/pull/64)

## 1. Problem

Mycelium currently protects lifecycle state with a single repository-global
owner. That prevents a second live root task from corrupting the first task's
transaction, but it also prevents the second task from receiving its own
session log, activity accounting, lineage, Stop enforcement, and handoff.

The desired behavior is stronger: independent Claude Code and Codex tasks may
work in the same checkout concurrently, each with an isolated lifecycle
transaction, while continuing to share one durable `.living/` knowledge layer.

PR #64 identified the right categories—session-scoped runtime state, collision-
free log allocation, and locked shared writers—but predates provider-neutral
state, Codex hooks, owner-token validation, retry-preserving Stop transactions,
filesystem containment, and the current cross-host test gate. Its code is a
requirements source, not a patch source.

## 2. Goals and non-goals

### Goals

- Give every identified root host task an independent lifecycle transaction.
- Let subagents contribute to their root task without reserving or finalizing a
  second transaction.
- Preserve exact-once Stop, retry evidence, lineage, authored metadata, and
  handoff semantics independently for every concurrent task.
- Serialize only operations that mutate shared durable files.
- Support Claude Code and Codex through the same shared implementation.
- Preserve already-active pre-upgrade flat transactions and identity-free host
  payloads without requiring an immediate repository migration.
- Reject malformed identities, linked state, escaping paths, partial markers,
  and lock failures without falling back to an unsafe mode.

### Non-goals

- No attempt to merge arbitrary simultaneous source-code edits made by two
  agents in the same working tree. Git/editor conflicts remain visible to the
  agents.
- No automatic deletion of abandoned live-session directories. Reaping without
  authoritative host liveness can race a slow or disconnected task.
- No Windows support in this change. Mycelium's supported hook hosts are macOS
  and Linux; POSIX locking may fail closed elsewhere.
- No change to host hook registration counts or wire-format adapters.

## 3. Locked design decisions

### 3.1 State layout

Provider-neutral shared state remains at `.mycelium/`:

```text
.mycelium/
  .gitignore
  plugin-root
  last-session.md                 # most recently accepted handoff
  locks/                          # shared durable-writer locks
  mycelium-stop.lock/             # repository lifecycle serialization
  mycelium-data-events-prev/      # accepted raw-lineage archives
  mycelium-data-events-abandoned/ # abandoned raw-lineage archives
  run/
    claude/<host-session-id>/
    codex/<host-session-id>/
```

Each identified task's directory owns every transient or retry-bearing field:

```text
run/<host>/<host-session-id>/
  active-session-log.tmp
  active-session-owner-id.tmp
  session-start-ts.tmp
  session-file-baseline.json
  living-reminder-baseline.json
  mycelium-reminded.tmp
  mycelium-session-activity.tmp
  mycelium-data-events.tmp
  data-lineage-session-id.tmp
  handoff-finalization-pending.tmp
  last-session.md                 # task-authored handoff until acceptance
```

Host namespacing prevents an accidental Claude/Codex ID collision. Session IDs
must be JSON strings and are validated exactly (`[A-Za-z0-9._-]+`, maximum 200
characters, excluding the special path components `.` and `..`); they are never
lossy-sanitized. Present non-string or otherwise invalid identified payloads
fail closed instead of downgrading to legacy state.

Identity-free payloads continue to use the legacy flat `.mycelium/*.tmp`
layout. An identified pre-upgrade task may also continue using flat state when
the flat owner token exactly matches its host session ID. New identified tasks
never claim another flat transaction.

### 3.2 Lock domains and order

Three lock domains have distinct purposes:

1. **Repository lifecycle lock** (`.mycelium/mycelium-stop.lock/`): serializes
   SessionStart reservation and Stop finalization across all sessions. It is
   also the ordering boundary for shared log allocation and global handoff
   publication.
2. **Per-session event lock** (`run/<host>/<id>/mycelium-session.lock/`):
   serializes PostToolUse state changes with Start/Stop for the same task.
   Different tasks' PostToolUse hooks remain concurrent.
3. **Durable file locks** (`.mycelium/locks/<stable-name>.lock`): serialize the
   complete read/derive/write transaction for shared `.living` registries,
   indexes, and graph builds, including standalone/manual script invocation.

When more than one lock is required, order is always repository lifecycle,
then per-session, then durable file lock. A PostToolUse handler acquires only
its per-session lock. No code may acquire a broader lock while holding a
narrower lock.

Lock acquisition is bounded and fail-closed. A directory lock with a recorded
dead PID is reclaimable immediately, but the reaper first claims the observed
directory and revalidates its filesystem identity plus owner so it cannot
delete a replacement generation after an ABA race. A recent ownerless
directory retains a publication-race grace period. File locks use
`fcntl.flock` on supported POSIX hosts and never silently proceed unlocked.

### 3.3 SessionStart

For an identified task, SessionStart:

1. Preflights `.living`, `.mycelium`, `run`, host, and session directories.
2. Acquires the repository lifecycle lock, then the session lock.
3. Reuses a valid active transaction for the same owner on resume/compaction.
4. Otherwise reserves the next unused daily log slot while holding the global
   lock, creates the log without overwriting an occupied path, publishes the
   owner token, then atomically publishes the versioned active marker.
5. Writes session-local Git and `.living` baselines.
6. Emits the exact session-local handoff path in automatic context.

A different root task creates a different run directory and log. It never
reads, deletes, archives, or supersedes another task's live state.

### 3.4 PostToolUse

An identified PostToolUse event must find an existing run directory and valid
active marker before any state mutation. After acquiring the per-session lock,
it revalidates owner and marker to close the Stop/cleanup race. A late event
after accepted Stop exits silently and cannot recreate the run directory.

Activity, reminders, baselines, read telemetry used by lifecycle enforcement,
and raw lineage are either session-local or protected append operations. Bash
and edit hooks from different tasks cannot pollute each other's evidence.

### 3.5 Stop and handoff publication

Stop acquires the repository lifecycle lock, then the target session lock, and
revalidates ownership before mutation. All current acceptance boundaries stay
intact: blocked or partially failed Stop retains the log, marker, owner,
baselines, raw lineage, retry timestamp, and session-local handoff.

The task writes its five-section handoff to its session directory. A complete
handoff contains each required heading exactly once, in canonical order, with a
nonblank body in every section; otherwise Stop atomically substitutes its
deterministic complete fallback. After log, registry, lineage, and handoff
finalization succeed, Stop atomically publishes that content to shared
`.mycelium/last-session.md`. Therefore simultaneous tasks cannot overwrite one
another's in-progress handoffs; the globally visible handoff is simply the most
recently accepted Stop.

Accepted cleanup removes only known files from that session directory and uses
`rmdir` for an empty directory. It never recursively deletes a path derived
from repository state. Abandoned directories remain for audit/retry.

### 3.6 Shared durable writers

Atomic replacement alone prevents torn bytes but not lost updates. Every shared
read-modify-write must hold a stable lock for its complete derivation:

- `LOG_REGISTRY.md` upserts;
- findings and todo table-registry upserts;
- `generate_index.py` reads plus `INDEX.md` replacement;
- `crystallize_findings.py` source scan plus registry/index replacement;
- the complete destructive knowledge-map build.

Atomic replacement preserves an existing target's permissions. Managed target
and lock ancestors must be non-symlink directories, and targets must be absent
or non-symlink regular files. A helper failure leaves the original bytes and
permissions unchanged.

Agent instructions must route shared table mutations through the locked helper.
Append-only prose files may use an explicit locked append helper; ordinary
read/edit/write sequences are not described as concurrency-safe.

## 4. Compatibility and migration

- Existing initialized repositories require no migration for runtime safety:
  `.mycelium` already ignores all runtime descendants, and automatic lifecycle
  context supplies the authoritative private handoff path. The idempotent
  migrator should still refresh any generated `MYCELIUM.md` rule that names the
  legacy shared handoff path.
- Existing settings and hook approvals remain valid; hook registration and
  command paths do not change.
- A pre-upgrade active flat transaction continues when its owner ID matches.
- Identity-free/older hosts retain flat single-transaction behavior.
- Existing shared `.mycelium/last-session.md` remains the resume-context source.
- The upgrade is additive and does not rewrite `.living` content.

## 5. Failure boundaries

- Invalid session ID—including a non-string JSON value or `.`/`..`: no run
  directory, marker, log, or shared write.
- Unsafe run/lock path: no mutation anywhere in the operation.
- Busy session/lifecycle lock: Stop blocks and preserves evidence; other hook
  events fail silently only where host contracts require non-blocking hooks.
- Failed shared writer: original bytes and mode remain; Stop retains active
  retry state when that writer is part of finalization.
- Missing scoped state on an identified late event: silent no-op, never legacy
  fallback.
- One session's corrupt state cannot authorize mutation of another session.

## 6. Required regression evidence

### Focused red/green tests

- Two distinct host session IDs started concurrently create two run dirs, two
  active markers, and two unique logs.
- The same host session ID started twice reuses one transaction.
- A subagent retaining its parent's ID contributes activity but cannot finalize
  the root transaction.
- Concurrent Stop calls for one ID finalize exactly once.
- Concurrent accepted Stops for two IDs produce two logs and two registry rows
  without losing authored fields.
- A blocked Stop for one ID does not block, clean, or satisfy another ID.
- Late events from an accepted ID do not recreate its run directory or mutate a
  still-active sibling.
- Malformed, non-string, `.`/`..`, colliding-after-sanitization, overlong, and
  linked IDs are rejected.
- Pre-upgrade matching flat state completes; a nonmatching new ID gets scoped
  state without touching it.
- Shared writer races preserve every distinct row and valid final bytes.
- Injected write/replace failures preserve original bytes and mode.

### Full local gate

- Complete Python compatibility and knowledge-map suites.
- Stop, hook-stress, and integration-stress shell suites.
- Fresh init/validation plus dry-run, actual, and idempotent legacy migration.
- Shell syntax, Python compile, JSON/YAML/manifest/metadata checks, and final
  `git diff --check`.

### Real-host gate

Against one frozen installed candidate:

- Fresh Codex task and fresh Claude Code task dispatch hooks naturally.
- Each host proves SessionStart context, positive/negative PostToolUse, edit
  tracking, blocked Stop, compliant retry, handoff, lineage, and cleanup.
- Two simultaneous root tasks in the same disposable checkout prove distinct
  run directories and exact-once independent finalization.
- Source and installed exercised files hash identically before and after each
  host run.

## 7. Documentation changes

- Update lifecycle documentation and changelog with the new state layout.
- Replace the single-repository-owner checklist invariant with one transaction
  per identified root task plus shared durable-writer coordination.
- Add the underlying regression pattern: **global safety serialization was
  mistaken for functional multi-session support**.
- Document that existing projects do not need migration for runtime safety or
  hook reapproval, while recommending migration to refresh stale generated
  handoff guidance.
