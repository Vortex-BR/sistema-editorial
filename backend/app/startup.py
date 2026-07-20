from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.core.build_info import BuildInfoError, load_build_info, runtime_identity_gaps
from app.core.observability import structured_log
from app.db.models import (
    Credential,
    ModelRoute,
    SuperiorSkill,
    SuperiorSkillScope,
    SuperiorSkillVersion,
)
from app.services.editorial_roles import ALL_AGENT_ROLES, roles_for_pipeline
from app.services.model_route_bootstrap import sync_missing_model_routes
from app.services.model_route_policy import (
    ModelRoutePolicyError,
    normalize_model_route_configuration,
)
from app.services.skill_sync import sync_default_skills
from app.services.superior_skills import (
    SuperiorSkillDefinition,
    sync_superior_skills,
)


REQUIRED_EDITORIAL_ROLES = tuple(sorted(ALL_AGENT_ROLES))


class ProductionPreflightError(RuntimeError):
    def __init__(self, requirements: list[str] | tuple[str, ...]):
        self.requirements = tuple(dict.fromkeys(requirements))
        names = ", ".join(self.requirements)
        super().__init__(f"Production preflight requirements: {names}")


@dataclass(frozen=True)
class StartupInventory:
    routes: tuple[ModelRoute, ...]
    credentials: tuple[Credential, ...]
    superior_versions: tuple[tuple[SuperiorSkill, SuperiorSkillVersion], ...]


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip()


def production_environment_gaps(config: Settings) -> list[str]:
    if not config.is_production:
        return []

    gaps: list[str] = []
    if not _has_text(config.admin_api_token):
        gaps.append("ADMIN_API_TOKEN")
    try:
        Fernet(config.credential_master_key.encode())
    except (AttributeError, TypeError, ValueError):
        gaps.append("CREDENTIAL_MASTER_KEY")
    if not (
        config.was_explicitly_configured("database_url")
        and _has_text(config.database_url)
    ):
        gaps.append("DATABASE_URL")
    if not (
        config.was_explicitly_configured("redis_url") and _has_text(config.redis_url)
    ):
        gaps.append("REDIS_URL")
    if config.superior_skills_mode != "enforced":
        gaps.append("SUPERIOR_SKILLS_MODE")
    if not _has_text(config.app_commit_sha) or (
        config.app_commit_sha.strip().lower() == "unversioned"
    ):
        gaps.append("APP_COMMIT_SHA")
    if not _has_text(config.app_build_version) or (
        config.app_build_version.strip().lower() == "development"
    ):
        gaps.append("APP_BUILD_VERSION")
    if not _has_text(config.app_source_digest) or (
        config.app_source_digest.strip().lower() == "unversioned"
    ):
        gaps.append("APP_SOURCE_DIGEST")
    try:
        build_info = load_build_info(config, required=True)
    except BuildInfoError:
        gaps.append("BUILD_INFO_FILE")
    else:
        gaps.extend(runtime_identity_gaps(config, build_info))
    return gaps


def _fail(requirements: list[str]) -> None:
    if not requirements:
        return
    structured_log(
        "startup.production_preflight_failed",
        level=logging.ERROR,
        stage="startup",
        source_type="api",
        error_code="PRODUCTION_PREFLIGHT_FAILED",
    )
    raise ProductionPreflightError(requirements)


def validate_production_environment(config: Settings = settings) -> None:
    _fail(production_environment_gaps(config))


def _usable_provider_credentials(
    config: Settings, credentials: tuple[Credential, ...]
) -> set[str]:
    vault = Fernet(config.credential_master_key.encode())
    providers: set[str] = set()
    for credential in credentials:
        if not credential.active:
            continue
        try:
            plaintext = vault.decrypt(bytes(credential.encrypted_value)).decode()
        except (InvalidToken, TypeError, UnicodeDecodeError, ValueError):
            continue
        if plaintext.strip():
            providers.add(_enum_value(credential.provider))
    return providers


def _has_configured_cost_rates(
    parameters: dict[str, object], *, prefix: str = ""
) -> bool:
    input_key = f"{prefix}input_cost_per_million"
    output_key = f"{prefix}output_cost_per_million"
    if input_key not in parameters or output_key not in parameters:
        return False
    try:
        input_rate = float(parameters[input_key])
        output_rate = float(parameters[output_key])
    except (TypeError, ValueError):
        return False
    return input_rate > 0 and output_rate > 0


def _maximum_output_cost(
    parameters: dict[str, object], *, prefix: str = ""
) -> float | None:
    try:
        max_output_tokens = int(parameters.get(f"{prefix}max_output_tokens", 2048))
        output_rate = float(parameters[f"{prefix}output_cost_per_million"])
    except (KeyError, TypeError, ValueError):
        return None
    return max_output_tokens * output_rate / 1_000_000


