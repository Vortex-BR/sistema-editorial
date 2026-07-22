import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes import create_project, start_project
from app.db.models import PipelineRunStatus, ProjectStatus
from app.orchestration.state import PipelineState, Stage
from app.orchestration.executor import PipelineExecutor
from app.services.pipeline_control import (
    ALLOWED_RUN_TRANSITIONS,
    CheckpointService,
    EventContext,
    InvalidRunTransition,
    PipelineRunService,
    RetryPolicy,
)
from app.services.pipeline_dispatch import PipelineDispatchService
from app.services.llm_gateway import ProviderError
from app.services.execution_manifest import ExecutionManifestService
from app.services.style_learning import StyleLearningService
from app.services.handoffs import HandoffService
from app.workers.tasks import (
    _project_status_for_run,
    discover_style_patterns,
    run_pipeline,
)


def test_stage_occurrence_identity_distinguishes_cycles_and_run_retries():
    run_id = uuid.uuid4()
    researcher_one = EventContext.for_stage(run_id, "researcher", 0, 0, 1)
    researcher_two = EventContext.for_stage(run_id, "researcher", 1, 0, 1)
    researcher_retry = EventContext.for_stage(run_id, "researcher", 0, 0, 2)
    writer_one = EventContext.for_stage(run_id, "writer", 2, 0, 1)
    writer_two = EventContext.for_stage(run_id, "writer", 2, 1, 1)

    assert researcher_one == EventContext.for_stage(
        run_id, "researcher", 0, 0, 1
    )
    assert researcher_one.research_cycle == 1
    assert researcher_two.research_cycle == 2
    assert writer_one.editor_cycle == 1
    assert writer_two.editor_cycle == 2
    assert len(
        {
            researcher_one.stage_occurrence_id,
            researcher_two.stage_occurrence_id,
            researcher_retry.stage_occurrence_id,
            writer_one.stage_occurrence_id,
            writer_two.stage_occurrence_id,
        }
    ) == 5


def test_gatekeeper_uses_the_research_cycle_completed_by_researcher():
    run_id = uuid.uuid4()
    researcher = EventContext.for_stage(run_id, "researcher", 0, 0, 1)
    gatekeeper = EventContext.for_stage(run_id, "research_gatekeeper", 1, 0, 1)

    assert researcher.research_cycle == gatekeeper.research_cycle == 1
    assert researcher.stage_attempt == gatekeeper.stage_attempt == 1


def test_run_state_machine_has_terminal_states_without_outgoing_edges():
    for status in (
        PipelineRunStatus.needs_review,
        PipelineRunStatus.blocked,
        PipelineRunStatus.failed,
        PipelineRunStatus.cancelled,
        PipelineRunStatus.completed,
        PipelineRunStatus.rejected,
    ):
        assert ALLOWED_RUN_TRANSITIONS[status] == set()
    assert ALLOWED_RUN_TRANSITIONS[PipelineRunStatus.needs_human_approval] == {
        PipelineRunStatus.completed,
        PipelineRunStatus.needs_review,
        PipelineRunStatus.rejected,
    }


def test_human_review_is_not_classified_as_technical_failure():
    assert PipelineRunStatus.needs_review in ALLOWED_RUN_TRANSITIONS[
        PipelineRunStatus.running
    ]
    assert _project_status_for_run(
        PipelineRunStatus.needs_review
    ) == ProjectStatus.needs_review
    assert _project_status_for_run(
        PipelineRunStatus.needs_human_approval
    ) == ProjectStatus.needs_human_approval
    assert _project_status_for_run(PipelineRunStatus.rejected) == ProjectStatus.rejected
    assert _project_status_for_run(PipelineRunStatus.blocked) == ProjectStatus.blocked
    assert _project_status_for_run(PipelineRunStatus.failed) == ProjectStatus.failed


