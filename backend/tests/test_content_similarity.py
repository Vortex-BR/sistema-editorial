from app.services.content_similarity import ContentSimilarityService


def test_lexical_similarity_identifies_near_duplicate_fingerprints():
    left = "guia de cultivo de tomates em vasos para iniciantes"
    right = "guia completo de cultivo de tomates em vasos para iniciantes"

    score = ContentSimilarityService._lexical_score(left, right)

    assert score > 0.8


def test_lexical_similarity_separates_distinct_topics():
    score = ContentSimilarityService._lexical_score(
        "guia de cultivo de tomates em vasos",
        "como escolher um seguro empresarial",
    )

    assert score == 0
