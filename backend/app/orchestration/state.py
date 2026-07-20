from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field


class Stage(str, Enum):
    planner = "planner"
    researcher = "researcher"
    research_gatekeeper = "research_gatekeeper"
    writer = "writer"
    editor = "editor"
    finalizer = "finalizer"
    quality_gate = "quality_gate"
    skill_curator = "skill_curator"
    needs_review = "needs_review"
    completed = "completed"
    blocked = "blocked"


class PipelineState(BaseModel):
    project_id: UUID
    pipeline_run_id: UUID | None = None
    stage: Stage = Stage.planner
    research_cycle: int = 0
    editor_cycle: int = 0
    plan: dict | None = None
    facts: list[dict] = Field(default_factory=list)
    research_audit: dict | None = None
    similarity_report: dict | None = None
    draft: dict | None = None
    editorial_review: dict | None = None
    final_package: dict | None = None
    quality_evaluation: dict | None = None
    rewrite_block_ids: list[UUID] = Field(default_factory=list)
    blocking_reason: str | None = None
    blocking_code: str | None = None
