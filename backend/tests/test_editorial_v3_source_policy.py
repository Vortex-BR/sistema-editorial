from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.schemas.editorial_v3 import (
    ContentKnowledgeContract,
    EvidenceRole,
    ExternalReference,
    ResearchSourceSignals,
    SourceOwnershipType,
    SourcePageType,
    SourceRole,
    SourceUsagePolicy,
)
from app.services.editorial_v3.knowledge_contract import (
    KnowledgeContractBuilder,
    KnowledgeContractInput,
)
from app.services.editorial_v3.source_policy import ResearchSourcePolicyService


def _contract():
    return KnowledgeContractBuilder().build(
        KnowledgeContractInput(
            topic="germinação de sementes de cannabis",
            reader_start_state="Leitor que precisa compreender a semente antes de escolher um método.",
            reader_final_state="Leitor capaz de reconhecer a emergência da plântula no substrato.",
            article_promise="Explicar a semente, comparar métodos e acompanhar o processo até a emergência.",
            scope_limit="Encerrar na emergência da plântula, sem avançar para o cultivo posterior.",
        )
    )


def _scientific(url: str = "https://journal.example.org/research/seed-germination"):
    return ResearchSourcePolicyService().assess(
        ResearchSourceSignals(
            url=url,
            ownership_type=SourceOwnershipType.scientific_publisher,
            page_type=SourcePageType.research_article,
            peer_reviewed=True,
            primary_research=True,
            author_present=True,
            publication_date_present=True,
            references_present=True,
            topic_relevance_score=0.95,
            content_depth_score=0.9,
            scientific_support_score=0.95,
            procedural_depth_score=0.4,
        )
    )


def _institutional(url: str = "https://university.example.edu/extension/germination-guide"):
    return ResearchSourcePolicyService().assess(
        ResearchSourceSignals(
            url=url,
            ownership_type=SourceOwnershipType.academic,
            page_type=SourcePageType.technical_guide,
            institutional_affiliation=True,
            author_present=True,
            references_present=True,
            topic_relevance_score=0.95,
            content_depth_score=0.9,
            scientific_support_score=0.8,
            procedural_depth_score=0.9,
        )
    )


def _ecommerce_blog():
    return ResearchSourcePolicyService().assess(
        ResearchSourceSignals(
            url="https://seed-shop.example.com/blog/germination-methods",
            ownership_type=SourceOwnershipType.ecommerce,
            page_type=SourcePageType.ecommerce_blog_article,
            is_ecommerce_domain=True,
            author_present=True,
            references_present=True,
            topic_relevance_score=0.95,
            content_depth_score=0.85,
            procedural_depth_score=0.9,
            scientific_support_score=0.5,
            commercial_intensity_score=0.6,
        )
    )


def test_transactional_ecommerce_product_page_is_rejected():
    assessment = ResearchSourcePolicyService().assess(
        ResearchSourceSignals(
            url="https://shop.example.com/produto/semente-x",
            ownership_type=SourceOwnershipType.ecommerce,
            page_type=SourcePageType.product_page,
            is_ecommerce_domain=True,
            has_product_schema=True,
            has_offer_schema=True,
            has_price=True,
            has_add_to_cart=True,
            topic_relevance_score=1.0,
            content_depth_score=1.0,
        )
    )

    assert assessment.source_role == SourceRole.ecommerce_transactional
    assert assessment.usage_policy == SourceUsagePolicy.rejected
    assert assessment.priority_score == 0
    assert not assessment.eligible_for_primary_evidence
    assert not assessment.eligible_for_external_reference


def test_ecommerce_blog_is_comparison_only_and_never_counts_as_independent():
    assessment = _ecommerce_blog()

    assert assessment.source_role == SourceRole.ecommerce_blog
    assert assessment.usage_policy == SourceUsagePolicy.comparison_only
    assert assessment.minimum_independent_corroborators == 2
    assert not assessment.counts_toward_independent_source_diversity
    assert not assessment.absolute_claim_support_allowed
    assert not assessment.eligible_for_external_reference
    assert EvidenceRole.comparison in assessment.allowed_evidence_roles


def test_ecommerce_blog_cannot_support_a_normal_or_absolute_claim():
    policy = ResearchSourcePolicyService()

    normal = policy.validate_bundle([_ecommerce_blog()])
    absolute = policy.validate_bundle(
        [_ecommerce_blog(), _scientific(), _institutional()],
        comparison_context=True,
        absolute_claim=True,
    )

    assert normal.status == "blocked"
    assert any("Comparison-only" in item for item in normal.blockers)
    assert absolute.status == "blocked"
    assert any("absolute claim" in item for item in absolute.blockers)


