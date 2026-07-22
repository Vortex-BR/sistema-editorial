import enum
import uuid
from datetime import datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from app.db.base import Base, TimestampMixin, UUIDMixin


class ProjectStatus(str, enum.Enum):
    draft = "draft"
    queued = "queued"
    running = "running"
    needs_review = "needs_review"
    needs_human_approval = "needs_human_approval"
    blocked = "blocked"
    completed = "completed"
    rejected = "rejected"
    failed = "failed"


class ContentType(str, enum.Enum):
    article = "article"
    existing_article_update = "existing_article_update"
    institutional_page = "institutional_page"
    service_page = "service_page"
    landing_page = "landing_page"
    category_page = "category_page"
    product_page = "product_page"
    product_description = "product_description"


class EditorialPipelineVersion(str, enum.Enum):
    v2 = "v2"
    v3 = "v3"


class PipelineRunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    waiting_retry = "waiting_retry"
    needs_review = "needs_review"
    needs_human_approval = "needs_human_approval"
    blocked = "blocked"
    failed = "failed"
    cancelled = "cancelled"
    completed = "completed"
    rejected = "rejected"


class PipelineDispatchStatus(str, enum.Enum):
    claimed = "claimed"
    sent = "sent"
    failed = "failed"
    expired = "expired"
    consumed = "consumed"


class TriggerType(str, enum.Enum):
    api = "api"
    automatic = "automatic"
    retry = "retry"
    resume = "resume"
    legacy = "legacy"


class GateDecision(str, enum.Enum):
    approved = "approved"
    insufficient = "insufficient"
    rewrite = "rewrite"
    rejected = "rejected"


class RunStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    blocked = "blocked"


class SkillKind(str, enum.Enum):
    default = "default"
    learned = "learned"


class SuperiorSkillScope(str, enum.Enum):
    global_core = "global_core"
    agent = "agent"


class LearningStatus(str, enum.Enum):
    quarantine = "quarantine"
    approved = "approved"
    rejected = "rejected"
    archived = "archived"


class CredentialProvider(str, enum.Enum):
    openai = "openai"
    anthropic = "anthropic"
    gemini = "gemini"
    tavily = "tavily"
    serper = "serper"


class PublicationProfile(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "publication_profiles"
    name: Mapped[str] = mapped_column(String(200))
    brand_name: Mapped[str] = mapped_column(String(200))
    website_url: Mapped[str | None] = mapped_column(Text)
    segment: Mapped[str] = mapped_column(String(160), index=True)
    brand_description: Mapped[str] = mapped_column(Text)
    mission: Mapped[str | None] = mapped_column(Text)
    value_proposition: Mapped[str | None] = mapped_column(Text)
    audience_description: Mapped[str] = mapped_column(Text)
    tone_of_voice: Mapped[str] = mapped_column(Text)
    research_summary: Mapped[str | None] = mapped_column(Text)
    profile_data: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(
        String(30), default="active", server_default="active", index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    projects = relationship("Project", back_populates="publication_profile")
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')",
            name="publication_profiles_status_valid",
        ),
        CheckConstraint(
            "version >= 1",
            name="publication_profiles_version_positive",
        ),
    )


class Project(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "projects"
    name: Mapped[str] = mapped_column(String(200))
    creation_idempotency_key: Mapped[str | None] = mapped_column(
        String(160), unique=True
    )
    topic: Mapped[str] = mapped_column(Text)
    search_intent: Mapped[str] = mapped_column(String(50))
    audience: Mapped[str] = mapped_column(Text)
    language: Mapped[str] = mapped_column(String(10), default="pt-BR")
    niche: Mapped[str | None] = mapped_column(String(120))
    publication_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("publication_profiles.id", ondelete="SET NULL"),
        index=True,
    )
    briefing: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType), default=ContentType.article, server_default="article"
    )
    editorial_pipeline_version: Mapped[EditorialPipelineVersion] = mapped_column(
        Enum(EditorialPipelineVersion),
        default=EditorialPipelineVersion.v2,
        server_default="v2",
        index=True,
    )
    event_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus), default=ProjectStatus.draft, index=True
    )
    current_stage: Mapped[str] = mapped_column(String(50), default="planner")
    research_cycles: Mapped[int] = mapped_column(Integer, default=0)
    editor_cycles: Mapped[int] = mapped_column(Integer, default=0)
    plans = relationship(
        "ResearchPlan", back_populates="project", cascade="all, delete-orphan"
    )
    articles = relationship(
        "Article", back_populates="project", cascade="all, delete-orphan"
    )
    pipeline_runs = relationship(
        "PipelineRun", back_populates="project", cascade="all, delete-orphan"
    )
    publication_profile = relationship(
        "PublicationProfile", back_populates="projects"
    )
    content_knowledge_contracts = relationship(
        "ContentKnowledgeContractRecord",
        back_populates="project",
        cascade="all, delete-orphan",
    )


class PipelineRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pipeline_runs"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[PipelineRunStatus] = mapped_column(
        Enum(PipelineRunStatus), default=PipelineRunStatus.queued, index=True
    )
    trigger_type: Mapped[TriggerType] = mapped_column(
        Enum(TriggerType), default=TriggerType.api
    )
    current_stage: Mapped[str] = mapped_column(String(50), default="planner")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_successful_checkpoint: Mapped[str | None] = mapped_column(String(50))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    lock_version: Mapped[int] = mapped_column(Integer, default=0)
    checkpoint_sequence: Mapped[int] = mapped_column(Integer, default=0)
    handoff_sequence: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(160))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    dispatch_status: Mapped[PipelineDispatchStatus | None] = mapped_column(
        Enum(PipelineDispatchStatus)
    )
    dispatch_claimed_by: Mapped[str | None] = mapped_column(String(160))
    dispatch_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatch_attempt: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_dispatch_error: Mapped[str | None] = mapped_column(Text)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    celery_task_id: Mapped[str | None] = mapped_column(String(160))
    billed_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    billed_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimated_external_cost_usd: Mapped[float] = mapped_column(
        Numeric(12, 6), default=0, server_default="0"
    )
    project = relationship("Project", back_populates="pipeline_runs")
    checkpoints = relationship(
        "PipelineCheckpoint", back_populates="pipeline_run", cascade="all, delete-orphan"
    )
    execution_manifest = relationship(
        "ExecutionManifest",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
        uselist=False,
    )
    quality_evaluation = relationship(
        "QualityEvaluation",
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
        uselist=False,
    )
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_pipeline_run_idempotency"),
        CheckConstraint("attempt >= 1", name="pipeline_run_attempt_positive"),
        CheckConstraint("dispatch_attempt >= 0", name="pipeline_run_dispatch_attempt_nonnegative"),
        CheckConstraint(
            "billed_prompt_tokens >= 0 AND billed_completion_tokens >= 0",
            name="pipeline_run_billed_tokens_nonnegative",
        ),
        CheckConstraint(
            "estimated_external_cost_usd >= 0",
            name="pipeline_run_external_cost_nonnegative",
        ),
        CheckConstraint(
            "dispatch_status IS NULL OR "
            "(dispatch_token IS NOT NULL AND dispatch_claimed_at IS NOT NULL)",
            name="pipeline_run_dispatch_identity_present",
        ),
        Index(
            "ix_pipeline_runs_dispatch_eligibility",
            "status",
            "next_retry_at",
            "dispatch_not_before",
            "dispatch_expires_at",
            postgresql_where=sa_text("status IN ('queued', 'waiting_retry')"),
        ),
    )


class PipelineCheckpoint(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pipeline_checkpoints"
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(50))
    sequence: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    contract_version: Mapped[str] = mapped_column(String(30), default="1.0")
    next_stage: Mapped[str] = mapped_column(String(50))
    state_json: Mapped[dict] = mapped_column(JSONB)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    resumable: Mapped[bool] = mapped_column(Boolean, default=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    idempotency_key: Mapped[str] = mapped_column(String(200))
    pipeline_run = relationship("PipelineRun", back_populates="checkpoints")
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "idempotency_key", name="uq_checkpoint_idempotency"),
        UniqueConstraint("pipeline_run_id", "sequence", name="uq_checkpoint_sequence"),
        CheckConstraint("sequence >= 1", name="checkpoint_sequence_positive"),
    )


class ExecutionManifest(UUIDMixin, Base):
    __tablename__ = "execution_manifests"
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )
    format_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    manifest_json: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    pipeline_run = relationship("PipelineRun", back_populates="execution_manifest")
    __table_args__ = (
        CheckConstraint(
            "format_version >= 1", name="execution_manifest_format_version_positive"
        ),
    )


class QualityEvaluation(UUIDMixin, Base):
    __tablename__ = "quality_evaluations"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    article_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_versions.id", ondelete="CASCADE"), unique=True, index=True
    )
    rubric_version: Mapped[str] = mapped_column(String(80))
    rubric_checksum: Mapped[str] = mapped_column(String(64))
    evaluator_kind: Mapped[str] = mapped_column(
        String(40), default="deterministic", server_default="deterministic"
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    overall_score: Mapped[float] = mapped_column(Float)
    thresholds_json: Mapped[dict] = mapped_column(JSONB)
    result_json: Mapped[dict] = mapped_column(JSONB)
    result_checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    pipeline_run = relationship("PipelineRun", back_populates="quality_evaluation")
    __table_args__ = (
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 1",
            name="quality_evaluation_score_range",
        ),
        CheckConstraint(
            "status IN ('passed', 'needs_improvement', 'blocked')",
            name="quality_evaluation_status_valid",
        ),
    )


