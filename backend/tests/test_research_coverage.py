import uuid

from app.db.models import FactLedger, ResearchQuestion
from app.services.research_coverage import ResearchCoverageService


PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


def question(text: str, priority: int) -> ResearchQuestion:
    return ResearchQuestion(
        id=uuid.uuid4(),
        plan_id=uuid.uuid4(),
        question=text,
        priority=priority,
        expected_source_types=["scientific"],
        coverage_status="uncovered",
    )


def fact(
    research_question: ResearchQuestion,
    source_id: uuid.UUID,
    *,
    project_id: uuid.UUID = PROJECT_ID,
    pipeline_run_id: uuid.UUID = RUN_ID,
    conflict_group: str | None = None,
    superseded_by_id: uuid.UUID | None = None,
) -> FactLedger:
    return FactLedger(
        id=uuid.uuid4(),
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        research_question_id=research_question.id,
        source_id=source_id,
        source_snapshot_id=None,
        claim_text=f"Evidence for {research_question.question}",
        exact_quote="Exact supporting quote",
        source_locator="section 1",
        extraction_method="test",
        confidence_score=0.9,
        approved=False,
        approved_by_run_id=None,
        conflict_group=conflict_group,
        superseded_by_id=superseded_by_id,
    )


def evaluate(questions, facts, selected, minimum=2, conflicts=()):
    return ResearchCoverageService.evaluate_rows(
        questions,
        facts,
        selected,
        project_id=PROJECT_ID,
        pipeline_run_id=RUN_ID,
        minimum_distinct_sources=minimum,
        reported_conflicts=conflicts,
    )


def test_facts_concentrated_in_one_question_never_cover_the_plan():
    first = question("How does the process work?", 1)
    second = question("What are the measured outcomes?", 2)
    facts = [fact(first, uuid.uuid4()), fact(first, uuid.uuid4())]

    result = evaluate([first, second], facts, [item.id for item in facts])

    assert result.distinct_source_count == 2
    assert result.missing_questions == (second.question,)
    assert result.coverage_by_question == {
        str(first.id): 1.0,
        str(second.id): 0.0,
    }
    assert result.coverage_complete is False
    assert result.permits_approval("approved") is False


def test_one_uncovered_question_blocks_even_when_other_questions_have_facts():
    questions = [
        question("What is the primary mechanism?", 1),
        question("Which evidence supports the result?", 2),
        question("What limitations must readers know?", 3),
    ]
    facts = [
        fact(questions[0], uuid.uuid4()),
        fact(questions[1], uuid.uuid4()),
    ]

    result = evaluate(questions, facts, [item.id for item in facts])

    assert result.missing_questions == (questions[2].question,)
    assert result.coverage_complete is False


def test_configured_minimum_requires_two_facts_for_each_question():
    questions = [
        question("How does the process work?", 1),
        question("Which limits apply?", 2),
    ]
    facts = [
        fact(questions[0], uuid.uuid4()),
        fact(questions[1], uuid.uuid4()),
    ]

    result = ResearchCoverageService.evaluate_rows(
        questions,
        facts,
        [item.id for item in facts],
        project_id=PROJECT_ID,
        pipeline_run_id=RUN_ID,
        minimum_distinct_sources=2,
        minimum_facts_per_question=2,
    )

    assert result.covered_question_ids == frozenset()
    assert result.coverage_by_question == {
        str(questions[0].id): 0.5,
        str(questions[1].id): 0.5,
    }
    assert result.missing_questions == tuple(q.question for q in questions)
    assert result.coverage_complete is False


def test_fact_id_from_another_run_rejects_an_otherwise_complete_selection():
    questions = [
        question("What is required before use?", 1),
        question("What is the expected result?", 2),
    ]
    current = [
        fact(questions[0], uuid.uuid4()),
        fact(questions[1], uuid.uuid4()),
    ]
    foreign = fact(
        questions[0],
        uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
    )

    result = evaluate(
        questions,
        [*current, foreign],
        [*[item.id for item in current], foreign.id],
    )

    assert result.missing_questions == ()
    assert result.invalid_fact_ids == (foreign.id,)
    assert result.coverage_complete is False


def test_minimum_sources_do_not_compensate_for_partial_coverage():
    questions = [
        question("Which inputs are necessary?", 1),
        question("Which risks are documented?", 2),
    ]
    facts = [
        fact(questions[0], uuid.uuid4()),
        fact(questions[0], uuid.uuid4()),
        fact(questions[0], uuid.uuid4()),
    ]

    result = evaluate(
        questions,
        facts,
        [item.id for item in facts],
        minimum=3,
    )

    assert result.distinct_source_count == 3
    assert result.missing_questions == (questions[1].question,)
    assert result.coverage_complete is False


