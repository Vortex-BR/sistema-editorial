"""Deterministic, domain-independent editorial architecture.

This service runs before research.  It does not decide facts or headings.  It
creates and validates the logical progression that every later stage must obey.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from app.schemas.editorial_hierarchy import (
    EditorialArchitectureType,
    EditorialHierarchyContract,
    EditorialHierarchyNode,
    NodeApplicability,
    NodeImportance,
    UniversalNodeRole,
)


@dataclass(frozen=True)
class HierarchyValidationReport:
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    covered_node_ids: tuple[str, ...] = ()
    missing_node_ids: tuple[str, ...] = ()
    first_positions: dict[str, int] = field(default_factory=dict)
    word_counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.blockers


class UniversalEditorialHierarchyBuilder:
    """Build a reader-progress graph from the explicit content architecture."""

    @classmethod
    def from_project(cls, project: Any) -> EditorialHierarchyContract:
        brief = dict(getattr(project, "briefing", None) or {})
        content_type_raw = str(
            brief.get("editorial_content_type") or "explanatory_guide"
        )
        try:
            architecture_type = EditorialArchitectureType(content_type_raw)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported editorial architecture type: {content_type_raw}"
            ) from exc
        topic = str(getattr(project, "topic", "")).strip()
        start = (
            str(brief.get("reader_start_state") or "").strip()
            or str(brief.get("reader_context") or "").strip()
            or f"Leitor que ainda não possui um modelo mental confiável sobre {topic}."
        )
        final = (
            str(brief.get("reader_final_state") or "").strip()
            or str(brief.get("reader_goal") or "").strip()
            or f"Leitor capaz de compreender, avaliar ou agir sobre {topic} com critérios claros."
        )
        return cls.build(
            topic=topic,
            architecture_type=architecture_type,
            reader_start_state=start,
            reader_final_state=final,
        )

    @classmethod
    def build(
        cls,
        *,
        topic: str,
        architecture_type: EditorialArchitectureType,
        reader_start_state: str,
        reader_final_state: str,
    ) -> EditorialHierarchyContract:
        specs = cls._templates()[architecture_type]
        nodes: list[EditorialHierarchyNode] = []
        prior_state = reader_start_state
        for index, spec in enumerate(specs, start=1):
            after = (
                reader_final_state
                if index == len(specs)
                else spec.get("after")
                or f"O leitor concluiu a função editorial {spec['node_id']} e está preparado para a próxima decisão."
            )
            node = EditorialHierarchyNode(
                node_id=spec["node_id"],
                sequence=index,
                role=spec["role"],
                title_function=spec["title"],
                purpose=spec["purpose"],
                reader_state_before=prior_state,
                reader_state_after=after,
                central_question=spec["question"].format(topic=topic),
                depends_on=list(spec.get("depends_on") or ([] if index == 1 else [specs[index - 2]["node_id"]])),
                applicability=spec.get("applicability", NodeApplicability.required),
                importance=spec.get("importance", NodeImportance.core),
                research_required=spec.get("research_required", True),
                completion_criteria=list(spec["criteria"]),
                minimum_depth_weight=float(spec.get("minimum_depth_weight", 1.0)),
                maximum_depth_weight=spec.get("maximum_depth_weight"),
                allows_internal_link_only=bool(spec.get("allows_internal_link_only", False)),
                metadata=dict(spec.get("metadata") or {}),
            )
            nodes.append(node)
            prior_state = after
        return EditorialHierarchyContract(
            architecture_type=architecture_type,
            topic=topic,
            reader_start_state=reader_start_state,
            reader_final_state=reader_final_state,
            nodes=nodes,
            closing_node_id=nodes[-1].node_id,
            metadata={
                "builder": "universal-editorial-hierarchy.v1",
                "domain_specific": False,
            },
        )

    @staticmethod
    def _templates() -> dict[EditorialArchitectureType, list[dict]]:
        C = NodeApplicability.conditional
        OPTIONAL = NodeApplicability.optional
        CORE = NodeImportance.core
        SUPPORT = NodeImportance.supporting
        PERIPHERAL = NodeImportance.peripheral
        return {
            EditorialArchitectureType.procedural_decision_guide: [
                {"node_id": "foundation", "role": UniversalNodeRole.foundation, "title": "Construir o modelo mental inicial", "purpose": "Definir o objeto, o resultado esperado e os limites necessários antes de apresentar escolhas ou ações.", "question": "O que precisa ser entendido sobre {topic} antes de escolher ou executar qualquer abordagem?", "criteria": ["objeto, escopo e resultado foram distinguidos", "terminologia indispensável foi explicada"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "approach_landscape", "role": UniversalNodeRole.landscape, "title": "Apresentar os caminhos reais", "purpose": "Mostrar as abordagens existentes lado a lado antes de aprofundar qualquer uma.", "question": "Quais abordagens reais existem para {topic}, onde começam, onde terminam e em que diferem?", "criteria": ["abordagens centrais foram inventariadas", "variações equivalentes foram agrupadas"], "importance": CORE, "minimum_depth_weight": 1.2},
                {"node_id": "shared_requirements", "role": UniversalNodeRole.requirements, "title": "Explicar requisitos compartilhados", "purpose": "Explicar condições, recursos e limites comuns que determinam se a execução pode começar.", "question": "Quais requisitos comuns tornam as abordagens de {topic} viáveis e como o leitor reconhece se estão presentes?", "criteria": ["requisitos foram ligados à sua função", "condições e limites foram explicitados"], "importance": CORE, "minimum_depth_weight": 1.2},
                {"node_id": "selection_logic", "role": UniversalNodeRole.decision_criteria, "title": "Orientar a escolha contextual", "purpose": "Transformar diferenças entre abordagens em critérios de decisão aplicáveis ao contexto do leitor.", "question": "Quais critérios sustentados permitem escolher entre as abordagens de {topic} sem declarar uma opção universalmente melhor?", "criteria": ["critérios de escolha possuem condições", "trade-offs foram preservados"], "importance": CORE, "minimum_depth_weight": 1.1},
                {"node_id": "preparation_gate", "role": UniversalNodeRole.preparation, "title": "Verificar preparação ou desbloqueio", "purpose": "Investigar e explicar uma preparação indispensável, espera, ativação ou condição prévia somente quando ela existir.", "question": "Existe alguma preparação, espera, ativação ou condição de desbloqueio indispensável antes da execução de {topic}?", "criteria": ["a necessidade da etapa foi confirmada ou descartada", "nenhuma etapa foi inventada por analogia"], "applicability": C, "importance": SUPPORT, "minimum_depth_weight": 0.6},
                {"node_id": "execution", "role": UniversalNodeRole.execution, "title": "Executar cada abordagem", "purpose": "Ensinar ações, sequência, propósito, condições e exceções de cada abordagem central.", "question": "Como executar cada abordagem central de {topic}, em qual sequência e sob quais condições?", "criteria": ["cada abordagem central possui sequência completa", "ações possuem propósito e condição"], "importance": CORE, "minimum_depth_weight": 2.0},
                {"node_id": "progress_signals", "role": UniversalNodeRole.progress_signal, "title": "Reconhecer avanço observável", "purpose": "Definir sinais observáveis de progresso e critérios de passagem; tempo isolado só vale quando for um critério técnico legítimo.", "question": "Quais sinais observáveis mostram que a execução de {topic} está avançando e que o próximo passo pode começar?", "criteria": ["cada etapa possui observação ou condição de conclusão", "tempo foi contextualizado quando utilizado"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "common_problems", "role": UniversalNodeRole.problems, "title": "Resolver problemas comuns", "purpose": "Separar falhas, causas prováveis e correções sustentadas.", "question": "O que pode dar errado em {topic}, por que acontece e qual correção é sustentada para cada situação?", "criteria": ["problemas possuem causa provável", "correções não excedem a evidência"], "importance": CORE, "minimum_depth_weight": 1.1},
                {"node_id": "self_diagnosis", "role": UniversalNodeRole.self_diagnosis, "title": "Permitir autodiagnóstico", "purpose": "Ensinar o leitor a avaliar o estado atual mesmo quando não há falha evidente.", "question": "Como o leitor avalia se {topic} está saudável, estável ou desviando antes de uma falha explícita?", "criteria": ["sinais normais e sinais de alerta foram distinguidos", "a avaliação leva a uma decisão clara"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "final_outcome", "role": UniversalNodeRole.outcome, "title": "Confirmar o resultado final", "purpose": "Fechar o processo somente quando o resultado contratado puder ser reconhecido de forma observável.", "question": "Qual resultado observável confirma que a promessa sobre {topic} foi cumprida e onde o escopo termina?", "criteria": ["resultado final é observável", "o texto não avança além do escopo"], "importance": CORE, "minimum_depth_weight": 0.8},
                {"node_id": "closing", "role": UniversalNodeRole.offer_bridge, "title": "Encerrar e orientar a próxima ação", "purpose": "Sintetizar a decisão e conectar uma próxima ação ou oferta apenas depois de cumprir a promessa, sem criar novos fatos.", "question": "Qual próxima ação editorial ou comercial é coerente depois que o leitor compreende {topic}?", "criteria": ["não cria alegações novas", "aparece depois de todos os nós aplicáveis"], "research_required": False, "applicability": OPTIONAL, "importance": PERIPHERAL, "minimum_depth_weight": 0.25, "maximum_depth_weight": 0.6},
            ],
            EditorialArchitectureType.procedural_how_to: [
                {"node_id": "foundation", "role": UniversalNodeRole.foundation, "title": "Definir o resultado e os limites", "purpose": "Explicar o que será realizado, qual resultado observável encerra o procedimento e quais limites precisam ser respeitados.", "question": "O que o leitor precisa compreender sobre {topic} antes de iniciar o procedimento?", "criteria": ["resultado final foi definido", "escopo e limites foram delimitados"], "importance": CORE, "minimum_depth_weight": 0.9},
                {"node_id": "requirements", "role": UniversalNodeRole.requirements, "title": "Confirmar pré-requisitos", "purpose": "Apresentar condições, recursos, materiais e impedimentos que determinam se o procedimento pode começar.", "question": "Quais pré-requisitos, materiais, condições e impedimentos precisam ser verificados antes de executar {topic}?", "criteria": ["pré-requisitos e materiais foram ligados à sua função", "impedimentos e limites foram explicitados"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "preparation", "role": UniversalNodeRole.preparation, "title": "Preparar a execução", "purpose": "Organizar a preparação indispensável sem inventar etapas por analogia.", "question": "Qual preparação comprovadamente necessária antecede a execução de {topic}?", "criteria": ["a preparação possui propósito", "nenhuma preparação artificial foi adicionada"], "applicability": C, "importance": SUPPORT, "minimum_depth_weight": 0.7},
                {"node_id": "execution", "role": UniversalNodeRole.execution, "title": "Executar o procedimento em sequência", "purpose": "Ensinar um único caminho principal com ações, ordem, propósito, condições e exceções claras.", "question": "Como executar {topic} em uma sequência completa, do início ao resultado contratado?", "criteria": ["ações e sequência foram desenvolvidas", "cada etapa possui propósito e condição"], "importance": CORE, "minimum_depth_weight": 2.0},
                {"node_id": "progress_signals", "role": UniversalNodeRole.progress_signal, "title": "Reconhecer progresso e critérios de avanço", "purpose": "Definir sinais observáveis que confirmam o avanço e o momento correto de seguir.", "question": "Quais sinais observáveis confirmam que {topic} está avançando e que a próxima etapa pode começar?", "criteria": ["sinais normais e de alerta foram distinguidos", "critérios de avanço são observáveis"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "problems", "role": UniversalNodeRole.problems, "title": "Diagnosticar e corrigir desvios", "purpose": "Relacionar problemas comuns, causas prováveis, verificações e correções sustentadas.", "question": "O que pode dar errado em {topic}, como diagnosticar a causa e qual correção é sustentada?", "criteria": ["problemas possuem causa e verificação", "correções não excedem a evidência"], "importance": CORE, "minimum_depth_weight": 1.1},
                {"node_id": "outcome", "role": UniversalNodeRole.outcome, "title": "Confirmar o resultado final", "purpose": "Encerrar somente quando o resultado prometido puder ser reconhecido de forma observável.", "question": "Qual resultado observável confirma que {topic} foi concluído dentro do escopo?", "criteria": ["resultado final é observável", "o texto não avança além do escopo"], "importance": CORE, "minimum_depth_weight": 0.8},
                {"node_id": "closing", "role": UniversalNodeRole.offer_bridge, "title": "Encerrar e orientar a próxima ação", "purpose": "Sintetizar a conclusão e indicar uma próxima ação coerente sem criar fatos ou oferta inexistente.", "question": "Qual próxima ação é coerente depois de concluir {topic}?", "criteria": ["não cria alegações novas", "aparece depois de cumprir a promessa"], "research_required": False, "applicability": OPTIONAL, "importance": PERIPHERAL, "minimum_depth_weight": 0.25, "maximum_depth_weight": 0.6},
            ],
            EditorialArchitectureType.explanatory_guide: [
                {"node_id": "foundation", "role": UniversalNodeRole.foundation, "title": "Delimitar o conceito e a pergunta", "purpose": "Estabelecer definições, escopo e a dúvida real que o conteúdo precisa resolver.", "question": "Quais conceitos e limites são indispensáveis para compreender {topic}?", "criteria": ["conceitos centrais definidos", "escopo delimitado"], "importance": CORE},
                {"node_id": "context", "role": UniversalNodeRole.landscape, "title": "Situar o contexto relevante", "purpose": "Mostrar como o tema se relaciona com o problema, cenário ou decisão do leitor.", "question": "Em qual contexto {topic} importa e quais dimensões precisam ser diferenciadas?", "criteria": ["contexto ligado à necessidade do leitor", "dimensões relevantes organizadas"], "importance": SUPPORT},
                {"node_id": "mechanism", "role": UniversalNodeRole.mechanism, "title": "Explicar como e por que funciona", "purpose": "Desenvolver o mecanismo, as relações causais sustentadas, condições e limitações.", "question": "Como {topic} funciona, por quais mecanismos e sob quais condições ou limitações?", "criteria": ["mecanismo desenvolvido", "condições e limites preservados"], "importance": CORE, "minimum_depth_weight": 1.8},
                {"node_id": "implications", "role": UniversalNodeRole.implications, "title": "Derivar implicações úteis", "purpose": "Traduzir a explicação em consequências, decisões ou aplicações sem extrapolar a evidência.", "question": "Quais implicações práticas e decisões decorrem do que é conhecido sobre {topic}?", "criteria": ["implicações decorrem do mecanismo", "não há extrapolação"], "importance": CORE, "minimum_depth_weight": 1.2},
                {"node_id": "misconceptions", "role": UniversalNodeRole.misconceptions, "title": "Corrigir confusões relevantes", "purpose": "Distinguir conceitos, mitos ou interpretações que alteram a compreensão do tema.", "question": "Quais confusões ou interpretações incorretas sobre {topic} precisam ser corrigidas?", "criteria": ["confusões relevantes foram diferenciadas", "correção possui suporte"], "applicability": C, "importance": SUPPORT, "minimum_depth_weight": 0.6},
                {"node_id": "practical_application", "role": UniversalNodeRole.outcome, "title": "Aplicar a compreensão", "purpose": "Mostrar como o leitor usa a explicação para reconhecer, avaliar ou agir no limite do escopo.", "question": "Como o leitor pode aplicar com segurança a compreensão de {topic}?", "criteria": ["aplicação ligada à explicação", "limite do escopo respeitado"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "closing", "role": UniversalNodeRole.offer_bridge, "title": "Concluir sem criar fatos", "purpose": "Retomar a resposta central e oferecer uma próxima ação coerente.", "question": "Qual conclusão e próxima ação são coerentes depois de compreender {topic}?", "criteria": ["promessa retomada", "nenhuma alegação nova"], "research_required": False, "applicability": OPTIONAL, "importance": PERIPHERAL, "minimum_depth_weight": 0.25, "maximum_depth_weight": 0.6},
            ],
            EditorialArchitectureType.comparison: [
                {"node_id": "foundation", "role": UniversalNodeRole.foundation, "title": "Definir o que está sendo comparado", "purpose": "Delimitar alternativas, finalidade e unidade de comparação.", "question": "O que exatamente deve ser comparado em {topic} e para qual decisão?", "criteria": ["alternativas e escopo definidos", "comparação equivalente"], "importance": CORE},
                {"node_id": "decision_criteria", "role": UniversalNodeRole.decision_criteria, "title": "Estabelecer critérios antes do veredito", "purpose": "Definir critérios relevantes e como cada um influencia a decisão.", "question": "Quais critérios objetivos e contextuais devem orientar a comparação de {topic}?", "criteria": ["critérios definidos antes das conclusões", "pesos condicionais explicitados"], "importance": CORE, "minimum_depth_weight": 1.1},
                {"node_id": "options", "role": UniversalNodeRole.options, "title": "Apresentar cada alternativa com equilíbrio", "purpose": "Explicar cada opção com escopo e profundidade comparáveis.", "question": "Quais são as alternativas centrais de {topic} e quais características relevantes cada uma possui?", "criteria": ["alternativas centrais cobertas", "profundidade equilibrada"], "importance": CORE, "minimum_depth_weight": 1.5},
                {"node_id": "comparison", "role": UniversalNodeRole.comparison, "title": "Comparar ponto a ponto", "purpose": "Aplicar os critérios às alternativas, preservando vantagens, limites e incertezas.", "question": "Como as alternativas de {topic} se comportam em cada critério e quais trade-offs aparecem?", "criteria": ["comparação usa os mesmos critérios", "trade-offs preservados"], "importance": CORE, "minimum_depth_weight": 1.8},
                {"node_id": "scenario_fit", "role": UniversalNodeRole.solution_fit, "title": "Relacionar opções a cenários", "purpose": "Mostrar em que condições cada alternativa tende a ser mais adequada.", "question": "Para quais cenários e restrições cada alternativa de {topic} é mais adequada?", "criteria": ["recomendações são condicionais", "cenários distintos foram cobertos"], "importance": CORE, "minimum_depth_weight": 1.1},
                {"node_id": "recommendation_logic", "role": UniversalNodeRole.recommendation_logic, "title": "Entregar uma lógica de escolha", "purpose": "Converter a comparação em decisão verificável sem declarar vencedor universal.", "question": "Qual lógica de decisão permite escolher em {topic} com base no contexto do leitor?", "criteria": ["decisão deriva dos critérios", "não há vencedor universal sem suporte"], "importance": CORE, "minimum_depth_weight": 1.0},
                {"node_id": "closing", "role": UniversalNodeRole.offer_bridge, "title": "Encerrar pela decisão", "purpose": "Retomar a escolha e orientar a próxima ação sem adicionar alegações.", "question": "Qual próxima ação é coerente depois da comparação de {topic}?", "criteria": ["decisão resumida", "CTA posterior à análise"], "research_required": False, "applicability": OPTIONAL, "importance": PERIPHERAL, "minimum_depth_weight": 0.25, "maximum_depth_weight": 0.6},
            ],
            EditorialArchitectureType.troubleshooting: [
                {"node_id": "symptom_definition", "role": UniversalNodeRole.symptoms, "title": "Definir o sintoma com precisão", "purpose": "Distinguir o problema real de sinais parecidos e estabelecer o estado esperado.", "question": "Como reconhecer e delimitar corretamente o problema em {topic}?", "criteria": ["sintoma definido", "estado normal diferenciado"], "importance": CORE},
                {"node_id": "diagnostic_baseline", "role": UniversalNodeRole.requirements, "title": "Coletar informações antes de corrigir", "purpose": "Definir verificações, contexto e dados necessários para não aplicar correções às cegas.", "question": "Quais verificações e informações são necessárias antes de diagnosticar {topic}?", "criteria": ["baseline verificável", "correção prematura evitada"], "importance": CORE},
                {"node_id": "probable_causes", "role": UniversalNodeRole.causes, "title": "Organizar causas por evidência e probabilidade", "purpose": "Relacionar cada causa a sinais diferenciais, condições e limites.", "question": "Quais causas podem explicar o problema em {topic} e quais sinais diferenciam cada uma?", "criteria": ["causas possuem sinais diferenciais", "probabilidade não é apresentada como certeza"], "importance": CORE, "minimum_depth_weight": 1.6},
                {"node_id": "diagnostic_path", "role": UniversalNodeRole.self_diagnosis, "title": "Construir uma sequência de diagnóstico", "purpose": "Ordenar testes do mais seguro e informativo ao mais invasivo ou caro.", "question": "Em qual ordem o leitor deve testar as hipóteses sobre {topic}?", "criteria": ["sequência minimiza risco", "cada teste leva a uma decisão"], "importance": CORE, "minimum_depth_weight": 1.4},
                {"node_id": "corrections", "role": UniversalNodeRole.corrections, "title": "Aplicar correções específicas", "purpose": "Associar cada diagnóstico confirmado a uma correção, condição de uso e risco.", "question": "Qual correção é sustentada para cada causa confirmada em {topic}?", "criteria": ["correção ligada à causa", "riscos e limites informados"], "importance": CORE, "minimum_depth_weight": 1.6},
                {"node_id": "verification", "role": UniversalNodeRole.verification, "title": "Verificar se a correção funcionou", "purpose": "Definir sinais observáveis, janela legítima e critérios de encerramento ou escalada.", "question": "Como verificar se a correção de {topic} funcionou e quando escalar o problema?", "criteria": ["resultado observável", "critério de escalada definido"], "importance": CORE},
                {"node_id": "prevention", "role": UniversalNodeRole.prevention, "title": "Reduzir recorrência", "purpose": "Explicar prevenção somente quando ela decorrer das causas confirmadas.", "question": "Quais práticas sustentadas reduzem a recorrência do problema em {topic}?", "criteria": ["prevenção deriva da causa", "não há promessa absoluta"], "applicability": C, "importance": SUPPORT},
                {"node_id": "closing", "role": UniversalNodeRole.offer_bridge, "title": "Concluir pelo estado verificado", "purpose": "Retomar o diagnóstico e orientar a próxima ação ou ajuda especializada.", "question": "Qual próxima ação é coerente depois de diagnosticar e verificar {topic}?", "criteria": ["encerramento após verificação", "nenhuma alegação nova"], "research_required": False, "applicability": OPTIONAL, "importance": PERIPHERAL, "minimum_depth_weight": 0.25, "maximum_depth_weight": 0.6},
            ],
            EditorialArchitectureType.commercial_education: [
                {"node_id": "problem_context", "role": UniversalNodeRole.problem_context, "title": "Explicar o problema antes da oferta", "purpose": "Delimitar a necessidade do leitor e as consequências relevantes sem dramatização promocional.", "question": "Qual problema ou decisão torna {topic} relevante para o leitor?", "criteria": ["problema definido sem exagero", "necessidade conectada ao contexto"], "importance": CORE},
                {"node_id": "decision_criteria", "role": UniversalNodeRole.decision_criteria, "title": "Ensinar critérios de avaliação", "purpose": "Dar ao leitor critérios independentes para avaliar soluções.", "question": "Quais critérios permitem avaliar soluções relacionadas a {topic}?", "criteria": ["critérios independentes da marca", "limites e trade-offs incluídos"], "importance": CORE, "minimum_depth_weight": 1.2},
                {"node_id": "solution_landscape", "role": UniversalNodeRole.landscape, "title": "Mapear categorias de solução", "purpose": "Apresentar alternativas reais antes de posicionar a oferta.", "question": "Quais categorias de solução existem para {topic} e como diferem?", "criteria": ["alternativas reais cobertas", "oferta não monopoliza o panorama"], "importance": CORE, "minimum_depth_weight": 1.2},
                {"node_id": "solution_fit", "role": UniversalNodeRole.solution_fit, "title": "Explicar adequação e limites", "purpose": "Mostrar para quem cada solução serve, quando não serve e quais requisitos possui.", "question": "Para quais cenários cada solução de {topic} é adequada e quais limitações possui?", "criteria": ["adequação contextual", "limitações explícitas"], "importance": CORE, "minimum_depth_weight": 1.4},
                {"node_id": "evidence_and_tradeoffs", "role": UniversalNodeRole.comparison, "title": "Sustentar benefícios e trade-offs", "purpose": "Separar benefício demonstrado, característica, inferência e alegação de marca.", "question": "Quais benefícios, custos, riscos e trade-offs de {topic} são sustentados por evidência?", "criteria": ["alegações classificadas", "trade-offs presentes"], "importance": CORE, "minimum_depth_weight": 1.5},
                {"node_id": "objections", "role": UniversalNodeRole.objections, "title": "Responder objeções com honestidade", "purpose": "Tratar dúvidas relevantes sem manipulação ou promessa absoluta.", "question": "Quais objeções legítimas sobre {topic} precisam ser respondidas e quais respostas são sustentadas?", "criteria": ["objeções reais tratadas", "respostas não promocionais"], "applicability": C, "importance": SUPPORT},
                {"node_id": "decision", "role": UniversalNodeRole.recommendation_logic, "title": "Permitir uma decisão informada", "purpose": "Entregar uma lógica de escolha coerente com os critérios apresentados.", "question": "Como o leitor decide se uma solução relacionada a {topic} é adequada ao seu contexto?", "criteria": ["decisão deriva dos critérios", "não há pressão artificial"], "importance": CORE},
                {"node_id": "closing", "role": UniversalNodeRole.offer_bridge, "title": "Conectar a oferta depois da educação", "purpose": "Apresentar a oferta como uma opção coerente, com escopo e CTA claros, sem criar fatos novos.", "question": "Como conectar a oferta à decisão sobre {topic} sem substituir a análise?", "criteria": ["oferta posterior à educação", "CTA coerente e não enganoso"], "research_required": False, "importance": PERIPHERAL, "minimum_depth_weight": 0.4, "maximum_depth_weight": 0.8},
            ],
        }


class EditorialHierarchyGate:
    @staticmethod
    def validate_plan(plan: dict, hierarchy: EditorialHierarchyContract) -> HierarchyValidationReport:
        blockers: list[str] = []
        warnings: list[str] = []
        nodes = {node.node_id: node for node in hierarchy.nodes}
        question_nodes: set[str] = set()
        for question in plan.get("questions", []):
            ids = [str(value) for value in question.get("node_ids", [])]
            unknown = sorted(set(ids) - set(nodes))
            if unknown:
                blockers.append("question_unknown_nodes:" + ",".join(unknown))
            question_nodes.update(ids)
        required_research = {
            node.node_id
            for node in hierarchy.nodes
            if node.research_required and node.applicability == NodeApplicability.required
        }
        missing_research = sorted(required_research - question_nodes)
        if missing_research:
            blockers.append("research_nodes_missing:" + ",".join(missing_research))

        sections = ((plan.get("editorial_blueprint") or {}).get("sections") or [])
        section_nodes: set[str] = set()
        first_positions: dict[str, int] = {}
        target_words: dict[str, int] = {}
        for position, section in enumerate(sections):
            ids = [str(value) for value in section.get("node_ids", [])]
            unknown = sorted(set(ids) - set(nodes))
            if unknown:
                blockers.append("section_unknown_nodes:" + ",".join(unknown))
            for node_id in ids:
                section_nodes.add(node_id)
                first_positions.setdefault(node_id, position)
                target_words[node_id] = target_words.get(node_id, 0) + int(
                    section.get("target_words") or 0
                )
        required_sections = {
            node.node_id
            for node in hierarchy.nodes
            if node.applicability == NodeApplicability.required
        }
        missing_sections = sorted(required_sections - section_nodes)
        if missing_sections:
            blockers.append("blueprint_nodes_missing:" + ",".join(missing_sections))
        EditorialHierarchyGate._validate_order(
            hierarchy, first_positions, blockers, prefix="blueprint"
        )
        EditorialHierarchyGate._validate_depth(
            hierarchy, target_words, blockers, warnings, prefix="blueprint"
        )
        return HierarchyValidationReport(
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(dict.fromkeys(warnings)),
            covered_node_ids=tuple(sorted(section_nodes)),
            missing_node_ids=tuple(missing_sections),
            first_positions=first_positions,
            word_counts=target_words,
        )

    @staticmethod
    def validate_draft(draft: dict, hierarchy: EditorialHierarchyContract) -> HierarchyValidationReport:
        import re

        blockers: list[str] = []
        warnings: list[str] = []
        nodes = {node.node_id: node for node in hierarchy.nodes}
        represented: set[str] = set()
        first_positions: dict[str, int] = {}
        word_counts: dict[str, int] = {}
        untagged: list[int] = []
        for fallback_position, block in enumerate(draft.get("blocks", [])):
            position = int(block.get("position", fallback_position))
            ids = [str(value) for value in block.get("node_ids", [])]
            if block.get("type") != "h1" and not ids:
                untagged.append(position)
            unknown = sorted(set(ids) - set(nodes))
            if unknown:
                blockers.append("draft_unknown_nodes:" + ",".join(unknown))
            text = " ".join(
                str(sentence.get("text") or "") for sentence in block.get("sentences", [])
            )
            words = len(re.findall(r"\b[\wÀ-ÿ'-]+\b", text))
            for node_id in ids:
                represented.add(node_id)
                first_positions.setdefault(node_id, position)
                word_counts[node_id] = word_counts.get(node_id, 0) + words
        if untagged:
            blockers.append("draft_untagged_blocks:" + ",".join(map(str, untagged[:20])))
        required = {
            node.node_id
            for node in hierarchy.nodes
            if node.applicability == NodeApplicability.required
        }
        missing = sorted(required - represented)
        if missing:
            blockers.append("draft_nodes_missing:" + ",".join(missing))
        EditorialHierarchyGate._validate_order(
            hierarchy, first_positions, blockers, prefix="draft"
        )
        EditorialHierarchyGate._validate_depth(
            hierarchy, word_counts, blockers, warnings, prefix="draft"
        )
        closing_position = first_positions.get(hierarchy.closing_node_id)
        if closing_position is not None:
            unfinished = [
                node.node_id
                for node in hierarchy.nodes
                if node.node_id != hierarchy.closing_node_id
                and node.applicability == NodeApplicability.required
                and first_positions.get(node.node_id, 10**9) > closing_position
            ]
            if unfinished:
                blockers.append("closing_before_required_nodes:" + ",".join(unfinished))
        return HierarchyValidationReport(
            blockers=tuple(dict.fromkeys(blockers)),
            warnings=tuple(dict.fromkeys(warnings)),
            covered_node_ids=tuple(sorted(represented)),
            missing_node_ids=tuple(missing),
            first_positions=first_positions,
            word_counts=word_counts,
        )

    @staticmethod
    def _validate_order(
        hierarchy: EditorialHierarchyContract,
        first_positions: dict[str, int],
        blockers: list[str],
        *,
        prefix: str,
    ) -> None:
        represented = [
            node for node in hierarchy.nodes if node.node_id in first_positions
        ]
        positions = [first_positions[node.node_id] for node in represented]
        if positions != sorted(positions):
            blockers.append(f"{prefix}_node_order_invalid")
        for node in represented:
            for dependency in node.depends_on:
                if dependency in first_positions and (
                    first_positions[dependency] >= first_positions[node.node_id]
                ):
                    blockers.append(
                        f"{prefix}_dependency_invalid:{dependency}>{node.node_id}"
                    )

    @staticmethod
    def _validate_depth(
        hierarchy: EditorialHierarchyContract,
        word_counts: dict[str, int],
        blockers: list[str],
        warnings: list[str],
        *,
        prefix: str,
    ) -> None:
        normalized_depth: dict[str, float] = {}
        for node in hierarchy.nodes:
            if node.node_id not in word_counts:
                continue
            normalized_depth[node.node_id] = word_counts[node.node_id] / max(
                node.minimum_depth_weight, 0.1
            )
            if (
                node.maximum_depth_weight is not None
                and node.importance == NodeImportance.peripheral
            ):
                core_words = [
                    word_counts.get(item.node_id, 0)
                    for item in hierarchy.nodes
                    if item.importance == NodeImportance.core
                    and word_counts.get(item.node_id, 0) > 0
                ]
                if core_words and word_counts[node.node_id] > (
                    min(core_words) * node.maximum_depth_weight
                ):
                    blockers.append(f"{prefix}_peripheral_overdeveloped:{node.node_id}")
        core_depth = [
            normalized_depth[node.node_id]
            for node in hierarchy.nodes
            if node.importance == NodeImportance.core
            and node.node_id in normalized_depth
        ]
        peripheral_depth = [
            normalized_depth[node.node_id]
            for node in hierarchy.nodes
            if node.importance == NodeImportance.peripheral
            and node.node_id in normalized_depth
        ]
        if core_depth and peripheral_depth and max(peripheral_depth) > min(core_depth) * 1.25:
            blockers.append(f"{prefix}_hierarchy_depth_inverted")
        if core_depth and min(core_depth) < 40:
            warnings.append(f"{prefix}_core_node_shallow")


def hierarchy_node_ids(items: Iterable[dict]) -> set[str]:
    return {
        str(node_id)
        for item in items
        for node_id in item.get("node_ids", [])
        if str(node_id)
    }
