# Accessibility Phase 1.5 — Pre-Audit Fixes

**Status**: Draft
**Date**: 2026-04-22
**Depends on**: [Phase 1 design](2026-04-22-accessibility-architecture.md), [Phase 1 plan](../plans/2026-04-22-accessibility-architecture-phase1.md)
**Target audit window**: 2026-04-22 → 2026-04-29
**Supersedes**: None

---

## 1. Context

Phase 1 shipped the three-layer accessibility architecture (L1 SessionStart menu, L2 domain files, L3 PreToolUse/Edit push) to a single canary repo (Scientific Claims Knowledge Graph). Post-deployment review identified two defects that would bias the Day-7 audit if the 7-day observation window starts without them being addressed:

1. **Curator-written `Triggers` field is underweighted relative to curator intent.** Phase 1 does honor triggers (+2), but at the same weight as tags, which erases the schema's specificity signal. A "relevance too low" audit outcome would conflate an under-calibrated scorer with a weak architecture.
2. **Subagent dispatches participate in ~96% of all edit activity** (14-day KG history: 673 subagent sessions vs 28 top-level sessions) but currently see no mycelium context. Without subagent injection, the audit measures the architecture on ~4% of its intended surface.

Phase 1.5 is a narrow pre-audit patch. It does not change Phase 1's layering; it corrects two measurement-biasing defects that would otherwise make the audit non-actionable.

## 2. Goal & Scope

### 2.1 In scope

- **Item A — Triggers weight differentiation.** Raise tokenized trigger match from +2 to +3 in `score_entry()`. Keep tokenization and list-parsing semantics unchanged. Phase 1.5 improves ranking calibration; it does NOT introduce identifier-phrase semantics (see §3.6).
- **Item B — Menu injection on subagent dispatch.** New PreToolUse hook on the `Agent` tool that prepends the current `.living/MENU.md` to the dispatched prompt via `updatedInput.prompt`, giving subagents the same routing surface top-level sessions receive at SessionStart.

### 2.2 Out of scope

- Exact-phrase trigger matching (bundled into Phase 2)
- Path extraction from dispatch prompts + push injection (B variant; explicitly rejected — see §2.4)
- Full monolith → domain migration (Phase 2)
- CLAUDE.md rules promotion (Phase 2)
- Broader rollout beyond KG canary (Phase 2)
- New push-active domains (Phase 2)
- Menu format upgrade (Phase 2, conditional on audit)

### 2.3 Success criteria

- Scorer change verifiable in isolation: tests demonstrate `Triggers`-matched entries outrank `Tags`-only-matched entries when the path token is present in both.
- Hook delivery verifiable: ≥95% of subagent dispatch events during the 7-day window contain `<mycelium-menu>` at the top of `updatedInput.prompt`.
- No regression in Phase 1 contracts: all 105 existing tests continue to pass; existing SessionStart menu, PreToolUse/Edit push, and dedup behavior unchanged.
- Error rate from new hook < 1% of Agent-dispatch fires, logged to `.claude/mycelium-hook-errors.log` with rotation.

### 2.4 Explicit non-goal: push in subagent context

