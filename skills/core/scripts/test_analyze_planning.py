"""Contract tests for the planning discussion in the analyze skill.

analyze offers an opt-in planning discussion before the first run. Its guide
and coverage lists live under skills/analyze/references/. The analysis
documentation template records whether the discussion happened, so the offer
is made once per analysis. These tests pin the decisions recorded for
recommendation 3 of the mycelium-extra review (issue #91).
"""

from __future__ import annotations

import re
from pathlib import Path

import data_contract_check

PLUGIN_ROOT = Path(__file__).resolve().parents[3]
ANALYZE = PLUGIN_ROOT / "skills" / "analyze" / "SKILL.md"
REFERENCES = PLUGIN_ROOT / "skills" / "analyze" / "references"
PLANNING = REFERENCES / "planning.md"
DECISIONS = REFERENCES / "analysis-decisions.md"
FAILURE_MODES = REFERENCES / "planning-failure-modes.md"
TEMPLATE = PLUGIN_ROOT / "skills" / "core" / "templates" / "analysis-readme.md"
SCILINTR_GUIDE = PLUGIN_ROOT / "skills" / "core" / "references" / "scilintr-guide.md"
REVIEW_DIR = PLUGIN_ROOT / "skills" / "core" / "references" / "review"


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _steps(path: Path) -> list[str]:
    """Top-level numbered steps of a SKILL.md, in order."""
    return re.findall(r"^\d+\. (.+)$", path.read_text(encoding="utf-8"), re.M)


