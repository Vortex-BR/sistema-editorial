import re
from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Article, ArticleVersion
from app.services.embeddings import EmbeddingError, EmbeddingGateway


@dataclass
class SimilarArticle:
    article_id: str
    project_id: str
    title: str
    score: float


class ContentSimilarityService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        embedding_route: dict | None = None,
        route_is_fixed: bool = False,
    ):
        self.db = db
        self.embeddings = EmbeddingGateway()
        self.embedding_route = embedding_route
        self.route_is_fixed = route_is_fixed

    @staticmethod
    def planning_fingerprint(project, plan: dict) -> str:
        return "\n".join(
            [
                project.topic,
                project.search_intent,
                project.audience,
                project.niche or "",
                *plan.get("semantic_keywords", []),
                *plan.get("content_gaps", []),
            ]
        )[:6000]

    @staticmethod
    def final_fingerprint(title: str, markdown: str, metadata: dict) -> str:
        return "\n".join(
            [title, str(metadata.get("focus_keyphrase", "")), markdown]
        )[:6000]

    async def assess(self, project, fingerprint: str) -> list[SimilarArticle]:
        embedding = None
        try:
            embedding = await self.embeddings.embed(
                self.db,
                fingerprint,
                fixed_route=self.embedding_route,
                route_is_fixed=self.route_is_fixed,
            )
        except (EmbeddingError, httpx.HTTPError, KeyError):
            embedding = None
        if embedding:
            distance = Article.content_embedding.cosine_distance(embedding.values).label(
                "distance"
            )
            rows = (
                await self.db.execute(
                    select(Article, ArticleVersion, distance)
                    .join(
                        ArticleVersion,
                        (ArticleVersion.article_id == Article.id)
                        & (ArticleVersion.version == Article.current_version),
                    )
                    .where(
                        Article.project_id != project.id,
                        Article.status == "approved",
                        Article.content_embedding.is_not(None),
                        Article.content_embedding_provider == embedding.provider,
                        Article.content_embedding_model == embedding.model,
                        Article.content_embedding_dimensions == len(embedding.values),
                    )
                    .order_by(distance)
                    .limit(8)
                )
            ).all()
            return [
                SimilarArticle(
                    article_id=str(article.id),
                    project_id=str(article.project_id),
                    title=version.title,
                    score=max(0, min(1, 1 - float(distance_value))),
                )
                for article, version, distance_value in rows
            ]
        rows = (
            await self.db.execute(
                select(Article, ArticleVersion)
                .join(
                    ArticleVersion,
                    (ArticleVersion.article_id == Article.id)
                    & (ArticleVersion.version == Article.current_version),
                )
                .where(
                    Article.project_id != project.id,
                    Article.status == "approved",
                    Article.content_fingerprint.is_not(None),
                )
                .limit(100)
            )
        ).all()
        return sorted(
            [
                SimilarArticle(
                    article_id=str(article.id),
                    project_id=str(article.project_id),
                    title=version.title,
                    score=self._lexical_score(fingerprint, article.content_fingerprint or ""),
                )
                for article, version in rows
            ],
            key=lambda item: item.score,
            reverse=True,
        )[:8]

    async def index_article(self, article: Article, fingerprint: str) -> None:
        article.content_fingerprint = fingerprint
        try:
            embedding = await self.embeddings.embed(
                self.db,
                fingerprint,
                fixed_route=self.embedding_route,
                route_is_fixed=self.route_is_fixed,
            )
        except (EmbeddingError, httpx.HTTPError, KeyError):
            embedding = None
        if embedding:
            article.content_embedding = embedding.values
            article.content_embedding_provider = embedding.provider
            article.content_embedding_model = embedding.model
            article.content_embedding_dimensions = len(embedding.values)

    @staticmethod
    def _lexical_score(left: str, right: str) -> float:
        left_tokens = set(re.findall(r"[\wÀ-ÿ]{3,}", left.casefold()))
        right_tokens = set(re.findall(r"[\wÀ-ÿ]{3,}", right.casefold()))
        if not left_tokens or not right_tokens:
            return 0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
