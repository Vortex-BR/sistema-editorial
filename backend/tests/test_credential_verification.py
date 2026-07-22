from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

import app.api.routes as routes_module
from app.api.routes import verify_credential
from app.db.models import CredentialProvider
from app.services.credential_verification import (
    CredentialVerificationResult,
    CredentialVerificationService,
)
from app.services.llm_gateway import LLMResult


class FakeGateway:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    async def generate_structured(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        return self.result


@pytest.mark.asyncio
async def test_gemini_verification_uses_minimal_structured_call():
    gateway = FakeGateway(
        LLMResult(
            data={"verified": True},
            provider="gemini",
            model="gemini-3.5-flash",
            prompt_tokens=2,
            completion_tokens=1,
            latency_ms=5,
        )
    )

    result = await CredentialVerificationService(gateway=gateway).verify(
        provider="gemini",
        api_key="provider-secret",
        model="gemini-3.5-flash",
    )

    assert result.verified is True
    assert result.model == "gemini-3.5-flash"
    assert result.verified_at is not None
    parameters = gateway.calls[0][1]["parameters"]
    assert parameters == {
        "temperature": 0,
        "max_output_tokens": 1024,
        "timeout_seconds": 30,
        "max_retries": 2,
    }


@pytest.mark.asyncio
async def test_openai_verification_uses_requested_gpt_4o_mini_route():
    gateway = FakeGateway(
        LLMResult(
            data={"verified": True},
            provider="openai",
            model="gpt-4o-mini",
            prompt_tokens=12,
            completion_tokens=5,
            latency_ms=7,
        )
    )

    result = await CredentialVerificationService(gateway=gateway).verify(
        provider="openai",
        api_key="provider-secret",
        model="gpt-4o-mini",
    )

    assert result.verified is True
    assert result.model == "gpt-4o-mini"
    assert result.verified_at is not None
    args, kwargs = gateway.calls[0]
    assert args[2].provider == "openai"
    assert args[2].model == "gpt-4o-mini"
    assert args[2].api_key == "provider-secret"
    assert kwargs["parameters"] == {
        "temperature": 0,
        "max_output_tokens": 1024,
        "timeout_seconds": 30,
        "max_retries": 2,
    }


@pytest.mark.asyncio
async def test_anthropic_verification_uses_configured_model_without_openai_parameters():
    gateway = FakeGateway(
        LLMResult(
            data={"verified": True},
            provider="anthropic",
            model="claude-sonnet-5",
            prompt_tokens=3,
            completion_tokens=1,
            latency_ms=6,
        )
    )

    result = await CredentialVerificationService(gateway=gateway).verify(
        provider="anthropic",
        api_key="provider-secret",
        model="claude-sonnet-5",
    )

    assert result.verified is True
    assert result.model == "claude-sonnet-5"
    args, kwargs = gateway.calls[0]
    assert args[2].provider == "anthropic"
    assert args[2].model == "claude-sonnet-5"
    assert kwargs["parameters"] == {
        "max_output_tokens": 1024,
        "timeout_seconds": 30,
        "max_retries": 2,
    }


@pytest.mark.asyncio
async def test_serper_verification_uses_one_bounded_result():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["payload"] = request.read().decode()
        return httpx.Response(200, request=request, json={"organic": []})

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    result = await CredentialVerificationService(
        client_factory=client_factory
    ).verify(provider="serper", api_key="serper-secret")

    assert result.verified is True
    assert result.model is None
    assert captured["headers"]["X-API-KEY"] == "serper-secret"
    assert '"num":1' in captured["payload"].replace(" ", "")


@pytest.mark.asyncio
async def test_verify_endpoint_persists_only_success_timestamp(monkeypatch):
    verified_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    credential = SimpleNamespace(encrypted_value=b"encrypted", verified_at=None)

    class FakeDb:
        def __init__(self):
            self.scalar_calls = 0
            self.commits = 0

        async def scalar(self, _query):
            self.scalar_calls += 1
            return credential if self.scalar_calls == 1 else "gemini-3.5-flash"

        async def commit(self):
            self.commits += 1

    class FakeService:
        async def verify(self, **kwargs):
            assert kwargs == {
                "provider": "gemini",
                "api_key": "decrypted-secret",
                "model": "gemini-3.5-flash",
            }
            return CredentialVerificationResult(
                provider="gemini",
                verified=True,
                verified_at=verified_at,
                latency_ms=12,
                model="gemini-3.5-flash",
                error_code=None,
            )

    class FakeVault:
        def decrypt(self, _value):
            return "decrypted-secret"

    monkeypatch.setattr(routes_module, "CredentialVault", FakeVault)
    monkeypatch.setattr(routes_module, "CredentialVerificationService", FakeService)
    db = FakeDb()

    response = await verify_credential(CredentialProvider.gemini, db)

    assert response.verified is True
    assert response.verified_at == verified_at
    assert credential.verified_at == verified_at
    assert db.commits == 1


@pytest.mark.asyncio
async def test_verify_endpoint_resolves_openai_route_model(monkeypatch):
    verified_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    credential = SimpleNamespace(encrypted_value=b"encrypted", verified_at=None)

    class FakeDb:
        def __init__(self):
            self.scalar_calls = 0

        async def scalar(self, _query):
            self.scalar_calls += 1
            return credential if self.scalar_calls == 1 else "gpt-4o-mini"

        async def commit(self):
            pass

    class FakeService:
        async def verify(self, **kwargs):
            assert kwargs == {
                "provider": "openai",
                "api_key": "decrypted-secret",
                "model": "gpt-4o-mini",
            }
            return CredentialVerificationResult(
                provider="openai",
                verified=True,
                verified_at=verified_at,
                latency_ms=9,
                model="gpt-4o-mini",
                error_code=None,
            )

    class FakeVault:
        def decrypt(self, _value):
            return "decrypted-secret"

    monkeypatch.setattr(routes_module, "CredentialVault", FakeVault)
    monkeypatch.setattr(routes_module, "CredentialVerificationService", FakeService)

    response = await verify_credential(CredentialProvider.openai, FakeDb())

    assert response.verified is True
    assert response.model == "gpt-4o-mini"
    assert credential.verified_at == verified_at


@pytest.mark.asyncio
async def test_verify_endpoint_resolves_anthropic_route_model(monkeypatch):
    verified_at = datetime(2026, 7, 15, tzinfo=timezone.utc)
    credential = SimpleNamespace(encrypted_value=b"encrypted", verified_at=None)

    class FakeDb:
        def __init__(self):
            self.scalar_calls = 0

        async def scalar(self, _query):
            self.scalar_calls += 1
            return credential if self.scalar_calls == 1 else "claude-sonnet-5"

        async def commit(self):
            pass

    class FakeService:
        async def verify(self, **kwargs):
            assert kwargs == {
                "provider": "anthropic",
                "api_key": "decrypted-secret",
                "model": "claude-sonnet-5",
            }
            return CredentialVerificationResult(
                provider="anthropic",
                verified=True,
                verified_at=verified_at,
                latency_ms=11,
                model="claude-sonnet-5",
                error_code=None,
            )

    class FakeVault:
        def decrypt(self, _value):
            return "decrypted-secret"

    monkeypatch.setattr(routes_module, "CredentialVault", FakeVault)
    monkeypatch.setattr(routes_module, "CredentialVerificationService", FakeService)

    response = await verify_credential(CredentialProvider.anthropic, FakeDb())

    assert response.verified is True
    assert response.model == "claude-sonnet-5"
    assert credential.verified_at == verified_at


@pytest.mark.asyncio
async def test_tavily_verification_uses_usage_endpoint_without_search_credit():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            request=request,
            json={"key": {"usage": 0, "limit": 1000}},
        )

    def client_factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    result = await CredentialVerificationService(
        client_factory=client_factory
    ).verify(provider="tavily", api_key="tvly-secret")

    assert result.verified is True
    assert captured == {
        "method": "GET",
        "url": "https://api.tavily.com/usage",
        "authorization": "Bearer tvly-secret",
    }
