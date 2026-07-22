import json

import httpx
import pytest
from pydantic import BaseModel, Field, model_validator

from app.schemas.agents import (
    CuratorOutput,
    EditorOutput,
    FactExtractionOutput,
    ResearchPlanOutput,
    WriterOutput,
    WriterRevisionOutput,
)
from app.services.llm_gateway import (
    LLMGateway,
    ModelTarget,
    ProviderError,
    _assert_openai_strict_schema,
)


class StructuredPayload(BaseModel):
    value: str


class SemanticPayload(BaseModel):
    decision: str
    score: float

    @model_validator(mode="after")
    def require_consistent_decision(self):
        if self.decision == "approved" and self.score < 0.6:
            raise ValueError("approved requires score of at least 0.6")
        return self


def gemini_response(request: httpx.Request, payload: object) -> httpx.Response:
    return httpx.Response(
        200,
        request=request,
        json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 4,
            },
        },
    )


def gateway(handler, sleeps: list[float] | None = None) -> LLMGateway:
    async def no_wait(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    def client_factory(**kwargs):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            **kwargs,
        )

    return LLMGateway(
        client_factory=client_factory,
        sleep=no_wait,
        jitter=lambda _minimum, _maximum: 0,
    )


def target() -> ModelTarget:
    return ModelTarget("gemini", "gemini-test", "test-secret")


def openai_target() -> ModelTarget:
    return ModelTarget("openai", "gpt-5-mini", "test-secret")


def anthropic_target(model: str = "claude-sonnet-5") -> ModelTarget:
    return ModelTarget("anthropic", model, "test-secret")


def anthropic_response(
    request: httpx.Request,
    payload: object,
    *,
    stop_reason: str = "end_turn",
    blocks: list[dict[str, object]] | None = None,
) -> httpx.Response:
    content = blocks or [{"type": "text", "text": json.dumps(payload)}]
    return httpx.Response(
        200,
        request=request,
        json={
            "content": content,
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 11, "output_tokens": 5},
        },
    )


def openai_response(
    request: httpx.Request,
    payload: object,
    *,
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> httpx.Response:
    message = {"content": json.dumps(payload)}
    if refusal is not None:
        message["refusal"] = refusal
    return httpx.Response(
        200,
        request=request,
        json={
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": message,
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 4},
        },
    )


@pytest.mark.asyncio
async def test_429_honors_retry_after_then_succeeds():
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                request=request,
                headers={"Retry-After": "3"},
                json={"error": {"message": "quota"}},
            )
        return gemini_response(request, {"value": "ok"})

    result = await gateway(handler, sleeps).generate_structured(
        "prompt", StructuredPayload, target()
    )

    assert result.data == {"value": "ok"}
    assert calls == 2
    assert sleeps == [3.0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503, 504])
async def test_5xx_is_retried_locally(status):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(status, request=request)
        return gemini_response(request, {"value": "ok"})

    result = await gateway(handler).generate_structured(
        "prompt", StructuredPayload, target()
    )

    assert result.data["value"] == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_timeout_is_retried_locally():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        return gemini_response(request, {"value": "ok"})

    result = await gateway(handler).generate_structured(
        "prompt", StructuredPayload, target()
    )

    assert result.data["value"] == "ok"
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "category"),
    [
        (400, "invalid_request"),
        (401, "authentication"),
        (403, "authentication"),
        (404, "model_not_found"),
    ],
)
async def test_permanent_http_errors_are_not_retried(status, category):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status,
            request=request,
            json={"error": {"message": "test-secret must never escape"}},
        )

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt", StructuredPayload, target()
        )

    assert calls == 1
    assert caught.value.category == category
    assert caught.value.retryable is False
    assert caught.value.http_status == status
    assert "test-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_invalid_json_gets_one_regeneration():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return gemini_response(request, "not-json")
        return gemini_response(request, {"value": "recovered"})

    result = await gateway(handler).generate_structured(
        "prompt", StructuredPayload, target()
    )

    assert result.data == {"value": "recovered"}
    assert calls == 2


@pytest.mark.asyncio
async def test_schema_failure_stops_after_one_regeneration():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return gemini_response(request, {"unexpected": True})

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt", StructuredPayload, target()
        )

    assert calls == 2
    assert caught.value.category == "invalid_output"
    assert caught.value.retryable is False
    assert caught.value.attempts == 2


