import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api.routes import (
    decide_agent_memory,
    decide_style_pattern,
    decide_style_source,
    list_agent_memories,
    list_style_patterns,
    list_style_sources,
)
from app.db.models import LearningStatus
from app.schemas.api import LearningDecisionWrite


class ScalarRows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ListDb:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def scalars(self, statement):
        self.statements.append(str(statement))
        return ScalarRows(self.rows)


class DecisionDb:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    async def get(self, _model, _row_id):
        return self.row

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_memory_list_filters_project_role_and_status_without_embedding():
    now = datetime.now(timezone.utc)
    project_id = uuid.uuid4()
    row = SimpleNamespace(
        id=uuid.uuid4(),
        agent_role="researcher",
        project_id=project_id,
        niche="testing",
        memory_kind="fact",
        content="conteudo seguro",
        confidence_score=0.9,
        status=LearningStatus.quarantine,
        source_type="human",
        source_id="source-1",
        origin_pipeline_run_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        embedding=[99.0],
        embedding_provider="internal-secret-provider",
    )
    db = ListDb([row])

    response = await list_agent_memories(
        agent_role="researcher",
        memory_status=LearningStatus.quarantine,
        project_id=project_id,
        db=db,
    )

    statement = db.statements[0]
    assert "agent_memories.agent_role" in statement
    assert "agent_memories.status" in statement
    assert "agent_memories.project_id" in statement
    assert response[0]["content"] == "conteudo seguro"
    assert "embedding" not in response[0]
    assert "embedding_provider" not in response[0]


@pytest.mark.asyncio
async def test_style_lists_support_review_filters_and_exclude_internal_fields():
    now = datetime.now(timezone.utc)
    project_id = uuid.uuid4()
    source = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        canonical_url="https://example.com/style",
        title="Fonte",
        publisher="Example",
        domain="example.com",
        status=LearningStatus.quarantine,
        excerpts=["evidencia segura"],
        origin_pipeline_run_id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        content_hash="internal-hash",
        metadata_json={"internal": "do-not-return"},
    )
    source_db = ListDb([source])
    pattern = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=project_id,
        target_agent_role="writer",
        niche="testing",
        pattern_type="structure",
        description="Descricao revisavel",
        source_ids=[str(source.id)],
        independent_domain_count=3,
        validation_count=1,
        status=LearningStatus.quarantine,
        origin_pipeline_run_id=uuid.uuid4(),
        approved_at=None,
        created_at=now,
        updated_at=now,
        embedding=[88.0],
    )
    pattern_db = ListDb([pattern])

    sources = await list_style_sources(
        source_status=LearningStatus.quarantine,
        project_id=project_id,
        db=source_db,
    )
    patterns = await list_style_patterns(
        pattern_status=LearningStatus.quarantine,
        target_agent_role="writer",
        project_id=project_id,
        db=pattern_db,
    )

    assert "style_sources.status" in source_db.statements[0]
    assert "style_sources.project_id" in source_db.statements[0]
    assert sources[0]["excerpts"] == ["evidencia segura"]
    assert "content_hash" not in sources[0]
    assert "metadata_json" not in sources[0]
    assert "style_patterns.status" in pattern_db.statements[0]
    assert "style_patterns.target_agent_role" in pattern_db.statements[0]
    assert "style_patterns.project_id" in pattern_db.statements[0]
    assert patterns[0]["description"] == "Descricao revisavel"
    assert "embedding" not in patterns[0]


@pytest.mark.asyncio
async def test_existing_curation_decisions_commit_expected_statuses():
    memory = SimpleNamespace(status=LearningStatus.quarantine)
    source = SimpleNamespace(status=LearningStatus.quarantine)
    pattern = SimpleNamespace(
        status=LearningStatus.quarantine,
        independent_domain_count=3,
        validation_count=1,
        approved_at=None,
    )
    memory.id = source.id = pattern.id = uuid.uuid4()
    memory_db = DecisionDb(memory)
    source_db = DecisionDb(source)
    pattern_db = DecisionDb(pattern)

    await decide_agent_memory(
        memory.id,
        LearningDecisionWrite(decision="rejected"),
        memory_db,
    )
    await decide_style_source(
        source.id,
        LearningDecisionWrite(decision="archived"),
        source_db,
    )
    await decide_style_pattern(
        pattern.id,
        LearningDecisionWrite(decision="approved"),
        pattern_db,
    )

    assert memory.status is LearningStatus.rejected
    assert source.status is LearningStatus.archived
    assert pattern.status is LearningStatus.approved
    assert pattern.approved_at is not None
    assert memory_db.commits == source_db.commits == pattern_db.commits == 1
