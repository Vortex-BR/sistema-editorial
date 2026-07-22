import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.sanitization import sanitize_nul
from app.db.models import (
    AgentRun,
    Article,
    ArticleVersion,
    FactLedger,
    HumanEditorialReview,
    PipelineRun,
    PipelineRunStatus,
    Project,
    ProjectStatus,
    QualityEvaluation,
    ResearchPlan,
    ResearchQuestion,
    SourceSnapshot,
    TriggerType,
)
from app.services.fact_conflicts import unresolved_fact_conflicts
from app.services.editorial_seal import (
    EditorialSealError,
    article_version_checksum,
    review_package_checksum,
    validate_review_seal,
)
from app.services.pipeline_control import EventService, PipelineRunService
from app.services.quality_evaluator import QualityEvaluator, quality_summary


class HumanReviewInputError(ValueError):
    pass


class HumanReviewConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class HumanReviewDecisionResult:
    review: HumanEditorialReview
    run: PipelineRun
    revision_run: PipelineRun | None = None
    revision_created: bool = False
    duplicate: bool = False


class HumanEditorialReviewService:
    decisions = frozenset({"approve", "reject", "request_revision"})

    def __init__(self, db: AsyncSession):
        self.db = db
        self.runs = PipelineRunService(db)
        self.quality = QualityEvaluator(db)

    async def ensure_pending(
        self, project: Project, run: PipelineRun
    ) -> HumanEditorialReview:
        existing = await self.db.scalar(
            select(HumanEditorialReview).where(
                HumanEditorialReview.pipeline_run_id == run.id
            )
        )
        if existing is not None:
            return existing
        article = await self.db.scalar(
            select(Article).where(Article.project_id == project.id)
        )
        if article is None or not article.current_version:
            raise HumanReviewInputError("Final article is not available for human review")
        version = await self.db.scalar(
            select(ArticleVersion).where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version == article.current_version,
                ArticleVersion.pipeline_run_id == run.id,
            )
        )
        if (
            version is None
            or article.active_pipeline_run_id != run.id
            or not (version.final_markdown or "").strip()
        ):
            raise HumanReviewInputError(
                "Current article version does not belong to the completed automation run"
            )
        quality_evaluation = await self.quality.evaluate(
            project, run, article, version
        )
        content_checksum = article_version_checksum(version)
        version.content_checksum = content_checksum
        package = await self.build_review_package(
            project,
            run,
            article,
            version,
            quality_evaluation=quality_evaluation,
        )
        package["article_version_checksum"] = content_checksum
        review = HumanEditorialReview(
            project_id=project.id,
            pipeline_run_id=run.id,
            article_version_id=version.id,
            decision="pending",
            review_package_json=package,
            review_package_checksum=review_package_checksum(package),
        )
        self.db.add(review)
        await self.db.flush()
        return review

    async def build_review_package(
        self,
        project: Project,
        run: PipelineRun,
        article: Article,
        version: ArticleVersion,
        *,
        quality_evaluation: QualityEvaluation | None = None,
    ) -> dict:
        questions = list(
            (
                await self.db.scalars(
                    select(ResearchQuestion)
                    .join(ResearchPlan, ResearchPlan.id == ResearchQuestion.plan_id)
                    .where(
                        ResearchPlan.project_id == project.id,
                        ResearchPlan.pipeline_run_id == run.id,
                    )
                    .order_by(
                        ResearchQuestion.priority,
                        ResearchQuestion.created_at,
                        ResearchQuestion.id,
                    )
                )
            ).all()
        )
        fact_rows = list(
            (
                await self.db.execute(
                    select(FactLedger, SourceSnapshot, ResearchQuestion)
                    .join(
                        SourceSnapshot,
                        SourceSnapshot.id == FactLedger.source_snapshot_id,
                    )
                    .join(
                        ResearchQuestion,
                        ResearchQuestion.id == FactLedger.research_question_id,
                    )
                    .where(
                        FactLedger.project_id == project.id,
                        FactLedger.pipeline_run_id == run.id,
                    )
                    .order_by(FactLedger.created_at, FactLedger.id)
                )
            ).all()
        )
        editor_run = await self.db.scalar(
            select(AgentRun)
            .where(
                AgentRun.project_id == project.id,
                AgentRun.pipeline_run_id == run.id,
                AgentRun.agent_role == "editor",
                AgentRun.status == "succeeded",
            )
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        previous = await self.db.scalar(
            select(ArticleVersion)
            .where(
                ArticleVersion.article_id == article.id,
                ArticleVersion.version < version.version,
            )
            .order_by(ArticleVersion.version.desc())
            .limit(1)
        )

        sources: dict[str, dict] = {}
        facts = []
        fact_items_by_id: dict[str, dict] = {}
        for fact, snapshot, question in fact_rows:
            source_key = str(snapshot.id)
            sources[source_key] = {
                "id": str(fact.source_id),
                "snapshot_id": str(snapshot.id),
                "title": snapshot.title,
                "url": snapshot.canonical_url,
                "domain": snapshot.domain,
                "author": snapshot.author,
                "publisher": snapshot.publisher,
                "published_at": (
                    snapshot.published_at.isoformat()
                    if snapshot.published_at is not None
                    else None
                ),
                "source_type": snapshot.source_type,
                "reliability_score": snapshot.reliability_score,
                "content_hash": snapshot.content_hash,
                "captured_at": snapshot.accessed_at.isoformat(),
                "extraction_method": snapshot.extraction_method,
                "search_markets": list(
                    (getattr(snapshot, "metadata_json", None) or {}).get(
                        "search_markets"
                    )
                    or []
                ),
                "source_country": (
                    getattr(snapshot, "metadata_json", None) or {}
                ).get("source_country"),
            }
            item = {
                "id": str(fact.id),
                "question_id": str(question.id),
                "question": question.question,
                "claim": fact.claim_text,
                "source_id": str(fact.source_id),
                "source_snapshot_id": str(snapshot.id),
                "confidence": fact.confidence_score,
                "approved": fact.approved,
                "conflict_group": fact.conflict_group,
                "superseded": fact.superseded_by_id is not None,
            }
            facts.append(item)
            fact_items_by_id[item["id"]] = item

        coverage_questions = [
            {
                "id": str(question.id),
                "question": question.question,
                "priority": question.priority,
                "coverage_status": getattr(
                    question.coverage_status,
                    "value",
                    question.coverage_status,
                ),
            }
            for question in questions
        ]
        coverage_complete = bool(coverage_questions) and all(
            item["coverage_status"] == "covered" for item in coverage_questions
        )
        unresolved_conflicts = unresolved_fact_conflicts(
            (fact for fact, _snapshot, _question in fact_rows),
            project_id=project.id,
            pipeline_run_id=run.id,
            valid_fact_ids=(
                fact.id for fact, _snapshot, _question in fact_rows if fact.approved
            ),
        )
        conflicts = [
            {
                "group": conflict.group,
                "active_fact_ids": list(conflict.active_fact_ids),
                "claims": [
                    fact_items_by_id[fact_id]
                    for fact_id in conflict.active_fact_ids
                ],
            }
            for conflict in unresolved_conflicts
        ]
        editor_output = (editor_run.output_json or {}) if editor_run else {}
        findings = [
            *editor_output.get("fidelity_findings", []),
            *editor_output.get("language_findings", []),
        ]
        unsupported_claims = int(
            (version.source_report or {}).get("unsupported_claim_count", 0) or 0
        )
        risks: list[dict] = []
        if not coverage_complete:
            risks.append(
                {
                    "code": "coverage_incomplete",
                    "message": "A cobertura determinística não está completa.",
                }
            )
        if conflicts:
            risks.append(
                {
                    "code": "unresolved_conflicts",
                    "message": "Há conflitos factuais não resolvidos no pacote.",
                }
            )
        if unsupported_claims:
            risks.append(
                {
                    "code": "unsupported_claims",
                    "message": "O pacote informa afirmações sem suporte.",
                    "count": unsupported_claims,
                }
            )
        for finding in findings:
            severity = str(finding.get("severity", "unknown")).lower()
            if severity in {"major", "critical"}:
                risks.append(
                    {
                        "code": "editor_finding",
                        "severity": severity,
                        "message": str(
                            finding.get("message")
                            or finding.get("description")
                            or "Risco editorial sinalizado pelo editor automático."
                        ),
                    }
                )
        quality = quality_summary(quality_evaluation)
        for blocker in (quality or {}).get("critical_blockers", []):
            risks.append(
                {
                    "code": blocker.get("code", "quality_blocker"),
                    "severity": "critical",
                    "message": "A avaliação independente detectou um bloqueio crítico.",
                }
            )
        current_markdown = version.final_markdown or ""
        # Versions created before finalization can legitimately keep this
        # field NULL. Treat that state as an empty comparison baseline.
        previous_markdown = (previous.final_markdown or "") if previous else ""
        return sanitize_nul(
            {
                "article_version_id": str(version.id),
                "article_version": version.version,
                "article_version_checksum": article_version_checksum(version),
                "pipeline_run_id": str(run.id),
                "facts": facts,
                "sources": list(sources.values()),
                "coverage": {
                    "complete": coverage_complete,
                    "questions": coverage_questions,
                },
                "conflicts": conflicts,
                "seo": version.seo_metadata or {},
                "quality_evaluation": quality,
                "changes": {
                    "previous_version": previous.version if previous else None,
                    "current_version": version.version,
                    "previous_title": previous.title if previous else None,
                    "current_title": version.title,
                    "previous_outline": previous.outline if previous else None,
                    "current_outline": version.outline,
                    "title_changed": previous.title != version.title if previous else True,
                    "outline_changed": (
                        previous.outline != version.outline if previous else True
                    ),
                    "markdown_changed": (
                        previous_markdown != current_markdown if previous else True
                    ),
                    "character_delta": len(current_markdown) - len(previous_markdown),
                    "change_reason": version.change_reason,
                },
                "risks": risks,
            }
        )

    async def decide(
        self,
        run_id: uuid.UUID,
        *,
        decision: str,
        reviewer: str,
        observation: str | None,
        idempotency_key: str,
    ) -> HumanReviewDecisionResult:
        if decision not in self.decisions:
            raise HumanReviewInputError("Unknown human review decision")
        reviewer = sanitize_nul(reviewer, strip_escaped=True).strip()
        observation = sanitize_nul(observation, strip_escaped=True)
        idempotency_key = sanitize_nul(idempotency_key, strip_escaped=True).strip()
        if not reviewer:
            raise HumanReviewInputError("A human reviewer identity is required")
        if not idempotency_key:
            raise HumanReviewInputError("Idempotency-Key is required")
        if len(idempotency_key) > 160:
            raise HumanReviewInputError("Idempotency-Key is too long")
        if decision in {"reject", "request_revision"} and not (
            observation or ""
        ).strip():
            raise HumanReviewInputError(
                "A reason or revision instruction is required for this decision"
            )

        run = await self.runs.acquire(run_id)
        review = await self.db.scalar(
            select(HumanEditorialReview)
            .where(HumanEditorialReview.pipeline_run_id == run.id)
            .with_for_update()
        )
        if review is None:
            raise HumanReviewInputError("Human review package not found")
        stored_decision = {
            "approve": "approved",
            "reject": "rejected",
            "request_revision": "revision_requested",
        }[decision]
        if review.decision != "pending":
            if (
                review.decision_idempotency_key == idempotency_key
                and review.decision == stored_decision
            ):
                revision_run = (
                    await self.db.get(PipelineRun, review.revision_run_id)
                    if review.revision_run_id
                    else None
                )
                return HumanReviewDecisionResult(
                    review=review,
                    run=run,
                    revision_run=revision_run,
                    duplicate=True,
                )
            raise HumanReviewConflict("This review already has a final decision")
        if PipelineRunStatus(run.status) != PipelineRunStatus.needs_human_approval:
            raise HumanReviewConflict("Pipeline run is not awaiting human approval")

        project = await self.db.get(Project, run.project_id)
        version = await self.db.get(ArticleVersion, review.article_version_id)
        article = (
            await self.db.get(Article, version.article_id) if version is not None else None
        )
        if project is None or version is None or article is None:
            raise HumanReviewInputError("Human review references are inconsistent")
        if (
            article.current_version != version.version
            or version.pipeline_run_id != run.id
            or article.active_pipeline_run_id != run.id
        ):
            raise HumanReviewConflict(
                "Human review no longer targets the current article version"
            )
        if decision == "approve":
            try:
                validate_review_seal(version, review, require_sealed=False)
            except EditorialSealError as exc:
                raise HumanReviewConflict(
                    "Reviewed content integrity could not be verified"
                ) from exc
        quality_evaluation = await self.db.scalar(
            select(QualityEvaluation).where(
                QualityEvaluation.pipeline_run_id == run.id
            )
        )
        if decision == "approve" and (
            quality_evaluation is None or quality_evaluation.status == "blocked"
        ):
            raise HumanReviewConflict(
                "Critical independent quality blockers must be resolved first"
            )
        if decision == "request_revision":
            other_active = await self.db.scalar(
                select(PipelineRun.id)
                .where(
                    PipelineRun.project_id == run.project_id,
                    PipelineRun.id != run.id,
                    PipelineRun.status.in_(
                        {
                            PipelineRunStatus.queued,
                            PipelineRunStatus.running,
                            PipelineRunStatus.waiting_retry,
                        }
                    ),
                )
                .limit(1)
            )
            if other_active is not None:
                raise HumanReviewConflict(
                    "Another pipeline run is already active for this project"
                )

        review.reviewer = reviewer
        review.decision = stored_decision
        review.observation = observation.strip() if observation else None
        review.reviewed_at = datetime.now(timezone.utc)
        review.decision_idempotency_key = idempotency_key
        revision_run = None
        revision_created = False
        if decision == "approve":
            version.editorial_status = "human_approved"
            version.sealed_at = review.reviewed_at
            article.status = "approved"
            run = await self.runs.transition(
                run.id,
                PipelineRunStatus.completed,
                origin="admin.human-review",
                reason=review.observation or "Approved by human editor-in-chief",
                stage="completed",
                expected_lock_version=run.lock_version,
            )
            project.status = ProjectStatus.completed
            project.current_stage = "completed"
        elif decision == "reject":
            version.editorial_status = "rejected"
            article.status = "rejected"
            run = await self.runs.transition(
                run.id,
                PipelineRunStatus.rejected,
                origin="admin.human-review",
                reason=review.observation,
                stage="human_approval",
                expected_lock_version=run.lock_version,
            )
            project.status = ProjectStatus.rejected
            project.current_stage = "human_approval"
        else:
            version.editorial_status = "revision_requested"
            article.status = "revision_requested"
            run = await self.runs.transition(
                run.id,
                PipelineRunStatus.needs_review,
                origin="admin.human-review",
                reason=review.observation,
                stage="human_approval",
                expected_lock_version=run.lock_version,
            )
            project.status = ProjectStatus.needs_review
            project.current_stage = "human_approval"
            revision_run, revision_created = await self.runs.create(
                project.id,
                f"human-review:{review.id}:revision",
                trigger_type=TriggerType.resume,
                metadata={
                    "human_revision": {
                        "review_id": str(review.id),
                        "parent_pipeline_run_id": str(run.id),
                        "parent_article_version_id": str(version.id),
                        "reviewer": reviewer,
                        "instructions": review.observation,
                    }
                },
            )
            review.revision_run_id = revision_run.id
            project.status = ProjectStatus.queued
            project.current_stage = "planner"

        await EventService(self.db).append(
            project.id,
            run.id,
            f"human_review.{stored_decision}",
            "human_approval",
            {
                "message": self._decision_message(stored_decision),
                "review_id": str(review.id),
                "reviewer": reviewer,
                "revision_run_id": str(revision_run.id) if revision_run else None,
            },
            idempotency_key=f"human-review:{review.id}:{stored_decision}",
        )
        await self.db.flush()
        return HumanReviewDecisionResult(
            review=review,
            run=run,
            revision_run=revision_run,
            revision_created=revision_created,
        )

    @staticmethod
    def _decision_message(decision: str) -> str:
        return {
            "approved": "Conteúdo aprovado pelo editor-chefe humano",
            "rejected": "Conteúdo rejeitado pelo editor-chefe humano",
            "revision_requested": "Editor-chefe humano solicitou nova revisão",
        }[decision]
