import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.pipeline_control as pipeline_control_module
from app.db.models import (
    PipelineDispatchStatus,
    PipelineRun,
    PipelineRunStatus,
    PipelineStateTransition,
    ProjectStatus,
    TriggerType,
)
from app.services.pipeline_control import (
    InvalidRunTransition,
    PROJECT_STATE_BEFORE_RUN_KEY,
    PipelineCancellationRequested,
    PipelineRunService,
)
from app.orchestration.executor import PipelineExecutor
from app.workers.tasks import _project_status_for_run


class FakeDb:
    def __init__(self):
        self.added = []
        self.flush_count = 0
        self.scalar_results = []

    def add(self, instance):
        self.added.append(instance)

    async def flush(self):
        self.flush_count += 1

    async def scalar(self, _query):
        return self.scalar_results.pop(0) if self.scalar_results else None


def pipeline_run(status: PipelineRunStatus) -> PipelineRun:
    now = datetime.now(timezone.utc)
    return PipelineRun(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        status=status,
        trigger_type=TriggerType.api,
        current_stage="researcher",
        attempt=2,
        idempotency_key=str(uuid.uuid4()),
        retryable=True,
        next_retry_at=now + timedelta(minutes=5),
        cancellation_requested_at=None,
        metadata_json={},
        lock_version=4,
        checkpoint_sequence=1,
        handoff_sequence=1,
        lease_owner="worker-one",
        lease_expires_at=now + timedelta(minutes=10),
        dispatch_token=uuid.uuid4(),
        dispatch_status=PipelineDispatchStatus.claimed,
        dispatch_claimed_by="beat-one",
        dispatch_claimed_at=now,
        dispatch_expires_at=now + timedelta(minutes=1),
        dispatch_attempt=3,
        dispatch_not_before=now + timedelta(minutes=1),
        last_dispatch_error="temporary broker failure",
        celery_task_id="celery-task-one",
    )


@pytest.fixture
def event_calls(monkeypatch):
    calls = []

    class FakeEventService:
        def __init__(self, _db):
            pass

        async def append(self, *args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(pipeline_control_module, "EventService", FakeEventService)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [PipelineRunStatus.queued, PipelineRunStatus.waiting_retry]
)
async def test_pending_run_is_cancelled_immediately_and_dispatch_is_invalidated(
    status, event_calls
):
    db = FakeDb()
    run = pipeline_run(status)
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=run)

    result = await service.request_cancellation(run.id, origin="admin.api")

    assert result is run
    assert run.status == PipelineRunStatus.cancelled
    assert run.cancellation_requested_at is not None
    assert run.finished_at is not None
    assert run.retryable is False
    assert run.next_retry_at is None
    assert run.dispatch_token is None
    assert run.dispatch_status is None
    assert run.dispatch_claimed_by is None
    assert run.dispatch_claimed_at is None
    assert run.dispatch_expires_at is None
    assert run.dispatch_not_before is None
    assert run.last_dispatch_error is None
    assert run.celery_task_id is None
    assert run.lease_owner is None
    assert run.lease_expires_at is None
    assert run.dispatch_attempt == 3
    transitions = [item for item in db.added if isinstance(item, PipelineStateTransition)]
    assert len(transitions) == 1
    assert transitions[0].from_status == status.value
    assert transitions[0].to_status == PipelineRunStatus.cancelled.value
    assert event_calls[0][0][2] == "pipeline.cancelled"
    assert event_calls[0][0][4]["actor"] == "admin.api"
    assert (
        event_calls[0][0][4]["reason"]
        == "Cancellation requested by an administrator"
    )


