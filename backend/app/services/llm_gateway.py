from __future__ import annotations

import asyncio
import json
import random
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Awaitable, Callable, Literal

import httpx
from pydantic import BaseModel, ValidationError

from app.core.observability import structured_log
from app.core.sanitization import (
    SanitizationReport,
    sanitize_nul,
    sanitize_nul_with_report,
)
from app.services.model_route_policy import (
    normalize_model_route_parameters,
    parameters_for_model_target,
)


ProviderErrorCategory = Literal[
    "authentication",
    "model_not_found",
    "invalid_request",
    "rate_limited",
    "timeout",
    "unavailable",
    "invalid_output",
    "content_filtered",
]

_PUBLIC_MESSAGES: dict[ProviderErrorCategory, str] = {
    "authentication": "A credencial do provedor foi recusada.",
    "model_not_found": "O modelo configurado não foi encontrado pelo provedor.",
    "invalid_request": "O provedor recusou a configuração desta chamada.",
    "rate_limited": "O provedor atingiu o limite temporário de uso.",
    "timeout": "O provedor excedeu o tempo limite da chamada.",
    "unavailable": "O provedor está temporariamente indisponível.",
    "invalid_output": "O provedor não retornou uma saída estruturada válida.",
    "content_filtered": "O provedor bloqueou a resposta por sua política de conteúdo.",
}

_TRANSIENT_CATEGORIES = frozenset({"rate_limited", "timeout", "unavailable"})
_FILTERED_FINISH_REASONS = frozenset(
    {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}
)

_ANTHROPIC_STRUCTURED_OUTPUT_MODEL = re.compile(
    r"^claude-(?:(?:sonnet|opus|haiku)-"
    r"(?:5(?:[-.]|$)|4[-.](?:5|6|7|8)(?:[-.]|$))|"
    r"(?:fable|mythos)-5(?:[-.]|$)|mythos-preview(?:[-.]|$))"
)
_ANTHROPIC_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "default",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maxProperties",
        "maximum",
        "minItems",
        "minLength",
        "minProperties",
        "minimum",
        "multipleOf",
        "uniqueItems",
    }
)
_ANTHROPIC_SUPPORTED_STRING_FORMATS = frozenset(
    {
        "date",
        "date-time",
        "duration",
        "email",
        "hostname",
        "ipv4",
        "ipv6",
        "time",
        "uri",
        "uuid",
    }
)


def _anthropic_supports_structured_outputs(model: str) -> bool:
    return bool(_ANTHROPIC_STRUCTURED_OUTPUT_MODEL.match(model.strip().lower()))


def _anthropic_structured_schema(node: object) -> object:
    """Transform Pydantic JSON Schema into Anthropic's supported subset.

    The original Pydantic model remains the final validator, so removing sampling
    constraints here cannot weaken application-side validation.
    """
    if isinstance(node, list):
        return [_anthropic_structured_schema(item) for item in node]
    if not isinstance(node, dict):
        return node

    transformed: dict[str, object] = {}
    constraints: list[str] = []
    for key, value in node.items():
        if key in _ANTHROPIC_UNSUPPORTED_SCHEMA_KEYS:
            if key not in {"default", "examples"}:
                constraints.append(f"{key}={value}")
            continue
        if key == "format" and value not in _ANTHROPIC_SUPPORTED_STRING_FORMATS:
            continue
        transformed[key] = _anthropic_structured_schema(value)

    properties = transformed.get("properties")
    if transformed.get("type") == "object" and isinstance(properties, dict):
        transformed["additionalProperties"] = False
    if constraints:
        existing = str(transformed.get("description") or "").strip()
        suffix = "Validation constraints: " + ", ".join(constraints) + "."
        transformed["description"] = f"{existing} {suffix}".strip()
    return transformed


