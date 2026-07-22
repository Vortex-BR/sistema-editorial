import io
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import PurePosixPath
from typing import NoReturn
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from bs4 import BeautifulSoup, Comment
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanitization import sanitize_nul
from app.db.models import (
    Article,
    ArticleVersion,
    FactLedger,
    HumanEditorialReview,
    PipelineRun,
    Project,
    ResearchQuestion,
    SourceSnapshot,
)
from app.services.execution_manifest import (
    ExecutionManifestError,
    ExecutionManifestService,
)
from app.services.editorial_seal import EditorialSealError, validate_review_seal

PACKAGE_REVISION = "2"
VERSION_INCONSISTENT_ERROR = "EDITORIAL_EXPORT_VERSION_INCONSISTENT"
HUMAN_APPROVAL_REQUIRED_ERROR = "HUMAN_EDITORIAL_APPROVAL_REQUIRED"
EXECUTION_MANIFEST_INVALID_ERROR = "EDITORIAL_EXPORT_EXECUTION_MANIFEST_INVALID"
EDITORIAL_SEAL_INVALID_ERROR = "EDITORIAL_EXPORT_SEAL_INVALID"
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_EXPORT_FACTS = 10_000
SAFE_HTML_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "ul",
}
DANGEROUS_HTML_TAGS = {
    "button",
    "embed",
    "form",
    "iframe",
    "input",
    "object",
    "script",
    "style",
    "svg",
    "template",
}
RESERVED_FILENAMES = {
    "aux",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}
SENSITIVE_PATTERNS = (
    re.compile(
        rb"(?i)(?:admin_token|database_url|redis_url|authorization|x-goog-api-key)"
        rb"\s*[:=]\s*[^\s\"',}]+"
    ),
    re.compile(rb"(?i)\bsk-[a-z0-9_-]{16,}\b"),
    re.compile(rb"\bAIza[a-zA-Z0-9_-]{20,}\b"),
    re.compile(rb"(?i)\b(?:postgres(?:ql)?|redis)(?:\+asyncpg)?://[^\s\"']+"),
    re.compile(rb"Traceback \(most recent call last\)"),
)


@dataclass(frozen=True)
class EditorialPackage:
    content: bytes
    filename: str
    root_directory: str


