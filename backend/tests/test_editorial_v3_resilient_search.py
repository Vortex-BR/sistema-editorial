from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.editorial_v3.knowledge_contract import KnowledgeContractInput
from app.services.editorial_v3.resilient_search import ResilientSearchCoordinator
from app.services.research_engine import (
    SearchDiagnostics,
    SearchDocument,
    SearchProviderError,
    SearchResponse,
)


def _document(url: str) -> SearchDocument:
    return SearchDocument(
        url=url,
        title="Germinação de sementes com papel-toalha",
        content=(
            "Evidência independente sobre germinação de sementes, papel-toalha, "
            "umidade, recipiente fechado e sinais observáveis do processo."
        ),
        publisher="example.org",
        source_type="practical",
        reliability_score=0.65,
        accessed_at=datetime.now(timezone.utc),
    )


class _RecoveryEngine:
    def __init__(self):
        self.calls: list[dict] = []

    async def search_detailed(
        self,
        query,
        provider,
        api_key,
        *,
        max_results,
        market,
        exclude_brazil,
    ):
        self.calls.append(
            {
                "query": query,
                "provider": provider,
                "api_key": api_key,
                "max_results": max_results,
                "market": market.code,
                "exclude_brazil": exclude_brazil,
            }
        )
        if len(self.calls) == 1:
            return SearchResponse(
                documents=[],
                diagnostics=SearchDiagnostics(
                    provider=provider,
                    query=query,
                    market=market.code,
                    raw_results=0,
                ),
            )
        if len(self.calls) == 2:
            raise SearchProviderError(
                "unavailable",
                provider=provider,
                model="search",
                retryable=True,
            )
        documents = [_document("https://example.org/a"), _document("https://example.net/b")]
        return SearchResponse(
            documents=documents,
            diagnostics=SearchDiagnostics(
                provider=provider,
                query=query,
                market=market.code,
                raw_results=2,
                retained_documents=2,
            ),
        )


@pytest.mark.asyncio
async def test_resilient_search_uses_market_rotation_and_fallback_provider():
    engine = _RecoveryEngine()
    result = await ResilientSearchCoordinator(engine).search(
        query="germinação em recipiente definição mecanismo evidência revisão",
        topic="guia de germinação de sementes",
        question="Quais condições e sinais confirmam o processo?",
        search_subject="germinação de sementes por papel-toalha em recipiente fechado",
        provider_credentials=[("tavily", "t-key"), ("serper", "s-key")],
        max_results=5,
    )

    assert len(result.documents) == 2
    assert [call["provider"] for call in engine.calls] == [
        "tavily",
        "serper",
        "tavily",
    ]
    assert [call["market"] for call in engine.calls] == ["br", "br", "us"]
    assert all(call["exclude_brazil"] is False for call in engine.calls)
    assert result.successful_attempts == 2
    assert len(result.failures) == 1
    assert result.attempts[-1].status == "succeeded"


def test_project_input_builds_factual_subject_beyond_the_seo_keyword():
    project = SimpleNamespace(
        topic="Como germinar sementes de cannabis com papel-toalha em recipiente fechado",
        editorial_pipeline_version="v3",
        briefing={
            "primary_keyword": "germinação em Tupperware",
            "secondary_keywords": ["sementes de cannabis", "papel-toalha"],
            "segment": "cultivo doméstico",
            "content_objective": "explicar um processo controlado por umidade e temperatura",
            "editorial_content_type": "explanatory_guide",
            "reader_start_state": "Leitor sem clareza sobre materiais e condições.",
            "reader_final_state": "Leitor capaz de reconhecer o resultado observado.",
            "article_promise": "Explicar preparação, processo, sinais e falhas.",
            "scope_limit": "Encerrar na confirmação do resultado.",
        },
    )

    data = KnowledgeContractInput.from_project(project)

    assert data.search_subject.startswith("Como germinar sementes de cannabis")
    assert "papel-toalha" in data.search_subject
    assert "germinação em Tupperware" in data.search_subject
    assert len(data.search_subject) <= 360
