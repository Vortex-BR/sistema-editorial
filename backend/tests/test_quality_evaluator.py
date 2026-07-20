import copy
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.quality_evaluator import (
    QualityEvaluationUnavailable,
    QualityEvaluator,
    RUBRIC_VERSION,
    checksum,
    evaluate_snapshot,
    quality_rubric_manifest,
    quality_summary,
)


def rubric():
    return quality_rubric_manifest(
        {
            "min_overall_score": 0.5,
            "min_axis_score": 0.0,
            "min_claim_overlap": 0.25,
            "max_duplicate_score": 0.9,
            "min_word_count": 20,
            "max_word_count": 1000,
            "min_approved_facts": 1,
            "min_h2_count": 1,
            "min_h3_count": 1,
            "min_distinct_sources": 1,
            "max_sentence_words": 30,
        }
    )


def snapshot():
    project_id = str(uuid.uuid4())
    pipeline_run_id = str(uuid.uuid4())
    question_id = str(uuid.uuid4())
    fact_id = str(uuid.uuid4())
    quote = "A energia solar reduz custos operacionais em pequenas empresas."
    return {
        "pipeline_run_id": pipeline_run_id,
        "project": {
            "id": project_id,
            "topic": "energia solar",
            "audience": "gestores de pequenas empresas",
            "language": "pt-BR",
            "search_intent": "informational",
            "content_type": "article",
        },
        "version": {
            "title": "Guia de energia solar para pequenas empresas",
            "outline": ["Como avaliar o consumo", "Próximos passos"],
            "markdown": (
                "# Guia de energia solar para pequenas empresas\n\n"
                "A energia solar reduz custos operacionais em empresas. "
                "Este guia explica como avaliar o consumo antes da decisão.\n\n"
                "## Como avaliar o consumo\n\n"
                "### Energia solar e consumo da empresa\n\n"
                "Compare as faturas recentes e considere a área disponível. "
                "Registre dúvidas e valide as condições técnicas com especialistas.\n\n"
                "## Próximos passos\n\n"
                "Organize os dados, compare propostas e documente os critérios."
            ),
            "seo": {
                "title": "Guia de energia solar para pequenas empresas",
                "meta_description": (
                    "Entenda como pequenas empresas podem avaliar energia solar, "
                    "comparar propostas e organizar uma decisão responsável."
                ),
                "slug": "guia-energia-solar-pequenas-empresas",
                "focus_keyphrase": "energia solar",
            },
        },
        "questions": [{"id": question_id, "priority": 1, "coverage_status": "covered"}],
        "facts": [
            {
                "id": fact_id,
                "project_id": project_id,
                "pipeline_run_id": pipeline_run_id,
                "question_id": question_id,
                "claim": "Energia solar reduz custos operacionais em empresas.",
                "exact_quote": quote,
                "snapshot_text": f"Introdução. {quote} Dados complementares.",
                "snapshot_id": str(uuid.uuid4()),
                "source_domain": "energia.example",
                "approved": True,
                "same_run": True,
                "conflict_group": None,
                "superseded": False,
            }
        ],
        "claims": [
            {
                "id": str(uuid.uuid4()),
                "text": "A energia solar reduz custos operacionais em empresas.",
                "is_factual": True,
                "evidence": [{"fact_id": fact_id, "producer_entailment_score": 0.99}],
            }
        ],
        "comparison_documents": [],
        "voice": ["clara", "direta"],
    }


def blocker_codes(result):
    return {item["code"] for item in result["critical_blockers"]}


def test_question_is_not_covered_when_its_fact_is_not_used_in_article():
    data = snapshot()
    second_question_id = str(uuid.uuid4())
    second_fact = copy.deepcopy(data["facts"][0])
    second_fact["id"] = str(uuid.uuid4())
    second_fact["question_id"] = second_question_id
    second_fact["source_domain"] = "second.example"
    data["questions"].append(
        {"id": second_question_id, "priority": 2, "coverage_status": "covered"}
    )
    data["facts"].append(second_fact)

    result = evaluate_snapshot(data, rubric())

    assert "core_coverage_incomplete" in blocker_codes(result)


