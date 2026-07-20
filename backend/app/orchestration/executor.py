import asyncio
import copy
import html
import json
import re
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.observability import structured_log
from app.core.sanitization import sanitize_nul
from app.db.models import (
    AgentRun,
    AgentMemory,
    Article,
    ArticleBlock,
    ArticleVersion,
    ClaimEvidence,
    GateDecision,
    ResearchPlan,
    ResearchQuestion,
    SentenceClaim,
    LearningStatus,
    PipelineRun,
    PipelineRunStatus,
    Project,
)
from app.orchestration.graph import EvidenceFirstGraph, PipelineNodes
from app.orchestration.state import PipelineState, Stage
from app.schemas.agents import (
    CuratorOutput,
    EditorOutput,
    FactExtractionOutput,
    ResearchPlanOutput,
    SEOBrief,
    WriterOutput,
    WriterRevisionOutput,
)
from app.services.agent_runtime import AgentRuntime
from app.services.content_similarity import ContentSimilarityService
from app.services.editorial_seal import article_version_checksum
from app.services.research_engine import (
    ResearchEngine,
    SearchDocument,
    SearchProviderError,
    canonicalize_url,
)
from app.services.search_policy import (
    BRAZIL,
    MIN_FACT_SOURCE_RELIABILITY,
    MAX_DOCUMENTS_PER_QUESTION,
    MAX_EXTRACTION_CHARS_PER_DOCUMENT,
    SEARCH_RESULTS_PER_MARKET,
    market_search_plan,
    merge_market_results,
)
from app.services.skill_registry import SkillRegistry
from app.services.skill_learning import (
    PipelineOutcomeSignals,
    SkillLearningInputError,
    SkillLearningService,
)
from app.services.content_versioning import ContentVersionService
from app.services.pipeline_control import (
    CheckpointService,
    EventContext,
    PipelineCancellationRequested,
    PipelineRunService,
)
from app.services.handoffs import HandoffService
from app.services.human_editorial_review import HumanEditorialReviewService
from app.services.editorial_hierarchy import (
    EditorialHierarchyGate,
    UniversalEditorialHierarchyBuilder,
)
from app.schemas.editorial_hierarchy import EditorialHierarchyContract
from app.services.execution_manifest import (
    ExecutionManifestService,
    pinned_default_definitions,
)
from app.services.research_ledger import ResearchLedgerService
from app.services.research_coverage import ResearchCoverageService
from app.services.quality_evaluator import (
    QualityEvaluator,
    editorial_naturalness_metrics,
)
from app.services.llm_gateway import ProviderError


_VISIBLE_EDITORIAL_META = re.compile(
    r"(?i)(?:\bsegundo\s+(?:as?\s+)?fontes?\b|"
    r"\bfontes?\s+aprovadas?\b|\bfatos?\s+aprovados?\b|"
    r"\bevidências?\s+aprovadas?\b|\bconsulte\s+(?:as?\s+)?"
    r"(?:fontes?|referências?)\b|\beste\s+(?:artigo|guia|texto)\s+"
    r"(?:se\s+baseia|foi\s+baseado)\b|^\s*resposta\s+direta\s*:|"
    r"\ba\s+seguir,\s*(?:(?:eu|nós)\s+)?"
    r"(?:explico|explicamos|mostro|mostramos|apresento|apresentamos|"
    r"detalho|detalhamos|veremos)\b|"
    r"\b(?:neste|nesse)\s+(?:artigo|guia|texto|conteúdo)\b|"
    r"\bao\s+longo\s+(?:deste|desse)\s+(?:artigo|guia|texto|conteúdo)\b|"
    r"\bvamos\s+(?:ver|entender|explorar|analisar|descobrir)\b)"
)
_GENERIC_TEMPLATE_LANGUAGE = re.compile(
    r"(?i)(?:\beste\s+guia\s+reúne\b|"
    r"\bo\s+conteúdo\s+foi\s+organizado\b|"
    r"\buse\s+os\s+pontos\s+apresentados\s+como\s+base\b|"
    r"\boutro\s+ponto\s+verificado\s+é\s+que\b|"
    r"\bsíntese\s+prática\b|"
    r"\bpara\s+decidir\s+o\s+próximo\s+passo\b|"
    r"\ba\s+sequência\s+essencial\s+é\s+clara\b)"
)


