"""Quality and coverage gates for Editorial V3.5 research."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.schemas.editorial_v3 import SourceUsagePolicy
from app.schemas.editorial_v3_runtime import ResearchTask, StructuredSourceDocument
from app.services.research_engine import SearchDocument, canonicalize_url

_STOPWORDS = {
    "a", "ao", "aos", "as", "and", "como", "com", "da", "das", "de", "do",
    "dos", "e", "em", "for", "from", "in", "la", "las", "los", "na", "no",
    "o", "of", "os", "ou", "para", "por", "que", "the", "to", "um", "uma",
    "with", "y",
}
_HIGH_TRUST_TYPES = {"scientific", "government", "university"}
_LOW_TRUST_TYPES = {"forum"}
_ROLE_TYPE_COMPATIBILITY: dict[str, set[str]] = {
    "scientific_primary": {"scientific"},
    "scientific_review": {"scientific"},
    "academic_repository": {"scientific", "university"},
    "scientific_database": {"scientific"},
    "institutional": {"government", "university"},
    "technical_procedural": {"government", "university", "practical", "scientific"},
    "independent_editorial": {"news", "practical"},
}


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]{3,}", _fold(value))
        if token not in _STOPWORDS
    }


def independent_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold().strip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    # Lightweight public-suffix handling for the domains most likely to appear.
    if ".".join(parts[-2:]) in {"com.br", "org.br", "gov.br", "edu.br", "co.uk", "org.uk"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


@dataclass(frozen=True)
class CandidateAcceptanceReport:
    sufficient: bool
    relevant_document_count: int
    independent_domain_count: int
    high_trust_document_count: int
    low_trust_document_count: int
    required_role_match_count: int
    minimum_independent_sources: int
    reasons: tuple[str, ...] = ()
    relevance_by_url: dict[str, float] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


class CandidateAcceptanceService:
    def __init__(self, minimum_relevance: float = 0.18):
        self.minimum_relevance = max(0.0, min(1.0, float(minimum_relevance)))

    def evaluate(
        self,
        documents: Iterable[SearchDocument],
        *,
        subject: str,
        query: str,
        required_source_roles: Iterable[str],
        minimum_independent_sources: int,
        authoritative_required: bool | None = None,
    ) -> CandidateAcceptanceReport:
        subject_tokens = _tokens(f"{subject} {query}")
        relevance_by_url: dict[str, float] = {}
        relevant: list[SearchDocument] = []
        for document in documents:
            document_tokens = _tokens(f"{document.title} {document.content[:3000]}")
            overlap = len(subject_tokens & document_tokens)
            score = overlap / max(1, min(len(subject_tokens), 12))
            # Provider results from high-trust sources get a small, bounded boost,
            # never enough to rescue a completely unrelated page.
            if document.source_type in _HIGH_TRUST_TYPES and overlap:
                score = min(1.0, score + 0.08)
            relevance_by_url[canonicalize_url(document.url)] = round(score, 3)
            if score >= self.minimum_relevance:
                relevant.append(document)

        domains = {independent_domain(item.url) for item in relevant if independent_domain(item.url)}
        high_trust = sum(item.source_type in _HIGH_TRUST_TYPES for item in relevant)
        low_trust = sum(item.source_type in _LOW_TRUST_TYPES for item in relevant)
        required_roles = {str(item) for item in required_source_roles}
        matched_roles: set[str] = set()
        for role in required_roles:
            compatible = _ROLE_TYPE_COMPATIBILITY.get(role, set())
            if any(item.source_type in compatible for item in relevant):
                matched_roles.add(role)

        required_independent = max(1, int(minimum_independent_sources))
        reasons: list[str] = []
        if len(relevant) < required_independent:
            reasons.append("insufficient_relevant_documents")
        if len(domains) < required_independent:
            reasons.append("insufficient_independent_domains")
        if authoritative_required is None:
            authoritative_required = bool(
                required_roles
                & {
                    "scientific_primary",
                    "scientific_review",
                    "academic_repository",
                    "scientific_database",
                }
            )
        if authoritative_required and high_trust == 0:
            reasons.append("authoritative_source_role_missing")
        if relevant and low_trust == len(relevant):
            reasons.append("only_low_trust_sources")
        if required_roles and not matched_roles:
            reasons.append("required_source_roles_not_represented")

        return CandidateAcceptanceReport(
            sufficient=not reasons,
            relevant_document_count=len(relevant),
            independent_domain_count=len(domains),
            high_trust_document_count=high_trust,
            low_trust_document_count=low_trust,
            required_role_match_count=len(matched_roles),
            minimum_independent_sources=required_independent,
            reasons=tuple(reasons),
            relevance_by_url=relevance_by_url,
        )


@dataclass(frozen=True)
class TaskCoverage:
    task_id: str
    knowledge_node_id: str
    accepted_source_count: int
    independent_source_count: int
    authoritative_source_count: int
    required_source_roles_found: tuple[str, ...]
    required_source_roles_missing: tuple[str, ...]
    minimum_independent_sources: int
    status: str
    reason_codes: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("required_source_roles_found", "required_source_roles_missing", "reason_codes"):
            payload[key] = list(payload[key])
        return payload


@dataclass(frozen=True)
class SourceCoverageReport:
    status: str
    task_reports: tuple[TaskCoverage, ...]
    deficient_task_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    suggested_blocking_code: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task_reports": [item.as_payload() for item in self.task_reports],
            "deficient_task_ids": list(self.deficient_task_ids),
            "reason_codes": list(self.reason_codes),
            "suggested_blocking_code": self.suggested_blocking_code,
        }


class SourceCoverageService:
    """Evaluate readable/accepted sources per research task before synthesis."""

    def evaluate(
        self,
        *,
        tasks: Iterable[ResearchTask],
        documents: Iterable[StructuredSourceDocument],
        source_task_map: dict[str, list[str]],
    ) -> SourceCoverageReport:
        task_list = list(tasks)
        documents_by_task: dict[str, dict[str, StructuredSourceDocument]] = {
            task.task_id: {} for task in task_list
        }
        for document in documents:
            urls = {
                canonicalize_url(str(document.url)),
                canonicalize_url(str(document.canonical_url)),
            }
            task_ids: set[str] = set()
            for url in urls:
                task_ids.update(source_task_map.get(url, []))
            for task_id in task_ids:
                documents_by_task.setdefault(task_id, {})[str(document.document_id)] = document

        reports: list[TaskCoverage] = []
        all_reasons: set[str] = set()
        for task in task_list:
            assigned = list(documents_by_task.get(task.task_id, {}).values())
            accepted = [
                item
                for item in assigned
                if item.assessment.usage_policy
                in {
                    SourceUsagePolicy.authoritative_evidence,
                    SourceUsagePolicy.corroborating_evidence,
                }
            ]
            independent = [
                item
                for item in accepted
                if item.assessment.counts_toward_independent_source_diversity
            ]
            independent_domains = {
                independent_domain(str(item.canonical_url)) for item in independent
            }
            authoritative = [
                item for item in accepted if item.assessment.eligible_for_primary_evidence
            ]
            found_roles = {
                item.assessment.source_role.value for item in accepted
            }
            requested_roles = set(task.required_source_roles)
            missing_roles = sorted(requested_roles - found_roles)
            reasons: list[str] = []
            if not assigned:
                reasons.append("no_readable_source_for_task")
            elif not accepted:
                reasons.append("all_task_sources_rejected_or_comparison_only")
            if len(independent_domains) < task.minimum_independent_sources:
                reasons.append("independent_source_diversity_insufficient")
            authoritative_required = bool(
                requested_roles
                & {
                    "scientific_primary",
                    "scientific_review",
                    "academic_repository",
                    "scientific_database",
                }
            ) or task.evidence_role.value in {
                "definition",
                "mechanism",
                "risk",
                "limitation",
            }
            regulatory_goal = re.search(
                r"\b(?:legal|legisla|regula|jurisdi|governo|norma)\w*",
                task.research_goal,
                re.IGNORECASE,
            )
            authoritative_required = bool(
                authoritative_required or regulatory_goal
            )
            if authoritative_required and not authoritative:
                reasons.append("authoritative_source_missing")
            # Do not require every desirable role; one represented role plus
            # diversity is sufficient. The missing list remains diagnostic.
            if requested_roles and not (requested_roles & found_roles):
                reasons.append("required_source_roles_missing")
            status = "passed" if not reasons else "incomplete"
            all_reasons.update(reasons)
            reports.append(
                TaskCoverage(
                    task_id=task.task_id,
                    knowledge_node_id=task.knowledge_node_id,
                    accepted_source_count=len(accepted),
                    independent_source_count=len(independent_domains),
                    authoritative_source_count=len(authoritative),
                    required_source_roles_found=tuple(sorted(found_roles)),
                    required_source_roles_missing=tuple(missing_roles),
                    minimum_independent_sources=task.minimum_independent_sources,
                    status=status,
                    reason_codes=tuple(reasons),
                )
            )

        deficient = tuple(item.task_id for item in reports if item.status != "passed")
        code: str | None = None
        if deficient:
            if "all_task_sources_rejected_or_comparison_only" in all_reasons:
                code = "V3_SOURCE_POLICY_REJECTED_ALL"
            elif "independent_source_diversity_insufficient" in all_reasons:
                code = "V3_SOURCE_DIVERSITY_INSUFFICIENT"
            elif "no_readable_source_for_task" in all_reasons:
                code = "V3_SOURCE_FETCH_EXHAUSTED"
            else:
                code = "V3_RESEARCH_COVERAGE_INCOMPLETE"
        return SourceCoverageReport(
            status="passed" if not deficient else "incomplete",
            task_reports=tuple(reports),
            deficient_task_ids=deficient,
            reason_codes=tuple(sorted(all_reasons)),
            suggested_blocking_code=code,
        )
