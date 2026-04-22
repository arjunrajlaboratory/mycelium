# Mycelium Accessibility Architecture — Phase 1 Design

**Date**: 2026-04-22 (revised post-Codex-review)
**Status**: Draft design, pending user approval
**Scope**: Phase 1 of a 2-phase implementation. Phase 2 is gated on Phase 1 audit outcome.
**Canary project**: Scientific Claims Knowledge Graph (`Science/Scientific Claims Knowledge Graph/`)
**Project**: mycelium (`/Users/mst36/tools/mycelium/`)

---

## 1. Problem

Diagnostic measurement across the Science portfolio (see `Science/.claude/mycelium-read-access.log` analyses from 2026-04-20 to 2026-04-22) showed:

- **97% of Edit/Write events occur with no preceding learnings/findings Read** in the same session. Of the 3% that are preceded, 89% have gaps >30 minutes, meaning the read was incidental — not informational.
- The progressive-disclosure design (`INDEX.md → domain → detail`) is used as-intended in only ~6% of sessions; 85% of sessions enter via `last-session.md` (resume state, not knowledge routing).
- The tracker that motivated this work only observes Read-tool access. Bash-based access (via `printf >>`, `cat`, etc.) accounts for ~65% of actual `.living/` touches and was invisible; a companion Bash hook now captures these, but the fundamental pattern is unchanged: **the pull-model (Claude must remember to Read) does not deliver learnings at the moment they are needed.**

The accessibility failure is not one of content quality or of writer discipline; it is one of **delivery mechanism**.

## 2. Goals

1. Inject a compact knowledge menu into every session opening context, without requiring Claude to perform a Read.
2. Push **relevant** domain-scoped learnings into context at the moment Claude is about to Edit a matching file. Relevance is ranked, not recency-based.
3. Preserve writing discipline with minimal added friction (one extra classification decision per entry).
4. Keep a human-readable audit trail — the menu is a real file, not computed in-memory — and structured error logs.
5. Validate the push channel is *trusted* by Claude before investing in full corpus reshaping.

## 3. Non-goals for Phase 1

Explicitly deferred to Phase 2 (separate spec, gated on Phase 1 audit):

- **Full Sonnet-driven migration of `learnings.md` into domain files.** Phase 1 seeds 20–30 hand-selected high-value entries only.
- Per-entry `Triggers:` metadata as the primary matching mechanism. Phase 1 uses a lightweight relevance ranker (§6.2); per-entry triggers are an optional signal already respected by the ranker but not required.
- Dispatch-prompt enrichment convention.
- Migration of stable rules into `CLAUDE.md.template`.
- Broader rollout beyond the KG canary.
- Expanding the push-active domain set beyond 5.

## 4. Architecture

Three delivery layers, with a sharp distinction between **push-active** domains and **pull-only** domains:

```
              ┌───────────────────────────────────────────────┐
              │  .living/learnings/                           │
              │  ─── PUSH-ACTIVE (Phase 1) ───                │
              │    figures.md         (matches: figure globs) │
              │    extraction.md      (matches: KG globs)     │
              │    pipelines.md       (matches: orchestr/run) │
              │    statistics.md      (matches: analysis py)  │
              │    writing.md         (matches: manuscripts)  │
              │  ─── PULL-ONLY (Phase 1, no push) ───         │
              │    debugging.md                               │
              │    tooling.md                                 │
              │                                               │
              │    learnings.md       (MONOLITH, unchanged)   │
              │  .living/MENU.md      (generated artifact)    │
              └───────────────────────────────────────────────┘
                               ▲            ▲           ▲
                               │            │           │
            writes             │            │           │
     ┌──────────────┐          │            │           │
     │ crystallizer │──────────┘            │           │
     │  (sonnet)    │                       │           │
     └──────────────┘                       │           │
                                            │           │
           reads by hook                    │           │
     ┌──────────────────┐                   │           │
     │ SessionStart     │ cat MENU.md ──────┘           │
     │ mycelium-health  │ → additionalContext           │
     └──────────────────┘                               │
                                                        │
           reads by hook                                │
     ┌──────────────────┐       walk push-active domains│
     │ PreToolUse/Edit  │       score relevance,        │
     │ mycelium-edit-   │       inject top K entries ───┘
     │ injector         │       → additionalContext
     └──────────────────┘
```

**Three moments of delivery:**

