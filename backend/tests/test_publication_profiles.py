from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.orchestration.executor import PipelineExecutor
from app.schemas.api import (
    ContentBriefWrite,
    ProjectCreate,
    PublicationProfileWrite,
)


def profile_payload(**overrides):
    payload = {
        "name": "Blog principal",
        "brand_name": "Marca Exemplo",
        "segment": "jardinagem",
        "brand_description": "Marca dedicada a ensinar cultivo doméstico.",
        "audience_description": "Adultos iniciantes que cultivam em casa.",
        "audience_age_min": 25,
        "audience_age_max": 45,
        "tone_of_voice": "Claro, próximo e experiente.",
    }
    payload.update(overrides)
    return payload


def test_profile_rejects_an_inverted_age_range():
    with pytest.raises(ValidationError, match="audience_age_min"):
        PublicationProfileWrite.model_validate(
            profile_payload(audience_age_min=50, audience_age_max=20)
        )


def test_content_brief_rejects_an_inverted_age_range():
    with pytest.raises(ValidationError, match="reader_age_min"):
        ContentBriefWrite(reader_age_min=60, reader_age_max=18)


def test_profiled_project_requires_the_core_editorial_brief():
    with pytest.raises(ValidationError, match="primary_keyword"):
        ProjectCreate(
            name="Novo artigo",
            topic="Um tema relevante",
            audience="Leitores interessados",
            publication_profile_id="7b03b01f-3c0c-458b-95e9-756458b4c95e",
        )


def test_legacy_project_without_profile_remains_backwards_compatible():
    project = ProjectCreate(
        name="Projeto histórico",
        topic="Tema existente",
        audience="Leitor existente",
    )

    assert project.publication_profile_id is None


def test_keyword_seed_uses_the_manifest_snapshot_not_mutable_project_data():
    executor = PipelineExecutor.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        name="Outro nome",
        topic="Um tema que poderia mudar depois",
        briefing={"primary_keyword": "palavra mutável"},
    )
    executor.execution_manifest = {
        "editorial_context": {
            "publication_profile": {
                "brand_name": "Marca Exemplo",
                "version": 3,
            },
            "content_brief": {
                "primary_keyword": "como cuidar de mudas",
                "content_objective": "ensinar o processo completo",
            },
        }
    }

    assert executor._keyword_seed_query() == "como cuidar de mudas"
    executor.project.briefing["primary_keyword"] = "valor alterado"
    assert executor._keyword_seed_query() == "como cuidar de mudas"


def test_seo_brief_preserves_the_requested_primary_and_related_keywords():
    executor = PipelineExecutor.__new__(PipelineExecutor)
    executor.project = SimpleNamespace(
        name="Guia",
        topic="Tema amplo com observações secundárias",
        audience="Leitores iniciantes",
        search_intent="informational",
    )
    executor.execution_manifest = {
        "editorial_context": {
            "publication_profile": {"brand_name": "Marca Exemplo"},
            "content_brief": {
                "primary_keyword": "cultivo em casa",
                "secondary_keywords": ["substrato para mudas", "cuidados iniciais"],
                "content_objective": "ensinar um processo coerente",
            },
        }
    }

    brief = executor._build_seo_brief(
        {
            "semantic_keywords": ["jardinagem doméstica"],
            "content_gaps": [],
            "competitor_angles": [],
        },
        ["como começar uma horta"],
    )

    assert brief["focus_keyphrase"] == "cultivo em casa"
    assert "substrato para mudas" in brief["related_keyphrases"]
    assert brief["article_angle"] == "ensinar um processo coerente"

def test_editorial_context_has_safe_backwards_compatible_defaults():
    executor = PipelineExecutor.__new__(PipelineExecutor)
    executor.execution_manifest = {}

    assert executor._editorial_context() == {
        "publication_profile": None,
        "content_brief": {},
    }
