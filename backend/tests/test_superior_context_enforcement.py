import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.dialects import postgresql

import app.services.agent_context as context_module
import app.services.agent_runtime as runtime_module
from app.api.routes import preview_agent_context, require_admin, router
from app.core.config import Settings, settings
from app.db.models import AgentRun, PipelineRun, Project
from app.schemas.api import AgentContextPreviewRequest
from app.services.agent_context import (
    AgentContextComposer,
    ComposedContext,
    SuperiorContextUnavailable,
)
from app.services.agent_runtime import AgentRuntime
from app.services.llm_gateway import LLMResult, ModelTarget
from app.services.learned_skills import (
    LearnedSkillResolution,
    ResolvedLearnedSkill,
)
from app.services.superior_skills import (
    AGENT_ROLES,
    SuperiorSkillRegistry,
    active_superior_definitions,
)


class CanaryOutput(BaseModel):
    accepted: bool


class EmptyRows:
    def all(self):
        return []


def _compiled_sql(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


@pytest.mark.parametrize("role", sorted(AGENT_ROLES))
def test_each_role_definition_has_required_sections_and_bounded_size(role):
    definitions = SuperiorSkillRegistry().load_defaults()
    core = definitions["superior.global-core"]
    persona = next(x for x in definitions.values() if x.agent_role == role)
    fragment = core.prompt_fragment() + "\n\n" + persona.prompt_fragment()

    for section in (
        persona.title,
        "Missão:",
        "Expertise:",
        "Responsabilidades:",
        "Limites:",
        "Método de decisão:",
        "Política de memória:",
        "Handoff:",
        "Voz:",
    ):
        assert section in fragment
    assert 2_000 <= len(fragment) <= 4_000
    lowered = fragment.lower()
    for forbidden in ("traceback", "select * from", "authorization: bearer"):
        assert forbidden not in lowered


async def _runtime_canary(monkeypatch, role: str, mode: str):
    definitions = SuperiorSkillRegistry().load_defaults()
    captured = {"prompts": [], "runs": []}
    route = SimpleNamespace(
        primary_provider="gemini",
        primary_model="canary-model",
        fallback_provider=None,
        fallback_model=None,
        parameters={},
    )

    class FakeDb:
        def __init__(self):
            self.scalar_calls = 0

        async def scalar(self, _query):
            self.scalar_calls += 1
            return route if self.scalar_calls == 1 else None

        async def get(self, model, _identifier):
            assert model is AgentRun
            return None

        def add(self, instance):
            captured["runs"].append(instance)

        async def flush(self):
            return None

        async def commit(self):
            return None

    async def active(_db, requested_role):
        assert requested_role == role
        persona = next(x for x in definitions.values() if x.agent_role == role)
        return [definitions["superior.global-core"], persona]

    async def no_items(*_args, **_kwargs):
        return []

    async def no_cache(*_args, **_kwargs):
        return None

    learned_resolution = LearnedSkillResolution(
        skills=(
            ResolvedLearnedSkill(
                skill_id="learned.canary.approved",
                version="1.2.0",
                checksum="a" * 64,
                rules=("LEARNED RULE FOR PROVIDER CANARY",),
                characters=40,
            ),
        ),
        fragment=(
            "<approved_learned_skills>\n"
            "LEARNED RULE FOR PROVIDER CANARY\n"
            "</approved_learned_skills>"
        ),
    )

    async def learned(*_args, **_kwargs):
        return learned_resolution

    async def target(*_args, **_kwargs):
        return ModelTarget("gemini", "canary-model", "not-a-real-key")

    async def no_event(*_args, **_kwargs):
        return None

    async def generate(prompt, _schema, primary, _fallback, parameters=None):
        assert parameters == {}
        captured["prompts"].append(prompt)
        return LLMResult(
            data={"accepted": True},
            provider=primary.provider,
            model=primary.model,
            prompt_tokens=1,
            completion_tokens=1,
            latency_ms=1,
        )

    monkeypatch.setattr(settings, "superior_skills_mode", mode)
    monkeypatch.setattr(context_module, "active_superior_definitions", active)
    monkeypatch.setattr(runtime_module, "CredentialVault", lambda: object())
    runtime = AgentRuntime(FakeDb())
    monkeypatch.setattr(runtime.context, "_memories", no_items)
    monkeypatch.setattr(runtime.context, "_patterns", no_items)
    monkeypatch.setattr(runtime.context, "_cache_get", no_cache)
    monkeypatch.setattr(runtime.context, "_cache_set", no_cache)
    monkeypatch.setattr(runtime.context.learned_skills, "resolve", learned)
    monkeypatch.setattr(runtime, "_target", target)
    monkeypatch.setattr(runtime, "event", no_event)
    monkeypatch.setattr(runtime.gateway, "generate_structured", generate)

    task_prompt = (
        f"<project_context>project-canary role={role}</project_context>\n"
        f"<default_or_role_rules>rules-for-{role}</default_or_role_rules>\n"
        "<output_contract>return accepted</output_contract>\n"
        "CURRENT TASK CANARY"
    )
    result = await runtime.call(
        uuid.uuid4(),
        role,
        uuid.uuid4(),
        {"private_input": "SECRET_MUST_NOT_REACH_PROMPT"},
        task_prompt,
        CanaryOutput,
        pipeline_run_id=uuid.uuid4(),
    )
    return captured, task_prompt, result


@pytest.mark.asyncio
@pytest.mark.parametrize("role", sorted(AGENT_ROLES))
async def test_enforced_canary_sends_compiled_role_context_to_gateway(
    monkeypatch, role
):
    captured, task_prompt, result = await _runtime_canary(monkeypatch, role, "enforced")
    prompt = captured["prompts"][0]
    definitions = SuperiorSkillRegistry().load_defaults()
    persona = next(x for x in definitions.values() if x.agent_role == role)

    assert result == {"accepted": True}
    assert prompt != task_prompt
    assert "<superior_context>" in prompt
    assert persona.title in prompt
    assert task_prompt in prompt
    assert "CURRENT TASK CANARY" in prompt
    assert "LEARNED RULE FOR PROVIDER CANARY" in prompt
    assert "SECRET_MUST_NOT_REACH_PROMPT" not in prompt
    for other in (x for x in definitions.values() if x.scope == "agent"):
        if other.agent_role != role:
            assert other.title not in prompt
    stored_metadata = captured["runs"][0].input_json["_superior_context"]
    assert stored_metadata["mode"] == "enforced"
    assert stored_metadata["status"] == "ready"
    assert stored_metadata["versions"] == {
        "superior.global-core": "2.0.0",
        persona.skill_id: persona.version,
    }
    assert stored_metadata["learned_skills"] == [
        {
            "skill_id": "learned.canary.approved",
            "version": "1.2.0",
            "checksum": "a" * 64,
            "rule_count": 1,
            "characters": 40,
        }
    ]


@pytest.mark.asyncio
async def test_shadow_canary_audits_context_but_sends_only_task(monkeypatch):
    captured, task_prompt, result = await _runtime_canary(
        monkeypatch, "editor", "shadow"
    )

    assert result == {"accepted": True}
    assert captured["prompts"] == [task_prompt]
    assert "LEARNED RULE FOR PROVIDER CANARY" not in captured["prompts"][0]
    metadata = captured["runs"][0].input_json["_superior_context"]
    assert metadata["mode"] == "shadow"
    assert metadata["status"] == "ready"
    assert metadata["versions"]["superior.editor"] == "2.1.0"
    assert metadata["learned_skills"][0]["skill_id"] == "learned.canary.approved"


@pytest.mark.asyncio
async def test_enforced_fails_closed_when_active_context_is_incomplete(monkeypatch):
    core = SuperiorSkillRegistry().load_defaults()["superior.global-core"]

    async def incomplete(_db, _role):
        return [core]

    monkeypatch.setattr(settings, "superior_skills_mode", "enforced")
    monkeypatch.setattr(context_module, "active_superior_definitions", incomplete)

    with pytest.raises(SuperiorContextUnavailable, match="writer"):
        await AgentContextComposer(object()).compose(
            "writer", uuid.uuid4(), "CURRENT TASK"
        )


@pytest.mark.asyncio
async def test_shadow_missing_context_is_explicit_in_metadata(monkeypatch):
    async def missing(_db, _role):
        return []

    monkeypatch.setattr(settings, "superior_skills_mode", "shadow")
    monkeypatch.setattr(context_module, "active_superior_definitions", missing)

    result = await AgentContextComposer(object()).compose(
        "writer", uuid.uuid4(), "CURRENT TASK"
    )

    assert result.prompt == "CURRENT TASK"
    assert result.metadata["status"] == "missing-superior-skill"
    assert result.metadata["versions"] == {}


@pytest.mark.asyncio
async def test_active_definition_query_excludes_non_active_versions():
    captured = []
    definition = SuperiorSkillRegistry().load_defaults()["superior.writer"]

    class Result:
        def all(self):
            return [(object(), SimpleNamespace(definition=definition.model_dump()))]

    class FakeDb:
        async def execute(self, statement):
            captured.append(statement)
            return Result()

    loaded = await active_superior_definitions(FakeDb(), "writer")
    sql = _compiled_sql(captured[0])

    assert [item.skill_id for item in loaded] == ["superior.writer"]
    assert "superior_skill_versions.status = 'active'" in sql
    assert "superior_skill_versions.version = superior_skills.current_version" in sql


@pytest.mark.asyncio
async def test_memory_query_enforces_role_approval_and_project_isolation():
    captured = []
    project_id = uuid.uuid4()

    class FakeDb:
        async def get(self, _model, identifier):
            assert identifier == project_id
            return SimpleNamespace(niche="finance")

        async def scalars(self, statement):
            captured.append(statement)
            return EmptyRows()

    composer = AgentContextComposer(FakeDb())
    memories = await composer._memories(
        "writer",
        project_id,
        "query",
        allow_external_embeddings=False,
    )
    sql = _compiled_sql(captured[0])

    assert memories == []
    assert "agent_memories.agent_role = 'writer'" in sql
    assert "agent_memories.status = 'approved'" in sql
    assert "agent_memories.project_id IS NULL" in sql
    assert str(project_id) in sql
    assert "agent_memories.niche = 'finance'" in sql


@pytest.mark.asyncio
async def test_handoff_query_is_scoped_to_project_role_and_pipeline_run(monkeypatch):
    definitions = SuperiorSkillRegistry().load_defaults()
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    captured = []

    class FakeDb:
        async def scalar(self, statement):
            captured.append(statement)
            return None

        async def flush(self):
            return None

    async def active(_db, _role):
        return [definitions["superior.global-core"], definitions["superior.writer"]]

    async def no_items(*_args, **_kwargs):
        return []

    async def no_cache(*_args, **_kwargs):
        return None

    async def no_learned(*_args, **_kwargs):
        return LearnedSkillResolution()

    monkeypatch.setattr(settings, "superior_skills_mode", "enforced")
    monkeypatch.setattr(context_module, "active_superior_definitions", active)
    composer = AgentContextComposer(FakeDb())
    monkeypatch.setattr(composer, "_memories", no_items)
    monkeypatch.setattr(composer, "_patterns", no_items)
    monkeypatch.setattr(composer, "_cache_get", no_cache)
    monkeypatch.setattr(composer, "_cache_set", no_cache)
    monkeypatch.setattr(composer.learned_skills, "resolve", no_learned)

    await composer.compose(
        "writer", project_id, "TASK", pipeline_run_id=pipeline_run_id
    )
    sql = _compiled_sql(captured[0])

    assert "agent_handoffs.project_id" in sql and str(project_id) in sql
    assert "agent_handoffs.to_role = 'writer'" in sql
    assert "agent_handoffs.pipeline_run_id" in sql and str(pipeline_run_id) in sql
    assert "LIMIT 1" in sql


@pytest.mark.asyncio
async def test_selected_memory_and_handoff_appear_once_without_context_growth(
    monkeypatch,
):
    definitions = SuperiorSkillRegistry().load_defaults()
    memory = SimpleNamespace(
        id="approved-memory",
        content="Approved project-specific preference",
        last_used_at=None,
    )
    handoff = SimpleNamespace(
        id="same-run-handoff",
        from_role="research_gatekeeper",
        payload={"decision": "approved"},
        fact_ids=["fact-1"],
    )

    class FakeDb:
        async def scalar(self, _statement):
            return handoff

        async def flush(self):
            return None

    async def active(_db, _role):
        return [definitions["superior.global-core"], definitions["superior.writer"]]

    async def memories(*_args, **_kwargs):
        return [memory]

    async def no_items(*_args, **_kwargs):
        return []

    async def no_cache(*_args, **_kwargs):
        return None

    async def no_learned(*_args, **_kwargs):
        return LearnedSkillResolution()

    monkeypatch.setattr(settings, "superior_skills_mode", "enforced")
    monkeypatch.setattr(context_module, "active_superior_definitions", active)
    composer = AgentContextComposer(FakeDb())
    monkeypatch.setattr(composer, "_memories", memories)
    monkeypatch.setattr(composer, "_patterns", no_items)
    monkeypatch.setattr(composer, "_cache_get", no_cache)
    monkeypatch.setattr(composer, "_cache_set", no_cache)
    monkeypatch.setattr(composer.learned_skills, "resolve", no_learned)

    result = await composer.compose(
        "writer", uuid.uuid4(), "UNIQUE CURRENT TASK", pipeline_run_id=uuid.uuid4()
    )

    assert result.prompt.count("Approved project-specific preference") == 1
    assert result.prompt.count('"decision": "approved"') == 1
    assert result.prompt.count("UNIQUE CURRENT TASK") == 1
    assert result.metadata["memory_ids"] == ["approved-memory"]
    assert result.metadata["handoff_id"] == "same-run-handoff"


@pytest.mark.asyncio
async def test_admin_preview_is_protected_run_scoped_and_provider_free(monkeypatch):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    captured = {}

    class FakeDb:
        async def get(self, model, identifier):
            if model is Project and identifier == project_id:
                return SimpleNamespace(id=project_id)
            if model is PipelineRun and identifier == pipeline_run_id:
                return SimpleNamespace(id=pipeline_run_id, project_id=project_id)
            return None

    async def compose(self, role, project, query, pipeline_run_id=None, **kwargs):
        captured.update(
            role=role,
            project=project,
            query=query,
            run_id=pipeline_run_id,
            kwargs=kwargs,
        )
        return ComposedContext(
            prompt="PROMPT PREVIEW credential=provider-test-secret",
            superior_fragment="FULL CONTEXT " + "sk-" + "abcdefghijklmnopqrstuv",
            metadata={
                "mode": "enforced",
                "status": "ready",
                "versions": {"superior.writer": "1.0.0"},
                "learned_skills": [
                    {
                        "skill_id": "learned.preview.approved",
                        "version": "2.0.0",
                        "checksum": "b" * 64,
                    }
                ],
                "learned_skill_characters": 240,
                "learned_skill_truncated": False,
                "memory_ids": ["memory-1"],
                "handoff_id": "handoff-1",
                "credential": "provider-test-secret",
            },
        )

    monkeypatch.setattr(AgentContextComposer, "compose", compose)

    response = await preview_agent_context(
        AgentContextPreviewRequest(
            agent_role="writer",
            project_id=project_id,
            pipeline_run_id=pipeline_run_id,
            task="CURRENT TASK",
        ),
        FakeDb(),
    )
    preview_route = next(
        route
        for route in router.routes
        if getattr(route, "path", None) == "/api/v1/admin/agent-context/preview"
    )

    assert require_admin in [item.call for item in preview_route.dependant.dependencies]
    assert preview_route.methods == {"POST"}
    assert all(
        getattr(route, "path", None) != "/api/v1/admin/agent-context/{agent_role}"
        for route in router.routes
    )
    assert captured["run_id"] == pipeline_run_id
    assert captured["kwargs"] == {"allow_external_embeddings": False}
    assert response == {
        "mode": "enforced",
        "metadata": {
            "mode": "enforced",
            "status": "ready",
            "versions": {"superior.writer": "1.0.0"},
            "learned_skills": [
                {
                    "skill_id": "learned.preview.approved",
                    "version": "2.0.0",
                    "checksum": "b" * 64,
                }
            ],
            "learned_skill_characters": 240,
            "learned_skill_truncated": False,
            "memory_ids": ["memory-1"],
            "handoff_id": "handoff-1",
        },
        "preview": "PROMPT PREVIEW credential=***",
        "compiled_context": "FULL CONTEXT ***",
    }
    assert "provider-test-secret" not in str(response)


def test_admin_preview_request_accepts_task_alias_only_in_typed_body():
    request = AgentContextPreviewRequest(
        agent_role="writer",
        project_id=uuid.uuid4(),
        query="Tarefa recebida no corpo",
    )

    assert request.task == "Tarefa recebida no corpo"
    with pytest.raises(ValidationError):
        AgentContextPreviewRequest(
            agent_role="writer",
            project_id=uuid.uuid4(),
            task="   ",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["project", "missing_run", "foreign_run"])
async def test_admin_preview_rejects_invalid_project_and_run_before_composition(
    monkeypatch, failure
):
    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    compose_called = False

    class FakeDb:
        async def get(self, model, _identifier):
            if model is Project:
                return None if failure == "project" else SimpleNamespace(id=project_id)
            if model is PipelineRun:
                if failure == "missing_run":
                    return None
                return SimpleNamespace(id=pipeline_run_id, project_id=uuid.uuid4())
            raise AssertionError(f"Unexpected model {model}")

    async def compose(*_args, **_kwargs):
        nonlocal compose_called
        compose_called = True
        raise AssertionError("Context composition must not run")

    monkeypatch.setattr(AgentContextComposer, "compose", compose)
    request = AgentContextPreviewRequest(
        agent_role="writer",
        project_id=project_id,
        pipeline_run_id=pipeline_run_id,
        task="Preview seguro",
    )

    with pytest.raises(HTTPException) as exc:
        await preview_agent_context(request, FakeDb())

    assert exc.value.status_code == 404
    assert compose_called is False


def test_superior_mode_rejects_unknown_values():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, superior_skills_mode="invalid")