def test_policy_block_is_a_running_terminal_transition_not_a_failure():
    assert PipelineRunStatus.blocked in ALLOWED_RUN_TRANSITIONS[
        PipelineRunStatus.running
    ]
    assert PipelineRunStatus.failed in ALLOWED_RUN_TRANSITIONS[
        PipelineRunStatus.running
    ]
    assert ALLOWED_RUN_TRANSITIONS[PipelineRunStatus.blocked] == set()


def test_automation_cannot_complete_without_the_human_gate():
    assert PipelineRunStatus.completed not in ALLOWED_RUN_TRANSITIONS[
        PipelineRunStatus.running
    ]
    assert PipelineRunStatus.needs_human_approval in ALLOWED_RUN_TRANSITIONS[
        PipelineRunStatus.running
    ]


def test_pending_human_approval_blocks_an_unreviewed_new_run():
    source = inspect.getsource(PipelineRunService.create)

    assert "PipelineRunStatus.needs_human_approval" in source


@pytest.mark.asyncio
async def test_v3_run_is_created_at_the_real_first_stage(monkeypatch):
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status="draft",
        current_stage="planner",
        editorial_pipeline_version="v3",
    )

    class Db:
        def __init__(self):
            self.responses = [project, None, None]
            self.added = []

        async def scalar(self, _query):
            return self.responses.pop(0)

        def add(self, item):
            self.added.append(item)

        async def flush(self):
            return None

    db = Db()
    monkeypatch.setattr(ExecutionManifestService, "create", AsyncMock())
    monkeypatch.setattr(ExecutionManifestService, "required", AsyncMock())

    run, created = await PipelineRunService(db).create(
        project.id,
        "test-v3-initial-stage",
    )

    assert created is True
    assert run.current_stage == "content_contract"
    assert project.current_stage == "content_contract"
    assert db.added[-1].stage == "content_contract"


def test_content_runs_do_not_launch_run_scoped_style_learning_in_parallel():
    assert "discover_style_patterns" not in inspect.getsource(create_project)
    assert "discover_style_patterns" not in inspect.getsource(start_project)


def test_completed_run_cannot_return_to_running():
    current = PipelineRunStatus.completed
    target = PipelineRunStatus.running

    with pytest.raises(InvalidRunTransition):
        if target not in ALLOWED_RUN_TRANSITIONS[current]:
            raise InvalidRunTransition(f"{current.value} -> {target.value}")


def test_retry_policy_separates_validation_from_temporary_failures():
    validation = RetryPolicy.classify(ValueError("invalid contract"), attempt=1)
    temporary = RetryPolicy.classify(ConnectionError("provider unavailable"), attempt=1)
    exhausted = RetryPolicy.classify(ConnectionError("provider unavailable"), attempt=4)

    assert validation.retryable is False
    assert temporary.retryable is True
    assert temporary.delay_seconds == 15
    assert exhausted.retryable is False


def test_retry_policy_reschedules_transient_provider_outages():
    transient = RetryPolicy.classify(
        ProviderError(
            "unavailable",
            provider="tavily",
            model="search",
            retryable=False,
            retry_after=45,
        ),
        attempt=1,
    )
    invalid = RetryPolicy.classify(
        ProviderError(
            "invalid_request",
            provider="tavily",
            model="search",
        ),
        attempt=1,
    )

    assert transient.retryable is True
    assert transient.delay_seconds == 45
    assert invalid.retryable is False


def test_pipeline_task_does_not_use_celery_autoretry():
    assert getattr(run_pipeline, "autoretry_for", None) is None
    assert getattr(discover_style_patterns, "autoretry_for", None) is None


def test_worker_claim_requires_dispatch_token():
    parameters = inspect.signature(PipelineDispatchService.claim_for_worker).parameters
    assert "dispatch_token" in parameters


