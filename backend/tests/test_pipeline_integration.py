import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    AgentRun,
    Article,
    ArticleBlock,
    ArticleVersion,
    FactLedger,
    ExecutionManifest,
    PipelineDispatchStatus,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    PipelineStateTransition,
    Project,
    ProjectStatus,
    ResearchPlan,
    ResearchQuestion,
    Source,
    SourceSnapshot,
    TriggerType,
    ModelRoute,
)
from app.db.session import register_session_sanitization_guards
from app.core.config import settings
from app.services.agent_context import ComposedContext
from app.services.agent_runtime import AgentRuntime
from app.services.llm_gateway import ModelTarget, ProviderError
from app.services.content_versioning import ContentVersionService
from app.services.handoffs import HandoffService
from app.services.pipeline_control import (
    CheckpointService,
    EventContext,
    EventService,
    PipelineRunBusy,
    PipelineRunService,
    PipelineRunVersionConflict,
)
from app.services.research_coverage import ResearchCoverageService

_PROTECTED_DATABASE_NAMES = {"postgres", "seo", "seo_ledger", "template0", "template1"}
_TEST_DATABASE_MARKER = re.compile(r"(?:^|[_-])(?:test|testing|integration)(?:$|[_-])")


def _database_target(database_url: str) -> tuple[str, str, int, str]:
    url = make_url(database_url)
    return (
        url.get_backend_name(),
        (url.host or "").lower(),
        url.port or 5432,
        (url.database or "").lower(),
    )


def _validated_test_database_url(
    test_database_url: str | None, application_database_url: str | None
) -> str | None:
    if not test_database_url:
        return None
    try:
        url = make_url(test_database_url)
    except ArgumentError as exc:
        raise RuntimeError("TEST_DATABASE_URL is not a valid database URL") from exc
    database_name = (url.database or "").lower()
    if url.drivername != "postgresql+asyncpg":
        raise RuntimeError(
            "TEST_DATABASE_URL must use PostgreSQL with the asyncpg driver"
        )
    if database_name in _PROTECTED_DATABASE_NAMES:
        raise RuntimeError(
            f"Refusing to run integration tests against protected database "
            f"{database_name!r}"
        )
    if not _TEST_DATABASE_MARKER.search(database_name):
        raise RuntimeError(
            "Refusing to run integration tests because the database name does not "
            "clearly identify a test environment"
        )
    if application_database_url:
        try:
            same_target = _database_target(test_database_url) == _database_target(
                application_database_url
            )
        except ArgumentError as exc:
            raise RuntimeError("DATABASE_URL is not a valid database URL") from exc
        if same_target:
            raise RuntimeError(
                "Refusing to run integration tests because TEST_DATABASE_URL and "
                "DATABASE_URL target the same database"
            )
    return test_database_url


TEST_DATABASE_URL = _validated_test_database_url(
    os.getenv("TEST_DATABASE_URL"), os.getenv("DATABASE_URL")
)

pytestmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="TEST_DATABASE_URL is required and must target a dedicated test database",
)


@pytest_asyncio.fixture
async def sessions():
    register_session_sanitization_guards()
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def test_credential_master_key(monkeypatch):
    master_key = Fernet.generate_key().decode()
    with monkeypatch.context() as patch:
        patch.setenv("CREDENTIAL_MASTER_KEY", master_key)
        patch.setattr(settings, "credential_master_key", master_key)
        yield


async def _project(session):
    project = Project(
        name="Integration project",
        topic="isolated pipeline runs",
        search_intent="informational",
        audience="engineering teams",
        status="draft",
    )
    session.add(project)
    await session.commit()
    return project


class _ResearcherProbeOutput(BaseModel):
    text: str
    provider_literal: str
    details: dict[str, object]