class PipelineStateTransition(UUIDMixin, Base):
    __tablename__ = "pipeline_state_transitions"
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    stage: Mapped[str] = mapped_column(String(50))
    origin: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class ResearchPlan(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "research_plans"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    rationale: Mapped[str] = mapped_column(Text)
    semantic_keywords: Mapped[list] = mapped_column(JSONB, default=list)
    competitor_angles: Mapped[list] = mapped_column(JSONB, default=list)
    content_gaps: Mapped[list] = mapped_column(JSONB, default=list)
    seo_brief: Mapped[dict] = mapped_column(JSONB, default=dict)
    editorial_blueprint: Mapped[dict] = mapped_column(JSONB, default=dict)
    hierarchy_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    project = relationship("Project", back_populates="plans")
    questions = relationship(
        "ResearchQuestion", back_populates="plan", cascade="all, delete-orphan"
    )
    __table_args__ = (
        UniqueConstraint("project_id", "version"),
        UniqueConstraint(
            "pipeline_run_id", "idempotency_key", name="uq_research_plan_idempotency"
        ),
    )


class ContentKnowledgeContractRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "content_knowledge_contracts"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    contract_version: Mapped[str] = mapped_column(
        String(30), default="editorial-v3", server_default="editorial-v3"
    )
    content_type: Mapped[str] = mapped_column(String(60), index=True)
    topic: Mapped[str] = mapped_column(Text)
    reader_start_state: Mapped[str] = mapped_column(Text)
    reader_final_state: Mapped[str] = mapped_column(Text)
    article_promise: Mapped[str] = mapped_column(Text)
    scope_limit: Mapped[str] = mapped_column(Text)
    contract_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(30), default="draft", server_default="draft", index=True
    )
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    producer: Mapped[str] = mapped_column(
        String(100), default="deterministic", server_default="deterministic"
    )
    project = relationship("Project", back_populates="content_knowledge_contracts")
    nodes = relationship(
        "KnowledgeNodeRecord", back_populates="contract", cascade="all, delete-orphan"
    )
    edges = relationship(
        "KnowledgeEdgeRecord", back_populates="contract", cascade="all, delete-orphan"
    )
    gaps = relationship(
        "KnowledgeGapRecord", back_populates="contract", cascade="all, delete-orphan"
    )
    source_assessments = relationship(
        "ResearchSourceAssessmentRecord",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    __table_args__ = (
        UniqueConstraint(
            "project_id", "version", name="uq_content_knowledge_contract_project_version"
        ),
        UniqueConstraint(
            "project_id", "checksum", name="uq_content_knowledge_contract_project_checksum"
        ),
        UniqueConstraint(
            "pipeline_run_id", "checksum", name="uq_content_knowledge_contract_run_checksum"
        ),
        CheckConstraint(
            "status IN ('draft', 'validated', 'active', 'superseded', 'blocked')",
            name="content_knowledge_contract_status_valid",
        ),
        CheckConstraint(
            "version >= 1", name="content_knowledge_contract_version_positive"
        ),
    )


class KnowledgeNodeRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_nodes"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer)
    node_type: Mapped[str] = mapped_column(String(60), index=True)
    title_function: Mapped[str] = mapped_column(Text)
    editorial_goal: Mapped[str] = mapped_column(Text)
    reader_state_before: Mapped[str] = mapped_column(Text)
    reader_state_after: Mapped[str] = mapped_column(Text)
    central_question: Mapped[str] = mapped_column(Text)
    depends_on: Mapped[list] = mapped_column(JSONB, default=list)
    required_knowledge: Mapped[list] = mapped_column(JSONB, default=list)
    required_decisions: Mapped[list] = mapped_column(JSONB, default=list)
    required_evidence_roles: Mapped[list] = mapped_column(JSONB, default=list)
    completion_criteria: Mapped[list] = mapped_column(JSONB, default=list)
    branches: Mapped[list] = mapped_column(JSONB, default=list)
    convergence_node_key: Mapped[str | None] = mapped_column(String(100))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    contract = relationship("ContentKnowledgeContractRecord", back_populates="nodes")
    __table_args__ = (
        UniqueConstraint("contract_id", "node_key", name="uq_knowledge_node_contract_key"),
        UniqueConstraint("contract_id", "sequence", name="uq_knowledge_node_contract_sequence"),
        CheckConstraint("sequence >= 1", name="knowledge_node_sequence_positive"),
    )


class KnowledgeEdgeRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_edges"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    from_node_key: Mapped[str] = mapped_column(String(100))
    to_node_key: Mapped[str] = mapped_column(String(100))
    relation: Mapped[str] = mapped_column(String(50))
    rationale: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    contract = relationship("ContentKnowledgeContractRecord", back_populates="edges")
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "from_node_key",
            "to_node_key",
            "relation",
            name="uq_knowledge_edge_contract_path",
        ),
        CheckConstraint(
            "from_node_key <> to_node_key", name="knowledge_edge_not_self_referencing"
        ),
    )


class KnowledgeGapRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_gaps"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(100), index=True)
    gap_type: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text)
    essential: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    status: Mapped[str] = mapped_column(
        String(40), default="open", server_default="open", index=True
    )
    original_problem: Mapped[str] = mapped_column(Text, default="", server_default="")
    reframed_problem: Mapped[str] = mapped_column(Text, default="", server_default="")
    resolution_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    contract = relationship("ContentKnowledgeContractRecord", back_populates="gaps")
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'researching', 'resolved', 'resolved_conditionally', 'disputed', 'blocked')",
            name="knowledge_gap_status_valid",
        ),
    )


class ResearchSourceAssessmentRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "research_source_assessments"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL")
    )
    source_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )
    canonical_url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), index=True)
    policy_version: Mapped[str] = mapped_column(
        String(50),
        default="research-source-policy.v1",
        server_default="research-source-policy.v1",
    )
    ownership_type: Mapped[str] = mapped_column(String(50))
    page_type: Mapped[str] = mapped_column(String(60))
    source_role: Mapped[str] = mapped_column(String(60), index=True)
    usage_policy: Mapped[str] = mapped_column(String(40), index=True)
    priority_score: Mapped[float] = mapped_column(Float)
    eligible_for_primary_evidence: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    eligible_for_corroborating_evidence: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    eligible_for_external_reference: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    counts_toward_independent_source_diversity: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    requires_independent_corroboration: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    minimum_independent_corroborators: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    absolute_claim_support_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    allowed_evidence_roles: Mapped[list] = mapped_column(JSONB, default=list)
    reason_codes: Mapped[list] = mapped_column(JSONB, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    signals_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    contract = relationship(
        "ContentKnowledgeContractRecord", back_populates="source_assessments"
    )
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "url_hash",
            "policy_version",
            name="uq_research_source_assessment_contract_url_policy",
        ),
        CheckConstraint(
            "priority_score >= 0 AND priority_score <= 1",
            name="research_source_assessment_priority_range",
        ),
        CheckConstraint(
            "minimum_independent_corroborators >= 0 AND minimum_independent_corroborators <= 5",
            name="research_source_assessment_corroborator_range",
        ),
        CheckConstraint(
            "usage_policy IN ('authoritative_evidence', 'corroborating_evidence', 'discovery_only', 'comparison_only', 'rejected')",
            name="research_source_assessment_usage_valid",
        ),
    )


class V3SourceDocumentRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_source_documents"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    canonical_url: Mapped[str] = mapped_column(Text)
    url_hash: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(60), index=True)
    source_role: Mapped[str] = mapped_column(String(60), index=True)
    usage_policy: Mapped[str] = mapped_column(String(40), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    document_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    assessment_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(
        String(30), default="accepted", server_default="accepted", index=True
    )
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id",
            "url_hash",
            "content_hash",
            name="uq_v3_source_document_run_url_content",
        ),
        CheckConstraint(
            "status IN ('accepted', 'comparison_only', 'discovery_only', 'rejected', 'unavailable')",
            name="v3_source_document_status_valid",
        ),
        CheckConstraint(
            "url_hash ~ '^[0-9a-f]{64}$' AND content_hash ~ '^[0-9a-f]{64}$'",
            name="v3_source_document_hashes_sha256",
        ),
    )


class V3KnowledgeClaimRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_knowledge_claims"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("v3_source_documents.id", ondelete="RESTRICT"), index=True
    )
    fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact_ledger.id", ondelete="SET NULL"), index=True
    )
    canonical_claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=False
    )
    claim_key: Mapped[str] = mapped_column(String(120))
    support_group: Mapped[str] = mapped_column(String(120), index=True)
    knowledge_node_key: Mapped[str] = mapped_column(String(100), index=True)
    evidence_role: Mapped[str] = mapped_column(String(50), index=True)
    claim_text: Mapped[str] = mapped_column(Text)
    exact_quote: Mapped[str] = mapped_column(Text)
    source_locator: Mapped[str] = mapped_column(String(500))
    method_ids: Mapped[list] = mapped_column(JSONB, default=list)
    conditions: Mapped[list] = mapped_column(JSONB, default=list)
    applicability: Mapped[list] = mapped_column(JSONB, default=list)
    limitations: Mapped[list] = mapped_column(JSONB, default=list)
    conclusion_status: Mapped[str] = mapped_column(String(40), index=True)
    confidence_score: Mapped[float] = mapped_column(Float)
    critical: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    conflict_group: Mapped[str | None] = mapped_column(String(160), index=True)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", index=True)
    validation_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "claim_key", name="uq_v3_knowledge_claim_run_key"
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="v3_knowledge_claim_confidence_range",
        ),
        CheckConstraint(
            "conclusion_status IN ('confirmed', 'well_supported', 'conditional', 'disputed', 'insufficient_evidence')",
            name="v3_knowledge_claim_conclusion_valid",
        ),
    )


class V3MethodDossierRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_method_dossiers"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    method_key: Mapped[str] = mapped_column(String(100))
    dossier_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="validated", server_default="validated", index=True
    )
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "method_key", name="uq_v3_method_dossier_run_key"
        ),
        CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name="v3_method_dossier_checksum_sha256"
        ),
    )


class V3SectionDossierRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_section_dossiers"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    section_key: Mapped[str] = mapped_column(String(100))
    dossier_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="validated", server_default="validated", index=True
    )
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "section_key", name="uq_v3_section_dossier_run_key"
        ),
        CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name="v3_section_dossier_checksum_sha256"
        ),
    )


class V3DecisionMatrixRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_decision_matrices"
    contract_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    matrix_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(
        String(30), default="validated", server_default="validated", index=True
    )
    __table_args__ = (
        CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name="v3_decision_matrix_checksum_sha256"
        ),
    )


class V3StageReviewRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_stage_reviews"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(80), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(30), index=True)
    payload_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "stage", "attempt", name="uq_v3_stage_review_run_stage_attempt"
        ),
        CheckConstraint("attempt >= 1", name="v3_stage_review_attempt_positive"),
        CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name="v3_stage_review_checksum_sha256"
        ),
    )


