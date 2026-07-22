from uuid import uuid4

from app.orchestration.v3.graph import EditorialIntelligenceV3Graph, V3PipelineNodes
from app.orchestration.v3.state import V3PipelineState, V3Stage
from app.schemas.editorial_v3 import EvidenceRole
from app.schemas.editorial_v3_runtime import (
    ResearchCoverageRequirement,
    ResearchTask,
    V3ResearchPlan,
)
from app.services.editorial_v3.information_coverage import (
    InformationCoverageService,
    infer_requirement_ids,
    requirements_for_task,
)
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.research_planner import (
    V3ResearchPlanningService,
    _information_units,
)


def _requirement(
    suffix: str,
    description: str,
    *,
    critical: bool = True,
    role: EvidenceRole = EvidenceRole.definition,
) -> ResearchCoverageRequirement:
    return ResearchCoverageRequirement(
        requirement_id=f"task_{suffix}_req_01",
        requirement_type="knowledge",
        description=description,
        evidence_roles=[role],
        critical=critical,
        minimum_approved_claims=1,
        minimum_independent_sources=1,
        query_terms=description.casefold().split()[:8],
    )


def _task(
    suffix: str,
    description: str,
    *,
    critical: bool = True,
    role: EvidenceRole = EvidenceRole.definition,
) -> ResearchTask:
    requirement = _requirement(
        suffix, description, critical=critical, role=role
    )
    return ResearchTask(
        task_id=f"task_{suffix}",
        knowledge_node_id=f"node_{suffix}",
        evidence_role=role,
        research_goal=f"Pesquisar e responder com evidência verificável: {description}",
        queries=[f"tema {description} evidência"],
        required_source_roles=["institutional"],
        minimum_independent_sources=1,
        critical=critical,
        rationale="A informação é necessária para cumprir o contrato editorial.",
        coverage_requirements=[requirement],
    )


def _plan(*tasks: ResearchTask) -> V3ResearchPlan:
    return V3ResearchPlan(
        rationale=(
            "Plano orientado por informações explícitas e verificáveis do contrato."
        ),
        tasks=list(tasks),
        method_discovery_queries=["tema conceitos", "tema evidência"],
        terminology_queries=[],
        stop_conditions=[
            "todos os requisitos críticos estão cobertos",
            "as evidências são independentes e adequadas",
        ],
        maximum_search_queries=max(5, len(tasks) * 2),
    )


def _record(
    task: ResearchTask,
    *,
    claim_id: str,
    approved: bool = True,
    host: str = "universidade.example",
    requirement_id: str | None = None,
    text: str | None = None,
) -> dict:
    requirement = requirements_for_task(task)[0]
    return {
        "claim_key": claim_id,
        "canonical_claim_id": claim_id,
        "knowledge_node_id": task.knowledge_node_id,
        "evidence_role": task.evidence_role.value,
        "claim_text": text or requirement.description,
        "approved": approved,
        "coverage_requirement_ids": [
            requirement_id or requirement.requirement_id
        ],
        "source_host": host,
        "independent_source": True,
        "authoritative_source": True,
        "usage_policy": "authoritative_evidence",
        "bundle_blockers": [] if approved else ["insufficient_support"],
    }


def test_planner_expands_contract_into_stable_information_units_and_queries():
    contract = KnowledgeContractBuilder().build(
        KnowledgeContractInput(
            topic="germinação de sementes em papel-toalha dentro de recipiente fechado",
            reader_start_state="Leitor sem clareza sobre o processo de germinação.",
            reader_final_state="Leitor capaz de executar, observar e concluir o processo.",
            article_promise="Explicar condições, sequência, sinais, riscos e transferência.",
            scope_limit="O conteúdo termina na transferência para o substrato inicial.",
        )
    )

    plan = V3ResearchPlanningService().build(contract, max_tasks=100)

    assert plan.tasks
    all_requirement_ids: list[str] = []
    for task in plan.tasks:
        assert task.coverage_requirements
        all_requirement_ids.extend(
            item.requirement_id for item in task.coverage_requirements
        )
        if task.critical:
            assert all(
                item.minimum_independent_sources == 2
                for item in task.coverage_requirements
            )
        assert len(task.queries) <= 6
        assert any(contract.topic.split()[0].casefold() in query.casefold() for query in task.queries)
        # At least one initial query must carry terms from a concrete requirement,
        # rather than only a generic node label.
        requirement_terms = {
            term
            for item in task.coverage_requirements
            for term in item.query_terms[:4]
        }
        assert any(
            any(term in query.casefold() for term in requirement_terms)
            for query in task.queries
        )
        assert any(
            "study review guideline evidence" in query.casefold()
            for query in task.queries
        )

    assert len(all_requirement_ids) == len(set(all_requirement_ids))



