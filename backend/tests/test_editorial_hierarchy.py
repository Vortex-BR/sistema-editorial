from __future__ import annotations

import re

import pytest

from app.schemas.editorial_hierarchy import (
    EditorialArchitectureType,
    NodeApplicability,
    NodeImportance,
)
from app.schemas.editorial_v3 import EditorialContentTypeV3
from app.services.editorial_hierarchy import (
    EditorialHierarchyGate,
    UniversalEditorialHierarchyBuilder,
)
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.research_planner import V3ResearchPlanningService


CASES = [
    (
        EditorialArchitectureType.procedural_decision_guide,
        "troca de óleo do carro",
        "Leitor sem um procedimento seguro para iniciar a manutenção.",
        "Leitor capaz de executar e verificar a troca sem vazamentos.",
    ),
    (
        EditorialArchitectureType.explanatory_guide,
        "portabilidade numérica",
        "Leitor que ainda não compreende como a portabilidade funciona.",
        "Leitor capaz de entender o processo, seus limites e efeitos práticos.",
    ),
    (
        EditorialArchitectureType.comparison,
        "planos de internet residencial",
        "Leitor sem critérios para comparar as alternativas disponíveis.",
        "Leitor capaz de escolher uma alternativa coerente com seu uso.",
    ),
    (
        EditorialArchitectureType.troubleshooting,
        "bateria do notebook não carrega",
        "Leitor que observa o sintoma, mas não conhece suas causas possíveis.",
        "Leitor capaz de diagnosticar, corrigir e verificar o resultado.",
    ),
    (
        EditorialArchitectureType.commercial_education,
        "backup gerenciado para pequenas empresas",
        "Leitor que reconhece o risco, mas não sabe avaliar soluções.",
        "Leitor capaz de decidir se o serviço é adequado ao seu contexto.",
    ),
]


def build_case(case):
    architecture, topic, start, final = case
    return UniversalEditorialHierarchyBuilder.build(
        topic=topic,
        architecture_type=architecture,
        reader_start_state=start,
        reader_final_state=final,
    )


def required_nodes(contract):
    return [
        node
        for node in contract.nodes
        if node.applicability == NodeApplicability.required
    ]


def make_valid_plan(contract):
    research_nodes = [
        node
        for node in contract.nodes
        if node.research_required
        and node.applicability == NodeApplicability.required
    ]
    questions = [
        {
            "question": node.central_question,
            "node_ids": [node.node_id],
        }
        for node in research_nodes
    ]
    sections = []
    for node in required_nodes(contract):
        target_words = 80
        if node.importance == NodeImportance.core:
            target_words = max(100, round(80 * node.minimum_depth_weight))
        elif node.importance == NodeImportance.peripheral:
            target_words = 30
        sections.append(
            {
                "heading": node.title_function,
                "node_ids": [node.node_id],
                "target_words": target_words,
            }
        )
    return {
        "questions": questions,
        "editorial_blueprint": {"sections": sections},
    }


def make_valid_draft(contract):
    blocks = [
        {
            "type": "h1",
            "position": 0,
            "node_ids": [],
            "sentences": [{"text": contract.topic}],
        }
    ]
    position = 1
    for node in required_nodes(contract):
        word_count = 90
        if node.importance == NodeImportance.core:
            word_count = max(100, round(90 * node.minimum_depth_weight))
        elif node.importance == NodeImportance.peripheral:
            word_count = 25
        text = " ".join(f"termo{index}" for index in range(word_count))
        blocks.append(
            {
                "type": "paragraph",
                "position": position,
                "node_ids": [node.node_id],
                "sentences": [{"text": text}],
            }
        )
        position += 1
    return {"blocks": blocks}


@pytest.mark.parametrize("case", CASES, ids=lambda value: str(value[0].value) if isinstance(value, tuple) else str(value))
def test_universal_hierarchy_is_domain_independent_and_ordered(case):
    contract = build_case(case)

    assert contract.metadata["domain_specific"] is False
    assert [node.sequence for node in contract.nodes] == list(
        range(1, len(contract.nodes) + 1)
    )
    assert contract.closing_node_id == contract.nodes[-1].node_id
    positions = {node.node_id: node.sequence for node in contract.nodes}
    assert all(
        positions[dependency] < node.sequence
        for node in contract.nodes
        for dependency in node.depends_on
    )

    serialized = str(contract.model_dump(mode="json")).casefold()
    assert not re.search(r"germina|radícula|jiffy|papel[- ]toalha|semente", serialized)


@pytest.mark.parametrize("case", CASES)
def test_valid_cross_domain_plans_pass_the_deterministic_gate(case):
    contract = build_case(case)
    report = EditorialHierarchyGate.validate_plan(make_valid_plan(contract), contract)

    assert report.passed, report.blockers
    assert not report.missing_node_ids


