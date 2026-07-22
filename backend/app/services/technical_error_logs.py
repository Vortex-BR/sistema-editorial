"""Persistent, redacted technical diagnostics for administrative troubleshooting."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import redact_sensitive, safe_exception_details
from app.core.observability import structured_exception_log
from app.core.sanitization import sanitize_nul
from app.db.models import TechnicalErrorLog


def _bounded(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = str(redact_sensitive(sanitize_nul(value)))
    return text[:limit]


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Convert diagnostic metadata to bounded JSON-compatible values."""
    if depth > 8:
        return "<maximum-depth>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return _json_safe(value.value, depth=depth + 1)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if isinstance(value, dict):
        return {
            str(_bounded(key, 200) or ""): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    return _bounded(value, 1000) or value.__class__.__name__


class TechnicalErrorLogService:
    """Writes diagnostics without allowing log persistence to break the pipeline."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def record(
        self,
        *,
        project_id: uuid.UUID,
        pipeline_run_id: uuid.UUID | None,
        stage: str,
        error: BaseException,
        correlation_id: str,
        error_code: str | None = None,
        error_category: str | None = None,
        retryable: bool = False,
        severity: str = "error",
        agent_run_id: uuid.UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        details = safe_exception_details(error)
        values = {
            "project_id": project_id,
            "pipeline_run_id": pipeline_run_id,
            "agent_run_id": agent_run_id,
            "stage": (stage or "unknown")[:50],
            "severity": severity if severity in {"warning", "error", "critical"} else "error",
            "error_code": _bounded(error_code, 100),
            "error_category": _bounded(error_category, 40),
            "exception_type": _bounded(details.get("exception_type"), 255),
            "message": _bounded(str(error), 8000) or error.__class__.__name__,
            "operation": _bounded(details.get("operation"), 30),
            "sql_template": _bounded(details.get("sql_template"), 30000),
            "traceback": _bounded(details.get("traceback"), 100000),
            "correlation_id": str(correlation_id or uuid.uuid4())[:36],
            "retryable": bool(retryable),
            "metadata_json": redact_sensitive(sanitize_nul(_json_safe(metadata or {}))),
        }
        try:
            # A SAVEPOINT prevents a diagnostics failure from poisoning the
            # transaction that still needs to persist the pipeline failure state.
            async with self.db.begin_nested():
                await self.db.execute(
                    pg_insert(TechnicalErrorLog)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=["correlation_id"])
                )
            return True
        except Exception as log_error:  # pragma: no cover - defensive fallback
            structured_exception_log(
                "technical_error_log.persistence_failed",
                log_error,
                project_id=project_id,
                pipeline_run_id=pipeline_run_id,
                stage=stage,
                correlation_id=correlation_id,
            )
            return False

    @classmethod
    async def record_isolated(
        cls,
        *,
        session_factory=None,
        **values: Any,
    ) -> bool:
        """Persist and commit a diagnostic independently from the failed work."""
        if session_factory is None:
            # Lazy import avoids a model/session import cycle during application startup.
            from app.db.session import SessionLocal

            session_factory = SessionLocal
        try:
            async with session_factory() as db:
                persisted = await cls(db).record(**values)
                if persisted:
                    await db.commit()
                else:
                    await db.rollback()
                return persisted
        except Exception as log_error:  # pragma: no cover - database outage fallback
            structured_exception_log(
                "technical_error_log.isolated_commit_failed",
                log_error,
                project_id=values.get("project_id"),
                pipeline_run_id=values.get("pipeline_run_id"),
                stage=values.get("stage"),
                correlation_id=values.get("correlation_id"),
            )
            return False