def test_minimum_approved_fact_gate_is_objective():
    data = snapshot()
    strict_rubric = rubric()
    strict_rubric["thresholds"]["min_approved_facts"] = 2

    result = evaluate_snapshot(data, strict_rubric)

    assert "insufficient_approved_facts" in blocker_codes(result)


def test_article_above_maximum_word_count_is_blocked():
    data = snapshot()
    data["version"]["markdown"] += "\n\n" + "conteúdo " * 1100

    result = evaluate_snapshot(data, rubric())

    assert "article_too_long" in blocker_codes(result)


def test_visible_source_domain_is_blocked_even_without_source_prefix():
    data = snapshot()
    data["version"]["markdown"] += "\n\nenergia.example"

    result = evaluate_snapshot(data, rubric())

    assert "visible_source_attribution" in blocker_codes(result)


def test_interrogative_internal_heading_is_blocked():
    data = snapshot()
    data["version"]["markdown"] = data["version"]["markdown"].replace(
        "## Como avaliar o consumo",
        "## Como avaliar o consumo?",
    )

    result = evaluate_snapshot(data, rubric())

    assert "internal_question_heading" in blocker_codes(result)


def test_internal_question_copied_without_question_mark_is_blocked():
    data = snapshot()
    data["questions"][0]["question"] = "Como avaliar o consumo?"

    result = evaluate_snapshot(data, rubric())

    assert "internal_question_heading" in blocker_codes(result)


def test_overlong_heading_is_blocked():
    data = snapshot()
    long_heading = (
        "Como avaliar cuidadosamente todas as condições disponíveis antes de "
        "tomar uma decisão detalhada sobre o projeto"
    )
    data["version"]["markdown"] = data["version"]["markdown"].replace(
        "## Como avaliar o consumo",
        f"## {long_heading}",
    )

    result = evaluate_snapshot(data, rubric())

    assert "overlong_heading" in blocker_codes(result)


def test_foreign_language_fragment_is_blocked_in_portuguese_article():
    data = snapshot()
    data["version"]["markdown"] += (
        "\n\nTransplanting into larger containers should happen with care."
    )

    result = evaluate_snapshot(data, rubric())

    assert "foreign_language_fragment" in blocker_codes(result)


def test_repeated_mechanical_sentence_opening_is_blocked():
    data = snapshot()
    data["version"]["markdown"] += (
        "\n\nOutro ponto verificado é que o consumo deve ser avaliado. "
        "Outro ponto verificado é que a área deve ser medida. "
        "Outro ponto verificado é que os dados devem ser organizados."
    )

    result = evaluate_snapshot(data, rubric())

    assert "repetitive_template_language" in blocker_codes(result)


def test_generic_emergency_summary_language_is_blocked():
    data = snapshot()
    data["version"]["markdown"] += (
        "\n\nEste guia reúne, em linguagem direta, os principais pontos."
    )

    result = evaluate_snapshot(data, rubric())

    assert "generic_template_language" in blocker_codes(result)


def test_visible_meta_narration_is_blocked():
    data = snapshot()
    data["version"]["markdown"] += (
        "\n\nA seguir, explico por que cada etapa merece atenção."
    )

    result = evaluate_snapshot(data, rubric())

    assert "visible_meta_narration" in blocker_codes(result)


def test_uniform_heading_and_paragraph_formula_is_blocked():
    data = snapshot()
    sections = []
    for index in range(6):
        sections.append(
            f"## Critério editorial {index}\n\n"
            "A condição precisa ser observada com atenção antes de iniciar. "
            "O acompanhamento ajuda a reconhecer mudanças durante o processo."
        )
    data["version"]["markdown"] = (
        "# Guia de energia solar para pequenas empresas\n\n"
        "### Energia solar e critérios\n\n" + "\n\n".join(sections)
    )

    result = evaluate_snapshot(data, rubric())

    assert "mechanical_prose_pattern" in blocker_codes(result)


def test_writer_cannot_remove_uncertainty_from_the_approved_fact():
    data = snapshot()
    fact = data["facts"][0]
    fact["claim"] = "A energia solar pode reduzir custos operacionais em empresas."
    fact["exact_quote"] = (
        "A energia solar pode reduzir custos operacionais em pequenas empresas."
    )
    fact["snapshot_text"] = fact["exact_quote"]
    data["claims"][0]["text"] = "A energia solar reduz custos operacionais em empresas."

    result = evaluate_snapshot(data, rubric())

    assert "claim_not_supported" in blocker_codes(result)


