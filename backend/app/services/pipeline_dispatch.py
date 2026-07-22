import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import PUBLIC_ERROR_MESSAGE, new_correlation_id
from app.core.observability import structured_exception_log
from app.db.models import (
    PipelineCheckpoint,
    PipelineDispatchStatus,
    PipelineRun,
    PipelineRunStatus,
)
from app.db.session import SessionLocal
from app.services.pipeline_control import TERMINAL_RUN_STATUSES, EventService, PipelineRunService


ACTIVE_DISPATCH_STATUSES = {
    PipelineDispatchStatus.claimed,
    PipelineDispatchStatus.sent,
}


@dataclass(frozen=True)
class DispatchReservation:
    run_id: uuid.UUID
    token: uuid.UUID
    attempt: int


@dataclass(frozen=True)
class DispatchResult:
    run_id: uuid.UUID
    status: str
    task_id: str | None = None
    error: str | None = None


class PipelineDispatchRejected(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def dispatcher_identity(task_id: str | None = None, origin: str = "celery.beat") -> str:
    suffix = task_id or str(uuid.uuid4())
    return f"{origin}:{socket.gethostname()}:{os.getpid()}:{suffix}"[:160]


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _schedule_is_due(run: PipelineRun, now: datetime) -> bool:
    next_retry_at = _aware(run.next_retry_at)
    if run.status == PipelineRunStatus.queued:
        return next_retry_at is None or next_retry_at <= now
    if run.status == PipelineRunStatus.waiting_retry:
        return next_retry_at is not None and next_retry_at <= now
    return False


class PipelineDispatchService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _eligible_expression(now: datetime):
        schedule_due = or_(
            and_(
                PipelineRun.status == PipelineRunStatus.queued,
                or_(
                    PipelineRun.next_retry_at.is_(None),
                    PipelineRun.next_retry_at <= now,
                ),
            ),
            and_(
                PipelineRun.status == PipelineRunStatus.waiting_retry,
                PipelineRun.next_retry_at.is_not(None),
                PipelineRun.next_retry_at <= now,
            ),
        )
        dispatch_available = and_(
            or_(
                PipelineRun.dispatch_not_before.is_(None),
                PipelineRun.dispatch_not_before <= now,
            ),
            or_(
                PipelineRun.dispatch_status.is_(None),
                PipelineRun.dispatch_status.not_in(ACTIVE_DISPATCH_STATUSES),
                PipelineRun.dispatch_expires_at <= now,
            ),
        )
        return and_(schedule_due, dispatch_available)

    async def recover_expired(
        self,
        now: datetime | None = None,
        *,
        run_id: uuid.UUID | None = None,
        limit: int | None = None,
    ) -> int:
        now = now or datetime.now(timezone.utc)
        query = (
            select(PipelineRun)
            .where(
                PipelineRun.dispatch_status.in_(ACTIVE_DISPATCH_STATUSES),
                PipelineRun.dispatch_expires_at.is_not(None),
                PipelineRun.dispatch_expires_at <= now,
                PipelineRun.status.not_in(TERMINAL_RUN_STATUSES),
            )
            .order_by(PipelineRun.dispatch_expires_at, PipelineRun.created_at)
            .limit(limit or settings.pipeline_dispatch_batch_size)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if run_id is not None:
            query = query.where(PipelineRun.id == run_id)
        rows = (
            await self.db.scalars(query)
        ).all()
        recovered = 0
        events = EventService(self.db)
        for run in rows:
            lease_expires_at = _aware(run.lease_expires_at)
            active_lease = bool(
                run.lease_owner and lease_expires_at and lease_expires_at > now
            )
            progress = await self.db.scalar(
                select(PipelineCheckpoint.id)
                .where(
                    PipelineCheckpoint.pipeline_run_id == run.id,
                    PipelineCheckpoint.completed_at > run.dispatch_claimed_at,
                )
                .limit(1)
            )
            if run.status == PipelineRunStatus.running or active_lease or progress:
                continue
            expired_token = run.dispatch_token
            run.dispatch_status = PipelineDispatchStatus.expired
            run.dispatch_expires_at = now
            await events.append(
                run.project_id,
                run.id,
                "dispatch.expired",
                run.current_stage,
                {
                    "dispatch_token": str(expired_token),
                    "dispatch_attempt": run.dispatch_attempt,
                    "claimed_by": run.dispatch_claimed_by,
                },
                idempotency_key=f"dispatch.expired:{expired_token}",
            )
            recovered += 1
        await self.db.flush()
        return recovered

    async def claim_batch(
        self,
        claimed_by: str,
        *,
        limit: int | None = None,
        run_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> list[DispatchReservation]:
        now = now or datetime.now(timezone.utc)
        limit = limit or settings.pipeline_dispatch_batch_size
        query = (
            select(PipelineRun)
            .where(self._eligible_expression(now))
            .order_by(PipelineRun.next_retry_at.asc().nullsfirst(), PipelineRun.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if run_id is not None:
            query = query.where(PipelineRun.id == run_id)
        runs = (await self.db.scalars(query)).all()
        reservations: list[DispatchReservation] = []
        events = EventService(self.db)
        for run in runs:
            if not _schedule_is_due(run, now):
                continue
            previous_token = run.dispatch_token
            reclaimed = run.dispatch_status == PipelineDispatchStatus.expired
            token = uuid.uuid4()
            run.dispatch_token = token
            run.dispatch_status = PipelineDispatchStatus.claimed
            run.dispatch_claimed_by = claimed_by
            run.dispatch_claimed_at = now
            run.dispatch_expires_at = now + timedelta(
                seconds=settings.pipeline_dispatch_claim_ttl_seconds
            )
            run.dispatch_attempt += 1
            run.dispatch_not_before = None
            run.celery_task_id = None
            payload = {
                "dispatch_token": str(token),
                "dispatch_attempt": run.dispatch_attempt,
                "claimed_by": claimed_by,
                "expires_at": run.dispatch_expires_at.isoformat(),
            }
            await events.append(
                run.project_id,
                run.id,
                "dispatch.claimed",
                run.current_stage,
                payload,
                idempotency_key=f"dispatch.claimed:{token}",
            )
            if reclaimed:
                await events.append(
                    run.project_id,
                    run.id,
                    "dispatch.reclaimed",
                    run.current_stage,
                    {**payload, "previous_dispatch_token": str(previous_token)},
                    idempotency_key=f"dispatch.reclaimed:{token}",
                )
            reservations.append(DispatchReservation(run.id, token, run.dispatch_attempt))
        await self.db.flush()
        return reservations

    async def mark_sent(
        self,
        reservation: DispatchReservation,
        task_id: str,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(timezone.utc)
        run = await PipelineRunService(self.db).acquire(reservation.run_id)
        if run.dispatch_token != reservation.token:
            return False
        if run.dispatch_status not in {
            PipelineDispatchStatus.claimed,
            PipelineDispatchStatus.consumed,
        }:
            return False
        if run.dispatch_status == PipelineDispatchStatus.claimed:
            run.dispatch_status = PipelineDispatchStatus.sent
            run.dispatch_expires_at = now + timedelta(
                seconds=settings.pipeline_dispatch_delivery_timeout_seconds
            )
        run.last_dispatched_at = now
        run.celery_task_id = task_id
        run.last_dispatch_error = None
        await EventService(self.db).append(
            run.project_id,
            run.id,
            "dispatch.sent",
            run.current_stage,
            {
                "dispatch_token": str(reservation.token),
                "dispatch_attempt": reservation.attempt,
                "celery_task_id": task_id,
            },
            idempotency_key=f"dispatch.sent:{reservation.token}",
        )
        await self.db.flush()
        return True

    async def mark_failed(
        self,
        reservation: DispatchReservation,
        error: Exception,
        now: datetime | None = None,
    ) -> bool:
        correlation_id = new_correlation_id()
        structured_exception_log(
            "dispatch.failed.internal",
            error,
            pipeline_run_id=reservation.run_id,
            correlation_id=correlation_id,
        )
        now = now or datetime.now(timezone.utc)
        run = await PipelineRunService(self.db).acquire(reservation.run_id)
        if (
            run.dispatch_token != reservation.token
            or run.dispatch_status != PipelineDispatchStatus.claimed
        ):
            return False
        delay = min(
            settings.pipeline_dispatch_retry_max_seconds,
            settings.pipeline_dispatch_retry_base_seconds
            * (2 ** min(16, max(0, reservation.attempt - 1))),
        )
        message = PUBLIC_ERROR_MESSAGE
        run.dispatch_status = PipelineDispatchStatus.failed
        run.dispatch_expires_at = now
        run.dispatch_not_before = now + timedelta(seconds=delay)
        run.last_dispatch_error = message
        await EventService(self.db).append(
            run.project_id,
            run.id,
            "dispatch.failed",
            run.current_stage,
            {
                "dispatch_token": str(reservation.token),
                "dispatch_attempt": reservation.attempt,
                "error_type": error.__class__.__name__,
                "message": message[:1000],
                "error_code": "DISPATCH_FAILED",
                "correlation_id": correlation_id,
                "retry_at": run.dispatch_not_before.isoformat(),
            },
            idempotency_key=f"dispatch.failed:{reservation.token}",
        )
        await self.db.flush()
        return True

    async def claim_for_worker(
        self,
        run_id: uuid.UUID,
        dispatch_token: uuid.UUID | None,
        lease_owner: str,
        *,
        lease_seconds: int = 1800,
        now: datetime | None = None,
    ) -> PipelineRun:
        now = now or datetime.now(timezone.utc)
        run = await PipelineRunService(self.db).acquire(run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            raise PipelineDispatchRejected(run.status.value)
        if dispatch_token is None:
            raise PipelineDispatchRejected("missing-dispatch-token")
        if run.dispatch_token != dispatch_token:
            raise PipelineDispatchRejected("stale-dispatch")
        if run.dispatch_status == PipelineDispatchStatus.consumed:
            raise PipelineDispatchRejected("already-consumed")
        if run.dispatch_status not in ACTIVE_DISPATCH_STATUSES:
            raise PipelineDispatchRejected("stale-dispatch")
        expires_at = _aware(run.dispatch_expires_at)
        if expires_at is None or expires_at <= now:
            raise PipelineDispatchRejected("expired-dispatch")
        if not _schedule_is_due(run, now):
            raise PipelineDispatchRejected("not-due")
        lease_expires_at = _aware(run.lease_expires_at)
        if (
            run.lease_owner
            and run.lease_owner != lease_owner
            and lease_expires_at
            and lease_expires_at > now
        ):
            raise PipelineDispatchRejected("already-running")
        run.lease_owner = lease_owner
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        run.dispatch_status = PipelineDispatchStatus.consumed
        await EventService(self.db).append(
            run.project_id,
            run.id,
            "worker.lease_acquired",
            run.current_stage,
            {
                "dispatch_token": str(dispatch_token),
                "dispatch_attempt": run.dispatch_attempt,
                "lease_owner": lease_owner,
                "lease_expires_at": run.lease_expires_at.isoformat(),
            },
            idempotency_key=f"worker.lease_acquired:{dispatch_token}",
        )
        await self.db.flush()
        return run


async def publish_reservations(
    reservations: list[DispatchReservation], task: Any, session_factory=SessionLocal
) -> list[DispatchResult]:
    results: list[DispatchResult] = []
    for reservation in reservations:
        task_id = str(uuid.uuid4())
        try:
            task.apply_async(
                args=[str(reservation.run_id), str(reservation.token)],
                task_id=task_id,
            )
        except Exception as exc:
            async with session_factory() as db:
                changed = await PipelineDispatchService(db).mark_failed(
                    reservation, exc
                )
                await db.commit()
            results.append(
                DispatchResult(
                    reservation.run_id,
                    "failed" if changed else "superseded",
                    error=PUBLIC_ERROR_MESSAGE,
                )
            )
            continue
        async with session_factory() as db:
            changed = await PipelineDispatchService(db).mark_sent(
                reservation, task_id
            )
            await db.commit()
        results.append(
            DispatchResult(
                reservation.run_id,
                "sent" if changed else "superseded",
                task_id=task_id,
            )
        )
    return results


async def dispatch_due_runs(task: Any, claimed_by: str) -> dict[str, int]:
    async with SessionLocal() as db:
        service = PipelineDispatchService(db)
        recovered = await service.recover_expired()
        reservations = await service.claim_batch(claimed_by)
        await db.commit()
    results = await publish_reservations(reservations, task)
    return {
        "recovered": recovered,
        "claimed": len(reservations),
        "sent": sum(result.status == "sent" for result in results),
        "failed": sum(result.status == "failed" for result in results),
    }


async def dispatch_one(
    run_id: uuid.UUID, task: Any, claimed_by: str
) -> DispatchResult | None:
    async with SessionLocal() as db:
        service = PipelineDispatchService(db)
        await service.recover_expired(run_id=run_id, limit=1)
        reservations = await service.claim_batch(claimed_by, limit=1, run_id=run_id)
        await db.commit()
    if not reservations:
        return None
    return (await publish_reservations(reservations, task))[0]
