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