class EditorialExportService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(
        self,
        project_id: uuid.UUID,
        *,
        exported_at: datetime | None = None,
        draft: bool = False,
    ) -> EditorialPackage:
        project = await self.db.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found")

        article = await self.db.scalar(
            select(Article).where(Article.project_id == project_id)
        )
        if article is None:
            raise HTTPException(409, "Article is not available for export")

        version = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
            )
        )
        if (
            version is None
            or version.article_id != article.id
            or version.version != article.current_version
            or version.pipeline_run_id is None
        ):
            self._raise_version_inconsistent()
        if not (version.final_markdown or "").strip():
            raise HTTPException(409, "Article version is not available for export")

        pipeline_run_id = version.pipeline_run_id
        pipeline_run = await self.db.get(PipelineRun, pipeline_run_id)
        if pipeline_run is None or pipeline_run.project_id != project_id:
            self._raise_version_inconsistent()
        human_review = await self.db.scalar(
            select(HumanEditorialReview).where(
                HumanEditorialReview.pipeline_run_id == pipeline_run_id,
                HumanEditorialReview.article_version_id == version.id,
            )
        )
        run_status = getattr(pipeline_run.status, "value", pipeline_run.status)
        human_approved = bool(
            human_review is not None
            and human_review.decision == "approved"
            and (human_review.reviewer or "").strip()
            and human_review.reviewed_at is not None
            and run_status == "completed"
            and version.editorial_status == "human_approved"
            and article.status == "approved"
        )
        if not draft and not human_approved:
            raise HTTPException(
                409,
                {
                    "error_code": HUMAN_APPROVAL_REQUIRED_ERROR,
                    "message": "Human editor-in-chief approval is required",
                },
            )
        try:
            validate_review_seal(version, human_review, require_sealed=True)
        except EditorialSealError as exc:
            if not draft:
                raise HTTPException(
                    409,
                    {
                        "error_code": EDITORIAL_SEAL_INVALID_ERROR,
                        "message": "A valid editorial seal is required for export",
                    },
                ) from exc
            editorial_seal = {"status": "unavailable", "publishable": False}
        else:
            editorial_seal = {
                "status": "ready",
                "publishable": human_approved and not draft,
                "content_checksum": version.content_checksum,
                "review_package_checksum": human_review.review_package_checksum,
                "sealed_at": version.sealed_at,
            }
        manifest_service = ExecutionManifestService(self.db)
        try:
            loaded_manifest = await manifest_service.required(
                pipeline_run_id,
                project_id=project_id,
            )
        except ExecutionManifestError as exc:
            if not draft:
                raise HTTPException(
                    409,
                    {
                        "error_code": EXECUTION_MANIFEST_INVALID_ERROR,
                        "message": (
                            "A valid execution manifest is required for "
                            "publishable export"
                        ),
                    },
                ) from exc
            execution_manifest = {
                "status": "unavailable",
                "error_code": exc.code,
                "message": "Execution manifest is unavailable for this draft",
                "pipeline_run_id": str(pipeline_run_id),
                "publishable": False,
            }
        else:
            execution_manifest = await manifest_service.summary(loaded_manifest)
        facts = await self._load_facts(project_id, pipeline_run_id)
        sources = self._sources_from_facts(facts)
        exported_at = (exported_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        root = safe_slug(project.name, fallback=f"projeto-{str(project.id)[:8]}")
        package_kind = "publishable" if human_approved and not draft else "review_draft"
        publication_status = (
            "human_approved" if package_kind == "publishable" else "not_publishable"
        )
        if package_kind == "review_draft":
            root = f"{root}-rascunho"
        safe_html = sanitize_html(version.final_html) if version.final_html else None
        safe_seo = self._safe_seo_metadata(version.seo_metadata or {})
        safe_report = self._safe_source_report(version.source_report or {})

        article_payload = {
            "package_revision": PACKAGE_REVISION,
            "package_kind": package_kind,
            "publication_status": publication_status,
            "article": {
                "id": article.id,
                "project_id": project.id,
                "current_version": article.current_version,
            },
            "version": {
                "id": version.id,
                "version": version.version,
                "pipeline_run_id": pipeline_run_id,
                "title": version.title,
                "outline": version.outline,
                "editorial_status": version.editorial_status,
                "content_checksum": version.content_checksum,
                "sealed_at": version.sealed_at,
                "created_at": version.created_at,
                "updated_at": version.updated_at,
                "markdown": version.final_markdown,
                "html": safe_html,
                "seo_metadata": safe_seo,
            },
        }
        evidence_payload = {
            "package_revision": PACKAGE_REVISION,
            "package_kind": package_kind,
            "project_id": project.id,
            "pipeline_run_id": pipeline_run_id,
            "facts": facts,
            "audit_report": safe_report,
        }
        metadata_payload = {
            "package_revision": PACKAGE_REVISION,
            "package_kind": package_kind,
            "publication_status": publication_status,
            "exported_at": exported_at,
            "project": {
                "id": project.id,
                "name": project.name,
                "topic": project.topic,
                "language": project.language,
                "content_type": project.content_type,
                "status": project.status,
            },
            "pipeline_run": (
                {
                    "id": pipeline_run.id,
                    "status": pipeline_run.status,
                    "current_stage": pipeline_run.current_stage,
                    "started_at": pipeline_run.started_at,
                    "finished_at": pipeline_run.finished_at,
                }
                if pipeline_run
                else None
            ),
            "article_version_id": version.id,
            "content_version": version.version,
            "editorial_status": version.editorial_status,
            "human_review": (
                {
                    "id": human_review.id,
                    "decision": human_review.decision,
                    "reviewer": human_review.reviewer,
                    "reviewed_at": human_review.reviewed_at,
                    "review_package_checksum": (
                        human_review.review_package_checksum
                    ),
                }
                if human_review is not None
                else None
            ),
            "source_count": len(sources),
            "fact_count": len(facts),
            "approved_fact_count": sum(bool(item["approved"]) for item in facts),
            "editorial_seal": editorial_seal,
            "execution_manifest": {
                "status": execution_manifest.get("status"),
                "error_code": execution_manifest.get("error_code"),
                "id": execution_manifest.get("id"),
                "checksum": execution_manifest.get("checksum"),
                "commit_sha": (execution_manifest.get("build") or {}).get(
                    "commit_sha"
                ),
                "build_version": (execution_manifest.get("build") or {}).get(
                    "build_version"
                ),
            },
        }
        markdown = version.final_markdown
        files: dict[str, bytes] = {
            "artigo.md": self._text_bytes(markdown),
            "artigo.json": self._json_bytes(article_payload),
            "fontes.json": self._json_bytes(
                {"package_revision": PACKAGE_REVISION, "sources": sources}
            ),
            "evidencias.json": self._json_bytes(evidence_payload),
            "metadata.json": self._json_bytes(metadata_payload),
            "manifesto-execucao.json": self._json_bytes(execution_manifest),
        }
        if safe_html:
            files["artigo.html"] = self._text_bytes(safe_html)
        if package_kind == "review_draft":
            files["LEIA-ME-RASCUNHO.txt"] = self._text_bytes(
                "RASCUNHO PARA REVISÃO. NÃO PUBLICAR.\n"
                "Este pacote não é publicável. Consulte metadata.json e "
                "manifesto-execucao.json para o estado da aprovação e do manifesto."
            )

        self._validate_files(files)
        content = self._zip(root, files)
        label = "PUBLICAVEL" if package_kind == "publishable" else "RASCUNHO"
        filename = f"{root}-v{version.version}-{label}-{exported_at:%Y%m%d}.zip"
        return EditorialPackage(content=content, filename=filename, root_directory=root)

    @staticmethod
    def _raise_version_inconsistent() -> NoReturn:
        raise HTTPException(
            409,
            {
                "error_code": VERSION_INCONSISTENT_ERROR,
                "message": "The current editorial version is inconsistent",
            },
        )

    async def _load_facts(
        self, project_id: uuid.UUID, pipeline_run_id: uuid.UUID
    ) -> list[dict]:
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
                    FactLedger.project_id == project_id,
                    FactLedger.pipeline_run_id == pipeline_run_id,
                )
                .order_by(FactLedger.created_at, FactLedger.id)
                .limit(MAX_EXPORT_FACTS + 1)
            )
        ).all()
        if len(rows) > MAX_EXPORT_FACTS:
            raise HTTPException(413, "Editorial package has too many facts")
        return [
            {
                "id": fact.id,
                "research_question_id": fact.research_question_id,
                "research_question": question.question,
                "claim": fact.claim_text,
                "exact_quote": fact.exact_quote,
                "source_locator": fact.source_locator,
                "confidence": fact.confidence_score,
                "approved": fact.approved,
                "approved_by_run_id": fact.approved_by_run_id,
                "conflict_group": fact.conflict_group,
                "superseded_by_id": fact.superseded_by_id,
                "source_id": fact.source_id,
                "source_snapshot_id": snapshot.id,
                "created_at": fact.created_at,
                "source": {
                    "id": fact.source_id,
                    "snapshot_id": snapshot.id,
                    "title": snapshot.title,
                    "url": snapshot.canonical_url,
                    "domain": snapshot.domain,
                    "author": snapshot.author,
                    "publisher": snapshot.publisher,
                    "source_type": snapshot.source_type,
                    "published_at": snapshot.published_at,
                    "captured_at": snapshot.accessed_at,
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
            for fact, snapshot, question in rows
        ]

    @staticmethod
    def _sources_from_facts(facts: list[dict]) -> list[dict]:
        sources = {
            (str(item["source_id"]), str(item["source_snapshot_id"])): item["source"]
            for item in facts
        }
        return [sources[key] for key in sorted(sources)]

    @staticmethod
    def _safe_seo_metadata(metadata: dict) -> dict:
        safe = {
            key: metadata[key]
            for key in (
                "title",
                "meta_description",
                "slug",
                "language",
                "focus_keyphrase",
                "related_keyphrases",
                "title_fact_ids",
                "meta_description_fact_ids",
            )
            if key in metadata
        }
        yoast = metadata.get("yoast_handoff")
        if isinstance(yoast, dict):
            safe["yoast_handoff"] = {
                key: yoast[key]
                for key in (
                    "plugin",
                    "requires_snippet_preview_review",
                    "requires_human_editorial_review",
                    "metrics_are_diagnostic",
                )
                if key in yoast
            }
        return safe

    @staticmethod
    def _safe_source_report(report: dict) -> dict:
        binding_raw = report.get("editorial_intelligence_binding")
        binding = None
        if isinstance(binding_raw, dict):
            binding = {
                key: binding_raw.get(key)
                for key in (
                    "validated_artifact_hash",
                    "article_version_id",
                    "draft_revision",
                )
                if binding_raw.get(key) is not None
            }
        traceability = []
        for block in report.get("traceability", []):
            if not isinstance(block, dict):
                continue
            sentences = []
            for sentence in block.get("sentences", []):
                if isinstance(sentence, dict):
                    sentences.append(
                        {
                            "sentence_id": sentence.get("sentence_id"),
                            "text": sentence.get("text"),
                            "question_ids": sentence.get("question_ids", []),
                            "answer_status": sentence.get("answer_status"),
                            "fact_ids": sentence.get("fact_ids", []),
                        }
                    )
            traceability.append(
                {
                    "block_id": block.get("block_id"),
                    "section_id": block.get("section_id"),
                    "method_id": block.get("method_id"),
                    "sentences": sentences,
                }
            )
        return {
            key: value
            for key, value in {
                "generated_at": report.get("generated_at"),
                "pipeline_contract_version": report.get("pipeline_contract_version"),
                "source_document_count": report.get("source_document_count"),
                "distinct_source_count": report.get("distinct_source_count"),
                "approved_claim_count": report.get("approved_claim_count"),
                "fact_count": report.get("fact_count"),
                "editorial_intelligence_binding": binding,
                "traceability": traceability,
                "title_fact_ids": report.get("title_fact_ids", []),
            }.items()
            if value is not None
        }

    @staticmethod
    def _text_bytes(value: str) -> bytes:
        return (sanitize_nul(value).rstrip() + "\n").encode("utf-8")

    @staticmethod
    def _json_bytes(value: dict) -> bytes:
        clean = sanitize_nul(value)
        return (
            json.dumps(
                clean,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
                default=_json_default,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _validate_files(files: dict[str, bytes]) -> None:
        total = sum(len(content) for content in files.values())
        if total > MAX_UNCOMPRESSED_BYTES:
            raise HTTPException(413, "Editorial package is too large")
        for path, content in files.items():
            safe_path = PurePosixPath(path)
            if safe_path.is_absolute() or ".." in safe_path.parts:
                raise HTTPException(500, "Invalid editorial package path")
            if any(pattern.search(content) for pattern in SENSITIVE_PATTERNS):
                raise HTTPException(422, "Editorial package contains unsafe content")

    @staticmethod
    def _zip(root: str, files: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with ZipFile(buffer, "w") as archive:
            for filename in sorted(files):
                info = ZipInfo(f"{root}/{filename}", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, files[filename])
        return buffer.getvalue()


def safe_slug(value: str, *, fallback: str = "projeto") -> str:
    normalized = unicodedata.normalize("NFKD", sanitize_nul(value, strip_escaped=True))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-.")[:80]
    if not slug or slug in RESERVED_FILENAMES:
        slug = fallback
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-.")
    return slug or "projeto"


def sanitize_html(value: str) -> str:
    soup = BeautifulSoup(sanitize_nul(value), "html.parser")
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for tag in list(soup.find_all(DANGEROUS_HTML_TAGS)):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.name not in SAFE_HTML_TAGS:
            tag.unwrap()
            continue
        if tag.name == "a":
            href = str(tag.get("href", ""))
            parsed = urlsplit(href)
            tag.attrs = {"href": href} if parsed.scheme in {"http", "https"} else {}
        else:
            tag.attrs = {}
    return str(soup).strip()


def _json_default(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, (uuid.UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")
