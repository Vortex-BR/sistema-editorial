import logging
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.routes as routes_module
from app.api.routes import require_admin, router
from app.core.config import settings
from app.db.models import (
    Credential,
    ModelRoute,
    PipelineEvent,
    PipelineRun,
    PipelineRunStatus,
    Project,
    PublicationProfile,
)
from app.db.session import get_db
from app.schemas.api import ProjectCreate
from app.services.pipeline_control import InvalidRunTransition


ADMIN_TOKEN = "test-administrative-token"
PROJECT_ID = uuid.uuid4()
PIPELINE_RUN_ID = uuid.uuid4()
BUSINESS_READ_PATHS = (
    "/api/v1/projects",
    f"/api/v1/pipeline-runs/{PIPELINE_RUN_ID}",
    f"/api/v1/projects/{PROJECT_ID}",
    f"/api/v1/projects/{PROJECT_ID}/error-logs?pipeline_run_id={PIPELINE_RUN_ID}",
    f"/api/v1/projects/{PROJECT_ID}/facts?pipeline_run_id={PIPELINE_RUN_ID}",
    "/api/v1/dashboard",
    "/api/v1/config/credentials",
    "/api/v1/config",
    "/api/v1/publication-profiles",
)
SENSITIVE_REQUESTS = (
    *(("GET", path, None) for path in BUSINESS_READ_PATHS),
    ("GET", f"/api/v1/projects/{uuid.uuid4()}/export", None),
    ("GET", "/api/v1/admin/memories", None),
    ("GET", "/api/v1/admin/style-patterns", None),
    ("GET", "/api/v1/admin/style-sources", None),
    ("GET", "/api/v1/admin/superior-skills", None),
    (
        "GET",
        "/api/v1/admin/learned-skills/learned.testing.rule/lifecycle",
        None,
    ),
    (
        "POST",
        "/api/v1/admin/learned-skills/learned.testing.rule/lifecycle",
        {"action": "rollback", "reason": "Canary rollback"},
    ),
    (
        "GET",
        "/api/v1/admin/superior-skills/researcher.core/versions",
        None,
    ),
    (
        "POST",
        "/api/v1/admin/agent-context/preview",
        {
            "agent_role": "researcher",
            "project_id": str(uuid.uuid4()),
            "pipeline_run_id": None,
            "task": "preview",
        },
    ),
    (
        "POST",
        "/api/v1/projects",
        {
            "name": "Protected project",
            "topic": "Authorization boundaries",
            "audience": "internal operators",
        },
    ),
    (
        "POST",
        f"/api/v1/projects/{PROJECT_ID}/events/ticket",
        {"pipeline_run_id": str(PIPELINE_RUN_ID)},
    ),
    ("POST", f"/api/v1/projects/{uuid.uuid4()}/run", None),
    ("POST", f"/api/v1/pipeline-runs/{uuid.uuid4()}/resume", None),
    ("POST", f"/api/v1/pipeline-runs/{uuid.uuid4()}/cancel", None),
    (
        "POST",
        f"/api/v1/pipeline-runs/{uuid.uuid4()}/human-review",
        {
            "decision": "approve",
            "reviewer": "Editor humano",
            "observation": "Revisão concluída",
        },
    ),
    (
        "PUT",
        "/api/v1/config/credentials/openai",
        {"provider": "openai", "value": "provider-test-secret"},
    ),
    (
        "PUT",
        "/api/v1/config/routes/researcher",
        {
            "agent_role": "researcher",
            "primary_provider": "openai",
            "primary_model": "test-model",
            "parameters": {},
        },
    ),
)


def _client(db_dependency) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = db_dependency
    return TestClient(app)


class _Rows:
    def __init__(self, items=()):
        self.items = list(items)

    def all(self):
        return self.items


