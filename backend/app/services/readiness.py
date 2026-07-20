from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.services.operational_heartbeat import HEARTBEAT_KEYS, LEGACY_HEARTBEAT_KEYS
from app.services.execution_preflight import inspect_execution_dependencies
from app.services.editorial_roles import normalize_pipeline_version
from app.workers.celery_app import celery


EXPECTED_ALEMBIC_HEAD = "0035"
COMPONENT_ORDER = (
    "postgresql",
    "migrations",
    "vector",
    "redis",
    "broker",
    "worker",
    "beat",
    "configuration",
    "execution_dependencies",
    "skills_mode",
)


@dataclass(frozen=True)
class ReadinessReport:
    components: dict[str, str]

    @property
    def ready(self) -> bool:
        return all(self.components[name] == "ready" for name in COMPONENT_ORDER)

    def safe_payload(self) -> dict[str, object]:
        return {
            "status": "ready" if self.ready else "not_ready",
            "components": {
                name: {"status": self.components[name]} for name in COMPONENT_ORDER
            },
        }


async def _rollback_safely(db: AsyncSession, timeout: float) -> None:
    try:
        await asyncio.wait_for(db.rollback(), timeout=timeout)
    except Exception:
        pass


async def database_component_states(
    db: AsyncSession, config: Settings = settings
) -> dict[str, str]:
    states = {
        "postgresql": "unavailable",
        "migrations": "unknown",
        "vector": "unknown",
    }
    timeout = config.readiness_timeout_seconds
    try:
        await asyncio.wait_for(db.execute(text("SELECT 1")), timeout=timeout)
    except Exception:
        return states
    states["postgresql"] = "ready"

    try:
        versions_result = await asyncio.wait_for(
            db.scalars(text("SELECT version_num FROM alembic_version")),
            timeout=timeout,
        )
        versions = tuple(sorted(str(item) for item in versions_result.all()))
        states["migrations"] = (
            "ready" if versions == (EXPECTED_ALEMBIC_HEAD,) else "outdated"
        )
    except Exception:
        states["migrations"] = "unavailable"
        await _rollback_safely(db, timeout)

    try:
        vector_active = await asyncio.wait_for(
            db.scalar(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
                    ")"
                )
            ),
            timeout=timeout,
        )
        states["vector"] = "ready" if vector_active else "missing"
    except Exception:
        states["vector"] = "unavailable"
    return states


def _heartbeat_state(value: object, *, now: float, max_age_seconds: float) -> str:
    if value is None:
        return "missing"
    try:
        age = max(0.0, now - float(value))
    except (TypeError, ValueError):
        return "invalid"
    return "ready" if age <= max_age_seconds else "stale"


def _heartbeat_instance_state(
    members: object,
    legacy_value: object,
    *,
    now: float,
    max_age_seconds: float,
) -> str:
    active = {
        str(member)
        for member in (members if isinstance(members, (list, tuple, set)) else [])
        if str(member)
    }
    legacy_state = _heartbeat_state(
        legacy_value, now=now, max_age_seconds=max_age_seconds
    )
    if legacy_state == "ready":
        active.add("legacy-instance")
    if not active:
        return "stale" if legacy_state == "stale" else "missing"
    if len(active) > 1:
        return "duplicate"
    return "ready"


async def redis_component_states(
    config: Settings = settings,
    *,
    now: float | None = None,
    client_factory=None,
) -> dict[str, str]:
    states = {
        "redis": "unavailable",
        "worker": "unknown",
        "beat": "unknown",
    }
    factory = client_factory or AsyncRedis.from_url
    client = None
    try:
        client = factory(
            config.redis_url,
            decode_responses=True,
            socket_connect_timeout=config.readiness_timeout_seconds,
            socket_timeout=config.readiness_timeout_seconds,
        )
        await asyncio.wait_for(client.ping(), timeout=config.readiness_timeout_seconds)
        checked_at = now if now is not None else time.time()
        minimum_score = checked_at - config.operational_heartbeat_max_age_seconds
        worker_members, beat_members, legacy_values = await asyncio.gather(
            asyncio.wait_for(
                client.zrangebyscore(HEARTBEAT_KEYS["worker"], minimum_score, "+inf"),
                timeout=config.readiness_timeout_seconds,
            ),
            asyncio.wait_for(
                client.zrangebyscore(HEARTBEAT_KEYS["beat"], minimum_score, "+inf"),
                timeout=config.readiness_timeout_seconds,
            ),
            asyncio.wait_for(
                client.mget(
                    LEGACY_HEARTBEAT_KEYS["worker"],
                    LEGACY_HEARTBEAT_KEYS["beat"],
                ),
                timeout=config.readiness_timeout_seconds,
            ),
        )
    except Exception:
        return states
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass

    states["redis"] = "ready"
    states["worker"] = _heartbeat_instance_state(
        worker_members,
        legacy_values[0],
        now=checked_at,
        max_age_seconds=config.operational_heartbeat_max_age_seconds,
    )
    states["beat"] = _heartbeat_instance_state(
        beat_members,
        legacy_values[1],
        now=checked_at,
        max_age_seconds=config.operational_heartbeat_max_age_seconds,
    )
    return states


def _connect_to_broker(config: Settings) -> None:
    timeout = config.readiness_timeout_seconds
    with celery.connection_for_read(
        url=config.redis_url,
        connect_timeout=timeout,
        transport_options={
            "socket_connect_timeout": timeout,
            "socket_timeout": timeout,
        },
    ) as connection:
        connection.ensure_connection(max_retries=0, timeout=timeout)


async def broker_component_state(config: Settings = settings) -> str:
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_connect_to_broker, config),
            timeout=config.readiness_timeout_seconds + 0.5,
        )
    except Exception:
        return "unavailable"
    return "ready"


async def readiness_report(
    db: AsyncSession,
    *,
    preflight_complete: bool,
    config: Settings = settings,
    pipeline_version: object | None = None,
    require_execution_dependencies: bool = True,
) -> ReadinessReport:
    database_states, redis_states, broker_state = await asyncio.gather(
        database_component_states(db, config),
        redis_component_states(config),
        broker_component_state(config),
    )
    execution_dependencies = "ready" if not require_execution_dependencies else "unknown"
    if (
        require_execution_dependencies
        and database_states.get("postgresql") == "ready"
    ):
        try:
            selected_pipeline = normalize_pipeline_version(
                pipeline_version
                if pipeline_version is not None
                else (
                    "v3"
                    if config.editorial_pipeline_v3_execution_enabled
                    else "v2"
                )
            )
            dependency_report = await inspect_execution_dependencies(
                db,
                selected_pipeline,
                repair_missing_routes=False,
                config=config,
            )
            execution_dependencies = (
                "ready" if dependency_report.ready else "not_ready"
            )
        except Exception:
            execution_dependencies = "unavailable"
    components = {
        **database_states,
        **redis_states,
        "broker": broker_state,
        "configuration": "ready" if preflight_complete else "not_ready",
        "execution_dependencies": execution_dependencies,
        "skills_mode": (
            "ready"
            if not config.is_production or config.superior_skills_mode == "enforced"
            else "not_enforced"
        ),
    }
    return ReadinessReport(components=components)
