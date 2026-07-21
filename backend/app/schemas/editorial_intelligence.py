"""Canonical contracts for the Editorial Intelligence Core.

V3.6.1 closes the semantic and audit relationships that were only partially
represented in V3.6: canonical claims, explicit question coverage, question to
sentence answers, recovery classification and artifact-bound validation.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.editorial_v3 import ConclusionStatus, EvidenceRole, V3StrictModel


class IntelligenceLifecycle(str, Enum):
    planned = "planned"
    evidence_attached = "evidence_attached"
    writer_ready = "writer_ready"
    draft_pending_validation = "draft_pending_validation"
    draft_validated = "draft_validated"
    blocked = "blocked"


class EditorialQuestionKind(str, Enum):
    central = "central"
    knowledge = "knowledge"
    decision = "decision"
    completion = "completion"


class ClaimWriterPolicy(str, Enum):
    direct = "direct"
    conditional = "conditional"
    context_only = "context_only"
    prohibited = "prohibited"


class QuestionCoverageStatus(str, Enum):
    unsupported = "unsupported"
    candidate = "candidate"
    semantically_supported = "semantically_supported"
    human_overridden = "human_overridden"


class QuestionAnswerStatus(str, Enum):
    direct = "direct"
    partial = "partial"
    contextual = "contextual"
    unanswered = "unanswered"


class RecoveryClass(str, Enum):
    recoverable = "recoverable"
    contract_error = "contract_error"
    nonrecoverable = "nonrecoverable"
    budget_exhausted = "budget_exhausted"


class IntelligenceFinding(V3StrictModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,99}$")
    message: str = Field(min_length=5, max_length=2000)
    section_id: str | None = Field(default=None, max_length=100)
    question_id: str | None = Field(default=None, max_length=140)
    claim_id: UUID | None = None
    source_id: UUID | None = None
    recovery_class: RecoveryClass = RecoveryClass.nonrecoverable
    details: dict = Field(default_factory=dict)


class EditorialQuestion(V3StrictModel):
    question_id: str = Field(pattern=r"^q_[a-z0-9_]{3,130}$")
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    kind: EditorialQuestionKind
    question: str = Field(min_length=5, max_length=1500)
    critical: bool = True
    research_required: bool = True
    required_evidence_roles: list[EvidenceRole] = Field(default_factory=list, max_length=30)
    completion_signal: str = Field(default="", max_length=1500)
    origin: Literal["contract", "emergent"] = "contract"
    rationale: str = Field(default="", max_length=1500)


class EmergentEditorialQuestionProposal(V3StrictModel):
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    kind: EditorialQuestionKind = EditorialQuestionKind.knowledge
    question: str = Field(min_length=8, max_length=1000)
    rationale: str = Field(min_length=5, max_length=1500)
    critical: bool = False
    required_evidence_roles: list[EvidenceRole] = Field(default_factory=list, max_length=10)
    completion_signal: str = Field(default="", max_length=1000)


class EmergentEditorialQuestionsOutput(V3StrictModel):
    questions: list[EmergentEditorialQuestionProposal] = Field(default_factory=list, max_length=20)


class SectionIntelligencePlan(V3StrictModel):
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    sequence: int = Field(ge=1, le=100)
    title_function: str = Field(min_length=3, max_length=500)
    editorial_goal: str = Field(min_length=10, max_length=2000)
    reader_state_before: str = Field(min_length=5, max_length=2000)
    reader_state_after: str = Field(min_length=5, max_length=2000)
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    question_ids: list[str] = Field(min_length=1, max_length=100)
    research_required: bool = True
    importance: str = Field(default="core", max_length=40)
    minimum_depth_weight: float = Field(default=1.0, ge=0.1, le=5.0)
    allowed_claim_ids: list[UUID] = Field(default_factory=list, max_length=300)
    prohibited_claim_ids: list[UUID] = Field(default_factory=list, max_length=300)
    conflict_ids: list[str] = Field(default_factory=list, max_length=100)
    required_conditions: list[str] = Field(default_factory=list, max_length=100)
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=100)
    completion_criteria: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def claim_sets_are_disjoint(self):
        if set(self.allowed_claim_ids) & set(self.prohibited_claim_ids):
            raise ValueError("A claim cannot be both allowed and prohibited in one section")
        return self


class EvidenceSourceNode(V3StrictModel):
    source_id: UUID
    canonical_url: str = Field(min_length=8, max_length=4000)
    title: str = Field(min_length=1, max_length=1000)
    source_role: str = Field(default="", max_length=100)
    usage_policy: str = Field(default="", max_length=100)
    publisher: str = Field(default="", max_length=500)
    content_hash: str = Field(default="", max_length=128)


class EvidenceClaimNode(V3StrictModel):
    # claim_id is the canonical claim ID in V3.6.1.
    claim_id: UUID
    source_claim_ids: list[UUID] = Field(default_factory=list, max_length=100)
    support_group: str = Field(default="", max_length=120)
    claim_text: str = Field(min_length=5, max_length=5000)
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    evidence_role: EvidenceRole
    source_ids: list[UUID] = Field(min_length=1, max_length=100)
    source_fact_ids: list[UUID] = Field(default_factory=list, max_length=100)
    method_ids: list[str] = Field(default_factory=list, max_length=30)
    conditions: list[str] = Field(default_factory=list, max_length=50)
    limitations: list[str] = Field(default_factory=list, max_length=50)
    applicability: list[str] = Field(default_factory=list, max_length=50)
    conclusion_status: ConclusionStatus
    confidence_score: float = Field(ge=0, le=1)
    conflict_group: str | None = Field(default=None, max_length=160)
    writer_policy: ClaimWriterPolicy
    integrity_issues: list[str] = Field(default_factory=list, max_length=30)


class QuestionClaimCoverage(V3StrictModel):
    question_id: str = Field(pattern=r"^q_[a-z0-9_]{3,130}$")
    claim_id: UUID
    status: QuestionCoverageStatus
    alignment_score: float = Field(ge=0, le=1)
    authorized_in_section: bool
    role_compatible: bool
    source_ids: list[UUID] = Field(default_factory=list, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)


class QuestionAnswerRecord(V3StrictModel):
    question_id: str = Field(pattern=r"^q_[a-z0-9_]{3,130}$")
    sentence_ids: list[UUID] = Field(default_factory=list, max_length=100)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=100)
    answer_status: QuestionAnswerStatus = QuestionAnswerStatus.unanswered
    completion_signal_score: float = Field(default=0.0, ge=0, le=1)


class EvidenceConflictNode(V3StrictModel):
    conflict_id: str = Field(pattern=r"^conflict_[a-z0-9_]{3,150}$")
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    claim_ids: list[UUID] = Field(min_length=1, max_length=100)
    status: Literal["represented", "unresolved", "resolved_conditionally"]
    required_language: str = Field(min_length=5, max_length=2000)
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=50)


class EvidenceGraph(V3StrictModel):
    graph_version: Literal["evidence-graph-v1", "evidence-graph-v1.1"] = "evidence-graph-v1.1"
    sources: list[EvidenceSourceNode] = Field(default_factory=list, max_length=2000)
    claims: list[EvidenceClaimNode] = Field(default_factory=list, max_length=5000)
    conflicts: list[EvidenceConflictNode] = Field(default_factory=list, max_length=1000)
    section_claim_map: dict[str, list[UUID]] = Field(default_factory=dict)
    question_claim_map: dict[str, list[UUID]] = Field(default_factory=dict)
    question_alignment_scores: dict[str, float] = Field(default_factory=dict)
    question_coverage: list[QuestionClaimCoverage] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def graph_references_are_closed(self):
        source_ids = {item.source_id for item in self.sources}
        claim_ids = {item.claim_id for item in self.claims}
        claim_by_id = {item.claim_id: item for item in self.claims}
        if len(source_ids) != len(self.sources):
            raise ValueError("Evidence graph source IDs must be unique")
        if len(claim_ids) != len(self.claims):
            raise ValueError("Evidence graph claim IDs must be unique")
        for claim in self.claims:
            if not set(claim.source_ids).issubset(source_ids):
                raise ValueError("Evidence claim references an unknown source")
        for section_id, values in self.section_claim_map.items():
            if not set(values).issubset(claim_ids):
                raise ValueError("Section claim map references an unknown claim")
            if any(claim_by_id[item].section_id != section_id for item in values):
                raise ValueError("Section claim map contains a claim from another section")
        for values in self.question_claim_map.values():
            if not set(values).issubset(claim_ids):
                raise ValueError("Question claim map references an unknown claim")
        if set(self.question_alignment_scores) - set(self.question_claim_map):
            raise ValueError("Question alignment scores reference an unknown question mapping")
        if any(score < 0 or score > 1 for score in self.question_alignment_scores.values()):
            raise ValueError("Question alignment scores must be between zero and one")
        conflict_ids = [item.conflict_id for item in self.conflicts]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("Evidence conflict IDs must be unique")
        for conflict in self.conflicts:
            if not set(conflict.claim_ids).issubset(claim_ids):
                raise ValueError("Evidence conflict references an unknown claim")
            if any(claim_by_id[item].section_id != conflict.section_id for item in conflict.claim_ids):
                raise ValueError("Evidence conflict contains a claim from another section")
        coverage_keys = [(item.question_id, item.claim_id) for item in self.question_coverage]
        if len(coverage_keys) != len(set(coverage_keys)):
            raise ValueError("Question coverage edges must be unique")
        if any(item.claim_id not in claim_ids for item in self.question_coverage):
            raise ValueError("Question coverage references an unknown claim")
        return self


class IntelligenceValidationReport(V3StrictModel):
    status: Literal["passed", "blocked"]
    phase: Literal["planning", "writer_readiness", "draft"]
    score: float = Field(ge=0, le=1)
    blockers: list[IntelligenceFinding] = Field(default_factory=list, max_length=500)
    warnings: list[IntelligenceFinding] = Field(default_factory=list, max_length=500)
    metrics: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def status_matches_findings(self):
        if self.status == "passed" and self.blockers:
            raise ValueError("A passed intelligence report cannot contain blockers")
        if self.status == "blocked" and not self.blockers:
            raise ValueError("A blocked intelligence report requires blockers")
        return self


class ContentIntelligenceState(V3StrictModel):
    intelligence_version: Literal[
        "editorial-intelligence-v1", "editorial-intelligence-v1.1"
    ] = "editorial-intelligence-v1.1"
    project_id: UUID
    pipeline_run_id: UUID
    contract_id: UUID | None = None
    revision: int = Field(default=1, ge=1)
    lifecycle: IntelligenceLifecycle = IntelligenceLifecycle.planned
    created_at: datetime
    updated_at: datetime
    locale: str = Field(default="pt-BR", min_length=2, max_length=40)
    topic: str = Field(min_length=3, max_length=500)
    content_type: str = Field(min_length=3, max_length=100)
    content_objective: str = Field(min_length=3, max_length=3000)
    search_intent: str = Field(min_length=3, max_length=100)
    reader_profile: dict = Field(default_factory=dict)
    commercial_context: dict = Field(default_factory=dict)
    brand_context: dict = Field(default_factory=dict)
    generation_constraints: dict = Field(default_factory=dict)
    prohibited_claims: list[str] = Field(default_factory=list, max_length=100)
    questions: list[EditorialQuestion] = Field(min_length=1, max_length=1000)
    sections: list[SectionIntelligencePlan] = Field(min_length=1, max_length=100)
    evidence_graph: EvidenceGraph = Field(default_factory=EvidenceGraph)
    question_answer_map: list[QuestionAnswerRecord] = Field(default_factory=list, max_length=1000)
    unresolved_gap_ids: list[UUID] = Field(default_factory=list, max_length=500)
    validation: IntelligenceValidationReport | None = None
    validated_artifact_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    article_version_id: UUID | None = None
    draft_revision: int = Field(default=0, ge=0)
    checksum: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def state_references_are_closed(self):
        section_ids = [item.section_id for item in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Intelligence section IDs must be unique")
        question_ids = [item.question_id for item in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("Intelligence question IDs must be unique")
        known_sections = set(section_ids)
        known_questions = set(question_ids)
        question_by_id = {item.question_id: item for item in self.questions}
        claim_by_id = {item.claim_id: item for item in self.evidence_graph.claims}
        known_claims = set(claim_by_id)
        conflict_by_id = {item.conflict_id: item for item in self.evidence_graph.conflicts}

        if any(item.section_id not in known_sections for item in self.questions):
            raise ValueError("Editorial question references an unknown section")
        all_section_questions: set[str] = set()
        for section in self.sections:
            if not set(section.question_ids).issubset(known_questions):
                raise ValueError("Section plan references an unknown question")
            if any(question_by_id[item].section_id != section.section_id for item in section.question_ids):
                raise ValueError("Section plan references a question owned by another section")
            all_section_questions.update(section.question_ids)
            if not set(section.depends_on).issubset(known_sections):
                raise ValueError("Section plan references an unknown dependency")
            if not set(section.allowed_claim_ids).issubset(known_claims):
                raise ValueError("Section allows a claim absent from the evidence graph")
            if not set(section.prohibited_claim_ids).issubset(known_claims):
                raise ValueError("Section prohibits a claim absent from the evidence graph")
            if any(claim_by_id[item].section_id != section.section_id for item in section.allowed_claim_ids):
                raise ValueError("Section allows a claim owned by another section")
            if any(claim_by_id[item].section_id != section.section_id for item in section.prohibited_claim_ids):
                raise ValueError("Section prohibits a claim owned by another section")
            if not set(section.conflict_ids).issubset(conflict_by_id):
                raise ValueError("Section references an unknown conflict")
            if any(conflict_by_id[item].section_id != section.section_id for item in section.conflict_ids):
                raise ValueError("Section references a conflict owned by another section")
        if all_section_questions != known_questions:
            raise ValueError("Every editorial question must belong to exactly one section plan")
        if any(claim.section_id not in known_sections for claim in self.evidence_graph.claims):
            raise ValueError("Evidence graph claim references an unknown section")
        if set(self.evidence_graph.section_claim_map) - known_sections:
            raise ValueError("Evidence graph section map references an unknown section")
        if set(self.evidence_graph.question_claim_map) - known_questions:
            raise ValueError("Evidence graph question map references an unknown question")
        for question_id, claim_ids in self.evidence_graph.question_claim_map.items():
            section_id = question_by_id[question_id].section_id
            if any(claim_by_id[item].section_id != section_id for item in claim_ids):
                raise ValueError("Question is mapped to a claim owned by another section")
        for edge in self.evidence_graph.question_coverage:
            if edge.question_id not in known_questions:
                raise ValueError("Question coverage references an unknown question")
            if claim_by_id[edge.claim_id].section_id != question_by_id[edge.question_id].section_id:
                raise ValueError("Question coverage crosses section ownership")
        answer_question_ids = [item.question_id for item in self.question_answer_map]
        if len(answer_question_ids) != len(set(answer_question_ids)):
            raise ValueError("Question answer records must be unique")
        for answer in self.question_answer_map:
            if answer.question_id not in known_questions:
                raise ValueError("Question answer map references an unknown question")
            if not set(answer.claim_ids).issubset(known_claims):
                raise ValueError("Question answer map references an unknown claim")
            section_id = question_by_id[answer.question_id].section_id
            if any(claim_by_id[item].section_id != section_id for item in answer.claim_ids):
                raise ValueError("Question answer uses a claim from another section")
        if self.lifecycle == IntelligenceLifecycle.draft_validated and not self.validated_artifact_hash:
            raise ValueError("A validated draft state requires validated_artifact_hash")
        return self
