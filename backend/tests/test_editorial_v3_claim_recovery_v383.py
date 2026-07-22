from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.db.models import V3KnowledgeClaimRecord
from app.schemas.editorial_v3 import (
    ConclusionStatus,
    EvidenceRole,
    SourceAssessment,
    SourceOwnershipType,
    SourcePageType,
    SourceRole,
    SourceUsagePolicy,
)
from app.schemas.editorial_v3_runtime import (
    ExtractedKnowledgeClaimCandidate,
    StructuredDocumentSection,
    StructuredSourceDocument,
)
from app.services.editorial_v3.artifact_repository import V3ArtifactRepository
from app.services.research_engine import SearchDocument


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)


def _assessment(url: str, *, usage: SourceUsagePolicy) -> SourceAssessment:
    authoritative = usage == SourceUsagePolicy.authoritative_evidence
    comparison = usage == SourceUsagePolicy.comparison_only
    return SourceAssessment(
        url=url,
        ownership_type=(
            SourceOwnershipType.ecommerce
            if comparison
            else SourceOwnershipType.academic
        ),
        page_type=(
            SourcePageType.ecommerce_blog_article
            if comparison
            else SourcePageType.research_article
        ),
        source_role=(
            SourceRole.ecommerce_blog
            if comparison
            else SourceRole.scientific_primary
        ),
        usage_policy=usage,
        priority_score=0.9 if authoritative else 0.3,
        eligible_for_primary_evidence=authoritative,
        eligible_for_corroborating_evidence=authoritative,
        eligible_for_external_reference=authoritative,
        counts_toward_independent_source_diversity=authoritative,
        requires_independent_corroboration=comparison,
        minimum_independent_corroborators=2 if comparison else 0,
        absolute_claim_support_allowed=authoritative,
        allowed_evidence_roles=(
            [EvidenceRole.comparison, EvidenceRole.limitation, EvidenceRole.common_error]
            if comparison
            else list(EvidenceRole)
        ),
        reason_codes=["test_source"],
    )


def _structured(url: str, assessment: SourceAssessment) -> StructuredSourceDocument:
    text = (
        "A absorção de água inicia a retomada metabólica da semente e antecede "
        "a emergência da radícula. "
    ) * 8
    return StructuredSourceDocument(
        document_id=uuid4(),
        url=url,
        canonical_url=url,
        title="Fonte científica sobre germinação",
        author="Equipe científica",
        publisher="Instituição científica",
        accessed_at=datetime.now(timezone.utc),
        language="pt-BR",
        document_type=assessment.page_type,
        content_hash="a" * 64,
        sections=[
            StructuredDocumentSection(
                section_id="sec_123456789abc",
                heading_path=["Germinação"],
                paragraphs=[text],
                ordered_steps=[],
                unordered_items=[],
                tables=[],
                source_locator="section:1",
                character_count=len(text),
            )
        ],
        assessment=assessment,
        plain_text=text,
    )