def _response_json_object(
    response: httpx.Response, *, provider: str, model: str
) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(
            "invalid_output",
            provider=provider,
            model=model,
            retryable=True,
            error_code="provider_invalid_response_json",
            response_received=True,
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderError(
            "invalid_output",
            provider=provider,
            model=model,
            retryable=True,
            error_code="provider_response_invalid",
            response_received=True,
        )
    return payload


def _usage_int(value: object) -> int:
    """Normalize untrusted provider usage fields without masking the response."""
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _joined_text_blocks(blocks: object) -> str | None:
    if not isinstance(blocks, list):
        return None
    texts = [
        block.get("text")
        for block in blocks
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "".join(texts) if texts else None


class ProviderError(RuntimeError):
    """Safe provider failure with machine-readable retry semantics."""

    def __init__(
        self,
        category: ProviderErrorCategory,
        *,
        provider: str,
        model: str,
        http_status: int | None = None,
        retryable: bool | None = None,
        retry_after: float | None = None,
        latency_ms: int = 0,
        attempts: int = 1,
        error_code: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        response_received: bool = False,
        validation_error: ValidationError | None = None,
    ) -> None:
        self.category = category
        self.provider = provider
        self.model = model
        self.http_status = http_status
        self.retryable = (
            category in _TRANSIENT_CATEGORIES if retryable is None else retryable
        )
        self.retry_after = retry_after
        self.latency_ms = max(0, latency_ms)
        self.attempts = max(1, attempts)
        self.error_code = error_code or f"provider_{category}"
        self.prompt_tokens = max(0, int(prompt_tokens or 0))
        self.completion_tokens = max(0, int(completion_tokens or 0))
        self.response_received = bool(response_received)
        self.validation_error = validation_error
        self.public_message = _PUBLIC_MESSAGES[category]
        super().__init__(self.public_message)

    def finalized(self, *, latency_ms: int, attempts: int) -> "ProviderError":
        return ProviderError(
            self.category,
            provider=self.provider,
            model=self.model,
            http_status=self.http_status,
            retryable=False,
            retry_after=self.retry_after,
            latency_ms=latency_ms,
            attempts=attempts,
            error_code=self.error_code,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            response_received=self.response_received,
            validation_error=self.validation_error,
        )


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str
    api_key: str


@dataclass
class LLMResult:
    data: dict[str, Any]
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    sanitization_report: SanitizationReport | None = None


@dataclass(frozen=True)
class ProviderAttemptRecord:
    provider: str
    model: str
    target_kind: Literal["primary", "fallback"]
    attempt_number: int
    status: Literal["succeeded", "failed", "invalid_output"]
    response_received: bool
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    started_at: datetime
    finished_at: datetime
    http_status: int | None = None
    error_code: str | None = None
    error_category: str | None = None


AttemptObserver = Callable[[ProviderAttemptRecord], Awaitable[None]]
BeforeAttempt = Callable[[ModelTarget, str, int, str], Awaitable[None]]


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed - datetime.now(timezone.utc)).total_seconds())


def provider_error_from_http(
    error: httpx.HTTPStatusError,
    *,
    provider: str,
    model: str,
) -> ProviderError:
    status = error.response.status_code
    if status in {401, 403}:
        category: ProviderErrorCategory = "authentication"
    elif status == 404:
        category = "model_not_found"
    elif status == 408:
        category = "timeout"
    elif status == 429:
        category = "rate_limited"
    elif 500 <= status <= 599:
        category = "unavailable"
    else:
        category = "invalid_request"
    return ProviderError(
        category,
        provider=provider,
        model=model,
        http_status=status,
        retry_after=_retry_after_seconds(error.response.headers.get("Retry-After")),
    )


def provider_error_from_transport(
    error: httpx.TransportError,
    *,
    provider: str,
    model: str,
) -> ProviderError:
    category: ProviderErrorCategory = (
        "timeout" if isinstance(error, httpx.TimeoutException) else "unavailable"
    )
    return ProviderError(category, provider=provider, model=model)


