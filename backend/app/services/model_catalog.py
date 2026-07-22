"""Known provider model metadata used to keep cost controls trustworthy.

The UI intentionally lets administrators type a model ID.  For models whose
standard rates and limits are known by this build, saving a route refreshes the
cost fields and role-safe execution limits.  This prevents an old model's price
from remaining attached to a newly selected model.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


OPENAI_MODEL_CATALOG: dict[str, dict[str, float | int]] = {
    "gpt-5-mini": {
        "input_cost_per_million": 0.25,
        "output_cost_per_million": 2.0,
        "maximum_output_tokens": 128_000,
    },
    "gpt-5-mini-2025-08-07": {
        "input_cost_per_million": 0.25,
        "output_cost_per_million": 2.0,
        "maximum_output_tokens": 128_000,
    },
    "gpt-5.4-mini": {
        "input_cost_per_million": 0.75,
        "output_cost_per_million": 4.5,
        "maximum_output_tokens": 128_000,
    },
    "gpt-5.4-mini-2026-03-17": {
        "input_cost_per_million": 0.75,
        "output_cost_per_million": 4.5,
        "maximum_output_tokens": 128_000,
    },
    "gpt-5.4": {
        "input_cost_per_million": 2.5,
        "output_cost_per_million": 15.0,
        "maximum_output_tokens": 128_000,
    },
    "gpt-5.4-2026-03-05": {
        "input_cost_per_million": 2.5,
        "output_cost_per_million": 15.0,
        "maximum_output_tokens": 128_000,
    },
}

# These are output ceilings, not output targets.  They provide enough room for
# strict JSON while keeping a single provider attempt compatible with the
# intended US$0.40 per-agent safety ceiling.
OPENAI_ROLE_PROFILES: dict[str, dict[str, Any]] = {
    "planner": {
        "reasoning_effort": "low",
        "max_output_tokens": 4_096,
        "timeout_seconds": 120.0,
        "max_retries": 1,
    },
    "researcher": {
        "reasoning_effort": "low",
        "max_output_tokens": 4_096,
        "timeout_seconds": 120.0,
        "max_retries": 1,
    },
    "research_gatekeeper": {
        "reasoning_effort": "medium",
        "max_output_tokens": 4_096,
        "timeout_seconds": 150.0,
        "max_retries": 1,
    },
    "writer": {
        "reasoning_effort": "low",
        "max_output_tokens": 24_000,
        "timeout_seconds": 240.0,
        "max_retries": 1,
    },
    "editor": {
        "reasoning_effort": "medium",
        "max_output_tokens": 12_000,
        "timeout_seconds": 180.0,
        "max_retries": 1,
    },
    "development_editor": {
        "reasoning_effort": "medium",
        "max_output_tokens": 12_000,
        "timeout_seconds": 180.0,
        "max_retries": 1,
    },
    "fact_checker": {
        "reasoning_effort": "high",
        "max_output_tokens": 12_000,
        "timeout_seconds": 210.0,
        "max_retries": 1,
    },
    "language_editor": {
        "reasoning_effort": "medium",
        "max_output_tokens": 12_000,
        "timeout_seconds": 180.0,
        "max_retries": 1,
    },
    "skill_curator": {
        "reasoning_effort": "low",
        "max_output_tokens": 2_048,
        "timeout_seconds": 90.0,
        "max_retries": 0,
    },
}

OPENAI_DEFAULT_MODELS_BY_ROLE: dict[str, str] = {
    "planner": "gpt-5-mini",
    "researcher": "gpt-5-mini",
    "research_gatekeeper": "gpt-5.4-mini",
    "writer": "gpt-5.4",
    "editor": "gpt-5.4-mini",
    "development_editor": "gpt-5.4-mini",
    "fact_checker": "gpt-5.4",
    "language_editor": "gpt-5.4-mini",
    "skill_curator": "gpt-5-mini",
}


def known_model_profile(provider: str, model: str) -> dict[str, float | int] | None:
    if provider.strip().lower() != "openai":
        return None
    profile = OPENAI_MODEL_CATALOG.get(model.strip())
    return dict(profile) if profile is not None else None


def apply_known_model_profile(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Refresh pricing and safe execution parameters for a known model.

    Unknown/custom models remain fully administrator-managed.  Known OpenAI
    models get canonical rates, so changing only the model ID cannot silently
    retain stale or cheaper pricing from the previous model.
    """

    normalized = dict(configuration)
    provider = str(normalized.get("primary_provider") or "").strip().lower()
    model = str(normalized.get("primary_model") or "").strip()
    role = str(normalized.get("agent_role") or "").strip()
    catalog = known_model_profile(provider, model)
    if catalog is None:
        return normalized

    parameters = dict(normalized.get("parameters") or {})
    parameters.pop("temperature", None)
    role_profile = OPENAI_ROLE_PROFILES.get(role, {})
    parameters.update(role_profile)
    parameters["input_cost_per_million"] = catalog["input_cost_per_million"]
    parameters["output_cost_per_million"] = catalog["output_cost_per_million"]
    if "max_output_tokens" in parameters:
        parameters["max_output_tokens"] = min(
            int(parameters["max_output_tokens"]),
            int(catalog["maximum_output_tokens"]),
        )
    normalized["parameters"] = parameters
    return normalized


def default_openai_route_configuration(role: str) -> dict[str, Any]:
    """Build the priced, role-safe OpenAI route used by production setup."""

    normalized_role = role.strip()
    try:
        model = OPENAI_DEFAULT_MODELS_BY_ROLE[normalized_role]
    except KeyError as exc:
        raise ValueError(f"Unsupported editorial role: {role}") from exc
    return apply_known_model_profile(
        {
            "agent_role": normalized_role,
            "primary_provider": "openai",
            "primary_model": model,
            "fallback_provider": None,
            "fallback_model": None,
            "parameters": {},
        }
    )
