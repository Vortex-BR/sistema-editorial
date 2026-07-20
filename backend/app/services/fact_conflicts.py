from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UnresolvedFactConflict:
    group: str
    active_fact_ids: tuple[str, ...]


def unresolved_fact_conflicts(
    facts: Iterable[Any],
    *,
    project_id: object,
    pipeline_run_id: object,
    valid_fact_ids: Iterable[object],
) -> tuple[UnresolvedFactConflict, ...]:
    """Return deterministic conflicts proven by at least two active facts."""
    if project_id is None or pipeline_run_id is None:
        return ()
    expected_project_id = str(project_id)
    expected_run_id = str(pipeline_run_id)
    valid_ids = {str(fact_id) for fact_id in valid_fact_ids}
    facts_by_group: dict[str, set[str]] = defaultdict(set)

    for fact in facts:
        fact_id = _value(fact, "id")
        fact_id_text = str(fact_id) if fact_id is not None else ""
        if not fact_id_text or fact_id_text not in valid_ids:
            continue
        if str(_value(fact, "project_id")) != expected_project_id:
            continue
        if str(_value(fact, "pipeline_run_id")) != expected_run_id:
            continue
        if _value(fact, "superseded_by_id") is not None or bool(
            _value(fact, "superseded", False)
        ):
            continue
        group = str(_value(fact, "conflict_group") or "").strip()
        if group:
            facts_by_group[group].add(fact_id_text)

    return tuple(
        UnresolvedFactConflict(group, tuple(sorted(fact_ids)))
        for group, fact_ids in sorted(facts_by_group.items())
        if len(fact_ids) > 1
    )


def _value(fact: Any, key: str, default: Any = None) -> Any:
    if isinstance(fact, Mapping):
        return fact.get(key, default)
    return getattr(fact, key, default)