def test_compound_contract_text_is_split_without_discarding_later_information():
    units = _information_units(
        "Controlar a temperatura. Manter umidade sem encharcar; observar sinais de mofo."
    )

    assert units == [
        "Controlar a temperatura",
        "Manter umidade sem encharcar",
        "observar sinais de mofo",
    ]

def test_coverage_passes_with_fewer_than_eighteen_claims_when_all_information_is_supported():
    tasks = (
        _task("temperature", "Faixa de temperatura adequada para a etapa inicial"),
        _task("humidity", "Como manter umidade sem encharcamento"),
        _task("transfer", "Sinal observável para realizar a transferência"),
    )
    plan = _plan(*tasks)
    records = [
        _record(task, claim_id=f"claim-{index}", host=f"fonte{index}.org")
        for index, task in enumerate(tasks, start=1)
    ]

    report = InformationCoverageService().evaluate(
        topic="germinação de sementes",
        plan=plan,
        evidence_records=records,
        minimum_overall_ratio=0.85,
    )

    assert len(records) == 3
    assert report.status == "passed"
    assert report.overall_coverage_ratio == 1.0
    assert report.critical_coverage_ratio == 1.0
    assert report.critical_missing_requirement_ids == ()


def test_eighteen_repetitive_claims_do_not_hide_a_missing_critical_information_unit():
    temperature = _task(
        "temperature", "Faixa de temperatura adequada para a etapa inicial"
    )
    humidity = _task("humidity", "Como manter umidade sem encharcamento")
    transfer = _task("transfer", "Sinal observável para realizar a transferência")
    plan = _plan(temperature, humidity, transfer)
    records = [
        _record(
            temperature,
            claim_id=f"temperature-{index}",
            host=f"fonte{index}.org",
        )
        for index in range(18)
    ]
    records.append(_record(humidity, claim_id="humidity-1", host="manual.org"))

    report = InformationCoverageService().evaluate(
        topic="germinação de sementes",
        plan=plan,
        evidence_records=records,
    )

    missing_id = requirements_for_task(transfer)[0].requirement_id
    assert report.status == "incomplete"
    assert report.suggested_blocking_code == (
        "V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE"
    )
    assert missing_id in report.critical_missing_requirement_ids
    assert any(
        item["requirement_id"] == missing_id for item in report.recovery_tasks
    )


def test_rejected_claim_produces_requirement_level_diagnostics_and_recovery_queries():
    tasks = (
        _task("risk", "Risco de excesso de água e sinais de deterioração"),
        _task("humidity", "Como manter umidade sem encharcamento"),
        _task("transfer", "Sinal observável para realizar a transferência"),
    )
    plan = _plan(*tasks)
    records = [
        _record(tasks[0], claim_id="risk-rejected", approved=False),
        _record(tasks[1], claim_id="humidity-ok", host="manual.org"),
        _record(tasks[2], claim_id="transfer-ok", host="university.org"),
    ]

    report = InformationCoverageService().evaluate(
        topic="germinação de sementes",
        plan=plan,
        evidence_records=records,
    )
    risk_report = next(
        item for item in report.requirement_reports if item.task_id == tasks[0].task_id
    )

    assert risk_report.status == "partial"
    assert "claims_not_approved_for_requirement" in risk_report.reason_codes
    assert "evidence_policy_blocked_requirement" in risk_report.reason_codes
    assert any(
        item["task_id"] == tasks[0].task_id and item["query"]
        for item in report.recovery_tasks
    )


