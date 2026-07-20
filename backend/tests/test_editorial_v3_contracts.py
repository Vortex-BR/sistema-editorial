import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.editorial_v3 import (
    ConclusionStatus,
    ContentKnowledgeContract,
    DecisionMatrix,
    DecisionRule,
    ExternalReference,
    GapResolutionStatus,
    KnowledgeGap,
    MethodDossier,
    ProcedureStep,
    SectionDossier,
    SourceRole,
    SourceUsagePolicy,
)
from app.services.editorial_v3.knowledge_completeness import (
    KnowledgeCompletenessService,
)
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)


def contract_input():
    return KnowledgeContractInput(
        topic="germinação de sementes de cannabis",
        reader_start_state=(
            "Leitor que precisa compreender a semente e os métodos antes de agir."
        ),
        reader_final_state=(
            "Leitor capaz de reconhecer a emergência da plântula no substrato."
        ),
        article_promise=(
            "Explicar a semente, comparar métodos, orientar a escolha e acompanhar "
            "o processo até a emergência da plântula."
        ),
        scope_limit=(
            "O guia termina na emergência da plântula e não cobre o cultivo posterior."
        ),
        jurisdiction="Brasil; validar legislação e restrições aplicáveis",
    )


def test_builder_creates_required_procedural_order_before_research():
    contract = KnowledgeContractBuilder().build(contract_input())

    assert [node.node_id for node in contract.nodes] == [
        "subject_foundation",
        "method_inventory",
        "process_requirements",
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
    ]
    execution = next(
        node for node in contract.nodes if node.node_id == "method_execution"
    )
    assert execution.branches
    assert execution.convergence_node_id == "progress_confirmation"
    assert contract.reader_final_state.endswith("substrato.")


def test_builder_checksum_is_deterministic():
    builder = KnowledgeContractBuilder()

    first = builder.build(contract_input())
    second = builder.build(contract_input())

    assert builder.checksum(first) == builder.checksum(second)


def test_contract_from_project_uses_rich_v3_brief():
    project = SimpleNamespace(
        topic="Tema procedural",
        briefing={
            "reader_start_state": "Leitor no início do processo e sem modelo mental completo.",
            "reader_final_state": "Leitor capaz de reconhecer o resultado final observado.",
            "article_promise": "Ensinar as alternativas, a escolha e o processo completo até o resultado.",
            "scope_limit": "Encerrar exatamente no resultado final definido pelo briefing.",
            "jurisdiction": "Mercado informado pelo projeto",
            "editorial_content_type": "procedural_decision_guide",
            "requires_method_comparison": True,
            "requires_external_reference_per_method": True,
            "required_methods": ["método direto", "papel-toalha"],
        },
    )

    result = KnowledgeContractInput.from_project(project)

    assert result.reader_start_state.startswith("Leitor no início")
    assert result.reader_final_state.endswith("observado.")
    assert result.jurisdiction == "Mercado informado pelo projeto"


def test_legacy_project_inputs_are_bounded_and_audited_for_safe_reruns():
    project = SimpleNamespace(
        topic="Tema muito longo. " + ("contexto " * 80),
        briefing={
            "primary_keyword": "termo focal da pesquisa",
            "reader_start_state": "estado inicial " * 200,
            "reader_final_state": "estado final " * 200,
            "article_promise": "promessa " * 500,
            "scope_limit": "limite " * 400,
            "editorial_content_type": "explanatory_guide",
        },
    )

    data = KnowledgeContractInput.from_project(project)
    contract = KnowledgeContractBuilder().build(data)

    assert len(contract.topic) <= 380
    assert len(contract.reader_start_state) <= 1_000
    assert len(contract.reader_final_state) <= 1_000
    assert len(contract.article_promise) <= 3_000
    assert len(contract.scope_limit) <= 2_000
    assert contract.metadata["search_subject"].startswith("termo focal da pesquisa")
    assert "Tema muito longo" in contract.metadata["search_subject"]
    assert len(contract.metadata["search_subject"]) <= 240
    assert "topic:truncated" in " ".join(contract.metadata["input_normalizations"])


def test_contract_rejects_reordered_dependency():
    contract = KnowledgeContractBuilder().build(contract_input())
    payload = contract.model_dump(mode="json")
    payload["nodes"][1]["depends_on"] = ["method_selection"]

    with pytest.raises(ValidationError, match="must precede"):
        ContentKnowledgeContract.model_validate(payload)