def test_topic_in_title_alone_does_not_fake_brief_alignment():
    data = snapshot()
    data["version"]["markdown"] = (
        "# Guia de energia solar para pequenas empresas\n\n"
        "Organize documentos internos e converse com a equipe responsável.\n\n"
        "## Critérios da análise\n\n"
        "### Próximos passos\n\n"
        "Compare os registros disponíveis antes de tomar uma decisão."
    )

    result = evaluate_snapshot(data, rubric())

    assert "brief_misaligned" in blocker_codes(result)


def test_unexplained_temperature_range_variation_is_blocked():
    data = snapshot()
    data["version"]["markdown"] += (
        "\n\nA temperatura recomendada é de 20 a 24 °C. "
        "A temperatura recomendada é de 20 a 25 °C."
    )

    result = evaluate_snapshot(data, rubric())

    assert "unexplained_numeric_variation" in blocker_codes(result)


def test_contextualized_temperature_range_variation_is_allowed():
    data = snapshot()
    data["version"]["markdown"] += (
        "\n\nUma orientação usa 20 a 24 °C. As faixas variam conforme o "
        "contexto analisado. Outra orientação usa 20 a 25 °C."
    )

    result = evaluate_snapshot(data, rubric())

    assert "unexplained_numeric_variation" not in blocker_codes(result)


def test_five_superficial_sections_do_not_count_as_a_developed_article():
    data = snapshot()
    strict_rubric = rubric()
    strict_rubric["thresholds"]["min_h2_count"] = 5
    data["version"]["markdown"] = (
        "# Guia de energia solar para pequenas empresas\n\n"
        "A energia solar reduz custos operacionais em empresas.\n\n"
        "## Energia solar e contexto\n\nTexto curto.\n\n"
        "### Consumo\n\nDetalhe curto.\n\n"
        "## Avaliação\n\nTexto curto.\n\n"
        "## Instalação\n\nTexto curto.\n\n"
        "## Cuidados\n\nTexto curto.\n\n"
        "## Próximos passos\n\nTexto curto."
    )

    result = evaluate_snapshot(data, strict_rubric)

    assert "shallow_section_development" in blocker_codes(result)


def add_conflicting_fact(
    data,
    group: str,
    *,
    superseded: bool = False,
    approved: bool = True,
    pipeline_run_id: str | None = None,
):
    data["facts"][0]["conflict_group"] = group
    second = copy.deepcopy(data["facts"][0])
    second["id"] = str(uuid.uuid4())
    second["approved"] = approved
    second["superseded"] = superseded
    if pipeline_run_id is not None:
        second["pipeline_run_id"] = pipeline_run_id
        second["same_run"] = pipeline_run_id == data["pipeline_run_id"]
    data["facts"].append(second)
    return data["facts"][0]["id"], second["id"]


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


def test_citation_must_really_exist_in_run_snapshot():
    data = snapshot()
    data["facts"][0]["snapshot_text"] = "O snapshot não contém a citação."

    result = evaluate_snapshot(data, rubric())

    assert result["status"] == "blocked"
    assert "citation_absent_from_snapshot" in blocker_codes(result)


def test_claim_support_is_recalculated_without_trusting_writer_score():
    data = snapshot()
    data["claims"][0]["text"] = "O investimento sempre dobra o lucro em sete dias."
    data["claims"][0]["evidence"][0]["producer_entailment_score"] = 1.0

    result = evaluate_snapshot(data, rubric())

    assert result["status"] == "blocked"
    assert "claim_not_supported" in blocker_codes(result)
    metrics = result["axes"]["claim_entailment"]["metrics"]
    assert metrics["producer_score_used_for_gate"] is False
    assert metrics["mean_producer_reported_score"] == 1.0


def test_partial_question_coverage_blocks_editorial_delivery():
    data = snapshot()
    data["questions"].append(
        {"id": str(uuid.uuid4()), "priority": 2, "coverage_status": "uncovered"}
    )

    result = evaluate_snapshot(data, rubric())

    assert result["status"] == "blocked"
    assert result["axes"]["coverage_factual"]["score"] == 0.6
    assert "core_coverage_incomplete" in blocker_codes(result)


