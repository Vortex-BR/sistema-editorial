from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.orchestration.v3.executor import EditorialV3Executor
from app.orchestration.v3.graph import EditorialIntelligenceV3Graph, V3PipelineNodes
from app.orchestration.v3.state import V3PipelineState, V3Stage
from app.schemas.editorial_v3 import (
    EvidenceRole,
    SourceAssessment,
    SourceOwnershipType,
    SourcePageType,
    SourceRole,
    SourceUsagePolicy,
)
from app.schemas.editorial_v3_runtime import (
    ResearchTask,
    StructuredDocumentSection,
    StructuredSourceDocument,
)
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.research_planner import V3ResearchPlanningService
from app.services.editorial_v3.search_acceptance import (
    SourceCoverageService,
    expand_source_task_map,
    source_role_satisfies,
)
from app.services.research_engine import canonicalize_url


def _task(
    task_id: str,
    *,
    node_id: str,
    role: EvidenceRole,
    goal: str,
    required_source_roles: list[str],
    minimum_sources: int = 1,
    critical: bool = False,
) -> ResearchTask:
    return ResearchTask(
        task_id=task_id,
        knowledge_node_id=node_id,
        evidence_role=role,
        research_goal=goal,
        queries=[goal],
        required_source_roles=required_source_roles,
        minimum_independent_sources=minimum_sources,
        critical=critical,
        rationale="A tarefa valida a cobertura factual do nó de conhecimento.",
    )


def _document(
    url: str,
    *,
    title: str,
    text: str,
    role: SourceRole,
    allowed_roles: list[EvidenceRole],
    primary: bool = True,
) -> StructuredSourceDocument:
    assessment = SourceAssessment(
        url=url,
        ownership_type=(
            SourceOwnershipType.academic
            if role
            in {
                SourceRole.scientific_primary,
                SourceRole.scientific_review,
                SourceRole.academic_repository,
            }
            else SourceOwnershipType.independent_editorial
        ),
        page_type=(
            SourcePageType.research_article
            if role == SourceRole.scientific_primary
            else SourcePageType.technical_guide
        ),
        source_role=role,
        usage_policy=(
            SourceUsagePolicy.authoritative_evidence
            if primary
            else SourceUsagePolicy.corroborating_evidence
        ),
        priority_score=0.9,
        eligible_for_primary_evidence=primary,
        eligible_for_corroborating_evidence=True,
        eligible_for_external_reference=True,
        counts_toward_independent_source_diversity=True,
        requires_independent_corroboration=False,
        minimum_independent_corroborators=0,
        absolute_claim_support_allowed=True,
        allowed_evidence_roles=allowed_roles,
        reason_codes=["eligible_test_source"],
    )
    content = (text.strip() + " ") * 12
    return StructuredSourceDocument(
        document_id=uuid4(),
        url=url,
        canonical_url=url,
        title=title,
        author="Equipe técnica",
        publisher="Fonte independente",
        accessed_at=datetime.now(timezone.utc),
        language="pt-BR",
        document_type=assessment.page_type,
        content_hash=("a" if "one" in url else "b") * 64,
        sections=[
            StructuredDocumentSection(
                section_id=(
                    "sec_123456789abc" if "one" in url else "sec_abcdef123456"
                ),
                heading_path=[title],
                paragraphs=[content],
                source_locator="section:1",
                character_count=len(content),
            )
        ],
        assessment=assessment,
        plain_text=content,
    )


def test_compatible_source_roles_satisfy_capability_instead_of_exact_label():
    assert source_role_satisfies("scientific_review", "scientific_primary")
    assert source_role_satisfies("technical_procedural", "specialist_practical")
    assert not source_role_satisfies("scientific_primary", "ecommerce_blog")


def test_source_coverage_accepts_scientific_primary_for_scientific_review_request():
    task = _task(
        "task_mechanism",
        node_id="mechanism",
        role=EvidenceRole.mechanism,
        goal="Explicar o mecanismo fisiológico da germinação e a absorção inicial de água.",
        required_source_roles=["scientific_review"],
    )
    document = _document(
        "https://science-one.example/research",
        title="Estudo primário sobre absorção de água e germinação",
        text=(
            "A germinação começa com absorção de água, reativação metabólica e "
            "desenvolvimento da radícula sob condições adequadas de umidade."
        ),
        role=SourceRole.scientific_primary,
        allowed_roles=[EvidenceRole.mechanism],
    )
    key = canonicalize_url(str(document.canonical_url))

    report = SourceCoverageService().evaluate(
        tasks=[task],
        documents=[document],
        source_task_map={key: [task.task_id]},
    )

    assert report.status == "passed"
    assert report.task_reports[0].required_source_roles_missing == ()
    assert "required_source_roles_missing" not in report.reason_codes


