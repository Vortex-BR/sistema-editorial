from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.orchestration.v3.executor import EditorialV3Executor, V3PipelineBlocked
from app.orchestration.v3.graph import EditorialIntelligenceV3Graph, V3PipelineNodes
from app.orchestration.v3.state import V3PipelineState, V3Stage
from app.schemas.editorial_intelligence import (
    ClaimWriterPolicy,
    ContentIntelligenceState,
    EvidenceConflictNode,
    IntelligenceLifecycle,
)
from app.schemas.editorial_v3 import ConclusionStatus, EvidenceRole, SectionDossier
from app.schemas.editorial_v3_runtime import (
    ClaimCheck,
    ResearchTask,
    V3DraftBlock,
    V3DraftSentence,
    V3EvidenceReference,
    V3FactCheckReview,
    V3ResearchPlan,
)
from app.services.editorial_v3.content_intelligence import ContentIntelligenceEngine
from app.services.editorial_v3.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetPlanner,
)
from app.services.editorial_v3.text_integrity import is_potentially_factual
from tests.test_editorial_intelligence_core import (
    _brief,
    _claim,
    _contract,
    _draft,
    _enriched_state,
    _source,
)


def test_unrelated_same_section_claim_does_not_cover_critical_question():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    source_id = uuid4()
    claim_id = uuid4()
    unrelated = _claim(claim_id, "foundation")
    unrelated["claim_text"] = "As folhas apresentam pigmentação verde por causa da clorofila."
    enriched = engine.attach_evidence(
        state,
        claims=[unrelated],
        source_documents=[_source(source_id)],
        section_dossiers=[
            SectionDossier.model_construct(
                section_id="foundation", allowed_claim_ids=[claim_id], conflicts=[]
            )
        ],
        gaps=[],
        claim_provenance={str(claim_id): {"source_document_ids": [str(source_id)]}},
    )

    report = engine.validate_writer_readiness(enriched)

    assert report.status == "blocked"
    assert any(
        item.code == "INTELLIGENCE_CRITICAL_QUESTION_UNSUPPORTED"
        and item.section_id == "foundation"
        for item in report.blockers
    )


def test_draft_must_explicitly_answer_critical_questions():
    engine, state, foundation_claim, analysis_claim = _enriched_state()
    draft = _draft(foundation_claim, analysis_claim)
    blocks = []
    for block in draft.blocks:
        blocks.append(
            block.model_copy(
                update={
                    "sentences": [
                        sentence.model_copy(
                            update={"question_ids": [], "answer_status": None}
                        )
                        for sentence in block.sentences
                    ]
                }
            )
        )
    draft = draft.model_copy(update={"blocks": blocks})

    report = engine.validate_draft(state, draft)

    assert report.status == "blocked"
    assert any(
        item.code == "INTELLIGENCE_CRITICAL_QUESTION_NOT_ANSWERED"
        for item in report.blockers
    )


def test_distinct_source_claim_ids_are_canonicalized_by_support_group():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    source_a, source_b = uuid4(), uuid4()
    claim_a, claim_b = uuid4(), uuid4()
    support_group = "foundation-definition-equivalent"
    raw_a = _claim(claim_a, "foundation")
    raw_b = _claim(claim_b, "foundation")
    for raw in (raw_a, raw_b):
        raw["support_group"] = support_group
        raw["claim_text"] = (
            "O conhecimento essencial de foundation explica o que o leitor "
            "precisa compreender sobre foundation."
        )
    enriched = engine.attach_evidence(
        state,
        claims=[raw_a, raw_b],
        source_documents=[_source(source_a), _source(source_b).model_copy(
            update={"document_id": source_b, "canonical_url": "https://example.net/fonte-b"}
        )],
        section_dossiers=[
            SectionDossier.model_construct(
                section_id="foundation",
                allowed_claim_ids=[claim_a, claim_b],
                conflicts=[],
            )
        ],
        gaps=[],
        claim_provenance={
            str(claim_a): {"source_document_ids": [str(source_a)]},
            str(claim_b): {"source_document_ids": [str(source_b)]},
        },
    )

    foundation_claims = [
        item for item in enriched.evidence_graph.claims if item.section_id == "foundation"
    ]
    assert len(foundation_claims) == 1
    assert set(foundation_claims[0].source_claim_ids) == {claim_a, claim_b}
    assert set(foundation_claims[0].source_ids) == {source_a, source_b}


