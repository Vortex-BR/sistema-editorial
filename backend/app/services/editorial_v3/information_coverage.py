"""Coverage-first research validation for Editorial Intelligence V3.

The old pipeline treated a global claim count as a proxy for knowledge quality.
That made a run with seventeen well-supported facts fail while another run with
eighteen repetitive facts could pass.  This module evaluates the exact
information units derived from the knowledge contract and produces executable
recovery queries for every missing unit.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from app.schemas.editorial_v3_runtime import (
    ResearchCoverageRequirement,
    ResearchTask,
    V3ResearchPlan,
)
from app.services.editorial_v3.text_integrity import (
    content_tokens,
    lexical_overlap,
    normalized_text,
)


@dataclass(frozen=True)
class RequirementCoverage:
    requirement_id: str
    task_id: str
    knowledge_node_id: str
    requirement_type: str
    description: str
    critical: bool
    status: str
    approved_claim_count: int
    raw_claim_count: int
    independent_source_count: int
    authoritative_source_count: int
    required_evidence_roles: tuple[str, ...]
    evidence_roles_found: tuple[str, ...]
    supporting_claim_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in (
            "required_evidence_roles",
            "evidence_roles_found",
            "supporting_claim_ids",
            "reason_codes",
        ):
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class InformationCoverageReport:
    status: str
    overall_coverage_ratio: float
    critical_coverage_ratio: float
    covered_requirement_ids: tuple[str, ...]
    partial_requirement_ids: tuple[str, ...]
    uncovered_requirement_ids: tuple[str, ...]
    critical_missing_requirement_ids: tuple[str, ...]
    supporting_missing_requirement_ids: tuple[str, ...]
    requirement_reports: tuple[RequirementCoverage, ...]
    recovery_tasks: tuple[dict[str, Any], ...]
    reason_codes: tuple[str, ...]
    suggested_blocking_code: str | None

    def as_payload(
        self, *, include_recovery_tasks: bool = True
    ) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "overall_coverage_ratio": self.overall_coverage_ratio,
            "critical_coverage_ratio": self.critical_coverage_ratio,
            "covered_requirement_ids": list(self.covered_requirement_ids),
            "partial_requirement_ids": list(self.partial_requirement_ids),
            "uncovered_requirement_ids": list(self.uncovered_requirement_ids),
            "critical_missing_requirement_ids": list(
                self.critical_missing_requirement_ids
            ),
            "supporting_missing_requirement_ids": list(
                self.supporting_missing_requirement_ids
            ),
            "requirement_reports": [item.as_payload() for item in self.requirement_reports],
            "reason_codes": list(self.reason_codes),
            "suggested_blocking_code": self.suggested_blocking_code,
        }
        if include_recovery_tasks:
            payload["recovery_tasks"] = [
                dict(item) for item in self.recovery_tasks
            ]
        return payload


def _fallback_requirement(task: ResearchTask) -> ResearchCoverageRequirement:
    """Keep old checkpoints executable after the coverage model upgrade."""

    return ResearchCoverageRequirement(
        requirement_id=f"{task.task_id}_req_legacy",
        requirement_type="question",
        description=task.research_goal,
        evidence_roles=[task.evidence_role],
        critical=task.critical,
        minimum_approved_claims=1,
        minimum_independent_sources=min(
            3, max(1, task.minimum_independent_sources)
        ),
        query_terms=[],
    )


def requirements_for_task(task: ResearchTask) -> list[ResearchCoverageRequirement]:
    return list(task.coverage_requirements) or [_fallback_requirement(task)]


def requirement_catalog(plan: V3ResearchPlan) -> dict[str, ResearchCoverageRequirement]:
    return {
        requirement.requirement_id: requirement
        for task in plan.tasks
        for requirement in requirements_for_task(task)
    }


def _match_score(description: str, claim_text: str) -> float:
    if not description or not claim_text:
        return 0.0
    directional = max(
        lexical_overlap(description, claim_text),
        lexical_overlap(claim_text, description),
    )
    description_tokens = set(content_tokens(description))
    claim_tokens = set(content_tokens(claim_text))
    if not description_tokens or not claim_tokens:
        return 0.0
    jaccard = len(description_tokens & claim_tokens) / max(
        1, len(description_tokens | claim_tokens)
    )
    sequence = SequenceMatcher(
        None, normalized_text(description), normalized_text(claim_text)
    ).ratio()
    return round(max(directional, jaccard, sequence * 0.7), 4)


def infer_requirement_ids(
    task: ResearchTask,
    *,
    claim_text: str,
    evidence_role: str,
    minimum_score: float = 0.24,
    limit: int = 4,
) -> list[str]:
    """Conservatively map a claim when a model omits explicit requirement IDs."""

    ranked: list[tuple[float, str]] = []
    for requirement in requirements_for_task(task):
        allowed_roles = {item.value for item in requirement.evidence_roles}
        if allowed_roles and evidence_role not in allowed_roles:
            continue
        score = _match_score(requirement.description, claim_text)
        if score >= minimum_score:
            ranked.append((score, requirement.requirement_id))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    if not ranked:
        return []
    best = ranked[0][0]
    return [
        requirement_id
        for score, requirement_id in ranked
        if score >= max(minimum_score, best - 0.12)
    ][:limit]


def _safe_query(*parts: str, limit: int = 280) -> str:
    query = " ".join(" ".join(str(part or "").split()) for part in parts if part)
    query = re.sub(r"\s+", " ", query).strip()
    return query[:limit].rstrip()


def recovery_queries(
    *,
    topic: str,
    requirement: ResearchCoverageRequirement,
) -> list[str]:
    role_terms = " ".join(item.value.replace("_", " ") for item in requirement.evidence_roles[:3])
    keywords = " ".join(requirement.query_terms[:8])
    candidates = [
        _safe_query(topic, requirement.description, "fonte técnica evidência"),
        _safe_query(topic, keywords, role_terms, "universidade revisão protocolo"),
        _safe_query(topic, requirement.description, "study review guideline evidence"),
    ]
    return list(dict.fromkeys(item for item in candidates if item))


class InformationCoverageService:
    """Evaluate approved evidence against each required information unit."""

    def evaluate(
        self,
        *,
        topic: str,
        plan: V3ResearchPlan,
        evidence_records: Iterable[dict[str, Any]],
        minimum_overall_ratio: float = 0.85,
    ) -> InformationCoverageReport:
        records = [dict(item) for item in evidence_records]
        reports: list[RequirementCoverage] = []
        recovery: list[dict[str, Any]] = []
        all_reasons: set[str] = set()

        for task in plan.tasks:
            node_records = [
                item
                for item in records
                if str(item.get("knowledge_node_id") or "")
                == task.knowledge_node_id
            ]
            for requirement in requirements_for_task(task):
                explicit = [
                    item
                    for item in node_records
                    if requirement.requirement_id
                    in set(item.get("coverage_requirement_ids") or [])
                ]
                # Legacy/model-repair fallback: only use semantically aligned claims
                # from the same node and an allowed evidence role.
                if not explicit:
                    allowed_roles = {item.value for item in requirement.evidence_roles}
                    explicit = [
                        item
                        for item in node_records
                        if (
                            not allowed_roles
                            or str(item.get("evidence_role") or "") in allowed_roles
                        )
                        and _match_score(
                            requirement.description,
                            str(item.get("claim_text") or ""),
                        )
                        >= 0.30
                    ]

                approved = [item for item in explicit if bool(item.get("approved"))]
                canonical_claim_ids = {
                    str(item.get("canonical_claim_id") or item.get("claim_key") or "")
                    for item in approved
                    if item.get("canonical_claim_id") or item.get("claim_key")
                }
                independent_hosts = {
                    str(item.get("source_host") or "")
                    for item in approved
                    if item.get("independent_source") and item.get("source_host")
                }
                authoritative_hosts = {
                    str(item.get("source_host") or "")
                    for item in approved
                    if item.get("authoritative_source") and item.get("source_host")
                }
                roles_found = {
                    str(item.get("evidence_role") or "") for item in approved
                }
                reasons: list[str] = []
                if not explicit:
                    reasons.append("no_claim_extracted_for_requirement")
                elif not approved:
                    reasons.append("claims_not_approved_for_requirement")
                    blockers = {
                        str(blocker)
                        for item in explicit
                        for blocker in item.get("bundle_blockers") or []
                    }
                    if blockers:
                        reasons.append("evidence_policy_blocked_requirement")
                if len(canonical_claim_ids) < requirement.minimum_approved_claims:
                    reasons.append("approved_claim_count_insufficient")
                if len(independent_hosts) < requirement.minimum_independent_sources:
                    reasons.append("independent_support_insufficient")
                allowed_roles = {item.value for item in requirement.evidence_roles}
                if approved and allowed_roles and not (roles_found & allowed_roles):
                    reasons.append("required_evidence_role_missing")

                covered = (
                    len(canonical_claim_ids) >= requirement.minimum_approved_claims
                    and len(independent_hosts)
                    >= requirement.minimum_independent_sources
                    and (not allowed_roles or bool(roles_found & allowed_roles))
                )
                if covered:
                    status = "covered"
                    reasons = []
                elif explicit:
                    status = "partial"
                else:
                    status = "uncovered"

                all_reasons.update(reasons)
                report = RequirementCoverage(
                    requirement_id=requirement.requirement_id,
                    task_id=task.task_id,
                    knowledge_node_id=task.knowledge_node_id,
                    requirement_type=requirement.requirement_type,
                    description=requirement.description,
                    critical=requirement.critical,
                    status=status,
                    approved_claim_count=len(canonical_claim_ids),
                    raw_claim_count=len(explicit),
                    independent_source_count=len(independent_hosts),
                    authoritative_source_count=len(authoritative_hosts),
                    required_evidence_roles=tuple(
                        item.value for item in requirement.evidence_roles
                    ),
                    evidence_roles_found=tuple(sorted(roles_found)),
                    supporting_claim_ids=tuple(sorted(canonical_claim_ids)),
                    reason_codes=tuple(dict.fromkeys(reasons)),
                )
                reports.append(report)

                if status != "covered":
                    for query_variant, query in enumerate(
                        recovery_queries(topic=topic, requirement=requirement)
                    ):
                        recovery.append(
                            {
                                "task_id": task.task_id,
                                "knowledge_node_id": task.knowledge_node_id,
                                "requirement_id": requirement.requirement_id,
                                "question": requirement.description,
                                "query": query,
                                "query_variant": query_variant,
                                "critical": requirement.critical,
                                "reason_codes": list(report.reason_codes),
                            }
                        )

        total = len(reports)
        covered = [item for item in reports if item.status == "covered"]
        critical = [item for item in reports if item.critical]
        critical_covered = [item for item in critical if item.status == "covered"]
        critical_missing = [item for item in critical if item.status != "covered"]
        supporting_missing = [
            item for item in reports if not item.critical and item.status != "covered"
        ]
        overall_ratio = round(len(covered) / total, 4) if total else 0.0
        critical_ratio = (
            round(len(critical_covered) / len(critical), 4) if critical else 1.0
        )

        passed = not critical_missing and overall_ratio >= minimum_overall_ratio
        blocking_code: str | None = None
        if not passed:
            if not records:
                blocking_code = "V3_INFORMATION_EXTRACTION_EMPTY"
            elif critical_missing:
                blocking_code = "V3_CRITICAL_INFORMATION_COVERAGE_INCOMPLETE"
            else:
                blocking_code = "V3_INFORMATION_COVERAGE_RATIO_INSUFFICIENT"

        # Recovery is bounded and deterministic.  Critical and partially-covered
        # requirements come first, but query variants are interleaved so the first
        # bounded batch attempts one useful query for every missing information unit
        # before spending the budget on second/third variants of the same gap.
        priority = {
            item.requirement_id: (
                0 if item.critical else 1,
                0 if item.status == "partial" else 1,
                item.task_id,
                item.requirement_id,
            )
            for item in reports
        }

        def recovery_priority(item: dict[str, Any]) -> tuple[Any, ...]:
            requirement_priority = priority.get(
                str(item.get("requirement_id") or ""), (9, 9, "", "")
            )
            return (
                requirement_priority[0],
                requirement_priority[1],
                int(item.get("query_variant") or 0),
                requirement_priority[2],
                requirement_priority[3],
            )

        recovery.sort(key=recovery_priority)
        deduped_recovery: list[dict[str, Any]] = []
        seen_queries: set[tuple[str, str]] = set()
        for item in recovery:
            key = (
                str(item.get("task_id") or ""),
                str(item.get("query") or "").casefold(),
            )
            if key in seen_queries:
                continue
            seen_queries.add(key)
            deduped_recovery.append(item)

        return InformationCoverageReport(
            status="passed" if passed else "incomplete",
            overall_coverage_ratio=overall_ratio,
            critical_coverage_ratio=critical_ratio,
            covered_requirement_ids=tuple(
                item.requirement_id for item in reports if item.status == "covered"
            ),
            partial_requirement_ids=tuple(
                item.requirement_id for item in reports if item.status == "partial"
            ),
            uncovered_requirement_ids=tuple(
                item.requirement_id for item in reports if item.status == "uncovered"
            ),
            critical_missing_requirement_ids=tuple(
                item.requirement_id for item in critical_missing
            ),
            supporting_missing_requirement_ids=tuple(
                item.requirement_id for item in supporting_missing
            ),
            requirement_reports=tuple(reports),
            recovery_tasks=tuple(deduped_recovery),
            reason_codes=tuple(sorted(all_reasons)),
            suggested_blocking_code=blocking_code,
        )
