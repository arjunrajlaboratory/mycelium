# DO NOT MERGE

This branch (`feat/accessibility-phase1`) is preserved for **side-by-side comparison** against the post-PR-#31 main architecture, not for merging.

## Strategic divergence

This branch implements the **push-channel** accessibility approach: PreToolUse Edit/Agent injectors that prepend matched learnings (L3) and the L1 menu into tool inputs, plus an events log measuring fire/dedup-skip/excluded-skip outcomes.

Main pivoted (PR #31, "Make learnings actually reachable") to a **pull-better** approach: heuristic INDEX summary at SessionStart, `recall_lessons.py` CLI for targeted lookup, CLAUDE.md re-anchored on `INDEX.md`. PR #31 deletes the files this branch modifies and depends on (`match_edit.py`, `mycelium-edit-injector.sh`, `mycelium-dispatch-injector.sh`, `_lib/log_rotation.sh`, `mycelium-bash-access.sh`).

The two architectures encode different diagnoses of the same baseline (97% of Edit/Write events with no preceding learnings/findings Read). Both may be partially correct; neither should be selected by merge.

## What this branch captured

Phase 1.5 audit window (2026-04-25T18:32 → 2026-04-29T23:59, ~4.5 days) in one canary (`Scientific Claims Knowledge Graph`):

- **296 successful push fires** (213 dispatch-injector, 83 edit-injector)
- 244 dedup-skips (rate-limiting working)
- 0 logged jq/menu-empty/match-empty errors
- Push delivery beat pull access ~4× in the same window (296 fires vs 72 Read-tool .living/ events)

Events log schema: `docs/spec/events-log-schema-v1.md`. Audit plan + reconciliation formula: `docs/plans/2026-04-25-injection-success-logging.md`.

## Status

Pinned during the experimental comparison window. The intended next step is to run upstream main's pull-better architecture in a second canary and compare head-to-head; the verdict on push vs pull (or hybrid) will come from that comparison, not from a merge.

**Please do not merge** without explicit coordination. The architectural divergence with main is intentional during the comparison window.
