from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.orchestration.v3.executor import EditorialV3Executor, V3PipelineBlocked
from app.orchestration.v3.state import V3PipelineState
from app.schemas.api import ProjectCreate
from app.schemas.editorial_v3 import (
    ApproachDimension,
    EditorialContentTypeV3,
    EvidenceRole,
)
from app.schemas.editorial_v3_runtime import ResearchTask, V3ResearchPlan
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.research_planner import (
    V3ResearchPlanningService,
    schedule_research_queries,
)
from app.services.research_engine import SearchProviderError


def _task(index: int, *, query_count: int = 3, critical: bool = True) -> ResearchTask:
    return ResearchTask(
        task_id=f"task_{index:02d}",
        knowledge_node_id=f"node_{index:02d}",
        evidence_role=EvidenceRole.definition,
        research_goal=f"Pesquisar evidências suficientes para o nó editorial número {index}.",
        queries=[f"consulta {index} rodada {round_}" for round_ in range(query_count)],
        required_source_roles=["institutional"],
        minimum_independent_sources=2,
        critical=critical,
        rationale="O nó precisa de fontes independentes antes da redação.",
    )


def _plan(task_count: int = 13, maximum_search_queries: int = 36) -> V3ResearchPlan:
    return V3ResearchPlan(
        rationale="Plano hierárquico com distribuição equilibrada entre todos os nós.",
        tasks=[_task(index) for index in range(task_count)],
        method_discovery_queries=["descoberta um", "descoberta dois"],
        stop_conditions=["todos os nós cobertos", "orçamento atingido"],
        maximum_search_queries=maximum_search_queries,
    )


def _flag_value(name: str):
    values = {
        "v3_max_source_documents": 48,
        "v3_max_search_provider_requests": 120,
        "v3_max_search_provider_retries": 40,
        "v3_max_search_estimated_credits": 120,
        "v3_source_discovery_timeout_seconds": 300,
        "v3_min_candidate_relevance": 0.0,
        "v3_search_results_per_query": 5,
    }
    return values.get(name, 5)


def _procedural_contract():
    return KnowledgeContractBuilder().build(
        KnowledgeContractInput(
            topic="guia de cultivo em ambientes controlados",
            reader_start_state="Leitor sem um mapa claro das alternativas de ambiente.",
            reader_final_state="Leitor capaz de comparar ambientes e acompanhar o resultado.",
            article_promise=(
                "Explicar as alternativas, os requisitos, a execução, os sinais e os limites."
            ),
            scope_limit="O conteúdo termina na confirmação do resultado definido no briefing.",
            required_method_labels=(
                "ambiente interno",
                "ambiente externo",
                "ambiente protegido",
            ),
            approach_dimension=ApproachDimension.environment,
        )
    )


def test_round_robin_schedule_covers_every_node_before_deepening():
    plan = _plan()
    schedule = schedule_research_queries(plan.tasks, limit=28)

    counts: dict[str, int] = {}
    for item in schedule:
        counts[item.task_id] = counts.get(item.task_id, 0) + 1

    assert len(schedule) == 28
    assert set(counts) == {task.task_id for task in plan.tasks}
    assert min(counts.values()) >= 2
    assert max(counts.values()) == 3
    assert [item.task_id for item in schedule[:13]] == [
        task.task_id for task in plan.tasks
    ]


def test_round_robin_scheduler_skips_queries_already_executed():
    tasks = [_task(index) for index in range(3)]
    executed = {
        task.task_id: [task.queries[0], task.queries[1]] for task in tasks
    }

    schedule = schedule_research_queries(
        tasks,
        limit=3,
        executed_queries_by_task=executed,
    )

    assert [item.query_index for item in schedule] == [2, 2, 2]
    assert [item.task_id for item in schedule] == [task.task_id for task in tasks]


class _Runtime:
    async def search_credential(self):
        return "fake", "fake-key"

    async def event(self, *_args, **_kwargs):
        return None


class _Search:
    def __init__(self):
        self.calls: list[str] = []

    async def search(self, query, *_args, **_kwargs):
        self.calls.append(query)
        return []


class _FailingSearch:
    async def search(self, *_args, **_kwargs):
        raise SearchProviderError(
            "unavailable",
            provider="fake",
            model="search",
            retryable=True,
        )


class _Artifacts:
    async def approved_coverage_by_node(self):
        return {}

    async def approve_claim_bundles(self):
        return None


