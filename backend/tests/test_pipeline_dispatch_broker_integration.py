import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from celery import Celery
from redis import Redis
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import PipelineDispatchStatus, PipelineRun, Project
from app.services.pipeline_control import PipelineRunService
from app.services.pipeline_dispatch import PipelineDispatchService, publish_reservations


pytestmark = pytest.mark.skipif(
    not (os.getenv("TEST_DATABASE_URL") and os.getenv("TEST_REDIS_URL")),
    reason="TEST_DATABASE_URL and TEST_REDIS_URL are required for broker tests",
)


@pytest_asyncio.fixture
async def integration_services():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    redis.ping()
    yield sessions, redis
    await engine.dispose()
    redis.close()


async def _reservation(sessions, label):
    async with sessions() as session:
        project = Project(
            name=label,
            topic="real celery broker",
            search_intent="informational",
            audience="engineers",
            status="queued",
        )
        session.add(project)
        await session.commit()
        run, _ = await PipelineRunService(session).create(project.id, str(uuid.uuid4()))
        reservation = (
            await PipelineDispatchService(session).claim_batch(
                "integration-beat", run_id=run.id
            )
        )[0]
        await session.commit()
        return project.id, run.id, reservation


class QueuePublisher:
    def __init__(self, queue):
        self.queue = queue
        app = Celery("dispatch-test", broker=os.environ["TEST_REDIS_URL"])
        self.task = app.signature("pipeline.run")

    def apply_async(self, args, task_id):
        return self.task.apply_async(args=args, task_id=task_id, queue=self.queue)


class UnavailablePublisher:
    def __init__(self):
        app = Celery("unavailable", broker="redis://127.0.0.1:1/15")
        app.conf.broker_connection_retry_on_startup = False
        app.conf.broker_connection_max_retries = 0
        app.conf.broker_transport_options = {
            "max_retries": 0,
            "socket_connect_timeout": 1,
        }
        self.task = app.signature("pipeline.run")

    def apply_async(self, args, task_id):
        return self.task.apply_async(args=args, task_id=task_id)


class FailFirstPublisher:
    def __init__(self, queue):
        self.real = QueuePublisher(queue)
        self.calls = 0

    def apply_async(self, args, task_id):
        self.calls += 1
        if self.calls == 1:
            raise ConnectionError("first message rejected")
        return self.real.apply_async(args, task_id)


@pytest.mark.asyncio
async def test_real_redis_accepts_only_the_reserved_message(
    integration_services,
):
    sessions, redis = integration_services
    queue = f"pipeline-dispatch-test-{uuid.uuid4()}"
    project_id, run_id, reservation = await _reservation(sessions, "accepted")
    try:
        results = await publish_reservations(
            [reservation], QueuePublisher(queue), sessions
        )
        assert results[0].status == "sent"
        assert redis.llen(queue) == 1
        async with sessions() as session:
            run = await session.get(PipelineRun, run_id)
            assert run.dispatch_status == PipelineDispatchStatus.sent
            assert run.celery_task_id == results[0].task_id
            assert await PipelineDispatchService(session).claim_batch(
                "second-beat", run_id=run_id
            ) == []
    finally:
        redis.delete(queue)
        async with sessions() as session:
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.commit()


@pytest.mark.asyncio
async def test_real_broker_refusal_is_durable(integration_services):
    sessions, _redis = integration_services
    project_id, run_id, reservation = await _reservation(sessions, "unavailable")
    try:
        results = await publish_reservations(
            [reservation], UnavailablePublisher(), sessions
        )
        assert results[0].status == "failed"
        async with sessions() as session:
            run = await session.get(PipelineRun, run_id)
            assert run.dispatch_status == PipelineDispatchStatus.failed
            assert run.dispatch_not_before > datetime.now(timezone.utc)
            assert run.last_dispatch_error
            assert run.next_retry_at is None
    finally:
        async with sessions() as session:
            await session.execute(delete(Project).where(Project.id == project_id))
            await session.commit()


@pytest.mark.asyncio
async def test_one_batch_failure_does_not_block_the_next_real_message(
    integration_services,
):
    sessions, redis = integration_services
    queue = f"pipeline-dispatch-batch-{uuid.uuid4()}"
    first_project, first_run, first = await _reservation(sessions, "first")
    second_project, second_run, second = await _reservation(sessions, "second")
    try:
        results = await publish_reservations(
            [first, second], FailFirstPublisher(queue), sessions
        )
        assert [result.status for result in results] == ["failed", "sent"]
        assert redis.llen(queue) == 1
        async with sessions() as session:
            failed = await session.get(PipelineRun, first_run)
            sent = await session.get(PipelineRun, second_run)
            assert failed.dispatch_status == PipelineDispatchStatus.failed
            assert sent.dispatch_status == PipelineDispatchStatus.sent
    finally:
        redis.delete(queue)
        async with sessions() as session:
            await session.execute(
                delete(Project).where(Project.id.in_([first_project, second_project]))
            )
            await session.commit()
