import asyncio
import hashlib
import hmac
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
    Header,
)
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import (
    AgentRun,
    AgentHandoff,
    Article,
    ArticleVersion,
    Credential,
    CredentialProvider,
    FactLedger,
    HumanEditorialReview,
    ModelRoute,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    ProviderAttempt,
    PipelineCheckpoint,
    PipelineStateTransition,
    Project,
    ProjectStatus,
    PublicationProfile,
    QualityEvaluation,
    Skill,
    SkillKind,
    SkillLifecycleEvent,
    SkillValidation,
    SkillVersion,
    AgentMemory,
    EmbeddingRoute,
    LearningStatus,
    StylePattern,
    StyleSource,
    SuperiorSkill,
    SuperiorSkillVersion,
)
from app.db.session import SessionLocal, get_db
from app.schemas.api import (
    AgentContextPreviewRequest,
    ConfigRead,
    CredentialRead,
    CredentialVerificationRead,
    CredentialWrite,
    DashboardRead,
    FactRead,
    HumanEditorialReviewDecision,
    HumanEditorialReviewDecisionRead,
    ModelRouteWrite,
    PipelineRunDetailRead,
    PipelineRunCancellationRead,
    ProjectCreate,
    ProjectCreateRead,
    ProjectDetailRead,
    ProjectRead,
    PublicationProfileRead,
    PublicationProfileWrite,
    AgentMemoryWrite,
    EmbeddingRouteWrite,
    LearningDecisionWrite,
    LearnedSkillLifecycleAction,
    StyleSourceWrite,
    SuperiorSkillVersionWrite,
    WebSocketTicketRead,
    WebSocketTicketRequest,
    V3KnowledgeContractPreviewRead,
    V3KnowledgeContractRead,
)
from app.core.config import settings
from app.core.sanitization import sanitize_nul
from app.core.errors import redact_sensitive, safe_public_message, safe_public_payload
from app.services.agent_context import AgentContextComposer
from app.services.embeddings import EmbeddingError, EmbeddingGateway
from app.services.credential_verification import CredentialVerificationService
from app.services.editorial_export import EditorialExportService
from app.services.human_editorial_review import (
    HumanEditorialReviewService,
    HumanReviewConflict,
    HumanReviewInputError,
)
from app.services.execution_manifest import (
    ExecutionManifestError,
    ExecutionManifestService,
)
from app.services.editorial_roles import ALL_AGENT_ROLES
from app.services.execution_preflight import inspect_execution_dependencies
from app.services.model_route_bootstrap import (
    DEFAULT_MODELS,
    PROVIDER_STANDARD_TOKEN_RATES as _PROVIDER_STANDARD_TOKEN_RATES,
    default_route_for_provider,
)
from app.services.model_catalog import apply_known_model_profile
from app.services.model_route_policy import (
    ModelRoutePolicyError,
    normalize_model_route_configuration,
)
from app.services.research_engine import canonicalize_url
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.contract_repository import (
    KnowledgeContractRepository,
)
from app.services.superior_skills import SuperiorSkillDefinition
from app.services.skill_learning import (
    SkillLearningInputError,
    SkillLifecycleConflict,
    SkillLearningService,
)
from app.services.vault import CredentialVault, VaultError
from app.services.pipeline_control import (
    EventService,
    InvalidRunTransition,
    PipelineRunService,
)
from app.services.pipeline_dispatch import dispatch_one, dispatcher_identity
from app.services.readiness import readiness_report
from app.services.quality_evaluator import quality_summary
from app.services.websocket_tickets import (
    WEBSOCKET_SUBPROTOCOL,
    WebSocketTicketStore,
    WebSocketTicketUnavailable,
    get_websocket_ticket_store,
)
from app.db.models import TriggerType

router = APIRouter(prefix="/api/v1")

WEBSOCKET_EVENT_BATCH_SIZE = 100
WEBSOCKET_SUBSCRIBE_TIMEOUT_SECONDS = 10

AGENT_ROLES = tuple(sorted(ALL_AGENT_ROLES))
PROVIDER_STANDARD_TOKEN_RATES = _PROVIDER_STANDARD_TOKEN_RATES


def _default_route_for_provider(provider: str, role: str) -> dict[str, object]:
    return default_route_for_provider(provider, role)


_PUBLIC_EVENT_PAYLOAD_FIELDS = frozenset(
    {
        "actor",
        "approved_fact_count",
        "available_markets",
        "brazil_context_explicit",
        "decision",
        "document_count",
        "error_code",
        "error_category",
        "http_status",
        "correlation_id",
        "fact_count",
        "market",
        "markets_requested",
        "markets_with_results",
        "message",
        "next_retry_at",
        "next_stage",
        "pipeline_continues",
        "question_id",
        "reason",
        "retryable",
        "selected_markets",
        "source_count",
        "status",
        "version",
    }
)

_AGENT_CONTEXT_PREVIEW_METADATA_FIELDS = frozenset(
    {
        "external_embeddings_enabled",
        "handoff_id",
        "learned_skill_characters",
        "learned_skill_truncated",
        "learned_skills",
        "memory_ids",
        "mode",
        "pipeline_run_id",
        "status",
        "style_pattern_ids",
        "versions",
    }
)
_CONTEXT_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:admin[-_]?token|api[-_]?key|authorization|credential|"
    r"database_url|password|redis_url|secret|token)\b[\"']?\s*[:=]\s*[\"']?)"
    r"[^\"'\s,;}]+"
)
_CONTEXT_PROVIDER_KEY = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{16,}|AIza[a-zA-Z0-9_-]{20,})\b"
)


def _public_event_payload(payload: object) -> dict:
    sanitized = safe_public_payload(payload)
    if not isinstance(sanitized, dict):
        return {}
    return {
        key: value
        for key, value in sanitized.items()
        if key in _PUBLIC_EVENT_PAYLOAD_FIELDS
    }


def _human_review_payload(
    review: HumanEditorialReview, *, include_package: bool
) -> dict:
    payload = {
        "id": review.id,
        "project_id": review.project_id,
        "pipeline_run_id": review.pipeline_run_id,
        "article_version_id": review.article_version_id,
        "reviewer": review.reviewer,
        "decision": review.decision,
        "observation": safe_public_message(review.observation),
        "reviewed_at": review.reviewed_at,
        "revision_run_id": review.revision_run_id,
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }
    if include_package:
        payload["review_package"] = safe_public_payload(review.review_package_json)
    return payload


def _project_read(project: Project, last_run_status: object = None) -> ProjectRead:
    status = getattr(last_run_status, "value", last_run_status)
    return ProjectRead.model_validate(project).model_copy(
        update={"last_run_status": status}
    )


_PROFILE_DATA_FIELDS = {
    "products_services",
    "audience_age_min",
    "audience_age_max",
    "audience_life_stage",
    "audience_knowledge_level",
    "audience_goals",
    "audience_pain_points",
    "brand_terms",
    "forbidden_terms",
    "primary_markets",
    "editorial_goals",
    "commercial_objective",
    "preferred_cta",
}


def _publication_profile_values(
    payload: PublicationProfileWrite,
) -> tuple[dict, dict]:
    values = sanitize_nul(payload.model_dump(), strip_escaped=True)
    profile_data = {key: values.pop(key) for key in tuple(_PROFILE_DATA_FIELDS)}
    return values, profile_data


def _publication_profile_read(
    profile: PublicationProfile,
) -> PublicationProfileRead:
    profile_data = profile.profile_data or {}
    return PublicationProfileRead.model_validate(
        {
            "id": profile.id,
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
            "status": profile.status,
            "version": profile.version,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
            **{
                key: profile_data.get(key)
                for key in _PROFILE_DATA_FIELDS
                if key in profile_data
            },
        }
    )


def _latest_run_status():
    return (
        select(PipelineRun.status)
        .where(PipelineRun.project_id == Project.id)
        .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        .limit(1)
        .correlate(Project)
        .scalar_subquery()
    )


def _research_outcome_code(run: PipelineRun, audit: dict | None = None) -> str | None:
    if run.error_code == "NO_USABLE_RESEARCH_RESULTS":
        return "no_usable_research_results"
    if run.error_code == "RESEARCH_INSUFFICIENT":
        return "research_insufficient"
    status_value = getattr(run.status, "value", run.status)
    if (
        status_value == "failed"
        and run.current_stage == "blocked"
        and isinstance(audit, dict)
        and audit.get("decision") == "insufficient"
        and (
            audit.get("coverage_complete") is False
            or bool(audit.get("missing_questions"))
            or bool(audit.get("unresolved_conflicts"))
        )
    ):
        # Compatibility diagnosis for immutable runs created before `blocked`
        # existed. The stored run and manifest remain unchanged.
        return "research_insufficient"
    return None


def _diagnostic_strings(value: object, *, limit: int = 20) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        message = safe_public_message(str(item))
        if message:
            result.append(message[:500])
    return result


def _research_diagnostic(
    run: PipelineRun | None,
    gatekeeper_runs: list[AgentRun],
) -> dict | None:
    if run is None or not gatekeeper_runs:
        return None
    output = gatekeeper_runs[-1].output_json
    if not isinstance(output, dict):
        return None

    coverage = output.get("coverage_by_question", {})
    coverage = coverage if isinstance(coverage, dict) else {}
    covered_count = int(
        output.get(
            "covered_question_count",
            sum(1 for score in coverage.values() if float(score or 0) >= 1),
        )
        or 0
    )
    total_count = int(output.get("total_question_count", len(coverage)) or 0)
    minimum_sources = int(output.get("minimum_distinct_sources", 5) or 5)
    diversity_score = max(
        0.0, min(1.0, float(output.get("source_diversity_score", 0) or 0))
    )
    distinct_sources = int(
        output.get(
            "distinct_source_count",
            round(diversity_score * minimum_sources),
        )
        or 0
    )
    counts = output.get("rejection_reason_counts", {})
    if not isinstance(counts, dict):
        counts = {}
    safe_counts = {
        str(reason)[:50]: max(0, int(count or 0))
        for reason, count in counts.items()
        if str(reason).strip()
    }
    return {
        "pipeline_run_id": run.id,
        "outcome_code": _research_outcome_code(run, output),
        "decision": str(output.get("decision"))[:30]
        if output.get("decision") is not None
        else None,
        "coverage_complete": bool(output.get("coverage_complete", False)),
        "covered_question_count": max(0, min(covered_count, total_count)),
        "total_question_count": max(0, total_count),
        "recommended_fact_count": max(
            0,
            int(
                output.get(
                    "recommended_fact_count",
                    len(output.get("approved_fact_ids", [])),
                )
                or 0
            ),
        ),
        "distinct_source_count": max(0, distinct_sources),
        "minimum_distinct_sources": max(0, minimum_sources),
        "source_diversity_score": diversity_score,
        "missing_questions": _diagnostic_strings(output.get("missing_questions")),
        "unresolved_conflicts": _diagnostic_strings(output.get("unresolved_conflicts")),
        "rejection_reason_counts": dict(sorted(safe_counts.items())),
        "instructions": _diagnostic_strings(output.get("instructions")),
    }