@pytest.mark.asyncio
async def test_schema_regeneration_includes_safe_validation_feedback():
    calls = 0
    repair_prompt = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls, repair_prompt
        calls += 1
        if calls == 1:
            return gemini_response(request, {"decision": "approved", "score": 0.2})
        request_payload = json.loads(request.read())
        repair_prompt = request_payload["contents"][0]["parts"][0]["text"]
        return gemini_response(request, {"decision": "insufficient", "score": 0.2})

    result = await gateway(handler).generate_structured(
        "original prompt", SemanticPayload, target()
    )

    assert result.data == {"decision": "insufficient", "score": 0.2}
    assert calls == 2
    assert "approved requires score of at least 0.6" in repair_prompt
    assert "original prompt" in repair_prompt


@pytest.mark.asyncio
async def test_content_filter_is_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            request=request,
            json={"promptFeedback": {"blockReason": "SAFETY"}},
        )

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt", StructuredPayload, target()
        )

    assert calls == 1
    assert caught.value.category == "content_filtered"
    assert caught.value.retryable is False


def test_agent_schemas_are_compatible_with_openai_strict_outputs():
    for schema in (
        ResearchPlanOutput,
        FactExtractionOutput,
        WriterOutput,
        WriterRevisionOutput,
        EditorOutput,
        CuratorOutput,
    ):
        _assert_openai_strict_schema(schema.model_json_schema())


@pytest.mark.asyncio
async def test_writer_request_enables_strict_structured_outputs():
    request_payload = {}
    fact_id = "7c1f3b37-8c22-4bfd-a66b-df3e0841e442"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_payload
        request_payload = json.loads(request.read())
        return openai_response(
            request,
            {
                "title": "Guia completo para energia solar",
                "title_evidence": [
                    {
                        "fact_id": fact_id,
                        "entailment_score": 1,
                    }
                ],
                "blocks": [
                    {
                        "block_id": None,
                        "type": "paragraph",
                        "position": 0,
                        "sentences": [
                            {
                                "text": "A orientação permanece rastreável.",
                                "is_factual": False,
                                "evidence": [],
                            }
                        ],
                    }
                ],
                "unsupported_claims": [],
            },
        )

    result = await gateway(handler).generate_structured(
        "prompt", WriterOutput, openai_target()
    )

    schema_config = request_payload["response_format"]["json_schema"]
    assert schema_config["strict"] is True
    _assert_openai_strict_schema(schema_config["schema"])
    assert result.data["unsupported_claims"] == []


@pytest.mark.asyncio
async def test_openai_truncation_gets_only_one_regeneration():
    calls = 0
    repair_prompt = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls, repair_prompt
        calls += 1
        if calls == 1:
            return openai_response(request, {}, finish_reason="length")
        repair_prompt = json.loads(request.read())["messages"][1]["content"]
        return openai_response(request, {"value": "recovered"})

    result = await gateway(handler).generate_structured(
        "original prompt", StructuredPayload, openai_target()
    )

    assert result.data == {"value": "recovered"}
    assert calls == 2
    assert "interrompida" in repair_prompt


@pytest.mark.asyncio
async def test_two_openai_truncations_stop_with_specific_diagnostic():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return openai_response(request, {}, finish_reason="length")

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt", StructuredPayload, openai_target()
        )

    assert calls == 2
    assert caught.value.error_code == "provider_output_truncated"
    assert caught.value.attempts == 2
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_openai_refusal_is_classified_and_not_retried():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return openai_response(request, {}, refusal="provider refusal")

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt", StructuredPayload, openai_target()
        )

    assert calls == 1
    assert caught.value.error_code == "provider_refusal"
    assert caught.value.category == "content_filtered"


@pytest.mark.asyncio
async def test_invalid_json_stops_after_one_regeneration_with_specific_code():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": "{invalid"},
                    }
                ]
            },
        )

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt", StructuredPayload, openai_target()
        )

    assert calls == 2
    assert caught.value.error_code == "provider_invalid_json"


@pytest.mark.asyncio
async def test_attempt_observer_receives_billed_usage_for_invalid_and_success():
    calls = 0
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return gemini_response(request, "not-an-object")
        return gemini_response(request, {"value": "recovered"})

    async def observe(record):
        attempts.append(record)

    result = await gateway(handler).generate_structured(
        "prompt",
        StructuredPayload,
        target(),
        attempt_observer=observe,
    )

    assert result.data == {"value": "recovered"}
    assert [record.status for record in attempts] == [
        "invalid_output",
        "succeeded",
    ]
    assert sum(record.prompt_tokens for record in attempts) == 20
    assert sum(record.completion_tokens for record in attempts) == 8
    assert all(record.response_received for record in attempts)
    assert all(record.finished_at >= record.started_at for record in attempts)


