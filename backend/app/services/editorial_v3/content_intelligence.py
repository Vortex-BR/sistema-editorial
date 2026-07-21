"""Deterministic Editorial Intelligence Core.

This service is the canonical bridge between the editorial contract, the
research artifacts and the writer.  It deliberately does not ask a language
model to decide whether the system is ready: question coverage, evidence
closure, section ownership and claim policies are calculated from persisted
artifacts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

from app.schemas.editorial_intelligence import (
    ClaimWriterPolicy,
    ContentIntelligenceState,
    EditorialQuestion,
    EditorialQuestionKind,
    EmergentEditorialQuestionProposal,
    EvidenceClaimNode,
    EvidenceConflictNode,
    EvidenceGraph,
    EvidenceSourceNode,
    IntelligenceFinding,
    IntelligenceLifecycle,
    IntelligenceValidationReport,
    QuestionAnswerRecord,
    QuestionAnswerStatus,
    QuestionClaimCoverage,
    QuestionCoverageStatus,
    RecoveryClass,
    SectionIntelligencePlan,
)
from app.schemas.editorial_v3 import (
    ConclusionStatus,
    ContentKnowledgeContract,
    EvidenceRole,
    KnowledgeGap,
    SectionDossier,
)
from app.schemas.editorial_v3_runtime import (
    StructuredSourceDocument,
    V3ResearchPlan,
    V3WriterOutput,
)
from app.services.editorial_v3.generation_context import active_node_ids
from app.services.editorial_v3.text_integrity import (
    claim_supports_sentence,
    is_potentially_factual,
    normalized_text,
    stable_slug,
    support_group_compatible,
)

_WORD = re.compile(r"[a-zA-ZÀ-ÿ0-9]+")
_UNCERTAINTY_MARKERS = {
    "incerto",
    "incerta",
    "evidencia limitada",
    "evidência limitada",
    "nao ha consenso",
    "não há consenso",
    "resultados divergem",
    "pode variar",
    "depende",
    "disputado",
    "uncertain",
    "limited evidence",
    "no consensus",
    "results differ",
    "may vary",
    "depends",
    "incierto",
    "evidencia limitada",
    "no hay consenso",
    "puede variar",
    "depende",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _unique(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = " ".join(str(raw or "").split()).strip()
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _uuid(value: object) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _question_id(section_id: str, kind: EditorialQuestionKind, index: int) -> str:
    base = stable_slug(f"{section_id}_{kind.value}_{index}", separator="_", limit=120)
    return f"q_{base}"


def _claim_policy(status: ConclusionStatus) -> ClaimWriterPolicy:
    if status in {ConclusionStatus.confirmed, ConclusionStatus.well_supported}:
        return ClaimWriterPolicy.direct
    if status == ConclusionStatus.conditional:
        return ClaimWriterPolicy.conditional
    if status == ConclusionStatus.disputed:
        return ClaimWriterPolicy.context_only
    return ClaimWriterPolicy.prohibited


_CONCLUSION_RISK = {
    ConclusionStatus.confirmed: 0,
    ConclusionStatus.well_supported: 1,
    ConclusionStatus.conditional: 2,
    ConclusionStatus.disputed: 3,
    ConclusionStatus.insufficient_evidence: 4,
}


def _conservative_conclusion(values: Iterable[object]) -> ConclusionStatus:
    statuses: list[ConclusionStatus] = []
    for value in values:
        try:
            statuses.append(ConclusionStatus(str(value)))
        except ValueError:
            statuses.append(ConclusionStatus.insufficient_evidence)
    return max(
        statuses or [ConclusionStatus.insufficient_evidence],
        key=lambda item: _CONCLUSION_RISK[item],
    )


def _intent(content_type: str, commercial: Mapping[str, object]) -> str:
    mapping = {
        "procedural_decision_guide": "procedural_decision",
        "procedural_how_to": "procedural_execution",
        "comparison": "comparative_decision",
        "troubleshooting": "problem_resolution",
        "explanatory_guide": "informational_explanation",
        "commercial_education": "commercial_education",
    }
    base = mapping.get(content_type, "informational")
    if any(str(commercial.get(key) or "").strip() for key in ("objective", "offer", "desired_action")):
        return f"{base}_with_commercial_transition"
    return base


def _phrase_coverage(phrase: str, text: str) -> float:
    wanted = {item.casefold() for item in _WORD.findall(phrase) if len(item) >= 3}
    if not wanted:
        return 0.0
    present = {item.casefold() for item in _WORD.findall(text)}
    return len(wanted & present) / len(wanted)


_SEMANTIC_STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na",
    "nos", "nas", "para", "por", "com", "sem", "uma", "um", "que", "qual", "quais",
    "como", "quando", "onde", "porque", "porquê", "ser", "estar", "tem", "ter", "sobre",
    "the", "a", "an", "of", "to", "and", "in", "on", "for", "with", "without", "what",
    "which", "how", "when", "where", "why", "is", "are", "be", "about", "el", "la", "los",
    "las", "del", "y", "en", "con", "sin", "qué", "cuál", "cómo", "cuándo", "dónde",
}
_SEMANTIC_EQUIVALENTS = {
    "temperatura": {"temperatura", "termico", "termica", "calor", "frio", "graus", "celsius"},
    "umidade": {"umidade", "humidade", "humedad", "moisture", "humidity", "agua", "água"},
    "prazo": {"prazo", "tempo", "duracao", "duração", "dias", "horas", "timeline", "duration"},
    "risco": {"risco", "perigo", "falha", "problema", "risk", "danger", "error"},
    "causa": {"causa", "motivo", "origem", "mecanismo", "cause", "reason", "mechanism"},
    "resultado": {"resultado", "efeito", "consequencia", "consequência", "outcome", "effect"},
    "quantidade": {"quantidade", "volume", "dose", "nivel", "nível", "amount", "quantity"},
    "comparacao": {"comparacao", "comparação", "diferenca", "diferença", "versus", "melhor", "pior", "comparison"},
}
_SEMANTIC_GENERIC_TOKENS = {
    "metodo", "método", "processo", "etapa", "conteudo", "conteúdo", "informacao",
    "informação", "explicar", "leitor", "guia", "forma", "maneira", "contexto",
    "adequado", "adequada", "correto", "correta", "principal", "importante",
    "method", "process", "step", "content", "information", "reader", "guide",
}
_SEMANTIC_DIMENSIONS = frozenset(_SEMANTIC_EQUIVALENTS)


def _semantic_tokens(value: str) -> set[str]:
    raw = {item.casefold() for item in _WORD.findall(normalized_text(value)) if len(item) >= 3}
    result = {item for item in raw if item not in _SEMANTIC_STOPWORDS}
    expanded = set(result)
    for canonical, variants in _SEMANTIC_EQUIVALENTS.items():
        normalized_variants = {normalized_text(item) for item in variants}
        if result & normalized_variants:
            expanded.add(canonical)
    return expanded


def _semantic_alignment(question: str, candidate: str) -> float:
    q_tokens = _semantic_tokens(question)
    c_tokens = _semantic_tokens(candidate)
    if not q_tokens or not c_tokens:
        return 0.0

    q_anchors = q_tokens - _SEMANTIC_GENERIC_TOKENS
    c_anchors = c_tokens - _SEMANTIC_GENERIC_TOKENS
    if not q_anchors or not c_anchors:
        return 0.0

    q_dimensions = q_anchors & _SEMANTIC_DIMENSIONS
    c_dimensions = c_anchors & _SEMANTIC_DIMENSIONS
    if q_dimensions and not (q_dimensions & c_dimensions):
        # A question about temperature cannot be closed by a claim about a
        # different measurable dimension merely because both mention a method.
        return 0.0

    overlap = q_anchors & c_anchors
    if not overlap:
        return 0.0

    directional = len(overlap) / len(q_anchors)
    jaccard = len(overlap) / len(q_anchors | c_anchors)
    sequence = _phrase_coverage(question, candidate)
    long_anchor_overlap = {
        token for token in overlap if len(token) >= 6 and token not in _SEMANTIC_DIMENSIONS
    }
    anchor_bonus = min(0.12, len(long_anchor_overlap) * 0.04)
    generic_overlap = len((q_tokens & c_tokens) & _SEMANTIC_GENERIC_TOKENS)
    generic_penalty = min(0.12, generic_overlap * 0.03)
    score = (
        (directional * 0.58)
        + (jaccard * 0.24)
        + (sequence * 0.18)
        + anchor_bonus
        - generic_penalty
    )
    return round(max(0.0, min(1.0, score)), 4)


def _canonical_claim_id(state: ContentIntelligenceState, raw: Mapping[str, object]) -> UUID | None:
    explicit = _uuid(raw.get("claim_id"))
    # Repository outputs already expose claim_id as the persisted canonical ID and
    # source_claim_ids as the underlying records. Preserve that identity exactly,
    # including IDs backfilled by migration 0035. Raw extraction rows from older
    # callers do not contain source_claim_ids and are still canonicalized here.
    if "source_claim_ids" in raw and explicit is not None:
        return explicit
    support_group = normalized_text(str(raw.get("support_group") or ""))
    if support_group:
        return uuid5(
            NAMESPACE_URL,
            f"editorial-intelligence:{state.pipeline_run_id}:claim:{support_group}",
        )
    return explicit


def _draft_artifact_hash(draft: V3WriterOutput) -> str:
    payload = draft.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class ContentIntelligenceEngine:
    """Build, enrich and validate the canonical editorial state."""

    def initialize(
        self,
        *,
        project_id: UUID,
        pipeline_run_id: UUID,
        contract_id: UUID | None,
        contract: ContentKnowledgeContract,
        generation_brief: Mapping[str, object],
    ) -> ContentIntelligenceState:
        active = set(active_node_ids(contract))
        resolution = dict(contract.metadata.get("node_resolution") or {})
        questions: list[EditorialQuestion] = []
        sections: list[SectionIntelligencePlan] = []

        for node in contract.nodes:
            if node.node_id not in active:
                continue
            node_resolution = resolution.get(node.node_id) or {}
            resolved_research_required = bool(
                node_resolution.get("research_required", node.research_required)
            )
            section_questions: list[EditorialQuestion] = []
            section_questions.append(
                EditorialQuestion(
                    question_id=_question_id(node.node_id, EditorialQuestionKind.central, 1),
                    section_id=node.node_id,
                    kind=EditorialQuestionKind.central,
                    question=node.central_question,
                    critical=node.importance.value in {"critical", "core"},
                    research_required=resolved_research_required,
                    required_evidence_roles=node.required_evidence_roles,
                    completion_signal="; ".join(node.completion_criteria[:5]),
                )
            )
            for index, item in enumerate(node.required_knowledge, start=1):
                section_questions.append(
                    EditorialQuestion(
                        question_id=_question_id(node.node_id, EditorialQuestionKind.knowledge, index),
                        section_id=node.node_id,
                        kind=EditorialQuestionKind.knowledge,
                        question=item,
                        critical=node.importance.value in {"critical", "core"},
                        research_required=resolved_research_required,
                        required_evidence_roles=node.required_evidence_roles,
                        completion_signal="Conhecimento explicitamente explicado no contexto da seção.",
                    )
                )
            for index, item in enumerate(node.required_decisions, start=1):
                section_questions.append(
                    EditorialQuestion(
                        question_id=_question_id(node.node_id, EditorialQuestionKind.decision, index),
                        section_id=node.node_id,
                        kind=EditorialQuestionKind.decision,
                        question=item,
                        critical=True,
                        research_required=resolved_research_required,
                        required_evidence_roles=node.required_evidence_roles,
                        completion_signal="Critério de decisão e limites apresentados ao leitor.",
                    )
                )
            questions.extend(section_questions)
            sections.append(
                SectionIntelligencePlan(
                    section_id=node.node_id,
                    sequence=node.sequence,
                    title_function=node.title_function,
                    editorial_goal=node.editorial_goal,
                    reader_state_before=node.reader_state_before,
                    reader_state_after=node.reader_state_after,
                    depends_on=[item for item in node.depends_on if item in active],
                    question_ids=[item.question_id for item in section_questions],
                    research_required=resolved_research_required,
                    importance=node.importance.value,
                    minimum_depth_weight=node.minimum_depth_weight,
                    completion_criteria=node.completion_criteria,
                    prohibited_conclusions=list(contract.prohibited_conclusions),
                )
            )

        now = _now()
        brief_reader = dict(generation_brief.get("reader") or {})
        objective = str(generation_brief.get("content_objective") or "").strip()
        state = ContentIntelligenceState(
            project_id=project_id,
            pipeline_run_id=pipeline_run_id,
            contract_id=contract_id,
            created_at=now,
            updated_at=now,
            locale=str(generation_brief.get("locale") or "pt-BR"),
            topic=contract.topic,
            content_type=contract.content_type.value,
            content_objective=objective or contract.article_promise,
            search_intent=_intent(
                contract.content_type.value,
                dict(generation_brief.get("commercial") or {}),
            ),
            reader_profile={
                **brief_reader,
                "start_state": contract.reader_start_state,
                "final_state": contract.reader_final_state,
            },
            commercial_context=dict(generation_brief.get("commercial") or {}),
            brand_context=dict(generation_brief.get("brand") or {}),
            generation_constraints={
                "structure": dict(generation_brief.get("structure") or {}),
                "primary_keyword": generation_brief.get("primary_keyword") or "",
                "secondary_keywords": list(generation_brief.get("secondary_keywords") or []),
                "scope_limit": contract.scope_limit,
                "article_promise": contract.article_promise,
                "internal_link": generation_brief.get("internal_link") or "",
            },
            prohibited_claims=_unique(
                [
                    *contract.prohibited_conclusions,
                    *list(
                        (generation_brief.get("evidence_policy") or {}).get(
                            "claims_to_avoid", []
                        )
                    ),
                ]
            ),
            questions=questions,
            sections=sections,
            validation=self._planning_validation(questions, sections),
        )
        return self._with_checksum(state)

    def add_emergent_questions(
        self,
        state: ContentIntelligenceState,
        *,
        proposals: Iterable[EmergentEditorialQuestionProposal],
        claims: Iterable[Mapping[str, object]],
        maximum_questions: int,
    ) -> ContentIntelligenceState:
        """Add bounded, evidence-grounded questions discovered after research.

        The language model may propose candidates, but deterministic checks own
        section validity, duplication, evidence relevance and criticality.
        """
        if maximum_questions <= 0:
            return state
        section_map = {item.section_id: item for item in state.sections}
        claim_texts: dict[str, list[str]] = defaultdict(list)
        for raw in claims:
            section_id = str(raw.get("knowledge_node_id") or "").strip()
            claim_text = " ".join(str(raw.get("claim_text") or "").split())
            if section_id in section_map and claim_text:
                claim_texts[section_id].append(claim_text)

        questions = list(state.questions)
        existing_ids = {item.question_id for item in questions}
        accepted: list[EditorialQuestion] = []
        for proposal in proposals:
            if len(accepted) >= maximum_questions:
                break
            section = section_map.get(proposal.section_id)
            if section is None or not section.research_required:
                continue
            question_text = " ".join(proposal.question.split())
            if any(
                max(
                    _semantic_alignment(question_text, item.question),
                    _semantic_alignment(item.question, question_text),
                )
                >= 0.68
                for item in questions
            ):
                continue
            evidence_score = max(
                (
                    _semantic_alignment(question_text, claim_text)
                    for claim_text in claim_texts.get(proposal.section_id, [])
                ),
                default=0.0,
            )
            if evidence_score < 0.20:
                continue
            index = len(
                [item for item in questions if item.section_id == proposal.section_id]
            ) + 1
            question_id = _question_id(proposal.section_id, proposal.kind, index)
            while question_id in existing_ids:
                index += 1
                question_id = _question_id(proposal.section_id, proposal.kind, index)
            accepted_question = EditorialQuestion(
                question_id=question_id,
                section_id=proposal.section_id,
                kind=proposal.kind,
                question=question_text,
                critical=bool(proposal.critical and evidence_score >= 0.34),
                research_required=True,
                required_evidence_roles=proposal.required_evidence_roles,
                completion_signal=(
                    proposal.completion_signal
                    or "A lacuna emergente é respondida com evidência rastreável."
                ),
                origin="emergent",
                rationale=proposal.rationale,
            )
            accepted.append(accepted_question)
            questions.append(accepted_question)
            existing_ids.add(question_id)

        if not accepted:
            return state
        ids_by_section: dict[str, list[str]] = defaultdict(list)
        for question in accepted:
            ids_by_section[question.section_id].append(question.question_id)
        sections = [
            section.model_copy(
                update={
                    "question_ids": list(
                        dict.fromkeys(
                            [*section.question_ids, *ids_by_section.get(section.section_id, [])]
                        )
                    )
                }
            )
            for section in state.sections
        ]
        updated = state.model_copy(
            update={
                "revision": state.revision + 1,
                "updated_at": _now(),
                "questions": questions,
                "sections": sections,
                "validation": self._planning_validation(questions, sections),
                "checksum": "",
            }
        )
        return self._with_checksum(updated)

    def attach_evidence(
        self,
        state: ContentIntelligenceState,
        *,
        claims: Iterable[Mapping[str, object]],
        source_documents: Iterable[StructuredSourceDocument],
        section_dossiers: Iterable[SectionDossier],
        gaps: Iterable[KnowledgeGap],
        claim_provenance: Mapping[str, Mapping[str, object]],
    ) -> ContentIntelligenceState:
        source_by_id: dict[UUID, EvidenceSourceNode] = {}
        for item in source_documents:
            source_by_id.setdefault(
                item.document_id,
                EvidenceSourceNode(
                    source_id=item.document_id,
                    canonical_url=str(item.canonical_url),
                    title=item.title,
                    source_role=item.assessment.source_role.value,
                    usage_policy=item.assessment.usage_policy.value,
                    publisher=str(item.publisher or ""),
                    content_hash=item.content_hash,
                ),
            )
        sources = list(source_by_id.values())
        known_source_ids = set(source_by_id)
        claim_nodes: list[EvidenceClaimNode] = []
        by_section: dict[str, list[UUID]] = defaultdict(list)
        by_conflict: dict[str, list[EvidenceClaimNode]] = defaultdict(list)
        source_claim_to_canonical: dict[UUID, UUID] = {}

        grouped_claim_inputs: dict[UUID, list[Mapping[str, object]]] = defaultdict(list)
        for raw in claims:
            canonical_id = _canonical_claim_id(state, raw)
            if canonical_id is None:
                continue
            grouped_claim_inputs[canonical_id].append(raw)
            for source_claim_id in [raw.get("claim_id"), *(raw.get("source_claim_ids") or [])]:
                parsed = _uuid(source_claim_id)
                if parsed is not None:
                    source_claim_to_canonical[parsed] = canonical_id

        for claim_id, raw_group in grouped_claim_inputs.items():
            provenance_candidates = [claim_provenance.get(str(claim_id), {})]
            for raw in raw_group:
                for key in [raw.get("claim_id"), *(raw.get("source_claim_ids") or [])]:
                    if key:
                        provenance_candidates.append(claim_provenance.get(str(key), {}))
            source_ids = list(
                dict.fromkeys(
                    item
                    for provenance in provenance_candidates
                    for item in (
                        _uuid(value)
                        for value in provenance.get("source_document_ids", []) or []
                    )
                    if item is not None and item in known_source_ids
                )
            )
            if not source_ids:
                continue

            section_ids = _unique(str(raw.get("knowledge_node_id") or "") for raw in raw_group)
            evidence_roles = _unique(str(raw.get("evidence_role") or "definition") for raw in raw_group)
            support_groups = _unique(str(raw.get("support_group") or "") for raw in raw_group)
            claim_texts = _unique(str(raw.get("claim_text") or "") for raw in raw_group)
            conflict_groups = _unique(str(raw.get("conflict_group") or "") for raw in raw_group)
            integrity_issues: list[str] = []
            if len(section_ids) != 1:
                integrity_issues.append("claim_section_mismatch")
            if len(evidence_roles) != 1:
                integrity_issues.append("claim_evidence_role_mismatch")
            if len(support_groups) > 1:
                integrity_issues.append("claim_support_group_mismatch")
            if len(conflict_groups) > 1:
                integrity_issues.append("claim_conflict_group_mismatch")
            for index, left in enumerate(claim_texts):
                for right in claim_texts[index + 1 :]:
                    compatible, _reason = support_group_compatible(left, right)
                    if not compatible:
                        integrity_issues.append("claim_text_semantic_mismatch")
                        break

            conclusion = _conservative_conclusion(raw.get("conclusion_status") for raw in raw_group)
            source_fact_ids = list(
                dict.fromkeys(
                    item
                    for item in (
                        _uuid(value)
                        for raw in raw_group
                        for value in raw.get("source_fact_ids", []) or []
                    )
                    if item is not None
                )
            )
            source_claim_ids = list(
                dict.fromkeys(
                    item
                    for item in (
                        _uuid(value)
                        for raw in raw_group
                        for value in [raw.get("claim_id"), *(raw.get("source_claim_ids") or [])]
                    )
                    if item is not None and item != claim_id
                )
            )
            confidence_values = [float(raw.get("confidence_score") or 0) for raw in raw_group]
            approved_for_direct = any(
                bool(raw.get("approved_for_direct_writing", True)) for raw in raw_group
            )
            policy = _claim_policy(conclusion)
            if integrity_issues or (
                not approved_for_direct
                and policy in {ClaimWriterPolicy.direct, ClaimWriterPolicy.conditional}
            ):
                policy = ClaimWriterPolicy.prohibited
            node = EvidenceClaimNode(
                claim_id=claim_id,
                source_claim_ids=source_claim_ids,
                support_group=(support_groups or [""])[0],
                claim_text=max(claim_texts or ["Claim sem texto válido"], key=len),
                section_id=(section_ids or ["unknown_section"])[0],
                evidence_role=(evidence_roles or [EvidenceRole.definition.value])[0],
                source_ids=source_ids,
                source_fact_ids=source_fact_ids,
                method_ids=_unique(
                    str(value) for raw in raw_group for value in raw.get("method_ids", []) or []
                ),
                conditions=_unique(
                    str(value) for raw in raw_group for value in raw.get("conditions", []) or []
                ),
                limitations=_unique(
                    str(value) for raw in raw_group for value in raw.get("limitations", []) or []
                ),
                applicability=_unique(
                    str(value) for raw in raw_group for value in raw.get("applicability", []) or []
                ),
                conclusion_status=conclusion,
                confidence_score=min(confidence_values or [0.0]),
                conflict_group=(conflict_groups[0] if conflict_groups else None),
                writer_policy=policy,
                integrity_issues=list(dict.fromkeys(integrity_issues)),
            )
            claim_nodes.append(node)
            by_section[node.section_id].append(node.claim_id)
            if node.conflict_group:
                by_conflict[node.conflict_group].append(node)

        dossier_map = {item.section_id: item for item in section_dossiers}
        claim_map = {item.claim_id: item for item in claim_nodes}
        conflicts: list[EvidenceConflictNode] = []
        conflict_ids_by_section: dict[str, list[str]] = defaultdict(list)
        for group, grouped_claims in sorted(by_conflict.items()):
            for section_id in sorted({item.section_id for item in grouped_claims}):
                members = [item for item in grouped_claims if item.section_id == section_id]
                if not members:
                    continue
                conflict_id = "conflict_" + stable_slug(
                    f"{section_id}_{group}", separator="_", limit=145
                )
                statuses = {item.conclusion_status for item in members}
                status = (
                    "unresolved"
                    if ConclusionStatus.insufficient_evidence in statuses
                    else "resolved_conditionally"
                    if ConclusionStatus.conditional in statuses
                    else "represented"
                )
                conflict = EvidenceConflictNode(
                    conflict_id=conflict_id,
                    section_id=section_id,
                    claim_ids=[item.claim_id for item in members],
                    status=status,
                    required_language=(
                        "Apresente explicitamente a divergência, as condições e os limites; "
                        "não transforme uma posição disputada em regra universal."
                    ),
                    prohibited_conclusions=[
                        "Não declarar consenso, causalidade ou superioridade universal quando as fontes divergem."
                    ],
                )
                conflicts.append(conflict)
                conflict_ids_by_section[section_id].append(conflict_id)

        updated_sections: list[SectionIntelligencePlan] = []
        allowed_by_section: dict[str, set[UUID]] = {}
        for section in state.sections:
            dossier = dossier_map.get(section.section_id)
            dossier_claims = {
                source_claim_to_canonical.get(value, value)
                for value in (dossier.allowed_claim_ids if dossier else [])
            }
            section_claims = list(dict.fromkeys(by_section.get(section.section_id, [])))
            if dossier is not None:
                section_claims = [item for item in section_claims if item in dossier_claims]
            allowed = [
                item for item in section_claims
                if claim_map[item].writer_policy != ClaimWriterPolicy.prohibited
            ]
            prohibited = [
                item for item in by_section.get(section.section_id, [])
                if claim_map[item].writer_policy == ClaimWriterPolicy.prohibited
                or (dossier is not None and item not in dossier_claims)
            ]
            allowed_by_section[section.section_id] = set(allowed)
            required_conditions = _unique(
                value
                for claim_id in allowed
                for value in [*claim_map[claim_id].conditions, *claim_map[claim_id].limitations]
            )
            dossier_conflicts = list(dossier.conflicts if dossier else [])
            updated_sections.append(
                section.model_copy(
                    update={
                        "allowed_claim_ids": list(dict.fromkeys(allowed)),
                        "prohibited_claim_ids": list(dict.fromkeys(prohibited)),
                        "conflict_ids": conflict_ids_by_section.get(section.section_id, []),
                        "required_conditions": required_conditions,
                        "prohibited_conclusions": _unique(
                            [*section.prohibited_conclusions, *dossier_conflicts]
                        ),
                    }
                )
            )

        question_claim_map: dict[str, list[UUID]] = {}
        question_alignment_scores: dict[str, float] = {}
        question_coverage: list[QuestionClaimCoverage] = []
        for question in state.questions:
            supported: list[tuple[UUID, float]] = []
            for claim_id in by_section.get(question.section_id, []):
                claim = claim_map[claim_id]
                authorized = claim_id in allowed_by_section.get(question.section_id, set())
                role_compatible = (
                    not question.required_evidence_roles
                    or claim.evidence_role in question.required_evidence_roles
                )
                score = _semantic_alignment(question.question, claim.claim_text)
                has_provenance = bool(claim.source_ids and claim.source_fact_ids)
                if (
                    authorized
                    and role_compatible
                    and has_provenance
                    and claim.writer_policy != ClaimWriterPolicy.prohibited
                    and score >= 0.34
                ):
                    status = QuestionCoverageStatus.semantically_supported
                    reason = "authorized_role_compatible_semantic_alignment"
                    supported.append((claim_id, score))
                elif score >= 0.20:
                    status = QuestionCoverageStatus.candidate
                    reason = "candidate_below_semantic_or_authorization_threshold"
                else:
                    status = QuestionCoverageStatus.unsupported
                    reason = "semantic_alignment_below_threshold"
                question_coverage.append(
                    QuestionClaimCoverage(
                        question_id=question.question_id,
                        claim_id=claim_id,
                        status=status,
                        alignment_score=score,
                        authorized_in_section=authorized,
                        role_compatible=role_compatible,
                        source_ids=claim.source_ids,
                        reason=reason,
                    )
                )
            ranked = [item for item, _score in sorted(supported, key=lambda item: item[1], reverse=True)]
            question_claim_map[question.question_id] = ranked
            question_alignment_scores[question.question_id] = round(
                max((score for _claim_id, score in supported), default=0.0), 4
            )

        unresolved_gap_ids = [
            item.gap_id
            for item in gaps
            if item.gap_id is not None
            and item.essential
            and item.status.value not in {"resolved", "resolved_conditionally"}
        ]
        graph = EvidenceGraph(
            sources=sources,
            claims=claim_nodes,
            conflicts=conflicts,
            section_claim_map={
                key: list(dict.fromkeys(value)) for key, value in by_section.items()
            },
            question_claim_map=question_claim_map,
            question_alignment_scores=question_alignment_scores,
            question_coverage=question_coverage,
        )
        enriched = state.model_copy(
            update={
                "intelligence_version": "editorial-intelligence-v1.1",
                "revision": state.revision + 1,
                "lifecycle": IntelligenceLifecycle.evidence_attached,
                "updated_at": _now(),
                "sections": updated_sections,
                "evidence_graph": graph,
                "question_answer_map": [],
                "unresolved_gap_ids": unresolved_gap_ids,
                "validation": None,
                "validated_artifact_hash": None,
                "article_version_id": None,
                "checksum": "",
            }
        )
        return self._with_checksum(enriched)

    def validate_writer_readiness(
        self, state: ContentIntelligenceState
    ) -> IntelligenceValidationReport:
        blockers: list[IntelligenceFinding] = []
        warnings: list[IntelligenceFinding] = []
        claims = {item.claim_id: item for item in state.evidence_graph.claims}
        coverage_by_question: dict[str, list[QuestionClaimCoverage]] = defaultdict(list)
        for edge in state.evidence_graph.question_coverage:
            coverage_by_question[edge.question_id].append(edge)
        critical_questions = [item for item in state.questions if item.critical]
        covered_critical = 0

        for section in state.sections:
            if not section.question_ids:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_SECTION_WITHOUT_QUESTIONS",
                        message="A seção não possui perguntas editoriais canônicas.",
                        section_id=section.section_id,
                        recovery_class=RecoveryClass.contract_error,
                    )
                )
            if section.research_required and not section.allowed_claim_ids:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_SECTION_WITHOUT_EVIDENCE",
                        message="A seção exige pesquisa, mas não possui claims autorizados e utilizáveis.",
                        section_id=section.section_id,
                        recovery_class=RecoveryClass.recoverable,
                    )
                )
            for claim_id in section.allowed_claim_ids:
                claim = claims.get(claim_id)
                if claim is None:
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_SECTION_UNKNOWN_CLAIM",
                            message="O plano de seção referencia um claim ausente do grafo.",
                            section_id=section.section_id,
                            claim_id=claim_id,
                            recovery_class=RecoveryClass.contract_error,
                        )
                    )
                elif claim.section_id != section.section_id:
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_CROSS_SECTION_CLAIM",
                            message="Um claim foi autorizado em uma seção diferente de sua responsabilidade editorial.",
                            section_id=section.section_id,
                            claim_id=claim_id,
                            recovery_class=RecoveryClass.contract_error,
                            details={"claim_section_id": claim.section_id},
                        )
                    )
                elif claim.integrity_issues:
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_CLAIM_INTEGRITY_INVALID",
                            message="Um claim canônico possui registros de origem semanticamente incompatíveis.",
                            section_id=section.section_id,
                            claim_id=claim_id,
                            recovery_class=RecoveryClass.nonrecoverable,
                            details={"issues": claim.integrity_issues},
                        )
                    )
                elif not claim.source_fact_ids:
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_CLAIM_WITHOUT_SOURCE_FACT",
                            message="Um claim autorizado não possui vínculo com fatos-fonte persistidos.",
                            section_id=section.section_id,
                            claim_id=claim_id,
                            recovery_class=RecoveryClass.recoverable,
                        )
                    )
                elif claim.writer_policy == ClaimWriterPolicy.context_only:
                    warnings.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_CONTEXT_ONLY_CLAIM",
                            message="O claim só pode aparecer como incerteza, divergência ou contexto limitado.",
                            section_id=section.section_id,
                            claim_id=claim_id,
                        )
                    )

        for question in critical_questions:
            section = next(item for item in state.sections if item.section_id == question.section_id)
            supported_edges = [
                edge
                for edge in coverage_by_question.get(question.question_id, [])
                if edge.status
                in {
                    QuestionCoverageStatus.semantically_supported,
                    QuestionCoverageStatus.human_overridden,
                }
                and edge.authorized_in_section
                and edge.role_compatible
                and edge.claim_id in set(section.allowed_claim_ids)
                and edge.claim_id in claims
                and claims[edge.claim_id].writer_policy
                in {ClaimWriterPolicy.direct, ClaimWriterPolicy.conditional}
                and bool(claims[edge.claim_id].source_fact_ids)
            ]
            if question.research_required and not supported_edges:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_CRITICAL_QUESTION_UNSUPPORTED",
                        message="Uma pergunta crítica não possui claim semanticamente alinhado, autorizado e rastreável.",
                        section_id=question.section_id,
                        question_id=question.question_id,
                        recovery_class=RecoveryClass.recoverable,
                        details={
                            "question": question.question,
                            "minimum_alignment": 0.34,
                            "candidate_edges": [
                                edge.model_dump(mode="json")
                                for edge in coverage_by_question.get(question.question_id, [])
                                if edge.status == QuestionCoverageStatus.candidate
                            ][:10],
                        },
                    )
                )
            else:
                covered_critical += 1

        for conflict in state.evidence_graph.conflicts:
            if conflict.status == "unresolved":
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_UNRESOLVED_CONFLICT",
                        message="Existe conflito de evidência sem conclusão editorial segura.",
                        section_id=conflict.section_id,
                        recovery_class=RecoveryClass.recoverable,
                        details={"conflict_id": conflict.conflict_id},
                    )
                )
        for gap_id in state.unresolved_gap_ids:
            blockers.append(
                IntelligenceFinding(
                    code="INTELLIGENCE_ESSENTIAL_GAP_OPEN",
                    message="Uma lacuna essencial continua aberta antes da redação.",
                    recovery_class=RecoveryClass.recoverable,
                    details={"gap_id": str(gap_id)},
                )
            )

        total_critical = len(critical_questions)
        score = 1.0 if not total_critical else covered_critical / total_critical
        if blockers:
            score = min(
                score,
                max(0.0, 1.0 - min(1.0, len(blockers) / max(1, len(state.sections)))),
            )
        recoverable_count = sum(
            item.recovery_class == RecoveryClass.recoverable for item in blockers
        )
        return IntelligenceValidationReport(
            status="blocked" if blockers else "passed",
            phase="writer_readiness",
            score=round(score, 4),
            blockers=blockers,
            warnings=warnings,
            metrics={
                "section_count": len(state.sections),
                "question_count": len(state.questions),
                "critical_question_count": total_critical,
                "covered_critical_question_count": covered_critical,
                "source_count": len(state.evidence_graph.sources),
                "canonical_claim_count": len(state.evidence_graph.claims),
                "conflict_count": len(state.evidence_graph.conflicts),
                "unresolved_gap_count": len(state.unresolved_gap_ids),
                "recoverable_blocker_count": recoverable_count,
                "nonrecoverable_blocker_count": len(blockers) - recoverable_count,
            },
        )

    def mark_writer_ready(
        self,
        state: ContentIntelligenceState,
        report: IntelligenceValidationReport,
    ) -> ContentIntelligenceState:
        lifecycle = (
            IntelligenceLifecycle.writer_ready
            if report.status == "passed"
            else IntelligenceLifecycle.blocked
        )
        updated = state.model_copy(
            update={
                "revision": state.revision + 1,
                "lifecycle": lifecycle,
                "updated_at": _now(),
                "validation": report,
                "question_answer_map": [],
                "validated_artifact_hash": None,
                "article_version_id": None,
                "checksum": "",
            }
        )
        return self._with_checksum(updated)

    def mark_draft_pending(
        self,
        state: ContentIntelligenceState,
        *,
        draft_revision: int | None = None,
    ) -> ContentIntelligenceState:
        updated = state.model_copy(
            update={
                "revision": state.revision + 1,
                "lifecycle": IntelligenceLifecycle.draft_pending_validation,
                "updated_at": _now(),
                "validation": None,
                "question_answer_map": [],
                "validated_artifact_hash": None,
                "article_version_id": None,
                "draft_revision": draft_revision or state.draft_revision + 1,
                "checksum": "",
            }
        )
        return self._with_checksum(updated)

    def mark_draft_validated(
        self,
        state: ContentIntelligenceState,
        report: IntelligenceValidationReport,
        *,
        draft: V3WriterOutput | None = None,
        article_version_id: UUID | None = None,
    ) -> ContentIntelligenceState:
        lifecycle = (
            IntelligenceLifecycle.draft_validated
            if report.status == "passed"
            else IntelligenceLifecycle.blocked
        )
        artifact_hash = _draft_artifact_hash(draft) if draft is not None else None
        answer_map = [
            QuestionAnswerRecord.model_validate(item)
            for item in report.metrics.get("question_answer_map", [])
        ]
        updated = state.model_copy(
            update={
                "revision": state.revision + 1,
                "lifecycle": lifecycle,
                "updated_at": _now(),
                "validation": report,
                "question_answer_map": answer_map,
                "validated_artifact_hash": artifact_hash if report.status == "passed" else None,
                "article_version_id": article_version_id,
                "draft_revision": max(1, state.draft_revision),
                "checksum": "",
            }
        )
        return self._with_checksum(updated)

    def validate_draft(
        self,
        state: ContentIntelligenceState,
        draft: V3WriterOutput,
    ) -> IntelligenceValidationReport:
        blockers: list[IntelligenceFinding] = []
        warnings: list[IntelligenceFinding] = []
        claims = {item.claim_id: item for item in state.evidence_graph.claims}
        sections = {item.section_id: item for item in state.sections}
        questions = {item.question_id: item for item in state.questions}
        questions_by_section: dict[str, list[EditorialQuestion]] = defaultdict(list)
        for question in state.questions:
            questions_by_section[question.section_id].append(question)
        supported_claims_by_question = {
            question_id: set(claim_ids)
            for question_id, claim_ids in state.evidence_graph.question_claim_map.items()
        }
        factual_by_section: dict[str, int] = defaultdict(int)
        used_claims: set[UUID] = set()
        used_claims_by_section: dict[str, set[UUID]] = defaultdict(set)
        answer_sentences: dict[str, list[tuple[UUID, list[UUID], float, QuestionAnswerStatus]]] = defaultdict(list)

        for block in draft.blocks:
            plan = sections.get(block.section_id)
            if plan is None:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_DRAFT_UNKNOWN_SECTION",
                        message="O rascunho contém um bloco fora do plano canônico.",
                        section_id=block.section_id,
                        recovery_class=RecoveryClass.contract_error,
                        details={"block_id": str(block.block_id)},
                    )
                )
                continue
            allowed = set(plan.allowed_claim_ids)
            prohibited = set(plan.prohibited_claim_ids)
            block_text = " ".join(sentence.text for sentence in block.content_sentences)
            for prohibited_conclusion in plan.prohibited_conclusions:
                phrase = normalized_text(prohibited_conclusion)
                if phrase and (
                    phrase in normalized_text(block_text)
                    or _phrase_coverage(prohibited_conclusion, block_text) >= 0.85
                ):
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_SECTION_PROHIBITED_CONCLUSION_PRESENT",
                            message="O bloco contém uma conclusão proibida especificamente para esta seção.",
                            section_id=block.section_id,
                            recovery_class=RecoveryClass.nonrecoverable,
                            details={
                                "block_id": str(block.block_id),
                                "prohibited_conclusion": prohibited_conclusion,
                            },
                        )
                    )

            for sentence in block.content_sentences:
                factual = sentence.is_factual or is_potentially_factual(
                    sentence.text, block_type=block.type
                )
                if factual:
                    factual_by_section[block.section_id] += 1
                    if not sentence.evidence:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_FACTUAL_SENTENCE_WITHOUT_CLAIM",
                                message="Uma frase verificável não possui claim aprovado.",
                                section_id=block.section_id,
                                recovery_class=RecoveryClass.nonrecoverable,
                                details={
                                    "sentence_id": str(sentence.sentence_id),
                                    "sentence": sentence.text[:500],
                                },
                            )
                        )
                elif sentence.evidence:
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_EDITORIAL_SENTENCE_WITH_EVIDENCE",
                            message="Uma frase classificada como editorial carrega evidência factual.",
                            section_id=block.section_id,
                            recovery_class=RecoveryClass.nonrecoverable,
                            details={"sentence_id": str(sentence.sentence_id)},
                        )
                    )

                valid_sentence_claims: list[UUID] = []
                for reference in sentence.evidence:
                    claim_id = reference.claim_id
                    used_claims.add(claim_id)
                    used_claims_by_section[block.section_id].add(claim_id)
                    claim = claims.get(claim_id)
                    if claim is None:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_DRAFT_UNKNOWN_CLAIM",
                                message="Uma frase usa claim ausente do estado canônico.",
                                section_id=block.section_id,
                                claim_id=claim_id,
                                recovery_class=RecoveryClass.contract_error,
                                details={"sentence_id": str(sentence.sentence_id)},
                            )
                        )
                        continue
                    if claim_id in prohibited or claim.writer_policy == ClaimWriterPolicy.prohibited:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_DRAFT_PROHIBITED_CLAIM",
                                message="Uma frase usa claim cuja evidência é insuficiente para redação.",
                                section_id=block.section_id,
                                claim_id=claim_id,
                                recovery_class=RecoveryClass.nonrecoverable,
                                details={"sentence_id": str(sentence.sentence_id)},
                            )
                        )
                    if claim_id not in allowed:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_DRAFT_CLAIM_NOT_ALLOWED_IN_SECTION",
                                message="A frase usa evidência que não pertence à responsabilidade desta seção.",
                                section_id=block.section_id,
                                claim_id=claim_id,
                                recovery_class=RecoveryClass.contract_error,
                                details={
                                    "claim_section_id": claim.section_id,
                                    "sentence_id": str(sentence.sentence_id),
                                },
                            )
                        )
                    supported, support_score, support_reason = claim_supports_sentence(
                        sentence.text,
                        claim.claim_text,
                        conditions=claim.conditions,
                        limitations=claim.limitations,
                        minimum_score=0.42,
                    )
                    reference.entailment_score = support_score
                    if not supported:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_CLAIM_DOES_NOT_SUPPORT_SENTENCE",
                                message="O claim citado não sustenta deterministicamente a frase gerada.",
                                section_id=block.section_id,
                                claim_id=claim_id,
                                recovery_class=RecoveryClass.nonrecoverable,
                                details={
                                    "support_score": support_score,
                                    "reason": support_reason,
                                    "sentence_id": str(sentence.sentence_id),
                                    "sentence": sentence.text[:500],
                                },
                            )
                        )
                    else:
                        valid_sentence_claims.append(claim_id)
                    sentence_norm = normalized_text(sentence.text)
                    if claim.writer_policy == ClaimWriterPolicy.context_only and not any(
                        marker in sentence_norm for marker in _UNCERTAINTY_MARKERS
                    ):
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_DISPUTED_CLAIM_OVERSTATED",
                                message="Um claim disputado foi usado sem linguagem explícita de incerteza ou divergência.",
                                section_id=block.section_id,
                                claim_id=claim_id,
                                recovery_class=RecoveryClass.nonrecoverable,
                                details={"sentence_id": str(sentence.sentence_id)},
                            )
                        )
                    if claim.writer_policy == ClaimWriterPolicy.conditional:
                        qualifiers = [*claim.conditions, *claim.limitations]
                        if qualifiers and max(
                            (_phrase_coverage(item, sentence.text) for item in qualifiers),
                            default=0.0,
                        ) < 0.25:
                            blockers.append(
                                IntelligenceFinding(
                                    code="INTELLIGENCE_CONDITIONAL_CLAIM_UNQUALIFIED",
                                    message="Um claim condicional foi usado sem explicitar condição ou limitação relevante.",
                                    section_id=block.section_id,
                                    claim_id=claim_id,
                                    recovery_class=RecoveryClass.nonrecoverable,
                                    details={"sentence_id": str(sentence.sentence_id)},
                                )
                            )

                declared_questions = list(dict.fromkeys(sentence.question_ids))
                for question_id in declared_questions:
                    question = questions.get(question_id)
                    if question is None:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_SENTENCE_UNKNOWN_QUESTION",
                                message="Uma frase declara responder a uma pergunta inexistente.",
                                section_id=block.section_id,
                                question_id=question_id,
                                recovery_class=RecoveryClass.contract_error,
                                details={"sentence_id": str(sentence.sentence_id)},
                            )
                        )
                        continue
                    if question.section_id != block.section_id:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_SENTENCE_CROSS_SECTION_QUESTION",
                                message="Uma frase foi vinculada a pergunta de outra seção.",
                                section_id=block.section_id,
                                question_id=question_id,
                                recovery_class=RecoveryClass.contract_error,
                                details={"sentence_id": str(sentence.sentence_id)},
                            )
                        )

                for question in questions_by_section.get(block.section_id, []):
                    declared = question.question_id in declared_questions
                    mapped_claims = supported_claims_by_question.get(
                        question.question_id, set()
                    )
                    answer_claims = [
                        item for item in valid_sentence_claims if item in mapped_claims
                    ]
                    if question.research_required and not answer_claims:
                        if declared:
                            blockers.append(
                                IntelligenceFinding(
                                    code="INTELLIGENCE_ANSWER_WITHOUT_QUESTION_EVIDENCE",
                                    message="A frase declara responder à pergunta de pesquisa, mas não usa claim mapeado a ela.",
                                    section_id=block.section_id,
                                    question_id=question.question_id,
                                    recovery_class=RecoveryClass.nonrecoverable,
                                    details={"sentence_id": str(sentence.sentence_id)},
                                )
                            )
                        continue
                    if not question.research_required and not declared:
                        # Editorial/completion questions require an explicit binding;
                        # they must never be inferred merely from being in the same section.
                        continue
                    alignment = max(
                        _semantic_alignment(question.question, sentence.text),
                        (
                            _semantic_alignment(
                                question.completion_signal, sentence.text
                            )
                            if question.completion_signal
                            else 0.0
                        ),
                    )
                    minimum_alignment = 0.22 if question.research_required else 0.18
                    if alignment < minimum_alignment:
                        if declared:
                            blockers.append(
                                IntelligenceFinding(
                                    code="INTELLIGENCE_ANSWER_SEMANTICALLY_INSUFFICIENT",
                                    message="A frase foi vinculada à pergunta, mas não entrega resposta semanticamente identificável.",
                                    section_id=block.section_id,
                                    question_id=question.question_id,
                                    recovery_class=RecoveryClass.nonrecoverable,
                                    details={
                                        "sentence_id": str(sentence.sentence_id),
                                        "alignment_score": alignment,
                                        "minimum_alignment": minimum_alignment,
                                    },
                                )
                            )
                        continue
                    contextual = any(
                        claims[item].writer_policy == ClaimWriterPolicy.context_only
                        for item in answer_claims
                    )
                    if contextual:
                        answer_status = QuestionAnswerStatus.contextual
                    elif alignment >= 0.34 or (
                        not question.research_required and alignment >= 0.25
                    ):
                        answer_status = QuestionAnswerStatus.direct
                    else:
                        answer_status = QuestionAnswerStatus.partial
                    if sentence.answer_status and sentence.answer_status != answer_status.value:
                        warnings.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_WRITER_ANSWER_STATUS_OVERRIDDEN",
                                message="O status de resposta declarado pelo Writer foi recalculado pelo validador.",
                                section_id=block.section_id,
                                question_id=question.question_id,
                                details={
                                    "sentence_id": str(sentence.sentence_id),
                                    "declared": sentence.answer_status,
                                    "calculated": answer_status.value,
                                },
                            )
                        )
                    answer_sentences[question.question_id].append(
                        (sentence.sentence_id, answer_claims, alignment, answer_status)
                    )

        for section in state.sections:
            if section.research_required and factual_by_section.get(section.section_id, 0) == 0:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_RESEARCH_SECTION_WITHOUT_FACTUAL_CONTENT",
                        message="Uma seção baseada em pesquisa não contém nenhuma frase factual rastreável.",
                        section_id=section.section_id,
                        recovery_class=RecoveryClass.nonrecoverable,
                    )
                )

        question_answer_map: list[QuestionAnswerRecord] = []
        answered_critical = 0
        for question in state.questions:
            matches = answer_sentences.get(question.question_id, [])
            sentence_ids = list(dict.fromkeys(item[0] for item in matches))
            claim_ids_for_answer = list(
                dict.fromkeys(claim_id for _sid, claim_ids, _score, _status in matches for claim_id in claim_ids)
            )
            statuses = [item[3] for item in matches]
            if QuestionAnswerStatus.direct in statuses:
                answer_status = QuestionAnswerStatus.direct
            elif QuestionAnswerStatus.partial in statuses:
                answer_status = QuestionAnswerStatus.partial
            elif QuestionAnswerStatus.contextual in statuses:
                answer_status = QuestionAnswerStatus.contextual
            else:
                answer_status = QuestionAnswerStatus.unanswered
            combined_answer = " ".join(
                sentence.text
                for block in draft.blocks
                for sentence in block.content_sentences
                if sentence.sentence_id in set(sentence_ids)
            )
            completion_score = (
                _semantic_alignment(question.completion_signal, combined_answer)
                if question.completion_signal and combined_answer
                else max((item[2] for item in matches), default=0.0)
            )
            record = QuestionAnswerRecord(
                question_id=question.question_id,
                sentence_ids=sentence_ids,
                claim_ids=claim_ids_for_answer,
                answer_status=answer_status,
                completion_signal_score=completion_score,
            )
            question_answer_map.append(record)
            if question.critical:
                if answer_status != QuestionAnswerStatus.direct:
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_CRITICAL_QUESTION_NOT_ANSWERED",
                            message="O rascunho não entrega resposta direta e rastreável para uma pergunta crítica.",
                            section_id=question.section_id,
                            question_id=question.question_id,
                            recovery_class=RecoveryClass.nonrecoverable,
                            details={
                                "answer_status": answer_status.value,
                                "completion_signal": question.completion_signal,
                                "completion_signal_score": completion_score,
                            },
                        )
                    )
                else:
                    answered_critical += 1

        full_text = " ".join(
            sentence.text for block in draft.blocks for sentence in block.content_sentences
        )
        full_normalized = normalized_text(full_text)
        for prohibited_claim in state.prohibited_claims:
            phrase = normalized_text(prohibited_claim)
            if phrase and (
                phrase in full_normalized
                or _phrase_coverage(prohibited_claim, full_text) >= 0.85
            ):
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_PROHIBITED_CONCLUSION_PRESENT",
                        message="O rascunho contém uma conclusão proibida pelo briefing ou contrato.",
                        recovery_class=RecoveryClass.nonrecoverable,
                        details={"prohibited_claim": prohibited_claim},
                    )
                )

        conflict_by_id = {item.conflict_id: item for item in state.evidence_graph.conflicts}
        for section in state.sections:
            section_text = " ".join(
                sentence.text
                for block in draft.blocks
                if block.section_id == section.section_id
                for sentence in block.content_sentences
            )
            used_in_section = used_claims_by_section.get(section.section_id, set())
            for conflict_id in section.conflict_ids:
                conflict = conflict_by_id[conflict_id]
                if not (used_in_section & set(conflict.claim_ids)):
                    continue
                normalized_section = normalized_text(section_text)
                if not any(marker in normalized_section for marker in _UNCERTAINTY_MARKERS):
                    blockers.append(
                        IntelligenceFinding(
                            code="INTELLIGENCE_CONFLICT_LANGUAGE_MISSING",
                            message="Uma evidência conflitante foi usada sem linguagem explícita de divergência ou limitação.",
                            section_id=section.section_id,
                            recovery_class=RecoveryClass.nonrecoverable,
                            details={"conflict_id": conflict_id},
                        )
                    )
                for conclusion in conflict.prohibited_conclusions:
                    if _phrase_coverage(conclusion, section_text) >= 0.85:
                        blockers.append(
                            IntelligenceFinding(
                                code="INTELLIGENCE_CONFLICT_PROHIBITED_CONCLUSION",
                                message="A seção contém uma conclusão proibida pelo grafo de conflito.",
                                section_id=section.section_id,
                                recovery_class=RecoveryClass.nonrecoverable,
                                details={"conflict_id": conflict_id, "conclusion": conclusion},
                            )
                        )

        usable_claims = {
            item.claim_id
            for item in state.evidence_graph.claims
            if item.writer_policy != ClaimWriterPolicy.prohibited
        }
        evidence_utilization = len(used_claims & usable_claims) / max(1, len(usable_claims))
        section_coverage = len(set(draft.covered_section_ids) & set(sections)) / max(1, len(sections))
        critical_count = sum(item.critical for item in state.questions)
        answer_coverage = answered_critical / max(1, critical_count)
        score = max(
            0.0,
            min(
                1.0,
                (section_coverage * 0.35)
                + (answer_coverage * 0.45)
                + (min(1.0, evidence_utilization * 2) * 0.20),
            ),
        )
        if blockers:
            score = min(score, 0.79)
        return IntelligenceValidationReport(
            status="blocked" if blockers else "passed",
            phase="draft",
            score=round(score, 4),
            blockers=blockers,
            warnings=warnings,
            metrics={
                "section_coverage": round(section_coverage, 4),
                "critical_answer_coverage": round(answer_coverage, 4),
                "evidence_utilization": round(evidence_utilization, 4),
                "used_claim_count": len(used_claims),
                "usable_claim_count": len(usable_claims),
                "factual_sentence_count": sum(factual_by_section.values()),
                "draft_artifact_hash": _draft_artifact_hash(draft),
                "question_answer_map": [item.model_dump(mode="json") for item in question_answer_map],
            },
        )

    def augment_research_plan(
        self,
        state: ContentIntelligenceState,
        plan: V3ResearchPlan,
    ) -> V3ResearchPlan:
        """Reserve executable query slots for the canonical question map.

        V3.6 appended intelligence queries after up to six legacy queries, which
        silently discarded them. V3.6.1 always reserves at least two slots when
        a research task has critical canonical questions.
        """

        questions_by_section: dict[str, list[EditorialQuestion]] = defaultdict(list)
        for question in state.questions:
            if question.research_required:
                questions_by_section[question.section_id].append(question)

        updated_tasks = []
        for task in plan.tasks:
            questions = sorted(
                questions_by_section.get(task.knowledge_node_id, []),
                key=lambda item: (not item.critical, item.kind.value, item.question_id),
            )
            selected = [
                item for item in questions if item.kind != EditorialQuestionKind.completion
            ][:3]
            if not selected:
                selected = questions[:2]
            canonical_questions = [item.question for item in selected]
            intelligence_queries = list(
                dict.fromkeys(
                    " ".join(f"{state.topic} {question}".split())[:280].rstrip()
                    for question in canonical_questions[:3]
                )
            )
            legacy_queries = list(dict.fromkeys(task.queries))
            reserved = min(3, max(2, len(intelligence_queries))) if intelligence_queries else 0
            legacy_limit = max(0, 6 - reserved)
            queries = list(
                dict.fromkeys(
                    [*legacy_queries[:legacy_limit], *intelligence_queries]
                )
            )[:6]
            # Fill unused slots with remaining legacy variants without displacing
            # the reserved canonical questions.
            if len(queries) < 6:
                for query in legacy_queries[legacy_limit:]:
                    if query not in queries:
                        queries.append(query)
                    if len(queries) == 6:
                        break
            goal_suffix = (
                " Perguntas editoriais canônicas: " + "; ".join(canonical_questions)
                if canonical_questions
                else ""
            )
            rationale_suffix = (
                " O resultado será aceito somente se responder ao mapa de perguntas "
                "do Motor de Inteligência Editorial; slots de consulta foram reservados "
                "para as perguntas críticas."
            )
            updated_tasks.append(
                task.model_copy(
                    update={
                        "queries": queries,
                        "research_goal": (task.research_goal + goal_suffix)[:1500],
                        "rationale": (task.rationale + rationale_suffix)[:1000],
                    }
                )
            )
        return plan.model_copy(
            update={
                "rationale": (
                    plan.rationale
                    + " O plano foi vinculado ao mapa canônico de perguntas editoriais "
                    "com capacidade reservada para consultas críticas."
                )[:3000],
                "tasks": updated_tasks,
            }
        )

    def writer_payload(self, state: ContentIntelligenceState) -> dict:
        claims = {item.claim_id: item for item in state.evidence_graph.claims}
        coverage_by_question: dict[str, list[QuestionClaimCoverage]] = defaultdict(list)
        for edge in state.evidence_graph.question_coverage:
            if edge.status in {
                QuestionCoverageStatus.semantically_supported,
                QuestionCoverageStatus.human_overridden,
            }:
                coverage_by_question[edge.question_id].append(edge)
        claim_policies = [
            {
                "claim_id": str(item.claim_id),
                "support_group": item.support_group,
                "section_id": item.section_id,
                "writer_policy": item.writer_policy.value,
                "conclusion_status": item.conclusion_status.value,
                "conditions": item.conditions,
                "limitations": item.limitations,
                "source_ids": [str(value) for value in item.source_ids],
                "source_fact_ids": [str(value) for value in item.source_fact_ids],
                "integrity_issues": item.integrity_issues,
            }
            for item in state.evidence_graph.claims
        ]
        question_evidence_plan = []
        for question in state.questions:
            edges = sorted(
                coverage_by_question.get(question.question_id, []),
                key=lambda item: item.alignment_score,
                reverse=True,
            )
            question_evidence_plan.append(
                {
                    "question_id": question.question_id,
                    "section_id": question.section_id,
                    "kind": question.kind.value,
                    "critical": question.critical,
                    "question": question.question,
                    "origin": question.origin,
                    "rationale": question.rationale,
                    "completion_signal": question.completion_signal,
                    "answer_contract": {
                        "sentence_id_required": True,
                        "question_id_binding_required": True,
                        "allowed_answer_status": ["direct", "partial", "contextual"],
                        "critical_requires_direct": question.critical,
                    },
                    "evidence": [
                        {
                            "claim_id": str(edge.claim_id),
                            "claim_text": claims[edge.claim_id].claim_text,
                            "alignment_score": edge.alignment_score,
                            "writer_policy": claims[edge.claim_id].writer_policy.value,
                            "conditions": claims[edge.claim_id].conditions,
                            "limitations": claims[edge.claim_id].limitations,
                            "source_ids": [str(value) for value in edge.source_ids],
                        }
                        for edge in edges
                        if edge.claim_id in claims
                    ],
                }
            )
        return {
            "intelligence_version": state.intelligence_version,
            "checksum": state.checksum,
            "lifecycle": state.lifecycle.value,
            "content_objective": state.content_objective,
            "search_intent": state.search_intent,
            "reader_profile": state.reader_profile,
            "generation_constraints": state.generation_constraints,
            "prohibited_claims": state.prohibited_claims,
            "questions": [item.model_dump(mode="json") for item in state.questions],
            "section_plans": [item.model_dump(mode="json") for item in state.sections],
            "question_evidence_plan": question_evidence_plan,
            "claim_policy_catalog": claim_policies,
            "conflicts": [item.model_dump(mode="json") for item in state.evidence_graph.conflicts],
        }

    def summary(self, state: ContentIntelligenceState) -> dict:
        validation = state.validation
        return {
            "version": state.intelligence_version,
            "revision": state.revision,
            "lifecycle": state.lifecycle.value,
            "checksum": state.checksum,
            "search_intent": state.search_intent,
            "section_count": len(state.sections),
            "question_count": len(state.questions),
            "source_count": len(state.evidence_graph.sources),
            "claim_count": len(state.evidence_graph.claims),
            "conflict_count": len(state.evidence_graph.conflicts),
            "unresolved_gap_count": len(state.unresolved_gap_ids),
            "validation_status": validation.status if validation else None,
            "validation_score": validation.score if validation else None,
            "validated_artifact_hash": state.validated_artifact_hash,
            "article_version_id": (
                str(state.article_version_id) if state.article_version_id else None
            ),
            "draft_revision": state.draft_revision,
        }

    def _planning_validation(
        self,
        questions: list[EditorialQuestion],
        sections: list[SectionIntelligencePlan],
    ) -> IntelligenceValidationReport:
        blockers: list[IntelligenceFinding] = []
        section_ids = {item.section_id for item in sections}
        for section in sections:
            if not section.question_ids:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_PLANNING_SECTION_EMPTY",
                        message="A seção foi criada sem pergunta editorial.",
                        section_id=section.section_id,
                    )
                )
            missing_dependencies = set(section.depends_on) - section_ids
            if missing_dependencies:
                blockers.append(
                    IntelligenceFinding(
                        code="INTELLIGENCE_PLANNING_DEPENDENCY_UNKNOWN",
                        message="A seção depende de um nó que não está ativo.",
                        section_id=section.section_id,
                        details={"missing_dependencies": sorted(missing_dependencies)},
                    )
                )
        return IntelligenceValidationReport(
            status="blocked" if blockers else "passed",
            phase="planning",
            score=0.0 if blockers else 1.0,
            blockers=blockers,
            metrics={"section_count": len(sections), "question_count": len(questions)},
        )

    def _with_checksum(self, state: ContentIntelligenceState) -> ContentIntelligenceState:
        payload = state.model_dump(mode="json", exclude={"checksum"})
        checksum = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        return state.model_copy(update={"checksum": checksum})
