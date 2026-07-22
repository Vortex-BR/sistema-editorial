"""Persistence for deterministic Editorial V3 source assessments."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ResearchSourceAssessmentRecord
from app.schemas.editorial_v3 import ResearchSourceSignals, SourceAssessment
from app.services.editorial_v3.source_policy import ResearchSourcePolicyService


@dataclass(frozen=True)
class MaterializedSourceAssessment:
    row: ResearchSourceAssessmentRecord
    assessment: SourceAssessment
    created: bool


class SourceAssessmentRepository:
    def __init__(
        self,
        db: AsyncSession,
        *,
        policy: ResearchSourcePolicyService | None = None,
    ):
        self.db = db
        self.policy = policy or ResearchSourcePolicyService()

    TRACKING_QUERY_PREFIXES = ("utm_",)
    TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

    @classmethod
    def canonicalize_url(cls, url: str) -> str:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower() or "https"
        hostname = (parts.hostname or "").lower()
        port = parts.port
        if port and not ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
            netloc = f"{hostname}:{port}"
        else:
            netloc = hostname
        path = parts.path or "/"
        if path != "/":
            path = path.rstrip("/")
        query_items = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            lowered = key.lower()
            if lowered in cls.TRACKING_QUERY_KEYS or any(
                lowered.startswith(prefix) for prefix in cls.TRACKING_QUERY_PREFIXES
            ):
                continue
            query_items.append((key, value))
        query = urlencode(sorted(query_items))
        return urlunsplit((scheme, netloc, path, query, ""))

    @classmethod
    def url_hash(cls, url: str) -> str:
        canonical = cls.canonicalize_url(url)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def materialize(
        self,
        *,
        contract_id: UUID,
        signals: ResearchSourceSignals,
        pipeline_run_id: UUID | None = None,
        source_id: UUID | None = None,
        source_snapshot_id: UUID | None = None,
    ) -> MaterializedSourceAssessment:
        assessment = self.policy.assess(signals)
        canonical_url = self.canonicalize_url(str(assessment.url))
        url_hash = self.url_hash(canonical_url)
        existing = await self.db.scalar(
            select(ResearchSourceAssessmentRecord).where(
                ResearchSourceAssessmentRecord.contract_id == contract_id,
                ResearchSourceAssessmentRecord.url_hash == url_hash,
                ResearchSourceAssessmentRecord.policy_version
                == assessment.policy_version,
            )
        )
        values = self._values(
            assessment,
            signals=signals,
            canonical_url=canonical_url,
            url_hash=url_hash,
            pipeline_run_id=pipeline_run_id,
            source_id=source_id,
            source_snapshot_id=source_snapshot_id,
        )
        if existing is not None:
            for key, value in values.items():
                setattr(existing, key, value)
            await self.db.flush()
            return MaterializedSourceAssessment(existing, assessment, False)

        row = ResearchSourceAssessmentRecord(contract_id=contract_id, **values)
        self.db.add(row)
        await self.db.flush()
        return MaterializedSourceAssessment(row, assessment, True)

    @staticmethod
    def _values(
        assessment: SourceAssessment,
        *,
        signals: ResearchSourceSignals,
        canonical_url: str,
        url_hash: str,
        pipeline_run_id: UUID | None,
        source_id: UUID | None,
        source_snapshot_id: UUID | None,
    ) -> dict:
        return {
            "pipeline_run_id": pipeline_run_id,
            "source_id": source_id,
            "source_snapshot_id": source_snapshot_id,
            "canonical_url": canonical_url,
            "url_hash": url_hash,
            "policy_version": assessment.policy_version,
            "ownership_type": assessment.ownership_type.value,
            "page_type": assessment.page_type.value,
            "source_role": assessment.source_role.value,
            "usage_policy": assessment.usage_policy.value,
            "priority_score": assessment.priority_score,
            "eligible_for_primary_evidence": assessment.eligible_for_primary_evidence,
            "eligible_for_corroborating_evidence": assessment.eligible_for_corroborating_evidence,
            "eligible_for_external_reference": assessment.eligible_for_external_reference,
            "counts_toward_independent_source_diversity": assessment.counts_toward_independent_source_diversity,
            "requires_independent_corroboration": assessment.requires_independent_corroboration,
            "minimum_independent_corroborators": assessment.minimum_independent_corroborators,
            "absolute_claim_support_allowed": assessment.absolute_claim_support_allowed,
            "allowed_evidence_roles": [
                role.value for role in assessment.allowed_evidence_roles
            ],
            "reason_codes": assessment.reason_codes,
            "warnings": assessment.warnings,
            "signals_json": signals.model_dump(mode="json"),
        }
