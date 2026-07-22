from types import SimpleNamespace
from uuid import UUID, uuid4

from app.schemas.editorial_hierarchy import (
    NodeApplicability,
    NodeImportance,
    UniversalNodeRole,
)
from app.schemas.editorial_v3 import (
    ConclusionStatus,
    ContentKnowledgeContract,
    EditorialContentTypeV3,
    EvidenceRole,
    KnowledgeEdgeContract,
    KnowledgeEdgeRelation,
    KnowledgeNodeContract,
    KnowledgeNodeKind,
    SectionDossier,
    SourceRole,
    SourceUsagePolicy,
)
from app.schemas.editorial_v3_runtime import (
    StructuredSourceDocument,
    V3DraftBlock,
    V3DraftSentence,
    V3EvidenceReference,
    V3WriterOutput,
)
from app.services.editorial_v3.content_intelligence import ContentIntelligenceEngine


def _node(
    node_id: str,
    sequence: int,
    *,
    depends_on: list[str] | None = None,
    research_required: bool = True,
    role: UniversalNodeRole = UniversalNodeRole.foundation,
) -> KnowledgeNodeContract:
    return KnowledgeNodeContract(
        node_id=node_id,
        sequence=sequence,
        kind=KnowledgeNodeKind.explanation,
        title_function=f"Explicar a função editorial de {node_id}",
        editorial_goal=f"Entregar uma explicação completa e verificável para a seção {node_id}.",
        reader_state_before=f"O leitor ainda não compreende os elementos de {node_id}.",
        reader_state_after=f"O leitor compreende os elementos necessários de {node_id}.",
        central_question=f"O que o leitor precisa compreender sobre {node_id}?",
        depends_on=depends_on or [],
        required_knowledge=[f"Conhecimento essencial e verificável de {node_id}"],
        required_decisions=[],
        required_evidence_roles=[EvidenceRole.definition],
        completion_criteria=[f"Explicar com clareza o conteúdo de {node_id}"],
        universal_role=role,
        applicability=NodeApplicability.required,
        importance=NodeImportance.core,
        research_required=research_required,
    )


def _contract() -> ContentKnowledgeContract:
    nodes = [
        _node("foundation", 1),
        _node("analysis", 2, depends_on=["foundation"], role=UniversalNodeRole.implications),
        _node(
            "closing",
            3,
            depends_on=["analysis"],
            research_required=False,
            role=UniversalNodeRole.outcome,
        ),
    ]
    return ContentKnowledgeContract(
        content_type=EditorialContentTypeV3.explanatory_guide,
        topic="tema editorial de teste",
        reader_start_state="O leitor ainda não entende o tema editorial de teste.",
        reader_final_state="O leitor entende o tema e consegue avaliar suas implicações.",
        article_promise="Explicar o tema com evidências, progressão lógica e conclusão limitada.",
        scope_limit="O texto deve permanecer no escopo explicativo definido pelo briefing.",
        nodes=nodes,
        edges=[
            KnowledgeEdgeContract(
                from_node_id="foundation",
                to_node_id="analysis",
                relation=KnowledgeEdgeRelation.sequence,
                rationale="A análise depende da compreensão dos fundamentos apresentados primeiro.",
            ),
            KnowledgeEdgeContract(
                from_node_id="analysis",
                to_node_id="closing",
                relation=KnowledgeEdgeRelation.sequence,
                rationale="O fechamento consolida somente o que foi demonstrado anteriormente.",
            ),
        ],
        prohibited_conclusions=["resultado universal garantido"],
    )


def _brief() -> dict:
    return {
        "locale": "pt-BR",
        "content_objective": "Ensinar o leitor sem ultrapassar as evidências disponíveis.",
        "primary_keyword": "tema editorial",
        "secondary_keywords": ["explicação confiável"],
        "reader": {"knowledge_level": "iniciante"},
        "brand": {"tone": "claro e técnico"},
        "commercial": {},
        "structure": {"minimum_h2": 2, "minimum_h3": 1},
        "evidence_policy": {"claims_to_avoid": ["promessa absoluta"]},
    }


