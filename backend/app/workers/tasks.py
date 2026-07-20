import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.errors import PUBLIC_ERROR_MESSAGE, new_correlation_id
from app.core.observability import structured_exception_log, structured_log
from app.db.models import (
    AgentMemory,
    Credential,
    CredentialProvider,
    LearningStatus,
    PipelineRun,
    PipelineRunStatus,
    Project,
    ProjectStatus,
    StylePattern,
)
from app.db.session import SessionLocal
from app.orchestration.executor import PipelineExecutor
from app.orchestration.v3.executor import EditorialV3Executor
from app.services.agent_runtime import AgentConfigurationError
from app.services.embeddings import EmbeddingGateway
from app.services.editorial_roles import roles_for_pipeline
from app.services.execution_manifest import (
    ExecutionManifestError,
    ExecutionManifestService,
)
from app.services.pipeline_control import (
    TERMINAL_RUN_STATUSES,
    EventContext,
    EventService,
    PipelineCancellationRequested,
    PipelineRunBusy,
    PipelineRunService,
)
from app.services.pipeline_dispatch import (
    PipelineDispatchRejected,
    PipelineDispatchService,
    dispatch_due_runs,
    dispatcher_identity,
)
from app.services.style_learning import StyleLearningService
from app.workers.async_executor import run_async_task
from app.workers.celery_app import celery


def _project_status_for_run(status: PipelineRunStatus) -> ProjectStatus:
    if status == PipelineRunStatus.completed:
        return ProjectStatus.completed
    if status == PipelineRunStatus.needs_review:
        return ProjectStatus.needs_review
    if status == PipelineRunStatus.needs_human_approval:
        return ProjectStatus.needs_human_approval
    if status == PipelineRunStatus.rejected:
        return ProjectStatus.rejected
    if status == PipelineRunStatus.blocked:
        return ProjectStatus.blocked
    if status == PipelineRunStatus.failed:
        return ProjectStatus.failed
    raise ValueError(f"Run status {status.value} is not terminal")


async def _configuration_gaps(db, pipeline_run_id: uuid.UUID) -> list[str]:
    try:
        manifest = await ExecutionManifestService(db).required(pipeline_run_id)
    except ExecutionManifestError as exc:
        return [f"manifesto:{exc.code}"]
    routes = manifest.data["model_routes"]
    roles = set(routes)
    pipeline_version = str(
        (manifest.data.get("feature_flags") or {}).get("editorial_pipeline_version")
        or "v2"
    )
    required_routes = set(roles_for_pipeline(pipeline_version))
    gaps = [f"rota:{role}" for role in sorted(required_routes - roles)]
    available = set(
        await db.scalars(
            select(Credential.provider).where(
                Credential.active.is_(True),
                Credential.verified_at.is_not(None),
            )
        )
    )
    for provider_name in sorted(
        {
            route["primary_provider"]
            for route in routes.values()
            if route.get("primary_provider")
        }
    ):
        if CredentialProvider(provider_name) not in available:
            gaps.append(f"credencial:llm:{provider_name}")
    search_provider = manifest.data["search_route"].get("provider")
    if not search_provider or CredentialProvider(search_provider) not in available:
        gaps.append("credencial:busca")
    return gaps


async def _honor_requested_cancellation(db, runs, run_id, lease_owner):
    run = await runs.honor_cancellation(
        run_id,
        origin="celery.pipeline.safe-boundary",
        expected_lease_owner=lease_owner,
    )
    if run is None:
        return None
    await db.commit()
    return {
        "project_id": str(run.project_id),
        "pipeline_run_id": str(run.id),
        "status": PipelineRunStatus.cancelled.value,
    }


