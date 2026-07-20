"""Generation-time brief and node applicability for Editorial V3.5.1."""

from __future__ import annotations

from typing import Any

from app.schemas.editorial_hierarchy import NodeApplicability
from app.schemas.editorial_v3 import ContentKnowledgeContract
from app.services.editorial_v3.text_integrity import normalized_text, stable_slug


def _clean_list(value: object, *, limit: int = 50) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in value[:limit]:
        item = " ".join(str(raw or "").split()).strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def generation_brief(project: Any, manifest: dict | None, contract: ContentKnowledgeContract) -> dict[str, Any]:
    manifest_context = (manifest or {}).get("editorial_context") or {}
    brief = dict(manifest_context.get("content_brief") or getattr(project, "briefing", None) or {})
    profile = dict(manifest_context.get("publication_profile") or {})
    locale = str(getattr(project, "language", None) or contract.metadata.get("project_locale") or "pt-BR")
    return {
        "locale": locale,
        "topic": contract.topic,
        "content_type": contract.content_type.value,
        "content_objective": str(brief.get("content_objective") or "").strip(),
        "primary_keyword": str(brief.get("primary_keyword") or "").strip(),
        "secondary_keywords": _clean_list(brief.get("secondary_keywords"), limit=30),
        "segment": str(brief.get("segment") or profile.get("segment") or "").strip(),
        "reader": {
            "project_audience": str(getattr(project, "audience", None) or "").strip(),
            "context": str(brief.get("reader_context") or profile.get("audience_description") or "").strip(),
            "age_min": brief.get("reader_age_min"),
            "age_max": brief.get("reader_age_max"),
            "life_stage": str(brief.get("reader_life_stage") or "").strip(),
            "knowledge_level": str(brief.get("reader_knowledge_level") or "mixed"),
            "goal": str(brief.get("reader_goal") or "").strip(),
        },
        "commercial": {
            "objective": str(brief.get("commercial_objective") or profile.get("commercial_objective") or "").strip(),
            "offer": str(brief.get("offer") or "").strip(),
            "desired_action": str(brief.get("desired_action") or profile.get("preferred_cta") or "").strip(),
        },
        "brand": {
            "name": str(profile.get("brand_name") or "").strip(),
            "description": str(profile.get("brand_description") or "").strip(),
            "mission": str(profile.get("mission") or "").strip(),
            "value_proposition": str(profile.get("value_proposition") or "").strip(),
            "tone_of_voice": str(brief.get("voice_override") or profile.get("tone_of_voice") or "").strip(),
            "approved_style_examples": _clean_list(brief.get("approved_style_examples"), limit=10),
        },
        "structure": {
            "minimum_words": brief.get("minimum_words"),
            "maximum_words": brief.get("maximum_words"),
            "minimum_h2": brief.get("minimum_h2"),
            "minimum_h3": brief.get("minimum_h3"),
            "required_sections": _clean_list(brief.get("required_sections"), limit=20),
        },
        "evidence_policy": {
            "preferred_sources": _clean_list(brief.get("preferred_sources"), limit=30),
            "prohibited_sources": _clean_list(brief.get("prohibited_sources"), limit=30),
            "maximum_source_age_days": brief.get("maximum_source_age_days"),
            "claims_to_avoid": _clean_list(brief.get("claims_to_avoid"), limit=30),
        },
        "internal_link": str(brief.get("related_page_url") or "").strip(),
        "additional_context": str(brief.get("additional_context") or contract.metadata.get("additional_context") or "").strip(),
        "article_promise": contract.article_promise,
        "scope_limit": contract.scope_limit,
        "reader_start_state": contract.reader_start_state,
        "reader_final_state": contract.reader_final_state,
    }


def resolve_node_applicability(contract: ContentKnowledgeContract, generation: dict[str, Any]) -> dict[str, dict[str, str | bool]]:
    required_sections = generation.get("structure", {}).get("required_sections") or []
    explicit = {stable_slug(item, separator="_") for item in required_sections}
    commercial = generation.get("commercial") or {}
    haystack = normalized_text(
        " ".join(
            [
                str(generation.get("content_objective") or ""),
                str(generation.get("additional_context") or ""),
                str((generation.get("reader") or {}).get("context") or ""),
                str((generation.get("reader") or {}).get("goal") or ""),
                str(commercial.get("objective") or ""),
                str(commercial.get("offer") or ""),
                str(commercial.get("desired_action") or ""),
                *required_sections,
            ]
        )
    )
    resolution: dict[str, dict[str, str | bool]] = {}
    for node in contract.nodes:
        aliases = {
            node.node_id,
            stable_slug(node.title_function, separator="_"),
            stable_slug(node.universal_role.value, separator="_"),
        }
        explicit_match = bool(aliases & explicit) or any(
            normalized_text(alias.replace("_", " ")) in haystack for alias in aliases
        )
        if node.applicability == NodeApplicability.required:
            included, reason = True, "required_by_contract"
        elif node.node_id == "closing":
            # Every article needs a conclusion. Commercial content is only used
            # when the brief explicitly contains an offer or desired action.
            included, reason = True, "closing_required_for_complete_article"
        elif node.applicability == NodeApplicability.conditional:
            included = explicit_match
            reason = "brief_signals_applicability" if included else "condition_not_established"
        else:
            included = explicit_match
            reason = "explicitly_requested" if included else "optional_not_requested"
        resolution[node.node_id] = {
            "included": included,
            "reason": reason,
            "applicability": node.applicability.value,
            "research_required": bool(node.research_required and included),
        }
    return resolution


def active_node_ids(contract: ContentKnowledgeContract) -> list[str]:
    resolution = (getattr(contract, "metadata", None) or {}).get("node_resolution") or {}
    if not resolution:
        return [node.node_id for node in contract.nodes]
    return [
        node.node_id
        for node in contract.nodes
        if bool((resolution.get(node.node_id) or {}).get("included", True))
    ]
