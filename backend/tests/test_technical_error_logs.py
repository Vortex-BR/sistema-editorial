import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.services.technical_error_logs import TechnicalErrorLogService


class Status(str, Enum):
    failed = "failed"


class NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class CapturingDb:
    def __init__(self):
        self.statement = None
        self.committed = False
        self.rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin_nested(self):
        return NestedTransaction()

    async def execute(self, statement):
        self.statement = statement

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_technical_error_log_is_redacted_idempotent_and_json_safe():
    db = CapturingDb()
    project_id = uuid.uuid4()
    run_id = uuid.uuid4()
    correlation_id = str(uuid.uuid4())
    error = IntegrityError(
        "INSERT INTO records (secret) VALUES ('raw-secret')",
        {"password": "raw-password"},
        Exception("duplicate token=raw-token"),
    )

    await TechnicalErrorLogService(db).record(
        project_id=project_id,
        pipeline_run_id=run_id,
        stage="source_reader",
        error=error,
        correlation_id=correlation_id,
        error_code="sqlalchemy.exc.IntegrityError",
        metadata={
            "api_token": "raw-api-token",
            "nested": "password=raw-password",
            "run_id": run_id,
            "failed_at": datetime(2026, 7, 21, 20, 0, tzinfo=timezone.utc),
            "cost": Decimal("0.25"),
            "status": Status.failed,
            "binary": b"not-for-the-log",
        },
    )

    assert db.statement is not None
    compiled = db.statement.compile(dialect=postgresql.dialect())
    params = compiled.params
    sql = str(compiled)

    assert "ON CONFLICT (correlation_id) DO NOTHING" in sql
    assert params["correlation_id"] == correlation_id
    assert params["metadata"]["api_token"] == "***"
    assert params["metadata"]["nested"] == "password=***"
    assert params["metadata"]["run_id"] == str(run_id)
    assert params["metadata"]["failed_at"] == "2026-07-21T20:00:00+00:00"
    assert params["metadata"]["cost"] == "0.25"
    assert params["metadata"]["status"] == "failed"
    assert params["metadata"]["binary"] == "<bytes:15>"
    assert "raw-token" not in params["message"]
    assert "raw-secret" not in (params["sql_template"] or "")
    assert "raw-password" not in (params["traceback"] or "")


@pytest.mark.asyncio
async def test_isolated_error_log_commits_its_own_transaction():
    db = CapturingDb()

    def session_factory():
        return db

    persisted = await TechnicalErrorLogService.record_isolated(
        session_factory=session_factory,
        project_id=uuid.uuid4(),
        pipeline_run_id=uuid.uuid4(),
        stage="writer",
        error=RuntimeError("writer failed"),
        correlation_id=str(uuid.uuid4()),
    )

    assert persisted is True
    assert db.statement is not None
    assert db.committed is True
    assert db.rolled_back is False
