# Grill mode

This file is consulted by the main `/mycelium:review` skill when invoked with
`grill` (e.g., `/mycelium:review grill`, "grill me on this analysis").

## What grill mode is

A conversational interview about every consequential decision in the
analysis. The aim is to help the user articulate the *why* behind their
choices and surface the ones they haven't actually thought through.

Grill mode is **not** a wall-of-text checklist. The opposite. The point is
that one question at a time, conversationally, the user can answer
quickly and move on, but if they hit a question they can't answer, they
discover it themselves rather than being told by a list.

Think of it as the most useful kind of senior-colleague conversation: the
colleague has read your code, has questions, asks one at a time, and
listens to your answers.

## Inputs

Before starting the interview, gather:
- The analysis script(s) and their inputs/outputs
- `.living/decisions.md` (the explicit decision log)
- The analysis's documentation file (UPPER_SNAKE_CASE.md)
- `specification.md` if present
- Any installed convention packs in `.living/conventions/`
- Recent commits if the analysis has been iterated

Compile a working list of consequential decisions. Don't show this list
to the user.

## What counts as a "consequential decision"

A decision that, if changed, would meaningfully change the result. The
canonical categories (use this as your search list, not your script):

1. **Estimand and question framing**
   - What is the analysis claiming, exactly? Causal? Predictive? Descriptive?
   - What population is the claim about?
   - Was a specific hypothesis pre-registered or was this exploratory?

2. **Sample / cohort definition**
   - Who's in? Who's out?
   - Inclusion/exclusion criteria — were they pre-specified or
     data-derived?
   - Sample size: was a power calculation done, or is n driven by
     convenience?

3. **Reference / comparator / baseline**
   - For genomics: which reference genome? which annotation?
   - For ML: what's the baseline being beaten?
   - For trials: what's the comparator arm?

4. **Variable definitions**
   - Continuous vs categorical handling — was anything dichotomized?
     why? where did the cutpoints come from?
   - Outcome construction — composite or single? if composite, how
     weighted?
   - Time of measurement — first, last, max, mean over a window?

5. **Filtering and QC thresholds**
   - For scRNA-seq: mt%, gene count, doublet detection
   - For RNA-seq: low-count filtering
   - For ML: outlier removal, missing-value handling
   - In each case: where did the threshold come from?

6. **Normalization / preprocessing choices**
   - Why this normalization?
   - Was the same normalization applied to all comparison arms?
   - For scRNA-seq specifically: is `adata.X` raw, log, or scaled at
     the time of each downstream call?

7. **Model / test choice**
   - Why this test / model?
   - Are its assumptions checked?
   - Are alternatives considered?

8. **Multiple comparisons**
   - How many tests are run? all reported?
   - Bonferroni vs FDR — and why?
   - Subgroup analyses pre-specified or post-hoc?

9. **Adjustment / confounding**
   - Which covariates are adjusted for? why?
   - Was a DAG drawn, or are covariates chosen by stepwise / kitchen-sink?
   - Are any potential adjustment variables actually colliders or
     mediators?

10. **Train/test / CV strategy** (for ML)
    - Is the split temporal? grouped by subject?
    - Where does preprocessing fit relative to the split?

11. **Robustness / sensitivity**
    - What did you re-run with different choices to check fragility?
    - Did the conclusion hold? If not, which choices flipped it?

12. **Stopping criteria / iteration**
    - Did you keep tweaking until the result looked right? where did
      you stop?
    - Were caveats from earlier iterations dropped from the final
      version?

13. **Figures / reporting**
    - n in the figure caption matches code?
    - Error bars labeled (SD/SE/CI)?
    - Axis labels include units?
    - Do plot descriptions in prose actually match the rendered plot?

## How to ask

### One question per turn

Start with **one** question, the most consequential one for this
analysis. Wait for the answer. Acknowledge it briefly. Ask the
next.

Bad:
> Here are 14 questions about your analysis: 1. What's your
> estimand? 2. How did you choose your cutpoints? 3. ...

