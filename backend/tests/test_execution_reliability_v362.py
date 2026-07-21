from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.routes as routes_module
from app.services.editorial_roles import (
    V2_AGENT_ROLES,
    V3_AGENT_ROLES,
    roles_for_pipeline,
)
from app.services.execution_manifest import ExecutionManifestUnavailable
from app.services.execution_preflight import ExecutionPreflightReport
from app.services.model_route_bootstrap import default_route_for_provider


@pytest.mark.parametrize("value", ["v2", "V2", None, SimpleNamespace(value="v2")])
def test_v2_requires_only_the_roles_used_by_the_v2_flow(value):
    roles = set(roles_for_pipeline(value))

    assert roles == V2_AGENT_ROLES
    assert "development_editor" not in roles
    assert "fact_checker" not in roles
    assert "language_editor" not in roles


@pytest.mark.parametrize("value", ["v3", "V3", SimpleNamespace(value="v3")])
def test_v3_requires_the_complete_editorial_intelligence_route_set(value):
    assert set(roles_for_pipeline(value)) == V3_AGENT_ROLES


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic"])
@pytest.mark.parametrize("role", sorted(V3_AGENT_ROLES))
def test_bootstrapped_routes_are_costed_and_bounded(provider, role):
    route = default_route_for_provider(provider, role)
    parameters = route["parameters"]

    assert route["agent_role"] == role
    assert route["primary_provider"] == provider
    assert route["primary_model"]
    assert parameters["max_output_tokens"] > 0
    assert parameters["timeout_seconds"] > 0
    assert parameters["max_retries"] >= 0
    assert parameters["input_cost_per_million"] >= 0
    assert parameters["output_cost_per_million"] >= 0


def test_manifest_errors_preserve_safe_dependency_diagnostics():
    error = ExecutionManifestUnavailable(
        "manifest unavailable",
        dependencies=("model_route:fact_checker", "credential:search:unverified"),
    )

    assert error.dependencies == (
        "model_route:fact_checker",
        "credential:search:unverified",
    )


@pytest.mark.asyncio
async def test_dependency_preflight_commits_safe_route_repairs(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())

    async def inspect(_db, _version, *, repair_missing_routes):
        assert _db is db
        assert repair_missing_routes is True
        return ExecutionPreflightReport(
            pipeline_version="v3",
            ready=True,
            gaps=(),
            repairs=("model_route:fact_checker",),
        )

    monkeypatch.setattr(routes_module, "inspect_execution_dependencies", inspect)

    result = await routes_module._require_execution_dependencies(db, "v3")

    db.commit.assert_awaited_once()
    assert result == {
        "pipeline_version": "v3",
        "status": "ready",
        "dependencies": [],
        "repairs": ["model_route:fact_checker"],
    }


@pytest.mark.asyncio
async def test_dependency_preflight_returns_exact_actionable_gaps(monkeypatch):
    db = SimpleNamespace(commit=AsyncMock())

    async def inspect(_db, _version, *, repair_missing_routes):
        return ExecutionPreflightReport(
            pipeline_version="v3",
            ready=False,
            gaps=("super_skill:language_editor", "credential:search:unverified"),
            repairs=(),
        )

    monkeypatch.setattr(routes_module, "inspect_execution_dependencies", inspect)

    with pytest.raises(HTTPException) as exc:
        await routes_module._require_execution_dependencies(db, "v3")

    assert exc.value.status_code == 409
    assert exc.value.detail["error_code"] == "EXECUTION_DEPENDENCIES_NOT_READY"
    assert exc.value.detail["dependencies"] == [
        "super_skill:language_editor",
        "credential:search:unverified",
    ]
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_uses_the_pipeline_being_started(monkeypatch):
    import app.services.readiness as readiness_module
    from app.core.config import Settings

    async def healthy_database(_db, _config):
        return {"postgresql": "ready", "migrations": "ready", "vector": "ready"}

    async def healthy_redis(_config):
        return {"redis": "ready", "worker": "ready", "beat": "ready"}

    async def healthy_broker(_config):
        return "ready"

    seen = []

    async def inspect(_db, pipeline_version, *, repair_missing_routes, config):
        seen.append(pipeline_version)
        return ExecutionPreflightReport(
            pipeline_version=str(pipeline_version),
            ready=True,
            gaps=(),
            repairs=(),
        )

    monkeypatch.setattr(readiness_module, "database_component_states", healthy_database)
    monkeypatch.setattr(readiness_module, "redis_component_states", healthy_redis)
    monkeypatch.setattr(readiness_module, "broker_component_state", healthy_broker)
    monkeypatch.setattr(readiness_module, "inspect_execution_dependencies", inspect)
    config = Settings(
        _env_file=None,
        app_env="test",
        editorial_pipeline_v3_enabled=True,
        editorial_pipeline_v3_execution_enabled=True,
    )

    report = await readiness_module.readiness_report(
        object(),
        preflight_complete=True,
        config=config,
        pipeline_version="v2",
    )

    assert report.ready is True
    assert seen == ["v2"]