def test_cross_task_mapping_reuses_relevant_source_without_assigning_unrelated_node():
    mechanism = _task(
        "task_mechanism",
        node_id="mechanism",
        role=EvidenceRole.mechanism,
        goal="Explicar absorção de água, ativação metabólica e desenvolvimento da radícula.",
        required_source_roles=["scientific_review"],
        critical=True,
    )
    conditions = _task(
        "task_conditions",
        node_id="conditions",
        role=EvidenceRole.environmental_condition,
        goal="Explicar como umidade, temperatura e oxigênio influenciam a germinação.",
        required_source_roles=["scientific_review"],
        critical=True,
    )
    unrelated = _task(
        "task_packaging",
        node_id="packaging",
        role=EvidenceRole.material,
        goal="Comparar embalagens comerciais, etiquetas de transporte e códigos de barras.",
        required_source_roles=["technical_procedural"],
    )
    document = _document(
        "https://science-one.example/germination",
        title="Germinação: absorção de água, temperatura e oxigênio",
        text=(
            "A absorção de água ativa o metabolismo e permite a emergência da radícula. "
            "Umidade contínua, temperatura adequada e disponibilidade de oxigênio "
            "influenciam a velocidade e a uniformidade da germinação."
        ),
        role=SourceRole.scientific_primary,
        allowed_roles=[
            EvidenceRole.mechanism,
            EvidenceRole.environmental_condition,
        ],
    )
    key = canonicalize_url(str(document.canonical_url))

    expanded, assignments = expand_source_task_map(
        tasks=[mechanism, conditions, unrelated],
        documents=[document],
        source_task_map={key: [mechanism.task_id]},
        minimum_score=0.12,
    )

    assert mechanism.task_id in expanded[key]
    assert conditions.task_id in expanded[key]
    assert unrelated.task_id not in expanded[key]
    assert any(item["task_id"] == conditions.task_id for item in assignments)


def test_research_planner_requires_two_sources_only_for_core_nodes():
    contract = KnowledgeContractBuilder().build(
        KnowledgeContractInput(
            topic="germinação de sementes em papel-toalha dentro de recipiente plástico",
            reader_start_state="Leitor sem conhecimento prático sobre o processo de germinação.",
            reader_final_state="Leitor capaz de executar, observar e concluir o processo com segurança.",
            article_promise="Explicar o processo completo, as condições, os sinais e as correções.",
            scope_limit="O conteúdo termina quando a plântula emerge do substrato inicial.",
        )
    )
    plan = V3ResearchPlanningService().build(contract, max_tasks=100)
    importance = {node.node_id: node.importance.value for node in contract.nodes}

    assert any(importance[task.knowledge_node_id] == "core" for task in plan.tasks)
    assert any(importance[task.knowledge_node_id] != "core" for task in plan.tasks)
    for task in plan.tasks:
        if importance[task.knowledge_node_id] == "core":
            assert task.minimum_independent_sources == 2
        else:
            assert task.minimum_independent_sources == 1


def test_graph_preserves_specific_synthesis_block_code():
    async def noop(state):
        return state

    nodes = V3PipelineNodes(
        **{
            name: noop
            for name in V3PipelineNodes.__annotations__
            if name not in {
                "source_coverage_gate",
                "targeted_source_recovery",
                "intelligence_planner",
                "evidence_graph_builder",
                "intelligence_gate",
            }
        }
    )
    state = V3PipelineState(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        stage=V3Stage.knowledge_synthesizer,
        blocking_code="V3_APPROVED_CLAIMS_INSUFFICIENT",
        blocking_reason="A síntese não produziu afirmações aprovadas suficientes.",
    )

    result = EditorialIntelligenceV3Graph(nodes)._transition(state)

    assert result.stage == V3Stage.blocked
    assert result.blocking_code == "V3_APPROVED_CLAIMS_INSUFFICIENT"
    assert "afirmações aprovadas" in str(result.blocking_reason)



