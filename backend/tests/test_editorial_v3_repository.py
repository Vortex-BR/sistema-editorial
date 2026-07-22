import hashlib
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.editorial_v3.contract_repository import KnowledgeContractRepository


def _project():
    return SimpleNamespace(
        id=uuid.uuid4(),
        topic="Tema procedural",
        editorial_pipeline_version="v3",
        briefing={
            "reader_start_state": "Leitor que precisa compreender o tema antes de iniciar.",
            "reader_final_state": "Leitor capaz de reconhecer o resultado final observável.",
            "article_promise": "Explicar fundamentos, alternativas, escolha e execução até o resultado.",
            "scope_limit": "Encerrar no resultado definido sem avançar para a fase seguinte.",
            "editorial_content_type": "procedural_decision_guide",
            "requires_method_comparison": True,
            "requires_external_reference_per_method": True,
            "required_methods": ["método direto", "papel-toalha"],
        },
    )


@pytest.mark.asyncio
async def test_materialize_reactivates_matching_superseded_contract():
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status="superseded",
        pipeline_run_id=None,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=existing),
        execute=AsyncMock(),
        flush=AsyncMock(),
    )
    run_id = uuid.uuid4()

    result = await KnowledgeContractRepository(db).materialize(
        _project(), pipeline_run_id=run_id
    )

    assert result.created is False
    assert result.row is existing
    assert existing.status == "validated"
    assert existing.pipeline_run_id == run_id
    db.execute.assert_awaited_once()
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_materialize_reuses_current_matching_contract_without_writes():
    existing = SimpleNamespace(
        id=uuid.uuid4(),
        status="validated",
        pipeline_run_id=None,
    )
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=existing),
        execute=AsyncMock(),
        flush=AsyncMock(),
    )

    result = await KnowledgeContractRepository(db).materialize(_project())

    assert result.created is False
    assert result.row is existing
    db.execute.assert_not_awaited()
    db.flush.assert_not_awaited()


def _structured_source_document(*, document_id: uuid.UUID | None = None):
    from datetime import datetime, timezone

    from app.schemas.editorial_v3 import (
        EvidenceRole,
        SourceAssessment,
        SourceOwnershipType,
        SourcePageType,
        SourceRole,
        SourceUsagePolicy,
    )
    from app.schemas.editorial_v3_runtime import (
        StructuredDocumentSection,
        StructuredSourceDocument,
    )

    url = "https://example.org/technical/source"
    text = (
        "Esta fonte técnica descreve o processo, as condições observáveis e "
        "as limitações necessárias para sustentar a informação editorial. "
    ) * 8
    assessment = SourceAssessment(
        url=url,
        ownership_type=SourceOwnershipType.public_institution,
        page_type=SourcePageType.technical_guide,
        source_role=SourceRole.institutional,
        usage_policy=SourceUsagePolicy.authoritative_evidence,
        priority_score=0.9,
        eligible_for_primary_evidence=True,
        eligible_for_corroborating_evidence=True,
        eligible_for_external_reference=True,
        counts_toward_independent_source_diversity=True,
        requires_independent_corroboration=False,
        minimum_independent_corroborators=0,
        absolute_claim_support_allowed=True,
        allowed_evidence_roles=[EvidenceRole.definition],
        reason_codes=["test_source"],
    )
    return StructuredSourceDocument(
        document_id=document_id or uuid.uuid4(),
        url=url,
        canonical_url=url,
        title="Fonte técnica institucional",
        author="Equipe técnica",
        publisher="Instituição",
        accessed_at=datetime.now(timezone.utc),
        language="pt-BR",
        document_type=SourcePageType.technical_guide,
        content_hash="a" * 64,
        sections=[
            StructuredDocumentSection(
                section_id="sec_123456789abc",
                heading_path=["Fonte técnica"],
                paragraphs=[text],
                source_locator="section:1",
                character_count=len(text),
            )
        ],
        assessment=assessment,
        source_signals=None,
        plain_text=text,
    )


def test_source_document_record_id_is_run_scoped_and_retry_stable():
    from app.services.editorial_v3.artifact_repository import V3ArtifactRepository

    run_one = uuid.uuid4()
    run_two = uuid.uuid4()
    url_hash = "b" * 64
    content_hash = "c" * 64

    first = V3ArtifactRepository._source_document_record_id(
        run_one, url_hash, content_hash
    )
    retry = V3ArtifactRepository._source_document_record_id(
        run_one, url_hash, content_hash
    )
    another_run = V3ArtifactRepository._source_document_record_id(
        run_two, url_hash, content_hash
    )

    assert first == retry
    assert first != another_run


@pytest.mark.asyncio
async def test_source_document_uses_conflict_safe_run_scoped_id():
    from app.services.editorial_v3.artifact_repository import V3ArtifactRepository

    project_id = uuid.uuid4()
    pipeline_run_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    document = _structured_source_document()
    url_hash = hashlib.sha256(str(document.canonical_url).encode()).hexdigest()
    expected_id = V3ArtifactRepository._source_document_record_id(
        pipeline_run_id, url_hash, document.content_hash
    )
    persisted = SimpleNamespace(id=expected_id)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[None, persisted]),
        execute=AsyncMock(),
        flush=AsyncMock(),
    )
    repo = V3ArtifactRepository(
        db, project_id=project_id, pipeline_run_id=pipeline_run_id
    )

    row = await repo.source_document(contract_id, document)

    assert row is persisted
    db.execute.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    compiled = str(statement.compile()).lower()
    assert "on conflict" in compiled
    assert "uq_v3_source_document_run_url_content" in compiled
    assert persisted.document_json["document_id"] == str(expected_id)
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_source_document_reconciles_legacy_row_id_without_new_insert():
    from app.services.editorial_v3.artifact_repository import V3ArtifactRepository

    legacy_id = uuid.uuid4()
    legacy_row = SimpleNamespace(id=legacy_id)
    db = SimpleNamespace(
        scalar=AsyncMock(return_value=legacy_row),
        execute=AsyncMock(),
        flush=AsyncMock(),
    )
    repo = V3ArtifactRepository(
        db, project_id=uuid.uuid4(), pipeline_run_id=uuid.uuid4()
    )

    row = await repo.source_document(
        uuid.uuid4(), _structured_source_document(document_id=uuid.uuid4())
    )

    assert row is legacy_row
    assert legacy_row.document_json["document_id"] == str(legacy_id)
    db.execute.assert_not_awaited()
    db.flush.assert_awaited_once()
