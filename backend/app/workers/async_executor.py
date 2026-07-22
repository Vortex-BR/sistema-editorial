import asyncio
import logging
from collections.abc import Awaitable
from typing import TypeVar

from app.db.session import engine


T = TypeVar("T")
logger = logging.getLogger(__name__)


def run_async_task(awaitable: Awaitable[T]) -> T:
    async def execute() -> T:
        task_error: BaseException | None = None
        try:
            return await awaitable
        except BaseException as exc:
            task_error = exc
            raise
        finally:
            try:
                await engine.dispose()
            except BaseException:
                if task_error is None:
                    raise
                logger.exception(
                    "Failed to dispose the SQLAlchemy engine after a Celery task; "
                    "preserving the original task exception"
                )

    return asyncio.run(execute())
