import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from enum import Enum

import pytest
from pydantic import BaseModel
from sqlalchemy import event
from sqlalchemy.exc import DataError
from sqlalchemy.orm import Session

from app.core.errors import (
    PERSISTENCE_INPUT_INVALID,
    PUBLIC_ERROR_MESSAGE,
    redact_sensitive,
    safe_public_message,
    safe_exception_details,
)
from app.db.models import AgentRun
from app.db import session as db_session
from app.db.session import register_session_sanitization_guards
from app.services.embeddings import EmbeddingGateway
from app.core.sanitization import (
    SanitizationKeyCollision,
    sanitize_nul,
    sanitize_nul_with_report,
)
from app.services.llm_gateway import LLMGateway, ModelTarget, ProviderError
from app.services.pipeline_control import RetryPolicy
from app.services.research_engine import ResearchEngine


class Marker(Enum):
    value = "unchanged"


def test_recursive_sanitizer_is_pure_idempotent_and_preserves_types():
    identifier = uuid.uuid4()
    timestamp = datetime.now(timezone.utc)
    original = {
        "\x00key": "\x00start",
        "nested": ["before\x00after", {"end": "finish\x00"}],
        "tuple": ("a\x00b", 7, True),
        "uuid": identifier,
        "datetime": timestamp,
        "enum": Marker.value,
    }
    untouched = copy.deepcopy(original)

    sanitized = sanitize_nul(original)

    assert sanitized == {
        "key": "start",
        "nested": ["beforeafter", {"end": "finish"}],
        "tuple": ("ab", 7, True),
        "uuid": identifier,
        "datetime": timestamp,
        "enum": Marker.value,
    }
    assert original == untouched
    assert isinstance(sanitized["tuple"], tuple)
    assert sanitize_nul(sanitized) == sanitized


def test_internal_preserves_literal_escapes_and_external_removes_them():
    value = {
        "text": r"real\u0000 and real\x00",
        "double": r"double\\u0000escape",
        "nul": "a\x00b",
    }

    internal = sanitize_nul(value)
    external, report = sanitize_nul_with_report(value, strip_escaped=True)

    assert internal == {
        "text": r"real\u0000 and real\x00",
        "double": r"double\\u0000escape",
        "nul": "ab",
    }
    assert external == {"text": "real and real", "double": "doubleescape", "nul": "ab"}
    assert report.nul_removed_count == 1
    assert report.escaped_nul_removed_count == 3
    assert report.sanitized_fields
    assert sanitize_nul(external, strip_escaped=True) == external


def test_key_collision_is_never_silent_and_reports_path():
    with pytest.raises(SanitizationKeyCollision, match=r"\$\.field") as caught:
        sanitize_nul({"field\x00": 1, "field": 2})
    assert caught.value.path == "$.field"


def test_retry_policy_distinguishes_provider_contract_from_persistence_input():
    provider = RetryPolicy.classify(
        ProviderError(
            "invalid_output",
            provider="gemini",
            model="test-model",
            retryable=False,
        ),
        1,
    )
    collision = RetryPolicy.classify(SanitizationKeyCollision("$.payload.key"), 1)

    assert provider.retryable is False
    assert collision.retryable is False
    assert collision.code == PERSISTENCE_INPUT_INVALID

    database_error = DataError(
        "INSERT INTO records(text) VALUES ($1)",
        {"text": "secret\x00value"},
        RuntimeError("unsupported Unicode u0000 text"),
    )
    classified = RetryPolicy.classify(database_error, 1)
    assert classified.retryable is False
    assert classified.code == PERSISTENCE_INPUT_INVALID


def test_redaction_and_public_message_hide_secrets_and_sql():
    technical = (
        "sqlalchemy asyncpg INSERT INTO agent_runs VALUES ($1) "
        "[parameters: {'password': 'db-secret'}] "
        "https://example.test/path?key=gemini-secret Authorization: Bearer api-secret "
        "token=plain-token password:plain-password"
    )
    redacted = redact_sensitive(technical)

    assert "db-secret" not in redacted
    assert "gemini-secret" not in redacted
    assert "api-secret" not in redacted
    assert "plain-token" not in redacted
    assert "plain-password" not in redacted
    assert "?key=***" in redacted
    assert safe_public_message(technical) == PUBLIC_ERROR_MESSAGE

    database_error = DataError(
        "INSERT INTO records(secret) VALUES ('literal-secret')",
        {"password": "bound-secret"},
        RuntimeError("invalid text"),
    )
    details = safe_exception_details(database_error)
    serialized = json.dumps(details)
    assert "literal-secret" not in serialized
    assert "bound-secret" not in serialized
    assert details["operation"] == "INSERT"
    assert "INSERT INTO records" in details["sql_template"]