The dispatch hook delivers **L1 only** (menu), not L3 (push). The subagent must pull domain files itself — this mirrors the parent at SessionStart. Auto-pushing based on path extraction from free-form prompts is rejected for two reasons: (1) it contaminates the audit by confounding "did progressive disclosure work?" with "did our path-extraction heuristic work?"; (2) it reproduces the L3 safety-net mechanism in a context where the failure mode it exists to patch (parent doesn't read at edit time) isn't yet known to occur.

---

## 3. Item A — Triggers Activation

### 3.1 Current behavior

`match_edit.py:score_entry()` currently awards the same score for tag matches and trigger matches:

```python
for tok in path_tokens:
    if tok in title_tokens:
        score += 3
    if tok in tag_tokens:
        score += 2
    if tok in trigger_tokens:
        score += 2      # ← tied with tags
    if tok in body_tokens:
        score += 1
```

Seed data shows curators write triggers as specific identifiers (`batch_extract`, `theme_viz`, `panel_`, `FancyArrowPatch`) while tags describe broader themes (`prompt-engineering`, `schema`, `matplotlib`). The current weights do not reflect this specificity.

### 3.2 Change

Raise the trigger weight from `+2` to `+3`:

```python
for tok in path_tokens:
    if tok in title_tokens:
        score += 3
    if tok in tag_tokens:
        score += 2
    if tok in trigger_tokens:
        score += 3      # was +2
    if tok in body_tokens:
        score += 1
```

No other changes. Tokenization, list-parsing, recency bonus, threshold, and truncation logic remain as Phase 1.

### 3.3 Interaction with threshold

`SCORE_THRESHOLD = 2.0` is unchanged. The weight change only affects relative ranking among matched entries and the margin above threshold. Entries that previously scored above threshold continue to do so; entries that previously scored below may now clear it only if they had a trigger match. This is the intended effect.

### 3.4 Tests

New unit tests in `test_match_edit.py`:
- `test_trigger_outranks_tag_same_token` — path token matches an entry's trigger and a different entry's tag; trigger-matched entry scores higher.
- `test_trigger_weight_is_3` — direct numeric assertion for a single-token path with one trigger match.
- `test_trigger_plus_title_stacks` — trigger match plus title match composes to +6 (not capped).
- `test_tag_weight_unchanged` — regression pin on the +2 tag weight.

All existing tests continue to pass without modification. Any existing test that pins a composite score through a trigger match will need its expected value updated — this is the only expected breakage and is captured in task-level TDD.

### 3.5 Rollback

Single-line revert. No migration, no state, no coordinated rollback with the hook.

### 3.6 Known limitation — tokenization still erases identifier phrases

The weight bump is applied to *tokenized* trigger matches. `theme_viz` is tokenized to `{theme, viz}`; an edit to `viz/random.py` would match the single token `viz` and receive +3, equal to a title hit. This is an audit-fairness trade: Phase 1.5 improves *ranking calibration* so curator-written triggers outrank curator-written tags at equal match specificity, but it does NOT test whether compound identifiers preserve their phrase semantics. Exact-phrase trigger matching at +3 with tokenized fallback at +2 remains the Phase 2 candidate (see §8.3). If the audit shows false-positives clustering on single-token trigger matches, that is the signal to advance Phase 2 item 3.

---

## 4. Item B — Dispatch Menu Injection

### 4.1 Current behavior

Parents receive `<mycelium-menu>` at SessionStart via `mycelium-health.sh`. Subagents dispatched via the `Agent` tool receive a fresh context containing only the prompt the parent passes. No mycelium content reaches them.

### 4.1.1 Mechanism selection

Two Claude Code primitives were considered for subagent-context injection:

| Primitive | Supports `additionalContext`? | Mutates parent prompt? | Decision-control complexity |
|-----------|-------------------------------|------------------------|------------------------------|
| `SubagentStart` hook | **No** — observability-only per current docs | N/A | None (cannot inject) |
| `PreToolUse` + `Agent` matcher | No, but supports `updatedInput` | Yes (prompt field is rewritten) | Requires `permissionDecision: "allow"` |

`SubagentStart` would be architecturally symmetric with `SessionStart` but does not currently permit context injection. `PreToolUse`/`Agent` is the only production primitive available. Phase 1.5 uses it, accepting that this mutates the parent-authored prompt in exchange for the subagent receiving L1 context. If Claude Code later adds `additionalContext` support to `SubagentStart`, Phase 2 should migrate; the mycelium menu generator and discovery logic are mechanism-agnostic.

### 4.2 Design

New hook: `skills/core/hooks/mycelium-dispatch-injector.sh`, registered as a PreToolUse hook with matcher `Agent`. The hook:

1. Reads the tool-call JSON from stdin (Claude Code contract).
2. Extracts the dispatched prompt from `tool_input.prompt`.
3. Locates the nearest enclosing mycelium-enabled repo (walk up from `cwd` looking for `.living/MENU.md`) — same discovery logic used by `mycelium-health.sh`.
4. Reads `.living/MENU.md`; if absent or unreadable, emits no modification and exits 0.
5. Emits JSON to stdout with `hookSpecificOutput.updatedInput.prompt` set to the concatenation:
   ```
   <mycelium-menu>
   {MENU.md contents}
   </mycelium-menu>

   {original prompt}
   ```

The modified prompt is what the subagent sees in its fresh context.

### 4.3 Hook contract

Input (stdin, from Claude Code):
```json
{
  "session_id": "...",
  "tool_name": "Agent",
  "tool_input": {
    "description": "...",
    "prompt": "...",
    "subagent_type": "..."
  }
}
```

Output (stdout):
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "updatedInput": {
      "description": "<copied verbatim from tool_input.description>",
      "subagent_type": "<copied verbatim from tool_input.subagent_type>",
      "prompt": "<mycelium-menu>\n{menu}\n</mycelium-menu>\n\n<original prompt>"
    }
  }
}
```

**Critical contract constraints** (derived from Claude Code + Agent SDK hook docs):

1. `permissionDecision: "allow"` MUST be emitted alongside `updatedInput`. Without it, input modification may be silently ignored by the Agent SDK enforcement path.
2. `updatedInput` REPLACES the entire `tool_input` object. Every field present in the input (`description`, `subagent_type`, `prompt`, plus any other fields Claude Code may add in future) MUST be echoed into `updatedInput` verbatim unless Phase 1.5 intentionally modifies it. For Phase 1.5, only `prompt` is modified; `description` and `subagent_type` are echoed unchanged.
3. Any field in `tool_input` that the hook does not recognize MUST be echoed through unchanged. The hook must iterate the input JSON rather than hardcode a whitelist, to remain forward-compatible with future Agent tool-input additions.
4. The hook MUST NOT emit `permissionDecision: "deny"` or `"ask"` under any circumstance. On error, emit nothing to stdout and exit 0 (silent-on-error, per §4.5).

### 4.4 Exclusions

The hook skips injection in two cases:
- **Already-injected prompts**: if `tool_input.prompt` already starts with `<mycelium-menu>`, skip. This prevents double-injection when a parent intentionally includes the menu itself, and protects against accidental chain-inheritance if subagents ever gain the ability to dispatch further subagents.
- **Subagent-excluded types**: a hardcoded set of `subagent_type` values that should NOT receive menu injection because they're either already mycelium-aware (`living-scribe`) or have no need for domain routing (`statusline-setup`). Initial exclusion list: `living-scribe`, `cost-tracker`, `statusline-setup`, `figure-qa`, `data-qa`, `stats-reviewer`, `pdf-gen`. Revisit after audit.

### 4.5 Error handling

Silent-on-error, matching Phase 1 conventions. On any failure (no MENU.md found, parse error, output write error), the hook exits 0 with no stdout (meaning: no modification) and logs a structured line to `.claude/mycelium-hook-errors.log` using the Phase 1 `_mycelium_rotate_error_log` helper. The dispatch proceeds with the original prompt. Never block a dispatch.

Log schema (append-only TSV):
```
2026-04-22T18:30:00	dispatch-injector	<session_id>	menu-empty	<details>	phase1-5.1
```

Columns (tab-separated): timestamp, hook_name, session_id, error_class, details, script_version. This matches the format used by mycelium-edit-injector.sh (with hook_name as the discriminator when both hooks write to the same log file).

### 4.6 Size budget

MENU.md is designed to be compact (one-line per domain). Phase 1 authorship target is ≤2KB. If MENU.md exceeds 8KB at injection time, the hook truncates to the first 8KB with a `… [truncated; Read .living/MENU.md]` marker and logs a structured warning. This is a hard cap independent of Phase 1's `DEFAULT_CAP_BYTES = 8000` (which applies to push content only).

### 4.7 Dedup

**None.** Unlike the edit-injector (which uses flock + SHA1 dedup to avoid re-pushing the same domain content on rapid re-edits), the dispatch-injector fires once per subagent dispatch and each subagent has its own fresh context. There is no duplication risk. Keeping dedup out also eliminates a class of state-corruption bugs and simplifies the hook.

### 4.8 Registration

The hook is registered in the canary's `.claude/settings.local.json` via an absolute path to the mycelium repo's canonical hook location — the same pattern Phase 1's edit-injector uses:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [{"type": "command", "command": "/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-edit-injector.sh"}]
      },
      {
        "matcher": "Agent",
        "hooks": [{"type": "command", "command": "/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-dispatch-injector.sh"}]
      }
    ]
  }
}
```