def _assert_no_real_nul(value) -> None:
    if isinstance(value, str):
        assert "\x00" not in value
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_no_real_nul(key)
            _assert_no_real_nul(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _assert_no_real_nul(item)


async def _cleanup_researcher_probe(sessions, project_id, route_id) -> None:
    async with sessions() as cleanup_session:
        await cleanup_session.execute(delete(Project).where(Project.id == project_id))
        if route_id is not None:
            await cleanup_session.execute(
                delete(ModelRoute).where(ModelRoute.id == route_id)
            )
        await cleanup_session.commit()


@pytest.mark.asyncio
async def test_research_coverage_is_persisted_per_question_and_run(sessions):
    async with sessions() as session:
        project = await _project(session)
        project_id = project.id
        run, _ = await PipelineRunService(session).create(
            project_id, f"coverage-{uuid.uuid4()}"
        )
        plan = ResearchPlan(
            project_id=project_id,
            pipeline_run_id=run.id,
            idempotency_key="coverage-plan",
            version=1,
            status="approved",
            rationale="Deterministic coverage",
            semantic_keywords=[],
            competitor_angles=[],
            content_gaps=[],
        )
        session.add(plan)
        await session.flush()
        questions = [
            ResearchQuestion(
                plan_id=plan.id,
                question=f"Priority question {index} requires evidence",
                priority=index,
                expected_source_types=["scientific"],
                coverage_status="uncovered",
            )
            for index in range(1, 4)
        ]
        session.add_all(questions)
        await session.flush()
        sources = [
            Source(
                canonical_url=f"https://coverage-{uuid.uuid4()}.example/source",
                title=f"Coverage source {index}",
                publisher="Test publisher",
                source_type="scientific",
                content_hash=uuid.uuid4().hex,
                snapshot_text="Persisted source content for coverage testing.",
                reliability_score=0.9,
                metadata_json={},
            )
            for index in range(1, 4)
        ]
        session.add_all(sources)
        await session.flush()
        source_ids = [source.id for source in sources]
        snapshots = [
            SourceSnapshot(
                source_id=source.id,
                pipeline_run_id=run.id,
                content_hash=source.content_hash,
                snapshot_text=source.snapshot_text,
                accessed_at=source.accessed_at,
                title=source.title,
                author=None,
                publisher=source.publisher,
                published_at=source.published_at,
                canonical_url=source.canonical_url,
                domain="example",
                source_type=source.source_type,
                reliability_score=source.reliability_score,
                extraction_method="integration_test",
                metadata_json={},
            )
            for source in sources
        ]
        session.add_all(snapshots)
        await session.flush()
        facts = [
            FactLedger(
                project_id=project_id,
                pipeline_run_id=run.id,
                research_question_id=questions[index].id,
                source_id=sources[index].id,
                source_snapshot_id=snapshots[index].id,
                claim_text=f"Supported fact for priority question {index + 1}",
                exact_quote="Exact supporting quotation",
                source_locator="section 1",
                extraction_method="test",
                confidence_score=0.9,
                approved=False,
            )
            for index in range(2)
        ]
        session.add_all(facts)
        await session.flush()
        service = ResearchCoverageService(session, project_id, run.id)

        partial = await service.evaluate(
            [fact.id for fact in facts], minimum_distinct_sources=2
        )
        partial.persist(approved=False, reviewer_run_id=uuid.uuid4())
        await session.commit()

        assert partial.missing_questions == (questions[2].question,)
        assert [question.coverage_status for question in questions] == [
            "covered",
            "covered",
            "uncovered",
        ]
        assert not any(fact.approved for fact in facts)

        final_fact = FactLedger(
            project_id=project_id,
            pipeline_run_id=run.id,
            research_question_id=questions[2].id,
            source_id=sources[2].id,
            source_snapshot_id=snapshots[2].id,
            claim_text="Supported fact for priority question 3",
            exact_quote="Another exact supporting quotation",
            source_locator="section 2",
            extraction_method="test",
            confidence_score=0.9,
            approved=False,
        )
        session.add(final_fact)
        await session.flush()
        complete = await service.evaluate(
            [*[fact.id for fact in facts], final_fact.id],
            minimum_distinct_sources=3,
        )
        reviewer_run_id = uuid.uuid4()
        complete.persist(approved=True, reviewer_run_id=reviewer_run_id)
        await session.commit()

        assert complete.coverage_complete is True
        assert all(question.coverage_status == "covered" for question in questions)
        assert all(fact.approved for fact in [*facts, final_fact])
        assert all(
            fact.approved_by_run_id == reviewer_run_id
            for fact in [*facts, final_fact]
        )

        await session.execute(delete(Project).where(Project.id == project_id))
        await session.commit()
        await session.execute(delete(Source).where(Source.id.in_(source_ids)))
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "initial_status", [PipelineRunStatus.queued, PipelineRunStatus.waiting_retry]
)
async def test_pending_cancellation_is_durable_auditable_and_idempotent(
    sessions, initial_status
):
    async with sessions() as session:
        project = await _project(session)
        project_id = project.id
        service = PipelineRunService(session)
        run, _ = await service.create(
            project_id, f"cancel-{initial_status.value}-{uuid.uuid4()}"
        )
        project.status = ProjectStatus.queued
        if initial_status == PipelineRunStatus.waiting_retry:
            run = await service.transition(
                run.id, PipelineRunStatus.running, origin="test"
            )
            run = await service.transition(
                run.id, PipelineRunStatus.waiting_retry, origin="test"
            )
            project.status = ProjectStatus.running
        now = datetime.now(timezone.utc)
        run.retryable = True
        run.next_retry_at = now + timedelta(minutes=5)
        run.dispatch_token = uuid.uuid4()
        run.dispatch_status = PipelineDispatchStatus.claimed
        run.dispatch_claimed_by = "beat-test"
        run.dispatch_claimed_at = now
        run.dispatch_expires_at = now + timedelta(minutes=1)
        run.dispatch_not_before = now + timedelta(minutes=1)
        run.celery_task_id = "task-test"
        await session.flush()

        cancelled = await service.request_cancellation(run.id, origin="admin.api")
        requested_at = cancelled.cancellation_requested_at
        cancelled_again = await service.request_cancellation(
            run.id, origin="admin.api"
        )
        await session.commit()

        assert cancelled_again.status == PipelineRunStatus.cancelled
        assert cancelled_again.cancellation_requested_at == requested_at
        assert cancelled_again.retryable is False
        assert cancelled_again.next_retry_at is None
        assert cancelled_again.dispatch_token is None
        assert cancelled_again.dispatch_status is None
        assert cancelled_again.dispatch_not_before is None
        assert cancelled_again.celery_task_id is None
        transitions = (
            await session.scalars(
                select(PipelineStateTransition).where(
                    PipelineStateTransition.pipeline_run_id == run.id,
                    PipelineStateTransition.to_status
                    == PipelineRunStatus.cancelled.value,
                )
            )
        ).all()
        events = (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == run.id,
                    PipelineEvent.event_type == "pipeline.cancelled",
                )
            )
        ).all()
        assert len(transitions) == 1
        assert len(events) == 1
        assert project.status == ProjectStatus.draft

        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_running_cancellation_is_honored_without_affecting_another_run(sessions):
    async with sessions() as session:
        selected_project = await _project(session)
        other_project = await _project(session)
        selected_project_id = selected_project.id
        other_project_id = other_project.id
        service = PipelineRunService(session)
        selected_project.status = ProjectStatus.completed
        selected, _ = await service.create(
            selected_project_id, f"cancel-running-{uuid.uuid4()}"
        )
        other, _ = await service.create(
            other_project_id, f"continue-running-{uuid.uuid4()}"
        )
        selected = await service.claim(selected.id, "worker-selected")
        selected = await service.transition(
            selected.id,
            PipelineRunStatus.running,
            origin="test",
            expected_lease_owner="worker-selected",
        )
        selected_project.status = ProjectStatus.running
        other = await service.transition(
            other.id, PipelineRunStatus.running, origin="test"
        )
        requested = await service.request_cancellation(
            selected.id, origin="admin.api"
        )
        await session.commit()

        assert requested.status == PipelineRunStatus.running
        assert requested.cancellation_requested_at is not None
        honored = await service.honor_cancellation(
            selected.id,
            origin="orchestrator.safe-boundary",
            expected_lease_owner="worker-selected",
        )
        await session.commit()

        assert honored is not None
        assert honored.status == PipelineRunStatus.cancelled
        assert honored.lease_owner is None
        assert selected_project.status == ProjectStatus.completed
        current_other = await session.get(PipelineRun, other.id)
        assert current_other.status == PipelineRunStatus.running
        event_types = set(
            await session.scalars(
                select(PipelineEvent.event_type).where(
                    PipelineEvent.pipeline_run_id == selected.id
                )
            )
        )
        assert {
            "pipeline.cancellation_requested",
            "pipeline.cancelled",
        } <= event_types

        await session.delete(selected_project)
        await session.delete(other_project)
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.usefixtures("test_credential_master_key")
async def test_researcher_agent_run_is_sanitized_before_query_autoflush(
    sessions, monkeypatch
):
    project_id = None
    route_id_to_delete = None
    run_id = uuid.uuid4()
    raw_provider_json = json.dumps(
        {
            "text": "provider\x00value",
            "provider_literal": r"left\u0000right and left\x00right",
            "details": {
                "nested": ["outer\x00inner", {"unchanged": 7}],
                "literal": r"remove\u0000external",
            },
        }
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [
                    {"content": {"parts": [{"text": raw_provider_json}]}}
                ],
                "usageMetadata": {
                    "promptTokenCount": 1,
                    "candidatesTokenCount": 1,
                },
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.llm_gateway.httpx.AsyncClient", Client)

    try:
        async with sessions() as session:
            project = await _project(session)
            project_id = project.id
            pipeline_run, _ = await PipelineRunService(session).create(
                project.id, f"nul-researcher-{uuid.uuid4()}"
            )
            route = await session.scalar(
                select(ModelRoute).where(ModelRoute.agent_role == "researcher")
            )
            if route is None:
                route = ModelRoute(
                    agent_role="researcher",
                    primary_provider="gemini",
                    primary_model="test-model",
                    parameters={},
                )
                session.add(route)
                await session.flush()
                route_id_to_delete = route.id
            await session.commit()

            runtime = AgentRuntime(session)

            async def target(*_args, **_kwargs):
                return ModelTarget("gemini", "test-model", "not-a-real-key")

            async def compose(*_args, **_kwargs):
                return ComposedContext(
                    prompt="research prompt",
                    metadata={"nested": ["context\x00value"]},
                    superior_fragment="",
                )

            monkeypatch.setattr(runtime, "_target", target)
            monkeypatch.setattr(runtime.context, "compose", compose)

            result = await runtime.call(
                project.id,
                "researcher",
                run_id,
                {
                    "sources": [{"content": "before\x00after"}],
                    "metadata": {"nested": ["metadata\x00value", {"kept": 3}]},
                    "internal_literal": r"keep\u0000literal and keep\x00literal",
                },
                "prompt",
                _ResearcherProbeOutput,
                pipeline_run_id=pipeline_run.id,
            )
            await session.commit()

            # EventService.append() executes SELECT Project ... FOR UPDATE after
            # session.add(AgentRun), causing the production-path autoflush.

        async with sessions() as read_session:
            stored = await read_session.scalar(
                select(AgentRun).where(AgentRun.id == run_id)
            )
            assert stored is not None
            assert stored.status.value == "succeeded"
            assert stored.input_json["sources"][0]["content"] == "beforeafter"
            assert stored.input_json["metadata"] == {
                "nested": ["metadatavalue", {"kept": 3}]
            }
            assert stored.input_json["_superior_context"]["nested"] == [
                "contextvalue"
            ]
            assert stored.input_json["internal_literal"] == (
                r"keep\u0000literal and keep\x00literal"
            )
            assert stored.output_json == {
                "text": "providervalue",
                "provider_literal": "leftright and leftright",
                "details": {
                    "nested": ["outerinner", {"unchanged": 7}],
                    "literal": "removeexternal",
                },
            }
            assert _ResearcherProbeOutput.model_validate(stored.output_json)
            assert result == stored.output_json
            assert stored.agent_role == "researcher"
            assert stored.provider == "gemini"
            assert stored.model == "test-model"
            assert stored.decision is None
            assert stored.feedback is None
            assert stored.error is None
            for text_value in (
                stored.idempotency_key,
                stored.agent_role,
                stored.provider,
                stored.model,
                stored.error,
            ):
                _assert_no_real_nul(text_value)
            _assert_no_real_nul(stored.input_json)
            _assert_no_real_nul(stored.output_json)
            events = (
                await read_session.scalars(
                    select(PipelineEvent).where(
                        PipelineEvent.pipeline_run_id == stored.pipeline_run_id
                    )
                )
            ).all()
            assert {event.event_type for event in events} >= {
                "agent.started",
                "agent.completed",
            }
            for event in events:
                _assert_no_real_nul(event.event_type)
                _assert_no_real_nul(event.stage)
                _assert_no_real_nul(event.idempotency_key)
                _assert_no_real_nul(event.payload)

            # A subsequent query proves that the real connection/session remains usable.
            assert await read_session.scalar(
                select(Project.id).where(Project.id == project_id)
            )
    finally:
        if project_id is not None:
            await _cleanup_researcher_probe(
                sessions, project_id, route_id_to_delete
            )

    async with sessions() as verification_session:
        assert await verification_session.get(Project, project_id) is None
        assert await verification_session.get(AgentRun, run_id) is None
        if route_id_to_delete is not None:
            assert await verification_session.get(ModelRoute, route_id_to_delete) is None


@pytest.mark.asyncio
async def test_provider_failure_persists_safe_diagnostics(
    sessions, monkeypatch, test_credential_master_key
):
    project_id = None
    route_id_to_delete = None
    agent_run_id = uuid.uuid4()
    try:
        async with sessions() as session:
            project = await _project(session)
            project_id = project.id
            pipeline_run, _ = await PipelineRunService(session).create(
                project.id, f"provider-diagnostics-{uuid.uuid4()}"
            )
            route = await session.scalar(
                select(ModelRoute).where(ModelRoute.agent_role == "researcher")
            )
            if route is None:
                route = ModelRoute(
                    agent_role="researcher",
                    primary_provider="gemini",
                    primary_model="gemini-3.5-flash",
                    parameters={},
                )
                session.add(route)
                await session.flush()
                route_id_to_delete = route.id
            await session.commit()

            runtime = AgentRuntime(session)

            async def target(*_args, **_kwargs):
                return ModelTarget("gemini", "gemini-3.5-flash", "not-a-real-key")

            async def compose(*_args, **_kwargs):
                return ComposedContext(
                    prompt="research prompt",
                    metadata={},
                    superior_fragment="",
                )

            async def fail_generation(*_args, **_kwargs):
                raise ProviderError(
                    category="model_not_found",
                    provider="gemini",
                    model="gemini-3.5-flash",
                    http_status=404,
                    retryable=False,
                    latency_ms=17,
                    attempts=1,
                )

            monkeypatch.setattr(runtime, "_target", target)
            monkeypatch.setattr(runtime.context, "compose", compose)
            monkeypatch.setattr(runtime.gateway, "generate_structured", fail_generation)

            with pytest.raises(ProviderError):
                await runtime.call(
                    project.id,
                    "researcher",
                    agent_run_id,
                    {"sources": []},
                    "prompt",
                    _ResearcherProbeOutput,
                    pipeline_run_id=pipeline_run.id,
                )

        async with sessions() as read_session:
            stored = await read_session.get(AgentRun, agent_run_id)
            assert stored is not None
            assert stored.status.value == "failed"
            assert stored.error == "O modelo configurado não foi encontrado pelo provedor."
            assert stored.error_code == "provider_model_not_found"
            assert stored.error_category == "model_not_found"
            assert stored.http_status == 404
            assert stored.retryable is False
            assert stored.provider == "gemini"
            assert stored.model == "gemini-3.5-flash"
            assert stored.latency_ms == 17
            assert stored.correlation_id
            assert "not-a-real-key" not in json.dumps(stored.input_json)
    finally:
        if project_id is not None:
            await _cleanup_researcher_probe(
                sessions, project_id, route_id_to_delete
            )


@pytest.mark.asyncio
async def test_two_runs_are_isolated_and_repeated_trigger_is_idempotent(sessions):
    async with sessions() as session:
        project = await _project(session)
        service = PipelineRunService(session)
        first, created = await service.create(project.id, "request-1", TriggerType.api)
        first_manifest = await session.scalar(
            select(ExecutionManifest).where(
                ExecutionManifest.pipeline_run_id == first.id
            )
        )
        assert first_manifest is not None
        duplicate, duplicate_created = await service.create(
            project.id, "request-1", TriggerType.api
        )
        assert created is True
        assert duplicate_created is False
        assert duplicate.id == first.id
        first = await service.transition(
            first.id, PipelineRunStatus.running, origin="test"
        )
        first = await service.transition(
            first.id, PipelineRunStatus.needs_human_approval, origin="test"
        )
        blocked, blocked_created = await service.create(
            project.id, "request-2", TriggerType.api
        )
        assert blocked_created is False
        assert blocked.id == first.id
        first = await service.transition(
            first.id, PipelineRunStatus.completed, origin="test-human-review"
        )
        second, second_created = await service.create(
            project.id, "request-2", TriggerType.api
        )
        second_manifest = await session.scalar(
            select(ExecutionManifest).where(
                ExecutionManifest.pipeline_run_id == second.id
            )
        )
        await session.commit()
        assert second_created is True
        assert second.id != first.id
        assert second_manifest is not None
        assert second_manifest.id != first_manifest.id
        await session.execute(delete(PipelineRun).where(PipelineRun.project_id == project.id))
        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_transition_locks_run_and_rejects_stale_version(sessions):
    async with sessions() as stale_session:
        project = await _project(stale_session)
        stale_service = PipelineRunService(stale_session)
        stale_run, _ = await stale_service.create(project.id, "version-guard")
        await stale_session.commit()
        run_id = stale_run.id
        stale_version = stale_run.lock_version

        async with sessions() as writer_session:
            current = await PipelineRunService(writer_session).transition(
                run_id,
                PipelineRunStatus.running,
                origin="current-worker",
                expected_lock_version=stale_version,
            )
            assert current.lock_version == stale_version + 1

            # The second transition must wait for the first transaction's row
            # lock. Once released, it must refresh the identity-mapped object
            # before checking the version (expire_on_commit=False).
            conflicting_transition = asyncio.create_task(
                stale_service.transition(
                    run_id,
                    PipelineRunStatus.needs_human_approval,
                    origin="stale-worker",
                    expected_lock_version=stale_version,
                )
            )
            await asyncio.sleep(0.1)
            assert conflicting_transition.done() is False
            await writer_session.commit()

        with pytest.raises(PipelineRunVersionConflict):
            await conflicting_transition
        await stale_session.rollback()

        current = await stale_session.get(PipelineRun, run_id)
        assert current.status == PipelineRunStatus.running
        assert current.lock_version == stale_version + 1
        await stale_session.delete(project)
        await stale_session.commit()


@pytest.mark.asyncio
async def test_transition_rejects_worker_without_current_lease(sessions):
    async with sessions() as session:
        project = await _project(session)
        service = PipelineRunService(session)
        run, _ = await service.create(project.id, "lease-guard")
        run = await service.claim(run.id, "current-worker")
        await session.commit()
        run_id = run.id

        with pytest.raises(PipelineRunBusy):
            await service.transition(
                run_id,
                PipelineRunStatus.running,
                origin="stale-worker",
                expected_lease_owner="old-worker",
                expected_lock_version=run.lock_version,
            )
        await session.rollback()

        current = await session.get(PipelineRun, run_id)
        assert current.status == PipelineRunStatus.queued
        assert current.lock_version == 0
        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_needs_review_is_terminal_without_technical_failure_fields(sessions):
    async with sessions() as session:
        project = await _project(session)
        service = PipelineRunService(session)
        run, _ = await service.create(project.id, "human-review")
        run = await service.claim(run.id, "review-worker")
        run = await service.transition(
            run.id,
            PipelineRunStatus.running,
            origin="test",
            expected_lease_owner="review-worker",
            expected_lock_version=run.lock_version,
        )
        run = await service.transition(
            run.id,
            PipelineRunStatus.needs_review,
            origin="orchestrator",
            reason="Editorial similarity requires human decision",
            stage="needs_review",
            expected_lease_owner="review-worker",
            expected_lock_version=run.lock_version,
        )
        await session.commit()

        assert run.status == PipelineRunStatus.needs_review
        assert run.finished_at is not None
        assert run.failed_at is None
        assert run.error_code is None
        assert run.error_message is None
        assert run.retryable is False
        assert run.next_retry_at is None
        transition = await session.scalar(
            select(PipelineStateTransition)
            .where(
                PipelineStateTransition.pipeline_run_id == run.id,
                PipelineStateTransition.to_status == "needs_review",
            )
            .order_by(PipelineStateTransition.created_at.desc())
        )
        assert transition.reason == "Editorial similarity requires human decision"

        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_events_receive_distinct_sequences(sessions):
    async with sessions() as session:
        project = await _project(session)
        run, _ = await PipelineRunService(session).create(project.id, "event-run")
        await session.commit()

    async def append(index):
        async with sessions() as session:
            await EventService(session).append(
                project.id,
                run.id,
                "integration.event",
                "planner",
                {"index": index},
                idempotency_key=f"event:{index}",
            )
            await session.commit()

    await asyncio.gather(*(append(index) for index in range(4)))
    async with sessions() as session:
        events = (
            await session.scalars(
                select(PipelineEvent).where(PipelineEvent.pipeline_run_id == run.id)
            )
        ).all()
        assert len({event.sequence for event in events}) == 4
        await session.execute(delete(PipelineRun).where(PipelineRun.id == run.id))
        project = await session.get(Project, project.id)
        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_event_sequence_refreshes_a_project_cached_before_lock(sessions):
    async with sessions() as session:
        project = await _project(session)
        run, _ = await PipelineRunService(session).create(
            project.id, "stale-event-sequence"
        )
        await session.commit()
        project_id, run_id = project.id, run.id

    async with sessions() as stale_session:
        stale_project = await stale_session.get(Project, project_id)
        stale_sequence = stale_project.event_sequence

        async with sessions() as concurrent_session:
            first = await EventService(concurrent_session).append(
                project_id,
                run_id,
                "integration.concurrent_event",
                "skill_curator",
                {},
                idempotency_key="concurrent-event",
            )
            await concurrent_session.commit()

        assert first.sequence == stale_sequence + 1
        assert stale_project.event_sequence == stale_sequence

        second = await EventService(stale_session).append(
            project_id,
            run_id,
            "integration.stale_session_event",
            "planner",
            {},
            idempotency_key="stale-session-event",
        )
        await stale_session.commit()

        assert second.sequence == first.sequence + 1

    async with sessions() as session:
        rows = (
            await session.scalars(
                select(PipelineEvent)
                .where(
                    PipelineEvent.pipeline_run_id == run_id,
                    PipelineEvent.event_type.in_(
                        {
                            "integration.concurrent_event",
                            "integration.stale_session_event",
                        }
                    ),
                )
                .order_by(PipelineEvent.sequence)
            )
        ).all()
        assert [row.sequence for row in rows] == [
            stale_sequence + 1,
            stale_sequence + 2,
        ]
        await session.execute(delete(PipelineRun).where(PipelineRun.id == run_id))
        project = await session.get(Project, project_id)
        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_stage_events_are_cycle_aware_idempotent_and_auditable(sessions):
    async with sessions() as session:
        project = await _project(session)
        run, _ = await PipelineRunService(session).create(project.id, "audit-events")
        agent_run = AgentRun(
            project_id=project.id,
            pipeline_run_id=run.id,
            idempotency_key="writer:audit-agent",
            agent_role="writer",
            attempt=1,
            status="running",
            input_json={},
        )
        session.add(agent_run)
        await session.flush()
        events = EventService(session)
        contexts = [
            EventContext.for_stage(run.id, "researcher", 0, 0, 1),
            EventContext.for_stage(run.id, "researcher", 1, 0, 1),
            EventContext.for_stage(run.id, "writer", 2, 0, 1),
            EventContext.for_stage(run.id, "writer", 2, 1, 1),
            EventContext.for_stage(run.id, "writer", 2, 0, 2),
        ]
        stages = ["researcher", "researcher", "writer", "writer", "writer"]
        created = []
        for stage, context in zip(stages, contexts, strict=True):
            created.append(
                await events.append(
                    project.id,
                    run.id,
                    "stage.started",
                    stage,
                    {},
                    idempotency_key=context.event_key("stage.started"),
                    context=context.with_agent(agent_run.id),
                )
            )
        duplicate = await events.append(
            project.id,
            run.id,
            "stage.started",
            "researcher",
            {},
            idempotency_key=contexts[0].event_key("stage.started"),
            context=contexts[0],
        )
        legacy = PipelineEvent(
            project_id=project.id,
            pipeline_run_id=run.id,
            sequence=project.event_sequence + 1,
            event_type="legacy.event",
            stage="planner",
            payload={},
            idempotency_key="legacy-event",
        )
        project.event_sequence += 1
        session.add(legacy)
        await session.commit()

        assert duplicate.id == created[0].id
        assert len({item.stage_occurrence_id for item in created}) == 5
        assert created[0].research_cycle == 1
        assert created[1].research_cycle == 2
        assert created[2].editor_cycle == 1
        assert created[3].editor_cycle == 2
        assert created[4].run_attempt == 2
        assert all(item.pipeline_run_id == run.id for item in created)
        assert all(item.agent_run_id == agent_run.id for item in created)
        assert legacy.stage_occurrence_id is None
        assert legacy.run_attempt is None

        ordered = (
            await session.scalars(
                select(PipelineEvent)
                .where(PipelineEvent.pipeline_run_id == run.id)
                .order_by(PipelineEvent.sequence)
            )
        ).all()
        assert [item.sequence for item in ordered] == sorted(
            item.sequence for item in ordered
        )
        assert len(ordered) == 6

        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_concurrent_duplicate_event_returns_one_record(sessions):
    async with sessions() as session:
        project = await _project(session)
        run, _ = await PipelineRunService(session).create(project.id, "same-event")
        await session.commit()
        project_id, run_id = project.id, run.id

    context = EventContext.for_stage(run_id, "researcher", 0, 0, 1)

    async def append_same():
        async with sessions() as session:
            event = await EventService(session).append(
                project_id,
                run_id,
                "stage.started",
                "researcher",
                {},
                idempotency_key=context.event_key("stage.started"),
                context=context,
            )
            await session.commit()
            return event.id

    event_ids = await asyncio.gather(append_same(), append_same())
    assert event_ids[0] == event_ids[1]

    async with sessions() as session:
        rows = (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == run_id,
                    PipelineEvent.idempotency_key
                    == context.event_key("stage.started"),
                )
            )
        ).all()
        assert len(rows) == 1
        project = await session.get(Project, project_id)
        await session.delete(project)
        await session.commit()

