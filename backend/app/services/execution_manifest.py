import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.build_info import load_build_info
from app.core.sanitization import sanitize_nul
from app.db.models import (
    AgentHandoff,
    AgentMemory,
    Credential,
    CredentialProvider,
    EmbeddingRoute,
    ExecutionManifest,
    LearningStatus,
    ModelRoute,
    PipelineRun,
    Project,
    PublicationProfile,
    SourceSnapshot,
    StylePattern,
    SuperiorSkill,
    SuperiorSkillScope,
    SuperiorSkillVersion,
)
from app.schemas.agents import (
    CuratorOutput,
    EditorOutput,
    FactExtractionOutput,
    ResearchAuditOutput,
    ResearchPlanOutput,
    WriterOutput,
)
from app.schemas.editorial_v3_runtime import (
    ApproachTaxonomyValidationOutput,
    KnowledgeClaimExtractionOutput,
    KnowledgeSynthesisOutput,
    MethodInventoryOutput,
    V3BlockRevisionOutput,
    V3DevelopmentReview,
    V3FactCheckReview,
    V3LanguageReview,
    V3WriterOutput,
    V3WriterSectionOutput,
)
from app.services.learned_skills import LearnedSkillResolver
from app.services.editorial_roles import roles_for_pipeline
from app.services.model_route_policy import (
    ModelRoutePolicyError,
    normalize_model_route_configuration,
)
from app.services.quality_evaluator import quality_rubric_manifest
from app.services.search_policy import search_policy_manifest
from app.services.skill_registry import SkillDefinition, SkillRegistry
from app.services.superior_skills import SuperiorSkillDefinition


MANIFEST_FORMAT_VERSION = 1
PROMPT_VERSIONS = {
    "planner": "planner.prompt.v5",
    "researcher": "researcher.prompt.v7",
    "research_gatekeeper": "research-gatekeeper.deterministic.v2",
    "writer": "writer.prompt.v12",
    "editor": "editor.prompt.v6",
    "development_editor": "editorial-v3.development-editor.v3",
    "fact_checker": "editorial-v3.fact-checker.v2",
    "language_editor": "editorial-v3.language-editor.v3",
    "skill_curator": "skill-curator.prompt.v2",
    "finalizer": "deterministic-finalizer.v3",
}
CONTRACTS = {
    "planner": ResearchPlanOutput,
    "researcher": FactExtractionOutput,
    "research_gatekeeper": ResearchAuditOutput,
    "writer": WriterOutput,
    "editor": EditorOutput,
    "development_editor": V3DevelopmentReview,
    "fact_checker": V3FactCheckReview,
    "language_editor": V3LanguageReview,
    "skill_curator": CuratorOutput,
}
V3_PROMPT_VERSIONS = {
    "approach_taxonomy": "editorial-v3.approach-taxonomy.v1",
    "claim_extraction": "editorial-v3.claim-extraction.v1",
    "method_inventory": "editorial-v3.method-inventory.v2",
    "knowledge_synthesis": "editorial-v3.knowledge-synthesis.v2",
    "writer": "editorial-v3.writer.v5",
    "writer_section": "editorial-v3.writer-section.v1",
    "writer_section_repair": "editorial-v3.writer-section-repair.v1",
    "writer_repair": "editorial-v3.writer-repair.v4",
    "development_editor": "editorial-v3.development-editor.v3",
    "fact_checker": "editorial-v3.fact-checker.v2",
    "language_editor": "editorial-v3.language-editor.v3",
    "block_revision": "editorial-v3.block-revision.v3",
    "quality_gate": "quality-rubric.procedural-guide.v3.5.1",
}
V3_CONTRACTS = {
    "approach_taxonomy": ApproachTaxonomyValidationOutput,
    "claim_extraction": KnowledgeClaimExtractionOutput,
    "method_inventory": MethodInventoryOutput,
    "knowledge_synthesis": KnowledgeSynthesisOutput,
    "writer": V3WriterOutput,
    "writer_section": V3WriterSectionOutput,
    "writer_section_repair": V3WriterSectionOutput,
    "writer_repair": V3WriterOutput,
    "development_editor": V3DevelopmentReview,
    "fact_checker": V3FactCheckReview,
    "language_editor": V3LanguageReview,
    "block_revision": V3BlockRevisionOutput,
}


