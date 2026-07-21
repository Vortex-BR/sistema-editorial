import hashlib
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from kombu.exceptions import OperationalError as KombuOperationalError
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    PipelineCheckpoint,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    PipelineStateTransition,
    Project,
    ProjectStatus,
    TriggerType,
)
from app.core.errors import (
    PERSISTENCE_INPUT_INVALID,
    PUBLIC_ERROR_MESSAGE,
    is_persistence_input_error,
)
from app.core.sanitization import sanitize_nul
from app.services.llm_gateway import ProviderError


class InvalidRunTransition(RuntimeError):
    pass


class PipelineRunBusy(RuntimeError):
    pass


class PipelineRunVersionConflict(PipelineRunBusy):
    pass


class PipelineCancellationRequested(RuntimeError):
    pass


TERMINAL_RUN_STATUSES = {
    PipelineRunStatus.needs_review,
    PipelineRunStatus.needs_human_approval,
    PipelineRunStatus.blocked,
    PipelineRunStatus.failed,
    PipelineRunStatus.cancelled,
    PipelineRunStatus.completed,
    PipelineRunStatus.rejected,
}

PROJECT_STATE_BEFORE_RUN_KEY = "project_state_before_run"
_TRANSIENT_PROJECT_STATUSES = {
    ProjectStatus.queued,
    ProjectStatus.running,
}
_TERMINAL_RUN_PROJECT_STATUSES = {
    PipelineRunStatus.needs_review: ProjectStatus.needs_review,
    PipelineRunStatus.needs_human_approval: ProjectStatus.needs_human_approval,
    PipelineRunStatus.blocked: ProjectStatus.blocked,
    PipelineRunStatus.failed: ProjectStatus.failed,
    PipelineRunStatus.completed: ProjectStatus.completed,
    PipelineRunStatus.rejected: ProjectStatus.rejected,
}

ALLOWED_RUN_TRANSITIONS = {
    PipelineRunStatus.queued: {
        PipelineRunStatus.running,
        PipelineRunStatus.cancelled,
        PipelineRunStatus.failed,
    },
    PipelineRunStatus.running: {
        PipelineRunStatus.waiting_retry,
        PipelineRunStatus.needs_review,
        PipelineRunStatus.needs_human_approval,
        PipelineRunStatus.blocked,
        PipelineRunStatus.failed,
        PipelineRunStatus.cancelled,
    },
    PipelineRunStatus.waiting_retry: {
        PipelineRunStatus.running,
        PipelineRunStatus.failed,
        PipelineRunStatus.cancelled,
    },
    PipelineRunStatus.needs_review: set(),
    PipelineRunStatus.needs_human_approval: {
        PipelineRunStatus.completed,
        PipelineRunStatus.needs_review,
        PipelineRunStatus.rejected,
    },
    PipelineRunStatus.blocked: set(),
    PipelineRunStatus.failed: set(),
    PipelineRunStatus.cancelled: set(),
    PipelineRunStatus.completed: set(),
    PipelineRunStatus.rejected: set(),
}


@dataclass(frozen=True)
class ErrorDecision:
    code: str
    retryable: bool
    delay_seconds: int | None