def test_disputed_claim_is_preserved_for_context_but_not_direct_writing():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    source_id, claim_id = uuid4(), uuid4()
    claim = _claim(claim_id, "foundation", status=ConclusionStatus.disputed)
    claim["support_group"] = "disputed-foundation"
    claim["conflict_group"] = "foundation-conflict"
    claim["approved_for_direct_writing"] = False
    enriched = engine.attach_evidence(
        state,
        claims=[claim],
        source_documents=[_source(source_id)],
        section_dossiers=[
            SectionDossier.model_construct(
                section_id="foundation", allowed_claim_ids=[claim_id], conflicts=[]
            )
        ],
        gaps=[],
        claim_provenance={str(claim_id): {"source_document_ids": [str(source_id)]}},
    )

    assert len(enriched.evidence_graph.claims) == 1
    node = enriched.evidence_graph.claims[0]
    assert node.conclusion_status == ConclusionStatus.disputed
    assert node.writer_policy == ClaimWriterPolicy.context_only
    assert enriched.evidence_graph.conflicts


def test_research_plan_reserves_question_queries_when_legacy_slots_are_full():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    task = ResearchTask(
        task_id="foundation_definition",
        knowledge_node_id="foundation",
        evidence_role=EvidenceRole.definition,
        research_goal="Investigar o fundamento editorial com fontes adequadas e verificáveis.",
        queries=[f"consulta original {index}" for index in range(6)],
        required_source_roles=["institutional"],
        minimum_independent_sources=1,
        critical=True,
        rationale="A tarefa precisa sustentar a seção de fundamento do artigo.",
    )
    plan = V3ResearchPlan(
        rationale="Plano suficientemente detalhado para o teste de reserva de consultas.",
        tasks=[task, task.model_copy(update={"task_id": "analysis_definition", "knowledge_node_id": "analysis"}), task.model_copy(update={"task_id": "closing_definition", "knowledge_node_id": "closing"})],
        method_discovery_queries=["descoberta um", "descoberta dois"],
        terminology_queries=[],
        stop_conditions=["cobertura suficiente", "orçamento atingido"],
        maximum_search_queries=18,
    )

    augmented = engine.augment_research_plan(state, plan)
    foundation = augmented.tasks[0]

    assert len(foundation.queries) == 6
    assert any("foundation" in query and "tema editorial" in query for query in foundation.queries)
    assert foundation.queries != task.queries


def test_section_specific_prohibition_is_enforced():
    engine, state, foundation_claim, analysis_claim = _enriched_state()
    sections = [
        item.model_copy(
            update={
                "prohibited_conclusions": [
                    *item.prohibited_conclusions,
                    "superioridade absoluta garantida",
                ]
            }
        )
        if item.section_id == "analysis"
        else item
        for item in state.sections
    ]
    state = state.model_copy(update={"sections": sections, "checksum": ""})
    draft = _draft(foundation_claim, analysis_claim)
    blocks = list(draft.blocks)
    analysis_block = blocks[3]
    blocks[3] = analysis_block.model_copy(
        update={
            "sentences": [
                analysis_block.sentences[0].model_copy(
                    update={
                        "text": analysis_block.sentences[0].text
                        + " Isso representa superioridade absoluta garantida."
                    }
                )
            ]
        }
    )

    report = engine.validate_draft(state, draft.model_copy(update={"blocks": blocks}))

    assert any(
        item.code == "INTELLIGENCE_SECTION_PROHIBITED_CONCLUSION_PRESENT"
        for item in report.blockers
    )