async def _run(
    run_id: uuid.UUID, lease_owner: str, dispatch_token: uuid.UUID | None = None
):
    async with SessionLocal() as db:
        runs = PipelineRunService(db)
        try:
            run = await PipelineDispatchService(db).claim_for_worker(
                run_id, dispatch_token, lease_owner
            )
        except PipelineDispatchRejected as exc:
            await db.rollback()
            return {"pipeline_run_id": str(run_id), "status": exc.reason}
        project = await db.get(Project, run.project_id)
        if project is None:
            raise ValueError("Project not found")
        project_id = project.id
        if run.status in TERMINAL_RUN_STATUSES:
            terminal_status = run.status.value
            if run.status == PipelineRunStatus.cancelled:
                await runs.restore_project_after_cancellation(run)
            else:
                project.status = _project_status_for_run(run.status)
                project.current_stage = run.current_stage
            await runs.release_lease(run.id, lease_owner)
            await db.commit()
            return {
                "project_id": str(project.id),
                "pipeline_run_id": str(run.id),
                "status": terminal_status,
            }
        if run.status == PipelineRunStatus.waiting_retry:
            run.attempt += 1
        run = await runs.transition(
            run.id,
            PipelineRunStatus.running,
            origin="celery.pipeline.run",
            stage=run.current_stage,
            expected_lease_owner=lease_owner,
            expected_lock_version=run.lock_version,
        )
        structured_log(
            "pipeline.started",
            project_id=project.id,
            pipeline_run_id=run.id,
            stage=run.current_stage,
            attempt=run.attempt,
        )
        await db.commit()
        cancelled_result = await _honor_requested_cancellation(
            db, runs, run.id, lease_owner
        )
        if cancelled_result is not None:
            return cancelled_result
        gaps = await _configuration_gaps(db, run.id)
        cancelled_result = await _honor_requested_cancellation(
            db, runs, run.id, lease_owner
        )
        if cancelled_result is not None:
            return cancelled_result
        if gaps:
            correlation_id = new_correlation_id()
            error = ValueError("Configuração incompleta: " + ", ".join(gaps))
            try:
                run, _ = await runs.record_failure(
                    run.id,
                    error,
                    "configuration.check",
                    expected_lease_owner=lease_owner,
                    expected_lock_version=run.lock_version,
                )
            except PipelineCancellationRequested:
                await db.rollback()
                cancelled_result = await _honor_requested_cancellation(
                    db, runs, run_id, lease_owner
                )
                if cancelled_result is not None:
                    return cancelled_result
                raise
            project.status = ProjectStatus.needs_review
            project.current_stage = "planner"
            await EventService(db).append(
                project.id,
                run.id,
                "pipeline.configuration_required",
                "planner",
                {
                    "message": str(error),
                    "missing": gaps,
                    "error_code": "MODEL_CONFIGURATION_INVALID",
                    "correlation_id": correlation_id,
                },
                idempotency_key=(
                    f"pipeline.configuration_required:planner:run-attempt-{run.attempt}"
                ),
                context=EventContext(
                    research_cycle=0,
                    editor_cycle=0,
                    run_attempt=run.attempt,
                    stage_attempt=1,
                ),
            )
            await runs.release_lease(run.id, lease_owner)
            await db.commit()
            return {
                "project_id": str(project.id),
                "pipeline_run_id": str(run.id),
                "status": "failed",
            }

        try:
            pipeline_version = getattr(
                project.editorial_pipeline_version,
                "value",
                project.editorial_pipeline_version,
            )
            if pipeline_version == "v3":
                state = await EditorialV3Executor(
                    db, project, run, lease_owner
                ).execute()
                project.research_cycles = 0
                project.editor_cycles = 0
            else:
                state = await PipelineExecutor(
                    db, project, run, lease_owner
                ).execute()
                project.research_cycles = state.research_cycle
                project.editor_cycles = state.editor_cycle
            project.status = _project_status_for_run(run.status)
            project.current_stage = run.current_stage
            await runs.release_lease(run.id, lease_owner)
            await db.commit()
            return {
                "project_id": str(project.id),
                "pipeline_run_id": str(run.id),
                "status": run.status.value,
                "stage": state.stage.value,
            }
        except PipelineCancellationRequested:
            await db.rollback()
            cancelled_result = await _honor_requested_cancellation(
                db, runs, run_id, lease_owner
            )
            if cancelled_result is not None:
                return cancelled_result
            raise
        except AgentConfigurationError as exc:
            correlation_id = new_correlation_id()
            await db.rollback()
            cancelled_result = await _honor_requested_cancellation(
                db, runs, run_id, lease_owner
            )
            if cancelled_result is not None:
                return cancelled_result
            try:
                run, _ = await runs.record_failure(
                    run_id,
                    ValueError(str(exc)),
                    "agent.configuration",
                    expected_lease_owner=lease_owner,
                )
            except PipelineCancellationRequested:
                await db.rollback()
                cancelled_result = await _honor_requested_cancellation(
                    db, runs, run_id, lease_owner
                )
                if cancelled_result is not None:
                    return cancelled_result
                raise
            project = await db.get(Project, run.project_id)
            project.status = ProjectStatus.needs_review
            project.current_stage = run.current_stage
            events = EventService(db)
            await events.record_stage_failure(
                run,
                str(exc),
                retryable=False,
                error_code="PROVIDER_CREDENTIAL_MISSING",
                correlation_id=correlation_id,
            )
            context = await events.latest_stage_context(
                run.id, run.current_stage, run.attempt
            )
            await events.append(
                run.project_id,
                run.id,
                "pipeline.configuration_required",
                run.current_stage,
                {
                    "message": str(exc),
                    "error_code": "PROVIDER_CREDENTIAL_MISSING",
                    "correlation_id": correlation_id,
                },
                idempotency_key=(
                    context.event_key("pipeline.configuration_required")
                    if context
                    else f"pipeline.configuration_required:{run.current_stage}:"
                    f"run-attempt-{run.attempt}"
                ),
                context=context,
            )
            await runs.release_lease(run.id, lease_owner)
            await db.commit()
            return {
                "project_id": str(run.project_id),
                "pipeline_run_id": str(run.id),
                "status": "failed",
            }
        except PipelineRunBusy:
            lost_stage = run.current_stage
            lost_attempt = run.attempt
            await db.rollback()
            structured_log(
                "pipeline.lease_lost",
                level=30,
                project_id=project_id,
                pipeline_run_id=run_id,
                stage=lost_stage,
                attempt=lost_attempt,
                error_code="worker.lease_lost",
            )
            return {
                "project_id": str(project_id),
                "pipeline_run_id": str(run_id),
                "status": "lease-lost",
            }
        except Exception as exc:
            correlation_id = new_correlation_id()
            # A failed flush poisons the SQLAlchemy transaction and may expire
            # ORM attributes.  Roll back before touching ``run`` again, or the
            # error handler itself raises PendingRollbackError and leaves the
            # pipeline apparently running until its lease expires.
            await db.rollback()
            cancelled_result = await _honor_requested_cancellation(
                db, runs, run_id, lease_owner
            )
            if cancelled_result is not None:
                return cancelled_result
            run = await runs.acquire(run_id)
            failed_stage = run.current_stage
            failed_attempt = run.attempt
            structured_exception_log(
                "pipeline.failed.internal",
                exc,
                project_id=project_id,
                pipeline_run_id=run_id,
                stage=failed_stage,
                attempt=failed_attempt,
                correlation_id=correlation_id,
            )
            if run.status in TERMINAL_RUN_STATUSES:
                terminal_status = run.status.value
                project = await db.get(Project, run.project_id)
                if run.status == PipelineRunStatus.cancelled:
                    await runs.restore_project_after_cancellation(run)
                else:
                    project.status = _project_status_for_run(run.status)
                    project.current_stage = run.current_stage
                await runs.release_lease(run.id, lease_owner)
                await db.commit()
                return {
                    "project_id": str(run.project_id),
                    "pipeline_run_id": str(run.id),
                    "status": terminal_status,
                }
            try:
                run, decision = await runs.record_failure(
                    run.id,
                    exc,
                    "celery.pipeline.run",
                    expected_lease_owner=lease_owner,
                    expected_lock_version=run.lock_version,
                )
            except PipelineCancellationRequested:
                await db.rollback()
                cancelled_result = await _honor_requested_cancellation(
                    db, runs, run_id, lease_owner
                )
                if cancelled_result is not None:
                    return cancelled_result
                raise
            project = await db.get(Project, run.project_id)
            project.status = "running" if decision.retryable else "failed"
            project.current_stage = run.current_stage
            structured_log(
                "pipeline.failed",
                level=40,
                project_id=run.project_id,
                pipeline_run_id=run.id,
                stage=run.current_stage,
                attempt=run.attempt,
                error_code=decision.code,
            )
            events = EventService(db)
            failure_message = run.error_message or PUBLIC_ERROR_MESSAGE
            await events.record_stage_failure(
                run,
                failure_message,
                retryable=decision.retryable,
                error_code=decision.code,
                correlation_id=correlation_id,
            )
            context = await events.latest_stage_context(
                run.id, run.current_stage, run.attempt
            )
            await events.append(
                run.project_id,
                run.id,
                "pipeline.waiting_retry" if decision.retryable else "pipeline.failed",
                run.current_stage,
                {
                    "message": failure_message,
                    "error_code": decision.code,
                    "correlation_id": correlation_id,
                    "retryable": decision.retryable,
                },
                idempotency_key=f"pipeline.failure:{run.attempt}",
                context=context,
            )
            await runs.release_lease(run.id, lease_owner)
            await db.commit()
            return {
                "project_id": str(run.project_id),
                "pipeline_run_id": str(run.id),
                "status": "waiting_retry" if decision.retryable else "failed",
                "next_retry_at": (
                    run.next_retry_at.isoformat() if run.next_retry_at else None
                ),
            }


