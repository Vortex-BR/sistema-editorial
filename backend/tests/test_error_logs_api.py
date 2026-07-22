import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.routes import project_error_logs
from app.db.models import Project


NOW = datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc)
PROJECT_ID = uuid.uuid4()
RUN_ID = uuid.uuid4()


class Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class ErrorLogDb:
    def __init__(self):
        self.project = SimpleNamespace(id=PROJECT_ID)
        self.run = SimpleNamespace(
            id=RUN_ID,
            project_id=PROJECT_ID,
            status="failed",
            current_stage="source_reader",
            attempt=1,
            retryable=False,
            next_retry_at=None,
            last_successful_checkpoint="source_discovery",
            error_code="sqlalchemy.exc.IntegrityError",
            error_message="Não foi possível concluir esta etapa.",
            created_at=NOW - timedelta(seconds=3),
            failed_at=NOW,
            finished_at=NOW,
        )
        self.technical = SimpleNamespace(
            id=uuid.uuid4(),
            severity="critical",
            created_at=NOW,
            stage="source_reader",
            exception_type="sqlalchemy.exc.IntegrityError",
            message="database postgresql://user:super-secret@postgres/db failed",
            error_code="sqlalchemy.exc.IntegrityError",
            error_category="persistence",
            correlation_id=str(uuid.uuid4()),
            retryable=False,
            metadata_json={"run_attempt": 1, "api_token": "must-not-leak"},
            operation="INSERT",
            sql_template="INSERT INTO v3_source_documents (...) VALUES (...) ",
            traceback="Traceback sanitizado",
        )
        self.agent = SimpleNamespace(
            id=uuid.uuid4(),
            pipeline_run_id=RUN_ID,
            project_id=PROJECT_ID,
            agent_role="source_reader",
            status="failed",
            error="provider failed",
            error_code="provider_timeout",
            error_category="timeout",
            correlation_id=str(uuid.uuid4()),
            retryable=True,
            recovered=False,
            http_status=504,
            provider="gemini",
            model="gemini-test",
            attempt=2,
            fallback_used=True,
            recovery_code=None,
            latency_ms=1250,
            created_at=NOW - timedelta(seconds=2),
            started_at=NOW - timedelta(seconds=4),
            finished_at=NOW - timedelta(seconds=2),
        )
        self.provider = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=PROJECT_ID,
            pipeline_run_id=RUN_ID,
            agent_run_id=self.agent.id,
            provider="gemini",
            model="gemini-test",
            target_kind="primary",
            run_attempt=1,
            attempt_number=2,
            status="failed",
            response_received=False,
            latency_ms=1200,
            http_status=504,
            error_code="provider_timeout",
            error_category="timeout",
            started_at=NOW - timedelta(seconds=4),
            finished_at=NOW - timedelta(seconds=2),
        )
        self.event = SimpleNamespace(
            id=uuid.uuid4(),
            project_id=PROJECT_ID,
            pipeline_run_id=RUN_ID,
            event_type="pipeline.failed",
            stage="source_reader",
            stage_attempt=1,
            run_attempt=1,
            created_at=NOW - timedelta(seconds=1),
            payload={
                "message": "Falha persistida",
                "error_code": "sqlalchemy.exc.IntegrityError",
                "correlation_id": self.technical.correlation_id,
            },
        )

    async def get(self, model, identifier):
        if model is Project and identifier == PROJECT_ID:
            return self.project
        return None

    async def scalar(self, query):
        if "FROM pipeline_runs" in str(query):
            return self.run
        return None

    async def scalars(self, query):
        sql = str(query)
        if "FROM technical_error_logs" in sql:
            return Rows([self.technical])
        if "FROM pipeline_runs" in sql:
            return Rows([self.run])
        if "FROM agent_runs" in sql:
            return Rows([self.agent])
        if "FROM provider_attempts" in sql:
            return Rows([self.provider])
        if "FROM pipeline_events" in sql:
            return Rows([self.event])
        raise AssertionError(f"Unexpected query: {sql}")


@pytest.mark.asyncio
async def test_error_log_endpoint_aggregates_and_redacts_diagnostics():
    db = ErrorLogDb()

    result = await project_error_logs(
        PROJECT_ID,
        pipeline_run_id=RUN_ID,
        limit=20,
        db=db,
    )

    assert result["project_id"] == PROJECT_ID
    assert result["pipeline_run_id"] == RUN_ID
    assert result["total"] == 5
    assert result["summary"] == {
        "critical": 3,
        "error": 2,
        "warning": 0,
        "retryable": 2,
        "recovered": 0,
    }
    assert result["logs"][0]["source"] == "internal"
    assert "super-secret" not in result["logs"][0]["message"]
    assert "***" in result["logs"][0]["message"]
    assert result["logs"][0]["metadata"]["api_token"] == "***"
    assert {item["source"] for item in result["logs"]} == {
        "internal",
        "pipeline",
        "agent",
        "provider",
        "event",
    }


@pytest.mark.asyncio
async def test_error_log_endpoint_rejects_invalid_limit_before_queries():
    db = ErrorLogDb()

    with pytest.raises(HTTPException) as raised:
        await project_error_logs(PROJECT_ID, limit=0, db=db)

    assert raised.value.status_code == 422


@pytest.mark.asyncio
async def test_error_log_endpoint_rejects_run_from_another_project():
    db = ErrorLogDb()

    async def missing_run(_query):
        return None

    db.scalar = missing_run
    with pytest.raises(HTTPException) as raised:
        await project_error_logs(PROJECT_ID, pipeline_run_id=RUN_ID, db=db)

    assert raised.value.status_code == 404
