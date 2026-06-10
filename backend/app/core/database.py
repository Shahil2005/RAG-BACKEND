import contextlib
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends
from loguru import logger
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import settings

# The shared declarative Base lives in app.models so all ORM models register on it.


def get_engine(host: str, **engine_kwargs: Any) -> AsyncEngine:
    return create_async_engine(host, **engine_kwargs)


engine = get_engine(
    settings.db_url,
    echo=False,  # do not log every SQL statement to the console
    pool_size=10,  # Up to 10 persistent connections
    max_overflow=20,  # Up to 20 temporary additional connections
    pool_timeout=30,  # Idle timeout for connections
)


class DatabaseSessionManager:
    def __init__(self) -> None:
        # Create the SQLAlchemy engine
        self.engine: AsyncEngine | None = engine
        # Create a SessionLocal class
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = async_sessionmaker(
            autocommit=False,
            class_=AsyncSession,
            autoflush=False,
            bind=self.engine,
            # Required for async SQLAlchemy: keep attributes loaded after commit so
            # accessing ORM fields post-commit doesn't trigger implicit (sync) IO,
            # which raises MissingGreenlet ("greenlet_spawn has not been called").
            expire_on_commit=False,
        )

    async def close(self) -> None:
        if self.engine is None:
            msg = "DatabaseSessionManager is not initialized"
            raise Exception(msg)
        await self.engine.dispose()

        self.engine = None
        self._sessionmaker = None
        logger.info("Database Connections closed")

    @contextlib.asynccontextmanager
    async def connect(self) -> AsyncIterator[AsyncConnection]:
        if self.engine is None:
            msg = "DatabaseSessionManager is not initialized"
            raise Exception(msg)

        async with self.engine.begin() as connection:
            try:
                yield connection
            except Exception:
                await connection.rollback()
                raise
            finally:
                await connection.close()

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if self._sessionmaker is None:
            msg = "DatabaseSessionManager is not initialized"
            raise Exception(msg)

        session = self._sessionmaker()
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


DBSessionManager = DatabaseSessionManager()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with DBSessionManager.session() as session:
        yield session


async def get_db_connect() -> AsyncIterator[AsyncConnection]:
    async with DBSessionManager.connect() as connect:
        yield connect


SQLALCHEMY_DATABASE_URL = settings.db_url

DBSessionDep = Annotated[AsyncSession, Depends(get_db)]
