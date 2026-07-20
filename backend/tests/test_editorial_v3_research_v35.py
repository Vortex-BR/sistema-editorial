from datetime import datetime, timezone

import pytest

from app.schemas.editorial_v3 import EvidenceRole
from app.services.editorial_v3.research_intent import (
    CanonicalResearchIntent,
    QueryLocalizationService,
)
from app.services.editorial_v3.search_acceptance import CandidateAcceptanceService
from app.services.editorial_v3.search_runtime import (
    ProviderCircuitBreaker,
    SearchBudgetExhausted,
    SearchBudgetLedger,
)
from app.services.research_engine import SearchDocument
from app.services.search_policy import SPAIN, SWITZERLAND, UNITED_STATES


def _intent() -> CanonicalResearchIntent:
    return CanonicalResearchIntent(
        version="research-intent.v1",
        canonical_subject=(
            "germinação de sementes de cannabis pelo método de papel-toalha "
            "em recipiente fechado"
        ),
        project_locale="pt-BR",
        project_language="pt",
        target_country="br",
        jurisdiction="Brasil",
        content_type="procedural_decision_guide",
        entity_terms=("germinação", "sementes", "cannabis", "papel-toalha"),
        method_labels=("papel-toalha em recipiente fechado",),
    )


@pytest.mark.parametrize(
    ("market", "expected_language", "expected_term"),
    [
        (UNITED_STATES, "en", "germination"),
        (SPAIN, "es", "germinación"),
        (SWITZERLAND, "de", "Keimung"),
    ],
)
def test_query_localization_never_sends_full_portuguese_query_to_foreign_market(
    market, expected_language, expected_term
):
    original = (
        "germinação de sementes de cannabis em papel-toalha "
        "condições ambientais e erros comuns"
    )
    localized = QueryLocalizationService().localize(
        query=original,
        intent=_intent(),
        market=market,
        evidence_role=EvidenceRole.mechanism,
    )

    assert localized.target_language == expected_language
    assert localized.localized_query != original
    assert expected_term.casefold() in localized.localized_query.casefold()
    assert localized.strategy in {"lexicon", "canonical_subject_with_localized_qualifiers"}


def _document(url: str, *, source_type: str) -> SearchDocument:
    content = (
        "Germinação de sementes de cannabis em papel-toalha, umidade, "
        "temperatura e recipiente fechado. Evidência técnica sobre o mecanismo. "
    ) * 4
    return SearchDocument(
        url=url,
        title="Germinação de sementes de cannabis em papel-toalha",
        content=content,
        publisher="Fonte independente",
        source_type=source_type,
        reliability_score=0.8,
        accessed_at=datetime.now(timezone.utc),
    )


def test_candidate_gate_rejects_two_forum_pages_even_when_both_are_relevant():
    report = CandidateAcceptanceService().evaluate(
        [
            _document("https://forum-a.example/topic", source_type="forum"),
            _document("https://forum-b.example/topic", source_type="forum"),
        ],
        subject=_intent().canonical_subject,
        query="germinação sementes cannabis mecanismo umidade temperatura",
        required_source_roles=["scientific_review", "institutional"],
        minimum_independent_sources=2,
    )

    assert report.sufficient is False
    assert report.relevant_document_count == 2
    assert "authoritative_source_role_missing" in report.reasons
    assert "only_low_trust_sources" in report.reasons


def test_candidate_gate_accepts_relevant_diverse_authoritative_sources():
    report = CandidateAcceptanceService().evaluate(
        [
            _document("https://university.example/research", source_type="university"),
            _document("https://government.example/guide", source_type="government"),
        ],
        subject=_intent().canonical_subject,
        query="germinação sementes cannabis mecanismo umidade temperatura",
        required_source_roles=["institutional"],
        minimum_independent_sources=2,
    )

    assert report.sufficient is True
    assert report.independent_domain_count == 2
    assert report.high_trust_document_count == 2