class EditorialIntelligenceSnapshot(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "editorial_intelligence_snapshots"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("content_knowledge_contracts.id", ondelete="SET NULL"), index=True
    )
    revision: Mapped[int] = mapped_column(
        Integer, default=1, server_default="1", index=True
    )
    stage: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    intelligence_version: Mapped[str] = mapped_column(
        String(60), default="editorial-intelligence-v1", server_default="editorial-intelligence-v1"
    )
    state_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    validation_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    validated_artifact_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    article_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_versions.id", ondelete="SET NULL"), index=True
    )
    draft_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "stage", "checksum", name="uq_editorial_intelligence_run_stage_checksum"
        ),
        CheckConstraint("revision >= 1", name="editorial_intelligence_revision_positive"),
        CheckConstraint(
            "status IN ('planned', 'evidence_attached', 'writer_ready', 'draft_pending_validation', 'draft_validated', 'blocked')",
            name="editorial_intelligence_status_valid",
        ),
        CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name="editorial_intelligence_checksum_sha256"
        ),
        CheckConstraint(
            "draft_revision >= 0",
            name="editorial_intelligence_draft_revision_nonnegative",
        ),
        CheckConstraint(
            "validated_artifact_hash IS NULL OR validated_artifact_hash ~ '^[0-9a-f]{64}$'",
            name="editorial_intelligence_artifact_hash_sha256",
        ),
    )


class V3ProceduralQualityRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "v3_procedural_quality_evaluations"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    article_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_versions.id", ondelete="SET NULL"), index=True
    )
    rubric_version: Mapped[str] = mapped_column(
        String(80), default="quality-rubric.procedural-guide.v3",
        server_default="quality-rubric.procedural-guide.v3",
    )
    status: Mapped[str] = mapped_column(String(30), index=True)
    overall_score: Mapped[float] = mapped_column(Float)
    result_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    checksum: Mapped[str] = mapped_column(String(64), index=True)
    __table_args__ = (
        CheckConstraint(
            "overall_score >= 0 AND overall_score <= 1",
            name="v3_procedural_quality_score_range",
        ),
        CheckConstraint(
            "checksum ~ '^[0-9a-f]{64}$'", name="v3_procedural_quality_checksum_sha256"
        ),
    )


class ResearchQuestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "research_questions"
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_plans.id", ondelete="CASCADE"), index=True
    )
    question: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer)
    importance: Mapped[str] = mapped_column(
        String(20), default="core", server_default="core", index=True
    )
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="")
    node_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    expected_source_types: Mapped[list] = mapped_column(JSONB, default=list)
    semantic_terms: Mapped[list] = mapped_column(JSONB, default=list)
    search_queries: Mapped[dict] = mapped_column(JSONB, default=dict)
    coverage_status: Mapped[str] = mapped_column(String(30), default="uncovered")
    plan = relationship("ResearchPlan", back_populates="questions")
    __table_args__ = (
        CheckConstraint(
            "importance IN ('core', 'supporting', 'optional')",
            name="research_question_importance_valid",
        ),
        CheckConstraint(
            "priority >= 1 AND priority <= 20",
            name="research_question_priority_range",
        ),
    )


class Source(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sources"
    canonical_url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot_text: Mapped[str] = mapped_column(Text)
    reliability_score: Mapped[float] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        CheckConstraint(
            "reliability_score >= 0 AND reliability_score <= 1",
            name="source_reliability_range",
        ),
    )


class SourceSnapshot(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "source_snapshots"
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot_text: Mapped[str] = mapped_column(Text)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    publisher: Mapped[str | None] = mapped_column(String(255))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    canonical_url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(50))
    reliability_score: Mapped[float] = mapped_column(Float)
    extraction_method: Mapped[str] = mapped_column(String(40))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    reused_from_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="SET NULL")
    )
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "source_id", "content_hash", name="uq_source_snapshot_run"
        ),
        CheckConstraint(
            "reliability_score >= 0 AND reliability_score <= 1",
            name="source_snapshot_reliability_range",
        ),
    )


class FactLedger(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "fact_ledger"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    research_question_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_questions.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    source_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_snapshots.id", ondelete="RESTRICT"), index=True
    )
    claim_text: Mapped[str] = mapped_column(Text)
    exact_quote: Mapped[str | None] = mapped_column(Text)
    source_locator: Mapped[str] = mapped_column(
        String(255), comment="Heading, page or text offsets"
    )
    extraction_method: Mapped[str] = mapped_column(String(40), default="llm")
    confidence_score: Mapped[float] = mapped_column(Float)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    approved_by_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    conflict_group: Mapped[str | None] = mapped_column(String(100), index=True)
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact_ledger.id")
    )
    source = relationship("Source")
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "source_id", "claim_text", name="uq_fact_per_pipeline_run"
        ),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="fact_confidence_range",
        ),
    )


class Article(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "articles"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True
    )
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType), default=ContentType.article, server_default="article"
    )
    active_pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    final_markdown: Mapped[str | None] = mapped_column(Text)
    final_html: Mapped[str | None] = mapped_column(Text)
    seo_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    content_fingerprint: Mapped[str | None] = mapped_column(Text)
    content_embedding: Mapped[list[float] | None] = mapped_column(Vector())
    content_embedding_provider: Mapped[str | None] = mapped_column(String(30))
    content_embedding_model: Mapped[str | None] = mapped_column(String(100))
    content_embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    project = relationship("Project", back_populates="articles")
    versions = relationship(
        "ArticleVersion", back_populates="article", cascade="all, delete-orphan"
    )


