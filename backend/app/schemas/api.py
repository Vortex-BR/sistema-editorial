from datetime import datetime
from typing import Any, Literal
from uuid import UUID
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.schemas.editorial_v3 import procedural_structural_minimum_words


class PublicationProfileWrite(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    brand_name: str = Field(min_length=2, max_length=200)
    website_url: str | None = Field(default=None, max_length=500)
    segment: str = Field(min_length=2, max_length=160)
    brand_description: str = Field(min_length=10, max_length=5000)
    mission: str | None = Field(default=None, max_length=3000)
    value_proposition: str | None = Field(default=None, max_length=3000)
    products_services: list[str] = Field(default_factory=list, max_length=30)
    audience_description: str = Field(min_length=3, max_length=5000)
    audience_age_min: int | None = Field(default=None, ge=0, le=120)
    audience_age_max: int | None = Field(default=None, ge=0, le=120)
    audience_life_stage: str | None = Field(default=None, max_length=200)
    audience_knowledge_level: Literal[
        "beginner", "intermediate", "advanced", "mixed"
    ] = "mixed"
    audience_goals: list[str] = Field(default_factory=list, max_length=30)
    audience_pain_points: list[str] = Field(default_factory=list, max_length=30)
    tone_of_voice: str = Field(min_length=3, max_length=3000)
    brand_terms: list[str] = Field(default_factory=list, max_length=50)
    forbidden_terms: list[str] = Field(default_factory=list, max_length=50)
    primary_markets: list[str] = Field(default_factory=list, max_length=20)
    editorial_goals: list[str] = Field(default_factory=list, max_length=30)
    commercial_objective: str | None = Field(default=None, max_length=3000)
    preferred_cta: str | None = Field(default=None, max_length=1000)
    research_summary: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def validate_age_range(self):
        if (
            self.audience_age_min is not None
            and self.audience_age_max is not None
            and self.audience_age_min > self.audience_age_max
        ):
            raise ValueError("audience_age_min must not exceed audience_age_max")
        return self


class PublicationProfileRead(PublicationProfileWrite):
    id: UUID
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ContentBriefWrite(BaseModel):
    content_objective: str = Field(default="", max_length=3000)
    primary_keyword: str = Field(default="", max_length=200)
    research_subject: str = Field(default="", max_length=1000)
    secondary_keywords: list[str] = Field(default_factory=list, max_length=30)
    segment: str = Field(default="", max_length=200)
    reader_context: str = Field(default="", max_length=5000)
    reader_age_min: int | None = Field(default=None, ge=0, le=120)
    reader_age_max: int | None = Field(default=None, ge=0, le=120)
    reader_life_stage: str = Field(default="", max_length=200)
    reader_knowledge_level: Literal["beginner", "intermediate", "advanced", "mixed"] = (
        "mixed"
    )
    reader_goal: str = Field(default="", max_length=3000)
    commercial_objective: str = Field(default="", max_length=3000)
    offer: str = Field(default="", max_length=3000)
    desired_action: str = Field(default="", max_length=1000)
    minimum_words: int | None = Field(default=None, ge=300, le=5000)
    maximum_words: int | None = Field(default=None, ge=400, le=6000)
    minimum_h2: int | None = Field(default=None, ge=2, le=10)
    minimum_h3: int | None = Field(default=None, ge=0, le=12)
    required_sections: list[str] = Field(default_factory=list, max_length=12)
    required_methods: list[str] = Field(default_factory=list, max_length=12)
    required_approach_type: Literal[
        "method",
        "environment",
        "system",
        "strategy",
        "technique",
        "material",
        "channel",
        "format",
        "option",
        "other",
    ] = "method"
    preferred_sources: list[str] = Field(default_factory=list, max_length=30)
    prohibited_sources: list[str] = Field(default_factory=list, max_length=30)
    maximum_source_age_days: int | None = Field(default=None, ge=1, le=36500)
    claims_to_avoid: list[str] = Field(default_factory=list, max_length=30)
    related_page_url: str = Field(default="", max_length=500)
    voice_override: str = Field(default="", max_length=3000)
    approved_style_examples: list[str] = Field(default_factory=list, max_length=10)
    # A complete editorial protocol can legitimately be much longer than the
    # short contextual fields above. Keep a finite bound so the brief cannot
    # grow without limit, while allowing detailed production instructions.
    additional_context: str = Field(default="", max_length=20_000)
    editorial_content_type: Literal[
        "procedural_decision_guide",
        "procedural_how_to",
        "explanatory_guide",
        "comparison",
        "troubleshooting",
        "commercial_education",
    ] = "explanatory_guide"
    # These limits mirror the effective bounds of ContentKnowledgeContract and
    # its nested hierarchy nodes. Accepting a larger briefing here only to
    # reject it inside a worker turns user input into an opaque technical error.
    reader_start_state: str = Field(default="", max_length=1000)
    reader_final_state: str = Field(default="", max_length=1000)
    article_promise: str = Field(default="", max_length=3000)
    scope_limit: str = Field(default="", max_length=2000)
    requires_method_comparison: bool = False
    requires_external_reference_per_method: bool = False

    @field_validator("required_methods")
    @classmethod
    def normalize_required_methods(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in value:
            label = " ".join(str(raw).split()).strip()
            if len(label) < 3:
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(label[:200])
        return normalized

    @model_validator(mode="after")
    def validate_age_range(self):
        if (
            self.reader_age_min is not None
            and self.reader_age_max is not None
            and self.reader_age_min > self.reader_age_max
        ):
            raise ValueError("reader_age_min must not exceed reader_age_max")
        if (
            self.minimum_words is not None
            and self.maximum_words is not None
            and self.minimum_words > self.maximum_words
        ):
            raise ValueError("minimum_words must not exceed maximum_words")
        return self


class ProjectCreate(BaseModel):
    name: str = Field(min_length=3, max_length=200)
    # Topic text is interpolated into graph questions whose total bound is 500.
    # 380 leaves room for the longest deterministic question template.
    topic: str = Field(min_length=3, max_length=380)
    search_intent: str = "informational"
    audience: str = Field(min_length=3)
    language: Literal["pt-BR", "en-US", "es-ES"] = "pt-BR"
    niche: str | None = None
    publication_profile_id: UUID | None = None
    briefing: ContentBriefWrite = Field(default_factory=ContentBriefWrite)
    content_type: str = Field(
        default="article",
        pattern=r"^(article|existing_article_update|institutional_page|service_page|landing_page|category_page|product_page|product_description)$",
    )
    editorial_pipeline_version: Literal["v2", "v3"] = "v2"
    start_immediately: bool = True

    @model_validator(mode="after")
    def require_rich_brief_for_profiled_content(self):
        if self.publication_profile_id is None:
            return self
        required = {
            "content_objective": self.briefing.content_objective,
            "primary_keyword": self.briefing.primary_keyword,
            "reader_context": self.briefing.reader_context,
            "reader_goal": self.briefing.reader_goal,
        }
        missing = [key for key, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(
                "profiled content requires a complete editorial brief: "
                + ", ".join(missing)
            )
        return self

    @model_validator(mode="after")
    def require_v3_knowledge_contract_brief(self):
        if self.editorial_pipeline_version != "v3":
            return self

        required = {
            "reader_start_state": self.briefing.reader_start_state,
            "reader_final_state": self.briefing.reader_final_state,
            "article_promise": self.briefing.article_promise,
            "scope_limit": self.briefing.scope_limit,
        }
        missing = [key for key, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(
                "editorial v3 requires a complete knowledge-contract brief: "
                + ", ".join(missing)
            )

        if self.briefing.editorial_content_type == "procedural_decision_guide":
            if not self.briefing.requires_method_comparison:
                raise ValueError("procedural decision guides require method comparison")
            if not self.briefing.requires_external_reference_per_method:
                raise ValueError(
                    "procedural decision guides require one external reference per method"
                )
            if len(self.briefing.required_methods) < 2:
                raise ValueError(
                    "procedural decision guides require at least two required_methods"
                )
            estimated_minimum = procedural_structural_minimum_words(
                len(self.briefing.required_methods)
            )
            if (
                self.briefing.maximum_words is not None
                and self.briefing.maximum_words < estimated_minimum
            ):
                raise ValueError(
                    "maximum_words is below the structural minimum for this "
                    f"procedural scope ({estimated_minimum} words)"
                )
        elif self.briefing.required_methods:
            raise ValueError(
                "required_methods are only valid for procedural decision guides"
            )
        return self


class AgentContextPreviewRequest(BaseModel):
    agent_role: str = Field(min_length=1, max_length=50)
    project_id: UUID
    pipeline_run_id: UUID | None = None
    task: str = Field(
        min_length=1,
        max_length=50_000,
        validation_alias=AliasChoices("task", "query"),
    )

    @field_validator("task")
    @classmethod
    def task_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task must contain text")
        return value


class ProjectRead(BaseModel):
    id: UUID
    name: str
    topic: str
    search_intent: str
    audience: str
    language: str
    niche: str | None
    publication_profile_id: UUID | None = None
    briefing: ContentBriefWrite = Field(default_factory=ContentBriefWrite)
    content_type: str
    editorial_pipeline_version: str = "v2"
    status: str
    last_run_status: str | None = None
    current_stage: str
    created_at: datetime
    model_config = {"from_attributes": True}


class ProjectCreateRead(ProjectRead):
    start_requested: bool = False
    pipeline_run_id: UUID | None = None
    run_created: bool = False
    dispatch_status: str | None = None
    start_error: dict[str, Any] | None = None


class V3KnowledgeContractPreviewRead(BaseModel):
    contract: dict[str, Any]
    checksum: str = Field(min_length=64, max_length=64)
    validation: dict[str, Any]
    execution_enabled: bool
    warning: str


class V3KnowledgeContractRead(V3KnowledgeContractPreviewRead):
    id: UUID
    version: int = Field(ge=1)
    status: str
    created: bool


class CredentialWrite(BaseModel):
    provider: str
    value: str = Field(min_length=8)


class CredentialRead(BaseModel):
    provider: str
    configured: bool
    last_four: str | None = None
    verified_at: datetime | None = None


class CredentialVerificationRead(BaseModel):
    provider: str
    verified: bool
    verified_at: datetime | None = None
    latency_ms: int
    model: str | None = None
    error_code: str | None = None


class CredentialRotationRequest(BaseModel):
    dry_run: bool = True
    confirmation: str | None = Field(default=None, max_length=32)


class CredentialRotationRead(BaseModel):
    key_count: int = Field(ge=1)
    total_credentials: int = Field(ge=0)
    already_primary: int = Field(ge=0)
    pending_rotation: int = Field(ge=0)
    rotated: int = Field(ge=0)
    dry_run: bool
    providers: list[str] = Field(default_factory=list, max_length=20)


class PipelineCheckpointRead(BaseModel):
    id: UUID
    sequence: int
    stage: str
    next_stage: str
    attempt: int
    contract_version: str
    resumable: bool
    completed_at: datetime


class PipelineTransitionRead(BaseModel):
    from_status: str = Field(alias="from")
    to_status: str = Field(alias="to")
    stage: str
    origin: str
    reason: str | None = None
    error_code: str | None = None
    created_at: datetime


class ProviderAttemptRead(BaseModel):
    id: UUID
    agent_run_id: UUID
    provider: str
    model: str
    target_kind: Literal["primary", "fallback"]
    run_attempt: int
    attempt_number: int
    status: Literal["succeeded", "failed", "invalid_output"]
    response_received: bool
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    http_status: int | None = None
    error_code: str | None = None
    error_category: str | None = None
    started_at: datetime
    finished_at: datetime


class AgentCallRead(BaseModel):
    id: UUID
    role: str
    attempt: int
    status: str
    provider: str | None = None
    model: str | None = None
    fallback_used: bool
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    latency_ms: int
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    http_status: int | None = None
    retryable: bool | None = None
    correlation_id: str | None = None
    recovered: bool = False
    recovery_code: str | None = None
    recovered_by_agent_run_id: UUID | None = None


class PipelineEventRead(BaseModel):
    sequence: int
    type: str
    stage: str
    stage_occurrence_id: UUID | None = None
    research_cycle: int | None = None
    editor_cycle: int | None = None
    run_attempt: int | None = None
    stage_attempt: int | None = None
    checkpoint_sequence: int | None = None
    agent_run_id: UUID | None = None
    payload: dict[str, Any]
    created_at: datetime


class HandoffRead(BaseModel):
    id: UUID
    sequence: int
    from_role: str
    to_role: str
    fact_ids: list[UUID | str]
    created_at: datetime


class ContentVersionRead(BaseModel):
    id: UUID
    article_id: UUID
    version: int
    editorial_status: str
    change_reason: str | None = None
    final_markdown: str | None = None
    final_html: str | None = None
    seo_metadata: dict[str, Any]
    source_report: dict[str, Any]
    created_at: datetime


class PipelineRunDetailRead(BaseModel):
    id: UUID
    project_id: UUID
    status: str
    current_stage: str
    attempt: int
    retryable: bool
    next_retry_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    last_successful_checkpoint: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    billed_prompt_tokens: int = 0
    billed_completion_tokens: int = 0
    estimated_external_cost_usd: float = 0
    checkpoints: list[PipelineCheckpointRead]
    transitions: list[PipelineTransitionRead]
    agent_calls: list[AgentCallRead]
    provider_attempts: list[ProviderAttemptRead]
    events: list[PipelineEventRead]
    handoffs: list[HandoffRead]
    content_versions: list[ContentVersionRead]
    execution_manifest: dict[str, Any] | None = None


class ProjectFactsRead(BaseModel):
    pipeline_run_id: UUID | None = None
    total: int
    approved: int


class PipelineRunSummaryRead(BaseModel):
    id: UUID
    status: str
    trigger_type: str
    current_stage: str
    attempt: int
    retryable: bool
    next_retry_at: datetime | None = None
    cancellation_requested_at: datetime | None = None
    last_successful_checkpoint: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    outcome_code: str | None = None


class PipelineRunCancellationRead(BaseModel):
    pipeline_run_id: UUID
    status: str
    cancellation_requested_at: datetime
    cancellation_pending: bool


class HumanEditorialReviewDecision(BaseModel):
    decision: Literal["approve", "reject", "request_revision"]
    reviewer: str = Field(min_length=2, max_length=160)
    observation: str | None = Field(default=None, max_length=10_000)

    @model_validator(mode="after")
    def reason_required_for_negative_decisions(self):
        if (
            self.decision in {"reject", "request_revision"}
            and not (self.observation or "").strip()
        ):
            raise ValueError(
                "observation is required when rejecting or requesting revision"
            )
        return self


class HumanEditorialReviewSummaryRead(BaseModel):
    id: UUID
    project_id: UUID
    pipeline_run_id: UUID
    article_version_id: UUID
    reviewer: str | None = None
    decision: str
    observation: str | None = None
    reviewed_at: datetime | None = None
    revision_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class HumanEditorialReviewRead(HumanEditorialReviewSummaryRead):
    review_package: dict[str, Any]


class HumanEditorialReviewDecisionRead(BaseModel):
    review: HumanEditorialReviewRead
    pipeline_run_status: str
    revision_run_id: UUID | None = None
    revision_created: bool = False
    duplicate: bool = False


class AgentRunSummaryRead(BaseModel):
    id: UUID
    pipeline_run_id: UUID
    role: str
    purpose: str | None = None
    status: str
    decision: str | None = None
    latency_ms: int
    cost: float
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    http_status: int | None = None
    retryable: bool | None = None
    correlation_id: str | None = None
    recovered: bool = False
    recovery_code: str | None = None
    recovered_by_agent_run_id: UUID | None = None


class ResearchDiagnosticRead(BaseModel):
    pipeline_run_id: UUID
    outcome_code: str | None = None
    decision: str | None = None
    coverage_complete: bool
    covered_question_count: int
    total_question_count: int
    recommended_fact_count: int
    distinct_source_count: int
    minimum_distinct_sources: int
    source_diversity_score: float
    missing_questions: list[str] = Field(default_factory=list)
    unresolved_conflicts: list[str] = Field(default_factory=list)
    rejection_reason_counts: dict[str, int] = Field(default_factory=dict)
    instructions: list[str] = Field(default_factory=list)


class ProjectArticleVersionRead(BaseModel):
    id: UUID
    article_id: UUID
    pipeline_run_id: UUID | None = None
    version: int
    title: str
    outline: list[Any]
    editorial_status: str
    markdown: str | None = None
    html: str | None = None
    seo_metadata: dict[str, Any]
    source_report: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EditorialFindingRead(BaseModel):
    category: str
    severity: str
    issue: str
    suggested_action: str


class EditorialDiagnosticRead(BaseModel):
    pipeline_run_id: UUID
    decision: str | None = None
    model_decision: str | None = None
    resolution: str | None = None
    blocking_finding_count: int = 0
    findings: list[EditorialFindingRead] = Field(default_factory=list)


class ProjectDetailRead(BaseModel):
    project: ProjectRead
    outcome_code: str | None = None
    facts: ProjectFactsRead
    pipeline_runs: list[PipelineRunSummaryRead]
    latest_pipeline_run: PipelineRunSummaryRead | None = None
    selected_pipeline_run: PipelineRunSummaryRead | None = None
    runs: list[AgentRunSummaryRead]
    article_version: ProjectArticleVersionRead | None = None
    article_pipeline_run_id: UUID | None = None
    article_matches_selected_pipeline_run: bool | None = None
    execution_manifest: dict[str, Any] | None = None
    quality_evaluation: dict[str, Any] | None = None
    research_diagnostic: ResearchDiagnosticRead | None = None
    v3_research_runtime: dict[str, Any] | None = None
    editorial_diagnostic: EditorialDiagnosticRead | None = None
    human_review: HumanEditorialReviewRead | None = None
    human_review_history: list[HumanEditorialReviewSummaryRead] = Field(
        default_factory=list
    )


class FactRead(BaseModel):
    id: UUID
    project_id: UUID
    pipeline_run_id: UUID
    claim: str
    source_id: UUID
    source_snapshot_id: UUID
    confidence: float
    approved: bool
    locator: str
    conflict_group: str | None = None


class DashboardStatsRead(BaseModel):
    total_projects: int
    completed: int
    blocked_runs: int
    failed_runs: int
    cancelled_runs: int
    approved_facts: int
    distinct_sources: int
    total_cost_usd: float


class DashboardRead(BaseModel):
    stats: DashboardStatsRead
    recent_projects: list[ProjectRead]


class ModelRouteRead(BaseModel):
    agent_role: str
    primary_provider: str
    primary_model: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class SkillRead(BaseModel):
    id: UUID
    skill_id: str
    kind: str
    version: str
    enabled: bool
    stable: bool
    niche: str | None = None
    project_id: UUID | None = None
    fingerprint: str | None = None
    lifecycle_status: str
    auto_inject: bool


class SuperiorSkillRead(BaseModel):
    skill_id: str
    scope: str
    agent_role: str | None = None
    version: str
    enabled: bool


class EmbeddingRouteRead(BaseModel):
    provider: str
    model: str
    dimensions: int | None = None


class PolicyRead(BaseModel):
    learned_skill_stability_threshold: int
    auto_inject_unstable_skills: bool
    superior_skills_mode: str


class ConfigRead(BaseModel):
    routes: list[ModelRouteRead]
    route_defaults: dict[str, dict[str, ModelRouteRead]] = Field(default_factory=dict)
    skills: list[SkillRead]
    superior_skills: list[SuperiorSkillRead]
    embedding_route: EmbeddingRouteRead | None = None
    policy: PolicyRead


class WebSocketTicketRequest(BaseModel):
    pipeline_run_id: UUID


class WebSocketTicketRead(BaseModel):
    ticket: str = Field(min_length=43, max_length=43)
    expires_in: int = Field(gt=0, le=60)
    protocol: str


class ModelRouteWrite(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_role: str
    primary_provider: str
    primary_model: str
    fallback_provider: str | None = None
    fallback_model: str | None = None
    parameters: Any = Field(default_factory=dict)


class SuperiorSkillVersionWrite(BaseModel):
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    definition: dict
    created_by: str = Field(default="admin", min_length=2, max_length=120)


class LearningDecisionWrite(BaseModel):
    decision: str = Field(pattern=r"^(approved|rejected|archived)$")


class LearnedSkillLifecycleAction(BaseModel):
    action: Literal[
        "approve",
        "promote",
        "activate",
        "disable",
        "rollback",
        "reject",
    ]
    reason: str = Field(min_length=3, max_length=500)


class EmbeddingRouteWrite(BaseModel):
    provider: str = Field(pattern=r"^(openai|gemini)$")
    model: str = Field(min_length=2, max_length=100)
    dimensions: int | None = Field(default=None, ge=1, le=10000)


class StyleSourceWrite(BaseModel):
    url: str = Field(min_length=10)
    title: str = Field(min_length=3)
    content: str = Field(min_length=100, max_length=50000)
    project_id: UUID | None = None


class AgentMemoryWrite(BaseModel):
    agent_role: str
    content: str = Field(min_length=10, max_length=4000)
    memory_kind: str = Field(min_length=2, max_length=50)
    project_id: UUID | None = None
    niche: str | None = Field(default=None, max_length=120)
    confidence_score: float = Field(default=0.8, ge=0, le=1)
