import contextlib
import os
import re
from collections.abc import AsyncIterator

from celery import Celery
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.constants import (
    CEL_DEFAULT_QUEUE,  # Default queue name for Celery tasks
    CEL_MAIN_NAME,  # Main application name for Celery instance
    CEL_TASK_PATHS,  # Paths where Celery should autodiscover tasks
    DB_CEL_ECHO,  # Database echo setting for Celery operations
    DB_CEL_MAX_OVERFLOW,  # Maximum overflow connections for database pool
    DB_CEL_POOL_SIZE,  # Database connection pool size
    DB_CEL_POOL_TIMEOUT,  # Timeout for database connection pool
)

# Import database utilities and connection URL
from app.core.database import SQLALCHEMY_DATABASE_URL, get_engine
from app.core.settings import settings

# Import function to register scheduled tasks with Celery
from app.job.schedules import register_celery_schedules

# Load environment variables from .env file
load_dotenv()

# Build the Redis broker/backend from the same REDIS_URL the rest of the app uses
# (falls back to host/port/password env vars). Broker -> db 0, result backend -> db 1.
_redis_base = (settings.redis_url or "").rstrip("/")
if _redis_base:
    _redis_base = re.sub(r"/\d+$", "", _redis_base)  # strip any trailing /<db>
else:
    _pw = os.getenv("REDIS_PASSWORD")
    _auth = f":{_pw}@" if _pw else ""
    _redis_base = f"redis://{_auth}localhost:{os.getenv('REDIS_PORT', '6379')}"

# Create Celery application instance with Redis as message broker and result backend
celery_app = Celery(
    CEL_MAIN_NAME,  # Application name for Celery identification
    broker=f"{_redis_base}/0",  # task distribution (db 0)
    backend=f"{_redis_base}/1",  # result storage (db 1)
    # Explicitly import the task modules so the @shared_task definitions register on
    # worker startup. autodiscover_tasks(["app.job.tasks"]) alone looks for a
    # nonexistent "app.job.tasks.tasks" module and would find nothing.
    include=["app.job.tasks.ingestion", "app.job.tasks.scheduler"],
)

# Configure Celery application with default queue settings
celery_app.conf.update(task_default_queue=CEL_DEFAULT_QUEUE)

# Automatically discover and register tasks from specified package paths
celery_app.autodiscover_tasks(CEL_TASK_PATHS)

# Register scheduled tasks (periodic tasks) with the Celery application
register_celery_schedules(celery_app)


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """
    Create an async session maker factory for database operations.

    Args:
        engine: SQLAlchemy async engine instance

    Returns:
        Configured async session maker that creates AsyncSession instances

    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@contextlib.asynccontextmanager
async def get_celery_db_session() -> AsyncIterator[AsyncSession]:
    """
    Async context manager for creating database sessions in Celery tasks.

    This function provides a properly configured database session that:
    - Uses connection pooling optimized for Celery worker processes
    - Automatically handles session cleanup and connection closing
    - Provides isolation for concurrent task execution

    Yields:
        AsyncSession: Database session for use in Celery tasks

    """
    # Create database engine with Celery-specific connection pool settings
    engine = get_engine(
        SQLALCHEMY_DATABASE_URL,
        echo=DB_CEL_ECHO,
        pool_size=DB_CEL_POOL_SIZE,  # Up to 10 persistent connections
        max_overflow=DB_CEL_MAX_OVERFLOW,  # Up to 20 temporary additional connections
        pool_timeout=DB_CEL_POOL_TIMEOUT,  # Idle timeout for connections
    )

    # Create session maker factory with the configured engine
    session_maker = get_sessionmaker(engine)

    # Create and yield database session within async context
    async with session_maker() as session:
        try:
            # Provide session to the calling code
            yield session
        finally:
            # Ensure session is properly closed to return connection to pool
            await session.close()
