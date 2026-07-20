from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from app.core.config import settings
from app.core.errors import new_correlation_id
from app.core.observability import structured_log
from app.core.sanitization import assert_no_nul, sanitize_nul_with_report


def sanitize_mapped_instance(instance) -> None:
    state = inspect(instance, raiseerr=False)
    if state is None or not hasattr(state, "mapper"):
        return
    for attribute in state.mapper.column_attrs:
        key = attribute.key
        value = getattr(instance, key, None)
        sanitized, report = sanitize_nul_with_report(
            value, path=f"$.{instance.__class__.__name__}.{key}"
        )
        if sanitized != value:
            setattr(instance, key, sanitized)
        if report.nul_removed_count or report.escaped_nul_removed_count:
            structured_log(
                "persistence.value_sanitized",
                project_id=getattr(instance, "project_id", None),
                pipeline_run_id=getattr(instance, "pipeline_run_id", None),
                stage=getattr(instance, "agent_role", None),
                source_type=instance.__class__.__name__,
                correlation_id=new_correlation_id(),
                **report.as_log_context(),
            )
        assert_no_nul(sanitized, path=f"$.{instance.__class__.__name__}.{key}")


def _sanitize_before_attach(_session, instance) -> None:
    sanitize_mapped_instance(instance)


def _last_chance_nul_guard(session, _flush_context, _instances) -> None:
    """Final invariant; service boundaries must sanitize before objects get here."""
    for instance in session.new.union(session.dirty):
        sanitize_mapped_instance(instance)


def register_session_sanitization_guards() -> None:
    """Register the ORM sanitization guards once for sync and async sessions."""
    listeners = (
        ("before_attach", _sanitize_before_attach),
        ("before_flush", _last_chance_nul_guard),
    )
    for event_name, listener in listeners:
        if not event.contains(Session, event_name, listener):
            event.listen(Session, event_name, listener)


register_session_sanitization_guards()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session
