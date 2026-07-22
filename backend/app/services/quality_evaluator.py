import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.sanitization import sanitize_nul
from app.db.models import (
    Article,
    ArticleBlock,
    ArticleVersion,
    ClaimEvidence,
    ExecutionManifest,
    FactLedger,
    PipelineRun,
    Project,
    QualityEvaluation,
    ResearchPlan,
    ResearchQuestion,
    SentenceClaim,
    SourceSnapshot,
)
from app.services.fact_conflicts import unresolved_fact_conflicts


RUBRIC_VERSION = "quality-rubric.v5"
RUBRIC_DEFINITION = {
    "axes": {
        "coverage_factual": 0.14,
        "citation_presence": 0.14,
        "claim_entailment": 0.14,
        "conflicts": 0.10,
        "duplication": 0.08,
        "seo_structure": 0.08,
        "readability": 0.07,
        "search_intent": 0.07,
        "commercial_value": 0.06,
        "voice_adherence": 0.06,
        "briefing_completeness": 0.06,
    },
    "critical_rules": [
        "citation_absent_from_snapshot",
        "claim_not_supported",
        "unresolved_conflict",
        "material_duplicate",
        "false_commercial_promise",
        "core_coverage_incomplete",
        "insufficient_source_diversity",
        "article_too_short",
        "article_too_long",
        "insufficient_approved_facts",
        "invalid_heading_structure",
        "foreign_language_fragment",
        "brief_misaligned",
        "unexplained_numeric_variation",
        "severe_mechanical_prose",
        "mechanical_prose_pattern",
        "internal_question_heading",
        "overlong_heading",
        "repetitive_template_language",
        "generic_template_language",
        "visible_meta_narration",
        "shallow_section_development",
        "visible_source_attribution",
    ],
    "coverage_partial_is_advisory": True,
    "producer_entailment_is_advisory_only": True,
    "automatic_publication": False,
}
_VISIBLE_ATTRIBUTION = re.compile(
    r"(?i)(?:https?://|\bwww\.|\bsegundo\s+(?:as?\s+)?fontes?\b|"
    r"\bfontes?\s+aprovadas?\b|\bfatos?\s+aprovados?\b|"
    r"\bevidências?\s+aprovadas?\b|\bconsulte\s+(?:as?\s+)?"
    r"(?:fontes?|referências?)\b|\beste\s+(?:artigo|guia|texto)\s+"
    r"(?:se\s+baseia|foi\s+baseado)\b)"
)
_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "entre",
    "for",
    "from",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "of",
    "os",
    "para",
    "por",
    "que",
    "the",
    "to",
    "um",
    "uma",
    "with",
}
_BRIEF_META_WORDS = {
    "artigo",
    "como",
    "completo",
    "conteúdo",
    "conseguir",
    "compreender",
    "entender",
    "ensinar",
    "explicar",
    "guia",
    "leitor",
    "mostrar",
    "principal",
    "qual",
    "quais",
    "terminar",
    "texto",
}
_FALSE_PROMISES = (
    "100% garantido",
    "resultado garantido",
    "resultados garantidos",
    "sem qualquer risco",
    "lucro garantido",
    "cura garantida",
    "sucesso garantido",
)
_GENERIC_TEMPLATE_LANGUAGE = re.compile(
    r"(?i)(?:\beste\s+guia\s+reúne\b|"
    r"\bo\s+conteúdo\s+foi\s+organizado\b|"
    r"\buse\s+os\s+pontos\s+apresentados\s+como\s+base\b|"
    r"\bsíntese\s+prática\b|"
    r"\bpara\s+decidir\s+o\s+próximo\s+passo\b|"
    r"\ba\s+sequência\s+essencial\s+é\s+clara\b)"
)
_VISIBLE_META_NARRATION = re.compile(
    r"(?i)(?:\ba\s+seguir,\s*(?:(?:eu|nós)\s+)?"
    r"(?:explico|explicamos|mostro|mostramos|apresento|apresentamos|"
    r"detalho|detalhamos|veremos)\b|"
    r"\b(?:neste|nesse)\s+(?:artigo|guia|texto|conteúdo)\b|"
    r"\bao\s+longo\s+(?:deste|desse)\s+(?:artigo|guia|texto|conteúdo)\b|"
    r"\bvamos\s+(?:ver|entender|explorar|analisar|descobrir)\b)"
)
_ENGLISH_MARKERS = {
    "and",
    "containers",
    "from",
    "into",
    "larger",
    "seed",
    "seeds",
    "should",
    "the",
    "transplanting",
    "when",
    "with",
}
_SPANISH_MARKERS = {
    "consiste",
    "cultivo",
    "hacia",
    "humedad",
    "re-ubicar",
    "semillas",
    "suelo",
    "trasplantar",
    "ubicación",
}
_INTENT_TERMS = {
    "informational": ("como", "o que", "guia", "entenda", "passo"),
    "commercial": ("comparar", "melhor", "benefício", "preço", "escolher"),
    "transactional": ("comprar", "contratar", "solicitar", "orçamento", "preço"),
    "navigational": ("site", "contato", "acesso", "página", "oficial"),
}


class QualityEvaluationError(RuntimeError):
    code = "QUALITY_EVALUATION_FAILED"


class QualityEvaluationUnavailable(QualityEvaluationError):
    code = "QUALITY_RUBRIC_UNAVAILABLE"