@pytest.mark.asyncio
async def test_gemini_truncation_preserves_billed_usage():
    calls = 0
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200,
                request=request,
                json={
                    "candidates": [
                        {
                            "finishReason": "MAX_TOKENS",
                            "content": {"parts": [{"text": '{"value":'}]},
                        }
                    ],
                    "usageMetadata": {
                        "promptTokenCount": 13,
                        "candidatesTokenCount": 7,
                    },
                },
            )
        return gemini_response(request, {"value": "recovered"})

    async def observe(record):
        attempts.append(record)

    result = await gateway(handler).generate_structured(
        "prompt",
        StructuredPayload,
        target(),
        attempt_observer=observe,
    )

    assert result.data == {"value": "recovered"}
    assert attempts[0].error_code == "provider_output_truncated"
    assert attempts[0].prompt_tokens == 13
    assert attempts[0].completion_tokens == 7


class ConstrainedStructuredPayload(BaseModel):
    value: str = Field(min_length=2, max_length=20)
    score: int = Field(default=1, ge=0, le=10)


@pytest.mark.asyncio
async def test_anthropic_sonnet_5_uses_native_structured_output_subset():
    request_payload: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_payload
        request_payload = json.loads(request.read())
        return anthropic_response(request, {"value": "ok", "score": 2})

    result = await gateway(handler).generate_structured(
        "prompt", ConstrainedStructuredPayload, anthropic_target()
    )

    assert result.data == {"value": "ok", "score": 2}
    assert "system" not in request_payload
    output_format = request_payload["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    schema = output_format["schema"]
    assert schema["additionalProperties"] is False
    value_schema = schema["properties"]["value"]
    score_schema = schema["properties"]["score"]
    assert "minLength" not in value_schema
    assert "maxLength" not in value_schema
    assert "minimum" not in score_schema
    assert "maximum" not in score_schema
    assert "default" not in score_schema
    assert "Validation constraints" in value_schema["description"]


@pytest.mark.asyncio
async def test_anthropic_refusal_is_not_retried_and_preserves_usage():
    calls = 0
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return anthropic_response(request, {}, stop_reason="refusal")

    async def observe(record):
        attempts.append(record)

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt",
            StructuredPayload,
            anthropic_target(),
            attempt_observer=observe,
        )

    assert calls == 1
    assert caught.value.category == "content_filtered"
    assert caught.value.error_code == "provider_refusal"
    assert attempts[0].prompt_tokens == 11
    assert attempts[0].completion_tokens == 5


@pytest.mark.asyncio
async def test_http_200_non_json_body_is_classified_and_retried():
    calls = 0
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, request=request, text="upstream proxy error")
        return gemini_response(request, {"value": "recovered"})

    async def observe(record):
        attempts.append(record)

    result = await gateway(handler).generate_structured(
        "prompt", StructuredPayload, target(), attempt_observer=observe
    )

    assert result.data == {"value": "recovered"}
    assert calls == 2
    assert attempts[0].error_code == "provider_invalid_response_json"
    assert attempts[0].response_received is True


@pytest.mark.asyncio
async def test_anthropic_joins_multiple_text_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        return anthropic_response(
            request,
            {},
            blocks=[
                {"type": "text", "text": '{"value":'},
                {"type": "text", "text": '"joined"}'},
            ],
        )

    result = await gateway(handler).generate_structured(
        "prompt", StructuredPayload, anthropic_target()
    )

    assert result.data == {"value": "joined"}


@pytest.mark.asyncio
async def test_malformed_provider_usage_does_not_escape_as_value_error():
    def handler(request: httpx.Request) -> httpx.Response:
        response = openai_response(request, {"value": "ok"})
        payload = json.loads(response.read())
        payload["usage"] = {
            "prompt_tokens": "not-a-number",
            "completion_tokens": None,
        }
        return httpx.Response(200, request=request, json=payload)

    result = await gateway(handler).generate_structured(
        "prompt", StructuredPayload, openai_target()
    )

    assert result.data == {"value": "ok"}
    assert result.prompt_tokens == 0
    assert result.completion_tokens == 0


@pytest.mark.asyncio
async def test_content_filter_does_not_fail_over_to_another_provider():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(
            200,
            request=request,
            json={"promptFeedback": {"blockReason": "SAFETY"}},
        )

    with pytest.raises(ProviderError) as caught:
        await gateway(handler).generate_structured(
            "prompt",
            StructuredPayload,
            target(),
            fallback=anthropic_target(),
        )

    assert caught.value.category == "content_filtered"
    assert calls == ["generativelanguage.googleapis.com"]
