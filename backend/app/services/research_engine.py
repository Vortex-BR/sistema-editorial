import asyncio
import hashlib
import json
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.core.observability import structured_log
from app.core.sanitization import sanitize_nul, sanitize_nul_with_report
from app.services.llm_gateway import (
    ProviderError,
    provider_error_from_http,
    provider_error_from_transport,
)
from app.services.search_policy import (
    SearchMarket,
    is_brazilian_url,
    source_country_from_url,
)


SENSITIVE_URL_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "credential",
    "key",
    "password",
    "signature",
    "token",
    "x_amz_credential",
    "x_amz_signature",
    "x_goog_signature",
}


class SearchProviderError(ProviderError):
    pass


@dataclass
class SearchDocument:
    url: str
    title: str
    content: str
    publisher: str | None
    source_type: str
    reliability_score: float
    accessed_at: datetime
    author: str | None = None
    published_at: datetime | None = None
    extraction_method: str = "provider_content"
    search_market: str | None = None
    search_language: str | None = None
    source_country: str | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def as_payload(self) -> dict[str, object]:
        return {
            "url": self.url,
            "title": self.title,
            "publisher": self.publisher,
            "author": self.author,
            "source_type": self.source_type,
            "reliability": self.reliability_score,
            "published_at": (
                self.published_at.isoformat() if self.published_at else None
            ),
            "accessed_at": self.accessed_at.isoformat(),
            "extraction_method": self.extraction_method,
            "search_market": self.search_market,
            "search_language": self.search_language,
            "source_country": self.source_country,
            "content_hash": self.content_hash,
            "content": self.content,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "SearchDocument":
        content = sanitize_nul(str(payload["content"]), strip_escaped=True)
        expected_hash = str(payload["content_hash"])
        actual_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if expected_hash != actual_hash:
            raise ValueError("Cached source content hash mismatch")
        accessed_at = parse_published_at(payload.get("accessed_at"))
        if accessed_at is None:
            raise ValueError("Cached source has no valid access timestamp")
        return cls(
            url=canonicalize_url(str(payload["url"])),
            title=sanitize_nul(str(payload["title"]), strip_escaped=True),
            content=content,
            publisher=(
                sanitize_nul(str(payload["publisher"]), strip_escaped=True)
                if payload.get("publisher")
                else None
            ),
            source_type=sanitize_nul(
                str(payload["source_type"]), strip_escaped=True
            ),
            reliability_score=float(payload["reliability"]),
            accessed_at=accessed_at,
            author=(
                sanitize_nul(str(payload["author"]), strip_escaped=True)
                if payload.get("author")
                else None
            ),
            published_at=parse_published_at(payload.get("published_at")),
            extraction_method=sanitize_nul(
                str(payload.get("extraction_method") or "provider_content"),
                strip_escaped=True,
            ),
            search_market=(
                sanitize_nul(str(payload["search_market"]), strip_escaped=True)
                if payload.get("search_market")
                else None
            ),
            search_language=(
                sanitize_nul(str(payload["search_language"]), strip_escaped=True)
                if payload.get("search_language")
                else None
            ),
            source_country=(
                sanitize_nul(str(payload["source_country"]), strip_escaped=True)
                if payload.get("source_country")
                else None
            ),
        )


@dataclass
class SearchDiagnostics:
    provider: str
    query: str
    market: str | None
    raw_results: int = 0
    retained_documents: int = 0
    discarded_invalid_result: int = 0
    discarded_missing_url: int = 0
    discarded_invalid_url: int = 0
    discarded_excluded_country: int = 0
    discarded_short_content: int = 0
    enrichment_failures: int = 0
    provider_requests: int = 0
    provider_retries: int = 0
    result_page_fetches: int = 0
    estimated_credits: float = 0.0
    elapsed_ms: int = 0

    def as_payload(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "query": self.query,
            "market": self.market,
            "raw_results": self.raw_results,
            "retained_documents": self.retained_documents,
            "discarded_invalid_result": self.discarded_invalid_result,
            "discarded_missing_url": self.discarded_missing_url,
            "discarded_invalid_url": self.discarded_invalid_url,
            "discarded_excluded_country": self.discarded_excluded_country,
            "discarded_short_content": self.discarded_short_content,
            "enrichment_failures": self.enrichment_failures,
            "provider_requests": self.provider_requests,
            "provider_retries": self.provider_retries,
            "result_page_fetches": self.result_page_fetches,
            "estimated_credits": self.estimated_credits,
            "elapsed_ms": self.elapsed_ms,
        }


@dataclass
class SearchResponse:
    documents: list[SearchDocument]
    diagnostics: SearchDiagnostics


@dataclass(frozen=True)
class _ProviderJSONResponse:
    payload: dict
    attempts: int
    elapsed_ms: int


def canonicalize_url(url: str) -> str:
    url = sanitize_nul(str(url), strip_escaped=True)
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"gclid", "fbclid"}
            and key.lower().replace("-", "_") not in SENSITIVE_URL_QUERY_KEYS
        ]
    )
    netloc = parts.netloc.rsplit("@", 1)[-1].lower()
    return urlunsplit(
        (parts.scheme.lower(), netloc, parts.path, query, "")
    )


