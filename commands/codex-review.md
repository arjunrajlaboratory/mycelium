---
description: >
  ALWAYS invoke this skill when the user wants to respond to, address, or act on
  Codex review comments on a pull request. Trigger phrases include: "respond to
  Codex", "address the Codex comment(s)", "fix what Codex flagged", "handle the
  Codex review", "Codex left comments", "reply to Codex", "@codex", "the bot
  flagged this", "/mycelium:codex-review". Also trigger proactively when the user
  pastes a Codex review comment or a link to one and asks you to fix it, or when
  they are iterating with Codex on a PR and want the round to converge. The
  defining behavior of this skill — and the reason to use it instead of an ad-hoc
  fix — is that it does not just patch the single line Codex flagged: it
  generalizes each comment into an underlying ERROR PATTERN and audits the WHOLE
  branch for other instances of that same pattern, fixing them all in one pass so
  later Codex rounds don't surface the same mistake one instance at a time. It
  auto-detects scope (a specific comment if you point to one, otherwise all open
  Codex comments on the PR), verifies the fixes against the project's tests, and
  drafts a reply that summarizes both the targeted fix and the branch-wide audit.
  When it posts, it appends `@codex review` to re-trigger Codex ONLY if it detects
  the user already has Codex access on the repo (the Codex bot has previously
  reviewed or commented on this PR); otherwise it asks first. Posting is
  outward-facing, so it always drafts the reply, shows it, and posts only after
  you confirm.
  Do NOT trigger for: a general analysis-aware code review with no Codex comment
  involved (/mycelium:review), writing NEW analysis code (/mycelium:analyze),
  generating reports (/mycelium:report), or open-ended brainstorming
  (/mycelium:ideas).
---

# Mycelium — Codex Review

Handle Codex review comments on a pull request so that each review round actually
converges instead of looping. The point is in Step 3: when Codex flags one
instance of a mistake, the same mistake is usually elsewhere on the branch too.
Fixing only the flagged line invites Codex to surface the next instance on its
next pass, and you burn cycles fixing them one at a time. This skill fixes the
flagged instance **and** audits the whole branch for the same error pattern.

## Why this skill exists

Codex reviews a diff and flags concrete instances. It does not (and cannot
reliably) tell you "and here are the four other places you made the same
mistake." An LLM coding agent responding to that comment tends to fix exactly the
line cited and move on — which is locally correct but globally incomplete. The
result is a back-and-forth: round 1 flags `foo.py:40`, you fix it, round 2 flags
`bar.py:88` (same bug), and so on. Generalizing each comment into an error
pattern and sweeping the branch once collapses that loop.

## Protocol

### Step 0 — Locate the PR and establish comment scope

Resolve the pull request first:

- If the user gave a PR number or URL, use it.
- Otherwise infer it from the current branch:
  `gh pr view --json number,url,headRefName,baseRefName`. If there is no PR for
  the current branch, ask the user which PR they mean rather than guessing.

Then **auto-detect comment scope**:

- **If the user points you to a specific comment** (pastes its text, gives a
  comment URL, or describes one), target that single comment. You can fetch one
  comment by id with `gh api repos/{owner}/{repo}/pulls/comments/{comment_id}`.
- **Otherwise, fetch all open Codex comments on the PR** and handle them in one
  pass. Codex posts in more than one place, so check all three:
  - inline review comments: `gh api repos/{owner}/{repo}/pulls/{number}/comments`
  - PR-level review summaries: `gh api repos/{owner}/{repo}/pulls/{number}/reviews`
  - conversation comments: `gh api repos/{owner}/{repo}/issues/{number}/comments`

  Keep the ones authored by the Codex bot. Match on the author login
  case-insensitively containing `codex` (the connector usually appears as
  something like `chatgpt-codex-connector[bot]`) and/or `.user.type == "Bot"`;
  if you are unsure, show the candidate authors to the user and confirm which is
  Codex. "Open" means not already resolved or already addressed: if the
  repo exposes review-thread resolution (GraphQL `reviewThreads { isResolved }`),
  skip resolved threads; otherwise skip any comment that already has a reply from
  you fixing it, and note which you skipped.

Record the PR's `{owner}/{repo}` and `{number}` — you will reuse them when
posting.

### Step 1 — Generalize each comment into an error pattern

For each Codex comment in scope, open the flagged code at its `file:line` and
read enough surrounding context to understand *why* it is wrong. Then name the
**underlying error pattern**, not just the literal line. Examples:

- "missing `None`/empty guard before indexing a lookup result"
- "exception swallowed with a bare `except: pass`"
- "hardcoded absolute path instead of a configurable one"
- "off-by-one on an inclusive range boundary"
- "mutable default argument"
- "f-string SQL / shell interpolation instead of parameterization"
- "reading a column by positional index instead of by name"

Write the pattern down (one line each). The pattern — not the line number — is
what you will hunt for in Step 3.

### Step 2 — Fix the flagged instance

Apply the smallest correct change that resolves the comment, consistent with the
surrounding code's style and conventions. Don't gold-plate; fix the cited issue.

### Step 3 — Audit the whole branch for the same pattern (the core of this skill)

For **each** error pattern from Step 1, search the entire branch — not just the
flagged file — for other instances, and fix them all now.

1. Scope the branch's own changes first: `git diff main...HEAD --name-only` for
   the touched files, and `git diff main...HEAD` for the changes themselves. The
   patterns Codex flags are usually concentrated in the code this branch added or
   changed.
2. Search for the pattern with `grep`/`rg`/`Glob` using a signature that matches
   the *generalized* mistake (e.g. for swallowed exceptions, search
   `except.*:\s*pass` and bare `except:`; for hardcoded paths, search for
   absolute-path string literals). Reason about each hit — do not blindly
   rewrite. Guard against false positives: a construct that looks similar but is
   actually correct in its context should be left alone (and is worth a one-line
   note).
3. If the pattern is clearly systemic (it predates the branch and appears in
   unchanged code too), fix the in-branch instances and flag the broader cleanup
   to the user as a separate follow-up rather than silently expanding the diff.
4. Keep a tally per pattern: where you searched, how many other instances you
   found, and how many you fixed. You will report this in the reply so the audit
   scope is explicit.

### Step 4 — Verify

Run the project's relevant tests and linters for the files you touched (e.g.
`pytest <paths>`, the repo's lint command) and confirm the flagged fix and every
audited fix pass and don't break anything else. If the project has no test
covering the changed behavior and the change is non-trivial, say so — and, when
it makes sense, add a test (TDD) that would have caught the bug Codex flagged, so
the pattern can't silently return.

### Step 5 — Draft the reply, then post on confirmation

Draft a reply comment that summarizes, per Codex comment:

- **The targeted fix** — what you changed and why it resolves the comment.
- **The branch-wide audit** — the error pattern, what you searched, and how many
  other instances you found and fixed (or "no other instances found", which is
  itself useful signal). This is what tells Codex (and the human) that the whole
  class of mistake was handled, not just the one line.

**Gate the `@codex review` re-trigger on Codex access.** Appending `@codex
review` to a comment only does something if the user has Codex access on the
repo, so:

- If you found evidence that the Codex bot has **previously** reviewed or
  **already** commented on this PR (you have it from Step 0), the user has
  access — append `@codex review` to the reply so Codex re-reviews the branch
  automatically.
- If there is no such evidence, **do not** auto-append it. Tell the user "I can
  add `@codex review` to re-trigger Codex if you have access — want me to?" and
  let them decide.

Posting is an outward-facing action, so **show the drafted reply (and whether it
includes `@codex review`) and post only after the user confirms.** On
confirmation:

- general summary reply (where `@codex review` belongs, so Codex re-reviews the
  branch): `gh pr comment {number} --body "<reply>"`
- to reply on a specific inline thread instead:
  `gh api repos/{owner}/{repo}/pulls/{number}/comments -f body="<reply>" -F in_reply_to={comment_id}`

If the user chose draft-only, hand them the reply text and stop.

### Step 6 — Post-action hook

If the same error pattern has now shown up across more than one Codex round (or
more than one PR), log a short entry to `.living/learnings.md` per the core
Post-Action Hook Protocol — e.g. "Codex has flagged swallowed exceptions on three
PRs; candidate for a lint rule / convention." Recurring patterns are exactly what
should crystallize into a convention so the mistake stops being made in the first
place. Otherwise no logging is needed.

## What this skill is NOT for

- A general analysis-aware code review with no Codex comment in play — use
  `/mycelium:review`.
- Writing new analysis code (`/mycelium:analyze`) or reports
  (`/mycelium:report`).
- Posting to GitHub without the user's confirmation — the reply is always shown
  and confirmed first.
- Force-fitting a fix when the Codex comment is wrong. Codex can be mistaken; if
  the flagged code is actually correct, say so in the reply with the reasoning
  rather than introducing a worse change to satisfy the bot.

## Cross-references inside this skill

- `commands/review.md` — the general analysis-aware review skill; use it when
  there is no Codex comment to respond to.
- The mycelium core `Post-Action Hook Protocol` in `commands/core.md` governs
  what to log to `.living/` when an audit surfaces a recurring pattern.