@pytest.mark.asyncio
async def test_partial_rewrite_creates_new_physical_blocks(sessions):
    logical_id = uuid.uuid4()
    draft = {
        "title": "Versioned content",
        "blocks": [
            {
                "block_id": str(logical_id),
                "type": "paragraph",
                "position": 0,
                "sentences": [
                    {"text": "Process description", "is_factual": False, "evidence": []}
                ],
            }
        ],
    }
    async with sessions() as session:
        project = await _project(session)
        runs = PipelineRunService(session)
        first, _ = await runs.create(project.id, "version-1")
        version_one = await ContentVersionService(session).persist_draft(
            project, first, draft, uuid.uuid4()
        )
        first = await runs.transition(
            first.id, PipelineRunStatus.running, origin="test"
        )
        first = await runs.transition(
            first.id, PipelineRunStatus.needs_human_approval, origin="test"
        )
        first = await runs.transition(
            first.id, PipelineRunStatus.completed, origin="test-human-review"
        )
        second, _ = await runs.create(project.id, "version-2")
        draft["blocks"][0]["sentences"][0]["text"] = "Rewritten process description"
        version_two = await ContentVersionService(session).persist_draft(
            project, second, draft, uuid.uuid4(), {logical_id}
        )
        await session.commit()
        first_block = await session.scalar(
            select(ArticleBlock).where(
                ArticleBlock.article_version_id == version_one.id
            )
        )
        second_block = await session.scalar(
            select(ArticleBlock).where(
                ArticleBlock.article_version_id == version_two.id
            )
        )
        assert first_block.id != second_block.id
        assert first_block.logical_block_id == second_block.logical_block_id
        assert second_block.replaces_block_id == first_block.id
        article = await session.scalar(
            select(Article).where(Article.project_id == project.id)
        )
        await session.execute(
            delete(ArticleVersion).where(ArticleVersion.article_id == article.id)
        )
        await session.delete(article)
        await session.execute(
            delete(PipelineRun).where(PipelineRun.project_id == project.id)
        )
        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_version_transaction_rolls_back_on_block_collision(sessions):
    logical_one, logical_two = uuid.uuid4(), uuid.uuid4()
    draft = {
        "title": "Invalid duplicate positions",
        "blocks": [
            {
                "block_id": str(logical_one),
                "type": "paragraph",
                "position": 0,
                "sentences": [{"text": "One", "is_factual": False, "evidence": []}],
            },
            {
                "block_id": str(logical_two),
                "type": "paragraph",
                "position": 0,
                "sentences": [{"text": "Two", "is_factual": False, "evidence": []}],
            },
        ],
    }
    async with sessions() as session:
        project = await _project(session)
        project_id = project.id
        run, _ = await PipelineRunService(session).create(project_id, "rollback-version")
        with pytest.raises(IntegrityError):
            await ContentVersionService(session).persist_draft(
                project, run, draft, uuid.uuid4()
            )
            await session.flush()
        await session.rollback()
        async with session.begin():
            versions = await session.scalar(
                select(ArticleVersion)
                .join(Article)
                .where(Article.project_id == project_id)
            )
            blocks = await session.scalar(
                select(ArticleBlock)
                .join(
                    ArticleVersion,
                    ArticleBlock.article_version_id == ArticleVersion.id,
                )
                .join(Article, ArticleVersion.article_id == Article.id)
                .where(Article.project_id == project_id)
            )
            article = await session.scalar(
                select(Article).where(Article.project_id == project_id)
            )
            assert versions is None
            assert blocks is None
            assert article is None
        async with session.begin():
            project = await session.get(Project, project_id)
            assert project is not None
            await session.delete(project)


