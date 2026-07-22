import httpx
import pytest
from fastapi.routing import APIWebSocketRoute

from app.api.routes import project_events
from app.core.config import Settings, settings
from app.main import create_app


DOCUMENTATION_PATHS = ("/docs", "/redoc", "/openapi.json")
ADMIN_TOKEN = "documentation-test-administrative-token"


def registered_routes(application):
    """Return concrete routes across FastAPI's legacy and included-router layouts."""

    concrete = []
    pending = list(application.routes)
    while pending:
        route = pending.pop(0)
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            pending[0:0] = list(original_router.routes)
            continue
        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            pending[0:0] = list(nested_routes)
            continue
        concrete.append(route)
    return concrete


async def get(application, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        return await client.get(path, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "app_env", ["production", " PRODUCTION ", "staging", ""]
)
@pytest.mark.parametrize("path", DOCUMENTATION_PATHS)
async def test_non_local_environments_do_not_register_documentation_routes(
    app_env, path
):
    application = create_app(Settings(_env_file=None, app_env=app_env))

    response = await get(
        application,
        path,
        headers={"Origin": "https://untrusted.example"},
    )

    assert response.status_code == 404
    assert application.docs_url is None
    assert application.redoc_url is None
    assert application.openapi_url is None
    assert path not in {route.path for route in registered_routes(application)}


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", ["development", " DEVELOPMENT ", "test"])
@pytest.mark.parametrize("path", DOCUMENTATION_PATHS)
async def test_non_production_keeps_documentation_available(app_env, path):
    application = create_app(Settings(_env_file=None, app_env=app_env))

    response = await get(application, path)

    assert response.status_code == 200
    assert application.docs_url == "/docs"
    assert application.redoc_url == "/redoc"
    assert application.openapi_url == "/openapi.json"


@pytest.mark.asyncio
async def test_production_health_remains_public_and_business_routes_stay_protected(
    monkeypatch,
):
    config = Settings(
        _env_file=None,
        app_env="production",
        admin_api_token=ADMIN_TOKEN,
    )
    application = create_app(config)
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    health_response = await get(application, "/api/v1/health")
    protected_response = await get(application, "/api/v1/projects")

    assert health_response.status_code == 200
    assert health_response.json() == {
        "status": "healthy",
        "service": "seo-research-ledger",
    }
    assert protected_response.status_code == 401
    assert ADMIN_TOKEN not in protected_response.text


def test_production_documentation_policy_does_not_change_websocket_routes():
    application = create_app(Settings(_env_file=None, app_env="production"))
    websocket_routes = [
        route
        for route in registered_routes(application)
        if isinstance(route, APIWebSocketRoute)
    ]

    assert len(websocket_routes) == 1
    assert websocket_routes[0].path == "/api/v1/projects/{project_id}/events"
    assert websocket_routes[0].endpoint is project_events
