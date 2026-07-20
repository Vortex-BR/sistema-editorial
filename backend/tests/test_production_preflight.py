import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.startup as startup_module
from app.api.routes import router
from app.core.config import Settings, settings
from app.db.models import (
    Credential,
    CredentialProvider,
    ModelRoute,
    SuperiorSkill,
    SuperiorSkillScope,
    SuperiorSkillVersion,
)
from app.db.session import get_db
from app.services.superior_skills import SuperiorSkillDefinition
from app.startup import (
    REQUIRED_EDITORIAL_ROLES,
    ProductionPreflightError,
    StartupInventory,
    initialize_startup,
    validate_production_environment,
    validate_production_inventory,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ROOT = Path(__file__).parents[2]
BUILD_INFO_PATH = ROOT / "backend/tests/fixtures/build-info.json"


def production_settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "admin_api_token": "admin-preflight-secret",
        "credential_master_key": Fernet.generate_key().decode(),
        "database_url": "postgresql+asyncpg://app:database-secret@db/app",
        "redis_url": "redis://default:redis-secret@redis/0",
        "superior_skills_mode": "enforced",
        "app_commit_sha": "0123456789abcdef0123456789abcdef01234567",
        "app_build_version": "release-2026.01",
        "app_source_digest": "89abcdef0123456789abcdef0123456789abcdef",
        "app_build_info_path": str(BUILD_INFO_PATH),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def model_routes() -> tuple[ModelRoute, ...]:
    return tuple(
        ModelRoute(
            agent_role=role,
            primary_provider="openai",
            primary_model="configured-model",
            parameters={
                "input_cost_per_million": 1.0,
                "output_cost_per_million": 4.0,
            },
        )
        for role in REQUIRED_EDITORIAL_ROLES
    )


def provider_credential(config: Settings) -> Credential:
    encrypted = Fernet(config.credential_master_key.encode()).encrypt(
        b"provider-api-secret"
    )
    return Credential(
        provider=CredentialProvider.openai,
        encrypted_value=encrypted,
        key_version=1,
        last_four="cret",
        active=True,
    )


def superior_definition(
    *, skill_id: str, scope: str, role: str | None
) -> SuperiorSkillDefinition:
    return SuperiorSkillDefinition(
        skill_id=skill_id,
        scope=scope,
        agent_role=role,
        version="1.0.0",
        title=f"Editorial core {role or 'global'}",
        mission="Keep editorial execution safe and consistent.",
        expertise=["Editorial policy"],
        responsibilities=["Apply the active policy"],
        boundaries=["Never disclose secrets"],
        decision_protocol=["Validate before acting"],
        memory_policy=["Use approved memory only"],
        handoff_policy=["Preserve evidence"],
        voice=["clear"],
    )


def superior_versions() -> tuple[tuple[SuperiorSkill, SuperiorSkillVersion], ...]:
    pairs: list[tuple[SuperiorSkill, SuperiorSkillVersion]] = []
    definitions = [
        superior_definition(
            skill_id="superior.global-core", scope="global_core", role=None
        ),
        *(
            superior_definition(
                skill_id=f"superior.{role.replace('_', '-')}",
                scope="agent",
                role=role,
            )
            for role in REQUIRED_EDITORIAL_ROLES
        ),
    ]
    for definition in definitions:
        scope = SuperiorSkillScope(definition.scope)
        skill = SuperiorSkill(
            skill_id=definition.skill_id,
            scope=scope,
            agent_role=definition.agent_role,
            enabled=True,
            current_version=definition.version,
        )
        version = SuperiorSkillVersion(
            version=definition.version,
            definition=definition.model_dump(mode="json"),
            checksum=definition.checksum(),
            status="active",
            reviewed_by_human=True,
            approved_at=NOW,
            created_by="test",
        )
        pairs.append((skill, version))
    return tuple(pairs)


def valid_inventory(config: Settings) -> StartupInventory:
    return StartupInventory(
        routes=model_routes(),
        credentials=(provider_credential(config),),
        superior_versions=superior_versions(),
    )


@pytest.mark.parametrize(
    ("overrides", "requirement"),
    [
        ({"admin_api_token": ""}, "ADMIN_API_TOKEN"),
        ({"credential_master_key": ""}, "CREDENTIAL_MASTER_KEY"),
        ({"superior_skills_mode": "shadow"}, "SUPERIOR_SKILLS_MODE"),
        ({"app_commit_sha": "unversioned"}, "APP_COMMIT_SHA"),
        ({"app_build_version": "development"}, "APP_BUILD_VERSION"),
        ({"app_source_digest": "unversioned"}, "APP_SOURCE_DIGEST"),
    ],
)
def test_invalid_production_environment_reports_only_requirement_names(
    overrides, requirement
):
    config = production_settings(**overrides)

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_environment(config)

    assert exc.value.requirements == (requirement,)
    assert requirement in str(exc.value)
    for value in overrides.values():
        if value:
            assert value not in str(exc.value)


@pytest.mark.parametrize(
    ("field_name", "requirement"),
    [("database_url", "DATABASE_URL"), ("redis_url", "REDIS_URL")],
)
def test_production_requires_explicit_database_and_redis_urls(field_name, requirement):
    config = production_settings()
    config.model_fields_set.remove(field_name)

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_environment(config)

    assert exc.value.requirements == (requirement,)


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_non_production_environments_remain_flexible(app_env):
    config = Settings(_env_file=None, app_env=app_env)
    empty = StartupInventory(routes=(), credentials=(), superior_versions=())

    validate_production_environment(config)
    validate_production_inventory(config, empty)


def test_active_model_route_requires_its_own_provider_credential():
    config = production_settings()
    inventory = valid_inventory(config)
    inventory = StartupInventory(
        routes=inventory.routes,
        credentials=(),
        superior_versions=inventory.superior_versions,
    )

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("PROVIDER_CREDENTIAL[openai]",)


def test_provider_requirements_are_derived_from_active_model_routes():
    config = production_settings()
    inventory = valid_inventory(config)
    researcher = next(
        route for route in inventory.routes if route.agent_role == "researcher"
    )
    researcher.primary_provider = "anthropic"

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("PROVIDER_CREDENTIAL[anthropic]",)


def test_primary_model_route_requires_nonzero_cost_rates():
    config = production_settings()
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.parameters = {
        "input_cost_per_million": 0.0,
        "output_cost_per_million": 0.0,
    }

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("MODEL_ROUTE_COST[writer:primary]",)



@pytest.mark.parametrize(
    "parameters",
    [
        {"input_cost_per_million": 1.0, "output_cost_per_million": 0.0},
        {"input_cost_per_million": 0.0, "output_cost_per_million": 4.0},
    ],
)
def test_primary_route_requires_both_nonzero_cost_rates(parameters):
    config = production_settings()
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.parameters = parameters

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("MODEL_ROUTE_COST[writer:primary]",)

def test_distinct_fallback_requires_its_own_nonzero_cost_rates():
    config = production_settings()
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.fallback_provider = "anthropic"
    writer.fallback_model = "configured-fallback-model"
    inventory = StartupInventory(
        routes=inventory.routes,
        credentials=(
            *inventory.credentials,
            Credential(
                provider=CredentialProvider.anthropic,
                encrypted_value=Fernet(config.credential_master_key.encode()).encrypt(
                    b"fallback-api-secret"
                ),
                key_version=1,
                last_four="cret",
                active=True,
            ),
        ),
        superior_versions=inventory.superior_versions,
    )

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("MODEL_ROUTE_COST[writer:fallback]",)


def test_same_model_fallback_may_reuse_primary_cost_rates():
    config = production_settings()
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.fallback_provider = writer.primary_provider
    writer.fallback_model = writer.primary_model

    validate_production_inventory(config, inventory)


def test_invalid_legacy_model_route_parameters_fail_production_preflight():
    config = production_settings()
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.parameters = {"headers": {"X-Unsafe": "value"}}

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("MODEL_ROUTE[writer]",)


def test_all_required_model_route_roles_must_exist():
    config = production_settings()
    inventory = valid_inventory(config)
    inventory = StartupInventory(
        routes=tuple(
            route for route in inventory.routes if route.agent_role != "writer"
        ),
        credentials=inventory.credentials,
        superior_versions=inventory.superior_versions,
    )

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("MODEL_ROUTE[writer]",)


def test_all_required_editorial_core_versions_must_be_usable():
    config = production_settings()
    inventory = valid_inventory(config)
    inventory = StartupInventory(
        routes=inventory.routes,
        credentials=inventory.credentials,
        superior_versions=tuple(
            pair
            for pair in inventory.superior_versions
            if pair[0].agent_role != "editor"
        ),
    )

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("SUPERIOR_SKILL[editor]",)


def test_valid_production_inventory_does_not_call_a_paid_provider(monkeypatch):
    config = production_settings()

    def external_call_forbidden(*_args, **_kwargs):
        raise AssertionError("startup attempted an external provider call")

    monkeypatch.setattr(httpx, "AsyncClient", external_call_forbidden)

    validate_production_environment(config)
    validate_production_inventory(config, valid_inventory(config))


@pytest.mark.asyncio
async def test_invalid_environment_fails_before_startup_database_work(monkeypatch):
    config = production_settings(admin_api_token="")
    calls: list[str] = []

    async def unexpected_sync(_db):
        calls.append("sync")

    monkeypatch.setattr(startup_module, "sync_default_skills", unexpected_sync)
    monkeypatch.setattr(startup_module, "sync_superior_skills", unexpected_sync)

    with pytest.raises(ProductionPreflightError):
        await initialize_startup(object(), config)

    assert calls == []


def test_preflight_error_and_logs_never_include_secret_values(caplog):
    config = production_settings(superior_skills_mode="shadow")
    secrets = (
        "admin-preflight-secret",
        config.credential_master_key,
        "database-secret",
        "redis-secret",
    )

    with caplog.at_level(logging.ERROR, logger="seo_pipeline"):
        with pytest.raises(ProductionPreflightError) as exc:
            validate_production_environment(config)

    combined = str(exc.value) + caplog.text
    assert "SUPERIOR_SKILLS_MODE" in combined
    for secret in secrets:
        assert secret not in combined


def test_production_liveness_is_independent_from_preflight(monkeypatch):
    database_requests = 0

    class HealthDb:
        async def execute(self, _query):
            nonlocal database_requests
            database_requests += 1

    async def database_dependency():
        yield HealthDb()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = database_dependency
    app.state.production_preflight_complete = False
    monkeypatch.setattr(settings, "app_env", "production")

    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "seo-research-ledger",
    }
    assert database_requests == 0


