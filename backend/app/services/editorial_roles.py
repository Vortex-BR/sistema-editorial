from __future__ import annotations

from collections.abc import Iterable


V2_AGENT_ROLES = frozenset(
    {
        "planner",
        "researcher",
        "research_gatekeeper",
        "writer",
        "editor",
        "skill_curator",
    }
)

V3_ONLY_AGENT_ROLES = frozenset(
    {
        "development_editor",
        "fact_checker",
        "language_editor",
    }
)

V3_AGENT_ROLES = V2_AGENT_ROLES | V3_ONLY_AGENT_ROLES
ALL_AGENT_ROLES = V3_AGENT_ROLES


def normalize_pipeline_version(value: object) -> str:
    raw = getattr(value, "value", value)
    return "v3" if str(raw).strip().lower() == "v3" else "v2"


def roles_for_pipeline(value: object) -> tuple[str, ...]:
    roles: Iterable[str] = (
        V3_AGENT_ROLES if normalize_pipeline_version(value) == "v3" else V2_AGENT_ROLES
    )
    return tuple(sorted(roles))