def test_public_transition_requires_run_identity_not_loaded_entity():
    parameters = inspect.signature(PipelineRunService.transition).parameters

    assert "run_id" in parameters
    assert "run" not in parameters
    assert "expected_lease_owner" in parameters
    assert "expected_lock_version" in parameters


def test_checkpoint_state_carries_pipeline_run_identity():
    import uuid

    run_id = uuid.uuid4()
    state = PipelineState(
        project_id=uuid.uuid4(),
        pipeline_run_id=run_id,
        stage=Stage.researcher,
    )

    restored = PipelineState.model_validate(state.model_dump(mode="json"))

    assert restored.pipeline_run_id == run_id
    assert restored.stage == Stage.researcher


def test_rewrite_resume_uses_draft_restored_from_checkpoint():
    import uuid

    block_id = uuid.uuid4()
    persisted_draft = {
        "title": "Persisted draft",
        "blocks": [{"block_id": str(block_id), "sentences": []}],
    }
    restored_state = PipelineState(
        project_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
        stage=Stage.writer,
        draft=persisted_draft,
        rewrite_block_ids=[block_id],
    )

    prior = PipelineExecutor._prior_draft(restored_state)

    assert prior is restored_state.draft
    assert prior == persisted_draft


def test_initial_writer_cycle_does_not_reuse_a_prior_draft():
    import uuid

    state = PipelineState(
        project_id=uuid.uuid4(),
        draft={"title": "Draft from unrelated state"},
    )

    assert PipelineExecutor._prior_draft(state) is None


def _research_executor():
    executor = PipelineExecutor.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        topic="germinação de sementes de cannabis",
        niche="cultivo de cannabis",
        language="pt-BR",
    )
    return executor


def test_research_query_keeps_topic_scope_and_gate_instructions():
    question_id = uuid.uuid4()
    state = PipelineState(
        project_id=uuid.uuid4(),
        research_cycle=1,
        plan={
            "semantic_keywords": ["profundidade", "substrato"],
            "questions": [],
        },
        research_audit={"instructions": ["Resolver a faixa de profundidade"]},
    )
    question = {
        "id": str(question_id),
        "question": "Qual é a profundidade recomendada?",
        "semantic_terms": ["semente de cannabis"],
    }

    query = _research_executor()._research_query(state, question)

    assert "germinação de sementes de cannabis" in query
    assert "cultivo de cannabis" in query
    assert "pt-BR" in query
    assert question["question"] in query
    assert "semente de cannabis" in query
    assert "Resolver a faixa de profundidade" in query
    assert "fontes primárias" in query


def test_incremental_research_targets_missing_and_conflict_questions():
    missing_id, conflict_id, ignored_id = (uuid.uuid4() for _ in range(3))
    conflict_fact_id = uuid.uuid4()
    questions = [
        {"id": str(missing_id), "question": "Pergunta ausente", "priority": 1},
        {"id": str(conflict_id), "question": "Pergunta conflitante", "priority": 2},
        {"id": str(ignored_id), "question": "Pergunta coberta", "priority": 3},
    ]
    state = PipelineState(
        project_id=uuid.uuid4(),
        plan={"questions": questions, "semantic_keywords": []},
        facts=[
            {
                "id": str(conflict_fact_id),
                "research_question_id": str(conflict_id),
            }
        ],
        research_audit={
            "missing_questions": ["Pergunta ausente"],
            "unresolved_conflict_fact_ids": {
                "depth": [str(conflict_fact_id)]
            },
        },
    )

    targets = _research_executor()._research_targets(state)

    assert [item["id"] for item in targets] == [
        str(missing_id),
        str(conflict_id),
    ]


