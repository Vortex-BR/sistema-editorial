from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, TypeVar
from urllib.parse import urlsplit


SEARCH_RESULTS_PER_MARKET = 7
MAX_DOCUMENTS_PER_QUESTION = 9
MAX_EXTRACTION_CHARS_PER_DOCUMENT = 3500
MIN_FACT_SOURCE_RELIABILITY = 0.60


@dataclass(frozen=True)
class SearchMarket:
    code: str
    country_code: str
    language_code: str
    query_key: str
    label: str


@dataclass(frozen=True)
class MarketSearch:
    market: SearchMarket
    query: str
    exclude_brazil: bool = False


UNITED_STATES = SearchMarket(
    code="us",
    country_code="us",
    language_code="en",
    query_key="united_states",
    label="Estados Unidos",
)
SPAIN = SearchMarket(
    code="es",
    country_code="es",
    language_code="es",
    query_key="spain",
    label="Espanha",
)
SWITZERLAND = SearchMarket(
    code="ch",
    country_code="ch",
    language_code="de",
    query_key="switzerland",
    label="Suíça",
)
BRAZIL = SearchMarket(
    code="br",
    country_code="br",
    language_code="pt",
    query_key="brazil",
    label="Brasil",
)

ALL_SEARCH_MARKETS = (BRAZIL, UNITED_STATES, SPAIN, SWITZERLAND)
INTERNATIONAL_SEARCH_MARKETS = (UNITED_STATES, SPAIN, SWITZERLAND)
_MARKET_BY_CODE = {market.code: market for market in ALL_SEARCH_MARKETS}
_LOCALE_MARKET = {"pt": BRAZIL, "en": UNITED_STATES, "es": SPAIN, "de": SWITZERLAND}

_GLOBAL_EVIDENCE_ROLES = {
    "definition",
    "mechanism",
    "risk",
    "limitation",
    "comparison",
    "external_reference",
}
_SCIENTIFIC_SOURCE_ROLES = {
    "scientific_primary",
    "scientific_review",
    "academic_repository",
    "scientific_database",
}
_T = TypeVar("_T")


def _locale_language(project_locale: str | None) -> str:
    normalized = str(project_locale or "pt-BR").strip().replace("_", "-").casefold()
    return normalized.split("-", 1)[0] or "pt"


def select_search_markets(
    *,
    project_locale: str = "pt-BR",
    evidence_role: object | None = None,
    required_source_roles: Iterable[object] = (),
    maximum_markets: int = 3,
) -> tuple[SearchMarket, ...]:
    """Choose markets from intent instead of applying a fixed international list.

    Local evidence is searched first. US/English is added for
    scientific, mechanism, risk and comparative evidence because those tasks
    frequently benefit from global databases.  The result is deterministic and
    bounded so one logical query cannot fan out without limit.
    """

    maximum_markets = max(1, min(4, int(maximum_markets)))
    language = _locale_language(project_locale)
    local = _LOCALE_MARKET.get(language, BRAZIL)
    role = str(getattr(evidence_role, "value", evidence_role) or "").casefold()
    source_roles = {
        str(getattr(item, "value", item) or "").casefold()
        for item in required_source_roles
    }

    ordered: list[SearchMarket] = []

    def add(market: SearchMarket | None) -> None:
        if market is not None and market not in ordered:
            ordered.append(market)

    add(local)
    if (
        not role
        or role in _GLOBAL_EVIDENCE_ROLES
        or source_roles & _SCIENTIFIC_SOURCE_ROLES
    ):
        add(UNITED_STATES)
    # A second language-aligned corpus improves recall without replacing local
    # evidence. Spanish is the most useful deterministic fallback for Portuguese;
    # English is the general fallback for every other locale.
    add(SPAIN if language == "pt" else UNITED_STATES)
    if role in {"external_reference", "comparison", "limitation"}:
        add(SWITZERLAND)
    for market in ALL_SEARCH_MARKETS:
        add(market)
    return tuple(ordered[:maximum_markets])


