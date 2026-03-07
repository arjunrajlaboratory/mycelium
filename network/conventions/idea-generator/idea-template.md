# Idea Template

Every persona-generated idea follows this structure. The template ensures ideas are concrete and actionable, not vague aspirations. Each field serves a purpose: grounding the idea in a specific perspective, connecting it to existing data, and making it executable.

---

## Template

```markdown
# [Idea Title]

## Persona
**[Persona Name]** — [Brief description of the disciplinary lens being applied]

## Motivation
Why this is interesting from this persona's perspective. What pattern, question, or
analogy from the persona's discipline makes this idea compelling? This should make
clear *why this persona specifically* would think of this — not just anyone.

## Connection to Existing Data
What specific datasets, analyses, or results in the current project make this idea
feasible? Reference concrete outputs, figures, or findings. An idea disconnected
from available data is a wish, not a plan.

## Approach
Concrete analytical steps. Each step should be specific enough that someone could
start implementing it.

1. [First step — what data to start with, what to compute]
2. [Second step — what analysis or transformation to apply]
3. [Third step — what to compare or visualize]
4. [Fourth step — how to validate or test the result]
5. [Optional fifth step — extension or follow-up]

## Expected Insights
What might we learn? Be specific about the *kind* of answer, even if the actual
answer is unknown. "We'd learn whether X correlates with Y" is better than
"We'd learn something interesting about X."

## Feasibility
- **Effort**: [Low / Medium / High]
- **Data ready**: [Yes / Mostly / Needs preprocessing / Needs new data]
- **Methods available**: [Standard tools / Needs custom implementation / Research-grade]
- **Key risk**: [The main thing that could make this not work]
```

---

## Guidance for Subagents

When generating ideas, keep these principles in mind:

- **Be specific, not generic.** "Apply machine learning" is not an idea. "Train a VAE on the gene expression matrix and examine whether the latent dimensions correspond to known biological axes" is an idea.
- **Connect to what exists.** Every idea must reference specific data or analyses already in the project. If you can't point to the concrete input, the idea isn't grounded.
- **Lean into your persona.** The value is in the disciplinary lens. A causal inference idea should use causal inference vocabulary and methods. An ecology idea should use ecological frameworks. Don't dilute the perspective.
- **Two ideas per persona.** One can be more ambitious, one more tractable. This gives a range of effort levels.
- **Feasibility honesty.** If it's hard, say it's hard. A brilliant but infeasible idea is still worth recording — but label it honestly.
