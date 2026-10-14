# Decision status — which past decision binds a task

<!--
Adapted from the decision-status skill in mycelium-extra
(https://github.com/mdmanurung/mycelium-extra), Copyright (c) 2026 Mikhael
Manurung, MIT License. See issue #91.
-->

`.living/decisions.md` is append-only, so one choice (a method, cohort,
contrast, threshold, or tool) can collect several entries over time: confirmed,
held, shelved, rejected, revisited. Use this guide when a task depends on such a
choice and more than one entry speaks to it, or when the user asks which
decision is current. Settle what the log itself settles, ask the user about the
rest, and record the answer so the question is not asked again.

The newest entry never wins by recency alone.

## The fields

Entries written from `skills/core/templates/decision-log-entry.md` carry these
fields. Older entries predate them; read those by their text.

- `**Status**:` is the entry's own state when it was written:
  - `active`: binds now.
  - `provisional`: binds now, but is expected to be revisited. Name the trigger
    in `**Revisit when**:`.
  - `held`: the choice is paused. Nothing replaces it yet, and the option
    stays open.
  - `shelved`: set aside for now. It may come back, and it does not undo an
    earlier `active` entry about a different option.
  - `rejected`: the option was ruled out for a stated cause.

  An entry with no `**Status**:` line is `unknown`, not `active`. Nothing writes
  `superseded` on an entry: older entries are never edited, and only a newer
  entry's `**Supersedes**:` line replaces one.
- `**Supersedes**:` lists the earlier entries this one replaces, each cited by
  date and exact heading title (`[2026-09-01] Use ComBat for batch
  correction`), separated by `; `. Do not cite by a positional ID such as `D-7`.
  Those IDs count entries in file order, so they shift whenever an earlier
  heading is added, removed, or relevelled. Use them to look entries up, never
  to cite them.
- `**Scope**:` says what the decision covers (dataset, stage, estimand) and
  what it does not.
- `**Revisit when**:` names the condition that reopens the decision.
- `**Resolved-by**:` appears only on an entry that records the user's answer to
  a contested thread (`user, YYYY-MM-DD`).

## 1. Retrieve the touched thread

- Name the decision points the task depends on (for example batch correction,
  the DE model, a cohort filter).
- Rules 1–3 below need the whole thread, so fetch it with the lookups below.
  Pass both flags to every lookup:
  - `--max 0`: without it, `recall_lessons.py` keeps only the 20 newest
    matches, which can drop the oldest entry or the start of a supersession
    chain.
  - `--follow`: this also returns every later entry that cites a found entry by
    its heading title, as a `**Supersedes**:` line does, until no new entry
    appears. A successor often names only the new method (`DESeq2`), so a
    search for the old one (`limma`) misses it without `--follow`.
- Run all three lookups and take the union.
  - **By tag.** Find candidate tags in `.living/INDEX.md` (**By tag**), then:

    ```bash
    python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py" --living-dir .living --file decisions --max 0 --follow --tag <tag>
    ```

  - **By text.** Many entries carry no tags, and a method may be named only in
    an entry's body. Search the full entry text for the method names and
    synonyms. Each `--term` is a case-insensitive literal, and repeated terms
    match any of them:

    ```bash
    python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py" --living-dir .living --file decisions --max 0 --follow --term <term> --term <synonym>
    ```

  - **By relationship wording.** Some entries link to an earlier one without
    naming it ("supersedes the entry above", "reverses the earlier call").
    `--follow` cannot see those links. Fetch every entry that uses such
    wording, from `<oldest-date>` (the date of the oldest thread entry found
    so far) onward, and keep only those that refer to a thread entry:

    ```bash
    python3 "$(cat .mycelium/plugin-root)/skills/core/scripts/recall_lessons.py" --living-dir .living --file decisions --max 0 --follow --since <oldest-date> --term supersede --term replace --term overturn --term revers --term amend --term addendum --term 'entry above'
    ```

  Every lookup prints whole entries. Use `--count-only` first if a thread
  looks large. To get line numbers for citations, run
  `grep -n '^### ' .living/decisions.md`.
- Read the body of every entry that could bind the task before resolving
  anything. A heading word such as "held" may be qualified in the body.
- Work through each thread oldest first. `recall_lessons.py` prints newest
  first.

## 2. Resolve by rule

For each decision point, apply the first rule that fits:

1. **Explicit supersession.** An entry replaces an earlier one for its stated
   scope when it names that entry:
   - in a `**Supersedes**:` line, or
   - in replace, supersede, overturn, or reverse wording in its heading or body
     ("supersedes the entry above").

   Follow the chain to its end. An addendum or amendment does not replace the
   original. It binds together with it and overrides only the part it changes.
2. **Scope match.** Entries that answer different questions (different
   datasets, stages, or estimands) do not conflict, and the entry whose scope
   matches the task binds. Trust a `**Scope**:` line as a binding scope only on
   an entry that also has a `**Status**:` line or a `**Resolved-by**:` line.
   Older entries sometimes use "Scope" for something else, such as what a fix
   covered.
3. **Contested.** If neither rule settles it, the thread is contested. Status
   words are not interchangeable: `held`, `shelved`, `rejected`, and a
   superseded entry each leave different options open.

You may say an entry looks superseded and why, but do not treat it as replaced
until the log or the user says so.

## 3. Ask only what is contested

- Ask only about a decision point that the task touches and that falls into one
  of these cases:
  - two or more entries are still unresolved after rules 1–2;
  - a premise an entry names has changed (new data, or a
    `**Revisit when**:` condition that now holds).
- Ask one question per message, most consequential first. Quote the competing
  entries by date and exact heading (with the line number from `grep -n`), say
  what each implies for the task, and recommend one.
- Ask at most **three questions per session**. Carry any remaining contested
  point forward as `contested` with your recommended reading, and name it in
  your answer. Do not quietly pick one.

## 4. Record the user's answer

Once the user explicitly confirms a resolution, append one new entry to the end
of `.living/decisions.md`. Never edit, reorder, renumber, or delete an existing
entry.

```markdown
### [YYYY-MM-DD] Resolution: <decision point>

**Context**: <the task that raised it; the entries in conflict>

**Decision**: <what binds now, in the user's words>

**Status**: active

**Supersedes**: [YYYY-MM-DD] <exact heading title>; ...

**Scope**: <what this covers, and what it does not>

**Revisit when**: <condition, or "user reopens it">

**Resolved-by**: user, YYYY-MM-DD

**Tags**: <the thread's tags, so the next lookup finds this entry>
```

- In `**Supersedes**:` list only entries the user agreed are replaced. An
  entry that stays valid for a narrower scope goes in `**Scope**:`, not in
  `**Supersedes**:`.
- Do not cite line numbers in the entry. They shift as the log grows.
- Write nothing if the user did not confirm, and never record a resolution you
  inferred yourself.

## 5. Report

End with a short table with one row per touched decision point. Give:

- the decision point;
- the binding entry (date, exact heading, line), or `contested`;
- the rule that settled it (`supersedes`, `scope`, `user`, or `contested`);
- what it means for the task.

Below the table, list any entry you wrote and any contested point carried
forward.