The hook script is NOT vendored into the canary's `.claude/hooks/`. It lives in the mycelium repo and the canary references it by absolute path. This matches the existing Phase 1 registration pattern and means Phase 1.5 scripts update in-place when the mycelium repo is updated, without requiring a sync step into each consumer repo.

### 4.9 Tests

**Unit tests** (new `test_dispatch_injector.sh`, following the style of `test_phase1_integration.sh`):
- `test_menu_prepended_when_present` — MENU.md exists, hook output contains `<mycelium-menu>` followed by original prompt.
- `test_no_modification_when_menu_absent` — no MENU.md, hook exits 0, stdout is empty or preserves input untouched.
- `test_skip_if_already_injected` — input prompt already starts with `<mycelium-menu>`; hook emits no modification.
- `test_excluded_subagent_type` — `subagent_type: "cost-tracker"` is in exclusion list; hook emits no modification.
- `test_truncation_at_8kb` — MENU.md artificially inflated to 12KB; hook truncates with marker.
- `test_error_logged_on_parse_failure` — malformed stdin; hook exits 0, error row appears in log.
- `test_no_modification_outside_repo` — `cwd` has no `.living/`; hook exits 0 cleanly.
- `test_other_tool_fields_preserved` — `description` and `subagent_type` in `tool_input` unchanged after hook.