@pytest.mark.asyncio
async def test_running_run_records_durable_request_until_safe_boundary(event_calls):
    db = FakeDb()
    run = pipeline_run(PipelineRunStatus.running)
    dispatch_token = run.dispatch_token
    lease_owner = run.lease_owner
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=run)

    result = await service.request_cancellation(run.id, origin="admin.api")

    assert result.status == PipelineRunStatus.running
    assert result.cancellation_requested_at is not None
    assert result.dispatch_token == dispatch_token
    assert result.lease_owner == lease_owner
    assert not any(isinstance(item, PipelineStateTransition) for item in db.added)
    assert event_calls[0][0][2] == "pipeline.cancellation_requested"


@pytest.mark.asyncio
async def test_running_cancellation_is_idempotent(event_calls):
    db = FakeDb()
    run = pipeline_run(PipelineRunStatus.running)
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=run)

    first = await service.request_cancellation(run.id, origin="admin.api")
    requested_at = first.cancellation_requested_at
    second = await service.request_cancellation(run.id, origin="admin.api")

    assert second is first
    assert second.cancellation_requested_at == requested_at
    assert len(event_calls) == 1


@pytest.mark.asyncio
async def test_terminal_run_conflicts_but_cancelled_run_is_idempotent(event_calls):
    db = FakeDb()
    completed = pipeline_run(PipelineRunStatus.completed)
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=completed)

    with pytest.raises(InvalidRunTransition):
        await service.request_cancellation(completed.id, origin="admin.api")

    cancelled = pipeline_run(PipelineRunStatus.cancelled)
    cancelled.cancellation_requested_at = None
    service.acquire = AsyncMock(return_value=cancelled)
    result = await service.request_cancellation(cancelled.id, origin="admin.api")

    assert result is cancelled
    assert result.cancellation_requested_at is not None
    assert event_calls == []


@pytest.mark.asyncio
async def test_worker_cannot_overwrite_a_cancellation_request(event_calls):
    db = FakeDb()
    run = pipeline_run(PipelineRunStatus.running)
    run.cancellation_requested_at = datetime.now(timezone.utc)
    service = PipelineRunService(db)

    with pytest.raises(PipelineCancellationRequested):
        await service._transition_locked(
            run,
            PipelineRunStatus.completed,
            origin="orchestrator",
        )

    assert run.status == PipelineRunStatus.running
    assert run.finished_at is None
    assert not any(isinstance(item, PipelineStateTransition) for item in db.added)


@pytest.mark.asyncio
async def test_safe_boundary_cancels_only_the_selected_run(event_calls):
    db = FakeDb()
    selected = pipeline_run(PipelineRunStatus.running)
    selected.cancellation_requested_at = datetime.now(timezone.utc)
    other = pipeline_run(PipelineRunStatus.running)
    other_before = {
        "status": other.status,
        "lease_owner": other.lease_owner,
        "dispatch_token": other.dispatch_token,
    }
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=selected)

    result = await service.honor_cancellation(
        selected.id,
        origin="orchestrator.safe-boundary",
        expected_lease_owner="worker-one",
    )

    assert result is selected
    assert selected.status == PipelineRunStatus.cancelled
    assert selected.lease_owner is None
    assert {
        "status": other.status,
        "lease_owner": other.lease_owner,
        "dispatch_token": other.dispatch_token,
    } == other_before
    assert event_calls[0][0][2] == "pipeline.cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prior_status", "prior_stage"),
    [
        (ProjectStatus.draft, "planner"),
        (ProjectStatus.completed, "completed"),
    ],
)
async def test_cancellation_restores_editorial_state_instead_of_failing_project(
    prior_status, prior_stage, event_calls
):
    db = FakeDb()
    run = pipeline_run(PipelineRunStatus.queued)
    run.metadata_json = {
        PROJECT_STATE_BEFORE_RUN_KEY: {
            "status": prior_status.value,
            "current_stage": prior_stage,
        }
    }
    project = SimpleNamespace(
        id=run.project_id,
        status=ProjectStatus.queued,
        current_stage="planner",
    )
    db.scalar_results = [run, project]
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=run)

    await service.request_cancellation(run.id, origin="admin.api")

    assert run.status == PipelineRunStatus.cancelled
    assert project.status == prior_status
    assert project.current_stage == prior_stage
    assert run.error_code is None
    assert run.failed_at is None
    assert run.retryable is False


