from celery import Celery
from app.core.config import settings
from app.services.operational_heartbeat import register_heartbeat_signals


register_heartbeat_signals()

celery = Celery(
    "seo_ledger",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)
celery.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_scheduler="app.workers.beat_scheduler:OperationalHeartbeatScheduler",
    beat_schedule={
        "resume-due-pipeline-runs": {
            "task": "pipeline.resume-due",
            "schedule": settings.pipeline_dispatch_interval_seconds,
        }
    },
)