class PipelineExecutor:
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
        self.research = ResearchEngine()
        self.skills = SkillRegistry(settings.skills_path)
        self.checkpoints = CheckpointService(db)
        self.run_service = PipelineRunService(db)
        self.versions = ContentVersionService(db)
        self.handoffs = HandoffService(db)
        self.ledger = ResearchLedgerService(db, project.id, pipeline_run.id)
        self._stage_context: EventContext | None = None
        self.execution_manifest: dict | None = None

    async def execute(self) -> PipelineState:
        loaded_manifest = await ExecutionManifestService(self.db).required(
            self.pipeline_run.id
        )
        self.execution_manifest = loaded_manifest.data
        self.runtime.bind_execution_manifest(loaded_manifest)
        self.skills = SkillRegistry(
            definitions=pinned_default_definitions(loaded_manifest.data)
        )
        await self._cancellation_boundary()
        graph = EvidenceFirstGraph(
            PipelineNodes(
                planner=self.planner,
                researcher=self.researcher,
                research_gatekeeper=self.research_gatekeeper,
                writer=self.writer,
                editor=self.editor,
                finalizer=self.finalizer,
                quality_gate=self.quality_gate,
                skill_curator=self.skill_curator,
            ),
            after_transition=self._checkpoint,
            max_research_cycles=int(self._flag("max_research_cycles")),
            max_editor_cycles=int(self._flag("max_editor_cycles")),
        )
        checkpoint = await self.checkpoints.latest(self.pipeline_run.id)
        state = (
            PipelineState.model_validate(checkpoint.state_json)
            if checkpoint
            else PipelineState(
                project_id=self.project.id, pipeline_run_id=self.pipeline_run.id
            )
        )
        if checkpoint:
            resume_context = EventContext.for_stage(
                self.pipeline_run.id,
                state.stage.value,
                state.research_cycle,
                state.editor_cycle,
                self.pipeline_run.attempt,
            ).with_checkpoint(checkpoint.sequence)
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "stage.resumed",
                state.stage.value,
                {
                    "checkpoint_id": str(checkpoint.id),
                    "checkpoint_sequence": checkpoint.sequence,
                },
                idempotency_key=resume_context.event_key("stage.resumed"),
                context=resume_context,
            )
            await self.db.commit()
        state = await graph.run(state)
        if state.stage == Stage.completed:
            await HumanEditorialReviewService(self.db).ensure_pending(
                self.project, self.pipeline_run
            )
            self.pipeline_run = await self.run_service.transition(
                self.pipeline_run.id,
                PipelineRunStatus.needs_human_approval,
                origin="orchestrator",
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
                    "message": (
                        "Pacote de revisão aprovado pelo gate automático; "
                        "a aprovação do editor-chefe humano permanece obrigatória."
                    )
                },
                idempotency_key="pipeline.needs_human_approval",
                context=self._stage_context,
            )
        elif state.stage == Stage.needs_review:
            self.pipeline_run = await self.run_service.transition(
                self.pipeline_run.id,
                PipelineRunStatus.needs_review,
                origin="orchestrator",
                reason=state.blocking_reason,
                stage=state.stage.value,
                expected_lease_owner=self.lease_owner,
                expected_lock_version=self.pipeline_run.lock_version,
            )
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "pipeline.needs_review",
                "planner",
                {"message": state.blocking_reason or "Human review required"},
                idempotency_key="pipeline.needs_review",
                context=self._stage_context,
            )
        else:
            self.pipeline_run = await self.run_service.transition(
                self.pipeline_run.id,
                PipelineRunStatus.blocked,
                origin="orchestrator",
                reason=state.blocking_reason,
                error_code=state.blocking_code,
                stage=state.stage.value,
                expected_lease_owner=self.lease_owner,
                expected_lock_version=self.pipeline_run.lock_version,
            )
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "pipeline.blocked",
                "blocked",
                {
                    "message": state.blocking_reason or "Pipeline blocked",
                    "error_code": state.blocking_code,
                },
                idempotency_key="pipeline.blocked",
                context=self._stage_context,
            )
        await self.db.commit()
        return state

    async def _checkpoint(self, completed_stage: str, state: PipelineState) -> None:
        context = self._stage_context
        checkpoint = await self.checkpoints.save(
            self.pipeline_run,
            completed_stage,
            state.stage.value,
            state.model_dump(mode="json"),
            result={
                "blocking_reason": state.blocking_reason,
                "blocking_code": state.blocking_code,
            },
            resumable=state.stage
            not in {Stage.blocked, Stage.needs_review, Stage.completed},
            event_context=context,
        )
        if context:
            completed_context = context.with_checkpoint(checkpoint.sequence)
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
                idempotency_key=completed_context.event_key("stage.completed"),
                context=completed_context,
            )
        self.pipeline_run = await self.run_service.renew_lease(
            self.pipeline_run.id, self.lease_owner
        )
        await self.db.commit()
        await self._cancellation_boundary()

    async def _stage(self, stage: str, message: str, state: PipelineState) -> None:
        await self._cancellation_boundary()
        self.pipeline_run.current_stage = stage
        self.project.current_stage = stage
        self.project.status = "running"
        self._stage_context = EventContext.for_stage(
            self.pipeline_run.id,
            stage,
            state.research_cycle,
            state.editor_cycle,
            self.pipeline_run.attempt,
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "stage.started",
            stage,
            {"message": message},
            idempotency_key=self._stage_context.event_key("stage.started"),
            context=self._stage_context,
        )
        await self.db.commit()

    async def _cancellation_boundary(self) -> None:
        with self.db.no_autoflush:
            snapshot = (
                await self.db.execute(
                    select(
                        PipelineRun.status,
                        PipelineRun.cancellation_requested_at,
                    ).where(PipelineRun.id == self.pipeline_run.id)
                )
            ).one_or_none()
        if snapshot is None:
            raise ValueError("Pipeline run not found")
        status, cancellation_requested_at = snapshot
        if (
            PipelineRunStatus(status) != PipelineRunStatus.cancelled
            and cancellation_requested_at is None
        ):
            return

        await self.db.rollback()
        cancelled = await self.run_service.honor_cancellation(
            self.pipeline_run.id,
            origin="orchestrator.safe-boundary",
            expected_lease_owner=self.lease_owner,
        )
        if cancelled is not None:
            self.pipeline_run = cancelled
            self.project = await self.db.get(Project, cancelled.project_id)
            await self.db.commit()
        raise PipelineCancellationRequested(str(self.pipeline_run.id))

    async def _handoff(
        self,
        from_role: str,
        to_role: str,
        payload: dict,
        fact_ids: list[str] | None = None,
        confidence_score: float = 1,
        *,
        state: PipelineState,
        producer_agent_run_id: uuid.UUID | None = None,
        research_cycle: int | None = None,
        editor_cycle: int | None = None,
    ) -> None:
        await self.handoffs.persist(
            self.project.id,
            self.pipeline_run,
            from_role,
            to_role,
            payload,
            fact_ids,
            confidence_score,
            research_cycle=(
                state.research_cycle if research_cycle is None else research_cycle
            ),
            editor_cycle=state.editor_cycle if editor_cycle is None else editor_cycle,
            producer_agent_run_id=producer_agent_run_id,
            event_context=self._stage_context,
        )

    def _context(self, run_id: uuid.UUID) -> dict:
        manifest = self.execution_manifest or {}
        learned = manifest.get("learned_skills", {})
        context = {
            "project_id": str(self.project.id),
            "run_id": str(self.pipeline_run.id),
            "agent_run_id": str(run_id),
            "language": self.project.language,
            "active_skill_versions": {
                "default": {
                    item["definition"]["skill_id"]: item["definition"]["version"]
                    for item in manifest.get("default_skills", [])
                },
                "learned": {
                    role: [
                        f"{item['skill_id']}@{item['version']}"
                        for item in entry.get("skills", [])
                    ]
                    for role, entry in learned.items()
                },
            },
            "execution_manifest_checksum": (
                self.runtime.execution_manifest.checksum
                if self.runtime.execution_manifest is not None
                else None
            ),
        }
        revision = (self.pipeline_run.metadata_json or {}).get("human_revision")
        if isinstance(revision, dict):
            context["human_revision"] = revision
        return context

    def _flag(self, name: str):
        if self.execution_manifest is None:
            raise ValueError("Execution manifest has not been loaded")
        try:
            return self.execution_manifest["feature_flags"][name]
        except KeyError as exc:
            raise ValueError(
                f"Execution manifest feature flag is missing: {name}"
            ) from exc

    def _revision_prompt(self, prompt: str) -> str:
        revision = (self.pipeline_run.metadata_json or {}).get("human_revision")
        if (
            not isinstance(revision, dict)
            or not str(revision.get("instructions", "")).strip()
        ):
            return prompt
        return (
            "<human_editor_revision>\n"
            "Instruções explícitas do editor-chefe humano. Elas orientam a revisão, "
            "mas não são evidência factual e não podem relaxar os gates de cobertura, "
            "fontes ou fidelidade.\n"
            f"Revisor: {revision.get('reviewer')}\n"
            f"Instruções: {revision.get('instructions')}\n"
            "</human_editor_revision>\n\n" + prompt
        )

    def _editorial_context(self) -> dict:
        context = (getattr(self, "execution_manifest", None) or {}).get(
            "editorial_context"
        ) or {}
        return {
            "publication_profile": context.get("publication_profile"),
            "content_brief": context.get("content_brief") or {},
        }

    @staticmethod
    def _state_hierarchy(state: PipelineState) -> EditorialHierarchyContract | None:
        raw = (state.plan or {}).get("hierarchy")
        if not raw:
            return None
        return EditorialHierarchyContract.model_validate(raw)

    async def planner(self, state: PipelineState) -> PipelineState:
        await self._stage("planner", "Planejamento da pesquisa iniciado", state)
        existing_plan = await self.db.scalar(
            select(ResearchPlan).where(
                ResearchPlan.pipeline_run_id == self.pipeline_run.id,
                ResearchPlan.idempotency_key == "planner",
            )
        )
        if existing_plan:
            questions = (
                await self.db.scalars(
                    select(ResearchQuestion)
                    .where(ResearchQuestion.plan_id == existing_plan.id)
                    .order_by(ResearchQuestion.priority, ResearchQuestion.created_at)
                )
            ).all()
            output = {
                "rationale": existing_plan.rationale,
                "semantic_keywords": existing_plan.semantic_keywords,
                "hierarchy": (getattr(existing_plan, "hierarchy_json", None) or UniversalEditorialHierarchyBuilder.from_project(self.project).model_dump(mode="json")),
                "competitor_angles": existing_plan.competitor_angles,
                "content_gaps": existing_plan.content_gaps,
                "seo_brief": getattr(existing_plan, "seo_brief", None) or {},
                "editorial_blueprint": (
                    getattr(existing_plan, "editorial_blueprint", None) or {}
                ),
                "questions": [
                    {
                        "id": str(question.id),
                        "question": question.question,
                        "priority": question.priority,
                        "importance": getattr(question, "importance", "core"),
                        "rationale": getattr(question, "rationale", "")
                        or ("Necessária para sustentar o escopo editorial."),
                        "node_ids": list(getattr(question, "node_ids", None) or []),
                        "expected_source_types": question.expected_source_types,
                        "semantic_terms": question.semantic_terms,
                        "search_queries": question.search_queries,
                    }
                    for question in questions
                ],
            }
            state.plan = output
            return await self._finish_planner(state, output)
        hierarchy = UniversalEditorialHierarchyBuilder.from_project(self.project)
        run_id = self._agent_run_id("planner", 1)
        payload = {
            "context": self._context(run_id),
            "topic": self.project.topic,
            "search_intent": self.project.search_intent,
            "audience": self.project.audience,
            "niche": self.project.niche,
            "editorial_context": self._editorial_context(),
            "hierarchy": (
                hierarchy.model_dump(mode="json") if hierarchy is not None else None
            ),
        }
        prompt = f"""
Você é o planejador de uma pesquisa editorial SEO baseada em evidências.
Idioma: {self.project.language}
Tópico: {self.project.topic}
Intenção: {self.project.search_intent}
Público: {self.project.audience}
Nicho: {self.project.niche or "não informado"}
LACUNAS SINALIZADAS PELO WRITER: {json.dumps((state.editorial_review or {}).get("writer_quality_gaps", []), ensure_ascii=False)}
CONTEXTO EDITORIAL FIXADO: {json.dumps(self._editorial_context(), ensure_ascii=False)}
HIERARQUIA EDITORIAL DETERMINÍSTICA: {json.dumps(hierarchy.model_dump(mode="json"), ensure_ascii=False)}

A hierarquia acima já foi decidida pelo sistema antes da pesquisa. Não invente, remova,
renomeie ou reordene nós. A hierarquia não deve ser reproduzida na saída; cada pergunta
deve preencher node_ids com um ou mais IDs válidos da hierarquia, e cada seção do
editorial_blueprint também deve declarar node_ids. Todos os nós required e factuais
devem estar cobertos por perguntas; todos os nós required devem estar cobertos pelo
blueprint na ordem de sequence. Nós conditional só entram quando a pergunta investigar
sua aplicabilidade; o fechamento nunca cria uma pergunta factual artificial.

Crie de 3 a 16 perguntas verificáveis para uso INTERNO da pesquisa. A quantidade
é adaptativa: use o menor conjunto que cubra a promessa central, decisões do leitor,
riscos e contexto indispensável. Elas não serão títulos do artigo. Marque cada
pergunta como core, supporting ou optional. Toda pergunta core deve ser necessária
para cumprir a promessa central; supporting aprofunda a decisão; optional adiciona
contexto sem bloquear a redação. Explique a função de cada pergunta em rationale.
Em conjunto, cubra todos os pontos explicitamente pedidos no tópico, agrupando
aspectos relacionados para evitar pesquisa redundante.
Priorize fontes primárias, órgãos públicos, universidades e literatura científica
quando apropriado. Não escreva o artigo nem antecipe conclusões.
Primeiro identifique a entrega central prometida ao leitor. A pergunta de prioridade
1 deve buscar a evidência necessária para cumprir exatamente essa entrega — não
apenas definir o tema. Em conteúdos "como fazer", cubra a sequência executável,
os critérios observáveis de decisão e os cuidados que mudam o resultado. Contexto,
armazenamento, histórico e curiosidades só merecem pergunta própria quando forem
necessários ao objetivo; nunca podem deslocar o procedimento principal.

Para cada pergunta, gere consultas localizadas em search_queries:
- united_states: consulta em inglês para fontes dos Estados Unidos;
- spain: consulta em espanhol para fontes da Espanha;
- switzerland: consulta adequada a fontes da Suíça, preferencialmente em alemão,
  francês ou inglês;
- brazil: consulta em português somente quando o tópico ou a própria pergunta
  mencionar explicitamente Brasil ou contexto brasileiro; caso contrário, null.

O idioma e o país do público não limitam a origem da pesquisa. Para conteúdo
destinado ao público brasileiro, mantenha a base factual internacional por padrão.
Além do plano de pesquisa, produza editorial_blueprint com a decisão que o leitor
precisa tomar, promessa central, tese, estratégia de abertura e conclusão e de 3 a
14 seções. Para cada seção, defina sua função, os temas de claims, transição e meta
de palavras. A arquitetura deve formar um raciocínio progressivo, não uma lista de
respostas às perguntas internas.

Use o perfil e o briefing para entender voz, maturidade do leitor, objetivo,
palavra-chave, oferta e ação desejada. Eles orientam a estratégia, mas NÃO são
evidência factual. Inclua no plano o conhecimento necessário para sustentar a
necessidade do leitor e a ligação útil com a oferta, sem transformar o artigo em
propaganda e sem presumir alegações da marca como verdade.

{self.skills.prompt_fragment("planner")}
"""
        await self._cancellation_boundary()
        output = await self.runtime.call(
            self.project.id,
            "planner",
            run_id,
            payload,
            self._revision_prompt(prompt),
            ResearchPlanOutput,
            pipeline_run_id=self.pipeline_run.id,
            event_context=self._stage_context,
        )
        await self._cancellation_boundary()
        output["hierarchy"] = hierarchy.model_dump(mode="json")
        plan_hierarchy_report = EditorialHierarchyGate.validate_plan(output, hierarchy)
        if plan_hierarchy_report.blockers:
            raise ValueError(
                "EDITORIAL_HIERARCHY_PLAN_BLOCKED: "
                + "; ".join(plan_hierarchy_report.blockers)
            )
        latest_version = await self.db.scalar(
            select(func.coalesce(func.max(ResearchPlan.version), 0)).where(
                ResearchPlan.project_id == self.project.id
            )
        )
        plan = ResearchPlan(
            project_id=self.project.id,
            pipeline_run_id=self.pipeline_run.id,
            idempotency_key="planner",
            version=latest_version + 1,
            status="approved",
            rationale=output["rationale"],
            semantic_keywords=output["semantic_keywords"],
            competitor_angles=output["competitor_angles"],
            content_gaps=output["content_gaps"],
            editorial_blueprint=output["editorial_blueprint"],
            hierarchy_json=output["hierarchy"],
        )
        self.db.add(plan)
        await self.db.flush()
        for question in output["questions"]:
            row = ResearchQuestion(
                plan_id=plan.id,
                question=question["question"],
                priority=question["priority"],
                importance=question["importance"],
                rationale=question["rationale"],
                node_ids=question.get("node_ids", []),
                expected_source_types=question["expected_source_types"],
                semantic_terms=question.get("semantic_terms", []),
                search_queries=question.get("search_queries", {}),
            )
            self.db.add(row)
            await self.db.flush()
            question["id"] = str(row.id)
        await self.db.commit()
        state.plan = output
        return await self._finish_planner(state, output)

    async def _finish_planner(
        self, state: PipelineState, output: dict
    ) -> PipelineState:
        await self._cancellation_boundary()
        similarity = await ContentSimilarityService(
            self.db,
            embedding_route=self.execution_manifest.get("embedding_route"),
            route_is_fixed=True,
        ).assess(
            self.project,
            ContentSimilarityService.planning_fingerprint(self.project, output),
        )
        await self._cancellation_boundary()
        matches = [
            {
                "article_id": item.article_id,
                "project_id": item.project_id,
                "title": item.title,
                "score": round(item.score, 3),
            }
            for item in similarity
            if item.score >= float(self._flag("content_similarity_warning_threshold"))
        ]
        state.similarity_report = {"matches": matches}
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "content.similarity_checked",
            "planner",
            {"matches": matches},
            idempotency_key="content.similarity_checked",
        )
        if matches and matches[0]["score"] >= float(
            self._flag("content_duplicate_threshold")
        ):
            state.stage = Stage.needs_review
            state.blocking_reason = "Possible duplicate content: " + matches[0]["title"]
            return state
        await self._handoff(
            "planner",
            "researcher",
            {
                "rationale": output["rationale"],
                "question_count": len(output["questions"]),
                "content_gaps": output["content_gaps"],
                "semantic_keywords": output["semantic_keywords"],
            },
            state=state,
            producer_agent_run_id=self._agent_run_id("planner", 1),
        )
        return state

    async def researcher(self, state: PipelineState) -> PipelineState:
        await self._stage("researcher", "Busca e extração de fatos iniciadas", state)
        search_provider, search_key = await self.runtime.search_credential()
        await self._ensure_google_keywords(
            state,
            provider=search_provider,
            api_key=search_key,
        )
        questions = self._research_targets(state)
        # A tentativa identifica o ciclo editorial da mesma pergunta. Somar o
        # índice da pergunta ao ciclo fazia o segundo ciclo colidir com IDs do
        # primeiro e apenas reutilizar a resposta antiga, sem pesquisar de novo.
        cycle_attempt = self._research_attempt(state)
        for question in questions:
            query = self._research_query(state, question)
            attempt = cycle_attempt
            run_id = self._agent_run_id(f"researcher:{question['id']}", attempt)
            documents = await self._cached_research_documents(run_id)
            await self._cancellation_boundary()
            if not documents:
                documents = await self._search_question_markets(
                    state,
                    question,
                    query=query,
                    provider=search_provider,
                    api_key=search_key,
                )
            if not documents:
                continue
            accepted_count = await self._extract_question(
                state,
                question,
                documents,
                attempt=attempt,
                run_id=run_id,
            )
            if accepted_count == 0:
                recovery_run_id = self._agent_run_id(
                    f"researcher:{question['id']}:extraction-recovery",
                    attempt,
                )
                await self.runtime.event(
                    self.project.id,
                    self.pipeline_run.id,
                    "research.extraction_regeneration_started",
                    "researcher",
                    {
                        "message": (
                            "A busca retornou fontes, mas a primeira extração não "
                            "produziu fatos verificáveis. Uma regeneração usando as "
                            "mesmas fontes foi iniciada."
                        ),
                        "question_id": str(question["id"]),
                        "document_count": len(documents),
                        "serper_repeated": False,
                    },
                    idempotency_key=(
                        f"research.extraction_regeneration_started:{recovery_run_id}"
                    ),
                    context=self._stage_context,
                )
                await self._extract_question(
                    state,
                    question,
                    documents,
                    attempt=attempt,
                    run_id=recovery_run_id,
                    recovery=True,
                )
            # A completed question is an idempotent research checkpoint. Making
            # it durable here exposes real progress and lets a worker restart
            # reuse the sources and facts without repeating Serper or OpenAI.
            await self.db.commit()
        await self.db.commit()
        state.facts = await self._all_fact_dicts()
        await self._handoff(
            "researcher",
            "research_gatekeeper",
            {
                "fact_count": len(state.facts),
                "research_cycle": state.research_cycle + 1,
                "questions": [x["question"] for x in state.plan["questions"]],
            },
            [str(x["id"]) for x in state.facts],
            state=state,
            research_cycle=state.research_cycle + 1,
        )
        return state

    async def _ensure_google_keywords(
        self,
        state: PipelineState,
        *,
        provider: str,
        api_key: str,
    ) -> None:
        """Discover target-market queries without turning Brazilian pages into facts."""
        plan = state.plan or {}
        if "google_keywords" in plan and "seo_brief" in plan:
            await self._persist_seo_brief(plan["seo_brief"])
            return
        discover = getattr(self.research, "discover_keywords", None)
        if discover is None:
            plan["google_keywords"] = []
            plan["seo_brief"] = self._build_seo_brief(plan, [])
            await self._persist_seo_brief(plan["seo_brief"])
            return
        run_id = self._agent_run_id("seo_keyword_research", 1)
        existing = await self.db.get(AgentRun, run_id)
        if existing is not None and isinstance(existing.output_json, dict):
            keywords = list(existing.output_json.get("keywords") or [])
            plan["google_keywords"] = keywords
            plan["seo_brief"] = existing.output_json.get(
                "seo_brief"
            ) or self._build_seo_brief(plan, keywords)
            await self._persist_seo_brief(plan["seo_brief"])
            return

        seed_terms = [
            str(value).strip()
            for value in plan.get("semantic_keywords", [])
            if str(value).strip()
        ][:6]
        query = self._keyword_seed_query()
        started_at = datetime.now(timezone.utc)
        error_code = None
        try:
            async with asyncio.timeout(45):
                keywords = await discover(
                    query,
                    provider,
                    api_key,
                    market=BRAZIL,
                    limit=12,
                )
        except Exception as exc:
            keywords = []
            error_code = (
                "keyword_discovery_timeout"
                if isinstance(exc, TimeoutError)
                else getattr(exc, "error_code", "keyword_discovery_unavailable")
            )
        finished_at = datetime.now(timezone.utc)
        seo_brief = self._build_seo_brief(plan, keywords)
        run = AgentRun(
            id=run_id,
            project_id=self.project.id,
            pipeline_run_id=self.pipeline_run.id,
            idempotency_key=f"seo_keyword_research:{run_id}",
            agent_role="seo_keyword_research",
            attempt=1,
            status="succeeded",
            input_json={
                "query": query,
                "market": "br",
                "used_as_factual_source": False,
            },
            output_json={
                "keywords": keywords,
                "fallback_keywords": ([] if keywords else seed_terms),
                "seo_brief": seo_brief,
                "market": "br",
                "source": "google_serper_related_searches",
                "used_as_factual_source": False,
                "error_code": error_code,
            },
            # AgentRun.decision is a PostgreSQL enum shared with the editorial
            # gates.  Persist only valid enum members here; the keyword-specific
            # outcome remains available in output_json without weakening the
            # database contract.
            decision=(
                GateDecision.approved
                if seo_brief.get("focus_keyphrase")
                else GateDecision.insufficient
            ),
            provider=provider,
            model="google-related-searches-v1",
            started_at=started_at,
            finished_at=finished_at,
            latency_ms=max(0, int((finished_at - started_at).total_seconds() * 1000)),
        )
        self.db.add(run)
        await self.db.flush()
        plan["google_keywords"] = keywords
        plan["seo_brief"] = seo_brief
        await self._persist_seo_brief(seo_brief)
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "seo.keyword_discovery_completed",
            "planner",
            {
                "message": (
                    "Consultas relacionadas do Google foram incorporadas ao "
                    "planejamento SEO."
                ),
                "keyword_count": len(keywords),
                "focus_keyphrase": seo_brief["focus_keyphrase"],
                "market": "br",
                "used_as_factual_source": False,
                "error_code": error_code,
            },
            idempotency_key=f"seo.keyword_discovery_completed:{run_id}",
            context=self._stage_context,
        )

    async def _persist_seo_brief(self, seo_brief: dict) -> None:
        scalar = getattr(self.db, "scalar", None)
        if scalar is None:
            return
        plan = await scalar(
            select(ResearchPlan).where(
                ResearchPlan.pipeline_run_id == self.pipeline_run.id,
                ResearchPlan.idempotency_key == "planner",
            )
        )
        if plan is not None:
            plan.seo_brief = SEOBrief.model_validate(seo_brief).model_dump(mode="json")
            await self.db.flush()

    def _build_seo_brief(
        self,
        plan: dict,
        google_keywords: list[str],
    ) -> dict:
        topic = re.sub(
            r"\s+",
            " ",
            str(getattr(self.project, "topic", "") or ""),
        ).strip()
        editorial_context = self._editorial_context()
        content_brief = editorial_context.get("content_brief") or {}
        requested_keyword = re.sub(
            r"\s+",
            " ",
            str(content_brief.get("primary_keyword") or ""),
        ).strip()
        keyword_seed = requested_keyword or self._keyword_seed_query()
        semantic = [
            re.sub(r"\s+", " ", str(value)).strip().rstrip("?")
            for value in plan.get("semantic_keywords", [])
            if str(value).strip()
        ]
        real_queries = [
            re.sub(r"\s+", " ", str(value)).strip().rstrip("?")
            for value in google_keywords
            if str(value).strip()
        ]
        requested_related = [
            re.sub(r"\s+", " ", str(value)).strip().rstrip("?")
            for value in content_brief.get("secondary_keywords", [])
            if str(value).strip()
        ]
        candidates = list(dict.fromkeys(real_queries + requested_related + semantic))
        seed_tokens = self._keyword_tokens(keyword_seed)
        focus_candidates = [
            value
            for value in candidates
            if (
                len(seed_tokens) < 2
                or len(seed_tokens & self._keyword_tokens(value)) >= 2
            )
        ]
        focus = (
            requested_keyword
            if requested_keyword
            else self._best_keyword(keyword_seed, focus_candidates)
        )
        if focus is None:
            focus = (
                keyword_seed
                or topic
                or str(getattr(self.project, "name", "") or "conteúdo").strip()
            )
        focus = self._truncate_at_word(focus.strip().rstrip("?"), 55)

        related = [
            value for value in candidates if value.casefold() != focus.casefold()
        ][:8]
        blueprint_sections = [
            str(section.get("heading_intent") or "").strip()
            for section in (plan.get("editorial_blueprint") or {}).get("sections", [])
            if str(section.get("heading_intent") or "").strip()
        ]
        requested_sections = [
            str(value).strip()
            for value in content_brief.get("required_sections", [])
            if str(value).strip()
        ]
        raw_sections = (
            requested_sections
            + blueprint_sections
            + list(plan.get("content_gaps", []))
            + list(plan.get("competitor_angles", []))
        )
        recommended_sections = list(
            dict.fromkeys(
                heading
                for value in raw_sections
                if (heading := self._editorial_heading(str(value)))
            )
        )[:7]
        defaults = [
            f"O que realmente importa em {focus}",
            "Condições essenciais",
            "Etapas do processo",
            "Erros comuns",
            "Cuidados durante o processo",
        ]
        configured_minimum_h2 = int(
            content_brief.get("minimum_h2") or settings.quality_min_h2_count
        )
        configured_minimum_h2 = max(3, min(8, configured_minimum_h2))
        for heading in defaults:
            if len(recommended_sections) >= configured_minimum_h2:
                break
            if heading.casefold() not in {
                value.casefold() for value in recommended_sections
            }:
                recommended_sections.append(heading)

        requested_angle = re.sub(
            r"\s+",
            " ",
            str(
                content_brief.get("content_objective")
                or content_brief.get("reader_goal")
                or ""
            ),
        ).strip()
        article_angle = requested_angle or next(
            (
                re.sub(r"\s+", " ", str(value)).strip()
                for value in plan.get("content_gaps", [])
                if str(value).strip()
            ),
            f"Guia prático e completo para {getattr(self.project, 'audience', 'leitores')}",
        )
        configured_intent = getattr(self.project, "search_intent", "informational")
        search_intent = getattr(
            configured_intent,
            "value",
            configured_intent,
        )
        return SEOBrief.model_validate(
            {
                "focus_keyphrase": focus,
                "related_keyphrases": related,
                "search_intent": search_intent,
                "article_angle": self._truncate_at_word(article_angle, 180),
                "recommended_sections": recommended_sections[:8],
                "minimum_words": int(
                    content_brief.get("minimum_words")
                    or settings.quality_min_word_count
                ),
                "maximum_words": max(
                    int(
                        content_brief.get("minimum_words")
                        or settings.quality_min_word_count
                    ),
                    int(
                        content_brief.get("maximum_words")
                        or settings.quality_max_word_count
                    ),
                ),
                "minimum_h2": configured_minimum_h2,
                "minimum_h3": int(
                    content_brief.get("minimum_h3")
                    if content_brief.get("minimum_h3") is not None
                    else settings.quality_min_h3_count
                ),
            }
        ).model_dump(mode="json")

    def _keyword_seed_query(self) -> str:
        """Return the concise primary subject, excluding secondary instructions."""
        requested_keyword = str(
            (self._editorial_context().get("content_brief") or {}).get(
                "primary_keyword"
            )
            or ""
        ).strip()
        if requested_keyword:
            return self._truncate_at_word(requested_keyword, 90)
        topic = re.sub(
            r"\s+",
            " ",
            str(getattr(self.project, "topic", "") or ""),
        ).strip()
        topic_words = self._keyword_tokens(topic)
        if topic and len(topic) <= 90 and 2 <= len(topic_words) <= 10:
            return topic.rstrip("?.!:;")

        name = re.sub(
            r"\s+",
            " ",
            str(getattr(self.project, "name", "") or ""),
        ).strip()
        name = re.sub(
            r"(?i)^(?:guia|artigo|conteúdo|post)(?:\s+completo)?"
            r"(?:\s+(?:de|sobre|para))?\s+",
            "",
            name,
        ).strip()
        if name:
            return name.rstrip("?.!:;")

        primary_clause = re.split(
            r"[.;]|\b(?:incluindo|também deve)\b",
            topic,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        return self._truncate_at_word(primary_clause.strip(), 90)

    async def _search_question_markets(
        self,
        state: PipelineState,
        question: dict,
        *,
        query: str,
        provider: str,
        api_key: str,
    ) -> list[SearchDocument]:
        localized_queries = await self._localized_queries_for_cycle(
            state,
            question,
            fallback_query=query,
        )
        searches = market_search_plan(
            topic=self.project.topic,
            question=question["question"],
            fallback_query=query,
            localized_queries=localized_queries,
        )

        async def bounded_search(search):
            try:
                async with asyncio.timeout(45):
                    return await self.research.search(
                        search.query,
                        provider,
                        api_key,
                        max_results=SEARCH_RESULTS_PER_MARKET,
                        market=search.market,
                        exclude_brazil=search.exclude_brazil,
                    )
            except TimeoutError as exc:
                raise SearchProviderError(
                    "timeout",
                    provider=provider,
                    model="search",
                    retryable=True,
                    error_code="search_timeout",
                ) from exc

        results = await asyncio.gather(
            *(bounded_search(search) for search in searches),
            return_exceptions=True,
        )
        groups: list[list[SearchDocument]] = []
        failures: list[Exception] = []
        for search, result in zip(searches, results, strict=True):
            if isinstance(result, Exception):
                failures.append(result)
                await self.runtime.event(
                    self.project.id,
                    self.pipeline_run.id,
                    "research.market_search_failed",
                    "researcher",
                    {
                        "message": (
                            "A pesquisa de um mercado internacional não pôde ser "
                            "concluída."
                        ),
                        "market": search.market.code,
                        "error_code": getattr(
                            result, "error_code", "search_market_failed"
                        ),
                        "retryable": bool(getattr(result, "retryable", False)),
                    },
                    idempotency_key=(
                        "research.market_search_failed:"
                        f"{question['id']}:{state.research_cycle + 1}:"
                        f"{search.market.code}"
                    ),
                    context=self._stage_context,
                )
                continue
            groups.append(result)
        if not groups and failures:
            raise failures[0]
        documents = merge_market_results(
            groups,
            limit=MAX_DOCUMENTS_PER_QUESTION,
        )
        documents, source_policy = self._apply_source_policy(documents)
        if source_policy["rejected_count"]:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "research.source_policy_applied",
                "researcher",
                {
                    "message": "Fontes incompatíveis com o briefing foram removidas.",
                    "question_id": str(question["id"]),
                    "rejected_count": source_policy["rejected_count"],
                    "prohibited_count": source_policy["prohibited_count"],
                    "stale_count": source_policy["stale_count"],
                    "status": "filtered",
                },
                idempotency_key=(
                    "research.source_policy_applied:"
                    f"{question['id']}:{state.research_cycle + 1}"
                ),
                context=self._stage_context,
            )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "research.market_search_completed",
            "researcher",
            {
                "message": "Pesquisa internacional concluída para esta pergunta.",
                "question_id": str(question["id"]),
                "markets_requested": [search.market.code for search in searches],
                "markets_with_results": sorted(
                    {
                        document.search_market
                        for document in documents
                        if document.search_market
                    }
                ),
                "document_count": len(documents),
                "brazil_context_explicit": any(
                    search.market.code == "br" for search in searches
                ),
            },
            idempotency_key=(
                "research.market_search_completed:"
                f"{question['id']}:{state.research_cycle + 1}"
            ),
            context=self._stage_context,
        )
        return documents

    @staticmethod
    def _source_domain(value: object) -> str:
        raw = str(value or "").strip().casefold()
        if not raw:
            return ""
        parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
        domain = (parsed.hostname or "").removeprefix("www.")
        return domain.strip(".")

    def _source_policy_payload(self) -> dict:
        brief = self._editorial_context().get("content_brief") or {}
        return {
            "preferred_sources": list(brief.get("preferred_sources", [])),
            "prohibited_sources": list(brief.get("prohibited_sources", [])),
            "maximum_source_age_days": brief.get("maximum_source_age_days"),
        }

    def _apply_source_policy(
        self, documents: list[SearchDocument]
    ) -> tuple[list[SearchDocument], dict[str, int]]:
        """Apply explicit editorial source constraints before paid extraction."""
        policy = self._source_policy_payload()
        prohibited = {
            domain
            for value in policy["prohibited_sources"]
            if (domain := self._source_domain(value))
        }
        preferred = {
            domain
            for value in policy["preferred_sources"]
            if (domain := self._source_domain(value))
        }
        maximum_age = policy.get("maximum_source_age_days")
        now = datetime.now(timezone.utc)
        accepted: list[SearchDocument] = []
        prohibited_count = 0
        stale_count = 0
        for document in documents:
            domain = self._source_domain(document.url)
            if any(
                domain == item or domain.endswith(f".{item}") for item in prohibited
            ):
                prohibited_count += 1
                continue
            if maximum_age and document.published_at is not None:
                published_at = document.published_at
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                else:
                    published_at = published_at.astimezone(timezone.utc)
                age_days = (now - published_at).days
                if age_days > int(maximum_age):
                    stale_count += 1
                    continue
            accepted.append(document)
        accepted.sort(
            key=lambda document: (
                not any(
                    self._source_domain(document.url) == item
                    or self._source_domain(document.url).endswith(f".{item}")
                    for item in preferred
                ),
                -float(document.reliability_score),
            )
        )
        return accepted[:MAX_DOCUMENTS_PER_QUESTION], {
            "rejected_count": prohibited_count + stale_count,
            "prohibited_count": prohibited_count,
            "stale_count": stale_count,
        }

    async def _localized_queries_for_cycle(
        self,
        state: PipelineState,
        question: dict,
        *,
        fallback_query: str,
    ) -> dict[str, str]:
        """Make every editorial cycle discover genuinely new international sources."""
        configured = {
            str(key): str(value).strip()
            for key, value in (question.get("search_queries") or {}).items()
            if value and str(value).strip()
        }
        if state.research_cycle == 0:
            return configured

        prior_domains: set[str] = set()
        for prior_attempt in range(1, state.research_cycle + 1):
            prior_run_id = self._agent_run_id(
                f"researcher:{question['id']}", prior_attempt
            )
            prior_run = await self.db.get(AgentRun, prior_run_id)
            sources = (
                (prior_run.input_json or {}).get("sources")
                if prior_run is not None
                else None
            )
            for source in sources if isinstance(sources, list) else []:
                if not isinstance(source, dict):
                    continue
                domain = (urlsplit(str(source.get("url") or "")).hostname or "").lower()
                if domain:
                    prior_domains.add(domain)

        exclusions = " ".join(f"-site:{domain}" for domain in sorted(prior_domains)[:8])
        widening = {
            "united_states": "alternative methods techniques comparison guide",
            "spain": "métodos técnicas comparación guía alternativas",
            "switzerland": "Methoden Techniken Vergleich Anleitung Alternativen",
            "brazil": "métodos técnicas comparação guia alternativas",
        }
        keys = set(configured) | set(widening)
        return {
            key: " ".join(
                part
                for part in (
                    configured.get(key) or fallback_query,
                    widening.get(key, "independent sources evidence"),
                    exclusions,
                )
                if part
            )[:1600]
            for key in keys
        }

    async def _extract_question(
        self,
        state: PipelineState,
        question: dict,
        documents: list[SearchDocument],
        attempt: int,
        run_id: uuid.UUID | None = None,
        recovery: bool = False,
    ) -> int:
        run_id = run_id or self._agent_run_id(f"researcher:{question['id']}", attempt)
        source_payload = [document.as_payload() for document in documents]
        prompt_sources = [
            {
                **source,
                "content": str(source["content"])[:MAX_EXTRACTION_CHARS_PER_DOCUMENT],
            }
            for source in source_payload
        ]
        payload = {
            "context": self._context(run_id),
            "topic": self.project.topic,
            "niche": self.project.niche,
            "language": self.project.language,
            "research_question": question["question"],
            "semantic_terms": list(
                dict.fromkeys(
                    question.get("semantic_terms", [])
                    + (state.plan or {}).get("semantic_keywords", [])
                )
            ),
            "gate_instructions": (state.research_audit or {}).get("instructions", []),
            "source_policy": self._source_policy_payload(),
            "claims_to_avoid": list(
                (self._editorial_context().get("content_brief") or {}).get(
                    "claims_to_avoid", []
                )
            ),
            "sources": source_payload,
        }
        await self.runtime.prepare_input(
            project_id=self.project.id,
            role="researcher",
            run_id=run_id,
            input_json=payload,
            attempt=attempt,
            pipeline_run_id=self.pipeline_run.id,
        )
        prompt = f"""
Você é um extrator de fatos para uma pesquisa editorial estritamente delimitada.
Tópico: {self.project.topic}
Nicho: {self.project.niche or "não informado"}
Idioma: {self.project.language}
Pergunta de pesquisa: {question["question"]}

Extraia de 3 a 6 fatos atômicos úteis, quando as fontes permitirem. Cada fato deve:
- responder à pergunta;
- ser explicitamente aplicável ao tópico, entidade e escopo informados;
- escrever claim_text integralmente em {self.project.language}, com gramática
  natural e revisão ortográfica; exact_quote deve permanecer no idioma original,
  copiado literalmente e nunca traduzido;
- apontar para uma URL fornecida;
- conter uma citação EXATA copiada do conteúdo dessa mesma URL;
- ser autocontido e não misturar múltiplas afirmações;
- produzir no máximo 2 fatos por URL;
- quando houver 2 ou mais fontes aplicáveis, usar pelo menos 2 URLs distintas;
- quando houver resultados de 2 ou mais search_market, usar fontes encontradas em
  pelo menos 2 mercados distintos;
- nunca usar como fato uma fonte com reliability abaixo de
  {MIN_FACT_SOURCE_RELIABILITY:.2f}; fóruns, Reddit, Quora e relatos anônimos servem
  apenas para descoberta, não como evidência;
- entre fontes igualmente relevantes, priorizar artigos científicos, órgãos
  públicos e universidades antes de páginas comerciais;
- usar apenas o material abaixo, sem conhecimento externo;
- respeitar integralmente a política de fontes e a lista de claims a evitar do
  briefing; um claim proibido não deve ser extraído nem mesmo quando aparece na fonte.

POLÍTICA DE FONTES: {json.dumps(self._source_policy_payload(), ensure_ascii=False)}
CLAIMS A EVITAR: {
            json.dumps(
                (self._editorial_context().get("content_brief") or {}).get(
                    "claims_to_avoid", []
                ),
                ensure_ascii=False,
            )
        }

search_market identifica o mercado no qual a busca foi executada, não prova o país
de origem do site. source_country só é preenchido quando o domínio permite inferência
segura. Não atribua um país à fonte sem evidência explícita.

Rejeite a fonte e não extraia fatos quando ela tratar de outra espécie, produto,
população ou contexto sem demonstrar aplicabilidade direta ao tópico. Defina
conflict_group somente para afirmações mutuamente incompatíveis no mesmo contexto
(entidade, população, período e método). Faixas compatíveis, recomendações
complementares ou escopos diferentes devem usar conflict_group=null.

INSTRUÇÕES DO ÚLTIMO GATE:
{json.dumps((state.research_audit or {}).get("instructions", []), ensure_ascii=False)}

{
            (
                "REGENERAÇÃO DE EXTRAÇÃO: a busca já retornou conteúdo. Selecione somente "
                "afirmações literais que respondam à pergunta e copie exact_quote sem alterar "
                "pontuação, números ou palavras. Não devolva fatos quando a citação não estiver "
                "presente no conteúdo."
                if recovery
                else ""
            )
        }

FONTES:
{json.dumps(prompt_sources, ensure_ascii=False)}

{self.skills.prompt_fragment("researcher")}
"""
        await self._cancellation_boundary()
        output = await self.runtime.call(
            self.project.id,
            "researcher",
            run_id,
            payload,
            self._revision_prompt(prompt),
            FactExtractionOutput,
            attempt=attempt,
            pipeline_run_id=self.pipeline_run.id,
            event_context=self._stage_context,
        )
        await self._cancellation_boundary()
        by_url = {canonicalize_url(d.url): d for d in documents}
        accepted: list[tuple[dict, SearchDocument]] = []
        count_by_url: Counter[str] = Counter()
        for candidate in output["facts"]:
            url = canonicalize_url(str(candidate["source_url"]))
            document = by_url.get(url) or by_url.get(url.rstrip("/"))
            if document is None or not self._quote_exists(
                candidate["exact_quote"], document.content
            ):
                continue
            # Forums and other low-confidence pages may guide discovery, but
            # they cannot become factual evidence for the article.
            if document.reliability_score < MIN_FACT_SOURCE_RELIABILITY:
                continue
            if count_by_url[url] >= 2:
                continue
            count_by_url[url] += 1
            accepted.append((candidate, document))

        if len(by_url) >= 2 and len(count_by_url) < 2:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "research.question_source_diversity_insufficient",
                "researcher",
                {
                    "message": (
                        "A extração não cobriu duas fontes distintas para a pergunta."
                    ),
                    "source_count": len(count_by_url),
                    "status": "completed_with_limited_diversity",
                    "pipeline_continues": True,
                },
                idempotency_key=f"research.question_source_diversity:{run_id}",
                context=(
                    self._stage_context.with_agent(run_id)
                    if self._stage_context
                    else None
                ),
            )
        available_markets = {
            document.search_market for document in documents if document.search_market
        }
        accepted_markets = {
            document.search_market for _, document in accepted if document.search_market
        }
        if len(available_markets) >= 2 and len(accepted_markets) < 2:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "research.question_market_diversity_insufficient",
                "researcher",
                {
                    "message": (
                        "A extração não cobriu dois mercados internacionais "
                        "distintos para a pergunta."
                    ),
                    "available_markets": sorted(available_markets),
                    "selected_markets": sorted(accepted_markets),
                    "status": "completed_with_limited_diversity",
                    "pipeline_continues": True,
                },
                idempotency_key=f"research.question_market_diversity:{run_id}",
                context=(
                    self._stage_context.with_agent(run_id)
                    if self._stage_context
                    else None
                ),
            )
        for candidate, document in accepted:
            await self.ledger.persist_fact(
                uuid.UUID(question["id"]), document, candidate
            )
        return len(accepted)

    async def _cached_research_documents(
        self, run_id: uuid.UUID
    ) -> list[SearchDocument]:
        run = await self.db.get(AgentRun, run_id)
        sources = (run.input_json or {}).get("sources") if run is not None else None
        if not isinstance(sources, list) or not sources:
            return []
        try:
            documents = [
                SearchDocument.from_payload(source)
                for source in sources
                if isinstance(source, dict)
            ]
        except (KeyError, TypeError, ValueError):
            return []
        if len(documents) != len(sources):
            return []
        structured_log(
            "research.sources_reused",
            project_id=self.project.id,
            pipeline_run_id=self.pipeline_run.id,
            agent_role="researcher",
            stage="researcher",
            source_type="cached",
        )
        return documents

    async def research_gatekeeper(self, state: PipelineState) -> PipelineState:
        await self._stage(
            "research_gatekeeper",
            "Auditoria de cobertura e conflitos iniciada",
            state,
        )
        run_id = self._agent_run_id("research_gatekeeper", state.research_cycle + 1)
        facts = await self._all_fact_dicts()
        payload = {
            "context": self._context(run_id),
            "plan": state.plan,
            "facts": facts,
            "minimum_distinct_sources": int(self._flag("min_distinct_sources")),
        }
        started_at = datetime.now(timezone.utc)
        run = await self.db.get(AgentRun, run_id)
        if run is None:
            run = AgentRun(
                id=run_id,
                project_id=self.project.id,
                pipeline_run_id=self.pipeline_run.id,
                idempotency_key=f"research_gatekeeper:{run_id}",
                agent_role="research_gatekeeper",
                attempt=state.research_cycle,
                status="running",
                input_json=payload,
                provider="deterministic",
                model="evidence-gate-v1",
                started_at=started_at,
            )
            self.db.add(run)
        else:
            run.status = "running"
            run.input_json = payload
            run.started_at = started_at
            run.finished_at = None
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "agent.started",
            "research_gatekeeper",
            {"message": "Validação determinística das evidências iniciada"},
            idempotency_key=f"agent.started:{run_id}",
            context=(
                self._stage_context.with_agent(run_id) if self._stage_context else None
            ),
        )
        await self.db.commit()

        # A extração já validou URL, citação literal e limite por fonte antes de
        # persistir cada fato. O gate não pode anular essas evidências por uma
        # opinião probabilística posterior. Ele apenas resolve conflict_group de
        # forma determinística e mede a cobertura que realmente existe.
        selected: list[dict] = []
        conflicts: dict[str, dict] = {}
        conflict_rejections: list[dict[str, str]] = []
        for fact in facts:
            group = str(fact.get("conflict_group") or "").strip()
            if not group:
                selected.append(fact)
                continue
            current = conflicts.get(group)
            if current is None or float(fact.get("confidence_score") or 0) > float(
                current.get("confidence_score") or 0
            ):
                if current is not None:
                    conflict_rejections.append(
                        {"fact_id": str(current["id"]), "reason_code": "conflict"}
                    )
                conflicts[group] = fact
            else:
                conflict_rejections.append(
                    {"fact_id": str(fact["id"]), "reason_code": "conflict"}
                )
        selected.extend(conflicts.values())
        coverage = await ResearchCoverageService(
            self.db, self.project.id, self.pipeline_run.id
        ).evaluate(
            [fact["id"] for fact in selected],
            minimum_distinct_sources=int(self._flag("min_distinct_sources")),
            minimum_facts_per_question=int(self._flag("min_facts_per_question")),
        )
        approved = coverage.evidence_ready
        coverage.persist(approved=approved, reviewer_run_id=run_id)
        await self.db.flush()
        rejection_reason_counts = (
            {"conflict": len(conflict_rejections)} if conflict_rejections else {}
        )
        output = sanitize_nul(
            {
                "decision": "approved" if approved else "insufficient",
                "coverage_by_question": coverage.coverage_by_question,
                "missing_questions": list(coverage.missing_questions),
                "missing_core_questions": list(coverage.missing_core_questions),
                "missing_supporting_questions": list(
                    coverage.missing_supporting_questions
                ),
                "missing_optional_questions": list(coverage.missing_optional_questions),
                "covered_node_ids": sorted(coverage.covered_node_ids),
                "missing_required_node_ids": list(coverage.missing_required_node_ids),
                "unresolved_conflicts": list(coverage.unresolved_conflicts),
                "unresolved_conflict_fact_ids": (coverage.unresolved_conflict_fact_ids),
                "source_diversity_score": coverage.source_diversity_score,
                "distinct_source_count": coverage.distinct_source_count,
                "minimum_distinct_sources": coverage.minimum_distinct_sources,
                "covered_question_count": len(coverage.covered_question_ids),
                "total_question_count": len(coverage.questions),
                "recommended_fact_count": len(coverage.valid_fact_ids),
                "selected_source_count_by_question": (
                    coverage.selected_source_count_by_question
                ),
                "approved_fact_ids": (
                    [str(fact_id) for fact_id in coverage.valid_fact_ids]
                    if approved
                    else []
                ),
                "fact_rejections": conflict_rejections,
                "rejection_reason_counts": rejection_reason_counts,
                "coverage_complete": coverage.coverage_complete,
                "core_coverage_complete": coverage.core_coverage_complete,
                "partial_coverage": approved and not coverage.coverage_complete,
                "evidence_ready": coverage.evidence_ready,
                "invalid_fact_id_count": len(coverage.invalid_fact_ids),
                "instructions": coverage.next_cycle_instructions(),
            },
            strip_escaped=True,
        )
        finished_at = datetime.now(timezone.utc)
        run.status = "succeeded"
        run.output_json = output
        run.decision = sanitize_nul(output["decision"])
        run.feedback = sanitize_nul(
            {
                "instructions": output.get("instructions", []),
                "rejection_reason_counts": rejection_reason_counts,
            }
        )
        run.finished_at = finished_at
        run.latency_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "agent.completed",
            "research_gatekeeper",
            {
                "message": "Evidências validadas com sucesso",
                "decision": output["decision"],
                "fact_count": len(output["approved_fact_ids"]),
            },
            idempotency_key=f"agent.completed:{run_id}",
            context=(
                self._stage_context.with_agent(run_id) if self._stage_context else None
            ),
        )
        state.research_audit = output
        state.facts = facts
        if output["decision"] == "approved":
            await self._handoff(
                "research_gatekeeper",
                "writer",
                {
                    "decision": "approved",
                    "coverage_by_question": output["coverage_by_question"],
                    "partial_coverage": output["partial_coverage"],
                    "missing_questions": output["missing_questions"],
                    "instructions": output.get("instructions", []),
                },
                [str(x) for x in output["approved_fact_ids"]],
                state=state,
                producer_agent_run_id=run_id,
            )
        else:
            await self._handoff(
                "research_gatekeeper",
                "researcher",
                {
                    "decision": "insufficient",
                    "missing_questions": output.get("missing_questions", []),
                    "unresolved_conflicts": output.get("unresolved_conflicts", []),
                    "rejection_reason_counts": rejection_reason_counts,
                    "instructions": output.get("instructions", []),
                },
                state=state,
                producer_agent_run_id=run_id,
            )
        return state

    def _research_targets(self, state: PipelineState) -> list[dict]:
        questions = list((state.plan or {}).get("questions", []))
        audit = state.research_audit or {}
        if not audit:
            return questions

        missing = {str(value) for value in audit.get("missing_questions", [])}
        conflict_ids = {
            str(fact_id)
            for ids in audit.get("unresolved_conflict_fact_ids", {}).values()
            for fact_id in ids
        }
        conflict_question_ids = {
            str(fact.get("research_question_id"))
            for fact in state.facts
            if str(fact.get("id")) in conflict_ids
        }
        targeted = [
            question
            for question in questions
            if question.get("question") in missing
            or str(question.get("id")) in conflict_question_ids
        ]
        if targeted:
            return targeted

        minimum_sources = int(audit.get("minimum_distinct_sources", 0) or 0)
        distinct_sources = int(audit.get("distinct_source_count", 0) or 0)
        if minimum_sources and distinct_sources < minimum_sources:
            counts = audit.get("selected_source_count_by_question", {})
            ordered = sorted(
                questions,
                key=lambda question: (
                    int(counts.get(str(question.get("id")), 0)),
                    int(question.get("priority", 5)),
                    str(question.get("id", "")),
                ),
            )
            if ordered:
                fewest = int(counts.get(str(ordered[0].get("id")), 0))
                return [
                    question
                    for question in ordered
                    if int(counts.get(str(question.get("id")), 0)) == fewest
                ]
        return questions

    def _research_query(self, state: PipelineState, question: dict) -> str:
        audit = state.research_audit or {}
        semantic_terms = list(
            dict.fromkeys(
                question.get("semantic_terms", [])
                + (state.plan or {}).get("semantic_keywords", [])
            )
        )
        parts = [
            f"Tópico: {self.project.topic}",
            f"Nicho: {self.project.niche or 'geral'}",
            f"Idioma: {self.project.language}",
            f"Pergunta: {question['question']}",
        ]
        if semantic_terms:
            parts.append("Termos semânticos: " + ", ".join(semantic_terms[:12]))
        if audit.get("instructions"):
            parts.append(
                "Instruções do gate: "
                + "; ".join(str(x) for x in audit["instructions"][:6])
            )
        brief = self._editorial_context().get("content_brief") or {}
        prohibited = [
            self._source_domain(value)
            for value in brief.get("prohibited_sources", [])
            if self._source_domain(value)
        ]
        if prohibited:
            parts.append(" ".join(f"-site:{domain}" for domain in prohibited[:12]))
        if state.research_cycle:
            parts.append("fontes primárias dados estudos evidências")
        return " | ".join(parts)[:1600]

    async def writer(self, state: PipelineState) -> PipelineState:
        audit = state.research_audit or {}
        coverage = await ResearchCoverageService(
            self.db, self.project.id, self.pipeline_run.id
        ).evaluate(
            None,
            minimum_distinct_sources=int(self._flag("min_distinct_sources")),
            minimum_facts_per_question=int(self._flag("min_facts_per_question")),
            reported_conflicts=audit.get("unresolved_conflicts", []),
        )
        evidence_ready = bool(
            getattr(
                coverage,
                "evidence_ready",
                getattr(coverage, "coverage_complete", False),
            )
        )
        if audit.get("decision") != "approved" or not evidence_ready:
            raise ValueError("Writer cannot start without validated research evidence")
        await self._stage("writer", "Redação rastreável iniciada", state)
        run_id = self._agent_run_id("writer", state.editor_cycle + 1)
        approved = await self._approved_fact_dicts()
        payload = {
            "context": self._context(run_id),
            "plan": state.plan,
            "approved_facts": approved,
            "rewrite_block_ids": [str(x) for x in state.rewrite_block_ids],
            "editorial_feedback": state.editorial_review or {},
            "editorial_context": self._editorial_context(),
            "hierarchy": (state.plan or {}).get("hierarchy", {}),
        }
        prior = self._prior_draft(state)
        minimum_words = self._minimum_article_words()
        target_words = max(
            minimum_words,
            self._quality_threshold("max_word_count", settings.quality_max_word_count),
        )
        minimum_h2 = max(
            1,
            self._quality_threshold("min_h2_count", settings.quality_min_h2_count),
        )
        minimum_h3 = max(
            0,
            self._quality_threshold("min_h3_count", settings.quality_min_h3_count),
        )
        if prior and state.rewrite_block_ids:
            return await self._writer_targeted_revision(
                state,
                approved,
                prior,
                run_id=run_id,
                minimum_words=minimum_words,
            )
        seo_brief = (state.plan or {}).get("seo_brief") or self._build_seo_brief(
            state.plan or {},
            list((state.plan or {}).get("google_keywords", [])),
        )
        prompt = f"""
Você é o redator. Escreva em {self.project.language} para {self.project.audience}.
Use EXCLUSIVAMENTE os fatos aprovados abaixo para qualquer afirmação verificável.
Toda sentença factual deve citar um ou mais fact_id. Transições, sínteses editoriais
sem alegação verificável e headings conceituais usam is_factual=false e evidence=[];
não invente uma citação para linguagem editorial. O título só usa title_evidence
quando contém número ou alegação factual específica. Não crie números,
recomendações ou conclusões que não estejam no ledger.
Não inclua um bloco h1: o campo title já é o H1.
O resultado é um artigo de blog, não um questionário nem um relatório. Use um
único H1 no campo title, H2 para as seções principais e H3 para desdobramentos.
Não copie as perguntas internas como títulos interrogativos. Inclua a palavra-chave
principal e termos relacionados de forma natural nos H1/H2/H3, sem repetição
artificial. Priorize as consultas reais do Google informadas abaixo.
Todo o texto visível deve estar em {self.project.language}. Nunca copie exact_quote
em inglês, espanhol ou outro idioma para o artigo; expresse apenas o sentido já
registrado em claim_text. Use headings editoriais com até 80 caracteres.
O title deve ter de 15 a 60 caracteres e conter naturalmente, uma única vez, a
focus_keyphrase do SEO BRIEF. Desenvolva o artigo pelo ângulo e pelas seções do
briefing, sintetizando todos os fatos disponíveis; as perguntas são apenas chaves
internas de cobertura e nunca determinam a forma da redação.
Distribua espaço conforme a promessa do título e o objetivo do briefing. O núcleo
da resposta deve aparecer na introdução e receber mais desenvolvimento do que
contexto periférico. Não trate todos os fatos como igualmente importantes só
porque estão no ledger.
A HIERARQUIA EDITORIAL em PLANO é o contrato estrutural obrigatório. Desenvolva os
nós na ordem de sequence, respeite depends_on e faça cada nó transformar o estado
do leitor descrito no contrato. Todo bloco, exceto um eventual H1 convertido pelo
sistema, deve declarar node_ids com um ou mais IDs válidos. covered_node_ids deve
listar todos os nós efetivamente desenvolvidos. Nós core recebem mais profundidade
que nós peripheral; o fechamento só aparece depois de todos os nós required. Um
subtema fora do contrato não ganha seção própria: pode ser mencionado brevemente
ou registrado como candidato a conteúdo interno, salvo quando segurança, conformidade
ou risco exigirem desenvolvimento explícito. Cada parágrafo deve retomar o contexto
necessário, desenvolver uma ideia e preparar a seguinte. Sintetize fatos relacionados
em explicações; não os apresente como verbetes independentes ou respostas coladas.
Não anuncie o que o artigo fará. São proibidas aberturas como "A seguir, explico",
"neste artigo", "vamos ver" e equivalentes. Comece pela resposta ou pela decisão
mais útil. Não siga a ordem dos fact_id nem transforme cada fato em uma frase
independente: agrupe evidências pela relação lógica entre elas e varie, com
intenção, a extensão de frases e parágrafos.
Adapte vocabulário, profundidade, exemplos e ritmo ao perfil editorial e ao
momento do leitor. Conecte a oferta e a ação desejada somente depois de entregar
valor e apenas de forma natural. Informações cadastrais da marca orientam a voz,
mas não substituem fact_id para alegações sobre produto, resultado, saúde ou risco.

Entregue entre {minimum_words} e {target_words} palavras de texto visível. Isso é
um requisito editorial, não uma sugestão. Abra respondendo diretamente à intenção
do leitor; desenvolva cada seção com explicação, contexto e aplicação prática
estritamente sustentados pelos fatos; encerre com uma síntese útil que não introduza
causalidade ou promessa nova. Use no mínimo {minimum_h2} H2. H3 é
{"opcional" if minimum_h3 == 0 else f"exigido somente quando houver ao menos {minimum_h3} desdobramentos reais"};
jamais crie H3 para repetir palavra-chave ou cumprir aparência de estrutura.
Mantenha seções substanciais, use listas apenas quando elas melhorarem a consulta
e não aumente a contagem repetindo a mesma ideia.
Cada fato pode sustentar mais de uma frase somente quando todas permanecerem dentro
do significado literal de claim_text e exact_quote.

O campo evidence é a citação estruturada da frase: não escreva expressões vagas
como "fontes confiáveis" e não é obrigatório mencionar o nome do site no texto.
Não dê créditos visíveis no artigo: não mencione sites, autores, editoras, URLs,
"segundo a fonte" ou expressões equivalentes. A evidência permanece apenas no
ledger interno; a redação e a voz do artigo são autorais.
Também não escreva "fatos aprovados", "fontes aprovadas", "evidências aprovadas",
"este artigo se baseia", "consulte as fontes/referências" ou "Resposta direta:".
Não use frases-modelo como "Este guia reúne", "O conteúdo foi organizado" ou
"Use os pontos apresentados como base", nem títulos como "Síntese prática".
Varie transições e não repita conectores como "Outro ponto verificado é que".
Quando fatos trouxerem faixas numéricas
diferentes para a mesma medida, explique o contexto da variação ou use somente a
faixa sustentada para aquele contexto.
Nunca torne a afirmação mais forte do que claim_text e exact_quote. Preserve
condicionais, incerteza e escopo. Se nenhum fato aprovado sustentar uma parte do
plano, omita essa afirmação em vez de completar com conhecimento externo.
Não diga que uma prática "aumenta as chances", "garante", "evita" ou causa um
resultado se essa relação não estiver explicitamente demonstrada no fato citado.
Durante uma reescrita, corrija, enfraqueça ou remova a frase indicada; não tente
resolver falta de evidência apenas adicionando uma atribuição genérica.

FATOS APROVADOS: {json.dumps(approved, ensure_ascii=False)}
PLANO: {json.dumps(state.plan, ensure_ascii=False)}
SEO BRIEF: {json.dumps(seo_brief, ensure_ascii=False)}
RASCUNHO ANTERIOR: {json.dumps(prior, ensure_ascii=False) if prior else "nenhum"}
FEEDBACK: {json.dumps(state.editorial_review or {}, ensure_ascii=False)}
CONTEÚDOS SEMELHANTES: {json.dumps((state.similarity_report or {}).get("matches", []), ensure_ascii=False)}
CONSULTAS REAIS DO GOOGLE: {json.dumps((state.plan or {}).get("google_keywords", []), ensure_ascii=False)}
CONTEXTO EDITORIAL FIXADO: {json.dumps(self._editorial_context(), ensure_ascii=False)}

Quando houver conteúdo semelhante, preserve a intenção somente se houver um
ângulo, evidência ou decisão do leitor materialmente diferente. Não copie a
estrutura, frases ou conclusões da peça anterior.

{self.skills.prompt_fragment("writer")}
"""
        await self._cancellation_boundary()
        raw_output = await self.runtime.call(
            self.project.id,
            "writer",
            run_id,
            payload,
            self._revision_prompt(prompt),
            WriterOutput,
            attempt=state.editor_cycle + 1,
            pipeline_run_id=self.pipeline_run.id,
            event_context=self._stage_context,
        )
        await self._cancellation_boundary()
        (
            output,
            invalid_fact_ids,
            _evidence_only_used,
            meta_sentences_removed,
        ) = self._normalize_writer_output(state, raw_output, approved)
        producer_run_id = run_id
        output = self._ensure_seo_heading_structure(state, output, approved)
        output["covered_node_ids"] = sorted(
            {
                str(node_id)
                for block in output.get("blocks", [])
                for node_id in block.get("node_ids", [])
                if str(node_id)
            }
        )
        hierarchy = self._state_hierarchy(state)
        hierarchy_report = (
            EditorialHierarchyGate.validate_draft(output, hierarchy)
            if hierarchy is not None
            else None
        )
        if hierarchy_report is not None and hierarchy_report.blockers:
            repair_run_id = self._agent_run_id(
                "writer:hierarchy-repair", state.editor_cycle + 1
            )
            repaired_raw = await self.runtime.call(
                self.project.id,
                "writer",
                repair_run_id,
                {
                    **payload,
                    "draft_to_repair": output,
                    "hierarchy_blockers": list(hierarchy_report.blockers),
                },
                self._revision_prompt(
                    "Reescreva o artigo completo para cumprir a hierarquia editorial "
                    "determinística. Preserve somente fatos e fact_id aprovados. Todo "
                    "bloco deve declarar node_ids válidos; cubra todos os nós required, "
                    "respeite a ordem e as dependências, dê mais profundidade aos nós "
                    "core do que aos peripheral e deixe o fechamento por último. "
                    "Devolva WriterOutput completo. Blockers: "
                    + json.dumps(list(hierarchy_report.blockers), ensure_ascii=False)
                    + "\nHierarquia: "
                    + json.dumps(hierarchy.model_dump(mode="json"), ensure_ascii=False)
                    + "\nRascunho: "
                    + json.dumps(output, ensure_ascii=False)
                ),
                WriterOutput,
                attempt=state.editor_cycle + 2,
                pipeline_run_id=self.pipeline_run.id,
                event_context=self._stage_context,
            )
            (
                output,
                repair_invalid_fact_ids,
                _repair_evidence_only_used,
                repair_meta_removed,
            ) = self._normalize_writer_output(state, repaired_raw, approved)
            invalid_fact_ids.update(repair_invalid_fact_ids)
            meta_sentences_removed += repair_meta_removed
            output = self._ensure_seo_heading_structure(state, output, approved)
            output["covered_node_ids"] = sorted(
                {
                    str(node_id)
                    for block in output.get("blocks", [])
                    for node_id in block.get("node_ids", [])
                    if str(node_id)
                }
            )
            hierarchy_report = EditorialHierarchyGate.validate_draft(output, hierarchy)
            producer_run_id = repair_run_id
        if hierarchy_report is not None and hierarchy_report.blockers:
            raise ValueError(
                "EDITORIAL_HIERARCHY_DRAFT_BLOCKED: "
                + "; ".join(hierarchy_report.blockers)
            )
        remaining_quality_gaps = self._draft_quality_gaps(
            output,
            minimum_words,
            maximum_words=target_words,
            minimum_h2=minimum_h2,
            minimum_h3=minimum_h3,
            state=state,
        )
        remaining_quality_gaps = list(dict.fromkeys(remaining_quality_gaps))
        for position, block in enumerate(output["blocks"]):
            block["position"] = position
            block["block_id"] = str(block.get("block_id") or uuid.uuid4())
        cited = {str(e["fact_id"]) for e in output["title_evidence"]} | {
            str(e["fact_id"])
            for block in output["blocks"]
            for sentence in block["sentences"]
            for e in sentence["evidence"]
        }
        writer_run = await self.db.get(AgentRun, producer_run_id)
        writer_run.output_json = output
        writer_run.feedback = sanitize_nul(
            {
                **(writer_run.feedback or {}),
                "invalid_fact_ids_removed": sorted(invalid_fact_ids),
                "full_regeneration_used": False,
                "visible_editorial_meta_sentences_removed": (meta_sentences_removed),
                "remaining_quality_gaps": remaining_quality_gaps,
            }
        )
        if meta_sentences_removed:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "writer.visible_editorial_meta_removed",
                "writer",
                {
                    "message": (
                        "Frases sobre fontes ou processo editorial foram removidas "
                        "do texto visível antes da revisão."
                    ),
                    "status": "corrected",
                    "sentence_count": meta_sentences_removed,
                },
                idempotency_key=(
                    f"writer.visible_editorial_meta_removed:{producer_run_id}"
                ),
                context=(
                    self._stage_context.with_agent(producer_run_id)
                    if self._stage_context
                    else None
                ),
            )
        if invalid_fact_ids:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "writer.invalid_evidence_removed",
                "writer",
                {
                    "message": (
                        "Referências inexistentes foram removidas antes de "
                        "persistir o artigo."
                    ),
                    "reason": "fact_id_not_in_approved_ledger",
                    "fact_count": len(invalid_fact_ids),
                    "status": "corrected",
                    "pipeline_continues": True,
                },
                idempotency_key=(f"writer.invalid_evidence_removed:{producer_run_id}"),
                context=(
                    self._stage_context.with_agent(producer_run_id)
                    if self._stage_context
                    else None
                ),
            )
        await self.versions.persist_draft(
            self.project,
            self.pipeline_run,
            output,
            producer_run_id,
            {uuid.UUID(str(value)) for value in state.rewrite_block_ids},
        )
        state.draft = output
        state.editorial_review = {
            "writer_quality_gaps": remaining_quality_gaps,
            "full_regeneration_used": False,
        }
        if remaining_quality_gaps:
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "writer.quality_advisories_detected",
                "writer",
                {
                    "message": (
                        "O rascunho seguirá para revisão substantiva com lacunas "
                        "editoriais localizadas; nenhuma regeneração integral foi feita."
                    ),
                    "quality_gaps": remaining_quality_gaps,
                },
                idempotency_key=(
                    f"writer.quality_advisories_detected:{producer_run_id}"
                ),
                context=(
                    self._stage_context.with_agent(producer_run_id)
                    if self._stage_context
                    else None
                ),
            )
        await self._handoff(
            "writer",
            "editor",
            {
                "title": output["title"],
                "block_ids": [x["block_id"] for x in output["blocks"]],
                "editor_cycle": state.editor_cycle + 1,
            },
            sorted(cited),
            state=state,
            producer_agent_run_id=producer_run_id,
            editor_cycle=state.editor_cycle + 1,
        )
        return state

    async def _writer_targeted_revision(
        self,
        state: PipelineState,
        approved: list[dict],
        prior: dict,
        *,
        run_id: uuid.UUID,
        minimum_words: int,
    ) -> PipelineState:
        rewrite_ids = {str(value) for value in state.rewrite_block_ids}
        prior_blocks = {
            str(block.get("block_id")): block
            for block in prior.get("blocks", [])
            if str(block.get("block_id")) in rewrite_ids
        }
        if set(prior_blocks) != rewrite_ids:
            raise ValueError("Targeted rewrite references an unknown article block")
        seo_brief = (state.plan or {}).get("seo_brief") or self._build_seo_brief(
            state.plan or {},
            list((state.plan or {}).get("google_keywords", [])),
        )
        hierarchy = self._state_hierarchy(state)
        payload = {
            "context": self._context(run_id),
            "seo_brief": seo_brief,
            "approved_facts": approved,
            "rewrite_blocks": list(prior_blocks.values()),
            "editorial_feedback": state.editorial_review or {},
            "editorial_context": self._editorial_context(),
            "hierarchy": hierarchy.model_dump(mode="json"),
        }
        prompt = f"""
Você é o redator em uma revisão DIRIGIDA. Não regenere o artigo completo.
Reescreva somente todos os blocos listados em BLOCOS PARA CORRIGIR e devolva cada
block_id original exatamente uma vez. Preserve o type de cada bloco.

Corrija apenas os achados major/critical do revisor. Remova garantias, causalidade,
exageros ou afirmações meta que não estejam literalmente sustentadas. Não escreva
"fatos/fontes/evidências aprovadas", "segundo as fontes", "este artigo se baseia",
"consulte as fontes/referências", URLs, sites, autores ou créditos. Não acrescente
conhecimento novo. Toda frase factual deve manter evidence com um fact_id permitido.
Transições, sínteses editoriais sem alegação verificável e headings conceituais devem
usar is_factual=false e evidence=[]. unsupported_claims deve ser uma lista vazia.
Corrija também idioma estrangeiro, ortografia, repetição mecânica, headings longos
e faixas numéricas divergentes apontados pelo revisor, sempre sem criar fatos.
Quando o achado for de naturalidade, reescreva a cadência e as conexões do bloco,
não apenas troque sinônimos. Remova narração meta como "a seguir", "neste artigo"
e "vamos ver"; evite frases com molde e comprimento uniformes.
Preserve a voz, o público, o objetivo e a função comercial do contexto fixado.
Preserve node_ids e a função hierárquica de cada bloco; a revisão dirigida não pode
apagar, deslocar ou reordenar nós do contrato.

SEO BRIEF: {json.dumps(seo_brief, ensure_ascii=False)}
BLOCOS PARA CORRIGIR: {json.dumps(list(prior_blocks.values()), ensure_ascii=False)}
ACHADOS DO REVISOR: {json.dumps(state.editorial_review or {}, ensure_ascii=False)}
FATOS PERMITIDOS: {json.dumps(approved, ensure_ascii=False)}
CONTEXTO EDITORIAL FIXADO: {json.dumps(self._editorial_context(), ensure_ascii=False)}

{self.skills.prompt_fragment("writer")}
"""
        producer_run_id = run_id
        recovered = False
        recovery_code = None
        try:
            revision = await self.runtime.call(
                self.project.id,
                "writer",
                run_id,
                payload,
                self._revision_prompt(prompt),
                WriterRevisionOutput,
                attempt=state.editor_cycle + 1,
                pipeline_run_id=self.pipeline_run.id,
                event_context=self._stage_context,
            )
            output = self._merge_targeted_revision(prior, revision, rewrite_ids)
        except (ProviderError, ValueError) as exc:
            if isinstance(exc, ProviderError) and exc.category != "invalid_output":
                raise
            recovery_code = (
                exc.error_code
                if isinstance(exc, ProviderError)
                else "targeted_revision_contract_invalid"
            )
            output, removed_count = self._recover_targeted_revision(
                prior,
                state.editorial_review or {},
            )
            recovery_run_id = self._agent_run_id(
                "writer:targeted-recovery", state.editor_cycle + 1
            )
            producer_run_id = recovery_run_id
            recovered = True
            started_at = datetime.now(timezone.utc)
            recovery_run = await self.db.get(AgentRun, recovery_run_id)
            if recovery_run is None:
                recovery_run = AgentRun(
                    id=recovery_run_id,
                    project_id=self.project.id,
                    pipeline_run_id=self.pipeline_run.id,
                    idempotency_key=f"writer:targeted-recovery:{recovery_run_id}",
                    agent_role="editorial_repair",
                    attempt=state.editor_cycle + 1,
                    status="succeeded",
                    input_json={
                        "writer_run_id": str(run_id),
                        "rewrite_block_ids": sorted(rewrite_ids),
                        "recovery_code": recovery_code,
                    },
                    output_json=output,
                    decision=GateDecision.approved,
                    provider="deterministic",
                    model="targeted-sentence-removal-v1",
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    latency_ms=0,
                    feedback={"removed_sentence_count": removed_count},
                )
                self.db.add(recovery_run)
                await self.db.flush()
            failed_run = await self.db.get(AgentRun, run_id)
            if failed_run is not None:
                failed_run.status = "failed"
                failed_run.error = (
                    failed_run.error
                    or "A reescrita não correspondeu aos blocos solicitados."
                )
                failed_run.error_code = failed_run.error_code or recovery_code
                failed_run.error_category = (
                    failed_run.error_category or "invalid_output"
                )
                failed_run.retryable = False
                failed_run.recovered = True
                failed_run.recovery_code = recovery_code
                failed_run.recovered_by_agent_run_id = recovery_run_id
                failed_run.feedback = sanitize_nul(
                    {
                        **(failed_run.feedback or {}),
                        "recovered": True,
                        "recovery_code": recovery_code,
                        "recovered_by_agent_run_id": str(recovery_run_id),
                        "removed_sentence_count": removed_count,
                    }
                )
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "writer.targeted_revision_recovered",
                "writer",
                {
                    "message": (
                        "A reescrita inválida foi recuperada removendo somente "
                        "as frases graves identificadas pelo revisor."
                    ),
                    "status": "corrected",
                    "recovery_code": recovery_code,
                    "removed_sentence_count": removed_count,
                },
                idempotency_key=(
                    f"writer.targeted_revision_recovered:{recovery_run_id}"
                ),
                context=(
                    self._stage_context.with_agent(recovery_run_id)
                    if self._stage_context
                    else None
                ),
            )
            await self.db.commit()

        (
            output,
            invalid_fact_ids,
            _evidence_only_used,
            meta_sentences_removed,
        ) = self._normalize_writer_output(state, output, approved)
        output = self._ensure_seo_heading_structure(state, output, approved)
        output["covered_node_ids"] = sorted(
            {
                str(node_id)
                for block in output.get("blocks", [])
                for node_id in block.get("node_ids", [])
                if str(node_id)
            }
        )
        if hierarchy is not None:
            hierarchy_report = EditorialHierarchyGate.validate_draft(output, hierarchy)
            if hierarchy_report.blockers:
                raise ValueError(
                    "EDITORIAL_HIERARCHY_TARGETED_REVISION_BLOCKED: "
                    + "; ".join(hierarchy_report.blockers)
                )
        quality_gaps = self._draft_quality_gaps(output, minimum_words, state=state)
        for position, block in enumerate(output["blocks"]):
            block["position"] = position
            block["block_id"] = str(block.get("block_id") or uuid.uuid4())
        cited = {str(evidence["fact_id"]) for evidence in output["title_evidence"]} | {
            str(evidence["fact_id"])
            for block in output["blocks"]
            for sentence in block["sentences"]
            for evidence in sentence["evidence"]
        }
        producer_run = await self.db.get(AgentRun, producer_run_id)
        if producer_run is not None:
            producer_run.output_json = output
            producer_run.feedback = sanitize_nul(
                {
                    **(producer_run.feedback or {}),
                    "targeted_revision": True,
                    "recovered": recovered,
                    "recovery_code": recovery_code,
                    "invalid_fact_ids_removed": sorted(invalid_fact_ids),
                    "full_regeneration_used": False,
                    "visible_editorial_meta_sentences_removed": (
                        meta_sentences_removed
                    ),
                    "quality_gaps": quality_gaps,
                }
            )
        await self.versions.persist_draft(
            self.project,
            self.pipeline_run,
            output,
            producer_run_id,
            {uuid.UUID(value) for value in rewrite_ids},
        )
        state.draft = output
        await self._handoff(
            "writer",
            "editor",
            {
                "title": output["title"],
                "block_ids": [block["block_id"] for block in output["blocks"]],
                "editor_cycle": state.editor_cycle + 1,
                "targeted_revision": True,
                "recovered": recovered,
            },
            sorted(cited),
            state=state,
            producer_agent_run_id=producer_run_id,
            editor_cycle=state.editor_cycle + 1,
        )
        return state

    @staticmethod
    def _merge_targeted_revision(
        prior: dict,
        revision: dict,
        rewrite_ids: set[str],
    ) -> dict:
        replacements = {
            str(block.get("block_id")): block for block in revision.get("blocks", [])
        }
        if set(replacements) != rewrite_ids or len(replacements) != len(
            revision.get("blocks", [])
        ):
            raise ValueError("Targeted revision must replace every requested block")
        merged = copy.deepcopy(prior)
        merged["blocks"] = [
            copy.deepcopy(replacements.get(str(block.get("block_id")), block))
            for block in merged.get("blocks", [])
        ]
        merged["unsupported_claims"] = []
        return merged

    @staticmethod
    def _apply_editor_revisions(prior: dict, revised_blocks: list[dict]) -> dict:
        """Replace only editor-identified blocks while preserving article identity."""
        merged = copy.deepcopy(prior)
        replacements = {
            str(block.get("block_id")): copy.deepcopy(block) for block in revised_blocks
        }
        known_ids = {str(block.get("block_id")) for block in merged.get("blocks", [])}
        unknown = set(replacements) - known_ids
        if unknown:
            raise ValueError("Editor revisions reference unknown blocks")
        blocks = []
        for original in merged.get("blocks", []):
            block_id = str(original.get("block_id"))
            replacement = replacements.get(block_id)
            if replacement is None:
                blocks.append(original)
                continue
            replacement["block_id"] = block_id
            replacement["position"] = original.get("position", 0)
            blocks.append(replacement)
        merged["blocks"] = blocks
        merged["unsupported_claims"] = []
        validated = WriterOutput.model_validate(merged).model_dump(mode="json")
        # Preserve the exact legacy shape for artifacts created before hierarchy
        # tagging. New runs always carry node_ids and remain fully validated.
        prior_by_id = {
            str(block.get("block_id")): block for block in prior.get("blocks", [])
        }
        for block in validated.get("blocks", []):
            original = prior_by_id.get(str(block.get("block_id")), {})
            replacement = replacements.get(str(block.get("block_id")), {})
            if "node_ids" not in original and "node_ids" not in replacement:
                block.pop("node_ids", None)
        if "covered_node_ids" not in prior:
            validated.pop("covered_node_ids", None)
        return validated

    @staticmethod
    def _recover_targeted_revision(
        prior: dict,
        editorial_review: dict,
    ) -> tuple[dict, int]:
        exact_targets: dict[str, set[str]] = {}
        for category in ("fidelity_findings", "language_findings"):
            for finding in editorial_review.get(category, []):
                if str(finding.get("severity")) not in {"major", "critical"}:
                    continue
                block_id = str(finding.get("block_id") or "")
                sentence = (
                    re.sub(r"\s+", " ", str(finding.get("sentence") or ""))
                    .strip()
                    .casefold()
                )
                if block_id and sentence:
                    exact_targets.setdefault(block_id, set()).add(sentence)

        recovered = copy.deepcopy(prior)
        removed = 0
        kept_blocks = []
        for block in recovered.get("blocks", []):
            block_id = str(block.get("block_id") or "")
            targets = exact_targets.get(block_id, set())
            sentences = []
            for sentence in block.get("sentences", []):
                text = str(sentence.get("text") or "")
                normalized = re.sub(r"\s+", " ", text).strip().casefold()
                if normalized in targets or _VISIBLE_EDITORIAL_META.search(text):
                    removed += 1
                    continue
                sentences.append(sentence)
            if sentences:
                block["sentences"] = sentences
                kept_blocks.append(block)
        recovered["blocks"] = kept_blocks
        recovered["unsupported_claims"] = []
        return recovered, removed

    async def editor(self, state: PipelineState) -> PipelineState:
        await self._stage("editor", "Revisão de fidelidade e qualidade iniciada", state)
        run_id = self._agent_run_id("editor", state.editor_cycle + 1)
        approved = await self._approved_fact_dicts()
        payload = {
            "context": self._context(run_id),
            "draft": state.draft,
            "approved_facts": approved,
            "editorial_context": self._editorial_context(),
            "writer_quality_gaps": (state.editorial_review or {}).get(
                "writer_quality_gaps", []
            ),
        }
        prompt = f"""
Você é o revisor editorial. Compare cada sentença com os fatos citados.
Rejeite distorções, causalidade indevida, exageros e citações que não sustentem
a sentença. Faça revisão substantiva diretamente: para cada problema textual corrigível,
devolva o bloco completo em revised_blocks, preservando block_id, posição e somente
fact_id aprovados. Use rewrite_block_ids sem revised_blocks apenas quando o problema
exigir nova evidência que o editor não pode criar.
O campo evidence de cada sentença já é a citação estruturada. Não exija que
URL, site ou editora sejam mencionados no texto visível quando o fact_id correto
estiver vinculado. Registre ajustes leves como severity=minor, mas APROVE o texto
quando não existir achado de fidelidade ou linguagem major/critical. Use rewrite
somente para achados de fidelidade ou linguagem major/critical. Se a evidência não sustentar uma
afirmação, mande enfraquecer ou remover essa afirmação, nunca inventar suporte.
Trate como achado de linguagem major qualquer frase em idioma diferente de
{self.project.language}, erro ortográfico evidente, conector mecânico repetido,
introdução/conclusão genérica de modelo ou heading que copie uma pergunta interna.
Também marque como major narração meta ("a seguir", "neste artigo", "vamos ver"),
subtítulos usados para fatiar parágrafos pequenos, cadência uniforme ao longo de
várias seções e qualquer trecho que apenas parafraseie claim_text em série.
Faixas numéricas diferentes para a mesma medida só podem permanecer quando o texto
explicar claramente o contexto ou a variação; caso contrário, registre achado major.
Leia também o artigo como unidade: marque como linguagem major qualquer salto
lógico, sequência de parágrafos sem conexão, seção que apenas enumera fatos ou
conclusão que não decorra do desenvolvimento. Não aprove frases corretas isoladas
quando o conjunto não formar um raciocínio claro e harmonioso.
Nunca sugira acrescentar fontes, referências, sites, autores, créditos ou frases
como "segundo as fontes". Se encontrar texto meta sobre "fatos/fontes/evidências
aprovadas", solicite apenas sua remoção, preservando o restante do bloco.

RASCUNHO: {json.dumps(state.draft, ensure_ascii=False)}
FATOS: {json.dumps(approved, ensure_ascii=False)}
LACUNAS SINALIZADAS PELO WRITER: {json.dumps((state.editorial_review or {}).get("writer_quality_gaps", []), ensure_ascii=False)}
CONTEXTO EDITORIAL FIXADO: {json.dumps(self._editorial_context(), ensure_ascii=False)}

Verifique se a voz, a profundidade, o vocabulário, o objetivo e a ação proposta
combinam com o perfil e o briefing fixados. O artigo deve informar primeiro e
conectar a oferta apenas quando isso for natural e sustentado. O perfil não
autoriza alegações factuais sem fact_id e não deve aparecer como ficha cadastral.

{self.skills.prompt_fragment("editor")}
"""
        await self._cancellation_boundary()
        recovered_from_provider_failure = False
        try:
            output = await self.runtime.call(
                self.project.id,
                "editor",
                run_id,
                payload,
                self._revision_prompt(prompt),
                EditorOutput,
                attempt=state.editor_cycle + 1,
                pipeline_run_id=self.pipeline_run.id,
                event_context=self._stage_context,
            )
        except ProviderError as exc:
            if exc.category != "invalid_output":
                raise
            output = await self._recover_editor_provider_output(
                state,
                editor_run_id=run_id,
                error=exc,
            )
            recovered_from_provider_failure = True
        await self._cancellation_boundary()
        valid_blocks = {b["block_id"] for b in state.draft["blocks"]}
        output["rewrite_block_ids"] = [
            str(x)
            for x in output.get("rewrite_block_ids", [])
            if str(x) in valid_blocks
        ]
        output = sanitize_nul(output, strip_escaped=True)
        revised_blocks = output.get("revised_blocks") or []
        if revised_blocks:
            revised_ids = {str(block.get("block_id")) for block in revised_blocks}
            if not revised_ids.issubset({str(value) for value in valid_blocks}):
                raise ValueError("Editor returned a revision for an unknown block")
            merged = self._apply_editor_revisions(state.draft, revised_blocks)
            merged, invalid_editor_refs, _, _ = self._normalize_writer_output(
                state, merged, approved
            )
            merged = self._ensure_seo_heading_structure(state, merged, approved)
            if invalid_editor_refs:
                output.setdefault("open_evidence_gaps", []).append(
                    "A revisão editorial continha referência inválida ou sentença "
                    "factual sem suporte e foi saneada localmente."
                )
            await self.versions.persist_draft(
                self.project,
                self.pipeline_run,
                merged,
                run_id,
                {uuid.UUID(value) for value in revised_ids},
            )
            state.draft = merged
            blocking_ids = {
                str(finding.get("block_id"))
                for category in ("fidelity_findings", "language_findings")
                for finding in output.get(category, [])
                if str(finding.get("severity")) in {"major", "critical"}
            }
            unresolved_block_ids = blocking_ids - revised_ids
            if unresolved_block_ids:
                output["rewrite_block_ids"] = sorted(
                    set(output.get("rewrite_block_ids") or []) | unresolved_block_ids
                )
            if (
                output.get("decision") != "rejected"
                and not output.get("open_evidence_gaps")
                and not unresolved_block_ids
            ):
                output["model_decision"] = output.get("decision")
                output["decision"] = "approved"
                output["resolution"] = "substantive_revision"
                output["rewrite_block_ids"] = []
        model_decision = str(
            output.get("model_decision") or output.get("decision") or "rejected"
        )
        rewrite_budget_remaining = state.editor_cycle < int(
            self._flag("max_editor_cycles")
        )
        editorial_resolution = (
            "provider_output_blocked"
            if recovered_from_provider_failure
            else (
                "substantive_revision"
                if output.get("resolution") == "substantive_revision"
                else self._editor_resolution(
                    output,
                    rewrite_budget_remaining=rewrite_budget_remaining,
                )
            )
        )
        if editorial_resolution == "targeted_rewrite":
            output["model_decision"] = model_decision
            output["decision"] = "rewrite"
            output["resolution"] = "targeted_rewrite"
        use_deterministic_repair = (
            editorial_resolution == "deterministic_targeted_repair"
        )
        if editorial_resolution == "approved_with_advisory_findings":
            output["model_decision"] = model_decision
            output["decision"] = "approved"
            output["resolution"] = "approved_with_advisory_findings"
            output["rewrite_block_ids"] = []
        run = await self.db.get(AgentRun, run_id)
        run.output_json = output
        run.decision = sanitize_nul(output["decision"])
        run.feedback = sanitize_nul(
            {
                "fidelity_findings": output.get("fidelity_findings", []),
                "language_findings": output.get("language_findings", []),
            }
        )
        if use_deterministic_repair:
            repaired = await self._persist_deterministic_editorial_repair(
                state,
                approved,
                editor_run_id=run_id,
                editor_output=output,
            )
            state.draft = repaired
            output = {
                **output,
                "model_decision": model_decision,
                "decision": "approved",
                "resolution": "deterministic_targeted_repair",
                "rewrite_block_ids": [],
            }
        hierarchy = self._state_hierarchy(state)
        if hierarchy is not None:
            hierarchy_report = EditorialHierarchyGate.validate_draft(
                state.draft or {}, hierarchy
            )
            if hierarchy_report.blockers:
                raise ValueError(
                    "EDITORIAL_HIERARCHY_EDITOR_BLOCKED: "
                    + "; ".join(hierarchy_report.blockers)
                )
        if output["decision"] == "approved" and not recovered_from_provider_failure:
            await self._mark_current_draft_approved(state)
        state.editorial_review = output
        if output["decision"] == "rewrite":
            await self._handoff(
                "editor",
                "writer",
                {
                    "decision": "rewrite",
                    "rewrite_block_ids": output["rewrite_block_ids"],
                    "fidelity_findings": output.get("fidelity_findings", []),
                    "language_findings": output.get("language_findings", []),
                },
                state=state,
                producer_agent_run_id=run_id,
                editor_cycle=state.editor_cycle + 1,
            )
        return state

    async def _recover_editor_provider_output(
        self,
        state: PipelineState,
        *,
        editor_run_id: uuid.UUID,
        error: ProviderError,
    ) -> dict:
        recovery_code = error.error_code
        recovery_run_id = self._agent_run_id(
            "editor:provider-output-recovery", state.editor_cycle + 1
        )
        minimum_h2 = max(
            1,
            self._quality_threshold("min_h2_count", settings.quality_min_h2_count),
        )
        minimum_h3 = max(
            0,
            self._quality_threshold("min_h3_count", settings.quality_min_h3_count),
        )
        deterministic_gaps = self._draft_quality_gaps(
            state.draft or {},
            self._minimum_article_words(),
            maximum_words=self._quality_threshold(
                "max_word_count", settings.quality_max_word_count
            ),
            minimum_h2=minimum_h2,
            minimum_h3=minimum_h3,
            state=state,
        )
        rewrite_block_ids = [
            str(block.get("block_id"))
            for block in (state.draft or {}).get("blocks", [])
            if block.get("block_id")
        ]
        requires_rewrite = bool(deterministic_gaps and rewrite_block_ids)
        language_findings = (
            [
                {
                    "block_id": rewrite_block_ids[0],
                    "sentence": "",
                    "issue": (
                        "A revisão automática falhou e o rascunho ainda possui "
                        "lacunas editoriais determinísticas: "
                        + ", ".join(deterministic_gaps)
                    ),
                    "severity": "major",
                    "suggested_action": (
                        "Revisar os blocos preservando os fact_id existentes."
                    ),
                }
            ]
            if requires_rewrite
            else []
        )
        fallback_output = {
            "decision": "rejected",
            "model_decision": "provider_output_invalid",
            "resolution": "provider_output_blocked",
            "fidelity_findings": [],
            "language_findings": language_findings,
            "rewrite_block_ids": [],
            "revised_blocks": [],
            "preserved_fact_ids": [],
            "open_evidence_gaps": [
                "A revisão editorial estruturada não pôde ser validada."
            ],
            "deterministic_quality_gaps": deterministic_gaps,
        }
        recovery_run = await self.db.get(AgentRun, recovery_run_id)
        if recovery_run is None:
            now = datetime.now(timezone.utc)
            recovery_run = AgentRun(
                id=recovery_run_id,
                project_id=self.project.id,
                pipeline_run_id=self.pipeline_run.id,
                idempotency_key=f"editor:provider-output-recovery:{recovery_run_id}",
                agent_role="editorial_repair",
                attempt=state.editor_cycle + 1,
                status="succeeded",
                input_json={
                    "editor_run_id": str(editor_run_id),
                    "recovery_code": recovery_code,
                    "reason": "editor_provider_output_invalid",
                },
                output_json=state.draft,
                decision=GateDecision.rejected,
                provider="deterministic",
                model="markdown-draft-preservation-v1",
                started_at=now,
                finished_at=now,
                latency_ms=0,
                feedback={
                    "resolution": "provider_output_blocked",
                    "article_preserved": True,
                    "human_review_required": True,
                },
            )
            self.db.add(recovery_run)
            await self.db.flush()

        failed_run = await self.db.get(AgentRun, editor_run_id)
        if failed_run is not None:
            failed_run.recovered = True
            failed_run.recovery_code = recovery_code
            failed_run.recovered_by_agent_run_id = recovery_run_id
            failed_run.feedback = sanitize_nul(
                {
                    **(failed_run.feedback or {}),
                    "recovered": True,
                    "recovery_code": recovery_code,
                    "recovered_by_agent_run_id": str(recovery_run_id),
                    "article_preserved": True,
                    "human_review_required": True,
                    "deterministic_quality_gaps": deterministic_gaps,
                }
            )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "editor.provider_output_recovered",
            "editor",
            {
                "message": (
                    "A saida estruturada do editor falhou; o rascunho validado "
                    "foi preservado e a execução foi bloqueada para nova revisão."
                ),
                "status": "blocked",
                "recovery_code": recovery_code,
                "human_review_required": True,
                "deterministic_quality_gaps": deterministic_gaps,
                "rewrite_requested": False,
            },
            idempotency_key=(f"editor.provider_output_recovered:{recovery_run_id}"),
            context=(
                self._stage_context.with_agent(recovery_run_id)
                if self._stage_context
                else None
            ),
        )
        await self.db.commit()
        return fallback_output

    async def finalizer(self, state: PipelineState) -> PipelineState:
        await self._stage("finalizer", "Formatação do pacote final iniciada", state)
        run_id = self._agent_run_id("finalizer", 1)
        existing_run = await self.db.get(AgentRun, run_id)
        if (
            existing_run
            and existing_run.status == "succeeded"
            and existing_run.output_json
        ):
            article = await self.db.scalar(
                select(Article).where(Article.project_id == self.project.id)
            )
            state.final_package = existing_run.output_json
            article.status = "quality_review"
            article.active_pipeline_run_id = self.pipeline_run.id
            article.final_markdown = state.final_package["markdown"]
            article.final_html = state.final_package["html"]
            article.seo_metadata = state.final_package["seo_metadata"]
            article.source_report = state.final_package["source_report"]
            current_version = await self.db.scalar(
                select(ArticleVersion).where(
                    ArticleVersion.article_id == article.id,
                    ArticleVersion.version == article.current_version,
                    ArticleVersion.pipeline_run_id == self.pipeline_run.id,
                )
            )
            if current_version is None:
                raise ValueError("Finalized article version is inconsistent")
            current_version.final_markdown = state.final_package["markdown"]
            current_version.final_html = state.final_package["html"]
            current_version.seo_metadata = state.final_package["seo_metadata"]
            current_version.source_report = state.final_package["source_report"]
            current_version.editorial_status = "quality_review"
            current_version.content_checksum = article_version_checksum(current_version)
            await self._handoff(
                "finalizer",
                "quality_gate",
                {
                    "article_id": str(article.id),
                    "resumed": True,
                    "editor_cycles": state.editor_cycle,
                },
                [str(item["id"]) for item in await self._approved_fact_dicts()],
                state=state,
                producer_agent_run_id=run_id,
            )
            return state
        started_at = datetime.now(timezone.utc)
        article = await self.db.scalar(
            select(Article).where(Article.project_id == self.project.id)
        )
        markdown_parts = [f"# {state.draft['title']}"]
        html_parts = [f"<h1>{html.escape(state.draft['title'])}</h1>"]
        for block in state.draft["blocks"]:
            sentence_texts = [s["text"] for s in block["sentences"]]
            text = " ".join(sentence_texts)
            if block["type"] in {"h2", "h3"}:
                level = block["type"][1]
                markdown_parts.append(f"{'#' * int(level)} {text}")
                html_parts.append(f"<h{level}>{html.escape(text)}</h{level}>")
            elif block["type"] == "list":
                markdown_parts.append("\n".join(f"- {item}" for item in sentence_texts))
                html_parts.append(
                    "<ul>"
                    + "".join(
                        f"<li>{html.escape(item)}</li>" for item in sentence_texts
                    )
                    + "</ul>"
                )
            else:
                markdown_parts.append(text)
                html_parts.append(f"<p>{html.escape(text)}</p>")
        approved = await self._approved_fact_dicts()
        sources = {}
        for fact in approved:
            sources[fact["source"]["url"]] = {
                "title": fact["source"]["title"],
                "url": fact["source"]["url"],
                "publisher": fact["source"].get("publisher"),
                "reliability_score": fact["source"]["reliability_score"],
            }
        first_paragraph_block = next(
            (b for b in state.draft["blocks"] if b["type"] == "paragraph"),
            None,
        )
        focus_keyphrase, related_keyphrases = self._seo_keyphrases(
            state,
            state.draft["title"],
        )
        meta_sentences = []
        meta_fact_ids = []
        if first_paragraph_block:
            paragraph_blocks = [
                block for block in state.draft["blocks"] if block["type"] == "paragraph"
            ]
            for sentence in (
                sentence
                for block in paragraph_blocks
                for sentence in block["sentences"]
            ):
                meta_sentences.append(sentence["text"])
                meta_fact_ids.extend(
                    str(e["fact_id"]) for e in sentence.get("evidence", [])
                )
                if len(" ".join(meta_sentences)) >= 90:
                    break
        if not meta_sentences:
            meta_sentences = [state.draft["title"]]
            meta_fact_ids = [str(e["fact_id"]) for e in state.draft["title_evidence"]]
        metadata = {
            "title": self._truncate_at_word(state.draft["title"], 60),
            "meta_description": self._truncate_at_word(" ".join(meta_sentences), 155),
            "slug": self._slug(state.draft["title"]),
            "language": self.project.language,
            "focus_keyphrase": focus_keyphrase,
            "related_keyphrases": related_keyphrases,
            "yoast_handoff": {
                "plugin": "Yoast SEO Premium",
                "requires_snippet_preview_review": True,
                "requires_human_editorial_review": True,
                "metrics_are_diagnostic": True,
            },
            "title_fact_ids": [
                str(e["fact_id"]) for e in state.draft["title_evidence"]
            ],
            "meta_description_fact_ids": list(dict.fromkeys(meta_fact_ids)),
        }
        source_report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "unsupported_claim_count": 0,
            "distinct_source_count": len(sources),
            "sources": list(sources.values()),
            "fact_count": len(approved),
            "traceability": [
                {
                    "block_id": block["block_id"],
                    "sentences": [
                        {
                            "text": sentence["text"],
                            "fact_ids": [
                                str(e["fact_id"]) for e in sentence.get("evidence", [])
                            ],
                        }
                        for sentence in block["sentences"]
                    ],
                }
                for block in state.draft["blocks"]
            ],
            "title_fact_ids": [
                str(e["fact_id"]) for e in state.draft["title_evidence"]
            ],
        }
        package = sanitize_nul(
            {
                "markdown": "\n\n".join(markdown_parts),
                "html": "\n".join(html_parts),
                "seo_metadata": metadata,
                "source_report": source_report,
                "unsupported_claim_count": 0,
            }
        )
        finalizer_input = sanitize_nul(
            {
                "article_id": str(article.id),
                "draft_title": state.draft["title"],
            }
        )
        self.db.add(
            AgentRun(
                id=run_id,
                project_id=self.project.id,
                pipeline_run_id=self.pipeline_run.id,
                idempotency_key=f"finalizer:{run_id}",
                agent_role="finalizer",
                status="succeeded",
                input_json=finalizer_input,
                output_json=package,
                decision="approved",
                provider="deterministic",
                model="evidence-preserving-formatter-v1",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
            )
        )
        article.status = "quality_review"
        article.active_pipeline_run_id = self.pipeline_run.id
        article.final_markdown = package["markdown"]
        article.final_html = package["html"]
        article.seo_metadata = metadata
        article.source_report = source_report
        current_version = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
            )
        )
        current_version.final_markdown = package["markdown"]
        current_version.final_html = package["html"]
        current_version.seo_metadata = metadata
        current_version.source_report = source_report
        current_version.editorial_status = "quality_review"
        current_version.content_checksum = article_version_checksum(current_version)
        await self._cancellation_boundary()
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "agent.completed",
            "finalizer",
            {
                "message": "Pacote final montado sem gerar novas afirmações",
                "run_id": str(run_id),
            },
            idempotency_key=f"agent.completed:{run_id}",
            context=(
                self._stage_context.with_agent(run_id) if self._stage_context else None
            ),
        )
        state.final_package = package
        await self._handoff(
            "finalizer",
            "quality_gate",
            {
                "article_id": str(article.id),
                "distinct_source_count": len(sources),
                "fact_count": len(approved),
                "editor_cycles": state.editor_cycle,
            },
            [str(x["id"]) for x in approved],
            state=state,
            producer_agent_run_id=run_id,
        )
        return state

    async def quality_gate(self, state: PipelineState) -> PipelineState:
        await self._stage(
            "quality_gate", "Avaliação editorial independente iniciada", state
        )
        article = await self.db.scalar(
            select(Article).where(Article.project_id == self.project.id)
        )
        version = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
                ArticleVersion.pipeline_run_id == self.pipeline_run.id,
            )
        )
        if version is None:
            raise ValueError("Quality gate has no current article version")
        quality = await QualityEvaluator(self.db).evaluate(
            self.project, self.pipeline_run, article, version
        )
        state.quality_evaluation = {
            "status": quality.status,
            "overall_score": float(quality.overall_score),
            "rubric_version": quality.rubric_version,
            "critical_blockers": (quality.result_json or {}).get(
                "critical_blockers", []
            ),
            "warnings": (quality.result_json or {}).get("warnings", []),
        }
        if quality.status != "passed":
            article.status = "blocked"
            version.editorial_status = "blocked"
            state.blocking_code = "ARTICLE_QUALITY_BLOCKED"
            state.blocking_reason = (
                "O artigo foi preservado para diagnóstico, mas não atingiu o "
                "padrão editorial mínimo para revisão humana."
            )
            await self.runtime.event(
                self.project.id,
                self.pipeline_run.id,
                "pipeline.quality_blocked",
                "quality_gate",
                {
                    "message": state.blocking_reason,
                    "error_code": state.blocking_code,
                    "quality_status": quality.status,
                    "blockers": state.quality_evaluation["critical_blockers"],
                },
                idempotency_key="pipeline.quality_blocked",
                context=self._stage_context,
            )
            return state

        await self._cancellation_boundary()
        await ContentSimilarityService(
            self.db,
            embedding_route=self.execution_manifest.get("embedding_route"),
            route_is_fixed=True,
        ).index_article(
            article,
            ContentSimilarityService.final_fingerprint(
                state.draft["title"],
                state.final_package["markdown"],
                state.final_package["seo_metadata"],
            ),
        )
        article.status = "needs_human_approval"
        version.editorial_status = "needs_human_approval"
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "quality_gate.passed",
            "quality_gate",
            {
                "message": "O artigo passou pelo gate automático de produção.",
                "overall_score": float(quality.overall_score),
                "rubric_version": quality.rubric_version,
            },
            idempotency_key="quality_gate.passed",
            context=self._stage_context,
        )
        await self._handoff(
            "quality_gate",
            "skill_curator",
            {
                "article_id": str(article.id),
                "quality_status": quality.status,
                "overall_score": float(quality.overall_score),
            },
            [str(item["id"]) for item in await self._approved_fact_dicts()],
            state=state,
        )
        return state

    async def skill_curator(self, state: PipelineState) -> PipelineState:
        await self._stage("skill_curator", "Curadoria de aprendizado iniciada", state)
        article = await self.db.scalar(
            select(Article).where(Article.project_id == self.project.id)
        )
        run_id = self._agent_run_id("skill_curator", 1)
        sources = state.final_package["source_report"]["sources"]
        payload = {
            "context": self._context(run_id),
            "article_id": str(article.id),
            "niche": self.project.niche or "general",
            "sources": sources,
            "research_cycles": state.research_cycle,
            "editor_cycles": state.editor_cycle,
            "editorial_review": state.editorial_review,
        }
        prompt = f"""
Você é o curador de skills. Identifique no máximo uma preferência de pesquisa
reutilizável para o nicho {self.project.niche or "general"}, baseada apenas no
processo e nas fontes deste artigo. A candidata deve permanecer instável,
auto_inject=false e usar evidence_article_id={article.id}. Não transforme uma
observação de um único artigo em regra universal.

Você também pode propor no máximo uma memória de processo por agente. Memórias
devem ser métodos, preferências ou falhas observadas, nunca fatos do tema. Toda
memória permanecerá em quarentena para revisão humana.

FONTES: {json.dumps(sources, ensure_ascii=False)}
RESULTADO DO PROCESSO: ciclos de pesquisa={state.research_cycle}, ciclos de
edição={state.editor_cycle}, revisão={json.dumps(state.editorial_review, ensure_ascii=False)}
"""
        await self._cancellation_boundary()
        output = await self.runtime.call(
            self.project.id,
            "skill_curator",
            run_id,
            payload,
            self._revision_prompt(prompt),
            CuratorOutput,
            pipeline_run_id=self.pipeline_run.id,
            event_context=self._stage_context,
        )
        await self._cancellation_boundary()
        if not output.get("candidates"):
            preferred = sorted(
                sources,
                key=lambda item: item.get("reliability_score", 0),
                reverse=True,
            )[:3]
            domains = [urlsplit(x["url"]).netloc for x in preferred]
            output["candidates"] = [
                {
                    "niche": self.project.niche or "general",
                    "title": "Preferências de fontes observadas no primeiro artigo",
                    "rules": [
                        "Priorizar e revalidar fontes de alta confiabilidade observadas: "
                        + ", ".join(domains)
                    ],
                    "evidence_article_id": str(article.id),
                    "confidence_score": 0.34,
                    "auto_inject": False,
                }
            ]
        learning = SkillLearningService(
            self.db,
            stability_threshold=int(self._flag("learned_skill_stability_threshold")),
            minimum_independent_articles=int(
                self._flag("learned_skill_min_independent_articles")
            ),
        )
        outcome = PipelineOutcomeSignals.from_pipeline_state(state)
        for candidate in output.get("candidates", [])[:1]:
            try:
                await learning.record_candidate(
                    project=self.project,
                    pipeline_run_id=self.pipeline_run.id,
                    article=article,
                    candidate=candidate,
                    outcome=outcome,
                )
            except SkillLearningInputError:
                continue
        for candidate in output.get("memory_candidates", [])[:6]:
            source_id = f"article:{article.id}:{candidate['agent_role']}:{candidate['memory_kind']}"
            exists = await self.db.scalar(
                select(AgentMemory.id).where(
                    AgentMemory.agent_role == candidate["agent_role"],
                    AgentMemory.source_id == source_id,
                )
            )
            if exists:
                continue
            self.db.add(
                AgentMemory(
                    agent_role=candidate["agent_role"],
                    project_id=None,
                    origin_pipeline_run_id=self.pipeline_run.id,
                    niche=self.project.niche,
                    memory_kind=candidate["memory_kind"],
                    content=candidate["content"],
                    source_type="completed-article",
                    source_id=source_id,
                    confidence_score=candidate["confidence_score"],
                    status=LearningStatus.quarantine,
                )
            )
        return state

    async def _all_fact_dicts(self) -> list[dict]:
        return await self.ledger.all_facts()

    async def _approved_fact_dicts(self) -> list[dict]:
        return await self.ledger.approved_facts()

    def _quality_threshold(self, name: str, default: int) -> int:
        brief = self._editorial_context().get("content_brief") or {}
        brief_keys = {
            "min_word_count": "minimum_words",
            "max_word_count": "maximum_words",
            "min_h2_count": "minimum_h2",
            "min_h3_count": "minimum_h3",
        }
        brief_value = brief.get(brief_keys.get(name, ""))
        if brief_value is not None:
            return int(brief_value)
        manifest = getattr(self, "execution_manifest", None) or {}
        thresholds = (manifest.get("quality_evaluator") or {}).get("thresholds") or {}
        return int(thresholds.get(name, default))

    def _minimum_article_words(self) -> int:
        return max(
            1,
            self._quality_threshold("min_word_count", settings.quality_min_word_count),
        )

    @staticmethod
    def _draft_markdown(output: dict) -> str:
        parts = [f"# {str(output.get('title') or '').strip()}"]
        for block in output.get("blocks", []):
            sentences = [
                str(sentence.get("text") or "").strip()
                for sentence in block.get("sentences", [])
                if str(sentence.get("text") or "").strip()
            ]
            if not sentences:
                continue
            block_type = block.get("type")
            if block_type in {"h1", "h2", "h3"}:
                level = int(str(block_type)[1])
                parts.append(f"{'#' * level} {' '.join(sentences)}")
            elif block_type == "list":
                parts.append("\n".join(f"- {sentence}" for sentence in sentences))
            else:
                parts.append(" ".join(sentences))
        return "\n\n".join(parts)

    @classmethod
    def _draft_quality_gaps(
        cls,
        output: dict,
        minimum_words: int,
        *,
        maximum_words: int | None = None,
        minimum_h2: int = 4,
        minimum_h3: int = 0,
        state: PipelineState | None = None,
    ) -> list[str]:
        markdown = cls._draft_markdown(output)
        visible = re.sub(
            r"^#{1,6}\s+|^[-*+]\s+",
            "",
            markdown,
            flags=re.MULTILINE,
        )
        word_count = len(re.findall(r"\b[\wÀ-ÿ'-]+\b", visible))
        h2_count = sum(block.get("type") == "h2" for block in output.get("blocks", []))
        h3_count = sum(block.get("type") == "h3" for block in output.get("blocks", []))
        gaps = []
        if word_count < minimum_words:
            gaps.append(f"word_count:{word_count}<{minimum_words}")
        maximum_words = (
            settings.quality_max_word_count if maximum_words is None else maximum_words
        )
        if word_count > maximum_words:
            gaps.append(f"word_count:{word_count}>{maximum_words}")
        if h2_count < minimum_h2:
            gaps.append(f"h2_count:{h2_count}<{minimum_h2}")
        if h3_count < minimum_h3:
            gaps.append(f"h3_count:{h3_count}<{minimum_h3}")
        headings = [
            str(sentence.get("text") or "").strip()
            for block in output.get("blocks", [])
            if block.get("type") in {"h2", "h3"}
            for sentence in block.get("sentences", [])
        ]
        if any(len(heading) > 80 for heading in headings):
            gaps.append("heading_too_long")
        title = str(output.get("title") or "").strip()
        if not 15 <= len(title) <= 60:
            gaps.append(f"title_length:{len(title)}")
        if state is not None:
            internal_questions = {
                re.sub(r"\s+", " ", str(question.get("question") or ""))
                .strip()
                .rstrip("?.!:;")
                .casefold()
                for question in (state.plan or {}).get("questions", [])
                if str(question.get("question") or "").strip()
            }
            if any(
                re.sub(r"\s+", " ", heading).strip().rstrip("?.!:;").casefold()
                in internal_questions
                for heading in headings
            ):
                gaps.append("internal_question_heading")
            focus, _ = cls._seo_keyphrases(state, title)
            focus_tokens = cls._keyword_tokens(focus)
            title_tokens = cls._keyword_tokens(title)
            subheading_tokens = cls._keyword_tokens(
                " ".join(
                    str(sentence.get("text") or "")
                    for block in output.get("blocks", [])
                    if block.get("type") in {"h2", "h3"}
                    for sentence in block.get("sentences", [])
                )
            )
            body_tokens = cls._keyword_tokens(
                " ".join(
                    str(sentence.get("text") or "")
                    for block in output.get("blocks", [])
                    if block.get("type") in {"paragraph", "list"}
                    for sentence in block.get("sentences", [])
                )
            )
            if focus_tokens and not focus_tokens <= title_tokens:
                gaps.append("focus_keyphrase_missing_from_title")
            if focus_tokens and (
                len(focus_tokens & subheading_tokens) / len(focus_tokens) < 0.6
            ):
                gaps.append("focus_keyphrase_missing_from_subheadings")
            if focus_tokens and (
                len(focus_tokens & body_tokens) / len(focus_tokens) < 0.5
            ):
                gaps.append("focus_keyphrase_missing_from_body")
        if _VISIBLE_EDITORIAL_META.search(visible):
            gaps.append("visible_editorial_meta")
        if _GENERIC_TEMPLATE_LANGUAGE.search(visible):
            gaps.append("generic_template_language")
        sentence_prefixes = Counter(
            " ".join(re.findall(r"\b[\wÀ-ÿ'-]+\b", sentence.casefold())[:5])
            for sentence in re.split(r"(?<=[.!?])\s+", visible)
            if len(re.findall(r"\b[\wÀ-ÿ'-]+\b", sentence)) >= 5
        )
        if any(prefix and count >= 3 for prefix, count in sentence_prefixes.items()):
            gaps.append("repetitive_sentence_opening")
        naturalness = editorial_naturalness_metrics(markdown)
        if naturalness["meta_narration_matches"]:
            gaps.append("visible_meta_narration")
        if naturalness["mechanical_prose"]:
            gaps.append("mechanical_prose_pattern")
        return gaps

    def _normalize_writer_output(
        self,
        state: PipelineState,
        output: dict,
        approved_facts: list[dict],
    ) -> tuple[dict, set[str], bool, int]:
        """Validate references and remove only unsupported factual sentences.

        A provider response is never regenerated in full here. Invalid references are
        discarded locally. A factual sentence that loses every valid citation is
        removed so an unsupported claim cannot enter the persisted article. The
        deterministic quality gate will expose any resulting structural gap.
        """
        output, meta_sentences_removed = self._remove_visible_editorial_meta_sentences(
            output
        )
        output, source_credit_sentences_removed = (
            self._remove_visible_source_credit_sentences(output, approved_facts)
        )
        meta_sentences_removed += source_credit_sentences_removed
        if self._text_contains_source_credit(
            str(output.get("title") or ""), approved_facts
        ):
            output["title"] = ""
        output["title"] = self._seo_title(state, str(output.get("title") or ""))
        allowed = {str(fact["id"]) for fact in approved_facts}
        invalid: set[str] = set()

        def valid_references(references: list[dict]) -> list[dict]:
            kept = []
            for reference in references or []:
                fact_id = str(reference.get("fact_id"))
                if fact_id in allowed:
                    kept.append(reference)
                else:
                    invalid.add(fact_id)
            return kept

        output["title_evidence"] = valid_references(output.get("title_evidence", []))
        kept_blocks: list[dict] = []
        unsupported_sentence_count = 0
        for block in output.get("blocks", []):
            kept_sentences: list[dict] = []
            for sentence in block.get("sentences", []):
                is_factual = bool(sentence.get("is_factual", True))
                references = valid_references(sentence.get("evidence", []))
                if is_factual and not references:
                    unsupported_sentence_count += 1
                    continue
                sentence["evidence"] = references if is_factual else []
                kept_sentences.append(sentence)
            if kept_sentences:
                block["sentences"] = kept_sentences
                kept_blocks.append(block)
        output["blocks"] = kept_blocks
        if unsupported_sentence_count:
            invalid.add(f"unsupported_sentences:{unsupported_sentence_count}")
        if not output["blocks"]:
            raise ValueError(
                "Writer produced no supportable content after evidence validation"
            )
        normalized = WriterOutput.model_validate(output).model_dump(mode="json")
        return normalized, invalid, False, meta_sentences_removed

    @staticmethod
    def _remove_visible_editorial_meta_sentences(
        output: dict,
    ) -> tuple[dict, int]:
        cleaned = copy.deepcopy(output)
        removed = 0
        kept_blocks = []
        for block in cleaned.get("blocks", []):
            sentences = []
            for sentence in block.get("sentences", []):
                if _VISIBLE_EDITORIAL_META.search(str(sentence.get("text") or "")):
                    removed += 1
                    continue
                sentences.append(sentence)
            if sentences:
                block["sentences"] = sentences
                kept_blocks.append(block)
        cleaned["blocks"] = kept_blocks
        return cleaned, removed

    @classmethod
    def _remove_visible_source_credit_sentences(
        cls,
        output: dict,
        approved_facts: list[dict],
    ) -> tuple[dict, int]:
        cleaned = copy.deepcopy(output)
        removed = 0
        kept_blocks = []
        for block in cleaned.get("blocks", []):
            sentences = []
            for sentence in block.get("sentences", []):
                if cls._text_contains_source_credit(
                    str(sentence.get("text") or ""), approved_facts
                ):
                    removed += 1
                    continue
                sentences.append(sentence)
            if sentences:
                block["sentences"] = sentences
                kept_blocks.append(block)
        cleaned["blocks"] = kept_blocks
        return cleaned, removed

    @staticmethod
    def _text_contains_source_credit(
        text: str,
        approved_facts: list[dict],
    ) -> bool:
        visible = text.casefold()
        if re.search(
            r"https?://|\bwww\.|\bfontes?\s*:", visible
        ) or _VISIBLE_EDITORIAL_META.search(visible):
            return True
        labels = set()
        for fact in approved_facts:
            source = fact.get("source") or {}
            labels.update(
                str(source.get(key) or "").strip().casefold()
                for key in ("domain", "publisher", "author")
            )
        return any(label and len(label) >= 5 and label in visible for label in labels)

    @staticmethod
    def _blocking_editor_findings(output: dict) -> list[dict]:
        """Return major factual or language defects that require correction."""
        return [
            finding
            for category in ("fidelity_findings", "language_findings")
            for finding in output.get(category, [])
            if str(finding.get("severity")) in {"major", "critical"}
        ]

    @classmethod
    def _editor_resolution(
        cls,
        output: dict,
        *,
        rewrite_budget_remaining: bool,
    ) -> str:
        model_decision = str(output.get("decision") or "rejected")
        blocking = cls._blocking_editor_findings(output)
        if model_decision == "approved" and not blocking:
            return "approved"
        if model_decision == "rewrite" and not blocking:
            return "approved_with_advisory_findings"
        if (
            blocking
            and rewrite_budget_remaining
            and bool(output.get("rewrite_block_ids"))
        ):
            return "targeted_rewrite"
        return "deterministic_targeted_repair"

    @staticmethod
    def _editorial_heading(question: str) -> str:
        """Turn an internal research question into a natural blog heading."""
        heading = re.sub(r"\s+", " ", question).strip().rstrip("?").strip()
        heading = re.sub(
            r"(?i),\s+(?:quais?|como|quando|o que)\b.*$",
            "",
            heading,
        )
        heading = re.sub(
            r"(?i)\s+e\s+(?:quais?|como|quando)\b.*$",
            "",
            heading,
        )
        effective = re.match(
            r"(?i)^quais\s+(.+?)\s+são\s+mais\s+eficazes\s+para\s+(.+)$",
            heading,
        )
        relational = re.match(
            r"(?i)^quais\s+(.+?)\s+(favorecem|influenciam|afetam)\s+(.+)$",
            heading,
        )
        if effective:
            heading = f"{effective.group(1)} para {effective.group(2)}"
        elif relational:
            heading = (
                f"{relational.group(1)} que {relational.group(2)} {relational.group(3)}"
            )
        else:
            heading = re.sub(
                r"(?i)^o\s+que\s+é\s+(?:o|a|um|uma)\s+",
                "",
                heading,
            )
            heading = re.sub(
                r"(?i)^como\s+deve\s+ser\s+(?:o|a)\s+",
                "",
                heading,
            )
            heading = re.sub(
                r"(?i)^momento\s+adequado\s+para\s+",
                "Quando ",
                heading,
            )
            heading = re.sub(
                r"(?i)^tempo\s+médio\s+de\s+desenvolvimento\s+durante\s+",
                "Tempo de ",
                heading,
            )
            heading = re.sub(
                r"(?i)^principais\s+erros\s+cometidos\s+",
                "Erros comuns ",
                heading,
            )
            heading = re.sub(
                r"(?i)^quais\s+erros\s+(?:devem\s+ser\s+)?(?:evitar|evitados?)\s*",
                "Erros a evitar ",
                heading,
            )
            heading = re.sub(r"(?i)^quais\s+são\s+(?:os|as)\s+", "", heading)
            heading = re.sub(r"(?i)^qual\s+é\s+(?:o|a)\s+", "", heading)
        heading = heading.strip()
        if len(heading) > 80:
            words = heading.split()
            shortened = ""
            for word in words:
                candidate = f"{shortened} {word}".strip()
                if len(candidate) > 80:
                    break
                shortened = candidate
            heading = shortened or heading[:80].rstrip()
        if heading:
            heading = heading[0].upper() + heading[1:]
        return heading or "Pontos verificados"

    @staticmethod
    def _keyword_candidates(state: PipelineState) -> list[str]:
        plan = state.plan or {}
        return list(
            dict.fromkeys(
                value
                for raw in (
                    list(plan.get("google_keywords", []))
                    + list(plan.get("semantic_keywords", []))
                )
                if (value := re.sub(r"\s+", " ", str(raw)).strip().rstrip("?"))
            )
        )

    @staticmethod
    def _keyword_tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[\wÀ-ÿ]+", value.casefold())
            if len(token) > 2
        }

    @classmethod
    def _best_keyword(cls, context: str, candidates: list[str]) -> str | None:
        context_tokens = cls._keyword_tokens(context)
        eligible = [
            value
            for value in candidates
            if 3 <= len(value) <= 90 and 2 <= len(value.split()) <= 10
        ]
        if not eligible:
            return None
        best = max(
            eligible,
            key=lambda value: (
                len(context_tokens & cls._keyword_tokens(value)),
                -abs(len(value) - min(55, len(context))),
                -candidates.index(value),
            ),
        )
        if context_tokens and not (context_tokens & cls._keyword_tokens(best)):
            return None
        return best

    @classmethod
    def _seo_keyphrases(
        cls,
        state: PipelineState,
        title: str,
    ) -> tuple[str, list[str]]:
        brief = (state.plan or {}).get("seo_brief") or {}
        candidates = cls._keyword_candidates(state)
        focus = str(brief.get("focus_keyphrase") or "").strip()
        if not focus:
            focus = cls._best_keyword(title, candidates) or title.strip()
        related = list(
            dict.fromkeys(
                str(value).strip()
                for value in (list(brief.get("related_keyphrases") or []) + candidates)
                if str(value).strip()
                and str(value).strip().casefold() != focus.casefold()
            )
        )[:5]
        return focus[:90], related

    @classmethod
    def _seo_title(cls, state: PipelineState, title: str) -> str:
        focus, _ = cls._seo_keyphrases(state, title)
        normalized = re.sub(r"\s+", " ", str(title or "")).strip().rstrip("?")
        focus_tokens = cls._keyword_tokens(focus)
        if (
            not normalized
            or not focus_tokens <= cls._keyword_tokens(normalized)
            or normalized.casefold().count(focus.casefold()) != 1
        ):
            normalized = f"{focus}: guia prático"
        if len(normalized) < 15:
            normalized = f"{normalized}: guia completo"
        return cls._truncate_at_word(normalized, 60)

    @classmethod
    def _ensure_seo_heading_structure(
        cls,
        state: PipelineState,
        output: dict,
        approved_facts: list[dict],
    ) -> dict:
        output["title"] = cls._seo_title(state, str(output.get("title") or ""))
        internal_questions = {
            re.sub(r"\s+", " ", str(question.get("question") or ""))
            .strip()
            .rstrip("?.!:;")
            .casefold()
            for question in (state.plan or {}).get("questions", [])
            if str(question.get("question") or "").strip()
        }
        for block in output.get("blocks", []):
            if block.get("type") == "h1":
                block["type"] = "h2"
            if block.get("type") not in {"h2", "h3"}:
                continue
            for sentence in block.get("sentences", []):
                text = re.sub(r"\s+", " ", str(sentence.get("text") or "")).strip()
                normalized_text = text.rstrip("?.!:;").casefold()
                if text.endswith("?") or normalized_text in internal_questions:
                    sentence["text"] = cls._editorial_heading(text)
                sentence["is_factual"] = False
                sentence["evidence"] = []
        return WriterOutput.model_validate(output).model_dump(mode="json")

    @staticmethod
    def _contains_visible_source_credit(
        output: dict,
        approved_facts: list[dict],
    ) -> bool:
        visible = " ".join(
            [str(output.get("title") or "")]
            + [
                str(sentence.get("text") or "")
                for block in output.get("blocks", [])
                for sentence in block.get("sentences", [])
            ]
        )
        return PipelineExecutor._text_contains_source_credit(visible, approved_facts)

    async def _persist_deterministic_editorial_repair(
        self,
        state: PipelineState,
        approved_facts: list[dict],
        *,
        editor_run_id: uuid.UUID,
        editor_output: dict,
    ) -> dict:
        """Preserve the last valid article and remove only exact grave sentences."""
        repair_run_id = self._agent_run_id("editorial_repair", state.editor_cycle + 1)
        existing = await self.db.get(AgentRun, repair_run_id)
        if existing is not None and existing.output_json:
            repaired = existing.output_json
            removed_count = int(
                (existing.feedback or {}).get("removed_sentence_count", 0)
            )
        else:
            started_at = datetime.now(timezone.utc)
            prior = copy.deepcopy(state.draft or {})
            repaired, removed_count = self._recover_targeted_revision(
                prior,
                editor_output,
            )
            repaired = self._ensure_seo_heading_structure(
                state,
                repaired,
                approved_facts,
            )
            repaired = WriterOutput.model_validate(repaired).model_dump(mode="json")
            finished_at = datetime.now(timezone.utc)
            self.db.add(
                AgentRun(
                    id=repair_run_id,
                    project_id=self.project.id,
                    pipeline_run_id=self.pipeline_run.id,
                    idempotency_key=f"editorial_repair:{repair_run_id}",
                    agent_role="editorial_repair",
                    attempt=state.editor_cycle + 1,
                    status="succeeded",
                    input_json={
                        "editor_run_id": str(editor_run_id),
                        "reason": "grave_findings_after_targeted_rewrite",
                        "finding_count": len(
                            self._blocking_editor_findings(editor_output)
                        ),
                    },
                    output_json=repaired,
                    decision="approved",
                    provider="deterministic",
                    model="targeted-sentence-removal-v2",
                    started_at=started_at,
                    finished_at=finished_at,
                    latency_ms=max(
                        0,
                        int((finished_at - started_at).total_seconds() * 1000),
                    ),
                    feedback={
                        "resolution": "deterministic_targeted_repair",
                        "removed_sentence_count": removed_count,
                        "article_preserved": True,
                    },
                )
            )
            await self.db.flush()
        await self.versions.persist_draft(
            self.project,
            self.pipeline_run,
            repaired,
            repair_run_id,
        )
        await self.runtime.event(
            self.project.id,
            self.pipeline_run.id,
            "editorial.targeted_repair_completed",
            "editor",
            {
                "message": (
                    "Somente as frases graves identificadas pelo revisor foram "
                    "removidas; o restante do artigo foi preservado."
                ),
                "removed_sentence_count": removed_count,
                "article_preserved": True,
                "status": "succeeded",
            },
            idempotency_key=f"editorial.targeted_repair_completed:{repair_run_id}",
            context=(
                self._stage_context.with_agent(repair_run_id)
                if self._stage_context
                else None
            ),
        )
        return repaired

    async def _mark_current_draft_approved(self, state: PipelineState) -> None:
        article = await self.db.scalar(
            select(Article).where(Article.project_id == self.project.id)
        )
        version = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
                ArticleVersion.pipeline_run_id == self.pipeline_run.id,
            )
        )
        if version is None:
            raise ValueError("Editorial approval has no current article version")
        version.editorial_status = "machine_approved"
        block_ids = [uuid.UUID(b["block_id"]) for b in state.draft["blocks"]]
        sentences = (
            await self.db.scalars(
                select(SentenceClaim)
                .join(ArticleBlock, SentenceClaim.block_id == ArticleBlock.id)
                .where(
                    ArticleBlock.article_version_id == version.id,
                    ArticleBlock.logical_block_id.in_(block_ids),
                )
            )
        ).all()
        for sentence in sentences:
            sentence.fidelity_status = "approved"
        evidence_rows = (
            await self.db.scalars(
                select(ClaimEvidence)
                .join(SentenceClaim)
                .join(ArticleBlock, SentenceClaim.block_id == ArticleBlock.id)
                .where(
                    ArticleBlock.article_version_id == version.id,
                    ArticleBlock.logical_block_id.in_(block_ids),
                )
            )
        ).all()
        for evidence in evidence_rows:
            evidence.reviewer_approved = True

    @staticmethod
    def _quote_exists(quote: str, content: str) -> bool:
        def normalize(value: str) -> str:
            value = unicodedata.normalize("NFKC", value)
            value = value.translate(
                str.maketrans(
                    {
                        "“": '"',
                        "”": '"',
                        "‘": "'",
                        "’": "'",
                        "–": "-",
                        "—": "-",
                        "‑": "-",
                        "\u00a0": " ",
                    }
                )
            )
            return re.sub(r"\s+", " ", value).strip().casefold()

        return normalize(quote) in normalize(content)

    def _agent_run_id(self, role: str, attempt: int) -> uuid.UUID:
        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"pipeline:{self.pipeline_run.id}:{role}:{attempt}",
        )

    @staticmethod
    def _research_attempt(state: PipelineState) -> int:
        return state.research_cycle + 1

    @staticmethod
    def _prior_draft(state: PipelineState) -> dict | None:
        return state.draft if state.rewrite_block_ids else None

    @staticmethod
    def _slug(value: str) -> str:
        value = unicodedata.normalize("NFKD", value.casefold())
        value = (
            "".join(
                character for character in value if not unicodedata.combining(character)
            )
            .encode("ascii", "ignore")
            .decode()
        )
        return re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:80] or "conteudo"

    @staticmethod
    def _truncate_at_word(value: str, limit: int) -> str:
        value = re.sub(r"\s+", " ", value).strip()
        if len(value) <= limit:
            return value
        shortened = value[: limit + 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return shortened or value[:limit].rstrip()