def test_missing_supporting_question_is_advisory_when_core_is_covered():
    data = snapshot()
    data["questions"].append(
        {
            "id": str(uuid.uuid4()),
            "priority": 2,
            "importance": "supporting",
            "coverage_status": "uncovered",
        }
    )

    result = evaluate_snapshot(data, rubric())

    assert result["axes"]["coverage_factual"]["score"] == 0.8
    assert "core_coverage_incomplete" not in blocker_codes(result)
    assert "non_core_coverage_partial" in {item["code"] for item in result["warnings"]}


def test_two_active_facts_in_the_same_group_are_a_critical_conflict():
    data = snapshot()
    active_ids = add_conflicting_fact(data, "measurement")

    result = evaluate_snapshot(data, rubric())

    assert result["status"] == "blocked"
    assert result["axes"]["conflicts"] == {
        "score": 0.0,
        "metrics": {
            "unresolved_groups": ["measurement"],
            "active_fact_ids": {"measurement": sorted(active_ids)},
        },
    }
    blocker = next(
        item
        for item in result["critical_blockers"]
        if item["code"] == "unresolved_conflict"
    )
    assert blocker["details"]["active_fact_ids"] == {"measurement": sorted(active_ids)}


def test_one_active_and_one_superseded_fact_resolve_the_conflict():
    data = snapshot()
    add_conflicting_fact(data, "measurement", superseded=True)

    result = evaluate_snapshot(data, rubric())

    assert result["status"] == "passed"
    assert "unresolved_conflict" not in blocker_codes(result)
    assert result["axes"]["conflicts"]["score"] == 1.0
    assert result["axes"]["conflicts"]["metrics"] == {
        "unresolved_groups": [],
        "active_fact_ids": {},
    }


def test_single_or_invalid_grouped_fact_does_not_create_a_conflict():
    single = snapshot()
    single["facts"][0]["conflict_group"] = "measurement"
    invalid = snapshot()
    add_conflicting_fact(invalid, "measurement", approved=False)

    single_result = evaluate_snapshot(single, rubric())
    invalid_result = evaluate_snapshot(invalid, rubric())

    assert single_result["status"] == "passed"
    assert invalid_result["status"] == "passed"
    assert "unresolved_conflict" not in blocker_codes(single_result)
    assert "unresolved_conflict" not in blocker_codes(invalid_result)


def test_fact_from_another_run_does_not_create_a_conflict():
    data = snapshot()
    add_conflicting_fact(data, "measurement", pipeline_run_id=str(uuid.uuid4()))

    result = evaluate_snapshot(data, rubric())

    assert "unresolved_conflict" not in blocker_codes(result)
    assert result["axes"]["conflicts"]["score"] == 1.0


def test_good_seo_cannot_compensate_for_a_factual_failure():
    data = snapshot()
    data["facts"][0]["exact_quote"] = "Citação inexistente"

    result = evaluate_snapshot(data, rubric())

    assert result["axes"]["seo_structure"]["score"] == 1.0
    assert result["status"] == "blocked"
    assert result["overall_score"] > 0


def test_rubric_and_result_are_versioned_and_reproducible():
    data = snapshot()
    configured = rubric()

    first = evaluate_snapshot(copy.deepcopy(data), configured)
    second = evaluate_snapshot(copy.deepcopy(data), configured)

    assert configured["version"] == RUBRIC_VERSION
    assert len(configured["checksum"]) == 64
    assert first == second
    assert first["result_checksum"] == checksum(
        {key: value for key, value in first.items() if key != "result_checksum"}
    )
    assert first["automatic_publication"] is False


def test_human_decision_is_compared_without_mutating_evaluation():
    result = evaluate_snapshot(snapshot(), rubric())
    row = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
        article_version_id=uuid.uuid4(),
        rubric_version=result["rubric_version"],
        rubric_checksum=result["rubric_checksum"],
        evaluator_kind="deterministic",
        status=result["status"],
        overall_score=result["overall_score"],
        result_checksum=result["result_checksum"],
        result_json=result,
        thresholds_json=result["thresholds"],
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    summary = quality_summary(row, human_decision="approved")

    assert summary["human_comparison"]["human_decision"] == "approved"
    assert isinstance(summary["human_comparison"]["agreement"], bool)
    assert summary["result_checksum"] == row.result_checksum
    json.dumps(summary)


