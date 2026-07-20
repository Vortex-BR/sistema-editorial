import copy
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.execution_manifest as manifest_module
from app.services.agent_context import AgentContextComposer
from app.services.execution_manifest import (
    ExecutionManifestContainsSecret,
    ExecutionManifestDrift,
    ExecutionManifestService,
    ExecutionManifestUnavailable,
    pinned_default_definitions,
    prompt_contract_manifest,
)
from app.services.skill_registry import SkillRegistry
from app.services.quality_evaluator import quality_rubric_manifest


NOW = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)


def test_research_contract_versions_are_pinned_for_new_runs():
    contracts = prompt_contract_manifest()

    assert contracts["planner"]["prompt_version"] == "planner.prompt.v5"
    assert contracts["researcher"]["prompt_version"] == "researcher.prompt.v7"
    assert contracts["writer"]["prompt_version"] == "writer.prompt.v12"
    assert contracts["editor"]["prompt_version"] == "editor.prompt.v6"
    assert contracts["finalizer"]["prompt_version"] == "deterministic-finalizer.v3"
    assert (
        contracts["research_gatekeeper"]["prompt_version"]
        == "research-gatekeeper.deterministic.v2"
    )


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


def default_skill(version: str, rule: str) -> dict:
    definition = {
        "skill_id": "default.reproducible",
        "version": version,
        "applies_to_agent": ["writer"],
        "description": "Pinned editorial process",
        "rules": [rule],
        "examples_good": [],
        "examples_bad": [],
        "llm_hint_template": "Apply the pinned rule.",
    }
    return {
        "definition": definition,
        "checksum": manifest_module._checksum(definition),
    }


def superior_definition(skill_id: str, scope: str, role: str | None) -> dict:
    return {
        "skill_id": skill_id,
        "scope": scope,
        "agent_role": role,
        "version": "1.0.0",
        "title": skill_id,
        "mission": "Preserve reproducibility",
        "expertise": ["audit"],
        "responsibilities": ["use fixed dependencies"],
        "boundaries": ["do not invent evidence"],
        "decision_protocol": ["check the manifest"],
        "memory_policy": ["use approved snapshots"],
        "handoff_policy": ["use run-scoped handoffs"],
        "voice": ["clear"],
    }


def manifest_data(*, skill_version: str = "1.0.0", rule: str = "old rule") -> dict:
    global_definition = superior_definition("superior.global-core", "global_core", None)
    writer_definition = superior_definition("superior.writer", "agent", "writer")
    return {
        "format_version": 1,
        "pipeline_run_id": "run-placeholder",
        "project_id": "project-placeholder",
        "fixed_at": NOW.isoformat(),
        "build": {
            "commit_sha": "unversioned",
            "build_version": "test",
            "source_digest": "unversioned",
        },
        "super_skills": {
            "writer": [
                {
                    "skill_id": global_definition["skill_id"],
                    "version": "1.0.0",
                    "checksum": manifest_module._checksum(global_definition),
                    "definition": global_definition,
                },
                {
                    "skill_id": writer_definition["skill_id"],
                    "version": "1.0.0",
                    "checksum": manifest_module._checksum(writer_definition),
                    "definition": writer_definition,
                },
            ]
        },
        "default_skills": [default_skill(skill_version, rule)],
        "learned_skills": {
            "writer": {
                "skills": [
                    {
                        "skill_id": "learned.editorial",
                        "version": "2.0.0",
                        "checksum": "a" * 64,
                        "rule_count": 1,
                        "characters": 30,
                    }
                ],
                "fragment": "<approved_learned_skills>OLD LEARNED RULE</approved_learned_skills>",
                "characters": 75,
                "truncated": False,
            }
        },
        "model_routes": {
            "writer": {
                "id": str(uuid.uuid4()),
                "primary_provider": "openai",
                "primary_model": "fixed-model",
                "fallback_provider": None,
                "fallback_model": None,
                "parameters": {"temperature": 0},
                "checksum": "b" * 64,
            }
        },
        "prompt_contracts": prompt_contract_manifest(),
        "quality_evaluator": quality_rubric_manifest(),
        "memory_snapshots": {"writer": []},
        "style_pattern_snapshots": {"writer": []},
        "embedding_route": None,
        "search_route": {"provider": "tavily"},
        "editorial_context": {
            "publication_profile": {
                "id": str(uuid.uuid4()),
                "version": 1,
                "brand_name": "Marca fixa",
                "tone_of_voice": "clara e próxima",
            },
            "content_brief": {
                "primary_keyword": "tema principal",
                "content_objective": "ensinar com profundidade",
                "reader_context": "leitor no início da pesquisa",
                "reader_goal": "tomar uma decisão informada",
            },
        },
        "feature_flags": {
            "superior_skills_mode": "enforced",
            "max_research_cycles": 3,
            "max_editor_cycles": 3,
        },
        "artifact_scope": {
            "handoffs": "append_only_run_scoped",
            "source_snapshots": "append_only_run_scoped",
        },
        "missing_dependencies": [],
    }


