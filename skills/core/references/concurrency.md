# Concurrency: multiple chats in one workspace

Mycelium supports several Claude chats (and their subagents) working in the
**same working tree at the same time**, sharing **one** `.living/` knowledge
base. The design principle is **coordinate, don't isolate**: the shared
knowledge stays shared; only the transient per-session bookkeeping is scoped,
and every writer to a shared file takes a lock.

## Session identity

Hooks key their runtime state on the Claude `session_id` from the hook stdin
payload:

- **Subagents share their parent's `session_id`** → they group into the
  parent's session automatically (one session log, shared activity).
- **Independent chats get distinct `session_id`s** → they never see each
  other's sentinels.

`skills/core/hooks/mycelium-run-paths.sh` is sourced by every hook and points
the sentinels at `.claude/mycelium/run/<session_id>/`:

```
.claude/mycelium/run/<session_id>/
  active-session-log.tmp    session-start-ts.tmp
  mycelium-reminded.tmp     mycelium-session-activity.tmp
  mycelium-data-events.tmp
```

With no `session_id` (older Claude Code, or unit tests) it falls back to the
legacy flat `.claude/*.tmp` paths, so behaviour is unchanged. `.claude/mycelium/`
is transient and git-ignored; `mycelium-health.sh` reaps finished/crashed
session dirs on the next SessionStart.

## Writing shared `.living/` files

Any read-modify-write of a shared file must be serialised. Use the helpers in
`skills/core/scripts/`:

| Helper | Use for |
|--------|---------|
| `mycelium_locks.file_lock(path)` | wrap a critical section (`fcntl.flock` on `<path>.lock`) |
| `mycelium_locks.atomic_write(path, data)` | tempfile + `os.replace` |
| `allocate_session_slot.py` | claim the next `YYYY-MM-DD-NNN` log slot atomically (O_EXCL) |
| `upsert_registry_row.py` | locked upsert into `LOG_REGISTRY.md` |
| `upsert_manifest_row.py` | locked upsert into any markdown-table registry (manifests, TODO, FINDINGS) |

**Agents:** when a step says to update a manifest or the findings registry,
upsert the row with `upsert_manifest_row.py` instead of hand-editing with the
Edit tool — two chats hand-editing the same table clobber each other, but the
helper locks and writes atomically.

Already-locked script writers: `generate_index.py` (INDEX.md),
`crystallize_findings.py` (findings registry + INDEX), `knowledge_map/cli.py`
(the whole graph/vault build, one exclusive `.build.lock`).

## Genuinely safe without locks

Append-only logs — `.living/learnings.md`, `.living/decisions.md`,
`.living/conventions.md`, session-log `## Session Summary`, and the
`fcntl.flock`-protected data-lineage NDJSON — tolerate concurrent writers
(POSIX `O_APPEND` never overwrites; at worst entries interleave).
