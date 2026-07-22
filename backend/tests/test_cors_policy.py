import httpx
import pytest

from app.core.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_cors_allows_only_the_headers_used_by_the_frontend():
    origin = "https://seo.example"
    application = create_app(
        Settings(_env_file=None, app_env="test", frontend_origin=origin)
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.options(
            "/api/v1/projects",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "content-type,x-admin-token,idempotency-key"
                ),
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    allowed_headers = response.headers["access-control-allow-headers"].lower()
    assert "content-type" in allowed_headers
    assert "x-admin-token" in allowed_headers
    assert "idempotency-key" in allowed_headers
    allowed_methods = response.headers["access-control-allow-methods"]
    for method in ("GET", "POST", "PUT", "DELETE"):
        assert method in allowed_methods
    assert "PATCH" not in allowed_methods


@pytest.mark.asyncio
async def test_cors_rejects_unlisted_request_headers():
    origin = "https://seo.example"
    application = create_app(
        Settings(_env_file=None, app_env="test", frontend_origin=origin)
    )
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.options(
            "/api/v1/projects",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-untrusted-header",
            },
        )

    assert response.status_code == 400
    assert response.headers["access-control-allow-origin"] == origin
