import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.websockets import WebSocketDisconnect

import app.api.routes as routes_module
from app.api.routes import project_events, router
from app.core.config import settings
from app.db.models import PipelineRun, Project
from app.db.session import get_db
from app.services.websocket_tickets import (
    IssuedWebSocketTicket,
    WEBSOCKET_SUBPROTOCOL,
    WEBSOCKET_TICKET_TTL_SECONDS,
    WebSocketTicketStore,
    WebSocketTicketUnavailable,
    get_websocket_ticket_store,
)


ADMIN_TOKEN = "websocket-test-administrative-token"
VALID_TICKET = "abcdefghijklmnopqrstuvwxyzABCDEFGH123456789"


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}
        self.getdel_calls = []
        self.closed = False

    async def set(self, key, value, *, ex, nx):
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.ttls[key] = ex
        return True

    async def getdel(self, key):
        self.getdel_calls.append(key)
        self.ttls.pop(key, None)
        return self.values.pop(key, None)

    async def aclose(self):
        self.closed = True


class BrokenRedis:
    async def set(self, *_args, **_kwargs):
        raise RedisConnectionError("unavailable")

    async def getdel(self, *_args, **_kwargs):
        raise RedisConnectionError("unavailable")

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_ticket_is_hashed_short_lived_and_consumed_once(caplog):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    redis = FakeRedis()
    store = WebSocketTicketStore(redis)
    caplog.set_level(logging.DEBUG)

    issued = await store.issue(project_id, pipeline_run_id)

    assert len(issued.value) == 43
    assert issued.expires_in == WEBSOCKET_TICKET_TTL_SECONDS == 60
    assert issued.protocol == WEBSOCKET_SUBPROTOCOL
    assert len(redis.values) == 1
    redis_key = next(iter(redis.values))
    redis_value = redis.values[redis_key]
    assert issued.value not in redis_key
    assert issued.value not in redis_value
    assert ADMIN_TOKEN not in redis_key
    assert ADMIN_TOKEN not in redis_value
    assert redis.ttls[redis_key] == 60
    assert issued.value not in caplog.text
    assert ADMIN_TOKEN not in caplog.text

    assert await store.consume(issued.value, project_id, pipeline_run_id) is True
    assert await store.consume(issued.value, project_id, pipeline_run_id) is False
    assert len(redis.getdel_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_scope", ["project", "run"])
async def test_ticket_with_wrong_scope_is_rejected_and_burned(wrong_scope):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    redis = FakeRedis()
    store = WebSocketTicketStore(redis)
    issued = await store.issue(project_id, pipeline_run_id)

    accepted = await store.consume(
        issued.value,
        uuid.uuid4() if wrong_scope == "project" else project_id,
        uuid.uuid4() if wrong_scope == "run" else pipeline_run_id,
    )

    assert accepted is False
    assert await store.consume(issued.value, project_id, pipeline_run_id) is False


@pytest.mark.asyncio
async def test_expired_and_invalid_tickets_are_rejected():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    redis = FakeRedis()
    store = WebSocketTicketStore(redis)
    issued = await store.issue(project_id, pipeline_run_id)
    redis.values.clear()
    redis.ttls.clear()

    assert await store.consume(issued.value, project_id, pipeline_run_id) is False
    assert await store.consume("invalid-ticket", project_id, pipeline_run_id) is False


@pytest.mark.asyncio
async def test_corrupt_ticket_record_fails_closed_after_atomic_consumption():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    redis = FakeRedis()
    store = WebSocketTicketStore(redis)
    redis.values[store._key(VALID_TICKET)] = "[]"

    assert await store.consume(VALID_TICKET, project_id, pipeline_run_id) is False
    assert redis.values == {}


@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed_for_issue_and_consume():
    store = WebSocketTicketStore(BrokenRedis())
    pipeline_run_id = uuid.uuid4()

    with pytest.raises(WebSocketTicketUnavailable):
        await store.issue(uuid.uuid4(), pipeline_run_id)
    with pytest.raises(WebSocketTicketUnavailable):
        await store.consume(VALID_TICKET, uuid.uuid4(), pipeline_run_id)


class FakeTicketStore:
    def __init__(self, *, unavailable=False):
        self.unavailable = unavailable
        self.issue_calls = []
        self.consume_calls = []
        self.consume_result = True

    async def issue(self, project_id, pipeline_run_id):
        self.issue_calls.append((project_id, pipeline_run_id))
        if self.unavailable:
            raise WebSocketTicketUnavailable
        return IssuedWebSocketTicket(VALID_TICKET)

    async def consume(self, ticket, project_id, pipeline_run_id):
        self.consume_calls.append((ticket, project_id, pipeline_run_id))
        if self.unavailable:
            raise WebSocketTicketUnavailable
        return self.consume_result


class TicketDb:
    def __init__(
        self,
        project_id,
        run_project_id=None,
        *,
        project_exists=True,
        run_exists=True,
    ):
        self.project_id = project_id
        self.run_project_id = run_project_id or project_id
        self.project_exists = project_exists
        self.run_exists = run_exists

    async def get(self, model, _identifier):
        if model is Project:
            return object() if self.project_exists else None
        if model is PipelineRun:
            return SimpleNamespace(project_id=self.run_project_id) if self.run_exists else None
        return None


def _ticket_client(db, store):
    app = FastAPI()
    app.include_router(router)

    async def database_dependency():
        yield db

    async def ticket_store_dependency():
        yield store

    app.dependency_overrides[get_db] = database_dependency
    app.dependency_overrides[get_websocket_ticket_store] = ticket_store_dependency
    return TestClient(app)


def test_ticket_endpoint_validates_scope_and_never_logs_secrets(monkeypatch, caplog):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    store = FakeTicketStore()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    caplog.set_level(logging.DEBUG)

    with _ticket_client(TicketDb(project_id), store) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/events/ticket",
            json={"pipeline_run_id": str(pipeline_run_id)},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 201
    assert response.json() == {
        "ticket": VALID_TICKET,
        "expires_in": 60,
        "protocol": "seo-events",
    }
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert store.issue_calls == [(project_id, pipeline_run_id)]
    assert ADMIN_TOKEN not in str(response.request.url)
    assert VALID_TICKET not in str(response.request.url)
    assert ADMIN_TOKEN not in caplog.text
    assert VALID_TICKET not in caplog.text


def test_ticket_endpoint_rejects_run_from_another_project(monkeypatch):
    project_id = uuid.uuid4()
    store = FakeTicketStore()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _ticket_client(TicketDb(project_id, uuid.uuid4()), store) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/events/ticket",
            json={"pipeline_run_id": str(uuid.uuid4())},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 404
    assert store.issue_calls == []


def test_ticket_endpoint_requires_a_pipeline_run_scope(monkeypatch):
    project_id = uuid.uuid4()
    store = FakeTicketStore()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _ticket_client(TicketDb(project_id), store) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/events/ticket",
            json={},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 422
    assert store.issue_calls == []


@pytest.mark.parametrize(
    ("db"),
    [
        TicketDb(uuid.uuid4(), project_exists=False),
        TicketDb(uuid.uuid4(), run_exists=False),
    ],
    ids=["missing-project", "missing-run"],
)
def test_ticket_endpoint_rejects_missing_scope_entities(monkeypatch, db):
    store = FakeTicketStore()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _ticket_client(db, store) as client:
        response = client.post(
            f"/api/v1/projects/{db.project_id}/events/ticket",
            json={"pipeline_run_id": str(uuid.uuid4())},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 404
    assert store.issue_calls == []


def test_ticket_endpoint_redis_unavailable_fails_closed(monkeypatch):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    store = FakeTicketStore(unavailable=True)
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _ticket_client(TicketDb(project_id), store) as client:
        response = client.post(
            f"/api/v1/projects/{project_id}/events/ticket",
            json={"pipeline_run_id": str(pipeline_run_id)},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 503
    assert response.json() == {
        "detail": "Não foi possível autorizar a conexão em tempo real."
    }


class FakeWebSocket:
    def __init__(
        self,
        protocols=None,
        pipeline_run_id=None,
        subscription=None,
        stop_after_sends=None,
    ):
        self.headers = (
            {"sec-websocket-protocol": protocols} if protocols is not None else {}
        )
        self.query_params = (
            {"pipeline_run_id": str(pipeline_run_id)} if pipeline_run_id else {}
        )
        self.accepted_subprotocol = None
        self.close_code = None
        self.sent = []
        self.subscription = subscription or {"type": "subscribe", "after_sequence": 0}
        self.stop_after_sends = stop_after_sends

    async def accept(self, subprotocol=None):
        self.accepted_subprotocol = subprotocol

    async def close(self, code):
        self.close_code = code

    async def receive_json(self):
        return self.subscription

    async def send_json(self, payload):
        self.sent.append(payload)
        if self.stop_after_sends == len(self.sent):
            raise WebSocketDisconnect()


@pytest.mark.asyncio
async def test_websocket_without_ticket_is_closed_with_policy_violation():
    websocket = FakeWebSocket(
        protocols=WEBSOCKET_SUBPROTOCOL, pipeline_run_id=uuid.uuid4()
    )
    store = FakeTicketStore()

    await project_events(websocket, uuid.uuid4(), store)

    assert websocket.accepted_subprotocol is None
    assert websocket.close_code == 1008
    assert store.consume_calls == []


@pytest.mark.asyncio
async def test_websocket_rejects_an_unstable_subprotocol_before_consumption():
    websocket = FakeWebSocket(
        protocols=f"other-events, {VALID_TICKET}", pipeline_run_id=uuid.uuid4()
    )
    store = FakeTicketStore()

    await project_events(websocket, uuid.uuid4(), store)

    assert websocket.accepted_subprotocol is None
    assert websocket.close_code == 1008
    assert store.consume_calls == []


@pytest.mark.asyncio
async def test_websocket_with_invalid_or_unavailable_ticket_fails_closed():
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    for store in (FakeTicketStore(), FakeTicketStore(unavailable=True)):
        store.consume_result = False
        websocket = FakeWebSocket(
            protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
            pipeline_run_id=pipeline_run_id,
        )

        await project_events(websocket, project_id, store)

        assert websocket.accepted_subprotocol is None
        assert websocket.close_code == 1008


class _Rows:
    def __init__(self, items):
        self.items = items

    def all(self):
        return self.items


@pytest.mark.asyncio
async def test_valid_websocket_accepts_only_stable_protocol_and_sends_no_ticket(
    monkeypatch,
):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    event = SimpleNamespace(
        project_id=project_id,
        sequence=1,
        pipeline_run_id=pipeline_run_id,
        event_type="pipeline.started",
        stage="planner",
        stage_occurrence_id=None,
        research_cycle=None,
        editor_cycle=None,
        run_attempt=1,
        stage_attempt=1,
        checkpoint_sequence=None,
        agent_run_id=None,
        payload={"message": "Execução iniciada."},
        created_at=datetime.now(timezone.utc),
    )

    class EventDb:
        async def scalars(self, _query):
            return _Rows([event])

    class SessionContext:
        async def __aenter__(self):
            return EventDb()

        async def __aexit__(self, *_args):
            return None

    async def stop_after_first_poll(_seconds):
        raise WebSocketDisconnect()

    monkeypatch.setattr(routes_module, "SessionLocal", SessionContext)
    monkeypatch.setattr(routes_module.asyncio, "sleep", stop_after_first_poll)
    store = FakeTicketStore()
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
        pipeline_run_id=pipeline_run_id,
    )

    await project_events(websocket, project_id, store)

    assert websocket.close_code is None
    assert websocket.accepted_subprotocol == WEBSOCKET_SUBPROTOCOL
    assert store.consume_calls == [(VALID_TICKET, project_id, pipeline_run_id)]
    assert len(websocket.sent) == 1
    assert websocket.sent[0]["type"] == "events.batch"
    assert [item["sequence"] for item in websocket.sent[0]["events"]] == [1]
    assert VALID_TICKET not in str(websocket.sent)
    assert ADMIN_TOKEN not in str(websocket.sent)


def _stream_event(project_id, pipeline_run_id, sequence):
    return SimpleNamespace(
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        sequence=sequence,
        event_type=f"pipeline.event.{sequence}",
        stage="planner",
        stage_occurrence_id=None,
        research_cycle=None,
        editor_cycle=None,
        run_attempt=1,
        stage_attempt=1,
        checkpoint_sequence=None,
        agent_run_id=None,
        payload={"message": f"Event {sequence}"},
        created_at=datetime.now(timezone.utc),
    )


def _install_stream_rows(monkeypatch, events):
    queries = []

    class EventDb:
        async def scalars(self, query):
            queries.append(query)
            return _Rows(events)

    class SessionContext:
        async def __aenter__(self):
            return EventDb()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(routes_module, "SessionLocal", SessionContext)
    return queries


@pytest.mark.asyncio
async def test_websocket_requires_a_run_fixed_by_the_ticket():
    project_id = uuid.uuid4()
    store = FakeTicketStore()
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}"
    )

    await project_events(websocket, project_id, store)

    assert websocket.close_code == 1008
    assert store.consume_calls == []