class _BusinessReadDb:
    def __init__(self):
        self.requests = 0
        self.project = SimpleNamespace(
            id=PROJECT_ID,
            name="Authorized project",
            topic="Protected reads",
            search_intent="informational",
            audience="internal operators",
            language="pt-BR",
            niche=None,
            content_type="article",
            status="draft",
            current_stage="planner",
            created_at=datetime.now(timezone.utc),
        )
        self.run = SimpleNamespace(
            id=PIPELINE_RUN_ID,
            project_id=PROJECT_ID,
            status="queued",
            current_stage="planner",
            attempt=1,
            retryable=False,
            next_retry_at=None,
            cancellation_requested_at=None,
            last_successful_checkpoint=None,
            error_code=None,
            error_message=None,
        )

    async def get(self, model, _identifier):
        self.requests += 1
        if model is PipelineRun:
            return self.run
        if model is Project:
            return self.project
        return None

    async def scalar(self, query):
        self.requests += 1
        sql = str(query)
        if "FROM execution_manifests" in sql:
            return None
        if "FROM quality_evaluations" in sql:
            return None
        if "FROM articles" in sql:
            return None
        if "FROM pipeline_runs" in sql:
            if "count(" in sql.lower():
                return 0
            return (
                self.run.id
                if sql.lstrip().startswith("SELECT pipeline_runs.id")
                else self.run
            )
        return 0

    async def scalars(self, _query):
        self.requests += 1
        return _Rows()

    async def execute(self, _query):
        self.requests += 1
        return _Rows()


