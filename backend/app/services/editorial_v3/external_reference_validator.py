"""Select verified, non-commercial deep links for each researched method."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.schemas.editorial_v3 import ExternalReference, SourceUsagePolicy
from app.schemas.editorial_v3_runtime import MethodInventoryItem, StructuredSourceDocument


def _tokens(value: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-z0-9áàâãéèêíóôõúçñ]{3,}", value.casefold())
        if item not in {"para", "como", "com", "uma", "uns", "das", "dos", "the", "and"}
    }


class ExternalReferenceValidator:
    def select(
        self,
        methods: list[MethodInventoryItem],
        documents: list[StructuredSourceDocument],
    ) -> dict[str, ExternalReference]:
        references: dict[str, ExternalReference] = {}
        for method in methods:
            method_tokens = _tokens(" ".join([method.name, *method.aliases, method.distinguishing_feature]))
            ranked: list[tuple[float, StructuredSourceDocument, float]] = []
            for document in documents:
                assessment = document.assessment
                if not assessment.eligible_for_external_reference:
                    continue
                if assessment.usage_policy not in {
                    SourceUsagePolicy.authoritative_evidence,
                    SourceUsagePolicy.corroborating_evidence,
                }:
                    continue
                if assessment.source_role.value in {
                    "ecommerce_blog",
                    "ecommerce_transactional",
                    "marketplace",
                    "commercial_first_party",
                    "community_question_discovery",
                }:
                    continue
                searchable = (document.title + " " + document.plain_text[:30000]).casefold()
                body_tokens = _tokens(searchable)
                token_overlap = len(method_tokens & body_tokens) / max(1, len(method_tokens))
                exact_names = [method.name, *method.aliases]
                phrase_match = any(
                    value.strip() and value.casefold() in searchable
                    for value in exact_names
                )
                overlap = max(0.95 if phrase_match else 0.0, token_overlap)
                ordered_count = sum(len(section.ordered_steps) for section in document.sections)
                unordered_count = sum(len(section.unordered_items) for section in document.sections)
                procedure_markers = len(
                    re.findall(
                        r"(?i)\b(?:passo|etapa|procedimento|como fazer|step|procedure|materials?|materiais)\b",
                        document.plain_text[:30000],
                    )
                )
                procedural = min(
                    1.0,
                    (0.55 if ordered_count >= 3 else 0.0)
                    + (0.20 if unordered_count >= 4 else 0.0)
                    + min(0.25, procedure_markers * 0.04),
                )
                if overlap < 0.7 or procedural < 0.6:
                    continue
                score = overlap * 0.55 + procedural * 0.30 + assessment.priority_score * 0.15
                ranked.append((score, document, overlap))
            if not ranked:
                continue
            _, selected, overlap = max(ranked, key=lambda item: item[0])
            references[method.method_id] = ExternalReference(
                method_id=method.method_id,
                url=selected.canonical_url,
                anchor_text=f"Consulte o guia completo sobre {method.name}",
                title=selected.title,
                author=selected.author,
                publisher=selected.publisher or urlsplit(str(selected.canonical_url)).netloc,
                source_role=selected.assessment.source_role,
                source_usage_policy=selected.assessment.usage_policy,
                is_ecommerce_domain=False,
                is_transactional_page=False,
                content_match_score=min(1.0, overlap),
                procedural_depth_score=min(1.0, max(0.6, self._procedural_depth(selected))),
                verified_at=datetime.now(timezone.utc),
                status="approved",
                rejection_reasons=[],
            )
        return references

    @staticmethod
    def _procedural_depth(document: StructuredSourceDocument) -> float:
        ordered_count = sum(len(section.ordered_steps) for section in document.sections)
        unordered_count = sum(len(section.unordered_items) for section in document.sections)
        markers = len(
            re.findall(
                r"(?i)\b(?:passo|etapa|procedimento|como fazer|step|procedure|materials?|materiais)\b",
                document.plain_text[:30000],
            )
        )
        return min(
            1.0,
            (0.55 if ordered_count >= 3 else 0.0)
            + (0.20 if unordered_count >= 4 else 0.0)
            + min(0.25, markers * 0.04),
        )