def _schema_regeneration_prompt(
    original_prompt: str,
    validation_error: ValidationError | None = None,
    *,
    error_code: str = "provider_schema_invalid",
) -> str:
    issues: list[str] = []
    if validation_error is not None:
        for detail in validation_error.errors(
            include_url=False,
            include_context=False,
            include_input=False,
        )[:6]:
            location = ".".join(str(part) for part in detail.get("loc", ()))
            message = sanitize_nul(str(detail.get("msg", "schema inválido")))
            issues.append(f"{location or 'raiz'}: {message}")
    fallback_guidance = {
        "provider_output_truncated": (
            "a saída foi interrompida; seja mais conciso sem omitir campos"
        ),
        "provider_invalid_json": "a resposta não formou um JSON válido",
        "provider_invalid_response_json": (
            "o corpo HTTP do provedor não continha JSON válido"
        ),
        "provider_response_invalid": (
            "a resposta não continha o objeto estruturado esperado"
        ),
        "provider_schema_invalid": (
            "o JSON não pôde ser validado pelo contrato solicitado"
        ),
    }
    guidance = (
        "; ".join(issues)
        if issues
        else fallback_guidance.get(
            error_code, fallback_guidance["provider_schema_invalid"]
        )
    )
    return (
        f"{original_prompt}\n\n"
        "CORREÇÃO OBRIGATÓRIA: a resposta anterior foi rejeitada. "
        f"Problemas: {guidance}. Gere uma resposta nova, consistente, e retorne "
        "somente JSON compatível com o schema."
    )