def test_requirement_inference_is_conservative_and_role_aware():
    task = _task(
        "temperature",
        "Faixa de temperatura adequada para a etapa inicial",
        role=EvidenceRole.environmental_condition,
    )
    requirement_id = requirements_for_task(task)[0].requirement_id

    assert infer_requirement_ids(
        task,
        claim_text="A faixa de temperatura adequada deve permanecer estável durante a etapa inicial.",
        evidence_role=EvidenceRole.environmental_condition.value,
    ) == [requirement_id]
    assert infer_requirement_ids(
        task,
        claim_text="O texto descreve apenas a embalagem do produto.",
        evidence_role=EvidenceRole.environmental_condition.value,
    ) == []
    assert infer_requirement_ids(
        task,
        claim_text="A faixa de temperatura adequada deve permanecer estável.",
        evidence_role=EvidenceRole.comparison.value,
    ) == []


def test_graph_routes_information_recovery_without_consuming_intelligence_recovery_state():
    async def noop(state):
        return state

    nodes = V3PipelineNodes(
        **{
            name: noop
            for name in V3PipelineNodes.__annotations__
        }
    )
    state = V3PipelineState(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        stage=V3Stage.knowledge_synthesizer,
        information_coverage_report={
            "status": "incomplete",
            "suggested_blocking_code": "V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE",
        },
        information_recovery_tasks=[
            {
                "task_id": "task_temperature",
                "requirement_id": "task_temperature_req_01",
                "query": "temperatura germinação estudo",
            }
        ],
        information_recovery_round=1,
        intelligence_recovery_round=0,
    )

    result = EditorialIntelligenceV3Graph(nodes)._transition(state)

    assert result.stage == V3Stage.targeted_source_recovery
    assert result.information_recovery_round == 1
    assert result.intelligence_recovery_round == 0


def test_legacy_tasks_receive_a_safe_fallback_requirement():
    task = ResearchTask(
        task_id="task_legacy",
        knowledge_node_id="node_legacy",
        evidence_role=EvidenceRole.definition,
        research_goal="Explicar um conceito legado com fonte verificável e adequada.",
        queries=["conceito legado definição"],
        required_source_roles=["institutional"],
        minimum_independent_sources=1,
        critical=True,
        rationale="Mantém checkpoints anteriores executáveis durante a atualização.",
    )

    requirements = requirements_for_task(task)

    assert len(requirements) == 1
    assert requirements[0].requirement_id == "task_legacy_req_legacy"
    assert requirements[0].description == task.research_goal


def test_recovery_queue_interleaves_query_variants_across_missing_information_units():
    tasks = (
        _task("temperature", "Faixa de temperatura adequada para a etapa inicial"),
        _task("humidity", "Como manter umidade sem encharcamento"),
        _task("transfer", "Sinal observável para realizar a transferência"),
    )

    report = InformationCoverageService().evaluate(
        topic="germinação de sementes",
        plan=_plan(*tasks),
        evidence_records=[],
    )

    first_round = report.recovery_tasks[:3]
    assert len(first_round) == 3
    assert len({item["requirement_id"] for item in first_round}) == 3
    assert {item["query_variant"] for item in first_round} == {0}


def test_graph_reprocesses_existing_sources_when_recovery_retargets_a_requirement():
    async def noop(state):
        return state

    nodes = V3PipelineNodes(
        **{name: noop for name in V3PipelineNodes.__annotations__}
    )
    state = V3PipelineState(
        project_id=uuid4(),
        pipeline_run_id=uuid4(),
        stage=V3Stage.targeted_source_recovery,
        raw_source_documents=[
            {
                "url": "https://example.org/source",
                "title": "Fonte existente",
                "content": "Conteúdo técnico existente.",
            }
        ],
        information_recovery_tasks=[
            {
                "task_id": "task_temperature",
                "requirement_id": "task_temperature_req_01",
                "query": "temperatura germinação estudo",
            }
        ],
        research_metrics={
            "last_recovery_mode": "information",
            "information_recovery_new_candidate_count": 0,
            "information_recovery_retargeted_existing_count": 1,
        },
    )

    result = EditorialIntelligenceV3Graph(nodes)._transition(state)

    assert result.stage == V3Stage.source_reader
    assert result.information_recovery_tasks == []