def market_search_plan(
    *,
    topic: str,
    question: str,
    fallback_query: str,
    localized_queries: Mapping[str, object] | None = None,
    project_locale: str = "pt-BR",
    evidence_role: object | None = None,
    required_source_roles: Iterable[object] = (),
    maximum_markets: int = 3,
    exclude_local_domains: bool = False,
) -> tuple[MarketSearch, ...]:
    """Return an intent-aware, language-aware market plan.

    V3.5 deliberately removes the old global ``-site:.br`` rule.  A local domain
    may contain the best regulatory, institutional or procedural evidence for a
    Portuguese project. Domain exclusion is now an explicit caller policy only.
    """

    localized_queries = localized_queries or {}
    markets = select_search_markets(
        project_locale=project_locale,
        evidence_role=evidence_role,
        required_source_roles=required_source_roles,
        maximum_markets=maximum_markets,
    )
    searches: list[MarketSearch] = []
    local_market = _LOCALE_MARKET.get(_locale_language(project_locale), BRAZIL)
    for market in markets:
        query = str(
            localized_queries.get(market.query_key)
            or localized_queries.get(market.code)
            or fallback_query
        ).strip()
        exclude_brazil = bool(exclude_local_domains and local_market == BRAZIL and market != BRAZIL)
        if exclude_brazil and "-site:.br" not in query.casefold():
            query = f"{query} -site:.br"
        searches.append(
            MarketSearch(
                market=market,
                query=query[:1600],
                exclude_brazil=exclude_brazil,
            )
        )
    return tuple(searches)


def is_brazilian_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == "br" or host.endswith(".br")


def source_country_from_url(url: str) -> str | None:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    for suffix, country in ((".br", "br"), (".es", "es"), (".ch", "ch"), (".us", "us")):
        if host.endswith(suffix):
            return country
    if host.endswith((".gov", ".edu", ".mil")):
        return "us"
    return None


def merge_market_results(
    result_groups: Iterable[Iterable[_T]], *, limit: int = 10
) -> list[_T]:
    """Round-robin merging prevents the first market from dominating the prompt."""

    groups = [
        sorted(
            group,
            key=lambda item: float(getattr(item, "reliability_score", 0.0)),
            reverse=True,
        )
        for group in result_groups
    ]
    merged: list[_T] = []
    seen_urls: set[str] = set()
    index = 0
    while len(merged) < limit and any(index < len(group) for group in groups):
        for group in groups:
            if index >= len(group):
                continue
            item = group[index]
            url = str(getattr(item, "url", ""))
            if url and url not in seen_urls:
                seen_urls.add(url)
                merged.append(item)
                if len(merged) >= limit:
                    break
        index += 1
    return merged


def search_policy_manifest() -> dict[str, object]:
    return {
        "policy_version": "intent-aware-search.v3.5",
        "market_selection": "project_locale_then_evidence_role",
        "available_markets": [market.code for market in ALL_SEARCH_MARKETS],
        "local_market_is_searched_first": True,
        "brazil_market_requires_explicit_context": False,
        "brazilian_results_are_isolated_to_brazil_market": False,
        "exclude_brazilian_domains_by_default": False,
        "maximum_markets_per_logical_query": 3,
        "queries_are_localized_per_market": True,
        "results_per_market": SEARCH_RESULTS_PER_MARKET,
        "maximum_documents_per_question": MAX_DOCUMENTS_PER_QUESTION,
        "maximum_extraction_characters_per_document": MAX_EXTRACTION_CHARS_PER_DOCUMENT,
        "minimum_fact_source_reliability": MIN_FACT_SOURCE_RELIABILITY,
        "keyword_discovery_market": "project_locale",
        "keyword_discovery_source": "google_serper_related_searches",
        "keyword_discovery_results_are_not_factual_sources": True,
    }