def test_search_budget_counts_real_provider_requests_and_retries():
    budget = SearchBudgetLedger(
        maximum_logical_queries=5,
        maximum_provider_requests=2,
        maximum_provider_retries=1,
        maximum_estimated_credits=10,
        timeout_seconds=30,
    )
    budget.begin_logical_query()
    budget.record_provider_call(requests=1, retries=0, estimated_credits=1)
    assert budget.exhaustion_reason(include_logical=False) is None

    budget.record_provider_call(requests=1, retries=1, estimated_credits=1)
    assert budget.exhaustion_reason(include_logical=False) == "provider_request_limit"
    with pytest.raises(SearchBudgetExhausted, match="provider_request_limit"):
        budget.require_capacity()


def test_provider_circuit_breaker_distinguishes_permanent_rate_and_transient_failures():
    circuits = ProviderCircuitBreaker()

    circuits.record_failure("tavily", "authentication")
    assert circuits.allows("tavily") is False
    assert circuits.state("tavily").status == "open_permanent"

    circuits.record_failure("serper", "rate_limited", retry_after=120)
    assert circuits.allows("serper") is False
    assert circuits.state("serper").status == "open_temporary"

    circuits.record_failure("fallback", "timeout")
    assert circuits.allows("fallback") is True
    circuits.record_failure("fallback", "timeout")
    assert circuits.allows("fallback") is False

    circuits.record_success("fallback")
    assert circuits.allows("fallback") is True


@pytest.mark.asyncio
async def test_resilient_search_scores_foreign_sources_against_localized_query_terms():
    from app.schemas.editorial_v3_runtime import ResearchTask
    from app.services.editorial_v3.resilient_search import ResilientSearchCoordinator
    from app.services.research_engine import SearchDiagnostics, SearchResponse

    class EnglishEngine:
        def __init__(self):
            self.calls = []

        async def search_detailed(
            self,
            query,
            provider,
            api_key,
            *,
            max_results,
            market,
            exclude_brazil,
            request_attempt_limit,
        ):
            self.calls.append((query, market.code, provider))
            now = datetime.now(timezone.utc)
            documents = [
                SearchDocument(
                    url="https://university.example/cannabis-seed-germination",
                    title="Cannabis seed germination in paper towel systems",
                    content=(
                        "Cannabis seed germination depends on moisture, temperature, "
                        "oxygen and controlled paper towel conditions. "
                    ) * 5,
                    publisher="University",
                    source_type="university",
                    reliability_score=0.9,
                    accessed_at=now,
                    search_market=market.code,
                    search_language=market.language_code,
                ),
                SearchDocument(
                    url="https://science.example/seed-germination-review",
                    title="Scientific review of cannabis seed germination",
                    content=(
                        "A scientific review describes germination mechanisms, moisture, "
                        "temperature and observable seed development. "
                    ) * 5,
                    publisher="Science",
                    source_type="scientific",
                    reliability_score=0.95,
                    accessed_at=now,
                    search_market=market.code,
                    search_language=market.language_code,
                ),
            ]
            return SearchResponse(
                documents=documents[:max_results],
                diagnostics=SearchDiagnostics(
                    provider=provider,
                    query=query,
                    market=market.code,
                    raw_results=2,
                    retained_documents=2,
                    provider_requests=1,
                    estimated_credits=1,
                ),
            )

    task = ResearchTask(
        task_id="task_mechanism",
        knowledge_node_id="node_mechanism",
        evidence_role=EvidenceRole.mechanism,
        research_goal=(
            "Explicar o mecanismo de germinação das sementes e as condições "
            "ambientais que sustentam o processo."
        ),
        queries=["germinação de sementes de cannabis mecanismo umidade temperatura"],
        required_source_roles=["scientific_review", "institutional"],
        minimum_independent_sources=2,
        critical=True,
        rationale="O mecanismo exige fontes independentes e autoritativas.",
    )
    engine = EnglishEngine()
    result = await ResilientSearchCoordinator(engine).search(
        query=task.queries[0],
        topic="germinação de sementes de cannabis",
        question=task.research_goal,
        search_subject=_intent().canonical_subject,
        provider_credentials=[("tavily", "key")],
        max_results=5,
        intent=_intent(),
        task=task,
    )

    assert result.acceptance is not None
    assert result.acceptance.sufficient is True
    assert len(result.documents) == 2
    assert len(engine.calls) == 2
    assert engine.calls[0][1] == "br"
    assert engine.calls[1][1] == "us"
    assert "germination" in engine.calls[1][0].casefold()
