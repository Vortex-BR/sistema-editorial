from datetime import datetime
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


Score = Annotated[float, Field(ge=0, le=1)]


class OpenAIStrictOutput(BaseModel):
    """Opt an output contract into OpenAI strict Structured Outputs.

    Runtime defaults remain useful for backward-compatible persisted artifacts,
    while the provider-facing JSON Schema marks every model field as required,
    as demanded by OpenAI strict Structured Outputs.
    """

    openai_strict: ClassVar[bool] = True
    model_config = ConfigDict(extra="forbid")

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)

        def require_all(node: object) -> None:
            if isinstance(node, list):
                for item in node:
                    require_all(item)
                return
            if not isinstance(node, dict):
                return
            properties = node.get("properties")
            if node.get("type") == "object" and isinstance(properties, dict):
                if node.get("additionalProperties") is False:
                    node["required"] = list(properties)
            for value in node.values():
                require_all(value)

        require_all(schema)
        return schema


class SourceType(str, Enum):
    scientific = "scientific"
    government = "government"
    university = "university"
    industry = "industry"
    practical = "practical"
    news = "news"
    forum = "forum"


class AgentContext(BaseModel):
    project_id: UUID
    run_id: UUID
    language: str = "pt-BR"
    active_skill_versions: dict[str, str] = Field(default_factory=dict)


class LocalizedSearchQueries(OpenAIStrictOutput):
    united_states: str = Field(min_length=8)
    spain: str = Field(min_length=8)
    switzerland: str = Field(min_length=8)
    brazil: str | None = Field(min_length=8)


class ResearchQuestionContract(OpenAIStrictOutput):
    id: UUID | None
    question: str = Field(min_length=8)
    priority: int = Field(ge=1, le=20)
    importance: Literal["core", "supporting", "optional"]
    rationale: str = Field(min_length=8, max_length=500)
    node_ids: list[str] = Field(min_length=1, max_length=4)
    expected_source_types: list[SourceType] = Field(min_length=1)
    semantic_terms: list[str]
    search_queries: LocalizedSearchQueries


class PlannerInput(BaseModel):
    context: AgentContext
    topic: str = Field(min_length=3)
    search_intent: Literal[
        "informational", "transactional", "commercial", "navigational"
    ]
    audience: str = Field(min_length=3)
    niche: str | None = None


class ArticleSectionBlueprint(OpenAIStrictOutput):
    node_ids: list[str] = Field(min_length=1, max_length=4)
    heading_intent: str = Field(min_length=5, max_length=120)
    section_purpose: str = Field(min_length=10, max_length=300)
    claim_topics: list[str] = Field(max_length=8)
    transition_to_next: str = Field(max_length=240)
    target_words: int = Field(ge=60, le=700)


class ArticleBlueprint(OpenAIStrictOutput):
    reader_decision: str = Field(min_length=10, max_length=300)
    central_promise: str = Field(min_length=10, max_length=300)
    thesis: str = Field(min_length=10, max_length=300)
    opening_strategy: str = Field(min_length=10, max_length=300)
    conclusion_strategy: str = Field(min_length=10, max_length=300)
    sections: list[ArticleSectionBlueprint] = Field(min_length=3, max_length=14)


class ResearchPlanOutput(OpenAIStrictOutput):
    rationale: str
    questions: list[ResearchQuestionContract] = Field(min_length=3, max_length=16)
    competitor_angles: list[str]
    content_gaps: list[str]
    semantic_keywords: list[str] = Field(min_length=3)
    editorial_blueprint: ArticleBlueprint


class SEOBrief(BaseModel):
    focus_keyphrase: str = Field(min_length=3, max_length=55)
    related_keyphrases: list[str] = Field(default_factory=list, max_length=8)
    search_intent: Literal[
        "informational", "transactional", "commercial", "navigational"
    ]
    article_angle: str = Field(min_length=8, max_length=180)
    recommended_sections: list[str] = Field(min_length=3, max_length=8)
    minimum_words: int = Field(default=650, ge=300, le=5000)
    maximum_words: int = Field(default=1000, ge=400, le=6000)
    minimum_h2: int = Field(default=3, ge=2, le=10)
    minimum_h3: int = Field(default=0, ge=0, le=12)

    @model_validator(mode="after")
    def validate_word_range(self):
        if self.minimum_words > self.maximum_words:
            raise ValueError("minimum_words must not exceed maximum_words")
        return self