@pytest.mark.parametrize("method,path,payload", SENSITIVE_REQUESTS)
@pytest.mark.parametrize("provided_token", [None, "", " ", "incorrect-token"])
def test_sensitive_routes_reject_unauthorized_before_database_access(
    monkeypatch, method, path, payload, provided_token
):
    database_requests = 0

    async def database_dependency():
        nonlocal database_requests
        database_requests += 1
        yield object()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    headers = {"X-Admin-Token": provided_token} if provided_token is not None else {}

    with _client(database_dependency) as client:
        response = client.request(method, path, json=payload, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Acesso administrativo não autorizado."}
    assert database_requests == 0
    assert ADMIN_TOKEN not in response.text


@pytest.mark.parametrize("method,path,payload", SENSITIVE_REQUESTS)
def test_sensitive_routes_allow_configured_admin_token(
    monkeypatch, method, path, payload
):
    database_requests = 0

    async def database_dependency():
        nonlocal database_requests
        database_requests += 1
        raise HTTPException(418, "authorized request reached the operation")
        yield

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _client(database_dependency) as client:
        response = client.request(
            method,
            path,
            json=payload,
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 418
    assert database_requests == 1


@pytest.mark.parametrize("path", BUSINESS_READ_PATHS)
def test_business_read_routes_return_functional_response_with_admin_token(
    monkeypatch, path
):
    fake_db = _BusinessReadDb()

    async def database_dependency():
        yield fake_db

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _client(database_dependency) as client:
        response = client.get(path, headers={"X-Admin-Token": ADMIN_TOKEN})

    assert response.status_code == 200, response.text
    assert fake_db.requests > 0


def test_health_remains_public(monkeypatch):
    database_requests = 0

    class HealthDb:
        async def execute(self, _query):
            nonlocal database_requests
            database_requests += 1

    async def database_dependency():
        yield HealthDb()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _client(database_dependency) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "service": "seo-research-ledger",
    }
    assert database_requests == 0


def test_audited_get_routes_require_admin_except_health():
    expected = {
        "/api/v1/health": False,
        "/api/v1/readiness": False,
        "/api/v1/projects": True,
        "/api/v1/pipeline-runs/{run_id}": True,
        "/api/v1/projects/{project_id}": True,
        "/api/v1/projects/{project_id}/facts": True,
        "/api/v1/dashboard": True,
        "/api/v1/config/credentials": True,
        "/api/v1/config": True,
    }

    audited = {
        route.path: require_admin
        in [dependency.call for dependency in route.dependant.dependencies]
        for route in router.routes
        if getattr(route, "path", None) in expected
    }

    assert audited == expected


def test_pipeline_detail_excludes_worker_coordination_and_secret_payloads(monkeypatch):
    dispatch_token = str(uuid.uuid4())
    celery_task_id = "celery-internal-task-secret"
    handoff_secret = "handoff-provider-secret"
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=PIPELINE_RUN_ID,
        project_id=PROJECT_ID,
        status="queued",
        current_stage="planner",
        attempt=1,
        retryable=False,
        next_retry_at=None,
        cancellation_requested_at=None,
        last_successful_checkpoint=None,
        error_code=None,
        error_message=None,
        dispatch_token=dispatch_token,
        dispatch_status="claimed",
        dispatch_claimed_by="worker-internal-identity",
        dispatch_claimed_at=now,
        dispatch_expires_at=now,
        dispatch_attempt=3,
        dispatch_not_before=now,
        last_dispatch_error="internal broker detail",
        last_dispatched_at=now,
        celery_task_id=celery_task_id,
        metadata_json={"credential": handoff_secret},
    )
    event = SimpleNamespace(
        sequence=1,
        event_type="pipeline.queued",
        stage="planner",
        stage_occurrence_id=None,
        research_cycle=None,
        editor_cycle=None,
        run_attempt=1,
        stage_attempt=1,
        checkpoint_sequence=None,
        agent_run_id=None,
        payload={
            "dispatch_token": dispatch_token,
            "claimed_by": "worker-internal-identity",
            "message": "Execução enfileirada.",
            "provider_secret": handoff_secret,
            "status": "queued",
            "question_id": str(uuid.uuid4()),
            "markets_requested": ["us", "es", "ch"],
            "markets_with_results": ["ch", "es", "us"],
            "document_count": 9,
            "brazil_context_explicit": False,
        },
        created_at=now,
    )
    handoff = SimpleNamespace(
        id=uuid.uuid4(),
        sequence=1,
        from_role="researcher",
        to_role="research_gatekeeper",
        producer_agent_run_id=uuid.uuid4(),
        fact_ids=[str(uuid.uuid4())],
        payload={"provider_secret": handoff_secret},
        created_at=now,
    )

    class PipelineDetailDb:
        async def get(self, model, _identifier):
            assert model is PipelineRun
            return run

        async def scalar(self, query):
            assert "FROM execution_manifests" in str(query)
            return None

        async def scalars(self, query):
            sql = str(query)
            if "FROM pipeline_events" in sql:
                return _Rows([event])
            if "SELECT agent_handoffs.id" in sql:
                return _Rows([handoff.id])
            if "FROM agent_handoffs" in sql:
                return _Rows([handoff])
            return _Rows([])

    async def database_dependency():
        yield PipelineDetailDb()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _client(database_dependency) as client:
        response = client.get(
            f"/api/v1/pipeline-runs/{PIPELINE_RUN_ID}",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload).isdisjoint(
        {
            "dispatch_status",
            "dispatch_token",
            "dispatch_claimed_by",
            "dispatch_claimed_at",
            "dispatch_expires_at",
            "dispatch_attempt",
            "dispatch_not_before",
            "last_dispatch_error",
            "last_dispatched_at",
            "celery_task_id",
            "metadata",
        }
    )
    assert payload["events"][0]["payload"] == {
        "brazil_context_explicit": False,
        "document_count": 9,
        "markets_requested": ["us", "es", "ch"],
        "markets_with_results": ["ch", "es", "us"],
        "message": "Execução enfileirada.",
        "question_id": event.payload["question_id"],
        "status": "queued",
    }
    assert "payload" not in payload["handoffs"][0]
    assert "producer_agent_run_id" not in payload["handoffs"][0]
    assert dispatch_token not in response.text
    assert celery_task_id not in response.text
    assert handoff_secret not in response.text


