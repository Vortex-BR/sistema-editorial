import uuid
from dataclasses import replace
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FactLedger, ResearchQuestion, Source, SourceSnapshot
from app.services.research_engine import SearchDocument
from app.core.observability import structured_log
from app.core.sanitization import sanitize_nul_with_report


def _bounded_text(value: object, limit: int, *, default: str | None = None):
    text = value.strip() if isinstance(value, str) else ""
    return text[:limit] or default


class ResearchLedgerService:
    def __init__(self, db: AsyncSession, project_id: uuid.UUID, pipeline_run_id: uuid.UUID):
        self.db = db
        self.project_id = project_id
        self.pipeline_run_id = pipeline_run_id

    async def persist_fact(
        self,
        question_id: uuid.UUID,
        document: SearchDocument,
        candidate: dict,
    ) -> FactLedger | None:
        candidate, candidate_report = sanitize_nul_with_report(
            candidate, strip_escaped=True, path="$.fact"
        )
        document = self._sanitize_document(document)
        if candidate_report.nul_removed_count or candidate_report.escaped_nul_removed_count:
            structured_log(
                "fact.sanitized",
                project_id=self.project_id,
                pipeline_run_id=self.pipeline_run_id,
                stage="researcher",
                source_type=document.source_type,
                **candidate_report.as_log_context(),
            )
        source, snapshot = await self._source_snapshot(document)
        existing = await self.db.scalar(
            select(FactLedger).where(
                FactLedger.pipeline_run_id == self.pipeline_run_id,
                FactLedger.source_id == source.id,
                FactLedger.claim_text == candidate["claim_text"],
            )
        )
        if existing:
            return existing
        fact = FactLedger(
            project_id=self.project_id,
            pipeline_run_id=self.pipeline_run_id,
            research_question_id=question_id,
            source_id=source.id,
            source_snapshot_id=snapshot.id,
            claim_text=candidate["claim_text"],
            exact_quote=candidate["exact_quote"],
            source_locator=candidate["source_locator"],
            confidence_score=min(
                float(candidate["confidence_score"]), document.reliability_score
            ),
            conflict_group=candidate.get("conflict_group"),
        )
        self.db.add(fact)
        await self.db.flush()
        return fact

    async def all_facts(self) -> list[dict]:
        rows = (
            await self.db.execute(
                select(FactLedger, SourceSnapshot, ResearchQuestion)
                .join(
                    SourceSnapshot,
                    FactLedger.source_snapshot_id == SourceSnapshot.id,
                )
                .join(
                    ResearchQuestion,
                    FactLedger.research_question_id == ResearchQuestion.id,
                )
                .where(
                    FactLedger.project_id == self.project_id,
                    FactLedger.pipeline_run_id == self.pipeline_run_id,
                )
            )
        ).all()
        return [
            self._fact_dict(fact, snapshot, question)
            for fact, snapshot, question in rows
        ]

    async def approved_facts(self) -> list[dict]:
        return [item for item in await self.all_facts() if item["approved"]]

    async def _source_snapshot(
        self, document: SearchDocument
    ) -> tuple[Source, SourceSnapshot]:
        document = self._sanitize_document(document)
        source = await self.db.scalar(
            select(Source).where(Source.canonical_url == document.url)
        )
        if source is None:
            await self.db.execute(
                pg_insert(Source)
                .values(
                    id=uuid.uuid4(),
                    canonical_url=document.url,
                    title=document.title,
                    publisher=document.publisher,
                    source_type=document.source_type,
                    published_at=document.published_at,
                    accessed_at=document.accessed_at,
                    content_hash=document.content_hash,
                    snapshot_text=document.content,
                    reliability_score=document.reliability_score,
                    metadata_json={},
                )
                .on_conflict_do_nothing(index_elements=[Source.canonical_url])
            )
            await self.db.flush()
            source = await self.db.scalar(
                select(Source).where(Source.canonical_url == document.url)
            )
        snapshot = await self.db.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.pipeline_run_id == self.pipeline_run_id,
                SourceSnapshot.source_id == source.id,
                SourceSnapshot.content_hash == document.content_hash,
            )
        )
        if snapshot is None:
            provenance = {
                "search_markets": (
                    [document.search_market] if document.search_market else []
                ),
                "search_language": document.search_language,
                "source_country": document.source_country,
            }
            snapshot = SourceSnapshot(
                source_id=source.id,
                pipeline_run_id=self.pipeline_run_id,
                content_hash=document.content_hash,
                snapshot_text=document.content,
                accessed_at=document.accessed_at,
                title=document.title,
                author=document.author,
                publisher=document.publisher,
                published_at=document.published_at,
                canonical_url=document.url,
                domain=(urlsplit(document.url).hostname or "unknown").lower(),
                source_type=document.source_type,
                reliability_score=document.reliability_score,
                extraction_method=document.extraction_method,
                metadata_json=provenance,
            )
            self.db.add(snapshot)
            await self.db.flush()
        elif document.search_market:
            metadata = dict(snapshot.metadata_json or {})
            markets = list(metadata.get("search_markets") or [])
            if document.search_market not in markets:
                markets.append(document.search_market)
                metadata["search_markets"] = markets
            metadata.setdefault("search_language", document.search_language)
            metadata.setdefault("source_country", document.source_country)
            snapshot.metadata_json = metadata
        return source, snapshot

    @staticmethod
    def _sanitize_document(document: SearchDocument) -> SearchDocument:
        values, _ = sanitize_nul_with_report(
            {
                "url": document.url,
                "title": document.title,
                "content": document.content,
                "publisher": document.publisher,
                "source_type": document.source_type,
                "author": document.author,
                "extraction_method": document.extraction_method,
                "search_market": document.search_market,
                "search_language": document.search_language,
                "source_country": document.source_country,
            },
            strip_escaped=True,
            path="$.source",
        )
        return replace(
            document,
            url=values["url"],
            title=values["title"],
            content=values["content"],
            publisher=_bounded_text(values["publisher"], 255),
            source_type=_bounded_text(values["source_type"], 50, default="unknown"),
            author=_bounded_text(values["author"], 255),
            extraction_method=_bounded_text(
                values["extraction_method"], 40, default="provider_content"
            ),
            search_market=_bounded_text(values["search_market"], 10),
            search_language=_bounded_text(values["search_language"], 10),
            source_country=_bounded_text(values["source_country"], 10),
        )

    @staticmethod
    def _fact_dict(fact, snapshot, question) -> dict:
        return {
            "id": str(fact.id),
            "project_id": str(fact.project_id),
            "pipeline_run_id": str(fact.pipeline_run_id),
            "research_question_id": str(fact.research_question_id),
            "research_question": question.question,
            "knowledge_node_ids": list(getattr(question, "node_ids", None) or []),
            "claim_text": fact.claim_text,
            "exact_quote": fact.exact_quote,
            "source_locator": fact.source_locator,
            "confidence_score": fact.confidence_score,
            "conflict_group": fact.conflict_group,
            "approved": fact.approved,
            "source": {
                "id": str(snapshot.source_id),
                "url": snapshot.canonical_url,
                "domain": snapshot.domain,
                "title": snapshot.title,
                "author": snapshot.author,
                "publisher": snapshot.publisher,
                "source_type": snapshot.source_type,
                "published_at": (
                    snapshot.published_at.isoformat()
                    if snapshot.published_at is not None
                    else None
                ),
                "snapshot_id": str(snapshot.id),
                "accessed_at": snapshot.accessed_at.isoformat(),
                "content_hash": snapshot.content_hash,
                "reliability_score": snapshot.reliability_score,
                "extraction_method": snapshot.extraction_method,
                "search_markets": list(
                    (getattr(snapshot, "metadata_json", None) or {}).get(
                        "search_markets"
                    )
                    or []
                ),
                "source_country": (
                    getattr(snapshot, "metadata_json", None) or {}
                ).get("source_country"),
            },
        }