def test_diversity_only_cycle_targets_questions_with_fewest_sources():
    low_first, high, low_second = (uuid.uuid4() for _ in range(3))
    questions = [
        {"id": str(low_second), "question": "Low second", "priority": 3},
        {"id": str(high), "question": "High", "priority": 1},
        {"id": str(low_first), "question": "Low first", "priority": 1},
    ]
    state = PipelineState(
        project_id=uuid.uuid4(),
        plan={"questions": questions, "semantic_keywords": []},
        research_audit={
            "missing_questions": [],
            "unresolved_conflict_fact_ids": {},
            "distinct_source_count": 4,
            "minimum_distinct_sources": 5,
            "selected_source_count_by_question": {
                str(low_first): 0,
                str(high): 2,
                str(low_second): 0,
            },
        },
    )

    targets = _research_executor()._research_targets(state)

    assert [item["id"] for item in targets] == [
        str(low_first),
        str(low_second),
    ]


def test_human_revision_instructions_reach_agents_without_becoming_evidence():
    executor = object.__new__(PipelineExecutor)
    executor.pipeline_run = type(
        "Run",
        (),
        {
            "metadata_json": {
                "human_revision": {
                    "reviewer": "Editora Ana",
                    "instructions": "Reescrever a abertura.",
                }
            }
        },
    )()

    prompt = executor._revision_prompt("Tarefa original")

    assert "Editora Ana" in prompt
    assert "Reescrever a abertura." in prompt
    assert "não são evidência factual" in prompt
    assert prompt.endswith("Tarefa original")


def test_checkpoint_keys_distinguish_research_and_editor_cycles():
    research_one = CheckpointService.idempotency_key(
        "researcher", 1, "1.0", {"research_cycle": 1, "editor_cycle": 0}
    )
    research_two = CheckpointService.idempotency_key(
        "researcher", 1, "1.0", {"research_cycle": 2, "editor_cycle": 0}
    )
    writer_one = CheckpointService.idempotency_key(
        "writer", 1, "1.0", {"research_cycle": 2, "editor_cycle": 1}
    )
    writer_two = CheckpointService.idempotency_key(
        "writer", 1, "1.0", {"research_cycle": 2, "editor_cycle": 2}
    )

    assert research_one != research_two
    assert writer_one != writer_two
    assert "research-cycle-2" in research_two
    assert "editor-cycle-2" in writer_two


def test_handoff_keys_distinguish_cycles_and_producer_calls():
    import uuid

    first_producer = uuid.uuid4()
    second_producer = uuid.uuid4()
    research_one = HandoffService.idempotency_key(
        "researcher", "research_gatekeeper", 1, 1, 0, None
    )
    research_two = HandoffService.idempotency_key(
        "researcher", "research_gatekeeper", 1, 2, 0, None
    )
    editor_one = HandoffService.idempotency_key(
        "editor", "writer", 1, 2, 1, first_producer
    )
    editor_two = HandoffService.idempotency_key(
        "editor", "writer", 1, 2, 2, second_producer
    )

    assert research_one != research_two
    assert editor_one != editor_two
    assert "research-cycle-2" in research_two
    assert str(second_producer) in editor_two


def test_historical_migrations_are_frozen_and_do_not_import_current_models():
    root = Path(__file__).parents[1] / "alembic" / "versions"
    for filename in ("0001_initial_schema.py", "0002_superior_skills_memory.py"):
        source = (root / filename).read_text(encoding="utf-8")
        assert "Base.metadata" not in source
        assert "app.db.models" not in source
        assert "CREATE TABLE" in source


@pytest.mark.asyncio
async def test_style_discovery_fails_closed_when_redis_lock_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent_runtime.CredentialVault",
        lambda: object(),
    )
    class FakeDb:
        async def get(self, _model, _identifier):
            return object()

    class BrokenRedis:
        async def set(self, *_args, **_kwargs):
            raise ConnectionError("redis unavailable")

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "app.services.style_learning.Redis.from_url",
        lambda *_args, **_kwargs: BrokenRedis(),
    )

    result = await StyleLearningService(FakeDb()).discover(__import__("uuid").uuid4())

    assert result == {"status": "redis-unavailable-lock-required", "patterns": 0}