@dataclass(frozen=True)
class EventContext:
    stage_occurrence_id: uuid.UUID | None = None
    research_cycle: int | None = None
    editor_cycle: int | None = None
    run_attempt: int | None = None
    stage_attempt: int | None = None
    checkpoint_sequence: int | None = None
    agent_run_id: uuid.UUID | None = None

    @classmethod
    def for_stage(
        cls,
        pipeline_run_id: uuid.UUID,
        stage: str,
        research_cycle: int,
        editor_cycle: int,
        run_attempt: int,
    ) -> "EventContext":
        active_research_cycle = research_cycle
        active_editor_cycle = editor_cycle
        if stage == "researcher":
            active_research_cycle += 1
            stage_attempt = active_research_cycle
        elif stage == "research_gatekeeper":
            active_research_cycle = max(1, active_research_cycle)
            stage_attempt = active_research_cycle
        elif stage in {"writer", "editor"}:
            active_editor_cycle += 1
            stage_attempt = active_editor_cycle
        else:
            stage_attempt = 1
        identity = (
            f"pipeline-stage:{pipeline_run_id}:{stage}:"
            f"research-cycle-{active_research_cycle}:"
            f"editor-cycle-{active_editor_cycle}:"
            f"run-attempt-{run_attempt}:stage-attempt-{stage_attempt}"
        )
        return cls(
            stage_occurrence_id=uuid.uuid5(uuid.NAMESPACE_URL, identity),
            research_cycle=active_research_cycle,
            editor_cycle=active_editor_cycle,
            run_attempt=run_attempt,
            stage_attempt=stage_attempt,
        )

    def with_checkpoint(self, sequence: int) -> "EventContext":
        return replace(self, checkpoint_sequence=sequence)

    def with_agent(self, agent_run_id: uuid.UUID) -> "EventContext":
        return replace(self, agent_run_id=agent_run_id)

    def event_key(self, event_type: str) -> str:
        if self.stage_occurrence_id is None:
            raise ValueError("Stage occurrence is required for a stage event key")
        return f"{event_type}:occurrence:{self.stage_occurrence_id}"


class RetryPolicy:
    max_attempts = 4

    @classmethod
    def classify(cls, error: Exception, attempt: int) -> ErrorDecision:
        if is_persistence_input_error(error):
            return ErrorDecision(
                code=PERSISTENCE_INPUT_INVALID,
                retryable=False,
                delay_seconds=None,
            )
        if isinstance(error, ProviderError):
            retryable = (
                error.category in {"rate_limited", "timeout", "unavailable"}
                and attempt < cls.max_attempts
            )
            exponential_delay = min(900, 15 * (2 ** max(0, attempt - 1)))
            provider_delay = int(error.retry_after or 0)
            return ErrorDecision(
                code=error.error_code[:100],
                retryable=retryable,
                delay_seconds=(
                    max(exponential_delay, provider_delay) if retryable else None
                ),
            )
        retryable_error = isinstance(
            error,
            (
                ConnectionError,
                TimeoutError,
                InterfaceError,
                OperationalError,
                KombuOperationalError,
            ),
        )
        retryable = retryable_error and attempt < cls.max_attempts
        delay = min(900, 15 * (2 ** max(0, attempt - 1))) if retryable else None
        return ErrorDecision(
            code=f"{error.__class__.__module__}.{error.__class__.__name__}"[:100],
            retryable=retryable,
            delay_seconds=delay,
        )