def test_easypanel_runs_preflight_before_starting_processes():
    script = (ROOT / "deploy/easypanel/entrypoint.sh").read_text(encoding="utf-8")

    assert script.index("python -m app.startup --settings-only") < script.index(
        "alembic upgrade head"
    )
    assert script.index("python -m app.startup\n") < script.index(
        "exec /usr/bin/supervisord"
    )


def test_route_that_cannot_fit_agent_budget_is_rejected_at_startup():
    config = production_settings(max_agent_cost_usd=0.01)
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.parameters = {
        "max_output_tokens": 8192,
        "input_cost_per_million": 3.0,
        "output_cost_per_million": 15.0,
    }

    with pytest.raises(ProductionPreflightError) as exc:
        validate_production_inventory(config, inventory)

    assert exc.value.requirements == ("MODEL_ROUTE_BUDGET[writer:primary]",)


def test_default_anthropic_writer_can_fit_the_default_agent_budget():
    from app.api.routes import _default_route_for_provider

    config = production_settings()
    route_data = _default_route_for_provider("anthropic", "writer")
    inventory = valid_inventory(config)
    writer = next(route for route in inventory.routes if route.agent_role == "writer")
    writer.primary_provider = route_data["primary_provider"]
    writer.primary_model = route_data["primary_model"]
    writer.parameters = route_data["parameters"]
    inventory = StartupInventory(
        routes=inventory.routes,
        credentials=(
            *inventory.credentials,
            Credential(
                provider=CredentialProvider.anthropic,
                encrypted_value=Fernet(config.credential_master_key.encode()).encrypt(
                    b"anthropic-secret"
                ),
                key_version=1,
                last_four="cret",
                active=True,
            ),
        ),
        superior_versions=inventory.superior_versions,
    )

    validate_production_inventory(config, inventory)
