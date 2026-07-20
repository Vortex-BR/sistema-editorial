"""Universal editorial hierarchy contracts shared by V2 and V3.

The hierarchy is deliberately domain-agnostic.  It models how the reader's
understanding must progress before research or prose generation starts.  Topic
facts are discovered later; the contract only defines the functions that the
content must fulfil, their dependencies and their relative importance.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HierarchyStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditorialArchitectureType(str, Enum):
    procedural_decision_guide = "procedural_decision_guide"
    procedural_how_to = "procedural_how_to"
    explanatory_guide = "explanatory_guide"
    comparison = "comparison"
    troubleshooting = "troubleshooting"
    commercial_education = "commercial_education"


class UniversalNodeRole(str, Enum):
    foundation = "foundation"
    landscape = "landscape"
    requirements = "requirements"
    decision_criteria = "decision_criteria"
    preparation = "preparation"
    execution = "execution"
    progress_signal = "progress_signal"
    transition = "transition"
    outcome = "outcome"
    problems = "problems"
    self_diagnosis = "self_diagnosis"
    mechanism = "mechanism"
    implications = "implications"
    misconceptions = "misconceptions"
    options = "options"
    comparison = "comparison"
    recommendation_logic = "recommendation_logic"
    symptoms = "symptoms"
    causes = "causes"
    corrections = "corrections"
    verification = "verification"
    prevention = "prevention"
    problem_context = "problem_context"
    solution_fit = "solution_fit"
    objections = "objections"
    offer_bridge = "offer_bridge"
    external_references = "external_references"


class NodeApplicability(str, Enum):
    required = "required"
    conditional = "conditional"
    optional = "optional"


class NodeImportance(str, Enum):
    core = "core"
    supporting = "supporting"
    peripheral = "peripheral"


class EditorialHierarchyNode(HierarchyStrictModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    sequence: int = Field(ge=1, le=100)
    role: UniversalNodeRole
    title_function: str = Field(min_length=5, max_length=240)
    purpose: str = Field(min_length=10, max_length=1200)
    reader_state_before: str = Field(min_length=5, max_length=1200)
    reader_state_after: str = Field(min_length=5, max_length=1200)
    central_question: str = Field(min_length=8, max_length=600)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    applicability: NodeApplicability = NodeApplicability.required
    importance: NodeImportance = NodeImportance.core
    research_required: bool = True
    completion_criteria: list[str] = Field(min_length=1, max_length=30)
    minimum_depth_weight: float = Field(default=1.0, ge=0.1, le=5.0)
    maximum_depth_weight: float | None = Field(default=None, ge=0.1, le=5.0)
    allows_internal_link_only: bool = False
    metadata: dict = Field(default_factory=dict)


class EditorialHierarchyContract(HierarchyStrictModel):
    hierarchy_version: Literal["universal-editorial-hierarchy.v1"] = (
        "universal-editorial-hierarchy.v1"
    )
    architecture_type: EditorialArchitectureType
    topic: str = Field(min_length=3, max_length=500)
    reader_start_state: str = Field(min_length=5, max_length=2000)
    reader_final_state: str = Field(min_length=5, max_length=2000)
    nodes: list[EditorialHierarchyNode] = Field(min_length=3, max_length=30)
    closing_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self):
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Editorial hierarchy node IDs must be unique")
        sequences = [node.sequence for node in self.nodes]
        if sequences != list(range(1, len(self.nodes) + 1)):
            raise ValueError("Editorial hierarchy sequences must be contiguous")
        positions = {node.node_id: node.sequence for node in self.nodes}
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency not in positions:
                    raise ValueError(
                        f"Hierarchy node {node.node_id} references an unknown dependency"
                    )
                if positions[dependency] >= node.sequence:
                    raise ValueError(
                        f"Hierarchy dependency {dependency} must precede {node.node_id}"
                    )
        if self.closing_node_id not in positions:
            raise ValueError("Editorial hierarchy closing node must exist")
        if positions[self.closing_node_id] != len(self.nodes):
            raise ValueError("Editorial hierarchy closing node must be last")
        return self
