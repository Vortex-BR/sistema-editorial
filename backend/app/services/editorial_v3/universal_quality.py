"""Deterministic quality rubric for non-procedural Editorial V3 content."""

from __future__ import annotations

import re

from app.schemas.editorial_v3 import ContentKnowledgeContract, SectionDossier
from app.schemas.editorial_v3_runtime import (
    ProceduralQualityEvaluation,
    V3DevelopmentReview,
    V3FactCheckReview,
    V3LanguageReview,
    V3WriterOutput,
)
from app.services.editorial_v3.prose_quality import analyze_editorial_prose
from app.services.editorial_v3.generation_context import active_node_ids


def _block_content_sentences(block):
    """Support current structured blocks and legacy/test block doubles."""

    return getattr(block, "content_sentences", getattr(block, "sentences", []))


class UniversalEditorialQualityService:
    """Evaluate hierarchy, evidence and prose without inventing procedures."""

    def evaluate(
        self,
        *,
        contract: ContentKnowledgeContract,
        sections: list[SectionDossier],
        draft: V3WriterOutput,
        development: V3DevelopmentReview,
        fact_check: V3FactCheckReview,
        language: V3LanguageReview,
        accepted_source_count: int,
        independent_source_count: int,
        minimum_word_count: int,
        maximum_word_count: int,
        diagnostics: dict | None = None,
    ) -> ProceduralQualityEvaluation:
        diagnostics = dict(diagnostics or {})
        blockers = [
            str(item.get("message") or item.get("code") or item)
            for item in diagnostics.get("blockers", [])
        ]
        warnings = [
            str(item.get("message") or item.get("code") or item)
            for item in diagnostics.get("warnings", [])
        ]
        text = " ".join(
            sentence.text for block in draft.blocks for sentence in _block_content_sentences(block)
        )
        markdown_chunks: list[str] = []
        for block in draft.blocks:
            content = " ".join(sentence.text for sentence in _block_content_sentences(block))
            if block.type in {"h1", "h2", "h3"}:
                markdown_chunks.append(f"{'#' * int(block.type[1])} {content}")
            elif block.type == "list":
                markdown_chunks.append("\n".join(f"- {item.text}" for item in _block_content_sentences(block)))
            else:
                markdown_chunks.append(content)
        prose = analyze_editorial_prose("\n\n".join(markdown_chunks), method_labels=[])
        word_count = len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", text))

        active_ids = set(active_node_ids(contract))
        active_nodes = [node for node in contract.nodes if node.node_id in active_ids]
        expected = [node.node_id for node in active_nodes]
        covered = set(draft.covered_section_ids)
        missing = set(expected) - covered
        unknown = covered - set(expected)
        if missing:
            blockers.append("Nós obrigatórios ausentes: " + ", ".join(sorted(missing)))
        if unknown:
            blockers.append("Nós desconhecidos no rascunho: " + ", ".join(sorted(unknown)))
        positions: dict[str, int] = {}
        words: dict[str, int] = {}
        for block in draft.blocks:
            positions.setdefault(block.section_id, block.position)
            words[block.section_id] = words.get(block.section_id, 0) + sum(
                len(re.findall(r"\b\w+[\wÀ-ÿ'-]*\b", sentence.text))
                for sentence in _block_content_sentences(block)
            )
        represented = [positions[node] for node in expected if node in positions]
        if represented != sorted(represented):
            blockers.append("A ordem hierárquica do contrato não foi respeitada")
        for node in active_nodes:
            current = positions.get(node.node_id)
            if current is None:
                continue
            if any(
                dependency not in positions or positions[dependency] >= current
                for dependency in node.depends_on
            ):
                blockers.append(
                    f"O nó {node.node_id} aparece antes de uma dependência obrigatória"
                )
            minimum = max(55, int(75 * node.minimum_depth_weight))
            if node.importance.value == "core" and words.get(node.node_id, 0) < minimum:
                blockers.append(f"O nó central {node.node_id} está superficial")

        if draft.covered_method_ids or any(block.method_id for block in draft.blocks):
            blockers.append("Uma arquitetura não procedural recebeu métodos artificiais")
        if development.status != "passed" or not development.promise_fulfilled:
            blockers.append("A revisão de desenvolvimento não confirmou a promessa editorial")
        if fact_check.status != "passed":
            blockers.append("O fact-checking não aprovou todas as afirmações")
        if language.status != "passed":
            blockers.append("A edição de linguagem não aprovou a redação")
        factual = [
            sentence
            for block in draft.blocks
            for sentence in _block_content_sentences(block)
            if sentence.is_factual
        ]
        if any(not sentence.evidence for sentence in factual):
            blockers.append("Existem afirmações factuais sem evidência")
        if word_count < minimum_word_count:
            blockers.append(
                f"Conteúdo curto demais para o escopo ({word_count} < {minimum_word_count})"
            )
        if word_count > maximum_word_count:
            blockers.append(
                f"Conteúdo excedeu o limite editorial ({word_count} > {maximum_word_count})"
            )
        if prose["summary_like_compression"]:
            blockers.append("O texto resume fatos sem desenvolver o raciocínio")
        if prose["heading_body_imbalance"]:
            blockers.append("Há muitos subtítulos para pouco desenvolvimento")
        if prose["severe_mechanical_prose"]:
            blockers.append("A prosa está mecanicamente uniforme")
        if prose["meta_narration_matches"]:
            blockers.append("O conteúdo narra a própria redação")
        if independent_source_count < 2:
            blockers.append("Diversidade de fontes independentes insuficiente")

        research = min(1.0, accepted_source_count / max(3, len(active_nodes) / 2))
        knowledge = min(1.0, len(sections) / max(1, len(active_nodes)))
        architecture_fit = max(
            0.0,
            1.0
            - (len(missing) + len(unknown)) / max(1, len(active_nodes)),
        )
        hierarchy_completeness = architecture_fit if represented == sorted(represented) else min(architecture_fit, 0.5)
        practical = max(0.0, min(1.0, development.decision_usefulness_score))
        coherence = max(0.0, min(1.0, development.procedural_completeness_score))
        model_naturalness = max(
            0.0,
            min(
                1.0,
                language.naturalness_score * 0.55
                + language.rhythm_score * 0.30
                + (1 - language.template_language_score) * 0.15,
            ),
        )
        naturalness = min(
            model_naturalness,
            float(prose["observable_naturalness_score"]),
        )
        factual_integrity = 1.0 if fact_check.status == "passed" and all(
            sentence.evidence for sentence in factual
        ) else 0.0
        raw_overall = round(
            research * 0.15
            + knowledge * 0.12
            + architecture_fit * 0.13
            + hierarchy_completeness * 0.18
            + practical * 0.10
            + coherence * 0.12
            + naturalness * 0.12
            + factual_integrity * 0.08,
            4,
        )
        axes = [
            research,
            knowledge,
            architecture_fit,
            hierarchy_completeness,
            practical,
            coherence,
            naturalness,
            factual_integrity,
        ]
        if raw_overall < 0.85:
            blockers.append(f"Pontuação editorial insuficiente: {raw_overall:.2f} < 0.85")
        if min(axes) < 0.70:
            blockers.append("Ao menos uma dimensão editorial ficou abaixo de 0,70")
        blockers = list(dict.fromkeys(blockers))
        overall = min(raw_overall, 0.59) if blockers else raw_overall
        return ProceduralQualityEvaluation(
            rubric_version="quality-rubric.universal-editorial.v1",
            architecture_type=contract.content_type.value,
            status="blocked" if blockers else "passed",
            overall_score=overall,
            research_quality=research,
            knowledge_model_quality=knowledge,
            comparison_decision_quality=architecture_fit,
            procedural_completeness=hierarchy_completeness,
            practical_utility=practical,
            editorial_coherence=coherence,
            naturalness=naturalness,
            factual_link_integrity=factual_integrity,
            critical_blockers=blockers,
            warnings=warnings,
        )