def test_ecommerce_blog_can_only_be_compared_after_two_independent_sources():
    policy = ResearchSourcePolicyService()

    insufficient = policy.validate_bundle(
        [_ecommerce_blog(), _scientific()],
        comparison_context=True,
    )
    sufficient = policy.validate_bundle(
        [_ecommerce_blog(), _scientific(), _institutional()],
        comparison_context=True,
    )

    assert insufficient.status == "blocked"
    assert any("two independent" in item for item in insufficient.blockers)
    assert sufficient.status == "passed"
    assert sufficient.independent_source_count == 2
    assert sufficient.ignored_commercial_source_count == 1


def test_critical_claim_requires_two_independent_noncommercial_sources():
    policy = ResearchSourcePolicyService()

    one_source = policy.validate_bundle([_scientific()], critical_claim=True)
    two_sources = policy.validate_bundle(
        [_scientific(), _institutional()], critical_claim=True
    )

    assert one_source.status == "blocked"
    assert two_sources.status == "passed"


def test_wiki_is_contextual_and_not_sufficient_for_a_critical_claim():
    policy = ResearchSourcePolicyService()
    wiki = policy.assess(
        ResearchSourceSignals(
            url="https://pt.wikipedia.org/wiki/Germinacao",
            ownership_type=SourceOwnershipType.encyclopedia,
            page_type=SourcePageType.encyclopedia_article,
            author_present=True,
            references_present=True,
            topic_relevance_score=0.9,
            content_depth_score=0.8,
            scientific_support_score=0.5,
        )
    )

    decision = policy.validate_bundle([wiki], critical_claim=True)

    assert wiki.source_role == SourceRole.encyclopedic_discovery
    assert wiki.usage_policy == SourceUsagePolicy.corroborating_evidence
    assert not wiki.eligible_for_primary_evidence
    assert decision.status == "blocked"



def test_noncommercial_article_with_products_in_path_is_not_rejected_by_url_alone():
    assessment = ResearchSourcePolicyService().assess(
        ResearchSourceSignals(
            url="https://university.example.edu/research/products-of-germination",
            ownership_type=SourceOwnershipType.academic,
            page_type=SourcePageType.unknown,
            institutional_affiliation=True,
            author_present=True,
            references_present=True,
            topic_relevance_score=0.9,
            content_depth_score=0.8,
            scientific_support_score=0.8,
        )
    )

    assert assessment.usage_policy == SourceUsagePolicy.authoritative_evidence
    assert assessment.source_role == SourceRole.institutional


def test_marketplace_listing_is_always_rejected():
    assessment = ResearchSourcePolicyService().assess(
        ResearchSourceSignals(
            url="https://market.example.com/listing/123",
            ownership_type=SourceOwnershipType.marketplace,
            page_type=SourcePageType.marketplace_listing,
            marketplace_signals=True,
            topic_relevance_score=1.0,
            content_depth_score=1.0,
        )
    )

    assert assessment.source_role == SourceRole.marketplace
    assert assessment.usage_policy == SourceUsagePolicy.rejected

def test_search_rank_is_not_part_of_source_assessment_contract():
    fields = ResearchSourceSignals.model_fields

    assert "search_rank" not in fields
    assert "search_position" not in fields


def test_contract_embeds_non_relaxable_source_policy():
    contract = _contract()

    assert contract.research_source_policy.reject_transactional_ecommerce is True
    assert contract.research_source_policy.ecommerce_blog_usage == "comparison_only"
    assert contract.research_source_policy.ecommerce_blog_min_independent_corroborators == 2
    assert contract.research_source_policy.search_rank_defines_authority is False

    payload = deepcopy(contract.model_dump(mode="json"))
    payload["research_source_policy"]["prioritized_source_roles"].append(
        SourceRole.ecommerce_blog.value
    )
    with pytest.raises(ValidationError, match="Commercial sources cannot be prioritized"):
        ContentKnowledgeContract.model_validate(payload)


def test_external_reference_rejects_ecommerce_even_when_content_is_deep():
    with pytest.raises(ValidationError, match="Commercial or community"):
        ExternalReference(
            method_id="paper_method",
            url="https://shop.example.com/blog/paper-method",
            anchor_text="Guia completo sobre o método do papel",
            title="Como germinar usando papel absorvente",
            author="Equipe da loja",
            publisher="Seed Shop",
            source_role=SourceRole.ecommerce_blog,
            source_usage_policy=SourceUsagePolicy.comparison_only,
            is_ecommerce_domain=True,
            is_transactional_page=False,
            content_match_score=0.99,
            procedural_depth_score=0.99,
            verified_at="2026-07-17T18:00:00Z",
            status="approved",
        )
