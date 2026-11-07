# Planning discussion — a sourced plan before the first run

<!--
Adapted from the grill skill in mycelium-extra
(https://github.com/mdmanurung/mycelium-extra), Copyright (c) 2026 Mikhael
Manurung, MIT License. See issue #91. Hook-enforced plan approval stays in
mycelium-extra; here the user confirms the plan in chat.
-->

Use this guide at step 3 of `$mycelium:analyze`. It turns a request into a
short plan where every consequential choice says where it came from, so the
user can correct a wrong assumption before anything runs rather than after.

Nothing in sections 3–7 writes to the project (analysis folders, manifests,
`.living/`) or runs analysis code. A session handoff that a lifecycle hook asks
for is bookkeeping, not analysis; write it as usual. The plan is recorded
(section 8) only after the user confirms it or skips the discussion.

## 1. Offer the discussion

Decide whether to offer:

- **New analysis**: offer.
- **Existing analysis**: read the `**Planning**:` line in its documentation
  file. If it reads `discussed` or `skipped`, do not offer again. Otherwise
  (absent or `pending`), offer only for new work on the analysis: a new
  question, or a new method for its question. Reruns, debugging, and
  parameter changes never get the offer.

Ask once, in plain text, before any planning question. Do not use a
host-specific question tool, so the offer reads the same in Claude Code and
Codex. Say what the discussion involves:

```text
Before I run anything, would you like a short planning discussion?
I'd ask at most 5 questions, one at a time, each with the evidence I found
and a recommended answer. You'd get a sourced plan to confirm before
anything runs.

  yes (recommended) — plan first
  skip — go straight to the analysis
```

A bare "yes", "ok" or "go ahead" starts the discussion, and so does any reply
that does not decline. "skip", "no" or "just run it" skips it.

## 2. If the user skips

Go straight to the analysis and ask no planning questions. Still write a short
plan, from repository evidence and labelled defaults, in the format of
section 7. Your first message after the skip is the plan, sent before you
create a file or run anything, so the user sees the defaults before the
results. A choice the user already stated in the request keeps its `user`
source; do not ask about anything else. Each other choice the user would own
becomes a labelled default (`default: planning skipped; <reason>`), and the
most consequential of them goes first under Main risk, so the user can
correct it later. Do not wait for a reply.
Record `**Planning**: skipped YYYY-MM-DD` (section 8). The data contract check
at step 6 of analyze still runs.

## 3. Ask for the question

Ask the user for the question in their own words and the result they hope to
claim, in one message, with no drafted options or examples: a drafted answer
anchors the user to it. Ask only for these two things; save anything else for
section 6. Open with one sentence restating only what the user
has already said. If the request already states the question or the claim in
the user's words, quote it and ask only for the missing part; if it states
both, skip this question. This message counts toward the limit of 5.

## 4. Retrieve before asking

Finding facts is your job, not the user's. Never ask what a file, a search, or
a read-only probe can answer. Before stating that something is absent, search
for it.

Read narrowly, in this order:

- The analysis documentation file and `analysis/ANALYSIS_MANIFEST.md`. If a
  finding or prior analysis already answers the question, say so first and make
  reuse versus re-run an explicit plan row.
- `data/DATA_MANIFEST.md` and the provenance for each dataset the analysis uses.
- `.living/conventions/ACTIVE_CONVENTIONS.yaml`, `.living/conventions.md`, and
  the installed packs' `analysis-conventions.md`.
- `.living/decisions.md` for the choices the plan depends on. When more than
  one entry speaks to a choice, settle it with
  `skills/core/references/decision-status.md`; a contested choice becomes a
  `user` question.
- `.living/learnings.md` for gotchas about these data or methods.

When a data property matters (columns, factor levels, sample counts), check it
with a read-only probe that writes nothing. Use only these forms, which
Mycelium's lifecycle hooks treat as reads rather than analysis runs: `head`,
`wc`, or `rg` on a file; `python3 -c '...'`; `Rscript -e '...'`; or a heredoc
piped to `python3 -` or `Rscript -`. Prefix the repository's environment
runner when it documents one (`conda run -n <env> python3 -c '...'`). Do not
use `R -e`, which Mycelium counts as an analysis run. For example:

```bash
head -1 data/raw/samples.csv
```

```bash
python3 -c 'import csv; r = list(csv.DictReader(open("data/raw/samples.csv"))); print(len(r), sorted({x["cohort"] for x in r}))'
```

```bash
Rscript -e 'd <- read.csv("data/raw/samples.csv"); print(nrow(d)); print(table(d$cohort))'
```

The data-lineage hook may record which files a probe read. That is
provenance: it asks nothing of you and never blocks Stop.

A probe's output is an observation, not a source for an expectation. Tag it
`[agent-derived: probe of <file>]`. It can confirm or contradict what the
manifest, a decision, or the user says, and a contradiction is worth raising.
But a value you only learned by probing the table (its row count, its levels)
cannot become the plan's expectation for that table, or the data contract
would check the data against itself and always pass.

Use the interpreter or environment the repository documents. If a probe fails,
do not debug it: mark the property unverified and make checking it the plan's
first step. **Never run a repository script by path** while planning
(`python3 analysis/<name>/run.py`, `bash run.sh`): that is analysis, so the
post-action hook opens its bookkeeping cycle and Stop blocks until `.living/`
is updated.

## 5. Source every decision

Walk `skills/analyze/references/analysis-decisions.md` (the design choices a
plan must settle) and `skills/analyze/references/planning-failure-modes.md`
(the mistakes a plan must guard against). Skip rows that do not apply. Settle
each consequential decision by the first rule that applies:

1. The repository settles it: cite it as `repo: <path>`.
2. A defensible technical choice exists and does not settle something the user
   owns (the scientific question, the estimand, an accepted trade-off): choose
   it and label it `default: <reason>`. "standard" or "usual" is not a reason.
3. Plausible answers would change the question, data, method, interpretation,
   or deliverable, and the choice is the user's: it is a `user` question
   (section 6).
4. Otherwise drop it. If a later stage needs it, park it with the condition
   that brings it back.

When the repository contradicts the request, do not silently override either
side. Show the evidence, recommend a position, and make it a `user` question.

## 6. Ask what only the user can answer

At most 5 questions in total, counting section 3. Ask one per message, most
consequential first, in this form:

```text
Q2. Unit of replication
Evidence: 3 donors per arm, 4 samples each (data/DATA_MANIFEST.md).
Options: (a) donor, with pseudobulk per donor (b) sample
Recommended: (a), because the samples within a donor are not independent.
```

Word the `Recommended:` line so that "yes" accepts it. After each answer,
decide for yourself (do not ask the user) whether any remaining user-owned
choice would change the plan; if none would, stop asking. At the limit, turn the remaining user-owned choices into labelled
defaults: the confirmation step is where the user overrides them. If the user
says "good enough", stop asking and write the plan. If the user says "just run
it" partway through, stop asking, show the plan with the remaining user-owned
choices as labelled defaults, and go ahead without waiting; record it as
`discussed`.

## 7. Present the plan and wait

Keep the prose under 150 words. The table and the lines below do not count
toward it. Quote the question and the claim only in words the user actually
wrote; if the user did not state one (a skip can leave the claim unstated),
write `not stated`, and never write words for the user.

```text
> Question (user's words): "<answer>"
> Hoped-for claim: "<answer>"

| # | Step | Choice | Source | Check |
| 1 | Unit of replication | donor; pseudobulk per donor | user | data contract: unit_of_replication |
| 2 | QC threshold | mito fraction < 0.2 | repo: .living/conventions.md | count cells removed per donor |

Facts:
- The contrast is treated vs control [human-stated]
- 3 donors per arm [agent-derived: data/DATA_MANIFEST.md]
- Cell Ranger 7 used GRCh38-2020-A [agent-asserted: ingest provenance, not opened]
Fact count: 1 human-stated, 1 agent-derived, 1 agent-asserted.

Defaults: <each default row, one line, and how to override it>
Main risk: <the failure mode most likely to mislead, and the check that catches it>
Parked: <item> — returns when <condition>
Decisions to record: <user answers and defaults to log once work begins>
```

The Check column names what actually checks the row: a data contract kind, a
`scilintr` rule, or a manual step. Never cite a tool for something it does not
inspect; `skills/analyze/references/planning-failure-modes.md` says what each
check covers. When nothing checks a row, write `none` or the manual step.

Tag every fact the plan rests on, one fact per line, each with its own tag, so
the user can find and check each `agent-asserted` one:

- `[human-stated]`: the user said it in this conversation.
- `[agent-derived: <path>]`: you read it in that file or probe output.
- `[agent-asserted: <source>]`: you believe it but did not read it here. Name
  where it should come from. These are what the user most needs to check.

Then stop and wait for the user to confirm. "yes" confirms. An edit revises the
plan; show the changed rows and wait again. A confirmation describes the plan;
it does not authorize steps the plan does not name.

## 8. Record

After the user confirms or skips:

- In the analysis documentation file's `## Plan` section, set the status line
  to `**Planning**: discussed YYYY-MM-DD` or `**Planning**: skipped YYYY-MM-DD`
  and paste the plan below it.
- Log each user answer, and each default the user confirmed, to
  `.living/decisions.md` with the decision template. Cite earlier entries by
  date and exact heading.
- Take the data contract's values (analyze step 6) from plan rows sourced to
  the user, a decision, the manifest, or a stated default, never from a probe
  of the table the contract checks. Where the plan has no such source for a
  value, leave that check out of the contract and say so.