class ArticleVersion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "article_versions"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    outline: Mapped[list] = mapped_column(JSONB, default=list)
    editorial_status: Mapped[str] = mapped_column(String(30), default="draft")
    change_reason: Mapped[str | None] = mapped_column(Text)
    final_markdown: Mapped[str | None] = mapped_column(Text)
    final_html: Mapped[str | None] = mapped_column(Text)
    seo_metadata: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    content_checksum: Mapped[str] = mapped_column(String(64))
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    article = relationship("Article", back_populates="versions")
    blocks = relationship(
        "ArticleBlock", back_populates="article_version", cascade="all, delete-orphan"
    )
    __table_args__ = (
        UniqueConstraint("article_id", "version"),
        UniqueConstraint("article_id", "idempotency_key", name="uq_article_version_idempotency"),
        CheckConstraint(
            "content_checksum ~ '^[0-9a-f]{64}$'",
            name="article_version_content_checksum_sha256",
        ),
        CheckConstraint(
            "sealed_at IS NULL OR editorial_status = 'human_approved'",
            name="article_version_seal_status_valid",
        ),
    )


class HumanEditorialReview(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "human_editorial_reviews"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    article_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_versions.id", ondelete="CASCADE"), unique=True, index=True
    )
    reviewer: Mapped[str | None] = mapped_column(String(160))
    decision: Mapped[str] = mapped_column(
        String(30), default="pending", server_default="pending", index=True
    )
    observation: Mapped[str | None] = mapped_column(Text)
    review_package_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    review_package_checksum: Mapped[str] = mapped_column(String(64))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_idempotency_key: Mapped[str | None] = mapped_column(
        String(160), unique=True
    )
    revision_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    __table_args__ = (
        CheckConstraint(
            "decision IN ('pending', 'approved', 'rejected', 'revision_requested')",
            name="human_editorial_review_decision_valid",
        ),
        CheckConstraint(
            "review_package_checksum ~ '^[0-9a-f]{64}$'",
            name="human_editorial_review_package_checksum_sha256",
        ),
    )


class ArticleBlock(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "article_blocks"
    article_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_versions.id", ondelete="CASCADE"), index=True
    )
    parent_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_blocks.id")
    )
    logical_block_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    replaces_block_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="SET NULL"), index=True
    )
    revision_reason: Mapped[str | None] = mapped_column(Text)
    block_type: Mapped[str] = mapped_column(String(20))
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    structured_payload: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=sa_text("'{}'::jsonb")
    )
    supported: Mapped[bool] = mapped_column(Boolean, default=False)
    article_version = relationship("ArticleVersion", back_populates="blocks")
    sentences = relationship(
        "SentenceClaim", back_populates="block", cascade="all, delete-orphan"
    )
    __table_args__ = (UniqueConstraint("article_version_id", "position"),)


class SentenceClaim(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "sentence_claims"
    block_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_blocks.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    logical_sentence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    is_factual: Mapped[bool] = mapped_column(Boolean, default=True)
    support_status: Mapped[str] = mapped_column(
        String(30), default="pending", index=True
    )
    fidelity_status: Mapped[str] = mapped_column(String(30), default="pending")
    block = relationship("ArticleBlock", back_populates="sentences")
    evidence = relationship(
        "ClaimEvidence", back_populates="sentence", cascade="all, delete-orphan"
    )
    __table_args__ = (
        UniqueConstraint(
            "block_id",
            "logical_sentence_id",
            name="uq_sentence_claim_block_logical",
        ),
    )


class ClaimEvidence(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "claim_evidence"
    sentence_claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sentence_claims.id", ondelete="CASCADE"), index=True
    )
    fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact_ledger.id", ondelete="RESTRICT"), index=True
    )
    entailment_score: Mapped[float] = mapped_column(Float)
    reviewer_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    sentence = relationship("SentenceClaim", back_populates="evidence")
    fact = relationship("FactLedger")
    __table_args__ = (
        UniqueConstraint("sentence_claim_id", "fact_id"),
        CheckConstraint(
            "entailment_score >= 0 AND entailment_score <= 1",
            name="evidence_entailment_range",
        ),
    )


class Skill(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "skills"
    skill_id: Mapped[str] = mapped_column(String(160), unique=True)
    kind: Mapped[SkillKind] = mapped_column(Enum(SkillKind), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    applies_to_agents: Mapped[list] = mapped_column(JSONB, default=list)
    niche: Mapped[str | None] = mapped_column(String(120), index=True)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    lifecycle_status: Mapped[str] = mapped_column(
        String(30), default="active", server_default="active", index=True
    )
    auto_inject: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    stable: Mapped[bool] = mapped_column(Boolean, default=False)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_version: Mapped[str] = mapped_column(String(30))
    versions = relationship(
        "SkillVersion", back_populates="skill", cascade="all, delete-orphan"
    )
    lifecycle_events = relationship(
        "SkillLifecycleEvent", back_populates="skill", cascade="all, delete-orphan"
    )
    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('candidate', 'corroborated', 'human_approved', "
            "'stable', 'active', 'disabled', 'rejected')",
            name="skill_lifecycle_status_valid",
        ),
        Index(
            "uq_skills_project_fingerprint",
            "project_id",
            "fingerprint",
            unique=True,
            postgresql_where=sa_text("fingerprint IS NOT NULL"),
        ),
    )


class SkillVersion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "skill_versions"
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text)
    definition: Mapped[dict] = mapped_column(JSONB)
    origin_article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id")
    )
    confidence_score: Mapped[float] = mapped_column(Float, default=0)
    validation_count: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by_human: Mapped[bool] = mapped_column(Boolean, default=False)
    skill = relationship("Skill", back_populates="versions")
    validations = relationship(
        "SkillValidation", back_populates="skill_version", cascade="all, delete-orphan"
    )
    __table_args__ = (UniqueConstraint("skill_id", "version"),)


