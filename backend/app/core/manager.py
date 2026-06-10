from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app import logger
from app.core.database import DBSessionManager
from app.core.redis import RedisHelper


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[Any]:
    """
    Handles startup and shutdown events.
    """
    # Startup: attach a Redis cache layer (used by OAuth state, throttling, etc.).
    try:
        app.state.cache = RedisHelper()
    except Exception as err:
        logger.error(f"[startup] failed to init Redis cache: {err!r}")
        app.state.cache = None

    yield

    if DBSessionManager.engine is not None:
        # Close the DB connection
        await DBSessionManager.close()
