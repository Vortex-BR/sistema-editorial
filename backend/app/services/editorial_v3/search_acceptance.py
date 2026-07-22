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

# Source roles are capabilities, not mutually exclusive labels. A scientific
# primary source can satisfy a request for scientific support even when the
# planner named ``scientific_review`` as the preferred role; likewise a deep
# specialist guide can satisfy a procedural role. Exact string equality caused
# false ``required_source_roles_missing`` blockers in production.
_ROLE_COMPATIBILITY: dict[str, set[str]] = {
    "scientific_primary": {
        "scientific_primary", "scientific_review", "academic_repository",
        "scientific_database", "institutional",
    },
    "scientific_review": {
        "scientific_review", "scientific_primary", "academic_repository",
        "scientific_database", "institutional",
    },
    "academic_repository": {
        "academic_repository", "scientific_primary", "scientific_review",
        "scientific_database", "institutional",
    },
    "scientific_database": {
        "scientific_database", "scientific_primary", "scientific_review",
        "academic_repository",
    },
    "institutional": {
        "institutional", "scientific_primary", "scientific_review",
        "academic_repository", "scientific_database",
    },
    "technical_procedural": {
        "technical_procedural", "specialist_practical", "institutional",
        "scientific_primary", "scientific_review",
    },
    "specialist_practical": {
        "specialist_practical", "technical_procedural", "institutional",
    },
    "independent_editorial": {
        "independent_editorial", "specialist_practical",
        "technical_procedural", "news_reporting", "encyclopedic_discovery",
    },
}


def source_role_satisfies(required_role: str, found_role: str) -> bool:
    required = str(required_role or "").strip()
    found = str(found_role or "").strip()
    if not required or not found:
        return False
    return found in _ROLE_COMPATIBILITY.get(required, {required})


def matched_required_source_roles(
    required_roles: Iterable[str], found_roles: Iterable[str]
) -> set[str]:
    found = {str(item) for item in found_roles if str(item).strip()}
    return {
        str(required)
        for required in required_roles
        if any(source_role_satisfies(str(required), candidate) for candidate in found)
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


def expand_source_task_map(
    *,
    tasks: Iterable[ResearchTask],
    documents: Iterable[StructuredSourceDocument],
    source_task_map: dict[str, list[str]],
    minimum_score: float = 0.16,
    maximum_new_tasks_per_document: int = 5,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """Attach a readable source to every task it materially supports.

    Search providers associate a result only with the query that discovered it.
    A single technical document often answers several neighboring knowledge
    nodes, so keeping that one-query mapping produced false coverage gaps even
    after the source had been fetched and classified. This deterministic pass
    uses role compatibility, allowed evidence roles and weighted lexical
    relevance to add only defensible cross-task assignments.
    """

    task_list = list(tasks)
    document_list = list(documents)
    result: dict[str, set[str]] = {
        canonicalize_url(str(url)): {str(item) for item in task_ids}
        for url, task_ids in source_task_map.items()
    }
    task_tokens = {
        task.task_id: _tokens(
            " ".join([task.research_goal, *task.queries, task.knowledge_node_id])
        )
        for task in task_list
    }
    token_frequency: dict[str, int] = {}
    for values in task_tokens.values():
        for token in values:
            token_frequency[token] = token_frequency.get(token, 0) + 1

    assignments: list[dict[str, Any]] = []
    threshold = max(0.08, min(0.6, float(minimum_score)))
    max_assignments = max(1, int(maximum_new_tasks_per_document))

    for document in document_list:
        assessment = document.assessment
        if assessment.usage_policy not in {
            SourceUsagePolicy.authoritative_evidence,
            SourceUsagePolicy.corroborating_evidence,
        }:
            continue
        found_role = assessment.source_role.value
        allowed_roles = set(assessment.allowed_evidence_roles)
        body_tokens = _tokens(f"{document.title} {document.plain_text[:30000]}")
        title_tokens = _tokens(document.title)
        if not body_tokens:
            continue

        ranked: list[tuple[float, ResearchTask]] = []
        for task in task_list:
            if task.evidence_role not in allowed_roles:
                continue
            if task.required_source_roles and not any(
                source_role_satisfies(required, found_role)
                for required in task.required_source_roles
            ):
                continue
            tokens_for_task = task_tokens.get(task.task_id, set())
            if not tokens_for_task:
                continue
            # Rare task terms carry more weight than subject words repeated in
            # every node. This avoids assigning every source to every task merely
            # because all queries mention the same topic.
            weighted_total = sum(
                1.0 / max(1, token_frequency.get(token, 1))
                for token in tokens_for_task
            )
            weighted_overlap = sum(
                1.0 / max(1, token_frequency.get(token, 1))
                for token in tokens_for_task & body_tokens
            )
            coverage = weighted_overlap / max(1e-9, weighted_total)
            title_overlap = len(tokens_for_task & title_tokens) / max(
                1, min(len(tokens_for_task), 10)
            )
            score = min(1.0, coverage * 0.82 + title_overlap * 0.18)
            if score >= threshold:
                ranked.append((score, task))

        ranked.sort(key=lambda item: (-item[0], not item[1].critical, item[1].task_id))
        for score, task in ranked[:max_assignments]:
            keys = {
                canonicalize_url(str(document.url)),
                canonicalize_url(str(document.canonical_url)),
            }
            already_assigned = any(task.task_id in result.get(key, set()) for key in keys)
            for key in keys:
                result.setdefault(key, set()).add(task.task_id)
            if not already_assigned:
                assignments.append(
                    {
                        "document_id": str(document.document_id),
                        "task_id": task.task_id,
                        "knowledge_node_id": task.knowledge_node_id,
                        "score": round(score, 4),
                        "source_role": found_role,
                    }
                )

    return (
        {key: sorted(values) for key, values in result.items()},
        assignments,
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
    synthesis_ready: bool
    task_reports: tuple[TaskCoverage, ...]
    deficient_task_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    suggested_blocking_code: str | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "synthesis_ready": self.synthesis_ready,
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
            matched_roles = matched_required_source_roles(requested_roles, found_roles)
            missing_roles = sorted(requested_roles - matched_roles)
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
            if requested_roles and not matched_roles:
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
        # V3.9 validates support at the exact information-requirement level after
        # claims are extracted.  The older task-level source gate must therefore
        # not prevent synthesis when every task already has usable, role-compatible
        # and authoritative evidence and the *only* remaining issue is that a core
        # task has one independent source instead of two.  The later information
        # gate still requires two independent sources for each critical fact and
        # performs targeted recovery for the precise missing unit.
        synthesis_ready = bool(reports) and all(
            item.status == "passed"
            or (
                item.accepted_source_count >= 1
                and set(item.reason_codes)
                <= {"independent_source_diversity_insufficient"}
            )
            for item in reports
        )
        code: str | None = None
        if deficient:
            if "all_task_sources_rejected_or_comparison_only" in all_reasons:
                code = "V3_SOURCE_POLICY_REJECTED_ALL"
            elif "no_readable_source_for_task" in all_reasons:
                code = "V3_SOURCE_FETCH_EXHAUSTED"
            elif "independent_source_diversity_insufficient" in all_reasons:
                code = "V3_SOURCE_DIVERSITY_INSUFFICIENT"
            else:
                code = "V3_RESEARCH_COVERAGE_INCOMPLETE"
        return SourceCoverageReport(
            status="passed" if not deficient else "incomplete",
            synthesis_ready=synthesis_ready,
            task_reports=tuple(reports),
            deficient_task_ids=deficient,
            reason_codes=tuple(sorted(all_reasons)),
            suggested_blocking_code=code,
        )