def test_session_before_attach_guard_cleans_orm_values():
    register_session_sanitization_guards()
    run = AgentRun(
        project_id=uuid.uuid4(),
        agent_role="researcher",
        status="running",
        input_json={"nested": ["before\x00after"]},
    )
    session = Session()
    try:
        session.add(run)
        assert run.input_json == {"nested": ["beforeafter"]}
    finally:
        session.close()


def test_session_sanitization_guard_registration_is_idempotent(monkeypatch):
    register_session_sanitization_guards()
    register_session_sanitization_guards()
    register_session_sanitization_guards()

    assert event.contains(
        Session, "before_attach", db_session._sanitize_before_attach
    )
    assert event.contains(
        Session, "before_flush", db_session._last_chance_nul_guard
    )

    sanitization_calls = 0
    sanitize_mapped_instance = db_session.sanitize_mapped_instance

    def count_sanitization_calls(instance):
        nonlocal sanitization_calls
        sanitization_calls += 1
        sanitize_mapped_instance(instance)

    monkeypatch.setattr(
        db_session, "sanitize_mapped_instance", count_sanitization_calls
    )
    run = AgentRun(
        project_id=uuid.uuid4(),
        agent_role="researcher",
        status="running",
        input_json={"nested": ["before\x00after"]},
    )
    session = Session()
    try:
        session.add(run)
        assert run.input_json == {"nested": ["beforeafter"]}
        assert sanitization_calls == 1
    finally:
        session.close()


class ProviderPayload(BaseModel):
    text: str
    literal: str


@pytest.mark.asyncio
async def test_llm_json_is_parsed_then_sanitized_then_validated(monkeypatch):
    captured = {}
    inner_json = json.dumps(
        {"text": "before\x00after", "literal": r"left\u0000right"},
        ensure_ascii=False,
    )

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": inner_json}]}}],
                "usageMetadata": {},
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured.update(url=url, kwargs=kwargs)
            return Response()

    monkeypatch.setattr("app.services.llm_gateway.httpx.AsyncClient", Client)
    target = ModelTarget("gemini", "gemini-test", "top-secret")

    result = await LLMGateway()._call("prompt", ProviderPayload, target)

    assert result.data == {"text": "beforeafter", "literal": "leftright"}
    assert result.sanitization_report.nul_removed_count == 1
    assert result.sanitization_report.escaped_nul_removed_count == 1
    assert "?key=" not in captured["url"]
    assert captured["kwargs"]["headers"] == {"x-goog-api-key": "top-secret"}


@pytest.mark.asyncio
async def test_gemini_embedding_uses_header_not_query_string(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"embedding": {"values": [0.1, 0.2]}}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured.update(url=url, kwargs=kwargs)
            return Response()

    monkeypatch.setattr("app.services.embeddings.httpx.AsyncClient", Client)

    result = await EmbeddingGateway()._call(
        "gemini", "embedding-test", "embedding-secret", "text"
    )

    assert result == [0.1, 0.2]
    assert "?key=" not in captured["url"]
    assert captured["kwargs"]["headers"] == {
        "x-goog-api-key": "embedding-secret"
    }


@pytest.mark.asyncio
async def test_search_content_is_sanitized_before_hashing(monkeypatch):
    raw_content = "a" * 60 + "\x00" + "b" * 60 + r"\u0000" + "end"
    clean_content = "a" * 60 + "b" * 60 + "end"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "url": "https://example.test/article",
                        "title": "title\x00value",
                        "raw_content": raw_content,
                    }
                ]
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("app.services.research_engine.httpx.AsyncClient", Client)

    documents = await ResearchEngine()._tavily("query", "secret", 1)

    assert len(documents) == 1
    assert documents[0].title == "titlevalue"
    assert documents[0].content == clean_content
    assert documents[0].content_hash == hashlib.sha256(
        clean_content.encode("utf-8")
    ).hexdigest()