def test_contract_rejects_dependency_without_matching_edge():
    contract = KnowledgeContractBuilder().build(contract_input())
    payload = contract.model_dump(mode="json")
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if not (
            edge["from_node_id"] == "method_comparison"
            and edge["to_node_id"] == "method_selection"
        )
    ]

    with pytest.raises(ValidationError, match="missing dependency edges"):
        ContentKnowledgeContract.model_validate(payload)


def test_contract_rejects_missing_method_selection():
    contract = KnowledgeContractBuilder().build(contract_input())
    payload = contract.model_dump(mode="json")
    payload["nodes"] = [
        node for node in payload["nodes"] if node["node_id"] != "method_selection"
    ]
    for index, node in enumerate(payload["nodes"], start=1):
        node["sequence"] = index
        node["depends_on"] = [
            dependency
            for dependency in node["depends_on"]
            if dependency != "method_selection"
        ]
    payload["edges"] = [
        edge
        for edge in payload["edges"]
        if "method_selection" not in {edge["from_node_id"], edge["to_node_id"]}
    ]

    with pytest.raises(ValidationError, match="missing required nodes"):
        ContentKnowledgeContract.model_validate(payload)


def _step(method_id: str) -> ProcedureStep:
    evidence = uuid.uuid4()
    return ProcedureStep(
        step_id=f"{method_id}_step_1",
        sequence=1,
        action="Executar a ação definida e sustentada para esta etapa do método.",
        purpose="Produzir o estado intermediário necessário para avançar com segurança.",
        execution_details=["Aplicar os detalhes operacionais aprovados no dossiê."],
        expected_observations=["Observar o sinal documentado de progresso."],
        warning_signs=[
            "Interromper e reavaliar se surgir um sinal documentado de falha."
        ],
        common_mistakes=[],
        completion_condition="Avançar somente quando o critério observável for atendido.",
        next_step_id=None,
        evidence_ids=[evidence],
    )


def _reference(method_id: str) -> ExternalReference:
    return ExternalReference(
        method_id=method_id,
        url=f"https://example.org/{method_id}",
        anchor_text=f"Guia técnico aprofundado sobre {method_id}",
        title=f"Procedimento completo para {method_id}",
        author="Equipe técnica",
        publisher="Example Research",
        source_role=SourceRole.technical_procedural,
        source_usage_policy=SourceUsagePolicy.authoritative_evidence,
        is_ecommerce_domain=False,
        is_transactional_page=False,
        content_match_score=0.95,
        procedural_depth_score=0.9,
        verified_at="2026-07-17T18:00:00Z",
        status="approved",
    )


def _method(method_id: str, *, transfer_required: bool) -> MethodDossier:
    return MethodDossier(
        method_id=method_id,
        name=f"Método {method_id}",
        definition="Descrição operacional sustentada do método e do seu ponto de partida.",
        mechanism_summary="Síntese do mecanismo relevante sem extrapolar a evidência disponível.",
        best_fit_conditions=["Cenário em que o método é adequadamente sustentado."],
        limitations=["Limitação documentada que precisa ser considerada."],
        required_materials=["Material documentado"],
        preparation=["Preparação documentada"],
        steps=[_step(method_id)],
        outcome_confirmation=["Sinal observável documentado de conclusão."],
        transfer_required=transfer_required,
        transfer_decision=(
            ["Critério observável documentado para realizar a transição."]
            if transfer_required
            else []
        ),
        post_method_monitoring=[
            "Condição documentada a acompanhar até o resultado final."
        ],
        external_reference=_reference(method_id),
    )


def _section(node_id: str) -> SectionDossier:
    return SectionDossier(
        section_id=node_id,
        reader_state_before="O leitor ainda precisa compreender ou executar esta parte do fluxo.",
        reader_state_after="O leitor alcança o estado necessário para avançar à próxima parte.",
        section_purpose="Cumprir a função editorial definida no contrato de conhecimento.",
        central_question="Qual resposta sustentada permite avançar neste ponto?",
        core_answer="Resposta sintetizada a partir das evidências aprovadas e das condições aplicáveis.",
        allowed_claim_ids=[uuid.uuid4()],
        transition_logic="Conectar o estado alcançado ao próximo nó sem introduzir fatos novos.",
    )


