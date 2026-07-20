"""Deterministic matching between briefing-required methods and synthesized dossiers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

_GENERIC_TOKENS = {
    "a",
    "as",
    "com",
    "da",
    "das",
    "de",
    "direto",
    "do",
    "dos",
    "em",
    "metodo",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
}


def normalize_method_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value))
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", normalized.casefold()))


def _core_tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_method_label(value).split()
        if token not in _GENERIC_TOKENS and len(token) > 1
    }


def method_label_matches(required: str, candidate: str) -> bool:
    left = normalize_method_label(required)
    right = normalize_method_label(candidate)
    if not left or not right:
        return False
    if left == right or left in right or right in left:
        return True
    left_tokens = _core_tokens(left)
    right_tokens = _core_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    intersection = left_tokens & right_tokens
    return bool(intersection) and (
        intersection == left_tokens
        or intersection == right_tokens
        or len(intersection) / len(left_tokens | right_tokens) >= 0.75
    )


def method_labels(item: Any) -> list[str]:
    labels = [str(getattr(item, "name", "") or "")]
    labels.extend(str(value) for value in getattr(item, "aliases", []) or [])
    labels.extend(
        str(value) for value in getattr(item, "equivalent_variations", []) or []
    )
    return [value for value in labels if value.strip()]


def required_method_matches(
    required_labels: Iterable[str],
    methods: Iterable[Any],
) -> tuple[dict[str, str], list[str]]:
    method_list = list(methods)
    matches: dict[str, str] = {}
    missing: list[str] = []
    for required in required_labels:
        matched = next(
            (
                str(getattr(method, "method_id"))
                for method in method_list
                if any(
                    method_label_matches(required, candidate)
                    for candidate in method_labels(method)
                )
            ),
            None,
        )
        if matched is None:
            missing.append(required)
        else:
            matches[required] = matched
    return matches, missing