@pytest.mark.parametrize("configured_token", ["", " "])
def test_missing_admin_configuration_fails_closed(monkeypatch, configured_token):
    database_requests = 0

    async def database_dependency():
        nonlocal database_requests
        database_requests += 1
        yield object()

    monkeypatch.setattr(settings, "admin_api_token", configured_token)

    with _client(database_dependency) as client:
        response = client.post(
            "/api/v1/projects",
            json={
                "name": "Blocked project",
                "topic": "Must not persist",
                "audience": "internal operators",
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Acesso administrativo não autorizado."}
    assert database_requests == 0
    assert "ADMIN_API_TOKEN" not in response.text


def test_admin_token_uses_constant_time_comparison(monkeypatch):
    compared = []

    def compare_digest(provided, configured):
        compared.append((provided, configured))
        return True

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(routes_module.hmac, "compare_digest", compare_digest)

    require_admin("provided-token")

    assert compared == [("provided-token", ADMIN_TOKEN)]


def test_authorized_cancellation_endpoint_returns_pending_running_state(monkeypatch):
    requested_at = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=PIPELINE_RUN_ID,
        project_id=PROJECT_ID,
        status=PipelineRunStatus.running,
        cancellation_requested_at=requested_at,
    )

    class FakeDb:
        def __init__(self):
            self.commits = 0

        async def commit(self):
            self.commits += 1

    fake_db = FakeDb()

    async def database_dependency():
        yield fake_db

    class PipelineRunService:
        def __init__(self, db):
            assert db is fake_db

        async def request_cancellation(self, run_id, *, origin):
            assert run_id == PIPELINE_RUN_ID
            assert origin == "admin.api"
            return run

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(routes_module, "PipelineRunService", PipelineRunService)

    with _client(database_dependency) as client:
        response = client.post(
            f"/api/v1/pipeline-runs/{PIPELINE_RUN_ID}/cancel",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert response.json() == {
        "pipeline_run_id": str(PIPELINE_RUN_ID),
        "status": "running",
        "cancellation_requested_at": requested_at.isoformat().replace("+00:00", "Z"),
        "cancellation_pending": True,
    }
    assert fake_db.commits == 1


def test_cancellation_endpoint_returns_conflict_for_terminal_run(monkeypatch):
    terminal = SimpleNamespace(status=PipelineRunStatus.completed)

    class FakeDb:
        async def get(self, model, run_id):
            assert model is PipelineRun
            assert run_id == PIPELINE_RUN_ID
            return terminal

    async def database_dependency():
        yield FakeDb()

    class PipelineRunService:
        def __init__(self, _db):
            pass

        async def request_cancellation(self, _run_id, *, origin):
            assert origin == "admin.api"
            raise InvalidRunTransition("completed -> cancelled")

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(routes_module, "PipelineRunService", PipelineRunService)

    with _client(database_dependency) as client:
        response = client.post(
            f"/api/v1/pipeline-runs/{PIPELINE_RUN_ID}/cancel",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 409
    assert response.json() == {"detail": "Pipeline run is already terminal: completed"}


def test_unauthorized_project_creation_has_no_cost_side_effects(monkeypatch):
    effects = {"database": 0, "events": 0, "runs": 0, "dispatch": 0}

    async def database_dependency():
        effects["database"] += 1
        yield object()

    class EventService:
        def __init__(self, *_args):
            effects["events"] += 1

    class PipelineRunService:
        def __init__(self, *_args):
            effects["runs"] += 1

    async def dispatch_one(*_args, **_kwargs):
        effects["dispatch"] += 1

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(routes_module, "EventService", EventService)
    monkeypatch.setattr(routes_module, "PipelineRunService", PipelineRunService)
    monkeypatch.setattr(routes_module, "dispatch_one", dispatch_one)

    with _client(database_dependency) as client:
        response = client.post(
            "/api/v1/projects",
            json={
                "name": "No-cost rejection",
                "topic": "No external work",
                "audience": "internal operators",
            },
        )

    assert response.status_code == 401
    assert effects == {"database": 0, "events": 0, "runs": 0, "dispatch": 0}


def test_authorized_project_creation_preserves_existing_behavior(monkeypatch):
    added = []
    events = []

    class FakeDb:
        def add(self, instance):
            added.append(instance)

        async def flush(self):
            project = next(item for item in added if isinstance(item, Project))
            project.id = uuid.uuid4()
            project.created_at = datetime.now(timezone.utc)
            project.current_stage = "planner"

        async def rollback(self):
            return None

        async def commit(self):
            return None

        async def refresh(self, _instance):
            return None

    fake_db = FakeDb()

    async def database_dependency():
        yield fake_db

    class EventService:
        def __init__(self, _db):
            pass

        async def append(self, *_args, **_kwargs):
            events.append((_args, _kwargs))

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(routes_module, "EventService", EventService)

    with _client(database_dependency) as client:
        response = client.post(
            "/api/v1/projects",
            json={
                "name": "Authorized project",
                "topic": "Protected creation",
                "audience": "internal operators",
                "start_immediately": False,
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 201
    assert response.json()["name"] == "Authorized project"
    assert len([item for item in added if isinstance(item, Project)]) == 1
    assert len(events) == 1
    assert (
        ProjectCreate(
            name="Defaults remain stable",
            topic="Start behavior",
            audience="internal operators",
        ).start_immediately
        is True
    )


def test_authorized_publication_profile_creation_keeps_rich_context(monkeypatch):
    added = []

    class FakeDb:
        def add(self, instance):
            added.append(instance)

        async def commit(self):
            profile = next(
                item for item in added if isinstance(item, PublicationProfile)
            )
            profile.id = uuid.uuid4()
            profile.created_at = datetime.now(timezone.utc)
            profile.updated_at = profile.created_at

        async def refresh(self, _instance):
            return None

    async def database_dependency():
        yield FakeDb()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    with _client(database_dependency) as client:
        response = client.post(
            "/api/v1/publication-profiles",
            json={
                "name": "Blog principal",
                "brand_name": "Marca Exemplo",
                "segment": "jardinagem",
                "brand_description": ("Marca brasileira que ensina cultivo doméstico."),
                "products_services": ["curso", "consultoria"],
                "audience_description": "Adultos iniciantes",
                "audience_age_min": 25,
                "audience_age_max": 45,
                "audience_goals": ["aprender com segurança"],
                "tone_of_voice": "Claro, próximo e experiente.",
                "commercial_objective": "Apresentar soluções úteis",
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 201
    assert response.json()["brand_name"] == "Marca Exemplo"
    assert response.json()["products_services"] == ["curso", "consultoria"]
    assert response.json()["audience_age_min"] == 25
    assert len(added) == 1
    assert added[0].profile_data["commercial_objective"] == (
        "Apresentar soluções úteis"
    )


def test_saved_provider_secret_is_encrypted_and_never_returned_or_logged(
    monkeypatch, caplog
):
    secret = "provider-secret-value-123456"
    added = []

    class FakeDb:
        async def scalar(self, _query):
            return None

        async def scalars(self, _query):
            return []

        def add(self, instance):
            added.append(instance)

        async def commit(self):
            return None

    async def database_dependency():
        yield FakeDb()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(
        settings, "credential_master_key", Fernet.generate_key().decode()
    )
    caplog.set_level(logging.DEBUG)

    with _client(database_dependency) as client:
        response = client.put(
            "/api/v1/config/credentials/openai",
            json={"provider": "openai", "value": secret},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in caplog.text
    credential = next(item for item in added if isinstance(item, Credential))
    assert secret.encode() not in credential.encrypted_value
    assert not any(
        isinstance(item, ModelRoute) and secret in repr(item) for item in added
    )
    assert not any(isinstance(item, PipelineEvent) for item in added)


def test_provider_secret_is_absent_from_configuration_errors(monkeypatch, caplog):
    secret = "provider-secret-on-error-123456"

    async def database_dependency():
        yield object()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(settings, "credential_master_key", "invalid-key")
    caplog.set_level(logging.DEBUG)

    with _client(database_dependency) as client:
        response = client.put(
            "/api/v1/config/credentials/openai",
            json={"provider": "openai", "value": secret},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 503
    assert secret not in response.text
    assert secret not in caplog.text


def test_authorized_model_route_update_remains_functional(monkeypatch):
    added = []

    class FakeDb:
        async def scalar(self, _query):
            return None

        def add(self, instance):
            added.append(instance)

        async def commit(self):
            return None

    async def database_dependency():
        yield FakeDb()

    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)

    with _client(database_dependency) as client:
        response = client.put(
            "/api/v1/config/routes/researcher",
            json={
                "agent_role": "researcher",
                "primary_provider": "openai",
                "primary_model": "test-model",
                "parameters": {"temperature": 0.2},
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )

    assert response.status_code == 200
    assert response.json()["saved"] is True
    route = next(item for item in added if isinstance(item, ModelRoute))
    assert route.agent_role == "researcher"
    assert route.primary_model == "test-model"
    assert route.parameters == {"temperature": 0.2}