Good:
> The analysis claims X. Is that meant as a causal claim, a
> predictive claim, or a descriptive observation about this
> sample? I want to make sure we're calibrating the rest of the
> review to the right ambition.

### Lead with the question that matters most for this analysis

Don't go in checklist order. Look at the analysis and pick the
choice that has the highest blast radius if wrong. Common picks:

- For a causal analysis: estimand and adjustment set
- For a small-n trial: power, ITT vs per-protocol, multiple
  outcomes
- For scRNA-seq: which clustering / DE pipeline and where
  pseudoreplication sits
- For an ML model: train/test split strategy and baseline

### Use the answer to choose the next question

The grill is not a fixed sequence. It's adaptive. If the answer to
the first question is solid and well-reasoned, move to a different
category. If the answer reveals confusion, dig there.

Specifically: if the user gives a justification that itself reveals
a problem ("we used t-test because that's what the tutorial used"),
**don't lecture**. Ask the next question that exposes the
implication:

> Got it — and the data here is counts per gene, right? what do
> we expect t-test assumptions to look like on counts?

This is more respectful of theory of mind than "well actually
t-test isn't appropriate here because..." It also gives the user
the satisfaction of figuring it out themselves, which makes the
review stick.

### Keep it short

Each question should be a sentence, two at most. The user is
answering in chat — don't give them homework.

### Don't ask things you can verify yourself

If the answer is in the code or in `.living/decisions.md`, don't
ask. Ask the *implication* instead: "I see you're filtering at
mt% < 5% — is that tissue-appropriate here?"

## When to stop

Stop when **any** of these is true:
- ~5–8 substantive exchanges (more than that and the user gets
  tired regardless of how good the questions are)
- The user signals "enough" / "let's stop" / "moving on" / "OK
  good" / "next" — even partial signals are fine
- You've covered the highest-blast-radius choices and the
  remaining categories don't apply or are clearly fine
- The user's answers have stayed at the level of justification
  for ~3 in a row (further questions become hair-splitting)

## How to wrap up

After stopping, write a short summary in chat with three buckets:

1. **Solid** — choices the user clearly justified, where I have no
   remaining concern
2. **Reconsider** — choices the user couldn't fully justify or that
   surfaced a concern during the conversation. For each, one
   sentence on what to think about and a suggested next step
3. **Defer** — choices we didn't get to, with a one-line reason
   why they may be worth a second look

Offer to file the **Reconsider** items as `todo/` items via
`/mycelium:core todo-idea`. Don't auto-file — ask first.

If the user wants the summary written to a file rather than just
in chat, write it to
`.living/outputs/reviews/YYYY-MM-DD-grill-<analysis>.md`.

## Tone

The grill is supposed to feel like talking to a thoughtful
colleague, not being interrogated. Specific guidelines:

- Acknowledge each answer in one short clause before moving on.
  Don't ignore the answer.
- Use plural pronouns when the analysis is the user's: "how should
  we think about X" reads better than "how should you think
  about X" because it implies you're thinking together.
- Don't pre-emptively defend any choice for the user. They might
  surprise you with a reason you wouldn't have thought of.
- It's OK to say "that's a good answer, I'll move on" and skip a
  question category entirely.
- It's OK to say "I'm out of useful questions — let's wrap up."

## Grill ≠ default review

If the user wants the comprehensive parallel-agent review, they
asked for that with the default invocation. Grill is intentionally
narrower: depth-first on judgment calls, not breadth-first on the
checklist. If you're tempted to "also run the default agents," ask
the user — it's their time budget.

## Grill ≠ tripwires

Grill and tripwires are complementary but distinct. Grill asks the
*analyst* what should happen at each decision point ("what would
happen if a sample were missing from metadata?"). Tripwires
(`deep-tripwires.md`, offered after the default review) ask the
*pipeline* what actually happens ("here's what the pipeline does
when I delete a metadata row"). When a grill answer is "I think it
would fail loudly" and you're not sure, that's a natural moment to
suggest the matching fault-injection tripwire as a follow-up.