def test_cross_section_question_reference_is_rejected_by_schema():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    sections = list(state.sections)
    sections[1] = sections[1].model_copy(
        update={"question_ids": [state.sections[0].question_ids[0]]}
    )

    with pytest.raises(ValidationError):
        ContentIntelligenceState.model_validate(
            {**state.model_dump(mode="json"), "sections": [item.model_dump(mode="json") for item in sections]}
        )


@pytest.mark.parametrize(
    "sentence",
    [
        "O solo argiloso retém umidade.",
        "A clorofila absorve luz azul e vermelha.",
        "As raízes transportam água para os tecidos.",
        "A condensação forma gotículas na tampa.",
    ],
)
def test_domain_declarative_sentences_are_detected_as_factual(sentence: str):
    assert is_potentially_factual(sentence, block_type="paragraph") is True


def test_context_budget_preserves_question_evidence_and_compacts_duplicates():
    planner = ContextBudgetPlanner()
    payload = {
        "claim_catalog": [
            {"claim_id": "keep", "claim_text": "x" * 1000},
            {"claim_id": "drop", "claim_text": "y" * 1000},
        ],
        "editorial_intelligence": {
            "question_evidence_plan": [
                {"question_id": "q_test", "evidence": [{"claim_id": "keep"}]}
            ],
            "claim_policy_catalog": [
                {"claim_id": "keep", "policy": "direct"},
                {"claim_id": "drop", "policy": "direct"},
            ],
        },
        "external_references": {
            "a": {"url": "https://example.org", "title": "Fonte", "raw": "z" * 5000}
        },
    }

    compacted, report = planner.compact_writer_input(payload, maximum_characters=2500)

    assert report.compacted is True
    assert [item["claim_id"] for item in compacted["claim_catalog"]] == ["keep"]
    assert "raw" not in compacted["external_references"]["a"]


def _noop_nodes():
    async def noop(state):
        return state

    return V3PipelineNodes(
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
        targeted_source_recovery=noop,
        intelligence_gate=noop,
    )


def test_graph_routes_recoverable_intelligence_blockers_to_research():
    graph = EditorialIntelligenceV3Graph(_noop_nodes())
    state = V3PipelineState(
        project_id=uuid4(),
        stage=V3Stage.intelligence_gate,
        intelligence_validation={"status": "blocked"},
        intelligence_recovery_tasks=[
            {"task_id": "foundation_definition", "query": "pergunta crítica"}
        ],
        intelligence_recovery_exhausted=False,
    )

    transitioned = graph._transition(state)

    assert transitioned.stage == V3Stage.targeted_source_recovery


def test_validated_state_is_bound_to_exact_draft_hash():
    engine, state, foundation_claim, analysis_claim = _enriched_state(
        conditional_analysis=True
    )
    draft = _draft(foundation_claim, analysis_claim)
    pending = engine.mark_draft_pending(state)
    report = engine.validate_draft(pending, draft)
    validated = engine.mark_draft_validated(pending, report, draft=draft)

    assert validated.lifecycle == IntelligenceLifecycle.draft_validated
    assert validated.validated_artifact_hash == report.metrics["draft_artifact_hash"]
    assert validated.draft_revision >= 1
    summary = engine.summary(validated)
    assert summary["validated_artifact_hash"] == validated.validated_artifact_hash
    assert summary["draft_revision"] == validated.draft_revision


