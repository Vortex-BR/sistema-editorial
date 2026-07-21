from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.core.config import Settings, settings
from app.core.observability import structured_log
from app.db.session import SessionLocal
from app.startup import initialize_startup


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime_settings: Settings = app.state.runtime_settings
    app.state.production_preflight_complete = False
    try:
        async with SessionLocal() as db:
            await initialize_startup(db, runtime_settings)
        app.state.production_preflight_complete = True
        structured_log(
            "superior_skills.mode_initialized",
            source_type="api",
            superior_skills_mode=runtime_settings.superior_skills_mode,
        )
        yield
    finally:
        app.state.production_preflight_complete = False


def create_app(config: Settings) -> FastAPI:
    documentation_url = "/docs" if config.api_documentation_enabled else None
    openapi_url = "/openapi.json" if config.api_documentation_enabled else None
    redoc_url = "/redoc" if config.api_documentation_enabled else None
    application = FastAPI(
        title=config.app_name,
        version="0.1.0",
        docs_url=documentation_url,
        openapi_url=openapi_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
    )
    application.state.runtime_settings = config
    application.state.production_preflight_complete = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[config.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Admin-Token",
            "X-Requested-With",
        ],
        expose_headers=["Content-Disposition", "ETag", "Retry-After", "X-Request-ID"],
    )
    application.include_router(router)
    return application


app = create_app(settings)