def manifest_row(data: dict | None = None):
    data = copy.deepcopy(data or manifest_data())
    run_id = uuid.uuid4()
    data["pipeline_run_id"] = str(run_id)
    return SimpleNamespace(
        id=uuid.uuid4(),
        pipeline_run_id=run_id,
        format_version=1,
        manifest_json=data,
        checksum=manifest_module._checksum(data),
        created_at=NOW,
    )


class ManifestDb:
    def __init__(self, row, *, handoffs=None, snapshots=None):
        self.row = row
        self.handoffs = handoffs or []
        self.snapshots = snapshots or []

    async def scalar(self, _statement):
        return self.row

    async def scalars(self, statement):
        sql = str(statement)
        if "FROM agent_handoffs" in sql:
            return Rows(self.handoffs)
        if "FROM source_snapshots" in sql:
            return Rows(self.snapshots)
        raise AssertionError(f"Unexpected query: {sql}")


@pytest.mark.asyncio
async def test_resume_reuses_the_exact_same_manifest():
    row = manifest_row()
    service = ExecutionManifestService(ManifestDb(row))

    first = await service.required(row.pipeline_run_id)
    resumed = await service.required(row.pipeline_run_id)

    assert resumed.row is first.row
    assert resumed.checksum == first.checksum
    assert resumed.data == first.data


def test_default_skill_changes_only_affect_a_new_manifest():
    old = manifest_data(skill_version="1.0.0", rule="keep old behavior")
    new = manifest_data(skill_version="2.0.0", rule="use new behavior")

    old_registry = SkillRegistry(definitions=pinned_default_definitions(old))
    new_registry = SkillRegistry(definitions=pinned_default_definitions(new))

    assert "keep old behavior" in old_registry.prompt_fragment("writer")
    assert "use new behavior" not in old_registry.prompt_fragment("writer")
    assert "use new behavior" in new_registry.prompt_fragment("writer")


@pytest.mark.asyncio
async def test_manifest_checksum_detects_drift():
    row = manifest_row()
    row.manifest_json["model_routes"]["writer"]["primary_model"] = "changed"

    with pytest.raises(ExecutionManifestDrift, match="checksum"):
        await ExecutionManifestService(ManifestDb(row)).required(row.pipeline_run_id)


@pytest.mark.asyncio
async def test_missing_fixed_dependency_fails_explicitly():
    data = manifest_data()
    data["missing_dependencies"] = ["model_route:writer"]
    row = manifest_row(data)

    with pytest.raises(ExecutionManifestUnavailable, match="model_route:writer"):
        await ExecutionManifestService(ManifestDb(row)).required(row.pipeline_run_id)


@pytest.mark.asyncio
async def test_manifest_rejects_secret_fields_even_with_a_valid_checksum():
    data = manifest_data()
    data["model_routes"]["writer"]["parameters"]["api_key"] = (
        "sk-secret-value-123456789"
    )
    row = manifest_row(data)

    with pytest.raises(ExecutionManifestContainsSecret):
        await ExecutionManifestService(ManifestDb(row)).required(row.pipeline_run_id)


@pytest.mark.asyncio
async def test_manifest_rejects_unknown_model_parameters_with_a_valid_checksum():
    data = manifest_data()
    data["model_routes"]["writer"]["parameters"]["callback"] = (
        "https://attacker.example/callback"
    )
    row = manifest_row(data)

    with pytest.raises(ExecutionManifestUnavailable):
        await ExecutionManifestService(ManifestDb(row)).required(row.pipeline_run_id)


@pytest.mark.asyncio
async def test_safe_summary_includes_append_only_run_artifact_ids():
    row = manifest_row()
    handoff_id = uuid.uuid4()
    snapshot_id = uuid.uuid4()

    summary = await ExecutionManifestService(
        ManifestDb(row, handoffs=[handoff_id], snapshots=[snapshot_id])
    ).safe_summary(row.pipeline_run_id)

    assert summary["checksum"] == row.checksum
    assert summary["handoff_ids"] == [str(handoff_id)]
    assert summary["source_snapshot_ids"] == [str(snapshot_id)]
    assert summary["editorial_context"] == row.manifest_json["editorial_context"]


@pytest.mark.asyncio
async def test_pinned_context_does_not_query_mutable_learned_skills():
    data = manifest_data()

    class ContextDb:
        async def scalar(self, _statement):
            return None

        async def flush(self):
            return None

    composer = AgentContextComposer(ContextDb())
    composer._cache_get = AsyncMock(return_value=None)
    composer._cache_set = AsyncMock(return_value=None)
    composer.learned_skills.resolve = AsyncMock(
        side_effect=AssertionError("mutable learned skills must not be queried")
    )

    result = await composer.compose(
        "writer",
        uuid.uuid4(),
        "CURRENT TASK",
        pipeline_run_id=uuid.uuid4(),
        execution_manifest=data,
    )

    assert "OLD LEARNED RULE" in result.prompt
    assert result.metadata["learned_skills"][0]["version"] == "2.0.0"
    composer.learned_skills.resolve.assert_not_awaited()