class PipelineRunService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(
        self,
        project_id: uuid.UUID,
        idempotency_key: str,
        trigger_type: TriggerType = TriggerType.api,
        metadata: dict | None = None,
    ) -> tuple[PipelineRun, bool]:
        idempotency_key = sanitize_nul(idempotency_key)
        metadata = sanitize_nul(metadata or {})
        project = await self.db.scalar(
            select(Project).where(Project.id == project_id).with_for_update()
        )
        if project is None:
            raise ValueError("Project not found")
        existing = await self.db.scalar(
            select(PipelineRun).where(
                PipelineRun.project_id == project_id,
                PipelineRun.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing, False
        active = await self.db.scalar(
            select(PipelineRun)
            .where(
                PipelineRun.project_id == project_id,
                PipelineRun.status.in_(
                    [
                        PipelineRunStatus.queued,
                        PipelineRunStatus.running,
                        PipelineRunStatus.waiting_retry,
                        PipelineRunStatus.needs_human_approval,
                    ]
                ),
            )
            .order_by(PipelineRun.created_at.desc())
            .limit(1)
        )
        if active:
            return active, False
        metadata[PROJECT_STATE_BEFORE_RUN_KEY] = await self._project_state_snapshot(
            project
        )
        pipeline_version = getattr(
            project.editorial_pipeline_version,
            "value",
            project.editorial_pipeline_version,
        )
        initial_stage = "content_contract" if pipeline_version == "v3" else "planner"
        run = PipelineRun(
            project_id=project_id,
            status=PipelineRunStatus.queued,
            trigger_type=trigger_type,
            current_stage=initial_stage,
            idempotency_key=idempotency_key,
            metadata_json=metadata,
        )
        self.db.add(run)
        await self.db.flush()
        from app.services.execution_manifest import ExecutionManifestService

        manifest_service = ExecutionManifestService(self.db)
        await manifest_service.create(run, project)
        await manifest_service.required(run.id, project_id=project.id)
        self.db.add(
            PipelineStateTransition(
                pipeline_run_id=run.id,
                from_status="none",
                to_status=PipelineRunStatus.queued.value,
                stage=initial_stage,
                origin="run.create",
                reason="Pipeline run created",
            )
        )
        project.current_stage = initial_stage
        await self.db.flush()
        return run, True

    async def _project_state_snapshot(self, project: Project) -> dict[str, str]:
        status = ProjectStatus(project.status)
        stage = project.current_stage
        if status not in _TRANSIENT_PROJECT_STATUSES:
            return {"status": status.value, "current_stage": stage}

        previous = await self.db.scalar(
            select(PipelineRun)
            .where(
                PipelineRun.project_id == project.id,
                PipelineRun.status.in_(TERMINAL_RUN_STATUSES),
            )
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
            .limit(1)
        )
        snapshot = self._stored_project_snapshot(previous)
        if snapshot is not None:
            return snapshot
        previous_status = (
            PipelineRunStatus(previous.status) if previous is not None else None
        )
        stable_status = _TERMINAL_RUN_PROJECT_STATUSES.get(
            previous_status, ProjectStatus.draft
        )
        return {
            "status": stable_status.value,
            "current_stage": previous.current_stage if previous is not None else "planner",
        }

    @staticmethod
    def _stored_project_snapshot(run: PipelineRun | None) -> dict[str, str] | None:
        if run is None or not isinstance(run.metadata_json, dict):
            return None
        raw = run.metadata_json.get(PROJECT_STATE_BEFORE_RUN_KEY)
        if not isinstance(raw, dict):
            return None
        try:
            status = ProjectStatus(raw.get("status"))
        except (TypeError, ValueError):
            return None
        stage = raw.get("current_stage")
        if status in _TRANSIENT_PROJECT_STATUSES or not isinstance(stage, str):
            return None
        stage = stage.strip()
        if not stage:
            return None
        return {"status": status.value, "current_stage": stage}

    async def restore_project_after_cancellation(self, run: PipelineRun) -> bool:
        """Restore editorial state only when this cancellation is the latest run."""
        if PipelineRunStatus(run.status) != PipelineRunStatus.cancelled:
            return False
        latest = await self.db.scalar(
            select(PipelineRun)
            .where(PipelineRun.project_id == run.project_id)
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
            .limit(1)
        )
        if latest is None or latest.id != run.id:
            return False
        project = await self.db.scalar(
            select(Project).where(Project.id == run.project_id).with_for_update()
        )
        if project is None:
            return False

        snapshot = self._stored_project_snapshot(run)
        if snapshot is None:
            previous = await self.db.scalar(
                select(PipelineRun)
                .where(
                    PipelineRun.project_id == run.project_id,
                    PipelineRun.id != run.id,
                    PipelineRun.status != PipelineRunStatus.cancelled,
                    PipelineRun.status.in_(TERMINAL_RUN_STATUSES),
                )
                .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
                .limit(1)
            )
            previous_status = (
                PipelineRunStatus(previous.status) if previous is not None else None
            )
            stable_status = _TERMINAL_RUN_PROJECT_STATUSES.get(
                previous_status, ProjectStatus.draft
            )
            snapshot = {
                "status": stable_status.value,
                "current_stage": (
                    previous.current_stage if previous is not None else "planner"
                ),
            }

        project.status = ProjectStatus(snapshot["status"])
        project.current_stage = snapshot["current_stage"]
        await self.db.flush()
        return True

    async def acquire(self, run_id: uuid.UUID) -> PipelineRun:
        run = await self.db.scalar(
            select(PipelineRun)
            .where(PipelineRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if run is None:
            raise ValueError("Pipeline run not found")
        dialect = self.db.bind.dialect.name if self.db.bind else ""
        if dialect == "postgresql":
            lock_id = int.from_bytes(
                hashlib.sha256(run.id.bytes).digest()[:8], "big", signed=True
            )
            acquired = await self.db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id}
            )
            if not acquired:
                raise PipelineRunBusy(str(run.id))
        return run

    async def claim(
        self, run_id: uuid.UUID, owner: str, lease_seconds: int = 1800
    ) -> PipelineRun:
        run = await self.acquire(run_id)
        now = datetime.now(timezone.utc)
        expires_at = run.lease_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            run.lease_owner
            and run.lease_owner != owner
            and expires_at
            and expires_at > now
        ):
            raise PipelineRunBusy(str(run.id))
        run.lease_owner = owner
        run.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self.db.flush()
        return run

    async def renew_lease(
        self, run_id: uuid.UUID, owner: str, lease_seconds: int = 1800
    ) -> PipelineRun:
        run = await self.acquire(run_id)
        self._validate_lease(run, owner)
        run.lease_expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=lease_seconds
        )
        await self.db.flush()
        return run

    async def release_lease(self, run_id: uuid.UUID, owner: str) -> PipelineRun:
        run = await self.acquire(run_id)
        if run.lease_owner == owner:
            run.lease_owner = None
            run.lease_expires_at = None
            await self.db.flush()
        return run

    @staticmethod
    def _validate_lease(run: PipelineRun, expected_owner: str | None) -> None:
        if expected_owner is None:
            return
        expires_at = run.lease_expires_at
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if (
            run.lease_owner != expected_owner
            or expires_at is None
            or expires_at <= datetime.now(timezone.utc)
        ):
            raise PipelineRunBusy(str(run.id))

    async def reap_expired_lease(
        self, run_id: uuid.UUID, now: datetime | None = None
    ) -> PipelineRun | None:
        now = now or datetime.now(timezone.utc)
        try:
            run = await self.acquire(run_id)
        except ValueError:
            return None
        if run.status != PipelineRunStatus.running:
            return None
        expires_at = run.lease_expires_at
        if expires_at is None:
            return None
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at >= now:
            return None

        if run.cancellation_requested_at is not None:
            return await self._honor_cancellation_locked(
                run,
                origin="celery.beat.lease-reaper",
                reason="Cancellation honored after the worker lease expired",
            )

        expired_owner = run.lease_owner
        run.error_code = "worker.lease_expired"
        run.error_message = "Worker lease expired before the run reached a checkpoint"
        run.retryable = True
        run.next_retry_at = now
        run.lease_owner = None
        run.lease_expires_at = None
        await self._transition_locked(
            run,
            PipelineRunStatus.waiting_retry,
            origin="celery.beat.lease-reaper",
            reason=run.error_message,
            error_code=run.error_code,
        )
        events = EventService(self.db)
        context = await events.latest_stage_context(
            run.id, run.current_stage, run.attempt
        )
        await events.append(
            run.project_id,
            run.id,
            "pipeline.lease_expired",
            run.current_stage,
            {
                "message": run.error_message,
                "retryable": True,
                "expired_lease_owner": expired_owner,
            },
            idempotency_key=f"pipeline.lease_expired:{run.attempt}",
            context=context,
        )
        await events.record_stage_failure(
            run, run.error_message, retryable=True
        )
        await self.db.flush()
        return run

    async def transition(
        self,
        run_id: uuid.UUID,
        target: PipelineRunStatus,
        *,
        origin: str,
        reason: str | None = None,
        stage: str | None = None,
        error_code: str | None = None,
        expected_lease_owner: str | None = None,
        expected_lock_version: int | None = None,
    ) -> PipelineRun:
        """Acquire the run row and apply a validated transition."""
        run = await self.acquire(run_id)
        self._validate_lease(run, expected_lease_owner)
        if (
            expected_lock_version is not None
            and run.lock_version != expected_lock_version
        ):
            raise PipelineRunVersionConflict(
                f"Pipeline run {run.id} version is {run.lock_version}, "
                f"expected {expected_lock_version}"
            )
        return await self._transition_locked(
            run,
            target,
            origin=origin,
            reason=reason,
            stage=stage,
            error_code=error_code,
        )

    async def request_cancellation(
        self,
        run_id: uuid.UUID,
        *,
        origin: str,
        reason: str = "Cancellation requested by an administrator",
    ) -> PipelineRun:
        run = await self.acquire(run_id)
        current = PipelineRunStatus(run.status)
        if current == PipelineRunStatus.cancelled:
            if run.cancellation_requested_at is None:
                run.cancellation_requested_at = datetime.now(timezone.utc)
            await self.restore_project_after_cancellation(run)
            await self.db.flush()
            return run
        if current in TERMINAL_RUN_STATUSES:
            raise InvalidRunTransition(f"{current.value} -> cancelled")
        if run.cancellation_requested_at is not None:
            return run

        run.cancellation_requested_at = datetime.now(timezone.utc)
        reason = sanitize_nul(reason)
        if current in {
            PipelineRunStatus.queued,
            PipelineRunStatus.waiting_retry,
        }:
            self._invalidate_dispatch_and_retry(run)
            await self._transition_locked(
                run,
                PipelineRunStatus.cancelled,
                origin=origin,
                reason=reason,
            )
            await self.restore_project_after_cancellation(run)
            await self._append_cancellation_event(
                run,
                "pipeline.cancelled",
                "Pipeline run cancelled before execution",
                reason=reason,
                actor=origin,
            )
        else:
            await self._append_cancellation_event(
                run,
                "pipeline.cancellation_requested",
                "Cancellation requested; waiting for a safe boundary",
                reason=reason,
                actor=origin,
            )
        await self.db.flush()
        return run

    async def honor_cancellation(
        self,
        run_id: uuid.UUID,
        *,
        origin: str,
        reason: str = "Cancellation honored at a safe boundary",
        expected_lease_owner: str | None = None,
    ) -> PipelineRun | None:
        run = await self.acquire(run_id)
        if PipelineRunStatus(run.status) == PipelineRunStatus.cancelled:
            await self.restore_project_after_cancellation(run)
            return run
        if run.cancellation_requested_at is None:
            return None
        self._validate_lease(run, expected_lease_owner)
        return await self._honor_cancellation_locked(
            run,
            origin=origin,
            reason=reason,
        )

    async def _honor_cancellation_locked(
        self,
        run: PipelineRun,
        *,
        origin: str,
        reason: str,
    ) -> PipelineRun:
        current = PipelineRunStatus(run.status)
        if current == PipelineRunStatus.cancelled:
            return run
        if run.cancellation_requested_at is None:
            raise PipelineCancellationRequested(str(run.id))
        if current in TERMINAL_RUN_STATUSES:
            raise InvalidRunTransition(f"{current.value} -> cancelled")
        self._invalidate_dispatch_and_retry(run)
        await self._transition_locked(
            run,
            PipelineRunStatus.cancelled,
            origin=origin,
            reason=reason,
        )
        await self.restore_project_after_cancellation(run)
        await self._append_cancellation_event(
            run,
            "pipeline.cancelled",
            "Pipeline run cancelled at a safe boundary",
            reason=reason,
            actor=origin,
        )
        await self.db.flush()
        return run

    @staticmethod
    def _invalidate_dispatch_and_retry(run: PipelineRun) -> None:
        run.retryable = False
        run.next_retry_at = None
        run.dispatch_token = None
        run.dispatch_status = None
        run.dispatch_claimed_by = None
        run.dispatch_claimed_at = None
        run.dispatch_expires_at = None
        run.dispatch_not_before = None
        run.last_dispatch_error = None
        run.celery_task_id = None
        run.lease_owner = None
        run.lease_expires_at = None

    async def _append_cancellation_event(
        self,
        run: PipelineRun,
        event_type: str,
        message: str,
        *,
        reason: str,
        actor: str,
    ) -> None:
        await EventService(self.db).append(
            run.project_id,
            run.id,
            event_type,
            run.current_stage,
            {
                "message": message,
                "status": run.status.value,
                "reason": reason,
                "actor": actor,
            },
            idempotency_key=event_type,
        )

    async def _transition_locked(
        self,
        run: PipelineRun,
        target: PipelineRunStatus,
        *,
        origin: str,
        reason: str | None = None,
        stage: str | None = None,
        error_code: str | None = None,
    ) -> PipelineRun:
        """Apply a transition to a row already locked in this transaction."""
        current = PipelineRunStatus(run.status)
        if target == current:
            return run
        if (
            run.cancellation_requested_at is not None
            and target != PipelineRunStatus.cancelled
        ):
            raise PipelineCancellationRequested(str(run.id))
        if target not in ALLOWED_RUN_TRANSITIONS[current]:
            raise InvalidRunTransition(f"{current.value} -> {target.value}")
        now = datetime.now(timezone.utc)
        run.status = target
        run.current_stage = stage or run.current_stage
        run.lock_version += 1
        if target == PipelineRunStatus.running and run.started_at is None:
            run.started_at = now
        if target in {
            PipelineRunStatus.completed,
            PipelineRunStatus.needs_review,
            PipelineRunStatus.needs_human_approval,
            PipelineRunStatus.rejected,
        }:
            run.finished_at = now
            run.failed_at = None
            run.error_code = None
            run.error_message = None
            run.retryable = False
            run.next_retry_at = None
        if target == PipelineRunStatus.blocked:
            run.finished_at = now
            run.failed_at = None
            run.error_code = error_code or "PIPELINE_QUALITY_BLOCKED"
            run.error_message = sanitize_nul(reason) if reason else None
            run.retryable = False
            run.next_retry_at = None
        if target == PipelineRunStatus.failed:
            run.failed_at = now
            run.finished_at = now
        if target == PipelineRunStatus.cancelled:
            run.finished_at = now
            run.failed_at = None
            run.error_code = None
            run.error_message = None
            run.retryable = False
            run.next_retry_at = None
        self.db.add(
            PipelineStateTransition(
                pipeline_run_id=run.id,
                from_status=current.value,
                to_status=target.value,
                stage=run.current_stage,
                origin=origin,
                reason=reason,
                error_code=error_code,
            )
        )
        await self.db.flush()
        return run

    async def record_failure(
        self,
        run_id: uuid.UUID,
        error: Exception,
        origin: str,
        *,
        expected_lease_owner: str | None = None,
        expected_lock_version: int | None = None,
    ) -> tuple[PipelineRun, ErrorDecision]:
        run = await self.acquire(run_id)
        self._validate_lease(run, expected_lease_owner)
        if (
            expected_lock_version is not None
            and run.lock_version != expected_lock_version
        ):
            raise PipelineRunVersionConflict(
                f"Pipeline run {run.id} version is {run.lock_version}, "
                f"expected {expected_lock_version}"
            )
        decision = RetryPolicy.classify(error, run.attempt)
        run.error_code = decision.code
        run.error_message = (
            error.public_message
            if isinstance(error, ProviderError)
            else PUBLIC_ERROR_MESSAGE
        )
        run.retryable = decision.retryable
        if decision.retryable:
            run.next_retry_at = datetime.now(timezone.utc) + timedelta(
                seconds=decision.delay_seconds or 0
            )
            await self._transition_locked(
                run,
                PipelineRunStatus.waiting_retry,
                origin=origin,
                reason=run.error_message,
                error_code=decision.code,
            )
        else:
            await self._transition_locked(
                run,
                PipelineRunStatus.failed,
                origin=origin,
                reason=run.error_message,
                error_code=decision.code,
            )
        return run, decision