class ResearchInput(BaseModel):
    context: AgentContext
    plan: ResearchPlanOutput
    missing_questions: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    prior_fact_ids: list[UUID] = Field(default_factory=list)


class SourceRecord(BaseModel):
    url: AnyHttpUrl
    title: str
    domain: str
    author: str | None = None
    publisher: str | None = None
    source_type: SourceType
    published_at: datetime | None = None
    accessed_at: datetime
    content_hash: str
    reliability_score: Score
    extraction_method: str
    snapshot_text: str = Field(min_length=20)


class AtomicFact(BaseModel):
    id: UUID | None
    research_question: str
    knowledge_node_ids: list[str] = Field(default_factory=list, max_length=4)
    claim_text: str = Field(min_length=5)
    exact_quote: str | None = None
    source_locator: str
    source: SourceRecord
    confidence_score: Score
    conflict_group: str | None


class ResearchOutput(BaseModel):
    facts: list[AtomicFact]
    queries_executed: list[str]
    failed_urls: list[str] = Field(default_factory=list)


class ExtractedFactCandidate(OpenAIStrictOutput):
    source_url: AnyHttpUrl
    claim_text: str = Field(min_length=5)
    exact_quote: str = Field(min_length=5)
    source_locator: str = Field(min_length=1)
    confidence_score: Score
    conflict_group: str | None


class FactExtractionOutput(OpenAIStrictOutput):
    facts: list[ExtractedFactCandidate]


class ResearchAuditInput(BaseModel):
    context: AgentContext
    plan: ResearchPlanOutput
    facts: list[AtomicFact]
    minimum_distinct_sources: int = Field(default=5, ge=2)


class FactAuditRejection(BaseModel):
    fact_id: UUID
    reason_code: Literal[
        "off_topic",
        "wrong_scope",
        "unsupported",
        "duplicate",
        "conflict",
        "low_quality",
    ]


class ResearchAuditOutput(BaseModel):
    decision: Literal["approved", "insufficient"]
    coverage_by_question: dict[str, Score]
    missing_questions: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    source_diversity_score: Score
    approved_fact_ids: list[UUID] = Field(default_factory=list)
    fact_rejections: list[FactAuditRejection] = Field(default_factory=list)
    instructions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_gate(self):
        if self.decision == "approved" and (
            self.missing_questions
            or self.unresolved_conflicts
            or self.source_diversity_score < 0.6
        ):
            raise ValueError(
                "Research cannot be approved with gaps, conflicts, or low source diversity"
            )
        approved = set(self.approved_fact_ids)
        rejected = [rejection.fact_id for rejection in self.fact_rejections]
        if len(rejected) != len(set(rejected)):
            raise ValueError("Each fact can have at most one rejection decision")
        if approved.intersection(rejected):
            raise ValueError("A fact cannot be both approved and rejected")
        return self


class EvidenceReference(OpenAIStrictOutput):
    fact_id: UUID
    entailment_score: Score


class DraftSentence(OpenAIStrictOutput):
    text: str
    is_factual: bool
    evidence: list[EvidenceReference] = Field(min_length=0)

    @model_validator(mode="after")
    def evidence_matches_sentence_kind(self):
        if self.is_factual and not self.evidence:
            raise ValueError(
                "Every factual sentence must cite at least one approved fact"
            )
        if not self.is_factual and self.evidence:
            raise ValueError(
                "Editorial transitions and headings must not carry fact evidence"
            )
        return self


class DraftBlock(OpenAIStrictOutput):
    block_id: UUID | None
    type: Literal["h1", "h2", "h3", "paragraph", "list"]
    position: int = Field(ge=0)
    node_ids: list[str] = Field(default_factory=list, max_length=4)
    sentences: list[DraftSentence] = Field(min_length=1)

    @model_validator(mode="after")
    def headings_are_single_sentence(self):
        if self.type in {"h1", "h2", "h3"} and len(self.sentences) != 1:
            raise ValueError("Headings must contain exactly one sentence")
        return self


class WriterInput(BaseModel):
    context: AgentContext
    plan: ResearchPlanOutput
    approved_facts: list[AtomicFact]
    rewrite_block_ids: list[UUID]
    editorial_feedback: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def only_approved_facts_with_ids(self):
        if not self.approved_facts or any(f.id is None for f in self.approved_facts):
            raise ValueError("Writer requires persisted, gatekeeper-approved facts")
        return self


