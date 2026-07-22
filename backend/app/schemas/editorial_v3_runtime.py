"""Runtime contracts for the executable Editorial Intelligence V3 pipeline.

The foundation contracts model the editorial graph.  These contracts model the
artifacts exchanged by the real research, synthesis, writing, and review stages.
All LLM-facing outputs are strict and use stable string keys while evidence is
being extracted; persisted UUIDs are attached deterministically afterwards.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import AliasChoices, AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from app.schemas.editorial_v3 import (
    ApproachDimension,
    ConclusionStatus,
    EvidenceRole,
    GapType,
    ResearchSourceSignals,
    SourceAssessment,
    SourcePageType,
)


class V3OpenAIStrictOutput(BaseModel):
    openai_strict: ClassVar[bool] = True
    model_config = ConfigDict(extra="forbid")


class ApproachTaxonomyItem(V3OpenAIStrictOutput):
    label: str = Field(min_length=3, max_length=200)
    detected_dimension: ApproachDimension
    comparable_at_same_level: bool
    valid_for_topic: bool
    rationale: str = Field(min_length=10, max_length=1000)


class ApproachTaxonomyValidationOutput(V3OpenAIStrictOutput):
    declared_dimension: ApproachDimension
    coherent_set: bool
    items: list[ApproachTaxonomyItem] = Field(min_length=2, max_length=20)
    blocking_issues: list[str] = Field(default_factory=list, max_length=20)
    normalized_collective_name: str = Field(min_length=3, max_length=160)

    @model_validator(mode="after")
    def validate_consistency(self):
        labels = [" ".join(item.label.casefold().split()) for item in self.items]
        if len(labels) != len(set(labels)):
            raise ValueError("Approach taxonomy labels must be unique")
        invalid = any(
            item.detected_dimension != self.declared_dimension
            or not item.comparable_at_same_level
            or not item.valid_for_topic
            for item in self.items
        )
        if self.coherent_set and (invalid or self.blocking_issues):
            raise ValueError(
                "A coherent approach set cannot contain invalid items or blockers"
            )
        return self


class ResearchTask(V3OpenAIStrictOutput):
    task_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,119}$")
    knowledge_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    evidence_role: EvidenceRole
    research_goal: str = Field(min_length=20, max_length=1500)
    queries: list[str] = Field(min_length=1, max_length=6)
    required_source_roles: list[str] = Field(min_length=1, max_length=12)
    minimum_independent_sources: int = Field(ge=1, le=4)
    critical: bool = False
    method_hint: str | None = Field(default=None, max_length=160)
    rationale: str = Field(min_length=10, max_length=1000)


class V3ResearchPlan(V3OpenAIStrictOutput):
    rationale: str = Field(min_length=20, max_length=3000)
    tasks: list[ResearchTask] = Field(min_length=3, max_length=100)
    method_discovery_queries: list[str] = Field(min_length=2, max_length=12)
    terminology_queries: list[str] = Field(default_factory=list, max_length=12)
    stop_conditions: list[str] = Field(min_length=2, max_length=20)
    maximum_search_queries: int = Field(ge=5, le=100)

    @model_validator(mode="after")
    def unique_tasks(self):
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("V3 research task IDs must be unique")
        return self


class StructuredTable(V3OpenAIStrictOutput):
    caption: str = Field(default="", max_length=500)
    headers: list[str] = Field(default_factory=list, max_length=30)
    rows: list[list[str]] = Field(default_factory=list, max_length=100)


class StructuredDocumentSection(V3OpenAIStrictOutput):
    section_id: str = Field(pattern=r"^sec_[a-f0-9]{12}$")
    heading_path: list[str] = Field(default_factory=list, max_length=10)
    paragraphs: list[str] = Field(default_factory=list, max_length=100)
    ordered_steps: list[str] = Field(default_factory=list, max_length=100)
    unordered_items: list[str] = Field(default_factory=list, max_length=100)
    tables: list[StructuredTable] = Field(default_factory=list, max_length=20)
    source_locator: str = Field(min_length=3, max_length=500)
    character_count: int = Field(ge=0)


class StructuredSourceDocument(V3OpenAIStrictOutput):
    document_id: UUID
    url: AnyHttpUrl
    canonical_url: AnyHttpUrl
    title: str = Field(min_length=3, max_length=1000)
    author: str | None = Field(default=None, max_length=500)
    publisher: str | None = Field(default=None, max_length=500)
    published_at: datetime | None = None
    accessed_at: datetime
    language: str | None = Field(default=None, max_length=20)
    document_type: SourcePageType
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sections: list[StructuredDocumentSection] = Field(min_length=1, max_length=300)
    bibliographic_references: list[str] = Field(default_factory=list, max_length=200)
    outgoing_links: list[str] = Field(default_factory=list, max_length=300)
    assessment: SourceAssessment
    source_signals: ResearchSourceSignals | None = None
    truncated: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=50)
    plain_text: str = Field(min_length=100, max_length=120000)


class ExtractedKnowledgeClaimCandidate(V3OpenAIStrictOutput):
    claim_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,119}$")
    support_group: str = Field(pattern=r"^[a-z][a-z0-9_]{2,119}$")
    source_url: AnyHttpUrl
    knowledge_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    evidence_role: EvidenceRole
    claim_text: str = Field(min_length=8, max_length=4000)
    exact_quote: str = Field(min_length=5, max_length=5000)
    source_locator: str = Field(min_length=3, max_length=500)
    method_labels: list[str] = Field(default_factory=list, max_length=20)
    conditions: list[str] = Field(default_factory=list, max_length=20)
    applicability: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    conclusion_status: ConclusionStatus
    confidence_score: float = Field(ge=0, le=1)
    critical: bool = False
    conflict_group: str | None = Field(default=None, max_length=160)


class KnowledgeClaimExtractionOutput(V3OpenAIStrictOutput):
    claims: list[ExtractedKnowledgeClaimCandidate] = Field(default_factory=list, max_length=120)
    discovered_method_labels: list[str] = Field(default_factory=list, max_length=30)
    unresolved_questions: list[str] = Field(default_factory=list, max_length=30)


class MethodInventoryItem(V3OpenAIStrictOutput):
    method_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    name: str = Field(min_length=3, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    distinguishing_feature: str = Field(min_length=10, max_length=1000)
    equivalent_variations: list[str] = Field(default_factory=list, max_length=20)
    supporting_claim_keys: list[str] = Field(min_length=1, max_length=50)


class MethodInventoryOutput(V3OpenAIStrictOutput):
    methods: list[MethodInventoryItem] = Field(min_length=2, max_length=20)
    rejected_duplicates: list[str] = Field(default_factory=list, max_length=30)
    rationale: str = Field(min_length=20, max_length=3000)

    @model_validator(mode="after")
    def unique_methods(self):
        method_ids = [item.method_id for item in self.methods]
        if len(method_ids) != len(set(method_ids)):
            raise ValueError("Method inventory IDs must be unique")
        labels: dict[str, str] = {}
        for method in self.methods:
            for label in [method.name, *method.aliases, *method.equivalent_variations]:
                normalized = " ".join(label.casefold().split())
                if not normalized:
                    continue
                owner = labels.get(normalized)
                if owner is not None and owner != method.method_id:
                    raise ValueError(
                        f"Method label {label!r} is assigned to more than one method"
                    )
                labels[normalized] = method.method_id
            if len(method.supporting_claim_keys) != len(set(method.supporting_claim_keys)):
                raise ValueError("Method supporting claims must be unique")
        return self


class DraftCorrection(V3OpenAIStrictOutput):
    problem: str = Field(min_length=5, max_length=1000)
    why_it_matters: str = Field(min_length=5, max_length=1500)
    correction: str = Field(min_length=5, max_length=1500)
    evidence_keys: list[str] = Field(min_length=1, max_length=20)


class DraftProcedureStep(V3OpenAIStrictOutput):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    sequence: int = Field(ge=1, le=100)
    action: str = Field(min_length=8, max_length=2000)
    purpose: str = Field(min_length=8, max_length=2000)
    preconditions: list[str] = Field(default_factory=list, max_length=30)
    execution_details: list[str] = Field(min_length=1, max_length=40)
    expected_observations: list[str] = Field(min_length=1, max_length=30)
    warning_signs: list[str] = Field(default_factory=list, max_length=30)
    common_mistakes: list[DraftCorrection] = Field(default_factory=list, max_length=20)
    completion_condition: str = Field(min_length=8, max_length=1500)
    next_step_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,99}$")
    evidence_keys: list[str] = Field(min_length=1, max_length=40)


class DraftMethodDossier(V3OpenAIStrictOutput):
    method_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    name: str = Field(min_length=3, max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    equivalent_variations: list[str] = Field(default_factory=list, max_length=20)
    definition: str = Field(min_length=20, max_length=3000)
    mechanism_summary: str = Field(min_length=20, max_length=3000)
    best_fit_conditions: list[str] = Field(min_length=1, max_length=30)
    limitations: list[str] = Field(min_length=1, max_length=30)
    required_materials: list[str] = Field(min_length=1, max_length=40)
    preparation: list[str] = Field(min_length=1, max_length=40)
    steps: list[DraftProcedureStep] = Field(min_length=1, max_length=40)
    outcome_confirmation: list[str] = Field(
        min_length=1,
        max_length=30,
        validation_alias=AliasChoices("outcome_confirmation", "germination_confirmation"),
        serialization_alias="outcome_confirmation",
    )
    transfer_required: bool
    transfer_decision: list[str] = Field(default_factory=list, max_length=30)
    post_method_monitoring: list[str] = Field(min_length=1, max_length=30)
    preferred_external_source_url: AnyHttpUrl | None = None
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def step_chain(self):
        if [step.sequence for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("Draft procedure steps must be contiguous")
        for index, step in enumerate(self.steps):
            expected = self.steps[index + 1].step_id if index + 1 < len(self.steps) else None
            if step.next_step_id != expected:
                raise ValueError("Draft procedure next_step_id must follow sequence")
        if self.transfer_required and not self.transfer_decision:
            raise ValueError("A transferring method requires observable transfer criteria")
        if not self.transfer_required and self.transfer_decision:
            raise ValueError("A direct method must not invent transfer criteria")
        return self


class DraftDecisionRule(V3OpenAIStrictOutput):
    condition: str = Field(min_length=8, max_length=1500)
    supported_direction: str = Field(min_length=8, max_length=1500)
    method_ids: list[str] = Field(min_length=1, max_length=20)
    evidence_keys: list[str] = Field(min_length=1, max_length=30)
    conclusion_status: ConclusionStatus


class DraftDecisionMatrix(V3OpenAIStrictOutput):
    dimensions: list[str] = Field(min_length=2, max_length=30)
    method_ids: list[str] = Field(min_length=2, max_length=30)
    rules: list[DraftDecisionRule] = Field(min_length=1, max_length=60)
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=30)


class DraftSectionDossier(V3OpenAIStrictOutput):
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    reader_state_before: str = Field(min_length=10, max_length=1500)
    reader_state_after: str = Field(min_length=10, max_length=1500)
    section_purpose: str = Field(min_length=20, max_length=2000)
    central_question: str = Field(min_length=8, max_length=500)
    core_answer: str = Field(min_length=20, max_length=5000)
    decision_logic: list[DraftDecisionRule] = Field(default_factory=list, max_length=30)
    procedural_elements: list[str] = Field(default_factory=list, max_length=50)
    allowed_claim_keys: list[str] = Field(min_length=1, max_length=100)
    important_conditions: list[str] = Field(default_factory=list, max_length=40)
    misconceptions: list[str] = Field(default_factory=list, max_length=30)
    conflicts: list[str] = Field(default_factory=list, max_length=30)
    transition_logic: str = Field(min_length=8, max_length=2000)
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=30)


class DraftKnowledgeGap(V3OpenAIStrictOutput):
    knowledge_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    gap_type: GapType
    description: str = Field(min_length=10, max_length=3000)
    essential: bool = True
    original_problem: str = Field(default="", max_length=3000)
    reframed_problem: str = Field(default="", max_length=3000)
    allowed_conclusion: str = Field(default="", max_length=3000)
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=30)
    supporting_evidence_keys: list[str] = Field(default_factory=list, max_length=50)
    conflicting_evidence_keys: list[str] = Field(default_factory=list, max_length=50)


class KnowledgeSynthesisOutput(V3OpenAIStrictOutput):
    methods: list[DraftMethodDossier] = Field(min_length=2, max_length=20)
    sections: list[DraftSectionDossier] = Field(min_length=3, max_length=40)
    decision_matrix: DraftDecisionMatrix
    gaps: list[DraftKnowledgeGap] = Field(default_factory=list, max_length=100)
    synthesis_notes: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_graph_references(self):
        method_ids = [item.method_id for item in self.methods]
        section_ids = [item.section_id for item in self.sections]
        if len(method_ids) != len(set(method_ids)):
            raise ValueError("Knowledge synthesis method IDs must be unique")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Knowledge synthesis section IDs must be unique")
        if set(self.decision_matrix.method_ids) != set(method_ids):
            raise ValueError("Decision matrix methods must exactly match synthesized methods")
        for rule in [*self.decision_matrix.rules, *(rule for section in self.sections for rule in section.decision_logic)]:
            if not set(rule.method_ids).issubset(method_ids):
                raise ValueError("Decision rule references an unknown synthesized method")
        gap_keys = [(gap.knowledge_node_id, gap.description.casefold().strip()) for gap in self.gaps]
        if len(gap_keys) != len(set(gap_keys)):
            raise ValueError("Knowledge synthesis gaps must be unique")
        return self


class GenericKnowledgeSynthesisOutput(V3OpenAIStrictOutput):
    """Knowledge synthesis for content that is not method-procedural.

    It preserves the same evidence discipline and ordered section dossiers,
    without forcing an artificial method inventory or decision matrix.
    """

    sections: list[DraftSectionDossier] = Field(min_length=3, max_length=40)
    gaps: list[DraftKnowledgeGap] = Field(default_factory=list, max_length=100)
    synthesis_notes: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_graph_references(self):
        section_ids = [item.section_id for item in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Knowledge synthesis section IDs must be unique")
        gap_keys = [
            (gap.knowledge_node_id, gap.description.casefold().strip())
            for gap in self.gaps
        ]
        if len(gap_keys) != len(set(gap_keys)):
            raise ValueError("Knowledge synthesis gaps must be unique")
        return self


class V3EvidenceReference(V3OpenAIStrictOutput):
    claim_id: UUID
    entailment_score: float = Field(ge=0, le=1)


class V3DraftSentence(V3OpenAIStrictOutput):
    sentence_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=5000)
    is_factual: bool
    evidence: list[V3EvidenceReference] = Field(default_factory=list, max_length=20)
    question_ids: list[str] = Field(default_factory=list, max_length=30)
    answer_status: Literal["direct", "partial", "contextual"] | None = None

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.is_factual and not self.evidence:
            raise ValueError("Every factual V3 sentence requires approved evidence")
        if not self.is_factual and self.evidence:
            raise ValueError("Editorial sentences must not carry evidence")
        return self


class V3TableRow(V3OpenAIStrictOutput):
    cells: list[V3DraftSentence] = Field(min_length=2, max_length=12)


class V3DraftBlock(V3OpenAIStrictOutput):
    block_id: UUID
    type: Literal["h1", "h2", "h3", "paragraph", "list", "table", "callout"]
    position: int = Field(ge=0)
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    method_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{2,99}$"
    )
    sentences: list[V3DraftSentence] = Field(default_factory=list, max_length=100)
    table_headers: list[V3DraftSentence] = Field(default_factory=list, max_length=12)
    table_rows: list[V3TableRow] = Field(default_factory=list, max_length=50)
    callout_kind: Literal["note", "tip", "warning", "important"] | None = None
    callout_title: V3DraftSentence | None = None

    @property
    def content_sentences(self) -> list[V3DraftSentence]:
        if self.type == "table" and self.table_headers:
            return [
                *self.table_headers,
                *(cell for row in self.table_rows for cell in row.cells),
            ]
        if self.type == "callout" and self.callout_title is not None:
            return [self.callout_title, *self.sentences]
        return self.sentences

    @model_validator(mode="after")
    def block_contract(self):
        if self.type in {"h1", "h2", "h3"}:
            if len(self.sentences) != 1:
                raise ValueError("Headings require one editorial sentence")
        elif self.type == "table":
            structured = bool(self.table_headers or self.table_rows)
            if structured:
                if len(self.table_headers) < 2 or not self.table_rows:
                    raise ValueError("Structured tables require at least two headers and one row")
                width = len(self.table_headers)
                if any(len(row.cells) != width for row in self.table_rows):
                    raise ValueError("Every structured table row must match the header width")
                if self.sentences:
                    raise ValueError("Structured tables must not duplicate content in sentences")
            elif not self.sentences:
                raise ValueError("Tables require structured cells or legacy row sentences")
        elif not self.sentences:
            raise ValueError("Non-table blocks require at least one sentence")
        if self.type != "table" and (self.table_headers or self.table_rows):
            raise ValueError("Only table blocks may contain table cells")
        if self.type != "callout" and (self.callout_kind or self.callout_title):
            raise ValueError("Only callout blocks may contain callout metadata")
        if self.type == "callout" and self.callout_kind is None:
            self.callout_kind = "note"
        return self


class V3WriterSectionOutput(V3OpenAIStrictOutput):
    """One resumable writing unit for a single active knowledge section."""

    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    title: str | None = Field(default=None, min_length=15, max_length=100)
    blocks: list[V3DraftBlock] = Field(min_length=2, max_length=60)
    covered_method_ids: list[str] = Field(default_factory=list, max_length=30)
    scope_confirmation: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def validate_section_unit(self):
        positions = [block.position for block in self.blocks]
        if positions != list(range(len(self.blocks))):
            raise ValueError("Writer section block positions must be local and contiguous")
        if any(block.section_id != self.section_id for block in self.blocks):
            raise ValueError("Every writer section block must belong to its declared section")
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("Writer section block IDs must be unique")
        sentence_ids = [
            sentence.sentence_id
            for block in self.blocks
            for sentence in block.content_sentences
        ]
        if len(sentence_ids) != len(set(sentence_ids)):
            raise ValueError("Writer section sentence IDs must be unique")
        if len(self.covered_method_ids) != len(set(self.covered_method_ids)):
            raise ValueError("Writer section method IDs must be unique")
        block_methods = {block.method_id for block in self.blocks if block.method_id}
        if not set(self.covered_method_ids).issubset(block_methods):
            raise ValueError("Writer section methods must be represented by tagged blocks")
        return self


class V3WriterOutput(V3OpenAIStrictOutput):
    title: str = Field(min_length=15, max_length=100)
    blocks: list[V3DraftBlock] = Field(min_length=10, max_length=300)
    covered_section_ids: list[str] = Field(min_length=3, max_length=50)
    covered_method_ids: list[str] = Field(default_factory=list, max_length=30)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=0)
    scope_confirmation: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def block_positions(self):
        positions = [block.position for block in self.blocks]
        if positions != list(range(len(self.blocks))):
            raise ValueError("V3 draft block positions must be contiguous")
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("V3 draft block IDs must be unique")
        sentence_ids = [
            sentence.sentence_id
            for block in self.blocks
            for sentence in block.content_sentences
        ]
        if len(sentence_ids) != len(set(sentence_ids)):
            raise ValueError("V3 draft sentence IDs must be unique")
        if len(self.covered_section_ids) != len(set(self.covered_section_ids)):
            raise ValueError("Covered section IDs must be unique")
        if len(self.covered_method_ids) != len(set(self.covered_method_ids)):
            raise ValueError("Covered method IDs must be unique")
        block_sections = {block.section_id for block in self.blocks}
        if not set(self.covered_section_ids).issubset(block_sections):
            raise ValueError("Covered sections must be represented by at least one block")
        block_methods = {block.method_id for block in self.blocks if block.method_id}
        if not set(self.covered_method_ids).issubset(block_methods):
            raise ValueError("Covered methods must be represented by tagged blocks")
        return self


class ReviewFinding(V3OpenAIStrictOutput):
    block_id: UUID | None = None
    section_id: str = Field(min_length=3, max_length=100)
    issue_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    issue: str = Field(min_length=5, max_length=2000)
    severity: Literal["minor", "major", "critical"]
    required_action: str = Field(min_length=5, max_length=1500)


class V3DevelopmentReview(V3OpenAIStrictOutput):
    status: Literal["passed", "rewrite", "blocked"]
    promise_fulfilled: bool
    procedural_completeness_score: float = Field(ge=0, le=1)
    decision_usefulness_score: float = Field(ge=0, le=1)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    rewrite_block_ids: list[UUID] = Field(default_factory=list, max_length=100)
    missing_research: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def status_consistency(self):
        severe = any(item.severity in {"major", "critical"} for item in self.findings)
        if self.status == "passed" and (
            severe or self.missing_research or self.rewrite_block_ids or not self.promise_fulfilled
        ):
            raise ValueError(
                "A passed development review cannot contain major gaps, rewrites, "
                "missing research or an unfulfilled promise"
            )
        if self.status == "rewrite" and not self.rewrite_block_ids:
            raise ValueError("A rewrite development review requires rewrite_block_ids")
        if self.status == "blocked" and not (self.missing_research or severe):
            raise ValueError("A blocked development review requires a blocking reason")
        return self


class ClaimCheck(V3OpenAIStrictOutput):
    block_id: UUID
    sentence_id: UUID
    sentence_text: str = Field(min_length=1, max_length=5000)
    claim_ids: list[UUID] = Field(default_factory=list, max_length=20)
    status: Literal["supported", "conditional_language_required", "unsupported", "contradicted"]
    issue: str = Field(default="", max_length=1500)


class V3FactCheckReview(V3OpenAIStrictOutput):
    status: Literal["passed", "rewrite", "blocked"]
    checks: list[ClaimCheck] = Field(default_factory=list, max_length=500)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    rewrite_block_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def status_consistency(self):
        unsupported = any(
            item.status in {"unsupported", "contradicted", "conditional_language_required"}
            for item in self.checks
        )
        severe = any(item.severity in {"major", "critical"} for item in self.findings)
        if self.status == "passed" and (unsupported or severe or self.rewrite_block_ids):
            raise ValueError(
                "A passed fact-check cannot contain unsupported checks, major findings or rewrites"
            )
        if self.status == "rewrite" and not self.rewrite_block_ids:
            raise ValueError("A rewrite fact-check requires rewrite_block_ids")
        if self.status == "blocked" and not (unsupported or severe):
            raise ValueError("A blocked fact-check requires an unsupported or severe finding")
        check_keys = [item.sentence_id for item in self.checks]
        if len(check_keys) != len(set(check_keys)):
            raise ValueError("Fact-check sentence checks must be unique by sentence_id")
        return self


class V3LanguageReview(V3OpenAIStrictOutput):
    status: Literal["passed", "rewrite", "blocked"]
    naturalness_score: float = Field(ge=0, le=1)
    rhythm_score: float = Field(ge=0, le=1)
    template_language_score: float = Field(ge=0, le=1)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    rewrite_block_ids: list[UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def status_consistency(self):
        severe = any(item.severity in {"major", "critical"} for item in self.findings)
        if self.status == "passed" and (severe or self.rewrite_block_ids):
            raise ValueError(
                "A passed language review cannot contain major findings or rewrite blocks"
            )
        if self.status == "rewrite" and not self.rewrite_block_ids:
            raise ValueError("A rewrite language review requires rewrite_block_ids")
        if self.status == "blocked" and not severe:
            raise ValueError("A blocked language review requires a major or critical finding")
        return self


class V3BlockRevision(V3OpenAIStrictOutput):
    block_id: UUID
    revised_block: V3DraftBlock
    reason: str = Field(min_length=5, max_length=1000)
    meaning_changed: bool = False


class V3BlockRevisionOutput(V3OpenAIStrictOutput):
    revisions: list[V3BlockRevision] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_revisions(self):
        ids = [item.block_id for item in self.revisions]
        if len(ids) != len(set(ids)):
            raise ValueError("Block revisions must be unique")
        return self


class ProceduralQualityEvaluation(V3OpenAIStrictOutput):
    rubric_version: Literal[
        "quality-rubric.procedural-guide.v3",
        "quality-rubric.universal-editorial.v1",
    ] = "quality-rubric.procedural-guide.v3"
    architecture_type: str = Field(default="procedural_decision_guide", max_length=80)
    status: Literal["passed", "blocked"]
    overall_score: float = Field(ge=0, le=1)
    research_quality: float = Field(ge=0, le=1)
    knowledge_model_quality: float = Field(ge=0, le=1)
    comparison_decision_quality: float = Field(ge=0, le=1)
    procedural_completeness: float = Field(ge=0, le=1)
    practical_utility: float = Field(ge=0, le=1)
    editorial_coherence: float = Field(ge=0, le=1)
    naturalness: float = Field(ge=0, le=1)
    factual_link_integrity: float = Field(ge=0, le=1)
    critical_blockers: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_pass(self):
        axes = [
            self.research_quality,
            self.knowledge_model_quality,
            self.comparison_decision_quality,
            self.procedural_completeness,
            self.practical_utility,
            self.editorial_coherence,
            self.naturalness,
            self.factual_link_integrity,
        ]
        if self.status == "passed" and (
            self.overall_score < 0.85
            or self.critical_blockers
            or min(axes) < 0.70
        ):
            raise ValueError("Editorial quality cannot pass below the rubric thresholds")
        if self.status == "blocked" and not self.critical_blockers:
            raise ValueError("Blocked editorial quality requires a blocker")
        return self
