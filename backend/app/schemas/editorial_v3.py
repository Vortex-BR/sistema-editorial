"""Strict contracts for the Editorial Intelligence V3 pipeline.

V3 models knowledge before research and writing.  The contracts in this module
are intentionally independent from the V2 question/fact pipeline so the new
architecture can be developed behind a feature flag without changing existing
runs or checkpoints.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AliasChoices, AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator

from app.schemas.editorial_hierarchy import (
    NodeApplicability,
    NodeImportance,
    UniversalNodeRole,
)

Score = Annotated[float, Field(ge=0, le=1)]


class V3StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EditorialContentTypeV3(str, Enum):
    procedural_decision_guide = "procedural_decision_guide"
    procedural_how_to = "procedural_how_to"
    explanatory_guide = "explanatory_guide"
    comparison = "comparison"
    troubleshooting = "troubleshooting"
    commercial_education = "commercial_education"


class ApproachDimension(str, Enum):
    method = "method"
    environment = "environment"
    system = "system"
    strategy = "strategy"
    technique = "technique"
    material = "material"
    channel = "channel"
    format = "format"
    option = "option"
    other = "other"


def procedural_structural_minimum_words(
    approach_count: int,
    section_count: int = 13,
) -> int:
    """Return the minimum space needed by a complete procedural architecture."""

    return 850 + (max(0, approach_count) * 320) + (max(0, section_count) * 45)


class KnowledgeNodeKind(str, Enum):
    subject_foundation = "subject_foundation"
    process_requirements = "process_requirements"
    method_inventory = "method_inventory"
    method_comparison = "method_comparison"
    method_selection = "method_selection"
    method_execution = "method_execution"
    progress_confirmation = "progress_confirmation"
    transition_decision = "transition_decision"
    transition_execution = "transition_execution"
    post_transition_monitoring = "post_transition_monitoring"
    final_outcome_confirmation = "final_outcome_confirmation"
    troubleshooting = "troubleshooting"
    external_references = "external_references"
    explanation = "explanation"


class KnowledgeEdgeRelation(str, Enum):
    prerequisite = "prerequisite"
    sequence = "sequence"
    branches_to = "branches_to"
    converges_to = "converges_to"
    supports = "supports"


class EvidenceRole(str, Enum):
    definition = "definition"
    mechanism = "mechanism"
    prerequisite = "prerequisite"
    material = "material"
    environmental_condition = "environmental_condition"
    action = "action"
    sequence = "sequence"
    decision_criterion = "decision_criterion"
    success_signal = "success_signal"
    failure_signal = "failure_signal"
    common_error = "common_error"
    correction = "correction"
    risk = "risk"
    exception = "exception"
    limitation = "limitation"
    comparison = "comparison"
    transition = "transition"
    final_outcome = "final_outcome"
    external_reference = "external_reference"


class ConclusionStatus(str, Enum):
    confirmed = "confirmed"
    well_supported = "well_supported"
    conditional = "conditional"
    disputed = "disputed"
    insufficient_evidence = "insufficient_evidence"


class SourceRole(str, Enum):
    scientific_primary = "scientific_primary"
    scientific_review = "scientific_review"
    academic_repository = "academic_repository"
    institutional = "institutional"
    scientific_database = "scientific_database"
    technical_procedural = "technical_procedural"
    specialist_practical = "specialist_practical"
    independent_editorial = "independent_editorial"
    news_reporting = "news_reporting"
    encyclopedic_discovery = "encyclopedic_discovery"
    community_question_discovery = "community_question_discovery"
    ecommerce_blog = "ecommerce_blog"
    ecommerce_transactional = "ecommerce_transactional"
    marketplace = "marketplace"
    commercial_first_party = "commercial_first_party"
    unknown = "unknown"


class SourceOwnershipType(str, Enum):
    academic = "academic"
    scientific_publisher = "scientific_publisher"
    public_institution = "public_institution"
    nonprofit_institution = "nonprofit_institution"
    independent_editorial = "independent_editorial"
    news_organization = "news_organization"
    encyclopedia = "encyclopedia"
    ecommerce = "ecommerce"
    manufacturer = "manufacturer"
    marketplace = "marketplace"
    community = "community"
    unknown = "unknown"


class SourcePageType(str, Enum):
    research_article = "research_article"
    review_article = "review_article"
    academic_repository = "academic_repository"
    scientific_database = "scientific_database"
    institutional_article = "institutional_article"
    technical_guide = "technical_guide"
    independent_article = "independent_article"
    news_article = "news_article"
    encyclopedia_article = "encyclopedia_article"
    ecommerce_blog_article = "ecommerce_blog_article"
    product_page = "product_page"
    category_page = "category_page"
    marketplace_listing = "marketplace_listing"
    commercial_landing_page = "commercial_landing_page"
    store_search_page = "store_search_page"
    forum_thread = "forum_thread"
    other = "other"
    unknown = "unknown"


class SourceUsagePolicy(str, Enum):
    authoritative_evidence = "authoritative_evidence"
    corroborating_evidence = "corroborating_evidence"
    discovery_only = "discovery_only"
    comparison_only = "comparison_only"
    rejected = "rejected"


class ResearchSourcePolicyContract(V3StrictModel):
    policy_version: Literal["research-source-policy.v1"] = "research-source-policy.v1"
    search_rank_defines_authority: Literal[False] = False
    reject_transactional_ecommerce: Literal[True] = True
    ecommerce_blog_usage: Literal["comparison_only"] = "comparison_only"
    ecommerce_blog_min_independent_corroborators: int = Field(default=2, ge=2, le=5)
    ecommerce_blog_can_support_absolute_claim: Literal[False] = False
    ecommerce_blog_can_be_external_reference: Literal[False] = False
    critical_claim_min_independent_sources: int = Field(default=2, ge=2, le=5)
    prioritized_source_roles: list[SourceRole] = Field(
        default_factory=lambda: [
            SourceRole.scientific_primary,
            SourceRole.scientific_review,
            SourceRole.academic_repository,
            SourceRole.scientific_database,
            SourceRole.institutional,
            SourceRole.technical_procedural,
            SourceRole.independent_editorial,
            SourceRole.news_reporting,
            SourceRole.encyclopedic_discovery,
        ],
        min_length=5,
        max_length=20,
    )

    @model_validator(mode="after")
    def prevent_policy_weakening(self):
        prohibited = {
            SourceRole.ecommerce_blog,
            SourceRole.ecommerce_transactional,
            SourceRole.marketplace,
            SourceRole.commercial_first_party,
        }
        if prohibited.intersection(self.prioritized_source_roles):
            raise ValueError("Commercial sources cannot be prioritized by the V3 policy")
        required = {
            SourceRole.scientific_primary,
            SourceRole.scientific_review,
            SourceRole.institutional,
        }
        if not required.issubset(set(self.prioritized_source_roles)):
            raise ValueError("The V3 source policy must prioritize scientific and institutional sources")
        return self


class ResearchSourceSignals(V3StrictModel):
    url: AnyHttpUrl
    title: str = Field(default="", max_length=1000)
    ownership_type: SourceOwnershipType = SourceOwnershipType.unknown
    page_type: SourcePageType = SourcePageType.unknown
    is_ecommerce_domain: bool = False
    has_product_schema: bool = False
    has_offer_schema: bool = False
    has_price: bool = False
    has_sku: bool = False
    has_add_to_cart: bool = False
    has_cart_or_checkout_links: bool = False
    marketplace_signals: bool = False
    author_present: bool = False
    publication_date_present: bool = False
    references_present: bool = False
    peer_reviewed: bool = False
    primary_research: bool = False
    review_research: bool = False
    institutional_affiliation: bool = False
    commercial_intensity_score: Score = 0.0
    content_depth_score: Score = 0.0
    procedural_depth_score: Score = 0.0
    scientific_support_score: Score = 0.0
    freshness_score: Score = 0.5
    topic_relevance_score: Score = 0.0


class SourceAssessment(V3StrictModel):
    assessment_id: UUID | None = None
    url: AnyHttpUrl
    policy_version: Literal["research-source-policy.v1"] = "research-source-policy.v1"
    ownership_type: SourceOwnershipType
    page_type: SourcePageType
    source_role: SourceRole
    usage_policy: SourceUsagePolicy
    priority_score: Score
    eligible_for_primary_evidence: bool
    eligible_for_corroborating_evidence: bool
    eligible_for_external_reference: bool
    counts_toward_independent_source_diversity: bool
    requires_independent_corroboration: bool
    minimum_independent_corroborators: int = Field(ge=0, le=5)
    absolute_claim_support_allowed: bool
    allowed_evidence_roles: list[EvidenceRole] = Field(default_factory=list, max_length=30)
    reason_codes: list[str] = Field(min_length=1, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_commercial_boundaries(self):
        commercial_roles = {
            SourceRole.ecommerce_blog,
            SourceRole.ecommerce_transactional,
            SourceRole.marketplace,
            SourceRole.commercial_first_party,
        }
        if self.usage_policy == SourceUsagePolicy.rejected and any(
            (
                self.eligible_for_primary_evidence,
                self.eligible_for_corroborating_evidence,
                self.eligible_for_external_reference,
                self.counts_toward_independent_source_diversity,
                self.absolute_claim_support_allowed,
            )
        ):
            raise ValueError("Rejected sources cannot remain eligible for evidence")
        if self.source_role == SourceRole.ecommerce_blog:
            if self.usage_policy != SourceUsagePolicy.comparison_only:
                raise ValueError("E-commerce blogs are comparison-only")
            if self.eligible_for_primary_evidence or self.eligible_for_external_reference:
                raise ValueError("E-commerce blogs cannot be primary evidence or external references")
            if self.minimum_independent_corroborators < 2:
                raise ValueError("E-commerce blogs require at least two independent corroborators")
            if self.absolute_claim_support_allowed:
                raise ValueError("E-commerce blogs cannot support absolute claims")
        if self.source_role in commercial_roles and self.counts_toward_independent_source_diversity:
            raise ValueError("Commercial sources cannot count as independent diversity")
        return self


class EvidenceBundleDecision(V3StrictModel):
    status: Literal["passed", "blocked"]
    eligible_source_count: int = Field(ge=0)
    independent_source_count: int = Field(ge=0)
    authoritative_source_count: int = Field(ge=0)
    ignored_commercial_source_count: int = Field(ge=0)
    blockers: list[str] = Field(default_factory=list, max_length=30)
    warnings: list[str] = Field(default_factory=list, max_length=30)


class GapType(str, Enum):
    no_source = "no_source"
    conflicting_sources = "conflicting_sources"
    terminology_mismatch = "terminology_mismatch"
    method_dependent = "method_dependent"
    condition_dependent = "condition_dependent"
    scientific_without_procedure = "scientific_without_procedure"
    procedure_without_support = "procedure_without_support"
    overbroad_question = "overbroad_question"
    missing_transition = "missing_transition"
    missing_observation = "missing_observation"
    missing_correction = "missing_correction"


class GapResolutionStatus(str, Enum):
    open = "open"
    researching = "researching"
    resolved = "resolved"
    resolved_conditionally = "resolved_conditionally"
    disputed = "disputed"
    blocked = "blocked"


class KnowledgeNodeContract(V3StrictModel):
    node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    sequence: int = Field(ge=1, le=100)
    kind: KnowledgeNodeKind
    title_function: str = Field(min_length=8, max_length=240)
    editorial_goal: str = Field(min_length=20, max_length=1000)
    reader_state_before: str = Field(min_length=10, max_length=1000)
    reader_state_after: str = Field(min_length=10, max_length=1000)
    central_question: str = Field(min_length=8, max_length=500)
    depends_on: list[str] = Field(default_factory=list, max_length=20)
    required_knowledge: list[str] = Field(default_factory=list, max_length=40)
    required_decisions: list[str] = Field(default_factory=list, max_length=20)
    required_evidence_roles: list[EvidenceRole] = Field(
        default_factory=list, max_length=30
    )
    completion_criteria: list[str] = Field(min_length=1, max_length=30)
    branches: list[str] = Field(default_factory=list, max_length=30)
    convergence_node_id: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9_]{2,79}$"
    )
    universal_role: UniversalNodeRole = UniversalNodeRole.foundation
    applicability: NodeApplicability = NodeApplicability.required
    importance: NodeImportance = NodeImportance.core
    research_required: bool = True
    minimum_depth_weight: float = Field(default=1.0, ge=0.1, le=5.0)
    maximum_depth_weight: float | None = Field(default=None, ge=0.1, le=5.0)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_branching(self):
        if self.branches and self.kind != KnowledgeNodeKind.method_execution:
            raise ValueError("Only method_execution nodes may define method branches")
        if self.branches and not self.convergence_node_id:
            raise ValueError("A branching node must define its convergence node")
        if self.kind == KnowledgeNodeKind.method_execution and not self.branches:
            # The concrete method names are discovered later, but the contract must
            # still declare that execution is a branching stage.
            raise ValueError("A procedural method_execution node requires branches")
        return self


class KnowledgeEdgeContract(V3StrictModel):
    from_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    to_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    relation: KnowledgeEdgeRelation
    rationale: str = Field(min_length=8, max_length=500)


class ContentKnowledgeContract(V3StrictModel):
    @model_validator(mode="before")
    @classmethod
    def discard_legacy_jurisdiction(cls, value):
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        normalized.pop("jurisdiction", None)
        metadata = normalized.get("metadata")
        if isinstance(metadata, dict):
            metadata = dict(metadata)
            research_intent = metadata.get("research_intent")
            if isinstance(research_intent, dict):
                research_intent = dict(research_intent)
                research_intent.pop("jurisdiction", None)
                metadata["research_intent"] = research_intent
            normalized["metadata"] = metadata
        return normalized

    contract_version: Literal["editorial-v3"] = "editorial-v3"
    content_type: EditorialContentTypeV3
    topic: str = Field(min_length=3, max_length=500)
    reader_start_state: str = Field(min_length=10, max_length=2000)
    reader_final_state: str = Field(min_length=10, max_length=2000)
    article_promise: str = Field(min_length=20, max_length=3000)
    scope_limit: str = Field(min_length=10, max_length=2000)
    requires_method_comparison: bool = False
    requires_external_reference_per_method: bool = False
    approach_dimension: ApproachDimension | None = None
    required_method_labels: list[str] = Field(default_factory=list, max_length=20)
    research_source_policy: ResearchSourcePolicyContract = Field(
        default_factory=ResearchSourcePolicyContract
    )
    nodes: list[KnowledgeNodeContract] = Field(min_length=3, max_length=40)
    edges: list[KnowledgeEdgeContract] = Field(min_length=2, max_length=100)
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=40)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph(self):
        normalized_methods = [
            " ".join(item.casefold().split()) for item in self.required_method_labels
        ]
        if self.content_type == EditorialContentTypeV3.procedural_decision_guide:
            if self.approach_dimension is None:
                # Backward compatibility for manifests created before V3.3.1.
                # New API requests always persist the explicit dimension.
                self.approach_dimension = ApproachDimension.method
        elif self.required_method_labels:
            raise ValueError(
                "Non-procedural content cannot declare required method labels"
            )
        if len(normalized_methods) != len(set(normalized_methods)):
            raise ValueError("Required method labels must be unique")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("Knowledge node IDs must be unique")

        sequences = [node.sequence for node in self.nodes]
        if sequences != list(range(1, len(self.nodes) + 1)):
            raise ValueError(
                "Knowledge nodes must be ordered with contiguous sequence values"
            )

        positions = {node.node_id: node.sequence for node in self.nodes}
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency not in positions:
                    raise ValueError(
                        f"Knowledge node {node.node_id} references an unknown dependency"
                    )
                if positions[dependency] >= node.sequence:
                    raise ValueError(
                        f"Dependency {dependency} must precede {node.node_id}"
                    )
            if node.convergence_node_id:
                convergence_position = positions.get(node.convergence_node_id)
                if convergence_position is None:
                    raise ValueError(
                        f"Node {node.node_id} references an unknown convergence node"
                    )
                if convergence_position <= node.sequence:
                    raise ValueError(
                        "A convergence node must appear after the branching node"
                    )

        edge_keys: set[tuple[str, str, KnowledgeEdgeRelation]] = set()
        for edge in self.edges:
            if edge.from_node_id not in positions or edge.to_node_id not in positions:
                raise ValueError("Knowledge edges must reference existing nodes")
            if edge.from_node_id == edge.to_node_id:
                raise ValueError("Knowledge edges cannot point to the same node")
            key = (edge.from_node_id, edge.to_node_id, edge.relation)
            if key in edge_keys:
                raise ValueError("Duplicate knowledge edge")
            edge_keys.add(key)
            if (
                edge.relation
                in {
                    KnowledgeEdgeRelation.prerequisite,
                    KnowledgeEdgeRelation.sequence,
                    KnowledgeEdgeRelation.converges_to,
                }
                and positions[edge.from_node_id] >= positions[edge.to_node_id]
            ):
                raise ValueError(
                    "Sequential knowledge edges must point to a later node"
                )

        edge_pairs = {(edge.from_node_id, edge.to_node_id) for edge in self.edges}
        for node in self.nodes:
            missing_dependency_edges = [
                dependency
                for dependency in node.depends_on
                if (dependency, node.node_id) not in edge_pairs
            ]
            if missing_dependency_edges:
                raise ValueError(
                    f"Knowledge node {node.node_id} is missing dependency edges"
                )
            if (
                node.convergence_node_id
                and (
                    node.node_id,
                    node.convergence_node_id,
                )
                not in edge_pairs
            ):
                raise ValueError(
                    f"Knowledge node {node.node_id} is missing its convergence edge"
                )

        if self.content_type == EditorialContentTypeV3.procedural_decision_guide:
            required = {
                "subject_foundation",
                "process_requirements",
                "method_inventory",
                "method_comparison",
                "method_selection",
                "method_execution",
                "progress_confirmation",
                "transition_decision",
                "transition_execution",
                "post_transition_monitoring",
                "final_outcome_confirmation",
                "troubleshooting",
                "external_references",
            }
            missing = sorted(required.difference(node_ids))
            if missing:
                raise ValueError(
                    "Procedural guide contract is missing required nodes: "
                    + ", ".join(missing)
                )
            canonical_kinds = {
                "subject_foundation": KnowledgeNodeKind.subject_foundation,
                "process_requirements": KnowledgeNodeKind.process_requirements,
                "method_inventory": KnowledgeNodeKind.method_inventory,
                "method_comparison": KnowledgeNodeKind.method_comparison,
                "method_selection": KnowledgeNodeKind.method_selection,
                "method_execution": KnowledgeNodeKind.method_execution,
                "progress_confirmation": KnowledgeNodeKind.progress_confirmation,
                "transition_decision": KnowledgeNodeKind.transition_decision,
                "transition_execution": KnowledgeNodeKind.transition_execution,
                "post_transition_monitoring": KnowledgeNodeKind.post_transition_monitoring,
                "final_outcome_confirmation": KnowledgeNodeKind.final_outcome_confirmation,
                "troubleshooting": KnowledgeNodeKind.troubleshooting,
                "external_references": KnowledgeNodeKind.external_references,
            }
            for node in self.nodes:
                expected_kind = canonical_kinds.get(node.node_id)
                if expected_kind is not None and node.kind != expected_kind:
                    raise ValueError(
                        f"Knowledge node {node.node_id} has an incompatible kind"
                    )
            if not self.requires_method_comparison:
                raise ValueError("Procedural decision guides require method comparison")
            if not self.requires_external_reference_per_method:
                raise ValueError(
                    "Procedural decision guides require one validated reference per method"
                )
            if self.nodes[-1].node_id != "external_references":
                raise ValueError(
                    "The final structural node must consolidate external references"
                )
            final_outcome = next(
                node
                for node in self.nodes
                if node.node_id == "final_outcome_confirmation"
            )
            if final_outcome.sequence >= self.nodes[-1].sequence:
                # References may follow the outcome, but the outcome itself must be
                # reached before the article closes.
                raise ValueError("Final outcome must precede reference consolidation")
        return self


class KnowledgeClaim(V3StrictModel):
    # claim_id is canonical across corroborating source records in V3.6.1.
    claim_id: UUID | None = None
    support_group: str = Field(default="", max_length=120)
    source_claim_ids: list[UUID] = Field(default_factory=list, max_length=100)
    graph_eligible: bool = True
    approved_for_direct_writing: bool = True
    claim_text: str = Field(min_length=5, max_length=5000)
    evidence_role: EvidenceRole
    knowledge_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    method_ids: list[str] = Field(default_factory=list, max_length=30)
    conditions: list[str] = Field(default_factory=list, max_length=30)
    applicability: list[str] = Field(default_factory=list, max_length=30)
    limitations: list[str] = Field(default_factory=list, max_length=30)
    source_context: str = Field(default="", max_length=10000)
    source_locator: str = Field(min_length=1, max_length=500)
    conclusion_status: ConclusionStatus
    confidence_score: Score
    conflict_group: str | None = Field(default=None, max_length=160)
    source_fact_ids: list[UUID] = Field(min_length=1, max_length=30)


class SupportedCorrection(V3StrictModel):
    problem: str = Field(min_length=5, max_length=1000)
    why_it_matters: str = Field(min_length=5, max_length=1500)
    correction: str = Field(min_length=5, max_length=1500)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=20)


class ProcedureStep(V3StrictModel):
    step_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    sequence: int = Field(ge=1, le=100)
    action: str = Field(min_length=8, max_length=2000)
    purpose: str = Field(min_length=8, max_length=2000)
    preconditions: list[str] = Field(default_factory=list, max_length=30)
    execution_details: list[str] = Field(min_length=1, max_length=40)
    expected_observations: list[str] = Field(min_length=1, max_length=30)
    warning_signs: list[str] = Field(default_factory=list, max_length=30)
    common_mistakes: list[SupportedCorrection] = Field(
        default_factory=list, max_length=20
    )
    completion_condition: str = Field(min_length=8, max_length=1500)
    next_step_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,99}$")
    evidence_ids: list[UUID] = Field(min_length=1, max_length=40)


class ExternalReference(V3StrictModel):
    method_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    url: AnyHttpUrl
    anchor_text: str = Field(min_length=8, max_length=300)
    title: str = Field(min_length=3, max_length=500)
    author: str | None = Field(default=None, max_length=300)
    publisher: str | None = Field(default=None, max_length=300)
    source_role: SourceRole
    source_usage_policy: SourceUsagePolicy
    is_ecommerce_domain: bool = False
    is_transactional_page: bool = False
    content_match_score: Score
    procedural_depth_score: Score
    verified_at: datetime
    status: Literal["approved", "rejected", "unavailable"]
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def approved_reference_has_depth(self):
        if self.status == "approved" and (
            self.content_match_score < 0.7 or self.procedural_depth_score < 0.6
        ):
            raise ValueError(
                "Approved references must match the method and contain procedural depth"
            )
        if self.status == "approved":
            prohibited_roles = {
                SourceRole.ecommerce_blog,
                SourceRole.ecommerce_transactional,
                SourceRole.marketplace,
                SourceRole.commercial_first_party,
                SourceRole.community_question_discovery,
            }
            if self.source_role in prohibited_roles:
                raise ValueError("Commercial or community sources cannot be approved as method references")
            if self.source_usage_policy not in {
                SourceUsagePolicy.authoritative_evidence,
                SourceUsagePolicy.corroborating_evidence,
            }:
                raise ValueError("Approved references must be evidence-eligible")
            if self.is_ecommerce_domain or self.is_transactional_page:
                raise ValueError("E-commerce sources cannot be approved as external method references")
        if self.status != "approved" and not self.rejection_reasons:
            raise ValueError("Rejected or unavailable references require a reason")
        return self


class MethodDossier(V3StrictModel):
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
    steps: list[ProcedureStep] = Field(min_length=1, max_length=40)
    outcome_confirmation: list[str] = Field(
        min_length=1,
        max_length=30,
        validation_alias=AliasChoices("outcome_confirmation", "germination_confirmation"),
        serialization_alias="outcome_confirmation",
    )
    transfer_required: bool
    transfer_decision: list[str] = Field(default_factory=list, max_length=30)
    post_method_monitoring: list[str] = Field(min_length=1, max_length=30)
    external_reference: ExternalReference | None = None
    unresolved_gap_ids: list[UUID] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_steps_and_transfer(self):
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Procedure step IDs must be unique within a method")
        if [step.sequence for step in self.steps] != list(
            range(1, len(self.steps) + 1)
        ):
            raise ValueError("Procedure steps must use contiguous sequence values")
        for index, step in enumerate(self.steps):
            expected_next = (
                self.steps[index + 1].step_id if index + 1 < len(self.steps) else None
            )
            if step.next_step_id != expected_next:
                raise ValueError(
                    "Procedure step next_step_id must follow sequence order"
                )
        if self.transfer_required and not self.transfer_decision:
            raise ValueError("Methods that require transfer need observable criteria")
        if not self.transfer_required and self.transfer_decision:
            raise ValueError(
                "Methods started in the final medium must not invent a transfer decision"
            )
        return self


class DecisionRule(V3StrictModel):
    condition: str = Field(min_length=8, max_length=1500)
    supported_direction: str = Field(min_length=8, max_length=1500)
    method_ids: list[str] = Field(min_length=1, max_length=20)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=30)
    conclusion_status: ConclusionStatus

    @model_validator(mode="after")
    def prohibit_unsupported_recommendation(self):
        if self.conclusion_status in {
            ConclusionStatus.disputed,
            ConclusionStatus.insufficient_evidence,
        }:
            raise ValueError(
                "Decision rules cannot be built from disputed or insufficient evidence"
            )
        return self


class DecisionMatrix(V3StrictModel):
    dimensions: list[str] = Field(min_length=2, max_length=30)
    method_ids: list[str] = Field(min_length=2, max_length=30)
    rules: list[DecisionRule] = Field(min_length=1, max_length=60)
    universal_best_method: str | None = None
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_method_references(self):
        known = set(self.method_ids)
        for rule in self.rules:
            if not set(rule.method_ids).issubset(known):
                raise ValueError("Decision rule references an unknown method")
        if self.universal_best_method is not None:
            raise ValueError(
                "A procedural decision matrix cannot declare a universal best method; "
                "recommendations must remain conditional and evidence-backed"
            )
        return self


class SectionDossier(V3StrictModel):
    section_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,99}$")
    reader_state_before: str = Field(min_length=10, max_length=1500)
    reader_state_after: str = Field(min_length=10, max_length=1500)
    section_purpose: str = Field(min_length=20, max_length=2000)
    central_question: str = Field(min_length=8, max_length=500)
    core_answer: str = Field(min_length=20, max_length=5000)
    decision_logic: list[DecisionRule] = Field(default_factory=list, max_length=30)
    procedural_elements: list[str] = Field(default_factory=list, max_length=50)
    allowed_claim_ids: list[UUID] = Field(min_length=1, max_length=100)
    important_conditions: list[str] = Field(default_factory=list, max_length=40)
    misconceptions: list[str] = Field(default_factory=list, max_length=30)
    conflicts: list[str] = Field(default_factory=list, max_length=30)
    external_references: list[ExternalReference] = Field(
        default_factory=list, max_length=30
    )
    transition_logic: str = Field(min_length=8, max_length=2000)
    unresolved_gap_ids: list[UUID] = Field(default_factory=list, max_length=30)


class KnowledgeGap(V3StrictModel):
    gap_id: UUID | None = None
    knowledge_node_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$")
    gap_type: GapType
    description: str = Field(min_length=10, max_length=3000)
    essential: bool = True
    status: GapResolutionStatus = GapResolutionStatus.open
    original_problem: str = Field(default="", max_length=3000)
    reframed_problem: str = Field(default="", max_length=3000)
    supporting_evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)
    conflicting_evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)
    allowed_conclusion: str = Field(default="", max_length=3000)
    prohibited_conclusions: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_resolution(self):
        resolved = self.status in {
            GapResolutionStatus.resolved,
            GapResolutionStatus.resolved_conditionally,
        }
        if resolved and not self.allowed_conclusion.strip():
            raise ValueError("Resolved gaps require an allowed conclusion")
        if self.status == GapResolutionStatus.resolved_conditionally and not (
            self.reframed_problem.strip() and self.prohibited_conclusions
        ):
            raise ValueError(
                "Conditional resolutions require a reframed problem and limits"
            )
        return self


class KnowledgeCompletenessReport(V3StrictModel):
    status: Literal["passed", "blocked"]
    score: Score
    blockers: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)
    covered_node_ids: list[str] = Field(default_factory=list, max_length=100)
    missing_node_ids: list[str] = Field(default_factory=list, max_length=100)
    unresolved_essential_gap_ids: list[UUID] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_status(self):
        if self.status == "passed" and (
            self.blockers
            or self.missing_node_ids
            or self.unresolved_essential_gap_ids
            or self.score < 0.85
        ):
            raise ValueError("A passed knowledge gate cannot contain blockers or gaps")
        if self.status == "blocked" and not self.blockers:
            raise ValueError("A blocked knowledge gate must explain why")
        return self
