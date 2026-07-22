from __future__ import annotations

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.routes import (
    AGENT_ROLES,
    PROVIDER_STANDARD_TOKEN_RATES,
    _default_route_for_provider,
    router,
)
from app.core.config import settings
from app.db.models import Credential, ModelRoute
from app.db.session import get_db
from app.startup import _route_gaps_and_providers


ADMIN_TOKEN = "route-defaults-administrative-token"


class CredentialDb:
    def __init__(self, existing_roles: tuple[str, ...] = ()):
        self.existing_roles = existing_roles
        self.added: list[object] = []
        self.commits = 0

    async def scalar(self, _query):
        return None

    async def scalars(self, _query):
        return self.existing_roles

    def add(self, instance):
        self.added.append(instance)

    async def commit(self):
        self.commits += 1


def credential_client(db: CredentialDb) -> TestClient:
    application = FastAPI()
    application.include_router(router)

    async def database_dependency():
        yield db

    application.dependency_overrides[get_db] = database_dependency
    return TestClient(application)


@pytest.mark.parametrize(
    ("provider", "expected_model"),
    [
        ("anthropic", "claude-sonnet-5"),
        ("gemini", "gemini-3.5-flash"),
    ],
)
def test_non_openai_default_routes_are_fully_priced_and_preflight_safe(
    provider, expected_model
):
    routes = tuple(
        ModelRoute(**_default_route_for_provider(provider, role))
        for role in AGENT_ROLES
    )

    gaps, providers = _route_gaps_and_providers(settings, routes)

    assert gaps == []
    assert providers == {provider}
    for route in routes:
        assert route.primary_provider == provider
        assert route.primary_model == expected_model
        assert route.parameters["input_cost_per_million"] == (
            PROVIDER_STANDARD_TOKEN_RATES[provider]["input_cost_per_million"]
        )
        assert route.parameters["output_cost_per_million"] == (
            PROVIDER_STANDARD_TOKEN_RATES[provider]["output_cost_per_million"]
        )
        assert route.parameters["input_cost_per_million"] > 0
        assert route.parameters["output_cost_per_million"] > 0
        assert route.parameters["max_output_tokens"] > 0
        assert route.parameters["timeout_seconds"] > 0
    assert all("temperature" not in route.parameters for route in routes)


def test_openai_default_routes_are_fully_priced_and_preflight_safe():
    routes = tuple(
        ModelRoute(**_default_route_for_provider("openai", role))
        for role in AGENT_ROLES
    )

    gaps, providers = _route_gaps_and_providers(settings, routes)

    assert gaps == []
    assert providers == {"openai"}
    for route in routes:
        parameters = route.parameters
        assert parameters["input_cost_per_million"] > 0
        assert parameters["output_cost_per_million"] > 0
        assert parameters["max_output_tokens"] > 0
        assert (
            parameters["max_output_tokens"]
            * parameters["output_cost_per_million"]
            / 1_000_000
            <= settings.max_agent_cost_usd
        )


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_saving_non_openai_credential_creates_all_missing_priced_routes(
    provider, monkeypatch
):
    db = CredentialDb()
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(
        settings, "credential_master_key", Fernet.generate_key().decode()
    )

    with credential_client(db) as client:
        response = client.put(
            f"/api/v1/config/credentials/{provider}",
            json={"provider": provider, "value": f"{provider}-secret-value-123456"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert db.commits == 1
    assert len([item for item in db.added if isinstance(item, Credential)]) == 1
    routes = tuple(item for item in db.added if isinstance(item, ModelRoute))
    assert {route.agent_role for route in routes} == set(AGENT_ROLES)
    gaps, providers = _route_gaps_and_providers(settings, routes)
    assert gaps == []
    assert providers == {provider}


def test_saving_credential_only_backfills_missing_roles(monkeypatch):
    existing = ("planner", "writer")
    db = CredentialDb(existing)
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(
        settings, "credential_master_key", Fernet.generate_key().decode()
    )

    with credential_client(db) as client:
        response = client.put(
            "/api/v1/config/credentials/gemini",
            json={"provider": "gemini", "value": "gemini-secret-value-123456"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    routes = tuple(item for item in db.added if isinstance(item, ModelRoute))
    assert {route.agent_role for route in routes} == set(AGENT_ROLES) - set(existing)
    assert all(route.parameters["input_cost_per_million"] > 0 for route in routes)
    assert all(route.parameters["output_cost_per_million"] > 0 for route in routes)
