---
name: harden
description: >
  Ship one learning's structural_mitigation_candidate as a real test, then mark
  the learning's mitigation_type structural. Picks a learning in
  .living/learnings.md that is still ambient-awareness but names a concrete
  check, writes the test in the repository's own test setup, proves it fails
  when the learning's problem is present and passes on the current code, and
  updates only that entry after the user confirms. Use when the user asks to harden, enforce, or
  turn a learning, gotcha, or lesson into a test or structural mitigation. Do
  not use for recording a new learning, promoting learnings to conventions,
  adding checks inside analysis code, or changing Mycelium itself.
---

<!--
Adapted from the harden skill in mycelium-extra
(https://github.com/mdmanurung/mycelium-extra), Copyright (c) 2026 Mikhael
Manurung, MIT License. See issue #91.
-->

# Mycelium — Harden

Resolve `.living/` and test paths relative to the user's working repository.

Turn a learning the project keeps having to remember into a test that
remembers it. A learning counts as `structural` only once its check has
shipped (`skills/core/templates/learning-entry.md`); until then it stays
`ambient-awareness`, the weakest kind. This skill ships the check. Harden one
learning per run.

## 1. Find a candidate

- Requires `.living/learnings.md`. Without it, say so and stop.
- Search it for `structural_mitigation_candidate`, then read each matching
  entry. An entry starts at the `### [YYYY-MM-DD]` heading above the match;
  older projects may use a legacy `## [YYYY-MM-DD]` heading.
- Keep entries whose `mitigation_type` is `ambient-awareness` and whose
  candidate names a concrete check: a function, file, column, or assertion.
  Skip the template placeholder and vague candidates ("be careful with X").
  Leave `convention` entries and entries with no `mitigation_type` alone: a
  convention is already enforced through `.living/conventions.md`, and
  changing its type is the user's call; an untyped entry needs its type
  recorded first. An entry marked `structural` whose test never shipped is
  demoted to `ambient-awareness` at crystallize time
  (`skills/core/references/skill-generation-guide.md`), which makes it a
  candidate here.
- A candidate that places its check in analysis code ("In
  `analysis/.../x.py`, assert ...") still qualifies when the check reads a file
  a test can read too: show it with "(check moves to a test)". If it checks a
  value only the running script holds, skip it and say why: it needs a code
  change and a re-run through `$mycelium:analyze`.
- Show at most five, newest first: date, title, and the candidate in one line.
  The user picks one. If none qualifies, say so and stop.

## 2. Place the test

- Follow the repository's test setup: `pytest.ini`, `[tool.pytest]` in
  `pyproject.toml`, `tests/testthat/`, or existing `test_*.py` files and how
  they are run (check `README`, `MYCELIUM.md`, `AGENTS.md`, `CLAUDE.md`).
- With no setup, create `tests/test_<slug>.py` (stdlib `assert`, runnable with
  `python3`) or `tests/testthat/test-<slug>.R`.
- Never edit analysis code, and never place the test under `analysis/`, a
  notebook directory, or any path listed in `ANALYSIS_MANIFEST.md`. A check
  inside analysis code changes what ran, so it belongs to a new analysis run,
  not to this skill. For a candidate placed in analysis code, write the same
  assertion in the test, reading the file the analysis reads or writes, and
  leave the script as it is.
- Name the test after the learning and cite it in a one-line comment:
  `# Guards learning "<title>" (.living/learnings.md, <date>).`

## 3. Prove it guards something

A test guards something only if it fails when the learning's problem is
present and passes now. How to make the problem present depends on what the
test checks:

- **A data check** (a column, a sample count, a file format) asserts a property
  of a file. Its red run feeds the same assertion a small fixture, built in the
  test or a temporary file, that recreates the bad data. It must fail there.
- **A code check** (a function's behavior) passes on the problematic input once
  the fix is in. Its red run is against the unmitigated implementation: the
  commit before the fix, checked out in a temporary `git worktree` (it holds
  only tracked files, so copy the new test into it), or the fix monkeypatched
  out inside a throwaway run. It must fail there, on its targeted assertion: a
  missing test, import error, or collection failure is not a red run. Never edit the
  repository's code to stage the red run, and never keep a test that fails on
  the input it is meant to handle.

Then the green run: the test must pass on the current code or data. Show both
runs.

If it cannot be made to fail in its red run, it guards nothing: stop, and
leave the entry `ambient-awareness`. If it fails on the current code, the
problem is live: report it, and do not mark the learning structural.

For a data learning (a column, a sample count, a file format), the test may
read the repository's data file. If that file is large or git-ignored, say the
test only runs where the data exists.

## 4. Record, after the user confirms

Show the test path, both runs, and the exact edit, then ask. After the user
confirms, edit only that entry:

- `**mitigation_type**: structural`
- `**structural_mitigation_candidate**: <unchanged text> Shipped: <test path> (YYYY-MM-DD).`

Append the `Shipped:` note to the end of the existing candidate text, even
when that text wraps over several lines. Change no other field or entry, and do not commit. If Mycelium's Stop hook
asks for `.living/` updates afterwards, follow it. End with one line: the
learning, the test path, and how to run it.