def _section(path: Path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    body = text.split(heading, 1)[1]
    return re.split(r"^## ", body, maxsplit=1, flags=re.M)[0]


def test_analyze_offers_planning_before_creating_or_running_anything() -> None:
    steps = _steps(ANALYZE)
    planning = next(i for i, s in enumerate(steps) if "planning discussion" in s)
    create = next(i for i, s in enumerate(steps) if s.startswith("**New analysis**"))
    contract = next(i for i, s in enumerate(steps) if "data assumptions" in s)
    assert planning < create < contract
    assert "skills/analyze/references/planning.md" in _flat(ANALYZE)
    for reference in (PLANNING, DECISIONS, FAILURE_MODES):
        assert reference.is_file()


def test_step_numbers_cited_by_the_guides_match_analyze() -> None:
    steps = _steps(ANALYZE)
    planning_step = 1 + next(i for i, s in enumerate(steps) if "planning discussion" in s)
    contract_step = 1 + next(i for i, s in enumerate(steps) if "data assumptions" in s)
    assert f"step {planning_step} of `$mycelium:analyze`" in _flat(PLANNING)
    # Every other analyze step a guide cites is the data contract step.
    for path in (PLANNING, DECISIONS):
        cited = re.findall(r"step (\d+) of analyze|analyze step (\d+)", _flat(path))
        numbers = {int(a or b) for a, b in cited}
        assert numbers == {contract_step}, (path.name, numbers)


def test_opt_in_question_defaults_to_yes_and_skip_asks_nothing_more() -> None:
    planning = _flat(PLANNING)
    offer = " ".join(_section(PLANNING, "## 1. Offer the discussion").split())
    # The offer says what the discussion involves before the user chooses.
    assert "5 questions" in offer
    assert "one at a time" in offer
    assert "confirm before anything runs" in offer
    # Yes is the recommended default, so a bare reply starts it.
    assert "(recommended)" in offer
    for reply in ("yes", "ok", "go ahead"):
        assert f'"{reply}"' in offer
    # A skip goes straight to the analysis with no planning questions.
    assert "ask no planning questions" in planning
    # Plain text so Claude Code and Codex behave the same.
    assert "plain text" in offer
    assert "host-specific question tool" in offer


def test_offer_is_made_once_per_analysis_from_a_recorded_field() -> None:
    template = TEMPLATE.read_text(encoding="utf-8")
    planning = PLANNING.read_text(encoding="utf-8")
    field = "**Planning**:"
    assert field in template
    # The record step writes exactly the values the offer step reads back.
    for value in ("discussed YYYY-MM-DD", "skipped YYYY-MM-DD"):
        assert f"`{field} {value}`" in planning
    offer = " ".join(_section(PLANNING, "## 1. Offer the discussion").split())
    assert f"`{field}`" in offer
    assert "reruns" in offer.lower() and "debugging" in offer
    # SKILL.md and the guide agree on what new work earns an offer on an
    # existing analysis: a new question or a new method, never a rerun.
    for text in (offer, _flat(ANALYZE)):
        assert "a new method for its question" in text
    assert "`pending`" in offer


def test_skip_still_yields_a_sourced_plan_and_the_contract_check_runs() -> None:
    skip = " ".join(_section(PLANNING, "## 2. If the user skips").split())
    assert "repository evidence and labelled defaults" in skip
    assert "do not wait" in skip.lower()
    # Choices the request already states stay user-sourced; the rest become
    # defaults the user can find and correct.
    assert "keeps its `user` source" in skip
    assert "`default: planning skipped; <reason>`" in skip
    assert "Main risk" in skip
    # Live test: a skip-path agent wrote the plan into the doc after the run,
    # so the user never saw the defaults first. The plan is the first message.
    assert "Your first message after the skip is the plan" in skip
    assert "before you create a file or run anything" in skip
    # The data contract check runs whichever way the user chose.
    assert "runs either way" in _flat(ANALYZE)


def test_every_decision_is_sourced_and_every_fact_tagged() -> None:
    planning = _flat(PLANNING)
    for source in ("`repo: <path>`", "`default: <reason>`", "`user`"):
        assert source in planning
    assert "drop it" in planning
    for tag in (
        "`[human-stated]`",
        "`[agent-derived: <path>]`",
        "`[agent-asserted: <source>]`",
    ):
        assert tag in planning
    assert "Fact count:" in planning


def test_questions_are_capped_and_asked_one_per_message() -> None:
    planning = _flat(PLANNING)
    assert "At most 5 questions" in planning
    # Deciding whether to continue is the agent's job; asking would spend the
    # user's question budget on process.
    assert "decide for yourself (do not ask the user)" in planning
    # Bailing out partway still ends in a shown, recorded plan.
    assert '"just run it" partway through' in planning
    assert "one per message" in planning
    assert "Recommended:" in planning
    # The opening question is asked without drafted options, to avoid anchoring.
    opening = " ".join(_section(PLANNING, "## 3. Ask for the question").split())
    assert "own words" in opening
    assert "no drafted options" in opening
    # Live test: the agent added a sub-question with drafted example answers.
    assert "Ask only for these two things" in opening


def test_plan_check_column_cites_tools_only_for_what_they_inspect() -> None:
    # Live test: a plan named "scilintr" as the check for its choice of test,
    # which scilintr does not inspect.
    plan = " ".join(_section(PLANNING, "## 7. Present the plan and wait").split())
    assert "Never cite a tool for something it does not inspect" in plan
    assert "planning-failure-modes.md" in plan


def test_probe_results_never_become_contract_expectations() -> None:
    # Codex P1 on #114: a value read from the table and copied into the
    # contract checks the data against itself and always passes.
    planning = _flat(PLANNING)
    assert "A probe's output is an observation, not a source" in planning
    record = " ".join(_section(PLANNING, "## 8. Record").split())
    assert "never from a probe of the table the contract checks" in record
    assert "never from a first look at the data" in _flat(ANALYZE)


def test_each_fact_carries_its_own_tag() -> None:
    # Live Codex run: the plan summarised provenance in one sentence, so no
    # single agent-asserted fact could be found and checked.
    plan = " ".join(_section(PLANNING, "## 7. Present the plan and wait").split())
    assert "one fact per line, each with its own tag" in plan


def test_plan_never_puts_words_in_the_users_mouth() -> None:
    # Codex P2 on #114: on a skip the user may never have stated a claim.
    plan = " ".join(_section(PLANNING, "## 7. Present the plan and wait").split())
    assert "`not stated`" in plan
    assert "never write words for the user" in plan


def test_planning_is_read_only_until_the_user_confirms() -> None:
    planning = _flat(PLANNING)
    # Live Codex run: the agent wrote the session handoff SessionStart asked
    # for. That is lifecycle bookkeeping, which the read-only rule allows.
    assert "session handoff" in planning
    assert "Never run a repository script by path" in planning
    # Probe forms are limited to those the hooks treat as reads; `R -e` is not.
    assert "Do not use `R -e`" in planning
    assert "never blocks Stop" in planning
    assert "post-action" in planning
    assert "wait for the user to confirm" in planning


def test_unit_of_replication_follows_the_assigned_unit() -> None:
    # Codex P1 on #114: a cell-versus-donor rule pseudoreplicates pooled
    # perturbation screens and has no answer for designs without donors.
    row = next(
        line for line in DECISIONS.read_text(encoding="utf-8").splitlines()
        if line.startswith("| Unit of replication |")
    )
    assert "otherwise the donor is" not in row
    assert "independently assigned" in row
    # Codex P2: observational contrasts assign nothing; the participant is the
    # independently sampled unit.
    assert "independently sampled" in row
    assert "mixed model" in row and "aggregat" in row
    # Codex P2: aggregating to the assigned unit leaves the levels above it
    # (wells from one donor) correlated, so they still need a term.
    assert "after aggregating" in row
    assert "blocking term or random effect" in row
    # Codex P1 on #114: in a pooled screen the guide can be the condition;
    # blocking on it would absorb the effect being estimated.
    assert "never the condition being tested" in row
    # Codex P1 (CLI round 5): the mixed-model random effect belongs on the
    # assigned unit the measurements share, never on the cell itself.
    assert "random effect for the assigned unit" in row
    assert "`(1 | donor)`" in row
    # Round 6: a nested effect for repeated measures of one cell is fine in
    # addition; it must not replace the assigned-unit effect.
    assert "in place of the assigned-unit effect" in row


def test_inherited_scientific_rows_hold_for_their_edge_cases() -> None:
    # Codex CLI round 6 found four rows (most inherited from upstream) whose
    # blanket rules are wrong for a common design.
    decisions = DECISIONS.read_text(encoding="utf-8")
    model = next(l for l in decisions.splitlines() if l.startswith("| Model or test |"))
    assert "no colliders or mediators" not in model
    assert "direct effect" in model
    # Codex P2: collider and mediator rules are for causal estimands; a
    # predictive model picks features by availability and leakage.
    assert "causal estimand" in model and "prediction time" in model
    modes = FAILURE_MODES.read_text(encoding="utf-8")
    seeds = next(l for l in modes.splitlines() if l.startswith("| Non-determinism |"))
    assert "inside each worker" not in seeds
    assert "`future.seed = TRUE`" in seeds and "L'Ecuyer-CMRG" in seeds
    scores = next(l for l in modes.splitlines() if l.startswith("| Score read as probability |"))
    assert "over-representation" in scores and "GSEA" in scores
    # Round 7: many-to-one joins repeat keys on one side by design.
    join = next(l for l in modes.splitlines() if l.startswith("| Join duplication |"))
    assert "both sides" not in join and "intended cardinality" in join
    # Round 7: penalized models tolerate rank deficiency.
    formula = next(l for l in modes.splitlines() if l.startswith("| Formula is not the model |"))
    assert "unpenalized" in formula
    # Round 7: unit_of_replication blocks repeated rows per unit, so it runs
    # on the per-unit table, not on observations a mixed model keeps.
    declaring = " ".join(_section(DECISIONS, "## Declaring a data check").split())
    assert "per-unit table" in declaring
    # Codex P2: `within` covers repeated measures such as visits, so a
    # longitudinal table keeps the check instead of dropping it.
    assert "`within`" in declaring and "visit" in declaring
    assert "the model's grouping term is the check" not in declaring


def test_contract_rows_follow_the_contract_guides_applicability() -> None:
    # The contract guide skips exploratory plots; the plan must not promise a
    # check that step 6 will never run.
    declaring = " ".join(_section(DECISIONS, "## Declaring a data check").split())
    assert "statistical test or model" in declaring
    assert "Exploratory plots" in declaring
    guide = _flat(PLUGIN_ROOT / "skills" / "core" / "references" / "data-contract-check.md")
    assert "Skip it for exploratory plots" in guide


def test_coverage_list_names_every_pre_run_decision_point() -> None:
    decisions = DECISIONS.read_text(encoding="utf-8")
    rows = re.findall(r"^\| ([^|]+?) \|", decisions, re.M)
    points = {row for row in rows if row not in {"Decision point", "---"}}
    assert {
        "Estimand and claim type",
        "Unit of replication",
        "Cohort and exclusions",
        "Input and matrix state",
        "Reference and identifiers",
        "QC thresholds",
        "Normalization and transformation",
        "Batch versus biology",
        "Annotation",
        "Model or test",
        "Multiplicity",
        "Leakage and circularity",
        "Validation and sensitivity",
        "Outputs and reporting",
    } <= points


def test_failure_modes_cite_only_checks_that_ship() -> None:
    # Upstream cites data-contract kinds and tools mycelium does not have.
    # A plan must never claim a check runs when it does not.
    text = FAILURE_MODES.read_text(encoding="utf-8")

    def cited(tool: str) -> set[str]:
        # "tool (`a`, `b`: what it covers)": every name before the colon.
        names: set[str] = set()
        for inside in re.findall(rf"{tool} \(([^)]*)\)", text):
            names |= set(re.findall(r"`([^`]+)`", inside.split(":", 1)[0]))
        return names

    kinds = cited("data-contract-check")
    assert {"schema", "cohort", "unit_of_replication"} <= kinds
    shipped = set(data_contract_check.KIND_KEYS)
    assert kinds <= shipped, kinds - shipped
    guide = SCILINTR_GUIDE.read_text(encoding="utf-8")
    rules = cited("scilintr")
    assert len(rules) >= 5, rules
    for rule in rules:
        assert f"`{rule}`" in guide, rule
    reviews = set(re.findall(r"review: `([^`]+)`", text))
    assert reviews, "expected at least one review checklist citation"
    for review in reviews:
        assert (REVIEW_DIR / f"{review}.md").is_file(), review
    # No shipped check covers a whole mode, so every citation is qualified.
    # An unqualified one is how two overclaims slipped in during review.
    rows = [line for line in text.splitlines() if line.startswith("| ")]
    checks = [row.rstrip(" |").rsplit(" | ", 1)[1] for row in rows]
    for check in checks:
        if check in {"Check", "---"}:
            continue
        for part in check.split("; "):
            assert part.startswith(("partial: ", "none", "cross-ref: ", "review: ")), check
    # scilintr lints Python and R, not shell runners: a row that names shell
    # suppression can credit it only in part.
    swallowed = next(line for line in text.splitlines() if line.startswith("| Swallowed errors"))
    assert "partial: scilintr" in swallowed and "run.sh" in swallowed
    # Code-level failures are the review checklist's job; point there.
    assert "skills/core/references/review/llm-failure-modes.md" in text


def test_port_drops_mycelium_extra_only_machinery_and_credits_the_author() -> None:
    for path in (PLANNING, DECISIONS, FAILURE_MODES):
        text = _flat(path)
        for absent in (
            ".mycelium-extra",
            "approval gate",
            "approve plan",
            "lucid-coder",
            "plot-first",
            "track-changes",
            "biology-skills",
            "`verify`",
        ):
            assert absent not in text, (path.name, absent)
        assert "Mikhael Manurung" in text
        assert "mycelium-extra" in text
