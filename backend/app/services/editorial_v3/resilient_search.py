"""Intent-aware and budgeted source discovery for Editorial V3.5."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.editorial_v3 import EditorialContentTypeV3, EvidenceRole
from app.schemas.editorial_v3_runtime import ResearchTask
from app.services.editorial_v3.research_intent import (
    CanonicalResearchIntent,
    QueryLocalizationService,
)
from app.services.editorial_v3.search_acceptance import (
    CandidateAcceptanceReport,
    CandidateAcceptanceService,
)
from app.services.editorial_v3.search_runtime import (
    ProviderCircuitBreaker,
    SearchBudgetExhausted,
    SearchBudgetLedger,
)
from app.services.research_engine import (
    ResearchEngine,
    SearchDocument,
    SearchProviderError,
    canonicalize_url,
)
from app.services.search_policy import MarketSearch, market_search_plan

_REAL_SEARCH_PROVIDERS = {"tavily", "serper"}
_QUERY_NOISE = {
    "abordagens", "ação", "causas", "comparação", "condições", "correção",
    "critérios", "definição", "desvantagens", "detalhado", "evidência",
    "explicação", "falha", "funcionamento", "guia", "limitações", "manual",
    "materiais", "mecanismo", "ordem", "passo", "problemas", "procedimento",
    "progresso", "revisão", "sinais", "técnico", "universidade",
}


@dataclass(frozen=True)
class SearchAttempt:
    provider: str
    market: str | None
    query: str
    status: str
    source_count: int
    original_query: str | None = None
    search_language: str | None = None
    localization_strategy: str | None = None
    translation_confidence: float | None = None
    error_category: str | None = None
    skipped_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    acceptance: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "market": self.market,
            "query": self.query,
            "original_query": self.original_query,
            "search_language": self.search_language,
            "localization_strategy": self.localization_strategy,
            "translation_confidence": self.translation_confidence,
            "status": self.status,
            "source_count": self.source_count,
            "error_category": self.error_category,
            "skipped_reason": self.skipped_reason,
            "diagnostics": self.diagnostics,
            "acceptance": self.acceptance,
        }


@dataclass
class ResilientSearchResult:
    documents: list[SearchDocument]
    attempts: list[SearchAttempt]
    failures: list[SearchProviderError]
    successful_attempts: int
    acceptance: CandidateAcceptanceReport | None = None
    budget_exhausted_by: str | None = None


def _normalize_query(value: str, *, limit: int = 360) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value[:limit].rstrip()


def query_variants(query: str, subject: str) -> tuple[str, ...]:
    """Return planned, simplified and subject-led recovery variants."""

    original = _normalize_query(query)
    subject = _normalize_query(subject, limit=300)
    subject_tokens = set(re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", subject.casefold()))
    useful: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-zA-ZÀ-ÿ0-9-]{3,}", original):
        lowered = token.casefold()
        if lowered in _QUERY_NOISE and lowered not in subject_tokens:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        useful.append(token)
    simplified = _normalize_query(" ".join(useful[:18]))
    subject_led = _normalize_query(f"{subject} {simplified}")
    return tuple(
        dict.fromkeys(item for item in (original, simplified, subject_led, subject) if item)
    )


def _legacy_intent(search_subject: str, project_locale: str = "pt-BR") -> CanonicalResearchIntent:
    language = project_locale.split("-", 1)[0].casefold()
    country = project_locale.split("-", 1)[1].casefold() if "-" in project_locale else "br"
    return CanonicalResearchIntent(
        version="research-intent.v1",
        canonical_subject=_normalize_query(search_subject, limit=500),
        project_locale=project_locale,
        project_language=language,
        target_country=country,
        jurisdiction=None,
        content_type=EditorialContentTypeV3.explanatory_guide.value,
        entity_terms=tuple(),
        method_labels=tuple(),
    )


def _authoritative_source_required(
    *,
    evidence_role: EvidenceRole,
    required_source_roles: list[str],
    research_goal: str,
) -> bool:
    explicit_scientific_roles = {
        "scientific_primary",
        "scientific_review",
        "academic_repository",
        "scientific_database",
    }
    if set(required_source_roles) & explicit_scientific_roles:
        return True
    if evidence_role in {
        EvidenceRole.definition,
        EvidenceRole.mechanism,
        EvidenceRole.risk,
        EvidenceRole.limitation,
    }:
        return True
    return bool(
        re.search(
            r"\b(?:legal|legisla|regula|jurisdi|governo|norma)\w*",
            research_goal,
            re.IGNORECASE,
        )
    )


class ResilientSearchCoordinator:
    def __init__(
        self,
        engine: ResearchEngine,
        *,
        budget: SearchBudgetLedger | None = None,
        circuits: ProviderCircuitBreaker | None = None,
        localizer: QueryLocalizationService | None = None,
        acceptance: CandidateAcceptanceService | None = None,
    ):
        self.engine = engine
        self.budget = budget or SearchBudgetLedger()
        self.circuits = circuits or ProviderCircuitBreaker()
        self.localizer = localizer or QueryLocalizationService()
        self.acceptance_service = acceptance or CandidateAcceptanceService()

    async def search(
        self,
        *,
        query: str,
        topic: str,
        question: str,
        search_subject: str,
        provider_credentials: list[tuple[str, str]],
        max_results: int,
        preferred_market_index: int = 0,
        max_attempts: int = 8,
        minimum_results: int = 2,
        intent: CanonicalResearchIntent | None = None,
        task: ResearchTask | None = None,
        project_locale: str = "pt-BR",
    ) -> ResilientSearchResult:
        if not provider_credentials:
            raise ValueError("provider_credentials cannot be empty")
        try:
            self.budget.begin_logical_query()
        except SearchBudgetExhausted as exc:
            return ResilientSearchResult([], [], [], 0, budget_exhausted_by=exc.reason)

        intent = intent or _legacy_intent(search_subject, project_locale)
        evidence_role = task.evidence_role if task else EvidenceRole.definition
        required_source_roles = task.required_source_roles if task else ["independent_editorial"]
        minimum_independent_sources = (
            task.minimum_independent_sources if task else max(1, minimum_results)
        )
        authoritative_required = bool(
            task
            and _authoritative_source_required(
                evidence_role=evidence_role,
                required_source_roles=required_source_roles,
                research_goal=task.research_goal,
            )
        )
        relevance_queries = [query]
        variants = query_variants(query, intent.canonical_subject or search_subject)
        real_providers = [
            item for item in provider_credentials if item[0] in _REAL_SEARCH_PROVIDERS
        ]
        if not real_providers:
            provider, api_key = provider_credentials[0]
            return await self._single_attempt(
                provider=provider,
                api_key=api_key,
                query=query,
                max_results=max_results,
                subject=intent.canonical_subject,
                required_source_roles=required_source_roles,
                minimum_independent_sources=minimum_independent_sources,
                authoritative_required=authoritative_required,
            )

        markets = list(
            market_search_plan(
                topic=topic,
                question=question,
                fallback_query=variants[0],
                project_locale=intent.project_locale,
                jurisdiction=intent.jurisdiction,
                evidence_role=evidence_role,
                required_source_roles=required_source_roles,
                maximum_markets=3,
            )
        )
        if markets:
            start = preferred_market_index % len(markets)
            markets = [*markets[start:], *markets[:start]]

        # Breadth first: primary provider/local market, fallback provider/local,
        # then additional markets and simplified variants. This yields diversity
        # early while keeping the number of real provider requests bounded.
        candidates: list[tuple[str, str, MarketSearch, str]] = []
        for variant_index, variant in enumerate(variants[:3]):
            for market_index, market_search in enumerate(markets):
                for provider in real_providers:
                    if variant_index == 0 or market_index == 0 or provider != real_providers[0]:
                        candidates.append((*provider, market_search, variant))

        unique: list[tuple[str, str, MarketSearch, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for provider, api_key, market_search, variant in candidates:
            key = (provider, market_search.market.code, variant.casefold())
            if key in seen:
                continue
            seen.add(key)
            unique.append((provider, api_key, market_search, variant))
            if len(unique) >= max(1, max_attempts):
                break

        documents: dict[str, SearchDocument] = {}
        attempts: list[SearchAttempt] = []
        failures: list[SearchProviderError] = []
        successful_attempts = 0
        acceptance_report: CandidateAcceptanceReport | None = None
        budget_exhausted_by: str | None = None

        for provider, api_key, market_search, variant in unique:
            reason = self.budget.exhaustion_reason(include_logical=False)
            if reason:
                budget_exhausted_by = reason
                break
            if not self.circuits.allows(provider):
                attempts.append(
                    SearchAttempt(
                        provider=provider,
                        market=market_search.market.code,
                        query=variant,
                        original_query=query,
                        status="skipped",
                        source_count=0,
                        skipped_reason="provider_circuit_open",
                    )
                )
                continue

            localized = self.localizer.localize(
                query=variant,
                intent=intent,
                market=market_search.market,
                evidence_role=evidence_role,
            )
            actual_query = localized.localized_query
            if actual_query not in relevance_queries:
                relevance_queries.append(actual_query)
            relevance_query = " ".join(relevance_queries)
            remaining_requests = max(
                0,
                self.budget.maximum_provider_requests
                - self.budget.provider_requests,
            )
            remaining_retries = max(
                0,
                self.budget.maximum_provider_retries
                - self.budget.provider_retries,
            )
            remaining_credit_requests = max(
                0,
                int(
                    self.budget.maximum_estimated_credits
                    - self.budget.estimated_credits
                ),
            )
            if remaining_requests <= 0:
                budget_exhausted_by = "provider_request_limit"
                break
            if remaining_credit_requests <= 0:
                budget_exhausted_by = "estimated_credit_limit"
                break
            request_attempt_limit = min(
                2,
                remaining_requests,
                remaining_retries + 1,
                remaining_credit_requests,
            )
            try:
                detailed = getattr(self.engine, "search_detailed", None)
                if callable(detailed):
                    try:
                        response = await detailed(
                            actual_query,
                            provider,
                            api_key,
                            max_results=max_results,
                            market=market_search.market,
                            exclude_brazil=market_search.exclude_brazil,
                            request_attempt_limit=request_attempt_limit,
                        )
                    except TypeError as exc:
                        # Compatibility with injected engines/test doubles created
                        # before V3.5 added request-level retry budgets.
                        if "request_attempt_limit" not in str(exc):
                            raise
                        response = await detailed(
                            actual_query,
                            provider,
                            api_key,
                            max_results=max_results,
                            market=market_search.market,
                            exclude_brazil=market_search.exclude_brazil,
                        )
                    found = response.documents
                    diagnostics = response.diagnostics.as_payload()
                else:
                    found = await self.engine.search(
                        actual_query,
                        provider,
                        api_key,
                        max_results=max_results,
                        market=market_search.market,
                        exclude_brazil=market_search.exclude_brazil,
                    )
                    diagnostics = {
                        "retained_documents": len(found),
                        "provider_requests": 1,
                        "provider_retries": 0,
                        "result_page_fetches": 0,
                        "estimated_credits": 1.0,
                    }
                self.budget.record_provider_call(
                    requests=int(diagnostics.get("provider_requests") or 1),
                    retries=int(diagnostics.get("provider_retries") or 0),
                    result_page_fetches=int(diagnostics.get("result_page_fetches") or 0),
                    estimated_credits=float(diagnostics.get("estimated_credits") or 1.0),
                )
                self.circuits.record_success(provider)
                successful_attempts += 1
                for document in found:
                    documents.setdefault(canonicalize_url(document.url), document)
                acceptance_report = self.acceptance_service.evaluate(
                    documents.values(),
                    subject=intent.canonical_subject,
                    query=relevance_query,
                    required_source_roles=required_source_roles,
                    minimum_independent_sources=minimum_independent_sources,
                    authoritative_required=authoritative_required,
                )
                attempts.append(
                    SearchAttempt(
                        provider=provider,
                        market=market_search.market.code,
                        query=actual_query,
                        original_query=query,
                        search_language=market_search.market.language_code,
                        localization_strategy=localized.strategy,
                        translation_confidence=localized.translation_confidence,
                        status="succeeded",
                        source_count=len(found),
                        diagnostics=diagnostics,
                        acceptance=acceptance_report.as_payload(),
                    )
                )
                if acceptance_report.sufficient:
                    break
            except SearchProviderError as exc:
                failures.append(exc)
                attempts_count = max(1, int(getattr(exc, "attempts", 1) or 1))
                self.budget.record_provider_call(
                    requests=attempts_count,
                    retries=max(0, attempts_count - 1),
                    estimated_credits=float(attempts_count),
                )
                self.circuits.record_failure(
                    provider,
                    exc.category,
                    retry_after=exc.retry_after,
                )
                attempts.append(
                    SearchAttempt(
                        provider=provider,
                        market=market_search.market.code,
                        query=actual_query,
                        original_query=query,
                        search_language=market_search.market.language_code,
                        localization_strategy=localized.strategy,
                        translation_confidence=localized.translation_confidence,
                        status="failed",
                        source_count=0,
                        error_category=exc.category,
                    )
                )

        if acceptance_report is None:
            acceptance_report = self.acceptance_service.evaluate(
                documents.values(),
                subject=intent.canonical_subject,
                query=" ".join(relevance_queries),
                required_source_roles=required_source_roles,
                minimum_independent_sources=minimum_independent_sources,
                authoritative_required=authoritative_required,
            )
        relevant_documents = [
            document
            for document in documents.values()
            if acceptance_report.relevance_by_url.get(
                canonicalize_url(document.url),
                0.0,
            )
            >= self.acceptance_service.minimum_relevance
        ]
        return ResilientSearchResult(
            documents=relevant_documents,
            attempts=attempts,
            failures=failures,
            successful_attempts=successful_attempts,
            acceptance=acceptance_report,
            budget_exhausted_by=budget_exhausted_by,
        )

    async def _single_attempt(
        self,
        *,
        provider: str,
        api_key: str,
        query: str,
        max_results: int,
        subject: str,
        required_source_roles: list[str],
        minimum_independent_sources: int,
        authoritative_required: bool,
    ) -> ResilientSearchResult:
        try:
            found = await self.engine.search(
                query,
                provider,
                api_key,
                max_results=max_results,
            )
            self.budget.record_provider_call()
            report = self.acceptance_service.evaluate(
                found,
                subject=subject,
                query=query,
                required_source_roles=required_source_roles,
                minimum_independent_sources=minimum_independent_sources,
                authoritative_required=authoritative_required,
            )
            relevant = [
                document
                for document in found
                if report.relevance_by_url.get(canonicalize_url(document.url), 0.0)
                >= self.acceptance_service.minimum_relevance
            ]
            return ResilientSearchResult(
                documents=relevant,
                attempts=[
                    SearchAttempt(
                        provider=provider,
                        market=None,
                        query=query,
                        original_query=query,
                        status="succeeded",
                        source_count=len(found),
                        diagnostics={"retained_documents": len(found), "provider_requests": 1},
                        acceptance=report.as_payload(),
                    )
                ],
                failures=[],
                successful_attempts=1,
                acceptance=report,
            )
        except SearchProviderError as exc:
            self.budget.record_provider_call(
                requests=max(1, int(getattr(exc, "attempts", 1) or 1)),
                retries=max(0, int(getattr(exc, "attempts", 1) or 1) - 1),
            )
            return ResilientSearchResult(
                documents=[],
                attempts=[
                    SearchAttempt(
                        provider=provider,
                        market=None,
                        query=query,
                        original_query=query,
                        status="failed",
                        source_count=0,
                        error_category=exc.category,
                    )
                ],
                failures=[exc],
                successful_attempts=0,
            )