def _route_gaps_and_providers(
    config: Settings,
    routes: tuple[ModelRoute, ...],
) -> tuple[list[str], set[str]]:
    # model_routes has no enable/disable column: every persisted row is active.
    gaps: list[str] = []
    configured_roles: set[str] = set()
    required_providers: set[str] = set()

    for route in routes:
        role = str(route.agent_role or "").strip()
        try:
            normalized = normalize_model_route_configuration(
                {
                    "agent_role": route.agent_role,
                    "primary_provider": route.primary_provider,
                    "primary_model": route.primary_model,
                    "fallback_provider": route.fallback_provider,
                    "fallback_model": route.fallback_model,
                    "parameters": route.parameters,
                }
            )
        except ModelRoutePolicyError:
            gaps.append(f"MODEL_ROUTE[{role or 'unknown'}]")
            continue
        role = normalized["agent_role"]
        primary_provider = normalized["primary_provider"]
        primary_model = normalized["primary_model"]
        fallback_provider = normalized["fallback_provider"]
        fallback_model = normalized["fallback_model"]
        parameters = normalized["parameters"]
        configured_roles.add(role)
        required_providers.add(primary_provider)
        if not _has_configured_cost_rates(parameters):
            gaps.append(f"MODEL_ROUTE_COST[{role}:primary]")
        else:
            maximum_output_cost = _maximum_output_cost(parameters)
            if (
                maximum_output_cost is None
                or maximum_output_cost > config.max_agent_cost_usd
            ):
                gaps.append(f"MODEL_ROUTE_BUDGET[{role}:primary]")
        if fallback_provider:
            required_providers.add(fallback_provider)
            fallback_is_distinct = (fallback_provider, fallback_model) != (
                primary_provider,
                primary_model,
            )
            if fallback_is_distinct and not _has_configured_cost_rates(
                parameters, prefix="fallback_"
            ):
                gaps.append(f"MODEL_ROUTE_COST[{role}:fallback]")
            elif fallback_is_distinct:
                maximum_output_cost = _maximum_output_cost(
                    parameters, prefix="fallback_"
                )
                if (
                    maximum_output_cost is None
                    or maximum_output_cost > config.max_agent_cost_usd
                ):
                    gaps.append(f"MODEL_ROUTE_BUDGET[{role}:fallback]")

    required_roles = roles_for_pipeline(
        "v3" if config.editorial_pipeline_v3_execution_enabled else "v2"
    )
    for role in required_roles:
        if role not in configured_roles:
            gaps.append(f"MODEL_ROUTE[{role}]")
    return gaps, required_providers


def _usable_superior_scope(
    skill: SuperiorSkill, version: SuperiorSkillVersion
) -> tuple[str, str | None] | None:
    if not skill.enabled:
        return None
    if version.status != "active" or version.version != skill.current_version:
        return None
    if not version.reviewed_by_human or version.approved_at is None:
        return None
    try:
        definition = SuperiorSkillDefinition.model_validate(version.definition)
    except (TypeError, ValidationError, ValueError):
        return None
    scope = _enum_value(skill.scope)
    if (
        definition.skill_id != skill.skill_id
        or definition.version != version.version
        or definition.scope != scope
        or definition.agent_role != skill.agent_role
        or definition.checksum() != version.checksum
    ):
        return None
    return scope, skill.agent_role


def production_inventory_gaps(
    config: Settings, inventory: StartupInventory
) -> list[str]:
    if not config.is_production:
        return []
    environment_gaps = production_environment_gaps(config)
    if environment_gaps:
        return environment_gaps

    gaps, required_providers = _route_gaps_and_providers(config, inventory.routes)
    usable_providers = _usable_provider_credentials(config, inventory.credentials)
    for provider in sorted(required_providers - usable_providers):
        gaps.append(f"PROVIDER_CREDENTIAL[{provider}]")

    usable_roles: set[str] = set()
    global_core_count = 0
    for skill, version in inventory.superior_versions:
        usable = _usable_superior_scope(skill, version)
        if usable is None:
            continue
        scope, role = usable
        if scope == SuperiorSkillScope.global_core.value:
            global_core_count += 1
        elif role:
            usable_roles.add(role)
    if global_core_count != 1:
        gaps.append("SUPERIOR_SKILL[global_core]")
    required_roles = roles_for_pipeline(
        "v3" if config.editorial_pipeline_v3_execution_enabled else "v2"
    )
    for role in required_roles:
        if role not in usable_roles:
            gaps.append(f"SUPERIOR_SKILL[{role}]")
    return gaps


def validate_production_inventory(
    config: Settings, inventory: StartupInventory
) -> None:
    _fail(production_inventory_gaps(config, inventory))


async def load_startup_inventory(db: AsyncSession) -> StartupInventory:
    routes = tuple((await db.scalars(select(ModelRoute))).all())
    credentials = tuple(
        (await db.scalars(select(Credential).where(Credential.active.is_(True)))).all()
    )
    rows = (
        await db.execute(
            select(SuperiorSkill, SuperiorSkillVersion)
            .join(
                SuperiorSkillVersion,
                SuperiorSkillVersion.superior_skill_id == SuperiorSkill.id,
            )
            .where(
                SuperiorSkill.enabled.is_(True),
                SuperiorSkillVersion.version == SuperiorSkill.current_version,
                SuperiorSkillVersion.status == "active",
            )
        )
    ).all()
    superior_versions = tuple((row[0], row[1]) for row in rows)
    return StartupInventory(routes, credentials, superior_versions)


async def initialize_startup(db: AsyncSession, config: Settings = settings) -> None:
    validate_production_environment(config)
    await sync_default_skills(db)
    await sync_superior_skills(db)
    required_roles = roles_for_pipeline(
        "v3" if config.editorial_pipeline_v3_execution_enabled else "v2"
    )
    await sync_missing_model_routes(db, roles=required_roles)
    await db.commit()
    if config.is_production:
        inventory = await load_startup_inventory(db)
        validate_production_inventory(config, inventory)


async def _run_full_preflight() -> None:
    from app.db.session import SessionLocal

    async with SessionLocal() as db:
        await initialize_startup(db, settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate application startup")
    parser.add_argument("--settings-only", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.settings_only:
            validate_production_environment(settings)
        else:
            validate_production_environment(settings)
            from app.workers.async_executor import run_async_task

            run_async_task(_run_full_preflight())
    except ProductionPreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