def _source(document_id: UUID) -> StructuredSourceDocument:
    return StructuredSourceDocument.model_construct(
        document_id=document_id,
        canonical_url="https://example.org/fonte-tecnica",
        title="Fonte técnica de teste",
        publisher="Instituição de teste",
        content_hash="a" * 64,
        assessment=SimpleNamespace(
            source_role=SourceRole.institutional,
            usage_policy=SourceUsagePolicy.authoritative_evidence,
        ),
    )


def _claim(claim_id: UUID, section_id: str, *, status: ConclusionStatus = ConclusionStatus.confirmed) -> dict:
    return {
        "claim_id": str(claim_id),
        "claim_text": (
            f"O conhecimento essencial de {section_id} explica o que o leitor "
            f"precisa compreender sobre {section_id}."
        ),
        "knowledge_node_id": section_id,
        "evidence_role": EvidenceRole.definition.value,
        "source_fact_ids": [str(uuid4())],
        "method_ids": [],
        "conditions": ["quando as condições descritas são atendidas"] if status == ConclusionStatus.conditional else [],
        "limitations": [],
        "applicability": [],
        "conclusion_status": status.value,
        "confidence_score": 0.92,
        "conflict_group": None,
    }


def _dossier(section_id: str, claim_id: UUID) -> SectionDossier:
    return SectionDossier.model_construct(
        section_id=section_id,
        allowed_claim_ids=[claim_id],
        conflicts=[],
    )


def _enriched_state(*, conditional_analysis: bool = False):
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    source_id = uuid4()
    foundation_claim = uuid4()
    analysis_claim = uuid4()
    claims = [
        _claim(foundation_claim, "foundation"),
        _claim(
            analysis_claim,
            "analysis",
            status=(
                ConclusionStatus.conditional
                if conditional_analysis
                else ConclusionStatus.well_supported
            ),
        ),
    ]
    provenance = {
        str(foundation_claim): {"source_document_ids": [str(source_id)]},
        str(analysis_claim): {"source_document_ids": [str(source_id)]},
    }
    enriched = engine.attach_evidence(
        state,
        claims=claims,
        source_documents=[_source(source_id)],
        section_dossiers=[
            _dossier("foundation", foundation_claim),
            _dossier("analysis", analysis_claim),
        ],
        gaps=[],
        claim_provenance=provenance,
    )
    return engine, enriched, foundation_claim, analysis_claim


def _sentence(
    text: str,
    claim_id: UUID | None = None,
    *,
    question_ids: list[str] | None = None,
    answer_status: str | None = None,
) -> V3DraftSentence:
    if claim_id is None:
        return V3DraftSentence(
            text=text,
            is_factual=False,
            evidence=[],
            question_ids=question_ids or [],
            answer_status=answer_status,
        )
    return V3DraftSentence(
        text=text,
        is_factual=True,
        evidence=[V3EvidenceReference(claim_id=claim_id, entailment_score=0.95)],
        question_ids=question_ids or [],
        answer_status=answer_status,
    )


