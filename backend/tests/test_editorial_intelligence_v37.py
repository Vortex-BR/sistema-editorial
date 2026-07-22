from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.editorial_intelligence import (
    ContentIntelligenceState,
    EditorialQuestion,
    EditorialQuestionKind,
    EmergentEditorialQuestionProposal,
    SectionIntelligencePlan,
)
from app.schemas.editorial_v3 import EvidenceRole
from app.services.editorial_v3.content_intelligence import (
    ContentIntelligenceEngine,
    _semantic_alignment,
)


def _state() -> ContentIntelligenceState:
    question = EditorialQuestion(
        question_id="q_foundation_central_1",
        section_id="foundation",
        kind=EditorialQuestionKind.central,
        question="Qual faixa de temperatura influencia a germinação?",
        critical=True,
        research_required=True,
        required_evidence_roles=[EvidenceRole.mechanism],
        completion_signal="A faixa e seus limites são explicados.",
    )
    section = SectionIntelligencePlan(
        section_id="foundation",
        sequence=1,
        title_function="Explicar as condições da germinação",
        editorial_goal="Explicar de forma verificável as condições que alteram a germinação.",
        reader_state_before="O leitor ainda não compreende as condições ambientais.",
        reader_state_after="O leitor compreende as condições ambientais relevantes.",
        question_ids=[question.question_id],
        research_required=True,
    )
    now = datetime.now(timezone.utc)
    return ContentIntelligenceState(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        contract_id=uuid4(),
        created_at=now,
        updated_at=now,
        topic="germinação de sementes",
        content_type="explanatory_guide",
        content_objective="Explicar condições ambientais com evidências.",
        search_intent="informational",
        questions=[question],
        sections=[section],
    )


def test_semantic_alignment_rejects_generic_or_cross_dimension_matches():
    assert _semantic_alignment(
        "Qual faixa de temperatura influencia a germinação?",
        "A pigmentação das folhas muda durante o desenvolvimento.",
    ) == 0.0
    assert _semantic_alignment(
        "Qual faixa de temperatura influencia a germinação?",
        "Este método é uma etapa importante do guia.",
    ) == 0.0
    assert _semantic_alignment(
        "Qual faixa de temperatura influencia a germinação?",
        "A temperatura em graus Celsius altera a velocidade da germinação.",
    ) >= 0.34


def test_emergent_questions_are_bounded_deduplicated_and_evidence_grounded():
    engine = ContentIntelligenceEngine()
    state = _state()
    proposals = [
        EmergentEditorialQuestionProposal(
            section_id="foundation",
            kind=EditorialQuestionKind.knowledge,
            question="Como a condensação revela excesso de umidade no recipiente?",
            rationale="As fontes descrevem condensação e excesso de umidade.",
            critical=True,
            required_evidence_roles=[EvidenceRole.mechanism],
        ),
        EmergentEditorialQuestionProposal(
            section_id="foundation",
            kind=EditorialQuestionKind.knowledge,
            question="Qual faixa de temperatura influencia a germinação?",
            rationale="Duplicata da pergunta central.",
            critical=True,
        ),
        EmergentEditorialQuestionProposal(
            section_id="foundation",
            kind=EditorialQuestionKind.knowledge,
            question="Como a cor da embalagem influencia a marca?",
            rationale="Não existe evidência para esta pergunta.",
            critical=True,
        ),
    ]
    claims = [
        {
            "knowledge_node_id": "foundation",
            "claim_text": (
                "Condensação intensa no recipiente pode indicar excesso de umidade "
                "e menor troca de ar."
            ),
        }
    ]

    updated = engine.add_emergent_questions(
        state,
        proposals=proposals,
        claims=claims,
        maximum_questions=2,
    )

    emergent = [item for item in updated.questions if item.origin == "emergent"]
    assert len(emergent) == 1
    assert "condensação" in emergent[0].question.casefold()
    assert emergent[0].section_id == "foundation"
    assert emergent[0].question_id in updated.sections[0].question_ids
    assert updated.revision == state.revision + 1
    assert updated.checksum and updated.checksum != state.checksum
