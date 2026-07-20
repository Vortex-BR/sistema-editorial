import uuid
import pytest
from app.orchestration.graph import EvidenceFirstGraph, PipelineNodes
from app.orchestration.state import PipelineState, Stage


async def noop(state):
    return state


def graph():
    return EvidenceFirstGraph(
        PipelineNodes(noop, noop, noop, noop, noop, noop, noop, noop)
    )


def test_no_edge_to_writer_without_gate_approval():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.research_gatekeeper,
        facts=[{"id": str(uuid.uuid4())}],
        research_audit={"decision": "insufficient"},
    )
    transitioned = graph()._transition(state)
    assert transitioned.stage == Stage.researcher


def test_gate_approval_requires_persisted_ids():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.research_gatekeeper,
        facts=[],
        research_audit={"decision": "approved", "approved_fact_ids": []},
    )
    transitioned = graph()._transition(state)
    assert transitioned.stage == Stage.blocked
    assert transitioned.blocking_code == "PIPELINE_QUALITY_BLOCKED"


def test_research_exhaustion_without_any_evidence_has_specific_outcome_code():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.research_gatekeeper,
        research_cycle=3,
        research_audit={"decision": "insufficient"},
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.blocked
    assert transitioned.blocking_code == "NO_USABLE_RESEARCH_RESULTS"


def test_gate_approval_requires_complete_deterministic_coverage():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.research_gatekeeper,
        facts=[{"id": str(uuid.uuid4())}],
        research_audit={
            "decision": "approved",
            "approved_fact_ids": [uuid.uuid4()],
        },
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.blocked
    assert "validated evidence" in transitioned.blocking_reason


def test_partial_but_validated_evidence_can_continue_to_writer():
    fact_id = uuid.uuid4()
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.research_gatekeeper,
        facts=[{"id": str(fact_id)}],
        research_audit={
            "decision": "approved",
            "approved_fact_ids": [fact_id],
            "coverage_complete": False,
            "evidence_ready": True,
            "partial_coverage": True,
        },
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.writer


def test_complete_deterministic_gate_is_the_only_edge_to_writer():
    fact_id = uuid.uuid4()
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.research_gatekeeper,
        facts=[{"id": str(fact_id)}],
        research_audit={
            "decision": "approved",
            "approved_fact_ids": [fact_id],
            "coverage_complete": True,
        },
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.writer


def test_finalizer_blocks_nonzero_unsupported_count():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.finalizer,
        final_package={"unsupported_claim_count": 1},
    )
    transitioned = graph()._transition(state)
    assert transitioned.stage == Stage.blocked


@pytest.mark.asyncio
async def test_needs_review_is_a_terminal_stage():
    state = PipelineState(project_id=uuid.uuid4(), stage=Stage.needs_review)

    result = await graph().run(state)

    assert result.stage == Stage.needs_review


def test_finalizer_moves_to_quality_gate_before_skill_learning():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.finalizer,
        final_package={"unsupported_claim_count": 0},
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.quality_gate


def test_quality_gate_blocks_learning_when_article_fails():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.quality_gate,
        quality_evaluation={"status": "blocked"},
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.blocked
    assert transitioned.blocking_code == "ARTICLE_QUALITY_BLOCKED"


def test_quality_gate_allows_curator_only_after_pass():
    state = PipelineState(
        project_id=uuid.uuid4(),
        stage=Stage.quality_gate,
        quality_evaluation={"status": "passed"},
    )

    transitioned = graph()._transition(state)

    assert transitioned.stage == Stage.skill_curator