@pytest.mark.asyncio
async def test_running_cancellation_restores_approved_project_at_safe_boundary(
    event_calls,
):
    db = FakeDb()
    run = pipeline_run(PipelineRunStatus.running)
    run.cancellation_requested_at = datetime.now(timezone.utc)
    run.metadata_json = {
        PROJECT_STATE_BEFORE_RUN_KEY: {
            "status": ProjectStatus.completed.value,
            "current_stage": "completed",
        }
    }
    project = SimpleNamespace(
        id=run.project_id,
        status=ProjectStatus.running,
        current_stage="writer",
    )
    db.scalar_results = [run, project]
    service = PipelineRunService(db)
    service.acquire = AsyncMock(return_value=run)

    await service.honor_cancellation(
        run.id,
        origin="orchestrator.safe-boundary",
        expected_lease_owner="worker-one",
    )

    assert project.status == ProjectStatus.completed
    assert project.current_stage == "completed"
    assert run.status == PipelineRunStatus.cancelled


@pytest.mark.asyncio
async def test_old_cancelled_run_cannot_overwrite_a_newer_project_state():
    db = FakeDb()
    cancelled = pipeline_run(PipelineRunStatus.cancelled)
    newer = pipeline_run(PipelineRunStatus.completed)
    newer.project_id = cancelled.project_id
    db.scalar_results = [newer]

    restored = await PipelineRunService(db).restore_project_after_cancellation(
        cancelled
    )

    assert restored is False


@pytest.mark.asyncio
async def test_transient_new_project_snapshot_falls_back_to_draft():
    db = FakeDb()
    project = SimpleNamespace(
        id=uuid.uuid4(),
        status=ProjectStatus.queued,
        current_stage="planner",
    )

    snapshot = await PipelineRunService(db)._project_state_snapshot(project)

    assert snapshot == {"status": "draft", "current_stage": "planner"}


def test_only_technical_failure_projects_to_failed():
    assert _project_status_for_run(PipelineRunStatus.failed) == ProjectStatus.failed
    with pytest.raises(ValueError):
        _project_status_for_run(PipelineRunStatus.cancelled)


@pytest.mark.asyncio
async def test_safe_boundary_rolls_back_partial_version_before_cancelling():
    requested_at = datetime.now(timezone.utc)
    run = pipeline_run(PipelineRunStatus.running)
    project = SimpleNamespace(id=run.project_id, status="running", current_stage="writer")
    order = []

    class NoAutoflush:
        def __enter__(self):
            return None

        def __exit__(self, *_args):
            return None

    class Snapshot:
        def one_or_none(self):
            return PipelineRunStatus.running, requested_at

    class BoundaryDb:
        no_autoflush = NoAutoflush()

        def __init__(self):
            self.partial_versions = ["uncommitted article version"]

        async def execute(self, _query):
            return Snapshot()

        async def rollback(self):
            order.append("rollback")
            self.partial_versions.clear()

        async def get(self, _model, _identifier):
            return project

        async def scalar(self, _query):
            return None

        async def commit(self):
            order.append("commit-cancellation")

    class RunService:
        async def honor_cancellation(self, *_args, **_kwargs):
            assert db.partial_versions == []
            order.append("honor-cancellation")
            run.status = PipelineRunStatus.cancelled
            run.cancellation_requested_at = requested_at
            return run

    db = BoundaryDb()
    executor = object.__new__(PipelineExecutor)
    executor.db = db
    executor.project = project
    executor.pipeline_run = run
    executor.lease_owner = "worker-one"
    executor.run_service = RunService()

    with pytest.raises(PipelineCancellationRequested):
        await executor._cancellation_boundary()

    assert db.partial_versions == []
    assert run.status == PipelineRunStatus.cancelled
    assert order == ["rollback", "honor-cancellation", "commit-cancellation"]