- **L1 — SessionStart**: every session gets `MENU.md` content as `additionalContext` at open.
- **L3 — PreToolUse/Edit**: push-active domains participate in path-matched injection; entries are **ranked by relevance** (path-token + title-token overlap), not pure recency; top 2–3 entries pushed, capped at 2000 tokens per injection.
- **L2 — On-demand pull**: all domains (including `debugging`, `tooling`) are Readable, discoverable via MENU. Phase 1 does not force-split the monolithic `learnings.md`; the hand-seeded domain files supplement it.

**Invariants:**

1. Any write to `.living/learnings/**` regenerates `.living/MENU.md`.
2. A domain file participates in L3 push iff its frontmatter declares `push_active: true` AND `matches:` globs. Phase 1 has exactly 5 push-active domains.
3. The original `learnings.md` monolith remains in place and Readable; it is linked from MENU.
4. **All hook failures are logged** to `.claude/mycelium-hook-errors.log`. Silent-on-error is acceptable only because observability is loud.

## 5. Components

### 5.1 New files in `/Users/mst36/tools/mycelium/`

| Path | Purpose |
|---|---|
| `skills/core/scripts/generate_menu.py` | Walks `.living/learnings/*.md`, parses frontmatter, emits `.living/MENU.md`. Idempotent. |
| `skills/core/scripts/match_edit.py` | Given a file_path, reads each push-active `learnings/*.md`, glob-matches, **scores entries by path-token + title-token overlap** (see §6.2), filters by min-score threshold, returns a structured **result dict** (`{"entries": [...], "matched_domains": [...], "truncated": bool, "bytes": N}`) on stdout as JSON. The hook is responsible for wrapping this into the `additionalContext` envelope — separation of concerns. Hard byte cap (default 2000 tokens ≈ 8000 chars). |
| `skills/core/scripts/seed_domains.py` | One-time helper: interactive-or-file-driven hand-selection of 20–30 highest-value entries from the existing `learnings.md`, creating seed `learnings/{domain}.md` files with correct frontmatter. Does NOT touch the monolith. |
| `skills/core/hooks/mycelium-edit-injector.sh` | PreToolUse hook (matcher: `Edit\|Write\|MultiEdit`). Extracts `file_path` (handles each tool's payload shape explicitly), resolves paths relative to repo root, invokes `match_edit.py`, emits its JSON output. Includes per-session de-dup. |
| `skills/core/templates/learnings-domain.md` | Template for a new domain file: frontmatter block + one example entry. |
| `skills/core/templates/DOMAINS.md` | Closed vocabulary of 8 domains with descriptions; marks 5 as push-active. |
| `docs/designs/2026-04-22-accessibility-architecture.md` | This spec. |

### 5.2 Modified files

| Path | Change |
|---|---|
| `skills/core/hooks/mycelium-health.sh` | Add step after existing "load last-session.md" logic: read `.living/MENU.md` if present, emit as `additionalContext` inside a `<mycelium-menu>` tag. |
| `skills/core/hooks/mycelium-post-action.sh` | Add step: if the session touched `.living/learnings/**`, invoke `generate_menu.py`. |
| `skills/core/templates/learning-entry.md` | Add `**Domain:**` required field. Add `**Triggers:**` optional field (consumed by relevance ranker when present; not required). |
| `SKILL.md` (mycelium root) | Update `crystallize` mode protocol: classify new learnings into a domain, append to `learnings/{domain}.md` if the domain file exists, otherwise append to monolithic `learnings.md` and leave a migration todo. |
| `skills/core/templates/CLAUDE.md.template` | Add short explanation of the L1 menu; point at `.living/learnings/DOMAINS.md`. |

### 5.3 Unchanged (explicitly)

- Existing mycelium scripts (`init_repo.py`, `validate_structure.py`, `crystallize_findings.py`, `generate_index.py`).
- The INDEX.md mechanism as a project-summary artifact (kept; not repurposed).
- `decisions.md`, `findings/`, `log/` subsystems.
- Existing read/bash trackers (provide baseline measurement).
- **The monolithic `.living/learnings.md` files in every project.** Phase 1 does not touch them.

## 6. Data flow

### 6.1 SessionStart

1. SessionStart hooks fire.
2. `mycelium-health.sh` runs existing checks.
3. New step: if `$REPO_ROOT/.living/MENU.md` exists, read it (hard cap ≤ 2000 tokens — truncate with a trailing marker if larger).
4. Emit content inside `<mycelium-menu>` tags as `additionalContext`.
5. On any error in this step, append a structured line to `.claude/mycelium-hook-errors.log` and exit 0.

### 6.2 PreToolUse/Edit (L3 push)

The hot path. All precision details specified here.

**Step 1 — Tool-payload extraction** (hook layer, bash + jq):

- `Edit`: `.tool_input.file_path`
- `Write`: `.tool_input.file_path`
- `MultiEdit`: `.tool_input.file_path` (single file, multiple edits within)
- Missing or empty → log `missing-file-path` structured error, exit 0.

**Step 2 — Path resolution** (hook layer):

1. If path is absolute: keep as-is.
2. If relative: resolve against `git rev-parse --show-toplevel` (fallback: hook cwd).
3. Resolve symlinks with `realpath`.
4. **Order of checks** (fail-fast, cheap before expensive):
   a. Path exists under repo root — if not, log `path-outside-repo`, exit 0.
   b. Path is a regular file or would-be file (Write/MultiEdit to new file is valid) — yes/no.
   c. Only then proceed to glob matching.

**Step 3 — Session ID** (hook layer):

- Primary source: `.session_id` field in the stdin hook payload (Claude Code provides this).
- Fallback: parent pid (`$PPID`) + hostname. Logged as fallback.

**Step 4 — De-dup check** (hook layer, before invoking match_edit.py):

- Dedup file: `$REPO_ROOT/.claude/mycelium-injection-dedup.tmp`, line-based format: `SESSION_ID<TAB>EPOCH<TAB>SHA1(session_id||path||domains_sorted)`.
- Concurrency: `flock` on the dedup file for the read-check-write sequence. If `flock` unavailable, use `mkdir`-based lock (POSIX-portable).
- TTL cleanup: when acquiring the lock, drop lines with `EPOCH` older than 600 seconds (10 min).
- Skip injection if hash seen within TTL. Emit empty output; exit 0.

**Step 5 — Invoke `match_edit.py <resolved_path>`** (Python layer). Script contract:

1. For each push-active domain file, read frontmatter. Reject files missing `push_active: true` or `matches:`.
2. Glob-test the path against each pattern using `fnmatch`-compatible semantics (`**` = recursive wildcard). Exit immediately if no domain matched.
3. For matching domains, parse entries by `## [YYYY-MM-DD]` headers. Score each entry:

   **Tokenization** (deterministic, lowercase):
   - Path tokens: basename stem (without extension) + parent-dir name + one level up, split on `[_\-./]+`, filter out empty and numeric-only tokens, dedup.
   - Entry tokens (by field): title tokens (same tokenizer), `Tags:` values (already list), `Triggers:` values (already list), body first-500-chars tokenized.

   **Scoring**:
   - +3 per path token found in title
   - +2 per path token found in Tags
   - +2 per path token found in Triggers
   - +1 per path token found in body (first 500 chars)
   - +0.5 recency bonus if entry dated within last 60 days
   - Minimum threshold: **score ≥ 2.0** required to include. Entries below threshold are dropped. If all entries in all matched domains score < 2.0, emit empty result (nothing pushed even though glob matched).

4. Select top K entries (global, not per-domain) up to K=3. Ties broken by recency.
5. Render selected entries into a content string.
6. **Truncation**:
   - Compute rendered byte length.
   - If under global cap (2000 tokens ≈ 8000 chars): emit all.
   - If over cap: drop lowest-scoring entries first until under cap. If a single entry alone exceeds cap, truncate it mid-body with a trailing `… [truncated; Read .living/learnings/{domain}.md]` marker.
   - **Per-domain `token_cap`/`top_k_push` frontmatter overrides** apply only when a single domain is matched. When multiple domains are matched, the global cap is authoritative (simplifies ranking).
7. Emit stdout JSON schema:
   ```json
   {
     "entries": [
       {"domain": "figures", "title": "...", "date": "2026-04-15",
        "score": 5.5, "content": "rendered markdown"}
     ],
     "matched_domains": ["figures", "writing"],
     "truncated": false,
     "dropped_below_threshold": 2,
     "bytes": 1423,
     "schema_version": 1
   }
   ```

**Step 6 — Hook wraps and emits** (back in hook layer):

- If `entries` is empty: emit nothing (no `additionalContext`), exit 0.
- Otherwise: build the `<mycelium-pushed-learnings>` envelope from `entries[*].content`, emit as the hook's JSON `{"hookSpecificOutput": {"additionalContext": "..."}}`, exit 0.

**Step 7 — Error handling** (every step above):

Any exception: append a line to `.claude/mycelium-hook-errors.log` with fields `ts, tool_name, file_path, repo_root, session_id, error_class, matched_domains, bytes, script_version`, then exit 0. Edits are never blocked.

### 6.3 Menu regeneration

Unchanged from prior draft. On any `.living/learnings/**` write, post-action hook invokes `generate_menu.py`.

## 7. File formats

### 7.1 Domain file (`learnings/{domain}.md`)

```markdown
---
domain: figures
description: color, DPI, typography, and layout rules for publication plots
push_active: true
matches:
  - "**/figures/**"
  - "**/panel_*.py"
  - "**/composite*.py"
top_k_push: 3        # optional; default 3
token_cap: 2000      # optional; default 2000
---

## [2026-04-15] Colorblind-safe palette required

**Domain**: figures
**Category**: gotcha
**Tags**: palette, colorblind, matplotlib
**Triggers**: ["palette", "color"]    # optional; boosts relevance score when edit path matches

**What happened**: Default matplotlib palette failed accessibility check.
**Resolution**: Use `["#0072B2", "#D55E00", ...]` or viridis.
```

A `debugging.md` or `tooling.md` file would have `push_active: false` (or omit the field) — it still appears in MENU and is Readable, but the injector ignores it.

### 7.2 MENU.md (generated)

```markdown
# Mycelium Knowledge Menu

Generated: 2026-04-22T14:32:18  ·  Push-active: 5  ·  Pull-only: 2  ·  Total entries: 127

**Push-active** (auto-injected on matching Edit):
- **figures** (12) — color, DPI, typography, layout for publication plots
- **extraction** (8) — KG claim extraction prompts, schemas, model choice
- **pipelines** (6) — DAG orchestration, retry, error handling
- **statistics** (4) — test selection, assumptions, effect-size
- **writing** (3) — IMRAD conventions, citations, figure legends

**Pull-only** (Readable on demand):
- **debugging** (7) — runtime failures, env issues
- **tooling** (9) — conda, pytest, LSP, hooks

**Unmigrated corpus**: `.living/learnings.md` (existing monolith, ~58KB, 87 entries). Phase 1 does not reshape this file. **Seeded entries are copied, not moved** — during Phase 1 the highest-value entries appear in both the monolith and their domain file. This duplication is accepted as transient; Phase 2 migration resolves it.
```

### 7.3 DOMAINS.md

```markdown
# Mycelium Domain Vocabulary

| Domain | Push-active | Description | Example |
|---|---|---|---|
| figures | ✓ | color, DPI, typography, layout | "use constrained_layout" |
| extraction | ✓ | KG claim extraction | "Sonnet > Haiku for complex abstracts" |
| pipelines | ✓ | orchestration, retry | "snapshot before retry" |
| statistics | ✓ | test selection, effect-size | "Welch's t is default" |
| writing | ✓ | IMRAD, citations, prose | "reporting guidelines per journal" |
| debugging | — | runtime failures, env issues | "matplotlib Agg backend for scripts" |
| tooling | — | conda, pytest, LSP, hooks | "warm LSP before Grep" |

Classification rule for writers: pick the domain a reader would look in first. When truly ambiguous, append to `learnings.md` (monolith) and flag with a migration-todo.
```

## 8. Migration mechanics (Phase 1 only)

**Phase 1 does NOT do a full automated migration.** The current `learnings.md` remains the corpus of record.

Seed step — executed once per project during Phase 1 rollout:

1. Operator (human) reviews `learnings.md` and hand-picks 20–30 highest-value entries **that belong to one of the 5 push-active domains**. "Highest-value" = rules/gotchas that, if pushed at edit time, would obviously shape output. Skew toward recent and specific.
2. Operator produces a **seed list file** (`seeds.yaml`) with this exact format:
   ```yaml
   # seeds.yaml — Phase 1 hand-seeded learnings
   seeds:
     - title: "Colorblind-safe palette required"
       domain: figures
       tags: [palette, colorblind, matplotlib]    # optional
       triggers: [palette, color]                  # optional
     - title: "300 DPI minimum for publication rasters"
       domain: figures
     - title: "Sonnet > Haiku for complex abstracts"
       domain: extraction
       triggers: [extraction, prompt]
     # ... 20-30 total
   ```
   The `title` field must match the exact text after `## [YYYY-MM-DD]` in the monolith.
3. `seed_domains.py <seeds.yaml>` reads the seed list, extracts each matching entry from the monolith (copy, not move — duplication is transient), and writes to `learnings/{domain}.md` with correct frontmatter. Frontmatter is generated from a template keyed on domain name. Script is idempotent (re-running with same seeds.yaml is a no-op).
4. Monolithic `learnings.md` is unchanged.
5. `generate_menu.py` runs to produce first MENU.md.

Rollback: `rm -rf .living/learnings/` restores pre-Phase-1 state (monolith intact, only the new domain tree deleted).

Full migration (automated Sonnet-driven split of all entries, deduplication against seed) is deferred to Phase 2 and only happens if the push channel is validated.

## 9. Hook registration

Phase 1 canary is KG only. The existing SessionStart registration in KG's `settings.local.json` is unchanged (`mycelium-health.sh` gets upgraded in place). One new PreToolUse block is added:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {"type": "command", "command": "/Users/mst36/tools/mycelium/skills/core/hooks/mycelium-edit-injector.sh"}
        ]
      }
    ]
  }
}
```

Note: Claude Code hook matcher semantics must be verified as `|`-alternation before implementation; if it's exact-match-only, the block is duplicated across three matcher values.

## 10. Validation gate

After 7 days of Phase 1 operation in the KG canary, run a structured audit combining delivery metrics and **precision metrics**:

**Delivery checks (objective):**

1. `.living/MENU.md` exists and is fresh (mtime within last 24h of last `.living/` write).
2. In ≥95% of sampled sessions (via JSONL inspection), SessionStart `additionalContext` contains `<mycelium-menu>`.
3. In ≥80% of Edit events to files matching a push-active domain glob, `additionalContext` contains `<mycelium-pushed-learnings>`.
4. `.claude/mycelium-hook-errors.log` shows error rate <1% per hook fire.

**Precision review (qualitative, 10-session sample):**

For 10 random sessions with ≥1 push event, for each pushed-learning block:

5. **Relevance**: was the pushed entry relevant to the current edit? (yes / partial / no)
6. **Influence**: did the subsequent edit reflect the pushed knowledge? (yes / unclear / no — with short rationale)
7. **Noise**: were there irrelevant entries in the same push block? (count them)
8. **Missed signal**: scan the 10 sessions for edits where a *known* relevant learning existed in the monolith or a domain file but was NOT pushed. Count these as misses. This catches under-ranking (the opposite failure mode of over-pushing).

**Additional distribution metrics (mechanical, no human judgment):**

9. **Median entries per push**: should be ≤ 3. Higher means the cap isn't being respected or the threshold is too permissive.
10. **Zero-push match rate**: when the glob matched but no entry cleared the score threshold, did `match_edit.py` correctly emit nothing? Target: no spurious injections.

Record in `todo/accessibility-phase1-audit.md`:
- **Relevance rate** = yes / (yes + partial + no)
- **Influence rate** = yes / (yes + unclear + no)
- **False-positive rate** = noise count / total entries pushed
- **Miss rate** = misses / (misses + true positives)

**Go/no-go:**

- Delivery: all 4 checks pass
- Precision:
  - Relevance ≥ 60%
  - Influence ≥ 30% (directional — noisy to attribute, do not over-weight)
  - False-positive rate ≤ 25%
  - Miss rate ≤ 40% (some misses are acceptable in Phase 1 given deliberately simple ranker)
  - Median entries per push ≤ 3
  - Zero-push path functions correctly (≥95% of glob-match-but-no-score cases emit nothing)

GO requires delivery pass AND relevance/FP/median/zero-push thresholds met. Influence and miss rates are directional — informative but not blocking.

Failure routing:
- Delivery fails → fix hooks, re-audit (no rollback)
- Relevance fails → ranker too loose; raise threshold or tighten token overlap
- FP too high → reduce K; raise threshold
- Miss rate too high → ranker too strict; add per-entry `Triggers:` coverage or lower threshold. Phase 2 per-entry signal upgrade pulls this back.
- Influence <30% but relevance good → content quality issue (stale learnings); trigger a crystallize-cleanup pass before Phase 2

## 11. Testing approach

**Unit** (under `skills/core/scripts/tests/`):

- `test_generate_menu.py`: fixture with 3 synthetic domain files (2 push, 1 pull) → exact MENU.md output.
- `test_match_edit.py`: fixture with 3 domain files, 8 test edit paths (hits, misses, ambiguous). Verify: (a) glob matching with `**` semantics, (b) tokenization rules on known inputs, (c) scoring on known inputs, (d) threshold filtering (score < 2.0 dropped; all-below-threshold → empty output), (e) K-cap and token-cap enforcement, (f) single-entry-over-cap mid-body truncation, (g) per-domain vs global cap behavior, (h) stdout schema matches §6.2 Step 5.7.
- `test_seed_domains.py`: fixture monolith + `seeds.yaml` → correct entries extracted, frontmatter correct, monolith untouched, idempotent on re-run.

**Integration** (shell-level tests for hook layer — tool-payload parsing lives here, not in match_edit tests):

- `skills/core/hooks/test_phase1_integration.sh`: creates temporary `.living/`-enabled repo; registers hooks; feeds synthetic `Edit`, `Write`, `MultiEdit` JSON payloads over stdin; asserts:
  - file-path extraction per tool type (Step 1)
  - path resolution order (Step 2, including outside-repo rejection)
  - session_id extraction + fallback (Step 3)
  - dedup: second identical fire within TTL emits nothing
  - concurrent-fire safety: two backgrounded hook invocations don't corrupt dedup file
  - error log contains one structured line on forced-failure scenarios

**Canary validation**:

- KG hooks enabled only after unit + integration tests green.
- Daily inspection: `cat .living/MENU.md`; `tail -50 .claude/mycelium-hook-errors.log`; `grep -c mycelium-pushed-learnings` across recent JSONLs to track injection volume.
- Day 7: run §10 audit.

## 12. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Push content is noisy → Claude habituates to ignoring it | Relevance ranking (not recency); K=3 cap; 2000-token budget; §10 precision audit catches this before expanding scope |
| Frontmatter parse errors | Per-file try/except; skip that file; log structured error; other files unaffected |
| Hook kills session due to crash | `{ … } 2>/dev/null` wrapper BUT accompanied by durable `.claude/mycelium-hook-errors.log` — observability restored |
| Claude Code matcher semantics differ from `Edit\|Write\|MultiEdit` | Explicit verification step in implementation plan before rollout; fallback is three separate matcher blocks |
| Path resolution edge cases (symlinks, outside-repo) | `match_edit.py` rejects absolute paths outside the current repo root; symlinks resolved via `realpath`; unhandled path patterns logged not silently ignored |
| Repeated injection on many edits to same file | Per-session dedup in `.claude/mycelium-injection-dedup.tmp`, keyed on `(session_id, path, domain_set)`; TTL 10 min |
| `additionalContext` truncated by Claude Code | 2000-token cap is conservative (Claude Code docs indicate ample headroom); size logged per injection for monitoring |
| Sonnet misclassification during migration | N/A in Phase 1 (no automated migration); hand-seed is operator-curated |
| Canary produces regressions | Easy rollback — remove PreToolUse block from KG's `settings.local.json`; monolith never modified, seed files can be deleted |
| Error log grows unbounded | Weekly rotation in `mycelium-health.sh` (truncate at 10MB, archive prev) |

## 13. Success criteria (Phase 1 complete)

- [ ] Scripts written and unit-tested: `generate_menu.py`, `match_edit.py`, `seed_domains.py`.
- [ ] New hook `mycelium-edit-injector.sh` written and integration-tested.
- [ ] Modified hooks: `mycelium-health.sh`, `mycelium-post-action.sh`.
- [ ] Updated templates: `learning-entry.md`, `DOMAINS.md`, `learnings-domain.md`, `CLAUDE.md.template`.
- [ ] Updated `SKILL.md` crystallize protocol.
- [ ] Operator-curated seed list applied to KG: 20–30 entries in 5 push-active domain files.
- [ ] `MENU.md` generated and fresh in KG.
- [ ] Hook matcher semantics verified in Claude Code; registration correct.
- [ ] Canary active in KG's `settings.local.json`.
- [ ] 7-day observation + 10-session audit per §10.
- [ ] Go/no-go decision recorded in `todo/accessibility-phase1-audit.md`.

## 14. Phase 2 preview (separate spec, gated)

Contingent on Phase 1 passing §10. Tentative scope:

- **Full automated migration**: Sonnet-driven split of remaining monolith into domain files; dedup against seed.
- **Per-entry relevance signals**: promote `Triggers:` from optional boost to primary matcher where authored.
- **Dispatch-prompt enrichment**: auto-inject relevant learnings into `Agent` dispatches.
- **Rules → CLAUDE.md promotion**: move stable rules-not-gotchas into `CLAUDE.md.template` for 100% delivery.
- **Expand push-active set**: add debugging/tooling if domain vocabulary refinement justifies it.
- **Broader rollout**: enable across all Science projects with `.living/`.

---

*Design ends. See `todo/TODO_REGISTRY.md` for tracking.*
