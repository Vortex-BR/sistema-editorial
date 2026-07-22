import pytest

from app.services.research_engine import ResearchEngine, canonicalize_url, classify_source
from app.services.search_policy import BRAZIL, UNITED_STATES


def test_canonicalize_url_removes_tracking_and_fragment():
    assert (
        canonicalize_url("https://Example.com/a?utm_source=x&id=2#section")
        == "https://example.com/a?id=2"
    )


def test_canonicalize_url_never_retains_credentials():
    assert canonicalize_url(
        "https://user:secret@Example.com/a?id=2&access_token=private"
        "&X-Amz-Signature=signed#section"
    ) == "https://example.com/a?id=2"


def test_government_source_receives_high_reliability():
    source_type, score = classify_source("https://www.gov.br/agricultura/guia")
    assert source_type == "government"
    assert score >= 0.9


@pytest.mark.asyncio
async def test_serper_receives_explicit_foreign_market_and_language():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"organic": []}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return Response()

    await ResearchEngine(client_factory=lambda **_kwargs: Client())._serper(
        "hemp germination -site:.br",
        "secret",
        3,
        market=UNITED_STATES,
        exclude_brazil=True,
    )

    assert captured["json"] == {
        "q": "hemp germination -site:.br",
        "num": 3,
        "gl": "us",
        "hl": "en",
    }
    assert captured["headers"]["X-API-KEY"] == "secret"


@pytest.mark.asyncio
async def test_serper_never_fetches_result_pages_during_discovery():
    get_calls = 0

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "organic": [
                    {
                        "link": f"https://example{index}.com/guide",
                        "title": f"Seed germination guide {index}",
                        "snippet": "Independent seed germination evidence " * 4,
                    }
                    for index in range(3)
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, *, headers, json):
            assert headers["X-API-KEY"] == "secret"
            assert json["gl"] == "us"
            return Response()

        async def get(self, _url):
            nonlocal get_calls
            get_calls += 1
            raise AssertionError("Result pages must be fetched only by SourceDocumentParser")

    response = await ResearchEngine(
        timeout_seconds=1,
        client_factory=lambda **_kwargs: Client(),
    )._serper_detailed(
        "seed germination",
        "secret",
        3,
        market=UNITED_STATES,
    )

    assert len(response.documents) == 3
    assert get_calls == 0
    assert response.diagnostics.result_page_fetches == 0
    assert response.diagnostics.provider_requests == 1
    assert all(item.extraction_method == "serper_snippet" for item in response.documents)


@pytest.mark.asyncio
async def test_keyword_discovery_uses_real_google_related_searches_for_brazil():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "relatedSearches": [
                    {"query": "como germinar semente de cannabis"},
                    {"query": "germinação de cannabis"},
                ],
                "peopleAlsoAsk": [
                    {"question": "Quanto tempo demora para germinar?"}
                ],
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return Response()

    keywords = await ResearchEngine(
        client_factory=lambda **_kwargs: Client()
    ).discover_keywords(
        "germinar semente cannabis",
        "serper",
        "secret",
        market=BRAZIL,
    )

    assert captured["json"] == {
        "q": "germinar semente cannabis",
        "num": 5,
        "gl": "br",
        "hl": "pt",
    }
    assert keywords == [
        "como germinar semente de cannabis",
        "germinação de cannabis",
        "Quanto tempo demora para germinar?",
    ]


@pytest.mark.asyncio
async def test_tavily_search_applies_country_boost_and_keeps_useful_short_snippet():
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "url": "https://example.com/technical-guide",
                        "title": "Technical germination guide",
                        "content": "Useful evidence for the requested process.",
                    }
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update({"url": url, "headers": headers, "json": json})
            return Response()

    response = await ResearchEngine(
        client_factory=lambda **_kwargs: Client()
    ).search_detailed(
        "seed germination technical guide -site:.br",
        "tavily",
        "secret",
        max_results=3,
        market=UNITED_STATES,
        exclude_brazil=True,
    )

    assert captured["json"]["country"] == "united states"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert len(response.documents) == 1
    assert response.diagnostics.raw_results == 1
    assert response.diagnostics.retained_documents == 1


@pytest.mark.asyncio
async def test_tavily_diagnostics_survive_malformed_provider_items():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    "unexpected",
                    {"url": "relative/path", "title": "Bad URL", "content": "A" * 80},
                    {"url": "", "title": "Missing URL", "content": "B" * 80},
                ]
            }

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    response = await ResearchEngine(
        client_factory=lambda **_kwargs: Client()
    ).search_detailed(
        "technical query",
        "tavily",
        "secret",
        max_results=3,
    )

    assert response.documents == []
    assert response.diagnostics.discarded_invalid_result == 1
    assert response.diagnostics.discarded_invalid_url == 1
    assert response.diagnostics.discarded_missing_url == 1


def test_classify_source_recognizes_international_institutional_and_scientific_hosts():
    from app.services.research_engine import classify_source

    assert classify_source("https://www.who.int/publications/item")[0] == "government"
    assert classify_source("https://example.ac.uk/research")[0] == "university"
    assert classify_source("https://link.springer.com/article/123")[0] == "scientific"
    assert classify_source("https://www.frontiersin.org/journals/plant-science")[0] == "scientific"