def _draft(
    foundation_claim: UUID,
    analysis_claim: UUID,
    *,
    analysis_text: str = (
        "O conhecimento essencial de analysis explica o que o leitor precisa "
        "compreender sobre analysis, quando as condições descritas são atendidas."
    ),
    analysis_claim_section: str = "analysis",
) -> V3WriterOutput:
    blocks = [
        V3DraftBlock(
            block_id=uuid4(),
            type="h1",
            position=0,
            section_id="foundation",
            sentences=[_sentence("Guia completo do tema editorial de teste")],
        ),
        V3DraftBlock(
            block_id=uuid4(),
            type="paragraph",
            position=1,
            section_id="foundation",
            sentences=[
                _sentence(
                    "O conhecimento essencial de foundation explica o que o leitor "
                    "precisa compreender sobre foundation.",
                    foundation_claim,
                    question_ids=[
                        "q_foundation_central_1",
                        "q_foundation_knowledge_1",
                    ],
                    answer_status="direct",
                )
            ],
        ),
        V3DraftBlock(
            block_id=uuid4(),
            type="h2",
            position=2,
            section_id="analysis",
            sentences=[_sentence("Análise e implicações")],
        ),
        V3DraftBlock(
            block_id=uuid4(),
            type="paragraph",
            position=3,
            section_id=analysis_claim_section,
            sentences=[
                _sentence(
                    analysis_text,
                    analysis_claim,
                    question_ids=[
                        "q_analysis_central_1",
                        "q_analysis_knowledge_1",
                    ],
                    answer_status="direct",
                )
            ],
        ),
        V3DraftBlock(
            block_id=uuid4(),
            type="h2",
            position=4,
            section_id="closing",
            sentences=[_sentence("Conclusão dentro do escopo")],
        ),
        V3DraftBlock(
            block_id=uuid4(),
            type="paragraph",
            position=5,
            section_id="closing",
            sentences=[
                _sentence(
                    "O fechamento explica o que o leitor precisa compreender sobre closing "
                    "e apresenta o conhecimento essencial de closing sem criar fatos novos.",
                    question_ids=[
                        "q_closing_central_1",
                        "q_closing_knowledge_1",
                    ],
                    answer_status="direct",
                )
            ],
        ),
    ]
    return V3WriterOutput.model_construct(
        title="Guia completo do tema editorial de teste",
        blocks=blocks,
        covered_section_ids=["foundation", "analysis", "closing"],
        covered_method_ids=[],
        unsupported_claims=[],
        scope_confirmation="O conteúdo permanece no escopo definido.",
    )


def test_intelligence_initialization_builds_canonical_question_and_section_map():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )

    assert state.lifecycle.value == "planned"
    assert state.validation is not None
    assert state.validation.status == "passed"
    assert [section.section_id for section in state.sections] == [
        "foundation",
        "analysis",
        "closing",
    ]
    assert all(section.question_ids for section in state.sections)
    assert state.reader_profile["knowledge_level"] == "iniciante"
    assert state.checksum and len(state.checksum) == 64


def test_evidence_graph_closes_provenance_and_writer_gate_passes():
    engine, state, foundation_claim, analysis_claim = _enriched_state()

    report = engine.validate_writer_readiness(state)
    ready = engine.mark_writer_ready(state, report)

    assert report.status == "passed"
    assert ready.lifecycle.value == "writer_ready"
    assert len(ready.evidence_graph.sources) == 1
    assert {item.claim_id for item in ready.evidence_graph.claims} == {
        foundation_claim,
        analysis_claim,
    }
    assert ready.evidence_graph.section_claim_map["analysis"] == [analysis_claim]
    assert all(
        ready.evidence_graph.question_claim_map[question.question_id]
        for question in ready.questions
        if question.research_required
    )


def test_writer_readiness_blocks_research_sections_without_evidence():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )

    report = engine.validate_writer_readiness(state)

    assert report.status == "blocked"
    codes = {item.code for item in report.blockers}
    assert "INTELLIGENCE_SECTION_WITHOUT_EVIDENCE" in codes
    assert "INTELLIGENCE_CRITICAL_QUESTION_UNSUPPORTED" in codes


def test_draft_validation_blocks_cross_section_claim_usage():
    engine, state, foundation_claim, analysis_claim = _enriched_state()
    report = engine.validate_draft(
        state,
        _draft(
            foundation_claim,
            analysis_claim,
            analysis_claim_section="foundation",
        ),
    )

    assert report.status == "blocked"
    assert any(
        item.code == "INTELLIGENCE_DRAFT_CLAIM_NOT_ALLOWED_IN_SECTION"
        for item in report.blockers
    )


def test_draft_validation_requires_conditions_for_conditional_claims():
    engine, state, foundation_claim, analysis_claim = _enriched_state(
        conditional_analysis=True
    )
    report = engine.validate_draft(
        state,
        _draft(
            foundation_claim,
            analysis_claim,
            analysis_text="Afirmação verificável associada à seção analysis.",
        ),
    )

    assert report.status == "blocked"
    assert any(
        item.code == "INTELLIGENCE_CONDITIONAL_CLAIM_UNQUALIFIED"
        for item in report.blockers
    )