class WriterOutput(OpenAIStrictOutput):
    title: str = Field(min_length=15, max_length=60)
    title_evidence: list[EvidenceReference] = Field(min_length=0)
    blocks: list[DraftBlock] = Field(min_length=1)
    covered_node_ids: list[str] = Field(default_factory=list, max_length=30)
    unsupported_claims: list[str] = Field(max_length=0)

    @model_validator(mode="after")
    def no_unsupported_claims(self):
        if self.unsupported_claims:
            raise ValueError("Draft is blocked while unsupported claims exist")
        return self


class WriterRevisionOutput(OpenAIStrictOutput):
    blocks: list[DraftBlock] = Field(min_length=1)
    covered_node_ids: list[str] = Field(default_factory=list, max_length=30)
    unsupported_claims: list[str] = Field(max_length=0)

    @model_validator(mode="after")
    def no_unsupported_claims(self):
        if self.unsupported_claims:
            raise ValueError("Revision is blocked while unsupported claims exist")
        return self


class FidelityFinding(OpenAIStrictOutput):
    block_id: UUID
    sentence: str
    issue: str
    severity: Literal["minor", "major", "critical"]
    suggested_action: str


class EditorInput(BaseModel):
    context: AgentContext
    draft: WriterOutput
    approved_facts: list[AtomicFact]


class EditorOutput(OpenAIStrictOutput):
    decision: Literal["approved", "rewrite", "rejected"]
    fidelity_findings: list[FidelityFinding]
    language_findings: list[FidelityFinding]
    rewrite_block_ids: list[UUID]
    revised_blocks: list[DraftBlock] = Field(max_length=12)
    preserved_fact_ids: list[UUID]
    open_evidence_gaps: list[str] = Field(max_length=12)

    @model_validator(mode="after")
    def rewrites_are_targeted(self):
        if self.decision == "rewrite" and not (
            self.rewrite_block_ids or self.revised_blocks
        ):
            raise ValueError("Rewrite decisions must identify or revise blocks")
        revised_ids = {block.block_id for block in self.revised_blocks}
        if None in revised_ids:
            raise ValueError("Editor revisions must preserve existing block IDs")
        if (
            self.rewrite_block_ids
            and revised_ids
            and not revised_ids.issubset(set(self.rewrite_block_ids))
        ):
            raise ValueError("Editor may revise only identified blocks")
        blocking = [
            finding
            for finding in (*self.fidelity_findings, *self.language_findings)
            if finding.severity in {"major", "critical"}
        ]
        if self.decision == "approved" and (blocking or self.open_evidence_gaps):
            raise ValueError(
                "Draft with major findings or evidence gaps cannot be approved"
            )
        return self


class FinalizerInput(BaseModel):
    context: AgentContext
    approved_draft: WriterOutput
    facts: list[AtomicFact]


class FinalizerOutput(BaseModel):
    markdown: str
    html: str
    seo_metadata: dict[str, str | dict | list]
    source_report: dict
    unsupported_claim_count: Literal[0] = 0


class SkillCandidate(OpenAIStrictOutput):
    niche: str
    title: str
    rules: list[str]
    evidence_article_id: UUID
    confidence_score: Score
    auto_inject: Literal[False]


class CuratorInput(BaseModel):
    context: AgentContext
    article_id: UUID
    niche: str
    successful_patterns: list[str]
    major_rework_count: int


class AgentMemoryCandidate(OpenAIStrictOutput):
    agent_role: Literal[
        "planner",
        "researcher",
        "research_gatekeeper",
        "writer",
        "editor",
        "skill_curator",
    ]
    memory_kind: Literal["method", "preference", "failure_pattern", "quality_pattern"]
    content: str = Field(min_length=20, max_length=500)
    confidence_score: Score


class CuratorOutput(OpenAIStrictOutput):
    candidates: list[SkillCandidate]
    stability_threshold: int = Field(ge=3)
    memory_candidates: list[AgentMemoryCandidate]


class StylePatternCandidate(BaseModel):
    pattern_type: Literal[
        "opening", "structure", "rhythm", "evidence", "headings", "transitions"
    ]
    description: str = Field(min_length=20, max_length=500)
    source_urls: list[AnyHttpUrl] = Field(min_length=3, max_length=8)


class StylePatternExtractionOutput(BaseModel):
    patterns: list[StylePatternCandidate] = Field(default_factory=list, max_length=6)