**Live canary verification** (manual, documented in audit worksheet):
- Dispatch a subagent in the KG project; verify subagent's initial context contains `<mycelium-menu>` with the expected 5 domain entries.

### 4.10 Rollback

Single config change: remove the `Agent` matcher block from `.claude/settings.local.json`. The hook script itself can remain installed (inert). No data to unwind, no state file to clean.

---

## 5. Audit Integration

### 5.1 Day-7 audit worksheet updates

The existing worksheet at `Scientific Claims Knowledge Graph/todo/accessibility-phase1-audit.md` gains two new delivery check rows:

- [ ] ≥95% of sampled subagent-dispatch events contain `<mycelium-menu>` at the top of `tool_input.prompt` (as seen in session transcripts)
- [ ] Error rate from `dispatch-injector` in `.claude/mycelium-hook-errors.log` < 1% of Agent-dispatch fires

Precision review expands to cover both top-level and subagent sessions. The 10-session sample should include ≥3 subagent sessions to provide signal on whether L1 alone (menu without push) is sufficient to drive L2 pull behavior in dispatched contexts.

### 5.2 Diagnostic signal (NOT a go/no-go criterion): menu-to-pull conversion

Because subagents receive only the menu (not pushed entries), the audit gains a new *diagnostic* signal that Phase 1 could not observe: **does the menu alone cause agents to pull relevant domain files?** For each subagent session with menu injection, record:

- Did the subagent open at least one `.living/learnings/*.md` file during its turns? (yes/no)
- If yes, was the opened file's domain relevant to the dispatched task? (relevant/partial/irrelevant)

**This is explicitly a diagnostic signal, not a pass/fail input.** Phase 1.5 MUST NOT alter the go/no-go thresholds or failure-routing rules inherited from Phase 1 §10. The menu-to-pull signal exists to inform Phase 2 prioritization (particularly whether to advance subagent-aware push in Phase 2), not to gate the Phase 1 architecture decision. If this signal is low but Phase 1 criteria pass, the audit still passes.

### 5.3 Routing of audit outcomes

Phase 1.5 does not change the Phase 1 go/no-go thresholds or the failure-routing rules in §10 of the Phase 1 design. It only makes those measurements fair. The only delivery-check additions (§5.1) are about whether the menu and error-log contracts are honored for subagent dispatches — they share the same ≥95% / <1% thresholds Phase 1 already uses for parent contexts.

---

## 6. Risks & Rollback

### 6.1 Risks