def test_valid_draft_passes_intelligence_validation_and_can_be_versioned():
    engine, state, foundation_claim, analysis_claim = _enriched_state(
        conditional_analysis=True
    )
    draft = _draft(foundation_claim, analysis_claim)
    report = engine.validate_draft(state, draft)
    validated = engine.mark_draft_validated(state, report, draft=draft)

    assert report.status == "passed"
    assert validated.lifecycle.value == "draft_validated"
    assert validated.revision == state.revision + 1
    assert validated.checksum != state.checksum


def test_intelligence_question_map_augments_research_queries_without_expanding_tasks():
    from app.services.editorial_v3.research_planner import V3ResearchPlanningService

    contract = _contract()
    all_research_nodes = [
        node.model_copy(update={"research_required": True})
        for node in contract.nodes
    ]
    contract = contract.model_copy(update={"nodes": all_research_nodes})
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=contract,
        generation_brief=_brief(),
    )
    original = V3ResearchPlanningService().build(contract)

    augmented = engine.augment_research_plan(state, original)

    assert len(augmented.tasks) == len(original.tasks)
    assert "mapa canônico" in augmented.rationale
    for before, after in zip(original.tasks, augmented.tasks, strict=True):
        assert before.task_id == after.task_id
        assert len(after.queries) <= 6
        assert "Perguntas editoriais canônicas" in after.research_goal
        assert set(before.queries).issubset(set(after.queries))


def test_draft_validation_promotes_obvious_factual_sentence_and_requires_claim():
    engine, state, foundation_claim, analysis_claim = _enriched_state()
    draft = _draft(foundation_claim, analysis_claim)
    blocks = list(draft.blocks)
    blocks[1] = blocks[1].model_copy(
        update={
            "sentences": [
                V3DraftSentence(
                    text="O processo dura 7 dias.",
                    is_factual=False,
                    evidence=[],
                )
            ]
        }
    )
    draft = draft.model_copy(update={"blocks": blocks})

    report = engine.validate_draft(state, draft)

    assert report.status == "blocked"
    assert any(
        item.code == "INTELLIGENCE_FACTUAL_SENTENCE_WITHOUT_CLAIM"
        for item in report.blockers
    )


def test_evidence_graph_aggregates_corroborating_rows_with_same_canonical_claim_id():
    engine = ContentIntelligenceEngine()
    state = engine.initialize(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        contract=_contract(),
        generation_brief=_brief(),
    )
    source_one = uuid4()
    source_two = uuid4()
    claim_id = uuid4()
    first = _claim(claim_id, "foundation")
    second = {**first, "source_fact_ids": [str(uuid4())]}
    enriched = engine.attach_evidence(
        state,
        claims=[first, second],
        source_documents=[_source(source_one), _source(source_two)],
        section_dossiers=[_dossier("foundation", claim_id)],
        gaps=[],
        claim_provenance={
            str(claim_id): {
                "source_document_ids": [str(source_one), str(source_two)]
            }
        },
    )

    assert len(enriched.evidence_graph.claims) == 1
    claim = enriched.evidence_graph.claims[0]
    assert claim.claim_id == claim_id
    assert set(claim.source_ids) == {source_one, source_two}
    assert len(claim.source_fact_ids) == 2
    assert claim.integrity_issues == []


def test_incompatible_rows_for_same_claim_are_prohibited_and_block_writer():
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
    first = _claim(claim_id, "foundation")
    conflicting = {**_claim(claim_id, "analysis"), "source_fact_ids": first["source_fact_ids"]}
    enriched = engine.attach_evidence(
        state,
        claims=[first, conflicting],
        source_documents=[_source(source_id)],
        section_dossiers=[_dossier("foundation", claim_id)],
        gaps=[],
        claim_provenance={str(claim_id): {"source_document_ids": [str(source_id)]}},
    )
    report = engine.validate_writer_readiness(enriched)

    assert enriched.evidence_graph.claims[0].writer_policy.value == "prohibited"
    assert "claim_section_mismatch" in enriched.evidence_graph.claims[0].integrity_issues
    assert report.status == "blocked"
    assert any(
        item.code in {
            "INTELLIGENCE_CLAIM_INTEGRITY_INVALID",
            "INTELLIGENCE_SECTION_WITHOUT_EVIDENCE",
        }
        for item in report.blockers
    )
