"""Deterministic signals for editorial prose quality.

This module intentionally does not attempt to detect whether text was written by
AI.  It measures visible defects a human editor can verify: summary-like
compression, repeated openings, uniform cadence, heading overload and an opening
that dumps numbers before orienting the reader.
"""

from __future__ import annotations

import re
import statistics
import unicodedata
from collections import Counter
from typing import Any, Iterable


_WORD = re.compile(r"\b[\wÀ-ÿ'-]+\b")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_HEADING = re.compile(r"^#{1,6}\s+")
_LIST = re.compile(r"^[-*+]\s+")
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:%|°\s*C|º\s*C|cm|mm|h|horas?|dias?)?\b", re.I)
_RANGE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:%|°\s*C|º\s*C|cm|mm)?\s*(?:a|até|–|—|-)\s*"
    r"\d+(?:[.,]\d+)?\s*(?:%|°\s*C|º\s*C|cm|mm)?\b",
    re.I,
)
_META = re.compile(
    r"(?i)\b(?:neste|nesse)\s+(?:artigo|guia|texto|conteúdo)\b|"
    r"\bao longo (?:deste|desse)\s+(?:artigo|guia|texto|conteúdo)\b|"
    r"\ba seguir\b|\bvamos (?:ver|entender|explorar|analisar)\b"
)
_SUMMARY_OPENINGS = re.compile(
    r"(?i)^(?:durante\s+(?:o|a)|a\s+\w+\s+(?:é|são)|o\s+\w+\s+(?:é|são)|"
    r"manter\b|garantir\b|evitar\b|quando\b|para\b)"
)
_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
    "que",
    "um",
    "uma",
}


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.casefold())
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = re.sub(r"[^a-z0-9_]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _words(value: str) -> list[str]:
    return _WORD.findall(value)


def _coefficient_of_variation(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean <= 0:
        return 0.0
    return statistics.pstdev(values) / mean


def _opener(sentence: str) -> str:
    tokens = [_normalize(token) for token in _words(sentence)]
    meaningful = [token for token in tokens if token and token not in _STOPWORDS]
    selected = meaningful[:2] or tokens[:2]
    return " ".join(selected)


def _method_mentions(text: str, labels: Iterable[str]) -> list[str]:
    normalized_text = _normalize(text)
    found: list[str] = []
    for label in labels:
        normalized_label = _normalize(str(label))
        if normalized_label and normalized_label in normalized_text:
            found.append(str(label))
    return list(dict.fromkeys(found))


def analyze_editorial_prose(
    markdown: str,
    *,
    method_labels: Iterable[str] = (),
    opening_word_window: int = 340,
) -> dict[str, Any]:
    """Return explainable prose signals suitable for writer/editor gates."""

    chunks = [
        chunk.strip()
        for chunk in re.split(r"\n\s*\n", str(markdown or ""))
        if chunk.strip()
    ]
    headings: list[str] = []
    subheading_count = 0
    paragraphs: list[str] = []
    list_items: list[str] = []
    visible_sequence: list[tuple[str, str]] = []

    for chunk in chunks:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            continue
        heading_lines = [line for line in lines if _HEADING.match(line)]
        if heading_lines and len(heading_lines) == len(lines):
            for line in heading_lines:
                text = _HEADING.sub("", line).strip()
                headings.append(text)
                if re.match(r"^#{2,6}\s+", line):
                    subheading_count += 1
                visible_sequence.append(("heading", text))
            continue
        if all(_LIST.match(line) for line in lines):
            for line in lines:
                text = _LIST.sub("", line).strip()
                list_items.append(text)
                visible_sequence.append(("list", text))
            continue
        body_lines = [
            _LIST.sub("", line).strip()
            for line in lines
            if not _HEADING.match(line)
        ]
        if body_lines:
            paragraph = " ".join(body_lines)
            paragraphs.append(paragraph)
            visible_sequence.append(("paragraph", paragraph))

    body_text = " ".join(
        text for kind, text in visible_sequence if kind != "heading"
    )
    body_words = _words(body_text)
    sentences = [
        sentence.strip()
        for sentence in _SENTENCE_SPLIT.split(body_text)
        if sentence.strip()
    ]
    sentence_lengths = [len(_words(sentence)) for sentence in sentences]
    paragraph_lengths = [len(_words(paragraph)) for paragraph in paragraphs]
    paragraph_sentence_counts = [
        len([item for item in _SENTENCE_SPLIT.split(paragraph) if item.strip()])
        for paragraph in paragraphs
    ]

    opener_counts = Counter(
        opener for opener in (_opener(sentence) for sentence in sentences) if opener
    )
    dominant_opener, dominant_opener_count = (
        opener_counts.most_common(1)[0] if opener_counts else ("", 0)
    )
    dominant_opener_rate = (
        dominant_opener_count / len(sentences) if sentences else 0.0
    )
    shape_counts = Counter(paragraph_sentence_counts)
    dominant_paragraph_shape_rate = (
        max(shape_counts.values()) / len(paragraph_sentence_counts)
        if paragraph_sentence_counts
        else 0.0
    )

    paragraph_length_cv = _coefficient_of_variation(paragraph_lengths)
    sentence_length_cv = _coefficient_of_variation(sentence_lengths)
    median_paragraph_words = (
        float(statistics.median(paragraph_lengths)) if paragraph_lengths else 0.0
    )
    compressed_paragraph_rate = (
        sum(length < 45 for length in paragraph_lengths) / len(paragraph_lengths)
        if paragraph_lengths
        else 0.0
    )
    developed_paragraph_rate = (
        sum(55 <= length <= 170 for length in paragraph_lengths)
        / len(paragraph_lengths)
        if paragraph_lengths
        else 0.0
    )
    words_per_subheading = (
        len(body_words) / subheading_count
        if subheading_count
        else float(len(body_words))
    )

    opening_words = body_words[:opening_word_window]
    opening_text = " ".join(opening_words)
    opening_methods = _method_mentions(opening_text, method_labels)
    opening_numeric_count = len(_NUMBER.findall(opening_text))
    opening_range_count = len(_RANGE.findall(opening_text))
    first_paragraph = paragraphs[0] if paragraphs else ""

    uniform_paragraph_shape = (
        len(paragraph_lengths) >= 6
        and (
            paragraph_length_cv <= 0.20
            or dominant_paragraph_shape_rate >= 0.72
        )
    )
    uniform_sentence_cadence = (
        len(sentence_lengths) >= 14 and sentence_length_cv <= 0.27
    )
    repeated_sentence_openers = (
        len(sentences) >= 12
        and dominant_opener_count >= 4
        and dominant_opener_rate >= 0.24
    )
    heading_body_imbalance = subheading_count >= 6 and words_per_subheading < 135
    summary_like_compression = (
        len(paragraph_lengths) >= 6
        and median_paragraph_words < 50
        and compressed_paragraph_rate >= 0.65
    )
    premature_numeric_density = (
        opening_range_count >= 2 or opening_numeric_count >= 5
    )
    opening_template_like = bool(_SUMMARY_OPENINGS.search(first_paragraph.strip()))
    meta_narration_matches = list(
        dict.fromkeys(match.group(0).strip() for match in _META.finditer(markdown))
    )

    severe_mechanical_prose = sum(
        [
            uniform_paragraph_shape,
            uniform_sentence_cadence,
            repeated_sentence_openers,
            heading_body_imbalance,
            summary_like_compression,
        ]
    ) >= 2

    score = 1.0
    score -= 0.18 if uniform_paragraph_shape else 0.0
    score -= 0.14 if uniform_sentence_cadence else 0.0
    score -= 0.14 if repeated_sentence_openers else 0.0
    score -= 0.18 if heading_body_imbalance else 0.0
    score -= 0.20 if summary_like_compression else 0.0
    score -= 0.08 if premature_numeric_density else 0.0
    score -= 0.06 if opening_template_like else 0.0
    score -= min(0.18, 0.06 * len(meta_narration_matches))

    return {
        "body_word_count": len(body_words),
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "subheading_count": subheading_count,
        "median_paragraph_words": round(median_paragraph_words, 2),
        "paragraph_length_cv": round(paragraph_length_cv, 4),
        "sentence_length_cv": round(sentence_length_cv, 4),
        "dominant_paragraph_shape_rate": round(
            dominant_paragraph_shape_rate, 4
        ),
        "dominant_sentence_opener": dominant_opener,
        "dominant_sentence_opener_rate": round(dominant_opener_rate, 4),
        "compressed_paragraph_rate": round(compressed_paragraph_rate, 4),
        "developed_paragraph_rate": round(developed_paragraph_rate, 4),
        "words_per_subheading": round(words_per_subheading, 2),
        "opening_method_mentions": opening_methods,
        "opening_method_mention_count": len(opening_methods),
        "opening_numeric_count": opening_numeric_count,
        "opening_range_count": opening_range_count,
        "opening_template_like": opening_template_like,
        "premature_numeric_density": premature_numeric_density,
        "uniform_paragraph_shape": uniform_paragraph_shape,
        "uniform_sentence_cadence": uniform_sentence_cadence,
        "repeated_sentence_openers": repeated_sentence_openers,
        "heading_body_imbalance": heading_body_imbalance,
        "summary_like_compression": summary_like_compression,
        "severe_mechanical_prose": severe_mechanical_prose,
        "meta_narration_matches": meta_narration_matches,
        "observable_naturalness_score": round(max(0.0, min(1.0, score)), 4),
    }