def test_complete_question_and_source_coverage_permits_model_recommendation():
    questions = [
        question("What does the primary study establish?", 1),
        question("How should the finding be applied?", 2),
    ]
    facts = [
        fact(questions[0], uuid.uuid4()),
        fact(questions[1], uuid.uuid4()),
    ]

    result = evaluate(questions, facts, reversed([item.id for item in facts]))

    assert result.missing_questions == ()
    assert result.invalid_fact_ids == ()
    assert result.distinct_source_count == 2
    assert result.coverage_complete is True
    assert result.permits_approval("approved") is True
    assert result.permits_approval("insufficient") is False


def test_only_conflicts_proven_by_two_active_facts_remain_blocking():
    questions = [
        question("What value was measured?", 1),
        question("Which method was used?", 2),
    ]
    facts = [
        fact(questions[0], uuid.uuid4(), conflict_group="measurement"),
        fact(questions[0], uuid.uuid4(), conflict_group="measurement"),
        fact(questions[1], uuid.uuid4()),
    ]

    persisted = evaluate(questions, facts, [item.id for item in facts])
    reported = evaluate(
        questions,
        facts[1:],
        [item.id for item in facts[1:]],
        conflicts=["methodology"],
    )

    assert persisted.unresolved_conflicts == ("measurement",)
    assert persisted.unresolved_conflict_fact_ids == {
        "measurement": tuple(sorted((str(facts[0].id), str(facts[1].id))))
    }
    assert persisted.coverage_complete is False
    assert reported.unresolved_conflicts == ()
    assert reported.coverage_complete is True


def test_unselected_conflicting_candidate_does_not_block_selected_fact():
    first = question("Which measurement is authoritative?", 1)
    second = question("Which method supports it?", 2)
    selected = fact(first, uuid.uuid4(), conflict_group="measurement")
    unselected = fact(first, uuid.uuid4(), conflict_group="measurement")
    method = fact(second, uuid.uuid4())

    result = evaluate(
        [first, second],
        [selected, unselected, method],
        [selected.id, method.id],
    )

    assert result.unresolved_conflicts == ()
    assert result.coverage_complete is True


def test_validated_candidate_conflict_remains_blocking_until_resolved():
    first = question("Which measurement is authoritative?", 1)
    second = question("Which method supports it?", 2)
    selected = fact(first, uuid.uuid4(), conflict_group="measurement")
    unselected = fact(first, uuid.uuid4(), conflict_group="measurement")
    method = fact(second, uuid.uuid4())

    result = evaluate(
        [first, second],
        [selected, unselected, method],
        [selected.id, method.id],
        conflicts=["measurement"],
    )

    assert result.unresolved_conflicts == ("measurement",)
    assert result.unresolved_conflict_fact_ids == {
        "measurement": tuple(sorted((str(selected.id), str(unselected.id))))
    }
    assert result.coverage_complete is False


def test_one_active_and_one_superseded_fact_do_not_block_coverage():
    current_question = question("Which value is current?", 1)
    active = fact(
        current_question,
        uuid.uuid4(),
        conflict_group="measurement",
    )
    superseded = fact(
        current_question,
        uuid.uuid4(),
        conflict_group="measurement",
        superseded_by_id=active.id,
    )

    result = evaluate(
        [current_question],
        [active, superseded],
        [active.id],
        minimum=1,
    )

    assert result.unresolved_conflicts == ()
    assert result.unresolved_conflict_fact_ids == {}
    assert result.coverage_complete is True


def test_single_grouped_fact_does_not_block_coverage():
    current_question = question("Which value is current?", 1)
    active = fact(
        current_question,
        uuid.uuid4(),
        conflict_group="measurement",
    )

    result = evaluate(
        [current_question],
        [active],
        [active.id],
        minimum=1,
    )

    assert result.unresolved_conflicts == ()
    assert result.coverage_complete is True


