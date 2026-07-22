"""Persistence and evidence validation for executable Editorial V3 artifacts."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ContentKnowledgeContractRecord,
    FactLedger,
    KnowledgeGapRecord,
    QualityEvaluation,
    ResearchPlan,
    ResearchQuestion,
    V3DecisionMatrixRecord,
    V3KnowledgeClaimRecord,
    V3MethodDossierRecord,
    V3ProceduralQualityRecord,
    V3SectionDossierRecord,
    V3SourceDocumentRecord,
    V3StageReviewRecord,
)
from app.schemas.editorial_v3 import (
    ConclusionStatus,
    DecisionMatrix,
    DecisionRule,
    ExternalReference,
    GapResolutionStatus,
    KnowledgeClaim,
    KnowledgeGap,
    MethodDossier,
    ProcedureStep,
    SectionDossier,
    SourceAssessment,
    SourceUsagePolicy,
    SupportedCorrection,
)
from app.schemas.editorial_v3_runtime import (
    DraftDecisionMatrix,
    DraftKnowledgeGap,
    DraftMethodDossier,
    DraftSectionDossier,
    ExtractedKnowledgeClaimCandidate,
    ProceduralQualityEvaluation,
    StructuredSourceDocument,
    V3ResearchPlan,
)
from app.services.editorial_v3.source_assessment_repository import (
    SourceAssessmentRepository,
)
from app.services.editorial_v3.source_policy import ResearchSourcePolicyService
from app.services.editorial_v3.text_integrity import (
    normalized_text,
    quote_is_present,
    support_group_compatible,
)
from app.services.research_engine import SearchDocument, canonicalize_url
from app.services.research_ledger import ResearchLedgerService


_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9áàâãéèêíïóôõöúçñ]+", re.I)


def _checksum(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _norm(value: str) -> str:
    return _SPACE.sub(" ", value or "").strip().casefold()


def _canonical_claim_id(pipeline_run_id: uuid.UUID, support_group: str) -> uuid.UUID:
    normalized = normalized_text(support_group)
    return uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"editorial-intelligence:{pipeline_run_id}:claim:{normalized}",
    )


def _conservative_status(values: list[str]) -> str:
    risk = {
        ConclusionStatus.confirmed.value: 0,
        ConclusionStatus.well_supported.value: 1,
        ConclusionStatus.conditional.value: 2,
        ConclusionStatus.disputed.value: 3,
        ConclusionStatus.insufficient_evidence.value: 4,
    }
    return max(values or [ConclusionStatus.insufficient_evidence.value], key=lambda item: risk.get(item, 4))


@dataclass(frozen=True)
class PersistedResearchPlan:
    row: ResearchPlan
    questions_by_task_id: dict[str, ResearchQuestion]


class V3ArtifactRepository:
    def __init__(self, db: AsyncSession, *, project_id: uuid.UUID, pipeline_run_id: uuid.UUID):
        self.db = db
        self.project_id = project_id
        self.pipeline_run_id = pipeline_run_id
        self.ledger = ResearchLedgerService(db, project_id, pipeline_run_id)
        self.source_policy = ResearchSourcePolicyService()
        self.source_assessments = SourceAssessmentRepository(
            db, policy=self.source_policy
        )

    async def research_plan(self, plan: V3ResearchPlan) -> PersistedResearchPlan:
        key = "editorial-v3-research-plan"
        existing = await self.db.scalar(
            select(ResearchPlan).where(
                ResearchPlan.pipeline_run_id == self.pipeline_run_id,
                ResearchPlan.idempotency_key == key,
            )
        )
        if existing is None:
            latest = await self.db.scalar(
                select(func.coalesce(func.max(ResearchPlan.version), 0)).where(
                    ResearchPlan.project_id == self.project_id
                )
            )
            existing = ResearchPlan(
                project_id=self.project_id,
                pipeline_run_id=self.pipeline_run_id,
                idempotency_key=key,
                version=int(latest or 0) + 1,
                status="approved",
                rationale=plan.rationale,
                semantic_keywords=list(dict.fromkeys(plan.method_discovery_queries + plan.terminology_queries)),
                competitor_angles=[],
                content_gaps=[],
                seo_brief={"pipeline": "editorial-v3"},
                editorial_blueprint=plan.model_dump(mode="json"),
            )
            self.db.add(existing)
            await self.db.flush()
            for index, task in enumerate(plan.tasks, start=1):
                question = ResearchQuestion(
                    plan_id=existing.id,
                    question=task.research_goal,
                    priority=min(7, max(1, 1 + (index - 1) % 7)),
                    importance="core" if task.critical else "supporting",
                    rationale=task.rationale,
                    expected_source_types=task.required_source_roles,
                    semantic_terms=[task.evidence_role.value, task.knowledge_node_id, *(task.method_hint and [task.method_hint] or [])],
                    search_queries={"v3": task.queries, "task_id": task.task_id},
                    coverage_status="uncovered",
                )
                self.db.add(question)
            await self.db.flush()
        questions = (
            await self.db.scalars(
                select(ResearchQuestion).where(ResearchQuestion.plan_id == existing.id)
            )
        ).all()
        mapped = {
            str((question.search_queries or {}).get("task_id")): question
            for question in questions
            if (question.search_queries or {}).get("task_id")
        }
        return PersistedResearchPlan(existing, mapped)

    @staticmethod
    def _source_document_record_id(
        pipeline_run_id: uuid.UUID,
        url_hash: str,
        content_hash: str,
    ) -> uuid.UUID:
        """Return the run-scoped, retry-stable primary key for a source row.

        ``StructuredSourceDocument.document_id`` is a content identity generated by
        the parser and is intentionally stable across projects.  The database row,
        however, belongs to one pipeline run.  Reusing the parser ID as a global
        primary key caused a second run that found the same source to fail with
        ``pk_v3_source_documents``.  Including the run and the table's natural
        unique key keeps retries idempotent without colliding across runs.
        """

        return uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "editorial-v3:source-document:"
                f"{pipeline_run_id}:{url_hash}:{content_hash}"
            ),
        )

    async def source_document(
        self,
        contract_id: uuid.UUID,
        document: StructuredSourceDocument,
    ) -> V3SourceDocumentRecord:
        canonical_url = str(document.canonical_url)
        url_hash = hashlib.sha256(canonical_url.encode()).hexdigest()
        lookup = select(V3SourceDocumentRecord).where(
            V3SourceDocumentRecord.pipeline_run_id == self.pipeline_run_id,
            V3SourceDocumentRecord.url_hash == url_hash,
            V3SourceDocumentRecord.content_hash == document.content_hash,
        )
        row = await self.db.scalar(lookup)
        usage = document.assessment.usage_policy
        status = {
            SourceUsagePolicy.rejected: "rejected",
            SourceUsagePolicy.comparison_only: "comparison_only",
            SourceUsagePolicy.discovery_only: "discovery_only",
        }.get(usage, "accepted")

        if row is None:
            record_id = self._source_document_record_id(
                self.pipeline_run_id,
                url_hash,
                document.content_hash,
            )
            normalized_document = document.model_copy(
                update={"document_id": record_id}
            )
            values = {
                "id": record_id,
                "contract_id": contract_id,
                "pipeline_run_id": self.pipeline_run_id,
                "canonical_url": canonical_url,
                "url_hash": url_hash,
                "title": document.title,
                "document_type": document.document_type.value,
                "source_role": document.assessment.source_role.value,
                "usage_policy": usage.value,
                "content_hash": document.content_hash,
                "document_json": normalized_document.model_dump(mode="json"),
                "assessment_json": document.assessment.model_dump(mode="json"),
                "status": status,
            }
            # The scheduler can redeliver a run after a lease recovery.  A
            # database-level conflict guard is therefore required in addition to
            # the optimistic SELECT above; two workers persisting the same source
            # must converge on one row instead of poisoning the transaction.
            await self.db.execute(
                pg_insert(V3SourceDocumentRecord)
                .values(**values)
                .on_conflict_do_nothing(
                    constraint="uq_v3_source_document_run_url_content"
                )
            )
            row = await self.db.scalar(lookup)
            if row is None:
                raise RuntimeError(
                    "Source document upsert completed without a persisted row"
                )

        # Reconcile legacy/checkpoint rows and refresh the latest deterministic
        # representation.  In particular, older deployments may already have a
        # row whose ID came directly from the parser.
        normalized_document = document.model_copy(update={"document_id": row.id})
        row.contract_id = contract_id
        row.canonical_url = canonical_url
        row.url_hash = url_hash
        row.title = document.title
        row.document_type = document.document_type.value
        row.source_role = document.assessment.source_role.value
        row.usage_policy = usage.value
        row.content_hash = document.content_hash
        row.document_json = normalized_document.model_dump(mode="json")
        row.assessment_json = document.assessment.model_dump(mode="json")
        row.status = status
        await self.db.flush()

        if document.source_signals is not None:
            await self.source_assessments.materialize(
                contract_id=contract_id,
                pipeline_run_id=self.pipeline_run_id,
                signals=document.source_signals,
            )
        return row

    async def claim(
        self,
        *,
        contract_id: uuid.UUID,
        candidate: ExtractedKnowledgeClaimCandidate,
        task_question: ResearchQuestion,
        source: SearchDocument,
        structured: StructuredSourceDocument,
        source_row: V3SourceDocumentRecord,
    ) -> V3KnowledgeClaimRecord | None:
        existing = await self.db.scalar(
            select(V3KnowledgeClaimRecord).where(
                V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run_id,
                V3KnowledgeClaimRecord.claim_key == candidate.claim_key,
            )
        )
        if existing is not None:
            return existing
        candidate_url = canonicalize_url(str(candidate.source_url))
        accepted_urls = {
            canonicalize_url(str(structured.url)),
            canonicalize_url(str(structured.canonical_url)),
            canonicalize_url(source.url),
        }
        if candidate_url not in accepted_urls:
            return None
        if not quote_is_present(candidate.exact_quote, structured.plain_text):
            return None
        assessment = structured.assessment
        if assessment.usage_policy == SourceUsagePolicy.rejected:
            return None
        if candidate.evidence_role not in assessment.allowed_evidence_roles:
            return None
        fact = await self.ledger.persist_fact(
            task_question.id,
            source,
            {
                "claim_text": candidate.claim_text,
                "exact_quote": candidate.exact_quote,
                "source_locator": candidate.source_locator[:255],
                "confidence_score": min(candidate.confidence_score, assessment.priority_score),
                "conflict_group": candidate.conflict_group,
            },
        )
        if fact is None:
            return None
        row = V3KnowledgeClaimRecord(
            contract_id=contract_id,
            pipeline_run_id=self.pipeline_run_id,
            source_document_id=source_row.id,
            fact_id=fact.id,
            canonical_claim_id=_canonical_claim_id(self.pipeline_run_id, candidate.support_group),
            claim_key=candidate.claim_key,
            support_group=candidate.support_group,
            knowledge_node_key=candidate.knowledge_node_id,
            evidence_role=candidate.evidence_role.value,
            claim_text=candidate.claim_text,
            exact_quote=candidate.exact_quote,
            source_locator=candidate.source_locator,
            method_ids=candidate.method_labels,
            conditions=candidate.conditions,
            applicability=candidate.applicability,
            limitations=candidate.limitations,
            conclusion_status=candidate.conclusion_status.value,
            confidence_score=min(candidate.confidence_score, assessment.priority_score),
            critical=candidate.critical,
            conflict_group=candidate.conflict_group,
            approved=False,
            validation_json={
                "quote_verified": True,
                "source_assessment": assessment.model_dump(mode="json"),
            },
        )
        self.db.add(row)
        await self.db.flush()
        return row

    async def approve_claim_bundles(
        self, *, procedural_context: bool = False,
    ) -> list[V3KnowledgeClaimRecord]:
        rows = list(
            (
                await self.db.scalars(
                    select(V3KnowledgeClaimRecord).where(
                        V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run_id
                    )
                )
            ).all()
        )
        documents = {
            row.id: row
            for row in (
                await self.db.scalars(
                    select(V3SourceDocumentRecord).where(
                        V3SourceDocumentRecord.pipeline_run_id == self.pipeline_run_id
                    )
                )
            ).all()
        }
        grouped: dict[str, list[V3KnowledgeClaimRecord]] = defaultdict(list)
        for row in rows:
            grouped[row.support_group].append(row)

        approved: list[V3KnowledgeClaimRecord] = []
        for support_group, claims in grouped.items():
            assessments_by_host: dict[str, SourceAssessment] = {}
            for item in claims:
                document = documents.get(item.source_document_id)
                if document is None:
                    continue
                assessment = SourceAssessment.model_validate(document.assessment_json)
                host = urlsplit(str(assessment.url)).netloc.casefold().removeprefix("www.")
                current = assessments_by_host.get(host)
                if current is None or assessment.priority_score > current.priority_score:
                    assessments_by_host[host] = assessment
            assessments = list(assessments_by_host.values())
            compatibility_issues: list[dict[str, str]] = []
            for index, left in enumerate(claims):
                for right in claims[index + 1 :]:
                    compatible, reason = support_group_compatible(
                        left.claim_text, right.claim_text
                    )
                    if not compatible:
                        compatibility_issues.append(
                            {
                                "left_claim_key": left.claim_key,
                                "right_claim_key": right.claim_key,
                                "reason": reason,
                            }
                        )
            critical = any(item.critical for item in claims)
            comparison = all(item.evidence_role in {"comparison", "limitation", "common_error"} for item in claims)
            absolute = any(item.conclusion_status == ConclusionStatus.confirmed.value for item in claims)
            decision = self.source_policy.validate_bundle(
                assessments,
                critical_claim=critical,
                absolute_claim=absolute,
                comparison_context=comparison,
                procedural_context=procedural_context,
            )
            valid_conclusion = not any(
                item.conclusion_status in {
                    ConclusionStatus.disputed.value,
                    ConclusionStatus.insufficient_evidence.value,
                }
                for item in claims
            )
            can_approve = (
                decision.status == "passed"
                and valid_conclusion
                and not compatibility_issues
            )
            canonical_claim_id = _canonical_claim_id(self.pipeline_run_id, support_group)
            for item in claims:
                item.canonical_claim_id = canonical_claim_id
                assessment = SourceAssessment.model_validate(documents[item.source_document_id].assessment_json)
                item.approved = bool(
                    can_approve
                    and assessment.usage_policy
                    in {
                        SourceUsagePolicy.authoritative_evidence,
                        SourceUsagePolicy.corroborating_evidence,
                    }
                )
                item.validation_json = {
                    **(item.validation_json or {}),
                    "support_group": support_group,
                    "bundle_decision": decision.model_dump(mode="json"),
                    "semantic_compatibility": {
                        "status": "passed" if not compatibility_issues else "blocked",
                        "issues": compatibility_issues,
                    },
                }
                if item.fact_id:
                    fact = await self.db.get(FactLedger, item.fact_id)
                    if fact is not None:
                        fact.approved = item.approved
                        fact.approved_by_run_id = self.pipeline_run_id if item.approved else None
                if item.approved:
                    approved.append(item)
        await self.db.flush()
        return approved

    async def approved_coverage_by_node(self) -> dict[str, dict[str, object]]:
        """Return evidence coverage using independent hosts, not raw page counts."""

        rows = list(
            (
                await self.db.scalars(
                    select(V3KnowledgeClaimRecord).where(
                        V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run_id,
                        V3KnowledgeClaimRecord.approved.is_(True),
                    )
                )
            ).all()
        )
        document_ids = {row.source_document_id for row in rows}
        documents = {
            row.id: row
            for row in (
                await self.db.scalars(
                    select(V3SourceDocumentRecord).where(
                        V3SourceDocumentRecord.id.in_(document_ids)
                    )
                )
            ).all()
        } if document_ids else {}
        coverage: dict[str, dict[str, object]] = {}
        for row in rows:
            item = coverage.setdefault(
                row.knowledge_node_key,
                {"claim_count": 0, "independent_hosts": set(), "evidence_roles": set()},
            )
            item["claim_count"] = int(item["claim_count"]) + 1
            cast_roles = item["evidence_roles"]
            assert isinstance(cast_roles, set)
            cast_roles.add(row.evidence_role)
            document = documents.get(row.source_document_id)
            if document is not None:
                assessment = SourceAssessment.model_validate(document.assessment_json)
                if assessment.counts_toward_independent_source_diversity:
                    hosts = item["independent_hosts"]
                    assert isinstance(hosts, set)
                    hosts.add(
                        urlsplit(document.canonical_url).netloc.casefold().removeprefix("www.")
                    )
        return {
            node_id: {
                "claim_count": int(item["claim_count"]),
                "independent_source_count": len(item["independent_hosts"]),
                "evidence_roles": sorted(item["evidence_roles"]),
            }
            for node_id, item in coverage.items()
        }

    async def knowledge_claims(
        self,
        *,
        approved_only: bool = True,
        intelligence_eligible: bool = False,
    ) -> list[KnowledgeClaim]:
        """Return canonical claims rather than one claim per source record.

        Direct synthesis uses approved_only=True. The intelligence graph may use
        graph-eligible disputed/insufficient records as context, but they remain
        forbidden for direct writing through their conclusion policy.
        """

        query = select(V3KnowledgeClaimRecord).where(
            V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run_id
        )
        if approved_only:
            query = query.where(V3KnowledgeClaimRecord.approved.is_(True))
        rows = list((await self.db.scalars(query)).all())
        if intelligence_eligible:
            rows = [
                row
                for row in rows
                if row.approved
                or row.conclusion_status
                in {
                    ConclusionStatus.disputed.value,
                    ConclusionStatus.insufficient_evidence.value,
                    ConclusionStatus.conditional.value,
                }
                or bool(row.conflict_group)
            ]

        grouped: dict[uuid.UUID, list[V3KnowledgeClaimRecord]] = defaultdict(list)
        for row in rows:
            canonical_id = row.canonical_claim_id or _canonical_claim_id(
                self.pipeline_run_id, row.support_group
            )
            row.canonical_claim_id = canonical_id
            grouped[canonical_id].append(row)

        result: list[KnowledgeClaim] = []
        for canonical_id, group in grouped.items():
            sections = {_norm(row.knowledge_node_key) for row in group}
            roles = {_norm(row.evidence_role) for row in group}
            support_groups = list(dict.fromkeys(row.support_group for row in group if row.support_group))
            graph_eligible = len(sections) == 1 and len(roles) == 1 and len(support_groups) == 1
            representative = max(group, key=lambda row: (len(row.claim_text or ""), row.confidence_score))
            conclusion = _conservative_status([row.conclusion_status for row in group])
            approved_for_direct = bool(
                any(row.approved for row in group)
                and conclusion
                not in {
                    ConclusionStatus.disputed.value,
                    ConclusionStatus.insufficient_evidence.value,
                }
                and graph_eligible
            )
            result.append(
                KnowledgeClaim(
                    claim_id=canonical_id,
                    support_group=(support_groups or [representative.support_group])[0],
                    source_claim_ids=[row.id for row in group],
                    graph_eligible=graph_eligible,
                    approved_for_direct_writing=approved_for_direct,
                    claim_text=representative.claim_text,
                    evidence_role=representative.evidence_role,
                    knowledge_node_id=representative.knowledge_node_key,
                    method_ids=list(dict.fromkeys(value for row in group for value in (row.method_ids or []))),
                    conditions=list(dict.fromkeys(value for row in group for value in (row.conditions or []))),
                    applicability=list(dict.fromkeys(value for row in group for value in (row.applicability or []))),
                    limitations=list(dict.fromkeys(value for row in group for value in (row.limitations or []))),
                    source_context="\n\n".join(
                        dict.fromkeys(row.exact_quote for row in group if row.exact_quote)
                    )[:10000],
                    source_locator="; ".join(
                        dict.fromkeys(row.source_locator for row in group if row.source_locator)
                    )[:500] or "canonical claim",
                    conclusion_status=conclusion,
                    confidence_score=min(row.confidence_score for row in group),
                    conflict_group=representative.conflict_group,
                    source_fact_ids=[row.fact_id or row.id for row in group],
                )
            )
        return sorted(result, key=lambda item: (item.knowledge_node_id, str(item.claim_id)))

    async def materialize_synthesis(
        self,
        *,
        contract_id: uuid.UUID,
        methods: list[DraftMethodDossier],
        sections: list[DraftSectionDossier],
        decision_matrix: DraftDecisionMatrix | None,
        gaps: list[DraftKnowledgeGap],
        references: dict[str, ExternalReference],
    ) -> tuple[list[MethodDossier], list[SectionDossier], DecisionMatrix | None, list[KnowledgeGap]]:
        await self.db.execute(
            delete(V3MethodDossierRecord).where(
                V3MethodDossierRecord.pipeline_run_id == self.pipeline_run_id
            )
        )
        await self.db.execute(
            delete(V3DecisionMatrixRecord).where(
                V3DecisionMatrixRecord.pipeline_run_id == self.pipeline_run_id
            )
        )
        await self.db.execute(
            delete(V3SectionDossierRecord).where(
                V3SectionDossierRecord.pipeline_run_id == self.pipeline_run_id
            )
        )
        await self.db.execute(
            delete(KnowledgeGapRecord).where(
                KnowledgeGapRecord.contract_id == contract_id
            )
        )
        await self.db.flush()

        claim_rows = list(
            (
                await self.db.scalars(
                    select(V3KnowledgeClaimRecord).where(
                        V3KnowledgeClaimRecord.pipeline_run_id == self.pipeline_run_id,
                        V3KnowledgeClaimRecord.approved.is_(True),
                    )
                )
            ).all()
        )
        claims: dict[str, V3KnowledgeClaimRecord] = {}
        for row in claim_rows:
            claims[row.claim_key] = row
            claims[str(row.id)] = row
            if row.fact_id is not None:
                claims[str(row.fact_id)] = row

        def evidence(keys: list[str]) -> list[uuid.UUID]:
            missing = [key for key in keys if key not in claims]
            if missing:
                raise ValueError("Synthesis references unavailable claims: " + ", ".join(missing))
            return [claims[key].fact_id or claims[key].id for key in keys]

        final_gaps: list[KnowledgeGap] = []
        for draft in gaps:
            status = (
                GapResolutionStatus.resolved_conditionally
                if draft.allowed_conclusion and draft.prohibited_conclusions
                else GapResolutionStatus.open
            )
            gap = KnowledgeGap(
                gap_id=uuid.uuid4(),
                knowledge_node_id=draft.knowledge_node_id,
                gap_type=draft.gap_type,
                description=draft.description,
                essential=draft.essential,
                status=status,
                original_problem=draft.original_problem,
                reframed_problem=draft.reframed_problem,
                supporting_evidence_ids=evidence(draft.supporting_evidence_keys) if draft.supporting_evidence_keys else [],
                conflicting_evidence_ids=evidence(draft.conflicting_evidence_keys) if draft.conflicting_evidence_keys else [],
                allowed_conclusion=draft.allowed_conclusion,
                prohibited_conclusions=draft.prohibited_conclusions,
            )
            final_gaps.append(gap)
            self.db.add(
                KnowledgeGapRecord(
                    id=gap.gap_id,
                    contract_id=contract_id,
                    node_key=gap.knowledge_node_id,
                    gap_type=gap.gap_type.value,
                    description=gap.description,
                    essential=gap.essential,
                    status=gap.status.value,
                    original_problem=gap.original_problem,
                    reframed_problem=gap.reframed_problem,
                    resolution_json=gap.model_dump(mode="json"),
                )
            )

        gap_by_text = {gap.description: gap.gap_id for gap in final_gaps}
        final_methods: list[MethodDossier] = []
        for draft in methods:
            steps = []
            for item in draft.steps:
                corrections = [
                    SupportedCorrection(
                        problem=correction.problem,
                        why_it_matters=correction.why_it_matters,
                        correction=correction.correction,
                        evidence_ids=evidence(correction.evidence_keys),
                    )
                    for correction in item.common_mistakes
                ]
                steps.append(
                    ProcedureStep(
                        step_id=item.step_id,
                        sequence=item.sequence,
                        action=item.action,
                        purpose=item.purpose,
                        preconditions=item.preconditions,
                        execution_details=item.execution_details,
                        expected_observations=item.expected_observations,
                        warning_signs=item.warning_signs,
                        common_mistakes=corrections,
                        completion_condition=item.completion_condition,
                        next_step_id=item.next_step_id,
                        evidence_ids=evidence(item.evidence_keys),
                    )
                )
            unresolved_ids = [gap_by_text[text] for text in draft.unresolved_gaps if text in gap_by_text]
            method = MethodDossier(
                method_id=draft.method_id,
                name=draft.name,
                aliases=draft.aliases,
                equivalent_variations=draft.equivalent_variations,
                definition=draft.definition,
                mechanism_summary=draft.mechanism_summary,
                best_fit_conditions=draft.best_fit_conditions,
                limitations=draft.limitations,
                required_materials=draft.required_materials,
                preparation=draft.preparation,
                steps=steps,
                outcome_confirmation=draft.outcome_confirmation,
                transfer_required=draft.transfer_required,
                transfer_decision=draft.transfer_decision,
                post_method_monitoring=draft.post_method_monitoring,
                external_reference=references.get(draft.method_id),
                unresolved_gap_ids=unresolved_ids,
            )
            final_methods.append(method)
            payload = method.model_dump(mode="json")
            self.db.add(
                V3MethodDossierRecord(
                    contract_id=contract_id,
                    pipeline_run_id=self.pipeline_run_id,
                    method_key=method.method_id,
                    dossier_json=payload,
                    checksum=_checksum(payload),
                    status="validated" if not unresolved_ids else "blocked",
                )
            )

        final_matrix: DecisionMatrix | None = None
        if decision_matrix is not None:
            final_rules = [
                DecisionRule(
                    condition=rule.condition,
                    supported_direction=rule.supported_direction,
                    method_ids=rule.method_ids,
                    evidence_ids=evidence(rule.evidence_keys),
                    conclusion_status=rule.conclusion_status,
                )
                for rule in decision_matrix.rules
            ]
            final_matrix = DecisionMatrix(
                dimensions=decision_matrix.dimensions,
                method_ids=decision_matrix.method_ids,
                rules=final_rules,
                universal_best_method=None,
                prohibited_conclusions=decision_matrix.prohibited_conclusions,
            )
            matrix_payload = final_matrix.model_dump(mode="json")
            self.db.add(
                V3DecisionMatrixRecord(
                    contract_id=contract_id,
                    pipeline_run_id=self.pipeline_run_id,
                    matrix_json=matrix_payload,
                    checksum=_checksum(matrix_payload),
                    status="validated",
                )
            )

        final_sections: list[SectionDossier] = []
        for draft in sections:
            section = SectionDossier(
                section_id=draft.section_id,
                reader_state_before=draft.reader_state_before,
                reader_state_after=draft.reader_state_after,
                section_purpose=draft.section_purpose,
                central_question=draft.central_question,
                core_answer=draft.core_answer,
                decision_logic=[
                    DecisionRule(
                        condition=rule.condition,
                        supported_direction=rule.supported_direction,
                        method_ids=rule.method_ids,
                        evidence_ids=evidence(rule.evidence_keys),
                        conclusion_status=rule.conclusion_status,
                    )
                    for rule in draft.decision_logic
                ],
                procedural_elements=draft.procedural_elements,
                allowed_claim_ids=evidence(draft.allowed_claim_keys),
                important_conditions=draft.important_conditions,
                misconceptions=draft.misconceptions,
                conflicts=draft.conflicts,
                external_references=(
                    list(references.values())
                    if draft.section_id == "external_references"
                    else []
                ),
                transition_logic=draft.transition_logic,
                unresolved_gap_ids=[gap_by_text[text] for text in draft.unresolved_gaps if text in gap_by_text],
            )
            final_sections.append(section)
            payload = section.model_dump(mode="json")
            self.db.add(
                V3SectionDossierRecord(
                    contract_id=contract_id,
                    pipeline_run_id=self.pipeline_run_id,
                    section_key=section.section_id,
                    dossier_json=payload,
                    checksum=_checksum(payload),
                    status="validated" if not section.unresolved_gap_ids else "blocked",
                )
            )
        await self.db.flush()
        return final_methods, final_sections, final_matrix, final_gaps

    async def stage_review(self, stage: str, payload: dict, status: str, attempt: int = 1) -> V3StageReviewRecord:
        row = await self.db.scalar(
            select(V3StageReviewRecord).where(
                V3StageReviewRecord.pipeline_run_id == self.pipeline_run_id,
                V3StageReviewRecord.stage == stage,
                V3StageReviewRecord.attempt == attempt,
            )
        )
        checksum = _checksum(payload)
        if row is None:
            row = V3StageReviewRecord(
                project_id=self.project_id,
                pipeline_run_id=self.pipeline_run_id,
                stage=stage,
                attempt=attempt,
                status=status,
                payload_json=payload,
                checksum=checksum,
            )
            self.db.add(row)
        else:
            row.status = status
            row.payload_json = payload
            row.checksum = checksum
        await self.db.flush()
        return row

    async def quality(
        self,
        evaluation: ProceduralQualityEvaluation,
        *,
        article_version_id: uuid.UUID | None,
    ) -> V3ProceduralQualityRecord:
        payload = evaluation.model_dump(mode="json")
        row = await self.db.scalar(
            select(V3ProceduralQualityRecord).where(
                V3ProceduralQualityRecord.pipeline_run_id == self.pipeline_run_id
            )
        )
        if row is None:
            row = V3ProceduralQualityRecord(
                project_id=self.project_id,
                pipeline_run_id=self.pipeline_run_id,
                article_version_id=article_version_id,
                rubric_version=evaluation.rubric_version,
                status=evaluation.status,
                overall_score=evaluation.overall_score,
                result_json=payload,
                checksum=_checksum(payload),
            )
            self.db.add(row)
        else:
            row.article_version_id = article_version_id
            row.status = evaluation.status
            row.overall_score = evaluation.overall_score
            row.result_json = payload
            row.checksum = _checksum(payload)
        if article_version_id is not None:
            axes = {
                "research_quality": {"score": evaluation.research_quality, "metrics": {}},
                "knowledge_model_quality": {"score": evaluation.knowledge_model_quality, "metrics": {}},
                "comparison_decision_quality": {"score": evaluation.comparison_decision_quality, "metrics": {}},
                "procedural_completeness": {"score": evaluation.procedural_completeness, "metrics": {}},
                "practical_utility": {"score": evaluation.practical_utility, "metrics": {}},
                "editorial_coherence": {"score": evaluation.editorial_coherence, "metrics": {}},
                "naturalness": {"score": evaluation.naturalness, "metrics": {}},
                "factual_link_integrity": {"score": evaluation.factual_link_integrity, "metrics": {}},
            }
            thresholds = {"min_overall_score": 0.85, "min_axis_score": 0.70}
            bridge_result = {
                "rubric_version": evaluation.rubric_version,
                "rubric_checksum": _checksum({
                    "rubric_version": evaluation.rubric_version,
                    "weights": [15, 10, 10, 20, 10, 10, 15, 10],
                    "minimum_overall": 0.85,
                    "minimum_axis": 0.70,
                }),
                "evaluator_kind": "deterministic_v3",
                "status": evaluation.status,
                "overall_score": evaluation.overall_score,
                "thresholds": thresholds,
                "axes": axes,
                "critical_blockers": [
                    {
                        "code": "v3_" + re.sub(r"[^a-z0-9]+", "_", blocker.casefold()).strip("_")[:70],
                        "critical": True,
                        "details": {"message": blocker},
                    }
                    for blocker in evaluation.critical_blockers
                ],
                "warnings": [
                    {"code": "v3_quality_warning", "details": {"message": warning}}
                    for warning in evaluation.warnings
                ],
                "automatic_publication": False,
                "source_evaluation_id": str(row.id),
            }
            result_checksum = _checksum(bridge_result)
            bridge_result["result_checksum"] = result_checksum
            generic = await self.db.scalar(
                select(QualityEvaluation).where(
                    QualityEvaluation.pipeline_run_id == self.pipeline_run_id
                )
            )
            if generic is None:
                generic = QualityEvaluation(
                    project_id=self.project_id,
                    pipeline_run_id=self.pipeline_run_id,
                    article_version_id=article_version_id,
                    rubric_version=evaluation.rubric_version,
                    rubric_checksum=bridge_result["rubric_checksum"],
                    evaluator_kind="deterministic_v3",
                    status=evaluation.status,
                    overall_score=evaluation.overall_score,
                    thresholds_json=thresholds,
                    result_json=bridge_result,
                    result_checksum=result_checksum,
                )
                self.db.add(generic)
            elif generic.article_version_id != article_version_id:
                raise ValueError("V3 quality bridge targets a different article version")
            else:
                generic.rubric_version = evaluation.rubric_version
                generic.rubric_checksum = bridge_result["rubric_checksum"]
                generic.evaluator_kind = "deterministic_v3"
                generic.status = evaluation.status
                generic.overall_score = evaluation.overall_score
                generic.thresholds_json = thresholds
                generic.result_json = bridge_result
                generic.result_checksum = result_checksum
        await self.db.flush()
        return row

    async def contract_row(self) -> ContentKnowledgeContractRecord:
        row = await self.db.scalar(
            select(ContentKnowledgeContractRecord).where(
                ContentKnowledgeContractRecord.pipeline_run_id == self.pipeline_run_id,
                ContentKnowledgeContractRecord.status.in_(["validated", "active"]),
            )
        )
        if row is None:
            raise ValueError("V3 pipeline run has no materialized knowledge contract")
        return row
