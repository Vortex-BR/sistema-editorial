from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Credential, CredentialProvider, ModelRoute
from app.services.editorial_roles import ALL_AGENT_ROLES
from app.services.model_catalog import (
    apply_known_model_profile,
    default_openai_route_configuration,
)
from app.services.model_route_policy import (
    ModelRoutePolicyError,
    normalize_model_route_configuration,
)


DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "claude-sonnet-5",
    "gemini": "gemini-3.5-flash",
}

PROVIDER_STANDARD_TOKEN_RATES = {
    "anthropic": {
        "input_cost_per_million": 3.0,
        "output_cost_per_million": 15.0,
    },
    "gemini": {
        "input_cost_per_million": 1.5,
        "output_cost_per_million": 9.0,
    },
}

LLM_PROVIDER_PRIORITY = ("openai", "gemini", "anthropic")

_ROLE_LIMITS = {
    "planner": (4096, 90, 2),
    "researcher": (1536, 60, 1),
    "research_gatekeeper": (2048, 90, 2),
    "writer": (16000, 240, 2),
    "editor": (12000, 180, 2),
    "development_editor": (12000, 180, 2),
    "fact_checker": (12000, 210, 2),
    "language_editor": (12000, 180, 2),
    "skill_curator": (3072, 120, 2),
}


@dataclass(frozen=True)
class ModelRouteBootstrapResult:
    provider: str | None
    created_roles: tuple[str, ...]
    invalid_routes: tuple[str, ...]


def default_route_for_provider(provider: str, role: str) -> dict[str, object]:
    """Return a priced and provider-safe default route for a supported role."""

    provider = provider.strip().lower()
    role = role.strip()
    if role not in ALL_AGENT_ROLES:
        raise ValueError(f"Unsupported editorial role: {role}")
    if provider == "openai":
        return normalize_model_route_configuration(
            default_openai_route_configuration(role)
        )
    if provider not in PROVIDER_STANDARD_TOKEN_RATES:
        raise ValueError(f"Unsupported credential provider: {provider}")

    max_output_tokens, timeout_seconds, max_retries = _ROLE_LIMITS[role]
    return normalize_model_route_configuration(
        {
            "agent_role": role,
            "primary_provider": provider,
            "primary_model": DEFAULT_MODELS[provider],
            "fallback_provider": None,
            "fallback_model": None,
            "parameters": {
                "max_output_tokens": max_output_tokens,
                "timeout_seconds": timeout_seconds,
                "max_retries": max_retries,
                **PROVIDER_STANDARD_TOKEN_RATES[provider],
            },
        }
    )


def normalized_route_values(route: ModelRoute) -> dict[str, Any]:
    configuration = {
        "agent_role": route.agent_role,
        "primary_provider": route.primary_provider,
        "primary_model": route.primary_model,
        "fallback_provider": route.fallback_provider,
        "fallback_model": route.fallback_model,
        "parameters": dict(route.parameters or {}),
    }
    configuration = apply_known_model_profile(configuration)
    return normalize_model_route_configuration(configuration)


async def verified_llm_providers(db: AsyncSession) -> tuple[str, ...]:
    providers = set(
        await db.scalars(
            select(Credential.provider).where(
                Credential.active.is_(True),
                Credential.verified_at.is_not(None),
                Credential.provider.in_(
                    (
                        CredentialProvider.openai,
                        CredentialProvider.gemini,
                        CredentialProvider.anthropic,
                    )
                ),
            )
        )
    )
    names = {getattr(provider, "value", provider) for provider in providers}
    return tuple(provider for provider in LLM_PROVIDER_PRIORITY if provider in names)


async def sync_missing_model_routes(
    db: AsyncSession,
    *,
    roles: tuple[str, ...] | None = None,
) -> ModelRouteBootstrapResult:
    """Add missing routes without overwriting administrator-defined routes."""

    expected = tuple(sorted(set(roles or tuple(ALL_AGENT_ROLES))))
    routes = list((await db.scalars(select(ModelRoute))).all())
    by_role = {route.agent_role: route for route in routes}
    invalid: list[str] = []

    for role, route in by_role.items():
        if role not in expected:
            continue
        try:
            normalized = normalized_route_values(route)
        except ModelRoutePolicyError as exc:
            invalid.append(f"model_route:{role}:{exc.code}")
            continue
        for field in (
            "primary_provider",
            "primary_model",
            "fallback_provider",
            "fallback_model",
            "parameters",
        ):
            setattr(route, field, normalized[field])

    providers = await verified_llm_providers(db)
    provider = providers[0] if providers else None
    created: list[str] = []
    if provider is not None:
        for role in expected:
            if role in by_role:
                continue
            values = default_route_for_provider(provider, role)
            db.add(ModelRoute(**values))
            created.append(role)

    if created or any(role in expected for role in by_role):
        await db.flush()
    return ModelRouteBootstrapResult(
        provider=provider,
        created_roles=tuple(created),
        invalid_routes=tuple(sorted(invalid)),
    )
