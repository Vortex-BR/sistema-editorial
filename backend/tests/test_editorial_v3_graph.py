import uuid

from app.orchestration.v3.graph import EditorialIntelligenceV3Graph, V3PipelineNodes
from app.orchestration.v3.state import V3PipelineState, V3Stage


async def noop(state):
    return state


def graph():
    return EditorialIntelligenceV3Graph(
        V3PipelineNodes(
            content_contract=noop,
            knowledge_architect=noop,
            knowledge_gate=noop,
            research_planner=noop,
            source_discovery=noop,
            source_reader=noop,
            knowledge_synthesizer=noop,
            knowledge_completeness_gate=noop,
            writer=noop,
            development_editor=noop,
            fact_checker=noop,
            language_editor=noop,
            external_reference_gate=noop,
            finalizer=noop,
            quality_gate=noop,
        )
    )


def test_v3_has_no_research_edge_before_knowledge_gate_passes():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.knowledge_gate,
        contract={"contract_version": "editorial-v3"},
        contract_validation={"status": "blocked"},
    )

    result = graph()._transition(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_KNOWLEDGE_CONTRACT_BLOCKED"


def test_v3_has_no_writer_edge_before_completeness_gate_passes():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.knowledge_completeness_gate,
        completeness_report={"status": "blocked"},
    )

    result = graph()._transition(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_KNOWLEDGE_INCOMPLETE"


def test_v3_completeness_gate_is_the_only_edge_to_writer():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.knowledge_completeness_gate,
        completeness_report={"status": "passed"},
    )

    result = graph()._transition(state)

    assert result.stage == V3Stage.writer


def test_v3_quality_gate_finishes_only_after_all_review_stages():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.quality_gate,
        quality_evaluation={"status": "passed"},
    )

    result = graph()._transition(state)

    assert result.stage == V3Stage.completed


def test_v3_source_discovery_transitions_on_raw_documents_before_structuring():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.source_discovery,
        raw_source_documents=[{"url": "https://example.org/source"}],
        source_documents=[],
    )

    result = graph()._transition(state)

    assert result.stage == V3Stage.source_reader


def test_v3_empty_source_result_has_an_actionable_outcome_code():
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.source_discovery,
        raw_source_documents=[],
    )

    result = graph()._transition(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_NO_SOURCE_RESULTS"
    assert "Nenhuma fonte utilizável" in result.blocking_reason


def test_v3_intelligence_planner_is_inserted_after_knowledge_gate_when_configured():
    configured = graph()
    configured.nodes.intelligence_planner = noop
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.knowledge_gate,
        contract_validation={"status": "passed"},
    )

    result = configured._transition(state)

    assert result.stage == V3Stage.intelligence_planner


def test_v3_intelligence_planning_blocks_without_canonical_state():
    configured = graph()
    configured.nodes.intelligence_planner = noop
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.intelligence_planner,
        intelligence_state=None,
        intelligence_validation={"status": "passed"},
    )

    result = configured._transition(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_INTELLIGENCE_PLANNING_MISSING"


def test_v3_evidence_graph_and_intelligence_gate_precede_completeness_gate():
    configured = graph()
    configured.nodes.evidence_graph_builder = noop
    configured.nodes.intelligence_gate = noop
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.knowledge_synthesizer,
        section_dossiers=[{"section_id": "foundation"}],
    )

    result = configured._transition(state)
    assert result.stage == V3Stage.evidence_graph_builder

    result.intelligence_state = {"intelligence_version": "editorial-intelligence-v1"}
    result = configured._transition(result)
    assert result.stage == V3Stage.intelligence_gate

    result.intelligence_validation = {"status": "passed"}
    result = configured._transition(result)
    assert result.stage == V3Stage.knowledge_completeness_gate


def test_v3_intelligence_gate_blocks_writer_when_evidence_state_is_incomplete():
    configured = graph()
    configured.nodes.intelligence_gate = noop
    state = V3PipelineState(
        project_id=uuid.uuid4(),
        stage=V3Stage.intelligence_gate,
        intelligence_validation={"status": "blocked"},
    )

    result = configured._transition(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_INTELLIGENCE_GATE_BLOCKED"