@pytest.mark.asyncio
async def test_repeated_stages_receive_monotonic_checkpoints(sessions):
    async with sessions() as session:
        project = await _project(session)
        run, _ = await PipelineRunService(session).create(project.id, "checkpoint-cycles")
        checkpoints = CheckpointService(session)
        first = await checkpoints.save(
            run,
            "researcher",
            "research_gatekeeper",
            {
                "project_id": str(project.id),
                "pipeline_run_id": str(run.id),
                "stage": "research_gatekeeper",
                "research_cycle": 1,
                "editor_cycle": 0,
            },
        )
        second = await checkpoints.save(
            run,
            "researcher",
            "research_gatekeeper",
            {
                "project_id": str(project.id),
                "pipeline_run_id": str(run.id),
                "stage": "research_gatekeeper",
                "research_cycle": 2,
                "editor_cycle": 0,
            },
        )
        await session.commit()

        assert (first.sequence, second.sequence) == (1, 2)
        assert first.idempotency_key != second.idempotency_key
        assert (await checkpoints.latest(run.id)).id == second.id
        checkpoint_events = (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == run.id,
                    PipelineEvent.event_type == "checkpoint.created",
                )
            )
        ).all()
        assert [item.checkpoint_sequence for item in checkpoint_events] == [1, 2]

        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_repeated_handoffs_receive_fresh_payload_and_sequence(sessions):
    async with sessions() as session:
        project = await _project(session)
        run, _ = await PipelineRunService(session).create(project.id, "handoff-cycles")
        handoffs = HandoffService(session)
        first = await handoffs.persist(
            project.id,
            run,
            "researcher",
            "research_gatekeeper",
            {"fact_count": 3},
            research_cycle=1,
        )
        second = await handoffs.persist(
            project.id,
            run,
            "researcher",
            "research_gatekeeper",
            {"fact_count": 8},
            research_cycle=2,
        )
        duplicate = await handoffs.persist(
            project.id,
            run,
            "researcher",
            "research_gatekeeper",
            {"fact_count": 8},
            research_cycle=2,
        )
        await session.commit()

        assert (first.sequence, second.sequence) == (1, 2)
        assert first.payload["fact_count"] == 3
        assert second.payload["fact_count"] == 8
        assert duplicate.id == second.id
        handoff_events = (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == run.id,
                    PipelineEvent.event_type == "handoff.created",
                )
            )
        ).all()
        assert len(handoff_events) == 2
        assert {item.payload["sequence"] for item in handoff_events} == {1, 2}

        await session.delete(project)
        await session.commit()


