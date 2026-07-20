"""Deterministic knowledge-contract construction for Editorial V3.

The builder establishes the editorial order before any web query or LLM call.
It does not assert subject-matter facts.  It defines what the research must prove,
which decisions the reader must be able to make, and where branches converge.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.editorial_hierarchy import (
    EditorialArchitectureType,
    NodeApplicability,
    NodeImportance,
    UniversalNodeRole,
)
from app.schemas.editorial_v3 import (
    ApproachDimension,
    ContentKnowledgeContract,
    EditorialContentTypeV3,
    EvidenceRole,
    KnowledgeEdgeContract,
    KnowledgeEdgeRelation,
    KnowledgeNodeContract,
    KnowledgeNodeKind,
    ResearchSourcePolicyContract,
)
from app.services.editorial_hierarchy import UniversalEditorialHierarchyBuilder
from app.services.editorial_v3.research_intent import (
    build_research_intent_payload,
    normalize_locale,
)


_PROCEDURAL_HIERARCHY = {
    "subject_foundation": (UniversalNodeRole.foundation, NodeImportance.core, 1.0),
    "method_inventory": (UniversalNodeRole.landscape, NodeImportance.core, 1.2),
    "process_requirements": (UniversalNodeRole.requirements, NodeImportance.core, 1.2),
    "method_comparison": (UniversalNodeRole.comparison, NodeImportance.core, 1.2),
    "method_selection": (UniversalNodeRole.decision_criteria, NodeImportance.core, 1.0),
    "method_execution": (UniversalNodeRole.execution, NodeImportance.core, 2.0),
    "progress_confirmation": (UniversalNodeRole.progress_signal, NodeImportance.core, 1.0),
    "transition_decision": (UniversalNodeRole.transition, NodeImportance.core, 1.0),
    "transition_execution": (UniversalNodeRole.transition, NodeImportance.core, 1.0),
    "post_transition_monitoring": (UniversalNodeRole.self_diagnosis, NodeImportance.core, 1.0),
    "final_outcome_confirmation": (UniversalNodeRole.outcome, NodeImportance.core, 0.8),
    "troubleshooting": (UniversalNodeRole.problems, NodeImportance.core, 1.1),
    "external_references": (UniversalNodeRole.external_references, NodeImportance.supporting, 0.5),
}

_GENERIC_KIND_BY_ROLE = {
    UniversalNodeRole.foundation: KnowledgeNodeKind.subject_foundation,
    UniversalNodeRole.landscape: KnowledgeNodeKind.explanation,
    UniversalNodeRole.requirements: KnowledgeNodeKind.explanation,
    UniversalNodeRole.decision_criteria: KnowledgeNodeKind.explanation,
    UniversalNodeRole.preparation: KnowledgeNodeKind.explanation,
    UniversalNodeRole.execution: KnowledgeNodeKind.explanation,
    UniversalNodeRole.progress_signal: KnowledgeNodeKind.explanation,
    UniversalNodeRole.transition: KnowledgeNodeKind.explanation,
    UniversalNodeRole.outcome: KnowledgeNodeKind.explanation,
    UniversalNodeRole.problems: KnowledgeNodeKind.troubleshooting,
    UniversalNodeRole.self_diagnosis: KnowledgeNodeKind.explanation,
    UniversalNodeRole.mechanism: KnowledgeNodeKind.explanation,
    UniversalNodeRole.implications: KnowledgeNodeKind.explanation,
    UniversalNodeRole.misconceptions: KnowledgeNodeKind.explanation,
    UniversalNodeRole.options: KnowledgeNodeKind.explanation,
    UniversalNodeRole.comparison: KnowledgeNodeKind.explanation,
    UniversalNodeRole.recommendation_logic: KnowledgeNodeKind.explanation,
    UniversalNodeRole.symptoms: KnowledgeNodeKind.troubleshooting,
    UniversalNodeRole.causes: KnowledgeNodeKind.troubleshooting,
    UniversalNodeRole.corrections: KnowledgeNodeKind.troubleshooting,
    UniversalNodeRole.verification: KnowledgeNodeKind.troubleshooting,
    UniversalNodeRole.prevention: KnowledgeNodeKind.troubleshooting,
    UniversalNodeRole.problem_context: KnowledgeNodeKind.explanation,
    UniversalNodeRole.solution_fit: KnowledgeNodeKind.explanation,
    UniversalNodeRole.objections: KnowledgeNodeKind.explanation,
    UniversalNodeRole.external_references: KnowledgeNodeKind.external_references,
    UniversalNodeRole.offer_bridge: KnowledgeNodeKind.explanation,
}

_EVIDENCE_BY_ROLE = {
    UniversalNodeRole.foundation: [EvidenceRole.definition],
    UniversalNodeRole.landscape: [EvidenceRole.definition, EvidenceRole.comparison],
    UniversalNodeRole.requirements: [EvidenceRole.prerequisite, EvidenceRole.limitation],
    UniversalNodeRole.decision_criteria: [EvidenceRole.decision_criterion, EvidenceRole.comparison],
    UniversalNodeRole.preparation: [EvidenceRole.prerequisite, EvidenceRole.action],
    UniversalNodeRole.execution: [EvidenceRole.action, EvidenceRole.sequence],
    UniversalNodeRole.progress_signal: [EvidenceRole.success_signal, EvidenceRole.decision_criterion],
    UniversalNodeRole.transition: [EvidenceRole.transition, EvidenceRole.decision_criterion],
    UniversalNodeRole.outcome: [EvidenceRole.final_outcome, EvidenceRole.success_signal],
    UniversalNodeRole.problems: [EvidenceRole.common_error, EvidenceRole.correction],
    UniversalNodeRole.self_diagnosis: [EvidenceRole.success_signal, EvidenceRole.failure_signal],
    UniversalNodeRole.mechanism: [EvidenceRole.mechanism, EvidenceRole.limitation],
    UniversalNodeRole.implications: [EvidenceRole.decision_criterion, EvidenceRole.limitation],
    UniversalNodeRole.misconceptions: [EvidenceRole.definition, EvidenceRole.limitation],
    UniversalNodeRole.options: [EvidenceRole.definition, EvidenceRole.comparison],
    UniversalNodeRole.comparison: [EvidenceRole.comparison, EvidenceRole.limitation],
    UniversalNodeRole.recommendation_logic: [EvidenceRole.decision_criterion, EvidenceRole.comparison],
    UniversalNodeRole.symptoms: [EvidenceRole.failure_signal, EvidenceRole.definition],
    UniversalNodeRole.causes: [EvidenceRole.mechanism, EvidenceRole.risk],
    UniversalNodeRole.corrections: [EvidenceRole.correction, EvidenceRole.action],
    UniversalNodeRole.verification: [EvidenceRole.success_signal, EvidenceRole.failure_signal],
    UniversalNodeRole.prevention: [EvidenceRole.risk, EvidenceRole.correction],
    UniversalNodeRole.problem_context: [EvidenceRole.definition, EvidenceRole.risk],
    UniversalNodeRole.solution_fit: [EvidenceRole.comparison, EvidenceRole.limitation],
    UniversalNodeRole.objections: [EvidenceRole.limitation, EvidenceRole.exception],
    UniversalNodeRole.external_references: [EvidenceRole.external_reference],
    UniversalNodeRole.offer_bridge: [],
}


_SUBJECT_STOPWORDS = {
    "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
    "e", "em", "guia", "o", "os", "para", "por", "sobre", "um", "uma",
    "the", "and", "for", "with", "guide", "how", "to",
}


def _normalized_phrase(value: Any, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split()).strip(" -–—;:,.|")
    if not text:
        return ""
    text = re.split(r"[.!?\n]", text, maxsplit=1)[0].strip()
    return text[:limit].rstrip()


def _subject_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-zA-ZÀ-ÿ0-9]{3,}", value.casefold())
        if token not in _SUBJECT_STOPWORDS
    }


def _build_search_subject(topic: str, brief: dict[str, Any]) -> str:
    """Build one natural factual subject, separate from the SEO keyword.

    The previous implementation joined fragments with semicolons, producing a
    keyword bag rather than a searchable object.  V3.5 chooses the richest
    natural phrase and only appends missing method/entity context.
    """

    explicit = _normalized_phrase(brief.get("research_subject"), limit=360)
    if explicit:
        return explicit

    primary = _normalized_phrase(brief.get("primary_keyword"), limit=220)
    topic_phrase = _normalized_phrase(topic, limit=260)
    objective = _normalized_phrase(brief.get("content_objective"), limit=260)
    context = _normalized_phrase(brief.get("additional_context"), limit=220)
    factual_candidates = [item for item in (topic_phrase, primary) if item]
    fallback_candidates = [item for item in (objective, context) if item]
    candidates = factual_candidates or fallback_candidates
    if not candidates:
        return "assunto editorial não informado"

    # Topic and primary keyword describe the factual object. Objectives and
    # additional context are only fallbacks; a long marketing objective must not
    # replace the entity or process that will actually be researched.
    base = max(
        enumerate(candidates),
        key=lambda pair: (len(_subject_tokens(pair[1])), len(pair[1]), -pair[0]),
    )[1]
    covered = _subject_tokens(base)
    additions: list[str] = []
    secondary = brief.get("secondary_keywords") or []
    required_methods = brief.get("required_methods") or []
    for raw in [primary, topic_phrase, *secondary[:3], *required_methods[:3]]:
        phrase = _normalized_phrase(raw, limit=100)
        tokens = _subject_tokens(phrase)
        if not phrase or not (tokens - covered):
            continue
        additions.append(phrase)
        covered.update(tokens)
        if len(additions) >= 2:
            break

    if not additions:
        return base[:360]
    suffix = " usando " + " e ".join(additions)
    return f"{base}{suffix}"[:360].rstrip()


@dataclass(frozen=True)
class KnowledgeContractInput:
    topic: str
    reader_start_state: str
    reader_final_state: str
    article_promise: str
    scope_limit: str
    jurisdiction: str | None = None
    content_type: EditorialContentTypeV3 = (
        EditorialContentTypeV3.procedural_decision_guide
    )
    requires_method_comparison: bool = True
    requires_external_reference_per_method: bool = True
    approach_dimension: ApproachDimension = ApproachDimension.method
    required_method_labels: tuple[str, ...] = ()
    additional_context: str = ""
    search_subject: str = ""
    project_locale: str = "pt-BR"
    input_normalizations: tuple[str, ...] = ()

    @classmethod
    def from_project(cls, project: Any) -> "KnowledgeContractInput":
        brief = dict(getattr(project, "briefing", None) or {})
        input_normalizations: list[str] = []

        def bounded(key: str, value: Any, limit: int) -> str:
            normalized = str(value or "").strip()
            if len(normalized) > limit:
                input_normalizations.append(
                    f"{key}:truncated:{len(normalized)}->{limit}"
                )
                normalized = normalized[:limit].rstrip()
            return normalized

        # Legacy projects may predate the API bounds.  Normalize them here so a
        # rerun can recover instead of repeating the same validation failure.
        topic = bounded("topic", getattr(project, "topic", ""), 380)
        # The factual subject is richer than the SEO keyword but remains bounded.
        # This is deterministic so no LLM call is required before research.
        search_subject = _build_search_subject(topic, brief)
        reader_context = str(brief.get("reader_context") or "").strip()
        reader_goal = str(brief.get("reader_goal") or "").strip()
        start_state = bounded("reader_start_state", brief.get("reader_start_state"), 1000)
        final_state = bounded("reader_final_state", brief.get("reader_final_state"), 1000)
        article_promise = bounded("article_promise", brief.get("article_promise"), 3000)
        scope_limit = bounded("scope_limit", brief.get("scope_limit"), 2000)
        required_method_labels: list[str] = []
        seen_method_labels: set[str] = set()
        raw_required_methods = brief.get("required_methods") or []
        if not isinstance(raw_required_methods, list):
            raise ValueError("Editorial V3 briefing field required_methods must be a list")
        for raw in raw_required_methods:
            label = " ".join(str(raw).split()).strip()
            key = label.casefold()
            if len(label) < 3 or key in seen_method_labels:
                continue
            seen_method_labels.add(key)
            required_method_labels.append(label[:200])
        content_type_value = str(
            brief.get("editorial_content_type") or "explanatory_guide"
        )
        try:
            content_type = EditorialContentTypeV3(content_type_value)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported Editorial V3 content type: {content_type_value}"
            ) from exc

        project_pipeline = getattr(
            getattr(project, "editorial_pipeline_version", "v2"),
            "value",
            getattr(project, "editorial_pipeline_version", "v2"),
        )
        if project_pipeline == "v3":
            required = {
                "reader_start_state": start_state,
                "reader_final_state": final_state,
                "article_promise": article_promise,
                "scope_limit": scope_limit,
            }
            missing = [key for key, value in required.items() if not value]
            if missing:
                raise ValueError(
                    "Editorial V3 project is missing required contract fields: "
                    + ", ".join(missing)
                )
            if (
                content_type == EditorialContentTypeV3.procedural_decision_guide
                and len(required_method_labels) < 2
            ):
                raise ValueError(
                    "Editorial V3 procedural guides require at least two required methods"
                )

        def strict_brief_bool(key: str, default: bool) -> bool:
            value = brief.get(key, default)
            if not isinstance(value, bool):
                raise ValueError(f"Editorial V3 briefing field {key} must be boolean")
            return value

        return cls(
            topic=topic,
            search_subject=search_subject,
            project_locale=normalize_locale(str(getattr(project, "language", None) or brief.get("language") or "pt-BR")),
            reader_start_state=start_state
            or reader_context
            or f"Leitor que precisa compreender {topic} antes de iniciar o processo.",
            reader_final_state=final_state
            or reader_goal
            or f"Leitor capaz de reconhecer o resultado final prometido para {topic}.",
            article_promise=article_promise
            or str(brief.get("content_objective") or "").strip()
            or (
                f"Explicar {topic}, apresentar as alternativas relevantes, orientar "
                "uma escolha contextual e acompanhar o processo até o resultado final."
            ),
            scope_limit=scope_limit
            or (
                "O conteúdo termina no resultado final definido pelo briefing e não "
                "avança para etapas posteriores não pesquisadas."
            ),
            jurisdiction=str(brief.get("jurisdiction") or "").strip() or None,
            content_type=content_type,
            requires_method_comparison=strict_brief_bool(
                "requires_method_comparison",
                content_type == EditorialContentTypeV3.procedural_decision_guide,
            ),
            requires_external_reference_per_method=strict_brief_bool(
                "requires_external_reference_per_method",
                content_type == EditorialContentTypeV3.procedural_decision_guide,
            ),
            approach_dimension=ApproachDimension(
                str(brief.get("required_approach_type") or "method")
            ),
            required_method_labels=tuple(required_method_labels),
            additional_context=str(brief.get("additional_context") or "").strip(),
            input_normalizations=tuple(input_normalizations),
        )


class KnowledgeContractBuilder:
    """Create the V3 knowledge graph without claiming unresolved facts."""

    def build(self, data: KnowledgeContractInput) -> ContentKnowledgeContract:
        if data.content_type == EditorialContentTypeV3.procedural_decision_guide:
            return self._procedural_decision_guide(data)
        return self._generic_contract(data)

    @staticmethod
    def checksum(contract: ContentKnowledgeContract) -> str:
        payload = json.dumps(
            contract.model_dump(mode="json"),
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _procedural_decision_guide(
        self, data: KnowledgeContractInput
    ) -> ContentKnowledgeContract:
        node_specs = [
            {
                "node_id": "subject_foundation",
                "kind": KnowledgeNodeKind.subject_foundation,
                "title_function": "Compreender o objeto e o processo antes de agir",
                "editorial_goal": (
                    "Abrir pelo problema e pela decisão do leitor, construir um modelo mental "
                    "simples do processo e preparar a apresentação das abordagens. Não despejar "
                    "faixas numéricas, riscos ou recomendações antes de o mapa do conteúdo existir."
                ),
                "before": data.reader_start_state,
                "after": (
                    "O leitor compreende o objeto, a terminologia central e a diferença "
                    "entre o início, as transições e o resultado final do processo."
                ),
                "question": (
                    f"O que o leitor precisa entender sobre {data.topic} antes de executar qualquer abordagem?"
                ),
                "depends_on": [],
                "knowledge": [
                    "estrutura e função do objeto central",
                    "definições dos estágios do processo",
                    "diferença entre processo iniciado, transição e resultado final",
                    "limites de viabilidade ou aplicabilidade sustentados pelas fontes",
                ],
                "decisions": [],
                "roles": [EvidenceRole.definition, EvidenceRole.mechanism],
                "criteria": [
                    "a abertura parte da dúvida ou decisão real do leitor",
                    "a terminologia essencial foi definida sem tom de dicionário",
                    "o leitor consegue distinguir os estágios que serão usados no guia",
                    "números e recomendações detalhadas não aparecem antes da orientação inicial",
                    "nenhuma conclusão procedural foi inventada nesta etapa",
                ],
            },
            {
                "node_id": "method_inventory",
                "kind": KnowledgeNodeKind.method_inventory,
                "title_function": "Apresentar cedo os caminhos disponíveis",
                "editorial_goal": (
                    "Orientar o leitor antes de despejar condições técnicas: mostrar quais "
                    "abordagens existem, o que muda entre eles e em que ponto cada caminho termina."
                ),
                "before": "O leitor entende o objetivo do processo, mas ainda não enxerga as alternativas disponíveis.",
                "after": "O leitor reconhece as abordagens documentados e possui um mapa mental para entender as condições comuns.",
                "question": "Quais abordagens relevantes existem, como funcionam em linhas gerais e quais variações devem ser agrupadas?",
                "depends_on": ["subject_foundation"],
                "knowledge": [
                    "definição operacional breve de cada abordagem",
                    "diferença prática central entre os caminhos",
                    "materiais e preparação que distinguem uma abordagem da outra",
                    "ponto inicial, necessidade de transferência e conexão com a etapa seguinte",
                    "variações equivalentes, nomes alternativos e limites",
                ],
                "decisions": ["distinguir abordagem de simples variação"],
                "roles": [
                    EvidenceRole.definition,
                    EvidenceRole.material,
                    EvidenceRole.sequence,
                    EvidenceRole.limitation,
                ],
                "criteria": [
                    "as abordagens aparecem antes das faixas e recomendações detalhadas",
                    "abordagens duplicados foram agrupados",
                    "cada abordagem possui uma descrição breve e comparável",
                    "nenhuma abordagem foi incluída apenas para aumentar a lista",
                ],
            },
            {
                "node_id": "process_requirements",
                "kind": KnowledgeNodeKind.process_requirements,
                "title_function": "Explicar o que todos as abordagens precisam em comum",
                "editorial_goal": (
                    "Depois de apresentar as alternativas, explicar cada requisito compartilhado "
                    "pela sequência importância, mecanismo, aplicação prática, manutenção, sinais "
                    "de falta ou excesso e ajuste possível."
                ),
                "before": "O leitor conhece as abordagens, mas ainda não entende as condições que fazem qualquer um deles funcionar.",
                "after": "O leitor entende o que controlar, por que isso importa e como reconhecer desvios antes de executar.",
                "question": "Quais condições são necessárias em todos as abordagens, por que importam e como podem ser mantidas na prática?",
                "depends_on": ["method_inventory"],
                "knowledge": [
                    "condições ambientais ou operacionais comuns",
                    "mecanismo ou função de cada condição no processo",
                    "como estabelecer e manter cada condição sem confundir presença com excesso",
                    "sinais observáveis de deficiência, excesso ou instabilidade",
                    "ajustes práticos sustentados e limites de aplicação",
                    "práticas de segurança, higiene e conformidade quando aplicáveis",
                ],
                "decisions": ["reconhecer se existem condições mínimas para começar"],
                "roles": [
                    EvidenceRole.prerequisite,
                    EvidenceRole.environmental_condition,
                    EvidenceRole.mechanism,
                    EvidenceRole.risk,
                    EvidenceRole.limitation,
                ],
                "criteria": [
                    "cada condição foi ligada a uma função, a uma forma de manutenção e a um risco",
                    "faixas numéricas aparecem somente depois de o leitor compreender o que está sendo controlado",
                    "regras condicionais não foram apresentadas como universais",
                    "o leitor sabe o que observar e ajustar antes de escolher ou executar uma abordagem",
                ],
            },
            {
                "node_id": "method_comparison",
                "kind": KnowledgeNodeKind.method_comparison,
                "title_function": "Comparar as abordagens por critérios úteis ao leitor",
                "editorial_goal": (
                    "Transformar o inventário em uma comparação funcional que exponha "
                    "diferenças, limitações, exigências e pontos de transição."
                ),
                "before": "O leitor conhece as abordagens, mas ainda não sabe em que eles diferem na prática.",
                "after": "O leitor consegue comparar as alternativas por critérios verificáveis.",
                "question": "Quais diferenças entre as abordagens mudam a escolha e a execução?",
                "depends_on": ["method_inventory", "process_requirements"],
                "knowledge": [
                    "nível de intervenção e observação",
                    "materiais e complexidade operacional",
                    "necessidade de transição ou transferência",
                    "vantagens, limitações e riscos documentados",
                ],
                "decisions": [
                    "identificar quais critérios importam para a situação do leitor"
                ],
                "roles": [
                    EvidenceRole.comparison,
                    EvidenceRole.risk,
                    EvidenceRole.limitation,
                    EvidenceRole.transition,
                ],
                "criteria": [
                    "a comparação usa os mesmos critérios para todos as abordagens",
                    "vantagens e limitações são sustentadas",
                    "a seção prepara uma decisão em vez de repetir as definições",
                ],
            },
            {
                "node_id": "method_selection",
                "kind": KnowledgeNodeKind.method_selection,
                "title_function": "Escolher a abordagem de forma contextual",
                "editorial_goal": (
                    "Converter os critérios comparativos em regras condicionais de "
                    "decisão, evitando declarar uma abordagem universalmente superior."
                ),
                "before": "O leitor conhece as diferenças, mas precisa relacioná-las ao próprio cenário.",
                "after": "O leitor consegue escolher uma alternativa e compreender o motivo da escolha.",
                "question": "Qual abordagem faz sentido para cada cenário e quais conclusões não podem ser generalizadas?",
                "depends_on": ["method_comparison"],
                "knowledge": [
                    "critérios observáveis de escolha",
                    "dependências de materiais, experiência e acompanhamento",
                    "condições que alteram a recomendação",
                    "casos em que a evidência não permite ranking",
                ],
                "decisions": ["selecionar uma abordagem com justificativa verificável"],
                "roles": [
                    EvidenceRole.decision_criterion,
                    EvidenceRole.comparison,
                    EvidenceRole.exception,
                    EvidenceRole.limitation,
                ],
                "criteria": [
                    "a decisão é condicional quando necessário",
                    "cada direção recomendada possui evidência",
                    "não existe melhor abordagem universal sem sustentação excepcional",
                ],
            },
            {
                "node_id": "method_execution",
                "kind": KnowledgeNodeKind.method_execution,
                "title_function": "Executar a abordagem escolhida em uma sequência verificável",
                "editorial_goal": (
                    "Desenvolver uma ramificação completa para cada abordagem, ligando "
                    "ação, motivo, observação, problema, correção e condição de avanço."
                ),
                "before": "O leitor escolheu uma abordagem, mas ainda não possui uma sequência executável.",
                "after": "O leitor consegue executar e acompanhar a abordagem escolhida até o ponto de convergência.",
                "question": "Qual sequência completa deve ser seguida em cada abordagem e como reconhecer o avanço?",
                "depends_on": ["method_selection"],
                "knowledge": [
                    "materiais, preparação e pré-condições",
                    "ações em ordem e motivo de cada ação",
                    "observações esperadas e sinais de problema",
                    "erros comuns, correções sustentadas e condição de avanço",
                ],
                "decisions": [
                    "decidir quando avançar, aguardar ou reavaliar em cada etapa"
                ],
                "roles": [
                    EvidenceRole.material,
                    EvidenceRole.action,
                    EvidenceRole.sequence,
                    EvidenceRole.success_signal,
                    EvidenceRole.failure_signal,
                    EvidenceRole.common_error,
                    EvidenceRole.correction,
                ],
                "criteria": [
                    "cada abordagem possui passos executáveis e ordenados",
                    "cada passo explica o propósito e a condição de conclusão",
                    "correções sem evidência são proibidas",
                    "todas as ramificações convergem em um estado observável",
                ],
                "branches": ["methods_discovered_and_approved_by_research"],
                "convergence": "progress_confirmation",
            },
            {
                "node_id": "progress_confirmation",
                "kind": KnowledgeNodeKind.progress_confirmation,
                "title_function": "Reconhecer progresso, falha e conclusão da abordagem inicial",
                "editorial_goal": (
                    "Definir sinais observáveis que indiquem avanço normal, problema ou "
                    "necessidade de uma nova decisão antes da transição."
                ),
                "before": "O leitor está executando uma abordagem e precisa interpretar o que observa.",
                "after": "O leitor distingue progresso, alerta e conclusão da abordagem inicial.",
                "question": "Quais sinais permitem confirmar o estágio alcançado sem depender apenas do tempo decorrido?",
                "depends_on": ["method_execution"],
                "knowledge": [
                    "sinais de progresso e variações normais",
                    "sinais de falha ou condição inadequada",
                    "diferença entre conclusão da abordagem e resultado final do guia",
                    "ações que devem ser evitadas",
                ],
                "decisions": ["confirmar o estágio antes de iniciar a transição"],
                "roles": [
                    EvidenceRole.success_signal,
                    EvidenceRole.failure_signal,
                    EvidenceRole.exception,
                    EvidenceRole.risk,
                ],
                "criteria": [
                    "sinais observáveis foram diferenciados de prazos absolutos",
                    "o leitor sabe quando aguardar e quando reavaliar",
                    "a conclusão desta etapa aponta para a decisão de transição",
                ],
            },
            {
                "node_id": "transition_decision",
                "kind": KnowledgeNodeKind.transition_decision,
                "title_function": "Decidir quando e se uma transição é necessária",
                "editorial_goal": (
                    "Aplicar critérios observáveis por abordagem para decidir a passagem ao "
                    "meio ou estágio seguinte, incluindo abordagens que já começaram nele."
                ),
                "before": "O leitor confirmou o estágio da abordagem inicial, mas não sabe se deve transferir ou continuar.",
                "after": "O leitor sabe se a transição se aplica e quais critérios autorizam o avanço.",
                "question": "Quando a transição deve ocorrer e em quais abordagens ela não existe?",
                "depends_on": ["progress_confirmation"],
                "knowledge": [
                    "critérios observáveis por abordagem",
                    "pré-condições do destino",
                    "riscos de antecipar ou atrasar",
                    "exceções para abordagens iniciados no estágio final",
                ],
                "decisions": ["transferir, continuar no mesmo meio ou reavaliar"],
                "roles": [
                    EvidenceRole.decision_criterion,
                    EvidenceRole.transition,
                    EvidenceRole.risk,
                    EvidenceRole.exception,
                ],
                "criteria": [
                    "a decisão não usa apenas tempo decorrido",
                    "abordagens sem transferência são explicitamente diferenciados",
                    "os riscos de cada decisão são sustentados",
                ],
            },
            {
                "node_id": "transition_execution",
                "kind": KnowledgeNodeKind.transition_execution,
                "title_function": "Executar a transição preservando a continuidade",
                "editorial_goal": (
                    "Ensinar a preparação, o manuseio e a acomodação necessários para "
                    "conectar a abordagem inicial ao estágio seguinte sem saltos lógicos."
                ),
                "before": "O leitor decidiu avançar e precisa realizar a transição corretamente.",
                "after": "O objeto foi colocado no estágio seguinte e as condições posteriores estão definidas.",
                "question": "Como realizar a transição e quais erros podem interromper o processo?",
                "depends_on": ["transition_decision"],
                "knowledge": [
                    "preparação do destino",
                    "sequência de manuseio e posicionamento",
                    "condições imediatamente posteriores",
                    "erros e riscos específicos da transição",
                ],
                "decisions": [
                    "confirmar que a transição foi concluída e iniciar monitoramento"
                ],
                "roles": [
                    EvidenceRole.prerequisite,
                    EvidenceRole.action,
                    EvidenceRole.sequence,
                    EvidenceRole.transition,
                    EvidenceRole.common_error,
                    EvidenceRole.correction,
                ],
                "criteria": [
                    "a sequência está completa e ligada à decisão anterior",
                    "ações delicadas possuem justificativa",
                    "o conteúdo contempla a condição das abordagens sem transferência",
                ],
            },
            {
                "node_id": "post_transition_monitoring",
                "kind": KnowledgeNodeKind.post_transition_monitoring,
                "title_function": "Acompanhar o período intermediário até o resultado final",
                "editorial_goal": (
                    "Orientar o que manter, observar e reavaliar depois da transição, sem "
                    "encerrar o guia antes do resultado prometido."
                ),
                "before": "A transição foi concluída ou a abordagem já se encontra no estágio final, mas o resultado ainda não é visível.",
                "after": "O leitor consegue acompanhar o período intermediário e interpretar mudanças relevantes.",
                "question": "O que fazer e observar entre a transição e o resultado final?",
                "depends_on": ["transition_execution"],
                "knowledge": [
                    "condições que precisam ser mantidas",
                    "mudanças esperadas e sinais de superfície",
                    "problemas comuns, espera adequada e reavaliação",
                    "ações que podem causar dano",
                ],
                "decisions": [
                    "aguardar, ajustar condição sustentada ou investigar um problema"
                ],
                "roles": [
                    EvidenceRole.environmental_condition,
                    EvidenceRole.success_signal,
                    EvidenceRole.failure_signal,
                    EvidenceRole.correction,
                    EvidenceRole.risk,
                ],
                "criteria": [
                    "o conteúdo cobre o período intermediário",
                    "problemas e respostas permanecem ligados à evidência",
                    "o próximo estado é o resultado final observável",
                ],
            },
            {
                "node_id": "final_outcome_confirmation",
                "kind": KnowledgeNodeKind.final_outcome_confirmation,
                "title_function": "Reconhecer o resultado final e encerrar no limite prometido",
                "editorial_goal": (
                    "Definir de forma observável quando a promessa foi cumprida e marcar "
                    "a fronteira para uma fase posterior que não pertence a este conteúdo."
                ),
                "before": "O leitor está monitorando o processo, mas ainda precisa confirmar o resultado final.",
                "after": data.reader_final_state,
                "question": "Como reconhecer que o resultado final foi alcançado e que uma nova fase começou?",
                "depends_on": ["post_transition_monitoring"],
                "knowledge": [
                    "critério observável de conclusão",
                    "diferença entre o resultado deste guia e a fase seguinte",
                    "limites do escopo editorial",
                ],
                "decisions": ["encerrar o procedimento deste guia no ponto correto"],
                "roles": [EvidenceRole.final_outcome, EvidenceRole.definition],
                "criteria": [
                    "o resultado final é observável",
                    "a promessa editorial foi cumprida",
                    "o texto não avança para etapas fora do escopo",
                ],
            },
            {
                "node_id": "troubleshooting",
                "kind": KnowledgeNodeKind.troubleshooting,
                "title_function": "Consolidar problemas sem remover o contexto das etapas",
                "editorial_goal": (
                    "Oferecer uma consulta rápida de sintomas, possíveis causas e respostas "
                    "sustentadas, mantendo também os alertas no ponto em que surgem."
                ),
                "before": "O leitor compreende o fluxo completo e precisa revisar problemas de forma transversal.",
                "after": "O leitor consegue localizar um problema e voltar à etapa relevante sem aplicar correções genéricas.",
                "question": "Quais problemas atravessam o processo e como devem ser investigados com segurança?",
                "depends_on": ["final_outcome_confirmation"],
                "knowledge": [
                    "sintomas e possíveis causas condicionais",
                    "etapa em que cada problema costuma ser observado",
                    "correções sustentadas e ações proibidas",
                    "casos em que a evidência é insuficiente",
                ],
                "decisions": [
                    "identificar a etapa e escolher apenas uma resposta sustentada"
                ],
                "roles": [
                    EvidenceRole.failure_signal,
                    EvidenceRole.common_error,
                    EvidenceRole.correction,
                    EvidenceRole.limitation,
                ],
                "criteria": [
                    "cada correção possui evidência",
                    "possíveis causas são apresentadas como hipóteses quando necessário",
                    "a seção não repete integralmente as abordagens",
                ],
            },
            {
                "node_id": "external_references",
                "kind": KnowledgeNodeKind.external_references,
                "title_function": "Oferecer aprofundamento externo validado para cada abordagem",
                "editorial_goal": (
                    "Selecionar referências que realmente ensinem a abordagem prometido, "
                    "com correspondência temática, profundidade procedural e URL verificada."
                ),
                "before": "O leitor concluiu o guia resumido e pode escolher aprofundar uma abordagem específico.",
                "after": "O leitor possui uma referência externa confiável e descritiva para cada abordagem aprovado.",
                "question": "Qual conteúdo externo é o melhor aprofundamento verificável para cada abordagem?",
                "depends_on": ["method_inventory", "troubleshooting"],
                "knowledge": [
                    "correspondência entre abordagem e página",
                    "profundidade, autoria e independência editorial do conteúdo externo",
                    "rejeição de lojas, marketplaces, páginas de produto, categoria e landing pages",
                    "estado HTTP, redirecionamentos e canonical",
                    "âncora descritiva e data da verificação",
                ],
                "decisions": ["aprovar ou rejeitar uma referência por abordagem"],
                "roles": [EvidenceRole.external_reference],
                "criteria": [
                    "cada abordagem aprovado possui ao menos uma referência válida",
                    "o link ensina a abordagem em vez de apenas vendê-lo",
                    "nenhuma referência externa pertence a e-commerce ou marketplace",
                    "URL, título, autoria e correspondência foram verificados",
                ],
            },
        ]

        nodes: list[KnowledgeNodeContract] = []
        for sequence, spec in enumerate(node_specs, start=1):
            nodes.append(
                KnowledgeNodeContract(
                    node_id=spec["node_id"],
                    sequence=sequence,
                    kind=spec["kind"],
                    title_function=spec["title_function"],
                    editorial_goal=spec["editorial_goal"],
                    reader_state_before=spec["before"],
                    reader_state_after=spec["after"],
                    central_question=spec["question"],
                    depends_on=spec["depends_on"],
                    required_knowledge=spec["knowledge"],
                    required_decisions=spec["decisions"],
                    required_evidence_roles=spec["roles"],
                    completion_criteria=spec["criteria"],
                    branches=spec.get("branches", []),
                    convergence_node_id=spec.get("convergence"),
                    universal_role=_PROCEDURAL_HIERARCHY[spec["node_id"]][0],
                    applicability=NodeApplicability.required,
                    importance=_PROCEDURAL_HIERARCHY[spec["node_id"]][1],
                    research_required=True,
                    minimum_depth_weight=_PROCEDURAL_HIERARCHY[spec["node_id"]][2],
                )
            )

        edges: list[KnowledgeEdgeContract] = []
        for node in nodes:
            for dependency in node.depends_on:
                edges.append(
                    KnowledgeEdgeContract(
                        from_node_id=dependency,
                        to_node_id=node.node_id,
                        relation=(
                            KnowledgeEdgeRelation.converges_to
                            if dependency == "method_execution"
                            and node.node_id == "progress_confirmation"
                            else KnowledgeEdgeRelation.prerequisite
                        ),
                        rationale=(
                            f"{node.node_id} depende do estado de conhecimento produzido por {dependency}."
                        ),
                    )
                )
        edges.append(
            KnowledgeEdgeContract(
                from_node_id="method_execution",
                to_node_id="progress_confirmation",
                relation=KnowledgeEdgeRelation.converges_to,
                rationale="As ramificações das abordagens voltam a um gate comum de observação.",
            )
        )
        # Remove the duplicate prerequisite edge for the explicit convergence.
        deduplicated: list[KnowledgeEdgeContract] = []
        seen: set[tuple[str, str]] = set()
        for edge in reversed(edges):
            pair = (edge.from_node_id, edge.to_node_id)
            if pair in seen:
                continue
            seen.add(pair)
            deduplicated.append(edge)
        deduplicated.reverse()

        return ContentKnowledgeContract(
            content_type=data.content_type,
            topic=data.topic,
            reader_start_state=data.reader_start_state,
            reader_final_state=data.reader_final_state,
            article_promise=data.article_promise,
            scope_limit=data.scope_limit,
            jurisdiction=data.jurisdiction,
            requires_method_comparison=data.requires_method_comparison,
            requires_external_reference_per_method=(
                data.requires_external_reference_per_method
            ),
            approach_dimension=data.approach_dimension,
            required_method_labels=list(data.required_method_labels),
            research_source_policy=ResearchSourcePolicyContract(),
            nodes=nodes,
            edges=deduplicated,
            prohibited_conclusions=[
                "declarar uma abordagem universalmente melhor sem evidência excepcional",
                "preencher lacunas de pesquisa com uma conclusão plausível",
                "confundir a conclusão da abordagem inicial com o resultado final do guia",
                "avançar para etapas posteriores ao limite de escopo",
                "apresentar uma correção sem fonte aplicável ao problema",
            ],
            metadata={
                "builder": "deterministic-procedural-contract.v3.5",
                "source_policy": "intent-aware-search.v3.5",
                "search_subject": data.search_subject,
                "project_locale": data.project_locale,
                "research_intent": build_research_intent_payload(
                    canonical_subject=data.search_subject,
                    project_locale=data.project_locale,
                    jurisdiction=data.jurisdiction,
                    content_type=data.content_type,
                    method_labels=data.required_method_labels,
                ),
                "input_normalizations": list(data.input_normalizations),
                "additional_context": data.additional_context,
                "additional_context_present": bool(data.additional_context),
                "required_method_count": len(data.required_method_labels),
                "approach_dimension": data.approach_dimension.value,
            },
        )

    def _generic_contract(
        self, data: KnowledgeContractInput
    ) -> ContentKnowledgeContract:
        hierarchy = UniversalEditorialHierarchyBuilder.build(
            topic=data.topic,
            architecture_type=EditorialArchitectureType(data.content_type.value),
            reader_start_state=data.reader_start_state,
            reader_final_state=data.reader_final_state,
        )
        # Generation needs the complete editorial graph, including closing/CTA
        # nodes that do not require research. Research planning already skips
        # nodes with research_required=False, so filtering them here only erased
        # the conclusion and commercial bridge from the writer contract.
        active_hierarchy_nodes = list(hierarchy.nodes)
        active_ids = {node.node_id for node in active_hierarchy_nodes}
        nodes: list[KnowledgeNodeContract] = []
        for sequence, node in enumerate(active_hierarchy_nodes, start=1):
            dependencies = [
                dependency for dependency in node.depends_on if dependency in active_ids
            ]
            nodes.append(
                KnowledgeNodeContract(
                    node_id=node.node_id,
                    sequence=sequence,
                    kind=_GENERIC_KIND_BY_ROLE[node.role],
                    title_function=node.title_function,
                    editorial_goal=node.purpose,
                    reader_state_before=node.reader_state_before,
                    reader_state_after=node.reader_state_after,
                    central_question=node.central_question,
                    depends_on=dependencies,
                    required_knowledge=list(node.completion_criteria),
                    required_decisions=(
                        ["permitir uma decisão contextual sustentada"]
                        if node.role
                        in {
                            UniversalNodeRole.decision_criteria,
                            UniversalNodeRole.recommendation_logic,
                            UniversalNodeRole.solution_fit,
                            UniversalNodeRole.self_diagnosis,
                        }
                        else []
                    ),
                    required_evidence_roles=_EVIDENCE_BY_ROLE[node.role],
                    completion_criteria=list(node.completion_criteria),
                    universal_role=node.role,
                    applicability=node.applicability,
                    importance=node.importance,
                    research_required=node.research_required,
                    minimum_depth_weight=node.minimum_depth_weight,
                    maximum_depth_weight=node.maximum_depth_weight,
                    metadata={
                        **node.metadata,
                        "allows_internal_link_only": node.allows_internal_link_only,
                    },
                )
            )

        edges = [
            KnowledgeEdgeContract(
                from_node_id=dependency,
                to_node_id=node.node_id,
                relation=KnowledgeEdgeRelation.prerequisite,
                rationale=(
                    f"{node.node_id} depende da transformação editorial produzida por {dependency}."
                ),
            )
            for node in nodes
            for dependency in node.depends_on
        ]
        if len(edges) < 2:
            edges = [
                KnowledgeEdgeContract(
                    from_node_id=nodes[index - 1].node_id,
                    to_node_id=nodes[index].node_id,
                    relation=KnowledgeEdgeRelation.sequence,
                    rationale="A compreensão progride segundo a hierarquia editorial universal.",
                )
                for index in range(1, len(nodes))
            ]
        return ContentKnowledgeContract(
            content_type=data.content_type,
            topic=data.topic,
            reader_start_state=data.reader_start_state,
            reader_final_state=data.reader_final_state,
            article_promise=data.article_promise,
            scope_limit=data.scope_limit,
            jurisdiction=data.jurisdiction,
            requires_method_comparison=False,
            requires_external_reference_per_method=False,
            approach_dimension=None,
            required_method_labels=[],
            research_source_policy=ResearchSourcePolicyContract(),
            nodes=nodes,
            edges=edges,
            prohibited_conclusions=[
                "preencher lacunas de pesquisa com conhecimento plausível",
                "inverter a hierarquia entre conteúdo central e periférico",
                "encerrar antes de cumprir a transformação prometida ao leitor",
                "converter alegação comercial em fato sem evidência independente",
                "avançar além do limite de escopo",
            ],
            metadata={
                "builder": "deterministic-universal-contract.v3.5",
                "source_policy": "intent-aware-search.v3.5",
                "search_subject": data.search_subject,
                "project_locale": data.project_locale,
                "research_intent": build_research_intent_payload(
                    canonical_subject=data.search_subject,
                    project_locale=data.project_locale,
                    jurisdiction=data.jurisdiction,
                    content_type=data.content_type,
                    method_labels=data.required_method_labels,
                ),
                "input_normalizations": list(data.input_normalizations),
                "additional_context": data.additional_context,
                "universal_hierarchy": hierarchy.model_dump(mode="json"),
                "closing_node_id": hierarchy.closing_node_id,
                "non_research_nodes": [
                    node.model_dump(mode="json")
                    for node in hierarchy.nodes
                    if not node.research_required
                ],
            },
        )