class SkillValidation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "skill_validations"
    skill_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), index=True
    )
    article_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_versions.id", ondelete="CASCADE"), index=True
    )
    evidence_source: Mapped[str] = mapped_column(
        String(50), default="pipeline_outcome"
    )
    editorial_rework_count: Mapped[int] = mapped_column(Integer, default=0)
    rubric_score: Mapped[float] = mapped_column(Float)
    factual_regression: Mapped[bool] = mapped_column(Boolean, default=False)
    corroborating: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    skill_version = relationship("SkillVersion", back_populates="validations")
    __table_args__ = (
        UniqueConstraint(
            "skill_version_id",
            "pipeline_run_id",
            name="uq_skill_validation_run",
        ),
        CheckConstraint(
            "editorial_rework_count >= 0",
            name="skill_validation_rework_nonnegative",
        ),
        CheckConstraint(
            "rubric_score >= 0 AND rubric_score <= 1",
            name="skill_validation_rubric_range",
        ),
    )


class SkillLifecycleEvent(UUIDMixin, Base):
    __tablename__ = "skill_lifecycle_events"
    skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("skills.id", ondelete="CASCADE"), index=True
    )
    skill_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("skill_versions.id", ondelete="SET NULL"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(50), index=True)
    actor: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    skill = relationship("Skill", back_populates="lifecycle_events")


class SuperiorSkill(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "superior_skills"
    skill_id: Mapped[str] = mapped_column(String(160), unique=True)
    scope: Mapped[SuperiorSkillScope] = mapped_column(Enum(SuperiorSkillScope))
    agent_role: Mapped[str | None] = mapped_column(String(50), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    current_version: Mapped[str] = mapped_column(String(30))
    versions = relationship(
        "SuperiorSkillVersion",
        back_populates="superior_skill",
        cascade="all, delete-orphan",
    )


class SuperiorSkillVersion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "superior_skill_versions"
    superior_skill_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("superior_skills.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(30))
    definition: Mapped[dict] = mapped_column(JSONB)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    reviewed_by_human: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(120), default="system")
    superior_skill = relationship("SuperiorSkill", back_populates="versions")
    __table_args__ = (UniqueConstraint("superior_skill_id", "version"),)


class AgentMemory(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_memories"
    agent_role: Mapped[str] = mapped_column(String(50), index=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    origin_pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    niche: Mapped[str | None] = mapped_column(String(120), index=True)
    memory_kind: Mapped[str] = mapped_column(String(50), index=True)
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50))
    source_id: Mapped[str | None] = mapped_column(String(160))
    confidence_score: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[LearningStatus] = mapped_column(
        Enum(LearningStatus), default=LearningStatus.quarantine, index=True
    )
    persona_version: Mapped[str | None] = mapped_column(String(30))
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_provider: Mapped[str | None] = mapped_column(String(30))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="agent_memory_confidence_range",
        ),
    )


class AgentHandoff(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_handoffs"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(300))
    sequence: Mapped[int] = mapped_column(Integer)
    producer_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    from_role: Mapped[str] = mapped_column(String(50), index=True)
    to_role: Mapped[str] = mapped_column(String(50), index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    fact_ids: Mapped[list] = mapped_column(JSONB, default=list)
    confidence_score: Mapped[float] = mapped_column(Float, default=1)
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "idempotency_key", name="uq_handoff_idempotency"),
        UniqueConstraint("pipeline_run_id", "sequence", name="uq_handoff_sequence"),
        CheckConstraint("sequence >= 1", name="handoff_sequence_positive"),
        CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="agent_handoff_confidence_range",
        ),
    )


class StyleSource(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "style_sources"
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    origin_pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    canonical_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(String(255))
    domain: Mapped[str] = mapped_column(String(255), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    excerpts: Mapped[list] = mapped_column(JSONB, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[LearningStatus] = mapped_column(
        Enum(LearningStatus), default=LearningStatus.quarantine, index=True
    )
    __table_args__ = (
        UniqueConstraint("project_id", "canonical_url", "content_hash"),
    )


class StylePattern(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "style_patterns"
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    origin_pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="SET NULL"), index=True
    )
    target_agent_role: Mapped[str] = mapped_column(String(50), default="writer")
    niche: Mapped[str | None] = mapped_column(String(120), index=True)
    pattern_type: Mapped[str] = mapped_column(String(80), index=True)
    description: Mapped[str] = mapped_column(Text)
    source_ids: Mapped[list] = mapped_column(JSONB, default=list)
    independent_domain_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[LearningStatus] = mapped_column(
        Enum(LearningStatus), default=LearningStatus.quarantine, index=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding: Mapped[list[float] | None] = mapped_column(Vector())
    embedding_provider: Mapped[str | None] = mapped_column(String(30))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint(
            "independent_domain_count >= 0 AND validation_count >= 0",
            name="style_pattern_counts_nonnegative",
        ),
    )


class EmbeddingRoute(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "embedding_routes"
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    dimensions: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    __table_args__ = (
        Index(
            "uq_embedding_routes_single_active",
            "active",
            unique=True,
            postgresql_where=sa_text("active"),
        ),
    )


class AgentRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    agent_role: Mapped[str] = mapped_column(String(50), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.pending
    )
    input_json: Mapped[dict] = mapped_column(JSONB)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    decision: Mapped[GateDecision | None] = mapped_column(Enum(GateDecision))
    feedback: Mapped[dict | None] = mapped_column(JSONB)
    provider: Mapped[str | None] = mapped_column(String(30))
    model: Mapped[str | None] = mapped_column(String(100))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_category: Mapped[str | None] = mapped_column(String(40))
    http_status: Mapped[int | None] = mapped_column(Integer)
    retryable: Mapped[bool | None] = mapped_column(Boolean)
    correlation_id: Mapped[str | None] = mapped_column(String(36))
    recovered: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa_text("false")
    )
    recovery_code: Mapped[str | None] = mapped_column(String(100))
    recovered_by_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "idempotency_key", name="uq_agent_run_idempotency"),
    )