def v3_prompt_contract_manifest() -> dict[str, dict[str, str]]:
    result = {
        name: {
            "prompt_version": V3_PROMPT_VERSIONS[name],
            "contract": schema.__name__,
            "contract_checksum": _checksum(schema.model_json_schema()),
        }
        for name, schema in V3_CONTRACTS.items()
    }
    result["quality_gate"] = {
        "prompt_version": V3_PROMPT_VERSIONS["quality_gate"],
        "contract": "ProceduralQualityEvaluation",
        "contract_checksum": _checksum({
            "minimum_overall": 0.85,
            "minimum_axis": 0.70,
            "blocked_score_cap": 0.59,
            "commercial_sources_are_authority": False,
            "method_overview_precedes_shared_conditions": True,
            "summary_like_body_is_blocking": True,
            "opening_numeric_dump_is_blocking": True,
        }),
    }
    return result


_SENSITIVE_KEY_EXACT = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "credential_value",
    "database_url",
    "encrypted",
    "encrypted_value",
    "password",
    "redis_url",
    "secret",
    "secret_value",
    "token",
}
_SENSITIVE_KEY_SUFFIXES = (
    "_api_key",
    "_access_token",
    "_refresh_token",
    "_auth_token",
    "_password",
    "_secret",
    "_secret_value",
    "_credential_value",
)


def _is_sensitive_manifest_key(value: object) -> bool:
    key = re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")
    return key in _SENSITIVE_KEY_EXACT or key.endswith(_SENSITIVE_KEY_SUFFIXES)
_SENSITIVE_VALUE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{12,}|"
    r"\bsk-[a-z0-9_-]{16,}\b|\bAIza[a-zA-Z0-9_-]{20,}\b|"
    r"(?:postgres(?:ql)?|redis)(?:\+asyncpg)?://[^\s]+)"
)


class ExecutionManifestError(RuntimeError):
    code = "EXECUTION_MANIFEST_INVALID"

    def __init__(self, message: str, *, dependencies: tuple[str, ...] | list[str] = ()):
        self.dependencies = tuple(str(item) for item in dependencies if str(item))
        super().__init__(message)


class ExecutionManifestUnavailable(ExecutionManifestError):
    code = "EXECUTION_MANIFEST_UNAVAILABLE"


class ExecutionManifestDrift(ExecutionManifestError):
    code = "EXECUTION_MANIFEST_DRIFT"


class ExecutionManifestContainsSecret(ExecutionManifestError):
    code = "EXECUTION_MANIFEST_CONTAINS_SECRET"


@dataclass(frozen=True)
class LoadedExecutionManifest:
    row: ExecutionManifest
    data: dict[str, Any]

    @property
    def checksum(self) -> str:
        return self.row.checksum


def _normalize_manifest_route(
    configuration: dict[str, Any], *, role: str | None = None
) -> dict[str, Any]:
    try:
        return normalize_model_route_configuration(configuration)
    except ModelRoutePolicyError as exc:
        dependency = f"model_route:{role or 'unknown'}:{exc.code}"
        raise ExecutionManifestUnavailable(
            "Model route configuration is invalid",
            dependencies=(dependency,),
        ) from exc


def _model_route_manifest(route: ModelRoute) -> dict[str, Any]:
    normalized = _normalize_manifest_route(
        {
            "agent_role": route.agent_role,
            "primary_provider": route.primary_provider,
            "primary_model": route.primary_model,
            "fallback_provider": route.fallback_provider,
            "fallback_model": route.fallback_model,
            "parameters": route.parameters,
        },
        role=route.agent_role,
    )
    route_values = {
        key: normalized[key]
        for key in (
            "primary_provider",
            "primary_model",
            "fallback_provider",
            "fallback_model",
            "parameters",
        )
    }
    return {
        "id": str(route.id),
        **route_values,
        "checksum": _checksum(route_values),
    }