def test_knowledge_completeness_blocks_writer_when_dossiers_are_missing():
    contract = KnowledgeContractBuilder().build(contract_input())

    report = KnowledgeCompletenessService().evaluate(
        contract,
        methods=[],
        sections=[],
        gaps=[],
        decision_matrix=None,
    )

    assert report.status == "blocked"
    assert report.missing_node_ids
    assert any("pelo menos dois métodos" in blocker for blocker in report.blockers)


def test_knowledge_completeness_passes_only_complete_dossiers():
    contract = KnowledgeContractBuilder().build(contract_input())
    methods = [
        _method("method_alpha", transfer_required=True),
        _method("method_beta", transfer_required=False),
    ]
    rule_evidence = uuid.uuid4()
    matrix = DecisionMatrix(
        dimensions=["necessidade de transição", "facilidade de observação"],
        method_ids=[method.method_id for method in methods],
        rules=[
            DecisionRule(
                condition="Quando o cenário exige uma transição observável entre estágios.",
                supported_direction="Considerar o método cujo dossiê documenta essa transição.",
                method_ids=["method_alpha"],
                evidence_ids=[rule_evidence],
                conclusion_status=ConclusionStatus.conditional,
            )
        ],
        universal_best_method=None,
    )

    report = KnowledgeCompletenessService().evaluate(
        contract,
        methods=methods,
        sections=[_section(node.node_id) for node in contract.nodes],
        gaps=[],
        decision_matrix=matrix,
    )

    assert report.status == "passed"
    assert report.score == 1.0


def test_unpersisted_open_essential_gap_still_blocks_completeness():
    contract = KnowledgeContractBuilder().build(contract_input())
    gap = KnowledgeGap(
        knowledge_node_id="method_selection",
        gap_type="condition_dependent",
        description="A decisão ainda depende de condições que não foram resolvidas.",
        essential=True,
        status=GapResolutionStatus.open,
    )

    report = KnowledgeCompletenessService().evaluate(
        contract,
        methods=[],
        sections=[],
        gaps=[gap],
        decision_matrix=None,
    )

    assert report.status == "blocked"
    assert "Existem lacunas essenciais não resolvidas" in report.blockers


def test_conditional_gap_requires_explicit_limits():
    with pytest.raises(ValidationError, match="Conditional resolutions"):
        KnowledgeGap(
            gap_id=uuid.uuid4(),
            knowledge_node_id="method_selection",
            gap_type="condition_dependent",
            description="A resposta muda de acordo com o método e as condições observadas.",
            essential=True,
            status=GapResolutionStatus.resolved_conditionally,
            allowed_conclusion="A escolha deve ser apresentada de forma condicional.",
        )


def test_method_dossier_rejects_transfer_without_criteria():
    payload = _method("method_gamma", transfer_required=True).model_dump(mode="json")
    payload["transfer_decision"] = []

    with pytest.raises(ValidationError, match="observable criteria"):
        MethodDossier.model_validate(payload)


def test_contract_from_project_rejects_unknown_v3_content_type():
    project = SimpleNamespace(
        topic="Tema procedural",
        editorial_pipeline_version="v3",
        briefing={
            "reader_start_state": "Leitor no início e sem modelo mental completo.",
            "reader_final_state": "Leitor capaz de reconhecer o resultado final observado.",
            "article_promise": "Explicar fundamentos, escolhas e execução até o resultado.",
            "scope_limit": "Encerrar exatamente no resultado final definido.",
            "editorial_content_type": "typo_content_type",
        },
    )

    with pytest.raises(ValueError, match="Unsupported Editorial V3 content type"):
        KnowledgeContractInput.from_project(project)


def test_contract_from_project_rejects_non_boolean_procedural_flags():
    project = SimpleNamespace(
        topic="Tema procedural",
        editorial_pipeline_version="v3",
        briefing={
            "reader_start_state": "Leitor no início e sem modelo mental completo.",
            "reader_final_state": "Leitor capaz de reconhecer o resultado final observado.",
            "article_promise": "Explicar fundamentos, escolhas e execução até o resultado.",
            "scope_limit": "Encerrar exatamente no resultado final definido.",
            "editorial_content_type": "procedural_decision_guide",
            "required_methods": ["método direto", "papel-toalha"],
            "requires_method_comparison": "false",
        },
    )

    with pytest.raises(ValueError, match="must be boolean"):
        KnowledgeContractInput.from_project(project)


