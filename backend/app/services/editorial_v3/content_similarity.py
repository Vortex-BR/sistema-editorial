"""Deterministic duplicate and search-intent collision checks for Editorial V3."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import Select, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Article, ArticleVersion, Project
from app.services.editorial_v3.text_integrity import normalized_text

_STOPWORDS = {
    # Portuguese
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "entre", "é", "na", "nas", "no", "nos", "o", "os", "ou", "para",
    "por", "que", "sem", "se", "um", "uma", "uns", "umas",
    # English
    "a", "an", "and", "as", "at", "by", "for", "from", "how", "in", "of", "on",
    "or", "the", "to", "with",
    # Spanish
    "como", "con", "de", "del", "el", "en", "la", "las", "los", "para", "por", "un", "una", "y",
}


def _ascii(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def _tokens(value: str) -> list[str]:
    normalized = _ascii(normalized_text(value)).casefold()
    return [
        item
        for item in re.findall(r"\b[a-z0-9][a-z0-9'-]*\b", normalized)
        if len(item) > 1 and item not in _STOPWORDS
    ]


def _content_for_comparison(value: str) -> str:
    """Remove reference boilerplate while preserving the substantive article body."""

    lines: list[str] = []
    in_sources = False
    for raw_line in str(value or "").splitlines():
        line = raw_line.strip()
        if re.match(r"^#{1,3}\s+(fontes|sources|referencias|referências)\s*$", line, re.I):
            in_sources = True
            continue
        if in_sources:
            continue
        line = re.sub(r"\[\d+\]", " ", line)
        line = re.sub(r"https?://\S+", " ", line)
        line = re.sub(r"[#>*_`|\[\](){}]", " ", line)
        if line:
            lines.append(line)
    return " ".join(lines)


def shingle_similarity(left: str, right: str, *, size: int = 5) -> float:
    """Return a five-word Jaccard score without trusting model judgement."""

    def shingles(value: str) -> set[tuple[str, ...]]:
        words = _tokens(_content_for_comparison(value))
        if not words:
            return set()
        if len(words) < size:
            return {tuple(words)}
        return {
            tuple(words[index : index + size])
            for index in range(len(words) - size + 1)
        }

    left_items = shingles(left)
    right_items = shingles(right)
    if not left_items or not right_items:
        return 0.0
    return len(left_items & right_items) / len(left_items | right_items)


def keyword_overlap(left: str, right: str) -> float:
    """Symmetric Jaccard overlap for comparing two intents or titles."""

    left_tokens = set(_tokens(left))
    right_tokens = set(_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def keyword_coverage(keyword: str, text: str) -> float:
    """Directional coverage: how much of a requested phrase appears in text.

    A Jaccard score against a full article becomes smaller as the article gets
    longer and therefore cannot be used as a brief-compliance gate.
    """

    required = set(_tokens(keyword))
    available = set(_tokens(text))
    if not required or not available:
        return 0.0
    return len(required & available) / len(required)


def normalized_keyword(value: str) -> str:
    return " ".join(_tokens(value))


def content_fingerprint(value: str) -> str:
    comparable = " ".join(_tokens(_content_for_comparison(value)))
    return hashlib.sha256(comparable.encode("utf-8")).hexdigest()


class V3ContentSimilarityService:
    """Compare a candidate only inside the same publication/content scope."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _scoped_articles_query(self, project: Project, article_id) -> Select:
        query = (
            select(Article, Project)
            .join(Project, Project.id == Article.project_id)
            .where(
                Article.id != article_id,
                Article.final_markdown.is_not(None),
                Article.final_markdown != "",
            )
        )
        if project.publication_profile_id is not None:
            query = query.where(
                Project.publication_profile_id == project.publication_profile_id
            )
        elif str(project.niche or "").strip():
            query = query.where(
                Project.language == project.language,
                Project.niche == project.niche,
            )
        else:
            # Without a publication profile or niche there is no safe tenant/site
            # boundary. Do not compare unrelated customers or brands.
            query = query.where(Project.id == project.id)
        return query.order_by(desc(Article.updated_at)).limit(250)

    async def evaluate(
        self,
        *,
        project: Project,
        article: Article,
        article_version_id,
        candidate_markdown: str,
        candidate_title: str,
        primary_keyword: str,
        duplicate_block_threshold: float,
        duplicate_warning_threshold: float,
    ) -> dict[str, Any]:
        candidate_scope = " ".join(
            item for item in [primary_keyword, project.topic, candidate_title] if item
        )
        current_keyword = normalized_keyword(primary_keyword or project.topic)
        comparisons: list[dict[str, Any]] = []
        blockers: list[str] = []
        warnings: list[str] = []
        scope_limited = bool(
            project.publication_profile_id is None
            and not str(project.niche or "").strip()
        )
        if scope_limited:
            warnings.append(
                "A verificação de canibalização ficou limitada ao artigo atual porque "
                "o projeto não possui perfil de publicação nem nicho definido."
            )

        rows = list(
            (
                await self.db.execute(
                    self._scoped_articles_query(project, article.id)
                )
            ).all()
        )
        for other_article, other_project in rows:
            other_markdown = str(other_article.final_markdown or "")
            other_title = str(
                (other_article.seo_metadata or {}).get("title")
                or other_project.topic
                or ""
            )
            other_brief = dict(other_project.briefing or {})
            other_primary = str(other_brief.get("primary_keyword") or other_project.topic or "")
            body_score = shingle_similarity(candidate_markdown, other_markdown)
            intent_score = keyword_overlap(
                candidate_scope,
                " ".join([other_primary, other_project.topic or "", other_title]),
            )
            exact_keyword_collision = bool(
                current_keyword
                and normalized_keyword(other_primary) == current_keyword
            )
            title_score = SequenceMatcher(
                None,
                normalized_keyword(candidate_title),
                normalized_keyword(other_title),
            ).ratio()
            if max(body_score, intent_score, title_score) < 0.35 and not exact_keyword_collision:
                continue
            comparisons.append(
                {
                    "article_id": str(other_article.id),
                    "project_id": str(other_project.id),
                    "project_name": other_project.name,
                    "topic": other_project.topic,
                    "title": other_title,
                    "primary_keyword": other_primary,
                    "body_similarity": round(body_score, 4),
                    "intent_overlap": round(intent_score, 4),
                    "title_similarity": round(title_score, 4),
                    "exact_primary_keyword_collision": exact_keyword_collision,
                }
            )
            if body_score >= duplicate_block_threshold:
                blockers.append(
                    "Conteúdo materialmente duplicado de outro artigo do mesmo escopo "
                    f"editorial ({body_score:.2f} >= {duplicate_block_threshold:.2f}; "
                    f"projeto: {other_project.name})."
                )
            elif body_score >= duplicate_warning_threshold:
                warnings.append(
                    "Alta similaridade textual com outro artigo do mesmo escopo "
                    f"({body_score:.2f}; projeto: {other_project.name})."
                )

            # Exact focus-keyphrase collisions are a hard SEO conflict when the
            # articles are distinct. Near collisions remain visible as warnings.
            if exact_keyword_collision:
                blockers.append(
                    "Canibalização de palavra-chave principal: outro artigo do mesmo "
                    f"escopo já usa '{primary_keyword or project.topic}' "
                    f"(projeto: {other_project.name})."
                )
            elif intent_score >= 0.82 and title_score >= 0.72:
                warnings.append(
                    "Possível canibalização de intenção: tópico e título são muito "
                    f"próximos de '{other_title}' ({intent_score:.2f})."
                )

        # Prior versions of the same article are informative, but should not block
        # a legitimate rewrite. A near-identical regeneration is surfaced as a warning.
        previous_versions = list(
            (
                await self.db.scalars(
                    select(ArticleVersion)
                    .where(
                        ArticleVersion.article_id == article.id,
                        ArticleVersion.id != article_version_id,
                        ArticleVersion.final_markdown.is_not(None),
                        ArticleVersion.final_markdown != "",
                    )
                    .order_by(desc(ArticleVersion.version))
                    .limit(10)
                )
            ).all()
        )
        prior_version_scores = []
        for previous in previous_versions:
            score = shingle_similarity(candidate_markdown, previous.final_markdown or "")
            prior_version_scores.append(
                {"version": previous.version, "similarity": round(score, 4)}
            )
        if prior_version_scores and prior_version_scores[0]["similarity"] >= duplicate_block_threshold:
            warnings.append(
                "A nova versão é quase idêntica à versão anterior do mesmo artigo; "
                "confirme se a regeneração produziu ganho editorial real."
            )

        comparisons.sort(
            key=lambda item: (
                item["body_similarity"],
                item["intent_overlap"],
                item["title_similarity"],
            ),
            reverse=True,
        )
        return {
            "version": "content-similarity.v3.5.1",
            "scope": (
                "publication_profile"
                if project.publication_profile_id is not None
                else "niche_and_language"
                if str(project.niche or "").strip()
                else "current_project_only"
            ),
            "candidate_fingerprint": content_fingerprint(candidate_markdown),
            "duplicate_block_threshold": duplicate_block_threshold,
            "duplicate_warning_threshold": duplicate_warning_threshold,
            "comparison_count": len(rows),
            "scope_limited": scope_limited,
            "material_comparisons": comparisons[:25],
            "prior_version_scores": prior_version_scores,
            "blockers": list(dict.fromkeys(blockers)),
            "warnings": list(dict.fromkeys(warnings)),
        }
