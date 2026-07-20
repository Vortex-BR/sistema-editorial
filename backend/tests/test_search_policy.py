from dataclasses import dataclass

from app.schemas.editorial_v3 import EvidenceRole
from app.services.search_policy import (
    MAX_DOCUMENTS_PER_QUESTION,
    MAX_EXTRACTION_CHARS_PER_DOCUMENT,
    explicitly_requires_brazil,
    market_search_plan,
    merge_market_results,
    search_policy_manifest,
    source_country_from_url,
)


LOCALIZED = {
    "united_states": "hemp seed paper towel germination guide",
    "spain": "guía germinación semilla cáñamo papel absorbente",
    "switzerland": "Hanfsamen Keimung Küchenpapier Anleitung",
    "brazil": "guia brasileiro germinação de cânhamo no papel toalha",
}


def test_v35_searches_local_market_first_and_never_excludes_it_by_default():
    searches = market_search_plan(
        topic="Guia de germinação de cânhamo",
        question="Como usar o método do papel toalha?",
        fallback_query="consulta",
        localized_queries=LOCALIZED,
        project_locale="pt-BR",
        evidence_role=EvidenceRole.mechanism,
    )

    assert [search.market.code for search in searches] == ["br", "us", "es"]
    assert [search.query for search in searches] == [
        LOCALIZED["brazil"],
        LOCALIZED["united_states"],
        LOCALIZED["spain"],
    ]
    assert all(not search.exclude_brazil for search in searches)
    assert all("-site:.br" not in search.query for search in searches)


def test_jurisdiction_is_prioritized_without_discarding_project_locale():
    searches = market_search_plan(
        topic="Requisitos de rotulagem",
        question="Quais regras se aplicam?",
        fallback_query="consulta",
        localized_queries=LOCALIZED,
        project_locale="pt-BR",
        jurisdiction="Espanha",
        evidence_role=EvidenceRole.risk,
    )

    assert [search.market.code for search in searches] == ["es", "br", "us"]


def test_domain_exclusion_is_an_explicit_policy_not_a_global_default():
    searches = market_search_plan(
        topic="Pesquisa internacional",
        question="Comparar fontes",
        fallback_query="consulta",
        project_locale="pt-BR",
        evidence_role=EvidenceRole.comparison,
        exclude_local_domains=True,
    )

    assert searches[0].market.code == "br"
    assert not searches[0].exclude_brazil
    assert all(search.exclude_brazil for search in searches[1:])
    assert all("-site:.br" in search.query for search in searches[1:])


def test_explicit_brazil_helper_remains_available_for_legacy_callers():
    assert not explicitly_requires_brazil(
        topic="Guia de germinação",
        question="Qual temperatura é adequada?",
    )
    assert explicitly_requires_brazil(
        topic="Guia de germinação",
        question="Quais regras se aplicam no Brasil?",
    )
    assert explicitly_requires_brazil(
        topic="Brazilian hemp regulation",
        question="Which rules apply?",
    )


def test_source_country_is_inferred_only_from_safe_domain_evidence():
    assert source_country_from_url("https://example.com.br/guia") == "br"
    assert source_country_from_url("https://example.es/guia") == "es"
    assert source_country_from_url("https://example.ch/guide") == "ch"
    assert source_country_from_url("https://example.edu/guide") == "us"
    assert source_country_from_url("https://example.com/guide") is None


@dataclass
class Result:
    url: str
    reliability_score: float = 0.0


def test_market_results_are_merged_round_robin_and_deduplicated():
    merged = merge_market_results(
        [
            [Result("https://us.example/1"), Result("https://shared.example")],
            [Result("https://es.example/1"), Result("https://shared.example")],
            [Result("https://ch.example/1"), Result("https://ch.example/2")],
        ],
        limit=5,
    )

    assert [item.url for item in merged] == [
        "https://us.example/1",
        "https://es.example/1",
        "https://ch.example/1",
        "https://shared.example",
        "https://ch.example/2",
    ]


def test_each_market_prioritizes_more_reliable_sources_before_merging():
    merged = merge_market_results(
        [
            [
                Result("https://forum.example", 0.45),
                Result("https://university.example", 0.90),
            ],
            [Result("https://government.example", 0.95)],
        ],
        limit=3,
    )

    assert [item.url for item in merged] == [
        "https://university.example",
        "https://government.example",
        "https://forum.example",
    ]


def test_manifest_pins_intent_aware_policy_and_prompt_limits():
    manifest = search_policy_manifest()

    assert manifest["policy_version"] == "intent-aware-search.v3.5"
    assert manifest["local_market_is_searched_first"] is True
    assert manifest["queries_are_localized_per_market"] is True
    assert manifest["exclude_brazilian_domains_by_default"] is False
    assert manifest["maximum_documents_per_question"] == MAX_DOCUMENTS_PER_QUESTION
    assert manifest["maximum_extraction_characters_per_document"] == (
        MAX_EXTRACTION_CHARS_PER_DOCUMENT
    )


def test_market_plan_infers_explicit_jurisdiction_from_topic_when_field_is_empty():
    searches = market_search_plan(
        topic="legislação de sementes na Espanha",
        question="quais normas se aplicam?",
        fallback_query="legislação sementes",
        project_locale="pt-BR",
        evidence_role="risk",
    )

    assert searches[0].market.code == "es"
    assert {item.market.code for item in searches} >= {"es", "br"}
