import json
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

import app.services.llm_gateway as gateway_module
from app.api.routes import router
from app.core.config import settings
from app.db.models import ModelRoute
from app.db.session import get_db
from app.services.execution_manifest import (
    ExecutionManifestUnavailable,
    _model_route_manifest,
)
from app.services.llm_gateway import LLMGateway, LLMResult, ModelTarget
from app.services.model_route_policy import (
    ModelRoutePolicyError,
    normalize_model_route_configuration,
)


ADMIN_TOKEN = "model-route-policy-administrative-token"
MISSING = object()


class ProviderPayload(BaseModel):
    accepted: bool


def configuration(
    provider="openai",
    model="gpt-4.1-mini",
    parameters=MISSING,
    *,
    fallback_provider=None,
    fallback_model=None,
):
    return {
        "agent_role": "writer",
        "primary_provider": provider,
        "primary_model": model,
        "fallback_provider": fallback_provider,
        "fallback_model": fallback_model,
        "parameters": {} if parameters is MISSING else parameters,
    }


@pytest.mark.parametrize(
    ("provider", "model", "parameters", "expected"),
    [
        ("openai", "gpt-4.1-mini", {"temperature": 0.2}, {"temperature": 0.2}),
        (
            "openai",
            "gpt-4.1-mini",
            {"max_output_tokens": 1024},
            {"max_output_tokens": 1024},
        ),
        (
            "openai",
            "gpt-4.1-mini",
            {"max_completion_tokens": 512},
            {"max_output_tokens": 512},
        ),
        (
            "anthropic",
            "claude-sonnet-4-20250514",
            {"max_tokens": 2048},
            {"max_output_tokens": 2048},
        ),
        ("gemini", "gemini-2.5-flash", {"temperature": 2}, {"temperature": 2.0}),
        (
            "openai",
            "gpt-4.1-mini",
            {"timeout_seconds": 45},
            {"timeout_seconds": 45.0},
        ),
        ("openai", "gpt-4.1-mini", {"max_retries": 2}, {"max_retries": 2}),
        (
            "openai",
            "gpt-4.1-mini",
            {"response_format": " JSON_SCHEMA "},
            {"response_format": "json_schema"},
        ),
        (
            "openai",
            "gpt-5.1",
            {"reasoning_effort": " MEDIUM "},
            {"reasoning_effort": "medium"},
        ),
        (
            "openai",
            "gpt-4.1-mini",
            {"input_cost_per_million": 1, "output_cost_per_million": 10.5},
            {"input_cost_per_million": 1.0, "output_cost_per_million": 10.5},
        ),
    ],
)
def test_each_supported_parameter_is_normalized(provider, model, parameters, expected):
    normalized = normalize_model_route_configuration(
        configuration(provider, model, parameters)
    )

    assert normalized["parameters"] == expected


@pytest.mark.parametrize(
    "key",
    [
        "header",
        "headers",
        "url",
        "base_url",
        "api_key",
        "auth",
        "tools",
        "callbacks",
        "proxies",
        "files",
        "unknown_parameter",
    ],
)
def test_unknown_and_security_sensitive_keys_are_rejected(key):
    with pytest.raises(ModelRoutePolicyError):
        normalize_model_route_configuration(
            configuration(parameters={key: "attacker-controlled-value"})
        )


@pytest.mark.parametrize(
    "parameters",
    [
        {"temperature": "0.2"},
        {"temperature": True},
        {"max_output_tokens": 10.0},
        {"timeout_seconds": "30"},
        {"max_retries": "2"},
        {"response_format": {"type": "json_schema"}},
        {"reasoning_effort": 3},
        {"input_cost_per_million": "1.25"},
        [],
        None,
    ],
)
def test_wrong_parameter_types_are_rejected(parameters):
    with pytest.raises(ModelRoutePolicyError):
        normalize_model_route_configuration(configuration(parameters=parameters))


@pytest.mark.parametrize(
    "parameters",
    [
        {"temperature": 2.1},
        {"max_output_tokens": 128_001},
        {"timeout_seconds": 301},
        {"max_retries": 6},
        {"input_cost_per_million": 10_001},
        {"response_format": "text"},
        {"reasoning_effort": "unbounded"},
    ],
)
def test_parameter_limits_are_enforced(parameters):
    with pytest.raises(ModelRoutePolicyError):
        normalize_model_route_configuration(configuration(parameters=parameters))