@pytest.mark.asyncio
async def test_resume_rejects_persisted_quality_result_drift():
    version = SimpleNamespace(id=uuid.uuid4())
    row = SimpleNamespace(
        article_version_id=version.id,
        result_json={"status": "passed", "result_checksum": "a" * 64},
        result_checksum="a" * 64,
    )

    class Db:
        async def scalar(self, _statement):
            return row

    with pytest.raises(QualityEvaluationUnavailable, match="checksum"):
        await QualityEvaluator(Db()).evaluate(
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=uuid.uuid4()),
            version,
        )


@pytest.mark.asyncio
async def test_service_persists_once_and_resume_reuses_the_same_result():
    source = snapshot()
    project = SimpleNamespace(
        id=uuid.uuid4(),
        topic=source["project"]["topic"],
        audience=source["project"]["audience"],
        search_intent=source["project"]["search_intent"],
        content_type="article",
    )
    run = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    article = SimpleNamespace(id=uuid.uuid4(), project_id=project.id)
    version = SimpleNamespace(
        id=uuid.uuid4(),
        article_id=article.id,
        pipeline_run_id=run.id,
        title=source["version"]["title"],
        outline=source["version"]["outline"],
        final_markdown=source["version"]["markdown"],
        seo_metadata=source["version"]["seo"],
    )
    question = SimpleNamespace(
        id=uuid.uuid4(),
        question="Como avaliar energia solar?",
        priority=1,
        coverage_status="covered",
    )
    fact = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project.id,
        pipeline_run_id=run.id,
        research_question_id=question.id,
        claim_text="Energia solar reduz custos operacionais em empresas.",
        exact_quote="A energia solar reduz custos operacionais em pequenas empresas.",
        approved=True,
        conflict_group=None,
        superseded_by_id=None,
        created_at=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )
    source_snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        pipeline_run_id=run.id,
        domain="energia.example",
        snapshot_text=(
            "A energia solar reduz custos operacionais em pequenas empresas."
        ),
    )
    claim = SimpleNamespace(
        id=uuid.uuid4(),
        text="A energia solar reduz custos operacionais em empresas.",
        is_factual=True,
        position=0,
    )
    evidence = SimpleNamespace(
        sentence_claim_id=claim.id,
        fact_id=fact.id,
        entailment_score=1.0,
    )
    manifest = SimpleNamespace(
        manifest_json={
            "quality_evaluator": rubric(),
            "super_skills": {
                "writer": [{"definition": {"voice": ["clara", "direta"]}}]
            },
        }
    )

    class Db:
        def __init__(self):
            self.evaluation = None
            self.add_count = 0

        async def scalar(self, statement):
            sql = str(statement)
            if "FROM quality_evaluations" in sql:
                return self.evaluation
            if "FROM execution_manifests" in sql:
                return manifest
            if "FROM research_plans" in sql:
                return None
            raise AssertionError(f"Unexpected scalar: {sql}")

        async def scalars(self, statement):
            sql = str(statement)
            if "FROM research_questions" in sql:
                return Rows([question])
            if "FROM sentence_claims" in sql:
                return Rows([claim])
            if "FROM articles" in sql:
                return Rows([])
            raise AssertionError(f"Unexpected scalars: {sql}")

        async def execute(self, statement):
            sql = str(statement)
            if "FROM fact_ledger LEFT OUTER JOIN source_snapshots" in sql:
                return Rows([(fact, source_snapshot)])
            if "FROM claim_evidence JOIN fact_ledger" in sql:
                return Rows([(evidence, fact)])
            raise AssertionError(f"Unexpected execute: {sql}")

        def add(self, value):
            self.add_count += 1
            value.id = uuid.uuid4()
            value.created_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
            self.evaluation = value

        async def flush(self):
            return None

    db = Db()
    evaluator = QualityEvaluator(db)

    first = await evaluator.evaluate(project, run, article, version)
    resumed = await evaluator.evaluate(project, run, article, version)

    assert first is resumed
    assert first.status == "passed"
    assert first.result_json["automatic_publication"] is False
    assert db.add_count == 1
