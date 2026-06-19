# Design — `/mycelium:codex-review` skill

**Date:** 2026-06-18
**Issue:** [#60 — Codex Review](https://github.com/arjunrajlaboratory/mycelium/issues/60)
**Status:** approved-pending-review

## Problem

When Claude Code responds to Codex review comments on GitHub, it tends to fix
only the *single* instance Codex flagged rather than catching *all* occurrences
of the same underlying error pattern across the branch. Each Codex review round
then surfaces a different instance of the same mistake, burning cycles fixing
them one at a time.

## Goal

Add a Mycelium skill that, when handling a Codex review comment:

1. Addresses the specific comment/error Codex raised, **and**
2. Audits the whole branch for similar instances of that same error pattern,
   fixing them all in one pass.
3. Optionally re-triggers Codex with `@codex review` when (and only when) the
   user has Codex access on the repo.

## Shape

A **pure-prose command** at `commands/codex-review.md` → `/mycelium:codex-review`,
matching the house style of `commands/review.md`: YAML `description:` frontmatter
(the trigger/description surfaced in the skill list and validated by CI), a
stepwise protocol body, a "What this skill is NOT for" section, and a
cross-references section.

No new Python helper script. The deterministic pieces (fetching comments,
detecting Codex activity, posting) are thin `gh` calls the skill runs inline,
consistent with how `review.md` shells out to `gh`/`git`.

## Protocol the command encodes

| Step | Behavior |
|------|----------|
| 0 — Locate PR & scope | Resolve the PR (current branch via `gh pr view --json number,url`, or a number/URL the user provides). **Auto-detect comment scope**: if the user points to a specific Codex comment/URL, target that one; otherwise fetch all open/unresolved Codex review comments on the PR (`gh api`, matched by Codex bot author + content). |
| 1 — Generalize the error | For each comment, read the flagged code at `file:line` and name the *underlying error pattern*, not just the literal line (e.g. swallowed exception, missing `None`-guard before index, hardcoded path, off-by-one). |
| 2 — Fix the flagged instance | Minimal, correct change consistent with surrounding code. |
| 3 — **Audit the whole branch** | For each pattern, search the full branch diff (`git diff main...HEAD`) and the touched modules for *other instances of the same pattern*, and fix them all in one pass. Record what was searched and what was found so the reply can state audit scope. Guard against false positives. This is the core behavior the issue asks for. |
| 4 — Verify | Run the project's relevant tests/linters; confirm the flagged fix and the audited fixes don't break anything. |
| 5 — Reply + gated re-trigger | Draft a reply summarizing the specific fix **and** the branch-wide audit (pattern, files searched, count of other instances fixed). **Gate `@codex review`**: append it only when there is evidence the Codex bot has already reviewed/commented on this PR; otherwise ask the user whether to add it. Posting is outward-facing → draft, show the user, post via `gh` only after explicit confirmation. |
| 6 — Post-action hook | If the same pattern recurs across review rounds, log a learning to `.living/learnings.md` (per core's post-action protocol) — a candidate for a convention. |

## Decisions (from brainstorming)

- **Skill shape:** pure-prose command only (no tested helper script).
- **Comment scope:** auto-detect — specific comment if the user points to one,
  else all open Codex comments on the PR.
- **`@codex review` gating:** detect prior Codex activity on the PR; auto-append
  only when detected, otherwise ask the user.
- **Posting:** draft → confirm → post (gated on explicit confirmation each time).

## Touch points

- `commands/codex-review.md` — new command (main deliverable).
- `README.md` — three command enumerations (lines ~47, ~82, ~162).
- `commands/core.md` — command list (~line 26).
- `CHANGELOG.md` — one entry.
- `skills/core/scripts/test_codex_review_command.py` — TDD content-contract test.

## Testing (TDD)

CI does **not** run pytest; it validates command frontmatter inline and runs
markdownlint. So:

- CI automatically covers the new command's frontmatter validity.
- A new pytest, `test_codex_review_command.py`, is written **first** (red),
  asserting (a) the command file exists with valid YAML frontmatter containing a
  non-empty `description`, and (b) the body encodes the four required behaviors —
  branch-wide audit, scope auto-detection, `@codex review` gated on prior Codex
  activity, and posting/replying. Robust keyword/regex checks, not brittle exact
  strings. Watch it fail, then write the command to make it pass. The test also
  serves as a regression guard so future edits keep the essential instructions.

## Out of scope (YAGNI)

- No persistent config setting for Codex access (we infer from PR activity).
- No automatic resolution of GitHub review threads.
- No helper script / new dependencies.
