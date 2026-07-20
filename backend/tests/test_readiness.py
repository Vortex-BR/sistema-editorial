import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routes as routes_module
import app.services.operational_heartbeat as heartbeat_module
import app.services.readiness as readiness_module
import app.workers.beat_scheduler as beat_scheduler_module
from app.api.routes import router
from app.core.config import Settings
from app.db.session import get_db
from app.services.operational_heartbeat import HEARTBEAT_KEYS, record_heartbeat
from app.services.readiness import (
    COMPONENT_ORDER,
    EXPECTED_ALEMBIC_HEAD,
    ReadinessReport,
    broker_component_state,
    database_component_states,
    readiness_report,
    redis_component_states,
)
from app.workers.celery_app import celery


NOW = 1_700_000_000.0
ROOT = Path(__file__).parents[2]


def readiness_settings(**overrides) -> Settings:
    values = {
        "redis_url": "redis://localhost:6379/15",
        "readiness_timeout_seconds": 0.2,
        "operational_heartbeat_max_age_seconds": 20,
        "operational_heartbeat_ttl_seconds": 30,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class ScalarRows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class FakeDatabase:
    def __init__(
        self,
        *,
        postgres_error: bool = False,
        migration_error: bool = False,
        versions=("0035",),
        vector_active: bool = True,
    ):
        self.postgres_error = postgres_error
        self.migration_error = migration_error
        self.versions = versions
        self.vector_active = vector_active
        self.rollback_calls = 0

    async def execute(self, _query):
        if self.postgres_error:
            raise ConnectionError("database unavailable")
        return object()

    async def scalars(self, _query):
        if self.migration_error:
            raise RuntimeError("migration metadata unavailable")
        return ScalarRows(self.versions)

    async def scalar(self, _query):
        return self.vector_active

    async def rollback(self):
        self.rollback_calls += 1


class FakeAsyncRedis:
    def __init__(
        self,
        values=(None, None),
        *,
        members=((), ()),
        unavailable=False,
    ):
        self.values = values
        self.members = members
        self.unavailable = unavailable
        self.closed = False

    async def ping(self):
        if self.unavailable:
            raise ConnectionError("redis unavailable")
        return True

    async def mget(self, *_keys):
        return self.values

    async def zrangebyscore(self, key, _minimum, _maximum):
        index = 0 if key == HEARTBEAT_KEYS["worker"] else 1
        return self.members[index]

    async def aclose(self):
        self.closed = True


def redis_factory(client):
    def factory(*_args, **_kwargs):
        return client

    return factory


def test_expected_alembic_head_matches_repository():
    config = Config(str(ROOT / "backend/alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend/alembic"))

    assert ScriptDirectory.from_config(config).get_current_head() == (
        EXPECTED_ALEMBIC_HEAD
    )


@pytest.mark.asyncio
async def test_postgresql_unavailable_is_not_ready():
    states = await database_component_states(
        FakeDatabase(postgres_error=True), readiness_settings()
    )

    assert states == {
        "postgresql": "unavailable",
        "migrations": "unknown",
        "vector": "unknown",
    }


@pytest.mark.asyncio
async def test_migration_behind_expected_head_is_not_ready():
    states = await database_component_states(
        FakeDatabase(versions=("0015",)), readiness_settings()
    )

    assert states["postgresql"] == "ready"
    assert states["migrations"] == "outdated"
    assert states["vector"] == "ready"


@pytest.mark.asyncio
async def test_vector_extension_missing_is_not_ready():
    states = await database_component_states(
        FakeDatabase(vector_active=False), readiness_settings()
    )

    assert states["postgresql"] == "ready"
    assert states["migrations"] == "ready"
    assert states["vector"] == "missing"


@pytest.mark.asyncio
async def test_redis_unavailable_marks_heartbeats_unknown():
    client = FakeAsyncRedis(unavailable=True)
    states = await redis_component_states(
        readiness_settings(), client_factory=redis_factory(client)
    )

    assert states == {
        "redis": "unavailable",
        "worker": "unknown",
        "beat": "unknown",
    }
    assert client.closed is True


@pytest.mark.asyncio
async def test_invalid_redis_configuration_is_reported_as_unavailable():
    def invalid_factory(*_args, **_kwargs):
        raise ValueError("invalid redis configuration")

    states = await redis_component_states(
        readiness_settings(), client_factory=invalid_factory
    )

    assert states == {
        "redis": "unavailable",
        "worker": "unknown",
        "beat": "unknown",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("values", "component"),
    [((None, str(NOW)), "worker"), ((str(NOW), None), "beat")],
)
async def test_missing_worker_or_beat_heartbeat_is_not_ready(values, component):
    states = await redis_component_states(
        readiness_settings(),
        now=NOW,
        client_factory=redis_factory(FakeAsyncRedis(values)),
    )

    assert states["redis"] == "ready"
    assert states[component] == "missing"


@pytest.mark.asyncio
async def test_expired_heartbeats_are_stale():
    old = str(NOW - 21)
    states = await redis_component_states(
        readiness_settings(),
        now=NOW,
        client_factory=redis_factory(FakeAsyncRedis((old, old))),
    )

    assert states["worker"] == "stale"
    assert states["beat"] == "stale"


@pytest.mark.asyncio
async def test_broker_unavailable_is_not_ready(monkeypatch):
    def unavailable(_config):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(readiness_module, "_connect_to_broker", unavailable)

    assert await broker_component_state(readiness_settings()) == "unavailable"


@pytest.mark.asyncio
async def test_every_healthy_component_produces_ready_without_provider_calls(
    monkeypatch,
):
    async def healthy_database(_db, _config):
        return {
            "postgresql": "ready",
            "migrations": "ready",
            "vector": "ready",
        }

    async def healthy_redis(_config):
        return {"redis": "ready", "worker": "ready", "beat": "ready"}

    async def healthy_broker(_config):
        return "ready"

    async def healthy_execution_dependencies(
        _db, _pipeline_version, *, repair_missing_routes, config
    ):
        assert repair_missing_routes is False
        assert config is not None
        return SimpleNamespace(ready=True)

    def provider_call_forbidden(*_args, **_kwargs):
        raise AssertionError("readiness attempted an external provider call")

    monkeypatch.setattr(readiness_module, "database_component_states", healthy_database)
    monkeypatch.setattr(readiness_module, "redis_component_states", healthy_redis)
    monkeypatch.setattr(readiness_module, "broker_component_state", healthy_broker)
    monkeypatch.setattr(
        readiness_module,
        "inspect_execution_dependencies",
        healthy_execution_dependencies,
    )
    monkeypatch.setattr(httpx, "AsyncClient", provider_call_forbidden)
    config = readiness_settings(app_env="production", superior_skills_mode="enforced")

    report = await readiness_report(object(), preflight_complete=True, config=config)

    assert report.ready is True
    assert report.safe_payload()["status"] == "ready"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preflight_complete", "mode", "component", "state"),
    [
        (False, "enforced", "configuration", "not_ready"),
        (True, "shadow", "skills_mode", "not_enforced"),
    ],
)
async def test_production_preflight_and_enforced_mode_are_required(
    monkeypatch, preflight_complete, mode, component, state
):
    async def healthy_database(_db, _config):
        return {
            "postgresql": "ready",
            "migrations": "ready",
            "vector": "ready",
        }

    async def healthy_redis(_config):
        return {"redis": "ready", "worker": "ready", "beat": "ready"}

    async def healthy_broker(_config):
        return "ready"

    monkeypatch.setattr(readiness_module, "database_component_states", healthy_database)
    monkeypatch.setattr(readiness_module, "redis_component_states", healthy_redis)
    monkeypatch.setattr(readiness_module, "broker_component_state", healthy_broker)

    report = await readiness_report(
        object(),
        preflight_complete=preflight_complete,
        config=readiness_settings(app_env="production", superior_skills_mode=mode),
    )

    assert report.ready is False
    assert report.components[component] == state


