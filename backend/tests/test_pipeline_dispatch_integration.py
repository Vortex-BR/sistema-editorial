import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import (
    PipelineDispatchStatus,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    Project,
)
from app.services.pipeline_control import PipelineRunService
from app.services.pipeline_dispatch import (
    PipelineDispatchRejected,
    PipelineDispatchService,
)


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL is required for PostgreSQL integration tests",
)


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _run(session, key: str, status=PipelineRunStatus.queued):
    project = Project(
        name=f"Dispatch {key}",
        topic="atomic dispatch",
        search_intent="informational",
        audience="engineers",
        status="queued",
    )
    session.add(project)
    await session.commit()
    run, _ = await PipelineRunService(session).create(project.id, key)
    run.status = status
    if status == PipelineRunStatus.waiting_retry:
        run.next_retry_at = datetime.now(timezone.utc)
    await session.commit()
    return project, run


async def _cleanup(sessions, project_ids):
    async with sessions() as session:
        await session.execute(delete(Project).where(Project.id.in_(project_ids)))
        await session.commit()


@pytest.mark.asyncio
async def test_two_dispatchers_reserve_a_run_only_once(sessions):
    async with sessions() as session:
        project, run = await _run(session, f"concurrent-{uuid.uuid4()}")
        project_id, run_id = project.id, run.id

    ready = asyncio.Event()

    async def claim(owner):
        async with sessions() as session:
            ready.set()
            reservations = await PipelineDispatchService(session).claim_batch(
                owner, run_id=run_id
            )
            await session.commit()
            return [item for item in reservations if item.run_id == run_id]

    first, second = await asyncio.gather(claim("beat-one"), claim("beat-two"))
    assert len(first) + len(second) == 1
    assert ready.is_set()

    async with sessions() as session:
        current = await session.get(PipelineRun, run_id)
        assert current.dispatch_attempt == 1
        assert current.dispatch_status == PipelineDispatchStatus.claimed
        events = (
            await session.scalars(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == run_id,
                    PipelineEvent.event_type == "dispatch.claimed",
                )
            )
        ).all()
        assert len(events) == 1
    await _cleanup(sessions, [project_id])


@pytest.mark.asyncio
async def test_waiting_retry_is_never_claimed_before_next_retry_at(sessions):
    async with sessions() as session:
        project, run = await _run(
            session, f"retry-clock-{uuid.uuid4()}", PipelineRunStatus.waiting_retry
        )
        project_id = project.id
        run.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await session.commit()
        assert await PipelineDispatchService(session).claim_batch(
            "early", run_id=run.id
        ) == []
        run.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        claimed = await PipelineDispatchService(session).claim_batch(
            "on-time", run_id=run.id
        )
        await session.commit()
        assert len(claimed) == 1
    await _cleanup(sessions, [project_id])


@pytest.mark.asyncio
async def test_dead_dispatch_expires_and_is_reclaimed_with_new_token(sessions):
    async with sessions() as session:
        project, run = await _run(session, f"dead-{uuid.uuid4()}")
        project_id, run_id = project.id, run.id
        first = (
            await PipelineDispatchService(session).claim_batch(
                "dead-beat", run_id=run.id
            )
        )[0]
        await session.commit()
        run.dispatch_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()

    async with sessions() as session:
        service = PipelineDispatchService(session)
        assert await service.recover_expired() == 1
        second = (await service.claim_batch("new-beat", run_id=run_id))[0]
        await session.commit()
        assert second.token != first.token
        assert second.attempt == 2
        event_types = set(
            await session.scalars(
                select(PipelineEvent.event_type).where(
                    PipelineEvent.pipeline_run_id == run_id
                )
            )
        )
        assert {"dispatch.expired", "dispatch.reclaimed"} <= event_types
    await _cleanup(sessions, [project_id])


