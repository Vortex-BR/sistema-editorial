from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class V3Stage(str, Enum):
    content_contract = "content_contract"
    knowledge_architect = "knowledge_architect"
    knowledge_gate = "knowledge_gate"
    intelligence_planner = "intelligence_planner"
    research_planner = "research_planner"
    source_discovery = "source_discovery"
    source_reader = "source_reader"
    source_coverage_gate = "source_coverage_gate"
    targeted_source_recovery = "targeted_source_recovery"
    knowledge_synthesizer = "knowledge_synthesizer"
    evidence_graph_builder = "evidence_graph_builder"
    intelligence_gate = "intelligence_gate"
    knowledge_completeness_gate = "knowledge_completeness_gate"
    writer = "writer"
    development_editor = "development_editor"
    fact_checker = "fact_checker"
    language_editor = "language_editor"
    external_reference_gate = "external_reference_gate"
    finalizer = "finalizer"
    quality_gate = "quality_gate"
    completed = "completed"
    blocked = "blocked"


class V3PipelineState(BaseModel):
    project_id: UUID
    pipeline_run_id: UUID | None = None
    stage: V3Stage = V3Stage.content_contract
    contract_id: UUID | None = None
    contract: dict | None = None
    contract_validation: dict | None = None
    intelligence_state: dict | None = None
    intelligence_validation: dict | None = None
    intelligence_revision: int = Field(default=0, ge=0)
    intelligence_recovery_round: int = Field(default=0, ge=0, le=2)
    intelligence_recovery_exhausted: bool = False
    intelligence_recovery_tasks: list[dict] = Field(default_factory=list)
    research_plan: dict | None = None
    raw_source_documents: list[dict] = Field(default_factory=list)
    source_task_map: dict[str, list[str]] = Field(default_factory=dict)
    source_documents: list[dict] = Field(default_factory=list)
    research_metrics: dict = Field(default_factory=dict)
    source_coverage_report: dict | None = None
    source_recovery_round: int = Field(default=0, ge=0, le=4)
    source_recovery_exhausted: bool = False
    knowledge_claims: list[dict] = Field(default_factory=list)
    knowledge_gaps: list[dict] = Field(default_factory=list)
    method_inventory: list[dict] = Field(default_factory=list)
    required_method_matches: dict[str, str] = Field(default_factory=dict)
    method_dossiers: list[dict] = Field(default_factory=list)
    section_dossiers: list[dict] = Field(default_factory=list)
    decision_matrix: dict | None = None
    external_references: dict[str, dict] = Field(default_factory=dict)
    completeness_report: dict | None = None
    draft: dict | None = None
    writer_sections: dict[str, dict] = Field(default_factory=dict)
    writer_completed_section_ids: list[str] = Field(default_factory=list)
    writer_section_repair_counts: dict[str, int] = Field(default_factory=dict)
    writer_progress: dict = Field(default_factory=dict)
    graph_transition_count: int = Field(default=0, ge=0)
    writer_diagnostics: dict | None = None
    context_budget_report: dict | None = None
    brief_compliance_report: dict | None = None
    writer_repair_count: int = Field(default=0, ge=0, le=2)
    development_review: dict | None = None
    fact_check: dict | None = None
    language_review: dict | None = None
    external_reference_report: dict | None = None
    final_package: dict | None = None
    article_version_id: UUID | None = None
    quality_evaluation: dict | None = None
    content_similarity_report: dict | None = None
    human_review_package_id: UUID | None = None
    blocking_reason: str | None = None
    blocking_code: str | None = None

    @model_validator(mode="after")
    def validate_resumable_writer_progress(self):
        completed = self.writer_completed_section_ids
        if len(completed) != len(set(completed)):
            raise ValueError("Writer completed section IDs must be unique")
        missing = [
            section_id
            for section_id in completed
            if section_id not in self.writer_sections
        ]
        if missing:
            raise ValueError(
                "Writer completed sections are missing persisted unit payloads: "
                + ", ".join(missing)
            )
        orphaned = sorted(set(self.writer_sections) - set(completed))
        if orphaned:
            raise ValueError(
                "Writer unit payloads are not marked as completed: "
                + ", ".join(orphaned)
            )
        if any(count < 0 for count in self.writer_section_repair_counts.values()):
            raise ValueError("Writer section repair counts cannot be negative")
        return self

    def resume_invariant_errors(
        self, *, project_id: UUID, pipeline_run_id: UUID
    ) -> list[str]:
        errors: list[str] = []
        if self.project_id != project_id:
            errors.append("checkpoint project_id does not match the current project")
        if self.pipeline_run_id != pipeline_run_id:
            errors.append("checkpoint pipeline_run_id does not match the current run")

        post_contract = {
            V3Stage.knowledge_architect,
            V3Stage.knowledge_gate,
            V3Stage.intelligence_planner,
            V3Stage.research_planner,
            V3Stage.source_discovery,
            V3Stage.source_reader,
            V3Stage.source_coverage_gate,
            V3Stage.targeted_source_recovery,
            V3Stage.knowledge_synthesizer,
            V3Stage.evidence_graph_builder,
            V3Stage.intelligence_gate,
            V3Stage.knowledge_completeness_gate,
            V3Stage.writer,
            V3Stage.development_editor,
            V3Stage.fact_checker,
            V3Stage.language_editor,
            V3Stage.external_reference_gate,
            V3Stage.finalizer,
            V3Stage.quality_gate,
            V3Stage.completed,
        }
        if self.stage in post_contract and not self.contract:
            errors.append(
                f"stage {self.stage.value} requires a persisted content contract"
            )

        writer_or_later = {
            V3Stage.writer,
            V3Stage.development_editor,
            V3Stage.fact_checker,
            V3Stage.language_editor,
            V3Stage.external_reference_gate,
            V3Stage.finalizer,
            V3Stage.quality_gate,
            V3Stage.completed,
        }
        if self.stage in writer_or_later:
            if (self.completeness_report or {}).get("status") != "passed":
                errors.append(
                    f"stage {self.stage.value} requires a passed completeness gate"
                )
            if not self.intelligence_state:
                errors.append(
                    f"stage {self.stage.value} requires canonical editorial intelligence"
                )

        after_writer = writer_or_later - {V3Stage.writer}
        if self.stage in after_writer and not self.draft:
            errors.append(f"stage {self.stage.value} requires a persisted draft")
        return errors