@pytest.mark.parametrize(
    "route",
    [
        configuration("tavily", "search-model"),
        configuration(
            "anthropic",
            "claude-sonnet-4-20250514",
            {"reasoning_effort": "medium"},
        ),
        configuration("openai", "gpt-5.1", {"temperature": 0.2}),
        configuration("anthropic", "claude-sonnet-4-20250514", {"temperature": 1.1}),
        configuration("anthropic", "claude-opus-4-8", {"temperature": 0.2}),
        configuration("anthropic", "claude-sonnet-5", {"temperature": 0.2}),
        configuration("openai", "gpt-4.1-mini", {"max_tokens": 100}),
        configuration("openai", "https://models.example/unsafe"),
        configuration("openai", "test\x00-model"),
        configuration(
            "openai",
            "gpt-4.1-mini",
            {"temperature": 1.5},
            fallback_provider="anthropic",
            fallback_model="claude-sonnet-4-20250514",
        ),
    ],
)
def test_provider_and_model_incompatible_configurations_are_rejected(route):
    with pytest.raises(ModelRoutePolicyError):
        normalize_model_route_configuration(route)


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("openai", "gpt-4.1-mini"),
        ("anthropic", "claude-sonnet-4-20250514"),
        ("gemini", "gemini-2.5-flash"),
    ],
)
def test_existing_default_configurations_remain_compatible(provider, model):
    normalized = normalize_model_route_configuration(
        configuration(provider, model, {"temperature": 0.2})
    )

    assert normalized["primary_provider"] == provider
    assert normalized["primary_model"] == model
    assert normalized["parameters"] == {"temperature": 0.2}


class RouteDb:
    def __init__(self):
        self.added = []
        self.scalar_calls = 0
        self.commits = 0

    async def scalar(self, _query):
        self.scalar_calls += 1
        return None

    def add(self, instance):
        self.added.append(instance)

    async def commit(self):
        self.commits += 1


def route_client(db: RouteDb) -> TestClient:
    application = FastAPI()
    application.include_router(router)

    async def database_dependency():
        yield db

    application.dependency_overrides[get_db] = database_dependency
    return TestClient(application)


