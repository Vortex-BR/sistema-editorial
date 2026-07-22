"""Persistence helpers for canonical Editorial Intelligence snapshots."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EditorialIntelligenceSnapshot,
    V3KnowledgeClaimRecord,
    V3SourceDocumentRecord,
)
from app.schemas.editorial_intelligence import ContentIntelligenceState


def _checksum(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


class EditorialIntelligenceRepository:
    def __init__(self, db: AsyncSession, *, project_id: UUID, pipeline_run_id: UUID):
        self.db = db
        self.project_id = project_id
        self.pipeline_run_id = pipeline_run_id

    async def save(
        self,
        state: ContentIntelligenceState,
        *,
        stage: str,
        status: str,
        validation: dict | None = None,
    ) -> EditorialIntelligenceSnapshot:
        payload = state.model_dump(mode="json")
        checksum = _checksum(payload)
        existing = await self.db.scalar(
            select(EditorialIntelligenceSnapshot).where(
                EditorialIntelligenceSnapshot.pipeline_run_id == self.pipeline_run_id,
                EditorialIntelligenceSnapshot.stage == stage,
                EditorialIntelligenceSnapshot.checksum == checksum,
            )
        )
        if existing is not None:
            return existing
        row = EditorialIntelligenceSnapshot(
            project_id=self.project_id,
            pipeline_run_id=self.pipeline_run_id,
            contract_id=state.contract_id,
            revision=state.revision,
            stage=stage,
            status=status,
            intelligence_version=state.intelligence_version,
            state_json=payload,
            validation_json=validation or {},
            validated_artifact_hash=state.validated_artifact_hash,
            article_version_id=state.article_version_id,
            draft_revision=state.draft_revision,
            checksum=checksum,
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def latest(self) -> ContentIntelligenceState | None:
        row = await self.db.scalar(
            select(EditorialIntelligenceSnapshot)
            .where(EditorialIntelligenceSnapshot.pipeline_run_id == self.pipeline_run_id)
            .order_by(EditorialIntelligenceSnapshot.revision.desc(), EditorialIntelligenceSnapshot.created_at.desc())
            .limit(1)
        )
        if row is None:
            return None
        return ContentIntelligenceState.model_validate(row.state_json)

    async def claim_provenance(self, *, include_graph_eligible: bool = True) -> dict[str, dict]:
        query = select(V3KnowledgeClaimRecord).where(
            V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run_id
        )
        rows = list((await self.db.scalars(query)).all())
        if not include_graph_eligible:
            rows = [row for row in rows if row.approved]
        else:
            rows = [
                row
                for row in rows
                if row.approved
                or row.conclusion_status in {"conditional", "disputed", "insufficient_evidence"}
                or bool(row.conflict_group)
            ]
        source_ids = {row.source_document_id for row in rows}
        source_rows = list(
            (
                await self.db.scalars(
                    select(V3SourceDocumentRecord).where(
                        V3SourceDocumentRecord.id.in_(source_ids)
                    )
                )
            ).all()
        ) if source_ids else []
        sources = {row.id: row for row in source_rows}
        result: dict[str, dict] = {}
        grouped: dict[str, list[V3KnowledgeClaimRecord]] = defaultdict(list)
        for row in rows:
            canonical_id = str(row.canonical_claim_id or row.fact_id or row.id)
            grouped[canonical_id].append(row)
        for canonical_id, claim_rows in grouped.items():
            documents = [sources.get(row.source_document_id) for row in claim_rows]
            documents = [item for item in documents if item is not None]
            payload = {
                "canonical_claim_id": canonical_id,
                "source_document_ids": [str(item.id) for item in documents],
                "source_urls": [item.canonical_url for item in documents],
                "support_groups": list(
                    dict.fromkeys(row.support_group for row in claim_rows if row.support_group)
                ),
                "claim_record_ids": [str(row.id) for row in claim_rows],
                "source_fact_ids": [str(row.fact_id) for row in claim_rows if row.fact_id],
                "approved_record_ids": [str(row.id) for row in claim_rows if row.approved],
            }
            result[canonical_id] = payload
            for row in claim_rows:
                result.setdefault(str(row.id), payload)
                if row.fact_id is not None:
                    result.setdefault(str(row.fact_id), payload)
        return result
