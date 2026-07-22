import os
import uuid

from sqlalchemy import text

from app.db.session import SessionLocal
from app.services.pipeline_control import EventService
from app.workers.async_executor import run_async_task
from app.workers.celery_app import celery


async def _record_connection_probe(
    project_id: uuid.UUID,
    pipeline_run_id: uuid.UUID,
    label: str,
    fail: bool,
):
    worker_pid = os.getpid()
    async with SessionLocal() as db:
        connection = (
            await db.execute(
                text(
                    "SELECT pg_backend_pid() AS backend_pid, backend_start "
                    "FROM pg_stat_activity WHERE pid = pg_backend_pid()"
                )
            )
        ).one()
        payload = {
            "label": label,
            "worker_pid": worker_pid,
            "pg_backend_pid": connection.backend_pid,
            "backend_start": connection.backend_start.isoformat(),
        }
        await EventService(db).append(
            project_id,
            pipeline_run_id,
            "test.celery.loop_probe",
            "test",
            payload,
            idempotency_key=f"test.celery.loop_probe:{label}",
        )
        await db.commit()
    if fail:
        raise RuntimeError(
            f"intentional Celery loop probe failure: {label}; worker_pid={worker_pid}"
        )
    return payload


@celery.task(name="test.celery.loop-probe")
def celery_loop_probe(
    project_id: str,
    pipeline_run_id: str,
    label: str,
    fail: bool = False,
):
    return run_async_task(
        _record_connection_probe(
            uuid.UUID(project_id), uuid.UUID(pipeline_run_id), label, fail
        )
    )