def _assert_openai_strict_schema(schema: dict[str, Any]) -> None:
    """Reject an opted-in schema locally unless OpenAI can enforce it."""

    def visit(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
            return
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            properties = node.get("properties")
            if not isinstance(properties, dict):
                raise ValueError("OpenAI strict objects require explicit properties")
            if node.get("additionalProperties") is not False:
                raise ValueError(
                    "OpenAI strict objects require additionalProperties=false"
                )
            if set(node.get("required") or []) != set(properties):
                raise ValueError("OpenAI strict objects require every field")
        for value in node.values():
            visit(value)

    visit(schema)


class LLMGateway:
    """Provider-neutral structured generation with a bounded local retry budget."""

    def __init__(
        self,
        timeout_seconds: float = 90,
        *,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        client_factory: Callable[..., httpx.AsyncClient] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[float, float], float] | None = None,
    ) -> None:
        self.timeout = float(timeout_seconds)
        self.connect_timeout = float(connect_timeout_seconds or timeout_seconds)
        self.read_timeout = float(read_timeout_seconds or timeout_seconds)
        self._client_factory = client_factory or httpx.AsyncClient
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.uniform

    async def generate_structured(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        primary: ModelTarget,
        fallback: ModelTarget | None = None,
        parameters: dict[str, Any] | None = None,
        attempt_observer: AttemptObserver | None = None,
        before_attempt: BeforeAttempt | None = None,
    ) -> LLMResult:
        normalized_parameters = normalize_model_route_parameters(
            parameters if parameters is not None else {},
            primary_provider=primary.provider,
            primary_model=primary.model,
            fallback_provider=fallback.provider if fallback else None,
            fallback_model=fallback.model if fallback else None,
        )
        last_error: ProviderError | None = None
        for target_kind, target in (("primary", primary), ("fallback", fallback)):
            if target is None:
                continue
            try:
                return await self._call_with_retries(
                    prompt,
                    output_schema,
                    target,
                    normalized_parameters,
                    target_kind=target_kind,
                    attempt_observer=attempt_observer,
                    before_attempt=before_attempt,
                )
            except ProviderError as exc:
                if exc.category == "content_filtered":
                    # A policy refusal is about the request, not provider health.
                    # Trying another provider would spend more and may bypass the
                    # intended safety decision.
                    raise
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ProviderError(
            "invalid_request",
            provider=primary.provider,
            model=primary.model,
        )

    async def _call_with_retries(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        target: ModelTarget,
        parameters: dict[str, object],
        *,
        target_kind: Literal["primary", "fallback"] = "primary",
        attempt_observer: AttemptObserver | None = None,
        before_attempt: BeforeAttempt | None = None,
    ) -> LLMResult:
        target_parameters = parameters_for_model_target(
            parameters,
            provider=target.provider,
            model=target.model,
            target_kind=target_kind,
        )
        normalized_parameters = normalize_model_route_parameters(
            target_parameters,
            primary_provider=target.provider,
            primary_model=target.model,
        )
        transient_attempt_limit = min(
            3, int(normalized_parameters.get("max_retries", 2)) + 1
        )
        overall_attempt_limit = max(2, transient_attempt_limit)
        invalid_output_count = 0
        attempt_number = 0
        started = time.perf_counter()
        attempt_prompt = prompt

        while attempt_number < overall_attempt_limit:
            attempt_number += 1
            if before_attempt is not None:
                await before_attempt(
                    target, attempt_prompt, attempt_number, target_kind
                )
            attempt_started = time.perf_counter()
            attempt_started_at = datetime.now(timezone.utc)
            try:
                result = await self._call(
                    attempt_prompt,
                    output_schema,
                    target,
                    parameters=normalized_parameters,
                )
                if attempt_observer is not None:
                    await attempt_observer(
                        ProviderAttemptRecord(
                            provider=target.provider,
                            model=target.model,
                            target_kind=target_kind,
                            attempt_number=attempt_number,
                            status="succeeded",
                            response_received=True,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens,
                            latency_ms=int(
                                (time.perf_counter() - attempt_started) * 1000
                            ),
                            started_at=attempt_started_at,
                            finished_at=datetime.now(timezone.utc),
                        )
                    )
                return replace(
                    result,
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            except ProviderError as exc:
                provider_error = exc
            except httpx.HTTPStatusError as exc:
                provider_error = provider_error_from_http(
                    exc, provider=target.provider, model=target.model
                )
            except httpx.TransportError as exc:
                provider_error = provider_error_from_transport(
                    exc, provider=target.provider, model=target.model
                )

            if provider_error.category == "invalid_output":
                attempt_prompt = _schema_regeneration_prompt(
                    prompt,
                    provider_error.validation_error,
                    error_code=provider_error.error_code,
                )
            if attempt_observer is not None:
                await attempt_observer(
                    ProviderAttemptRecord(
                        provider=target.provider,
                        model=target.model,
                        target_kind=target_kind,
                        attempt_number=attempt_number,
                        status=(
                            "invalid_output"
                            if provider_error.category == "invalid_output"
                            else "failed"
                        ),
                        response_received=provider_error.response_received,
                        prompt_tokens=provider_error.prompt_tokens,
                        completion_tokens=provider_error.completion_tokens,
                        latency_ms=int((time.perf_counter() - attempt_started) * 1000),
                        started_at=attempt_started_at,
                        finished_at=datetime.now(timezone.utc),
                        http_status=provider_error.http_status,
                        error_code=provider_error.error_code,
                        error_category=provider_error.category,
                    )
                )

            should_retry = False
            if provider_error.category == "invalid_output":
                invalid_output_count += 1
                should_retry = (
                    invalid_output_count <= 1 and attempt_number < overall_attempt_limit
                )
            elif provider_error.retryable:
                should_retry = attempt_number < transient_attempt_limit

            if not should_retry:
                raise provider_error.finalized(
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    attempts=attempt_number,
                )

            backoff = min(8.0, float(2 ** max(0, attempt_number - 1)))
            delay = max(
                backoff + self._jitter(0.0, 0.5),
                provider_error.retry_after or 0.0,
            )
            structured_log(
                "provider.retry_scheduled",
                provider=target.provider,
                model=target.model,
                attempt=attempt_number,
                error_code=provider_error.error_code,
                http_status=provider_error.http_status,
                retryable=True,
                retry_delay_ms=int(delay * 1000),
            )
            await self._sleep(delay)

        raise ProviderError(
            "unavailable",
            provider=target.provider,
            model=target.model,
            retryable=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=attempt_number,
        )

    async def _call(
        self,
        prompt: str,
        output_schema: type[BaseModel],
        target: ModelTarget,
        parameters: dict[str, Any] | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        schema = output_schema.model_json_schema()
        openai_strict = bool(getattr(output_schema, "openai_strict", False))
        if target.provider == "openai" and openai_strict:
            _assert_openai_strict_schema(schema)
        normalized_parameters = normalize_model_route_parameters(
            parameters if parameters is not None else {},
            primary_provider=target.provider,
            primary_model=target.model,
        )
        route_timeout = normalized_parameters.get("timeout_seconds")
        if route_timeout is not None:
            timeout: float | httpx.Timeout = float(route_timeout)
        else:
            read_timeout = self.read_timeout
            connect_timeout = min(self.connect_timeout, read_timeout)
            timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
        async with self._client_factory(timeout=timeout) as client:
            if target.provider == "openai":
                request_json: dict[str, Any] = {
                    "model": target.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "Return only valid JSON matching the supplied schema.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": output_schema.__name__,
                            "strict": openai_strict,
                            "schema": schema,
                        },
                    },
                }
                if "temperature" in normalized_parameters:
                    request_json["temperature"] = normalized_parameters["temperature"]
                if "max_output_tokens" in normalized_parameters:
                    request_json["max_completion_tokens"] = normalized_parameters[
                        "max_output_tokens"
                    ]
                if "reasoning_effort" in normalized_parameters:
                    request_json["reasoning_effort"] = normalized_parameters[
                        "reasoning_effort"
                    ]
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers={"Authorization": f"Bearer {target.api_key}"},
                    json=request_json,
                )
                response.raise_for_status()
                raw = _response_json_object(
                    response, provider=target.provider, model=target.model
                )
                choices = raw.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        response_received=True,
                    )
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        response_received=True,
                    )
                usage = raw.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                prompt_tokens, completion_tokens = (
                    _usage_int(usage.get("prompt_tokens")),
                    _usage_int(usage.get("completion_tokens")),
                )
                if choice.get("finish_reason") == "content_filter":
                    raise ProviderError(
                        "content_filtered",
                        provider=target.provider,
                        model=target.model,
                        retryable=False,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                if choice.get("finish_reason") == "length":
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_output_truncated",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                message = choice.get("message")
                if not isinstance(message, dict):
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                if message.get("refusal"):
                    raise ProviderError(
                        "content_filtered",
                        provider=target.provider,
                        model=target.model,
                        retryable=False,
                        error_code="provider_refusal",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                text = message.get("content")
            elif target.provider == "anthropic":
                native_structured_output = _anthropic_supports_structured_outputs(
                    target.model
                )
                request_json = {
                    "model": target.model,
                    "max_tokens": normalized_parameters.get("max_output_tokens", 8192),
                    "messages": [{"role": "user", "content": prompt}],
                }
                if native_structured_output:
                    request_json["output_config"] = {
                        "format": {
                            "type": "json_schema",
                            "schema": _anthropic_structured_schema(schema),
                        }
                    }
                else:
                    request_json["system"] = (
                        f"Return only JSON matching this schema: {json.dumps(schema)}"
                    )
                if "temperature" in normalized_parameters:
                    request_json["temperature"] = normalized_parameters["temperature"]
                response = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": target.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                    json=request_json,
                )
                response.raise_for_status()
                raw = _response_json_object(
                    response, provider=target.provider, model=target.model
                )
                usage = raw.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                prompt_tokens, completion_tokens = (
                    _usage_int(usage.get("input_tokens")),
                    _usage_int(usage.get("output_tokens")),
                )
                stop_reason = str(raw.get("stop_reason") or "").lower()
                if stop_reason == "refusal":
                    raise ProviderError(
                        "content_filtered",
                        provider=target.provider,
                        model=target.model,
                        retryable=False,
                        error_code="provider_refusal",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                if stop_reason == "max_tokens":
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_output_truncated",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                content = raw.get("content") or []
                if not content:
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                text = _joined_text_blocks(content)
            elif target.provider == "gemini":
                generation_config: dict[str, Any] = {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": schema,
                }
                if "temperature" in normalized_parameters:
                    generation_config["temperature"] = normalized_parameters[
                        "temperature"
                    ]
                if "max_output_tokens" in normalized_parameters:
                    generation_config["maxOutputTokens"] = normalized_parameters[
                        "max_output_tokens"
                    ]
                response = await client.post(
                    "https://generativelanguage.googleapis.com/v1beta/"
                    f"models/{target.model}:generateContent",
                    headers={"x-goog-api-key": target.api_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": generation_config,
                    },
                )
                response.raise_for_status()
                raw = _response_json_object(
                    response, provider=target.provider, model=target.model
                )
                usage = raw.get("usageMetadata")
                usage = usage if isinstance(usage, dict) else {}
                prompt_tokens, completion_tokens = (
                    _usage_int(usage.get("promptTokenCount")),
                    _usage_int(usage.get("candidatesTokenCount")),
                )
                candidates = raw.get("candidates") or []
                prompt_feedback = raw.get("promptFeedback")
                prompt_feedback = (
                    prompt_feedback if isinstance(prompt_feedback, dict) else {}
                )
                block_reason = prompt_feedback.get("blockReason")
                if not candidates and block_reason:
                    raise ProviderError(
                        "content_filtered",
                        provider=target.provider,
                        model=target.model,
                        retryable=False,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                if not candidates:
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                candidate = candidates[0]
                if not isinstance(candidate, dict):
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                finish_reason = str(candidate.get("finishReason") or "").upper()
                candidate_content = candidate.get("content")
                candidate_content = (
                    candidate_content if isinstance(candidate_content, dict) else {}
                )
                parts = candidate_content.get("parts") or []
                if finish_reason in _FILTERED_FINISH_REASONS:
                    raise ProviderError(
                        "content_filtered",
                        provider=target.provider,
                        model=target.model,
                        retryable=False,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                if finish_reason == "MAX_TOKENS":
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_output_truncated",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                if not parts:
                    raise ProviderError(
                        "invalid_output",
                        provider=target.provider,
                        model=target.model,
                        retryable=True,
                        error_code="provider_response_invalid",
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        response_received=True,
                    )
                text = _joined_text_blocks(parts)
            else:
                raise ProviderError(
                    "invalid_request",
                    provider=target.provider,
                    model=target.model,
                    retryable=False,
                )

        try:
            if not isinstance(text, str):
                raise TypeError("Provider output is not text")
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            decoded = json.loads(text)
            sanitized, report = sanitize_nul_with_report(
                decoded, strip_escaped=True, path="$.provider_response"
            )
            if report.nul_removed_count or report.escaped_nul_removed_count:
                structured_log(
                    "provider.response_sanitized",
                    provider=target.provider,
                    **report.as_log_context(),
                )
            parsed = output_schema.model_validate(sanitized)
        except ValidationError as exc:
            raise ProviderError(
                "invalid_output",
                provider=target.provider,
                model=target.model,
                retryable=True,
                error_code="provider_schema_invalid",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                response_received=True,
                validation_error=exc,
            ) from exc
        except (ValueError, UnicodeDecodeError) as exc:
            raise ProviderError(
                "invalid_output",
                provider=target.provider,
                model=target.model,
                retryable=True,
                error_code="provider_invalid_json",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                response_received=True,
            ) from exc
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(
                "invalid_output",
                provider=target.provider,
                model=target.model,
                retryable=True,
                error_code="provider_response_invalid",
                prompt_tokens=locals().get("prompt_tokens", 0),
                completion_tokens=locals().get("completion_tokens", 0),
                response_received=True,
            ) from exc
        return LLMResult(
            sanitize_nul(parsed.model_dump(mode="json"), strip_escaped=True),
            target.provider,
            target.model,
            int(prompt_tokens or 0),
            int(completion_tokens or 0),
            int((time.perf_counter() - started) * 1000),
            report,
        )
