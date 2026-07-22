import os
import uuid

import pytest
from redis import Redis as SyncRedis
from redis.asyncio import Redis

from app.core.config import settings
from app.services.operational_heartbeat import HEARTBEAT_KEYS, record_heartbeat
from app.services.readiness import redis_component_states
from app.services.style_learning import StyleLearningService

pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_REDIS_URL"),
    reason="TEST_REDIS_URL is required for Redis integration tests",
)


@pytest.mark.asyncio
async def test_style_discovery_respects_real_distributed_lock(monkeypatch):
    project_id = uuid.uuid4()
    client = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    key = f"style-discovery:{project_id}"
    await client.set(key, "another-worker", ex=30)

    class FakeDb:
        async def get(self, _model, _identifier):
            return object()

    monkeypatch.setattr("app.services.agent_runtime.CredentialVault", lambda: object())
    monkeypatch.setattr(
        "app.services.style_learning.settings.redis_url", os.environ["TEST_REDIS_URL"]
    )
    try:
        result = await StyleLearningService(FakeDb()).discover(project_id)
        assert result == {"status": "already-running", "patterns": 0}
        assert await client.get(key) == "another-worker"
    finally:
        await client.delete(key)
        await client.aclose()


@pytest.mark.asyncio
async def test_operational_heartbeats_use_real_redis_ttl(monkeypatch):
    redis_url = os.environ["TEST_REDIS_URL"]
    async_client = Redis.from_url(redis_url, decode_responses=True)
    sync_client = SyncRedis.from_url(redis_url, decode_responses=True)
    now = 1_700_000_000.0
    monkeypatch.setattr(settings, "redis_url", redis_url)
    try:
        assert record_heartbeat("worker", client=sync_client, timestamp=now)
        assert record_heartbeat("beat", client=sync_client, timestamp=now)
        states = await redis_component_states(settings, now=now)

        assert states == {
            "redis": "ready",
            "worker": "ready",
            "beat": "ready",
        }
        for key in HEARTBEAT_KEYS.values():
            ttl = await async_client.ttl(key)
            assert 0 < ttl <= settings.operational_heartbeat_ttl_seconds
    finally:
        await async_client.delete(*HEARTBEAT_KEYS.values())
        await async_client.aclose()
        sync_client.close()