@pytest.mark.asyncio
async def test_worker_rejects_duplicate_and_old_tokens(sessions):
    async with sessions() as session:
        project, run = await _run(session, f"tokens-{uuid.uuid4()}")
        project_id = project.id
        run_id = run.id
        reservation = (
            await PipelineDispatchService(session).claim_batch("beat", run_id=run_id)
        )[0]
        await session.commit()
        claimed = await PipelineDispatchService(session).claim_for_worker(
            run_id, reservation.token, "worker-one"
        )
        await session.commit()
        assert claimed.dispatch_status == PipelineDispatchStatus.consumed
        with pytest.raises(PipelineDispatchRejected, match="already-consumed"):
            await PipelineDispatchService(session).claim_for_worker(
                run_id, reservation.token, "worker-two"
            )
        await session.rollback()
        with pytest.raises(PipelineDispatchRejected, match="stale-dispatch"):
            await PipelineDispatchService(session).claim_for_worker(
                run_id, uuid.uuid4(), "worker-two"
            )
        await session.rollback()
    await _cleanup(sessions, [project_id])


@pytest.mark.asyncio
async def test_worker_rejects_missing_and_expired_tokens(sessions):
    async with sessions() as session:
        project, run = await _run(session, f"expired-token-{uuid.uuid4()}")
        project_id = project.id
        run_id = run.id
        reservation = (
            await PipelineDispatchService(session).claim_batch("beat", run_id=run_id)
        )[0]
        await session.commit()
        with pytest.raises(PipelineDispatchRejected, match="missing-dispatch-token"):
            await PipelineDispatchService(session).claim_for_worker(
                run_id, None, "worker"
            )
        await session.rollback()
        run = await session.get(PipelineRun, run_id)
        run.dispatch_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
        with pytest.raises(PipelineDispatchRejected, match="expired-dispatch"):
            await PipelineDispatchService(session).claim_for_worker(
                run_id, reservation.token, "worker"
            )
        await session.rollback()
    await _cleanup(sessions, [project_id])


@pytest.mark.asyncio
async def test_terminal_and_running_with_active_lease_are_not_dispatched(sessions):
    project_ids = []
    async with sessions() as session:
        terminal_project, terminal = await _run(
            session, f"terminal-{uuid.uuid4()}", PipelineRunStatus.completed
        )
        running_project, running = await _run(
            session, f"running-{uuid.uuid4()}", PipelineRunStatus.running
        )
        project_ids += [terminal_project.id, running_project.id]
        running.lease_owner = "live-worker"
        running.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
        await session.commit()
        service = PipelineDispatchService(session)
        assert await service.claim_batch("beat", run_id=terminal.id) == []
        assert await service.claim_batch("beat", run_id=running.id) == []
    await _cleanup(sessions, project_ids)


@pytest.mark.asyncio
async def test_cancelled_run_is_not_reenqueued_by_dispatch(sessions):
    async with sessions() as session:
        project, run = await _run(session, f"cancelled-{uuid.uuid4()}")
        project_id = project.id
        reservation = (
            await PipelineDispatchService(session).claim_batch(
                "beat", run_id=run.id
            )
        )[0]
        await PipelineDispatchService(session).mark_sent(
            reservation, "celery-task-cancelled"
        )
        await PipelineRunService(session).request_cancellation(
            run.id, origin="admin.api"
        )
        await session.commit()

        assert await PipelineDispatchService(session).claim_batch(
            "beat-again", run_id=run.id
        ) == []
        assert await PipelineDispatchService(session).recover_expired(
            run_id=run.id,
            now=datetime.now(timezone.utc) + timedelta(minutes=5),
        ) == 0
    await _cleanup(sessions, [project_id])


@pytest.mark.asyncio
async def test_skip_locked_batch_dispatches_other_runs(sessions):
    project_ids = []
    run_ids = []
    async with sessions() as session:
        for index in range(3):
            project, run = await _run(session, f"batch-{index}-{uuid.uuid4()}")
            project_ids.append(project.id)
            run_ids.append(run.id)

    async with sessions() as blocker:
        await blocker.scalar(
            select(PipelineRun)
            .where(PipelineRun.id == run_ids[0])
            .with_for_update()
        )
        async with sessions() as dispatcher:
            claimed = await PipelineDispatchService(dispatcher).claim_batch(
                "batch-beat", limit=3
            )
            await dispatcher.commit()
        claimed_ids = {item.run_id for item in claimed}
        assert run_ids[0] not in claimed_ids
        assert set(run_ids[1:]) <= claimed_ids
        await blocker.rollback()
    await _cleanup(sessions, project_ids)
