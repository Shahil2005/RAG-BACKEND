from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.core.database import get_db, get_db_connect
from app.core.settings import settings
from app.schema.auth import AuthContext

# For ORM queries
DBSessionDep = Annotated[AsyncSession, Depends(get_db)]

# For Raw SQL queries
DBConnectionDep = Annotated[AsyncConnection, Depends(get_db_connect)]


def extract_session_token(request: Request) -> str | None:
    """Pull the session token from the session cookie or an Authorization: Bearer header.

    Mirrors the original JwtAuthGuard / AuthController.extractToken precedence.
    """
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        return cookie
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[len("Bearer ") :]
    return None


async def get_current_user(request: Request, db: DBSessionDep) -> AuthContext:
    """FastAPI replacement for NestJS JwtAuthGuard + @CurrentUser().

    Raises 401 on a missing/invalid session so protected routes behave identically.
    """
    # Imported here to avoid a circular import at module load.
    from fastapi import HTTPException, status

    from app.services.auth_service import AuthService

    token = extract_session_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing session")

    org_header = request.headers.get("x-organization-id")
    cache = getattr(request.app.state, "cache", None)
    try:
        user = await AuthService(db, cache).get_session_from_token(token, org_header)
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session"
        ) from err

    # Stash the raw token so handlers (e.g. logout) can revoke the exact session.
    request.state.session_token = token
    return user


CurrentUserDep = Annotated[AuthContext, Depends(get_current_user)]