def test_decision_matrix_rejects_universal_best_method():
    with pytest.raises(ValidationError, match="cannot declare a universal best method"):
        DecisionMatrix(
            dimensions=["complexidade", "necessidade de transição"],
            method_ids=["method_alpha", "method_beta"],
            rules=[
                DecisionRule(
                    condition="Quando o contexto exige menor manipulação durante o processo.",
                    supported_direction="Considerar o método sustentado para esse contexto específico.",
                    method_ids=["method_alpha"],
                    evidence_ids=[uuid.uuid4()],
                    conclusion_status=ConclusionStatus.conditional,
                )
            ],
            universal_best_method="method_alpha",
        )


def test_unpersisted_essential_gap_reduces_completeness_score():
    contract = KnowledgeContractBuilder().build(contract_input())
    gap = KnowledgeGap(
        knowledge_node_id="method_selection",
        gap_type="condition_dependent",
        description="A decisão ainda depende de condições que não foram resolvidas.",
        essential=True,
        status=GapResolutionStatus.open,
    )

    report = KnowledgeCompletenessService().evaluate(
        contract,
        methods=[
            _method("method_alpha", transfer_required=True),
            _method("method_beta", transfer_required=False),
        ],
        sections=[_section(node.node_id) for node in contract.nodes],
        gaps=[gap],
        decision_matrix=DecisionMatrix(
            dimensions=["necessidade de transição", "facilidade de observação"],
            method_ids=["method_alpha", "method_beta"],
            rules=[
                DecisionRule(
                    condition="Quando o cenário exige uma transição observável entre estágios.",
                    supported_direction="Considerar o método cujo dossiê documenta essa transição.",
                    method_ids=["method_alpha"],
                    evidence_ids=[uuid.uuid4()],
                    conclusion_status=ConclusionStatus.conditional,
                )
            ],
        ),
    )

    assert report.status == "blocked"
    assert report.score == 0.8


def test_v3_method_inventory_rejects_duplicate_labels_across_methods():
    import pytest
    from pydantic import ValidationError

    from app.schemas.editorial_v3_runtime import MethodInventoryOutput

    with pytest.raises(ValidationError, match="assigned to more than one method"):
        MethodInventoryOutput.model_validate(
            {
                "methods": [
                    {
                        "method_id": "method_a",
                        "name": "Método direto",
                        "aliases": ["Direto no meio"],
                        "distinguishing_feature": "O processo começa no meio final sem uma transferência intermediária.",
                        "equivalent_variations": [],
                        "supporting_claim_keys": ["claim_a"],
                    },
                    {
                        "method_id": "method_b",
                        "name": "Método alternativo",
                        "aliases": ["Método direto"],
                        "distinguishing_feature": "O processo usa um meio intermediário antes da etapa seguinte.",
                        "equivalent_variations": [],
                        "supporting_claim_keys": ["claim_b"],
                    },
                ],
                "rejected_duplicates": [],
                "rationale": "Os métodos foram separados por diferenças operacionais sustentadas.",
            }
        )


def test_required_method_matching_is_accent_insensitive_and_uses_aliases():
    from app.schemas.editorial_v3_runtime import MethodInventoryItem
    from app.services.editorial_v3.method_coverage import required_method_matches

    methods = [
        MethodInventoryItem(
            method_id="papel_toalha",
            name="Método do papel-toalha",
            aliases=["papel toalha"],
            equivalent_variations=["guardanapo úmido"],
            distinguishing_feature="A semente permanece entre camadas úmidas antes da transferência.",
            supporting_claim_keys=["claim_papel_1", "claim_papel_2", "claim_papel_3"],
        ),
        MethodInventoryItem(
            method_id="copo_agua",
            name="Imersão inicial em copo com água",
            aliases=["copo d'água"],
            equivalent_variations=[],
            distinguishing_feature="A hidratação inicial acontece em água antes da etapa seguinte.",
            supporting_claim_keys=["claim_agua_1", "claim_agua_2", "claim_agua_3"],
        ),
    ]

    mapping, missing = required_method_matches(
        ["Papel toalha", "copo d’agua"],
        methods,
    )

    assert mapping == {
        "Papel toalha": "papel_toalha",
        "copo d’agua": "copo_agua",
    }
    assert missing == []