@pytest.mark.asyncio
async def test_claim_extraction_isolates_batch_type_error_and_preserves_other_documents():
    contract = KnowledgeContractBuilder().build(
        KnowledgeContractInput(
            topic="germinação de sementes em papel-toalha",
            reader_start_state="Leitor sem clareza sobre o processo de germinação.",
            reader_final_state="Leitor capaz de executar e reconhecer o resultado esperado.",
            article_promise="Explicar o processo com evidências verificáveis e observações claras.",
            scope_limit="O conteúdo termina na confirmação do resultado inicial.",
        )
    )
    plan = V3ResearchPlanningService().build(contract, max_tasks=100)
    task = plan.tasks[0]
    document_one = _document(
        "https://science-one.example/claim",
        title="Fonte científica sobre germinação inicial",
        text="A absorção de água inicia a ativação metabólica e antecede a emergência da radícula.",
        role=SourceRole.scientific_primary,
        allowed_roles=[task.evidence_role],
    )
    document_two = _document(
        "https://science-two.example/claim",
        title="Segunda fonte sobre germinação inicial",
        text="A disponibilidade contínua de água permite a retomada metabólica da semente.",
        role=SourceRole.scientific_primary,
        allowed_roles=[task.evidence_role],
    )
    executor = EditorialV3Executor.__new__(EditorialV3Executor)
    executor.pipeline_run = SimpleNamespace(id=uuid4())
    executor.db = SimpleNamespace(
        scalars=AsyncMock(return_value=[]),
        scalar=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )
    executor.artifacts = SimpleNamespace(claim=AsyncMock(return_value=SimpleNamespace()))
    executor._flag = lambda name: 6 if name == "v3_max_documents_per_research_task" else 0
    executor._document_for_agent = lambda document, _goal: {
        "url": str(document.canonical_url)
    }

    calls = 0

    async def agent_call(**kwargs):
        nonlocal calls
        calls += 1
        documents = kwargs["input_json"]["documents"]
        if len(documents) > 1:
            raise TypeError("synthetic malformed batch")
        url = documents[0]["url"]
        return {
            "claims": [
                {
                    "claim_key": "water_absorption_starts_germination",
                    "support_group": "water_absorption_germination",
                    "source_url": url,
                    "knowledge_node_id": task.knowledge_node_id,
                    "evidence_role": task.evidence_role.value,
                    "claim_text": "A absorção de água participa do início da germinação.",
                    "exact_quote": "A absorção de água",
                    "source_locator": "section:1",
                    "method_labels": [],
                    "conditions": [],
                    "applicability": [],
                    "limitations": [],
                    "conclusion_status": "well_supported",
                    "confidence_score": 0.8,
                    "critical": task.critical,
                    "conflict_group": None,
                }
            ],
            "discovered_method_labels": [],
            "unresolved_questions": [],
        }

    executor._agent_call = agent_call
    key_one = canonicalize_url(str(document_one.canonical_url))
    key_two = canonicalize_url(str(document_two.canonical_url))
    state = V3PipelineState(
        project_id=uuid4(),
        pipeline_run_id=executor.pipeline_run.id,
        contract_id=uuid4(),
        source_task_map={
            key_one: [task.task_id],
            key_two: [task.task_id],
        },
    )
    persisted_plan = SimpleNamespace(
        questions_by_task_id={task.task_id: SimpleNamespace(id=uuid4())}
    )

    persisted = await executor._extract_claims_for_tasks(
        state=state,
        contract=contract,
        tasks=[task],
        documents=[document_one, document_two],
        persisted_plan=persisted_plan,
    )

    assert persisted == 2
    assert calls == 3
    assert executor.artifacts.claim.await_count == 2
    failures = state.research_metrics["claim_extraction_failures"]
    assert failures == [
        {
            "task_id": task.task_id,
            "knowledge_node_id": task.knowledge_node_id,
            "phase": "batch",
            "error_type": "TypeError",
        }
    ]


def _corroborating_assessment(url: str, *, host_suffix: str = "") -> SourceAssessment:
    """Build a corroborating (non-authoritative) independent source assessment."""
    return SourceAssessment(
        url=url,
        ownership_type=SourceOwnershipType.independent_editorial,
        page_type=SourcePageType.technical_guide,
        source_role=SourceRole.technical_procedural,
        usage_policy=SourceUsagePolicy.corroborating_evidence,
        priority_score=0.78,
        eligible_for_primary_evidence=False,
        eligible_for_corroborating_evidence=True,
        eligible_for_external_reference=True,
        counts_toward_independent_source_diversity=True,
        requires_independent_corroboration=False,
        minimum_independent_corroborators=0,
        absolute_claim_support_allowed=False,
        allowed_evidence_roles=list(EvidenceRole),
        reason_codes=["classified_as_technical_procedural", "usage_corroborating_evidence"],
    )


def _authoritative_assessment(url: str) -> SourceAssessment:
    """Build an authoritative scientific source assessment."""
    return SourceAssessment(
        url=url,
        ownership_type=SourceOwnershipType.academic,
        page_type=SourcePageType.research_article,
        source_role=SourceRole.scientific_primary,
        usage_policy=SourceUsagePolicy.authoritative_evidence,
        priority_score=0.95,
        eligible_for_primary_evidence=True,
        eligible_for_corroborating_evidence=True,
        eligible_for_external_reference=True,
        counts_toward_independent_source_diversity=True,
        requires_independent_corroboration=False,
        minimum_independent_corroborators=0,
        absolute_claim_support_allowed=True,
        allowed_evidence_roles=list(EvidenceRole),
        reason_codes=["classified_as_scientific_primary", "usage_authoritative_evidence"],
    )