def _assert_normalized_model_routes(data: dict[str, Any]) -> None:
    routes = data.get("model_routes")
    if not isinstance(routes, dict):
        raise ExecutionManifestUnavailable(
            "Execution manifest has invalid model routes"
        )
    route_keys = (
        "primary_provider",
        "primary_model",
        "fallback_provider",
        "fallback_model",
        "parameters",
    )
    for role, route in routes.items():
        if not isinstance(route, dict):
            raise ExecutionManifestUnavailable(
                "Execution manifest has invalid model routes"
            )
        normalized = _normalize_manifest_route(
            {"agent_role": role, **{key: route.get(key) for key in route_keys}},
            role=role,
        )
        if {key: route.get(key) for key in route_keys} != {
            key: normalized[key] for key in route_keys
        }:
            raise ExecutionManifestDrift(
                "Execution manifest model routes are not normalized"
            )


def prompt_contract_manifest() -> dict[str, dict[str, str]]:
    result = {}
    for role, schema in CONTRACTS.items():
        result[role] = {
            "prompt_version": PROMPT_VERSIONS[role],
            "contract": schema.__name__,
            "contract_checksum": _checksum(schema.model_json_schema()),
        }
    result["finalizer"] = {
        "prompt_version": PROMPT_VERSIONS["finalizer"],
        "contract": "DeterministicEditorialPackage",
        "contract_checksum": _checksum(
            {
                "required": [
                    "markdown",
                    "html",
                    "seo_metadata",
                    "source_report",
                    "unsupported_claim_count",
                ],
                "unsupported_claim_count": 0,
            }
        ),
    }
    return result