def _editorial_diagnostic(runs: list[AgentRun]) -> dict | None:
    editor_runs = [run for run in runs if run.agent_role == "editor"]
    if not editor_runs:
        return None
    editor = editor_runs[-1]
    output = editor.output_json if isinstance(editor.output_json, dict) else {}
    findings = []
    for category in ("fidelity_findings", "language_findings"):
        for finding in output.get(category, [])[:12]:
            if not isinstance(finding, dict):
                continue
            findings.append(
                {
                    "category": category.removesuffix("_findings"),
                    "severity": str(finding.get("severity") or "minor")[:20],
                    "issue": (safe_public_message(finding.get("issue")) or "")[:500],
                    "suggested_action": (
                        safe_public_message(finding.get("suggested_action")) or ""
                    )[:500],
                }
            )
    repair = next(
        (run for run in reversed(runs) if run.agent_role == "editorial_repair"),
        None,
    )
    blocking_count = sum(
        1 for finding in findings if finding["severity"] in {"major", "critical"}
    )
    resolution = output.get("resolution")
    if repair is not None:
        repair_feedback = getattr(repair, "feedback", None) or {}
        resolution = repair_feedback.get("resolution") or (
            "deterministic_targeted_repair"
            if getattr(repair, "model", None) == "targeted-sentence-removal-v2"
            else "deterministic_editorial_repair"
        )
    return {
        "pipeline_run_id": editor.pipeline_run_id,
        "decision": getattr(
            repair.decision if repair is not None else editor.decision,
            "value",
            repair.decision if repair is not None else editor.decision,
        ),
        "model_decision": str(
            output.get("model_decision")
            or getattr(editor.decision, "value", editor.decision)
        ),
        "resolution": str(resolution) if resolution else None,
        "blocking_finding_count": blocking_count,
        "findings": findings,
    }


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not settings.admin_api_token.strip():
        raise HTTPException(503, "Acesso administrativo não autorizado.")
    if (
        not x_admin_token
        or not x_admin_token.strip()
        or not hmac.compare_digest(x_admin_token, settings.admin_api_token)
    ):
        raise HTTPException(401, "Acesso administrativo não autorizado.")


@router.get("/health")
async def health():
    return {"status": "healthy", "service": "seo-research-ledger"}


@router.get("/readiness")
async def readiness(request: Request, db: AsyncSession = Depends(get_db)):
    report = await readiness_report(
        db,
        preflight_complete=getattr(
            request.app.state, "production_preflight_complete", False
        ),
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK
        if report.ready
        else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=report.safe_payload(),
    )


async def _require_run_start_readiness(
    request: Request,
    db: AsyncSession,
    pipeline_version: object,
    *,
    existing_manifest: bool = False,
) -> None:
    if not settings.is_production:
        return
    report = await readiness_report(
        db,
        preflight_complete=getattr(
            request.app.state, "production_preflight_complete", False
        ),
        pipeline_version=pipeline_version,
        require_execution_dependencies=not existing_manifest,
    )
    if report.ready:
        return
    raise HTTPException(
        503,
        {
            "error_code": "SYSTEM_NOT_READY",
            "message": "Novos runs estão pausados até a prontidão operacional.",
            "components": report.safe_payload()["components"],
        },
    )


async def _require_execution_dependencies(
    db: AsyncSession,
    pipeline_version: object,
) -> dict[str, object]:
    report = await inspect_execution_dependencies(
        db,
        pipeline_version,
        repair_missing_routes=True,
    )
    if report.repairs:
        await db.commit()
    if report.ready:
        return report.safe_payload()
    raise HTTPException(
        409,
        {
            "error_code": "EXECUTION_DEPENDENCIES_NOT_READY",
            "message": (
                "A execução não pôde ser iniciada porque existem dependências "
                "editoriais incompletas. Corrija os itens listados e tente novamente."
            ),
            "dependencies": list(report.gaps),
            "repairs": list(report.repairs),
            "pipeline_version": report.pipeline_version,
        },
    )


async def _dispatch_pipeline_run(run_id: uuid.UUID, *, origin: str) -> str:
    """Publish one run without turning a transient broker outage into data loss."""

    from app.workers.tasks import run_pipeline

    dispatch = await dispatch_one(
        run_id,
        run_pipeline,
        dispatcher_identity(origin=origin),
    )
    if dispatch is None:
        return "pending"
    if dispatch.status == "failed":
        # The durable dispatch ledger and Beat own retries. The API reports the
        # condition, but the already committed project/run stays recoverable.
        return "retry_scheduled"
    return dispatch.status


def _project_payload_matches(
    project: Project,
    *,
    values: dict[str, object],
    publication_profile_id: uuid.UUID | None,
    briefing: dict[str, object],
) -> bool:
    """Reject accidental reuse of an idempotency key for another project."""

    for key, expected in values.items():
        actual = getattr(project, key)
        actual = getattr(actual, "value", actual)
        if actual != expected:
            return False
    return (
        project.publication_profile_id == publication_profile_id
        and sanitize_nul(project.briefing or {}, strip_escaped=True) == briefing
    )


@router.get(
    "/config/execution-preflight",
    dependencies=[Depends(require_admin)],
)
async def execution_preflight(
    pipeline_version: str = "v3",
    repair: bool = False,
    db: AsyncSession = Depends(get_db),
):
    if pipeline_version not in {"v2", "v3"}:
        raise HTTPException(422, "pipeline_version must be v2 or v3")
    report = await inspect_execution_dependencies(
        db,
        pipeline_version,
        repair_missing_routes=repair,
    )
    if repair and report.repairs:
        await db.commit()
    return report.safe_payload()


@router.get(
    "/publication-profiles",
    response_model=list[PublicationProfileRead],
    dependencies=[Depends(require_admin)],
)
async def list_publication_profiles(
    include_archived: bool = False,
    db: AsyncSession = Depends(get_db),
):
    statement = select(PublicationProfile)
    if not include_archived:
        statement = statement.where(PublicationProfile.status == "active")
    profiles = list(
        (
            await db.scalars(
                statement.order_by(
                    PublicationProfile.updated_at.desc(),
                    PublicationProfile.name,
                )
            )
        ).all()
    )
    return [_publication_profile_read(profile) for profile in profiles]