def test_heartbeat_write_uses_redis_ttl():
    calls = []

    class FakePipeline:
        def zadd(self, *args, **kwargs):
            calls.append(("zadd", args, kwargs))
            return self

        def zremrangebyscore(self, *args, **kwargs):
            calls.append(("zremrangebyscore", args, kwargs))
            return self

        def expire(self, *args, **kwargs):
            calls.append(("expire", args, kwargs))
            return self

        def execute(self):
            calls.append(("execute", (), {}))

    class FakeSyncRedis:
        def pipeline(self, **_kwargs):
            return FakePipeline()

    assert record_heartbeat(
        "worker",
        client=FakeSyncRedis(),
        timestamp=NOW,
        ttl_seconds=30,
        identity="worker:test-instance",
    )
    assert calls == [
        (
            "zadd",
            (HEARTBEAT_KEYS["worker"], {"worker:test-instance": NOW}),
            {},
        ),
        (
            "zremrangebyscore",
            (HEARTBEAT_KEYS["worker"], "-inf", NOW - 30),
            {},
        ),
        ("expire", (HEARTBEAT_KEYS["worker"], 30), {}),
        ("execute", (), {}),
    ]


@pytest.mark.asyncio
async def test_duplicate_worker_instances_are_not_ready():
    states = await redis_component_states(
        readiness_settings(),
        now=NOW,
        client_factory=redis_factory(
            FakeAsyncRedis(
                values=(None, str(NOW)),
                members=(("worker:a", "worker:b"), ()),
            )
        ),
    )

    assert states["worker"] == "duplicate"
    assert states["beat"] == "ready"


