from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from pydantic import BaseModel

from app.core.observability import structured_log
from app.services.llm_gateway import (
    LLMGateway,
    ModelTarget,
    ProviderError,
    provider_error_from_http,
    provider_error_from_transport,
)


class _LLMVerificationOutput(BaseModel):
    verified: bool


@dataclass(frozen=True)
class CredentialVerificationResult:
    provider: str
    verified: bool
    verified_at: datetime | None
    latency_ms: int
    model: str | None
    error_code: str | None


class CredentialVerificationService:
    def __init__(self, *, client_factory=None, gateway: LLMGateway | None = None):
        self._client_factory = client_factory or httpx.AsyncClient
        self._gateway = gateway or LLMGateway(timeout_seconds=30)

    async def verify(
        self, *, provider: str, api_key: str, model: str | None = None
    ) -> CredentialVerificationResult:
        started = time.perf_counter()
        try:
            if provider in {"openai", "anthropic", "gemini"}:
                selected_model = model or {
                    "openai": "gpt-4o-mini",
                    "anthropic": "claude-sonnet-5",
                    "gemini": "gemini-3.5-flash",
                }[provider]
                result = await self._gateway.generate_structured(
                    "Responda somente com JSON e defina verified=true.",
                    _LLMVerificationOutput,
                    ModelTarget(provider, selected_model, api_key),
                    parameters={
                        **(
                            {"reasoning_effort": "low"}
                            if provider == "openai"
                            and selected_model.startswith(("gpt-5", "o1", "o3", "o4"))
                            else (
                                {}
                                if provider == "anthropic"
                                else {"temperature": 0}
                            )
                        ),
                        "max_output_tokens": 1024,
                        "timeout_seconds": 30,
                        "max_retries": 2,
                    },
                )
                verified = result.data.get("verified") is True
                return CredentialVerificationResult(
                    provider=provider,
                    verified=verified,
                    verified_at=datetime.now(timezone.utc) if verified else None,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    model=selected_model,
                    error_code=None if verified else "provider_invalid_output",
                )
            if provider in {"serper", "tavily"}:
                if provider == "serper":
                    await self._verify_serper(api_key)
                else:
                    await self._verify_tavily(api_key)
                return CredentialVerificationResult(
                    provider=provider,
                    verified=True,
                    verified_at=datetime.now(timezone.utc),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    model=None,
                    error_code=None,
                )
            return CredentialVerificationResult(
                provider=provider,
                verified=False,
                verified_at=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                model=model,
                error_code="credential_verification_not_supported",
            )
        except ProviderError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            structured_log(
                "credential.verification_failed",
                level=30,
                provider=provider,
                model=model,
                latency_ms=latency_ms,
                error_code=exc.error_code,
                http_status=exc.http_status,
                retryable=exc.retryable,
            )
            return CredentialVerificationResult(
                provider=provider,
                verified=False,
                verified_at=None,
                latency_ms=latency_ms,
                model=model,
                error_code=exc.error_code,
            )

    async def _verify_tavily(self, api_key: str) -> None:
        try:
            async with self._client_factory(timeout=30) as client:
                # /usage authenticates the key without consuming a search credit.
                response = await client.get(
                    "https://api.tavily.com/usage",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise json.JSONDecodeError("Expected object", "", 0)
        except httpx.HTTPStatusError as exc:
            raise provider_error_from_http(
                exc, provider="tavily", model="search"
            ).finalized(latency_ms=0, attempts=1) from None
        except httpx.TransportError as exc:
            raise provider_error_from_transport(
                exc, provider="tavily", model="search"
            ).finalized(latency_ms=0, attempts=1) from None
        except (json.JSONDecodeError, TypeError, ValueError):
            raise ProviderError(
                "invalid_output",
                provider="tavily",
                model="search",
                retryable=False,
                error_code="search_invalid_output",
            ) from None

    async def _verify_serper(self, api_key: str) -> None:
        try:
            async with self._client_factory(timeout=30) as client:
                response = await client.post(
                    "https://google.serper.dev/search",
                    headers={
                        "X-API-KEY": api_key,
                        "Content-Type": "application/json",
                    },
                    json={"q": "site:example.com Example Domain", "num": 1},
                )
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise json.JSONDecodeError("Expected object", "", 0)
        except httpx.HTTPStatusError as exc:
            raise provider_error_from_http(
                exc, provider="serper", model="search"
            ).finalized(latency_ms=0, attempts=1) from None
        except httpx.TransportError as exc:
            raise provider_error_from_transport(
                exc, provider="serper", model="search"
            ).finalized(latency_ms=0, attempts=1) from None
        except (json.JSONDecodeError, TypeError, ValueError):
            raise ProviderError(
                "invalid_output",
                provider="serper",
                model="search",
                retryable=False,
                error_code="search_invalid_output",
            ) from None