@celery.task(
    name="pipeline.run",
    bind=True,
)
def run_pipeline(self, run_id: str, dispatch_token: str | None = None):
    owner = self.request.id or str(uuid.uuid4())
    return run_async_task(
        _run(
            uuid.UUID(run_id),
            owner,
            uuid.UUID(dispatch_token) if dispatch_token else None,
        )
    )


async def _reap_expired_runs(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    async with SessionLocal() as db:
        expired_ids = list(
            await db.scalars(
                select(PipelineRun.id).where(
                    PipelineRun.status == PipelineRunStatus.running,
                    PipelineRun.lease_expires_at.is_not(None),
                    PipelineRun.lease_expires_at < now,
                )
            )
        )
    reaped = 0
    for expired_run_id in expired_ids:
        async with SessionLocal() as db:
            run = await PipelineRunService(db).reap_expired_lease(
                expired_run_id, now
            )
            if run is None:
                await db.rollback()
                continue
            structured_log(
                (
                    "pipeline.cancelled"
                    if run.status == PipelineRunStatus.cancelled
                    else "pipeline.lease_expired"
                ),
                level=20 if run.status == PipelineRunStatus.cancelled else 30,
                project_id=run.project_id,
                pipeline_run_id=run.id,
                stage=run.current_stage,
                attempt=run.attempt,
                error_code=run.error_code,
            )
            await db.commit()
            reaped += 1
    return reaped


async def _resume_due_runs(task_id: str | None = None) -> dict[str, int]:
    reaped = await _reap_expired_runs()
    result = await dispatch_due_runs(
        run_pipeline, dispatcher_identity(task_id, "celery.beat")
    )
    return {"reaped": reaped, **result}


@celery.task(name="pipeline.resume-due", bind=True)
def resume_due_pipeline_runs(self):
    return run_async_task(_resume_due_runs(self.request.id))


async def _discover_style(
    project_id: uuid.UUID, pipeline_run_id: uuid.UUID | None = None
):
    async with SessionLocal() as db:
        try:
            return await StyleLearningService(db).discover(project_id, pipeline_run_id)
        except AgentConfigurationError as exc:
            return {"status": "configuration-required", "message": str(exc)}


@celery.task(
    name="style.discover",
    bind=True,
)
def discover_style_patterns(self, project_id: str, pipeline_run_id: str | None = None):
    return run_async_task(
        _discover_style(
            uuid.UUID(project_id),
            uuid.UUID(pipeline_run_id) if pipeline_run_id else None,
        )
    )


async def _reindex_learning_embeddings():
    async with SessionLocal() as db:
        gateway = EmbeddingGateway()
        memories = (
            await db.scalars(
                select(AgentMemory).where(AgentMemory.status == LearningStatus.approved)
            )
        ).all()
        patterns = (
            await db.scalars(
                select(StylePattern).where(StylePattern.status == LearningStatus.approved)
            )
        ).all()
        updated = 0
        for row, item_text in [
            *((item, item.content) for item in memories),
            *((item, item.description) for item in patterns),
        ]:
            embedding = await gateway.embed(db, item_text)
            if not embedding:
                continue
            row.embedding = embedding.values
            row.embedding_provider = embedding.provider
            row.embedding_model = embedding.model
            row.embedding_dimensions = len(embedding.values)
            updated += 1
        await db.commit()
        return {"updated": updated}


@celery.task(name="learning.reindex-embeddings")
def reindex_learning_embeddings():
    return run_async_task(_reindex_learning_embeddings())
