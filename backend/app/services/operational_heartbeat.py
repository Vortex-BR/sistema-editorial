from __future__ import annotations

import os
import socket
import time
from functools import lru_cache
from typing import Literal

from celery.signals import heartbeat_sent, worker_ready
from redis import Redis

from app.core.build_info import load_build_info
from app.core.config import settings


HeartbeatComponent = Literal["worker", "beat"]
HEARTBEAT_KEYS = {
    "worker": "seo:operational:heartbeats:v2:worker",
    "beat": "seo:operational:heartbeats:v2:beat",
}
LEGACY_HEARTBEAT_KEYS = {
    "worker": "seo:operational:heartbeat:worker",
    "beat": "seo:operational:heartbeat:beat",
}


@lru_cache(maxsize=4)
def _client(redis_url: str) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
    )


def heartbeat_identity(component: HeartbeatComponent) -> str:
    build = load_build_info(settings)
    return (
        f"{component}:{build.commit_sha}:{socket.gethostname()}:{os.getpid()}"
    )[:240]


def record_heartbeat(
    component: HeartbeatComponent,
    *,
    client: Redis | None = None,
    timestamp: float | None = None,
    ttl_seconds: int | None = None,
    identity: str | None = None,
) -> bool:
    try:
        target = client or _client(settings.redis_url)
        recorded_at = timestamp if timestamp is not None else time.time()
        ttl = ttl_seconds or settings.operational_heartbeat_ttl_seconds
        member = identity or heartbeat_identity(component)
        pipeline = target.pipeline(transaction=True)
        pipeline.zadd(HEARTBEAT_KEYS[component], {member: recorded_at})
        pipeline.zremrangebyscore(
            HEARTBEAT_KEYS[component], "-inf", recorded_at - ttl
        )
        pipeline.expire(HEARTBEAT_KEYS[component], ttl)
        pipeline.execute()
    except Exception:
        return False
    return True


def record_worker_heartbeat(**_kwargs) -> bool:
    return record_heartbeat("worker")


def record_beat_heartbeat() -> bool:
    return record_heartbeat("beat")


def register_heartbeat_signals() -> None:
    worker_ready.connect(
        record_worker_heartbeat,
        weak=False,
        dispatch_uid="operational-worker-ready-heartbeat",
    )
    heartbeat_sent.connect(
        record_worker_heartbeat,
        weak=False,
        dispatch_uid="operational-worker-periodic-heartbeat",
    )
