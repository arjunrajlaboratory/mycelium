# Design: observed file changes for data lineage (provenance item 2)

Status: revision 3, after a second round of the same two reviews. Revision 3
overrides revision 2 wherever they differ; the revision-2 text below is kept
for the reasoning it records. Branch `worktree-lineage-observed-io`, base
`2694878`. Source request:
`~/code/BioReasoning/docs/mycelium-provenance-prompt.md`, item 2.

## Revision 3 (overrides the sections below)

| Round-2 finding | Revision 3 |
| --- | --- |
| Observations routed through `lineage-skips-unowned.ndjson` are deleted as gaps on the no-transaction Stop path and become fake analysis events on the owned path | Observations never enter that channel. Each host session has its own append-only store, `.mycelium/io/obs/<host>/<session>.ndjson`, which is the observation record of truth. The extractor joins referenced observations into a manifest by `observation_id`; nothing is moved or deleted at Stop. |
| Observations in the event stream count as session work | Observations never enter the event stream. A Bash call that changed files but produced no lineage record gets a `data_command` with `signals: ["observed-change"]` and the `observation_id`, through the existing data-command paths (owned stream or unowned parking), which already are not work and never reopen. |
| Journal and snapshot cleanup runs only on owned Stops; abandoned sessions and other roots never clean up | No Stop duty. Every journal append expires open intervals past an age bound (2 h ordinary, 24 h background) with a closed end, and compacts to a watermark (the earliest open interval) under its own lock. Each Post sweeps Pre snapshots older than 24 h for any session, writing an `unavailable: call-unresolved` observation into that session's store before deleting the snapshot. |
| Background intervals closed at per-turn Stop hide later overlap | Background intervals stay open until completion evidence or the 24 h bound. |
| Post closes its interval before walking | The Post walk runs first; the interval closes after it (implemented). |
| Pre-run proof judged racy against hash time | Judged against the Pre snapshot's own stat time (`taken_ns`), as git's racy rule does (implemented). |
| Late Codex Post not reconciled with the earlier unresolved gap | The late observation keeps the original `observation_id` (`<host>:<tool_use_id>`) and status `background`. A sweep-written `call-unresolved` record and a later real observation share the id; readers take the real one. |
| Blocking `flock` can stall unrelated hooks | Locks are taken with a bounded wait (non-blocking retries, 2 s); a timeout records `unavailable: lock-timeout` instead of waiting. Observation files never share a lock with the unowned-skip drain. |
| Codex `workdir` outside the session's repository | The observation names its `scope.root`; a command whose `workdir` lies outside it is marked `unavailable: workdir-outside-scope`. |
| Two walks per call is a real cost | Measured with the module: Pre step about 120 ms warm / 360 ms cold on SNP-tree (16,628 watched files after environment excludes), about 40 / 240 ms on AP1-learning (5,909). Reported to the user with the per-call totals once the hooks are wired. |