class TestProceduralCorroboration:
    """Validate the procedural corroboration path in validate_bundle."""

    def test_procedural_bundle_approved_with_two_independent_corroborating_sources(self):
        """Bundles in procedural context with 2+ independent corroborating sources
        pass validation even without an authoritative source."""
        from app.services.editorial_v3.source_policy import ResearchSourcePolicyService

        policy = ResearchSourcePolicyService()
        assessments = [
            _corroborating_assessment("https://guide-one.example/germination"),
            _corroborating_assessment("https://guide-two.example/germination"),
        ]
        decision = policy.validate_bundle(
            assessments,
            critical_claim=False,
            absolute_claim=False,
            procedural_context=True,
        )
        assert decision.status == "passed", f"Expected passed, got blockers: {decision.blockers}"
        assert decision.authoritative_source_count == 0
        assert decision.independent_source_count == 2
        assert any("procedural corroboration" in w for w in decision.warnings)

    def test_procedural_absolute_claim_still_requires_authoritative_source(self):
        """Absolute claims (conclusion_status=confirmed) in procedural context
        must still have an authoritative source — procedural bypass does not apply."""
        from app.services.editorial_v3.source_policy import ResearchSourcePolicyService

        policy = ResearchSourcePolicyService()
        assessments = [
            _corroborating_assessment("https://guide-one.example/germination"),
            _corroborating_assessment("https://guide-two.example/germination"),
        ]
        decision = policy.validate_bundle(
            assessments,
            critical_claim=False,
            absolute_claim=True,
            procedural_context=True,
        )
        assert decision.status == "blocked"
        assert any("authoritative" in b for b in decision.blockers)

    def test_non_procedural_context_requires_authoritative_source(self):
        """Without procedural_context, corroborating-only bundles are blocked
        regardless of the number of independent sources."""
        from app.services.editorial_v3.source_policy import ResearchSourcePolicyService

        policy = ResearchSourcePolicyService()
        assessments = [
            _corroborating_assessment("https://guide-one.example/germination"),
            _corroborating_assessment("https://guide-two.example/germination"),
            _corroborating_assessment("https://guide-three.example/germination"),
        ]
        decision = policy.validate_bundle(
            assessments,
            critical_claim=False,
            absolute_claim=False,
            procedural_context=False,
        )
        assert decision.status == "blocked"
        assert any("authoritative" in b for b in decision.blockers)

    def test_procedural_with_single_independent_source_still_blocked(self):
        """One independent source in procedural context is not enough —
        the minimum corroboration threshold (2) must be met."""
        from app.services.editorial_v3.source_policy import ResearchSourcePolicyService

        policy = ResearchSourcePolicyService()
        assessments = [
            _corroborating_assessment("https://guide-one.example/germination"),
        ]
        decision = policy.validate_bundle(
            assessments,
            critical_claim=False,
            absolute_claim=False,
            procedural_context=True,
        )
        assert decision.status == "blocked"
        assert any("authoritative" in b for b in decision.blockers)

    def test_procedural_with_authoritative_source_passes_normally(self):
        """When an authoritative source is present, the bundle passes
        through the normal path even in procedural context."""
        from app.services.editorial_v3.source_policy import ResearchSourcePolicyService

        policy = ResearchSourcePolicyService()
        assessments = [
            _authoritative_assessment("https://science.example/paper"),
            _corroborating_assessment("https://guide-one.example/germination"),
        ]
        decision = policy.validate_bundle(
            assessments,
            critical_claim=False,
            absolute_claim=False,
            procedural_context=True,
        )
        assert decision.status == "passed"
        assert decision.authoritative_source_count == 1
        # No procedural corroboration warning since authoritative source is present
        assert not any("procedural corroboration" in w for w in decision.warnings)


@pytest.mark.asyncio
async def test_materialize_synthesis_cleans_old_records_for_idempotency():
    """Verify that materialize_synthesis cleans old V3MethodDossierRecord,
    V3DecisionMatrixRecord, V3SectionDossierRecord, and KnowledgeGapRecord rows
    prior to adding new ones, avoiding duplicates and IntegrityError conflicts."""
    from app.services.editorial_v3.artifact_repository import V3ArtifactRepository
    
    db = AsyncMock()
    db.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: []))
    
    project_id = uuid4()
    pipeline_run_id = uuid4()
    contract_id = uuid4()
    
    repo = V3ArtifactRepository(db, project_id=project_id, pipeline_run_id=pipeline_run_id)
    
    await repo.materialize_synthesis(
        contract_id=contract_id,
        methods=[],
        sections=[],
        decision_matrix=None,
        gaps=[],
        references={},
    )
    
    # Verify that the 4 delete statements were executed
    assert db.execute.call_count == 4
    db.flush.assert_called()


