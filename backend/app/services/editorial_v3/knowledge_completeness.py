"""Deterministic knowledge gate used before any V3 writer call."""

from __future__ import annotations

from collections import Counter

from app.schemas.editorial_v3 import (
    ContentKnowledgeContract,
    DecisionMatrix,
    GapResolutionStatus,
    KnowledgeCompletenessReport,
    KnowledgeGap,
    MethodDossier,
    SectionDossier,
)
from app.services.editorial_v3.method_coverage import required_method_matches


class KnowledgeCompletenessService:
    MINIMUM_PASS_SCORE = 0.85

    def evaluate(
        self,
        contract: ContentKnowledgeContract,
        *,
        methods: list[MethodDossier],
        sections: list[SectionDossier],
        gaps: list[KnowledgeGap],
        decision_matrix: DecisionMatrix | None,
        minimum_steps_per_method: int = 1,
        minimum_claims_per_method: int = 1,
    ) -> KnowledgeCompletenessReport:
        blockers: list[str] = []
        warnings: list[str] = []
        active_ids = set(contract.metadata.get("active_node_ids") or [
            node.node_id for node in contract.nodes
        ])
        node_ids = {node.node_id for node in contract.nodes if node.node_id in active_ids}
        section_ids = {section.section_id for section in sections}
        missing_node_ids = sorted(node_ids.difference(section_ids))

        if missing_node_ids:
            blockers.append(
                "Dossiês ausentes para nós obrigatórios: " + ", ".join(missing_node_ids)
            )

        unresolved_essential_gaps = [
            gap
            for gap in gaps
            if gap.essential
            and gap.status
            not in {
                GapResolutionStatus.resolved,
                GapResolutionStatus.resolved_conditionally,
            }
        ]
        unresolved_essential = [
            gap.gap_id for gap in unresolved_essential_gaps if gap.gap_id is not None
        ]
        if unresolved_essential_gaps:
            blockers.append("Existem lacunas essenciais não resolvidas")

        if contract.requires_method_comparison:
            if len(methods) < 2:
                blockers.append("O guia comparativo exige pelo menos dois métodos")
            if decision_matrix is None:
                blockers.append("A matriz de decisão entre métodos ainda não existe")

        method_ids = [method.method_id for method in methods]
        duplicates = [key for key, count in Counter(method_ids).items() if count > 1]
        if duplicates:
            blockers.append("Métodos duplicados: " + ", ".join(sorted(duplicates)))

        _, missing_required_methods = required_method_matches(
            contract.required_method_labels,
            methods,
        )
        if missing_required_methods:
            blockers.append(
                "Métodos obrigatórios sem dossiê validado: "
                + ", ".join(missing_required_methods)
            )

        for method in methods:
            if method.unresolved_gap_ids:
                blockers.append(
                    f"O método {method.method_id} ainda possui lacunas não resolvidas"
                )
            if contract.requires_external_reference_per_method and (
                method.external_reference is None
                or method.external_reference.status != "approved"
            ):
                blockers.append(
                    f"O método {method.method_id} não possui referência externa aprovada"
                )
            if not any(step.expected_observations for step in method.steps):
                blockers.append(
                    f"O método {method.method_id} não possui sinais observáveis"
                )
            if len(method.steps) < minimum_steps_per_method:
                blockers.append(
                    f"O método {method.method_id} possui apenas {len(method.steps)} etapas; "
                    f"mínimo exigido: {minimum_steps_per_method}"
                )
            evidence_ids = {
                evidence_id
                for step in method.steps
                for evidence_id in step.evidence_ids
            }
            if len(evidence_ids) < minimum_claims_per_method:
                blockers.append(
                    f"O método {method.method_id} possui apenas {len(evidence_ids)} claims "
                    f"procedurais distintos; mínimo exigido: {minimum_claims_per_method}"
                )

        if decision_matrix is not None:
            unknown = set(decision_matrix.method_ids).difference(method_ids)
            if unknown:
                blockers.append(
                    "A matriz de decisão referencia métodos desconhecidos: "
                    + ", ".join(sorted(unknown))
                )
            missing_from_matrix = set(method_ids).difference(decision_matrix.method_ids)
            if missing_from_matrix:
                blockers.append(
                    "Métodos ausentes da matriz de decisão: "
                    + ", ".join(sorted(missing_from_matrix))
                )

        coverage_ratio = (
            len(node_ids.intersection(section_ids)) / len(node_ids) if node_ids else 0
        )
        method_work_required = bool(
            contract.requires_method_comparison
            or contract.requires_external_reference_per_method
            or contract.required_method_labels
        )
        method_quality = (
            sum(
                1
                for method in methods
                if method.steps
                and len(method.steps) >= minimum_steps_per_method
                and len(
                    {
                        evidence_id
                        for step in method.steps
                        for evidence_id in step.evidence_ids
                    }
                )
                >= minimum_claims_per_method
                and not method.unresolved_gap_ids
                and (
                    not contract.requires_external_reference_per_method
                    or (
                        method.external_reference is not None
                        and method.external_reference.status == "approved"
                    )
                )
            )
            / len(methods)
            if methods
            else (0.0 if method_work_required else 1.0)
        )
        gap_quality = 1.0 if not unresolved_essential_gaps else 0.0
        decision_quality = (
            1.0
            if not contract.requires_method_comparison or decision_matrix is not None
            else 0.0
        )
        score = round(
            (coverage_ratio * 0.4)
            + (method_quality * 0.3)
            + (gap_quality * 0.2)
            + (decision_quality * 0.1),
            4,
        )
        if score < self.MINIMUM_PASS_SCORE:
            blockers.append(
                f"Completude de conhecimento insuficiente: {score:.2f} < {self.MINIMUM_PASS_SCORE:.2f}"
            )

        blockers = list(dict.fromkeys(blockers))
        status = "blocked" if blockers else "passed"
        return KnowledgeCompletenessReport(
            status=status,
            score=score,
            blockers=blockers,
            warnings=warnings,
            covered_node_ids=sorted(node_ids.intersection(section_ids)),
            missing_node_ids=missing_node_ids,
            unresolved_essential_gap_ids=unresolved_essential,
        )
