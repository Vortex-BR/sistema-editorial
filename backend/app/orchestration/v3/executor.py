"""Executable Editorial Intelligence V3 orchestration.

This pipeline is knowledge-first: the writer is unreachable until the ordered
knowledge contract, source policy, corroborated claims, method dossiers,
decision matrix, and external references pass deterministic validation.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import (
    Article,
    ArticleVersion,
    EditorialPipelineVersion,
    PipelineRun,
    PipelineRunStatus,
    ProjectStatus,
    V3KnowledgeClaimRecord,
    V3SourceDocumentRecord,
)
from app.orchestration.v3.graph import (
    EditorialIntelligenceV3Graph,
    V3PipelineNodes,
)
from app.orchestration.v3.stages.knowledge_gate import KnowledgeContractGateStage
from app.orchestration.v3.state import V3PipelineState, V3Stage
from app.schemas.editorial_intelligence import (
    ContentIntelligenceState,
    EmergentEditorialQuestionsOutput,
    IntelligenceLifecycle,
)
from app.schemas.editorial_v3 import (
    ContentKnowledgeContract,
    DecisionMatrix,
    EditorialContentTypeV3,
    KnowledgeGap,
    MethodDossier,
    SectionDossier,
    SourceUsagePolicy,
    procedural_structural_minimum_words,
)
from app.schemas.editorial_v3_runtime import (
    ApproachTaxonomyValidationOutput,
    GenericKnowledgeSynthesisOutput,
    KnowledgeClaimExtractionOutput,
    KnowledgeSynthesisOutput,
    MethodInventoryOutput,
    ResearchTask,
    StructuredSourceDocument,
    V3BlockRevisionOutput,
    V3DevelopmentReview,
    V3FactCheckReview,
    V3LanguageReview,
    V3ResearchPlan,
    V3WriterOutput,
    V3WriterSectionOutput,
)
from app.services.agent_runtime import AgentConfigurationError, AgentRuntime
from app.services.content_versioning import ContentVersionService
from app.services.editorial_seal import article_version_checksum
from app.services.editorial_v3.artifact_repository import V3ArtifactRepository
from app.services.editorial_v3.contract_repository import KnowledgeContractRepository
from app.services.editorial_v3.content_intelligence import ContentIntelligenceEngine
from app.services.editorial_v3.context_budget import (
    ContextBudgetExceeded,
    ContextBudgetPlanner,
)
from app.services.editorial_v3.intelligence_repository import (
    EditorialIntelligenceRepository,
)
from app.services.editorial_v3.content_similarity import (
    V3ContentSimilarityService,
    keyword_coverage,
    shingle_similarity,
)
from app.services.editorial_v3.document_parser import SourceDocumentParser
from app.services.editorial_v3.generation_context import (
    active_node_ids,
    generation_brief,
    resolve_node_applicability,
)
from app.services.editorial_v3.external_reference_validator import (
    ExternalReferenceValidator,
)
from app.services.editorial_v3.language_quality import language_report
from app.services.editorial_v3.knowledge_completeness import (
    KnowledgeCompletenessService,
)
from app.services.editorial_v3.method_coverage import required_method_matches
from app.services.editorial_v3.procedural_quality import ProceduralQualityService
from app.services.editorial_v3.universal_quality import UniversalEditorialQualityService
from app.services.editorial_v3.prose_quality import analyze_editorial_prose
from app.services.editorial_v3.research_planner import (
    V3ResearchPlanningService,
    build_targeted_gap_queries,
    schedule_research_queries,
)
from app.services.editorial_v3.resilient_search import ResilientSearchCoordinator
from app.services.editorial_v3.research_intent import CanonicalResearchIntent
from app.services.editorial_v3.search_acceptance import (
    CandidateAcceptanceService,
    SourceCoverageService,
    expand_source_task_map,
)
from app.services.editorial_v3.text_integrity import (
    claim_supports_sentence,
    is_potentially_factual,
    normalized_text,
    revision_preserves_meaning,
    stable_slug,
)
from app.services.editorial_v3.search_runtime import (
    ProviderCircuitBreaker,
    SearchBudgetLedger,
)
from app.services.execution_manifest import (
    ExecutionManifestService,
    pinned_v3_definitions,
)
from app.services.human_editorial_review import HumanEditorialReviewService
from app.services.pipeline_control import (
    CheckpointService,
    EventContext,
    PipelineCancellationRequested,
    PipelineRunService,
)
from app.services.skill_registry import SkillRegistry
from app.services.research_engine import (
    ResearchEngine,
    SearchDocument,
    SearchProviderError,
    canonicalize_url,
)


class V3PipelineBlocked(RuntimeError):
    def __init__(self, message: str, code: str = "V3_PIPELINE_BLOCKED"):
        self.code = code
        super().__init__(message)


def _slug(value: str, limit: int = 100) -> str:
    return stable_slug(value, separator="_", limit=limit)


def _compact(value: object, limit: int = 24000) -> str:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return payload[:limit]


def _inline_html(value: str) -> str:
    escaped = html.escape(value)
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")

    def replace(match: re.Match[str]) -> str:
        label = html.escape(html.unescape(match.group(1)))
        url = html.escape(html.unescape(match.group(2)), quote=True)
        return f'<a href="{url}" rel="noopener noreferrer">{label}</a>'

    return pattern.sub(replace, escaped)


_SOURCE_INSTRUCTION_PATTERN = re.compile(
    r"(?is)(?:\b(?:ignore|disregard|forget)\b.{0,100}\b(?:previous|prior|system|developer|instructions?|prompt)\b"
    r"|\byou\s+are\s+(?:chatgpt|an?\s+ai|a\s+language\s+model)\b"
    r"|\b(?:system|developer)\s+message\b"
    r"|\bdo\s+not\s+follow\b.{0,80}\binstructions?\b)"
)


def _safe_source_fragment(value: str) -> tuple[str, bool]:
    clean = " ".join(str(value or "").split())
    if _SOURCE_INSTRUCTION_PATTERN.search(clean):
        return "", True
    return clean, False


def _draft_markdown_for_analysis(draft: V3WriterOutput) -> str:
    chunks: list[str] = []
    for block in draft.blocks:
        texts = [sentence.text for sentence in block.content_sentences]
        combined = " ".join(texts)
        if block.type in {"h1", "h2", "h3"}:
            chunks.append(f"{'#' * int(block.type[1])} {combined}")
        elif block.type == "list":
            chunks.append("\n".join(f"- {item}" for item in texts))
        else:
            chunks.append(combined)
    return "\n\n".join(chunks)


class EditorialV3Executor:
    """Coordinate the complete V3 pipeline and preserve resumability."""

    def __init__(
        self,
        db: AsyncSession,
        project,
        pipeline_run: PipelineRun,
        lease_owner: str,
    ):
        self.db = db
        self.project = project
        self.pipeline_run = pipeline_run
        self.lease_owner = lease_owner
        self.runtime = AgentRuntime(db)
        self.search = ResearchEngine()
        self.reader = SourceDocumentParser()
        self.planner_service = V3ResearchPlanningService()
        self.reference_validator = ExternalReferenceValidator()
        self.completeness = KnowledgeCompletenessService()
        self.quality = ProceduralQualityService()
        self.universal_quality = UniversalEditorialQualityService()
        self.content_similarity = V3ContentSimilarityService(db)
        self.intelligence = ContentIntelligenceEngine()
        self.context_budget = ContextBudgetPlanner()
        self.intelligence_repository = EditorialIntelligenceRepository(
            db, project_id=project.id, pipeline_run_id=pipeline_run.id
        )
        self.v3_skills = SkillRegistry(str(Path(settings.skills_path).parent / "v3"))
        self.checkpoints = CheckpointService(db)
        self.run_service = PipelineRunService(db)
        self.versions = ContentVersionService(db)
        self.contracts = KnowledgeContractRepository(db)
        self.artifacts = V3ArtifactRepository(
            db,
            project_id=project.id,
            pipeline_run_id=pipeline_run.id,
        )
        self._stage_context: EventContext | None = None
        self.execution_manifest: dict | None = None

    async def execute(self) -> V3PipelineState:
        if (
            getattr(
                self.project.editorial_pipeline_version,
                "value",
                self.project.editorial_pipeline_version,
            )
            != EditorialPipelineVersion.v3.value
        ):
            raise ValueError("EditorialV3Executor can only run V3 projects")
        if not settings.editorial_pipeline_v3_execution_enabled:
            raise ValueError("Editorial V3 execution is disabled")

        manifest = await ExecutionManifestService(self.db).required(
            self.pipeline_run.id, project_id=self.project.id
        )
        self.execution_manifest = manifest.data
        self.runtime.bind_execution_manifest(manifest)
        self.v3_skills = SkillRegistry(definitions=pinned_v3_definitions(manifest.data))
        if not bool(self._flag("editorial_pipeline_v3_execution_enabled")):
            raise ValueError(
                "The fixed execution manifest does not enable Editorial V3"
            )
        graph = EditorialIntelligenceV3Graph(
            V3PipelineNodes(
                content_contract=self.content_contract,
                knowledge_architect=self.knowledge_architect,
                knowledge_gate=self.knowledge_gate,
                intelligence_planner=self.intelligence_planner,
                research_planner=self.research_planner,
                source_discovery=self.source_discovery,
                source_reader=self.source_reader,
                source_coverage_gate=self.source_coverage_gate,
                targeted_source_recovery=self.targeted_source_recovery,
                knowledge_synthesizer=self.knowledge_synthesizer,
                evidence_graph_builder=self.evidence_graph_builder,
                intelligence_gate=self.intelligence_gate,
                knowledge_completeness_gate=self.knowledge_completeness_gate,
                writer=self.writer,
                development_editor=self.development_editor,
                fact_checker=self.fact_checker,
                language_editor=self.language_editor,
                external_reference_gate=self.external_reference_gate,
                finalizer=self.finalizer,
                quality_gate=self.quality_gate,
            ),
            after_transition=self._checkpoint,
            max_transitions=int(
                self._optional_flag(
                    "v3_graph_max_transitions", settings.v3_graph_max_transitions
                )
            ),
        )
        checkpoint = await self.checkpoints.latest(self.pipeline_run.id)
        try:
            state = (
                V3PipelineState.model_validate(checkpoint.state_json)
                if checkpoint
                else V3PipelineState(
                    project_id=self.project.id,
                    pipeline_run_id=self.pipeline_run.id,
                )
            )
        except ValidationError as exc:
            state = V3PipelineState(
                project_id=self.project.id,
                pipeline_run_id=self.pipeline_run.id,
                stage=V3Stage.blocked,
                blocking_reason=f"The latest V3 checkpoint is structurally invalid: {exc}",
                blocking_code="V3_CHECKPOINT_INVALID",
            )
            await self._checkpoint("checkpoint_validation", state)
        if checkpoint and state.stage != V3Stage.blocked:
            invariant_errors = state.resume_invariant_errors(
                project_id=self.project.id, pipeline_run_id=self.pipeline_run.id
            )
            if invariant_errors:
                state.stage = V3Stage.blocked
                state.blocking_reason = "; ".join(invariant_errors)
                state.blocking_code = "V3_CHECKPOINT_INVARIANT_VIOLATION"
                await self._checkpoint("checkpoint_validation", state)
        if checkpoint and state.stage != V3Stage.blocked:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "stage.resumed",
                state.stage.value,
                {
                    "checkpoint_id": str(checkpoint.id),
                    "checkpoint_sequence": checkpoint.sequence,
                },
                idempotency_key=f"v3.stage.resumed:{checkpoint.id}",
                context=EventContext.for_stage(
                    self.pipeline_run.id,
                    state.stage.value,
                    0,
                    0,
                    self.pipeline_run.attempt,
                ).with_checkpoint(checkpoint.sequence),
            )
            await self.db.commit()
        try:
            if state.stage != V3Stage.blocked:
                state = await graph.run(state)
        except V3PipelineBlocked as exc:
            state.stage = V3Stage.blocked
            state.blocking_reason = str(exc)
            state.blocking_code = exc.code
            await self._checkpoint("exception_gate", state)
        if state.stage == V3Stage.completed:
            await HumanEditorialReviewService(self.db).ensure_pending(
                self.project, self.pipeline_run
            )
            self.pipeline_run = await self.run_service.transition(
                self.pipeline_run.id,
                PipelineRunStatus.needs_human_approval,
                origin="orchestrator.v3",
                stage="human_approval",
                expected_lease_owner=self.lease_owner,
                expected_lock_version=self.pipeline_run.lock_version,
            )
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "pipeline.needs_human_approval",
                "human_approval",
                {
                    "message": "O guia V3 passou pelos gates automáticos e aguarda revisão humana final.",
                    "pipeline_contract_version": "editorial-v3.8",
                },
                idempotency_key="v3.pipeline.needs_human_approval",
                context=self._stage_context,
            )
        else:
            blocked_stage = self.pipeline_run.current_stage
            self.pipeline_run = await self.run_service.transition(
                self.pipeline_run.id,
                PipelineRunStatus.blocked,
                origin="orchestrator.v3",
                reason=state.blocking_reason,
                error_code=state.blocking_code,
                stage=blocked_stage,
                expected_lease_owner=self.lease_owner,
                expected_lock_version=self.pipeline_run.lock_version,
            )
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "pipeline.blocked",
                blocked_stage,
                {
                    "message": state.blocking_reason or "V3 pipeline blocked",
                    "error_code": state.blocking_code,
                },
                idempotency_key="v3.pipeline.blocked",
                context=self._stage_context,
            )
        await self.db.commit()
        return state

    async def content_contract(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "content_contract", "Materializando o contrato editorial V3", state
        )
        try:
            materialized = await self.contracts.materialize(
                self.project,
                pipeline_run_id=self.pipeline_run.id,
            )
        except (ValidationError, ValueError) as exc:
            raise V3PipelineBlocked(
                "O briefing não pôde ser convertido no contrato editorial. "
                "Revise o tópico e os campos de estado do leitor, promessa e escopo.",
                "V3_CONTENT_CONTRACT_INVALID",
            ) from exc
        contract = materialized.contract
        generation = generation_brief(self.project, self.execution_manifest, contract)
        node_resolution = resolve_node_applicability(contract, generation)
        contract = contract.model_copy(
            update={
                "metadata": {
                    **contract.metadata,
                    "generation_brief": generation,
                    "node_resolution": node_resolution,
                    "active_node_ids": [
                        node.node_id
                        for node in contract.nodes
                        if bool(
                            (node_resolution.get(node.node_id) or {}).get("included")
                        )
                    ],
                }
            }
        )
        if contract.content_type == EditorialContentTypeV3.procedural_decision_guide:
            estimated_minimum = procedural_structural_minimum_words(
                len(contract.required_method_labels), len(contract.nodes)
            )
            maximum_words = int(
                dict(self.project.briefing or {}).get("maximum_words") or 0
            )
            if maximum_words and maximum_words < estimated_minimum:
                raise V3PipelineBlocked(
                    "A faixa de palavras informada não comporta a arquitetura "
                    f"procedural: máximo {maximum_words}, mínimo estrutural "
                    f"{estimated_minimum}.",
                    "V3_WORD_RANGE_SCOPE_CONFLICT",
                )
        state.contract_id = materialized.row.id
        state.contract = contract.model_dump(mode="json")
        return state

    async def knowledge_architect(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "knowledge_architect", "Validando ordem, dependências e ramificações", state
        )
        contract = ContentKnowledgeContract.model_validate(state.contract)
        metadata = {
            **contract.metadata,
            "knowledge_architect": "deterministic-editorial-graph-v3.1",
            "audited_at": datetime.now(timezone.utc).isoformat(),
        }
        if (
            contract.content_type == EditorialContentTypeV3.procedural_decision_guide
            and len(contract.required_method_labels) >= 2
        ):
            validation = ApproachTaxonomyValidationOutput.model_validate(
                await self._agent_call(
                    role="planner",
                    key="approach_taxonomy",
                    attempt=1,
                    input_json={
                        "topic": contract.topic,
                        "declared_dimension": contract.approach_dimension.value,
                        "approaches": contract.required_method_labels,
                        "article_promise": contract.article_promise,
                        "scope_limit": contract.scope_limit,
                    },
                    prompt=(
                        "Valide somente a taxonomia das abordagens antes da pesquisa factual. "
                        "Confirme se todos os rótulos pertencem à dimensão declarada, são "
                        "alternativas comparáveis no mesmo nível de abstração e são pertinentes "
                        "ao tópico. Não avalie qual é melhor e não invente fatos técnicos. "
                        "Devolva cada rótulo exatamente como recebido. Marque coherent_set=false "
                        "quando houver mistura de abordagem, ambiente, sistema, material, técnica ou "
                        "outro nível conceitual, ou quando um item for etapa/subtema em vez de "
                        "alternativa comparável."
                    ),
                    output_schema=ApproachTaxonomyValidationOutput,
                )
            )
            expected_labels = {
                " ".join(item.casefold().split())
                for item in contract.required_method_labels
            }
            returned_labels = {
                " ".join(item.label.casefold().split()) for item in validation.items
            }
            invalid_items = [
                item.label
                for item in validation.items
                if (
                    item.detected_dimension != contract.approach_dimension
                    or not item.comparable_at_same_level
                    or not item.valid_for_topic
                )
            ]
            if (
                validation.declared_dimension != contract.approach_dimension
                or expected_labels != returned_labels
                or not validation.coherent_set
                or invalid_items
                or validation.blocking_issues
            ):
                reasons = [*validation.blocking_issues]
                if expected_labels != returned_labels:
                    reasons.append(
                        "A validação não devolveu exatamente todas as abordagens informadas."
                    )
                if invalid_items:
                    reasons.append(
                        "Abordagens incompatíveis ou em níveis diferentes: "
                        + ", ".join(invalid_items)
                    )
                raise V3PipelineBlocked(
                    "Taxonomia de abordagens inválida: " + "; ".join(reasons[:8]),
                    "V3_APPROACH_TAXONOMY_INVALID",
                )
            metadata["approach_taxonomy"] = validation.model_dump(mode="json")
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "v3.approach_taxonomy.validated",
                "knowledge_architect",
                {
                    "dimension": contract.approach_dimension.value,
                    "approach_count": len(contract.required_method_labels),
                    "collective_name": validation.normalized_collective_name,
                },
                idempotency_key="v3.approach_taxonomy.validated",
                context=self._stage_context,
            )
        state.contract = contract.model_copy(update={"metadata": metadata}).model_dump(
            mode="json"
        )
        return state

    async def knowledge_gate(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "knowledge_gate", "Executando o gate do contrato de conhecimento", state
        )
        return await KnowledgeContractGateStage()(state)

    async def intelligence_planner(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "intelligence_planner",
            "Transformando o briefing e o contrato em um estado editorial canônico",
            state,
        )
        if state.pipeline_run_id is None:
            raise V3PipelineBlocked(
                "Editorial intelligence requires a pipeline run ID",
                "V3_INTELLIGENCE_RUN_ID_MISSING",
            )
        contract = ContentKnowledgeContract.model_validate(state.contract)
        generation = generation_brief(self.project, self.execution_manifest, contract)
        intelligence = self.intelligence.initialize(
            project_id=self.project.id,
            pipeline_run_id=state.pipeline_run_id,
            contract_id=state.contract_id,
            contract=contract,
            generation_brief=generation,
        )
        validation = intelligence.validation
        state.intelligence_state = intelligence.model_dump(mode="json")
        state.intelligence_validation = (
            validation.model_dump(mode="json") if validation is not None else None
        )
        state.intelligence_revision = intelligence.revision
        await self.intelligence_repository.save(
            intelligence,
            stage="intelligence_planner",
            status=intelligence.lifecycle.value,
            validation=state.intelligence_validation,
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.intelligence.planned",
            "intelligence_planner",
            self.intelligence.summary(intelligence),
            idempotency_key=f"v3.intelligence.planned:{intelligence.checksum}",
            context=self._stage_context,
        )
        return state

    async def research_planner(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "research_planner", "Planejando pesquisa por função de evidência", state
        )
        contract = ContentKnowledgeContract.model_validate(state.contract)
        plan = self.planner_service.build(
            contract,
            max_tasks=int(self._flag("v3_max_research_tasks")),
            maximum_search_queries=int(self._flag("v3_max_search_queries")),
        )
        if state.intelligence_state:
            plan = self.intelligence.augment_research_plan(
                ContentIntelligenceState.model_validate(state.intelligence_state),
                plan,
            )
        await self.artifacts.research_plan(plan)
        state.research_plan = plan.model_dump(mode="json")
        return state

    def _search_runtime(
        self,
        state: V3PipelineState,
        plan: V3ResearchPlan,
    ) -> tuple[
        ResilientSearchCoordinator,
        SearchBudgetLedger,
        ProviderCircuitBreaker,
    ]:
        persisted_budget = state.research_metrics.get("search_budget")
        budget = SearchBudgetLedger.from_payload(
            persisted_budget,
            maximum_logical_queries=plan.maximum_search_queries,
            maximum_provider_requests=int(
                self._flag("v3_max_search_provider_requests")
            ),
            maximum_provider_retries=int(self._flag("v3_max_search_provider_retries")),
            maximum_result_page_fetches=0,
            maximum_estimated_credits=float(
                self._flag("v3_max_search_estimated_credits")
            ),
            timeout_seconds=float(self._flag("v3_source_discovery_timeout_seconds")),
        )
        if not persisted_budget:
            # Resume V3.4/early V3.5 checkpoints that persisted only aggregate
            # counters. Without this bridge, supplemental research would reset
            # the logical budget and could overrun configured limits.
            budget.logical_queries = min(
                plan.maximum_search_queries,
                max(0, int(state.research_metrics.get("total_query_count") or 0)),
            )
            budget.provider_requests = max(
                0, int(state.research_metrics.get("provider_request_count") or 0)
            )
            budget.provider_retries = max(
                0, int(state.research_metrics.get("provider_retry_count") or 0)
            )
            budget.estimated_credits = max(
                0.0,
                float(state.research_metrics.get("estimated_search_credits") or 0.0),
            )
        circuits = ProviderCircuitBreaker.from_payload(
            state.research_metrics.get("provider_circuits")
        )
        coordinator = ResilientSearchCoordinator(
            self.search,
            budget=budget,
            circuits=circuits,
            acceptance=CandidateAcceptanceService(
                float(self._flag("v3_min_candidate_relevance"))
            ),
        )
        return coordinator, budget, circuits

    @staticmethod
    def _search_blocking_code(
        *,
        failure_categories: list[str],
        budget_reason: str | None,
        provider_names: list[str],
        circuits: ProviderCircuitBreaker,
        had_successful_attempt: bool,
    ) -> str:
        if budget_reason:
            return "V3_SEARCH_ATTEMPT_BUDGET_EXHAUSTED"
        authentication_failures = {
            provider
            for provider in provider_names
            if circuits.state(provider).last_error_category == "authentication"
            and not circuits.allows(provider)
        }
        if (
            provider_names
            and not had_successful_attempt
            and authentication_failures == set(provider_names)
        ):
            return "V3_SEARCH_CREDENTIALS_INVALID"
        if had_successful_attempt:
            return "V3_SEARCH_NO_CANDIDATES"
        if provider_names and circuits.all_unavailable(provider_names):
            return "V3_SEARCH_PROVIDERS_UNAVAILABLE"
        if any(category == "authentication" for category in failure_categories):
            return "V3_SEARCH_CREDENTIALS_INVALID"
        return "V3_SEARCH_PROVIDERS_UNAVAILABLE"

    @staticmethod
    def _persist_search_runtime(
        state: V3PipelineState,
        *,
        budget: SearchBudgetLedger,
        circuits: ProviderCircuitBreaker,
    ) -> None:
        state.research_metrics = {
            **state.research_metrics,
            "search_budget": budget.as_payload(),
            "provider_circuits": circuits.as_payload(),
        }

    async def source_discovery(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "source_discovery",
            "Descobrindo fontes por intenção, idioma, mercado e função de evidência",
            state,
        )
        plan = V3ResearchPlan.model_validate(state.research_plan)
        if plan.maximum_search_queries < len(plan.tasks):
            raise V3PipelineBlocked(
                "O orçamento de consultas não permite pesquisar cada nó ao menos uma vez.",
                "V3_QUERY_BUDGET_INSUFFICIENT",
            )
        contract = ContentKnowledgeContract.model_validate(state.contract)
        intent = CanonicalResearchIntent.from_contract(contract)
        documents: dict[str, SearchDocument] = {}
        maximum_source_documents = int(self._flag("v3_max_source_documents"))
        task_map: dict[str, set[str]] = {}
        failures: list[SearchProviderError] = []
        successful_attempts = 0
        attempt_payloads: list[dict] = []

        reserve_capacity = max(
            0,
            plan.maximum_search_queries
            - min(len(plan.tasks), plan.maximum_search_queries),
        )
        desired_reserve = min(8, max(2, plan.maximum_search_queries // 4))
        supplemental_reserve = min(desired_reserve, reserve_capacity)
        initial_query_limit = max(1, plan.maximum_search_queries - supplemental_reserve)
        try:
            provider_credentials = await self._search_credentials()
        except AgentConfigurationError as exc:
            state.source_recovery_exhausted = True
            state.blocking_code = "V3_SEARCH_CREDENTIALS_MISSING"
            state.blocking_reason = str(exc)
            state.research_metrics = {
                **state.research_metrics,
                "search_error_code": state.blocking_code,
                "search_error_message": str(exc),
            }
            return state

        coordinator, budget, circuits = self._search_runtime(state, plan)
        logical_queries_before = budget.logical_queries
        schedule = schedule_research_queries(plan.tasks, limit=initial_query_limit)
        task_by_id = {task.task_id: task for task in plan.tasks}
        executed_queries_by_task: dict[str, list[str]] = {}
        markets_by_task: dict[str, set[str]] = {}
        languages_by_task: dict[str, set[str]] = {}
        providers_used: set[str] = set()
        budget_exhausted_by: str | None = None

        for assignment in schedule:
            await self._cancellation_boundary()
            if budget.exhaustion_reason(include_logical=True):
                budget_exhausted_by = budget.exhaustion_reason(include_logical=True)
                break
            task = task_by_id[assignment.task_id]
            executed_queries_by_task.setdefault(assignment.task_id, []).append(
                assignment.query
            )
            result = await coordinator.search(
                query=assignment.query,
                topic=contract.topic,
                question=task.research_goal,
                search_subject=intent.canonical_subject,
                provider_credentials=provider_credentials,
                max_results=int(self._flag("v3_search_results_per_query")),
                preferred_market_index=assignment.query_index,
                intent=intent,
                task=task,
            )
            failures.extend(result.failures)
            successful_attempts += result.successful_attempts
            budget_exhausted_by = result.budget_exhausted_by or budget_exhausted_by
            for attempt in result.attempts:
                payload = attempt.as_payload()
                payload["task_id"] = assignment.task_id
                payload["query_index"] = assignment.query_index
                attempt_payloads.append(payload)
                if attempt.status != "skipped":
                    providers_used.add(attempt.provider)
                if attempt.market:
                    markets_by_task.setdefault(assignment.task_id, set()).add(
                        attempt.market
                    )
                if attempt.search_language:
                    languages_by_task.setdefault(assignment.task_id, set()).add(
                        attempt.search_language
                    )
            for document in result.documents:
                key = canonicalize_url(document.url)
                if key not in documents:
                    if len(documents) >= maximum_source_documents:
                        continue
                    documents[key] = document
                task_map.setdefault(key, set()).add(assignment.task_id)

        state.raw_source_documents = [item.as_payload() for item in documents.values()]
        state.source_task_map = {key: sorted(value) for key, value in task_map.items()}
        diagnostic_totals: dict[str, int | float] = {}
        for attempt in attempt_payloads:
            for key, value in dict(attempt.get("diagnostics") or {}).items():
                if isinstance(value, (int, float)):
                    diagnostic_totals[key] = diagnostic_totals.get(key, 0) + value
        candidate_task_ids = {
            task_id for task_ids in task_map.values() for task_id in task_ids
        }
        executed_task_ids = set(executed_queries_by_task)
        all_task_ids = {task.task_id for task in plan.tasks}
        failure_categories = [failure.category for failure in failures]
        provider_names = [provider for provider, _key in provider_credentials]
        provisional_code = None
        if not documents:
            provisional_code = self._search_blocking_code(
                failure_categories=failure_categories,
                budget_reason=budget_exhausted_by,
                provider_names=provider_names,
                circuits=circuits,
                had_successful_attempt=successful_attempts > 0,
            )
            state.blocking_code = provisional_code
            state.blocking_reason = (
                "A pesquisa inicial não encontrou candidatos legíveis; o pipeline "
                "iniciará recuperação direcionada antes de bloquear."
            )

        state.research_metrics = {
            **state.research_metrics,
            "research_runtime_version": "intent-aware-research.v3.5",
            "research_intent": intent.as_payload(),
            "initial_query_count": budget.logical_queries - logical_queries_before,
            "total_query_count": budget.logical_queries,
            "provider_attempt_count": len(attempt_payloads),
            "initial_discovered_source_count": len(documents),
            "maximum_source_documents": maximum_source_documents,
            "initial_query_limit": initial_query_limit,
            "supplemental_query_reserve": supplemental_reserve,
            "supplemental_query_count": 0,
            "supplemental_source_count": 0,
            "executed_queries_by_task": executed_queries_by_task,
            "initial_tasks_queried": len(executed_task_ids),
            "initial_tasks_with_candidates": len(candidate_task_ids),
            "initial_uncovered_task_ids": sorted(all_task_ids - candidate_task_ids),
            "markets_by_task": {
                key: sorted(value) for key, value in markets_by_task.items()
            },
            "languages_by_task": {
                key: sorted(value) for key, value in languages_by_task.items()
            },
            "providers_used": sorted(providers_used),
            "search_attempts": attempt_payloads[:240],
            "search_diagnostic_totals": diagnostic_totals,
            "search_failure_categories": failure_categories[:60],
            "search_error_code": provisional_code,
            "query_allocation_strategy": "node_round_robin.intent_localized.quality_gated.v3.5",
        }
        self._persist_search_runtime(state, budget=budget, circuits=circuits)
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.sources.discovered",
            "source_discovery",
            {
                "query_count": budget.logical_queries,
                "provider_attempt_count": len(attempt_payloads),
                "source_count": len(documents),
                "tasks_queried": len(executed_task_ids),
                "tasks_with_candidates": len(candidate_task_ids),
                "uncovered_task_ids": sorted(all_task_ids - candidate_task_ids),
                "failure_categories": failure_categories[:30],
                "markets_by_task": {
                    key: sorted(value) for key, value in markets_by_task.items()
                },
                "languages_by_task": {
                    key: sorted(value) for key, value in languages_by_task.items()
                },
                "budget": budget.as_payload(),
                "provider_circuits": circuits.as_payload(),
                "provisional_error_code": provisional_code,
            },
            idempotency_key="v3.sources.discovered.v35",
            context=self._stage_context,
        )
        return state

    async def source_reader(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "source_reader",
            "Lendo cada fonte uma única vez e aplicando a política editorial",
            state,
        )
        if state.contract_id is None:
            raise V3PipelineBlocked("Knowledge contract ID is missing")
        existing: dict[str, StructuredSourceDocument] = {}
        # Reconcile checkpoint payloads created by older deployments.  Their
        # parser-level document IDs were global across runs, while persisted
        # source rows are run-scoped.  Normalizing here keeps later claim lookup
        # aligned with the actual database primary key.
        for payload in state.source_documents:
            item = StructuredSourceDocument.model_validate(payload)
            source_row = await self.artifacts.source_document(
                state.contract_id, item
            )
            item = item.model_copy(update={"document_id": source_row.id})
            existing[str(item.document_id)] = item
        updated_task_map = dict(state.source_task_map)
        read_failures: list[str] = []
        read_attempts = {
            str(key): int(value)
            for key, value in dict(
                state.research_metrics.get("source_read_attempts_by_url") or {}
            ).items()
        }
        processed = set(state.research_metrics.get("processed_raw_source_urls") or [])
        maximum_fetches = int(self._flag("v3_max_source_fetches"))
        fetch_count = int(state.research_metrics.get("source_fetch_count", 0))

        for payload in state.raw_source_documents:
            if fetch_count >= maximum_fetches:
                break
            await self._cancellation_boundary()
            raw = SearchDocument.from_payload(payload)
            raw_key = canonicalize_url(raw.url)
            if raw_key in processed or read_attempts.get(raw_key, 0) >= 2:
                continue
            read_attempts[raw_key] = read_attempts.get(raw_key, 0) + 1
            fetch_count += 1
            try:
                structured = await self.reader.read(raw)
                structured = self._apply_brief_source_policy(structured, state)
            except Exception as exc:  # one malformed source must not abort the run
                read_failures.append(f"{raw_key}:{type(exc).__name__}")
                if read_attempts[raw_key] >= 2:
                    processed.add(raw_key)
                continue
            processed.add(raw_key)
            source_row = await self.artifacts.source_document(
                state.contract_id, structured
            )
            structured = structured.model_copy(
                update={"document_id": source_row.id}
            )
            tasks = updated_task_map.get(raw_key, [])
            for key in {
                raw_key,
                canonicalize_url(str(structured.url)),
                canonicalize_url(str(structured.canonical_url)),
            }:
                updated_task_map[key] = sorted(
                    set(updated_task_map.get(key, [])) | set(tasks)
                )
            if structured.assessment.usage_policy != SourceUsagePolicy.rejected:
                existing[str(structured.document_id)] = structured

        structured_documents = list(existing.values())
        plan = V3ResearchPlan.model_validate(state.research_plan)
        expanded_task_map, cross_task_assignments = expand_source_task_map(
            tasks=plan.tasks,
            documents=structured_documents,
            source_task_map=updated_task_map,
            minimum_score=float(self._flag("v3_min_candidate_relevance")),
        )
        state.source_task_map = expanded_task_map
        state.source_documents = [
            item.model_dump(mode="json") for item in structured_documents
        ]
        state.research_metrics = {
            **state.research_metrics,
            "source_fetch_count": fetch_count,
            "source_read_attempts_by_url": read_attempts,
            "processed_raw_source_urls": sorted(processed),
            "source_read_failures": [
                *state.research_metrics.get("source_read_failures", []),
                *read_failures,
            ][-100:],
            "structured_source_count": len(structured_documents),
            "cross_task_assignment_count": len(cross_task_assignments),
            "cross_task_assignments": cross_task_assignments[:200],
        }
        accepted = sum(
            item.assessment.usage_policy
            in {
                SourceUsagePolicy.authoritative_evidence,
                SourceUsagePolicy.corroborating_evidence,
            }
            for item in structured_documents
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.sources.read",
            "source_reader",
            {
                "structured_count": len(structured_documents),
                "evidence_eligible_count": accepted,
                "source_fetch_count": fetch_count,
                "maximum_source_fetches": maximum_fetches,
                "read_failures": read_failures[:30],
                "cross_task_assignment_count": len(cross_task_assignments),
            },
            idempotency_key=f"v3.sources.read.v35:{state.source_recovery_round}",
            context=self._stage_context,
        )
        return state

    async def source_coverage_gate(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "source_coverage_gate",
            "Validando relevância, autoridade e diversidade por nó",
            state,
        )
        plan = V3ResearchPlan.model_validate(state.research_plan)
        documents = [
            StructuredSourceDocument.model_validate(item)
            for item in state.source_documents
        ]
        report = SourceCoverageService().evaluate(
            tasks=plan.tasks,
            documents=documents,
            source_task_map=state.source_task_map,
        )
        state.source_coverage_report = report.as_payload()
        max_rounds = int(self._flag("v3_max_source_recovery_rounds"))
        budget_reason = dict(state.research_metrics.get("search_budget") or {}).get(
            "exhausted_by"
        )
        fetch_exhausted = int(
            state.research_metrics.get("source_fetch_count", 0)
        ) >= int(self._flag("v3_max_source_fetches"))
        state.source_recovery_exhausted = bool(
            report.status != "passed"
            and (
                state.source_recovery_round >= max_rounds
                or budget_reason
                or fetch_exhausted
            )
        )
        if state.source_recovery_exhausted:
            state.blocking_code = (
                "V3_SEARCH_ATTEMPT_BUDGET_EXHAUSTED"
                if budget_reason
                else report.suggested_blocking_code or "V3_RESEARCH_COVERAGE_INCOMPLETE"
            )
            state.blocking_reason = (
                "A cobertura de fontes não atingiu os requisitos após recuperação "
                "e os limites seguros da execução foram alcançados."
            )
        state.research_metrics = {
            **state.research_metrics,
            "source_coverage": report.as_payload(),
            "source_recovery_round": state.source_recovery_round,
            "source_recovery_exhausted": state.source_recovery_exhausted,
        }
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.sources.coverage_evaluated",
            "source_coverage_gate",
            report.as_payload(),
            idempotency_key=f"v3.sources.coverage:{state.source_recovery_round}",
            context=self._stage_context,
        )
        return state

    async def targeted_source_recovery(self, state: V3PipelineState) -> V3PipelineState:
        intelligence_mode = bool(state.intelligence_recovery_tasks)
        current_round = (
            state.intelligence_recovery_round
            if intelligence_mode
            else state.source_recovery_round
        )
        next_round = current_round + 1
        recovery_label = "inteligência editorial" if intelligence_mode else "fontes"
        await self._stage(
            "targeted_source_recovery",
            f"Recuperando lacunas de {recovery_label} — rodada {next_round}",
            state,
        )
        plan = V3ResearchPlan.model_validate(state.research_plan)
        contract = ContentKnowledgeContract.model_validate(state.contract)
        intent = CanonicalResearchIntent.from_contract(contract)
        max_rounds = (
            2 if intelligence_mode else int(self._flag("v3_max_source_recovery_rounds"))
        )
        if current_round >= max_rounds:
            if intelligence_mode:
                state.intelligence_recovery_exhausted = True
                state.blocking_code = (
                    state.blocking_code or "V3_INTELLIGENCE_RECOVERY_EXHAUSTED"
                )
            else:
                state.source_recovery_exhausted = True
                state.blocking_code = (
                    state.blocking_code or "V3_RESEARCH_COVERAGE_INCOMPLETE"
                )
            return state
        try:
            provider_credentials = await self._search_credentials()
        except AgentConfigurationError as exc:
            if intelligence_mode:
                state.intelligence_recovery_exhausted = True
            else:
                state.source_recovery_exhausted = True
            state.blocking_code = "V3_SEARCH_CREDENTIALS_MISSING"
            state.blocking_reason = str(exc)
            return state

        coordinator, budget, circuits = self._search_runtime(state, plan)
        provider_names = [provider for provider, _key in provider_credentials]
        report_payload = dict(state.source_coverage_report or {})
        recovery_entries = [
            dict(item)
            for item in state.intelligence_recovery_tasks
            if isinstance(item, dict)
        ]
        recovery_by_task: dict[str, list[dict]] = {}
        for entry in recovery_entries:
            task_id = str(entry.get("task_id") or "")
            if task_id:
                recovery_by_task.setdefault(task_id, []).append(entry)
        deficient_ids = (
            set(recovery_by_task)
            if intelligence_mode
            else set(report_payload.get("deficient_task_ids") or [])
        )
        tasks = [
            task
            for task in plan.tasks
            if not deficient_ids or task.task_id in deficient_ids
        ]
        task_reports = {
            str(item.get("task_id")): item
            for item in report_payload.get("task_reports") or []
            if isinstance(item, dict)
        }
        existing_urls = {
            canonicalize_url(SearchDocument.from_payload(item).url)
            for item in state.raw_source_documents
        }
        remaining_candidate_slots = max(
            0,
            int(self._flag("v3_max_source_documents")) - len(existing_urls),
        )
        new_documents: dict[str, SearchDocument] = {}
        attempts: list[dict] = []
        failures: list[SearchProviderError] = []
        successful_attempts = 0
        executed = {
            str(task_id): [str(query) for query in queries]
            for task_id, queries in dict(
                state.research_metrics.get("executed_queries_by_task") or {}
            ).items()
        }
        maximum_round_queries = min(8, max(1, len(tasks)))
        targeted_task_ids: list[str] = []
        for task in tasks[:maximum_round_queries]:
            if budget.exhaustion_reason(include_logical=True):
                break
            used = set(executed.get(task.task_id, []))
            if intelligence_mode:
                exact_queries = [
                    " ".join(str(entry.get("query") or "").split())[:280]
                    for entry in recovery_by_task.get(task.task_id, [])
                    if str(entry.get("query") or "").strip()
                ]
                candidates = list(dict.fromkeys(exact_queries))
            else:
                task_report = task_reports.get(task.task_id, {})
                reasons = list(
                    task_report.get("reason_codes")
                    or report_payload.get("reason_codes")
                    or []
                )
                candidates = build_targeted_gap_queries(
                    contract,
                    task,
                    limit=3,
                    reason_codes=reasons,
                )
            query = next((item for item in candidates if item not in used), None)
            if not query:
                continue
            targeted_task_ids.append(task.task_id)
            executed.setdefault(task.task_id, []).append(query)
            question = task.research_goal
            if intelligence_mode:
                matching_entry = next(
                    (
                        entry
                        for entry in recovery_by_task.get(task.task_id, [])
                        if " ".join(str(entry.get("query") or "").split())[:280]
                        == query
                    ),
                    {},
                )
                question = str(matching_entry.get("question") or question)
            result = await coordinator.search(
                query=query,
                topic=contract.topic,
                question=question,
                search_subject=intent.canonical_subject,
                provider_credentials=provider_credentials,
                max_results=int(self._flag("v3_search_results_per_query")),
                preferred_market_index=next_round,
                intent=intent,
                task=task,
            )
            failures.extend(result.failures)
            successful_attempts += result.successful_attempts
            for attempt in result.attempts:
                payload = attempt.as_payload()
                payload.update(
                    {
                        "task_id": task.task_id,
                        "recovery_round": next_round,
                        "recovery_mode": (
                            "intelligence" if intelligence_mode else "source_coverage"
                        ),
                    }
                )
                attempts.append(payload)
            for document in result.documents:
                key = canonicalize_url(document.url)
                state.source_task_map[key] = sorted(
                    set(state.source_task_map.get(key, [])) | {task.task_id}
                )
                if (
                    key not in existing_urls
                    and len(new_documents) < remaining_candidate_slots
                ):
                    existing_urls.add(key)
                    new_documents[key] = document

        markets_by_task = {
            str(task_id): set(str(item) for item in values)
            for task_id, values in dict(
                state.research_metrics.get("markets_by_task") or {}
            ).items()
        }
        languages_by_task = {
            str(task_id): set(str(item) for item in values)
            for task_id, values in dict(
                state.research_metrics.get("languages_by_task") or {}
            ).items()
        }
        providers_used = set(state.research_metrics.get("providers_used") or [])
        diagnostic_totals = {
            str(key): value
            for key, value in dict(
                state.research_metrics.get("search_diagnostic_totals") or {}
            ).items()
            if isinstance(value, (int, float))
        }
        for attempt in attempts:
            task_id = str(attempt.get("task_id") or "")
            if attempt.get("status") != "skipped" and attempt.get("provider"):
                providers_used.add(str(attempt["provider"]))
            if task_id and attempt.get("market"):
                markets_by_task.setdefault(task_id, set()).add(str(attempt["market"]))
            if task_id and attempt.get("search_language"):
                languages_by_task.setdefault(task_id, set()).add(
                    str(attempt["search_language"])
                )
            for key, value in dict(attempt.get("diagnostics") or {}).items():
                if isinstance(value, (int, float)):
                    diagnostic_totals[str(key)] = (
                        diagnostic_totals.get(str(key), 0) + value
                    )

        state.raw_source_documents.extend(
            item.as_payload() for item in new_documents.values()
        )
        failure_categories = [failure.category for failure in failures]
        budget_reason = budget.exhaustion_reason(include_logical=True)
        no_more_provider = circuits.all_unavailable(provider_names)
        exhausted = bool(
            not new_documents
            and (next_round >= max_rounds or budget_reason or no_more_provider)
        )
        if intelligence_mode:
            state.intelligence_recovery_round = next_round
            state.intelligence_recovery_exhausted = exhausted
            if new_documents:
                state.intelligence_recovery_exhausted = False
                state.intelligence_validation = None
                state.blocking_code = None
                state.blocking_reason = None
        else:
            state.source_recovery_round = next_round
            state.source_recovery_exhausted = exhausted

        if exhausted:
            state.blocking_code = (
                "V3_INTELLIGENCE_RECOVERY_EXHAUSTED"
                if intelligence_mode
                else self._search_blocking_code(
                    failure_categories=failure_categories,
                    budget_reason=budget_reason,
                    provider_names=provider_names,
                    circuits=circuits,
                    had_successful_attempt=successful_attempts > 0,
                )
            )
            state.blocking_reason = (
                "A recuperação orientada pelo Motor de Inteligência Editorial "
                "não encontrou novas evidências após esgotar as tentativas seguras."
                if intelligence_mode
                else "A recuperação direcionada não encontrou novas fontes e não há "
                "mais tentativas seguras disponíveis."
            )
        metric_prefix = (
            "intelligence_recovery" if intelligence_mode else "source_recovery"
        )
        state.research_metrics = {
            **state.research_metrics,
            "total_query_count": budget.logical_queries,
            "executed_queries_by_task": executed,
            f"{metric_prefix}_round": next_round,
            f"{metric_prefix}_new_candidate_count": len(new_documents),
            f"{metric_prefix}_attempts": [
                *state.research_metrics.get(f"{metric_prefix}_attempts", []),
                *attempts,
            ][-240:],
            "last_recovery_mode": (
                "intelligence" if intelligence_mode else "source_coverage"
            ),
            f"{metric_prefix}_failure_categories": [
                *state.research_metrics.get(f"{metric_prefix}_failure_categories", []),
                *failure_categories,
            ][-100:],
            "markets_by_task": {
                key: sorted(value) for key, value in markets_by_task.items()
            },
            "languages_by_task": {
                key: sorted(value) for key, value in languages_by_task.items()
            },
            "providers_used": sorted(providers_used),
            "search_diagnostic_totals": diagnostic_totals,
        }
        self._persist_search_runtime(state, budget=budget, circuits=circuits)
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.intelligence.recovery_completed"
            if intelligence_mode
            else "v3.sources.recovery_completed",
            "targeted_source_recovery",
            {
                "mode": "intelligence" if intelligence_mode else "source_coverage",
                "round": next_round,
                "targeted_task_ids": targeted_task_ids,
                "new_candidate_count": len(new_documents),
                "attempt_count": len(attempts),
                "failure_categories": failure_categories[:30],
                "budget": budget.as_payload(),
                "provider_circuits": circuits.as_payload(),
                "exhausted": exhausted,
            },
            idempotency_key=(
                f"v3.intelligence.recovery:{next_round}"
                if intelligence_mode
                else f"v3.sources.recovery:{next_round}"
            ),
            context=self._stage_context,
        )
        return state

    async def knowledge_synthesizer(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "knowledge_synthesizer",
            "Extraindo, triangulando e sintetizando conhecimento",
            state,
        )
        if state.contract_id is None:
            raise V3PipelineBlocked("Knowledge contract ID is missing")
        contract = ContentKnowledgeContract.model_validate(state.contract)
        is_procedural = (
            contract.content_type == EditorialContentTypeV3.procedural_decision_guide
        )
        plan = V3ResearchPlan.model_validate(state.research_plan)
        coverage_report = dict(state.source_coverage_report or {})
        if coverage_report and coverage_report.get("status") != "passed":
            raise V3PipelineBlocked(
                "A síntese foi impedida porque a cobertura de fontes ainda está incompleta.",
                str(
                    coverage_report.get("suggested_blocking_code")
                    or state.blocking_code
                    or "V3_RESEARCH_COVERAGE_INCOMPLETE"
                ),
            )
        documents = [
            StructuredSourceDocument.model_validate(item)
            for item in state.source_documents
        ]
        persisted_plan = await self.artifacts.research_plan(plan)
        await self._extract_claims_for_tasks(
            state=state,
            contract=contract,
            tasks=plan.tasks,
            documents=documents,
            persisted_plan=persisted_plan,
        )
        await self.artifacts.approve_claim_bundles(procedural_context=is_procedural)
        await self._supplement_research(
            state=state,
            contract=contract,
            plan=plan,
            persisted_plan=persisted_plan,
            is_procedural=is_procedural,
        )
        claims = await self.artifacts.knowledge_claims(approved_only=True)
        minimum_approved_claims = int(self._flag("v3_min_approved_claims"))
        failed_task_ids = set(
            state.research_metrics.get("claim_extraction_failed_task_ids") or []
        )
        if len(claims) < minimum_approved_claims and failed_task_ids:
            retry_tasks = [
                task for task in plan.tasks if task.task_id in failed_task_ids
            ]
            if retry_tasks:
                await self._extract_claims_for_tasks(
                    state=state,
                    contract=contract,
                    tasks=retry_tasks,
                    documents=[
                        StructuredSourceDocument.model_validate(item)
                        for item in state.source_documents
                    ],
                    persisted_plan=persisted_plan,
                )
                await self.artifacts.approve_claim_bundles(
                    procedural_context=is_procedural
                )
                claims = await self.artifacts.knowledge_claims(approved_only=True)
        state.knowledge_claims = [item.model_dump(mode="json") for item in claims]
        if len(claims) < minimum_approved_claims:
            first_node = next(
                (node.node_id for node in contract.nodes if node.research_required),
                contract.nodes[0].node_id,
            )
            state.knowledge_gaps = [
                {
                    "knowledge_node_id": first_node,
                    "gap_type": "procedure_without_support"
                    if is_procedural
                    else "overbroad_question",
                    "description": (
                        "A pesquisa concluiu a leitura, mas não alcançou a quantidade "
                        "mínima de claims aprovados para materializar os dossiês."
                    ),
                    "essential": True,
                    "status": "open",
                }
            ]
            state.blocking_code = "V3_APPROVED_CLAIMS_INSUFFICIENT"
            state.blocking_reason = (
                f"Claims aprovados: {len(claims)} de {minimum_approved_claims}. "
                "A execução foi encerrada de forma controlada, sem tratar a ausência "
                "de evidência como falha técnica."
            )
            return state

        claim_key_map = {str(item.claim_id): item for item in claims}
        approved_claim_payload = [
            {**item.model_dump(mode="json"), "claim_key": str(item.claim_id)}
            for item in claims
        ]
        inventory_items = []
        references = {}
        decision_matrix = None

        if is_procedural:
            inventory_output = await self._agent_call(
                role="research_gatekeeper",
                key="method_inventory",
                attempt=1,
                input_json={"topic": contract.topic, "claims": approved_claim_payload},
                prompt=(
                    "Identifique apenas abordagens operacionalmente distintos sustentados "
                    "pelos claims aprovados. Agrupe sinônimos e variações equivalentes. "
                    "supporting_claim_keys deve usar somente claim_id fornecido. Não crie "
                    "diferenças artificiais nem transforme detalhes periféricos em abordagem."
                ),
                output_schema=MethodInventoryOutput,
            )
            inventory = MethodInventoryOutput.model_validate(inventory_output)
            inventory_items = inventory.methods
            approved_claim_ids = set(claim_key_map)
            inventory_claim_ids = {
                key for item in inventory_items for key in item.supporting_claim_keys
            }
            if not inventory_claim_ids.issubset(approved_claim_ids):
                raise V3PipelineBlocked(
                    "Method inventory referenced claims that were not approved",
                    "V3_METHOD_INVENTORY_EVIDENCE_INVALID",
                )
            required_matches, missing_required = required_method_matches(
                contract.required_method_labels, inventory_items
            )
            if missing_required:
                raise V3PipelineBlocked(
                    "Required methods were not supported by the approved research: "
                    + ", ".join(missing_required),
                    "V3_REQUIRED_METHODS_MISSING",
                )
            minimum_claims_per_method = int(self._flag("v3_min_claims_per_method"))
            shallow_methods = [
                item.method_id
                for item in inventory_items
                if len(set(item.supporting_claim_keys)) < minimum_claims_per_method
            ]
            if shallow_methods:
                raise V3PipelineBlocked(
                    "Methods without enough approved procedural claims: "
                    + ", ".join(shallow_methods),
                    "V3_METHOD_EVIDENCE_SHALLOW",
                )
            state.required_method_matches = required_matches
            state.method_inventory = [
                item.model_dump(mode="json") for item in inventory_items
            ]
            references = self.reference_validator.select(inventory_items, documents)
            state.external_references = {
                key: value.model_dump(mode="json") for key, value in references.items()
            }
            inventory_method_ids = {item.method_id for item in inventory_items}
            missing_references = sorted(inventory_method_ids - set(references))
            if missing_references:
                raise V3PipelineBlocked(
                    "No independent, procedure-matched external reference was found for: "
                    + ", ".join(missing_references),
                    "V3_EXTERNAL_REFERENCE_INCOMPLETE",
                )
            synthesis_output = await self._agent_call(
                role="research_gatekeeper",
                key="knowledge_synthesis",
                attempt=1,
                input_json={
                    "contract": contract.model_dump(mode="json"),
                    "method_inventory": [
                        item.model_dump(mode="json") for item in inventory_items
                    ],
                    "approved_claims": approved_claim_payload,
                    "external_references": state.external_references,
                },
                prompt=(
                    "Construa dossiês completos antes da redação. Preserve os IDs e "
                    "a hierarquia do contrato. Preserve exatamente o inventário de abordagens. "
                    "Cada seção deve usar apenas claim_key fornecido. Cada abordagem precisa "
                    "de preparação, passos em ordem, finalidade, observações, correções, "
                    "condição de avanço, resultado e acompanhamento. A escolha deve ser "
                    "condicional; nunca declare um melhor abordagem universal. Não escreva o artigo."
                ),
                output_schema=KnowledgeSynthesisOutput,
            )
            synthesis = KnowledgeSynthesisOutput.model_validate(synthesis_output)
            inventory_by_id = {item.method_id: item for item in inventory_items}
            synthesis = synthesis.model_copy(
                update={
                    "methods": [
                        method.model_copy(
                            update={
                                "name": inventory_by_id[method.method_id].name,
                                "aliases": inventory_by_id[method.method_id].aliases,
                                "equivalent_variations": inventory_by_id[
                                    method.method_id
                                ].equivalent_variations,
                            }
                        )
                        for method in synthesis.methods
                        if method.method_id in inventory_by_id
                    ]
                }
            )
            if {item.method_id for item in synthesis.methods} != inventory_method_ids:
                raise V3PipelineBlocked(
                    "Knowledge synthesis changed the approved method inventory",
                    "V3_SYNTHESIS_METHOD_SET_INVALID",
                )
            methods_for_materialization = synthesis.methods
            sections_for_materialization = synthesis.sections
            decision_matrix = synthesis.decision_matrix
            gaps_for_materialization = synthesis.gaps
            all_keys: set[str] = set()
            for method in synthesis.methods:
                for step in method.steps:
                    all_keys.update(step.evidence_keys)
                    for correction in step.common_mistakes:
                        all_keys.update(correction.evidence_keys)
            for rule in synthesis.decision_matrix.rules:
                all_keys.update(rule.evidence_keys)
            inventory_method_ids = {item.method_id for item in inventory_items}
            for method in synthesis.methods:
                selected = references[method.method_id]
                if (
                    method.preferred_external_source_url is not None
                    and canonicalize_url(str(method.preferred_external_source_url))
                    != canonicalize_url(str(selected.url))
                ):
                    raise V3PipelineBlocked(
                        f"Method {method.method_id} selected an unapproved external source",
                        "V3_SYNTHESIS_EXTERNAL_REFERENCE_INVALID",
                    )
        else:
            state.required_method_matches = {}
            state.method_inventory = []
            state.external_references = {}
            synthesis_output = await self._agent_call(
                role="research_gatekeeper",
                key="knowledge_synthesis",
                attempt=1,
                input_json={
                    "contract": contract.model_dump(mode="json"),
                    "approved_claims": approved_claim_payload,
                },
                prompt=(
                    "Construa um dossiê editorial premium antes da redação. Preserve "
                    "exatamente os IDs, a ordem, as dependências, a importância e os "
                    "critérios de conclusão do contrato. Cada seção deve resolver sua "
                    "pergunta central com mecanismo, condições, limites, implicações ou "
                    "decisão conforme a função do nó, usando somente claim_key fornecido. "
                    "Não invente abordagens, passo a passo ou matriz de decisão quando o tipo "
                    "editorial não os exige. Não escreva o artigo."
                ),
                output_schema=GenericKnowledgeSynthesisOutput,
            )
            synthesis = GenericKnowledgeSynthesisOutput.model_validate(synthesis_output)
            methods_for_materialization = []
            sections_for_materialization = synthesis.sections
            gaps_for_materialization = synthesis.gaps
            all_keys = set()

        expected_sections = {node.node_id for node in contract.nodes}
        synthesized_sections = {
            item.section_id for item in sections_for_materialization
        }
        if synthesized_sections != expected_sections:
            missing = sorted(expected_sections - synthesized_sections)
            extra = sorted(synthesized_sections - expected_sections)
            raise V3PipelineBlocked(
                f"Knowledge synthesis section mismatch; missing={missing}, extra={extra}",
                "V3_SYNTHESIS_SECTION_SET_INVALID",
            )
        for section in sections_for_materialization:
            all_keys.update(section.allowed_claim_keys)
            for rule in section.decision_logic:
                all_keys.update(rule.evidence_keys)
        for gap in gaps_for_materialization:
            all_keys.update(gap.supporting_evidence_keys)
            all_keys.update(gap.conflicting_evidence_keys)
        if not all_keys.issubset(claim_key_map):
            raise V3PipelineBlocked(
                "Knowledge synthesis referenced claims that were not approved",
                "V3_SYNTHESIS_EVIDENCE_INVALID",
            )
        gap_descriptions = {item.description for item in gaps_for_materialization}
        unresolved_labels = {
            label
            for method in methods_for_materialization
            for label in method.unresolved_gaps
        } | {
            label
            for section in sections_for_materialization
            for label in section.unresolved_gaps
        }
        if not unresolved_labels.issubset(gap_descriptions):
            raise V3PipelineBlocked(
                "Dossiers referenced unresolved gaps that were not declared",
                "V3_SYNTHESIS_GAP_REFERENCE_INVALID",
            )
        methods, sections, matrix, gaps = await self.artifacts.materialize_synthesis(
            contract_id=state.contract_id,
            methods=methods_for_materialization,
            sections=sections_for_materialization,
            decision_matrix=decision_matrix,
            gaps=gaps_for_materialization,
            references=references,
        )
        state.method_dossiers = [item.model_dump(mode="json") for item in methods]
        state.section_dossiers = [item.model_dump(mode="json") for item in sections]
        state.decision_matrix = matrix.model_dump(mode="json") if matrix else {}
        state.knowledge_gaps = [item.model_dump(mode="json") for item in gaps]
        return state

    async def evidence_graph_builder(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "evidence_graph_builder",
            "Ligando perguntas, seções, claims, conflitos e fontes",
            state,
        )
        if not state.intelligence_state:
            raise V3PipelineBlocked(
                "Canonical editorial intelligence state is missing",
                "V3_INTELLIGENCE_STATE_MISSING",
            )
        intelligence = ContentIntelligenceState.model_validate(state.intelligence_state)
        provenance = await self.intelligence_repository.claim_provenance(
            include_graph_eligible=True
        )
        graph_claims = await self.artifacts.knowledge_claims(
            approved_only=False,
            intelligence_eligible=True,
        )
        state.knowledge_claims = [item.model_dump(mode="json") for item in graph_claims]
        if bool(self._flag("v3_emergent_questions_enabled")):
            maximum = int(self._flag("v3_max_emergent_questions"))
            existing_emergent = sum(
                item.origin == "emergent" for item in intelligence.questions
            )
            remaining = max(0, maximum - existing_emergent)
            if remaining:
                proposed_count = 0
                try:
                    proposal_output = EmergentEditorialQuestionsOutput.model_validate(
                        await self._agent_call(
                            role="planner",
                            key="emergent_questions",
                            attempt=state.intelligence_recovery_round + 1,
                            input_json={
                                "topic": intelligence.topic,
                                "content_objective": intelligence.content_objective,
                                "sections": [
                                    {
                                        "section_id": item.section_id,
                                        "editorial_goal": item.editorial_goal,
                                        "existing_question_ids": item.question_ids,
                                    }
                                    for item in intelligence.sections
                                ],
                                "existing_questions": [
                                    {
                                        "question_id": item.question_id,
                                        "section_id": item.section_id,
                                        "question": item.question,
                                    }
                                    for item in intelligence.questions
                                ],
                                "evidence_claims": [
                                    {
                                        "knowledge_node_id": item.get(
                                            "knowledge_node_id"
                                        ),
                                        "evidence_role": item.get("evidence_role"),
                                        "claim_text": item.get("claim_text"),
                                        "conditions": item.get("conditions", []),
                                        "limitations": item.get("limitations", []),
                                        "conclusion_status": item.get(
                                            "conclusion_status"
                                        ),
                                        "conflict_group": item.get("conflict_group"),
                                    }
                                    for item in state.knowledge_claims[:160]
                                ],
                                "maximum_questions": remaining,
                            },
                            prompt=(
                                "Analise somente as evidências já coletadas e proponha perguntas "
                                "editoriais emergentes que ainda não estejam cobertas pelo plano. "
                                "Priorize ambiguidades materiais, condições, limitações, conflitos "
                                "entre fontes, objeções reais do leitor e conclusões que ficariam "
                                "enganosas sem contexto. Use apenas section_id existente. Não "
                                "responda às perguntas, não invente fatos, não altere o escopo e "
                                "não repita perguntas existentes. Marque critical=true apenas se "
                                "a omissão tornar a seção materialmente incompleta ou enganosa."
                            ),
                            output_schema=EmergentEditorialQuestionsOutput,
                        )
                    )
                    proposed_count = len(proposal_output.questions)
                    intelligence = self.intelligence.add_emergent_questions(
                        intelligence,
                        proposals=proposal_output.questions,
                        claims=state.knowledge_claims,
                        maximum_questions=remaining,
                    )
                except Exception as exc:  # best-effort semantic enrichment
                    await self.runtime.event(
                        self.project.id,
                        self.pipeline_run.id,
                        "v3.intelligence.emergent_questions_skipped",
                        "evidence_graph_builder",
                        {"error_type": type(exc).__name__},
                        idempotency_key=(
                            "v3.intelligence.emergent_questions_skipped:"
                            f"{state.intelligence_recovery_round}"
                        ),
                        context=self._stage_context,
                    )
                else:
                    accepted_count = (
                        sum(
                            item.origin == "emergent" for item in intelligence.questions
                        )
                        - existing_emergent
                    )
                    await self.runtime.event(
                        self.project.id,
                        self.pipeline_run.id,
                        "v3.intelligence.emergent_questions_evaluated",
                        "evidence_graph_builder",
                        {
                            "proposed_count": proposed_count,
                            "accepted_count": accepted_count,
                            "maximum_questions": maximum,
                        },
                        idempotency_key=(
                            "v3.intelligence.emergent_questions_evaluated:"
                            f"{state.intelligence_recovery_round}:"
                            f"{intelligence.checksum}"
                        ),
                        context=self._stage_context,
                    )
        enriched = self.intelligence.attach_evidence(
            intelligence,
            claims=state.knowledge_claims,
            source_documents=[
                StructuredSourceDocument.model_validate(item)
                for item in state.source_documents
            ],
            section_dossiers=[
                SectionDossier.model_validate(item) for item in state.section_dossiers
            ],
            gaps=[KnowledgeGap.model_validate(item) for item in state.knowledge_gaps],
            claim_provenance=provenance,
        )
        state.intelligence_state = enriched.model_dump(mode="json")
        state.intelligence_validation = None
        state.intelligence_revision = enriched.revision
        await self.intelligence_repository.save(
            enriched,
            stage="evidence_graph_builder",
            status=enriched.lifecycle.value,
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.intelligence.evidence_graph_built",
            "evidence_graph_builder",
            self.intelligence.summary(enriched),
            idempotency_key=f"v3.intelligence.evidence_graph_built:{enriched.checksum}",
            context=self._stage_context,
        )
        return state

    async def intelligence_gate(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "intelligence_gate",
            "Validando cobertura editorial e autorização de claims por seção",
            state,
        )
        if not state.intelligence_state:
            raise V3PipelineBlocked(
                "Canonical editorial intelligence state is missing",
                "V3_INTELLIGENCE_STATE_MISSING",
            )
        intelligence = ContentIntelligenceState.model_validate(state.intelligence_state)
        report = self.intelligence.validate_writer_readiness(intelligence)
        intelligence = self.intelligence.mark_writer_ready(intelligence, report)
        state.intelligence_state = intelligence.model_dump(mode="json")
        state.intelligence_validation = report.model_dump(mode="json")
        state.intelligence_revision = intelligence.revision

        recovery_tasks: list[dict] = []
        if report.status == "blocked":
            recoverable = [
                item
                for item in report.blockers
                if item.recovery_class.value == "recoverable"
            ]
            nonrecoverable = [
                item
                for item in report.blockers
                if item.recovery_class.value != "recoverable"
            ]
            if (
                recoverable
                and not nonrecoverable
                and state.intelligence_recovery_round < 2
            ):
                plan = V3ResearchPlan.model_validate(state.research_plan)
                question_map = {
                    item.question_id: item for item in intelligence.questions
                }
                tasks_by_section: dict[str, list[ResearchTask]] = {}
                for task in plan.tasks:
                    tasks_by_section.setdefault(task.knowledge_node_id, []).append(task)
                seen: set[tuple[str, str]] = set()
                for finding in recoverable:
                    question = question_map.get(finding.question_id or "")
                    section_id = finding.section_id or (
                        question.section_id if question is not None else None
                    )
                    candidate_tasks = tasks_by_section.get(section_id or "", [])
                    if not candidate_tasks:
                        candidate_tasks = [task for task in plan.tasks if task.critical]
                    if not candidate_tasks:
                        candidate_tasks = list(plan.tasks[:1])
                    question_text = (
                        question.question
                        if question is not None
                        else str(finding.details.get("question") or finding.message)
                    )
                    query = " ".join(f"{intelligence.topic} {question_text}".split())[
                        :280
                    ]
                    for task in candidate_tasks[:2]:
                        key = (task.task_id, query.casefold())
                        if key in seen:
                            continue
                        seen.add(key)
                        recovery_tasks.append(
                            {
                                "task_id": task.task_id,
                                "section_id": section_id,
                                "question_id": finding.question_id,
                                "question": question_text,
                                "query": query,
                                "reason_code": finding.code,
                            }
                        )
                state.intelligence_recovery_tasks = recovery_tasks[:8]
                state.intelligence_recovery_exhausted = not bool(recovery_tasks)
                if recovery_tasks:
                    state.blocking_code = None
                    state.blocking_reason = None
                else:
                    state.blocking_code = "V3_INTELLIGENCE_RECOVERY_PLAN_EMPTY"
                    state.blocking_reason = (
                        "O gate identificou lacunas recuperáveis, mas não conseguiu "
                        "associá-las a tarefas de pesquisa executáveis."
                    )
            else:
                state.intelligence_recovery_tasks = []
                state.intelligence_recovery_exhausted = True
                state.blocking_code = (
                    "V3_INTELLIGENCE_RECOVERY_EXHAUSTED"
                    if recoverable and not nonrecoverable
                    else "V3_INTELLIGENCE_GATE_NONRECOVERABLE"
                )
                state.blocking_reason = (
                    "O Motor de Inteligência Editorial permaneceu bloqueado após "
                    "as rodadas máximas de recuperação."
                    if recoverable and not nonrecoverable
                    else "O Motor de Inteligência Editorial encontrou inconsistências "
                    "de contrato ou integridade que não podem ser corrigidas por pesquisa."
                )
        else:
            state.intelligence_recovery_tasks = []
            state.intelligence_recovery_exhausted = False
            state.blocking_code = None
            state.blocking_reason = None

        await self.intelligence_repository.save(
            intelligence,
            stage="intelligence_gate",
            status=intelligence.lifecycle.value,
            validation=state.intelligence_validation,
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.intelligence.writer_readiness_checked",
            "intelligence_gate",
            {
                **self.intelligence.summary(intelligence),
                "blocker_codes": [item.code for item in report.blockers],
                "warning_codes": [item.code for item in report.warnings],
                "recovery_round": state.intelligence_recovery_round,
                "recovery_task_count": len(state.intelligence_recovery_tasks),
                "recovery_tasks": state.intelligence_recovery_tasks,
            },
            idempotency_key=(
                "v3.intelligence.writer_readiness_checked:"
                f"{intelligence.checksum}:{state.intelligence_recovery_round}"
            ),
            context=self._stage_context,
        )
        return state

    async def knowledge_completeness_gate(
        self, state: V3PipelineState
    ) -> V3PipelineState:
        await self._stage(
            "knowledge_completeness_gate",
            "Validando completude antes da redação",
            state,
        )
        report = self.completeness.evaluate(
            ContentKnowledgeContract.model_validate(state.contract),
            methods=[
                MethodDossier.model_validate(item) for item in state.method_dossiers
            ],
            sections=[
                SectionDossier.model_validate(item) for item in state.section_dossiers
            ],
            gaps=[KnowledgeGap.model_validate(item) for item in state.knowledge_gaps],
            decision_matrix=(
                DecisionMatrix.model_validate(state.decision_matrix)
                if state.decision_matrix
                else None
            ),
            minimum_steps_per_method=int(self._flag("v3_min_steps_per_method")),
            minimum_claims_per_method=int(self._flag("v3_min_claims_per_method")),
        )
        state.completeness_report = report.model_dump(mode="json")
        return state

    async def writer(self, state: V3PipelineState) -> V3PipelineState:
        contract = ContentKnowledgeContract.model_validate(state.contract)
        is_procedural = (
            contract.content_type == EditorialContentTypeV3.procedural_decision_guide
        )
        is_single_path_procedural = (
            contract.content_type == EditorialContentTypeV3.procedural_how_to
        )
        await self._stage(
            "writer",
            "Redigindo a peça a partir da arquitetura e dos dossiês validados",
            state,
        )
        target_word_range = self._target_word_range(state)
        generation = generation_brief(self.project, self.execution_manifest, contract)
        if not state.intelligence_state:
            raise V3PipelineBlocked(
                "Writer cannot run without the canonical Editorial Intelligence state",
                "V3_WRITER_INTELLIGENCE_STATE_MISSING",
            )
        intelligence_state = ContentIntelligenceState.model_validate(
            state.intelligence_state
        )
        if intelligence_state.lifecycle not in {
            IntelligenceLifecycle.writer_ready,
            IntelligenceLifecycle.draft_validated,
        }:
            raise V3PipelineBlocked(
                "Writer cannot run before the Editorial Intelligence gate passes",
                "V3_WRITER_INTELLIGENCE_NOT_READY",
            )
        intelligence_payload = self.intelligence.writer_payload(intelligence_state)
        active_sections = set(active_node_ids(contract))
        writer_allowed_claim_ids = {
            str(claim_id)
            for section in intelligence_state.sections
            for claim_id in section.allowed_claim_ids
        }
        claim_catalog = [
            {
                "claim_id": item.get("claim_id"),
                "support_group": item.get("support_group"),
                "source_claim_ids": item.get("source_claim_ids") or [],
                "claim_text": item.get("claim_text"),
                "knowledge_node_id": item.get("knowledge_node_id"),
                "evidence_role": item.get("evidence_role"),
                "method_ids": item.get("method_ids") or [],
                "conditions": item.get("conditions") or [],
                "limitations": item.get("limitations") or [],
                "applicability": item.get("applicability") or [],
                "conclusion_status": item.get("conclusion_status"),
                "confidence_score": item.get("confidence_score"),
            }
            for item in state.knowledge_claims
            if str(item.get("knowledge_node_id") or "") in active_sections
            and str(item.get("claim_id") or "") in writer_allowed_claim_ids
        ]
        method_labels = [
            label
            for dossier in state.method_dossiers
            for label in [
                str(dossier.get("name") or ""),
                *[str(item) for item in dossier.get("aliases", [])],
            ]
            if label
        ]
        writer_input = {
            "contract": contract.model_dump(mode="json"),
            "section_dossiers": state.section_dossiers,
            "method_dossiers": state.method_dossiers,
            "decision_matrix": state.decision_matrix or None,
            "external_references": state.external_references,
            "required_method_matches": state.required_method_matches,
            "approved_claim_ids": [item["claim_id"] for item in claim_catalog],
            "claim_catalog": claim_catalog,
            "generation_brief": generation,
            "editorial_intelligence": intelligence_payload,
            "voice_brief": (generation.get("brand") or {}).get("tone_of_voice") or "",
            "target_word_range": list(target_word_range),
            "node_resolution": contract.metadata.get("node_resolution") or {},
            "editorial_sequence": [
                {
                    "section_id": node.node_id,
                    "sequence": node.sequence,
                    "universal_role": node.universal_role.value,
                    "importance": node.importance.value,
                    "applicability": node.applicability.value,
                    "minimum_depth_weight": node.minimum_depth_weight,
                    "maximum_depth_weight": node.maximum_depth_weight,
                    "depends_on": node.depends_on,
                    "title_function": node.title_function,
                    "editorial_goal": node.editorial_goal,
                    "reader_state_before": node.reader_state_before,
                    "reader_state_after": node.reader_state_after,
                    "completion_criteria": node.completion_criteria,
                }
                for node in contract.nodes
                if node.node_id in active_sections
            ],
            "opening_requirements": {
                "orient_reader_before_detail": True,
                "minimum_method_mentions": (
                    min(2, len(state.method_dossiers)) if is_procedural else 0
                ),
                "method_labels": method_labels,
                "avoid_numeric_density_in_first_words": 340,
                "opening_purpose": (
                    "situar a decisão do leitor e apresentar os caminhos antes das "
                    "condições compartilhadas"
                    if is_procedural
                    else "estabelecer o problema, o contexto e o modelo mental necessário "
                    "para compreender o restante da peça"
                ),
            },
            "body_prose_requirements": {
                "develop_reasoning_instead_of_fact_summaries": True,
                "vary_sentence_and_paragraph_shape_by_function": True,
                "connect_each_node_to_previous_and_next": True,
                "avoid_repeating_heading_in_first_sentence": True,
                "avoid_one_paragraph_per_heading": True,
                "preserve_direct_specific_headings": True,
                "core_nodes_must_receive_more_depth_than_peripheral_nodes": True,
            },
        }
        try:
            writer_input, budget_report = self.context_budget.compact_writer_input(
                writer_input,
                maximum_characters=max(
                    10_000,
                    int(settings.agent_task_data_max_characters * 0.80),
                ),
            )
        except ContextBudgetExceeded as exc:
            state.context_budget_report = exc.report.as_payload()
            raise V3PipelineBlocked(
                str(exc),
                "V3_WRITER_CONTEXT_BUDGET_EXCEEDED",
            ) from exc
        state.context_budget_report = budget_report.as_payload()
        locale = str(generation.get("locale") or self.project.language or "pt-BR")
        common_prompt = (
            f"Escreva conteúdo editorial premium no idioma e variante {locale} a partir do "
            "contrato validado, do briefing de geração e do estado canônico de Inteligência Editorial. "
            "O mapa de perguntas, os planos de seção, as políticas de uso dos claims e os conflitos "
            "presentes em editorial_intelligence são restrições obrigatórias, não sugestões. "
            "A ordem dos nós ativos, "
            "suas dependências, importância, estado "
            "do leitor e critérios de conclusão são obrigatórios. Faça cada seção resolver "
            "a função do nó e preparar a seguinte; não trate o texto como respostas coladas. "
            "Nós centrais precisam de desenvolvimento proporcionalmente maior que nós de "
            "apoio ou periféricos. Desenvolva raciocínio, mecanismo, condição, implicação, "
            "decisão ou exceção conforme a função real do nó, sem preencher elementos que "
            "não se aplicam. Cada frase factual deve citar claim_id aprovado. Títulos e subtítulos "
            "também podem ser factuais: quando contiverem números, comparação, causalidade, promessa "
            "ou conclusão verificável, devem carregar evidência no bloco correspondente. Transições "
            "puramente editoriais não recebem evidência. Não escreva uma frase por fonte, não use todos "
            "os claims por obrigação e não "
            "crie fatos, experiência pessoal ou autoridade inexistente. Respeite a faixa de "
            "palavras, varie ritmo e extensão dos parágrafos e evite linguagem de template. "
            "Quando usar tabela, preencha table_headers e table_rows com células tipadas; não codifique "
            "linhas com barras verticais em sentences. Quando usar callout, use callout_kind e trate "
            "callout_title como uma sentença rastreável, com evidência quando factual. "
            "Inclua exatamente um bloco H1, idêntico ao campo title. "
            "covered_section_ids deve conter todos e somente os nós ativos indicados em "
            "editorial_sequence, na mesma progressão representada pelos blocos. Respeite público, "
            "objetivo, marca, oferta, CTA, claims proibidos, seções obrigatórias e política de links "
            "do generation_brief; não invente oferta nem CTA quando esses campos estiverem vazios. "
            "Cada sentença deve possuir sentence_id UUID estável. Para responder uma pergunta "
            "editorial, preencha question_ids somente com IDs autorizados para a seção e marque "
            "answer_status como direct, partial ou contextual. Toda pergunta crítica deve ter ao "
            "menos uma resposta direct semanticamente explícita. Em revisões, preserve o sentence_id "
            "quando a sentença representa a mesma unidade lógica e gere novo UUID somente para uma "
            "sentença realmente nova. Prefira frases factuais atômicas: não combine proposições "
            "independentes sustentadas por claims diferentes na mesma sentença."
        )
        procedural_prompt = (
            " Para esta arquitetura procedural, apresente as abordagens antes de aprofundar "
            "condições, comparação e escolha. Depois desenvolva a execução completa de cada "
            "abordagem, sinais observáveis, problemas, correções, transições, acompanhamento e "
            "resultado. Use method_id exatamente como no dossiê nos blocos específicos e "
            "null nos blocos gerais. covered_method_ids deve conter todas e somente as "
            "abordagens aprovadas. Cada abordagem exige heading, preparação, lista de passos, "
            "observações, critério de avanço e link externo aprovado."
        )
        how_to_prompt = (
            " Esta arquitetura é um procedimento de caminho único. Desenvolva pré-requisitos, "
            "preparação aplicável, ações em ordem, propósito de cada etapa, sinais observáveis, "
            "problemas, correções e resultado final. Não invente alternativas concorrentes, "
            "method_id, comparação ou matriz de decisão. Listas são permitidas quando representam "
            "uma sequência ou checklist real sustentado pelos dossiês."
        )
        generic_prompt = (
            " Esta arquitetura não é procedural. Não invente abordagens, materiais, etapas ou "
            "matriz de decisão. Use os papéis universais e os dossiês de seção para construir "
            "a progressão adequada ao tipo editorial. covered_method_ids deve ficar vazio e "
            "method_id deve ser null em todos os blocos."
        )
        mode_prompt = (
            procedural_prompt
            if is_procedural
            else how_to_prompt
            if is_single_path_procedural
            else generic_prompt
        )
        if bool(self._optional_flag("v3_incremental_writer_enabled", False)):
            draft = await self._write_incremental_sections(
                state,
                writer_input=writer_input,
                common_prompt=common_prompt,
                mode_prompt=mode_prompt,
                target_word_range=target_word_range,
            )
        else:
            output = await self._agent_call(
                role="writer",
                key="article",
                attempt=1,
                input_json=writer_input,
                prompt=common_prompt + mode_prompt,
                output_schema=V3WriterOutput,
            )
            draft = V3WriterOutput.model_validate(output)
        pending_intelligence = self.intelligence.mark_draft_pending(
            ContentIntelligenceState.model_validate(state.intelligence_state)
        )
        state.intelligence_state = pending_intelligence.model_dump(mode="json")
        state.intelligence_revision = pending_intelligence.revision
        self._validate_draft_evidence(draft, state)
        diagnostics = self._draft_diagnostics(
            state,
            draft,
            minimum_word_count=target_word_range[0],
            maximum_word_count=target_word_range[1],
        )
        diagnostics, intelligence_draft_report = self._merge_intelligence_diagnostics(
            state, draft, diagnostics
        )
        max_repairs = int(self._flag("v3_writer_repair_attempts"))
        if diagnostics["blockers"] and state.writer_repair_count < max_repairs:
            repair_input = {
                **writer_input,
                "draft_to_repair": draft.model_dump(mode="json"),
                "deterministic_diagnostics": diagnostics,
            }
            try:
                repair_input, repair_budget_report = (
                    self.context_budget.compact_writer_input(
                        repair_input,
                        maximum_characters=max(
                            10_000,
                            int(settings.agent_task_data_max_characters * 0.90),
                        ),
                    )
                )
            except ContextBudgetExceeded as exc:
                state.context_budget_report = exc.report.as_payload()
                raise V3PipelineBlocked(
                    str(exc),
                    "V3_WRITER_REPAIR_CONTEXT_BUDGET_EXCEEDED",
                ) from exc
            state.context_budget_report = {
                **(state.context_budget_report or {}),
                "repair": repair_budget_report.as_payload(),
            }
            repaired = await self._agent_call(
                role="writer",
                key="article_repair",
                attempt=state.writer_repair_count + 2,
                input_json=repair_input,
                prompt=(
                    "Repare o V3WriterOutput completo usando somente o contrato, os "
                    "dossiês e claims aprovados. Corrija cada blocker determinístico, "
                    "preserve evidências válidas, a identidade dos nós e a progressão. "
                    "Não crie fatos nem seções fora do contrato. Reequilibre profundidade "
                    "quando um nó periférico estiver maior que um nó central e respeite a "
                    "faixa de palavras. "
                    + (
                        procedural_prompt
                        if is_procedural
                        else how_to_prompt
                        if is_single_path_procedural
                        else generic_prompt
                    )
                ),
                output_schema=V3WriterOutput,
            )
            draft = V3WriterOutput.model_validate(repaired)
            state.writer_repair_count += 1
            pending_intelligence = self.intelligence.mark_draft_pending(
                ContentIntelligenceState.model_validate(state.intelligence_state)
            )
            state.intelligence_state = pending_intelligence.model_dump(mode="json")
            state.intelligence_revision = pending_intelligence.revision
            self._validate_draft_evidence(draft, state)
            diagnostics = self._draft_diagnostics(
                state,
                draft,
                minimum_word_count=target_word_range[0],
                maximum_word_count=target_word_range[1],
            )
            diagnostics, intelligence_draft_report = (
                self._merge_intelligence_diagnostics(state, draft, diagnostics)
            )
        state.writer_diagnostics = diagnostics
        state.brief_compliance_report = diagnostics
        hard_blocker_codes = {
            "DRAFT_SECTION_INCOMPLETE",
            "DRAFT_SECTION_ORDER_INVALID",
            "DRAFT_DEPENDENCY_INVALID",
            "DRAFT_CORE_NODE_TOO_SHALLOW",
            "DRAFT_PERIPHERAL_DEPTH_INVERSION",
            "DRAFT_METHODS_PRESENTED_TOO_LATE",
            "DRAFT_METHOD_INCOMPLETE",
            "DRAFT_METHOD_UNKNOWN",
            "DRAFT_METHOD_WITHOUT_BLOCKS",
            "DRAFT_METHOD_WITHOUT_HEADING",
            "DRAFT_METHOD_STEPS_SHALLOW",
            "DRAFT_METHOD_REFERENCE_MISSING",
            "DRAFT_NONPROCEDURAL_METHOD_LEAK",
            "DRAFT_TOO_SHORT",
            "DRAFT_TOO_LONG",
            "DRAFT_H1_COUNT_INVALID",
            "DRAFT_H1_TITLE_MISMATCH",
            "DRAFT_MINIMUM_H2_NOT_MET",
            "DRAFT_MINIMUM_H3_NOT_MET",
            "DRAFT_REQUIRED_SECTION_MISSING",
            "DRAFT_PROHIBITED_CLAIM_PRESENT",
            "DRAFT_INTERNAL_LINK_MISSING",
            "DRAFT_LANGUAGE_MISMATCH",
            "DRAFT_PRIMARY_KEYWORD_TITLE_MISSING",
            "DRAFT_PRIMARY_KEYWORD_BODY_MISSING",
            "DRAFT_OFFER_MISSING",
            "DRAFT_CTA_MISSING",
            "DRAFT_STYLE_EXAMPLE_COPIED",
        }
        hard_blockers = [
            item
            for item in diagnostics["blockers"]
            if item["code"] in hard_blocker_codes
            or str(item["code"]).startswith("INTELLIGENCE_")
        ]
        if hard_blockers:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "v3.writer.preflight_blocked",
                "writer",
                {**diagnostics, "hard_blockers": hard_blockers},
                idempotency_key="v3.writer.preflight_blocked",
                context=self._stage_context,
            )
            raise V3PipelineBlocked(
                "Writer preflight still contains structural blockers: "
                + ", ".join(item["code"] for item in hard_blockers),
                "V3_DRAFT_PREFLIGHT_BLOCKED",
            )
        event_name = (
            "v3.writer.preflight_deferred_to_editors"
            if diagnostics["blockers"]
            else "v3.writer.preflight_passed"
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            event_name,
            "writer",
            diagnostics,
            idempotency_key=event_name,
            context=self._stage_context,
        )
        state.draft = draft.model_dump(mode="json")
        if intelligence_draft_report is not None:
            intelligence_state = self.intelligence.mark_draft_validated(
                ContentIntelligenceState.model_validate(state.intelligence_state),
                intelligence_draft_report,
                draft=draft,
            )
            state.intelligence_state = intelligence_state.model_dump(mode="json")
            state.intelligence_validation = intelligence_draft_report.model_dump(
                mode="json"
            )
            state.intelligence_revision = intelligence_state.revision
            await self.intelligence_repository.save(
                intelligence_state,
                stage="writer",
                status=intelligence_state.lifecycle.value,
                validation=state.intelligence_validation,
            )
        return state

    async def _write_incremental_sections(
        self,
        state: V3PipelineState,
        *,
        writer_input: dict,
        common_prompt: str,
        mode_prompt: str,
        target_word_range: tuple[int, int],
    ) -> V3WriterOutput:
        sequence = list(writer_input.get("editorial_sequence") or [])
        section_ids = [str(item.get("section_id") or "") for item in sequence]
        if not section_ids or any(not item for item in section_ids):
            raise V3PipelineBlocked(
                "Incremental writer received an empty or invalid editorial sequence",
                "V3_INCREMENTAL_WRITER_SEQUENCE_INVALID",
            )
        if len(section_ids) != len(set(section_ids)):
            raise V3PipelineBlocked(
                "Incremental writer received duplicate section IDs",
                "V3_INCREMENTAL_WRITER_SEQUENCE_DUPLICATE",
            )

        persisted_ids = set(state.writer_sections)
        unknown_persisted = sorted(persisted_ids - set(section_ids))
        if unknown_persisted:
            raise V3PipelineBlocked(
                "Persisted writer units no longer belong to the fixed editorial sequence: "
                + ", ".join(unknown_persisted),
                "V3_INCREMENTAL_WRITER_CHECKPOINT_DRIFT",
            )

        completed = list(state.writer_completed_section_ids)
        expected_prefix = section_ids[: len(completed)]
        if completed != expected_prefix:
            raise V3PipelineBlocked(
                "Persisted writer completion order is not a prefix of the fixed editorial sequence",
                "V3_INCREMENTAL_WRITER_COMPLETION_ORDER_INVALID",
            )
        section_ranges = self._writer_section_word_ranges(
            sequence, target_word_range=target_word_range
        )
        minimum_block_counts = self._writer_section_minimum_block_counts(len(sequence))
        maximum_block_counts = self._writer_section_maximum_block_counts(
            len(sequence), minimum_block_counts=minimum_block_counts
        )
        total = len(sequence)
        for index, section in enumerate(sequence):
            section_id = section_ids[index]
            if section_id in completed:
                try:
                    self._validate_writer_section_unit(
                        state.writer_sections[section_id],
                        state=state,
                        expected_section_id=section_id,
                        first=index == 0,
                        minimum_blocks=minimum_block_counts[index],
                        maximum_blocks=maximum_block_counts[index],
                        target_word_range=section_ranges[index],
                    )
                except (ValidationError, ValueError, V3PipelineBlocked) as exc:
                    raise V3PipelineBlocked(
                        f"Persisted writer unit {section_id} is invalid: {exc}",
                        "V3_INCREMENTAL_WRITER_UNIT_INVALID",
                    ) from exc
                continue

            state.writer_progress = {
                "status": "running",
                "current_section_id": section_id,
                "completed": len(completed),
                "total": total,
            }
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "v3.writer.unit_started",
                "writer",
                {
                    "message": f"Redigindo informação {index + 1} de {total}: {section_id}",
                    "section_id": section_id,
                    "position": index + 1,
                    "total": total,
                },
                idempotency_key=f"v3.writer.unit_started:{section_id}",
                context=self._stage_context,
            )

            section_range = section_ranges[index]
            section_input = self._writer_section_input(
                writer_input,
                section=section,
                section_id=section_id,
                section_index=index,
                section_count=total,
                target_word_range=section_range,
                minimum_block_count=minimum_block_counts[index],
                maximum_block_count=maximum_block_counts[index],
                state=state,
            )
            try:
                section_input, section_budget = (
                    self.context_budget.compact_writer_input(
                        section_input,
                        maximum_characters=max(
                            10_000,
                            int(settings.agent_task_data_max_characters * 0.70),
                        ),
                    )
                )
            except ContextBudgetExceeded as exc:
                state.context_budget_report = {
                    **(state.context_budget_report or {}),
                    f"writer_section:{section_id}": exc.report.as_payload(),
                }
                raise V3PipelineBlocked(
                    str(exc),
                    "V3_WRITER_SECTION_CONTEXT_BUDGET_EXCEEDED",
                ) from exc
            state.context_budget_report = {
                **(state.context_budget_report or {}),
                f"writer_section:{section_id}": section_budget.as_payload(),
            }

            unit_common_prompt = common_prompt.replace(
                "Inclua exatamente um bloco H1, idêntico ao campo title. ",
                "O artigo completo terá exatamente um H1 idêntico ao title; somente a primeira unidade pode criá-lo. ",
            ).replace(
                "covered_section_ids deve conter todos e somente os nós ativos indicados em editorial_sequence, na mesma progressão representada pelos blocos. ",
                "Cada unidade deve conter blocos somente para current_section e usar exatamente o section_id solicitado. ",
            )
            unit_mode_prompt = mode_prompt.replace(
                "covered_method_ids deve conter todas e somente as abordagens aprovadas.",
                "covered_method_ids deve conter somente as abordagens efetivamente desenvolvidas nesta unidade.",
            )
            boundary_prompt = (
                " Gere somente a unidade da seção current_section; não escreva, resuma "
                "nem antecipe as demais seções. Use posições locais iniciando em zero e "
                f"produza entre {minimum_block_counts[index]} e "
                f"{maximum_block_counts[index]} blocos úteis. "
            )
            if index == 0:
                boundary_prompt += (
                    "Esta é a primeira unidade: title deve ser preenchido e deve existir "
                    "exatamente um bloco H1 cujo texto seja idêntico a title. "
                )
            else:
                boundary_prompt += (
                    "Esta não é a primeira unidade: title deve ser null, não gere H1 e "
                    "inicie a seção com H2 ou H3 apropriado. "
                )
            boundary_prompt += (
                "Cada bloco deve usar exatamente o section_id atual. Mantenha conexão "
                "natural com previous_writer_context, resolva os critérios de conclusão "
                "do nó e não repita informações já concluídas."
            )
            output = await self._agent_call(
                role="writer",
                key=f"article_section:{section_id}",
                attempt=1,
                input_json=section_input,
                prompt=unit_common_prompt + unit_mode_prompt + boundary_prompt,
                output_schema=V3WriterSectionOutput,
            )
            try:
                unit = self._validate_writer_section_unit(
                    output,
                    state=state,
                    expected_section_id=section_id,
                    first=index == 0,
                    minimum_blocks=minimum_block_counts[index],
                    maximum_blocks=maximum_block_counts[index],
                    target_word_range=section_range,
                )
            except (ValidationError, ValueError, V3PipelineBlocked) as exc:
                repair_limit = int(
                    self._optional_flag(
                        "v3_writer_section_repair_attempts",
                        settings.v3_writer_section_repair_attempts,
                    )
                )
                last_error: Exception = exc
                repair_source = output
                unit = None
                for repair_attempt in range(1, repair_limit + 1):
                    state.writer_section_repair_counts[section_id] = repair_attempt
                    repair_input = {
                        **section_input,
                        "writer_unit_to_repair": repair_source,
                        "unit_validation_error": str(last_error)[:4000],
                    }
                    try:
                        repair_input, repair_budget = (
                            self.context_budget.compact_writer_input(
                                repair_input,
                                maximum_characters=max(
                                    10_000,
                                    int(settings.agent_task_data_max_characters * 0.85),
                                ),
                            )
                        )
                    except ContextBudgetExceeded as budget_exc:
                        state.context_budget_report = {
                            **(state.context_budget_report or {}),
                            f"writer_section_repair:{section_id}:{repair_attempt}": budget_exc.report.as_payload(),
                        }
                        raise V3PipelineBlocked(
                            str(budget_exc),
                            "V3_WRITER_SECTION_REPAIR_CONTEXT_BUDGET_EXCEEDED",
                        ) from budget_exc
                    state.context_budget_report = {
                        **(state.context_budget_report or {}),
                        f"writer_section_repair:{section_id}:{repair_attempt}": repair_budget.as_payload(),
                    }
                    repair_source = await self._agent_call(
                        role="writer",
                        key=f"article_section_repair:{section_id}",
                        attempt=repair_attempt,
                        input_json=repair_input,
                        prompt=(
                            "Repare somente a unidade current_section. Corrija o erro "
                            "determinístico informado sem alterar o escopo, sem criar claims "
                            "e sem escrever outras seções. Respeite a faixa de palavras, "
                            "os limites mínimo e máximo de blocos, as evidências autorizadas, as "
                            "posições locais e as regras de H1/title da unidade."
                        ),
                        output_schema=V3WriterSectionOutput,
                    )
                    try:
                        unit = self._validate_writer_section_unit(
                            repair_source,
                            state=state,
                            expected_section_id=section_id,
                            first=index == 0,
                            minimum_blocks=minimum_block_counts[index],
                            maximum_blocks=maximum_block_counts[index],
                            target_word_range=section_range,
                        )
                        break
                    except (
                        ValidationError,
                        ValueError,
                        V3PipelineBlocked,
                    ) as repair_exc:
                        last_error = repair_exc
                if unit is None:
                    raise V3PipelineBlocked(
                        f"Writer unit {section_id} remained invalid after repair: {last_error}",
                        "V3_INCREMENTAL_WRITER_UNIT_INVALID",
                    ) from last_error

            state.writer_sections[section_id] = unit.model_dump(mode="json")
            completed.append(section_id)
            state.writer_completed_section_ids = completed
            state.writer_progress = {
                "status": "running",
                "current_section_id": None,
                "completed": len(completed),
                "total": total,
                "last_completed_section_id": section_id,
            }
            await self._progress_checkpoint(
                "writer",
                state,
                unit_id=section_id,
                completed=len(completed),
                total=total,
            )

        raw_draft = self._assemble_writer_section_payloads(state, section_ids)
        try:
            draft = V3WriterOutput.model_validate(raw_draft)
        except ValidationError as exc:
            max_repairs = int(self._flag("v3_writer_repair_attempts"))
            if state.writer_repair_count >= max_repairs:
                raise V3PipelineBlocked(
                    f"Incremental writer assembly is invalid: {exc}",
                    "V3_INCREMENTAL_WRITER_ASSEMBLY_INVALID",
                ) from exc
            repair_input = {
                **writer_input,
                "draft_to_repair": raw_draft,
                "assembly_validation_errors": exc.errors(include_url=False),
            }
            try:
                repair_input, repair_budget = self.context_budget.compact_writer_input(
                    repair_input,
                    maximum_characters=max(
                        10_000, int(settings.agent_task_data_max_characters * 0.90)
                    ),
                )
            except ContextBudgetExceeded as budget_exc:
                state.context_budget_report = {
                    **(state.context_budget_report or {}),
                    "writer_assembly_repair": budget_exc.report.as_payload(),
                }
                raise V3PipelineBlocked(
                    str(budget_exc),
                    "V3_WRITER_ASSEMBLY_REPAIR_CONTEXT_BUDGET_EXCEEDED",
                ) from budget_exc
            state.context_budget_report = {
                **(state.context_budget_report or {}),
                "writer_assembly_repair": repair_budget.as_payload(),
            }
            repaired = await self._agent_call(
                role="writer",
                key="article_assembly_repair",
                attempt=state.writer_repair_count + 2,
                input_json=repair_input,
                prompt=(
                    "Normalize e repare o rascunho montado por unidades em um único "
                    "V3WriterOutput válido. Preserve a ordem, o conteúdo factual e as "
                    "evidências das unidades; não invente claims nem seções. Garanta um "
                    "único H1 igual ao title, posições globais contíguas e todos os nós "
                    "ativos em covered_section_ids."
                ),
                output_schema=V3WriterOutput,
            )
            draft = V3WriterOutput.model_validate(repaired)
            state.writer_repair_count += 1

        state.writer_progress = {
            "status": "assembled",
            "current_section_id": None,
            "completed": total,
            "total": total,
        }
        return draft

    @staticmethod
    def _writer_section_minimum_block_counts(section_count: int) -> list[int]:
        if section_count < 1:
            raise V3PipelineBlocked(
                "Incremental writer received no sections for block allocation",
                "V3_INCREMENTAL_WRITER_SEQUENCE_INVALID",
            )
        counts = [2] * section_count
        missing = max(0, 10 - sum(counts))
        for index in range(missing):
            counts[index % section_count] += 1
        return counts

    @staticmethod
    def _writer_section_maximum_block_counts(
        section_count: int, *, minimum_block_counts: list[int]
    ) -> list[int]:
        if section_count < 1 or len(minimum_block_counts) != section_count:
            raise V3PipelineBlocked(
                "Incremental writer received an invalid block allocation",
                "V3_INCREMENTAL_WRITER_BLOCK_BUDGET_INVALID",
            )
        article_maximum = 300
        preferred_per_section = 30
        quotient, remainder = divmod(article_maximum, section_count)
        maximums = [
            min(preferred_per_section, quotient + (1 if index < remainder else 0))
            for index in range(section_count)
        ]
        if any(
            maximums[index] < minimum_block_counts[index]
            for index in range(section_count)
        ):
            raise V3PipelineBlocked(
                "The article block budget is too small for the active section structure",
                "V3_INCREMENTAL_WRITER_BLOCK_BUDGET_INFEASIBLE",
            )
        return maximums

    @staticmethod
    def _writer_section_word_ranges(
        sequence: list[dict],
        *,
        target_word_range: tuple[int, int],
    ) -> list[tuple[int, int]]:
        section_count = len(sequence)
        minimum_total, maximum_total = target_word_range
        if section_count < 1 or minimum_total < 1 or maximum_total < minimum_total:
            raise V3PipelineBlocked(
                "Incremental writer received an invalid article word budget",
                "V3_INCREMENTAL_WRITER_WORD_BUDGET_INVALID",
            )

        # Prefer enough room for two useful blocks while adapting to blueprints
        # with many active sections. The complete allocation never exceeds the
        # article maximum, so section prompts cannot collectively force an
        # overlong draft.
        preferred_floor = 120
        absolute_floor = 40
        per_section_floor = min(preferred_floor, maximum_total // section_count)
        if per_section_floor < absolute_floor:
            raise V3PipelineBlocked(
                "The article word budget is too small for the number of active sections",
                "V3_INCREMENTAL_WRITER_WORD_BUDGET_INFEASIBLE",
            )

        minimum_weights = [
            max(0.1, float(item.get("minimum_depth_weight") or 1.0))
            for item in sequence
        ]
        maximum_weights = [
            max(0.1, float(item.get("maximum_depth_weight") or 1.0))
            for item in sequence
        ]

        def allocate(
            total: int, lower_bounds: list[int], weights: list[float]
        ) -> list[int]:
            remaining = total - sum(lower_bounds)
            if remaining < 0:
                raise V3PipelineBlocked(
                    "The incremental writer allocation exceeds the article word budget",
                    "V3_INCREMENTAL_WRITER_WORD_BUDGET_INFEASIBLE",
                )
            if remaining == 0:
                return list(lower_bounds)
            weight_total = sum(weights)
            raw = [remaining * weight / weight_total for weight in weights]
            extras = [int(value) for value in raw]
            remainder = remaining - sum(extras)
            order = sorted(
                range(section_count),
                key=lambda index: (raw[index] - extras[index], weights[index], -index),
                reverse=True,
            )
            for index in order[:remainder]:
                extras[index] += 1
            return [
                lower_bounds[index] + extras[index] for index in range(section_count)
            ]

        minimum_budget_total = max(minimum_total, per_section_floor * section_count)
        minimums = allocate(
            minimum_budget_total,
            [per_section_floor] * section_count,
            minimum_weights,
        )
        maximums = allocate(maximum_total, minimums, maximum_weights)
        return list(zip(minimums, maximums, strict=True))

    def _writer_section_input(
        self,
        writer_input: dict,
        *,
        section: dict,
        section_id: str,
        section_index: int,
        section_count: int,
        target_word_range: tuple[int, int],
        minimum_block_count: int,
        maximum_block_count: int,
        state: V3PipelineState,
    ) -> dict:
        previous_context: list[dict] = []
        for previous_id in state.writer_completed_section_ids[-2:]:
            payload = state.writer_sections.get(previous_id) or {}
            blocks = payload.get("blocks") or []
            previous_context.append(
                {
                    "section_id": previous_id,
                    "scope_confirmation": payload.get("scope_confirmation"),
                    "closing_blocks": blocks[-3:],
                }
            )
        section_dossiers = [
            item
            for item in writer_input.get("section_dossiers") or []
            if str(item.get("section_id") or item.get("knowledge_node_id") or "")
            == section_id
        ]
        allowed_claim_ids = {
            str(claim_id)
            for dossier in section_dossiers
            for claim_id in dossier.get("allowed_claim_ids") or []
        }
        intelligence = writer_input.get("editorial_intelligence") or {}
        for plan in intelligence.get("section_plans") or []:
            if str(plan.get("section_id") or "") == section_id:
                allowed_claim_ids.update(
                    str(claim_id) for claim_id in plan.get("allowed_claim_ids") or []
                )
        claim_catalog = [
            item
            for item in writer_input.get("claim_catalog") or []
            if str(item.get("claim_id") or "") in allowed_claim_ids
            or str(item.get("knowledge_node_id") or "") == section_id
        ]
        return {
            **writer_input,
            "current_section": section,
            "current_section_index": section_index,
            "section_count": section_count,
            "target_word_range": list(target_word_range),
            "section_dossiers": section_dossiers,
            "claim_catalog": claim_catalog,
            "approved_claim_ids": [item.get("claim_id") for item in claim_catalog],
            "previous_writer_context": previous_context,
            "writer_unit_contract": {
                "one_section_only": True,
                "local_block_positions": True,
                "minimum_block_count": minimum_block_count,
                "maximum_block_count": maximum_block_count,
                "first_unit": section_index == 0,
                "last_unit": section_index == section_count - 1,
            },
        }

    @staticmethod
    def _validate_writer_section_boundary(
        unit: V3WriterSectionOutput,
        *,
        first: bool,
        minimum_blocks: int = 2,
        maximum_blocks: int = 60,
    ) -> None:
        if len(unit.blocks) < minimum_blocks:
            raise ValueError(f"Writer unit requires at least {minimum_blocks} blocks")
        if len(unit.blocks) > maximum_blocks:
            raise ValueError(f"Writer unit allows at most {maximum_blocks} blocks")
        h1_blocks = [block for block in unit.blocks if block.type == "h1"]
        if first:
            if unit.title is None or len(h1_blocks) != 1:
                raise ValueError(
                    "The first writer unit requires title and exactly one H1"
                )
            h1_text = h1_blocks[0].sentences[0].text.strip()
            if h1_text != unit.title.strip():
                raise ValueError("The first writer unit H1 must be identical to title")
        else:
            if unit.title is not None or h1_blocks:
                raise ValueError("Only the first writer unit may contain title or H1")
            if unit.blocks[0].type not in {"h2", "h3"}:
                raise ValueError(
                    "Every subsequent writer unit must begin with H2 or H3"
                )

    def _validate_writer_section_unit(
        self,
        payload: dict | V3WriterSectionOutput,
        *,
        state: V3PipelineState,
        expected_section_id: str,
        first: bool,
        minimum_blocks: int,
        maximum_blocks: int,
        target_word_range: tuple[int, int],
    ) -> V3WriterSectionOutput:
        unit = V3WriterSectionOutput.model_validate(payload)
        self._validate_writer_section_boundary(
            unit,
            first=first,
            minimum_blocks=minimum_blocks,
            maximum_blocks=maximum_blocks,
        )
        if unit.section_id != expected_section_id:
            raise ValueError(
                f"Writer returned section {unit.section_id} while "
                f"{expected_section_id} was requested"
            )
        text = " ".join(
            sentence.text
            for block in unit.blocks
            for sentence in block.content_sentences
        )
        word_count = len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", text))
        minimum_words, maximum_words = target_word_range
        if word_count < minimum_words or word_count > maximum_words:
            raise ValueError(
                "Writer unit word count is outside its allocated range "
                f"({word_count} not in {minimum_words}-{maximum_words})"
            )
        self._validate_writer_section_evidence(unit, state)
        return unit

    def _validate_writer_section_evidence(
        self, unit: V3WriterSectionOutput, state: V3PipelineState
    ) -> None:
        partial = V3WriterOutput.model_construct(
            title=unit.title or "Incremental writer section validation",
            blocks=unit.blocks,
            covered_section_ids=[unit.section_id],
            covered_method_ids=unit.covered_method_ids,
            unsupported_claims=[],
            scope_confirmation=unit.scope_confirmation,
        )
        self._validate_draft_evidence(partial, state, require_complete_scope=False)

    def _assemble_writer_section_payloads(
        self, state: V3PipelineState, section_ids: list[str]
    ) -> dict:
        blocks: list[dict] = []
        covered_methods: list[str] = []
        confirmations: list[str] = []
        title: str | None = None
        for section_index, section_id in enumerate(section_ids):
            unit = V3WriterSectionOutput.model_validate(
                state.writer_sections[section_id]
            )
            self._validate_writer_section_boundary(unit, first=section_index == 0)
            if section_index == 0:
                title = unit.title
            confirmations.append(unit.scope_confirmation)
            for method_id in unit.covered_method_ids:
                if method_id not in covered_methods:
                    covered_methods.append(method_id)
            for local_index, source_block in enumerate(unit.blocks):
                block = source_block.model_dump(mode="json")
                block["position"] = len(blocks)
                block["block_id"] = str(
                    uuid.uuid5(
                        self.pipeline_run.id,
                        f"writer:block:{section_id}:{local_index}",
                    )
                )
                self._stabilize_writer_sentence_ids(
                    block,
                    section_id=section_id,
                    block_index=local_index,
                )
                blocks.append(block)
        confirmation = " | ".join(confirmations)
        if len(confirmation) > 1000:
            confirmation = confirmation[:997].rstrip() + "..."
        return {
            "title": title,
            "blocks": blocks,
            "covered_section_ids": section_ids,
            "covered_method_ids": covered_methods,
            "unsupported_claims": [],
            "scope_confirmation": confirmation,
        }

    def _stabilize_writer_sentence_ids(
        self, block: dict, *, section_id: str, block_index: int
    ) -> None:
        def assign(sentence: dict, path: str) -> None:
            sentence["sentence_id"] = str(
                uuid.uuid5(
                    self.pipeline_run.id,
                    f"writer:sentence:{section_id}:{block_index}:{path}",
                )
            )

        for index, sentence in enumerate(block.get("sentences") or []):
            assign(sentence, f"sentences:{index}")
        for index, sentence in enumerate(block.get("table_headers") or []):
            assign(sentence, f"table_headers:{index}")
        for row_index, row in enumerate(block.get("table_rows") or []):
            for cell_index, sentence in enumerate(row.get("cells") or []):
                assign(sentence, f"table_rows:{row_index}:cells:{cell_index}")
        callout_title = block.get("callout_title")
        if callout_title:
            assign(callout_title, "callout_title")

    async def development_editor(self, state: V3PipelineState) -> V3PipelineState:
        contract = ContentKnowledgeContract.model_validate(state.contract)
        is_procedural = (
            contract.content_type == EditorialContentTypeV3.procedural_decision_guide
        )
        is_single_path_procedural = (
            contract.content_type == EditorialContentTypeV3.procedural_how_to
        )
        await self._stage(
            "development_editor",
            "Revisando hierarquia, promessa, sequência e utilidade",
            state,
        )
        universal_prompt = (
            "Atue como editor de desenvolvimento. Verifique o texto contra o contrato "
            "hierárquico, não apenas contra contagem de palavras. Cada nó deve cumprir sua "
            "função, respeitar dependências, transformar o estado do leitor e preparar o nó "
            "seguinte. Reprove nós centrais superficiais, inversões de ordem, seções que "
            "funcionam como fichas independentes, detalhes periféricos mais desenvolvidos "
            "que a espinha dorsal e fechamento antecipado. Confirme a promessa editorial, "
            "a utilidade prática e a adequação ao tipo de conteúdo. Marque blocked quando "
            "faltar pesquisa; use rewrite apenas para problemas corrigíveis com os dossiês."
        )
        procedural_prompt = (
            " Para a arquitetura procedural, confirme ainda que o panorama de abordagens vem "
            "antes das condições e da escolha, e que cada abordagem possui execução, sinais "
            "observáveis, correções, transição, acompanhamento e resultado."
        )
        how_to_prompt = (
            " Para o procedimento de caminho único, confirme pré-requisitos, preparação aplicável, "
            "sequência completa, propósito das etapas, sinais observáveis, diagnóstico, correções e "
            "resultado final. Reprove comparação ou múltiplos métodos artificiais."
        )
        generic_prompt = (
            " Esta arquitetura não é procedural: reprove abordagens, etapas ou materiais "
            "inventados apenas para encaixar o tema em um guia de execução."
        )
        draft, review = await self._review_and_revise(
            state,
            stage="development_editor",
            review_schema=V3DevelopmentReview,
            prompt=universal_prompt
            + (
                procedural_prompt
                if is_procedural
                else how_to_prompt
                if is_single_path_procedural
                else generic_prompt
            ),
        )
        diagnostics = self._draft_diagnostics(
            state,
            draft,
            minimum_word_count=self._target_word_range(state)[0],
            maximum_word_count=self._target_word_range(state)[1],
        )
        structural = [
            item
            for item in diagnostics["blockers"]
            if item["code"]
            in {
                "DRAFT_SECTION_INCOMPLETE",
                "DRAFT_SECTION_ORDER_INVALID",
                "DRAFT_DEPENDENCY_INVALID",
                "DRAFT_CORE_NODE_TOO_SHALLOW",
                "DRAFT_PERIPHERAL_DEPTH_INVERSION",
                "DRAFT_NONPROCEDURAL_METHOD_LEAK",
            }
        ]
        if structural:
            raise V3PipelineBlocked(
                "Development editor left hierarchy blockers: "
                + ", ".join(item["code"] for item in structural),
                "V3_DEVELOPMENT_HIERARCHY_BLOCKED",
            )
        await self._validate_intelligence_draft_stage(
            state, draft, stage="development_editor"
        )
        state.draft = draft.model_dump(mode="json")
        state.development_review = review.model_dump(mode="json")
        await self.artifacts.stage_review(
            "development_editor", state.development_review, review.status
        )
        return state

    async def fact_checker(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "fact_checker", "Verificando claims, condições e causalidade", state
        )
        draft, review = await self._review_and_revise(
            state,
            stage="fact_checker",
            review_schema=V3FactCheckReview,
            prompt=(
                "Atue como fact-checker. Compare cada frase factual com os claims aprovados e suas limitações. "
                "Verifique números, condições, causalidade, negação, exceções e força da linguagem. Não aprove uma "
                "frase apenas por compartilhar palavras com a fonte. Marque unsupported ou contradicted quando a "
                "evidência não implicar a frase. A revisão não pode inventar novos fatos. "
                "Para cada frase factual, copie exatamente block_id, sentence_id, sentence_text "
                "e os claim_ids atuais. Gere exatamente um ClaimCheck por sentence_id."
            ),
        )
        self._validate_fact_check_review(
            draft=draft, review=review, state=state, require_passed=True
        )
        await self._validate_intelligence_draft_stage(
            state, draft, stage="fact_checker"
        )
        state.draft = draft.model_dump(mode="json")
        state.fact_check = review.model_dump(mode="json")
        await self.artifacts.stage_review(
            "fact_checker", state.fact_check, review.status
        )
        return state

    async def language_editor(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "language_editor", "Editando naturalidade, ritmo e clareza", state
        )
        draft, review = await self._review_and_revise(
            state,
            stage="language_editor",
            review_schema=V3LanguageReview,
            prompt=(
                "Atue como editor de texto humano. Preserve títulos diretos e específicos quando já funcionarem, "
                "mas faça o corpo alcançar a mesma qualidade: remova cadência repetitiva, conectores genéricos, "
                "introduções previsíveis, parágrafos curtos em série, repetição do subtítulo e conclusão-resumo. "
                "Procure frases que soam como fichas de evidência condensadas e transforme os blocos em explicações "
                "com progressão, contraste e consequência. Varie o tamanho das frases e dos parágrafos conforme a "
                "função; evite começar muitas sentenças com a mesma construção. A abertura não pode despejar números "
                "antes de orientar o leitor e apresentar as abordagens. Preserve o significado e todas as evidências. "
                "Não insira erros artificiais, gírias gratuitas, vivências ou opiniões inexistentes e não tente "
                "enganar detectores: edite para uma pessoa real."
            ),
        )
        self._validate_draft_evidence(draft, state)
        await self._validate_intelligence_draft_stage(
            state, draft, stage="language_editor:pre_fact_check"
        )
        state.draft = draft.model_dump(mode="json")
        state.language_review = review.model_dump(mode="json")
        await self.artifacts.stage_review(
            "language_editor", state.language_review, review.status
        )

        # Language edits happen after the first fact-check. Run an independent,
        # read-only pass over the exact final sentences so a stylistic rewrite
        # cannot silently change a number, condition, negation or conclusion.
        final_fact_review = V3FactCheckReview.model_validate(
            await self._agent_call(
                role="fact_checker",
                key="fact_checker:post_language_review",
                attempt=1,
                input_json={
                    "draft": draft.model_dump(mode="json"),
                    "contract": state.contract,
                    "approved_claims": state.knowledge_claims,
                    "previous_fact_check": state.fact_check,
                    "language_review": state.language_review,
                    "editorial_intelligence": (
                        self.intelligence.writer_payload(
                            ContentIntelligenceState.model_validate(
                                state.intelligence_state
                            )
                        )
                        if state.intelligence_state
                        else {}
                    ),
                },
                prompt=(
                    "Execute um fact-check final e somente leitura após a edição de linguagem. "
                    "Crie exatamente um ClaimCheck para cada frase factual do rascunho atual, "
                    "copiando block_id, sentence_id e sentence_text integralmente e usando "
                    "exatamente os claim_ids da frase. Gere exatamente um ClaimCheck por "
                    "sentence_id. Não solicite reescrita. Marque passed apenas se todas as frases "
                    "forem sustentadas sem mudar número, unidade, condição, negação, causalidade "
                    "ou força da conclusão; caso contrário, marque blocked."
                ),
                output_schema=V3FactCheckReview,
            )
        )
        if final_fact_review.status == "rewrite":
            raise V3PipelineBlocked(
                "Post-language fact-check requested a rewrite after the final language pass",
                "V3_POST_LANGUAGE_FACT_CHECK_REWRITE",
            )
        self._validate_fact_check_review(
            draft=draft,
            review=final_fact_review,
            state=state,
            require_passed=True,
        )
        state.fact_check = final_fact_review.model_dump(mode="json")
        await self.artifacts.stage_review(
            "post_language_fact_checker", state.fact_check, final_fact_review.status
        )
        target_range = self._target_word_range(state)
        final_diagnostics = self._draft_diagnostics(
            state,
            draft,
            minimum_word_count=target_range[0],
            maximum_word_count=target_range[1],
        )
        final_diagnostics, intelligence_draft_report = (
            self._merge_intelligence_diagnostics(state, draft, final_diagnostics)
        )
        state.writer_diagnostics = final_diagnostics
        state.brief_compliance_report = final_diagnostics
        if final_diagnostics["blockers"]:
            raise V3PipelineBlocked(
                "A edição final deixou violações determinísticas do briefing: "
                + ", ".join(
                    item["code"] for item in final_diagnostics["blockers"][:12]
                ),
                "V3_POST_LANGUAGE_BRIEF_COMPLIANCE_BLOCKED",
            )
        if intelligence_draft_report is not None and state.intelligence_state:
            intelligence_state = self.intelligence.mark_draft_validated(
                ContentIntelligenceState.model_validate(state.intelligence_state),
                intelligence_draft_report,
                draft=draft,
            )
            state.intelligence_state = intelligence_state.model_dump(mode="json")
            state.intelligence_validation = intelligence_draft_report.model_dump(
                mode="json"
            )
            state.intelligence_revision = intelligence_state.revision
            await self.intelligence_repository.save(
                intelligence_state,
                stage="language_editor",
                status=intelligence_state.lifecycle.value,
                validation=state.intelligence_validation,
            )
        return state

    async def external_reference_gate(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "external_reference_gate",
            "Confirmando referências externas aplicáveis",
            state,
        )
        draft = V3WriterOutput.model_validate(state.draft)
        text = " ".join(
            sentence.text
            for block in draft.blocks
            for sentence in block.content_sentences
        )
        blockers: list[str] = []
        for method in [
            MethodDossier.model_validate(item) for item in state.method_dossiers
        ]:
            reference = method.external_reference
            if reference is None or reference.status != "approved":
                blockers.append(f"{method.method_id}: referência ausente ou reprovada")
                continue
            if str(reference.url) not in text:
                blockers.append(
                    f"{method.method_id}: URL aprovada não foi incluída no artigo"
                )
        state.external_reference_report = {
            "status": "blocked" if blockers else "passed",
            "blockers": blockers,
            "reference_count": len(state.external_references),
        }
        return state

    async def finalizer(self, state: V3PipelineState) -> V3PipelineState:
        await self._stage(
            "finalizer", "Montando pacote candidato sem criar novos claims", state
        )
        draft = V3WriterOutput.model_validate(state.draft)
        self._validate_draft_evidence(draft, state)
        writer_run_id = self._agent_run_id("writer:article", 1)

        def persisted_sentence(sentence):
            return {
                "sentence_id": str(sentence.sentence_id),
                "text": sentence.text,
                "is_factual": sentence.is_factual,
                "question_ids": list(sentence.question_ids),
                "answer_status": sentence.answer_status,
                "evidence": [
                    {
                        "fact_id": str(evidence.claim_id),
                        "entailment_score": evidence.entailment_score,
                    }
                    for evidence in sentence.evidence
                ],
            }

        persistence_draft = {
            "title": draft.title,
            "blocks": [
                {
                    "block_id": str(block.block_id),
                    "type": block.type,
                    "position": block.position,
                    "sentences": [
                        persisted_sentence(sentence)
                        for sentence in block.content_sentences
                    ],
                    "structured_payload": {
                        "table_headers": [
                            persisted_sentence(sentence)
                            for sentence in block.table_headers
                        ],
                        "table_rows": [
                            [persisted_sentence(cell) for cell in row.cells]
                            for row in block.table_rows
                        ],
                        "callout_kind": block.callout_kind,
                        "callout_title": (
                            persisted_sentence(block.callout_title)
                            if block.callout_title is not None
                            else None
                        ),
                    },
                }
                for block in draft.blocks
            ],
        }
        version = await self.versions.persist_draft(
            self.project,
            self.pipeline_run,
            persistence_draft,
            writer_run_id,
        )

        sources = await self._source_report()
        source_number = {
            str(item.get("url")): index
            for index, item in enumerate(sources, start=1)
            if item.get("url")
        }
        claim_rows = list(
            (
                await self.db.execute(
                    select(V3KnowledgeClaimRecord, V3SourceDocumentRecord)
                    .join(
                        V3SourceDocumentRecord,
                        V3SourceDocumentRecord.id
                        == V3KnowledgeClaimRecord.source_document_id,
                    )
                    .where(
                        V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run.id,
                        V3KnowledgeClaimRecord.approved.is_(True),
                    )
                )
            ).all()
        )
        claim_to_source: dict[uuid.UUID, int] = {}
        for claim_row, source_row in claim_rows:
            claim_id = claim_row.fact_id or claim_row.id
            index = source_number.get(source_row.canonical_url)
            if index is not None:
                claim_to_source[claim_id] = index

        def rendered_sentence(sentence, *, html_mode: bool) -> str:
            references = sorted(
                {
                    claim_to_source[item.claim_id]
                    for item in sentence.evidence
                    if item.claim_id in claim_to_source
                }
            )
            if html_mode:
                body = _inline_html(sentence.text)
                if references:
                    body += "".join(
                        f'<sup class="editorial-citation"><a href="#source-{item}">[{item}]</a></sup>'
                        for item in references
                    )
                return body
            suffix = "".join(f"[{item}]" for item in references)
            return sentence.text + (f" {suffix}" if suffix else "")

        markdown_parts: list[str] = []
        html_parts: list[str] = []
        for block in draft.blocks:
            md_texts = [
                rendered_sentence(item, html_mode=False)
                for item in block.content_sentences
            ]
            html_texts = [
                rendered_sentence(item, html_mode=True)
                for item in block.content_sentences
            ]
            md_combined = " ".join(md_texts)
            html_combined = " ".join(html_texts)
            if block.type in {"h1", "h2", "h3"}:
                level = int(block.type[1])
                markdown_parts.append(f"{'#' * level} {md_combined}")
                html_parts.append(f"<h{level}>{html_combined}</h{level}>")
            elif block.type == "list":
                markdown_parts.append("\n".join(f"- {item}" for item in md_texts))
                html_parts.append(
                    "<ul>"
                    + "".join(f"<li>{item}</li>" for item in html_texts)
                    + "</ul>"
                )
            elif block.type == "table":
                if block.table_headers:
                    md_header = [
                        rendered_sentence(item, html_mode=False)
                        for item in block.table_headers
                    ]
                    md_rows = [
                        [rendered_sentence(cell, html_mode=False) for cell in row.cells]
                        for row in block.table_rows
                    ]
                    html_header = [
                        rendered_sentence(item, html_mode=True)
                        for item in block.table_headers
                    ]
                    html_rows = [
                        [rendered_sentence(cell, html_mode=True) for cell in row.cells]
                        for row in block.table_rows
                    ]
                    markdown_parts.append(
                        "\n".join(
                            [
                                "| " + " | ".join(md_header) + " |",
                                "| " + " | ".join(["---"] * len(md_header)) + " |",
                                *["| " + " | ".join(row) + " |" for row in md_rows],
                            ]
                        )
                    )
                    html_parts.append(
                        '<table class="editorial-table"><thead><tr>'
                        + "".join(f"<th>{cell}</th>" for cell in html_header)
                        + "</tr></thead><tbody>"
                        + "".join(
                            "<tr>"
                            + "".join(f"<td>{cell}</td>" for cell in row)
                            + "</tr>"
                            for row in html_rows
                        )
                        + "</tbody></table>"
                    )
                else:
                    # Compatibility path for V3.5 checkpoints whose table rows
                    # were encoded as pipe-delimited sentences.
                    parsed_rows = [
                        [cell.strip() for cell in item.split("|") if cell.strip()]
                        for item in md_texts
                    ]
                    column_count = max((len(row) for row in parsed_rows), default=0)
                    if column_count >= 2:
                        rows = [
                            row + [""] * (column_count - len(row))
                            for row in parsed_rows
                        ]
                        header, body_rows = rows[0], rows[1:]
                        markdown_parts.append(
                            "\n".join(
                                [
                                    "| " + " | ".join(header) + " |",
                                    "| " + " | ".join(["---"] * column_count) + " |",
                                    *[
                                        "| " + " | ".join(row) + " |"
                                        for row in body_rows
                                    ],
                                ]
                            )
                        )
                        html_rows = [
                            [cell.strip() for cell in item.split("|") if cell.strip()]
                            for item in html_texts
                        ]
                        html_rows = [
                            row + [""] * (column_count - len(row)) for row in html_rows
                        ]
                        html_header, html_body = html_rows[0], html_rows[1:]
                        html_parts.append(
                            '<table class="editorial-table"><thead><tr>'
                            + "".join(f"<th>{cell}</th>" for cell in html_header)
                            + "</tr></thead><tbody>"
                            + "".join(
                                "<tr>"
                                + "".join(f"<td>{cell}</td>" for cell in row)
                                + "</tr>"
                                for row in html_body
                            )
                            + "</tbody></table>"
                        )
                    else:
                        markdown_parts.append(md_combined)
                        html_parts.append(
                            '<table class="editorial-table"><tbody><tr><td>'
                            + html_combined
                            + "</td></tr></tbody></table>"
                        )
            elif block.type == "callout":
                kind = block.callout_kind or "note"
                label_md = (
                    rendered_sentence(block.callout_title, html_mode=False)
                    if block.callout_title is not None
                    else ""
                )
                label_html = (
                    rendered_sentence(block.callout_title, html_mode=True)
                    if block.callout_title is not None
                    else ""
                )
                body_md_texts = [
                    rendered_sentence(item, html_mode=False) for item in block.sentences
                ]
                body_html_texts = [
                    rendered_sentence(item, html_mode=True) for item in block.sentences
                ]
                quote_lines = [f"> {item}" for item in body_md_texts]
                markdown_parts.append(
                    (f"> **{label_md}**\n" if label_md else "") + "\n".join(quote_lines)
                )
                title_html = (
                    f'<strong class="editorial-callout-title">{label_html}</strong>'
                    if label_html
                    else ""
                )
                html_combined = " ".join(body_html_texts)
                html_parts.append(
                    f'<aside class="editorial-callout editorial-callout-{kind}">'
                    f"{title_html}<p>{html_combined}</p></aside>"
                )
            else:
                markdown_parts.append(md_combined)
                html_parts.append(f"<p>{html_combined}</p>")

        if sources:
            markdown_parts.append(
                "## Fontes\n\n"
                + "\n".join(
                    f"{index}. [{item.get('title') or item.get('url')}]({item.get('url')})"
                    for index, item in enumerate(sources, start=1)
                )
            )
            html_parts.append(
                '<section class="editorial-sources"><h2>Fontes</h2><ol>'
                + "".join(
                    f'<li id="source-{index}"><a href="{html.escape(str(item.get("url") or ""), quote=True)}" '
                    f'rel="noopener noreferrer">{html.escape(str(item.get("title") or item.get("url") or "Fonte"))}</a></li>'
                    for index, item in enumerate(sources, start=1)
                )
                + "</ol></section>"
            )

        markdown = "\n\n".join(markdown_parts)
        html_output = "\n".join(html_parts)
        seo_metadata = {
            "title": self._truncate_at_word(draft.title, 60),
            "meta_description": self._meta_description(draft),
            "slug": stable_slug(draft.title, separator="-", limit=120),
            "language": self.project.language,
            "pipeline_contract_version": "editorial-v3.8",
            "editorial_architecture": ContentKnowledgeContract.model_validate(
                state.contract
            ).content_type.value,
            "human_review_required": True,
        }
        source_report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_contract_version": "editorial-v3.8",
            "sources": sources,
            "source_document_count": len(sources),
            "distinct_source_count": len(
                {
                    urlsplit(item["url"]).netloc.casefold()
                    for item in sources
                    if item.get("url")
                }
            ),
            "approved_claim_count": len(state.knowledge_claims),
            "brief_compliance": state.brief_compliance_report
            or state.writer_diagnostics
            or {},
            "editorial_intelligence": (
                self.intelligence.summary(
                    ContentIntelligenceState.model_validate(state.intelligence_state)
                )
                if state.intelligence_state
                else {}
            ),
            "external_references": state.external_references,
            "traceability": [
                {
                    "block_id": str(block.block_id),
                    "section_id": block.section_id,
                    "method_id": block.method_id,
                    "sentences": [
                        {
                            "sentence_id": str(sentence.sentence_id),
                            "text": sentence.text,
                            "question_ids": list(sentence.question_ids),
                            "answer_status": sentence.answer_status,
                            "fact_ids": [
                                str(item.claim_id) for item in sentence.evidence
                            ],
                            "source_numbers": sorted(
                                {
                                    claim_to_source[item.claim_id]
                                    for item in sentence.evidence
                                    if item.claim_id in claim_to_source
                                }
                            ),
                        }
                        for sentence in block.content_sentences
                    ],
                }
                for block in draft.blocks
            ],
        }
        article = await self.db.scalar(
            select(Article).where(Article.project_id == self.project.id)
        )
        if article is None:
            raise V3PipelineBlocked("Article persistence failed", "V3_ARTICLE_MISSING")

        # Persist only an immutable candidate before the quality gate. Public/final
        # fields are promoted atomically only after the deterministic rubric passes.
        if not article.final_markdown:
            article.status = "quality_review"
        article.active_pipeline_run_id = self.pipeline_run.id
        version.editorial_status = "quality_review"
        version.status = "quality_review"
        version.final_markdown = None
        version.final_html = None
        version.seo_metadata = {}
        version.source_report = {}
        version.content_checksum = article_version_checksum(version)
        state.article_version_id = version.id
        state.final_package = {
            "markdown": markdown,
            "html": html_output,
            "seo_metadata": seo_metadata,
            "source_report": source_report,
        }
        return state

    async def quality_gate(self, state: V3PipelineState) -> V3PipelineState:
        contract = ContentKnowledgeContract.model_validate(state.contract)
        await self._stage(
            "quality_gate",
            "Aplicando a rubrica editorial da arquitetura contratada",
            state,
        )
        documents = [
            StructuredSourceDocument.model_validate(item)
            for item in state.source_documents
        ]
        accepted = [
            item
            for item in documents
            if item.assessment.usage_policy
            in {
                SourceUsagePolicy.authoritative_evidence,
                SourceUsagePolicy.corroborating_evidence,
            }
        ]
        accepted_hosts = {
            urlsplit(str(item.canonical_url)).netloc.casefold() for item in accepted
        }
        independent_hosts = {
            urlsplit(str(item.canonical_url)).netloc.casefold()
            for item in accepted
            if item.assessment.counts_toward_independent_source_diversity
        }
        target_range = list(
            (state.writer_diagnostics or {}).get("target_word_range") or []
        )
        minimum_word_count = (
            int(target_range[0])
            if len(target_range) == 2
            else int(self._flag("v3_min_word_count"))
        )
        maximum_word_count = (
            int(target_range[1])
            if len(target_range) == 2
            else int(self._flag("v3_max_word_count"))
        )
        common = {
            "contract": contract,
            "sections": [
                SectionDossier.model_validate(item) for item in state.section_dossiers
            ],
            "draft": V3WriterOutput.model_validate(state.draft),
            "development": V3DevelopmentReview.model_validate(state.development_review),
            "fact_check": V3FactCheckReview.model_validate(state.fact_check),
            "language": V3LanguageReview.model_validate(state.language_review),
            "accepted_source_count": len(accepted_hosts),
            "independent_source_count": len(independent_hosts),
            "minimum_word_count": minimum_word_count,
            "maximum_word_count": maximum_word_count,
        }
        if contract.content_type == EditorialContentTypeV3.procedural_decision_guide:
            if not state.decision_matrix:
                raise V3PipelineBlocked(
                    "Procedural quality requires a decision matrix",
                    "V3_QUALITY_MATRIX_MISSING",
                )
            evaluation = self.quality.evaluate(
                **common,
                methods=[
                    MethodDossier.model_validate(item) for item in state.method_dossiers
                ],
                matrix=DecisionMatrix.model_validate(state.decision_matrix),
                minimum_steps_per_method=int(self._flag("v3_min_steps_per_method")),
            ).model_copy(update={"architecture_type": contract.content_type.value})
        else:
            evaluation = self.universal_quality.evaluate(
                **common,
                diagnostics=state.writer_diagnostics,
            )
        article = await self.db.scalar(
            select(Article).where(Article.project_id == self.project.id)
        )
        version = (
            await self.db.get(ArticleVersion, state.article_version_id)
            if state.article_version_id is not None
            else None
        )
        if article is None or version is None:
            raise V3PipelineBlocked(
                "Quality gate cannot resolve the current article version",
                "V3_QUALITY_ARTICLE_MISSING",
            )

        package_data = dict(state.final_package or {})
        candidate_markdown = str(package_data.get("markdown") or "")
        if not candidate_markdown:
            raise V3PipelineBlocked(
                "Quality gate received no final candidate markdown",
                "V3_FINAL_PACKAGE_INCOMPLETE",
            )
        brief = generation_brief(self.project, self.execution_manifest, contract)
        similarity_report = await self.content_similarity.evaluate(
            project=self.project,
            article=article,
            article_version_id=version.id,
            candidate_markdown=candidate_markdown,
            candidate_title=V3WriterOutput.model_validate(state.draft).title,
            primary_keyword=str(brief.get("primary_keyword") or self.project.topic),
            duplicate_block_threshold=float(settings.quality_max_duplicate_score),
            duplicate_warning_threshold=float(
                settings.content_similarity_warning_threshold
            ),
        )
        state.content_similarity_report = similarity_report
        intelligence_report = None
        intelligence_blockers: list[str] = []
        intelligence_warnings: list[str] = []
        if state.intelligence_state:
            intelligence_report = self.intelligence.validate_draft(
                ContentIntelligenceState.model_validate(state.intelligence_state),
                V3WriterOutput.model_validate(state.draft),
            )
            intelligence_blockers = [
                f"{item.code}: {item.message}" for item in intelligence_report.blockers
            ]
            intelligence_warnings = [
                f"{item.code}: {item.message}" for item in intelligence_report.warnings
            ]
            state.intelligence_validation = intelligence_report.model_dump(mode="json")
        combined_blockers = list(
            dict.fromkeys(
                [
                    *evaluation.critical_blockers,
                    *similarity_report.get("blockers", []),
                    *intelligence_blockers,
                ]
            )
        )
        combined_warnings = list(
            dict.fromkeys(
                [
                    *evaluation.warnings,
                    *similarity_report.get("warnings", []),
                    *intelligence_warnings,
                ]
            )
        )
        if combined_blockers or combined_warnings != evaluation.warnings:
            evaluation = evaluation.model_copy(
                update={
                    "status": "blocked" if combined_blockers else evaluation.status,
                    "overall_score": (
                        min(float(evaluation.overall_score), 0.59)
                        if combined_blockers
                        else evaluation.overall_score
                    ),
                    "critical_blockers": combined_blockers,
                    "warnings": combined_warnings,
                }
            )
        source_report = dict(package_data.get("source_report") or {})
        source_report["content_similarity"] = similarity_report
        if intelligence_report is not None:
            source_report["editorial_intelligence_validation"] = (
                intelligence_report.model_dump(mode="json")
            )
        package_data["source_report"] = source_report
        state.final_package = package_data
        if intelligence_report is not None and state.intelligence_state:
            quality_draft = V3WriterOutput.model_validate(state.draft)
            intelligence_state = self.intelligence.mark_draft_validated(
                ContentIntelligenceState.model_validate(state.intelligence_state),
                intelligence_report,
                draft=quality_draft,
                article_version_id=version.id,
            )
            state.intelligence_state = intelligence_state.model_dump(mode="json")
            state.intelligence_revision = intelligence_state.revision
            await self.intelligence_repository.save(
                intelligence_state,
                stage="quality_gate",
                status=intelligence_state.lifecycle.value,
                validation=state.intelligence_validation,
            )
            package_data = dict(state.final_package or {})
            bound_source_report = dict(package_data.get("source_report") or {})
            bound_source_report["editorial_intelligence"] = self.intelligence.summary(
                intelligence_state
            )
            bound_source_report["editorial_intelligence_binding"] = {
                "validated_artifact_hash": intelligence_state.validated_artifact_hash,
                "article_version_id": (
                    str(intelligence_state.article_version_id)
                    if intelligence_state.article_version_id
                    else None
                ),
                "draft_revision": intelligence_state.draft_revision,
            }
            package_data["source_report"] = bound_source_report
            state.final_package = package_data
        await self.artifacts.quality(
            evaluation,
            article_version_id=state.article_version_id,
        )
        state.quality_evaluation = evaluation.model_dump(mode="json")
        if evaluation.status == "passed":
            if state.intelligence_state:
                validated_intelligence = ContentIntelligenceState.model_validate(
                    state.intelligence_state
                )
                if (
                    validated_intelligence.lifecycle
                    != IntelligenceLifecycle.draft_validated
                    or not validated_intelligence.validated_artifact_hash
                    or validated_intelligence.article_version_id != version.id
                ):
                    raise V3PipelineBlocked(
                        "Quality gate cannot promote a candidate that is not bound to the "
                        "validated draft hash and article version.",
                        "V3_QUALITY_INTELLIGENCE_BINDING_INVALID",
                    )
            package_data = dict(state.final_package or {})
            required_package_keys = {
                "markdown",
                "html",
                "seo_metadata",
                "source_report",
            }
            if not required_package_keys.issubset(package_data):
                raise V3PipelineBlocked(
                    "Quality gate passed without a complete final candidate package",
                    "V3_FINAL_PACKAGE_INCOMPLETE",
                )
            article.final_markdown = str(package_data["markdown"])
            article.final_html = str(package_data["html"])
            article.seo_metadata = dict(package_data["seo_metadata"])
            article.source_report = dict(package_data["source_report"])
            article.content_fingerprint = (
                str(
                    (state.content_similarity_report or {}).get("candidate_fingerprint")
                    or ""
                )
                or None
            )
            version.final_markdown = article.final_markdown
            version.final_html = article.final_html
            version.seo_metadata = article.seo_metadata
            version.source_report = article.source_report
            version.editorial_status = "needs_human_approval"
            version.content_checksum = article_version_checksum(version)
            article.status = "needs_human_approval"
            version.status = "needs_human_approval"
            self.project.status = ProjectStatus.needs_human_approval
            self.pipeline_run.status = PipelineRunStatus.needs_human_approval
            self.pipeline_run.current_stage = "needs_human_approval"
            self.project.current_stage = "needs_human_approval"
            if not version.content_checksum:
                version.content_checksum = article_version_checksum(version)
            package = await HumanEditorialReviewService(self.db).create_package(
                project_id=self.project.id,
                pipeline_run_id=self.pipeline_run.id,
                article_version_id=version.id,
            )
            state.human_review_package_id = package.id
            state.stage = V3Stage.completed
        else:
            # A rejected candidate must never leak into the candidate version. Keep any
            # previously approved public article intact so a failed regeneration cannot
            # erase content that is already under human review or published.
            version.final_markdown = None
            version.final_html = None
            version.seo_metadata = {}
            version.source_report = {}
            version.editorial_status = "blocked"
            version.content_checksum = article_version_checksum(version)
            if not article.final_markdown:
                article.status = "blocked"
            version.status = "blocked"
            self.project.status = ProjectStatus.blocked
            self.pipeline_run.status = PipelineRunStatus.blocked
            state.stage = V3Stage.blocked
            state.blocking_code = "V3_QUALITY_BLOCKED"
            state.blocking_reason = "; ".join(evaluation.critical_blockers[:5])
        return state

    async def _extract_claims_for_tasks(
        self,
        *,
        state: V3PipelineState,
        contract: ContentKnowledgeContract,
        tasks: list[ResearchTask],
        documents: list[StructuredSourceDocument],
        persisted_plan,
        only_document_ids: set[uuid.UUID] | None = None,
    ) -> int:
        """Extract verifiable claims without letting one malformed batch abort the run.

        Production showed a bare ``TypeError`` in the synthesizer after sources
        had already passed the coverage gate. The previous implementation put
        every document for a task into one call and allowed any Python type
        error in that batch or one candidate to roll back the whole stage. The
        guarded path below retries a failed batch per document, records a safe
        diagnostic and preserves claims from the remaining tasks.
        """

        existing_groups = list(
            await self.db.scalars(
                select(V3KnowledgeClaimRecord.support_group).where(
                    V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run.id
                )
            )
        )
        nodes = {node.node_id: node for node in contract.nodes}
        persisted_count = 0
        failures = list(state.research_metrics.get("claim_extraction_failures") or [])
        failed_task_ids: set[str] = set(
            state.research_metrics.get("claim_extraction_failed_task_ids") or []
        )
        source_rows: dict[uuid.UUID, V3SourceDocumentRecord | None] = {}

        def record_failure(task: ResearchTask, phase: str, error: Exception) -> None:
            failed_task_ids.add(task.task_id)
            failures.append(
                {
                    "task_id": task.task_id,
                    "knowledge_node_id": task.knowledge_node_id,
                    "phase": phase,
                    "error_type": type(error).__name__,
                }
            )

        async def extract_batch(
            task: ResearchTask,
            node,
            batch: list[StructuredSourceDocument],
            *,
            key_suffix: str,
            attempt: int,
        ) -> KnowledgeClaimExtractionOutput:
            output = await self._agent_call(
                role="researcher",
                key=f"claim_extraction:{task.task_id}:{key_suffix}",
                attempt=attempt,
                input_json={
                    "task": task.model_dump(mode="json"),
                    "required_evidence_roles": [
                        role.value for role in node.required_evidence_roles
                    ],
                    "known_support_groups": existing_groups[-150:],
                    "documents": [
                        self._document_for_agent(item, task.research_goal)
                        for item in batch
                    ],
                },
                prompt=(
                    "Você é o pesquisador factual da Editorial Intelligence V3. Extraia somente afirmações "
                    "literalmente sustentadas pelos documentos. exact_quote deve ser um trecho exato. Reúna "
                    "afirmações semanticamente equivalentes de fontes independentes no mesmo support_group. "
                    "Não transforme blog de e-commerce em verdade: conteúdo comparison_only só pode registrar "
                    "comparação, limitação ou erro relatado e nunca sustenta conclusão absoluta. Não invente "
                    "abordagem, parâmetro, autoria, URL ou causalidade. Use conditional quando a resposta depender "
                    f"de contexto. Tarefa: {task.research_goal}"
                ),
                output_schema=KnowledgeClaimExtractionOutput,
            )
            return KnowledgeClaimExtractionOutput.model_validate(output)

        async def persist_extraction(
            task: ResearchTask,
            node,
            batch: list[StructuredSourceDocument],
            extraction: KnowledgeClaimExtractionOutput,
        ) -> int:
            task_question = persisted_plan.questions_by_task_id.get(task.task_id)
            if task_question is None:
                record_failure(
                    task, "research_question_missing", KeyError(task.task_id)
                )
                return 0
            allowed_roles = set(node.required_evidence_roles)
            count = 0
            for candidate in extraction.claims:
                try:
                    if allowed_roles and candidate.evidence_role not in allowed_roles:
                        continue
                    candidate_url = canonicalize_url(str(candidate.source_url))
                    document = next(
                        (
                            item
                            for item in batch
                            if candidate_url
                            in {
                                canonicalize_url(str(item.url)),
                                canonicalize_url(str(item.canonical_url)),
                            }
                        ),
                        None,
                    )
                    if document is None:
                        continue
                    if document.document_id not in source_rows:
                        source_rows[document.document_id] = await self.db.scalar(
                            select(V3SourceDocumentRecord).where(
                                V3SourceDocumentRecord.pipeline_run_id
                                == self.pipeline_run.id,
                                V3SourceDocumentRecord.id == document.document_id,
                            )
                        )
                    source_row = source_rows[document.document_id]
                    if source_row is None:
                        continue
                    source = SearchDocument(
                        url=canonicalize_url(str(document.canonical_url)),
                        title=str(document.title),
                        content=str(document.plain_text),
                        publisher=(
                            str(document.publisher) if document.publisher else None
                        ),
                        source_type=str(document.assessment.source_role.value),
                        reliability_score=float(document.assessment.priority_score),
                        accessed_at=document.accessed_at,
                        author=str(document.author) if document.author else None,
                        published_at=document.published_at,
                        extraction_method="v3_structured_reader",
                    )
                    suffix = uuid.uuid5(uuid.NAMESPACE_URL, source.url).hex[:8]
                    normalized = candidate.model_copy(
                        update={
                            "claim_key": f"{_slug(str(candidate.claim_key), 105)}_{suffix}",
                            "support_group": _slug(str(candidate.support_group), 119),
                            "source_url": source.url,
                            "knowledge_node_id": task.knowledge_node_id,
                            "critical": bool(task.critical),
                        }
                    )
                    row = await self.artifacts.claim(
                        contract_id=state.contract_id,
                        candidate=normalized,
                        task_question=task_question,
                        source=source,
                        structured=document,
                        source_row=source_row,
                    )
                    if row is not None:
                        count += 1
                        existing_groups.append(normalized.support_group)
                except TypeError as exc:
                    # A malformed candidate must not erase valid claims already
                    # extracted for other documents or knowledge nodes.
                    record_failure(task, "candidate_persistence", exc)
                    continue
            return count

        for task in tasks:
            node = nodes.get(task.knowledge_node_id)
            if node is None:
                continue
            task_documents = [
                document
                for document in documents
                if (
                    only_document_ids is None
                    or document.document_id in only_document_ids
                )
                and task.task_id
                in state.source_task_map.get(
                    canonicalize_url(str(document.canonical_url)),
                    [],
                )
            ][: int(self._flag("v3_max_documents_per_research_task"))]
            if not task_documents:
                continue
            base_attempt = 1 if only_document_ids is None else 2
            try:
                extraction = await extract_batch(
                    task,
                    node,
                    task_documents,
                    key_suffix="batch",
                    attempt=base_attempt,
                )
            except TypeError as exc:
                record_failure(task, "batch", exc)
                # Isolate the source that triggers the type mismatch and retain
                # evidence from every other readable document.
                for index, document in enumerate(task_documents, start=1):
                    try:
                        extraction = await extract_batch(
                            task,
                            node,
                            [document],
                            key_suffix=f"document_{document.document_id.hex[:12]}",
                            attempt=base_attempt + index,
                        )
                    except TypeError as isolated_exc:
                        record_failure(task, "isolated_document", isolated_exc)
                        continue
                    persisted_count += await persist_extraction(
                        task, node, [document], extraction
                    )
            else:
                persisted_count += await persist_extraction(
                    task, node, task_documents, extraction
                )

        state.research_metrics = {
            **state.research_metrics,
            "claim_extraction_persisted_count": persisted_count,
            "claim_extraction_failures": failures[-100:],
            "claim_extraction_failed_task_ids": sorted(failed_task_ids),
        }
        return persisted_count

    async def _search_credentials(self) -> list[tuple[str, str]]:
        multiple = getattr(self.runtime, "search_credentials", None)
        if callable(multiple):
            return list(await multiple())
        return [await self.runtime.search_credential()]

    async def _supplement_research(
        self,
        *,
        state: V3PipelineState,
        contract: ContentKnowledgeContract,
        plan: V3ResearchPlan,
        persisted_plan,
        is_procedural: bool = False,
    ) -> None:
        """Execute the reserved query budget against under-covered nodes.

        Remaining planned queries are consumed first. If the plan has no unused
        query for a missing node, targeted gap queries are generated. Both paths
        use round-robin allocation so a single early node cannot monopolize the
        reserve.
        """

        coverage = await self.artifacts.approved_coverage_by_node()
        missing = [
            task
            for task in plan.tasks
            if int(
                coverage.get(task.knowledge_node_id, {}).get(
                    "independent_source_count", 0
                )
            )
            < task.minimum_independent_sources
        ]
        missing.sort(key=lambda item: (not item.critical, plan.tasks.index(item)))
        query_used = int(state.research_metrics.get("total_query_count", 0))
        remaining_queries = max(0, plan.maximum_search_queries - query_used)
        max_documents = int(self._flag("v3_max_source_documents"))
        remaining_documents = max(0, max_documents - len(state.source_documents))
        if not missing or remaining_queries == 0:
            return

        raw_executed = state.research_metrics.get("executed_queries_by_task", {})
        executed_queries_by_task = {
            str(task_id): [str(query) for query in queries]
            for task_id, queries in dict(raw_executed).items()
        }
        planned_schedule = schedule_research_queries(
            missing,
            limit=remaining_queries,
            executed_queries_by_task=executed_queries_by_task,
        )
        scheduled: list[tuple[ResearchTask, str, str]] = []
        task_by_id = {task.task_id: task for task in missing}
        for assignment in planned_schedule:
            scheduled.append(
                (task_by_id[assignment.task_id], assignment.query, "planned")
            )

        remaining_slots = remaining_queries - len(scheduled)
        if remaining_slots > 0:
            fallback_by_task: dict[str, list[str]] = {}
            for task in missing:
                candidates = build_targeted_gap_queries(contract, task, limit=3)
                already = set(executed_queries_by_task.get(task.task_id, []))
                fallback_by_task[task.task_id] = [
                    query[:600] for query in candidates if query[:600] not in already
                ]
            while remaining_slots > 0:
                progressed = False
                for task in missing:
                    queue = fallback_by_task[task.task_id]
                    if not queue:
                        continue
                    scheduled.append((task, queue.pop(0), "targeted"))
                    remaining_slots -= 1
                    progressed = True
                    if remaining_slots == 0:
                        break
                if not progressed:
                    break

        if not scheduled:
            return

        provider_credentials = await self._search_credentials()
        intent = CanonicalResearchIntent.from_contract(contract)
        coordinator, budget, circuits = self._search_runtime(state, plan)
        existing_urls = {
            canonicalize_url(SearchDocument.from_payload(item).url)
            for item in state.raw_source_documents
        }
        new_raw_by_url: dict[str, SearchDocument] = {}
        new_task_map: dict[str, set[str]] = {}
        newly_assigned_existing: set[tuple[str, str]] = set()
        failures: list[str] = []
        query_modes = {"planned": 0, "targeted": 0}
        supplemental_queries = 0
        supplemental_attempts: list[dict] = []
        supplemental_providers: set[str] = set()
        supplemental_markets: set[str] = set()
        for task, query, mode in scheduled:
            await self._cancellation_boundary()
            supplemental_queries += 1
            query_modes[mode] += 1
            executed_queries_by_task.setdefault(task.task_id, []).append(query)
            result = await coordinator.search(
                query=query,
                topic=str(getattr(self.project, "topic", "") or contract.topic),
                question=task.research_goal,
                search_subject=str(
                    contract.metadata.get("search_subject") or contract.topic
                ),
                provider_credentials=provider_credentials,
                max_results=int(self._flag("v3_search_results_per_query")),
                preferred_market_index=(
                    len(executed_queries_by_task.get(task.task_id, [])) - 1
                ),
                intent=intent,
                task=task,
            )
            failures.extend(f"{task.task_id}:{exc.category}" for exc in result.failures)
            for attempt in result.attempts:
                payload = attempt.as_payload()
                payload["task_id"] = task.task_id
                payload["mode"] = mode
                supplemental_attempts.append(payload)
                supplemental_providers.add(attempt.provider)
                if attempt.market:
                    supplemental_markets.add(attempt.market)
            for source in result.documents:
                key = canonicalize_url(source.url)
                if key in new_raw_by_url:
                    new_task_map.setdefault(key, set()).add(task.task_id)
                    continue
                if key in existing_urls:
                    previous = set(state.source_task_map.get(key, []))
                    if task.task_id not in previous:
                        newly_assigned_existing.add((key, task.task_id))
                    state.source_task_map[key] = sorted(previous | {task.task_id})
                    continue
                if remaining_documents <= len(new_raw_by_url):
                    continue
                existing_urls.add(key)
                new_raw_by_url[key] = source
                new_task_map.setdefault(key, set()).add(task.task_id)

        new_raw = list(new_raw_by_url.values())
        new_documents: list[StructuredSourceDocument] = []
        maximum_fetches = int(self._flag("v3_max_source_fetches"))
        fetch_count = int(state.research_metrics.get("source_fetch_count", 0))
        read_attempts = {
            str(key): int(value)
            for key, value in dict(
                state.research_metrics.get("source_read_attempts_by_url") or {}
            ).items()
        }
        processed = set(state.research_metrics.get("processed_raw_source_urls") or [])
        for raw in new_raw[:remaining_documents]:
            raw_key = canonicalize_url(raw.url)
            if fetch_count >= maximum_fetches:
                failures.append("source_fetch_budget_exhausted")
                break
            if raw_key in processed or read_attempts.get(raw_key, 0) >= 2:
                continue
            read_attempts[raw_key] = read_attempts.get(raw_key, 0) + 1
            fetch_count += 1
            try:
                structured = await self.reader.read(raw)
                structured = self._apply_brief_source_policy(structured, state)
            except Exception as exc:
                failures.append(f"{raw_key}:{type(exc).__name__}")
                if read_attempts[raw_key] >= 2:
                    processed.add(raw_key)
                continue
            processed.add(raw_key)
            source_row = await self.artifacts.source_document(
                state.contract_id, structured
            )
            structured = structured.model_copy(
                update={"document_id": source_row.id}
            )
            tasks = sorted(new_task_map.get(raw_key, set()))
            for key in {
                raw_key,
                canonicalize_url(str(structured.url)),
                canonicalize_url(str(structured.canonical_url)),
            }:
                state.source_task_map[key] = sorted(
                    set(state.source_task_map.get(key, [])) | set(tasks)
                )
            if structured.assessment.usage_policy != SourceUsagePolicy.rejected:
                new_documents.append(structured)

        state.raw_source_documents.extend(item.as_payload() for item in new_raw)
        state.source_documents.extend(
            item.model_dump(mode="json") for item in new_documents
        )
        global_markets_by_task = {
            str(task_id): set(str(item) for item in values)
            for task_id, values in dict(
                state.research_metrics.get("markets_by_task") or {}
            ).items()
        }
        global_languages_by_task = {
            str(task_id): set(str(item) for item in values)
            for task_id, values in dict(
                state.research_metrics.get("languages_by_task") or {}
            ).items()
        }
        global_providers = set(state.research_metrics.get("providers_used") or [])
        global_diagnostics = {
            str(key): value
            for key, value in dict(
                state.research_metrics.get("search_diagnostic_totals") or {}
            ).items()
            if isinstance(value, (int, float))
        }
        for attempt in supplemental_attempts:
            task_id = str(attempt.get("task_id") or "")
            if attempt.get("status") != "skipped" and attempt.get("provider"):
                global_providers.add(str(attempt["provider"]))
            if task_id and attempt.get("market"):
                global_markets_by_task.setdefault(task_id, set()).add(
                    str(attempt["market"])
                )
            if task_id and attempt.get("search_language"):
                global_languages_by_task.setdefault(task_id, set()).add(
                    str(attempt["search_language"])
                )
            for key, value in dict(attempt.get("diagnostics") or {}).items():
                if isinstance(value, (int, float)):
                    global_diagnostics[str(key)] = (
                        global_diagnostics.get(str(key), 0) + value
                    )
        state.research_metrics = {
            **state.research_metrics,
            "supplemental_query_count": supplemental_queries,
            "total_query_count": budget.logical_queries,
            "supplemental_source_count": len(new_documents),
            "supplemental_failures": failures[:30],
            "supplemental_query_modes": query_modes,
            "supplemental_provider_attempt_count": len(supplemental_attempts),
            "supplemental_search_attempts": supplemental_attempts[:120],
            "supplemental_providers_used": sorted(supplemental_providers),
            "supplemental_markets_used": sorted(supplemental_markets),
            "supplemental_targeted_nodes": [task.knowledge_node_id for task in missing],
            "source_fetch_count": fetch_count,
            "source_read_attempts_by_url": read_attempts,
            "processed_raw_source_urls": sorted(processed),
            "markets_by_task": {
                key: sorted(value) for key, value in global_markets_by_task.items()
            },
            "languages_by_task": {
                key: sorted(value) for key, value in global_languages_by_task.items()
            },
            "providers_used": sorted(global_providers),
            "search_diagnostic_totals": global_diagnostics,
            "executed_queries_by_task": executed_queries_by_task,
        }

        existing_document_by_url: dict[str, StructuredSourceDocument] = {}
        for payload in state.source_documents:
            document = StructuredSourceDocument.model_validate(payload)
            existing_document_by_url[canonicalize_url(str(document.url))] = document
            existing_document_by_url[canonicalize_url(str(document.canonical_url))] = (
                document
            )
        reassigned_documents = {
            existing_document_by_url[url].document_id: existing_document_by_url[url]
            for url, _task_id in newly_assigned_existing
            if url in existing_document_by_url
        }
        extraction_documents = {
            item.document_id: item
            for item in [*new_documents, *reassigned_documents.values()]
        }
        if extraction_documents:
            await self._extract_claims_for_tasks(
                state=state,
                contract=contract,
                tasks=missing,
                documents=list(extraction_documents.values()),
                persisted_plan=persisted_plan,
                only_document_ids=set(extraction_documents),
            )
            await self.artifacts.approve_claim_bundles(procedural_context=is_procedural)

        after = await self.artifacts.approved_coverage_by_node()
        unresolved = [
            task.knowledge_node_id
            for task in missing
            if int(
                after.get(task.knowledge_node_id, {}).get("independent_source_count", 0)
            )
            < task.minimum_independent_sources
        ]
        state.research_metrics = {
            **state.research_metrics,
            "supplemental_unresolved_node_ids": unresolved,
        }
        self._persist_search_runtime(state, budget=budget, circuits=circuits)
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "v3.research.supplemented",
            "knowledge_synthesizer",
            {
                "targeted_nodes": [item.knowledge_node_id for item in missing],
                "query_count": supplemental_queries,
                "query_modes": query_modes,
                "provider_attempt_count": len(supplemental_attempts),
                "providers_used": sorted(supplemental_providers),
                "markets_used": sorted(supplemental_markets),
                "accepted_source_count": len(new_documents),
                "coverage_before": coverage,
                "coverage_after": after,
                "unresolved_node_ids": unresolved,
                "failures": failures[:30],
            },
            idempotency_key="v3.research.supplemented",
            context=self._stage_context,
        )

    async def _review_and_revise(self, state, *, stage, review_schema, prompt):
        draft = V3WriterOutput.model_validate(state.draft)
        review = review_schema.model_validate(
            await self._agent_call(
                role=stage,
                key=f"{stage}:review",
                attempt=1,
                input_json={
                    "draft": draft.model_dump(mode="json"),
                    "contract": state.contract,
                    "section_dossiers": state.section_dossiers,
                    "method_dossiers": state.method_dossiers,
                    "approved_claims": state.knowledge_claims,
                    "writer_diagnostics": state.writer_diagnostics,
                    "generation_brief": generation_brief(
                        self.project,
                        self.execution_manifest,
                        ContentKnowledgeContract.model_validate(state.contract),
                    ),
                    "editorial_intelligence": (
                        self.intelligence.writer_payload(
                            ContentIntelligenceState.model_validate(
                                state.intelligence_state
                            )
                        )
                        if state.intelligence_state
                        else {}
                    ),
                },
                prompt=prompt,
                output_schema=review_schema,
            )
        )
        if review.status != "rewrite" or not review.rewrite_block_ids:
            return draft, review
        requested = {str(item) for item in review.rewrite_block_ids}
        blocks = [
            item.model_dump(mode="json")
            for item in draft.blocks
            if str(item.block_id) in requested
        ]
        requested_indexes = {
            index
            for index, item in enumerate(draft.blocks)
            if str(item.block_id) in requested
        }
        context_indexes = {
            nearby
            for index in requested_indexes
            for nearby in range(max(0, index - 1), min(len(draft.blocks), index + 2))
        }
        neighbor_context = [
            {
                **draft.blocks[index].model_dump(mode="json"),
                "editable": index in requested_indexes,
            }
            for index in sorted(context_indexes)
        ]
        revisions = V3BlockRevisionOutput.model_validate(
            await self._agent_call(
                role=stage,
                key=f"{stage}:revision",
                attempt=1,
                input_json={
                    "blocks": blocks,
                    "neighbor_context": neighbor_context,
                    "article_outline": [
                        {
                            "block_id": str(item.block_id),
                            "type": item.type,
                            "position": item.position,
                            "section_id": item.section_id,
                            "method_id": item.method_id,
                            "preview": " ".join(
                                sentence.text for sentence in item.content_sentences
                            )[:240],
                        }
                        for item in draft.blocks
                    ],
                    "findings": [
                        item.model_dump(mode="json") for item in review.findings
                    ],
                    "allowed_claims": state.knowledge_claims,
                    "section_dossiers": state.section_dossiers,
                    "writer_diagnostics": state.writer_diagnostics,
                    "generation_brief": generation_brief(
                        self.project,
                        self.execution_manifest,
                        ContentKnowledgeContract.model_validate(state.contract),
                    ),
                    "editorial_intelligence": (
                        self.intelligence.writer_payload(
                            ContentIntelligenceState.model_validate(
                                state.intelligence_state
                            )
                        )
                        if state.intelligence_state
                        else {}
                    ),
                },
                prompt=(
                    "Reescreva somente os blocos solicitados, mas use os blocos vizinhos e o outline para criar "
                    "continuidade real. Preserve block_id, type, position, section_id e method_id. Preserve também "
                    "cada sentence_id, question_ids, answer_status, factualidade e evidence claim_ids da unidade "
                    "lógica correspondente; não divida, funda, remova ou acrescente sentenças neste ciclo localizado. "
                    "Não altere claims, condições ou evidências; meaning_changed deve ser false. Não devolva blocos que não foram "
                    "solicitados. Evite apenas trocar sinônimos: corrija a progressão, a abertura, o ritmo e a ligação "
                    "com o parágrafo anterior sem introduzir fatos novos. Preserve também a estrutura de "
                    "tabelas (quantidade de cabeçalhos, linhas e células) e o tipo/título de callouts."
                ),
                output_schema=V3BlockRevisionOutput,
            )
        )
        returned = {str(item.block_id) for item in revisions.revisions}
        if returned != requested:
            missing = sorted(requested - returned)
            extra = sorted(returned - requested)
            raise V3PipelineBlocked(
                f"{stage} revision response did not match the requested block set "
                f"(missing={missing}, extra={extra})",
                "V3_EDITOR_REVISION_SET_INVALID",
            )
        if len(blocks) != len(requested):
            raise V3PipelineBlocked(
                f"{stage} requested rewrite IDs that are not present in the current draft",
                "V3_EDITOR_REWRITE_TARGET_MISSING",
            )

        replacement = {}
        original_blocks = {block.block_id: block for block in draft.blocks}
        for item in revisions.revisions:
            if str(item.block_id) not in requested or item.meaning_changed:
                raise V3PipelineBlocked(
                    f"{stage} revision changed meaning or an unrequested block",
                    "V3_EDITOR_REVISION_INVALID",
                )
            original = original_blocks.get(item.block_id)
            revised = item.revised_block
            if original is None or any(
                (
                    revised.block_id != original.block_id,
                    revised.position != original.position,
                    revised.section_id != original.section_id,
                    revised.method_id != original.method_id,
                    revised.type != original.type,
                )
            ):
                raise V3PipelineBlocked(
                    f"{stage} revision changed protected block identity fields",
                    "V3_EDITOR_REVISION_IDENTITY_INVALID",
                )
            if original.type == "table":
                original_structured = bool(original.table_headers)
                revised_structured = bool(revised.table_headers)
                original_shape = (
                    len(original.table_headers),
                    len(original.table_rows),
                    tuple(len(row.cells) for row in original.table_rows),
                )
                revised_shape = (
                    len(revised.table_headers),
                    len(revised.table_rows),
                    tuple(len(row.cells) for row in revised.table_rows),
                )
                if (
                    original_structured != revised_structured
                    or original_shape != revised_shape
                ):
                    raise V3PipelineBlocked(
                        f"{stage} revision changed protected table structure",
                        "V3_EDITOR_REVISION_TABLE_SHAPE_CHANGED",
                    )
            if original.type == "callout" and (
                revised.callout_kind != original.callout_kind
                or (revised.callout_title is None) != (original.callout_title is None)
            ):
                raise V3PipelineBlocked(
                    f"{stage} revision changed protected callout structure",
                    "V3_EDITOR_REVISION_CALLOUT_SHAPE_CHANGED",
                )
            original_sentences = {
                sentence.sentence_id: sentence
                for sentence in original.content_sentences
            }
            revised_sentences = {
                sentence.sentence_id: sentence for sentence in revised.content_sentences
            }
            if set(original_sentences) != set(revised_sentences):
                raise V3PipelineBlocked(
                    f"{stage} revision added, removed or replaced logical sentence identities",
                    "V3_EDITOR_REVISION_SENTENCE_SET_CHANGED",
                )
            for sentence_id, original_sentence in original_sentences.items():
                revised_sentence = revised_sentences[sentence_id]
                preserved, reason = revision_preserves_meaning(
                    original_sentence.text,
                    revised_sentence.text,
                )
                if not preserved:
                    raise V3PipelineBlocked(
                        f"{stage} revision changed sentence {sentence_id}: {reason}",
                        "V3_EDITOR_REVISION_MEANING_CHANGED",
                    )
                original_claim_ids = [
                    evidence.claim_id for evidence in original_sentence.evidence
                ]
                revised_claim_ids = [
                    evidence.claim_id for evidence in revised_sentence.evidence
                ]
                if original_claim_ids != revised_claim_ids:
                    raise V3PipelineBlocked(
                        f"{stage} revision changed evidence for sentence {sentence_id}",
                        "V3_EDITOR_REVISION_EVIDENCE_CHANGED",
                    )
                if original_sentence.is_factual != revised_sentence.is_factual:
                    raise V3PipelineBlocked(
                        f"{stage} revision changed factuality for sentence {sentence_id}",
                        "V3_EDITOR_REVISION_FACTUALITY_CHANGED",
                    )
                if original_sentence.question_ids != revised_sentence.question_ids:
                    raise V3PipelineBlocked(
                        f"{stage} revision changed question bindings for sentence {sentence_id}",
                        "V3_EDITOR_REVISION_QUESTION_BINDING_CHANGED",
                    )
                if original_sentence.answer_status != revised_sentence.answer_status:
                    raise V3PipelineBlocked(
                        f"{stage} revision changed answer status for sentence {sentence_id}",
                        "V3_EDITOR_REVISION_ANSWER_STATUS_CHANGED",
                    )
            replacement[item.block_id] = item.revised_block
        updated = draft.model_copy(
            update={
                "blocks": [
                    replacement.get(block.block_id, block) for block in draft.blocks
                ]
            }
        )
        self._validate_draft_evidence(updated, state)
        second = review_schema.model_validate(
            await self._agent_call(
                role=stage,
                key=f"{stage}:review",
                attempt=2,
                input_json={
                    "draft": updated.model_dump(mode="json"),
                    "contract": state.contract,
                    "section_dossiers": state.section_dossiers,
                    "method_dossiers": state.method_dossiers,
                    "approved_claims": state.knowledge_claims,
                    "writer_diagnostics": state.writer_diagnostics,
                    "generation_brief": generation_brief(
                        self.project,
                        self.execution_manifest,
                        ContentKnowledgeContract.model_validate(state.contract),
                    ),
                    "editorial_intelligence": (
                        self.intelligence.writer_payload(
                            ContentIntelligenceState.model_validate(
                                state.intelligence_state
                            )
                        )
                        if state.intelligence_state
                        else {}
                    ),
                },
                prompt=prompt
                + " Esta é a verificação final após uma revisão localizada; não solicite outro ciclo.",
                output_schema=review_schema,
            )
        )
        return updated, second

    async def _agent_call(
        self, *, role, key, attempt, input_json, prompt, output_schema
    ):
        skill_roles: list[str]
        if key.startswith("claim_extraction"):
            skill_roles = ["researcher"]
        elif key == "emergent_questions":
            skill_roles = ["planner"]
        elif key == "method_inventory" or key == "knowledge_synthesis":
            skill_roles = ["knowledge_synthesizer", "research_gatekeeper"]
        elif key.startswith("article"):
            skill_roles = ["writer"]
        elif key.startswith("development_editor"):
            skill_roles = ["development_editor"]
        elif key.startswith("fact_checker"):
            skill_roles = ["fact_checker"]
        elif key.startswith("language_editor"):
            skill_roles = ["language_editor"]
        else:
            skill_roles = []
        fragments = []
        for skill_role in skill_roles:
            fragment = self.v3_skills.prompt_fragment(skill_role)
            if fragment:
                fragments.append(fragment)
        stage_prompt = prompt
        if fragments:
            stage_prompt = (
                "<editorial_v3_stage_skills>\n"
                + "\n\n".join(fragments)
                + "\n</editorial_v3_stage_skills>\n\n"
                + prompt
            )
        if role in {"development_editor", "fact_checker", "language_editor"}:
            try:
                input_json, _review_budget = self.context_budget.compact_review_input(
                    dict(input_json),
                    maximum_characters=max(
                        10_000,
                        int(settings.agent_task_data_max_characters * 0.90),
                    ),
                )
            except ContextBudgetExceeded as exc:
                raise V3PipelineBlocked(
                    str(exc),
                    "V3_REVIEW_CONTEXT_BUDGET_EXCEEDED",
                ) from exc
        return await self.runtime.call(
            self.project.id,
            role,
            self._agent_run_id(f"{role}:{key}", attempt),
            {
                "v3_stage_key": key,
                "task_data_keys": sorted(str(item) for item in input_json),
            },
            stage_prompt,
            output_schema,
            attempt=attempt,
            pipeline_run_id=self.pipeline_run.id,
            event_context=self._stage_context,
            task_data=input_json,
        )

    def _target_word_range(self, state: V3PipelineState) -> tuple[int, int]:
        configured_minimum = int(self._flag("v3_min_word_count"))
        configured_maximum = int(self._flag("v3_max_word_count"))
        brief = dict(self.project.briefing or {})
        brief_minimum = int(brief.get("minimum_words") or 0)
        brief_maximum = int(brief.get("maximum_words") or 0)
        contract = ContentKnowledgeContract.model_validate(state.contract)
        active_ids = set(active_node_ids(contract))
        active_nodes = [node for node in contract.nodes if node.node_id in active_ids]
        section_count = max(1, len(active_nodes))
        if contract.content_type == EditorialContentTypeV3.procedural_decision_guide:
            method_count = len(state.method_dossiers)
            scope_minimum = procedural_structural_minimum_words(
                method_count, section_count
            )
        else:
            core_weight = sum(
                node.minimum_depth_weight
                for node in active_nodes
                if node.importance.value == "core"
            )
            supporting_weight = sum(
                node.minimum_depth_weight
                for node in active_nodes
                if node.importance.value != "core"
            )
            # This is a structural floor, not a universal long-form quota. It
            # allows a focused 650–900 word brief when the active architecture
            # genuinely fits while still rejecting impossible scopes.
            scope_minimum = int(260 + core_weight * 82 + supporting_weight * 38)
            scope_minimum = max(500, scope_minimum)

        maximum = (
            min(configured_maximum, brief_maximum)
            if brief_maximum
            else configured_maximum
        )
        if brief_minimum:
            minimum = max(scope_minimum, brief_minimum)
        elif brief_maximum:
            minimum = scope_minimum
        else:
            minimum = max(scope_minimum, configured_minimum)
        if minimum > maximum:
            raise V3PipelineBlocked(
                "A faixa de palavras não comporta os nós ativos do contrato: "
                f"mínimo estrutural {minimum}, máximo editorial {maximum}.",
                "V3_WORD_RANGE_SCOPE_CONFLICT",
            )
        return minimum, maximum

    async def _validate_intelligence_draft_stage(
        self,
        state: V3PipelineState,
        draft: V3WriterOutput,
        *,
        stage: str,
        article_version_id: uuid.UUID | None = None,
    ) -> None:
        """Invalidate, revalidate and snapshot the canonical state after any draft stage."""

        if not state.intelligence_state:
            return
        current = ContentIntelligenceState.model_validate(state.intelligence_state)
        pending = self.intelligence.mark_draft_pending(current)
        state.intelligence_state = pending.model_dump(mode="json")
        state.intelligence_revision = pending.revision
        await self.intelligence_repository.save(
            pending,
            stage=f"{stage}:pending_validation",
            status=pending.lifecycle.value,
        )
        report = self.intelligence.validate_draft(pending, draft)
        state.intelligence_validation = report.model_dump(mode="json")
        validated = self.intelligence.mark_draft_validated(
            pending,
            report,
            draft=draft,
            article_version_id=article_version_id,
        )
        state.intelligence_state = validated.model_dump(mode="json")
        state.intelligence_revision = validated.revision
        await self.intelligence_repository.save(
            validated,
            stage=stage,
            status=validated.lifecycle.value,
            validation=state.intelligence_validation,
        )
        if report.status != "passed":
            raise V3PipelineBlocked(
                "Draft failed Editorial Intelligence validation after "
                f"{stage}: " + ", ".join(item.code for item in report.blockers[:12]),
                "V3_DRAFT_INTELLIGENCE_INVALID",
            )

    def _merge_intelligence_diagnostics(
        self,
        state: V3PipelineState,
        draft: V3WriterOutput,
        diagnostics: dict,
    ) -> tuple[dict, object | None]:
        if not state.intelligence_state:
            return diagnostics, None
        intelligence = ContentIntelligenceState.model_validate(state.intelligence_state)
        report = self.intelligence.validate_draft(intelligence, draft)
        merged = {
            **diagnostics,
            "blockers": list(diagnostics.get("blockers") or []),
            "warnings": list(diagnostics.get("warnings") or []),
            "editorial_intelligence": report.model_dump(mode="json"),
        }
        merged["blockers"].extend(
            {
                "code": item.code,
                "message": item.message,
                "details": {
                    **item.details,
                    "section_id": item.section_id,
                    "question_id": item.question_id,
                    "claim_id": str(item.claim_id) if item.claim_id else None,
                    "source_id": str(item.source_id) if item.source_id else None,
                },
            }
            for item in report.blockers
        )
        merged["warnings"].extend(
            {
                "code": item.code,
                "message": item.message,
                "details": {
                    **item.details,
                    "section_id": item.section_id,
                    "question_id": item.question_id,
                    "claim_id": str(item.claim_id) if item.claim_id else None,
                    "source_id": str(item.source_id) if item.source_id else None,
                },
            }
            for item in report.warnings
        )
        merged["status"] = "blocked" if merged["blockers"] else "passed"
        return merged, report

    def _draft_diagnostics(
        self,
        state: V3PipelineState,
        draft: V3WriterOutput,
        *,
        minimum_word_count: int,
        maximum_word_count: int,
    ) -> dict:
        blockers: list[dict] = []
        warnings: list[dict] = []

        def add_blocker(code: str, message: str, **details) -> None:
            blockers.append({"code": code, "message": message, "details": details})

        def add_warning(code: str, message: str, **details) -> None:
            warnings.append({"code": code, "message": message, "details": details})

        text = " ".join(
            sentence.text
            for block in draft.blocks
            for sentence in block.content_sentences
        )
        markdown = _draft_markdown_for_analysis(draft)
        word_count = len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", text))
        contract = ContentKnowledgeContract.model_validate(state.contract)
        is_procedural = (
            contract.content_type == EditorialContentTypeV3.procedural_decision_guide
        )
        methods = [MethodDossier.model_validate(item) for item in state.method_dossiers]
        h1_blocks = [block for block in draft.blocks if block.type == "h1"]
        if len(h1_blocks) != 1:
            add_blocker(
                "DRAFT_H1_COUNT_INVALID",
                "O rascunho deve conter exatamente um H1.",
                actual=len(h1_blocks),
                required=1,
            )
        elif normalized_text(h1_blocks[0].sentences[0].text) != normalized_text(
            draft.title
        ):
            add_blocker(
                "DRAFT_H1_TITLE_MISMATCH",
                "O H1 deve corresponder ao título editorial aprovado.",
                title=draft.title,
                h1=h1_blocks[0].sentences[0].text,
            )
        required_sections = set(active_node_ids(contract))
        covered_sections = set(draft.covered_section_ids)
        missing_sections = sorted(required_sections - covered_sections)
        unknown_sections = sorted(covered_sections - required_sections)
        section_first_positions: dict[str, int] = {}
        section_word_counts: dict[str, int] = {}
        for block in draft.blocks:
            section_first_positions.setdefault(block.section_id, block.position)
            section_word_counts[block.section_id] = section_word_counts.get(
                block.section_id, 0
            ) + sum(
                len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", sentence.text))
                for sentence in block.content_sentences
            )
        if missing_sections or unknown_sections:
            add_blocker(
                "DRAFT_SECTION_INCOMPLETE",
                "O rascunho precisa cobrir todos e somente os nós do contrato.",
                missing_section_ids=missing_sections,
                unknown_section_ids=unknown_sections,
            )
        represented_positions = [
            section_first_positions[node.node_id]
            for node in contract.nodes
            if node.node_id in required_sections
            and node.node_id in section_first_positions
        ]
        if represented_positions != sorted(represented_positions):
            add_blocker(
                "DRAFT_SECTION_ORDER_INVALID",
                "O rascunho não respeita a progressão editorial do contrato.",
                expected_order=[
                    node.node_id
                    for node in contract.nodes
                    if node.node_id in required_sections
                ],
                first_positions=section_first_positions,
            )
        for node in contract.nodes:
            if node.node_id not in required_sections:
                continue
            current = section_first_positions.get(node.node_id)
            if current is None:
                continue
            invalid_dependencies = [
                dependency
                for dependency in node.depends_on
                if dependency not in section_first_positions
                or section_first_positions[dependency] >= current
            ]
            if invalid_dependencies:
                add_blocker(
                    "DRAFT_DEPENDENCY_INVALID",
                    f"O nó {node.node_id} aparece antes de suas dependências.",
                    node_id=node.node_id,
                    invalid_dependencies=invalid_dependencies,
                )

        normalized_depth: dict[str, float] = {}
        for node in contract.nodes:
            words = section_word_counts.get(node.node_id, 0)
            normalized_depth[node.node_id] = words / max(0.1, node.minimum_depth_weight)
            minimum_words = max(55, int(75 * node.minimum_depth_weight))
            if node.importance.value == "core" and words < minimum_words:
                add_blocker(
                    "DRAFT_CORE_NODE_TOO_SHALLOW",
                    f"O nó central {node.node_id} não recebeu profundidade suficiente.",
                    node_id=node.node_id,
                    word_count=words,
                    minimum_word_count=minimum_words,
                )
            if node.maximum_depth_weight is not None and words > int(
                180 * node.maximum_depth_weight
            ):
                add_warning(
                    "DRAFT_NODE_ABOVE_DEPTH_TARGET",
                    f"O nó {node.node_id} ultrapassou sua profundidade planejada.",
                    node_id=node.node_id,
                    word_count=words,
                )
        core_depths = [
            normalized_depth[node.node_id]
            for node in contract.nodes
            if node.importance.value == "core"
            and section_word_counts.get(node.node_id, 0)
        ]
        peripheral_nodes = [
            node
            for node in contract.nodes
            if node.importance.value == "peripheral"
            and section_word_counts.get(node.node_id, 0)
        ]
        if core_depths and peripheral_nodes:
            shallowest_core = min(core_depths)
            for node in peripheral_nodes:
                if normalized_depth[node.node_id] > shallowest_core * 1.35:
                    add_blocker(
                        "DRAFT_PERIPHERAL_DEPTH_INVERSION",
                        "Um nó periférico recebeu mais desenvolvimento proporcional que um nó central.",
                        peripheral_node_id=node.node_id,
                        normalized_depth=normalized_depth[node.node_id],
                        shallowest_core_depth=shallowest_core,
                    )

        method_labels = [
            label
            for method in methods
            for label in [method.name, *method.aliases]
            if label
        ]
        prose_quality = analyze_editorial_prose(
            markdown, method_labels=method_labels if is_procedural else []
        )
        if prose_quality["summary_like_compression"]:
            add_blocker(
                "DRAFT_SUMMARY_LIKE_COMPRESSION",
                "O corpo está comprimido em resumos sem desenvolvimento suficiente.",
            )
        if prose_quality["heading_body_imbalance"]:
            add_blocker(
                "DRAFT_HEADING_BODY_IMBALANCE",
                "Há subtítulos demais para pouco desenvolvimento do corpo.",
            )
        if prose_quality["severe_mechanical_prose"]:
            add_blocker(
                "DRAFT_MECHANICAL_PROSE",
                "A cadência e a construção dos parágrafos estão mecânicas demais.",
            )
        if prose_quality["premature_numeric_density"]:
            add_blocker(
                "DRAFT_PREMATURE_NUMERIC_DENSITY",
                "A abertura antecipa números antes de orientar o leitor.",
            )
        if prose_quality["meta_narration_matches"]:
            add_blocker(
                "DRAFT_META_NARRATION",
                "O texto narra a própria redação em vez de desenvolver o assunto.",
            )
        if prose_quality["repeated_sentence_openers"]:
            add_warning(
                "DRAFT_REPEATED_OPENERS",
                "Muitas frases começam com a mesma construção.",
            )
        if (
            prose_quality["uniform_sentence_cadence"]
            or prose_quality["uniform_paragraph_shape"]
        ):
            add_warning(
                "DRAFT_UNIFORM_CADENCE",
                "A cadência ou o formato dos parágrafos está uniforme demais.",
            )

        per_method: dict[str, dict] = {}
        required_method_ids = {item.method_id for item in methods}
        covered_method_ids = set(draft.covered_method_ids)
        if is_procedural:
            inventory_position = section_first_positions.get("method_inventory")
            requirements_position = section_first_positions.get("process_requirements")
            if (
                inventory_position is not None
                and requirements_position is not None
                and inventory_position > requirements_position
            ):
                add_blocker(
                    "DRAFT_METHODS_PRESENTED_TOO_LATE",
                    "As abordagens precisam ser apresentadas antes das condições compartilhadas.",
                )
            missing_method_ids = sorted(required_method_ids - covered_method_ids)
            extra_method_ids = sorted(covered_method_ids - required_method_ids)
            if missing_method_ids:
                add_blocker(
                    "DRAFT_METHOD_INCOMPLETE",
                    "Há abordagens aprovadas que não foram desenvolvidas.",
                    missing_method_ids=missing_method_ids,
                )
            if extra_method_ids:
                add_blocker(
                    "DRAFT_METHOD_UNKNOWN",
                    "O rascunho declarou abordagens fora do inventário aprovado.",
                    extra_method_ids=extra_method_ids,
                )
            minimum_steps = int(self._flag("v3_min_steps_per_method"))
            for method in methods:
                method_blocks = [
                    block
                    for block in draft.blocks
                    if block.method_id == method.method_id
                ]
                method_text = " ".join(
                    sentence.text
                    for block in method_blocks
                    for sentence in block.content_sentences
                )
                method_words = len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", method_text))
                heading_count = sum(
                    block.type in {"h2", "h3"} for block in method_blocks
                )
                list_item_count = sum(
                    len(block.content_sentences)
                    for block in method_blocks
                    if block.type == "list"
                )
                expected_steps = max(minimum_steps, len(method.steps))
                reference_url = (
                    str(method.external_reference.url)
                    if method.external_reference is not None
                    else ""
                )
                reference_present = bool(reference_url and reference_url in method_text)
                per_method[method.method_id] = {
                    "name": method.name,
                    "block_count": len(method_blocks),
                    "heading_count": heading_count,
                    "list_item_count": list_item_count,
                    "expected_step_count": expected_steps,
                    "word_count": method_words,
                    "external_reference_present": reference_present,
                }
                if not method_blocks:
                    add_blocker(
                        "DRAFT_METHOD_WITHOUT_BLOCKS",
                        f"A abordagem {method.name} não possui blocos identificados.",
                        method_id=method.method_id,
                    )
                    continue
                if heading_count == 0:
                    add_blocker(
                        "DRAFT_METHOD_WITHOUT_HEADING",
                        f"A abordagem {method.name} não possui subtítulo próprio.",
                        method_id=method.method_id,
                    )
                if list_item_count < expected_steps:
                    add_blocker(
                        "DRAFT_METHOD_STEPS_SHALLOW",
                        f"A abordagem {method.name} não apresenta os passos completos.",
                        method_id=method.method_id,
                    )
                if not reference_present:
                    add_blocker(
                        "DRAFT_METHOD_REFERENCE_MISSING",
                        f"O link externo aprovado da abordagem {method.name} não foi incluído.",
                        method_id=method.method_id,
                        required_url=reference_url,
                    )
        elif covered_method_ids or any(block.method_id for block in draft.blocks):
            add_blocker(
                "DRAFT_NONPROCEDURAL_METHOD_LEAK",
                "Conteúdo não procedural não pode inventar abordagens ou blocos de abordagem.",
            )

        if word_count < minimum_word_count:
            add_blocker(
                "DRAFT_TOO_SHORT",
                "O artigo está curto para o escopo contratado.",
                word_count=word_count,
                minimum_word_count=minimum_word_count,
            )
        elif word_count < int(minimum_word_count * 1.08):
            add_warning(
                "DRAFT_NEAR_MINIMUM",
                "O artigo está muito próximo do mínimo planejado.",
                word_count=word_count,
            )
        if word_count > maximum_word_count:
            add_blocker(
                "DRAFT_TOO_LONG",
                "O artigo excedeu o limite máximo aceito no briefing.",
                word_count=word_count,
                maximum_word_count=maximum_word_count,
            )

        generation = generation_brief(self.project, self.execution_manifest, contract)
        structure = generation.get("structure") or {}

        primary_keyword = str(generation.get("primary_keyword") or "").strip()
        if primary_keyword:
            title_coverage = keyword_coverage(primary_keyword, draft.title)
            body_coverage = keyword_coverage(primary_keyword, text)
            if title_coverage < 0.50:
                add_blocker(
                    "DRAFT_PRIMARY_KEYWORD_TITLE_MISSING",
                    "O título/H1 não cobre suficientemente a palavra-chave principal do briefing.",
                    primary_keyword=primary_keyword,
                    coverage=round(title_coverage, 4),
                )
            if body_coverage < 0.60:
                add_blocker(
                    "DRAFT_PRIMARY_KEYWORD_BODY_MISSING",
                    "O corpo não responde de forma lexicalmente identificável ao tópico principal.",
                    primary_keyword=primary_keyword,
                    coverage=round(body_coverage, 4),
                )
        secondary_keywords = [
            str(item).strip()
            for item in generation.get("secondary_keywords") or []
            if str(item).strip()
        ]
        uncovered_secondary = [
            item for item in secondary_keywords if keyword_coverage(item, text) < 0.67
        ]
        if secondary_keywords and len(uncovered_secondary) == len(secondary_keywords):
            add_warning(
                "DRAFT_SECONDARY_KEYWORDS_UNUSED",
                "Nenhuma palavra-chave secundária foi coberta de forma natural.",
                keywords=uncovered_secondary,
            )

        commercial = generation.get("commercial") or {}
        closing_text = " ".join(
            sentence.text
            for block in draft.blocks
            if block.section_id == "closing"
            for sentence in block.content_sentences
        )
        for field, code, label in (
            ("offer", "DRAFT_OFFER_MISSING", "oferta"),
            ("desired_action", "DRAFT_CTA_MISSING", "ação desejada"),
        ):
            requirement = str(commercial.get(field) or "").strip()
            if requirement and keyword_coverage(requirement, closing_text) < 0.45:
                add_blocker(
                    code,
                    f"O fechamento não contempla a {label} definida no briefing.",
                    expected=requirement,
                )

        for example in (generation.get("brand") or {}).get(
            "approved_style_examples"
        ) or []:
            example_text = str(example).strip()
            if len(example_text) < 25:
                continue
            normalized_example = normalized_text(example_text)
            normalized_draft = normalized_text(text)
            copy_score = shingle_similarity(example_text, text, size=4)
            if normalized_example and normalized_example in normalized_draft:
                add_blocker(
                    "DRAFT_STYLE_EXAMPLE_COPIED",
                    "Um exemplo de estilo aprovado foi copiado literalmente.",
                    excerpt=example_text[:180],
                )
            elif copy_score >= 0.62:
                add_warning(
                    "DRAFT_STYLE_EXAMPLE_TOO_SIMILAR",
                    "O rascunho está lexicalmente próximo demais de um exemplo de estilo.",
                    similarity=round(copy_score, 4),
                )
        h2_count = sum(block.type == "h2" for block in draft.blocks)
        h3_count = sum(block.type == "h3" for block in draft.blocks)
        minimum_h2 = int(structure.get("minimum_h2") or 0)
        minimum_h3 = int(structure.get("minimum_h3") or 0)
        if h2_count < minimum_h2:
            add_blocker(
                "DRAFT_MINIMUM_H2_NOT_MET",
                "O rascunho não atingiu o mínimo de H2 do briefing.",
                actual=h2_count,
                required=minimum_h2,
            )
        if h3_count < minimum_h3:
            add_blocker(
                "DRAFT_MINIMUM_H3_NOT_MET",
                "O rascunho não atingiu o mínimo de H3 do briefing.",
                actual=h3_count,
                required=minimum_h3,
            )
        heading_text = " ".join(
            sentence.text
            for block in draft.blocks
            if block.type in {"h1", "h2", "h3"}
            for sentence in block.content_sentences
        ).casefold()
        for required in structure.get("required_sections") or []:
            required_words = {
                item
                for item in re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", str(required).casefold())
            }
            if required_words and not required_words.issubset(
                set(re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", heading_text))
            ):
                add_blocker(
                    "DRAFT_REQUIRED_SECTION_MISSING",
                    f"A seção obrigatória '{required}' não foi localizada nos headings.",
                    required_section=required,
                )
        for forbidden in (generation.get("evidence_policy") or {}).get(
            "claims_to_avoid"
        ) or []:
            forbidden_norm = " ".join(str(forbidden).casefold().split())
            if forbidden_norm and forbidden_norm in " ".join(text.casefold().split()):
                add_blocker(
                    "DRAFT_PROHIBITED_CLAIM_PRESENT",
                    "O rascunho contém uma alegação proibida pelo briefing.",
                    prohibited_claim=forbidden,
                )
        locale = str(generation.get("locale") or self.project.language or "pt-BR")
        language_diagnostics = language_report(text, locale)
        if language_diagnostics.get("blocked"):
            add_blocker(
                "DRAFT_LANGUAGE_MISMATCH",
                "O idioma real do rascunho não corresponde ao locale do projeto.",
                **language_diagnostics,
            )

        internal_link = str(generation.get("internal_link") or "").strip()
        if internal_link and internal_link not in text:
            add_blocker(
                "DRAFT_INTERNAL_LINK_MISSING",
                "O link interno relacionado solicitado no briefing não foi incluído.",
                related_page_url=internal_link,
            )

        return {
            "status": "blocked" if blockers else "passed",
            "architecture_type": contract.content_type.value,
            "word_count": word_count,
            "h2_count": h2_count,
            "h3_count": h3_count,
            "target_word_range": [minimum_word_count, maximum_word_count],
            "required_section_count": len(required_sections),
            "covered_section_count": len(required_sections & covered_sections),
            "required_method_count": len(required_method_ids),
            "covered_method_count": len(required_method_ids & covered_method_ids),
            "per_method": per_method,
            "section_first_positions": section_first_positions,
            "section_word_counts": section_word_counts,
            "normalized_node_depth": normalized_depth,
            "prose_quality": prose_quality,
            "language_quality": language_diagnostics,
            "blockers": blockers,
            "warnings": warnings,
        }

    def _apply_brief_source_policy(
        self, document: StructuredSourceDocument, state: V3PipelineState
    ) -> StructuredSourceDocument:
        """Apply user-declared source exclusions and freshness limits after reading."""

        contract = ContentKnowledgeContract.model_validate(state.contract)
        policy = (
            generation_brief(self.project, self.execution_manifest, contract).get(
                "evidence_policy"
            )
            or {}
        )
        url = canonicalize_url(str(document.canonical_url))
        host = (urlsplit(url).hostname or "").casefold().removeprefix("www.")
        prohibited = [
            normalized_text(str(item))
            for item in policy.get("prohibited_sources") or []
        ]
        preferred = [
            normalized_text(str(item)) for item in policy.get("preferred_sources") or []
        ]
        url_norm = normalized_text(url)
        host_norm = normalized_text(host)
        reasons: list[str] = []
        warnings = list(document.warnings)

        if any(item and (item in url_norm or item in host_norm) for item in prohibited):
            reasons.append("BRIEF_PROHIBITED_SOURCE")
        maximum_age = policy.get("maximum_source_age_days")
        if maximum_age:
            maximum_age = int(maximum_age)
            if document.published_at is None:
                reasons.append("BRIEF_SOURCE_DATE_UNKNOWN")
            else:
                published = document.published_at
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                if published < datetime.now(timezone.utc) - timedelta(days=maximum_age):
                    reasons.append("BRIEF_SOURCE_TOO_OLD")
        if preferred and any(
            item and (item in url_norm or item in host_norm) for item in preferred
        ):
            warnings.append("BRIEF_PREFERRED_SOURCE_MATCH")

        if not reasons:
            return document.model_copy(
                update={"warnings": list(dict.fromkeys(warnings))[:50]}
            )
        assessment = document.assessment.model_copy(
            update={
                "usage_policy": SourceUsagePolicy.rejected,
                "eligible_for_primary_evidence": False,
                "eligible_for_corroborating_evidence": False,
                "eligible_for_external_reference": False,
                "counts_toward_independent_source_diversity": False,
                "absolute_claim_support_allowed": False,
                "allowed_evidence_roles": [],
                "reason_codes": list(
                    dict.fromkeys([*document.assessment.reason_codes, *reasons])
                )[:30],
                "warnings": list(
                    dict.fromkeys([*document.assessment.warnings, *warnings])
                )[:30],
            }
        )
        return document.model_copy(
            update={
                "assessment": assessment,
                "warnings": list(dict.fromkeys([*warnings, *reasons]))[:50],
            }
        )

    def _validate_draft_evidence(
        self,
        draft: V3WriterOutput,
        state: V3PipelineState,
        *,
        require_complete_scope: bool = True,
    ) -> None:
        """Apply a deterministic evidence floor to every generated sentence.

        Model-declared ``is_factual`` and ``entailment_score`` values are never
        trusted as authority. Obvious verifiable statements are promoted to
        factual, evidence IDs are resolved against the approved claim catalog,
        and claim-to-sentence support is recomputed before a draft may advance.
        """

        catalog = {
            uuid.UUID(str(item["claim_id"])): item
            for item in state.knowledge_claims
            if item.get("claim_id")
        }
        active_sections = set(
            active_node_ids(ContentKnowledgeContract.model_validate(state.contract))
        )
        allowed_claims_by_section: dict[str, set[uuid.UUID]] = {}
        if state.intelligence_state:
            intelligence = ContentIntelligenceState.model_validate(
                state.intelligence_state
            )
            allowed_claims_by_section = {
                section.section_id: set(section.allowed_claim_ids)
                for section in intelligence.sections
            }
        violations: list[str] = []

        for block in draft.blocks:
            if block.section_id not in active_sections:
                violations.append(
                    f"block {block.block_id} references inactive section {block.section_id}"
                )
            for sentence in block.content_sentences:
                deterministic_factual = is_potentially_factual(
                    sentence.text, block_type=block.type
                )
                if deterministic_factual and not sentence.is_factual:
                    violations.append(
                        f"sentence was labeled editorial but is verifiable: {sentence.text[:180]}"
                    )
                factual = sentence.is_factual or deterministic_factual
                if factual and not sentence.evidence:
                    violations.append(
                        f"factual sentence has no approved evidence: {sentence.text[:180]}"
                    )
                    continue
                if not factual and sentence.evidence:
                    violations.append(
                        f"editorial sentence carries evidence: {sentence.text[:180]}"
                    )
                    continue
                if not factual:
                    continue

                claims: list[dict] = []
                supported_claims: list[str] = []
                irrelevant_claims: list[str] = []
                support_groups: set[str] = set()
                for evidence in sentence.evidence:
                    claim = catalog.get(evidence.claim_id)
                    if claim is None:
                        violations.append(
                            f"sentence references unapproved claim {evidence.claim_id}"
                        )
                        continue
                    claims.append(claim)
                    allowed_claims = allowed_claims_by_section.get(block.section_id)
                    if (
                        allowed_claims is not None
                        and evidence.claim_id not in allowed_claims
                    ):
                        violations.append(
                            "sentence uses a claim not authorized for section "
                            f"{block.section_id}: {evidence.claim_id}"
                        )
                    claim_section = str(claim.get("knowledge_node_id") or "")
                    if claim_section and claim_section != block.section_id:
                        violations.append(
                            "sentence uses a claim owned by another section "
                            f"({claim_section} -> {block.section_id}): {evidence.claim_id}"
                        )
                    support_group = normalized_text(
                        str(claim.get("support_group") or claim.get("claim_id") or "")
                    )
                    if support_group:
                        support_groups.add(support_group)
                    supported, score, reason = claim_supports_sentence(
                        sentence.text,
                        str(claim.get("claim_text") or ""),
                        conditions=claim.get("conditions") or (),
                        limitations=claim.get("limitations") or (),
                        minimum_score=0.42,
                    )
                    evidence.entailment_score = score
                    if supported:
                        supported_claims.append(str(evidence.claim_id))
                    elif score < 0.12:
                        irrelevant_claims.append(str(evidence.claim_id))
                    else:
                        violations.append(
                            "individual claim does not entail the sentence "
                            f"({reason}, score={score:.2f}, claim={evidence.claim_id}): "
                            f"{sentence.text[:180]}"
                        )

                if not claims:
                    continue
                if not supported_claims:
                    violations.append(
                        "no individual approved claim supports the factual sentence; "
                        "split it into atomic statements instead of combining evidence: "
                        f"{sentence.text[:180]}"
                    )
                if len(support_groups) > 1:
                    violations.append(
                        "sentence combines independent claim groups; split it into atomic "
                        f"sentences: {sentence.text[:180]}"
                    )
                if irrelevant_claims:
                    violations.append(
                        "sentence contains unrelated evidence IDs: "
                        + ", ".join(irrelevant_claims)
                    )

        covered_sections = set(draft.covered_section_ids)
        unknown_sections = covered_sections - active_sections
        if require_complete_scope:
            missing_sections = active_sections - covered_sections
            if missing_sections:
                violations.append(
                    "draft omits active sections: "
                    + ", ".join(sorted(missing_sections))
                )
        if unknown_sections:
            violations.append(
                "draft references inactive or unknown sections: "
                + ", ".join(sorted(unknown_sections))
            )

        required_methods = {item["method_id"] for item in state.method_dossiers}
        referenced_methods = set(draft.covered_method_ids) | {
            block.method_id for block in draft.blocks if block.method_id
        }
        unknown_methods = referenced_methods - required_methods
        if unknown_methods:
            violations.append(
                "draft references unknown methods: "
                + ", ".join(sorted(unknown_methods))
            )

        if violations:
            raise V3PipelineBlocked(
                "Draft evidence validation failed: " + "; ".join(violations[:12]),
                "V3_DRAFT_EVIDENCE_INVALID",
            )

    def _validate_fact_check_review(
        self,
        *,
        draft: V3WriterOutput,
        review: V3FactCheckReview,
        state: V3PipelineState,
        require_passed: bool = True,
    ) -> None:
        """Require one exact fact-check result per logical sentence identity."""

        expected: dict[uuid.UUID, tuple[uuid.UUID, str, set[uuid.UUID]]] = {}
        for block in draft.blocks:
            for sentence in block.content_sentences:
                if sentence.is_factual or is_potentially_factual(
                    sentence.text, block_type=block.type
                ):
                    expected[sentence.sentence_id] = (
                        block.block_id,
                        normalized_text(sentence.text),
                        {item.claim_id for item in sentence.evidence},
                    )

        actual: dict[uuid.UUID, object] = {}
        violations: list[str] = []
        for check in review.checks:
            key = check.sentence_id
            if key in actual:
                violations.append(
                    f"duplicate fact-check for sentence_id {check.sentence_id}"
                )
                continue
            actual[key] = check
            expected_item = expected.get(key)
            if expected_item is None:
                violations.append(
                    f"fact-check references a missing or non-current sentence_id {check.sentence_id}"
                )
                continue
            expected_block, expected_text, expected_claims = expected_item
            if check.block_id != expected_block:
                violations.append(
                    f"fact-check block does not match sentence_id {check.sentence_id}"
                )
            if normalized_text(check.sentence_text) != expected_text:
                violations.append(
                    f"fact-check text does not match sentence_id {check.sentence_id}"
                )
            if set(check.claim_ids) != expected_claims:
                violations.append(
                    f"fact-check claim IDs do not match sentence evidence for {check.sentence_id}"
                )
            if check.status != "supported":
                violations.append(
                    f"fact-check did not support sentence {check.sentence_id}: {check.status}"
                )

        missing = set(expected) - set(actual)
        if missing:
            violations.append(
                f"fact-check omitted {len(missing)} factual sentence(s): "
                + ", ".join(str(item) for item in sorted(missing, key=str)[:10])
            )
        extras = set(actual) - set(expected)
        if extras:
            violations.append(
                f"fact-check included {len(extras)} non-current sentence(s)"
            )
        if require_passed and review.status != "passed":
            violations.append(f"fact-check final status is {review.status}")
        if expected and not review.checks:
            violations.append("fact-check returned no checks for a factual draft")

        self._validate_draft_evidence(draft, state)
        if violations:
            raise V3PipelineBlocked(
                "Fact-check integrity validation failed: " + "; ".join(violations[:12]),
                "V3_FACT_CHECK_INTEGRITY_INVALID",
            )

    def _document_for_agent(
        document: StructuredSourceDocument, research_goal: str = ""
    ) -> dict:
        """Select relevant sections and remove instruction-like source fragments."""

        goal_tokens = set(normalized_text(research_goal).split())

        def relevance(section) -> tuple[float, int]:
            payload = " ".join(
                [
                    *section.heading_path,
                    *section.paragraphs[:30],
                    *section.ordered_steps[:30],
                    *section.unordered_items[:30],
                ]
            )
            section_tokens = set(normalized_text(payload).split())
            overlap = len(goal_tokens & section_tokens) / max(1, len(goal_tokens))
            structural_bonus = 0.08 if section.ordered_steps or section.tables else 0.0
            return overlap + structural_bonus, section.character_count

        ranked = sorted(document.sections, key=relevance, reverse=True)[:24]
        ranked = sorted(ranked, key=lambda item: document.sections.index(item))
        removed_fragments = 0
        safe_sections: list[dict] = []
        for section in ranked:

            def safe_many(values, limit):
                nonlocal removed_fragments
                result = []
                for raw in list(values)[:limit]:
                    clean, removed = _safe_source_fragment(str(raw))
                    removed_fragments += int(removed)
                    if clean:
                        result.append(clean)
                return result

            safe_tables = []
            for table in section.tables[:6]:
                payload = table.model_dump(mode="json")
                serialized = json.dumps(payload, ensure_ascii=False, default=str)
                _clean, removed = _safe_source_fragment(serialized)
                if removed:
                    removed_fragments += 1
                    continue
                safe_tables.append(payload)
            safe_sections.append(
                {
                    "source_locator": section.source_locator,
                    "heading_path": safe_many(section.heading_path, 12),
                    "paragraphs": safe_many(section.paragraphs, 20),
                    "ordered_steps": safe_many(section.ordered_steps, 30),
                    "unordered_items": safe_many(section.unordered_items, 30),
                    "tables": safe_tables,
                }
            )
        return {
            "url": str(document.canonical_url),
            "title": document.title,
            "author": document.author,
            "publisher": document.publisher,
            "published_at": document.published_at,
            "source_role": document.assessment.source_role.value,
            "source_usage_policy": document.assessment.usage_policy.value,
            "allowed_evidence_roles": [
                item.value for item in document.assessment.allowed_evidence_roles
            ],
            "selection_goal": research_goal,
            "document_truncated": document.truncated,
            "agent_sanitization": {
                "instruction_like_fragments_removed": removed_fragments,
                "source_content_is_untrusted_data": True,
            },
            "sections": safe_sections,
        }

    async def _source_report(self) -> list[dict]:
        rows = list(
            (
                await self.db.scalars(
                    select(V3SourceDocumentRecord).where(
                        V3SourceDocumentRecord.pipeline_run_id == self.pipeline_run.id,
                        V3SourceDocumentRecord.status.in_(
                            ["accepted", "comparison_only", "discovery_only"]
                        ),
                    )
                )
            ).all()
        )
        return [
            {
                "url": row.canonical_url,
                "title": row.title,
                "source_role": row.source_role,
                "usage_policy": row.usage_policy,
                "status": row.status,
            }
            for row in rows
        ]

    @staticmethod
    def _truncate_at_word(value: str, maximum: int) -> str:
        clean = " ".join((value or "").split())
        if len(clean) <= maximum:
            return clean
        candidate = clean[: maximum + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return candidate or clean[:maximum].rstrip()

    @classmethod
    def _meta_description(cls, draft: V3WriterOutput) -> str:
        sentences = [
            " ".join(sentence.text.split())
            for block in draft.blocks
            if block.type in {"paragraph", "callout"}
            for sentence in block.content_sentences
            if sentence.text.strip()
        ]
        selected: list[str] = []
        for sentence in sentences:
            candidate = " ".join([*selected, sentence]).strip()
            if len(candidate) <= 160:
                selected.append(sentence)
                if len(candidate) >= 120:
                    break
            elif selected:
                break
        result = " ".join(selected).strip()
        if not result:
            # Prefer a complete, truthful fallback over cutting a factual sentence.
            result = draft.title.rstrip(".!?") + "."
        if len(result) > 160:
            result = (
                cls._truncate_at_word(draft.title.rstrip(".!?"), 158).rstrip(".!?")
                + "."
            )
        return result

    async def _stage(self, stage: str, message: str, state: V3PipelineState) -> None:
        await self._cancellation_boundary()
        self.pipeline_run.current_stage = stage
        self.project.current_stage = stage
        self.project.status = ProjectStatus.running
        self._stage_context = EventContext.for_stage(
            self.pipeline_run.id,
            stage,
            0,
            0,
            self.pipeline_run.attempt,
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "stage.started",
            stage,
            {"message": message, "pipeline_contract_version": "editorial-v3.8"},
            idempotency_key=self._stage_context.event_key("stage.started"),
            context=self._stage_context,
        )
        await self.db.commit()

    async def _progress_checkpoint(
        self,
        stage: str,
        state: V3PipelineState,
        *,
        unit_id: str,
        completed: int,
        total: int,
    ) -> None:
        checkpoint = await self.checkpoints.save(
            self.pipeline_run,
            stage,
            stage,
            state.model_dump(mode="json"),
            result={
                "progress_unit_id": unit_id,
                "completed": completed,
                "total": total,
                "pipeline_contract_version": "editorial-v3.8",
            },
            resumable=True,
            event_context=self._stage_context,
            idempotency_suffix=f"progress:{unit_id}",
        )
        context = (
            self._stage_context.with_checkpoint(checkpoint.sequence)
            if self._stage_context
            else None
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "stage.progress",
            stage,
            {
                "message": f"Informação {completed} de {total} concluída e salva",
                "checkpoint_id": str(checkpoint.id),
                "checkpoint_sequence": checkpoint.sequence,
                "unit_id": unit_id,
                "completed": completed,
                "total": total,
            },
            idempotency_key=f"stage.progress:{stage}:{unit_id}",
            context=context,
        )
        self.pipeline_run = await self.run_service.renew_lease(
            self.pipeline_run.id,
            self.lease_owner,
        )
        await self.db.commit()
        await self._cancellation_boundary()

    async def _checkpoint(self, completed_stage: str, state: V3PipelineState) -> None:
        checkpoint = await self.checkpoints.save(
            self.pipeline_run,
            completed_stage,
            state.stage.value,
            state.model_dump(mode="json"),
            result={
                "blocking_reason": state.blocking_reason,
                "blocking_code": state.blocking_code,
                "pipeline_contract_version": "editorial-v3.8",
            },
            resumable=state.stage not in {V3Stage.blocked, V3Stage.completed},
            event_context=self._stage_context,
        )
        if self._stage_context:
            context = self._stage_context.with_checkpoint(checkpoint.sequence)
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "stage.completed",
                completed_stage,
                {
                    "checkpoint_id": str(checkpoint.id),
                    "checkpoint_sequence": checkpoint.sequence,
                    "next_stage": state.stage.value,
                },
                idempotency_key=context.event_key("stage.completed"),
                context=context,
            )
        self.pipeline_run = await self.run_service.renew_lease(
            self.pipeline_run.id,
            self.lease_owner,
        )
        await self.db.commit()
        await self._cancellation_boundary()

    async def _cancellation_boundary(self) -> None:
        snapshot = await self.db.execute(
            select(PipelineRun.status, PipelineRun.cancellation_requested_at).where(
                PipelineRun.id == self.pipeline_run.id
            )
        )
        row = snapshot.one_or_none()
        if row and (
            row.cancellation_requested_at is not None
            or row.status == PipelineRunStatus.cancelled
        ):
            raise PipelineCancellationRequested("Pipeline cancellation requested")

    def _optional_flag(self, name: str, default):
        if self.execution_manifest is None:
            return getattr(settings, name, default)
        flags = self.execution_manifest.get("feature_flags") or {}
        return flags.get(name, default)

    def _flag(self, name: str):
        if self.execution_manifest is None:
            return getattr(settings, name)
        flags = self.execution_manifest.get("feature_flags") or {}
        if name not in flags:
            raise V3PipelineBlocked(
                f"Execution manifest is missing V3 feature flag: {name}",
                "V3_MANIFEST_INCOMPLETE",
            )
        return flags[name]

    def _agent_run_id(self, role: str, attempt: int) -> uuid.UUID:
        return uuid.uuid5(
            self.pipeline_run.id,
            f"editorial-v3:{role}:attempt:{attempt}",
        )
