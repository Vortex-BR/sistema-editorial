import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.api import ContentBriefWrite, ProjectCreate


def _complete_v3_brief(**overrides):
    payload = {
        "reader_start_state": "Leitor que precisa compreender o assunto antes do primeiro método.",
        "reader_final_state": "Leitor capaz de reconhecer o resultado final observável do guia.",
        "article_promise": "Explicar fundamentos, alternativas, escolha, execução e resultado final.",
        "scope_limit": "O conteúdo termina no resultado prometido e não avança para a fase seguinte.",
        "editorial_content_type": "procedural_decision_guide",
        "requires_method_comparison": True,
        "requires_external_reference_per_method": True,
        "required_methods": ["método direto", "método indireto"],
    }
    payload.update(overrides)
    return ContentBriefWrite(**payload)


def test_v3_project_may_be_created_as_draft_with_complete_contract_brief():
    payload = ProjectCreate(
        name="Guia procedural V3",
        topic="Tema procedural",
        audience="Leitor iniciante",
        editorial_pipeline_version="v3",
        start_immediately=False,
        briefing=_complete_v3_brief(),
    )

    assert payload.editorial_pipeline_version == "v3"
    assert payload.briefing.editorial_content_type == "procedural_decision_guide"



def test_default_brief_uses_domain_independent_explanatory_architecture():
    brief = ContentBriefWrite()

    assert brief.editorial_content_type == "explanatory_guide"
    assert brief.requires_method_comparison is False
    assert brief.requires_external_reference_per_method is False


def test_content_brief_accepts_a_long_editorial_protocol():
    protocol = "instrução detalhada " * 600

    brief = ContentBriefWrite(additional_context=protocol)

    assert len(brief.additional_context) > 5_000


def test_content_brief_keeps_a_finite_editorial_protocol_limit():
    with pytest.raises(ValidationError, match="at most 20000 characters"):
        ContentBriefWrite(additional_context="x" * 20_001)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("reader_start_state", 1_000),
        ("reader_final_state", 1_000),
        ("article_promise", 3_000),
        ("scope_limit", 2_000),
    ],
)
def test_brief_limits_match_the_v3_knowledge_contract(field, limit):
    with pytest.raises(ValidationError, match=f"at most {limit} characters"):
        ContentBriefWrite(**{field: "x" * (limit + 1)})


def test_project_topic_is_rejected_before_it_can_fail_inside_the_worker():
    with pytest.raises(ValidationError, match="at most 380 characters"):
        ProjectCreate(
            name="Projeto com tópico excessivo",
            topic="x" * 381,
            audience="Leitor iniciante",
            start_immediately=False,
        )


def test_v3_project_rejects_incomplete_contract_brief_even_via_api():
    with pytest.raises(ValidationError, match="complete knowledge-contract brief"):
        ProjectCreate(
            name="Guia procedural V3",
            topic="Tema procedural",
            audience="Leitor iniciante",
            editorial_pipeline_version="v3",
            start_immediately=False,
        )


def test_v3_procedural_project_requires_comparison_and_external_references():
    with pytest.raises(ValidationError, match="require method comparison"):
        ProjectCreate(
            name="Guia procedural V3",
            topic="Tema procedural",
            audience="Leitor iniciante",
            editorial_pipeline_version="v3",
            start_immediately=False,
            briefing=_complete_v3_brief(requires_method_comparison=False),
        )

    with pytest.raises(ValidationError, match="external reference per method"):
        ProjectCreate(
            name="Guia procedural V3",
            topic="Tema procedural",
            audience="Leitor iniciante",
            editorial_pipeline_version="v3",
            start_immediately=False,
            briefing=_complete_v3_brief(requires_external_reference_per_method=False),
        )


def test_v3_execution_requires_the_v3_feature_flag():
    with pytest.raises(ValidationError, match="requires EDITORIAL_PIPELINE_V3_ENABLED"):
        Settings(
            editorial_pipeline_v3_enabled=False,
            editorial_pipeline_v3_execution_enabled=True,
        )


def test_v3_execution_can_be_enabled_after_the_executable_pipeline_is_installed():
    configured = Settings(
        editorial_pipeline_v3_enabled=True,
        editorial_pipeline_v3_execution_enabled=True,
    )
    assert configured.editorial_pipeline_v3_execution_enabled is True


def test_v3_word_range_must_be_ordered():
    with pytest.raises(ValidationError, match="V3_MIN_WORD_COUNT"):
        Settings(v3_min_word_count=3000, v3_max_word_count=2000)


def test_v3_procedural_project_requires_at_least_two_named_methods():
    with pytest.raises(ValidationError, match="at least two required_methods"):
        ProjectCreate(
            name="Guia procedural V3",
            topic="Tema procedural",
            audience="Leitor iniciante",
            editorial_pipeline_version="v3",
            start_immediately=False,
            briefing=_complete_v3_brief(required_methods=["método único"]),
        )
