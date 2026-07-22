import math
import re
from collections.abc import Mapping
from typing import Any


SUPPORTED_LLM_PROVIDERS = frozenset({"openai", "anthropic", "gemini"})
MODEL_ROUTE_PARAMETER_ALLOWLIST = {
    "openai": frozenset(
        {
            "temperature",
            "max_output_tokens",
            "timeout_seconds",
            "max_retries",
            "response_format",
            "reasoning_effort",
            "input_cost_per_million",
            "output_cost_per_million",
        }
    ),
    "anthropic": frozenset(
        {
            "temperature",
            "max_output_tokens",
            "timeout_seconds",
            "max_retries",
            "response_format",
            "input_cost_per_million",
            "output_cost_per_million",
        }
    ),
    "gemini": frozenset(
        {
            "temperature",
            "max_output_tokens",
            "timeout_seconds",
            "max_retries",
            "response_format",
            "input_cost_per_million",
            "output_cost_per_million",
        }
    ),
}

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
_OPENAI_REASONING_MODEL = re.compile(r"^(?:gpt-5(?:[.-]|$)|o[134](?:-|$))")
_ANTHROPIC_NO_TEMPERATURE = re.compile(
    r"^claude-(?:(?:opus-4-(?:[7-9]|[1-9]\d)(?:-|$))|"
    r"(?:(?:opus|sonnet|haiku)-(?:[5-9]|[1-9]\d)(?:[.-]|$)))"
)
_PROVIDER_ALIASES = {
    "openai": {"max_completion_tokens": "max_output_tokens"},
    "anthropic": {"max_tokens": "max_output_tokens"},
    "gemini": {},
}
_ALL_PROVIDER_ALIASES = frozenset(
    alias for aliases in _PROVIDER_ALIASES.values() for alias in aliases
)
_TEMPERATURE_LIMIT = {"openai": 2.0, "anthropic": 1.0, "gemini": 2.0}
_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_REQUEST_PARAMETER_KEYS = frozenset(
    {
        "temperature",
        "max_output_tokens",
        "timeout_seconds",
        "max_retries",
        "response_format",
        "reasoning_effort",
    }
)
_COST_PARAMETER_KEYS = frozenset({"input_cost_per_million", "output_cost_per_million"})
_FALLBACK_PARAMETER_SUFFIXES = _REQUEST_PARAMETER_KEYS | _COST_PARAMETER_KEYS


class ModelRoutePolicyError(ValueError):
    public_detail = "Configuração de ModelRoute inválida."

    def __init__(self, code: str):
        self.code = code
        super().__init__(self.public_detail)


def _fail(code: str) -> None:
    raise ModelRoutePolicyError(code)


def _normalize_provider(value: object) -> str:
    if not isinstance(value, str):
        _fail("provider_type")
    provider = value.strip().lower()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        _fail("provider_unsupported")
    return provider


def _normalize_model(value: object) -> str:
    if not isinstance(value, str):
        _fail("model_type")
    model = value.strip()
    if not _MODEL_ID.fullmatch(model):
        _fail("model_invalid")
    return model