@pytest.mark.asyncio
async def test_expired_running_lease_is_reaped_for_retry(sessions):
    async with sessions() as session:
        project = await _project(session)
        service = PipelineRunService(session)
        run, _ = await service.create(project.id, "expired-worker")
        run = await service.claim(run.id, "dead-task", lease_seconds=60)
        run = await service.transition(
            run.id,
            PipelineRunStatus.running,
            origin="test",
            expected_lease_owner="dead-task",
            expected_lock_version=run.lock_version,
        )
        run.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

        reaped = await service.reap_expired_lease(run.id, datetime.now(timezone.utc))
        await session.commit()

        assert reaped.status == PipelineRunStatus.waiting_retry
        assert reaped.error_code == "worker.lease_expired"
        assert reaped.retryable is True
        assert reaped.next_retry_at is not None
        assert reaped.lease_owner is None
        transition = await session.scalar(
            select(PipelineStateTransition)
            .where(
                PipelineStateTransition.pipeline_run_id == run.id,
                PipelineStateTransition.to_status == "waiting_retry",
            )
            .order_by(PipelineStateTransition.created_at.desc())
        )
        assert transition.from_status == "running"
        assert transition.to_status == "waiting_retry"
        assert transition.origin == "celery.beat.lease-reaper"

        await session.delete(project)
        await session.commit()
