"""Deterministic context budgeting for Editorial V3 model calls.

The planner removes duplicated representations before a request reaches the
hard AgentRuntime ceiling. It never truncates a draft or factual string in the
middle and reports every degradation step for auditability.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextBudgetReport:
    maximum_characters: int
    original_characters: int
    final_characters: int
    compacted: bool = False
    steps: list[str] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "maximum_characters": self.maximum_characters,
            "original_characters": self.original_characters,
            "final_characters": self.final_characters,
            "compacted": self.compacted,
            "steps": list(self.steps),
        }


def _size(value: object) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def _claim_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        claim_id = value.get("claim_id")
        if claim_id:
            found.add(str(claim_id))
        for child in value.values():
            found.update(_claim_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_claim_ids(child))
    return found


class ContextBudgetExceeded(ValueError):
    def __init__(self, report: ContextBudgetReport):
        self.report = report
        super().__init__(
            f"Editorial context still exceeds {report.maximum_characters} characters "
            f"after deterministic compaction ({report.final_characters})."
        )


class ContextBudgetPlanner:
    def _finish(
        self,
        result: dict[str, Any],
        report: ContextBudgetReport,
    ) -> tuple[dict[str, Any], ContextBudgetReport]:
        report.final_characters = _size(result)
        report.compacted = bool(report.steps)
        if report.final_characters > report.maximum_characters:
            raise ContextBudgetExceeded(report)
        return result, report

    @staticmethod
    def _compact_contract(result: dict[str, Any], report: ContextBudgetReport) -> None:
        contract = result.get("contract")
        if not isinstance(contract, dict):
            return
        result["contract"] = {
            key: contract.get(key)
            for key in (
                "topic",
                "content_type",
                "article_promise",
                "scope_limit",
                "reader_start_state",
                "reader_final_state",
                "prohibited_conclusions",
                "required_method_labels",
                "metadata",
            )
            if key in contract
        }
        report.steps.append("contract_reduced_to_generation_fields")

    @staticmethod
    def _compact_sections(result: dict[str, Any], report: ContextBudgetReport) -> None:
        dossiers = result.get("section_dossiers")
        if not isinstance(dossiers, list):
            return
        result["section_dossiers"] = [
            {
                key: dossier.get(key)
                for key in (
                    "section_id",
                    "objective",
                    "allowed_claim_ids",
                    "required_points",
                    "conditions",
                    "limitations",
                    "conflicts",
                    "completion_criteria",
                )
                if key in dossier
            }
            for dossier in dossiers
            if isinstance(dossier, dict)
        ]
        report.steps.append("section_dossiers_compacted")

    @staticmethod
    def _compact_methods(result: dict[str, Any], report: ContextBudgetReport) -> None:
        dossiers = result.get("method_dossiers")
        if not isinstance(dossiers, list):
            return
        result["method_dossiers"] = [
            {
                key: dossier.get(key)
                for key in (
                    "method_id",
                    "name",
                    "aliases",
                    "preparation",
                    "steps",
                    "expected_observations",
                    "warning_signs",
                    "completion_condition",
                    "conditions",
                    "limitations",
                    "claim_ids",
                )
                if key in dossier
            }
            for dossier in dossiers
            if isinstance(dossier, dict)
        ]
        report.steps.append("method_dossiers_compacted")

    @staticmethod
    def _compact_references(result: dict[str, Any], report: ContextBudgetReport) -> None:
        references = result.get("external_references")
        if not isinstance(references, dict):
            return
        result["external_references"] = {
            key: {
                field: value
                for field, value in item.items()
                if field
                in {
                    "url",
                    "title",
                    "publisher",
                    "method_id",
                    "source_role",
                    "status",
                }
            }
            for key, item in references.items()
            if isinstance(item, dict)
        }
        report.steps.append("external_references_compacted")

    @staticmethod
    def _compact_diagnostics(result: dict[str, Any], report: ContextBudgetReport) -> None:
        diagnostics = result.get("writer_diagnostics")
        if not isinstance(diagnostics, dict):
            return
        result["writer_diagnostics"] = {
            key: value[:50] if isinstance(value, list) else value
            for key, value in diagnostics.items()
            if key
            in {
                "status",
                "blockers",
                "warnings",
                "metrics",
                "target_word_range",
            }
        }
        report.steps.append("writer_diagnostics_compacted")

    @staticmethod
    def _compact_previous_fact_check(
        result: dict[str, Any], report: ContextBudgetReport
    ) -> None:
        previous = result.get("previous_fact_check")
        if not isinstance(previous, dict) or not isinstance(previous.get("checks"), list):
            return
        result["previous_fact_check"] = {
            **previous,
            "checks": [
                {
                    key: item.get(key)
                    for key in (
                        "block_id",
                        "sentence_id",
                        "sentence_text",
                        "claim_ids",
                        "status",
                    )
                    if key in item
                }
                for item in previous["checks"]
                if isinstance(item, dict)
            ],
        }
        report.steps.append("previous_fact_check_compacted")

    @staticmethod
    def _compact_claim_list(
        result: dict[str, Any],
        report: ContextBudgetReport,
        *,
        key: str,
        allowed_ids: set[str],
        relevant_sections: set[str] | None = None,
    ) -> None:
        claims = result.get(key)
        if not isinstance(claims, list):
            return
        compacted = []
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            claim_id = str(claim.get("claim_id") or "")
            section_id = str(
                claim.get("knowledge_node_id") or claim.get("section_id") or ""
            )
            if allowed_ids and claim_id not in allowed_ids:
                if not relevant_sections or section_id not in relevant_sections:
                    continue
            compacted.append(
                {
                    field: claim.get(field)
                    for field in (
                        "claim_id",
                        "support_group",
                        "source_claim_ids",
                        "claim_text",
                        "knowledge_node_id",
                        "section_id",
                        "evidence_role",
                        "method_ids",
                        "conditions",
                        "limitations",
                        "applicability",
                        "conclusion_status",
                        "confidence_score",
                        "conflict_group",
                    )
                    if field in claim
                }
            )
        result[key] = compacted
        report.steps.append(f"{key}_restricted_and_compacted")

    def compact_writer_input(
        self,
        payload: dict[str, Any],
        *,
        maximum_characters: int,
    ) -> tuple[dict[str, Any], ContextBudgetReport]:
        result = copy.deepcopy(payload)
        original = _size(result)
        report = ContextBudgetReport(
            maximum_characters=maximum_characters,
            original_characters=original,
            final_characters=original,
        )
        if original <= maximum_characters:
            return result, report

        intelligence = result.get("editorial_intelligence") or {}
        question_plan = (
            intelligence.get("question_evidence_plan", [])
            if isinstance(intelligence, dict)
            else []
        )
        referenced_claims = _claim_ids(question_plan)
        self._compact_claim_list(
            result,
            report,
            key="claim_catalog",
            allowed_ids=referenced_claims,
        )
        if isinstance(intelligence, dict) and isinstance(
            intelligence.get("claim_policy_catalog"), list
        ):
            intelligence["claim_policy_catalog"] = [
                item
                for item in intelligence["claim_policy_catalog"]
                if not referenced_claims
                or str(item.get("claim_id")) in referenced_claims
            ]
            report.steps.append("claim_policy_catalog_restricted")

        reducers = (
            self._compact_references,
            self._compact_contract,
            self._compact_sections,
            self._compact_methods,
        )
        for reducer in reducers:
            if _size(result) <= maximum_characters:
                break
            reducer(result, report)
        return self._finish(result, report)

    def compact_review_input(
        self,
        payload: dict[str, Any],
        *,
        maximum_characters: int,
    ) -> tuple[dict[str, Any], ContextBudgetReport]:
        """Remove duplicated review context without truncating the draft itself."""

        result = copy.deepcopy(payload)
        original = _size(result)
        report = ContextBudgetReport(
            maximum_characters=maximum_characters,
            original_characters=original,
            final_characters=original,
        )
        if original <= maximum_characters:
            return result, report

        relevant_payload = {
            key: result.get(key)
            for key in (
                "draft",
                "blocks",
                "neighbor_context",
                "editorial_intelligence",
            )
        }
        used_claim_ids = _claim_ids(relevant_payload)
        relevant_sections = {
            str(item.get("section_id"))
            for collection_name in ("blocks", "neighbor_context")
            for item in (result.get(collection_name) or [])
            if isinstance(item, dict) and item.get("section_id")
        }
        for key in ("approved_claims", "allowed_claims", "claim_catalog"):
            self._compact_claim_list(
                result,
                report,
                key=key,
                allowed_ids=used_claim_ids,
                relevant_sections=relevant_sections,
            )

        reducers = (
            self._compact_diagnostics,
            self._compact_previous_fact_check,
            self._compact_references,
            self._compact_contract,
            self._compact_sections,
            self._compact_methods,
        )
        for reducer in reducers:
            if _size(result) <= maximum_characters:
                break
            reducer(result, report)
        return self._finish(result, report)
