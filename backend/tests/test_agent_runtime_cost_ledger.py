from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import BaseModel

from app.core.config import settings
from app.db.models import AgentRun, ModelRoute, PipelineRun, ProviderAttempt
from app.services.agent_context import ComposedContext
from app.services.agent_runtime import AgentRuntime, PipelineBudgetExceeded
from app.services.llm_gateway import LLMResult, ModelTarget, ProviderAttemptRecord


class Output(BaseModel):
    ok: bool


class FakeContext:
    async def compose(self, _role, _project_id, prompt, **_kwargs):
        return ComposedContext(prompt=prompt, metadata={}, superior_fragment="")


class FakeDb:
    def __init__(self, route: ModelRoute):
        self.route = route
        self.run: AgentRun | None = None
        self.pipeline = SimpleNamespace(
            estimated_external_cost_usd=0.0,
            billed_prompt_tokens=0,
            billed_completion_tokens=0,
        )
        self.provider_attempts: list[ProviderAttempt] = []
        self.commits = 0

    async def scalar(self, query):
        rendered = str(query)
        if "FROM model_routes" in rendered:
            return self.route
        if "FROM provider_attempts" in rendered:
            return None
        raise AssertionError(f"Unexpected scalar query: {rendered}")

    async def get(self, model, _identity):
        if model is AgentRun:
            return self.run
        if model is PipelineRun:
            return self.pipeline
        raise AssertionError(f"Unexpected get model: {model}")

    def add(self, instance):
        if isinstance(instance, AgentRun):
            self.run = instance
        elif isinstance(instance, ProviderAttempt):
            self.provider_attempts.append(instance)
        else:
            raise AssertionError(f"Unexpected added instance: {type(instance)}")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


class SuccessfulGateway:
    async def generate_structured(
        self,
        prompt,
        _schema,
        primary,
        _fallback=None,
        *,
        parameters=None,
        attempt_observer=None,
        before_attempt=None,
    ):
        assert parameters is not None
        if before_attempt:
            await before_attempt(primary, prompt, 1, "primary")
        if attempt_observer:
            now = datetime.now(timezone.utc)
            await attempt_observer(
                ProviderAttemptRecord(
                    provider=primary.provider,
                    model=primary.model,
                    target_kind="primary",
                    attempt_number=1,
                    status="succeeded",
                    response_received=True,
                    prompt_tokens=100,
                    completion_tokens=50,
                    latency_ms=5,
                    started_at=now,
                    finished_at=now,
                )
            )
        return LLMResult(
            data={"ok": True},
            provider=primary.provider,
            model=primary.model,
            prompt_tokens=100,
            completion_tokens=50,
            latency_ms=5,
        )


class FallbackBudgetGateway:
    async def generate_structured(
        self,
        prompt,
        _schema,
        _primary,
        fallback=None,
        *,
        parameters=None,
        attempt_observer=None,
        before_attempt=None,
    ):
        assert parameters is not None
        assert fallback is not None
        assert attempt_observer is not None
        assert before_attempt is not None
        await before_attempt(fallback, prompt, 1, "fallback")
        raise AssertionError("The fallback provider call must be blocked by the budget")


async def configured_runtime(db: FakeDb, gateway) -> AgentRuntime:
    runtime = AgentRuntime(db)  # type: ignore[arg-type]
    runtime.context = FakeContext()  # type: ignore[assignment]
    runtime.gateway = gateway

    async def target(provider: str, model: str, required: bool = True):
        del required
        return ModelTarget(provider=provider, model=model, api_key="secret")

    async def event(*_args, **_kwargs):
        return None

    runtime._target = target  # type: ignore[method-assign]
    runtime.event = event  # type: ignore[method-assign]
    return runtime


@pytest.mark.asyncio
async def test_repeated_agent_execution_persists_each_billed_provider_attempt(monkeypatch):
    monkeypatch.setattr(settings, "credential_master_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "max_pipeline_cost_usd", 100.0)
    monkeypatch.setattr(settings, "max_agent_cost_usd", 100.0)
    route = ModelRoute(
        agent_role="writer",
        primary_provider="openai",
        primary_model="gpt-4o-mini",
        parameters={
            "temperature": 0,
            "max_output_tokens": 100,
            "input_cost_per_million": 1.0,
            "output_cost_per_million": 2.0,
        },
    )
    db = FakeDb(route)
    runtime = await configured_runtime(db, SuccessfulGateway())
    project_id = uuid.uuid4()
    pipeline_id = uuid.uuid4()
    run_id = uuid.uuid4()

    await runtime.call(
        project_id,
        "writer",
        run_id,
        {},
        "write",
        Output,
        pipeline_run_id=pipeline_id,
    )
    assert db.run is not None
    db.run.status = "failed"
    db.run.output_json = None
    await runtime.call(
        project_id,
        "writer",
        run_id,
        {},
        "write again",
        Output,
        pipeline_run_id=pipeline_id,
    )

    assert [(item.run_attempt, item.attempt_number) for item in db.provider_attempts] == [
        (1, 1),
        (2, 1),
    ]
    assert db.run.attempt == 2
    assert db.run.prompt_tokens == 200
    assert db.run.completion_tokens == 100
    assert db.pipeline.billed_prompt_tokens == 200
    assert db.pipeline.billed_completion_tokens == 100
    assert float(db.pipeline.estimated_external_cost_usd) > 0


@pytest.mark.asyncio
async def test_budget_projection_uses_fallback_specific_output_limit(monkeypatch):
    monkeypatch.setattr(settings, "credential_master_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "max_pipeline_cost_usd", 10.0)
    monkeypatch.setattr(settings, "max_agent_cost_usd", 0.5)
    route = ModelRoute(
        agent_role="writer",
        primary_provider="openai",
        primary_model="gpt-4o-mini",
        fallback_provider="anthropic",
        fallback_model="claude-sonnet-5",
        parameters={
            "temperature": 0,
            "max_output_tokens": 100,
            "input_cost_per_million": 1.0,
            "output_cost_per_million": 1.0,
            "fallback_max_output_tokens": 8000,
            "fallback_input_cost_per_million": 1.0,
            "fallback_output_cost_per_million": 100.0,
        },
    )
    db = FakeDb(route)
    runtime = await configured_runtime(db, FallbackBudgetGateway())

    with pytest.raises(PipelineBudgetExceeded):
        await runtime.call(
            uuid.uuid4(),
            "writer",
            uuid.uuid4(),
            {},
            "write",
            Output,
            pipeline_run_id=uuid.uuid4(),
        )


def test_budget_token_estimate_includes_utf8_and_schema_overhead():
    prompt_only = AgentRuntime._estimated_input_tokens("ação editorial")
    with_schema = AgentRuntime._estimated_input_tokens(
        "ação editorial",
        '{"type":"object","properties":{"texto":{"type":"string"}}}',
        "Return only valid JSON matching the supplied schema.",
    )

    assert prompt_only >= 6
    assert with_schema > prompt_only
