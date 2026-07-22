"""Deterministic procedural-guide quality rubric for Editorial V3."""

from __future__ import annotations

import re

from app.schemas.editorial_v3 import (
    ContentKnowledgeContract,
    DecisionMatrix,
    MethodDossier,
    SectionDossier,
)
from app.schemas.editorial_v3_runtime import (
    ProceduralQualityEvaluation,
    V3DevelopmentReview,
    V3FactCheckReview,
    V3LanguageReview,
    V3WriterOutput,
)
from app.services.editorial_v3.prose_quality import analyze_editorial_prose
from app.services.editorial_v3.generation_context import active_node_ids

_TEMPLATE = re.compile(
    r"(?i)\b(?:é importante destacar|vale ressaltar|neste (?:artigo|guia)|"
    r"ao longo deste|em resumo|desempenha um papel fundamental|ao seguir essas dicas)\b"
)


def _block_content_sentences(block):
    """Support current structured blocks and legacy/test block doubles."""

    return getattr(block, "content_sentences", getattr(block, "sentences", []))


class ProceduralQualityService:
    def evaluate(
        self,
        *,
        contract: ContentKnowledgeContract,
        methods: list[MethodDossier],
        sections: list[SectionDossier],
        matrix: DecisionMatrix,
        draft: V3WriterOutput,
        development: V3DevelopmentReview,
        fact_check: V3FactCheckReview,
        language: V3LanguageReview,
        accepted_source_count: int,
        independent_source_count: int,
        minimum_word_count: int = 1800,
        maximum_word_count: int = 3500,
        minimum_steps_per_method: int = 1,
    ) -> ProceduralQualityEvaluation:
        blockers: list[str] = []
        warnings: list[str] = []
        factual_sentences = [
            sentence
            for block in draft.blocks
            for sentence in _block_content_sentences(block)
            if sentence.is_factual
        ]
        text = " ".join(sentence.text for block in draft.blocks for sentence in _block_content_sentences(block))
        markdown_chunks: list[str] = []
        for block in draft.blocks:
            block_texts = [sentence.text for sentence in _block_content_sentences(block)]
            combined = " ".join(block_texts)
            if block.type in {"h1", "h2", "h3"}:
                markdown_chunks.append(f"{'#' * int(block.type[1])} {combined}")
            elif block.type == "list":
                markdown_chunks.append("\n".join(f"- {item}" for item in block_texts))
            else:
                markdown_chunks.append(combined)
        method_labels = [
            label
            for method in methods
            for label in [
                str(getattr(method, "name", method.method_id)),
                *[str(item) for item in getattr(method, "aliases", [])],
            ]
            if label
        ]
        naturalness_metrics = analyze_editorial_prose(
            "\n\n".join(markdown_chunks),
            method_labels=method_labels,
        )
        word_count = len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", text))
        active_ids = set(active_node_ids(contract))
        active_nodes = [node for node in contract.nodes if node.node_id in active_ids]
        expected_section_order = [node.node_id for node in active_nodes]
        missing_sections = set(expected_section_order) - set(draft.covered_section_ids)
        if missing_sections:
            blockers.append("Seções obrigatórias ausentes: " + ", ".join(sorted(missing_sections)))
        section_first_positions: dict[str, int] = {}
        section_word_counts: dict[str, int] = {}
        for index, block in enumerate(draft.blocks):
            position = int(getattr(block, "position", index))
            section_first_positions.setdefault(block.section_id, position)
            section_word_counts[block.section_id] = section_word_counts.get(
                block.section_id, 0
            ) + sum(
                len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", sentence.text))
                for sentence in _block_content_sentences(block)
            )
        represented_positions = [
            section_first_positions[section_id]
            for section_id in expected_section_order
            if section_id in section_first_positions
        ]
        if represented_positions != sorted(represented_positions):
            blockers.append("A ordem editorial do contrato não foi respeitada")
        inventory_position = section_first_positions.get("method_inventory")
        requirements_position = section_first_positions.get("process_requirements")
        if (
            inventory_position is not None
            and requirements_position is not None
            and inventory_position > requirements_position
        ):
            blockers.append(
                "Os métodos foram apresentados depois das condições técnicas"
            )
        if "subject_foundation" in expected_section_order and section_word_counts.get(
            "subject_foundation", 0
        ) < 80:
            blockers.append("A orientação inicial está curta demais")
        if "method_inventory" in expected_section_order and section_word_counts.get(
            "method_inventory", 0
        ) < max(120, len(methods) * 30):
            blockers.append("A visão geral dos métodos está superficial")
        if "process_requirements" in expected_section_order and section_word_counts.get(
            "process_requirements", 0
        ) < 260:
            blockers.append(
                "As condições compartilhadas estão resumidas demais"
            )
        expected_method_ids = {method.method_id for method in methods}
        covered_method_ids = set(draft.covered_method_ids)
        missing_methods = expected_method_ids - covered_method_ids
        unknown_methods = covered_method_ids - expected_method_ids
        if missing_methods:
            blockers.append(
                "Métodos obrigatórios ausentes do artigo: "
                + ", ".join(sorted(missing_methods))
            )
        if unknown_methods:
            blockers.append(
                "O artigo referencia métodos não aprovados: "
                + ", ".join(sorted(unknown_methods))
            )
        if development.status != "passed" or not development.promise_fulfilled:
            blockers.append("A revisão de desenvolvimento não confirmou a promessa editorial")
        if fact_check.status != "passed":
            blockers.append("O fact-checking não aprovou todas as afirmações")
        if language.status != "passed":
            blockers.append("A edição de linguagem não aprovou a redação")
        if any(not sentence.evidence for sentence in factual_sentences):
            blockers.append("Existem afirmações factuais sem evidência")
        if any(method.external_reference is None for method in methods):
            blockers.append("Há método sem referência externa independente aprovada")
        if any(not method.steps for method in methods):
            blockers.append("Há método sem sequência procedural")
        for method in methods:
            method_blocks = [
                block for block in draft.blocks if block.method_id == method.method_id
            ]
            if not method_blocks:
                blockers.append(f"O método {method.method_id} não possui blocos próprios")
                continue
            if not any(block.type in {"h2", "h3"} for block in method_blocks):
                blockers.append(f"O método {method.method_id} não possui subtítulo próprio")
            list_items = sum(
                len(_block_content_sentences(block)) for block in method_blocks if block.type == "list"
            )
            expected_steps = max(minimum_steps_per_method, len(method.steps))
            if list_items < expected_steps:
                blockers.append(
                    f"O método {method.method_id} não apresenta os passos completos "
                    f"({list_items} < {expected_steps})"
                )
        if any(
            not step.expected_observations or not step.completion_condition
            for method in methods
            for step in method.steps
        ):
            blockers.append("Há etapa sem observação ou condição de avanço")
        if word_count < minimum_word_count:
            blockers.append(
                f"Guia procedural curto demais para o escopo "
                f"({word_count} < {minimum_word_count} palavras)"
            )
        elif word_count < int(minimum_word_count * 1.1):
            warnings.append(
                f"O guia possui {word_count} palavras; revise se a profundidade está suficiente"
            )
        if word_count > int(maximum_word_count * 1.15):
            blockers.append(
                f"Guia excedeu o limite editorial sem justificativa "
                f"({word_count} > {maximum_word_count} palavras)"
            )
        elif word_count > maximum_word_count:
            warnings.append(
                f"O guia possui {word_count} palavras, acima da faixa planejada de {maximum_word_count}"
            )
        template_hits = len(_TEMPLATE.findall(text))
        if template_hits > 3:
            blockers.append("Linguagem de template repetida em excesso")
        if naturalness_metrics["summary_like_compression"]:
            blockers.append(
                "O corpo foi comprimido em parágrafos-resumo sem desenvolvimento suficiente"
            )
        if naturalness_metrics["heading_body_imbalance"]:
            blockers.append(
                "Há muitos subtítulos para pouco desenvolvimento do corpo"
            )
        if naturalness_metrics["severe_mechanical_prose"]:
            blockers.append(
                "Cadência, aberturas e formato de parágrafos estão mecânicos demais"
            )
        if naturalness_metrics["premature_numeric_density"]:
            blockers.append(
                "A abertura antecipa números e faixas antes de orientar o leitor"
            )
        minimum_opening_mentions = min(2, len(methods))
        if (
            minimum_opening_mentions
            and naturalness_metrics["opening_method_mention_count"]
            < minimum_opening_mentions
        ):
            blockers.append(
                "A abertura não apresenta os métodos antes das condições detalhadas"
            )
        if naturalness_metrics["meta_narration_matches"]:
            blockers.append("O texto narra a própria redação em vez de ensinar diretamente")
        if naturalness_metrics["repeated_sentence_openers"]:
            warnings.append("Muitas frases começam com a mesma construção")

        research = min(1.0, accepted_source_count / max(4, len(methods) * 2))
        if independent_source_count < 3:
            blockers.append("Diversidade de fontes independentes insuficiente")
        knowledge = min(1.0, len(sections) / max(1, len(active_nodes)))
        comparison = min(
            1.0,
            (len(matrix.method_ids) / max(1, len(methods))) * 0.5
            + (min(1.0, len(matrix.rules) / max(2, len(methods))) * 0.5),
        )
        step_count = sum(len(method.steps) for method in methods)
        procedure = min(1.0, step_count / max(6, len(methods) * 3))
        practical = max(0.0, min(1.0, development.decision_usefulness_score))
        coherence = max(0.0, min(1.0, development.procedural_completeness_score))
        model_naturalness = max(
            0.0,
            min(
                1.0,
                (language.naturalness_score * 0.55)
                + (language.rhythm_score * 0.30)
                + ((1 - language.template_language_score) * 0.15),
            ),
        )
        deterministic_naturalness = float(
            naturalness_metrics["observable_naturalness_score"]
        )
        naturalness = max(0.0, min(model_naturalness, deterministic_naturalness))
        factual_links = 1.0 if fact_check.status == "passed" and all(
            method.external_reference is not None for method in methods
        ) else 0.0
        raw_overall = round(
            research * 0.15
            + knowledge * 0.10
            + comparison * 0.10
            + procedure * 0.20
            + practical * 0.10
            + coherence * 0.10
            + naturalness * 0.15
            + factual_links * 0.10,
            4,
        )
        axes = [research, knowledge, comparison, procedure, practical, coherence, naturalness, factual_links]
        if raw_overall < 0.85:
            blockers.append(f"Pontuação procedural insuficiente: {raw_overall:.2f} < 0.85")
        if min(axes) < 0.70:
            blockers.append("Ao menos uma dimensão crítica ficou abaixo de 0,70")
        blockers = list(dict.fromkeys(blockers))
        # A média não pode mascarar um blocker crítico. O score exibido é
        # deliberadamente limitado quando a peça não é publicável.
        overall = min(raw_overall, 0.59) if blockers else raw_overall
        return ProceduralQualityEvaluation(
            status="blocked" if blockers else "passed",
            overall_score=overall,
            research_quality=research,
            knowledge_model_quality=knowledge,
            comparison_decision_quality=comparison,
            procedural_completeness=procedure,
            practical_utility=practical,
            editorial_coherence=coherence,
            naturalness=naturalness,
            factual_link_integrity=factual_links,
            critical_blockers=blockers,
            warnings=warnings,
        )
