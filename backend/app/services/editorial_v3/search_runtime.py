"""Shared budgets and provider circuit breakers for V3.5 source discovery."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchBudgetLedger:
    maximum_logical_queries: int = 36
    maximum_provider_requests: int = 96
    maximum_provider_retries: int = 32
    maximum_result_page_fetches: int = 0
    maximum_estimated_credits: float = 96.0
    timeout_seconds: float = 240.0
    logical_queries: int = 0
    provider_requests: int = 0
    provider_retries: int = 0
    result_page_fetches: int = 0
    estimated_credits: float = 0.0
    started_monotonic: float = field(default_factory=time.monotonic, repr=False)
    persisted_elapsed_seconds: float = 0.0

    @property
    def elapsed_seconds(self) -> float:
        return self.persisted_elapsed_seconds + max(
            0.0, time.monotonic() - self.started_monotonic
        )

    def exhaustion_reason(self, *, include_logical: bool = True) -> str | None:
        if include_logical and self.logical_queries >= self.maximum_logical_queries:
            return "logical_query_limit"
        if self.provider_requests >= self.maximum_provider_requests:
            return "provider_request_limit"
        if self.provider_retries >= self.maximum_provider_retries:
            return "provider_retry_limit"
        if self.result_page_fetches >= self.maximum_result_page_fetches > 0:
            return "result_page_fetch_limit"
        if self.estimated_credits >= self.maximum_estimated_credits:
            return "estimated_credit_limit"
        if self.elapsed_seconds >= self.timeout_seconds:
            return "source_discovery_timeout"
        return None

    def require_capacity(self, *, include_logical: bool = False) -> None:
        reason = self.exhaustion_reason(include_logical=include_logical)
        if reason:
            raise SearchBudgetExhausted(reason)

    def begin_logical_query(self) -> None:
        self.require_capacity(include_logical=True)
        self.logical_queries += 1

    def record_provider_call(
        self,
        *,
        requests: int = 1,
        retries: int = 0,
        result_page_fetches: int = 0,
        estimated_credits: float = 1.0,
    ) -> None:
        self.provider_requests += max(0, int(requests))
        self.provider_retries += max(0, int(retries))
        self.result_page_fetches += max(0, int(result_page_fetches))
        self.estimated_credits += max(0.0, float(estimated_credits))

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("started_monotonic", None)
        payload["elapsed_seconds"] = round(self.elapsed_seconds, 3)
        payload["exhausted_by"] = self.exhaustion_reason(include_logical=True)
        return payload

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None, **defaults: Any) -> "SearchBudgetLedger":
        data = dict(defaults)
        previous = dict(payload or {})
        fields = {
            "maximum_logical_queries",
            "maximum_provider_requests",
            "maximum_provider_retries",
            "maximum_result_page_fetches",
            "maximum_estimated_credits",
            "timeout_seconds",
            "logical_queries",
            "provider_requests",
            "provider_retries",
            "result_page_fetches",
            "estimated_credits",
        }
        data.update({key: value for key, value in previous.items() if key in fields})
        data["persisted_elapsed_seconds"] = float(previous.get("elapsed_seconds") or 0.0)
        return cls(**data)


class SearchBudgetExhausted(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass
class ProviderCircuitState:
    provider: str
    status: str = "closed"
    consecutive_failures: int = 0
    opened_reason: str | None = None
    retry_after_epoch: float | None = None
    last_error_category: str | None = None

    def available(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        if self.status == "closed":
            return True
        if self.status == "open_permanent":
            return False
        if self.retry_after_epoch is not None and now >= self.retry_after_epoch:
            self.status = "half_open"
            return True
        return self.status == "half_open"


class ProviderCircuitBreaker:
    """Prevent repeated calls to a provider that is known to be unavailable."""

    PERMANENT_CATEGORIES = {
        "authentication",
        "invalid_request",
        "model_not_found",
        "permission_denied",
    }
    TRANSIENT_CATEGORIES = {"timeout", "unavailable", "connection", "invalid_output"}

    def __init__(self, states: dict[str, ProviderCircuitState] | None = None):
        self.states = states or {}

    def state(self, provider: str) -> ProviderCircuitState:
        return self.states.setdefault(provider, ProviderCircuitState(provider=provider))

    def allows(self, provider: str) -> bool:
        return self.state(provider).available()

    def record_success(self, provider: str) -> None:
        state = self.state(provider)
        state.status = "closed"
        state.consecutive_failures = 0
        state.opened_reason = None
        state.retry_after_epoch = None
        state.last_error_category = None

    def record_failure(
        self,
        provider: str,
        category: str,
        *,
        retry_after: float | None = None,
    ) -> None:
        state = self.state(provider)
        state.consecutive_failures += 1
        state.last_error_category = category
        if category in self.PERMANENT_CATEGORIES:
            state.status = "open_permanent"
            state.opened_reason = category
            state.retry_after_epoch = None
            return
        if category == "rate_limited":
            state.status = "open_temporary"
            state.opened_reason = category
            state.retry_after_epoch = time.time() + max(60.0, float(retry_after or 60.0))
            return
        if category in self.TRANSIENT_CATEGORIES and state.consecutive_failures >= 2:
            state.status = "open_temporary"
            state.opened_reason = category
            state.retry_after_epoch = time.time() + 60.0

    def all_unavailable(self, providers: list[str]) -> bool:
        return bool(providers) and not any(self.allows(provider) for provider in providers)

    def as_payload(self) -> dict[str, Any]:
        return {provider: asdict(state) for provider, state in self.states.items()}

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "ProviderCircuitBreaker":
        states: dict[str, ProviderCircuitState] = {}
        for provider, raw in dict(payload or {}).items():
            if not isinstance(raw, dict):
                continue
            states[str(provider)] = ProviderCircuitState(
                provider=str(provider),
                status=str(raw.get("status") or "closed"),
                consecutive_failures=int(raw.get("consecutive_failures") or 0),
                opened_reason=str(raw.get("opened_reason") or "") or None,
                retry_after_epoch=(
                    float(raw["retry_after_epoch"])
                    if raw.get("retry_after_epoch") is not None
                    else None
                ),
                last_error_category=str(raw.get("last_error_category") or "") or None,
            )
        return cls(states)