@pytest.mark.asyncio
async def test_source_discovery_allocates_initial_budget_to_every_task():
    executor = object.__new__(EditorialV3Executor)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.pipeline_run = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = _Runtime()
    executor.search = _Search()
    executor._stage_context = None
    executor._stage = AsyncMock()
    executor._cancellation_boundary = AsyncMock()
    executor._flag = _flag_value

    plan = _plan()
    contract = _procedural_contract()
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=executor.pipeline_run.id,
        contract=contract.model_dump(mode="json"),
        research_plan=plan.model_dump(mode="json"),
    )

    await executor.source_discovery(state)

    executed = state.research_metrics["executed_queries_by_task"]
    assert state.research_metrics["initial_query_count"] == 28
    assert set(state.research_metrics["initial_uncovered_task_ids"]) == {
        task.task_id for task in plan.tasks
    }
    assert set(executed) == {task.task_id for task in plan.tasks}
    assert all(len(executed[task.task_id]) >= 2 for task in plan.tasks)
    assert len(executor.search.calls) == 28


def test_research_queries_use_the_concise_search_subject_without_quoting_the_brief():
    data = KnowledgeContractInput(
        topic="Um briefing editorial muito descritivo. " + ("contexto adicional " * 10),
        search_subject="cultivo de mudas em casa",
        reader_start_state="Leitor sem clareza sobre o processo e suas alternativas.",
        reader_final_state="Leitor capaz de escolher e confirmar o resultado esperado.",
        article_promise="Explicar os fundamentos, a escolha e a execução completa.",
        scope_limit="Encerrar na confirmação do resultado prometido.",
        content_type=EditorialContentTypeV3.explanatory_guide,
    )
    contract = KnowledgeContractBuilder().build(data)

    plan = V3ResearchPlanningService().build(contract)
    queries = [query for task in plan.tasks for query in task.queries]

    assert queries
    assert all("cultivo de mudas em casa" in query for query in queries)
    assert all(contract.topic not in query for query in queries)
    assert all('"cultivo de mudas em casa"' not in query for query in queries)


@pytest.mark.asyncio
async def test_source_discovery_propagates_provider_failure_as_technical_error():
    executor = object.__new__(EditorialV3Executor)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.pipeline_run = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = _Runtime()
    executor.search = _FailingSearch()
    executor._stage_context = None
    executor._stage = AsyncMock()
    executor._cancellation_boundary = AsyncMock()
    executor._flag = _flag_value
    contract = _procedural_contract()
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=executor.pipeline_run.id,
        contract=contract.model_dump(mode="json"),
        research_plan=_plan(task_count=3, maximum_search_queries=5).model_dump(
            mode="json"
        ),
    )

    await executor.source_discovery(state)

    assert state.blocking_code == "V3_SEARCH_PROVIDERS_UNAVAILABLE"
    assert state.research_metrics["search_failure_categories"] == ["unavailable"] * 3


@pytest.mark.asyncio
async def test_supplemental_budget_is_actually_executed_for_missing_nodes():
    executor = object.__new__(EditorialV3Executor)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.pipeline_run = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = _Runtime()
    executor.search = _Search()
    executor.reader = SimpleNamespace()
    executor.artifacts = _Artifacts()
    executor._stage_context = None
    executor._cancellation_boundary = AsyncMock()
    executor._flag = _flag_value
    executor._extract_claims_for_tasks = AsyncMock(return_value=0)

    contract = _procedural_contract()
    plan = _plan(task_count=3, maximum_search_queries=8)
    initially_executed = {
        task.task_id: [task.queries[0], task.queries[1]] for task in plan.tasks
    }
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=executor.pipeline_run.id,
        contract=contract.model_dump(mode="json"),
        research_plan=plan.model_dump(mode="json"),
        research_metrics={
            "total_query_count": 6,
            "executed_queries_by_task": initially_executed,
        },
    )

    await executor._supplement_research(
        state=state,
        contract=contract,
        plan=plan,
        persisted_plan=SimpleNamespace(questions_by_task_id={}),
    )

    assert state.research_metrics["supplemental_query_count"] == 2
    assert state.research_metrics["total_query_count"] == 8
    assert state.research_metrics["supplemental_query_modes"] == {
        "planned": 2,
        "targeted": 0,
    }
    assert len(executor.search.calls) == 2
    assert executor.search.calls == [plan.tasks[0].queries[2], plan.tasks[1].queries[2]]