@pytest.mark.asyncio
async def test_batch_excludes_other_runs_and_is_ordered(monkeypatch):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    other_run_id = uuid.uuid4()
    events = [
        _stream_event(project_id, pipeline_run_id, 3),
        _stream_event(project_id, other_run_id, 2),
        _stream_event(project_id, pipeline_run_id, 1),
    ]
    queries = _install_stream_rows(monkeypatch, events)
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
        pipeline_run_id=pipeline_run_id,
        stop_after_sends=1,
    )

    await project_events(websocket, project_id, FakeTicketStore())

    assert [event["sequence"] for event in websocket.sent[0]["events"]] == [1, 3]
    assert {event["pipeline_run_id"] for event in websocket.sent[0]["events"]} == {
        str(pipeline_run_id)
    }
    assert queries[0]._limit_clause.value == 100


@pytest.mark.asyncio
async def test_subscription_cursor_skips_old_events(monkeypatch):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    _install_stream_rows(
        monkeypatch,
        [
            _stream_event(project_id, pipeline_run_id, 2),
            _stream_event(project_id, pipeline_run_id, 7),
        ],
    )
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
        pipeline_run_id=pipeline_run_id,
        subscription={"type": "subscribe", "after_sequence": 5},
        stop_after_sends=1,
    )

    await project_events(websocket, project_id, FakeTicketStore())

    assert websocket.sent[0]["after_sequence"] == 5
    assert [event["sequence"] for event in websocket.sent[0]["events"]] == [7]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "subscription",
    [
        {"type": "subscribe", "after_sequence": -1},
        {"type": "subscribe", "after_sequence": "1"},
        {"type": "subscribe", "after_sequence": True},
        {"type": "subscribe", "after_sequence": 2_147_483_648},
        {
            "type": "subscribe",
            "after_sequence": 0,
            "pipeline_run_id": str(uuid.uuid4()),
        },
    ],
)
async def test_invalid_or_scope_changing_subscription_is_rejected(subscription):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
        pipeline_run_id=pipeline_run_id,
        subscription=subscription,
    )

    await project_events(websocket, project_id, FakeTicketStore())

    assert websocket.accepted_subprotocol == WEBSOCKET_SUBPROTOCOL
    assert websocket.close_code == 1008
    assert websocket.sent == []


