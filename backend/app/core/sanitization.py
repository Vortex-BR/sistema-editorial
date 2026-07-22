import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


_ESCAPED_NUL_RE = re.compile(r"\\+(?:u0000|x00)", re.IGNORECASE)


class SanitizationError(ValueError):
    """Base class for deterministic invalid persistence input."""


class SanitizationKeyCollision(SanitizationError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Sanitized dictionary key collision at {path}")


class UnsanitizedNulError(SanitizationError):
    def __init__(self, path: str):
        self.path = path
        super().__init__(f"Unsanitized NUL detected at {path}")


@dataclass
class SanitizationReport:
    nul_removed_count: int = 0
    escaped_nul_removed_count: int = 0
    sanitized_fields: set[str] = field(default_factory=set)

    def as_log_context(self) -> dict[str, Any]:
        return {
            "nul_removed_count": self.nul_removed_count,
            "escaped_nul_removed_count": self.escaped_nul_removed_count,
            "sanitized_fields": sorted(self.sanitized_fields),
        }


def sanitize_nul(value: Any, *, strip_escaped: bool = False) -> Any:
    sanitized, _ = sanitize_nul_with_report(value, strip_escaped=strip_escaped)
    return sanitized


def sanitize_nul_with_report(
    value: Any, *, strip_escaped: bool = False, path: str = "$"
) -> tuple[Any, SanitizationReport]:
    report = SanitizationReport()
    return _sanitize(value, strip_escaped, path, report), report


def _sanitize(
    value: Any, strip_escaped: bool, path: str, report: SanitizationReport
) -> Any:
    if isinstance(value, str):
        nul_count = value.count("\x00")
        escaped_count = len(_ESCAPED_NUL_RE.findall(value)) if strip_escaped else 0
        if nul_count or escaped_count:
            report.nul_removed_count += nul_count
            report.escaped_nul_removed_count += escaped_count
            report.sanitized_fields.add(path)
        cleaned = value.replace("\x00", "")
        if strip_escaped:
            cleaned = _ESCAPED_NUL_RE.sub("", cleaned)
        return cleaned
    if isinstance(value, dict):
        result = {}
        original_keys: dict[Any, Any] = {}
        for key, item in value.items():
            clean_key = _sanitize(key, strip_escaped, f"{path}.<key>", report)
            key_path = f"{path}.{clean_key}" if isinstance(clean_key, str) else path
            if clean_key in result and original_keys[clean_key] != key:
                raise SanitizationKeyCollision(key_path)
            original_keys[clean_key] = key
            result[clean_key] = _sanitize(item, strip_escaped, key_path, report)
        return result
    if isinstance(value, list):
        return [
            _sanitize(item, strip_escaped, f"{path}[{index}]", report)
            for index, item in enumerate(value)
        ]
    if isinstance(value, tuple):
        return tuple(
            _sanitize(item, strip_escaped, f"{path}[{index}]", report)
            for index, item in enumerate(value)
        )
    return value


def assert_no_nul(value: Any, *, path: str = "$") -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise UnsanitizedNulError(path)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            assert_no_nul(key, path=f"{path}.<key>")
            assert_no_nul(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_nul(item, path=f"{path}[{index}]")


def enum_or_value(value: Any) -> Any:
    """Keep enums untouched while allowing callers to inspect their value."""
    return value.value if isinstance(value, Enum) else value