def test_api_returns_safe_422_before_persistence(monkeypatch, caplog):
    secret = "sk-parameter-secret-must-not-appear"
    db = RouteDb()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    caplog.set_level(logging.DEBUG)

    with route_client(db) as client:
        response = client.put(
            "/api/v1/config/routes/writer",
            json={
                **configuration(parameters={"api_key": secret}),
                "agent_role": "writer",
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": ModelRoutePolicyError.public_detail,
    }
    assert secret not in response.text
    assert secret not in caplog.text
    assert db.scalar_calls == 0
    assert db.added == []
    assert db.commits == 0


def test_api_rejects_safe_top_level_extra_fields(monkeypatch):
    secret = "top-level-api-key-secret"
    db = RouteDb()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with route_client(db) as client:
        response = client.put(
            "/api/v1/config/routes/writer",
            json={
                **configuration(),
                "agent_role": "writer",
                "api_key": secret,
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": ModelRoutePolicyError.public_detail,
    }
    assert secret not in response.text
    assert db.scalar_calls == 0
    assert db.added == []
    assert db.commits == 0


@pytest.mark.parametrize(
    "route",
    [
        configuration(parameters={"temperature": "0.2"}),
        configuration(parameters={"max_output_tokens": 128_001}),
        configuration("tavily", "search-model"),
        configuration(parameters=[]),
    ],
)
def test_api_returns_422_for_each_invalid_configuration_category(monkeypatch, route):
    db = RouteDb()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with route_client(db) as client:
        response = client.put(
            "/api/v1/config/routes/writer",
            json={**route, "agent_role": "writer"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 422
    assert response.json() == {
        "detail": ModelRoutePolicyError.public_detail,
    }
    assert db.scalar_calls == 0
    assert db.added == []
    assert db.commits == 0


def test_api_persists_and_returns_only_normalized_parameters(monkeypatch):
    db = RouteDb()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with route_client(db) as client:
        response = client.put(
            "/api/v1/config/routes/writer",
            json={
                **configuration(
                    " OpenAI ",
                    " gpt-4.1-mini ",
                    {
                        "max_completion_tokens": 1024,
                        "temperature": 1,
                        "response_format": " JSON_SCHEMA ",
                    },
                ),
                "agent_role": "writer",
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert response.json()["parameters"] == {
        "max_output_tokens": 1024,
        "response_format": "json_schema",
        "temperature": 1.0,
    }
    route = next(item for item in db.added if isinstance(item, ModelRoute))
    assert route.primary_provider == "openai"
    assert route.primary_model == "gpt-4.1-mini"
    assert route.parameters == response.json()["parameters"]


def test_execution_manifest_receives_only_normalized_parameters():
    route = SimpleNamespace(
        id=uuid.uuid4(),
        agent_role="writer",
        primary_provider=" OpenAI ",
        primary_model=" gpt-4.1-mini ",
        fallback_provider=None,
        fallback_model=None,
        parameters={"max_completion_tokens": 512, "temperature": 1},
    )

    manifest_route = _model_route_manifest(route)

    assert manifest_route["primary_provider"] == "openai"
    assert manifest_route["primary_model"] == "gpt-4.1-mini"
    assert manifest_route["parameters"] == {
        "max_output_tokens": 512,
        "temperature": 1.0,
    }
    assert "max_completion_tokens" not in json.dumps(manifest_route)


def test_execution_manifest_rejects_unsafe_legacy_route_parameters():
    route = SimpleNamespace(
        id=uuid.uuid4(),
        agent_role="writer",
        primary_provider="openai",
        primary_model="gpt-4.1-mini",
        fallback_provider=None,
        fallback_model=None,
        parameters={"callbacks": ["https://attacker.example/callback"]},
    )

    with pytest.raises(ExecutionManifestUnavailable):
        _model_route_manifest(route)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "max_key"),
    [
        ("openai", "gpt-4.1-mini", "max_completion_tokens"),
        ("anthropic", "claude-sonnet-4-20250514", "max_tokens"),
        ("gemini", "gemini-2.5-flash", "maxOutputTokens"),
    ],
)
async def test_gateway_maps_only_allowlisted_parameters_to_provider_payload(
    monkeypatch, provider, model, max_key
):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            content = json.dumps({"accepted": True})
            if provider == "openai":
                return {"choices": [{"message": {"content": content}}], "usage": {}}
            if provider == "anthropic":
                return {"content": [{"text": content}], "usage": {}}
            return {
                "candidates": [{"content": {"parts": [{"text": content}]}}],
                "usageMetadata": {},
            }

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured.update(url=url, request=kwargs)
            return Response()

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", Client)
    parameters = {
        "temperature": 0.4,
        "max_output_tokens": 321,
        "timeout_seconds": 42,
        "max_retries": 1,
        "response_format": "json_schema",
        "input_cost_per_million": 1.25,
        "output_cost_per_million": 10,
    }

    result = await LLMGateway()._call(
        "prompt",
        ProviderPayload,
        ModelTarget(provider, model, "provider-secret"),
        parameters=parameters,
    )

    assert result.data == {"accepted": True}
    assert captured["client"] == {"timeout": 42.0}
    payload = captured["request"]["json"]
    provider_config = payload.get("generationConfig", payload)
    assert provider_config[max_key] == 321
    assert provider_config["temperature"] == 0.4
    serialized = json.dumps(payload)
    for local_only in (
        "timeout_seconds",
        "max_retries",
        "input_cost_per_million",
        "output_cost_per_million",
        "provider-secret",
    ):
        assert local_only not in serialized


@pytest.mark.asyncio
async def test_openai_reasoning_effort_is_mapped_for_reasoning_models(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{"message": {"content": json.dumps({"accepted": True})}}],
                "usage": {},
            }

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            captured.update(kwargs)
            return Response()

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", Client)

    await LLMGateway()._call(
        "prompt",
        ProviderPayload,
        ModelTarget("openai", "gpt-5.1", "provider-secret"),
        parameters={"reasoning_effort": "medium"},
    )

    assert captured["json"]["reasoning_effort"] == "medium"
    assert "temperature" not in captured["json"]


@pytest.mark.asyncio
async def test_gateway_rejects_unknown_parameters_before_creating_http_client(
    monkeypatch,
):
    class Client:
        def __init__(self, **_kwargs):
            raise AssertionError("HTTP client must not be created")

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", Client)

    with pytest.raises(ModelRoutePolicyError):
        await LLMGateway()._call(
            "prompt",
            ProviderPayload,
            ModelTarget("openai", "gpt-4.1-mini", "provider-secret"),
            parameters={"headers": {"X-Unsafe": "value"}},
        )


@pytest.mark.asyncio
async def test_configured_retry_limit_controls_transport_attempts():
    gateway = LLMGateway(sleep=AsyncMock())
    expected = LLMResult(
        data={"accepted": True},
        provider="openai",
        model="gpt-4.1-mini",
        prompt_tokens=1,
        completion_tokens=1,
        latency_ms=1,
    )
    gateway._call = AsyncMock(
        side_effect=[
            httpx.ConnectError("first"),
            httpx.ConnectError("second"),
            expected,
        ]
    )
    result = await gateway._call_with_retries(
        "prompt",
        ProviderPayload,
        ModelTarget("openai", "gpt-4.1-mini", "provider-secret"),
        {"max_retries": 2},
    )

    assert result.data == expected.data
    assert gateway._call.await_count == 3


def test_cross_provider_fallback_accepts_target_specific_parameters_and_costs():
    normalized = normalize_model_route_configuration(
        configuration(
            "openai",
            "gpt-5-mini",
            {
                "reasoning_effort": "medium",
                "max_output_tokens": 4096,
                "input_cost_per_million": 0.25,
                "output_cost_per_million": 2.0,
                "fallback_max_tokens": 2048,
                "fallback_input_cost_per_million": 3.0,
                "fallback_output_cost_per_million": 15.0,
            },
            fallback_provider="anthropic",
            fallback_model="claude-sonnet-4-20250514",
        )
    )

    assert normalized["parameters"]["reasoning_effort"] == "medium"
    assert normalized["parameters"]["fallback_max_output_tokens"] == 2048
    assert normalized["parameters"]["fallback_input_cost_per_million"] == 3.0


def test_fallback_parameter_requires_a_configured_fallback():
    with pytest.raises(ModelRoutePolicyError):
        normalize_model_route_configuration(
            configuration(parameters={"fallback_max_output_tokens": 1024})
        )


@pytest.mark.asyncio
async def test_gateway_projects_parameters_for_cross_provider_fallback():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), json.loads(request.read())))
        if "openai.com" in str(request.url):
            return httpx.Response(500, request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "content": [{"text": json.dumps({"accepted": True})}],
                "usage": {"input_tokens": 5, "output_tokens": 2},
                "stop_reason": "end_turn",
            },
        )

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    gateway = LLMGateway(
        client_factory=client_factory,
        sleep=AsyncMock(),
        jitter=lambda _minimum, _maximum: 0,
    )
    result = await gateway.generate_structured(
        "prompt",
        ProviderPayload,
        ModelTarget("openai", "gpt-5-mini", "primary"),
        ModelTarget("anthropic", "claude-sonnet-4-20250514", "fallback"),
        parameters={
            "reasoning_effort": "medium",
            "max_output_tokens": 4096,
            "max_retries": 0,
            "fallback_max_tokens": 1024,
            "input_cost_per_million": 0.25,
            "output_cost_per_million": 2.0,
            "fallback_input_cost_per_million": 3.0,
            "fallback_output_cost_per_million": 15.0,
        },
    )

    assert result.data == {"accepted": True}
    anthropic_payload = next(
        payload for url, payload in requests if "anthropic.com" in url
    )
    assert anthropic_payload["max_tokens"] == 1024
    assert "reasoning_effort" not in anthropic_payload


def test_known_openai_model_refreshes_stale_price_and_writer_limits():
    from app.services.model_catalog import apply_known_model_profile

    refreshed = apply_known_model_profile(
        configuration(
            provider="openai",
            model="gpt-5.4",
            parameters={
                "reasoning_effort": "medium",
                "max_output_tokens": 8192,
                "timeout_seconds": 180,
                "max_retries": 2,
                "input_cost_per_million": 0.25,
                "output_cost_per_million": 2.0,
            },
        )
    )

    assert refreshed["parameters"] == {
        "reasoning_effort": "low",
        "max_output_tokens": 24000,
        "timeout_seconds": 240.0,
        "max_retries": 1,
        "input_cost_per_million": 2.5,
        "output_cost_per_million": 15.0,
    }


def test_unknown_openai_model_keeps_admin_managed_parameters():
    from app.services.model_catalog import apply_known_model_profile

    original = configuration(
        provider="openai",
        model="custom-model-1",
        parameters={
            "max_output_tokens": 2048,
            "input_cost_per_million": 1.0,
            "output_cost_per_million": 3.0,
        },
    )

    assert apply_known_model_profile(original) == original
