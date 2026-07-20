"""Canonical research intent and deterministic query localization for V3.5.

The SEO keyword is not the factual research object.  This module keeps a
structured, auditable intent and produces one query per market/language without
making the provider guess what a Portuguese sentence means under ``hl=en``.

Localization is deterministic and intentionally conservative.  Known editorial
and technical phrases are translated; unknown entity names and product terms are
preserved.  When the lexicon cannot translate enough of a query, the localized
query is rebuilt from the canonical subject plus evidence-role qualifiers instead
of sending the original sentence unchanged to a foreign-language market.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from app.schemas.editorial_v3 import ContentKnowledgeContract, EditorialContentTypeV3, EvidenceRole
from app.services.search_policy import SearchMarket

_INTENT_VERSION = "research-intent.v1"
_LOCALIZATION_VERSION = "query-localization.v1"

_LOCALE_DEFAULTS: dict[str, tuple[str, str]] = {
    "pt": ("pt-BR", "br"),
    "en": ("en-US", "us"),
    "es": ("es-ES", "es"),
    "de": ("de-CH", "ch"),
}

_STOPWORDS: dict[str, set[str]] = {
    "pt": {
        "a", "ao", "aos", "as", "com", "como", "da", "das", "de", "do", "dos",
        "e", "em", "entre", "esse", "esta", "este", "isso", "na", "nas", "no",
        "nos", "o", "os", "ou", "para", "pela", "pelo", "por", "que", "se",
        "sem", "sobre", "um", "uma",
    },
    "en": {
        "a", "an", "and", "as", "at", "by", "for", "from", "how", "in", "of",
        "on", "or", "the", "to", "with", "without",
    },
    "es": {
        "a", "al", "como", "con", "de", "del", "el", "en", "entre", "la", "las",
        "los", "o", "para", "por", "que", "sin", "sobre", "un", "una", "y",
    },
    "de": {
        "als", "am", "an", "auf", "aus", "bei", "das", "der", "die", "ein", "eine",
        "für", "im", "in", "mit", "oder", "ohne", "und", "von", "wie", "zu",
    },
}

# Longest phrases are applied first.  This is deliberately focused on editorial
# research terminology and common technical constructions; unknown domain terms
# remain intact rather than being hallucinated.
_PHRASE_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "papel-toalha": "paper towel",
        "papel toalha": "paper towel",
        "recipiente fechado": "sealed container",
        "sementes de cannabis": "cannabis seeds",
        "semente de cannabis": "cannabis seed",
        "passo a passo": "step by step",
        "guia técnico": "technical guide",
        "fontes independentes": "independent sources",
        "sinais observáveis": "observable signs",
        "erros comuns": "common mistakes",
        "solução de problemas": "troubleshooting",
        "condições ambientais": "environmental conditions",
        "critérios de decisão": "decision criteria",
        "resultado final": "final outcome",
        "revisão sistemática": "systematic review",
        "evidência científica": "scientific evidence",
        "limites e exceções": "limitations and exceptions",
    },
    "es": {
        "papel-toalha": "papel de cocina",
        "papel toalha": "papel de cocina",
        "recipiente fechado": "recipiente cerrado",
        "sementes de cannabis": "semillas de cannabis",
        "semente de cannabis": "semilla de cannabis",
        "passo a passo": "paso a paso",
        "guia técnico": "guía técnica",
        "fontes independentes": "fuentes independientes",
        "sinais observáveis": "señales observables",
        "erros comuns": "errores comunes",
        "solução de problemas": "resolución de problemas",
        "condições ambientais": "condiciones ambientales",
        "critérios de decisão": "criterios de decisión",
        "resultado final": "resultado final",
        "revisão sistemática": "revisión sistemática",
        "evidência científica": "evidencia científica",
        "limites e exceções": "limitaciones y excepciones",
    },
    "de": {
        "papel-toalha": "Papiertuch",
        "papel toalha": "Papiertuch",
        "recipiente fechado": "geschlossener Behälter",
        "sementes de cannabis": "Cannabissamen",
        "semente de cannabis": "Cannabissamen",
        "passo a passo": "Schritt für Schritt",
        "guia técnico": "technischer Leitfaden",
        "fontes independentes": "unabhängige Quellen",
        "sinais observáveis": "beobachtbare Anzeichen",
        "erros comuns": "häufige Fehler",
        "solução de problemas": "Fehlerbehebung",
        "condições ambientais": "Umgebungsbedingungen",
        "critérios de decisão": "Entscheidungskriterien",
        "resultado final": "Endergebnis",
        "revisão sistemática": "systematische Übersicht",
        "evidência científica": "wissenschaftliche Evidenz",
        "limites e exceções": "Einschränkungen und Ausnahmen",
    },
}

_TOKEN_TRANSLATIONS: dict[str, dict[str, str]] = {
    "en": {
        "abordagem": "approach", "abordagens": "approaches", "ação": "action",
        "causa": "cause", "causas": "causes", "comparação": "comparison",
        "condição": "condition", "condições": "conditions", "correção": "correction",
        "correções": "corrections", "critério": "criterion", "critérios": "criteria",
        "cultivo": "cultivation", "definição": "definition", "desvantagens": "disadvantages",
        "detalhado": "detailed", "evidência": "evidence", "evidências": "evidence",
        "exceção": "exception", "exceções": "exceptions", "explicação": "explanation",
        "falha": "failure", "falhas": "failures", "funcionamento": "function",
        "germinação": "germination", "germinar": "germinate", "guia": "guide",
        "legislação": "legislation", "limitação": "limitation", "limitações": "limitations",
        "manual": "manual", "material": "material", "materiais": "materials",
        "mecanismo": "mechanism", "método": "method", "métodos": "methods",
        "observação": "observation", "observações": "observations", "ordem": "order",
        "preparação": "preparation", "procedimento": "procedure", "processo": "process",
        "progresso": "progress", "protocolo": "protocol", "regulação": "regulation",
        "revisão": "review", "risco": "risk", "riscos": "risks", "segurança": "safety",
        "semente": "seed", "sementes": "seeds", "sinal": "sign", "sinais": "signs",
        "técnica": "technique", "técnico": "technical", "temperatura": "temperature",
        "umidade": "moisture", "universidade": "university", "vantagens": "advantages",
        "ventilação": "ventilation",
    },
    "es": {
        "abordagem": "enfoque", "abordagens": "enfoques", "ação": "acción",
        "causa": "causa", "causas": "causas", "comparação": "comparación",
        "condição": "condición", "condições": "condiciones", "correção": "corrección",
        "correções": "correcciones", "critério": "criterio", "critérios": "criterios",
        "cultivo": "cultivo", "definição": "definición", "desvantagens": "desventajas",
        "detalhado": "detallado", "evidência": "evidencia", "evidências": "evidencias",
        "exceção": "excepción", "exceções": "excepciones", "explicação": "explicación",
        "falha": "fallo", "falhas": "fallos", "funcionamento": "funcionamiento",
        "germinação": "germinación", "germinar": "germinar", "guia": "guía",
        "legislação": "legislación", "limitação": "limitación", "limitações": "limitaciones",
        "manual": "manual", "material": "material", "materiais": "materiales",
        "mecanismo": "mecanismo", "método": "método", "métodos": "métodos",
        "observação": "observación", "observações": "observaciones", "ordem": "orden",
        "preparação": "preparación", "procedimento": "procedimiento", "processo": "proceso",
        "progresso": "progreso", "protocolo": "protocolo", "regulação": "regulación",
        "revisão": "revisión", "risco": "riesgo", "riscos": "riesgos", "segurança": "seguridad",
        "semente": "semilla", "sementes": "semillas", "sinal": "señal", "sinais": "señales",
        "técnica": "técnica", "técnico": "técnico", "temperatura": "temperatura",
        "umidade": "humedad", "universidade": "universidad", "vantagens": "ventajas",
        "ventilação": "ventilación",
    },
    "de": {
        "abordagem": "Ansatz", "abordagens": "Ansätze", "ação": "Handlung",
        "causa": "Ursache", "causas": "Ursachen", "comparação": "Vergleich",
        "condição": "Bedingung", "condições": "Bedingungen", "correção": "Korrektur",
        "correções": "Korrekturen", "critério": "Kriterium", "critérios": "Kriterien",
        "cultivo": "Anbau", "definição": "Definition", "desvantagens": "Nachteile",
        "detalhado": "detailliert", "evidência": "Evidenz", "evidências": "Evidenz",
        "exceção": "Ausnahme", "exceções": "Ausnahmen", "explicação": "Erklärung",
        "falha": "Fehler", "falhas": "Fehler", "funcionamento": "Funktionsweise",
        "germinação": "Keimung", "germinar": "keimen", "guia": "Leitfaden",
        "legislação": "Gesetzgebung", "limitação": "Einschränkung", "limitações": "Einschränkungen",
        "manual": "Handbuch", "material": "Material", "materiais": "Materialien",
        "mecanismo": "Mechanismus", "método": "Methode", "métodos": "Methoden",
        "observação": "Beobachtung", "observações": "Beobachtungen", "ordem": "Reihenfolge",
        "preparação": "Vorbereitung", "procedimento": "Verfahren", "processo": "Prozess",
        "progresso": "Fortschritt", "protocolo": "Protokoll", "regulação": "Regulierung",
        "revisão": "Übersicht", "risco": "Risiko", "riscos": "Risiken", "segurança": "Sicherheit",
        "semente": "Samen", "sementes": "Samen", "sinal": "Anzeichen", "sinais": "Anzeichen",
        "técnica": "Technik", "técnico": "technisch", "temperatura": "Temperatur",
        "umidade": "Feuchtigkeit", "universidade": "Universität", "vantagens": "Vorteile",
        "ventilação": "Belüftung",
    },
}

_ROLE_QUALIFIERS: dict[str, dict[EvidenceRole, str]] = {
    "pt": {
        EvidenceRole.definition: "definição terminologia fonte institucional",
        EvidenceRole.mechanism: "mecanismo evidência científica revisão",
        EvidenceRole.prerequisite: "pré-requisitos condições técnicas",
        EvidenceRole.material: "materiais preparação protocolo",
        EvidenceRole.environmental_condition: "condições ambientais limites evidência",
        EvidenceRole.action: "procedimento técnico passo a passo",
        EvidenceRole.sequence: "sequência protocolo técnico",
        EvidenceRole.decision_criterion: "critérios de decisão comparação",
        EvidenceRole.success_signal: "sinais observáveis de sucesso",
        EvidenceRole.failure_signal: "sinais de falha diagnóstico",
        EvidenceRole.common_error: "erros comuns causas",
        EvidenceRole.correction: "correções solução de problemas",
        EvidenceRole.risk: "riscos segurança evidência",
        EvidenceRole.exception: "exceções variações limitações",
        EvidenceRole.limitation: "limitações desvantagens",
        EvidenceRole.comparison: "comparação critérios vantagens limitações",
        EvidenceRole.transition: "transição critérios observáveis",
        EvidenceRole.final_outcome: "resultado final confirmação observável",
        EvidenceRole.external_reference: "protocolo técnico referência independente",
    },
    "en": {
        EvidenceRole.definition: "definition terminology institutional source",
        EvidenceRole.mechanism: "mechanism scientific evidence review",
        EvidenceRole.prerequisite: "prerequisites technical conditions",
        EvidenceRole.material: "materials preparation protocol",
        EvidenceRole.environmental_condition: "environmental conditions limits evidence",
        EvidenceRole.action: "technical procedure step by step",
        EvidenceRole.sequence: "sequence technical protocol",
        EvidenceRole.decision_criterion: "decision criteria comparison",
        EvidenceRole.success_signal: "observable success signs",
        EvidenceRole.failure_signal: "failure signs diagnosis",
        EvidenceRole.common_error: "common mistakes causes",
        EvidenceRole.correction: "corrections troubleshooting",
        EvidenceRole.risk: "risks safety evidence",
        EvidenceRole.exception: "exceptions variations limitations",
        EvidenceRole.limitation: "limitations disadvantages",
        EvidenceRole.comparison: "comparison criteria advantages limitations",
        EvidenceRole.transition: "transition observable criteria",
        EvidenceRole.final_outcome: "final outcome observable confirmation",
        EvidenceRole.external_reference: "independent technical protocol",
    },
    "es": {
        EvidenceRole.definition: "definición terminología fuente institucional",
        EvidenceRole.mechanism: "mecanismo evidencia científica revisión",
        EvidenceRole.prerequisite: "requisitos condiciones técnicas",
        EvidenceRole.material: "materiales preparación protocolo",
        EvidenceRole.environmental_condition: "condiciones ambientales límites evidencia",
        EvidenceRole.action: "procedimiento técnico paso a paso",
        EvidenceRole.sequence: "secuencia protocolo técnico",
        EvidenceRole.decision_criterion: "criterios de decisión comparación",
        EvidenceRole.success_signal: "señales observables de éxito",
        EvidenceRole.failure_signal: "señales de fallo diagnóstico",
        EvidenceRole.common_error: "errores comunes causas",
        EvidenceRole.correction: "correcciones resolución de problemas",
        EvidenceRole.risk: "riesgos seguridad evidencia",
        EvidenceRole.exception: "excepciones variaciones limitaciones",
        EvidenceRole.limitation: "limitaciones desventajas",
        EvidenceRole.comparison: "comparación criterios ventajas limitaciones",
        EvidenceRole.transition: "transición criterios observables",
        EvidenceRole.final_outcome: "resultado final confirmación observable",
        EvidenceRole.external_reference: "protocolo técnico independiente",
    },
    "de": {
        EvidenceRole.definition: "Definition Terminologie institutionelle Quelle",
        EvidenceRole.mechanism: "Mechanismus wissenschaftliche Evidenz Übersicht",
        EvidenceRole.prerequisite: "Voraussetzungen technische Bedingungen",
        EvidenceRole.material: "Materialien Vorbereitung Protokoll",
        EvidenceRole.environmental_condition: "Umgebungsbedingungen Grenzen Evidenz",
        EvidenceRole.action: "technisches Verfahren Schritt für Schritt",
        EvidenceRole.sequence: "Reihenfolge technisches Protokoll",
        EvidenceRole.decision_criterion: "Entscheidungskriterien Vergleich",
        EvidenceRole.success_signal: "beobachtbare Erfolgszeichen",
        EvidenceRole.failure_signal: "Fehlerzeichen Diagnose",
        EvidenceRole.common_error: "häufige Fehler Ursachen",
        EvidenceRole.correction: "Korrekturen Fehlerbehebung",
        EvidenceRole.risk: "Risiken Sicherheit Evidenz",
        EvidenceRole.exception: "Ausnahmen Varianten Einschränkungen",
        EvidenceRole.limitation: "Einschränkungen Nachteile",
        EvidenceRole.comparison: "Vergleich Kriterien Vorteile Einschränkungen",
        EvidenceRole.transition: "Übergang beobachtbare Kriterien",
        EvidenceRole.final_outcome: "Endergebnis beobachtbare Bestätigung",
        EvidenceRole.external_reference: "unabhängiges technisches Protokoll",
    },
}


def normalize_locale(value: str | None) -> str:
    raw = str(value or "pt-BR").strip().replace("_", "-")
    language = raw.split("-", 1)[0].casefold()
    return _LOCALE_DEFAULTS.get(language, (raw or "pt-BR", "br"))[0]


def locale_language(value: str | None) -> str:
    return normalize_locale(value).split("-", 1)[0].casefold()


def locale_country(value: str | None) -> str:
    normalized = normalize_locale(value)
    if "-" in normalized:
        return normalized.split("-", 1)[1].casefold()
    return _LOCALE_DEFAULTS.get(locale_language(normalized), (normalized, "br"))[1]


def _ascii_fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def meaningful_terms(value: str, *, language: str = "pt", limit: int = 16) -> tuple[str, ...]:
    stopwords = _STOPWORDS.get(language, set())
    terms: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[\wÀ-ÿ][\wÀ-ÿ-]{2,}", str(value or ""), re.UNICODE):
        key = _ascii_fold(token).casefold()
        if key in stopwords or key in seen or key.isdigit():
            continue
        seen.add(key)
        terms.append(token)
        if len(terms) >= limit:
            break
    return tuple(terms)


@dataclass(frozen=True)
class CanonicalResearchIntent:
    version: str
    canonical_subject: str
    project_locale: str
    project_language: str
    target_country: str
    jurisdiction: str | None
    content_type: str
    entity_terms: tuple[str, ...]
    method_labels: tuple[str, ...]

    def as_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entity_terms"] = list(self.entity_terms)
        payload["method_labels"] = list(self.method_labels)
        return payload

    @classmethod
    def from_contract(cls, contract: ContentKnowledgeContract) -> "CanonicalResearchIntent":
        payload = contract.metadata.get("research_intent")
        if isinstance(payload, dict):
            locale = normalize_locale(str(payload.get("project_locale") or "pt-BR"))
            subject = " ".join(
                str(payload.get("canonical_subject") or contract.metadata.get("search_subject") or contract.topic).split()
            )[:500]
            return cls(
                version=str(payload.get("version") or _INTENT_VERSION),
                canonical_subject=subject,
                project_locale=locale,
                project_language=locale_language(locale),
                target_country=str(payload.get("target_country") or locale_country(locale)),
                jurisdiction=str(payload.get("jurisdiction") or "").strip() or contract.jurisdiction,
                content_type=str(payload.get("content_type") or contract.content_type.value),
                entity_terms=tuple(str(item) for item in payload.get("entity_terms") or meaningful_terms(subject, language=locale_language(locale))),
                method_labels=tuple(str(item) for item in payload.get("method_labels") or contract.required_method_labels),
            )
        locale = normalize_locale(str(contract.metadata.get("project_locale") or "pt-BR"))
        subject = " ".join(
            str(contract.metadata.get("search_subject") or contract.topic).split()
        )[:500]
        return cls(
            version=_INTENT_VERSION,
            canonical_subject=subject,
            project_locale=locale,
            project_language=locale_language(locale),
            target_country=locale_country(locale),
            jurisdiction=contract.jurisdiction,
            content_type=contract.content_type.value,
            entity_terms=meaningful_terms(subject, language=locale_language(locale)),
            method_labels=tuple(contract.required_method_labels),
        )


def build_research_intent_payload(
    *,
    canonical_subject: str,
    project_locale: str,
    jurisdiction: str | None,
    content_type: EditorialContentTypeV3,
    method_labels: Iterable[str] = (),
) -> dict[str, Any]:
    locale = normalize_locale(project_locale)
    language = locale_language(locale)
    subject = " ".join(str(canonical_subject or "").split()).strip()[:500]
    return CanonicalResearchIntent(
        version=_INTENT_VERSION,
        canonical_subject=subject,
        project_locale=locale,
        project_language=language,
        target_country=locale_country(locale),
        jurisdiction=str(jurisdiction or "").strip() or None,
        content_type=content_type.value,
        entity_terms=meaningful_terms(subject, language=language),
        method_labels=tuple(" ".join(str(item).split())[:200] for item in method_labels if str(item).strip()),
    ).as_payload()


@dataclass(frozen=True)
class LocalizedQuery:
    original_query: str
    localized_query: str
    source_language: str
    target_language: str
    market: str
    strategy: str
    translation_confidence: float
    localization_version: str = _LOCALIZATION_VERSION

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)


class QueryLocalizationService:
    """Build bounded search queries aligned with each market language."""

    def localize(
        self,
        *,
        query: str,
        intent: CanonicalResearchIntent,
        market: SearchMarket,
        evidence_role: EvidenceRole,
        max_length: int = 360,
    ) -> LocalizedQuery:
        original = re.sub(r"\s+", " ", str(query or "")).strip()
        source_language = intent.project_language
        target_language = market.language_code.casefold()
        if not original:
            original = intent.canonical_subject
        if target_language == source_language:
            return LocalizedQuery(
                original_query=original,
                localized_query=original[:max_length].rstrip(),
                source_language=source_language,
                target_language=target_language,
                market=market.code,
                strategy="identity",
                translation_confidence=1.0,
            )

        translated_query, query_confidence = self._translate_text(
            original,
            source_language=source_language,
            target_language=target_language,
        )
        translated_subject, subject_confidence = self._translate_text(
            intent.canonical_subject,
            source_language=source_language,
            target_language=target_language,
        )
        qualifiers = _ROLE_QUALIFIERS.get(target_language, _ROLE_QUALIFIERS["en"])[
            evidence_role
        ]

        # A confident translation keeps the planned query.  Otherwise rebuild it
        # from the factual subject and role-specific qualifiers; this avoids
        # submitting a complete Portuguese sentence under an English/Spanish/German
        # language parameter while still preserving names that must not be translated.
        if query_confidence >= 0.45:
            candidate = f"{translated_query} {qualifiers}"
            strategy = "lexicon"
            confidence = query_confidence
        else:
            subject = translated_subject or " ".join(intent.entity_terms)
            candidate = f"{subject} {qualifiers}"
            strategy = "canonical_subject_with_localized_qualifiers"
            confidence = max(query_confidence, subject_confidence * 0.9)

        candidate = self._deduplicate_words(candidate)
        return LocalizedQuery(
            original_query=original,
            localized_query=candidate[:max_length].rstrip(),
            source_language=source_language,
            target_language=target_language,
            market=market.code,
            strategy=strategy,
            translation_confidence=round(max(0.0, min(1.0, confidence)), 3),
        )

    @staticmethod
    def _deduplicate_words(value: str) -> str:
        tokens = value.split()
        result: list[str] = []
        recent: set[str] = set()
        for token in tokens:
            key = _ascii_fold(token.strip('"\'(),.;:')).casefold()
            # Preserve repeated short connective words, but remove repeated
            # evidence qualifiers and query-padding terms.
            if len(key) >= 4 and key in recent:
                continue
            result.append(token)
            if len(key) >= 4:
                recent.add(key)
        return re.sub(r"\s+", " ", " ".join(result)).strip()

    def _translate_text(
        self,
        value: str,
        *,
        source_language: str,
        target_language: str,
    ) -> tuple[str, float]:
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or source_language == target_language:
            return text, 1.0 if text else 0.0
        # The current UI emits Portuguese, English and Spanish.  English/Spanish
        # inputs can still be searched globally as-is; role qualifiers provide the
        # target-language signal when no deterministic reverse lexicon exists.
        if source_language != "pt":
            meaningful = meaningful_terms(text, language=source_language, limit=30)
            return " ".join(meaningful) or text, 0.35

        phrase_map = _PHRASE_TRANSLATIONS.get(target_language, {})
        lowered = text.casefold()
        phrase_hits = 0
        for source, target in sorted(phrase_map.items(), key=lambda item: len(item[0]), reverse=True):
            pattern = re.compile(rf"(?<!\w){re.escape(source)}(?!\w)", re.IGNORECASE)
            lowered, count = pattern.subn(target, lowered)
            phrase_hits += count

        token_map = _TOKEN_TRANSLATIONS.get(target_language, {})
        source_stopwords = _STOPWORDS.get(source_language, set())
        target_stopwords = _STOPWORDS.get(target_language, set())
        output: list[str] = []
        meaningful_count = 0
        translated_count = phrase_hits
        for token in re.findall(r"[\wÀ-ÿ-]+|[^\w\s]", lowered, re.UNICODE):
            if not re.match(r"[\wÀ-ÿ-]+$", token, re.UNICODE):
                continue
            folded = _ascii_fold(token).casefold()
            if folded in source_stopwords:
                continue
            if len(folded) >= 3:
                meaningful_count += 1
            translated = token_map.get(token.casefold()) or token_map.get(folded)
            if translated:
                output.append(translated)
                translated_count += 1
            elif folded not in target_stopwords:
                # Preserve unknown entities, brands, acronyms and scientific names.
                output.append(token)
        result = re.sub(r"\s+", " ", " ".join(output)).strip()
        confidence = translated_count / max(1, meaningful_count + phrase_hits)
        return result, min(1.0, confidence)
