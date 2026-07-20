"""Deterministic source-selection policy for Editorial Intelligence V3.

Search position is intentionally absent from the input.  The service evaluates
what a page is, who controls it, and what kind of evidence it can legitimately
support.  Transactional e-commerce pages are rejected.  Editorial content
published by an e-commerce business is comparison-only and never counts as
independent corroboration or as the final external reference for a method.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.schemas.editorial_v3 import (
    EvidenceBundleDecision,
    EvidenceRole,
    ResearchSourcePolicyContract,
    ResearchSourceSignals,
    SourceAssessment,
    SourceOwnershipType,
    SourcePageType,
    SourceRole,
    SourceUsagePolicy,
)


class ResearchSourcePolicyService:
    TRANSACTIONAL_PATH_MARKERS = {
        "product",
        "products",
        "produto",
        "produtos",
        "shop",
        "store",
        "loja",
        "comprar",
        "cart",
        "carrinho",
        "checkout",
        "marketplace",
        "oferta",
        "ofertas",
    }
    BLOG_PATH_MARKERS = {
        "blog",
        "article",
        "articles",
        "artigo",
        "artigos",
        "learn",
        "guide",
        "guias",
    }

    def __init__(self, policy: ResearchSourcePolicyContract | None = None):
        self.policy = policy or ResearchSourcePolicyContract()

    def assess(self, signals: ResearchSourceSignals) -> SourceAssessment:
        ownership = signals.ownership_type
        page_type = self._resolved_page_type(signals)
        ecommerce_domain = signals.is_ecommerce_domain or ownership in {
            SourceOwnershipType.ecommerce,
            SourceOwnershipType.manufacturer,
            SourceOwnershipType.marketplace,
        }

        if (
            page_type == SourcePageType.marketplace_listing
            or signals.marketplace_signals
        ):
            return self._rejected(
                signals,
                ownership=SourceOwnershipType.marketplace,
                page_type=SourcePageType.marketplace_listing,
                role=SourceRole.marketplace,
                reasons=[
                    "marketplace_listing_rejected",
                    "commercial_transactional_source",
                ],
            )

        if self._is_transactional(signals, page_type):
            return self._rejected(
                signals,
                ownership=ownership,
                page_type=page_type,
                role=SourceRole.ecommerce_transactional,
                reasons=["transactional_ecommerce_rejected", "not_editorial_evidence"],
            )

        if ecommerce_domain:
            return SourceAssessment(
                url=signals.url,
                ownership_type=ownership,
                page_type=(
                    SourcePageType.ecommerce_blog_article
                    if page_type
                    in {
                        SourcePageType.independent_article,
                        SourcePageType.technical_guide,
                        SourcePageType.unknown,
                        SourcePageType.other,
                    }
                    else page_type
                ),
                source_role=(
                    SourceRole.commercial_first_party
                    if ownership == SourceOwnershipType.manufacturer
                    else SourceRole.ecommerce_blog
                ),
                usage_policy=SourceUsagePolicy.comparison_only,
                priority_score=self._score(signals, base=0.18, commercial_penalty=0.45),
                eligible_for_primary_evidence=False,
                eligible_for_corroborating_evidence=False,
                eligible_for_external_reference=False,
                counts_toward_independent_source_diversity=False,
                requires_independent_corroboration=True,
                minimum_independent_corroborators=self.policy.ecommerce_blog_min_independent_corroborators,
                absolute_claim_support_allowed=False,
                allowed_evidence_roles=[
                    EvidenceRole.comparison,
                    EvidenceRole.limitation,
                    EvidenceRole.common_error,
                ],
                reason_codes=[
                    "ecommerce_editorial_comparison_only",
                    "requires_two_independent_noncommercial_sources",
                    "cannot_support_absolute_claim",
                    "cannot_be_external_method_reference",
                ],
                warnings=[
                    "Use only to discover or compare a reported claim; verify the claim in independent sources before publication."
                ],
            )

        (
            role,
            usage,
            base,
            primary,
            corroborating,
            external,
            independent,
            min_corroborators,
            absolute,
            allowed,
        ) = self._noncommercial_policy(signals, ownership, page_type)
        reasons = [f"classified_as_{role.value}", f"usage_{usage.value}"]
        warnings: list[str] = []
        if role in {SourceRole.news_reporting, SourceRole.encyclopedic_discovery}:
            warnings.append(
                "Use as contextual or discovery evidence; critical technical claims still require scientific, institutional, or independent technical support."
            )
        if role == SourceRole.specialist_practical and not signals.references_present:
            warnings.append(
                "Specialist practical content without references should not be the sole support for a critical claim."
            )

        return SourceAssessment(
            url=signals.url,
            ownership_type=ownership,
            page_type=page_type,
            source_role=role,
            usage_policy=usage,
            priority_score=self._score(signals, base=base),
            eligible_for_primary_evidence=primary,
            eligible_for_corroborating_evidence=corroborating,
            eligible_for_external_reference=(
                external
                and signals.topic_relevance_score >= 0.7
                and signals.procedural_depth_score >= 0.6
            ),
            counts_toward_independent_source_diversity=independent,
            requires_independent_corroboration=min_corroborators > 0,
            minimum_independent_corroborators=min_corroborators,
            absolute_claim_support_allowed=absolute,
            allowed_evidence_roles=allowed,
            reason_codes=reasons,
            warnings=warnings,
        )

    def validate_bundle(
        self,
        assessments: list[SourceAssessment],
        *,
        critical_claim: bool = False,
        absolute_claim: bool = False,
        comparison_context: bool = False,
        external_reference: bool = False,
    ) -> EvidenceBundleDecision:
        blockers: list[str] = []
        warnings: list[str] = []
        rejected = [
            item
            for item in assessments
            if item.usage_policy == SourceUsagePolicy.rejected
        ]
        if rejected:
            blockers.append(
                "Rejected transactional or marketplace sources are present in the evidence bundle"
            )

        comparison_only = [
            item
            for item in assessments
            if item.usage_policy == SourceUsagePolicy.comparison_only
        ]
        if comparison_only and not comparison_context:
            blockers.append(
                "Comparison-only commercial sources cannot support a normal factual claim"
            )
        if comparison_only and absolute_claim:
            blockers.append(
                "Commercial comparison sources cannot support an absolute claim"
            )

        eligible = [
            item
            for item in assessments
            if item.usage_policy
            in {
                SourceUsagePolicy.authoritative_evidence,
                SourceUsagePolicy.corroborating_evidence,
            }
        ]
        independent = [
            item for item in eligible if item.counts_toward_independent_source_diversity
        ]
        authoritative = [
            item for item in eligible if item.eligible_for_primary_evidence
        ]

        if external_reference:
            ineligible_reference = [
                item for item in assessments if not item.eligible_for_external_reference
            ]
            if ineligible_reference:
                blockers.append(
                    "External references must be noncommercial, evidence-eligible, relevant, and procedurally deep"
                )

        if (
            critical_claim
            and len(independent) < self.policy.critical_claim_min_independent_sources
        ):
            blockers.append(
                "Critical claims require at least two independent noncommercial sources"
            )
        if not comparison_context and assessments and not authoritative:
            blockers.append(
                "A factual claim requires at least one authoritative scientific, institutional, or qualified technical source"
            )

        for commercial in comparison_only:
            if len(independent) < commercial.minimum_independent_corroborators:
                blockers.append(
                    "An e-commerce blog claim requires at least two independent noncommercial corroborators"
                )

        if comparison_only:
            warnings.append(
                "Commercial sources were ignored for authority and source-diversity counts"
            )

        blockers = list(dict.fromkeys(blockers))
        warnings = list(dict.fromkeys(warnings))
        return EvidenceBundleDecision(
            status="blocked" if blockers else "passed",
            eligible_source_count=len(eligible),
            independent_source_count=len(independent),
            authoritative_source_count=len(authoritative),
            ignored_commercial_source_count=len(comparison_only) + len(rejected),
            blockers=blockers,
            warnings=warnings,
        )

    def _resolved_page_type(self, signals: ResearchSourceSignals) -> SourcePageType:
        if signals.page_type != SourcePageType.unknown:
            return signals.page_type
        path_parts = {
            part.lower() for part in urlparse(str(signals.url)).path.split("/") if part
        }
        commerce_signal_present = any(
            (
                signals.is_ecommerce_domain,
                signals.ownership_type
                in {
                    SourceOwnershipType.ecommerce,
                    SourceOwnershipType.manufacturer,
                    SourceOwnershipType.marketplace,
                },
                signals.has_product_schema,
                signals.has_offer_schema,
                signals.has_price,
                signals.has_sku,
                signals.has_add_to_cart,
                signals.has_cart_or_checkout_links,
                signals.marketplace_signals,
            )
        )
        if commerce_signal_present and path_parts.intersection(
            self.TRANSACTIONAL_PATH_MARKERS
        ):
            return SourcePageType.product_page
        if path_parts.intersection(self.BLOG_PATH_MARKERS):
            return (
                SourcePageType.ecommerce_blog_article
                if signals.is_ecommerce_domain
                else SourcePageType.independent_article
            )
        if signals.primary_research or signals.peer_reviewed:
            return SourcePageType.research_article
        if signals.review_research:
            return SourcePageType.review_article
        return SourcePageType.other

    @staticmethod
    def _is_transactional(
        signals: ResearchSourceSignals, page_type: SourcePageType
    ) -> bool:
        if page_type in {
            SourcePageType.product_page,
            SourcePageType.category_page,
            SourcePageType.marketplace_listing,
            SourcePageType.commercial_landing_page,
            SourcePageType.store_search_page,
        }:
            return True
        commerce_markers = sum(
            int(value)
            for value in (
                signals.has_product_schema,
                signals.has_offer_schema,
                signals.has_price,
                signals.has_sku,
                signals.has_add_to_cart,
                signals.has_cart_or_checkout_links,
            )
        )
        return commerce_markers >= 2

    def _noncommercial_policy(
        self,
        signals: ResearchSourceSignals,
        ownership: SourceOwnershipType,
        page_type: SourcePageType,
    ) -> tuple[
        SourceRole,
        SourceUsagePolicy,
        float,
        bool,
        bool,
        bool,
        bool,
        int,
        bool,
        list[EvidenceRole],
    ]:
        broad_roles = list(EvidenceRole)
        if signals.primary_research or page_type == SourcePageType.research_article:
            return (
                SourceRole.scientific_primary,
                SourceUsagePolicy.authoritative_evidence,
                0.95,
                True,
                True,
                True,
                True,
                0,
                True,
                broad_roles,
            )
        if signals.review_research or page_type == SourcePageType.review_article:
            return (
                SourceRole.scientific_review,
                SourceUsagePolicy.authoritative_evidence,
                0.94,
                True,
                True,
                True,
                True,
                0,
                True,
                broad_roles,
            )
        if page_type == SourcePageType.academic_repository:
            return (
                SourceRole.academic_repository,
                SourceUsagePolicy.authoritative_evidence,
                0.9,
                True,
                True,
                True,
                True,
                0,
                True,
                broad_roles,
            )
        if page_type == SourcePageType.scientific_database:
            return (
                SourceRole.scientific_database,
                SourceUsagePolicy.authoritative_evidence,
                0.9,
                True,
                True,
                False,
                True,
                0,
                True,
                [EvidenceRole.definition, EvidenceRole.mechanism, EvidenceRole.risk],
            )
        if (
            ownership
            in {
                SourceOwnershipType.academic,
                SourceOwnershipType.public_institution,
                SourceOwnershipType.nonprofit_institution,
            }
            or signals.institutional_affiliation
        ):
            return (
                SourceRole.institutional,
                SourceUsagePolicy.authoritative_evidence,
                0.88,
                True,
                True,
                True,
                True,
                0,
                True,
                broad_roles,
            )
        if page_type == SourcePageType.technical_guide:
            # A practical independent guide can be extremely useful for sequence,
            # observations and troubleshooting, but its self-declared detail does
            # not make it scientifically authoritative. Institutional ownership
            # was handled above; all remaining technical guides are corroborating
            # evidence and must not support absolute claims by themselves.
            return (
                SourceRole.technical_procedural,
                SourceUsagePolicy.corroborating_evidence,
                0.78,
                False,
                True,
                True,
                True,
                0 if signals.references_present else 1,
                False,
                broad_roles,
            )
        if (
            ownership == SourceOwnershipType.news_organization
            or page_type == SourcePageType.news_article
        ):
            return (
                SourceRole.news_reporting,
                SourceUsagePolicy.corroborating_evidence,
                0.68,
                False,
                True,
                False,
                True,
                1,
                False,
                [
                    EvidenceRole.definition,
                    EvidenceRole.risk,
                    EvidenceRole.limitation,
                    EvidenceRole.exception,
                ],
            )
        if (
            ownership == SourceOwnershipType.encyclopedia
            or page_type == SourcePageType.encyclopedia_article
        ):
            return (
                SourceRole.encyclopedic_discovery,
                SourceUsagePolicy.corroborating_evidence,
                0.65,
                False,
                True,
                False,
                True,
                1,
                False,
                [
                    EvidenceRole.definition,
                    EvidenceRole.mechanism,
                    EvidenceRole.comparison,
                ],
            )
        if (
            ownership == SourceOwnershipType.community
            or page_type == SourcePageType.forum_thread
        ):
            return (
                SourceRole.community_question_discovery,
                SourceUsagePolicy.discovery_only,
                0.2,
                False,
                False,
                False,
                False,
                0,
                False,
                [],
            )
        if (
            ownership == SourceOwnershipType.independent_editorial
            or page_type == SourcePageType.independent_article
        ):
            role = (
                SourceRole.specialist_practical
                if signals.procedural_depth_score >= 0.6
                else SourceRole.independent_editorial
            )
            return (
                role,
                SourceUsagePolicy.corroborating_evidence,
                0.72,
                False,
                True,
                True,
                True,
                1 if not signals.references_present else 0,
                False,
                broad_roles,
            )
        return (
            SourceRole.unknown,
            SourceUsagePolicy.discovery_only,
            0.1,
            False,
            False,
            False,
            False,
            0,
            False,
            [],
        )

    @staticmethod
    def _score(
        signals: ResearchSourceSignals,
        *,
        base: float,
        commercial_penalty: float = 0.0,
    ) -> float:
        quality = (
            signals.topic_relevance_score * 0.28
            + signals.content_depth_score * 0.18
            + signals.procedural_depth_score * 0.18
            + signals.scientific_support_score * 0.18
            + signals.freshness_score * 0.08
            + (0.04 if signals.author_present else 0.0)
            + (0.04 if signals.references_present else 0.0)
            + (0.02 if signals.publication_date_present else 0.0)
        )
        score = (base * 0.55) + (quality * 0.45)
        score -= signals.commercial_intensity_score * commercial_penalty
        return round(min(1.0, max(0.0, score)), 4)

    @staticmethod
    def _rejected(
        signals: ResearchSourceSignals,
        *,
        ownership: SourceOwnershipType,
        page_type: SourcePageType,
        role: SourceRole,
        reasons: list[str],
    ) -> SourceAssessment:
        return SourceAssessment(
            url=signals.url,
            ownership_type=ownership,
            page_type=page_type,
            source_role=role,
            usage_policy=SourceUsagePolicy.rejected,
            priority_score=0.0,
            eligible_for_primary_evidence=False,
            eligible_for_corroborating_evidence=False,
            eligible_for_external_reference=False,
            counts_toward_independent_source_diversity=False,
            requires_independent_corroboration=False,
            minimum_independent_corroborators=0,
            absolute_claim_support_allowed=False,
            allowed_evidence_roles=[],
            reason_codes=reasons,
            warnings=[],
        )