@pytest.mark.asyncio
async def test_claim_reconciles_semantically_equivalent_support_groups():
    run_id = uuid4()
    project_id = uuid4()
    prior = SimpleNamespace(
        support_group="water_metabolic_restart",
        claim_text="A água inicia a retomada metabólica da semente.",
    )
    db = SimpleNamespace(
        scalars=AsyncMock(return_value=_Rows([prior])),
        scalar=AsyncMock(return_value=None),
        add=Mock(),
        flush=AsyncMock(),
    )
    repo = V3ArtifactRepository(db, project_id=project_id, pipeline_run_id=run_id)
    repo.ledger = SimpleNamespace(
        persist_fact=AsyncMock(return_value=SimpleNamespace(id=uuid4()))
    )
    assessment = _assessment(
        "https://science.example/article",
        usage=SourceUsagePolicy.authoritative_evidence,
    )
    structured = _structured("https://science.example/article", assessment)
    source_row = SimpleNamespace(id=structured.document_id)
    source = SearchDocument(
        url=str(structured.canonical_url),
        title=structured.title,
        content=structured.plain_text,
        publisher=structured.publisher,
        source_type=assessment.source_role.value,
        reliability_score=assessment.priority_score,
        accessed_at=structured.accessed_at,
        extraction_method="test",
    )
    candidate = ExtractedKnowledgeClaimCandidate(
        claim_key="water_restarts_seed_metabolism",
        support_group="different_model_slug",
        source_url=structured.canonical_url,
        knowledge_node_id="water_absorption",
        evidence_role=EvidenceRole.mechanism,
        claim_text="A absorção de água inicia a retomada metabólica da semente.",
        exact_quote="A absorção de água inicia a retomada metabólica da semente",
        source_locator="section:1",
        conclusion_status=ConclusionStatus.well_supported,
        confidence_score=0.9,
    )
    diagnostics: dict[str, int] = {}

    row = await repo.claim(
        contract_id=uuid4(),
        candidate=candidate,
        task_question=SimpleNamespace(id=uuid4()),
        source=source,
        structured=structured,
        source_row=source_row,
        diagnostics=diagnostics,
    )

    assert row is not None
    assert row.support_group == "water_metabolic_restart"
    assert diagnostics == {
        "support_group_reconciled": 1,
        "claim_persisted": 1,
    }


@pytest.mark.asyncio
async def test_comparison_only_row_does_not_poison_authoritative_bundle():
    run_id = uuid4()
    project_id = uuid4()
    contract_id = uuid4()
    authoritative_document_id = uuid4()
    comparison_document_id = uuid4()
    authoritative_assessment = _assessment(
        "https://science.example/article",
        usage=SourceUsagePolicy.authoritative_evidence,
    )
    comparison_assessment = _assessment(
        "https://store.example/blog/article",
        usage=SourceUsagePolicy.comparison_only,
    )

    def claim(document_id, key, role):
        return V3KnowledgeClaimRecord(
            id=uuid4(),
            contract_id=contract_id,
            pipeline_run_id=run_id,
            source_document_id=document_id,
            fact_id=None,
            canonical_claim_id=uuid4(),
            claim_key=key,
            support_group="water_metabolic_restart",
            knowledge_node_key="water_absorption",
            evidence_role=role,
            claim_text="A absorção de água inicia a retomada metabólica da semente.",
            exact_quote="A absorção de água inicia a retomada metabólica",
            source_locator="section:1",
            method_ids=[],
            conditions=[],
            applicability=[],
            limitations=[],
            conclusion_status=ConclusionStatus.well_supported.value,
            confidence_score=0.9,
            critical=False,
            conflict_group=None,
            approved=False,
            validation_json={},
        )

    authoritative_claim = claim(
        authoritative_document_id,
        "authoritative_water_claim",
        EvidenceRole.mechanism.value,
    )
    comparison_claim = claim(
        comparison_document_id,
        "commercial_water_claim",
        EvidenceRole.comparison.value,
    )
    documents = [
        SimpleNamespace(
            id=authoritative_document_id,
            canonical_url=str(authoritative_assessment.url),
            usage_policy=authoritative_assessment.usage_policy.value,
            assessment_json=authoritative_assessment.model_dump(mode="json"),
        ),
        SimpleNamespace(
            id=comparison_document_id,
            canonical_url=str(comparison_assessment.url),
            usage_policy=comparison_assessment.usage_policy.value,
            assessment_json=comparison_assessment.model_dump(mode="json"),
        ),
    ]
    db = SimpleNamespace(
        scalars=AsyncMock(side_effect=[_Rows([authoritative_claim, comparison_claim]), _Rows(documents)]),
        get=AsyncMock(return_value=None),
        flush=AsyncMock(),
    )
    repo = V3ArtifactRepository(db, project_id=project_id, pipeline_run_id=run_id)

    approved = await repo.approve_claim_bundles(procedural_context=True)

    assert approved == [authoritative_claim]
    assert authoritative_claim.approved is True
    assert comparison_claim.approved is False
    assert authoritative_claim.validation_json["ignored_non_evidence_claim_count"] == 1