def classify_source(url: str) -> tuple[str, float]:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if (
        host.endswith(".gov")
        or ".gov." in host
        or host.endswith(".go.jp")
        or host.endswith(".gob.es")
        or host == "who.int"
        or host.endswith(".who.int")
        or host == "fao.org"
        or host.endswith(".fao.org")
        or host == "europa.eu"
        or host.endswith(".europa.eu")
    ):
        return "government", 0.95
    if (
        host.endswith(".edu")
        or ".edu." in host
        or ".ac." in host
        or "univers" in host
    ):
        return "university", 0.9
    if any(
        marker in host
        for marker in (
            "pubmed",
            "ncbi.nlm.nih.gov",
            "scielo",
            "nature.com",
            "science.org",
            "springer.com",
            "wiley.com",
            "frontiersin.org",
            "mdpi.com",
            "jstor.org",
            "doi.org",
        )
    ):
        return "scientific", 0.95
    if any(
        marker in host
        for marker in (
            "reuters",
            "apnews",
            "bbc.",
            "agenciabrasil",
            "theguardian",
        )
    ):
        return "news", 0.82
    if any(marker in host for marker in ("reddit", "quora", "forum")):
        return "forum", 0.45
    return "practical", 0.65


def parse_published_at(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ResearchEngine:
    def __init__(
        self,
        timeout_seconds: float = 30,
        *,
        client_factory=None,
        sleep=None,
        jitter=None,
    ):
        self.timeout = timeout_seconds
        self._client_factory = client_factory or httpx.AsyncClient
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.uniform

    async def search(
        self,
        query: str,
        provider: str,
        api_key: str,
        max_results: int = 5,
        *,
        market: SearchMarket | None = None,
        exclude_brazil: bool = False,
        request_attempt_limit: int = 3,
    ) -> list[SearchDocument]:
        return (
            await self.search_detailed(
                query,
                provider,
                api_key,
                max_results=max_results,
                market=market,
                exclude_brazil=exclude_brazil,
                request_attempt_limit=request_attempt_limit,
            )
        ).documents

    async def search_detailed(
        self,
        query: str,
        provider: str,
        api_key: str,
        max_results: int = 5,
        *,
        market: SearchMarket | None = None,
        exclude_brazil: bool = False,
        request_attempt_limit: int = 3,
    ) -> SearchResponse:
        query = re.sub(r"\s+", " ", str(query or "")).strip()
        if not query:
            raise SearchProviderError(
                "invalid_request", provider=provider, model="search", retryable=False
            )
        if provider == "tavily":
            response = await self._tavily_detailed(
                query,
                api_key,
                max_results,
                market=market,
                exclude_brazil=exclude_brazil,
                request_attempt_limit=request_attempt_limit,
            )
        elif provider == "serper":
            response = await self._serper_detailed(
                query,
                api_key,
                max_results,
                market=market,
                exclude_brazil=exclude_brazil,
                request_attempt_limit=request_attempt_limit,
            )
        else:
            raise SearchProviderError(
                "invalid_request",
                provider=provider,
                model="search",
                retryable=False,
            )
        structured_log("search.completed", **response.diagnostics.as_payload())
        return response

    async def discover_keywords(
        self,
        query: str,
        provider: str,
        api_key: str,
        *,
        market: SearchMarket,
        limit: int = 12,
    ) -> list[str]:
        """Return real Google related searches/PAA without using result pages as facts."""
        if provider != "serper":
            return []
        async with self._client_factory(timeout=self.timeout) as client:
            provider_response = await self._search_json(
                client,
                provider="serper",
                url="https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                payload={
                    "q": query,
                    "num": 5,
                    "gl": market.country_code,
                    "hl": market.language_code,
                },
            )
        payload = provider_response.payload
        candidates = []
        for item in payload.get("relatedSearches", []):
            if isinstance(item, dict):
                candidates.append(item.get("query"))
        for item in payload.get("peopleAlsoAsk", []):
            if isinstance(item, dict):
                candidates.append(item.get("question"))
        return list(
            dict.fromkeys(
                keyword
                for value in candidates
                if (keyword := sanitize_nul(str(value or ""), strip_escaped=True).strip())
            )
        )[:limit]

    @staticmethod
    def _search_error(error: ProviderError, provider: str) -> SearchProviderError:
        return SearchProviderError(
            error.category,
            provider=provider,
            model="search",
            http_status=error.http_status,
            retryable=error.retryable,
            retry_after=error.retry_after,
            latency_ms=error.latency_ms,
            attempts=error.attempts,
            error_code=f"search_{error.category}",
        )

    async def _search_json(
        self,
        client: httpx.AsyncClient,
        *,
        provider: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, object],
        attempt_limit: int = 3,
    ) -> _ProviderJSONResponse:
        invalid_output_count = 0
        attempt_limit = max(1, min(3, int(attempt_limit)))
        started = time.perf_counter()
        for attempt in range(1, attempt_limit + 1):
            try:
                response = await client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, dict):
                    raise TypeError("Search provider payload is not an object")
                return _ProviderJSONResponse(
                    payload=result,
                    attempts=attempt,
                    elapsed_ms=int((time.perf_counter() - started) * 1000),
                )
            except httpx.HTTPStatusError as exc:
                error = provider_error_from_http(
                    exc, provider=provider, model="search"
                )
            except httpx.TransportError as exc:
                error = provider_error_from_transport(
                    exc, provider=provider, model="search"
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                invalid_output_count += 1
                error = ProviderError(
                    "invalid_output",
                    provider=provider,
                    model="search",
                    retryable=True,
                )

            should_retry = (
                attempt < attempt_limit
                and (
                    error.retryable
                    if error.category != "invalid_output"
                    else invalid_output_count <= 1
                )
            )
            if not should_retry:
                raise self._search_error(
                    error.finalized(
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        attempts=attempt,
                    ),
                    provider,
                )
            delay = max(
                min(8.0, float(2 ** (attempt - 1))) + self._jitter(0.0, 0.5),
                error.retry_after or 0.0,
            )
            structured_log(
                "search.retry_scheduled",
                provider=provider,
                model="search",
                attempt=attempt,
                error_code=f"search_{error.category}",
                http_status=error.http_status,
                retryable=True,
                retry_delay_ms=int(delay * 1000),
            )
            await self._sleep(delay)
        raise SearchProviderError(
            "unavailable", provider=provider, model="search", retryable=False
        )

    async def _tavily(
        self,
        query: str,
        api_key: str,
        max_results: int,
        *,
        market: SearchMarket | None = None,
        exclude_brazil: bool = False,
        request_attempt_limit: int = 3,
    ) -> list[SearchDocument]:
        return (
            await self._tavily_detailed(
                query,
                api_key,
                max_results,
                market=market,
                exclude_brazil=exclude_brazil,
                request_attempt_limit=request_attempt_limit,
            )
        ).documents

    async def _tavily_detailed(
        self,
        query: str,
        api_key: str,
        max_results: int,
        *,
        market: SearchMarket | None = None,
        exclude_brazil: bool = False,
        request_attempt_limit: int = 3,
    ) -> SearchResponse:
        async with self._client_factory(timeout=self.timeout) as client:
            provider_response = await self._search_json(
                client,
                provider="tavily",
                url="https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {api_key}"},
                payload={
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": "text",
                    **(
                        {
                            "country": {
                                "us": "united states",
                                "es": "spain",
                                "ch": "switzerland",
                                "br": "brazil",
                            }[market.code]
                        }
                        if market and market.code in {"us", "es", "ch", "br"}
                        else {}
                    ),
                },
                attempt_limit=request_attempt_limit,
            )
        payload = provider_response.payload
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []
        diagnostics = SearchDiagnostics(
            provider="tavily",
            query=query,
            market=market.code if market else None,
            raw_results=len(raw_results),
            provider_requests=provider_response.attempts,
            provider_retries=max(0, provider_response.attempts - 1),
            estimated_credits=float(provider_response.attempts),
            elapsed_ms=provider_response.elapsed_ms,
        )
        documents: list[SearchDocument] = []
        for index, raw_item in enumerate(raw_results[:max_results]):
            item, report = sanitize_nul_with_report(
                raw_item, strip_escaped=True, path=f"$.tavily.results[{index}]"
            )
            if not isinstance(item, dict):
                diagnostics.discarded_invalid_result += 1
                continue
            raw_url = str(item.get("url") or "").strip()
            if not raw_url:
                diagnostics.discarded_missing_url += 1
                continue
            url = canonicalize_url(raw_url)
            parsed_url = urlsplit(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                diagnostics.discarded_invalid_url += 1
                continue
            if exclude_brazil and is_brazilian_url(url):
                diagnostics.discarded_excluded_country += 1
                continue
            title = sanitize_nul(item.get("title") or url, strip_escaped=True)
            content = sanitize_nul(
                item.get("raw_content") or item.get("content") or "",
                strip_escaped=True,
            ).strip()
            if len(content) < 40:
                combined = f"{title}. {content}".strip()
                if content and len(combined) >= 40:
                    content = combined
                else:
                    diagnostics.discarded_short_content += 1
                    continue
            source_type, reliability = classify_source(url)
            self._log_sanitization(report, "tavily", url, source_type)
            documents.append(
                SearchDocument(
                    url=url,
                    title=title,
                    content=content[:20000],
                    publisher=urlsplit(url).netloc,
                    source_type=source_type,
                    reliability_score=reliability,
                    accessed_at=datetime.now(timezone.utc),
                    author=item.get("author"),
                    published_at=parse_published_at(
                        item.get("published_date") or item.get("date")
                    ),
                    extraction_method=(
                        "tavily_raw_content"
                        if item.get("raw_content")
                        else "tavily_snippet"
                    ),
                    search_market=market.code if market else None,
                    search_language=market.language_code if market else None,
                    source_country=source_country_from_url(url),
                )
            )
        diagnostics.retained_documents = len(documents)
        return SearchResponse(documents=documents, diagnostics=diagnostics)

    async def _serper(
        self,
        query: str,
        api_key: str,
        max_results: int,
        *,
        market: SearchMarket | None = None,
        exclude_brazil: bool = False,
        request_attempt_limit: int = 3,
    ) -> list[SearchDocument]:
        return (
            await self._serper_detailed(
                query,
                api_key,
                max_results,
                market=market,
                exclude_brazil=exclude_brazil,
                request_attempt_limit=request_attempt_limit,
            )
        ).documents

    async def _serper_detailed(
        self,
        query: str,
        api_key: str,
        max_results: int,
        *,
        market: SearchMarket | None = None,
        exclude_brazil: bool = False,
        request_attempt_limit: int = 3,
    ) -> SearchResponse:
        # Serper is used only for discovery. Result pages are intentionally not
        # fetched here; SourceDocumentParser is the single, SSRF-hardened reader.
        async with self._client_factory(timeout=self.timeout) as client:
            provider_response = await self._search_json(
                client,
                provider="serper",
                url="https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                payload={
                    "q": query,
                    "num": max_results,
                    **(
                        {"gl": market.country_code, "hl": market.language_code}
                        if market
                        else {}
                    ),
                },
                attempt_limit=request_attempt_limit,
            )
        raw_results = provider_response.payload.get("organic", [])
        if not isinstance(raw_results, list):
            raw_results = []
        results = raw_results[:max_results]
        diagnostics = SearchDiagnostics(
            provider="serper",
            query=query,
            market=market.code if market else None,
            raw_results=len(results),
            provider_requests=provider_response.attempts,
            provider_retries=max(0, provider_response.attempts - 1),
            result_page_fetches=0,
            estimated_credits=float(provider_response.attempts),
            elapsed_ms=provider_response.elapsed_ms,
        )
        documents: list[SearchDocument] = []
        for index, raw_item in enumerate(results):
            item, report = sanitize_nul_with_report(
                raw_item, strip_escaped=True, path=f"$.serper.organic[{index}]"
            )
            if not isinstance(item, dict):
                diagnostics.discarded_invalid_result += 1
                continue
            raw_url = str(item.get("link") or "").strip()
            if not raw_url:
                diagnostics.discarded_missing_url += 1
                continue
            url = canonicalize_url(raw_url)
            parsed_url = urlsplit(url)
            if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                diagnostics.discarded_invalid_url += 1
                continue
            if exclude_brazil and is_brazilian_url(url):
                diagnostics.discarded_excluded_country += 1
                continue
            title = sanitize_nul(item.get("title") or url, strip_escaped=True)
            content = sanitize_nul(item.get("snippet") or "", strip_escaped=True).strip()
            combined = f"{title}. {content}".strip()
            if len(combined) < 40:
                diagnostics.discarded_short_content += 1
                continue
            source_type, reliability = classify_source(url)
            self._log_sanitization(report, "serper", url, source_type)
            documents.append(
                SearchDocument(
                    url=url,
                    title=title,
                    content=combined[:4000],
                    publisher=urlsplit(url).netloc,
                    source_type=source_type,
                    reliability_score=reliability,
                    accessed_at=datetime.now(timezone.utc),
                    author=item.get("author"),
                    published_at=parse_published_at(item.get("date")),
                    extraction_method="serper_snippet",
                    search_market=market.code if market else None,
                    search_language=market.language_code if market else None,
                    source_country=source_country_from_url(url),
                )
            )
        diagnostics.retained_documents = len(documents)
        return SearchResponse(documents=documents, diagnostics=diagnostics)

    @staticmethod
    def _log_sanitization(report, provider: str, url: str, source_type: str) -> None:
        if not (report.nul_removed_count or report.escaped_nul_removed_count):
            return
        structured_log(
            "research.content_sanitized",
            provider=provider,
            domain=urlsplit(url).netloc,
            source_type=source_type,
            **report.as_log_context(),
        )
