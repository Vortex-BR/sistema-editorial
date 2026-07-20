"""Deterministic, knowledge-first research planning for Editorial V3.

The planner does not create a bag of unrelated questions.  It derives a bounded
set of research tasks from the ordered knowledge graph, the reader decisions,
and the evidence roles that each node must satisfy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.editorial_v3 import (
    ContentKnowledgeContract,
    EditorialContentTypeV3,
    EvidenceRole,
)
from app.schemas.editorial_v3_runtime import ResearchTask, V3ResearchPlan


_ROLE_TERMS: dict[EvidenceRole, str] = {
    EvidenceRole.definition: "definição e terminologia",
    EvidenceRole.mechanism: "mecanismo e funcionamento",
    EvidenceRole.prerequisite: "pré-requisitos e condições",
    EvidenceRole.material: "materiais e preparação",
    EvidenceRole.environmental_condition: "condições ambientais e limites",
    EvidenceRole.action: "procedimento passo a passo",
    EvidenceRole.sequence: "sequência e ordem das etapas",
    EvidenceRole.decision_criterion: "critérios de decisão",
    EvidenceRole.success_signal: "sinais de progresso",
    EvidenceRole.failure_signal: "sinais de falha",
    EvidenceRole.common_error: "erros comuns e causas",
    EvidenceRole.correction: "correções e solução de problemas",
    EvidenceRole.risk: "riscos e segurança",
    EvidenceRole.exception: "exceções e variações",
    EvidenceRole.limitation: "limitações e desvantagens",
    EvidenceRole.comparison: "comparação de alternativas",
    EvidenceRole.transition: "momento de transição",
    EvidenceRole.final_outcome: "resultado final observável",
    EvidenceRole.external_reference: "protocolo técnico detalhado",
}

_CONTENT_VOCABULARY: dict[EditorialContentTypeV3, dict[str, str]] = {
    EditorialContentTypeV3.procedural_decision_guide: {
        "artifact": "guia de decisão procedural",
        "technical": "protocolo técnico procedimento",
        "discovery": "abordagens métodos comparáveis",
    },
    EditorialContentTypeV3.procedural_how_to: {
        "artifact": "guia procedural de caminho único",
        "technical": "protocolo técnico procedimento passo a passo",
        "discovery": "pré-requisitos sequência sinais correções",
    },
    EditorialContentTypeV3.explanatory_guide: {
        "artifact": "explicação estruturada",
        "technical": "revisão explicação mecanismo",
        "discovery": "conceitos mecanismos implicações",
    },
    EditorialContentTypeV3.comparison: {
        "artifact": "comparação editorial",
        "technical": "estudo comparativo critérios limitações",
        "discovery": "alternativas diferenças critérios",
    },
    EditorialContentTypeV3.troubleshooting: {
        "artifact": "diagnóstico e correção",
        "technical": "manual técnico causas diagnóstico correção",
        "discovery": "sintomas causas verificações soluções",
    },
    EditorialContentTypeV3.commercial_education: {
        "artifact": "conteúdo educacional comercial",
        "technical": "evidência independente critérios limitações",
        "discovery": "necessidades opções critérios de escolha",
    },
}

_SOURCE_ROLES_BY_EVIDENCE: dict[EvidenceRole, list[str]] = {
    EvidenceRole.definition: ["scientific_review", "institutional", "independent_editorial"],
    EvidenceRole.mechanism: ["scientific_review", "scientific_primary", "institutional"],
    EvidenceRole.risk: ["scientific_review", "institutional", "scientific_primary"],
    EvidenceRole.limitation: ["scientific_review", "institutional", "independent_editorial"],
    EvidenceRole.comparison: ["scientific_review", "independent_editorial", "technical_procedural"],
    EvidenceRole.action: ["technical_procedural", "institutional", "specialist_practical"],
    EvidenceRole.sequence: ["technical_procedural", "institutional", "specialist_practical"],
    EvidenceRole.material: ["technical_procedural", "institutional", "specialist_practical"],
    EvidenceRole.correction: ["technical_procedural", "institutional", "specialist_practical"],
    EvidenceRole.common_error: ["technical_procedural", "institutional", "specialist_practical"],
    EvidenceRole.external_reference: ["technical_procedural", "institutional", "independent_editorial"],
}


def _source_roles_for(evidence_role: EvidenceRole) -> list[str]:
    return list(
        _SOURCE_ROLES_BY_EVIDENCE.get(
            evidence_role,
            ["institutional", "technical_procedural", "independent_editorial"],
        )
    )



@dataclass(frozen=True)
class ScheduledResearchQuery:
    task_id: str
    knowledge_node_id: str
    query: str
    query_index: int
    critical: bool


def schedule_research_queries(
    tasks: list[ResearchTask],
    *,
    limit: int,
    executed_queries_by_task: dict[str, list[str]] | None = None,
) -> list[ScheduledResearchQuery]:
    """Allocate queries in rounds so one node cannot consume the whole budget.

    The first round gives every task one query whenever the budget permits.
    Subsequent rounds deepen all tasks evenly.  Already executed query strings are
    skipped, which lets the same scheduler drive the supplemental pass.
    """

    if limit <= 0 or not tasks:
        return []
    executed = {
        task_id: set(values)
        for task_id, values in (executed_queries_by_task or {}).items()
    }
    ordered = list(tasks)
    if limit < len(ordered):
        ordered = sorted(
            enumerate(ordered),
            key=lambda pair: (not pair[1].critical, pair[0]),
        )
        ordered = [task for _, task in ordered]

    queues: dict[str, list[tuple[int, str]]] = {}
    for task in ordered:
        seen = executed.get(task.task_id, set())
        queues[task.task_id] = [
            (index, query)
            for index, query in enumerate(task.queries)
            if query not in seen
        ]

    scheduled: list[ScheduledResearchQuery] = []
    while len(scheduled) < limit:
        progressed = False
        for task in ordered:
            queue = queues[task.task_id]
            if not queue:
                continue
            index, query = queue.pop(0)
            scheduled.append(
                ScheduledResearchQuery(
                    task_id=task.task_id,
                    knowledge_node_id=task.knowledge_node_id,
                    query=query,
                    query_index=index,
                    critical=task.critical,
                )
            )
            progressed = True
            if len(scheduled) >= limit:
                break
        if not progressed:
            break
    return scheduled

def _slug(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value[:80] or "task"


def _search_subject(contract: ContentKnowledgeContract) -> str:
    """Return a bounded, recall-friendly subject for provider queries."""

    metadata_subject = str(contract.metadata.get("search_subject") or "")
    subject = " ".join((metadata_subject or contract.topic).split()).strip()
    return subject[:240].rstrip()


def _short_phrase(value: str, *, limit: int = 110) -> str:
    value = " ".join(str(value or "").split()).strip(" -–—;:,.|")
    if not value:
        return ""
    value = re.split(r"[.!?\n]", value, maxsplit=1)[0].strip()
    return value[:limit].rstrip()


def _query(*parts: str, limit: int = 280) -> str:
    query = " ".join(part.strip() for part in parts if part.strip())
    query = re.sub(r"\s+", " ", query).strip()
    return query[:limit].rstrip()


def build_targeted_gap_queries(
    contract: ContentKnowledgeContract,
    task: ResearchTask,
    *,
    limit: int = 3,
    reason_codes: list[str] | tuple[str, ...] = (),
) -> list[str]:
    """Create bounded recovery queries for an under-covered knowledge node."""

    node = next(
        (item for item in contract.nodes if item.node_id == task.knowledge_node_id),
        None,
    )
    subject = _search_subject(contract)
    if node is None:
        return [_query(subject, _ROLE_TERMS[task.evidence_role], "fonte técnica")]
    focus = _short_phrase(
        next(iter(node.required_knowledge), "") or node.central_question,
        limit=100,
    )
    roles = list(node.required_evidence_roles) or [task.evidence_role]
    vocabulary = _CONTENT_VOCABULARY[contract.content_type]
    reason_terms = {
        "no_readable_source_for_task": "documento completo html pdf",
        "all_task_sources_rejected_or_comparison_only": "fonte independente institucional",
        "independent_source_diversity_insufficient": "fonte alternativa independente",
        "authoritative_source_missing": "universidade governo revisão científica",
        "required_source_roles_missing": "protocolo técnico fonte institucional",
    }
    recovery_hint = " ".join(
        dict.fromkeys(reason_terms[code] for code in reason_codes if code in reason_terms)
    )
    candidates = [
        _query(subject, focus, _ROLE_TERMS[roles[0]], recovery_hint or "evidência"),
        _query(subject, focus, vocabulary["technical"]),
        _query(subject, _ROLE_TERMS[roles[-1]], "limites sinais observáveis fonte independente"),
    ]
    return list(dict.fromkeys(item for item in candidates if item))[:limit]


class V3ResearchPlanningService:
    """Build an executable plan while preserving the contract's causal order."""

    def build(
        self,
        contract: ContentKnowledgeContract,
        *,
        max_tasks: int = 22,
        maximum_search_queries: int = 36,
    ) -> V3ResearchPlan:
        tasks: list[ResearchTask] = []
        search_subject = _search_subject(contract)
        vocabulary = _CONTENT_VOCABULARY[contract.content_type]
        active_ids = set(contract.metadata.get("active_node_ids") or [
            item.node_id for item in contract.nodes
        ])
        for node in contract.nodes:
            if not node.research_required or node.node_id not in active_ids:
                continue
            roles = list(node.required_evidence_roles) or [EvidenceRole.definition]
            # A single task may ask the researcher to extract several roles.  We use
            # the highest-value role as its routing label and carry the full role set
            # in the research goal/rationale.  This controls cost without flattening
            # the knowledge model into one question per fact type.
            primary_role = roles[0]
            knowledge = "; ".join(node.required_knowledge[:8])
            decisions = "; ".join(node.required_decisions[:5]) or "nenhuma decisão específica"
            goal = (
                f"Resolver o nó '{node.node_id}' do {vocabulary['artifact']} sobre {contract.topic}. "
                f"A pesquisa deve sustentar: {knowledge}. Também deve permitir: {decisions}. "
                f"Extrair evidências para os papéis: {', '.join(item.value for item in roles)}."
            )
            focus = _short_phrase(
                next(iter(node.required_knowledge), "") or node.central_question,
                limit=100,
            )
            queries = [
                _query(search_subject, _ROLE_TERMS[primary_role], "evidência revisão"),
                _query(search_subject, focus, vocabulary["technical"]),
            ]
            if len(roles) > 1:
                queries.append(_query(search_subject, _ROLE_TERMS[roles[1]], focus))
            if node.kind.value in {"method_inventory", "method_execution", "method_comparison"}:
                queries.append(_query(search_subject, "comparação de abordagens e procedimentos"))
                for method_label in contract.required_method_labels:
                    queries.append(
                        _query(search_subject, f'"{method_label}"', "procedimento materiais sinais erros")
                    )
            if node.kind.value in {"troubleshooting", "progress_confirmation", "post_transition_monitoring"}:
                queries.append(_query(search_subject, "problemas sinais e correções"))
            task = ResearchTask(
                task_id=f"task_{node.sequence:02d}_{_slug(node.node_id)}",
                knowledge_node_id=node.node_id,
                evidence_role=primary_role,
                research_goal=goal,
                queries=list(dict.fromkeys(queries))[:6],
                required_source_roles=_source_roles_for(primary_role),
                minimum_independent_sources=(2 if node.kind.value not in {"external_references"} else 1),
                critical=node.importance.value == "core",
                rationale=(
                    f"Este nó leva o leitor de '{node.reader_state_before}' para "
                    f"'{node.reader_state_after}'. Critérios de conclusão: "
                    + "; ".join(node.completion_criteria[:8])
                ),
            )
            tasks.append(task)

        if len(tasks) > max_tasks:
            # Preserve every critical stage and then the earliest supporting stages.
            critical = [item for item in tasks if item.critical]
            supporting = [item for item in tasks if not item.critical]
            tasks = (critical + supporting)[:max_tasks]

        if contract.requires_method_comparison:
            discovery_queries = [
                _query(search_subject, vocabulary["discovery"]),
                _query(search_subject, vocabulary["technical"]),
                _query(search_subject, "fontes independentes critérios limitações"),
            ]
            discovery_queries.extend(
                _query(search_subject, f'"{method_label}"', "guia técnico detalhado")
                for method_label in contract.required_method_labels
            )
        else:
            discovery_queries = [
                _query(search_subject, vocabulary["discovery"]),
                _query(search_subject, vocabulary["technical"]),
            ]
        terminology_queries = [
            _query(search_subject, "glossário definição"),
            _query(search_subject, "encyclopedia wiki references"),
        ]
        total_available = sum(len(item.queries) for item in tasks)
        return V3ResearchPlan(
            rationale=(
                "Plano derivado do grafo de conhecimento. As consultas são agrupadas "
                "por função editorial e não pela posição dos resultados de busca. "
                "Fontes comerciais podem apenas levantar hipóteses para comparação."
            ),
            tasks=tasks,
            method_discovery_queries=list(dict.fromkeys(discovery_queries))[:12],
            terminology_queries=terminology_queries,
            stop_conditions=(
                [
                    "todos os nós críticos possuem ao menos duas fontes independentes elegíveis",
                    "cada abordagem possui ação, observação, critério de avanço e referência externa elegível",
                    "lacunas essenciais foram resolvidas ou convertidas em conclusão condicional explícita",
                    "o limite de consultas ou orçamento da execução foi atingido",
                ]
                if contract.requires_method_comparison
                else [
                    "todos os nós críticos possuem evidência independente suficiente",
                    "as dependências e critérios de conclusão do contrato podem ser respondidos",
                    "lacunas essenciais foram resolvidas ou convertidas em conclusão condicional explícita",
                    "o limite de consultas ou orçamento da execução foi atingido",
                ]
            ),
            maximum_search_queries=min(maximum_search_queries, max(5, total_available)),
        )
