import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db.models import SourceSnapshot
from app.services.research_engine import SearchDocument
from app.services.research_ledger import ResearchLedgerService


CAPTURED_AT = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)


class SnapshotDb:
    def __init__(self, source):
        self.source = source
        self.added = []

    async def scalar(self, statement):
        sql = str(statement)
        if "FROM sources" in sql:
            return self.source
        if "FROM source_snapshots" in sql:
            return None
        raise AssertionError(f"Unexpected scalar query: {sql}")

    def add(self, value):
        value.id = uuid.uuid4()
        self.added.append(value)

    async def flush(self):
        return None


def document(*, title, content, reliability, captured_at, author, published_at):
    return SearchDocument(
        url="https://example.com/article",
        title=title,
        content=content,
        publisher="Example Press",
        source_type="news",
        reliability_score=reliability,
        accessed_at=captured_at,
        author=author,
        published_at=published_at,
        extraction_method="serper_html_text",
    )


@pytest.mark.asyncio
async def test_same_url_captures_independent_metadata_for_each_run():
    source = SimpleNamespace(
        id=uuid.uuid4(),
        title="Aggregate title at first collection",
        reliability_score=0.8,
    )
    first_document = document(
        title="Captured title A",
        content="First captured article body with immutable evidence.",
        reliability=0.91,
        captured_at=CAPTURED_AT,
        author="Author A",
        published_at=CAPTURED_AT - timedelta(days=2),
    )
    first_db = SnapshotDb(source)
    _, first_snapshot = await ResearchLedgerService(
        first_db, uuid.uuid4(), uuid.uuid4()
    )._source_snapshot(first_document)

    source.title = "Aggregate title changed later"
    source.reliability_score = 0.2
    second_document = document(
        title="Captured title B",
        content="Second captured article body with revised evidence.",
        reliability=0.73,
        captured_at=CAPTURED_AT + timedelta(days=1),
        author="Author B",
        published_at=CAPTURED_AT - timedelta(days=1),
    )
    second_db = SnapshotDb(source)
    _, second_snapshot = await ResearchLedgerService(
        second_db, uuid.uuid4(), uuid.uuid4()
    )._source_snapshot(second_document)

    assert isinstance(first_snapshot, SourceSnapshot)
    assert first_snapshot.title == "Captured title A"
    assert first_snapshot.author == "Author A"
    assert first_snapshot.reliability_score == 0.91
    assert first_snapshot.content_hash == first_document.content_hash
    assert first_snapshot.canonical_url == first_document.url
    assert first_snapshot.domain == "example.com"
    assert second_snapshot.title == "Captured title B"
    assert second_snapshot.author == "Author B"
    assert second_snapshot.reliability_score == 0.73
    assert second_snapshot.content_hash == second_document.content_hash
    assert first_snapshot.content_hash != second_snapshot.content_hash


def test_fact_payload_uses_snapshot_metadata_and_preserves_locator_and_hash():
    snapshot = SimpleNamespace(
        id=uuid.uuid4(),
        source_id=uuid.uuid4(),
        canonical_url="https://example.com/original",
        domain="example.com",
        title="Original captured title",
        author="Original author",
        publisher="Original publisher",
        source_type="scientific",
        published_at=CAPTURED_AT,
        accessed_at=CAPTURED_AT,
        content_hash="c" * 64,
        reliability_score=0.97,
        extraction_method="tavily_raw_content",
    )
    fact = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
        research_question_id=uuid.uuid4(),
        claim_text="A reproducible historical claim.",
        exact_quote="Exact quote from the captured body.",
        source_locator="heading: Results, offsets 12-47",
        confidence_score=0.92,
        conflict_group=None,
        approved=True,
    )
    question = SimpleNamespace(question="What did the source report?")

    payload = ResearchLedgerService._fact_dict(fact, snapshot, question)

    assert payload["source"]["title"] == "Original captured title"
    assert payload["source"]["content_hash"] == "c" * 64
    assert payload["source"]["reliability_score"] == 0.97
    assert payload["source_locator"] == "heading: Results, offsets 12-47"
