import hashlib
import json
import re
import secrets
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings


WEBSOCKET_SUBPROTOCOL = "seo-events"
WEBSOCKET_TICKET_TTL_SECONDS = 60
_TICKET_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_TICKET_KEY_PREFIX = "websocket-ticket:"


class WebSocketTicketUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class IssuedWebSocketTicket:
    value: str
    expires_in: int = WEBSOCKET_TICKET_TTL_SECONDS
    protocol: str = WEBSOCKET_SUBPROTOCOL


class WebSocketTicketStore:
    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_url(cls, redis_url: str) -> "WebSocketTicketStore":
        return cls(
            Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
        )

    @staticmethod
    def _key(ticket: str) -> str:
        digest = hashlib.sha256(ticket.encode("ascii")).hexdigest()
        return f"{_TICKET_KEY_PREFIX}{digest}"

    async def issue(
        self, project_id: uuid.UUID, pipeline_run_id: uuid.UUID
    ) -> IssuedWebSocketTicket:
        record = json.dumps(
            {
                "project_id": str(project_id),
                "pipeline_run_id": str(pipeline_run_id),
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        for _attempt in range(3):
            ticket = secrets.token_urlsafe(32)
            try:
                stored = await self.client.set(
                    self._key(ticket),
                    record,
                    ex=WEBSOCKET_TICKET_TTL_SECONDS,
                    nx=True,
                )
            except (RedisError, OSError, TimeoutError) as exc:
                raise WebSocketTicketUnavailable from exc
            if stored:
                return IssuedWebSocketTicket(ticket)
        raise WebSocketTicketUnavailable

    async def consume(
        self,
        ticket: str,
        project_id: uuid.UUID,
        pipeline_run_id: uuid.UUID,
    ) -> bool:
        if not _TICKET_PATTERN.fullmatch(ticket):
            return False
        try:
            stored = await self.client.getdel(self._key(ticket))
        except (RedisError, OSError, TimeoutError) as exc:
            raise WebSocketTicketUnavailable from exc
        if stored is None:
            return False
        try:
            if isinstance(stored, bytes):
                stored = stored.decode("utf-8")
            record = json.loads(stored)
        except (TypeError, UnicodeError, ValueError):
            return False
        if not isinstance(record, dict):
            return False
        return (
            record.get("project_id") == str(project_id)
            and record.get("pipeline_run_id") == str(pipeline_run_id)
        )

    async def close(self) -> None:
        try:
            await self.client.aclose()
        except (RedisError, OSError, TimeoutError):
            return


async def get_websocket_ticket_store() -> AsyncIterator[WebSocketTicketStore]:
    store = WebSocketTicketStore.from_url(settings.redis_url)
    try:
        yield store
    finally:
        await store.close()