class ExecutionManifestService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, run: PipelineRun, project: Project) -> ExecutionManifest:
        existing = await self.db.scalar(
            select(ExecutionManifest).where(
                ExecutionManifest.pipeline_run_id == run.id
            )
        )
        if existing is not None:
            raise ExecutionManifestDrift("Execution manifest already exists")

        data = await self._snapshot(run, project)
        _assert_secret_free(data)
        row = ExecutionManifest(
            pipeline_run_id=run.id,
            format_version=MANIFEST_FORMAT_VERSION,
            manifest_json=data,
            checksum=_checksum(data),
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def required(
        self,
        pipeline_run_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
    ) -> LoadedExecutionManifest:
        row = await self.db.scalar(
            select(ExecutionManifest).where(
                ExecutionManifest.pipeline_run_id == pipeline_run_id
            )
        )
        if row is None:
            raise ExecutionManifestUnavailable(
                "Pipeline run has no fixed execution manifest"
            )
        if row.pipeline_run_id != pipeline_run_id:
            raise ExecutionManifestDrift(
                "Execution manifest belongs to a different pipeline run"
            )
        data = sanitize_nul(row.manifest_json or {}, strip_escaped=True)
        if row.format_version != MANIFEST_FORMAT_VERSION:
            raise ExecutionManifestDrift("Unsupported execution manifest format")
        if data.get("format_version") != MANIFEST_FORMAT_VERSION:
            raise ExecutionManifestDrift("Execution manifest payload version drift")
        if data.get("pipeline_run_id") != str(pipeline_run_id):
            raise ExecutionManifestDrift(
                "Execution manifest payload belongs to a different pipeline run"
            )
        if project_id is not None and data.get("project_id") != str(project_id):
            raise ExecutionManifestDrift(
                "Execution manifest payload belongs to a different project"
            )
        if _checksum(data) != row.checksum:
            raise ExecutionManifestDrift("Execution manifest checksum mismatch")
        _assert_secret_free(data)
        required_sections = {
            "build",
            "default_skills",
            "embedding_route",
            "feature_flags",
            "learned_skills",
            "memory_snapshots",
            "model_routes",
            "prompt_contracts",
            "quality_evaluator",
            "search_route",
            "style_pattern_snapshots",
            "super_skills",
        }
        if required_sections - data.keys():
            raise ExecutionManifestUnavailable(
                "Execution manifest is missing required dependency sections"
            )
        _assert_normalized_model_routes(data)
        missing = data.get("missing_dependencies") or []
        if missing:
            dependencies = tuple(str(item) for item in missing)
            raise ExecutionManifestUnavailable(
                "Fixed execution dependencies are unavailable: "
                + ", ".join(dependencies),
                dependencies=dependencies,
            )
        if data.get("prompt_contracts") != prompt_contract_manifest():
            raise ExecutionManifestDrift("Prompt or output contract version drift")
        pipeline_version = str(
            (data.get("feature_flags") or {}).get("editorial_pipeline_version") or "v2"
        )
        if pipeline_version == "v3" and data.get("v3_prompt_contracts") != v3_prompt_contract_manifest():
            raise ExecutionManifestDrift("Editorial V3 prompt or output contract version drift")
        if pipeline_version == "v3":
            pinned_v3_definitions(data)
        build = data.get("build") or {}
        build_info = load_build_info(settings)
        current_commit = build_info.commit_sha
        pinned_commit = str(build.get("commit_sha", "")).strip()
        if (
            current_commit not in {"", "unversioned"}
            and pinned_commit not in {"", "unversioned"}
            and current_commit != pinned_commit
        ):
            raise ExecutionManifestDrift("Application commit differs from fixed run")
        current_build = build_info.build_version
        pinned_build = str(build.get("build_version", "")).strip()
        if (
            current_build not in {"", "development", "unversioned"}
            and pinned_build not in {"", "development", "unversioned"}
            and current_build != pinned_build
        ):
            raise ExecutionManifestDrift("Application build differs from fixed run")
        return LoadedExecutionManifest(row=row, data=data)

    async def safe_summary(self, pipeline_run_id: uuid.UUID) -> dict[str, Any]:
        try:
            loaded = await self.required(pipeline_run_id)
        except ExecutionManifestError as exc:
            return {
                "status": "unavailable",
                "error_code": exc.code,
                "message": "Execution manifest is unavailable for this run",
                "pipeline_run_id": str(pipeline_run_id),
                "handoff_ids": await self._artifact_ids(AgentHandoff, pipeline_run_id),
                "source_snapshot_ids": await self._artifact_ids(
                    SourceSnapshot, pipeline_run_id
                ),
            }
        return await self.summary(loaded)

    async def summary(self, loaded: LoadedExecutionManifest) -> dict[str, Any]:
        pipeline_run_id = loaded.row.pipeline_run_id
        data = loaded.data
        return {
            "status": "ready",
            "id": str(loaded.row.id),
            "pipeline_run_id": str(pipeline_run_id),
            "format_version": loaded.row.format_version,
            "checksum": loaded.row.checksum,
            "created_at": loaded.row.created_at,
            "build": data["build"],
            "mode": data["feature_flags"]["superior_skills_mode"],
            "feature_flags": data["feature_flags"],
            "super_skills": {
                role: [
                    {
                        "skill_id": item["skill_id"],
                        "version": item["version"],
                        "checksum": item["checksum"],
                    }
                    for item in entries
                ]
                for role, entries in data["super_skills"].items()
            },
            "default_skills": [
                {
                    "skill_id": item["definition"]["skill_id"],
                    "version": item["definition"]["version"],
                    "checksum": item["checksum"],
                }
                for item in data["default_skills"]
            ],
            "v3_skills": [
                {
                    "skill_id": item["definition"]["skill_id"],
                    "version": item["definition"]["version"],
                    "checksum": item["checksum"],
                }
                for item in (data.get("v3_skills") or [])
            ],
            "learned_skills": {
                role: entry["skills"]
                for role, entry in data["learned_skills"].items()
            },
            "model_routes": data["model_routes"],
            "prompt_contracts": data["prompt_contracts"],
            "quality_evaluator": data["quality_evaluator"],
            "memory_ids": {
                role: [item["id"] for item in entries]
                for role, entries in data["memory_snapshots"].items()
            },
            "style_pattern_ids": {
                role: [item["id"] for item in entries]
                for role, entries in data["style_pattern_snapshots"].items()
            },
            "embedding_route": data["embedding_route"],
            "search_route": data["search_route"],
            "editorial_context": data.get("editorial_context") or {
                "publication_profile": None,
                "content_brief": {},
            },
            "handoff_ids": await self._artifact_ids(AgentHandoff, pipeline_run_id),
            "source_snapshot_ids": await self._artifact_ids(
                SourceSnapshot, pipeline_run_id
            ),
        }

    async def _snapshot(self, run: PipelineRun, project: Project) -> dict[str, Any]:
        pipeline_version = getattr(
            project.editorial_pipeline_version,
            "value",
            project.editorial_pipeline_version,
        )
        roles = list(roles_for_pipeline(pipeline_version))
        routes = list(
            (
                await self.db.scalars(
                    select(ModelRoute).order_by(ModelRoute.agent_role)
                )
            ).all()
        )
        route_manifest = {
            route.agent_role: _model_route_manifest(route)
            for route in routes
            if route.agent_role in roles
        }
        superior = await self._super_skills(roles)
        defaults = []
        for definition in SkillRegistry(settings.skills_path).load_defaults().values():
            dumped = definition.model_dump(mode="json")
            defaults.append({"definition": dumped, "checksum": _checksum(dumped)})
        defaults.sort(key=lambda item: item["definition"]["skill_id"])
        v3_defaults: list[dict[str, Any]] = []
        if pipeline_version == "v3":
            v3_root = Path(settings.skills_path).parent / "v3"
            for definition in SkillRegistry(str(v3_root)).load_defaults().values():
                dumped = definition.model_dump(mode="json")
                v3_defaults.append({"definition": dumped, "checksum": _checksum(dumped)})
            v3_defaults.sort(key=lambda item: item["definition"]["skill_id"])

        learned: dict[str, dict] = {}
        memories: dict[str, list[dict]] = {}
        patterns: dict[str, list[dict]] = {}
        resolver = LearnedSkillResolver(self.db)
        for role in roles:
            resolution = await resolver.resolve(role, project.id)
            learned[role] = {
                "skills": resolution.metadata(),
                "fragment": resolution.fragment,
                "characters": resolution.characters,
                "truncated": resolution.truncated,
            }
            memories[role] = await self._memories(project, role)
            patterns[role] = await self._patterns(project, role)

        embedding = await self.db.scalar(
            select(EmbeddingRoute)
            .where(EmbeddingRoute.active.is_(True))
            .order_by(EmbeddingRoute.updated_at.desc())
        )
        embedding_manifest = (
            {
                "id": str(embedding.id),
                "provider": embedding.provider,
                "model": embedding.model,
                "dimensions": embedding.dimensions,
            }
            if embedding is not None
            else None
        )
        credential_providers = set(
            await self.db.scalars(
                select(Credential.provider).where(
                    Credential.active.is_(True),
                    Credential.verified_at.is_not(None),
                )
            )
        )
        search_providers = [
            provider.value
            for provider in (CredentialProvider.tavily, CredentialProvider.serper)
            if provider in credential_providers
        ]
        search_provider = search_providers[0] if search_providers else None
        missing = [
            f"model_route:{role}" for role in roles if role not in route_manifest
        ]
        if search_provider is None:
            missing.append("search_route")
        if settings.superior_skills_mode == "enforced":
            missing.extend(
                f"super_skills:{role}"
                for role in roles
                if len(superior.get(role, [])) != 2
            )
        if not defaults:
            missing.append("default_skills")
        if pipeline_version == "v3" and not v3_defaults:
            missing.append("v3_skills")

        profile = (
            await self.db.get(PublicationProfile, project.publication_profile_id)
            if project.publication_profile_id
            else None
        )
        profile_snapshot = None
        if profile is not None:
            profile_snapshot = {
                "id": str(profile.id),
                "version": profile.version,
                "name": profile.name,
                "brand_name": profile.brand_name,
                "website_url": profile.website_url,
                "segment": profile.segment,
                "brand_description": profile.brand_description,
                "mission": profile.mission,
                "value_proposition": profile.value_proposition,
                "audience_description": profile.audience_description,
                "tone_of_voice": profile.tone_of_voice,
                "research_summary": profile.research_summary,
                **(profile.profile_data or {}),
            }
        build_info = load_build_info(settings)
        data = {
            "format_version": MANIFEST_FORMAT_VERSION,
            "pipeline_run_id": str(run.id),
            "project_id": str(project.id),
            "fixed_at": datetime.now(timezone.utc).isoformat(),
            "build": {
                **build_info.as_dict(),
            },
            "super_skills": superior,
            "default_skills": defaults,
            "v3_skills": v3_defaults if pipeline_version == "v3" else None,
            "learned_skills": learned,
            "model_routes": route_manifest,
            "prompt_contracts": prompt_contract_manifest(),
            "v3_prompt_contracts": (
                v3_prompt_contract_manifest()
                if pipeline_version == "v3"
                else None
            ),
            "quality_evaluator": quality_rubric_manifest(),
            "memory_snapshots": memories,
            "style_pattern_snapshots": patterns,
            "embedding_route": embedding_manifest,
            "search_route": {
                "provider": search_provider,
                "providers": search_providers,
                "fallback_providers": search_providers[1:],
                "credential_verification_required_before_activation": True,
                "credential_reverification_during_run": False,
                "policy": search_policy_manifest(),
            },
            "editorial_context": {
                "publication_profile": profile_snapshot,
                "content_brief": project.briefing or {},
            },
            "feature_flags": {
                "editorial_pipeline_version": getattr(
                    project.editorial_pipeline_version,
                    "value",
                    project.editorial_pipeline_version,
                ),
                "editorial_pipeline_v3_enabled": settings.editorial_pipeline_v3_enabled,
                "editorial_pipeline_v3_execution_enabled": settings.editorial_pipeline_v3_execution_enabled,
                "v3_max_research_tasks": settings.v3_max_research_tasks,
                "v3_max_search_queries": settings.v3_max_search_queries,
                "v3_search_results_per_query": settings.v3_search_results_per_query,
                "v3_max_source_documents": settings.v3_max_source_documents,
                "v3_max_search_provider_requests": settings.v3_max_search_provider_requests,
                "v3_max_search_provider_retries": settings.v3_max_search_provider_retries,
                "v3_max_search_estimated_credits": settings.v3_max_search_estimated_credits,
                "v3_source_discovery_timeout_seconds": settings.v3_source_discovery_timeout_seconds,
                "v3_max_source_fetches": settings.v3_max_source_fetches,
                "v3_max_source_recovery_rounds": settings.v3_max_source_recovery_rounds,
                "v3_min_candidate_relevance": settings.v3_min_candidate_relevance,
                "v3_max_documents_per_research_task": settings.v3_max_documents_per_research_task,
                "v3_min_approved_claims": settings.v3_min_approved_claims,
                "v3_min_information_coverage_ratio": settings.v3_min_information_coverage_ratio,
                "v3_max_information_recovery_rounds": settings.v3_max_information_recovery_rounds,
                "v3_max_information_recovery_queries_per_round": settings.v3_max_information_recovery_queries_per_round,
                "v3_min_claims_per_method": settings.v3_min_claims_per_method,
                "v3_min_steps_per_method": settings.v3_min_steps_per_method,
                "v3_writer_repair_attempts": settings.v3_writer_repair_attempts,
                "v3_writer_section_repair_attempts": settings.v3_writer_section_repair_attempts,
                "v3_incremental_writer_enabled": settings.v3_incremental_writer_enabled,
                "v3_graph_max_transitions": settings.v3_graph_max_transitions,
                "v3_emergent_questions_enabled": settings.v3_emergent_questions_enabled,
                "v3_max_emergent_questions": settings.v3_max_emergent_questions,
                "v3_min_word_count": settings.v3_min_word_count,
                "v3_max_word_count": settings.v3_max_word_count,
                "superior_skills_mode": settings.superior_skills_mode,
                "max_research_cycles": settings.max_research_cycles,
                "max_editor_cycles": settings.max_editor_cycles,
                "min_distinct_sources": settings.min_distinct_sources,
                "min_facts_per_question": settings.min_facts_per_question,
                "max_pipeline_cost_usd": settings.max_pipeline_cost_usd,
                "max_agent_cost_usd": settings.max_agent_cost_usd,
                "provider_connect_timeout_seconds": (
                    settings.provider_connect_timeout_seconds
                ),
                "provider_read_timeout_seconds": (
                    settings.provider_read_timeout_seconds
                ),
                "max_agent_memories_per_prompt": (
                    settings.max_agent_memories_per_prompt
                ),
                "max_learned_skills_per_prompt": (
                    settings.max_learned_skills_per_prompt
                ),
                "max_learned_skill_characters_per_prompt": (
                    settings.max_learned_skill_characters_per_prompt
                ),
                "learned_skill_stability_threshold": (
                    settings.learned_skill_stability_threshold
                ),
                "learned_skill_min_independent_articles": (
                    settings.learned_skill_min_independent_articles
                ),
                "content_similarity_warning_threshold": (
                    settings.content_similarity_warning_threshold
                ),
                "content_duplicate_threshold": settings.content_duplicate_threshold,
            },
            "artifact_scope": {
                "handoffs": "append_only_run_scoped",
                "source_snapshots": "append_only_run_scoped",
            },
            "missing_dependencies": sorted(set(missing)),
        }
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))

    async def _super_skills(self, roles: list[str]) -> dict[str, list[dict]]:
        rows = (
            await self.db.execute(
                select(SuperiorSkill, SuperiorSkillVersion)
                .join(
                    SuperiorSkillVersion,
                    (SuperiorSkillVersion.superior_skill_id == SuperiorSkill.id)
                    & (
                        SuperiorSkillVersion.version
                        == SuperiorSkill.current_version
                    ),
                )
                .where(
                    SuperiorSkill.enabled.is_(True),
                    SuperiorSkillVersion.status == "active",
                )
                .order_by(SuperiorSkill.scope, SuperiorSkill.skill_id)
            )
        ).all()
        result = {role: [] for role in roles}
        for skill, version in rows:
            definition = SuperiorSkillDefinition.model_validate(version.definition)
            calculated = definition.checksum()
            if calculated != version.checksum:
                raise ExecutionManifestDrift(
                    f"Superior skill checksum drift: {skill.skill_id}"
                )
            entry = {
                "id": str(skill.id),
                "version_id": str(version.id),
                "skill_id": skill.skill_id,
                "version": version.version,
                "checksum": version.checksum,
                "definition": definition.model_dump(mode="json"),
            }
            if skill.scope == SuperiorSkillScope.global_core:
                for role in roles:
                    result[role].append(entry)
            elif skill.agent_role in result:
                result[skill.agent_role].append(entry)
        return result

    async def _memories(self, project: Project, role: str) -> list[dict]:
        conditions = [
            AgentMemory.agent_role == role,
            AgentMemory.status == LearningStatus.approved,
            or_(AgentMemory.project_id.is_(None), AgentMemory.project_id == project.id),
        ]
        if project.niche:
            conditions.append(
                or_(AgentMemory.niche.is_(None), AgentMemory.niche == project.niche)
            )
        rows = list(
            (
                await self.db.scalars(
                    select(AgentMemory)
                    .where(*conditions)
                    .order_by(
                        AgentMemory.confidence_score.desc(),
                        AgentMemory.updated_at.desc(),
                        AgentMemory.id,
                    )
                    .limit(settings.max_agent_memories_per_prompt)
                )
            ).all()
        )
        return [self._memory_snapshot(row) for row in rows]

    async def _patterns(self, project: Project, role: str) -> list[dict]:
        applicability = [
            StylePattern.project_id.is_(None),
            StylePattern.project_id == project.id,
        ]
        if project.niche:
            applicability.append(StylePattern.niche == project.niche)
        rows = list(
            (
                await self.db.scalars(
                    select(StylePattern)
                    .where(
                        StylePattern.target_agent_role == role,
                        StylePattern.status == LearningStatus.approved,
                        or_(*applicability),
                    )
                    .order_by(
                        StylePattern.validation_count.desc(),
                        StylePattern.updated_at.desc(),
                        StylePattern.id,
                    )
                    .limit(4)
                )
            ).all()
        )
        return [self._pattern_snapshot(row) for row in rows]

    @staticmethod
    def _memory_snapshot(row: AgentMemory) -> dict:
        data = {
            "id": str(row.id),
            "agent_role": row.agent_role,
            "content": row.content,
            "memory_kind": row.memory_kind,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "confidence_score": row.confidence_score,
            "persona_version": row.persona_version,
        }
        return {**data, "checksum": _checksum(data)}

    @staticmethod
    def _pattern_snapshot(row: StylePattern) -> dict:
        data = {
            "id": str(row.id),
            "target_agent_role": row.target_agent_role,
            "pattern_type": row.pattern_type,
            "description": row.description,
            "source_ids": row.source_ids,
            "validation_count": row.validation_count,
        }
        return {**data, "checksum": _checksum(data)}

    async def _artifact_ids(self, model, pipeline_run_id: uuid.UUID) -> list[str]:
        rows = await self.db.scalars(
            select(model.id)
            .where(model.pipeline_run_id == pipeline_run_id)
            .order_by(model.id)
        )
        return [str(item) for item in rows.all()]