def test_plan_gate_blocks_missing_research_and_blueprint_nodes():
    contract = build_case(CASES[1])
    plan = make_valid_plan(contract)
    missing_id = required_nodes(contract)[1].node_id
    plan["questions"] = [
        question
        for question in plan["questions"]
        if missing_id not in question["node_ids"]
    ]
    plan["editorial_blueprint"]["sections"] = [
        section
        for section in plan["editorial_blueprint"]["sections"]
        if missing_id not in section["node_ids"]
    ]

    report = EditorialHierarchyGate.validate_plan(plan, contract)

    assert not report.passed
    assert any(item.startswith("research_nodes_missing:") for item in report.blockers)
    assert any(item.startswith("blueprint_nodes_missing:") for item in report.blockers)


def test_plan_gate_blocks_inverted_node_order():
    contract = build_case(CASES[2])
    plan = make_valid_plan(contract)
    plan["editorial_blueprint"]["sections"][0], plan["editorial_blueprint"]["sections"][1] = (
        plan["editorial_blueprint"]["sections"][1],
        plan["editorial_blueprint"]["sections"][0],
    )

    report = EditorialHierarchyGate.validate_plan(plan, contract)

    assert not report.passed
    assert "blueprint_node_order_invalid" in report.blockers


@pytest.mark.parametrize("case", CASES)
def test_valid_cross_domain_drafts_pass_the_deterministic_gate(case):
    contract = build_case(case)
    report = EditorialHierarchyGate.validate_draft(make_valid_draft(contract), contract)

    assert report.passed, report.blockers
    assert not report.missing_node_ids


def test_draft_gate_blocks_missing_core_node():
    contract = build_case(CASES[3])
    draft = make_valid_draft(contract)
    missing_id = next(
        node.node_id
        for node in contract.nodes
        if node.importance == NodeImportance.core
        and node.applicability == NodeApplicability.required
    )
    draft["blocks"] = [
        block for block in draft["blocks"] if missing_id not in block.get("node_ids", [])
    ]

    report = EditorialHierarchyGate.validate_draft(draft, contract)

    assert not report.passed
    assert any(item.startswith("draft_nodes_missing:") for item in report.blockers)


def test_draft_gate_blocks_peripheral_section_deeper_than_core_content():
    contract = build_case(CASES[1])
    draft = make_valid_draft(contract)
    closing = next(node for node in contract.nodes if node.node_id == contract.closing_node_id)
    draft["blocks"].append(
        {
            "type": "paragraph",
            "position": len(draft["blocks"]),
            "node_ids": [closing.node_id],
            "sentences": [
                {"text": " ".join(f"periferico{index}" for index in range(500))}
            ],
        }
    )

    report = EditorialHierarchyGate.validate_draft(draft, contract)

    assert not report.passed
    assert any("peripheral_overdeveloped" in item for item in report.blockers)
    assert "draft_hierarchy_depth_inverted" in report.blockers


def test_v3_generic_contract_uses_universal_hierarchy_without_fake_methods():
    data = KnowledgeContractInput(
        topic="como funciona a portabilidade numérica",
        reader_start_state="Leitor que ainda não compreende as etapas e limitações do serviço.",
        reader_final_state="Leitor capaz de compreender o mecanismo e seus efeitos práticos.",
        article_promise="Explicar o funcionamento, os limites e a aplicação prática da portabilidade.",
        scope_limit="Encerrar após explicar a aplicação prática, sem comparar operadoras específicas.",
        content_type=EditorialContentTypeV3.explanatory_guide,
        requires_method_comparison=False,
        requires_external_reference_per_method=False,
    )

    contract = KnowledgeContractBuilder().build(data)
    plan = V3ResearchPlanningService().build(contract)

    assert contract.requires_method_comparison is False
    assert contract.required_method_labels == []
    assert "universal_hierarchy" in contract.metadata
    assert all(not node.node_id.startswith("method_") for node in contract.nodes)
    assert all("cada método" not in condition.casefold() for condition in plan.stop_conditions)
    assert all(
        "passo a passo materiais sinais erros" not in query.casefold()
        for task in plan.tasks
        for query in task.queries
    )


def test_v3_procedural_contract_preserves_detailed_graph_and_universal_roles():
    data = KnowledgeContractInput(
        topic="troca de óleo do carro",
        reader_start_state="Leitor que precisa compreender o procedimento antes de iniciar a manutenção.",
        reader_final_state="Leitor capaz de confirmar o nível correto e a ausência de vazamentos.",
        article_promise="Comparar abordagens, orientar a escolha e explicar a execução até a verificação final.",
        scope_limit="Encerrar na verificação do nível e não avançar para outros serviços automotivos.",
        content_type=EditorialContentTypeV3.procedural_decision_guide,
        requires_method_comparison=True,
        requires_external_reference_per_method=True,
        required_method_labels=("drenagem pelo cárter", "extração por sucção"),
    )

    contract = KnowledgeContractBuilder().build(data)
    plan = V3ResearchPlanningService().build(contract)

    assert len(contract.nodes) == 13
    assert {node.node_id for node in contract.nodes} >= {
        "subject_foundation",
        "method_inventory",
        "method_execution",
        "progress_confirmation",
        "troubleshooting",
        "final_outcome_confirmation",
    }
    assert all(node.universal_role is not None for node in contract.nodes)
    assert any("cada abordagem" in condition.casefold() for condition in plan.stop_conditions)