def checksum(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def configured_thresholds() -> dict[str, float | int]:
    return {
        "min_overall_score": settings.quality_min_overall_score,
        "min_axis_score": settings.quality_min_axis_score,
        "min_claim_overlap": settings.quality_min_claim_overlap,
        "max_duplicate_score": settings.quality_max_duplicate_score,
        "min_word_count": settings.quality_min_word_count,
        "max_word_count": settings.quality_max_word_count,
        "min_approved_facts": settings.quality_min_approved_facts,
        "min_h2_count": settings.quality_min_h2_count,
        "min_h3_count": settings.quality_min_h3_count,
        "min_distinct_sources": settings.min_distinct_sources,
        "max_sentence_words": settings.quality_max_sentence_words,
    }


def quality_rubric_manifest(
    thresholds: dict[str, float | int] | None = None,
) -> dict[str, Any]:
    return {
        "version": RUBRIC_VERSION,
        "checksum": checksum(RUBRIC_DEFINITION),
        "evaluator_kind": "deterministic",
        "thresholds": dict(thresholds or configured_thresholds()),
    }


def evaluate_snapshot(
    snapshot: dict[str, Any], rubric: dict[str, Any]
) -> dict[str, Any]:
    _validate_rubric(rubric)
    thresholds = rubric["thresholds"]
    questions = snapshot.get("questions") or []
    facts = snapshot.get("facts") or []
    claims = snapshot.get("claims") or []
    markdown = str(snapshot.get("version", {}).get("markdown") or "")
    title = str(snapshot.get("version", {}).get("title") or "")
    seo = snapshot.get("version", {}).get("seo") or {}
    outline = snapshot.get("version", {}).get("outline") or []
    project = snapshot.get("project") or {}
    editorial_context = snapshot.get("editorial_context") or {}
    content_brief = editorial_context.get("content_brief") or {}
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    axes: dict[str, dict[str, Any]] = {}

    approved_facts = [fact for fact in facts if fact.get("approved")]
    cited_fact_ids = {
        str(evidence.get("fact_id"))
        for claim in claims
        for evidence in claim.get("evidence") or []
        if evidence.get("fact_id")
    }
    approved_by_question: dict[str, int] = defaultdict(int)
    for fact in approved_facts:
        if str(fact.get("id")) in cited_fact_ids:
            approved_by_question[str(fact.get("question_id"))] += 1
    core_questions = [
        question
        for question in questions
        if str(question.get("importance") or "core") == "core"
    ] or list(questions)
    supporting_questions = [
        question
        for question in questions
        if str(question.get("importance") or "core") == "supporting"
    ]
    optional_questions = [
        question
        for question in questions
        if str(question.get("importance") or "core") == "optional"
    ]

    def uncovered(items: list[dict[str, Any]]) -> list[str]:
        return [
            str(question.get("id"))
            for question in items
            if str(question.get("coverage_status")) != "covered"
            or approved_by_question[str(question.get("id"))] == 0
        ]

    missing_core = uncovered(core_questions)
    missing_supporting = uncovered(supporting_questions)
    missing_optional = uncovered(optional_questions)
    core_score = (
        (len(core_questions) - len(missing_core)) / len(core_questions)
        if core_questions
        else 0.0
    )
    supporting_score = (
        (len(supporting_questions) - len(missing_supporting))
        / len(supporting_questions)
        if supporting_questions
        else 1.0
    )
    coverage_score = round((core_score * 0.8) + (supporting_score * 0.2), 4)
    axes["coverage_factual"] = _axis(
        coverage_score,
        total_questions=len(questions),
        core_question_count=len(core_questions),
        missing_core_question_ids=missing_core,
        missing_supporting_question_ids=missing_supporting,
        missing_optional_question_ids=missing_optional,
    )
    if missing_core or not core_questions:
        blockers.append(_blocker("core_coverage_incomplete", question_ids=missing_core))
    if missing_supporting or missing_optional:
        warnings.append(
            {
                "code": "non_core_coverage_partial",
                "supporting_question_ids": missing_supporting,
                "optional_question_ids": missing_optional,
                "message": (
                    "A promessa central está sustentada, mas há tópicos de apoio "
                    "ou opcionais sem evidência suficiente."
                ),
            }
        )

    distinct_domains = {
        str(fact.get("source_domain") or "").strip().casefold()
        for fact in approved_facts
        if str(fact.get("source_domain") or "").strip()
    }
    minimum_distinct_sources = int(thresholds["min_distinct_sources"])
    if len(distinct_domains) < minimum_distinct_sources:
        blockers.append(
            _blocker(
                "insufficient_source_diversity",
                distinct_source_count=len(distinct_domains),
                minimum_distinct_sources=minimum_distinct_sources,
            )
        )

    minimum_approved_facts = int(thresholds["min_approved_facts"])
    if len(approved_facts) < minimum_approved_facts:
        blockers.append(
            _blocker(
                "insufficient_approved_facts",
                approved_fact_count=len(approved_facts),
                minimum_approved_facts=minimum_approved_facts,
            )
        )

    facts_by_id = {str(fact.get("id")): fact for fact in facts}
    invalid_citations = [
        str(fact.get("id")) for fact in approved_facts if not _citation_is_present(fact)
    ]
    citation_score = (
        (len(approved_facts) - len(invalid_citations)) / len(approved_facts)
        if approved_facts
        else 0.0
    )
    axes["citation_presence"] = _axis(
        citation_score,
        approved_fact_count=len(approved_facts),
        invalid_fact_ids=invalid_citations,
    )
    if invalid_citations or not approved_facts:
        blockers.append(
            _blocker("citation_absent_from_snapshot", fact_ids=invalid_citations)
        )

    unsupported_claims: list[str] = []
    semantic_review_claims: list[str] = []
    overlap_scores: list[float] = []
    producer_scores: list[float] = []
    factual_claims = [claim for claim in claims if claim.get("is_factual", True)]
    for claim in factual_claims:
        supported = False
        best_overlap = 0.0
        claim_text = str(claim.get("text") or "")
        for evidence in claim.get("evidence") or []:
            fact = facts_by_id.get(str(evidence.get("fact_id")))
            if fact is None or not fact.get("approved") or not fact.get("same_run"):
                continue
            reported = evidence.get("producer_entailment_score")
            if isinstance(reported, (int, float)):
                producer_scores.append(float(reported))
            overlap = _claim_overlap(claim_text, fact)
            best_overlap = max(best_overlap, overlap)
            deterministic_support = all(
                (
                    _citation_is_present(fact),
                    _numbers_are_supported(claim_text, fact),
                    _claim_scope_is_preserved(claim_text, fact),
                    _negation_is_preserved(claim_text, fact),
                    _causality_is_preserved(claim_text, fact),
                    _semantic_anchor_score(claim_text, fact) > 0,
                )
            )
            if deterministic_support:
                supported = True
        overlap_scores.append(best_overlap)
        if supported and best_overlap < float(thresholds["min_claim_overlap"]):
            semantic_review_claims.append(str(claim.get("id")))
        if not supported:
            unsupported_claims.append(str(claim.get("id")))
    entailment_score = (
        (len(factual_claims) - len(unsupported_claims)) / len(factual_claims)
        if factual_claims
        else 0.0
    )
    axes["claim_entailment"] = _axis(
        entailment_score,
        factual_claim_count=len(factual_claims),
        unsupported_claim_ids=unsupported_claims,
        semantic_review_claim_ids=semantic_review_claims,
        mean_independent_overlap=_mean(overlap_scores),
        mean_producer_reported_score=_mean(producer_scores),
        producer_score_used_for_gate=False,
        lexical_overlap_used_as_advisory=True,
    )
    if unsupported_claims or not factual_claims:
        blockers.append(_blocker("claim_not_supported", claim_ids=unsupported_claims))
    if semantic_review_claims:
        warnings.append(
            {
                "code": "claim_semantic_review_recommended",
                "claim_ids": semantic_review_claims,
                "message": (
                    "As validações de números, escopo, negação e causalidade passaram, "
                    "mas a paráfrase é semanticamente distante do registro factual."
                ),
            }
        )

    unresolved_conflicts = unresolved_fact_conflicts(
        facts,
        project_id=project.get("id"),
        pipeline_run_id=snapshot.get("pipeline_run_id"),
        valid_fact_ids=(
            fact.get("id") for fact in approved_facts if fact.get("same_run")
        ),
    )
    conflict_groups = [conflict.group for conflict in unresolved_conflicts]
    active_conflict_fact_ids = {
        conflict.group: list(conflict.active_fact_ids)
        for conflict in unresolved_conflicts
    }
    axes["conflicts"] = _axis(
        1.0 if not conflict_groups else 0.0,
        unresolved_groups=conflict_groups,
        active_fact_ids=active_conflict_fact_ids,
    )
    if conflict_groups:
        blockers.append(
            _blocker(
                "unresolved_conflict",
                conflict_groups=conflict_groups,
                active_fact_ids=active_conflict_fact_ids,
            )
        )

    duplicate_scores = [
        _shingle_similarity(markdown, candidate)
        for candidate in snapshot.get("comparison_documents") or []
        if str(candidate).strip()
    ]
    maximum_duplicate = max(duplicate_scores, default=0.0)
    axes["duplication"] = _axis(
        1.0 - maximum_duplicate,
        maximum_similarity=round(maximum_duplicate, 4),
        comparison_count=len(duplicate_scores),
    )
    if maximum_duplicate >= float(thresholds["max_duplicate_score"]):
        blockers.append(
            _blocker("material_duplicate", similarity=round(maximum_duplicate, 4))
        )

    headings = re.findall(r"(?m)^#{1,3}\s+.+$", markdown)
    subheadings = re.findall(r"(?m)^#{2,3}\s+.+$", markdown)
    h1_count = len(re.findall(r"(?m)^#\s+.+$", markdown))
    h2_count = len(re.findall(r"(?m)^##\s+.+$", markdown))
    h3_count = len(re.findall(r"(?m)^###\s+.+$", markdown))
    normalized_questions = {
        _normalize(str(question.get("question") or "")).rstrip("?.!:;")
        for question in questions
        if str(question.get("question") or "").strip()
    }
    internal_question_headings = [
        heading
        for heading in subheadings
        if (
            heading.rstrip().endswith("?")
            or _normalize(re.sub(r"^#{2,3}\s+", "", heading)).rstrip("?.!:;")
            in normalized_questions
        )
    ]
    overlong_headings = [
        heading
        for heading in subheadings
        if len(re.sub(r"^#{2,3}\s+", "", heading).strip()) > 80
    ]
    meta_description = str(seo.get("meta_description") or "")
    focus_tokens = _tokens(str(seo.get("focus_keyphrase") or ""))
    subheading_tokens = _tokens(" ".join(subheadings))
    seo_title = str(seo.get("title") or title).strip()
    title_tokens = _tokens(seo_title)
    focus_in_title = bool(focus_tokens) and focus_tokens <= title_tokens
    focus_in_headings = bool(focus_tokens) and (
        len(focus_tokens & subheading_tokens) / len(focus_tokens) >= 0.6
    )
    seo_checks = {
        "title_present": bool(seo_title),
        "title_length": 15 <= len(seo_title) <= 60,
        "meta_description": 70 <= len(meta_description.strip()) <= 160,
        "slug_present": bool(str(seo.get("slug") or "").strip()),
        "focus_keyphrase": bool(str(seo.get("focus_keyphrase") or "").strip()),
        "heading_structure": bool(headings or outline),
        "single_h1": h1_count == 1,
        "h2_present": h2_count >= int(thresholds["min_h2_count"]),
        "h3_present": h3_count >= int(thresholds["min_h3_count"]),
        "focus_keyphrase_in_title": focus_in_title,
        "focus_keyphrase_in_headings": focus_in_headings,
        "concise_headings": not overlong_headings,
    }
    axes["seo_structure"] = _axis(
        _boolean_score(seo_checks.values()),
        checks=seo_checks,
        h1_count=h1_count,
        h2_count=h2_count,
        h3_count=h3_count,
        focus_heading_token_coverage=(
            round(len(focus_tokens & subheading_tokens) / len(focus_tokens), 4)
            if focus_tokens
            else 0.0
        ),
    )
    if (
        h1_count != 1
        or h2_count < int(thresholds["min_h2_count"])
        or h3_count < int(thresholds["min_h3_count"])
    ):
        blockers.append(
            _blocker(
                "invalid_heading_structure",
                h1_count=h1_count,
                h2_count=h2_count,
                h3_count=h3_count,
            )
        )
    if not focus_in_title or not focus_in_headings:
        warnings.append(
            {
                "code": "focus_keyphrase_placement_advisory",
                "focus_in_title": focus_in_title,
                "focus_in_headings": focus_in_headings,
                "message": (
                    "A palavra-chave deve orientar o tema, mas não precisa ser "
                    "forçada em vários subtítulos."
                ),
            }
        )
    if internal_question_headings:
        blockers.append(
            _blocker(
                "internal_question_heading",
                heading_count=len(internal_question_headings),
            )
        )
    if overlong_headings:
        blockers.append(
            _blocker(
                "overlong_heading",
                heading_count=len(overlong_headings),
                maximum_length=max(
                    len(re.sub(r"^#{2,3}\s+", "", heading).strip())
                    for heading in overlong_headings
                ),
            )
        )

    plain_text = re.sub(r"(?m)^#{1,6}\s+", "", markdown)
    words = re.findall(r"\b[\wÀ-ÿ'-]+\b", plain_text)
    sentences = [
        item.strip() for item in re.split(r"(?<=[.!?])\s+", plain_text) if item.strip()
    ]
    foreign_fragments = _foreign_language_fragments(
        sentences,
        str(project.get("language") or ""),
    )
    if foreign_fragments:
        blockers.append(
            _blocker(
                "foreign_language_fragment",
                sentence_count=len(foreign_fragments),
            )
        )
    sentence_openings: dict[str, int] = defaultdict(int)
    for sentence in sentences:
        opening = " ".join(re.findall(r"\b[\wÀ-ÿ'-]+\b", _normalize(sentence))[:5])
        if opening:
            sentence_openings[opening] += 1
    repeated_openings = sorted(
        opening for opening, count in sentence_openings.items() if count >= 3
    )
    if repeated_openings:
        blockers.append(
            _blocker(
                "repetitive_template_language",
                repeated_opening_count=len(repeated_openings),
            )
        )
    naturalness = editorial_naturalness_metrics(markdown)
    if naturalness["generic_template_matches"]:
        blockers.append(
            _blocker(
                "generic_template_language",
                matches=naturalness["generic_template_matches"],
            )
        )
    if naturalness["meta_narration_matches"]:
        blockers.append(
            _blocker(
                "visible_meta_narration",
                matches=naturalness["meta_narration_matches"],
            )
        )
    severe_mechanical_prose = bool(
        naturalness["mechanical_prose"]
        and naturalness["words_per_subheading"] < 75
        and naturalness["dominant_paragraph_shape_rate"] >= 0.85
        and naturalness["dominant_sentence_bucket_rate"] >= 0.65
    )
    if naturalness["mechanical_prose"]:
        blockers.append(
            _blocker(
                "mechanical_prose_pattern",
                words_per_subheading=naturalness["words_per_subheading"],
                dominant_paragraph_shape_rate=naturalness[
                    "dominant_paragraph_shape_rate"
                ],
                dominant_sentence_bucket_rate=naturalness[
                    "dominant_sentence_bucket_rate"
                ],
            )
        )
    if severe_mechanical_prose:
        blockers.append(
            _blocker(
                "severe_mechanical_prose",
                words_per_subheading=naturalness["words_per_subheading"],
                dominant_paragraph_shape_rate=naturalness[
                    "dominant_paragraph_shape_rate"
                ],
                dominant_sentence_bucket_rate=naturalness[
                    "dominant_sentence_bucket_rate"
                ],
            )
        )
    temperature_pattern = re.compile(
        r"(?i)\b\d+(?:[.,]\d+)?\s*(?:a|[-–])\s*"
        r"\d+(?:[.,]\d+)?\s*°?\s*c\b"
    )
    temperature_ranges = {
        _normalize(match.group(0)) for match in temperature_pattern.finditer(plain_text)
    }
    range_sentence_indexes = [
        index
        for index, sentence in enumerate(sentences)
        if temperature_pattern.search(sentence)
    ]
    variation_context = " ".join(
        sentences[context_index]
        for index in range_sentence_indexes
        for context_index in range(
            max(0, index - 1),
            min(len(sentences), index + 2),
        )
    )
    variation_is_explained = bool(
        re.search(
            r"(?i)\b(?:varia\w*|diferen\w*|depende\w*|conforme|"
            r"contexto|faixas?|intervalos?)\b",
            variation_context,
        )
    )
    if len(temperature_ranges) > 1 and not variation_is_explained:
        blockers.append(
            _blocker(
                "unexplained_numeric_variation",
                range_count=len(temperature_ranges),
            )
        )
    section_word_counts = []
    for section in re.split(r"(?m)^##\s+", markdown)[1:]:
        section = section.split("\n", 1)[1] if "\n" in section else ""
        body = re.sub(r"(?m)^#{3,6}\s+.*$", "", section)
        section_word_counts.append(len(re.findall(r"\b[\wÀ-ÿ'-]+\b", body)))
    developed_section_count = sum(count >= 60 for count in section_word_counts)
    if int(thresholds["min_h2_count"]) >= 4 and developed_section_count < int(
        thresholds["min_h2_count"]
    ):
        blockers.append(
            _blocker(
                "shallow_section_development",
                developed_section_count=developed_section_count,
                required_developed_sections=int(thresholds["min_h2_count"]),
                section_word_counts=section_word_counts,
            )
        )
    sentence_lengths = [
        len(re.findall(r"\b[\wÀ-ÿ'-]+\b", _normalize(item))) for item in sentences
    ]
    average_sentence = _mean(sentence_lengths)
    long_sentence_rate = (
        sum(
            length > int(thresholds["max_sentence_words"])
            for length in sentence_lengths
        )
        / len(sentence_lengths)
        if sentence_lengths
        else 1.0
    )
    readability_checks = {
        "minimum_word_count": len(words) >= int(thresholds["min_word_count"]),
        "maximum_word_count": len(words) <= int(thresholds["max_word_count"]),
        "average_sentence_length": bool(sentence_lengths)
        and average_sentence <= int(thresholds["max_sentence_words"]),
        "long_sentence_rate": long_sentence_rate <= 0.25,
        "paragraphs_present": len(
            [value for value in markdown.split("\n\n") if value.strip()]
        )
        >= 3,
        "sections_developed": (
            int(thresholds["min_h2_count"]) < 4
            or developed_section_count >= int(thresholds["min_h2_count"])
        ),
        "no_visible_meta_narration": not naturalness["meta_narration_matches"],
        "non_mechanical_cadence": not naturalness["mechanical_prose"],
    }
    axes["readability"] = _axis(
        _boolean_score(readability_checks.values()),
        checks=readability_checks,
        word_count=len(words),
        average_sentence_words=round(average_sentence, 2),
        long_sentence_rate=round(long_sentence_rate, 4),
        naturalness=naturalness,
    )
    minimum_word_count = int(thresholds["min_word_count"])
    maximum_word_count = int(thresholds["max_word_count"])
    if len(words) < minimum_word_count:
        blockers.append(
            _blocker(
                "article_too_short",
                word_count=len(words),
                minimum_word_count=minimum_word_count,
            )
        )
    if len(words) > maximum_word_count:
        blockers.append(
            _blocker(
                "article_too_long",
                word_count=len(words),
                maximum_word_count=maximum_word_count,
            )
        )
    visible_attributions = sorted(
        {match.group(0) for match in _VISIBLE_ATTRIBUTION.finditer(markdown)}
    )
    if visible_attributions:
        blockers.append(
            _blocker(
                "visible_source_attribution",
                occurrence_count=len(visible_attributions),
            )
        )
    visible_source_labels = sorted(
        {
            label
            for fact in approved_facts
            for raw_label in (
                fact.get("source_domain"),
                fact.get("source_publisher"),
                fact.get("source_author"),
            )
            if (label := _normalize(str(raw_label or "")))
            and len(label) >= 5
            and label in _normalize(markdown)
        }
    )
    if visible_source_labels and not any(
        blocker.get("code") == "visible_source_attribution" for blocker in blockers
    ):
        blockers.append(
            _blocker(
                "visible_source_attribution",
                occurrence_count=len(visible_source_labels),
            )
        )

    topic_tokens = _tokens(str(project.get("topic") or ""))
    title_tokens = _tokens(title)
    body_tokens = _tokens(markdown)
    topic_coverage = (
        len(topic_tokens & (title_tokens | body_tokens)) / len(topic_tokens)
        if topic_tokens
        else 0.0
    )
    intent = str(project.get("search_intent") or "").strip().lower()
    intent_terms = _INTENT_TERMS.get(intent, ())
    normalized_body = _normalize(f"{title} {markdown}")
    intent_signal = (
        1.0
        if not intent_terms or any(term in normalized_body for term in intent_terms)
        else 0.5
    )
    axes["search_intent"] = _axis(
        (topic_coverage + intent_signal) / 2,
        configured_intent=intent,
        topic_token_coverage=round(topic_coverage, 4),
        explicit_intent_signal=intent_signal == 1.0,
    )

    false_promises = [phrase for phrase in _FALSE_PROMISES if phrase in normalized_body]
    commercial_checks = {
        "no_false_promises": not false_promises,
        "audience_addressed": bool(str(project.get("audience") or "").strip()),
        "actionable_content": bool(
            re.search(r"(?i)\b(como|passo|escolh|avali|compare|considere)\w*", markdown)
        ),
    }
    axes["commercial_value"] = _axis(
        _boolean_score(commercial_checks.values()),
        checks=commercial_checks,
        false_promise_phrases=false_promises,
    )
    if false_promises:
        blockers.append(_blocker("false_commercial_promise", phrases=false_promises))

    alphabetic_words = [word for word in words if any(char.isalpha() for char in word)]
    uppercase_rate = (
        sum(word.isupper() and len(word) > 2 for word in alphabetic_words)
        / len(alphabetic_words)
        if alphabetic_words
        else 1.0
    )
    expected_voice = [str(item) for item in snapshot.get("voice") or []]
    voice_profile = _normalize(" ".join(expected_voice))
    concise_voice_requested = any(
        term in voice_profile
        for term in ("clara", "concisa", "direta", "objetiva", "clear", "concise")
    )
    voice_checks = {
        "limited_uppercase": uppercase_rate <= 0.03,
        "limited_exclamations": markdown.count("!") <= max(2, len(words) // 200),
        "voice_profile_fixed": bool(expected_voice),
        "requested_clarity_or_concision": (
            not concise_voice_requested
            or (
                bool(sentence_lengths)
                and average_sentence <= int(thresholds["max_sentence_words"])
                and long_sentence_rate <= 0.25
            )
        ),
    }
    axes["voice_adherence"] = _axis(
        _boolean_score(voice_checks.values()),
        checks=voice_checks,
        expected_voice=expected_voice,
        uppercase_word_rate=round(uppercase_rate, 4),
    )

    briefing_checks = {
        "topic": bool(str(project.get("topic") or "").strip()),
        "audience": bool(str(project.get("audience") or "").strip()),
        "search_intent": bool(intent),
        "content_type": bool(str(project.get("content_type") or "").strip()),
        "title": bool(title.strip()),
        "outline": bool(outline or headings),
        "article": bool(markdown.strip()),
        "seo": bool(seo),
    }
    publication_profile = editorial_context.get("publication_profile")
    body_without_headings = re.sub(
        r"(?m)^#{1,6}\s+.*$",
        "",
        markdown,
    )
    body_tokens_for_brief = _brief_tokens(body_without_headings)
    topic_tokens_for_brief = _brief_tokens(str(project.get("topic") or ""))
    primary_keyword_tokens = _brief_tokens(
        str(content_brief.get("primary_keyword") or "")
    )
    objective_tokens = _brief_tokens(str(content_brief.get("content_objective") or ""))
    reader_goal_tokens = _brief_tokens(str(content_brief.get("reader_goal") or ""))

    def body_coverage(expected: set[str]) -> float:
        return (
            len(expected & body_tokens_for_brief) / len(expected) if expected else 1.0
        )

    topic_body_coverage = body_coverage(topic_tokens_for_brief)
    primary_keyword_body_coverage = body_coverage(primary_keyword_tokens)
    objective_body_coverage = body_coverage(objective_tokens)
    reader_goal_body_coverage = body_coverage(reader_goal_tokens)
    body_alignment_checks = {
        "topic_covered_in_body": topic_body_coverage >= 0.5,
        "primary_keyword_covered_in_body": (
            not primary_keyword_tokens or primary_keyword_body_coverage >= 0.5
        ),
        "content_objective_reflected": (
            not objective_tokens or objective_body_coverage >= 0.25
        ),
        "reader_goal_reflected": (
            not reader_goal_tokens or reader_goal_body_coverage >= 0.25
        ),
    }
    if publication_profile:
        briefing_checks.update(
            {
                "publication_profile": bool(
                    str(publication_profile.get("brand_name") or "").strip()
                ),
                "primary_keyword": bool(
                    str(content_brief.get("primary_keyword") or "").strip()
                ),
                "content_objective": bool(
                    str(content_brief.get("content_objective") or "").strip()
                ),
                "reader_context": bool(
                    str(content_brief.get("reader_context") or "").strip()
                ),
                "reader_goal": bool(
                    str(content_brief.get("reader_goal") or "").strip()
                ),
            }
        )
    briefing_checks.update(body_alignment_checks)
    axes["briefing_completeness"] = _axis(
        _boolean_score(briefing_checks.values()),
        checks=briefing_checks,
        topic_body_coverage=round(topic_body_coverage, 4),
        primary_keyword_body_coverage=round(primary_keyword_body_coverage, 4),
        content_objective_body_coverage=round(objective_body_coverage, 4),
        reader_goal_body_coverage=round(reader_goal_body_coverage, 4),
    )
    if (
        not body_alignment_checks["topic_covered_in_body"]
        or not (body_alignment_checks["primary_keyword_covered_in_body"])
    ):
        blockers.append(
            _blocker(
                "brief_misaligned",
                topic_body_coverage=round(topic_body_coverage, 4),
                primary_keyword_body_coverage=round(primary_keyword_body_coverage, 4),
            )
        )

    weights = RUBRIC_DEFINITION["axes"]
    overall = round(
        sum(axes[name]["score"] * weight for name, weight in weights.items()),
        4,
    )
    low_axes = sorted(
        name
        for name, value in axes.items()
        if value["score"] < float(thresholds["min_axis_score"])
    )
    if blockers:
        status = "blocked"
    elif overall < float(thresholds["min_overall_score"]) or low_axes:
        status = "needs_improvement"
        warnings.append(
            {
                "code": "quality_threshold_not_met",
                "low_axes": low_axes,
                "overall_score": overall,
            }
        )
    else:
        status = "passed"
    result = sanitize_nul(
        {
            "rubric_version": rubric["version"],
            "rubric_checksum": rubric["checksum"],
            "evaluator_kind": "deterministic",
            "status": status,
            "overall_score": overall,
            "thresholds": thresholds,
            "axes": axes,
            "critical_blockers": blockers,
            "warnings": warnings,
            "automatic_publication": False,
        },
        strip_escaped=True,
    )
    result["result_checksum"] = checksum(result)
    return result


class QualityEvaluator:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def evaluate(
        self,
        project: Project,
        run: PipelineRun,
        article: Article,
        version: ArticleVersion,
    ) -> QualityEvaluation:
        existing = await self.db.scalar(
            select(QualityEvaluation).where(QualityEvaluation.pipeline_run_id == run.id)
        )
        if existing is not None:
            stored_result = dict(existing.result_json or {})
            embedded_checksum = stored_result.pop("result_checksum", None)
            calculated_checksum = checksum(stored_result)
            if (
                existing.article_version_id != version.id
                or embedded_checksum != existing.result_checksum
                or calculated_checksum != existing.result_checksum
            ):
                raise QualityEvaluationUnavailable(
                    "Persisted quality evaluation checksum or version drift"
                )
            return existing
        manifest = await self.db.scalar(
            select(ExecutionManifest).where(ExecutionManifest.pipeline_run_id == run.id)
        )
        if manifest is None:
            raise QualityEvaluationUnavailable(
                "Execution manifest is required for quality evaluation"
            )
        rubric = (manifest.manifest_json or {}).get("quality_evaluator")
        _validate_rubric(rubric)
        snapshot = await self._snapshot(project, run, article, version, manifest)
        rubric = copy.deepcopy(rubric)
        thresholds = rubric.setdefault("thresholds", {})
        content_brief = (snapshot.get("editorial_context") or {}).get(
            "content_brief"
        ) or {}
        seo_brief = (snapshot.get("research_plan") or {}).get("seo_brief") or {}
        dynamic_values = {
            "min_word_count": content_brief.get("minimum_words")
            or seo_brief.get("minimum_words"),
            "max_word_count": content_brief.get("maximum_words")
            or seo_brief.get("maximum_words"),
            "min_h2_count": content_brief.get("minimum_h2")
            or seo_brief.get("minimum_h2"),
            "min_h3_count": (
                content_brief.get("minimum_h3")
                if content_brief.get("minimum_h3") is not None
                else seo_brief.get("minimum_h3")
            ),
        }
        for key, value in dynamic_values.items():
            if value is not None:
                thresholds[key] = int(value)
        result = evaluate_snapshot(snapshot, rubric)
        row = QualityEvaluation(
            project_id=project.id,
            pipeline_run_id=run.id,
            article_version_id=version.id,
            rubric_version=result["rubric_version"],
            rubric_checksum=result["rubric_checksum"],
            evaluator_kind=result["evaluator_kind"],
            status=result["status"],
            overall_score=result["overall_score"],
            thresholds_json=result["thresholds"],
            result_json=result,
            result_checksum=result["result_checksum"],
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def _snapshot(
        self,
        project: Project,
        run: PipelineRun,
        article: Article,
        version: ArticleVersion,
        manifest: ExecutionManifest,
    ) -> dict[str, Any]:
        plan_row = await self.db.scalar(
            select(ResearchPlan)
            .where(
                ResearchPlan.project_id == project.id,
                ResearchPlan.pipeline_run_id == run.id,
            )
            .order_by(ResearchPlan.version.desc(), ResearchPlan.id.desc())
            .limit(1)
        )
        questions = list(
            (
                await self.db.scalars(
                    select(ResearchQuestion)
                    .join(ResearchPlan, ResearchPlan.id == ResearchQuestion.plan_id)
                    .where(
                        ResearchPlan.project_id == project.id,
                        ResearchPlan.pipeline_run_id == run.id,
                    )
                    .order_by(ResearchQuestion.priority, ResearchQuestion.id)
                )
            ).all()
        )
        fact_rows = list(
            (
                await self.db.execute(
                    select(FactLedger, SourceSnapshot)
                    .outerjoin(
                        SourceSnapshot,
                        SourceSnapshot.id == FactLedger.source_snapshot_id,
                    )
                    .where(
                        FactLedger.project_id == project.id,
                        FactLedger.pipeline_run_id == run.id,
                    )
                    .order_by(FactLedger.created_at, FactLedger.id)
                )
            ).all()
        )
        claim_rows = list(
            (
                await self.db.scalars(
                    select(SentenceClaim)
                    .join(ArticleBlock, ArticleBlock.id == SentenceClaim.block_id)
                    .where(ArticleBlock.article_version_id == version.id)
                    .order_by(ArticleBlock.position, SentenceClaim.position)
                )
            ).all()
        )
        evidence_rows = list(
            (
                await self.db.execute(
                    select(ClaimEvidence, FactLedger)
                    .join(FactLedger, FactLedger.id == ClaimEvidence.fact_id)
                    .join(
                        SentenceClaim,
                        SentenceClaim.id == ClaimEvidence.sentence_claim_id,
                    )
                    .join(ArticleBlock, ArticleBlock.id == SentenceClaim.block_id)
                    .where(ArticleBlock.article_version_id == version.id)
                    .order_by(ClaimEvidence.sentence_claim_id, ClaimEvidence.fact_id)
                )
            ).all()
        )
        comparison_articles = list(
            (
                await self.db.scalars(
                    select(Article)
                    .where(
                        Article.id != article.id,
                        Article.final_markdown.is_not(None),
                    )
                    .order_by(Article.id)
                )
            ).all()
        )
        facts = []
        for fact, source_snapshot in fact_rows:
            facts.append(
                {
                    "id": str(fact.id),
                    "project_id": str(fact.project_id),
                    "pipeline_run_id": str(fact.pipeline_run_id),
                    "question_id": str(fact.research_question_id),
                    "claim": fact.claim_text,
                    "exact_quote": fact.exact_quote,
                    "approved": fact.approved,
                    "conflict_group": fact.conflict_group,
                    "superseded": fact.superseded_by_id is not None,
                    "same_run": fact.pipeline_run_id == run.id,
                    "snapshot_id": (
                        str(source_snapshot.id) if source_snapshot is not None else None
                    ),
                    "snapshot_text": (
                        source_snapshot.snapshot_text
                        if source_snapshot is not None
                        and source_snapshot.pipeline_run_id == run.id
                        else None
                    ),
                    "source_domain": (
                        source_snapshot.domain
                        if source_snapshot is not None
                        and source_snapshot.pipeline_run_id == run.id
                        else None
                    ),
                    "source_publisher": (
                        getattr(source_snapshot, "publisher", None)
                        if source_snapshot is not None
                        and source_snapshot.pipeline_run_id == run.id
                        else None
                    ),
                    "source_author": (
                        getattr(source_snapshot, "author", None)
                        if source_snapshot is not None
                        and source_snapshot.pipeline_run_id == run.id
                        else None
                    ),
                }
            )
        evidence_by_claim: dict[str, list[dict]] = defaultdict(list)
        for evidence, fact in evidence_rows:
            evidence_by_claim[str(evidence.sentence_claim_id)].append(
                {
                    "fact_id": str(fact.id),
                    "producer_entailment_score": evidence.entailment_score,
                }
            )
        manifest_data = manifest.manifest_json or {}
        editorial_context = manifest_data.get("editorial_context") or {
            "publication_profile": None,
            "content_brief": {},
        }
        voice = []
        for entry in (manifest_data.get("super_skills") or {}).get("writer", []):
            voice.extend((entry.get("definition") or {}).get("voice") or [])
        profile_voice = str(
            (editorial_context.get("publication_profile") or {}).get("tone_of_voice")
            or ""
        ).strip()
        if profile_voice:
            voice.append(profile_voice)
        return {
            "pipeline_run_id": str(run.id),
            "project": {
                "id": str(project.id),
                "topic": project.topic,
                "audience": project.audience,
                "language": getattr(project, "language", "pt-BR"),
                "search_intent": project.search_intent,
                "content_type": getattr(
                    project.content_type, "value", project.content_type
                ),
            },
            "editorial_context": editorial_context,
            "version": {
                "id": str(version.id),
                "title": version.title,
                "outline": version.outline or [],
                "markdown": version.final_markdown or "",
                "seo": version.seo_metadata or {},
            },
            "research_plan": {
                "seo_brief": getattr(plan_row, "seo_brief", {}) or {},
                "editorial_blueprint": (
                    getattr(plan_row, "editorial_blueprint", {}) or {}
                ),
            },
            "questions": [
                {
                    "id": str(question.id),
                    "question": question.question,
                    "priority": question.priority,
                    "importance": getattr(question, "importance", "core"),
                    "rationale": getattr(question, "rationale", ""),
                    "coverage_status": getattr(
                        question.coverage_status,
                        "value",
                        question.coverage_status,
                    ),
                }
                for question in questions
            ],
            "facts": facts,
            "claims": [
                {
                    "id": str(claim.id),
                    "text": claim.text,
                    "is_factual": claim.is_factual,
                    "evidence": evidence_by_claim[str(claim.id)],
                }
                for claim in claim_rows
            ],
            "comparison_documents": [
                item.final_markdown or "" for item in comparison_articles
            ],
            "voice": list(dict.fromkeys(str(item) for item in voice)),
        }


def quality_summary(
    evaluation: QualityEvaluation | None,
    *,
    human_decision: str | None = None,
) -> dict[str, Any] | None:
    if evaluation is None:
        return None
    result = dict(evaluation.result_json or {})
    decision = str(human_decision or "pending")
    comparison = None
    if decision != "pending":
        evaluator_accepts = evaluation.status == "passed"
        human_accepts = decision == "approved"
        comparison = {
            "human_decision": decision,
            "evaluator_recommendation": evaluation.status,
            "agreement": evaluator_accepts == human_accepts,
        }
    return {
        "id": str(evaluation.id),
        "project_id": str(evaluation.project_id),
        "pipeline_run_id": str(evaluation.pipeline_run_id),
        "article_version_id": str(evaluation.article_version_id),
        "rubric_version": evaluation.rubric_version,
        "rubric_checksum": evaluation.rubric_checksum,
        "evaluator_kind": evaluation.evaluator_kind,
        "status": evaluation.status,
        "overall_score": evaluation.overall_score,
        "result_checksum": evaluation.result_checksum,
        "axes": result.get("axes") or {},
        "critical_blockers": result.get("critical_blockers") or [],
        "warnings": result.get("warnings") or [],
        "thresholds": evaluation.thresholds_json or {},
        "automatic_publication": False,
        "human_comparison": comparison,
        "created_at": (
            evaluation.created_at.isoformat()
            if getattr(evaluation.created_at, "isoformat", None)
            else evaluation.created_at
        ),
    }


def _validate_rubric(rubric: dict[str, Any] | None) -> None:
    if not isinstance(rubric, dict):
        raise QualityEvaluationUnavailable("Quality rubric is not fixed in manifest")
    if rubric.get("version") != RUBRIC_VERSION:
        raise QualityEvaluationUnavailable("Quality rubric version is unavailable")
    if rubric.get("checksum") != checksum(RUBRIC_DEFINITION):
        raise QualityEvaluationUnavailable("Quality rubric checksum drift")
    thresholds = rubric.get("thresholds")
    required = set(configured_thresholds())
    if not isinstance(thresholds, dict) or required - thresholds.keys():
        raise QualityEvaluationUnavailable("Quality thresholds are incomplete")


def _axis(score: float, **metrics: Any) -> dict[str, Any]:
    return {"score": round(max(0.0, min(1.0, score)), 4), "metrics": metrics}


def _blocker(code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "critical": True, "details": details}


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _tokens(value: str) -> set[str]:
    words = {
        word
        for word in re.findall(r"\b[\wÀ-ÿ'-]+\b", _normalize(value))
        if len(word) > 1
    }
    return words - _STOPWORDS


def _brief_tokens(value: str) -> set[str]:
    return _tokens(value) - _BRIEF_META_WORDS


def editorial_naturalness_metrics(markdown: str) -> dict[str, Any]:
    """Return conservative, explainable signals of visibly mechanical prose.

    This is deliberately not an "AI detector". It only catches editorial
    defects that a human reviewer can verify in the text itself: narration
    about the act of writing, heading overload and overly uniform cadence.
    """
    chunks = [
        chunk.strip()
        for chunk in re.split(r"\n\s*\n", str(markdown or ""))
        if chunk.strip()
    ]
    body_paragraphs: list[str] = []
    body_lines: list[str] = []
    subheading_count = 0
    for chunk in chunks:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        subheading_count += sum(bool(re.match(r"^#{2,6}\s+", line)) for line in lines)
        visible_lines = [
            re.sub(r"^[-*+]\s+", "", line)
            for line in lines
            if not re.match(r"^#{1,6}\s+", line)
        ]
        if not visible_lines:
            continue
        body_lines.extend(visible_lines)
        if not all(re.match(r"^[-*+]\s+", line) for line in lines):
            body_paragraphs.append(" ".join(visible_lines))

    body_text = " ".join(body_lines)
    body_words = re.findall(r"\b[\wÀ-ÿ'-]+\b", body_text)
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+", body_text)
        if sentence.strip()
    ]
    sentence_lengths = [
        len(re.findall(r"\b[\wÀ-ÿ'-]+\b", sentence)) for sentence in sentences
    ]
    average_sentence_words = _mean(sentence_lengths)
    if sentence_lengths and average_sentence_words:
        variance = sum(
            (length - average_sentence_words) ** 2 for length in sentence_lengths
        ) / len(sentence_lengths)
        sentence_length_cv = (variance**0.5) / average_sentence_words
    else:
        sentence_length_cv = 0.0
    sentence_buckets = Counter(max(0, (length - 1) // 5) for length in sentence_lengths)
    dominant_sentence_bucket_rate = (
        max(sentence_buckets.values()) / len(sentence_lengths)
        if sentence_lengths
        else 0.0
    )

    paragraph_sentence_counts = [
        len(
            [
                sentence
                for sentence in re.split(r"(?<=[.!?])\s+", paragraph)
                if sentence.strip()
            ]
        )
        for paragraph in body_paragraphs
    ]
    paragraph_shapes = Counter(paragraph_sentence_counts)
    dominant_paragraph_shape_rate = (
        max(paragraph_shapes.values()) / len(paragraph_sentence_counts)
        if paragraph_sentence_counts
        else 0.0
    )
    words_per_subheading = (
        len(body_words) / subheading_count
        if subheading_count
        else float(len(body_words))
    )
    dense_heading_structure = subheading_count >= 6 and words_per_subheading < 110
    uniform_paragraph_shape = (
        len(paragraph_sentence_counts) >= 5 and dominant_paragraph_shape_rate >= 0.75
    )
    uniform_sentence_cadence = (
        len(sentence_lengths) >= 10
        and dominant_sentence_bucket_rate >= 0.55
        and sentence_length_cv <= 0.35
    )
    mechanical_prose = (dense_heading_structure and uniform_paragraph_shape) or (
        uniform_paragraph_shape and uniform_sentence_cadence
    )
    meta_matches = list(
        dict.fromkeys(
            match.group(0).strip()
            for match in _VISIBLE_META_NARRATION.finditer(markdown)
        )
    )
    generic_matches = list(
        dict.fromkeys(
            match.group(0).strip()
            for match in _GENERIC_TEMPLATE_LANGUAGE.finditer(markdown)
        )
    )
    return {
        "body_word_count": len(body_words),
        "sentence_count": len(sentence_lengths),
        "paragraph_count": len(paragraph_sentence_counts),
        "subheading_count": subheading_count,
        "average_sentence_words": round(float(average_sentence_words), 2),
        "sentence_length_cv": round(sentence_length_cv, 4),
        "dominant_sentence_bucket_rate": round(dominant_sentence_bucket_rate, 4),
        "dominant_paragraph_shape_rate": round(dominant_paragraph_shape_rate, 4),
        "words_per_subheading": round(words_per_subheading, 2),
        "dense_heading_structure": dense_heading_structure,
        "uniform_paragraph_shape": uniform_paragraph_shape,
        "uniform_sentence_cadence": uniform_sentence_cadence,
        "mechanical_prose": mechanical_prose,
        "meta_narration_matches": meta_matches,
        "generic_template_matches": generic_matches,
    }


def _foreign_language_fragments(
    sentences: list[str],
    language: str,
) -> list[str]:
    if not language.casefold().startswith("pt"):
        return []
    fragments = []
    for sentence in sentences:
        tokens = set(re.findall(r"\b[\wÀ-ÿ'-]+\b", _normalize(sentence)))
        if len(tokens & _ENGLISH_MARKERS) >= 2 or len(tokens & _SPANISH_MARKERS) >= 2:
            fragments.append(sentence)
    return fragments


def _citation_is_present(fact: dict[str, Any]) -> bool:
    quote = _normalize(str(fact.get("exact_quote") or ""))
    snapshot = _normalize(str(fact.get("snapshot_text") or ""))
    return bool(fact.get("same_run") and quote and snapshot and quote in snapshot)


def _claim_overlap(claim: str, fact: dict[str, Any]) -> float:
    claim_tokens = _tokens(claim)
    evidence_tokens = _tokens(
        f"{fact.get('claim') or ''} {fact.get('exact_quote') or ''}"
    )
    if not claim_tokens:
        return 0.0
    return len(claim_tokens & evidence_tokens) / len(claim_tokens)


def _numbers_are_supported(claim: str, fact: dict[str, Any]) -> bool:
    claim_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", claim))
    evidence_numbers = set(
        re.findall(
            r"\b\d+(?:[.,]\d+)?%?\b",
            f"{fact.get('claim') or ''} {fact.get('exact_quote') or ''}",
        )
    )
    return claim_numbers <= evidence_numbers


def _claim_scope_is_preserved(claim: str, fact: dict[str, Any]) -> bool:
    """Reject common certainty and causality upgrades missed by token overlap."""
    normalized_claim = _normalize(claim)
    normalized_evidence = _normalize(
        f"{fact.get('claim') or ''} {fact.get('exact_quote') or ''}"
    )
    qualifier_groups = (
        ("pode", "podem", "could", "may", "might"),
        ("tende", "tendem", "tend", "tends"),
        ("geralmente", "normalmente", "usually", "generally"),
        ("sugere", "sugerem", "suggest", "suggests"),
        ("associado", "associada", "associated"),
    )
    for group in qualifier_groups:
        if any(term in normalized_evidence for term in group) and not any(
            term in normalized_claim for term in group
        ):
            return False

    strong_claim_patterns = (
        r"\bgarant\w*\b",
        r"\bsempre\b",
        r"\bsem\s+risco\b",
        r"\bcomprov\w*\b",
        r"\b(?:causa|causam|provoca|provocam)\b",
    )
    for pattern in strong_claim_patterns:
        if re.search(pattern, normalized_claim) and not re.search(
            pattern, normalized_evidence
        ):
            return False
    return True


def _negation_is_preserved(claim: str, fact: dict[str, Any]) -> bool:
    normalized_claim = _normalize(claim)
    normalized_evidence = _normalize(
        f"{fact.get('claim') or ''} {fact.get('exact_quote') or ''}"
    )
    negations = ("não", "nunca", "sem", "not", "never", "without")
    evidence_has_negation = any(
        re.search(rf"\b{re.escape(term)}\b", normalized_evidence) for term in negations
    )
    claim_has_negation = any(
        re.search(rf"\b{re.escape(term)}\b", normalized_claim) for term in negations
    )
    return not evidence_has_negation or claim_has_negation


def _causality_is_preserved(claim: str, fact: dict[str, Any]) -> bool:
    normalized_claim = _normalize(claim)
    normalized_evidence = _normalize(
        f"{fact.get('claim') or ''} {fact.get('exact_quote') or ''}"
    )
    causal_patterns = (
        r"\bcaus\w*\b",
        r"\bprovoc\w*\b",
        r"\bleva\s+a\b",
        r"\bfaz\s+com\s+que\b",
        r"\bresulta\s+em\b",
        r"\bimpede\w*\b",
        r"\bevita\w*\b",
        r"\bcaus(?:e|es|ed|ing)?\b",
        r"\bleads?\s+to\b",
        r"\bprevents?\b",
    )
    claim_is_causal = any(
        re.search(pattern, normalized_claim) for pattern in causal_patterns
    )
    evidence_is_causal = any(
        re.search(pattern, normalized_evidence) for pattern in causal_patterns
    )
    return not claim_is_causal or evidence_is_causal


def _semantic_anchor_score(claim: str, fact: dict[str, Any]) -> float:
    """Require a concrete shared subject without demanding copied phrasing."""
    claim_tokens = _tokens(claim)
    evidence_text = f"{fact.get('claim') or ''} {fact.get('exact_quote') or ''}"
    evidence_tokens = _tokens(evidence_text)
    shared = claim_tokens & evidence_tokens
    if shared:
        return len(shared) / max(1, min(len(claim_tokens), len(evidence_tokens)))
    claim_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", claim))
    evidence_numbers = set(re.findall(r"\b\d+(?:[.,]\d+)?%?\b", evidence_text))
    return 1.0 if claim_numbers and claim_numbers <= evidence_numbers else 0.0


def _shingle_similarity(left: str, right: str, size: int = 5) -> float:
    def shingles(value: str) -> set[tuple[str, ...]]:
        words = list(re.findall(r"\b[\wÀ-ÿ'-]+\b", _normalize(value)))
        if not words:
            return set()
        if len(words) < size:
            return {tuple(words)}
        return {
            tuple(words[index : index + size]) for index in range(len(words) - size + 1)
        }

    left_shingles = shingles(left)
    right_shingles = shingles(right)
    if not left_shingles or not right_shingles:
        return 0.0
    return len(left_shingles & right_shingles) / len(left_shingles | right_shingles)


def _boolean_score(values) -> float:
    items = list(values)
    return sum(bool(item) for item in items) / len(items) if items else 0.0


def _mean(values) -> float:
    items = list(values)
    return round(sum(items) / len(items), 4) if items else 0.0