def pinned_default_definitions(data: dict[str, Any]) -> dict[str, SkillDefinition]:
    definitions = {}
    for entry in data.get("default_skills", []):
        definition_data = entry.get("definition") or {}
        if _checksum(definition_data) != entry.get("checksum"):
            raise ExecutionManifestDrift("Default skill checksum mismatch")
        definition = SkillDefinition.model_validate(definition_data)
        definitions[definition.skill_id] = definition
    if not definitions:
        raise ExecutionManifestUnavailable("No default skills fixed in manifest")
    return definitions


def pinned_v3_definitions(data: dict[str, Any]) -> dict[str, SkillDefinition]:
    definitions = {}
    for entry in data.get("v3_skills") or []:
        definition_data = entry.get("definition") or {}
        if _checksum(definition_data) != entry.get("checksum"):
            raise ExecutionManifestDrift("Editorial V3 skill checksum mismatch")
        definition = SkillDefinition.model_validate(definition_data)
        definitions[definition.skill_id] = definition
    if not definitions:
        raise ExecutionManifestUnavailable("No Editorial V3 skills fixed in manifest")
    return definitions


def _checksum(value: Any) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assert_secret_free(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_sensitive_manifest_key(key):
                location = f"{path}.{key}"
                raise ExecutionManifestContainsSecret(
                    f"Sensitive field is forbidden in execution manifest at {location}",
                    dependencies=(f"manifest_path:{location}",),
                )
            _assert_secret_free(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_secret_free(item, f"{path}[{index}]")
    elif isinstance(value, str) and _SENSITIVE_VALUE.search(value):
        raise ExecutionManifestContainsSecret(
            f"Sensitive value is forbidden in execution manifest at {path}",
            dependencies=(f"manifest_path:{path}",),
        )
