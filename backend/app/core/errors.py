import re
import traceback
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.exc import DataError

from app.core.sanitization import SanitizationError


PERSISTENCE_INPUT_INVALID = "PERSISTENCE_INPUT_INVALID"
PUBLIC_ERROR_MESSAGE = (
    "Não foi possível concluir esta etapa. "
    "Os detalhes técnicos foram registrados internamente."
)

_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[-_]?key|x-goog-api-key|password|passwd|secret|token|"
    r"cookie|database_url|redis_url|dsn)",
    re.IGNORECASE,
)
_QUERY_SECRET = re.compile(
    r"([?&](?:key|api_key|token|access_token|secret|password)=)[^&#\s]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[^\s,;]+", re.IGNORECASE)
_DSN = re.compile(r"([a-z][a-z0-9+.-]*://[^:/\s]+:)[^@/\s]+@", re.IGNORECASE)
_PARAMETERS = re.compile(r"\[parameters:\s*.*?(?:\]\s*|$)", re.IGNORECASE | re.DOTALL)
_HEADER_SECRET = re.compile(
    r"((?:authorization|x-goog-api-key|x-api-key|cookie)\s*[:=]\s*)[^\s,;}]+",
    re.IGNORECASE,
)
_SQL_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_TECHNICAL_ERROR = re.compile(
    r"(?:traceback|sqlalchemy|asyncpg|insert\s+into|select\s+.+\s+from|"
    r"parameters\s*:|file\s+[\"']?/app/|untranslatablecharactererror)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class PublicError:
    error_code: str
    message: str
    stage: str
    correlation_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "stage": self.stage,
            "correlation_id": self.correlation_id,
        }


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def public_error(
    *, stage: str, error_code: str, correlation_id: str | None = None, message: str | None = None
) -> PublicError:
    return PublicError(
        error_code=error_code,
        message=message or PUBLIC_ERROR_MESSAGE,
        stage=stage,
        correlation_id=correlation_id or new_correlation_id(),
    )


def is_persistence_input_error(error: BaseException) -> bool:
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        name = current.__class__.__name__
        message = str(current).lower()
        if isinstance(current, SanitizationError):
            return True
        if name == "UntranslatableCharacterError":
            return True
        if isinstance(current, DataError) and any(
            marker in message
            for marker in ("unicode", "u0000", "nul", "character", "text")
        ):
            return True
        for nested in (
            current.__cause__,
            current.__context__,
            getattr(current, "orig", None),
        ):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def redact_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            return _QUERY_SECRET.sub(r"\1***", url)
        query = urlencode(
            [
                (key, "***" if _SENSITIVE_KEY.search(key) else value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
            ]
        )
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except ValueError:
        return _QUERY_SECRET.sub(r"\1***", url)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***" if _SENSITIVE_KEY.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    if not isinstance(value, str):
        return value
    redacted = _PARAMETERS.sub("[parameters: ***]", value)
    redacted = _BEARER.sub(r"\1***", redacted)
    redacted = _HEADER_SECRET.sub(r"\1***", redacted)
    redacted = _DSN.sub(r"\1***@", redacted)
    return _QUERY_SECRET.sub(r"\1***", redacted)


def safe_public_message(value: Any) -> str | None:
    if value is None:
        return None
    text = str(redact_sensitive(str(value)))
    return PUBLIC_ERROR_MESSAGE if _TECHNICAL_ERROR.search(text) else text[:1000]


def safe_public_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"traceback", "sql", "statement", "parameters", "headers"}:
                continue
            if lowered in {"message", "error", "reason"}:
                result[key] = safe_public_message(item)
            else:
                result[key] = safe_public_payload(item)
        return result
    if isinstance(value, list):
        return [safe_public_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(safe_public_payload(item) for item in value)
    return redact_sensitive(value)


def safe_exception_details(error: BaseException) -> dict[str, Any]:
    statement = getattr(error, "statement", None)
    operation = None
    template = None
    if statement:
        template = _SQL_STRING_LITERAL.sub("'***'", redact_sensitive(str(statement)))
        operation = template.lstrip().split(None, 1)[0].upper() if template.strip() else None
    formatted = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    safe_traceback = _SQL_STRING_LITERAL.sub("'***'", redact_sensitive(formatted))
    return {
        "exception_type": f"{error.__class__.__module__}.{error.__class__.__name__}",
        "operation": operation,
        "sql_template": template,
        "traceback": safe_traceback,
    }