class EventService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def latest_stage_context(
        self, pipeline_run_id: uuid.UUID, stage: str, run_attempt: int
    ) -> EventContext | None:
        event = await self.db.scalar(
            select(PipelineEvent)
            .where(
                PipelineEvent.pipeline_run_id == pipeline_run_id,
                PipelineEvent.event_type == "stage.started",
                PipelineEvent.stage == stage,
                PipelineEvent.run_attempt == run_attempt,
            )
            .order_by(PipelineEvent.sequence.desc())
            .limit(1)
        )
        if event is None or event.stage_occurrence_id is None:
            return None
        return EventContext(
            stage_occurrence_id=event.stage_occurrence_id,
            research_cycle=event.research_cycle,
            editor_cycle=event.editor_cycle,
            run_attempt=event.run_attempt,
            stage_attempt=event.stage_attempt,
            checkpoint_sequence=event.checkpoint_sequence,
            agent_run_id=event.agent_run_id,
        )

    async def record_stage_failure(
        self,
        run: PipelineRun,
        message: str,
        *,
        retryable: bool,
        error_code: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        context = await self.latest_stage_context(run.id, run.current_stage, run.attempt)
        if context is None:
            return
        await self.append(
            run.project_id,
            run.id,
            "stage.failed",
            run.current_stage,
            {
                "message": message[:1000],
                "retryable": retryable,
                "error_code": error_code,
                "correlation_id": correlation_id,
            },
            idempotency_key=context.event_key("stage.failed"),
            context=context,
        )
        if retryable:
            await self.append(
                run.project_id,
                run.id,
                "stage.retry_scheduled",
                run.current_stage,
                {
                    "message": message[:1000],
                    "next_retry_at": (
                        run.next_retry_at.isoformat() if run.next_retry_at else None
                    ),
                },
                idempotency_key=context.event_key("stage.retry_scheduled"),
                context=context,
            )

    async def append(
        self,
        project_id: uuid.UUID,
        pipeline_run_id: uuid.UUID | None,
        event_type: str,
        stage: str,
        payload: dict,
        idempotency_key: str | None = None,
        context: EventContext | None = None,
    ) -> PipelineEvent:
        event_type = sanitize_nul(event_type)
        stage = sanitize_nul(stage)
        payload = sanitize_nul(payload)
        idempotency_key = sanitize_nul(idempotency_key)
        project = await self.db.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise ValueError("Project not found")
        if pipeline_run_id and idempotency_key:
            existing = await self.db.scalar(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == pipeline_run_id,
                    PipelineEvent.idempotency_key == idempotency_key,
                )
            )
            if existing:
                return existing
        context = context or EventContext()
        try:
            async with self.db.begin_nested():
                project.event_sequence += 1
                event = PipelineEvent(
                    project_id=project_id,
                    pipeline_run_id=pipeline_run_id,
                    sequence=project.event_sequence,
                    event_type=event_type,
                    stage=stage,
                    stage_occurrence_id=context.stage_occurrence_id,
                    research_cycle=context.research_cycle,
                    editor_cycle=context.editor_cycle,
                    run_attempt=context.run_attempt,
                    stage_attempt=context.stage_attempt,
                    checkpoint_sequence=context.checkpoint_sequence,
                    agent_run_id=context.agent_run_id,
                    payload=payload,
                    idempotency_key=idempotency_key,
                )
                self.db.add(event)
                await self.db.flush()
            return event
        except IntegrityError:
            if not pipeline_run_id or not idempotency_key:
                raise
            existing = await self.db.scalar(
                select(PipelineEvent).where(
                    PipelineEvent.pipeline_run_id == pipeline_run_id,
                    PipelineEvent.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            return existing


class CheckpointService:
    contract_version = "1.0"

    def __init__(self, db: AsyncSession):
        self.db = db

    async def save(
        self,
        run: PipelineRun,
        completed_stage: str,
        next_stage: str,
        state: dict,
        result: dict | None = None,
        resumable: bool = True,
        event_context: EventContext | None = None,
        idempotency_suffix: str | None = None,
    ) -> PipelineCheckpoint:
        completed_stage = sanitize_nul(completed_stage)
        next_stage = sanitize_nul(next_stage)
        state = sanitize_nul(state)
        result = sanitize_nul(result or {})
        locked_run = await self.db.scalar(
            select(PipelineRun)
            .where(PipelineRun.id == run.id)
            .with_for_update()
        )
        if locked_run is None:
            raise ValueError("Pipeline run not found")
        key = self.idempotency_key(
            completed_stage,
            locked_run.attempt,
            self.contract_version,
            state,
            idempotency_suffix=idempotency_suffix,
        )
        existing = await self.db.scalar(
            select(PipelineCheckpoint).where(
                PipelineCheckpoint.pipeline_run_id == run.id,
                PipelineCheckpoint.idempotency_key == key,
            )
        )
        if existing:
            await self._record_event(locked_run, existing, event_context)
            return existing
        locked_run.checkpoint_sequence += 1
        checkpoint = PipelineCheckpoint(
            pipeline_run_id=locked_run.id,
            stage=completed_stage,
            sequence=locked_run.checkpoint_sequence,
            attempt=locked_run.attempt,
            contract_version=self.contract_version,
            next_stage=next_stage,
            state_json=state,
            result_json=result,
            resumable=resumable,
            idempotency_key=key,
        )
        self.db.add(checkpoint)
        locked_run.last_successful_checkpoint = completed_stage
        locked_run.current_stage = next_stage
        await self.db.flush()
        await self._record_event(locked_run, checkpoint, event_context)
        return checkpoint

    async def _record_event(
        self,
        run: PipelineRun,
        checkpoint: PipelineCheckpoint,
        event_context: EventContext | None,
    ) -> None:
        context = (
            event_context.with_checkpoint(checkpoint.sequence)
            if event_context
            else EventContext(
                run_attempt=checkpoint.attempt,
                checkpoint_sequence=checkpoint.sequence,
            )
        )
        await EventService(self.db).append(
            run.project_id,
            run.id,
            "checkpoint.created",
            checkpoint.stage,
            {
                "checkpoint_id": str(checkpoint.id),
                "sequence": checkpoint.sequence,
                "next_stage": checkpoint.next_stage,
                "resumable": checkpoint.resumable,
            },
            idempotency_key=f"checkpoint.created:{checkpoint.id}",
            context=context,
        )

    async def latest(self, run_id: uuid.UUID) -> PipelineCheckpoint | None:
        return await self.db.scalar(
            select(PipelineCheckpoint)
            .where(
                PipelineCheckpoint.pipeline_run_id == run_id,
                PipelineCheckpoint.resumable.is_(True),
            )
            .order_by(PipelineCheckpoint.sequence.desc())
            .limit(1)
        )

    @staticmethod
    def idempotency_key(
        completed_stage: str,
        run_attempt: int,
        contract_version: str,
        state: dict,
        *,
        idempotency_suffix: str | None = None,
    ) -> str:
        research_cycle = int(state.get("research_cycle", 0))
        editor_cycle = int(state.get("editor_cycle", 0))
        key = (
            f"{completed_stage}:research-cycle-{research_cycle}:"
            f"editor-cycle-{editor_cycle}:run-attempt-{run_attempt}:"
            f"contract-{contract_version}"
        )
        if idempotency_suffix:
            key += f":{sanitize_nul(idempotency_suffix)}"
        return key
