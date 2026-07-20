import json
import logging
from datetime import datetime, timezone

from app.core.errors import redact_sensitive, safe_exception_details
from app.core.sanitization import sanitize_nul


logger = logging.getLogger("seo_pipeline")


def structured_log(message: str, level: int = logging.INFO, **context) -> None:
    allowed = {
        "project_id",
        "pipeline_run_id",
        "agent_role",
        "stage",
        "task_id",
        "content_version_id",
        "provider",
        "model",
        "attempt",
        "error_code",
        "error_category",
        "correlation_id",
        "http_status",
        "retryable",
        "latency_ms",
        "retry_delay_ms",
        "source_type",
        "domain",
        "superior_skills_mode",
        "nul_removed_count",
        "escaped_nul_removed_count",
        "sanitized_fields",
    }
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        **{
            key: redact_sensitive(value)
            for key, value in context.items()
            if key in allowed and value is not None
        },
    }
    payload = sanitize_nul(payload)
    logger.log(
        level, json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    )


def structured_exception_log(message: str, error: BaseException, **context) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "message": message,
        **{
            key: redact_sensitive(value)
            for key, value in context.items()
            if value is not None
        },
        **safe_exception_details(error),
    }
    payload = sanitize_nul(payload)
    logger.error(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