def _bounded_number(value: object, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("parameter_type")
    normalized = float(value)
    if not math.isfinite(normalized) or not minimum <= normalized <= maximum:
        _fail("parameter_limit")
    return normalized


def _bounded_integer(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("parameter_type")
    if not minimum <= value <= maximum:
        _fail("parameter_limit")
    return value


def _normalize_parameter_value(key: str, value: object) -> object:
    if key == "temperature":
        return _bounded_number(value, minimum=0, maximum=2)
    if key == "max_output_tokens":
        return _bounded_integer(value, minimum=1, maximum=128_000)
    if key == "timeout_seconds":
        return _bounded_number(value, minimum=1, maximum=300)
    if key == "max_retries":
        return _bounded_integer(value, minimum=0, maximum=2)
    if key in {"input_cost_per_million", "output_cost_per_million"}:
        return _bounded_number(value, minimum=0, maximum=10_000)
    if key == "response_format":
        if not isinstance(value, str) or value.strip().lower() != "json_schema":
            _fail("response_format_invalid")
        return "json_schema"
    if key == "reasoning_effort":
        if not isinstance(value, str):
            _fail("parameter_type")
        normalized = value.strip().lower()
        if normalized not in _REASONING_EFFORTS:
            _fail("reasoning_effort_invalid")
        return normalized
    _fail("parameter_unknown")


def _validate_provider_parameters(
    provider: str, model: str, parameters: Mapping[str, object]
) -> None:
    if parameters.keys() - MODEL_ROUTE_PARAMETER_ALLOWLIST[provider]:
        _fail("parameter_provider_incompatible")
    temperature = parameters.get("temperature")
    if temperature is not None and temperature > _TEMPERATURE_LIMIT[provider]:
        _fail("parameter_provider_limit")
    if provider == "openai" and _OPENAI_REASONING_MODEL.match(model):
        if "temperature" in parameters:
            _fail("temperature_reasoning_incompatible")
    if (
        provider == "anthropic"
        and "temperature" in parameters
        and _ANTHROPIC_NO_TEMPERATURE.match(model)
    ):
        _fail("temperature_model_incompatible")
    if "reasoning_effort" in parameters and not (
        provider == "openai" and _OPENAI_REASONING_MODEL.match(model)
    ):
        _fail("reasoning_model_incompatible")


def _temperature_is_supported(provider: str, model: str) -> bool:
    if provider == "openai" and _OPENAI_REASONING_MODEL.match(model):
        return False
    if provider == "anthropic" and _ANTHROPIC_NO_TEMPERATURE.match(model):
        return False
    return True


def parameters_for_model_target(
    parameters: Mapping[str, object],
    *,
    provider: str,
    model: str,
    target_kind: str,
) -> dict[str, object]:
    """Project a persisted route into provider-safe request parameters."""
    provider = _normalize_provider(provider)
    model = _normalize_model(model)
    if target_kind not in {"primary", "fallback"}:
        _fail("target_kind_invalid")

    projected: dict[str, object] = {}
    if target_kind == "primary":
        for key in _REQUEST_PARAMETER_KEYS:
            if key in parameters:
                projected[key] = parameters[key]
    else:
        # Portable limits and retry controls may be shared. Provider-specific
        # sampling/reasoning controls are inherited only when compatible.
        for key in (
            "max_output_tokens",
            "timeout_seconds",
            "max_retries",
            "response_format",
        ):
            if key in parameters:
                projected[key] = parameters[key]
        if "temperature" in parameters and _temperature_is_supported(provider, model):
            projected["temperature"] = parameters["temperature"]
        for suffix in _REQUEST_PARAMETER_KEYS:
            fallback_key = f"fallback_{suffix}"
            if fallback_key in parameters:
                projected[suffix] = parameters[fallback_key]

    _validate_provider_parameters(provider, model, projected)
    return dict(sorted(projected.items()))


def normalize_model_route_parameters(
    parameters: object,
    *,
    primary_provider: str,
    primary_model: str,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
) -> dict[str, object]:
    primary_provider = _normalize_provider(primary_provider)
    primary_model = _normalize_model(primary_model)
    if (fallback_provider is None) != (fallback_model is None):
        _fail("fallback_incomplete")
    if fallback_provider is not None and fallback_model is not None:
        fallback_provider = _normalize_provider(fallback_provider)
        fallback_model = _normalize_model(fallback_model)
    if not isinstance(parameters, Mapping):
        _fail("parameters_type")

    normalized: dict[str, object] = {}
    for key, value in parameters.items():
        if not isinstance(key, str):
            _fail("parameter_key_type")
        is_fallback = key.startswith("fallback_")
        raw_key = key.removeprefix("fallback_") if is_fallback else key
        if is_fallback and fallback_provider is None:
            _fail("fallback_parameter_without_target")
        provider = fallback_provider if is_fallback else primary_provider
        if provider is None:
            _fail("fallback_incomplete")
        aliases = _PROVIDER_ALIASES[provider]
        if raw_key in _ALL_PROVIDER_ALIASES and raw_key not in aliases:
            _fail("parameter_provider_incompatible")
        canonical_suffix = aliases.get(raw_key, raw_key)
        allowed = (
            _FALLBACK_PARAMETER_SUFFIXES
            if is_fallback
            else MODEL_ROUTE_PARAMETER_ALLOWLIST[primary_provider]
        )
        if canonical_suffix not in allowed:
            _fail("parameter_provider_incompatible")
        canonical_key = (
            f"fallback_{canonical_suffix}" if is_fallback else canonical_suffix
        )
        if canonical_key in normalized:
            _fail("parameter_duplicate")
        normalized[canonical_key] = _normalize_parameter_value(canonical_suffix, value)

    primary_parameters = parameters_for_model_target(
        normalized,
        provider=primary_provider,
        model=primary_model,
        target_kind="primary",
    )
    _validate_provider_parameters(primary_provider, primary_model, primary_parameters)
    if fallback_provider is not None and fallback_model is not None:
        fallback_parameters = parameters_for_model_target(
            normalized,
            provider=fallback_provider,
            model=fallback_model,
            target_kind="fallback",
        )
        _validate_provider_parameters(
            fallback_provider, fallback_model, fallback_parameters
        )
    return dict(sorted(normalized.items()))


def normalize_model_route_configuration(
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    agent_role = configuration.get("agent_role")
    if not isinstance(agent_role, str) or not 1 <= len(agent_role.strip()) <= 50:
        _fail("agent_role_invalid")

    primary_provider = _normalize_provider(configuration.get("primary_provider"))
    primary_model = _normalize_model(configuration.get("primary_model"))
    fallback_provider_value = configuration.get("fallback_provider")
    fallback_model_value = configuration.get("fallback_model")
    if fallback_provider_value is not None and not isinstance(
        fallback_provider_value, str
    ):
        _fail("fallback_provider_type")
    if fallback_model_value is not None and not isinstance(fallback_model_value, str):
        _fail("fallback_model_type")
    has_fallback_provider = isinstance(fallback_provider_value, str) and bool(
        fallback_provider_value.strip()
    )
    has_fallback_model = isinstance(fallback_model_value, str) and bool(
        fallback_model_value.strip()
    )
    if has_fallback_provider != has_fallback_model:
        _fail("fallback_incomplete")

    fallback_provider = (
        _normalize_provider(fallback_provider_value) if has_fallback_provider else None
    )
    fallback_model = (
        _normalize_model(fallback_model_value) if has_fallback_model else None
    )
    parameters = normalize_model_route_parameters(
        configuration.get("parameters", {}),
        primary_provider=primary_provider,
        primary_model=primary_model,
        fallback_provider=fallback_provider,
        fallback_model=fallback_model,
    )
    return {
        "agent_role": agent_role.strip(),
        "primary_provider": primary_provider,
        "primary_model": primary_model,
        "fallback_provider": fallback_provider,
        "fallback_model": fallback_model,
        "parameters": parameters,
    }