def test_fact_check_is_keyed_by_sentence_id_even_when_text_is_duplicated():
    engine, intel_state, foundation_claim, analysis_claim = _enriched_state()
    draft = _draft(foundation_claim, analysis_claim)
    # Duplicate the foundation factual sentence as a distinct logical sentence.
    original = draft.blocks[1].sentences[0]
    duplicate_block = V3DraftBlock(
        block_id=uuid4(),
        type="paragraph",
        position=2,
        section_id="foundation",
        sentences=[original.model_copy(update={"sentence_id": uuid4()})],
    )
    blocks = [draft.blocks[0], draft.blocks[1], duplicate_block]
    for position, block in enumerate(draft.blocks[2:], start=3):
        blocks.append(block.model_copy(update={"position": position}))
    draft = draft.model_copy(update={"blocks": blocks})
    state = V3PipelineState(
        project_id=uuid4(),
        contract=_contract().model_dump(mode="json"),
        knowledge_claims=[
            _claim(foundation_claim, "foundation"),
            _claim(analysis_claim, "analysis", status=ConclusionStatus.well_supported),
        ],
        intelligence_state=intel_state.model_dump(mode="json"),
        method_dossiers=[],
    )
    checks = []
    for block in draft.blocks:
        for sentence in block.content_sentences:
            if sentence.is_factual:
                checks.append(
                    ClaimCheck(
                        block_id=block.block_id,
                        sentence_id=sentence.sentence_id,
                        sentence_text=sentence.text,
                        claim_ids=[item.claim_id for item in sentence.evidence],
                        status="supported",
                    )
                )
    review = V3FactCheckReview(status="passed", checks=checks, findings=[], rewrite_block_ids=[])
    executor = EditorialV3Executor.__new__(EditorialV3Executor)

    executor._validate_fact_check_review(
        draft=draft,
        review=review,
        state=state,
        require_passed=True,
    )


def test_frankenstein_sentence_cannot_be_approved_by_concatenating_claims():
    engine, intel_state, foundation_claim, analysis_claim = _enriched_state()
    draft = _draft(foundation_claim, analysis_claim)
    blocks = list(draft.blocks)
    blocks[1] = blocks[1].model_copy(
        update={
            "sentences": [
                V3DraftSentence(
                    text=(
                        "O conhecimento essencial de foundation explica o fundamento, "
                        "e o conhecimento de analysis explica a análise."
                    ),
                    is_factual=True,
                    evidence=[
                        V3EvidenceReference(claim_id=foundation_claim, entailment_score=0),
                        V3EvidenceReference(claim_id=analysis_claim, entailment_score=0),
                    ],
                    question_ids=["q_foundation_central_1"],
                    answer_status="direct",
                )
            ]
        }
    )
    state = V3PipelineState(
        project_id=uuid4(),
        contract=_contract().model_dump(mode="json"),
        knowledge_claims=[
            {**_claim(foundation_claim, "foundation"), "support_group": "foundation"},
            {**_claim(analysis_claim, "analysis"), "support_group": "analysis"},
        ],
        intelligence_state=intel_state.model_dump(mode="json"),
        method_dossiers=[],
    )
    executor = EditorialV3Executor.__new__(EditorialV3Executor)

    with pytest.raises(V3PipelineBlocked, match="atomic"):
        executor._validate_draft_evidence(draft.model_copy(update={"blocks": blocks}), state)


def test_graph_returns_successful_intelligence_recovery_to_source_reader():
    graph = EditorialIntelligenceV3Graph(_noop_nodes())
    state = V3PipelineState(
        project_id=uuid4(),
        stage=V3Stage.targeted_source_recovery,
        raw_source_documents=[{"url": "https://example.org/new-source"}],
        intelligence_recovery_tasks=[
            {"task_id": "foundation_definition", "query": "pergunta crítica"}
        ],
        intelligence_recovery_exhausted=False,
        research_metrics={
            "last_recovery_mode": "intelligence",
            "intelligence_recovery_new_candidate_count": 1,
        },
    )

    transitioned = graph._transition(state)

    assert transitioned.stage == V3Stage.source_reader
    assert transitioned.intelligence_recovery_tasks == []
    assert transitioned.research_metrics["last_recovery_mode"] is None


def test_graph_retries_intelligence_recovery_without_new_candidates_until_exhausted():
    graph = EditorialIntelligenceV3Graph(_noop_nodes())
    state = V3PipelineState(
        project_id=uuid4(),
        stage=V3Stage.targeted_source_recovery,
        intelligence_recovery_tasks=[
            {"task_id": "foundation_definition", "query": "pergunta crítica"}
        ],
        intelligence_recovery_exhausted=False,
        research_metrics={
            "last_recovery_mode": "intelligence",
            "intelligence_recovery_new_candidate_count": 0,
        },
    )

    transitioned = graph._transition(state)

    assert transitioned.stage == V3Stage.targeted_source_recovery