def test_worker_signal_and_beat_scheduler_record_their_own_heartbeat(monkeypatch):
    components = []
    beat_calls = []
    monkeypatch.setattr(
        heartbeat_module,
        "record_heartbeat",
        lambda component: components.append(component) or True,
    )
    monkeypatch.setattr(
        beat_scheduler_module,
        "record_beat_heartbeat",
        lambda: beat_calls.append("beat") or True,
    )
    monkeypatch.setattr(beat_scheduler_module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(
        beat_scheduler_module.settings,
        "operational_heartbeat_interval_seconds",
        5.0,
    )
    monkeypatch.setattr(
        beat_scheduler_module.PersistentScheduler,
        "tick",
        lambda _self, *_args, **_kwargs: 60.0,
    )
    scheduler = object.__new__(beat_scheduler_module.OperationalHeartbeatScheduler)
    scheduler._next_operational_heartbeat = 0.0

    assert heartbeat_module.record_worker_heartbeat() is True
    assert scheduler.tick() == 5.0
    assert components == ["worker"]
    assert beat_calls == ["beat"]
    assert celery.conf.beat_scheduler == (
        "app.workers.beat_scheduler:OperationalHeartbeatScheduler"
    )


@pytest.mark.parametrize("ready", [True, False])
def test_readiness_endpoint_returns_safe_operational_state(monkeypatch, ready):
    components = {name: "ready" for name in COMPONENT_ORDER}
    if not ready:
        components["broker"] = "unavailable"
    report = ReadinessReport(components)

    async def fake_report(_db, *, preflight_complete):
        assert preflight_complete is True
        return report

    async def database_dependency():
        yield object()

    monkeypatch.setattr(routes_module, "readiness_report", fake_report)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database_dependency
    app.state.production_preflight_complete = True

    with TestClient(app) as client:
        response = client.get("/api/v1/readiness")

    assert response.status_code == (200 if ready else 503)
    assert response.json() == report.safe_payload()
    serialized = json.dumps(response.json()).lower()
    for forbidden in (
        "redis://",
        "postgresql://",
        "password",
        "token",
        "internal-host",
    ):
        assert forbidden not in serialized
