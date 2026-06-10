from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

# Mounted under /api/v1 by app.router.api_router (see app/router/__init__.py).
router = APIRouter(tags=["base"])


@router.get("/health")
async def health_check(db: Annotated[AsyncSession, Depends(get_db)]) -> JSONResponse:
    try:
        await db.execute(text("SELECT 1"))
        return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "All Healthy"})
    except ProgrammingError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"error": "Database connection failed"}
        )
    except asyncpg.InvalidCatalogNameError:
        # Note: This might be sensitive to expose the DB status,
        # which might enable an attacker to guess the DB name.
        # This exceptin should be used with caution. Recommended only
        # for secure environments.
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"error": "Database not found"})
    except asyncpg.InvalidPasswordError:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": "Invalid password"})
    except ConnectionRefusedError:
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"error": "Connection Refused"})
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": "Internal server error"}
        )


@router.get("/pulse")
def pulse() -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "I'm Alive"})
