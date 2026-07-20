from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
from app.db.models import (
    Credential,
    CredentialProvider,
    ModelRoute,
    SuperiorSkill,
    SuperiorSkillScope,
    SuperiorSkillVersion,
)
from app.services.editorial_roles import normalize_pipeline_version, roles_for_pipeline
from app.services.model_route_bootstrap import normalized_route_values, sync_missing_model_routes
from app.services.model_route_policy import ModelRoutePolicyError
from app.services.skill_registry import SkillRegistry
from app.services.superior_skills import SuperiorSkillDefinition


@dataclass(frozen=True)
class ExecutionPreflightReport:
    pipeline_version: str
    ready: bool
    gaps: tuple[str, ...]
    repairs: tuple[str, ...]

    def safe_payload(self) -> dict[str, object]:
        return {
            "pipeline_version": self.pipeline_version,
            "status": "ready" if self.ready else "not_ready",
            "dependencies": list(self.gaps),
            "repairs": list(self.repairs),
        }


def _value(value: object) -> str:
    return str(getattr(value, "value", value)).strip().lower()


async def inspect_execution_dependencies(
    db: AsyncSession,
    pipeline_version: object,
    *,
    repair_missing_routes: bool = False,
    config: Settings = settings,
) -> ExecutionPreflightReport:
    version = normalize_pipeline_version(pipeline_version)
    roles = roles_for_pipeline(version)
    repairs: list[str] = []
    gaps: list[str] = []

    if version == "v3":
        if not config.editorial_pipeline_v3_enabled:
            gaps.append("feature_flag:EDITORIAL_PIPELINE_V3_ENABLED")
        if not config.editorial_pipeline_v3_execution_enabled:
            gaps.append("feature_flag:EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED")

    if repair_missing_routes:
        bootstrap = await sync_missing_model_routes(db, roles=roles)
        repairs.extend(f"model_route:{role}" for role in bootstrap.created_roles)
        gaps.extend(bootstrap.invalid_routes)

    routes = list(
        (
            await db.scalars(
                select(ModelRoute)
                .where(ModelRoute.agent_role.in_(roles))
                .order_by(ModelRoute.agent_role)
            )
        ).all()
    )
    route_by_role = {route.agent_role: route for route in routes}
    required_llm_providers: set[str] = set()
    for role in roles:
        route = route_by_role.get(role)
        if route is None:
            gaps.append(f"model_route:{role}")
            continue
        try:
            normalized = normalized_route_values(route)
        except ModelRoutePolicyError as exc:
            gaps.append(f"model_route:{role}:{exc.code}")
            continue
        required_llm_providers.add(str(normalized["primary_provider"]))
        fallback = normalized.get("fallback_provider")
        if fallback:
            required_llm_providers.add(str(fallback))

    verified = {
        _value(provider)
        for provider in await db.scalars(
            select(Credential.provider).where(
                Credential.active.is_(True),
                Credential.verified_at.is_not(None),
            )
        )
    }
    for provider in sorted(required_llm_providers - verified):
        gaps.append(f"credential:llm:{provider}:unverified")
    if not (
        verified
        & {CredentialProvider.tavily.value, CredentialProvider.serper.value}
    ):
        gaps.append("credential:search:unverified")

    rows = (
        await db.execute(
            select(SuperiorSkill, SuperiorSkillVersion)
            .join(
                SuperiorSkillVersion,
                (SuperiorSkillVersion.superior_skill_id == SuperiorSkill.id)
                & (SuperiorSkillVersion.version == SuperiorSkill.current_version),
            )
            .where(
                SuperiorSkill.enabled.is_(True),
                SuperiorSkillVersion.status == "active",
            )
        )
    ).all()
    global_skills = 0
    role_skills: set[str] = set()
    for skill, skill_version in rows:
        try:
            definition = SuperiorSkillDefinition.model_validate(skill_version.definition)
        except (TypeError, ValueError):
            gaps.append(f"super_skill:{skill.skill_id}:invalid")
            continue
        if definition.checksum() != skill_version.checksum:
            gaps.append(f"super_skill:{skill.skill_id}:checksum")
            continue
        if _value(skill.scope) == SuperiorSkillScope.global_core.value:
            global_skills += 1
        elif skill.agent_role:
            role_skills.add(skill.agent_role)
    if global_skills != 1:
        gaps.append("super_skill:global_core")
    for role in roles:
        if role not in role_skills:
            gaps.append(f"super_skill:{role}")

    try:
        defaults = SkillRegistry(config.skills_path).load_defaults()
    except (OSError, TypeError, ValueError):
        defaults = {}
    if not defaults:
        gaps.append("skills:default")
    if version == "v3":
        try:
            v3_defaults = SkillRegistry(
                str(Path(config.skills_path).parent / "v3")
            ).load_defaults()
        except (OSError, TypeError, ValueError):
            v3_defaults = {}
        if not v3_defaults:
            gaps.append("skills:v3")

    normalized = tuple(sorted(set(gaps)))
    return ExecutionPreflightReport(
        pipeline_version=version,
        ready=not normalized,
        gaps=normalized,
        repairs=tuple(sorted(set(repairs))),
    )
