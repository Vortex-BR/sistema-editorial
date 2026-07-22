import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.schemas.editorial_v3 import (
    ResearchSourceSignals,
    SourceOwnershipType,
    SourcePageType,
)
from app.services.editorial_v3.source_assessment_repository import (
    SourceAssessmentRepository,
)


def _signals():
    return ResearchSourceSignals(
        url="https://shop.example.com/blog/germination",
        ownership_type=SourceOwnershipType.ecommerce,
        page_type=SourcePageType.ecommerce_blog_article,
        is_ecommerce_domain=True,
        author_present=True,
        references_present=True,
        topic_relevance_score=0.9,
        content_depth_score=0.8,
        procedural_depth_score=0.8,
        commercial_intensity_score=0.6,
    )


@pytest.mark.asyncio
async def test_materialize_creates_comparison_only_assessment():
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        add=Mock(),
        flush=AsyncMock(),
    )

    result = await SourceAssessmentRepository(db).materialize(
        contract_id=uuid.uuid4(),
        signals=_signals(),
        pipeline_run_id=uuid.uuid4(),
    )

    assert result.created is True
    assert result.row.usage_policy == "comparison_only"
    assert result.row.minimum_independent_corroborators == 2
    assert result.row.eligible_for_external_reference is False
    db.add.assert_called_once_with(result.row)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_materialize_updates_existing_assessment_idempotently():
    existing = SimpleNamespace()
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=existing),
        add=Mock(),
        flush=AsyncMock(),
    )

    result = await SourceAssessmentRepository(db).materialize(
        contract_id=uuid.uuid4(),
        signals=_signals(),
    )

    assert result.created is False
    assert result.row is existing
    assert existing.source_role == "ecommerce_blog"
    assert existing.counts_toward_independent_source_diversity is False
    db.add.assert_not_called()
    db.flush.assert_awaited_once()


def test_url_hash_is_deterministic_and_removes_tracking_parameters():
    first = SourceAssessmentRepository.url_hash(
        "HTTPS://Example.org/article/?utm_source=google&b=2&a=1#section"
    )
    second = SourceAssessmentRepository.url_hash(
        "https://example.org/article?a=1&b=2"
    )

    assert first == second
    assert len(first) == 64
    assert SourceAssessmentRepository.canonicalize_url(
        "https://example.org/article/?utm_campaign=x#fragment"
    ) == "https://example.org/article"