@router.get(
    "/publication-profiles/{profile_id}",
    response_model=PublicationProfileRead,
    dependencies=[Depends(require_admin)],
)
async def get_publication_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    profile = await db.get(PublicationProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Perfil editorial não encontrado")
    return _publication_profile_read(profile)


@router.post(
    "/publication-profiles",
    response_model=PublicationProfileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_publication_profile(
    payload: PublicationProfileWrite,
    db: AsyncSession = Depends(get_db),
):
    values, profile_data = _publication_profile_values(payload)
    profile = PublicationProfile(
        **values,
        profile_data=profile_data,
        status="active",
        version=1,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return _publication_profile_read(profile)


@router.put(
    "/publication-profiles/{profile_id}",
    response_model=PublicationProfileRead,
    dependencies=[Depends(require_admin)],
)
async def update_publication_profile(
    profile_id: uuid.UUID,
    payload: PublicationProfileWrite,
    db: AsyncSession = Depends(get_db),
):
    profile = await db.get(PublicationProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Perfil editorial não encontrado")
    values, profile_data = _publication_profile_values(payload)
    for key, value in values.items():
        setattr(profile, key, value)
    profile.profile_data = profile_data
    profile.version += 1
    await db.commit()
    await db.refresh(profile)
    return _publication_profile_read(profile)


@router.delete(
    "/publication-profiles/{profile_id}",
    response_model=PublicationProfileRead,
    dependencies=[Depends(require_admin)],
)
async def archive_publication_profile(
    profile_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    profile = await db.get(PublicationProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Perfil editorial não encontrado")
    profile.status = "archived"
    profile.version += 1
    await db.commit()
    await db.refresh(profile)
    return _publication_profile_read(profile)


@router.get(
    "/projects",
    response_model=list[ProjectRead],
    dependencies=[Depends(require_admin)],
)
async def list_projects(
    project_status: ProjectStatus | None = None,
    last_run_status: PipelineRunStatus | None = None,
    db: AsyncSession = Depends(get_db),
):
    latest_status = _latest_run_status().label("last_run_status")
    statement = select(Project, latest_status)
    if project_status is not None:
        statement = statement.where(Project.status == project_status)
    if last_run_status is not None:
        statement = statement.where(_latest_run_status() == last_run_status)
    rows = (await db.execute(statement.order_by(Project.created_at.desc()))).all()
    return [_project_read(project, status) for project, status in rows]


@router.post(
    "/projects",
    response_model=ProjectCreateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if (
        payload.start_immediately
        and payload.editorial_pipeline_version == "v3"
        and not settings.editorial_pipeline_v3_execution_enabled
    ):
        raise HTTPException(
            409,
            {
                "error_code": "EDITORIAL_V3_EXECUTION_DISABLED",
                "message": (
                    "A execução V3 está desabilitada pela configuração do ambiente. "
                    "Ative as duas flags V3 somente depois de aplicar a migration 0035 "
                    "e validar as rotas de modelos e pesquisa."
                ),
            },
        )
    if payload.start_immediately:
        await _require_execution_dependencies(db, payload.editorial_pipeline_version)
        await _require_run_start_readiness(
            request, db, payload.editorial_pipeline_version
        )

    clean_payload = sanitize_nul(
        payload.model_dump(exclude={"start_immediately"}),
        strip_escaped=True,
    )
    publication_profile_id = clean_payload.pop("publication_profile_id", None)
    briefing = clean_payload.pop("briefing", {})
    if publication_profile_id is not None:
        profile = await db.get(PublicationProfile, publication_profile_id)
        if profile is None or profile.status != "active":
            raise HTTPException(422, "Selecione um perfil editorial ativo")

    idempotency_key = sanitize_nul(idempotency_key, strip_escaped=True)
    if idempotency_key:
        existing = await db.scalar(
            select(Project).where(Project.creation_idempotency_key == idempotency_key)
        )
        if existing:
            if not _project_payload_matches(
                existing,
                values=clean_payload,
                publication_profile_id=publication_profile_id,
                briefing=briefing,
            ):
                raise HTTPException(
                    409,
                    {
                        "error_code": "IDEMPOTENCY_KEY_PAYLOAD_MISMATCH",
                        "message": (
                            "Esta chave de idempotência já pertence a outro pedido. "
                            "Atualize o formulário antes de tentar novamente."
                        ),
                    },
                )
            last_run = await db.scalar(
                select(PipelineRun)
                .where(PipelineRun.project_id == existing.id)
                .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
                .limit(1)
            )
            run_created = False
            if payload.start_immediately and last_run is None:
                try:
                    last_run, run_created = await PipelineRunService(db).create(
                        existing.id,
                        f"project-create:{existing.id}",
                        trigger_type=TriggerType.automatic,
                    )
                except ExecutionManifestError as exc:
                    await db.rollback()
                    raise HTTPException(
                        409,
                        {
                            "error_code": exc.code,
                            "message": (
                                "O projeto existe, mas as dependências da execução "
                                "não puderam ser fixadas."
                            ),
                            "dependencies": list(getattr(exc, "dependencies", ())),
                        },
                    ) from exc
                await db.commit()
            dispatch_status = (
                await _dispatch_pipeline_run(
                    last_run.id, origin="api.project-create-idempotent"
                )
                if run_created and last_run is not None
                else (
                    getattr(last_run.dispatch_status, "value", last_run.dispatch_status)
                    if last_run is not None
                    else "not_started"
                )
            )
            base = _project_read(existing, last_run.status if last_run else None)
            return ProjectCreateRead(
                **base.model_dump(),
                start_requested=payload.start_immediately,
                pipeline_run_id=last_run.id if last_run else None,
                run_created=run_created,
                dispatch_status=dispatch_status,
            )

    project = Project(
        **clean_payload,
        publication_profile_id=publication_profile_id,
        briefing=briefing,
        creation_idempotency_key=idempotency_key,
        status="queued" if payload.start_immediately else "draft",
    )
    db.add(project)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        if idempotency_key:
            existing = await db.scalar(
                select(Project).where(
                    Project.creation_idempotency_key == idempotency_key
                )
            )
            if existing:
                base = _project_read(existing, None)
                return ProjectCreateRead(
                    **base.model_dump(),
                    start_requested=payload.start_immediately,
                    dispatch_status="unknown",
                )
        raise

    await EventService(db).append(
        project.id,
        None,
        "project.created",
        "planner",
        {
            "topic": project.topic,
            "content_type": getattr(
                project.content_type, "value", project.content_type
            ),
            "publication_profile_id": (
                str(project.publication_profile_id)
                if project.publication_profile_id
                else None
            ),
            "start_requested": payload.start_immediately,
            "pipeline_version": payload.editorial_pipeline_version,
        },
    )

    pipeline_run = None
    run_created = False
    if payload.start_immediately:
        try:
            pipeline_run, run_created = await PipelineRunService(db).create(
                project.id,
                f"project-create:{project.id}",
                trigger_type=TriggerType.automatic,
            )
        except ExecutionManifestError as exc:
            await db.rollback()
            raise HTTPException(
                409,
                {
                    "error_code": exc.code,
                    "message": (
                        "As dependências da execução não puderam ser fixadas. "
                        "Use o diagnóstico abaixo para corrigir a configuração."
                    ),
                    "dependencies": list(getattr(exc, "dependencies", ())),
                },
            ) from exc

    # Project, event, run and manifest are committed atomically. A failed
    # manifest can no longer leave a dashboard project without a run.
    await db.commit()
    await db.refresh(project)

    dispatch_status = "not_requested"
    if payload.start_immediately and pipeline_run is not None:
        dispatch_status = await _dispatch_pipeline_run(
            pipeline_run.id, origin="api.project-create"
        )

    base = _project_read(project, pipeline_run.status if pipeline_run else None)
    return ProjectCreateRead(
        **base.model_dump(),
        start_requested=payload.start_immediately,
        pipeline_run_id=pipeline_run.id if pipeline_run else None,
        run_created=run_created,
        dispatch_status=dispatch_status,
    )


@router.post(
    "/projects/{project_id}/editorial-v3/knowledge-contract/preview",
    response_model=V3KnowledgeContractPreviewRead,
    dependencies=[Depends(require_admin)],
)
async def preview_v3_knowledge_contract(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    if not settings.editorial_pipeline_v3_enabled:
        raise HTTPException(
            409,
            {
                "error_code": "EDITORIAL_V3_DISABLED",
                "message": (
                    "Ative EDITORIAL_PIPELINE_V3_ENABLED para inspecionar contratos V3."
                ),
            },
        )
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    builder = KnowledgeContractBuilder()
    try:
        contract = builder.build(KnowledgeContractInput.from_project(project))
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            422,
            {
                "error_code": "EDITORIAL_V3_CONTRACT_INVALID",
                "message": str(exc),
            },
        ) from exc
    return V3KnowledgeContractPreviewRead(
        contract=contract.model_dump(mode="json"),
        checksum=builder.checksum(contract),
        validation={
            "status": "passed",
            "contract_version": contract.contract_version,
            "node_count": len(contract.nodes),
            "edge_count": len(contract.edges),
            "writer_allowed": False,
        },
        execution_enabled=settings.editorial_pipeline_v3_execution_enabled,
        warning=(
            "Esta prévia valida apenas a ordem e as dependências editoriais. A execução "
            "real ocorre ao iniciar o projeto e permanece condicionada aos gates de pesquisa."
        ),
    )


@router.post(
    "/projects/{project_id}/editorial-v3/knowledge-contract",
    response_model=V3KnowledgeContractRead,
    dependencies=[Depends(require_admin)],
)
async def materialize_v3_knowledge_contract(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    if not settings.editorial_pipeline_v3_enabled:
        raise HTTPException(
            409,
            {
                "error_code": "EDITORIAL_V3_DISABLED",
                "message": "Ative EDITORIAL_PIPELINE_V3_ENABLED para materializar contratos V3.",
            },
        )
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    project_pipeline = getattr(
        getattr(project, "editorial_pipeline_version", "v2"),
        "value",
        getattr(project, "editorial_pipeline_version", "v2"),
    )
    if project_pipeline != "v3":
        raise HTTPException(
            409,
            {
                "error_code": "PROJECT_NOT_EDITORIAL_V3",
                "message": "O projeto precisa usar editorial_pipeline_version=v3.",
            },
        )
    repository = KnowledgeContractRepository(db)
    try:
        result = await repository.materialize(project)
        await db.commit()
    except IntegrityError as exc:
        # Two administrators may request the same deterministic contract at the
        # same time.  The project/checksum unique constraint is the final guard;
        # return the winner instead of surfacing a false failure.
        await db.rollback()
        project = await db.get(Project, project_id)
        if project is None:
            raise HTTPException(404, "Project not found") from exc
        repository = KnowledgeContractRepository(db)
        result = await repository.find_existing(project)
        if result is None:
            raise HTTPException(
                409,
                {
                    "error_code": "EDITORIAL_V3_CONTRACT_CONFLICT",
                    "message": "O contrato V3 não pôde ser persistido de forma idempotente.",
                },
            ) from exc
    except (TypeError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            422,
            {
                "error_code": "EDITORIAL_V3_CONTRACT_INVALID",
                "message": str(exc),
            },
        ) from exc
    return V3KnowledgeContractRead(
        id=result.row.id,
        version=result.row.version,
        status=result.row.status,
        created=result.created,
        contract=result.contract.model_dump(mode="json"),
        checksum=result.row.checksum,
        validation={
            "status": "passed",
            "contract_version": result.contract.contract_version,
            "node_count": len(result.contract.nodes),
            "edge_count": len(result.contract.edges),
            "writer_allowed": False,
        },
        execution_enabled=settings.editorial_pipeline_v3_execution_enabled,
        warning=(
            "Contrato persistido e validado. O Writer continuará bloqueado até pesquisa, "
            "síntese, links e completude passarem pelos gates V3."
        ),
    )


@router.post(
    "/projects/{project_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def start_project(
    project_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    project_pipeline = getattr(
        getattr(project, "editorial_pipeline_version", "v2"),
        "value",
        getattr(project, "editorial_pipeline_version", "v2"),
    )
    if (
        project_pipeline == "v3"
        and not settings.editorial_pipeline_v3_execution_enabled
    ):
        raise HTTPException(
            409,
            {
                "error_code": "EDITORIAL_V3_EXECUTION_DISABLED",
                "message": (
                    "A execução V3 está desabilitada pela configuração do ambiente. "
                    "Ative EDITORIAL_PIPELINE_V3_ENABLED e "
                    "EDITORIAL_PIPELINE_V3_EXECUTION_ENABLED após aplicar a migration 0035."
                ),
            },
        )
    project_status = ProjectStatus(project.status)
    has_active_run_state = project_status in {
        ProjectStatus.queued,
        ProjectStatus.running,
        ProjectStatus.needs_human_approval,
    }
    if not has_active_run_state:
        await _require_execution_dependencies(db, project_pipeline)
        await _require_run_start_readiness(request, db, project_pipeline)
    try:
        run, created = await PipelineRunService(db).create(
            project.id,
            idempotency_key or f"manual:{uuid.uuid4()}",
            trigger_type=TriggerType.api,
        )
    except ExecutionManifestError as exc:
        await db.rollback()
        raise HTTPException(
            409,
            {
                "error_code": exc.code,
                "message": (
                    "As dependências da execução não puderam ser fixadas. "
                    "Use o diagnóstico abaixo para corrigir a configuração."
                ),
                "dependencies": list(getattr(exc, "dependencies", ())),
            },
        ) from exc
    if created:
        project.status = "queued"
    await db.commit()
    dispatch_status = (
        await _dispatch_pipeline_run(run.id, origin="api.project-run")
        if created
        else "duplicate"
    )
    return {
        "project_id": project.id,
        "pipeline_run_id": run.id,
        "status": run.status,
        "duplicate": not created,
        "dispatch_status": dispatch_status,
    }


@router.get(
    "/pipeline-runs/{run_id}",
    response_model=PipelineRunDetailRead,
    dependencies=[Depends(require_admin)],
)
async def pipeline_run_detail(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(404, "Pipeline run not found")
    checkpoints = (
        await db.scalars(
            select(PipelineCheckpoint)
            .where(PipelineCheckpoint.pipeline_run_id == run.id)
            .order_by(PipelineCheckpoint.completed_at)
        )
    ).all()
    transitions = (
        await db.scalars(
            select(PipelineStateTransition)
            .where(PipelineStateTransition.pipeline_run_id == run.id)
            .order_by(PipelineStateTransition.created_at)
        )
    ).all()
    agent_calls = (
        await db.scalars(
            select(AgentRun)
            .where(AgentRun.pipeline_run_id == run.id)
            .order_by(AgentRun.created_at)
        )
    ).all()
    provider_attempts = (
        await db.scalars(
            select(ProviderAttempt)
            .where(ProviderAttempt.pipeline_run_id == run.id)
            .order_by(
                ProviderAttempt.started_at,
                ProviderAttempt.agent_run_id,
                ProviderAttempt.run_attempt,
                ProviderAttempt.target_kind,
                ProviderAttempt.attempt_number,
            )
        )
    ).all()
    events = (
        await db.scalars(
            select(PipelineEvent)
            .where(PipelineEvent.pipeline_run_id == run.id)
            .order_by(PipelineEvent.sequence)
        )
    ).all()
    versions = (
        await db.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.pipeline_run_id == run.id)
            .order_by(ArticleVersion.version)
        )
    ).all()
    handoffs = (
        await db.scalars(
            select(AgentHandoff)
            .where(AgentHandoff.pipeline_run_id == run.id)
            .order_by(AgentHandoff.sequence)
        )
    ).all()
    execution_manifest = await ExecutionManifestService(db).safe_summary(run.id)
    return {
        "id": run.id,
        "project_id": run.project_id,
        "status": run.status,
        "current_stage": run.current_stage,
        "attempt": run.attempt,
        "retryable": run.retryable,
        "next_retry_at": run.next_retry_at,
        "cancellation_requested_at": run.cancellation_requested_at,
        "last_successful_checkpoint": run.last_successful_checkpoint,
        "error_code": run.error_code,
        "error_message": safe_public_message(run.error_message),
        "billed_prompt_tokens": int(getattr(run, "billed_prompt_tokens", 0) or 0),
        "billed_completion_tokens": int(
            getattr(run, "billed_completion_tokens", 0) or 0
        ),
        "estimated_external_cost_usd": float(
            getattr(run, "estimated_external_cost_usd", 0) or 0
        ),
        "checkpoints": [
            {
                "id": item.id,
                "sequence": item.sequence,
                "stage": item.stage,
                "next_stage": item.next_stage,
                "attempt": item.attempt,
                "contract_version": item.contract_version,
                "resumable": item.resumable,
                "completed_at": item.completed_at,
            }
            for item in checkpoints
        ],
        "transitions": [
            {
                "from": item.from_status,
                "to": item.to_status,
                "stage": item.stage,
                "origin": item.origin,
                "reason": safe_public_message(item.reason),
                "error_code": item.error_code,
                "created_at": item.created_at,
            }
            for item in transitions
        ],
        "agent_calls": [
            {
                "id": item.id,
                "role": item.agent_role,
                "attempt": item.attempt,
                "status": item.status,
                "provider": item.provider,
                "model": item.model,
                "fallback_used": item.fallback_used,
                "prompt_tokens": item.prompt_tokens,
                "completion_tokens": item.completion_tokens,
                "estimated_cost_usd": float(item.estimated_cost_usd or 0),
                "latency_ms": item.latency_ms,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
                "error": safe_public_message(item.error),
                "error_code": item.error_code,
                "error_category": item.error_category,
                "http_status": item.http_status,
                "retryable": item.retryable,
                "correlation_id": item.correlation_id,
                "recovered": item.recovered,
                "recovery_code": item.recovery_code,
                "recovered_by_agent_run_id": item.recovered_by_agent_run_id,
            }
            for item in agent_calls
        ],
        "provider_attempts": [
            {
                "id": item.id,
                "agent_run_id": item.agent_run_id,
                "provider": item.provider,
                "model": item.model,
                "target_kind": item.target_kind,
                "run_attempt": item.run_attempt,
                "attempt_number": item.attempt_number,
                "status": item.status,
                "response_received": item.response_received,
                "prompt_tokens": item.prompt_tokens,
                "completion_tokens": item.completion_tokens,
                "estimated_cost_usd": float(item.estimated_cost_usd or 0),
                "latency_ms": item.latency_ms,
                "http_status": item.http_status,
                "error_code": item.error_code,
                "error_category": item.error_category,
                "started_at": item.started_at,
                "finished_at": item.finished_at,
            }
            for item in provider_attempts
        ],
        "events": [
            {
                "sequence": item.sequence,
                "type": item.event_type,
                "stage": item.stage,
                "stage_occurrence_id": item.stage_occurrence_id,
                "research_cycle": item.research_cycle,
                "editor_cycle": item.editor_cycle,
                "run_attempt": item.run_attempt,
                "stage_attempt": item.stage_attempt,
                "checkpoint_sequence": item.checkpoint_sequence,
                "agent_run_id": item.agent_run_id,
                "payload": _public_event_payload(item.payload),
                "created_at": item.created_at,
            }
            for item in events
        ],
        "handoffs": [
            {
                "id": item.id,
                "sequence": item.sequence,
                "from_role": item.from_role,
                "to_role": item.to_role,
                "fact_ids": item.fact_ids,
                "created_at": item.created_at,
            }
            for item in handoffs
        ],
        "content_versions": [
            {
                "id": item.id,
                "article_id": item.article_id,
                "version": item.version,
                "editorial_status": item.editorial_status,
                "change_reason": item.change_reason,
                "final_markdown": item.final_markdown,
                "final_html": item.final_html,
                "seo_metadata": item.seo_metadata,
                "source_report": item.source_report,
                "created_at": item.created_at,
            }
            for item in versions
        ],
        "execution_manifest": execution_manifest,
    }


@router.post(
    "/pipeline-runs/{run_id}/resume",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def resume_pipeline_run(
    run_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    run = await db.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(404, "Pipeline run not found")
    project = await db.get(Project, run.project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    project_pipeline = getattr(
        getattr(project, "editorial_pipeline_version", "v2"),
        "value",
        getattr(project, "editorial_pipeline_version", "v2"),
    )
    await _require_run_start_readiness(
        request,
        db,
        project_pipeline,
        existing_manifest=True,
    )
    status_value = getattr(run.status, "value", run.status)
    if status_value not in {"queued", "waiting_retry"}:
        raise HTTPException(409, "Only queued or waiting_retry runs can be resumed")
    try:
        await ExecutionManifestService(db).required(run.id)
    except ExecutionManifestError as exc:
        raise HTTPException(
            409,
            {
                "error_code": exc.code,
                "message": "Fixed execution dependencies are unavailable",
            },
        ) from exc
    dispatch_status = await _dispatch_pipeline_run(run.id, origin="api.pipeline-resume")
    await db.refresh(run)
    return {
        "pipeline_run_id": run.id,
        "status": status_value,
        "next_retry_at": run.next_retry_at,
        "dispatch_status": dispatch_status,
    }


@router.post(
    "/pipeline-runs/{run_id}/cancel",
    response_model=PipelineRunCancellationRead,
    dependencies=[Depends(require_admin)],
)
async def cancel_pipeline_run(run_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    service = PipelineRunService(db)
    try:
        run = await service.request_cancellation(
            run_id,
            origin="admin.api",
        )
    except ValueError as exc:
        raise HTTPException(404, "Pipeline run not found") from exc
    except InvalidRunTransition as exc:
        current = await db.get(PipelineRun, run_id)
        current_status = current.status.value if current is not None else "unknown"
        raise HTTPException(
            409, f"Pipeline run is already terminal: {current_status}"
        ) from exc

    await db.commit()
    return {
        "pipeline_run_id": run.id,
        "status": run.status,
        "cancellation_requested_at": run.cancellation_requested_at,
        "cancellation_pending": run.status == PipelineRunStatus.running,
    }


@router.post(
    "/pipeline-runs/{run_id}/human-review",
    response_model=HumanEditorialReviewDecisionRead,
    dependencies=[Depends(require_admin)],
)
async def decide_human_editorial_review(
    run_id: uuid.UUID,
    payload: HumanEditorialReviewDecision,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    service = HumanEditorialReviewService(db)
    try:
        result = await service.decide(
            run_id,
            decision=payload.decision,
            reviewer=payload.reviewer,
            observation=payload.observation,
            idempotency_key=idempotency_key or "",
        )
    except HumanReviewInputError as exc:
        raise HTTPException(422, "Human review input is invalid") from exc
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise HTTPException(404, "Human review target not found") from exc
        raise HTTPException(422, "Human review input is invalid") from exc
    except HumanReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ExecutionManifestError as exc:
        await db.rollback()
        raise HTTPException(
            409,
            {
                "error_code": exc.code,
                "message": "Revision dependencies could not be fixed safely",
            },
        ) from exc
    except InvalidRunTransition as exc:
        raise HTTPException(409, "Human review transition is no longer valid") from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            409, "Human review decision conflicts with history"
        ) from exc

    await db.commit()
    if result.revision_run is not None and result.revision_created:
        from app.workers.tasks import run_pipeline

        dispatch = await dispatch_one(
            result.revision_run.id,
            run_pipeline,
            dispatcher_identity(origin="api.human-review-revision"),
        )
        if dispatch and dispatch.status == "failed":
            raise HTTPException(
                503,
                {
                    "message": "Revisão registrada, mas o worker não pôde ser acionado",
                    "pipeline_run_id": str(result.revision_run.id),
                },
            )
    return {
        "review": _human_review_payload(result.review, include_package=True),
        "pipeline_run_status": result.run.status,
        "revision_run_id": (
            result.revision_run.id if result.revision_run is not None else None
        ),
        "revision_created": result.revision_created,
        "duplicate": result.duplicate,
    }


@router.get(
    "/projects/{project_id}",
    response_model=ProjectDetailRead,
    dependencies=[Depends(require_admin)],
)
async def project_detail(
    project_id: uuid.UUID,
    pipeline_run_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    pipeline_runs = (
        await db.scalars(
            select(PipelineRun)
            .where(PipelineRun.project_id == project_id)
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        )
    ).all()
    latest_pipeline_run = pipeline_runs[0] if pipeline_runs else None
    selected_pipeline_run = latest_pipeline_run
    if pipeline_run_id is not None:
        selected_pipeline_run = next(
            (item for item in pipeline_runs if item.id == pipeline_run_id), None
        )
        if selected_pipeline_run is None:
            raise HTTPException(404, "Pipeline run not found for project")
    if selected_pipeline_run is not None:
        facts_count = await db.scalar(
            select(func.count(FactLedger.id)).where(
                FactLedger.project_id == project_id,
                FactLedger.pipeline_run_id == selected_pipeline_run.id,
            )
        )
        approved_count = await db.scalar(
            select(func.count(FactLedger.id)).where(
                FactLedger.project_id == project_id,
                FactLedger.pipeline_run_id == selected_pipeline_run.id,
                FactLedger.approved.is_(True),
            )
        )
        runs = (
            await db.scalars(
                select(AgentRun)
                .where(
                    AgentRun.project_id == project_id,
                    AgentRun.pipeline_run_id == selected_pipeline_run.id,
                )
                .order_by(AgentRun.created_at, AgentRun.id)
            )
        ).all()
    else:
        facts_count = 0
        approved_count = 0
        runs = []
    article = await db.scalar(select(Article).where(Article.project_id == project_id))
    article_version = None
    if article is not None and article.current_version > 0:
        article_version = await db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
            )
        )
    article_pipeline_run_id = (
        article_version.pipeline_run_id if article_version is not None else None
    )
    article_matches_selected_pipeline_run = (
        article_pipeline_run_id == selected_pipeline_run.id
        if article_version is not None and selected_pipeline_run is not None
        else None
    )
    human_reviews = list(
        (
            await db.scalars(
                select(HumanEditorialReview)
                .where(HumanEditorialReview.project_id == project_id)
                .order_by(
                    HumanEditorialReview.created_at.desc(),
                    HumanEditorialReview.id.desc(),
                )
            )
        ).all()
    )
    selected_human_review = (
        next(
            (
                review
                for review in human_reviews
                if review.pipeline_run_id == selected_pipeline_run.id
            ),
            None,
        )
        if selected_pipeline_run is not None
        else None
    )
    execution_manifest = (
        await ExecutionManifestService(db).safe_summary(selected_pipeline_run.id)
        if selected_pipeline_run is not None
        else None
    )
    quality_evaluation_row = (
        await db.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.pipeline_run_id == selected_pipeline_run.id
            )
        )
        if selected_pipeline_run is not None
        else None
    )
    quality_evaluation = quality_summary(
        quality_evaluation_row,
        human_decision=(
            selected_human_review.decision
            if selected_human_review is not None
            else None
        ),
    )
    gatekeeper_runs = [run for run in runs if run.agent_role == "research_gatekeeper"]
    research_diagnostic = _research_diagnostic(selected_pipeline_run, gatekeeper_runs)
    v3_research_runtime = None
    if selected_pipeline_run is not None:
        latest_checkpoint = await db.scalar(
            select(PipelineCheckpoint)
            .where(PipelineCheckpoint.pipeline_run_id == selected_pipeline_run.id)
            .order_by(PipelineCheckpoint.sequence.desc())
            .limit(1)
        )
        if latest_checkpoint is not None and isinstance(
            latest_checkpoint.state_json, dict
        ):
            checkpoint_state = latest_checkpoint.state_json
            metrics = dict(checkpoint_state.get("research_metrics") or {})
            if metrics.get("research_runtime_version"):
                v3_research_runtime = {
                    "version": metrics.get("research_runtime_version"),
                    "stage": checkpoint_state.get("stage"),
                    "blocking_code": checkpoint_state.get("blocking_code"),
                    "blocking_reason": safe_public_message(
                        checkpoint_state.get("blocking_reason")
                    ),
                    "research_intent": metrics.get("research_intent"),
                    "search_budget": metrics.get("search_budget"),
                    "provider_circuits": metrics.get("provider_circuits"),
                    "providers_used": metrics.get("providers_used", []),
                    "markets_by_task": metrics.get("markets_by_task", {}),
                    "languages_by_task": metrics.get("languages_by_task", {}),
                    "search_diagnostic_totals": metrics.get(
                        "search_diagnostic_totals", {}
                    ),
                    "source_fetch_count": metrics.get("source_fetch_count", 0),
                    "structured_source_count": metrics.get(
                        "structured_source_count", 0
                    ),
                    "source_recovery_round": checkpoint_state.get(
                        "source_recovery_round", 0
                    ),
                    "source_recovery_exhausted": checkpoint_state.get(
                        "source_recovery_exhausted", False
                    ),
                    "source_coverage": checkpoint_state.get("source_coverage_report"),
                }
    editorial_diagnostic = _editorial_diagnostic(runs)
    selected_audit = gatekeeper_runs[-1].output_json if gatekeeper_runs else None

    def pipeline_run_summary(item: PipelineRun | None):
        if item is None:
            return None
        return {
            "id": item.id,
            "status": item.status,
            "trigger_type": item.trigger_type,
            "current_stage": item.current_stage,
            "attempt": item.attempt,
            "retryable": item.retryable,
            "next_retry_at": item.next_retry_at,
            "cancellation_requested_at": item.cancellation_requested_at,
            "last_successful_checkpoint": item.last_successful_checkpoint,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
            "error_code": item.error_code,
            "error_message": safe_public_message(getattr(item, "error_message", None)),
            "outcome_code": _research_outcome_code(
                item,
                selected_audit
                if selected_pipeline_run is not None
                and item.id == selected_pipeline_run.id
                else None,
            ),
        }

    def agent_run_purpose(item: AgentRun) -> str | None:
        input_json = getattr(item, "input_json", None)
        return input_json.get("purpose") if isinstance(input_json, dict) else None

    return {
        "project": _project_read(
            project,
            latest_pipeline_run.status if latest_pipeline_run is not None else None,
        ),
        "outcome_code": (
            research_diagnostic.get("outcome_code")
            if research_diagnostic is not None
            else None
        ),
        "facts": {
            "pipeline_run_id": selected_pipeline_run.id
            if selected_pipeline_run is not None
            else None,
            "total": facts_count,
            "approved": approved_count,
        },
        "pipeline_runs": [pipeline_run_summary(item) for item in pipeline_runs],
        "latest_pipeline_run": pipeline_run_summary(latest_pipeline_run),
        "selected_pipeline_run": pipeline_run_summary(selected_pipeline_run),
        "runs": [
            {
                "id": r.id,
                "pipeline_run_id": r.pipeline_run_id,
                "role": r.agent_role,
                "purpose": agent_run_purpose(r),
                "status": r.status,
                "decision": r.decision,
                "latency_ms": r.latency_ms,
                "cost": float(r.estimated_cost_usd or 0),
                "error": safe_public_message(getattr(r, "error", None)),
                "error_code": getattr(r, "error_code", None),
                "error_category": getattr(r, "error_category", None),
                "http_status": getattr(r, "http_status", None),
                "retryable": getattr(r, "retryable", None),
                "correlation_id": getattr(r, "correlation_id", None),
                "recovered": getattr(r, "recovered", False),
                "recovery_code": getattr(r, "recovery_code", None),
                "recovered_by_agent_run_id": getattr(
                    r, "recovered_by_agent_run_id", None
                ),
            }
            for r in runs
        ],
        "article_version": {
            "id": article_version.id,
            "article_id": article_version.article_id,
            "pipeline_run_id": article_version.pipeline_run_id,
            "version": article_version.version,
            "title": article_version.title,
            "outline": article_version.outline,
            "editorial_status": article_version.editorial_status,
            "markdown": article_version.final_markdown,
            "html": article_version.final_html,
            "seo_metadata": article_version.seo_metadata,
            "source_report": article_version.source_report,
            "created_at": article_version.created_at,
            "updated_at": article_version.updated_at,
        }
        if article_version is not None
        else None,
        "article_pipeline_run_id": article_pipeline_run_id,
        "article_matches_selected_pipeline_run": article_matches_selected_pipeline_run,
        "execution_manifest": execution_manifest,
        "quality_evaluation": quality_evaluation,
        "research_diagnostic": research_diagnostic,
        "v3_research_runtime": v3_research_runtime,
        "editorial_diagnostic": editorial_diagnostic,
        "human_review": (
            _human_review_payload(selected_human_review, include_package=True)
            if selected_human_review is not None
            else None
        ),
        "human_review_history": [
            _human_review_payload(review, include_package=False)
            for review in human_reviews
        ],
    }


@router.get(
    "/projects/{project_id}/export",
    dependencies=[Depends(require_admin)],
)
async def export_project_package(
    project_id: uuid.UUID,
    draft: bool = False,
    db: AsyncSession = Depends(get_db),
):
    package = await EditorialExportService(db).build(project_id, draft=draft)
    return Response(
        content=package.content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{package.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/projects/{project_id}/facts",
    response_model=list[FactRead],
    dependencies=[Depends(require_admin)],
)
async def list_facts(
    project_id: uuid.UUID,
    pipeline_run_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    if pipeline_run_id is None:
        pipeline_run_id = await db.scalar(
            select(PipelineRun.id)
            .where(PipelineRun.project_id == project_id)
            .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
            .limit(1)
        )
    else:
        selected_run = await db.scalar(
            select(PipelineRun).where(
                PipelineRun.id == pipeline_run_id,
                PipelineRun.project_id == project_id,
            )
        )
        if selected_run is None:
            raise HTTPException(404, "Pipeline run not found for project")
    if pipeline_run_id is None:
        return []
    facts = (
        await db.scalars(
            select(FactLedger)
            .where(
                FactLedger.project_id == project_id,
                FactLedger.pipeline_run_id == pipeline_run_id,
            )
            .order_by(FactLedger.created_at)
        )
    ).all()
    return [
        {
            "id": f.id,
            "project_id": f.project_id,
            "pipeline_run_id": f.pipeline_run_id,
            "claim": f.claim_text,
            "source_id": f.source_id,
            "source_snapshot_id": f.source_snapshot_id,
            "confidence": f.confidence_score,
            "approved": f.approved,
            "locator": f.source_locator,
            "conflict_group": f.conflict_group,
        }
        for f in facts
    ]


@router.get(
    "/dashboard",
    response_model=DashboardRead,
    dependencies=[Depends(require_admin)],
)
async def dashboard(db: AsyncSession = Depends(get_db)):
    total = await db.scalar(select(func.count(Project.id))) or 0
    completed = (
        await db.scalar(
            select(func.count(Project.id)).where(Project.status == "completed")
        )
        or 0
    )
    failed_runs = (
        await db.scalar(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.status == PipelineRunStatus.failed
            )
        )
        or 0
    )
    blocked_runs = (
        await db.scalar(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.status == PipelineRunStatus.blocked
            )
        )
        or 0
    )
    cancelled_runs = (
        await db.scalar(
            select(func.count(PipelineRun.id)).where(
                PipelineRun.status == PipelineRunStatus.cancelled
            )
        )
        or 0
    )
    facts = (
        await db.scalar(
            select(func.count(FactLedger.id)).where(FactLedger.approved.is_(True))
        )
        or 0
    )
    source_count = (
        await db.scalar(select(func.count(func.distinct(FactLedger.source_id)))) or 0
    )
    cost = (
        await db.scalar(select(func.coalesce(func.sum(AgentRun.estimated_cost_usd), 0)))
        or 0
    )
    latest_status = _latest_run_status().label("last_run_status")
    projects = (
        await db.execute(
            select(Project, latest_status).order_by(Project.updated_at.desc()).limit(8)
        )
    ).all()
    return {
        "stats": {
            "total_projects": total,
            "completed": completed,
            "blocked_runs": blocked_runs,
            "failed_runs": failed_runs,
            "cancelled_runs": cancelled_runs,
            "approved_facts": facts,
            "distinct_sources": source_count,
            "total_cost_usd": float(cost),
        },
        "recent_projects": [
            _project_read(project, status) for project, status in projects
        ],
    }


@router.put(
    "/config/credentials/{provider}",
    response_model=CredentialRead,
    dependencies=[Depends(require_admin)],
)
async def save_credential(
    provider: CredentialProvider,
    payload: CredentialWrite,
    db: AsyncSession = Depends(get_db),
):
    if payload.provider != provider.value:
        raise HTTPException(400, "Provider in path and body must match")
    try:
        vault = CredentialVault()
    except VaultError as exc:
        raise HTTPException(503, str(exc)) from exc
    credential = await db.scalar(
        select(Credential).where(Credential.provider == provider)
    )
    if credential:
        credential.encrypted_value = vault.encrypt(payload.value)
        credential.last_four = payload.value[-4:]
        credential.verified_at = None
    else:
        credential = Credential(
            provider=provider,
            encrypted_value=vault.encrypt(payload.value),
            last_four=payload.value[-4:],
        )
        db.add(credential)
    if provider.value in DEFAULT_MODELS:
        existing_roles = set(await db.scalars(select(ModelRoute.agent_role)))
        for role in AGENT_ROLES:
            if role not in existing_roles:
                db.add(ModelRoute(**_default_route_for_provider(provider.value, role)))
    await db.commit()
    return CredentialRead(
        provider=provider.value,
        configured=True,
        last_four=credential.last_four,
        verified_at=credential.verified_at,
    )


@router.post(
    "/config/credentials/{provider}/verify",
    response_model=CredentialVerificationRead,
    dependencies=[Depends(require_admin)],
)
async def verify_credential(
    provider: CredentialProvider,
    db: AsyncSession = Depends(get_db),
):
    credential = await db.scalar(
        select(Credential).where(
            Credential.provider == provider,
            Credential.active.is_(True),
        )
    )
    if credential is None:
        raise HTTPException(404, "Credencial não configurada.")
    try:
        api_key = CredentialVault().decrypt(credential.encrypted_value)
    except VaultError as exc:
        raise HTTPException(503, "Credencial indisponível para verificação.") from exc

    model = None
    if provider in {
        CredentialProvider.openai,
        CredentialProvider.anthropic,
        CredentialProvider.gemini,
    }:
        model = await db.scalar(
            select(ModelRoute.primary_model)
            .where(ModelRoute.primary_provider == provider.value)
            .order_by(ModelRoute.agent_role)
            .limit(1)
        )
        model = model or DEFAULT_MODELS[provider.value]
    result = await CredentialVerificationService().verify(
        provider=provider.value,
        api_key=api_key,
        model=model,
    )
    credential.verified_at = result.verified_at
    await db.commit()
    return CredentialVerificationRead(
        provider=result.provider,
        verified=result.verified,
        verified_at=result.verified_at,
        latency_ms=result.latency_ms,
        model=result.model,
        error_code=result.error_code,
    )


@router.get(
    "/config/credentials",
    response_model=list[CredentialRead],
    dependencies=[Depends(require_admin)],
)
async def list_credentials(db: AsyncSession = Depends(get_db)):
    configured = {
        x.provider.value: x for x in (await db.scalars(select(Credential))).all()
    }
    return [
        CredentialRead(
            provider=p.value,
            configured=p.value in configured,
            last_four=configured[p.value].last_four if p.value in configured else None,
            verified_at=configured[p.value].verified_at
            if p.value in configured
            else None,
        )
        for p in CredentialProvider
    ]


@router.put("/config/routes/{agent_role}", dependencies=[Depends(require_admin)])
async def save_model_route(
    agent_role: str, payload: ModelRouteWrite, db: AsyncSession = Depends(get_db)
):
    if payload.model_extra:
        raise HTTPException(422, ModelRoutePolicyError.public_detail)
    agent_role = sanitize_nul(agent_role, strip_escaped=True)
    clean_payload = sanitize_nul(payload.model_dump(), strip_escaped=True)
    if agent_role != clean_payload["agent_role"]:
        raise HTTPException(400, "Agent role mismatch")
    try:
        clean_payload = apply_known_model_profile(clean_payload)
        clean_payload = normalize_model_route_configuration(clean_payload)
    except ModelRoutePolicyError as exc:
        raise HTTPException(422, exc.public_detail) from None
    route = await db.scalar(
        select(ModelRoute).where(ModelRoute.agent_role == agent_role)
    )
    if route:
        for key, value in clean_payload.items():
            if key == "agent_role":
                continue
            setattr(route, key, value)
    else:
        route = ModelRoute(**clean_payload)
        db.add(route)
    await db.commit()
    return {"saved": True, **clean_payload}


@router.get(
    "/config",
    response_model=ConfigRead,
    dependencies=[Depends(require_admin)],
)
async def get_config(db: AsyncSession = Depends(get_db)):
    routes = (
        await db.scalars(select(ModelRoute).order_by(ModelRoute.agent_role))
    ).all()
    skills = (await db.scalars(select(Skill).order_by(Skill.skill_id))).all()
    superior_skills = (
        await db.scalars(select(SuperiorSkill).order_by(SuperiorSkill.skill_id))
    ).all()
    embedding_route = await db.scalar(
        select(EmbeddingRoute).where(EmbeddingRoute.active.is_(True))
    )
    return {
        "routes": [
            {
                "agent_role": r.agent_role,
                "primary_provider": r.primary_provider,
                "primary_model": r.primary_model,
                "fallback_provider": r.fallback_provider,
                "fallback_model": r.fallback_model,
                "parameters": dict(r.parameters or {}),
            }
            for r in routes
        ],
        "route_defaults": {
            provider: {
                role: _default_route_for_provider(provider, role)
                for role in AGENT_ROLES
            }
            for provider in DEFAULT_MODELS
        },
        "skills": [
            {
                "id": s.id,
                "skill_id": s.skill_id,
                "kind": s.kind,
                "version": s.current_version,
                "enabled": s.enabled,
                "stable": s.stable,
                "niche": s.niche,
                "project_id": s.project_id,
                "fingerprint": s.fingerprint,
                "lifecycle_status": s.lifecycle_status,
                "auto_inject": s.auto_inject,
            }
            for s in skills
        ],
        "superior_skills": [
            {
                "skill_id": s.skill_id,
                "scope": s.scope,
                "agent_role": s.agent_role,
                "version": s.current_version,
                "enabled": s.enabled,
            }
            for s in superior_skills
        ],
        "embedding_route": {
            "provider": embedding_route.provider,
            "model": embedding_route.model,
            "dimensions": embedding_route.dimensions,
        }
        if embedding_route
        else None,
        "policy": {
            "learned_skill_stability_threshold": settings.learned_skill_stability_threshold,
            "learned_skill_min_independent_articles": (
                settings.learned_skill_min_independent_articles
            ),
            "auto_inject_unstable_skills": False,
            "superior_skills_mode": settings.superior_skills_mode,
        },
    }


@router.get(
    "/admin/learned-skills/{skill_id}/lifecycle",
    dependencies=[Depends(require_admin)],
)
async def get_learned_skill_lifecycle(
    skill_id: str, db: AsyncSession = Depends(get_db)
):
    skill = await db.scalar(
        select(Skill).where(
            Skill.skill_id == skill_id,
            Skill.kind == SkillKind.learned,
        )
    )
    if skill is None:
        raise HTTPException(404, "Learned skill not found")
    version = await db.scalar(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill.id,
            SkillVersion.version == skill.current_version,
        )
    )
    if version is None:
        raise HTTPException(409, "Learned skill current version is missing")
    validations = list(
        (
            await db.scalars(
                select(SkillValidation)
                .where(SkillValidation.skill_version_id == version.id)
                .order_by(SkillValidation.created_at, SkillValidation.id)
            )
        ).all()
    )
    events = list(
        (
            await db.scalars(
                select(SkillLifecycleEvent)
                .where(SkillLifecycleEvent.skill_id == skill.id)
                .order_by(SkillLifecycleEvent.created_at, SkillLifecycleEvent.id)
            )
        ).all()
    )
    return {
        "skill": {
            "skill_id": skill.skill_id,
            "project_id": skill.project_id,
            "niche": skill.niche,
            "fingerprint": skill.fingerprint,
            "version": version.version,
            "lifecycle_status": skill.lifecycle_status,
            "validation_count": version.validation_count,
            "reviewed_by_human": version.reviewed_by_human,
            "stable": skill.stable,
            "enabled": skill.enabled,
            "auto_inject": skill.auto_inject,
        },
        "validations": [
            {
                "id": row.id,
                "pipeline_run_id": row.pipeline_run_id,
                "article_id": row.article_id,
                "article_version_id": row.article_version_id,
                "evidence_source": row.evidence_source,
                "editorial_rework_count": row.editorial_rework_count,
                "rubric_score": row.rubric_score,
                "factual_regression": row.factual_regression,
                "corroborating": row.corroborating,
                "outcome": safe_public_payload(row.outcome_json),
                "created_at": row.created_at,
            }
            for row in validations
        ],
        "events": [
            {
                "id": row.id,
                "from_status": row.from_status,
                "to_status": row.to_status,
                "action": row.action,
                "actor": row.actor,
                "reason": row.reason,
                "pipeline_run_id": row.pipeline_run_id,
                "article_id": row.article_id,
                "details": safe_public_payload(row.details),
                "created_at": row.created_at,
            }
            for row in events
        ],
    }


@router.post(
    "/admin/learned-skills/{skill_id}/lifecycle",
    dependencies=[Depends(require_admin)],
)
async def update_learned_skill_lifecycle(
    skill_id: str,
    payload: LearnedSkillLifecycleAction,
    db: AsyncSession = Depends(get_db),
):
    service = SkillLearningService(db)
    try:
        skill, version = await service.apply_action(
            skill_id,
            payload.action,
            reason=payload.reason,
        )
    except SkillLearningInputError as exc:
        raise HTTPException(404, "Learned skill not found") from exc
    except SkillLifecycleConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    await db.commit()
    return {
        "skill_id": skill.skill_id,
        "version": version.version,
        "lifecycle_status": skill.lifecycle_status,
        "stable": skill.stable,
        "enabled": skill.enabled,
        "auto_inject": skill.auto_inject,
    }


@router.get(
    "/admin/superior-skills",
    dependencies=[Depends(require_admin)],
)
async def list_superior_skills(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(select(SuperiorSkill).order_by(SuperiorSkill.skill_id))
    ).all()
    return [
        {
            "id": row.id,
            "skill_id": row.skill_id,
            "scope": row.scope,
            "agent_role": row.agent_role,
            "enabled": row.enabled,
            "current_version": row.current_version,
        }
        for row in rows
    ]


@router.get(
    "/admin/superior-skills/{skill_id}/versions",
    dependencies=[Depends(require_admin)],
)
async def list_superior_skill_versions(
    skill_id: str, db: AsyncSession = Depends(get_db)
):
    skill = await db.scalar(
        select(SuperiorSkill).where(SuperiorSkill.skill_id == skill_id)
    )
    if not skill:
        raise HTTPException(404, "Superior skill not found")
    versions = (
        await db.scalars(
            select(SuperiorSkillVersion)
            .where(SuperiorSkillVersion.superior_skill_id == skill.id)
            .order_by(SuperiorSkillVersion.created_at.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "version": row.version,
            "status": row.status,
            "checksum": row.checksum,
            "definition": row.definition,
            "reviewed_by_human": row.reviewed_by_human,
            "approved_at": row.approved_at,
            "created_by": row.created_by,
            "created_at": row.created_at,
        }
        for row in versions
    ]


@router.post(
    "/admin/superior-skills/{skill_id}/versions",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_superior_skill_version(
    skill_id: str,
    payload: SuperiorSkillVersionWrite,
    db: AsyncSession = Depends(get_db),
):
    skill = await db.scalar(
        select(SuperiorSkill).where(SuperiorSkill.skill_id == skill_id)
    )
    if not skill:
        raise HTTPException(404, "Superior skill not found")
    definition_data = {
        **payload.definition,
        "skill_id": skill_id,
        "version": payload.version,
    }
    try:
        definition = SuperiorSkillDefinition.model_validate(definition_data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if (
        definition.agent_role != skill.agent_role
        or definition.scope != skill.scope.value
    ):
        raise HTTPException(409, "Definition scope or role does not match the skill")
    existing = await db.scalar(
        select(SuperiorSkillVersion).where(
            SuperiorSkillVersion.superior_skill_id == skill.id,
            SuperiorSkillVersion.version == payload.version,
        )
    )
    if existing:
        raise HTTPException(409, "Version already exists")
    row = SuperiorSkillVersion(
        superior_skill_id=skill.id,
        version=payload.version,
        definition=definition.model_dump(mode="json"),
        checksum=definition.checksum(),
        status="draft",
        created_by=payload.created_by,
    )
    db.add(row)
    await db.commit()
    return {"id": row.id, "version": row.version, "status": row.status}


@router.post(
    "/admin/superior-skills/{skill_id}/versions/{version}/activate",
    dependencies=[Depends(require_admin)],
)
async def activate_superior_skill_version(
    skill_id: str, version: str, db: AsyncSession = Depends(get_db)
):
    skill = await db.scalar(
        select(SuperiorSkill)
        .where(SuperiorSkill.skill_id == skill_id)
        .with_for_update()
    )
    if not skill:
        raise HTTPException(404, "Superior skill not found")
    target = await db.scalar(
        select(SuperiorSkillVersion).where(
            SuperiorSkillVersion.superior_skill_id == skill.id,
            SuperiorSkillVersion.version == version,
        )
    )
    if not target:
        raise HTTPException(404, "Version not found")
    SuperiorSkillDefinition.model_validate(target.definition)
    prior = await db.scalar(
        select(SuperiorSkillVersion).where(
            SuperiorSkillVersion.superior_skill_id == skill.id,
            SuperiorSkillVersion.version == skill.current_version,
        )
    )
    if prior and prior.id != target.id:
        prior.status = "superseded"
    target.status = "active"
    target.reviewed_by_human = True
    target.approved_at = datetime.now(timezone.utc)
    skill.current_version = version
    await db.commit()
    return {"skill_id": skill_id, "active_version": version}


@router.post(
    "/admin/agent-context/preview",
    dependencies=[Depends(require_admin)],
)
async def preview_agent_context(
    request: AgentContextPreviewRequest,
    db: AsyncSession = Depends(get_db),
):
    if request.agent_role not in AGENT_ROLES:
        raise HTTPException(404, "Unknown agent role")
    if not await db.get(Project, request.project_id):
        raise HTTPException(404, "Project not found")
    if request.pipeline_run_id is not None:
        pipeline_run = await db.get(PipelineRun, request.pipeline_run_id)
        if pipeline_run is None or pipeline_run.project_id != request.project_id:
            raise HTTPException(404, "Pipeline run not found for project")
    composed = await AgentContextComposer(db).compose(
        request.agent_role,
        request.project_id,
        request.task,
        pipeline_run_id=request.pipeline_run_id,
        allow_external_embeddings=False,
    )
    safe_metadata = safe_public_payload(composed.metadata)
    safe_metadata = {
        key: safe_metadata[key]
        for key in _AGENT_CONTEXT_PREVIEW_METADATA_FIELDS
        if key in safe_metadata
    }
    return {
        "mode": safe_metadata.get("mode", "unknown"),
        "metadata": safe_metadata,
        "preview": _safe_agent_context_preview(composed.prompt),
        "compiled_context": _safe_agent_context_preview(composed.superior_fragment),
    }


def _safe_agent_context_preview(value: str) -> str:
    redacted = sanitize_nul(str(redact_sensitive(value)), strip_escaped=True)
    redacted = _CONTEXT_SECRET_ASSIGNMENT.sub(r"\1***", redacted)
    return _CONTEXT_PROVIDER_KEY.sub("***", redacted)


@router.get(
    "/admin/memories",
    dependencies=[Depends(require_admin)],
)
async def list_agent_memories(
    agent_role: str | None = None,
    memory_status: LearningStatus | None = None,
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(AgentMemory).order_by(AgentMemory.created_at.desc())
    if agent_role:
        query = query.where(AgentMemory.agent_role == agent_role)
    if memory_status:
        query = query.where(AgentMemory.status == memory_status)
    if project_id:
        query = query.where(AgentMemory.project_id == project_id)
    rows = (await db.scalars(query)).all()
    return [
        {
            "id": row.id,
            "agent_role": row.agent_role,
            "project_id": row.project_id,
            "niche": row.niche,
            "kind": row.memory_kind,
            "content": row.content,
            "confidence": row.confidence_score,
            "status": row.status,
            "source_type": row.source_type,
            "source_id": row.source_id,
            "origin_pipeline_run_id": row.origin_pipeline_run_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.post(
    "/admin/memories",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_agent_memory(
    payload: AgentMemoryWrite, db: AsyncSession = Depends(get_db)
):
    clean_payload = sanitize_nul(payload.model_dump(), strip_escaped=True)
    if payload.agent_role not in AGENT_ROLES:
        raise HTTPException(422, "Unknown agent role")
    if payload.project_id and not await db.get(Project, payload.project_id):
        raise HTTPException(404, "Project not found")
    row = AgentMemory(
        **clean_payload,
        source_type="human",
        status=LearningStatus.quarantine,
    )
    db.add(row)
    await db.commit()
    return {"id": row.id, "status": row.status}


@router.post(
    "/admin/memories/{memory_id}/decision",
    dependencies=[Depends(require_admin)],
)
async def decide_agent_memory(
    memory_id: uuid.UUID,
    payload: LearningDecisionWrite,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(AgentMemory, memory_id)
    if not row:
        raise HTTPException(404, "Memory not found")
    row.status = LearningStatus(payload.decision)
    if payload.decision == "approved":
        try:
            embedding = await EmbeddingGateway().embed(db, row.content)
        except (EmbeddingError, httpx.HTTPError, KeyError):
            embedding = None
        if embedding:
            row.embedding = embedding.values
            row.embedding_provider = embedding.provider
            row.embedding_model = embedding.model
            row.embedding_dimensions = len(embedding.values)
    await db.commit()
    return {"id": row.id, "status": row.status}


@router.get(
    "/admin/style-sources",
    dependencies=[Depends(require_admin)],
)
async def list_style_sources(
    source_status: LearningStatus | None = None,
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(StyleSource).order_by(StyleSource.created_at.desc())
    if source_status:
        query = query.where(StyleSource.status == source_status)
    if project_id:
        query = query.where(StyleSource.project_id == project_id)
    rows = (await db.scalars(query)).all()
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "url": row.canonical_url,
            "title": row.title,
            "publisher": row.publisher,
            "domain": row.domain,
            "status": row.status,
            "excerpts": row.excerpts,
            "origin_pipeline_run_id": row.origin_pipeline_run_id,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.post(
    "/admin/style-sources",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_style_source(
    payload: StyleSourceWrite, db: AsyncSession = Depends(get_db)
):
    clean_payload = sanitize_nul(payload.model_dump(), strip_escaped=True)
    if payload.project_id and not await db.get(Project, payload.project_id):
        raise HTTPException(404, "Project not found")
    url = canonicalize_url(clean_payload["url"])
    domain = urlsplit(url).netloc
    if not url.startswith(("http://", "https://")) or not domain:
        raise HTTPException(422, "Invalid source URL")
    content = clean_payload["content"]
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = await db.scalar(
        select(StyleSource).where(
            StyleSource.project_id == clean_payload["project_id"],
            StyleSource.canonical_url == url,
            StyleSource.content_hash == digest,
        )
    )
    if existing:
        return {"id": existing.id, "status": existing.status, "duplicate": True}
    excerpts = [
        content[index : index + 300].strip()
        for index in range(0, min(len(content), 900), 300)
        if content[index : index + 300].strip()
    ]
    row = StyleSource(
        project_id=clean_payload["project_id"],
        canonical_url=url,
        title=clean_payload["title"],
        publisher=domain,
        domain=domain,
        content_hash=digest,
        excerpts=excerpts,
        metadata_json={"ingestion": "manual", "raw_content_stored": False},
    )
    db.add(row)
    await db.commit()
    return {"id": row.id, "status": row.status, "duplicate": False}


@router.post(
    "/admin/style-sources/{source_id}/decision",
    dependencies=[Depends(require_admin)],
)
async def decide_style_source(
    source_id: uuid.UUID,
    payload: LearningDecisionWrite,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(StyleSource, source_id)
    if not row:
        raise HTTPException(404, "Style source not found")
    row.status = LearningStatus(payload.decision)
    await db.commit()
    return {"id": row.id, "status": row.status}


@router.post(
    "/admin/projects/{project_id}/style-discovery",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def start_style_discovery(
    project_id: uuid.UUID, db: AsyncSession = Depends(get_db)
):
    if not await db.get(Project, project_id):
        raise HTTPException(404, "Project not found")
    from app.workers.tasks import discover_style_patterns

    discover_style_patterns.delay(str(project_id))
    return {"project_id": project_id, "status": "queued"}


@router.get(
    "/admin/style-patterns",
    dependencies=[Depends(require_admin)],
)
async def list_style_patterns(
    pattern_status: LearningStatus | None = None,
    target_agent_role: str | None = None,
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(StylePattern).order_by(StylePattern.created_at.desc())
    if pattern_status:
        query = query.where(StylePattern.status == pattern_status)
    if target_agent_role:
        query = query.where(StylePattern.target_agent_role == target_agent_role)
    if project_id:
        query = query.where(StylePattern.project_id == project_id)
    rows = (await db.scalars(query)).all()
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "target_agent_role": row.target_agent_role,
            "niche": row.niche,
            "pattern_type": row.pattern_type,
            "description": row.description,
            "source_ids": row.source_ids,
            "independent_domain_count": row.independent_domain_count,
            "validation_count": row.validation_count,
            "status": row.status,
            "origin_pipeline_run_id": row.origin_pipeline_run_id,
            "approved_at": row.approved_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in rows
    ]


@router.post(
    "/admin/style-patterns/{pattern_id}/decision",
    dependencies=[Depends(require_admin)],
)
async def decide_style_pattern(
    pattern_id: uuid.UUID,
    payload: LearningDecisionWrite,
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(StylePattern, pattern_id)
    if not row:
        raise HTTPException(404, "Style pattern not found")
    if payload.decision == "approved" and (
        row.independent_domain_count < 3 or row.validation_count < 1
    ):
        raise HTTPException(409, "Pattern needs three domains and one validation")
    row.status = LearningStatus(payload.decision)
    row.approved_at = (
        datetime.now(timezone.utc) if payload.decision == "approved" else None
    )
    await db.commit()
    return {"id": row.id, "status": row.status}


@router.put(
    "/admin/embedding-route",
    dependencies=[Depends(require_admin)],
)
async def save_embedding_route(
    payload: EmbeddingRouteWrite, db: AsyncSession = Depends(get_db)
):
    clean_payload = sanitize_nul(payload.model_dump(), strip_escaped=True)
    if db.bind and db.bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(84736291)"))
    await db.execute(select(EmbeddingRoute.id).with_for_update())
    for row in (await db.scalars(select(EmbeddingRoute))).all():
        row.active = False
    route = await db.scalar(
        select(EmbeddingRoute).where(
            EmbeddingRoute.provider == clean_payload["provider"],
            EmbeddingRoute.model == clean_payload["model"],
        )
    )
    if route:
        route.dimensions = clean_payload["dimensions"]
        route.active = True
    else:
        route = EmbeddingRoute(**clean_payload, active=True)
        db.add(route)
    await db.commit()
    return {"provider": route.provider, "model": route.model, "active": True}


@router.post(
    "/admin/embeddings/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_admin)],
)
async def reindex_approved_learning():
    from app.workers.tasks import reindex_learning_embeddings

    reindex_learning_embeddings.delay()
    return {"status": "queued"}


@router.post(
    "/projects/{project_id}/events/ticket",
    response_model=WebSocketTicketRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_project_events_ticket(
    project_id: uuid.UUID,
    payload: WebSocketTicketRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    ticket_store: WebSocketTicketStore = Depends(get_websocket_ticket_store),
):
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    pipeline_run = await db.get(PipelineRun, payload.pipeline_run_id)
    if pipeline_run is None or pipeline_run.project_id != project_id:
        raise HTTPException(404, "Pipeline run not found for project")
    try:
        ticket = await ticket_store.issue(project_id, payload.pipeline_run_id)
    except WebSocketTicketUnavailable as exc:
        raise HTTPException(
            503, "Não foi possível autorizar a conexão em tempo real."
        ) from exc
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {
        "ticket": ticket.value,
        "expires_in": ticket.expires_in,
        "protocol": ticket.protocol,
    }


def _websocket_ticket(websocket: WebSocket) -> str | None:
    offered = [
        protocol.strip()
        for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if protocol.strip()
    ]
    if len(offered) != 2 or offered[0] != WEBSOCKET_SUBPROTOCOL:
        return None
    return offered[1]


def _subscription_cursor(payload: object) -> int:
    if not isinstance(payload, dict) or set(payload) != {"type", "after_sequence"}:
        raise ValueError("Invalid subscription")
    after_sequence = payload["after_sequence"]
    if (
        payload["type"] != "subscribe"
        or isinstance(after_sequence, bool)
        or not isinstance(after_sequence, int)
        or after_sequence < 0
        or after_sequence > 2_147_483_647
    ):
        raise ValueError("Invalid subscription")
    return after_sequence


def _event_payload(event: PipelineEvent) -> dict:
    return {
        "sequence": event.sequence,
        "pipeline_run_id": str(event.pipeline_run_id),
        "type": event.event_type,
        "stage": event.stage,
        "stage_occurrence_id": (
            str(event.stage_occurrence_id) if event.stage_occurrence_id else None
        ),
        "research_cycle": event.research_cycle,
        "editor_cycle": event.editor_cycle,
        "run_attempt": event.run_attempt,
        "stage_attempt": event.stage_attempt,
        "checkpoint_sequence": event.checkpoint_sequence,
        "agent_run_id": str(event.agent_run_id) if event.agent_run_id else None,
        "payload": safe_public_payload(event.payload),
        "created_at": event.created_at.isoformat(),
    }


@router.websocket("/projects/{project_id}/events")
async def project_events(
    websocket: WebSocket,
    project_id: uuid.UUID,
    ticket_store: WebSocketTicketStore = Depends(get_websocket_ticket_store),
):
    requested_run = websocket.query_params.get("pipeline_run_id")
    try:
        if not requested_run:
            raise ValueError
        pipeline_run_id = uuid.UUID(requested_run)
    except (TypeError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    ticket = _websocket_ticket(websocket)
    if ticket is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    try:
        authorized = await ticket_store.consume(ticket, project_id, pipeline_run_id)
    except WebSocketTicketUnavailable:
        authorized = False
    if not authorized:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await websocket.accept(subprotocol=WEBSOCKET_SUBPROTOCOL)
    try:
        subscription = await asyncio.wait_for(
            websocket.receive_json(), timeout=WEBSOCKET_SUBSCRIBE_TIMEOUT_SECONDS
        )
        last_sequence = _subscription_cursor(subscription)
    except WebSocketDisconnect:
        return
    except (TimeoutError, TypeError, ValueError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    initial_replay = last_sequence == 0
    try:
        while True:
            async with SessionLocal() as db:
                query = (
                    select(PipelineEvent)
                    .where(
                        PipelineEvent.project_id == project_id,
                        PipelineEvent.pipeline_run_id == pipeline_run_id,
                        PipelineEvent.sequence > last_sequence,
                    )
                    .order_by(
                        PipelineEvent.sequence.desc()
                        if initial_replay
                        else PipelineEvent.sequence
                    )
                    .limit(WEBSOCKET_EVENT_BATCH_SIZE)
                )
                rows = (await db.scalars(query)).all()
                events = sorted(
                    (
                        event
                        for event in rows
                        if event.project_id == project_id
                        and event.pipeline_run_id == pipeline_run_id
                        and event.sequence > last_sequence
                    ),
                    key=lambda event: event.sequence,
                )
                if initial_replay:
                    events = events[-WEBSOCKET_EVENT_BATCH_SIZE:]
                else:
                    events = events[:WEBSOCKET_EVENT_BATCH_SIZE]
                if events:
                    batch_after_sequence = last_sequence
                    last_sequence = events[-1].sequence
                    await websocket.send_json(
                        {
                            "type": "events.batch",
                            "pipeline_run_id": str(pipeline_run_id),
                            "after_sequence": batch_after_sequence,
                            "last_sequence": last_sequence,
                            "events": [_event_payload(event) for event in events],
                        }
                    )
                initial_replay = False
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