Decisions taken with the user: inputs are inferred and their pre-run content is
proven where possible (observed reads move to item 7's in-process helper); the
watched scope is the whole repository minus excludes.

## What changed from revision 1

| Review finding | Revision 2 |
| --- | --- |
| A repository listing rewritten at each Post is not a pre-run baseline (between-call edits, deletions, parallel calls) | PreToolUse takes a real stat snapshot of its own; no shared listing |
| ctime vs `time.time_ns()` is unsound (coarse clocks, NFS, skew, coarse granularity) | No clock comparison: changes are differences between two stat snapshots; coarse or racy identities are "unknown" |
| `observed` cannot promise exclusivity (unhooked writers, background jobs, cleared in-flight entries) | Status `no-known-overlap`; changes are candidates; an append-only call journal keeps every interval until Stop |
| One call-level diff attached to each script event of `a.py && b.py` | One observation record per Bash invocation; events reference it |
| Codex background terminals give no Post until a later `write_stdin`, under the original id | Unmatched Pre snapshots persist; Stop reports them as unresolved; a late Post reconciles once |
| PostToolUseFailure through the current decoder would record exit 0 | Event-name branch; exit parsed from `error` ("Exit code N") or null with `failed: true`; fixture first |
| Stop's normalizers drop new keys and fill null hashes from current disk content | Normalizers pass observation keys through; no disk enrichment for any record carrying `hash_status` |
| Named paths promoted to inputs | Withdrawn: `named_paths` stay directionless (pattern 45) |
| Hashing has no stability protocol; byte budget does not bound time | fstat-before/after plus path identity; racy-clean rule; per-call time budget; deferral |
| Observation tied to session lock and ownership exits | Observation runs first, under no session lock, and is written whether or not a transaction exists |

## What a hook can observe

A hook sees the filesystem before and after a call; it cannot see reads. So
item 2 delivers **observed file changes** (create, modify, delete, rename
candidates) and **inferred inputs with proven pre-run content where the proof
holds**. `inputs[].source` is never `observed` from a hook.

## Mechanism

### PreToolUse (Bash, and edit tools for the journal only)

1. Append `{kind: "pre", id, host, session, agent, tool, t_wall}` to the
   repository call journal `.mycelium/io/calls.ndjson` (stable lock, append).
2. For Bash only: walk the watched scope and write the stat snapshot
   (path → dev, ino, size, mtime_ns, ctime_ns, mode) with `marshal` to
   `.mycelium/io/pre/<host>/<session>/<id>.snap` (atomic write). Walk time is
   bounded (default 3 s); on timeout the snapshot is written as `partial` and
   the observation becomes `unavailable: walk-timeout`.
3. Always exit 0, emit nothing, never emit a permission decision.

### Post (Bash PostToolUse, Claude Bash PostToolUseFailure, late Codex completion)

1. Append `{kind: "post", id, t_wall, outcome}` to the journal.
2. Load the Pre snapshot for the id (missing → `unavailable: no-pre-snapshot`).
3. Walk again; diff the two snapshots by path:
   - `created`: only in post. `deleted`: only in pre. `modified`: stat tuple
     differs and (when both hashes are obtainable) content differs.
     Metadata-only changes (chmod; equal hash) are listed under
     `metadata_only`, not as modifications.
   - Rename candidates: a deleted and a created path with equal
     (dev, ino, size, mtime_ns). Recorded as `rename_candidate`, never as
     proof; directory moves show up as delete/create sets.
   - Change lists are capped (default 1,000 paths) with exact counts; a
     larger set (`pip install`, `git checkout`) is summarized by top-level
     directory.
4. Overlap: every other journal interval that intersects [pre, post] (any
   host, session, agent, tool), including open intervals (no post yet) and
   intervals closed by Stop as `abandoned`, is listed in `overlapping`.
5. Hash created/modified files and inferred inputs within the time budget
   (below). The "before" hash of a modified file is the cache entry whose
   identity equals the Pre stat identity; otherwise `before.sha256` is null
   with `hash_status: "not-captured"`.
6. Write one observation record (below) and delete the snapshot.

The observation step runs before `mycelium_prepare_locked_post_tool_state`, so
a session-lock timeout, a missing transaction, or an ownership exit never
skips it. Its record goes to the shared, host-session-keyed record file Stop
already drains (`lineage-skips-unowned.ndjson` channel and its lock), so it
needs no transaction and never reopens one (pattern 45).

### Inputs (inferred, pre-run content proven where possible)

For each inferred input (static scan of the script, shell-certain redirection
or `cp`/`mv` source): if its identity (dev, ino, size, mtime_ns, ctime_ns) is
equal in the Pre and Post snapshots, not racy, and the hash protocol below
succeeds with the identity unchanged after hashing, the record carries
`pre_run_proven: true` and that hash. Otherwise the hash is recorded as of Post
with `pre_run_proven: false` and a reason (`changed-during-call`,
`outside-scope`, `racy-timestamp`, `not-hashed`).

### Hashing protocol

- Open with `O_NOFOLLOW|O_NONBLOCK`, require a regular file, `fstat` before
  and after reading; the result is kept only if (dev, ino, size, mtime_ns,
  ctime_ns) are unchanged and equal to the path's `lstat` identity.
- Racy-clean rule (as in git): an identity whose ctime is within the
  filesystem's timestamp granularity of the hash start is not cached and
  does not count as proven.
- Cache: `.mycelium/io/hash-cache.ndjson`-backed dict keyed by (dev, ino,
  size, mtime_ns, ctime_ns) → sha256, compacted at Stop (no sqlite: avoids
  side files that bypass `O_NOFOLLOW` and NFS locking issues).
- Per-call time budget (default 2 s of hashing). Files left over get
  `sha256: null, hash_status: "pending"` with their identity. Stop extraction
  resolves `pending` only by hashing under the same protocol when the identity
  still matches, within its own budget; otherwise `stale`. Never a hash of
  later content.
- `SIZE_LIMIT_BYTES` no longer suppresses lineage hashes; it remains for
  embedding.
- macOS dataless (cloud) files (`st_flags & SF_DATALESS`) are never read:
  `hash_status: "dataless"`.

## Observation record

```json
{
  "kind": "observation", "observation_id": "<host>:<tool_use_id>",
  "host": "claude", "host_session_id": "...", "agent_id": null,
  "tool": "Bash", "bash_cmd": "...", "outcome": "success|failure|unknown",
  "bash_exit": 0, "window": {"pre_wall": "...", "post_wall": "..."},
  "status": "no-known-overlap|overlap|background|unavailable",
  "reason": null, "overlapping": [{"id": "...", "tool": "Edit", "open": false}],
  "scope": {"entries": 21455, "unwatched": ["data/raw -> /Volumes/x"],
            "truncated": false, "walk_ms": [102, 98]},
  "changes": {"created": [...], "modified": [...], "deleted": [...],
              "rename_candidates": [...], "metadata_only": [...],
              "counts": {...}, "capped": false}
}
```

Each change entry: `path`, `before`/`after` `{sha256, size_bytes,
hash_status}`.

Analysis events and `data_command` records gain
`observation_id` and a copy of `status`. Their `outputs[]` entries gain
`source` (`inferred-static`, `inferred-repository`, `inferred-shell`) and,
at extraction, `observed_change` when the observation lists the same path.
Script-level attribution of a multi-script command stays unresolved: the
observation belongs to the invocation, not to either script.

## Background and asynchronous calls

- Claude `run_in_background: true`, or a command whose shell structure starts
  a job (`&`, `nohup`, `setsid`, `sbatch`, `qsub`, `screen`/`tmux` new
  sessions): status `background`; the journal interval stays open, so later
  calls list it under `overlapping` until Stop closes it as `abandoned`.
- Codex running terminals: no Post arrives; the Pre snapshot persists.
  Stop records each unmatched Pre as a `call-unresolved` gap. A late Post for
  the same id (after `write_stdin`) produces the observation then, once;
  the snapshot is deleted only after its observation is durable. Snapshots
  older than 24 h are dropped by Stop with a recorded gap.
- `sbatch`/`qsub` outputs are never observed (they run elsewhere and later).

## Failed calls

Claude: the data tracker and the observer also register on
`PostToolUseFailure` (matcher `Bash`). The tracker branches on
`hook_event_name`: exit comes from `error` matching `^Exit code (\d+)`, else
null with `failed: true`; it never defaults to 0 on this path. Codex: ordinary
nonzero exits still reach PostToolUse; handler errors and cancellation leave
an unmatched Pre (above).

## Scope

Whole repository root, gitignored data included. Excluded: `.git`,
`.mycelium`, `.living`, `.claude`, `.codex`, `.venv`/`venv` with `pyvenv.cfg`,
conda environments (`conda-meta/`), `node_modules`, `__pycache__`,
`.ipynb_checkpoints`, `.snakemake`, `renv/library`, `.DS_Store`, `._*`,
nested Git repositories and worktrees (a directory with its own `.git`).
Symlinked directories and other devices are not followed and are listed in
`scope.unwatched`; inferred paths there get `outside-scope`. A project can
add roots or excludes in `.mycelium/io/scope`. Entry cap 250,000 and the
walk-time bound both yield `unavailable` with a reason, never an empty
change list.

## Lifecycle interaction

- Observation records are not session work and never reopen (pattern 45).
  Session-work accounting stays with `session_file_changes.py`; the two are
  not merged in this item.
- Stop (`mycelium-data-lineage-stop.sh` / `extract_data_lineage.py`) moves a
  session's observation records into its manifest under `observations`,
  closes its open journal intervals as `abandoned`, records unmatched Pre
  snapshots as `call-unresolved` skips, and keeps unrelated sessions' entries.
- `normalize_event` / `normalize_data_command` pass through `observation_id`,
  `status`, `source`, `hash_status`, `pre_run_proven`, `observed_change`;
  the disk-enrichment fallback skips any entry that has `hash_status`;
  `n_rows_if_tabular` is skipped above a size bound.

## Hooks and registration

- New `skills/core/hooks/mycelium-io-observer.sh` (modes `pre`, `post`).
  Bash Post observation runs inside `mycelium-data-tracker.sh` first, so the
  lineage record can carry `observation_id`; edit-tool Post only closes the
  journal interval.
- Claude (`init_repo.install_claude_hooks`): PreToolUse
  `Bash|Edit|Write|MultiEdit|NotebookEdit` → observer `pre`; PostToolUse
  `Edit|Write|MultiEdit|NotebookEdit` → observer `post`; PostToolUseFailure
  `Bash` → data tracker; PostToolUseFailure edit matcher → observer `post`.
  Migration adds them.
- Codex (`hooks/hooks.json`): PreToolUse `Bash|apply_patch` → observer;
  apply_patch Post → observer. The dispatcher allowlist gains the observer.
  The cross-host checklist's "five Codex registrations" item and its test are
  updated. Changing `hooks.json` changes the trusted bundle hash: release
  notes say Codex users re-trust hooks.
- Antigravity: no PreToolUse; observations `unavailable: host-has-no-pre-hook`.

## State safety

All files under `.mycelium/io/` after the existing shared-state containment
check; per-call snapshot files under host/session directories validated like
run directories (pattern 29); appends under stable `fcntl` locks with
`O_NOFOLLOW|O_NONBLOCK` regular-file checks (the `_append_with_stable_lock`
helper); atomic replace for compaction; NUL checks on payload-derived paths.

## Cost (to measure and report)

Two walks per Bash call: about 100–130 ms each warm and 0.4–0.55 s cold on
SNP-tree / AP1-learning, plus interpreter starts. Reported per phase, warm
and cold, cached and uncached hashing, against the current tracker.

## Tests (red first)

Python script, R script, inline heredoc, `cp`, `awk > out`; failed script with
a partial output (Claude failure payload fixture); `run_in_background`;
`nohup … &`; parallel Bash calls with overlapping intervals where one
finishes first; an Edit beside a Bash call; a between-call edit (must not
appear); rename, delete, chmod-only; an input rewritten during the call; a
racy timestamp; an over-budget file resolved at Stop and one changed before
hashing; missing Pre snapshot; unmatched Pre at Stop and a late Post after
it; scope over cap and walk timeout; symlinked `.mycelium/io`; symlinked data
directory (unwatched, listed); a `git checkout`-sized change set (capped);
lock-timeout and no-transaction paths still produce the observation;
old-format events through `extract_data_lineage.py`; normalizers keep the new
keys and never enrich `pending`/`stale` entries; R static scan with
near-neighbour negatives (done: `test_static_scan_languages.py`).