| Risk | Likelihood | Severity | Mitigation |
|------|-----------|----------|------------|
| Hook breaks subagent dispatch (e.g., malformed JSON output) | Low | High | Silent-on-error contract + thorough hook-output tests before canary activation |
| Trigger weight bump unintentionally pushes sub-threshold entries above | Medium | Low | Re-run Phase 1 smoke test after scorer change; accepted trade-off per §3.3 |
| Menu bloats to > 8KB before audit concludes | Low | Medium | Hard truncation cap in hook (§4.6) + existing Phase 1 invariant that MENU.md regenerates to one-line-per-domain |
| Subagent sees `<mycelium-menu>` but is instructed to ignore it by parent prompt | Medium | Low | Acceptable; measurement still reveals whether the signal lands at all |
| Exclusion list gets stale as new subagent types land | Medium | Low | Exclusion list is plain-text, post-audit review step |

### 6.2 Rollback matrix

| Scenario | Action | Data loss |
|----------|--------|-----------|
| Audit reveals scorer delta made things worse | Single-line revert of trigger weight | None |
| Hook causes dispatch failures | Remove `Agent` matcher block from `settings.local.json` | None |
| Both fail | Revert scorer + remove matcher; Phase 1 continues unaffected | None |

Phase 1.5 is intentionally decoupled from Phase 1 commits — the scorer change is additive and isolated; the hook is a new file registered independently.

---

## 7. Open Questions & Implementation Prerequisites

### 7.1 Open questions

1. **Git strategy.** Phase 1 lives on `feat/accessibility-phase1` (27 commits ahead, pre-merge). Phase 1.5 options:
   - Branch off `feat/accessibility-phase1` as `feat/accessibility-phase1-5` (keeps pre-audit work physically adjacent to Phase 1).
   - Commit Phase 1.5 directly to `feat/accessibility-phase1` (one audit-ready PR at the end).
   - Branch off main after merging Phase 1 (requires merging before audit completes).
   - *Recommendation: commit directly to `feat/accessibility-phase1`; the audit evaluates the merged architecture and Phase 1.5 is a prerequisite for the audit, not a separate feature.*
2. **MENU.md 8KB cap vs Phase 1's 8000-byte push cap.** Should they be expressed as the same constant? Currently they're independent and serve different purposes, but the overlap in numerology is coincidental and may confuse future readers.
3. **Exclusion list location.** Should the `subagent_type` exclusion list live in the hook script (simple) or in MENU.md frontmatter / a separate YAML (introspectable)? *Recommendation: in the hook for Phase 1.5; revisit only if the list grows past ~10 entries.*

### 7.2 Implementation prerequisites (plan phase MUST address)

These are missing or ambiguous artifacts referenced in this spec that the implementation plan must explicitly handle:

1. **No `skills/core/templates/settings.local.json.template` exists today.** §4.8 references it as the template for hook registration. The plan must either (a) create the template as part of Phase 1.5, or (b) remove the template reference and document canary-only registration via direct edit of the KG project's `.claude/settings.local.json`. Recommendation: (b) — consistent with how Phase 1 handled it.
2. **Error-log rotation helper is not yet shared.** The `_mycelium_rotate_error_log` function defined in `mycelium-health.sh:32–45` is the only rotation implementation. §4.5 depends on it. The plan must either (a) extract it into a shared `skills/core/hooks/_lib/log_rotation.sh` sourced by both hooks, or (b) duplicate the function verbatim into the new dispatch-injector. Recommendation: (a) — one source of truth prevents divergence.
3. **No `.living/MENU.md` format spec embedded in this document.** MENU.md is specified in Phase 1 design §4; the dispatch hook depends on that contract being stable. The plan must pin that MENU.md's format is frozen for the duration of Phase 1.5 (no format changes in parallel).

---

## 8. Deferred to Phase 2 (unchanged)

1. Full automated monolith → domain migration with Sonnet sub-agent.
2. Path extraction + push injection for subagents (Item B variant rejected in Phase 1.5; reconsider only if audit shows menu-only injection is insufficient).
3. Exact-phrase trigger matching at +3 with tokenized fallback at +2.
4. CLAUDE.md rules promotion (mycelium-specific conventions land in every repo's CLAUDE.md).
5. Broader rollout to Gastruloids, SpaceBar, AutoReview.
6. Expand push-active domain set (debugging, tooling become push-active if audit shows demand).
7. MENU.md format upgrade (per-domain recent-entry summaries, trigger previews) if audit shows menu alone is insufficient to drive pulls.
