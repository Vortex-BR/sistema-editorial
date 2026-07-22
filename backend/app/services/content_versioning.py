import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Article,
    ArticleBlock,
    ArticleVersion,
    ClaimEvidence,
    PipelineRun,
    SentenceClaim,
)
from app.core.sanitization import sanitize_nul
from app.services.editorial_seal import article_version_checksum


class ContentVersionService:
    """Persists immutable content versions and physical block revisions."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def persist_draft(
        self,
        project,
        pipeline_run: PipelineRun,
        draft: dict,
        agent_run_id: uuid.UUID,
        rewrite_block_ids: set[uuid.UUID] | None = None,
    ) -> ArticleVersion:
        draft = sanitize_nul(draft)
        rewrite_block_ids = rewrite_block_ids or set()
        article = await self.db.scalar(
            select(Article)
            .where(Article.project_id == project.id)
            .with_for_update()
        )
        if article is None:
            article = Article(
                project_id=project.id,
                content_type=project.content_type,
                status="draft",
            )
            self.db.add(article)
            await self.db.flush()

        idempotency_key = f"writer:{agent_run_id}"
        existing = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.idempotency_key == idempotency_key,
            )
        )
        if existing:
            return existing

        prior_blocks: dict[uuid.UUID, ArticleBlock] = {}
        if article.current_version:
            prior_version = await self.db.scalar(
                select(ArticleVersion).where(
                    ArticleVersion.article_id == article.id,
                    ArticleVersion.version == article.current_version,
                )
            )
            if prior_version:
                rows = (
                    await self.db.scalars(
                        select(ArticleBlock).where(
                            ArticleBlock.article_version_id == prior_version.id
                        )
                    )
                ).all()
                prior_blocks = {row.logical_block_id: row for row in rows}

        version_number = article.current_version + 1
        version = ArticleVersion(
            article_id=article.id,
            pipeline_run_id=pipeline_run.id,
            idempotency_key=idempotency_key,
            version=version_number,
            title=draft["title"],
            outline=[
                block["sentences"][0]["text"]
                for block in draft["blocks"]
                if block["type"] in {"h2", "h3"}
            ],
            editorial_status="pending",
            change_reason=f"writer agent run {agent_run_id}",
            final_markdown=None,
            final_html=None,
            seo_metadata={},
            source_report={},
        )
        version.content_checksum = article_version_checksum(version)
        self.db.add(version)
        await self.db.flush()

        for block_data in draft["blocks"]:
            logical_id = uuid.UUID(str(block_data["block_id"]))
            prior = prior_blocks.get(logical_id)
            block = ArticleBlock(
                id=uuid.uuid4(),
                logical_block_id=logical_id,
                replaces_block_id=prior.id if prior else None,
                revision_reason=(
                    "targeted rewrite"
                    if logical_id in rewrite_block_ids
                    else "carried into immutable version" if prior else "initial draft"
                ),
                article_version_id=version.id,
                block_type=block_data["type"],
                position=block_data["position"],
                text=" ".join(item["text"] for item in block_data["sentences"]),
                structured_payload=dict(block_data.get("structured_payload") or {}),
                supported=all(
                    not item.get("is_factual", True) or bool(item.get("evidence"))
                    for item in block_data["sentences"]
                ),
            )
            self.db.add(block)
            await self.db.flush()
            for sentence_position, sentence_data in enumerate(block_data["sentences"]):
                sentence = SentenceClaim(
                    block_id=block.id,
                    position=sentence_position,
                    logical_sentence_id=uuid.UUID(str(sentence_data.get("sentence_id") or uuid.uuid4())),
                    text=sentence_data["text"],
                    is_factual=sentence_data.get("is_factual", True),
                    support_status=(
                        "supported" if sentence_data.get("evidence") else "not_applicable"
                    ),
                )
                self.db.add(sentence)
                await self.db.flush()
                for evidence in sentence_data.get("evidence", []):
                    self.db.add(
                        ClaimEvidence(
                            sentence_claim_id=sentence.id,
                            fact_id=uuid.UUID(str(evidence["fact_id"])),
                            entailment_score=evidence["entailment_score"],
                        )
                    )
        article.current_version = version_number
        article.active_pipeline_run_id = pipeline_run.id
        await self.db.flush()
        return version