class ProviderAttempt(UUIDMixin, Base):
    __tablename__ = "provider_attempts"
    agent_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    target_kind: Mapped[str] = mapped_column(String(20))
    run_attempt: Mapped[int] = mapped_column(Integer, default=1, server_default=sa_text("1"))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), index=True)
    response_received: Mapped[bool] = mapped_column(Boolean, default=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_category: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "agent_run_id", "run_attempt", "target_kind", "attempt_number",
            name="uq_provider_attempt_agent_run_target_number",
        ),
        CheckConstraint(
            "target_kind IN ('primary', 'fallback')",
            name="provider_attempt_target_kind_valid",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'failed', 'invalid_output')",
            name="provider_attempt_status_valid",
        ),
        CheckConstraint(
            "run_attempt >= 1 AND attempt_number >= 1 AND prompt_tokens >= 0 "
            "AND completion_tokens >= 0 "
            "AND estimated_cost_usd >= 0 AND latency_ms >= 0",
            name="provider_attempt_values_nonnegative",
        ),
    )


class Credential(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "credentials"
    provider: Mapped[CredentialProvider] = mapped_column(
        Enum(CredentialProvider), unique=True
    )
    encrypted_value: Mapped[bytes] = mapped_column(LargeBinary)
    key_version: Mapped[int] = mapped_column(Integer, default=1)
    last_four: Mapped[str] = mapped_column(String(4))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelRoute(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "model_routes"
    agent_role: Mapped[str] = mapped_column(String(50), unique=True)
    primary_provider: Mapped[str] = mapped_column(String(30))
    primary_model: Mapped[str] = mapped_column(String(100))
    fallback_provider: Mapped[str | None] = mapped_column(String(30))
    fallback_model: Mapped[str | None] = mapped_column(String(100))
    parameters: Mapped[dict] = mapped_column(JSONB, default=dict)


class TechnicalErrorLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "technical_error_logs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    stage: Mapped[str] = mapped_column(String(50), index=True)
    severity: Mapped[str] = mapped_column(
        String(20), default="error", server_default="error", index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(100), index=True)
    error_category: Mapped[str | None] = mapped_column(String(40))
    exception_type: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    operation: Mapped[str | None] = mapped_column(String(30))
    sql_template: Mapped[str | None] = mapped_column(Text)
    traceback: Mapped[str | None] = mapped_column(Text)
    correlation_id: Mapped[str] = mapped_column(String(36), unique=True)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_text("false"))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default="{}")

    __table_args__ = (
        CheckConstraint(
            "severity IN ('warning', 'error', 'critical')",
            name="technical_error_logs_severity_valid",
        ),
    )


class PipelineEvent(UUIDMixin, Base):
    __tablename__ = "pipeline_events"
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    pipeline_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(50))
    stage: Mapped[str] = mapped_column(String(50))
    stage_occurrence_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    research_cycle: Mapped[int | None] = mapped_column(Integer)
    editor_cycle: Mapped[int | None] = mapped_column(Integer)
    run_attempt: Mapped[int | None] = mapped_column(Integer)
    stage_attempt: Mapped[int | None] = mapped_column(Integer)
    checkpoint_sequence: Mapped[int | None] = mapped_column(Integer)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    __table_args__ = (
        UniqueConstraint("project_id", "sequence"),
        UniqueConstraint("pipeline_run_id", "idempotency_key", name="uq_event_idempotency"),
        CheckConstraint(
            "research_cycle IS NULL OR research_cycle >= 0",
            name="event_research_cycle_nonnegative",
        ),
        CheckConstraint(
            "editor_cycle IS NULL OR editor_cycle >= 0",
            name="event_editor_cycle_nonnegative",
        ),
        CheckConstraint(
            "run_attempt IS NULL OR run_attempt >= 1",
            name="event_run_attempt_positive",
        ),
        CheckConstraint(
            "stage_attempt IS NULL OR stage_attempt >= 1",
            name="event_stage_attempt_positive",
        ),
        CheckConstraint(
            "checkpoint_sequence IS NULL OR checkpoint_sequence >= 1",
            name="event_checkpoint_sequence_positive",
        ),
        Index("ix_pipeline_events_run_order", "project_id", "pipeline_run_id", "sequence", "created_at"),
        Index(
            "ix_pipeline_events_stage_occurrence",
            "pipeline_run_id",
            "stage_occurrence_id",
            "sequence",
        ),
    )
