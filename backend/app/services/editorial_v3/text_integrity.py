"""Deterministic text-integrity helpers for Editorial V3 generation.

These helpers do not pretend to replace a semantic model.  They provide a
conservative floor that model-produced labels cannot bypass: quote order,
number/negation preservation, lexical support, factuality markers and stable
Unicode identifiers.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)?", re.UNICODE)
_NUMBER = re.compile(r"(?<!\w)[+-]?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d+)?(?:\s?%|\s?[a-zA-Zµ°²³/]+)?", re.UNICODE)
_URL = re.compile(r"https?://", re.I)
_COMPARATIVE = re.compile(
    r"\b(?:mais|menos)\s+[a-zà-ÿ0-9-]+(?:\s+[a-zà-ÿ0-9-]+){0,3}\s+(?:que|do que)\b"
    r"|\b(?:more|less)\s+[a-z0-9-]+(?:\s+[a-z0-9-]+){0,3}\s+than\b"
    r"|\b(?:más|menos)\s+[a-záéíóúñ0-9-]+(?:\s+[a-záéíóúñ0-9-]+){0,3}\s+que\b",
    re.I,
)

_NEGATIONS = {
    "nao",
    "nunca",
    "jamais",
    "sem",
    "nenhum",
    "nenhuma",
    "nem",
    "not",
    "never",
    "without",
    "no",
    "sin",
    "nunca",
    "ningun",
    "ninguna",
}

_FACTUAL_MARKERS = {
    # PT
    "causa",
    "causam",
    "provoca",
    "provocam",
    "aumenta",
    "aumentam",
    "reduz",
    "reduzem",
    "melhora",
    "melhoram",
    "piora",
    "pioram",
    "depende",
    "exige",
    "requer",
    "ocorre",
    "acontece",
    "leva",
    "resulta",
    "significa",
    "consiste",
    "contem",
    "possui",
    "apresenta",
    "permite",
    "impede",
    "deve",
    "precisa",
    "recomendado",
    "ideal",
    "seguro",
    "eficaz",
    "maior",
    "menor",
    "melhor",
    "pior",
    # EN/ES common markers
    "causes",
    "increases",
    "reduces",
    "requires",
    "occurs",
    "results",
    "means",
    "contains",
    "recommended",
    "safe",
    "effective",
    "causa",
    "aumenta",
    "reduce",
    "requiere",
    "ocurre",
    "resulta",
    "contiene",
    "seguro",
    "eficaz",
}


_DECLARATIVE_PREDICATES = {
    # PT
    "absorve", "armazena", "conduz", "contem", "contém", "cresce", "decompoe", "decompõe",
    "define", "elimina", "forma", "funciona", "gera", "inclui", "indica", "libera", "mantem",
    "mantém", "mede", "produz", "reage", "remove", "retem", "retém", "transporta", "transportam", "utiliza",
    "varia", "vive", "existe", "sao", "são", "fica", "permanece",
    # EN
    "absorbs", "stores", "conducts", "contains", "grows", "defines", "forms", "works", "generates",
    "includes", "indicates", "releases", "maintains", "measures", "produces", "removes", "retains",
    "transports", "uses", "varies", "exists", "is", "are", "remains",
    # ES
    "absorbe", "almacena", "conduce", "contiene", "crece", "define", "forma", "funciona", "genera",
    "incluye", "indica", "libera", "mantiene", "mide", "produce", "elimina", "retiene", "transporta",
    "utiliza", "varia", "existe", "es", "son", "permanece",
}

_EDITORIAL_PREFIXES = (
    "neste artigo",
    "neste guia",
    "a seguir",
    "agora vamos",
    "em resumo",
    "em sintese",
    "para concluir",
    "este conteudo",
    "esta secao",
    "o objetivo aqui",
    "o fechamento",
    "a conclusao",
    "esta conclusao",
    "este paragrafo",
    "a secao",
    "esta secao",
    "this article",
    "this guide",
    "in summary",
    "to conclude",
    "en este articulo",
    "en esta guia",
    "en resumen",
)


def strip_diacritics(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in normalized if not unicodedata.combining(char))


def stable_slug(value: str, *, separator: str = "_", limit: int = 100) -> str:
    """Create a stable ASCII identifier without deleting accented letters."""

    ascii_value = strip_diacritics(value).casefold()
    slug = re.sub(r"[^a-z0-9]+", separator, ascii_value).strip(separator)
    return (slug or "item")[:limit].rstrip(separator) or "item"


def normalized_text(value: str) -> str:
    value = strip_diacritics(value).casefold()
    value = re.sub(r"[^a-z0-9%+./-]+", " ", value)
    return _SPACE.sub(" ", value).strip()


def tokens(value: str) -> list[str]:
    return [strip_diacritics(item).casefold() for item in _WORD.findall(value or "")]


def content_tokens(value: str) -> list[str]:
    return [item for item in tokens(value) if len(item) > 2]


def quote_is_present(quote: str, text: str, *, fuzzy_threshold: float = 0.94) -> bool:
    """Verify a quote using ordered contiguous text or a tight local fuzzy window.

    The former implementation accepted a quote when 90% of its words occurred
    anywhere in the document.  This implementation preserves order and
    locality, so shuffled words cannot pass.
    """

    normalized_quote = normalized_text(quote)
    normalized_source = normalized_text(text)
    if not normalized_quote or not normalized_source:
        return False
    if normalized_quote in normalized_source:
        return True

    quote_tokens = normalized_quote.split()
    source_tokens = normalized_source.split()
    if len(quote_tokens) < 5 or len(source_tokens) < len(quote_tokens):
        return False

    # Allow small punctuation/OCR differences, but only inside a local ordered
    # window whose size is close to the quote length.
    min_window = max(5, len(quote_tokens) - 2)
    max_window = min(len(source_tokens), len(quote_tokens) + 3)
    quote_joined = " ".join(quote_tokens)
    for size in range(min_window, max_window + 1):
        for start in range(0, len(source_tokens) - size + 1):
            candidate = " ".join(source_tokens[start : start + size])
            if SequenceMatcher(None, quote_joined, candidate).ratio() >= fuzzy_threshold:
                return True
    return False


def numeric_facts(value: str) -> tuple[str, ...]:
    return tuple(normalized_text(item) for item in _NUMBER.findall(value or ""))


def negation_signature(value: str) -> frozenset[str]:
    return frozenset(item for item in tokens(value) if item in _NEGATIONS)


def is_potentially_factual(value: str, *, block_type: str | None = None) -> bool:
    """Conservative deterministic factuality classifier.

    It intentionally catches obvious verifiable statements that a model could
    otherwise label as editorial.  Pure navigational/editorial transitions are
    excluded unless they contain numbers, URLs or factual markers.
    """

    text = (value or "").strip()
    if not text:
        return False
    normalized = normalized_text(text)
    if _URL.search(text) or _NUMBER.search(text) or _COMPARATIVE.search(text):
        return True
    word_tokens = set(normalized.split())
    marker_hit = bool(word_tokens & _FACTUAL_MARKERS)
    if block_type in {"h1", "h2", "h3"}:
        return marker_hit
    # Sentences whose grammatical subject is the article/section/conclusion are
    # editorial metadata, not domain claims. Quantities and URLs were already
    # handled above, so they cannot use this escape hatch.
    if normalized.startswith(_EDITORIAL_PREFIXES):
        return False
    if text.rstrip().endswith("?"):
        return False
    predicate_hit = bool(
        word_tokens & {normalized_text(item) for item in _DECLARATIVE_PREDICATES}
    )
    # Do not infer factuality from generic word endings: that heuristic marked
    # editorial transitions and conclusions as facts. Explicit causal,
    # quantitative, comparative or domain predicates remain deterministic.
    return marker_hit or predicate_hit


def lexical_overlap(left: str, right: str) -> float:
    left_tokens = set(content_tokens(left))
    right_tokens = set(content_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, len(left_tokens))


def claim_support_score(sentence: str, claim_text: str) -> float:
    """Return a conservative lexical support score from claim to sentence."""

    if numeric_facts(sentence) and not set(numeric_facts(sentence)).issubset(
        set(numeric_facts(claim_text))
    ):
        return 0.0
    if negation_signature(sentence) != negation_signature(claim_text):
        # A missing/extra negation reverses many claims and must not be treated
        # as a minor lexical difference.
        return 0.0
    base = lexical_overlap(sentence, claim_text)
    sequence = SequenceMatcher(
        None, normalized_text(sentence), normalized_text(claim_text)
    ).ratio()
    return round(max(base, sequence * 0.75), 4)


def claim_supports_sentence(
    sentence: str,
    claim_text: str,
    *,
    conditions: Iterable[str] = (),
    limitations: Iterable[str] = (),
    minimum_score: float = 0.42,
) -> tuple[bool, float, str]:
    score = claim_support_score(sentence, claim_text)
    if score < minimum_score:
        return False, score, "lexical_entailment_below_threshold"

    sentence_norm = normalized_text(sentence)
    # When the claim is explicitly conditional/limited and the sentence uses
    # absolute language, reject unless at least one condition/limitation is
    # reflected in the sentence.
    qualifiers = [normalized_text(item) for item in [*conditions, *limitations] if item]
    absolute_markers = {"sempre", "nunca", "garante", "garantido", "todos", "qualquer", "always", "guarantees", "all"}
    if qualifiers and set(sentence_norm.split()) & absolute_markers:
        if not any(lexical_overlap(item, sentence_norm) >= 0.35 for item in qualifiers):
            return False, score, "claim_conditions_or_limitations_omitted"
    return True, score, "supported"


def support_group_compatible(left: str, right: str) -> tuple[bool, str]:
    """Check whether two claim texts are safe to treat as corroboration."""

    if numeric_facts(left) != numeric_facts(right):
        return False, "numeric_mismatch"
    if negation_signature(left) != negation_signature(right):
        return False, "negation_mismatch"
    left_set = set(content_tokens(left))
    right_set = set(content_tokens(right))
    if not left_set or not right_set:
        return False, "empty_semantic_signature"
    jaccard = len(left_set & right_set) / len(left_set | right_set)
    directional = min(lexical_overlap(left, right), lexical_overlap(right, left))
    if max(jaccard, directional) < 0.38:
        return False, "semantic_overlap_below_threshold"
    return True, "compatible"


def revision_preserves_meaning(original: str, revised: str) -> tuple[bool, str]:
    if numeric_facts(original) != numeric_facts(revised):
        return False, "numbers_or_units_changed"
    if negation_signature(original) != negation_signature(revised):
        return False, "negation_changed"
    original_tokens = set(content_tokens(original))
    revised_tokens = set(content_tokens(revised))
    if original_tokens and revised_tokens:
        containment = len(original_tokens & revised_tokens) / max(1, min(len(original_tokens), len(revised_tokens)))
        if containment < 0.48:
            return False, "semantic_overlap_too_low"
    return True, "preserved"
