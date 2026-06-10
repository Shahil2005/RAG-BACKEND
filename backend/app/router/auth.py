"""Auth routes — port of the NestJS AuthController.

Endpoint paths, redirects, cookie semantics and JSON shapes are preserved exactly
(mounted under /api/v1 by the parent router) so the existing frontend is unaffected.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse

from app import logger
from app.core.dependencies import CurrentUserDep, DBSessionDep, extract_session_token
from app.core.settings import settings
from app.schema.auth import AuthContext, AuthorizeUrlResponse, LogoutResponse, SessionResponse
from app.services.auth_service import AuthService
from app.services.oauth_service import OAuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _web_origin() -> str:
    return settings.web_origin


def _login_error_redirect(reason: str) -> str:
    from urllib.parse import urlencode

    params = urlencode({"error": "oauth", "reason": reason})
    return f"{_web_origin()}/login?{params}"


def _map_oauth_failure_reason(message: str) -> str:
    if "AADSTS7000215" in message or "Invalid client secret" in message:
        return "invalid_client_secret"
    if "Invalid or expired OAuth state" in message:
        return "invalid_state"
    if "admin consent" in message or "AADSTS65001" in message:
        return "admin_consent"
    if "Token exchange failed" in message:
        return "token_exchange"
    return "unknown"


def _is_popup(popup: str | None) -> bool:
    return popup in ("1", "true")


@router.get("/microsoft/authorize-url")
async def microsoft_authorize_url(
    request: Request, db: DBSessionDep, popup: str | None = None
) -> AuthorizeUrlResponse:
    cache = getattr(request.app.state, "cache", None)
    _, authorize_url = await OAuthService(cache).create_login_state(_is_popup(popup))
    return AuthorizeUrlResponse(authorize_url=authorize_url)


@router.get("/microsoft/login")
async def microsoft_login(
    request: Request, db: DBSessionDep, popup: str | None = None
) -> Response:
    web_origin = _web_origin()
    is_popup = _is_popup(popup)
    cache = getattr(request.app.state, "cache", None)
    try:
        _, authorize_url = await OAuthService(cache).create_login_state(is_popup)
        return RedirectResponse(authorize_url, status_code=status.HTTP_302_FOUND)
    except Exception as err:
        logger.error(f"[auth] login.start_failed: {err!r}")
        if is_popup:
            return RedirectResponse(
                f"{web_origin}/auth/microsoft/complete?error=oauth",
                status_code=status.HTTP_302_FOUND,
            )
        return RedirectResponse(
            _login_error_redirect("login_start_failed"), status_code=status.HTTP_302_FOUND
        )


@router.get("/microsoft/callback")
async def microsoft_callback(
    request: Request,
    db: DBSessionDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> Response:
    web_origin = _web_origin()

    if error or not code or not state:
        reason = "access_denied" if error == "access_denied" else "missing_code"
        logger.warning(
            f"[auth] callback.rejected reason={reason} microsoftError={error} "
            f"microsoftErrorDescription={error_description}"
        )
        return RedirectResponse(
            _login_error_redirect(reason), status_code=status.HTTP_302_FOUND
        )

    cache = getattr(request.app.state, "cache", None)
    try:
        result = await AuthService(db, cache).complete_microsoft_login(code, state)
        cookie_name = settings.session_cookie_name

        target = (
            f"{web_origin}/auth/microsoft/complete"
            if result.popup
            else f"{web_origin}/dashboard"
        )
        response = RedirectResponse(target, status_code=status.HTTP_302_FOUND)
        max_age = max(
            0, int((result.expires_at - datetime.now(timezone.utc)).total_seconds())
        )
        response.set_cookie(
            key=cookie_name,
            value=result.token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            path="/",
            max_age=max_age,
        )
        logger.info(
            f"[auth] callback.success userId={result.user['id']} popup={result.popup}"
        )
        return response
    except Exception as err:
        message = str(err)
        reason = _map_oauth_failure_reason(message)
        logger.error(f"[auth] callback.failed reason={reason}: {err!r}")
        return RedirectResponse(
            _login_error_redirect(reason), status_code=status.HTTP_302_FOUND
        )


@router.post("/logout")
async def logout(request: Request, db: DBSessionDep, user: CurrentUserDep) -> Response:
    token = getattr(request.state, "session_token", None)
    if token:
        await AuthService(db).logout(token)
    response = JSONResponse(content=LogoutResponse().model_dump())
    response.delete_cookie(settings.session_cookie_name, path="/")
    logger.info("[auth] logout.success")
    return response


@router.get("/me")
async def me(request: Request, db: DBSessionDep) -> dict:
    token = extract_session_token(request)
    if not token:
        logger.warning("[auth] me.unauthorized reason=no_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    cache = getattr(request.app.state, "cache", None)
    return await AuthService(db, cache).get_me(token)


@router.get("/session")
async def session(user: CurrentUserDep) -> SessionResponse:
    return SessionResponse(user=user)


@router.get("/bootstrap")
async def bootstrap(user: CurrentUserDep) -> AuthContext:
    return user
