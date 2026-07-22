"""Conservative deterministic language checks for supported Editorial V3 locales."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_MARKERS = {
    "pt": {
        "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
        "e", "em", "entre", "esta", "este", "isso", "mais", "na", "nas", "não", "no",
        "nos", "o", "os", "ou", "para", "pela", "pelo", "por", "que", "se", "sem",
        "ser", "sua", "suas", "um", "uma",
    },
    "en": {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
        "is", "it", "more", "not", "of", "on", "or", "that", "the", "this", "to", "was",
        "what", "when", "which", "with", "you", "your",
    },
    "es": {
        "a", "al", "como", "con", "de", "del", "el", "en", "entre", "es", "esta", "este",
        "la", "las", "los", "más", "no", "o", "para", "pero", "por", "que", "se", "sin",
        "su", "sus", "un", "una", "y",
    },
}

_SUPPORTED = {"pt-BR": "pt", "en-US": "en", "es-ES": "es"}


def language_report(text: str, locale: str) -> dict[str, Any]:
    expected = _SUPPORTED.get(locale)
    if expected is None:
        return {
            "supported": False,
            "locale": locale,
            "expected_language": None,
            "detected_language": None,
            "scores": {},
            "blocked": True,
            "reason": "unsupported_locale",
        }
    words = re.findall(r"\b[^\W\d_]{1,30}\b", str(text or "").casefold(), re.UNICODE)
    counts = Counter(words)
    scores = {
        language: sum(counts[token] for token in markers)
        for language, markers in _MARKERS.items()
    }
    detected = max(scores, key=scores.get)
    expected_score = scores[expected]
    detected_score = scores[detected]
    # Avoid false positives on short headings or highly technical text. A mismatch
    # must have enough grammatical evidence and a clear advantage over the target.
    blocked = bool(
        len(words) >= 120
        and detected != expected
        and detected_score >= 12
        and detected_score >= max(1, expected_score) * 1.65
    )
    return {
        "supported": True,
        "locale": locale,
        "expected_language": expected,
        "detected_language": detected,
        "scores": scores,
        "word_count": len(words),
        "blocked": blocked,
        "reason": "language_mismatch" if blocked else "ok",
    }