def test_two_independent_active_conflict_groups_are_reported_deterministically():
    current_question = question("Which facts conflict?", 1)
    facts = [
        fact(current_question, uuid.uuid4(), conflict_group=group)
        for group in ("zeta", "alpha", "zeta", "alpha")
    ]

    result = evaluate(
        [current_question],
        facts,
        [item.id for item in facts],
        minimum=1,
    )

    assert result.unresolved_conflicts == ("alpha", "zeta")
    assert result.unresolved_conflict_fact_ids == {
        "alpha": tuple(sorted(str(item.id) for item in (facts[1], facts[3]))),
        "zeta": tuple(sorted(str(item.id) for item in (facts[0], facts[2]))),
    }


def test_grouped_fact_from_another_run_or_project_does_not_interfere():
    current_question = question("Which value is scoped?", 1)
    current = fact(
        current_question,
        uuid.uuid4(),
        conflict_group="measurement",
    )
    foreign_run = fact(
        current_question,
        uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
        conflict_group="measurement",
    )
    foreign_project = fact(
        current_question,
        uuid.uuid4(),
        project_id=uuid.uuid4(),
        conflict_group="measurement",
    )

    result = evaluate(
        [current_question],
        [current, foreign_run, foreign_project],
        [current.id],
        minimum=1,
    )

    assert result.unresolved_conflicts == ()
    assert result.coverage_complete is True


def test_superseded_fact_cannot_cover_a_priority_question():
    first = question("Which current value is authoritative?", 1)
    second = question("Which method produced the value?", 2)
    replacement_id = uuid.uuid4()
    superseded = fact(
        first,
        uuid.uuid4(),
        superseded_by_id=replacement_id,
    )
    current = fact(second, uuid.uuid4())

    result = evaluate(
        [first, second],
        [superseded, current],
        [superseded.id, current.id],
    )

    assert result.invalid_fact_ids == (superseded.id,)
    assert result.missing_questions == (first.question,)
    assert result.coverage_complete is False


def test_individual_status_and_global_fact_approval_match_the_evidence():
    first = question("Which fact covers the first question?", 1)
    second = question("Which fact covers the second question?", 2)
    first_fact = fact(first, uuid.uuid4())
    unselected_fact = fact(first, uuid.uuid4())
    second_fact = fact(second, uuid.uuid4())
    reviewer_run_id = uuid.uuid4()

    partial = evaluate(
        [first, second],
        [first_fact, unselected_fact, second_fact],
        [first_fact.id],
    )
    partial.persist(approved=False, reviewer_run_id=reviewer_run_id)

    assert first.coverage_status == "covered"
    assert second.coverage_status == "uncovered"
    assert not any(item.approved for item in partial.facts)

    complete = evaluate(
        [first, second],
        [first_fact, unselected_fact, second_fact],
        [second_fact.id, first_fact.id],
    )
    complete.persist(approved=True, reviewer_run_id=reviewer_run_id)

    assert first.coverage_status == second.coverage_status == "covered"
    assert first_fact.approved is True
    assert first_fact.approved_by_run_id == reviewer_run_id
    assert second_fact.approved is True
    assert unselected_fact.approved is False
    assert unselected_fact.approved_by_run_id is None


def test_selected_source_counts_are_recorded_per_question():
    first = question("Which sources support the first question?", 1)
    second = question("Which sources support the second question?", 2)
    shared_source = uuid.uuid4()
    facts = [
        fact(first, shared_source),
        fact(first, shared_source),
        fact(second, uuid.uuid4()),
    ]

    result = evaluate([first, second], facts, [item.id for item in facts])

    assert result.selected_source_count_by_question == {
        str(first.id): 1,
        str(second.id): 1,
    }


def test_production_regression_six_questions_ignore_unselected_scope_drift():
    questions = [
        question(f"Cannabis germination question {index}?", index)
        for index in range(1, 7)
    ]
    selected = [
        fact(
            current_question,
            uuid.uuid4(),
            conflict_group="germination_depth" if index == 0 else None,
        )
        for index, current_question in enumerate(questions)
    ]
    soybean_candidate = fact(
        questions[0], uuid.uuid4(), conflict_group="germination_depth"
    )
    corn_candidate = fact(
        questions[0], uuid.uuid4(), conflict_group="germination_depth"
    )
    soybean_candidate.claim_text = "Soybean planting depth outside the topic"
    corn_candidate.claim_text = "Corn planting depth outside the topic"

    result = evaluate(
        questions,
        [*selected, soybean_candidate, corn_candidate],
        [item.id for item in selected],
        minimum=5,
    )

    assert result.covered_question_ids == frozenset(q.id for q in questions)
    assert result.distinct_source_count == 6
    assert result.unresolved_conflicts == ()
    assert result.coverage_complete is True
    assert result.permits_approval("approved") is True
