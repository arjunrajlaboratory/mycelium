# Mycelium Injection Events Log — Schema v1

**File**: `${REPO_ROOT}/.claude/mycelium-injection-events.log`
**Format**: TSV, append-only, 8 columns, no header.
**Hook version string**: `phase1.5-events-v1` (column 8). Bump on any schema change.

## Columns

| # | Name | Type | Allowed values | Notes |
|---|------|------|----------------|-------|
| 1 | TIMESTAMP | string | ISO 8601 local time, second precision | matches error-log format |
| 2 | HOOK | enum | `edit-injector`, `dispatch-injector` | |
| 3 | TOOL | enum | `Edit`, `Write`, `MultiEdit`, `Agent` | |
| 4 | TARGET | string | relative path (edit) OR `subagent_type` value (dispatch) OR `unknown` (dispatch fallback) | sanitized: tabs/newlines → space |
| 5 | SESSION_ID | string | from Claude Code input | sanitized |
| 6 | OUTCOME | enum | `fired`, `dedup-skip`, `excluded-skip` | `excluded-skip` is dispatch-only |
| 7 | DETAIL | string | see below | sanitized; may be empty |
| 8 | HOOK_VERSION | string | `phase1.5-events-v1` | parsers warn-and-skip on unknown |

## DETAIL conventions

- edit-injector + `fired`: `<matched_domains_csv>;bytes=<N>` where `bytes` is `wc -c` of the rendered learnings payload.
- edit-injector + `dedup-skip`: empty.
- dispatch-injector + `fired`: `bytes=<N>` where `bytes` is `wc -c` of the post-truncation menu content.
- dispatch-injector + `dedup-skip`: empty.
- dispatch-injector + `excluded-skip`: `subagent_type=<value>`.

## TSV escape rule

Any string field that may contain `\t`, `\r`, or `\n` is sanitized: each such byte is replaced by a single ASCII space. This is enforced by `_mycelium_tsv_sanitize` in `skills/core/hooks/_lib/log_rotation.sh`.

## Concurrency

Writers use `_mycelium_append_log_locked` which holds an exclusive `flock` on `${logfile}.lock` while the rotate-check and the append happen. Without `flock`, writers fall back to best-effort plain append.

## Reconciliation formula (for audit worksheet)

For a given canary repo and time window:

```
total_edit_invocations ≈ count(fired, edit) + count(dedup-skip, edit) + count(match-edit-empty-output errors)
total_dispatch_invocations ≈ count(fired, dispatch) + count(dedup-skip, dispatch) + count(excluded-skip, dispatch)
```

Note that edit-injector marks dedup BEFORE running `match_edit.py`. A match-empty result therefore produces no `fired` row but still contributes to dedup state; subsequent runs become `dedup-skip`. The error log captures these as `match-edit-empty-output` rows.

## Version policy

Increment `phase1.5-events-v1` on any of: column add/remove/reorder, type change, allowed-value expansion, semantics change. Old logs (with old `HOOK_VERSION`) remain readable by their original-schema parsers.