@pytest.mark.asyncio
async def test_initial_replay_is_limited_to_the_latest_batch(monkeypatch):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    _install_stream_rows(
        monkeypatch,
        [
            _stream_event(project_id, pipeline_run_id, sequence)
            for sequence in range(1, 151)
        ],
    )
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
        pipeline_run_id=pipeline_run_id,
        stop_after_sends=1,
    )

    await project_events(websocket, project_id, FakeTicketStore())

    sequences = [event["sequence"] for event in websocket.sent[0]["events"]]
    assert sequences == list(range(51, 151))


@pytest.mark.asyncio
async def test_known_cursor_drains_multiple_batches_without_losing_events(monkeypatch):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    queries = _install_stream_rows(
        monkeypatch,
        [
            _stream_event(project_id, pipeline_run_id, sequence)
            for sequence in range(1, 251)
        ],
    )

    async def no_poll_delay(_seconds):
        return None

    monkeypatch.setattr(routes_module.asyncio, "sleep", no_poll_delay)
    websocket = FakeWebSocket(
        protocols=f"{WEBSOCKET_SUBPROTOCOL}, {VALID_TICKET}",
        pipeline_run_id=pipeline_run_id,
        subscription={"type": "subscribe", "after_sequence": 50},
        stop_after_sends=2,
    )

    await project_events(websocket, project_id, FakeTicketStore())

    sequences = [
        event["sequence"] for batch in websocket.sent for event in batch["events"]
    ]
    assert sequences == list(range(51, 251))
    assert len(queries) == 2
