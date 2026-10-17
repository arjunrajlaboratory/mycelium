# Prose tics — catalogue for the final prose pass

The Phase 6.5 final prose pass removes the patterns below from a finished report. Each entry gives the tell, a before/after pair, and when the pattern is fine. The standing prose rules in Phase 2 (no mannered prose; concrete subjects) apply from the first draft; this catalogue is the concrete list that the last pass checks against.

## What the pass never changes

The pass edits wording only. A replacement keeps, exactly as they were:

- every number, value wrapper (`\SciVal{...}{...}`, `\SciText{...}{...}`, or an HTML `data-sci-val` / `data-sci-text` span) and its text;
- every figure, table, and section reference, and every citation;
- every caveat, and the strength of every claim: a hedge stays a hedge, and "suggests" does not become "shows";
- canonical and defined terms, and quoted data examples;
- for a slide title, its single point and its one-sentence shape.

The pass also never adds content: no new fact, figure reference, measured basis, or example that the sentence did not already carry. Every *After* below is built only from its *Before*. If removing a tic would change any of these, leave the sentence alone. A sentence that is wrong, not just mannered, goes back to Phase 2.

## 1. Stock intensifiers and signposting

Tells: "Notably,", "Importantly,", "Crucially,", "Strikingly,", "Interestingly,", "Remarkably,", "It is worth noting that", "It should be emphasized that".

- *Before:* "Notably, treated cells grew more slowly than controls."
- *After:* "Treated cells grew more slowly than controls."
- *Before:* "It is worth noting that the effect vanished at the highest dose."
- *After:* "The effect vanished at the highest dose."

If a result matters more than its neighbors, say why ("This is the only condition in which..."), not that it does.

## 2. Rhetorical reversals

Tells: "not just X, but Y", "not merely X; Y", "X isn't about A, it's about B", "rather than X, Y" where no reader held X.

- *Before:* "Rather than a simple filter, the score is a calibrated probability."
- *After:* "The score is a calibrated probability."
- *Before:* "The model does not just fit the data; it predicts the held-out replicate."
- *After:* "The model fits the data and predicts the held-out replicate."

Fine when X is a live alternative the reader could hold: "Growth slowed rather than stopped" reports what the data ruled out.

## 3. Dash rhythm and cadence triplets

Tells: more than one dash pair in a paragraph; asides set off by dashes for drama; lists of three adjectives or clauses where the third was added for rhythm.

- *Before:* "The method is fast and accurate — a rare combination — and it runs on a laptop."
- *After:* "The method is fast and accurate, and it runs on a laptop."

Use a comma, parentheses, or a second sentence instead of a dash pair. Keep a three-item list when there are three real items.

## 4. Grand flourishes and inflated verbs

Tells: "underscores", "highlights the power of", "a testament to", "paves the way", "opens new avenues", "sheds light on", "lays the groundwork for", "plays a pivotal role", "in the landscape of"; "serves as", "stands as", "leverages", "utilizes", "facilitates" where "is", "uses", or "lets" would do.

- *Before:* "Evidence-first calling recovered all six known clones (Figure 2). These results underscore the power of the approach and pave the way for clone tracing at scale."
- *After:* "Evidence-first calling recovered all six known clones (Figure 2)." (The second sentence adds no claim the first does not make.)
- *Before:* "The panel serves as a validation set."
- *After:* "The panel is a validation set."

## 5. Vague boosters

Tells: "robust", "comprehensive", "novel", "key", "critical", "powerful", "rich", "significant" (outside statistical significance), with no measured basis in the sentence.

- *Before:* "We present a novel, robust pipeline for comprehensive clone calling."
- *After:* "We present a pipeline for clone calling."
- *Before:* "Coverage is a key driver of call quality (Figure 3)."
- *After:* "Coverage drives call quality (Figure 3)."

Fine when the measured basis is in the same sentence: "robust to the seed: calls agree across all ten seeds". "Significant" is reserved for a named statistical test. The pass may delete a booster with no basis; it may not add a basis the report does not already state.

## 6. Hedging stacks

Tells: two or more hedges on one claim: "may potentially suggest", "could possibly indicate", "appears to perhaps", "might be somewhat consistent with".

- *Before:* "These data may potentially suggest a possible role for IL6."
- *After:* "These data suggest a role for IL6."

Collapse the stack to one hedge that matches the evidence. Never remove the last hedge: that changes the claim.

## 7. Throat-clearing openers and recap closers

Tells: "In this section, we...", "It is important to understand that", "As mentioned above,"; "In summary,", "Overall,", "Taken together,", "In conclusion," opening a sentence that synthesizes nothing; a closing sentence that restates the paragraph.

- *Before:* "Taken together, treated cells grew more slowly." (after a single result)
- *After:* "Treated cells grew more slowly."
- *Before:* "...in all three doses. Overall, the treatment slowed growth."
- *After:* "...in all three doses." (the paragraph already said it)

Fine when the sentence combines two or more earlier results into a claim none of them makes alone.

## 8. Elegant variation

Tells: one thing called by rotating names to avoid repetition: "clone", then "lineage", then "population", then "subgroup".

- *Before:* "We called twelve clones. Each lineage held at least fifty cells, and the largest population..."
- *After:* "We called twelve clones. Each clone held at least fifty cells, and the largest clone..."

In a report a new word reads as a new thing. Repeat the canonical term.

## 9. Concept-as-subject nominalizations

Tells: an idea or process as the grammatical subject where a concrete actor was available: "the application of", "the observation that", "the fact that", "this finding demonstrates", "the improvement in X reflects".

- *Before:* "The application of the coverage filter resulted in the removal of low-quality cells."
- *After:* "The coverage filter removed low-quality cells."
- *Before:* "This finding demonstrates that the clones are stable."
- *After:* "The clones are stable."

This is the Phase 2 concrete-subjects rule, a general rule rather than an exact one: a concept subject is fine when the concept is the topic (a definition, a named hypothesis).

## 10. Colon-headline sentences

Tells: a short setup, a colon, then the point: "The result: ...", "The upshot: ...", "The takeaway is simple: ...", "Here's the catch: ...".

- *Before:* "The upshot: evidence-first calling trades recall for precision."
- *After:* "Evidence-first calling trades recall for precision."

Fine for a colon that introduces a list or a definition, and for a finding-form subsection title with a colon clause.