def test_context_budget_never_truncates_an_irreducible_draft():
    planner = ContextBudgetPlanner()
    payload = {"draft": {"blocks": [{"text": "conteudo factual " * 500}]}}

    with pytest.raises(ContextBudgetExceeded) as error:
        planner.compact_review_input(payload, maximum_characters=1000)

    assert error.value.report.original_characters > 1000
    assert error.value.report.final_characters > 1000


def test_conflict_claim_requires_explicit_uncertainty_language():
    engine, state, foundation_claim, analysis_claim = _enriched_state()
    conflict = EvidenceConflictNode(
        conflict_id="conflict_analysis_interpretation",
        section_id="analysis",
        claim_ids=[analysis_claim],
        status="represented",
        required_language="Apresente a divergência e os limites.",
        prohibited_conclusions=["há consenso absoluto"],
    )
    graph = state.evidence_graph.model_copy(
        update={"conflicts": [*state.evidence_graph.conflicts, conflict]}
    )
    sections = [
        item.model_copy(update={"conflict_ids": [conflict.conflict_id]})
        if item.section_id == "analysis"
        else item
        for item in state.sections
    ]
    state = ContentIntelligenceState.model_validate(
        {
            **state.model_dump(mode="json"),
            "evidence_graph": graph.model_dump(mode="json"),
            "sections": [item.model_dump(mode="json") for item in sections],
        }
    )

    report = engine.validate_draft(state, _draft(foundation_claim, analysis_claim))

    assert any(
        item.code == "INTELLIGENCE_CONFLICT_LANGUAGE_MISSING"
        for item in report.blockers
    )


def test_persisted_canonical_claim_id_is_preserved_with_accented_support_group():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    source_id = uuid4()
    canonical_id = uuid4()
    source_claim_id = uuid4()
    claim = _claim(canonical_id, "foundation")
    claim.update(
        {
            "support_group": "Definição — Fundação",
            "source_claim_ids": [str(source_claim_id)],
        }
    )
    enriched = engine.attach_evidence(
        state,
        claims=[claim],
        source_documents=[_source(source_id)],
        section_dossiers=[
            SectionDossier.model_construct(
                section_id="foundation",
                allowed_claim_ids=[canonical_id],
                conflicts=[],
            )
        ],
        gaps=[],
        claim_provenance={
            str(canonical_id): {"source_document_ids": [str(source_id)]}
        },
    )

    assert enriched.evidence_graph.claims[0].claim_id == canonical_id
    assert enriched.sections[0].allowed_claim_ids == [canonical_id]


@pytest.mark.parametrize(
    "sentence",
    [
        "Saiba mais e continue para a próxima seção.",
        "Agora seguimos com clareza e sem repetir a introdução.",
    ],
)
def test_editorial_transitions_with_conjunction_are_not_misclassified_as_factual(
    sentence: str,
):
    assert is_potentially_factual(sentence, block_type="paragraph") is False


def test_explicit_comparison_remains_factual_without_numeric_markers():
    assert (
        is_potentially_factual(
            "Este método é mais eficiente do que o anterior.",
            block_type="paragraph",
        )
        is True
    )


def test_node_resolution_research_override_is_consistent_for_section_and_questions():
    engine = ContentIntelligenceEngine()
    contract = _contract()
    metadata = dict(contract.metadata)
    metadata["node_resolution"] = {
        **dict(metadata.get("node_resolution") or {}),
        "foundation": {"research_required": False},
    }
    contract = contract.model_copy(update={"metadata": metadata})

    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=contract,
        generation_brief=_brief(),
    )

    section = next(item for item in state.sections if item.section_id == "foundation")
    questions = [
        item for item in state.questions if item.section_id == "foundation"
    ]
    assert section.research_required is False
    assert questions
    assert all(item.research_required is False for item in questions)