@pytest.mark.asyncio
async def test_existing_manifest_resume_does_not_depend_on_current_route_inventory(
    monkeypatch,
):
    import app.services.readiness as readiness_module
    from app.core.config import Settings

    async def healthy_database(_db, _config):
        return {"postgresql": "ready", "migrations": "ready", "vector": "ready"}

    async def healthy_redis(_config):
        return {"redis": "ready", "worker": "ready", "beat": "ready"}

    async def healthy_broker(_config):
        return "ready"

    async def inspect_forbidden(*_args, **_kwargs):
        raise AssertionError(
            "existing fixed manifest rechecked mutable route inventory"
        )

    monkeypatch.setattr(readiness_module, "database_component_states", healthy_database)
    monkeypatch.setattr(readiness_module, "redis_component_states", healthy_redis)
    monkeypatch.setattr(readiness_module, "broker_component_state", healthy_broker)
    monkeypatch.setattr(
        readiness_module,
        "inspect_execution_dependencies",
        inspect_forbidden,
    )

    report = await readiness_module.readiness_report(
        object(),
        preflight_complete=True,
        config=Settings(_env_file=None, app_env="test"),
        pipeline_version="v3",
        require_execution_dependencies=False,
    )

    assert report.ready is True
    assert report.components["execution_dependencies"] == "ready"


def test_project_idempotency_payload_comparison_rejects_reused_key_drift():
    project = SimpleNamespace(
        name="Projeto A",
        topic="Tema A",
        editorial_pipeline_version=SimpleNamespace(value="v3"),
        publication_profile_id="profile-1",
        briefing={"goal": "original"},
    )

    assert routes_module._project_payload_matches(
        project,
        values={
            "name": "Projeto A",
            "topic": "Tema A",
            "editorial_pipeline_version": "v3",
        },
        publication_profile_id="profile-1",
        briefing={"goal": "original"},
    )
    assert not routes_module._project_payload_matches(
        project,
        values={
            "name": "Projeto B",
            "topic": "Tema A",
            "editorial_pipeline_version": "v3",
        },
        publication_profile_id="profile-1",
        briefing={"goal": "original"},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dispatch", "expected"),
    [
        (None, "pending"),
        (SimpleNamespace(status="failed"), "retry_scheduled"),
        (SimpleNamespace(status="sent"), "sent"),
    ],
)
async def test_dispatch_result_never_turns_committed_run_into_a_false_creation_error(
    monkeypatch,
    dispatch,
    expected,
):
    publish = AsyncMock(return_value=dispatch)
    monkeypatch.setattr(routes_module, "dispatch_one", publish)

    result = await routes_module._dispatch_pipeline_run(
        SimpleNamespace(), origin="test"
    )

    assert result == expected
    publish.assert_awaited_once()

@pytest.mark.asyncio
async def test_run_start_gate_uses_the_current_app_runtime_settings(monkeypatch):
    from app.core.config import Settings
    from app.services.readiness import COMPONENT_ORDER, ReadinessReport

    runtime_config = Settings(
        _env_file=None,
        app_env="production",
        superior_skills_mode="enforced",
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                runtime_settings=runtime_config,
                production_preflight_complete=True,
            )
        )
    )
    components = {name: "ready" for name in COMPONENT_ORDER}
    components["worker"] = "missing"

    async def report(
        _db,
        *,
        preflight_complete,
        config,
        pipeline_version,
        require_execution_dependencies,
    ):
        assert preflight_complete is True
        assert config is runtime_config
        assert pipeline_version == "v3"
        assert require_execution_dependencies is True
        return ReadinessReport(components)

    monkeypatch.setattr(routes_module, "readiness_report", report)

    with pytest.raises(HTTPException) as exc:
        await routes_module._require_run_start_readiness(request, object(), "v3")

    assert exc.value.status_code == 503
    assert exc.value.detail["error_code"] == "SYSTEM_NOT_READY"
    assert exc.value.detail["components"]["worker"]["status"] == "missing"
