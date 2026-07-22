from app.orchestration.executor import PipelineExecutor
from app.services.skill_registry import SkillRegistry


def test_yoast_skill_reaches_commercial_content_roles():
    registry = SkillRegistry()

    for role in ("planner", "writer", "editor", "finalizer"):
        prompt = registry.prompt_fragment(role)
        assert "seo.wordpress-yoast-premium@1.0.0" in prompt


def test_skill_prompt_includes_examples_and_description():
    prompt = SkillRegistry().prompt_fragment("writer")

    assert "Boas referências:" in prompt
    assert "Evitar:" in prompt
    assert "métricas são sinais de revisão" in prompt


def test_metadata_truncation_preserves_word_boundaries():
    value = "Uma descrição comercial específica com benefício claro e prova real"

    shortened = PipelineExecutor._truncate_at_word(value, 42)

    assert len(shortened) <= 42
    assert shortened == "Uma descrição comercial específica com"
    assert not shortened.endswith(" ")
