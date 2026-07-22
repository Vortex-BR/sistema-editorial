from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import require_admin
from app.core.config import settings
from app.schemas.agents import StylePatternExtractionOutput
from app.services.agent_context import AgentContextComposer
from app.services.learned_skills import LearnedSkillResolution
import app.services.agent_context as agent_context_module
from app.services.style_learning import StyleLearningService
from app.services.superior_skills import (
    AGENT_ROLES,
    SuperiorSkillDefinition,
    SuperiorSkillRegistry,
)


def test_registry_has_one_global_core_and_one_brain_per_llm_role():
    definitions = SuperiorSkillRegistry().load_defaults().values()

    assert sum(x.scope == "global_core" for x in definitions) == 1
    assert {x.agent_role for x in definitions if x.scope == "agent"} == AGENT_ROLES


def test_superior_skill_checksum_is_stable():
    definition = SuperiorSkillRegistry().load_defaults()["superior.writer"]

    assert definition.checksum() == definition.checksum()
    assert "Diretor de Copy e Conteúdo" in definition.prompt_fragment()
    assert "Não imitar autor" in definition.prompt_fragment()


def test_agent_scope_requires_supported_role():
    payload = SuperiorSkillRegistry().load_defaults()["superior.writer"].model_dump()
    payload["agent_role"] = "unknown"

    with pytest.raises(ValidationError):
        SuperiorSkillDefinition.model_validate(payload)


def test_memory_is_labeled_as_data_not_instruction():
    memory = SimpleNamespace(id="m1", content="Preferir abertura direta")
    pattern = SimpleNamespace(id="p1", description="Variar o ritmo")

    fragment = AgentContextComposer._memory_fragment([memory], [pattern])

    assert "não instrução nem evidência factual" in fragment
    assert "Memória m1" in fragment
    assert "Padrão editorial p1" in fragment


def test_handoff_preserves_fact_ids_and_origin():
    handoff = SimpleNamespace(
        from_role="research_gatekeeper",
        payload={"decision": "approved"},
        fact_ids=["fact-1"],
    )

    fragment = AgentContextComposer._handoff_fragment(handoff)

    assert "research_gatekeeper" in fragment
    assert "fact-1" in fragment
    assert "fatos só são válidos pelos fact_ids aprovados" in fragment


def test_style_pattern_requires_three_source_urls():
    with pytest.raises(ValidationError):
        StylePatternExtractionOutput.model_validate(
            {
                "patterns": [
                    {
                        "pattern_type": "opening",
                        "description": "Abrir com a decisão principal do leitor.",
                        "source_urls": [
                            "https://one.example/a",
                            "https://two.example/b",
                        ],
                    }
                ]
            }
        )


def test_style_excerpts_never_store_full_article():
    excerpts = StyleLearningService._excerpts("palavra " * 1000)

    assert len(excerpts) == 3
    assert all(len(x) <= 300 for x in excerpts)


def test_admin_token_uses_configured_secret(monkeypatch):
    monkeypatch.setattr(settings, "admin_api_token", "correct-secret")

    require_admin("correct-secret")
    with pytest.raises(HTTPException) as error:
        require_admin("wrong-secret")
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_enforced_context_orders_core_persona_memory_and_task(monkeypatch):
    definitions = SuperiorSkillRegistry().load_defaults()

    class FakeDb:
        async def scalar(self, _query):
            return None

        async def flush(self):
            return None

    async def active(_db, _role):
        return [definitions["superior.writer"], definitions["superior.global-core"]]

    async def no_items(*_args, **_kwargs):
        return []

    async def no_cache(*_args):
        return None

    async def no_learned(*_args):
        return LearnedSkillResolution()

    monkeypatch.setattr(agent_context_module, "active_superior_definitions", active)
    monkeypatch.setattr(settings, "superior_skills_mode", "enforced")
    composer = AgentContextComposer(FakeDb())
    monkeypatch.setattr(composer, "_memories", no_items)
    monkeypatch.setattr(composer, "_patterns", no_items)
    monkeypatch.setattr(composer, "_cache_get", no_cache)
    monkeypatch.setattr(composer, "_cache_set", no_cache)
    monkeypatch.setattr(composer.learned_skills, "resolve", no_learned)

    result = await composer.compose("writer", "project-1", "TAREFA ORIGINAL")

    assert result.prompt.index("Núcleo global") < result.prompt.index("Diretor de Copy")
    assert result.prompt.index("Diretor de Copy") < result.prompt.index(
        "approved_memory_data"
    )
    assert result.prompt.index("approved_memory_data") < result.prompt.index(
        "TAREFA ORIGINAL"
    )
    assert result.metadata["versions"] == {
        "superior.global-core": "2.0.0",
        "superior.writer": "2.1.0",
    }


@pytest.mark.asyncio
async def test_shadow_context_records_but_does_not_inject(monkeypatch):
    definitions = SuperiorSkillRegistry().load_defaults()

    class FakeDb:
        async def scalar(self, _query):
            return None

        async def flush(self):
            return None

    async def active(_db, _role):
        return [definitions["superior.global-core"], definitions["superior.editor"]]

    async def no_items(*_args, **_kwargs):
        return []

    async def no_cache(*_args):
        return None

    async def no_learned(*_args):
        return LearnedSkillResolution()

    monkeypatch.setattr(agent_context_module, "active_superior_definitions", active)
    monkeypatch.setattr(settings, "superior_skills_mode", "shadow")
    composer = AgentContextComposer(FakeDb())
    monkeypatch.setattr(composer, "_memories", no_items)
    monkeypatch.setattr(composer, "_patterns", no_items)
    monkeypatch.setattr(composer, "_cache_get", no_cache)
    monkeypatch.setattr(composer, "_cache_set", no_cache)
    monkeypatch.setattr(composer.learned_skills, "resolve", no_learned)

    result = await composer.compose("editor", "project-1", "TAREFA ORIGINAL")

    assert result.prompt == "TAREFA ORIGINAL"
    assert "Diretor Editorial" in result.superior_fragment