@pytest.mark.asyncio
async def test_knowledge_architect_blocks_mixed_approach_taxonomy():
    executor = object.__new__(EditorialV3Executor)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.pipeline_run = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = _Runtime()
    executor._stage_context = None
    executor._stage = AsyncMock()
    executor._agent_call = AsyncMock(
        return_value={
            "declared_dimension": "environment",
            "coherent_set": False,
            "items": [
                {
                    "label": "ambiente interno",
                    "detected_dimension": "environment",
                    "comparable_at_same_level": True,
                    "valid_for_topic": True,
                    "rationale": "É um ambiente comparável aos demais ambientes.",
                },
                {
                    "label": "ambiente externo",
                    "detected_dimension": "environment",
                    "comparable_at_same_level": True,
                    "valid_for_topic": True,
                    "rationale": "É um ambiente comparável aos demais ambientes.",
                },
                {
                    "label": "rega manual",
                    "detected_dimension": "technique",
                    "comparable_at_same_level": False,
                    "valid_for_topic": True,
                    "rationale": "É uma técnica e não um ambiente de cultivo.",
                },
            ],
            "blocking_issues": ["O conjunto mistura ambiente e técnica."],
            "normalized_collective_name": "ambientes de cultivo",
        }
    )
    contract = _procedural_contract().model_copy(
        update={
            "required_method_labels": [
                "ambiente interno",
                "ambiente externo",
                "rega manual",
            ]
        }
    )
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=executor.pipeline_run.id,
        contract=contract.model_dump(mode="json"),
    )

    with pytest.raises(V3PipelineBlocked) as exc:
        await executor.knowledge_architect(state)

    assert exc.value.code == "V3_APPROACH_TAXONOMY_INVALID"


def _project_payload(**brief_overrides):
    briefing = {
        "content_objective": "Ensinar o leitor a comparar alternativas com segurança.",
        "primary_keyword": "guia de cultivo",
        "reader_context": "Leitor iniciante que precisa compreender as alternativas.",
        "reader_goal": "Escolher uma abordagem adequada ao próprio contexto.",
        "editorial_content_type": "procedural_decision_guide",
        "reader_start_state": "Leitor sem clareza sobre as abordagens disponíveis.",
        "reader_final_state": "Leitor capaz de escolher e acompanhar uma abordagem.",
        "article_promise": "Comparar alternativas e acompanhar o processo até o resultado.",
        "scope_limit": "O conteúdo termina na confirmação do resultado definido.",
        "requires_method_comparison": True,
        "requires_external_reference_per_method": True,
        "required_methods": ["ambiente interno", "ambiente externo", "ambiente protegido"],
        "required_approach_type": "environment",
    }
    briefing.update(brief_overrides)
    return {
        "name": "Guia de cultivo completo",
        "topic": "guia de cultivo",
        "audience": "Leitores iniciantes",
        "editorial_pipeline_version": "v3",
        "start_immediately": False,
        "briefing": briefing,
    }


def test_project_creation_rejects_word_range_below_structural_minimum():
    with pytest.raises(ValidationError, match="structural minimum"):
        ProjectCreate.model_validate(_project_payload(maximum_words=2200))


def test_project_creation_accepts_explicit_environment_dimension():
    project = ProjectCreate.model_validate(_project_payload(maximum_words=2600))
    assert project.briefing.required_approach_type == "environment"

@pytest.mark.asyncio
async def test_source_discovery_blocks_when_query_budget_cannot_cover_every_node():
    executor = object.__new__(EditorialV3Executor)
    executor.project = SimpleNamespace(id=uuid.uuid4())
    executor.pipeline_run = SimpleNamespace(id=uuid.uuid4())
    executor.runtime = _Runtime()
    executor.search = _Search()
    executor._stage_context = None
    executor._stage = AsyncMock()
    executor._cancellation_boundary = AsyncMock()
    executor._flag = _flag_value

    plan = _plan(task_count=6, maximum_search_queries=5)
    state = V3PipelineState(
        project_id=executor.project.id,
        pipeline_run_id=executor.pipeline_run.id,
        research_plan=plan.model_dump(mode="json"),
    )

    with pytest.raises(V3PipelineBlocked) as exc:
        await executor.source_discovery(state)

    assert exc.value.code == "V3_QUERY_BUDGET_INSUFFICIENT"
    assert executor.search.calls == []
